-- =============================================================================
-- silver.incident_root_cause
-- Incident / root-cause records for root-cause analysis (PS3)
--
-- Sources (mars_dev.bronze catalog):
--   CTA.SERVICENOW_AVAILABILITY_EVENTS  (375,578 rows, 57 cols) -> mars_dev.bronze.cta_servicenow_availability_events
--   SERVICENOW_CTA_CHARGABILITY         (415,350 rows, 31 cols) -> mars_dev.bronze.servicenow_cta_chargability
--   silver.incident_history (S15)       (rebuilt 2026-07-20 on servicenow_incident;
--                                         see 15_incident_history__create.sql)
--                                                                -- sn_category / sn_maintenance_type enrichment
--
-- UPDATE 2026-09-15: jb_base now reads bronze.servicenow_cta_chargability instead
-- of bronze.cta_servicenow_data_from_jumpbox. Same U_* column names and same join
-- key (U_EVENT_ID / WOT# = c.SN_U_EVENT_ID). Chargability is the ServiceNow-API-
-- sourced successor to the Oracle-mirrored jumpbox extract (~700x the row count);
-- jumpbox retained in bronze for lineage only.
--
-- MEASURED 2026-09-15 (all figures from live bronze; supersedes the estimates that
-- accompanied the swap):
--   ENRICHMENT RATE   375,572 of 375,578 availability events now match a
--                     chargability row = 99.998%, against 604 = 0.16% for jumpbox.
--                     This is the point of the swap: jb_* columns went from
--                     effectively always-NULL to effectively always-populated.
--   NOT 1:1           u_event_id repeats (87 excess rows, max 3 per event) -- the
--                     earlier "1:1, no fan-out" line was inherited from the 604-row
--                     jumpbox era and is NOT true of this table. jb_base now
--                     QUALIFYs to one row per event; see the CTE below.
--   COLUMN RETYPE     jb_opened_at changes STRING -> TIMESTAMP, because chargability
--                     types sys_created_on as TIMESTAMP where jumpbox had STRING.
--                     Accepted deliberately: it is the correct type, nothing outside
--                     S11/S17 reads the column, and no DQ check pins its type.
--                     Side effect: S11's ORDER BY on it is now chronological rather
--                     than lexical.
--   AE STAYS AUTHORITATIVE FOR FAILURE LEVEL
--                     jb_failure_level agrees with AE_FAILURE_LEVEL on 99.90% of
--                     rows (307 disagreements, mostly 0 <-> non-0 chargeability
--                     flips). is_device_fault reads AE_FAILURE_LEVEL and must keep
--                     doing so. jb_failure_level is supplemental -- do NOT use it
--                     as a label.
--   DOES NOT CLOSE THE UNKNOWN-FAILURE-LEVEL GAP
--                     where AE_FAILURE_LEVEL is NULL (60,207 rows) chargability is
--                     blank too on all but 146. The 5e78f0a "preserve unknown
--                     AE_FAILURE_LEVEL" handling stays necessary.
--   EMPTY STRING != NULL
--                     u_failure_level uses '' for missing, not NULL. Any IS NULL
--                     test on it silently misses ~60K rows; use nullif(trim(x),'').
--   NO FRESHNESS GAIN FOR SILVER
--                     chargability runs to 2026-09-10; availability_events stops at
--                     2026-04-11 (the Oracle freeze). This CTE is LEFT JOINed FROM
--                     availability events, so the ~40K post-11-Apr work orders match
--                     nothing and never reach silver. The swap improves coverage
--                     WITHIN the existing window; it does not move the window.
--                     Silver's vintage still unblocks only via the Oracle
--                     incremental feed.
--   PS3 LEAKAGE -- DO NOT FEED THESE TO A MODEL
--                     jb_fault_description, jb_resolution (and u_symptom if ever
--                     added) are free-text fields that NAME the fault. They were
--                     inert while jumpbox matched 0%; they are now populated on
--                     ~375K rows. gold/device_ps3_incident does not select them and
--                     must not start: TF-IDF over this text is what produced PS3's
--                     spurious 0.999 F1 before the 2026-07-13 patch stripped it.
--
-- CMDB CI LINKAGE -- CONCLUSIVELY CLOSED 2026-09-15 (not a join-technique problem):
--   Four independent join strategies tested against live incident/device data, all
--   below a usable population-level match rate:
--     incident.cmdb_ci_sys_id -> cmdb_ci.sys_id (direct)........... 0%
--     incident.cmdb_ci_display_value -> cmdb_ci.name (display).... 0.3%
--     incident -> task_ci -> cmdb_ci.sys_id (indirect)............. 3.63% (12,708 / 350,386 incidents)
--     cmdb_ci.asset_tag -> dim_device.DEVICE_ID..................... 0.00% (2 / 246,502 incidents)
--   Root cause: only 18.47% of cmdb_ci.asset_tag values are even shaped like a
--   fleet device ID (regex ^[A-Z]{2,4}[0-9]{4,6}$), vs 92.61% of real
--   dim_device.DEVICE_ID values -- and the small fleet-ID-shaped subset still
--   doesn't match current, incident-generating devices. The bronze CMDB CI export
--   (servicenow_cmdb_ci_pos_device/card_handling/netgear/onboard_card_interface/acc)
--   does not cover the same fleet as the incidents in this dataset. Needs a
--   corrected export from the ServiceNow data owner, scoped to the actual
--   TVM/GATE/BMV/RVG fleet -- not solvable by trying more join keys here.
--
-- CRITICAL DATA GAP -- SVN_STAGE TABLES ALL HAVE 0 ROWS IN ORACLE:
--   SVN_STAGE.INCIDENT, FAULT, WORK_ORDER, WORK_ORDER_TASK, CMDB_CI, CHANGE_REQUEST
--   (+ 29 more SVN_STAGE tables) all empty in Oracle EDW staging environment.
--   WORKAROUND: CTA.SERVICENOW_AVAILABILITY_EVENTS is the CTA-curated ServiceNow
--   mirror joined to availability events. Used as the sole incident source.
--   SERVICENOW_CTA_CHARGABILITY (415,350 rows) supplements with additional SN
--   fields -- supersedes the old 604-row CTA.SERVICENOW_DATA_FROM_JUMPBOX extract.
--   IMPACT: No work-order lifecycle, no CMDB CI lineage, no technician detail.
--   UPDATE 2026-06-24: silver.incident_history (S15) now provides full SN incident
--   data. Joined here on device_id + date to enrich with sn_category.
--   UPDATE 2026-07-20: S15 rebuilt on servicenow_incident (its cta_servicenow_incident
--   source had regressed to near-empty for 2023H2); sn_priority_label dropped from
--   this enrichment as a result -- see comment at s17_first below.
--
-- Notes:
--   - AE_TRANSIT_DAY_KEY: 8-digit YYYYMMDD confirmed (no mixed-format issue)
--   - Grain: one row per availability event (SIL-C1 fix 2026-07-22: QUALIFY dedup)
--   - AE_FAULT_DESCRIPTION, AE_SYMPTOM, AE_PROBLEM, AE_RESOLUTION = PS3 NLP features
--   - AE_FAILURE_LEVEL: Ventra KPI taxonomy (Michael R2 confirmed 2026-06-22)
--       NULL = unknown / not populated in source (NOT the same as 0 = FULLY_FUNCTIONAL)
--       is_chargeable = AE_FAILURE_LEVEL > 0 (hardware failure, chargeable to SLA)
--       is_device_fault = AE_FAILURE_LEVEL IN (1,2,3,4,5,16) (PS3 training scope)
--       FIX 2026-09-10: preserve source NULLs (no COALESCE to 0); S18 device_outage
--       uses known_failure_level_count to distinguish unknown from confirmed 0.
--   - Chargability joined on U_EVENT_ID = SN_U_EVENT_ID (WOT#). NOT 1:1 at source
--     (87 excess rows) -- jb_base QUALIFYs to one row per event to hold the grain
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
      AND sn.AE_EVENT_ID IS NOT NULL
      AND TRIM(sn.AE_EVENT_ID) <> ''
      AND sn.AE_TRANSIT_DAY_KEY IS NOT NULL
),
jb_base AS (
    -- SERVICENOW_CTA_CHARGABILITY: supplemental fields (415,350 rows, full-history
    -- API extract; supersedes the old 604-row jumpbox extract -- see header note)
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
    FROM mars_dev.bronze.servicenow_cta_chargability jb
    -- DEDUP 2026-09-15: u_event_id is NOT 1:1 on this table. Measured: 415,350 rows
    -- / 415,263 distinct / 87 excess, max 3 rows per event. 72 of those duplicated
    -- events match an availability event, so the LEFT JOIN below would add ~73 rows
    -- and break this table's one-row-per-availability-event grain (confirmed
    -- empirically: an ungated join returns 375,651 rows vs 375,578 events). The 1:1
    -- claim inherited from the jumpbox era was true of that 604-row extract, not of
    -- this one. Keep the most recently updated row per event; SYS_ID is the
    -- deterministic tie-break so reruns are stable. Same class of guard as S11's
    -- per-device ROW_NUMBER and s17_first's per-device-day dedup below.
    QUALIFY ROW_NUMBER() OVER (
        PARTITION BY jb.U_EVENT_ID
        ORDER BY jb.SYS_UPDATED_ON DESC, jb.SYS_CREATED_ON DESC, jb.SYS_ID
    ) = 1
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
        -- sn_priority_label dropped 2026-07-20: S15 was rebuilt on
        -- servicenow_incident (see 15_incident_history__create.sql), whose
        -- priority column is already a plain integer -- no embedded
        -- "N - Label" string like cta_servicenow_incident had, so there's no
        -- label left to split out. Nothing downstream (checked gold PS3)
        -- referenced this field, so it's dropped rather than guessed at --
        -- fabricating a code-to-label mapping without a confirmed source
        -- would risk mislabeling priority codes 2/4/5 (only "1 - Critical"
        -- and "3 - Low" were ever confirmed from the old export).
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
        CASE
            WHEN sb.AE_FAILURE_LEVEL IS NULL THEN NULL
            WHEN sb.AE_FAILURE_LEVEL = 0  THEN 'FULLY_FUNCTIONAL'
            WHEN sb.AE_FAILURE_LEVEL = 1  THEN 'NONPAYMENT'
            WHEN sb.AE_FAILURE_LEVEL = 2  THEN 'PURCHASE_CARD'
            WHEN sb.AE_FAILURE_LEVEL = 3  THEN 'PURCHASE_PRODUCT'
            WHEN sb.AE_FAILURE_LEVEL = 4  THEN 'ALL_PURCHASE'
            WHEN sb.AE_FAILURE_LEVEL = 5  THEN 'ALL_FUNCTIONS'
            WHEN sb.AE_FAILURE_LEVEL = 6  THEN 'ALL_LINES_BUSY'
            WHEN sb.AE_FAILURE_LEVEL = 16 THEN 'BUS_READER_ASSEMBLY'
            WHEN sb.AE_FAILURE_LEVEL = 98 THEN 'NON_AVAIL_PULLOUT'
            WHEN sb.AE_FAILURE_LEVEL = 99 THEN 'INSUFFICIENT_INVENTORY'
            ELSE                               'BACK_OFFICE_OR_UNKNOWN'
        END                                     AS failure_level_label,

        -- is_chargeable: failure_level > 0 = real hardware failure chargeable to SLA (R2-1)
        -- NULL when AE_FAILURE_LEVEL is unknown (not FALSE)
        (sb.AE_FAILURE_LEVEL > 0)               AS is_chargeable,

        -- is_device_fault: Category-2 hardware levels only (PS3 training scope)
        -- NULL when AE_FAILURE_LEVEL is unknown (not FALSE)
        sb.AE_FAILURE_LEVEL IN (1, 2, 3, 4, 5, 16) AS is_device_fault

    FROM sn_base sb
),

-- SIL-C1 (2026-07-22): dedup on availability_event_id before writing silver.
-- Two fan-out sources: SN 2-rows-per-sys_id and multi-WOT-per-AE joins.
-- PS3 gold had a workaround QUALIFY; fix at source so device_outage/S17 consumers are clean.
enriched AS (
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
    AND s17.rn            = 1
)

SELECT *
FROM enriched
WHERE availability_event_id IS NOT NULL
  AND TRIM(availability_event_id) <> ''
  AND transit_day IS NOT NULL
QUALIFY ROW_NUMBER() OVER (
    PARTITION BY UPPER(TRIM(availability_event_id))
    ORDER BY EDW_UPDATED_DTM DESC NULLS LAST, from_cta_sn_mirror DESC
) = 1;

-- Post-load optimisation:
-- OPTIMIZE mars_dev.silver.incident_root_cause ZORDER BY (device_id, transit_day);
