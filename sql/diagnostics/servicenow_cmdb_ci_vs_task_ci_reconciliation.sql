-- =============================================================================
-- DIAGNOSTIC: cmdb_ci (CSV) vs cmdb_ci_* (XML) vs task_ci (XML) — same table?
-- Added: 2026-08-13
--
-- Hypothesis to test: "CSV cmdb_ci and XML task_ci might be the same data saved
-- under different names at different times."
--
-- ServiceNow semantics (confirmed in schema workbook + raw XML headers):
--   cmdb_ci*     = Configuration Item records (assets/devices) — one row per CI
--   task_ci      = Incident↔CI link rows — many rows per incident, many per CI
-- They are DIFFERENT objects. XML ingest saved them separately:
--   CSV  cmdb_ci  → bronze.cta_servicenow_cmdb_ci
--   XML  cmdb_ci  → bronze.servicenow_cmdb_ci_{acc,card_handling,netgear,...}
--   XML  task_ci  → bronze.servicenow_task_ci  (NOT a cmdb_ci rename)
--
-- Run on Databricks. If Part 1 shows wildly different schemas/grains, the
-- "saved as task_ci by mistake" theory is ruled out.
-- =============================================================================

USE CATALOG mars_dev;

-- -----------------------------------------------------------------------------
-- PART 1 — Schema / grain sanity (should rule out "same table" immediately)
-- -----------------------------------------------------------------------------
SELECT 'cta_servicenow_cmdb_ci (CSV)' AS label, COUNT(*) AS rows, COUNT(DISTINCT name) AS distinct_name
FROM mars_dev.bronze.cta_servicenow_cmdb_ci;

SELECT 'servicenow_task_ci (XML link)' AS label, COUNT(*) AS rows,
       COUNT(DISTINCT task_sys_id) AS distinct_incidents,
       COUNT(DISTINCT ci_item_sys_id) AS distinct_cis
FROM mars_dev.bronze.servicenow_task_ci;

SELECT 'XML cmdb_ci union (5 class tables)' AS label, SUM(cnt) AS rows, SUM(distinct_sys_id) AS distinct_sys_id
FROM (
  SELECT COUNT(*) cnt, COUNT(DISTINCT sys_id) distinct_sys_id FROM mars_dev.bronze.servicenow_cmdb_ci_pos_device
  UNION ALL SELECT COUNT(*), COUNT(DISTINCT sys_id) FROM mars_dev.bronze.servicenow_cmdb_ci_card_handling
  UNION ALL SELECT COUNT(*), COUNT(DISTINCT sys_id) FROM mars_dev.bronze.servicenow_cmdb_ci_netgear
  UNION ALL SELECT COUNT(*), COUNT(DISTINCT sys_id) FROM mars_dev.bronze.servicenow_cmdb_ci_onboard_card_interface
  UNION ALL SELECT COUNT(*), COUNT(DISTINCT sys_id) FROM mars_dev.bronze.servicenow_cmdb_ci_acc
) u;

-- Column counts — cmdb_ci CSV ~108 cols; task_ci ~27 cols (link table only)
SELECT 'cta_servicenow_cmdb_ci' AS tbl, COUNT(*) AS col_count
FROM mars_dev.information_schema.columns
WHERE table_schema = 'bronze' AND table_name = 'cta_servicenow_cmdb_ci'
UNION ALL
SELECT 'servicenow_task_ci', COUNT(*)
FROM mars_dev.information_schema.columns
WHERE table_schema = 'bronze' AND table_name = 'servicenow_task_ci';

-- -----------------------------------------------------------------------------
-- PART 2 — Overlap: CSV cmdb_ci.name vs task_ci.ci_item_display_value
-- (If task_ci were mis-saved cmdb_ci, names might overlap heavily — expect low)
-- -----------------------------------------------------------------------------
WITH csv_ci AS (
  SELECT DISTINCT TRIM(name) AS ci_name FROM mars_dev.bronze.cta_servicenow_cmdb_ci WHERE name IS NOT NULL
),
task_ci AS (
  SELECT DISTINCT TRIM(ci_item_display_value) AS ci_display
  FROM mars_dev.bronze.servicenow_task_ci WHERE ci_item_display_value IS NOT NULL
)
SELECT
  (SELECT COUNT(*) FROM csv_ci) AS csv_distinct_names,
  (SELECT COUNT(*) FROM task_ci) AS task_ci_distinct_ci_displays,
  (SELECT COUNT(*) FROM csv_ci c JOIN task_ci t ON c.ci_name = t.ci_display) AS exact_name_overlap;

-- -----------------------------------------------------------------------------
-- PART 3 — Overlap: CSV cmdb_ci vs XML cmdb_ci union (the pair we never merged)
-- CSV has no sys_id — join on name only
-- -----------------------------------------------------------------------------
WITH xml_ci AS (
  SELECT sys_id, TRIM(name) AS ci_name, 'pos_device' AS src FROM mars_dev.bronze.servicenow_cmdb_ci_pos_device
  UNION ALL SELECT sys_id, TRIM(name), 'card_handling' FROM mars_dev.bronze.servicenow_cmdb_ci_card_handling
  UNION ALL SELECT sys_id, TRIM(name), 'netgear' FROM mars_dev.bronze.servicenow_cmdb_ci_netgear
  UNION ALL SELECT sys_id, TRIM(name), 'onboard_card_interface' FROM mars_dev.bronze.servicenow_cmdb_ci_onboard_card_interface
  UNION ALL SELECT sys_id, TRIM(name), 'acc' FROM mars_dev.bronze.servicenow_cmdb_ci_acc
),
csv_ci AS (
  SELECT TRIM(name) AS ci_name FROM mars_dev.bronze.cta_servicenow_cmdb_ci WHERE name IS NOT NULL
)
SELECT
  (SELECT COUNT(DISTINCT ci_name) FROM csv_ci) AS csv_names,
  (SELECT COUNT(DISTINCT ci_name) FROM xml_ci) AS xml_names,
  (SELECT COUNT(*) FROM (SELECT DISTINCT ci_name FROM csv_ci) c
   JOIN (SELECT DISTINCT ci_name FROM xml_ci) x USING (ci_name)) AS name_overlap;

-- -----------------------------------------------------------------------------
-- PART 4 — task_ci.ci_item_sys_id → XML cmdb_ci.sys_id (correct link path)
-- -----------------------------------------------------------------------------
WITH xml_ci AS (
  SELECT sys_id FROM mars_dev.bronze.servicenow_cmdb_ci_pos_device
  UNION SELECT sys_id FROM mars_dev.bronze.servicenow_cmdb_ci_card_handling
  UNION SELECT sys_id FROM mars_dev.bronze.servicenow_cmdb_ci_netgear
  UNION SELECT sys_id FROM mars_dev.bronze.servicenow_cmdb_ci_onboard_card_interface
  UNION SELECT sys_id FROM mars_dev.bronze.servicenow_cmdb_ci_acc
),
task AS (
  SELECT DISTINCT ci_item_sys_id FROM mars_dev.bronze.servicenow_task_ci
  WHERE ci_item_sys_id IS NOT NULL
)
SELECT
  COUNT(*) AS task_ci_distinct_ci_refs,
  SUM(CASE WHEN x.sys_id IS NOT NULL THEN 1 ELSE 0 END) AS resolve_to_xml_cmdb_ci,
  ROUND(100.0 * SUM(CASE WHEN x.sys_id IS NOT NULL THEN 1 ELSE 0 END) / COUNT(*), 2) AS pct_resolved
FROM task t
LEFT JOIN xml_ci x ON x.sys_id = t.ci_item_sys_id;

-- -----------------------------------------------------------------------------
-- PART 5 — incident.cmdb_ci_sys_id → XML cmdb_ci vs CSV name (S15 impact)
-- -----------------------------------------------------------------------------
WITH inc AS (
  SELECT cmdb_ci_sys_id, cmdb_ci_display_value
  FROM mars_dev.bronze.servicenow_incident_conformed
  WHERE cmdb_ci_sys_id IS NOT NULL
  QUALIFY ROW_NUMBER() OVER (PARTITION BY sys_id ORDER BY sys_updated_on DESC) = 1
),
xml_ci AS (
  SELECT sys_id, TRIM(name) AS ci_name FROM mars_dev.bronze.servicenow_cmdb_ci_pos_device
  UNION ALL SELECT sys_id, TRIM(name) FROM mars_dev.bronze.servicenow_cmdb_ci_card_handling
  UNION ALL SELECT sys_id, TRIM(name) FROM mars_dev.bronze.servicenow_cmdb_ci_netgear
  UNION ALL SELECT sys_id, TRIM(name) FROM mars_dev.bronze.servicenow_cmdb_ci_onboard_card_interface
  UNION ALL SELECT sys_id, TRIM(name) FROM mars_dev.bronze.servicenow_cmdb_ci_acc
)
SELECT
  COUNT(*) AS incidents_with_cmdb_ci_sys_id,
  SUM(CASE WHEN x.sys_id IS NOT NULL THEN 1 ELSE 0 END) AS match_xml_cmdb_sys_id,
  SUM(CASE WHEN c.name IS NOT NULL THEN 1 ELSE 0 END) AS match_csv_cmdb_by_display_name
FROM inc i
LEFT JOIN xml_ci x ON x.sys_id = i.cmdb_ci_sys_id
LEFT JOIN mars_dev.bronze.cta_servicenow_cmdb_ci c
  ON TRIM(c.name) = TRIM(i.cmdb_ci_display_value);

-- -----------------------------------------------------------------------------
-- PART 6 — Sample rows side-by-side (visual proof of different shapes)
-- -----------------------------------------------------------------------------
SELECT 'CSV cmdb_ci sample' AS src, name, asset_tag, serial_number, model_id
FROM mars_dev.bronze.cta_servicenow_cmdb_ci LIMIT 5;

SELECT 'XML task_ci sample' AS src, task_sys_id, task_display_value, ci_item_sys_id, ci_item_display_value
FROM mars_dev.bronze.servicenow_task_ci LIMIT 5;

SELECT 'XML cmdb_ci pos_device sample' AS src, sys_id, name, asset_tag, model_id_sys_id
FROM mars_dev.bronze.servicenow_cmdb_ci_pos_device LIMIT 5;
