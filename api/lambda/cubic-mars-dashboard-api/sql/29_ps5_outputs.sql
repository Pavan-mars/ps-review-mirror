-- =====================================================================
-- 29_ps5_outputs.sql   27-Jul-2026
--
-- Tables for s3://<gold>/chicago/ps5/notebook_outputs/, shaped to the REAL CSV
-- headers read off the export -- not to sql/01's assumed PS5 schema.
--
-- WHY NEW TABLES RATHER THAN ps5_reliability_estimates
-- ----------------------------------------------------
-- ps5_reliability_estimates declares `device_type device_type NOT NULL` -- a
-- column typed on an ENUM that never got created. sql/01 has reported
-- `failed: 1 -- column "device_type" does not exist` on every deploy for weeks,
-- and that same broken column 500'd the entire Analyse modal earlier today.
-- Loading into it would inherit the fault. These tables use VARCHAR and carry
-- no enum dependency.
--
-- The export's own column names are kept verbatim wherever they are legal, so a
-- reader can match a dashboard field to a notebook column without a mapping
-- table. Two exceptions, both reserved words in PostgreSQL:
--     window -> window_label      table -> table_name
--
-- device_type is NOT in the CSVs -- the export splits by folder (tvm/, gates/,
-- validators/). The loader supplies it from the folder name, normalised to the
-- TVM / GATE / VALIDATOR the rest of the schema already uses.
-- =====================================================================

-- Per-device RUL. The headline PS5 feed: 1 row per device.
CREATE TABLE IF NOT EXISTS ps5_device_rul (
  city_id city_code NOT NULL REFERENCES cities(id),
  device_type VARCHAR(12) NOT NULL,
  device_id VARCHAR(40) NOT NULL,
  mars_device_category VARCHAR(12),
  current_healthy_age_days NUMERIC(10,2),
  rul_standard_days NUMERIC(10,2),
  predicted_median_survival_days NUMERIC(10,2),
  hazard_score NUMERIC(12,6),
  risk_band VARCHAR(16),
  is_overdue BOOLEAN,
  days_since_hw_oos NUMERIC(10,2),
  roll_fail_30d NUMERIC(10,2),
  n_prior_oos INT,
  facility_id VARCHAR(20),
  feature_asof_date DATE,
  event_definition VARCHAR(40),
  event_def_version VARCHAR(60),
  PRIMARY KEY (city_id, device_id)
);
CREATE INDEX IF NOT EXISTS ix_ps5_device_rul_band
  ON ps5_device_rul (city_id, device_type, risk_band, rul_standard_days);

-- ps5_serial_reliability retired 26-Sep-2026 (shadow PS5 stack); dropped by sql/68_ps5_shadow_drop.sql.

-- Out-of-time concordance leaderboard. `sd` is the standard deviation across
-- folds, so a model with a high oot_cindex and a wide sd is not better than a
-- steadier one -- the pair has to be read together, which is why sd is kept.
CREATE TABLE IF NOT EXISTS ps5_cindex_leaderboard (
  city_id city_code NOT NULL REFERENCES cities(id),
  device_type VARCHAR(12) NOT NULL,
  model VARCHAR(80) NOT NULL,
  feats VARCHAR(200) NOT NULL,
  oot_cindex NUMERIC(8,5),
  sd NUMERIC(8,5),
  window_label VARCHAR(40),
  PRIMARY KEY (city_id, device_type, model, feats)
);

-- Permutation importance. cindex_drop is the fall in concordance when the
-- feature is shuffled, so LARGER means more important -- the opposite reading
-- from a SHAP magnitude, and worth stating because a chart sorted the wrong way
-- would invert the whole story.
CREATE TABLE IF NOT EXISTS ps5_permutation_importance (
  city_id city_code NOT NULL REFERENCES cities(id),
  device_type VARCHAR(12) NOT NULL,
  feature_name VARCHAR(120) NOT NULL,
  cindex_drop NUMERIC(12,6),
  is_enriched BOOLEAN,
  PRIMARY KEY (city_id, device_type, feature_name)
);

-- Join coverage per upstream source. pct_matched is the share of rows that
-- found a match; a low value here explains a weak model far better than any
-- metric on the model itself.
CREATE TABLE IF NOT EXISTS ps5_enrich_coverage (
  city_id city_code NOT NULL REFERENCES cities(id),
  device_type VARCHAR(12) NOT NULL,
  source_name VARCHAR(80) NOT NULL,
  table_name VARCHAR(120) NOT NULL,
  status VARCHAR(30),
  pct_matched NUMERIC(7,4),
  tel_min DATE,
  tel_max DATE,
  n_feats INT,
  PRIMARY KEY (city_id, device_type, source_name, table_name)
);

-- Serving view. Ranks WITHIN a device type, never across: TVM and validator
-- hazard scores come from separately fitted models and are not on one scale, so
-- a fleet-wide ordering would compare numbers that have no common unit.
CREATE OR REPLACE VIEW v_ps5_device_rul AS
SELECT
  r.*,
  RANK() OVER (PARTITION BY r.city_id, r.device_type
               ORDER BY r.rul_standard_days ASC NULLS LAST)  AS rul_rank_in_type,
  COUNT(*) OVER (PARTITION BY r.city_id, r.device_type)      AS n_devices_in_type,
  -- Overdue AND short RUL is the actionable intersection. Either alone is a
  -- watch item; both together is a work order.
  (r.is_overdue AND r.rul_standard_days IS NOT NULL
     AND r.rul_standard_days <= 30)                          AS act_now
FROM ps5_device_rul r;
