-- =====================================================================
-- CUBIC MARS Chicago — Phase-1 PS2/PS5 summary tables + one-time backfill
-- Date: 2026-07-12   Runs AFTER docs/schema.sql (base + v3 + v4).  Idempotent.
--
-- WHY a "v5" addendum: the v3/v4 tables ps2_cascade_chains_daily and
-- ps5_reliability_estimates are GRANULAR / per-device — built for the FUTURE
-- daily feed (task #72). Today's locked Level_1 rerun is AGGREGATE / per-category.
-- We cannot fabricate 2.19M granular chain rows from a summary, so Phase-1 adds
-- small summary/status tables that hold EXACTLY what the dashboard renders today
-- (parity with src/data/mockData.js getPS2*/getPS5* functions). The granular
-- tables stay for when the daily Databricks job starts writing real per-event rows.
-- =====================================================================


-- ---------- v5 summary / status tables ----------
CREATE TABLE IF NOT EXISTS ps2_cascade_window_summary (
  city_id                 city_code   NOT NULL REFERENCES cities(id),
  window_bucket           VARCHAR(10) NOT NULL,
  cascade_days            BIGINT      NOT NULL,
  pct                     NUMERIC(5,2) NOT NULL,
  total_cascade_days      BIGINT      NOT NULL,
  slow_fast_fault_mult    NUMERIC(6,2),
  slow_fast_duration_mult INT,
  computed_date           DATE        NOT NULL,
  PRIMARY KEY (city_id, window_bucket, computed_date)
);

CREATE TABLE IF NOT EXISTS ps2_subsystem_hub_summary (
  city_id       city_code   NOT NULL REFERENCES cities(id),
  node_id       VARCHAR(30) NOT NULL,
  freq          SMALLINT,
  is_hub        BOOLEAN     DEFAULT FALSE,
  computed_date DATE        NOT NULL,
  PRIMARY KEY (city_id, node_id, computed_date)
);

CREATE TABLE IF NOT EXISTS ps2_subsystem_hub_edges (
  city_id       city_code   NOT NULL REFERENCES cities(id),
  source_sub    VARCHAR(30) NOT NULL,
  target_sub    VARCHAR(30) NOT NULL,
  phi           NUMERIC(8,4),
  computed_date DATE        NOT NULL,
  PRIMARY KEY (city_id, source_sub, target_sub, computed_date)
);

CREATE TABLE IF NOT EXISTS ps2_facility_contagion_summary (
  city_id                     city_code NOT NULL REFERENCES cities(id),
  total_facility_cascade_days BIGINT,
  multi_device_contagion_pct  NUMERIC(5,2),
  trend_start_pct             NUMERIC(5,2),
  trend_end_pct               NUMERIC(5,2),
  hotspot_facility_id         INT,
  hotspot_facility_name       VARCHAR(100),
  hotspot_min_devices         SMALLINT,
  hotspot_max_devices         SMALLINT,
  computed_date               DATE NOT NULL,
  PRIMARY KEY (city_id, computed_date)
);

-- ps5_reliability_status retired 26-Sep-2026 (shadow PS5 stack); dropped by sql/68_ps5_shadow_drop.sql.

-- ---------- backfill: PS2 cascade window distribution (2,198,548 total) ----------
INSERT INTO ps2_cascade_window_summary
  (city_id, window_bucket, cascade_days, pct, total_cascade_days, slow_fast_fault_mult, slow_fast_duration_mult, computed_date) VALUES
  ('CHI','0-5min',   377865, 17.2, 2198548, 9.4, 1881, DATE '2026-07-11'),
  ('CHI','5-15min',  136893,  6.2, 2198548, 9.4, 1881, DATE '2026-07-11'),
  ('CHI','15-30min',  97218,  4.4, 2198548, 9.4, 1881, DATE '2026-07-11'),
  ('CHI','30-60min',  47479,  2.2, 2198548, 9.4, 1881, DATE '2026-07-11'),
  ('CHI','60min+',  1539093, 70.0, 2198548, 9.4, 1881, DATE '2026-07-11')
ON CONFLICT (city_id, window_bucket, computed_date) DO UPDATE
  SET cascade_days = EXCLUDED.cascade_days, pct = EXCLUDED.pct;

-- ---------- backfill: PS2 subsystem hub (SYSTEM<->COMMS) ----------
INSERT INTO ps2_subsystem_hub_summary (city_id, node_id, freq, is_hub, computed_date) VALUES
  ('CHI','SYSTEM',100,TRUE, DATE '2026-07-11'),
  ('CHI','COMMS', 88, TRUE, DATE '2026-07-11'),
  ('CHI','CHU',   62, FALSE,DATE '2026-07-11'),
  ('CHI','CSC_READER',55,FALSE,DATE '2026-07-11'),
  ('CHI','SCRST', 41, FALSE,DATE '2026-07-11'),
  ('CHI','BHU',   34, FALSE,DATE '2026-07-11')
ON CONFLICT (city_id, node_id, computed_date) DO UPDATE SET freq = EXCLUDED.freq;

INSERT INTO ps2_subsystem_hub_edges (city_id, source_sub, target_sub, phi, computed_date) VALUES
  ('CHI','CHU','SYSTEM',39.5, DATE '2026-07-11'),
  ('CHI','CSC_READER','SYSTEM',24.7, DATE '2026-07-11')
ON CONFLICT (city_id, source_sub, target_sub, computed_date) DO UPDATE SET phi = EXCLUDED.phi;

-- ---------- backfill: PS2 facility contagion (Facility 45 North Park) ----------
INSERT INTO ps2_facility_contagion_summary
  (city_id, total_facility_cascade_days, multi_device_contagion_pct, trend_start_pct, trend_end_pct,
   hotspot_facility_id, hotspot_facility_name, hotspot_min_devices, hotspot_max_devices, computed_date) VALUES
  ('CHI', 164515, 84.9, 75.5, 93.5, 45, 'North Park', 268, 275, DATE '2026-07-11')
ON CONFLICT (city_id, computed_date) DO UPDATE
  SET multi_device_contagion_pct = EXCLUDED.multi_device_contagion_pct;

-- ---------- backfill: PS2 association rules (lift 9.678 vs conviction 99.0) ----------
DELETE FROM ps2_subsystem_associations WHERE city_id='CHI' AND computed_date=DATE '2026-07-11';
INSERT INTO ps2_subsystem_associations
  (city_id, antecedent_subsystem, consequent_subsystem, support, confidence, lift, conviction, computed_date) VALUES
  ('CHI','CHU + SCRST','CSC_READER + SYSTEM',0.14,0.81,9.678,99.0, DATE '2026-07-11'),
  ('CHI','CHU','SYSTEM',0.22,0.76,6.12,41.3, DATE '2026-07-11'),
  ('CHI','CSC_READER','SYSTEM',0.19,0.69,4.87,28.9, DATE '2026-07-11'),
  ('CHI','SCRST','COMMS',0.11,0.58,3.34,15.2, DATE '2026-07-11');

-- ---------- backfill: PS2 HMM regimes ----------
INSERT INTO ps2_hmm_regimes (city_id, regime, pct, dwell_days_min, dwell_days_max, computed_date) VALUES
  ('CHI','Critical', 2.6, 1.5, 2.4, DATE '2026-07-11'),
  ('CHI','Minor',   21.0, 2.1, 4.2, DATE '2026-07-11'),
  ('CHI','Moderate',76.4, 3.8, 6.3, DATE '2026-07-11')
ON CONFLICT (city_id, regime, computed_date) DO UPDATE SET pct = EXCLUDED.pct;

-- NOTE (execution-surfaced, do NOT auto-apply the v4 view until fixed):
-- docs/schema.sql section 14 CREATE VIEW v_executive_summary references
-- ml_models.primary_metric_value and ml_models.is_champion, plus
-- ps1_failure_predictions.will_fail_3d_flag/prediction_date — none of which exist
-- in the base ml_models table (it has accuracy, f1_score, is_active). Applying
-- schema.sql as-is will ERROR on this view. Fix = add columns is_champion BOOLEAN
-- + primary_metric_value NUMERIC to ml_models (v5 DDL), then create the view.
