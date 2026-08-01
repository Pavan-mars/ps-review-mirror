-- =====================================================================
-- 39_ps3_v2.sql                                              29-Jul-2026
--
-- PS3 HARDENED REMEDIATION RUN -> SERVING TABLES
--
-- Source: s3://cubic-mars-pm-s3-datalake-dev-gold-170202974600/
--         chicago/ps3_hardened_remediation/runs/ps3_20260729T074311Z/tables/
-- Column lists below were READ FROM THE PARQUET SCHEMAS, not inferred.
--
-- NOTHING HERE TOUCHES THE EXISTING PS3 TABLES. ps3_severity_predictions,
-- ps3_category_coverage, ps3_device_severity and every other ps3_* object
-- created by sql/03, 06, 15, 17, 19, 23 and 24 is left exactly as it is and
-- remains the Plan B feed. Every name below is prefixed ps3_v2_.
--
-- TWO DELIBERATE CHOICES, BOTH LEARNED THE HARD WAY ON THIS PROGRAMME:
--
-- 1. SURROGATE PRIMARY KEYS ONLY. Every table gets a BIGSERIAL id. No natural
--    unique constraint anywhere. ux_ps5_serial_rul_key failed on real data
--    because an assumed grain was wrong, and that failure blocked a whole
--    migration. A load that succeeds and lets the data tell you its grain beats
--    a constraint that guesses it.
--
-- 2. TIMESTAMPS ARE STORED AS TEXT. The run publishes ISO-8601 strings. Casting
--    them at load time is one more thing that can fail at 3am before a demo,
--    and ISO-8601 sorts correctly as text, so ORDER BY still works. The cost is
--    that date arithmetic must be cast explicitly; that is a fair trade today.
-- =====================================================================

CREATE TABLE IF NOT EXISTS ps3_v2_run_scorecard (
  id                BIGSERIAL PRIMARY KEY,
  city_id           TEXT NOT NULL,
  pipeline_version  TEXT NOT NULL,
  run_id            TEXT,
  device_category                   TEXT,
  head                              TEXT,
  status                            TEXT,
  champion                          TEXT,
  validation_f1_macro               DOUBLE PRECISION,
  test_f1_macro                     DOUBLE PRECISION,
  promotion_gate_pass               TEXT,
  shap_status                       TEXT,
  shap_rows                         BIGINT,
  causal_status                     TEXT,
  causal_dashboard_ready_rows       BIGINT,
  reason                            TEXT,
  loaded_at         TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS ix_run_scorecard_city ON ps3_v2_run_scorecard (city_id, pipeline_version);

CREATE TABLE IF NOT EXISTS ps3_v2_severity_action_queue (
  id                BIGSERIAL PRIMARY KEY,
  city_id           TEXT NOT NULL,
  pipeline_version  TEXT NOT NULL,
  run_id            TEXT,
  device_category                   TEXT,
  device_id                         TEXT,
  serial_number                     TEXT,
  facility_id                       TEXT,
  source_event_timestamp            TEXT,
  data_as_of_timestamp              TEXT,
  availability_event_id             TEXT,
  predicted_severity                TEXT,
  critical_probability              DOUBLE PRECISION,
  major_probability                 DOUBLE PRECISION,
  prediction_confidence             DOUBLE PRECISION,
  decision_threshold                DOUBLE PRECISION,
  action_band                       TEXT,
  action_priority_score             DOUBLE PRECISION,
  prior_incidents_24h               BIGINT,
  prior_incidents_7d                BIGINT,
  prior_incidents_30d               BIGINT,
  prior_critical_rate_30d           DOUBLE PRECISION,
  hours_since_prior_incident        DOUBLE PRECISION,
  model                             TEXT,
  model_status                      TEXT,
  score_generated_at_utc            TEXT,
  prediction_scope                  TEXT,
  loaded_at         TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS ix_severity_action_queue_city ON ps3_v2_severity_action_queue (city_id, pipeline_version);
CREATE INDEX IF NOT EXISTS ix_severity_action_queue_0 ON ps3_v2_severity_action_queue (city_id, pipeline_version, action_priority_score DESC);
CREATE INDEX IF NOT EXISTS ix_severity_action_queue_1 ON ps3_v2_severity_action_queue (city_id, device_id);

CREATE TABLE IF NOT EXISTS ps3_v2_shap_incident (
  id                BIGSERIAL PRIMARY KEY,
  city_id           TEXT NOT NULL,
  pipeline_version  TEXT NOT NULL,
  run_id            TEXT,
  device_category                   TEXT,
  head                              TEXT,
  availability_event_id             TEXT,
  device_id                         TEXT,
  serial_number                     TEXT,
  facility_id                       TEXT,
  event_timestamp                   TEXT,
  actual_label                      TEXT,
  predicted_label                   TEXT,
  predicted_probability             DOUBLE PRECISION,
  feature                           TEXT,
  feature_value                     TEXT,
  shap_value                        DOUBLE PRECISION,
  abs_shap_value                    DOUBLE PRECISION,
  feature_rank                      BIGINT,
  model                             TEXT,
  explainer                         TEXT,
  publication_status                TEXT,
  explanation_scope                 TEXT,
  loaded_at         TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS ix_shap_incident_city ON ps3_v2_shap_incident (city_id, pipeline_version);
CREATE INDEX IF NOT EXISTS ix_shap_incident_0 ON ps3_v2_shap_incident (city_id, availability_event_id);
CREATE INDEX IF NOT EXISTS ix_shap_incident_1 ON ps3_v2_shap_incident (city_id, device_id);

CREATE TABLE IF NOT EXISTS ps3_v2_shap_global (
  id                BIGSERIAL PRIMARY KEY,
  city_id           TEXT NOT NULL,
  pipeline_version  TEXT NOT NULL,
  run_id            TEXT,
  device_category                   TEXT,
  head                              TEXT,
  target_class                      TEXT,
  feature                           TEXT,
  mean_abs_shap                     DOUBLE PRECISION,
  mean_signed_shap                  DOUBLE PRECISION,
  feature_rank                      BIGINT,
  explained_partition               TEXT,
  model                             TEXT,
  explainer                         TEXT,
  n_explained_rows                  BIGINT,
  publication_status                TEXT,
  explanation_scope                 TEXT,
  loaded_at         TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS ix_shap_global_city ON ps3_v2_shap_global (city_id, pipeline_version);

CREATE TABLE IF NOT EXISTS ps3_v2_device_reliability (
  id                BIGSERIAL PRIMARY KEY,
  city_id           TEXT NOT NULL,
  pipeline_version  TEXT NOT NULL,
  run_id            TEXT,
  device_id                         TEXT,
  incident_count                    BIGINT,
  critical_incidents                BIGINT,
  critical_rate                     DOUBLE PRECISION,
  latest_incident_at                TEXT,
  mean_interval_hours               DOUBLE PRECISION,
  median_interval_hours             DOUBLE PRECISION,
  reliability_risk_band             TEXT,
  first_incident_at                 TEXT,
  recent_24h_recurrence             BIGINT,
  recent_30d_recurrence             BIGINT,
  days_since_latest_incident        DOUBLE PRECISION,
  device_category                   TEXT,
  loaded_at         TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS ix_device_reliability_city ON ps3_v2_device_reliability (city_id, pipeline_version);
CREATE INDEX IF NOT EXISTS ix_device_reliability_0 ON ps3_v2_device_reliability (city_id, pipeline_version, critical_rate DESC);

CREATE TABLE IF NOT EXISTS ps3_v2_component_reliability (
  id                BIGSERIAL PRIMARY KEY,
  city_id           TEXT NOT NULL,
  pipeline_version  TEXT NOT NULL,
  run_id            TEXT,
  component_label                   TEXT,
  incident_count                    BIGINT,
  affected_devices                  BIGINT,
  affected_serials                  BIGINT,
  critical_rate                     DOUBLE PRECISION,
  component_label_semantics         TEXT,
  portfolio_priority_score          DOUBLE PRECISION,
  critical_incidents                BIGINT,
  latest_incident_at                TEXT,
  max_prior_incidents_30d           BIGINT,
  device_category                   TEXT,
  loaded_at         TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS ix_component_reliability_city ON ps3_v2_component_reliability (city_id, pipeline_version);

CREATE TABLE IF NOT EXISTS ps3_v2_facility_hotspots (
  id                BIGSERIAL PRIMARY KEY,
  city_id           TEXT NOT NULL,
  pipeline_version  TEXT NOT NULL,
  run_id            TEXT,
  facility_id                       TEXT,
  incident_count                    BIGINT,
  affected_devices                  BIGINT,
  critical_rate                     DOUBLE PRECISION,
  latest_incident_at                TEXT,
  max_facility_prior_incidents_24h  BIGINT,
  device_category                   TEXT,
  loaded_at         TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS ix_facility_hotspots_city ON ps3_v2_facility_hotspots (city_id, pipeline_version);
CREATE INDEX IF NOT EXISTS ix_facility_hotspots_0 ON ps3_v2_facility_hotspots (city_id, pipeline_version, incident_count DESC);

CREATE TABLE IF NOT EXISTS ps3_v2_model_comparison (
  id                BIGSERIAL PRIMARY KEY,
  city_id           TEXT NOT NULL,
  pipeline_version  TEXT NOT NULL,
  run_id            TEXT,
  device_category                   TEXT,
  head                              TEXT,
  model                             TEXT,
  train_f1_macro                    DOUBLE PRECISION,
  validation_f1_macro               DOUBLE PRECISION,
  validation_accuracy               DOUBLE PRECISION,
  validation_balanced_accuracy      DOUBLE PRECISION,
  train_validation_f1_gap           DOUBLE PRECISION,
  validation_stability_pass         TEXT,
  validation_stability_reasons      TEXT,
  test_f1_macro                     DOUBLE PRECISION,
  promotion_gate_pass               TEXT,
  promotion_gate_reasons            TEXT,
  validation_ece                    DOUBLE PRECISION,
  validation_roc_auc_macro_ovr      DOUBLE PRECISION,
  decision_threshold                DOUBLE PRECISION,
  fit_status                        TEXT,
  selected_on_validation_only       TEXT,
  test_used_for_model_selection     TEXT,
  test_accuracy                     DOUBLE PRECISION,
  test_balanced_accuracy            DOUBLE PRECISION,
  test_ece                          DOUBLE PRECISION,
  test_constant_predictor           TEXT,
  test_macro_precision              DOUBLE PRECISION,
  test_macro_recall                 DOUBLE PRECISION,
  test_support                      BIGINT,
  test_brier                        DOUBLE PRECISION,
  test_log_loss                     DOUBLE PRECISION,
  test_roc_auc_macro_ovr            DOUBLE PRECISION,
  test_validation_f1_drop           DOUBLE PRECISION,
  loaded_at         TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS ix_model_comparison_city ON ps3_v2_model_comparison (city_id, pipeline_version);

CREATE TABLE IF NOT EXISTS ps3_v2_driver_importance (
  id                BIGSERIAL PRIMARY KEY,
  city_id           TEXT NOT NULL,
  pipeline_version  TEXT NOT NULL,
  run_id            TEXT,
  device_category                   TEXT,
  head                              TEXT,
  model                             TEXT,
  feature                           TEXT,
  mean_macro_f1_drop                DOUBLE PRECISION,
  std_macro_f1_drop                 DOUBLE PRECISION,
  n_validation_rows                 BIGINT,
  importance_scope                  TEXT,
  interpretation                    TEXT,
  loaded_at         TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS ix_driver_importance_city ON ps3_v2_driver_importance (city_id, pipeline_version);

CREATE TABLE IF NOT EXISTS ps3_v2_promotion_status (
  id                BIGSERIAL PRIMARY KEY,
  city_id           TEXT NOT NULL,
  pipeline_version  TEXT NOT NULL,
  run_id            TEXT,
  device_category                   TEXT,
  head                              TEXT,
  status                            TEXT,
  champion_selected_on_validation   TEXT,
  action_feed_eligible              TEXT,
  target                            TEXT,
  target_semantics                  TEXT,
  reason                            TEXT,
  loaded_at         TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS ix_promotion_status_city ON ps3_v2_promotion_status (city_id, pipeline_version);

CREATE TABLE IF NOT EXISTS ps3_v2_causal_effects (
  id                BIGSERIAL PRIMARY KEY,
  city_id           TEXT NOT NULL,
  pipeline_version  TEXT NOT NULL,
  run_id            TEXT,
  device_category                   TEXT,
  treatment_id                      TEXT,
  treatment_column                  TEXT,
  treatment_definition              TEXT,
  outcome_id                        TEXT,
  outcome_label                     TEXT,
  target_semantics                  TEXT,
  estimation_status                 TEXT,
  n_crossfit_rows                   BIGINT,
  treated_rows                      BIGINT,
  control_rows                      BIGINT,
  event_rows                        BIGINT,
  non_event_rows                    BIGINT,
  aipw_risk_difference              DOUBLE PRECISION,
  confidence_interval_95_low        DOUBLE PRECISION,
  confidence_interval_95_high       DOUBLE PRECISION,
  standard_error                    DOUBLE PRECISION,
  p_value_normal_approx             DOUBLE PRECISION,
  propensity_overlap_share          DOUBLE PRECISION,
  max_abs_weighted_smd_numeric      DOUBLE PRECISION,
  balance_pass                      TEXT,
  overlap_pass                      TEXT,
  dashboard_ready                   TEXT,
  evidence_level                    TEXT,
  scope                             TEXT,
  reason                            TEXT,
  loaded_at         TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS ix_causal_effects_city ON ps3_v2_causal_effects (city_id, pipeline_version);

CREATE TABLE IF NOT EXISTS ps3_v2_display_policy (
  id                BIGSERIAL PRIMARY KEY,
  city_id           TEXT NOT NULL,
  pipeline_version  TEXT NOT NULL,
  run_id            TEXT,
  dashboard_element                 TEXT,
  dataset                           TEXT,
  display_allowed                   TEXT,
  row_count                         BIGINT,
  display_label                     TEXT,
  required_disclaimer               TEXT,
  loaded_at         TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS ix_display_policy_city ON ps3_v2_display_policy (city_id, pipeline_version);

CREATE TABLE IF NOT EXISTS ps3_v2_readiness (
  id                BIGSERIAL PRIMARY KEY,
  city_id           TEXT NOT NULL,
  pipeline_version  TEXT NOT NULL,
  run_id            TEXT,
  device_category                   TEXT,
  gold_rows                         BIGINT,
  severity_label_coverage           DOUBLE PRECISION,
  component_label_coverage          DOUBLE PRECISION,
  configured_enrichment_sources     TEXT,
  loaded_enrichment_sources         TEXT,
  data_readiness_status             TEXT,
  reason                            TEXT,
  loaded_at         TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS ix_readiness_city ON ps3_v2_readiness (city_id, pipeline_version);

CREATE TABLE IF NOT EXISTS ps3_v2_data_freshness (
  id                BIGSERIAL PRIMARY KEY,
  city_id           TEXT NOT NULL,
  pipeline_version  TEXT NOT NULL,
  run_id            TEXT,
  data_as_of_timestamp              TEXT,
  run_timestamp_utc                 TEXT,
  input_rows                        BIGINT,
  eligible_rows                     BIGINT,
  event_start                       TEXT,
  event_end                         TEXT,
  freshness_scope                   TEXT,
  loaded_at         TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS ix_data_freshness_city ON ps3_v2_data_freshness (city_id, pipeline_version);


-- ---------------------------------------------------------------------------
-- RUN REGISTRY + CURRENT-VERSION RESOLVER
-- Every view resolves the version through v_ps3_v2_current, so loading a newer
-- run switches the dashboard over with no code change and rolling back is a
-- DELETE of the newer pipeline_version.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS ps3_v2_runs (
  city_id           TEXT NOT NULL,
  pipeline_version  TEXT NOT NULL,
  run_id            TEXT NOT NULL,
  s3_prefix         TEXT,
  tables_loaded     INTEGER,
  rows_loaded       BIGINT,
  loaded_at         TIMESTAMPTZ DEFAULT NOW(),
  PRIMARY KEY (city_id, pipeline_version, run_id)
);

CREATE OR REPLACE VIEW v_ps3_v2_current AS
SELECT DISTINCT ON (city_id)
       city_id, pipeline_version, run_id, s3_prefix, tables_loaded, rows_loaded, loaded_at
FROM ps3_v2_runs ORDER BY city_id, loaded_at DESC;

-- The action queue the operations team works. Ranked by the run's own priority
-- score, not by probability: the score already folds in recurrence and recency,
-- which is what makes it a queue rather than a leaderboard.
CREATE OR REPLACE VIEW v_ps3_v2_queue AS
SELECT q.*,
  CASE WHEN q.prior_incidents_30d >= 3 THEN 'REPEAT OFFENDER - 3+ incidents in 30 days'
       WHEN q.prior_incidents_7d  >= 2 THEN 'REPEATING - 2+ incidents this week'
       WHEN q.prior_incidents_24h >= 1 THEN 'RECURRING - already seen in last 24h'
       ELSE 'first incident in the window' END          AS recurrence_note,
  CASE WHEN q.prediction_confidence >= 0.80 THEN 'HIGH'
       WHEN q.prediction_confidence >= 0.60 THEN 'MODERATE'
       ELSE 'LOW - treat as a hint, not a call' END     AS confidence_band
FROM ps3_v2_severity_action_queue q
JOIN v_ps3_v2_current c ON c.city_id=q.city_id AND c.pipeline_version=q.pipeline_version;

-- Headline scorecard with the promotion gate spelled out in words.
CREATE OR REPLACE VIEW v_ps3_v2_scorecard AS
SELECT s.*,
  CASE WHEN LOWER(COALESCE(s.promotion_gate_pass,'')) IN ('true','t','yes','1')
       THEN 'PROMOTED - cleared the gate on validation'
       ELSE 'NOT PROMOTED - ' || COALESCE(NULLIF(s.reason,''),'gate not met') END AS gate_note,
  CASE WHEN s.test_f1_macro IS NULL OR s.validation_f1_macro IS NULL THEN NULL
       ELSE ROUND((s.validation_f1_macro - s.test_f1_macro)::numeric,4) END       AS val_minus_test
FROM ps3_v2_run_scorecard s
JOIN v_ps3_v2_current c ON c.city_id=s.city_id AND c.pipeline_version=s.pipeline_version;

-- Per-incident explanation, top factors first.
CREATE OR REPLACE VIEW v_ps3_v2_shap AS
SELECT x.* FROM ps3_v2_shap_incident x
JOIN v_ps3_v2_current c ON c.city_id=x.city_id AND c.pipeline_version=x.pipeline_version;

-- Causal evidence, with the honest verdict attached. estimation_status and
-- dashboard_ready come from the run itself -- a row that did not clear balance
-- and overlap is NOT evidence and is labelled so rather than hidden.
CREATE OR REPLACE VIEW v_ps3_v2_causal AS
SELECT e.*,
  CASE WHEN LOWER(COALESCE(e.dashboard_ready,'')) IN ('true','t','yes','1')
         THEN 'USABLE - passed balance and overlap checks'
       WHEN LOWER(COALESCE(e.balance_pass,'')) NOT IN ('true','t','yes','1')
         THEN 'NOT USABLE - groups were not comparable (balance check failed)'
       WHEN LOWER(COALESCE(e.overlap_pass,'')) NOT IN ('true','t','yes','1')
         THEN 'NOT USABLE - too little overlap between treated and untreated'
       ELSE 'NOT USABLE - ' || COALESCE(NULLIF(e.reason,''),'did not clear the checks') END AS causal_verdict
FROM ps3_v2_causal_effects e
JOIN v_ps3_v2_current c ON c.city_id=e.city_id AND c.pipeline_version=e.pipeline_version;

-- What the run itself says may be shown. If display_allowed is false the front
-- end must not render that element -- the notebook is the authority on this,
-- not the dashboard.
CREATE OR REPLACE VIEW v_ps3_v2_policy AS
SELECT p.* FROM ps3_v2_display_policy p
JOIN v_ps3_v2_current c ON c.city_id=p.city_id AND c.pipeline_version=p.pipeline_version;

CREATE OR REPLACE VIEW v_ps3_v2_table_status AS
SELECT 'ps3_v2_severity_action_queue' AS table_name,(SELECT COUNT(*) FROM ps3_v2_severity_action_queue) AS n_rows
UNION ALL SELECT 'ps3_v2_shap_incident',(SELECT COUNT(*) FROM ps3_v2_shap_incident)
UNION ALL SELECT 'ps3_v2_shap_global',(SELECT COUNT(*) FROM ps3_v2_shap_global)
UNION ALL SELECT 'ps3_v2_device_reliability',(SELECT COUNT(*) FROM ps3_v2_device_reliability)
UNION ALL SELECT 'ps3_v2_component_reliability',(SELECT COUNT(*) FROM ps3_v2_component_reliability)
UNION ALL SELECT 'ps3_v2_facility_hotspots',(SELECT COUNT(*) FROM ps3_v2_facility_hotspots)
UNION ALL SELECT 'ps3_v2_run_scorecard',(SELECT COUNT(*) FROM ps3_v2_run_scorecard)
UNION ALL SELECT 'ps3_v2_model_comparison',(SELECT COUNT(*) FROM ps3_v2_model_comparison)
UNION ALL SELECT 'ps3_v2_causal_effects',(SELECT COUNT(*) FROM ps3_v2_causal_effects)
UNION ALL SELECT 'ps3_v2_display_policy',(SELECT COUNT(*) FROM ps3_v2_display_policy)
UNION ALL SELECT 'ps3_v2_runs',(SELECT COUNT(*) FROM ps3_v2_runs);
