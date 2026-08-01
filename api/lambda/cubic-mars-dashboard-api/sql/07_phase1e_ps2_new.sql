-- =====================================================================
-- Phase-1e : NEW PS2 analytics tables for the richer Cascading-Failure tab
--   ps2_cascade_paths          (Markov / top cascade sequences)
--   ps2_ignition_termination   (where cascades start vs end)
--   ps2_business_impact         (device impact ranking)
-- Idempotent. Runs after 01-06. Seeds ONLY locked-real anchors
-- (source_label='locked_2026-07-11'); the enhanced PS2 notebook then emits
-- sql/08_ps2_run_backfill.sql with the authoritative full run (a later
-- computed_date), and the API always returns the newest computed_date only.
-- =====================================================================

CREATE TABLE IF NOT EXISTS ps2_cascade_paths (
  city_id        city_code    NOT NULL REFERENCES cities(id),
  path_rank      SMALLINT     NOT NULL,
  cascade_path   VARCHAR(220) NOT NULL,
  path_len       SMALLINT,
  first_subsystem VARCHAR(30),
  last_subsystem  VARCHAR(30),
  occurrences    BIGINT,
  pct_of_chains  NUMERIC(7,3),
  computed_date  DATE         NOT NULL,
  PRIMARY KEY (city_id, path_rank, computed_date)
);

CREATE TABLE IF NOT EXISTS ps2_ignition_termination (
  city_id          city_code   NOT NULL REFERENCES cities(id),
  subsystem        VARCHAR(30) NOT NULL,
  rank             SMALLINT,
  ignition_days    BIGINT,
  termination_days BIGINT,
  ignition_pct     NUMERIC(7,3),
  termination_pct  NUMERIC(7,3),
  net_role         VARCHAR(12),
  computed_date    DATE        NOT NULL,
  PRIMARY KEY (city_id, subsystem, computed_date)
);

CREATE TABLE IF NOT EXISTS ps2_business_impact (
  city_id       city_code   NOT NULL REFERENCES cities(id),
  device_id     VARCHAR(20) NOT NULL,
  category      VARCHAR(12),
  total_impact  NUMERIC(14,1),
  cascade_days  INT,
  avg_impact    NUMERIC(10,3),
  max_impact    NUMERIC(12,1),
  impact_rank   SMALLINT,
  computed_date DATE        NOT NULL,
  PRIMARY KEY (city_id, device_id, computed_date)
);

-- ---------- seed: ps2_cascade_paths (from the locked real association rules) ----------
-- occurrences = round(real_support * 2,198,548 real total cascade days); 2-step relationships.
DELETE FROM ps2_cascade_paths WHERE city_id='CHI' AND computed_date=DATE '2026-07-11';
INSERT INTO ps2_cascade_paths
  (city_id, path_rank, cascade_path, path_len, first_subsystem, last_subsystem, occurrences, pct_of_chains, computed_date) VALUES
  ('CHI',1,'CHU->SYSTEM',            2,'CHU','SYSTEM',            483681,22.000,DATE '2026-07-11'),
  ('CHI',2,'CSC_READER->SYSTEM',     2,'CSC_READER','SYSTEM',     417724,19.000,DATE '2026-07-11'),
  ('CHI',3,'CHU+SCRST->CSC_READER+SYSTEM',3,'CHU','SYSTEM',       307797,14.000,DATE '2026-07-11'),
  ('CHI',4,'SCRST->COMMS',           2,'SCRST','COMMS',           241840,11.000,DATE '2026-07-11')
ON CONFLICT (city_id, path_rank, computed_date) DO UPDATE SET occurrences=EXCLUDED.occurrences;

-- ---------- seed: ps2_ignition_termination (from the locked real hub + rule direction) --------
-- freq is the real hub involvement; net_role derived from real rule antecedent/consequent side.
-- ignition/termination day counts are populated by the notebook run (08).
DELETE FROM ps2_ignition_termination WHERE city_id='CHI' AND computed_date=DATE '2026-07-11';
INSERT INTO ps2_ignition_termination
  (city_id, subsystem, rank, ignition_days, termination_days, ignition_pct, termination_pct, net_role, computed_date) VALUES
  ('CHI','SYSTEM',    1,NULL,NULL,NULL,NULL,'Terminator',DATE '2026-07-11'),
  ('CHI','COMMS',     2,NULL,NULL,NULL,NULL,'Terminator',DATE '2026-07-11'),
  ('CHI','CHU',       3,NULL,NULL,NULL,NULL,'Ignitor',   DATE '2026-07-11'),
  ('CHI','CSC_READER',4,NULL,NULL,NULL,NULL,'Ignitor',   DATE '2026-07-11'),
  ('CHI','SCRST',     5,NULL,NULL,NULL,NULL,'Ignitor',   DATE '2026-07-11'),
  ('CHI','BHU',       6,NULL,NULL,NULL,NULL,'Relay',     DATE '2026-07-11')
ON CONFLICT (city_id, subsystem, computed_date) DO UPDATE SET net_role=EXCLUDED.net_role;

-- ---------- seed: ps2_business_impact (from the locked real top-20 cascade devices) ----------
-- cascade_days + category + rank are the real burden ranking; total/avg/max impact
-- (chain_length x fault-type weight) are populated by the notebook run (08).
DELETE FROM ps2_business_impact WHERE city_id='CHI' AND computed_date=DATE '2026-07-11';
INSERT INTO ps2_business_impact
  (city_id, device_id, category, total_impact, cascade_days, avg_impact, max_impact, impact_rank, computed_date) VALUES
  ('CHI','TVM01703','TVM',      NULL,810,NULL,NULL, 1,DATE '2026-07-11'),
  ('CHI','TVM03901','TVM',      NULL,810,NULL,NULL, 2,DATE '2026-07-11'),
  ('CHI','TVM10801','TVM',      NULL,808,NULL,NULL, 3,DATE '2026-07-11'),
  ('CHI','TVM11402','TVM',      NULL,808,NULL,NULL, 4,DATE '2026-07-11'),
  ('CHI','TVM18101','TVM',      NULL,808,NULL,NULL, 5,DATE '2026-07-11'),
  ('CHI','TVM04501','TVM',      NULL,807,NULL,NULL, 6,DATE '2026-07-11'),
  ('CHI','TVM04901','TVM',      NULL,807,NULL,NULL, 7,DATE '2026-07-11'),
  ('CHI','TVM05401','TVM',      NULL,806,NULL,NULL, 8,DATE '2026-07-11'),
  ('CHI','TVM17001','TVM',      NULL,805,NULL,NULL, 9,DATE '2026-07-11'),
  ('CHI','TVM18103','TVM',      NULL,804,NULL,NULL,10,DATE '2026-07-11'),
  ('CHI','TVM00122','TVM',      NULL,803,NULL,NULL,11,DATE '2026-07-11'),
  ('CHI','TVM12101','TVM',      NULL,763,NULL,NULL,12,DATE '2026-07-11'),
  ('CHI','BMV02629','VALIDATOR',NULL,759,NULL,NULL,13,DATE '2026-07-11'),
  ('CHI','BMV01417','VALIDATOR',NULL,756,NULL,NULL,14,DATE '2026-07-11'),
  ('CHI','BMV04130','VALIDATOR',NULL,755,NULL,NULL,15,DATE '2026-07-11'),
  ('CHI','BMV04488','VALIDATOR',NULL,753,NULL,NULL,16,DATE '2026-07-11'),
  ('CHI','BMV03221','VALIDATOR',NULL,752,NULL,NULL,17,DATE '2026-07-11'),
  ('CHI','BMV03864','VALIDATOR',NULL,752,NULL,NULL,18,DATE '2026-07-11'),
  ('CHI','BMV04252','VALIDATOR',NULL,751,NULL,NULL,19,DATE '2026-07-11'),
  ('CHI','BMV05866','VALIDATOR',NULL,749,NULL,NULL,20,DATE '2026-07-11')
ON CONFLICT (city_id, device_id, computed_date) DO UPDATE SET cascade_days=EXCLUDED.cascade_days;

