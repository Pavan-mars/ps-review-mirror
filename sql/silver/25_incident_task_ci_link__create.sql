-- =============================================================================
-- S25 (DRAFT): silver.incident_task_ci_link
-- Added: 2026-07-07, validation pass (Chicago silver/gold rebuild review)
--
-- STATUS: DRAFT -- SCHEMA NOT YET CONFIRMED. Do not point a gold table at this
-- table until Part A of servicenow_xml_schema_probe_and_reconciliation.sql
-- (sql/diagnostics/) has been run and every column name below has been checked
-- against its real output. The bronze tables this reads from were loaded via
-- schema INFERENCE with no explicit schema and no sampled-data check against a
-- written spec (see ingest_servicenow_xml_bronze.py) -- past experience on this
-- project (nearly every existing silver file's own bug log) is that assumed
-- ServiceNow column names are wrong more often than not until checked. Treat
-- every non-sys_id column reference below as a hypothesis, not a fact.
--
-- PURPOSE:
-- This is a deliberately narrow, additive first step for wiring Robin's new
-- XML-based ServiceNow export (bronze.servicenow_incident, servicenow_task_ci,
-- and the CMDB CI class registries -- all landed this session, NOT yet consumed
-- by any existing silver/gold table) into the project. It does NOT touch or
-- replace S15 incident_history or S17 incident_root_cause, which remain the
-- proven, in-production path sourced from the older CSV-based ServiceNow tables.
--
-- It targets specifically the one concrete, well-scoped lead the ingestion plan
-- flagged as worth chasing: bronze.servicenow_task_ci's ci_item values include
-- BUS NUMBERS (e.g. "8013 Nova", "1539 New Flyer") -- a possible second path to
-- VALIDATOR (BMV bus) incident signal, independent of the availability-events /
-- incident_root_cause feed, which has confirmed 0% VALIDATOR coverage today
-- (BMV buses generate zero ServiceNow incidents visible to S17). If this table
-- shows real, joinable bus-number incident data, it would be the first ServiceNow
-- incident signal VALIDATOR has ever had in this project.
--
-- SOURCES (bronze, XML-based, this session):
-- mars_dev.bronze.servicenow_incident -- confirmed cols: sys_id, sys_created_on,
-- sys_updated_on (per bronze_data_contract registration). All other column names
-- below (u_chargeable, u_event_code, severity, priority, impact, close_notes,
-- cmdb_ci) are inferred from the ingestion plan's ml_role description and
-- standard ServiceNow incident-table conventions -- UNCONFIRMED.
-- mars_dev.bronze.servicenow_task_ci -- confirmed cols: sys_id, sys_created_on,
-- sys_updated_on. ci_item is named directly in the ingestion plan (bus-number
-- values sampled there); the incident-linkage field is assumed to be a standard
-- ServiceNow task_ci reference column named "task", which -- per
-- flatten_reference_columns() in the ingestion notebook -- would land as two
-- columns: task_sys_id and task_display_value. UNCONFIRMED.
--
-- WHAT THIS DOES NOT DO:
-- Does not replace cta_servicenow_incident/S15/S17 as the PS1/PS3 source.
-- Does not attempt the full incident/CMDB rebuild against the new export --
-- that is a bigger design decision (which source wins on conflict, whether to
-- union or replace, how to handle the ~25x row-count gap flagged in the
-- validation report between the two "incident" tables) deliberately left for a
-- separate, dedicated pass rather than bundled quietly into this fix.
--
-- NEXT STEPS AFTER SCHEMA CONFIRMATION:
-- 1. Correct any column names below that Part A's DESCRIBE TABLE output shows
--    are wrong.
-- 2. Run the post-build verification query at the bottom -- specifically the
--    validator_bus_number_hits count. If it's meaningfully greater than 0,
--    escalate to whoever owns the PS1/PS3 VALIDATOR-coverage gap; this table
--    would be new, real evidence, not yet incorporated into gold.
-- =============================================================================

DROP TABLE IF EXISTS mars_dev.silver.incident_task_ci_link;

CREATE TABLE mars_dev.silver.incident_task_ci_link AS
WITH

-- -- Incident core (subset of fields -- UNCONFIRMED column names, see header) --
inc AS (
  SELECT
    sys_id AS incident_sys_id,
    sys_created_on,
    sys_updated_on
    -- Add back once confirmed via the schema probe (Part A):
    -- , number AS incident_number
    -- , u_chargeable, u_event_code, severity, priority, close_notes
  FROM mars_dev.bronze.servicenow_incident
),

-- -- task_ci: incident-to-CI linkage; ci_item carries bus numbers per the ------
-- -- ingestion plan's sample (e.g. "8013 Nova", "1539 New Flyer")             --
task_link AS (
  SELECT
    sys_id AS task_ci_sys_id,
    -- UNCONFIRMED: assumed standard ServiceNow reference field "task", flattened
    -- by ingest_servicenow_xml_bronze.py's flatten_reference_columns() into
    -- task_sys_id / task_display_value. Verify both column names exist before
    -- relying on this join.
    task_sys_id AS linked_incident_sys_id,
    ci_item,
    -- Heuristic bus-number extraction: ci_item samples look like "8013 Nova" /
    -- "1539 New Flyer" (leading digits = bus number, rest = manufacturer/model).
    -- UNCONFIRMED beyond the two samples in the ingestion plan -- re-check
    -- against a real distinct-value pull before trusting this pattern broadly.
    CASE
      WHEN ci_item IS NOT NULL AND ci_item RLIKE '^[0-9]+'
        THEN CAST(regexp_extract(ci_item, '^([0-9]+)', 1) AS STRING)
      ELSE NULL
    END AS extracted_bus_number
  FROM mars_dev.bronze.servicenow_task_ci
)

SELECT
  t.task_ci_sys_id,
  t.linked_incident_sys_id,
  t.ci_item,
  t.extracted_bus_number,
  i.incident_sys_id,
  i.sys_created_on AS incident_created_on,
  i.sys_updated_on AS incident_updated_on
FROM task_link t
LEFT JOIN inc i
  ON i.incident_sys_id = t.linked_incident_sys_id;

-- Post-build verification (run after confirming the schema and re-running the
-- CREATE TABLE above with any needed column-name corrections):
-- SELECT
--   COUNT(*) AS total_rows,
--   COUNT(DISTINCT task_ci_sys_id) AS distinct_task_ci_rows,
--   COUNT(linked_incident_sys_id) AS rows_with_incident_match,
--   SUM(CASE WHEN extracted_bus_number IS NOT NULL THEN 1 ELSE 0 END)
--     AS validator_bus_number_hits,
--   COUNT(DISTINCT extracted_bus_number) AS distinct_bus_numbers
-- FROM mars_dev.silver.incident_task_ci_link;
--
-- If validator_bus_number_hits > 0: this is new VALIDATOR incident signal that
-- did not exist anywhere in the project before today. Next step would be
-- matching extracted_bus_number against dim_device / the BMV device population
-- to see how many distinct buses are reachable this way -- a real analysis
-- question for whoever owns PS1/PS3 VALIDATOR scope, not a pipeline question.
