-- =====================================================================
-- 25_ps4_scored.sql  --  PS4 tables shaped to what the notebooks ACTUALLY write
-- CUBIC MARS Chicago / CTA-Ventra
--
-- DDL ONLY. Applied by action=migrate. No INSERT lives here.
--
-- ---------------------------------------------------------------------
-- WHY THIS SUPERSEDES sql/20 FOR THE SERVING PATH
-- ---------------------------------------------------------------------
-- sql/20_ps4_anomaly.sql was written on 26-Jul against an ASSUMED export layout
-- (ps4_runs / ps4_model_leaderboard / ps4_device_hourly and friends, under
-- s3://<gold>/chicago/ml_outputs/ps4/<run_id>/). The four PS4 notebooks do not
-- write that. Read from CELL 19 of the anomaly notebook and CELL 19 of each
-- clustering notebook, the real contract is:
--
--   ARTIFACTS bucket, not gold:
--     s3://cubic-mars-pm-s3-datalake-dev-artifacts-170202974600
--       chicago/ps4/scored/asof=<date>/anomalies/     parquet
--       chicago/ps4/scored/asof=<date>/timeline/      parquet
--       chicago/ps4/scored/asof=<date>/outliers/      parquet
--       chicago/ps4/manifest/asof=<date>/manifest.json   <-- SEPARATE prefix
--
--   GOLD bucket, one folder per device type:
--     chicago/ps4/clustering/{tvm,gate,validator}/asof=<date>/assignments/
--     chicago/ps4/clustering/{tvm,gate,validator}/asof=<date>/summary/
--     chicago/ps4/clustering/manifest/asof=<date>/
--
-- Two things follow that the old shape got wrong. The manifest lives OUTSIDE the
-- data folder, so an S3 notification scoped to the scored prefix would never
-- fire. And partitioning is Hive-style `asof=<date>`, not a bare date folder.
--
-- sql/20's tables are left in place -- they are referenced by routes already
-- deployed and dropping them mid-flight would 500 the tab. They simply stay
-- empty until a run writes that shape. These tables are what the loader fills.
--
-- ---------------------------------------------------------------------
-- WHY NOT REUSE ps4_anomaly_alerts (sql/01)
-- ---------------------------------------------------------------------
-- It is close but not compatible, and forcing the fit would lose data:
--   * it has triggering_signal VARCHAR(30); the notebook emits anomaly_type as a
--     '+'-joined list of every active signal, which overflows 30 chars
--   * calibration_version is NOT NULL and the notebook does not emit it
--   * device_type and severity are ENUMs; the notebook writes free text
--   * signal_active_count, asof_date and transit_day have no home at all
-- ps4_anomaly_alerts stays as the human-acknowledgement table (it carries
-- acknowledged_by / resolved_at, which a model never writes). ps4_anomalies
-- below is the model output. They are different things and stay separate.
-- =====================================================================

-- ---------------------------------------------------------------------
-- 1. Model-detected anomalies. One row per anomalous device-hour.
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS ps4_anomalies (
  city_id             city_code    NOT NULL REFERENCES cities(id),
  asof_date           DATE         NOT NULL,
  device_id           VARCHAR(40)  NOT NULL,
  device_type         VARCHAR(12),
  detected_at         TIMESTAMPTZ  NOT NULL,
  -- '+'-joined list of the signals that fired, e.g. "spc+iforest+zscore".
  -- VARCHAR(120) because the notebook concatenates every active signal name;
  -- the 30 chars ps4_anomaly_alerts allows would truncate a 3-signal event.
  anomaly_type        VARCHAR(120),
  severity            VARCHAR(12),          -- Critical | High | Medium
  anomaly_score       NUMERIC(10,6),
  signal_active_count SMALLINT,
  status              VARCHAR(16)  NOT NULL DEFAULT 'active',
  transit_day         DATE,
  facility_id         VARCHAR(24),
  facility_name       VARCHAR(120),
  if_score            NUMERIC(10,6),
  PRIMARY KEY (city_id, asof_date, device_id, detected_at)
);
CREATE INDEX IF NOT EXISTS idx_ps4_anom_dev
  ON ps4_anomalies (city_id, device_id, detected_at DESC);
CREATE INDEX IF NOT EXISTS idx_ps4_anom_sev
  ON ps4_anomalies (city_id, severity, detected_at DESC);
CREATE INDEX IF NOT EXISTS idx_ps4_anom_fac
  ON ps4_anomalies (city_id, facility_id);

-- ---------------------------------------------------------------------
-- 2. Hourly anomaly timeline, already aggregated by the notebook.
--    Grain: (asof_date, bucket_time, device_type). NOT per device -- do not
--    join it to a device table without an aggregate.
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS ps4_anomaly_timeline (
  city_id      city_code    NOT NULL REFERENCES cities(id),
  asof_date    DATE         NOT NULL,
  bucket_time  TIMESTAMPTZ  NOT NULL,
  device_type  VARCHAR(12)  NOT NULL,
  anomaly_count BIGINT,             -- the notebook's "count"
  avg_score    NUMERIC(10,6),
  PRIMARY KEY (city_id, asof_date, bucket_time, device_type)
);

-- ---------------------------------------------------------------------
-- 3. Outlier scatter feed -- x/y per device-hour for the scatter panel.
--    This is the largest of the three; the loader windows it.
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS ps4_outlier_scores (
  city_id      city_code    NOT NULL REFERENCES cities(id),
  asof_date    DATE         NOT NULL,
  device_id    VARCHAR(40)  NOT NULL,
  device_type  VARCHAR(12),
  recorded_at  TIMESTAMPTZ  NOT NULL,
  x_metric     NUMERIC(14,4),       -- event_count_hour
  y_metric     NUMERIC(10,6),       -- isolation-forest score
  z_score      NUMERIC(10,6),
  is_outlier   BOOLEAN,
  PRIMARY KEY (city_id, asof_date, device_id, recorded_at)
);
CREATE INDEX IF NOT EXISTS idx_ps4_outlier_dev
  ON ps4_outlier_scores (city_id, device_id, recorded_at DESC);

-- ---------------------------------------------------------------------
-- 4. Fault clustering -- per-device cluster assignment.
--    Three separate notebooks (TVM / GATE / VALIDATOR) write the same shape to
--    three folders, so device_type is part of the key rather than a column that
--    happens to be constant.
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS ps4_cluster_assignments (
  city_id             city_code    NOT NULL REFERENCES cities(id),
  asof_date           DATE         NOT NULL,
  device_type         VARCHAR(12)  NOT NULL,
  device_key          VARCHAR(64)  NOT NULL,
  device_id           VARCHAR(40),
  cluster_id          INT          NOT NULL,
  cluster_confidence  NUMERIC(10,6),
  champion_pipeline   VARCHAR(80),
  champion_run_id     VARCHAR(64),
  -- Silhouette travels ON the assignment row because it is the only number that
  -- says whether the clusters mean anything. A cluster id without it is a label
  -- with no evidence behind it.
  champion_silhouette NUMERIC(10,6),
  engine              VARCHAR(16),
  PRIMARY KEY (city_id, asof_date, device_type, device_key)
);
CREATE INDEX IF NOT EXISTS idx_ps4_clus_dev
  ON ps4_cluster_assignments (city_id, device_id);
CREATE INDEX IF NOT EXISTS idx_ps4_clus_cluster
  ON ps4_cluster_assignments (city_id, device_type, cluster_id);

-- ---------------------------------------------------------------------
-- 5. Fault clustering -- per-cluster summary.
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS ps4_cluster_summary (
  city_id             city_code    NOT NULL REFERENCES cities(id),
  asof_date           DATE         NOT NULL,
  device_type         VARCHAR(12)  NOT NULL,
  cluster_id          INT          NOT NULL,
  device_count        BIGINT,
  mean_target         NUMERIC(14,6),
  mean_confidence     NUMERIC(10,6),
  champion_pipeline   VARCHAR(80),
  champion_silhouette NUMERIC(10,6),
  PRIMARY KEY (city_id, asof_date, device_type, cluster_id)
);

-- ---------------------------------------------------------------------
-- Serving views. Latest as_of only, so a tab never has to know the run date.
-- ---------------------------------------------------------------------
CREATE OR REPLACE VIEW v_ps4_latest_asof AS
SELECT city_id, MAX(asof_date) AS asof_date FROM ps4_anomalies GROUP BY city_id;

CREATE OR REPLACE VIEW v_ps4_anomalies AS
SELECT a.* FROM ps4_anomalies a
JOIN v_ps4_latest_asof l ON l.city_id = a.city_id AND l.asof_date = a.asof_date;

CREATE OR REPLACE VIEW v_ps4_timeline AS
SELECT t.* FROM ps4_anomaly_timeline t
JOIN (SELECT city_id, MAX(asof_date) d FROM ps4_anomaly_timeline GROUP BY city_id) l
  ON l.city_id = t.city_id AND l.d = t.asof_date;

-- Device rollup: how many anomalous hours each device had, and how bad.
-- Counted at the anomaly grain, so nothing fans out.
CREATE OR REPLACE VIEW v_ps4_device_anomaly AS
SELECT a.city_id, a.asof_date, a.device_id, a.device_type,
       a.facility_id, a.facility_name,
       COUNT(*)                                   AS anomaly_hours,
       MAX(a.anomaly_score)                       AS max_anomaly_score,
       ROUND(AVG(a.anomaly_score), 6)             AS avg_anomaly_score,
       MAX(a.detected_at)                         AS last_detected_at,
       COUNT(*) FILTER (WHERE a.severity = 'Critical') AS n_critical,
       COUNT(*) FILTER (WHERE a.severity = 'High')     AS n_high,
       MODE() WITHIN GROUP (ORDER BY a.anomaly_type)   AS dominant_anomaly_type
FROM v_ps4_anomalies a
GROUP BY a.city_id, a.asof_date, a.device_id, a.device_type,
         a.facility_id, a.facility_name;

-- Clustering joined to anomaly load: does a cluster actually separate the
-- devices that misbehave from the ones that do not? That is the only question
-- that makes an unsupervised cluster id worth showing.
CREATE OR REPLACE VIEW v_ps4_cluster_profile AS
SELECT c.city_id, c.asof_date, c.device_type, c.cluster_id,
       COUNT(*)                                        AS n_devices,
       COUNT(d.device_id)                              AS n_devices_with_anomalies,
       ROUND(AVG(COALESCE(d.anomaly_hours, 0)), 2)     AS avg_anomaly_hours,
       ROUND(AVG(d.avg_anomaly_score), 6)              AS avg_anomaly_score,
       MAX(c.champion_silhouette)                      AS champion_silhouette,
       MAX(c.champion_pipeline)                        AS champion_pipeline
FROM ps4_cluster_assignments c
LEFT JOIN v_ps4_device_anomaly d
       ON d.city_id = c.city_id AND d.device_id = c.device_id
GROUP BY c.city_id, c.asof_date, c.device_type, c.cluster_id;
