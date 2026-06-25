-- =============================================================================
-- silver.kpi_avail_enriched
-- Availability events enriched with relief and ServiceNow data
--
-- Sources (mars_dev.bronze catalog):
--   EDW.AVAILABILITY_EVENTS            (752,510 rows, 24 cols)
--   EDW.AVAILABILITY_RELIEF            (2,117 rows,   7 cols)
--   CTA.SERVICENOW_AVAILABILITY_EVENTS (375,578 rows, 57 cols)
--   CTA.SERVICENOW_DATA_FROM_JUMPBOX   (604 rows,    ~27 cols)
--
-- NOTE: EDW.AVAILABILITY_PERIODS (17 rows) omitted - defines operating period
--       windows but was not used in the original SQL (dead CTE). Excluded.
--
-- Notes:
--   - TRANSIT_DAY_KEY in AVAILABILITY_EVENTS is 6-digit YYMMDD (e.g. 160430 = 2016-04-30)
--     NOT 8-digit YYYYMMDD like other tables. Fix: TO_DATE(..., 'yyMMdd')
--   - SVN_STAGE tables: all 35 have 0 rows in Oracle.
--     CTA.SERVICENOW_AVAILABILITY_EVENTS is the CTA-side mirror (workaround).
--     Joined on sn.AE_EVENT_ID = ae.EVENT_ID (ServiceNow WOT# format may differ
--     from AE EVENT_ID format - verify svn_match_rate in dry-run)
--   - AVAILABILITY_RELIEF joins on DEVICE_ID + FAILURE_LEVEL + date range
--     (not EVENT_ID - column does not exist in relief table)
--   - outage_duration_min: no cap applied (curated EDW table, data is clean)
--     Guard: END_DTM >= START_DTM to exclude any negative durations
--
-- Dimension joined:
--   mars_dev.silver.dim_device (S06) - joined on DEVICE_ID (not DEVICE_KEY)
--   AVAILABILITY_EVENTS has both; DEVICE_ID gives better dim match rate
--
-- Validation run 2026-06-15 - bugs fixed from original:
--   BUG 1:  bronze.* -> parquet S3 paths (4 tables)
--   BUG 2:  silver.kpi_avail_enriched    -> mars_dev.silver.kpi_avail_enriched
--   BUG 3:  silver.dim_device            -> mars_dev.silver.dim_device
--   BUG 4:  TO_DATE(x::text,'YYYYMMDD')  -> mixed-format CASE expression
--           TRANSIT_DAY_KEY has TWO formats in the same table (validated 2026-06-15):
--             min=160430  (6-digit YYMMDD) - older records through ~2019
--             max=20260411 (8-digit YYYYMMDD) - newer records from ~2020 onward
--           Fix: branch on string length
--             LENGTH=6 -> TO_DATE(x, 'yyMMdd')
--             LENGTH=8 -> TO_DATE(x, 'yyyyMMdd')
--   BUG 5:  EXTRACT(EPOCH FROM (a-b))/60 -> (unix_timestamp(a)-unix_timestamp(b))/60.0
--           + END_DTM >= START_DTM guard
--   BUG 6:  CREATE INDEX                 -> not supported on Delta; use OPTIMIZE/ZORDER
--           Recommended: OPTIMIZE mars_dev.silver.kpi_avail_enriched
--                          ZORDER BY (DEVICE_ID, transit_day);
--   BUG 7:  period_lookup CTE was defined but never joined - removed
--   BUG 8:  LEFT JOIN silver.dim_device ON DEVICE_KEY -> ON DEVICE_ID
--   BUG 9:  relief_lookup schema mismatch (CRITICAL):
--           Assumed columns: EVENT_ID, RELIEF_CODE, RELIEF_REASON, RELIEF_DTM
--           Actual columns:  RELIEF_ID, DEVICE_ID, FAILURE_LEVEL, START_DTM,
--                            END_DTM, NOTES
--           Join fixed: EVENT_ID -> DEVICE_ID + FAILURE_LEVEL + date range overlap
--   BUG 10: DROP TABLE IF EXISTS silver.* -> mars_dev.silver.*
--   BUG 11: sn_jumpbox CTE column names wrong (confirmed 2026-06-15 from S17 V-01b):
--           jb.DEVICE_ID           -> jb.U_DEVICE_ID
--           jb.INCIDENT_NUMBER     -> jb.U_EVENT_ID (WOT#, aliased jb_wot_number)
--           jb.INCIDENT_STATE      -> jb.U_WOT_STATE + jb.U_FAULT_STATE
--           jb.INCIDENT_CATEGORY   -> does not exist; removed
--           jb.INCIDENT_SUBCATEGORY -> does not exist; removed
--           jb.ASSIGNED_TO         -> does not exist; removed
--           jb.ASSIGNMENT_GROUP    -> does not exist; removed
--           jb.RESOLVED_AT         -> jb.U_END_DTM (aliased jb_resolved_at)
--           jb.CLOSE_NOTES         -> jb.U_RESOLUTION (aliased jb_resolution)
--           Added: jb_fault_description, jb_affected_component, jb_failure_level,
--                  jb_start_dtm, jb_opened_at, jb_caller, jb_facility_name
-- =============================================================================

DROP TABLE IF EXISTS mars_dev.silver.kpi_avail_enriched;

CREATE TABLE mars_dev.silver.kpi_avail_enriched AS
WITH avail_base AS (
    SELECT
        ae.TRANSIT_DAY_KEY,
        CASE
            WHEN LENGTH(CAST(ae.TRANSIT_DAY_KEY AS STRING)) = 6
            THEN TO_DATE(CAST(ae.TRANSIT_DAY_KEY AS STRING), 'yyMMdd')
            WHEN LENGTH(CAST(ae.TRANSIT_DAY_KEY AS STRING)) = 8
            THEN TO_DATE(CAST(ae.TRANSIT_DAY_KEY AS STRING), 'yyyyMMdd')
            ELSE NULL
        END                                                     AS transit_day,
        ae.EVENT_ID,
        ae.RMA,
        ae.DEVICE_ID,
        ae.DEVICE_TYPE_ID,
        ae.DEVICE_KEY,
        ae.FAULT_STATE,
        ae.FAILURE_LEVEL,
        ae.START_DTM,
        ae.END_DTM,
        ae.FAULT_DESCRIPTION,
        ae.SYMPTOM,
        ae.PROBLEM,
        ae.RESOLUTION,
        ae.EXCLUDED,
        ae.ARRAY_ID,
        ae.ARRAY_SIZE,
        ae.ARRAY_POSITION,
        ae.UPDATED_DTM,
        ae.DEVICE_TYPE_NAME                             AS ae_device_type_name,
        ae.OPERATOR_ID,
        ae.OPERATOR_NAME,
        ae.FACILITY_ID,
        ae.FACILITY_NAME,
        -- Duration in minutes; guard against negative durations (data error)
        CASE
            WHEN ae.END_DTM IS NOT NULL
             AND ae.START_DTM IS NOT NULL
             AND ae.END_DTM >= ae.START_DTM
            THEN (unix_timestamp(ae.END_DTM) - unix_timestamp(ae.START_DTM)) / 60.0
            ELSE NULL
        END                                             AS outage_duration_min
    FROM mars_dev.bronze.edw_availability_events ae
),
relief_lookup AS (
    -- Actual columns: RELIEF_ID, DEVICE_ID, FAILURE_LEVEL, START_DTM, END_DTM, NOTES
    -- Join key: DEVICE_ID + FAILURE_LEVEL + event falls within relief date range
    SELECT
        ar.RELIEF_ID,
        ar.DEVICE_ID                                    AS relief_device_id,
        ar.FAILURE_LEVEL                                AS relief_failure_level,
        ar.START_DTM                                    AS relief_start_dtm,
        ar.END_DTM                                      AS relief_end_dtm,
        ar.NOTES                                        AS relief_notes
    FROM mars_dev.bronze.edw_availability_relief ar
),
sn_events AS (
    SELECT
        sn.AE_EVENT_ID,
        sn.SN_U_EVENT_ID,
        sn.SN_SYS_ID,
        sn.SN_U_DEVICE_TYPE,
        sn.SN_U_DEVICE_ID,
        sn.AE_DEVICE_ID,
        sn.AE_FAULT_STATE,
        sn.AE_FAILURE_LEVEL,
        sn.AE_START_DTM,
        sn.AE_END_DTM,
        sn.AE_FAULT_DESCRIPTION,
        sn.AE_SYMPTOM,
        sn.AE_PROBLEM,
        sn.AE_RESOLUTION,
        sn.AE_DEVICE_TYPE_NAME,
        sn.AE_OPERATOR_ID,
        sn.AE_OPERATOR_NAME,
        sn.AE_FACILITY_ID,
        sn.AE_FACILITY_NAME,
        sn.SN_U_AFFECTED_COMPONENT,
        sn.SN_U_WOT_STATE,
        sn.SN_U_REQUEST_TYPE,
        sn.EDW_INSERTED_DTM                             AS sn_edw_inserted_dtm,
        sn.EDW_UPDATED_DTM                              AS sn_edw_updated_dtm
    FROM mars_dev.bronze.cta_servicenow_availability_events sn
),
sn_jumpbox AS (
    -- BUG S11-A (CRITICAL): Pre-build dry-run 2026-06-18 found max_rows_per_device=17
    -- in jumpbox (356 rows / 355 devices). SELECT DISTINCT on all columns keeps all 17
    -- rows for that device; JOIN ON DEVICE_ID then fans out availability_events.
    -- Confirmed: total_output_rows=752,718 vs 752,510 source (208 extra rows).
    -- Fix: ROW_NUMBER() OVER (PARTITION BY U_DEVICE_ID ORDER BY SYS_CREATED_ON DESC)
    -- picks the single most-recent jumpbox record per device - eliminates fan-out.
    -- Actual column names confirmed 2026-06-15 from S17 V-01b schema check.
    -- All fields are U_ prefixed; no INCIDENT_NUMBER/CATEGORY/SUBCATEGORY/ASSIGNED_TO/PRIORITY
    SELECT
        jb_device_id, jb_wot_number, jb_wot_state, jb_fault_state,
        jb_fault_description, jb_resolution, jb_affected_component,
        jb_failure_level, jb_start_dtm, jb_resolved_at,
        jb_opened_at, jb_caller, jb_facility_name
    FROM (
        SELECT
            jb.U_DEVICE_ID                              AS jb_device_id,
            jb.U_EVENT_ID                               AS jb_wot_number,
            jb.U_WOT_STATE                              AS jb_wot_state,
            jb.U_FAULT_STATE                            AS jb_fault_state,
            jb.U_FAULT_DESCRIPTION                      AS jb_fault_description,
            jb.U_RESOLUTION                             AS jb_resolution,
            jb.U_AFFECTED_COMPONENT                     AS jb_affected_component,
            jb.U_FAILURE_LEVEL                          AS jb_failure_level,
            jb.U_START_DTM                              AS jb_start_dtm,
            jb.U_END_DTM                                AS jb_resolved_at,
            jb.SYS_CREATED_ON                           AS jb_opened_at,
            jb.U_CALLER                                 AS jb_caller,
            jb.U_FACILITY_NAME                          AS jb_facility_name,
            ROW_NUMBER() OVER (
                PARTITION BY jb.U_DEVICE_ID
                ORDER BY jb.SYS_CREATED_ON DESC
            )                                           AS rn
        FROM mars_dev.bronze.cta_servicenow_data_from_jumpbox jb
    ) ranked
    WHERE rn = 1
)
SELECT
    ab.TRANSIT_DAY_KEY,
    ab.transit_day,
    ab.EVENT_ID,
    ab.RMA,
    ab.DEVICE_ID,
    ab.DEVICE_TYPE_ID,
    ab.DEVICE_KEY,
    ab.FAULT_STATE,
    ab.FAILURE_LEVEL,
    ab.START_DTM,
    ab.END_DTM,
    ab.outage_duration_min,
    ab.FAULT_DESCRIPTION,
    ab.SYMPTOM,
    ab.PROBLEM,
    ab.RESOLUTION,
    ab.EXCLUDED,
    ab.ARRAY_ID,
    ab.ARRAY_SIZE,
    ab.ARRAY_POSITION,
    ab.UPDATED_DTM,
    ab.ae_device_type_name,
    ab.OPERATOR_ID,
    ab.OPERATOR_NAME,
    ab.FACILITY_ID,
    ab.FACILITY_NAME,
    -- Device enrichment from dim_device
    dd.DEVICE_NAME,
    dd.DEVICE_TYPE_NAME,
    dd.DEVICE_CONTROL_GROUP_TYPE_NAME,
    dd.mars_device_category,
    dd.DEVICE_SERIAL_NUMBER,
    -- Relief/exclusion (joined on device + failure_level + date range)
    rl.RELIEF_ID,
    rl.relief_failure_level,
    rl.relief_start_dtm,
    rl.relief_end_dtm,
    rl.relief_notes,
    -- ServiceNow enrichment (CTA mirror; SVN_STAGE = 0 rows workaround)
    (sn.AE_EVENT_ID IS NOT NULL)                        AS svn_data_available,
    sn.SN_U_EVENT_ID,
    sn.SN_SYS_ID,
    sn.SN_U_DEVICE_TYPE                                 AS sn_device_type,
    sn.SN_U_DEVICE_ID                                   AS sn_device_id,
    sn.AE_FAULT_STATE                                   AS sn_fault_state,
    sn.AE_FAILURE_LEVEL                                 AS sn_failure_level,
    sn.AE_FAULT_DESCRIPTION                             AS sn_fault_description,
    sn.AE_SYMPTOM                                       AS sn_symptom,
    sn.AE_PROBLEM                                       AS sn_problem,
    sn.AE_RESOLUTION                                    AS sn_resolution,
    sn.SN_U_AFFECTED_COMPONENT                          AS sn_affected_component,
    sn.SN_U_WOT_STATE                                   AS sn_wot_state,
    sn.SN_U_REQUEST_TYPE                                AS sn_request_type,
    sn.sn_edw_inserted_dtm,
    sn.sn_edw_updated_dtm,
    -- Jumpbox supplemental (604 rows, joined on DEVICE_ID; actual U_ column names)
    jb.jb_wot_number,
    jb.jb_wot_state,
    jb.jb_fault_state,
    jb.jb_fault_description,
    jb.jb_resolution,
    jb.jb_affected_component,
    jb.jb_failure_level,
    jb.jb_start_dtm,
    jb.jb_resolved_at,
    jb.jb_opened_at,
    jb.jb_caller,
    jb.jb_facility_name

FROM avail_base ab
LEFT JOIN mars_dev.silver.dim_device dd
    ON dd.DEVICE_ID   = ab.DEVICE_ID
   AND dd.is_current  = TRUE
LEFT JOIN relief_lookup rl
    ON rl.relief_device_id   = ab.DEVICE_ID
   AND rl.relief_failure_level = ab.FAILURE_LEVEL
   AND rl.relief_start_dtm   <= ab.START_DTM
   AND (rl.relief_end_dtm IS NULL OR rl.relief_end_dtm >= ab.START_DTM)
LEFT JOIN sn_events sn
    ON sn.AE_EVENT_ID = ab.EVENT_ID
LEFT JOIN sn_jumpbox jb
    ON jb.jb_device_id = ab.DEVICE_ID;

-- Post-load optimisation:
-- OPTIMIZE mars_dev.silver.kpi_avail_enriched ZORDER BY (DEVICE_ID, transit_day);
