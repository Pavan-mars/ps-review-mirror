-- =====================================================================
-- CUBIC MARS Chicago -- Phase-1c  PS5  MIGRATION 05
-- Adds the DEVICE-grain reliability feed + SERIAL-grain feed + Weibull/Cox
-- parameter tables + a daily scoring-run tracker, on top of the existing
-- category-level ps5_reliability_status (phase-1 backfill).
--
-- Runs AFTER docs/schema.sql + phase1_ps2_ps5_backfill.sql (+ ps3 migration 04).
-- 100% ADDITIVE and IDEMPOTENT -- nothing existing is dropped or renamed:
--   * new tables use CREATE TABLE IF NOT EXISTS
--   * safe to re-run.
--
-- WHY (answers "any change in RDS schema if required?"):
--   The shipped PS5 RDS layer is CATEGORY-level status only (ps5_reliability_status:
--   one concordance number per device type, gate closed). The new PS5 delivery scores
--   at DEVICE grain (expected days to next failure) AND SERIAL grain, daily, and needs
--   the fitted Weibull/Cox parameters + a run tracker. This migration adds exactly that.
--
-- GATE POLICY: data_quality_gate_passed stays FALSE (feature-flagged) until the real-run
--   C-index is reviewed. v_executive_summary already reads AVG(rul_standard_days) WHERE
--   data_quality_gate_passed = TRUE, so RUL correctly renders "Pending" until you flip it.
-- =====================================================================

BEGIN;

-- ---------------------------------------------------------------------
-- 0. ensure the category-level status table exists (from phase1 backfill).
--    Kept as-is; the writer upserts concordance + dashboard_ready per type.
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS ps5_reliability_status (
  city_id           city_code   NOT NULL REFERENCES cities(id),
  device_type       device_type NOT NULL,
  concordance_index NUMERIC(6,5),
  registry_status   VARCHAR(40),
  dashboard_ready   BOOLEAN     NOT NULL DEFAULT FALSE,
  blockers          TEXT,
  as_of_date        DATE        NOT NULL,
  PRIMARY KEY (city_id, device_type, as_of_date)
);

-- ---------------------------------------------------------------------
-- 1. DEVICE-grain reliability estimates -- the headline PS5 feed.
--    One row per device per scoring day. rul_standard_days = expected days to
--    the next chargeable failure given the device's CURRENT healthy age.
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS ps5_reliability_estimates (
  id                             BIGSERIAL    PRIMARY KEY,
  city_id                        city_code    NOT NULL REFERENCES cities(id),
  run_id                         UUID,
  device_id                      VARCHAR(40)  NOT NULL,
  mars_device_category           VARCHAR(12)  NOT NULL,      -- 'TVM' / 'GATE' / 'VALIDATOR'
  current_healthy_age_days       NUMERIC(10,1),             -- days since last failure
  rul_standard_days              NUMERIC(10,1),             -- expected days to next chargeable failure
  predicted_median_survival_days NUMERIC(10,1),
  hazard_score                   NUMERIC(6,4),              -- 0..1 relative risk (Cox)
  risk_band                      VARCHAR(10),               -- CRITICAL/HIGH/MEDIUM/LOW
  is_overdue                     BOOLEAN,
  n_prior_failures               INT,
  concordance_index              NUMERIC(6,5),              -- champion C-index for this device type
  champion_model                 VARCHAR(48),
  weibull_shape                  NUMERIC(8,4),              -- <1 infant / ~1 random / >1 wear-out
  data_quality_gate_passed       BOOLEAN      NOT NULL DEFAULT FALSE,  -- feature-flagged until reviewed
  facility_id                    VARCHAR(20),
  as_of_date                     DATE         NOT NULL,
  scored_at                      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
  UNIQUE (city_id, device_id, as_of_date)
);
CREATE INDEX IF NOT EXISTS idx_ps5_relest_city_date ON ps5_reliability_estimates (city_id, as_of_date DESC);
CREATE INDEX IF NOT EXISTS idx_ps5_relest_band      ON ps5_reliability_estimates (city_id, risk_band);
CREATE INDEX IF NOT EXISTS idx_ps5_relest_rul       ON ps5_reliability_estimates (city_id, rul_standard_days);

-- ---------------------------------------------------------------------
-- 2. SERIAL-grain reliability -- the component-level ask. One row per
--    (device, component serial) per day. risk_tier is the actionable signal
--    (parametric component RUL is directional under the high censoring).
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS ps5_serial_reliability (
  id                             BIGSERIAL    PRIMARY KEY,
  city_id                        city_code    NOT NULL REFERENCES cities(id),
  run_id                         UUID,
  device_id                      VARCHAR(40)  NOT NULL,
  component_serial_nbr           VARCHAR(64)  NOT NULL,
  component_type                 VARCHAR(40),               -- CSC_READER/PRINTER/BHU/CHU/GATE_MECH/OTHER
  mars_device_category           VARCHAR(12)  NOT NULL,
  component_age_days             NUMERIC(10,1),
  failures_total                 INT,
  chargeable_failure_count       INT,
  risk_score                     NUMERIC(12,8),             -- failures / age-day (operational)
  risk_tier                      VARCHAR(10),               -- CRITICAL/HIGH/MEDIUM/LOW
  expected_component_rul_days    NUMERIC(10,1),
  predicted_median_survival_days NUMERIC(10,1),
  is_overdue                     BOOLEAN,
  failure_observed               BOOLEAN,
  as_of_date                     DATE         NOT NULL,
  scored_at                      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
  UNIQUE (city_id, device_id, component_serial_nbr, as_of_date)
);
CREATE INDEX IF NOT EXISTS idx_ps5_serial_city_date ON ps5_serial_reliability (city_id, as_of_date DESC);
CREATE INDEX IF NOT EXISTS idx_ps5_serial_device    ON ps5_serial_reliability (city_id, device_id);
CREATE INDEX IF NOT EXISTS idx_ps5_serial_tier      ON ps5_serial_reliability (city_id, risk_tier);

-- ---------------------------------------------------------------------
-- 3. Fitted Weibull parameters (device + component grain, per device type).
--    Refreshed WEEKLY (survival params are not daily-volatile).
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS ps5_weibull_params (
  city_id              city_code   NOT NULL REFERENCES cities(id),
  mars_device_category VARCHAR(12) NOT NULL,
  grain                VARCHAR(12) NOT NULL,       -- 'device' | 'component'
  weibull_shape        NUMERIC(8,4),               -- rho
  weibull_scale        NUMERIC(12,2),              -- lambda (days)
  median_days          NUMERIC(10,1),
  failure_pattern      VARCHAR(20),                -- wear-out / random / infant-mortality / high-censoring
  as_of_date           DATE        NOT NULL,
  PRIMARY KEY (city_id, mars_device_category, grain, as_of_date)
);

-- ---------------------------------------------------------------------
-- 4. Cox hazard ratios (device + component grain) -- feature attribution.
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS ps5_cox_hazard_ratios (
  city_id              city_code   NOT NULL REFERENCES cities(id),
  mars_device_category VARCHAR(12) NOT NULL,
  grain                VARCHAR(12) NOT NULL,
  feature              VARCHAR(48) NOT NULL,
  coef                 NUMERIC(10,5),
  hazard_ratio         NUMERIC(12,4),              -- exp(coef); >1 raises failure hazard
  as_of_date           DATE        NOT NULL,
  PRIMARY KEY (city_id, mars_device_category, grain, feature, as_of_date)
);

-- ---------------------------------------------------------------------
-- 5. daily scoring-run tracker -- idempotency + observability.
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS ps5_scoring_runs (
  run_id                UUID         PRIMARY KEY DEFAULT uuid_generate_v4(),
  city_id               city_code    NOT NULL REFERENCES cities(id),
  run_ts                TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
  gold_source_partition VARCHAR(20),
  n_devices_scored      INT,
  n_serials_scored      INT,
  model_version         VARCHAR(16),
  scoring_mode          VARCHAR(12)  NOT NULL DEFAULT 'BATCH',
  status                VARCHAR(16)  NOT NULL DEFAULT 'SUCCESS',
  note                  VARCHAR(240)
);

-- ---------------------------------------------------------------------
-- 6. dashboard views
-- ---------------------------------------------------------------------
-- gate + concordance the /ps5/status route joins against
CREATE OR REPLACE VIEW v_ps5_dashboard_ready AS
SELECT s.city_id, s.device_type, s.concordance_index, s.dashboard_ready,
       s.registry_status, s.blockers, s.as_of_date
FROM ps5_reliability_status s
JOIN (SELECT city_id, device_type, MAX(as_of_date) mx
      FROM ps5_reliability_status GROUP BY city_id, device_type) l
  ON l.city_id = s.city_id AND l.device_type = s.device_type AND l.mx = s.as_of_date;

-- latest device 360 (device reliability + its serial components)
CREATE OR REPLACE VIEW v_ps5_device_360 AS
SELECT e.city_id, e.device_id, e.mars_device_category, e.current_healthy_age_days,
       e.rul_standard_days, e.predicted_median_survival_days, e.hazard_score, e.risk_band,
       e.is_overdue, e.n_prior_failures, e.concordance_index, e.data_quality_gate_passed, e.as_of_date
FROM ps5_reliability_estimates e
JOIN (SELECT city_id, device_id, MAX(as_of_date) mx
      FROM ps5_reliability_estimates GROUP BY city_id, device_id) l
  ON l.city_id = e.city_id AND l.device_id = e.device_id AND l.mx = e.as_of_date;

-- ---------------------------------------------------------------------
-- 7. migration record
-- ---------------------------------------------------------------------
INSERT INTO schema_migrations (version, name) VALUES
  ('20260718000005', 'v5c_ps5_reliability_survival_device_serial_weibull_cox')
ON CONFLICT (version) DO NOTHING;

COMMIT;

-- =====================================================================
-- Post-migration verification (run manually):
--   \d ps5_reliability_estimates
--   SELECT COUNT(*) FROM ps5_serial_reliability;
--   SELECT device_type, concordance_index, dashboard_ready FROM v_ps5_dashboard_ready;
--   SELECT AVG(rul_standard_days) FROM ps5_reliability_estimates WHERE data_quality_gate_passed;  -- NULL until gate opens
-- =====================================================================
