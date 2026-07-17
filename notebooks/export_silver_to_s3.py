# Databricks notebook source
# =============================================================================
# export_silver_to_s3 — Export all silver Delta tables to S3 for SageMaker
#
# Run this in Databricks after building/refreshing silver tables.
# Writes Parquet (not Delta) to the gold bucket under the chicago/silver/
# prefix so SageMaker notebooks can read them via pd.read_parquet().
#
# Destination pattern:
#   s3://cubic-mars-pm-s3-datalake-dev-gold-170202974600/chicago/silver/<table>
#
# Widgets:
#   catalog     mars_dev (default)
#   bucket      cubic-mars-pm-s3-datalake-dev-gold-170202974600
#   prefix      chicago/silver
#   mode        overwrite | append
#   skip_errors true | false  — continue on single-table failure
# =============================================================================
from typing import Any

dbutils: Any = globals().get("dbutils")
spark: Any   = globals().get("spark")

dbutils.widgets.text("catalog",      "mars_dev")
dbutils.widgets.text("bucket",       "cubic-mars-pm-s3-datalake-dev-gold-170202974600")
dbutils.widgets.text("prefix",       "chicago/silver")
dbutils.widgets.text("mode",         "overwrite")
dbutils.widgets.text("skip_errors",  "true")

catalog      = dbutils.widgets.get("catalog").strip()
bucket       = dbutils.widgets.get("bucket").strip()
prefix       = dbutils.widgets.get("prefix").strip().rstrip("/")
mode         = dbutils.widgets.get("mode").strip()
skip_errors  = dbutils.widgets.get("skip_errors").strip().lower() == "true"

assert mode in ("overwrite", "append"), f"mode must be overwrite|append, got {mode!r}"

# Write Parquet so SageMaker can use pd.read_parquet() without the deltalake lib.
# Disable Delta format-check so Spark accepts a Parquet write to a path that
# previously held a _delta_log (stale S3 list results can otherwise block the write).
spark.conf.set("spark.databricks.delta.formatCheck.enabled", "false")

SILVER_TABLES = [
    "device_event_enriched",
    "device_failures",               # S26 (2026-07-17) — hardware OOS events, TVM/GATE/VALIDATOR
    "device_incident_features_daily",
    "device_mttr",                   # S28 (2026-07-17) — rolling MTTR per device-failure-day
    "device_outage",
    "device_survival_intervals",     # S29 (2026-07-17) — failure-free intervals (survival analysis)
    "device_uptime_intervals",
    "dim_device",
    "dim_event_matrix",
    "dim_event_type",
    "dim_facility",
    "dim_failure_level",
    "dim_stop_point",
    "hw_config_current",
    "incident_history",
    "incident_root_cause",
    "incident_task_ci_link",         # S25 — VALIDATOR incident signal via servicenow_task_ci join
    "kpi_avail_enriched",
    "kpi_daily",
    "kpi_monthly_benchmark",
    "maintenance_ledger",
    "metric_daily",
    "metric_hourly",
    "read_tap_daily",
    "station_network_daily",         # S27 (2026-07-17) — station co-failure signal for PS2
    "tap_event_daily",
    "tvm_sale_daily",
    "usage_lifecycle_daily",
    "use_revenue_daily",
]

print(f"catalog     : {catalog}")
print(f"bucket      : {bucket}")
print(f"prefix      : {prefix}")
print(f"mode        : {mode}")
print(f"skip_errors : {skip_errors}")
print(f"tables      : {len(SILVER_TABLES)}")
print()

results = []

for i, table in enumerate(SILVER_TABLES, 1):
    src  = f"{catalog}.silver.{table}"
    dest = f"s3://{bucket}/{prefix}/{table}"
    print(f"[{i:02d}/{len(SILVER_TABLES)}] {src}")
    print(f"           -> {dest}")

    try:
        # Clear any existing Delta or Parquet files at the destination.
        # Remove _delta_log first — Spark rejects a Parquet write to a path
        # that still has a transaction log, even momentarily after a full rm.
        for subpath in [f"{dest}/_delta_log", dest]:
            try:
                dbutils.fs.rm(subpath, recurse=True)
            except Exception:
                pass

        (spark.table(src)
              .write
              .format("parquet")
              .mode("overwrite")
              .save(dest))

        row_count = spark.read.format("parquet").load(dest).count()
        print(f"           OK  {row_count:,} rows\n")
        results.append({"table": table, "status": "OK", "rows": row_count, "error": None})

    except Exception as exc:
        msg = str(exc)[:200]
        print(f"           FAIL: {msg}\n")
        results.append({"table": table, "status": "FAIL", "rows": None, "error": msg})
        if not skip_errors:
            raise

# ── Summary ───────────────────────────────────────────────────────────────────
ok   = [r for r in results if r["status"] == "OK"]
fail = [r for r in results if r["status"] == "FAIL"]

print("=" * 65)
print(f"export_silver_to_s3 complete — {len(ok)}/{len(SILVER_TABLES)} tables exported")
print("=" * 65)

if ok:
    print(f"\n{'Table':<40} {'Rows':>12}")
    print("-" * 54)
    for r in ok:
        print(f"  {r['table']:<38} {r['rows']:>12,}")
    total_rows = sum(r["rows"] for r in ok)
    print("-" * 54)
    print(f"  {'TOTAL':<38} {total_rows:>12,}")

if fail:
    print(f"\nFAILED ({len(fail)}):")
    for r in fail:
        print(f"  {r['table']}: {r['error']}")

print(f"\nSageMaker path prefix: s3://{bucket}/{prefix}/<table_name>")
print("Read in SageMaker:  pd.read_parquet(f's3://{bucket}/{prefix}/<table>', storage_options={'client_kwargs': {'region_name': 'us-east-1'}})")
