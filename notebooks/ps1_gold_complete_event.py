# Databricks notebook source
# =============================================================================
# ps1_gold_complete_event — emit "Gold Layer Complete" to EventBridge
#
# THE FINAL TASK of the daily Databricks job. Run it after
#   run_layer_silver -> run_layer_gold -> validate_gold -> export_gold_to_s3
# have all succeeded. It is the trigger for PS1 daily scoring.
#
# WHY PutEvents AND NOT S3 -> EventBridge
# --------------------------------------
# Enabling S3 event notifications requires put-bucket-notification-configuration,
# which REPLACES the bucket's entire notification document. The gold bucket
# already routes to the ps1-cross-wired-push Lambda. A naive put deletes that
# notification, returns success, prints nothing, and the first symptom is a load
# that silently stops arriving days later.
#
# PutEvents touches NO shared AWS configuration. It is undone by deleting one
# EventBridge rule. It also signals SEMANTIC completion -- "the gold layer is
# finished for 2026-08-16" -- rather than "an object appeared", which fires on
# the first file rather than the last.
#
# WHY THE PAYLOAD CARRIES asof_date
# ---------------------------------
# So that nothing downstream has to guess it. cross_wired_daily_job.py currently
# re-derives "latest transit_day" independently; two components separately
# guessing the same fact is a race. One component decides and tells the others.
#
# IAM: the Databricks instance profile needs events:PutEvents on the default bus.
#      Nothing else. See the policy at the foot of this file.
# =============================================================================
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

dbutils: Any = globals().get("dbutils")
spark: Any = globals().get("spark")

import boto3
from pyspark.sql import functions as F

# ---- widgets ----------------------------------------------------------------
dbutils.widgets.text("catalog", "mars_dev")
dbutils.widgets.text("city_id", "CHI")
dbutils.widgets.text("asof_date", "")          # blank = latest transit_day in gold
dbutils.widgets.text("region", "us-east-1")
dbutils.widgets.text("event_bus", "default")
dbutils.widgets.dropdown("dry_run", "false", ["true", "false"])

catalog = dbutils.widgets.get("catalog").strip()
city_id = dbutils.widgets.get("city_id").strip().upper()
region = dbutils.widgets.get("region").strip()
bus = dbutils.widgets.get("event_bus").strip()
dry_run = dbutils.widgets.get("dry_run").strip().lower() == "true"
asof_param = dbutils.widgets.get("asof_date").strip()

spark.sql(f"USE CATALOG {catalog}")
GOLD = f"{catalog}.gold"

# The five gold tables run_layer_gold builds. PS1's spine is first.
GOLD_TABLES = [
    "device_ps1_daily",
    "device_ps2_chains",
    "device_ps3_incident",
    "device_ps4_hourly",
    "device_ps5_component",
]

# ---- resolve the as-of date -------------------------------------------------
if asof_param:
    asof_date = asof_param
else:
    latest = (
        spark.table(f"{GOLD}.device_ps1_daily")
        .agg(F.max("transit_day").alias("d"))
        .collect()[0]["d"]
    )
    if latest is None:
        raise RuntimeError(
            "device_ps1_daily has no transit_day values. The gold build did not "
            "produce data, so there is nothing to signal completion of. Refusing "
            "to emit a 'complete' event for an empty layer."
        )
    asof_date = str(latest)

# ---- verify the layer really is complete before saying so -------------------
# An event that says "complete" when a table is missing or empty is worse than
# no event: it starts the scoring chain against data that is not there, and the
# failure surfaces three steps downstream where its cause is unrecognisable.
row_counts: dict[str, int] = {}
missing: list[str] = []
empty: list[str] = []

for t in GOLD_TABLES:
    full = f"{GOLD}.{t}"
    try:
        n = spark.table(full).filter(F.col("transit_day") == asof_date).count()
    except Exception as exc:                       # noqa: BLE001
        print(f"[MISSING] {full}: {exc}")
        missing.append(t)
        continue
    row_counts[t] = int(n)
    if n == 0:
        empty.append(t)
    print(f"  {t:<24} rows for {asof_date}: {n:,}")

if missing:
    raise RuntimeError(
        f"Gold tables missing entirely: {missing}. Not emitting a completion "
        f"event -- the layer is not complete."
    )

# PS1 cannot score without its own spine. The other four being empty is a
# problem for their own problem statements, and is reported, not fatal here.
if "device_ps1_daily" in empty:
    raise RuntimeError(
        f"{GOLD}.device_ps1_daily has ZERO rows for {asof_date}. PS1 scoring "
        f"would run against nothing and, because an empty filter raises no "
        f"exception downstream, would look like a successful run producing "
        f"NULL predictions. Refusing to emit the event."
    )
if empty:
    print(f"[WARN] empty for {asof_date} (not PS1's spine, so not fatal here): {empty}")

# ---- emit --------------------------------------------------------------------
detail = {
    "city": city_id,
    "asof_date": asof_date,
    "catalog": catalog,
    "tables": GOLD_TABLES,
    "row_counts": row_counts,
    "empty_tables": empty,
    "emitted_at_utc": datetime.now(timezone.utc).isoformat(),
    "pipeline": "raw -> bronze -> silver -> gold",
    "schema_version": "1.0",
}

entry = {
    "Source": "cubic.mars.databricks",
    "DetailType": "Gold Layer Complete",
    "Detail": json.dumps(detail),
    "EventBusName": bus,
}

print("\nevent to publish:")
print(json.dumps({**entry, "Detail": detail}, indent=2, default=str))

if dry_run:
    print("\nDRY RUN — nothing published. Set dry_run=false to emit.")
else:
    resp = boto3.client("events", region_name=region).put_events(Entries=[entry])
    failed = resp.get("FailedEntryCount", 0)
    if failed:
        # PutEvents returns 200 even when an entry fails. Checking
        # FailedEntryCount is the only way to know it landed -- another instance
        # of "the call succeeded" not meaning "the thing happened".
        raise RuntimeError(f"PutEvents reported {failed} failed entries: {resp}")
    print(f"\npublished — EventId {resp['Entries'][0].get('EventId')}")
    print(f"matches EventBridge rule: cubic-mars-ps1-gold-complete")

# =============================================================================
# IAM POLICY the Databricks instance profile needs (attach, do not replace):
#
#   {
#     "Version": "2012-10-17",
#     "Statement": [{
#       "Effect": "Allow",
#       "Action": "events:PutEvents",
#       "Resource": "arn:aws:events:us-east-1:170202974600:event-bus/default"
#     }]
#   }
#
# NOTE: `iam put-role-policy` REPLACES the named inline policy. Attach this as
# a NEW policy name, or GET the existing document, merge, and PUT the merged
# version. Do not overwrite an existing policy blind.
#
# ROLLBACK: set dry_run=true, or remove this task from the job. Nothing
# downstream breaks -- the EventBridge rule simply never fires, and the
# watchdog (cubic-mars-ps1-watchdog) reports the absence, which is the
# behaviour we want.
# =============================================================================
