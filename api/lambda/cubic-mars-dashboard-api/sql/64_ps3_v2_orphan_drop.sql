-- =====================================================================
-- PS3 v2 orphan retirement -- DROP. 23-Sep-2026. Apply AFTER sql/63.
--
-- THE SAME RULE, APPLIED TO PS3. A ps3_v2_* table earns its place in
-- Aurora only if something still writes it or something live still reads
-- it. Eleven of the eighteen fail both tests. Seven pass the second one
-- and are deliberately kept -- the header says which, and why, because
-- that list is the whole point of this file.
--
-- WHERE THEY CAME FROM. sql/39 and sql/40 created eighteen tables to
-- serve ONE hardened-remediation run, ps3_20260729T074311Z, landed by
-- cubic-mars-ps3-v2-loader from
--     chicago/ps3_hardened_remediation/runs/<run_id>/tables/
-- Unlike the PS2 orphans these were never hand-seeded: no migration
-- INSERTs a literal row into any ps3_v2_* table, so every row in them
-- came off that one parquet drop on 29-Jul and nothing has touched them
-- since.
--
-- THERE IS NO PRODUCER, AND THAT IS THE FINDING. The string
-- ps3_hardened_remediation exists in exactly four places in this repo:
-- the sql/39 header comment, this loader's own handler.py and deploy.sh,
-- and the docs that describe the loader. No notebook, no .py, no .sh, no
-- databricks.yml job and no Step Function writes that prefix. The
-- seventeen parquet basenames in the loader spec.json were searched
-- individually as well; none is emitted anywhere outside the loader.
--
-- THE NAME TRAP THAT ALMOST SETTLED THIS THE WRONG WAY. There IS a
-- notebook called PS3_v2_OOS_Serial_SageMaker.ipynb, with an engine
-- ps3_oos_engine_v2.py, and it is NOT the producer. It writes
--     S3_OUT_PREFIX    = chicago/ps3_v2      (not ps3_hardened_remediation)
--     RDS_TABLE_PREFIX = ps3v2_              (not ps3_v2_)
-- Different prefix, and a table family whose name differs by a single
-- underscore. Nothing named ps3v2_ exists in Aurora. Reading "the v2
-- notebook" as "producer of the v2 tables" is the one mistake that would
-- have kept all eighteen alive for no reason.
--
-- HOW EACH WAS CLEARED, and where this check has gone wrong before:
--   * Readers: handler.py parsed as an AST, so Python comments are
--     structurally absent rather than filtered by hand. All thirty
--     ps3_v2 references are in code; none is in a comment. Each was
--     attributed to the route block containing it, and separately to
--     nothing at all -- which is the case that matters, because that is
--     where _device_360 lives.
--   * Views: each CREATE VIEW parsed as its own semicolon-terminated
--     statement, after comment-stripping that tracks string state, so an
--     apostrophe inside a line comment cannot open a false quote and
--     swallow the rest of the file. Control: comment-stripped CREATE
--     TABLE count 181, statement-level count 181, zero files disagreeing.
--   * Producers: write helpers read with a bracket-depth argument
--     parser, not a regex, because a first argument can contain commas.
--   * Front end: all thirty-five source files under dashboard/src.
--     101 literal API paths, 6 template literals -- every one of which
--     interpolates only a query string onto a literal path -- and ZERO
--     string concatenation. The path list is therefore complete, not
--     merely long. No component calls /ps3/v2- anything.
--
-- THE CONTROL, and it earned its keep. ps3_v25_device_summary is
-- provably live: V4PS3Overview.jsx calls /ps3/v25/device-summary. Run
-- through the identical checks it reports NO ROUTE BLOCK -- it is reached
-- through a module-level dispatch dict, not an if-path-equals block. A
-- check that only read route bodies would have called the control
-- retirable. It is not on any list here, and the outside-the-route-block
-- pass is what separates it from the eleven below.
--
-- SEVEN THAT ARE NOT DROPPED. _device_360 is not inside any route block,
-- serves /ps1/device-360, and V4api.js calls it; its response key
-- ps3_v2_rootcause_360 is read by V4Device360Popup.jsx, V4DeviceW.js and
-- V4Evidence.js, and appears three times in the shipped bundle.
--   ps3_v2_device_serial            read directly by _device_360
--   ps3_v2_device_serial_component  via v_ps3_v2_rootcause, AND fed into
--                                   device_level_aggregation by sql/59,
--                                   which /device/aggregate serves
--   ps3_v2_component_taxonomy       the LEFT JOIN inside v_ps3_v2_rootcause
--   ps3_v2_severity_action_queue    via v_ps3_v2_queue, and via
--                                   v_ps3_action_latest -> v_device_360
--   ps3_v2_shap_incident            via v_ps3_v2_shap
--   ps3_v2_device_reliability       via v_ps3_device_latest -> v_device_360,
--                                   behind /device/360/risk
--   ps3_v2_runs                     v_ps3_v2_current reads it, and the
--                                   queue, shap and rootcause views all
--                                   JOIN v_ps3_v2_current. One row, and
--                                   dropping it empties three live views.
-- sql/58 also builds per-device indexes on ps3_v2_device_reliability and
-- ps3_v2_severity_action_queue specifically to make v_device_360 fast.
-- They stay, frozen at their 29-Jul vintage. That is the same problem
-- sql/63 recorded for ps2_device_catalog: a live panel reading a table
-- nothing refreshes. Deleting them would not fix it, only change the
-- symptom from stale to empty.
--
-- WHAT BREAKS ON PURPOSE. Fourteen /ps3/v2- routes exist and no component
-- calls any of them; eleven lose their table here. They will not 500 --
-- every query in that block goes through _v2(), which catches and returns
-- an _unavailable payload. The loader verify action reads
-- v_ps3_v2_table_status and v_ps3_v2_policy and will stop working, and a
-- future full load would roll back on the first missing table. That is
-- accepted: there is no producer to load from.
--
-- ROWS DESTROYED: 428 at the 16-Aug inventory, across 11 tables, the
-- largest being ps3_v2_facility_hotspots at 320. The seven kept tables
-- hold 8,569 rows between them.
--
-- v_ps3_v2_rootcause_rollup is NOT dropped although only the orphan route
-- /ps3/v2-rootcause-rollup reads it. It is a view over v_ps3_v2_rootcause,
-- which stays; dropping it frees nothing and is a separate decision from
-- retiring tables.
--
-- No CASCADE. A DROP that fails because something unexpected depends on
-- the table is the outcome we want.
--
-- Ship through apply_sql, dry-run first. Swap the Lambda zip BEFORE
-- applying, because apply_sql reads this file from the package. This file
-- ends on a statement.
-- =====================================================================

-- ---------------------------------------------------------------------
-- 1. Views over the doomed tables, dropped first so the table DROPs below
--    are not refused. v_ps3_v2_current, v_ps3_v2_queue, v_ps3_v2_shap,
--    v_ps3_v2_rootcause and its two children are NOT here -- they read the
--    seven kept tables and _device_360 reads them.
--
--    v_ps3_v2_table_status counts eleven tables, eight of which go below.
--    It is the only object that spans both sets, and it serves only
--    /ps3/v2-status, which nothing calls.
-- ---------------------------------------------------------------------
DROP VIEW IF EXISTS v_ps3_v2_table_status;
DROP VIEW IF EXISTS v_ps3_v2_scorecard;
DROP VIEW IF EXISTS v_ps3_v2_causal;
DROP VIEW IF EXISTS v_ps3_v2_policy;

-- ---------------------------------------------------------------------
-- 2. sql/39 model-evaluation tables. These describe how the 29-Jul
--    bake-off went -- champions, gates, per-model metrics, permutation
--    importance. They are a record of one training run, served only by
--    /ps3/v2-scorecard, /ps3/v2-models and /ps3/v2-drivers.
--    ps3_v25_model_scorecard and ps3_v25_model_feature_importance are the
--    live generation of this content and are NOT affected.
-- ---------------------------------------------------------------------
DROP TABLE IF EXISTS ps3_v2_run_scorecard;
DROP TABLE IF EXISTS ps3_v2_model_comparison;
DROP TABLE IF EXISTS ps3_v2_driver_importance;
DROP TABLE IF EXISTS ps3_v2_promotion_status;

-- ---------------------------------------------------------------------
-- 3. sql/39 fleet-grain rollups. Note the pairing carefully:
--    ps3_v2_component_reliability is fleet grain and goes; the device x
--    serial x component table it looks like,
--    ps3_v2_device_serial_component, is the one _device_360 reads and it
--    STAYS. The names differ by a prefix, and the wrong one of a
--    near-identical pair has been dropped before for exactly that reason.
--    ps3_v2_device_reliability is also NOT here -- v_ps3_device_latest
--    feeds it into v_device_360.
-- ---------------------------------------------------------------------
DROP TABLE IF EXISTS ps3_v2_component_reliability;
DROP TABLE IF EXISTS ps3_v2_facility_hotspots;

-- ---------------------------------------------------------------------
-- 4. sql/39 global SHAP. ps3_v2_shap_incident is the per-incident table
--    and STAYS: _device_360 reads it through v_ps3_v2_shap to fill the
--    drivers panel. This is the fleet-level aggregate behind
--    /ps3/v2-shap-global only.
-- ---------------------------------------------------------------------
DROP TABLE IF EXISTS ps3_v2_shap_global;

-- ---------------------------------------------------------------------
-- 5. sql/39 causal estimates. AIPW risk differences from the 29-Jul run,
--    served only by /ps3/v2-causal. The live causal surface is
--    ps3_v25_causal_effects and ps3_v25_causal_balance behind
--    /ps3/v25/causal-effects, which V4PS3Overview.jsx does call.
-- ---------------------------------------------------------------------
DROP TABLE IF EXISTS ps3_v2_causal_effects;

-- ---------------------------------------------------------------------
-- 6. sql/39 run-governance trio, all three served only by /ps3/v2-status:
--    what the dashboard was allowed to display, whether the inputs were
--    fit to model, and how stale the window was. Governance about a run
--    that no longer feeds anything. ps3_v2_runs is NOT here -- it anchors
--    v_ps3_v2_current, which three live views JOIN.
-- ---------------------------------------------------------------------
DROP TABLE IF EXISTS ps3_v2_display_policy;
DROP TABLE IF EXISTS ps3_v2_readiness;
DROP TABLE IF EXISTS ps3_v2_data_freshness;
