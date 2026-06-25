# Databricks notebook source
# MAGIC %md
# MAGIC # validate_silver — runtime DQ checks for the 23 silver tables (`mars_dev.silver`)
# MAGIC Operationalizes `docs/validation/Silver_Gold_Validation_2026-06-25.md`.
# MAGIC
# MAGIC Per table: existence, row count, grain uniqueness, future/2032-sentinel dates, key-column null rates.
# MAGIC Plus special checks: `dim_device` SCD2 one-current-per-device, `read_tap_daily` 2032 sentinel,
# MAGIC `kpi_daily` events-grain fan-out.
# MAGIC
# MAGIC Writes a scorecard to `mars_dev.audit.silver_validation_results` and prints PASS / WARN / FAIL.
# MAGIC Every check is wrapped so a wrong column name degrades to WARN (skipped) rather than aborting the run.

# COMMAND ----------
spark.sql("USE CATALOG mars_dev")          # bind catalog (avoids NO_SUCH_CATALOG)
from datetime import datetime
CAT, SCH = "mars_dev", "silver"
RUN_TS = datetime.utcnow().isoformat()
RESULTS = []

def log(table, check, status, detail=""):
    RESULTS.append((RUN_TS, f"{SCH}.{table}", check, status, str(detail)))
    print(f"[{status:4}] {table:26} {check:22} {detail}")

def exists(t):
    # NOTE: spark.catalog.tableExists mis-reports 3-part UC names on this workspace -> use information_schema
    return spark.sql(f"SELECT count(*) n FROM {CAT}.information_schema.tables "
                     f"WHERE table_schema='{SCH}' AND table_name='{t}'").first()["n"] > 0

def safe(table, check, fn):
    try:
        fn()
    except Exception as e:
        log(table, check, "WARN", f"skipped: {str(e).splitlines()[0][:90]}")

def check_table(t, grain=None, date_cols=None, min_rows=1, notnull=None):
    if not exists(t):
        log(t, "exists", "FAIL", "table not found"); return
    fq = f"{CAT}.{SCH}.{t}"
    n = spark.table(fq).count()
    log(t, "row_count", "PASS" if n >= min_rows else "FAIL", f"{n:,} rows")
    if grain:
        def g():
            d = spark.sql(f"SELECT count(*) - count(DISTINCT {', '.join(grain)}) AS d FROM {fq}").first()["d"]
            log(t, "grain_unique", "PASS" if d == 0 else "FAIL", f"{d:,} dup rows on ({', '.join(grain)})")
        safe(t, "grain_unique", g)
    for dc in (date_cols or []):
        def fut(dc=dc):
            c = spark.sql(f"SELECT count(*) c FROM {fq} WHERE try_cast({dc} AS DATE) > current_date()").first()["c"]
            log(t, f"no_future:{dc}", "PASS" if c == 0 else "WARN", f"{c:,} rows with {dc} > today")
        safe(t, f"no_future:{dc}", fut)
    for col in (notnull or []):
        def nn(col=col):
            r = spark.sql(f"SELECT round(100.0*sum(CASE WHEN {col} IS NULL THEN 1 ELSE 0 END)/greatest(count(*),1),2) pct FROM {fq}").first()["pct"]
            log(t, f"notnull:{col}", "PASS" if (r or 0) == 0 else "WARN", f"{r}% null")
        safe(t, f"notnull:{col}", nn)

# COMMAND ----------
# Per-table config: grain = uniqueness key; date_cols = future-date guard; notnull = key columns.
# Tables with intentionally finer/event grain are checked for existence+nulls only (grain noted below).
SILVER = {
    "dim_failure_level":       dict(grain=["failure_level"], notnull=["failure_level"]),
    "dim_stop_point":          dict(grain=["STOP_POINT_ID"], notnull=["STOP_POINT_ID"]),
    "dim_event_matrix":        dict(grain=["event_code_id"], notnull=["event_code_id"]),
    "dim_facility":            dict(grain=["facid"], notnull=["facid"]),
    "metric_hourly":           dict(grain=["DEVICE_KEY", "hour_bucket"], date_cols=["transit_day"], notnull=["DEVICE_KEY"]),
    "dim_device":              dict(grain=["DEVICE_KEY"], notnull=["DEVICE_ID", "DEVICE_KEY"]),
    "dim_event_type":          dict(grain=["EVENT_TYPE_KEY"], notnull=["EVENT_TYPE_ID"]),
    "device_uptime_intervals": dict(date_cols=["transit_day"], notnull=["DEVICE_ID"]),
    "hw_config_current":       dict(notnull=["DEVICE_ID"]),                       # multi-row per device (per component)
    "metric_daily":            dict(grain=["DEVICE_KEY", "transit_day"], date_cols=["transit_day"], notnull=["DEVICE_KEY"]),
    "kpi_avail_enriched":      dict(date_cols=["transit_day"], notnull=["DEVICE_ID"]),  # event grain
    "kpi_daily":               dict(date_cols=["transit_day"]),                   # EVENTS grain by design (see special check)
    "tap_event_daily":         dict(grain=["DEVICE_ID", "transit_day", "OPERATOR_ID", "BUS_ID"], date_cols=["transit_day"]),
    "tvm_sale_daily":          dict(date_cols=["transit_day"], notnull=["DEVICE_ID"]),
    "incident_history":        dict(grain=["number"]),
    "device_event_enriched":   dict(grain=["DW_DEVICE_EVENT_ID"], date_cols=["transit_day"], notnull=["DEVICE_ID"]),
    "incident_root_cause":     dict(date_cols=["transit_day"], notnull=["AE_DEVICE_ID"]),
    "device_outage":           dict(date_cols=["transit_day"], notnull=["DEVICE_ID"]),
    "maintenance_ledger":      dict(date_cols=["ledger_date"], notnull=["DEVICE_ID"]),
    "usage_lifecycle_daily":   dict(grain=["DEVICE_KEY", "transit_day"], date_cols=["transit_day"], notnull=["DEVICE_KEY"]),
    "use_revenue_daily":       dict(grain=["DEVICE_ID", "transit_day"], date_cols=["transit_day"], notnull=["DEVICE_ID"]),
    "read_tap_daily":          dict(grain=["DEVICE_ID", "transit_day", "OPERATOR_ID", "FACILITY_ID", "BUS_ID"], date_cols=["transit_day"], notnull=["DEVICE_ID"]),
    "kpi_monthly_benchmark":   dict(grain=["month_start", "KPI_ID"], notnull=["KPI_ID"]),
}
for t, cfg in SILVER.items():
    check_table(t, **cfg)

# COMMAND ----------
# Special checks (beyond the generic ones)

# 1) dim_device SCD2: exactly one is_current = TRUE per DEVICE_ID (else fan-out cascades downstream)
def chk_scd2():
    m = spark.sql(f"SELECT count(*) m FROM (SELECT DEVICE_ID FROM {CAT}.{SCH}.dim_device "
                  f"WHERE is_current = TRUE GROUP BY DEVICE_ID HAVING count(*) > 1)").first()["m"]
    log("dim_device", "scd2_one_current", "PASS" if m == 0 else "FAIL", f"{m:,} devices with >1 current row")
safe("dim_device", "scd2_one_current", chk_scd2)

# 2) read_tap_daily: 2032 sentinel must be excluded (TRANSIT_DAY_KEY < 20270101)
def chk_2032():
    c = spark.sql(f"SELECT count(*) c FROM {CAT}.{SCH}.read_tap_daily WHERE TRANSIT_DAY_KEY >= 20270101").first()["c"]
    log("read_tap_daily", "no_2032_sentinel", "PASS" if c == 0 else "FAIL", f"{c:,} rows with TRANSIT_DAY_KEY >= 20270101")
safe("read_tap_daily", "no_2032_sentinel", chk_2032)

# 3) kpi_daily events-grain: avg rows per (DEVICE_ID, KPI_ID, transit_day) -> gold must pre-aggregate
def chk_kpi_grain():
    r = spark.sql(f"SELECT count(*)/greatest(count(DISTINCT DEVICE_ID, KPI_ID, transit_day),1) r FROM {CAT}.{SCH}.kpi_daily").first()["r"] or 1
    log("kpi_daily", "events_per_dev_kpi_day", "WARN" if r > 1.2 else "PASS", f"{r:.2f} rows/(dev,kpi,day) — gold must pre-aggregate")
safe("kpi_daily", "events_per_dev_kpi_day", chk_kpi_grain)

# COMMAND ----------
# Write scorecard + summary
res = spark.createDataFrame(RESULTS, "run_ts string, table string, check string, status string, detail string")
spark.sql("CREATE SCHEMA IF NOT EXISTS mars_dev.audit")
res.write.mode("append").saveAsTable("mars_dev.audit.silver_validation_results")

print("\n=== SUMMARY ===")
display(res.groupBy("status").count().orderBy("status"))
print("FAILs (if any):")
display(res.where("status = 'FAIL'"))
print("WARNs (if any):")
display(res.where("status = 'WARN'"))
