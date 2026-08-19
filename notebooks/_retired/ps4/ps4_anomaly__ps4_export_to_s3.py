"""
ps4_export_to_s3.py  --  the SageMaker end of the PS4 pipeline.

Paste as the final cell of the PS4 notebook (or `%run` it). It writes the run's
outputs to S3 in the layout cubic-mars-ps4-rds-loader reads, then drops a
manifest.json LAST. The manifest is the S3 trigger, so writing it last is what
guarantees the Lambda never fires against a half-written run.

    s3://<gold>/chicago/ml_outputs/ps4/<run_id>/
        device_daily.parquet         device x day, all history
        device_hourly.parquet        device x hour, recent window only
        spc_thresholds.parquet
        signal_summary.parquet
        station_anomaly.parquet
        model_leaderboard.parquet
        leakage_scan.parquet
        manifest.json                <-- written LAST; this is the trigger

WHAT YOU MUST BIND
    sdf              the scored Spark dataframe at device x hour grain
    leaderboard_df   pandas frame of the model bake-off
    SPC_THRESHOLDS   the dict already printed in the run log
    RUN_ID           e.g. "ps4_20260724_1841"
Everything else is derived here.

target_rate IS NOT OPTIONAL. The loader refuses a leaderboard without it, on
purpose: the 24-Jul run reported test AP 0.9994, which means nothing until you
know the base rate is 0.4796. A metric that flatters is worse than no metric.
"""
import json
import boto3
import pandas as pd
from pyspark.sql import functions as F

# ---------------------------------------------------------------- config ----
S3_BUCKET = "cubic-mars-pm-s3-datalake-dev-gold-170202974600"
PS4_ROOT  = "chicago/ml_outputs/ps4"
CITY_ID   = "CHI"
HOURLY_WINDOW_DAYS = 7          # must match the Lambda's HOURLY_WINDOW_DAYS

RUN_ID   = globals().get("RUN_ID") or f"ps4_{pd.Timestamp.utcnow():%Y%m%d_%H%M}"
RUN_PFX  = f"{PS4_ROOT}/{RUN_ID}"
RUN_URI  = f"s3://{S3_BUCKET}/{RUN_PFX}"
_s3      = boto3.client("s3")


def _put_parquet(pdf: pd.DataFrame, name: str) -> str:
    """One file per output. Single files keep the loader simple and the run
    self-describing; these are summaries, not the lake."""
    key = f"{RUN_PFX}/{name}"
    buf = pdf.to_parquet(index=False)
    _s3.put_object(Bucket=S3_BUCKET, Key=key, Body=buf)
    print(f"  {len(pdf):>9,} rows -> s3://{S3_BUCKET}/{key}")
    return name


outputs = {}
print(f"PS4 export -> {RUN_URI}")

# ------------------------------------------------------- 1. device x day ----
daily = (sdf
    .withColumn("transit_day", F.to_date("hour_bucket"))
    .groupBy("DEVICE_ID", "transit_day", "mars_device_category", "FACILITY_ID")
    .agg(F.count("*").alias("total_hours"),
         F.sum(F.col("is_anomaly").cast("int")).alias("anomaly_hours"),
         F.sum(F.col("spc_violation").cast("int")).alias("spc_violation_hours"),
         F.sum(F.col("if_anomaly").cast("int")).alias("if_anomaly_hours"),
         F.max("anomaly_score").alias("max_anomaly_score"),
         F.avg("anomaly_score").alias("avg_anomaly_score"))
    .withColumn("anomaly_rate", F.round(F.col("anomaly_hours") / F.col("total_hours"), 4))
    .withColumnRenamed("DEVICE_ID", "device_id")
    .withColumnRenamed("mars_device_category", "device_category")
    .withColumnRenamed("FACILITY_ID", "facility_id"))
outputs["ps4_device_daily"] = _put_parquet(daily.toPandas(), "device_daily.parquet")

# ------------------------------------------------------ 2. device x hour ----
# The gold table is 36.6M device-hours. Aurora is the SERVING store, not a second
# copy of the lake, so only the recent window goes across. The Lambda applies the
# same cut independently, so a wider export cannot quietly bloat the database.
cutoff = (pd.Timestamp.utcnow().normalize() - pd.Timedelta(days=HOURLY_WINDOW_DAYS))
hourly = (sdf
    .filter(F.col("hour_bucket") >= F.lit(str(cutoff)))
    .select(F.col("DEVICE_ID").alias("device_id"), "hour_bucket",
            F.col("mars_device_category").alias("device_category"),
            F.col("FACILITY_ID").alias("facility_id"),
            "event_count", "anomaly_score", "is_anomaly", "spc_violation", "if_anomaly"))
outputs["ps4_device_hourly"] = _put_parquet(hourly.toPandas(), "device_hourly.parquet")

# --------------------------------------------------------- 3. SPC limits ----
spc = pd.DataFrame([
    {"device_category": k, "mean_events": v["mean"], "ucl": v["ucl"], "lcl": v["lcl"]}
    for k, v in SPC_THRESHOLDS.items()])
outputs["ps4_spc_thresholds"] = _put_parquet(spc, "spc_thresholds.parquet")

# ----------------------------------------------------- 4. signal summary ----
sig = (sdf.groupBy(F.col("mars_device_category").alias("device_category"), "split_name")
    .agg(F.count("*").alias("n_hours"),
         F.sum(F.col("spc_violation").cast("int")).alias("spc_active"),
         F.sum(F.col("if_anomaly").cast("int")).alias("if_active"))
    .toPandas())
rows = []
for _, r in sig.iterrows():
    for name, active in (("spc", r["spc_active"]), ("pca_if", r["if_active"])):
        rows.append({"signal_name": name, "device_category": r["device_category"],
                     "split_name": r["split_name"], "n_hours": int(r["n_hours"]),
                     "n_active": int(active),
                     "activation_rate": round(float(active) / max(int(r["n_hours"]), 1), 4)})
outputs["ps4_signal_summary"] = _put_parquet(pd.DataFrame(rows), "signal_summary.parquet")

# ------------------------------------------------------- 5. per station -----
station = (sdf.groupBy(F.col("FACILITY_ID").alias("facility_id"),
                       F.col("mars_device_category").alias("device_category"))
    .agg(F.countDistinct("DEVICE_ID").alias("n_devices"),
         F.count("*").alias("total_hours"),
         F.sum(F.col("is_anomaly").cast("int")).alias("anomaly_hours"))
    .withColumn("anomaly_rate", F.round(F.col("anomaly_hours") / F.col("total_hours"), 4))
    .toPandas())
outputs["ps4_station_anomaly"] = _put_parquet(station, "station_anomaly.parquet")

# ------------------------------------------------------- 6. leaderboard -----
# base_rate is stamped on EVERY row so average precision can never be read
# without it, wherever the row ends up.
base_rate = float(sdf.select(F.avg(F.col("is_anomaly").cast("double"))).first()[0])
lb = leaderboard_df.copy()
lb["base_rate"] = round(base_rate, 4)
lb = lb.sort_values("test_ap", ascending=False).reset_index(drop=True)
lb["lb_rank"] = lb.index + 1
lb["is_champion"] = lb["lb_rank"] == 1
outputs["ps4_model_leaderboard"] = _put_parquet(lb, "model_leaderboard.parquet")

# ---------------------------------------------------- 7. leakage scan -------
# Solo AUC of each feature against the target. PS4 reported test AP 0.9994 on a
# 0.4796 base rate, and its target is DERIVED from the SPC / PCA / IF signals that
# also sit in the feature matrix -- so the model may be predicting something it was
# handed. Anything above ~0.95 solo is the label wearing a different name.
try:
    from pyspark.ml.evaluation import BinaryClassificationEvaluator
    ev = BinaryClassificationEvaluator(labelCol="is_anomaly", metricName="areaUnderROC")
    scan = []
    for feat in FEATURE_COLS:                       # bind: the training feature list
        try:
            d = sdf.select(F.col(feat).cast("double").alias("rawPrediction"),
                           F.col("is_anomaly").cast("double").alias("is_anomaly")).dropna()
            auc = ev.evaluate(d)
            scan.append({"feature_name": feat, "solo_auc": round(max(auc, 1 - auc), 4)})
        except Exception:
            continue
    if scan:
        outputs["ps4_leakage_scan"] = _put_parquet(
            pd.DataFrame(scan).sort_values("solo_auc", ascending=False), "leakage_scan.parquet")
        worst = max(s["solo_auc"] for s in scan)
        print(f"  leakage scan: highest solo AUC = {worst:.4f}"
              f"{'   <-- INVESTIGATE, this feature is close to being the label' if worst > 0.95 else ''}")
except Exception as e:
    print(f"  [warn] leakage scan skipped: {e}")

# --------------------------------------------------------- 8. manifest ------
# LAST. This object is the S3 trigger; writing it last means the Lambda can never
# fire against a partially written run.
manifest = {
    "run_id": RUN_ID,
    "city_id": CITY_ID,
    "run_ts": pd.Timestamp.utcnow().isoformat(),
    "run_kind": "train",
    "source_notebook": globals().get("NOTEBOOK_NAME", "PS4_anomaly_pyspark"),
    "gold_snapshot_s3": globals().get("GOLD_S3"),
    "computed_date": str(pd.Timestamp.utcnow().date()),
    "champion": str(lb.loc[0, "model"]),
    "target_col": "is_anomaly",
    "target_rate": round(base_rate, 4),
    "n_rows_total": int(sdf.count()),
    "n_features": len(globals().get("FEATURE_COLS", [])),
    "leakage_checked": "ps4_leakage_scan" in outputs,
    "notes": globals().get("RUN_NOTES", ""),
    "outputs": outputs,
}
_s3.put_object(Bucket=S3_BUCKET, Key=f"{RUN_PFX}/manifest.json",
               Body=json.dumps(manifest, indent=2).encode())
print(f"\nmanifest -> {RUN_URI}/manifest.json")
print(f"base rate {base_rate:.4f} | champion {manifest['champion']} | "
      f"leakage_checked={manifest['leakage_checked']}")
print("S3 ObjectCreated on the manifest now triggers cubic-mars-ps4-rds-loader.")
