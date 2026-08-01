-- ============================================================================
-- seed_from_11_phase1g_ps1_serving.sql
-- Extracted 2026-07-26 from sql/11_phase1g_ps1_serving.sql
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
--     psql "$CONN" -f manual/seed_from_11_phase1g_ps1_serving.sql
-- ============================================================================

INSERT INTO ps1_model_performance (city_id,device_category,model_name,algorithm,train_auc,train_ap,train_f1,val_auc,val_ap,val_f1,test_auc,test_ap,test_f1,test_prec,test_rec,decision_threshold,mlflow_version,endpoint_name,n_features,quality_gate,promoted,computed_date) VALUES
 ('CHI','TVM','LightGBM (Optuna)','lightgbm',0.79,0.46,0.47,0.77,0.44,0.46,0.7642,0.4389,0.4593,0.3440,0.6910,0.113,'v9','chicago-ps1-3d-tvm-failure-v1',114,'FAIL',FALSE,DATE '2026-07-13'),
 ('CHI','GATE','LightGBM (Optuna)','lightgbm',1.00,1.00,1.00,1.00,1.00,0.99,0.9038,0.4046,0.4481,0.5467,0.3796,0.243,'None','chicago-ps1-3d-gate-failure-v1',96,'FAIL',FALSE,DATE '2026-07-13')
ON CONFLICT (city_id,device_category,computed_date) DO NOTHING;

INSERT INTO ps1_feature_importance (city_id,device_category,feature_name,avg_importance,avg_shap,feat_rank,computed_date) VALUES
 ('CHI','TVM','device_fail_rate_30d',0.80040,0.80040,1,DATE '2026-07-13'),
 ('CHI','TVM','sales_7d_avg',0.12870,0.12870,2,DATE '2026-07-13'),
 ('CHI','TVM','total_outage_min',0.07584,0.07584,3,DATE '2026-07-13'),
 ('CHI','TVM','scrst_events',0.07548,0.07548,4,DATE '2026-07-13'),
 ('CHI','TVM','oos_lifetime_count',0.06891,0.06891,5,DATE '2026-07-13'),
 ('CHI','TVM','availability_pct_7d',0.06402,0.06402,6,DATE '2026-07-13'),
 ('CHI','TVM','device_age_days',0.06318,0.06318,7,DATE '2026-07-13'),
 ('CHI','TVM','events_30d',0.04784,0.04784,8,DATE '2026-07-13'),
 ('CHI','GATE','device_fail_rate_30d',0.44900,0.44900,1,DATE '2026-07-13'),
 ('CHI','GATE','quarter',0.11200,0.11200,2,DATE '2026-07-13'),
 ('CHI','GATE','month',0.06800,0.06800,3,DATE '2026-07-13'),
 ('CHI','GATE','events_30d',0.05100,0.05100,4,DATE '2026-07-13'),
 ('CHI','GATE','availability_pct_7d',0.04300,0.04300,5,DATE '2026-07-13'),
 ('CHI','GATE','total_outage_min',0.03900,0.03900,6,DATE '2026-07-13')
ON CONFLICT (city_id,device_category,feature_name,computed_date) DO NOTHING;

