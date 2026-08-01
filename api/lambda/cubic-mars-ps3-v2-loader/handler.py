# =====================================================================
# cubic-mars-ps3-v2-loader                                   29-Jul-2026
#
# S3 (gold/chicago/ps3_hardened_remediation/runs/<run_id>/tables/) -> Aurora
# ps3_v2_* -> dashboard-api
#
# A SEPARATE Lambda. It shares no table, prefix or deployment with anything
# that fills the existing PS3 tables, so those remain the Plan B feed and
# "roll back" is "stop invoking this".
#
# Reads FLAT parquet files -- one file per table, no partitions. duckdb is used
# for the read because the managed pyarrow layer ships without the zstd codec
# (measured on the PS4 v3 run); pyarrow stays as the fallback.
#
# ACTIONS  {"action":"dry_run"} | {"action":"load"} | {"action":"verify"}
# DELETE SCOPE  WHERE city_id=:c AND pipeline_version=:p  -- never a TRUNCATE,
# so two runs can sit side by side for comparison.
# =====================================================================
import io, json, os, re
import boto3, pg8000.native

REGION      = os.environ.get("AWS_REGION", "us-east-1")
BUCKET      = os.environ.get("PS3_BUCKET", "cubic-mars-pm-s3-datalake-dev-gold-170202974600")
PS3_ROOT    = os.environ.get("PS3_ROOT", "chicago/ps3_hardened_remediation/runs")
RDS_SECRET  = os.environ.get("RDS_SECRET_ID", "cubic-mars-secret-rds-dev")
RDS_HOST    = os.environ.get("RDS_HOST", "")
CITY_ID     = os.environ.get("CITY_ID", "CHI")

_s3  = boto3.client("s3", region_name=REGION)
_sec = boto3.client("secretsmanager", region_name=REGION)
_conn = None
CONN_INFO = {}
SPEC = json.load(open(os.path.join(os.path.dirname(__file__), "spec.json")))


def connect():
    """The SECRET decides the database, not the env var -- matching the
    dashboard-api exactly. That mismatch (appdb vs postgres) once produced
    'relation does not exist' for tables that plainly existed."""
    global _conn
    if _conn is not None:
        try:
            _conn.run("SELECT 1"); return _conn
        except Exception:
            try: _conn.close()
            except Exception: pass
            _conn = None
    s = json.loads(_sec.get_secret_value(SecretId=RDS_SECRET)["SecretString"])
    host = RDS_HOST or s.get("host")
    db   = s.get("dbname") or s.get("database") or os.environ.get("RDS_DATABASE") or "postgres"
    port = int(s.get("port") or 5432)
    CONN_INFO.update({"host": host, "database": db, "port": port})
    _conn = pg8000.native.Connection(user=s.get("username", "postgres"), password=s["password"],
                                     host=host, port=port, database=db, timeout=60)
    return _conn


def latest_run():
    """Newest runs/<run_id>/ prefix. The run id is timestamp-ordered
    (ps3_20260729T074311Z), so lexical max is chronological max."""
    r = _s3.list_objects_v2(Bucket=BUCKET, Prefix=PS3_ROOT.rstrip("/") + "/", Delimiter="/")
    ids = [p["Prefix"].rstrip("/").rsplit("/", 1)[-1] for p in r.get("CommonPrefixes", [])]
    return max(ids) if ids else None


def read_parquet(key):
    body = _s3.get_object(Bucket=BUCKET, Key=key)["Body"].read()
    try:
        import duckdb
        tmp = "/tmp/_ps3.parquet"
        open(tmp, "wb").write(body)
        con = duckdb.connect()
        try:
            res = con.execute("SELECT * FROM read_parquet(?)", [tmp])
            names = [d[0] for d in res.description]
            return [dict(zip(names, r)) for r in res.fetchall()]
        finally:
            con.close()
            try: os.remove(tmp)
            except Exception: pass
    except Exception as e_duck:
        try:
            import pyarrow.parquet as pq
            t = pq.read_table(io.BytesIO(body))
            n = t.column_names
            cells = {c: t.column(c).to_pylist() for c in n}
            return [{c: cells[c][i] for c in n} for i in range(t.num_rows)]
        except Exception as e_arrow:
            raise RuntimeError("read failed %s -- duckdb: %s | pyarrow: %s"
                               % (key, str(e_duck)[:140], str(e_arrow)[:140]))


def cell(v):
    """Coerce one parquet cell for binding.

    29-Jul-2026. WHOLE-NUMBER FLOATS MUST BECOME INTEGERS.
    pandas cannot hold NULLs in an int64 column, so any count column that has a
    single missing value comes back as float64 and every value in it arrives as
    3.0 rather than 3. Postgres refuses that outright:
        invalid input syntax for type bigint: "3.0"
    and the whole load rolls back on the second dataset. Converting integral
    floats to int here fixes every BIGINT column at once, and costs nothing on
    DOUBLE columns -- Postgres widens an int to double silently, and a genuine
    measurement of exactly 3.0 is still 3.0 once stored.

    Deliberately NOT done by declaring the types per column in spec.json: that
    would be one more place for the schema to be described, and therefore one
    more place for it to be described wrongly."""
    if v is None: return None
    if isinstance(v, float) and v != v: return None
    if isinstance(v, float) and v.is_integer(): return int(v)
    if isinstance(v, bool): return "true" if v else "false"
    if hasattr(v, "isoformat"): return v.isoformat()
    if isinstance(v, str):
        s = v.strip()
        return None if s == "" or s.lower() in ("nan", "none", "null", "<na>") else s
    return v


def insert(c, table, cols, rows, dry):
    if dry or not rows: return len(rows)
    n = 0
    cl = ", ".join(cols)
    for i in range(0, len(rows), 300):
        ch = rows[i:i+300]; ph = []; args = {}
        for ri, r in enumerate(ch):
            nm = ["p%d_%d" % (ri, ci) for ci in range(len(cols))]
            ph.append("(" + ", ".join(":" + x for x in nm) + ")")
            for a, col in zip(nm, cols): args[a] = r.get(col)
        c.run("INSERT INTO %s (%s) VALUES %s" % (table, cl, ", ".join(ph)), **args)
        n += len(ch)
    return n


def lambda_handler(event, context):
    event = event or {}
    action = (event.get("action") or "load").lower()
    city   = (event.get("city") or CITY_ID).upper()
    pv     = event.get("pipeline_version") or "hardened_v2"

    if action == "verify":
        c = connect()
        def q(sql, **kw):
            try:
                r = c.run(sql, **kw); cols = [d["name"] for d in c.columns]
                return [dict(zip(cols, row)) for row in r]
            except Exception as e:
                return {"error": "%s: %s" % (type(e).__name__, str(e)[:180])}
        return {"ok": True, "action": "verify", "conn": dict(CONN_INFO),
                "tables": q("SELECT * FROM v_ps3_v2_table_status"),
                "run": q("SELECT * FROM v_ps3_v2_current WHERE city_id=:c", c=city),
                "scorecard": q("SELECT device_category, head, champion, validation_f1_macro,"
                               " test_f1_macro, gate_note FROM v_ps3_v2_scorecard"
                               " WHERE city_id=:c ORDER BY device_category, head", c=city),
                "policy": q("SELECT dashboard_element, display_allowed, row_count,"
                            " required_disclaimer FROM v_ps3_v2_policy WHERE city_id=:c", c=city)}

    dry = (action == "dry_run")
    run_id = event.get("run_id") or latest_run()
    if not run_id:
        return {"ok": False, "error": "no run_id prefix under s3://%s/%s/" % (BUCKET, PS3_ROOT)}
    prefix = "%s/%s/tables/" % (PS3_ROOT.rstrip("/"), run_id)

    out = {"ok": True, "action": action, "city": city, "run_id": run_id,
           "pipeline_version": pv, "s3_prefix": "s3://%s/%s" % (BUCKET, prefix),
           "datasets": {}, "unmapped": {}, "missing": []}

    c = connect()
    out["conn"] = dict(CONN_INFO)
    if not dry: c.run("BEGIN")
    try:
        total = 0; nt = 0
        for key, spec in SPEC.items():
            k = prefix + spec["file"] + ".parquet"
            try:
                raw = read_parquet(k)
            except Exception as e:
                out["missing"].append({"dataset": key, "key": k, "why": str(e)[:180]})
                continue
            present = set()
            for r in raw[:50]: present |= set(str(x) for x in r)
            extra = sorted(present - set(spec["cols"]))
            if extra: out["unmapped"][key] = extra
            shaped = []
            for r in raw:
                d = {col: cell(r.get(col)) for col in spec["cols"]}
                d["city_id"] = city; d["pipeline_version"] = pv; d["run_id"] = run_id
                shaped.append(d)
            cols = ["city_id", "pipeline_version", "run_id"] + spec["cols"]
            if not dry:
                c.run("DELETE FROM %s WHERE city_id=:c AND pipeline_version=:p" % spec["table"],
                      c=city, p=pv)
            n = insert(c, spec["table"], cols, shaped, dry)
            out["datasets"][key] = {"table": spec["table"], "rows_read": len(raw), "rows_loaded": n}
            total += n; nt += 1
        if not dry:
            c.run("DELETE FROM ps3_v2_runs WHERE city_id=:c AND pipeline_version=:p AND run_id=:r",
                  c=city, p=pv, r=run_id)
            insert(c, "ps3_v2_runs",
                   ["city_id", "pipeline_version", "run_id", "s3_prefix", "tables_loaded", "rows_loaded"],
                   [{"city_id": city, "pipeline_version": pv, "run_id": run_id,
                     "s3_prefix": out["s3_prefix"], "tables_loaded": nt, "rows_loaded": total}], dry)
            c.run("COMMIT")
        out["tables_loaded"] = nt; out["rows_total"] = total
    except Exception as e:
        if not dry:
            try: c.run("ROLLBACK")
            except Exception: pass
        out["ok"] = False; out["error"] = "%s: %s" % (type(e).__name__, str(e)[:400])
        return out
    if dry: out["note"] = "DRY RUN -- nothing written. Check rows_read, missing and unmapped."
    return out
