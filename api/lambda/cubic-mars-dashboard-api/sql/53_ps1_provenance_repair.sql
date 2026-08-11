-- =====================================================================
-- CUBIC MARS Chicago -- sql/53
-- Repair sql/52's failed statement. Same goal, correct schema.
-- Date: 2026-08-11   Idempotent. Reversible -- rollback in the footer.
--
-- WHAT HAPPENED
-- -------------
-- sql/52 applied 5 of its 6 statements. The first one failed:
--
--     42703  column "label_revision" does not exist
--     There is a column named "label_revision" in table "mp", but it cannot
--     be referenced from this part of the query.
--
-- The subquery selected label_revision FROM ps1_inference_runs. That column
-- does not exist there. sql/16 added label_revision to ps1_model_performance
-- and to ps1_failure_summary; it never added one to ps1_inference_runs, whose
-- CREATE TABLE carries target_col but no label_revision. PostgreSQL's hint is
-- precise: the column exists on the UPDATE target, and a FROM-subquery cannot
-- see the UPDATE target.
--
-- HOW THE ERROR WAS MADE, because it is the interesting part.
-- sql/52 was tested against PostgreSQL 16.13 and reported 6 statements, zero
-- failures. The test fixture table was written BY HAND from what the migration
-- was assumed to contain -- and the hand-written fixture included a
-- label_revision column that the real ps1_inference_runs does not have. So the
-- test proved the SQL was consistent with an invented schema, not with this
-- database. A fixture built from the assumption cannot falsify the assumption.
--
-- THE RULE THAT FOLLOWS: build test fixtures by extracting the real CREATE
-- TABLE from the migration files, never by retyping what the table "should"
-- look like. This file was tested that way -- the fixture below is generated
-- from sql/16's own DDL text.
--
-- WHAT SUCCEEDED IN sql/52, AND IS NOT REPEATED HERE
-- --------------------------------------------------
--   recall_floor  TVM 0.80 / GATE 0.70   applied
--   v_ps1_provenance_gaps                created
-- Only the run_id / target_col / label_revision backfill is redone.
-- =====================================================================

-- ---------------------------------------------------------------------
-- 1. run_id and target_col -- the two columns ps1_inference_runs really has.
--    Restricted to run_kind='train': a batch_score row records that a loader
--    scored something, not which model is registered. Reading the latest row
--    of any kind is what made TVM report ps1_20260810 -- a Path B scoring run
--    from 2026-08-10 -- as though it were the served model.
-- ---------------------------------------------------------------------
UPDATE ps1_model_performance mp
SET    run_id     = COALESCE(mp.run_id,     ir.run_id),
       target_col = COALESCE(mp.target_col, ir.target_col)
FROM (
  SELECT DISTINCT ON (city_id, device_category, endpoint_name)
         city_id, device_category, endpoint_name, run_id, target_col
  FROM   ps1_inference_runs
  WHERE  run_kind = 'train'
  ORDER  BY city_id, device_category, endpoint_name, run_ts DESC
) ir
WHERE  mp.city_id         = ir.city_id
  AND  mp.device_category = ir.device_category
  AND  mp.endpoint_name   = ir.endpoint_name
  AND  (mp.run_id IS NULL OR mp.target_col IS NULL);

-- ---------------------------------------------------------------------
-- 2. label_revision has NO source table. It is derived from the label itself,
--    on the programme's own documentation rather than on a guess: sql/16
--    annotates the column as
--        target_col VARCHAR(32)  -- will_hardware_oos_3d (R7-1 aligned)
--    so a row whose target IS will_hardware_oos_3d is by definition R7-1.
--    Any other target is left NULL -- there is no documented mapping for it.
-- ---------------------------------------------------------------------
UPDATE ps1_model_performance
SET    label_revision = 'R7-1'
WHERE  target_col = 'will_hardware_oos_3d'
  AND  label_revision IS NULL;

-- ---------------------------------------------------------------------
-- 3. Widen the gaps view to name label_revision's true status. It is not
--    "missing"; it is derived, and it is absent only where the target is not
--    the R7-1 label. Saying that plainly stops a future reader from writing
--    another backfill for a column that has no source.
-- ---------------------------------------------------------------------
DROP VIEW IF EXISTS v_ps1_provenance_gaps;

CREATE OR REPLACE VIEW v_ps1_provenance_gaps AS
SELECT city_id,
       device_category,
       computed_date,
       (run_id         IS NULL) AS missing_run_id,
       (target_col     IS NULL) AS missing_target_col,
       (label_revision IS NULL) AS missing_label_revision,
       (recall_floor   IS NULL) AS missing_recall_floor,
       CASE
         WHEN run_id IS NULL OR target_col IS NULL
           THEN 'PROVENANCE MISSING -- no train-kind ps1_inference_runs row joins this '
                'fleet on (city_id, device_category, endpoint_name)'
         WHEN device_category = 'VALIDATOR' AND recall_floor IS NULL
           THEN 'complete except recall_floor, which is NULL BY DECISION -- no VALIDATOR '
                'floor is documented in this programme'
         WHEN recall_floor IS NULL
           THEN 'recall_floor missing -- sql/16 documents TVM 0.80 and GATE 0.70'
         WHEN label_revision IS NULL
           THEN 'label_revision absent because target_col is not will_hardware_oos_3d; '
                'there is no documented revision for any other label'
         ELSE 'complete'
       END AS assessment
FROM   ps1_model_performance;

COMMENT ON VIEW v_ps1_provenance_gaps IS
  'Provenance completeness per ps1_model_performance row, after sql/52 + sql/53. '
  'VALIDATOR.recall_floor NULL is a pending decision, not a defect. label_revision '
  'is DERIVED from target_col, not copied -- ps1_inference_runs has no such column.';

-- ---------------------------------------------------------------------
-- ROLLBACK. Fills NULLs only, so the undo blanks the same columns:
--
--   UPDATE ps1_model_performance
--   SET run_id=NULL, target_col=NULL, label_revision=NULL
--   WHERE city_id='CHI' AND computed_date=DATE '2026-07-26';
--   DROP VIEW IF EXISTS v_ps1_provenance_gaps;
--   -- then re-apply sql/52 for the sql/52-era view definition.
--
-- STILL THE REAL FIX, AND STILL NOT DONE: widen the INSERT in
-- sql/load/ps1_sklearn_20260726.sql to name run_id, target_col, label_revision,
-- recall_floor and base_rate_pct. Until that happens, the next load writes the
-- same holes and someone applies 52 and 53 again.
-- ---------------------------------------------------------------------
