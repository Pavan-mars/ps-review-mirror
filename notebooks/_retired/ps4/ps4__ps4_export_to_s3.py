"""
ps4_export_to_s3.py  --  the LAST cell of the PS4 notebook.

This is the piece that is missing today. The 24-Jul PS4 run trained fine and then
stopped: its console log shows the XGBoost fit starting and nothing after it, and
the only S3 path it mentions is the gold table it READS
(chicago/gold/device_ps4_hourly). It never wrote an output folder, so
cubic-mars-ps4-rds-loader has had nothing to load since the day it was built.

WHAT THIS WRITES

    s3://<gold>/chicago/ml_outputs/ps4/<run_id>/
        ps4_model_leaderboard.parquet
        ps4_spc_thresholds.parquet
        ps4_signal_summary.parquet
        ps4_device_daily.parquet
        ps4_device_hourly.parquet      (windowed -- see below)
        ps4_station_anomaly.parquet
        ps4_leakage_scan.parquet
        manifest.json                  <-- WRITTEN LAST; this is the trigger

The Lambda has an S3 ObjectCreated notification on manifest.json only, so it can
never fire against a half-written folder. Order matters here: if you move the
manifest write earlier, the loader will read a partial run and report success.

THE CONTRACT
    Every key under `outputs` must name a table in the loader's ALLOWED_TABLES.
    Anything else is REFUSED -- the whole run, not just that file. That is on
    purpose: a typo'd table name should stop the load, not silently skip a feed.

    target_rate is REQUIRED whenever ps4_model_leaderboard is present. The 24-Jul
    run reported test AP 0.9994; AP without its base rate beside it is not a
    result, it is a number. The loader refuses the leaderboard if it is absent.

THE LEAKAGE SCAN IS NOT OPTIONAL
    spark_gbt scored test AP 0.9994 and spark_rf 0.9992 against a target rate of
    ~0.48 on 29.2M training rows. A near-perfect AP on a coin-flip base rate is
    the signature of a feature that encodes the label. ps4_leakage_scan carries
    one row per feature with its SOLO AUC -- the AUC of that feature used alone.
    Any feature above ~0.90 solo is doing the model's whole job and needs
    explaining before this run is shown to anyone.
"""
import datetime
import json
import uuid

import boto3
from pyspark.sql import functions as F

# ---------------------------------------------------------------- config ----
S3_BUCKET = "cubic-mars-pm-s3-datalake-dev-gold-170202974600"
PS4_ROOT = "chicago/ml_outputs/ps4"
CITY_ID = "CHI"

RUN_ID = f"ps4_{datetime.date.today():%Y%m%d}"
AS_OF = datetime.date.today().isoformat()
OUT = f"s3://{S3_BUCKET}/{PS4_ROOT}/{RUN_ID}"

# device_ps4_hourly is 36.6M device-hours. The daily rollup carries all history;
# the hourly detail is capped so Aurora stays a serving store and not a second
# copy of the lake. Keep this in step with the Lambda's HOURLY_WINDOW_DAYS.
HOURLY_WINDOW_DAYS = 7

_s3 = boto3.client("s3")
outputs = {}


def put(df, table):
    """Write one output and register it in the manifest. Coalesced to one file."""
    if df is None:
        print(f"  [skip] {table}: no dataframe")
        return
    n = df.count()
    if n == 0:
        print(f"  [skip] {table}: 0 rows")
        return
    df.coalesce(1).write.mode("overwrite").parquet(f"{OUT}/{table}.parquet")
    outputs[table] = f"{table}.parquet"
    print(f"  {table:<24} {n:>12,} rows")


print(f"PS4 export -> {OUT}")

# ------------------------------------------------------------- the feeds ----
# Each of these must already exist in the notebook. Names follow the notebook's
# own variables; rename on the left only, never reshape on the right -- the
# loader validates columns against ALLOWED_TABLES and drops anything it does not
# recognise, so a silently-renamed column becomes a silently-missing column.

put(leaderboard_sdf,      "ps4_model_leaderboard")
put(spc_thresholds_sdf,   "ps4_spc_thresholds")
put(signal_summary_sdf,   "ps4_signal_summary")
put(device_daily_sdf,     "ps4_device_daily")
put(station_anomaly_sdf,  "ps4_station_anomaly")
put(leakage_scan_sdf,     "ps4_leakage_scan")

# Hourly, windowed. Filtered HERE rather than in the Lambda so what lands in S3
# is what lands in Aurora -- a cut applied downstream is invisible to anyone
# reading the bucket.
_cut = F.date_sub(F.current_date(), HOURLY_WINDOW_DAYS)
put(device_hourly_sdf.where(F.col("hour_bucket") >= _cut), "ps4_device_hourly")

# ------------------------------------------------------------- manifest -----
# LAST. Nothing above this line triggers anything.
manifest = {
    "run_id": RUN_ID,
    "city_id": CITY_ID,
    "computed_date": AS_OF,
    "run_ts": datetime.datetime.utcnow().isoformat(),
    "run_kind": "train",
    "source_notebook": "PS4_SageMaker_MLflow_FeatureStore_PySpark.ipynb",
    "gold_snapshot_s3": f"s3://{S3_BUCKET}/chicago/gold/device_ps4_hourly",
    "champion": CHAMPION_MODEL,          # e.g. "spark_gbt"
    "target_col": TARGET_COL,            # e.g. "is_anomaly_next_24h"
    "n_rows_total": N_ROWS_TOTAL,
    "n_rows_train": N_ROWS_TRAIN,
    "n_rows_val": N_ROWS_VAL,
    "n_rows_test": N_ROWS_TEST,
    "n_features": N_FEATURES,
    # REQUIRED whenever the leaderboard is exported. Without it the loader
    # refuses ps4_model_leaderboard rather than write a naked AP.
    "target_rate": TARGET_RATE,          # e.g. 0.4796
    "data_start": DATA_START,
    "data_end": DATA_END,
    "hourly_window_days": HOURLY_WINDOW_DAYS,
    "leakage_checked": bool(outputs.get("ps4_leakage_scan")),
    "notes": NOTES,
    "outputs": outputs,
}

_s3.put_object(
    Bucket=S3_BUCKET,
    Key=f"{PS4_ROOT}/{RUN_ID}/manifest.json",
    Body=json.dumps(manifest, indent=2, default=str).encode())

print(f"\nmanifest -> {OUT}/manifest.json")
print(f"outputs  : {sorted(outputs)}")
if not outputs.get("ps4_leakage_scan"):
    print("\n  WARNING: no leakage scan in this export. test AP 0.9994 against a "
          "~0.48 base rate needs one before the run is presented.")
print("\nS3 ObjectCreated on the manifest now triggers cubic-mars-ps4-rds-loader.")
