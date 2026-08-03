-- =====================================================================
-- sql/45_ps3_v25.sql
--
-- The 20 tables published by PS3_RootCause_Severity_SageMaker_Source_First_V25.
--
-- PURELY ADDITIVE. Nothing here drops, alters or renames anything. Every
-- existing PS3 table -- ps3_incident_predictions, ps3_device_predictions,
-- ps3_v2_rootcause, ps3_serial_risk and the rest that the deployed API serves
-- today -- is untouched and remains the plan-B set. Every new name is prefixed
-- ps3_v25_ so a collision with that generation is structurally impossible.
--
-- TYPES ARE READ, NOT INFERRED. Column names and pandas dtypes were captured by
-- executing the V25 table builders and profiling every column, the same way
-- sql/44 was built from real PS2 manifests. VERIFY against the ps3_schema_dump
-- output from the real run before applying: a dtype that differs between the
-- fixture and production is exactly the kind of thing that only shows up on the
-- first load.
--
-- FOUR DESIGN CHOICES, INHERITED FROM sql/44 DELIBERATELY
--
-- 1. NATURAL PRIMARY KEY, NO SURROGATE id. The loader's find_pk_collapse() does
--    keys = [k for k in pk if k in use], and id is stripped from use just above
--    it, so a surrogate PK disables that protection entirely.
-- 2. computed_date ON EVERY TABLE, filled by the loader from the S3 partition.
--    Without it nothing records which run a row came from and /ps3/status has
--    nothing to read.
-- 3. TEXT FOR EVERY STRING. TEXT and VARCHAR(n) are identical on disk and in
--    speed in PostgreSQL, so a length cap would only add a failure mode.
-- 4. JSONB WHERE THE VALUE IS NOT SCALAR. ps3_run_stage_audit.model_runs holds
--    a list of dicts. As TEXT it would load a stringified Python repr and be
--    unqueryable.
--
-- ONE THING TO CHECK BEFORE THE FIRST LOAD. The loader refuses to publish a
-- table whose declared key is null or duplicated. Keys chosen below are the
-- grain each builder groups on, but several group with dropna=False, so a null
-- component or facility is possible in real data where the fixture had none.
-- The load is a dry run first for exactly this reason.
-- =====================================================================

-- COLUMNS WHOSE TYPE COULD NOT BE OBSERVED (all-null in the reference run).
-- pandas types an all-NaN column float64 whatever it will really hold, so each
-- of these is declared TEXT, which accepts anything, and must be confirmed
-- against the ps3_schema_dump output from the real run before this is applied:
--   ps3_v25_device_episode_fact.bus_id
--   ps3_v25_device_episode_fact.linked_severity
--   ps3_v25_device_episode_fact.linked_component
--   ps3_v25_device_episode_fact.linked_root_cause
--   ps3_v25_device_episode_fact.linked_root_cause_domain
--   ps3_v25_device_episode_fact.linked_confidence
--   ps3_v25_device_episode_fact.linked_source
--   ps3_v25_device_episode_fact.link_method
--   ps3_v25_device_episode_fact.confirmed_root_cause_label
--   ps3_v25_device_episode_fact.confirmed_root_cause_domain
--   ps3_v25_device_episode_fact.root_cause_confidence
--   ps3_v25_device_episode_fact.root_cause_evidence_source
--   ps3_v25_device_episode_fact.root_cause_link_method
--   ps3_v25_device_episode_fact.candidate_root_cause_raw
--   ps3_v25_device_summary.latest_dashboard_root_cause_domain
--   ps3_v25_prediction_explainability.device_id
--   ps3_v25_prediction_explainability.mars_device_category
--   ps3_v25_prediction_explainability.predicted_label
--   ps3_v25_prediction_explainability.shap_value
--   ps3_v25_prediction_explainability.abs_shap_value
--   ps3_v25_root_cause_evidence_audit.reference

-- ps3_v25_causal_balance: 36 rows in the reference run, 4 columns, PK (city_id, treatment_component, covariate)
CREATE TABLE IF NOT EXISTS ps3_v25_causal_balance (
    "city_id"        city_code NOT NULL REFERENCES cities(id)
  , "computed_date"  DATE
  , "treatment_component" TEXT NOT NULL
  , "covariate" TEXT NOT NULL
  , "standardised_mean_difference" DOUBLE PRECISION
  , "balance_status" TEXT
  , PRIMARY KEY ("city_id", "treatment_component", "covariate")
);

-- ps3_v25_causal_effects: 4 rows in the reference run, 22 columns, PK (city_id, treatment_component, outcome)
CREATE TABLE IF NOT EXISTS ps3_v25_causal_effects (
    "city_id"        city_code NOT NULL REFERENCES cities(id)
  , "computed_date"  DATE
  , "treatment_component" TEXT NOT NULL
  , "outcome" TEXT NOT NULL
  , "estimator" TEXT
  , "average_treatment_effect" DOUBLE PRECISION
  , "standard_error" DOUBLE PRECISION
  , "ci_low_95" DOUBLE PRECISION
  , "ci_high_95" DOUBLE PRECISION
  , "significant_95" BOOLEAN
  , "episodes_used" BIGINT
  , "treated_episodes" BIGINT
  , "control_episodes" BIGINT
  , "overlap_share" DOUBLE PRECISION
  , "treated_prevalence" DOUBLE PRECISION
  , "propensity_p01" DOUBLE PRECISION
  , "propensity_p99" DOUBLE PRECISION
  , "models_converged" BOOLEAN
  , "status" TEXT
  , "interpretation" TEXT
  , "p_value_two_sided" DOUBLE PRECISION
  , "p_value_holm" DOUBLE PRECISION
  , "significant_95_holm" BOOLEAN
  , "multiplicity_note" TEXT
  , PRIMARY KEY ("city_id", "treatment_component", "outcome")
);

-- ps3_v25_commanded_split: 3 rows in the reference run, 5 columns, PK (city_id, mars_device_category)
CREATE TABLE IF NOT EXISTS ps3_v25_commanded_split (
    "city_id"        city_code NOT NULL REFERENCES cities(id)
  , "computed_date"  DATE
  , "mars_device_category" TEXT NOT NULL
  , "oos_episodes" BIGINT
  , "commanded_signal_episodes" BIGINT
  , "failure_only_episodes" BIGINT
  , "basis" TEXT
  , PRIMARY KEY ("city_id", "mars_device_category")
);

-- ps3_v25_component_summary: 24 rows in the reference run, 7 columns, PK (city_id, mars_device_category, component_attribution, dashboard_root_cause_domain, dashboard_severity)
CREATE TABLE IF NOT EXISTS ps3_v25_component_summary (
    "city_id"        city_code NOT NULL REFERENCES cities(id)
  , "computed_date"  DATE
  , "mars_device_category" TEXT NOT NULL
  , "component_attribution" TEXT NOT NULL
  , "dashboard_root_cause_domain" TEXT NOT NULL
  , "dashboard_severity" TEXT NOT NULL
  , "oos_episode_count" BIGINT
  , "device_count" BIGINT
  , "confirmed_root_cause_count" BIGINT
  , PRIMARY KEY ("city_id", "mars_device_category", "component_attribution", "dashboard_root_cause_domain", "dashboard_severity")
);

-- ps3_v25_device_day: 14,720 rows in the reference run, 11 columns, PK (city_id, device_id, mars_device_category, event_date)
CREATE TABLE IF NOT EXISTS ps3_v25_device_day (
    "city_id"        city_code NOT NULL REFERENCES cities(id)
  , "computed_date"  DATE
  , "device_id" TEXT NOT NULL
  , "mars_device_category" TEXT NOT NULL
  , "event_date" TIMESTAMP NOT NULL
  , "oos_episode_starts" BIGINT
  , "oos_set_events" BIGINT
  , "set_signal_span_minutes" DOUBLE PRECISION
  , "commanded_signal_episodes" BIGINT
  , "observed_severity_episodes" BIGINT
  , "confirmed_root_cause_episodes" BIGINT
  , "failure_only_episode_starts" BIGINT
  , "grain" TEXT
  , PRIMARY KEY ("city_id", "device_id", "mars_device_category", "event_date")
);
CREATE INDEX IF NOT EXISTS idx_ps3_v25_device_day_event_date_mars_device_category ON ps3_v25_device_day ("city_id", "event_date", "mars_device_category");

-- ps3_v25_device_episode_fact: 14,720 rows in the reference run, 78 columns, PK (city_id, oos_episode_id)
CREATE TABLE IF NOT EXISTS ps3_v25_device_episode_fact (
    "city_id"        city_code NOT NULL REFERENCES cities(id)
  , "computed_date"  DATE
  , "oos_episode_id" TEXT NOT NULL
  , "device_id" TEXT
  , "mars_device_category" TEXT
  , "episode_start" TIMESTAMP
  , "episode_last_signal" TIMESTAMP
  , "oos_set_event_count" BIGINT
  , "first_oos_event_id" TEXT
  , "contains_commanded_oos_signal" BOOLEAN
  , "observed_event_component" TEXT
  , "component_subsystem" TEXT
  , "component_position" TEXT
  , "facility_id" TEXT
  , "facility_name" TEXT
  , "bus_id" TEXT
  , "component_serial_nbr" TEXT
  , "event_type_name" TEXT
  , "event_type_id" TEXT
  , "observed_event_severity" TEXT
  , "event_type_severity" TEXT
  , "event_priority" TEXT
  , "requires_service_call" BOOLEAN
  , "any_automatic_clear" BOOLEAN
  , "distinct_serials_in_episode" BIGINT
  , "distinct_components_in_episode" BIGINT
  , "set_signal_span_minutes" DOUBLE PRECISION
  , "oos_fact_definition" TEXT
  , "oos_minutes_union" DOUBLE PRECISION
  , "oos_minutes_naive_sum" DOUBLE PRECISION
  , "events_with_clear" DOUBLE PRECISION
  , "events_clear_clamped" DOUBLE PRECISION
  , "episode_scope_status" TEXT
  , "episode_scope_start" TIMESTAMP
  , "_episode_row_id" BIGINT
  , "linked_severity" TEXT
  , "linked_component" TEXT
  , "linked_root_cause" TEXT
  , "linked_root_cause_domain" TEXT
  , "linked_confidence" TEXT
  , "linked_source" TEXT
  , "link_method" TEXT
  , "observed_severity_label" TEXT
  , "severity_status" TEXT
  , "component_attribution" TEXT
  , "component_attribution_status" TEXT
  , "confirmed_root_cause_label" TEXT
  , "confirmed_root_cause_domain" TEXT
  , "root_cause_confidence" TEXT
  , "root_cause_evidence_source" TEXT
  , "root_cause_link_method" TEXT
  , "root_cause_status" TEXT
  , "candidate_root_cause_raw" TEXT
  , "evidence_conflict_status" TEXT
  , "event_month" BIGINT
  , "event_day_of_week" BIGINT
  , "event_hour" BIGINT
  , "log_oos_set_event_count" DOUBLE PRECISION
  , "log_set_signal_span_minutes" DOUBLE PRECISION
  , "prior_episodes_7d" DOUBLE PRECISION
  , "prior_episodes_30d" DOUBLE PRECISION
  , "prior_episodes_90d" DOUBLE PRECISION
  , "days_since_prior_episode" DOUBLE PRECISION
  , "device_oos_recency_status" TEXT
  , "predicted_severity" TEXT
  , "predicted_severity_confidence" TEXT
  , "severity_model_status" TEXT
  , "run_id" TEXT
  , "computed_at_utc" TEXT
  , "data_as_of_date" TEXT
  , "data_freshness_days" BIGINT
  , "is_current_operational_score" BOOLEAN
  , "freshness_status" TEXT
  , "right_censored_tail_days" BIGINT
  , "chargeability_policy" TEXT
  , "shap_interpretation" TEXT
  , "dashboard_root_cause_domain" TEXT
  , "dashboard_root_cause_status" TEXT
  , "dashboard_severity" TEXT
  , "dashboard_severity_status" TEXT
  , PRIMARY KEY ("city_id", "oos_episode_id")
);
CREATE INDEX IF NOT EXISTS idx_ps3_v25_device_episode_fact_device_id_episode_start ON ps3_v25_device_episode_fact ("city_id", "device_id", "episode_start");
CREATE INDEX IF NOT EXISTS idx_ps3_v25_device_episode_fact_mars_device_category_episode_start ON ps3_v25_device_episode_fact ("city_id", "mars_device_category", "episode_start");
CREATE INDEX IF NOT EXISTS idx_ps3_v25_device_episode_fact_facility_id ON ps3_v25_device_episode_fact ("city_id", "facility_id");
CREATE INDEX IF NOT EXISTS idx_ps3_v25_device_episode_fact_component_serial_nbr ON ps3_v25_device_episode_fact ("city_id", "component_serial_nbr");

-- ps3_v25_device_reliability: 1,050 rows in the reference run, 16 columns, PK (city_id, device_id, mars_device_category)
CREATE TABLE IF NOT EXISTS ps3_v25_device_reliability (
    "city_id"        city_code NOT NULL REFERENCES cities(id)
  , "computed_date"  DATE
  , "device_id" TEXT NOT NULL
  , "mars_device_category" TEXT NOT NULL
  , "oos_episode_count" BIGINT
  , "oos_set_event_count" BIGINT
  , "critical_episodes" BIGINT
  , "first_episode_at" TIMESTAMP
  , "latest_episode_at" TIMESTAMP
  , "mean_interval_hours" DOUBLE PRECISION
  , "median_interval_hours" DOUBLE PRECISION
  , "confirmed_root_cause_episodes" BIGINT
  , "observed_severity_episodes" BIGINT
  , "commanded_signal_episodes" BIGINT
  , "critical_rate" DOUBLE PRECISION
  , "failure_only_episode_count" BIGINT
  , "reliability_risk_band" TEXT
  , "band_basis" TEXT
  , PRIMARY KEY ("city_id", "device_id", "mars_device_category")
);
CREATE INDEX IF NOT EXISTS idx_ps3_v25_device_reliability_mars_device_category_oos_episode_count ON ps3_v25_device_reliability ("city_id", "mars_device_category", "oos_episode_count");

-- ps3_v25_device_summary: 1,050 rows in the reference run, 9 columns, PK (city_id, device_id, mars_device_category)
CREATE TABLE IF NOT EXISTS ps3_v25_device_summary (
    "city_id"        city_code NOT NULL REFERENCES cities(id)
  , "computed_date"  DATE
  , "device_id" TEXT NOT NULL
  , "mars_device_category" TEXT NOT NULL
  , "oos_episode_count" BIGINT
  , "first_oos_episode_start" TIMESTAMP
  , "latest_oos_episode_start" TIMESTAMP
  , "latest_dashboard_severity" TEXT
  , "latest_dashboard_root_cause_domain" TEXT
  , "confirmed_root_cause_episode_count" BIGINT
  , "observed_severity_episode_count" BIGINT
  , PRIMARY KEY ("city_id", "device_id", "mars_device_category")
);

-- ps3_v25_facility_rollup: 45 rows in the reference run, 11 columns, PK (city_id, facility_id, mars_device_category)
CREATE TABLE IF NOT EXISTS ps3_v25_facility_rollup (
    "city_id"        city_code NOT NULL REFERENCES cities(id)
  , "computed_date"  DATE
  , "facility_id" TEXT NOT NULL
  , "mars_device_category" TEXT NOT NULL
  , "devices" BIGINT
  , "oos_episodes" BIGINT
  , "first_episode" TIMESTAMP
  , "latest_episode" TIMESTAMP
  , "active_days" BIGINT
  , "commanded_signal_episodes" BIGINT
  , "confirmed_root_cause_episodes" BIGINT
  , "episodes_per_device" DOUBLE PRECISION
  , "failure_only_episodes" BIGINT
  , PRIMARY KEY ("city_id", "facility_id", "mars_device_category")
);
CREATE INDEX IF NOT EXISTS idx_ps3_v25_facility_rollup_mars_device_category ON ps3_v25_facility_rollup ("city_id", "mars_device_category");

-- ps3_v25_label_maturity: 3 rows in the reference run, 8 columns, PK (city_id, mars_device_category)
CREATE TABLE IF NOT EXISTS ps3_v25_label_maturity (
    "city_id"        city_code NOT NULL REFERENCES cities(id)
  , "computed_date"  DATE
  , "mars_device_category" TEXT NOT NULL
  , "oos_episode_count" BIGINT
  , "observed_severity_count" BIGINT
  , "confirmed_root_cause_count" BIGINT
  , "candidate_root_cause_count" BIGINT
  , "component_attribution_count" BIGINT
  , "severity_coverage" DOUBLE PRECISION
  , "confirmed_root_cause_coverage" DOUBLE PRECISION
  , PRIMARY KEY ("city_id", "mars_device_category")
);

-- ps3_v25_model_feature_importance: 36 rows in the reference run, 5 columns, PK (city_id, target, model, feature, model_scope)
CREATE TABLE IF NOT EXISTS ps3_v25_model_feature_importance (
    "city_id"        city_code NOT NULL REFERENCES cities(id)
  , "computed_date"  DATE
  , "target" TEXT NOT NULL
  , "model" TEXT NOT NULL
  , "feature" TEXT NOT NULL
  , "importance" DOUBLE PRECISION
  , "model_scope" TEXT NOT NULL
  , PRIMARY KEY ("city_id", "target", "model", "feature", "model_scope")
);

-- ps3_v25_model_scorecard: 14 rows in the reference run, 13 columns, PK (city_id, target, candidate_model, model_scope)
CREATE TABLE IF NOT EXISTS ps3_v25_model_scorecard (
    "city_id"        city_code NOT NULL REFERENCES cities(id)
  , "computed_date"  DATE
  , "target" TEXT NOT NULL
  , "candidate_model" TEXT NOT NULL
  , "f1_macro" DOUBLE PRECISION
  , "f1_weighted" DOUBLE PRECISION
  , "balanced_accuracy" DOUBLE PRECISION
  , "accuracy" DOUBLE PRECISION
  , "mcc" DOUBLE PRECISION
  , "majority_f1_macro" DOUBLE PRECISION
  , "macro_f1_lift" DOUBLE PRECISION
  , "label_coverage" DOUBLE PRECISION
  , "quality_gate" TEXT
  , "detail" TEXT
  , "model_scope" TEXT NOT NULL
  , PRIMARY KEY ("city_id", "target", "candidate_model", "model_scope")
);

-- ps3_v25_oos_source_audit: 1 rows in the reference run, 12 columns, PK (city_id, source)
CREATE TABLE IF NOT EXISTS ps3_v25_oos_source_audit (
    "city_id"        city_code NOT NULL REFERENCES cities(id)
  , "computed_date"  DATE
  , "source" TEXT NOT NULL
  , "status" TEXT
  , "reference" TEXT
  , "detail" TEXT
  , "engine" TEXT
  , "pushed_down" TEXT
  , "scanned_rows" BIGINT
  , "window_start" TEXT
  , "window_end" TEXT
  , "current_device_filter" TEXT
  , "rows_removed_by_current_filter" BIGINT
  , "selected_rows" BIGINT
  , PRIMARY KEY ("city_id", "source")
);

-- ps3_v25_prediction_explainability: 3 rows in the reference run, 12 columns, PK (city_id, target, model_output, oos_episode_id, feature)
CREATE TABLE IF NOT EXISTS ps3_v25_prediction_explainability (
    "city_id"        city_code NOT NULL REFERENCES cities(id)
  , "computed_date"  DATE
  , "target" TEXT NOT NULL
  , "model_output" TEXT NOT NULL
  , "oos_episode_id" TEXT NOT NULL
  , "device_id" TEXT
  , "mars_device_category" TEXT
  , "predicted_label" TEXT
  , "feature" TEXT NOT NULL
  , "shap_value" TEXT
  , "abs_shap_value" TEXT
  , "explanation_status" TEXT
  , "explanation_note" TEXT
  , "model_scope" TEXT
  , PRIMARY KEY ("city_id", "target", "model_output", "oos_episode_id", "feature")
);

-- ps3_v25_repeat_interval: 12 rows in the reference run, 11 columns, PK (city_id, component_attribution, mars_device_category)
CREATE TABLE IF NOT EXISTS ps3_v25_repeat_interval (
    "city_id"        city_code NOT NULL REFERENCES cities(id)
  , "computed_date"  DATE
  , "component_attribution" TEXT NOT NULL
  , "mars_device_category" TEXT NOT NULL
  , "attributed_episodes" BIGINT
  , "devices" BIGINT
  , "episodes_with_a_next" BIGINT
  , "median_days_to_next" DOUBLE PRECISION
  , "p25_days_to_next" DOUBLE PRECISION
  , "mean_days_to_next" DOUBLE PRECISION
  , "repeat_rate_within_horizon" DOUBLE PRECISION
  , "horizon_days" BIGINT
  , "basis" TEXT
  , PRIMARY KEY ("city_id", "component_attribution", "mars_device_category")
);

-- ps3_v25_root_cause_evidence_audit: 7 rows in the reference run, 5 columns, PK (city_id, source)
CREATE TABLE IF NOT EXISTS ps3_v25_root_cause_evidence_audit (
    "city_id"        city_code NOT NULL REFERENCES cities(id)
  , "computed_date"  DATE
  , "source" TEXT NOT NULL
  , "status" TEXT
  , "reference" TEXT
  , "rows" DOUBLE PRECISION
  , "detail" TEXT
  , PRIMARY KEY ("city_id", "source")
);

-- ps3_v25_run_stage_audit: 8 rows in the reference run, 9 columns, PK (city_id, stage)
CREATE TABLE IF NOT EXISTS ps3_v25_run_stage_audit (
    "city_id"        city_code NOT NULL REFERENCES cities(id)
  , "computed_date"  DATE
  , "stage" TEXT NOT NULL
  , "status" TEXT
  , "detail" TEXT
  , "at_utc" TEXT
  , "rows" DOUBLE PRECISION
  , "mode" TEXT
  , "evidence_sources" DOUBLE PRECISION
  , "taxonomy_rows" DOUBLE PRECISION
  , "model_runs" JSONB
  , PRIMARY KEY ("city_id", "stage")
);

-- ps3_v25_run_status: 19 rows in the reference run, 14 columns, PK (city_id, table_name)
CREATE TABLE IF NOT EXISTS ps3_v25_run_status (
    "city_id"        city_code NOT NULL REFERENCES cities(id)
  , "computed_date"  DATE
  , "run_id" TEXT
  , "revision" TEXT
  , "computed_date" TEXT
  , "table_name" TEXT NOT NULL
  , "publish_status" TEXT
  , "rows" BIGINT
  , "path" TEXT
  , "run_mode" TEXT
  , "data_as_of_date" TEXT
  , "is_current_operational_score" BOOLEAN
  , "computed_at_utc" TEXT
  , "run_is_coherent" BOOLEAN
  , "tables_published" BIGINT
  , "tables_total" BIGINT
  , PRIMARY KEY ("city_id", "table_name")
);

-- ps3_v25_serial_reliability: 5,063 rows in the reference run, 10 columns, PK (city_id, component_serial_nbr, device_id, mars_device_category)
CREATE TABLE IF NOT EXISTS ps3_v25_serial_reliability (
    "city_id"        city_code NOT NULL REFERENCES cities(id)
  , "computed_date"  DATE
  , "component_serial_nbr" TEXT NOT NULL
  , "device_id" TEXT NOT NULL
  , "mars_device_category" TEXT NOT NULL
  , "oos_episode_count" BIGINT
  , "first_episode_at" TIMESTAMP
  , "latest_episode_at" TIMESTAMP
  , "component_attributions" BIGINT
  , "confirmed_root_cause_episodes" BIGINT
  , "observed_span_days" DOUBLE PRECISION
  , "episodes_per_100_observed_days" DOUBLE PRECISION
  , PRIMARY KEY ("city_id", "component_serial_nbr", "device_id", "mars_device_category")
);
CREATE INDEX IF NOT EXISTS idx_ps3_v25_serial_reliability_device_id ON ps3_v25_serial_reliability ("city_id", "device_id");

-- ps3_v25_source_column_profile: 7 rows in the reference run, 9 columns, PK (city_id, column)
CREATE TABLE IF NOT EXISTS ps3_v25_source_column_profile (
    "city_id"        city_code NOT NULL REFERENCES cities(id)
  , "computed_date"  DATE
  , "column" TEXT NOT NULL
  , "status" TEXT
  , "rows" BIGINT
  , "non_null" BIGINT
  , "null_rate" DOUBLE PRECISION
  , "distinct_values" BIGINT
  , "deterministic_given_event_type" BOOLEAN
  , "usable_as_observed_label" BOOLEAN
  , "note" TEXT
  , PRIMARY KEY ("city_id", "column")
);

-- ---------------------------------------------------------------------
-- The /ps3/status source. One row per table per run: if run_id or
-- computed_date disagree across the union, the dashboard is half-loaded and
-- this is the cheapest way to see it.
-- ---------------------------------------------------------------------
CREATE OR REPLACE VIEW v_ps3_v25_status AS
SELECT 'ps3_v25_causal_balance' AS table_name, city_id, computed_date, COUNT(*) AS rows
  FROM ps3_v25_causal_balance GROUP BY city_id, computed_date
UNION ALL
SELECT 'ps3_v25_causal_effects' AS table_name, city_id, computed_date, COUNT(*) AS rows
  FROM ps3_v25_causal_effects GROUP BY city_id, computed_date
UNION ALL
SELECT 'ps3_v25_commanded_split' AS table_name, city_id, computed_date, COUNT(*) AS rows
  FROM ps3_v25_commanded_split GROUP BY city_id, computed_date
UNION ALL
SELECT 'ps3_v25_component_summary' AS table_name, city_id, computed_date, COUNT(*) AS rows
  FROM ps3_v25_component_summary GROUP BY city_id, computed_date
UNION ALL
SELECT 'ps3_v25_device_day' AS table_name, city_id, computed_date, COUNT(*) AS rows
  FROM ps3_v25_device_day GROUP BY city_id, computed_date
UNION ALL
SELECT 'ps3_v25_device_episode_fact' AS table_name, city_id, computed_date, COUNT(*) AS rows
  FROM ps3_v25_device_episode_fact GROUP BY city_id, computed_date
UNION ALL
SELECT 'ps3_v25_device_reliability' AS table_name, city_id, computed_date, COUNT(*) AS rows
  FROM ps3_v25_device_reliability GROUP BY city_id, computed_date
UNION ALL
SELECT 'ps3_v25_device_summary' AS table_name, city_id, computed_date, COUNT(*) AS rows
  FROM ps3_v25_device_summary GROUP BY city_id, computed_date
UNION ALL
SELECT 'ps3_v25_facility_rollup' AS table_name, city_id, computed_date, COUNT(*) AS rows
  FROM ps3_v25_facility_rollup GROUP BY city_id, computed_date
UNION ALL
SELECT 'ps3_v25_label_maturity' AS table_name, city_id, computed_date, COUNT(*) AS rows
  FROM ps3_v25_label_maturity GROUP BY city_id, computed_date
UNION ALL
SELECT 'ps3_v25_model_feature_importance' AS table_name, city_id, computed_date, COUNT(*) AS rows
  FROM ps3_v25_model_feature_importance GROUP BY city_id, computed_date
UNION ALL
SELECT 'ps3_v25_model_scorecard' AS table_name, city_id, computed_date, COUNT(*) AS rows
  FROM ps3_v25_model_scorecard GROUP BY city_id, computed_date
UNION ALL
SELECT 'ps3_v25_oos_source_audit' AS table_name, city_id, computed_date, COUNT(*) AS rows
  FROM ps3_v25_oos_source_audit GROUP BY city_id, computed_date
UNION ALL
SELECT 'ps3_v25_prediction_explainability' AS table_name, city_id, computed_date, COUNT(*) AS rows
  FROM ps3_v25_prediction_explainability GROUP BY city_id, computed_date
UNION ALL
SELECT 'ps3_v25_repeat_interval' AS table_name, city_id, computed_date, COUNT(*) AS rows
  FROM ps3_v25_repeat_interval GROUP BY city_id, computed_date
UNION ALL
SELECT 'ps3_v25_root_cause_evidence_audit' AS table_name, city_id, computed_date, COUNT(*) AS rows
  FROM ps3_v25_root_cause_evidence_audit GROUP BY city_id, computed_date
UNION ALL
SELECT 'ps3_v25_run_stage_audit' AS table_name, city_id, computed_date, COUNT(*) AS rows
  FROM ps3_v25_run_stage_audit GROUP BY city_id, computed_date
UNION ALL
SELECT 'ps3_v25_run_status' AS table_name, city_id, computed_date, COUNT(*) AS rows
  FROM ps3_v25_run_status GROUP BY city_id, computed_date
UNION ALL
SELECT 'ps3_v25_serial_reliability' AS table_name, city_id, computed_date, COUNT(*) AS rows
  FROM ps3_v25_serial_reliability GROUP BY city_id, computed_date
UNION ALL
SELECT 'ps3_v25_source_column_profile' AS table_name, city_id, computed_date, COUNT(*) AS rows
  FROM ps3_v25_source_column_profile GROUP BY city_id, computed_date;
