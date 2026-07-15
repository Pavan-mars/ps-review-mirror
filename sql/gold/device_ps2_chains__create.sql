-- =============================================================================
-- gold.device_ps2_chains
-- PS2 -- Cascade / Fault-Chain Analysis: fault-chain features per device per day
-- Grain: (device_id, transit_day) where device had >= 2 fault-onset OOS events
-- Target: subsystem_chain -- ordered sequence of component subsystems per fault day
-- Device types: TVM, GATE, VALIDATOR
-- Sources (silver):
--   S06  mars_dev.silver.dim_device
--   S16  mars_dev.silver.device_event_enriched
--   S24  mars_dev.silver.device_incident_features_daily  (NEW 2026-07-07)
--         +5 incident history cols; join on DEVICE_KEY + transit_day
--         TVM 17.75% coverage, GATE 3.33%, VALIDATOR 0%
-- Sources (bronze):
--   mars_dev.bronze.ncs_stage_cashbox_tracking  (TVM only -- 140M rows)
--   mars_dev.bronze.ncs_stage_cashbox_type      (15 rows -- cashbox type dimension)
--
-- DATA GAP NOTE:
--   SVN_STAGE tables all have 0 rows. CI dependency features are UNAVAILABLE.
--   Chain analysis is event-sequence-based within a single device only.
--
-- Fixes applied 2026-06-19 (pre-validation run):
--   FIX 1: READER excluded from category filter (0 events confirmed)
--   FIX 2: Severity filter replaced with fault-onset filter:
--             OLD: severity IN ('WARN','CRITICAL')
--                  -- V01 confirmed: 100% INFO, CRITICAL=22, WARN=0 -> near-zero rows
--             NEW: is_hardware_oos_event = TRUE             (commanded codes 106/110/151/208/519 excluded)
--                  AND EVENT_STATE_TYPE_NAME = 'Set'        -- fault onset only (not Clear)
--                  -- NOT IN ('SYSTEM','COMMS') dropped -- commanded codes were the SYSTEM/COMMS noise;
--                  -- is_hardware_oos_event excludes them at source (S07 column). Updated 2026-06-23.
--                  -- After filter: avg 3.99 fault onsets/day expected (same volume; cleaner signal)
--   FIX 3: STRING_AGG(... ORDER BY ...) -> struct sort approach (Spark SQL equivalent):
--             array_join(transform(array_sort(collect_list(struct(dt, val))), x->x.val), '->')
--   FIX 4: FILTER (WHERE event_seq = 1) -> MAX(CASE WHEN event_seq = 1 THEN col END)
--   FIX 5: FILTER (WHERE event_seq = subquery) -> MAX(CASE WHEN event_seq = dc.fault_event_count ...)
--   FIX 6: ILIKE on STRING_AGG result -> MAX(CASE WHEN component_subsystem = 'BHU' ...) approach
--             -- more reliable than substring match on aggregated string
--   FIX 7: EXTRACT(EPOCH FROM (a-b)) / 60.0 -> (unix_timestamp(a) - unix_timestamp(b)) / 60.0
--   FIX 8: fe.EVENT_TYPE_ID::text -> CAST(fe.EVENT_TYPE_ID AS STRING)
--   FIX 9: cashbox_daily CTE -- 3 column bugs fixed:
--             DATE_KEY (does not exist) -> TRANSIT_DAY_KEY  (decimal 8,0 YYYYMMDD)
--             TO_DATE(DATE_KEY::text, 'YYYYMMDD')::date
--               -> TO_DATE(CAST(ct.TRANSIT_DAY_KEY AS STRING), 'yyyyMMdd')
--             TRANSACTION_TYPE (does not exist) -> CASHBOX_TYPE_ID-based features:
--               Type 1 = Bill Cashbox, Type 2 = Coin Cashbox,
--               Type 5 = Bus Cashbox (VALIDATOR farebox)
--             cashbox_jam_count  -> bill_cashbox_events  (TYPE_ID = 1)
--             cashbox_full_count -> coin_cashbox_events  (TYPE_ID = 2)
--             cashbox_empty_count -> bus_cashbox_events  (TYPE_ID = 5)
--             Added: total_cash_value_cents, total_dump_count
--   FIX 10: bronze.ncs_cashbox_tracking -> mars_dev.bronze.ncs_stage_cashbox_tracking
--   FIX 11: NULL::text -> CAST(NULL AS STRING)
--   FIX 12: silver./gold. prefixes -> mars_dev.silver. / mars_dev.gold.
--   FIX 13: CREATE INDEX (x6) -> removed (not supported on Delta); use OPTIMIZE/ZORDER
--   FIX 14: transit_day >= 2024-01-01 added to fault_events (ML training window)
--
-- Expected output: ~1.06M rows (V06b: VALIDATOR 563K + GATE 255K + TVM 245K)
-- =============================================================================

DROP TABLE IF EXISTS mars_dev.gold.device_ps2_chains;

CREATE TABLE mars_dev.gold.device_ps2_chains AS
WITH fault_events AS (
    -- FIX 2: fault-onset filter replaces severity=WARN/CRITICAL (which gave near-zero rows)
    -- EVENT_STATE_TYPE_NAME='Set' = fault onset only; excludes Clear/Automatic Clear (recovery)
    -- component_subsystem exclusion removes Emp Logon (SYSTEM) + Volt Drop (COMMS) noise
    SELECT
        dee.DEVICE_ID,
        dee.DEVICE_KEY,
        dee.mars_device_category,
        dee.transit_day,
        dee.EVENT_DTM,
        dee.DW_DEVICE_EVENT_ID,
        dee.component_subsystem,
        dee.severity,
        dee.EVENT_TYPE_ID,
        dee.EVENT_TYPE_NAME,
        dee.EVENT_STATE_TYPE_NAME,
        dee.COMPONENT_TYPE_NAME,
        dee.COMPONENT_SERIAL_NBR,
        dee.is_hardware_oos_event,
        ROW_NUMBER() OVER (
            PARTITION BY dee.DEVICE_ID, dee.transit_day
            ORDER BY dee.EVENT_DTM
        ) AS event_seq
    FROM mars_dev.silver.device_event_enriched dee
    WHERE dee.mars_device_category IN ('TVM','GATE','VALIDATOR')
      AND dee.is_hardware_oos_event = TRUE
      AND dee.EVENT_STATE_TYPE_NAME = 'Set'
      AND dee.transit_day >= '2024-01-01'
),
days_with_cascade AS (
    SELECT DEVICE_ID, transit_day, COUNT(*) AS fault_event_count
    FROM fault_events
    GROUP BY DEVICE_ID, transit_day
    HAVING COUNT(*) >= 2
),
chain_agg AS (
    SELECT
        fe.DEVICE_ID,
        -- DEVICE_KEY removed from GROUP BY: BMV devices have N DEVICE_KEY values per day
        -- (bus-assignment changes in S16). fault_events.event_seq already partitions by
        -- (DEVICE_ID, transit_day), so the chain is already cross-bus-assignment.
        -- MAX picks any one DEVICE_KEY for the downstream inc24 join (VALIDATOR has 0% S24
        -- coverage, so the specific key doesn't affect results).
        MAX(fe.DEVICE_KEY)  AS DEVICE_KEY,
        fe.mars_device_category,
        fe.transit_day,
        -- FIX 3: STRING_AGG(... ORDER BY ...) -> struct sort (Spark SQL)
        -- struct(EVENT_DTM, val) sorts by EVENT_DTM; transform extracts val field
        array_join(
            transform(
                array_sort(collect_list(struct(fe.EVENT_DTM AS dt, fe.component_subsystem AS val))),
                x -> x.val
            ), '->'
        )                                                                    AS subsystem_chain,
        array_join(
            transform(
                array_sort(collect_list(struct(fe.EVENT_DTM AS dt, CAST(fe.EVENT_TYPE_ID AS STRING) AS val))),
                x -> x.val
            ), '->'
        )                                                                    AS event_type_chain,
        array_join(
            transform(
                array_sort(collect_list(struct(fe.EVENT_DTM AS dt, fe.severity AS val))),
                x -> x.val
            ), '->'
        )                                                                    AS severity_chain,
        COUNT(*)                                                             AS chain_length,
        MIN(fe.EVENT_DTM)                                                    AS chain_start_dtm,
        MAX(fe.EVENT_DTM)                                                    AS chain_end_dtm,
        -- FIX 7: EXTRACT EPOCH -> unix_timestamp
        (unix_timestamp(MAX(fe.EVENT_DTM)) - unix_timestamp(MIN(fe.EVENT_DTM))) / 60.0
                                                                             AS chain_span_min,
        -- FIX 4: FILTER(WHERE event_seq=1) -> MAX(CASE WHEN ...)
        MAX(CASE WHEN fe.event_seq = 1               THEN fe.component_subsystem END)
                                                                             AS first_subsystem,
        -- FIX 5: FILTER with correlated subquery -> dc.fault_event_count (already computed)
        MAX(CASE WHEN fe.event_seq = dc.fault_event_count
                 THEN fe.component_subsystem END)                            AS last_subsystem,
        COUNT(CASE WHEN fe.severity = 'CRITICAL' THEN 1 END)                AS critical_in_chain,
        COUNT(CASE WHEN fe.is_hardware_oos_event = TRUE THEN 1 END)          AS oos_in_chain,
        -- FIX 6: ILIKE on STRING_AGG -> subsystem presence flags (more reliable)
        -- has_cash_cascade: BHU AND CHU both appear on same device-day (bill+coin jam cascade)
        (MAX(CASE WHEN fe.component_subsystem = 'BHU' THEN 1 ELSE 0 END)
         + MAX(CASE WHEN fe.component_subsystem = 'CHU' THEN 1 ELSE 0 END)) >= 2
                                                                             AS has_cash_cascade,
        MAX(CASE WHEN fe.component_subsystem = 'PRINTER'    THEN 1 ELSE 0 END) = 1
                                                                             AS has_printer_in_chain,
        MAX(CASE WHEN fe.component_subsystem = 'GATE_MECH'  THEN 1 ELSE 0 END) = 1
                                                                             AS has_gate_mech_in_chain,
        MAX(CASE WHEN fe.component_subsystem = 'CSC_READER' THEN 1 ELSE 0 END) = 1
                                                                             AS has_csc_reader_in_chain,
        -- Additional subsystem flags confirmed in top-OOS list (V04)
        MAX(CASE WHEN fe.component_subsystem = 'DOPP'       THEN 1 ELSE 0 END) = 1
                                                                             AS has_dopp_in_chain,
        MAX(CASE WHEN fe.component_subsystem = 'SCRST'      THEN 1 ELSE 0 END) = 1
                                                                             AS has_scrst_in_chain,
        COUNT(DISTINCT fe.component_subsystem)                               AS distinct_subsystems
    FROM fault_events fe
    JOIN days_with_cascade dc
        ON dc.DEVICE_ID   = fe.DEVICE_ID
       AND dc.transit_day = fe.transit_day
    GROUP BY fe.DEVICE_ID, fe.mars_device_category, fe.transit_day
),
-- FIX 9+10: cashbox_daily -- DATE_KEY->TRANSIT_DAY_KEY, TRANSACTION_TYPE->CASHBOX_TYPE_ID
-- ncs_stage_cashbox_tracking confirmed schema (V07a): TRANSIT_DAY_KEY decimal(8,0) YYYYMMDD
-- CASHBOX_TYPE_ID: 1=Bill CBX, 2=Coin CBX, 3=Coin Hopper, 5=Bus Cashbox, 6=Mobile Safe
cashbox_daily AS (
    SELECT
        ct.DEVICE_ID,
        TO_DATE(CAST(ct.TRANSIT_DAY_KEY AS STRING), 'yyyyMMdd')              AS cbx_date,
        COUNT(*)                                                             AS cashbox_events,
        COUNT(CASE WHEN ct.CASHBOX_TYPE_ID = 1 THEN 1 END)                  AS bill_cashbox_events,
        COUNT(CASE WHEN ct.CASHBOX_TYPE_ID = 2 THEN 1 END)                  AS coin_cashbox_events,
        COUNT(CASE WHEN ct.CASHBOX_TYPE_ID = 5 THEN 1 END)                  AS bus_cashbox_events,
        SUM(COALESCE(CAST(ct.VALUE_CASH AS BIGINT), 0))                     AS total_cash_value_cents,
        SUM(COALESCE(CAST(ct.DUMP_COUNT AS INT), 0))                        AS total_dump_count
    FROM mars_dev.bronze.ncs_stage_cashbox_tracking ct
    WHERE ct.TRANSIT_DAY_KEY IS NOT NULL
    GROUP BY ct.DEVICE_ID, TO_DATE(CAST(ct.TRANSIT_DAY_KEY AS STRING), 'yyyyMMdd')
)
SELECT
    ca.DEVICE_ID,
    ca.DEVICE_KEY,
    ca.transit_day,
    ca.mars_device_category,
    ca.subsystem_chain,
    ca.event_type_chain,
    ca.severity_chain,
    ca.chain_length,
    ca.chain_start_dtm,
    ca.chain_end_dtm,
    ca.chain_span_min,
    ca.first_subsystem,
    ca.last_subsystem,
    ca.critical_in_chain,
    ca.oos_in_chain,
    ca.distinct_subsystems,
    (ca.chain_length >= 2)                    AS has_cascade,
    ca.has_cash_cascade,
    ca.has_printer_in_chain,
    ca.has_gate_mech_in_chain,
    ca.has_csc_reader_in_chain,
    ca.has_dopp_in_chain,
    ca.has_scrst_in_chain,
    -- Cashbox context (TVM/VALIDATOR only; 0 for GATE)
    -- FIX 9: cashbox_jam/full/empty_count -> type-based counts (TRANSACTION_TYPE absent)
    COALESCE(cb.cashbox_events, 0)            AS cashbox_events,
    COALESCE(cb.bill_cashbox_events, 0)       AS bill_cashbox_events,
    COALESCE(cb.coin_cashbox_events, 0)       AS coin_cashbox_events,
    COALESCE(cb.bus_cashbox_events, 0)        AS bus_cashbox_events,
    COALESCE(cb.total_cash_value_cents, 0)    AS total_cash_value_cents,
    COALESCE(cb.total_dump_count, 0)          AS total_dump_count,
    -- Device context
    dd.DEVICE_NAME,
    dd.FACILITY_ID,
    dd.FACILITY_NAME,
    dd.OPERATOR_ID,
    dd.OPERATOR_NAME,
    dd.DEVICE_SERIAL_NUMBER,
    dd.DEVICE_CONTROL_GROUP_TYPE_NAME,
    dd.BUS_ID,
    -- Gate-bank cascade features (R2-14): group GATE devices at same turnstile bank
    -- TRANSIT_ARRAY_ID + ARRAY_POSITION identify physically co-located gates -> cascade correlation
    -- NULL for TVM/VALIDATOR (not part of a gate array)
    dd.TRANSIT_ARRAY_ID,
    dd.ARRAY_POSITION,
    dd.FARE_CONTROL_AREA,
    dd.TURNSTILE_DEVICE_TYPE,
    dd.TURNSTILE_DEVICE_NUMBER,
    -- CI dependency features UNAVAILABLE (SVN_STAGE = 0 rows)
    CAST(NULL AS STRING)                      AS ci_related_devices,
    CAST(NULL AS STRING)                      AS shared_facility_chain,
    FALSE                                     AS svn_ci_data_available,
    -- Incident history context (S24 -- R4 2026-07-07)
    -- 5 cols selected: chain severity depends on device maintenance history and recency
    -- count cols: COALESCE 0; MTTR and priority: NULL kept (no history is distinct from zero)
    COALESCE(inc24.incident_count_7d_past,    0) AS incident_count_7d_past,
    COALESCE(inc24.chargeable_count_30d_past, 0) AS chargeable_count_30d_past,
    inc24.avg_mttr_30d_past,
    inc24.min_priority_30d_past,
    inc24.days_since_last_incident
FROM chain_agg ca
LEFT JOIN mars_dev.silver.dim_device dd
    ON dd.DEVICE_ID = ca.DEVICE_ID AND dd.is_current = TRUE
LEFT JOIN cashbox_daily cb
    ON cb.DEVICE_ID = ca.DEVICE_ID AND cb.cbx_date = ca.transit_day
-- R4 (2026-07-07): S24 incident features — join on DEVICE_KEY + transit_day
LEFT JOIN mars_dev.silver.device_incident_features_daily inc24
    ON inc24.DEVICE_KEY  = ca.DEVICE_KEY
   AND inc24.transit_day = ca.transit_day;

-- Post-build:
-- OPTIMIZE mars_dev.gold.device_ps2_chains ZORDER BY (DEVICE_ID, transit_day);
--
-- Post-build verification:
-- SELECT
--     COUNT(*)                                                        AS total_rows,
--     COUNT(DISTINCT DEVICE_ID)                                      AS distinct_devices,
--     COUNT(DISTINCT mars_device_category)                           AS categories,
--     SUM(CASE WHEN has_cash_cascade         THEN 1 ELSE 0 END)     AS cash_cascades,
--     SUM(CASE WHEN has_gate_mech_in_chain   THEN 1 ELSE 0 END)     AS gate_mech_chains,
--     SUM(CASE WHEN has_dopp_in_chain        THEN 1 ELSE 0 END)     AS dopp_chains,
--     ROUND(AVG(chain_length), 1)                                    AS avg_chain_length,
--     ROUND(AVG(chain_span_min), 1)                                  AS avg_chain_span_min,
--     ROUND(AVG(distinct_subsystems), 2)                             AS avg_distinct_subsystems
-- FROM mars_dev.gold.device_ps2_chains;
-- Expected: ~1.06M rows, avg_chain_length 3-5, distinct_subsystems 1-3
