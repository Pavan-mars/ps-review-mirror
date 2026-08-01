-- =====================================================================
-- 32_ps5_serial_dedup.sql   27-Jul-2026
--
-- WHAT THE GRAIN AUDIT FOUND
-- --------------------------
-- v_ps5_serial_dupes, measured against the loaded rows:
--
--   VALIDATOR  3,823 duplicate keys   16,022 rows   worst case 25 copies
--   TVM            8 duplicate keys       16 rows   worst case  2 copies
--   GATE           0
--
-- and critically, EVERY max_* column came back 1:
--   max_component_types 1, max_asof_dates 1, max_serial_sources 1,
--   max_risk_tiers 1, max_rul_values 1
--
-- The duplicated rows are identical on every measured column. So this is not a
-- missing grain column -- there is no date, component type or source that tells
-- the copies apart. It is exact row duplication, which means a join fanned out
-- in the serial roster upstream of the survival model.
--
-- WHY DEDUPLICATE HERE AND NOT JUST WAIT FOR THE RE-RUN
-- -----------------------------------------------------
-- Left as-is the dashboard reports 20,943 validator components when roughly
-- 8,744 exist, and any COUNT or SUM over components is inflated by up to 25x for
-- the worst device. That is a wrong number on a screen, which is worse than a
-- missing one.
--
-- The rows are NOT deleted. The raw table keeps every row so the fan-out stays
-- visible and countable in v_ps5_serial_dupes; only the SERVING view collapses
-- them. When the roster is fixed upstream this view keeps working unchanged --
-- DISTINCT over already-unique rows is a no-op.
--
-- DISTINCT is safe here precisely BECAUSE the audit proved the copies are
-- identical. If any measured column had varied, collapsing them would have
-- silently picked one value over another, and the right answer would have been
-- to add that column to the grain instead.
-- =====================================================================

CREATE OR REPLACE VIEW v_ps5_serial_rul AS
WITH deduped AS (
  SELECT DISTINCT
    city_id, device_type, device_id, component_serial_nbr, component_type_name,
    mars_device_category, component_age_days, device_oos_failures_total,
    risk_score, risk_tier, expected_component_rul_days,
    predicted_median_survival_days, is_overdue, event_definition,
    event_def_version, feature_asof_date, serial_source
  FROM ps5_serial_rul
)
SELECT
  d.*,
  (d.component_serial_nbr IS NOT NULL)                       AS has_serial,
  RANK() OVER (PARTITION BY d.city_id, d.device_type
               ORDER BY d.expected_component_rul_days ASC NULLS LAST)
                                                             AS rul_rank_in_type,
  COUNT(*) OVER (PARTITION BY d.city_id, d.device_type)      AS n_components_in_type,
  (d.is_overdue AND d.expected_component_rul_days IS NOT NULL
     AND d.expected_component_rul_days <= 30)                AS act_now
FROM deduped d;

-- Standing visibility of the defect. If a reader asks "why does the component
-- count differ from the export row count", this is the answer, per device type,
-- straight from the data rather than from a comment.
CREATE OR REPLACE VIEW v_ps5_serial_fanout AS
SELECT
  r.city_id,
  r.device_type,
  COUNT(*)                                                   AS rows_loaded,
  COUNT(DISTINCT (r.device_id, r.component_serial_nbr))      AS distinct_components,
  COUNT(*) - COUNT(DISTINCT (r.device_id, r.component_serial_nbr))
                                                             AS surplus_rows,
  ROUND(COUNT(*)::numeric
        / NULLIF(COUNT(DISTINCT (r.device_id, r.component_serial_nbr)), 0), 2)
                                                             AS fanout_factor
FROM ps5_serial_rul r
GROUP BY r.city_id, r.device_type;
