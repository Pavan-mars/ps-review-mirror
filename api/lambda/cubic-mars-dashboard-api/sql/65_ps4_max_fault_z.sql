-- =====================================================================
-- PS4: land max_fault_z, the column the alert rules actually use.
-- 23-Sep-2026. Additive and idempotent. Apply BEFORE deploying the
-- PS4 loader that lists max_fault_z in COLS_DEVICE.
--
-- WHY THIS EXISTS, AND WHY IT IS NOT OPTIONAL.
-- The PS4 weekly notebook used to feed its alert rules from max_abs_z, the
-- largest ABSOLUTE z across five metrics. Three of the five signals are
-- deliberately one-sided -- signal_oos, signal_hardware and signal_tap_quality
-- test `>= 3.0`, not `abs(...) >= 3.0` -- because only the high side is a
-- fault. So a device with oos_rate_z = -4, meaning FAR FEWER out-of-service
-- events than its own baseline, scored max_abs_z = 4, cleared the
-- `max_abs_z >= 4.0` door and raised an alert with no signal set at all. That
-- is the complete explanation for the 50 signal-less alerts in the 20-Sep run
-- (GATE 3, TVM 6, VALIDATOR 41): devices flagged for being unusually healthy.
--
-- The notebook now computes max_fault_z -- abs() only on the two-sided metrics
-- (event_log, latency_ms), the signed z on the one-sided ones -- and the rules
-- read that. max_abs_z is unchanged and still published, because it is a
-- genuine diagnostic.
--
-- THE PART THAT MAKES THIS A PREREQUISITE RATHER THAN A FOLLOW-UP.
-- Until today `max_abs_z >= 4` implied actionable, which implied severity >=
-- High. Two dashboard consumers lean on that invariant: they pick a device's
-- worst week with argmax(max_abs_z) and render THAT week's severity --
-- V4Device360.jsx:475-477 (shown at :746 as "Worst severity") and
-- V4DeviceW.js:88-90 (consumed at :148 as ps4_severity). The rule change
-- breaks the invariant, so those selectors now preferentially land on a
-- healthy week carrying a large negative one-sided z and report its severity
-- (Normal) in place of the device's genuinely worst week:
--
--     week A  oos_rate_z = -5.0  healthy   max_abs_z 5.0   severity Normal
--     week B  oos_rate_z = +4.2  faulty    max_abs_z 4.2   severity High
--
-- argmax picks A. The front end cannot be repointed until this column exists
-- in Aurora, which is why the DDL leads and the JSX follows.
--
-- BOTH VIEWS USE EXPLICIT COLUMN LISTS, so an ALTER on the tables alone would
-- not surface the column. They are recreated below. CREATE OR REPLACE VIEW can
-- only APPEND columns -- inserting one mid-list fails with "cannot change name
-- of view column" -- so max_fault_z goes last in each, beside run_id rather
-- than beside max_abs_z where it belongs logically. That is the price of not
-- dropping views that other objects may depend on.
--
-- Ship through the handler's apply_sql action, dry-run first.
-- =====================================================================

-- ---------------------------------------------------------------------
-- 1. The two tables the loader writes. Nullable on purpose: every row
--    written before the notebook re-runs has no value for this, and a
--    NOT NULL default would invent one.
-- ---------------------------------------------------------------------
ALTER TABLE ps4_weekly_device_summary
  ADD COLUMN IF NOT EXISTS max_fault_z DOUBLE PRECISION;

ALTER TABLE ps4_weekly_alerts
  ADD COLUMN IF NOT EXISTS max_fault_z DOUBLE PRECISION;

-- ---------------------------------------------------------------------
-- 2. Republish both views with the column appended. Every existing column
--    keeps its name, type and position, which is what CREATE OR REPLACE
--    requires.
-- ---------------------------------------------------------------------
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
  a.pipeline_version, a.asof_date, a.run_id,
  -- Appended, not inserted. Prefer this over max_abs_z when ranking a device's
  -- weeks: it is the value the alert rules read. NULL for every row loaded
  -- before the notebook re-runs, so consumers must fall back to max_abs_z.
  a.max_fault_z
FROM ps4_weekly_alerts a
JOIN v_ps4_v3_current cur
  ON cur.city_id = a.city_id AND cur.pipeline_version = a.pipeline_version;

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
  s.pipeline_version, s.asof_date, s.run_id,
  s.max_fault_z
FROM ps4_weekly_device_summary s
JOIN v_ps4_v3_current cur
  ON cur.city_id = s.city_id AND cur.pipeline_version = s.pipeline_version;
