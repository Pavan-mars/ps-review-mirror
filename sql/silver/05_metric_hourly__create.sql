-- =============================================================================
-- silver.metric_hourly  (S05)
-- Hourly aggregated device transaction timing metrics - METRIC_ID=401 only
-- Grain: (DEVICE_KEY, hour_bucket = DATE_TRUNC('HOUR', TIME_INCREMENT_KEY))
--
-- Source (mars_dev.bronze catalog - same tables as S10 metric_daily):
--   EDW.DEVICE_METRIC    (592M rows, 19 cols) - one row per card-tap event
--     Key cols: DEVICE_KEY, TRANSIT_DAY_KEY (YYYYMMDD int), TIME_INCREMENT_KEY (HHMM int),
--               METRIC_KEY (FK to METRIC_DIMENSION), METRIC_VALUE (ms)
--   EDW.METRIC_DIMENSION (62 rows) - metric ID/name lookup
--
-- Governance note (2026-06-23):
--   Updated to read from mars_dev.bronze.edw_device_metric catalog table (same as S10).
--   Removed direct S3 parquet reads - all sources now go through governed bronze Delta tables.
--   This silver table was created to fix the gold-layer governance violation in PS4, which
--   was reading S3 parquet directly from within the gold DDL.
--
-- Why not reuse S10 metric_daily?
--   S10 collapses TIME_INCREMENT_KEY to day grain - hourly bucket cannot be reconstructed.
--   PS4 anomaly detection (Signal 2) requires hour-bucket grain for EWMA baseline.
--
-- Chicago data facts (validated 2026-06-15, confirmed 2026-06-22):
--   Only METRIC_ID=401 ("Transaction Time", MILLI) has Chicago data.
--   METRIC_IDs 800 and 810 = 0 rows (other-tenant firmware codes).
--   ~2,794 distinct DEVICE_KEYs (VALIDATOR + GATE only).
--   TIME_INCREMENT_KEY stored as integer (e.g. 900 instead of '0900') - LPAD to 4 chars needed.
--   Future TRANSIT_DAY_KEY values present (up to 2028) - CURRENT_DATE() guard required.
--
-- Produced columns:
--   metric_401_tap_count_hour - card taps recorded in the hour window
--   metric_401_avg_ms_hour    - avg processing time ms (PS4 Signal 2 baseline)
--   metric_401_max_ms_hour    - slowest tap in hour (outlier / degradation signal)
--
-- Consumed by:
--   G04  mars_dev.gold.device_ps4_hourly  (metric_hourly CTE -> metric_baseline CTE -> Signal 2)
-- =============================================================================

DROP TABLE IF EXISTS mars_dev.silver.metric_hourly;

CREATE TABLE mars_dev.silver.metric_hourly AS
SELECT
    dm.DEVICE_KEY,
    -- TIME_INCREMENT_KEY stored as integer (e.g. 900 -> '0900'); LPAD handles this
    DATE_TRUNC('HOUR',
        TIMESTAMPADD(HOUR,
            CAST(LEFT(LPAD(CAST(dm.TIME_INCREMENT_KEY AS STRING), 4, '0'), 2) AS INT),
            CAST(TO_DATE(CAST(dm.TRANSIT_DAY_KEY AS STRING), 'yyyyMMdd') AS TIMESTAMP)
        )
    )                                           AS hour_bucket,
    TO_DATE(CAST(dm.TRANSIT_DAY_KEY AS STRING), 'yyyyMMdd')
                                                AS transit_day,
    COUNT(*)                                    AS metric_401_tap_count_hour,
    AVG(dm.METRIC_VALUE)                        AS metric_401_avg_ms_hour,
    MAX(dm.METRIC_VALUE)                        AS metric_401_max_ms_hour
FROM mars_dev.bronze.edw_device_metric dm
WHERE dm.METRIC_KEY = (
    SELECT METRIC_KEY
    FROM mars_dev.bronze.edw_metric_dimension
    WHERE METRIC_ID = 401
    LIMIT 1
)
AND TO_DATE(CAST(dm.TRANSIT_DAY_KEY AS STRING), 'yyyyMMdd') >= '2024-01-01'
AND TO_DATE(CAST(dm.TRANSIT_DAY_KEY AS STRING), 'yyyyMMdd') <= CURRENT_DATE()
GROUP BY
    dm.DEVICE_KEY,
    DATE_TRUNC('HOUR',
        TIMESTAMPADD(HOUR,
            CAST(LEFT(LPAD(CAST(dm.TIME_INCREMENT_KEY AS STRING), 4, '0'), 2) AS INT),
            CAST(TO_DATE(CAST(dm.TRANSIT_DAY_KEY AS STRING), 'yyyyMMdd') AS TIMESTAMP)
        )
    ),
    TO_DATE(CAST(dm.TRANSIT_DAY_KEY AS STRING), 'yyyyMMdd');

-- Post-build:
-- OPTIMIZE mars_dev.silver.metric_hourly ZORDER BY (DEVICE_KEY, hour_bucket);
--
-- Post-build verification:
-- SELECT
--     COUNT(*)                               AS total_rows,
--     COUNT(DISTINCT DEVICE_KEY)             AS distinct_devices,
--     MIN(transit_day)                       AS earliest_day,
--     MAX(transit_day)                       AS latest_day,
--     ROUND(AVG(metric_401_avg_ms_hour), 1)  AS avg_ms,
--     MAX(metric_401_max_ms_hour)            AS peak_ms,
--     COUNT(DISTINCT DATE_FORMAT(hour_bucket, 'HH')) AS distinct_hours
-- FROM mars_dev.silver.metric_hourly;
-- Expected: ~2,794 distinct DEVICE_KEYs, avg ~574ms, peak ~15,260ms, 24 distinct hours
