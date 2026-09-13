# Databricks notebook source
# MAGIC %md
# MAGIC # ingest_use_txn_daily — USE_TRANSACTION → device-day revenue aggregate
# MAGIC
# MAGIC Notebook conversion of `notebooks/ingestion/ingest_use_txn_daily.py`. **Logic unchanged** —
# MAGIC only split into cells so the config can be checked before anything is written.
# MAGIC
# MAGIC ## READ THIS BEFORE RUN ALL
# MAGIC
# MAGIC `mode = "overwrite"` runs **`CREATE OR REPLACE TABLE`** — it replaces the *entire* bronze
# MAGIC table with only the months in `start_ym`..`end_ym`. The widget defaults are `202601`–`202606`,
# MAGIC so **Run All with defaults would discard everything outside Jan–Jun 2026.**
# MAGIC
# MAGIC | you want | mode | range |
# MAGIC |---|---|---|
# MAGIC | clean full rebuild (recommended) | `overwrite` on the first batch, `append` after | the whole history |
# MAGIC | add new months only | `append` | **only months not already in bronze** — append does not dedupe |
# MAGIC
# MAGIC Run **cell 2 first** (read-only). It prints what bronze holds now, so the range is chosen
# MAGIC from measured state rather than assumed.
# MAGIC
# MAGIC The aggregate is computed Oracle-side with `GROUP BY`, so re-running a month is
# MAGIC content-identical — but `append` still writes duplicate ROWS. Overlap is the thing to avoid.

# COMMAND ----------
# ============================== CELL 1 : CONFIG + WIDGETS ==============================
import json, time
spark.sql("USE CATALOG mars_dev")

dbutils.widgets.text("start_ym", "202601")
dbutils.widgets.text("end_ym",   "202606")
dbutils.widgets.dropdown("mode", "overwrite", ["overwrite", "append"])
START_YM = dbutils.widgets.get("start_ym")          # 'YYYYMM' inclusive
END_YM   = dbutils.widgets.get("end_ym")            # 'YYYYMM' inclusive
MODE     = dbutils.widgets.get("mode")

ODS_HOST, ODS_PORT, SC = "10.3.10.30", 1521, "cubic"
def s(k): return dbutils.secrets.get(SC, k)
url = f"jdbc:oracle:thin:@//{ODS_HOST}:{ODS_PORT}/{s('ods_service')}"
USER, PWD = s("ods_user"), s("ods_pwd")
RAW_BASE = "s3://cubic-mars-pm-s3-datalake-dev-raw-170202974600/chicago_ventra/edw/use_transaction_daily"
BRONZE   = "mars_dev.bronze.edw_use_transaction_daily"
raw_path = f"{RAW_BASE}/load_{START_YM}_{END_YM}"

def months(ym0, ym1):
    y, m = int(ym0[:4]), int(ym0[4:]); y1, m1 = int(ym1[:4]), int(ym1[4:]); out = []
    while (y, m) <= (y1, m1):
        lo = y * 10000 + m * 100 + 1; hi = y * 10000 + m * 100 + 31
        out.append((lo, hi)); m += 1
        if m > 12: y += 1; m = 1
    return out

def read_month(lo, hi):
    q = f"""(SELECT TRANSIT_DAY_KEY, DEVICE_ID, REVENUE_OR_TEST,
                    COUNT(*)                    AS TXN_COUNT,
                    SUM(NVL(FARE_DUE,0))        AS FARE_DUE_SUM,
                    SUM(NVL(CALCULATED_FARE,0)) AS CALC_FARE_SUM,
                    SUM(NVL(USAGE_FEE,0))       AS USAGE_FEE_SUM,
                    MIN(TRANSACTION_DTM)        AS FIRST_TXN_DTM,
                    MAX(TRANSACTION_DTM)        AS LAST_TXN_DTM
             FROM EDW.USE_TRANSACTION
             WHERE TRANSIT_DAY_KEY BETWEEN {lo} AND {hi}
             GROUP BY TRANSIT_DAY_KEY, DEVICE_ID, REVENUE_OR_TEST) t"""
    return (spark.read.format("jdbc").option("url", url).option("user", USER)
            .option("password", PWD).option("driver", "oracle.jdbc.OracleDriver")
            .option("oracle.net.CONNECT_TIMEOUT", "15000").option("oracle.jdbc.ReadTimeout", "3600000")
            .option("queryTimeout", "3000").option("fetchsize", "20000").option("dbtable", q).load())

_m = months(START_YM, END_YM)
print(f"start_ym={START_YM}  end_ym={END_YM}  mode={MODE}")
print(f"{len(_m)} month(s) -> {len(_m)} PARALLEL Oracle connections, one JDBC query each")
print(f"raw -> {raw_path}")
print(f"bronze -> {BRONZE}   ({'CREATE OR REPLACE - REPLACES THE WHOLE TABLE' if MODE=='overwrite' else 'INSERT INTO - appends, does NOT dedupe'})")

# COMMAND ----------
# ============================== CELL 2 : WHAT IS IN BRONZE NOW? (read-only) ==============================
# Choose the range from measured state, not from the widget defaults.
try:
    b = spark.table(BRONZE)
    n = b.count()
    rng = b.agg({"TRANSIT_DAY_KEY": "min"}).collect()[0][0], b.agg({"TRANSIT_DAY_KEY": "max"}).collect()[0][0]
    print(f"{BRONZE}")
    print(f"  rows            {n:,}")
    print(f"  TRANSIT_DAY_KEY {rng[0]} .. {rng[1]}")
    print(f"  distinct days   {b.select('TRANSIT_DAY_KEY').distinct().count():,}")
    print("\n  rows per month currently held:")
    (b.selectExpr("substr(cast(TRANSIT_DAY_KEY as string),1,6) as ym")
       .groupBy("ym").count().orderBy("ym").show(60, False))
    print("  -> APPEND only months absent above. OVERWRITE replaces every month shown.")
except Exception as e:
    print(f"bronze table not readable: {str(e).splitlines()[0][:90]}")
    print("  -> if it does not exist, use mode=overwrite with the full history range.")

# COMMAND ----------
# ============================== CELL 3 : THE LOAD (writes) ==============================
out = {"start_ym": START_YM, "end_ym": END_YM, "mode": MODE}
try:
    t0 = time.time()
    mons = months(START_YM, END_YM)
    out["months"] = len(mons)
    dfs = [read_month(lo, hi) for lo, hi in mons]
    full = dfs[0]
    for d in dfs[1:]:
        full = full.unionByName(d)
    # single action -> all month-queries execute in parallel as Spark tasks
    full.write.mode("overwrite").parquet(raw_path)
    out["raw_seconds"] = round(time.time() - t0, 1)
    if MODE == "overwrite":
        spark.sql(f"CREATE OR REPLACE TABLE {BRONZE} AS SELECT * FROM parquet.`{raw_path}`")
    else:
        spark.sql(f"INSERT INTO {BRONZE} SELECT * FROM parquet.`{raw_path}`")
    b = spark.table(BRONZE)
    out["bronze_rows"] = b.count()
    out["distinct_days"] = b.select("TRANSIT_DAY_KEY").distinct().count()
    out["distinct_devices"] = b.select("DEVICE_ID").distinct().count()
    out["total_fare_due"] = float(b.agg({"FARE_DUE_SUM": "sum"}).collect()[0][0] or 0)
    out["day_range"] = [r[0] for r in b.agg({"TRANSIT_DAY_KEY": "min"}).collect()] + \
                       [r[0] for r in b.agg({"TRANSIT_DAY_KEY": "max"}).collect()]
    out["seconds"] = round(time.time() - t0, 1)
    out["status"] = "OK"
except Exception as e:
    out["status"] = "FAILED"; out["err"] = str(e)[:400]
print(json.dumps(out, indent=2, default=str))

# COMMAND ----------
# ============================== CELL 4 : POST-LOAD SANITY (read-only) ==============================
# Silver 21_use_revenue_daily filters REVENUE_OR_TEST='REVENUE' AND TRANSIT_DAY_KEY >= 20240101
# and expects roughly 2.0M rows out. Check the input can support that before rebuilding silver.
if out.get("status") == "OK":
    b = spark.table(BRONZE)
    print("rows per month after this run:")
    (b.selectExpr("substr(cast(TRANSIT_DAY_KEY as string),1,6) as ym")
       .groupBy("ym").count().orderBy("ym").show(60, False))
    print("\nduplicate grain check (TRANSIT_DAY_KEY, DEVICE_ID, REVENUE_OR_TEST should be unique):")
    d = (b.groupBy("TRANSIT_DAY_KEY", "DEVICE_ID", "REVENUE_OR_TEST").count()
           .where("count > 1").count())
    print(f"  {d:,} duplicated grain group(s)  -> {'PASS' if d == 0 else '** FAIL: append overlap **'}")
    rev = b.where("REVENUE_OR_TEST = 'REVENUE' AND TRANSIT_DAY_KEY >= 20240101").count()
    print(f"\nrows silver will consume (REVENUE, >= 20240101): {rev:,}   (expected ~2.0M)")
else:
    print("load did not succeed - nothing to verify")
