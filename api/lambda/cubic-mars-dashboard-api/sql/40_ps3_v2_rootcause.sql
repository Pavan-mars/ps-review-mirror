-- =====================================================================
-- 40_ps3_v2_rootcause.sql                                    29-Jul-2026
--
-- THE ROOT-CAUSE HALF OF PS3, WHICH sql/39 LEFT ON THE FLOOR.
--
-- sql/39 loaded the SEVERITY head (how bad is it) and component_reliability at
-- fleet grain. It did NOT load the three tables that answer the other question
-- PS3 exists for: WHICH COMPONENT IS BEHIND THIS DEVICE'S FAILURES.
--
-- Source parquet, same run ps3_20260729T074311Z:
--   ps3_device_serial_component_reliability   1,413 rows  device x serial x component
--   ps3_device_serial_reliability               927 rows  device x serial
--   ps3_component_taxonomy_audit                 29 rows  is the taxonomy trustworthy
--
-- BE HONEST ABOUT WHAT THIS IS. The run does NOT publish a per-incident
-- predicted component -- there is no rootcause equivalent of
-- ps3_severity_action_queue. What it publishes is OBSERVED ATTRIBUTION: for a
-- given device and serial, which component its incidents actually came from,
-- how often, and how often those were critical.
--
-- That is arguably the more useful artifact for operations, and it must be
-- labelled as what it is: measured history, not a model prediction. The views
-- below carry that wording so it reaches the screen rather than living here.
--
-- The taxonomy audit is loaded WITH it, deliberately. A component attribution
-- is only as good as the label taxonomy behind it; if a label is flagged
-- unmapped or collapsed, the attribution built on it inherits that doubt.
-- Shipping the audit beside the answer is what lets someone check it.
--
-- Nothing here touches sql/39's tables or any pre-existing ps3_* object.
-- =====================================================================

CREATE TABLE IF NOT EXISTS ps3_v2_device_serial_component (
  id BIGSERIAL PRIMARY KEY,
  city_id TEXT NOT NULL, pipeline_version TEXT NOT NULL, run_id TEXT,
  device_id TEXT, serial_number TEXT, component_label TEXT,
  incident_count BIGINT, critical_rate DOUBLE PRECISION,
  component_label_semantics TEXT, latest_incident_at TEXT,
  recurrence_30d BIGINT, device_category TEXT,
  loaded_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS ix_dsc_city ON ps3_v2_device_serial_component (city_id, pipeline_version);
CREATE INDEX IF NOT EXISTS ix_dsc_dev  ON ps3_v2_device_serial_component (city_id, device_id);
CREATE INDEX IF NOT EXISTS ix_dsc_comp ON ps3_v2_device_serial_component (city_id, component_label);

CREATE TABLE IF NOT EXISTS ps3_v2_device_serial (
  id BIGSERIAL PRIMARY KEY,
  city_id TEXT NOT NULL, pipeline_version TEXT NOT NULL, run_id TEXT,
  device_id TEXT, serial_number TEXT, incident_count BIGINT,
  critical_incidents BIGINT, critical_rate DOUBLE PRECISION,
  latest_incident_at TEXT, max_prior_incidents_30d BIGINT, device_category TEXT,
  loaded_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS ix_ds_city ON ps3_v2_device_serial (city_id, pipeline_version);
CREATE INDEX IF NOT EXISTS ix_ds_dev  ON ps3_v2_device_serial (city_id, device_id);

CREATE TABLE IF NOT EXISTS ps3_v2_component_taxonomy (
  id BIGSERIAL PRIMARY KEY,
  city_id TEXT NOT NULL, pipeline_version TEXT NOT NULL, run_id TEXT,
  device_category TEXT, component_label TEXT, split TEXT,
  taxonomy_status TEXT, action TEXT, rows BIGINT,
  loaded_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS ix_tax_city ON ps3_v2_component_taxonomy (city_id, pipeline_version);

-- Root-cause attribution per device x serial x component, with the taxonomy
-- verdict joined on so a doubtful label cannot be read as a confident answer.
CREATE OR REPLACE VIEW v_ps3_v2_rootcause AS
SELECT
  d.city_id, d.device_category, d.device_id, d.serial_number, d.component_label,
  d.component_label_semantics, d.incident_count, d.critical_rate,
  d.recurrence_30d, d.latest_incident_at,
  ROUND((d.incident_count * COALESCE(d.critical_rate,0))::numeric, 2) AS critical_weighted,
  t.taxonomy_status, t.action AS taxonomy_action,
  CASE WHEN t.taxonomy_status IS NULL THEN 'taxonomy not audited for this label'
       WHEN LOWER(t.taxonomy_status) LIKE '%ok%'
         OR LOWER(t.taxonomy_status) LIKE '%mapped%' THEN 'label is mapped and trustworthy'
       ELSE 'label flagged by the taxonomy audit: ' || t.taxonomy_status END AS taxonomy_note,
  'observed attribution from incident history - not a model prediction' AS basis,
  d.pipeline_version, d.run_id
FROM ps3_v2_device_serial_component d
JOIN v_ps3_v2_current c
  ON c.city_id = d.city_id AND c.pipeline_version = d.pipeline_version
LEFT JOIN ps3_v2_component_taxonomy t
  ON t.city_id = d.city_id AND t.pipeline_version = d.pipeline_version
 AND t.component_label = d.component_label
 AND t.device_category = d.device_category;

-- Fleet answer to "which component should we fix first", ranked by critical
-- incidents rather than raw count: one component causing 10 critical failures
-- outranks another causing 40 minor ones.
CREATE OR REPLACE VIEW v_ps3_v2_rootcause_rollup AS
SELECT
  city_id, device_category, component_label, component_label_semantics,
  COUNT(DISTINCT device_id)                                  AS devices_affected,
  COUNT(DISTINCT serial_number)                              AS serials_affected,
  SUM(incident_count)                                        AS incidents,
  ROUND(AVG(critical_rate)::numeric, 4)                      AS mean_critical_rate,
  ROUND(SUM(incident_count * COALESCE(critical_rate,0))::numeric, 1) AS critical_weighted,
  SUM(recurrence_30d)                                        AS recurrence_30d,
  MAX(taxonomy_note)                                         AS taxonomy_note,
  pipeline_version
FROM v_ps3_v2_rootcause
GROUP BY city_id, device_category, component_label, component_label_semantics, pipeline_version;

-- Serials whose incidents concentrate in ONE component. That concentration is
-- the strongest root-cause signal available here: a serial with 8 incidents all
-- on the same part is a component fault, not bad luck.
CREATE OR REPLACE VIEW v_ps3_v2_rootcause_concentration AS
WITH s AS (
  SELECT city_id, device_category, device_id, serial_number,
         SUM(incident_count) AS total_incidents,
         MAX(incident_count) AS top_component_incidents,
         COUNT(*)            AS distinct_components,
         pipeline_version
  FROM v_ps3_v2_rootcause
  GROUP BY city_id, device_category, device_id, serial_number, pipeline_version)
SELECT s.*,
  ROUND((s.top_component_incidents::numeric / NULLIF(s.total_incidents,0)), 3) AS concentration,
  CASE WHEN s.distinct_components = 1 AND s.total_incidents >= 3
         THEN 'SINGLE COMPONENT - every incident on this serial came from one part'
       WHEN s.top_component_incidents::numeric / NULLIF(s.total_incidents,0) >= 0.7
         THEN 'DOMINANT COMPONENT - most incidents trace to one part'
       ELSE 'MIXED - incidents spread across parts, no single culprit' END AS concentration_verdict
FROM s;
