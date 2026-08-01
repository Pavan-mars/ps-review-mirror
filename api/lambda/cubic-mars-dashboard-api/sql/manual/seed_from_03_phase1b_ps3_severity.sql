-- ============================================================================
-- seed_from_03_phase1b_ps3_severity.sql
-- Extracted 2026-07-26 from sql/03_phase1b_ps3_severity.sql
--
-- ####################################################################
-- ##  NOT A MIGRATION. Kept out of the numbered sql/NN_*.sql         ##
-- ##  sequence on purpose, so migrate() cannot run it.               ##
-- ####################################################################
--
-- WHY THIS WAS MOVED
-- ------------------
-- These are hardcoded literal metric rows for city CHI, dated 2026-07-13. They
-- were sitting inside a schema migration, and migrate() runs on EVERY deploy --
-- so any PS1/PS3 purge was undone by the next deploy. Worse, the guards are
-- `ON CONFLICT ... DO NOTHING`, which means they re-insert precisely when the
-- rows are absent: immediately after a wipe.
--
-- They are also the origin of two numbers already removed from the API surface:
--   * ps1_model_performance carries train_auc 1.00 / val_auc 1.00 for GATE
--     against a held-out 0.9038 -- the in-sample fit the dashboard no longer shows.
--   * ps3_device_metrics carries 'train' and 'val' split rows, which /ps3/devices
--     now filters out.
--
-- A schema migration should create schema. Seeding measured model output belongs
-- to the loader that reads the run artifacts. Run this file by hand ONLY if you
-- deliberately want the 13-Jul demo numbers back:
--     psql "$CONN" -f manual/seed_from_03_phase1b_ps3_severity.sql
-- ============================================================================

INSERT INTO ps3_severity_summary
  (city_id, champion_model, test_auc_macro, test_f1_macro, test_accuracy,
   n_incidents, n_major, n_critical, device_note, date_start, date_end,
   feasibility_pct, is_root_cause, true_rootcause_status,
   dominant_feature, dominant_feature_shap, endpoint_name, mlflow_version,
   sm_package, serving_image, dashboard_ready, as_of_date) VALUES
  ('CHI','lightgbm_multiclass',0.9666,0.9050,0.9083,
   34696,18814,15882,'TVM-dominant (train split TVM 25,183 / GATE 1,399)',
   DATE '2024-01-01', DATE '2026-04-11',
   70, FALSE,
   'BLOCKED — SVN_STAGE U_FS_FAULT_CODES / U_FS_ACTION_CODES = 0 rows (9-class root cause). 3-class failure_level_label severity is the shipped workaround.',
   'sn_event_code_id', 0.79, 'chicago-ps3-rootcause-v1', 'v8',
   'chicago-ps3-root-cause/14',
   '170202974600.dkr.ecr.us-east-1.amazonaws.com/cubic-pdm/mars-ps3:latest',
   TRUE, DATE '2026-07-13')
ON CONFLICT (city_id, as_of_date) DO UPDATE SET
  champion_model=EXCLUDED.champion_model, test_auc_macro=EXCLUDED.test_auc_macro,
  test_f1_macro=EXCLUDED.test_f1_macro, test_accuracy=EXCLUDED.test_accuracy,
  n_major=EXCLUDED.n_major, n_critical=EXCLUDED.n_critical,
  dominant_feature=EXCLUDED.dominant_feature, dashboard_ready=EXCLUDED.dashboard_ready;


INSERT INTO ps3_severity_drivers (city_id, feature, shap_importance, solo_auc, driver_rank, as_of_date) VALUES
  ('CHI','sn_event_code_id',     0.7916, 0.758, 1, DATE '2026-07-13'),
  ('CHI','bhu_events_24h',       0.1372, 0.632, 2, DATE '2026-07-13'),
  ('CHI','csc_reader_events_24h',   NULL, 0.593, 3, DATE '2026-07-13'),
  ('CHI','oos_onsets_7d_prior',     NULL, 0.579, 4, DATE '2026-07-13'),
  ('CHI','oos_onsets_24h',          NULL, 0.567, 5, DATE '2026-07-13'),
  ('CHI','component_age_days',   0.0832, 0.563, 6, DATE '2026-07-13'),
  ('CHI','gate_mech_events_24h',    NULL, 0.540, 7, DATE '2026-07-13'),
  ('CHI','events_7d_prior',         NULL, 0.515, 8, DATE '2026-07-13'),
  ('CHI','events_24h_prior',        NULL, 0.512, 9, DATE '2026-07-13'),
  ('CHI','comms_events_24h',        NULL, 0.512,10, DATE '2026-07-13');

