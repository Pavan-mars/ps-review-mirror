-- =====================================================================
-- sql/45_ps3_v25.sql
--
-- The 20 tables published by PS3_RootCause_Severity_SageMaker_Source_First_V26.
--
-- BUILT FROM A REAL RUN, NOT A FIXTURE. Every column name, value type and null
-- rate below was read from ps3_schema_dump.py executed in the same kernel as
-- run 6a7002b0-a0fc-41f8-bb0f-1ad5a5358edf (source_first_ps1_ps2_ps3_label_aligned_v26, computed_date 2026-04-11,
-- run_mode REPLAY). 20 tables, 271 columns, 1 JSONB.
--
-- PURELY ADDITIVE. Nothing here drops, alters or renames anything. Every
-- existing PS3 table -- ps3_incident_predictions, ps3_device_predictions,
-- ps3_v2_rootcause, ps3_serial_risk and the rest the deployed API serves today
-- -- is untouched and remains the plan-B set. Every new name is prefixed
-- ps3_v25_ so a collision with that generation is structurally impossible.
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
-- TYPES FOLLOW OBSERVED VALUES, NOT PANDAS DTYPES.
-- ps3_v25_device_episode_fact.predicted_component_confidence is an object-dtype
-- column holding python floats; keyed on the dtype it would have been TEXT and
-- every dashboard query would have had to cast a confidence score.
-- =====================================================================

-- ALL-NULL IN THE REAL RUN -- TYPE CHOSEN BY INTENT, NOT OBSERVED.
-- pandas types an all-NaN column float64 whatever it will really hold, so
-- each of these is declared TEXT, which accepts any future value. They are
-- all-null because the label/taxonomy exports they depend on are not yet
-- configured, not because the column is dead:
--   ps3_v25_device_episode_fact.linked_severity
--   ps3_v25_device_episode_fact.linked_component
--   ps3_v25_device_episode_fact.linked_root_cause
--   ps3_v25_device_episode_fact.linked_root_cause_domain
--   ps3_v25_device_episode_fact.linked_source
--   ps3_v25_device_episode_fact.link_method
--   ps3_v25_device_episode_fact.observed_severity_label
--   ps3_v25_device_episode_fact.confirmed_root_cause_label
--   ps3_v25_device_episode_fact.confirmed_root_cause_domain
--   ps3_v25_device_episode_fact.root_cause_evidence_source
--   ps3_v25_device_episode_fact.root_cause_link_method
--   ps3_v25_device_episode_fact.candidate_root_cause_raw
--   ps3_v25_device_summary.latest_dashboard_severity
--   ps3_v25_device_summary.latest_dashboard_root_cause_domain
--   ps3_v25_prediction_explainability.device_id
--   ps3_v25_prediction_explainability.mars_device_category
--   ps3_v25_prediction_explainability.predicted_label
--   ps3_v25_root_cause_evidence_audit.reference
--
-- TYPED BY INTENT, READ OFF THE BUILDER SOURCE RATHER THAN THE RUN:
--   ps3_v25_device_day.event_date -> DATE (observed dtype datetime64[ns], null_rate 0.0)
--   ps3_v25_device_episode_fact.linked_confidence -> DOUBLE PRECISION (observed dtype object, null_rate 1.0)
--   ps3_v25_device_episode_fact.root_cause_confidence -> DOUBLE PRECISION (observed dtype object, null_rate 1.0)
--   ps3_v25_prediction_explainability.shap_value -> DOUBLE PRECISION (observed dtype float64, null_rate 1.0)
--   ps3_v25_prediction_explainability.abs_shap_value -> DOUBLE PRECISION (observed dtype float64, null_rate 1.0)
--
-- KNOWN SHAPE ISSUES IN THE SOURCE RUN (recorded here, fixed in the notebook,
-- not worked around in SQL):
--   ps3_v25_device_episode_fact._episode_row_id is an internal scratch column
--     that leaked past the publish boundary. Harmless to load; drop it from
--     the notebook's publish list rather than from this DDL, so the table and
--     the parquet keep the same shape.
--   ps3_v25_device_day has 54,239 rows -- exactly the episode count -- and
--     oos_episode_starts is 1 on every row. That is one row per episode, not
--     one row per device-day. The declared key still holds only if no device
--     has two episodes in a day; see the key report.
--   ps3_v25_serial_reliability.component_serial_nbr has 2 distinct values over
--     2,762 rows: the serial is almost entirely the unattributed bucket. The
--     composite key holds because device_id carries the grain.
--

-- ps3_v25_causal_balance: 54 rows in run 6a7002b0, 4 columns, PK (city_id, treatment_component, covariate)
CREATE TABLE IF NOT EXISTS ps3_v25_causal_balance (
    "city_id"        city_code NOT NULL REFERENCES cities(id)
  , "computed_date"  DATE
  , "treatment_component" TEXT NOT NULL
  , "covariate" TEXT NOT NULL
  , "standardised_mean_difference" DOUBLE PRECISION
  , "balance_status" TEXT
  , PRIMARY KEY ("city_id", "treatment_component", "covariate")
);

-- ps3_v25_causal_effects: 6 rows in run 6a7002b0, 22 columns, PK (city_id, treatment_component, outcome)
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

-- ps3_v25_commanded_split: 3 rows in run 6a7002b0, 5 columns, PK (city_id, mars_device_category)
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

-- ps3_v25_component_summary: 14 rows in run 6a7002b0, 7 columns, PK (city_id, mars_device_category, component_attribution, dashboard_root_cause_domain, dashboard_severity)
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

-- ps3_v25_device_day: 54,239 rows in run 6a7002b0, 11 columns, PK (city_id, device_id, mars_device_category, event_date)
CREATE TABLE IF NOT EXISTS ps3_v25_device_day (
    "city_id"        city_code NOT NULL REFERENCES cities(id)
  , "computed_date"  DATE
  , "device_id" TEXT NOT NULL
  , "mars_device_category" TEXT NOT NULL
  , "event_date" DATE NOT NULL
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

-- ps3_v25_device_episode_fact: 54,239 rows in run 6a7002b0, 78 columns, PK (city_id, oos_episode_id)
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
  , "linked_severity" TEXT
  , "linked_component" TEXT
  , "linked_root_cause" TEXT
  , "linked_root_cause_domain" TEXT
  , "linked_confidence" DOUBLE PRECISION
  , "linked_source" TEXT
  , "link_method" TEXT
  , "observed_severity_label" TEXT
  , "severity_status" TEXT
  , "component_attribution" TEXT
  , "component_attribution_status" TEXT
  , "confirmed_root_cause_label" TEXT
  , "confirmed_root_cause_domain" TEXT
  , "root_cause_confidence" DOUBLE PRECISION
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
  , "predicted_component" TEXT
  , "predicted_component_confidence" DOUBLE PRECISION
  , "component_model_status" TEXT
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

-- ps3_v25_device_reliability: 2,806 rows in run 6a7002b0, 16 columns, PK (city_id, device_id, mars_device_category)
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

-- ps3_v25_device_summary: 2,806 rows in run 6a7002b0, 9 columns, PK (city_id, device_id, mars_device_category)
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

-- ps3_v25_facility_rollup: 366 rows in run 6a7002b0, 11 columns, PK (city_id, facility_id, mars_device_category)
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

-- ps3_v25_label_maturity: 3 rows in run 6a7002b0, 8 columns, PK (city_id, mars_device_category)
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

-- ps3_v25_model_feature_importance: 12 rows in run 6a7002b0, 5 columns, PK (city_id, target, model, feature, model_scope)
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

-- ps3_v25_model_scorecard: 8 rows in run 6a7002b0, 13 columns, PK (city_id, target, candidate_model, model_scope)
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

-- ps3_v25_oos_source_audit: 1 rows in run 6a7002b0, 12 columns, PK (city_id, source)
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

-- ps3_v25_prediction_explainability: 3 rows in run 6a7002b0, 12 columns, PK (city_id, target, model_output, oos_episode_id, feature)
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
  , "shap_value" DOUBLE PRECISION
  , "abs_shap_value" DOUBLE PRECISION
  , "explanation_status" TEXT
  , "explanation_note" TEXT
  , "model_scope" TEXT
  , PRIMARY KEY ("city_id", "target", "model_output", "oos_episode_id", "feature")
);

-- ps3_v25_repeat_interval: 14 rows in run 6a7002b0, 11 columns, PK (city_id, component_attribution, mars_device_category)
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

-- ps3_v25_root_cause_evidence_audit: 7 rows in run 6a7002b0, 5 columns, PK (city_id, source)
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

-- ps3_v25_run_stage_audit: 8 rows in run 6a7002b0, 9 columns, PK (city_id, stage)
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

-- ps3_v25_run_status: 19 rows in run 6a7002b0, 14 columns, PK (city_id, table_name)
CREATE TABLE IF NOT EXISTS ps3_v25_run_status (
    "city_id"        city_code NOT NULL REFERENCES cities(id)
  , "computed_date"  DATE
  , "run_id" TEXT
  , "revision" TEXT
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

-- ps3_v25_serial_reliability: 2,762 rows in run 6a7002b0, 10 columns, PK (city_id, component_serial_nbr, device_id, mars_device_category)
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

-- ps3_v25_source_column_profile: 7 rows in run 6a7002b0, 9 columns, PK (city_id, column)
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
-- The /ps3/status source. One row per table per run: if computed_date
-- disagrees across the union, the dashboard is half-loaded and this is the
-- cheapest way to see it.
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
