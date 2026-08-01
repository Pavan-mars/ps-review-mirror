-- =====================================================================
-- CUBIC MARS Chicago — Phase-1b  PS3 FAILURE-SEVERITY tables + backfill
-- Date: 2026-07-13   Runs AFTER 01_schema_core.sql + 02_phase1_ps2_ps5_backfill.sql. Idempotent.
--
-- WHAT SHIPPED (verified honest 2026-07-13 from PS3_output_20260713_0706.html):
--   The live endpoint chicago-ps3-rootcause-v1 is a 3-class-collapsed-to-BINARY
--   FAILURE-SEVERITY classifier (failure_level_label MAJOR vs CRITICAL), NOT the
--   9-class ROOT-CAUSE model. True root cause is BLOCKED (SVN_STAGE U_FS_FAULT_CODES /
--   U_FS_ACTION_CODES = 0 rows; feasibility 70%). Champion lightgbm_multiclass:
--   test AUC-macro 0.9666 / F1-macro 0.9050 / acc 0.9083, balanced classes
--   (MAJOR 18,814 / CRITICAL 15,882 = 34,696). Leakage scan clean (top solo-AUC
--   sn_event_code_id 0.758 < 0.95, text_cols=[]). ~79% SHAP-driven by sn_event_code_id.
--
-- Deliberately named ps3_severity_* (NOT ps3_root_cause_*): the reserved
-- root_cause / ps3_root_cause_* names stay for the TRUE 9-class model when the
-- SVN_STAGE ETL is activated, so the two never get conflated.
-- =====================================================================


-- ---------- ml_models: the two columns the deferred v_executive_summary needs ----------
-- (documented in 02_*.sql NOTE; adding them now also lets PS3 register as champion)
ALTER TABLE ml_models ADD COLUMN IF NOT EXISTS is_champion          BOOLEAN      DEFAULT FALSE;
ALTER TABLE ml_models ADD COLUMN IF NOT EXISTS primary_metric_value NUMERIC(8,5);
ALTER TABLE ml_models ADD COLUMN IF NOT EXISTS primary_metric_name  VARCHAR(20);

-- ---------- PS3 severity: model scorecard (one row per city + as_of_date) ----------
CREATE TABLE IF NOT EXISTS ps3_severity_summary (
  city_id               city_code    NOT NULL REFERENCES cities(id),
  champion_model        VARCHAR(48)  NOT NULL,
  test_auc_macro        NUMERIC(6,4),
  test_f1_macro         NUMERIC(6,4),
  test_accuracy         NUMERIC(6,4),
  n_incidents           INT,
  n_major               INT,
  n_critical            INT,
  device_note           VARCHAR(120),          -- TVM-dominant; PS3 is not per-device here
  date_start            DATE,
  date_end              DATE,
  feasibility_pct       SMALLINT,              -- 70 (SVN_STAGE gap = the other 30%)
  is_root_cause         BOOLEAN      NOT NULL DEFAULT FALSE,  -- FALSE: this is SEVERITY
  true_rootcause_status VARCHAR(200),          -- why the 9-class root-cause is blocked
  dominant_feature      VARCHAR(48),           -- sn_event_code_id
  dominant_feature_shap NUMERIC(5,4),          -- 0.79 share
  endpoint_name         VARCHAR(64),
  mlflow_version        VARCHAR(16),
  sm_package            VARCHAR(64),
  serving_image         VARCHAR(160),          -- BYOC ECR image URI
  dashboard_ready       BOOLEAN      NOT NULL DEFAULT TRUE,   -- TRUE as SEVERITY (banner)
  as_of_date            DATE         NOT NULL,
  PRIMARY KEY (city_id, as_of_date)
);

-- ---------- PS3 severity: model drivers (SHAP + leakage-scan solo-AUC) ----------
CREATE TABLE IF NOT EXISTS ps3_severity_drivers (
  city_id         city_code   NOT NULL REFERENCES cities(id),
  feature         VARCHAR(48) NOT NULL,
  shap_importance NUMERIC(6,4),               -- NULL where not in the exported SHAP top set
  solo_auc        NUMERIC(6,4),               -- single-feature AUC from the leakage scan
  driver_rank     SMALLINT    NOT NULL,       -- 'rank' is reserved -> driver_rank
  as_of_date      DATE        NOT NULL,
  PRIMARY KEY (city_id, feature, as_of_date)
);

-- ---------- PS3 severity: per-incident prediction feed (SCAFFOLD — empty) ----------
-- Populated later by a batch scorer that invokes chicago-ps3-rootcause-v1. The
-- dashboard reads the summary/drivers today; predictions light up when the feed runs.
CREATE TABLE IF NOT EXISTS ps3_severity_predictions (
  id                   BIGSERIAL    PRIMARY KEY,
  city_id              city_code    NOT NULL REFERENCES cities(id),
  device_id            VARCHAR(40),
  mars_device_category VARCHAR(12),            -- 'TVM' / 'GATE'
  incident_id          VARCHAR(40),
  incident_dtm         TIMESTAMPTZ,
  sn_event_code_id     INT,
  predicted_label      VARCHAR(10),            -- MAJOR / CRITICAL
  proba_critical       NUMERIC(6,5),
  actual_label         VARCHAR(10),
  model_version        VARCHAR(16)  DEFAULT 'v8',
  scored_at            TIMESTAMPTZ  DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_ps3_sev_pred_city_dtm
  ON ps3_severity_predictions (city_id, incident_dtm DESC);

-- ===================== BACKFILL (locked 2026-07-13 numbers) =====================

-- [seed INSERT INTO ps3_severity_summary moved 2026-07-26 to manual/seed_from_03_phase1b_ps3_severity.sql -- migrate() runs on every deploy, so leaving
--  hardcoded 13-Jul metric rows here silently undid every purge.]
DELETE FROM ps3_severity_drivers WHERE city_id='CHI' AND as_of_date=DATE '2026-07-13';
-- [seed INSERT INTO ps3_severity_drivers moved 2026-07-26 to manual/seed_from_03_phase1b_ps3_severity.sql -- migrate() runs on every deploy, so leaving
--  hardcoded 13-Jul metric rows here silently undid every purge.]
-- ---------- register the PS3 champion in ml_models (feeds the future exec view) ----------
INSERT INTO ml_models
  (model_name, problem_stmt, city_id, device_type, version, algorithm,
   accuracy, f1_score, training_date, s3_artifact_uri, is_active, deployed_env,
   is_champion, primary_metric_value, primary_metric_name) VALUES
  ('ps3-root-cause-classification','PS3','CHI','tvms','v8','lightgbm_multiclass',
   0.9083, 0.9050, TIMESTAMPTZ '2026-07-13 07:01:00+00',
   '170202974600.dkr.ecr.us-east-1.amazonaws.com/cubic-pdm/mars-ps3:latest',
   TRUE, 'dev', TRUE, 0.9666, 'auc_macro')
ON CONFLICT (problem_stmt, city_id, device_type, version) DO UPDATE SET
  algorithm=EXCLUDED.algorithm, accuracy=EXCLUDED.accuracy, f1_score=EXCLUDED.f1_score,
  is_champion=EXCLUDED.is_champion, primary_metric_value=EXCLUDED.primary_metric_value,
  primary_metric_name=EXCLUDED.primary_metric_name, s3_artifact_uri=EXCLUDED.s3_artifact_uri;

