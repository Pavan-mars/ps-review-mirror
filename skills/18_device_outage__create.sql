-- =============================================================================
-- silver.device_outage
-- Out-of-service outage intervals derived from device_event_enriched
--
-- Source: mars_dev.silver.device_event_enriched (S16 -- must be created first)
--         mars_dev.silver.incident_root_cause   (S17 -- for is_chargeable / failure_level)
--
-- Logic:
--   - Filter to is_hardware_oos_event = TRUE (hardware-only OOS from S07 dim_event_type)
--     Commanded/maintenance codes excluded (106, 110, 151, 208, 519, 1603, 1604).
--   - outage_end = CLEAR_DTM when present; else LEAD() to next OOS event for same device
--   - duration_min capped at 10,080 min (7 days) -- raw CLEAR_DTM has outliers up to 975 years
--   - is_resolved = TRUE when outage_end is not NULL
--   - is_chargeable: joined from incident_root_cause on DEVICE_ID + transit_day
--     Michael R2-1: failure_level > 0 = real hardware failure (chargeable)
--                   failure_level = 0 or NULL = non-chargeable (operational/informational)
--     Aggregation: MAX(AE_FAILURE_LEVEL) per device+day (handles M:1 when multiple events/day)
--
-- Notes:
--   - EDW.AVAILABILITY_EVENTS (645,410 rows) is the EDW-curated outage summary;
--     this table derives outages from raw device events for finer granularity
--   - For PS1 rolling features, join back on device_id + transit_day
--   - Dependency chain: S06 -> S07 -> S16 -> S17 -> S18
--
-- Validation run 2026-06-15 -- bugs fixed from original:
--   BUG 1: silver.*      -> mars_dev.silver.*
--   BUG 2: EXTRACT(EPOCH FROM (a-b))/60 -> (unix_timestamp(a)-unix_timestamp(b))/60.0
--   BUG 3: duration_min had no outlier guard (CLEAR_DTM can be 975y in future)
--           -> added LEAST(..., 10080.0) cap and outage_end >= outage_start guard
--   BUG 4: CREATE INDEX -> not supported on Delta; use OPTIMIZE/ZORDER after load
--
-- Michael R2 changes applied 2026-06-24:
--   R2-1: Added is_chargeable + failure_level via LEFT JOIN to incident_root_cause
--         PS1 positive label = is_hardware_oos_event = TRUE AND is_chargeable = TRUE
-- =============================================================================

DROP TABLE IF EXISTS mars_dev.silver.device_outage;

CREATE TABLE mars_dev.silver.device_outage AS
WITH

-- -- OOS events from device_event_enriched (hardware faults only) -------------
oos_events AS (
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

-- -- Resolve outage end via CLEAR_DTM or next OOS event -----------------------
with_end AS (
    SELECT
        oo.*,
        COALESCE(
            oo.CLEAR_DTM,
            LEAD(oo.outage_start) OVER (
                PARTITION BY oo.DEVICE_ID
                ORDER BY oo.outage_start
            )
        )                                       AS outage_end
    FROM oos_events oo
),

-- -- Chargeable flag from incident_root_cause (Michael R2-1) ------------------
-- MAX(AE_FAILURE_LEVEL) per device+day resolves M:1 when device has multiple
-- availability events on the same day.
-- failure_level > 0 = real hardware failure chargeable to SLA
-- failure_level = 0 or NULL = operational / non-chargeable
--
-- FIX 2026-09-04 (DQ finding: null_vs_zero_conflation_check found 74% of device-days where an
-- incident genuinely occurred with AE_FAILURE_LEVEL unknown were being silently shown as
-- failure_level=0 / is_chargeable=FALSE downstream -- i.e. "we don't know" was being trained as
-- "confirmed no failure". gold_ps1_null_zero_impact_check confirmed 100% of these (31,593/31,593)
-- reached Gold's device_ps1_daily feature columns, and device_ps5_component pulls the same
-- defective columns directly. Root cause: the original COALESCE(...,0) below could not distinguish
-- "no incident at all" (0 is correct) from "an incident exists but its failure level is unknown"
-- (should stay NULL, not become 0). Fix adds two extra aggregates so the final SELECT below can
-- tell the two cases apart. OLD version commented out, not deleted:
--
-- fail_lvl AS (
--     SELECT
--         device_id,
--         transit_day,
--         MAX(AE_FAILURE_LEVEL)                   AS max_failure_level
--     FROM mars_dev.silver.incident_root_cause
--     GROUP BY device_id, transit_day
-- )
fail_lvl AS (
    SELECT
        device_id,
        transit_day,
        MAX(AE_FAILURE_LEVEL)                   AS max_failure_level,
        COUNT(*)                                AS incident_row_count,
        COUNT(AE_FAILURE_LEVEL)                 AS known_failure_level_count
    FROM mars_dev.silver.incident_root_cause
    GROUP BY device_id, transit_day
)

-- -- Final output --------------------------------------------------------------
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

    -- Duration in minutes; capped at 7 days
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

    -- Chargeable / PS1 label (Michael R2-1)
    -- PS1 positive failure label = is_hardware_oos_event = TRUE AND is_chargeable = TRUE
    --
    -- FIX 2026-09-04: see the fail_lvl CTE comment above for full context. This now distinguishes
    -- three real cases instead of collapsing two different ones into the same 0/FALSE value:
    --   1. No incident row at all for this device-day       -> failure_level=0, is_chargeable=FALSE
    --      (genuinely correct -- nothing happened, this is a real negative)
    --   2. Incident row(s) exist, but AE_FAILURE_LEVEL unknown for all of them
    --      -> failure_level=NULL, is_chargeable=NULL (stays an honest "unknown", not a false 0)
    --   3. Incident row(s) exist with a known AE_FAILURE_LEVEL -> use the real value
    -- NOTE FOR REVIEWERS: is_chargeable can now be NULL, where it was previously always TRUE/FALSE.
    -- Downstream code filtering WHERE is_chargeable = TRUE is unaffected. Code filtering
    -- WHERE is_chargeable = FALSE will now correctly exclude the "unknown" rows too (previously
    -- those rows were wrongly included as if confirmed non-chargeable) -- this is the intended
    -- fix, but please confirm no downstream logic depends on the old (incorrect) FALSE behavior
    -- before this is deployed. device_ps5_component pulls these columns directly and will see this
    -- change; device_ps1_daily's will_fail_Xd label itself is unaffected (already confirmed to not
    -- depend on failure_level/is_chargeable, see R7-1 in device_ps1_daily__create.sql).
    -- OLD version commented out, not deleted:
    -- COALESCE(fl.max_failure_level, 0)           AS failure_level,
    -- (COALESCE(fl.max_failure_level, 0) > 0)     AS is_chargeable,
    CASE
        WHEN fl.device_id IS NULL THEN 0
        WHEN fl.known_failure_level_count = 0 THEN NULL
        ELSE fl.max_failure_level
    END                                          AS failure_level,
    CASE
        WHEN fl.device_id IS NULL THEN FALSE
        WHEN fl.known_failure_level_count = 0 THEN NULL
        ELSE (fl.max_failure_level > 0)
    END                                          AS is_chargeable,

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

FROM with_end we
LEFT JOIN fail_lvl fl
    ON  fl.device_id   = we.DEVICE_ID
    AND fl.transit_day = we.transit_day;

-- Post-load optimisation:
-- OPTIMIZE mars_dev.silver.device_outage ZORDER BY (DEVICE_ID, outage_start);
