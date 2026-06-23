-- =============================================================================
-- silver.device_outage
-- Out-of-service outage intervals derived from device_event_enriched
--
-- Source: mars_dev.silver.device_event_enriched (S03 — must be created first)
--
-- Logic:
--   - Filter to is_hardware_oos_event = TRUE (hardware-only OOS from S02 dim_event_type)
--     Commanded/maintenance codes excluded (106, 110, 151, 208, 519, 1603, 1604).
--     This eliminates the ~84× over-count vs EDW.AVAILABILITY_EVENTS (645,410 rows).
--   - outage_end = CLEAR_DTM when present (all events in sample had CLEAR_DTM),
--     else LEAD() to next OOS event start for same device
--   - duration_min capped at 10,080 min (7 days) — raw CLEAR_DTM has outliers
--     up to 975 years; values beyond 7 days are data quality, not real durations
--   - is_resolved = TRUE when outage_end is not NULL
--
-- Notes:
--   - EDW.AVAILABILITY_EVENTS (645,410 rows) is EDW-curated outage summary;
--     this table derives outages from raw device events for finer granularity
--   - For PS1 rolling features, join back on device_id + transit_day
--   - Dependency chain: S01 dim_device → S02 dim_event_type → S03 device_event_enriched → S04
--
-- Validation run 2026-06-15 — bugs fixed from original:
--   BUG 1: silver.*      → mars_dev.silver.*
--   BUG 2: EXTRACT(EPOCH FROM (a-b))/60 → (unix_timestamp(a)-unix_timestamp(b))/60.0
--   BUG 3: duration_min had no outlier guard (CLEAR_DTM can be 975y in future)
--           → added LEAST(..., 10080.0) cap and outage_end >= outage_start guard
--   BUG 4: CREATE INDEX   → not supported on Delta; use OPTIMIZE/ZORDER after load
--           Recommended: OPTIMIZE mars_dev.silver.device_outage
--                          ZORDER BY (DEVICE_ID, outage_start);
-- =============================================================================

DROP TABLE IF EXISTS mars_dev.silver.device_outage;

CREATE TABLE mars_dev.silver.device_outage AS
WITH oos_events AS (
    SELECT
        DW_DEVICE_EVENT_ID,
        DEVICE_KEY,
        DEVICE_ID,
        mars_device_category,
        DEVICE_NAME,
        FACILITY_ID,
        FACILITY_NAME,
        OPERATOR_ID,
        OPERATOR_NAME,
        transit_day,
        EVENT_DTM                               AS outage_start,
        CLEAR_DTM,
        AUTOMATIC_CLEAR_FLAG,
        CLEAR_EVENT_STATE_TYPE_NAME,
        component_subsystem,
        severity,
        EVENT_STATE_TYPE_NAME,
        EVENT_TYPE_ID,
        EVENT_TYPE_NAME,
        COMPONENT_TYPE_ID,
        COMPONENT_TYPE_NAME,
        COMPONENT_SERIAL_NBR,
        COMPONENT_POSITION,
        EXTENDED_DATA_SHORT,
        EXTENDED_DATA_LONG,
        ROUTE_NUMBER,
        BUS_ID,
        STOP_POINT_ID,
        LATITUDE,
        LONGITUDE,
        MESSAGE_ID,
        EVENT_REASON_CODE_ID
    FROM mars_dev.silver.device_event_enriched
    WHERE is_hardware_oos_event = TRUE
),
with_end AS (
    SELECT
        oo.*,
        -- outage_end: explicit CLEAR_DTM first; fallback to next OOS event start
        COALESCE(
            oo.CLEAR_DTM,
            LEAD(oo.outage_start) OVER (
                PARTITION BY oo.DEVICE_ID
                ORDER BY oo.outage_start
            )
        )                                       AS outage_end
    FROM oos_events oo
)
SELECT
    we.DW_DEVICE_EVENT_ID                       AS source_event_id,
    we.DEVICE_KEY,
    we.DEVICE_ID,
    we.mars_device_category,
    we.DEVICE_NAME,
    we.FACILITY_ID,
    we.FACILITY_NAME,
    we.OPERATOR_ID,
    we.OPERATOR_NAME,
    we.transit_day,
    we.outage_start,
    we.outage_end,

    -- Duration in minutes; capped at 7 days (same rationale as S03)
    -- outage_end >= outage_start guard excludes negative durations
    CASE
        WHEN we.outage_end IS NOT NULL
         AND we.outage_end >= we.outage_start
        THEN LEAST(
               (unix_timestamp(we.outage_end) - unix_timestamp(we.outage_start)) / 60.0,
               10080.0
             )
        ELSE NULL
    END                                         AS duration_min,

    -- Resolution flags
    (we.CLEAR_DTM IS NOT NULL)                  AS has_explicit_clear,
    (we.AUTOMATIC_CLEAR_FLAG = 1)               AS is_auto_cleared,
    (we.outage_end IS NOT NULL)                 AS is_resolved,
    we.CLEAR_EVENT_STATE_TYPE_NAME,

    -- Event classification
    we.component_subsystem,
    we.severity,
    we.EVENT_STATE_TYPE_NAME,
    we.EVENT_TYPE_ID,
    we.EVENT_TYPE_NAME,
    we.COMPONENT_TYPE_ID,
    we.COMPONENT_TYPE_NAME,
    we.COMPONENT_SERIAL_NBR,
    we.COMPONENT_POSITION,
    we.EXTENDED_DATA_SHORT,
    we.EXTENDED_DATA_LONG,
    we.ROUTE_NUMBER,
    we.BUS_ID,
    we.STOP_POINT_ID,
    we.LATITUDE,
    we.LONGITUDE,
    we.MESSAGE_ID,
    we.EVENT_REASON_CODE_ID

FROM with_end we;

-- Post-load optimisation:
-- OPTIMIZE mars_dev.silver.device_outage ZORDER BY (DEVICE_ID, outage_start);
