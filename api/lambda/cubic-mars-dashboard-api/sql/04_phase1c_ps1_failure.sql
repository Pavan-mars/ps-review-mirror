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

-- [seed INSERT INTO ps1_failure_summary moved 2026-07-26 to manual/seed_from_04_phase1c_ps1_failure.sql -- migrate() runs on every deploy, so leaving
--  hardcoded 13-Jul metric rows here silently undid every purge.]
DELETE FROM ps1_leaderboard WHERE city_id='CHI' AND as_of_date=DATE '2026-07-13';
-- [seed INSERT INTO ps1_leaderboard moved 2026-07-26 to manual/seed_from_04_phase1c_ps1_failure.sql -- migrate() runs on every deploy, so leaving
--  hardcoded 13-Jul metric rows here silently undid every purge.]
DELETE FROM ps1_features WHERE city_id='CHI' AND as_of_date=DATE '2026-07-13';
-- [seed INSERT INTO ps1_features moved 2026-07-26 to manual/seed_from_04_phase1c_ps1_failure.sql -- migrate() runs on every deploy, so leaving
--  hardcoded 13-Jul metric rows here silently undid every purge.]
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

