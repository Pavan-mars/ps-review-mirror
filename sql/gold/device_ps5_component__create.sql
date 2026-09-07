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
--   S15  mars_dev.silver.incident_history  (NEW 2026-07-07 -- R4)
--         Lifetime device-level ServiceNow aggregation — different signal from S18 device_outage:
--         S18 = NCS telemetry hardware OOS events; S15 = field technician response records.
--         Adds: total_inc_device, chargeable_inc_device, avg_mttr_device, distinct_event_codes_device
--         Join grain: DEVICE_KEY only (lifetime aggregates, not rolling windows — no date join)
--         NOTE: S24 (daily rolling windows) is NOT used here — grain mismatch with component rows.
--   S13  mars_dev.silver.tap_event_daily   (NEW 2026-07-07 -- R5)
--         Lifetime device-level usage intensity — total taps, avg daily taps, active service days.
--         Higher avg_daily_taps = faster mechanical wear -> shorter expected component lifespan.
--         Covers TVM, GATE, VALIDATOR (bus farebox taps). Join grain: DEVICE_ID only.
--   S28  mars_dev.silver.device_mttr        (NEW 2026-07-17 -- R6)
--         Rolling MTTR per device-failure-day aggregated to lifetime device level.
--         Fills VALIDATOR MTTR gap: S15 ServiceNow = 0% VALIDATOR coverage; S28 = 100%.
--         Covers TVM + GATE + VALIDATOR. Join grain: DEVICE_KEY only.
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
        hwc.mars_device_category,
        -- PS5-GAP fix: derive component type from COMPONENT_DESCRIPTION for censored components
        -- Fills COMPONENT_TYPE_NAME when there's no failure in device_outage (is_censored=TRUE)
        CASE
            WHEN UPPER(hwc.COMPONENT_DESCRIPTION) LIKE '%BILLACCEPTOR%'
              OR UPPER(hwc.COMPONENT_DESCRIPTION) LIKE '%BILL_ACCEPTOR%'
              OR UPPER(hwc.COMPONENT_DESCRIPTION) LIKE '%BHU%'        THEN 'BHU'
            WHEN UPPER(hwc.COMPONENT_DESCRIPTION) LIKE '%COIN%'
              OR UPPER(hwc.COMPONENT_DESCRIPTION) LIKE '%COIN_MECH%'  THEN 'CHU'
            WHEN UPPER(hwc.COMPONENT_DESCRIPTION) LIKE '%PRINTER%'
              OR UPPER(hwc.COMPONENT_DESCRIPTION) LIKE '%RECEIPT%'    THEN 'PRINTER'
            WHEN UPPER(hwc.COMPONENT_DESCRIPTION) LIKE '%CSC%'
              OR UPPER(hwc.COMPONENT_DESCRIPTION) LIKE '%CARD%READER%'
              OR UPPER(hwc.COMPONENT_DESCRIPTION) LIKE '%READER%'     THEN 'CSC_READER'
            WHEN UPPER(hwc.COMPONENT_DESCRIPTION) LIKE '%GATE%'
              OR UPPER(hwc.COMPONENT_DESCRIPTION) LIKE '%TURNSTILE%'
              OR UPPER(hwc.COMPONENT_DESCRIPTION) LIKE '%MECH_BOARD%' THEN 'GATE_MECH'
            WHEN UPPER(hwc.COMPONENT_DESCRIPTION) LIKE '%SAM%'
              OR UPPER(hwc.COMPONENT_DESCRIPTION) LIKE '%SECURITY%MODULE%' THEN 'SAM'
            WHEN UPPER(hwc.COMPONENT_DESCRIPTION) LIKE '%SCRST%'      THEN 'SCRST'
            ELSE NULL
        END                                                           AS derived_component_type_hw
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
-- R4 (2026-07-07): device-level ServiceNow lifetime summary from S15 incident_history
-- Grain: one row per DEVICE_KEY (lifetime aggregates — NOT rolling windows like S24)
-- Covers TVM + GATE only (VALIDATOR/BMV* = 0 ServiceNow incidents confirmed 2026-07-07)
-- Separate from device_outage (S18): these are technician-filed tickets, not NCS telemetry events
-- UPDATE 2026-07-20: date floor moved 2024-01-01 -> 2023-07-01 -- S15 rebuilt on
-- servicenow_incident, which has real TVM/GATE incident data back into 2023H2
-- (unlike the prior cta_servicenow_incident source); see 15_incident_history__create.sql
device_inc_lifetime AS (
    SELECT
        DEVICE_KEY,
        COUNT(*)                                                      AS total_inc_device,
        SUM(is_chargeable)                                            AS chargeable_inc_device,
        AVG(time_to_resolve_minutes)                                  AS avg_mttr_device,
        COUNT(DISTINCT event_code_id)                                 AS distinct_event_codes_device,
        MIN(incident_date)                                            AS first_incident_date,
        MAX(incident_date)                                            AS last_incident_date,
        SUM(is_major_incident)                                        AS major_inc_device
    FROM mars_dev.silver.incident_history
    WHERE DEVICE_KEY IS NOT NULL
      AND incident_date IS NOT NULL
      AND incident_date >= '2023-07-01'
    GROUP BY DEVICE_KEY
),
-- R5 (2026-07-07): device-level usage intensity from S13 tap_event_daily
-- Higher usage = faster mechanical wear -> shorter component survival time
-- Covers all device types (TVM/GATE/VALIDATOR bus farebox)
-- UPDATE 2026-07-20: date floor moved 2024-01-01 -> 2023-07-01 -- edw_abp_tap
-- (S13's bronze source) confirmed to have real data back to 2023-07-01 after
-- bronze rerun (previously hard-started 2024-01-01, same issue as edw_device_event)
device_usage_lifetime AS (
    SELECT
        DEVICE_ID,
        SUM(tap_count)                                                    AS lifetime_tap_count,
        AVG(tap_count)                                                    AS avg_daily_taps,
        COUNT(DISTINCT transit_day)                                       AS active_service_days,
        MAX(transit_day)                                                  AS last_tap_day
    FROM mars_dev.silver.tap_event_daily
    WHERE DEVICE_ID IS NOT NULL
      AND transit_day >= '2023-07-01'
    GROUP BY DEVICE_ID
),
-- R6 (2026-07-17): device-level MTTR lifetime from S28 device_mttr
-- Aggregated to one row per DEVICE_KEY so it joins cleanly to component grain.
-- avg_downtime_per_fail_day_min: mean of daily downtime across all failure days
-- avg_rolling_mttr_30d_min: avg of the per-day 30d rolling MTTR (smoothed MTTR signal)
-- Fills VALIDATOR gap: S15 ServiceNow = 0% VALIDATOR; S28 = 100% via device_event_enriched.
device_mttr_lifetime AS (
    SELECT
        DEVICE_KEY,
        ROUND(AVG(downtime_minutes),  1)  AS avg_downtime_per_fail_day_min,
        ROUND(AVG(avg_downtime_30d),  1)  AS avg_rolling_mttr_30d_min,
        ROUND(AVG(avg_downtime_90d),  1)  AS avg_rolling_mttr_90d_min,
        MAX(downtime_minutes)             AS max_downtime_ever_min,
        COUNT(*)                          AS total_failure_days_s28,
        MAX(failure_date)                 AS last_failure_date_s28
    FROM mars_dev.silver.device_mttr
    GROUP BY DEVICE_KEY
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
    -- PS5-GAP fix: COALESCE with derived_component_type_hw for censored components
    COALESCE(cf.COMPONENT_TYPE_NAME, hw.derived_component_type_hw)     AS COMPONENT_TYPE_NAME,
    -- FIX 10: hw.COMPONENT_TYPE_DESC -> removed (not in any silver table)
    GREATEST(0, hw.component_age_days)                                  AS component_age_days,
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
    TRUE                                                              AS hw_from_ncs_stage,
    -- R5 (2026-07-07): usage intensity from S13 tap_event_daily — all device types
    -- NULL for devices with no tap records in S13 (rare — most active devices have taps)
    COALESCE(dul.lifetime_tap_count,  0)                             AS lifetime_tap_count,
    COALESCE(dul.avg_daily_taps,      0)                             AS avg_daily_taps,
    COALESCE(dul.active_service_days, 0)                             AS active_service_days,
    dul.last_tap_day,
    -- R4 (2026-07-07): ServiceNow lifetime aggregates from S15 (NOT from S24 — grain mismatch)
    -- Device-level totals across the full 2024+ window; NULL for VALIDATOR (BMV* = 0 incidents)
    -- Use alongside NCS-telemetry failure counts: technician tickets are an independent signal
    COALESCE(dil.total_inc_device,           0) AS total_inc_device,
    COALESCE(dil.chargeable_inc_device,      0) AS chargeable_inc_device,
    dil.avg_mttr_device,
    COALESCE(dil.distinct_event_codes_device,0) AS distinct_event_codes_device,
    dil.first_incident_date,
    dil.last_incident_date,
    COALESCE(dil.major_inc_device,           0) AS major_inc_device,
    -- R6 (2026-07-17): device MTTR from S28 — covers VALIDATOR (fills S15 0% gap)
    -- avg_downtime_per_fail_day_min: mean downtime on actual failure days (all 3 categories)
    -- avg_rolling_mttr_30d_min: avg of the per-day 30d rolling MTTR across all failure days
    -- COALESCE 0 for devices with no failure history in S28 (never-failed in training window)
    COALESCE(dmttr.avg_downtime_per_fail_day_min, 0) AS avg_downtime_per_fail_day_min,
    COALESCE(dmttr.avg_rolling_mttr_30d_min,      0) AS avg_rolling_mttr_30d_min,
    COALESCE(dmttr.avg_rolling_mttr_90d_min,      0) AS avg_rolling_mttr_90d_min,
    COALESCE(dmttr.max_downtime_ever_min,         0) AS max_downtime_ever_min,
    COALESCE(dmttr.total_failure_days_s28,        0) AS total_failure_days_s28,
    dmttr.last_failure_date_s28
FROM all_hw hw
LEFT JOIN component_failures cf
    ON cf.DEVICE_ID            = hw.DEVICE_ID
   AND cf.COMPONENT_SERIAL_NBR = hw.COMPONENT_SERIAL_NBR
LEFT JOIN cashbox_stats cs
    ON cs.DEVICE_ID = hw.DEVICE_ID
-- R4 (2026-07-07): device-level ServiceNow lifetime summary — join on DEVICE_KEY only
LEFT JOIN device_inc_lifetime dil
    ON dil.DEVICE_KEY = hw.DEVICE_KEY
-- R5 (2026-07-07): usage intensity from S13 — join on DEVICE_ID only
LEFT JOIN device_usage_lifetime dul
    ON dul.DEVICE_ID = hw.DEVICE_ID
-- R6 (2026-07-17): device MTTR lifetime from S28 — join on DEVICE_KEY
-- Same value broadcast to all component rows for the same device (device-level signal)
LEFT JOIN device_mttr_lifetime dmttr
    ON dmttr.DEVICE_KEY = hw.DEVICE_KEY
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
