-- =============================================================================
-- silver.device_mttr  (S28)
-- Rolling mean time to repair (MTTR) per device, per failure day.
-- Fills the VALIDATOR gap in S24 device_incident_features_daily (which has 0%
-- BMV coverage from ServiceNow). Provides unified MTTR for all device categories.
--
-- Source:
--   S26 mars_dev.silver.device_failures   (device-day failures with downtime_minutes)
--
-- Grain: (DEVICE_KEY, failure_date) — one row per device per calendar day
--   where the device had at least one hardware failure. Days with no failures
--   have no row; join to a dense device-day spine with COALESCE(x, NULL) downstream.
--
-- Key columns:
--   downtime_minutes        : total downtime on this failure day (from S26)
--   avg_downtime_30d        : trailing 30-day mean downtime per failure day (MTTR proxy)
--   avg_downtime_90d        : trailing 90-day MTTR proxy
--   failure_days_30d        : count of failure days in past 30 calendar days
--   failure_days_90d        : count in past 90 calendar days
--   days_since_prev_failure : calendar days between this failure and the previous one
--                             (NULL for first failure; high value = long healthy run)
--   failure_source          : 'availability_events' | 'device_event_enriched'
--
-- Notes:
--   - RANGE INTERVAL windows count calendar days, not row positions.
--     For sparse failure histories, this matters — ROWS BETWEEN would include
--     failures from much earlier periods.
--     Databricks requires TIMESTAMP (not DATE) for INTERVAL RANGE ORDER BY:
--     failure_date is CAST to TIMESTAMP in window ORDER BY; all other refs stay DATE.
--   - downtime_minutes for VALIDATOR (BMV) = duration_to_clear_min from device_event_enriched
--     (time from OOS Set to Clear). May be 0 when CLEAR_DTM is absent.
--   - MTTR in ServiceNow (S24) covers TVM 17.75%, GATE 3.33%, VALIDATOR 0%.
--     This table covers all categories at 100% of failure events.
--
-- Build order: S26 (device_failures) -> S28 (this table)
-- Feeds:       gold.device_ps1_daily  (avg_downtime_30d, failure_days_30d as features)
--              gold.device_ps5_component  (device reliability context for replacement model)
--
-- Validation:
--   SELECT device_category,
--          COUNT(DISTINCT DEVICE_KEY)    AS devices_with_mttr,
--          ROUND(AVG(avg_downtime_30d),1) AS p50_mttr_30d_min,
--          ROUND(AVG(failure_days_30d),2) AS avg_fail_days_per_month
--   FROM mars_dev.silver.device_mttr
--   GROUP BY device_category ORDER BY 1;
-- =============================================================================

DROP TABLE IF EXISTS mars_dev.silver.device_mttr;

CREATE TABLE mars_dev.silver.device_mttr
USING DELTA
TBLPROPERTIES ('delta.autoOptimize.optimizeWrite' = 'true')
COMMENT 'Rolling MTTR per device-failure-day. Covers all categories (TVM/GATE from availability_events, VALIDATOR from device_event_enriched). Fills VALIDATOR gap in S24.'
AS
SELECT
    df.DEVICE_KEY,
    df.DEVICE_ID,
    df.device_category,
    df.failure_date,
    df.downtime_minutes,
    df.failure_source,

    -- Rolling MTTR: average downtime per failure day over trailing calendar windows
    -- RANGE INTERVAL used so that gaps in failure history are respected as calendar days.
    ROUND(
        AVG(df.downtime_minutes) OVER (
            PARTITION BY df.DEVICE_KEY
            ORDER BY CAST(df.failure_date AS TIMESTAMP)
            RANGE BETWEEN INTERVAL 29 DAYS PRECEDING AND CURRENT ROW
        ), 2
    )                                                           AS avg_downtime_30d,

    ROUND(
        AVG(df.downtime_minutes) OVER (
            PARTITION BY df.DEVICE_KEY
            ORDER BY CAST(df.failure_date AS TIMESTAMP)
            RANGE BETWEEN INTERVAL 89 DAYS PRECEDING AND CURRENT ROW
        ), 2
    )                                                           AS avg_downtime_90d,

    -- Failure frequency: count of failure days in trailing windows
    COUNT(*) OVER (
        PARTITION BY df.DEVICE_KEY
        ORDER BY CAST(df.failure_date AS TIMESTAMP)
        RANGE BETWEEN INTERVAL 29 DAYS PRECEDING AND CURRENT ROW
    )                                                           AS failure_days_30d,

    COUNT(*) OVER (
        PARTITION BY df.DEVICE_KEY
        ORDER BY CAST(df.failure_date AS TIMESTAMP)
        RANGE BETWEEN INTERVAL 89 DAYS PRECEDING AND CURRENT ROW
    )                                                           AS failure_days_90d,

    -- Gap analysis: days between this failure and the previous failure
    -- NULL = first recorded failure for this device (no prior history)
    -- High value = long healthy run before this failure (could indicate sudden degradation)
    DATEDIFF(
        df.failure_date,
        LAG(df.failure_date) OVER (
            PARTITION BY df.DEVICE_KEY ORDER BY df.failure_date
        )
    )                                                           AS days_since_prev_failure,

    -- Max single-failure downtime in past 30 days (outlier / severe failure signal)
    MAX(df.downtime_minutes) OVER (
        PARTITION BY df.DEVICE_KEY
        ORDER BY CAST(df.failure_date AS TIMESTAMP)
        RANGE BETWEEN INTERVAL 29 DAYS PRECEDING AND CURRENT ROW
    )                                                           AS max_downtime_30d

FROM mars_dev.silver.device_failures df;

-- Post-load optimisation:
-- OPTIMIZE mars_dev.silver.device_mttr ZORDER BY (DEVICE_KEY, failure_date);

-- Post-load validation:
-- SELECT device_category,
--        COUNT(DISTINCT DEVICE_KEY)        AS devices,
--        COUNT(*)                          AS total_failure_days,
--        ROUND(AVG(downtime_minutes), 1)   AS avg_downtime_min,
--        ROUND(AVG(avg_downtime_30d), 1)   AS avg_mttr_30d,
--        ROUND(AVG(failure_days_30d), 2)   AS avg_fail_days_per_month
-- FROM mars_dev.silver.device_mttr
-- GROUP BY device_category ORDER BY 1;
