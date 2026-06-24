-- =============================================================================
-- gold.device_ps1_daily
-- PS1 — Predictive Failure: daily feature table for TVM / GATE / VALIDATOR
-- Grain: (device_id, transit_day)
-- Target: will_fail_7d — 1 if device has a real outage (duration_min > 0)
--         within the next 7 calendar days
-- Training window: transit_day >= 2024-01-01 (pre-2024 legacy data excluded)
--
-- Sources (silver):
--   S06  mars_dev.silver.dim_device
--   S16  mars_dev.silver.device_event_enriched
--   S18  mars_dev.silver.device_outage
--   S10  mars_dev.silver.metric_daily          (26% device coverage — DEVICE_ID sparse)
--   S12  mars_dev.silver.kpi_daily             (10% coverage — pre-aggregated to 1 row/device/day)
--   S13  mars_dev.silver.tap_event_daily        (27% coverage — VALIDATOR+GATE only; TVM=0)
--   S14  mars_dev.silver.tvm_sale_daily         (3% coverage — TVM only)
--
-- Fixes applied 2026-06-19 (pre-validation run):
--   FIX 1: READER excluded from mars_device_category filter (0 events confirmed)
--   FIX 2: kpi_daily pre-aggregated to 1 row per (DEVICE_ID, transit_day) —
--           5.37 KPI rows per device-day caused fan-out without this CTE
--   FIX 3: kpi.AVAILABILITY_PCT and kpi.FAULT_COUNT removed — do not exist in S12
--           (removed as BUG 18/19 during S12 build)
--   FIX 4: metric_daily columns renamed to actual S10 names:
--             metric_800_txn_count → m401_daily_txn_count
--             metric_800_delta     → m401_txn_count_delta
--             metric_401_fault_count  removed (does not exist)
--             counter_reset_flag   → volume_drop_flag
--           METRIC_ID 800/810 = 0 rows for Chicago; only METRIC_ID 401 present
--   FIX 5: tap_event_daily subquery adds transit_day <= CURRENT_DATE() —
--           bronze ABP_TAP has data entry rows to 2032-12-14
--   FIX 6: tap.avg_timing_ms / p95_timing_ms / has_slow_transactions removed —
--           ABP_USE_TRAN_TIMING_DATA is PATH_NOT_FOUND; replaced with peak_hour_tap_count
--   FIX 7: spine filtered to transit_day >= 2024-01-01 (ML training window)
--   FIX 8: will_fail_7d uses duration_min > 0 NOT severity IN ('CRITICAL','WARN') —
--           severity filter produced only 17 positive labels (confirmed pre-validation V08)
--           explode-backward label approach used (equi-join, faster than EXISTS/range join)
--   FIX 9: All silver./gold. prefixes → mars_dev.silver. / mars_dev.gold.
--   FIX 10: CREATE INDEX removed (not supported on Delta) → OPTIMIZE/ZORDER after build
--
-- Device scope (updated 2026-06-24):
--   TVM:       4,512 current devices — BTP/AVM/EVM/TVM (RTL/POS moved to OTHER 2026-06-24)
--   GATE:      2,342 current devices — RVG/SAG/HBG/TT_/TTC/TWA/TEX
--   VALIDATOR: 6,734 current devices — BMV/FBX/BTP PortableFarebox
--   NOTE: READER removed 2026-06-23 — reader is a component, not a standalone device
--   NOTE: RTL+POS moved to OTHER 2026-06-24 per Michael R2-6 (no availability/WO records)
--   Total spine: ~13,588 TVM+GATE+VALIDATOR
--
-- Michael R2 additions (2026-06-24):
--   R2-1: is_chargeable + failure_level from S18 device_outage (failure_level > 0 = real hardware fault)
--   R2-14: TRANSIT_ARRAY_ID + ARRAY_POSITION from S06 dim_device (GATE devices — PS2 gate-bank cascade)
-- =============================================================================

DROP TABLE IF EXISTS mars_dev.gold.device_ps1_daily;

CREATE TABLE mars_dev.gold.device_ps1_daily AS
WITH all_devices AS (
    SELECT
        DEVICE_ID, DEVICE_KEY, DEVICE_NAME, FACILITY_ID, FACILITY_NAME,
        OPERATOR_ID, OPERATOR_NAME, DEVICE_SERIAL_NUMBER,
        mars_device_category,
        DEVICE_CONTROL_GROUP_TYPE_NAME,
        BUS_ID,
        TRANSIT_MODE_NAME,
        -- Gate-bank cascade context (R2-14: GATE devices — PS2 array grouping)
        TRANSIT_ARRAY_ID,
        ARRAY_POSITION,
        FARE_CONTROL_AREA
    FROM mars_dev.silver.dim_device
    WHERE is_current = TRUE
      AND mars_device_category IN ('TVM','GATE','VALIDATOR')
      -- READER excluded: 0 events confirmed in device_event_enriched
),
event_daily AS (
    SELECT
        dee.DEVICE_ID,
        dee.transit_day,
        COUNT(*)                                                              AS event_count,
        SUM(CASE WHEN dee.severity = 'CRITICAL'              THEN 1 ELSE 0 END) AS critical_events,
        SUM(CASE WHEN dee.component_subsystem = 'BHU'        THEN 1 ELSE 0 END) AS bhu_events,
        SUM(CASE WHEN dee.component_subsystem = 'CHU'        THEN 1 ELSE 0 END) AS chu_events,
        SUM(CASE WHEN dee.component_subsystem = 'PRINTER'    THEN 1 ELSE 0 END) AS printer_events,
        SUM(CASE WHEN dee.component_subsystem = 'GATE_MECH'  THEN 1 ELSE 0 END) AS gate_mech_events,
        SUM(CASE WHEN dee.component_subsystem = 'CSC_READER' THEN 1 ELSE 0 END) AS csc_reader_events,
        SUM(CASE WHEN dee.component_subsystem = 'BANKCARD'   THEN 1 ELSE 0 END) AS bankcard_events,
        SUM(CASE WHEN dee.component_subsystem = 'SYSTEM'     THEN 1 ELSE 0 END) AS system_events,
        SUM(CASE WHEN dee.component_subsystem = 'SCRST'      THEN 1 ELSE 0 END) AS scrst_events,
        SUM(CASE WHEN dee.component_subsystem = 'COMMS'      THEN 1 ELSE 0 END) AS comms_events,
        SUM(CASE WHEN dee.is_oos_event = TRUE                THEN 1 ELSE 0 END) AS oos_event_count,
        -- hardware_oos_count: hardware-only OOS fault onsets.
        -- is_hardware_oos_event excludes commanded codes (106/110/151/208/519/1603/1604) at source.
        -- NOT IN ('SYSTEM','COMMS') dropped — commanded codes were the noise source; excluded precisely via S07.
        SUM(CASE WHEN dee.is_hardware_oos_event = TRUE
                  AND dee.EVENT_STATE_TYPE_NAME = 'Set'              THEN 1 ELSE 0 END) AS hardware_oos_count
    FROM mars_dev.silver.device_event_enriched dee
    WHERE dee.mars_device_category IN ('TVM','GATE','VALIDATOR')
    GROUP BY dee.DEVICE_ID, dee.transit_day
),
outage_daily AS (
    SELECT
        do_.DEVICE_ID,
        do_.transit_day,
        COUNT(*)                                                              AS outage_count,
        SUM(COALESCE(do_.duration_min, 0))                                    AS total_outage_min,
        MAX(COALESCE(do_.duration_min, 0))                                    AS max_outage_min,
        -- Chargeable outages: failure_level > 0 = real hardware failure, SLA-chargeable (R2-1)
        SUM(CASE WHEN do_.is_chargeable = TRUE THEN 1 ELSE 0 END)            AS chargeable_outage_count,
        SUM(CASE WHEN do_.is_chargeable = TRUE
                 THEN COALESCE(do_.duration_min, 0) ELSE 0 END)              AS chargeable_outage_min,
        MAX(COALESCE(do_.failure_level, 0))                                   AS max_failure_level
    FROM mars_dev.silver.device_outage do_
    WHERE do_.mars_device_category IN ('TVM','GATE','VALIDATOR')
      AND do_.duration_min > 0
    GROUP BY do_.DEVICE_ID, do_.transit_day
),
rolling AS (
    SELECT
        ed.DEVICE_ID,
        ed.transit_day,
        SUM(ed.event_count)                   OVER w7  AS events_7d,
        SUM(ed.critical_events)               OVER w7  AS critical_events_7d,
        SUM(ed.comms_events)                  OVER w7  AS comms_events_7d,
        SUM(ed.oos_event_count)               OVER w7  AS oos_events_7d,
        SUM(ed.hardware_oos_count)            OVER w7  AS hardware_oos_events_7d,
        SUM(COALESCE(od.outage_count, 0))     OVER w7  AS outages_7d,
        SUM(COALESCE(od.total_outage_min, 0)) OVER w7  AS outage_min_7d,
        CASE
            WHEN SUM(COALESCE(od.total_outage_min, 0)) OVER w7 > 0
            THEN ROUND((1 - SUM(COALESCE(od.total_outage_min,0)) OVER w7 / (7 * 1440.0)) * 100, 2)
            ELSE 100.0
        END                                            AS availability_pct_7d,
        SUM(ed.event_count)                   OVER w30 AS events_30d,
        SUM(ed.critical_events)               OVER w30 AS critical_events_30d
    FROM event_daily ed
    LEFT JOIN outage_daily od
        ON od.DEVICE_ID = ed.DEVICE_ID AND od.transit_day = ed.transit_day
    WINDOW
        w7  AS (PARTITION BY ed.DEVICE_ID ORDER BY ed.transit_day ROWS BETWEEN 6  PRECEDING AND CURRENT ROW),
        w30 AS (PARTITION BY ed.DEVICE_ID ORDER BY ed.transit_day ROWS BETWEEN 29 PRECEDING AND CURRENT ROW)
),
-- FIX 2/3: kpi_daily pre-aggregated — 5.37 rows/device-day without this causes fan-out
-- AVAILABILITY_PCT and FAULT_COUNT removed (do not exist in S12 — BUG 18/19)
kpi_device_daily AS (
    SELECT
        kd.DEVICE_ID,
        kd.transit_day,
        ROUND(AVG(kd.KPI_VALUE), 4)                                       AS avg_kpi_value,
        MAX(kd.KPI_VALUE)                                                 AS max_kpi_value,
        COUNT(DISTINCT kd.KPI_ID)                                         AS kpi_count,
        SUM(CASE WHEN kd.meets_target = TRUE  THEN 1 ELSE 0 END)         AS kpi_targets_met,
        SUM(CASE WHEN kd.meets_target = FALSE THEN 1 ELSE 0 END)         AS kpi_targets_missed
    FROM mars_dev.silver.kpi_daily kd
    WHERE kd.DEVICE_ID IS NOT NULL
    GROUP BY kd.DEVICE_ID, kd.transit_day
),
-- FIX: tvm_sale_daily — column names confirmed against S14 SQL
tvm_sales AS (
    SELECT
        tsd.DEVICE_ID,
        tsd.transit_day,
        tsd.daily_sales_count,
        tsd.error_txn_count,
        tsd.error_txn_rate_pct,
        tsd.cash_sales_pct,
        tsd.total_revenue_cents,
        tsd.sales_active_hours,
        AVG(tsd.daily_sales_count) OVER (
            PARTITION BY tsd.DEVICE_ID ORDER BY tsd.transit_day
            ROWS BETWEEN 6 PRECEDING AND CURRENT ROW
        ) AS sales_7d_avg,
        CASE
            WHEN AVG(tsd.daily_sales_count) OVER (
                PARTITION BY tsd.DEVICE_ID ORDER BY tsd.transit_day
                ROWS BETWEEN 6 PRECEDING AND 1 PRECEDING
            ) > 0
            AND tsd.daily_sales_count < 0.5 * AVG(tsd.daily_sales_count) OVER (
                PARTITION BY tsd.DEVICE_ID ORDER BY tsd.transit_day
                ROWS BETWEEN 6 PRECEDING AND 1 PRECEDING
            )
            THEN TRUE ELSE FALSE
        END AS sales_decline_flag
    FROM mars_dev.silver.tvm_sale_daily tsd
),
-- FIX 8: will_fail_7d — explode-backward label; equi-join (faster than EXISTS/range join)
-- duration_min > 0 replaces severity IN ('CRITICAL','WARN') which produced only 17 positives
outage_label_days AS (
    SELECT DISTINCT
        do2.DEVICE_ID,
        DATE_ADD(do2.transit_day, -seq.n) AS label_day
    FROM mars_dev.silver.device_outage do2
    CROSS JOIN (
        SELECT 1 AS n UNION ALL SELECT 2 UNION ALL SELECT 3 UNION ALL
        SELECT 4 UNION ALL SELECT 5 UNION ALL SELECT 6 UNION ALL SELECT 7
    ) seq
    WHERE do2.duration_min > 0
      AND do2.mars_device_category IN ('TVM','GATE','VALIDATOR')
),
-- FIX 7: spine filtered to >= 2024-01-01 (ML training window; excludes legacy pre-2024 rows)
spine AS (
    SELECT DISTINCT ad.DEVICE_ID, ed.transit_day
    FROM all_devices ad
    JOIN event_daily ed ON ed.DEVICE_ID = ad.DEVICE_ID
    WHERE ed.transit_day >= '2024-01-01'
)
SELECT
    sp.DEVICE_ID,
    sp.transit_day,
    ad.DEVICE_KEY,
    ad.DEVICE_NAME,
    ad.FACILITY_ID,
    ad.FACILITY_NAME,
    ad.OPERATOR_ID,
    ad.OPERATOR_NAME,
    ad.DEVICE_SERIAL_NUMBER,
    ad.mars_device_category,
    ad.DEVICE_CONTROL_GROUP_TYPE_NAME,
    ad.BUS_ID,
    ad.TRANSIT_MODE_NAME,
    -- Gate-bank cascade context (R2-14: GATE devices for PS2 chain analysis; NULL for TVM/VALIDATOR)
    ad.TRANSIT_ARRAY_ID,
    ad.ARRAY_POSITION,
    ad.FARE_CONTROL_AREA,
    -- Daily event features
    COALESCE(ed.event_count, 0)             AS event_count,
    COALESCE(ed.critical_events, 0)         AS critical_events,
    COALESCE(ed.bhu_events, 0)              AS bhu_events,
    COALESCE(ed.chu_events, 0)              AS chu_events,
    COALESCE(ed.printer_events, 0)          AS printer_events,
    COALESCE(ed.gate_mech_events, 0)        AS gate_mech_events,
    COALESCE(ed.csc_reader_events, 0)       AS csc_reader_events,
    COALESCE(ed.bankcard_events, 0)         AS bankcard_events,
    COALESCE(ed.system_events, 0)           AS system_events,
    COALESCE(ed.scrst_events, 0)            AS scrst_events,
    COALESCE(ed.comms_events, 0)            AS comms_events,
    COALESCE(ed.oos_event_count, 0)         AS oos_event_count,
    COALESCE(ed.hardware_oos_count, 0)     AS hardware_oos_count,
    -- Outage features (real outages only; duration_min > 0 excludes 52.2% instant-clear rows)
    COALESCE(od.outage_count, 0)            AS outage_count,
    COALESCE(od.total_outage_min, 0)        AS total_outage_min,
    COALESCE(od.max_outage_min, 0)          AS max_outage_min,
    -- Chargeable outage features (R2-1: failure_level > 0 = real hardware fault, SLA-chargeable)
    COALESCE(od.chargeable_outage_count, 0) AS chargeable_outage_count,
    COALESCE(od.chargeable_outage_min, 0)   AS chargeable_outage_min,
    COALESCE(od.max_failure_level, 0)       AS max_failure_level,
    -- Rolling window features
    COALESCE(rw.events_7d, 0)               AS events_7d,
    COALESCE(rw.critical_events_7d, 0)      AS critical_events_7d,
    COALESCE(rw.comms_events_7d, 0)         AS comms_events_7d,
    COALESCE(rw.oos_events_7d, 0)           AS oos_events_7d,
    COALESCE(rw.hardware_oos_events_7d, 0) AS hardware_oos_events_7d,
    COALESCE(rw.outages_7d, 0)              AS outages_7d,
    COALESCE(rw.outage_min_7d, 0)           AS outage_min_7d,
    COALESCE(rw.availability_pct_7d, 100.0) AS availability_pct_7d,
    COALESCE(rw.events_30d, 0)              AS events_30d,
    COALESCE(rw.critical_events_30d, 0)     AS critical_events_30d,
    -- Tap features (VALIDATOR + GATE only; TVM=0 rows confirmed)
    -- avg_timing_ms / p95_timing_ms / has_slow_transactions removed (timing table PATH_NOT_FOUND)
    COALESCE(tap.tap_count, 0)              AS tap_count,
    COALESCE(tap.unique_cards, 0)           AS unique_cards,
    COALESCE(tap.tap_reject_rate_pct, 0)    AS tap_reject_rate_pct,
    COALESCE(tap.peak_hour_tap_count, 0)    AS peak_hour_tap_count,
    -- KPI features (pre-aggregated; AVAILABILITY_PCT / FAULT_COUNT removed — not in S12)
    kp.avg_kpi_value,
    kp.max_kpi_value,
    COALESCE(kp.kpi_count, 0)              AS kpi_count,
    COALESCE(kp.kpi_targets_met, 0)        AS kpi_targets_met,
    COALESCE(kp.kpi_targets_missed, 0)     AS kpi_targets_missed,
    -- Metric features (S10 actual column names; METRIC_ID 800/810 absent in Chicago)
    COALESCE(md.m401_daily_txn_count, 0)   AS metric_txn_count,
    COALESCE(md.m401_txn_count_delta, 0)   AS metric_txn_delta,
    COALESCE(md.m401_avg_txn_time_ms, 0)   AS metric_avg_txn_ms,
    COALESCE(md.m401_max_txn_time_ms, 0)   AS metric_max_txn_ms,
    COALESCE(md.volume_drop_flag, FALSE)   AS volume_drop_flag,
    -- TVM sales features (LEFT JOIN; 3.1% coverage; NULL/0 for GATE/VALIDATOR)
    COALESCE(ts.daily_sales_count, 0)      AS daily_sales_count,
    COALESCE(ts.error_txn_count, 0)        AS sales_error_txn_count,
    COALESCE(ts.error_txn_rate_pct, 0)     AS sales_error_rate_pct,
    COALESCE(ts.cash_sales_pct, 0)         AS cash_sales_pct,
    COALESCE(ts.total_revenue_cents, 0)    AS daily_revenue_cents,
    COALESCE(ts.sales_active_hours, 0)     AS sales_active_hours,
    COALESCE(ts.sales_7d_avg, 0)           AS sales_7d_avg,
    COALESCE(ts.sales_decline_flag, FALSE) AS sales_decline_flag,
    -- TARGET: 1 if device has a real outage within next 7 days
    -- V08 pre-validation confirmed: severity=CRITICAL/WARN = 17 positives (broken)
    --                               duration_min > 0     = 3.66M label pairs (correct)
    CASE WHEN old.DEVICE_ID IS NOT NULL THEN 1 ELSE 0 END AS will_fail_7d

FROM spine sp
JOIN all_devices ad                    ON ad.DEVICE_ID  = sp.DEVICE_ID
LEFT JOIN event_daily ed               ON ed.DEVICE_ID  = sp.DEVICE_ID AND ed.transit_day = sp.transit_day
LEFT JOIN outage_daily od              ON od.DEVICE_ID  = sp.DEVICE_ID AND od.transit_day = sp.transit_day
LEFT JOIN rolling rw                   ON rw.DEVICE_ID  = sp.DEVICE_ID AND rw.transit_day = sp.transit_day
LEFT JOIN (
    -- FIX 5: future-date filter — tap_event_daily has rows to 2032-12-14 (bronze data error)
    SELECT DEVICE_ID, transit_day, tap_count, unique_cards,
           tap_reject_rate_pct, peak_hour_tap_count
    FROM mars_dev.silver.tap_event_daily
    WHERE transit_day <= CURRENT_DATE()
) tap                                  ON tap.DEVICE_ID  = sp.DEVICE_ID AND tap.transit_day  = sp.transit_day
LEFT JOIN kpi_device_daily kp          ON kp.DEVICE_ID   = sp.DEVICE_ID AND kp.transit_day   = sp.transit_day
LEFT JOIN mars_dev.silver.metric_daily md  ON md.DEVICE_ID  = sp.DEVICE_ID AND md.transit_day = sp.transit_day
LEFT JOIN tvm_sales ts                 ON ts.DEVICE_ID   = sp.DEVICE_ID AND ts.transit_day   = sp.transit_day
LEFT JOIN outage_label_days old        ON old.DEVICE_ID  = sp.DEVICE_ID AND old.label_day    = sp.transit_day;

-- Post-build:
-- OPTIMIZE mars_dev.gold.device_ps1_daily ZORDER BY (DEVICE_ID, transit_day);
--
-- Post-build verification:
-- SELECT
--     COUNT(*)                                                          AS total_rows,
--     COUNT(DISTINCT DEVICE_ID)                                        AS distinct_devices,
--     MIN(transit_day)                                                 AS earliest_day,
--     MAX(transit_day)                                                 AS latest_day,
--     SUM(will_fail_7d)                                                AS positive_labels,
--     ROUND(SUM(will_fail_7d) * 100.0 / COUNT(*), 2)                 AS positive_rate_pct,
--     ROUND(SUM(CASE WHEN metric_txn_count > 0 THEN 1 ELSE 0 END) * 100.0 / COUNT(*), 1) AS metric_coverage_pct,
--     ROUND(SUM(CASE WHEN avg_kpi_value IS NOT NULL THEN 1 ELSE 0 END) * 100.0 / COUNT(*), 1) AS kpi_coverage_pct,
--     ROUND(SUM(CASE WHEN tap_count > 0 THEN 1 ELSE 0 END) * 100.0 / COUNT(*), 1) AS tap_coverage_pct
-- FROM mars_dev.gold.device_ps1_daily;
-- Expected: positive_rate_pct 20-60%, metric_coverage ≈ 26%, kpi_coverage ≈ 10%, tap_coverage ≈ 27%
