-- =============================================================================
-- silver.read_tap_daily  (S22)
-- Daily tap/read aggregation from EDW.READ_TRANSACTION - device-day grain
-- PS1 tap-volume feature; PS4 daily read-activity signal
--
-- Source (bronze):
--   mars_dev.bronze.edw_read_transaction       (EDW.READ_TRANSACTION)
--   mars_dev.silver.dim_device                 (S06 - device enrichment)
--
-- Bronze schema (confirmed 2026-06-25, 118 columns, 12,443,504 rows):
--   TRANSIT_DAY_KEY      decimal(8,0)  - date key YYYYMMDD
--   DEVICE_ID            varchar(15)   - device identifier, matches dim_device
--   TRANSACTION_DTM      timestamp     - transaction timestamp (hour extraction)
--   TAP_STATUS_ID        decimal(3,0)  - approval code (1/900/904 = approved)
--   REVENUE_OR_TEST      varchar(7)    - filter to 'REVENUE' (same as S21)
--   TOKEN_ID             decimal(18,0) - fare media token (unique card count)
--   OPERATOR_ID          decimal(5,0)  - operator
--   FACILITY_ID          decimal(5,0)  - facility
--   BUS_ID               decimal(9,0)  - bus vehicle ID
--   IN_OUT               decimal(1,0)  - entry/exit direction
--   TRANSFER_CODE        decimal(3,0)  - transfer tap flag
--   VALUE_REMAINING      decimal(10,0) - card balance after tap
--
-- Grain: (DEVICE_ID, transit_day) - aggregated from 12.4M transaction rows
-- Complements S13 tap_event_daily (sourced from NCS/ABP_TAP, VALIDATOR+GATE only)
-- EDW_READ_TRANSACTION covers a broader device and transaction scope
--
-- Build order: S06 (dim_device) -> S22 (this table)
-- Feeds:       gold.device_ps1_daily (read_tap_count feature)
--              gold.device_ps4_hourly (daily read-activity signal)
-- =============================================================================

DROP TABLE IF EXISTS mars_dev.silver.read_tap_daily;

CREATE TABLE mars_dev.silver.read_tap_daily AS
WITH tap_agg AS (
    SELECT
        rt.DEVICE_ID,
        TO_DATE(CAST(CAST(rt.TRANSIT_DAY_KEY AS BIGINT) AS STRING), 'yyyyMMdd')
                                                                AS transit_day,
        rt.TRANSIT_DAY_KEY,
        rt.OPERATOR_ID,
        rt.FACILITY_ID,
        rt.BUS_ID,

        -- Volume
        COUNT(*)                                                AS daily_read_count,
        COUNT(DISTINCT rt.TOKEN_ID)                             AS unique_tokens,

        -- Approval breakdown (same codes as ABP_TAP - 1/900/904 = approved)
        SUM(CASE WHEN rt.TAP_STATUS_ID IN (1, 900, 904) THEN 1 ELSE 0 END)
                                                                AS approved_read_count,
        SUM(CASE WHEN rt.TAP_STATUS_ID NOT IN (1, 900, 904) THEN 1 ELSE 0 END)
                                                                AS rejected_read_count,

        -- Entry/exit split (IN_OUT: 1=entry, 0=exit)
        SUM(CASE WHEN rt.IN_OUT = 1 THEN 1 ELSE 0 END)         AS entry_count,
        SUM(CASE WHEN rt.IN_OUT = 0 THEN 1 ELSE 0 END)         AS exit_count,

        -- Transfer taps
        SUM(CASE WHEN rt.TRANSFER_CODE IS NOT NULL
                  AND rt.TRANSFER_CODE > 0 THEN 1 ELSE 0 END)  AS transfer_tap_count,

        -- Operational window
        COUNT(DISTINCT HOUR(rt.TRANSACTION_DTM))                AS read_active_hours,
        MIN(rt.TRANSACTION_DTM)                                 AS first_read_dtm,
        MAX(rt.TRANSACTION_DTM)                                 AS last_read_dtm

    FROM mars_dev.bronze.edw_read_transaction rt
    WHERE rt.REVENUE_OR_TEST = 'REVENUE'
      AND rt.TRANSACTION_DTM  IS NOT NULL
      AND rt.DEVICE_ID         IS NOT NULL
      AND rt.TRANSIT_DAY_KEY  >= 20240101                       -- ML training window
    GROUP BY
        rt.DEVICE_ID,
        rt.TRANSIT_DAY_KEY,
        rt.OPERATOR_ID,
        rt.FACILITY_ID,
        rt.BUS_ID
)
SELECT
    -- -- Keys -----------------------------------------------------------------
    ta.DEVICE_ID,
    ta.transit_day,
    ta.TRANSIT_DAY_KEY,
    ta.OPERATOR_ID,
    ta.FACILITY_ID,
    ta.BUS_ID,

    -- -- Volume ----------------------------------------------------------------
    ta.daily_read_count,
    ta.unique_tokens,
    ta.approved_read_count,
    ta.rejected_read_count,
    CASE
        WHEN ta.daily_read_count > 0
        THEN ROUND(CAST(ta.rejected_read_count AS DOUBLE) / ta.daily_read_count * 100, 4)
        ELSE 0
    END                                                         AS reject_rate_pct,

    -- -- Entry/exit ------------------------------------------------------------
    ta.entry_count,
    ta.exit_count,
    ta.transfer_tap_count,

    -- -- Operational window ----------------------------------------------------
    ta.read_active_hours,
    ta.first_read_dtm,
    ta.last_read_dtm,

    -- -- Device enrichment from S06 --------------------------------------------
    dd.DEVICE_KEY,
    dd.DEVICE_NAME,
    dd.mars_device_category,
    dd.FACILITY_NAME,
    dd.OPERATOR_NAME,
    dd.TRANSIT_MODE_NAME

FROM tap_agg ta
LEFT JOIN mars_dev.silver.dim_device dd
    ON  dd.DEVICE_ID  = ta.DEVICE_ID
    AND dd.is_current = TRUE;

-- Post-load verification:
-- SELECT
--     COUNT(*)                                               AS total_rows,
--     COUNT(DISTINCT DEVICE_ID)                             AS distinct_devices,
--     MIN(transit_day)                                      AS earliest_day,
--     MAX(transit_day)                                      AS latest_day,
--     SUM(CASE WHEN DEVICE_KEY IS NULL THEN 1 ELSE 0 END)   AS unmatched_devices,
--     ROUND(SUM(CASE WHEN rejected_read_count > 0 THEN 1 ELSE 0 END) * 100.0 / COUNT(*), 2)
--                                                           AS pct_days_with_rejects
-- FROM mars_dev.silver.read_tap_daily;
