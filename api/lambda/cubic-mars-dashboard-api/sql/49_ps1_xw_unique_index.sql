-- =====================================================================
-- 49_ps1_xw_unique_index.sql   10-Aug-2026
--
-- Enforce the ps1_cross_wired_daily grain that has, until now, only been
-- MEASURED. sql/34 built v_ps1_xw_grain for exactly this purpose and said so:
--
--   "v_ps1_xw_grain at the bottom to MEASURE the grain from the loaded rows.
--    If it comes back empty, the natural key holds and a unique index can be
--    added in a later file with evidence behind it."
--
-- THE EVIDENCE. Run 10-Aug-2026 via the loader's own verify action against all
-- 786,525 loaded rows:
--
--   grain_dupes -> dup_keys = 0
--
-- Zero duplicate groups on (city_id, device_type, device_key,
-- component_serial_nbr, transit_day). This file is that later file.
--
-- WHY "NULLS NOT DISTINCT" IS THE WHOLE POINT
-- --------------------------------------------
-- component_serial_nbr is NULL for validators -- sql/34 line 84 says so, and
-- the loader header calls it "what killed" an earlier attempt at a natural key.
--
-- A plain UNIQUE index treats NULLs as DISTINCT, so (A, NULL, day) and
-- (A, NULL, day) would BOTH be accepted. The index would enforce nothing for
-- VALIDATOR -- 494,932 of 786,525 rows, 63% of the table -- while looking
-- correct in every review and passing every grain check, because the rows it
-- fails to constrain are exactly the rows no check would flag.
--
-- NULLS NOT DISTINCT (PostgreSQL 15+) makes NULL compare equal to NULL for
-- uniqueness. Aurora here is PostgreSQL 16.4, measured 10-Aug, so it is
-- available. The alternative -- COALESCE(component_serial_nbr,'') in an
-- expression index -- works on any version but encodes a sentinel that then has
-- to be remembered by everyone who reads the schema. Prefer the version that
-- says what it means.
--
-- WHAT THIS CHANGES OPERATIONALLY
-- --------------------------------
-- The loader uses COPY (copy_rows in the deployed handler). With this index in
-- place a duplicate ABORTS the copy for that fleet rather than landing silently.
-- That is the intent: the fleet-scoped SAVEPOINT means one bad fleet rolls back
-- alone and the other two still load, and the failure is visible in the run
-- report instead of arriving as a quietly doubled aggregate weeks later.
--
-- The pre-flight guard below refuses to create the index if the grain is
-- violated at apply time, so this fails with a readable message naming the
-- count rather than a bare constraint violation from CREATE INDEX.
--
-- LOCKING. A non-concurrent CREATE UNIQUE INDEX takes a brief exclusive lock.
-- At 786,525 rows that is seconds. Do NOT apply while the 06:40 loader is
-- mid-COPY. CONCURRENTLY is deliberately not used: it cannot run inside a
-- transaction and leaves an INVALID index behind on failure, which is a worse
-- state to inherit than a retry.
--
-- IDEMPOTENT. IF NOT EXISTS, so re-applying is a no-op.
-- REVERSIBLE. DROP INDEX IF EXISTS ux_ps1_cross_wired_daily_grain;
-- =====================================================================

-- Pre-flight: refuse loudly if the grain does not hold at apply time.
DO $$
DECLARE dups bigint;
BEGIN
  SELECT COUNT(*) INTO dups FROM (
    SELECT 1
    FROM ps1_cross_wired_daily
    GROUP BY city_id, device_type, device_key, component_serial_nbr, transit_day
    HAVING COUNT(*) > 1
  ) d;
  IF dups > 0 THEN
    RAISE EXCEPTION
      'REFUSING to create ux_ps1_cross_wired_daily_grain: % duplicate grain group(s) present. Investigate with SELECT * FROM v_ps1_xw_grain before retrying.', dups;
  END IF;
END $$;

CREATE UNIQUE INDEX IF NOT EXISTS ux_ps1_cross_wired_daily_grain
  ON ps1_cross_wired_daily
  (city_id, device_type, device_key, component_serial_nbr, transit_day)
  NULLS NOT DISTINCT;

COMMENT ON INDEX ux_ps1_cross_wired_daily_grain IS
  'Grain of the PS1 cross-wired feed. NULLS NOT DISTINCT because component_serial_nbr is NULL for validators (63% of rows); a plain UNIQUE would constrain nothing for them. Evidence: v_ps1_xw_grain returned 0 duplicate groups over 786,525 rows on 10-Aug-2026.';
