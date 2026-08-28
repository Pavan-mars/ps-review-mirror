-- 28-Aug-2026. Indexes for the v_device_360 family.
--
-- THE MEASUREMENT. /device/360/risk took 12.1s for 20 rows and 15.3s for 300 --
-- the cost does not move with the row count, so it is not the LIMIT, it is the
-- view. /device/360 for ONE device answers in 0.74s, because the device_id
-- predicate pushes down into each DISTINCT ON. Without that predicate every
-- per-PS view sorts its whole source table.
--
-- ps1_cross_wired_daily is 786,525 rows and v_ps1_device_latest orders it by
-- (city_id, device_id, transit_day DESC, asof_date DESC). That full sort is
-- the bill. The other sources are 200 to 9,000 rows and barely register, but
-- they are indexed here too because the index is small and the next reload
-- could grow any of them.
--
-- These indexes match the view's ORDER BY exactly, so DISTINCT ON becomes an
-- ordered index scan instead of a sort.
--
-- Apply via {"action":"apply_sql","file":"58_device_360_perf_indexes.sql"}
-- (dry_run first). NOT in the migrate() tuple -- frozen at sql/48 by design.
--
-- LOCKING. Plain CREATE INDEX takes a SHARE lock: it blocks WRITES to that
-- table while it builds, and does not block reads. The dashboard keeps
-- serving. Run it OUTSIDE the 05:45-08:23 UTC loader window so no loader is
-- writing. CONCURRENTLY is deliberately not used -- it cannot run inside a
-- transaction block and apply_sql's error handling is per statement, so a
-- failed concurrent build would leave an INVALID index behind to clean up.

CREATE INDEX IF NOT EXISTS ix_ps1_xw_latest_per_device
  ON ps1_cross_wired_daily (city_id, device_id, transit_day DESC, asof_date DESC);

CREATE INDEX IF NOT EXISTS ix_ps4_weekly_latest_per_device
  ON ps4_weekly_device_summary (city_id, device_id, week_start DESC);

CREATE INDEX IF NOT EXISTS ix_ps4_cluster_latest_per_device
  ON ps4_cluster_assignments (city_id, device_id, asof_date DESC);

CREATE INDEX IF NOT EXISTS ix_ps5_rul_latest_per_device
  ON ps5_device_rul (city_id, device_id, feature_asof_date DESC);

CREATE INDEX IF NOT EXISTS ix_ps3_v2_rel_latest_per_device
  ON ps3_v2_device_reliability (city_id, device_id, loaded_at DESC);

CREATE INDEX IF NOT EXISTS ix_ps3_v2_queue_latest_per_device
  ON ps3_v2_severity_action_queue (city_id, device_id, loaded_at DESC);

CREATE INDEX IF NOT EXISTS ix_ps2_catalog_latest_per_device
  ON ps2_device_catalog (city_id, device_id, computed_date DESC);

ANALYZE ps1_cross_wired_daily;
