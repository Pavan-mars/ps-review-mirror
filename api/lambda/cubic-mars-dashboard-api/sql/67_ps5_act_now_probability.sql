-- 67_ps5_act_now_probability.sql                                    26-Sep-2026
--
-- act_now was `is_overdue AND rul_standard_days <= 30`. The models predict the
-- gap to the NEXT hardware-OOS event, whose fitted medians are ~1 day, so
-- `<= 30` was always true and act_now fired on ~every overdue device.
--
-- act_now is now `p_oos_7d >= p_threshold`: the probability, from the fitted
-- Weibull/Cox, of another OOS event within 7 days given the current fault-free
-- run. The threshold is per fleet in ps5_act_now_policy so UAT can tune it
-- with an UPDATE -- no redeploy, no notebook re-run. p_oos_7d is NULL on rows
-- exported before this change; those rows are act_now = FALSE, never guessed.
--
-- Components have no model of their own (they inherit the device Weibull), so
-- a component's act_now is its HOST DEVICE's act_now.
--
-- Idempotent. Safe to re-run.

ALTER TABLE ps5_device_rul ADD COLUMN IF NOT EXISTS p_oos_7d NUMERIC(8,6);

CREATE TABLE IF NOT EXISTS ps5_act_now_policy (
  city_id      city_code    NOT NULL REFERENCES cities(id),
  device_type  VARCHAR(12)  NOT NULL,
  p_threshold  NUMERIC(6,4) NOT NULL CHECK (p_threshold > 0 AND p_threshold <= 1),
  horizon_days INT          NOT NULL DEFAULT 7,
  note         TEXT,
  updated_at   TIMESTAMPTZ  NOT NULL DEFAULT now(),
  PRIMARY KEY (city_id, device_type)
);

-- Starting point only. Set from the p_oos_7d distribution after the refit.
INSERT INTO ps5_act_now_policy (city_id, device_type, p_threshold, note)
SELECT 'CHI', t, 0.90, 'initial default 26-Sep-2026; tune in UAT'
FROM unnest(ARRAY['GATE', 'TVM', 'VALIDATOR']) AS t
ON CONFLICT (city_id, device_type) DO NOTHING;

-- r.* gains p_oos_7d in the middle of the column list, which CREATE OR REPLACE
-- cannot do. The serial view reads this one, so both are dropped and rebuilt.
DROP VIEW IF EXISTS v_ps5_serial_rul;
DROP VIEW IF EXISTS v_ps5_device_rul;

CREATE VIEW v_ps5_device_rul AS
SELECT
  r.*,
  RANK() OVER (PARTITION BY r.city_id, r.device_type
               ORDER BY r.rul_standard_days ASC NULLS LAST)  AS rul_rank_in_type,
  COUNT(*) OVER (PARTITION BY r.city_id, r.device_type)      AS n_devices_in_type,
  p.p_threshold                                              AS act_now_threshold,
  COALESCE(r.p_oos_7d >= p.p_threshold, FALSE)               AS act_now
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
