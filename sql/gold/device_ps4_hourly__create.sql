-- =============================================================================
-- gold.device_ps4_hourly
-- PS4 — Anomaly Detection: hourly feature table for ALL device types
-- Grain: (device_id, hour_bucket = DATE_TRUNC('HOUR', EVENT_DTM))
-- Target: ensemble_anomaly_flag (>= 2 of 3 signals active)
-- Device types: TVM, GATE, VALIDATOR  (READER removed 2026-06-23 — reader is a component, not device category)
-- Sources (silver):
--   S01  mars_dev.silver.dim_device
--   S03  mars_dev.silver.device_event_enriched  (has hour_bucket column, S03:L159)
--   S10  mars_dev.silver.tap_event_daily
-- Sources (silver):
--   S01  mars_dev.silver.dim_device
--   S03  mars_dev.silver.device_event_enriched  (has hour_bucket column, S03:L159)
--   S10  mars_dev.silver.tap_event_daily
--   S14  mars_dev.silver.metric_hourly  (METRIC_ID=401 hourly tap timing — was direct S3 parquet, fixed 2026-06-22)
-- bus_realtime_daily JOIN REMOVED (CTA_REAL_TIME_BUS_DATA dropped 2026-06-12)
--
-- Anomaly signals (3 → ensemble):
--   Signal 1: event_rate_anomaly  — hourly events > 2*stddev of same-hour baseline
--   Signal 2: metric_anomaly      — METRIC_ID 401 avg_ms deviates > 2*stddev from baseline
--             ⚠️  METRIC_IDs 800 and 810 have 0 rows for Chicago (S07 validation 2026-06-15)
--             Original SQL used METRIC_ID 800 — redesigned for 401 (Transaction Time ms)
--   Signal 3: reject_rate_anomaly — tap_reject_rate > 5% (daily grain)
--
-- Fixes applied 2026-06-19 (pre-build read of S07/S10/S03 before validation):
--   FIX 1: gold./silver./bronze. → mars_dev.gold./silver. + S3 parquet paths
--   FIX 2: bronze.device_metric → parquet S3 path (superseded by FIX 15)
--   FIX 3: bronze.metric_dimension → parquet S3 path (superseded by FIX 15)
--   FIX 15: raw parquet S3 reads (device_metric + metric_dimension) → mars_dev.silver.metric_hourly (S14)
--            Gold-layer governance fix 2026-06-22: gold tables must not read S3 parquet directly
--   FIX 4: EXTRACT(HOUR FROM ...)::int → CAST(HOUR(...) AS INT) (Spark SQL)
--   FIX 5: EXTRACT(DOW FROM ...)::int  → CAST(DAYOFWEEK(...) AS INT)
--           Note: Spark DAYOFWEEK = 1 (Sunday) … 7 (Saturday)
--   FIX 6: metric_hourly CTE — METRIC_IDs 800 and 810 = 0 rows for Chicago:
--           Confirmed in S07 header: "Only METRIC_ID=401 has data; 800 and 810 absent"
--           OLD: pivot on METRIC_ID IN (800, 810, 401) → metric_800_hourly always 0
--           NEW: only METRIC_ID=401; produces metric_401_tap_count_hour + metric_401_avg_ms_hour
--   FIX 7: TO_DATE(TRANSIT_DAY_KEY::text,'YYYYMMDD')::date
--             + (LEFT(TIME_INCREMENT_KEY::text, 2)::int * INTERVAL '1 hour')
--           → TIMESTAMPADD(HOUR, CAST(LEFT(LPAD(CAST(...AS STRING),4,'0'),2) AS INT),
--                          CAST(TO_DATE(CAST(...AS STRING),'yyyyMMdd') AS TIMESTAMP))
--           TIME_INCREMENT_KEY = char4 HHMM; LPAD to 4 to handle integer storage (<1000)
--   FIX 8: ::text casts (×7) → CAST(... AS STRING)
--   FIX 9: metric_baseline — columns renamed from metric_800_hourly → metric_401_avg_ms_hour
--           EXTRACT(HOUR FROM hour_bucket) → HOUR(hour_bucket)
--   FIX 10: Signal 2 — ABS(metric_800_hourly - baseline_mean_metric800)
--             → ABS(metric_401_avg_ms_hour - baseline_mean_metric401)
--   FIX 11: Ensemble score / ensemble_anomaly_flag — same metric_800 → metric_401 fix
--   FIX 12: avg_timing_ms in tap subquery → removed (CTA.ABP_USE_TRAN_TIMING_DATA = PATH_NOT_FOUND)
--           avg_tap_timing_ms feature → replaced with peak_hour_tap_count (from S10)
--   FIX 13: transit_day <= CURRENT_DATE() added to tap subquery (2032 future dates confirmed PS1)
--           transit_day >= '2024-01-01' added to hourly_events (ML training window)
--   FIX 14: CREATE INDEX (×6) → removed; not supported on Delta
-- =============================================================================

DROP TABLE IF EXISTS mars_dev.gold.device_ps4_hourly;

CREATE TABLE mars_dev.gold.device_ps4_hourly AS
WITH hourly_events AS (
    SELECT
        dee.DEVICE_ID,
        dee.DEVICE_KEY,
        dee.mars_device_category,
        dee.hour_bucket,
        dee.transit_day,
        -- FIX 4: EXTRACT(HOUR FROM ...)::int → CAST(HOUR(...) AS INT)
        CAST(HOUR(dee.hour_bucket) AS INT)                              AS hour_of_day,
        -- FIX 5: EXTRACT(DOW FROM ...)::int → CAST(DAYOFWEEK(...) AS INT)
        CAST(DAYOFWEEK(dee.hour_bucket) AS INT)                         AS day_of_week,
        COUNT(*)                                                        AS event_count_hour,
        SUM(CASE WHEN dee.severity = 'CRITICAL'             THEN 1 ELSE 0 END) AS critical_count_hour,
        SUM(CASE WHEN dee.is_hardware_oos_event              THEN 1 ELSE 0 END) AS oos_count_hour,
        -- TVM subsystems
        SUM(CASE WHEN dee.component_subsystem = 'BHU'       THEN 1 ELSE 0 END) AS bhu_count_hour,
        SUM(CASE WHEN dee.component_subsystem = 'CHU'       THEN 1 ELSE 0 END) AS chu_count_hour,
        SUM(CASE WHEN dee.component_subsystem = 'PRINTER'   THEN 1 ELSE 0 END) AS printer_count_hour,
        -- GATE subsystems
        SUM(CASE WHEN dee.component_subsystem = 'GATE_MECH' THEN 1 ELSE 0 END) AS gate_mech_count_hour,
        -- READER/VALIDATOR subsystems
        SUM(CASE WHEN dee.component_subsystem = 'CSC_READER' THEN 1 ELSE 0 END) AS csc_reader_count_hour,
        -- Shared
        SUM(CASE WHEN dee.component_subsystem = 'SCRST'     THEN 1 ELSE 0 END) AS scrst_count_hour,
        SUM(CASE WHEN dee.component_subsystem = 'COMMS'     THEN 1 ELSE 0 END) AS comms_count_hour,
        MAX(dee.severity)                                               AS max_severity_hour
    FROM mars_dev.silver.device_event_enriched dee
    WHERE dee.mars_device_category IN ('TVM','GATE','VALIDATOR')
      AND dee.hour_bucket IS NOT NULL
      -- FIX 13: ML training window (device_event_enriched starts 2024-01-01)
      AND dee.transit_day >= '2024-01-01'
    GROUP BY dee.DEVICE_ID, dee.DEVICE_KEY, dee.mars_device_category,
             dee.hour_bucket, dee.transit_day
),
event_baseline AS (
    SELECT
        DEVICE_ID,
        hour_bucket,
        hour_of_day,
        event_count_hour,
        AVG(event_count_hour) OVER (
            PARTITION BY DEVICE_ID, hour_of_day
            ORDER BY hour_bucket
            ROWS BETWEEN 28 * 24 PRECEDING AND 1 PRECEDING
        ) AS baseline_mean_events,
        STDDEV(event_count_hour) OVER (
            PARTITION BY DEVICE_ID, hour_of_day
            ORDER BY hour_bucket
            ROWS BETWEEN 28 * 24 PRECEDING AND 1 PRECEDING
        ) AS baseline_stddev_events
    FROM hourly_events
),
metric_hourly AS (
    -- FIX 15: replaced raw parquet.`s3://...device_metric/` + parquet.`s3://...metric_dimension/`
    -- with mars_dev.silver.metric_hourly (S14) — gold-layer governance fix 2026-06-22
    -- TIMESTAMPADD/LPAD/TRANSIT_DAY_KEY parsing + date filters live in S14 DDL
    SELECT
        DEVICE_KEY,
        hour_bucket,
        metric_401_tap_count_hour,
        metric_401_avg_ms_hour,
        metric_401_max_ms_hour
    FROM mars_dev.silver.metric_hourly
),
metric_baseline AS (
    -- FIX 9: renamed metric_800_hourly → metric_401_avg_ms_hour
    --         EXTRACT(HOUR FROM hour_bucket) → HOUR(hour_bucket)
    SELECT
        DEVICE_KEY,
        hour_bucket,
        metric_401_tap_count_hour,
        metric_401_avg_ms_hour,
        metric_401_max_ms_hour,
        AVG(metric_401_avg_ms_hour) OVER (
            PARTITION BY DEVICE_KEY, HOUR(hour_bucket)
            ORDER BY hour_bucket
            ROWS BETWEEN 28 * 24 PRECEDING AND 1 PRECEDING
        ) AS baseline_mean_metric401,
        STDDEV(metric_401_avg_ms_hour) OVER (
            PARTITION BY DEVICE_KEY, HOUR(hour_bucket)
            ORDER BY hour_bucket
            ROWS BETWEEN 28 * 24 PRECEDING AND 1 PRECEDING
        ) AS baseline_stddev_metric401
    FROM metric_hourly
)
SELECT
    he.DEVICE_ID,
    he.DEVICE_KEY,
    he.mars_device_category,
    he.hour_bucket,
    he.transit_day,
    he.hour_of_day,
    he.day_of_week,
    he.max_severity_hour,
    -- Hourly event features
    he.event_count_hour,
    he.critical_count_hour,
    he.oos_count_hour,
    he.bhu_count_hour,
    he.chu_count_hour,
    he.printer_count_hour,
    he.gate_mech_count_hour,
    he.csc_reader_count_hour,
    he.scrst_count_hour,
    he.comms_count_hour,
    -- Signal 1: event rate anomaly
    eb.baseline_mean_events,
    eb.baseline_stddev_events,
    CASE
        WHEN eb.baseline_stddev_events > 0
          AND he.event_count_hour > eb.baseline_mean_events + 2 * eb.baseline_stddev_events
        THEN 1 ELSE 0
    END                                                                 AS event_rate_anomaly,
    -- Metric features (METRIC_ID=401 — Transaction Time ms)
    -- FIX 6: metric_800/810_hourly removed (0 rows Chicago); metric_401 replaces
    COALESCE(mb.metric_401_tap_count_hour, 0)                           AS metric_401_tap_count_hour,
    COALESCE(mb.metric_401_avg_ms_hour, 0)                              AS metric_401_avg_ms_hour,
    COALESCE(mb.metric_401_max_ms_hour, 0)                              AS metric_401_max_ms_hour,
    mb.baseline_mean_metric401,
    mb.baseline_stddev_metric401,
    -- Signal 2: avg transaction time anomaly (FIX 10: was metric_800, now metric_401)
    CASE
        WHEN mb.baseline_stddev_metric401 > 0
          AND ABS(COALESCE(mb.metric_401_avg_ms_hour, 0) - mb.baseline_mean_metric401)
              > 2 * mb.baseline_stddev_metric401
        THEN 1 ELSE 0
    END                                                                 AS metric_anomaly,
    -- Tap features (daily grain, FIX 12: avg_timing_ms removed → peak_hour_tap_count)
    COALESCE(tap.tap_reject_rate_pct, 0)                                AS tap_reject_rate_daily,
    COALESCE(tap.peak_hour_tap_count, 0)                                AS peak_hour_tap_count,
    -- Signal 3: reject rate anomaly
    CASE WHEN COALESCE(tap.tap_reject_rate_pct, 0) > 5.0 THEN 1 ELSE 0 END
                                                                        AS reject_rate_anomaly,
    -- FIX 11: Ensemble anomaly score — metric_800_hourly replaced with metric_401_avg_ms_hour
    (
        CASE WHEN eb.baseline_stddev_events > 0
              AND he.event_count_hour > eb.baseline_mean_events + 2 * eb.baseline_stddev_events
             THEN 1 ELSE 0 END
      + CASE WHEN mb.baseline_stddev_metric401 > 0
              AND ABS(COALESCE(mb.metric_401_avg_ms_hour, 0) - mb.baseline_mean_metric401)
                  > 2 * mb.baseline_stddev_metric401
             THEN 1 ELSE 0 END
      + CASE WHEN COALESCE(tap.tap_reject_rate_pct, 0) > 5.0 THEN 1 ELSE 0 END
    )                                                                   AS anomaly_signal_count,
    CASE WHEN (
        CASE WHEN eb.baseline_stddev_events > 0
              AND he.event_count_hour > eb.baseline_mean_events + 2 * eb.baseline_stddev_events
             THEN 1 ELSE 0 END
      + CASE WHEN mb.baseline_stddev_metric401 > 0
              AND ABS(COALESCE(mb.metric_401_avg_ms_hour, 0) - mb.baseline_mean_metric401)
                  > 2 * mb.baseline_stddev_metric401
             THEN 1 ELSE 0 END
      + CASE WHEN COALESCE(tap.tap_reject_rate_pct, 0) > 5.0 THEN 1 ELSE 0 END
    ) >= 2 THEN 1 ELSE 0 END                                           AS ensemble_anomaly_flag,
    -- Device context
    dd.DEVICE_NAME,
    dd.FACILITY_ID,
    dd.FACILITY_NAME,
    dd.OPERATOR_ID,
    dd.OPERATOR_NAME,
    dd.BUS_ID,
    dd.bus_device_flag
FROM hourly_events he
LEFT JOIN event_baseline eb
    ON eb.DEVICE_ID = he.DEVICE_ID AND eb.hour_bucket = he.hour_bucket
LEFT JOIN metric_baseline mb
    ON mb.DEVICE_KEY = he.DEVICE_KEY AND mb.hour_bucket = he.hour_bucket
LEFT JOIN (
    -- FIX 12: avg_timing_ms removed (PATH_NOT_FOUND); peak_hour_tap_count replaces
    -- FIX 13: transit_day <= CURRENT_DATE() to exclude 2032 future dates
    SELECT
        DEVICE_ID,
        transit_day,
        tap_reject_rate_pct,
        peak_hour_tap_count
    FROM mars_dev.silver.tap_event_daily
    WHERE transit_day <= CURRENT_DATE()
) tap ON tap.DEVICE_ID = he.DEVICE_ID AND tap.transit_day = he.transit_day
LEFT JOIN mars_dev.silver.dim_device dd
    ON dd.DEVICE_KEY = he.DEVICE_KEY AND dd.is_current = TRUE;

-- FIX 14: CREATE INDEX (×6) removed — not supported on Delta tables
-- Post-build:
-- OPTIMIZE mars_dev.gold.device_ps4_hourly ZORDER BY (DEVICE_ID, hour_bucket);
--
-- Post-build verification:
-- SELECT
--     mars_device_category,
--     COUNT(*)                                               AS total_rows,
--     COUNT(DISTINCT DEVICE_ID)                             AS distinct_devices,
--     MIN(transit_day)                                      AS earliest_day,
--     MAX(transit_day)                                      AS latest_day,
--     SUM(event_rate_anomaly)                               AS signal1_count,
--     SUM(metric_anomaly)                                   AS signal2_count,
--     SUM(reject_rate_anomaly)                              AS signal3_count,
--     SUM(ensemble_anomaly_flag)                            AS ensemble_count,
--     ROUND(AVG(event_count_hour), 2)                       AS avg_events_per_hour,
--     SUM(CASE WHEN metric_401_avg_ms_hour > 0 THEN 1 END) AS hours_with_metric401
-- FROM mars_dev.gold.device_ps4_hourly
-- GROUP BY mars_device_category;
-- Note: metric_401 matches only ~2,784 DEVICE_KEYs (41.4% dim_device match rate from S07)
-- Note: tap features match only bus-capable devices (VALIDATOR + some TVM)
