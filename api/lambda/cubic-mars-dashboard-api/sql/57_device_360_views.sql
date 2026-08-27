-- 27-Aug-2026. v_device_360 : the central device layer (Phase 1, ADDITIVE).
-- dim_device_incident_cmdb (latest snapshot) = the spine, LEFT JOINed to the
-- latest per-device signal from each PS chain on (city_id, device_id), plus
-- device_360_validation -- the key-conformance scorecard (DELETE+INSERT, so
-- RE-APPLYING THIS FILE refreshes the numbers; do that after each dim load
-- you want to measure). Fanout guard must read 0 before any tab repoints.
--
-- Apply via:  {"action":"apply_sql","file":"57_device_360_views.sql"}
-- (dry_run first), AFTER 56_dim_device_incident_cmdb.sql. NOT in the migrate()
-- tuple (frozen at sql/48 by design).
--
-- Join-key policy v1: (city_id, device_id) only. ps1_cross_wired_daily and
-- ps4_cluster_assignments rows with NULL device_id (device_key-only) are
-- EXCLUDED and counted in the validation table; a device_key fallback join is
-- v2 material only if those counts prove material.
-- CREATE OR REPLACE VIEW cannot change a view's column set -- a later revision
-- that adds/removes columns must DROP VIEW first (the sql/05 lesson).
-- ---- the spine: latest dim snapshot per city --------------------------------
CREATE OR REPLACE VIEW v_device_central AS
SELECT d.*
FROM dim_device_incident_cmdb d
WHERE d.as_of_date = (SELECT MAX(d2.as_of_date)
                      FROM dim_device_incident_cmdb d2
                      WHERE d2.city_id = d.city_id);

-- ---- PS1: latest cross-wired daily row per device ---------------------------
CREATE OR REPLACE VIEW v_ps1_device_latest AS
SELECT DISTINCT ON (x.city_id, x.device_id)
       x.city_id::text AS city_id, x.device_id, x.device_key,
       x.transit_day       AS ps1_transit_day,
       x.asof_date         AS ps1_asof_date,
       x.ps1_fail_prob, x.ps1_predicted, x.ps1_risk_tier,
       x.is_prob_anomaly   AS ps1_is_prob_anomaly,
       x.threshold_used    AS ps1_threshold_used,
       x.avg_rolling_mttr_30d_min AS ps1_mttr_30d_min,
       x.last_failure_date_s28    AS ps1_last_failure_date
FROM ps1_cross_wired_daily x
WHERE x.device_id IS NOT NULL
ORDER BY x.city_id, x.device_id, x.transit_day DESC, x.asof_date DESC;

-- ---- PS2: latest cascade-catalog row per device (top-200 universe) ----------
CREATE OR REPLACE VIEW v_ps2_device_latest AS
SELECT DISTINCT ON (c.city_id, c.device_id)
       c.city_id::text AS city_id, c.device_id,
       c.cascade_days   AS ps2_cascade_days,
       c.avg_chain_len  AS ps2_avg_chain_len,
       c.max_chain_len  AS ps2_max_chain_len,
       c.dom_subsystem  AS ps2_dom_subsystem,
       c.dom_error_code AS ps2_dom_error_code,
       c.worst_window   AS ps2_worst_window,
       c.computed_date  AS ps2_computed_date
FROM ps2_device_catalog c
ORDER BY c.city_id, c.device_id, c.computed_date DESC;

-- ---- PS3: latest reliability + latest severity-action row per device --------
CREATE OR REPLACE VIEW v_ps3_device_latest AS
SELECT DISTINCT ON (r.city_id, r.device_id)
       r.city_id::text AS city_id, r.device_id,
       r.incident_count            AS ps3_incident_count,
       r.critical_rate             AS ps3_critical_rate,
       r.reliability_risk_band     AS ps3_risk_band,
       r.days_since_latest_incident AS ps3_days_since_incident,
       r.recent_30d_recurrence     AS ps3_recurrence_30d,
       r.loaded_at                 AS ps3_loaded_at
FROM ps3_v2_device_reliability r
ORDER BY r.city_id, r.device_id, r.loaded_at DESC;

CREATE OR REPLACE VIEW v_ps3_action_latest AS
SELECT DISTINCT ON (q.city_id, q.device_id)
       q.city_id::text AS city_id, q.device_id,
       q.predicted_severity    AS ps3_predicted_severity,
       q.critical_probability  AS ps3_critical_probability,
       q.action_band           AS ps3_action_band,
       q.action_priority_score AS ps3_action_priority,
       q.loaded_at             AS ps3_action_loaded_at
FROM ps3_v2_severity_action_queue q
ORDER BY q.city_id, q.device_id, q.loaded_at DESC;

-- ---- PS4: latest weekly anomaly summary + latest cluster per device ---------
CREATE OR REPLACE VIEW v_ps4_device_latest AS
SELECT DISTINCT ON (w.city_id, w.device_id)
       w.city_id::text AS city_id, w.device_id,
       w.week_start          AS ps4_week_start,
       w.anomaly_score_max   AS ps4_anomaly_score_max,
       w.anomaly_score_p95   AS ps4_anomaly_score_p95,
       w.severity            AS ps4_severity,
       w.is_actionable_week  AS ps4_is_actionable_week,
       w.anomaly_types       AS ps4_anomaly_types,
       w.dominant_cluster_id AS ps4_dominant_cluster_id
FROM ps4_weekly_device_summary w
ORDER BY w.city_id, w.device_id, w.week_start DESC;

CREATE OR REPLACE VIEW v_ps4_cluster_latest AS
SELECT DISTINCT ON (a.city_id, a.device_id)
       a.city_id::text AS city_id, a.device_id,
       a.cluster_id         AS ps4_cluster_id,
       a.cluster_confidence AS ps4_cluster_confidence,
       a.engine             AS ps4_cluster_engine,
       a.asof_date          AS ps4_cluster_asof
FROM ps4_cluster_assignments a
WHERE a.device_id IS NOT NULL
ORDER BY a.city_id, a.device_id, a.asof_date DESC;

-- ---- PS5: latest RUL/survival row per device --------------------------------
CREATE OR REPLACE VIEW v_ps5_device_latest AS
SELECT DISTINCT ON (p.city_id, p.device_id)
       p.city_id::text AS city_id, p.device_id,
       p.rul_standard_days             AS ps5_rul_days,
       p.predicted_median_survival_days AS ps5_median_survival_days,
       p.hazard_score                  AS ps5_hazard_score,
       p.risk_band                     AS ps5_risk_band,
       p.is_overdue                    AS ps5_is_overdue,
       p.days_since_hw_oos             AS ps5_days_since_hw_oos,
       p.feature_asof_date             AS ps5_asof_date
FROM ps5_device_rul p
ORDER BY p.city_id, p.device_id, p.feature_asof_date DESC NULLS LAST;

-- ---- the 360: spine LEFT JOIN every PS latest -------------------------------
CREATE OR REPLACE VIEW v_device_360 AS
SELECT
    d.city_id, d.device_id, d.device_key, d.device_name,
    d.mars_device_category, d.device_type_name, d.transit_mode_name,
    d.facility_id, d.facility_name, d.operator_id, d.operator_name,
    d.serial_number, d.component_serial_nbr, d.component_type,
    d.cmdb_ci_sys_id,
    d.incident_number  AS sn_latest_incident,
    d.opened_at        AS sn_latest_opened_at,
    d.incident_count   AS sn_incident_count,
    d.as_of_date       AS dim_as_of_date,
    p1.ps1_fail_prob, p1.ps1_predicted, p1.ps1_risk_tier,
    p1.ps1_is_prob_anomaly, p1.ps1_mttr_30d_min, p1.ps1_last_failure_date,
    p1.ps1_transit_day,
    p2.ps2_cascade_days, p2.ps2_avg_chain_len, p2.ps2_max_chain_len,
    p2.ps2_dom_subsystem, p2.ps2_worst_window,
    p3.ps3_incident_count, p3.ps3_critical_rate, p3.ps3_risk_band,
    p3.ps3_days_since_incident, p3.ps3_recurrence_30d,
    q3.ps3_predicted_severity, q3.ps3_action_band, q3.ps3_action_priority,
    p4.ps4_anomaly_score_max, p4.ps4_severity, p4.ps4_is_actionable_week,
    p4.ps4_anomaly_types, p4.ps4_week_start,
    k4.ps4_cluster_id, k4.ps4_cluster_confidence,
    p5.ps5_rul_days, p5.ps5_median_survival_days, p5.ps5_hazard_score,
    p5.ps5_risk_band, p5.ps5_is_overdue, p5.ps5_asof_date
FROM v_device_central d
LEFT JOIN v_ps1_device_latest p1 ON p1.city_id = d.city_id AND p1.device_id = d.device_id
LEFT JOIN v_ps2_device_latest p2 ON p2.city_id = d.city_id AND p2.device_id = d.device_id
LEFT JOIN v_ps3_device_latest p3 ON p3.city_id = d.city_id AND p3.device_id = d.device_id
LEFT JOIN v_ps3_action_latest q3 ON q3.city_id = d.city_id AND q3.device_id = d.device_id
LEFT JOIN v_ps4_device_latest p4 ON p4.city_id = d.city_id AND p4.device_id = d.device_id
LEFT JOIN v_ps4_cluster_latest k4 ON k4.city_id = d.city_id AND k4.device_id = d.device_id
LEFT JOIN v_ps5_device_latest p5 ON p5.city_id = d.city_id AND p5.device_id = d.device_id;

-- ============================================================================
-- Key-conformance validation. Refreshed on every migrate() run (DELETE+INSERT).
-- Read via GET /device/360/validation (route spec) -- and its row count is
-- visible in the catalog action immediately.
-- ============================================================================
CREATE TABLE IF NOT EXISTS device_360_validation (
    check_name  text NOT NULL,
    metric      text NOT NULL,
    value       numeric,
    computed_at timestamptz DEFAULT now()
);

DELETE FROM device_360_validation;

INSERT INTO device_360_validation (check_name, metric, value)
SELECT 'dim', 'rows_latest_snapshot', COUNT(*) FROM v_device_central
UNION ALL SELECT 'dim', 'cmdb_mapped', COUNT(*) FROM v_device_central WHERE cmdb_ci_sys_id IS NOT NULL
UNION ALL SELECT 'dim', 'with_incidents', COUNT(*) FROM v_device_central WHERE incident_count > 0
-- FANOUT GUARD: must be exactly 0. If not, a PS join duplicated spine rows.
UNION ALL SELECT 'fanout', 'v360_rows_minus_central',
    (SELECT COUNT(*) FROM v_device_360) - (SELECT COUNT(*) FROM v_device_central)
-- per-PS: how many devices each chain scores, and how many resolve to the spine
UNION ALL SELECT 'ps1', 'latest_devices', (SELECT COUNT(*) FROM v_ps1_device_latest)
UNION ALL SELECT 'ps1', 'matched_to_dim',
    (SELECT COUNT(*) FROM v_ps1_device_latest p JOIN v_device_central d
      ON d.city_id = p.city_id AND d.device_id = p.device_id)
UNION ALL SELECT 'ps1', 'xw_rows_null_device_id_latest_asof',
    (SELECT COUNT(*) FROM ps1_cross_wired_daily
      WHERE device_id IS NULL
        AND asof_date = (SELECT MAX(asof_date) FROM ps1_cross_wired_daily))
UNION ALL SELECT 'ps2', 'latest_devices', (SELECT COUNT(*) FROM v_ps2_device_latest)
UNION ALL SELECT 'ps2', 'matched_to_dim',
    (SELECT COUNT(*) FROM v_ps2_device_latest p JOIN v_device_central d
      ON d.city_id = p.city_id AND d.device_id = p.device_id)
UNION ALL SELECT 'ps3', 'latest_devices', (SELECT COUNT(*) FROM v_ps3_device_latest)
UNION ALL SELECT 'ps3', 'matched_to_dim',
    (SELECT COUNT(*) FROM v_ps3_device_latest p JOIN v_device_central d
      ON d.city_id = p.city_id AND d.device_id = p.device_id)
UNION ALL SELECT 'ps3', 'action_queue_devices', (SELECT COUNT(*) FROM v_ps3_action_latest)
UNION ALL SELECT 'ps3', 'serials_in_dim',
    (SELECT COUNT(DISTINCT s.serial_number) FROM ps3_v2_device_serial s
      JOIN v_device_central d
        ON s.serial_number = d.serial_number OR s.serial_number = d.component_serial_nbr)
UNION ALL SELECT 'ps4', 'weekly_devices', (SELECT COUNT(*) FROM v_ps4_device_latest)
UNION ALL SELECT 'ps4', 'weekly_matched_to_dim',
    (SELECT COUNT(*) FROM v_ps4_device_latest p JOIN v_device_central d
      ON d.city_id = p.city_id AND d.device_id = p.device_id)
UNION ALL SELECT 'ps4', 'cluster_rows_null_device_id_latest_asof',
    (SELECT COUNT(*) FROM ps4_cluster_assignments
      WHERE device_id IS NULL
        AND asof_date = (SELECT MAX(asof_date) FROM ps4_cluster_assignments))
UNION ALL SELECT 'ps5', 'rul_devices', (SELECT COUNT(*) FROM v_ps5_device_latest)
UNION ALL SELECT 'ps5', 'matched_to_dim',
    (SELECT COUNT(*) FROM v_ps5_device_latest p JOIN v_device_central d
      ON d.city_id = p.city_id AND d.device_id = p.device_id);
