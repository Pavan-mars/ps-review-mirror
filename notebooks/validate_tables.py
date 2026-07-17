# Databricks notebook source
# =============================================================================
# validate_tables -- row counts + key metrics for all silver (S01-S24) and
#                    gold (PS1-PS5) tables in mars_dev Unity Catalog.
#
# Run AFTER run_layer (silver first, then gold).
# No data is modified -- read-only.
#
# Widgets:
#   catalog   mars_dev (default)
# =============================================================================

from typing import Any

# Databricks runtime globals — injected into the notebook namespace before execution.
dbutils: Any = globals().get("dbutils")
spark: Any   = globals().get("spark")
display: Any = globals().get("display")

dbutils.widgets.text("catalog", "mars_dev")
catalog = dbutils.widgets.get("catalog").strip()

spark.sql(f"USE CATALOG {catalog}")

# COMMAND ----------

# Helper: run a validation query and return one result row as a dict.
# Returns {"error": <msg>} if the table doesn't exist or query fails.
def check(sql):
    try:
        row = spark.sql(sql).collect()
        return row[0].asDict() if row else {"error": "no rows returned"}
    except Exception as e:
        return {"error": str(e)[:120]}

def fmt(v):
    if v is None:
        return "NULL"
    if isinstance(v, bool):
        return str(v)
    if isinstance(v, (int, float)):
        return f"{int(v):,}"
    return str(v)

def status(rows, min_rows=1):
    if isinstance(rows, dict) and "error" in rows:
        return "FAIL  (table missing or query error)"
    n = rows.get("rows") or rows.get("total_rows") or 0
    if n == 0:
        return "FAIL  (0 rows)"
    if n < min_rows:
        return f"WARN  ({fmt(n)} rows -- below expected {fmt(min_rows)})"
    return f"OK    ({fmt(n)} rows)"

results = []   # (layer, code, table, status_str, detail_dict)

# COMMAND ----------
# =============================================================================
# SILVER TABLES  S01 - S23
# =============================================================================
print("=" * 70)
print("SILVER TABLES")
print("=" * 70)

# -- S01 dim_failure_level ---------------------------------------------------
r = check(f"""
    SELECT COUNT(*) AS rows,
           COUNT(DISTINCT failure_level) AS distinct_levels
    FROM {catalog}.silver.dim_failure_level
""")
s = status(r, min_rows=30)
print(f"S01  dim_failure_level          {s}")
if "error" not in r:
    print(f"       distinct_levels={fmt(r.get('distinct_levels'))}")
results.append(("silver", "S01", "dim_failure_level", s, r))

# -- S02 dim_stop_point ------------------------------------------------------
r = check(f"""
    SELECT COUNT(*) AS rows,
           COUNT(DISTINCT STOP_POINT_ID) AS distinct_stops
    FROM {catalog}.silver.dim_stop_point
""")
s = status(r, min_rows=100)
print(f"S02  dim_stop_point             {s}")
if "error" not in r:
    print(f"       distinct_stops={fmt(r.get('distinct_stops'))}")
results.append(("silver", "S02", "dim_stop_point", s, r))

# -- S03 dim_event_matrix ----------------------------------------------------
r = check(f"""
    SELECT COUNT(*) AS rows,
           COUNT(DISTINCT event_code_id) AS distinct_codes
    FROM {catalog}.silver.dim_event_matrix
""")
s = status(r, min_rows=100)
print(f"S03  dim_event_matrix           {s}")
if "error" not in r:
    print(f"       distinct_codes={fmt(r.get('distinct_codes'))}")
results.append(("silver", "S03", "dim_event_matrix", s, r))

# -- S04 dim_facility --------------------------------------------------------
r = check(f"""
    SELECT COUNT(*) AS rows,
           COUNT(DISTINCT facid) AS distinct_facilities
    FROM {catalog}.silver.dim_facility
""")
s = status(r, min_rows=50)
print(f"S04  dim_facility               {s}")
if "error" not in r:
    print(f"       distinct_facilities={fmt(r.get('distinct_facilities'))}")
results.append(("silver", "S04", "dim_facility", s, r))

# -- S05 metric_hourly -------------------------------------------------------
r = check(f"""
    SELECT COUNT(*) AS rows,
           COUNT(DISTINCT DEVICE_KEY) AS distinct_devices,
           MIN(transit_day) AS min_day,
           MAX(transit_day) AS max_day
    FROM {catalog}.silver.metric_hourly
""")
s = status(r, min_rows=1000)
print(f"S05  metric_hourly              {s}")
if "error" not in r:
    print(f"       distinct_devices={fmt(r.get('distinct_devices'))}  range={r.get('min_day')} to {r.get('max_day')}")
results.append(("silver", "S05", "metric_hourly", s, r))

# -- S06 dim_device ----------------------------------------------------------
r = check(f"""
    SELECT COUNT(*) AS rows,
           COUNT(DISTINCT DEVICE_ID) AS distinct_devices,
           COUNT(CASE WHEN is_current = TRUE THEN 1 END) AS current_devices,
           COUNT(DISTINCT mars_device_category) AS categories
    FROM {catalog}.silver.dim_device
""")
s = status(r, min_rows=5000)
print(f"S06  dim_device                 {s}")
if "error" not in r:
    print(f"       distinct_devices={fmt(r.get('distinct_devices'))}  current={fmt(r.get('current_devices'))}  categories={fmt(r.get('categories'))}")
results.append(("silver", "S06", "dim_device", s, r))

# -- S07 dim_event_type ------------------------------------------------------
r = check(f"""
    SELECT COUNT(*) AS rows,
           COUNT(CASE WHEN is_oos_event = TRUE THEN 1 END) AS oos_events,
           COUNT(CASE WHEN is_hardware_oos_event = TRUE THEN 1 END) AS hw_oos_events,
           COUNT(CASE WHEN applies_to_gate = TRUE THEN 1 END) AS gate_events
    FROM {catalog}.silver.dim_event_type
""")
s = status(r, min_rows=400)
print(f"S07  dim_event_type             {s}")
if "error" not in r:
    print(f"       oos_events={fmt(r.get('oos_events'))}  hw_oos={fmt(r.get('hw_oos_events'))}  gate_events={fmt(r.get('gate_events'))}")
results.append(("silver", "S07", "dim_event_type", s, r))

# -- S08 device_uptime_intervals ---------------------------------------------
r = check(f"""
    SELECT COUNT(*) AS rows,
           COUNT(DISTINCT DEVICE_ID) AS distinct_devices
    FROM {catalog}.silver.device_uptime_intervals
""")
s = status(r, min_rows=1000)
print(f"S08  device_uptime_intervals    {s}")
if "error" not in r:
    print(f"       distinct_devices={fmt(r.get('distinct_devices'))}")
results.append(("silver", "S08", "device_uptime_intervals", s, r))

# -- S09 hw_config_current ---------------------------------------------------
r = check(f"""
    SELECT COUNT(*) AS rows,
           COUNT(DISTINCT DEVICE_ID) AS distinct_devices,
           COUNT(DISTINCT COMPONENT_DESCRIPTION) AS component_types
    FROM {catalog}.silver.hw_config_current
""")
s = status(r, min_rows=100)
print(f"S09  hw_config_current          {s}")
if "error" not in r:
    print(f"       distinct_devices={fmt(r.get('distinct_devices'))}  component_types={fmt(r.get('component_types'))}")
results.append(("silver", "S09", "hw_config_current", s, r))

# -- S10 metric_daily --------------------------------------------------------
r = check(f"""
    SELECT COUNT(*) AS rows,
           COUNT(DISTINCT DEVICE_KEY) AS distinct_devices,
           MIN(transit_day) AS min_day,
           MAX(transit_day) AS max_day
    FROM {catalog}.silver.metric_daily
""")
s = status(r, min_rows=100000)
print(f"S10  metric_daily               {s}")
if "error" not in r:
    print(f"       distinct_devices={fmt(r.get('distinct_devices'))}  range={r.get('min_day')} to {r.get('max_day')}")
results.append(("silver", "S10", "metric_daily", s, r))

# -- S11 kpi_avail_enriched --------------------------------------------------
r = check(f"""
    SELECT COUNT(*) AS rows,
           COUNT(DISTINCT DEVICE_ID) AS distinct_devices,
           MIN(transit_day) AS min_day,
           MAX(transit_day) AS max_day
    FROM {catalog}.silver.kpi_avail_enriched
""")
s = status(r, min_rows=10000)
print(f"S11  kpi_avail_enriched         {s}")
if "error" not in r:
    print(f"       distinct_devices={fmt(r.get('distinct_devices'))}  range={r.get('min_day')} to {r.get('max_day')}")
results.append(("silver", "S11", "kpi_avail_enriched", s, r))

# -- S12 kpi_daily -----------------------------------------------------------
r = check(f"""
    SELECT COUNT(*) AS rows,
           COUNT(DISTINCT DEVICE_ID) AS distinct_devices,
           MIN(transit_day) AS min_day,
           MAX(transit_day) AS max_day
    FROM {catalog}.silver.kpi_daily
""")
s = status(r, min_rows=10000)
print(f"S12  kpi_daily                  {s}")
if "error" not in r:
    print(f"       distinct_devices={fmt(r.get('distinct_devices'))}  range={r.get('min_day')} to {r.get('max_day')}")
results.append(("silver", "S12", "kpi_daily", s, r))

# -- S13 tap_event_daily -----------------------------------------------------
r = check(f"""
    SELECT COUNT(*) AS rows,
           COUNT(DISTINCT DEVICE_ID) AS distinct_devices,
           MIN(transit_day) AS min_day,
           MAX(transit_day) AS max_day
    FROM {catalog}.silver.tap_event_daily
""")
s = status(r, min_rows=100000)
print(f"S13  tap_event_daily            {s}")
if "error" not in r:
    print(f"       distinct_devices={fmt(r.get('distinct_devices'))}  range={r.get('min_day')} to {r.get('max_day')}")
results.append(("silver", "S13", "tap_event_daily", s, r))

# -- S14 tvm_sale_daily ------------------------------------------------------
r = check(f"""
    SELECT COUNT(*) AS rows,
           COUNT(DISTINCT DEVICE_ID) AS distinct_devices,
           MIN(transit_day) AS min_day,
           MAX(transit_day) AS max_day
    FROM {catalog}.silver.tvm_sale_daily
""")
s = status(r, min_rows=10000)
print(f"S14  tvm_sale_daily             {s}")
if "error" not in r:
    print(f"       distinct_devices={fmt(r.get('distinct_devices'))}  range={r.get('min_day')} to {r.get('max_day')}")
results.append(("silver", "S14", "tvm_sale_daily", s, r))

# -- S15 incident_history ----------------------------------------------------
r = check(f"""
    SELECT COUNT(*) AS rows,
           COUNT(DISTINCT wm_asset) AS distinct_devices,
           MIN(opened_dtm) AS earliest,
           MAX(opened_dtm) AS latest
    FROM {catalog}.silver.incident_history
""")
s = status(r, min_rows=1000)
print(f"S15  incident_history           {s}")
if "error" not in r:
    print(f"       distinct_devices={fmt(r.get('distinct_devices'))}  range={r.get('earliest')} to {r.get('latest')}")
results.append(("silver", "S15", "incident_history", s, r))

# -- S16 device_event_enriched -----------------------------------------------
r = check(f"""
    SELECT COUNT(*) AS rows,
           COUNT(DISTINCT DEVICE_ID) AS distinct_devices,
           MIN(transit_day) AS min_day,
           MAX(transit_day) AS max_day,
           COUNT(CASE WHEN is_hardware_oos_event = TRUE THEN 1 END) AS hw_oos_rows
    FROM {catalog}.silver.device_event_enriched
""")
s = status(r, min_rows=1000000)
print(f"S16  device_event_enriched      {s}")
if "error" not in r:
    print(f"       distinct_devices={fmt(r.get('distinct_devices'))}  range={r.get('min_day')} to {r.get('max_day')}  hw_oos_rows={fmt(r.get('hw_oos_rows'))}")
results.append(("silver", "S16", "device_event_enriched", s, r))

# -- S17 incident_root_cause -------------------------------------------------
r = check(f"""
    SELECT COUNT(*) AS rows,
           COUNT(DISTINCT DEVICE_ID) AS distinct_devices,
           COUNT(CASE WHEN is_chargeable = TRUE THEN 1 END) AS chargeable_rows
    FROM {catalog}.silver.incident_root_cause
""")
s = status(r, min_rows=1000)
print(f"S17  incident_root_cause        {s}")
if "error" not in r:
    print(f"       distinct_devices={fmt(r.get('distinct_devices'))}  chargeable_rows={fmt(r.get('chargeable_rows'))}")
results.append(("silver", "S17", "incident_root_cause", s, r))

# -- S18 device_outage -------------------------------------------------------
r = check(f"""
    SELECT COUNT(*) AS rows,
           COUNT(DISTINCT DEVICE_ID) AS distinct_devices,
           MIN(transit_day) AS min_day,
           MAX(transit_day) AS max_day
    FROM {catalog}.silver.device_outage
""")
s = status(r, min_rows=1000)
print(f"S18  device_outage              {s}")
if "error" not in r:
    print(f"       distinct_devices={fmt(r.get('distinct_devices'))}  range={r.get('min_day')} to {r.get('max_day')}")
results.append(("silver", "S18", "device_outage", s, r))

# -- S19 maintenance_ledger --------------------------------------------------
r = check(f"""
    SELECT COUNT(*) AS rows,
           COUNT(DISTINCT DEVICE_ID) AS distinct_devices,
           MIN(ledger_date) AS min_day,
           MAX(ledger_date) AS max_day
    FROM {catalog}.silver.maintenance_ledger
""")
s = status(r, min_rows=1000)
print(f"S19  maintenance_ledger         {s}")
if "error" not in r:
    print(f"       distinct_devices={fmt(r.get('distinct_devices'))}  range={r.get('min_day')} to {r.get('max_day')}")
results.append(("silver", "S19", "maintenance_ledger", s, r))

# -- S20 usage_lifecycle_daily -----------------------------------------------
r = check(f"""
    SELECT COUNT(*) AS rows,
           COUNT(DISTINCT DEVICE_KEY) AS distinct_devices,
           MIN(transit_day) AS min_day,
           MAX(transit_day) AS max_day,
           MAX(days_in_service) AS max_days_in_service
    FROM {catalog}.silver.usage_lifecycle_daily
""")
s = status(r, min_rows=100000)
print(f"S20  usage_lifecycle_daily      {s}")
if "error" not in r:
    print(f"       distinct_devices={fmt(r.get('distinct_devices'))}  range={r.get('min_day')} to {r.get('max_day')}  max_days_in_service={fmt(r.get('max_days_in_service'))}")
results.append(("silver", "S20", "usage_lifecycle_daily", s, r))

# -- S21 use_revenue_daily ---------------------------------------------------
r = check(f"""
    SELECT COUNT(*) AS rows,
           COUNT(DISTINCT DEVICE_ID) AS distinct_devices,
           MIN(transit_day) AS min_day,
           MAX(transit_day) AS max_day,
           ROUND(AVG(priced_txn_pct), 2) AS avg_priced_txn_pct
    FROM {catalog}.silver.use_revenue_daily
""")
s = status(r, min_rows=200000)
print(f"S21  use_revenue_daily          {s}")
if "error" not in r:
    print(f"       distinct_devices={fmt(r.get('distinct_devices'))}  range={r.get('min_day')} to {r.get('max_day')}  avg_priced_txn_pct={r.get('avg_priced_txn_pct')}%")
results.append(("silver", "S21", "use_revenue_daily", s, r))

# -- S22 read_tap_daily ------------------------------------------------------
r = check(f"""
    SELECT COUNT(*) AS rows,
           COUNT(DISTINCT DEVICE_ID) AS distinct_devices,
           MIN(transit_day) AS min_day,
           MAX(transit_day) AS max_day,
           MAX(transit_day) <= '2026-12-31' AS sentinel_excluded
    FROM {catalog}.silver.read_tap_daily
""")
s = status(r, min_rows=300000)
print(f"S22  read_tap_daily             {s}")
if "error" not in r:
    print(f"       distinct_devices={fmt(r.get('distinct_devices'))}  range={r.get('min_day')} to {r.get('max_day')}  sentinel_excluded={r.get('sentinel_excluded')}")
results.append(("silver", "S22", "read_tap_daily", s, r))

# -- S23 kpi_monthly_benchmark -----------------------------------------------
r = check(f"""
    SELECT COUNT(*) AS rows,
           MIN(month_start) AS min_month,
           MAX(month_start) AS max_month
    FROM {catalog}.silver.kpi_monthly_benchmark
""")
s = status(r, min_rows=1)
print(f"S23  kpi_monthly_benchmark      {s}  [governance only -- frozen 2015 data]")
if "error" not in r:
    print(f"       range={r.get('min_month')} to {r.get('max_month')}")
results.append(("silver", "S23", "kpi_monthly_benchmark", s, r))

# -- S24 device_incident_features_daily (NEW 2026-07-07) ---------------------
r = check(f"""
    SELECT COUNT(*) AS rows,
           COUNT(DISTINCT DEVICE_KEY) AS distinct_devices,
           MIN(transit_day) AS min_day,
           MAX(transit_day) AS max_day,
           SUM(CASE WHEN incident_count_30d_past IS NULL THEN 1 ELSE 0 END) AS null_count_cols,
           SUM(CASE WHEN avg_mttr_30d_past IS NULL THEN 1 ELSE 0 END)       AS null_mttr_rows,
           SUM(CASE WHEN incident_count_7d_past > incident_count_30d_past
                    OR incident_count_30d_past > incident_count_90d_past
                    THEN 1 ELSE 0 END)                                       AS window_violations,
           SUM(CASE WHEN transit_day BETWEEN '2025-01-01' AND '2025-02-28'
                    THEN 1 ELSE 0 END)                                       AS jan_feb_2025_rows
    FROM {catalog}.silver.device_incident_features_daily
""")
s = status(r, min_rows=100000)
print(f"S24  device_incident_features_daily  {s}")
if "error" not in r:
    print(f"       distinct_devices={fmt(r.get('distinct_devices'))}  range={r.get('min_day')} to {r.get('max_day')}")
    print(f"       null_count_cols={fmt(r.get('null_count_cols'))} (expected 0)  null_mttr_rows={fmt(r.get('null_mttr_rows'))} (expected ~1/device)  window_violations={fmt(r.get('window_violations'))} (expected 0)")
    jan_feb = r.get('jan_feb_2025_rows', 0)
    jan_feb_ok = "OK" if (jan_feb or 0) > 0 else "FAIL -- gap exclusion still present or table not rebuilt"
    print(f"       jan_feb_2025_rows={fmt(jan_feb)} [{jan_feb_ok}]")
results.append(("silver", "S24", "device_incident_features_daily", s, r))

# -- S25 incident_task_ci_link -----------------------------------------------
r = check(f"""
    SELECT COUNT(*) AS rows,
           COUNT(DISTINCT DEVICE_KEY) AS distinct_devices,
           COUNT(DISTINCT incident_number) AS distinct_incidents,
           ROUND(100.0 * SUM(CASE WHEN incident_sys_id IS NOT NULL THEN 1 ELSE 0 END) / COUNT(*), 1)
               AS incident_join_pct
    FROM {catalog}.silver.incident_task_ci_link
""")
s = status(r, min_rows=1000)
print(f"S25  incident_task_ci_link       {s}")
if "error" not in r:
    print(f"       distinct_devices={fmt(r.get('distinct_devices'))}  distinct_incidents={fmt(r.get('distinct_incidents'))}")
    print(f"       incident_join_pct={r.get('incident_join_pct')}%  (expect ~91.8%)")
results.append(("silver", "S25", "incident_task_ci_link", s, r))

# -- S26 device_failures (NEW 2026-07-17) ----------------------------------------
r = check(f"""
    SELECT COUNT(*) AS rows,
           COUNT(DISTINCT DEVICE_KEY) AS distinct_devices,
           MIN(failure_date) AS min_day,
           MAX(failure_date) AS max_day,
           COUNT(DISTINCT device_category) AS categories,
           COUNT(CASE WHEN failure_source = 'availability_events' THEN 1 END) AS tvm_gate_rows,
           COUNT(CASE WHEN failure_source = 'device_event_enriched' THEN 1 END) AS validator_rows
    FROM {catalog}.silver.device_failures
""")
s = status(r, min_rows=1000)
print(f"S26  device_failures             {s}")
if "error" not in r:
    print(f"       distinct_devices={fmt(r.get('distinct_devices'))}  range={r.get('min_day')} to {r.get('max_day')}  categories={fmt(r.get('categories'))}")
    print(f"       tvm_gate_rows={fmt(r.get('tvm_gate_rows'))}  validator_rows={fmt(r.get('validator_rows'))}")
results.append(("silver", "S26", "device_failures", s, r))

# -- S27 station_network_daily (NEW 2026-07-17) ----------------------------------
r = check(f"""
    SELECT COUNT(*) AS rows,
           COUNT(DISTINCT FACILITY_ID) AS distinct_facilities,
           MIN(transit_day) AS min_day,
           MAX(transit_day) AS max_day,
           SUM(CASE WHEN is_coordinated_failure = TRUE THEN 1 ELSE 0 END) AS coordinated_events,
           SUM(CASE WHEN is_major_station_event = TRUE THEN 1 ELSE 0 END) AS major_events
    FROM {catalog}.silver.station_network_daily
""")
s = status(r, min_rows=100)
print(f"S27  station_network_daily       {s}")
if "error" not in r:
    print(f"       distinct_facilities={fmt(r.get('distinct_facilities'))}  range={r.get('min_day')} to {r.get('max_day')}")
    print(f"       coordinated_events={fmt(r.get('coordinated_events'))}  major_events={fmt(r.get('major_events'))}")
results.append(("silver", "S27", "station_network_daily", s, r))

# -- S28 device_mttr (NEW 2026-07-17) --------------------------------------------
r = check(f"""
    SELECT COUNT(*) AS rows,
           COUNT(DISTINCT DEVICE_KEY) AS distinct_devices,
           MIN(failure_date) AS min_day,
           MAX(failure_date) AS max_day,
           ROUND(AVG(avg_downtime_30d), 1) AS avg_mttr_30d_min
    FROM {catalog}.silver.device_mttr
""")
s = status(r, min_rows=1000)
print(f"S28  device_mttr                 {s}")
if "error" not in r:
    print(f"       distinct_devices={fmt(r.get('distinct_devices'))}  range={r.get('min_day')} to {r.get('max_day')}  avg_mttr_30d={r.get('avg_mttr_30d_min')} min")
results.append(("silver", "S28", "device_mttr", s, r))

# -- S29 device_survival_intervals (NEW 2026-07-17) ------------------------------
r = check(f"""
    SELECT COUNT(*) AS rows,
           COUNT(DISTINCT DEVICE_KEY) AS distinct_devices,
           SUM(CASE WHEN is_ongoing = TRUE THEN 1 ELSE 0 END) AS ongoing_intervals,
           SUM(CASE WHEN is_first_interval = TRUE THEN 1 ELSE 0 END) AS first_intervals,
           ROUND(AVG(interval_days), 1) AS avg_interval_days
    FROM {catalog}.silver.device_survival_intervals
""")
s = status(r, min_rows=100)
print(f"S29  device_survival_intervals   {s}")
if "error" not in r:
    print(f"       distinct_devices={fmt(r.get('distinct_devices'))}  ongoing={fmt(r.get('ongoing_intervals'))}  first={fmt(r.get('first_intervals'))}  avg_days={r.get('avg_interval_days')}")
results.append(("silver", "S29", "device_survival_intervals", s, r))

# COMMAND ----------
# =============================================================================
# GOLD TABLES  PS1 - PS5
# =============================================================================
print()
print("=" * 70)
print("GOLD TABLES")
print("=" * 70)

# -- PS1 device_ps1_daily ----------------------------------------------------
r = check(f"""
    SELECT COUNT(*) AS total_rows,
           COUNT(DISTINCT DEVICE_ID) AS distinct_devices,
           MIN(transit_day) AS min_day,
           MAX(transit_day) AS max_day,
           SUM(will_fail_3d)  AS labels_3d,
           ROUND(SUM(will_fail_3d)  * 100.0 / COUNT(*), 2) AS rate_3d_pct,
           SUM(will_fail_7d)  AS labels_7d,
           ROUND(SUM(will_fail_7d)  * 100.0 / COUNT(*), 2) AS rate_7d_pct,
           SUM(will_fail_14d) AS labels_14d,
           ROUND(SUM(will_fail_14d) * 100.0 / COUNT(*), 2) AS rate_14d_pct,
           SUM(CASE WHEN incident_count_30d_past > 0 THEN 1 ELSE 0 END) AS rows_with_inc_history,
           ROUND(SUM(CASE WHEN incident_count_30d_past > 0 THEN 1 ELSE 0 END) * 100.0 / COUNT(*), 2) AS inc_coverage_pct
    FROM {catalog}.gold.device_ps1_daily
""")
s = status(r, min_rows=100000)
print(f"PS1  device_ps1_daily           {s}")
if "error" not in r:
    print(f"       distinct_devices={fmt(r.get('distinct_devices'))}  range={r.get('min_day')} to {r.get('max_day')}")
    print(f"       will_fail_3d={fmt(r.get('labels_3d'))} ({r.get('rate_3d_pct')}%)  will_fail_7d={fmt(r.get('labels_7d'))} ({r.get('rate_7d_pct')}%)  will_fail_14d={fmt(r.get('labels_14d'))} ({r.get('rate_14d_pct')}%)")
    print(f"       s24_inc_coverage={fmt(r.get('rows_with_inc_history'))} rows ({r.get('inc_coverage_pct')}%)  [TVM > GATE > VALIDATOR=0% expected]")
results.append(("gold", "PS1", "device_ps1_daily", s, r))

# -- PS2 device_ps2_chains ---------------------------------------------------
r = check(f"""
    SELECT COUNT(*) AS total_rows,
           COUNT(DISTINCT DEVICE_ID) AS distinct_devices,
           MIN(transit_day) AS min_day,
           MAX(transit_day) AS max_day,
           ROUND(AVG(chain_length), 1) AS avg_chain_length,
           SUM(CASE WHEN has_cash_cascade THEN 1 ELSE 0 END) AS cash_cascades,
           SUM(CASE WHEN incident_count_7d_past > 0 THEN 1 ELSE 0 END) AS rows_with_inc_history,
           ROUND(SUM(CASE WHEN incident_count_7d_past > 0 THEN 1 ELSE 0 END) * 100.0 / COUNT(*), 2) AS inc_coverage_pct
    FROM {catalog}.gold.device_ps2_chains
""")
s = status(r, min_rows=100000)
print(f"PS2  device_ps2_chains          {s}")
if "error" not in r:
    print(f"       distinct_devices={fmt(r.get('distinct_devices'))}  range={r.get('min_day')} to {r.get('max_day')}")
    print(f"       avg_chain_length={r.get('avg_chain_length')}  cash_cascades={fmt(r.get('cash_cascades'))}")
    print(f"       s24_inc_coverage={r.get('inc_coverage_pct')}% rows with incident history")
results.append(("gold", "PS2", "device_ps2_chains", s, r))

# -- PS3 device_ps3_incident -------------------------------------------------
r = check(f"""
    SELECT COUNT(*) AS total_rows,
           COUNT(DISTINCT DEVICE_ID) AS distinct_devices,
           MIN(AE_START_DTM) AS min_date,
           MAX(AE_START_DTM) AS max_date,
           COUNT(DISTINCT failure_level) AS distinct_levels
    FROM {catalog}.gold.device_ps3_incident
""")
s = status(r, min_rows=1000)
print(f"PS3  device_ps3_incident        {s}")
if "error" not in r:
    print(f"       distinct_devices={fmt(r.get('distinct_devices'))}  range={r.get('min_date')} to {r.get('max_date')}")
    print(f"       distinct_failure_levels={fmt(r.get('distinct_levels'))}")
results.append(("gold", "PS3", "device_ps3_incident", s, r))

# -- PS4 device_ps4_hourly ---------------------------------------------------
r = check(f"""
    SELECT COUNT(*) AS total_rows,
           COUNT(DISTINCT DEVICE_ID) AS distinct_devices,
           MIN(transit_day) AS min_day,
           MAX(transit_day) AS max_day,
           SUM(ensemble_anomaly_flag) AS anomaly_hours,
           ROUND(SUM(ensemble_anomaly_flag) * 100.0 / COUNT(*), 2) AS anomaly_rate_pct,
           SUM(CASE WHEN incident_count_7d_past > 0 THEN 1 ELSE 0 END) AS rows_with_inc_history,
           ROUND(SUM(CASE WHEN incident_count_7d_past > 0 THEN 1 ELSE 0 END) * 100.0 / COUNT(*), 2) AS inc_coverage_pct
    FROM {catalog}.gold.device_ps4_hourly
""")
s = status(r, min_rows=100000)
print(f"PS4  device_ps4_hourly          {s}")
if "error" not in r:
    print(f"       distinct_devices={fmt(r.get('distinct_devices'))}  range={r.get('min_day')} to {r.get('max_day')}")
    print(f"       anomaly_hours={fmt(r.get('anomaly_hours'))}  anomaly_rate={r.get('anomaly_rate_pct')}%")
    print(f"       s24_inc_coverage={r.get('inc_coverage_pct')}% hourly rows with incident history")
results.append(("gold", "PS4", "device_ps4_hourly", s, r))

# -- PS5 device_ps5_component ------------------------------------------------
r = check(f"""
    SELECT COUNT(*) AS total_rows,
           COUNT(DISTINCT DEVICE_ID) AS distinct_devices,
           COUNT(DISTINCT COMPONENT_TYPE_NAME) AS component_types,
           SUM(CASE WHEN is_censored THEN 1 ELSE 0 END) AS censored_rows,
           ROUND(AVG(days_to_failure), 1) AS avg_days_to_failure,
           SUM(CASE WHEN total_inc_device > 0 THEN 1 ELSE 0 END) AS rows_with_inc_history,
           ROUND(SUM(CASE WHEN total_inc_device > 0 THEN 1 ELSE 0 END) * 100.0 / COUNT(*), 2) AS inc_coverage_pct
    FROM {catalog}.gold.device_ps5_component
""")
s = status(r, min_rows=1000)
print(f"PS5  device_ps5_component       {s}")
if "error" not in r:
    print(f"       distinct_devices={fmt(r.get('distinct_devices'))}  component_types={fmt(r.get('component_types'))}")
    print(f"       censored_rows={fmt(r.get('censored_rows'))}  avg_days_to_failure={r.get('avg_days_to_failure')}")
    print(f"       s15_inc_coverage={r.get('inc_coverage_pct')}% components with lifetime incident history")
results.append(("gold", "PS5", "device_ps5_component", s, r))

# COMMAND ----------
# =============================================================================
# SUMMARY
# =============================================================================
print()
print("=" * 70)
print("SUMMARY")
print("=" * 70)

ok   = [(l,c,t) for l,c,t,s,_ in results if s.startswith("OK")]
warn = [(l,c,t) for l,c,t,s,_ in results if s.startswith("WARN")]
fail = [(l,c,t) for l,c,t,s,_ in results if s.startswith("FAIL")]

print(f"  OK    : {len(ok):2d} tables")
print(f"  WARN  : {len(warn):2d} tables")
print(f"  FAIL  : {len(fail):2d} tables")
print(f"  TOTAL : {len(results):2d} tables checked  (silver S01-S29 + gold PS1-PS5)")

if warn:
    print("\nWARNINGS:")
    for l,c,t in warn:
        print(f"  [{l}] {c} {t}")

if fail:
    print("\nFAILURES:")
    for l,c,t in fail:
        print(f"  [{l}] {c} {t}")

if not warn and not fail:
    print("\nAll tables passed validation.")
