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
-- [seed INSERT INTO ps1_model_performance moved 2026-07-26 to manual/seed_from_11_phase1g_ps1_serving.sql -- migrate() runs on every deploy, so leaving
--  hardcoded 13-Jul metric rows here silently undid every purge.]
DELETE FROM ps1_feature_importance WHERE city_id='CHI' AND computed_date=DATE '2026-07-13';
-- [seed INSERT INTO ps1_feature_importance moved 2026-07-26 to manual/seed_from_11_phase1g_ps1_serving.sql -- migrate() runs on every deploy, so leaving
--  hardcoded 13-Jul metric rows here silently undid every purge.]
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

