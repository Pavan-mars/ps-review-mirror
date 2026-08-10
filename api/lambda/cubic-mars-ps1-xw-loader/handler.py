# =====================================================================
# cubic-mars-ps1-xw-loader   28-Jul-2026
#
# s3://<artifacts>/chicago/device_ps1_cross_wired_daily/{gate,tvm,validator}
#     -> Aurora ps1_cross_wired_daily
#
# Three parquet objects, 786,525 rows total, 34 columns each -- MEASURED, not
# taken from the console log, which says 30 columns and an older TVM count.
# Written by the three PS1 SageMaker notebooks. Table: sql/34_ps1_cross_wired.sql.
#
# ACTIONS
#   {"action":"dry_run"}   read S3, report rows/columns/dtypes, WRITE NOTHING.
#                          Also reports what the loader would map vs. push to
#                          `extra`. Run this FIRST on any new export. It is how
#                          the twelve columns the console log never named were
#                          found -- including DEVICE_ID and the label
#                          will_hardware_oos_3d, both of which this loader was
#                          originally written without.
#   {"action":"load"}      replace-then-insert, per device type, in a savepoint.
#   {}                     same as load.
#
# Optional payload keys:
#   "types": ["gate"]      restrict to a subset
#   "limit": 5000          load only the first N rows per type (smoke test)
#
# WHY REPLACE AND NOT UPSERT
# --------------------------
# The table has a surrogate PK and no natural unique key (see sql/34's header --
# COMPONENT_SERIAL_NBR is NULL for validators, which is what killed the
# equivalent index in PS5). With no unique key there is nothing for ON CONFLICT
# to match, so an upsert is not available. DELETE by (city, device_type) then
# INSERT is idempotent and cannot double-count on a re-run, which is the
# property that actually matters here.
#
# The DELETE is scoped to the device type being loaded, so a failure on
# validators cannot take gate's rows with it.
# =====================================================================
import os, json, io, decimal, datetime as dt

import boto3
import pg8000.native

REGION      = os.environ.get("AWS_REGION", "us-east-1")
BUCKET      = os.environ.get("ARTIFACT_BUCKET",
                             "cubic-mars-pm-s3-datalake-dev-artifacts-170202974600")
PREFIX      = os.environ.get("PS1_XW_PREFIX", "chicago/device_ps1_cross_wired_daily")
CITY_ID     = os.environ.get("CITY_ID", "CHI")
RDS_HOST    = os.environ["RDS_HOST"]
RDS_DB      = os.environ.get("RDS_DATABASE", "postgres")
SECRET_ID   = os.environ["RDS_SECRET_ID"]
BATCH       = int(os.environ.get("PS1_XW_BATCH", "500"))

# slug in S3  ->  device_type stored in the table
TYPES = {"gate": "GATE", "tvm": "TVM", "validator": "VALIDATOR"}

# MEASURED from S3 by the 28-Jul dry_run -- not copied from the console logs.
# The logs say TVM 184,386; that was the 26-Jul run. TVM was re-run at 08:19 on
# 28-Jul and the file now holds 184,483. S3 is the truth.
EXPECTED = {"gate": 107110, "tvm": 184483, "validator": 494932, "total": 786525}

s3 = boto3.client("s3", region_name=REGION)

# ---------------------------------------------------------------------
# Column map. Keys are LOWERCASED source names -- the export mixes cases
# (DEVICE_KEY and COMPONENT_SERIAL_NBR are upper, everything else lower), and
# case-folding at the boundary is cheaper than trusting either convention.
# ---------------------------------------------------------------------
COLMAP = {
    "device_key":                     "device_key",
    "component_serial_nbr":           "component_serial_nbr",
    "device_category":                "device_category",
    "transit_day":                    "transit_day",
    "ps1_fail_prob":                  "ps1_fail_prob",
    "ps1_predicted":                  "ps1_predicted",
    "threshold_used":                 "threshold_used",
    "ps1_risk_tier":                  "ps1_risk_tier",
    "is_prob_anomaly":                "is_prob_anomaly",
    "is_coordinated_station_failure": "is_coordinated_station_failure",
    "is_major_station_event":         "is_major_station_event",
    "station_devices_failed":         "station_devices_failed",
    "days_healthy_before_chain":      "days_healthy_before_chain",
    "avg_rolling_mttr_30d_min":       "avg_rolling_mttr_30d_min",
    "total_failure_days_s28":         "total_failure_days_s28",
    "last_failure_date_s28":          "last_failure_date_s28",
    "shap_feat1": "shap_feat1", "shap_val1": "shap_val1",
    "shap_feat2": "shap_feat2", "shap_val2": "shap_val2",
    "shap_feat3": "shap_feat3", "shap_val3": "shap_val3",
    # 28-Jul-2026. The dry_run measured 34 columns, not the 30 the console log
    # reports. These twelve were landing in `extra`; four of them are
    # load-bearing and all twelve are cheaper to query as real columns:
    #   DEVICE_ID              the stable id every other tab joins on. An earlier
    #                          version of this loader left device_id NULL on the
    #                          theory that it needed an SCD2 dim lookup. It does
    #                          not -- it ships in the file.
    #   will_hardware_oos_3d   THE LABEL. Makes precision/recall measurable on
    #                          the scored population (v_ps1_xw_performance).
    #   FACILITY_ID            station rollups with no join.
    #   COMPONENT_TYPE         the component name the PS3 pareto renders as None.
    "device_id":                  "device_id",
    "component_type":             "component_type",
    "facility_id":                "facility_id",
    "operator_id":                "operator_id",
    "event_date":                 "event_date",
    "score":                      "score",
    "ps1_p95_hist":               "ps1_p95_hist",
    "will_hardware_oos_3d":       "will_hardware_oos_3d",
    "avg_rolling_mttr_90d_min":   "avg_rolling_mttr_90d_min",
    "max_downtime_ever_min":      "max_downtime_ever_min",
    "component_age_days":         "component_age_days",
    "no_prior_failure_in_window": "no_prior_failure_in_window",
    # the notebook's own stamps, if the newer export cell was used
    "ps1_device_type":                None,   # redundant with device_type
    "ps1_export_ts":                  None,
}

# Order must match sql/34's column list only in the sense that both names exist;
# the INSERT names its columns explicitly, so position here is free.
TARGET_COLS = [
    "city_id", "device_type", "device_key", "device_id", "component_serial_nbr",
    "component_type", "device_category", "facility_id", "operator_id",
    "transit_day", "event_date",
    "ps1_fail_prob", "ps1_predicted", "threshold_used", "ps1_risk_tier",
    "score", "ps1_p95_hist", "is_prob_anomaly", "will_hardware_oos_3d",
    "is_coordinated_station_failure", "is_major_station_event",
    "station_devices_failed", "days_healthy_before_chain",
    "avg_rolling_mttr_30d_min", "avg_rolling_mttr_90d_min",
    "max_downtime_ever_min", "total_failure_days_s28", "last_failure_date_s28",
    "component_age_days", "no_prior_failure_in_window",
    "shap_feat1", "shap_val1", "shap_feat2", "shap_val2", "shap_feat3", "shap_val3",
    "extra", "asof_date", "run_id",
]

# Columns whose bind parameter needs an explicit cast. See the INSERT builder.
CAST_SUFFIX = {"extra": "::jsonb"}

BOOL_COLS = {"is_prob_anomaly", "is_coordinated_station_failure",
             "is_major_station_event", "no_prior_failure_in_window"}
# will_hardware_oos_3d stays an INT, not a BOOL. It is the label, and it is
# averaged directly to get a base rate -- AVG over boolean is not valid in
# Postgres without a cast, and forcing the cast at every call site is how a
# base rate quietly becomes a count.
INT_COLS  = {"ps1_predicted", "station_devices_failed", "total_failure_days_s28",
             "will_hardware_oos_3d"}
DATE_COLS = {"transit_day", "last_failure_date_s28", "asof_date", "event_date"}


def secret():
    sm = boto3.client("secretsmanager", region_name=REGION)
    return json.loads(sm.get_secret_value(SecretId=SECRET_ID)["SecretString"])


CONN_INFO = {}


def connect():
    # 28-Jul-2026. THE SECRET IS AUTHORITATIVE for host/port/database -- exactly
    # how cubic-mars-dashboard-api resolves them:
    #     database = s["dbname"] or s["database"] or env DB_NAME
    # This loader read the RDS_DATABASE env var alone and defaulted to "postgres",
    # so migrate() reported sql/34 applied 16 / failed 0 while the load died on
    # relation "ps1_cross_wired_daily" does not exist. Both were true: the table
    # was created in the secret's database and looked for in "postgres".
    c = secret()
    host = c.get("host") or RDS_HOST
    db   = c.get("dbname") or c.get("database") or RDS_DB
    port = int(c.get("port") or 5432)
    CONN_INFO.update({"host": host, "database": db, "port": port})
    return pg8000.native.Connection(
        user=c["username"], password=c["password"],
        host=host, database=db, port=port,
        ssl_context=True, timeout=60,
    )


def clean(v, col):
    """numpy/pandas scalar -> something pg8000 will bind.

    pandas hands back numpy scalars, NaT and NaN. NaN is a float, so it binds
    silently into a NUMERIC column as the literal 'NaN' -- which Postgres
    accepts and which then poisons every AVG built on the column. Every null
    flavour is normalised to None here, at the one place it can be.
    """
    if v is None:
        return None
    # pandas NaT / NaN. `v != v` is the NaN test that does not need numpy.
    try:
        if v != v:
            return None
    except Exception:
        pass
    if hasattr(v, "item"):          # numpy scalar
        try:
            v = v.item()
        except Exception:
            v = str(v)
    if col in DATE_COLS:
        if isinstance(v, dt.datetime):
            return v.date()
        if isinstance(v, dt.date):
            return v
        s = str(v)[:10]
        try:
            return dt.date.fromisoformat(s)
        except ValueError:
            return None
    if col in BOOL_COLS:
        if isinstance(v, bool):
            return v
        if isinstance(v, (int, float)):
            return bool(v)
        return str(v).strip().lower() in ("true", "t", "1", "yes", "y")
    if col in INT_COLS:
        try:
            return int(round(float(v)))
        except (TypeError, ValueError):
            return None
    if isinstance(v, (dt.datetime, dt.date, decimal.Decimal, bool, int, float)):
        return v
    return str(v)


def read_parquet(slug):
    """Read one object. pandas comes from the AWSSDKPandas layer."""
    import pandas as pd
    key = f"{PREFIX}/{slug}"
    obj = s3.get_object(Bucket=BUCKET, Key=key)
    body = obj["Body"].read()
    df = pd.read_parquet(io.BytesIO(body))
    return df, key, obj["ContentLength"], obj["LastModified"]


def describe(df):
    return {c: str(df[c].dtype) for c in df.columns}


def shape_rows(df, device_type, asof, run_id, limit=None):
    """DataFrame -> list of tuples in TARGET_COLS order."""
    lower = {c.lower(): c for c in df.columns}
    mapped, extras = {}, []
    for lc, src in lower.items():
        tgt = COLMAP.get(lc, "__unknown__")
        if tgt is None:
            continue                       # deliberately ignored
        if tgt == "__unknown__":
            extras.append(src)
        else:
            mapped[tgt] = src

    if limit:
        df = df.head(limit)

    recs = df.to_dict("records")
    out = []
    for r in recs:
        row = []
        for col in TARGET_COLS:
            if col == "city_id":
                row.append(CITY_ID)
            elif col == "device_type":
                row.append(device_type)
            elif col == "asof_date":
                row.append(asof)
            elif col == "run_id":
                row.append(run_id)
            elif col == "extra":
                if extras:
                    d = {}
                    for e in extras:
                        v = clean(r.get(e), e)
                        if isinstance(v, (dt.date, dt.datetime)):
                            v = v.isoformat()
                        elif isinstance(v, decimal.Decimal):
                            v = float(v)
                        d[e] = v
                    row.append(json.dumps(d))
                else:
                    row.append(None)
            else:
                src = mapped.get(col)
                row.append(clean(r.get(src), col) if src else None)
        out.append(tuple(row))
    return out, sorted(extras), sorted(mapped)


def csv_cell(v):
    """One value -> a CSV field for COPY ... WITH (FORMAT csv, NULL '').

    None becomes an EMPTY, UNQUOTED field, which NULL '' reads as SQL NULL. A
    QUOTED empty string is NOT null to Postgres, so quoting must be skipped for
    None specifically -- getting that backwards turns every null numeric into a
    type error at COPY time.
    """
    if v is None:
        return ""
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (dt.date, dt.datetime)):
        return v.isoformat()
    return str(v)


def copy_rows(conn, rows, chunk=100_000):
    """Bulk-insert via COPY FROM STDIN instead of batched INSERT.

    WHY. The batched-INSERT version moved ~15,000 rows a minute, so 786,525 rows
    projected to 30-50 minutes against a 900-second Lambda ceiling. And COMMIT
    only runs at the very end, so a timeout commits NOTHING -- not a partial
    load, a wasted quarter hour. COPY does the same work in one server-side pass
    with no per-row parse or bind.

    Chunked at 100k so the CSV buffer stays near 30 MB instead of materialising
    ~240 MB for the validator file on top of its pandas frame.

    csv.writer, not manual joins: SHAP feature names can contain commas and
    quotes, and hand-rolled escaping is how a bulk load silently shifts every
    column one to the right.
    """
    import csv, io as _io
    collist = ", ".join(TARGET_COLS)
    sql = f"COPY ps1_cross_wired_daily ({collist}) FROM STDIN WITH (FORMAT csv, NULL '')"
    done = 0
    for i in range(0, len(rows), chunk):
        buf = _io.StringIO()
        w = csv.writer(buf, lineterminator="\n")
        for r in rows[i:i + chunk]:
            w.writerow([csv_cell(v) for v in r])
        buf.seek(0)
        conn.run(sql, stream=buf)
        done += min(chunk, len(rows) - i)
    return done


VERIFY_SQL = [
    ("summary",     "SELECT * FROM v_ps1_xw_summary ORDER BY device_type"),
    # 0 => (device_key, serial, transit_day) IS unique and a unique index can be
    # added WITH EVIDENCE. Non-zero => it is not, and nothing may assume it is.
    ("grain_dupes", "SELECT COUNT(*) AS dup_keys FROM v_ps1_xw_grain"),
    ("performance", "SELECT * FROM v_ps1_xw_performance ORDER BY device_type"),
    # positive_rate MUST climb LOW < MEDIUM < HIGH < CRITICAL. If it does not,
    # the tier cutpoints are mislabelled and every panel sorting by tier misleads.
    ("tier_calib",  "SELECT device_type, ps1_risk_tier, n_rows, n_positive, "
                    "positive_rate, mean_predicted_prob "
                    "FROM v_ps1_xw_tier_calibration "
                    "ORDER BY device_type, positive_rate DESC"),
    ("shap_top8",   "SELECT device_type, feature_name, n_rows, mean_abs_shap, "
                    "mean_signed_shap, importance_rank FROM v_ps1_shap_importance "
                    "WHERE importance_rank <= 8 ORDER BY device_type, importance_rank"),
    ("causation",   "SELECT * FROM v_ps1_xw_causation ORDER BY device_type"),
    # Should be 0 -- all 34 columns are mapped. Non-zero means the export gained
    # a column the loader has not been told about.
    # ---- IS THE LABEL AN ONSET OR A STATE? -----------------------------
    # will_hardware_oos_3d is true on 91.4% of TVM device-days. The usual cause
    # is labelling the OOS *state* rather than its *onset*: a device out of
    # service for three weeks has every one of those days labelled 1, plus the
    # three before. A model then predicts "is this device currently broken",
    # which is easy and useless -- you cannot pre-empt a failure that already
    # happened.
    #
    # continuation_share  = share of positive days that FOLLOW a positive day.
    #                       Near 0.95 => state, not onset.
    # onset_base_rate     = the real event rate, if the label were onset-only.
    #                       This is the number PS1 should be trained against.
    #
    # Partitioned by (device_id, component_serial_nbr) because the table is at
    # device x component x day grain -- partitioning by device alone would
    # interleave separate components' histories and invent transitions that
    # never happened.
    ("label_shape",
     "SELECT device_type, COUNT(*) AS device_days, "
     "COUNT(*) FILTER (WHERE lbl = 1) AS positive_days, "
     "COUNT(*) FILTER (WHERE lbl = 1 AND prev = 1) AS continuation_days, "
     "ROUND(COUNT(*) FILTER (WHERE lbl = 1 AND prev = 1)::numeric "
     "      / NULLIF(COUNT(*) FILTER (WHERE lbl = 1), 0), 4) AS continuation_share, "
     "COUNT(*) FILTER (WHERE lbl = 1 AND COALESCE(prev, 0) = 0) AS onsets, "
     "ROUND(COUNT(*) FILTER (WHERE lbl = 1 AND COALESCE(prev, 0) = 0)::numeric "
     "      / NULLIF(COUNT(*), 0), 5) AS onset_base_rate "
     "FROM (SELECT device_type, will_hardware_oos_3d AS lbl, "
     "             LAG(will_hardware_oos_3d) OVER (PARTITION BY device_id, "
     "               component_serial_nbr ORDER BY transit_day) AS prev "
     "      FROM ps1_cross_wired_daily WHERE city_id = :c) z "
     "GROUP BY device_type ORDER BY device_type"),
    # How long is a spell? If most positive runs are long, that is the same
    # finding from the other side.
    ("spell_lengths",
     "SELECT device_type, run_len, COUNT(*) AS n_spells FROM ("
     "  SELECT device_type, device_id, component_serial_nbr, grp, COUNT(*) AS run_len"
     "  FROM (SELECT device_type, device_id, component_serial_nbr, transit_day,"
     "               will_hardware_oos_3d AS lbl,"
     "               ROW_NUMBER() OVER (PARTITION BY device_id, component_serial_nbr"
     "                                  ORDER BY transit_day)"
     "             - ROW_NUMBER() OVER (PARTITION BY device_id, component_serial_nbr,"
     "                                  will_hardware_oos_3d ORDER BY transit_day) AS grp"
     "        FROM ps1_cross_wired_daily WHERE city_id = :c) a"
     "  WHERE lbl = 1 GROUP BY device_type, device_id, component_serial_nbr, grp) b "
     "GROUP BY device_type, run_len ORDER BY device_type, run_len"),
    ("extra_used",  "SELECT COUNT(*) AS n FROM ps1_cross_wired_daily WHERE extra IS NOT NULL"),
]


def verify(report):
    """Read-only diagnostics. Writes nothing; safe to run any time.

    Exists because there is no other way to query appdb from CloudShell -- the
    dashboard-api exposes fixed routes only.
    """
    conn = connect()
    report["connected_to"] = dict(CONN_INFO)
    out = {}
    for name, sql in VERIFY_SQL:
        try:
            rows = conn.run(sql, c=CITY_ID)
            cols = [c["name"] for c in conn.columns]
            out[name] = {"columns": cols,
                         "rows": [[str(v) if v is not None else None for v in r]
                                  for r in rows]}
        except Exception as e:
            out[name] = {"error": f"{type(e).__name__}: {e}"[:300]}
    try:
        conn.close()
    except Exception:
        pass
    report["verify"] = out
    return report


# =====================================================================
# AUDIT -- what is actually in Aurora, and could we rebuild it?
#
# n_stamps > 1 or n_runs > 1 means loads have STACKED rather than replaced --
# the same run present twice, inflating every count built on that table.
#
# Read-only. No DDL, no DELETE, no INSERT.
# =====================================================================

# Deliberately conservative: anything not proven says "unknown", because
# "unknown" stops a drop and a wrong guess does not.
RELOAD_SOURCE = {
    "ps1_cross_wired_daily":   "S3 artifacts chicago/device_ps1_cross_wired_daily/{gate,tvm,validator}",
    "ps1_failure_predictions": "sql/load/ps1_predictions_20260726.sql (VERIFY completeness)",
    "ps1_inference_runs":      "sql/load/ps1_run_20260726.sql (VERIFY)",
    "ps1_model_performance":   "sql/load/ps1_sklearn_20260726.sql (VERIFY)",
    "ps3_incident_predictions": "sql/load/ps3_incidents_20260726.sql",
    "ps3_category_coverage":   "sql/load/ps3_coverage_20260726.sql",
    "dim_device_station":      "sql/load/dim_device_station_20260726.sql",
    "dim_device_serial":       "REBUILT by sql/28 from dim_device_component + dim_device_station",
    # THE ONE THAT MATTERS. Nothing ever wrote chicago/dim/device_serial/ to S3
    # and there is no dim_device_component script in sql/load/. Drop it and the
    # device<->serial<->component map every tab joins through is unrecoverable.
    "dim_device_component":    "NO KNOWN SOURCE -- do not drop",
}


def reload_source(t):
    if t in RELOAD_SOURCE:
        return RELOAD_SOURCE[t]
    if t.startswith("ps2_"):
        return "S3 artifacts ps2_outputs/ via cubic-mars-ps2-rds-loader"
    if t.startswith("ps3_"):
        return "unknown -- no PS3 model-output prefix exists in S3; verify before dropping"
    if t.startswith("ps1_"):
        return "unknown -- verify before dropping"
    return "unknown"


def audit(report, prefixes=("ps1", "ps2", "ps3", "dim")):
    conn = connect()
    report["connected_to"] = dict(CONN_INFO)
    like = " OR ".join([f"c.relname LIKE '{p}\\_%'" for p in prefixes])
    try:
        tabs = [r[0] for r in conn.run(
            "SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
            f"WHERE n.nspname = 'public' AND c.relkind = 'r' AND ({like}) ORDER BY c.relname")]
    except Exception as e:
        report["audit_error"] = f"{type(e).__name__}: {e}"
        return report

    # Stamp columns read from the catalogue rather than assumed -- these tables
    # were written by five loaders over several weeks and share no convention.
    have = {}
    for t, c in conn.run(
            "SELECT table_name, column_name FROM information_schema.columns "
            "WHERE table_schema = 'public' AND column_name IN "
            "('city_id','asof_date','computed_date','prediction_date','run_id','transit_day')"):
        have.setdefault(t, set()).add(c)

    out = []
    for t in tabs:
        cols = have.get(t, set())
        sel = ["COUNT(*) AS n_rows"]
        if "city_id" in cols:
            sel.append("COUNT(DISTINCT city_id) AS n_cities")
        stamp = next((c for c in ("asof_date", "computed_date", "prediction_date")
                      if c in cols), None)
        if stamp:
            sel += [f"COUNT(DISTINCT {stamp}) AS n_stamps",
                    f"MIN({stamp})::text AS first_stamp",
                    f"MAX({stamp})::text AS last_stamp"]
        if "run_id" in cols:
            sel.append("COUNT(DISTINCT run_id) AS n_runs")
        try:
            r = conn.run(f"SELECT {', '.join(sel)} FROM {t}")[0]
            names = [c["name"] for c in conn.columns]
            rec = {"table": t, "stamp_column": stamp or "-",
                   "reload_source": reload_source(t)}
            rec.update({n: (str(v) if v is not None else None)
                        for n, v in zip(names, r)})
            rec["stacked"] = bool(
                (rec.get("n_stamps") and int(rec["n_stamps"]) > 1)
                or (rec.get("n_runs") and int(rec["n_runs"]) > 1))
            out.append(rec)
        except Exception as e:
            out.append({"table": t, "error": f"{type(e).__name__}: {str(e)[:160]}"})
    try:
        conn.close()
    except Exception:
        pass
    report["audit"] = out
    report["audit_totals"] = {
        "tables": len(out),
        "empty": sum(1 for r in out if r.get("n_rows") == "0"),
        "stacked": sum(1 for r in out if r.get("stacked")),
        "no_reload_source": sum(1 for r in out
                                if str(r.get("reload_source", "")).startswith(("unknown", "NO KNOWN"))),
    }
    return report


def lambda_handler(event, context):
    event  = event or {}
    action = event.get("action", "load")
    want   = [t.lower() for t in event.get("types", list(TYPES))]
    limit  = event.get("limit")
    asof   = dt.date.today()
    run_id = f"ps1xw_{asof.isoformat().replace('-', '')}"

    report = {"action": action, "bucket": BUCKET, "prefix": PREFIX,
              "city_id": CITY_ID, "run_id": run_id, "files": {}, "errors": {}}

    # verify touches no S3 and writes nothing -- return before the parquet reads.
    if action == "verify":
        return verify(report)
    if action == "audit":
        return audit(report, tuple(event.get("prefixes", ["ps1", "ps2", "ps3", "dim"])))

    frames = {}
    for slug in want:
        if slug not in TYPES:
            report["errors"][slug] = "unknown device type"
            continue
        try:
            df, key, size, mtime = read_parquet(slug)
            frames[slug] = df
            report["files"][slug] = {
                "key": key, "bytes": size,
                "last_modified": mtime.isoformat(),
                "rows_in_s3": int(len(df)),
                "n_columns": int(len(df.columns)),
                "columns": list(df.columns),
                "dtypes": describe(df),
            }
        except Exception as e:
            report["errors"][slug] = f"read failed: {type(e).__name__}: {e}"

    if action == "dry_run":
        # Show the mapping decision without touching the database. This is the
        # answer to "which 8 columns is the console log not naming".
        for slug, df in frames.items():
            _, extras, mapped = shape_rows(df, TYPES[slug], asof, run_id, limit=1)
            report["files"][slug]["mapped_columns"]   = mapped
            report["files"][slug]["unmapped_to_extra"] = extras
        report["total_rows_in_s3"] = sum(
            f.get("rows_in_s3", 0) for f in report["files"].values())
        report["expected"] = EXPECTED
        report["note"] = "dry_run: nothing was written"
        return report

    conn = connect()
    # Report it. A silent database mismatch is what made the first load fail.
    report["connected_to"] = dict(CONN_INFO)
    total = 0
    try:
        conn.run("BEGIN")
        for slug, df in frames.items():
            device_type = TYPES[slug]
            sp = f"sp_{slug}"
            conn.run(f"SAVEPOINT {sp}")
            try:
                rows, extras, mapped = shape_rows(
                    df, device_type, asof, run_id, limit=limit)
                if not rows:
                    raise ValueError("0 rows shaped -- refusing to delete "
                                     "existing rows for an empty load")

                conn.run("DELETE FROM ps1_cross_wired_daily "
                         "WHERE city_id = :c AND device_type = :d",
                         c=CITY_ID, d=device_type)

                cols = ", ".join(TARGET_COLS)
                ins  = copy_rows(conn, rows)

                conn.run(f"RELEASE SAVEPOINT {sp}")
                total += ins
                report["files"][slug].update({
                    "rows_loaded": ins,
                    "mapped_columns": mapped,
                    "unmapped_to_extra": extras,
                })
            except Exception as e:
                conn.run(f"ROLLBACK TO SAVEPOINT {sp}")
                report["errors"][slug] = f"{type(e).__name__}: {e}"
                print(f"[{slug}] FAILED {type(e).__name__}: {e}")

        conn.run("COMMIT")
    except Exception as e:
        try:
            conn.run("ROLLBACK")
        except Exception:
            pass
        report["errors"]["__transaction__"] = f"{type(e).__name__}: {e}"
    finally:
        try:
            conn.close()
        except Exception:
            pass

    report["total_rows_loaded"] = total
    report["expected"] = EXPECTED
    print(json.dumps({k: v for k, v in report.items() if k != "files"}))
    return report
