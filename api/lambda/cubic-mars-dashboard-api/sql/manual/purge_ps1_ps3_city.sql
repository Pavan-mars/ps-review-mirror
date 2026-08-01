-- ============================================================================
-- purge_ps1_ps3_city.sql                       CUBIC MARS Chicago  2026-07-26
--
-- ####################################################################
-- ##  THIS FILE IS DESTRUCTIVE AND IS **NOT** A MIGRATION.           ##
-- ##  It lives in sql/manual/ precisely so that migrate() cannot     ##
-- ##  pick it up. migrate() uses an explicit file list of            ##
-- ##  sql/NN_*.sql -- do NOT renumber this file into that sequence,  ##
-- ##  or every future deploy will wipe production data.              ##
-- ####################################################################
--
-- Clears the PS1 and PS3 serving tables for ONE city so a fresh notebook run
-- lands on clean tables, with nothing left over from the backfills, the
-- 13-Jul PS1->PS3 bridge smoke test, or the hand-seeded metric rows.
--
-- Prefer the Lambda action, which is safer (confirm token, dry run, existence
-- checks, single transaction, before/after counts):
--
--     aws lambda invoke --function-name cubic-mars-dashboard-api \
--       --cli-binary-format raw-in-base64-out \
--       --payload '{"action":"purge","scope":"both","city":"CHI","dry_run":true}' \
--       /dev/stdout
--
-- Use this file only for a direct psql session.
--
-- SCOPING: every PS1/PS3 table carries city_id, so this is DELETE ... WHERE
-- city_id, never TRUNCATE. Boston / LA / TOC rows in the same tables are not
-- touched. Change :city below if you mean a different tenant.
--
-- NOT INCLUDED, on purpose:
--   servicenow_staging   operator-created records, not model output
--   ml_batch_load_audit  the evidence of what previous loads did -- wiping it
--                        removes the only record of how the bad data arrived
--   dim_station, cities, ps2_*, ps4_*, ps5_*   out of scope
-- Uncomment the final block if you genuinely want the first two gone as well.
-- ============================================================================

\set city 'CHI'

BEGIN;

-- ---------------------------------------------------------------------
-- Counts BEFORE. Read this output before committing.
-- ---------------------------------------------------------------------
SELECT 'BEFORE' AS phase, tbl, cnt FROM (
  SELECT 'ps1_failure_predictions' tbl, COUNT(*) cnt FROM ps1_failure_predictions WHERE city_id = :'city'
  UNION ALL SELECT 'ps1_serial_predictions', COUNT(*) FROM ps1_serial_predictions WHERE city_id = :'city'
  UNION ALL SELECT 'ps1_inference_runs',     COUNT(*) FROM ps1_inference_runs     WHERE city_id = :'city'
  UNION ALL SELECT 'ps1_failure_summary',    COUNT(*) FROM ps1_failure_summary    WHERE city_id = :'city'
  UNION ALL SELECT 'ps1_leaderboard',        COUNT(*) FROM ps1_leaderboard        WHERE city_id = :'city'
  UNION ALL SELECT 'ps1_model_performance',  COUNT(*) FROM ps1_model_performance  WHERE city_id = :'city'
  UNION ALL SELECT 'ps3_incident_predictions', COUNT(*) FROM ps3_incident_predictions WHERE city_id = :'city'
  UNION ALL SELECT 'ps3_device_predictions',   COUNT(*) FROM ps3_device_predictions   WHERE city_id = :'city'
  UNION ALL SELECT 'ps3_serial_predictions',   COUNT(*) FROM ps3_serial_predictions   WHERE city_id = :'city'
) s ORDER BY cnt DESC;

-- ---------------------------------------------------------------------
-- PS1 - serving grain, then model metadata.
-- ---------------------------------------------------------------------
DELETE FROM ps1_failure_predictions WHERE city_id = :'city';
DELETE FROM ps1_serial_predictions  WHERE city_id = :'city';
DELETE FROM ps1_inference_runs      WHERE city_id = :'city';
DELETE FROM ps1_explainability      WHERE city_id = :'city';
DELETE FROM ps1_station_summary     WHERE city_id = :'city';
DELETE FROM ps1_risk_trend          WHERE city_id = :'city';
DELETE FROM ps1_risk_bands          WHERE city_id = :'city';
DELETE FROM ps1_failure_summary     WHERE city_id = :'city';
DELETE FROM ps1_leaderboard         WHERE city_id = :'city';
DELETE FROM ps1_features            WHERE city_id = :'city';
DELETE FROM ps1_model_performance   WHERE city_id = :'city';
DELETE FROM ps1_feature_importance  WHERE city_id = :'city';
DELETE FROM ps1_threshold_sweep     WHERE city_id = :'city';
DELETE FROM ps1_calibration         WHERE city_id = :'city';
DELETE FROM ps1_confusion           WHERE city_id = :'city';

-- ---------------------------------------------------------------------
-- PS3 - two-head run (migration 15).
-- ---------------------------------------------------------------------
DELETE FROM ps3_incident_predictions    WHERE city_id = :'city';
DELETE FROM ps3_device_predictions      WHERE city_id = :'city';
DELETE FROM ps3_serial_predictions      WHERE city_id = :'city';
DELETE FROM ps3_head_summary            WHERE city_id = :'city';
DELETE FROM ps3_head_leaderboard        WHERE city_id = :'city';
DELETE FROM ps3_head_class_metrics      WHERE city_id = :'city';
DELETE FROM ps3_head_feature_importance WHERE city_id = :'city';
DELETE FROM ps3_leakage_scan            WHERE city_id = :'city';
DELETE FROM ps3_model_runs              WHERE city_id = :'city';

-- ---------------------------------------------------------------------
-- PS3 - the earlier severity model's tables (migration 03/06). These are not
-- present in every checkout, so each is guarded: a missing table is skipped
-- rather than aborting the transaction and leaving a half-purged database.
-- ---------------------------------------------------------------------
DO $$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY['ps3_severity_predictions','ps3_severity_summary',
                           'ps3_severity_drivers','ps3_device_metrics']
  LOOP
    IF to_regclass('public.' || t) IS NOT NULL THEN
      EXECUTE format('DELETE FROM %I WHERE city_id = %L', t, 'CHI');
      RAISE NOTICE 'purged %', t;
    ELSE
      RAISE NOTICE 'skipped % (not present in this database)', t;
    END IF;
  END LOOP;
END $$;

-- ---------------------------------------------------------------------
-- Counts AFTER. Every row should be 0. Inspect, then COMMIT or ROLLBACK.
-- ---------------------------------------------------------------------
SELECT 'AFTER' AS phase, tbl, cnt FROM (
  SELECT 'ps1_failure_predictions' tbl, COUNT(*) cnt FROM ps1_failure_predictions WHERE city_id = :'city'
  UNION ALL SELECT 'ps1_serial_predictions', COUNT(*) FROM ps1_serial_predictions WHERE city_id = :'city'
  UNION ALL SELECT 'ps1_failure_summary',    COUNT(*) FROM ps1_failure_summary    WHERE city_id = :'city'
  UNION ALL SELECT 'ps3_incident_predictions', COUNT(*) FROM ps3_incident_predictions WHERE city_id = :'city'
  UNION ALL SELECT 'ps3_device_predictions',   COUNT(*) FROM ps3_device_predictions   WHERE city_id = :'city'
  UNION ALL SELECT 'ps3_serial_predictions',   COUNT(*) FROM ps3_serial_predictions   WHERE city_id = :'city'
) s ORDER BY tbl;

-- Everything above is inside one transaction. Nothing is durable until you run:
COMMIT;
-- ...or discard the whole thing with:
-- ROLLBACK;

-- ---------------------------------------------------------------------
-- OPTIONAL, not run by default. Uncomment only if you mean it.
--   servicenow_staging  holds incidents an operator staged from the dashboard.
--   ml_batch_load_audit is the record of what every prior load did -- it is
--   how you would prove where the bad rows came from. Keep it unless you have
--   a reason not to.
-- ---------------------------------------------------------------------
-- BEGIN;
-- DELETE FROM servicenow_staging  WHERE city_id = 'CHI';
-- DELETE FROM ml_batch_load_audit WHERE city_id = 'CHI' AND ps_id IN ('PS1','PS3');
-- COMMIT;
