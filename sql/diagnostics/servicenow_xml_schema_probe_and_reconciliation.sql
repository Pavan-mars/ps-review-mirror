-- =============================================================================
-- DIAGNOSTIC: ServiceNow XML bronze -- schema probe + row-count reconciliation
-- Added: 2026-07-07, validation pass (Chicago silver/gold rebuild review)
--
-- WHY THIS EXISTS:
-- Robin's second, XML-based ServiceNow export landed as 10 new bronze tables this
-- session (ingest_servicenow_xml_bronze.py, registered in bronze_data_contract as
-- SERVICENOW.INCIDENT / SERVICENOW.TASK_CI / etc.). Every silver table that reads
-- ServiceNow data today (S15 incident_history, S17 incident_root_cause, and the
-- PS3 gold table through S17) still reads the OLDER CSV-based tables
-- (cta_servicenow_incident, cta_servicenow_cmdb_ci, etc., loaded 2026-06-24) --
-- none of them reference the new tables yet. Before wiring the new tables into
-- anything (see 25_incident_task_ci_link__create.sql in this same PR for a first,
-- narrowly-scoped attempt), run Part A below to see their real column names --
-- the ingestion notebook used schema INFERENCE (no explicit schema was ever
-- specified or verified against sample data), so nothing past sys_id /
-- sys_created_on / sys_updated_on / the flatten_reference_columns()
-- {field}_sys_id / {field}_display_value suffix pattern is confirmed.
--
-- Part B addresses a separate, specific finding from the validation review: S15's
-- header states its source bronze.cta_servicenow_incident at 7,028,048 rows, but
-- the 2026-06-25 project record for the same CSV drop states 278,001 rows for the
-- "incident" table -- a 25x difference, while the other 3 tables in that same drop
-- (cmdb_ci, cmdb_model, cmdb_model_category) match the earlier figures within 2%.
-- Part B just re-counts the live tables so this can be confirmed or corrected with
-- a real number rather than reconciled by guessing which historical note is stale.
-- =============================================================================


-- -----------------------------------------------------------------------------
-- PART A -- schema probe: real column names for all 10 new bronze tables
-- Run each DESCRIBE and read the Data type / Comment columns before writing any
-- SQL that references a business field (not sys_id/sys_created_on/sys_updated_on)
-- against these tables.
-- -----------------------------------------------------------------------------

DESCRIBE TABLE mars_dev.bronze.servicenow_incident;
DESCRIBE TABLE mars_dev.bronze.servicenow_task_ci;
DESCRIBE TABLE mars_dev.bronze.servicenow_cmdb_ci_pos_device;
DESCRIBE TABLE mars_dev.bronze.servicenow_cmdb_ci_onboard_card_interface;
DESCRIBE TABLE mars_dev.bronze.servicenow_cmdb_ci_card_handling;
DESCRIBE TABLE mars_dev.bronze.servicenow_cmdb_ci_netgear;
DESCRIBE TABLE mars_dev.bronze.servicenow_cmdb_ci_acc;
DESCRIBE TABLE mars_dev.bronze.servicenow_cmdb_hardware_product_model;
DESCRIBE TABLE mars_dev.bronze.servicenow_cmdb_software_component_model;
DESCRIBE TABLE mars_dev.bronze.servicenow_cmdb_model_category;

-- Quick eyeball of a handful of real rows helps as much as the schema listing --
-- reference fields will show up as two flattened columns ({field}_sys_id,
-- {field}_display_value); confirm which display-value columns look like usable
-- join keys (device IDs, bus numbers, etc.) before assuming a name.
SELECT * FROM mars_dev.bronze.servicenow_incident LIMIT 5;
SELECT * FROM mars_dev.bronze.servicenow_task_ci LIMIT 20;


-- -----------------------------------------------------------------------------
-- PART B -- row-count reconciliation: new XML bronze vs. older CSV bronze
-- (where an equivalent table exists), and vs. the historical project record.
-- -----------------------------------------------------------------------------

SELECT 'servicenow_incident (new, XML)' AS table_label, COUNT(*) AS row_count
FROM mars_dev.bronze.servicenow_incident
UNION ALL
SELECT 'cta_servicenow_incident (old, CSV)', COUNT(*)
FROM mars_dev.bronze.cta_servicenow_incident
-- Historical project record (2026-06-25 note) for this same CSV table: 278,001 rows.
-- 15_incident_history__create.sql's own header states 7,028,048 rows for the same
-- table. If the live COUNT(*) below matches 7,028,048, the CSV table was likely
-- reloaded at a broader scope sometime after 2026-06-25 (e.g. all CTA ServiceNow
-- incidents, not only fare-device ones) -- confirm with Robin/Sathish which is
-- current rather than assuming either number.

UNION ALL
SELECT 'servicenow_cmdb_ci_pos_device + onboard_card_interface + card_handling + netgear + acc (new, XML, summed)',
       (SELECT COUNT(*) FROM mars_dev.bronze.servicenow_cmdb_ci_pos_device)
     + (SELECT COUNT(*) FROM mars_dev.bronze.servicenow_cmdb_ci_onboard_card_interface)
     + (SELECT COUNT(*) FROM mars_dev.bronze.servicenow_cmdb_ci_card_handling)
     + (SELECT COUNT(*) FROM mars_dev.bronze.servicenow_cmdb_ci_netgear)
     + (SELECT COUNT(*) FROM mars_dev.bronze.servicenow_cmdb_ci_acc)
UNION ALL
SELECT 'cta_servicenow_cmdb_ci (old, CSV, single combined table)', COUNT(*)
FROM mars_dev.bronze.cta_servicenow_cmdb_ci

UNION ALL
SELECT 'servicenow_cmdb_hardware_product_model + software_component_model (new, XML, summed)',
       (SELECT COUNT(*) FROM mars_dev.bronze.servicenow_cmdb_hardware_product_model)
     + (SELECT COUNT(*) FROM mars_dev.bronze.servicenow_cmdb_software_component_model)
UNION ALL
SELECT 'cta_servicenow_cmdb_model (old, CSV, single combined table)', COUNT(*)
FROM mars_dev.bronze.cta_servicenow_cmdb_model

UNION ALL
SELECT 'servicenow_cmdb_model_category (new, XML)', COUNT(*)
FROM mars_dev.bronze.servicenow_cmdb_model_category
UNION ALL
SELECT 'cta_servicenow_cmdb_model_category (old, CSV)', COUNT(*)
FROM mars_dev.bronze.cta_servicenow_cmdb_model_category

UNION ALL
SELECT 'servicenow_task_ci (new, XML -- no CSV equivalent, net-new)', COUNT(*)
FROM mars_dev.bronze.servicenow_task_ci;

-- Note: the new XML export's CI tables split what the old CSV export kept as one
-- combined cmdb_ci / cmdb_model table into 5 / 2 device-class-specific tables
-- respectively (see the ingestion plan, section 0.1) -- summing them for this
-- comparison is intentional, not an error.
