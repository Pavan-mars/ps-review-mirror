-- =============================================================================
-- silver.usage_lifecycle_daily (S20)
-- Cumulative device lifecycle and wear features - daily grain
-- Primary input for PS5 (Remaining Useful Life) wear and age features
--
-- Sources (all silver - must be built first):
-- mars_dev.silver.metric_daily (S10) - daily tap counts (usage proxy)
-- mars_dev.silver.device_outage (S18) - daily failure/outage summary
-- mars_dev.silver.maintenance_ledger (S19) - daily maintenance visit summary
-- mars_dev.silver.dim_device (S06) - device age reference
--
-- Build order: S06 -> S07 -> S16 -> S18 -> S10 -> S19 -> S20
--
-- Why these columns?
-- PS5 RUL needs "how worn is this device today" - the key inputs are:
-- - cumulative_tap_count: total lifecycle usage (wear proxy)
-- - days_in_service: device age in operational days
-- - days_since_last_failure: recency of last repair episode
-- - cumulative_failure_count: lifetime failure frequency
-- - cumulative_maintenance_visits: preventive maintenance record
-- - cumulative_outage_min: total downtime to date
-- These are right-censored survival features - they describe each device's
-- state on each day, feeding the Weibull / CoxPH / DeepSurv models.
--
-- Grain: (DEVICE_KEY, transit_day) - one row per device per operational day
-- Only days with metric_daily records are included (VALIDATOR + GATE devices only,
-- ~2,794 distinct DEVICE_KEYs). Devices with no metric data have no rows here.
-- If all devices are needed, drive from dim_device and LEFT JOIN metric_daily.
--
-- Validation run 2026-06-23:
-- metric_daily data: 2024-01-01 to 2025-11-07 (bronze ingestion stalled, see S10 note)
-- device_outage: filtered to is_hardware_oos_event = TRUE (hardware failures only)
-- maintenance_ledger: REPAIR_EPISODE + TECH_LOGIN + MAINTENANCE_MODE
--
-- DEVICE CATEGORY COVERAGE -- cross-reference note (added 2026-07-07, validation pass):
-- This table's VALIDATOR+GATE-only coverage (see "Grain" above -- it's driven off
-- S10 metric_daily, so TVM never appears here) differs from its two sibling tables,
-- S19 maintenance_ledger and S24 device_incident_features_daily -- by design, not by
-- inconsistency. Each table's device-category coverage is a direct consequence of
-- which source data actually contains rows for that category, not an arbitrary
-- filter choice:
--
-- S20 usage_lifecycle_daily (this table) -> VALIDATOR + GATE only
-- (driven off S10 metric_daily, which only carries METRIC_ID=401 readings
-- for VALIDATOR + GATE devices -- TVM has 0 metric_daily rows)
-- S19 maintenance_ledger -> TVM + GATE only
-- (no VALIDATOR/BMV maintenance-mode or tech-login events found in
-- DEVICE_EVENT to date)
-- S24 device_incident_features_daily -> TVM + GATE only
-- (driven off S15 incident_history; VALIDATOR/BMV buses have 0 ServiceNow
-- incidents logged -- confirmed 2026-07-07)
--
-- Net effect: no single device category, including TVM and GATE, has identical
-- coverage across all three tables, and that's expected given the underlying
-- source data. Anyone joining across S19/S20/S24 should confirm which specific
-- combination of tables they need before assuming matching device coverage --
-- TVM appears in S19/S24 but never in this table; VALIDATOR appears only here.
-- This is also why PS5 (which needs TVM too) cannot source wear/age features for
-- TVM from this table alone -- see sql/silver/09_hw_config_current__create.sql
-- and the PS5 gold table for the TVM-inclusive component-age path instead.
-- =============================================================================

DROP TABLE IF EXISTS mars_dev.silver.usage_lifecycle_daily;

CREATE TABLE mars_dev.silver.usage_lifecycle_daily AS
WITH

-- -- Daily outage summary per device (hardware failures only via S18) ----------
daily_outage AS (
SELECT
do.DEVICE_ID,
do.transit_day,
COUNT(*) AS daily_failure_count,
SUM(COALESCE(do.duration_min, 0)) AS daily_outage_min
FROM mars_dev.silver.device_outage do
GROUP BY do.DEVICE_ID, do.transit_day
),

-- -- Daily maintenance visit count per device (S19) ---------------------------
-- Counts each ledger_type separately and combined
daily_maint AS (
SELECT
ml.DEVICE_ID,
ml.ledger_date AS transit_day,
COUNT(*) AS daily_maint_events,
SUM(CASE WHEN ml.ledger_type = 'TECH_LOGIN' THEN 1 ELSE 0 END)
AS daily_tech_logins,
SUM(CASE WHEN ml.ledger_type = 'MAINTENANCE_MODE' THEN 1 ELSE 0 END)
AS daily_maint_mode_events,
SUM(COALESCE(ml.duration_min, 0)) AS daily_maint_duration_min
FROM mars_dev.silver.maintenance_ledger ml
WHERE ml.source_table = 'DEVICE_EVENT'
GROUP BY ml.DEVICE_ID, ml.ledger_date
),

-- -- Base: metric_daily gives the set of (device, day) with usage data ---------
-- Driving from metric_daily means only VALIDATOR + GATE devices are included
base AS (
SELECT
md.DEVICE_KEY,
md.DEVICE_ID,
md.transit_day,
md.mars_device_category,
md.FACILITY_ID,
md.FACILITY_NAME,
md.OPERATOR_ID,
md.OPERATOR_NAME,
md.m401_daily_txn_count,
COALESCE(do.daily_failure_count, 0) AS daily_failure_count,
COALESCE(do.daily_outage_min, 0) AS daily_outage_min,
COALESCE(dm.daily_maint_events, 0) AS daily_maint_events,
COALESCE(dm.daily_tech_logins, 0) AS daily_tech_logins,
COALESCE(dm.daily_maint_mode_events, 0) AS daily_maint_mode_events,
COALESCE(dm.daily_maint_duration_min, 0) AS daily_maint_duration_min
FROM mars_dev.silver.metric_daily md
LEFT JOIN daily_outage do
ON do.DEVICE_ID = md.DEVICE_ID
AND do.transit_day = md.transit_day
LEFT JOIN daily_maint dm
ON dm.DEVICE_ID = md.DEVICE_ID
AND dm.transit_day = md.transit_day
)

-- -- Final: compute cumulative lifecycle features via window functions ----------
SELECT
b.DEVICE_KEY,
b.DEVICE_ID,
b.transit_day,
b.mars_device_category,
b.FACILITY_ID,
b.FACILITY_NAME,
b.OPERATOR_ID,
b.OPERATOR_NAME,

-- -- Daily snapshot --------------------------------------------------------
b.m401_daily_txn_count,
b.daily_failure_count,
b.daily_outage_min,
b.daily_maint_events,
b.daily_tech_logins,
b.daily_maint_mode_events,
b.daily_maint_duration_min,

-- -- Cumulative lifecycle counters (key PS5 features) ---------------------
-- days_in_service: sequential operational day number for this device
ROW_NUMBER() OVER (
PARTITION BY b.DEVICE_KEY
ORDER BY b.transit_day
) AS days_in_service,

-- cumulative_tap_count: lifetime usage - primary wear proxy for PS5
SUM(b.m401_daily_txn_count) OVER (
PARTITION BY b.DEVICE_KEY
ORDER BY b.transit_day
ROWS UNBOUNDED PRECEDING
) AS cumulative_tap_count,

-- cumulative_failure_count: lifetime hardware failures
SUM(b.daily_failure_count) OVER (
PARTITION BY b.DEVICE_KEY
ORDER BY b.transit_day
ROWS UNBOUNDED PRECEDING
) AS cumulative_failure_count,

-- cumulative_outage_min: total hardware downtime to date
SUM(b.daily_outage_min) OVER (
PARTITION BY b.DEVICE_KEY
ORDER BY b.transit_day
ROWS UNBOUNDED PRECEDING
) AS cumulative_outage_min,

-- cumulative_maintenance_visits: total maintenance events to date
SUM(b.daily_maint_events) OVER (
PARTITION BY b.DEVICE_KEY
ORDER BY b.transit_day
ROWS UNBOUNDED PRECEDING
) AS cumulative_maint_events,

-- -- Recency features (PS5 survival model inputs) --------------------------
-- days_since_last_failure: 0 on a failure day; NULL before first failure
-- Computed as: transit_day - last day with daily_failure_count > 0
DATEDIFF(
b.transit_day,
LAST_VALUE(
CASE WHEN b.daily_failure_count > 0 THEN b.transit_day END
) IGNORE NULLS OVER (
PARTITION BY b.DEVICE_KEY
ORDER BY b.transit_day
ROWS UNBOUNDED PRECEDING
)
) AS days_since_last_failure,

-- days_since_last_maintenance: days since last tech login or maintenance-mode event
DATEDIFF(
b.transit_day,
LAST_VALUE(
CASE WHEN b.daily_maint_events > 0 THEN b.transit_day END
) IGNORE NULLS OVER (
PARTITION BY b.DEVICE_KEY
ORDER BY b.transit_day
ROWS UNBOUNDED PRECEDING
)
) AS days_since_last_maintenance,

-- -- Rolling 30-day windows (PS1 + PS5 feature set) -----------------------
SUM(b.daily_failure_count) OVER (
PARTITION BY b.DEVICE_KEY
ORDER BY b.transit_day
ROWS BETWEEN 29 PRECEDING AND CURRENT ROW
) AS failure_count_30d,

SUM(b.m401_daily_txn_count) OVER (
PARTITION BY b.DEVICE_KEY
ORDER BY b.transit_day
ROWS BETWEEN 29 PRECEDING AND CURRENT ROW
) AS tap_count_30d,

SUM(b.daily_maint_events) OVER (
PARTITION BY b.DEVICE_KEY
ORDER BY b.transit_day
ROWS BETWEEN 29 PRECEDING AND CURRENT ROW
) AS maint_events_30d

FROM base b;

-- Post-build optimisation:
-- OPTIMIZE mars_dev.silver.usage_lifecycle_daily ZORDER BY (DEVICE_KEY, transit_day);

-- Post-build verification:
-- SELECT
-- COUNT(*) AS total_rows,
-- COUNT(DISTINCT DEVICE_KEY) AS distinct_devices,
-- MIN(transit_day) AS earliest_day,
-- MAX(transit_day) AS latest_day,
-- MAX(days_in_service) AS max_days_in_service,
-- MAX(cumulative_tap_count) AS max_lifetime_taps,
-- MAX(cumulative_failure_count) AS max_lifetime_failures,
-- ROUND(AVG(days_since_last_failure), 1) AS avg_days_since_failure,
-- SUM(CASE WHEN days_since_last_failure IS NULL THEN 1 ELSE 0 END)
-- AS rows_before_first_failure
-- FROM mars_dev.silver.usage_lifecycle_daily;
-- Expected: ~2,794 devices, data 2024-01-01 to 2025-11-07 (metric_daily range)
