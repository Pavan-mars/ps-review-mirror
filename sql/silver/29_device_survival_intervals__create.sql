-- =============================================================================
-- silver.device_survival_intervals  (S29)
-- One row per consecutive failure-free interval per device.
-- Used in PS2 survival analysis (Kaplan-Meier / Cox regression) and as a
-- feature source for time-to-next-failure and mean-time-between-failures (MTBF).
--
-- Source:
--   S26 mars_dev.silver.device_failures  (device-day failure grain)
--
-- Interval definition:
--   A "survival interval" is a consecutive run of failure-free calendar days
--   for a device, bounded by two adjacent failure dates or by the start/end of
--   the observation window.
--
--   past_intervals   : gaps BETWEEN two confirmed failures (both ends known)
--   ongoing_interval : period AFTER the most recent failure up to CURRENT_DATE()
--                      (right-censored — device has not yet failed again)
--
-- Grain: (DEVICE_KEY, interval_start_date)
--   One row per failure-free gap per device.
--   Devices that have NEVER failed have no rows here — they are right-censored
--   at the full observation window and should be handled in the survival model
--   by joining dim_device and treating missing rows as one long censored interval.
--
-- Key columns:
--   interval_start_date    : first failure-free day after the preceding failure
--                            (DATE_ADD(preceding_failure_date, 1))
--   interval_end_date      : last failure-free day before the next failure
--                            (DATE_SUB(following_failure_date, 1)); NULL if ongoing
--   interval_days          : length of the interval in calendar days
--   preceding_failure_date : the failure that ended the previous interval (NULL if
--                            this is the device's first-ever interval in training window)
--   following_failure_date : the failure that closes this interval (NULL if ongoing)
--   is_ongoing             : TRUE = right-censored (device still healthy as of CURRENT_DATE())
--   is_first_interval      : TRUE = no prior failure in training window (left-censored start)
--
-- PS2 usage:
--   JOIN to device_ps2_chains on DEVICE_KEY + transit_day BETWEEN interval_start_date
--   AND COALESCE(interval_end_date, CURRENT_DATE()) to get the current survival
--   age (days healthy since last failure) as a feature.
--
-- MTBF usage (device_mttr complement):
--   SELECT device_category,
--          ROUND(AVG(interval_days), 1) AS mean_days_between_failures
--   FROM mars_dev.silver.device_survival_intervals
--   WHERE NOT is_ongoing AND NOT is_first_interval
--   GROUP BY device_category;
--
-- Build order: S26 (device_failures) -> S29 (this table)
-- Feeds: gold.device_ps2_chains (survival age feature)
--        gold.device_ps1_daily  (longest_healthy_run feature — optional)
-- =============================================================================

DROP TABLE IF EXISTS mars_dev.silver.device_survival_intervals;

CREATE TABLE mars_dev.silver.device_survival_intervals
USING DELTA
TBLPROPERTIES ('delta.autoOptimize.optimizeWrite' = 'true')
COMMENT 'Consecutive failure-free intervals per device (survival analysis grain). Right-censored ongoing intervals included. Never-failed devices have no rows.'
AS

WITH ordered_failures AS (
    SELECT
        DEVICE_KEY,
        DEVICE_ID,
        device_category,
        failure_date,
        LAG(failure_date)  OVER (PARTITION BY DEVICE_KEY ORDER BY failure_date) AS prev_failure_date,
        LEAD(failure_date) OVER (PARTITION BY DEVICE_KEY ORDER BY failure_date) AS next_failure_date,
        ROW_NUMBER()       OVER (PARTITION BY DEVICE_KEY ORDER BY failure_date) AS failure_seq
    FROM mars_dev.silver.device_failures
),

-- ── Intervals between two known failure dates ─────────────────────────────────
-- Condition: there is a prior failure AND at least 1 day gap between them
past_intervals AS (
    SELECT
        DEVICE_KEY,
        DEVICE_ID,
        device_category,
        DATE_ADD(prev_failure_date, 1)                      AS interval_start_date,
        DATE_SUB(failure_date, 1)                           AS interval_end_date,
        DATEDIFF(
            DATE_SUB(failure_date, 1),
            DATE_ADD(prev_failure_date, 1)
        ) + 1                                               AS interval_days,
        prev_failure_date                                   AS preceding_failure_date,
        failure_date                                        AS following_failure_date,
        FALSE                                               AS is_ongoing,
        FALSE                                               AS is_first_interval
    FROM ordered_failures
    WHERE prev_failure_date IS NOT NULL
      AND DATEDIFF(failure_date, prev_failure_date) > 1    -- gap of ≥2 days (1 failure-free day exists)
),

-- ── Ongoing interval after the most recent failure (right-censored) ───────────
latest_failures AS (
    SELECT
        DEVICE_KEY,
        DEVICE_ID,
        device_category,
        MAX(failure_date) AS last_failure_date
    FROM mars_dev.silver.device_failures
    GROUP BY DEVICE_KEY, DEVICE_ID, device_category
),
ongoing_intervals AS (
    SELECT
        lf.DEVICE_KEY,
        lf.DEVICE_ID,
        lf.device_category,
        DATE_ADD(lf.last_failure_date, 1)                   AS interval_start_date,
        CAST(NULL AS DATE)                                  AS interval_end_date,
        DATEDIFF(CURRENT_DATE(), lf.last_failure_date)      AS interval_days,
        lf.last_failure_date                                AS preceding_failure_date,
        CAST(NULL AS DATE)                                  AS following_failure_date,
        TRUE                                                AS is_ongoing,
        FALSE                                               AS is_first_interval
    FROM latest_failures lf
    WHERE DATEDIFF(CURRENT_DATE(), lf.last_failure_date) >= 1  -- at least 1 failure-free day so far
),

-- ── First interval in window: period before a device's very first recorded failure ─
-- Left-censored: we do not know when the prior failure (if any) occurred before
-- the training window start (2024-01-01). Marked is_first_interval=TRUE.
first_failures AS (
    SELECT
        DEVICE_KEY,
        DEVICE_ID,
        device_category,
        MIN(failure_date) AS first_failure_date
    FROM mars_dev.silver.device_failures
    GROUP BY DEVICE_KEY, DEVICE_ID, device_category
),
first_intervals AS (
    SELECT
        ff.DEVICE_KEY,
        ff.DEVICE_ID,
        ff.device_category,
        DATE '2024-01-01'                                   AS interval_start_date,
        DATE_SUB(ff.first_failure_date, 1)                  AS interval_end_date,
        DATEDIFF(DATE_SUB(ff.first_failure_date, 1), DATE '2024-01-01') + 1
                                                            AS interval_days,
        CAST(NULL AS DATE)                                  AS preceding_failure_date,
        ff.first_failure_date                               AS following_failure_date,
        FALSE                                               AS is_ongoing,
        TRUE                                                AS is_first_interval
    FROM first_failures ff
    WHERE DATEDIFF(ff.first_failure_date, DATE '2024-01-01') > 0  -- first failure not on 2024-01-01 itself
)

SELECT * FROM past_intervals
UNION ALL
SELECT * FROM ongoing_intervals
UNION ALL
SELECT * FROM first_intervals;

-- Post-load validation:
-- SELECT device_category,
--        COUNT(*)                              AS total_intervals,
--        SUM(CAST(is_ongoing AS INT))          AS ongoing_intervals,
--        SUM(CAST(is_first_interval AS INT))   AS first_intervals,
--        ROUND(AVG(interval_days), 1)          AS avg_interval_days,
--        ROUND(AVG(CASE WHEN NOT is_ongoing AND NOT is_first_interval
--                       THEN interval_days END), 1) AS mean_days_between_failures
-- FROM mars_dev.silver.device_survival_intervals
-- GROUP BY device_category ORDER BY 1;
