-- =====================================================================
-- 21_fleet_event_baseline.sql  --  the shared OOS / chargeable base statistic
-- CUBIC MARS Chicago / CTA-Ventra
--
-- One table, read by PS1, PS2, PS3 and PS5 alike, so the fleet's headline event
-- counts are stated once and cannot drift between tabs.
--
-- WHY BOTH NUMBERS, AND WHY OOS LEADS
--   A chargeable event is a CONTRACT classification applied AFTER the physical
--   event, through a chain of if/then conditions that may or may not be logical.
--   Chargeable events are therefore a strict SUBSET of OOS events. The physical
--   thing a maintenance model can act on is the OOS event; chargeability is a
--   commercial consequence of it. Every model in this programme targets
--   will_hardware_oos_3d for that reason.
--
--   So the band shows total OOS as the denominator and chargeable as the subset,
--   never the other way round, and never chargeable alone.
--
-- DDL ONLY. Applied by `action=migrate` from CloudShell. Counts arrive through
-- the load path, never from a seed INSERT here -- migrate() runs on every deploy
-- and an INSERT in this file would refill the table behind any purge.
-- =====================================================================

CREATE TABLE IF NOT EXISTS fleet_event_baseline (
  city_id            city_code    NOT NULL REFERENCES cities(id),
  scope              VARCHAR(40)  NOT NULL,   -- which source table the counts came from
  device_category    VARCHAR(12)  NOT NULL,   -- TVM | GATE | VALIDATOR | ALL
  period_start       DATE,
  period_end         DATE,
  total_oos_events   BIGINT,                  -- the denominator
  chargeable_events  BIGINT,                  -- the subset
  -- Derived, not supplied, so the two can never disagree on screen. NULLIF keeps
  -- a zero denominator out of the division rather than erroring the whole view.
  chargeable_pct     NUMERIC(7,4)
      GENERATED ALWAYS AS (
        ROUND(chargeable_events::numeric / NULLIF(total_oos_events, 0), 4)
      ) STORED,
  n_devices          INT,
  source_table       VARCHAR(120),            -- e.g. mars_dev.gold.device_ps3_incident
  source_run_id      VARCHAR(48),
  notes              TEXT,
  as_of_date         DATE         NOT NULL,
  PRIMARY KEY (city_id, scope, device_category, as_of_date)
);
CREATE INDEX IF NOT EXISTS idx_fleet_baseline_latest
  ON fleet_event_baseline (city_id, as_of_date DESC);

-- Latest snapshot per scope, with an ALL row synthesised when the source only
-- supplies per-category rows. UNION rather than ROLLUP so an explicitly loaded
-- ALL row always wins over a computed one.
CREATE OR REPLACE VIEW v_fleet_event_baseline AS
WITH latest AS (
  SELECT b.*, ROW_NUMBER() OVER (
           PARTITION BY city_id, scope, device_category ORDER BY as_of_date DESC) AS rn
  FROM fleet_event_baseline b
),
cur AS (SELECT * FROM latest WHERE rn = 1)
SELECT city_id, scope, device_category, period_start, period_end,
       total_oos_events, chargeable_events, chargeable_pct, n_devices,
       source_table, as_of_date
FROM cur
UNION ALL
SELECT city_id, scope, 'ALL', MIN(period_start), MAX(period_end),
       SUM(total_oos_events), SUM(chargeable_events),
       ROUND(SUM(chargeable_events)::numeric / NULLIF(SUM(total_oos_events), 0), 4),
       SUM(n_devices), MAX(source_table), MAX(as_of_date)
FROM cur
WHERE device_category <> 'ALL'
  AND NOT EXISTS (SELECT 1 FROM cur c2
                  WHERE c2.city_id = cur.city_id AND c2.scope = cur.scope
                    AND c2.device_category = 'ALL')
GROUP BY city_id, scope;
