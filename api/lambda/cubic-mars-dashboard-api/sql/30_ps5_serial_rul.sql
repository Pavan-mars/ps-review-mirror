-- =====================================================================
-- 30_ps5_serial_rul.sql   27-Jul-2026
--
-- WHY A NEW TABLE INSTEAD OF FIXING ps5_serial_reliability
-- --------------------------------------------------------
-- The real load committed 12 of 15 files. The three that failed all hit the
-- SAME legacy table:
--
--   gates      null value in column "as_of_date" violates not-null constraint
--   tvm        null value in column "as_of_date" violates not-null constraint
--   validators null value in column "component_serial_nbr" violates not-null
--
-- ps5_serial_reliability predates the PS5 v5 export. It carries eight columns
-- the export does not produce -- as_of_date, run_id, scored_at, failures_total,
-- chargeable_failure_count, failure_observed, component_type, id -- two of them
-- NOT NULL. It also still holds 12,904 rows from the sql/02 backfill, and
-- sql/02 INSERTs into it on every single migrate. Reshaping it would break that
-- file permanently.
--
-- So the export gets its own table, shaped to what it actually emits. This is
-- the approach that made ps5_device_rul land 17 of 17 columns with nothing
-- dropped; the legacy table is left alone and keeps serving whatever already
-- reads it.
--
-- NO PRIMARY KEY, AND THAT IS DELIBERATE
-- --------------------------------------
-- The validators export carries rows with a NULL COMPONENT_SERIAL_NBR -- 20,943
-- rows of which some components have no serial recorded upstream. A primary key
-- on (device_id, component_serial_nbr) cannot hold them, and the alternatives
-- are both worse: dropping the rows understates the validator fleet, and
-- substituting a sentinel serial invents identity that does not exist.
--
-- A NULL serial is a fact about the source data, not a defect to paper over. The
-- unique index below therefore covers only the rows that HAVE a serial, so real
-- duplicates are still caught while unserialed components are still stored.
-- =====================================================================

CREATE TABLE IF NOT EXISTS ps5_serial_rul (
  city_id city_code NOT NULL REFERENCES cities(id),
  device_type VARCHAR(12) NOT NULL,
  device_id VARCHAR(40) NOT NULL,
  component_serial_nbr VARCHAR(64),          -- NULL-able on purpose, see header
  component_type_name VARCHAR(120),
  mars_device_category VARCHAR(12),
  component_age_days NUMERIC(10,2),
  device_oos_failures_total INT,
  risk_score NUMERIC(12,6),
  risk_tier VARCHAR(16),
  expected_component_rul_days NUMERIC(10,2),
  predicted_median_survival_days NUMERIC(10,2),
  is_overdue BOOLEAN,
  event_definition VARCHAR(40),
  event_def_version VARCHAR(60),
  feature_asof_date DATE,
  serial_source VARCHAR(60)
);

-- Partial unique index: enforced only where a serial exists. Catches a genuine
-- duplicate without rejecting a component the source never serialised.
CREATE UNIQUE INDEX IF NOT EXISTS ux_ps5_serial_rul_key
  ON ps5_serial_rul (city_id, device_id, component_serial_nbr)
  WHERE component_serial_nbr IS NOT NULL;

CREATE INDEX IF NOT EXISTS ix_ps5_serial_rul_tier
  ON ps5_serial_rul (city_id, device_type, risk_tier, expected_component_rul_days);

-- Serving view. Two things a raw SELECT does not give:
--   1. has_serial separates identified components from unserialised ones, so a
--      count of "components at risk" cannot silently mix the two populations.
--   2. rul_rank_in_type ranks WITHIN a device type. Validator and TVM survival
--      models are fitted separately, so their RUL figures are not on one scale
--      and a fleet-wide ordering would compare numbers with no common unit.
CREATE OR REPLACE VIEW v_ps5_serial_rul AS
SELECT
  s.*,
  (s.component_serial_nbr IS NOT NULL)                       AS has_serial,
  RANK() OVER (PARTITION BY s.city_id, s.device_type
               ORDER BY s.expected_component_rul_days ASC NULLS LAST)
                                                             AS rul_rank_in_type,
  COUNT(*) OVER (PARTITION BY s.city_id, s.device_type)      AS n_components_in_type,
  -- Overdue AND short remaining life is the actionable intersection; either
  -- alone is a watch item, both together is a work order.
  (s.is_overdue AND s.expected_component_rul_days IS NOT NULL
     AND s.expected_component_rul_days <= 30)                AS act_now
FROM ps5_serial_rul s;
