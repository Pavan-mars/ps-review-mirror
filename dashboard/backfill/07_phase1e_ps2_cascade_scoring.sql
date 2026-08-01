-- =====================================================================
-- CUBIC MARS Chicago -- Phase-1e  PS2  MIGRATION 07
-- Batch-Transform-scored cascade tables (markov, hmm, recurrence) + the two
-- population-level tables referenced by earlier backfills but never given
-- checked-in DDL (ps2_subsystem_associations, ps2_hmm_regimes) + one new
-- population table (ps2_conditional_prob).
--
-- Runs AFTER phase1_ps2_ps5_backfill.sql and 06_phase1d_ps5_oos_set_reliability.sql.
-- 100% ADDITIVE + IDEMPOTENT, same idiom as migration 06:
--   * CREATE TABLE IF NOT EXISTS -- safe whether or not the ad-hoc versions
--     of ps2_subsystem_associations / ps2_hmm_regimes already exist live
--   * safe to re-run; nothing existing is dropped or renamed
--
-- WHY: PK's 2026-07-24 direction -- PS2's daily gold-data pipeline follows the
-- same MLflow model registry -> SageMaker Model (ECR image_uri) -> EndpointConfig
-- pattern as PS3/PS5, served via Batch Transform, orchestrated by a Step
-- Function (state-refresh -> transform -> load) on a daily EventBridge rule.
-- No Databricks gold/silver table is touched by this migration or the pipeline
-- it supports -- everything here is computed in sagemaker/ps2/batch/ from
-- read-only Parquet exports of gold.device_ps2_chains + silver.hw_config_current.
--
-- Family split (confirmed with PK):
--   SCOREABLE (Batch-Transform, fitted params) -- markov, hmm, recurrence
--   POPULATION-LEVEL (computed directly, no model) -- phi, conditional
--     probability, association rules, network centrality, facility contagion
--     (these five already have a landing spot except conditional_prob, which
--     this migration adds)
--
-- Written for PostgreSQL 16 (Aurora). Aurora is the APP-TIER db only -- never
-- the ML source of truth.
-- =====================================================================

BEGIN;

-- ---------------------------------------------------------------------
-- 1. scoring-run tracker (lineage) -- mirrors ps5_scoring_runs
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS ps2_scoring_runs (
  run_id             UUID         PRIMARY KEY,
  city_id            city_code    NOT NULL REFERENCES cities(id),
  run_ts             TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
  n_devices_scored   INT,
  families           VARCHAR(64),                 -- comma-separated: markov,hmm,recurrence
  status             VARCHAR(16)  NOT NULL,       -- SUCCESS / FAILED
  note               TEXT,
  run_date           DATE
);

-- ---------------------------------------------------------------------
-- 2. MARKOV -- device + serial grain (Batch-Transform-scored)
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS ps2_markov_device_scores (
  city_id                   city_code    NOT NULL REFERENCES cities(id),
  run_id                    UUID,
  device_id                 VARCHAR(20)  NOT NULL,
  mars_device_category      VARCHAR(12),
  current_subsystem         VARCHAR(30),
  predicted_next_subsystem  VARCHAR(30),
  predicted_next_prob       NUMERIC(6,4),
  escalation_subsystem      VARCHAR(30),
  escalation_prob           NUMERIC(6,4),
  self_loop_prob            NUMERIC(6,4),
  as_of_date                DATE         NOT NULL,
  event_definition          VARCHAR(24),
  event_def_version         VARCHAR(24),
  scored_at                 TIMESTAMPTZ,
  PRIMARY KEY (city_id, device_id, as_of_date)
);

CREATE TABLE IF NOT EXISTS ps2_markov_serial_scores (
  city_id                   city_code    NOT NULL REFERENCES cities(id),
  run_id                    UUID,
  device_id                 VARCHAR(20)  NOT NULL,
  component_serial_nbr      VARCHAR(40)  NOT NULL,
  component_description     VARCHAR(60),
  mars_device_category      VARCHAR(12),
  current_subsystem         VARCHAR(30),
  predicted_next_subsystem  VARCHAR(30),
  predicted_next_prob       NUMERIC(6,4),
  escalation_subsystem      VARCHAR(30),
  escalation_prob           NUMERIC(6,4),
  as_of_date                DATE         NOT NULL,
  event_definition          VARCHAR(24),
  event_def_version         VARCHAR(24),
  scored_at                 TIMESTAMPTZ,
  PRIMARY KEY (city_id, device_id, component_serial_nbr, as_of_date)
);

-- ---------------------------------------------------------------------
-- 3. HMM -- per-device CURRENT regime (Batch-Transform-scored). The fleet-level
--    prevalence table (ps2_hmm_regimes) already exists from an earlier ad-hoc
--    backfill (referenced by INSERT in phase1_ps2_ps5_backfill.sql, no DDL
--    checked in) -- CREATE TABLE IF NOT EXISTS backstops that gap here.
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS ps2_hmm_regimes (
  city_id           city_code    NOT NULL REFERENCES cities(id),
  regime            VARCHAR(20)  NOT NULL,        -- Minor / Moderate / Critical
  pct               NUMERIC(5,2),
  dwell_days_min    NUMERIC(6,2),
  dwell_days_max    NUMERIC(6,2),
  computed_date     DATE         NOT NULL,
  PRIMARY KEY (city_id, regime, computed_date)
);

CREATE TABLE IF NOT EXISTS ps2_hmm_device_regime (
  city_id                    city_code    NOT NULL REFERENCES cities(id),
  run_id                     UUID,
  device_id                  VARCHAR(20)  NOT NULL,
  mars_device_category       VARCHAR(12),
  current_regime             VARCHAR(20),          -- Minor / Moderate / Critical cascade
  regime_posterior           JSONB,                -- {"Minor cascade": 0.02, ...}
  prob_escalate_to_critical  NUMERIC(6,4),
  dwell_days_expected        NUMERIC(6,2),
  as_of_date                 DATE         NOT NULL,
  event_definition           VARCHAR(24),
  event_def_version          VARCHAR(24),
  scored_at                  TIMESTAMPTZ,
  PRIMARY KEY (city_id, device_id, as_of_date)
);

-- serial-grain counterpart -- run_batch_transform.py scores hmm at BOTH grains
-- (device, serial) same as markov; this table was the known gap flagged during
-- the build (only markov originally got a serial table) -- added here so all
-- three scoreable families are symmetric device+serial, per PK's "process the
-- same for all PS2 gold layer data" direction.
CREATE TABLE IF NOT EXISTS ps2_hmm_serial_regime (
  city_id                    city_code    NOT NULL REFERENCES cities(id),
  run_id                     UUID,
  device_id                  VARCHAR(20)  NOT NULL,
  component_serial_nbr       VARCHAR(40)  NOT NULL,
  component_description      VARCHAR(60),
  mars_device_category       VARCHAR(12),
  current_regime             VARCHAR(20),
  regime_posterior           JSONB,
  prob_escalate_to_critical  NUMERIC(6,4),
  dwell_days_expected        NUMERIC(6,2),
  as_of_date                 DATE         NOT NULL,
  event_definition           VARCHAR(24),
  event_def_version          VARCHAR(24),
  scored_at                  TIMESTAMPTZ,
  PRIMARY KEY (city_id, device_id, component_serial_nbr, as_of_date)
);

-- ---------------------------------------------------------------------
-- 4. RECURRENCE -- per-device chronic/sporadic classification (Batch-Transform-scored)
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS ps2_device_recurrence_scores (
  city_id                   city_code    NOT NULL REFERENCES cities(id),
  run_id                    UUID,
  device_id                 VARCHAR(20)  NOT NULL,
  mars_device_category      VARCHAR(12),
  cascade_days_total        INT,
  cascade_rate              NUMERIC(8,4),
  recurrence_class          VARCHAR(12),           -- chronic / sporadic
  recurrence_hazard_score   NUMERIC(6,4),
  as_of_date                DATE         NOT NULL,
  event_definition          VARCHAR(24),
  event_def_version         VARCHAR(24),
  scored_at                 TIMESTAMPTZ,
  PRIMARY KEY (city_id, device_id, as_of_date)
);

-- serial-grain counterpart -- same rationale as ps2_hmm_serial_regime above.
CREATE TABLE IF NOT EXISTS ps2_serial_recurrence_scores (
  city_id                   city_code    NOT NULL REFERENCES cities(id),
  run_id                    UUID,
  device_id                 VARCHAR(20)  NOT NULL,
  component_serial_nbr      VARCHAR(40)  NOT NULL,
  component_description     VARCHAR(60),
  mars_device_category      VARCHAR(12),
  cascade_days_total        INT,
  cascade_rate              NUMERIC(8,4),
  recurrence_class          VARCHAR(12),
  recurrence_hazard_score   NUMERIC(6,4),
  as_of_date                DATE         NOT NULL,
  event_definition          VARCHAR(24),
  event_def_version         VARCHAR(24),
  scored_at                 TIMESTAMPTZ,
  PRIMARY KEY (city_id, device_id, component_serial_nbr, as_of_date)
);

-- ---------------------------------------------------------------------
-- 5. Population-level tables (no model, computed directly in refresh_device_state.py)
--    ps2_subsystem_hub_edges / ps2_subsystem_hub_summary / ps2_facility_contagion_summary
--    already exist (phase1_ps2_ps5_backfill.sql) -- reused as-is for phi and
--    network centrality. ps2_subsystem_associations is referenced by INSERT
--    there too but has no checked-in DDL -- backstop it here.
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS ps2_subsystem_associations (
  city_id                city_code    NOT NULL REFERENCES cities(id),
  antecedent_subsystem   VARCHAR(60)  NOT NULL,
  consequent_subsystem   VARCHAR(60)  NOT NULL,
  support                NUMERIC(6,4),
  confidence             NUMERIC(6,4),
  lift                   NUMERIC(8,4),
  conviction             NUMERIC(10,2),
  computed_date          DATE         NOT NULL,
  PRIMARY KEY (city_id, antecedent_subsystem, consequent_subsystem, computed_date)
);

-- Genuinely new: conditional-probability grid P(B follows A within the same chain)
CREATE TABLE IF NOT EXISTS ps2_conditional_prob (
  city_id                city_code    NOT NULL REFERENCES cities(id),
  antecedent_subsystem   VARCHAR(30)  NOT NULL,
  consequent_subsystem   VARCHAR(30)  NOT NULL,
  conditional_prob       NUMERIC(6,4),
  support_count          INT,
  computed_date          DATE         NOT NULL,
  PRIMARY KEY (city_id, antecedent_subsystem, consequent_subsystem, computed_date)
);

-- ---------------------------------------------------------------------
-- 6. event-definition reference (one row per version) -- dashboard chip, same
--    pattern as ps5_event_definition
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS ps2_event_definition (
  event_def_version  VARCHAR(24)  PRIMARY KEY,
  event_definition   VARCHAR(24)  NOT NULL,          -- 'cascade_chain'
  label              VARCHAR(80)  NOT NULL,
  criteria           JSONB        NOT NULL,
  effective_date     DATE         NOT NULL DEFAULT CURRENT_DATE,
  created_at         TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

INSERT INTO ps2_event_definition (event_def_version, event_definition, label, criteria)
VALUES ('2026-07-24.v1', 'cascade_chain', 'Cascade day = device-day with chain_length >= 2 hardware-OOS fault onsets',
  '{"source":"gold.device_ps2_chains","grain":"device_id,transit_day","min_chain_length":2}'::jsonb)
ON CONFLICT (event_def_version) DO UPDATE SET
  event_definition = EXCLUDED.event_definition, label = EXCLUDED.label, criteria = EXCLUDED.criteria;

COMMIT;
