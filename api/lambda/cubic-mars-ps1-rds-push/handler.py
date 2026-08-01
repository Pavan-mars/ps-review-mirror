"""
cubic-mars-ps1-rds-push
=======================
Loads the PS1 notebooks' cross-wired output into Aurora PostgreSQL.

  s3://<gold>/chicago/gold/device_ps1_cross_wired_daily   (parquet, device x component x day)
      -> ps1_inference_runs        (one row per device category)
      -> ps1_failure_predictions   (device-day grain)
      -> ps1_serial_predictions    (device x serial x day grain)
      -> ml_batch_load_audit       (one row per target table)

Tables come from sql/04 + sql/11 + sql/16 (already applied by the dashboard-api
`action=migrate`). This Lambda never creates or alters them.

TRIGGERS (all three supported, same code path)
  1. EventBridge schedule  -> {} or {"computed_date": "2026-07-26"}
  2. S3 ObjectCreated      -> uses the event's bucket/key
  3. Manual invoke         -> {"bucket": "...", "key": "...", "dry_run": true}

LABEL SEMANTICS
  PS1 is trained on the HARDWARE-OOS label (will_hardware_oos_3d), not the
  chargeable-SLA label (will_fail_3d). Chargeable events are a SUBSET of OOS and are
  determined by contract rules applied AFTER the physical event, so OOS is the
  correct physical target for maintenance. target_col and label_revision are written
  on every row so the provenance travels with the data.

ATTRIBUTION WEIGHT
  ps1_serial_predictions.attribution_weight is an EQUAL SPLIT (1/n components on that
  device-day). The notebooks do not compute a learned attribution. serial_risk_score
  is therefore device risk re-expressed at finer grain -- do not present it as a
  learned per-component risk.

Runtime deps: pg8000 (bundled in the zip) + pyarrow (AWS SDK for pandas managed layer).
Env: GOLD_BUCKET, GOLD_KEY, RDS_HOST, RDS_PORT, RDS_DATABASE, RDS_SECRET_ID, CITY_ID
"""
import datetime, hashlib, io, json, logging, os, statistics

import boto3
import pg8000.native
import pyarrow.parquet as pq

log = logging.getLogger()
log.setLevel(logging.INFO)

REGION        = os.environ.get("AWS_REGION", "us-east-1")
GOLD_BUCKET   = os.environ.get("GOLD_BUCKET", "cubic-mars-pm-s3-datalake-dev-gold-170202974600")
GOLD_KEY      = os.environ.get("GOLD_KEY", "chicago/gold/device_ps1_cross_wired_daily")
RDS_SECRET_ID = os.environ.get("RDS_SECRET_ID", "cubic-mars-secret-rds-dev")
RDS_DATABASE  = os.environ.get("RDS_DATABASE", "postgres")
RDS_HOST      = os.environ.get("RDS_HOST", "")
RDS_PORT      = int(os.environ.get("RDS_PORT", "5432"))
CITY_ID       = os.environ.get("CITY_ID", "CHI")

TARGET_COL     = os.environ.get("PS1_TARGET_COL", "will_hardware_oos_3d")
LABEL_REVISION = os.environ.get("PS1_LABEL_REVISION", "R7-1")

_s3 = boto3.client("s3", region_name=REGION)
_sec = boto3.client("secretsmanager", region_name=REGION)
_conn = None

ALIASES = {
    "device_id":           ["DEVICE_KEY", "DEVICE_ID", "device_id", "device_key"],
    "device_category":     ["device_category", "mars_device_category", "DEVICE_CATEGORY"],
    "failure_probability": ["ps1_fail_prob", "failure_probability", "probability", "score"],
    "predicted_label":     ["ps1_predicted", "predicted_label", "prediction"],
    "decision_threshold":  ["threshold_used", "decision_threshold", "active_threshold"],
    "prediction_date":     ["transit_day", "event_date", "prediction_date", "scoring_date"],
    "facility_id":         ["FACILITY_ID", "facility_id", "FACID"],
    "risk_band":           ["ps1_risk_tier", "risk_band", "risk_tier"],
    "matched_serial_nbr":  ["COMPONENT_SERIAL_NBR", "matched_serial_nbr", "component_serial_nbr"],
    "component_type":      ["COMPONENT_TYPE", "component_type"],
    "component_age_days":  ["component_age_days", "COMPONENT_AGE_DAYS"],
}
RISK_CUTS = [(0.70, "CRITICAL"), (0.50, "HIGH"), (0.30, "MEDIUM")]


def _band(p):
    if p is None:
        return "UNKNOWN"
    for cut, name in RISK_CUTS:
        if p >= cut:
            return name
    return "LOW"


def _conn_get():
    global _conn
    if _conn is None:
        s = json.loads(_sec.get_secret_value(SecretId=RDS_SECRET_ID)["SecretString"])
        _conn = pg8000.native.Connection(
            user=s.get("username") or s.get("user") or "postgres",
            password=s.get("password"),
            host=s.get("host") or RDS_HOST,
            port=int(s.get("port") or RDS_PORT),
            database=s.get("dbname") or s.get("database") or RDS_DATABASE,
            ssl_context=True, timeout=30)
    return _conn


def _resolve(names, canonical):
    for c in ALIASES[canonical]:
        if c in names:
            return c
    return None


def _f(v):
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if f != f else f          # NaN guard


def _date(v, default):
    if v is None:
        return default
    if isinstance(v, (datetime.date, datetime.datetime)):
        return v.date() if isinstance(v, datetime.datetime) else v
    s = str(v)[:10]
    try:
        return datetime.date.fromisoformat(s)
    except ValueError:
        return default


def build_rows(table, city, run_id, computed_date, gold_s3):
    """table: pyarrow.Table from the cross-wired parquet. Returns (rows_by_target, warnings)."""
    names = set(table.schema.names)
    col = {k: _resolve(names, k) for k in ALIASES}
    warn = [
        f"LABEL SEMANTICS: target={TARGET_COL} (hardware OOS, not chargeable), "
        f"label_revision={LABEL_REVISION}. Chargeable events are a SUBSET of OOS and are "
        f"assigned by contract rules after the physical event. Label the dashboard "
        f"'hardware OOS risk', not 'failure' or 'SLA'."
    ]
    missing = [k for k in ("device_id", "failure_probability") if not col[k]]
    if missing:
        raise ValueError(f"parquet missing required field(s) {missing}; has {sorted(names)[:40]}")

    g = lambda k: (table.column(col[k]).to_pylist() if col[k] else [None] * table.num_rows)
    dev  = [str(x) for x in g("device_id")]
    prob = [_f(x) for x in g("failure_probability")]
    cat  = g("device_category"); thr = [_f(x) for x in g("decision_threshold")]
    pl   = g("predicted_label"); fac = g("facility_id"); rb = g("risk_band")
    ser  = g("matched_serial_nbr"); ctype = g("component_type")
    cage = [_f(x) for x in g("component_age_days")]
    day  = [_date(x, computed_date) for x in g("prediction_date")]

    if not col["device_category"]: warn.append("no device_category column - loaded as NULL")
    if not col["facility_id"]:     warn.append("no facility column - facility_id NULL (/ps1/station-summary stays empty)")
    if not col["risk_band"]:       warn.append("risk_band derived from probability (0.70/0.50/0.30)")
    if not col["matched_serial_nbr"]: warn.append("no component serial column - ps1_serial_predictions EMPTY")

    def label(i):
        if pl[i] is not None:
            try: return int(pl[i])
            except (TypeError, ValueError): pass
        if prob[i] is not None and thr[i] is not None:
            return int(prob[i] >= thr[i])
        return None

    def bandof(i):
        return str(rb[i]) if col["risk_band"] and rb[i] is not None else _band(prob[i])

    # ---- device grain
    seen, dev_rows = set(), []
    for i in range(table.num_rows):
        k = (dev[i], day[i])
        if k in seen:
            continue
        seen.add(k)
        dev_rows.append(dict(
            city_id=city,
            prediction_id=hashlib.sha1(f"{run_id}|{dev[i]}|{day[i]}".encode()).hexdigest()[:32],
            device_category=(str(cat[i]) if cat[i] is not None else None),
            device_id=dev[i][:20],
            facility_id=(str(fac[i])[:20] if fac[i] is not None else None),
            failure_probability=(round(prob[i], 5) if prob[i] is not None else None),
            predicted_label=label(i),
            decision_threshold=(round(thr[i], 5) if thr[i] is not None else None),
            prediction_date=day[i], inference_ts=None, computed_date=computed_date,
            run_id=run_id, model_version=None, target_col=TARGET_COL,
            risk_band=bandof(i), matched_serial_nbr=None))
    warn.append(f"device grain: {table.num_rows:,} source rows -> {len(dev_rows):,} device-day rows")

    # ---- serial grain (equal-split attribution)
    ser_rows = []
    if col["matched_serial_nbr"]:
        keep = {}
        for i in range(table.num_rows):
            if ser[i] is None:
                continue
            keep.setdefault((dev[i], str(ser[i]), day[i]), i)
        # count DISTINCT serials per device-day so the weights sum to exactly 1.0
        counts = {}
        for (d, s, dy) in keep:
            counts[(d, dy)] = counts.get((d, dy), 0) + 1
        for (d, s, dy), i in keep.items():
            w = round(1.0 / counts[(d, dy)], 5)
            ser_rows.append(dict(
                city_id=city, run_id=run_id, device_id=d[:40], matched_serial_nbr=s[:64],
                device_category=(str(cat[i]) if cat[i] is not None else None),
                component_type=(str(ctype[i])[:48] if ctype[i] is not None else None),
                component_age_days=(round(cage[i], 2) if cage[i] is not None else None),
                device_failure_probability=(round(prob[i], 5) if prob[i] is not None else None),
                attribution_weight=w,
                serial_risk_score=(round(prob[i] * w, 5) if prob[i] is not None else None),
                risk_band=bandof(i), prediction_date=dy, computed_date=computed_date))
        warn.append("attribution_weight = EQUAL SPLIT - a placeholder, not a learned "
                    "per-component risk; do not present serial_risk_score as one")

    # ---- run registry
    cats = sorted({str(c) for c in cat if c is not None}) or ["UNKNOWN"]
    thr_med = statistics.median([t for t in thr if t is not None]) if any(t is not None for t in thr) else None
    run_rows = []
    for c in cats:
        idx = [i for i in range(table.num_rows) if str(cat[i]) == c] if cats != ["UNKNOWN"] else range(table.num_rows)
        run_rows.append(dict(
            city_id=city, run_id=run_id,
            run_ts=datetime.datetime.combine(computed_date, datetime.time()),
            run_kind="batch_score", device_category=c, scoring_date=computed_date,
            endpoint_name=os.environ.get("PS1_ENDPOINT_PREFIX", "chicago-ps1-3d") + f"-{c.lower()}-failure-v1",
            serving_image=None, model_version=None, mlflow_version=None,
            target_col=TARGET_COL,
            decision_threshold=(round(thr_med, 5) if thr_med is not None else None),
            n_devices_scored=len({dev[i] for i in idx}),
            n_flagged=sum(1 for i in idx if label(i) == 1),
            gold_snapshot_s3=gold_s3, status="success", error_text=None, duration_s=None,
            computed_date=computed_date))

    if len(cats) == 1 and cats != ["UNKNOWN"]:
        warn.append(f"ONLY ONE device_category present ({cats[0]}) - the notebook's CELL 24 "
                    f"writes without partition_cols, so each run overwrites the previous "
                    f"category. Fix with partition_cols=['device_category'] or one key per category.")

    return {"ps1_inference_runs": run_rows,
            "ps1_failure_predictions": dev_rows,
            "ps1_serial_predictions": ser_rows}, warn


ORDER = ["ps1_inference_runs", "ps1_failure_predictions", "ps1_serial_predictions"]
DELETE = {
    "ps1_inference_runs":      "city_id = :c AND run_id = :r",
    "ps1_failure_predictions": "city_id = :c AND computed_date = :d",
    "ps1_serial_predictions":  "city_id = :c AND run_id = :r",
}


def write_rows(conn, rows_by_target, city, run_id, computed_date, source_rows, gold_s3, warn, batch=500):
    loaded = {}
    for t in ORDER:
        rows = rows_by_target.get(t, [])
        conn.run(f"DELETE FROM {t} WHERE {DELETE[t]}", c=city, r=run_id, d=computed_date)
        if not rows:
            loaded[t] = 0
            continue
        cols = list(rows[0].keys()); collist = ", ".join(cols)
        for i in range(0, len(rows), batch):
            chunk = rows[i:i + batch]
            tup, params = [], {}
            for n, r in enumerate(chunk):
                tup.append("(" + ", ".join(f":p{n}_{c}" for c in cols) + ")")
                for c in cols:
                    params[f"p{n}_{c}"] = r[c]
            conn.run(f"INSERT INTO {t} ({collist}) VALUES " + ", ".join(tup), **params)
        loaded[t] = len(rows)
        log.info("[load] %s <- %d rows", t, len(rows))
    note = "; ".join(warn)[:2000]
    for t in ORDER:
        try:
            conn.run(
                "INSERT INTO ml_batch_load_audit (city_id, ps_id, run_id, target_table, s3_source,"
                " rows_read, rows_loaded, columns_added, columns_skipped, status, error_text)"
                " VALUES (:c,'PS1',:r,:t,:src,:rr,:rl,NULL,NULL,:st,:note)",
                c=city, r=run_id, t=t, src=gold_s3, rr=int(source_rows), rl=int(loaded.get(t, 0)),
                st="ok" if loaded.get(t, 0) else "ok_empty", note=note)
        except Exception as e:
            log.warning("[audit] skipped for %s: %s", t, str(e)[:160])
            break
    return loaded


def process(bucket, key, computed_date=None, run_id=None, dry_run=False):
    cd = _date(computed_date, datetime.date.today())
    rid = run_id or f"ps1_{cd.isoformat().replace('-', '')}"
    gold_s3 = f"s3://{bucket}/{key}"
    log.info("[read] %s", gold_s3)
    body = _s3.get_object(Bucket=bucket, Key=key)["Body"].read()
    tbl = pq.read_table(io.BytesIO(body))
    log.info("[read] %d rows, %d columns", tbl.num_rows, tbl.num_columns)

    rows_by_target, warn = build_rows(tbl, CITY_ID, rid, cd, gold_s3)
    counts = {k: len(v) for k, v in rows_by_target.items()}
    for w in warn:
        log.info("[note] %s", w)
    if dry_run:
        return {"dry_run": True, "run_id": rid, "computed_date": cd.isoformat(),
                "source_rows": tbl.num_rows, "would_load": counts, "notes": warn}
    loaded = write_rows(_conn_get(), rows_by_target, CITY_ID, rid, cd,
                        tbl.num_rows, gold_s3, warn)
    return {"run_id": rid, "computed_date": cd.isoformat(), "source_rows": tbl.num_rows,
            "loaded": loaded, "notes": warn}


def lambda_handler(event, context):
    event = event or {}
    results = []
    if "Records" in event:                                  # S3 ObjectCreated
        for rec in event["Records"]:
            b = rec["s3"]["bucket"]["name"]
            k = rec["s3"]["object"]["key"]
            if "device_ps1_cross_wired" not in k:
                log.info("[skip] %s", k); continue
            results.append(process(b, k, event.get("computed_date")))
    else:                                                   # EventBridge / manual
        results.append(process(event.get("bucket", GOLD_BUCKET),
                               event.get("key", GOLD_KEY),
                               event.get("computed_date"),
                               event.get("run_id"),
                               bool(event.get("dry_run"))))
    return {"statusCode": 200, "body": json.dumps({"ps1_rds_push": results}, default=str)}
