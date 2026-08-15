-- =====================================================================
-- CUBIC MARS Chicago -- PS1 inventory: row counts and freshness
--
-- READ ONLY. Every statement is a SELECT. No DDL, no DML, no transaction
-- that can leave anything behind. Safe to run at any time, repeatedly.
--
-- Run it exactly the way sql/50 through sql/54 were run today.
--
-- PURPOSE: item 5 of the PS1 inventory -- "all RDS tables with their
-- schema and the no of rows currently available". The SCHEMA half comes
-- from the migrations and is in docs/PS1_OPERATIONAL_INVENTORY.md §5.
-- Only the COUNTS need the live database, and only the live database
-- can answer them -- a row count is not derivable from DDL.
--
-- WHAT TO LOOK FOR, so the output is not just numbers:
--
--   1. ps1_cross_wired_daily should be 786,525 (107,110 GATE +
--      184,483 TVM + 494,932 VALIDATOR). That is the loader's own
--      EXPECTED constant, measured 28-Jul. A different number is not
--      automatically wrong -- but it is automatically worth explaining.
--
--   2. ps1_failure_predictions / ps1_serial_predictions /
--      ps1_inference_runs are PATH B tables, and Path B was switched
--      off on 2026-08-10 at 11:55Z. Their max(computed_date) should be
--      ON OR BEFORE 2026-08-10. If it is later, something is still
--      writing to them and the shutdown is not what it appears.
--
--   3. Several dashboard routes read those frozen tables. Query 5
--      makes the staleness explicit per table rather than leaving it
--      to be inferred from a date column nobody looks at.
--
--   4. A count of 0 is a FACT, not a failure. Several PS1 tables have
--      never been populated. Query 1 lists every table including the
--      empty ones, precisely so that "absent from the output" can
--      never be confused with "zero".
-- =====================================================================

-- ---------------------------------------------------------------------
-- 1. Row count for every PS1 object, empty ones included.
--    Built from a VALUES list rather than a catalog scan so that a
--    table which does not exist raises here, loudly, instead of being
--    silently omitted from the report.
-- ---------------------------------------------------------------------
SELECT 'ps1_cross_wired_daily'    AS table_name, COUNT(*) AS n_rows FROM ps1_cross_wired_daily
UNION ALL SELECT 'ps1_failure_predictions',  COUNT(*) FROM ps1_failure_predictions
UNION ALL SELECT 'ps1_serial_predictions',   COUNT(*) FROM ps1_serial_predictions
UNION ALL SELECT 'ps1_inference_runs',       COUNT(*) FROM ps1_inference_runs
UNION ALL SELECT 'ps1_model_performance',    COUNT(*) FROM ps1_model_performance
UNION ALL SELECT 'ps1_confusion',            COUNT(*) FROM ps1_confusion
UNION ALL SELECT 'ps1_feature_importance',   COUNT(*) FROM ps1_feature_importance
UNION ALL SELECT 'ps1_explainability',       COUNT(*) FROM ps1_explainability
UNION ALL SELECT 'ps1_leaderboard',          COUNT(*) FROM ps1_leaderboard
UNION ALL SELECT 'ps1_features',             COUNT(*) FROM ps1_features
UNION ALL SELECT 'ps1_risk_trend',           COUNT(*) FROM ps1_risk_trend
UNION ALL SELECT 'ps1_risk_bands',           COUNT(*) FROM ps1_risk_bands
UNION ALL SELECT 'ps1_threshold_sweep',      COUNT(*) FROM ps1_threshold_sweep
UNION ALL SELECT 'ps1_calibration',          COUNT(*) FROM ps1_calibration
UNION ALL SELECT 'ps1_station_summary',      COUNT(*) FROM ps1_station_summary
UNION ALL SELECT 'ps1_failure_summary',      COUNT(*) FROM ps1_failure_summary
UNION ALL SELECT 'ml_batch_load_audit',      COUNT(*) FROM ml_batch_load_audit
UNION ALL SELECT 'servicenow_staging',       COUNT(*) FROM servicenow_staging
ORDER BY table_name;

-- ---------------------------------------------------------------------
-- 2. The 786,525 contract, per fleet.
--    Also counts distinct devices and days, because a right total made
--    of the wrong shape is still wrong -- e.g. one day of 786k rows
--    rather than many days of a few thousand.
-- ---------------------------------------------------------------------
SELECT device_type,
       COUNT(*)                          AS n_rows,
       COUNT(DISTINCT device_key)        AS n_devices,
       COUNT(DISTINCT transit_day)       AS n_days,
       MIN(transit_day)                  AS first_day,
       MAX(transit_day)                  AS last_day,
       COUNT(*) FILTER (WHERE component_serial_nbr IS NULL) AS n_null_serial,
       COUNT(*) FILTER (WHERE ps1_fail_prob IS NULL)        AS n_null_prob,
       ROUND(AVG(will_hardware_oos_3d)::numeric * 100, 4)   AS label_base_rate_pct
FROM   ps1_cross_wired_daily
GROUP  BY device_type
UNION ALL
SELECT 'TOTAL', COUNT(*), COUNT(DISTINCT device_key), COUNT(DISTINCT transit_day),
       MIN(transit_day), MAX(transit_day),
       COUNT(*) FILTER (WHERE component_serial_nbr IS NULL),
       COUNT(*) FILTER (WHERE ps1_fail_prob IS NULL),
       ROUND(AVG(will_hardware_oos_3d)::numeric * 100, 4)
FROM   ps1_cross_wired_daily
ORDER  BY 1;

-- ---------------------------------------------------------------------
-- 3. Path B tables -- are they really frozen?
--    Path B was disabled 2026-08-10 11:55Z. These three should not
--    have moved since. This is the query that would catch a revival
--    nobody announced.
-- ---------------------------------------------------------------------
SELECT 'ps1_failure_predictions' AS table_name,
       COUNT(*) AS n_rows, MAX(computed_date) AS max_computed_date,
       COUNT(DISTINCT run_id) AS n_runs
FROM   ps1_failure_predictions
UNION ALL
SELECT 'ps1_serial_predictions', COUNT(*), MAX(computed_date), COUNT(DISTINCT run_id)
FROM   ps1_serial_predictions
UNION ALL
SELECT 'ps1_inference_runs', COUNT(*), MAX(computed_date), COUNT(DISTINCT run_id)
FROM   ps1_inference_runs;

-- ---------------------------------------------------------------------
-- 4. Every inference run on record, both kinds.
--    run_kind matters: 'train' rows say a model was registered,
--    'batch_score' rows say a loader scored something. sql/53 conflated
--    them once and produced a falsehood; keep them visibly separate.
-- ---------------------------------------------------------------------
SELECT run_id, run_kind, device_category, endpoint_name, mlflow_version,
       model_version, status, n_devices_scored, n_flagged,
       run_ts, computed_date
FROM   ps1_inference_runs
ORDER  BY run_ts DESC, device_category;

-- ---------------------------------------------------------------------
-- 5. Staleness, stated rather than implied.
--    Each row says how old its newest record is. A dashboard panel
--    reading a table with a large age is showing history while looking
--    like it is showing today.
-- ---------------------------------------------------------------------
SELECT t.table_name, t.max_date,
       (CURRENT_DATE - t.max_date) AS days_stale,
       t.reads_via
FROM (
  SELECT 'ps1_cross_wired_daily'   AS table_name, MAX(transit_day)    AS max_date,
         'PATH A live -- /ps1/predictions + 14 xw-* routes'            AS reads_via
    FROM ps1_cross_wired_daily
  UNION ALL
  SELECT 'ps1_failure_predictions', MAX(computed_date),
         'PATH B frozen -- /ps1/crosstab, /ps1/coverage'
    FROM ps1_failure_predictions
  UNION ALL
  SELECT 'ps1_serial_predictions', MAX(computed_date),
         'PATH B frozen -- /ps1/serial-predictions'
    FROM ps1_serial_predictions
  UNION ALL
  SELECT 'ps1_inference_runs', MAX(computed_date),
         'PATH B frozen -- /ps1/runs, /ps1/model-performance'
    FROM ps1_inference_runs
  UNION ALL
  SELECT 'ps1_model_performance', MAX(computed_date),
         'sql/load 26-Jul -- /ps1/summary, /ps1/leaderboard'
    FROM ps1_model_performance
  UNION ALL
  SELECT 'ps1_confusion', MAX(computed_date),
         'sql/load 26-Jul -- /ps1/confusion, /ps1/summary'
    FROM ps1_confusion
) t
ORDER BY days_stale DESC NULLS FIRST;

-- ---------------------------------------------------------------------
-- 6. E-1, straight from the view that exists to answer it.
--    NOTE this view is read by NO dashboard route -- verified by grep
--    over handler.py. It is a data-layer disclosure that nothing
--    currently surfaces. Expected state 2026-08-11: GAP on all three
--    fleets. And remember its own COMMENT: serving_run_id is a PROXY.
--    Registration is not deployment. Only DescribeEndpointConfig
--    observes what an endpoint actually runs.
-- ---------------------------------------------------------------------
SELECT * FROM v_ps1_serving_gap ORDER BY device_category;

-- ---------------------------------------------------------------------
-- 7. Provenance completeness after sql/52 + 53 + 54.
--    VALIDATOR.recall_floor NULL is expected and is a pending decision,
--    not a defect.
-- ---------------------------------------------------------------------
SELECT * FROM v_ps1_provenance_gaps ORDER BY device_category;

-- ---------------------------------------------------------------------
-- 8. The scorecard itself, with the five provenance columns visible.
-- ---------------------------------------------------------------------
SELECT city_id, device_category, model_name, algorithm,
       test_auc, test_ap, test_f1, test_prec, test_rec,
       decision_threshold, mlflow_version, endpoint_name,
       quality_gate, promoted,
       target_col, label_revision, recall_floor, base_rate_pct, run_id,
       computed_date
FROM   ps1_model_performance
ORDER  BY device_category;

-- ---------------------------------------------------------------------
-- 9. Cross-wire sufficiency (sql/50). sufficient_data=false means the
--    cells were too small to publish a rate -- which is a statement,
--    not a silence. That distinction is the whole point of sql/50.
-- ---------------------------------------------------------------------
SELECT * FROM v_ps1_xw_causation ORDER BY device_type;

-- ---------------------------------------------------------------------
-- 10. Loader audit -- what actually ran, and what it skipped.
--     columns_skipped is the interesting field: a loader that silently
--     drops a column it did not recognise will look like a clean load.
-- ---------------------------------------------------------------------
SELECT ps_id, target_table, run_id, s3_source,
       rows_read, rows_loaded, columns_added, columns_skipped,
       status, error_text, loaded_at
FROM   ml_batch_load_audit
WHERE  ps_id = 'PS1' OR target_table LIKE 'ps1%'
ORDER  BY loaded_at DESC
LIMIT  50;

-- ---------------------------------------------------------------------
-- 11. Table status view, including the retired flag from sql/51.
--     NOTE: this view has no city_id filter -- its counts are
--     account-wide, not per city. Fine today (one city), wrong the day
--     Boston lands.
-- ---------------------------------------------------------------------
SELECT * FROM v_ps1_table_status ORDER BY table_name;
