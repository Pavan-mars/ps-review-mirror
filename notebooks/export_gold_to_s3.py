# Databricks notebook source
# =============================================================================
# export_gold_to_s3 — Export all 5 gold Delta tables to S3 for SageMaker
#
# Run this in Databricks AFTER building all gold tables (PS1-PS5).
# SageMaker notebooks read from S3 using the deltalake Python library;
# they cannot read directly from Unity Catalog managed storage.
#
# Widgets:
#   catalog     mars_dev (default)     source Unity Catalog
#   bucket      cubic-mars-pm-s3-datalake-dev-gold-170202974600
#   prefix      chicago/gold           S3 key prefix (no trailing slash)
#   mode        overwrite | append     Delta write mode (default: overwrite)
# =============================================================================

dbutils.widgets.text("catalog", "mars_dev")
dbutils.widgets.text("bucket",  "cubic-mars-pm-s3-datalake-dev-gold-170202974600")
dbutils.widgets.text("prefix",  "chicago/gold")
dbutils.widgets.text("mode",    "overwrite")

catalog = dbutils.widgets.get("catalog").strip()
bucket  = dbutils.widgets.get("bucket").strip()
prefix  = dbutils.widgets.get("prefix").strip().rstrip("/")
mode    = dbutils.widgets.get("mode").strip()

assert mode in ("overwrite", "append"), f"mode must be overwrite|append, got {mode!r}"

# Export as Parquet (not Delta) so SageMaker can read via pd.read_parquet().
# Databricks 14+ writes Delta with deletionVectors enabled by default;
# the open-source deltalake Python library in SageMaker does not support
# that protocol feature. Parquet has no protocol versioning and is
# universally readable by pandas / PyArrow / SageMaker.
#
# Each table is exported to: s3://<bucket>/<prefix>/<table>/
# SageMaker reads the whole folder: pd.read_parquet(GOLD_S3)

# Disable Delta format-check so Spark does not reject a Parquet write to a
# path that previously contained a _delta_log (S3 eventual consistency can
# make deleted Delta files appear present to the JVM even after dbutils.fs.rm).
spark.conf.set("spark.databricks.delta.formatCheck.enabled", "false")

GOLD_TABLES = [
    "device_ps1_daily",
    "device_ps2_chains",
    "device_ps3_incident",
    "device_ps4_hourly",
    "device_ps5_component",
]

print(f"catalog : {catalog}")
print(f"bucket  : {bucket}")
print(f"prefix  : {prefix}")
print(f"mode    : {mode}")
print(f"tables  : {len(GOLD_TABLES)}")
print()

for table in GOLD_TABLES:
    src  = f"{catalog}.gold.{table}"
    dest = f"s3://{bucket}/{prefix}/{table}"

    # Remove any existing files (Delta or Parquet) before writing fresh.
    # Explicitly remove _delta_log first — Spark rejects Parquet writes to
    # a path that still has a Delta transaction log, even after a full rm,
    # because S3 list operations can return stale results momentarily.
    for subpath in [f"{dest}/_delta_log", dest]:
        try:
            dbutils.fs.rm(subpath, recurse=True)
        except Exception:
            pass
    print(f"  cleared {dest}")

    print(f"  exporting {src}")
    print(f"         -> {dest}")
    (spark.table(src)
          .write
          .format("parquet")
          .mode("overwrite")
          .save(dest))
    row_count = spark.read.format("parquet").load(dest).count()
    print(f"     done   {row_count:,} rows verified in S3\n")

print("export_gold_to_s3 complete — all 5 tables written to S3")
