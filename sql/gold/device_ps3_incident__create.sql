-- =============================================================================
-- gold.device_ps3_incident
-- PS3 -- Root Cause / Incident Classification: feature table for ALL device types
-- Grain: (device_id, availability_event_id) -- exactly 1 row per incident
-- Target: AE_FAILURE_LEVEL (cat-2 device faults: 1/2/3/4/5/16) -- confirmed taxonomy 2026-06-23
-- Device types: TVM, GATE, VALIDATOR (READER removed -- reader is a component, not device category)
-- Primary source: silver.incident_root_cause (S17)
--   backed by CTA.SERVICENOW_AVAILABILITY_EVENTS via SN mirror (from_cta_sn_mirror = TRUE)
--   SVN_STAGE tables have 0 rows (from_svn_stage is always FALSE)
--
-- !  SVN_STAGE DATA GAP:
--   All 35 SVN_STAGE tables have 0 rows. MISSING features:
--     - Work order task details (labor hours, technician, action taken)
--     - CMDB CI configuration item data
--     - Change request linkage
--     - Full incident lifecycle timestamps
--
-- Fixes applied 2026-06-19 (pre-validation run):
--   FIX 1: gold./silver. prefixes -> mars_dev.gold. / mars_dev.silver.
--   FIX 2: INTERVAL '24 hours' -> INTERVAL 24 HOURS (Spark SQL)
--           INTERVAL '7 days'  -> INTERVAL 7 DAYS
--   FIX 3 (events_24h_prior): severity IN ('WARN','CRITICAL') -> fault-onset filter
--           OLD: severity IN ('WARN','CRITICAL') -> near-zero rows (confirmed: 100% INFO)
--           NEW: is_oos_event = TRUE AND EVENT_STATE_TYPE_NAME = 'Set'
--           Renamed output column: critical_24h_prior -> oos_onsets_24h
--   FIX 4 (critical_7d_prior): severity = 'CRITICAL' -> same fault-onset filter
--           Renamed output columns: critical_events_7d_prior/oos_events_7d_prior
--                                 -> oos_onsets_7d_prior / events_7d_prior
--   FIX 5: ILIKE -> LIKE (ILIKE unsupported in Spark SQL) -- also redesigned; see FIX 7
--   FIX 6: CREATE INDEX (x6) -> removed; not supported on Delta (use OPTIMIZE/ZORDER)
--   FIX 7: hw_age join fan-out -- V04 confirmed 78.32% blank affected_component
--           Old: ILIKE '' matches ALL hw components when blank -> 1.41x fan-out
--           New: hw_best_match CTE with ROW_NUMBER() per availability_event_id:
--                  priority 0: COMPONENT_DESCRIPTION LIKE '%affected_component%' (non-blank)
--                  priority 1: most recently changed component (fallback for blank/no-match)
--                  WHERE rn = 1 -> exactly 1 hw row per incident = no fan-out
--   FIX 8 (updated 2026-06-23 from Michael's Ventra KPI Failure Levels sheet):
--           Level 0  = Fully Functional  (193,643; 61.4%) -- exclude (not a failure)
--           Level 1  = Nonpayment Functions        (6 rows, 0.0%)
--           Level 2  = Purchase Card Functions (61,036; 19.4%)
--           Level 3  = Purchase Product Functions (5,920; 1.9%)
--           Level 4  = All Purchase Functions  ) confirmed hardware faults (cat-2)
--           Level 5  = All Functions           ) Michael 2026-06-22 -- include these
--           Level 16 = Bus Reader Assembly Fail) ~55K rows; previously mislabelled UNKNOWN
--           Levels 6/98/99 = Operational (not hardware failures) -- exclude
--           NEW: WHERE AE_FAILURE_LEVEL IN (1,2,3,4,5,16) -> all cat-2 hardware faults
--           Expected rows: ~66,962 + ~55,000 levels-4/5/16 = ~122K total
--   FIX 9: transit_day >= '2024-01-01' added to all_incidents
--           V06 confirmed data back to 2017 (e.g., 2017-03-18). device_event_enriched
--           starts 2024-01-01, so pre-2024 incidents always yield 0 event lookups.
--   FIX 10: mars_device_category IN ('TVM','GATE','READER','VALIDATOR') in all_incidents
--            correctly excludes 81,362 null-category rows (bus devices not in dim_device)
--            READER=0, VALIDATOR=0 incidents confirmed by V01
--
-- Expected output: ~122K rows (levels 1/2/3/4/5/16, 2024-01-01+, TVM+GATE+VALIDATOR)
-- =============================================================================

DROP TABLE IF EXISTS mars_dev.gold.device_ps3_incident;

CREATE TABLE mars_dev.gold.device_ps3_incident AS
WITH all_incidents AS (
    SELECT *
    FROM mars_dev.silver.incident_root_cause
    -- READER removed: reader is a component (not a device category) -- Michael 2026-06-22
    WHERE mars_device_category IN ('TVM','GATE','VALIDATOR')
      -- FIX 9: 2024+ only; pre-2024 incidents get 0 event matches in lookups
      AND transit_day >= '2024-01-01'
      -- FIX 8 (updated 2026-06-23): levels 4/5/16 are real hardware faults (confirmed by Michael)
      AND AE_FAILURE_LEVEL IN (1, 2, 3, 4, 5, 16)
    -- Dedup: two fan-out sources confirmed in validate_gold grain check:
    -- (1) bronze SN table has 2 duplicate rows per sys_id feeding S17
    -- (2) two distinct SN incidents (e.g. WOT2740405 + WOT2741082) both link to the same AE
    -- Combined = 4x per availability_event_id. Keep earliest-start / lowest sys_id for determinism.
    QUALIFY ROW_NUMBER() OVER (
        PARTITION BY availability_event_id
        ORDER BY AE_START_DTM ASC NULLS LAST, SN_SYS_ID ASC NULLS LAST
    ) = 1
),
events_24h_prior AS (
    SELECT
        ai.availability_event_id,
        ai.device_id,
        ai.AE_START_DTM,
        COUNT(dee.DW_DEVICE_EVENT_ID)                                   AS events_24h_prior,
        -- FIX 3: severity IN ('WARN','CRITICAL') -> fault-onset filter
        -- confirmed: device_event_enriched severity is 100% INFO; WARN=0, CRITICAL=22
        SUM(CASE WHEN dee.is_hardware_oos_event = TRUE AND dee.EVENT_STATE_TYPE_NAME = 'Set'
                 THEN 1 ELSE 0 END)                                     AS oos_onsets_24h,
        -- Subsystem breakdown (TVM)
        SUM(CASE WHEN dee.component_subsystem = 'BHU'        THEN 1 ELSE 0 END) AS bhu_24h,
        SUM(CASE WHEN dee.component_subsystem = 'CHU'        THEN 1 ELSE 0 END) AS chu_24h,
        SUM(CASE WHEN dee.component_subsystem = 'PRINTER'    THEN 1 ELSE 0 END) AS printer_24h,
        -- Subsystem breakdown (GATE)
        SUM(CASE WHEN dee.component_subsystem = 'GATE_MECH'  THEN 1 ELSE 0 END) AS gate_mech_24h,
        -- Subsystem breakdown (READER/VALIDATOR)
        SUM(CASE WHEN dee.component_subsystem = 'CSC_READER' THEN 1 ELSE 0 END) AS csc_reader_24h,
        -- Shared noisy subsystems (kept as informational features)
        SUM(CASE WHEN dee.component_subsystem = 'COMMS'      THEN 1 ELSE 0 END) AS comms_24h
    FROM all_incidents ai
    LEFT JOIN mars_dev.silver.device_event_enriched dee
        ON dee.DEVICE_ID = ai.device_id
       -- FIX 2: INTERVAL '24 hours' -> INTERVAL 24 HOURS
       AND dee.EVENT_DTM >= ai.AE_START_DTM - INTERVAL 24 HOURS
       AND dee.EVENT_DTM <  ai.AE_START_DTM
    GROUP BY ai.availability_event_id, ai.device_id, ai.AE_START_DTM
),
critical_7d_prior AS (
    SELECT
        ai.availability_event_id,
        ai.device_id,
        -- FIX 4: severity = 'CRITICAL' -> fault-onset filter (same reason as FIX 3)
        COUNT(CASE WHEN dee.is_oos_event = TRUE
                    AND dee.EVENT_STATE_TYPE_NAME = 'Set' THEN 1 END)   AS oos_onsets_7d_prior,
        COUNT(dee.DW_DEVICE_EVENT_ID)                                   AS events_7d_prior
    FROM all_incidents ai
    LEFT JOIN mars_dev.silver.device_event_enriched dee
        ON dee.DEVICE_ID = ai.device_id
       -- FIX 2
       AND dee.EVENT_DTM >= ai.AE_START_DTM - INTERVAL 7 DAYS
       AND dee.EVENT_DTM <  ai.AE_START_DTM
    GROUP BY ai.availability_event_id, ai.device_id
),
hw_best_match AS (
    -- FIX 7: replace fan-out ILIKE join with ranked per-incident CTE
    -- V04 confirmed: 78.32% blank affected_component -> old ILIKE '' matched ALL components
    -- V03b: avg 1.41 hw components/device, max 7 -> fan-out created up to 7 duplicate rows
    -- ROW_NUMBER per availability_event_id: priority = description match, then most-recent
    SELECT
        availability_event_id,
        COMPONENT_DESCRIPTION,
        COMPONENT_SERIAL_NBR,
        component_age_days,
        component_install_date
    FROM (
        SELECT
            inc.availability_event_id,
            hwc.COMPONENT_DESCRIPTION,
            hwc.COMPONENT_SERIAL_NBR,
            hwc.component_age_days,
            hwc.REPORTED_CHANGED_DTM                                    AS component_install_date,
            ROW_NUMBER() OVER (
                PARTITION BY inc.availability_event_id
                ORDER BY
                    -- FIX 5: ILIKE -> LIKE (Spark SQL). Priority 0 = description match
                    CASE WHEN TRIM(COALESCE(inc.affected_component, '')) != ''
                              AND UPPER(hwc.COMPONENT_DESCRIPTION) LIKE
                                  CONCAT('%', UPPER(TRIM(inc.affected_component)), '%')
                         THEN 0 ELSE 1 END,
                    -- Fallback priority: most recently changed component
                    hwc.REPORTED_CHANGED_DTM DESC NULLS LAST
            )                                                           AS rn
        FROM all_incidents inc
        LEFT JOIN mars_dev.silver.hw_config_current hwc
            ON hwc.DEVICE_ID = inc.device_id
           AND hwc.mars_device_category IN ('TVM','GATE','VALIDATOR')  -- READER removed (component, not device)
    ) ranked
    WHERE rn = 1
),
-- PS3-GAP 2 fix: derive component type from fault description regex + event_code → dim_event_type
-- Covers ~40-70% of the 78.32% blank affected_component rows
component_derived AS (
    SELECT ai.availability_event_id,
        CASE
            WHEN TRIM(COALESCE(ai.affected_component, '')) != ''
                THEN UPPER(TRIM(ai.affected_component))
            WHEN UPPER(COALESCE(ai.AE_FAULT_DESCRIPTION, '')) LIKE '%PRINTER%'
              OR UPPER(COALESCE(ai.AE_FAULT_DESCRIPTION, '')) LIKE '%PAPER JAM%'
              OR UPPER(COALESCE(ai.AE_FAULT_DESCRIPTION, '')) LIKE '%RECEIPT%'
                THEN 'PRINTER'
            WHEN UPPER(COALESCE(ai.AE_FAULT_DESCRIPTION, '')) LIKE '%CARD READER%'
              OR UPPER(COALESCE(ai.AE_FAULT_DESCRIPTION, '')) LIKE '%CSC%'
              OR UPPER(COALESCE(ai.AE_FAULT_DESCRIPTION, '')) LIKE '%SMARTCARD%'
                THEN 'CSC_READER'
            WHEN UPPER(COALESCE(ai.AE_FAULT_DESCRIPTION, '')) LIKE '%BILL%'
              OR UPPER(COALESCE(ai.AE_FAULT_DESCRIPTION, '')) LIKE '%BHU%'
              OR UPPER(COALESCE(ai.AE_FAULT_DESCRIPTION, '')) LIKE '%BANK NOTE%'
                THEN 'BHU'
            WHEN UPPER(COALESCE(ai.AE_FAULT_DESCRIPTION, '')) LIKE '%COIN%'
              OR UPPER(COALESCE(ai.AE_FAULT_DESCRIPTION, '')) LIKE '%CHU%'
                THEN 'CHU'
            WHEN UPPER(COALESCE(ai.AE_FAULT_DESCRIPTION, '')) LIKE '%GATE%'
              OR UPPER(COALESCE(ai.AE_FAULT_DESCRIPTION, '')) LIKE '%TURNSTILE%'
                THEN 'GATE_MECH'
            WHEN UPPER(COALESCE(ai.AE_FAULT_DESCRIPTION, '')) LIKE '%COMM%'
              OR UPPER(COALESCE(ai.AE_FAULT_DESCRIPTION, '')) LIKE '%NETWORK%'
              OR UPPER(COALESCE(ai.AE_FAULT_DESCRIPTION, '')) LIKE '%OFFLINE%'
                THEN 'COMMS'
            WHEN det.component_subsystem IS NOT NULL THEN det.component_subsystem
            ELSE NULL
        END AS derived_component_type
    FROM all_incidents ai
    LEFT JOIN mars_dev.silver.dim_event_type det
        ON det.EVENT_TYPE_ID = ai.sn_event_code_id
),
-- PS3-GAP 3 partial fix: map event_code_id + failure_level → KPI_ID via edw_kpi_rules
-- Provides cause/KPI classification without waiting for Robin ServiceNow re-export
kpi_cause_class AS (
    -- edw_kpi_rules may have multiple KPI_IDs per (EVENT_ID, FAILURE_LEVEL); keep lowest KPI_ID.
    SELECT event_id_str, kr_failure_level, KPI_ID, kpi_category_name
    FROM (
        SELECT
            CAST(kr.EVENT_ID AS STRING) AS event_id_str,
            kr.FAILURE_LEVEL            AS kr_failure_level,
            kr.KPI_ID,
            k.KPI_NAME                  AS kpi_category_name,
            ROW_NUMBER() OVER (PARTITION BY kr.EVENT_ID, kr.FAILURE_LEVEL ORDER BY kr.KPI_ID ASC) AS rn
        FROM mars_dev.bronze.edw_kpi_rules kr
        LEFT JOIN mars_dev.bronze.edw_kpi k ON k.KPI_ID = kr.KPI_ID
    )
    WHERE rn = 1
)
SELECT
    ai.availability_event_id,
    ai.device_id,
    ai.DEVICE_KEY,
    ai.transit_day,
    ai.transit_day_key,
    ai.AE_START_DTM,
    ai.AE_END_DTM,
    ai.incident_duration_min,
    ai.mars_device_category,
    -- Prediction targets
    -- PS3-GAP 4 fix: merge level 1 (6 rows, ghost class) into level 2
    CASE WHEN ai.AE_FAILURE_LEVEL = 1 THEN 2 ELSE ai.AE_FAILURE_LEVEL END AS failure_level,
    ai.failure_level_label,
    ai.root_cause_category,
    -- Text features (for NLP / embedding model)
    ai.AE_FAULT_DESCRIPTION,
    ai.AE_SYMPTOM,
    ai.AE_PROBLEM,
    ai.AE_RESOLUTION,
    ai.affected_component,
    ai.wot_state,
    ai.request_type,
    -- Pre-incident event features (24h window)
    COALESCE(e24.events_24h_prior, 0)                 AS events_24h_prior,
    COALESCE(e24.oos_onsets_24h, 0)                   AS oos_onsets_24h,
    COALESCE(e24.bhu_24h, 0)                          AS bhu_events_24h,
    COALESCE(e24.chu_24h, 0)                          AS chu_events_24h,
    COALESCE(e24.printer_24h, 0)                      AS printer_events_24h,
    COALESCE(e24.gate_mech_24h, 0)                    AS gate_mech_events_24h,
    COALESCE(e24.csc_reader_24h, 0)                   AS csc_reader_events_24h,
    COALESCE(e24.comms_24h, 0)                        AS comms_events_24h,
    -- Pre-incident event features (7d window)
    COALESCE(c7d.oos_onsets_7d_prior, 0)              AS oos_onsets_7d_prior,
    COALESCE(c7d.events_7d_prior, 0)                  AS events_7d_prior,
    -- Component age at time of incident (FIX 7: exactly 1 row per incident)
    hw.component_age_days,
    hw.component_install_date,
    hw.COMPONENT_DESCRIPTION                          AS matched_component,
    hw.COMPONENT_SERIAL_NBR                           AS matched_serial_nbr,
    -- PS3-GAP 2: derived component type (regex on fault description + event_code fallback)
    cd.derived_component_type,
    -- PS3-GAP 3: KPI rule classification (event_code_id + failure_level → KPI_ID)
    kcc.KPI_ID                                        AS kpi_rule_id,
    kcc.kpi_category_name,
    -- Incident ServiceNow context
    ai.SN_U_EVENT_ID,
    ai.SN_SYS_ID,
    ai.AE_FAULT_STATE,
    -- Device context
    ai.DEVICE_NAME,
    ai.DEVICE_TYPE_NAME,
    ai.AE_FACILITY_ID                                  AS FACILITY_ID,
    ai.FACILITY_NAME,
    ai.AE_OPERATOR_ID                                  AS OPERATOR_ID,
    ai.OPERATOR_NAME,
    ai.DEVICE_SERIAL_NUMBER,
    -- Data source flags
    ai.from_cta_sn_mirror,
    ai.from_svn_stage,  -- Always FALSE (SVN_STAGE = 0 rows)
    -- Chargeable / fault classification (R2-1, added 2026-06-24)
    -- is_chargeable = failure_level > 0 (real hardware fault, SLA-chargeable)
    -- is_device_fault = failure_level IN (1,2,3,4,5,16) (PS3 cat-2 training scope)
    ai.is_chargeable,
    ai.is_device_fault,
    -- ServiceNow category enrichment from S15 incident_history (R2-3, added 2026-06-24)
    -- sn_category: 'Corrective Maintenance' = real breakdown, 'Planned Maintenance' = scheduled
    ai.sn_category,
    ai.sn_maintenance_type,
    ai.sn_priority,
    ai.sn_event_code_id,
    ai.sn_event_code_name,
    -- PS3-GAP 5: NLP keyword binary flags (SQL-only, ~1 day lift before embedding approach)
    CASE WHEN UPPER(COALESCE(ai.AE_FAULT_DESCRIPTION, '')) LIKE '%PRINTER%'
           OR UPPER(COALESCE(ai.AE_FAULT_DESCRIPTION, '')) LIKE '%PAPER%'
           OR UPPER(COALESCE(ai.AE_FAULT_DESCRIPTION, '')) LIKE '%RECEIPT%'
         THEN 1 ELSE 0 END                            AS desc_printer_flag,
    CASE WHEN UPPER(COALESCE(ai.AE_FAULT_DESCRIPTION, '')) LIKE '%CARD READER%'
           OR UPPER(COALESCE(ai.AE_FAULT_DESCRIPTION, '')) LIKE '%CSC%'
         THEN 1 ELSE 0 END                            AS desc_card_reader_flag,
    CASE WHEN UPPER(COALESCE(ai.AE_FAULT_DESCRIPTION, '')) LIKE '%BILL%'
           OR UPPER(COALESCE(ai.AE_FAULT_DESCRIPTION, '')) LIKE '%BHU%'
           OR UPPER(COALESCE(ai.AE_FAULT_DESCRIPTION, '')) LIKE '%BANK NOTE%'
           OR UPPER(COALESCE(ai.AE_FAULT_DESCRIPTION, '')) LIKE '%CASH%'
         THEN 1 ELSE 0 END                            AS desc_bill_handler_flag,
    CASE WHEN UPPER(COALESCE(ai.AE_FAULT_DESCRIPTION, '')) LIKE '%COIN%'
           OR UPPER(COALESCE(ai.AE_FAULT_DESCRIPTION, '')) LIKE '%CHU%'
         THEN 1 ELSE 0 END                            AS desc_coin_flag,
    CASE WHEN UPPER(COALESCE(ai.AE_FAULT_DESCRIPTION, '')) LIKE '%COMM%'
           OR UPPER(COALESCE(ai.AE_FAULT_DESCRIPTION, '')) LIKE '%NETWORK%'
           OR UPPER(COALESCE(ai.AE_FAULT_DESCRIPTION, '')) LIKE '%OFFLINE%'
         THEN 1 ELSE 0 END                            AS desc_comms_flag,
    CASE WHEN UPPER(COALESCE(ai.AE_FAULT_DESCRIPTION, '')) LIKE '%TIMEOUT%'
           OR UPPER(COALESCE(ai.AE_FAULT_DESCRIPTION, '')) LIKE '%SLOW%'
         THEN 1 ELSE 0 END                            AS desc_timeout_flag,
    CASE WHEN UPPER(COALESCE(ai.AE_FAULT_DESCRIPTION, '')) LIKE '%REPLACE%'
           OR UPPER(COALESCE(ai.AE_RESOLUTION, '')) LIKE '%REPLACED%'
         THEN 1 ELSE 0 END                            AS desc_replacement_flag
FROM all_incidents ai
LEFT JOIN events_24h_prior e24  ON e24.availability_event_id = ai.availability_event_id
LEFT JOIN critical_7d_prior c7d ON c7d.availability_event_id = ai.availability_event_id
LEFT JOIN hw_best_match hw      ON hw.availability_event_id  = ai.availability_event_id
LEFT JOIN component_derived cd  ON cd.availability_event_id  = ai.availability_event_id
LEFT JOIN kpi_cause_class kcc   ON CAST(kcc.event_id_str AS INT) = ai.sn_event_code_id
                                AND kcc.kr_failure_level = ai.AE_FAILURE_LEVEL;

-- FIX 6: CREATE INDEX (x6) removed -- not supported on Delta tables
-- Post-build:
-- OPTIMIZE mars_dev.gold.device_ps3_incident ZORDER BY (device_id, transit_day);
--
-- Post-build verification:
-- SELECT
--     mars_device_category,
--     COUNT(*)                                               AS total_rows,       -- Expect ~66,962 total
--     COUNT(DISTINCT device_id)                             AS distinct_devices,
--     COUNT(CASE WHEN failure_level = 2 THEN 1 END)        AS major_incidents,   -- Expect ~61K
--     COUNT(CASE WHEN failure_level = 3 THEN 1 END)        AS critical_incidents, -- Expect ~5.9K
--     COUNT(CASE WHEN failure_level = 1 THEN 1 END)        AS minor_incidents,   -- Expect 6
--     ROUND(AVG(oos_onsets_24h), 2)                        AS avg_oos_24h,
--     ROUND(AVG(component_age_days), 0)                    AS avg_component_age_days,
--     SUM(CASE WHEN matched_component IS NULL THEN 1 END)  AS no_hw_match
-- FROM mars_dev.gold.device_ps3_incident
-- GROUP BY mars_device_category;
-- Note: no_hw_match > 0 expected for devices not in hw_config_current
