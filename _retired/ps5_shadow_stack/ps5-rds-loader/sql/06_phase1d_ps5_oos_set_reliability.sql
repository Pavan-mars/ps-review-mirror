-- =====================================================================
-- CUBIC MARS Chicago -- Phase-1d  PS5  MIGRATION 06
-- Hardware-OOS-Set event columns on the PS5 device/serial reliability feed.
--
-- Runs AFTER 05_phase1c_ps5_reliability_survival.sql. 100% ADDITIVE + IDEMPOTENT:
--   * ALTER TABLE IF EXISTS ... ADD COLUMN IF NOT EXISTS  (safe whether or not 05 ran)
--   * CREATE TABLE IF NOT EXISTS / CREATE OR REPLACE VIEW
--   * safe to re-run; nothing existing is dropped or renamed.
--
-- WHY: 05 shipped the device-grain + serial-grain reliability feed on the *chargeable*
--   failure event. The v5.1 rework redefines the survival event to ANY HARDWARE OOS 'Set'
--   (fault_state=Set + is_device_fault + counted-as-OOS; commanded/maintenance OOS excluded;
--   chargeable gating removed) and adds the as-of failure-recency features. This migration
--   records WHICH event each RUL is measured against + the aligned recency, so the dashboard
--   can show the event-definition chip and the ps5_daily_scorer Lambda can write both grains.
--
-- Written for PostgreSQL 16 (Aurora). Aurora is the APP-TIER db only -- never the ML source.
-- =====================================================================

BEGIN;

-- ---------------------------------------------------------------------
-- 1. scoring-run tracker: which event definition this run scored, + as-of + scorer
-- ---------------------------------------------------------------------
ALTER TABLE IF EXISTS ps5_scoring_runs ADD COLUMN IF NOT EXISTS event_definition  VARCHAR(24);
ALTER TABLE IF EXISTS ps5_scoring_runs ADD COLUMN IF NOT EXISTS event_def_version VARCHAR(24);
ALTER TABLE IF EXISTS ps5_scoring_runs ADD COLUMN IF NOT EXISTS event_filter      JSONB;
ALTER TABLE IF EXISTS ps5_scoring_runs ADD COLUMN IF NOT EXISTS run_date          DATE;
ALTER TABLE IF EXISTS ps5_scoring_runs ADD COLUMN IF NOT EXISTS scorer            VARCHAR(32);

-- ---------------------------------------------------------------------
-- 2. device-grain: as-of hardware-OOS recency + event provenance
--    (days_since_hw_oos / roll_fail_30d are computed STRICTLY as-of the scoring date)
-- ---------------------------------------------------------------------
ALTER TABLE IF EXISTS ps5_reliability_estimates ADD COLUMN IF NOT EXISTS days_since_hw_oos  NUMERIC(10,1);
ALTER TABLE IF EXISTS ps5_reliability_estimates ADD COLUMN IF NOT EXISTS roll_fail_30d      INT;
ALTER TABLE IF EXISTS ps5_reliability_estimates ADD COLUMN IF NOT EXISTS event_definition   VARCHAR(24);
ALTER TABLE IF EXISTS ps5_reliability_estimates ADD COLUMN IF NOT EXISTS event_def_version  VARCHAR(24);
ALTER TABLE IF EXISTS ps5_reliability_estimates ADD COLUMN IF NOT EXISTS feature_asof_date  DATE;

-- ---------------------------------------------------------------------
-- 3. serial-grain: OOS-Set failure count + event provenance
--    (keeps chargeable_failure_count from 05; adds the broader hardware-OOS count)
-- ---------------------------------------------------------------------
ALTER TABLE IF EXISTS ps5_serial_reliability ADD COLUMN IF NOT EXISTS device_oos_failures_total INT;
ALTER TABLE IF EXISTS ps5_serial_reliability ADD COLUMN IF NOT EXISTS event_definition   VARCHAR(24);
ALTER TABLE IF EXISTS ps5_serial_reliability ADD COLUMN IF NOT EXISTS event_def_version  VARCHAR(24);
ALTER TABLE IF EXISTS ps5_serial_reliability ADD COLUMN IF NOT EXISTS feature_asof_date  DATE;

-- ---------------------------------------------------------------------
-- 4. event-definition reference (one row per version) -- the dashboard chip reads this
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS ps5_event_definition (
  event_def_version  VARCHAR(24)  PRIMARY KEY,
  event_definition   VARCHAR(24)  NOT NULL,          -- 'hw_oos_set'
  label              VARCHAR(80)  NOT NULL,          -- 'Failure = any hardware OOS (Set)'
  criteria           JSONB        NOT NULL,          -- exact filter (fault_state, levels, exclusions, chargeable=false)
  window_mode        VARCHAR(24),                    -- 'telemetry_era'
  telemetry_start    DATE,                           -- 2024-01-01
  cindex_floor       NUMERIC(4,2),                   -- promotion gate (0.65)
  effective_date     DATE         NOT NULL DEFAULT CURRENT_DATE,
  created_at         TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

INSERT INTO ps5_event_definition
  (event_def_version, event_definition, label, criteria, window_mode, telemetry_start, cindex_floor)
VALUES
  ('2026-07-23.v1', 'hw_oos_set', 'Failure = any hardware OOS (Set)',
   ('{"fault_state":"Set","is_device_fault":true,"device_fault_levels":[1,2,3,4,5,16],'
   || '"counted_as_oos":true,"exclude_commanded_oos":true,"exclude_maintenance_oos":true,'
   || '"require_chargeable":false}')::jsonb,
   'telemetry_era', DATE '2024-01-01', 0.65)
ON CONFLICT (event_def_version) DO UPDATE SET
  event_definition = EXCLUDED.event_definition, label = EXCLUDED.label, criteria = EXCLUDED.criteria,
  window_mode = EXCLUDED.window_mode, telemetry_start = EXCLUDED.telemetry_start,
  cindex_floor = EXCLUDED.cindex_floor;

-- ---------------------------------------------------------------------
-- 5. feature-label alignment audit (governance) -- the notebook's [leak-check] result per run
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS ps5_feature_alignment_audit (
  id                    BIGSERIAL    PRIMARY KEY,
  city_id               city_code    NOT NULL REFERENCES cities(id),
  run_date              DATE         NOT NULL,
  mars_device_category  VARCHAR(12),                 -- TVM / GATE / VALIDATOR (NULL = all)
  event_def_version     VARCHAR(24),
  leak_check_passed     BOOLEAN,                     -- roll_fail_30d == strict as-of recount
  n_intervals_checked   INT,
  n_mismatches          INT,
  asof_gap_days_median  NUMERIC(10,1),               -- median (interval_start - last prior failure)
  created_at            TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
  UNIQUE (city_id, run_date, mars_device_category, event_def_version)
);

-- ---------------------------------------------------------------------
-- 6. latest-per-device view exposing the OOS-Set columns (NEW name; does not touch 05's view)
--    The /ps5/reliability API route reads this.
-- ---------------------------------------------------------------------
CREATE OR REPLACE VIEW v_ps5_reliability_oos_latest AS
SELECT e.city_id, e.device_id, e.mars_device_category,
       e.current_healthy_age_days, e.rul_standard_days, e.predicted_median_survival_days,
       e.hazard_score, e.risk_band, e.is_overdue, e.n_prior_failures,
       e.concordance_index, e.champion_model, e.weibull_shape, e.data_quality_gate_passed,
       e.days_since_hw_oos, e.roll_fail_30d,
       e.event_definition, e.event_def_version, e.feature_asof_date, e.as_of_date,
       d.label AS event_label, d.window_mode, d.telemetry_start, d.cindex_floor
FROM ps5_reliability_estimates e
JOIN (SELECT city_id, device_id, MAX(as_of_date) AS mx
        FROM ps5_reliability_estimates GROUP BY city_id, device_id) l
  ON l.city_id = e.city_id AND l.device_id = e.device_id AND l.mx = e.as_of_date
LEFT JOIN ps5_event_definition d ON d.event_def_version = e.event_def_version;

-- latest-per-serial view exposing the OOS-Set columns
CREATE OR REPLACE VIEW v_ps5_serial_oos_latest AS
SELECT s.city_id, s.device_id, s.component_serial_nbr, s.component_type, s.mars_device_category,
       s.component_age_days, s.device_oos_failures_total, s.risk_score, s.risk_tier,
       s.expected_component_rul_days, s.predicted_median_survival_days, s.is_overdue,
       s.event_definition, s.event_def_version, s.feature_asof_date, s.as_of_date
FROM ps5_serial_reliability s
JOIN (SELECT city_id, device_id, component_serial_nbr, MAX(as_of_date) AS mx
        FROM ps5_serial_reliability GROUP BY city_id, device_id, component_serial_nbr) l
  ON l.city_id = s.city_id AND l.device_id = s.device_id
 AND l.component_serial_nbr = s.component_serial_nbr AND l.mx = s.as_of_date;

COMMIT;

-- =====================================================================
-- POST-CHECKS (run manually after apply):
--   \d ps5_reliability_estimates    -- expect days_since_hw_oos, roll_fail_30d, event_def_version, feature_asof_date
--   SELECT * FROM ps5_event_definition;                 -- expect the 2026-07-23.v1 row
--   SELECT count(*) FROM v_ps5_reliability_oos_latest;  -- after the Lambda/writer loads a scoring run
-- =====================================================================
