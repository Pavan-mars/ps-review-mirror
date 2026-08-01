"""
Local integration test for cubic-mars-ps3-rds-push, run against:
  - a real local PostgreSQL instance (stand-in for Aurora PostgreSQL dev)
  - a moto-mocked S3 bucket (stand-in for the export bucket / manifest.json trigger)

Same pattern as cubic-mars-ps2-rds-push/test_handler.py (21-Jul-2026 PS2 serial-grain
delivery) — forked, not imported, so this Lambda has zero cross-Lambda dependency.

  RDS_HOST=127.0.0.1 RDS_PORT=5432 RDS_DATABASE=ps3_test \
  RDS_TEST_USER=postgres RDS_TEST_PASSWORD=testpass \
  python3 test_handler.py

Exercises FOUR cases (one more than the PS2 test — the on-demand producer):
  1. CREATE TABLE from scratch (ps3_device_leaderboard, from the deep-dive notebook)
  2. ADD COLUMN IF NOT EXISTS on a re-run with one new column (schema evolution)
  2b. Idempotent re-run of the same manifest (no duplicate rows)
  3. Type-conflict column is SKIPPED, not auto-ALTERed (safety rule)
  4. ps3_ondemand_inference_log — the cubic-mars-ps3-inference Lambda's producer
     shape loads through the identical path (proves the two Lambdas' contracts agree)
"""
import io
import json
import os
import sys

import boto3
import pandas as pd
import pg8000.native as pg8000
from moto import mock_aws

sys.path.insert(0, os.path.dirname(__file__))

RDS_HOST = os.environ.get("RDS_HOST", "127.0.0.1")
RDS_PORT = int(os.environ.get("RDS_PORT", 5432))
RDS_DATABASE = os.environ.get("RDS_DATABASE", "ps3_test")
RDS_USER = os.environ.get("RDS_TEST_USER", "postgres")
RDS_PASSWORD = os.environ.get("RDS_TEST_PASSWORD", "testpass")
BUCKET = "cubic-mars-pm-s3-datalake-dev-gold-170202974600"

os.environ["RDS_HOST"] = RDS_HOST
os.environ["RDS_PORT"] = str(RDS_PORT)
os.environ["RDS_DATABASE"] = RDS_DATABASE
os.environ["AWS_REGION"] = "us-east-1"
os.environ["AWS_DEFAULT_REGION"] = "us-east-1"


def make_manifest_and_parquet(table_name, grain, computed_date, run_id, df, prefix):
    df = df.copy()
    df["grain"] = grain
    df["run_id"] = run_id
    df["computed_date"] = computed_date
    df["city_id"] = "CHI"

    key_prefix = f"{prefix}/{table_name}/computed_date={computed_date}/run_id={run_id}"
    buf = io.BytesIO()
    df.to_parquet(buf, index=False)
    buf.seek(0)
    data_key = f"{key_prefix}/part-0.parquet"
    manifest = {
        "table_name": table_name, "grain": grain, "row_count": len(df), "columns": list(df.columns),
        "computed_date": computed_date, "run_id": run_id, "s3_data_key": data_key,
    }
    manifest_key = f"{key_prefix}/manifest.json"
    return data_key, buf.getvalue(), manifest_key, json.dumps(manifest, indent=2).encode()


def reset_test_table(cur, table_name):
    cur.run(f'DROP TABLE IF EXISTS "{table_name}" CASCADE')


def run():
    conn = pg8000.Connection(user=RDS_USER, password=RDS_PASSWORD, host=RDS_HOST,
                              port=RDS_PORT, database=RDS_DATABASE)
    conn.run("DROP TABLE IF EXISTS ps3_load_audit")
    reset_test_table(conn, "ps3_device_leaderboard_test")
    reset_test_table(conn, "ps3_ondemand_inference_log")
    conn.close()

    import handler as h
    h._CACHED_CREDS = {"username": RDS_USER, "password": RDS_PASSWORD,
                       "host": RDS_HOST, "port": RDS_PORT, "dbname": RDS_DATABASE}
    h._CACHED_CONN = None

    with mock_aws():
        s3 = boto3.client("s3", region_name="us-east-1")
        s3.create_bucket(Bucket=BUCKET)

        # ---- Case 1: CREATE TABLE from scratch (device leaderboard) --------------
        df1 = pd.DataFrame({
            "entity_rank": [1, 2], "device_id": ["TVM-0030", "TVM-0028"],
            "risk_score": [85.74, 76.05], "risk_band": ["CRITICAL", "CRITICAL"],
            "mars_device_category": ["TVM", "TVM"],
        })
        data_key, data_bytes, manifest_key, manifest_bytes = make_manifest_and_parquet(
            "ps3_device_leaderboard_test", "device", "2026-07-21", "run-001", df1, "chicago/ps3_deepdive")
        s3.put_object(Bucket=BUCKET, Key=data_key, Body=data_bytes)
        s3.put_object(Bucket=BUCKET, Key=manifest_key, Body=manifest_bytes)

        result1 = h.process_manifest(BUCKET, manifest_key)
        assert result1["status"] == "ok", result1
        assert result1["rows_loaded"] == 2, result1
        print("[case 1] CREATE TABLE:", result1)

        conn = pg8000.Connection(user=RDS_USER, password=RDS_PASSWORD, host=RDS_HOST,
                                  port=RDS_PORT, database=RDS_DATABASE)
        cols = {r[0] for r in conn.run(
            "SELECT column_name FROM information_schema.columns WHERE table_name=:t",
            t="ps3_device_leaderboard_test")}
        assert {"entity_rank", "device_id", "risk_score", "risk_band", "grain", "run_id", "computed_date"}.issubset(cols), cols
        print("[case 1] columns after create:", sorted(cols))

        # ---- Case 2: re-run with a NEW column -> ADD COLUMN IF NOT EXISTS --------
        df2 = pd.DataFrame({
            "entity_rank": [1], "device_id": ["TVM-0099"], "risk_score": [91.2],
            "risk_band": ["CRITICAL"], "mars_device_category": ["TVM"],
            "rootcause_conf_gap": [0.61],   # <- new column
        })
        data_key2, data_bytes2, manifest_key2, manifest_bytes2 = make_manifest_and_parquet(
            "ps3_device_leaderboard_test", "device", "2026-07-22", "run-002", df2, "chicago/ps3_deepdive")
        s3.put_object(Bucket=BUCKET, Key=data_key2, Body=data_bytes2)
        s3.put_object(Bucket=BUCKET, Key=manifest_key2, Body=manifest_bytes2)

        result2 = h.process_manifest(BUCKET, manifest_key2)
        assert result2["status"] == "ok", result2
        assert "rootcause_conf_gap" in result2["columns_added"], result2
        print("[case 2] ADD COLUMN:", result2)

        row_count = conn.run('SELECT COUNT(*) FROM "ps3_device_leaderboard_test"')[0][0]
        assert row_count == 3, f"expected 3 rows (2 + 1), got {row_count}"

        # ---- Case 2b: re-run run-002 AGAIN (idempotency) --------------------------
        h.process_manifest(BUCKET, manifest_key2)
        row_count_2b = conn.run('SELECT COUNT(*) FROM "ps3_device_leaderboard_test"')[0][0]
        assert row_count_2b == 3, f"re-running the same manifest should not duplicate rows, got {row_count_2b}"
        print(f"[case 2b] idempotent re-run OK: still {row_count_2b} rows")

        # ---- Case 3: TYPE CONFLICT column is skipped, never auto-ALTERed ----------
        df3 = pd.DataFrame({
            "entity_rank": [1], "device_id": ["GATE-0004"],
            "risk_score": ["not-a-number-anymore"],  # was double precision -> CONFLICT
            "risk_band": ["HIGH"], "mars_device_category": ["GATE"],
        })
        data_key3, data_bytes3, manifest_key3, manifest_bytes3 = make_manifest_and_parquet(
            "ps3_device_leaderboard_test", "device", "2026-07-23", "run-003", df3, "chicago/ps3_deepdive")
        s3.put_object(Bucket=BUCKET, Key=data_key3, Body=data_bytes3)
        s3.put_object(Bucket=BUCKET, Key=manifest_key3, Body=manifest_bytes3)

        result3 = h.process_manifest(BUCKET, manifest_key3)
        assert any(c["column"] == "risk_score" for c in result3["columns_conflicted"]), result3
        print("[case 3] TYPE CONFLICT correctly skipped:", result3["columns_conflicted"])

        stored_type = conn.run(
            "SELECT data_type FROM information_schema.columns WHERE table_name=:t AND column_name='risk_score'",
            t="ps3_device_leaderboard_test")[0][0]
        assert "double" in stored_type.lower(), f"risk_score type must NOT have been auto-altered, got {stored_type}"
        print(f"[case 3] risk_score column type unchanged: {stored_type}")

        # ---- Case 4: cubic-mars-ps3-inference's on-demand producer shape ----------
        # Proves the two Lambdas' write_output()/manifest contract genuinely agree —
        # this Lambda has no special-case code for "ondemand" vs. "device"/"serial" grain.
        df4 = pd.DataFrame({
            "city_id": ["CHI"], "device_id": ["TVM-0500"], "matched_serial_nbr": ["SER-777"],
            "pred_severity": ["ALL_FUNCTIONS"], "pred_severity_collapsed": ["CRITICAL"],
            "pred_component": ["CSC_READER"], "endpoint_name": ["cubic-mars-ps3-two-head-dev"],
            "source": ["ondemand"],
        })
        data_key4, data_bytes4, manifest_key4, manifest_bytes4 = make_manifest_and_parquet(
            "ps3_ondemand_inference_log", "ondemand", "2026-07-21", "run-ondemand-001", df4, "chicago/ps3_deepdive")
        s3.put_object(Bucket=BUCKET, Key=data_key4, Body=data_bytes4)
        s3.put_object(Bucket=BUCKET, Key=manifest_key4, Body=manifest_bytes4)
        result4 = h.process_manifest(BUCKET, manifest_key4)
        assert result4["status"] == "ok", result4
        assert result4["rows_loaded"] == 1
        print("[case 4] on-demand inference log loaded through the same path:", result4)

        audit_rows = conn.run("SELECT table_name, status, columns_added, columns_conflicted FROM ps3_load_audit ORDER BY id")
        print(f"\n[audit] {len(audit_rows)} ps3_load_audit rows written:")
        for r in audit_rows:
            print("   ", r)

        conn.close()

    print("\nALL CASES PASSED.")


if __name__ == "__main__":
    run()
