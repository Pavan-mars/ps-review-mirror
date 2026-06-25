# Databricks notebook source
# USE_TRANSACTION -> device-day revenue aggregate (Oracle-side GROUP BY, parallel by month)
# Reusable: set start_ym / end_ym / mode widgets. Each month = one JDBC connection with the
# date filter INSIDE the query, so Oracle scans only that month. Spark runs them in parallel.
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
dbutils.notebook.exit(json.dumps(out, indent=2, default=str))
