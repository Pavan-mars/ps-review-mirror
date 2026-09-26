"""
ps4_device_daily_export.py  --  add after CELL 19 of the PS4 anomaly notebook.

THE PROBLEM THIS SOLVES
-----------------------
CELL 19 exports three feeds. Two of them cannot go into Aurora:

    anomalies   17,568,514 rows   ~2.6 GB in Aurora
    outliers    36,629,754 rows   ~5.5 GB in Aurora
    timeline        72,751 rows   ~11 MB      <- fine, already loading

The instinct is a bigger loader. It is the wrong instinct. Aurora is the SERVING
store: it answers "which devices should an engineer look at today", and that
question needs roughly 4,700 device-days, not 36.6 million device-hours. Copying
the lake into the serving tier makes every dashboard query slower and buys
nothing a reader can use.

So the fix is upstream: aggregate here, where Spark already has the data
distributed, and export the small result. Two new feeds, both bounded:

    device_daily     ~n_devices x n_days   anomalous hours per device per day
    outlier_bins     ~2,000 rows           the scatter, pre-bucketed

WHAT THE DASHBOARD ACTUALLY NEEDS, PANEL BY PANEL
-------------------------------------------------
    "which devices are worst"       -> device_daily, ordered by anomaly_hours
    "is it getting better"          -> timeline (already loading)
    "how do devices spread"         -> outlier_bins, a 2-D histogram
    "what does this device do"      -> device_daily filtered to one device
None of those needs an individual device-hour row.

A NOTE ON THE LABEL, WHICH MATTERS MORE THAN THE VOLUME
-------------------------------------------------------
anomaly_hours is 47.96% of total_hours. In the notebook:

    signal_active_count   := sum(signal_* columns)
    ensemble_anomaly_flag := (signal_active_count >= SIGNAL_THRESHOLD)   # =2

So the "anomaly" label is a deterministic function of the signal columns. If any
signal_* column or signal_active_count is in FEATURE_COLS, a tree model can
reconstruct the label exactly -- which is why spark_gbt reports test AP 0.9994
and spark_rf 0.9992, while spark_lr, unable to express a threshold rule, manages
only 0.9367. That gap between the tree models and the linear one IS the tell.

Aggregating does not fix that. It makes the volume tractable; the label still
needs either (a) signal_* and signal_active_count excluded from FEATURE_COLS, or
(b) a target the signals PREDICT rather than CONSTITUTE -- e.g. hardware OOS in
the next 24h, which is what PS1 already uses. Until then anomaly_rate below is
"share of hours with >=2 signals active", and the export labels it that way
rather than calling it an anomaly rate.
"""
from pyspark.sql import functions as F
from pyspark.sql import Window

# Reuses the names CELL 19 already defines: PS4_SCORED_S3, _asof_date, CITY_CODE,
# df_spark, TARGET, DEV_COL, SIGNAL_COLS, write_parquet_s3, _score.
_scored_base = f"{PS4_SCORED_S3}/asof={_asof_date}"

# ------------------------------------------------------- device_daily -------
# One row per (device, transit_day). This is the feed the dashboard ranks on.
_dd = (
    df_spark
    .withColumn("city_id", F.lit(CITY_CODE))
    .withColumn("device_type", F.upper(F.col(DEV_COL)))
    .groupBy("city_id", "device_type", F.col("DEVICE_ID").alias("device_id"),
             "transit_day")
    .agg(
        F.count(F.lit(1)).alias("total_hours"),
        F.sum(F.col(TARGET).cast("int")).alias("flagged_hours"),
        F.max(_score).alias("max_score"),
        F.avg(_score).alias("avg_score"),
        F.max("signal_active_count").alias("max_signals_active"),
        # Per-signal counts, so a reader can see WHICH detector fired rather than
        # only that something did. This is the column the old anomaly_type string
        # was trying to convey, at a grain that fits.
        *[F.sum(F.col(c).cast("int")).alias(f"n_{c}") for c in SIGNAL_COLS],
    )
    # Named flagged_rate, NOT anomaly_rate. It is the share of hours with
    # >= SIGNAL_THRESHOLD signals active. Calling it an anomaly rate would assert
    # something the 47.96% base rate does not support.
    .withColumn("flagged_rate",
                F.round(F.col("flagged_hours") / F.col("total_hours"), 4))
    .withColumn("asof_date", F.lit(_asof_date))
)

_dd_uri = f"{_scored_base}/device_daily/"
write_parquet_s3(_dd, _dd_uri)
_dd_n = _dd.count()
print(f"  device_daily rows={_dd_n:,} -> {_dd_uri}")

# ------------------------------------------------------- outlier_bins -------
# The scatter panel as a 2-D histogram. 40 x 40 cells covers the plot at screen
# resolution; shipping 36.6M points to draw ~1,600 visually distinct positions is
# waste at every layer -- S3 read, Aurora storage, API payload, browser render.
_NBINS = 40
if "if_score" in df_spark.columns:
    _x = F.coalesce(F.col("event_count_hour"), F.lit(0)).cast("double")
    _y = F.col("if_score").cast("double")
    _xmax = df_spark.agg(F.max(_x)).collect()[0][0] or 1.0
    _ymin, _ymax = df_spark.agg(F.min(_y), F.max(_y)).collect()[0]
    _ymin = float(_ymin if _ymin is not None else 0.0)
    _ymax = float(_ymax if _ymax is not None else 1.0)
    _yspan = (_ymax - _ymin) or 1.0

    _bins = (
        df_spark.filter(F.col("if_score").isNotNull())
        .withColumn("city_id", F.lit(CITY_CODE))
        .withColumn("device_type", F.upper(F.col(DEV_COL)))
        .withColumn("x_bin", F.least(F.lit(_NBINS - 1),
                                     F.floor(_x / F.lit(float(_xmax)) * _NBINS)).cast("int"))
        .withColumn("y_bin", F.least(F.lit(_NBINS - 1),
                                     F.floor((_y - F.lit(_ymin)) / F.lit(_yspan) * _NBINS)).cast("int"))
        .groupBy("city_id", "device_type", "x_bin", "y_bin")
        .agg(
            F.count(F.lit(1)).alias("n_points"),
            F.countDistinct("DEVICE_ID").alias("n_devices"),
            F.avg(_x).alias("x_mid"),
            F.avg(_y).alias("y_mid"),
            F.sum(F.col(TARGET).cast("int")).alias("n_flagged"),
        )
        .withColumn("asof_date", F.lit(_asof_date))
        .withColumn("x_scale_max", F.lit(float(_xmax)))
        .withColumn("y_scale_min", F.lit(_ymin))
        .withColumn("y_scale_max", F.lit(_ymax))
    )
    _bins_uri = f"{_scored_base}/outlier_bins/"
    write_parquet_s3(_bins, _bins_uri)
    print(f"  outlier_bins rows={_bins.count():,} -> {_bins_uri}")
else:
    _bins_uri = None
    print("  [INFO] outlier_bins skipped (if_score absent)")

# ------------------------------------------------- extend the manifest ------
# The manifest is the trigger, so it must be rewritten LAST and must now name the
# two new feeds. The loader reads rds_targets to decide what to pull.
import json as _json

_mkey = f"{PS4_S3_PREFIX}/manifest/asof={_asof_date}/manifest.json"
_cur = _json.loads(
    boto3.client("s3").get_object(Bucket=ARTIFACT_BUCKET, Key=_mkey)["Body"].read())

_cur["paths"]["device_daily"] = _dd_uri
_cur["paths"]["outlier_bins"] = _bins_uri
_cur["counts"]["device_daily_rows"] = int(_dd_n)
# rds_targets is what the loader honours. anomalies and outliers come OUT: they
# stay in S3 for anyone doing lake-side analysis, they simply do not go to Aurora.
_cur["rds_targets"] = ["anomaly_timeline", "device_daily", "outlier_bins"]
_cur["label_caveat"] = (
    "ensemble_anomaly_flag = (signal_active_count >= 2), a deterministic function "
    "of the signal_* features. flagged_rate is the share of hours with >=2 signals "
    "active, not a validated anomaly rate. Base rate 0.4796.")

boto3.client("s3").put_object(
    Bucket=ARTIFACT_BUCKET, Key=_mkey,
    Body=_json.dumps(_cur, indent=2, default=str).encode())

print(f"\nmanifest updated -> s3://{ARTIFACT_BUCKET}/{_mkey}")
print(f"  rds_targets: {_cur['rds_targets']}")
print("  anomalies / outliers remain in S3 for lake-side work, but are no longer "
      "Aurora targets.")
