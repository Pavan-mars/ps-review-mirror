# =====================================================================
# cubic-mars-ps4-v3-loader                                   29-Jul-2026
#
# S3 (chicago/ps4/v3) -> Aurora ps4_weekly_* / ps4_cluster_* / ps4_v3_runs
#
# A SEPARATE Lambda from cubic-mars-ps4-rds-loader ON PURPOSE.
#
# The instruction was: do not remove or retire the current PS4 tables, they are
# Plan B. The cheapest way to honour that is to not touch the code that fills
# them. This function shares nothing with the old loader -- not a table, not a
# prefix, not a deployment -- so "roll back to Plan B" is "stop invoking this",
# with no restore step and no risk that a bad edit here breaks the old feed.
#
# WHAT IT READS
#   s3://<artifacts>/chicago/ps4/v3/manifests/latest.json
# which the notebook promotes only after writing
#   chicago/ps4/v3/runs/run_id=<RUN_ID>/READY.json
# READY.json is written LAST. This loader checks for it before reading a single
# parquet part: a manifest without its READY marker means the run died partway
# and the parquet under it is half a run. Reading it would load a truthful-
# looking partial week, which is worse than loading nothing.
#
# ACTIONS
#   {"action":"dry_run"}   read S3, report row counts and column mapping, write
#                          nothing. ALWAYS run this first.
#   {"action":"load"}      delete this (city, pipeline_version) then insert.
#   {"action":"verify"}    row counts, reconciliation, cluster quality.
#   {}                     same as load (so EventBridge/S3 can invoke it bare).
#
# DELETE SCOPE. DELETE ... WHERE city_id = :c AND pipeline_version = :pv.
# Never a bare TRUNCATE, never DELETE by city alone. Two pipeline versions can
# coexist, which is what makes an A/B against Plan B possible at all.
# =====================================================================
import io
import json
import os
import re

import boto3
import pg8000.native

REGION           = os.environ.get("AWS_REGION", "us-east-1")
ARTIFACT_BUCKET  = os.environ.get("ARTIFACT_BUCKET",
                                  "cubic-mars-pm-s3-datalake-dev-artifacts-170202974600")
V3_ROOT          = os.environ.get("PS4_V3_ROOT", "chicago/ps4/v3")
RDS_SECRET_ID    = os.environ.get("RDS_SECRET_ID", "cubic-mars-secret-rds-dev")
RDS_HOST         = os.environ.get("RDS_HOST", "")
RDS_PORT         = int(os.environ.get("RDS_PORT", "5432"))
CITY_ID          = os.environ.get("CITY_ID", "CHI")

_s3   = boto3.client("s3", region_name=REGION)
_sec  = boto3.client("secretsmanager", region_name=REGION)
_conn = None

CONN_INFO = {}


# ---------------------------------------------------------------------------
# Silhouette fallback.
#
# Measured in the reference run's own cluster-selection log:
#   GATE       k=3  0.545102  over 293,033 training rows
#   TVM        k=5  0.528693  over 157,160 training rows
#   VALIDATOR  k=3  0.458884  over 830,050 training rows
#
# Used ONLY when the manifest carries no silhouette, and always written with
# quality_source='run_log' so the screen can say where it came from. If the
# manifest does publish it, the manifest wins and the source reads 'manifest'.
# These are not defaults in the sense of "reasonable guesses" -- they are
# transcribed numbers from a specific run, and they are wrong the moment a
# different run is loaded, which is exactly why the source column exists.
# ---------------------------------------------------------------------------
SILHOUETTE_RUN_LOG = {
    "GATE":      {"k_selected": 3, "silhouette": 0.545102, "train_rows": 293033},
    "TVM":       {"k_selected": 5, "silhouette": 0.528693, "train_rows": 157160},
    "VALIDATOR": {"k_selected": 3, "silhouette": 0.458884, "train_rows": 830050},
}
SILHOUETTE_RUN_LOG_RUN = "ps4-20260728T213153Z-bf4609d4"


# Dataset name -> (target table, ordered column list).
# Column names are the notebook's own, lowercased. Anything the parquet carries
# that is not listed here is REPORTED as unmapped by dry_run and dropped -- it
# is never silently coerced into a column that happens to be free.
COLS_DEVICE = [
    "city_id", "device_id", "device_type", "week_start", "pipeline_version",
    "week_end", "facility_id", "observed_days", "observed_hours",
    "candidate_days", "actionable_days", "is_actionable_week",
    "anomaly_score_max", "anomaly_score_mean", "anomaly_score_p95",
    "max_abs_z", "cluster_distance_ratio_max", "has_low_coverage_day",
    "dominant_cluster_id", "anomaly_types", "severity", "asof_date", "run_id",
    # max_fault_z is the value the notebook's alert RULES read; max_abs_z above
    # is the diagnostic they used to read. Three of the five signals are
    # one-sided, so taking abs() of all five let a device with oos_rate_z = -4
    # -- far FEWER out-of-service events than its baseline -- clear the
    # `>= 4.0` door and alert for being unusually healthy.
    # ORDER MATTERS: sql/65 must be applied before this list is deployed, or
    # insert() builds a column list Aurora does not have. It is nullable, so
    # every row loaded before the notebook re-runs carries NULL here.
    "max_fault_z",
]

DATASETS = {
    "weekly_device_summary": {
        "table": "ps4_weekly_device_summary", "cols": COLS_DEVICE,
        "aliases": ("weekly_device_summary", "device_summary", "weekly_summary"),
    },
    "weekly_alerts": {
        "table": "ps4_weekly_alerts", "cols": COLS_DEVICE,
        "aliases": ("weekly_alerts", "alerts"),
    },
    "weekly_timeline": {
        "table": "ps4_weekly_timeline",
        "cols": ["city_id", "device_type", "week_start", "pipeline_version",
                 "week_end", "devices_observed", "actionable_devices",
                 "candidate_device_days", "mean_anomaly_score",
                 "max_anomaly_score", "asof_date", "run_id"],
        "aliases": ("weekly_timeline", "timeline"),
    },
    "cluster_profile": {
        "table": "ps4_cluster_profile",
        "cols": ["city_id", "device_type", "cluster_id", "pipeline_version",
                 "scored_device_days", "candidate_rate", "actionable_rate",
                 "mean_cluster_distance", "train_cluster_distance_p99",
                 "train_cluster_share", "asof_date", "run_id"],
        "aliases": ("cluster_profile", "clusters", "cluster_profiles"),
    },
}

INT_COLS = {
    "observed_days", "observed_hours", "candidate_days", "actionable_days",
    "is_actionable_week", "has_low_coverage_day", "dominant_cluster_id",
    "devices_observed", "actionable_devices", "candidate_device_days",
    "cluster_id", "scored_device_days",
}
DATE_COLS = {"week_start", "week_end", "asof_date"}


# ------------------------------------------------------------------ infra ----
def secret():
    return json.loads(_sec.get_secret_value(SecretId=RDS_SECRET_ID)["SecretString"])


def connect():
    """The SECRET decides the database, not the env var.

    This exact mismatch cost a full debugging cycle on 27-Jul: the dashboard-api
    resolves dbname from the secret (appdb) while a loader defaulted to the env
    var (postgres). Same cluster, same host, different database, and the loader
    reported 'relation does not exist' for tables that plainly existed. The
    resolution order below is copied from dashboard-api character for character
    so the two can never drift again.
    """
    global _conn
    if _conn is not None:
        try:
            _conn.run("SELECT 1")
            return _conn
        except Exception:
            # A cached connection in a warm container dies silently while idle;
            # the next statement fails with 'cannot read from timed out object'.
            # Ping, and rebuild on failure, rather than discovering it mid-load.
            try:
                _conn.close()
            except Exception:
                pass
            _conn = None
    s = secret()
    host = RDS_HOST or s.get("host")
    db   = s.get("dbname") or s.get("database") or os.environ.get("RDS_DATABASE") or "postgres"
    port = int(s.get("port") or RDS_PORT)
    CONN_INFO.update({"host": host, "database": db, "port": port})
    _conn = pg8000.native.Connection(
        user=s.get("username", "postgres"), password=s["password"],
        host=host, port=port, database=db, timeout=30)
    return _conn


# ------------------------------------------------------------------- s3 ------
def get_json(bucket, key):
    return json.loads(_s3.get_object(Bucket=bucket, Key=key)["Body"].read())


def head(bucket, key):
    """(exists, reason). 403 and 404 are different faults and must not merge.

    A bare `except: return False` on this call once reported an IAM denial as
    'write unfinished', which sent a whole afternoon at the Spark job instead of
    the role policy.
    """
    try:
        _s3.head_object(Bucket=bucket, Key=key)
        return True, None
    except Exception as e:
        r = getattr(e, "response", {})
        code = r.get("Error", {}).get("Code", "")
        status = r.get("ResponseMetadata", {}).get("HTTPStatusCode")
        if code in ("403", "AccessDenied") or status == 403:
            return False, (f"ACCESS DENIED on s3://{bucket}/{key} -- the Lambda role "
                           f"cannot read this prefix. Fix the IAM grant, not the notebook.")
        if code in ("404", "NoSuchKey", "NotFound") or status == 404:
            return False, f"NOT FOUND s3://{bucket}/{key}"
        return False, f"{type(e).__name__} on s3://{bucket}/{key}: {str(e)[:160]}"


def list_parquet(bucket, prefix):
    out, token = [], None
    prefix = prefix.rstrip("/") + "/"
    while True:
        kw = {"Bucket": bucket, "Prefix": prefix}
        if token:
            kw["ContinuationToken"] = token
        r = _s3.list_objects_v2(**kw)
        for o in r.get("Contents", []):
            if o["Key"].endswith(".parquet"):
                out.append((o["Key"], o["Size"]))
        if not r.get("IsTruncated"):
            break
        token = r.get("NextContinuationToken")
    return sorted(out)


def read_parts(bucket, parts):
    """One part at a time. Holding every arrow table at once is what turns a
    300 MB folder into a multi-GB heap in a 3 GB Lambda.

    DUCKDB FIRST, PYARROW SECOND -- 29-Jul-2026, measured.
    The v3 notebook writes parquet with ZSTD compression, and the pyarrow that
    ships in the AWS SDK for pandas managed layer is a slimmed build with that
    codec compiled out:
        ArrowNotImplementedError: Support for codec 'zstd' not built
    The whole dry run died on the first part. duckdb bundles its own zstd, is a
    20 MB wheel (comfortably inside the 50 MB direct-upload limit), and reads the
    same file without complaint. pyarrow is kept as the fallback so a snappy or
    uncompressed export still loads if duckdb is ever missing from the package.
    """
    rows = []
    for key, _sz in parts:
        body = _s3.get_object(Bucket=bucket, Key=key)["Body"].read()
        got = None
        try:
            import duckdb
            # /tmp because duckdb reads from a path; Lambda gives 512 MB there
            # and these datasets are single-digit MB.
            tmp = "/tmp/_ps4v3_part.parquet"
            with open(tmp, "wb") as fh:
                fh.write(body)
            con = duckdb.connect()
            try:
                res = con.execute("SELECT * FROM read_parquet(?)", [tmp])
                names = [d[0] for d in res.description]
                got = [dict(zip(names, r)) for r in res.fetchall()]
            finally:
                con.close()
                try: os.remove(tmp)
                except Exception: pass
        except Exception as e_duck:
            try:
                import pyarrow.parquet as pq
                tbl = pq.read_table(io.BytesIO(body))
                names = tbl.column_names
                cells = {c: tbl.column(c).to_pylist() for c in names}
                got = [{c: cells[c][i] for c in names} for i in range(tbl.num_rows)]
                del tbl, cells
            except Exception as e_arrow:
                raise RuntimeError(
                    "could not read s3://%s/%s -- duckdb: %s | pyarrow: %s"
                    % (bucket, key, str(e_duck)[:160], str(e_arrow)[:160]))
        # Partition values are re-attached HERE, per part, because each part
        # carries different ones. A row already holding a real value keeps it;
        # the path only fills what the file omitted.
        hp = hive_partition_cols(key)
        if hp:
            for r in got:
                for k, v in hp.items():
                    if r.get(k) is None:
                        r[k] = v
        rows.extend(got)
        del body, got
    return rows


def hive_partition_cols(key):
    """Partition values live in the PATH, not in the file.

    29-Jul-2026, measured. The notebook writes
        .../weekly_device_summary/week_start=2026-03-23/device_type=GATE/part-*.parquet
    Spark and pandas both REMOVE a partition column from the file body -- the
    value is the directory name. Reading one part therefore yields rows with no
    device_type and no week_start, and the insert died on
        null value in column "device_type" violates not-null constraint
    even though the notebook asserts both columns exist in the frame. They do
    exist; they just do not travel inside the parquet.

    Parsing them off the key is engine-independent -- it works whether the part
    is read by duckdb or pyarrow, and it does not depend on either one's
    hive_partitioning support being enabled.
    """
    from urllib.parse import unquote
    # LOOKAHEAD on the trailing slash, not a literal one. A literal "/" is
    # CONSUMED by the match, so in
    #     /week_start=2026-03-23/device_type=GATE/
    # the first match eats the separator the second one needs, and
    # device_type is silently missed -- which is half the bug this
    # function exists to fix. Verified on the real key before shipping.
    return {k: unquote(v)
            for k, v in re.findall(r"/([A-Za-z0-9_]+)=([^/]+)(?=/)", key)}


def strip_uri(u):
    """s3://bucket/key -> key. A manifest may publish either form."""
    if not isinstance(u, str):
        return None
    m = re.match(r"^s3://([^/]+)/(.+)$", u)
    return m.group(2) if m else u.lstrip("/")


def resolve_prefix(manifest, name, spec):
    """Find this dataset's S3 prefix.

    Order: the manifest's own paths dict (authoritative), then a conventional
    <V3_ROOT>/<name> probe. The probe is a fallback, not the primary path --
    guessing a prefix is how the old loader ended up pointed at
    chicago/ml_outputs/ps4, a path that never existed.
    """
    paths = manifest.get("paths") or {}
    if isinstance(paths, dict):
        for k, v in paths.items():
            kl = str(k).lower()
            if any(a in kl for a in spec["aliases"]):
                p = strip_uri(v if isinstance(v, str) else (v or {}).get("path"))
                if p:
                    return p.rstrip("/"), f"manifest.paths[{k}]"
    return f"{V3_ROOT}/{name}", "convention (manifest carried no matching path)"


# ----------------------------------------------------------------- shape -----
def low(row):
    return {str(k).lower(): v for k, v in row.items()}


def cell(v, col):
    if v is None:
        return None
    if isinstance(v, float) and v != v:          # NaN
        return None
    if hasattr(v, "isoformat"):
        s = v.isoformat()
        return s[:10] if col in DATE_COLS else s
    if isinstance(v, str):
        s = v.strip()
        if s == "" or s.lower() in ("nan", "none", "null", "<na>"):
            return None
        return s[:10] if col in DATE_COLS and len(s) > 10 else s
    if col in INT_COLS:
        try:
            return int(v)
        except Exception:
            return None
    if isinstance(v, bool):
        return 1 if v else 0
    return v


def shape(rows, cols, city, pv, run_id, asof):
    out = []
    for r in rows:
        lr = low(r)
        d = {}
        for c in cols:
            d[c] = cell(lr.get(c), c)
        # Identity columns are part of the primary key. If the parquet omits
        # one, fill it from the manifest rather than inserting a NULL that the
        # key rejects with an error naming the wrong cause.
        d["city_id"] = (d.get("city_id") or city or "").upper() or city
        d["pipeline_version"] = d.get("pipeline_version") or pv
        d["run_id"] = d.get("run_id") or run_id
        if "asof_date" in cols:
            d["asof_date"] = d.get("asof_date") or asof
        if d.get("device_type"):
            d["device_type"] = str(d["device_type"]).upper()
        out.append(d)
    return out


def insert(c, table, cols, rows, dry):
    if dry or not rows:
        return len(rows)
    n = 0
    collist = ", ".join(cols)
    for i in range(0, len(rows), 400):
        chunk = rows[i:i + 400]
        ph, args = [], {}
        for ri, r in enumerate(chunk):
            names = [f"p{ri}_{ci}" for ci in range(len(cols))]
            ph.append("(" + ", ".join(":" + x for x in names) + ")")
            for nm, col in zip(names, cols):
                args[nm] = r.get(col)
        c.run(f"INSERT INTO {table} ({collist}) VALUES " + ", ".join(ph), **args)
        n += len(chunk)
    return n


def find_silhouette(obj, found=None):
    """Recursive scan of the manifest for a published silhouette.

    The manifest schema is the notebook's, not ours, and it may nest the metric
    under models/, clustering/, or a per-device-type dict. Scanning beats
    hardcoding a path that a later notebook edit quietly moves.
    """
    if found is None:
        found = {}
    if isinstance(obj, dict):
        keys = {str(k).lower(): k for k in obj}
        sil_key = next((keys[k] for k in keys if "silhouette" in k), None)
        dt = None
        for cand in ("device_type", "devicetype", "type", "slug"):
            if cand in keys and isinstance(obj[keys[cand]], str):
                dt = obj[keys[cand]].upper()
                break
        if sil_key is not None and dt:
            k_key = next((keys[k] for k in keys
                          if k in ("k", "k_selected", "n_clusters", "best_k")), None)
            tr_key = next((keys[k] for k in keys
                           if k in ("train_rows", "n_train", "n_train_rows", "rows")), None)
            try:
                found[dt] = {
                    "silhouette": float(obj[sil_key]),
                    "k_selected": int(obj[k_key]) if k_key else None,
                    "train_rows": int(obj[tr_key]) if tr_key else None,
                }
            except Exception:
                pass
        for k, v in obj.items():
            # A dict keyed by device type: {"GATE": {"silhouette": ...}, ...}
            if isinstance(v, dict) and str(k).upper() in ("GATE", "TVM", "VALIDATOR"):
                sub = {str(x).lower(): x for x in v}
                sk = next((sub[x] for x in sub if "silhouette" in x), None)
                if sk is not None:
                    kk = next((sub[x] for x in sub
                               if x in ("k", "k_selected", "n_clusters", "best_k")), None)
                    tk = next((sub[x] for x in sub
                               if x in ("train_rows", "n_train", "n_train_rows", "rows")), None)
                    try:
                        found[str(k).upper()] = {
                            "silhouette": float(v[sk]),
                            "k_selected": int(v[kk]) if kk else None,
                            "train_rows": int(v[tk]) if tk else None,
                        }
                    except Exception:
                        pass
            find_silhouette(v, found)
    elif isinstance(obj, list):
        for v in obj:
            find_silhouette(v, found)
    return found


# ---------------------------------------------------------------- manifest ---
def load_manifest(event):
    """latest.json, or an explicit run_id, plus the READY.json gate."""
    run_id = event.get("run_id")
    if run_id:
        key = f"{V3_ROOT}/runs/run_id={run_id}/READY.json"
        okk, why = head(ARTIFACT_BUCKET, key)
        if not okk:
            return None, {"error": f"run_id={run_id} has no READY.json", "detail": why}
        return get_json(ARTIFACT_BUCKET, key), {"manifest_key": key, "gate": "READY.json (explicit run_id)"}

    key = f"{V3_ROOT}/manifests/latest.json"
    okk, why = head(ARTIFACT_BUCKET, key)
    if not okk:
        return None, {"error": "no latest.json manifest", "detail": why,
                      "looked_at": f"s3://{ARTIFACT_BUCKET}/{key}"}
    man = get_json(ARTIFACT_BUCKET, key)
    rid = man.get("run_id")
    gate = {"manifest_key": key}
    if rid:
        rk = f"{V3_ROOT}/runs/run_id={rid}/READY.json"
        rok, rwhy = head(ARTIFACT_BUCKET, rk)
        gate["ready_key"] = rk
        gate["ready_present"] = rok
        if not rok:
            # latest.json is promoted only after READY.json is written, so this
            # combination should be impossible. It is checked anyway, because
            # "should be impossible" is what the 28-Jul overwrite also was.
            return None, {"error": "manifest present but READY.json missing -- "
                                   "refusing to load a possibly partial run",
                          "detail": rwhy, **gate}
    return man, gate


# ------------------------------------------------------------------ main -----
def lambda_handler(event, context):
    event = event or {}
    action = (event.get("action") or ("dry_run" if event.get("dry_run") else "load")).lower()
    city = (event.get("city") or CITY_ID).upper()

    if action == "verify":
        return verify(city)

    dry = (action == "dry_run")
    man, gate = load_manifest(event)
    if man is None:
        return {"ok": False, "action": action, **gate}

    run_id = man.get("run_id")
    pv     = event.get("pipeline_version") or man.get("pipeline_version") or "v3"
    asof   = man.get("asof_date") or man.get("asof")
    src_rows = man.get("source_rows") or man.get("rows_in") or None

    out = {
        "ok": True, "action": action, "city": city,
        "run_id": run_id, "pipeline_version": pv, "asof_date": asof,
        "event_type": man.get("event_type"),
        "manifest": gate, "datasets": {}, "unmapped": {},
    }

    c = connect()
    out["conn"] = dict(CONN_INFO)
    if not dry:
        c.run("BEGIN")
    try:
        for name, spec in DATASETS.items():
            prefix, how = resolve_prefix(man, name, spec)
            parts = list_parquet(ARTIFACT_BUCKET, prefix)
            if not parts:
                out["datasets"][name] = {"prefix": prefix, "resolved_via": how,
                                         "error": "no .parquet parts under this prefix"}
                continue
            raw = read_parts(ARTIFACT_BUCKET, parts)
            present = set()
            for r in raw[:50]:
                present |= set(str(k).lower() for k in r)
            unmapped = sorted(present - set(spec["cols"]))
            if unmapped:
                # Reported, never guessed into a free column. A column the
                # schema does not know about is a schema decision, not a
                # runtime one.
                out["unmapped"][name] = unmapped
            shaped = shape(raw, spec["cols"], city, pv, run_id, asof)
            # The dry run previously reported rows_loaded without ever checking
            # them, so a frame full of NULL key columns looked like a clean
            # 7,742-row read and only failed at INSERT. Count nulls on the
            # not-null columns and surface them before the load, not during it.
            keycols = [c for c in ("city_id", "device_id", "device_type",
                                   "week_start", "cluster_id", "pipeline_version")
                       if c in spec["cols"]]
            nulls = {c: sum(1 for r in shaped if r.get(c) is None) for c in keycols}
            nulls = {c: n for c, n in nulls.items() if n}
            if nulls:
                out.setdefault("null_key_columns", {})[name] = nulls
            if not dry:
                c.run(f"DELETE FROM {spec['table']} WHERE city_id=:c AND pipeline_version=:p",
                      c=city, p=pv)
            n = insert(c, spec["table"], spec["cols"], shaped, dry)
            out["datasets"][name] = {
                "table": spec["table"], "prefix": prefix, "resolved_via": how,
                "parts": len(parts), "rows_read": len(raw), "rows_loaded": n,
                "partition_cols": sorted(hive_partition_cols(parts[0][0]).keys()),
            }

        # ---- cluster quality -------------------------------------------------
        sil = find_silhouette(man)
        source = "manifest" if sil else "run_log"
        if not sil:
            sil = {k: dict(v) for k, v in SILHOUETTE_RUN_LOG.items()}
            out["silhouette_note"] = (
                "The manifest published no silhouette. The three values below are "
                "transcribed from the cluster-selection log of run "
                f"{SILHOUETTE_RUN_LOG_RUN} and are stored with quality_source="
                "'run_log' so the dashboard states their provenance. They are only "
                "valid for that run.")
            if run_id and run_id != SILHOUETTE_RUN_LOG_RUN:
                out["silhouette_warning"] = (
                    f"Loaded run_id is {run_id} but the fallback silhouettes were "
                    f"measured on {SILHOUETTE_RUN_LOG_RUN}. They are NOT this run's "
                    f"numbers. Publish silhouette in the manifest, or treat the "
                    f"cluster quality panel as unverified for this run.")
        qrows = [{"city_id": city, "device_type": dt, "pipeline_version": pv,
                  "k_selected": v.get("k_selected"), "silhouette": v.get("silhouette"),
                  "train_rows": v.get("train_rows"), "quality_source": source,
                  "asof_date": asof, "run_id": run_id}
                 for dt, v in sorted(sil.items())]
        if not dry:
            c.run("DELETE FROM ps4_cluster_quality WHERE city_id=:c AND pipeline_version=:p",
                  c=city, p=pv)
        nq = insert(c, "ps4_cluster_quality",
                    ["city_id", "device_type", "pipeline_version", "k_selected",
                     "silhouette", "train_rows", "quality_source", "asof_date", "run_id"],
                    qrows, dry)
        out["cluster_quality"] = {"rows": nq, "source": source,
                                  "device_types": [r["device_type"] for r in qrows]}

        # ---- run registry ----------------------------------------------------
        d = out["datasets"]
        reg = [{
            "city_id": city, "pipeline_version": pv, "run_id": run_id or "unknown",
            "asof_date": asof, "manifest_key": gate.get("manifest_key"),
            "ready_key": gate.get("ready_key"), "source_rows": src_rows,
            "rows_summary":  (d.get("weekly_device_summary") or {}).get("rows_loaded"),
            "rows_alerts":   (d.get("weekly_alerts") or {}).get("rows_loaded"),
            "rows_timeline": (d.get("weekly_timeline") or {}).get("rows_loaded"),
            "rows_cluster":  (d.get("cluster_profile") or {}).get("rows_loaded"),
        }]
        if not dry:
            c.run("DELETE FROM ps4_v3_runs WHERE city_id=:c AND pipeline_version=:p "
                  "AND run_id=:r", c=city, p=pv, r=run_id or "unknown")
        insert(c, "ps4_v3_runs",
               ["city_id", "pipeline_version", "run_id", "asof_date", "manifest_key",
                "ready_key", "source_rows", "rows_summary", "rows_alerts",
                "rows_timeline", "rows_cluster"], reg, dry)

        if not dry:
            c.run("COMMIT")
    except Exception as e:
        if not dry:
            try:
                c.run("ROLLBACK")
            except Exception:
                pass
        out["ok"] = False
        out["error"] = f"{type(e).__name__}: {str(e)[:400]}"
        return out

    if dry:
        out["note"] = ("DRY RUN -- nothing was written. Check rows_read and the "
                       "unmapped block, then re-invoke with {\"action\":\"load\"}.")
    return out


def verify(city):
    c = connect()

    def q(sql, **kw):
        try:
            r = c.run(sql, **kw)
            cols = [d["name"] for d in c.columns]
            return [dict(zip(cols, row)) for row in r]
        except Exception as e:
            return {"error": f"{type(e).__name__}: {str(e)[:200]}"}

    return {
        "ok": True, "action": "verify", "city": city, "conn": dict(CONN_INFO),
        "tables": q("SELECT * FROM v_ps4_v3_table_status"),
        "current_run": q("SELECT * FROM v_ps4_v3_current WHERE city_id=:c", c=city),
        "cluster_quality": q(
            "SELECT device_type, k_selected, silhouette, train_rows, quality_source,"
            " separation_verdict FROM v_ps4_cluster_quality WHERE city_id=:c"
            " ORDER BY device_type", c=city),
        "reconcile": q("SELECT * FROM v_ps4_weekly_alert_reconcile WHERE city_id=:c", c=city),
        "weeks": q(
            "SELECT device_type, MIN(week_start) AS first_week,"
            " MAX(week_start) AS last_week, COUNT(*) AS week_rows,"
            " SUM(devices_observed) AS device_weeks,"
            " SUM(actionable_devices) AS actionable"
            " FROM v_ps4_weekly_timeline WHERE city_id=:c"
            " GROUP BY device_type ORDER BY device_type", c=city),
        "top_alerts": q(
            "SELECT device_id, device_type, week_start, severity,"
            " ROUND(cluster_distance_ratio_max::numeric,3) AS dist_ratio,"
            " actionable_days, coverage_note"
            " FROM v_ps4_weekly_alerts WHERE city_id=:c"
            " ORDER BY cluster_distance_ratio_max DESC NULLS LAST LIMIT 10", c=city),
    }
