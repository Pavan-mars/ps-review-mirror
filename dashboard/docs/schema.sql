-- ============================================================================
-- CUBIC MARS - Predictive Analytics Dashboard
-- PostgreSQL Database Schema
-- Version: 1.0.0
-- Generated: 2026-05-02
-- ============================================================================
-- This schema maps 1:1 with the mock data functions in src/data/mockData.js.
-- Each table corresponds to a specific API endpoint that the dashboard will
-- consume once the backend is wired up.
-- ============================================================================

-- ============================================================================
-- 0. EXTENSIONS
-- ============================================================================
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- ============================================================================
-- 1. ENUM TYPES
-- ============================================================================
CREATE TYPE severity_level AS ENUM ('critical', 'high', 'medium', 'low');
CREATE TYPE device_type    AS ENUM ('readers', 'tvms', 'gates', 'validators');
CREATE TYPE city_code      AS ENUM ('CHI', 'BOS', 'LAX', 'TOC');
CREATE TYPE alert_status   AS ENUM ('active', 'acknowledged', 'resolved', 'suppressed');
CREATE TYPE user_role      AS ENUM ('admin', 'city_manager', 'device_sme', 'toc_operator');
CREATE TYPE deployment_env AS ENUM ('dev', 'uat', 'production');

CREATE TYPE toc_company AS ENUM (
  'RSPL', 'SET', 'WMT', 'LNER', 'TPT',
  'LSA', 'HAL', 'NTL', 'GAT', 'C2C', 'TFW'
);

-- ============================================================================
-- 2. REFERENCE TABLES
-- ============================================================================

-- Cities / Deployment Regions
CREATE TABLE cities (
  id          city_code   PRIMARY KEY,
  name        VARCHAR(64) NOT NULL,
  region      VARCHAR(64),
  timezone    VARCHAR(48) DEFAULT 'UTC',
  is_pilot    BOOLEAN     DEFAULT FALSE,
  go_live     DATE,
  created_at  TIMESTAMPTZ DEFAULT NOW()
);

INSERT INTO cities (id, name, region, is_pilot, go_live) VALUES
  ('CHI', 'Chicago',     'North America', TRUE,  '2026-01-15'),
  ('BOS', 'Boston',      'North America', FALSE, NULL),
  ('LAX', 'Los Angeles', 'North America', FALSE, NULL),
  ('TOC', 'TOC (UK)',    'Europe',        FALSE, NULL);

-- TOC Companies (UK Train Operating Companies)
CREATE TABLE toc_companies (
  code        toc_company  PRIMARY KEY,
  full_name   VARCHAR(128) NOT NULL,
  region      VARCHAR(64),
  active      BOOLEAN      DEFAULT TRUE,
  created_at  TIMESTAMPTZ  DEFAULT NOW()
);

INSERT INTO toc_companies (code, full_name, region) VALUES
  ('RSPL', 'Rail Services Plus Ltd',      'National'),
  ('SET',  'South Eastern Trains',        'South East'),
  ('WMT',  'West Midlands Trains',        'West Midlands'),
  ('LNER', 'London North Eastern Railway','East Coast'),
  ('TPT',  'TransPennine Trains',         'North'),
  ('LSA',  'London Southeastern Area',    'London'),
  ('HAL',  'Heathrow Airport Ltd',        'London'),
  ('NTL',  'Northern Trains Ltd',         'North'),
  ('GAT',  'Gatwick Airport Transit',     'South East'),
  ('C2C',  'c2c Rail',                    'Essex/East London'),
  ('TFW',  'Transport for Wales',         'Wales');

-- Devices catalogue
CREATE TABLE devices (
  id          SERIAL       PRIMARY KEY,
  device_type device_type  NOT NULL,
  label       VARCHAR(64)  NOT NULL,
  city_id     city_code    REFERENCES cities(id),
  toc_code    toc_company  REFERENCES toc_companies(code),
  asset_id    VARCHAR(64)  UNIQUE,
  installed   DATE,
  active      BOOLEAN      DEFAULT TRUE,
  created_at  TIMESTAMPTZ  DEFAULT NOW()
);

CREATE INDEX idx_devices_city   ON devices(city_id);
CREATE INDEX idx_devices_type   ON devices(device_type);
CREATE INDEX idx_devices_toc    ON devices(toc_code);

-- ============================================================================
-- 3. PROBLEM STATEMENT 1 — FAILURE PREDICTIONS  (FailurePredictions.jsx)
-- Maps to: generatePredictionData(), generateSeverityData(),
--          generateTimeSeriesData(), generateDeviceHealthData()
-- ============================================================================

-- Prediction summary per city/device
CREATE TABLE failure_predictions (
  id            UUID         DEFAULT uuid_generate_v4() PRIMARY KEY,
  city_id       city_code    NOT NULL REFERENCES cities(id),
  device_type   device_type  NOT NULL,
  toc_code      toc_company  REFERENCES toc_companies(code),
  predicted_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
  probability   NUMERIC(5,4) NOT NULL CHECK (probability BETWEEN 0 AND 1),
  severity      severity_level NOT NULL,
  confidence    NUMERIC(5,4) CHECK (confidence BETWEEN 0 AND 1),
  model_version VARCHAR(16)  DEFAULT '1.0',
  created_at    TIMESTAMPTZ  DEFAULT NOW()
);

CREATE INDEX idx_fp_city_device ON failure_predictions(city_id, device_type);
CREATE INDEX idx_fp_predicted   ON failure_predictions(predicted_at DESC);
CREATE INDEX idx_fp_severity    ON failure_predictions(severity);

-- Severity distribution snapshots (pie/bar chart data)
CREATE TABLE severity_distribution (
  id          UUID         DEFAULT uuid_generate_v4() PRIMARY KEY,
  city_id     city_code    NOT NULL REFERENCES cities(id),
  device_type device_type  NOT NULL,
  toc_code    toc_company  REFERENCES toc_companies(code),
  severity    severity_level NOT NULL,
  count       INTEGER      NOT NULL DEFAULT 0,
  snapshot_at TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_sd_snapshot ON severity_distribution(snapshot_at DESC);

-- Time series for trend charts
CREATE TABLE prediction_timeseries (
  id          UUID         DEFAULT uuid_generate_v4() PRIMARY KEY,
  city_id     city_code    NOT NULL REFERENCES cities(id),
  device_type device_type  NOT NULL,
  toc_code    toc_company  REFERENCES toc_companies(code),
  ts          TIMESTAMPTZ  NOT NULL,
  value       NUMERIC(10,4) NOT NULL,
  metric_name VARCHAR(64)  NOT NULL DEFAULT 'failure_prob',
  created_at  TIMESTAMPTZ  DEFAULT NOW()
);

CREATE INDEX idx_pts_ts     ON prediction_timeseries(ts DESC);
CREATE INDEX idx_pts_city   ON prediction_timeseries(city_id, device_type, ts DESC);

-- Device health scores
CREATE TABLE device_health (
  id              UUID         DEFAULT uuid_generate_v4() PRIMARY KEY,
  device_id       INTEGER      REFERENCES devices(id),
  city_id         city_code    NOT NULL REFERENCES cities(id),
  device_type     device_type  NOT NULL,
  toc_code        toc_company  REFERENCES toc_companies(code),
  health_score    NUMERIC(5,2) NOT NULL CHECK (health_score BETWEEN 0 AND 100),
  status          VARCHAR(16)  DEFAULT 'healthy',
  last_checked_at TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
  created_at      TIMESTAMPTZ  DEFAULT NOW()
);

CREATE INDEX idx_dh_city_type ON device_health(city_id, device_type);

-- ============================================================================
-- 4. PROBLEM STATEMENT 2 & 3 — ROOT CAUSE ANALYSIS  (RootCauseAnalysis.jsx)
-- Maps to: generateRootCauseData(), generateCascadeData(),
--          generateCorrelationMatrix(), generateContributingFactors()
-- ============================================================================

-- Root cause categories and their frequencies
CREATE TABLE root_causes (
  id            UUID         DEFAULT uuid_generate_v4() PRIMARY KEY,
  city_id       city_code    NOT NULL REFERENCES cities(id),
  device_type   device_type  NOT NULL,
  toc_code      toc_company  REFERENCES toc_companies(code),
  cause_category VARCHAR(128) NOT NULL,
  occurrence_count INTEGER   NOT NULL DEFAULT 0,
  severity      severity_level,
  avg_resolution_hrs NUMERIC(8,2),
  analyzed_at   TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
  created_at    TIMESTAMPTZ  DEFAULT NOW()
);

CREATE INDEX idx_rc_city ON root_causes(city_id, device_type);

-- Cascading failure chains
CREATE TABLE cascade_events (
  id              UUID         DEFAULT uuid_generate_v4() PRIMARY KEY,
  chain_id        UUID         NOT NULL,  -- groups events in same cascade
  city_id         city_code    NOT NULL REFERENCES cities(id),
  source_device   device_type  NOT NULL,
  target_device   device_type  NOT NULL,
  toc_code        toc_company  REFERENCES toc_companies(code),
  propagation_time_sec INTEGER NOT NULL,
  impact_score    NUMERIC(5,2),
  event_time      TIMESTAMPTZ  NOT NULL,
  created_at      TIMESTAMPTZ  DEFAULT NOW()
);

CREATE INDEX idx_ce_chain ON cascade_events(chain_id);
CREATE INDEX idx_ce_city  ON cascade_events(city_id, event_time DESC);

-- Correlation matrix between device types
CREATE TABLE device_correlations (
  id            UUID         DEFAULT uuid_generate_v4() PRIMARY KEY,
  city_id       city_code    NOT NULL REFERENCES cities(id),
  toc_code      toc_company  REFERENCES toc_companies(code),
  device_a      device_type  NOT NULL,
  device_b      device_type  NOT NULL,
  correlation   NUMERIC(5,4) NOT NULL CHECK (correlation BETWEEN -1 AND 1),
  sample_size   INTEGER,
  computed_at   TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_dc_city ON device_correlations(city_id);

-- Contributing factors (feature importance from ML models)
CREATE TABLE contributing_factors (
  id            UUID         DEFAULT uuid_generate_v4() PRIMARY KEY,
  city_id       city_code    NOT NULL REFERENCES cities(id),
  device_type   device_type  NOT NULL,
  toc_code      toc_company  REFERENCES toc_companies(code),
  factor_name   VARCHAR(128) NOT NULL,
  importance    NUMERIC(5,4) NOT NULL CHECK (importance BETWEEN 0 AND 1),
  direction     VARCHAR(16)  DEFAULT 'positive', -- positive or negative
  model_version VARCHAR(16),
  computed_at   TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_cf_city ON contributing_factors(city_id, device_type);

-- ============================================================================
-- 5. PROBLEM STATEMENT 4 — ANOMALIES & OUTLIERS  (AnomaliesOutliers.jsx)
-- Maps to: generateAnomalyData(), generateAnomalyTimeline(),
--          generateOutlierScatter(), generateAnomalyBreakdown()
-- ============================================================================

-- Detected anomalies
CREATE TABLE anomalies (
  id             UUID         DEFAULT uuid_generate_v4() PRIMARY KEY,
  city_id        city_code    NOT NULL REFERENCES cities(id),
  device_type    device_type  NOT NULL,
  toc_code       toc_company  REFERENCES toc_companies(code),
  anomaly_type   VARCHAR(64)  NOT NULL,
  severity       severity_level NOT NULL,
  anomaly_score  NUMERIC(5,4) NOT NULL,
  description    TEXT,
  detected_at    TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
  resolved_at    TIMESTAMPTZ,
  status         alert_status DEFAULT 'active',
  created_at     TIMESTAMPTZ  DEFAULT NOW()
);

CREATE INDEX idx_anom_city     ON anomalies(city_id, device_type);
CREATE INDEX idx_anom_detected ON anomalies(detected_at DESC);
CREATE INDEX idx_anom_status   ON anomalies(status);

-- Anomaly timeline (time-bucketed counts)
CREATE TABLE anomaly_timeline (
  id          UUID         DEFAULT uuid_generate_v4() PRIMARY KEY,
  city_id     city_code    NOT NULL REFERENCES cities(id),
  device_type device_type  NOT NULL,
  toc_code    toc_company  REFERENCES toc_companies(code),
  bucket_time TIMESTAMPTZ  NOT NULL,
  count       INTEGER      NOT NULL DEFAULT 0,
  avg_score   NUMERIC(5,4),
  created_at  TIMESTAMPTZ  DEFAULT NOW()
);

CREATE INDEX idx_at_bucket ON anomaly_timeline(bucket_time DESC);

-- Outlier scatter data (for scatter plots)
CREATE TABLE outlier_scores (
  id           UUID         DEFAULT uuid_generate_v4() PRIMARY KEY,
  city_id      city_code    NOT NULL REFERENCES cities(id),
  device_type  device_type  NOT NULL,
  toc_code     toc_company  REFERENCES toc_companies(code),
  x_metric     NUMERIC(10,4) NOT NULL,
  y_metric     NUMERIC(10,4) NOT NULL,
  is_outlier   BOOLEAN      DEFAULT FALSE,
  z_score      NUMERIC(6,3),
  recorded_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_os_city ON outlier_scores(city_id, device_type);

-- ============================================================================
-- 6. PROBLEM STATEMENT 5 — SLA PERFORMANCE  (SLAPerformance.jsx)
-- Maps to: generateSLAData(), generateComplianceTrend(),
--          generateSLABreachData(), generatePerformanceMetrics()
-- ============================================================================

-- SLA targets and actuals
CREATE TABLE sla_metrics (
  id             UUID         DEFAULT uuid_generate_v4() PRIMARY KEY,
  city_id        city_code    NOT NULL REFERENCES cities(id),
  device_type    device_type  NOT NULL,
  toc_code       toc_company  REFERENCES toc_companies(code),
  metric_name    VARCHAR(128) NOT NULL,
  target_value   NUMERIC(10,4) NOT NULL,
  actual_value   NUMERIC(10,4) NOT NULL,
  unit           VARCHAR(16)  DEFAULT '%',
  is_breached    BOOLEAN      GENERATED ALWAYS AS (actual_value < target_value) STORED,
  period_start   TIMESTAMPTZ  NOT NULL,
  period_end     TIMESTAMPTZ  NOT NULL,
  created_at     TIMESTAMPTZ  DEFAULT NOW()
);

CREATE INDEX idx_sla_city   ON sla_metrics(city_id, device_type);
CREATE INDEX idx_sla_period ON sla_metrics(period_start DESC);
CREATE INDEX idx_sla_breach ON sla_metrics(is_breached) WHERE is_breached = TRUE;

-- Compliance trend (rolling % compliance over time)
CREATE TABLE compliance_trend (
  id              UUID         DEFAULT uuid_generate_v4() PRIMARY KEY,
  city_id         city_code    NOT NULL REFERENCES cities(id),
  device_type     device_type  NOT NULL,
  toc_code        toc_company  REFERENCES toc_companies(code),
  compliance_pct  NUMERIC(5,2) NOT NULL CHECK (compliance_pct BETWEEN 0 AND 100),
  period_date     DATE         NOT NULL,
  created_at      TIMESTAMPTZ  DEFAULT NOW()
);

CREATE INDEX idx_ct_date ON compliance_trend(period_date DESC);

-- SLA breach events
CREATE TABLE sla_breaches (
  id              UUID         DEFAULT uuid_generate_v4() PRIMARY KEY,
  city_id         city_code    NOT NULL REFERENCES cities(id),
  device_type     device_type  NOT NULL,
  toc_code        toc_company  REFERENCES toc_companies(code),
  breach_type     VARCHAR(128) NOT NULL,
  severity        severity_level NOT NULL,
  duration_hrs    NUMERIC(8,2),
  root_cause      TEXT,
  remediation     TEXT,
  breached_at     TIMESTAMPTZ  NOT NULL,
  resolved_at     TIMESTAMPTZ,
  created_at      TIMESTAMPTZ  DEFAULT NOW()
);

CREATE INDEX idx_sb_city ON sla_breaches(city_id, device_type);
CREATE INDEX idx_sb_time ON sla_breaches(breached_at DESC);

-- Performance metrics (KPI cards)
CREATE TABLE performance_metrics (
  id              UUID         DEFAULT uuid_generate_v4() PRIMARY KEY,
  city_id         city_code    NOT NULL REFERENCES cities(id),
  device_type     device_type  NOT NULL,
  toc_code        toc_company  REFERENCES toc_companies(code),
  metric_name     VARCHAR(128) NOT NULL,
  current_value   NUMERIC(12,4) NOT NULL,
  previous_value  NUMERIC(12,4),
  change_pct      NUMERIC(6,2),
  trend_direction VARCHAR(8)   DEFAULT 'stable', -- up, down, stable
  recorded_at     TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_pm_city ON performance_metrics(city_id, device_type);

-- ============================================================================
-- 7. AUTHENTICATION & RBAC
-- Maps to: src/auth/mockAuthAPI.js
-- ============================================================================

CREATE TABLE users (
  id             UUID         DEFAULT uuid_generate_v4() PRIMARY KEY,
  username       VARCHAR(64)  UNIQUE NOT NULL,
  email          VARCHAR(256) UNIQUE NOT NULL,
  password_hash  TEXT         NOT NULL,  -- bcrypt via pgcrypto
  full_name      VARCHAR(128),
  role           user_role    NOT NULL DEFAULT 'city_manager',
  is_active      BOOLEAN      DEFAULT TRUE,
  last_login     TIMESTAMPTZ,
  created_at     TIMESTAMPTZ  DEFAULT NOW(),
  updated_at     TIMESTAMPTZ  DEFAULT NOW()
);

-- Granular permissions per user
CREATE TABLE user_permissions (
  id          UUID         DEFAULT uuid_generate_v4() PRIMARY KEY,
  user_id     UUID         NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  city_id     city_code    REFERENCES cities(id),
  device_type device_type,
  toc_code    toc_company  REFERENCES toc_companies(code),
  granted_at  TIMESTAMPTZ  DEFAULT NOW(),
  UNIQUE(user_id, city_id, device_type, toc_code)
);

CREATE INDEX idx_up_user ON user_permissions(user_id);

-- Audit trail
CREATE TABLE audit_log (
  id          BIGSERIAL    PRIMARY KEY,
  user_id     UUID         REFERENCES users(id),
  action      VARCHAR(64)  NOT NULL,  -- LOGIN, LOGOUT, CREATE_USER, UPDATE_USER, etc.
  target_type VARCHAR(64),            -- user, permission, setting
  target_id   VARCHAR(128),
  details     JSONB,
  ip_address  INET,
  user_agent  TEXT,
  created_at  TIMESTAMPTZ  DEFAULT NOW()
);

CREATE INDEX idx_al_user    ON audit_log(user_id, created_at DESC);
CREATE INDEX idx_al_action  ON audit_log(action, created_at DESC);

-- Refresh tokens for JWT rotation
CREATE TABLE refresh_tokens (
  id          UUID         DEFAULT uuid_generate_v4() PRIMARY KEY,
  user_id     UUID         NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  token_hash  TEXT         NOT NULL UNIQUE,
  expires_at  TIMESTAMPTZ  NOT NULL,
  revoked     BOOLEAN      DEFAULT FALSE,
  created_at  TIMESTAMPTZ  DEFAULT NOW()
);

CREATE INDEX idx_rt_user ON refresh_tokens(user_id);
CREATE INDEX idx_rt_exp  ON refresh_tokens(expires_at) WHERE revoked = FALSE;

-- ============================================================================
-- 8. ML MODEL REGISTRY
-- Tracks the 80 models (5 PS x 4 Cities x 4 Devices)
-- ============================================================================

CREATE TABLE ml_models (
  id              UUID         DEFAULT uuid_generate_v4() PRIMARY KEY,
  model_name      VARCHAR(128) NOT NULL,
  problem_stmt    VARCHAR(8)   NOT NULL, -- PS1, PS2, PS3, PS4, PS5
  city_id         city_code    NOT NULL REFERENCES cities(id),
  device_type     device_type  NOT NULL,
  version         VARCHAR(16)  NOT NULL DEFAULT '1.0.0',
  algorithm       VARCHAR(64),           -- XGBoost, LSTM, IsolationForest, etc.
  accuracy        NUMERIC(5,4),
  f1_score        NUMERIC(5,4),
  training_date   TIMESTAMPTZ,
  s3_artifact_uri TEXT,                  -- s3://bucket/models/...
  is_active       BOOLEAN      DEFAULT TRUE,
  deployed_env    deployment_env,
  created_at      TIMESTAMPTZ  DEFAULT NOW(),
  UNIQUE(problem_stmt, city_id, device_type, version)
);

CREATE INDEX idx_ml_active ON ml_models(is_active) WHERE is_active = TRUE;

-- ============================================================================
-- 9. SYSTEM CONFIGURATION
-- ============================================================================

CREATE TABLE system_config (
  key         VARCHAR(128) PRIMARY KEY,
  value       JSONB        NOT NULL,
  description TEXT,
  updated_by  UUID         REFERENCES users(id),
  updated_at  TIMESTAMPTZ  DEFAULT NOW()
);

-- Default config entries
INSERT INTO system_config (key, value, description) VALUES
  ('sla.default_targets', '{"uptime": 99.5, "mttr_hrs": 4, "mtbf_days": 30}', 'Default SLA targets for new cities'),
  ('anomaly.threshold', '{"z_score": 3.0, "isolation_forest_contamination": 0.05}', 'Anomaly detection thresholds'),
  ('auth.jwt_expiry_mins', '30', 'JWT access token expiry in minutes'),
  ('auth.refresh_expiry_days', '7', 'Refresh token expiry in days'),
  ('deployment.current_phase', '"1"', 'Current deployment phase (1-4)');

-- ============================================================================
-- 10. VIEWS FOR DASHBOARD API ENDPOINTS
-- ============================================================================

-- Aggregated prediction summary (PS1 overview cards)
CREATE OR REPLACE VIEW v_prediction_summary AS
SELECT
  fp.city_id,
  fp.device_type,
  fp.severity,
  COUNT(*)           AS prediction_count,
  AVG(fp.probability) AS avg_probability,
  AVG(fp.confidence)  AS avg_confidence
FROM failure_predictions fp
WHERE fp.predicted_at >= NOW() - INTERVAL '24 hours'
GROUP BY fp.city_id, fp.device_type, fp.severity;

-- Active anomaly counts (PS4 overview cards)
CREATE OR REPLACE VIEW v_active_anomalies AS
SELECT
  a.city_id,
  a.device_type,
  a.severity,
  COUNT(*) AS active_count
FROM anomalies a
WHERE a.status = 'active'
GROUP BY a.city_id, a.device_type, a.severity;

-- SLA compliance snapshot (PS5 overview)
CREATE OR REPLACE VIEW v_sla_compliance AS
SELECT
  sm.city_id,
  sm.device_type,
  COUNT(*) FILTER (WHERE NOT sm.is_breached) * 100.0 / NULLIF(COUNT(*), 0) AS compliance_pct,
  COUNT(*) FILTER (WHERE sm.is_breached) AS breach_count
FROM sla_metrics sm
WHERE sm.period_start >= NOW() - INTERVAL '30 days'
GROUP BY sm.city_id, sm.device_type;

-- ============================================================================
-- 11. ROW-LEVEL SECURITY (RLS) FOR RBAC
-- ============================================================================

ALTER TABLE failure_predictions   ENABLE ROW LEVEL SECURITY;
ALTER TABLE anomalies             ENABLE ROW LEVEL SECURITY;
ALTER TABLE sla_metrics           ENABLE ROW LEVEL SECURITY;
ALTER TABLE root_causes           ENABLE ROW LEVEL SECURITY;

-- Example RLS policy: users only see data for their permitted cities
-- (Applied via application-level SET session variable)
CREATE POLICY city_access_policy ON failure_predictions
  FOR SELECT
  USING (
    city_id = ANY(
      string_to_array(current_setting('app.allowed_cities', TRUE), ',')::city_code[]
    )
  );

-- Repeat similar policies for other tables as needed.
-- In practice, the API layer sets: SET LOCAL app.allowed_cities = 'CHI,BOS';

-- ============================================================================
-- 12. MIGRATION TRACKING
-- ============================================================================

CREATE TABLE schema_migrations (
  version     VARCHAR(14)  PRIMARY KEY,  -- YYYYMMDDHHMMSS
  name        VARCHAR(256) NOT NULL,
  applied_at  TIMESTAMPTZ  DEFAULT NOW()
);

INSERT INTO schema_migrations (version, name) VALUES
  ('20260502000001', 'initial_schema_creation');

-- ============================================================================
-- END OF SCHEMA
-- ============================================================================
