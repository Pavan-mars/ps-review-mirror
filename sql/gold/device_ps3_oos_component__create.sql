-- =====================================================================
-- gold.device_ps3_oos_component  --  PS3 ROOT-CAUSE head, hardware-OOS spine
-- Created 2026-07-26.  Layer L4 (after silver.device_event_enriched).
--
-- WHY THIS TABLE EXISTS
--   PS3's root-cause head has been trained on gold.device_ps3_incident, whose
--   spine is ServiceNow AVAILABILITY EVENTS. That population is small and
--   structurally excludes validators:
--       TVM 32,842 | GATE 1,770 | VALIDATOR 0
--   and its target (derived_component_type, via affected_component) is
--   78.32% blank -- see FIX 7 in device_ps3_incident__create.sql.
--
--   The hardware-OOS population in silver.device_event_enriched, same window,
--   measured 2026-07-26:
--       VALIDATOR 11,642,104 onsets / 3,290 devices / 4 subsystems
--       TVM        4,208,151 onsets /   475 devices / 8 subsystems
--       GATE         895,638 onsets /   865 devices / 5 subsystems
--   component_subsystem is 100.0% filled for every category.
--   COMPONENT_SERIAL_NBR fill: TVM 99.5% | GATE 93.5% | VALIDATOR 63.9%.
--
--   So this spine gives a dense target, 8/5/4 modellable classes, and makes
--   VALIDATOR a first-class PS3 device type for the first time.
--
-- GRAIN: (DEVICE_ID, DW_DEVICE_EVENT_ID) -- one row per hardware-OOS onset.
--
-- NOTE ON dim_event_matrix: no join is needed. The dimension is already
-- denormalised into device_event_enriched (component_subsystem, is_oos_event,
-- requires_service_call, event_priority, is_reader_event, oos_counted_*_kpi
-- are all present as columns).
--
-- NOTE ON commanded OOS: is_hardware_oos_event already excludes commanded
-- events -- the 2026-07-26 profile returned commanded = 0 for every category.
-- The explicit guard below is belt-and-braces, not a real filter.
--
-- LEAKAGE DISCIPLINE: every feature is either (a) an attribute of the onset
-- itself known at onset time, or (b) a strictly PRIOR-window aggregate that
-- ends BEFORE this event's EVENT_DTM. Nothing describing the resolution of
-- this event (CLEAR_DTM, duration_to_clear_min, AUTOMATIC_CLEAR_FLAG) is a
-- feature -- those are carried as outcome columns for analysis only and MUST
-- be excluded from the model feature list.
-- =====================================================================

-- PERF ROUND 1 (2026-09-16): sample onsets down to ~3M (SageMaker Studio's
-- load ceiling: keep 100% of GATE, ~25% of TVM, ~9% of VALIDATOR, deterministic
-- on DW_DEVICE_EVENT_ID) BEFORE computing prior-window features, not after --
-- see the `sampled` CTE below.
--
-- PERF ROUND 2 (2026-09-16): device_event_enriched (190M+ rows, NOT
-- partitioned -- only a manual post-load ZORDER OPTIMIZE is suggested in its
-- own CREATE TABLE, and it has never actually been run) was being read and
-- re-filtered from scratch once per prior_* self-join. Materialize and cache
-- that filtered history ONCE (oos_history below), bounded by a static date
-- floor 30 days before onsets' earliest possible transit_day.
--
-- IDEMPOTENCY (2026-09-16): CACHE TABLE ... AS SELECT registers oos_history
-- as a SESSION-scoped temp view. A run that fails partway through this file
-- (before reaching the DROP VIEW at the end) leaves it registered in that
-- session; the next run's CACHE TABLE then fails with
-- TEMP_TABLE_OR_VIEW_ALREADY_EXISTS. Drop it defensively before creating it
-- too, so reruns are safe after a partial failure, not just after a clean one.
--
-- REVERTED SAME DAY: tried ANALYZE TABLE oos_history here to help the
-- optimizer with prior_station's low-cardinality join key. Broke the build --
-- this environment runs on Spark Connect / Unity Catalog governed compute
-- (confirmed from the failing run's stack trace), where CACHE TABLE creates a
-- Spark-local relation, not a Unity Catalog table, so ANALYZE TABLE can never
-- resolve it. Removed outright.
DROP VIEW IF EXISTS oos_history;

CACHE TABLE oos_history AS
SELECT
    DEVICE_ID, EVENT_DTM, DW_DEVICE_EVENT_ID, component_subsystem,
    is_reader_event, requires_service_call, event_priority,
    COMPONENT_SERIAL_NBR, FACILITY_ID, mars_device_category,
    unix_timestamp(EVENT_DTM) AS _evt_ts
FROM mars_dev.silver.device_event_enriched
WHERE is_hardware_oos_event = TRUE
  AND EVENT_STATE_TYPE_NAME = 'Set'
  AND transit_day >= DATE '2023-12-01';

-- PERF ROUND 3 (2026-09-16) -- THE STRUCTURAL FIX, NOT VERIFIED AGAINST LIVE
-- EXECUTION. Rounds 1-2 reduced the SIZE of the four prior_* self-joins'
-- inputs but never touched the joins themselves. Spark's equi-join on
-- DEVICE_ID can only use DEVICE_ID for the hash/shuffle key -- the time-
-- window predicates (p.EVENT_DTM < o.EVENT_DTM AND p.EVENT_DTM >=
-- o.EVENT_DTM - INTERVAL n) are correlated against another row's column, so
-- Spark applies them as a filter AFTER the join, not as part of it. That
-- means Spark first materialises every (onset x that device's ENTIRE
-- history) pair, then discards everything outside the window. Using this
-- file's own measured profile in the header above: TVM alone is roughly
-- 1.05M sampled onsets x ~8,859 avg events/device =~ 9.3 BILLION
-- intermediate rows for ONE of the four joins; VALIDATOR and GATE add
-- billions more. Caching and sampling never touched this -- it isn't a
-- scan-count or row-count problem, it's the join operator's shape.
--
-- Fix: replace all four self-joins with window functions over oos_history,
-- computed via one sort-per-partition pass instead of a join -- the same
-- RANGE BETWEEN ... PRECEDING pattern this repo already uses for the same
-- class of problem in 24_device_incident_features_daily__create.sql
-- (Gap 2 RC3). ORDER BY unix_timestamp(EVENT_DTM) (seconds, computed once
-- into oos_history._evt_ts above) because Spark's window RANGE frame needs a
-- numeric/date ordering column for integer bounds; "N PRECEDING AND
-- 1 PRECEDING" reproduces "< current AND >= current - N seconds" exactly, at
-- SECOND granularity.
--
-- KNOWN PRECISION DIFFERENCE from the original self-join (which compared
-- full TIMESTAMP values, sub-second precision): two events for the SAME
-- device in the SAME SECOND are tied here and both excluded from each
-- other's prior-window count, whereas the original ordered them correctly at
-- sub-second resolution. Accepted as narrow and unlikely to matter for
-- sensor-logged hardware OOS onsets; flagged explicitly rather than left to
-- be discovered by surprise.
--
-- history_windowed computes every prior-window aggregate for EVERY row in
-- oos_history, not just the sampled ~3M -- window functions need the full,
-- continuous per-partition history to count correctly; sampling first would
-- silently undercount. The final SELECT then does ONE cheap, unique-key
-- equi-join from `sampled` onto this precomputed table -- a lookup, not a
-- fan-out join.
--
-- prior_station (FACILITY_ID + mars_device_category keyed -- far lower-
-- cardinality than DEVICE_ID, and per the user's analysis the most plausible
-- skew source: VALIDATOR buses concentrated at a small number of garages)
-- needed two adaptations, since its self-join excluded THIS DEVICE
-- (p.DEVICE_ID <> o.DEVICE_ID), not a tied timestamp -- COUNT(DISTINCT ...)
-- also isn't allowed inside a window frame in Spark SQL at all:
--   station_oos_events_24h = (all events at this facility+category in the
--     last 24h, via a window partitioned by FACILITY_ID+mars_device_category)
--     MINUS prior_oos_24h (this device's own count in the same window) --
--     arithmetic, not array manipulation. Exactly reproduces "other devices'
--     events" because this device's own facility-window events are always a
--     subset of the facility-window total.
--   station_devices_oos_24h = size(array_remove(collect_set(DEVICE_ID) OVER
--     the same window, DEVICE_ID)) -- the distinct-device set for the
--     window with this row's own device removed (collect_set already
--     dedupes, so array_remove strips at most one entry).
--
-- REVISED SAME DAY: the collect_set(...) OVER (window) approach above failed
-- on the actual run -- [MISSING_GROUP_BY]. Catalyst rewrote the three
-- collect_set window expressions into a separate internal Aggregate node
-- with no GROUP BY (visible in the failing plan immediately above
-- `SubqueryAlias oos_history`), while the COUNT/SUM/MIN window expressions
-- planned correctly into proper Window nodes with no error. This is a real
-- limitation of this Spark/Catalyst version's window-function planning for
-- collection-building ("ImperativeAggregate") functions like collect_set --
-- not a SQL logic error, and not something reasoning about the SQL alone
-- would have predicted.
-- Fix: replaced every collect_set(...) OVER (...) with
-- approx_count_distinct(...) OVER (...) -- an algebraic/mergeable aggregate
-- in the same category as COUNT/SUM/MIN (which all planned correctly), not
-- a collection-building one, so it should not hit the same Catalyst path.
-- Trade-off: HyperLogLog-based approximate counts instead of exact ones.
-- Accepted given the small cardinalities involved (a handful of subsystem
-- values per category per the header; per-device serial counts within a 7d
-- window) -- HLL-style sketches are near-exact at this scale in practice,
-- though this is not a formal guarantee. station_devices_oos_24h could no
-- longer use array_remove on a collected set, so it uses the same
-- subtraction technique as station_oos_events_24h instead: this device's
-- own ID is in the facility-window's approx-distinct-device count exactly
-- when prior_oos_24h > 0 (see the CASE WHEN next to it below).
--
-- CORRECTNESS NOT YET CONFIRMED. Run the post-build validation block at the
-- end of this file -- especially the new check #5, a manual spot-check of
-- one known device's counts -- before trusting this for training. This is
-- the highest-risk change made to this file this session; runtime and
-- correctness both need confirming, not just "did it execute."
CREATE OR REPLACE TABLE mars_dev.gold.device_ps3_oos_component
USING DELTA
PARTITIONED BY (transit_day)
AS
WITH onsets AS (
    SELECT
        e.DW_DEVICE_EVENT_ID,
        e.DEVICE_ID,
        e.DEVICE_KEY,
        e.mars_device_category,
        e.EVENT_DTM,
        e.transit_day,
        e.EVENT_DAY_KEY,
        e.hour_of_day,
        e.day_of_week,
        e.month_of_year,
        e.FACILITY_ID,
        e.FACILITY_NAME,
        e.OPERATOR_ID,
        e.DEVICE_TYPE_NAME,
        e.STOP_POINT_ID,
        -- ---- target + component identity -------------------------------
        e.component_subsystem                         AS target_component_subsystem,
        e.COMPONENT_TYPE_NAME,
        e.COMPONENT_TYPE_ID,
        e.COMPONENT_SERIAL_NBR,
        e.COMPONENT_POSITION,
        -- ---- onset-time attributes (safe features) ---------------------
        e.EVENT_TYPE_ID,
        e.EVENT_TYPE_NAME,
        e.event_priority,
        e.event_type_severity,
        e.severity,
        e.is_reader_event,
        e.requires_service_call,
        e.is_set_clear,
        e.oos_counted_gate_kpi,
        e.oos_counted_bus_kpi,
        e.oos_counted_fmvd_kpi,
        -- ---- OUTCOME columns: analysis only, NEVER features ------------
        e.CLEAR_DTM                                   AS outcome_clear_dtm,
        e.duration_to_clear_min                       AS outcome_duration_to_clear_min,
        e.AUTOMATIC_CLEAR_FLAG                        AS outcome_automatic_clear_flag,
        e.MATCHED_FLAG                                AS outcome_matched_flag
    FROM mars_dev.silver.device_event_enriched e
    WHERE e.is_hardware_oos_event   = TRUE
      AND e.EVENT_STATE_TYPE_NAME   = 'Set'
      AND COALESCE(e.is_commanded_oos_event, FALSE) = FALSE   -- belt-and-braces
      AND e.component_subsystem IS NOT NULL
      AND e.mars_device_category IN ('TVM', 'GATE', 'VALIDATOR')
      AND e.transit_day >= DATE '2024-01-01'
      AND e.transit_day <= CURRENT_DATE()                     -- sentinel-date guard
),

-- Deterministic stratified sample, applied BEFORE prior-window features are
-- looked up (not after) -- see PERF ROUND 1 above. Keeps every GATE row
-- (smallest class, 895,638) and caps the two larger categories: ~25% of TVM
-- -> ~1.05M, ~9% of VALIDATOR -> ~1.05M, ~3M rows total.
sampled AS (
    SELECT o.*
    FROM onsets o
    WHERE
          o.mars_device_category = 'GATE'
       OR (o.mars_device_category = 'TVM'       AND ABS(HASH(o.DW_DEVICE_EVENT_ID)) % 100 < 25)
       OR (o.mars_device_category = 'VALIDATOR' AND ABS(HASH(o.DW_DEVICE_EVENT_ID)) % 100 < 9)
),

-- All four prior-window feature sets, computed once via window functions
-- over the FULL oos_history (not `sampled` -- see PERF ROUND 3 above for why).
history_windowed AS (
    SELECT
        DW_DEVICE_EVENT_ID,

        -- prior_device (24h, partitioned by DEVICE_ID)
        COUNT(*)                                                OVER w_24h AS prior_oos_24h,
        approx_count_distinct(component_subsystem)              OVER w_24h AS prior_distinct_subsystems_24h,
        SUM(CASE WHEN is_reader_event      THEN 1 ELSE 0 END)   OVER w_24h AS prior_reader_oos_24h,
        SUM(CASE WHEN requires_service_call THEN 1 ELSE 0 END)  OVER w_24h AS prior_service_call_oos_24h,
        MIN(event_priority)                                     OVER w_24h AS prior_min_priority_24h,

        -- prior_device_7d (7d, partitioned by DEVICE_ID)
        COUNT(*)                                                OVER w_7d  AS prior_oos_7d,
        approx_count_distinct(component_subsystem)              OVER w_7d  AS prior_distinct_subsystems_7d,
        approx_count_distinct(COMPONENT_SERIAL_NBR)              OVER w_7d  AS prior_distinct_serials_7d,

        -- prior_serial (30d, partitioned by DEVICE_ID + COMPONENT_SERIAL_NBR)
        -- NULL-guarded: the original self-join required
        -- o.COMPONENT_SERIAL_NBR IS NOT NULL, so a NULL serial always
        -- produced no match. A window PARTITION BY that includes a NULL
        -- COMPONENT_SERIAL_NBR would otherwise group all NULL-serial events
        -- for a device together, which is not the original's semantics.
        CASE WHEN COMPONENT_SERIAL_NBR IS NOT NULL
             THEN COUNT(*)     OVER w_30d_serial END             AS prior_serial_oos_30d_raw,
        CASE WHEN COMPONENT_SERIAL_NBR IS NOT NULL
             THEN MAX(EVENT_DTM) OVER w_30d_serial END           AS prior_serial_last_oos_dtm_raw,

        -- prior_station (24h, partitioned by FACILITY_ID + mars_device_category)
        -- Self-excluded via subtraction (all events/devices at this facility+
        -- category MINUS this device's own contribution) rather than a join
        -- predicate. station_devices_oos_24h: this device's own ID is in the
        -- facility-window's distinct-device set exactly when it has at least
        -- one prior event of its own in the same 24h window (prior_oos_24h >
        -- 0) -- w_station_24h's partition is broader than w_24h's but shares
        -- the same RANGE bound, so this device's own prior events (if any)
        -- are always a subset of the facility-window population.
        (COUNT(*) OVER w_station_24h) - (COUNT(*) OVER w_24h)    AS station_oos_events_24h,
        (approx_count_distinct(DEVICE_ID) OVER w_station_24h)
            - (CASE WHEN (COUNT(*) OVER w_24h) > 0 THEN 1 ELSE 0 END)
                                                                  AS station_devices_oos_24h

    FROM oos_history
    WINDOW
        w_24h         AS (PARTITION BY DEVICE_ID
                           ORDER BY _evt_ts
                           RANGE BETWEEN 86400   PRECEDING AND 1 PRECEDING),
        w_7d          AS (PARTITION BY DEVICE_ID
                           ORDER BY _evt_ts
                           RANGE BETWEEN 604800  PRECEDING AND 1 PRECEDING),
        w_30d_serial  AS (PARTITION BY DEVICE_ID, COMPONENT_SERIAL_NBR
                           ORDER BY _evt_ts
                           RANGE BETWEEN 2592000 PRECEDING AND 1 PRECEDING),
        w_station_24h AS (PARTITION BY FACILITY_ID, mars_device_category
                           ORDER BY _evt_ts
                           RANGE BETWEEN 86400   PRECEDING AND 1 PRECEDING)
),

-- Component age at onset, from the SCD-current hardware config. Unrelated to
-- the self-join problem above (joins hw_config_current, not device_event_
-- enriched) -- unchanged.
component_age AS (
    SELECT
        o.DW_DEVICE_EVENT_ID,
        MAX(h.component_age_days)                                                AS component_age_days
    FROM sampled o
    LEFT JOIN mars_dev.silver.hw_config_current h
           ON h.DEVICE_ID            = o.DEVICE_ID
          AND h.COMPONENT_SERIAL_NBR = o.COMPONENT_SERIAL_NBR
    GROUP BY o.DW_DEVICE_EVENT_ID
)

SELECT
    s.DW_DEVICE_EVENT_ID,
    s.DEVICE_ID,
    s.DEVICE_KEY,
    s.mars_device_category,
    s.EVENT_DTM,
    s.transit_day,
    s.hour_of_day,
    s.day_of_week,
    s.month_of_year,
    s.FACILITY_ID,
    s.FACILITY_NAME,
    s.OPERATOR_ID,
    s.DEVICE_TYPE_NAME,
    s.STOP_POINT_ID,

    s.target_component_subsystem,
    s.COMPONENT_TYPE_NAME,
    s.COMPONENT_TYPE_ID,
    s.COMPONENT_SERIAL_NBR,
    s.COMPONENT_POSITION,
    ca.component_age_days,

    s.EVENT_TYPE_ID,
    s.EVENT_TYPE_NAME,
    s.event_priority,
    s.event_type_severity,
    s.severity,
    s.is_reader_event,
    s.requires_service_call,
    s.oos_counted_gate_kpi,
    s.oos_counted_bus_kpi,
    s.oos_counted_fmvd_kpi,

    COALESCE(hw.prior_oos_24h, 0)                    AS prior_oos_24h,
    COALESCE(hw.prior_distinct_subsystems_24h, 0)    AS prior_distinct_subsystems_24h,
    COALESCE(hw.prior_reader_oos_24h, 0)             AS prior_reader_oos_24h,
    COALESCE(hw.prior_service_call_oos_24h, 0)       AS prior_service_call_oos_24h,
    hw.prior_min_priority_24h,
    COALESCE(hw.prior_oos_7d, 0)                     AS prior_oos_7d,
    COALESCE(hw.prior_distinct_subsystems_7d, 0)     AS prior_distinct_subsystems_7d,
    COALESCE(hw.prior_distinct_serials_7d, 0)        AS prior_distinct_serials_7d,
    COALESCE(hw.prior_serial_oos_30d_raw, 0)         AS prior_serial_oos_30d,
    DATEDIFF(s.EVENT_DTM, hw.prior_serial_last_oos_dtm_raw) AS prior_serial_days_since_last_oos,
    COALESCE(hw.station_devices_oos_24h, 0)          AS station_devices_oos_24h,
    COALESCE(hw.station_oos_events_24h, 0)           AS station_oos_events_24h,

    -- outcome columns: analysis only, excluded from the feature list
    s.outcome_clear_dtm,
    s.outcome_duration_to_clear_min,
    s.outcome_automatic_clear_flag,
    s.outcome_matched_flag,

    'CHI'                                            AS city_id,
    CURRENT_TIMESTAMP()                              AS _gold_load_ts
FROM sampled s
LEFT JOIN history_windowed hw ON hw.DW_DEVICE_EVENT_ID = s.DW_DEVICE_EVENT_ID
LEFT JOIN component_age    ca ON ca.DW_DEVICE_EVENT_ID = s.DW_DEVICE_EVENT_ID
;

-- Free the cache -- run_layer_gold.py builds 5+ other gold tables in the same
-- session; leaving this cached would hold cluster memory those builds need.
-- DROP VIEW (not just UNCACHE TABLE): dropping a cached temp view also
-- unpersists it, and additionally removes the temp view's name registration
-- -- see the IDEMPOTENCY note above CACHE TABLE for why that distinction
-- matters here specifically.
DROP VIEW IF EXISTS oos_history;

-- ---------------------------------------------------------------------
-- Post-build validation. Run these; do not assume.
-- ---------------------------------------------------------------------
-- 1) grain uniqueness -- must return 0
-- SELECT COUNT(*) - COUNT(DISTINCT DW_DEVICE_EVENT_ID) AS dup_rows
-- FROM mars_dev.gold.device_ps3_oos_component;
--
-- 2) class balance per category -- every category needs >= 3 classes
-- SELECT mars_device_category, target_component_subsystem, COUNT(*) AS rows,
--        COUNT(DISTINCT DEVICE_ID) AS devices
-- FROM mars_dev.gold.device_ps3_oos_component
-- GROUP BY 1,2 ORDER BY 1, rows DESC;
--
-- 3) size sanity -- expect roughly 3M rows total
-- SELECT mars_device_category, COUNT(*) FROM mars_dev.gold.device_ps3_oos_component
-- GROUP BY 1 ORDER BY 2 DESC;
--
-- 4) leakage guard -- no prior-window column may correlate perfectly with the
--    target. The notebook's single-feature audit enforces this at train time;
--    this is the cheap pre-check.
-- SELECT target_component_subsystem, AVG(prior_serial_oos_30d), AVG(prior_oos_24h)
-- FROM mars_dev.gold.device_ps3_oos_component GROUP BY 1 ORDER BY 1;
--
-- 5) NEW (2026-09-16) -- manual spot-check of the round-3 window-function
--    rewrite. Pick one device with several onsets close together in time and
--    manually verify prior_oos_24h / prior_oos_7d / station_oos_events_24h /
--    station_devices_oos_24h against what a hand count (or the old self-join
--    logic, if you still have a table built with it to compare against)
--    would produce. This is the check that actually confirms correctness,
--    not just that the query ran.
-- SELECT DEVICE_ID, EVENT_DTM, prior_oos_24h, prior_oos_7d,
--        prior_serial_oos_30d, station_devices_oos_24h, station_oos_events_24h
-- FROM mars_dev.gold.device_ps3_oos_component
-- WHERE DEVICE_ID = '<pick one with several close-together onsets>'
-- ORDER BY EVENT_DTM;
