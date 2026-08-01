-- =============================================================================
-- OOS <-> Chargeable real mapping, at DEVICE grain and SERIAL/COMPONENT grain
--
-- Purpose: quantify -- with real counts, not assumption -- exactly how "chargeable
-- SLA failure" relates to "any hardware OOS" per device and per component serial.
-- This is the evidence behind the PS1/PS2/PS5 label redefinition (2026-07-23/24):
-- is_chargeable is a NARROWER subset of is_hardware_oos_event, never the reverse.
-- Every row counted here already IS a hardware-OOS event by construction --
-- silver.device_outage (S18) is built 100% from device_event_enriched WHERE
-- is_hardware_oos_event = TRUE (verified against sql/silver/18_device_outage__create.sql).
-- is_chargeable sits on the SAME row (joined from incident_root_cause, R2-1:
-- failure_level > 0 => is_chargeable = TRUE) -- so this needs zero extra joins.
--
-- FIX (2026-07-24, v2): v1 sourced the serial-grain mapping from
-- device_outage.COMPONENT_SERIAL_NBR (i.e. device_event_enriched's per-EVENT serial
-- column). That column is populated only when a specific fault event happened to
-- carry a component serial -- confirmed sparse in practice (a live run showed
-- serial_sum_oos = NULL for ~47/50 of the top devices). Per the Data Foundation doc
-- (Michael's clarifications, 17-Jul-2026, re-ingested 24-Jul): the AUTHORITATIVE,
-- ~100%-populated device<->serial mapping is bronze.edw_device_current_hw_config,
-- already materialized as silver.hw_config_current (S09) -- confirmed by reading
-- its DDL directly. v2 sources the serial-grain mapping from THAT table instead.
-- Caveat this creates: hw_config_current gives every device's known component
-- serials, but does NOT tell you which specific OOS event hit which specific
-- component (device_event_enriched rarely records that link) -- so the OOS/
-- chargeable totals on the serial-grain view are the DEVICE's totals, repeated
-- across each of its known serials, not independently split per serial. The
-- old event-attributed view is kept separately (section 3b) for the minority of
-- rows where a real per-event serial link exists, clearly labeled as partial.
--
-- Scope: FULL HISTORY (no date filter). Grain: ONE ROW PER DEVICE / PER SERIAL
-- (lifetime totals), not per-day.
--
-- Run on Databricks (mars_dev), read-only -- no table is created or altered except
-- the named VIEWs (safe to re-run; CREATE OR REPLACE).
--
-- Output columns (device + serial mapping views):
--   total_hardware_oos_events     every is_hardware_oos_event='Set' episode (the full population)
--   total_chargeable_events       the subset also is_chargeable = TRUE (failure_level > 0)
--   non_chargeable_hw_oos_events  total - chargeable -- these are the events the OLD label
--                                 (chargeable-only) silently dropped and the redefinition adds back
--   chargeable_pct                total_chargeable_events / total_hardware_oos_events * 100
-- =============================================================================

USE CATALOG mars_dev;

-- -----------------------------------------------------------------------------
-- 0) HEADLINE: overall + per-category rollup -- read this first
-- -----------------------------------------------------------------------------
SELECT
    mars_device_category,
    COUNT(DISTINCT DEVICE_ID)                                                       AS devices_with_hw_oos,
    COUNT(*)                                                                        AS total_hardware_oos_events,
    SUM(CASE WHEN is_chargeable THEN 1 ELSE 0 END)                                  AS total_chargeable_events,
    COUNT(*) - SUM(CASE WHEN is_chargeable THEN 1 ELSE 0 END)                       AS non_chargeable_hw_oos_events,
    ROUND(100.0 * SUM(CASE WHEN is_chargeable THEN 1 ELSE 0 END) / COUNT(*), 2)     AS chargeable_pct,
    MIN(transit_day)                                                                AS earliest_event,
    MAX(transit_day)                                                                AS latest_event
FROM mars_dev.silver.device_outage
GROUP BY ROLLUP(mars_device_category)
ORDER BY mars_device_category;
-- Read this as: for TVM/GATE, (100 - chargeable_pct)% of real hardware-OOS events are
-- currently INVISIBLE to will_fail_3d/7d/14d because they weren't chargeable. VALIDATOR
-- isn't gated by is_chargeable in PS1 today (R6-1 already reads OOS-Set episodes
-- directly) -- its row is for comparison only.

-- -----------------------------------------------------------------------------
-- 1) Serial coverage check -- TWO views of coverage, side by side, so the gap is visible
--    (a) event-attributed: what device_event_enriched itself records per fault event (sparse)
--    (b) catalog-based: hw_config_current's actual device<->serial mapping (should be ~complete)
-- -----------------------------------------------------------------------------
SELECT
    do_.mars_device_category,
    COUNT(DISTINCT do_.DEVICE_ID)                                                       AS devices_with_hw_oos,
    SUM(CASE WHEN do_.COMPONENT_SERIAL_NBR IS NOT NULL THEN 1 ELSE 0 END)               AS oos_events_with_event_level_serial,
    COUNT(*)                                                                            AS total_hardware_oos_events,
    ROUND(100.0 * SUM(CASE WHEN do_.COMPONENT_SERIAL_NBR IS NOT NULL THEN 1 ELSE 0 END)
                / COUNT(*), 2)                                                          AS event_level_serial_coverage_pct,
    COUNT(DISTINCT hw.DEVICE_ID)                                                        AS devices_with_a_catalog_serial,
    ROUND(100.0 * COUNT(DISTINCT hw.DEVICE_ID) / COUNT(DISTINCT do_.DEVICE_ID), 2)      AS catalog_device_serial_coverage_pct
FROM mars_dev.silver.device_outage do_
LEFT JOIN (SELECT DISTINCT DEVICE_ID FROM mars_dev.silver.hw_config_current
           WHERE COMPONENT_SERIAL_NBR IS NOT NULL) hw
       ON hw.DEVICE_ID = do_.DEVICE_ID
GROUP BY do_.mars_device_category
ORDER BY do_.mars_device_category;
-- event_level_serial_coverage_pct will be low (this is the v1 bug -- device_event_enriched
-- rarely records a per-event serial). catalog_device_serial_coverage_pct should be close to
-- 100% for TVM/GATE/VALIDATOR -- that's hw_config_current, the real mapping "established
-- long ago." If any category's catalog coverage ISN'T near 100%, section (4) below finds
-- exactly which devices are missing from hw_config_current.

-- -----------------------------------------------------------------------------
-- 2) DEVICE-grain mapping (one row per DEVICE_ID, lifetime totals) -- unchanged, correct in v1
-- -----------------------------------------------------------------------------
CREATE OR REPLACE VIEW mars_dev.audit.oos_chargeable_mapping_device AS
SELECT
    DEVICE_ID,
    MAX(mars_device_category)                                                       AS mars_device_category,
    COUNT(*)                                                                        AS total_hardware_oos_events,
    SUM(CASE WHEN is_chargeable THEN 1 ELSE 0 END)                                  AS total_chargeable_events,
    COUNT(*) - SUM(CASE WHEN is_chargeable THEN 1 ELSE 0 END)                       AS non_chargeable_hw_oos_events,
    ROUND(100.0 * SUM(CASE WHEN is_chargeable THEN 1 ELSE 0 END) / COUNT(*), 2)     AS chargeable_pct,
    MIN(transit_day)                                                                AS first_event_date,
    MAX(transit_day)                                                                AS last_event_date
FROM mars_dev.silver.device_outage
GROUP BY DEVICE_ID;

SELECT * FROM mars_dev.audit.oos_chargeable_mapping_device
ORDER BY total_hardware_oos_events DESC
LIMIT 200;

-- -----------------------------------------------------------------------------
-- 3) SERIAL-grain mapping v2 -- sourced from hw_config_current (the authoritative
--    device<->serial catalog), joined to the device-level OOS/chargeable totals.
--    One row per (device, known component serial). Counts are the DEVICE's totals,
--    repeated per serial -- NOT independently split per component (see header note).
-- -----------------------------------------------------------------------------
CREATE OR REPLACE VIEW mars_dev.audit.oos_chargeable_mapping_serial AS
SELECT
    hw.DEVICE_ID,
    hw.mars_device_category,
    hw.COMPONENT_SERIAL_NBR                                                         AS serial_id,
    hw.COMPONENT_DESCRIPTION                                                        AS component_description,
    hw.component_age_days,
    d.total_hardware_oos_events,     -- device-level total (NOT serial-attributed -- see header)
    d.total_chargeable_events,       -- device-level total (NOT serial-attributed -- see header)
    d.non_chargeable_hw_oos_events,
    d.chargeable_pct,
    d.first_event_date,
    d.last_event_date
FROM mars_dev.silver.hw_config_current hw
JOIN mars_dev.audit.oos_chargeable_mapping_device d
  ON d.DEVICE_ID = hw.DEVICE_ID
WHERE hw.COMPONENT_SERIAL_NBR IS NOT NULL;

SELECT * FROM mars_dev.audit.oos_chargeable_mapping_serial
ORDER BY total_hardware_oos_events DESC
LIMIT 200;
-- Every device with hardware-OOS activity AND a known catalog serial now shows up here,
-- one row per component it has (a device with 3 known components -> 3 rows, same totals).

-- -----------------------------------------------------------------------------
-- 3b) Event-attributed serial view (kept separately) -- the minority of OOS events
--     that DO carry a real per-event component serial on device_event_enriched itself.
--     Useful if/when event-level component attribution improves; not a complete mapping today.
-- -----------------------------------------------------------------------------
CREATE OR REPLACE VIEW mars_dev.audit.oos_chargeable_mapping_serial_event_attributed AS
SELECT
    COMPONENT_SERIAL_NBR                                                            AS serial_id,
    MAX(DEVICE_ID)                                                                  AS device_id,
    MAX(mars_device_category)                                                       AS mars_device_category,
    MAX(COMPONENT_TYPE_NAME)                                                        AS component_type_name,
    COUNT(*)                                                                        AS total_hardware_oos_events,
    SUM(CASE WHEN is_chargeable THEN 1 ELSE 0 END)                                  AS total_chargeable_events,
    ROUND(100.0 * SUM(CASE WHEN is_chargeable THEN 1 ELSE 0 END) / COUNT(*), 2)     AS chargeable_pct
FROM mars_dev.silver.device_outage
WHERE COMPONENT_SERIAL_NBR IS NOT NULL
GROUP BY COMPONENT_SERIAL_NBR;

-- -----------------------------------------------------------------------------
-- 4) Data-gap check: in-scope devices with hardware-OOS activity but ZERO rows in
--    hw_config_current (i.e. genuinely missing from the catalog mapping, not a query bug)
-- -----------------------------------------------------------------------------
SELECT
    d.DEVICE_ID, d.mars_device_category, d.total_hardware_oos_events, d.total_chargeable_events
FROM mars_dev.audit.oos_chargeable_mapping_device d
LEFT JOIN (SELECT DISTINCT DEVICE_ID FROM mars_dev.silver.hw_config_current) hw
       ON hw.DEVICE_ID = d.DEVICE_ID
WHERE hw.DEVICE_ID IS NULL
ORDER BY d.total_hardware_oos_events DESC
LIMIT 100;
-- Expected to be a short list (or empty) given hw_config_current is driven from a LEFT JOIN
-- off dim_device, so every current device should have a row even with NULL component cols --
-- a device showing up here has NO row in hw_config_current at all, which is worth flagging
-- to Michael/the data team directly rather than assumed away.
