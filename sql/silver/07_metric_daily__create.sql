-- =============================================================================
-- silver.metric_daily
-- Daily aggregated device transaction timing metrics
--
-- Sources (mars_dev.bronze catalog):
--   EDW.DEVICE_METRIC    (592M rows, 19 cols) — one row per transaction event
--     Partitioned: year/month/day
--     Key cols: DEVICE_KEY, TRANSIT_DAY_KEY (YYYYMMDD), METRIC_KEY, METRIC_VALUE
--   EDW.METRIC_DIMENSION (62 rows,   8 cols)  — metric ID/name lookup
--
-- Chicago data validation 2026-06-15:
--   Only METRIC_ID=401 ("Transaction Time", MILLI) has data in DEVICE_METRIC.
--   METRIC_IDs 800 and 810 return 0 rows for Chicago — confirmed absent.
--   METRIC_SUMMARY_BY_DAY removed from bronze pipeline (June 2026 cleanup).
--
-- METRIC_ID 401 = Transaction Time (milliseconds)
--   "Transaction Time for requests for payment of Transit Fares initiated at
--    Fare Gate or bus Reader Assembly"
--   — Each DEVICE_METRIC row = one card tap event
--   — METRIC_VALUE = processing duration in milliseconds
--   — Sample week (2025-01-01–07): avg 574.5ms, max 15,260ms (PS4 anomaly signal)
--   — 2,794 devices, ~127 taps/device/day
--
-- Derived daily columns:
--   m401_daily_txn_count  — total taps recorded (COUNT(*) per device per day)
--   m401_avg_txn_time_ms  — average processing time
--   m401_max_txn_time_ms  — slowest tap (outlier / degradation signal for PS4)
-- Delta columns (LAG over DEVICE_KEY ORDER BY transit_day):
--   m401_txn_count_delta  — day-over-day volume change
--   m401_avg_time_delta_ms — day-over-day performance change (+ = slower)
-- volume_drop_flag: today < yesterday (possible downtime or reduced service)
--
-- Dimension joined:
--   mars_dev.silver.dim_device (S01)
--   NOTE: DEVICE_METRIC has DEVICE_KEY but no DEVICE_ID.
--         DEVICE_KEY join to dim_device may miss historical SCD2 keys.
--
-- Validation run 2026-06-15 — bugs fixed from original:
--   BUG 1: bronze.metric_summary_by_day → PATH NOT FOUND (pipeline cleanup)
--           → rewritten to use bronze.device_metric
--   BUG 2: Pivot for METRIC_IDs 800 and 810 → 0 rows for Chicago; removed
--   BUG 3: All metric column names were wrong (descriptions mismatched IDs)
--   BUG 4: SUM(METRIC_VALUE) for time metric → AVG+MAX (each row = one transaction)
--   BUG 5: bronze.metric_dimension  → parquet S3 path
--   BUG 6: silver.dim_device        → mars_dev.silver.dim_device
--   BUG 7: silver.metric_daily      → mars_dev.silver.metric_daily
--   BUG 8: TRANSIT_DAY_KEY::text    → TO_DATE(CAST(... AS STRING), 'yyyyMMdd')
--   BUG 9: METRIC_UNITS             → METRIC_UNIT
--   BUG 10: CREATE INDEX            → not supported on Delta; use OPTIMIZE/ZORDER
--            Recommended: OPTIMIZE mars_dev.silver.metric_daily
--                           ZORDER BY (DEVICE_KEY, transit_day);
-- BUG F: Future TRANSIT_DAY_KEY values (up to 2028-05-13) found in device_metric bronze
--         Pre-build dry-run (2026-06-18): latest_day=2028-05-13 (data entry errors in Oracle ODS).
--         Fix: CURRENT_DATE() guard in day_grain WHERE clause filters future rows.
--
-- NOTE:  dim_device match rate = 99.88% (6,724 of 6,732 distinct DEVICE_KEYs matched).
--        Post-validation 2026-06-19 confirmed: pre-build estimate of 41.4% was incorrect.
--        Chicago DEVICE_METRIC contains VALIDATOR + GATE keys only; both are fully covered
--        by current dim_device. Only 8 DEVICE_KEYs (44 rows, 0.12%) have no dim match.
--        All device enrichment columns (DEVICE_ID, DEVICE_NAME, etc.) are populated for
--        99.88% of rows — no imputation needed for downstream PS4/PS5 pipelines.
--
-- NOTE:  Data gap confirmed post-validation 2026-06-19: latest_day = 2025-11-07.
--        DEVICE_METRIC bronze ingestion appears stalled after Nov 2025 (7+ month gap).
--        Impact: PS1 metric features NULL for Nov 2025 → present; PS4 Signal 2 no data.
--        Action: raise with Viren (infra) to investigate bronze pipeline ingestion.
--
-- NOTE:  global_max_txn_ms = 113,462ms (113 seconds) detected in full-dataset scan.
--        SQL comment estimated max ~15,260ms from a 1-week sample (2025-01-01 to 07).
--        These extreme values are legitimate PS4 anomaly candidates — no cap applied.
-- =============================================================================

DROP TABLE IF EXISTS mars_dev.silver.metric_daily;

CREATE TABLE mars_dev.silver.metric_daily AS
WITH day_grain AS (
    SELECT
        dm.DEVICE_KEY,
        dm.TRANSIT_DAY_KEY,
        dm.FACILITY_ID,
        COUNT(*)                    AS m401_daily_txn_count,
        AVG(dm.METRIC_VALUE)        AS m401_avg_txn_time_ms,
        MAX(dm.METRIC_VALUE)        AS m401_max_txn_time_ms
    FROM mars_dev.bronze.edw_device_metric dm
    WHERE dm.METRIC_KEY = (
        SELECT METRIC_KEY FROM mars_dev.bronze.edw_metric_dimension
        WHERE METRIC_ID = 401
        LIMIT 1
    )
      AND TO_DATE(CAST(dm.TRANSIT_DAY_KEY AS STRING), 'yyyyMMdd') <= CURRENT_DATE()
    GROUP BY dm.DEVICE_KEY, dm.TRANSIT_DAY_KEY, dm.FACILITY_ID
),
with_day AS (
    SELECT
        dg.*,
        TO_DATE(CAST(dg.TRANSIT_DAY_KEY AS STRING), 'yyyyMMdd') AS transit_day
    FROM day_grain dg
),
with_delta AS (
    SELECT
        wd.*,
        wd.m401_daily_txn_count
          - LAG(wd.m401_daily_txn_count, 1, 0) OVER (
                PARTITION BY wd.DEVICE_KEY ORDER BY wd.transit_day
            )                                               AS m401_txn_count_delta,
        wd.m401_avg_txn_time_ms
          - LAG(wd.m401_avg_txn_time_ms) OVER (
                PARTITION BY wd.DEVICE_KEY ORDER BY wd.transit_day
            )                                               AS m401_avg_time_delta_ms,
        LAG(wd.m401_daily_txn_count) OVER (
            PARTITION BY wd.DEVICE_KEY ORDER BY wd.transit_day
        )                                                   AS prev_m401_txn_count
    FROM with_day wd
)
SELECT
    wd.DEVICE_KEY,
    wd.TRANSIT_DAY_KEY,
    wd.transit_day,
    wd.FACILITY_ID,
    dd.DEVICE_ID,
    dd.DEVICE_NAME,
    dd.DEVICE_TYPE_NAME,
    dd.mars_device_category,
    dd.FACILITY_NAME,
    dd.OPERATOR_ID,
    dd.OPERATOR_NAME,
    wd.m401_daily_txn_count,
    wd.m401_avg_txn_time_ms,
    wd.m401_max_txn_time_ms,
    wd.m401_txn_count_delta,
    wd.m401_avg_time_delta_ms,
    (wd.prev_m401_txn_count IS NOT NULL
     AND wd.m401_daily_txn_count < wd.prev_m401_txn_count) AS volume_drop_flag

FROM with_delta wd
LEFT JOIN mars_dev.silver.dim_device dd
    ON dd.DEVICE_KEY = wd.DEVICE_KEY;

-- Post-load optimisation:
-- OPTIMIZE mars_dev.silver.metric_daily ZORDER BY (DEVICE_KEY, transit_day);
