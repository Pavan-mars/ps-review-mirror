"""
cubic-mars-dim-loader
=====================
DAILY refresh of the top-level device <-> serial <-> component map.

  Databricks  ->  s3://<gold>/chicago/dim/device_serial/<yyyy-mm-dd>/
                      device_serial.parquet
                      device_event_totals.parquet   (optional)
                      manifest.json                 <-- written LAST; the trigger
              ->  THIS LAMBDA (inside the RDS VPC)
              ->  dim_device_serial, device_event_totals
              ->  every dashboard, via the dashboard-api

Tables come from sql/22_dim_device_serial.sql, applied by the dashboard-api
`action=migrate`. This Lambda has no DDL and cannot create or alter them.

TRIGGERS
  1. EventBridge daily 05:45 UTC  -> loads the newest dated folder
  2. S3 ObjectCreated on manifest -> loads that folder
  3. Manual  {"as_of": "2026-07-27", "dry_run": true}

WHY THE TWO TABLES ARE LOADED SEPARATELY
  The source extract repeats each device's event totals on EVERY serial row --
  1..7 times. Summing the rows inflates the fleet total 2.60x (103,927,823
  against a true 39,969,550). So the serial map is loaded at device+serial grain
  with NO additive measures, and the totals are collapsed to ONE ROW PER DEVICE
  before insert. The loader does that collapse itself and REFUSES the file if a
  device's rows disagree on their totals -- that disagreement would mean the
  source is no longer device-grain and the collapse would silently pick one.

SWAP, NOT TRUNCATE
  The refresh writes the new as_of_date first and only then deletes older
  snapshots, inside one transaction. A crash mid-load leaves yesterday's map
  intact rather than an empty dimension that every dashboard joins to.

Runtime deps: pg8000 (bundled) + pyarrow (AWS SDK for pandas managed layer).
Env: GOLD_BUCKET, DIM_PREFIX, RDS_HOST, RDS_PORT, RDS_DATABASE, RDS_SECRET_ID,
     CITY_ID, KEEP_SNAPSHOTS
"""
import datetime, io, json, logging, os

import boto3
import pg8000.native
import pyarrow.parquet as pq

log = logging.getLogger()
log.setLevel(logging.INFO)

REGION        = os.environ.get("AWS_REGION", "us-east-1")
GOLD_BUCKET   = os.environ.get("GOLD_BUCKET", "cubic-mars-pm-s3-datalake-dev-gold-170202974600")
DIM_PREFIX    = os.environ.get("DIM_PREFIX", "chicago/dim/device_serial")
RDS_SECRET_ID = os.environ.get("RDS_SECRET_ID", "cubic-mars-secret-rds-dev")
RDS_DATABASE  = os.environ.get("RDS_DATABASE", "postgres")
RDS_HOST      = os.environ.get("RDS_HOST", "")
RDS_PORT      = int(os.environ.get("RDS_PORT", "5432"))
CITY_ID       = os.environ.get("CITY_ID", "CHI")
KEEP          = int(os.environ.get("KEEP_SNAPSHOTS", "3"))   # dated snapshots retained

BATCH = 500
_s3   = boto3.client("s3", region_name=REGION)
_sec  = boto3.client("secretsmanager", region_name=REGION)
_conn = None

SERIAL_COLS = ["device_id", "serial_id", "mars_device_category",
               "component_description", "component_age_days"]
TOTAL_COLS  = ["device_id", "mars_device_category",
               "total_hardware_oos_events", "total_chargeable_events"]

# The source column names differ from ours in case and spelling; map rather than
# rename upstream, so a Databricks change does not silently drop a column.
ALIAS = {
    "device_id":                 ["device_id", "DEVICE_ID"],
    "serial_id":                 ["serial_id", "SERIAL_ID", "COMPONENT_SERIAL_NBR", "matched_serial_nbr"],
    "mars_device_category":      ["mars_device_category", "device_category", "MARS_DEVICE_CATEGORY"],
    "component_description":     ["component_description", "COMPONENT_DESCRIPTION", "component_type"],
    "component_age_days":        ["component_age_days", "COMPONENT_AGE_DAYS"],
    "total_hardware_oos_events": ["total_hardware_oos_events", "total_oos_events"],
    "total_chargeable_events":   ["total_chargeable_events", "chargeable_events"],
}

GRAIN_NOTE = os.environ.get("GRAIN_NOTE",
    "fault records, NOT out-of-service occurrences - median 8/device/day; grain unresolved")


def conn():
    global _conn
    if _conn is None:
        sec = json.loads(_sec.get_secret_value(SecretId=RDS_SECRET_ID)["SecretString"])
        # 27-Jul-2026. The SECRET decides the database, not the env var.
        # This said database=RDS_DATABASE (default "postgres"), but the secret
        # carries dbname="appdb" and that is where migrate() creates every table.
        # The PS4 loader had the identical line and failed with
        #     relation "ps4_anomaly_timeline" does not exist
        # -- right cluster, right host, wrong database. Same resolution order as
        # the dashboard-api so the two cannot drift.
        dbname = sec.get("dbname") or sec.get("database") \
            or os.environ.get("RDS_DATABASE") or "postgres"
        log.info("connecting to database=%s", dbname)
        _conn = pg8000.native.Connection(
            user=sec.get("username", "postgres"), password=sec["password"],
            host=RDS_HOST or sec.get("host"), port=RDS_PORT, database=dbname,
            ssl_context=True, timeout=60)
    return _conn


def pick(row, field):
    for k in ALIAS[field]:
        if k in row and row[k] is not None:
            return row[k]
    return None


def read_parquet(bucket, key):
    keys = [key] if key.endswith(".parquet") else []
    if not keys:
        pfx = key if key.endswith("/") else key + "/"
        tok = None
        while True:
            kw = {"Bucket": bucket, "Prefix": pfx}
            if tok: kw["ContinuationToken"] = tok
            r = _s3.list_objects_v2(**kw)
            keys += [o["Key"] for o in r.get("Contents", []) if o["Key"].endswith(".parquet")]
            tok = r.get("NextContinuationToken")
            if not r.get("IsTruncated"): break
    out = []
    for k in keys:
        body = _s3.get_object(Bucket=bucket, Key=k)["Body"].read()
        out += pq.read_table(io.BytesIO(body)).to_pylist()
    return out


def norm(v):
    if v is None: return None
    if isinstance(v, float) and v != v: return None
    if isinstance(v, bytes): return v.decode("utf-8", "replace")
    return v


def insert(c, table, cols, rows, dry):
    if not rows or dry: return len(rows)
    done = 0
    for i in range(0, len(rows), BATCH):
        chunk = rows[i:i + BATCH]
        ph, params = [], {}
        for j, r in enumerate(chunk):
            ph.append("(" + ", ".join(f":p{j}_{k}" for k in range(len(cols))) + ")")
            for k, col in enumerate(cols):
                params[f"p{j}_{k}"] = norm(r.get(col))
        c.run(f"INSERT INTO {table} ({', '.join(cols)}) VALUES " + ", ".join(ph), **params)
        done += len(chunk)
    return done


def latest_prefix():
    r = _s3.list_objects_v2(Bucket=GOLD_BUCKET, Prefix=DIM_PREFIX.rstrip("/") + "/", Delimiter="/")
    pres = [p["Prefix"] for p in r.get("CommonPrefixes", [])]
    return sorted(pres)[-1] if pres else None


def lambda_handler(event, context):
    event = event or {}
    dry = bool(event.get("dry_run"))

    if "Records" in event:
        key = event["Records"][0]["s3"]["object"]["key"]
        if not key.endswith("manifest.json"):
            return {"statusCode": 200, "body": json.dumps({"skipped": f"not a manifest: {key}"})}
        prefix = key.rsplit("/", 1)[0] + "/"
    elif event.get("as_of"):
        prefix = f"{DIM_PREFIX.rstrip('/')}/{event['as_of']}/"
    else:
        prefix = latest_prefix()
    if not prefix:
        return {"statusCode": 404, "body": json.dumps(
            {"error": f"no dated folders under s3://{GOLD_BUCKET}/{DIM_PREFIX}/"})}

    try:
        man = json.loads(_s3.get_object(Bucket=GOLD_BUCKET,
                                        Key=prefix + "manifest.json")["Body"].read())
    except Exception as e:
        return {"statusCode": 404, "body": json.dumps(
            {"error": f"no manifest at {prefix}manifest.json: {str(e)[:200]}"})}

    city  = (man.get("city_id") or CITY_ID).upper()
    as_of = man.get("as_of_date") or prefix.rstrip("/").rsplit("/", 1)[-1]
    src   = man.get("source_table") or "unspecified"
    out   = {"as_of": as_of, "city": city, "dry_run": dry,
             "s3_prefix": f"s3://{GOLD_BUCKET}/{prefix}"}

    raw = read_parquet(GOLD_BUCKET, prefix + (man.get("device_serial") or "device_serial.parquet"))
    if not raw:
        return {"statusCode": 400, "body": json.dumps(
            {"error": "device_serial file is empty; refusing to replace a working dimension",
             **out})}

    # ---- serial grain: dedupe on (device, serial) ------------------------
    serial, seen = [], set()
    for r in raw:
        did, sid = pick(r, "device_id"), pick(r, "serial_id")
        if not did or sid is None: continue
        k = (str(did), str(sid))
        if k in seen: continue
        seen.add(k)
        serial.append({"city_id": city, "device_id": str(did), "serial_id": str(sid),
                       "mars_device_category": pick(r, "mars_device_category"),
                       "component_description": pick(r, "component_description"),
                       "component_age_days": pick(r, "component_age_days"),
                       "source_table": src, "as_of_date": as_of})

    # ---- device grain: collapse the repeated totals ----------------------
    # REFUSE rather than pick one if a device's rows disagree -- that would mean
    # the extract is no longer device-grain and any collapse would be a guess.
    totals, conflict = {}, []
    for r in raw:
        did = pick(r, "device_id")
        if not did: continue
        o, c_ = pick(r, "total_hardware_oos_events"), pick(r, "total_chargeable_events")
        if o is None and c_ is None: continue
        prev = totals.get(str(did))
        cur  = (o, c_)
        if prev and prev["k"] != cur:
            conflict.append(str(did)); continue
        totals[str(did)] = {"k": cur, "city_id": city, "device_id": str(did),
                            "mars_device_category": pick(r, "mars_device_category"),
                            "total_hardware_oos_events": o, "total_chargeable_events": c_,
                            "period_start": man.get("period_start"),
                            "period_end": man.get("period_end"),
                            "grain_note": man.get("grain_note") or GRAIN_NOTE,
                            "source_table": src, "as_of_date": as_of}
    if conflict:
        return {"statusCode": 400, "body": json.dumps({
            "error": f"{len(conflict)} device(s) have serial rows that disagree on their "
                     f"event totals, so the extract is not device-grain. Nothing loaded.",
            "sample": conflict[:10], **out})}

    out["rows_parsed"] = {"dim_device_serial": len(serial),
                          "device_event_totals": len(totals)}
    if dry:
        out["status"] = "dry_run"
        return {"statusCode": 200, "body": json.dumps(out, default=str)}

    c = conn()
    try:
        c.run("BEGIN")
        # write the new snapshot FIRST, then retire old ones -- a failure here
        # leaves yesterday's dimension in place rather than an empty table.
        c.run("DELETE FROM dim_device_serial WHERE city_id=:c AND as_of_date=:d", c=city, d=as_of)
        c.run("DELETE FROM device_event_totals WHERE city_id=:c AND as_of_date=:d", c=city, d=as_of)
        n1 = insert(c, "dim_device_serial",
                    ["city_id", "device_id", "serial_id", "mars_device_category",
                     "component_description", "component_age_days", "source_table", "as_of_date"],
                    serial, False)
        n2 = insert(c, "device_event_totals",
                    ["city_id", "device_id", "mars_device_category",
                     "total_hardware_oos_events", "total_chargeable_events",
                     "period_start", "period_end", "grain_note", "source_table", "as_of_date"],
                    list(totals.values()), False)
        keep = [r[0] for r in c.run(
            "SELECT DISTINCT as_of_date FROM dim_device_serial WHERE city_id=:c "
            "ORDER BY as_of_date DESC LIMIT :k", c=city, k=KEEP)]
        if keep:
            c.run("DELETE FROM dim_device_serial WHERE city_id=:c AND as_of_date <> ALL(:k)",
                  c=city, k=keep)
            c.run("DELETE FROM device_event_totals WHERE city_id=:c AND as_of_date <> ALL(:k)",
                  c=city, k=keep)
        c.run("COMMIT")
        out.update(status="committed", rows_loaded={"dim_device_serial": n1,
                                                    "device_event_totals": n2},
                   snapshots_retained=[str(d) for d in keep])
    except Exception as e:
        try: c.run("ROLLBACK")
        except Exception: pass
        out.update(status="rolled_back", error=str(e)[:400],
                   note="One transaction: yesterday's dimension is untouched.")
        return {"statusCode": 500, "body": json.dumps(out, default=str)}

    for t in ("dim_device_serial", "device_event_totals"):
        try:
            out.setdefault("rows_after", {})[t] = int(c.run(
                f"SELECT COUNT(*) FROM {t} WHERE city_id=:c AND as_of_date=:d",
                c=city, d=as_of)[0][0])
        except Exception as e:
            out.setdefault("rows_after", {})[t] = f"count failed: {str(e)[:80]}"
    return {"statusCode": 200, "body": json.dumps(out, default=str)}
