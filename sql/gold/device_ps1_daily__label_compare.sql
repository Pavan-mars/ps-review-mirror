-- =============================================================================
-- PS1 label comparison — "any outage" (current) vs "material failure" (design)
--
-- Purpose: decide the PS1 target with data, not assumption (validation report §3.1).
-- Run on Databricks AFTER mars_dev.gold.device_ps1_daily and
-- mars_dev.silver.kpi_avail_enriched exist. Read-only — produces a summary, no table change.
--
--   Label A (current build): will_fail_7d in device_ps1_daily
--                            = ANY device_outage with duration_min > 0 in the next 7 days.
--   Label B (material):      a material hardware failure in the next 7 days, taken from the
--                            S08 availability/FAILURE_LEVEL keystone, i.e.
--                            AE_FAILURE_LEVEL IN (1,2,3,4,5,16)  (cat-2 device faults; the same
--                            set PS3 uses). This is the Cubic-validated "real failure" definition.
--
-- Output columns:
--   device_days          spine size per category (NULL category row = overall, from ROLLUP)
--   pos_a / pos_rate_a   Label A positives and % (expect the broad ~20-60%)
--   pos_b / pos_rate_b   Label B positives and % (the material-failure rate — the number to judge)
--   both / a_only / b_only   agreement between the two labels
--
-- Decision guide: if pos_rate_b is workable (e.g. >= ~1-2% with enough absolute positives),
-- prefer Label B as the PS1 target (aligns to the keystone + PS3). If it is too sparse, keep
-- Label A but document it as an "availability/outage" predictor, not a "material failure" model.
-- NOTE: verify S08 column names (device_id, transit_day, AE_FAILURE_LEVEL) — taken from how
-- device_ps3_incident__create.sql consumes kpi_avail_enriched (authoritative as of 2026-06-23).
-- =============================================================================

USE CATALOG mars_dev;

WITH spine AS (
    SELECT DEVICE_ID, transit_day, mars_device_category,
           will_fail_7d AS label_a
    FROM mars_dev.gold.device_ps1_daily
),
material_incident_days AS (
    -- material hardware-failure incident days from the S08 keystone (same filter as PS3)
    SELECT DISTINCT device_id AS DEVICE_ID, transit_day AS incident_day
    FROM mars_dev.silver.kpi_avail_enriched
    WHERE AE_FAILURE_LEVEL IN (1, 2, 3, 4, 5, 16)
      AND transit_day >= '2024-01-01'
),
label_b AS (
    SELECT s.DEVICE_ID, s.transit_day,
           MAX(CASE WHEN mi.incident_day IS NOT NULL THEN 1 ELSE 0 END) AS label_b
    FROM spine s
    LEFT JOIN material_incident_days mi
      ON  mi.DEVICE_ID    = s.DEVICE_ID
      AND mi.incident_day >  s.transit_day
      AND mi.incident_day <= DATE_ADD(s.transit_day, 7)   -- next 7 days, matching Label A window
    GROUP BY s.DEVICE_ID, s.transit_day
)
SELECT
    COALESCE(s.mars_device_category, 'ALL')                                  AS category,
    COUNT(*)                                                                 AS device_days,
    SUM(s.label_a)                                                           AS pos_a_any_outage,
    ROUND(100.0 * SUM(s.label_a) / COUNT(*), 3)                              AS pos_rate_a_pct,
    SUM(b.label_b)                                                           AS pos_b_material,
    ROUND(100.0 * SUM(b.label_b) / COUNT(*), 3)                              AS pos_rate_b_pct,
    SUM(CASE WHEN s.label_a = 1 AND b.label_b = 1 THEN 1 ELSE 0 END)         AS both_pos,
    SUM(CASE WHEN s.label_a = 1 AND b.label_b = 0 THEN 1 ELSE 0 END)         AS a_only,
    SUM(CASE WHEN s.label_a = 0 AND b.label_b = 1 THEN 1 ELSE 0 END)         AS b_only
FROM spine s
JOIN label_b b
  ON b.DEVICE_ID = s.DEVICE_ID AND b.transit_day = s.transit_day
GROUP BY ROLLUP (s.mars_device_category)
ORDER BY category;
