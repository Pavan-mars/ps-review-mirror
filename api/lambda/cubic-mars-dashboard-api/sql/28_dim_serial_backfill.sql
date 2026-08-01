-- =====================================================================
-- 28_dim_serial_backfill.sql   27-Jul-2026
--
-- WHY
-- ---
-- /ps3/fleet-inventory and /ps3/fleet-devices both return 0 rows, which empties
-- the PS3 "Coverage & Fleet" sub-tab. The chain is:
--
--   /ps3/fleet-inventory -> v_component_inventory -> v_device_serial
--                                                 -> dim_device_serial  (EMPTY)
--
-- dim_device_serial is written only by cubic-mars-dim-loader, and that Lambda
-- reports:
--
--   no dated folders under
--   s3://cubic-mars-pm-s3-datalake-dev-gold-170202974600/chicago/dim/device_serial/
--
-- So the loader is not broken -- its SOURCE has never been produced. No notebook
-- writes that prefix yet.
--
-- WHAT THIS DOES INSTEAD
-- ----------------------
-- Everything dim_device_serial needs is already in Aurora, in two tables that
-- sql/load/dim_device_station_20260726.sql populated:
--
--   dim_device_component   11,718 rows   device_id, component_serial_nbr,
--                                        component_description, component_age_days
--   dim_device_station     18,616 rows   device_id -> mars_device_category
--
-- The component table is the serial grain; the station table supplies the one
-- column it lacks. Joining them reconstructs the dimension without a Databricks
-- run and without inventing a single value -- every field below is copied or
-- joined, none is derived from a rule of thumb.
--
-- WHY THIS IS NOT A PERMANENT ANSWER
-- ----------------------------------
-- It is a backfill, and it says so in source_table. The real fix is the notebook
-- writing chicago/dim/device_serial/asof=<date>/ so the daily loader has
-- something to load; this only removes the dependency on that work landing
-- before the tab is usable.
--
-- The guard below is what makes the two coexist safely: the INSERT fires ONLY
-- when the city has no dimension rows from any other source. The moment the S3
-- feed produces real rows, re-running this file becomes a no-op rather than
-- doubling every component in v_component_inventory.
--
-- Idempotent: the DELETE removes this file's own previous output first, so
-- migrate() may run it on every deploy.
-- =====================================================================

DELETE FROM dim_device_serial
 WHERE city_id = 'CHI' AND source_table = 'aurora_backfill';

INSERT INTO dim_device_serial
  (city_id, device_id, serial_id, mars_device_category,
   component_description, component_age_days, source_table, as_of_date)
SELECT
  c.city_id,
  c.device_id,
  c.component_serial_nbr,
  s.mars_device_category,
  c.component_description,
  -- dim_device_component stores age as NUMERIC(10,2); dim_device_serial is INT.
  -- Rounded rather than truncated: a component 4.9 days old is 5, not 4.
  -- Negative ages are preserved, NOT clamped -- age_is_negative is a GENERATED
  -- column that flags them, and v_component_inventory already excludes them from
  -- age averages while counting them separately as a data-quality signal.
  ROUND(c.component_age_days)::INT,
  'aurora_backfill',
  c.as_of_date
FROM dim_device_component c
LEFT JOIN dim_device_station s
       ON s.city_id = c.city_id AND s.device_id = c.device_id
-- The guard. Evaluated against the post-DELETE state, so it means "no rows from
-- a source other than this backfill". A device whose category cannot be resolved
-- is still inserted with a NULL category: dropping it would silently shrink the
-- fleet count, and an unknown category is a fact worth showing.
WHERE c.city_id = 'CHI'
  AND NOT EXISTS (SELECT 1 FROM dim_device_serial x WHERE x.city_id = c.city_id)
ON CONFLICT (city_id, device_id, serial_id) DO NOTHING;
