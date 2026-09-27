-- 75_ps2_retire.sql -- PS2 stale-element retirement.                    27-Sep-2026
--
-- 1. Hand-seeded rows. sql/02 (computed_date 2026-07-11) and sql/08 (2026-07-14) were
--    re-inserted by `migrate` on every API deploy. Both files are retired
--    (_retired/ps2_seed_backfills) and out of the migrate list. Their rows are removed
--    here ONLY from tables that hold a newer loader vintage -- a table whose newest
--    rows are the seed keeps them (nothing to fall back to).
-- 2. ps2_business_impact: written by the loader (alias now removed) and by the sql/08
--    seed, read by nothing -- no route, no view, no dashboard panel. Run the `depends`
--    action first; apply_sql reports a blocked drop as failed and moves on.
--
-- Idempotent.

DO $$
DECLARE t TEXT; d DATE; n BIGINT;
BEGIN
  FOREACH t IN ARRAY ARRAY['ps2_cascade_window_summary','ps2_facility_contagion_summary','ps2_hmm_regimes',
                           'ps2_subsystem_associations','ps2_subsystem_hub_edges','ps2_subsystem_hub_summary',
                           'ps2_cascade_paths','ps2_ignition_termination','ps2_top_devices','ps2_window_detail'] LOOP
    IF to_regclass(t) IS NULL THEN CONTINUE; END IF;
    FOREACH d IN ARRAY ARRAY[DATE '2026-07-11', DATE '2026-07-14'] LOOP
      EXECUTE format('DELETE FROM %I WHERE city_id = %L AND computed_date = %L
                        AND EXISTS (SELECT 1 FROM %I WHERE city_id = %L AND computed_date > %L)',
                     t, 'CHI', d, t, 'CHI', d);
      GET DIAGNOSTICS n = ROW_COUNT;
      IF n > 0 THEN RAISE NOTICE 'sql/75: % seed rows removed from % (computed_date %)', n, t, d; END IF;
    END LOOP;
  END LOOP;
END $$;

DROP TABLE IF EXISTS ps2_business_impact;
