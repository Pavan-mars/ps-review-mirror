-- =============================================================================
-- silver.device_event_enriched
-- De-duplicated and enriched device event fact table
--
-- Source (mars_dev.bronze catalog):
--   EDW.DEVICE_EVENT  (1.12 billion rows, 49 cols)
--
-- Dimensions joined (must be created first):
--   mars_dev.silver.dim_device     (S06 - device master + mars_device_category)
--   mars_dev.silver.dim_event_type (S07 - component_subsystem + is_oos_event + is_hardware_oos_event
--                                          + is_commanded_oos_event + is_reader_event
--                                          + S03 KPI flags: event_priority, oos_counted_*_kpi,
--                                            requires_service_call, is_set_clear)
-- Build order: S03 -> S06 -> S07 -> S16 -> S18 -> S17
--
-- De-dup logic: ROW_NUMBER() PARTITION BY DW_DEVICE_EVENT_ID
--               ORDER BY EDW_UPDATED_DTM DESC NULLS LAST
--   -> keeps the most recently updated record per unique event ID
--
-- Validation run 2026-06-15 - bugs fixed from original:
--   BUG 1: bronze.device_event         -> parquet S3 path
--   BUG 2: silver.dim_device           -> mars_dev.silver.dim_device
--   BUG 3: silver.dim_event_type       -> mars_dev.silver.dim_event_type
--   BUG 4: ::text / ::date cast        -> CAST(... AS STRING) / CAST(... AS DATE)
--   BUG 5: TO_DATE(x::text,'YYYYMMDD') -> TO_DATE(CAST(x AS STRING),'yyyyMMdd')
--   BUG 6: EXTRACT(HOUR FROM ...)::int -> HOUR(...)
--   BUG 7: EXTRACT(DOW FROM ...)::int  -> DAYOFWEEK(...) - 1  (0=Sun..6=Sat)
--   BUG 8: EXTRACT(MONTH FROM ...)::int-> MONTH(...)
--   BUG 9: EXTRACT(EPOCH FROM (a-b))/60 -> (unix_timestamp(a)-unix_timestamp(b))/60.0
--   BUG 10: ILIKE '%x%'               -> LOWER(col) LIKE '%x%'
--   BUG 11: et.SEVERITY >= 3 with NULL -> COALESCE(et.SEVERITY, 0) >= 3
--   BUG 12: et.is_oos_event OR NULL    -> COALESCE(et.is_oos_event, FALSE) = TRUE
--   BUG 13: CREATE INDEX statements    -> not supported on Delta; use OPTIMIZE/ZORDER
--           Recommended after load:
--           OPTIMIZE mars_dev.silver.device_event_enriched
--             ZORDER BY (DEVICE_ID, transit_day);
--   BUG 14: JOIN ON DEVICE_KEY         -> JOIN ON DEVICE_ID AND dd.is_current = TRUE
--           DEVICE_KEY is SCD2 surrogate; events reference the historical key.
--           dim_device is now full SCD2 (189,570 rows); is_current=TRUE guard selects
--           the single active row per DEVICE_ID - prevents fan-out and enriches events
--           with current device attributes (~100% match rate, correct for ML features).
--   BUG 15: EVENT_STATE_TYPE_NAME OOS patterns dead
--           Only 5 values exist: 'Set','Clear','Set and Clear','Automatic Clear',NULL.
--           'out%of%service' and 'fault' never match -> removed; is_oos_event now
--           relies solely on et.is_oos_event from dim_event_type.
--   BUG 16: duration_to_clear_min outliers
--           max = 513M min (≈975 years); all 1.4M rows in 1 week have CLEAR_DTM.
--           Added CLEAR_DTM >= EVENT_DTM guard and 10,080-min cap (7 days).
--           Events open >7 days are data quality issues, not real durations.
-- =============================================================================

DROP TABLE IF EXISTS mars_dev.silver.device_event_enriched;

CREATE TABLE mars_dev.silver.device_event_enriched AS
WITH deduped AS (
    SELECT
        de.*,
        ROW_NUMBER() OVER (
            PARTITION BY de.DW_DEVICE_EVENT_ID
            ORDER BY de.EDW_UPDATED_DTM DESC NULLS LAST
        ) AS rn
    FROM mars_dev.bronze.edw_device_event de
),
base AS (
    SELECT
        de.DW_DEVICE_EVENT_ID,
        de.EVENT_DAY_KEY,
        de.TIME_PERIOD_KEY,
        de.TIME_INCREMENT_KEY,
        de.EVENT_TYPE_KEY,
        de.OPERATOR_KEY,
        de.DEVICE_KEY,
        de.EMPLOYEE_KEY,
        de.CLEAR_DAY_KEY,
        de.CLEAR_EMPLOYEE_KEY,
        de.AUTOMATIC_CLEAR_FLAG,
        de.CLEAR_DTM,
        de.CLEAR_MESSAGE_ID,
        de.CLEAR_EVENT_STATE_TYPE_ID,
        de.CLEAR_EVENT_STATE_TYPE_NAME,
        de.COMPONENT_POSITION,
        de.COMPONENT_SERIAL_NBR,
        de.COMPONENT_TYPE_ID,
        de.COMPONENT_TYPE_NAME,
        de.DEVICE_TRANSACTION_ID,
        de.EDW_INSERTED_DTM,
        de.EVENT_DTM,
        de.EVENT_STATE_TYPE_ID,
        de.EVENT_STATE_TYPE_NAME,
        de.EVENT_STATUS_PATH_ID,
        de.EVENT_STATUS_PATH_NAME,
        de.EXTENDED_DATA_SHORT,
        de.EXTENDED_DATA_LONG,
        de.EXTENDED_DATA_STRING,
        de.LOCAL_LOGON_ID,
        de.MATCHED_FLAG,
        de.MESSAGE_ID,
        de.SOURCE_INSERTED_DTM,
        de.STAGING_INSERTED_DTM,
        de.EDW_UPDATED_DTM,
        de.EXTERNAL_DEVICE_ID,
        de.DEVICE_ID,
        de.OPERATOR_ID,
        de.FACILITY_ID,
        de.BUS_ID,
        de.DEVICE_POSITION,
        de.POSTING_DAY_KEY,
        de.ROUTE_NUMBER,
        de.BLOCK_NUMBER,
        de.LATITUDE,
        de.LONGITUDE,
        de.EVENT_REASON_CODE_ID,
        de.STOP_POINT_ID,
        de.STOP_POINT_KEY
    FROM deduped de
    WHERE de.rn = 1
)
SELECT
    b.DW_DEVICE_EVENT_ID,
    b.EVENT_DAY_KEY,
    b.EVENT_DTM,
    b.DEVICE_KEY,
    -- FIX 2026-07-01 (DQ v3): recover null DEVICE_ID from DEVICE_KEY -> dim_device (dk)
    COALESCE(b.DEVICE_ID, dk.DEVICE_ID)                        AS DEVICE_ID,
    b.EVENT_TYPE_KEY,
    b.OPERATOR_ID,
    b.FACILITY_ID,
    b.BUS_ID,
    b.COMPONENT_TYPE_ID,
    b.COMPONENT_TYPE_NAME,
    b.COMPONENT_SERIAL_NBR,
    b.COMPONENT_POSITION,
    b.EVENT_STATE_TYPE_ID,
    COALESCE(b.EVENT_STATE_TYPE_NAME, 'UNKNOWN')                          AS EVENT_STATE_TYPE_NAME,
    b.EVENT_STATUS_PATH_ID,
    b.EVENT_STATUS_PATH_NAME,
    b.AUTOMATIC_CLEAR_FLAG,
    b.CLEAR_DTM,
    b.CLEAR_DAY_KEY,
    b.CLEAR_EVENT_STATE_TYPE_NAME,
    b.MATCHED_FLAG,
    b.MESSAGE_ID,
    b.EXTENDED_DATA_SHORT,
    b.EXTENDED_DATA_LONG,
    b.EXTENDED_DATA_STRING,
    b.DEVICE_TRANSACTION_ID,
    b.ROUTE_NUMBER,
    b.BLOCK_NUMBER,
    b.STOP_POINT_ID,
    b.STOP_POINT_KEY,
    b.LATITUDE,
    b.LONGITUDE,
    b.EVENT_REASON_CODE_ID,
    b.EDW_INSERTED_DTM,
    b.EDW_UPDATED_DTM,
    b.SOURCE_INSERTED_DTM,
    b.STAGING_INSERTED_DTM,

    -- Date/time derived fields (Spark SQL syntax)
    TO_DATE(CAST(b.EVENT_DAY_KEY AS STRING), 'yyyyMMdd')        AS transit_day,
    HOUR(b.EVENT_DTM)                                           AS hour_of_day,
    DAYOFWEEK(b.EVENT_DTM) - 1                                  AS day_of_week,   -- 0=Sun..6=Sat
    MONTH(b.EVENT_DTM)                                          AS month_of_year,
    date_trunc('hour', b.EVENT_DTM)                             AS hour_bucket,

    -- Duration until clear (minutes)
    -- CLEAR_DTM >= EVENT_DTM guard excludes negative durations (data error)
    -- 10,080-min cap (7 days): events open longer are data quality issues
    CASE
        WHEN b.CLEAR_DTM IS NOT NULL
         AND b.EVENT_DTM IS NOT NULL
         AND b.CLEAR_DTM >= b.EVENT_DTM
        THEN LEAST(
               (unix_timestamp(b.CLEAR_DTM) - unix_timestamp(b.EVENT_DTM)) / 60.0,
               10080.0
             )
        ELSE NULL
    END                                                         AS duration_to_clear_min,

    -- Device enrichment from silver.dim_device (S06)
    dd.DEVICE_NAME,
    dd.DEVICE_TYPE_ID,
    dd.DEVICE_TYPE_NAME,
    dd.DEVICE_CONTROL_GROUP_ID,
    dd.DEVICE_CONTROL_GROUP_NAME,
    dd.DEVICE_CONTROL_GROUP_TYPE_ID,
    dd.DEVICE_CONTROL_GROUP_TYPE_NAME,
    dd.TRANSIT_MODE_NAME,
    dd.AGENCY_NAME,
    dd.FACILITY_NAME,
    dd.OPERATOR_NAME,
    dd.DEVICE_SERIAL_NUMBER,
    dd.mars_device_category,

    -- Event type enrichment from silver.dim_event_type (S07)
    et.EVENT_TYPE_ID,
    et.EVENT_SOURCE,
    et.EVENT_TYPE_NAME,
    et.EVENT_TYPE_DESC,
    et.SEVERITY                                                 AS event_type_severity,
    et.component_subsystem,
    et.is_oos_event                                             AS et_is_oos_event,

    -- Severity: from event-type only
    -- EVENT_STATE_TYPE_NAME values (Set/Clear/Set and Clear) do not carry severity signal
    -- COALESCE guards against NULL SEVERITY (94.8% NULL in dim_event_type)
    CASE
        WHEN COALESCE(et.SEVERITY, 0) >= 3                      THEN 'CRITICAL'
        WHEN COALESCE(et.SEVERITY, 0) = 2                       THEN 'WARN'
        ELSE                                                          'INFO'
    END                                                         AS severity,

    -- is_oos_event: all OOS (hardware + commanded) - keep for reference/PS2
    -- EVENT_STATE_TYPE_NAME has only 5 values (Set/Clear/Set and Clear/Automatic Clear/NULL)
    -- - none contain 'fault' or 'out of service', so those patterns are removed
    COALESCE(et.is_oos_event, FALSE)                            AS is_oos_event,

    -- is_hardware_oos_event: hardware failures only - use this for PS1 labels and S18
    -- Excludes commanded/maintenance codes (106, 110, 151, 208, 519, 1603, 1604)
    COALESCE(et.is_hardware_oos_event, FALSE)                   AS is_hardware_oos_event,

    -- is_commanded_oos_event: operator-triggered OOS - useful for PS3 context
    COALESCE(et.is_commanded_oos_event, FALSE)                  AS is_commanded_oos_event,

    -- is_reader_event: CSC_READER subsystem (EVENT_TYPE_ID 200-299) - added 2026-06-24
    COALESCE(et.is_reader_event, FALSE)                         AS is_reader_event,

    -- S03 dim_event_matrix enrichment (via S07 JOIN - added 2026-06-24)
    -- Authoritative per-code KPI counting rules and service flags from Michael's Device Event Matrix Excel.
    -- NULL for event codes absent from S03 (codes not in Michael's 155-row matrix).
    et.event_priority,          -- 1=critical, 2=high, 3=medium, 4=low (NULL if not in S03)
    et.oos_counted_gate_kpi,    -- TRUE = this OOS counts toward gate availability KPI
    et.oos_counted_bus_kpi,     -- TRUE = this OOS counts toward bus availability KPI
    et.oos_counted_fmvd_kpi,    -- TRUE = this OOS counts toward FMVD/TVM availability KPI
    et.requires_service_call,   -- TRUE = event requires field technician dispatch
    et.is_set_clear,            -- TRUE = event fires in Set/Clear pairs (not one-shot)

    -- GX/DQ alignment 2026-06-25: silver build-audit timestamp (freshness expectations)
    current_timestamp()                                         AS _silver_load_ts

FROM base b
-- FIX 2026-07-01 (DQ v3): recover null DEVICE_ID from DEVICE_KEY. dk = the dim_device row for
-- the event's (historical) DEVICE_KEY surrogate -> 1:1 (one dim row per key), no fan-out.
-- DEVICE_KEY is small enough to broadcast. No category scope: this is the all-device feature
-- spine (PS4/validators included) -- we only drop truly unattributable events (both null).
LEFT JOIN mars_dev.silver.dim_device     dk ON dk.DEVICE_KEY     = b.DEVICE_KEY
-- Enrich on the COALESCED id so backfilled rows also get current attributes.
-- Join on DEVICE_ID (business key), not DEVICE_KEY; is_current=TRUE selects the single active
-- row per DEVICE_ID - prevents fan-out, enriches events with current device attributes.
LEFT JOIN mars_dev.silver.dim_device     dd ON dd.DEVICE_ID      = COALESCE(b.DEVICE_ID, dk.DEVICE_ID)
                                           AND dd.is_current      = TRUE
LEFT JOIN mars_dev.silver.dim_event_type et ON et.EVENT_TYPE_KEY = b.EVENT_TYPE_KEY
-- Drop events with neither a DEVICE_ID nor a resolvable DEVICE_KEY (unattributable).
WHERE COALESCE(b.DEVICE_ID, dk.DEVICE_ID) IS NOT NULL;

-- Post-load optimisation (run after CREATE TABLE completes):
-- OPTIMIZE mars_dev.silver.device_event_enriched ZORDER BY (DEVICE_ID, transit_day);
