-- =====================================================================
-- CUBIC MARS Chicago -- sql/54
-- URGENT CORRECTION. sql/53 stamped the scorecard with the WRONG run_id,
-- and the API is now asserting serving_matches_scorecard = true when the
-- truth is false. This is worse than the NULL it replaced.
-- Date: 2026-08-11   Idempotent. Reversible -- rollback in the footer.
--
-- WHAT WENT WRONG
-- ---------------
-- sql/53 backfilled run_id from "the latest train-kind row in
-- ps1_inference_runs per endpoint", ordered by run_ts DESC. TWO training runs
-- landed on 2026-07-26:
--
--     ps1_sklearn_20260726   12:00Z   mlflow_version 'v3-sklearn'
--     ps1_20260726           16:40Z   mlflow_version 'v2'          <- later
--
-- So "latest" selected the SPARK run. But the rows in ps1_model_performance
-- are the SKLEARN run -- they carry mlflow_version 'v3-sklearn' and the AUC
-- figures from sql/load/ps1_sklearn_20260726.sql.
--
-- The result: sklearn metrics stamped with a Spark run_id. Because the serving
-- lookup ALSO resolves to the Spark run, the two now agree, and
-- /ps1/model-performance reports serving_matches_scorecard = TRUE.
--
-- THAT IS THE EXACT CLAIM E-1 EXISTS TO PREVENT. The repository states the
-- opposite in sql/load/ps1_sklearn_20260726.sql:
--
--   "chicago-ps1-3d-{gate,tvm,validator}-failure-v1 are still serving the
--    SPARK champion. Until those are re-registered, this scorecard describes
--    the selected model, not the one answering inference calls."
--
-- A NULL run_id said "unverified", which was true. matches=true says "the
-- endpoint serves this model", which is false. Filling a column with a
-- confident wrong value is a worse outcome than leaving it empty -- the whole
-- argument this codebase has been making all day, and sql/53 broke it.
--
-- THE CONFLATION, NAMED SO IT IS NOT REPEATED
-- -------------------------------------------
-- "The most recent training run for this endpoint" and "the training run that
-- produced THIS scorecard row" are different things. They coincide only when
-- one model is trained per endpoint per day. Two runs on 2026-07-26 broke that
-- assumption, and nothing in sql/53 noticed because it never asked which run
-- wrote the row.
--
-- THE DISCRIMINATOR IS IN THE ROW ITSELF, NOT INFERRED
-- ----------------------------------------------------
-- Each load stamped its own mlflow_version into ps1_model_performance:
--     sql/load/ps1_sklearn_20260726.sql  ->  'v3-sklearn'
--     sql/load/ps1_run_20260726.sql      ->  'v2'
-- and each declared its own run_id in the matching ps1_inference_runs INSERT.
-- The mapping is therefore stated by the load files, not guessed here.
-- =====================================================================

-- ---------------------------------------------------------------------
-- 1. Point each scorecard row at the run that actually produced it.
--    Unconditional on the current value: sql/53 wrote a wrong one, so a
--    COALESCE guard would preserve the error.
-- ---------------------------------------------------------------------
UPDATE ps1_model_performance
SET    run_id = 'ps1_sklearn_20260726'
WHERE  mlflow_version = 'v3-sklearn'
  AND  (run_id IS DISTINCT FROM 'ps1_sklearn_20260726');

UPDATE ps1_model_performance
SET    run_id = 'ps1_20260726'
WHERE  mlflow_version = 'v2'
  AND  (run_id IS DISTINCT FROM 'ps1_20260726');

-- ---------------------------------------------------------------------
-- 2. A view that states the serving gap outright, so it does not depend on
--    a route computing it correctly. This is the E-1 disclosure in the data
--    layer, where it cannot be lost to a handler bug.
--
--    NOTE ON WHAT "serving" MEANS HERE, because it is a PROXY and should not
--    be read as ground truth: it is the latest train-kind run recorded for
--    that endpoint in ps1_inference_runs. Model REGISTRATION is not model
--    DEPLOYMENT. Nothing in this database observes what a SageMaker endpoint
--    is actually running -- only DescribeEndpointConfig can answer that. The
--    column is evidence, not proof.
-- ---------------------------------------------------------------------
CREATE OR REPLACE VIEW v_ps1_serving_gap AS
WITH latest_train AS (
  SELECT DISTINCT ON (city_id, device_category, endpoint_name)
         city_id, device_category, endpoint_name,
         run_id AS serving_run_id, run_ts AS serving_run_ts
  FROM   ps1_inference_runs
  WHERE  run_kind = 'train'
  ORDER  BY city_id, device_category, endpoint_name, run_ts DESC
)
SELECT mp.city_id,
       mp.device_category,
       mp.endpoint_name,
       mp.mlflow_version           AS scorecard_mlflow_version,
       mp.run_id                   AS scorecard_run_id,
       lt.serving_run_id,
       lt.serving_run_ts,
       (mp.run_id = lt.serving_run_id) AS scorecard_is_serving_run,
       CASE
         WHEN lt.serving_run_id IS NULL
           THEN 'UNKNOWN -- no train-kind run recorded for this endpoint'
         WHEN mp.run_id IS NULL
           THEN 'UNKNOWN -- the scorecard row has no run_id'
         WHEN mp.run_id = lt.serving_run_id
           THEN 'ALIGNED -- the most recently registered run is the one this scorecard describes'
         ELSE 'GAP -- this scorecard describes ' || mp.run_id ||
              ' but the most recently registered run for this endpoint is ' ||
              lt.serving_run_id || '. These metrics are NOT what that endpoint returns.'
       END AS assessment
FROM   ps1_model_performance mp
LEFT   JOIN latest_train lt
       ON  lt.city_id         = mp.city_id
       AND lt.device_category = mp.device_category
       AND lt.endpoint_name   = mp.endpoint_name;

COMMENT ON VIEW v_ps1_serving_gap IS
  'E-1 in the data layer. serving_run_id is the latest train-kind run recorded in '
  'ps1_inference_runs for that endpoint -- a PROXY. Registration is not deployment; '
  'only DescribeEndpointConfig observes what an endpoint actually runs. Expected '
  'state 2026-08-11: GAP on all three fleets, scorecard ps1_sklearn_20260726 vs '
  'registered ps1_20260726 (Spark). sql/54.';

-- ---------------------------------------------------------------------
-- ROLLBACK:
--   UPDATE ps1_model_performance SET run_id = NULL
--   WHERE city_id='CHI' AND computed_date=DATE '2026-07-26';
--   DROP VIEW IF EXISTS v_ps1_serving_gap;
--   -- NULL is the honest pre-sql/53 state. Do NOT restore sql/53's value.
--
-- AND THE UPSTREAM FIX, STILL NOT DONE: widen the INSERT in
-- sql/load/ps1_sklearn_20260726.sql to name run_id explicitly. Every repair in
-- 52, 53 and 54 exists because that one INSERT omitted five columns.
-- ---------------------------------------------------------------------
