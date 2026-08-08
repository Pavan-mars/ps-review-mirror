-- =====================================================================
-- 47_ps3_latest_run_widen.sql        04-Aug-2026
--
-- LET A SCORING RUN BE THE CURRENT RUN.
--
-- v_ps3_latest_run selects MAX(run_ts) WHERE run_kind = 'train'. Every PS3
-- device-grain view joins to it -- v_ps3_head_gates, v_ps3_device_risk,
-- v_ps3_serial_risk, v_ps3_device_360, and v_ps3_device_all through the
-- first of those.
--
-- ps3_rc_daily_score.py writes run_kind = 'batch_score', deliberately: a
-- scoring run has no labels for the day it scored and measures nothing, so
-- calling it 'train' would be a lie told to a filter. The consequence was
-- that daily scoring would land in Aurora correctly and be invisible on
-- every PS3 screen -- the pipeline would look healthy and change nothing.
--
-- This widens the filter rather than changing what the scorer declares.
--
-- WHAT THIS CHANGES ON SCREEN, so it is not a surprise:
--   * The newest run wins regardless of kind. Once daily scoring starts, the
--     device screens follow the scorer, not the last training run.
--   * ps3_head_summary rows published by a scoring run carry the TRAINING
--     metrics of the model that produced them -- the scorer copies them
--     forward and marks the run batch_score. So the gate flags
--     (severity_shippable / rootcause_shippable) keep working, and the
--     accuracy figures on screen remain the ones that were actually
--     measured, on the run that measured them.
--   * run_kind is exposed below so a screen can say which kind is current.
--     A dashboard that cannot tell a scored day from a trained one will
--     eventually present one as the other.
--
-- Idempotent. CREATE OR REPLACE VIEW, no data movement, no reload. Reverting
-- is the same statement with the IN list narrowed back to ('train').
-- =====================================================================

CREATE OR REPLACE VIEW v_ps3_latest_run AS
SELECT city_id, run_id, run_ts, as_of_date, n_incidents, device_scope,
       severity_collapse_verified,
       -- NEW. Downstream views may select it; existing ones ignore it, and a
       -- widened SELECT list is safe under CREATE OR REPLACE as long as the
       -- existing columns keep their names, types and order. They do.
       run_kind
FROM (
  SELECT r.*,
         ROW_NUMBER() OVER (PARTITION BY r.city_id ORDER BY r.run_ts DESC) AS rn
  FROM ps3_model_runs r
  WHERE r.run_kind IN ('train', 'batch_score')
) t
WHERE rn = 1;

-- ---------------------------------------------------------------------
-- The run registry, for a status panel. Every run this city has published,
-- newest first, with the row counts that landed against each. Lets a screen
-- answer "what is current, what did it supersede, and when" without joining
-- three tables by hand.
-- ---------------------------------------------------------------------
CREATE OR REPLACE VIEW v_ps3_run_registry AS
SELECT r.city_id,
       r.run_id,
       r.run_kind,
       r.run_ts,
       r.as_of_date,
       r.device_scope,
       r.source_notebook,
       (r.run_id = l.run_id)                                   AS is_current,
       (SELECT COUNT(*) FROM ps3_head_summary h
         WHERE h.city_id = r.city_id AND h.run_id = r.run_id)   AS n_head_rows,
       (SELECT COUNT(*) FROM ps3_device_predictions d
         WHERE d.city_id = r.city_id AND d.run_id = r.run_id)   AS n_device_rows,
       (SELECT COUNT(*) FROM ps3_serial_predictions s
         WHERE s.city_id = r.city_id AND s.run_id = r.run_id)   AS n_serial_rows
FROM ps3_model_runs r
LEFT JOIN v_ps3_latest_run l ON l.city_id = r.city_id
ORDER BY r.city_id, r.run_ts DESC;
