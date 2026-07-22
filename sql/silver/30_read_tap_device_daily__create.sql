-- =============================================================================
-- silver.read_tap_device_daily  (S30)
-- Device-day collapse of read_tap_daily (SIL-H2 fix, 2026-07-22)
--
-- Problem: read_tap_daily grain is (DEVICE_ID, OPERATOR_ID, FACILITY_ID, BUS_ID, transit_day).
-- VALIDATOR devices move between buses intra-day → 2–5 rows per device-day.
-- Joining on DEVICE_ID + transit_day alone fans out the PS1 spine.
--
-- Solution: aggregate to (DEVICE_ID, transit_day), recompute rates from summed counts.
-- Source: mars_dev.silver.read_tap_daily (S22 — must be built first)
-- Feeds:  PS1 VALIDATOR notebooks (prefer this over raw read_tap_daily)
-- =============================================================================

DROP TABLE IF EXISTS mars_dev.silver.read_tap_device_daily;

CREATE TABLE mars_dev.silver.read_tap_device_daily AS
SELECT
    rt.DEVICE_ID,
    rt.transit_day,
    MAX(rt.DEVICE_KEY)                                          AS DEVICE_KEY,
    MAX(rt.mars_device_category)                                AS mars_device_category,
    MAX(rt.FACILITY_ID)                                         AS FACILITY_ID,
    MAX(rt.FACILITY_NAME)                                       AS FACILITY_NAME,
    MAX(rt.OPERATOR_ID)                                         AS OPERATOR_ID,
    MAX(rt.OPERATOR_NAME)                                       AS OPERATOR_NAME,
    SUM(rt.daily_read_count)                                    AS daily_read_count,
    SUM(rt.unique_tokens)                                       AS unique_tokens,
    SUM(rt.approved_read_count)                                 AS approved_read_count,
    SUM(rt.rejected_read_count)                                 AS rejected_read_count,
    SUM(rt.null_status_read_count)                              AS null_status_read_count,
    CASE
        WHEN SUM(rt.daily_read_count) > 0
        THEN ROUND(CAST(SUM(rt.rejected_read_count) AS DOUBLE)
                   / SUM(rt.daily_read_count) * 100, 4)
        ELSE 0
    END                                                         AS reject_rate_pct,
    SUM(rt.entry_count)                                         AS entry_count,
    SUM(rt.exit_count)                                          AS exit_count,
    SUM(rt.transfer_tap_count)                                  AS transfer_tap_count,
    COUNT(DISTINCT rt.BUS_ID)                                   AS bus_routes_served
FROM mars_dev.silver.read_tap_daily rt
GROUP BY rt.DEVICE_ID, rt.transit_day;

-- Post-build verification:
-- SELECT COUNT(*) total,
--        COUNT(*) - COUNT(DISTINCT DEVICE_ID, transit_day) dups
-- FROM mars_dev.silver.read_tap_device_daily;
-- Expect dups = 0.
