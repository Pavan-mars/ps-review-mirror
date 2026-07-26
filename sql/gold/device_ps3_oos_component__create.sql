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

-- Strictly prior-window device history. The join is < (not <=) on EVENT_DTM,
-- so an onset can never see itself or anything simultaneous.
prior_device AS (
    SELECT
        o.DW_DEVICE_EVENT_ID,
        COUNT(p.DW_DEVICE_EVENT_ID)                                              AS prior_oos_24h,
        COUNT(DISTINCT p.component_subsystem)                                    AS prior_distinct_subsystems_24h,
        SUM(CASE WHEN p.is_reader_event      THEN 1 ELSE 0 END)                  AS prior_reader_oos_24h,
        SUM(CASE WHEN p.requires_service_call THEN 1 ELSE 0 END)                 AS prior_service_call_oos_24h,
        MIN(p.event_priority)                                                    AS prior_min_priority_24h
    FROM onsets o
    LEFT JOIN mars_dev.silver.device_event_enriched p
           ON p.DEVICE_ID = o.DEVICE_ID
          AND p.is_hardware_oos_event = TRUE
          AND p.EVENT_STATE_TYPE_NAME = 'Set'
          AND p.EVENT_DTM <  o.EVENT_DTM
          AND p.EVENT_DTM >= o.EVENT_DTM - INTERVAL 24 HOURS
    GROUP BY o.DW_DEVICE_EVENT_ID
),
prior_device_7d AS (
    SELECT
        o.DW_DEVICE_EVENT_ID,
        COUNT(p.DW_DEVICE_EVENT_ID)                                              AS prior_oos_7d,
        COUNT(DISTINCT p.component_subsystem)                                    AS prior_distinct_subsystems_7d,
        COUNT(DISTINCT p.COMPONENT_SERIAL_NBR)                                   AS prior_distinct_serials_7d
    FROM onsets o
    LEFT JOIN mars_dev.silver.device_event_enriched p
           ON p.DEVICE_ID = o.DEVICE_ID
          AND p.is_hardware_oos_event = TRUE
          AND p.EVENT_STATE_TYPE_NAME = 'Set'
          AND p.EVENT_DTM <  o.EVENT_DTM
          AND p.EVENT_DTM >= o.EVENT_DTM - INTERVAL 7 DAYS
    GROUP BY o.DW_DEVICE_EVENT_ID
),

-- Prior-window recurrence for THIS component serial: the strongest available
-- signal that a specific physical component is degrading.
prior_serial AS (
    SELECT
        o.DW_DEVICE_EVENT_ID,
        COUNT(p.DW_DEVICE_EVENT_ID)                                              AS prior_serial_oos_30d,
        MAX(p.EVENT_DTM)                                                         AS prior_serial_last_oos_dtm
    FROM onsets o
    LEFT JOIN mars_dev.silver.device_event_enriched p
           ON p.DEVICE_ID           = o.DEVICE_ID
          AND p.COMPONENT_SERIAL_NBR = o.COMPONENT_SERIAL_NBR
          AND o.COMPONENT_SERIAL_NBR IS NOT NULL
          AND p.is_hardware_oos_event = TRUE
          AND p.EVENT_STATE_TYPE_NAME = 'Set'
          AND p.EVENT_DTM <  o.EVENT_DTM
          AND p.EVENT_DTM >= o.EVENT_DTM - INTERVAL 30 DAYS
    GROUP BY o.DW_DEVICE_EVENT_ID
),

-- Station co-failure stress, prior 24h, same facility + device family.
prior_station AS (
    SELECT
        o.DW_DEVICE_EVENT_ID,
        COUNT(DISTINCT p.DEVICE_ID)                                              AS station_devices_oos_24h,
        COUNT(p.DW_DEVICE_EVENT_ID)                                              AS station_oos_events_24h
    FROM onsets o
    LEFT JOIN mars_dev.silver.device_event_enriched p
           ON p.FACILITY_ID          = o.FACILITY_ID
          AND p.mars_device_category = o.mars_device_category
          AND p.DEVICE_ID           <> o.DEVICE_ID
          AND p.is_hardware_oos_event = TRUE
          AND p.EVENT_STATE_TYPE_NAME = 'Set'
          AND p.EVENT_DTM <  o.EVENT_DTM
          AND p.EVENT_DTM >= o.EVENT_DTM - INTERVAL 24 HOURS
    GROUP BY o.DW_DEVICE_EVENT_ID
),

-- Component age at onset, from the SCD-current hardware config.
component_age AS (
    SELECT
        o.DW_DEVICE_EVENT_ID,
        MAX(h.component_age_days)                                                AS component_age_days
    FROM onsets o
    LEFT JOIN mars_dev.silver.hw_config_current h
           ON h.DEVICE_ID            = o.DEVICE_ID
          AND h.COMPONENT_SERIAL_NBR = o.COMPONENT_SERIAL_NBR
    GROUP BY o.DW_DEVICE_EVENT_ID
),

-- Deterministic stratified sample. 16.7M onsets will not load in SageMaker
-- Studio; this keeps every GATE row (smallest class) and caps the two larger
-- categories. Deterministic on DW_DEVICE_EVENT_ID so re-runs are reproducible
-- and the temporal split stays stable.
sampled AS (
    SELECT o.*,
           ABS(HASH(o.DW_DEVICE_EVENT_ID)) % 100 AS _bucket
    FROM onsets o
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

    COALESCE(pd.prior_oos_24h, 0)                    AS prior_oos_24h,
    COALESCE(pd.prior_distinct_subsystems_24h, 0)    AS prior_distinct_subsystems_24h,
    COALESCE(pd.prior_reader_oos_24h, 0)             AS prior_reader_oos_24h,
    COALESCE(pd.prior_service_call_oos_24h, 0)       AS prior_service_call_oos_24h,
    pd.prior_min_priority_24h,
    COALESCE(p7.prior_oos_7d, 0)                     AS prior_oos_7d,
    COALESCE(p7.prior_distinct_subsystems_7d, 0)     AS prior_distinct_subsystems_7d,
    COALESCE(p7.prior_distinct_serials_7d, 0)        AS prior_distinct_serials_7d,
    COALESCE(ps.prior_serial_oos_30d, 0)             AS prior_serial_oos_30d,
    DATEDIFF(s.EVENT_DTM, ps.prior_serial_last_oos_dtm) AS prior_serial_days_since_last_oos,
    COALESCE(pst.station_devices_oos_24h, 0)         AS station_devices_oos_24h,
    COALESCE(pst.station_oos_events_24h, 0)          AS station_oos_events_24h,

    -- outcome columns: analysis only, excluded from the feature list
    s.outcome_clear_dtm,
    s.outcome_duration_to_clear_min,
    s.outcome_automatic_clear_flag,
    s.outcome_matched_flag,

    'CHI'                                            AS city_id,
    CURRENT_TIMESTAMP()                              AS _gold_load_ts
FROM sampled s
LEFT JOIN prior_device    pd  ON pd.DW_DEVICE_EVENT_ID  = s.DW_DEVICE_EVENT_ID
LEFT JOIN prior_device_7d p7  ON p7.DW_DEVICE_EVENT_ID  = s.DW_DEVICE_EVENT_ID
LEFT JOIN prior_serial    ps  ON ps.DW_DEVICE_EVENT_ID  = s.DW_DEVICE_EVENT_ID
LEFT JOIN prior_station   pst ON pst.DW_DEVICE_EVENT_ID = s.DW_DEVICE_EVENT_ID
LEFT JOIN component_age   ca  ON ca.DW_DEVICE_EVENT_ID  = s.DW_DEVICE_EVENT_ID
WHERE
      -- keep 100% of GATE (895,638 -- the smallest category)
      s.mars_device_category = 'GATE'
      -- ~25% of TVM  -> ~1.05M
   OR (s.mars_device_category = 'TVM'       AND s._bucket < 25)
      -- ~9% of VALIDATOR -> ~1.05M
   OR (s.mars_device_category = 'VALIDATOR' AND s._bucket < 9)
;

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
