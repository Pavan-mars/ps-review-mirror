-- =====================================================================
-- 20_ps4_anomaly.sql  --  PS4 anomaly detection serving schema
-- CUBIC MARS Chicago / CTA-Ventra
--
-- Applied by the dashboard-api `action=migrate` from CloudShell, like every
-- other numbered file. DDL ONLY -- there is not a single INSERT in this file.
-- migrate() runs on every deploy, so a data INSERT here would refill the tables
-- behind any purge. PS4 run data arrives via the cubic-mars-ps4-rds-loader
-- Lambda, triggered by S3 or EventBridge.
--
-- GRAIN, AND WHY IT IS NOT THE FULL HOURLY FEED
--   The PS4 gold table device_ps4_hourly holds 36,629,754 device-hours over
--   2023-07-01..2026-04-11. Loading that into Aurora would be a ~37M-row table
--   serving a dashboard that never asks a question below day grain. So:
--     ps4_device_daily   - device x day rollup, the panel grain (all history)
--     ps4_device_hourly  - device x hour, RECENT WINDOW ONLY (default 7 days),
--                          for the "what happened last night" drill-down
--   The loader records the retained window on ps4_runs so the cut is visible
--   rather than implied.
--
-- SELF-HEAL
--   CREATE TABLE IF NOT EXISTS is a silent no-op when a table of the same name
--   exists with a different shape -- that is how ps3_incident_predictions spent
--   weeks "migrating successfully" while doing nothing. The database already
--   holds ps4_anomaly_alerts / ps4_anomaly_predictions / ps4_anomaly_rate_forecast
--   / ps4_anomaly_signal_detail from an earlier bundle whose DDL is not in this
--   repo. The tables below are deliberately NEW names so they cannot collide,
--   and each still carries the city_id guard used in sql/15.
-- =====================================================================

-- ---------------------------------------------------------------------
-- 1. Run lineage. One row per PS4 training or scoring run.
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS ps4_runs (
  city_id             city_code    NOT NULL REFERENCES cities(id),
  run_id              VARCHAR(48)  NOT NULL,
  run_ts              TIMESTAMPTZ  NOT NULL,
  run_kind            VARCHAR(16)  NOT NULL DEFAULT 'train',   -- train | batch_score
  source_notebook     VARCHAR(160),
  gold_snapshot_s3    VARCHAR(300),
  s3_run_prefix       VARCHAR(300),          -- where the loader read this run from
  champion            VARCHAR(48),
  target_col          VARCHAR(48),           -- e.g. is_anomaly_hour
  n_rows_total        BIGINT,
  n_rows_train        BIGINT,
  n_rows_val          BIGINT,
  n_rows_test         BIGINT,
  n_features          INT,
  target_rate         NUMERIC(7,4),          -- base rate; AP must be read against it
  data_start          TIMESTAMPTZ,
  data_end            TIMESTAMPTZ,
  hourly_window_days  INT,                   -- how much hourly detail was retained
  leakage_checked     BOOLEAN DEFAULT FALSE, -- see notes; PS4 AP ~0.999 needs a scan
  notes               TEXT,
  computed_date       DATE         NOT NULL,
  PRIMARY KEY (city_id, run_id)
);
CREATE INDEX IF NOT EXISTS idx_ps4_runs_ts ON ps4_runs (city_id, run_ts DESC);

-- ---------------------------------------------------------------------
-- 2. Model bake-off. base_rate is stored ON the leaderboard so average
--    precision is never read without the number it has to beat.
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS ps4_model_leaderboard (
  city_id        city_code    NOT NULL REFERENCES cities(id),
  run_id         VARCHAR(48)  NOT NULL,
  model          VARCHAR(48)  NOT NULL,
  val_ap         NUMERIC(7,4),
  test_ap        NUMERIC(7,4),
  val_auc        NUMERIC(7,4),
  test_auc       NUMERIC(7,4),
  test_f1        NUMERIC(7,4),
  test_prec      NUMERIC(7,4),
  test_rec       NUMERIC(7,4),
  test_accuracy  NUMERIC(7,4),
  base_rate      NUMERIC(7,4),
  ap_lift_over_base NUMERIC(8,4) GENERATED ALWAYS AS (test_ap - base_rate) STORED,
  fit_s          NUMERIC(10,3),
  lb_rank        SMALLINT,
  is_champion    BOOLEAN      DEFAULT FALSE,
  note           VARCHAR(200),
  as_of_date     DATE         NOT NULL,
  PRIMARY KEY (city_id, run_id, model)
);

-- ---------------------------------------------------------------------
-- 3. SPC control limits per device type (the Signal-1 detector).
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS ps4_spc_thresholds (
  city_id         city_code    NOT NULL REFERENCES cities(id),
  run_id          VARCHAR(48)  NOT NULL,
  device_category VARCHAR(12)  NOT NULL,
  mean_events     NUMERIC(12,4),
  ucl             NUMERIC(12,4),
  lcl             NUMERIC(12,4),
  violation_rate  NUMERIC(7,4),
  as_of_date      DATE         NOT NULL,
  PRIMARY KEY (city_id, run_id, device_category)
);

-- ---------------------------------------------------------------------
-- 4. Signal activation summary -- one row per (detector, device type, split).
--    PS4 runs several detectors (SPC, PCA reconstruction, Isolation Forest);
--    keeping them in one narrow table means a new detector needs no DDL.
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS ps4_signal_summary (
  city_id         city_code    NOT NULL REFERENCES cities(id),
  run_id          VARCHAR(48)  NOT NULL,
  signal_name     VARCHAR(40)  NOT NULL,   -- spc | pca_if | ...
  device_category VARCHAR(12)  NOT NULL,
  split_name      VARCHAR(12)  NOT NULL,   -- train | val | test | all
  activation_rate NUMERIC(7,4),
  n_hours         BIGINT,
  n_active        BIGINT,
  threshold_used  NUMERIC(12,6),
  as_of_date      DATE         NOT NULL,
  PRIMARY KEY (city_id, run_id, signal_name, device_category, split_name)
);

-- ---------------------------------------------------------------------
-- 5. Device x day rollup -- the panel grain, full history.
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS ps4_device_daily (
  city_id            city_code    NOT NULL REFERENCES cities(id),
  run_id             VARCHAR(48)  NOT NULL,
  device_id          VARCHAR(40)  NOT NULL,
  transit_day        DATE         NOT NULL,
  device_category    VARCHAR(12),
  facility_id        VARCHAR(20),
  total_hours        INT,
  anomaly_hours      INT,
  anomaly_rate       NUMERIC(7,4),
  spc_violation_hours INT,
  if_anomaly_hours   INT,
  max_anomaly_score  NUMERIC(9,6),
  avg_anomaly_score  NUMERIC(9,6),
  computed_date      DATE         NOT NULL,
  PRIMARY KEY (city_id, run_id, device_id, transit_day)
);
CREATE INDEX IF NOT EXISTS idx_ps4_dev_daily_day
  ON ps4_device_daily (city_id, transit_day DESC, anomaly_rate DESC);
CREATE INDEX IF NOT EXISTS idx_ps4_dev_daily_dev
  ON ps4_device_daily (city_id, device_id, transit_day DESC);

-- ---------------------------------------------------------------------
-- 6. Device x hour -- RECENT WINDOW ONLY. See the header note.
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS ps4_device_hourly (
  city_id         city_code    NOT NULL REFERENCES cities(id),
  run_id          VARCHAR(48)  NOT NULL,
  device_id       VARCHAR(40)  NOT NULL,
  hour_bucket     TIMESTAMPTZ  NOT NULL,
  device_category VARCHAR(12),
  facility_id     VARCHAR(20),
  event_count     INT,
  anomaly_score   NUMERIC(9,6),
  is_anomaly      BOOLEAN,
  spc_violation   BOOLEAN,
  if_anomaly      BOOLEAN,
  computed_date   DATE         NOT NULL,
  PRIMARY KEY (city_id, run_id, device_id, hour_bucket)
);
CREATE INDEX IF NOT EXISTS idx_ps4_dev_hourly_time
  ON ps4_device_hourly (city_id, hour_bucket DESC);

-- ---------------------------------------------------------------------
-- 7. Station rollup. facility_id joins dim_device_station for the name.
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS ps4_station_anomaly (
  city_id       city_code    NOT NULL REFERENCES cities(id),
  run_id        VARCHAR(48)  NOT NULL,
  facility_id   VARCHAR(20)  NOT NULL,
  device_category VARCHAR(12) NOT NULL DEFAULT 'ALL',
  n_devices     INT,
  total_hours   BIGINT,
  anomaly_hours BIGINT,
  anomaly_rate  NUMERIC(7,4),
  as_of_date    DATE         NOT NULL,
  PRIMARY KEY (city_id, run_id, facility_id, device_category)
);
CREATE INDEX IF NOT EXISTS idx_ps4_station_rate
  ON ps4_station_anomaly (city_id, anomaly_rate DESC);

-- ---------------------------------------------------------------------
-- 8. Leakage scan. PS4 reported test AP 0.9994 against a 0.4796 base rate.
--    A feature whose solo AUC is near 1.0 IS the label wearing another name.
--    Same gate as ps3_leakage_scan.
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS ps4_leakage_scan (
  city_id      city_code    NOT NULL REFERENCES cities(id),
  run_id       VARCHAR(48)  NOT NULL,
  feature_name VARCHAR(80)  NOT NULL,
  solo_auc     NUMERIC(7,4),
  flagged      BOOLEAN      GENERATED ALWAYS AS (solo_auc > 0.95) STORED,
  as_of_date   DATE         NOT NULL,
  PRIMARY KEY (city_id, run_id, feature_name)
);

-- ---------------------------------------------------------------------
-- 9. Latest-run convenience views, mirroring the PS3 pattern.
-- ---------------------------------------------------------------------
CREATE OR REPLACE VIEW v_ps4_latest_run AS
SELECT city_id, run_id, run_ts, target_rate, leakage_checked
FROM (SELECT city_id, run_id, run_ts, target_rate, leakage_checked,
             ROW_NUMBER() OVER (PARTITION BY city_id ORDER BY run_ts DESC) rn
      FROM ps4_runs) t
WHERE rn = 1;

-- Leaderboard with the honest reading attached: a model whose AP barely clears
-- the base rate is not skilful, and one whose AP is ~1.0 on a balanced target is
-- suspicious until the leakage scan says otherwise.
CREATE OR REPLACE VIEW v_ps4_leaderboard AS
SELECT l.city_id, l.run_id, l.model, l.val_ap, l.test_ap, l.test_auc,
       l.test_accuracy, l.base_rate, l.ap_lift_over_base,
       l.lb_rank, l.is_champion,
       CASE
         WHEN l.base_rate IS NULL                       THEN 'unknown_base_rate'
         WHEN l.test_ap <= l.base_rate + 0.02           THEN 'no_better_than_base'
         WHEN l.test_ap >= 0.99 AND r.leakage_checked IS NOT TRUE
                                                        THEN 'suspiciously_high_unverified'
         ELSE 'ok'
       END AS verdict
FROM ps4_model_leaderboard l
JOIN v_ps4_latest_run r ON r.city_id = l.city_id AND r.run_id = l.run_id;
