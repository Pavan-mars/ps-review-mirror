-- =============================================================================
-- silver.station_network_daily  (S27)
-- Daily co-failure summary per (FACILITY_ID, device_category) — station-level
-- network and infrastructure failure signal for PS2 co-failure chain model.
--
-- Purpose:
--   When multiple devices fail at the same station on the same day, the root
--   cause is more likely a shared infrastructure event (AFC router down, power
--   failure, network partition) than independent device faults. This table flags
--   those coordinated events so PS2 can model station-level cascade chains.
--
-- Source:
--   S26 mars_dev.silver.device_failures  (device-day failure grain)
--   S06 mars_dev.silver.dim_device        (FACILITY_ID + FACILITY_NAME enrichment)
--
-- Grain: (FACILITY_ID, device_category, transit_day)
--   One row per station per device category per day where at least one device
--   had a hardware failure. Days with no failures have no row (sparse).
--
-- Key columns:
--   devices_failed          : distinct DEVICE_KEYs with failures on that day/station/category
--   is_coordinated_failure  : TRUE when ≥3 devices failed — likely infrastructure event
--   total_downtime_minutes  : sum of downtime across all failed devices at station
--   avg_downtime_minutes    : mean downtime per failed device (proxy for severity)
--
-- PS2 usage:
--   LEFT JOIN gold.device_ps2_chains to station_network_daily on
--   (FACILITY_ID + transit_day) to flag whether a device failure was part of a
--   coordinated station event (shared_infra_failure feature).
--
-- Build order: S06, S26 -> S27 (this table)
-- Feeds: gold.device_ps2_chains (station cascade signal)
--        gold.device_ps1_daily  (station_devices_failed feature — optional enrichment)
--
-- Validation:
--   SELECT device_category,
--          SUM(CAST(is_coordinated_failure AS INT)) AS coordinated_events,
--          COUNT(*) AS total_station_failure_days,
--          ROUND(AVG(devices_failed), 2) AS avg_devices_per_failure_day
--   FROM mars_dev.silver.station_network_daily
--   GROUP BY device_category ORDER BY 1;
-- =============================================================================

DROP TABLE IF EXISTS mars_dev.silver.station_network_daily;

CREATE TABLE mars_dev.silver.station_network_daily
USING DELTA
TBLPROPERTIES ('delta.autoOptimize.optimizeWrite' = 'true')
COMMENT 'Station-level daily co-failure summary. Flags coordinated infrastructure events (≥3 devices failed same day/station). Source: S26 device_failures.'
AS
SELECT
    d.FACILITY_ID,
    -- MAX() on name/operator cols keeps them out of GROUP BY key.
    -- Prevents SCD2 fan-out: if dim_device has >1 is_current row per DEVICE_KEY with
    -- different FACILITY_NAME (name-update reload), these would produce duplicate
    -- (FACILITY_ID, device_category, transit_day) rows when grouped by name columns.
    MAX(d.FACILITY_NAME)                                        AS FACILITY_NAME,
    MAX(d.OPERATOR_ID)                                          AS OPERATOR_ID,
    MAX(d.OPERATOR_NAME)                                        AS OPERATOR_NAME,
    df.device_category,
    df.failure_date                                             AS transit_day,

    -- Co-failure counts
    COUNT(DISTINCT df.DEVICE_KEY)                              AS devices_failed,
    SUM(df.failure_event_count)                                AS total_failure_events,
    SUM(df.downtime_minutes)                                   AS total_downtime_minutes,
    ROUND(AVG(df.downtime_minutes), 2)                         AS avg_downtime_minutes,
    MAX(df.downtime_minutes)                                   AS max_downtime_minutes,

    -- Infrastructure / coordinated failure flag:
    -- ≥3 distinct devices failing at the same station on the same day in the same category
    -- is a strong signal for a shared root cause (router, power, network partition).
    COUNT(DISTINCT df.DEVICE_KEY) >= 3                         AS is_coordinated_failure,

    -- Severe: ≥5 devices (high-impact station-wide event)
    COUNT(DISTINCT df.DEVICE_KEY) >= 5                         AS is_major_station_event,

    -- Failure window at the station
    MIN(df.first_failure_dtm)                                  AS first_failure_dtm,
    MAX(df.last_failure_dtm)                                   AS last_failure_dtm

FROM mars_dev.silver.device_failures df
INNER JOIN (
    -- Deduplicate dim_device to 1 row per DEVICE_KEY — same pattern as S26.
    -- A DEVICE_KEY with >1 is_current=TRUE row (SCD2 violation) would otherwise join
    -- to multiple FACILITY_IDs, producing duplicate (FACILITY_ID, category, date) output rows
    -- even after removing FACILITY_NAME from the GROUP BY (confirmed: 1 duplicate persisted).
    SELECT DEVICE_KEY,
           MAX(FACILITY_ID)    AS FACILITY_ID,
           MAX(FACILITY_NAME)  AS FACILITY_NAME,
           MAX(OPERATOR_ID)    AS OPERATOR_ID,
           MAX(OPERATOR_NAME)  AS OPERATOR_NAME
    FROM mars_dev.silver.dim_device
    WHERE is_current = TRUE
    GROUP BY DEVICE_KEY
) d ON d.DEVICE_KEY = df.DEVICE_KEY
WHERE d.FACILITY_ID IS NOT NULL          -- exclude devices with no station assignment
GROUP BY
    d.FACILITY_ID,
    df.device_category,
    df.failure_date;

-- Post-load validation:
-- SELECT device_category,
--        COUNT(*) AS station_failure_days,
--        SUM(CAST(is_coordinated_failure AS INT)) AS coordinated_events,
--        SUM(CAST(is_major_station_event AS INT)) AS major_events,
--        ROUND(AVG(devices_failed), 2) AS avg_devices_per_station_day
-- FROM mars_dev.silver.station_network_daily
-- GROUP BY device_category ORDER BY 1;
