-- =============================================================================
-- silver.kpi_daily
-- Daily KPI metrics per device, enriched with KPI definitions and device context
--
-- Sources (mars_dev.bronze catalog):
--   EDW.KPI_DETAIL_EVENTS_BY_DAY (1,220,970 rows, 23 cols) -> mars_dev.bronze.edw_kpi_detail_events_by_day
--   EDW.KPI                      (60 rows,  13 cols)        -> mars_dev.bronze.edw_kpi
--   EDW.KPI_RULES                (117 rows,  7 cols)        -> mars_dev.bronze.edw_kpi_rules
--   EDW.KPI_TARGET               (216 rows,  6 cols)        -> mars_dev.bronze.edw_kpi_target
--   EDW.KPI_SUMMARY_BY_DAY       (221,465 rows, 5 cols)     -> mars_dev.bronze.edw_kpi_summary_by_day
--   CTA.KPI_AGENCY_MAP           (92 rows,   4 cols)        -> mars_dev.bronze.cta_kpi_agency_map
--   CTA.SLDC_MONTHLY_SUMMARY     (12,788 rows, 16 cols)     -> mars_dev.bronze.cta_sldc_monthly_summary
--   NOTE: CTA.KPI_TVM_DATE_TABLE - PATH_NOT_FOUND; excluded from pipeline
--
-- Notes:
--   - TRANSIT_DAY_KEY: all 8-digit YYYYMMDD (validated 2026-06-15; len6=0, len8=1,220,970)
--   - KPI_TARGET has 4 bands per KPI_ID (A/B/C/D) - aggregated to (KPI_ID, KPI_BAND)
--     before range join (KPI_VALUE BETWEEN KPI_MINIMUM AND KPI_MAXIMUM); no fan-out
--   - KPI_AGENCY_MAP has up to 3 rows per KPI_ID - aggregated to 1 row per KPI_ID
--   - KPI_RULES KPI_ID is mixed type (numeric + alphanumeric "21.1P") - CAST to STRING
--   - meets_target = TRUE when KPI_VALUE falls within Band A (best performance tier)
--   - dim_device joined on DEVICE_ID (business key) for 98.7% match rate
--   - KPI_SUMMARY_BY_DAY has no OPERATOR_ID (5-col table) - joined on KPI_ID + day only
--
-- Validation run 2026-06-15:
--   Dry-run 2025-01-01 to 07: 837 raw = 837 enriched (no fan-out confirmed)
--   kpi_name=100%, target_band=100%, meets_target=29.5%,
--   dim_device=98.7%, sldc=100%, agency=100%
--
-- Bugs fixed from original (33 total):
--   BUG 1:  silver.kpi_daily          -> mars_dev.silver.kpi_daily
--   BUG 2:  TRANSIT_DAY_KEY::text     -> CAST(... AS STRING)
--   BUG 3:  TO_DATE(...)::date        -> TO_DATE(...) (already returns DATE)
--   BUG 4:  DATE_TRUNC('month',...)::date -> TRUNC(..., 'MM')
--   BUG 5:  bronze.kpi_detail_events_by_day -> parquet S3 path
--   BUG 6:  bronze.kpi                      -> parquet S3 path (inline in FROM)
--   BUG 7:  bronze.kpi_rules                -> parquet S3 path
--   BUG 8:  bronze.kpi_target               -> parquet S3 path
--   BUG 9:  bronze.kpi_summary_by_day       -> parquet S3 path
--   BUG 10: bronze.cta_kpi_agency_map       -> parquet S3 path
--   BUG 11: bronze.cta_kpi_tvm_date_table   -> PATH_NOT_FOUND; CTE + join removed
--   BUG 12: bronze.cta_sldc_monthly_summary -> parquet S3 path
--   BUG 13: silver.dim_device ON DEVICE_KEY -> mars_dev.silver.dim_device ON DEVICE_ID
--   BUG 14: CREATE INDEX -> not supported on Delta; OPTIMIZE/ZORDER comment only
--   BUG 15: kd.KPI_NUMERATOR    - column does not exist in actual 23-col schema; removed
--   BUG 16: kd.KPI_DENOMINATOR  - column does not exist; removed
--   BUG 17: kd.EVENT_COUNT      - column does not exist; removed
--   BUG 18: kd.FAULT_COUNT      - column does not exist; removed
--   BUG 19: kd.AVAILABILITY_PCT - column does not exist; removed
--           (actual 23 cols: TRANSIT_DAY_KEY, EVENT_ID, DEVICE_ID, DEVICE_TYPE_ID,
--            DEVICE_KEY, ARRAY_ID, ARRAY_SIZE, START_DTM, END_DTM, FAILURE_LEVEL,
--            FAULT_DESCRIPTION, EXCLUDED, KPI_VALUE, RELIEF_VALUE, INSERTED_DTM,
--            UPDATED_DTM, KPI_ID, DEVICE_TYPE_NAME, OPERATOR_ID, OPERATOR_NAME,
--            FACILITY_ID, FACILITY_NAME, ARRAY_POSITION)
--   BUG 20: k.DEVICE_TYPE_ID - not in KPI table; removed
--   BUG 21: k.FORMULA        - not in KPI table; removed
--           (actual KPI cols: KPI_SYSTEM, KPI_NAME, KPI_TYPE, UNITS, METRIC_CATEGORY_ID,
--            GROUPED, THRESHOLD, BASE_QTY, KPI_DESC, KPI_CATEGORY_ID, KPI_ID,
--            OPERATOR_ID, KPI_CRITICALITY)
--   BUG 22: kr.RULE_CONDITION - not in KPI_RULES; replaced with aggregated rule_count
--   BUG 23: kr.RULE_SEVERITY  - not in KPI_RULES; removed
--           (actual KPI_RULES cols: RULE_ID, EVENT_ID, METRIC_ID, DEVICE_TYPE_ID,
--            FAILURE_LEVEL, INSERTED_DTM, KPI_ID)
--   BUG 24: target_lookup CTE: TARGET_VALUE/TARGET_TYPE/EFFECTIVE_FROM_DTM/EFFECTIVE_TO_DTM
--           - none exist. Actual: KPI_BAND/KPI_MINIMUM/KPI_MAXIMUM/KPI_DEDUCTION_TYPE/
--           KPI_DEDUCTION_VALUE. Redesigned: range join + meets_target = (KPI_BAND = 'A')
--   BUG 25: ks.OPERATOR_ID in kpi_summary_base - does not exist (5-col table);
--           removed from join condition
--   BUG 26: am.AGENCY_ID       -> am.KPI_AGENCY_ID
--   BUG 27: am.AGENCY_NAME     -> am.KPI_AGENCY_NAME
--   BUG 28: am.KPI_AGENCY_CODE - does not exist; removed
--   BUG 29: sldc_monthly CTE: OPERATOR_ID/FACILITY_ID/MONTH_KEY/AVAILABILITY_TARGET/
--           AVAILABILITY_ACTUAL/TOTAL_REQUIRED_HOURS/TOTAL_AVAILABLE_HOURS/
--           TOTAL_FAULT_HOURS - none exist in SLDC_MONTHLY_SUMMARY.
--           Actual cols: MONTH_DTM, KPI_AGENCY_ID, KPI_ID, KPI_VALUE, BAND,
--           SLDC_PERCENT, ADJUSTED_SLDC_PERCENT. Full redesign.
--   BUG 30: kpi_rules fan-out (multiple rules per KPI_ID) -> GROUP BY KPI_ID
--   BUG 31: KPI_ID in kpi_rules is mixed type (numeric + "21.1P") -> CAST to STRING
--   BUG 32: agency_map fan-out (up to 3 rows per KPI_ID) -> GROUP BY KPI_ID
--   BUG 33: target_bands fan-out (4 bands per KPI_ID) -> GROUP BY KPI_ID, KPI_BAND
-- =============================================================================

DROP TABLE IF EXISTS mars_dev.silver.kpi_daily;

CREATE TABLE mars_dev.silver.kpi_daily AS
WITH kpi_detail_base AS (
    -- Grain: (DEVICE_ID, KPI_ID, TRANSIT_DAY_KEY, event instance) - NOT 1 row per device-KPI-day.
    -- Post-validation 2026-06-19: source has avg 1.74 events per (DEVICE_ID,KPI_ID,day).
    -- KPI_DETAIL_EVENTS_BY_DAY is an EVENTS table; multiple availability events can trigger
    -- the same KPI on the same device+day. Gold tables (G01 PS1) must pre-aggregate this
    -- table before joining to avoid 5.37× fan-out (confirmed pre-validation 2026-06-19).
    -- TRANSIT_DAY_KEY: all 8-digit YYYYMMDD (validated; no mixed-format CASE needed)
    SELECT
        kd.KPI_ID,
        kd.DEVICE_ID,
        kd.DEVICE_KEY,
        kd.TRANSIT_DAY_KEY,
        TO_DATE(CAST(kd.TRANSIT_DAY_KEY AS STRING), 'yyyyMMdd') AS transit_day,
        kd.OPERATOR_ID,
        kd.FACILITY_ID,
        kd.KPI_VALUE,
        kd.FAILURE_LEVEL,
        kd.FAULT_DESCRIPTION,
        kd.EXCLUDED,
        kd.RELIEF_VALUE,
        kd.ARRAY_ID,
        kd.ARRAY_SIZE,
        kd.ARRAY_POSITION,
        kd.START_DTM,
        kd.END_DTM,
        kd.DEVICE_TYPE_ID,
        kd.DEVICE_TYPE_NAME
    FROM mars_dev.bronze.edw_kpi_detail_events_by_day kd
    WHERE kd.EXCLUDED = 0   -- Gap 1 fix: exclude planned/administrative outage exclusions
),
kpi_summary_base AS (
    -- 5-col table: TRANSIT_DAY_KEY, KPI_VALUE, KPI_QUANTITY, SUMM_DTM, KPI_ID
    -- No OPERATOR_ID column - joined on KPI_ID + TRANSIT_DAY_KEY only
    SELECT
        ks.KPI_ID,
        ks.TRANSIT_DAY_KEY,
        ks.KPI_VALUE              AS summary_kpi_value,
        ks.KPI_QUANTITY           AS summary_kpi_quantity
    FROM mars_dev.bronze.edw_kpi_summary_by_day ks
),
target_bands AS (
    -- 4 bands per KPI_ID (A=best, D=worst); aggregate to (KPI_ID, KPI_BAND) to deduplicate
    -- Range join (KPI_VALUE BETWEEN KPI_MINIMUM AND KPI_MAXIMUM) is non-overlapping
    -- within a KPI - at most 1 matching band per detail row
    SELECT
        KPI_ID,
        KPI_BAND,
        MIN(KPI_MINIMUM)          AS KPI_MINIMUM,
        MAX(KPI_MAXIMUM)          AS KPI_MAXIMUM,
        MAX(KPI_DEDUCTION_TYPE)   AS KPI_DEDUCTION_TYPE,
        MAX(KPI_DEDUCTION_VALUE)  AS KPI_DEDUCTION_VALUE
    FROM mars_dev.bronze.edw_kpi_target
    GROUP BY KPI_ID, KPI_BAND
),
kpi_rules_agg AS (
    -- Pre-aggregate to prevent fan-out (multiple rules per KPI_ID)
    -- KPI_ID is mixed type (numeric + alphanumeric "21.1P") - cast to STRING for join
    SELECT
        CAST(KPI_ID AS STRING)            AS kpi_id_str,
        COUNT(*)                           AS rule_count,
        MIN(FAILURE_LEVEL)                 AS rule_min_failure_level,
        MAX(FAILURE_LEVEL)                 AS rule_max_failure_level
    FROM mars_dev.bronze.edw_kpi_rules
    GROUP BY CAST(KPI_ID AS STRING)
),
sldc_monthly AS (
    -- MONTH_DTM is already a timestamp; TRUNC to month grain
    -- GROUP BY KPI_ID + month prevents fan-out from multi-agency rows per KPI
    -- MIN(BAND) selects best band (A < B < C alphabetically)
    SELECT
        KPI_ID,
        TRUNC(MONTH_DTM, 'MM')             AS sldc_month,
        MAX(KPI_VALUE)                      AS sldc_kpi_value,
        MIN(BAND)                           AS sldc_band,
        MAX(SLDC_PERCENT)                   AS sldc_percent,
        MAX(ADJUSTED_SLDC_PERCENT)          AS sldc_adjusted_pct
    FROM mars_dev.bronze.cta_sldc_monthly_summary
    GROUP BY KPI_ID, TRUNC(MONTH_DTM, 'MM')
),
agency_map AS (
    -- Up to 3 rows per KPI_ID (multi-agency); pre-aggregate to 1 row per KPI_ID
    SELECT
        KPI_ID,
        MIN(KPI_AGENCY_ID)    AS KPI_AGENCY_ID,
        MIN(KPI_AGENCY_NAME)  AS KPI_AGENCY_NAME
    FROM mars_dev.bronze.cta_kpi_agency_map
    GROUP BY KPI_ID
)
SELECT
    kd.KPI_ID,
    kd.DEVICE_ID,
    kd.DEVICE_KEY,
    kd.TRANSIT_DAY_KEY,
    kd.transit_day,
    kd.OPERATOR_ID,
    kd.FACILITY_ID,
    kd.KPI_VALUE,
    kd.FAILURE_LEVEL,
    kd.FAULT_DESCRIPTION,
    kd.EXCLUDED,
    kd.RELIEF_VALUE,
    kd.ARRAY_ID,
    kd.ARRAY_SIZE,
    kd.ARRAY_POSITION,
    kd.START_DTM,
    kd.END_DTM,
    kd.DEVICE_TYPE_ID,
    kd.DEVICE_TYPE_NAME,
    -- KPI definition (actual columns confirmed 2026-06-15)
    k.KPI_NAME,
    k.KPI_SYSTEM,
    k.KPI_TYPE,
    k.UNITS                               AS kpi_units,
    k.KPI_DESC,
    k.KPI_CRITICALITY,
    -- KPI rules (pre-aggregated to 1 row per KPI_ID - no fan-out)
    kr.rule_count                         AS kpi_rule_count,
    kr.rule_min_failure_level,
    kr.rule_max_failure_level,
    -- Target band: which performance band does this KPI_VALUE fall into?
    -- Band A = meets target (0% deduction); NULL = outside all defined bands
    tg.KPI_BAND                           AS kpi_target_band,
    tg.KPI_DEDUCTION_TYPE,
    tg.KPI_DEDUCTION_VALUE,
    CASE
        WHEN tg.KPI_BAND IS NOT NULL THEN tg.KPI_BAND = 'A'
        ELSE NULL
    END                                   AS meets_target,
    -- Summary cross-check (KPI-day aggregate, not device-level)
    ks.summary_kpi_value,
    ks.summary_kpi_quantity,
    -- Device enrichment (joined on DEVICE_ID for 98.7% match rate)
    dd.DEVICE_NAME,
    dd.DEVICE_TYPE_NAME                   AS dim_device_type_name,
    dd.mars_device_category,
    dd.FACILITY_NAME,
    dd.OPERATOR_NAME,
    -- SLDC monthly performance context at KPI+month grain
    sl.sldc_kpi_value,
    sl.sldc_band,
    sl.sldc_percent,
    sl.sldc_adjusted_pct,
    -- Agency mapping
    ag.KPI_AGENCY_ID,
    ag.KPI_AGENCY_NAME

FROM kpi_detail_base kd
LEFT JOIN mars_dev.bronze.edw_kpi k
    ON k.KPI_ID           = kd.KPI_ID
LEFT JOIN kpi_rules_agg kr
    ON kr.kpi_id_str      = CAST(kd.KPI_ID AS STRING)
LEFT JOIN target_bands tg
    ON tg.KPI_ID          = kd.KPI_ID
   AND kd.KPI_VALUE BETWEEN tg.KPI_MINIMUM AND tg.KPI_MAXIMUM
LEFT JOIN kpi_summary_base ks
    ON ks.KPI_ID          = kd.KPI_ID
   AND ks.TRANSIT_DAY_KEY = kd.TRANSIT_DAY_KEY
LEFT JOIN mars_dev.silver.dim_device dd
    ON dd.DEVICE_ID       = kd.DEVICE_ID
   AND dd.is_current      = TRUE
LEFT JOIN sldc_monthly sl
    ON sl.KPI_ID          = kd.KPI_ID
   AND sl.sldc_month      = TRUNC(kd.transit_day, 'MM')
LEFT JOIN agency_map ag
    ON ag.KPI_ID          = kd.KPI_ID;

-- Post-load optimisation:
-- OPTIMIZE mars_dev.silver.kpi_daily ZORDER BY (DEVICE_ID, transit_day);
