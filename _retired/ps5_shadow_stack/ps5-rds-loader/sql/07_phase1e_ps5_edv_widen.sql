-- =====================================================================
-- CUBIC MARS Chicago -- Phase-1e  PS5  MIGRATION 07
-- Widen event_def_version + register the min-outage hybrid event definition.
--
-- Runs AFTER 06_phase1d. 100% ADDITIVE + IDEMPOTENT:
--   * event_def_version was VARCHAR(24) (sized for '2026-07-23.v1'); the live
--     v5.1 hybrid stamps '2026-07-24.oos_spine.minoutage5' (31 chars), so widen
--     to VARCHAR(64) on every grain + the ps5_event_definition PK.
--   * two OOS-latest views depend on event_def_version, so DROP them, ALTER,
--     then recreate verbatim (Postgres won't ALTER a column a view reads).
--   * register the hybrid event-definition row so the dashboard chip resolves.
--   * safe to re-run: DROP VIEW IF EXISTS / ALTER to same type / ON CONFLICT.
-- =====================================================================

BEGIN;

-- 1. drop the two views that read event_def_version (recreated below)
DROP VIEW IF EXISTS v_ps5_reliability_oos_latest;
DROP VIEW IF EXISTS v_ps5_serial_oos_latest;

-- 2. widen event_def_version everywhere it is stored (no table rewrite: varchar grow)
ALTER TABLE IF EXISTS ps5_reliability_estimates ALTER COLUMN event_def_version TYPE VARCHAR(64);
ALTER TABLE IF EXISTS ps5_serial_reliability    ALTER COLUMN event_def_version TYPE VARCHAR(64);
ALTER TABLE IF EXISTS ps5_scoring_runs          ALTER COLUMN event_def_version TYPE VARCHAR(64);
ALTER TABLE IF EXISTS ps5_event_definition      ALTER COLUMN event_def_version TYPE VARCHAR(64);

-- 3. recreate the OOS-latest views verbatim from 06 on the widened columns
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

-- 4. register the live hybrid event definition (device=hw OOS Set, min-outage>=5m,
--    commanded/maintenance excluded; GATE type scored on chargeable)
INSERT INTO ps5_event_definition
  (event_def_version, event_definition, label, criteria, window_mode, telemetry_start, cindex_floor)
VALUES
  ('2026-07-24.oos_spine.minoutage5', 'hw_oos_set',
   'Failure = hardware OOS (Set), min-outage >=5m; GATE scored on chargeable',
   ('{"fault_state":"Set","is_device_fault":true,"counted_as_oos":true,'
   || '"exclude_commanded_oos":true,"exclude_maintenance_oos":true,"min_outage_min":5,'
   || '"require_chargeable":false,"per_type":{"TVM":"hw_oos","VALIDATOR":"hw_oos","GATE":"chargeable"}}')::jsonb,
   'telemetry_era', DATE '2024-01-01', 0.65)
ON CONFLICT (event_def_version) DO UPDATE SET
  event_definition = EXCLUDED.event_definition, label = EXCLUDED.label, criteria = EXCLUDED.criteria,
  window_mode = EXCLUDED.window_mode, telemetry_start = EXCLUDED.telemetry_start,
  cindex_floor = EXCLUDED.cindex_floor;

COMMIT;

-- =====================================================================
-- POST-CHECKS:
--   SELECT event_def_version, label FROM ps5_event_definition ORDER BY 1;
--   SELECT count(*) FROM v_ps5_reliability_oos_latest;
-- =====================================================================
