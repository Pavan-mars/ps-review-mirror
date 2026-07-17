-- =============================================================================
-- silver.metric_daily  (S10)  v2 - 2026-07-17
-- Daily device tap-timing metrics (M401) + reader/comms event counts
--
-- Sources:
--   bronze.edw_device_metric     (592M rows) -- one row per card-tap event
--   bronze.edw_metric_dimension  (62 rows)   -- metric ID/name lookup
--   silver.device_event_enriched (S16)       -- reader/comms events per device
--   silver.dim_device            (S06)       -- device enrichment
--
-- METRIC_ID 401 = "Transaction Time" (milliseconds)
--   One DEVICE_METRIC row = one card tap. METRIC_VALUE = processing duration ms.
--   Sample stats: avg 574.5ms, max 113,462ms (PS4 anomaly signal).
--   Chicago coverage: GATE + VALIDATOR only (see TVM note below).
--
-- Confirmed absent — permanent source gaps (Oracle ODS / Chicago tenant):
--   METRIC_ID 401 / TVM devices : 0 rows in DEVICE_METRIC for Chicago TVM keys.
--                                  TVM rows in this table come from comms events only;
--                                  all m401_* columns are NULL for TVM.
--   METRIC_IDs 701-705           : reader-leg timing not recorded at source for Chicago.
--                                  M401 + reader/comms events are the substitute signals.
--   METRIC_IDs 800, 810          : other-tenant firmware codes; 0 Chicago rows.
--
-- Bronze pipeline note (confirmed 2026-06-19, stall continues as of 2026-07-17):
--   DEVICE_METRIC ingestion stalled after 2025-11-07 (7+ month gap).
--   M401 features will be NULL for all devices after 2025-11-07 until pipeline restored.
--   Reader/comms events from S16 are unaffected (separate pipeline).
--   Action: raise with Viren (infra) to investigate bronze pipeline ingestion.
--
-- Spine design:
--   M401 active days (GATE + VALIDATOR) UNION comms-event active days (all categories).
--   TVM rows appear only from comms events; m401_* columns are NULL for those rows.
--
-- New in v2 (2026-07-17):
--   m401_p95_txn_time_ms, m401_p99_txn_time_ms
--   m401_slow_tap_count, m401_slow_tap_pct  (slow = >1000ms)
--   m401_rolling_7d_avg_ms
--   m401_z_score_vs_28d                     (PS4 anomaly signal)
--   comms_csc_read_err_count, comms_host_comm_lost_count, comms_device_comms_lost_count
--   comms_total_count, comms_event_flag
--
-- Device coverage (dim_device is_current=TRUE, validated 2026-07-17):
--   GATE 1,382  |  TVM 1,019  |  VALIDATOR 4,218
--   M401 dim_device match rate: 99.88% (8 DEVICE_KEYs / 44 rows unmatched).
--
-- Grain: (DEVICE_KEY, transit_day)
-- Feeds: gold.device_ps1_daily  (tap-timing + comms features)
--        gold.device_ps4_hourly (metric_hourly S05 is primary hourly grain)
--        gold.device_ps5_component (comms reliability features)
-- =============================================================================

DROP TABLE IF EXISTS mars_dev.silver.metric_daily;

CREATE TABLE mars_dev.silver.metric_daily AS
WITH

-- ── M401 per-device per-day aggregation ──────────────────────────────────────
m401_grain AS (
    SELECT
        dm.DEVICE_KEY,
        dm.TRANSIT_DAY_KEY,
        TO_DATE(CAST(dm.TRANSIT_DAY_KEY AS STRING), 'yyyyMMdd')         AS transit_day,
        MAX(dm.FACILITY_ID)                                               AS FACILITY_ID,
        COUNT(*)                                                           AS m401_daily_txn_count,
        AVG(dm.METRIC_VALUE)                                              AS m401_avg_txn_time_ms,
        MAX(dm.METRIC_VALUE)                                              AS m401_max_txn_time_ms,
        PERCENTILE_APPROX(dm.METRIC_VALUE, 0.95)                         AS m401_p95_txn_time_ms,
        PERCENTILE_APPROX(dm.METRIC_VALUE, 0.99)                         AS m401_p99_txn_time_ms,
        SUM(CASE WHEN dm.METRIC_VALUE > 1000 THEN 1 ELSE 0 END)         AS m401_slow_tap_count
    FROM mars_dev.bronze.edw_device_metric dm
    WHERE dm.METRIC_KEY = (
        SELECT METRIC_KEY FROM mars_dev.bronze.edw_metric_dimension
        WHERE METRIC_ID = 401
        LIMIT 1
    )
      AND TO_DATE(CAST(dm.TRANSIT_DAY_KEY AS STRING), 'yyyyMMdd') <= CURRENT_DATE()
    GROUP BY dm.DEVICE_KEY, dm.TRANSIT_DAY_KEY
),

-- ── Reader/comms events per device per day (from S16) ────────────────────────
-- Event names confirmed from dim_event_type on 2026-07-17:
--   'CSC Read err'       -- card-reader read failure (GATE 21.9M, VALIDATOR 17.4M, TVM 1.1M)
--   'Host Comm Lost'     -- AFC host communication lost (VALIDATOR 10.7M, GATE 2.0M, TVM 75K)
--   'DeviceCommsLost'    -- device-level comms lost (VALIDATOR 8.4M, GATE 117K, TVM 46K)
comms_grain AS (
    SELECT
        dee.DEVICE_KEY,
        CAST(dee.EVENT_DTM AS DATE)                                        AS transit_day,
        SUM(CASE WHEN upper(det.EVENT_TYPE_NAME) LIKE '%CSC READ ERR%'    THEN 1 ELSE 0 END) AS comms_csc_read_err_count,
        SUM(CASE WHEN upper(det.EVENT_TYPE_NAME) LIKE '%HOST COMM LOST%'  THEN 1 ELSE 0 END) AS comms_host_comm_lost_count,
        SUM(CASE WHEN upper(det.EVENT_TYPE_NAME) LIKE '%DEVICECOMMSLOST%'
                  OR upper(det.EVENT_TYPE_NAME) LIKE '%DEVICE COMMS LOST%'
                                                                           THEN 1 ELSE 0 END) AS comms_device_comms_lost_count
    FROM mars_dev.silver.device_event_enriched dee
    JOIN mars_dev.silver.dim_event_type det
        ON det.EVENT_TYPE_KEY = dee.EVENT_TYPE_KEY
    WHERE upper(det.EVENT_TYPE_NAME) RLIKE
        '(CSC READ ERR|HOST COMM LOST|DEVICECOMMSLOST|DEVICE COMMS LOST)'
    GROUP BY dee.DEVICE_KEY, CAST(dee.EVENT_DTM AS DATE)
),

-- ── Unified spine: M401 days UNION comms-event days (includes TVM) ───────────
spine AS (
    SELECT DEVICE_KEY, transit_day,
           TRANSIT_DAY_KEY
    FROM m401_grain
    UNION
    SELECT DEVICE_KEY, transit_day,
           CAST(DATE_FORMAT(transit_day, 'yyyyMMdd') AS INT) AS TRANSIT_DAY_KEY
    FROM comms_grain
),

-- ── Join M401 + comms onto unified spine ──────────────────────────────────────
joined AS (
    SELECT
        s.DEVICE_KEY,
        s.TRANSIT_DAY_KEY,
        s.transit_day,
        COALESCE(m.FACILITY_ID, 0)                                            AS FACILITY_ID,
        -- M401 tap-timing (NULL for TVM -- no Chicago DEVICE_METRIC data)
        m.m401_daily_txn_count,
        m.m401_avg_txn_time_ms,
        m.m401_max_txn_time_ms,
        m.m401_p95_txn_time_ms,
        m.m401_p99_txn_time_ms,
        m.m401_slow_tap_count,
        ROUND(100.0 * m.m401_slow_tap_count / NULLIF(m.m401_daily_txn_count, 0), 2) AS m401_slow_tap_pct,
        -- Reader/comms events (all categories)
        COALESCE(c.comms_csc_read_err_count,      0)                         AS comms_csc_read_err_count,
        COALESCE(c.comms_host_comm_lost_count,    0)                         AS comms_host_comm_lost_count,
        COALESCE(c.comms_device_comms_lost_count, 0)                         AS comms_device_comms_lost_count
    FROM spine s
    LEFT JOIN m401_grain m
        ON  m.DEVICE_KEY  = s.DEVICE_KEY
        AND m.transit_day = s.transit_day
    LEFT JOIN comms_grain c
        ON  c.DEVICE_KEY  = s.DEVICE_KEY
        AND c.transit_day = s.transit_day
),

-- ── Rolling window features (day-over-day, 7d trend, 28d z-score baseline) ───
with_rolling AS (
    SELECT
        j.*,
        -- Day-over-day volume and timing deltas
        j.m401_daily_txn_count
          - LAG(j.m401_daily_txn_count, 1, 0) OVER (
                PARTITION BY j.DEVICE_KEY ORDER BY j.transit_day
            )                                                                  AS m401_txn_count_delta,
        j.m401_avg_txn_time_ms
          - LAG(j.m401_avg_txn_time_ms) OVER (
                PARTITION BY j.DEVICE_KEY ORDER BY j.transit_day
            )                                                                  AS m401_avg_time_delta_ms,
        LAG(j.m401_daily_txn_count) OVER (
            PARTITION BY j.DEVICE_KEY ORDER BY j.transit_day
        )                                                                      AS prev_m401_txn_count,
        -- Rolling 7-day average processing time (trend baseline)
        AVG(j.m401_avg_txn_time_ms) OVER (
            PARTITION BY j.DEVICE_KEY ORDER BY j.transit_day
            ROWS BETWEEN 6 PRECEDING AND CURRENT ROW
        )                                                                      AS m401_rolling_7d_avg_ms,
        -- 28-day look-back baseline for z-score (excludes current day — no leakage)
        AVG(j.m401_avg_txn_time_ms) OVER (
            PARTITION BY j.DEVICE_KEY ORDER BY j.transit_day
            ROWS BETWEEN 28 PRECEDING AND 1 PRECEDING
        )                                                                      AS m401_baseline_28d_avg_ms,
        STDDEV_SAMP(j.m401_avg_txn_time_ms) OVER (
            PARTITION BY j.DEVICE_KEY ORDER BY j.transit_day
            ROWS BETWEEN 28 PRECEDING AND 1 PRECEDING
        )                                                                      AS m401_baseline_28d_std_ms
    FROM joined j
)

SELECT
    wr.DEVICE_KEY,
    wr.TRANSIT_DAY_KEY,
    wr.transit_day,
    wr.FACILITY_ID,
    dd.DEVICE_ID,
    dd.DEVICE_NAME,
    dd.DEVICE_TYPE_NAME,
    dd.mars_device_category,
    dd.FACILITY_NAME,
    dd.OPERATOR_ID,
    dd.OPERATOR_NAME,

    -- ── M401 tap-timing features (NULL for TVM — no Chicago DEVICE_METRIC data) ─
    wr.m401_daily_txn_count,
    wr.m401_avg_txn_time_ms,
    wr.m401_max_txn_time_ms,
    wr.m401_p95_txn_time_ms,
    wr.m401_p99_txn_time_ms,
    wr.m401_slow_tap_count,
    wr.m401_slow_tap_pct,
    wr.m401_txn_count_delta,
    wr.m401_avg_time_delta_ms,
    wr.m401_rolling_7d_avg_ms,
    -- z-score: standard deviations above/below 28-day personal baseline (PS4 anomaly)
    CASE WHEN wr.m401_baseline_28d_std_ms > 0
         THEN ROUND(
                (wr.m401_avg_txn_time_ms - wr.m401_baseline_28d_avg_ms)
                / wr.m401_baseline_28d_std_ms, 3)
         ELSE NULL
    END                                                                        AS m401_z_score_vs_28d,
    -- volume drop: today's tap count fell below yesterday's (downtime or reduced service)
    (wr.prev_m401_txn_count IS NOT NULL
     AND wr.m401_daily_txn_count < wr.prev_m401_txn_count)                   AS volume_drop_flag,

    -- ── Reader/comms event features (all categories incl. TVM) ──────────────
    wr.comms_csc_read_err_count,
    wr.comms_host_comm_lost_count,
    wr.comms_device_comms_lost_count,
    wr.comms_csc_read_err_count
      + wr.comms_host_comm_lost_count
      + wr.comms_device_comms_lost_count                                       AS comms_total_count,
    (wr.comms_csc_read_err_count
      + wr.comms_host_comm_lost_count
      + wr.comms_device_comms_lost_count) > 0                                 AS comms_event_flag

FROM with_rolling wr
LEFT JOIN mars_dev.silver.dim_device dd
    ON  dd.DEVICE_KEY  = wr.DEVICE_KEY
    AND dd.is_current  = TRUE;

-- Post-load optimisation:
-- OPTIMIZE mars_dev.silver.metric_daily ZORDER BY (DEVICE_KEY, transit_day);

-- Post-load validation:
-- SELECT mars_device_category,
--        COUNT(DISTINCT DEVICE_KEY)            AS distinct_devices,
--        COUNT(*)                               AS device_day_rows,
--        MIN(transit_day)                       AS earliest,
--        MAX(transit_day)                       AS latest,
--        SUM(CAST(m401_daily_txn_count IS NULL AS INT)) AS tvm_null_m401_rows,
--        ROUND(AVG(m401_avg_txn_time_ms), 1)   AS mean_avg_ms,
--        ROUND(AVG(comms_total_count), 2)       AS avg_comms_events_per_day
-- FROM mars_dev.silver.metric_daily
-- GROUP BY mars_device_category ORDER BY 1;
