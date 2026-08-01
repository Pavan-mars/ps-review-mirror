-- =====================================================================
-- 33_ps4_device_day.sql   28-Jul-2026
--
-- THE TABLE IS ps4_device_day, NOT ps4_device_daily.
--
-- sql/20_ps4_anomaly.sql line 123 already creates ps4_device_daily, with a
-- different shape: device_category (not device_type), anomaly_rate (not
-- is_anomaly_day), a NOT NULL run_id, and none of if_score_mean /
-- event_count_mean / severity_rank. CREATE TABLE IF NOT EXISTS is a silent no-op
-- against an existing table, so the first version of this file created nothing
-- and then failed 5 of its 8 statements on columns that did not exist.
--
-- Third time this has happened -- after ps5_reliability_estimates and
-- ps5_serial_reliability. The rule that keeps working: when an export does not
-- match an existing table, give it its OWN table rather than reshaping one that
-- something else already writes to. sql/20's ps4_device_daily is left untouched
-- and whatever reads it keeps working.
--
-- The PS4 aggregation ran. From the run console:
--
--     hourly      36,629,754  ->  device_daily  3,511,681   (9.59%)
--     anomalies    2,848,215  (device-days)
--     outliers     3,511,681  (device-days)
--     timeline         3,048  (daily x device_type)
--
-- The manifest names four rds_targets. Only TWO are loaded, and the other two are
-- not being dropped -- they are being DERIVED, because they carry no information
-- device_daily does not already hold:
--
--   anomalies  is device_daily filtered to is_anomaly_day = 1. Same grain, same
--              columns, a subset of the rows.
--   outliers   is device_daily with three columns renamed. From the notebook:
--                  x_metric = coalesce(event_count_mean, 0)
--                  y_metric = if_score_mean
--                  z_score  = if_score_mean
--              Every one of those is already a device_daily column.
--
-- Loading all four would put 9.9M rows in the serving tier to express 3.5M rows
-- of information. The views at the bottom reproduce both feeds exactly.
--
-- WHY A WINDOW, AND WHAT IS NOT LOST
-- ----------------------------------
-- 3,511,681 device-days over ~830 days is more than a 15-minute Lambda can insert
-- row-by-row through pg8000, and more than any dashboard panel reads. So the
-- detail table holds a ROLLING WINDOW (default 180 days, PS4_DAYS_BACK) and
-- ps4_device_lifetime holds a full-history rollup at device grain.
--
-- That split is not a compromise on completeness -- it matches what the two
-- questions need. "How has this device behaved lately" is a windowed question.
-- "Which devices are worst overall" is a lifetime question, and it needs one row
-- per device, not 830. The full hourly and daily history stays in S3 for
-- lake-side work.
--
-- window_days is stored on ps4_device_lifetime so a reader can never mistake a
-- windowed count for a lifetime one.
-- =====================================================================

CREATE TABLE IF NOT EXISTS ps4_device_day (
  city_id city_code NOT NULL REFERENCES cities(id),
  device_type VARCHAR(12) NOT NULL,
  device_id VARCHAR(40) NOT NULL,
  transit_day DATE NOT NULL,
  total_hours INT,
  anomaly_hours INT,
  is_anomaly_day BOOLEAN,
  signal_active_count_max INT,
  severity_rank INT,
  anomaly_score_mean NUMERIC(12,6),
  anomaly_score_max NUMERIC(12,6),
  if_score_mean NUMERIC(12,6),
  if_score_max NUMERIC(12,6),
  event_count_mean NUMERIC(14,3),
  first_detected_at TIMESTAMP,
  last_detected_at TIMESTAMP,
  anomaly_types TEXT,
  asof_date DATE NOT NULL,
  PRIMARY KEY (city_id, device_id, transit_day)
);
CREATE INDEX IF NOT EXISTS ix_ps4_dday_type_day
  ON ps4_device_day (city_id, device_type, transit_day DESC);
CREATE INDEX IF NOT EXISTS ix_ps4_dday_anom
  ON ps4_device_day (city_id, is_anomaly_day, transit_day DESC);

-- Full-history rollup, one row per device. Computed by the loader over EVERY
-- device-day in S3, not just the window, so a device that was worst two years ago
-- is still visible after it drops out of the detail table.
CREATE TABLE IF NOT EXISTS ps4_device_lifetime (
  city_id city_code NOT NULL REFERENCES cities(id),
  device_type VARCHAR(12) NOT NULL,
  device_id VARCHAR(40) NOT NULL,
  n_days INT,
  total_hours BIGINT,
  anomaly_hours BIGINT,
  n_anomaly_days INT,
  -- Named flagged_rate, NOT anomaly_rate. The label is
  -- ensemble_anomaly_flag = (signal_active_count >= 2), a deterministic function
  -- of the signal_* features, with a 47.96% base rate. Calling it an anomaly rate
  -- would assert a validation the run does not support.
  flagged_rate NUMERIC(7,4),
  anomaly_score_max NUMERIC(12,6),
  if_score_max NUMERIC(12,6),
  first_day DATE,
  last_day DATE,
  window_days INT,          -- what the DETAIL table holds; lifetime ignores it
  asof_date DATE NOT NULL,
  PRIMARY KEY (city_id, device_id)
);
CREATE INDEX IF NOT EXISTS ix_ps4_life_rate
  ON ps4_device_lifetime (city_id, device_type, flagged_rate DESC);

-- ---------------------------------------------------------------------------
-- The two feeds we chose not to store, reproduced exactly.
-- ---------------------------------------------------------------------------

-- `anomalies`: device_daily where the day was flagged.
CREATE OR REPLACE VIEW v_ps4_day_anomalies AS
SELECT
  d.city_id, d.device_type, d.device_id, d.transit_day,
  d.anomaly_hours, d.total_hours, d.signal_active_count_max, d.severity_rank,
  d.anomaly_score_max        AS anomaly_score,
  d.anomaly_types,
  COALESCE(d.last_detected_at, d.transit_day::timestamp) AS detected_at,
  TRUE                       AS is_outlier,
  'active'::varchar(12)      AS status,
  d.asof_date
FROM ps4_device_day d
WHERE d.is_anomaly_day IS TRUE;

-- `outliers`: the scatter feed. Columns renamed to match the export exactly, so
-- anything written against the S3 schema works unchanged against this view.
CREATE OR REPLACE VIEW v_ps4_outlier_scores AS
SELECT
  d.city_id, d.device_type, d.device_id,
  d.transit_day,
  d.transit_day::timestamp             AS bucket_time,
  d.transit_day::timestamp             AS recorded_at,
  COALESCE(d.event_count_mean, 0)      AS x_metric,
  d.if_score_mean                      AS y_metric,
  d.if_score_mean                      AS z_score,
  d.is_anomaly_day,
  d.asof_date
FROM ps4_device_day d;

-- Serving view for the device panel: window behaviour beside lifetime standing,
-- so a device cannot look calm simply because its worst period fell outside the
-- window.
CREATE OR REPLACE VIEW v_ps4_device_status AS
SELECT
  l.city_id, l.device_type, l.device_id,
  l.n_days, l.anomaly_hours, l.total_hours, l.flagged_rate,
  l.n_anomaly_days, l.first_day, l.last_day, l.window_days, l.asof_date,
  w.window_days_present, w.window_anomaly_days, w.window_flagged_rate,
  w.last_flagged_day,
  RANK() OVER (PARTITION BY l.city_id, l.device_type
               ORDER BY l.flagged_rate DESC NULLS LAST) AS rank_in_type
FROM ps4_device_lifetime l
LEFT JOIN (
  SELECT city_id, device_id,
         COUNT(*)                                            AS window_days_present,
         COUNT(*) FILTER (WHERE is_anomaly_day)              AS window_anomaly_days,
         ROUND(SUM(anomaly_hours)::numeric
               / NULLIF(SUM(total_hours), 0), 4)             AS window_flagged_rate,
         MAX(transit_day) FILTER (WHERE is_anomaly_day)      AS last_flagged_day
  FROM ps4_device_day GROUP BY city_id, device_id
) w ON w.city_id = l.city_id AND w.device_id = l.device_id;
