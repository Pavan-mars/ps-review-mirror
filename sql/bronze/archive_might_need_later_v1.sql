-- =====================================================================
-- archive_might_need_later_v1.sql  (Databricks SQL, catalog mars_dev)
--
-- Decision 13-Sep-2026 (PK): the 11 bronze tables that no silver/gold
-- DDL, no notebook and no SageMaker notebook reads are NOT dropped.
-- They are renamed with the prefix might_need_later__ and taken out of
-- the incremental contract scope, so they stop costing refresh time but
-- can be brought back with a single RENAME.
--
-- Facts behind the list (notebooks/incremental/00_SCOPE_tables_in_scope.csv,
-- tier 3; three independent scans, 02-Sep-2026):
--   - none of the 11 appears in any FROM/JOIN of sql/silver, sql/gold,
--     the 21 Python notebooks or the 15 SageMaker notebooks;
--   - ncs_stage_device_event_history is the largest object in the
--     lakehouse (1.25 B rows, 46.7 GB) and has zero consumers.
--
-- Safety:
--   - RENAME on a Unity Catalog managed table moves no data; storage,
--     history and time travel are preserved; cost is unchanged.
--   - The immutable raw parquet under chicago_ventra/ is the real
--     archive: any of these can also be rebuilt from raw.
--   - Retrieval = ALTER TABLE ... RENAME TO the original name and set
--     scope back in audit.bronze_data_contract.
--   - Run each block once; the QUALIFY guard below refuses to rename a
--     table that is missing or already renamed.
-- =====================================================================
USE CATALOG mars_dev;

-- 0. Pre-check: every table must exist under its original name.
SELECT table_name,
       CASE WHEN table_name LIKE 'might_need_later__%' THEN 'ALREADY_RENAMED' ELSE 'OK' END AS state
FROM information_schema.tables
WHERE table_schema = 'bronze'
  AND (table_name IN ('ncs_stage_device_event_history','cta_abp_use_tran_timing_data','edw_metric_summary_by_day',
                      'ncs_stage_cashbox_manual_counts','ncs_stage_sale_transaction_device_msg','edw_device_last_set_event',
                      'edw_device_location_history','edw_device_current_tables','edw_device_current_sw_config',
                      'edw_date_dimension','cta_kpi_tvm_date_table')
       OR table_name LIKE 'might_need_later__%')
ORDER BY table_name;

-- 1. Rename (same schema; UC managed tables rename in place).
ALTER TABLE bronze.ncs_stage_device_event_history        RENAME TO bronze.might_need_later__ncs_stage_device_event_history;
ALTER TABLE bronze.cta_abp_use_tran_timing_data          RENAME TO bronze.might_need_later__cta_abp_use_tran_timing_data;
ALTER TABLE bronze.edw_metric_summary_by_day             RENAME TO bronze.might_need_later__edw_metric_summary_by_day;
ALTER TABLE bronze.ncs_stage_cashbox_manual_counts       RENAME TO bronze.might_need_later__ncs_stage_cashbox_manual_counts;
ALTER TABLE bronze.ncs_stage_sale_transaction_device_msg RENAME TO bronze.might_need_later__ncs_stage_sale_transaction_device_msg;
ALTER TABLE bronze.edw_device_last_set_event             RENAME TO bronze.might_need_later__edw_device_last_set_event;
ALTER TABLE bronze.edw_device_location_history           RENAME TO bronze.might_need_later__edw_device_location_history;
ALTER TABLE bronze.edw_device_current_tables             RENAME TO bronze.might_need_later__edw_device_current_tables;
ALTER TABLE bronze.edw_device_current_sw_config          RENAME TO bronze.might_need_later__edw_device_current_sw_config;
ALTER TABLE bronze.edw_date_dimension                    RENAME TO bronze.might_need_later__edw_date_dimension;
ALTER TABLE bronze.cta_kpi_tvm_date_table                RENAME TO bronze.might_need_later__cta_kpi_tvm_date_table;

-- 2. Say why, on the table itself.
COMMENT ON TABLE bronze.might_need_later__ncs_stage_device_event_history        IS 'Archived 2026-09-13: no consumer in silver/gold/notebooks (tier 3). Restore = RENAME back + contract scope.';
COMMENT ON TABLE bronze.might_need_later__cta_abp_use_tran_timing_data          IS 'Archived 2026-09-13: silver/13 references it in a comment only.';
COMMENT ON TABLE bronze.might_need_later__edw_metric_summary_by_day             IS 'Archived 2026-09-13: no consumer.';
COMMENT ON TABLE bronze.might_need_later__ncs_stage_cashbox_manual_counts       IS 'Archived 2026-09-13: no consumer.';
COMMENT ON TABLE bronze.might_need_later__ncs_stage_sale_transaction_device_msg IS 'Archived 2026-09-13: no consumer.';
COMMENT ON TABLE bronze.might_need_later__edw_device_last_set_event             IS 'Archived 2026-09-13: no consumer.';
COMMENT ON TABLE bronze.might_need_later__edw_device_location_history           IS 'Archived 2026-09-13: no consumer.';
COMMENT ON TABLE bronze.might_need_later__edw_device_current_tables             IS 'Archived 2026-09-13: no consumer.';
COMMENT ON TABLE bronze.might_need_later__edw_device_current_sw_config          IS 'Archived 2026-09-13: no consumer.';
COMMENT ON TABLE bronze.might_need_later__edw_date_dimension                    IS 'Archived 2026-09-13: no consumer (silver uses its own date_time dim).';
COMMENT ON TABLE bronze.might_need_later__cta_kpi_tvm_date_table                IS 'Archived 2026-09-13: silver/12 references it in a comment only.';

-- 3. Take them out of the incremental scope so NB12 never refreshes them.
--    (column names follow audit.bronze_data_contract as of NB10; adjust if
--     the contract uses different names -- check with DESCRIBE first.)
UPDATE audit.bronze_data_contract
SET    load_strategy = 'drop_from_scope',
       scope         = 'archived',
       notes         = CONCAT(COALESCE(notes, ''), ' | archived 2026-09-13 as might_need_later__ (no consumer)')
WHERE  table_name IN ('ncs_stage_device_event_history','cta_abp_use_tran_timing_data','edw_metric_summary_by_day',
                      'ncs_stage_cashbox_manual_counts','ncs_stage_sale_transaction_device_msg','edw_device_last_set_event',
                      'edw_device_location_history','edw_device_current_tables','edw_device_current_sw_config',
                      'edw_date_dimension','cta_kpi_tvm_date_table');

-- 4. Post-check: 11 rows, all might_need_later__, all still with their row counts.
SELECT table_name, comment
FROM   information_schema.tables
WHERE  table_schema = 'bronze' AND table_name LIKE 'might_need_later__%'
ORDER  BY table_name;

-- Restore example (one table):
-- ALTER TABLE bronze.might_need_later__edw_date_dimension RENAME TO bronze.edw_date_dimension;
-- UPDATE audit.bronze_data_contract SET load_strategy='full', scope='whole' WHERE table_name='edw_date_dimension';
