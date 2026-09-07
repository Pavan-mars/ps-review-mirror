-- =============================================================================
-- S02: silver.dim_stop_point
-- S-code: S02  |  Build file: 20  |  Status: READY (NCS_STAGE version)
--
-- PURPOSE:
--   Stop point / station location dimension for PS1/PS2/PS4 geo features.
--   Provides lat/long coordinates and zone/route context per stop point.
--
-- SOURCE (current):
--   NCS_STAGE.STOP_POINT  (bronze: mars_dev.bronze.ncs_stage_stop_point)
--   18 columns, exact row count unknown -- full refresh each build.
--
-- PENDING: EDW.STOP_POINT_DIMENSION (Michael R2-11)
--   EDW version is NOT in the 1,009 accessible Oracle tables as of 2026-06-24.
--   When Michael/Cubic confirm access, rebuild from EDW source and add:
--   ADDRESS, CTA_STOP_ID, RAIL_STATION_FLAG, LINE_NAME, DIRECTION.
--   Until then, NCS_STAGE version provides LATITUDE/LONGITUDE for geo joins.
--
-- JOIN TARGETS:
--   silver.device_event_enriched:  ON STOP_POINT_ID = de.STOP_POINT_ID
--   silver.device_outage:          ON STOP_POINT_ID = do.STOP_POINT_ID (via event)
--   gold PS2:                      grouping cascades by zone + facility proximity
--
-- KEY COLUMN NOTES:
--   LATITUDE / LONGITUDE: VARCHAR2(12) in Oracle -- CAST to DOUBLE for geo math
--   ACTIVE_FLAG: NUMBER(1) -- 1 = active, 0 = inactive; filter with ACTIVE_FLAG = 1
--   STOP_POINT_TYPE_ID: 1=rail station, 2=bus stop (verify with Cubic -- NCS unconfirmed)
--   ZONE_ID: fare zone identifier -- links to zone-based availability KPIs
--   LINE_ROUTE_ID: NCS routing ID for bus routes; NULL for most rail stations
--
-- Michael R2 changes (2026-06-24):
--   R2-11: dim_stop_point required for PS2 gate-bank cascade and PS4 geo anomaly.
--          Using NCS_STAGE version until EDW access confirmed.
-- =============================================================================

DROP TABLE IF EXISTS mars_dev.silver.dim_stop_point;

CREATE TABLE mars_dev.silver.dim_stop_point AS
SELECT
    sp.STOP_POINT_ID,
    sp.NAME                                                     AS stop_point_name,
    sp.STOP_POINT_TYPE_ID,

    -- Location
    -- LATITUDE/LONGITUDE stored as VARCHAR2(12) in Oracle -- cast to DOUBLE for geo
    --
    -- FIX 2026-09-04 (DQ finding: latitude/longitude showed 100% null in Silver DQ. Root cause:
    -- source values are in DMS (degrees/minutes/seconds) format, e.g. "42 21 34.1", not plain
    -- decimal degrees -- TRY_CAST silently returns NULL for every row in this format. Confirmed
    -- via grep across all 30 Silver files and all 6 Gold files: nothing downstream currently reads
    -- these two columns at all -- they were added ahead of planned PS2 gate-bank cascade / PS4 geo
    -- anomaly features that were never actually wired in. Removed rather than fixed with an
    -- untested DMS parser, since there is no current consumer to fix for and a parser should be
    -- built and verified against real data at the point something actually needs it, not
    -- speculatively now. latitude_raw/longitude_raw are UNCHANGED and continue to carry the real
    -- Bronze values correctly -- confirmed matching Bronze's null count exactly (34/12,887), this
    -- was never a Bronze data problem. OLD version commented out, not deleted:
    -- TRY_CAST(sp.LATITUDE  AS DOUBLE)                            AS latitude,
    -- TRY_CAST(sp.LONGITUDE AS DOUBLE)                            AS longitude,
    sp.LATITUDE                                                 AS latitude_raw,
    sp.LONGITUDE                                                AS longitude_raw,
    sp.RADIUS_OF_CONFIDENCE,

    -- Fare and routing context
    sp.ZONE_ID,
    sp.LINE_ROUTE_ID,
    sp.OPERATOR_ID,
    sp.SECTOR_ID,
    sp.ALIGHTING_SECTOR_ID,
    sp.ALIGHTING_ZONE_ID,

    -- Transfer and display
    sp.TRANSFER_GROUP_ID,
    sp.STOP_POINT_DISPLAY_INDEX,
    sp.BUSINESS_NUMBER,
    sp.EXTERNAL_REFERENCE,

    -- Status
    (sp.ACTIVE_FLAG = 1)                                        AS is_active,
    sp.ACTIVE_FLAG,
    sp.UPDATED_DTM

FROM mars_dev.bronze.ncs_stage_stop_point sp;
-- No filter: load all stop points including inactive (is_active = FALSE).
-- Downstream joins use WHERE dim_stop_point.is_active = TRUE for current locations.

-- Post-load optimisation:
-- OPTIMIZE mars_dev.silver.dim_stop_point ZORDER BY (STOP_POINT_ID);

-- Post-build verification:
-- FIX 2026-09-04: updated to match the removed latitude/longitude columns -- was referencing
-- columns that no longer exist after the fix above, would have errored if run as-is.
-- OLD version commented out, not deleted:
-- SELECT COUNT(*)                                                AS total_rows,
--        SUM(CASE WHEN is_active = TRUE  THEN 1 ELSE 0 END)    AS active_stops,
--        SUM(CASE WHEN latitude IS NULL  THEN 1 ELSE 0 END)    AS null_lat,
--        SUM(CASE WHEN longitude IS NULL THEN 1 ELSE 0 END)    AS null_lon,
--        COUNT(DISTINCT ZONE_ID)                                AS zones,
--        COUNT(DISTINCT OPERATOR_ID)                            AS operators
-- FROM mars_dev.silver.dim_stop_point;
-- SELECT COUNT(*)                                                AS total_rows,
--        SUM(CASE WHEN is_active = TRUE  THEN 1 ELSE 0 END)    AS active_stops,
--        SUM(CASE WHEN latitude_raw IS NULL  THEN 1 ELSE 0 END) AS null_lat_raw,
--        SUM(CASE WHEN longitude_raw IS NULL THEN 1 ELSE 0 END) AS null_lon_raw,
--        COUNT(DISTINCT ZONE_ID)                                AS zones,
--        COUNT(DISTINCT OPERATOR_ID)                            AS operators
-- FROM mars_dev.silver.dim_stop_point;
