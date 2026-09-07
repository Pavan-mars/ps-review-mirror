-- =============================================================================
-- S25: silver.incident_task_ci_link
-- Added: 2026-07-07, validation pass (Chicago silver/gold rebuild review)
--
-- STATUS: CONFIRMED AND VALIDATED 2026-07-15.
-- Schema confirmed via DESCRIBE TABLE on both bronze sources.
-- Post-build validation confirmed 823 VALIDATOR devices / 10,453 distinct
-- incidents reachable via bus-number join -- the first ServiceNow incident
-- signal VALIDATOR has ever had in this project (S17/S15 have 0% VALIDATOR
-- coverage). Safe to point PS1/PS3 gold at this table.
--
-- PURPOSE:
-- UPDATE 2026-07-22 (SIL-H1c): incident side repointed to
-- bronze.servicenow_incident_conformed (same schema as servicenow_incident;
-- merged CTA+XML export via NB100+NB101). Adds ~44K CTA-unique incidents.
-- Wires ServiceNow task_ci + conformed incidents as VALIDATOR incident signal.
-- Does NOT replace S15 incident_history or S17 incident_root_cause, which
-- remain the proven CSV-based path for TVM + GATE incidents.
--
-- KEY FINDING (confirmed 2026-07-15):
-- servicenow_task_ci.ci_item_display_value contains bus numbers for BMV devices
-- (e.g. "1855 New Flyer", "1265 New Flyer"). Joining via:
--   CONCAT('BMV', LPAD(extracted_bus_number, 5, '0')) = dim_device.DEVICE_ID
-- yields:
--   - 823 distinct VALIDATOR DEVICE_IDs matched  (~25% of ~3,290 BMV fleet)
--   - 10,453 distinct incidents linked to those devices
--   - 10,457 incident rows with DEVICE_KEY populated
-- This is real, joinable VALIDATOR incident data -- not previously available
-- anywhere in this project through S15/S17.
--
-- SOURCES (bronze, XML-based):
-- mars_dev.bronze.servicenow_incident -- flat cols confirmed via DESCRIBE 2026-07-15:
--   sys_id (string), number (string), sys_created_on/sys_updated_on (timestamp),
--   u_wm_asset (string), u_chargeable (boolean),
--   u_chargeable_level_display_value (string), u_event_code_display_value (string),
--   severity/priority/impact/urgency (bigint), category, subcategory, cause,
--   short_description, close_notes, close_code, opened_at, resolved_at,
--   closed_at (timestamp), incident_state/state (bigint), reopen_count (bigint),
--   u_major_incident (boolean), u_maintenance_type (string),
--   u_outage_start_time/u_outage_end_time (timestamp), u_ncs_device_id (string).
-- mars_dev.bronze.servicenow_task_ci -- flat cols confirmed via DESCRIBE 2026-07-15:
--   sys_id, sys_created_on, sys_updated_on (timestamp), _action, applied (bool),
--   ci_item_sys_id, ci_item_display_value,
--   task_sys_id, task_display_value,
--   _ingested_at, _source_file, _row_tag.
--   flatten_reference_columns() in the ingestion notebook split every SN reference
--   field into {field}_sys_id + {field}_display_value -- no xpath needed.
--
-- JOIN KEY (confirmed 2026-07-15):
--   CONCAT('BMV', LPAD(extracted_bus_number, 5, '0')) = dim_device.DEVICE_ID
--   e.g. ci_item "1855 New Flyer" -> extracted "1855" -> "BMV01855" -> DEVICE_KEY
--
-- WHAT THIS DOES NOT DO:
-- Does not replace cta_servicenow_incident/S15/S17 as the PS1/PS3 source.
-- Does not attempt the full incident/CMDB rebuild against the new XML export.
--
-- NEXT STEPS:
-- 1. DONE (2026-07-15): schema confirmed, join validated, DEVICE_KEY wired.
-- 2. Wire DEVICE_KEY from this table into PS1 gold as new VALIDATOR incident
--    features (incident count, chargeable count, MTTR per device per day).
--    Current PS1 VALIDATOR has 0 incident features; this provides ~25% coverage.
-- 3. Consider wiring into PS3 gold for VALIDATOR root-cause classification.
-- =============================================================================

DROP TABLE IF EXISTS mars_dev.silver.incident_task_ci_link;

CREATE TABLE mars_dev.silver.incident_task_ci_link
USING DELTA
COMMENT 'Silver Table 25. VALIDATOR incident signal via servicenow_task_ci bus-number linkage. 823 BMV devices / 10,453 incidents confirmed 2026-07-15.'
TBLPROPERTIES (
  'quality' = 'silver',
  'pipeline.table_number' = '25'
)
AS
WITH

-- -- Incident core: conformed SN export (SIL-H1c 2026-07-22) ------------------
inc AS (
  SELECT
    sys_id                              AS incident_sys_id,
    number                              AS incident_number,
    sys_created_on,
    sys_updated_on,
    opened_at,
    resolved_at,
    closed_at,
    u_chargeable,
    u_chargeable_level_display_value    AS chargeable_level,
    u_event_code_display_value          AS event_code,
    category,
    subcategory,
    cause,
    short_description,
    close_notes,
    close_code,
    CAST(priority AS INT)               AS priority,
    CAST(severity AS INT)               AS severity,
    CAST(incident_state AS INT)         AS incident_state,
    CAST(reopen_count AS INT)           AS reopen_count,
    u_major_incident,
    u_maintenance_type,
    u_outage_start_time,
    u_outage_end_time
  FROM mars_dev.bronze.servicenow_incident_conformed
  QUALIFY ROW_NUMBER() OVER (PARTITION BY sys_id ORDER BY sys_updated_on DESC) = 1
),

-- -- task_ci: incident-to-CI linkage; ci_item_display_value carries bus numbers -
task_link AS (
  SELECT
    sys_id                              AS task_ci_sys_id,
    task_sys_id                         AS linked_incident_sys_id,
    task_display_value                  AS linked_incident_number,
    ci_item_display_value               AS ci_item,
    -- Bus-number extraction: "1855 New Flyer" -> "1855"
    -- Confirmed format: leading digits = bus number, rest = manufacturer name.
    CASE
      WHEN ci_item_display_value IS NOT NULL
        AND ci_item_display_value RLIKE '^[0-9]+'
        THEN regexp_extract(ci_item_display_value, '^([0-9]+)', 1)
      ELSE NULL
    END                                 AS extracted_bus_number,
    -- Constructed DEVICE_ID for dim_device join: "1855" -> "BMV01855"
    CASE
      WHEN ci_item_display_value IS NOT NULL
        AND ci_item_display_value RLIKE '^[0-9]+'
        THEN CONCAT('BMV', LPAD(regexp_extract(ci_item_display_value, '^([0-9]+)', 1), 5, '0'))
      ELSE NULL
    END                                 AS constructed_device_id
  FROM mars_dev.bronze.servicenow_task_ci
)

SELECT
  -- task_ci linkage
  t.task_ci_sys_id,
  t.linked_incident_sys_id,
  t.linked_incident_number,
  t.ci_item,
  t.extracted_bus_number,
  t.constructed_device_id,

  -- dim_device enrichment (VALIDATOR devices only via bus-number join)
  d.DEVICE_KEY,
  d.DEVICE_ID,
  d.mars_device_category,
  d.FACILITY_NAME,
  d.FACILITY_ID,
  d.OPERATOR_ID,

  -- Incident fields
  i.incident_sys_id,
  i.incident_number,
  i.sys_created_on                      AS incident_created_on,
  i.sys_updated_on                      AS incident_updated_on,
  i.opened_at,
  i.resolved_at,
  i.closed_at,
  CAST(DATE(i.opened_at) AS DATE)       AS incident_date,
  CASE
    WHEN i.resolved_at IS NOT NULL AND i.opened_at IS NOT NULL
      THEN (unix_timestamp(i.resolved_at) - unix_timestamp(i.opened_at)) / 60.0
    ELSE NULL
  END                                   AS time_to_resolve_minutes,
  i.u_chargeable                        AS is_chargeable,
  i.chargeable_level,
  i.event_code,
  i.category,
  i.subcategory,
  i.cause,
  i.short_description,
  i.close_notes,
  i.close_code,
  i.priority,
  i.severity,
  i.incident_state,
  i.reopen_count,
  i.u_major_incident                    AS is_major_incident,
  i.u_maintenance_type                  AS maintenance_type,
  i.u_outage_start_time,
  i.u_outage_end_time,

  current_timestamp()                   AS _silver_load_ts

FROM task_link t
INNER JOIN inc i
  ON i.incident_sys_id = t.linked_incident_sys_id
LEFT JOIN mars_dev.silver.dim_device d
  ON d.DEVICE_ID = t.constructed_device_id
 AND d.is_current = TRUE
WHERE i.incident_number IS NOT NULL;

-- Post-build verification:
-- SELECT
--   COUNT(*)                                                            AS total_rows,
--   COUNT(DISTINCT task_ci_sys_id)                                      AS distinct_task_ci_rows,
--   SUM(CASE WHEN extracted_bus_number IS NOT NULL THEN 1 ELSE 0 END)  AS bus_number_hits,
--   COUNT(DISTINCT extracted_bus_number)                                AS distinct_bus_numbers,
--   COUNT(DISTINCT DEVICE_KEY)                                          AS matched_validator_devices,
--   COUNT(DISTINCT incident_number)                                     AS distinct_incidents,
--   SUM(CASE WHEN is_chargeable = TRUE THEN 1 ELSE 0 END)             AS chargeable_incidents,
--   ROUND(AVG(time_to_resolve_minutes), 0)                             AS avg_mttr_minutes
-- FROM mars_dev.silver.incident_task_ci_link
-- WHERE DEVICE_KEY IS NOT NULL;
--
-- Expected (confirmed 2026-07-15):
--   matched_validator_devices ~ 823, distinct_incidents ~ 10,453
