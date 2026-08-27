"""
cubic-mars-dim-incident-loader -- loads dim_device_incident_cmdb (device -> CMDB CI ->
ServiceNow incident map) into Aurora appdb from the S3 export written by the
Databricks job device_incident_cmdb_daily.

Modeled on cubic-mars-dim-loader: one transaction, snapshot-dated rows,
KEEP_SNAPSHOTS retention, refuse-empty-export, dry_run support. The secret's
dbname (appdb) wins over any env var, same as every other loader.

Invoke shapes:
  {"dry_run": true}          parse + validate the latest export, load nothing
  {"as_of": "2026-08-27"}    load the newest run under that as_of_date folder
  {}                         load the newest manifest under the whole prefix
  S3 event on manifest.json  load that run
"""
import os, io, json, boto3, pg8000.native

GOLD_BUCKET = os.environ.get("GOLD_BUCKET", "cubic-mars-pm-s3-datalake-dev-gold-170202974600")
PREFIX      = os.environ.get("DIC_PREFIX", "chicago/dim/device_incident_cmdb").rstrip("/")
SECRET_ID   = os.environ.get("RDS_SECRET_ID", "cubic-mars-secret-rds-dev")
RDS_HOST    = os.environ.get("RDS_HOST")
RDS_PORT    = int(os.environ.get("RDS_PORT", "5432"))
CITY_ID     = os.environ.get("CITY_ID", "CHI")
KEEP        = int(os.environ.get("KEEP_SNAPSHOTS", "3"))
TABLE       = "dim_device_incident_cmdb"

_s3 = boto3.client("s3")
_sm = boto3.client("secretsmanager")

# Idempotent DDL, run outside the load transaction. The same DDL lives in the
# repo (device_incident_cmdb_automation/sql/) and must ALSO be folded into the
# dashboard-api migrate() tuple at its next deploy, so a cold rebuild recreates
# the table without this loader (avoids a B15-class live-only object).
DDL = [
    """CREATE TABLE IF NOT EXISTS dim_device_incident_cmdb (
        city_id               text        NOT NULL DEFAULT 'CHI',
        device_id             text        NOT NULL,
        device_key            text,
        device_name           text,
        bus_id                text,
        bus_device_flag       boolean,
        serial_number         text,
        device_serial_number  text,
        component_serial_nbr  text,
        component_type        text,
        facility_id           text,
        facility_name         text,
        operator_id           text,
        operator_name         text,
        mars_device_category  text,
        device_type_name      text,
        transit_mode_name     text,
        incident_number       text,
        incident_sys_id       text,
        cmdb_ci_sys_id        text,
        opened_at             timestamptz,
        closed_at             timestamptz,
        all_incident_numbers  text,
        all_incident_sys_ids  text,
        all_cmdb_ci_sys_ids   text,
        incident_count        bigint      NOT NULL DEFAULT 0,
        as_of_date            date        NOT NULL,
        run_id                text,
        loaded_at             timestamptz DEFAULT now(),
        PRIMARY KEY (city_id, device_id, as_of_date)
    )""",
    "CREATE INDEX IF NOT EXISTS ix_ddic_cmdb_ci ON dim_device_incident_cmdb (cmdb_ci_sys_id)",
    "CREATE INDEX IF NOT EXISTS ix_ddic_category ON dim_device_incident_cmdb (city_id, mars_device_category, as_of_date)",
]

COLS = ["city_id", "device_id", "device_key", "device_name", "bus_id", "bus_device_flag",
        "serial_number", "device_serial_number", "component_serial_nbr", "component_type",
        "facility_id", "facility_name", "operator_id", "operator_name", "mars_device_category",
        "device_type_name", "transit_mode_name", "incident_number", "incident_sys_id",
        "cmdb_ci_sys_id", "opened_at", "closed_at", "all_incident_numbers",
        "all_incident_sys_ids", "all_cmdb_ci_sys_ids", "incident_count", "as_of_date", "run_id"]


def conn():
    sec = json.loads(_sm.get_secret_value(SecretId=SECRET_ID)["SecretString"])
    # The secret's dbname (appdb) is authoritative -- never an RDS_DATABASE env var.
    dbname = sec.get("dbname") or sec.get("database") or "postgres"
    return pg8000.native.Connection(
        user=sec["username"], password=sec["password"],
        host=RDS_HOST or sec.get("host"), port=RDS_PORT, database=dbname,
        ssl_context=True, timeout=60)


def _newest_manifest(prefix):
    best, tok = None, {}
    while True:
        r = _s3.list_objects_v2(Bucket=GOLD_BUCKET, Prefix=prefix, **tok)
        for o in r.get("Contents", []):
            if o["Key"].endswith("manifest.json") and (best is None or o["LastModified"] > best["LastModified"]):
                best = o
        if not r.get("IsTruncated"):
            break
        tok = {"ContinuationToken": r["NextContinuationToken"]}
    return best["Key"] if best else None


def read_rows(data_prefix):
    import pyarrow.parquet as pq  # lazy: provided by the layer at runtime, absent in CloudShell's import gate
    rows = []
    tok = {}
    while True:
        r = _s3.list_objects_v2(Bucket=GOLD_BUCKET, Prefix=data_prefix, **tok)
        for o in r.get("Contents", []):
            if o["Key"].endswith(".parquet"):
                body = _s3.get_object(Bucket=GOLD_BUCKET, Key=o["Key"])["Body"].read()
                rows += pq.read_table(io.BytesIO(body)).to_pylist()
        if not r.get("IsTruncated"):
            break
        tok = {"ContinuationToken": r["NextContinuationToken"]}
    return rows


def insert(c, table, cols, rows, chunk_size=200):
    total = 0
    for i in range(0, len(rows), chunk_size):
        chunk = rows[i:i + chunk_size]
        params, ph = {}, []
        for j, r in enumerate(chunk):
            names = []
            for k, col in enumerate(cols):
                p = f"p{j}_{k}"
                params[p] = r.get(col)
                names.append(f":{p}")
            ph.append("(" + ", ".join(names) + ")")
        c.run(f"INSERT INTO {table} ({', '.join(cols)}) VALUES " + ", ".join(ph), **params)
        total += len(chunk)
    return total


def lambda_handler(event, context):
    event = event or {}
    dry = bool(event.get("dry_run"))

    if "Records" in event:
        key = event["Records"][0]["s3"]["object"]["key"]
        if not key.endswith("manifest.json"):
            return {"statusCode": 200, "body": json.dumps({"skipped": f"not a manifest: {key}"})}
        man_key = key
    elif event.get("as_of"):
        man_key = _newest_manifest(f"{PREFIX}/as_of_date={event['as_of']}/")
    else:
        man_key = _newest_manifest(PREFIX + "/")
    if not man_key:
        return {"statusCode": 404, "body": json.dumps(
            {"error": f"no manifest.json under s3://{GOLD_BUCKET}/{PREFIX}/"})}

    man = json.loads(_s3.get_object(Bucket=GOLD_BUCKET, Key=man_key)["Body"].read())
    city   = (man.get("city_id") or CITY_ID).upper()
    as_of  = man["as_of_date"]
    run_id = man.get("run_id")
    data_prefix = man_key.rsplit("/", 1)[0] + "/data/"
    out = {"manifest": f"s3://{GOLD_BUCKET}/{man_key}", "as_of": as_of,
           "run_id": run_id, "city": city, "dry_run": dry}

    raw = read_rows(data_prefix)
    out["rows_in_export"] = len(raw)
    if not raw:
        return {"statusCode": 400, "body": json.dumps(
            {"error": "export is empty; refusing to replace a working dimension", **out})}
    if man.get("row_count") and int(man["row_count"]) != len(raw):
        return {"statusCode": 400, "body": json.dumps(
            {"error": f"row_count mismatch: manifest={man['row_count']} read={len(raw)}", **out})}

    rows = []
    for r in raw:
        d = {k: r.get(k) for k in COLS}
        d["city_id"] = city
        d["as_of_date"] = as_of
        d["run_id"] = r.get("run_id") or run_id
        d["incident_count"] = int(r.get("incident_count") or 0)
        rows.append(d)

    if dry:
        out["status"] = "dry_run"
        out["sample_row"] = {k: str(v)[:60] for k, v in rows[0].items()}
        return {"statusCode": 200, "body": json.dumps(out, default=str)}

    c = conn()
    try:
        for stmt in DDL:
            c.run(stmt)
        c.run("BEGIN")
        # replace this as_of's snapshot, then retire old snapshots -- a failure
        # anywhere rolls back and leaves yesterday's dimension in place.
        c.run(f"DELETE FROM {TABLE} WHERE city_id=:c AND as_of_date=:d", c=city, d=as_of)
        n = insert(c, TABLE, COLS, rows)
        keep = [row[0] for row in c.run(
            f"SELECT DISTINCT as_of_date FROM {TABLE} WHERE city_id=:c "
            f"ORDER BY as_of_date DESC LIMIT :k", c=city, k=KEEP)]
        if keep:
            c.run(f"DELETE FROM {TABLE} WHERE city_id=:c AND as_of_date <> ALL(:k)",
                  c=city, k=keep)
        c.run("COMMIT")
        out.update(status="committed", rows_loaded=n,
                   snapshots_retained=[str(d) for d in keep])
    except Exception as e:
        try:
            c.run("ROLLBACK")
        except Exception:
            pass
        out.update(status="rolled_back", error=str(e)[:400],
                   note="One transaction: the previous snapshot is untouched.")
        return {"statusCode": 500, "body": json.dumps(out, default=str)}

    out["rows_after"] = int(c.run(
        f"SELECT COUNT(*) FROM {TABLE} WHERE city_id=:c AND as_of_date=:d",
        c=city, d=as_of)[0][0])
    return {"statusCode": 200, "body": json.dumps(out, default=str)}
