-- =====================================================================
-- CUBIC MARS Chicago - Phase-2c  PS3 two-head GATE-AWARE views
-- Date: 2026-07-26.  Runs AFTER 15.  Idempotent.  ADDITIVE ONLY (views only).
--
-- WHY THIS EXISTS
--   Migration 15 stores every head's scorecard including gate_pass, but nothing
--   consumes it. On the 19-Jul run GATE/severity has gate_pass = FALSE
--   (macro-F1 0.4978 vs a 0.55 floor; AUC-OVR 0.4358, i.e. worse than random,
--   champion = BaselineMostFrequent on a 1,410-vs-9 class split). Once the
--   SEVERITY_COLLAPSE recompute is applied, every one of the 450 GATE device
--   rows reads pct_critical_pred = 1.0000. Rendering that unfiltered publishes
--   a leaderboard asserting that every Chicago gate is 100% critical - the
--   mirror image of the 0.0000 bug it replaced.
--
--   These views mask severity fields for any (device_category, head='severity')
--   whose gate_pass is FALSE, and expose severity_shippable so the dashboard can
--   label the cell "not modelled" instead of drawing a number. Root-cause fields
--   are masked independently on their own head's gate_pass.
--   Masking lives in SQL, not in the API, so every consumer inherits it.
-- =====================================================================

-- Latest train run per city (single source of "current").
CREATE OR REPLACE VIEW v_ps3_latest_run AS
SELECT city_id, run_id, run_ts, as_of_date, n_incidents, device_scope,
       severity_collapse_verified
FROM (
  SELECT r.*, ROW_NUMBER() OVER (PARTITION BY r.city_id ORDER BY r.run_ts DESC) AS rn
  FROM ps3_model_runs r
  WHERE r.run_kind = 'train'
) t
WHERE rn = 1;

-- Per (city, category) gate flags for both heads, from the latest run.
CREATE OR REPLACE VIEW v_ps3_head_gates AS
SELECT l.city_id, l.run_id, h.device_category,
       BOOL_OR(h.head = 'severity'   AND COALESCE(h.gate_pass, FALSE)) AS severity_shippable,
       BOOL_OR(h.head = 'root_cause' AND COALESCE(h.gate_pass, FALSE)) AS rootcause_shippable,
       MAX(CASE WHEN h.head = 'severity'   THEN h.test_f1_macro  END) AS severity_f1_macro,
       MAX(CASE WHEN h.head = 'root_cause' THEN h.test_f1_macro  END) AS rootcause_f1_macro,
       MAX(CASE WHEN h.head = 'severity'   THEN h.macro_f1_floor END) AS severity_floor,
       MAX(CASE WHEN h.head = 'root_cause' THEN h.macro_f1_floor END) AS rootcause_floor,
       BOOL_AND(COALESCE(h.modeled, FALSE))                            AS both_heads_modeled
FROM v_ps3_latest_run l
JOIN ps3_head_summary h ON h.city_id = l.city_id AND h.run_id = l.run_id
GROUP BY l.city_id, l.run_id, h.device_category;

-- Device-grain risk leaderboard, severity masked when its head failed the gate.
CREATE OR REPLACE VIEW v_ps3_device_risk AS
SELECT d.city_id, d.run_id, d.device_id, d.mars_device_category,
       d.n_incidents,
       CASE WHEN g.severity_shippable  THEN d.pct_critical_pred      END AS pct_critical_pred,
       CASE WHEN g.severity_shippable  THEN d.dominant_pred_severity END AS dominant_pred_severity,
       CASE WHEN g.rootcause_shippable THEN d.dominant_pred_component END AS dominant_pred_component,
       d.avg_component_age_days, d.last_incident_dtm, d.computed_date,
       COALESCE(g.severity_shippable,  FALSE) AS severity_shippable,
       COALESCE(g.rootcause_shippable, FALSE) AS rootcause_shippable,
       g.severity_f1_macro, g.severity_floor,
       g.rootcause_f1_macro, g.rootcause_floor
FROM ps3_device_predictions d
JOIN v_ps3_latest_run l ON l.city_id = d.city_id AND l.run_id = d.run_id
LEFT JOIN v_ps3_head_gates g
       ON g.city_id = d.city_id AND g.run_id = d.run_id
      AND g.device_category = d.mars_device_category;

-- Serial-grain equivalent.
CREATE OR REPLACE VIEW v_ps3_serial_risk AS
SELECT s.city_id, s.run_id, s.device_id, s.matched_serial_nbr, s.mars_device_category,
       s.n_incidents, s.component_age_days,
       CASE WHEN g.severity_shippable  THEN s.pct_critical_pred       END AS pct_critical_pred,
       CASE WHEN g.rootcause_shippable THEN s.dominant_pred_component END AS dominant_pred_component,
       s.last_incident_dtm, s.computed_date,
       COALESCE(g.severity_shippable,  FALSE) AS severity_shippable,
       COALESCE(g.rootcause_shippable, FALSE) AS rootcause_shippable
FROM ps3_serial_predictions s
JOIN v_ps3_latest_run l ON l.city_id = s.city_id AND l.run_id = s.run_id
LEFT JOIN v_ps3_head_gates g
       ON g.city_id = s.city_id AND g.run_id = s.run_id
      AND g.device_category = s.mars_device_category;

-- Device-360 header row for the drill-down modal (serials + incidents are
-- fetched separately by the /ps3/device-360 route).
CREATE OR REPLACE VIEW v_ps3_device_360 AS
SELECT r.city_id, r.run_id, r.device_id, r.mars_device_category,
       r.n_incidents, r.pct_critical_pred, r.dominant_pred_severity,
       r.dominant_pred_component, r.avg_component_age_days, r.last_incident_dtm,
       r.computed_date, r.severity_shippable, r.rootcause_shippable,
       r.severity_f1_macro, r.severity_floor, r.rootcause_f1_macro, r.rootcause_floor,
       (SELECT COUNT(*) FROM ps3_serial_predictions sp
         WHERE sp.city_id = r.city_id AND sp.run_id = r.run_id
           AND sp.device_id = r.device_id)                       AS n_serials,
       l.run_ts, l.severity_collapse_verified
FROM v_ps3_device_risk r
JOIN v_ps3_latest_run l ON l.city_id = r.city_id AND l.run_id = r.run_id;
