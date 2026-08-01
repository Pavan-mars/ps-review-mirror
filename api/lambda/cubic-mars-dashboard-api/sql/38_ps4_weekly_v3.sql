-- =====================================================================
-- 38_ps4_weekly_v3.sql                                       29-Jul-2026
--
-- PS4 WEEKLY ANOMALY / CLUSTERING -- v3 PIPELINE SERVING TABLES
--
-- Source of every column below: the run of
--   PS4_SageMaker_Anomaly_Clustering_Weekly_RDS_v3_gp_download1.ipynb
--   run_id ps4-20260728T213153Z-bf4609d4
-- reading 14,817,096 device-hour rows and publishing to
--   s3://cubic-mars-pm-s3-datalake-dev-artifacts-170202974600/chicago/ps4/v3
--
-- NOTHING HERE TOUCHES THE EXISTING PS4 TABLES.
-- ps4_device_day, ps4_device_lifetime, ps4_anomaly_timeline, ps4_anomalies,
-- ps4_cluster_assignments, ps4_cluster_summary, ps4_outlier_scores,
-- ps4_anomaly_alerts, ps4_runs, ps4_signal_summary, ps4_spc_thresholds,
-- ps4_device_daily, ps4_device_hourly, ps4_station_anomaly, ps4_leakage_scan
-- are all left exactly as they are. They remain the Plan B feed. Every object
-- in this file is a NEW name, so a rollback is "stop calling the /ps4/weekly*
-- routes" -- no restore, no re-load.
--
-- WHY NEW TABLES RATHER THAN REUSING ps4_cluster_summary.
-- The v3 grain is (device, device_type, WEEK, pipeline_version). The old tables
-- are day-grain and as-of-grain. CREATE TABLE IF NOT EXISTS against an existing
-- table is a SILENT NO-OP in Postgres -- it does not add the missing columns and
-- it does not report anything. That has bitten this programme four times. The
-- rule adopted after the fourth is: an export gets its OWN table.
--
-- PRIMARY KEY. The notebook publishes
--   "rds_upsert_key": ["city_id","device_id","device_type","week_start",
--                      "pipeline_version"]
-- and that tuple is used verbatim below. pipeline_version is IN the key on
-- purpose: it lets two pipeline versions sit side by side for comparison
-- without either overwriting the other, which is exactly what a "does the new
-- one beat the old one" conversation needs.
--
-- Nothing in this file DELETEs. The loader owns deletion, scoped to
-- (city_id, pipeline_version), so a reload never touches another version.
-- =====================================================================


-- ---------------------------------------------------------------------------
-- 1. WEEKLY DEVICE SUMMARY -- one row per device per week.
--
-- 7,742 rows in the reference run. Column meanings, taken from the notebook's
-- aggregation block and NOT invented here:
--
--   observed_days / observed_hours   how much data the week actually had.
--                                    A week with 2 observed days is not a
--                                    quiet week, it is a week with no data,
--                                    and has_low_coverage_day says so.
--   candidate_days                   device-days the detector flagged at all.
--   actionable_days                  candidate days that survived the
--                                    persistence / severity gate.
--   is_actionable_week               1 when actionable_days > 0. This is the
--                                    field the alerts feed filters on.
--   anomaly_score_max/mean/p95       distribution of the score across the week,
--                                    kept in all three forms because a single
--                                    max hides whether it was one spike or a
--                                    sustained shift.
--   max_abs_z                        largest absolute z on any signal.
--   cluster_distance_ratio_max       distance to the assigned cluster centroid
--                                    divided by that cluster's training p99.
--                                    > 1 means the device sat further out than
--                                    99% of the training population -- that is
--                                    the actual "this does not look like
--                                    anything we trained on" number.
--   dominant_cluster_id              modal cluster over the week.
--   severity                         Normal / High / Critical, as the notebook
--                                    labels it. Stored as text, not an enum,
--                                    so a new level does not require a
--                                    migration.
--   anomaly_types                    which signal families fired.
--
-- HONEST FRAMING, ENFORCED BY NAMING. There is no "failure_probability" column
-- here and there must never be one. PS4 is unsupervised: it says a device is
-- unlike its peers, which is not the same claim as "it will fail". PS1 owns
-- failure probability. Keeping the two vocabularies separate in the schema is
-- what stops them merging on a slide.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS ps4_weekly_device_summary (
  city_id                      TEXT        NOT NULL,
  device_id                    TEXT        NOT NULL,
  device_type                  TEXT        NOT NULL,
  week_start                   DATE        NOT NULL,
  pipeline_version             TEXT        NOT NULL,
  week_end                     DATE,
  facility_id                  TEXT,
  observed_days                INTEGER,
  observed_hours               INTEGER,
  candidate_days               INTEGER,
  actionable_days              INTEGER,
  is_actionable_week           SMALLINT,
  anomaly_score_max            DOUBLE PRECISION,
  anomaly_score_mean           DOUBLE PRECISION,
  anomaly_score_p95            DOUBLE PRECISION,
  max_abs_z                    DOUBLE PRECISION,
  cluster_distance_ratio_max   DOUBLE PRECISION,
  has_low_coverage_day         SMALLINT,
  dominant_cluster_id          INTEGER,
  anomaly_types                TEXT,
  severity                     TEXT,
  asof_date                    DATE,
  run_id                       TEXT,
  loaded_at                    TIMESTAMPTZ DEFAULT NOW(),
  PRIMARY KEY (city_id, device_id, device_type, week_start, pipeline_version)
);

CREATE INDEX IF NOT EXISTS ix_ps4_wds_week
  ON ps4_weekly_device_summary (city_id, week_start DESC, device_type);
CREATE INDEX IF NOT EXISTS ix_ps4_wds_actionable
  ON ps4_weekly_device_summary (city_id, is_actionable_week, week_start DESC);
CREATE INDEX IF NOT EXISTS ix_ps4_wds_device
  ON ps4_weekly_device_summary (city_id, device_id, week_start DESC);


-- ---------------------------------------------------------------------------
-- 2. WEEKLY ALERTS -- the actionable subset, 1,108 rows in the reference run.
--
-- Identical schema to the summary by design: it IS the same rows filtered to
-- is_actionable_week = 1. Stored separately rather than served as a view over
-- the summary because the notebook publishes it as its own dataset, and the two
-- must be reconcilable against each other. v_ps4_weekly_alert_reconcile below
-- makes any divergence visible instead of hiding it.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS ps4_weekly_alerts (
  city_id                      TEXT        NOT NULL,
  device_id                    TEXT        NOT NULL,
  device_type                  TEXT        NOT NULL,
  week_start                   DATE        NOT NULL,
  pipeline_version             TEXT        NOT NULL,
  week_end                     DATE,
  facility_id                  TEXT,
  observed_days                INTEGER,
  observed_hours               INTEGER,
  candidate_days               INTEGER,
  actionable_days              INTEGER,
  is_actionable_week           SMALLINT,
  anomaly_score_max            DOUBLE PRECISION,
  anomaly_score_mean           DOUBLE PRECISION,
  anomaly_score_p95            DOUBLE PRECISION,
  max_abs_z                    DOUBLE PRECISION,
  cluster_distance_ratio_max   DOUBLE PRECISION,
  has_low_coverage_day         SMALLINT,
  dominant_cluster_id          INTEGER,
  anomaly_types                TEXT,
  severity                     TEXT,
  asof_date                    DATE,
  run_id                       TEXT,
  loaded_at                    TIMESTAMPTZ DEFAULT NOW(),
  PRIMARY KEY (city_id, device_id, device_type, week_start, pipeline_version)
);

CREATE INDEX IF NOT EXISTS ix_ps4_wa_week
  ON ps4_weekly_alerts (city_id, week_start DESC, severity);


-- ---------------------------------------------------------------------------
-- 3. WEEKLY TIMELINE -- fleet rollup, one row per (device_type, week).
--
-- devices_observed is the DENOMINATOR. Without it, actionable_devices is an
-- unreadable count: 40 actionable out of 60 observed and 40 out of 2,000 are
-- the same number and opposite situations.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS ps4_weekly_timeline (
  city_id                TEXT        NOT NULL,
  device_type            TEXT        NOT NULL,
  week_start             DATE        NOT NULL,
  pipeline_version       TEXT        NOT NULL,
  week_end               DATE,
  devices_observed       INTEGER,
  actionable_devices     INTEGER,
  candidate_device_days  INTEGER,
  mean_anomaly_score     DOUBLE PRECISION,
  max_anomaly_score      DOUBLE PRECISION,
  asof_date              DATE,
  run_id                 TEXT,
  loaded_at              TIMESTAMPTZ DEFAULT NOW(),
  PRIMARY KEY (city_id, device_type, week_start, pipeline_version)
);


-- ---------------------------------------------------------------------------
-- 4. CLUSTER PROFILE -- one row per (device_type, cluster).
--
--   train_cluster_share        what fraction of the TRAINING population landed
--                              in this cluster. A cluster holding 2% of
--                              training is a niche behaviour; one holding 60%
--                              is "normal".
--   train_cluster_distance_p99 the distance cut that
--                              cluster_distance_ratio_max is measured against.
--                              Shipping it means the ratio can be audited on
--                              screen rather than trusted.
--   candidate_rate /           how often devices in this cluster get flagged.
--   actionable_rate            A cluster with a high actionable_rate is the
--                              interesting one -- that is the cluster that
--                              earns its keep.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS ps4_cluster_profile (
  city_id                     TEXT        NOT NULL,
  device_type                 TEXT        NOT NULL,
  cluster_id                  INTEGER     NOT NULL,
  pipeline_version            TEXT        NOT NULL,
  scored_device_days          BIGINT,
  candidate_rate              DOUBLE PRECISION,
  actionable_rate             DOUBLE PRECISION,
  mean_cluster_distance       DOUBLE PRECISION,
  train_cluster_distance_p99  DOUBLE PRECISION,
  train_cluster_share         DOUBLE PRECISION,
  asof_date                   DATE,
  run_id                      TEXT,
  loaded_at                   TIMESTAMPTZ DEFAULT NOW(),
  PRIMARY KEY (city_id, device_type, cluster_id, pipeline_version)
);


-- ---------------------------------------------------------------------------
-- 5. CLUSTER QUALITY -- silhouette, k, and where the number came from.
--
-- This table exists because a cluster label with no separation metric is a
-- decoration. Silhouette is the only evidence that "cluster 2" means anything:
--   > 0.50   well separated
--   0.25-0.50 weak but real structure
--   < 0.25   not separable -- do not name the clusters, do not act on them
--
-- The reference run measured, from its own selection log:
--   GATE       k=3  silhouette 0.545102   293,033 training rows
--   TVM        k=5  silhouette 0.528693   157,160 training rows
--   VALIDATOR  k=3  silhouette 0.458884   830,050 training rows
--
-- quality_source is NOT decoration either. If the manifest publishes silhouette
-- the loader writes 'manifest'; if it does not, the loader writes the three
-- measured values above with source 'run_log', and the dashboard prints that
-- word next to the number. A figure whose provenance is on screen can be
-- challenged; one without it gets believed by default, which is worse.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS ps4_cluster_quality (
  city_id            TEXT        NOT NULL,
  device_type        TEXT        NOT NULL,
  pipeline_version   TEXT        NOT NULL,
  k_selected         INTEGER,
  silhouette         DOUBLE PRECISION,
  train_rows         BIGINT,
  quality_source     TEXT,
  asof_date          DATE,
  run_id             TEXT,
  loaded_at          TIMESTAMPTZ DEFAULT NOW(),
  PRIMARY KEY (city_id, device_type, pipeline_version)
);


-- ---------------------------------------------------------------------------
-- 6. RUN REGISTRY -- what the loader read, and from where.
--
-- One row per (city, pipeline_version, run). The manifest key and READY key are
-- stored verbatim so "which S3 object is this screen showing" is answerable
-- without opening the console. The 28-Jul PS1 incident was invisible for hours
-- precisely because nothing recorded where the data came from.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS ps4_v3_runs (
  city_id           TEXT        NOT NULL,
  pipeline_version  TEXT        NOT NULL,
  run_id            TEXT        NOT NULL,
  asof_date         DATE,
  manifest_key      TEXT,
  ready_key         TEXT,
  source_rows       BIGINT,
  rows_summary      INTEGER,
  rows_alerts       INTEGER,
  rows_timeline     INTEGER,
  rows_cluster      INTEGER,
  loaded_at         TIMESTAMPTZ DEFAULT NOW(),
  PRIMARY KEY (city_id, pipeline_version, run_id)
);


-- ---------------------------------------------------------------------------
-- VIEWS
-- ---------------------------------------------------------------------------

-- The newest pipeline_version present, per city. Every serving view below
-- resolves through this rather than hardcoding a version string, so loading a
-- new version switches the dashboard over with no code change -- and rolling
-- back is a DELETE of the newer version, nothing else.
CREATE OR REPLACE VIEW v_ps4_v3_current AS
SELECT DISTINCT ON (city_id)
       city_id, pipeline_version, run_id, asof_date,
       manifest_key, source_rows,
       rows_summary, rows_alerts, rows_timeline, rows_cluster
FROM ps4_v3_runs
ORDER BY city_id, loaded_at DESC;

-- Cluster quality with the interpretation attached in SQL, so the front end
-- cannot invent its own bands and drift from this file.
CREATE OR REPLACE VIEW v_ps4_cluster_quality AS
SELECT
  q.city_id, q.device_type, q.pipeline_version,
  q.k_selected, q.silhouette, q.train_rows, q.quality_source,
  q.asof_date, q.run_id,
  CASE
    WHEN q.silhouette IS NULL      THEN 'NOT PUBLISHED'
    WHEN q.silhouette >= 0.50      THEN 'WELL SEPARATED'
    WHEN q.silhouette >= 0.25      THEN 'WEAK BUT REAL'
    ELSE                                'NOT SEPARABLE'
  END AS separation_verdict,
  CASE
    WHEN q.silhouette IS NULL THEN
      'This run did not publish a silhouette. Treat the cluster ids as arbitrary groupings until it does.'
    WHEN q.silhouette >= 0.50 THEN
      'Clusters are well separated. Naming them and acting on membership is defensible.'
    WHEN q.silhouette >= 0.25 THEN
      'Structure is real but the boundaries are soft. Use cluster distance, not cluster membership, to prioritise.'
    ELSE
      'The clusters are not separable. Do not present them as device behaviour types.'
  END AS separation_note
FROM ps4_cluster_quality q;

-- Fleet rollup joined to the current run, with the rate the timeline table does
-- not itself carry.
CREATE OR REPLACE VIEW v_ps4_weekly_timeline AS
SELECT
  t.city_id, t.device_type, t.week_start, t.week_end,
  t.devices_observed, t.actionable_devices, t.candidate_device_days,
  t.mean_anomaly_score, t.max_anomaly_score,
  ROUND((t.actionable_devices::numeric
         / NULLIF(t.devices_observed, 0)), 4)          AS actionable_share,
  t.pipeline_version, t.asof_date, t.run_id
FROM ps4_weekly_timeline t
JOIN v_ps4_v3_current cur
  ON cur.city_id = t.city_id
 AND cur.pipeline_version = t.pipeline_version;

-- Cluster profile with the distance ratio made explicit.
-- 29-Jul-2026. NAMED v_ps4_v3_cluster_profile, NOT v_ps4_cluster_profile.
--
-- sql/25_ps4_scored.sql already defines v_ps4_cluster_profile over
-- ps4_cluster_summary, whose city_id carries the city_code ENUM. CREATE OR
-- REPLACE VIEW cannot change a column's type, so redefining it with city_id
-- TEXT fails outright:
--     cannot change data type of view column "city_id" from city_code to text
--
-- That refusal is the correct outcome and the old view is left intact -- it is
-- part of the Plan B feed. The lesson is that checking for a name collision on
-- TABLES is not enough; views share the same namespace and must be checked too.
CREATE OR REPLACE VIEW v_ps4_v3_cluster_profile AS
SELECT
  p.city_id, p.device_type, p.cluster_id,
  p.scored_device_days, p.candidate_rate, p.actionable_rate,
  p.mean_cluster_distance, p.train_cluster_distance_p99, p.train_cluster_share,
  ROUND((p.mean_cluster_distance
         / NULLIF(p.train_cluster_distance_p99, 0))::numeric, 4)
                                                        AS mean_distance_ratio,
  q.silhouette, q.k_selected, q.quality_source,
  p.pipeline_version, p.asof_date, p.run_id
FROM ps4_cluster_profile p
JOIN v_ps4_v3_current cur
  ON cur.city_id = p.city_id AND cur.pipeline_version = p.pipeline_version
LEFT JOIN ps4_cluster_quality q
  ON q.city_id = p.city_id
 AND q.device_type = p.device_type
 AND q.pipeline_version = p.pipeline_version;

-- The alert feed the dashboard reads. Newest week first, worst first inside it.
--
-- WHY cluster_distance_ratio_max LEADS THE SORT and not anomaly_score_max:
-- the score is model-internal and not comparable across device types; the ratio
-- is normalised by each cluster's own training p99, so a GATE at 1.8 and a TVM
-- at 1.8 mean the same thing. Sorting a mixed-type list by the raw score would
-- rank device types, not devices.
CREATE OR REPLACE VIEW v_ps4_weekly_alerts AS
SELECT
  a.city_id, a.device_id, a.device_type, a.facility_id,
  a.week_start, a.week_end,
  a.severity, a.anomaly_types,
  a.actionable_days, a.candidate_days, a.observed_days, a.observed_hours,
  a.anomaly_score_max, a.anomaly_score_mean, a.anomaly_score_p95,
  a.max_abs_z, a.cluster_distance_ratio_max, a.dominant_cluster_id,
  a.has_low_coverage_day,
  -- Coverage caveat carried as text so a thin week cannot be read as a clean
  -- one. observed_days < 5 out of 7 is the notebook's own low-coverage flag.
  CASE WHEN a.has_low_coverage_day = 1 OR COALESCE(a.observed_days, 0) < 5
       THEN 'PARTIAL WEEK -- ' || COALESCE(a.observed_days, 0)
            || ' observed days; the score is based on less than a full week'
       ELSE 'full week' END                              AS coverage_note,
  a.pipeline_version, a.asof_date, a.run_id
FROM ps4_weekly_alerts a
JOIN v_ps4_v3_current cur
  ON cur.city_id = a.city_id AND cur.pipeline_version = a.pipeline_version;

-- Device-week summary for the current version, same coverage caveat.
CREATE OR REPLACE VIEW v_ps4_weekly_device AS
SELECT
  s.city_id, s.device_id, s.device_type, s.facility_id,
  s.week_start, s.week_end, s.severity, s.anomaly_types,
  s.observed_days, s.observed_hours, s.candidate_days, s.actionable_days,
  s.is_actionable_week,
  s.anomaly_score_max, s.anomaly_score_mean, s.anomaly_score_p95,
  s.max_abs_z, s.cluster_distance_ratio_max, s.dominant_cluster_id,
  s.has_low_coverage_day,
  CASE WHEN s.has_low_coverage_day = 1 OR COALESCE(s.observed_days, 0) < 5
       THEN TRUE ELSE FALSE END                          AS partial_week,
  s.pipeline_version, s.asof_date, s.run_id
FROM ps4_weekly_device_summary s
JOIN v_ps4_v3_current cur
  ON cur.city_id = s.city_id AND cur.pipeline_version = s.pipeline_version;

-- Reconciliation. If the published alerts file and the actionable rows of the
-- published summary disagree, this shows it as a number rather than leaving it
-- to be discovered on a slide.
CREATE OR REPLACE VIEW v_ps4_weekly_alert_reconcile AS
SELECT
  s.city_id, s.pipeline_version, s.device_type,
  COUNT(*) FILTER (WHERE s.is_actionable_week = 1)        AS summary_actionable,
  (SELECT COUNT(*) FROM ps4_weekly_alerts a
    WHERE a.city_id = s.city_id
      AND a.pipeline_version = s.pipeline_version
      AND a.device_type = s.device_type)                  AS alerts_rows,
  COUNT(*)                                                AS summary_rows
FROM ps4_weekly_device_summary s
GROUP BY s.city_id, s.pipeline_version, s.device_type;

-- Devices actionable in more than one week -- repeat offenders. A device that
-- is anomalous once is noise; one that is anomalous four weeks running is a
-- work order.
CREATE OR REPLACE VIEW v_ps4_weekly_persistent AS
SELECT
  city_id, device_id, device_type, facility_id,
  COUNT(*)                                     AS actionable_weeks,
  MIN(week_start)                              AS first_week,
  MAX(week_start)                              AS last_week,
  MAX(cluster_distance_ratio_max)              AS worst_distance_ratio,
  MAX(anomaly_score_max)                       AS worst_score,
  STRING_AGG(DISTINCT severity, ', ')          AS severities,
  pipeline_version
FROM v_ps4_weekly_device
WHERE is_actionable_week = 1
GROUP BY city_id, device_id, device_type, facility_id, pipeline_version
HAVING COUNT(*) > 1;

-- Facility concentration. Answers "is this a device problem or a site problem",
-- which is the question that actually changes what a crew is dispatched to do.
CREATE OR REPLACE VIEW v_ps4_weekly_facility AS
SELECT
  city_id, pipeline_version, facility_id, device_type,
  COUNT(DISTINCT device_id)                                        AS devices,
  COUNT(DISTINCT device_id) FILTER (WHERE is_actionable_week = 1)  AS actionable_devices,
  ROUND(AVG(cluster_distance_ratio_max)::numeric, 4)               AS mean_distance_ratio,
  MAX(week_start)                                                  AS last_week
FROM v_ps4_weekly_device
WHERE facility_id IS NOT NULL
GROUP BY city_id, pipeline_version, facility_id, device_type;

-- Status board. Every one of the six tables, its row count, and whether it is
-- populated -- the same shape as v_ps1_table_status so one panel can render
-- either.
CREATE OR REPLACE VIEW v_ps4_v3_table_status AS
SELECT 'ps4_weekly_device_summary' AS table_name,
       (SELECT COUNT(*) FROM ps4_weekly_device_summary) AS n_rows
UNION ALL SELECT 'ps4_weekly_alerts',
       (SELECT COUNT(*) FROM ps4_weekly_alerts)
UNION ALL SELECT 'ps4_weekly_timeline',
       (SELECT COUNT(*) FROM ps4_weekly_timeline)
UNION ALL SELECT 'ps4_cluster_profile',
       (SELECT COUNT(*) FROM ps4_cluster_profile)
UNION ALL SELECT 'ps4_cluster_quality',
       (SELECT COUNT(*) FROM ps4_cluster_quality)
UNION ALL SELECT 'ps4_v3_runs',
       (SELECT COUNT(*) FROM ps4_v3_runs);
