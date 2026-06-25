-- =============================================================================
-- silver.hw_config_current
-- Hardware component configuration - current snapshot (SCD1 overwrite)
--
-- Source (mars_dev.bronze catalog):
--   EDW.DEVICE_CURRENT_HW_CONFIG  (11,736 rows, 17 cols)
--
-- Dimension joined:
--   mars_dev.silver.dim_device (S06 - must be created first)
--
-- Notes:
--   - COMPONENT_SERIAL_NBR: NULLIF(TRIM(...), '') cleans blank strings
--   - REPORTED_CHANGED_DTM = component install/swap date; may be NULL for legacy components
--   - component_age_days: days from install (or last_reported if install unknown) to today
--   - Partitioned by city_id for multi-tenant extension (all Chicago rows = 'CHICAGO')
--   - Coverage: 11.7K rows - not all devices have HW config records
--
-- Validation run 2026-06-15 - bugs fixed from original:
--   BUG 1: edw_chicago.EDW.DEVICE_CURRENT_HW_CONFIG -> parquet S3 path
--           'edw_chicago' is not a Databricks catalog (only mars_dev exists)
--   BUG 2: PARTITIONED BY (city_id) ZORDER BY (DEVICE_ID) in CTAS
--           ZORDER BY is not valid in CREATE TABLE - only in OPTIMIZE
--           Fix: removed from CREATE; OPTIMIZE command added at end
--   BUG 3: e.REPORTED_CHANGED_DTM::date -> CAST(... AS DATE)
--   BUG 4: e.LAST_REPORTED_DTM::date    -> CAST(... AS DATE)
--   BUG 5: e.OPERATOR_ID               -> dd.OPERATOR_ID
--           Bronze parquet has only 6 cols (DEVICE_KEY, COMPONENT_DESCRIPTION,
--           LAST_REPORTED_DTM, REPORTED_CHANGED_DTM, COMPONENT_SERIAL_NBR, DEVICE_ID)
--           OPERATOR_ID is not ingested; get from dim_device join instead
--
-- Validation run 2026-06-18 - additional fix:
--   BUG 6: INNER JOIN (driving hw_config) -> LEFT JOIN (driving dim_device)
--           Original INNER JOIN silently dropped all dim_device devices that have
--           no HW config record. Devices without components are valid ML subjects -
--           excluding them from hw_config_current causes downstream PS5 feature
--           joins to lose those devices entirely. Fix: drive from dim_device with
--           LEFT JOIN to hw_config so all active devices appear; component columns
--           are NULL for devices with no HW config record (handled by PS5 imputation).
--           hw_source set to NULL (not 'EDW') when no HW record exists.
--
--   NOTE:  Multiple rows per device expected (one per component: AV2_SAM, SIM_ICCID,
--           billacceptor, etc.)
-- =============================================================================

CREATE OR REPLACE TABLE mars_dev.silver.hw_config_current
USING DELTA
PARTITIONED BY (city_id)
AS
SELECT
    dd.DEVICE_ID,
    e.COMPONENT_DESCRIPTION,
    NULLIF(TRIM(e.COMPONENT_SERIAL_NBR), '')  AS COMPONENT_SERIAL_NBR,
    e.LAST_REPORTED_DTM,
    e.REPORTED_CHANGED_DTM,
    dd.OPERATOR_ID,
    dd.FACILITY_ID,
    CASE WHEN e.DEVICE_ID IS NOT NULL THEN 'EDW' END AS hw_source,
    CASE
        WHEN e.REPORTED_CHANGED_DTM IS NOT NULL
        THEN DATEDIFF(CURRENT_DATE(), CAST(e.REPORTED_CHANGED_DTM AS DATE))
        WHEN e.LAST_REPORTED_DTM IS NOT NULL
        THEN DATEDIFF(CURRENT_DATE(), CAST(e.LAST_REPORTED_DTM  AS DATE))
        ELSE NULL
    END                                       AS component_age_days,
    dd.DEVICE_KEY,
    dd.DEVICE_NAME,
    dd.DEVICE_TYPE_NAME,
    dd.mars_device_category,
    dd.FACILITY_NAME,
    dd.OPERATOR_NAME,
    dd.DEVICE_SERIAL_NUMBER                   AS device_serial_number,
    'CHICAGO'                                 AS city_id

FROM mars_dev.silver.dim_device dd
LEFT JOIN mars_dev.bronze.edw_device_current_hw_config e
    ON e.DEVICE_ID = dd.DEVICE_ID
WHERE dd.is_current = TRUE;

-- Post-load optimisation:
-- OPTIMIZE mars_dev.silver.hw_config_current ZORDER BY (DEVICE_ID);
