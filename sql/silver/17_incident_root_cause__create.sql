-- =============================================================================
-- silver.incident_root_cause
-- Incident / root-cause records for root-cause analysis (PS3)
--
-- Sources (mars_dev.bronze catalog):
--   CTA.SERVICENOW_AVAILABILITY_EVENTS  (375,578 rows, 57 cols) -> mars_dev.bronze.cta_servicenow_availability_events
--   CTA.SERVICENOW_DATA_FROM_JUMPBOX    (604 rows,    27 cols)  -> mars_dev.bronze.cta_servicenow_data_from_jumpbox
--   silver.incident_history (S15)       (7,028,048 rows)        -- sn_category / sn_maintenance_type enrichment
--
-- CRITICAL DATA GAP -- SVN_STAGE TABLES ALL HAVE 0 ROWS IN ORACLE:
--   SVN_STAGE.INCIDENT, FAULT, WORK_ORDER, WORK_ORDER_TASK, CMDB_CI, CHANGE_REQUEST
--   (+ 29 more SVN_STAGE tables) all empty in Oracle EDW staging environment.
--   WORKAROUND: CTA.SERVICENOW_AVAILABILITY_EVENTS is the CTA-curated ServiceNow
--   mirror joined to availability events. Used as the sole incident source.
--   CTA.SERVICENOW_DATA_FROM_JUMPBOX (604 rows) supplements with additional SN fields.
--   IMPACT: No work-order lifecycle, no CMDB CI lineage, no technician detail.
--   UPDATE 2026-06-24: silver.incident_history (S15) now provides full SN incident
--   data (7M rows). Joined here on device_id + date to enrich with sn_category.
--
-- Notes:
--   - AE_TRANSIT_DAY_KEY: 8-digit YYYYMMDD confirmed (no mixed-format issue)
--   - Grain: one row per availability event (same as silver.kpi_avail_enriched)
--   - AE_FAULT_DESCRIPTION, AE_SYMPTOM, AE_PROBLEM, AE_RESOLUTION = PS3 NLP features
--   - AE_FAILURE_LEVEL: Ventra KPI taxonomy (Michael R2 confirmed 2026-06-22)
--       is_chargeable = AE_FAILURE_LEVEL > 0 (hardware failure, chargeable to SLA)
--       is_device_fault = AE_FAILURE_LEVEL IN (1,2,3,4,5,16) (PS3 training scope)
--   - Jumpbox joined on U_EVENT_ID = SN_U_EVENT_ID (WOT# -- 1:1, no fan-out)
--   - S15 joined on wm_asset = device_id AND DATE(opened_dtm) = transit_day
--     Using first incident per device-day (ROW_NUMBER) to prevent fan-out
--   - dim_device joined on AE_DEVICE_ID: 91.9% match rate
--
-- Michael R2 changes applied 2026-06-24:
--   R2-1: is_chargeable = (AE_FAILURE_LEVEL > 0) added as explicit column
--         PS1/PS3 label = is_device_fault = TRUE (subset of is_chargeable)
--   R2-3: sn_category / sn_maintenance_type from silver.incident_history (S15) added
--         Corrective Maintenance = real breakdown, Planned Maintenance = scheduled
--
-- Validation run 2026-06-15:
--   Dry-run 2025-01-01 to 07: 2,540 rows, 1,170 devices
--   has_duration=100%, dim_device=91.9%, jumpbox=0% (2026 extract vs Jan-2025 filter)
--
-- Bugs fixed from original (22 total -- see original design file for full list):
--   BUG 1-22: silver.* paths, EXTRACT(EPOCH), column names, join keys all corrected.
-- =============================================================================

DROP TABLE IF EXISTS mars_dev.silver.incident_root_cause;

CREATE TABLE mars_dev.silver.incident_root_cause AS
WITH sn_base AS (
    -- CTA.SERVICENOW_AVAILABILITY_EVENTS: primary incident source (SVN_STAGE=0 workaround)
    SELECT
        sn.SN_U_EVENT_ID,
        sn.SN_SYS_ID,
        sn.SN_U_DEVICE_TYPE,
        sn.SN_U_DEVICE_ID,
        sn.AE_DEVICE_ID,
        sn.AE_TRANSIT_DAY_KEY,
        TO_DATE(CAST(sn.AE_TRANSIT_DAY_KEY AS STRING), 'yyyyMMdd') AS transit_day,
        sn.AE_EVENT_ID,
        sn.AE_FAULT_STATE,
        sn.AE_FAILURE_LEVEL,
        sn.AE_START_DTM,
        sn.AE_END_DTM,
        CASE
            WHEN sn.AE_END_DTM IS NOT NULL AND sn.AE_START_DTM IS NOT NULL
            THEN (unix_timestamp(sn.AE_END_DTM) - unix_timestamp(sn.AE_START_DTM)) / 60.0
            ELSE NULL
        END                                      AS incident_duration_min,
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
        sn.EDW_INSERTED_DTM,
        sn.EDW_UPDATED_DTM
    FROM mars_dev.bronze.cta_servicenow_availability_events sn
    WHERE sn.AE_DEVICE_ID IS NOT NULL
),
jb_base AS (
    -- CTA.SERVICENOW_DATA_FROM_JUMPBOX: supplemental fields (604 rows, 2026 extract)
    SELECT
        jb.U_EVENT_ID             AS jb_event_id,
        jb.U_DEVICE_ID            AS jb_device_id,
        jb.U_WOT_STATE            AS jb_wot_state,
        jb.U_FAULT_STATE          AS jb_fault_state,
        jb.U_REQUEST_TYPE         AS jb_request_type,
        jb.U_FAULT_DESCRIPTION    AS jb_fault_description,
        jb.U_RESOLUTION           AS jb_resolution,
        jb.U_AFFECTED_COMPONENT   AS jb_affected_component,
        jb.U_FAILURE_LEVEL        AS jb_failure_level,
        jb.U_START_DTM            AS jb_start_dtm,
        jb.U_END_DTM              AS jb_end_dtm,
        jb.SYS_CREATED_ON         AS jb_opened_at,
        jb.U_CALLER               AS jb_caller,
        jb.U_FACILITY_NAME        AS jb_facility_name
    FROM mars_dev.bronze.cta_servicenow_data_from_jumpbox jb
),

-- -- S15 enrichment: first incident per device+day (prevent fan-out) -----------
-- Join key: wm_asset (SAG00901) = AE_DEVICE_ID, DATE(opened_dtm) = transit_day
-- Provides: category (Corrective/Planned Maintenance), maintenance_type, priority
s17_first AS (
    SELECT
        wm_asset,
        CAST(DATE(opened_dtm) AS DATE)          AS incident_date,
        category                                AS sn_category,
        maintenance_type                        AS sn_maintenance_type,
        priority                                AS sn_priority,
        priority_label                          AS sn_priority_label,
        chargeable_level                        AS sn_chargeable_level,
        event_code_id                           AS sn_event_code_id,
        event_code_name                         AS sn_event_code_name,
        ROW_NUMBER() OVER (
            PARTITION BY wm_asset, CAST(DATE(opened_dtm) AS DATE)
            ORDER BY opened_dtm
        )                                       AS rn
    FROM mars_dev.silver.incident_history
    WHERE wm_asset IS NOT NULL
),

classified AS (
    SELECT
        sb.*,
        -- Root-cause categorisation from free-text fields (PS3 target engineering)
        CASE
            WHEN UPPER(sb.AE_FAULT_DESCRIPTION)    LIKE '%CASH%'
              OR UPPER(sb.AE_SYMPTOM)               LIKE '%CASH%'
              OR UPPER(sb.SN_U_AFFECTED_COMPONENT)  LIKE '%BHU%'
              OR UPPER(sb.SN_U_AFFECTED_COMPONENT)  LIKE '%CHU%'   THEN 'CASH_HANDLING'
            WHEN UPPER(sb.AE_FAULT_DESCRIPTION)    LIKE '%PRINTER%'
              OR UPPER(sb.AE_SYMPTOM)               LIKE '%PRINT%'
              OR UPPER(sb.SN_U_AFFECTED_COMPONENT)  LIKE '%PRINT%' THEN 'PRINTER'
            WHEN UPPER(sb.AE_FAULT_DESCRIPTION)    LIKE '%CARD%'
              OR UPPER(sb.AE_FAULT_DESCRIPTION)    LIKE '%READER%'
              OR UPPER(sb.SN_U_AFFECTED_COMPONENT)  LIKE '%CSC%'   THEN 'CARD_READER'
            WHEN UPPER(sb.AE_FAULT_DESCRIPTION)    LIKE '%GATE%'
              OR UPPER(sb.SN_U_AFFECTED_COMPONENT)  LIKE '%GATE%'  THEN 'GATE_MECH'
            WHEN UPPER(sb.AE_FAULT_DESCRIPTION)    LIKE '%NETWORK%'
              OR UPPER(sb.AE_FAULT_DESCRIPTION)    LIKE '%COMM%'
              OR UPPER(sb.AE_FAULT_DESCRIPTION)    LIKE '%CONNECT%' THEN 'COMMS'
            WHEN UPPER(sb.AE_FAULT_DESCRIPTION)    LIKE '%POWER%'
              OR UPPER(sb.AE_FAULT_DESCRIPTION)    LIKE '%SUPPLY%'  THEN 'POWER'
            WHEN UPPER(sb.AE_FAULT_DESCRIPTION)    LIKE '%SOFTWARE%'
              OR UPPER(sb.AE_FAULT_DESCRIPTION)    LIKE '%FIRMWARE%'
              OR UPPER(sb.AE_FAULT_DESCRIPTION)    LIKE '%REBOOT%'  THEN 'SOFTWARE'
            ELSE 'UNKNOWN'
        END                                     AS root_cause_category,

        -- Ventra KPI failure level taxonomy (Michael 2026-06-22 confirmed)
        CASE sb.AE_FAILURE_LEVEL
            WHEN 0  THEN 'FULLY_FUNCTIONAL'
            WHEN 1  THEN 'NONPAYMENT'
            WHEN 2  THEN 'PURCHASE_CARD'
            WHEN 3  THEN 'PURCHASE_PRODUCT'
            WHEN 4  THEN 'ALL_PURCHASE'
            WHEN 5  THEN 'ALL_FUNCTIONS'
            WHEN 6  THEN 'ALL_LINES_BUSY'
            WHEN 16 THEN 'BUS_READER_ASSEMBLY'
            WHEN 98 THEN 'NON_AVAIL_PULLOUT'
            WHEN 99 THEN 'INSUFFICIENT_INVENTORY'
            ELSE         'BACK_OFFICE_OR_UNKNOWN'
        END                                     AS failure_level_label,

        -- is_chargeable: failure_level > 0 = real hardware failure chargeable to SLA (R2-1)
        (sb.AE_FAILURE_LEVEL > 0)               AS is_chargeable,

        -- is_device_fault: Category-2 hardware levels only (PS3 training scope)
        sb.AE_FAILURE_LEVEL IN (1, 2, 3, 4, 5, 16) AS is_device_fault

    FROM sn_base sb
)
SELECT
    c.SN_U_EVENT_ID,
    c.SN_SYS_ID,
    c.SN_U_DEVICE_TYPE,
    c.SN_U_DEVICE_ID,
    c.AE_DEVICE_ID              AS device_id,
    c.AE_TRANSIT_DAY_KEY        AS transit_day_key,
    c.transit_day,
    c.AE_EVENT_ID               AS availability_event_id,
    c.AE_FAULT_STATE,
    c.AE_FAILURE_LEVEL,
    c.failure_level_label,
    c.is_chargeable,
    c.is_device_fault,
    c.incident_duration_min,
    c.AE_FAULT_DESCRIPTION,
    c.AE_SYMPTOM,
    c.AE_PROBLEM,
    c.AE_RESOLUTION,
    c.SN_U_AFFECTED_COMPONENT   AS affected_component,
    c.SN_U_WOT_STATE            AS wot_state,
    c.SN_U_REQUEST_TYPE         AS request_type,
    c.root_cause_category,
    c.AE_DEVICE_TYPE_NAME,
    c.AE_OPERATOR_ID,
    c.AE_OPERATOR_NAME,
    c.AE_FACILITY_ID,
    c.AE_FACILITY_NAME,
    c.AE_START_DTM,
    c.AE_END_DTM,
    c.EDW_INSERTED_DTM,
    c.EDW_UPDATED_DTM,

    -- S15 enrichment (category from full ServiceNow incident export -- R2-3)
    s17.sn_category,            -- 'Corrective Maintenance' = real breakdown; 'Planned Maintenance' = scheduled
    s17.sn_maintenance_type,    -- 'Corrective Maintenance' / 'Planned Maintenance'
    s17.sn_priority,
    s17.sn_priority_label,
    s17.sn_chargeable_level,
    s17.sn_event_code_id,
    s17.sn_event_code_name,

    -- Jumpbox supplemental fields (actual column names)
    jb.jb_event_id              AS jb_wot_number,
    jb.jb_device_id,
    jb.jb_wot_state,
    jb.jb_fault_state,
    jb.jb_request_type,
    jb.jb_fault_description,
    jb.jb_resolution,
    jb.jb_affected_component,
    jb.jb_failure_level,
    jb.jb_start_dtm,
    jb.jb_end_dtm               AS jb_resolved_at,
    jb.jb_opened_at,
    jb.jb_caller,
    jb.jb_facility_name,

    -- Device enrichment
    dd.DEVICE_KEY,
    dd.DEVICE_NAME,
    dd.DEVICE_TYPE_NAME,
    dd.DEVICE_CONTROL_GROUP_TYPE_NAME,
    dd.mars_device_category,
    dd.FACILITY_NAME,
    dd.OPERATOR_NAME,
    dd.DEVICE_SERIAL_NUMBER,

    -- Data source flags
    TRUE                        AS from_cta_sn_mirror,
    FALSE                       AS from_svn_stage

FROM classified c
LEFT JOIN mars_dev.silver.dim_device dd
    ON dd.DEVICE_ID  = c.AE_DEVICE_ID
   AND dd.is_current = TRUE
LEFT JOIN jb_base jb
    ON jb.jb_event_id = c.SN_U_EVENT_ID
LEFT JOIN s17_first s17
    ON  s17.wm_asset     = c.AE_DEVICE_ID
    AND s17.incident_date = c.transit_day
    AND s17.rn            = 1;

-- Post-load optimisation:
-- OPTIMIZE mars_dev.silver.incident_root_cause ZORDER BY (device_id, transit_day);
