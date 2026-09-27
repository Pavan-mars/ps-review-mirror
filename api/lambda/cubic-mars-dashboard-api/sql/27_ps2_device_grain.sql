-- (27-Sep-2026) ps2_cascade_velocity: statement removed -- table retired (sql/62, 63, 75).


-- Lead/lag timing between subsystem pairs, in minutes. 80 rows = the ordered
-- pairs actually observed. sub_a = sub_b rows are present and meaningful: they
-- are the recurrence interval of a subsystem against itself.
CREATE TABLE IF NOT EXISTS ps2_leadlag_timing (
  city_id city_code NOT NULL REFERENCES cities(id),
  sub_a VARCHAR(30) NOT NULL,
  sub_b VARCHAR(30) NOT NULL,
  n_events BIGINT,
  mean NUMERIC(12,3),
  median NUMERIC(12,3),
  p25 NUMERIC(12,3),
  p75 NUMERIC(12,3),
  computed_date DATE NOT NULL,
  PRIMARY KEY (city_id, sub_a, sub_b, computed_date)
);

-- Per-device cascade recurrence. 4,673 devices -- the same device count as
-- ps2_business_impact, so the two join 1:1 and together give a device a cascade
-- day count, a chronic flag and a business impact in one row.
--
-- `chronic` arrives from the export as the STRING 'True'/'False', not a boolean.
-- Declared BOOLEAN here so Postgres casts on insert and the column is usable in
-- a WHERE clause; a VARCHAR would make every filter a string comparison and
-- would silently treat 'false' and 'False' as different values.
CREATE TABLE IF NOT EXISTS ps2_recurrence (
  city_id city_code NOT NULL REFERENCES cities(id),
  device_id VARCHAR(40) NOT NULL,
  cascade_days INT,
  chronic BOOLEAN,
  computed_date DATE NOT NULL,
  PRIMARY KEY (city_id, device_id, computed_date)
);

CREATE INDEX IF NOT EXISTS ix_ps2_recurrence_chronic
  ON ps2_recurrence (city_id, chronic, cascade_days DESC);

-- ---------------------------------------------------------------------------
-- v_ps2_device_cascade -- the join the dashboard actually wants.
--
-- Replaces the ps2_top_devices route's source. ps2_top_devices is NOT part of
-- the S3 export and was never loaded by the PS2 pipeline; it holds one stale
-- seed row (BMV01005) from an early backfill, so /ps2/topdevices has been
-- serving a fleet of one. This view is built only from tables the 27-Jul load
-- actually populated.
--
-- impact_rank and max_impact are computed HERE rather than read from source:
-- the export supplies total_impact, cascade_days and avg_impact but leaves
-- max_impact and impact_rank null on every row, and a rank column of nulls is
-- worse than no rank column at all.
-- 27-Sep-2026: ps2_business_impact DDL moved here from the retired sql/07 -- v_ps2_device_cascade below reads it,
-- and a fresh database (UAT/Prod) would otherwise never create it.
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

CREATE OR REPLACE VIEW v_ps2_device_cascade AS
WITH latest AS (
  SELECT city_id, MAX(computed_date) AS d
  FROM ps2_business_impact GROUP BY city_id
)
SELECT
  b.city_id,
  b.computed_date,
  b.device_id,
  b.category,
  b.total_impact,
  b.avg_impact,
  b.cascade_days                                     AS impact_cascade_days,
  r.cascade_days                                     AS recurrence_cascade_days,
  r.chronic,
  RANK() OVER (PARTITION BY b.city_id, b.computed_date
               ORDER BY b.total_impact DESC NULLS LAST)  AS impact_rank,
  RANK() OVER (PARTITION BY b.city_id, b.computed_date, b.category
               ORDER BY b.total_impact DESC NULLS LAST)  AS impact_rank_in_category
FROM ps2_business_impact b
JOIN latest l ON l.city_id = b.city_id AND l.d = b.computed_date
LEFT JOIN ps2_recurrence r
       ON r.city_id = b.city_id
      AND r.device_id = b.device_id
      AND r.computed_date = b.computed_date;
