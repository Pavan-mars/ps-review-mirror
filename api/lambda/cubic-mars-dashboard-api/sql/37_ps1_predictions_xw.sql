-- =====================================================================
-- 37_ps1_predictions_xw.sql   28-Jul-2026
--
-- PUTS GATES AND TVMs BACK IN THE DASHBOARD.
--
-- MEASURED from the live API, not inferred: /ps1/predictions returns 200 rows
-- and every one is VALIDATOR. The route already ranks per device_category --
-- that fix went in on 26-Jul -- but ranking cannot conjure rows that are not
-- there. ps1_failure_predictions holds 1,922 rows and, at its latest
-- computed_date, no GATE or TVM rows at all.
--
-- ps1_cross_wired_daily has all three:
--     GATE       819 devices    107,110 rows
--     TVM        470 devices    184,483 rows
--     VALIDATOR 2,928 devices   494,932 rows
--
-- This view exposes it in EXACTLY the column shape /ps1/predictions already
-- returns, so the route can be repointed and every panel built on it -- Fleet
-- Overview, Device Predictions, Component Risk, Stations, Cross-tab -- renders
-- unchanged, with three device types instead of one.
--
-- DISTINCT ON gives one row per device: its most recent scored day. The tabs
-- are a current-state view, not a history, and returning 786,525 rows where the
-- UI expects a device list would be a different bug.
--
-- NO cat_rank COLUMN, deliberately. The route computes cat_rank in its own CTE
-- via ROW_NUMBER(); if this view also carried one, `SELECT p.*, ROW_NUMBER() ...
-- AS cat_rank` would produce two columns of that name and every later reference
-- to r.cat_rank would fail as ambiguous.
-- =====================================================================

CREATE OR REPLACE VIEW v_ps1_predictions_xw AS
SELECT DISTINCT ON (city_id, device_id)
  ('xw_' || device_id)            AS prediction_id,
  city_id,
  device_type                     AS device_category,
  device_id,
  facility_id,
  operator_id,
  ps1_fail_prob                   AS failure_probability,
  ps1_predicted                   AS predicted_label,
  threshold_used                  AS decision_threshold,
  transit_day                     AS prediction_date,
  NULL::timestamp                 AS inference_ts,
  ps1_risk_tier,
  component_type,
  will_hardware_oos_3d
FROM ps1_cross_wired_daily
ORDER BY city_id, device_id, transit_day DESC, ps1_fail_prob DESC NULLS LAST;

-- Coverage proof. Read this after deploying: three rows means the dashboard now
-- has three device types where it had one.
CREATE OR REPLACE VIEW v_ps1_predictions_xw_coverage AS
SELECT city_id, device_category, COUNT(*) AS n_devices,
       MIN(prediction_date) AS earliest, MAX(prediction_date) AS latest
FROM v_ps1_predictions_xw
GROUP BY city_id, device_category;
