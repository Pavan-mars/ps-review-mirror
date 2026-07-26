-- =====================================================================
-- CUBIC MARS Chicago - Phase-2a  PS3 TWO-HEAD (severity + root cause)
-- Date: 2026-07-26.  Runs AFTER 01..14.  Idempotent.  ADDITIVE ONLY.
--
-- WHY THIS EXISTS
--   Migration 03 wired PS3 at a single-head, city-level SEVERITY summary only
--   (ps3_severity_summary/_drivers/_predictions), reflecting the 2026-07-13
--   binary MAJOR/CRITICAL endpoint. The production notebook
--   notebooks/ps3_root_cause_analysis/PS3_SageMaker_MLflow_FeatureStore.ipynb
--   now emits TWO heads per device category:
--       head='severity'   -> target failure_level_label
--       head='root_cause' -> target derived_component_type
--   at THREE grains (incident / device / serial), for TVM and GATE
--   (VALIDATOR emits a stub - no PS3 incidents - and is recorded as modeled=FALSE).
--
--   Column names below are taken from the run artifact contract, not invented:
--     {cat}_{head}_champion_summary.json, {cat}_{head}_leaderboard.csv,
--     {cat}_prediction_explainability.csv, {cat}_leakage_scan.csv,
--     {cat}_incident_predictions.csv, {cat}_device_predictions.csv,
--     {cat}_serial_predictions.csv, ps3_run_summary.json,
--     and the RDS targets named in ps3_rds_load_manifest.json.
--
-- NAMING: migration 03's ps3_severity_* tables are LEFT INTACT. The reserved
--   ps3_root_cause_* prefix from 03's comment is now legitimately used, because
--   the root-cause head is real (derived_component_type from the event matrix),
--   NOT the SVN_STAGE 9-class model that remains blocked. See ps3_head_summary
--   .rootcause_source to keep the two provenances distinguishable.
-- =====================================================================

-- ---------------------------------------------------------------------
-- 1. Run registry - one row per PS3 notebook/batch execution
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS ps3_model_runs (
  city_id            city_code    NOT NULL REFERENCES cities(id),
  run_id             VARCHAR(48)  NOT NULL,
  run_ts             TIMESTAMPTZ  NOT NULL,
  run_kind           VARCHAR(16)  NOT NULL DEFAULT 'train',  -- train | batch_score
  source_notebook    VARCHAR(160),
  git_sha            VARCHAR(40),
  n_incidents        INT,
  device_scope       VARCHAR(60),               -- e.g. 'TVM,GATE,VALIDATOR'
  endpoint_name      VARCHAR(80),
  serving_image      VARCHAR(200),              -- ECR image URI actually used
  mlflow_version     VARCHAR(16),
  sm_package_arn     VARCHAR(200),
  severity_collapse_verified BOOLEAN DEFAULT FALSE,  -- SEVERITY_COLLAPSE keyed on CODES
  notes              TEXT,
  as_of_date         DATE         NOT NULL,
  PRIMARY KEY (city_id, run_id)
);
CREATE INDEX IF NOT EXISTS idx_ps3_runs_city_ts ON ps3_model_runs (city_id, run_ts DESC);

-- ---------------------------------------------------------------------
-- 2. Per (device_category, head) champion scorecard
--    Mirrors {cat}_{head}_champion_summary.json exactly.
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS ps3_head_summary (
  city_id                city_code    NOT NULL REFERENCES cities(id),
  run_id                 VARCHAR(48)  NOT NULL,
  device_category        VARCHAR(12)  NOT NULL,   -- TVM | GATE | VALIDATOR
  head                   VARCHAR(12)  NOT NULL,   -- severity | root_cause
  modeled                BOOLEAN      NOT NULL DEFAULT TRUE,
  target_col             VARCHAR(40),             -- failure_level_label | derived_component_type
  champion               VARCHAR(48),
  n_classes              SMALLINT,
  class_labels           TEXT,                    -- comma-joined, order as reported
  test_f1_macro          NUMERIC(7,4),
  test_f1_weighted       NUMERIC(7,4),
  test_accuracy          NUMERIC(7,4),
  test_balanced_accuracy NUMERIC(7,4),
  test_auc_macro_ovr     NUMERIC(7,4),
  test_pr_auc_macro      NUMERIC(7,4),
  test_cohen_kappa       NUMERIC(7,4),
  test_mcc               NUMERIC(7,4),
  test_log_loss          NUMERIC(9,4),
  macro_f1_floor         NUMERIC(7,4),            -- the v7.0 promotion gate for PS3
  gate_pass              BOOLEAN,
  n_train                INT,
  n_test                 INT,
  n_features             INT,
  rootcause_source       VARCHAR(40),             -- 'derived_component_type' vs 'svn_stage_9class'
  as_of_date             DATE         NOT NULL,
  PRIMARY KEY (city_id, run_id, device_category, head)
);

-- ---------------------------------------------------------------------
-- 3. Per-class metrics (from champion_summary.per_class + class_balance_train)
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS ps3_head_class_metrics (
  city_id         city_code    NOT NULL REFERENCES cities(id),
  run_id          VARCHAR(48)  NOT NULL,
  device_category VARCHAR(12)  NOT NULL,
  head            VARCHAR(12)  NOT NULL,
  class_label     VARCHAR(48)  NOT NULL,
  precision_val   NUMERIC(7,4),   -- 'precision' is reserved in some tooling
  recall_val      NUMERIC(7,4),
  f1              NUMERIC(7,4),
  support         INT,
  train_count     INT,            -- class_balance_train
  as_of_date      DATE         NOT NULL,
  PRIMARY KEY (city_id, run_id, device_category, head, class_label)
);

-- ---------------------------------------------------------------------
-- 4. Model bake-off leaderboard - {cat}_{head}_leaderboard.csv
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS ps3_head_leaderboard (
  city_id         city_code    NOT NULL REFERENCES cities(id),
  run_id          VARCHAR(48)  NOT NULL,
  device_category VARCHAR(12)  NOT NULL,
  head            VARCHAR(12)  NOT NULL,
  model           VARCHAR(48)  NOT NULL,
  f1_macro        NUMERIC(7,4),
  f1_weighted     NUMERIC(7,4),
  accuracy        NUMERIC(7,4),
  auc_macro_ovr   NUMERIC(7,4),
  pr_auc_macro    NUMERIC(7,4),
  fit_s           NUMERIC(10,3),
  lb_rank         SMALLINT,
  is_champion     BOOLEAN      DEFAULT FALSE,
  as_of_date      DATE         NOT NULL,
  PRIMARY KEY (city_id, run_id, device_category, head, model)
);

-- ---------------------------------------------------------------------
-- 5. Feature importance - {cat}_prediction_explainability.csv
--    (head, feature_rank, feature_name, shap_importance)
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS ps3_head_feature_importance (
  city_id         city_code    NOT NULL REFERENCES cities(id),
  run_id          VARCHAR(48)  NOT NULL,
  device_category VARCHAR(12)  NOT NULL,
  head            VARCHAR(12)  NOT NULL,
  feature_rank    SMALLINT     NOT NULL,
  feature_name    VARCHAR(80)  NOT NULL,
  shap_importance NUMERIC(12,6),
  as_of_date      DATE         NOT NULL,
  PRIMARY KEY (city_id, run_id, device_category, head, feature_rank)
);

-- ---------------------------------------------------------------------
-- 6. Leakage scan - {cat}_leakage_scan.csv (headerless: feature, solo_auc)
--    v7.0 gate: nothing may exceed ~0.95 solo AUC.
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS ps3_leakage_scan (
  city_id         city_code    NOT NULL REFERENCES cities(id),
  run_id          VARCHAR(48)  NOT NULL,
  device_category VARCHAR(12)  NOT NULL,
  feature_name    VARCHAR(80)  NOT NULL,
  solo_auc        NUMERIC(7,4),
  flagged         BOOLEAN      GENERATED ALWAYS AS (solo_auc > 0.95) STORED,
  as_of_date      DATE         NOT NULL,
  PRIMARY KEY (city_id, run_id, device_category, feature_name)
);

-- ---------------------------------------------------------------------
-- 7. Per-incident predictions - {cat}_incident_predictions.csv
--    Named in ps3_rds_load_manifest.json as ps3_incident_predictions.
--    Only identity/label columns are typed; the ~30 enr_* model features are
--    kept in a JSONB blob so the loader never breaks when the feature set
--    changes between runs (the rds-push Lambda adds real columns on demand).
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS ps3_incident_predictions (
  city_id                city_code    NOT NULL REFERENCES cities(id),
  run_id                 VARCHAR(48)  NOT NULL,
  availability_event_id  VARCHAR(48)  NOT NULL,
  device_id              VARCHAR(40),
  mars_device_category   VARCHAR(12),
  ae_start_dtm           TIMESTAMPTZ,
  matched_serial_nbr     VARCHAR(64),
  component_age_days     NUMERIC(10,2),
  facility_id            VARCHAR(20),
  facility_name          VARCHAR(120),
  pred_severity          VARCHAR(48),
  pred_severity_conf     NUMERIC(7,5),
  pred_severity_collapsed VARCHAR(16),   -- MAJOR | CRITICAL, recomputed from CODES
  actual_severity        VARCHAR(48),
  pred_component         VARCHAR(48),
  pred_component_conf    NUMERIC(7,5),
  actual_component       VARCHAR(48),
  features               JSONB,
  scored_at              TIMESTAMPTZ  DEFAULT NOW(),
  computed_date          DATE         NOT NULL,
  PRIMARY KEY (city_id, run_id, availability_event_id)
);
CREATE INDEX IF NOT EXISTS idx_ps3_inc_pred_city_dtm
  ON ps3_incident_predictions (city_id, ae_start_dtm DESC);
CREATE INDEX IF NOT EXISTS idx_ps3_inc_pred_device
  ON ps3_incident_predictions (city_id, device_id, ae_start_dtm DESC);
CREATE INDEX IF NOT EXISTS idx_ps3_inc_pred_computed
  ON ps3_incident_predictions (city_id, computed_date DESC);

-- ---------------------------------------------------------------------
-- 8. Per-device rollup - {cat}_device_predictions.csv
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS ps3_device_predictions (
  city_id                 city_code    NOT NULL REFERENCES cities(id),
  run_id                  VARCHAR(48)  NOT NULL,
  device_id               VARCHAR(40)  NOT NULL,
  mars_device_category    VARCHAR(12),
  n_incidents             INT,
  pct_critical_pred       NUMERIC(7,4),
  dominant_pred_severity  VARCHAR(48),
  dominant_pred_component VARCHAR(48),
  avg_component_age_days  NUMERIC(10,2),
  last_incident_dtm       TIMESTAMPTZ,
  computed_date           DATE         NOT NULL,
  PRIMARY KEY (city_id, run_id, device_id)
);
CREATE INDEX IF NOT EXISTS idx_ps3_dev_pred_risk
  ON ps3_device_predictions (city_id, pct_critical_pred DESC);

-- ---------------------------------------------------------------------
-- 9. Per-serial / component rollup - {cat}_serial_predictions.csv
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS ps3_serial_predictions (
  city_id                 city_code    NOT NULL REFERENCES cities(id),
  run_id                  VARCHAR(48)  NOT NULL,
  device_id               VARCHAR(40)  NOT NULL,
  matched_serial_nbr      VARCHAR(64)  NOT NULL,
  mars_device_category    VARCHAR(12),
  n_incidents             INT,
  component_age_days      NUMERIC(10,2),
  dominant_pred_component VARCHAR(48),
  pct_critical_pred       NUMERIC(7,4),
  last_incident_dtm       TIMESTAMPTZ,
  computed_date           DATE         NOT NULL,
  PRIMARY KEY (city_id, run_id, device_id, matched_serial_nbr)
);
CREATE INDEX IF NOT EXISTS idx_ps3_ser_pred_risk
  ON ps3_serial_predictions (city_id, pct_critical_pred DESC);

-- ---------------------------------------------------------------------
-- 10. Convenience view: latest run scorecard across both heads
-- ---------------------------------------------------------------------
CREATE OR REPLACE VIEW v_ps3_two_head_scorecard AS
WITH latest AS (
  SELECT city_id, run_id,
         ROW_NUMBER() OVER (PARTITION BY city_id ORDER BY run_ts DESC) AS rn
  FROM ps3_model_runs WHERE run_kind = 'train'
)
SELECT h.city_id, h.run_id, h.device_category, h.head, h.modeled,
       h.champion, h.target_col, h.n_classes, h.class_labels,
       h.test_f1_macro, h.test_auc_macro_ovr, h.test_pr_auc_macro,
       h.macro_f1_floor, h.gate_pass,
       h.n_train, h.n_test, h.n_features,
       r.run_ts, r.endpoint_name, r.serving_image, r.mlflow_version
FROM ps3_head_summary h
JOIN latest l ON l.city_id = h.city_id AND l.run_id = h.run_id AND l.rn = 1
JOIN ps3_model_runs r ON r.city_id = h.city_id AND r.run_id = h.run_id;
