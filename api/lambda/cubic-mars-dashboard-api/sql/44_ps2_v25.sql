-- =====================================================================
-- sql/44_ps2_v25.sql
--
-- The 20 tables published by PS2_Failure_Patterns_v2_5_2/3_Storage_Safe.
-- PURELY ADDITIVE. Nothing here drops, alters or renames anything. The 27
-- existing PS2 tables and ps2_v2_run_quality are untouched and remain the
-- plan-B set.
--
-- COLLISION CHECK, run against the real bucket listing on 02-Aug-2026:
--   ps2_outputs/ holds 28 directories. None of the 20 names below appears in
--   it. The nearest miss is ps2_v2_run_quality, which is an older generation
--   and is NOT one of these tables -- this notebook writes ps2_v25_run_quality.
--
-- EVERY COLUMN NAME AND TYPE IS READ, NOT INFERRED. Types come from the pandas
-- dtype map embedded in each part-0.parquet by pdf.to_parquet(); primary keys
-- come from each manifest.json's primary_keys. Both were dumped from
-- ps2_replay_outputs run bb211e9d-c65d-4e7d-8d04-f597b6ede968 (as-of
-- 2026-04-11) before a line of this file was written.
--
-- FOUR DELIBERATE DESIGN CHOICES
--
-- 1. NATURAL PRIMARY KEY, NO SURROGATE id.
--    A departure from sql/42's "id BIGSERIAL PRIMARY KEY". The loader's
--    find_pk_collapse() does keys = [k for k in pk if k in use], and id is
--    stripped from use a few lines earlier -- so a surrogate PK disables that
--    protection entirely. A natural PK switches it on, and it makes the rule
--    "nothing downstream references id" structurally true rather than a
--    convention someone has to remember. Safe because
--    write_dashboard_table() refuses to publish if any declared key is null
--    or if any key group duplicates.
--
-- 2. computed_date ON EVERY TABLE.
--    The loader fills it from the S3 partition
--    (o.setdefault("computed_date", cdate) when the target has the column).
--    Without it nothing in Aurora records which run a row came from and
--    /ps2/status has nothing to read.
--
-- 3. TEXT FOR EVERY STRING COLUMN.
--    The dtype map gives types, not maximum lengths. In PostgreSQL TEXT and
--    VARCHAR(n) are the same on disk and in speed, so a length cap here would
--    buy nothing and would introduce "value too long" as a live failure mode
--    the first time a subsystem name or cluster id grows.
--
-- 4. EVERY IDENTIFIER QUOTED.
--    precision and source are both column names here. This project already
--    lost a load to an unquoted "window" AFTER eight tables were staged.
--    Quoting the whole set removes the class rather than the instance.
--
-- TIMESTAMP, not TIMESTAMPTZ: the loader's norm() emits naive isoformat
-- strings, so TIMESTAMPTZ would silently apply the server timezone.
--
-- AFTER APPLYING, ADD THIS FILE TO migrate()'s TUPLE in the dashboard-api
-- handler. migrate() reads a hardcoded filename tuple, not the sql/ directory.
-- A file that is not in the tuple is never executed and migrate() still
-- reports success.
-- =====================================================================

-- ps2_v25_category_profile: 5 rows in run bb211e9d, PK (device_category_raw, device_category)
CREATE TABLE IF NOT EXISTS ps2_v25_category_profile (
  "city_id"            city_code NOT NULL REFERENCES cities(id),
  "device_category_raw" TEXT,
  "device_category"     TEXT,
  "event_count"         BIGINT,
  "device_count"        BIGINT,
  "first_event_ts"      TIMESTAMP,
  "last_event_ts"       TIMESTAMP,
  "is_mapped"           BOOLEAN,
  "is_target_scope"     BOOLEAN,
  "run_id"              TEXT,
  "run_ts_utc"          TIMESTAMP,
  "as_of_ts"            TIMESTAMP,
  "source_start_ts"     TIMESTAMP,
  "source_end_ts"       TIMESTAMP,
  "notebook_version"    TEXT,
  "quality_status"      TEXT,
  "run_mode"            TEXT,
  "run_disposition"     TEXT,
  "output_scope"        TEXT,
  "is_production"       BOOLEAN,
  "computed_date"      DATE
  , PRIMARY KEY ("city_id", "device_category_raw", "device_category")
);

-- ps2_v25_failure_definition_alignment: 4 rows in run bb211e9d, PK (device_category)
CREATE TABLE IF NOT EXISTS ps2_v25_failure_definition_alignment (
  "city_id"            city_code NOT NULL REFERENCES cities(id),
  "silver_ps1_failure_device_days" BIGINT,
  "governed_oos_episode_device_days" BIGINT,
  "overlap_device_days" BIGINT,
  "silver_only_device_days" BIGINT,
  "governed_only_device_days" BIGINT,
  "device_category"     TEXT,
  "silver_to_governed_overlap_rate" DOUBLE PRECISION,
  "governed_to_silver_overlap_rate" DOUBLE PRECISION,
  "definition_jaccard"  DOUBLE PRECISION,
  "run_id"              TEXT,
  "run_ts_utc"          TIMESTAMP,
  "as_of_ts"            TIMESTAMP,
  "source_start_ts"     TIMESTAMP,
  "source_end_ts"       TIMESTAMP,
  "notebook_version"    TEXT,
  "quality_status"      TEXT,
  "run_mode"            TEXT,
  "run_disposition"     TEXT,
  "output_scope"        TEXT,
  "is_production"       BOOLEAN,
  "computed_date"      DATE
  , PRIMARY KEY ("city_id", "device_category")
);

-- ps2_v25_failure_horizon_profile: 9 rows in run bb211e9d, PK (device_category, lead_day)
CREATE TABLE IF NOT EXISTS ps2_v25_failure_horizon_profile (
  "city_id"            city_code NOT NULL REFERENCES cities(id),
  "device_category"     TEXT,
  "lead_day"            INTEGER,
  "positive_device_days_at_lead" BIGINT,
  "eligible_device_days" BIGINT,
  "positive_rate_at_lead" DOUBLE PRECISION,
  "label_definition"    TEXT,
  "run_id"              TEXT,
  "run_ts_utc"          TIMESTAMP,
  "as_of_ts"            TIMESTAMP,
  "source_start_ts"     TIMESTAMP,
  "source_end_ts"       TIMESTAMP,
  "notebook_version"    TEXT,
  "quality_status"      TEXT,
  "run_mode"            TEXT,
  "run_disposition"     TEXT,
  "output_scope"        TEXT,
  "is_production"       BOOLEAN,
  "computed_date"      DATE
  , PRIMARY KEY ("city_id", "device_category", "lead_day")
);

-- ps2_v25_failure_label_daily: 624 rows in run bb211e9d, PK (label_date, device_category)
CREATE TABLE IF NOT EXISTS ps2_v25_failure_label_daily (
  "city_id"            city_code NOT NULL REFERENCES cities(id),
  "label_date"          DATE,
  "device_category"     TEXT,
  "eligible_device_days" BIGINT,
  "positive_device_days" BIGINT,
  "eligible_devices"    BIGINT,
  "positive_devices"    BIGINT,
  "future_hardware_oos_set_events" BIGINT,
  "median_hours_to_next_oos" DOUBLE PRECISION,
  "p90_hours_to_next_oos" DOUBLE PRECISION,
  "negative_device_days" BIGINT,
  "label_positive_rate" DOUBLE PRECISION,
  "run_id"              TEXT,
  "run_ts_utc"          TIMESTAMP,
  "as_of_ts"            TIMESTAMP,
  "source_start_ts"     TIMESTAMP,
  "source_end_ts"       TIMESTAMP,
  "notebook_version"    TEXT,
  "quality_status"      TEXT,
  "run_mode"            TEXT,
  "run_disposition"     TEXT,
  "output_scope"        TEXT,
  "is_production"       BOOLEAN,
  "computed_date"      DATE
  , PRIMARY KEY ("city_id", "label_date", "device_category")
);

-- ps2_v25_failure_label_summary: 4 rows in run bb211e9d, PK (device_category)
CREATE TABLE IF NOT EXISTS ps2_v25_failure_label_summary (
  "city_id"            city_code NOT NULL REFERENCES cities(id),
  "eligible_device_days" BIGINT,
  "positive_device_days" BIGINT,
  "eligible_devices"    BIGINT,
  "positive_devices"    BIGINT,
  "future_hardware_oos_set_events" BIGINT,
  "mean_hours_to_next_oos" DOUBLE PRECISION,
  "median_hours_to_next_oos" DOUBLE PRECISION,
  "p90_hours_to_next_oos" DOUBLE PRECISION,
  "positive_days_with_commanded_oos" BIGINT,
  "device_category"     TEXT,
  "negative_device_days" BIGINT,
  "label_positive_rate" DOUBLE PRECISION,
  "positive_device_share" DOUBLE PRECISION,
  "label_horizon_days"  INTEGER,
  "label_cutoff_date"   DATE,
  "label_definition"    TEXT,
  "run_id"              TEXT,
  "run_ts_utc"          TIMESTAMP,
  "as_of_ts"            TIMESTAMP,
  "source_start_ts"     TIMESTAMP,
  "source_end_ts"       TIMESTAMP,
  "notebook_version"    TEXT,
  "quality_status"      TEXT,
  "run_mode"            TEXT,
  "run_disposition"     TEXT,
  "output_scope"        TEXT,
  "is_production"       BOOLEAN,
  "computed_date"      DATE
  , PRIMARY KEY ("city_id", "device_category")
);

-- ps2_v25_ps1_label_parity: 3 rows in run bb211e9d, PK (device_category)
CREATE TABLE IF NOT EXISTS ps2_v25_ps1_label_parity (
  "city_id"            city_code NOT NULL REFERENCES cities(id),
  "device_category"     TEXT,
  "eligible_device_days" BIGINT,
  "comparable_device_days" BIGINT,
  "matching_device_days" BIGINT,
  "mismatching_device_days" BIGINT,
  "source_label_positive_rate" DOUBLE PRECISION,
  "rebuilt_label_positive_rate" DOUBLE PRECISION,
  "legacy_sla_positive_rate" DOUBLE PRECISION,
  "parity_rate"         DOUBLE PRECISION,
  "parity_status"       TEXT,
  "rebuilt_target"      TEXT,
  "run_id"              TEXT,
  "run_ts_utc"          TIMESTAMP,
  "as_of_ts"            TIMESTAMP,
  "source_start_ts"     TIMESTAMP,
  "source_end_ts"       TIMESTAMP,
  "notebook_version"    TEXT,
  "quality_status"      TEXT,
  "run_mode"            TEXT,
  "run_disposition"     TEXT,
  "output_scope"        TEXT,
  "is_production"       BOOLEAN,
  "computed_date"      DATE
  , PRIMARY KEY ("city_id", "device_category")
);

-- ps2_v25_ps1_model_performance: 4 rows in run bb211e9d, PK (device_category)
CREATE TABLE IF NOT EXISTS ps2_v25_ps1_model_performance (
  "city_id"            city_code NOT NULL REFERENCES cities(id),
  "device_category"     TEXT,
  "evaluated_device_days" BIGINT,
  "eligible_device_days" BIGINT,
  "prediction_coverage" DOUBLE PRECISION,
  "true_positive"       BIGINT,
  "false_positive"      BIGINT,
  "true_negative"       BIGINT,
  "false_negative"      BIGINT,
  "actual_positive_rate" DOUBLE PRECISION,
  "predicted_positive_rate" DOUBLE PRECISION,
  "precision"           DOUBLE PRECISION,
  "recall"              DOUBLE PRECISION,
  "specificity"         DOUBLE PRECISION,
  "f1_score"            DOUBLE PRECISION,
  "balanced_accuracy"   DOUBLE PRECISION,
  "brier_score"         DOUBLE PRECISION,
  "roc_auc"             DOUBLE PRECISION,
  "pr_auc"              DOUBLE PRECISION,
  "evaluation_status"   TEXT,
  "run_id"              TEXT,
  "run_ts_utc"          TIMESTAMP,
  "as_of_ts"            TIMESTAMP,
  "source_start_ts"     TIMESTAMP,
  "source_end_ts"       TIMESTAMP,
  "notebook_version"    TEXT,
  "quality_status"      TEXT,
  "run_mode"            TEXT,
  "run_disposition"     TEXT,
  "output_scope"        TEXT,
  "is_production"       BOOLEAN,
  "computed_date"      DATE
  , PRIMARY KEY ("city_id", "device_category")
);

-- ps2_v25_run_quality: 16 rows in run bb211e9d, PK (check_name)
CREATE TABLE IF NOT EXISTS ps2_v25_run_quality (
  "city_id"            city_code NOT NULL REFERENCES cities(id),
  "check_name"          TEXT,
  "passed"              BOOLEAN,
  "observed_value"      DOUBLE PRECISION,
  "threshold"           TEXT,
  "severity"            TEXT,
  "metric_context"      TEXT,
  "quality_status"      TEXT,
  "run_mode"            TEXT,
  "run_disposition"     TEXT,
  "run_id"              TEXT,
  "run_ts_utc"          TIMESTAMP,
  "as_of_ts"            TIMESTAMP,
  "source_start_ts"     TIMESTAMP,
  "source_end_ts"       TIMESTAMP,
  "notebook_version"    TEXT,
  "output_scope"        TEXT,
  "is_production"       BOOLEAN,
  "computed_date"      DATE
  , PRIMARY KEY ("city_id", "check_name")
);

-- ps2_v2_cofailure_clusters: 83,184 rows in run bb211e9d, PK (event_date, cluster_scope, cluster_id, device_category)
CREATE TABLE IF NOT EXISTS ps2_v2_cofailure_clusters (
  "city_id"            city_code NOT NULL REFERENCES cities(id),
  "event_date"          DATE,
  "cluster_scope"       TEXT,
  "cluster_id"          TEXT,
  "device_category"     TEXT,
  "facility_id"         TEXT,
  "cofailing_devices"   BIGINT,
  "hardware_oos_onsets" BIGINT,
  "observed_group_devices" DOUBLE PRECISION,
  "cofailure_share"     DOUBLE PRECISION,
  "coordinated_station_flag" BOOLEAN,
  "major_station_flag"  BOOLEAN,
  "run_id"              TEXT,
  "run_ts_utc"          TIMESTAMP,
  "as_of_ts"            TIMESTAMP,
  "source_start_ts"     TIMESTAMP,
  "source_end_ts"       TIMESTAMP,
  "notebook_version"    TEXT,
  "quality_status"      TEXT,
  "run_mode"            TEXT,
  "run_disposition"     TEXT,
  "output_scope"        TEXT,
  "is_production"       BOOLEAN,
  "computed_date"      DATE
  , PRIMARY KEY ("city_id", "event_date", "cluster_scope", "cluster_id", "device_category")
);
CREATE INDEX IF NOT EXISTS idx_ps2_v2_cofailure_clusters_device_category_event_date ON ps2_v2_cofailure_clusters ("city_id", "device_category", "event_date");
CREATE INDEX IF NOT EXISTS idx_ps2_v2_cofailure_clusters_facility_id ON ps2_v2_cofailure_clusters ("city_id", "facility_id");

-- ps2_v2_component_serial_patterns: 78 rows in run bb211e9d, PK (device_category, component_subsystem, component_serial_id)
CREATE TABLE IF NOT EXISTS ps2_v2_component_serial_patterns (
  "city_id"            city_code NOT NULL REFERENCES cities(id),
  "device_category"     TEXT,
  "component_subsystem" TEXT,
  "component_serial_id" TEXT,
  "hardware_oos_episode_count" BIGINT,
  "validated_failure_count" BIGINT,
  "hardware_oos_minutes" DOUBLE PRECISION,
  "observed_oos_days"   BIGINT,
  "last_oos_ts"         TIMESTAMP,
  "serial_evidence_tier" TEXT,
  "component_priority_score" DOUBLE PRECISION,
  "current_component_age_days" DOUBLE PRECISION,
  "hardware_component_description" TEXT,
  "hardware_source"     TEXT,
  "current_config_device_count" DOUBLE PRECISION,
  "hardware_age_enrichment" TEXT,
  "run_id"              TEXT,
  "run_ts_utc"          TIMESTAMP,
  "as_of_ts"            TIMESTAMP,
  "source_start_ts"     TIMESTAMP,
  "source_end_ts"       TIMESTAMP,
  "notebook_version"    TEXT,
  "quality_status"      TEXT,
  "run_mode"            TEXT,
  "run_disposition"     TEXT,
  "output_scope"        TEXT,
  "is_production"       BOOLEAN,
  "computed_date"      DATE
  , PRIMARY KEY ("city_id", "device_category", "component_subsystem", "component_serial_id")
);
CREATE INDEX IF NOT EXISTS idx_ps2_v2_component_serial_patterns_component_serial_id ON ps2_v2_component_serial_patterns ("city_id", "component_serial_id");

-- ps2_v2_cross_ps_alignment: 3 rows in run bb211e9d, PK (source)
CREATE TABLE IF NOT EXISTS ps2_v2_cross_ps_alignment (
  "city_id"            city_code NOT NULL REFERENCES cities(id),
  "truth_positive_count" BIGINT,
  "signal_positive_count" DOUBLE PRECISION,
  "matched_positive_count" BIGINT,
  "precision"           DOUBLE PRECISION,
  "recall"              DOUBLE PRECISION,
  "population_unit"     TEXT,
  "alignment_status"    TEXT,
  "source"              TEXT,
  "run_id"              TEXT,
  "run_ts_utc"          TIMESTAMP,
  "as_of_ts"            TIMESTAMP,
  "source_start_ts"     TIMESTAMP,
  "source_end_ts"       TIMESTAMP,
  "notebook_version"    TEXT,
  "quality_status"      TEXT,
  "run_mode"            TEXT,
  "run_disposition"     TEXT,
  "output_scope"        TEXT,
  "is_production"       BOOLEAN,
  "computed_date"      DATE
  , PRIMARY KEY ("city_id", "source")
);

-- ps2_v2_customer_exposure: 633 rows in run bb211e9d, PK (event_date, device_category)
CREATE TABLE IF NOT EXISTS ps2_v2_customer_exposure (
  "city_id"            city_code NOT NULL REFERENCES cities(id),
  "event_date"          DATE,
  "device_category"     TEXT,
  "hardware_oos_onsets" BIGINT,
  "hardware_oos_minutes" DOUBLE PRECISION,
  "transactions_exposed" DOUBLE PRECISION,
  "revenue_cents_exposed" DOUBLE PRECISION,
  "run_id"              TEXT,
  "run_ts_utc"          TIMESTAMP,
  "as_of_ts"            TIMESTAMP,
  "source_start_ts"     TIMESTAMP,
  "source_end_ts"       TIMESTAMP,
  "notebook_version"    TEXT,
  "quality_status"      TEXT,
  "run_mode"            TEXT,
  "run_disposition"     TEXT,
  "output_scope"        TEXT,
  "is_production"       BOOLEAN,
  "computed_date"      DATE
  , PRIMARY KEY ("city_id", "event_date", "device_category")
);

-- ps2_v2_daily_oos_trend: 633 rows in run bb211e9d, PK (event_date, device_category)
CREATE TABLE IF NOT EXISTS ps2_v2_daily_oos_trend (
  "city_id"            city_code NOT NULL REFERENCES cities(id),
  "event_date"          DATE,
  "device_category"     TEXT,
  "hardware_oos_onsets" BIGINT,
  "affected_devices"    BIGINT,
  "hardware_oos_minutes" DOUBLE PRECISION,
  "validated_failure_onsets" BIGINT,
  "chargeable_oos_onsets" BIGINT,
  "run_id"              TEXT,
  "run_ts_utc"          TIMESTAMP,
  "as_of_ts"            TIMESTAMP,
  "source_start_ts"     TIMESTAMP,
  "source_end_ts"       TIMESTAMP,
  "notebook_version"    TEXT,
  "quality_status"      TEXT,
  "run_mode"            TEXT,
  "run_disposition"     TEXT,
  "output_scope"        TEXT,
  "is_production"       BOOLEAN,
  "computed_date"      DATE
  , PRIMARY KEY ("city_id", "event_date", "device_category")
);

-- ps2_v2_device_deterioration: 2,208 rows in run bb211e9d, PK (device_id, event_date, alert_reason)
CREATE TABLE IF NOT EXISTS ps2_v2_device_deterioration (
  "city_id"            city_code NOT NULL REFERENCES cities(id),
  "device_id"           TEXT,
  "device_category"     TEXT,
  "event_date"          DATE,
  "hardware_oos_onsets" BIGINT,
  "hardware_oos_minutes" DOUBLE PRECISION,
  "validated_failure_onsets" BIGINT,
  "baseline_mean_28d"   DOUBLE PRECISION,
  "baseline_std_28d"    DOUBLE PRECISION,
  "oos_zscore_28d"      DOUBLE PRECISION,
  "alert_reason"        TEXT,
  "run_id"              TEXT,
  "run_ts_utc"          TIMESTAMP,
  "as_of_ts"            TIMESTAMP,
  "source_start_ts"     TIMESTAMP,
  "source_end_ts"       TIMESTAMP,
  "notebook_version"    TEXT,
  "quality_status"      TEXT,
  "run_mode"            TEXT,
  "run_disposition"     TEXT,
  "output_scope"        TEXT,
  "is_production"       BOOLEAN,
  "computed_date"      DATE
  , PRIMARY KEY ("city_id", "device_id", "event_date", "alert_reason")
);
CREATE INDEX IF NOT EXISTS idx_ps2_v2_device_deterioration_device_id ON ps2_v2_device_deterioration ("city_id", "device_id");
CREATE INDEX IF NOT EXISTS idx_ps2_v2_device_deterioration_event_date ON ps2_v2_device_deterioration ("city_id", "event_date");

-- ps2_v2_leadlag_timing: 123 rows in run bb211e9d, PK (device_category, component_subsystem, next_subsystem)
CREATE TABLE IF NOT EXISTS ps2_v2_leadlag_timing (
  "city_id"            city_code NOT NULL REFERENCES cities(id),
  "device_category"     TEXT,
  "component_subsystem" TEXT,
  "next_subsystem"      TEXT,
  "pattern_key"         TEXT,
  "edge_support"        BIGINT,
  "median_edge_lag_seconds" BIGINT,
  "p95_edge_lag_seconds" BIGINT,
  "pre_oos_rate"        DOUBLE PRECISION,
  "pre_oos_lift_vs_category" DOUBLE PRECISION,
  "evidence_tier"       TEXT,
  "run_id"              TEXT,
  "run_ts_utc"          TIMESTAMP,
  "as_of_ts"            TIMESTAMP,
  "source_start_ts"     TIMESTAMP,
  "source_end_ts"       TIMESTAMP,
  "notebook_version"    TEXT,
  "quality_status"      TEXT,
  "run_mode"            TEXT,
  "run_disposition"     TEXT,
  "output_scope"        TEXT,
  "is_production"       BOOLEAN,
  "computed_date"      DATE
  , PRIMARY KEY ("city_id", "device_category", "component_subsystem", "next_subsystem")
);

-- ps2_v2_oos_governance: 16 rows in run bb211e9d, PK (device_category, oos_evidence_class, failure_evidence_class)
CREATE TABLE IF NOT EXISTS ps2_v2_oos_governance (
  "city_id"            city_code NOT NULL REFERENCES cities(id),
  "device_category"     TEXT,
  "oos_evidence_class"  TEXT,
  "failure_evidence_class" TEXT,
  "event_count"         BIGINT,
  "device_count"        BIGINT,
  "outage_minutes"      DOUBLE PRECISION,
  "run_id"              TEXT,
  "run_ts_utc"          TIMESTAMP,
  "as_of_ts"            TIMESTAMP,
  "source_start_ts"     TIMESTAMP,
  "source_end_ts"       TIMESTAMP,
  "notebook_version"    TEXT,
  "quality_status"      TEXT,
  "run_mode"            TEXT,
  "run_disposition"     TEXT,
  "output_scope"        TEXT,
  "is_production"       BOOLEAN,
  "computed_date"      DATE
  , PRIMARY KEY ("city_id", "device_category", "oos_evidence_class", "failure_evidence_class")
);

-- ps2_v2_pattern_drift: 140 rows in run bb211e9d, PK (device_category, component_subsystem, next_subsystem)
CREATE TABLE IF NOT EXISTS ps2_v2_pattern_drift (
  "city_id"            city_code NOT NULL REFERENCES cities(id),
  "device_category"     TEXT,
  "component_subsystem" TEXT,
  "next_subsystem"      TEXT,
  "baseline_count"      BIGINT,
  "recent_count"        BIGINT,
  "baseline_pre_oos_count" BIGINT,
  "recent_pre_oos_count" BIGINT,
  "baseline_pre_oos_rate" DOUBLE PRECISION,
  "recent_pre_oos_rate" DOUBLE PRECISION,
  "rate_change"         DOUBLE PRECISION,
  "drift_flag"          BOOLEAN,
  "run_id"              TEXT,
  "run_ts_utc"          TIMESTAMP,
  "as_of_ts"            TIMESTAMP,
  "source_start_ts"     TIMESTAMP,
  "source_end_ts"       TIMESTAMP,
  "notebook_version"    TEXT,
  "quality_status"      TEXT,
  "run_mode"            TEXT,
  "run_disposition"     TEXT,
  "output_scope"        TEXT,
  "is_production"       BOOLEAN,
  "computed_date"      DATE
  , PRIMARY KEY ("city_id", "device_category", "component_subsystem", "next_subsystem")
);

-- ps2_v2_precursor_patterns: 123 rows in run bb211e9d, PK (device_category, component_subsystem, next_subsystem)
CREATE TABLE IF NOT EXISTS ps2_v2_precursor_patterns (
  "city_id"            city_code NOT NULL REFERENCES cities(id),
  "device_category"     TEXT,
  "component_subsystem" TEXT,
  "next_subsystem"      TEXT,
  "edge_support"        BIGINT,
  "pre_oos_edge_count"  BIGINT,
  "validated_failure_edge_count" BIGINT,
  "median_edge_lag_seconds" BIGINT,
  "p95_edge_lag_seconds" BIGINT,
  "baseline_edge_count" BIGINT,
  "recent_edge_count"   BIGINT,
  "baseline_pre_oos_count" BIGINT,
  "recent_pre_oos_count" BIGINT,
  "baseline_pre_oos_rate" DOUBLE PRECISION,
  "pre_oos_rate"        DOUBLE PRECISION,
  "pre_oos_wilson_lower_95" DOUBLE PRECISION,
  "pre_oos_lift_vs_category" DOUBLE PRECISION,
  "validated_failure_rate" DOUBLE PRECISION,
  "pattern_key"         TEXT,
  "evidence_tier"       TEXT,
  "priority_score"      DOUBLE PRECISION,
  "run_id"              TEXT,
  "run_ts_utc"          TIMESTAMP,
  "as_of_ts"            TIMESTAMP,
  "source_start_ts"     TIMESTAMP,
  "source_end_ts"       TIMESTAMP,
  "notebook_version"    TEXT,
  "quality_status"      TEXT,
  "run_mode"            TEXT,
  "run_disposition"     TEXT,
  "output_scope"        TEXT,
  "is_production"       BOOLEAN,
  "computed_date"      DATE
  , PRIMARY KEY ("city_id", "device_category", "component_subsystem", "next_subsystem")
);

-- ps2_v2_repair_effectiveness: 38,395 rows in run bb211e9d, PK (repair_id)
CREATE TABLE IF NOT EXISTS ps2_v2_repair_effectiveness (
  "city_id"            city_code NOT NULL REFERENCES cities(id),
  "repair_id"           TEXT,
  "maintenance_component_subsystem" TEXT,
  "ledger_type"         TEXT,
  "maintenance_date"    DATE,
  "pre_30d_oos_onsets"  BIGINT,
  "post_30d_oos_onsets" BIGINT,
  "post_vs_pre_change"  DOUBLE PRECISION,
  "interpretation_note" TEXT,
  "run_id"              TEXT,
  "run_ts_utc"          TIMESTAMP,
  "as_of_ts"            TIMESTAMP,
  "source_start_ts"     TIMESTAMP,
  "source_end_ts"       TIMESTAMP,
  "notebook_version"    TEXT,
  "quality_status"      TEXT,
  "run_mode"            TEXT,
  "run_disposition"     TEXT,
  "output_scope"        TEXT,
  "is_production"       BOOLEAN,
  "computed_date"      DATE
  , PRIMARY KEY ("city_id", "repair_id")
);
CREATE INDEX IF NOT EXISTS idx_ps2_v2_repair_effectiveness_maintenance_date ON ps2_v2_repair_effectiveness ("city_id", "maintenance_date");
CREATE INDEX IF NOT EXISTS idx_ps2_v2_repair_effectiveness_maintenance_component_subsystem ON ps2_v2_repair_effectiveness ("city_id", "maintenance_component_subsystem");

-- ps2_v2_topology_nodes: 19 rows in run bb211e9d, PK (device_category, subsystem)
CREATE TABLE IF NOT EXISTS ps2_v2_topology_nodes (
  "city_id"            city_code NOT NULL REFERENCES cities(id),
  "device_category"     TEXT,
  "subsystem"           TEXT,
  "outgoing_edge_volume" BIGINT,
  "out_degree"          BIGINT,
  "outgoing_pre_oos_rate" DOUBLE PRECISION,
  "incoming_edge_volume" BIGINT,
  "in_degree"           BIGINT,
  "flow_centrality_score" DOUBLE PRECISION,
  "run_id"              TEXT,
  "run_ts_utc"          TIMESTAMP,
  "as_of_ts"            TIMESTAMP,
  "source_start_ts"     TIMESTAMP,
  "source_end_ts"       TIMESTAMP,
  "notebook_version"    TEXT,
  "quality_status"      TEXT,
  "run_mode"            TEXT,
  "run_disposition"     TEXT,
  "output_scope"        TEXT,
  "is_production"       BOOLEAN,
  "computed_date"      DATE
  , PRIMARY KEY ("city_id", "device_category", "subsystem")
);

-- ---------------------------------------------------------------------
-- v_ps2_v25_status -- load coherence in one query.
--
-- A healthy load returns exactly ONE run_id across all 20 rows. More than one
-- means a partial load: the failure mode that otherwise shows up as a tab
-- where 18 panels are today and 2 are from last week. This is what
-- /ps2/status reads.
-- ---------------------------------------------------------------------
CREATE OR REPLACE VIEW v_ps2_v25_status AS
  SELECT 'ps2_v25_category_profile'::text AS table_name, city_id, run_id, computed_date,
         notebook_version, MAX(as_of_ts) AS as_of_ts, COUNT(*) AS row_count
    FROM ps2_v25_category_profile GROUP BY city_id, run_id, computed_date, notebook_version
  UNION ALL
  SELECT 'ps2_v25_failure_definition_alignment'::text AS table_name, city_id, run_id, computed_date,
         notebook_version, MAX(as_of_ts) AS as_of_ts, COUNT(*) AS row_count
    FROM ps2_v25_failure_definition_alignment GROUP BY city_id, run_id, computed_date, notebook_version
  UNION ALL
  SELECT 'ps2_v25_failure_horizon_profile'::text AS table_name, city_id, run_id, computed_date,
         notebook_version, MAX(as_of_ts) AS as_of_ts, COUNT(*) AS row_count
    FROM ps2_v25_failure_horizon_profile GROUP BY city_id, run_id, computed_date, notebook_version
  UNION ALL
  SELECT 'ps2_v25_failure_label_daily'::text AS table_name, city_id, run_id, computed_date,
         notebook_version, MAX(as_of_ts) AS as_of_ts, COUNT(*) AS row_count
    FROM ps2_v25_failure_label_daily GROUP BY city_id, run_id, computed_date, notebook_version
  UNION ALL
  SELECT 'ps2_v25_failure_label_summary'::text AS table_name, city_id, run_id, computed_date,
         notebook_version, MAX(as_of_ts) AS as_of_ts, COUNT(*) AS row_count
    FROM ps2_v25_failure_label_summary GROUP BY city_id, run_id, computed_date, notebook_version
  UNION ALL
  SELECT 'ps2_v25_ps1_label_parity'::text AS table_name, city_id, run_id, computed_date,
         notebook_version, MAX(as_of_ts) AS as_of_ts, COUNT(*) AS row_count
    FROM ps2_v25_ps1_label_parity GROUP BY city_id, run_id, computed_date, notebook_version
  UNION ALL
  SELECT 'ps2_v25_ps1_model_performance'::text AS table_name, city_id, run_id, computed_date,
         notebook_version, MAX(as_of_ts) AS as_of_ts, COUNT(*) AS row_count
    FROM ps2_v25_ps1_model_performance GROUP BY city_id, run_id, computed_date, notebook_version
  UNION ALL
  SELECT 'ps2_v25_run_quality'::text AS table_name, city_id, run_id, computed_date,
         notebook_version, MAX(as_of_ts) AS as_of_ts, COUNT(*) AS row_count
    FROM ps2_v25_run_quality GROUP BY city_id, run_id, computed_date, notebook_version
  UNION ALL
  SELECT 'ps2_v2_cofailure_clusters'::text AS table_name, city_id, run_id, computed_date,
         notebook_version, MAX(as_of_ts) AS as_of_ts, COUNT(*) AS row_count
    FROM ps2_v2_cofailure_clusters GROUP BY city_id, run_id, computed_date, notebook_version
  UNION ALL
  SELECT 'ps2_v2_component_serial_patterns'::text AS table_name, city_id, run_id, computed_date,
         notebook_version, MAX(as_of_ts) AS as_of_ts, COUNT(*) AS row_count
    FROM ps2_v2_component_serial_patterns GROUP BY city_id, run_id, computed_date, notebook_version
  UNION ALL
  SELECT 'ps2_v2_cross_ps_alignment'::text AS table_name, city_id, run_id, computed_date,
         notebook_version, MAX(as_of_ts) AS as_of_ts, COUNT(*) AS row_count
    FROM ps2_v2_cross_ps_alignment GROUP BY city_id, run_id, computed_date, notebook_version
  UNION ALL
  SELECT 'ps2_v2_customer_exposure'::text AS table_name, city_id, run_id, computed_date,
         notebook_version, MAX(as_of_ts) AS as_of_ts, COUNT(*) AS row_count
    FROM ps2_v2_customer_exposure GROUP BY city_id, run_id, computed_date, notebook_version
  UNION ALL
  SELECT 'ps2_v2_daily_oos_trend'::text AS table_name, city_id, run_id, computed_date,
         notebook_version, MAX(as_of_ts) AS as_of_ts, COUNT(*) AS row_count
    FROM ps2_v2_daily_oos_trend GROUP BY city_id, run_id, computed_date, notebook_version
  UNION ALL
  SELECT 'ps2_v2_device_deterioration'::text AS table_name, city_id, run_id, computed_date,
         notebook_version, MAX(as_of_ts) AS as_of_ts, COUNT(*) AS row_count
    FROM ps2_v2_device_deterioration GROUP BY city_id, run_id, computed_date, notebook_version
  UNION ALL
  SELECT 'ps2_v2_leadlag_timing'::text AS table_name, city_id, run_id, computed_date,
         notebook_version, MAX(as_of_ts) AS as_of_ts, COUNT(*) AS row_count
    FROM ps2_v2_leadlag_timing GROUP BY city_id, run_id, computed_date, notebook_version
  UNION ALL
  SELECT 'ps2_v2_oos_governance'::text AS table_name, city_id, run_id, computed_date,
         notebook_version, MAX(as_of_ts) AS as_of_ts, COUNT(*) AS row_count
    FROM ps2_v2_oos_governance GROUP BY city_id, run_id, computed_date, notebook_version
  UNION ALL
  SELECT 'ps2_v2_pattern_drift'::text AS table_name, city_id, run_id, computed_date,
         notebook_version, MAX(as_of_ts) AS as_of_ts, COUNT(*) AS row_count
    FROM ps2_v2_pattern_drift GROUP BY city_id, run_id, computed_date, notebook_version
  UNION ALL
  SELECT 'ps2_v2_precursor_patterns'::text AS table_name, city_id, run_id, computed_date,
         notebook_version, MAX(as_of_ts) AS as_of_ts, COUNT(*) AS row_count
    FROM ps2_v2_precursor_patterns GROUP BY city_id, run_id, computed_date, notebook_version
  UNION ALL
  SELECT 'ps2_v2_repair_effectiveness'::text AS table_name, city_id, run_id, computed_date,
         notebook_version, MAX(as_of_ts) AS as_of_ts, COUNT(*) AS row_count
    FROM ps2_v2_repair_effectiveness GROUP BY city_id, run_id, computed_date, notebook_version
  UNION ALL
  SELECT 'ps2_v2_topology_nodes'::text AS table_name, city_id, run_id, computed_date,
         notebook_version, MAX(as_of_ts) AS as_of_ts, COUNT(*) AS row_count
    FROM ps2_v2_topology_nodes GROUP BY city_id, run_id, computed_date, notebook_version;
