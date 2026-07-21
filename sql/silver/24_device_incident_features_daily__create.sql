-- =============================================================================
-- S24: silver.device_incident_features_daily
-- S-code: S24 | Build file: 24 | Status: READY (source S15 live 2026-07-07)
--
-- PURPOSE:
-- Backward-looking daily incident feature aggregations per device.
-- Transforms per-incident rows in silver.incident_history (S15) into
-- per-device-day rolling window features safe for ML training (no leakage).
-- Used by gold PS1, PS2, PS4 to enrich device-day feature vectors with
-- incident history context from ServiceNow.
--
-- SOURCE (silver):
-- mars_dev.silver.incident_history (S15) -- rebuilt 2026-07-20 on
-- servicenow_incident (see 15_incident_history__create.sql); row/col counts
-- above are stale pending a fresh check against the rebuilt table.
-- Filtered to: DEVICE_KEY IS NOT NULL, 2023-07-01+
-- Device incidents only: rows across TVM (473 devices) + GATE (824 devices)
-- VALIDATOR (BMV*): 0 rows -- no ServiceNow incidents exist for bus validators
--
-- GRAIN:
-- One row per (DEVICE_KEY, transit_day) where at least one incident occurred.
-- Rows: ~96,840 | Devices: ~1,297 (TVM + GATE only)
-- Gold left-join fills zeros for device-days with no incident history.
--
-- WINDOW LOGIC (all BACKWARD-ONLY — no data leakage):
-- ROWS BETWEEN N PRECEDING AND 1 PRECEDING -> excludes current day
-- Ordering by transit_day (incident date) within each DEVICE_KEY partition.
-- Windows are row-based (not day-based) because grain is already one row per
-- device per incident day — consecutive rows are consecutive incident days.
--
-- ⚠️ VALIDATOR COVERAGE (confirmed 2026-07-07):
-- BMV* devices (Bus Mobile Validators, 3,290 devices, 1,869,019 PS1 rows)
-- have ZERO incidents in incident_history. ServiceNow does not log incidents
-- for bus validators. All incident feature columns will be NULL / zero-filled
-- after gold LEFT JOIN. VALIDATOR uses z-score proxy target in PS1 ML pipeline.
--
-- COLUMN NOTES:
-- count cols -> COALESCE to 0 (zero = no prior incidents, valid state)
-- avg_mttr_* -> NULL when no prior incidents (intentional, not zero-filled)
-- min_priority_* -> NULL when no prior incidents (1=Critical, 4=Low)
-- days_since_last -> NULL for first-ever incident row per device (no LAG available)
-- incident_rate_trend -> positive = accelerating faults; negative = settling down
--
-- PS IMPACT:
-- PS1 +all 12 cols : TVM 17.75% device-day coverage; GATE 3.33%; VALIDATOR 0%
-- PS2 +5 cols : incident_count_7d_past, chargeable_count_30d_past,
-- avg_mttr_30d_past, min_priority_30d_past, days_since_last_incident
-- PS4 +5 cols : incident_count_7d_past, chargeable_count_7d_past,
-- chargeable_count_30d_past, avg_mttr_30d_past, days_since_last_incident
-- PS3 / PS5 : no change (PS3 is incident-based already; PS5 needs component grain)
--
-- BUILD ORDER:
-- Requires: mars_dev.silver.incident_history (S15) — must exist before running
-- Feeds : mars_dev.gold.device_ps1_daily, device_ps2_chains, device_ps4_hourly
--
-- DEVICE CATEGORY COVERAGE -- cross-reference note (added 2026-07-07, validation pass):
-- This table's TVM+GATE-only coverage (see SOURCE/GRAIN above) differs from its two
-- sibling tables, S19 maintenance_ledger and S20 usage_lifecycle_daily -- by design,
-- not by inconsistency. Each table's device-category coverage is a direct consequence
-- of which source data actually contains rows for that category, not an arbitrary
-- filter choice:
--
-- S24 device_incident_features_daily (this table) -> TVM + GATE only
-- (driven off S15 incident_history; VALIDATOR/BMV buses have 0 ServiceNow
-- incidents logged -- confirmed 2026-07-07, see the warning above)
-- S19 maintenance_ledger -> TVM + GATE only
-- (no VALIDATOR/BMV maintenance-mode or tech-login events found in
-- DEVICE_EVENT to date)
-- S20 usage_lifecycle_daily -> VALIDATOR + GATE only
-- (driven off S10 metric_daily, which only carries METRIC_ID=401 readings
-- for VALIDATOR + GATE devices -- TVM has 0 metric_daily rows)
--
-- Net effect: no single device category, including TVM and GATE, has identical
-- coverage across all three tables, and that's expected given the underlying
-- source data. Anyone joining across S19/S20/S24 should confirm which specific
-- combination of tables they need before assuming matching device coverage --
-- VALIDATOR appears only in S20, never here or in S19.
-- =============================================================================

DROP TABLE IF EXISTS mars_dev.silver.device_incident_features_daily;

CREATE TABLE mars_dev.silver.device_incident_features_daily
USING DELTA
PARTITIONED BY (DEVICE_KEY)
COMMENT 'Silver Table 24. Backward-looking daily incident features per device. Source: S15 incident_history. TVM + GATE only.'
TBLPROPERTIES (
'delta.autoOptimize.optimizeWrite' = 'true',
'delta.autoOptimize.autoCompact' = 'true',
'quality' = 'silver',
'pipeline.source' = 'mars_dev.silver.incident_history',
'pipeline.created_by' = 'mars_servicenow_integration',
'pipeline.table_number' = '24'
)
AS

WITH

-- -- SOURCE: incident_history filtered to device incidents only ---------------
-- Excludes: non-device rows (DEVICE_KEY IS NULL), pre-2023-07
-- Row count comment above is stale (pending recheck) -- source table rebuilt
-- 2026-07-20 on servicenow_incident; date floor moved 2024-01-01 -> 2023-07-01
-- to match the extended PS1 training window.
src AS (
SELECT
DEVICE_KEY,
CAST(incident_date AS DATE) AS transit_day,
is_chargeable,
time_to_resolve_minutes,
priority,
is_major_incident,
event_code_id
FROM mars_dev.silver.incident_history
WHERE DEVICE_KEY IS NOT NULL
AND incident_date IS NOT NULL
AND incident_date >= '2023-07-01'
),

-- -- DAILY AGGREGATION: one row per DEVICE_KEY + transit_day ------------------
daily_agg AS (
SELECT
DEVICE_KEY,
transit_day,
COUNT(*) AS inc_count_day,
SUM(is_chargeable) AS chargeable_count_day,
AVG(time_to_resolve_minutes) AS avg_ttresolve_day,
MIN(priority) AS min_priority_day,
SUM(is_major_incident) AS major_inc_count_day,
COUNT(DISTINCT event_code_id) AS distinct_event_codes_day
FROM src
GROUP BY DEVICE_KEY, transit_day
),

-- -- WINDOW FEATURES: backward-only rolling aggregations per device -----------
-- Gap 2 RC3 fix: RANGE INTERVAL (true calendar-day windows, not incident-row count).
-- Old ROWS BETWEEN was spanning 38-176 actual calendar days for "7d"/"30d" labels.
-- CAST(transit_day AS TIMESTAMP): RANGE INTERVAL requires TIMESTAMP ORDER BY col in Spark SQL;
-- DATE type raises DATATYPE_MISMATCH.RANGE_FRAME_INVALID_TYPE (SQLSTATE 42K09).
windowed AS (
SELECT
DEVICE_KEY,
transit_day,

-- -- Incident volume ---------------------------------------------------
SUM(inc_count_day) OVER (
PARTITION BY DEVICE_KEY ORDER BY CAST(transit_day AS TIMESTAMP)
RANGE BETWEEN INTERVAL 7 DAYS PRECEDING AND INTERVAL 1 DAY PRECEDING) AS incident_count_7d_past,

SUM(inc_count_day) OVER (
PARTITION BY DEVICE_KEY ORDER BY CAST(transit_day AS TIMESTAMP)
RANGE BETWEEN INTERVAL 30 DAYS PRECEDING AND INTERVAL 1 DAY PRECEDING) AS incident_count_30d_past,

SUM(inc_count_day) OVER (
PARTITION BY DEVICE_KEY ORDER BY CAST(transit_day AS TIMESTAMP)
RANGE BETWEEN INTERVAL 90 DAYS PRECEDING AND INTERVAL 1 DAY PRECEDING) AS incident_count_90d_past,

-- -- Chargeable outages ------------------------------------------------
SUM(chargeable_count_day) OVER (
PARTITION BY DEVICE_KEY ORDER BY CAST(transit_day AS TIMESTAMP)
RANGE BETWEEN INTERVAL 7 DAYS PRECEDING AND INTERVAL 1 DAY PRECEDING) AS chargeable_count_7d_past,

SUM(chargeable_count_day) OVER (
PARTITION BY DEVICE_KEY ORDER BY CAST(transit_day AS TIMESTAMP)
RANGE BETWEEN INTERVAL 30 DAYS PRECEDING AND INTERVAL 1 DAY PRECEDING) AS chargeable_count_30d_past,

-- -- Mean Time to Resolve (minutes) ------------------------------------
AVG(avg_ttresolve_day) OVER (
PARTITION BY DEVICE_KEY ORDER BY CAST(transit_day AS TIMESTAMP)
RANGE BETWEEN INTERVAL 7 DAYS PRECEDING AND INTERVAL 1 DAY PRECEDING) AS avg_mttr_7d_past,

AVG(avg_ttresolve_day) OVER (
PARTITION BY DEVICE_KEY ORDER BY CAST(transit_day AS TIMESTAMP)
RANGE BETWEEN INTERVAL 30 DAYS PRECEDING AND INTERVAL 1 DAY PRECEDING) AS avg_mttr_30d_past,

-- -- Severity ----------------------------------------------------------
MIN(min_priority_day) OVER (
PARTITION BY DEVICE_KEY ORDER BY CAST(transit_day AS TIMESTAMP)
RANGE BETWEEN INTERVAL 30 DAYS PRECEDING AND INTERVAL 1 DAY PRECEDING) AS min_priority_30d_past,

-- -- Major incidents ---------------------------------------------------
SUM(major_inc_count_day) OVER (
PARTITION BY DEVICE_KEY ORDER BY CAST(transit_day AS TIMESTAMP)
RANGE BETWEEN INTERVAL 30 DAYS PRECEDING AND INTERVAL 1 DAY PRECEDING) AS major_inc_count_30d_past,

-- -- Fault code diversity ----------------------------------------------
SUM(distinct_event_codes_day) OVER (
PARTITION BY DEVICE_KEY ORDER BY CAST(transit_day AS TIMESTAMP)
RANGE BETWEEN INTERVAL 30 DAYS PRECEDING AND INTERVAL 1 DAY PRECEDING) AS distinct_event_codes_30d,

-- -- Recency -----------------------------------------------------------
LAG(transit_day, 1) OVER (
PARTITION BY DEVICE_KEY ORDER BY transit_day) AS _prev_transit_day

FROM daily_agg
)

-- -- FINAL SELECT: cast to DDL types, zero-fill counts, derive trend ----------
SELECT
CAST(DEVICE_KEY AS DECIMAL(8,0)) AS DEVICE_KEY,
transit_day,

-- -- Incident volume (COALESCE 0: no prior history is a valid state) ------
COALESCE(CAST(incident_count_7d_past AS BIGINT), 0) AS incident_count_7d_past,
COALESCE(CAST(incident_count_30d_past AS BIGINT), 0) AS incident_count_30d_past,
COALESCE(CAST(incident_count_90d_past AS BIGINT), 0) AS incident_count_90d_past,

-- -- Chargeable outages (COALESCE 0) --------------------------------------
COALESCE(CAST(chargeable_count_7d_past AS BIGINT), 0) AS chargeable_count_7d_past,
COALESCE(CAST(chargeable_count_30d_past AS BIGINT), 0) AS chargeable_count_30d_past,

-- -- MTTR (NULL kept: model treats missing as unknown, not zero) ----------
CAST(avg_mttr_7d_past AS DOUBLE) AS avg_mttr_7d_past,
CAST(avg_mttr_30d_past AS DOUBLE) AS avg_mttr_30d_past,

-- -- Severity (NULL kept: no history is different from priority=4) --------
CAST(min_priority_30d_past AS INT) AS min_priority_30d_past,

-- -- Major incidents (COALESCE 0) -----------------------------------------
COALESCE(CAST(major_inc_count_30d_past AS BIGINT), 0) AS major_inc_count_30d_past,

-- -- Fault diversity (COALESCE 0) -----------------------------------------
COALESCE(CAST(distinct_event_codes_30d AS BIGINT), 0) AS distinct_event_codes_30d,

-- -- Recency (NULL for first-ever incident row per device) ----------------
CAST(DATEDIFF(transit_day, _prev_transit_day) AS INT) AS days_since_last_incident,

-- -- Trend: positive = faults accelerating; negative = settling -----------
(COALESCE(incident_count_7d_past, 0) / 7.0) -
(COALESCE(incident_count_30d_past, 0) / 30.0) AS incident_rate_trend

FROM windowed;

-- Post-load verification:
-- SELECT
-- COUNT(*) AS total_rows, -- stale expected value (pre-2026-07-20 rebuild), recheck
-- COUNT(DISTINCT DEVICE_KEY) AS distinct_devices, -- stale expected value, recheck
-- MIN(transit_day) AS earliest, -- Expect ~2023-07-01 now (was 2024-01-01)
-- MAX(transit_day) AS latest, -- stale expected value, recheck
-- SUM(CASE WHEN incident_count_7d_past IS NULL THEN 1 ELSE 0 END) AS null_7d, -- Expect 0
-- SUM(CASE WHEN incident_count_30d_past IS NULL THEN 1 ELSE 0 END) AS null_30d, -- Expect 0
-- SUM(CASE WHEN avg_mttr_30d_past IS NULL THEN 1 ELSE 0 END) AS null_mttr,-- Expect ~1,297 (first rows)
-- ROUND(AVG(incident_count_30d_past), 2) AS avg_inc_30d, -- Expect ~37
-- ROUND(AVG(chargeable_count_30d_past), 2) AS avg_chg_30d, -- Expect ~22
-- ROUND(AVG(avg_mttr_30d_past), 0) AS avg_mttr_min -- Expect ~600
-- FROM mars_dev.silver.device_incident_features_daily;

-- Post-load optimisation:
-- OPTIMIZE mars_dev.silver.device_incident_features_daily ZORDER BY (transit_day);
