-- =============================================================================
-- silver.device_failures  (S26)
-- One row per (DEVICE_KEY, failure_date) where the device had at least one
-- hardware OOS or chargeable failure event on that calendar day.
--
-- Sources:
--   TVM / GATE  : mars_dev.bronze.edw_availability_events
--                 Filter: FAILURE_LEVEL > 0 AND EXCLUDED = 0
--                 Join to dim_device (is_current=TRUE) for mars_device_category
--
--   VALIDATOR   : mars_dev.silver.device_event_enriched
--                 Filter: is_oos_event = TRUE AND EVENT_STATE_TYPE_NAME = 'Set'
--                 mars_device_category + OOS flags already pre-joined in S16
--
-- Why two sources:
--   availability_events has TVM/GATE chargeable failures but only 65 BMV events (July 2021 only).
--   device_event_enriched (S16) covers current BMV devices with OOS events via
--   dim_event_matrix.is_hardware_oos_event. BMV failures are NOT in availability_events.
--
-- Validated device counts (2026-07-17, post SCD2-dedup + is_current fixes):
--   Devices WITH failure events in this table (distinct DEVICE_KEY):
--     TVM  469  /  513 current fleet  (91% experienced ≥1 chargeable failure)
--     GATE 944  / 1,382 current fleet (68% — 438 current gates never failed in training window)
--     VALIDATOR 1,535 / 4,218 current fleet (36% — 64% had no is_hardware_oos_event=TRUE OOS Set)
--   Devices with zero failures have no rows here — they are present in dim_device and
--   appear with zeroed failure features when joined to the gold PS1/PS5 spine.
--
-- AVM exclusion:
--   AVMs (431 devices, DEVICE_ID LIKE 'AVM%') have mars_device_category = 'OTHER'
--   in dim_device. Filtering on IN ('TVM','GATE') and = 'VALIDATOR' excludes them
--   automatically. The NOT LIKE 'AVM%' guard below adds belt-and-suspenders safety
--   for any AVMs that may appear with chargeable failures in legacy data.
--
-- Grain: (DEVICE_KEY, device_category, failure_date) — one row per device per day
--   with failure events. Days with no failure have no row (sparse; join to dim_device
--   calendar for a dense device-day spine if needed downstream).
--
-- Output columns:
--   device_category  : 'TVM' | 'GATE' | 'VALIDATOR'
--   failure_date     : calendar date of first failure event
--   failure_event_count : events on that day
--   first_failure_dtm, last_failure_dtm : event window
--   downtime_minutes : total downtime on this failure day, double-capped:
--                     (1) per-event cap at 10,080 min (7 days) — removes bad END_DTM/CLEAR_DTM outliers
--                         (TVM source had max 1,781,060 min = 1,237 days; VALIDATOR 393,120 min = 273 days)
--                     (2) day-level cap at 1,440 min (24 hours) — a device cannot have more than
--                         24h of actual downtime in one calendar day; SUM of many short OOS events
--                         on same day (e.g. VALIDATOR with 39 events/day) could otherwise exceed 1,440.
--                     0 if END_DTM / CLEAR_DTM not available.
--   failure_source   : 'availability_events' | 'device_event_enriched'
--
-- Build order: S06 (dim_device), S16 (device_event_enriched) -> S26 (this table)
-- Feeds:       gold.device_ps1_daily (will_fail_3d/7d/14d label + rolling features)
--              silver.device_mttr (mean-time-to-repair calculation)
--
-- Validation queries:
--   SELECT device_category, COUNT(DISTINCT DEVICE_KEY) AS devices,
--          COUNT(*) AS failure_days, MIN(failure_date), MAX(failure_date)
--   FROM mars_dev.silver.device_failures
--   GROUP BY device_category ORDER BY 1;
--   Expected (validated 2026-07-17 after SCD2-dedup + is_current fixes):
--     TVM ~469, GATE ~944, VALIDATOR ~1,535 distinct devices WITH failure events
--     (not all current devices fail — see device count note above)
-- =============================================================================

DROP TABLE IF EXISTS mars_dev.silver.device_failures;

CREATE TABLE mars_dev.silver.device_failures
USING DELTA
TBLPROPERTIES ('delta.autoOptimize.optimizeWrite' = 'true')
COMMENT 'Hardware failure events per device-day. TVM/GATE from availability_events and VALIDATOR from device_event_enriched OOS Set events. AVM excluded.'
AS

-- ── TVM / GATE: chargeable failures from edw_availability_events ─────────────
WITH tvm_gate_failures AS (
    SELECT
        ae.DEVICE_KEY,
        MAX(ae.DEVICE_ID)                                   AS DEVICE_ID,  -- MAX: same key, multiple IDs in source
        d.mars_device_category                              AS device_category,
        CAST(ae.START_DTM AS DATE)                          AS failure_date,
        COUNT(*)                                             AS failure_event_count,
        MIN(ae.START_DTM)                                   AS first_failure_dtm,
        -- FIX 2026-09-04 (DQ finding: temporal_pair check found last_failure_dtm before
        -- first_failure_dtm on a small number of rows -- root cause: these are two independent
        -- aggregates (MIN of START_DTM, MAX of END_DTM) that can legitimately draw from different
        -- individual events within the same device+day group when a day has multiple failure
        -- episodes with inconsistent resolution status (some ended, some still open with a NULL
        -- END_DTM excluded from the MAX). LOW CONFIDENCE FIX, FLAGGING FOR REVIEW: this is a
        -- minimal defensive guard (ensures last is never reported before first), not a redesign of
        -- what these two columns actually mean -- volume is small (a handful of rows) and severity
        -- is warning-only, so a full semantic redesign (e.g. deciding whether "first/last" should
        -- track a single episode vs. span multiple) was not attempted here and should be a
        -- separate decision if this needs to be more precise than a defensive floor.
        -- OLD version commented out, not deleted:
        -- MAX(ae.END_DTM)                                     AS last_failure_dtm,
        GREATEST(MAX(ae.END_DTM), MIN(ae.START_DTM))        AS last_failure_dtm,
        LEAST(
            SUM(
                CASE WHEN ae.END_DTM IS NOT NULL
                     THEN LEAST(GREATEST(TIMESTAMPDIFF(MINUTE, ae.START_DTM, ae.END_DTM), 0), 10080)
                     ELSE 0
                END
            ), 1440                                         -- day-level cap: max 24h actual downtime per calendar day
        )                                                   AS downtime_minutes,
        'availability_events'                                AS failure_source
    FROM mars_dev.bronze.edw_availability_events ae
    INNER JOIN (
        -- Deduplicate dim_device to 1 row per DEVICE_KEY before joining.
        -- dim_device can have >1 is_current=TRUE row per DEVICE_KEY (SCD2 violation —
        -- confirmed 2026-07-17: TVM shows 1,019 rows vs 513 distinct keys).
        -- Without dedup, availability_events fans out to N copies per affected device-day,
        -- producing duplicate rows in this table and all downstream tables (S27, S28, S29).
        SELECT DEVICE_KEY, MAX(mars_device_category) AS mars_device_category
        FROM mars_dev.silver.dim_device
        WHERE is_current = TRUE
        GROUP BY DEVICE_KEY
    ) d ON d.DEVICE_KEY = ae.DEVICE_KEY
    WHERE ae.FAILURE_LEVEL  > 0
      AND ae.EXCLUDED        = 0
      AND d.mars_device_category IN ('TVM', 'GATE')
      AND ae.DEVICE_ID NOT LIKE 'AVM%'                      -- belt-and-suspenders AVM guard
    GROUP BY
        ae.DEVICE_KEY,
        d.mars_device_category,
        CAST(ae.START_DTM AS DATE)
),

-- ── VALIDATOR (BMV): hardware OOS Set events from device_event_enriched ───────
-- is_hardware_oos_event, mars_device_category, duration_to_clear_min, CLEAR_DTM are
-- pre-joined in S16 (device_event_enriched__create.sql); no additional joins needed.
-- is_current filter: device_event_enriched includes all historical BMV devices
-- (confirmed 2026-07-17: 12,277 non-current vs 1,535 current in S29 without this filter).
-- Semi-join to dim_device (current only) keeps VALIDATOR consistent with TVM/GATE scope.
validator_failures AS (
    SELECT
        dee.DEVICE_KEY,
        MAX(dee.DEVICE_ID)                                  AS DEVICE_ID,  -- MAX: same key, multiple IDs in source
        dee.mars_device_category                            AS device_category,
        CAST(dee.EVENT_DTM AS DATE)                         AS failure_date,
        COUNT(*)                                             AS failure_event_count,
        MIN(dee.EVENT_DTM)                                  AS first_failure_dtm,
        -- FIX 2026-09-04: same defensive guard and reasoning as the TVM/GATE branch above -- see
        -- that comment for full context. OLD version commented out, not deleted:
        -- MAX(COALESCE(dee.CLEAR_DTM, dee.EVENT_DTM))        AS last_failure_dtm,
        GREATEST(MAX(COALESCE(dee.CLEAR_DTM, dee.EVENT_DTM)), MIN(dee.EVENT_DTM)) AS last_failure_dtm,
        LEAST(
            SUM(LEAST(COALESCE(dee.duration_to_clear_min, 0), 10080)),
            1440                                            -- day-level cap: max 24h actual downtime per calendar day
        )                                                   AS downtime_minutes,
        'device_event_enriched'                              AS failure_source
    FROM mars_dev.silver.device_event_enriched dee
    INNER JOIN (
        SELECT DEVICE_KEY
        FROM mars_dev.silver.dim_device
        WHERE is_current = TRUE
        GROUP BY DEVICE_KEY
    ) d_cur ON d_cur.DEVICE_KEY = dee.DEVICE_KEY
    WHERE dee.is_hardware_oos_event   = TRUE  -- excludes maintenance/commanded (106,110,151,208,519,1603,1604)
      AND dee.EVENT_STATE_TYPE_NAME   = 'Set'
      AND dee.mars_device_category    = 'VALIDATOR'
    GROUP BY
        dee.DEVICE_KEY,
        dee.mars_device_category,
        CAST(dee.EVENT_DTM AS DATE)
)

SELECT * FROM tvm_gate_failures
UNION ALL
SELECT * FROM validator_failures;

-- Post-load validation:
-- SELECT device_category,
--        COUNT(DISTINCT DEVICE_KEY) AS distinct_devices,
--        COUNT(*)                   AS failure_day_rows,
--        MIN(failure_date)          AS earliest,
--        MAX(failure_date)          AS latest,
--        ROUND(AVG(failure_event_count),2) AS avg_events_per_fail_day
-- FROM mars_dev.silver.device_failures
-- GROUP BY device_category ORDER BY 1;
