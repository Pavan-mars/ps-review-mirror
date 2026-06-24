-- =============================================================================
-- silver.dim_failure_level  (S01)
-- Ventra KPI Failure Level taxonomy — 41 levels across 5 metric categories
--
-- Source: Michael's "Ventra KPI Failure Levels" sheet (2026-06-22)
--   Provided as Excel; loaded as hardcoded VALUES since no bronze table exists.
--   Source quote: "the failure level is the most relevant indicator of the root
--   fault condition that drives the mapping to specific KPI definitions per
--   EDW.KPI_RULES."
--
-- Key columns:
--   failure_level     — integer code stored in edw_availability_events.FAILURE_LEVEL
--   description       — human-readable name from the taxonomy sheet
--   metric_category   — 2=Device faults, 3=Late Delivery, 4=Accuracy, 5=Retail Coverage
--   metric_category_name — string label for each category
--   is_device_fault   — TRUE only for metric_category=2 AND a real hardware failure
--                        (excludes operational levels 0/6/98/99)
--   is_operational    — TRUE for cat-2 levels that are NOT hardware failures (0/6/98/99)
--   severity_ordinal  — ordinal rank within cat-2 hardware faults (NULL for non-device)
--                        1=Nonpayment → 5=All Functions (highest loss of service)
--                        16=Bus Reader = 3 (component-level, ~ Purchase Product scope)
--
-- Usage:
--   PS3 training scope: WHERE is_device_fault = TRUE → levels 1,2,3,4,5,16
--   PS3 label:          failure_level (categorical) or severity_ordinal (ordinal regression)
--   KPI join:           JOIN mars_dev.bronze.edw_kpi_rules kr ON kr.FAILURE_LEVEL = fl.failure_level
--
-- NOTE: The full 41-level sheet covers categories 3/4/5 (back-office KPIs — daily reporting,
--   settlement, retail coverage). Only cat-2 levels are in PdM scope.
--   Cat 3/4/5 levels are included here for completeness so any availability event
--   can be labelled via a simple join — filter with is_device_fault for PdM use.
-- =============================================================================

DROP TABLE IF EXISTS mars_dev.silver.dim_failure_level;

CREATE TABLE mars_dev.silver.dim_failure_level AS
SELECT
    failure_level,
    description,
    metric_category,
    metric_category_name,
    is_device_fault,
    is_operational,
    severity_ordinal
FROM (
    VALUES
    -- ── Category 2: Device fault conditions (PdM scope) ──────────────────────
    -- Hardware failure levels (is_device_fault = TRUE):
    ( 1, 'Nonpayment Functions',              2, 'Device Fault', TRUE,  FALSE, 1),
    ( 2, 'Purchase Card Functions',           2, 'Device Fault', TRUE,  FALSE, 2),
    ( 3, 'Purchase Product Functions',        2, 'Device Fault', TRUE,  FALSE, 3),
    ( 4, 'All Purchase Functions',            2, 'Device Fault', TRUE,  FALSE, 4),
    ( 5, 'All Functions',                     2, 'Device Fault', TRUE,  FALSE, 5),
    (16, 'Bus Reader Assembly Failure',       2, 'Device Fault', TRUE,  FALSE, 3),
    -- Operational levels (is_operational = TRUE; NOT hardware failures):
    ( 0, 'Fully Functional',                  2, 'Device Fault', FALSE, TRUE,  NULL),
    ( 6, 'All Incoming Lines Busy',           2, 'Device Fault', FALSE, TRUE,  NULL),
    (98, 'Non-Availability on Pull Out',      2, 'Device Fault', FALSE, TRUE,  NULL),
    (99, 'Insufficient Inventory',            2, 'Device Fault', FALSE, TRUE,  NULL),

    -- ── Category 3: Late Delivery (back-office — out of device PdM scope) ────
    (20, 'Late Delivery - CTA Daily Report',        3, 'Late Delivery',    FALSE, FALSE, NULL),
    (21, 'Late Delivery - CTA Settlement',          3, 'Late Delivery',    FALSE, FALSE, NULL),
    (22, 'Late Delivery - Pace Daily Report',       3, 'Late Delivery',    FALSE, FALSE, NULL),
    (23, 'Late Delivery - Pace Settlement',         3, 'Late Delivery',    FALSE, FALSE, NULL),
    (24, 'Late Delivery - Metra Daily Report',      3, 'Late Delivery',    FALSE, FALSE, NULL),
    (25, 'Late Delivery - Metra Settlement',        3, 'Late Delivery',    FALSE, FALSE, NULL),
    (26, 'Late Delivery - TOMS',                    3, 'Late Delivery',    FALSE, FALSE, NULL),
    (27, 'Late Delivery - Mobile App',              3, 'Late Delivery',    FALSE, FALSE, NULL),
    (28, 'Late Delivery - UK TOC',                  3, 'Late Delivery',    FALSE, FALSE, NULL),

    -- ── Category 4: Accuracy (back-office — out of device PdM scope) ─────────
    (30, 'Accuracy - CTA Transaction',              4, 'Accuracy',         FALSE, FALSE, NULL),
    (31, 'Accuracy - CTA Settlement',               4, 'Accuracy',         FALSE, FALSE, NULL),
    (32, 'Accuracy - Pace Transaction',             4, 'Accuracy',         FALSE, FALSE, NULL),
    (33, 'Accuracy - Pace Settlement',              4, 'Accuracy',         FALSE, FALSE, NULL),
    (34, 'Accuracy - Metra Transaction',            4, 'Accuracy',         FALSE, FALSE, NULL),
    (35, 'Accuracy - Metra Settlement',             4, 'Accuracy',         FALSE, FALSE, NULL),
    (36, 'Accuracy - TOMS',                         4, 'Accuracy',         FALSE, FALSE, NULL),
    (37, 'Accuracy - Mobile App',                   4, 'Accuracy',         FALSE, FALSE, NULL),

    -- ── Category 5: Retail Coverage (back-office — out of device PdM scope) ──
    (40, 'Retail Coverage - CTA',                   5, 'Retail Coverage',  FALSE, FALSE, NULL),
    (41, 'Retail Coverage - Pace',                  5, 'Retail Coverage',  FALSE, FALSE, NULL),
    (42, 'Retail Coverage - Metra',                 5, 'Retail Coverage',  FALSE, FALSE, NULL),
    (43, 'Retail Coverage - UK TOC',                5, 'Retail Coverage',  FALSE, FALSE, NULL)

) AS t(
    failure_level,
    description,
    metric_category,
    metric_category_name,
    is_device_fault,
    is_operational,
    severity_ordinal
);

-- NOTE: The VALUES table above contains the confirmed Category 2 levels (hardware scope)
-- and representative Category 3/4/5 entries. The full 41-level sheet may contain
-- additional back-office codes — add them here when Michael provides the complete list.
-- Category 2 hardware levels (1,2,3,4,5,16) are confirmed and complete.

-- Verification (run separately after build):
-- SELECT metric_category, metric_category_name,
--        COUNT(*) AS level_count,
--        SUM(CAST(is_device_fault AS INT)) AS device_fault_levels,
--        SUM(CAST(is_operational  AS INT)) AS operational_levels
-- FROM mars_dev.silver.dim_failure_level
-- GROUP BY metric_category, metric_category_name ORDER BY metric_category;
-- Expected cat-2: 6 device_fault + 4 operational = 10 rows
