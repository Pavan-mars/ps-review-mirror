-- =============================================================================
-- DIAGNOSTIC: S14 tvm_sale_daily — Why only 3% TVM device-day coverage?
-- Created: 2026-07-15
--
-- Three hypotheses to test:
--   H1: Bronze table is incomplete (like the ServiceNow situation — few rows loaded)
--   H2: Device ID format mismatch (NCS DEVICE_ID ≠ dim_device.DEVICE_ID)
--   H3: NCS simply does not record all TVMs (e.g. cash-only or older model TVMs
--       use a different system and never appear in NCS at all)
--
-- Run queries A → D in order. Each one narrows the hypothesis.
-- =============================================================================


-- =============================================================================
-- QUERY A: Bronze table size and date range
-- Expected if data is complete: millions of rows, 2024-01-01 to recent
-- If H1 is true: far fewer rows than expected, or date range is narrow
-- =============================================================================
SELECT
    COUNT(*)                              AS total_rows,
    COUNT(DISTINCT DEVICE_ID)             AS distinct_ncs_devices,
    MIN(CAST(TRANSACTION_DTM AS DATE))    AS earliest_txn_day,
    MAX(CAST(TRANSACTION_DTM AS DATE))    AS latest_txn_day,
    SUM(CASE WHEN TRANSACTION_DTM < '2024-01-01' THEN 1 ELSE 0 END) AS pre_2024_rows,
    SUM(CASE WHEN TRANSACTION_DTM >= '2024-01-01' THEN 1 ELSE 0 END) AS from_2024_rows,
    ROUND(AVG(daily_count), 0)            AS avg_txns_per_device_day
FROM (
    SELECT DEVICE_ID, CAST(TRANSACTION_DTM AS DATE) AS d, COUNT(*) AS daily_count
    FROM mars_dev.bronze.ncs_stage_sale_transaction
    GROUP BY DEVICE_ID, CAST(TRANSACTION_DTM AS DATE)
) sub;


-- =============================================================================
-- QUERY B: Device ID format — what do NCS IDs look like vs dim_device TVM IDs?
-- Run both sub-queries to visually compare the ID format.
-- If H2 is true: the formats will look different (e.g. numeric vs alphanumeric)
-- =============================================================================

-- B1: Sample NCS device IDs (top 20 by transaction count)
SELECT
    DEVICE_ID                             AS ncs_device_id,
    COUNT(*)                              AS total_txns,
    MIN(CAST(TRANSACTION_DTM AS DATE))    AS first_seen,
    MAX(CAST(TRANSACTION_DTM AS DATE))    AS last_seen
FROM mars_dev.bronze.ncs_stage_sale_transaction
GROUP BY DEVICE_ID
ORDER BY total_txns DESC
LIMIT 20;

-- B2: Sample dim_device TVM IDs for visual comparison
SELECT DEVICE_ID, DEVICE_NAME, FACILITY_NAME
FROM mars_dev.silver.dim_device
WHERE mars_device_category = 'TVM'
  AND is_current = TRUE
LIMIT 20;


-- =============================================================================
-- QUERY C: Match rate — how many NCS device IDs actually join to dim_device?
-- This is the key diagnostic.
-- If H2 is true: match_rate_pct will be low (like the ServiceNow 57.6% situation)
-- If H3 is true: match_rate_pct will be high but only N of the full TVM fleet appears
-- =============================================================================
SELECT
    COUNT(DISTINCT st.DEVICE_ID)           AS total_ncs_devices,
    COUNT(DISTINCT dd.DEVICE_ID)           AS matched_to_dim_device,
    ROUND(
        COUNT(DISTINCT dd.DEVICE_ID) * 100.0 / COUNT(DISTINCT st.DEVICE_ID), 1
    )                                      AS device_match_rate_pct,
    COUNT(DISTINCT CASE WHEN dd.DEVICE_ID IS NULL THEN st.DEVICE_ID END)
                                           AS unmatched_ncs_devices,

    -- TVM fleet size for context
    (SELECT COUNT(*) FROM mars_dev.silver.dim_device
     WHERE mars_device_category = 'TVM' AND is_current = TRUE)
                                           AS total_tvm_fleet,

    -- How many TVM devices in fleet EVER appear in NCS at all?
    (SELECT COUNT(DISTINCT dd2.DEVICE_ID)
     FROM mars_dev.bronze.ncs_stage_sale_transaction st2
     JOIN mars_dev.silver.dim_device dd2
       ON dd2.DEVICE_ID = st2.DEVICE_ID AND dd2.is_current = TRUE
     WHERE dd2.mars_device_category = 'TVM')
                                           AS tvm_devices_with_any_sales

FROM mars_dev.bronze.ncs_stage_sale_transaction st
LEFT JOIN mars_dev.silver.dim_device dd
    ON dd.DEVICE_ID = st.DEVICE_ID AND dd.is_current = TRUE;


-- =============================================================================
-- QUERY D: Unmatched NCS device IDs — what do they look like?
-- Tells us if unmatched IDs are sentinel/aggregated IDs or a format mismatch
-- =============================================================================
SELECT
    st.DEVICE_ID                          AS unmatched_ncs_device_id,
    COUNT(*)                              AS total_txns,
    ROUND(AVG(st.NET_VALUE), 0)           AS avg_net_value_cents,
    MIN(CAST(st.TRANSACTION_DTM AS DATE)) AS first_seen,
    MAX(CAST(st.TRANSACTION_DTM AS DATE)) AS last_seen
FROM mars_dev.bronze.ncs_stage_sale_transaction st
LEFT JOIN mars_dev.silver.dim_device dd
    ON dd.DEVICE_ID = st.DEVICE_ID AND dd.is_current = TRUE
WHERE dd.DEVICE_ID IS NULL
GROUP BY st.DEVICE_ID
ORDER BY total_txns DESC
LIMIT 30;


-- =============================================================================
-- QUERY E: Coverage breakdown — matched TVM devices: how many days each?
-- Tells us whether 3% comes from few devices or from devices with sparse days
-- =============================================================================
SELECT
    days_with_sales_bucket,
    COUNT(*) AS device_count
FROM (
    SELECT
        sd.DEVICE_ID,
        COUNT(DISTINCT sd.transit_day) AS days_with_sales,
        CASE
            WHEN COUNT(DISTINCT sd.transit_day) >= 300 THEN '300+ days'
            WHEN COUNT(DISTINCT sd.transit_day) >= 100 THEN '100-299 days'
            WHEN COUNT(DISTINCT sd.transit_day) >= 30  THEN '30-99 days'
            WHEN COUNT(DISTINCT sd.transit_day) >= 7   THEN '7-29 days'
            ELSE '1-6 days'
        END AS days_with_sales_bucket
    FROM mars_dev.silver.tvm_sale_daily sd
    JOIN mars_dev.silver.dim_device dd
        ON dd.DEVICE_ID = sd.DEVICE_ID AND dd.is_current = TRUE
    WHERE dd.mars_device_category = 'TVM'
    GROUP BY sd.DEVICE_ID
) sub
GROUP BY days_with_sales_bucket
ORDER BY MIN(days_with_sales) DESC;
