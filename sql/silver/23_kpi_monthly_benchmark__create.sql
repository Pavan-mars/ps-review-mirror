-- =============================================================================
-- silver.kpi_monthly_benchmark  (S23)
-- Monthly contract-level KPI performance vs SLA benchmarks
-- NOT device-grain - contract/system grain: one row per (month, KPI_ID)
--
-- Source (bronze):
--   mars_dev.bronze.cta_kpi_monthly_summary   (CTA.KPI_MONTHLY_SUMMARY - 851 rows)
--   mars_dev.bronze.edw_kpi                   (EDW.KPI - KPI name/type lookup)
--
-- Grain: (month_start, KPI_ID) - 851 rows total (small reference table)
--
-- ⚠️  DATA COVERAGE WARNING (confirmed 2026-06-25 from Databricks bronze):
--   Bronze has 851 rows across only 23 months: May 2013 -> March 2015.
--   NO DATA EXISTS from April 2015 onwards - 10+ year gap.
--   This table has ZERO overlap with the ML training window (2024-01-01+).
--   -> S23 is GOVERNANCE/HISTORY ONLY - cannot be used as a PS1/PS4 ML feature.
--   -> Any gold table LEFT JOIN on month will return NULL for all 2024+ rows.
--   -> Raised with Michael (item #4): is there a more recent KPI data source?
--
-- Column notes (from CTA_KPI_MONTHLY_SUMMARY.csv schema validation):
--   MONTH_DTM           TIMESTAMP  - first day of the month (2014-01-01 format)
--   KPI_ID              STRING     - e.g. "1","13","21.1P","25.1P" (NOT always integer)
--   KPI_VALUE           INTEGER    - raw KPI score (units vary by KPI: Minutes, MS, Each)
--   BAND                STRING     - performance band (A/B/C...)
--   SLDC_PERCENT        INTEGER    - Service Level Default Credit % triggered
--   BASE_SLDC_AMT       DOUBLE     - base SLDC dollar amount
--   ADJUSTED_SLDC_AMT   DOUBLE     - adjusted SLDC after earnback
--   COMPOUND            STRING     - Y/N compound credit flag
--   PERSISTENT          STRING     - Y/N persistent credit flag
--   CURRENT_EARNBACK    DOUBLE     - current period earnback amount
--   FUTURE_EARNBACK_VALUE DOUBLE   - future earnback dollar value
--   EB_50_PERCENT_MONTH TIMESTAMP  - month earnback reaches 50%
--   EB_25_PERCENT_MONTH TIMESTAMP  - month earnback reaches 25%
--
-- EDW_KPI join: 37/47 KPI_IDs match by string comparison; 10 have no EDW_KPI row
--   (KPI_IDs 19, 21, 22-30 appear in monthly summary but not in EDW_KPI master)
--   -> LEFT JOIN preserves all 851 rows; unmatched get NULL KPI_NAME
--
-- Michael R3-9: CTA.KPI_MONTHLY_SUMMARY IS the V2 monthly KPI source.
--   EDW.KPI_MONTHLY does NOT exist - use this table as the contract benchmark spine.
--
-- Usage (REVISED after data range confirmed):
--   ✗ PS1/PS4 ML features - NOT usable (data ends 2015; ML window starts 2024)
--   ✓ Governance history  - 2013-2015 SLA/SLDC benchmarks for contract reference
--   ✓ Erik's status deck  - historical SLDC/earnback reference only
--   PENDING Michael: confirm whether a 2015-2026 continuation exists in Oracle
--
-- Build order: standalone (no upstream silver dependencies)
-- Feeds: gold tables (optional join for monthly context); reporting/governance layer
-- =============================================================================

DROP TABLE IF EXISTS mars_dev.silver.kpi_monthly_benchmark;

CREATE TABLE mars_dev.silver.kpi_monthly_benchmark AS
SELECT
    -- -- Time grain -------------------------------------------------------------
    CAST(TO_DATE(km.MONTH_DTM, 'yyyy-MM-dd HH:mm:ss') AS DATE)    AS month_start,
    YEAR(TO_DATE(km.MONTH_DTM, 'yyyy-MM-dd HH:mm:ss'))             AS report_year,
    MONTH(TO_DATE(km.MONTH_DTM, 'yyyy-MM-dd HH:mm:ss'))            AS report_month,

    -- -- KPI identity -----------------------------------------------------------
    km.KPI_ID,
    k.KPI_NAME,
    k.KPI_TYPE,
    k.UNITS,
    k.KPI_CRITICALITY,        -- 'Core' / 'Non-Core' / 'Core*1'
    k.OPERATOR_ID             AS kpi_operator_id,

    -- -- Performance score ------------------------------------------------------
    km.KPI_VALUE,
    km.BAND,                  -- performance band (A = best)

    -- -- SLA credit (SLDC) ------------------------------------------------------
    km.SLDC_PERCENT,
    km.BASE_SLDC_AMT,
    km.ADJUSTED_SLDC_AMT,
    (km.BASE_SLDC_AMT - km.ADJUSTED_SLDC_AMT)                      AS sldc_reduction_amt,
    CAST(km.COMPOUND   AS BOOLEAN)                                  AS is_compound,
    CAST(km.PERSISTENT AS BOOLEAN)                                  AS is_persistent,

    -- -- Earnback ---------------------------------------------------------------
    km.CURRENT_EARNBACK,
    km.FUTURE_EARNBACK_VALUE,
    CASE
        WHEN km.EB_50_PERCENT_MONTH IS NOT NULL
        THEN CAST(TO_DATE(km.EB_50_PERCENT_MONTH, 'yyyy-MM-dd HH:mm:ss') AS DATE)
        ELSE NULL
    END                                                             AS eb_50_pct_month,
    CASE
        WHEN km.EB_25_PERCENT_MONTH IS NOT NULL
        THEN CAST(TO_DATE(km.EB_25_PERCENT_MONTH, 'yyyy-MM-dd HH:mm:ss') AS DATE)
        ELSE NULL
    END                                                             AS eb_25_pct_month,

    -- -- Derived flag -----------------------------------------------------------
    -- sldc_triggered: TRUE if any SLDC credit applies this month for this KPI
    (km.SLDC_PERCENT > 0 OR km.BASE_SLDC_AMT > 0)                  AS sldc_triggered

FROM mars_dev.bronze.cta_kpi_monthly_summary km
LEFT JOIN mars_dev.bronze.edw_kpi k
    ON CAST(k.KPI_ID AS STRING) = CAST(km.KPI_ID AS STRING);
-- 851 total rows; 37/47 KPI_IDs match EDW_KPI; 10 get NULL KPI_NAME (not in EDW_KPI master)
-- No WHERE filter - keep full history (2014 onwards; 851 rows is small enough for full load)

-- Post-load verification:
-- SELECT
--     COUNT(*)                                              AS total_rows,       -- Expect 851
--     COUNT(DISTINCT KPI_ID)                                AS distinct_kpis,    -- Expect 37
--     SUM(CASE WHEN KPI_NAME IS NULL THEN 1 ELSE 0 END)    AS unmatched_kpis,   -- Expect ~10
--     MIN(month_start)                                      AS earliest_month,   -- Expect 2013-05-01
--     MAX(month_start)                                      AS latest_month,     -- Expect 2015-03-01
--     COUNT(DISTINCT month_start)                           AS distinct_months,  -- Expect 23
--     SUM(CASE WHEN sldc_triggered THEN 1 ELSE 0 END)      AS months_with_sldc,
--     ROUND(SUM(BASE_SLDC_AMT), 2)                         AS total_base_sldc
-- FROM mars_dev.silver.kpi_monthly_benchmark;
-- ⚠️  NO ROWS will match a join on transit_day >= 2024-01-01 - data ends 2015-03-01
