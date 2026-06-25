-- =============================================================================
-- silver.use_revenue_daily  (S21)
-- Daily revenue aggregation from EDW.USE_TRANSACTION - device-day grain
-- PS1 revenue-decline feature; PS4 daily revenue signal
--
-- Source (bronze):
--   mars_dev.bronze.edw_use_transaction_daily  (EDW.USE_TRANSACTION)
--   mars_dev.silver.dim_device                 (S06 - device enrichment)
--
-- Bronze schema (confirmed 2026-06-25, 11 columns, 2,191,869 rows):
--   TRANSIT_DAY_KEY    decimal(8,0)   - date key format YYYYMMDD (e.g. 20240115)
--   DEVICE_ID          varchar(15)    - device identifier, matches dim_device 99.9%
--   REVENUE_OR_TEST    varchar(7)     - 'REVENUE' or 'TEST'; filter to REVENUE only
--   TXN_COUNT          decimal(38,10) - total transaction count for device-day
--   FARE_DUE_SUM       decimal(38,10) - sum of fare due (in CENTS)
--   CALC_FARE_SUM      decimal(38,10) - calculated fare sum (in CENTS)
--   EXTRA_FARE_SUM     decimal(38,10) - extra/surcharge fare sum (in CENTS)
--   UNCOLLECTIBLE_SUM  decimal(38,10) - uncollectible amount (in CENTS)
--   PRICED_TXN_COUNT   decimal(38,10) - count of priced (non-zero-fare) transactions
--   FIRST_TXN_DTM      timestamp      - first transaction timestamp of the day
--   LAST_TXN_DTM       timestamp      - last transaction timestamp of the day
--
-- Grain: ALREADY device-day in bronze (TRANSIT_DAY_KEY + DEVICE_ID + REVENUE_OR_TEST)
--   -> No aggregation needed; filter to REVENUE_OR_TEST = 'REVENUE' and enrich.
--
-- All monetary amounts kept in CENTS in silver; divide by 100.0 in gold for dollars.
--
-- Michael R3 context (QA doc R3-1):
--   USE_TRANSACTION IS the correct schema+object; account-based taps + trips merged.
--   FARE_DUE in cents confirmed. No USAGE_FEE column. Revenue feature for PS1 + PS4.
--
-- Build order: S06 (dim_device) -> S21 (this table)
-- Feeds:       gold.device_ps1_daily (revenue_decline_flag feature)
--              gold.device_ps4_hourly (daily revenue proxy)
-- =============================================================================

DROP TABLE IF EXISTS mars_dev.silver.use_revenue_daily;

CREATE TABLE mars_dev.silver.use_revenue_daily AS
SELECT
    -- -- Keys -----------------------------------------------------------------
    ut.DEVICE_ID,
    -- Convert YYYYMMDD integer key to DATE
    TO_DATE(CAST(ut.TRANSIT_DAY_KEY AS STRING), 'yyyyMMdd')    AS transit_day,
    ut.TRANSIT_DAY_KEY,

    -- -- Transaction volume ----------------------------------------------------
    CAST(ut.TXN_COUNT         AS BIGINT)                        AS daily_txn_count,
    CAST(ut.PRICED_TXN_COUNT  AS BIGINT)                        AS priced_txn_count,
    CASE
        WHEN ut.TXN_COUNT > 0
        THEN ROUND(CAST(ut.PRICED_TXN_COUNT AS DOUBLE) / CAST(ut.TXN_COUNT AS DOUBLE) * 100, 4)
        ELSE 0
    END                                                         AS priced_txn_pct,

    -- -- Revenue (all in CENTS - divide by 100.0 in gold for dollars) ---------
    CAST(ut.FARE_DUE_SUM      AS DECIMAL(18,2))                 AS daily_fare_due_cents,
    CAST(ut.CALC_FARE_SUM     AS DECIMAL(18,2))                 AS daily_calc_fare_cents,
    CAST(ut.EXTRA_FARE_SUM    AS DECIMAL(18,2))                 AS daily_extra_fare_cents,
    CAST(ut.UNCOLLECTIBLE_SUM AS DECIMAL(18,2))                 AS daily_uncollectible_cents,

    -- Effective revenue = FARE_DUE_SUM - UNCOLLECTIBLE_SUM
    CAST(ut.FARE_DUE_SUM - ut.UNCOLLECTIBLE_SUM AS DECIMAL(18,2))
                                                                AS daily_net_revenue_cents,

    -- Dollar convenience columns (gold can use these directly)
    ROUND(CAST(ut.FARE_DUE_SUM      AS DOUBLE) / 100.0, 2)     AS daily_fare_due_dollars,
    ROUND(CAST(ut.FARE_DUE_SUM - ut.UNCOLLECTIBLE_SUM AS DOUBLE) / 100.0, 2)
                                                                AS daily_net_revenue_dollars,

    -- -- Operational window ----------------------------------------------------
    ut.FIRST_TXN_DTM,
    ut.LAST_TXN_DTM,
    ROUND((UNIX_TIMESTAMP(ut.LAST_TXN_DTM) - UNIX_TIMESTAMP(ut.FIRST_TXN_DTM)) / 3600.0, 2)
                                                                AS revenue_active_hours,

    -- -- Device enrichment from S06 --------------------------------------------
    dd.DEVICE_KEY,
    dd.DEVICE_NAME,
    dd.mars_device_category,
    dd.FACILITY_ID,
    dd.FACILITY_NAME,
    dd.OPERATOR_ID,
    dd.OPERATOR_NAME,
    dd.TRANSIT_MODE_NAME

FROM mars_dev.bronze.edw_use_transaction_daily ut
LEFT JOIN mars_dev.silver.dim_device dd
    ON  dd.DEVICE_ID  = ut.DEVICE_ID
    AND dd.is_current = TRUE
WHERE ut.REVENUE_OR_TEST = 'REVENUE'                           -- exclude test transactions
  AND ut.TRANSIT_DAY_KEY >= 20240101;                          -- ML training window

-- Post-load verification:
-- SELECT
--     COUNT(*)                                                   AS total_rows,
--     COUNT(DISTINCT DEVICE_ID)                                  AS distinct_devices,
--     MIN(transit_day)                                           AS earliest_day,
--     MAX(transit_day)                                           AS latest_day,
--     SUM(CASE WHEN DEVICE_KEY IS NULL THEN 1 ELSE 0 END)        AS unmatched_devices,
--     ROUND(SUM(daily_fare_due_dollars) / 1e6, 2)                AS total_revenue_million_dollars
-- FROM mars_dev.silver.use_revenue_daily;
-- Expected: ~2.0M rows (post REVENUE filter); unmatched < 0.1%
