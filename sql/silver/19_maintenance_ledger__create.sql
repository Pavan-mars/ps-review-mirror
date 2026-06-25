-- =============================================================================
-- silver.maintenance_ledger  (S19)
-- Unified maintenance and repair history for PS5 RUL and PS1 feature engineering
--
-- Source A - Repair episodes (corrective maintenance):
--   mars_dev.bronze.edw_availability_events  (645K rows, 24 cols)
--   Each row = a device OOS->restore episode curated from ServiceNow
--   = the primary "failure+repair" event log for PS5 label and time-between-failures
--
-- Source B - Maintenance-mode + tech-login events (maintenance visits):
--   mars_dev.silver.device_event_enriched  (S16)
--   Filtered to is_commanded_oos_event = TRUE:
--     106 = Employee Logon    (tech login to device)
--     110 = Commanded OOS     (operator-initiated OOS)
--     151 = Maintenance Mode  (device in scheduled maintenance)
--     208 = SCT Commanded OOS (SCT commanded maintenance)
--     519 = OOS by Command (CHU)
--   Confirmed by Michael (2026-06-22): "tech logins and the device entering/exiting
--   maintenance mode are captured in EDW.DEVICE_EVENT"
--
-- Context (Michael's reply 2026-06-22):
--   No separate work-order/parts/MAXIMO system for V2.
--   Maintenance history = availability episodes + maintenance-mode/tech-login device events.
--   See Maintenance_History_Source_22Jun2026.md for full details.
--
-- NOTE: EVENT_TYPE_IDs for maintenance slice are identified via is_commanded_oos_event
--   flag in S07/S16 (added 2026-06-23). Run the analysis queries in the MD doc to
--   confirm additional maintenance-related IDs before expanding the filter.
--
-- Grain: one row per maintenance or repair event
-- Produced columns:
--   ledger_type      - REPAIR_EPISODE | TECH_LOGIN | MAINTENANCE_MODE | COMMANDED_OOS
--   event_dtm        - start of the event / repair episode
--   event_end_dtm    - end (CLEAR_DTM for device events, END_DTM for availability episodes)
--   duration_min     - event duration in minutes (NULL if end unknown)
--   failure_level    - 0-3 from availability_events; NULL for device-event rows
--   component_subsystem - from device_event_enriched; NULL for availability-event rows
--
-- Consumed by:
--   PS5 RUL: time-between-failures, repair frequency, time-since-last-repair features
--   PS1:     maintenance_visit_count rolling window, days_since_last_maintenance
--
-- Build order: S06 -> S07 -> S16 -> S19
-- =============================================================================

DROP TABLE IF EXISTS mars_dev.silver.maintenance_ledger;

CREATE TABLE mars_dev.silver.maintenance_ledger AS

-- -- SOURCE A: Repair episodes from EDW.AVAILABILITY_EVENTS -------------------
-- Each row = a completed OOS->restore episode (failure + corrective repair)
-- This is the primary failure history for PS5 RUL label and time-between-failures
SELECT
    ae.DEVICE_ID,
    dd.DEVICE_KEY,
    dd.mars_device_category,
    dd.FACILITY_ID,
    dd.FACILITY_NAME,
    dd.OPERATOR_ID,
    dd.OPERATOR_NAME,
    -- Fix (2026-06-23): removed redundant outer TO_DATE(<DATE>, 'yyyy-MM-dd') wrapper -
    -- TO_DATE with a format string expects a STRING and returns NULL on a DATE input.
    -- The CASE already yields a DATE.
    CASE
        WHEN LENGTH(CAST(ae.TRANSIT_DAY_KEY AS STRING)) = 6
        THEN TO_DATE(CAST(ae.TRANSIT_DAY_KEY AS STRING), 'yyMMdd')
        WHEN LENGTH(CAST(ae.TRANSIT_DAY_KEY AS STRING)) = 8
        THEN TO_DATE(CAST(ae.TRANSIT_DAY_KEY AS STRING), 'yyyyMMdd')
        ELSE NULL
    END                                                    AS ledger_date,
    ae.START_DTM                                            AS event_dtm,
    ae.END_DTM                                              AS event_end_dtm,
    CASE
        WHEN ae.END_DTM IS NOT NULL
         AND ae.START_DTM IS NOT NULL
         AND ae.END_DTM >= ae.START_DTM
        THEN (unix_timestamp(ae.END_DTM) - unix_timestamp(ae.START_DTM)) / 60.0
        ELSE NULL
    END                                                     AS duration_min,
    'REPAIR_EPISODE'                                        AS ledger_type,
    ae.FAILURE_LEVEL                                        AS failure_level,
    CAST(NULL AS STRING)                                    AS component_subsystem,
    CAST(NULL AS BIGINT)                                    AS event_type_id,
    CAST(NULL AS STRING)                                    AS event_type_name,
    ae.FAULT_STATE                                          AS fault_state,
    ae.FAULT_DESCRIPTION                                    AS fault_description,
    ae.EVENT_ID                                             AS source_event_id,
    'AVAILABILITY_EVENTS'                                   AS source_table

FROM mars_dev.bronze.edw_availability_events ae
LEFT JOIN mars_dev.silver.dim_device dd
    ON dd.DEVICE_ID  = ae.DEVICE_ID
   AND dd.is_current = TRUE

UNION ALL

-- -- SOURCE B: Maintenance-mode + tech-login events from DEVICE_EVENT ---------
-- is_commanded_oos_event = TRUE: codes 106/110/151/208/519 (confirmed 2026-06-23)
-- These are maintenance visits and tech logins - NOT hardware failures
-- duration_min = time spent in maintenance mode (event -> clear)
SELECT
    dee.DEVICE_ID,
    dee.DEVICE_KEY,
    dee.mars_device_category,
    dee.FACILITY_ID,
    dee.FACILITY_NAME,
    dee.OPERATOR_ID,
    dee.OPERATOR_NAME,
    dee.transit_day                                         AS ledger_date,
    dee.EVENT_DTM                                           AS event_dtm,
    dee.CLEAR_DTM                                           AS event_end_dtm,
    dee.duration_to_clear_min                               AS duration_min,
    CASE dee.EVENT_TYPE_ID
        WHEN 106 THEN 'TECH_LOGIN'
        WHEN 151 THEN 'MAINTENANCE_MODE'
        ELSE           'COMMANDED_OOS'
    END                                                     AS ledger_type,
    CAST(NULL AS INT)                                       AS failure_level,
    dee.component_subsystem,
    CAST(dee.EVENT_TYPE_ID AS BIGINT)                       AS event_type_id,
    dee.EVENT_TYPE_NAME                                     AS event_type_name,
    dee.EVENT_STATE_TYPE_NAME                               AS fault_state,
    dee.EXTENDED_DATA_SHORT                                 AS fault_description,
    dee.DW_DEVICE_EVENT_ID                                  AS source_event_id,
    'DEVICE_EVENT'                                          AS source_table

FROM mars_dev.silver.device_event_enriched dee
WHERE dee.is_commanded_oos_event = TRUE;

-- Post-build optimisation:
-- OPTIMIZE mars_dev.silver.maintenance_ledger ZORDER BY (DEVICE_ID, event_dtm);

-- Post-build verification:
-- SELECT
--     source_table,
--     ledger_type,
--     COUNT(*)                    AS rows,
--     COUNT(DISTINCT DEVICE_ID)   AS devices,
--     MIN(event_dtm)              AS earliest,
--     MAX(event_dtm)              AS latest,
--     ROUND(AVG(duration_min), 1) AS avg_duration_min
-- FROM mars_dev.silver.maintenance_ledger
-- GROUP BY source_table, ledger_type
-- ORDER BY source_table, ledger_type;
-- Expected:
--   AVAILABILITY_EVENTS / REPAIR_EPISODE   ~645K rows
--   DEVICE_EVENT / TECH_LOGIN              depends on volume of code 106 events
--   DEVICE_EVENT / MAINTENANCE_MODE        depends on volume of code 151 events
--   DEVICE_EVENT / COMMANDED_OOS           codes 110/208/519
