-- =====================================================================
-- Phase-1g : PS1 serving tables backing the teammates' rich PS1 tab.
-- Feeds /ps1/predictions, /ps1/model-performance, /ps1/risk-trend,
-- /ps1/feature-importance, /ps1/station-summary, /ps1/explainability.
-- DDL only (idempotent). Populated by the PS1 batch-scoring notebook via
-- sql/12_ps1_serving_backfill.sql. PS1 model NOT promoted (QGs failed) -
-- predictions are for review, surfaced behind a not-promoted ribbon.
-- =====================================================================

-- drop any pre-existing (colleague/legacy) PS1 serving tables so our exact schema wins
-- SAFETY (v2): only drop the 2 tables this file re-seeds. The scored-data tables
-- (predictions, explainability, station, risk_bands, threshold_sweep, calibration,
-- confusion, risk_trend) are populated by the scoring notebook backfill and are
-- PRESERVED here via CREATE IF NOT EXISTS so a consolidated re-deploy never wipes them.
DROP TABLE IF EXISTS ps1_model_performance, ps1_feature_importance CASCADE;

-- per-device scored predictions (recent snapshot)
CREATE TABLE IF NOT EXISTS ps1_failure_predictions (
  city_id city_code NOT NULL REFERENCES cities(id),
  prediction_id VARCHAR(48) NOT NULL,
  device_category VARCHAR(12), device_id VARCHAR(20), facility_id VARCHAR(20),
  failure_probability NUMERIC(7,5), predicted_label SMALLINT,
  decision_threshold NUMERIC(7,5), prediction_date DATE, inference_ts VARCHAR(32),
  computed_date DATE NOT NULL,
  PRIMARY KEY (city_id, prediction_id, computed_date)
);
CREATE INDEX IF NOT EXISTS ix_ps1_pred ON ps1_failure_predictions (city_id, device_category, computed_date);

-- per-device model performance (train/val/test metrics -> route nests as s3_metrics)
CREATE TABLE IF NOT EXISTS ps1_model_performance (
  city_id city_code NOT NULL REFERENCES cities(id),
  device_category VARCHAR(12) NOT NULL, model_name VARCHAR(60), algorithm VARCHAR(40),
  train_auc NUMERIC(7,4), train_ap NUMERIC(7,4), train_f1 NUMERIC(7,4),
  val_auc NUMERIC(7,4), val_ap NUMERIC(7,4), val_f1 NUMERIC(7,4),
  test_auc NUMERIC(7,4), test_ap NUMERIC(7,4), test_f1 NUMERIC(7,4),
  test_prec NUMERIC(7,4), test_rec NUMERIC(7,4),
  decision_threshold NUMERIC(7,5), mlflow_version VARCHAR(16), endpoint_name VARCHAR(80),
  n_features INT, quality_gate VARCHAR(8), promoted BOOLEAN, computed_date DATE NOT NULL,
  PRIMARY KEY (city_id, device_category, computed_date)
);

-- daily risk trend (per device category)
CREATE TABLE IF NOT EXISTS ps1_risk_trend (
  city_id city_code NOT NULL REFERENCES cities(id),
  trend_date DATE NOT NULL, device_category VARCHAR(12) NOT NULL,
  avg_prob_pct NUMERIC(7,3), failures INT, total INT, computed_date DATE NOT NULL,
  PRIMARY KEY (city_id, trend_date, device_category, computed_date)
);

-- feature importance per device category (aggregate SHAP)
CREATE TABLE IF NOT EXISTS ps1_feature_importance (
  city_id city_code NOT NULL REFERENCES cities(id),
  device_category VARCHAR(12) NOT NULL, feature_name VARCHAR(60) NOT NULL,
  avg_importance NUMERIC(9,5), avg_shap NUMERIC(9,5), feat_rank SMALLINT, computed_date DATE NOT NULL,
  PRIMARY KEY (city_id, device_category, feature_name, computed_date)
);

-- per-prediction explainability (SHAP)
CREATE TABLE IF NOT EXISTS ps1_explainability (
  city_id city_code NOT NULL REFERENCES cities(id),
  prediction_id VARCHAR(48) NOT NULL, feature_name VARCHAR(60) NOT NULL,
  shap_value NUMERIC(10,6), feature_value VARCHAR(40), computed_date DATE NOT NULL,
  PRIMARY KEY (city_id, prediction_id, feature_name, computed_date)
);

-- per-facility rollup of predictions
CREATE TABLE IF NOT EXISTS ps1_station_summary (
  city_id city_code NOT NULL REFERENCES cities(id),
  facility_id VARCHAR(20) NOT NULL, total_devices INT, predicted_failures INT,
  avg_risk_pct NUMERIC(7,3), critical_count INT, high_count INT, medium_count INT,
  last_inference_date DATE, computed_date DATE NOT NULL,
  PRIMARY KEY (city_id, facility_id, computed_date)
);

-- seed: model-performance + feature-importance from run 20260713_0905 (real).
-- predictions/risk-trend/station-summary stay empty until the scoring notebook runs.
DELETE FROM ps1_model_performance WHERE city_id='CHI' AND computed_date=DATE '2026-07-13';
INSERT INTO ps1_model_performance (city_id,device_category,model_name,algorithm,train_auc,train_ap,train_f1,val_auc,val_ap,val_f1,test_auc,test_ap,test_f1,test_prec,test_rec,decision_threshold,mlflow_version,endpoint_name,n_features,quality_gate,promoted,computed_date) VALUES
 ('CHI','TVM','LightGBM (Optuna)','lightgbm',0.79,0.46,0.47,0.77,0.44,0.46,0.7642,0.4389,0.4593,0.3440,0.6910,0.113,'v9','chicago-ps1-3d-tvm-failure-v1',114,'FAIL',FALSE,DATE '2026-07-13'),
 ('CHI','GATE','LightGBM (Optuna)','lightgbm',1.00,1.00,1.00,1.00,1.00,0.99,0.9038,0.4046,0.4481,0.5467,0.3796,0.243,'None','chicago-ps1-3d-gate-failure-v1',96,'FAIL',FALSE,DATE '2026-07-13')
ON CONFLICT (city_id,device_category,computed_date) DO NOTHING;
DELETE FROM ps1_feature_importance WHERE city_id='CHI' AND computed_date=DATE '2026-07-13';
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

CREATE TABLE IF NOT EXISTS ps1_risk_bands (
  city_id city_code NOT NULL REFERENCES cities(id),
  device_category VARCHAR(12) NOT NULL, band VARCHAR(10) NOT NULL,
  device_count INT, pct NUMERIC(6,2), computed_date DATE NOT NULL,
  PRIMARY KEY (city_id, device_category, band, computed_date)
);
CREATE TABLE IF NOT EXISTS ps1_threshold_sweep (
  city_id city_code NOT NULL REFERENCES cities(id),
  device_category VARCHAR(12) NOT NULL, threshold NUMERIC(5,2) NOT NULL,
  precision NUMERIC(7,4), recall NUMERIC(7,4), alert_rate NUMERIC(7,4), f1 NUMERIC(7,4),
  computed_date DATE NOT NULL,
  PRIMARY KEY (city_id, device_category, threshold, computed_date)
);
CREATE TABLE IF NOT EXISTS ps1_calibration (
  city_id city_code NOT NULL REFERENCES cities(id),
  device_category VARCHAR(12) NOT NULL, bin_lo NUMERIC(7,4) NOT NULL,
  bin_hi NUMERIC(7,4), pred_mean NUMERIC(7,4), actual_rate NUMERIC(7,4), n INT,
  computed_date DATE NOT NULL,
  PRIMARY KEY (city_id, device_category, bin_lo, computed_date)
);
CREATE TABLE IF NOT EXISTS ps1_confusion (
  city_id city_code NOT NULL REFERENCES cities(id),
  device_category VARCHAR(12) NOT NULL, tp INT, fp INT, tn INT, fn INT,
  computed_date DATE NOT NULL,
  PRIMARY KEY (city_id, device_category, computed_date)
);

