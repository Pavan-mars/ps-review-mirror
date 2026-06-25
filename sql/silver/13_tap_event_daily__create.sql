-- =============================================================================
-- silver.tap_event_daily
-- Daily aggregated tap/transaction events per device
--
-- Source (mars_dev.bronze catalog):
--   EDW.ABP_TAP  (2.05B rows, 52 cols) -> mars_dev.bronze.edw_abp_tap
--   NOTE: CTA.ABP_USE_TRAN_TIMING_DATA - PATH_NOT_FOUND; excluded from pipeline
--
-- Notes:
--   - ABP_TAP is partitioned by year/month/day (EDW load date, NOT tap date)
--     Use DATE(TRANSACTION_DTM) for tap date - do NOT filter on partition columns
--   - Grain: (DEVICE_ID, DATE(TRANSACTION_DTM), OPERATOR_ID, BUS_ID)
--   - 3,150 distinct CTA devices: bus validators (BMV), gate readers, TVMs
--   - TAP_STATUS_ID approved codes (validated 2026-06-15):
--       1   = Device Approved          (41.62%)
--       900 = Server Approved          (52.86%)
--       904 = Server Approved Cached   (0.03%)
--       All other codes = rejected     (5.49%)
--     Rejection categories include: 901 Server Denied, 5 Passback,
--       11 Timeout, 4 Risk Assessment, 903 Multi-Ride Denied, 701 Stale Tap
--   - peak_hour_tap_count: MAX hourly tap count per device per day;
--     replaces the unsupported LEFT JOIN LATERAL with a subquery CTE
--   - FACILITY_ID not in ABP_TAP; sourced from mars_dev.silver.dim_device join
--   - dim_device join: 100% match rate (validated 2026-06-15)
--
-- Validation run 2026-06-15:
--   Dry-run 2025-01-01 to 07: 17,699 device-day rows, 3,150 devices, 3.55M taps
--   dim_device=100%, peak_hour=100%, avg_reject_rate ≈ 5.49%
--
-- Bugs fixed from original (21 total):
--   BUG 1:  silver.tap_event_daily -> mars_dev.silver.tap_event_daily
--   BUG 2:  TRANSIT_DAY_KEY::text + ::date -> removed (column does not exist in ABP_TAP)
--   BUG 3:  bronze.abp_tap -> parquet S3 path
--   BUG 4:  bronze.cta_abp_use_tran_timing_data -> PATH_NOT_FOUND;
--           timing_agg CTE + join + avg/max/p95_timing_ms + has_slow_transactions removed
--   BUG 5:  silver.dim_device ON DEVICE_KEY -> mars_dev.silver.dim_device ON DEVICE_ID
--   BUG 6:  CREATE INDEX (x6) -> not supported on Delta; OPTIMIZE/ZORDER comment only
--   BUG 7:  LEFT JOIN LATERAL (...) -> not supported in Spark SQL;
--           rewritten as peak_hour_agg subquery CTE
--   BUG 8:  EXTRACT(HOUR FROM TAP_DTM) -> HOUR(TRANSACTION_DTM)
--   BUG 9:  PERCENTILE_CONT(0.95) WITHIN GROUP -> removed (timing table gone)
--   BUG 10: ROUND(x::numeric / y * 100, 4) -> ROUND(CAST(x AS DOUBLE) / y * 100, 4)
--   BUG 11: TRANSIT_DAY_KEY -> does not exist in ABP_TAP;
--           transit_day derived as DATE(TRANSACTION_DTM)
--   BUG 12: t.DEVICE_KEY -> does not exist in ABP_TAP; removed from GROUP BY and SELECT
--   BUG 13: t.MEDIA_ID -> t.TOKEN_ID
--   BUG 14: t.TRANSACTION_STATUS_ID -> t.TAP_STATUS_ID
--   BUG 15: t.AMOUNT -> t.FARE_DUE
--   BUG 16: t.TAP_DTM -> t.TRANSACTION_DTM
--   BUG 17: dd.TRANSIT_MODE_NAME -> not in device_dimension; removed
--   BUG 18: dd.BUS_ID -> t.BUS_ID (exists in ABP_TAP; not in dim_device)
--   BUG 19: t.FACILITY_ID -> does not exist in ABP_TAP; sourced from dim_device join
--   BUG 20: mars_device_category -> not in bronze device_dimension parquet;
--           available after S06 creates mars_dev.silver.dim_device
--   BUG 21: TAP_STATUS_ID = 0 -> wrong approved code (0 never appears in data);
--           approved = IN (1, 900, 904); rejected = NOT IN (1, 900, 904)
-- =============================================================================

DROP TABLE IF EXISTS mars_dev.silver.tap_event_daily;

CREATE TABLE mars_dev.silver.tap_event_daily AS
WITH tap_agg AS (
    -- Grain: (DEVICE_ID, transit_day, OPERATOR_ID, BUS_ID)
    -- ABP_TAP is partitioned by EDW load date - filter on TRANSACTION_DTM for tap date
    SELECT
        t.DEVICE_ID,
        DATE(t.TRANSACTION_DTM)                                AS transit_day,
        t.OPERATOR_ID,
        t.BUS_ID,
        COUNT(*)                                               AS tap_count,
        COUNT(DISTINCT t.TOKEN_ID)                            AS unique_cards,
        -- Approved: Device Approved (1), Server Approved (900), Server Approved Cached (904)
        SUM(CASE WHEN t.TAP_STATUS_ID IN (1, 900, 904) THEN 1 ELSE 0 END)
                                                               AS tap_approved_count,
        SUM(CASE WHEN t.TAP_STATUS_ID NOT IN (1, 900, 904) THEN 1 ELSE 0 END)
                                                               AS tap_reject_count,
        SUM(COALESCE(t.FARE_DUE, 0))                          AS total_fare,
        AVG(COALESCE(t.FARE_DUE, 0))                          AS avg_fare,
        MIN(t.TRANSACTION_DTM)                                AS first_tap_dtm,
        MAX(t.TRANSACTION_DTM)                                AS last_tap_dtm
    FROM mars_dev.bronze.edw_abp_tap t
    GROUP BY t.DEVICE_ID, DATE(t.TRANSACTION_DTM), t.OPERATOR_ID, t.BUS_ID
),
peak_hour_agg AS (
    -- Replaces unsupported LEFT JOIN LATERAL: compute hourly counts then take MAX
    SELECT
        DEVICE_ID,
        transit_day,
        MAX(hour_taps)     AS peak_hour_tap_count
    FROM (
        SELECT
            DEVICE_ID,
            DATE(TRANSACTION_DTM) AS transit_day,
            HOUR(TRANSACTION_DTM) AS tap_hour,
            COUNT(*)              AS hour_taps
        FROM mars_dev.bronze.edw_abp_tap
        GROUP BY DEVICE_ID, DATE(TRANSACTION_DTM), HOUR(TRANSACTION_DTM)
    )
    GROUP BY DEVICE_ID, transit_day
)
SELECT
    ta.DEVICE_ID,
    ta.transit_day,
    ta.OPERATOR_ID,
    ta.BUS_ID,
    ta.tap_count,
    ta.unique_cards,
    ta.tap_approved_count,
    ta.tap_reject_count,
    CASE
        WHEN ta.tap_count > 0
        THEN ROUND(CAST(ta.tap_reject_count AS DOUBLE) / ta.tap_count * 100, 4)
        ELSE 0
    END                                          AS tap_reject_rate_pct,
    ta.total_fare,
    ta.avg_fare,
    ph.peak_hour_tap_count,
    ta.first_tap_dtm,
    ta.last_tap_dtm,
    -- Device enrichment (FACILITY_ID not in ABP_TAP; from dim_device)
    dd.FACILITY_ID,
    dd.DEVICE_NAME,
    dd.DEVICE_TYPE_NAME,
    dd.mars_device_category,
    dd.FACILITY_NAME,
    dd.OPERATOR_NAME

FROM tap_agg ta
LEFT JOIN peak_hour_agg ph
    ON ph.DEVICE_ID   = ta.DEVICE_ID
   AND ph.transit_day = ta.transit_day
LEFT JOIN mars_dev.silver.dim_device dd
    ON dd.DEVICE_ID   = ta.DEVICE_ID
   AND dd.is_current  = TRUE;

-- Post-load optimisation:
-- OPTIMIZE mars_dev.silver.tap_event_daily ZORDER BY (DEVICE_ID, transit_day);
