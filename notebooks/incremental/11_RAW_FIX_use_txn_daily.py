# Databricks notebook source
# MAGIC %md
# MAGIC # 11 — RAW FIX: `edw_use_transaction_daily`  (the one unreadable prefix)
# MAGIC
# MAGIC Probe 02 measured this as the ONLY raw prefix that cannot be read as one DataFrame:
# MAGIC `MIXED(load_YYYYMM | src_year=...)` under `.../edw/use_transaction_daily/`.
# MAGIC
# MAGIC Fix: read each sub-tree **separately** (each is internally consistent), align columns, union,
# MAGIC rewrite to a fresh `.../edw/use_transaction_daily_v2/` in one `year=/month=/day=` layout derived
# MAGIC from `TRANSIT_DAY_KEY`, verify row counts, then rebuild bronze from v2. **The old tree is not
# MAGIC touched** — delete it only after reconciliation, and even then prefer renaming.
# MAGIC
# MAGIC DRY_RUN=True inventories and counts; set False to write.

# COMMAND ----------
import datetime as _dt
from pyspark.sql import functions as F

DRY_RUN  = True
RAW      = "s3://cubic-mars-pm-s3-datalake-dev-raw-170202974600/chicago_ventra/edw/use_transaction_daily/"
RAW_V2   = "s3://cubic-mars-pm-s3-datalake-dev-raw-170202974600/chicago_ventra/edw/use_transaction_daily_v2/"
BRONZE   = "mars_dev.bronze.edw_use_transaction_daily"

kids = [x.path for x in dbutils.fs.ls(RAW) if not x.name.startswith(("_", "."))]
print(f"sub-trees under the mixed prefix ({len(kids)}):")
dfs, counts = [], {}
for p in kids:
    try:
        d = spark.read.parquet(p)
        counts[p] = d.count()
        dfs.append(d)
        print(f"  {p.split('use_transaction_daily/')[-1]:32s} rows={counts[p]:>12,} cols={len(d.columns)}")
    except Exception as e:
        print(f"  {p:60s} UNREADABLE ALONE: {str(e).splitlines()[0][:80]}")
total_old = sum(counts.values())
print(f"total across sub-trees: {total_old:,}")

# align on the union of business columns (case-insensitive), drop partition/audit artefacts
biz = []
for d in dfs:
    for c in d.columns:
        if c.lower() not in [x.lower() for x in biz] and c.lower() not in ("src_year", "year", "month", "day"):
            biz.append(c)
def align(d):
    have = {c.lower(): c for c in d.columns}
    return d.select(*[F.col(have[c.lower()]).alias(c) if c.lower() in have else F.lit(None).alias(c) for c in biz])
merged = None
for d in dfs:
    merged = align(d) if merged is None else merged.unionByName(align(d))
# de-duplicate on the grain (a day may exist in both a load_* batch and a src_year tree)
GRAIN = [c for c in ("TRANSIT_DAY_KEY", "DEVICE_ID", "REVENUE_OR_TEST") if c.lower() in [x.lower() for x in biz]]
before = merged.count(); merged = merged.dropDuplicates(GRAIN); after = merged.count()
print(f"union={before:,} -> dedup on {GRAIN} = {after:,}  (overlap removed: {before-after:,})")

if DRY_RUN:
    print("\nDRY_RUN — nothing written. Review the numbers above, then set DRY_RUN=False.")
else:
    k = F.col("TRANSIT_DAY_KEY").cast("long")
    (merged.withColumn("year", (k/10000).cast("int")).withColumn("month", ((k%10000)/100).cast("int"))
           .withColumn("day", (k%100).cast("int"))
           .withColumn("_ingest_ts", F.current_timestamp())
           .withColumn("_batch_id", F.lit(_dt.datetime.now().strftime("%Y%m%d_%H%M%S")))
           .withColumn("_source_system", F.lit("raw_layout_rebuild"))
           .write.mode("overwrite").partitionBy("year", "month", "day").parquet(RAW_V2))
    n_v2 = spark.read.parquet(RAW_V2).count()
    assert n_v2 == after, f"v2 count {n_v2:,} != dedup count {after:,} — DO NOT proceed"
    v = spark.sql(f"DESCRIBE HISTORY {BRONZE} LIMIT 1").first()["version"]
    print(f"bronze rollback point: RESTORE TABLE {BRONZE} TO VERSION AS OF {v}")
    (spark.read.parquet(RAW_V2).write.format("delta").mode("overwrite")
     .option("overwriteSchema", "true").saveAsTable(BRONZE))
    print(f"REBUILT: raw v2 {n_v2:,} rows -> bronze {spark.sql(f'SELECT COUNT(*) n FROM {BRONZE}').first()['n']:,}")
    print(f"Old tree untouched at {RAW} — remove ONLY after silver/gold reconcile, and record it in the contract.")
