"""
cubic-mars-ps4-rds-loader
=========================
S3 (the PS4 notebooks' real output layout) -> this Lambda, inside the RDS VPC
-> Aurora ps4_* tables -> the dashboard.

REWRITTEN 27-Jul-2026 AGAINST THE ACTUAL BUCKET
-----------------------------------------------
The first version of this file targeted
s3://<gold>/chicago/ml_outputs/ps4/<run_id>/manifest.json. That path does not
exist and never did -- it was inferred from the notebook's intent rather than
read from the bucket. Verified against the real listing on 27-Jul:

  ARTIFACTS bucket  cubic-mars-pm-s3-datalake-dev-artifacts-170202974600
    chicago/ps4/scored/asof=<date>/anomalies/    parquet   17,568,514 rows
    chicago/ps4/scored/asof=<date>/timeline/     parquet        72,751 rows
    chicago/ps4/scored/asof=<date>/outliers/     parquet   36,629,754 rows
    chicago/ps4/manifest/asof=<date>/manifest.json    <-- OWN prefix, not scored/

  GOLD bucket       cubic-mars-pm-s3-datalake-dev-gold-170202974600
    chicago/ps4/clustering/{tvm,gate,validator}/asof=<date>/assignments/
    chicago/ps4/clustering/{tvm,gate,validator}/asof=<date>/cluster_summary/
    chicago/ps4/clustering/manifest/asof=<date>/{tvm,gate,validator}_manifest.json

Three things the old version had wrong, each fatal on its own: the bucket is
ARTIFACTS not gold; the partition is Hive-style `asof=<date>` not a bare folder;
and the manifest sits OUTSIDE the data prefix, so an S3 notification scoped to
scored/ would never have fired at all.

WHAT THIS LOADER DELIBERATELY WILL NOT LOAD
-------------------------------------------
anomalies (17.6M rows, ~2.6 GB in Aurora) and outliers (36.6M rows, ~5.5 GB).
Two independent reasons, either one sufficient:

  SIZE.   Aurora is the serving store, not a second copy of the lake. Lambda has
          a hard 15-minute wall clock, and ~1 GB of snappy parquet expands 4-8x
          as Python dicts. The fix is an aggregate, not a bigger Lambda --
          notebooks/ps4/ps4_device_daily_export.py adds it.

  MEANING. The manifest reports anomaly_hours 17,568,514 of total_hours
          36,629,754 -- 47.96% of every device-hour flagged, which is exactly the
          target_rate 0.4796 on the leaderboard. In the notebook:
              signal_active_count := sum(signal_* columns)
              ensemble_anomaly_flag := (signal_active_count >= SIGNAL_THRESHOLD)
          with SIGNAL_THRESHOLD = 2. The label is a deterministic function of
          features the model is also given, which is why spark_gbt reports test
          AP 0.9994 while spark_lr -- which cannot express a threshold rule --
          reports 0.9367. Loading 17.6M rows of that would put a meaningless
          number in front of a client.

Ask for them anyway and the loader returns a refusal naming the reason. It does
not half-load, and it does not silently skip.

TRIGGERS
  1. S3 ObjectCreated on .../manifest/asof=<date>/*.json  -> that as-of
  2. EventBridge schedule / {}                            -> newest as-of
  3. Manual {"asof": "2026-04-11", "dry_run": true}

Runtime deps: pg8000 (bundled) + pyarrow (AWS SDK for pandas managed layer).
Env: ARTIFACT_BUCKET, GOLD_BUCKET, PS4_PREFIX, PS4_CLUSTER_PREFIX,
     RDS_HOST, RDS_PORT, RDS_DATABASE, RDS_SECRET_ID, CITY_ID, KEEP_SNAPSHOTS
"""
import io
import datetime as _dt
import json
import logging
import os
import re

import boto3
import pg8000.native

log = logging.getLogger()
log.setLevel(logging.INFO)

REGION = os.environ.get("AWS_REGION", "us-east-1")
ARTIFACT_BUCKET = os.environ.get(
    "ARTIFACT_BUCKET", "cubic-mars-pm-s3-datalake-dev-artifacts-170202974600")
GOLD_BUCKET = os.environ.get(
    "GOLD_BUCKET", "cubic-mars-pm-s3-datalake-dev-gold-170202974600")
PS4_PREFIX = os.environ.get("PS4_PREFIX", "chicago/ps4")
# Rolling window for the device_daily DETAIL table. The full history is still
# summarised into ps4_device_lifetime, so nothing is lost -- this only bounds how
# many device-days the serving tier holds. 0 = load every day (3.5M rows; will not
# finish inside a 15-minute Lambda through pg8000).
DAYS_BACK = int(os.environ.get("PS4_DAYS_BACK", "180"))
PS4_CLUSTER_PREFIX = os.environ.get("PS4_CLUSTER_PREFIX", "chicago/ps4/clustering")
RDS_SECRET_ID = os.environ.get("RDS_SECRET_ID", "cubic-mars-secret-rds-dev")
RDS_DATABASE = os.environ.get("RDS_DATABASE", "postgres")
RDS_HOST = os.environ.get("RDS_HOST", "")
RDS_PORT = int(os.environ.get("RDS_PORT", "5432"))
CITY_ID = os.environ.get("CITY_ID", "CHI")
KEEP_SNAPSHOTS = int(os.environ.get("KEEP_SNAPSHOTS", "3"))
DEVICE_SLUGS = ("tvm", "gate", "validator")

_s3 = boto3.client("s3", region_name=REGION)
_sec = boto3.client("secretsmanager", region_name=REGION)
_conn = None

# Feeds this loader writes, and the row cap that keeps each honest. A feed over
# its cap is REFUSED, never truncated -- a silently truncated load looks exactly
# like a complete one on screen, which is the worst of both.
LOADABLE = {
    "timeline": {
        "table": "ps4_anomaly_timeline",
        "cols": ["city_id", "asof_date", "bucket_time", "device_type",
                 "anomaly_count", "avg_score"],
        "max_rows": 500_000,
    },
    "assignments": {
        "table": "ps4_cluster_assignments",
        "cols": ["city_id", "asof_date", "device_type", "device_key", "device_id",
                 "cluster_id", "cluster_confidence", "champion_pipeline",
                 "champion_run_id", "champion_silhouette", "engine"],
        "max_rows": 200_000,
    },
    "cluster_summary": {
        "table": "ps4_cluster_summary",
        "cols": ["city_id", "asof_date", "device_type", "cluster_id", "device_count",
                 "mean_target", "mean_confidence", "champion_pipeline",
                 "champion_silhouette"],
        "max_rows": 10_000,
    },
}

REFUSED = {
    "anomalies": ("17,568,514 rows = 47.96% of all device-hours, ~2.6 GB in "
                  "Aurora. ensemble_anomaly_flag is a deterministic function of "
                  "the signal_* features (signal_active_count >= 2), so the feed "
                  "is neither Lambda-loadable nor meaningful yet. Export a "
                  "device_daily aggregate instead."),
    "outliers": ("36,629,754 rows, ~5.5 GB in Aurora -- one row per device-hour. "
                 "Aurora is the serving store, not a copy of the lake. A scatter "
                 "panel needs a bucketed aggregate or a sample, not every point."),
}


# ------------------------------------------------------------------ infra ----
def conn():
    global _conn
    if _conn is not None:
        return _conn
    sec = json.loads(_sec.get_secret_value(SecretId=RDS_SECRET_ID)["SecretString"])
    host = RDS_HOST or sec.get("host")
    if not host:
        raise RuntimeError("RDS_HOST unset and the secret carries no host")
    # 27-Jul-2026. The SECRET decides the database, not the env var.
    #
    # This read RDS_DATABASE (default "postgres") while the dashboard-api reads
    #     s.get("dbname") or s.get("database") or os.environ.get("DB_NAME","postgres")
    # The secret carries dbname="appdb". So migrate() created every ps4_* table in
    # appdb while this loader connected to postgres and reported
    #     relation "ps4_anomaly_timeline" does not exist
    # -- same cluster, same host, different database. Matching the dashboard-api's
    # resolution order exactly means the two can never drift again, and the env
    # var stays as a deliberate override rather than an accidental default.
    dbname = sec.get("dbname") or sec.get("database") \
        or os.environ.get("RDS_DATABASE") or "postgres"
    log.info("connecting to database=%s on %s", dbname, host)
    _conn = pg8000.native.Connection(
        user=sec.get("username", "postgres"), password=sec["password"], host=host,
        port=int(sec.get("port", RDS_PORT)), database=dbname, timeout=30)
    return _conn


def norm(v):
    """Coerce a parquet cell for binding. NaN -> NULL, never the string 'nan'."""
    if v is None:
        return None
    if isinstance(v, float) and v != v:
        return None
    if isinstance(v, str):
        s = v.strip()
        return None if s == "" or s.lower() in ("nan", "none", "null", "<na>") else s
    if hasattr(v, "isoformat"):
        return v.isoformat()
    return v


def list_parts(bucket, prefix):
    """Every .parquet part under prefix. _SUCCESS and committer files ignored."""
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


def has_success(bucket, prefix):
    """Spark writes _SUCCESS last. Absent means the write did not finish.

    Returns (ok, reason). The bare `except: return False` this replaced reported
    AccessDenied as "write unfinished" -- which sent me looking at Spark when the
    actual fault was an IAM prefix scoped to the wrong path. A 403 and a 404 mean
    completely different things and must not collapse into one answer.
    """
    key = prefix.rstrip("/") + "/_SUCCESS"
    try:
        _s3.head_object(Bucket=bucket, Key=key)
        return True, None
    except Exception as e:
        code = getattr(e, "response", {}).get("Error", {}).get("Code", "")
        status = getattr(e, "response", {}).get(
            "ResponseMetadata", {}).get("HTTPStatusCode")
        if code in ("403", "AccessDenied") or status == 403:
            return False, (f"ACCESS DENIED on s3://{bucket}/{key} -- the Lambda "
                           f"role cannot read this prefix. Check the IAM grant, "
                           f"not the Spark job.")
        if code in ("404", "NoSuchKey", "NotFound") or status == 404:
            return False, f"no _SUCCESS at s3://{bucket}/{key} -- write unfinished"
        return False, f"{type(e).__name__} on s3://{bucket}/{key}: {str(e)[:160]}"


def read_parts(bucket, parts):
    """Read parquet parts into dicts, ONE PART AT A TIME.

    Part-at-a-time on purpose: holding every arrow table at once is what turns a
    350 MB folder into a multi-GB heap. The LOADABLE caps bound the total; this
    bounds the peak.
    """
    import pyarrow.parquet as pq
    rows = []
    for key, _size in parts:
        body = _s3.get_object(Bucket=bucket, Key=key)["Body"].read()
        tbl = pq.read_table(io.BytesIO(body))
        cols = tbl.column_names
        cells = {c: tbl.column(c).to_pylist() for c in cols}
        rows.extend({c: cells[c][i] for c in cols} for i in range(tbl.num_rows))
        del tbl, cells, body
    return rows


def stream_device_daily(bucket, parts, c, city, asof, days_back):
    """Stream device_daily part-by-part: filter to the window, insert, discard.

    3,511,681 device-days cannot be materialised as Python dicts in a 3008 MB
    Lambda -- that is roughly 3.5 GB of dict overhead alone, before the values.
    read_parts() is part-at-a-time but still accumulates every row into one list,
    which is fine for the 72k-row timeline and fatal here.

    This never holds more than one parquet part. Each part is filtered to the
    window, inserted, and dropped. The lifetime rollup is accumulated in a small
    per-device dict as we go -- one entry per device (~4,700), not per device-day
    -- so the full history is summarised without ever holding it.
    """
    import pyarrow.parquet as pq
    cutoff = (_dt.date.fromisoformat(asof) - _dt.timedelta(days=int(days_back))
              if days_back and int(days_back) > 0 else None)

    COLS = ["city_id", "device_type", "device_id", "transit_day", "total_hours",
            "anomaly_hours", "is_anomaly_day", "signal_active_count_max",
            "severity_rank", "anomaly_score_mean", "anomaly_score_max",
            "if_score_mean", "if_score_max", "event_count_mean",
            "first_detected_at", "last_detected_at", "anomaly_types", "asof_date"]
    QUOTED = ", ".join('"' + x + '"' for x in COLS)

    life = {}          # device_id -> lifetime accumulator (bounded by device count)
    n_seen = n_win = 0
    buf = []

    def _flush():
        nonlocal buf
        if not buf:
            return
        ph, args = [], {}
        for ri, r in enumerate(buf):
            nm = ["p%d_%d" % (ri, ci) for ci in range(len(COLS))]
            ph.append("(" + ", ".join(":" + x for x in nm) + ")")
            for n2, cn in zip(nm, COLS):
                args[n2] = r.get(cn)
        c.run("INSERT INTO ps4_device_day (%s) VALUES %s "
              "ON CONFLICT (city_id, device_id, transit_day) DO NOTHING"
              % (QUOTED, ", ".join(ph)), **args)
        buf = []

    for key, _size in parts:
        body = _s3.get_object(Bucket=bucket, Key=key)["Body"].read()
        tbl = pq.read_table(io.BytesIO(body))
        names = tbl.column_names
        cells = {n: tbl.column(n).to_pylist() for n in names}
        for i in range(tbl.num_rows):
            row = {n: cells[n][i] for n in names}
            dev = pick(row, "device_id", "DEVICE_ID")
            dtp = str(pick(row, "device_type", "DEVICE_TYPE") or "").upper()
            day = pick(row, "transit_day", "TRANSIT_DAY")
            if not dev or day is None:
                continue
            day = day.date() if hasattr(day, "date") else day
            th  = int(pick(row, "total_hours") or 0)
            ah  = int(pick(row, "anomaly_hours") or 0)
            isa = bool(pick(row, "is_anomaly_day"))
            n_seen += 1

            # lifetime accumulator -- every row contributes, window or not
            a = life.get(dev)
            if a is None:
                a = life[dev] = {"device_type": dtp, "n_days": 0, "total_hours": 0,
                                 "anomaly_hours": 0, "n_anomaly_days": 0,
                                 "amax": None, "imax": None,
                                 "first": day, "last": day}
            a["n_days"] += 1
            a["total_hours"] += th
            a["anomaly_hours"] += ah
            a["n_anomaly_days"] += 1 if isa else 0
            for k, src in (("amax", "anomaly_score_max"), ("imax", "if_score_max")):
                v = pick(row, src)
                if v is not None and (a[k] is None or float(v) > float(a[k])):
                    a[k] = float(v)
            if day < a["first"]: a["first"] = day
            if day > a["last"]:  a["last"] = day

            if cutoff is not None and day < cutoff:
                continue
            n_win += 1
            types = pick(row, "anomaly_types_set", "anomaly_types")
            if isinstance(types, (list, tuple)):
                types = ",".join(sorted(str(t) for t in types if t is not None))
            buf.append({
                "city_id": city, "device_type": dtp, "device_id": dev,
                "transit_day": day, "total_hours": th, "anomaly_hours": ah,
                "is_anomaly_day": isa,
                "signal_active_count_max": pick(row, "signal_active_count_max"),
                "severity_rank": pick(row, "severity_rank"),
                "anomaly_score_mean": pick(row, "anomaly_score_mean"),
                "anomaly_score_max": pick(row, "anomaly_score_max"),
                "if_score_mean": pick(row, "if_score_mean"),
                "if_score_max": pick(row, "if_score_max"),
                "event_count_mean": pick(row, "event_count_mean"),
                "first_detected_at": pick(row, "first_detected_at"),
                "last_detected_at": pick(row, "last_detected_at"),
                "anomaly_types": types, "asof_date": asof})
            if len(buf) >= 400:
                _flush()
        del tbl, cells, body
    _flush()

    # lifetime rollup, one row per device
    LCOLS = ["city_id", "device_type", "device_id", "n_days", "total_hours",
             "anomaly_hours", "n_anomaly_days", "flagged_rate",
             "anomaly_score_max", "if_score_max", "first_day", "last_day",
             "window_days", "asof_date"]
    LQ = ", ".join('"' + x + '"' for x in LCOLS)
    items = list(life.items())
    for i in range(0, len(items), 400):
        chunk = items[i:i + 400]
        ph, args = [], {}
        for ri, (dev, a) in enumerate(chunk):
            nm = ["q%d_%d" % (ri, ci) for ci in range(len(LCOLS))]
            ph.append("(" + ", ".join(":" + x for x in nm) + ")")
            vals = [city, a["device_type"], dev, a["n_days"], a["total_hours"],
                    a["anomaly_hours"], a["n_anomaly_days"],
                    round(a["anomaly_hours"] / a["total_hours"], 4) if a["total_hours"] else None,
                    a["amax"], a["imax"], a["first"], a["last"],
                    int(days_back or 0), asof]
            for n2, v in zip(nm, vals):
                args[n2] = v
        c.run("INSERT INTO ps4_device_lifetime (%s) VALUES %s "
              "ON CONFLICT (city_id, device_id) DO NOTHING"
              % (LQ, ", ".join(ph)), **args)

    return {"rows_in_s3": n_seen, "rows_loaded_window": n_win,
            "devices_rolled_up": len(life), "window_days": int(days_back or 0),
            "cutoff": str(cutoff) if cutoff else None}


def pick(row, *names):
    """First present key, case-insensitively. Spark and pandas disagree on case."""
    low = {str(k).lower(): v for k, v in row.items()}
    for n in names:
        if n in row:
            return row[n]
        if str(n).lower() in low:
            return low[str(n).lower()]
    return None


def insert(c, table, cols, rows, dry):
    if not rows or dry:
        return len(rows)
    n = 0
    for i in range(0, len(rows), 500):
        chunk = rows[i:i + 500]
        ph, args = [], {}
        for ri, r in enumerate(chunk):
            names = [f"p{ri}_{ci}" for ci in range(len(cols))]
            ph.append("(" + ", ".join(":" + x for x in names) + ")")
            for nm, col in zip(names, cols):
                args[nm] = norm(r.get(col))
        c.run(f"INSERT INTO {table} ({', '.join(cols)}) VALUES " + ", ".join(ph), **args)
        n += len(chunk)
    return n


def latest_asof(bucket, root):
    """Newest asof=<date> partition under root."""
    found, token = set(), None
    root = root.rstrip("/") + "/"
    while True:
        kw = {"Bucket": bucket, "Prefix": root}
        if token:
            kw["ContinuationToken"] = token
        r = _s3.list_objects_v2(**kw)
        for o in r.get("Contents", []):
            m = re.search(r"asof=(\d{4}-\d{2}-\d{2})", o["Key"])
            if m:
                found.add(m.group(1))
        if not r.get("IsTruncated"):
            break
        token = r.get("NextContinuationToken")
    return max(found) if found else None


# ------------------------------------------------------------- transforms ----
def shape_timeline(rows, city, asof):
    out = []
    for r in rows:
        bt = pick(r, "bucket_time")
        dt = pick(r, "device_type", "mars_device_category")
        if bt is None or dt is None:
            continue
        out.append({
            "city_id": city, "asof_date": asof, "bucket_time": bt,
            "device_type": str(dt).upper(),
            # the notebook names this column plain "count"
            "anomaly_count": pick(r, "anomaly_count", "count"),
            "avg_score": pick(r, "avg_score"),
        })
    return out


def shape_assignments(rows, city, asof, device_type):
    out = []
    for r in rows:
        key = pick(r, "device_key", "DEVICE_ID", "device_id")
        if key is None:
            continue
        out.append({
            "city_id": city, "asof_date": asof,
            "device_type": str(pick(r, "device_type") or device_type).upper(),
            "device_key": str(key),
            "device_id": pick(r, "DEVICE_ID", "device_id"),
            "cluster_id": pick(r, "cluster_id"),
            "cluster_confidence": pick(r, "cluster_confidence"),
            "champion_pipeline": pick(r, "champion_pipeline"),
            "champion_run_id": pick(r, "champion_run_id"),
            "champion_silhouette": pick(r, "champion_silhouette"),
            "engine": pick(r, "engine"),
        })
    return out


def shape_cluster_summary(rows, city, asof, device_type):
    out = []
    for r in rows:
        cid = pick(r, "cluster_id")
        if cid is None:
            continue
        # mean_<TARGET_COL> is named after each notebook's own target, so take
        # the first mean_* column that is not the confidence average.
        mean_target = None
        for k, v in r.items():
            if str(k).startswith("mean_") and str(k) not in (
                    "mean_confidence", "mean_cluster_confidence"):
                mean_target = v
                break
        out.append({
            "city_id": city, "asof_date": asof,
            "device_type": str(pick(r, "device_type") or device_type).upper(),
            "cluster_id": cid,
            "device_count": pick(r, "device_count"),
            "mean_target": mean_target,
            "mean_confidence": pick(r, "mean_confidence", "mean_cluster_confidence"),
            "champion_pipeline": pick(r, "champion_pipeline"),
            "champion_silhouette": pick(r, "champion_silhouette"),
        })
    return out


def retire(c, table, city, keep, dry):
    """Keep the newest `keep` as-ofs. Runs AFTER the inserts, same transaction."""
    if dry:
        return []
    rows = c.run(f"SELECT DISTINCT asof_date FROM {table} WHERE city_id=:c "
                 "ORDER BY asof_date DESC", c=city)
    stale = [r[0] for r in rows][keep:]
    for d in stale:
        c.run(f"DELETE FROM {table} WHERE city_id=:c AND asof_date=:d", c=city, d=d)
    return [str(d) for d in stale]


# ----------------------------------------------------------------- handler ----
def lambda_handler(event, context):
    event = event or {}
    dry = bool(event.get("dry_run"))
    city = (event.get("city") or CITY_ID).upper()
    result = {"dry_run": dry, "city": city, "loaded": {}, "refused": {}, "skipped": {}}

    asof = event.get("asof")
    for rec in event.get("Records") or []:
        if (rec.get("eventSource") or rec.get("EventSource")) == "aws:s3":
            m = re.search(r"asof=(\d{4}-\d{2}-\d{2})", rec["s3"]["object"]["key"])
            if m:
                asof = m.group(1)
    scored_asof = asof or latest_asof(ARTIFACT_BUCKET, f"{PS4_PREFIX}/scored")
    cluster_asof = asof or latest_asof(GOLD_BUCKET, PS4_CLUSTER_PREFIX)
    result["scored_asof"] = scored_asof
    result["cluster_asof"] = cluster_asof

    # The two pipelines carry DIFFERENT as-of dates: the anomaly run sets asof
    # from max(hour_dt) in the DATA (2026-04-11) while clustering uses the RUN
    # date (2026-07-27). Both are correct. Reported separately so the dashboard
    # labels them rather than implying one snapshot.
    if scored_asof and cluster_asof and scored_asof != cluster_asof:
        result["note_asof_differs"] = (
            f"scored asof={scored_asof} is max(hour_dt) in the data; clustering "
            f"asof={cluster_asof} is the run date. Label them separately on screen.")

    result["refused"].update(REFUSED)

    c = conn()
    if not dry:
        c.run("BEGIN")
    try:
        # ---- timeline (artifacts bucket) ----
        if scored_asof:
            pre = f"{PS4_PREFIX}/scored/asof={scored_asof}/timeline"
            ok, why = has_success(ARTIFACT_BUCKET, pre)
            if not ok:
                result["skipped"]["timeline"] = why
            else:
                parts = list_parts(ARTIFACT_BUCKET, pre)
                rows = read_parts(ARTIFACT_BUCKET, parts)
                spec = LOADABLE["timeline"]
                if len(rows) > spec["max_rows"]:
                    result["refused"]["timeline"] = (
                        f"{len(rows):,} rows exceeds the {spec['max_rows']:,} cap")
                else:
                    shaped = shape_timeline(rows, city, scored_asof)
                    if not dry:
                        c.run("DELETE FROM ps4_anomaly_timeline WHERE city_id=:c "
                              "AND asof_date=:d", c=city, d=scored_asof)
                    n = insert(c, spec["table"], spec["cols"], shaped, dry)
                    result["loaded"]["timeline"] = {
                        "table": spec["table"], "parts": len(parts),
                        "rows_read": len(rows), "rows_loaded": n}
        else:
            result["skipped"]["timeline"] = (
                f"no asof= partition under s3://{ARTIFACT_BUCKET}/{PS4_PREFIX}/scored/")

        # ---- device_daily (artifacts bucket) -- the aggregated feed ------------
        # 28-Jul-2026. The PS4 run now emits device_daily: 36,629,754 hourly rows
        # compressed to 3,511,681 device-days (9.59%).
        #
        # anomalies (2,848,215) and outliers (3,511,681) are NOT loaded and are
        # NOT lost. anomalies is device_daily filtered to is_anomaly_day; outliers
        # is device_daily with three columns renamed (x_metric = event_count_mean,
        # y_metric and z_score = if_score_mean -- all already present). sql/33
        # reproduces both as views. Loading them would put 9.9M rows in the
        # serving tier to express 3.5M rows of information.
        if scored_asof:
            pre = f"{PS4_PREFIX}/scored/asof={scored_asof}/device_daily"
            ok_s, why = has_success(ARTIFACT_BUCKET, pre)
            if not ok_s:
                result["skipped"]["device_daily"] = why
            else:
                parts = list_parts(ARTIFACT_BUCKET, pre)
                if not parts:
                    result["skipped"]["device_daily"] = f"no parquet parts under {pre}"
                elif dry:
                    result["loaded"]["device_daily"] = {
                        "table": "ps4_device_day + ps4_device_lifetime",
                        "parts": len(parts), "window_days": DAYS_BACK,
                        "note": "dry run -- streaming loader not executed"}
                else:
                    # Window then insert, never materialising the full 3.5M rows.
                    c.run("DELETE FROM ps4_device_day     WHERE city_id=:c", c=city)
                    c.run("DELETE FROM ps4_device_lifetime WHERE city_id=:c", c=city)
                    st = stream_device_daily(ARTIFACT_BUCKET, parts, c, city,
                                             scored_asof, DAYS_BACK)
                    st["table"] = "ps4_device_daily + ps4_device_lifetime"
                    st["parts"] = len(parts)
                    result["loaded"]["device_daily"] = st
        else:
            result["skipped"]["device_daily"] = "no scored asof= partition"

        # ---- clustering (gold bucket), one folder per device type ----
        if cluster_asof:
            for slug in DEVICE_SLUGS:
                for feed, shaper in (("assignments", shape_assignments),
                                     ("cluster_summary", shape_cluster_summary)):
                    pre = f"{PS4_CLUSTER_PREFIX}/{slug}/asof={cluster_asof}/{feed}"
                    tag = f"{slug}.{feed}"
                    ok, why = has_success(GOLD_BUCKET, pre)
                    if not ok:
                        result["skipped"][tag] = why
                        continue
                    parts = list_parts(GOLD_BUCKET, pre)
                    if not parts:
                        result["skipped"][tag] = "no parquet parts"
                        continue
                    rows = read_parts(GOLD_BUCKET, parts)
                    spec = LOADABLE[feed]
                    if len(rows) > spec["max_rows"]:
                        result["refused"][tag] = (
                            f"{len(rows):,} rows exceeds the {spec['max_rows']:,} cap")
                        continue
                    shaped = shaper(rows, city, cluster_asof, slug.upper())
                    if not dry:
                        c.run(f"DELETE FROM {spec['table']} WHERE city_id=:c "
                              "AND asof_date=:d AND device_type=:t",
                              c=city, d=cluster_asof, t=slug.upper())
                    n = insert(c, spec["table"], spec["cols"], shaped, dry)
                    result["loaded"][tag] = {
                        "table": spec["table"], "parts": len(parts),
                        "rows_read": len(rows), "rows_loaded": n}
        else:
            result["skipped"]["clustering"] = (
                f"no asof= partition under s3://{GOLD_BUCKET}/{PS4_CLUSTER_PREFIX}/")

        # ---- retire old snapshots, AFTER the inserts ----
        result["retired"] = {
            t: retire(c, t, city, KEEP_SNAPSHOTS, dry)
            for t in ("ps4_anomaly_timeline", "ps4_cluster_assignments",
                      "ps4_cluster_summary")}

        if not dry:
            c.run("COMMIT")
        result["status"] = "dry_run_ok" if dry else "committed"
    except Exception as e:
        if not dry:
            try:
                c.run("ROLLBACK")
            except Exception:
                pass
        log.exception("ps4 load failed")
        result["status"] = "failed"
        result["error"] = str(e)[:600]

    # Audit AFTER the commit -- an audit row for a rolled-back load would be a lie.
    if not dry and result.get("status") == "committed":
        try:
            for feed, info in result["loaded"].items():
                c.run("INSERT INTO ml_batch_load_audit (city_id, ps_id, run_id, "
                      "target_table, s3_source, rows_read, rows_loaded, status) "
                      "VALUES (:c,'PS4',:r,:t,:s,:rr,:rl,'success')",
                      c=city, r=f"ps4_{scored_asof or cluster_asof}",
                      t=info["table"], s=feed,
                      rr=info["rows_read"], rl=info["rows_loaded"])
        except Exception as e:
            result["audit_warning"] = str(e)[:200]

    log.info(json.dumps(result, default=str)[:2000])
    return {"statusCode": 200 if result.get("status") != "failed" else 500,
            "body": json.dumps(result, default=str)}
