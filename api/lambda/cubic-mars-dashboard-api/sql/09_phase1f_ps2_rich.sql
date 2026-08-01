-- =====================================================================
-- Phase-1f : RICH PS2 tables for correlation heatmaps, Markov/network,
-- conditional probability, error-code intelligence, and device drill-down.
-- DDL only (idempotent). Data is populated by the PS2 notebook via
-- sql/10_ps2_run_backfill_2.sql (rds_export/ps2_run_backfill_2.sql).
-- Every read route returns the newest computed_date only.
-- =====================================================================

CREATE TABLE IF NOT EXISTS ps2_device_catalog (
  city_id city_code NOT NULL REFERENCES cities(id),
  device_id VARCHAR(20) NOT NULL, device_name VARCHAR(80), serial VARCHAR(60),
  category VARCHAR(12), control_group VARCHAR(60), facility VARCHAR(100), operator VARCHAR(80),
  cascade_days INT, avg_chain_len NUMERIC(7,2), max_chain_len INT,
  dom_subsystem VARCHAR(30), dom_error_code VARCHAR(20), worst_cascade_path VARCHAR(220),
  worst_window VARCHAR(12), computed_date DATE NOT NULL,
  PRIMARY KEY (city_id, device_id, computed_date)
);
-- SAFETY (v2): do NOT drop ps2_device_cascades on re-migrate (data comes from the one-off
-- backfill, not this bundle). Surrogate id already applied in prod; CREATE IF NOT EXISTS preserves it.
-- DROP TABLE IF EXISTS ps2_device_cascades;
CREATE TABLE ps2_device_cascades (
  id BIGSERIAL PRIMARY KEY,
  city_id city_code NOT NULL REFERENCES cities(id),
  device_id VARCHAR(20) NOT NULL, transit_day DATE NOT NULL,
  subsystem_chain VARCHAR(220), event_code_chain VARCHAR(220), severity_chain VARCHAR(140),
  chain_length INT, chain_span_min NUMERIC(10,2),
  first_subsystem VARCHAR(30), last_subsystem VARCHAR(30), computed_date DATE NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_ps2_devcascades ON ps2_device_cascades (city_id, device_id, computed_date);
CREATE TABLE IF NOT EXISTS ps2_error_codes (
  city_id city_code NOT NULL REFERENCES cities(id),
  error_code VARCHAR(20) NOT NULL, occurrences BIGINT, top_subsystem VARCHAR(30),
  pct NUMERIC(7,3), computed_date DATE NOT NULL,
  PRIMARY KEY (city_id, error_code, computed_date)
);
CREATE TABLE IF NOT EXISTS ps2_error_code_transitions (
  city_id city_code NOT NULL REFERENCES cities(id),
  from_code VARCHAR(20) NOT NULL, to_code VARCHAR(20) NOT NULL, occurrences BIGINT,
  computed_date DATE NOT NULL,
  PRIMARY KEY (city_id, from_code, to_code, computed_date)
);
CREATE TABLE IF NOT EXISTS ps2_phi_matrix (
  city_id city_code NOT NULL REFERENCES cities(id),
  sub_a VARCHAR(30) NOT NULL, sub_b VARCHAR(30) NOT NULL, phi NUMERIC(12,4),
  computed_date DATE NOT NULL,
  PRIMARY KEY (city_id, sub_a, sub_b, computed_date)
);
CREATE TABLE IF NOT EXISTS ps2_markov_transitions (
  city_id city_code NOT NULL REFERENCES cities(id),
  from_sub VARCHAR(30) NOT NULL, to_sub VARCHAR(30) NOT NULL, prob NUMERIC(7,4),
  computed_date DATE NOT NULL,
  PRIMARY KEY (city_id, from_sub, to_sub, computed_date)
);
CREATE TABLE IF NOT EXISTS ps2_network_centrality (
  city_id city_code NOT NULL REFERENCES cities(id),
  node_id VARCHAR(30) NOT NULL, betweenness NUMERIC(7,4), pagerank NUMERIC(7,4),
  in_degree INT, out_degree INT, role VARCHAR(16), computed_date DATE NOT NULL,
  PRIMARY KEY (city_id, node_id, computed_date)
);
CREATE TABLE IF NOT EXISTS ps2_conditional_prob (
  city_id city_code NOT NULL REFERENCES cities(id),
  sub_a VARCHAR(30) NOT NULL, sub_b VARCHAR(30) NOT NULL, window_bucket VARCHAR(12) NOT NULL,
  p_b_given_a NUMERIC(7,4), computed_date DATE NOT NULL,
  PRIMARY KEY (city_id, sub_a, sub_b, window_bucket, computed_date)
);

