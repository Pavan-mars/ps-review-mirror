-- =====================================================================
-- CUBIC MARS Chicago -- sql/52
-- Backfill the five provenance columns sql/load never wrote
-- Date: 2026-08-11   Idempotent. Reversible -- rollback in the footer.
--
-- WHAT THE 2026-08-11 DEPLOY EXPOSED
-- ----------------------------------
-- The moment /ps1/summary started serving ps1_model_performance, it returned
-- target=None for all three fleets, and /ps1/model-performance returned
-- serving_matches_scorecard=None. Neither is a bug in those routes. Both are
-- the same missing data, surfaced for the first time because until today
-- nothing read those columns.
--
-- sql/16 added five columns to ps1_model_performance in July:
--     target_col   label_revision   recall_floor   base_rate_pct   run_id
-- The INSERT in sql/load/ps1_sklearn_20260726.sql names 22 columns and none of
-- those five is among them. So they have been NULL since the row was written.
--
-- A scorecard that cannot state which label it was scored against is not a
-- scorecard, and a run_id of NULL makes it impossible to say whether the
-- endpoint is serving this model or a different one -- which is exactly the
-- question E-1 exists to answer.
--
-- WHERE THE VALUES COME FROM -- MEASURED, NOT INVENTED
-- ----------------------------------------------------
-- run_id, target_col and label_revision are copied from ps1_inference_runs,
-- which DOES carry them for the same run: 'ps1_sklearn_20260726',
-- 'will_hardware_oos_3d'. The join is on (city_id, device_category,
-- endpoint_name) restricted to run_kind='train'. This is a copy between two
-- tables written by the same load, not a reconstruction.
--
-- recall_floor is a POLICY constant, not a measurement. sql/16's own header
-- states it: "PS1 is gated on AP/PR-AUC + a recall floor (TVM 0.80 / GATE
-- 0.70)". Those two are set. VALIDATOR IS DELIBERATELY LEFT NULL -- no
-- document in this programme states a VALIDATOR floor, and inventing one to
-- make a column look complete would be worse than an honest NULL. It means
-- VALIDATOR is currently gated on nothing, which is a decision that needs
-- taking, not a gap to paper over.
--
-- base_rate_pct is NOT set here. /ps1/summary already derives it from
-- ps1_confusion's own TP/FP/TN/FN when the column is NULL, and a derived
-- figure from the run's real confusion matrix beats a second copy that can
-- drift away from it.
--
-- SAFETY
-- ------
-- Every UPDATE is guarded by "IS NULL", so this only ever fills blanks and can
-- never overwrite a value a later, better-behaved loader has written. Running
-- it twice changes nothing the second time.
-- =====================================================================

-- ---------------------------------------------------------------------
-- 1. run_id / target_col / label_revision, copied from the train run
-- ---------------------------------------------------------------------
UPDATE ps1_model_performance mp
SET    run_id         = COALESCE(mp.run_id,         ir.run_id),
       target_col     = COALESCE(mp.target_col,     ir.target_col),
       label_revision = COALESCE(mp.label_revision, ir.label_revision)
FROM (
  SELECT DISTINCT ON (city_id, device_category, endpoint_name)
         city_id, device_category, endpoint_name, run_id, target_col, label_revision
  FROM   ps1_inference_runs
  WHERE  run_kind = 'train'
  ORDER  BY city_id, device_category, endpoint_name, run_ts DESC
) ir
WHERE  mp.city_id         = ir.city_id
  AND  mp.device_category = ir.device_category
  AND  mp.endpoint_name   = ir.endpoint_name
  AND  (mp.run_id IS NULL OR mp.target_col IS NULL OR mp.label_revision IS NULL);

-- ---------------------------------------------------------------------
-- 2. recall_floor -- the documented policy, TVM 0.80 / GATE 0.70 (sql/16)
--    VALIDATOR intentionally absent. See the header.
-- ---------------------------------------------------------------------
UPDATE ps1_model_performance
SET    recall_floor = 0.80
WHERE  device_category = 'TVM'  AND recall_floor IS NULL;

UPDATE ps1_model_performance
SET    recall_floor = 0.70
WHERE  device_category = 'GATE' AND recall_floor IS NULL;

-- ---------------------------------------------------------------------
-- 3. A view that makes the remaining holes impossible to miss.
--    Anything still NULL after this file has run is a real gap with no
--    source, not an oversight -- and it should be visible without anyone
--    having to remember to go looking.
-- ---------------------------------------------------------------------
CREATE OR REPLACE VIEW v_ps1_provenance_gaps AS
SELECT city_id,
       device_category,
       computed_date,
       (run_id         IS NULL) AS missing_run_id,
       (target_col     IS NULL) AS missing_target_col,
       (label_revision IS NULL) AS missing_label_revision,
       (recall_floor   IS NULL) AS missing_recall_floor,
       CASE
         WHEN device_category = 'VALIDATOR' AND recall_floor IS NULL
           THEN 'recall_floor NULL BY DECISION -- no VALIDATOR floor is documented'
         WHEN run_id IS NULL OR target_col IS NULL
           THEN 'provenance missing -- the scorecard cannot state its label or its run'
         ELSE 'complete'
       END AS assessment
FROM   ps1_model_performance;

COMMENT ON VIEW v_ps1_provenance_gaps IS
  'Which ps1_model_performance rows still lack provenance after sql/52. '
  'VALIDATOR.recall_floor NULL is expected and is a pending decision, not a defect. '
  'Anything else NULL means a loader wrote a row without its lineage.';

-- ---------------------------------------------------------------------
-- ROLLBACK. Only ever fills NULLs, so the undo is to blank the same columns:
--
--   UPDATE ps1_model_performance
--   SET run_id=NULL, target_col=NULL, label_revision=NULL, recall_floor=NULL
--   WHERE city_id='CHI' AND computed_date=DATE '2026-07-26';
--   DROP VIEW IF EXISTS v_ps1_provenance_gaps;
--
-- THE REAL FIX IS UPSTREAM. This file repairs rows that already exist. The
-- INSERT in sql/load/ps1_sklearn_20260726.sql must be widened to name these
-- columns, or the next load recreates the same holes and someone runs sql/52
-- again wondering why it was needed twice.
-- ---------------------------------------------------------------------
