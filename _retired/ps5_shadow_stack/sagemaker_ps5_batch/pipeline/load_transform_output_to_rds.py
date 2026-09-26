#!/usr/bin/env python3
# =============================================================================
# load_transform_output_to_rds.py -- load the Batch Transform output into RDS.
# Reads the scored device + serial parquet the container wrote to S3, writes ONE
# ps5_scoring_runs row (lineage), then upserts ps5_reliability_estimates and
# ps5_serial_reliability. Idempotent (ON CONFLICT ... as_of_date) and GATED
# (data_quality_gate_passed carried from the model's gate_pass).
#
# Schema target = migration 05 + 06. Aurora is the APP-TIER db only.
#
# Usage:
#   python load_transform_output_to_rds.py --scored s3://.../chicago/ps5/scored \
#     --asof 2026-04-11 --city CHI --secret cubic/rds/dashboard [--dry-run]
# =============================================================================
import argparse, io, json, math, uuid

import pandas as pd


def _f(v):
    try:
        return None if v is None or (isinstance(v, float) and math.isnan(v)) else float(v)
    except Exception:
        return None


def _fac(v):
    return str(v) if (v is not None and not (isinstance(v, float) and pd.isna(v)) and str(v) != "nan") else None


def _read_s3_prefix(bucket, prefix):
    """Concat every parquet object under a Batch-Transform output prefix (files end in .out)."""
    import boto3
    s3 = boto3.client("s3"); frames = []
    tok = None
    while True:
        kw = {"Bucket": bucket, "Prefix": prefix}
        if tok:
            kw["ContinuationToken"] = tok
        resp = s3.list_objects_v2(**kw)
        for o in resp.get("Contents", []):
            if o["Key"].endswith("/"):
                continue
            body = s3.get_object(Bucket=bucket, Key=o["Key"])["Body"].read()
            try:
                frames.append(pd.read_parquet(io.BytesIO(body)))
            except Exception:
                frames.append(pd.read_csv(io.BytesIO(body)))
        if not resp.get("IsTruncated"):
            break
        tok = resp.get("NextContinuationToken")
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def _split_s3(uri):
    p = uri.replace("s3://", "").split("/", 1)
    return p[0], (p[1] if len(p) > 1 else "")


DEV_SQL = """INSERT INTO ps5_reliability_estimates
  (city_id, run_id, device_id, mars_device_category, current_healthy_age_days, rul_standard_days,
   predicted_median_survival_days, hazard_score, risk_band, is_overdue, n_prior_failures, concordance_index,
   champion_model, weibull_shape, data_quality_gate_passed, facility_id, as_of_date, days_since_hw_oos,
   roll_fail_30d, event_definition, event_def_version, feature_asof_date, scored_at)
  VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s, now())
  ON CONFLICT (city_id, device_id, as_of_date) DO UPDATE SET
   run_id=EXCLUDED.run_id, mars_device_category=EXCLUDED.mars_device_category,
   current_healthy_age_days=EXCLUDED.current_healthy_age_days, rul_standard_days=EXCLUDED.rul_standard_days,
   predicted_median_survival_days=EXCLUDED.predicted_median_survival_days, hazard_score=EXCLUDED.hazard_score,
   risk_band=EXCLUDED.risk_band, is_overdue=EXCLUDED.is_overdue, n_prior_failures=EXCLUDED.n_prior_failures,
   concordance_index=EXCLUDED.concordance_index, champion_model=EXCLUDED.champion_model,
   weibull_shape=EXCLUDED.weibull_shape, data_quality_gate_passed=EXCLUDED.data_quality_gate_passed,
   facility_id=EXCLUDED.facility_id, days_since_hw_oos=EXCLUDED.days_since_hw_oos,
   roll_fail_30d=EXCLUDED.roll_fail_30d, event_definition=EXCLUDED.event_definition,
   event_def_version=EXCLUDED.event_def_version, feature_asof_date=EXCLUDED.feature_asof_date, scored_at=now()"""

SER_SQL = """INSERT INTO ps5_serial_reliability
  (city_id, run_id, device_id, component_serial_nbr, component_type, mars_device_category, component_age_days,
   device_oos_failures_total, risk_score, risk_tier, expected_component_rul_days, predicted_median_survival_days,
   is_overdue, as_of_date, event_definition, event_def_version, feature_asof_date, scored_at)
  VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s, now())
  ON CONFLICT (city_id, device_id, component_serial_nbr, as_of_date) DO UPDATE SET
   run_id=EXCLUDED.run_id, component_type=EXCLUDED.component_type, mars_device_category=EXCLUDED.mars_device_category,
   component_age_days=EXCLUDED.component_age_days, device_oos_failures_total=EXCLUDED.device_oos_failures_total,
   risk_score=EXCLUDED.risk_score, risk_tier=EXCLUDED.risk_tier,
   expected_component_rul_days=EXCLUDED.expected_component_rul_days,
   predicted_median_survival_days=EXCLUDED.predicted_median_survival_days, is_overdue=EXCLUDED.is_overdue,
   event_definition=EXCLUDED.event_definition, event_def_version=EXCLUDED.event_def_version,
   feature_asof_date=EXCLUDED.feature_asof_date, scored_at=now()"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--scored", required=True, help="s3://.../chicago/ps5/scored (has device/ and serial/ subprefixes)")
    ap.add_argument("--asof", required=True)
    ap.add_argument("--city", default="CHI")
    ap.add_argument("--secret", required=False, help="Secrets Manager id for RDS creds")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    bucket, base = _split_s3(args.scored.rstrip("/"))
    dev = _read_s3_prefix(bucket, f"{base}/device/asof={args.asof}/")
    ser = _read_s3_prefix(bucket, f"{base}/serial/asof={args.asof}/")
    print(f"[load] device rows={len(dev)} serial rows={len(ser)} (asof {args.asof})")
    if len(dev):
        gp = bool(dev["gate_pass"].iloc[0]) if "gate_pass" in dev.columns else False
        print(f"[load] event={dev.get('event_def_version', pd.Series(['?'])).iloc[0]} | gate_pass={gp} "
              f"| median RUL={dev['rul_standard_days'].median():.0f}d")

    if args.dry_run or not args.secret:
        print("[load] dry-run (no RDS write). Pass --secret to write."); return

    import boto3, psycopg2
    sec = json.loads(boto3.client("secretsmanager").get_secret_value(SecretId=args.secret)["SecretString"])
    conn = psycopg2.connect(host=sec["host"], port=int(sec.get("port", 5432)), dbname=sec["dbname"],
                            user=sec["username"], password=sec["password"], connect_timeout=8)
    conn.autocommit = False
    run_id = str(uuid.uuid4())
    edv = str(dev["event_def_version"].iloc[0]) if len(dev) and "event_def_version" in dev.columns else "2026-07-23.v1"
    try:
        cur = conn.cursor()
        cur.execute("""INSERT INTO ps5_scoring_runs
            (run_id, city_id, run_ts, n_devices_scored, n_serials_scored, model_version, scoring_mode, status,
             note, event_definition, event_def_version, event_filter, run_date, scorer)
            VALUES (%s,%s, now(), %s,%s,%s,'BATCH','SUCCESS',%s,'hw_oos_set',%s,'{}'::jsonb,%s,'batch_transform_loader')""",
            (run_id, args.city, len(dev), len(ser), edv, f"batch-transform OOS-Set {args.asof}", edv, args.asof))
        for _, r in dev.iterrows():
            cur.execute(DEV_SQL, (
                args.city, run_id, r["device_id"], r["mars_device_category"], _f(r["current_healthy_age_days"]),
                _f(r["rul_standard_days"]), _f(r["predicted_median_survival_days"]), _f(r["hazard_score"]),
                r["risk_band"], bool(r["is_overdue"]), int(r.get("n_prior_oos", 0)), _f(r.get("cv_cindex")),
                (r.get("champion") or "CoxPH (params-only serving)"), _f(r.get("weibull_shape")),
                bool(r.get("gate_pass", False)), _fac(r.get("facility_id")), args.asof,
                _f(r.get("days_since_hw_oos")), int(r.get("roll_fail_30d", 0)),
                str(r.get("event_definition", "hw_oos_set")), str(r.get("event_def_version", edv)),
                str(r.get("feature_asof_date", args.asof))))
        for _, r in ser.iterrows():
            cur.execute(SER_SQL, (
                args.city, run_id, r["device_id"], r["component_serial_nbr"], r.get("component_type_name"),
                r["mars_device_category"], _f(r["component_age_days"]), int(r.get("device_oos_failures_total", 0)),
                _f(r["risk_score"]), r["risk_tier"], _f(r["expected_component_rul_days"]),
                _f(r["predicted_median_survival_days"]), bool(r["is_overdue"]), args.asof,
                str(r.get("event_definition", "hw_oos_set")), str(r.get("event_def_version", edv)),
                str(r.get("feature_asof_date", args.asof))))
        conn.commit()
        print(f"[load] committed run {run_id}: {len(dev)} devices + {len(ser)} serials for {args.city} @ {args.asof}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
