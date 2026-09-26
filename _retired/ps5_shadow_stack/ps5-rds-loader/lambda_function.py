#!/usr/bin/env python3
# =============================================================================
# ps5_rds_loader  --  in-VPC go-live loader for the PS5 reliability feed.
#
# One artifact, three actions (event.action / --action):
#   migrate : apply the PS5 schema in-VPC (05_phase1c + 06_phase1d, idempotent).
#             Runs from inside the VPC because psql from default CloudShell cannot
#             reach the private Aurora writer.
#   load    : read the notebook outputs from S3 (per type:
#             <type>_device_rul_estimates.csv / <type>_serial_reliability.csv /
#             <type>_device_survival_params.json), merge the params governance
#             (cv_cindex / gate_pass / champion / weibull_shape) into each device
#             row, and UPSERT ps5_scoring_runs + ps5_reliability_estimates +
#             ps5_serial_reliability. Idempotent on as_of_date.
#   all     : migrate, then load (default).
#
# DB driver = pg8000 (PURE PYTHON) so the Lambda zip needs no native build.
# DB credentials come ONLY from AWS Secrets Manager (secret id in env/arg) or a
# --db-url; the password is never logged, written, or embedded here.
#
#   Lambda event: {"action":"all","bucket":"<gold-bucket>",
#                  "prefix":"chicago/ps5/notebook_outputs","asof":"2026-04-11",
#                  "city":"CHI"}                         (dry_run:true to preview)
#   CloudShell/SageMaker CLI:
#     python lambda_function.py --action all --bucket <b> \
#        --prefix chicago/ps5/notebook_outputs --asof 2026-04-11 --city CHI \
#        --secret cubic-mars-secret-rds-dev            # or --db-url / --localdir
# =============================================================================
import os, io, csv, json, math, uuid, ssl, datetime, urllib.parse

SUBS = ["tvm", "gates", "validators"]
HERE = os.path.dirname(os.path.abspath(__file__))
MIG_FILES = ["05_phase1c_ps5_reliability_survival.sql",
             "06_phase1d_ps5_oos_set_reliability.sql",
             "07_phase1e_ps5_edv_widen.sql"]

# city_id is the city_code ENUM -> cast the bound text param explicitly so the
# pg8000 server-side prepared statement doesn't trip "column is city_code but
# expression is text". run_date/as_of_date/feature_asof_date are passed as real
# datetime.date objects (pg8000 maps those to DATE natively).
RUN_SQL = """INSERT INTO ps5_scoring_runs
  (run_id, city_id, run_ts, n_devices_scored, n_serials_scored, model_version, scoring_mode, status,
   note, event_definition, event_def_version, event_filter, run_date, scorer)
  VALUES (%s,%s::city_code, now(), %s,%s,%s,'LAMBDA','SUCCESS',%s,'hw_oos_set',%s,'{}'::jsonb,%s,'lambda_s3_loader')"""

DEV_SQL = """INSERT INTO ps5_reliability_estimates
  (city_id, run_id, device_id, mars_device_category, current_healthy_age_days, rul_standard_days,
   predicted_median_survival_days, hazard_score, risk_band, is_overdue, n_prior_failures, concordance_index,
   champion_model, weibull_shape, data_quality_gate_passed, facility_id, as_of_date, days_since_hw_oos,
   roll_fail_30d, event_definition, event_def_version, feature_asof_date, scored_at)
  VALUES (%s::city_code,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s, now())
  ON CONFLICT (city_id, device_id, as_of_date) DO UPDATE SET
   run_id=EXCLUDED.run_id, mars_device_category=EXCLUDED.mars_device_category,
   current_healthy_age_days=EXCLUDED.current_healthy_age_days, rul_standard_days=EXCLUDED.rul_standard_days,
   predicted_median_survival_days=EXCLUDED.predicted_median_survival_days, hazard_score=EXCLUDED.hazard_score,
   risk_band=EXCLUDED.risk_band, is_overdue=EXCLUDED.is_overdue, n_prior_failures=EXCLUDED.n_prior_failures,
   concordance_index=EXCLUDED.concordance_index, champion_model=EXCLUDED.champion_model,
   weibull_shape=EXCLUDED.weibull_shape, data_quality_gate_passed=EXCLUDED.data_quality_gate_passed,
   facility_id=EXCLUDED.facility_id, days_since_hw_oos=EXCLUDED.days_since_hw_oos, roll_fail_30d=EXCLUDED.roll_fail_30d,
   event_definition=EXCLUDED.event_definition, event_def_version=EXCLUDED.event_def_version,
   feature_asof_date=EXCLUDED.feature_asof_date, scored_at=now()"""

SER_SQL = """INSERT INTO ps5_serial_reliability
  (city_id, run_id, device_id, component_serial_nbr, component_type, mars_device_category, component_age_days,
   device_oos_failures_total, risk_score, risk_tier, expected_component_rul_days, predicted_median_survival_days,
   is_overdue, as_of_date, event_definition, event_def_version, feature_asof_date, scored_at)
  VALUES (%s::city_code,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s, now())
  ON CONFLICT (city_id, device_id, component_serial_nbr, as_of_date) DO UPDATE SET
   run_id=EXCLUDED.run_id, component_type=EXCLUDED.component_type, mars_device_category=EXCLUDED.mars_device_category,
   component_age_days=EXCLUDED.component_age_days, device_oos_failures_total=EXCLUDED.device_oos_failures_total,
   risk_score=EXCLUDED.risk_score, risk_tier=EXCLUDED.risk_tier,
   expected_component_rul_days=EXCLUDED.expected_component_rul_days,
   predicted_median_survival_days=EXCLUDED.predicted_median_survival_days, is_overdue=EXCLUDED.is_overdue,
   event_definition=EXCLUDED.event_definition, event_def_version=EXCLUDED.event_def_version,
   feature_asof_date=EXCLUDED.feature_asof_date, scored_at=now()"""


# ---- coercion helpers -------------------------------------------------------
def _f(v):
    try:
        if v is None or (isinstance(v, str) and v.strip() == ""):
            return None
        f = float(v)
        return None if math.isnan(f) else f
    except Exception:
        return None

def _b(v):
    return str(v).strip().lower() in ("true", "1", "t", "y", "yes")

def _i(v, d=0):
    try:
        return int(float(v))
    except Exception:
        return d

def _fac(v):
    v = v.strip() if isinstance(v, str) else v
    return str(v) if v not in (None, "", "nan", "None") else None

def _d(v):
    """coerce -> datetime.date or None (pg8000 binds date objects to DATE)."""
    if v is None:
        return None
    if isinstance(v, datetime.datetime):
        return v.date()
    if isinstance(v, datetime.date):
        return v
    s = str(v).strip()
    if s == "" or s.lower() in ("nan", "none", "nat"):
        return None
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%m/%d/%Y"):
        try:
            return datetime.datetime.strptime(s[:10], fmt).date()
        except Exception:
            pass
    return None


# ---- source: shared governance merge ---------------------------------------
def _merge_gov(devrows, params):
    gov = {"cv_cindex": params.get("cv_cindex"),
           "gate_pass": bool(params.get("gate_pass", False)),
           "champion": params.get("champion") or "CoxPH (params-only serving)",
           "weibull_shape": (params.get("weibull") or {}).get("shape")}
    for r in devrows:
        r.update(gov)
    return devrows


# ---- source: S3 (case-normalised keys) -------------------------------------
def _get_text(s3, bucket, key):
    try:
        return s3.get_object(Bucket=bucket, Key=key)["Body"].read().decode("utf-8")
    except Exception:
        return None

def _s3_csv(s3, bucket, prefix, sub, name):
    for key in (f"{prefix}/{sub}/{sub}_{name}", f"{prefix}/{sub}_{name}"):
        t = _get_text(s3, bucket, key)
        if t is not None:
            return [{(k or "").lower(): v for k, v in row.items()} for row in csv.DictReader(io.StringIO(t))]
    return []

def _s3_json(s3, bucket, prefix, sub, name):
    for key in (f"{prefix}/{sub}/{sub}_{name}", f"{prefix}/{sub}_{name}"):
        t = _get_text(s3, bucket, key)
        if t is not None:
            return json.loads(t)
    return {}

def load_from_s3(bucket, prefix):
    import boto3
    s3 = boto3.client("s3")
    devs, sers = [], []
    for sub in SUBS:
        d = _s3_csv(s3, bucket, prefix, sub, "device_rul_estimates.csv")
        if d:
            devs += _merge_gov(d, _s3_json(s3, bucket, prefix, sub, "device_survival_params.json"))
        sers += _s3_csv(s3, bucket, prefix, sub, "serial_reliability.csv")
    return devs, sers


# ---- source: local dir (offline dry-run / SageMaker terminal) --------------
def _loc_path(root, sub, name):
    for p in (os.path.join(root, sub, f"{sub}_{name}"), os.path.join(root, f"{sub}_{name}")):
        if os.path.exists(p):
            return p
    return None

def _loc_csv(root, sub, name):
    p = _loc_path(root, sub, name)
    if not p:
        return []
    with open(p, newline="", encoding="utf-8") as fh:
        return [{(k or "").lower(): v for k, v in row.items()} for row in csv.DictReader(fh)]

def _loc_json(root, sub, name):
    p = _loc_path(root, sub, name)
    return json.load(open(p)) if p else {}

def load_from_local(root):
    devs, sers = [], []
    for sub in SUBS:
        d = _loc_csv(root, sub, "device_rul_estimates.csv")
        if d:
            devs += _merge_gov(d, _loc_json(root, sub, "device_survival_params.json"))
        sers += _loc_csv(root, sub, "serial_reliability.csv")
    return devs, sers


# ---- db (pg8000, pure python) ----------------------------------------------
def _ssl_ctx():
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE           # encrypt-in-transit == sslmode=require
    return ctx

def _connect(secret, db_url):
    import pg8000.dbapi as pg
    ctx = _ssl_ctx()
    if db_url:
        u = urllib.parse.urlparse(db_url)
        return pg.connect(user=u.username, password=u.password, host=u.hostname,
                          port=u.port or 5432, database=(u.path or "/").lstrip("/") or "appdb",
                          ssl_context=ctx, timeout=10)
    import boto3
    s = json.loads(boto3.client("secretsmanager").get_secret_value(SecretId=secret)["SecretString"])
    return pg.connect(user=s["username"], password=s["password"], host=s["host"],
                      port=int(s.get("port", 5432)), database=s.get("dbname", "appdb"),
                      ssl_context=ctx, timeout=10)


# ---- migrate ----------------------------------------------------------------
def _split_sql(text):
    """Split a .sql file into individual statements (pg8000 = one stmt / execute).
    Respects single-quoted strings and -- line comments; drops BEGIN/COMMIT."""
    out, buf, i, n, insq = [], [], 0, len(text), False
    while i < n:
        c = text[i]
        if insq:
            buf.append(c)
            if c == "'":
                insq = False
            i += 1; continue
        if c == "-" and i + 1 < n and text[i + 1] == "-":
            j = text.find("\n", i)
            i = n if j == -1 else j
            continue
        if c == "'":
            insq = True; buf.append(c); i += 1; continue
        if c == ";":
            s = "".join(buf).strip()
            if s:
                out.append(s)
            buf = []; i += 1; continue
        buf.append(c); i += 1
    tail = "".join(buf).strip()
    if tail:
        out.append(tail)
    return [s for s in out if s.strip().upper().rstrip(";") not in ("BEGIN", "COMMIT")]

PS5_WRITE_TABLES = ["ps5_reliability_estimates", "ps5_serial_reliability", "ps5_scoring_runs"]

def run_inspect(conn):
    """Read-only: existing columns + row counts for the PS5 tables (diagnose drift)."""
    cur = conn.cursor()
    out = {}
    for t in PS5_WRITE_TABLES + ["ps5_reliability_status", "ps5_event_definition"]:
        cur.execute("SELECT column_name, data_type, character_maximum_length "
                    "FROM information_schema.columns WHERE table_schema='public' AND table_name=%s "
                    "ORDER BY ordinal_position", (t,))
        cols = [{"col": r[0], "type": r[1], "len": r[2]} for r in cur.fetchall()]
        n = None
        if cols:
            cur.execute('SELECT count(*) FROM "%s"' % t)
            n = cur.fetchone()[0]
        out[t] = {"exists": bool(cols), "n_rows": n, "n_cols": len(cols),
                  "columns": [c["col"] for c in cols]}
    return out

def run_migrate(conn, dry_run=False, reset=False):
    # base-schema guard: 05/06 reference cities + city_code (from docs/schema.sql)
    applied, stmts_total, dropped = [], 0, []
    cur = conn.cursor()
    cur.execute("SELECT to_regclass('public.cities'), to_regclass('public.schema_migrations')")
    cities, migtbl = cur.fetchone()
    if cities is None:
        raise RuntimeError("base schema missing (public.cities not found) -- apply docs/schema.sql "
                           "+ phase1_ps2_ps5_backfill.sql before the PS5 migrations")
    # reset: drop drifted PS5 write tables (+ their dependent views via CASCADE) so 05/06/07
    # recreate them exactly. Guarded: only when caller passes reset=True AND every such table is
    # empty (never destroys loaded data). ps5_reliability_status is left untouched.
    if reset and not dry_run:
        for t in PS5_WRITE_TABLES:
            cur.execute("SELECT to_regclass(%s)", ("public." + t,))
            if cur.fetchone()[0] is None:
                continue
            cur.execute('SELECT count(*) FROM "%s"' % t)
            rows = cur.fetchone()[0]
            if rows:
                raise RuntimeError(f"reset refused: {t} has {rows} rows (not empty) -- "
                                   "inspect and clear intentionally before reset")
            cur.execute('DROP TABLE IF EXISTS "%s" CASCADE' % t)
            dropped.append(t)
    for fn in MIG_FILES:
        path = os.path.join(HERE, fn)
        stmts = ["CREATE EXTENSION IF NOT EXISTS \"uuid-ossp\""] + _split_sql(open(path).read()) \
            if fn.startswith("05") else _split_sql(open(path).read())
        stmts_total += len(stmts)
        if dry_run:
            applied.append({"file": fn, "statements": len(stmts)})
            continue
        for s in stmts:
            cur.execute(s)
        applied.append({"file": fn, "statements": len(stmts)})
    if not dry_run:
        conn.commit()
    return {"migrated": applied, "statements_total": stmts_total, "dropped": dropped,
            "base_schema": {"cities": bool(cities), "schema_migrations": bool(migtbl)}}


# ---- load -------------------------------------------------------------------
def _dev_tuple(city, run_id, r, asof_d, edv):
    return (city, run_id, r["device_id"], r["mars_device_category"], _f(r.get("current_healthy_age_days")),
            _f(r.get("rul_standard_days")), _f(r.get("predicted_median_survival_days")), _f(r.get("hazard_score")),
            r.get("risk_band"), _b(r.get("is_overdue")), _i(r.get("n_prior_oos", 0)), _f(r.get("cv_cindex")),
            (r.get("champion") or "CoxPH (params-only serving)"), _f(r.get("weibull_shape")),
            _b(r.get("gate_pass")), _fac(r.get("facility_id")), asof_d, _f(r.get("days_since_hw_oos")),
            _i(r.get("roll_fail_30d", 0)), str(r.get("event_definition", "hw_oos_set")),
            str(r.get("event_def_version", edv)), _d(r.get("feature_asof_date")) or asof_d)

def _ser_tuple(city, run_id, r, asof_d, edv):
    return (city, run_id, r["device_id"], r.get("component_serial_nbr"), r.get("component_type_name"),
            r["mars_device_category"], _f(r.get("component_age_days")), _i(r.get("device_oos_failures_total", 0)),
            _f(r.get("risk_score")), r.get("risk_tier"), _f(r.get("expected_component_rul_days")),
            _f(r.get("predicted_median_survival_days")), _b(r.get("is_overdue")), asof_d,
            str(r.get("event_definition", "hw_oos_set")), str(r.get("event_def_version", edv)),
            _d(r.get("feature_asof_date")) or asof_d)

def do_load(conn, devs, sers, asof, city, edv):
    asof_d = _d(asof)
    run_id = str(uuid.uuid4())
    model_version = (edv or "v5.1")[:16]
    cur = conn.cursor()
    cur.execute(RUN_SQL, (run_id, city, len(devs), len(sers), model_version,
                          f"lambda first-live {asof}", edv, asof_d))
    if devs:
        cur.executemany(DEV_SQL, [_dev_tuple(city, run_id, r, asof_d, edv) for r in devs])
    if sers:
        cur.executemany(SER_SQL, [_ser_tuple(city, run_id, r, asof_d, edv) for r in sers])
    conn.commit()
    return run_id


# ---- orchestration ----------------------------------------------------------
def run(action="all", bucket=None, prefix=None, asof=None, city="CHI",
        secret=None, db_url=None, dry_run=False, localdir=None, sample=0, reset=False):
    action = (action or "all").lower()

    if action == "inspect":
        conn = _connect(secret, db_url)
        try:
            return {"ok": True, "action": "inspect", "inspect": run_inspect(conn)}
        finally:
            conn.close()

    need_load = action in ("load", "all")
    devs, sers, edv, by = [], [], None, {}

    if need_load:
        devs, sers = (load_from_local(localdir) if localdir else load_from_s3(bucket, prefix))
        edv = (devs[0].get("event_def_version") if devs else None) or "2026-07-23.v1"
        for r in devs:
            k = r.get("mars_device_category", "?")
            by[k] = by.get(k, 0) + 1
        src = f"local:{localdir}" if localdir else f"s3://{bucket}/{prefix}"
        print(f"[load] source={src} devices={len(devs)} serials={len(sers)} event={edv} per_type={by}")
        if sample and devs:
            asof_d = _d(asof)
            print("[sample] dev[0] =", _dev_tuple(city, "<run_id>", devs[0], asof_d, edv))
            if sers:
                print("[sample] ser[0] =", _ser_tuple(city, "<run_id>", sers[0], asof_d, edv))

    if dry_run:
        out = {"dry_run": True, "action": action, "devices": len(devs), "serials": len(sers),
               "event_def_version": edv, "per_type": by}
        if action in ("migrate", "all"):
            out["migrate_preview"] = {"files": MIG_FILES}
        return out

    if need_load and not devs and not localdir:
        return {"ok": False, "error": f"no device rows under s3://{bucket}/{prefix}"}

    conn = _connect(secret, db_url)
    conn.autocommit = False
    result = {"ok": True, "action": action, "city": city, "asof": asof}
    try:
        if action in ("migrate", "all"):
            result["migrate"] = run_migrate(conn, reset=reset)
            print(f"[migrate] {result['migrate']}")
        if need_load:
            run_id = do_load(conn, devs, sers, asof, city, edv)
            result.update({"run_id": run_id, "devices": len(devs), "serials": len(sers),
                           "event_def_version": edv, "per_type": by})
            print(f"[load] committed run {run_id}: {len(devs)} devices + {len(sers)} serials @ {asof}")
        return result
    finally:
        conn.close()


def lambda_handler(event, context=None):
    event = event or {}
    action = event.get("action") or os.environ.get("PS5_ACTION", "all")
    bucket = event.get("bucket") or os.environ.get("PS5_S3_BUCKET")
    prefix = event.get("prefix") or os.environ.get("PS5_S3_PREFIX", "chicago/ps5/notebook_outputs")
    asof   = event.get("asof")   or os.environ.get("PS5_ASOF")
    city   = event.get("city")   or os.environ.get("PS5_CITY", "CHI")
    dry    = bool(event.get("dry_run", False))
    if action in ("load", "all") and not (bucket and asof) and not dry:
        return {"statusCode": 400, "error": "load needs bucket (event/PS5_S3_BUCKET) and asof (event/PS5_ASOF)"}
    res = run(action, bucket, prefix, asof, city,
              os.environ.get("PS5_RDS_SECRET"), os.environ.get("DATABASE_URL"),
              dry_run=dry, reset=bool(event.get("reset", False)))
    ok = res.get("ok") or res.get("dry_run")
    return {"statusCode": 200 if ok else 500, **res}


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--action", default="all", choices=["migrate", "load", "all", "inspect"])
    ap.add_argument("--reset", action="store_true", help="drop empty drifted PS5 write tables before migrate")
    ap.add_argument("--bucket", default=None)
    ap.add_argument("--prefix", default="chicago/ps5/notebook_outputs")
    ap.add_argument("--asof", default=None)
    ap.add_argument("--city", default="CHI")
    ap.add_argument("--secret", default=None)
    ap.add_argument("--db-url", default=os.environ.get("DATABASE_URL"))
    ap.add_argument("--localdir", default=None, help="read outputs from a local dir instead of S3 (offline)")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--sample", type=int, default=0, help="print N fully-coerced rows (dry-run inspection)")
    a = ap.parse_args()
    print(json.dumps(run(a.action, a.bucket, a.prefix, a.asof, a.city, a.secret,
                         a.db_url, a.dry_run, a.localdir, a.sample, a.reset), indent=2, default=str))
