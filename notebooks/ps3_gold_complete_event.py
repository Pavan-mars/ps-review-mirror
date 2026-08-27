# Databricks notebook source
# =============================================================================
# ps3_gold_complete_event — emit "PS3 Gold Export Complete" to EventBridge
#
# THE FINAL TASK of the PS3 daily Databricks pre-score chain. Run after
#   ps3_daily_incremental (or run_layer_gold) -> export_gold_ps3_incremental_to_s3
# have succeeded. This is the trigger for PS3 daily batch scoring on AWS.
#
# WHY A SEPARATE DetailType FROM ps1_gold_complete_event
# ------------------------------------------------------
# PS1 and PS3 can run on different cadences during cutover. A PS3-only export
# must not start the PS1 Processing chain, and vice versa. Both events share
# the same bus and IAM pattern; Step Functions listens on each DetailType.
#
# WHY PutEvents AND NOT S3 -> EventBridge
# Same rationale as ps1_gold_complete_event.py — never replace bucket notifications.
# =============================================================================
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

dbutils: Any = globals().get("dbutils")
spark: Any = globals().get("spark")

import boto3
from pyspark.sql import functions as F

dbutils.widgets.text("catalog", "mars_dev")
dbutils.widgets.text("city_id", "CHI")
dbutils.widgets.text("asof_date", "")
dbutils.widgets.text("incr_s3_uri", "")   # optional; from export task taskValues
dbutils.widgets.text("bucket", "cubic-mars-pm-s3-datalake-dev-gold-170202974600")
dbutils.widgets.text("region", "us-east-1")
dbutils.widgets.text("event_bus", "default")
dbutils.widgets.dropdown("dry_run", "false", ["true", "false"])

catalog = dbutils.widgets.get("catalog").strip()
city_id = dbutils.widgets.get("city_id").strip().upper()
region = dbutils.widgets.get("region").strip()
bus = dbutils.widgets.get("event_bus").strip()
dry_run = dbutils.widgets.get("dry_run").strip().lower() == "true"
asof_param = dbutils.widgets.get("asof_date").strip()
incr_uri = dbutils.widgets.get("incr_s3_uri").strip()

spark.sql(f"USE CATALOG {catalog}")
GOLD = f"{catalog}.gold"
PS3_TABLE = "device_ps3_incident"

if asof_param:
    asof_date = asof_param
else:
    latest = (
        spark.table(f"{GOLD}.{PS3_TABLE}")
        .agg(F.max("transit_day").alias("d"))
        .collect()[0]["d"]
    )
    if latest is None:
        raise RuntimeError(
            f"{GOLD}.{PS3_TABLE} has no transit_day values. Refusing to emit "
            f"a completion event for an empty PS3 gold table."
        )
    asof_date = str(latest)

full = f"{GOLD}.{PS3_TABLE}"
try:
    n_asof = spark.table(full).filter(F.col("transit_day") == asof_date).count()
    n_total = spark.table(full).count()
except Exception as exc:  # noqa: BLE001
    raise RuntimeError(f"{full} missing or unreadable: {exc}") from exc

print(f"  {PS3_TABLE:<24} rows for {asof_date}: {n_asof:,}  (table total: {n_total:,})")

if n_asof == 0:
    raise RuntimeError(
        f"{full} has ZERO rows for {asof_date}. PS3 batch scoring would run "
        f"against nothing and look like a successful empty run. Refusing to emit."
    )

if not incr_uri:
    bucket = dbutils.widgets.get("bucket").strip()
    # Default to the JSONL scoring feed: the Batch Transform reads JSON lines,
    # not the parquet slice (execution 53495197 proved it the hard way).
    incr_uri = f"s3://{bucket}/chicago/gold/device_ps3_incident_incr_jsonl/asof={asof_date}"

detail = {
    "city": city_id,
    "asof_date": asof_date,
    "catalog": catalog,
    "table": PS3_TABLE,
    "row_count_asof": int(n_asof),
    "row_count_total": int(n_total),
    "incr_s3_uri": incr_uri,
    "emitted_at_utc": datetime.now(timezone.utc).isoformat(),
    "pipeline": "ps3 silver+gold -> S3 export",
    "schema_version": "1.0",
}

entry = {
    "Source": "cubic.mars.databricks",
    "DetailType": "PS3 Gold Export Complete",
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
        raise RuntimeError(f"PutEvents reported {failed} failed entries: {resp}")
    print(f"\npublished — EventId {resp['Entries'][0].get('EventId')}")
    print("matches EventBridge rule: cubic-mars-ps3-gold-export-complete")
