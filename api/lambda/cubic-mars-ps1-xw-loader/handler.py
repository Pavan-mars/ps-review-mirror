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


def connect():
    c = secret()
    return pg8000.native.Connection(
        user=c["username"], password=c["password"],
        host=RDS_HOST, database=RDS_DB, port=int(c.get("port", 5432)),
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


def lambda_handler(event, context):
    event  = event or {}
    action = event.get("action", "load")
    want   = [t.lower() for t in event.get("types", list(TYPES))]
    limit  = event.get("limit")
    asof   = dt.date.today()
    run_id = f"ps1xw_{asof.isoformat().replace('-', '')}"

    report = {"action": action, "bucket": BUCKET, "prefix": PREFIX,
              "city_id": CITY_ID, "run_id": run_id, "files": {}, "errors": {}}

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
                n    = len(TARGET_COLS)
                ins  = 0
                for i in range(0, len(rows), BATCH):
                    chunk = rows[i:i + BATCH]
                    ph, params = [], {}
                    for j, r in enumerate(chunk):
                        names = [f"p{j}_{k}" for k in range(n)]
                        # CAST_SUFFIX is not cosmetic. pg8000 binds a Python str
                        # as text (OID 25), and Postgres has NO implicit or
                        # assignment cast from text to jsonb -- the INSERT would
                        # fail with "column extra is of type jsonb but
                        # expression is of type text". The ::jsonb is what makes
                        # the bind legal.
                        ph.append("(" + ", ".join(
                            ":" + x + CAST_SUFFIX.get(TARGET_COLS[k], "")
                            for k, x in enumerate(names)) + ")")
                        for k, name in enumerate(names):
                            params[name] = r[k]
                    sql = (f"INSERT INTO ps1_cross_wired_daily ({cols}) VALUES "
                           + ", ".join(ph))
                    conn.run(sql, **params)
                    ins += len(chunk)

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
