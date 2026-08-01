# Databricks notebook source
# =============================================================================
# cross_wired_daily_job — Unified PS1–PS5 cross-wire export for Dispatcher → RDS
#
# Builds device × component × day rows with OOS, chargeable, station, serial,
# incident, PS2 cascade, PS4 daily anomaly roll-up, and PS5 component context.
#
# Prerequisites (run on schedule BEFORE this job, or ensure tables are fresh):
#   run_layer_gold → validate_gold → export_gold_to_s3
#   Optional: SageMaker PS1 CELL 24 → s3://<artifact_bucket>/chicago/device_ps1_cross_wired_daily/{gate|tvm|validator}/
#   Optional: PS4 PySpark CELL 19 → chicago/ps4/scored/
#   Optional: PS5 batch → chicago/ps5/scored/
#
# Widgets:
#   catalog              mars_dev
#   bucket               cubic-mars-pm-s3-datalake-dev-gold-170202974600
#   cross_wire_prefix    chicago/cross_wired
#   asof_date            YYYY-MM-DD (blank = latest transit_day in PS1 gold)
#   city_id              CHI
#   lookback_days        1  (only export this many latest transit_days)
#   register_delta       true  (also write mars_dev.gold.device_cross_wired_daily)
#
# Output:
#   s3://<bucket>/<cross_wire_prefix>/daily/asof=<date>/
#   s3://<bucket>/<cross_wire_prefix>/manifest/asof=<date>/manifest.json
#
# Spec: docs/architecture/Cross_Wire_Databricks_Daily_Job_Column_Manifest.md
# =============================================================================
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

dbutils: Any = globals().get("dbutils")
spark: Any = globals().get("spark")

from pyspark.sql import functions as F
from pyspark.sql import Window

# ---- widgets ----------------------------------------------------------------
dbutils.widgets.text("catalog", "mars_dev")
dbutils.widgets.text("bucket", "cubic-mars-pm-s3-datalake-dev-gold-170202974600")
dbutils.widgets.text("artifact_bucket", "cubic-mars-pm-s3-datalake-dev-artifacts-170202974600")
dbutils.widgets.text("cross_wire_prefix", "chicago/cross_wired")
dbutils.widgets.text("asof_date", "")
dbutils.widgets.text("city_id", "CHI")
dbutils.widgets.text("lookback_days", "1")
dbutils.widgets.dropdown("register_delta", "true", ["true", "false"])

catalog = dbutils.widgets.get("catalog").strip()
bucket = dbutils.widgets.get("bucket").strip()
artifact_bucket = dbutils.widgets.get("artifact_bucket").strip()
cross_wire_prefix = dbutils.widgets.get("cross_wire_prefix").strip().rstrip("/")
city_id = dbutils.widgets.get("city_id").strip().upper()
lookback_days = max(1, int(dbutils.widgets.get("lookback_days").strip() or "1"))
register_delta = dbutils.widgets.get("register_delta").strip().lower() == "true"
asof_date_param = dbutils.widgets.get("asof_date").strip()

spark.sql(f"USE CATALOG {catalog}")
spark.conf.set("spark.databricks.delta.formatCheck.enabled", "false")

RUN_TS = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
GOLD = f"{catalog}.gold"
SILVER = f"{catalog}.silver"


def _table_exists(name: str) -> bool:
    try:
        spark.table(name)
        return True
    except Exception:
        return False


def _cols(name: str) -> set[str]:
    return set(spark.table(name).columns)


def _pick(df_name: str, *candidates: str) -> list[str]:
    avail = _cols(df_name)
    return [c for c in candidates if c in avail]


# ---- resolve as-of date(s) ----------------------------------------------------
ps1_tbl = f"{GOLD}.device_ps1_daily"
if not _table_exists(ps1_tbl):
    raise RuntimeError(f"Missing {ps1_tbl} — run run_layer_gold first")

if asof_date_param:
    target_days = [asof_date_param]
else:
    latest = spark.table(ps1_tbl).agg(F.max("transit_day").alias("d")).collect()[0]["d"]
    if latest is None:
        raise RuntimeError("device_ps1_daily has no transit_day values")
    target_days = [
        r["transit_day"]
        for r in (
            spark.table(ps1_tbl)
            .select("transit_day")
            .distinct()
            .orderBy(F.desc("transit_day"))
            .limit(lookback_days)
            .collect()
        )
    ]

print(f"catalog={catalog}  city={city_id}  days={target_days}  run_ts={RUN_TS}")

# ---- device-day spine (PS1 gold) ----------------------------------------------
ps1_cols = _pick(
    ps1_tbl,
    "DEVICE_ID", "DEVICE_KEY", "transit_day", "mars_device_category",
    "FACILITY_ID", "FACILITY_NAME", "DEVICE_NAME",
    "hardware_oos_count", "chargeable_outage_count", "chargeable_outage_min",
    "oos_events_7d", "hardware_oos_events_7d", "outage_min_7d", "availability_pct_7d",
    "will_fail_3d", "is_chargeable",
    "incident_count_7d_past", "chargeable_count_7d_past", "days_since_last_incident",
    "TRANSIT_ARRAY_ID", "ARRAY_POSITION", "device_age_days",
)

spine = (
    spark.table(ps1_tbl)
    .filter(F.col("transit_day").isin(target_days))
    .select(*ps1_cols)
    .withColumn("city_id", F.lit(city_id))
    .withColumn(
        "device_type_dashboard",
        F.when(F.upper(F.col("mars_device_category")) == "TVM", F.lit("tvms"))
        .when(F.upper(F.col("mars_device_category")) == "GATE", F.lit("gates"))
        .when(F.upper(F.col("mars_device_category")) == "VALIDATOR", F.lit("validators"))
        .otherwise(F.lit("readers")),
    )
)

# OOS label alias when present in a future gold build; else derive proxy from counts
if "will_hardware_oos_3d" in _cols(ps1_tbl):
    spine = spine.withColumn("will_hardware_oos_3d", F.col("will_hardware_oos_3d"))
else:
    spine = spine.withColumn(
        "will_hardware_oos_3d",
        F.when(F.coalesce(F.col("hardware_oos_count"), F.lit(0)) > 0, F.lit(1)).otherwise(F.lit(0))
        if "hardware_oos_count" in ps1_cols
        else F.lit(None).cast("int"),
    )

spine = spine.withColumn(
    "is_hardware_oos",
    F.when(F.coalesce(F.col("hardware_oos_count"), F.lit(0)) > 0, F.lit(1)).otherwise(F.lit(0))
    if "hardware_oos_count" in ps1_cols
    else F.lit(0),
)

# ---- station enrich (dim_device) ----------------------------------------------
if _table_exists(f"{SILVER}.dim_device"):
    dd_cols = _pick(
        f"{SILVER}.dim_device",
        "DEVICE_ID", "OPERATOR_ID", "BUS_ID", "bus_device_flag", "STOP_POINT_ID",
    )
    dd = (
        spark.table(f"{SILVER}.dim_device")
        .filter(F.col("is_current") == True)  # noqa: E712
        .select(*dd_cols)
        .dropDuplicates(["DEVICE_ID"])
    )
    spine = spine.join(dd, on="DEVICE_ID", how="left")

if _table_exists(f"{SILVER}.dim_stop_point") and "STOP_POINT_ID" in spine.columns:
    sp = spark.table(f"{SILVER}.dim_stop_point").select(
        F.col("STOP_POINT_ID"),
        F.col("STOP_POINT_NAME").alias("STOP_POINT_NAME"),
    ).dropDuplicates(["STOP_POINT_ID"])
    spine = spine.join(sp, on="STOP_POINT_ID", how="left")

# ---- PS2 cascade (device-day) ------------------------------------------------
ps2_tbl = f"{GOLD}.device_ps2_chains"
if _table_exists(ps2_tbl):
    ps2_keep = _pick(
        ps2_tbl,
        "DEVICE_KEY", "transit_day",
        "is_coordinated_station_failure", "is_major_station_event",
        "station_devices_failed", "days_healthy_before_chain",
        "no_prior_failure_in_window", "chain_length", "chain_failure_count",
        "failure_acceleration_rate",
    )
    ps2_sig = spark.table(ps2_tbl).filter(F.col("transit_day").isin(target_days)).select(*ps2_keep)
    spine = spine.join(ps2_sig, on=["DEVICE_KEY", "transit_day"], how="left")

# ---- PS4 hourly → daily roll-up ---------------------------------------------
ps4_tbl = f"{GOLD}.device_ps4_hourly"
if _table_exists(ps4_tbl):
    ps4 = spark.table(ps4_tbl).filter(F.col("transit_day").isin(target_days))
    ps4_day = ps4.groupBy("DEVICE_ID", "transit_day").agg(
        F.sum(F.coalesce(F.col("ensemble_anomaly_flag"), F.lit(0))).alias("anomaly_hours_day"),
        F.max(F.coalesce(F.col("ensemble_anomaly_flag"), F.lit(0))).alias("ensemble_anomaly_flag_daily"),
        F.sum(F.coalesce(F.col("signal_active_count"), F.lit(0))).alias("signal_active_hours_day"),
    )
    spine = spine.join(ps4_day, on=["DEVICE_ID", "transit_day"], how="left")
else:
    spine = (
        spine
        .withColumn("anomaly_hours_day", F.lit(None).cast("long"))
        .withColumn("ensemble_anomaly_flag_daily", F.lit(None).cast("int"))
        .withColumn("signal_active_hours_day", F.lit(None).cast("long"))
    )

# ---- PS3 incidents aggregated to device-day ----------------------------------
ps3_tbl = f"{GOLD}.device_ps3_incident"
if _table_exists(ps3_tbl):
    ps3 = spark.table(ps3_tbl).filter(F.col("transit_day").isin(target_days))
    if "device_id" in ps3.columns and "DEVICE_ID" not in ps3.columns:
        ps3 = ps3.withColumnRenamed("device_id", "DEVICE_ID")
    ae_col = "AE_START_DTM" if "AE_START_DTM" in ps3.columns else "ae_start_dtm"
    w = Window.partitionBy("DEVICE_ID", "transit_day").orderBy(F.desc(F.col(ae_col)))
    ps3_latest = (
        ps3.withColumn("_rn", F.row_number().over(w))
        .filter(F.col("_rn") == 1)
        .select(
            "DEVICE_ID", "transit_day",
            F.col("availability_event_id"),
            F.col("SN_SYS_ID") if "SN_SYS_ID" in ps3.columns else F.lit(None).cast("string").alias("SN_SYS_ID"),
            F.col("AE_FAILURE_LEVEL").alias("AE_FAILURE_LEVEL") if "AE_FAILURE_LEVEL" in ps3.columns else F.lit(None).cast("int").alias("AE_FAILURE_LEVEL"),
            F.col("failure_level_label") if "failure_level_label" in ps3.columns else F.lit(None).cast("string").alias("failure_level_label"),
            F.col("derived_component_type") if "derived_component_type" in ps3.columns else F.lit(None).cast("string").alias("derived_component_type"),
        )
    )
    ps3_counts = ps3.groupBy("DEVICE_ID", "transit_day").agg(
        F.count("*").alias("incident_count_day"),
        F.max("AE_FAILURE_LEVEL").alias("max_failure_level_day") if "AE_FAILURE_LEVEL" in ps3.columns else F.lit(0).alias("max_failure_level_day"),
    )
    spine = (
        spine.join(ps3_counts, on=["DEVICE_ID", "transit_day"], how="left")
        .join(ps3_latest, on=["DEVICE_ID", "transit_day"], how="left")
    )

# ---- expand to component / serial grain --------------------------------------
hw_tbl = f"{SILVER}.hw_config_current"
if _table_exists(hw_tbl):
    hw = (
        spark.table(hw_tbl)
        .select(
            "DEVICE_KEY",
            F.col("COMPONENT_SERIAL_NBR"),
            F.col("COMPONENT_TYPE"),
            F.col("COMPONENT_PART_NBR"),
            F.col("COMPONENT_MANUFACTURER"),
        )
        .filter(F.col("DEVICE_KEY").isNotNull())
        .dropDuplicates(["DEVICE_KEY", "COMPONENT_SERIAL_NBR"])
    )
    cross = spine.join(hw, on="DEVICE_KEY", how="left")
else:
    cross = (
        spine
        .withColumn("COMPONENT_SERIAL_NBR", F.lit(None).cast("string"))
        .withColumn("COMPONENT_TYPE", F.lit(None).cast("string"))
        .withColumn("COMPONENT_PART_NBR", F.lit(None).cast("string"))
        .withColumn("COMPONENT_MANUFACTURER", F.lit(None).cast("string"))
    )

# ---- PS5 component reliability -----------------------------------------------
ps5_tbl = f"{GOLD}.device_ps5_component"
if _table_exists(ps5_tbl):
    ps5_keep = _pick(
        ps5_tbl,
        "DEVICE_KEY", "COMPONENT_SERIAL_NBR",
        "days_to_failure", "is_censored", "component_age_days",
        "COMPONENT_TYPE_NAME", "avg_rolling_mttr_30d_min", "total_failure_days_s28",
        "last_failure_date_s28", "avg_rolling_mttr_90d_min", "max_downtime_ever_min",
    )
    ps5_sig = spark.table(ps5_tbl).select(*ps5_keep).dropDuplicates(["DEVICE_KEY", "COMPONENT_SERIAL_NBR"])
    join_keys = [k for k in ["DEVICE_KEY", "COMPONENT_SERIAL_NBR"] if k in cross.columns and k in ps5_sig.columns]
    if len(join_keys) == 2:
        cross = cross.join(ps5_sig, on=join_keys, how="left")

# ---- optional PS1 SageMaker cross-wired scores from S3 (per device category) ---
ps1_xw_base = f"s3://{artifact_bucket}/chicago/device_ps1_cross_wired_daily"
ps1_xw_legacy_bases = [f"s3://{bucket}/chicago/gold/device_ps1_cross_wired_daily"]
ps1_xw_slugs = {"GATE": "gate", "TVM": "tvm", "VALIDATOR": "validator"}
xw_keep = [
    "DEVICE_KEY", "transit_day", "COMPONENT_SERIAL_NBR",
    "ps1_fail_prob", "ps1_predicted", "ps1_risk_tier", "threshold_used",
    "device_category", "is_prob_anomaly",
]
ps1_xw_parts = []
_missing_ps1_cats: list[str] = []
for _cat, _slug in ps1_xw_slugs.items():
    _path = f"{ps1_xw_base}/{_slug}/"
    try:
        _df = spark.read.parquet(_path)
        _cols = [c for c in xw_keep if c in _df.columns]
        if "DEVICE_KEY" not in _cols or "transit_day" not in _cols:
            print(f"[INFO] PS1 cross-wired {_cat}: missing join keys at {_path}")
            _missing_ps1_cats.append(_cat)
            continue
        _df = (
            _df.filter(F.col("transit_day").isin(target_days))
            .select(*_cols)
            .dropDuplicates([c for c in ["DEVICE_KEY", "transit_day", "COMPONENT_SERIAL_NBR"] if c in _cols])
        )
        if "device_category" not in _df.columns:
            _df = _df.withColumn("device_category", F.lit(_cat))
        ps1_xw_parts.append(_df)
        print(f"Joined PS1 cross-wired scores from {_path}")
    except Exception as exc:
        print(f"[INFO] PS1 cross-wired {_cat} not joined at {_path} ({exc})")
        _missing_ps1_cats.append(_cat)

# Legacy gold-bucket layout — backfill categories missing from artifacts path
if _missing_ps1_cats:
    for _legacy_base in ps1_xw_legacy_bases:
        for _cat in list(_missing_ps1_cats):
            _slug = ps1_xw_slugs[_cat]
            _slug_path = f"{_legacy_base}/{_slug}/"
            try:
                _df = spark.read.parquet(_slug_path)
                _cols = [c for c in xw_keep if c in _df.columns]
                if "DEVICE_KEY" not in _cols or "transit_day" not in _cols:
                    continue
                _df = (
                    _df.filter(F.col("transit_day").isin(target_days))
                    .select(*_cols)
                    .dropDuplicates([c for c in ["DEVICE_KEY", "transit_day", "COMPONENT_SERIAL_NBR"] if c in _cols])
                )
                if "device_category" not in _df.columns:
                    _df = _df.withColumn("device_category", F.lit(_cat))
                ps1_xw_parts.append(_df)
                _missing_ps1_cats.remove(_cat)
                print(f"Joined PS1 cross-wired {_cat} from legacy path {_slug_path}")
            except Exception:
                pass
        if _missing_ps1_cats:
            try:
                _legacy = spark.read.parquet(_legacy_base)
                _cols = [c for c in xw_keep if c in _legacy.columns]
                if "DEVICE_KEY" in _cols and "transit_day" in _cols and "device_category" in _legacy.columns:
                    _legacy = (
                        _legacy.filter(F.col("transit_day").isin(target_days))
                        .select(*_cols)
                        .dropDuplicates([c for c in ["DEVICE_KEY", "transit_day", "COMPONENT_SERIAL_NBR"] if c in _cols])
                    )
                    for _cat in list(_missing_ps1_cats):
                        _sub = _legacy.filter(F.upper(F.col("device_category")) == _cat)
                        if not _sub.isEmpty():
                            ps1_xw_parts.append(_sub)
                            _missing_ps1_cats.remove(_cat)
                            print(f"Joined PS1 cross-wired {_cat} from legacy flat path {_legacy_base}")
            except Exception as exc:
                print(f"[INFO] PS1 cross-wired legacy path not joined ({_legacy_base}: {exc})")

if ps1_xw_parts:
    ps1_xw = ps1_xw_parts[0]
    for _part in ps1_xw_parts[1:]:
        ps1_xw = ps1_xw.unionByName(_part, allowMissingColumns=True)
    _join_keys = [c for c in ["DEVICE_KEY", "transit_day", "COMPONENT_SERIAL_NBR"] if c in ps1_xw.columns]
    cross = cross.join(ps1_xw, on=_join_keys, how="left")

# ---- city-level KPI counts (diagram: total devices, serials, OOS, chargeable) -
day_kpis = cross.groupBy("transit_day", "mars_device_category").agg(
    F.countDistinct("DEVICE_ID").alias("total_devices_city"),
    F.countDistinct("COMPONENT_SERIAL_NBR").alias("total_serials_city"),
    F.countDistinct(F.when(F.col("is_hardware_oos") == 1, F.col("DEVICE_ID"))).alias("devices_with_oos_today"),
    F.countDistinct(
        F.when(F.coalesce(F.col("chargeable_outage_count"), F.lit(0)) > 0, F.col("DEVICE_ID"))
    ).alias("devices_chargeable_today"),
)
cross = cross.join(day_kpis, on=["transit_day", "mars_device_category"], how="left")

# ---- lineage columns ----------------------------------------------------------
cross = (
    cross
    .withColumn("asof_date", F.col("transit_day").cast("string"))
    .withColumn("run_ts", F.lit(RUN_TS))
    .withColumn("pipeline_version", F.lit("cross_wired_daily_v1"))
    .withColumn(
        "source_tables_json",
        F.lit(json.dumps([
            ps1_tbl, ps2_tbl if _table_exists(ps2_tbl) else None,
            ps4_tbl if _table_exists(ps4_tbl) else None,
            ps3_tbl if _table_exists(ps3_tbl) else None,
            ps5_tbl if _table_exists(ps5_tbl) else None,
            hw_tbl if _table_exists(hw_tbl) else None,
        ])),
    )
)

# ---- validation ---------------------------------------------------------------
row_count = cross.count()
dupes = (
    cross.groupBy("city_id", "DEVICE_ID", "DEVICE_KEY", "transit_day", "COMPONENT_SERIAL_NBR")
    .count()
    .filter(F.col("count") > 1)
    .count()
)
if dupes > 0:
    raise RuntimeError(f"Cross-wire grain violation: {dupes} duplicate key groups")
if row_count == 0:
    raise RuntimeError("Cross-wire output is empty — check target_days and gold tables")

print(f"Cross-wire rows: {row_count:,}  dupes: {dupes}")

# ---- write per asof_date partition to S3 --------------------------------------
manifest_days = []
for day in target_days:
    day_str = str(day)
    part = cross.filter(F.col("transit_day") == day)
    n = part.count()
    out_uri = f"s3://{bucket}/{cross_wire_prefix}/daily/asof={day_str}"
    for sub in [f"{out_uri}/_delta_log", out_uri]:
        try:
            dbutils.fs.rm(sub, recurse=True)
        except Exception:
            pass
    part.write.mode("overwrite").format("parquet").save(out_uri)
    verified = spark.read.parquet(out_uri).count()
    print(f"  wrote {out_uri}  rows={verified:,}")

    manifest = {
        "job": "cross_wired_daily",
        "city": city_id,
        "asof_date": day_str,
        "run_ts": RUN_TS,
        "pipeline_version": "cross_wired_daily_v1",
        "s3_uri": out_uri,
        "row_count": verified,
        "duplicate_key_groups": dupes,
        "rds_targets": [
            "failure_predictions", "anomalies", "anomaly_timeline",
            "ps5_reliability_estimates", "ps5_serial_reliability", "ps3_severity_predictions",
        ],
        "loader_hint": f"python load_cross_wired_to_rds.py --uri {out_uri} --asof {day_str} --city {city_id}",
        "schema_version": "1.0",
    }
    manifest_uri = f"s3://{bucket}/{cross_wire_prefix}/manifest/asof={day_str}/manifest.json"
    manifest_local = f"/tmp/cross_wired_manifest_{day_str}.json"
    with open(manifest_local, "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=2)
    dbutils.fs.cp(f"file:{manifest_local}", manifest_uri)
    print(f"  manifest -> {manifest_uri}")
    manifest_days.append(manifest)

# ---- optional Delta registration in Unity Catalog -----------------------------
delta_tbl = f"{GOLD}.device_cross_wired_daily"
if register_delta:
    (
        cross.write.format("delta")
        .mode("overwrite")
        .option("overwriteSchema", "true")
        .partitionBy("transit_day")
        .saveAsTable(delta_tbl)
    )
    print(f"Registered {delta_tbl}  partitions={target_days}")

print("\ncross_wired_daily_job complete")
for m in manifest_days:
    print(f"  {m['asof_date']}: {m['row_count']:,} rows -> {m['s3_uri']}")
