-- =====================================================================
-- 34_ps1_cross_wired.sql   28-Jul-2026
--
-- THE PS1 CROSS-WIRED DAILY EXPORT -- the table that has never existed.
--
-- WHERE THE DATA IS
-- -----------------
--   s3://cubic-mars-pm-s3-datalake-dev-artifacts-170202974600/
--       chicago/device_ps1_cross_wired_daily/gate        1.6 MiB  08:27:04
--                                           /tvm         1.1 MiB  08:19:29
--                                           /validator   4.4 MiB  08:28:58
--
-- Three separate keys. Earlier runs wrote all three to ONE bare key in gold,
-- which pandas to_parquet treats as a full overwrite, so only the last notebook
-- to finish survived. That is fixed; do not let the export path collapse again.
--
-- WHAT IS IN IT, AND WHY IT MATTERS
-- ----------------------------------
-- 34 columns, identical across GATE / TVM / VALIDATOR -- MEASURED by the
-- loader's dry_run, 28-Jul. The console log's "Columns in output (30)" is an
-- undercount of its own frame; trust the file, not the log.
--
--     GATE        107,110 rows   1,676,627 bytes
--     TVM         184,483 rows   1,117,661 bytes
--     VALIDATOR   494,932 rows   4,579,009 bytes
--     total       786,525
--
-- TVM is 184,483 here and 184,386 in the 26-Jul console log because TVM was
-- re-run at 08:19 on 28-Jul. S3 is the current truth; reconcile against 786,525.
--
-- This carries the SHAP triples -- shap_feat1..3 with signed shap_val1..3 -- at
-- device x component x day grain. That is the feature-importance and causation
-- evidence the dashboard has been missing. It was never lost; it had nowhere to
-- land.
--
-- NO PRIMARY KEY ON THE BUSINESS COLUMNS. READ THIS BEFORE ADDING ONE.
-- ---------------------------------------------------------------------
-- The obvious key is (device_key, component_serial_nbr, transit_day). It is NOT
-- declared, for two reasons that have already cost this programme real time:
--
--   1. COMPONENT_SERIAL_NBR is nullable. Validators carry NULL serials -- that
--      is exactly what broke sql/30's ux_ps5_serial_rul_key, which had to be
--      dropped in sql/31.
--   2. A uniqueness constraint is a CLAIM about data nobody has measured yet.
--      Asserting one that the export does not satisfy either fails the load or,
--      worse, succeeds for the wrong reason and locks in a grain we invented.
--
-- So: a surrogate BIGSERIAL, plain indexes for the lookups, and
-- v_ps1_xw_grain at the bottom to MEASURE the grain from the loaded rows. If it
-- comes back empty, the natural key holds and a unique index can be added in a
-- later file with evidence behind it.
--
-- DEVICE_KEY IS THE SCD2 SURROGATE -- BUT DEVICE_ID SHIPS TOO
-- -------------------------------------------------------------
-- 28-Jul-2026, MEASURED by the loader's dry_run: the export carries 34 columns,
-- not the 30 the console log claims, and DEVICE_ID is one of them. An earlier
-- version of this file left device_id NULL and said resolving it needed a
-- verified DEVICE_KEY -> DEVICE_ID dim. It does not. It is in the file.
--
-- Both are stored. device_key is the SCD2 surrogate the model scored on;
-- device_id is the stable business identifier every other tab joins on. Join
-- OUTWARD on device_id and you are safe; join this table's device_key to
-- anything at DEVICE_ID grain and one device's many key versions multiply the
-- rows -- the defect that inflated the PS5 validator roster 25x.
--
-- THE LABEL IS HERE: will_hardware_oos_3d
-- ----------------------------------------
-- The OOS target, on the same row as the prediction and the threshold it was
-- made at. That is what makes v_ps1_xw_performance below possible: precision,
-- recall and lift MEASURED on the scored population rather than inherited from
-- a training-time baseline CSV that carries no device identity.
--
-- OOS, not chargeable events. Chargeable events are a subset of OOS gated by
-- if/then conditions that fire after the fact, so they cannot be the target for
-- predicting a failure that has not happened yet.
-- =====================================================================

CREATE TABLE IF NOT EXISTS ps1_cross_wired_daily (
  xw_id                          BIGSERIAL PRIMARY KEY,
  city_id                        city_code   NOT NULL REFERENCES cities(id),
  device_type                    VARCHAR(12) NOT NULL,   -- GATE | TVM | VALIDATOR
  device_key                     VARCHAR(64) NOT NULL,   -- SCD2 surrogate
  device_id                      VARCHAR(40),            -- stable business id; JOIN ON THIS
  component_serial_nbr           VARCHAR(64),            -- NULL for validators
  component_type                 VARCHAR(80),            -- the PS3 pareto's missing name
  device_category                VARCHAR(40),
  facility_id                    VARCHAR(40),
  operator_id                    VARCHAR(40),
  transit_day                    DATE        NOT NULL,
  event_date                     DATE,

  -- PS1: the prediction and the threshold it was made at. threshold_used is
  -- stored per row because it is tuned per device type (GATE ran at 0.135) and a
  -- probability without its threshold cannot be turned back into a decision.
  ps1_fail_prob                  NUMERIC(9,6),
  ps1_predicted                  SMALLINT,
  threshold_used                 NUMERIC(9,6),
  ps1_risk_tier                  VARCHAR(12),            -- CRITICAL|HIGH|MEDIUM|LOW
  score                          NUMERIC(14,8),          -- raw model score, pre-calibration
  ps1_p95_hist                   NUMERIC(9,6),           -- the p95 that is_prob_anomaly tests
  is_prob_anomaly                BOOLEAN,                -- prob > p95 within the run

  -- THE LABEL. 1 = hardware went OOS within 3 days of this row's day.
  will_hardware_oos_3d           SMALLINT,

  -- PS2 chain context, cross-wired in at the same grain.
  is_coordinated_station_failure BOOLEAN,
  is_major_station_event         BOOLEAN,
  station_devices_failed         INT,
  days_healthy_before_chain      NUMERIC(10,2),

  -- PS5 reliability history.
  avg_rolling_mttr_30d_min       NUMERIC(12,3),
  avg_rolling_mttr_90d_min       NUMERIC(12,3),
  max_downtime_ever_min          NUMERIC(14,3),
  total_failure_days_s28         INT,
  last_failure_date_s28          DATE,
  component_age_days             NUMERIC(10,2),
  no_prior_failure_in_window     BOOLEAN,

  -- SHAP top-3. Signed values: a negative val pushes AWAY from failure. Do not
  -- rank on the raw value -- rank on abs(), which is what v_ps1_shap_importance
  -- below does.
  shap_feat1                     VARCHAR(120),
  shap_val1                      NUMERIC(14,8),
  shap_feat2                     VARCHAR(120),
  shap_val2                      NUMERIC(14,8),
  shap_feat3                     VARCHAR(120),
  shap_val3                      NUMERIC(14,8),

  -- Anything the export gains in future lands here instead of being dropped.
  -- As of the 28-Jul dry_run all 34 columns are mapped, so this is empty -- it
  -- exists so a schema change in the notebook cannot silently lose data.
  extra                          JSONB,

  asof_date                      DATE        NOT NULL,
  run_id                         VARCHAR(64)
);

CREATE INDEX IF NOT EXISTS ix_ps1_xw_dev_day
  ON ps1_cross_wired_daily (city_id, device_key, transit_day DESC);
CREATE INDEX IF NOT EXISTS ix_ps1_xw_type_day
  ON ps1_cross_wired_daily (city_id, device_type, transit_day DESC);
CREATE INDEX IF NOT EXISTS ix_ps1_xw_tier
  ON ps1_cross_wired_daily (city_id, device_type, ps1_risk_tier);
CREATE INDEX IF NOT EXISTS ix_ps1_xw_device_id
  ON ps1_cross_wired_daily (city_id, device_id, transit_day DESC);
CREATE INDEX IF NOT EXISTS ix_ps1_xw_facility
  ON ps1_cross_wired_daily (city_id, facility_id, transit_day DESC);
CREATE INDEX IF NOT EXISTS ix_ps1_xw_label
  ON ps1_cross_wired_daily (city_id, device_type, will_hardware_oos_3d);
CREATE INDEX IF NOT EXISTS ix_ps1_xw_serial
  ON ps1_cross_wired_daily (city_id, component_serial_nbr)
  WHERE component_serial_nbr IS NOT NULL;

-- ---------------------------------------------------------------------------
-- FEATURE IMPORTANCE, computed from the loaded rows.
--
-- Not a leaderboard, not a training-time artefact: this is what actually drove
-- the scored population, unpivoted from the three SHAP slots. mean_abs_shap is
-- the magnitude ranking; mean_signed_shap says which DIRECTION the feature
-- pushed on average, so a feature that is important but protective is visibly
-- different from one that is important and damaging. Both are needed -- ranking
-- on the signed mean alone lets a feature that pushes hard in both directions
-- average out to zero and disappear.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE VIEW v_ps1_shap_importance AS
WITH unpivoted AS (
  SELECT city_id, device_type, transit_day, shap_feat1 AS feat, shap_val1 AS val
    FROM ps1_cross_wired_daily WHERE shap_feat1 IS NOT NULL
  UNION ALL
  SELECT city_id, device_type, transit_day, shap_feat2, shap_val2
    FROM ps1_cross_wired_daily WHERE shap_feat2 IS NOT NULL
  UNION ALL
  SELECT city_id, device_type, transit_day, shap_feat3, shap_val3
    FROM ps1_cross_wired_daily WHERE shap_feat3 IS NOT NULL
)
SELECT
  city_id,
  device_type,
  feat                                              AS feature_name,
  COUNT(*)                                          AS n_rows,
  ROUND(AVG(ABS(val)), 6)                           AS mean_abs_shap,
  ROUND(AVG(val), 6)                                AS mean_signed_shap,
  ROUND(MAX(ABS(val)), 6)                           AS max_abs_shap,
  COUNT(*) FILTER (WHERE val > 0)                   AS n_pushes_toward_failure,
  COUNT(*) FILTER (WHERE val < 0)                   AS n_pushes_away,
  RANK() OVER (PARTITION BY city_id, device_type
               ORDER BY AVG(ABS(val)) DESC)         AS importance_rank
FROM unpivoted
GROUP BY city_id, device_type, feat;

-- Per-device drivers: the top feature for one device, for the modal. Latest day
-- only -- a device's driver today is the question the panel asks.
CREATE OR REPLACE VIEW v_ps1_device_drivers AS
WITH latest AS (
  SELECT DISTINCT ON (city_id, device_key)
         city_id, device_key, device_id, device_type, component_type,
         facility_id, transit_day, ps1_fail_prob,
         ps1_risk_tier, threshold_used, will_hardware_oos_3d,
         shap_feat1, shap_val1, shap_feat2, shap_val2, shap_feat3, shap_val3
  FROM ps1_cross_wired_daily
  ORDER BY city_id, device_key, transit_day DESC
)
SELECT * FROM latest;

-- ---------------------------------------------------------------------------
-- CAUSATION: does the PS2 chain context actually move the PS1 risk?
--
-- Lift, not correlation-by-eyeball. critical_rate_in_chain is the share of
-- device-days at CRITICAL when a coordinated station failure was in play;
-- critical_rate_no_chain is the same share when it was not. The ratio is the
-- lift, and it is the number that says whether station-level contagion is
-- carrying PS1 risk or merely co-occurring with it.
--
-- Rows with fewer than 30 device-days on either side are excluded: a lift
-- computed from a handful of days is noise wearing a decimal point.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE VIEW v_ps1_xw_causation AS
WITH base AS (
  SELECT
    city_id, device_type,
    COUNT(*) FILTER (WHERE is_coordinated_station_failure)                    AS n_chain,
    COUNT(*) FILTER (WHERE NOT is_coordinated_station_failure
                        OR is_coordinated_station_failure IS NULL)            AS n_no_chain,
    COUNT(*) FILTER (WHERE is_coordinated_station_failure
                       AND ps1_risk_tier = 'CRITICAL')                        AS n_crit_chain,
    COUNT(*) FILTER (WHERE (NOT is_coordinated_station_failure
                            OR is_coordinated_station_failure IS NULL)
                       AND ps1_risk_tier = 'CRITICAL')                        AS n_crit_no_chain,
    ROUND(AVG(ps1_fail_prob) FILTER (WHERE is_coordinated_station_failure), 6) AS mean_prob_chain,
    ROUND(AVG(ps1_fail_prob) FILTER (WHERE NOT is_coordinated_station_failure
                                        OR is_coordinated_station_failure IS NULL), 6)
                                                                              AS mean_prob_no_chain,
    ROUND(AVG(days_healthy_before_chain), 2)                                  AS mean_days_healthy_before_chain,
    ROUND(AVG(station_devices_failed::numeric), 2)                            AS mean_station_devices_failed
  FROM ps1_cross_wired_daily
  GROUP BY city_id, device_type
)
SELECT
  city_id, device_type, n_chain, n_no_chain,
  ROUND(n_crit_chain::numeric    / NULLIF(n_chain, 0),    4) AS critical_rate_in_chain,
  ROUND(n_crit_no_chain::numeric / NULLIF(n_no_chain, 0), 4) AS critical_rate_no_chain,
  ROUND( (n_crit_chain::numeric    / NULLIF(n_chain, 0))
       / NULLIF(n_crit_no_chain::numeric / NULLIF(n_no_chain, 0), 0), 3) AS critical_lift,
  mean_prob_chain, mean_prob_no_chain,
  mean_days_healthy_before_chain, mean_station_devices_failed
FROM base
WHERE n_chain >= 30 AND n_no_chain >= 30;

-- ---------------------------------------------------------------------------
-- GRAIN AUDIT. Run this AFTER the load, before anyone declares a unique index.
-- Empty result => (device_key, component_serial_nbr, transit_day) is unique and
-- the constraint can be added with evidence. Non-empty => the columns whose
-- distinct-count exceeds 1 are the rest of the real grain.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE VIEW v_ps1_xw_grain AS
SELECT
  city_id, device_type, device_key, component_serial_nbr, transit_day,
  COUNT(*)                              AS n_rows,
  COUNT(DISTINCT ps1_fail_prob)         AS n_probs,
  COUNT(DISTINCT ps1_risk_tier)         AS n_tiers,
  COUNT(DISTINCT threshold_used)        AS n_thresholds,
  COUNT(DISTINCT device_category)       AS n_categories,
  COUNT(DISTINCT run_id)                AS n_runs
FROM ps1_cross_wired_daily
GROUP BY city_id, device_type, device_key, component_serial_nbr, transit_day
HAVING COUNT(*) > 1;

-- Load reconciliation. Compare these counts against the console-log figures
-- (107,110 / 184,386 / 494,932) before trusting any panel built on this table.
CREATE OR REPLACE VIEW v_ps1_xw_summary AS
SELECT
  city_id, device_type,
  COUNT(*)                                        AS n_rows,
  COUNT(DISTINCT device_key)                      AS n_device_keys,
  COUNT(DISTINCT component_serial_nbr)            AS n_serials,
  MIN(transit_day)                                AS first_day,
  MAX(transit_day)                                AS last_day,
  COUNT(*) FILTER (WHERE ps1_risk_tier='CRITICAL') AS n_critical,
  COUNT(*) FILTER (WHERE ps1_risk_tier='HIGH')     AS n_high,
  COUNT(*) FILTER (WHERE ps1_risk_tier='MEDIUM')   AS n_medium,
  COUNT(*) FILTER (WHERE ps1_risk_tier='LOW')      AS n_low,
  COUNT(DISTINCT device_id)                        AS n_device_ids,
  COUNT(DISTINCT facility_id)                      AS n_facilities,
  COUNT(*) FILTER (WHERE shap_feat1 IS NOT NULL)   AS n_with_shap,
  COUNT(*) FILTER (WHERE will_hardware_oos_3d = 1) AS n_positive_label,
  ROUND(AVG(will_hardware_oos_3d::numeric), 5)     AS label_base_rate,
  MAX(asof_date)                                   AS asof_date
FROM ps1_cross_wired_daily
GROUP BY city_id, device_type;

-- ---------------------------------------------------------------------------
-- MEASURED PERFORMANCE, from the scored population.
--
-- will_hardware_oos_3d is the OOS label and it sits on the same row as the
-- prediction, so precision and recall can be COUNTED here rather than inherited
-- from a Model Quality baseline CSV -- those carry only prediction/probability/
-- label with no device identity, so they cannot be sliced by type, facility or
-- component and cannot be re-checked against the fleet the model actually ran on.
--
-- NO ACCURACY COLUMN. The base rate is low; a model that predicts "healthy"
-- everywhere scores high accuracy and catches nothing. Precision, recall and
-- lift are what a maintenance decision rests on. Accuracy is reported nowhere
-- in this file on purpose -- if a panel needs it, it computes it and owns it.
--
-- The rate columns are per device type because threshold_used is tuned per type
-- (GATE ran at 0.135). Pooling types would compare decisions made at different
-- operating points.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE VIEW v_ps1_xw_performance AS
WITH c AS (
  SELECT
    city_id, device_type,
    COUNT(*)                                                          AS n_scored,
    COUNT(*) FILTER (WHERE will_hardware_oos_3d = 1)                  AS n_pos,
    COUNT(*) FILTER (WHERE ps1_predicted = 1
                       AND will_hardware_oos_3d = 1)                  AS tp,
    COUNT(*) FILTER (WHERE ps1_predicted = 1
                       AND will_hardware_oos_3d = 0)                  AS fp,
    COUNT(*) FILTER (WHERE ps1_predicted = 0
                       AND will_hardware_oos_3d = 1)                  AS fn,
    COUNT(*) FILTER (WHERE ps1_predicted = 0
                       AND will_hardware_oos_3d = 0)                  AS tn,
    MIN(threshold_used)                                               AS threshold_min,
    MAX(threshold_used)                                               AS threshold_max
  FROM ps1_cross_wired_daily
  WHERE will_hardware_oos_3d IS NOT NULL AND ps1_predicted IS NOT NULL
  GROUP BY city_id, device_type
)
SELECT
  city_id, device_type, n_scored, n_pos, tp, fp, fn, tn,
  threshold_min, threshold_max,
  ROUND(n_pos::numeric / NULLIF(n_scored, 0), 5)        AS base_rate,
  ROUND(tp::numeric / NULLIF(tp + fp, 0), 4)            AS precision_at_threshold,
  ROUND(tp::numeric / NULLIF(tp + fn, 0), 4)            AS recall_at_threshold,
  ROUND(2.0 * tp / NULLIF(2 * tp + fp + fn, 0), 4)      AS f1_at_threshold,
  -- Lift: how much better than flagging at random. 1.0 means the model adds
  -- nothing, whatever its precision looks like in isolation.
  ROUND( (tp::numeric / NULLIF(tp + fp, 0))
       / NULLIF(n_pos::numeric / NULLIF(n_scored, 0), 0), 3) AS precision_lift
FROM c;

-- Does the risk tier mean anything? Positive rate should climb monotonically
-- LOW -> MEDIUM -> HIGH -> CRITICAL. If it does not, the tier cutpoints are
-- mislabelled and every panel that sorts by tier is misleading.
CREATE OR REPLACE VIEW v_ps1_xw_tier_calibration AS
SELECT
  city_id, device_type, ps1_risk_tier,
  COUNT(*)                                                  AS n_rows,
  COUNT(*) FILTER (WHERE will_hardware_oos_3d = 1)          AS n_positive,
  ROUND(AVG(will_hardware_oos_3d::numeric), 5)              AS positive_rate,
  ROUND(AVG(ps1_fail_prob), 5)                              AS mean_predicted_prob,
  ROUND(MIN(ps1_fail_prob), 5)                              AS min_prob,
  ROUND(MAX(ps1_fail_prob), 5)                              AS max_prob
FROM ps1_cross_wired_daily
WHERE will_hardware_oos_3d IS NOT NULL
GROUP BY city_id, device_type, ps1_risk_tier;

-- Station rollup, now that facility_id ships in the export.
CREATE OR REPLACE VIEW v_ps1_xw_facility AS
SELECT
  city_id, facility_id,
  COUNT(DISTINCT device_id)                                 AS n_devices,
  COUNT(*)                                                  AS n_device_days,
  COUNT(*) FILTER (WHERE ps1_risk_tier = 'CRITICAL')        AS n_critical,
  ROUND(AVG(ps1_fail_prob), 5)                              AS mean_fail_prob,
  COUNT(*) FILTER (WHERE will_hardware_oos_3d = 1)          AS n_oos_3d,
  COUNT(*) FILTER (WHERE is_coordinated_station_failure)    AS n_chain_days,
  MAX(transit_day)                                          AS last_day
FROM ps1_cross_wired_daily
WHERE facility_id IS NOT NULL
GROUP BY city_id, facility_id;
