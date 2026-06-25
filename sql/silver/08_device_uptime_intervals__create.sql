-- =============================================================================
-- silver.device_uptime_intervals
-- Device uptime / heartbeat intervals from last-state and end-of-day tables
--
-- Sources (bronze UC managed tables in mars_dev.bronze):
--   ncs_stage_device_end_of_day        (2.96M rows, 23 cols)  - daily EOD records per device
--   ncs_stage_device_end_of_day_msg_count (31.4M rows, 16 cols) - daily message counts
--   EDW.DEVICE_LAST_STATE (catalog)     (90,689 rows, 7 cols)  - last heartbeat per device
--
-- Dimension joined:
--   mars_dev.silver.dim_device (S06)
--
-- Notes:
--   - LAST_HEART_BEAT_DTM in DEVICE_LAST_STATE = last device communication timestamp
--   - is_silent_device = gap from last heartbeat to EDW_UPDATED_DTM > 24h
--   - COMPLETE_FLAG (decimal 0/1): 1 = device completed EOD reporting = operational proxy
--   - last_state joined on DEVICE_KEY: one row per device (current state only)
--
-- Validation run 2026-06-15 - bugs fixed from original:
--   BUG 1: bronze.*                  -> parquet S3 paths (3 tables)
--   BUG 2: silver.dim_device         -> mars_dev.silver.dim_device
--   BUG 3: silver.device_uptime_intervals -> mars_dev.silver.device_uptime_intervals
--   BUG 4: EXTRACT(EPOCH FROM (a-b)) -> (unix_timestamp(a) - unix_timestamp(b))
--   BUG 5: TO_DATE(x::text,'YYYYMMDD')::date -> TO_DATE(CAST(x AS STRING),'yyyyMMdd')
--   BUG 6: ROUND(x::numeric / y * 100, 2) -> ROUND(CAST(x AS DOUBLE) / y * 100, 2)
--   BUG 7: CREATE INDEX              -> not supported on Delta; use OPTIMIZE/ZORDER
--   BUG 8: eod.DATE_KEY              -> eod.TRANSIT_DAY_KEY (confirmed 2026-06-18)
--
-- Validation run 2026-06-18 - additional fixes after bronze catalog schema probe:
--   BUG A: NCS parquet S3 paths      -> UC managed catalog table refs (both NCS tables)
--   BUG B: UPTIME_SECONDS, DOWNTIME_SECONDS, TOTAL_SECONDS -> NULL (columns absent in
--           bronze ncs_stage_device_end_of_day; confirmed 2026-06-18 schema probe)
--           uptime_pct, uptime_hours, downtime_hours all -> NULL consequently
--           FACID, OPERATOR_ID, BUS_ID in EOD CTE -> removed (absent; come from dim_device)
--   BUG C: mc.MSG_COUNT              -> mc.MESSAGE_COUNT (actual column name)
--   BUG D: mc.EVENT_TYPE_ID          -> mc.MESSAGE_NAME  (no event type ID in msg table)
--   Added: COMPLETE_FLAG, eod_count_messages, eod_count_received_messages
--          (COMPLETE_FLAG = 1 is the primary uptime proxy for this table)
--
--   BUG E: Future TRANSIT_DAY_KEY values (up to 2034-03-03) found in bronze
--           -> added CURRENT_DATE() guard in end_of_day_daily CTE (2026-06-18)
--
-- ✓ UNBLOCKED - 2026-06-18: NCS tables confirmed in mars_dev.bronze catalog
-- =============================================================================

DROP TABLE IF EXISTS mars_dev.silver.device_uptime_intervals;

CREATE TABLE mars_dev.silver.device_uptime_intervals AS
WITH last_state AS (
    SELECT
        ls.DEVICE_KEY,
        ls.EVENT_DTM                    AS last_event_dtm,
        ls.DEVICE_STATE_TYPE_KEY,
        ls.MESSAGE_ID,
        ls.LAST_HEART_BEAT_DTM,
        ls.EDW_INSERTED_DTM,
        ls.EDW_UPDATED_DTM,
        (unix_timestamp(ls.EDW_UPDATED_DTM) - unix_timestamp(ls.LAST_HEART_BEAT_DTM)) / 3600.0
                                        AS hours_since_last_hb,
        (
            ls.LAST_HEART_BEAT_DTM IS NOT NULL
            AND (unix_timestamp(ls.EDW_UPDATED_DTM) - unix_timestamp(ls.LAST_HEART_BEAT_DTM)) > 86400
        )                               AS is_silent_device
    FROM mars_dev.bronze.edw_device_last_state ls
),
end_of_day_daily AS (
    SELECT
        eod.DEVICE_ID,
        eod.DEVICE_KEY,
        eod.TRANSIT_DAY_KEY,
        TO_DATE(CAST(eod.TRANSIT_DAY_KEY AS STRING), 'yyyyMMdd') AS eod_date,
        eod.COMPLETE_FLAG,
        eod.COUNT_MESSAGES              AS eod_count_messages,
        eod.COUNT_RECEIVED_MESSAGES     AS eod_count_received_messages,
        -- UPTIME_SECONDS / DOWNTIME_SECONDS / TOTAL_SECONDS absent in bronze (2026-06-18)
        -- Use COMPLETE_FLAG as primary uptime proxy
        CAST(NULL AS BIGINT)            AS UPTIME_SECONDS,
        CAST(NULL AS BIGINT)            AS DOWNTIME_SECONDS,
        CAST(NULL AS BIGINT)            AS TOTAL_SECONDS,
        CAST(NULL AS DOUBLE)            AS uptime_pct,
        CAST(NULL AS DOUBLE)            AS uptime_hours,
        CAST(NULL AS DOUBLE)            AS downtime_hours
    FROM mars_dev.bronze.ncs_stage_device_end_of_day eod
    WHERE eod.TRANSIT_DAY_KEY IS NOT NULL
      AND TO_DATE(CAST(eod.TRANSIT_DAY_KEY AS STRING), 'yyyyMMdd') <= CURRENT_DATE()
),
msg_counts AS (
    SELECT
        mc.DEVICE_ID,
        mc.TRANSIT_DAY_KEY,
        SUM(mc.MESSAGE_COUNT)               AS total_msg_count,
        COUNT(DISTINCT mc.MESSAGE_NAME)     AS distinct_message_types
    FROM mars_dev.bronze.ncs_stage_device_end_of_day_msg_count mc
    GROUP BY mc.DEVICE_ID, mc.TRANSIT_DAY_KEY
)
SELECT
    dd.DEVICE_KEY,
    dd.DEVICE_ID,
    dd.DEVICE_NAME,
    dd.mars_device_category,
    dd.FACILITY_ID,
    dd.FACILITY_NAME,
    dd.OPERATOR_ID,
    dd.OPERATOR_NAME,
    eod.eod_date,
    eod.TRANSIT_DAY_KEY                 AS date_key,
    eod.COMPLETE_FLAG,
    eod.eod_count_messages,
    eod.eod_count_received_messages,
    eod.uptime_hours,
    eod.downtime_hours,
    eod.uptime_pct,
    eod.UPTIME_SECONDS,
    eod.DOWNTIME_SECONDS,
    eod.TOTAL_SECONDS,
    COALESCE(mc.total_msg_count, 0)         AS total_msg_count,
    COALESCE(mc.distinct_message_types, 0)  AS distinct_message_types,
    ls.last_event_dtm,
    ls.DEVICE_STATE_TYPE_KEY,
    ls.LAST_HEART_BEAT_DTM,
    ls.hours_since_last_hb,
    ls.is_silent_device,
    ls.EDW_UPDATED_DTM                  AS last_state_updated_dtm

FROM end_of_day_daily eod
JOIN mars_dev.silver.dim_device dd
    ON dd.DEVICE_ID  = eod.DEVICE_ID
   AND dd.is_current = TRUE
LEFT JOIN last_state ls
    ON ls.DEVICE_KEY = dd.DEVICE_KEY
LEFT JOIN msg_counts mc
    ON mc.DEVICE_ID  = eod.DEVICE_ID
   AND mc.TRANSIT_DAY_KEY = eod.TRANSIT_DAY_KEY;

-- Post-load optimisation:
-- OPTIMIZE mars_dev.silver.device_uptime_intervals ZORDER BY (DEVICE_ID, eod_date);
