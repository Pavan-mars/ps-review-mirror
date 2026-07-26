# =============================================================================
# ps3_rds_writer -- upsert PS3 scored feeds (from S3 / notebook outputs) into RDS.
#
# Reads the CSVs the PS3 notebook / batch scorer produced and idempotently
# upserts them into the migration-04 tables via INSERT ... ON CONFLICT DO UPDATE
# (the daily job is safe to retry). Also writes the two-head scorecards.
#
# Runs as a Databricks task or standalone (psycopg2 + boto3 + pandas). RDS creds
# come from Secrets Manager (never hard-coded) -- Aurora PostgreSQL is the APP-TIER
# DB only; it is NEVER the ML source.
#
# Usage:
#   python ps3_rds_writer.py --outputs s3://.../PS3_outputs_pavan --city CHI \
#          --secret cubic/rds/dashboard --asof 2026-07-18
# =============================================================================
import os, sys, json, argparse, datetime as dt
import pandas as pd

try:
    import psycopg2
    from psycopg2.extras import execute_values
except Exception:
    psycopg2 = None


def get_conn(secret_id, region="us-east-1"):
    """Resolve RDS creds from Secrets Manager and open a psycopg2 connection."""
    import boto3
    sm = boto3.client("secretsmanager", region_name=region)
    s = json.loads(sm.get_secret_value(SecretId=secret_id)["SecretString"])
    return psycopg2.connect(host=s["host"], port=s.get("port", 5432), dbname=s["dbname"],
                            user=s["username"], password=s["password"])


def _read(base, rel):
    """Read a CSV from S3 or local path; return None if absent."""
    path = base.rstrip("/") + "/" + rel
    try:
        so = {"client_kwargs": {"region_name": "us-east-1"}} if path.startswith("s3") else None
        return pd.read_csv(path, storage_options=so)
    except Exception as e:
        print(f"  [skip] {rel}: {type(e).__name__}"); return None


def upsert(cur, table, cols, rows, conflict, update_cols):
    if not rows:
        return 0
    set_clause = ", ".join(f"{c}=EXCLUDED.{c}" for c in update_cols)
    sql = (f"INSERT INTO {table} ({', '.join(cols)}) VALUES %s "
           f"ON CONFLICT ({conflict}) DO UPDATE SET {set_clause}")
    execute_values(cur, sql, rows)
    return len(rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--outputs", required=True, help="PS3_outputs_pavan base (s3:// or local)")
    ap.add_argument("--city", default="CHI")
    ap.add_argument("--secret", required=True, help="Secrets Manager id for RDS creds")
    ap.add_argument("--region", default="us-east-1")
    ap.add_argument("--asof", default=dt.date.today().strftime("%Y-%m-%d"))
    ap.add_argument("--sev-model-version", default="v9")
    ap.add_argument("--rc-model-version", default="v1")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    if psycopg2 is None and not a.dry_run:
        sys.exit("psycopg2 not installed -- pip install psycopg2-binary (or run --dry-run)")

    conn = None if a.dry_run else get_conn(a.secret, a.region)
    cur = None if a.dry_run else conn.cursor()

    # 1) open a scoring-run row -> run_id (idempotency + lineage)
    run_id = None
    if not a.dry_run:
        cur.execute("""INSERT INTO ps3_scoring_runs
                       (city_id, gold_source_partition, severity_model_version,
                        rootcause_model_version, scoring_mode, status)
                       VALUES (%s,%s,%s,%s,'BATCH','SUCCESS') RETURNING run_id""",
                    (a.city, a.asof, a.sev_model_version, a.rc_model_version))
        run_id = cur.fetchone()[0]
    print(f"scoring run_id = {run_id} | asof {a.asof}")

    tot = {"incident": 0, "device": 0, "serial": 0}
    for cat, folder in [("TVM", "tvm"), ("GATE", "gates")]:
        # ---- incident predictions (two heads -> one row per incident per head) ----
        inc = _read(a.outputs, f"{folder}/{folder if folder!='gates' else 'gate'}_incident_predictions.csv")
        # notebook prefixes files with the folder-singular (tvm_/gate_)
        if inc is None:
            inc = _read(a.outputs, f"{folder}/{cat.lower()}_incident_predictions.csv")
        if inc is not None:
            rows = []
            for _, r in inc.iterrows():
                if "pred_severity" in inc.columns and pd.notna(r.get("pred_severity")):
                    rows.append((a.city, run_id, str(r["availability_event_id"]), str(r["device_id"]), cat,
                                 r.get("matched_serial_nbr"), _int(r.get("component_age_days")),
                                 r.get("FACILITY_ID"), r.get("AE_START_DTM"), "severity",
                                 str(r["pred_severity"]), _num(r.get("pred_severity_conf")),
                                 r.get("pred_severity_collapsed"), r.get("actual_severity"), a.sev_model_version))
                if "pred_component" in inc.columns and pd.notna(r.get("pred_component")):
                    rows.append((a.city, run_id, str(r["availability_event_id"]), str(r["device_id"]), cat,
                                 r.get("matched_serial_nbr"), _int(r.get("component_age_days")),
                                 r.get("FACILITY_ID"), r.get("AE_START_DTM"), "root_cause",
                                 str(r["pred_component"]), _num(r.get("pred_component_conf")),
                                 None, r.get("actual_component"), a.rc_model_version))
            cols = ["city_id", "run_id", "availability_event_id", "device_id", "mars_device_category",
                    "matched_serial_nbr", "component_age_days", "facility_id", "incident_dtm",
                    "prediction_head", "predicted_class", "predicted_confidence", "predicted_collapsed",
                    "actual_class", "model_version"]
            if not a.dry_run:
                tot["incident"] += upsert(cur, "ps3_incident_predictions", cols, rows,
                                          "city_id, availability_event_id, prediction_head, model_version",
                                          ["predicted_class", "predicted_confidence", "predicted_collapsed",
                                           "actual_class", "run_id", "scored_at"])
            print(f"  {cat} incident rows: {len(rows)}")

        # ---- device rollup ----
        dev = _read(a.outputs, f"{folder}/{cat.lower()}_device_predictions.csv")
        if dev is not None and not a.dry_run:
            cols = ["city_id", "run_id", "device_id", "mars_device_category", "n_incidents",
                    "pct_critical_pred", "dominant_pred_severity", "dominant_pred_component",
                    "avg_component_age_days", "last_incident_dtm", "as_of_date"]
            rows = [(a.city, run_id, str(r["device_id"]), cat, _int(r.get("n_incidents")),
                     _num(r.get("pct_critical_pred")), r.get("dominant_pred_severity"),
                     r.get("dominant_pred_component"), _int(r.get("avg_component_age_days")),
                     r.get("last_incident_dtm"), a.asof) for _, r in dev.iterrows()]
            tot["device"] += upsert(cur, "ps3_device_predictions", cols, rows,
                                    "city_id, device_id, as_of_date",
                                    ["n_incidents", "pct_critical_pred", "dominant_pred_severity",
                                     "dominant_pred_component", "avg_component_age_days",
                                     "last_incident_dtm", "run_id", "scored_at"])

        # ---- serial rollup (serial-level ask) ----
        ser = _read(a.outputs, f"{folder}/{cat.lower()}_serial_predictions.csv")
        if ser is not None and not a.dry_run:
            cols = ["city_id", "run_id", "device_id", "matched_serial_nbr", "mars_device_category",
                    "component_age_days", "n_incidents", "dominant_pred_component", "pct_critical_pred",
                    "last_incident_dtm", "as_of_date"]
            rows = [(a.city, run_id, str(r["device_id"]), str(r["matched_serial_nbr"]), cat,
                     _int(r.get("component_age_days")), _int(r.get("n_incidents")),
                     r.get("dominant_pred_component"), _num(r.get("pct_critical_pred")),
                     r.get("last_incident_dtm"), a.asof) for _, r in ser.iterrows()]
            tot["serial"] += upsert(cur, "ps3_serial_predictions", cols, rows,
                                    "city_id, device_id, matched_serial_nbr, as_of_date",
                                    ["component_age_days", "n_incidents", "dominant_pred_component",
                                     "pct_critical_pred", "last_incident_dtm", "run_id", "scored_at"])

    if not a.dry_run:
        cur.execute("""UPDATE ps3_scoring_runs SET n_incidents_scored=%s, n_devices_scored=%s,
                       n_serials_scored=%s WHERE run_id=%s""",
                    (tot["incident"], tot["device"], tot["serial"], run_id))
        conn.commit(); cur.close(); conn.close()
    print(f"upserted -> incident {tot['incident']}, device {tot['device']}, serial {tot['serial']}"
          + (" (DRY-RUN, nothing written)" if a.dry_run else ""))


def _num(x):
    try:
        return None if x is None or (isinstance(x, float) and pd.isna(x)) else float(x)
    except Exception:
        return None


def _int(x):
    v = _num(x)
    return None if v is None else int(v)


if __name__ == "__main__":
    main()
