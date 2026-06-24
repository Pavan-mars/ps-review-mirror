-- =============================================================================
-- silver.tvm_sale_daily
-- Daily aggregated TVM sale transactions per device
--
-- Source (bronze UC managed table):
--   mars_dev.bronze.ncs_stage_sale_transaction  (NCS_STAGE.SALE_TRANSACTION)
--
-- Notes:
--   - Grain: (device_id, transit_day)
--   - TRANSACTION_DTM is a TIMESTAMP column — cast to DATE to derive transit_day
--   - TRANSACTION_STATUS_CD: 0 = success, non-zero = error/failed transaction
--   - NET_VALUE / CASH_COLLECTED / CR_DB_AMOUNT are in cents (divide by 100 for dollars)
--   - Key derived metrics:
--       daily_sales_count   = total sale transactions
--       error_txn_rate      = failed transactions / total (early degradation signal)
--       cash_sales_pct      = proportion of cash vs card payments
--       total_revenue       = SUM(NET_VALUE) — daily revenue proxy
--       sales_active_hours  = distinct hours with activity (operational window)
--   - Source contains TVM AND VALIDATOR transactions (confirmed post-validation 2026-06-19).
--     Table name reflects primary use (TVM); VALIDATOR rows (834 devices, 13.38%) also present.
--     Downstream gold.tvm_ps1_daily MUST filter: WHERE mars_device_category = 'TVM'.
--   - 4 unmatched (NULL-category) devices with avg 4,626 sales/day — likely NCS aggregated
--     sentinel IDs; not real Chicago CTA devices. Excluded by category filter in gold tables.
--   - Feeds gold.tvm_ps1_daily as sales-drop early-warning feature (KPI 13, 15)
--
-- Validation run 2026-06-18 — bugs fixed from original:
--   BUG 1: FROM bronze.ncs_sale_transaction  → mars_dev.bronze.ncs_stage_sale_transaction
--   BUG 2: silver.tvm_sale_daily             → mars_dev.silver.tvm_sale_daily
--   BUG 3: silver.dim_device                 → mars_dev.silver.dim_device
--   BUG 4: TRUNC(x)::date                    → CAST(x AS DATE) (Spark SQL)
--   BUG 5: x::numeric                        → CAST(x AS DOUBLE)  (Spark SQL)
--   BUG 6: CREATE INDEX                      → not supported on Delta; use OPTIMIZE/ZORDER
-- =============================================================================

DROP TABLE IF EXISTS mars_dev.silver.tvm_sale_daily;

CREATE TABLE mars_dev.silver.tvm_sale_daily AS
WITH sale_agg AS (
    SELECT
        st.DEVICE_ID,
        CAST(st.TRANSACTION_DTM AS DATE)                         AS transit_day,
        st.OPERATOR_ID,
        st.FACID                                                 AS FACILITY_ID,
        -- Volume
        COUNT(*)                                                 AS daily_sales_count,
        -- Error rate: non-zero status = failed/errored transaction
        SUM(CASE WHEN st.TRANSACTION_STATUS_CD <> 0 THEN 1 ELSE 0 END)
                                                                 AS error_txn_count,
        -- Payment method breakdown
        SUM(CASE WHEN st.CASH_COLLECTED > 0 THEN 1 ELSE 0 END)  AS cash_sales_count,
        SUM(CASE WHEN st.CR_DB_AMOUNT > 0 THEN 1 ELSE 0 END)    AS card_sales_count,
        -- Revenue (NET_VALUE in cents)
        SUM(COALESCE(st.NET_VALUE, 0))                           AS total_revenue_cents,
        AVG(COALESCE(st.NET_VALUE, 0))                           AS avg_sale_value_cents,
        SUM(COALESCE(st.CASH_COLLECTED, 0))                      AS total_cash_cents,
        SUM(COALESCE(st.CR_DB_AMOUNT, 0))                        AS total_card_cents,
        -- Operational window
        COUNT(DISTINCT EXTRACT(HOUR FROM st.TRANSACTION_DTM))    AS sales_active_hours,
        MIN(st.TRANSACTION_DTM)                                  AS first_sale_dtm,
        MAX(st.TRANSACTION_DTM)                                  AS last_sale_dtm
    FROM mars_dev.bronze.ncs_stage_sale_transaction st
    WHERE st.TRANSACTION_DTM IS NOT NULL
    GROUP BY st.DEVICE_ID, CAST(st.TRANSACTION_DTM AS DATE), st.OPERATOR_ID, st.FACID
)
SELECT
    sa.DEVICE_ID,
    sa.transit_day,
    sa.OPERATOR_ID,
    sa.FACILITY_ID,
    sa.daily_sales_count,
    sa.error_txn_count,
    CASE
        WHEN sa.daily_sales_count > 0
        THEN ROUND(CAST(sa.error_txn_count AS DOUBLE) / sa.daily_sales_count * 100, 4)
        ELSE 0
    END                                                          AS error_txn_rate_pct,
    sa.cash_sales_count,
    sa.card_sales_count,
    CASE
        WHEN sa.daily_sales_count > 0
        THEN ROUND(CAST(sa.cash_sales_count AS DOUBLE) / sa.daily_sales_count * 100, 2)
        ELSE 0
    END                                                          AS cash_sales_pct,
    sa.total_revenue_cents,
    sa.avg_sale_value_cents,
    sa.total_cash_cents,
    sa.total_card_cents,
    sa.sales_active_hours,
    sa.first_sale_dtm,
    sa.last_sale_dtm,
    dd.DEVICE_KEY,
    dd.DEVICE_NAME,
    dd.FACILITY_NAME,
    dd.OPERATOR_NAME,
    dd.mars_device_category
FROM sale_agg sa
LEFT JOIN mars_dev.silver.dim_device dd
    ON dd.DEVICE_ID  = sa.DEVICE_ID
   AND dd.is_current = TRUE;

-- Post-load optimisation:
-- OPTIMIZE mars_dev.silver.tvm_sale_daily ZORDER BY (DEVICE_ID, transit_day);
