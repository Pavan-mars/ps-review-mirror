-- =====================================================================
-- CUBIC MARS Chicago - Phase-2b  PS1 daily-batch lineage + serial grain
-- Date: 2026-07-26.  Runs AFTER 01..15.  Idempotent.  ADDITIVE ONLY.
--
-- WHY THIS EXISTS
--   Migrations 04 + 11 already give PS1 13 tables and 17 API routes, covering
--   training scorecards, leaderboard, SHAP, calibration, confusion, threshold
--   sweep, risk bands, station rollup and a per-prediction feed. Three things
--   are missing for a DAILY BATCH pipeline:
--     (a) run lineage - nothing records WHICH endpoint / image / model version
--         produced a given day's rows, nor whether the nightly batch succeeded.
--         Without it you cannot answer "why did yesterday's numbers change".
--     (b) serial / component grain - the PS1 engine attributes device-level risk
--         down to components via silver.hw_config_current, but there is nowhere
--         to land it (PS2/PS3 both already have serial grain; PS1 did not).
--     (c) the promoted-label provenance. As of R7-1 (2026-07-24,
--         sql/gold/device_ps1_daily__create.sql) will_fail_3d was REDEFINED from
--         chargeable-only to any-hardware-OOS for TVM/GATE, aligning all three
--         categories with VALIDATOR's R6-1 definition. ps1_model_performance had
--         no target column at all, so a dashboard reading it cannot tell which
--         definition a number was computed under. That is now explicit.
--
-- v7.0 rule respected: PS1 is gated on AP/PR-AUC + a recall floor (TVM 0.80 /
-- GATE 0.70), never raw accuracy. recall_floor and gate_pass travel with the row.
-- =====================================================================

-- ---------------------------------------------------------------------
-- 1. Daily batch / inference run registry (shared shape with ps3_model_runs)
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS ps1_inference_runs (
  city_id           city_code    NOT NULL REFERENCES cities(id),
  run_id            VARCHAR(48)  NOT NULL,
  run_ts            TIMESTAMPTZ  NOT NULL,
  run_kind          VARCHAR(16)  NOT NULL DEFAULT 'batch_score', -- train | batch_score
  device_category   VARCHAR(12)  NOT NULL,
  scoring_date      DATE,                      -- the business date scored
  endpoint_name     VARCHAR(80),
  serving_image     VARCHAR(200),
  model_version     VARCHAR(24),
  mlflow_version    VARCHAR(16),
  target_col        VARCHAR(32),               -- will_hardware_oos_3d (R7-1 aligned)
  decision_threshold NUMERIC(7,5),
  n_devices_scored  INT,
  n_flagged         INT,
  gold_snapshot_s3  VARCHAR(300),              -- exact S3 prefix the features came from
  status            VARCHAR(16)  NOT NULL DEFAULT 'running', -- running|success|failed
  error_text        TEXT,
  duration_s        NUMERIC(10,2),
  computed_date     DATE         NOT NULL,
  PRIMARY KEY (city_id, run_id, device_category)
);
CREATE INDEX IF NOT EXISTS idx_ps1_runs_city_ts   ON ps1_inference_runs (city_id, run_ts DESC);
CREATE INDEX IF NOT EXISTS idx_ps1_runs_status    ON ps1_inference_runs (city_id, status, run_ts DESC);

-- ---------------------------------------------------------------------
-- 2. Lineage + risk band on the existing per-prediction feed
--    (ALTER ... ADD COLUMN IF NOT EXISTS is a no-op on re-run)
-- ---------------------------------------------------------------------
ALTER TABLE ps1_failure_predictions ADD COLUMN IF NOT EXISTS run_id             VARCHAR(48);
ALTER TABLE ps1_failure_predictions ADD COLUMN IF NOT EXISTS model_version      VARCHAR(24);
ALTER TABLE ps1_failure_predictions ADD COLUMN IF NOT EXISTS target_col         VARCHAR(32);
ALTER TABLE ps1_failure_predictions ADD COLUMN IF NOT EXISTS risk_band          VARCHAR(10);
ALTER TABLE ps1_failure_predictions ADD COLUMN IF NOT EXISTS matched_serial_nbr VARCHAR(64);
CREATE INDEX IF NOT EXISTS idx_ps1_pred_run   ON ps1_failure_predictions (city_id, run_id);
CREATE INDEX IF NOT EXISTS idx_ps1_pred_risk
  ON ps1_failure_predictions (city_id, computed_date DESC, failure_probability DESC);

-- ---------------------------------------------------------------------
-- 3. Label provenance + the recall-floor gate on the performance table
-- ---------------------------------------------------------------------
ALTER TABLE ps1_model_performance ADD COLUMN IF NOT EXISTS target_col     VARCHAR(32);
ALTER TABLE ps1_model_performance ADD COLUMN IF NOT EXISTS label_revision VARCHAR(16);  -- e.g. 'R7-1'
ALTER TABLE ps1_model_performance ADD COLUMN IF NOT EXISTS recall_floor   NUMERIC(6,4);
ALTER TABLE ps1_model_performance ADD COLUMN IF NOT EXISTS base_rate_pct  NUMERIC(6,2);
ALTER TABLE ps1_model_performance ADD COLUMN IF NOT EXISTS run_id         VARCHAR(48);

ALTER TABLE ps1_failure_summary   ADD COLUMN IF NOT EXISTS label_revision VARCHAR(16);

-- ---------------------------------------------------------------------
-- 4. PS1 serial / component grain
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS ps1_serial_predictions (
  city_id              city_code    NOT NULL REFERENCES cities(id),
  run_id               VARCHAR(48)  NOT NULL,
  device_id            VARCHAR(40)  NOT NULL,
  matched_serial_nbr   VARCHAR(64)  NOT NULL,
  device_category      VARCHAR(12),
  component_type       VARCHAR(48),
  component_age_days   NUMERIC(10,2),
  device_failure_probability NUMERIC(7,5),  -- inherited from the device-level model
  attribution_weight   NUMERIC(7,5),        -- share of device risk attributed here
  serial_risk_score    NUMERIC(7,5),        -- device_failure_probability * attribution_weight
  risk_band            VARCHAR(10),
  prediction_date      DATE,
  computed_date        DATE         NOT NULL,
  PRIMARY KEY (city_id, run_id, device_id, matched_serial_nbr)
);
CREATE INDEX IF NOT EXISTS idx_ps1_serial_risk
  ON ps1_serial_predictions (city_id, computed_date DESC, serial_risk_score DESC);

-- ---------------------------------------------------------------------
-- 5. Batch load audit - what the rds-push Lambda did, incl. skipped columns
--    (auto-schema pattern: ADD COLUMN IF NOT EXISTS, never auto-ALTER TYPE;
--     a type conflict is skipped and recorded here rather than silently cast)
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS ml_batch_load_audit (
  id             BIGSERIAL    PRIMARY KEY,
  city_id        city_code    NOT NULL REFERENCES cities(id),
  ps_id          VARCHAR(8)   NOT NULL,        -- 'PS1' | 'PS3'
  run_id         VARCHAR(48),
  target_table   VARCHAR(64)  NOT NULL,
  s3_source      VARCHAR(300),
  rows_read      INT,
  rows_loaded    INT,
  columns_added  TEXT,                         -- comma list of ADDed columns
  columns_skipped TEXT,                        -- type-conflict columns, NOT cast
  status         VARCHAR(16)  NOT NULL,        -- success | partial | failed
  error_text     TEXT,
  loaded_at      TIMESTAMPTZ  DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_ml_load_audit_recent
  ON ml_batch_load_audit (city_id, ps_id, loaded_at DESC);

-- ---------------------------------------------------------------------
-- 6. Freshness view - is the nightly batch actually running?
--    Drives a dashboard staleness banner instead of silently showing old rows.
-- ---------------------------------------------------------------------
CREATE OR REPLACE VIEW v_ml_batch_freshness AS
SELECT 'PS1'::VARCHAR(8) AS ps_id, city_id, device_category,
       MAX(run_ts)                                   AS last_run_ts,
       MAX(scoring_date)                             AS last_scoring_date,
       (NOW() - MAX(run_ts)) > INTERVAL '36 hours'   AS is_stale,
       COUNT(*) FILTER (WHERE status = 'failed')     AS failed_runs
FROM ps1_inference_runs
GROUP BY city_id, device_category
UNION ALL
SELECT 'PS3'::VARCHAR(8), city_id, NULL::VARCHAR(12),
       MAX(run_ts), NULL::DATE,
       (NOW() - MAX(run_ts)) > INTERVAL '36 hours',
       0
FROM ps3_model_runs
GROUP BY city_id;
