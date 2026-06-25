-- =============================================================================
-- gold.device_ps5_component
-- PS5 -- Survival Analysis / Component Lifetime: feature table for ALL device types
-- Grain: (device_id, component_serial_nbr) -- one row per installed component
-- Target: days_to_failure + is_censored (survival analysis targets)
-- Device types: TVM, GATE, VALIDATOR  (mars_device_category column)
-- Reader models: sourced from device_event_enriched COMPONENT_TYPE grain (reader-component view)
--   READER is NOT a mars_device_category -- confirmed Michael 2026-06-22.
--   Reader failures live inside parent TVM/BMV/RVG/HBG/SAG/RTL as COMPONENT_TYPE events.
-- Sources (silver):
--   S18  mars_dev.silver.device_outage
--   S09  mars_dev.silver.hw_config_current
-- Sources (bronze):
--   mars_dev.bronze.ncs_stage_cashbox_tracking  (TVM/VALIDATOR only)
--
-- ! CRITICAL DATA GAPS:
--   1. SVN_STAGE.WORK_ORDER, MAINTENANCE_ACTIVITY_COUNT = 0 rows -> no work-order data
--   2. EDW.MAINTENANCE_ACTIVITY, MAINTENANCE_ACTIVITY_COUNT = 0 rows -> no maintenance log
--   WORKAROUND: Component lifetime from REPORTED_CHANGED_DTM (NCS_STAGE hw config)
--              + OOS outages as failure proxy (silver.device_outage S18)
--              + is_censored = TRUE if no OOS outage found (still running)
--
-- ! COMPONENT_TYPE_NAME / DESC NOTE:
--   These columns do NOT exist in silver.hw_config_current (S09).
--   S09 has COMPONENT_DESCRIPTION (free-text), COMPONENT_SERIAL_NBR.
--   COMPONENT_TYPE_NAME comes from silver.device_outage (S18) via component_failures join;
--   NULL for components with no failures (is_censored = TRUE).
--
-- Fixes applied 2026-06-19 (pre-build read of S18/S09 before validation):
--   FIX 1: gold./silver. prefixes -> mars_dev.gold. / mars_dev.silver.
--   FIX 2: silver.hw_config_current -> mars_dev.silver.hw_config_current
--   FIX 3: silver.device_outage    -> mars_dev.silver.device_outage
--   FIX 4: EXTRACT(EPOCH FROM (next_outage_start - outage_start)) / 86400.0
--           -> (unix_timestamp(next_outage_start) - unix_timestamp(outage_start)) / 86400.0
--   FIX 5: EXTRACT(EPOCH FROM (cf.first_failure_dtm - hw.REPORTED_CHANGED_DTM)) / 86400.0
--           -> (unix_timestamp(cf.first_failure_dtm)
--              - unix_timestamp(CAST(hw.REPORTED_CHANGED_DTM AS TIMESTAMP))) / 86400.0
--   FIX 6: STRING_AGG(DISTINCT component_subsystem, ',')
--           -> array_join(array_sort(array_distinct(collect_list(component_subsystem))), ',')
--   FIX 7: hwc.COMPONENT_TYPE_NAME in all_hw -> REMOVED (not in hw_config_current S09)
--   FIX 8: hwc.COMPONENT_TYPE_DESC in all_hw -> REMOVED (not in hw_config_current S09)
--   FIX 9: hw.COMPONENT_TYPE_NAME in final SELECT -> cf.COMPONENT_TYPE_NAME
--           (available from component_failures via device_outage S18)
--           NULL for censored components (no failures found)
--   FIX 10: hw.COMPONENT_TYPE_DESC in final SELECT -> REMOVED entirely
--   FIX 11: bronze.ncs_cashbox_tracking -> mars_dev.bronze.ncs_stage_cashbox_tracking
--   FIX 12: TRANSACTION_TYPE LIKE '%JAM%' -> CASHBOX_TYPE_ID-based features:
--            Type 1 = Bill Cashbox, Type 2 = Coin Cashbox, Type 5 = Bus Cashbox
--            cashbox_jams -> bill_cashbox_events (TYPE_ID = 1; bill handling = jam indicator)
--   FIX 13: ct.TRANSACTION_DTM (column does not exist) ->
--            MIN(ct.CASHBOX_INSERTED_DTM) / MAX(COALESCE(ct.CASHBOX_REMOVED_DTM, ct.CASHBOX_INSERTED_DTM))
--   FIX 14: cashbox_jam_count in final SELECT -> bill_cashbox_events (consistent with PS2 naming)
--   FIX 15: CREATE INDEX (x6) -> removed; not supported on Delta
-- =============================================================================

DROP TABLE IF EXISTS mars_dev.gold.device_ps5_component;

CREATE TABLE mars_dev.gold.device_ps5_component AS
WITH all_hw AS (
    -- FIX 7+8: COMPONENT_TYPE_NAME and COMPONENT_TYPE_DESC removed -- not in S09
    SELECT
        hwc.DEVICE_ID,
        hwc.DEVICE_KEY,
        hwc.DEVICE_NAME,
        hwc.FACILITY_ID,
        hwc.FACILITY_NAME,
        hwc.OPERATOR_ID,
        hwc.OPERATOR_NAME,
        hwc.device_serial_number,
        hwc.COMPONENT_DESCRIPTION,
        hwc.COMPONENT_SERIAL_NBR,
        hwc.component_age_days,
        hwc.LAST_REPORTED_DTM,
        hwc.REPORTED_CHANGED_DTM,
        hwc.hw_source,
        hwc.mars_device_category
    FROM mars_dev.silver.hw_config_current hwc
    WHERE hwc.mars_device_category IN ('TVM','GATE','VALIDATOR')
      AND hwc.COMPONENT_SERIAL_NBR IS NOT NULL
),
-- Pre-compute LEAD before aggregation (Spark cannot nest window functions inside aggregates)
component_outages_lead AS (
    SELECT
        do_.DEVICE_ID,
        do_.COMPONENT_SERIAL_NBR,
        do_.COMPONENT_TYPE_NAME,
        do_.outage_start,
        do_.duration_min,
        do_.component_subsystem,
        do_.is_chargeable,
        do_.failure_level,
        LEAD(do_.outage_start) OVER (
            PARTITION BY do_.DEVICE_ID, do_.COMPONENT_SERIAL_NBR
            ORDER BY do_.outage_start
        )                                                             AS next_outage_start
    FROM mars_dev.silver.device_outage do_
    WHERE do_.mars_device_category IN ('TVM','GATE','VALIDATOR')
      AND do_.COMPONENT_SERIAL_NBR IS NOT NULL
),
component_failures AS (
    SELECT
        DEVICE_ID,
        COMPONENT_SERIAL_NBR,
        COMPONENT_TYPE_NAME,
        COUNT(*)                                                      AS failure_count,
        MIN(outage_start)                                             AS first_failure_dtm,
        MAX(outage_start)                                             AS last_failure_dtm,
        SUM(COALESCE(duration_min, 0))                                AS total_failure_min,
        AVG(COALESCE(duration_min, 0))                                AS avg_failure_min,
        -- FIX 4: EXTRACT(EPOCH FROM (...)) / 86400 -> unix_timestamp subtraction
        AVG(
            CASE WHEN next_outage_start IS NOT NULL
            THEN (unix_timestamp(next_outage_start) - unix_timestamp(outage_start)) / 86400.0
            END
        )                                                             AS avg_days_between_failures,
        -- FIX 6: STRING_AGG(DISTINCT ...) -> array_join + array_sort + array_distinct + collect_list
        array_join(
            array_sort(array_distinct(collect_list(component_subsystem))), ','
        )                                                             AS failure_subsystems,
        -- Chargeable failure counts (R2-1: is_chargeable = failure_level > 0)
        SUM(CASE WHEN is_chargeable = TRUE THEN 1 ELSE 0 END)        AS chargeable_failure_count,
        MAX(COALESCE(failure_level, 0))                               AS max_failure_level
    FROM component_outages_lead
    GROUP BY DEVICE_ID, COMPONENT_SERIAL_NBR, COMPONENT_TYPE_NAME
),
-- Cashbox context (TVM/VALIDATOR only; LEFT JOIN returns 0/NULL for GATE/READER)
-- FIX 11+12+13: bronze.ncs_cashbox_tracking -> ncs_stage_cashbox_tracking;
--               TRANSACTION_TYPE->CASHBOX_TYPE_ID; TRANSACTION_DTM->CASHBOX_INSERTED/REMOVED_DTM
cashbox_stats AS (
    SELECT
        ct.DEVICE_ID,
        COUNT(*)                                                      AS total_cashbox_events,
        COUNT(CASE WHEN ct.CASHBOX_TYPE_ID = 1 THEN 1 END)           AS bill_cashbox_events,
        COUNT(CASE WHEN ct.CASHBOX_TYPE_ID = 2 THEN 1 END)           AS coin_cashbox_events,
        COUNT(CASE WHEN ct.CASHBOX_TYPE_ID = 5 THEN 1 END)           AS bus_cashbox_events,
        SUM(COALESCE(CAST(ct.DUMP_COUNT AS INT), 0))                  AS total_dump_count,
        MIN(ct.CASHBOX_INSERTED_DTM)                                  AS first_cashbox_event,
        MAX(COALESCE(ct.CASHBOX_REMOVED_DTM, ct.CASHBOX_INSERTED_DTM)) AS last_cashbox_event
    FROM mars_dev.bronze.ncs_stage_cashbox_tracking ct
    WHERE ct.DEVICE_ID IS NOT NULL
    GROUP BY ct.DEVICE_ID
)
SELECT
    hw.DEVICE_ID,
    hw.DEVICE_KEY,
    hw.COMPONENT_DESCRIPTION,
    hw.COMPONENT_SERIAL_NBR,
    hw.DEVICE_NAME,
    hw.FACILITY_ID,
    hw.FACILITY_NAME,
    hw.OPERATOR_ID,
    hw.OPERATOR_NAME,
    hw.device_serial_number,
    -- FIX 9: hw.COMPONENT_TYPE_NAME -> cf.COMPONENT_TYPE_NAME (from device_outage via join)
    --         NULL when is_censored=TRUE (no failures found in device_outage)
    cf.COMPONENT_TYPE_NAME,
    -- FIX 10: hw.COMPONENT_TYPE_DESC -> removed (not in any silver table)
    hw.component_age_days,
    hw.LAST_REPORTED_DTM,
    hw.REPORTED_CHANGED_DTM,
    hw.hw_source,
    hw.mars_device_category,
    -- Failure history
    COALESCE(cf.failure_count, 0)                                     AS failures_total,
    cf.first_failure_dtm,
    cf.last_failure_dtm,
    COALESCE(cf.total_failure_min, 0)                                 AS total_failure_min,
    COALESCE(cf.avg_failure_min, 0)                                   AS avg_failure_min,
    cf.avg_days_between_failures,
    cf.failure_subsystems,
    -- SURVIVAL TARGETS
    -- FIX 5: EXTRACT(EPOCH FROM (...)) / 86400 -> unix_timestamp subtraction
    CASE
        WHEN cf.failure_count > 0 AND hw.REPORTED_CHANGED_DTM IS NOT NULL
        THEN GREATEST(
            0.0,
            (unix_timestamp(cf.first_failure_dtm)
             - unix_timestamp(CAST(hw.REPORTED_CHANGED_DTM AS TIMESTAMP))) / 86400.0
        )
        ELSE hw.component_age_days
    END                                                               AS days_to_failure,
    (cf.failure_count IS NULL OR cf.failure_count = 0)                AS is_censored,
    -- is_not_censored: explicit alias for PS5 notebook compatibility (EVENT_COL = "is_not_censored")
    NOT (cf.failure_count IS NULL OR cf.failure_count = 0)            AS is_not_censored,
    -- Chargeable component failures (R2-1, added 2026-06-24)
    COALESCE(cf.chargeable_failure_count, 0)                         AS chargeable_failure_count,
    COALESCE(cf.max_failure_level, 0)                                AS max_failure_level,
    cf.avg_days_between_failures                                      AS mtbf_days,
    -- Cashbox context (TVM/VALIDATOR only; FIX 14: cashbox_jam_count -> bill_cashbox_events)
    COALESCE(cs.total_cashbox_events, 0)                              AS cashbox_events_total,
    COALESCE(cs.bill_cashbox_events, 0)                               AS bill_cashbox_events,
    COALESCE(cs.coin_cashbox_events, 0)                               AS coin_cashbox_events,
    COALESCE(cs.bus_cashbox_events, 0)                                AS bus_cashbox_events,
    COALESCE(cs.total_dump_count, 0)                                  AS total_dump_count,
    cs.first_cashbox_event,
    cs.last_cashbox_event,
    -- Data gap flags
    FALSE                                                             AS has_work_order_data,
    FALSE                                                             AS has_maintenance_log,
    TRUE                                                              AS hw_from_ncs_stage
FROM all_hw hw
LEFT JOIN component_failures cf
    ON cf.DEVICE_ID            = hw.DEVICE_ID
   AND cf.COMPONENT_SERIAL_NBR = hw.COMPONENT_SERIAL_NBR
LEFT JOIN cashbox_stats cs
    ON cs.DEVICE_ID = hw.DEVICE_ID
WHERE hw.component_age_days IS NOT NULL;

-- FIX 15: CREATE INDEX (x6) removed -- not supported on Delta tables
-- Post-build:
-- OPTIMIZE mars_dev.gold.device_ps5_component ZORDER BY (DEVICE_ID, COMPONENT_SERIAL_NBR);
--
-- Post-build verification:
-- SELECT
--     mars_device_category,
--     COUNT(*)                                                        AS total_rows,
--     COUNT(DISTINCT DEVICE_ID)                                      AS distinct_devices,
--     SUM(CASE WHEN is_censored = FALSE THEN 1 END)                  AS has_failures,
--     SUM(CASE WHEN is_censored = TRUE  THEN 1 END)                  AS censored,
--     ROUND(AVG(component_age_days), 0)                              AS avg_age_days,
--     ROUND(AVG(days_to_failure), 0)                                 AS avg_days_to_failure,
--     ROUND(AVG(failures_total), 2)                                  AS avg_failures_per_component,
--     ROUND(AVG(mtbf_days), 0)                                       AS avg_mtbf_days,
--     SUM(CASE WHEN COMPONENT_TYPE_NAME IS NOT NULL THEN 1 END)      AS has_component_type
-- FROM mars_dev.gold.device_ps5_component
-- GROUP BY mars_device_category;
-- Note: COMPONENT_TYPE_NAME = NULL when is_censored=TRUE (no failure in device_outage)
-- Note: cashbox_events_total > 0 only for TVM/VALIDATOR devices
-- Note: S10 note: 41.4% dim_device match rate for DEVICE_KEY; all devices should appear
--       via hw_config_current (LEFT JOIN from dim_device in S09)
