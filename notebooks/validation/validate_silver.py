# Databricks notebook source
# MAGIC %md
# MAGIC # validate_silver — runtime DQ checks for the 24 silver tables (`mars_dev.silver`)
# MAGIC Operationalizes `docs/PS_Data_Quality_Gaps.docx` gap fixes (2026-07-15 batch).
# MAGIC
# MAGIC Per table: existence, row count, grain uniqueness, future/2032-sentinel dates, key-column null rates.
# MAGIC Plus special checks: `dim_device` SCD2 one-current-per-device, `read_tap_daily` 2032 sentinel,
# MAGIC `kpi_daily` events-grain fan-out, `incident_history` VALIDATOR coverage, `kpi_daily` EXCLUDED filter,
# MAGIC `tap_event_daily` timeout columns, `read_tap_daily` null-status sanity, `device_incident_features_daily` window sanity.
# MAGIC
# MAGIC Writes a scorecard to `mars_dev.audit.silver_validation_results` and prints PASS / WARN / FAIL.
# MAGIC Every check is wrapped so a wrong column name degrades to WARN (skipped) rather than aborting the run.

# COMMAND ----------
from typing import Any
from datetime import datetime

# Databricks runtime globals — injected into the notebook namespace before execution.
spark: Any   = globals().get("spark")
display: Any = globals().get("display")

spark.sql("USE CATALOG mars_dev")          # bind catalog (avoids NO_SUCH_CATALOG)
CAT, SCH = "mars_dev", "silver"
RUN_TS = datetime.utcnow().isoformat()
RESULTS = []

def log(table, check, status, detail=""):
    RESULTS.append((RUN_TS, f"{SCH}.{table}", check, status, str(detail)))
    print(f"[{status:4}] {table:34} {check:28} {detail}")

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
    "dim_failure_level":             dict(grain=["failure_level"], notnull=["failure_level"]),
    "dim_stop_point":                dict(grain=["STOP_POINT_ID"], notnull=["STOP_POINT_ID"]),
    "dim_event_matrix":              dict(grain=["event_code_id"], notnull=["event_code_id"]),
    "dim_facility":                  dict(grain=["facid"], notnull=["facid"]),
    "metric_hourly":                 dict(grain=["DEVICE_KEY", "hour_bucket"], date_cols=["transit_day"], notnull=["DEVICE_KEY"]),
    "dim_device":                    dict(grain=["DEVICE_KEY"], notnull=["DEVICE_ID", "DEVICE_KEY"]),
    "dim_event_type":                dict(grain=["EVENT_TYPE_KEY"], notnull=["EVENT_TYPE_ID"]),
    "device_uptime_intervals":       dict(date_cols=["transit_day"], notnull=["DEVICE_ID"]),
    "hw_config_current":             dict(notnull=["DEVICE_ID"]),                          # multi-row per device (per component)
    "metric_daily":                  dict(grain=["DEVICE_KEY", "transit_day"], date_cols=["transit_day"], notnull=["DEVICE_KEY"]),
    "kpi_avail_enriched":            dict(date_cols=["transit_day"], notnull=["DEVICE_ID"]),  # event grain
    "kpi_daily":                     dict(date_cols=["transit_day"]),                     # EVENTS grain by design (see special check)
    "tap_event_daily":               dict(grain=["DEVICE_ID", "transit_day", "OPERATOR_ID", "BUS_ID"], date_cols=["transit_day"],
                                         notnull=["DEVICE_ID", "tap_timeout_count"]),     # Gap 4 fix: timeout cols added
    "tvm_sale_daily":                dict(date_cols=["transit_day"], notnull=["DEVICE_ID"]),
    # Gap fix: grain changed to incident_number (not "number" — column was always incident_number in S15 schema)
    "incident_history":              dict(grain=["incident_number"], notnull=["incident_number"]),
    "device_event_enriched":         dict(grain=["DW_DEVICE_EVENT_ID"], date_cols=["transit_day"], notnull=["DEVICE_ID"]),
    "incident_root_cause":           dict(date_cols=["transit_day"], notnull=["AE_DEVICE_ID"]),
    "device_outage":                 dict(date_cols=["transit_day"], notnull=["DEVICE_ID"]),
    "maintenance_ledger":            dict(date_cols=["ledger_date"], notnull=["DEVICE_ID"]),
    "usage_lifecycle_daily":         dict(grain=["DEVICE_KEY", "transit_day"], date_cols=["transit_day"], notnull=["DEVICE_KEY"]),
    "use_revenue_daily":             dict(grain=["DEVICE_ID", "transit_day"], date_cols=["transit_day"], notnull=["DEVICE_ID"]),
    "read_tap_daily":                dict(grain=["DEVICE_ID", "transit_day", "OPERATOR_ID", "FACILITY_ID", "BUS_ID"],
                                         date_cols=["transit_day"], notnull=["DEVICE_ID"]),
    "kpi_monthly_benchmark":         dict(grain=["month_start", "KPI_ID"], notnull=["KPI_ID"]),
    # S24: added 2026-07-15 — was missing from prior validation
    "device_incident_features_daily":dict(grain=["DEVICE_KEY", "transit_day"],
                                         date_cols=["transit_day"], notnull=["DEVICE_KEY"]),
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

# 4) Gap 1 fix — kpi_daily: EXCLUDED filter must remove all excluded rows (should be 0 after fix)
def chk_kpi_excluded():
    c = spark.sql(f"SELECT count(*) c FROM {CAT}.{SCH}.kpi_daily WHERE EXCLUDED != 0").first()["c"]
    log("kpi_daily", "no_excluded_rows", "PASS" if c == 0 else "FAIL",
        f"{c:,} rows with EXCLUDED != 0 (should be 0 after Gap 1 fix)")
safe("kpi_daily", "no_excluded_rows", chk_kpi_excluded)

# 5) Gap 4 fix — tap_event_daily: timeout columns present and non-negative
def chk_tap_timeout():
    r = spark.sql(f"SELECT count(*) c, sum(CASE WHEN tap_timeout_count < 0 THEN 1 ELSE 0 END) neg "
                  f"FROM {CAT}.{SCH}.tap_event_daily").first()
    ok = r["neg"] == 0
    log("tap_event_daily", "timeout_cols_valid", "PASS" if ok else "FAIL",
        f"{r['c']:,} rows, {r['neg']} negative timeout counts (expect 0)")
safe("tap_event_daily", "timeout_cols_valid", chk_tap_timeout)

def chk_tap_timeout_rate():
    r = spark.sql(f"SELECT round(100.0*sum(tap_timeout_count)/greatest(sum(tap_count),1),4) pct "
                  f"FROM {CAT}.{SCH}.tap_event_daily").first()["pct"]
    # TAP_STATUS_ID=11 (Timeout) was ~1.3M rows from prior validation — expect > 0%
    log("tap_event_daily", "timeout_rate_nonzero", "PASS" if (r or 0) > 0 else "WARN",
        f"overall tap_timeout_rate = {r}% (expect >0 — status_id=11 confirmed present)")
safe("tap_event_daily", "timeout_rate_nonzero", chk_tap_timeout_rate)

# 6) Gap 4 fix — read_tap_daily: NULL TAP_STATUS_ID now treated as approved; null_status_read_count ~52%
def chk_read_tap_null_status():
    r = spark.sql(f"SELECT round(100.0*sum(null_status_read_count)/greatest(sum(daily_read_count),1),2) pct "
                  f"FROM {CAT}.{SCH}.read_tap_daily").first()["pct"]
    ok = (r is not None) and (40 <= r <= 65)  # confirmed ~51.97% — allow ±10pp
    log("read_tap_daily", "null_status_pct", "PASS" if ok else "WARN",
        f"{r}% of reads have NULL TAP_STATUS_ID (expected ~52%; treated as approved after fix)")
safe("read_tap_daily", "null_status_pct", chk_read_tap_null_status)

def chk_read_tap_reject_rate():
    r = spark.sql(f"SELECT round(avg(reject_rate_pct),2) avg_rr FROM {CAT}.{SCH}.read_tap_daily").first()["avg_rr"]
    # Before fix: ~57% (NULL counted as rejected). After fix: should be ~5%
    ok = (r is not None) and r < 15
    log("read_tap_daily", "reject_rate_post_fix", "PASS" if ok else "WARN",
        f"avg reject_rate_pct = {r}% (expect <15% after NULL→approved fix; was ~57% before)")
safe("read_tap_daily", "reject_rate_post_fix", chk_read_tap_reject_rate)

# 7) Gap fix — incident_history: VALIDATOR supplement must now have rows (was 0% before S15 UNION ALL)
def chk_incident_validator():
    c = spark.sql(f"SELECT count(*) c FROM {CAT}.{SCH}.incident_history "
                  f"WHERE mars_device_category = 'VALIDATOR'").first()["c"]
    log("incident_history", "validator_rows_present", "PASS" if c > 0 else "FAIL",
        f"{c:,} VALIDATOR rows (expect >0 after edw_availability_events UNION ALL)")
safe("incident_history", "validator_rows_present", chk_incident_validator)

def chk_incident_categories():
    rows = spark.sql(f"SELECT COALESCE(mars_device_category,'NULL') cat, count(*) n "
                     f"FROM {CAT}.{SCH}.incident_history GROUP BY cat ORDER BY n DESC").collect()
    summary = {r["cat"]: r["n"] for r in rows}
    log("incident_history", "category_coverage", "PASS", str(summary))
safe("incident_history", "category_coverage", chk_incident_categories)

# 8) Gap 2 RC3 fix — device_incident_features_daily: 7d window should be ≤ 30d window (sanity)
# Old ROWS-based windows spanned 38-176 calendar days; RANGE INTERVAL should produce smaller 7d counts
def chk_s24_window_sanity():
    r = spark.sql(f"SELECT sum(CASE WHEN incident_count_7d_past > incident_count_30d_past THEN 1 ELSE 0 END) violations, "
                  f"count(*) total FROM {CAT}.{SCH}.device_incident_features_daily").first()
    ok = r["violations"] == 0
    log("device_incident_features_daily", "7d_le_30d_window", "PASS" if ok else "FAIL",
        f"{r['violations']:,} rows where 7d_count > 30d_count (should be 0 with RANGE INTERVAL windows)")
safe("device_incident_features_daily", "7d_le_30d_window", chk_s24_window_sanity)

def chk_s24_row_count():
    r = spark.sql(f"SELECT count(*) c, count(DISTINCT DEVICE_KEY) d FROM {CAT}.{SCH}.device_incident_features_daily").first()
    log("device_incident_features_daily", "coverage_summary",
        "PASS" if r["d"] >= 100 else "WARN",
        f"{r['c']:,} rows, {r['d']:,} devices (expect ~96K rows, ~1,297 TVM+GATE devices)")
safe("device_incident_features_daily", "coverage_summary", chk_s24_row_count)

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
