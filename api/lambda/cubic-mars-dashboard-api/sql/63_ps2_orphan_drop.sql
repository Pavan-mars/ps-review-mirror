-- =====================================================================
-- PS2 orphan retirement -- DROP. 23-Sep-2026. Apply AFTER sql/62.
--
-- THE RULE PK SET: a PS2 table earns its place in Aurora only if one of
-- the two current PS2 notebooks writes it. Everything else gets checked,
-- verified and dropped. These 13 are what survived that check.
--
-- WHERE THEY CAME FROM. All but one are hand-seeded July demo tables --
-- sql/01, 02, 05, 07 and 09 both create them and INSERT literal rows, from
-- the phase-1d/1e/1f dashboard build. No pipeline has ever written them:
-- they are not among the 53 prefixes under chicago/ps2_outputs, so the
-- loader has never had a source for them, let alone a stale one. Their
-- content is the July numbers, frozen, and the routes that served them
-- went with the legacy dashboard earlier today.
--
-- HOW EACH WAS CLEARED, and the specific way this check has gone wrong
-- before:
--   * Producers: both notebooks parsed with a bracket-depth argument
--     reader, not a regex -- write_output's first argument can itself
--     contain commas, which a regex splits in the wrong place and then
--     reports the table as unwritten.
--   * Readers: handler.py with Python comments stripped, every route body
--     attributed to its own route, plus the code OUTSIDE any route block --
--     which is where _device_360 lives, and _device_360 is the reason two
--     tables are NOT on this list.
--   * Views: each CREATE VIEW parsed as a single statement terminated by
--     its own semicolon. A first pass that chunked between one CREATE VIEW
--     and the next reported v_sla_compliance as reading
--     ps2_cascade_chains_daily and ps2_facility_contagion_daily. It does
--     not -- it reads sla_metrics, and the intervening CREATE TABLEs had
--     been swept into the view's body. Both tables are on this list on the
--     corrected reading, not the first one.
--   * Front end: every API path in dashboard/src, confirmed to contain no
--     template-literal or concatenated route, so the list is complete
--     rather than merely long.
--
-- TWO THAT LOOKED IDENTICAL AND ARE NOT DROPPED. ps2_device_catalog and
-- ps2_device_cascades have no producer either, and they are read on a live
-- path: _device_360 serves /ps1/device-360, which V4api.js calls, and
-- v_ps2_device_latest feeds v_device_360 behind /device/360/risk.
-- They stay, frozen at their July vintage. That is a separate problem --
-- a live panel reading a table nothing refreshes -- and deleting them
-- would not fix it, it would just change the symptom from stale to empty.
--
-- ps2_v2_run_quality is here for completeness and will almost certainly
-- report as already absent: it is the only name on this list that no
-- migration creates, it is missing from the 16-Aug live inventory, and the
-- lineage doc records the loader reporting it as no_target. It exists as an
-- S3 prefix written by an older generation of the patterns notebook; the
-- current one writes ps2_v25_run_quality. The DROP is harmless either way,
-- and the prefix is deleted with the others.
--
-- ROWS DESTROYED: 117 at the 16-Aug inventory, across 12 tables. Two of
-- them -- ps2_cascade_chains_daily and ps2_facility_contagion_daily -- have
-- been empty since they were created in sql/01 and have never held a row.
--
-- No CASCADE. A DROP that fails because something unexpected depends on
-- the table is the outcome we want.
--
-- Ship through apply_sql, dry-run first. This file ends on a statement.
-- =====================================================================

-- ---------------------------------------------------------------------
-- 1. sql/01 core-schema tables the medallion never filled. Both empty
--    since creation; ps2_hmm_regimes and ps2_subsystem_associations came
--    from the same block and are NOT here -- the loader writes those.
-- ---------------------------------------------------------------------
DROP TABLE IF EXISTS ps2_cascade_chains_daily;
DROP TABLE IF EXISTS ps2_facility_contagion_daily;

-- ---------------------------------------------------------------------
-- 2. sql/02 phase-1 backfill. Seeded from the July analysis to give the
--    first dashboard something to render; /ps2/windows, /ps2/hub and
--    /ps2/facility all went with the legacy page.
-- ---------------------------------------------------------------------
DROP TABLE IF EXISTS ps2_cascade_window_summary;
DROP TABLE IF EXISTS ps2_facility_contagion_summary;
DROP TABLE IF EXISTS ps2_subsystem_hub_edges;
DROP TABLE IF EXISTS ps2_subsystem_hub_summary;

-- ---------------------------------------------------------------------
-- 3. sql/05 phase-1d. ps2_top_devices was already dead in code: the
--    27-Jul comment at handler.py _device_360 records it being replaced by
--    v_ps2_device_cascade precisely because "the S3 pipeline never loads
--    it". This drops the table that comment is about.
-- ---------------------------------------------------------------------
DROP TABLE IF EXISTS ps2_top_devices;
DROP TABLE IF EXISTS ps2_window_detail;

-- ---------------------------------------------------------------------
-- 4. sql/07 phase-1e. ps2_ignition_termination is NOT
--    ps2_ignition_termination_subsystem: different table, different grain,
--    and the subsystem one is live behind /ps2/serial/ignition. The names
--    differ by one suffix and the wrong one has been dropped before in
--    other projects for exactly that reason.
-- ---------------------------------------------------------------------
DROP TABLE IF EXISTS ps2_cascade_paths;
DROP TABLE IF EXISTS ps2_ignition_termination;

-- ---------------------------------------------------------------------
-- 5. sql/09 phase-1f. The error-code pair behind /ps2/errorcodes.
--    ps2_device_catalog and ps2_device_cascades come from this same file
--    and are deliberately kept -- see the header.
-- ---------------------------------------------------------------------
DROP TABLE IF EXISTS ps2_error_code_transitions;
DROP TABLE IF EXISTS ps2_error_codes;

-- ---------------------------------------------------------------------
-- 6. No migration creates this one. Expect "already absent".
-- ---------------------------------------------------------------------
DROP TABLE IF EXISTS ps2_v2_run_quality;
