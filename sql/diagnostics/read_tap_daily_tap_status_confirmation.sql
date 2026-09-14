-- =============================================================================
-- DIAGNOSTIC: read_tap_daily TAP_STATUS_ID approval-code confirmation
-- Added: 2026-07-07, validation pass (Chicago silver/gold rebuild review)
--
-- WHY THIS EXISTS:
-- 22_read_tap_daily__create.sql hardcodes TAP_STATUS_ID IN (1, 900, 901, 905) as the
-- "approved" definition for READ_TRANSACTION (901/905 are READ_TRANSACTION-specific;
-- S13 tap_event_daily uses 904/905 from ABP_TAP instead). Run this query after
-- bronze reloads to confirm the code distribution still matches assumptions.
-- read_tap_daily feeds gold.device_ps1_daily (read_tap_count feature) and
-- gold.device_ps4_hourly (daily read-activity signal), so a wrong approval-code
-- assumption here quietly mis-labels approved vs. rejected reads in two PS
-- feature tables.
--
-- HOW TO READ THE RESULTS:
-- Expected READ_TRANSACTION distribution (2026-07-17 QR-2 validation):
--   900 (~46%) Server Approved, 1 (~20%) Device Approved, 901 (~16%) approved,
--   701 (~15%) Stale Tap (excluded from approved AND rejected in S22),
--   4 (~3%) rejected, NULL (~0.2%) counted as approved.
-- Approved set: (1, 900, 901, 905) plus NULL. Excluded neutral: 701.
-- If a new code carries meaningful volume outside that set, review S22 approval logic.
-- =============================================================================

SELECT
TAP_STATUS_ID,
COUNT(*) AS row_count,
ROUND(COUNT(*) * 100.0 / SUM(COUNT(*)) OVER (), 2) AS pct_of_total
FROM mars_dev.bronze.edw_read_transaction
GROUP BY TAP_STATUS_ID
ORDER BY row_count DESC;
