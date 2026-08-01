-- ============================================================================
-- seed_from_04_phase1c_ps1_failure.sql
-- Extracted 2026-07-26 from sql/04_phase1c_ps1_failure.sql
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
--     psql "$CONN" -f manual/seed_from_04_phase1c_ps1_failure.sql
-- ============================================================================

INSERT INTO ps1_failure_summary
 (city_id,device,champion_model,test_auc,test_ap,test_accuracy,test_f1,test_precision,test_recall,
  op_threshold,op_fleet_pct,op_precision,op_recall,op_f2,recall_floor,quality_gate,promoted,
  brier_raw,brier_cal,auc_cal,prec_at_k,rec_at_k,lift_at_k,map_score,
  mlflow_version,sm_registered,endpoint_name,overfit_flag,n_train,n_test,n_test_pos,base_rate_pct,target,run_id,as_of_date) VALUES
 ('CHI','TVM','LightGBM (Optuna)',0.7642,0.4389,0.7033,0.4593,0.3440,0.6910,
  0.1132,39.7,0.3350,0.7292,0.5903,0.80,'FAIL',FALSE,
  0.1823,0.1313,0.7632,0.4941,0.2709,2.71,0.4268,
  'v9',FALSE,'chicago-ps1-3d-tvm-failure-v1',FALSE,272692,45681,8331,18.24,'will_fail_3d','20260713_0905',DATE '2026-07-13'),
 ('CHI','Gates','LightGBM (Optuna)',0.9038,0.4046,0.9950,0.4481,0.5467,0.3796,
  0.0100,0.4,0.5493,0.3611,0.3877,0.70,'FAIL',FALSE,
  0.0039,0.0045,0.6809,0.0222,0.4144,4.14,0.2342,
  'None',FALSE,'chicago-ps1-3d-gate-failure-v1',TRUE,514719,80720,432,0.54,'will_fail_3d','20260713_0905',DATE '2026-07-13')
ON CONFLICT (city_id,device,as_of_date) DO UPDATE SET
  test_auc=EXCLUDED.test_auc, test_ap=EXCLUDED.test_ap, test_recall=EXCLUDED.test_recall,
  op_recall=EXCLUDED.op_recall, quality_gate=EXCLUDED.quality_gate, overfit_flag=EXCLUDED.overfit_flag;


INSERT INTO ps1_leaderboard (city_id,device,model,auc,ap,f1,prec,rec,lb_rank,is_champion,note,as_of_date) VALUES
 ('CHI','TVM','honest_stack_calib',0.766,0.440,NULL,NULL,NULL,1,FALSE,'calibrated stacking ensemble',DATE '2026-07-13'),
 ('CHI','TVM','LightGBM (Optuna)',0.764,0.439,0.459,0.344,0.691,2,TRUE,'deployed champion',DATE '2026-07-13'),
 ('CHI','TVM','XGBoost (Optuna)',0.763,0.437,0.459,0.340,0.707,3,FALSE,'',DATE '2026-07-13'),
 ('CHI','TVM','LightGBM',0.761,0.429,0.454,0.329,0.729,4,FALSE,'',DATE '2026-07-13'),
 ('CHI','TVM','CatBoost',0.762,0.428,0.456,0.322,0.781,5,FALSE,'',DATE '2026-07-13'),
 ('CHI','TVM','XGBoost',0.760,0.426,0.455,0.327,0.750,6,FALSE,'',DATE '2026-07-13'),
 ('CHI','TVM','Random Forest',0.700,0.358,0.378,0.294,0.528,7,FALSE,'',DATE '2026-07-13'),
 ('CHI','TVM','Logistic Reg.',0.657,0.314,0.362,0.261,0.590,8,FALSE,'',DATE '2026-07-13'),
 ('CHI','Gates','CatBoost',0.924,0.419,0.413,0.298,0.676,1,FALSE,'strongest honest Gates model',DATE '2026-07-13'),
 ('CHI','Gates','XGBoost',0.920,0.412,0.396,0.278,0.688,2,FALSE,'',DATE '2026-07-13'),
 ('CHI','Gates','honest_stack_calib',0.930,0.412,NULL,NULL,NULL,3,FALSE,'calibrated stacking ensemble',DATE '2026-07-13'),
 ('CHI','Gates','LightGBM (Optuna)',0.904,0.405,0.448,0.547,0.380,4,TRUE,'deployed champion — over-fit (train/val AUC 1.0)',DATE '2026-07-13'),
 ('CHI','Gates','XGBoost (Optuna)',0.918,0.378,0.488,0.520,0.461,5,FALSE,'over-fit (train/val AUC 1.0)',DATE '2026-07-13'),
 ('CHI','Gates','LightGBM',0.924,0.357,0.421,0.303,0.692,6,FALSE,'',DATE '2026-07-13'),
 ('CHI','Gates','Random Forest',0.924,0.219,0.317,0.240,0.468,7,FALSE,'',DATE '2026-07-13'),
 ('CHI','Gates','Logistic Reg.',0.884,0.076,0.114,0.062,0.734,8,FALSE,'',DATE '2026-07-13');


INSERT INTO ps1_features (city_id,device,feature,mean_abs_shap,pct_total,feat_rank,as_of_date) VALUES
 ('CHI','TVM','device_fail_rate_30d',0.80040,47.40,1,DATE '2026-07-13'),
 ('CHI','TVM','sales_7d_avg',0.12870,7.60,2,DATE '2026-07-13'),
 ('CHI','TVM','total_outage_min',0.07584,4.50,3,DATE '2026-07-13'),
 ('CHI','TVM','scrst_events',0.07548,4.50,4,DATE '2026-07-13'),
 ('CHI','TVM','oos_lifetime_count',0.06891,4.10,5,DATE '2026-07-13'),
 ('CHI','TVM','availability_pct_7d',0.06402,3.80,6,DATE '2026-07-13'),
 ('CHI','TVM','device_age_days',0.06318,3.70,7,DATE '2026-07-13'),
 ('CHI','TVM','events_30d',0.04784,2.80,8,DATE '2026-07-13'),
 ('CHI','Gates','device_fail_rate_30d',2.14016,44.90,1,DATE '2026-07-13'),
 ('CHI','Gates','quarter',0.35247,7.40,2,DATE '2026-07-13'),
 ('CHI','Gates','system_events',0.28067,5.90,3,DATE '2026-07-13'),
 ('CHI','Gates','month',0.17116,3.60,4,DATE '2026-07-13'),
 ('CHI','Gates','gate_mech_events',0.16319,3.40,5,DATE '2026-07-13'),
 ('CHI','Gates','ARRAY_POSITION',0.14129,3.00,6,DATE '2026-07-13'),
 ('CHI','Gates','oos_flag',0.13913,2.90,7,DATE '2026-07-13'),
 ('CHI','Gates','tap_reject_rate_pct',0.12827,2.70,8,DATE '2026-07-13');

