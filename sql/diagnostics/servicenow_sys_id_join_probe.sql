-- =============================================================================
-- DIAGNOSTIC: ServiceNow sys_id join probe (Robin hypothesis, 2026-08)
--
-- Tests whether CMDB linkage should use sys_id GUIDs (XML export) rather than
-- display-name joins (CSV export). Robin's example:
--   incident.number                  = INC8051351
--   incident.cmdb_ci                 = 778d88a90fe0b2c46022715ce1050e1c  (target CI sys_id)
--   incident.cmdb_ci__display_value  = "TVM03804 VENDOR ASSY-CTA OSFS"
--
-- In mars_dev bronze (XML ingest), reference fields are flattened as:
--   cmdb_ci_sys_id, cmdb_ci_display_value
--   model_id_sys_id, model_id_display_value
--   cmdb_model_category_sys_id, cmdb_model_category_display_value
--
-- Join hypotheses:
--   (a) incident.cmdb_ci_sys_id  = cmdb_ci.sys_id          (union of 5 XML CI class tables)
--   (b) cmdb_ci.model_id_sys_id  = cmdb_model.sys_id      (hardware + software model tables)
--   (c) cmdb_model.cmdb_model_category_sys_id = cmdb_model_category.sys_id
--   (d) incident.u_wm_asset      = dim_device.DEVICE_ID   (text / Ventra device id)
--
-- Run on Databricks against mars_dev. Read PART 7 summary last.
-- Prior note (S15 header 2026-07-20): (a) had ZERO matches vs the 5 XML CI tables;
-- this probe re-tests with match rates, GUID format checks, and name-join comparison.
-- =============================================================================

USE CATALOG mars_dev;

-- -----------------------------------------------------------------------------
-- PART 0 — column names (confirm Robin's field names vs flattened bronze cols)
-- -----------------------------------------------------------------------------
DESCRIBE TABLE mars_dev.bronze.servicenow_incident_conformed;
-- Expect: cmdb_ci_sys_id, cmdb_ci_display_value, u_wm_asset, number, sys_id

-- -----------------------------------------------------------------------------
-- PART 1 — is cmdb_ci_sys_id GUID-shaped? (32 hex chars, ServiceNow style)
-- -----------------------------------------------------------------------------
WITH inc AS (
  SELECT
    number,
    cmdb_ci_sys_id,
    cmdb_ci_display_value,
    u_wm_asset
  FROM mars_dev.bronze.servicenow_incident_conformed
  WHERE cmdb_ci_sys_id IS NOT NULL AND TRIM(cmdb_ci_sys_id) <> ''
)
SELECT
  COUNT(*) AS rows_with_cmdb_ci_sys_id,
  SUM(CASE WHEN cmdb_ci_sys_id RLIKE '^[0-9a-fA-F]{32}$' THEN 1 ELSE 0 END) AS guid_32_hex,
  SUM(CASE WHEN cmdb_ci_sys_id NOT RLIKE '^[0-9a-fA-F]{32}$' THEN 1 ELSE 0 END) AS not_guid_shaped,
  ROUND(100.0 * SUM(CASE WHEN cmdb_ci_sys_id RLIKE '^[0-9a-fA-F]{32}$' THEN 1 ELSE 0 END) / COUNT(*), 2)
    AS pct_guid_shaped
FROM inc;

-- Sample non-GUID values (if any)
SELECT cmdb_ci_sys_id, cmdb_ci_display_value, number
FROM mars_dev.bronze.servicenow_incident_conformed
WHERE cmdb_ci_sys_id IS NOT NULL
  AND cmdb_ci_sys_id NOT RLIKE '^[0-9a-fA-F]{32}$'
LIMIT 20;

-- -----------------------------------------------------------------------------
-- PART 2 — Robin spot check (edit number if needed)
-- -----------------------------------------------------------------------------
SELECT
  number,
  sys_id AS incident_sys_id,
  cmdb_ci_sys_id,
  cmdb_ci_display_value,
  u_wm_asset,
  opened_at
FROM mars_dev.bronze.servicenow_incident_conformed
WHERE UPPER(TRIM(number)) = 'INC8051351'
   OR number = '8051351';

-- -----------------------------------------------------------------------------
-- PART 3 — (a) incident.cmdb_ci_sys_id → CMDB CI.sys_id (5 XML class tables)
-- -----------------------------------------------------------------------------
WITH inc AS (
  SELECT
    sys_id AS incident_sys_id,
    number,
    cmdb_ci_sys_id,
    cmdb_ci_display_value,
    u_wm_asset
  FROM mars_dev.bronze.servicenow_incident_conformed
  WHERE cmdb_ci_sys_id IS NOT NULL AND TRIM(cmdb_ci_sys_id) <> ''
  QUALIFY ROW_NUMBER() OVER (PARTITION BY sys_id ORDER BY sys_updated_on DESC) = 1
),
ci_union AS (
  SELECT sys_id, name, model_id_sys_id, model_id_display_value, 'pos_device' AS ci_class
  FROM mars_dev.bronze.servicenow_cmdb_ci_pos_device
  UNION ALL
  SELECT sys_id, name, model_id_sys_id, model_id_display_value, 'card_handling'
  FROM mars_dev.bronze.servicenow_cmdb_ci_card_handling
  UNION ALL
  SELECT sys_id, name, model_id_sys_id, model_id_display_value, 'netgear'
  FROM mars_dev.bronze.servicenow_cmdb_ci_netgear
  UNION ALL
  SELECT sys_id, name, model_id_sys_id, model_id_display_value, 'onboard_card_interface'
  FROM mars_dev.bronze.servicenow_cmdb_ci_onboard_card_interface
  UNION ALL
  SELECT sys_id, name, model_id_sys_id, model_id_display_value, 'acc'
  FROM mars_dev.bronze.servicenow_cmdb_ci_acc
),
joined AS (
  SELECT
    i.*,
    c.sys_id AS matched_ci_sys_id,
    c.name AS matched_ci_name,
    c.ci_class,
    c.model_id_sys_id
  FROM inc i
  LEFT JOIN ci_union c ON c.sys_id = i.cmdb_ci_sys_id
)
SELECT
  COUNT(*) AS incidents_with_cmdb_ci_sys_id,
  SUM(CASE WHEN matched_ci_sys_id IS NOT NULL THEN 1 ELSE 0 END) AS matched_via_sys_id,
  ROUND(100.0 * SUM(CASE WHEN matched_ci_sys_id IS NOT NULL THEN 1 ELSE 0 END) / COUNT(*), 2)
    AS sys_id_match_rate_pct,
  COUNT(DISTINCT matched_ci_sys_id) AS distinct_cis_matched
FROM joined;

-- Match rate by CI class (where did sys_ids land?)
SELECT
  ci_class,
  COUNT(*) AS incident_rows
FROM (
  SELECT i.cmdb_ci_sys_id, c.ci_class
  FROM mars_dev.bronze.servicenow_incident_conformed i
  JOIN (
    SELECT sys_id, 'pos_device' AS ci_class FROM mars_dev.bronze.servicenow_cmdb_ci_pos_device
    UNION ALL SELECT sys_id, 'card_handling' FROM mars_dev.bronze.servicenow_cmdb_ci_card_handling
    UNION ALL SELECT sys_id, 'netgear' FROM mars_dev.bronze.servicenow_cmdb_ci_netgear
    UNION ALL SELECT sys_id, 'onboard_card_interface' FROM mars_dev.bronze.servicenow_cmdb_ci_onboard_card_interface
    UNION ALL SELECT sys_id, 'acc' FROM mars_dev.bronze.servicenow_cmdb_ci_acc
  ) c ON c.sys_id = i.cmdb_ci_sys_id
  WHERE i.cmdb_ci_sys_id IS NOT NULL
) x
GROUP BY ci_class
ORDER BY incident_rows DESC;

-- -----------------------------------------------------------------------------
-- PART 3b — (a alt) name join: cmdb_ci_display_value = cta CSV cmdb_ci.name
-- (Old S15 v1 path — compare to sys_id path)
-- -----------------------------------------------------------------------------
WITH inc AS (
  SELECT number, cmdb_ci_sys_id, cmdb_ci_display_value, u_wm_asset
  FROM mars_dev.bronze.servicenow_incident_conformed
  WHERE cmdb_ci_display_value IS NOT NULL AND TRIM(cmdb_ci_display_value) <> ''
),
ci_csv AS (
  SELECT name AS ci_name, asset_tag, serial_number, model_id AS model_name_display
  FROM mars_dev.bronze.cta_servicenow_cmdb_ci
)
SELECT
  COUNT(*) AS incidents_with_cmdb_ci_display,
  SUM(CASE WHEN ci.ci_name IS NOT NULL THEN 1 ELSE 0 END) AS matched_via_display_name,
  ROUND(100.0 * SUM(CASE WHEN ci.ci_name IS NOT NULL THEN 1 ELSE 0 END) / COUNT(*), 2)
    AS display_name_match_rate_pct
FROM inc i
LEFT JOIN ci_csv ci ON TRIM(i.cmdb_ci_display_value) = TRIM(ci.ci_name);

-- -----------------------------------------------------------------------------
-- PART 4 — (b) cmdb_ci.model_id_sys_id → cmdb_model.sys_id (on sys_id-matched CIs)
-- -----------------------------------------------------------------------------
WITH matched_ci AS (
  SELECT c.sys_id, c.name, c.model_id_sys_id, c.ci_class
  FROM (
    SELECT sys_id, name, model_id_sys_id, 'pos_device' AS ci_class
    FROM mars_dev.bronze.servicenow_cmdb_ci_pos_device
    UNION ALL SELECT sys_id, name, model_id_sys_id, 'card_handling'
    FROM mars_dev.bronze.servicenow_cmdb_ci_card_handling
    UNION ALL SELECT sys_id, name, model_id_sys_id, 'netgear'
    FROM mars_dev.bronze.servicenow_cmdb_ci_netgear
    UNION ALL SELECT sys_id, name, model_id_sys_id, 'onboard_card_interface'
    FROM mars_dev.bronze.servicenow_cmdb_ci_onboard_card_interface
    UNION ALL SELECT sys_id, name, model_id_sys_id, 'acc'
    FROM mars_dev.bronze.servicenow_cmdb_ci_acc
  ) c
  WHERE c.sys_id IN (
    SELECT DISTINCT cmdb_ci_sys_id
    FROM mars_dev.bronze.servicenow_incident_conformed
    WHERE cmdb_ci_sys_id IS NOT NULL
  )
),
models AS (
  SELECT sys_id, name, cmdb_model_category_sys_id, 'hardware' AS model_kind
  FROM mars_dev.bronze.servicenow_cmdb_hardware_product_model
  UNION ALL
  SELECT sys_id, name, cmdb_model_category_sys_id, 'software'
  FROM mars_dev.bronze.servicenow_cmdb_software_component_model
)
SELECT
  COUNT(*) AS cis_linked_to_incidents,
  SUM(CASE WHEN model_id_sys_id IS NOT NULL AND TRIM(model_id_sys_id) <> '' THEN 1 ELSE 0 END)
    AS cis_with_model_id_sys_id,
  SUM(CASE WHEN m.sys_id IS NOT NULL THEN 1 ELSE 0 END) AS matched_model_via_sys_id,
  ROUND(
    100.0 * SUM(CASE WHEN m.sys_id IS NOT NULL THEN 1 ELSE 0 END)
    / NULLIF(SUM(CASE WHEN model_id_sys_id IS NOT NULL AND TRIM(model_id_sys_id) <> '' THEN 1 ELSE 0 END), 0),
    2
  ) AS model_sys_id_match_rate_pct
FROM matched_ci c
LEFT JOIN models m ON m.sys_id = c.model_id_sys_id;

-- -----------------------------------------------------------------------------
-- PART 5 — (c) cmdb_model.cmdb_model_category_sys_id → category.sys_id
-- -----------------------------------------------------------------------------
WITH models AS (
  SELECT sys_id, name, cmdb_model_category_sys_id
  FROM mars_dev.bronze.servicenow_cmdb_hardware_product_model
  UNION ALL
  SELECT sys_id, name, cmdb_model_category_sys_id
  FROM mars_dev.bronze.servicenow_cmdb_software_component_model
),
cats AS (
  SELECT sys_id, name FROM mars_dev.bronze.servicenow_cmdb_model_category
)
SELECT
  COUNT(*) AS models_total,
  SUM(CASE WHEN cmdb_model_category_sys_id IS NOT NULL AND TRIM(cmdb_model_category_sys_id) <> '' THEN 1 ELSE 0 END)
    AS models_with_category_sys_id,
  SUM(CASE WHEN cat.sys_id IS NOT NULL THEN 1 ELSE 0 END) AS matched_category_via_sys_id,
  ROUND(
    100.0 * SUM(CASE WHEN cat.sys_id IS NOT NULL THEN 1 ELSE 0 END)
    / NULLIF(SUM(CASE WHEN cmdb_model_category_sys_id IS NOT NULL AND TRIM(cmdb_model_category_sys_id) <> '' THEN 1 ELSE 0 END), 0),
    2
  ) AS category_sys_id_match_rate_pct
FROM models m
LEFT JOIN cats cat ON cat.sys_id = m.cmdb_model_category_sys_id;

-- -----------------------------------------------------------------------------
-- PART 6 — (d) incident.u_wm_asset → dim_device.DEVICE_ID (text join)
-- -----------------------------------------------------------------------------
WITH inc AS (
  SELECT number, u_wm_asset, cmdb_ci_sys_id, cmdb_ci_display_value
  FROM mars_dev.bronze.servicenow_incident_conformed
  WHERE u_wm_asset IS NOT NULL AND TRIM(u_wm_asset) <> ''
  QUALIFY ROW_NUMBER() OVER (PARTITION BY sys_id ORDER BY sys_updated_on DESC) = 1
)
SELECT
  COUNT(*) AS incidents_with_wm_asset,
  SUM(CASE WHEN dd.DEVICE_ID IS NOT NULL THEN 1 ELSE 0 END) AS matched_dim_device,
  ROUND(100.0 * SUM(CASE WHEN dd.DEVICE_ID IS NOT NULL THEN 1 ELSE 0 END) / COUNT(*), 2)
    AS wm_asset_match_rate_pct,
  COUNT(DISTINCT dd.DEVICE_ID) AS distinct_devices_matched,
  COUNT(DISTINCT dd.mars_device_category) AS categories_seen
FROM inc i
LEFT JOIN mars_dev.silver.dim_device dd
  ON dd.DEVICE_ID = UPPER(TRIM(i.u_wm_asset))
 AND dd.is_current = TRUE;

-- Breakdown by device category (TVM / GATE / VALIDATOR / other)
SELECT
  dd.mars_device_category,
  COUNT(*) AS incident_rows
FROM mars_dev.bronze.servicenow_incident_conformed i
JOIN mars_dev.silver.dim_device dd
  ON dd.DEVICE_ID = UPPER(TRIM(i.u_wm_asset))
 AND dd.is_current = TRUE
WHERE i.u_wm_asset IS NOT NULL
GROUP BY dd.mars_device_category
ORDER BY incident_rows DESC;

-- Does u_wm_asset agree with device id parsed from cmdb_ci_display_value prefix?
-- e.g. "TVM03804 VENDOR ASSY-CTA OSFS" -> TVM03804
WITH inc AS (
  SELECT
    number,
    u_wm_asset,
    cmdb_ci_display_value,
    UPPER(TRIM(regexp_extract(cmdb_ci_display_value, '^([A-Za-z]+[0-9]+)', 1))) AS device_from_display_prefix
  FROM mars_dev.bronze.servicenow_incident_conformed
  WHERE u_wm_asset IS NOT NULL AND cmdb_ci_display_value IS NOT NULL
)
SELECT
  COUNT(*) AS rows_both_populated,
  SUM(CASE WHEN UPPER(TRIM(u_wm_asset)) = device_from_display_prefix THEN 1 ELSE 0 END) AS wm_asset_eq_display_prefix,
  ROUND(
    100.0 * SUM(CASE WHEN UPPER(TRIM(u_wm_asset)) = device_from_display_prefix THEN 1 ELSE 0 END) / COUNT(*),
    2
  ) AS agree_pct
FROM inc;

-- -----------------------------------------------------------------------------
-- PART 7 — end-to-end chain on one incident (full sys_id path a→b→c + text path d)
-- -----------------------------------------------------------------------------
WITH inc AS (
  SELECT *
  FROM mars_dev.bronze.servicenow_incident_conformed
  WHERE UPPER(TRIM(number)) IN ('INC8051351', '8051351')
     OR UPPER(TRIM(u_wm_asset)) = 'TVM03804'
  LIMIT 5
),
ci_union AS (
  SELECT sys_id, name, model_id_sys_id, model_id_display_value, 'pos_device' AS ci_class
  FROM mars_dev.bronze.servicenow_cmdb_ci_pos_device
  UNION ALL SELECT sys_id, name, model_id_sys_id, model_id_display_value, 'card_handling'
  FROM mars_dev.bronze.servicenow_cmdb_ci_card_handling
  UNION ALL SELECT sys_id, name, model_id_sys_id, model_id_display_value, 'netgear'
  FROM mars_dev.bronze.servicenow_cmdb_ci_netgear
  UNION ALL SELECT sys_id, name, model_id_sys_id, model_id_display_value, 'onboard_card_interface'
  FROM mars_dev.bronze.servicenow_cmdb_ci_onboard_card_interface
  UNION ALL SELECT sys_id, name, model_id_sys_id, model_id_display_value, 'acc'
  FROM mars_dev.bronze.servicenow_cmdb_ci_acc
),
models AS (
  SELECT sys_id, name, cmdb_model_category_sys_id, 'hardware' AS kind
  FROM mars_dev.bronze.servicenow_cmdb_hardware_product_model
  UNION ALL
  SELECT sys_id, name, cmdb_model_category_sys_id, 'software'
  FROM mars_dev.bronze.servicenow_cmdb_software_component_model
)
SELECT
  i.number,
  i.cmdb_ci_sys_id,
  i.cmdb_ci_display_value,
  i.u_wm_asset,
  c.name AS ci_name,
  c.ci_class,
  m.name AS model_name,
  m.kind AS model_kind,
  cat.name AS model_category_name,
  dd.DEVICE_ID AS dim_device_id,
  dd.mars_device_category
FROM inc i
LEFT JOIN ci_union c ON c.sys_id = i.cmdb_ci_sys_id
LEFT JOIN models m ON m.sys_id = c.model_id_sys_id
LEFT JOIN mars_dev.bronze.servicenow_cmdb_model_category cat
  ON cat.sys_id = m.cmdb_model_category_sys_id
LEFT JOIN mars_dev.silver.dim_device dd
  ON dd.DEVICE_ID = UPPER(TRIM(i.u_wm_asset))
 AND dd.is_current = TRUE;

-- -----------------------------------------------------------------------------
-- PART 8 — SUMMARY interpretation guide
-- -----------------------------------------------------------------------------
-- (a) sys_id_match_rate_pct  >> 0  → Robin is correct; wire S15 CMDB via sys_id union
-- (a) sys_id_match_rate_pct  = 0  → CI sys_ids point outside the 5 XML tables;
--     need unified cmdb_ci export from Robin or match against cta_servicenow_cmdb_ci
--     if a sys_id column was added to CSV.
-- (a alt) display_name_match_rate_pct >> sys_id → use name join to CSV cmdb_ci instead
-- (b)(c) high rates on matched subset → model/category chain works once (a) works
-- (d) wm_asset_match_rate_pct ~57% for TVM/GATE scope is prior validated range;
--     agree_pct high → u_wm_asset and display prefix are consistent
