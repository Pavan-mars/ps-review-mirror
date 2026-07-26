-- =====================================================================
-- CUBIC MARS Chicago — Phase-1c  PS1 FAILURE-PREDICTION tables + backfill
-- Date: 2026-07-13   Runs AFTER 01 + 02 + 03. Idempotent.
--
-- HONEST STATE (from run 20260713_0905 console.log): the run TRAINED all models
-- but did NOT pass — both quality gates FAILED (recall below floor), neither model
-- was promoted, SageMaker registration was blocked (AWS_REGION undefined → ARN not
-- registered), the Gates model over-fit via Optuna val-leakage (train/val AUC 1.000)
-- and its calibration collapsed AUC 0.904→0.681. Endpoints show a prior/None model,
-- not a validated one. These tables carry that truth for an honest PS1 dashboard tab.
-- =====================================================================


-- ---------- ps1_failure_summary : per-device run scorecard ----------
CREATE TABLE IF NOT EXISTS ps1_failure_summary (
  city_id         city_code   NOT NULL REFERENCES cities(id),
  device          VARCHAR(10) NOT NULL,      -- 'TVM' / 'Gates'
  champion_model  VARCHAR(48),
  -- champion_final TEST metrics
  test_auc        NUMERIC(6,4),
  test_ap         NUMERIC(6,4),
  test_accuracy   NUMERIC(6,4),
  test_f1         NUMERIC(6,4),
  test_precision  NUMERIC(6,4),
  test_recall     NUMERIC(6,4),
  -- operating point (active F2-optimal threshold)
  op_threshold    NUMERIC(7,4),
  op_fleet_pct    NUMERIC(5,1),
  op_precision    NUMERIC(6,4),
  op_recall       NUMERIC(6,4),
  op_f2           NUMERIC(6,4),
  recall_floor    NUMERIC(6,4),
  quality_gate    VARCHAR(8),                -- 'FAIL' / 'PASS'
  promoted        BOOLEAN     DEFAULT FALSE,
  -- calibration (Brier) + ranking (top-10% coverage)
  brier_raw       NUMERIC(7,4),
  brier_cal       NUMERIC(7,4),
  auc_cal         NUMERIC(6,4),
  prec_at_k       NUMERIC(6,4),
  rec_at_k        NUMERIC(6,4),
  lift_at_k       NUMERIC(6,2),
  map_score       NUMERIC(6,4),
  -- registration / serving
  mlflow_version  VARCHAR(16),
  sm_registered   BOOLEAN     DEFAULT FALSE,
  endpoint_name   VARCHAR(64),
  overfit_flag    BOOLEAN     DEFAULT FALSE,
  -- scope
  n_train         INT,
  n_test          INT,
  n_test_pos      INT,
  base_rate_pct   NUMERIC(6,2),
  target          VARCHAR(20),
  run_id          VARCHAR(24),
  as_of_date      DATE        NOT NULL,
  PRIMARY KEY (city_id, device, as_of_date)
);

-- ---------- ps1_leaderboard : every algorithm per device ----------
CREATE TABLE IF NOT EXISTS ps1_leaderboard (
  city_id     city_code   NOT NULL REFERENCES cities(id),
  device      VARCHAR(10) NOT NULL,
  model       VARCHAR(40) NOT NULL,
  auc         NUMERIC(6,4),
  ap          NUMERIC(6,4),
  f1          NUMERIC(6,4),
  prec        NUMERIC(6,4),
  rec         NUMERIC(6,4),
  lb_rank     SMALLINT,
  is_champion BOOLEAN     DEFAULT FALSE,
  note        VARCHAR(60),
  as_of_date  DATE        NOT NULL,
  PRIMARY KEY (city_id, device, model, as_of_date)
);

-- ---------- ps1_features : top SHAP drivers per device ----------
CREATE TABLE IF NOT EXISTS ps1_features (
  city_id       city_code   NOT NULL REFERENCES cities(id),
  device        VARCHAR(10) NOT NULL,
  feature       VARCHAR(48) NOT NULL,
  mean_abs_shap NUMERIC(9,5),
  pct_total     NUMERIC(6,2),
  feat_rank     SMALLINT    NOT NULL,
  as_of_date    DATE        NOT NULL,
  PRIMARY KEY (city_id, device, feat_rank, as_of_date)
);

-- ===================== BACKFILL (locked 20260713_0905 numbers) =====================

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

DELETE FROM ps1_leaderboard WHERE city_id='CHI' AND as_of_date=DATE '2026-07-13';
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

DELETE FROM ps1_features WHERE city_id='CHI' AND as_of_date=DATE '2026-07-13';
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

-- ---------- register PS1 champions in ml_models (not promoted → is_active FALSE) ----------
INSERT INTO ml_models
 (model_name,problem_stmt,city_id,device_type,version,algorithm,accuracy,f1_score,training_date,
  s3_artifact_uri,is_active,deployed_env,is_champion,primary_metric_value,primary_metric_name) VALUES
 ('ps1-3d-tvm-device-failure','PS1','CHI','tvms','v9','LightGBM (Optuna)',0.7033,0.4593,
  TIMESTAMPTZ '2026-07-13 08:53:00+00','(not registered — AWS_REGION bug)',FALSE,'dev',TRUE,0.4389,'ap'),
 ('ps1-3d-gate-device-failure','PS1','CHI','gates','v9','LightGBM (Optuna)',0.9950,0.4481,
  TIMESTAMPTZ '2026-07-13 08:53:00+00','(not registered — AWS_REGION bug)',FALSE,'dev',TRUE,0.4046,'ap')
ON CONFLICT (problem_stmt,city_id,device_type,version) DO UPDATE SET
  algorithm=EXCLUDED.algorithm, accuracy=EXCLUDED.accuracy, f1_score=EXCLUDED.f1_score,
  is_active=EXCLUDED.is_active, is_champion=EXCLUDED.is_champion,
  primary_metric_value=EXCLUDED.primary_metric_value, primary_metric_name=EXCLUDED.primary_metric_name;

