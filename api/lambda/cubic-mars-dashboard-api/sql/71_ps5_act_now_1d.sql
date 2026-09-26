-- 71_ps5_act_now_1d.sql                                              26-Sep-2026
--
-- The 7-day probability from sql/67 saturates. Measured on the 26-Sep refit:
-- p50/p90 of p_oos_7d = GATE 0.956/0.992, TVM 0.9995/1.0, VALIDATOR 0.988/1.0.
-- These devices fault every one to two days, so "an OOS within 7 days" is
-- near-certain for almost all of them and no threshold separates TVMs or
-- validators.
--
-- act_now now uses the probability at the horizon the policy row names
-- (horizon_days: 1 or 7), and the policy is moved to 1 day. p_oos_7d stays
-- in the table for reference. The threshold is a placeholder until it is set
-- from the p_oos_1d spread of the next run:
--   UPDATE ps5_act_now_policy SET p_threshold = <x>, note = '<why>'
--    WHERE city_id = 'CHI' AND device_type = '<T>';
-- Rows loaded before the notebook exported p_oos_1d have it NULL, so act_now
-- is FALSE for them -- never guessed.
--
-- Idempotent. Safe to re-run.

ALTER TABLE ps5_device_rul ADD COLUMN IF NOT EXISTS p_oos_1d NUMERIC(8,6);

UPDATE ps5_act_now_policy
   SET horizon_days = 1, p_threshold = 0.50,
       note = 'sql/71 26-Sep-2026: 1-day horizon; 0.50 placeholder until set from the p_oos_1d spread',
       updated_at = now()
 WHERE city_id = 'CHI' AND horizon_days = 7;

DROP VIEW IF EXISTS v_ps5_serial_rul;
DROP VIEW IF EXISTS v_ps5_device_rul;

CREATE VIEW v_ps5_device_rul AS
SELECT
  r.*,
  RANK() OVER (PARTITION BY r.city_id, r.device_type
               ORDER BY r.rul_standard_days ASC NULLS LAST)  AS rul_rank_in_type,
  COUNT(*) OVER (PARTITION BY r.city_id, r.device_type)      AS n_devices_in_type,
  p.p_threshold                                              AS act_now_threshold,
  p.horizon_days                                             AS act_now_horizon_days,
  CASE p.horizon_days WHEN 1 THEN r.p_oos_1d ELSE r.p_oos_7d END
                                                             AS act_now_p,
  COALESCE(CASE p.horizon_days WHEN 1 THEN r.p_oos_1d ELSE r.p_oos_7d END
           >= p.p_threshold, FALSE)                          AS act_now
FROM ps5_device_rul r
LEFT JOIN ps5_act_now_policy p
  ON p.city_id = r.city_id AND p.device_type = r.device_type;

CREATE VIEW v_ps5_serial_rul AS
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
  COALESCE(v.act_now, FALSE)                                 AS act_now
FROM deduped d
LEFT JOIN v_ps5_device_rul v
  ON v.city_id = d.city_id AND v.device_id = d.device_id;
