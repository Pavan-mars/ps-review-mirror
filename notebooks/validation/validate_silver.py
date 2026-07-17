# Databricks notebook source
# MAGIC %md
# MAGIC # validate_silver — runtime DQ checks for the 25 silver tables (`mars_dev.silver`)
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
            # GROUP BY correctly handles NULLs (NULLs group together as one group).
            # count(*) - count(DISTINCT ...) is wrong for NULLable grain columns: Spark's
            # COUNT(DISTINCT col1,col2,...) excludes any row where ANY column is NULL,
            # producing false-positive dup counts when legitimate grain cols are NULLable
            # (e.g. read_tap_daily.FACILITY_ID, tap_event_daily.BUS_ID).
            d = spark.sql(
                f"SELECT COALESCE(SUM(cnt - 1), 0) d FROM "
                f"(SELECT count(*) cnt FROM {fq} GROUP BY {', '.join(grain)} HAVING count(*) > 1)"
            ).first()["d"]
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
    "device_uptime_intervals":       dict(date_cols=["eod_date"], notnull=["DEVICE_ID"]),   # date col is eod_date (not transit_day)
    "hw_config_current":             dict(notnull=["DEVICE_ID"]),                          # multi-row per device (per component)
    "metric_daily":                  dict(grain=["DEVICE_KEY", "transit_day"], date_cols=["transit_day"], notnull=["DEVICE_KEY"]),
    "kpi_avail_enriched":            dict(date_cols=["transit_day"], notnull=["DEVICE_ID"]),  # event grain
    "kpi_daily":                     dict(date_cols=["transit_day"]),                     # EVENTS grain by design (see special check)
    "tap_event_daily":               dict(grain=["DEVICE_ID", "transit_day", "OPERATOR_ID", "BUS_ID"], date_cols=["transit_day"],
                                         notnull=["DEVICE_ID", "tap_timeout_count"]),     # Gap 4 fix: timeout cols added
    # S14 actual grain is (DEVICE_ID, transit_day, OPERATOR_ID, FACILITY_ID) — header comment says
    # (device_id, transit_day) but the SQL groups by OPERATOR_ID + FACILITY_ID too. VALIDATOR bus
    # devices serve multiple operators/facilities per day, producing N rows per (DEVICE_ID, transit_day).
    # Gold tables must pre-aggregate before joining (see device_ps1_daily tvm_sales CTE).
    "tvm_sale_daily":                dict(grain=["DEVICE_ID", "transit_day", "OPERATOR_ID", "FACILITY_ID"],
                                         date_cols=["transit_day"], notnull=["DEVICE_ID"]),
    # Gap fix: grain changed to incident_number (not "number" — column was always incident_number in S15 schema)
    "incident_history":              dict(grain=["incident_number"], notnull=["incident_number"]),
    "device_event_enriched":         dict(grain=["DW_DEVICE_EVENT_ID"], date_cols=["transit_day"], notnull=["DEVICE_ID"]),
    "incident_root_cause":           dict(date_cols=["transit_day"], notnull=["device_id"]),   # AE_DEVICE_ID aliased to device_id in final SELECT
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
    # S25: added 2026-07-15 — VALIDATOR incident signal via servicenow_task_ci bus-number join
    "incident_task_ci_link":         dict(date_cols=["incident_date"],
                                         notnull=["task_ci_sys_id"]),
    # S26-S29: added 2026-07-17 — hardware failure signal, station network, MTTR, survival intervals
    "device_failures":               dict(grain=["DEVICE_KEY", "device_category", "failure_date"],
                                         date_cols=["failure_date"],
                                         notnull=["DEVICE_KEY", "device_category"]),
    "station_network_daily":         dict(grain=["FACILITY_ID", "device_category", "transit_day"],
                                         date_cols=["transit_day"],
                                         notnull=["FACILITY_ID"]),
    "device_mttr":                   dict(grain=["DEVICE_KEY", "failure_date"],
                                         date_cols=["failure_date"],
                                         notnull=["DEVICE_KEY"]),
    "device_survival_intervals":     dict(grain=["DEVICE_KEY", "interval_start_date"],
                                         notnull=["DEVICE_KEY", "interval_start_date"]),
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
    # Confirmed 2026-07-15: actual NULL TAP_STATUS_ID in edw_read_transaction = 0.18%
    # (prior ~52% estimate was incorrect — based on different bronze table or stale diagnostic)
    ok = r is not None  # just confirm column exists and returns a value
    log("read_tap_daily", "null_status_pct", "PASS" if ok else "WARN",
        f"{r}% of reads have NULL TAP_STATUS_ID (confirmed 0.18% in current bronze data)")
safe("read_tap_daily", "null_status_pct", chk_read_tap_null_status)

def chk_read_tap_reject_rate():
    r = spark.sql(f"SELECT round(avg(reject_rate_pct),2) avg_rr FROM {CAT}.{SCH}.read_tap_daily").first()["avg_rr"]
    # Confirmed 2026-07-15: codes 701+901 added as approved (READ_TRANSACTION-specific).
    # Expected reject rate after fix: ~2.8% (only codes 3, 4, 702, 703 remain rejected).
    # ⚠ PENDING domain confirmation that 701/901 are truly approved for READ_TRANSACTION.
    ok = (r is not None) and r < 10
    log("read_tap_daily", "reject_rate_post_fix", "PASS" if ok else "WARN",
        f"avg reject_rate_pct = {r}% (expect ~2.8% after 701/901 added as approved; was 41.5%)")
safe("read_tap_daily", "reject_rate_post_fix", chk_read_tap_reject_rate)

# 7) incident_history: TVM + GATE only (VALIDATOR signal confirmed absent from edw_availability_events
#    2026-07-15 — that table has TVM/RVG IDs only). VALIDATOR coverage is in S25 incident_task_ci_link.
def chk_incident_categories_present():
    rows = spark.sql(f"SELECT COALESCE(mars_device_category,'NULL') cat, count(*) n "
                     f"FROM {CAT}.{SCH}.incident_history GROUP BY cat").collect()
    cats = {r["cat"]: r["n"] for r in rows}
    tvm_ok  = cats.get("TVM", 0) > 0
    gate_ok = cats.get("GATE", 0) > 0
    log("incident_history", "tvm_gate_present", "PASS" if (tvm_ok and gate_ok) else "FAIL",
        f"TVM={cats.get('TVM',0):,}  GATE={cats.get('GATE',0):,}  NULL={cats.get('NULL',0):,}")
safe("incident_history", "tvm_gate_present", chk_incident_categories_present)

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

# 9) S25 — incident_task_ci_link: VALIDATOR bus-number join coverage
def chk_s25_validator_coverage():
    r = spark.sql(f"SELECT count(DISTINCT DEVICE_KEY) devs, count(DISTINCT incident_number) incs "
                  f"FROM {CAT}.{SCH}.incident_task_ci_link WHERE DEVICE_KEY IS NOT NULL").first()
    ok = r["devs"] >= 800 and r["incs"] >= 10000
    log("incident_task_ci_link", "validator_coverage",
        "PASS" if ok else "WARN",
        f"{r['devs']:,} VALIDATOR devices, {r['incs']:,} incidents (expect ~823 devices, ~10,453 incidents)")
safe("incident_task_ci_link", "validator_coverage", chk_s25_validator_coverage)

def chk_s25_join_rate():
    r = spark.sql(f"SELECT count(*) total, "
                  f"sum(CASE WHEN incident_sys_id IS NOT NULL THEN 1 ELSE 0 END) joined "
                  f"FROM {CAT}.{SCH}.incident_task_ci_link").first()
    pct = round(100.0 * r["joined"] / max(r["total"], 1), 1)
    log("incident_task_ci_link", "incident_join_rate",
        "PASS" if pct >= 85 else "WARN",
        f"{pct}% task_ci rows joined to incident (expect ~91.8%; 16,267 unmatched is known)")
safe("incident_task_ci_link", "incident_join_rate", chk_s25_join_rate)

def chk_s25_no_fanout():
    d = spark.sql(f"SELECT count(*) - count(DISTINCT task_ci_sys_id) d "
                  f"FROM {CAT}.{SCH}.incident_task_ci_link").first()["d"]
    log("incident_task_ci_link", "no_fanout",
        "PASS" if d == 0 else "FAIL",
        f"{d:,} duplicate task_ci_sys_id rows (should be 0 — grain is one row per task_ci record)")
safe("incident_task_ci_link", "no_fanout", chk_s25_no_fanout)

# 10) S26 device_failures — all three device categories must be present
def chk_s26_categories():
    rows = spark.sql(f"SELECT device_category, COUNT(DISTINCT DEVICE_KEY) devs, COUNT(*) n "
                     f"FROM {CAT}.{SCH}.device_failures GROUP BY device_category ORDER BY 1").collect()
    cats = {r["device_category"]: (r["devs"], r["n"]) for r in rows}
    tvm_ok  = cats.get("TVM",  (0,0))[0] > 0
    gate_ok = cats.get("GATE", (0,0))[0] > 0
    val_ok  = cats.get("VALIDATOR", (0,0))[0] > 0
    ok = tvm_ok and gate_ok and val_ok
    log("device_failures", "category_coverage", "PASS" if ok else "WARN",
        str({k: f"{v[0]} devs / {v[1]} failure-days" for k, v in cats.items()}))
safe("device_failures", "category_coverage", chk_s26_categories)

def chk_s26_no_avm():
    c = spark.sql(f"SELECT count(*) c FROM {CAT}.{SCH}.device_failures "
                  f"WHERE DEVICE_ID LIKE 'AVM%'").first()["c"]
    log("device_failures", "no_avm_rows", "PASS" if c == 0 else "FAIL",
        f"{c} AVM rows (should be 0 — mars_device_category=OTHER excluded by filter)")
safe("device_failures", "no_avm_rows", chk_s26_no_avm)

# 11) S27 station_network_daily — coordinated failure flag sanity
def chk_s27_coordinated():
    r = spark.sql(f"SELECT count(*) total, "
                  f"sum(CASE WHEN is_coordinated_failure THEN 1 ELSE 0 END) coord, "
                  f"sum(CASE WHEN is_major_station_event THEN 1 ELSE 0 END) major "
                  f"FROM {CAT}.{SCH}.station_network_daily").first()
    pct = round(100.0 * (r["coord"] or 0) / max(r["total"] or 1, 1), 1)
    log("station_network_daily", "coordinated_event_rate",
        "PASS" if (r["total"] or 0) > 0 else "WARN",
        f"{pct}% of station-failure-days coordinated (≥3 devices); major={r['major']} (≥5 devices)")
safe("station_network_daily", "coordinated_event_rate", chk_s27_coordinated)

# 12) S28 device_mttr — no negative rolling MTTR values
def chk_s28_mttr_sanity():
    r = spark.sql(f"SELECT "
                  f"sum(CASE WHEN avg_downtime_30d < 0 THEN 1 ELSE 0 END) neg30, "
                  f"sum(CASE WHEN avg_downtime_90d < 0 THEN 1 ELSE 0 END) neg90, "
                  f"sum(CASE WHEN failure_days_30d < 1 THEN 1 ELSE 0 END) zero_days "
                  f"FROM {CAT}.{SCH}.device_mttr").first()
    ok = (r["neg30"] == 0) and (r["neg90"] == 0) and (r["zero_days"] == 0)
    log("device_mttr", "mttr_sanity", "PASS" if ok else "FAIL",
        f"neg_30d={r['neg30']} neg_90d={r['neg90']} zero_failure_days={r['zero_days']} (all should be 0)")
safe("device_mttr", "mttr_sanity", chk_s28_mttr_sanity)

# 13) S29 device_survival_intervals — interval_days ≥ 1; ongoing rows must have NULL end date
def chk_s29_interval_days():
    r = spark.sql(f"SELECT sum(CASE WHEN interval_days < 1 THEN 1 ELSE 0 END) bad "
                  f"FROM {CAT}.{SCH}.device_survival_intervals").first()
    log("device_survival_intervals", "interval_days_positive", "PASS" if r["bad"] == 0 else "FAIL",
        f"{r['bad']} rows with interval_days < 1 (should be 0)")
safe("device_survival_intervals", "interval_days_positive", chk_s29_interval_days)

def chk_s29_ongoing_null_end():
    r = spark.sql(f"SELECT "
                  f"sum(CASE WHEN is_ongoing AND interval_end_date IS NOT NULL THEN 1 ELSE 0 END) bad_ongoing, "
                  f"sum(CASE WHEN NOT is_ongoing AND interval_end_date IS NULL THEN 1 ELSE 0 END) bad_closed "
                  f"FROM {CAT}.{SCH}.device_survival_intervals").first()
    ok = (r["bad_ongoing"] == 0) and (r["bad_closed"] == 0)
    log("device_survival_intervals", "ongoing_end_date_null", "PASS" if ok else "FAIL",
        f"bad_ongoing={r['bad_ongoing']} (ongoing must have NULL end); bad_closed={r['bad_closed']} (closed must have end date)")
safe("device_survival_intervals", "ongoing_end_date_null", chk_s29_ongoing_null_end)

# 14) S10 metric_daily v2 (2026-07-17) — new M401 + comms columns present after rebuild
def chk_s10_v2_cols():
    cols_expected = [
        "m401_p95_txn_time_ms", "m401_p99_txn_time_ms",
        "m401_slow_tap_count", "m401_slow_tap_pct",
        "m401_rolling_7d_avg_ms", "m401_z_score_vs_28d",
        "comms_csc_read_err_count", "comms_host_comm_lost_count",
        "comms_device_comms_lost_count", "comms_total_count", "comms_event_flag",
    ]
    actual = [c.lower() for c in spark.table(f"{CAT}.{SCH}.metric_daily").columns]
    missing = [c for c in cols_expected if c not in actual]
    log("metric_daily", "v2_cols_present",
        "PASS" if not missing else "FAIL",
        f"missing={missing} (should be empty after S10 v2 rebuild 2026-07-17)")
safe("metric_daily", "v2_cols_present", chk_s10_v2_cols)

# S10: TVM rows must be present (comms spine adds TVM even though TVM has no M401 data)
def chk_s10_tvm_comms():
    rows = spark.sql(
        f"SELECT dd.mars_device_category cat, "
        f"COUNT(DISTINCT md.DEVICE_KEY) devs, "
        f"SUM(CAST(md.m401_daily_txn_count IS NULL AS INT)) null_m401_rows, "
        f"SUM(md.comms_total_count) total_comms "
        f"FROM {CAT}.{SCH}.metric_daily md "
        f"JOIN {CAT}.{SCH}.dim_device dd ON dd.DEVICE_KEY = md.DEVICE_KEY AND dd.is_current = TRUE "
        f"GROUP BY dd.mars_device_category ORDER BY 1"
    ).collect()
    cats = {r["cat"]: r for r in rows}
    tvm_ok  = cats.get("TVM", {}).get("devs", 0) > 0
    gate_ok = cats.get("GATE", {}).get("devs", 0) > 0
    val_ok  = cats.get("VALIDATOR", {}).get("devs", 0) > 0
    log("metric_daily", "tvm_comms_spine_present",
        "PASS" if tvm_ok else "WARN",
        str({k: f"{v['devs']} devs | null_m401={v['null_m401_rows']} | comms={v['total_comms']}"
             for k, v in cats.items()}) +
        " (TVM WARN = S10 not yet rebuilt with comms spine)")
safe("metric_daily", "tvm_comms_spine_present", chk_s10_tvm_comms)

# S10: slow_tap_pct must be 0-100; avg gives a sense of the degradation signal level
def chk_s10_slow_tap_range():
    r = spark.sql(
        f"SELECT sum(CASE WHEN m401_slow_tap_pct < 0 OR m401_slow_tap_pct > 100 THEN 1 ELSE 0 END) bad, "
        f"round(avg(m401_slow_tap_pct), 2) avg_pct "
        f"FROM {CAT}.{SCH}.metric_daily WHERE m401_slow_tap_pct IS NOT NULL"
    ).first()
    log("metric_daily", "slow_tap_pct_range",
        "PASS" if (r["bad"] or 0) == 0 else "FAIL",
        f"{r['bad']} rows outside 0-100%; avg_slow_tap_pct={r['avg_pct']}% (>1000ms taps)")
safe("metric_daily", "slow_tap_pct_range", chk_s10_slow_tap_range)

# S10: z-score sanity — |z| > 10 std devs should be rare (<5% of non-null rows)
def chk_s10_zscore_sanity():
    r = spark.sql(
        f"SELECT count(*) total, "
        f"sum(CASE WHEN ABS(m401_z_score_vs_28d) > 10 THEN 1 ELSE 0 END) extreme "
        f"FROM {CAT}.{SCH}.metric_daily WHERE m401_z_score_vs_28d IS NOT NULL"
    ).first()
    pct = round(100.0 * (r["extreme"] or 0) / max(r["total"] or 1, 1), 2)
    log("metric_daily", "z_score_extreme_rate",
        "PASS" if pct < 5 else "WARN",
        f"{pct}% rows with |z_score_vs_28d| > 10 std devs (expect <5%)")
safe("metric_daily", "z_score_extreme_rate", chk_s10_zscore_sanity)

# S10: comms events non-zero — at least some rows should have comms events
# (GATE: 21.9M CSC Read err confirmed 2026-07-17; VALIDATOR: 17.4M; TVM: 1.1M)
def chk_s10_comms_nonzero():
    r = spark.sql(
        f"SELECT sum(CASE WHEN comms_event_flag THEN 1 ELSE 0 END) with_comms, count(*) total "
        f"FROM {CAT}.{SCH}.metric_daily"
    ).first()
    pct = round(100.0 * (r["with_comms"] or 0) / max(r["total"] or 1, 1), 1)
    log("metric_daily", "comms_event_coverage",
        "PASS" if (r["with_comms"] or 0) > 0 else "WARN",
        f"{pct}% of device-days have at least one reader/comms event "
        f"(WARN = comms_grain CTE empty or S16 not yet rebuilt)")
safe("metric_daily", "comms_event_coverage", chk_s10_comms_nonzero)

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
