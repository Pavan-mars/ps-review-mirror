"""
cubic-mars-ps5-rds-loader   27-Jul-2026

s3://<gold>/chicago/ps5/notebook_outputs/  ->  Aurora ps5_*

LAYOUT (verified against the real listing, not assumed):
    <device>/<device>_<artifact>.csv      device in {gates, tvm, validators}
    ps5_rds_load_manifest.json            names what the run intends for RDS

Built on the cubic-mars-ps2-rds-loader, which carries four fixes earned the hard
way this morning and all of which apply here:

  1. SCHEMA-ADAPTIVE. Target columns come from information_schema at runtime and
     the loader inserts the intersection. Hand-written column lists are N chances
     to typo a name that then loads as NULL and looks like missing data.
  2. IDENTIFIER QUOTING. Every column name is quoted. "window", "rank", "role",
     "end" and "order" are reserved words that appear in these exports.
  3. PER-TABLE SAVEPOINTS. One bad file rolls back to its own savepoint; the rest
     still commit. Without this, table 9 of 22 failing discarded the other 21.
  4. PK-COLLAPSE DETECTION. If dropping an unmapped column collapses distinct
     rows onto one primary key, refuse THAT file and name the column, rather than
     letting Postgres raise 23505 mid-transaction.

New here: CSV instead of parquet (no pyarrow needed), a device dimension parsed
from the folder rather than a column, and dry_run doubling as the S3-vs-Aurora
reconciliation report.
"""
import csv, io, json, os, re, boto3, pg8000.native

REGION        = os.environ.get("AWS_REGION", "us-east-1")
GOLD_BUCKET   = os.environ.get("GOLD_BUCKET", "cubic-mars-pm-s3-datalake-dev-gold-170202974600")
PS5_PREFIX    = os.environ.get("PS5_PREFIX", "chicago/ps5/notebook_outputs")
RDS_SECRET_ID = os.environ.get("RDS_SECRET_ID", "cubic-mars-secret-rds-dev")
RDS_HOST      = os.environ.get("RDS_HOST", "")
RDS_PORT      = int(os.environ.get("RDS_PORT", "5432"))
CITY_ID       = os.environ.get("CITY_ID", "CHI")
MIN_MATCH     = float(os.environ.get("MIN_MATCH", "0.5"))

# Folder name -> the device_type value the dashboard already uses everywhere
# else. The export folders are plural/lowercase; the rest of the schema is
# singular/upper. Normalising HERE means no downstream route has to know.
DEVICE = {"gates": "GATE", "tvm": "TVM", "validators": "VALIDATOR"}

# <artifact suffix> -> Aurora table. Only these five are loaded; the .png, the
# .parquet device_state and the *_params.json files are model internals with no
# reader on the dashboard, and copying them into the serving tier buys nothing.
ARTIFACTS = {
    # Targets are the sql/29 tables, NOT ps5_reliability_estimates. That table
    # declares device_type on an enum that never got created in Aurora -- the
    # same broken column that 500'd the Analyse modal earlier today. Loading
    # into it would inherit the fault.
    "device_rul_estimates":    "ps5_device_rul",
    # 27-Jul-2026. Repointed to ps5_serial_rul (sql/30). The legacy
    # ps5_serial_reliability has as_of_date and component_serial_nbr NOT
    # NULL and the export fills neither -- all three device files rolled
    # back on 23502. sql/02 still writes to the legacy table, so it is
    # left untouched rather than reshaped.
    "serial_reliability":      "ps5_serial_rul",
    "cindex_leaderboard_v5":   "ps5_cindex_leaderboard",
    "permutation_importance":  "ps5_permutation_importance",
    "enrich_coverage":         "ps5_enrich_coverage",
}

# Per-(artifact) renames, applied before the intersection. Keyed by artifact, not
# global: a global rename is right for one file and silently wrong for another,
# which is how the PS2 loader dropped both antecedent columns on its first run.
COL_ALIASES = {
    # Read off the real CSV headers, not guessed. device_rul_estimates and
    # serial_reliability already use the target names, so they need none.
    #
    # window and table are RESERVED words in PostgreSQL. The loader quotes every
    # identifier, so a quoted "window" would work -- but the dashboard's route
    # SQL is hand-written and would not quote it. Renaming at the boundary
    # removes the whole class instead of the one instance that fails first.
    "cindex_leaderboard_v5": {"window": "window_label"},
    "permutation_importance": {"feature": "feature_name"},
    "enrich_coverage": {"table": "table_name", "source": "source_name"},
}

# A row without its key is not a row, whatever the coverage percentage says.
IDENTITY_COLS = ("device_id", "component_serial_nbr", "model", "feature_name")

_s3  = boto3.client("s3", region_name=REGION)
_sec = boto3.client("secretsmanager", region_name=REGION)
_conn = None


def conn():
    global _conn
    if _conn is not None:
        return _conn
    sec = json.loads(_sec.get_secret_value(SecretId=RDS_SECRET_ID)["SecretString"])
    # The secret carries dbname=appdb; migrate() creates every table there.
    # Defaulting to "postgres" cost a full debug cycle on the PS4 loader.
    dbname = (sec.get("dbname") or sec.get("database")
              or os.environ.get("RDS_DATABASE") or "postgres")
    _conn = pg8000.native.Connection(
        user=sec.get("username", "postgres"), password=sec["password"],
        host=RDS_HOST or sec.get("host"), port=int(sec.get("port", RDS_PORT)),
        database=dbname, ssl_context=True, timeout=120)
    print("connected to database=%s" % dbname)
    return _conn


def target_columns(c, table):
    r = c.run("SELECT column_name FROM information_schema.columns "
              "WHERE table_schema='public' AND table_name=:t ORDER BY ordinal_position", t=table)
    return [x[0] for x in r] if r else None


def pk_columns(c, table):
    r = c.run("SELECT a.attname FROM pg_index i "
              "JOIN pg_attribute a ON a.attrelid=i.indrelid AND a.attnum=ANY(i.indkey) "
              "WHERE i.indrelid=to_regclass('public.'||:t) AND i.indisprimary", t=table)
    return [x[0] for x in r] if r else []


def rds_count(c, table):
    try:
        return c.run("SELECT COUNT(*) FROM %s WHERE city_id=:c" % table, c=CITY_ID)[0][0]
    except Exception:
        return None


def norm(v):
    if v is None:
        return None
    s = str(v).strip()
    if s == "" or s.lower() in ("nan", "none", "null", "<na>", "na"):
        return None
    return s


def find_pk_collapse(rows_, use, pk):
    keys = [k for k in pk if k in use]
    if not keys or not rows_:
        return None
    seen = {}
    for r in rows_:
        k = tuple(norm(r.get(x)) for x in keys)
        if k in seen:
            a = seen[k]
            return {"pk": keys, "example_key": [str(x) for x in k],
                    "separating_columns": sorted(
                        col for col in set(a) | set(r)
                        if col not in keys and norm(a.get(col)) != norm(r.get(col))),
                    "rows_in": len(rows_)}
        seen[k] = r
    return None


def read_csv(bucket, key):
    body = _s3.get_object(Bucket=bucket, Key=key)["Body"].read().decode("utf-8-sig")
    return list(csv.DictReader(io.StringIO(body)))


def lambda_handler(event, context):
    event = event or {}
    dry  = bool(event.get("dry_run"))
    only = set(event.get("only") or [])
    res = {"dry_run": dry, "city": CITY_ID, "loaded": {}, "no_target": {},
           "refused": {}, "skipped": {}, "errors": {}, "reconcile": {}}

    c = conn()
    if not dry:
        c.run("BEGIN")
    try:
        for folder, dev in sorted(DEVICE.items()):
            for art, tgt in sorted(ARTIFACTS.items()):
                tag = "%s/%s" % (folder, art)
                if only and art not in only and tag not in only:
                    continue
                key = "%s/%s/%s_%s.csv" % (PS5_PREFIX, folder, folder, art)
                try:
                    src = read_csv(GOLD_BUCKET, key)
                except Exception as e:
                    res["skipped"][tag] = "not readable: %s" % str(e)[:120]
                    continue
                if not src:
                    res["skipped"][tag] = "0 rows in S3"
                    continue

                tcols = target_columns(c, tgt)
                if tcols is None:
                    res["no_target"][tag] = {"rows_in_s3": len(src), "table": tgt,
                                             "columns": sorted(src[0].keys())}
                    continue

                al = COL_ALIASES.get(art, {})
                shaped = []
                for r in src:
                    lo = {k.lower(): v for k, v in r.items()}
                    o = {al.get(k, k): v for k, v in lo.items()}
                    o.setdefault("city_id", CITY_ID)
                    # device_type comes from the FOLDER, not from a column. The
                    # export splits by device and does not repeat it inside.
                    if "device_type" in tcols:
                        o.setdefault("device_type", dev)
                    if "device_category" in tcols:
                        o.setdefault("device_category", dev)
                    shaped.append(o)

                srcset = set(shaped[0].keys())
                use = [x for x in tcols if x in srcset and x != "id"]
                dropped = sorted(srcset - set(use))
                missing = sorted(set(tcols) - srcset)

                idm = [x for x in tcols if x in IDENTITY_COLS and x not in use]
                if idm:
                    res["refused"][tag] = {"reason": "identity column(s) %s unfilled" % idm,
                                           "matched": use, "unused_from_source": dropped}
                    continue
                cover = len(use) / max(1, len([x for x in tcols if x != "id"]))
                if cover < MIN_MATCH:
                    res["refused"][tag] = {"reason": "only %d/%d target columns matched (%.0f%%)"
                                           % (len(use), len(tcols), cover * 100),
                                           "matched": use, "missing_in_source": missing,
                                           "unused_from_source": dropped}
                    continue
                col = find_pk_collapse(shaped, use, pk_columns(c, tgt))
                if col:
                    res["refused"][tag] = dict(col, reason="rows collapse on %s's primary key; "
                                               "separating column(s) %s are not in the table"
                                               % (tgt, col["separating_columns"]))
                    continue

                res["reconcile"][tag] = {"table": tgt, "rows_in_s3": len(src),
                                         "rows_in_rds_before": rds_count(c, tgt)}
                if not dry:
                    sp = "sp_" + re.sub(r"\W", "_", tag)[:50]
                    c.run("SAVEPOINT %s" % sp)
                    try:
                        # Scoped to THIS device, so loading gates cannot wipe the
                        # tvm rows loaded a moment earlier.
                        dcol = "device_type" if "device_type" in tcols else (
                               "device_category" if "device_category" in tcols else None)
                        if dcol:
                            c.run('DELETE FROM %s WHERE city_id=:c AND "%s"=:d' % (tgt, dcol),
                                  c=CITY_ID, d=dev)
                        else:
                            c.run("DELETE FROM %s WHERE city_id=:c" % tgt, c=CITY_ID)
                        for i in range(0, len(shaped), 500):
                            chunk = shaped[i:i + 500]
                            ph, args = [], {}
                            for ri, r in enumerate(chunk):
                                nm = ["p%d_%d" % (ri, ci) for ci in range(len(use))]
                                ph.append("(" + ", ".join(":" + x for x in nm) + ")")
                                for n2, cn in zip(nm, use):
                                    args[n2] = norm(r.get(cn))
                            q = ", ".join('"' + x + '"' for x in use)
                            c.run("INSERT INTO %s (%s) VALUES %s" % (tgt, q, ", ".join(ph)), **args)
                        c.run("RELEASE SAVEPOINT %s" % sp)
                    except Exception as te:
                        c.run("ROLLBACK TO SAVEPOINT %s" % sp)
                        res["errors"][tag] = {"table": tgt, "error": str(te)[:400]}
                        continue

                res["loaded"][tag] = {"table": tgt, "device": dev, "rows": len(shaped),
                                      "columns_used": use, "columns_dropped": dropped,
                                      "columns_missing": missing}
        if not dry:
            c.run("COMMIT")
        # A skipped, refused or errored artifact keeps the PREVIOUS run's rows in its table while the
        # rest are replaced, so the dashboard mixes two runs. That must not read as a clean commit.
        _gaps = sorted(set(res["errors"]) | set(res["refused"]) | set(res["skipped"]) | set(res["no_target"]))
        res["incomplete"] = _gaps
        if dry:
            res["status"] = "dry_run_ok" if not _gaps else "dry_run_incomplete"
        else:
            res["status"] = "committed" if not _gaps else "committed_partial"
    except Exception as e:
        if not dry:
            try:
                c.run("ROLLBACK")
            except Exception:
                pass
        res["status"] = "failed"
        res["error"] = str(e)[:600]

    res["summary"] = {"loaded": len(res["loaded"]), "no_target": len(res["no_target"]),
                      "refused": len(res["refused"]), "skipped": len(res["skipped"]),
                      "errors": len(res["errors"]),
                      "rows": sum(v["rows"] for v in res["loaded"].values())}
    # Compact line FIRST: the full dump is truncated and status serialises last,
    # so on a 15-file run the log could not answer "did it commit".
    print("PS5", res.get("status"), json.dumps(res["summary"]))
    # 200 only for a complete load. A returned 500 is not a Lambda error metric -- alarm on the log line
    # (CloudWatch metric filter: "PS5 committed_partial") to catch a scheduled run with a gap.
    return {"statusCode": 200 if res.get("status") in ("committed", "dry_run_ok") else 500,
            "body": json.dumps(res, default=str)}
