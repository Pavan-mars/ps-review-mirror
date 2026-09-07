-- 02-Sep-2026. device_level_aggregation -- one row per device, carrying the
-- identity keys and the four W dimensions the Device 360 popup reads.
--
-- WHY ONE ROW PER DEVICE, NOT PER (DEVICE, COMPONENT).
-- A device carries 1..7 components. Putting the component serial in the KEY
-- repeats every device measure on every component row, and any SUM or COUNT
-- over the table then inflates. This estate has paid for that once:
-- notebooks/dim/export_device_serial_daily.py records the 27-Jul extract
-- inflating a fleet total 2.60x (103,927,823 against a true 39,969,550), and
-- the fix was two outputs at two grains. So component lists live in JSONB
-- columns here -- present, queryable with jsonb operators, incapable of
-- fanning a count out. Precedent in this schema: ps1_cross_wired_daily.extra.
--
-- WHAT IT DOES NOT COVER. The composite /ps1/device-360 also returns causation
-- matrices and the ps3_v2 rootcause family, which are DERIVED in Python rather
-- than read from a table. Those are deliberately absent, and the front end
-- falls back to the composite for them. This is the fast path for identity,
-- the four W dimensions and the headline measures -- not a replacement.
--
-- Apply with {"action":"apply_sql","file":"59_device_level_aggregation.sql"}
-- (dry_run first). RE-APPLYING IS THE REFRESH: the DELETE+INSERT below is the
-- reload path, so this file is run again whenever a PS family reloads.
-- NOT in the migrate() tuple -- that list is frozen at sql/48 by design.

CREATE TABLE IF NOT EXISTS device_level_aggregation (
    city_id                 text NOT NULL DEFAULT 'CHI',
    device_id               text NOT NULL,
    device_key              text,
    device_name             text,
    bus_id                  text,
    bus_device_flag         boolean,
    serial_number           text,
    component_serial_nbr    text,
    component_type          text,
    cmdb_ci_sys_id          text,
    incident_number         text,

    facility_id             text,
    facility_name           text,
    operator_id             text,
    operator_name           text,
    mars_device_category    text,
    device_type_name        text,
    transit_mode_name       text,

    dim_as_of_date          date,
    sn_latest_opened_at     timestamptz,
    ps1_transit_day         date,
    ps1_last_failure_date   date,
    ps3_days_since_incident double precision,
    ps4_week_start          date,
    ps5_asof_date           date,
    vintage_oldest          date,
    vintage_newest          date,
    vintage_spread_days     integer,

    ps1_fail_prob           numeric,
    ps1_predicted           smallint,
    ps1_risk_tier           text,
    ps2_cascade_days        integer,
    ps2_avg_chain_len       numeric,
    ps2_max_chain_len       integer,
    ps2_worst_window        text,
    ps3_incident_count      bigint,
    ps3_critical_rate       double precision,
    ps3_risk_band           text,
    ps3_recurrence_30d      bigint,
    ps3_predicted_severity  text,
    ps3_action_band         text,
    ps3_action_priority     double precision,
    ps4_severity            text,
    ps4_anomaly_score_max   double precision,
    ps4_is_actionable_week  smallint,
    ps4_anomaly_types       text,
    ps4_cluster_id          integer,
    ps5_rul_days            numeric,
    ps5_median_survival_days numeric,
    ps5_hazard_score        numeric,
    ps5_risk_band           text,
    ps5_is_overdue          boolean,
    sn_incident_count       bigint,
    signal_count            integer,

    -- WHY is two different claims and they are not interchangeable. The SHAP
    -- columns are MODEL EXPLANATION. ps2_dom_subsystem is an observed pattern.
    -- Confirmed cause would come from the ps3 linked_* family, which is null
    -- across this estate until ServiceNow incident linkage runs.
    ps2_dom_subsystem       text,
    ps1_shap_feat1          text,
    ps1_shap_val1           numeric,
    ps1_shap_feat2          text,
    ps1_shap_val2           numeric,
    ps1_shap_feat3          text,
    ps1_shap_val3           numeric,

    ps1_drivers_json        jsonb,
    ps3_components_json     jsonb,
    ps5_components_json     jsonb,

    computed_at             timestamptz DEFAULT now(),
    PRIMARY KEY (city_id, device_id)
);

CREATE INDEX IF NOT EXISTS ix_dla_category ON device_level_aggregation (city_id, mars_device_category);
CREATE INDEX IF NOT EXISTS ix_dla_signals  ON device_level_aggregation (city_id, signal_count DESC);
CREATE INDEX IF NOT EXISTS ix_dla_cmdb     ON device_level_aggregation (cmdb_ci_sys_id);
CREATE INDEX IF NOT EXISTS ix_dla_serial   ON device_level_aggregation (serial_number);

DELETE FROM device_level_aggregation;

INSERT INTO device_level_aggregation (
  city_id, device_id, device_key, device_name, bus_id, bus_device_flag,
  serial_number, component_serial_nbr, component_type, cmdb_ci_sys_id, incident_number,
  facility_id, facility_name, operator_id, operator_name,
  mars_device_category, device_type_name, transit_mode_name,
  dim_as_of_date, sn_latest_opened_at,
  ps1_transit_day, ps1_last_failure_date, ps3_days_since_incident,
  ps4_week_start, ps5_asof_date, vintage_oldest, vintage_newest, vintage_spread_days,
  ps1_fail_prob, ps1_predicted, ps1_risk_tier,
  ps2_cascade_days, ps2_avg_chain_len, ps2_max_chain_len, ps2_worst_window,
  ps3_incident_count, ps3_critical_rate, ps3_risk_band, ps3_recurrence_30d,
  ps3_predicted_severity, ps3_action_band, ps3_action_priority,
  ps4_severity, ps4_anomaly_score_max, ps4_is_actionable_week, ps4_anomaly_types, ps4_cluster_id,
  ps5_rul_days, ps5_median_survival_days, ps5_hazard_score, ps5_risk_band, ps5_is_overdue,
  sn_incident_count, signal_count, ps2_dom_subsystem,
  ps1_shap_feat1, ps1_shap_val1, ps1_shap_feat2, ps1_shap_val2, ps1_shap_feat3, ps1_shap_val3,
  ps1_drivers_json, ps3_components_json, ps5_components_json
)
SELECT
  v.city_id, v.device_id, v.device_key, v.device_name, c.bus_id,
  c.bus_device_flag,
  v.serial_number, v.component_serial_nbr, v.component_type, v.cmdb_ci_sys_id,
  v.sn_latest_incident,
  v.facility_id, v.facility_name, v.operator_id, v.operator_name,
  v.mars_device_category, v.device_type_name, v.transit_mode_name,
  v.dim_as_of_date, v.sn_latest_opened_at,
  v.ps1_transit_day, v.ps1_last_failure_date, v.ps3_days_since_incident,
  v.ps4_week_start, v.ps5_asof_date,
  LEAST(v.ps1_transit_day, v.ps4_week_start, v.ps5_asof_date),
  GREATEST(v.dim_as_of_date, v.ps1_transit_day, v.ps4_week_start, v.ps5_asof_date),
  (GREATEST(v.dim_as_of_date, v.ps1_transit_day, v.ps4_week_start, v.ps5_asof_date)
   - LEAST(v.ps1_transit_day, v.ps4_week_start, v.ps5_asof_date)),
  v.ps1_fail_prob, v.ps1_predicted, v.ps1_risk_tier,
  v.ps2_cascade_days, v.ps2_avg_chain_len, v.ps2_max_chain_len, v.ps2_worst_window,
  v.ps3_incident_count, v.ps3_critical_rate, v.ps3_risk_band, v.ps3_recurrence_30d,
  v.ps3_predicted_severity, v.ps3_action_band, v.ps3_action_priority,
  v.ps4_severity, v.ps4_anomaly_score_max, v.ps4_is_actionable_week,
  v.ps4_anomaly_types, v.ps4_cluster_id,
  v.ps5_rul_days, v.ps5_median_survival_days, v.ps5_hazard_score,
  v.ps5_risk_band, v.ps5_is_overdue,
  v.sn_incident_count,
  ((v.ps3_action_band IS NOT NULL)::int
   + (v.ps4_severity IS NOT NULL AND v.ps4_severity <> 'Normal')::int
   + COALESCE(v.ps5_is_overdue, false)::int
   + (COALESCE(v.sn_incident_count, 0) > 0)::int),
  v.ps2_dom_subsystem,
  x.shap_feat1, x.shap_val1, x.shap_feat2, x.shap_val2, x.shap_feat3, x.shap_val3,
  (SELECT jsonb_agg(t.d) FROM (
      SELECT jsonb_build_object('rank', 1, 'feature', x.shap_feat1, 'shap', x.shap_val1) AS d
      WHERE x.shap_feat1 IS NOT NULL
      UNION ALL
      SELECT jsonb_build_object('rank', 2, 'feature', x.shap_feat2, 'shap', x.shap_val2)
      WHERE x.shap_feat2 IS NOT NULL
      UNION ALL
      SELECT jsonb_build_object('rank', 3, 'feature', x.shap_feat3, 'shap', x.shap_val3)
      WHERE x.shap_feat3 IS NOT NULL
   ) t),
  (SELECT jsonb_agg(jsonb_build_object(
            'serial_number', c.serial_number,
            'component_label', c.component_label,
            'semantics', c.component_label_semantics,
            'incident_count', c.incident_count,
            'critical_rate', c.critical_rate,
            'recurrence_30d', c.recurrence_30d,
            'latest_incident_at', c.latest_incident_at))
     FROM ps3_v2_device_serial_component c
    WHERE c.city_id = v.city_id AND c.device_id = v.device_id),
  (SELECT jsonb_agg(jsonb_build_object(
            'component_serial_nbr', r.component_serial_nbr,
            'component_type_name', r.component_type_name,
            'component_age_days', r.component_age_days,
            'risk_tier', r.risk_tier,
            'risk_score', r.risk_score,
            'expected_component_rul_days', r.expected_component_rul_days,
            'predicted_median_survival_days', r.predicted_median_survival_days,
            'is_overdue', r.is_overdue,
            'serial_source', r.serial_source))
     FROM ps5_serial_rul r
    WHERE r.city_id = v.city_id AND r.device_id = v.device_id)
FROM v_device_360 v
-- bus_id and bus_device_flag live on the dimension, not on v_device_360's
-- projection -- the view was written for the cross-PS columns and never
-- carried them. Read them from the spine rather than widening the view,
-- because CREATE OR REPLACE VIEW cannot change a view's column set and
-- widening it would mean dropping and rebuilding every dependent object.
LEFT JOIN v_device_central c
       ON c.city_id = v.city_id AND c.device_id = v.device_id
LEFT JOIN LATERAL (
  SELECT w.shap_feat1, w.shap_val1, w.shap_feat2, w.shap_val2, w.shap_feat3, w.shap_val3
    FROM ps1_cross_wired_daily w
   WHERE w.city_id::text = v.city_id AND w.device_id = v.device_id
   ORDER BY w.transit_day DESC, w.asof_date DESC
   LIMIT 1
) x ON TRUE;

ANALYZE device_level_aggregation;
