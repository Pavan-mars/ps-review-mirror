-- =====================================================================
-- CUBIC MARS Chicago -- sql/51
-- Retire ps1_failure_summary. Mark it, do not remove it.
-- Date: 2026-08-10   Idempotent. Reversible -- rollback SQL in the footer.
--
-- WHAT THIS TABLE IS
-- ------------------
-- Two rows, TVM and Gates, typed by hand on 13-Jul-2026 out of a console log
-- (the INSERT now lives in sql/manual/seed_from_04_phase1c_ps1_failure.sql).
-- quality_gate FAIL, promoted false, and target = 'will_fail_3d' -- a label
-- name that exists nowhere in this system. The column the models are trained
-- on is will_hardware_oos_3d. VALIDATOR is absent entirely.
--
-- No loader has ever written to it. There is no refresh path, so it could only
-- age. Until today /ps1/summary read it, which means the dashboard has been
-- presenting a four-week-old FAILED, NOT-PROMOTED, two-fleet verdict as the
-- current state of PS1 while ps1_model_performance sat beside it holding the
-- 26-Jul sklearn run: three fleets, PASS, promoted, correct target column.
--
-- WHAT THIS FILE DOES, AND DOES NOT DO
-- ------------------------------------
-- Does:  records the retirement in the database catalog, so that anyone who
--        reaches this table through psql \d+, an ORM introspection or a schema
--        dump learns it is retired without having to read handler.py.
-- Does:  corrects v_ps1_table_status, whose superseded_by mapping pointed
--        ps1_failure_summary at v_ps1_xw_summary. That was wrong.
--        v_ps1_xw_summary is a per-fleet ROW-COUNT reconciliation of the
--        cross-wired load -- it is a data summary, not a model scorecard, and
--        it carries no AUC, no gate and no promotion decision. The scorecard
--        that actually replaces this table is ps1_model_performance.
-- Does:  correct two other rows of that same mapping that went stale on
--        26-Jul when sql/load/ finally populated ps1_model_performance (0 -> 3)
--        and ps1_feature_importance (0 -> 45). They are no longer empty and
--        must stop being described as superseded.
--
-- Does NOT: DROP the table. Does NOT DELETE its rows. Does NOT ALTER it.
--        migrate() would recreate it empty on the next deploy anyway
--        (CREATE TABLE IF NOT EXISTS, sql/04), so a DROP buys nothing and
--        costs the audit trail of what the dashboard used to show. The rows
--        stay as the record of the 13-Jul run, which is what they honestly are.
--
-- The route change that makes this real is in handler.py, not here:
-- /ps1/summary now reads ps1_model_performance + ps1_confusion, and falls back
-- to this table only when a city has no model-performance row. Deleting the
-- rows here would break that fallback for no gain.
-- =====================================================================

COMMENT ON TABLE ps1_failure_summary IS
  'RETIRED 2026-08-10. Hand-seeded on 2026-07-13 from a console log; two fleets '
  'only (TVM, Gates -- no VALIDATOR); quality_gate FAIL; promoted false; '
  'target=''will_fail_3d'', which is not a column in this system (the real label '
  'is will_hardware_oos_3d). No loader writes to it and it has no refresh path. '
  'SUPERSEDED BY ps1_model_performance + ps1_confusion, which /ps1/summary now '
  'reads. Rows retained deliberately: they are the record of the 13-Jul run and '
  'they back the fallback in /ps1/summary for a city with no model-performance '
  'row. Do not build anything new on this table.';

COMMENT ON COLUMN ps1_failure_summary.target IS
  'Holds ''will_fail_3d'' for the 13-Jul seed rows. That label does not exist. '
  'The trained target is will_hardware_oos_3d -- see ps1_model_performance.target_col.';

-- ---------------------------------------------------------------------------
-- v_ps1_table_status: correct the three rows that are now wrong.
--
-- Column contract: the four columns sql/36 published keep their names, order
-- and types. `retired` is APPENDED, which is the only shape change
-- CREATE OR REPLACE VIEW permits. The DROP is not a CASCADE, on purpose: if
-- anything has come to depend on this view it fails loudly rather than being
-- removed silently, and the CREATE OR REPLACE that follows still applies.
-- ---------------------------------------------------------------------------
DROP VIEW IF EXISTS v_ps1_table_status;

CREATE OR REPLACE VIEW v_ps1_table_status AS
SELECT t.table_name, t.n_rows, t.superseded_by, (t.n_rows = 0) AS is_empty, t.retired
FROM (
  VALUES
    ('ps1_cross_wired_daily',  (SELECT COUNT(*) FROM ps1_cross_wired_daily),  NULL::text,                  FALSE),
    -- 26-Jul: sql/load/ populated this, 0 -> 45 rows. Not empty, not superseded.
    ('ps1_feature_importance', (SELECT COUNT(*) FROM ps1_feature_importance), NULL::text,                  FALSE),
    -- 26-Jul: sql/load/ populated this, 0 -> 3 rows, and as of sql/51 it is the
    -- scorecard /ps1/summary reads. It supersedes; it is not superseded.
    ('ps1_model_performance',  (SELECT COUNT(*) FROM ps1_model_performance),  NULL::text,                  FALSE),
    ('ps1_calibration',        (SELECT COUNT(*) FROM ps1_calibration),        'v_ps1_xw_tier_calibration', FALSE),
    ('ps1_explainability',     (SELECT COUNT(*) FROM ps1_explainability),     'v_ps1_device_drivers',      FALSE),
    ('ps1_features',           (SELECT COUNT(*) FROM ps1_features),           'ps1_cross_wired_daily',     FALSE),
    -- was 'v_ps1_xw_summary', which is a row-count reconciliation of the
    -- cross-wired load and carries no model metric at all. Corrected.
    ('ps1_failure_summary',    (SELECT COUNT(*) FROM ps1_failure_summary),    'ps1_model_performance',     TRUE)
) AS t(table_name, n_rows, superseded_by, retired);

COMMENT ON VIEW v_ps1_table_status IS
  'Which PS1 tables hold anything, and what replaced the ones that do not. '
  'retired=true means the table still exists and still has rows but nothing '
  'should read it -- see superseded_by. sql/51, 2026-08-10.';

-- ---------------------------------------------------------------------------
-- ROLLBACK. Nothing here writes a row, so the undo is three statements:
--
--   COMMENT ON TABLE ps1_failure_summary IS NULL;
--   COMMENT ON COLUMN ps1_failure_summary.target IS NULL;
--   DROP VIEW IF EXISTS v_ps1_table_status;
--   -- then re-apply sql/36_ps1_state_framing.sql, which recreates
--   -- v_ps1_table_status with the original four-column mapping.
--
-- To restore the ROUTE as well, revert the /ps1/summary block in handler.py to
-- its single-SELECT form against ps1_failure_summary. The table was never
-- touched, so that revert is complete on its own.
-- ---------------------------------------------------------------------------
