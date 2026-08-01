"""
export_device_serial_daily.py  --  the Databricks end of the daily dimension refresh.

Schedule this as a Databricks job. It writes the device <-> serial <-> component
map to S3 in the layout cubic-mars-dim-loader reads, then drops manifest.json
LAST so the Lambda can never fire against a half-written folder.

    s3://<gold>/chicago/dim/device_serial/<yyyy-mm-dd>/
        device_serial.parquet
        device_event_totals.parquet   (optional; see the grain caveat below)
        manifest.json                 <-- written LAST; this is the trigger

THE QUERY, AND WHY IT IS SHAPED THIS WAY
----------------------------------------
The 27-Jul extract repeated each device's event totals on EVERY serial row --
1..7 rows per device. Summing those rows inflates the fleet total 2.60x
(103,927,823 against a true 39,969,550). Two separate outputs at two separate
grains removes the trap entirely:

    device_serial        one row per (device, serial)  -- NO additive measures
    device_event_totals  one row per device            -- the counts live here

A dashboard can then join the map freely without ever fanning a count out.

THE GRAIN CAVEAT ON THE COUNTS
------------------------------
total_hardware_oos_events in the source is a count of FAULT RECORDS, not
out-of-service occurrences: the median device shows ~8 per day and the worst TVM
~109 per day. Until that is resolved against an availability-event definition,
the counts load with grain_note attached and must not be labelled "OOS events"
on any screen. Set EXPORT_TOTALS = False to ship the map alone.
"""
from datetime import date
from pyspark.sql import functions as F

S3_BUCKET = "cubic-mars-pm-s3-datalake-dev-gold-170202974600"
DIM_ROOT  = "chicago/dim/device_serial"
CITY_ID   = "CHI"
AS_OF     = str(date.today())
OUT       = f"s3://{S3_BUCKET}/{DIM_ROOT}/{AS_OF}"

EXPORT_TOTALS = True     # flip to False to publish the map without the counts

# ---------------------------------------------------------------- the map ----
# hw_config_current is the CURRENT hardware configuration: one row per
# device-component, carrying the serial, the component description and its age.
# It is the same table whose join the PS1 v3 notebooks skipped on a bad key
# (COMPONENT_TYPE, which does not exist -- the column is COMPONENT_DESCRIPTION),
# which is why PS1 lost facility, component and age in the first place.
serial_map = (
    spark.table("mars_dev.silver.hw_config_current")
         .where(F.col("city_id") == F.lit(CITY_ID))
         .where(F.col("DEVICE_ID").isNotNull() & F.col("COMPONENT_SERIAL_NBR").isNotNull())
         .select(
             F.col("DEVICE_ID").alias("device_id"),
             F.col("COMPONENT_SERIAL_NBR").cast("string").alias("serial_id"),
             F.col("mars_device_category"),
             F.col("COMPONENT_DESCRIPTION").alias("component_description"),
             F.col("component_age_days").cast("int").alias("component_age_days"))
         # one row per (device, serial). LAST_REPORTED_DTM breaks ties so a
         # re-reported component does not appear twice.
         .dropDuplicates(["device_id", "serial_id"]))

serial_map.coalesce(1).write.mode("overwrite").parquet(f"{OUT}/device_serial.parquet")
n_rows = serial_map.count()
n_dev  = serial_map.select("device_id").distinct().count()
n_ser  = serial_map.select("serial_id").distinct().count()
print(f"device_serial: {n_rows:,} rows | {n_dev:,} devices | {n_ser:,} distinct serials")

# ------------------------------------------------------- the device totals ----
# DEVICE GRAIN. Aggregated here, not repeated per serial, so nothing downstream
# can double count. COUNT(DISTINCT EVENT_ID) rather than COUNT(*) because the
# source holds several rows per event.
totals_file = None
if EXPORT_TOTALS:
    totals = (
        spark.table("mars_dev.silver.device_outage")          # <-- confirm this source
             .groupBy(F.col("DEVICE_ID").alias("device_id"),
                      F.col("mars_device_category"))
             .agg(F.countDistinct("EVENT_ID").alias("total_hardware_oos_events"),
                  F.countDistinct(F.when(F.col("is_chargeable"), F.col("EVENT_ID")))
                   .alias("total_chargeable_events")))
    totals.coalesce(1).write.mode("overwrite").parquet(f"{OUT}/device_event_totals.parquet")
    totals_file = "device_event_totals.parquet"
    print(f"device_event_totals: {totals.count():,} devices")

# ------------------------------------------------------------- manifest ------
# LAST. This object is the S3 trigger.
import json, boto3
manifest = {
    "city_id": CITY_ID,
    "as_of_date": AS_OF,
    "device_serial": "device_serial.parquet",
    "device_event_totals": totals_file,
    "source_table": "mars_dev.silver.hw_config_current",
    "period_start": "2023-07-01",
    "period_end": AS_OF,
    "grain_note": ("device_event_totals counts DISTINCT EVENT_ID from device_outage; "
                   "confirm this equals an out-of-service occurrence before labelling it OOS"),
    "n_rows": n_rows, "n_devices": n_dev, "n_serials": n_ser,
}
boto3.client("s3").put_object(
    Bucket=S3_BUCKET, Key=f"{DIM_ROOT}/{AS_OF}/manifest.json",
    Body=json.dumps(manifest, indent=2).encode())
print(f"manifest -> {OUT}/manifest.json")
print("S3 ObjectCreated on the manifest now triggers cubic-mars-dim-loader.")
