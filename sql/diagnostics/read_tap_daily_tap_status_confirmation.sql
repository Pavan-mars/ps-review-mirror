-- =============================================================================
-- DIAGNOSTIC: read_tap_daily TAP_STATUS_ID approval-code confirmation
-- Added: 2026-07-07, validation pass (Chicago silver/gold rebuild review)
--
-- WHY THIS EXISTS:
-- 22_read_tap_daily__create.sql hardcodes TAP_STATUS_ID IN (1, 900, 904) as the
-- "approved" definition, explicitly flagged in its own comment as:
-- "ASSUMED from ABP_TAP; UNVERIFIED for READ_TRANSACTION" -- with this exact
-- confirmation query already written in that file's comments but, as far as this
-- review found, never run. This file just promotes it to a standalone, runnable
-- script so it's not waiting to be noticed inside a 100+ line CREATE TABLE file.
-- read_tap_daily feeds gold.device_ps1_daily (read_tap_count feature) and
-- gold.device_ps4_hourly (daily read-activity signal), so a wrong approval-code
-- assumption here quietly mis-labels approved vs. rejected reads in two PS
-- feature tables.
--
-- HOW TO READ THE RESULTS:
-- Compare the codes that actually carry the bulk of the row volume against
-- ABP_TAP's known distribution (1 = Device Approved 41.62%, 900 = Server Approved
-- 52.86%, 904 = Server Approved Cached 0.03%, everything else = rejected).
-- If READ_TRANSACTION's top-volume codes are the same 3 values, the assumption in
-- read_tap_daily is confirmed correct as-is -- no SQL change needed, just remove
-- the UNVERIFIED flag from that file's comment. If a different code carries
-- meaningful volume and isn't in (1, 900, 904), the approved_read_count /
-- rejected_read_count / reject_rate_pct columns in read_tap_daily need a review.
-- =============================================================================

SELECT
TAP_STATUS_ID,
COUNT(*) AS row_count,
ROUND(COUNT(*) * 100.0 / SUM(COUNT(*)) OVER (), 2) AS pct_of_total
FROM mars_dev.bronze.edw_read_transaction
GROUP BY TAP_STATUS_ID
ORDER BY row_count DESC;
