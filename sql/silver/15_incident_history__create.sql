-- =============================================================================
-- S15: silver.incident_history -- rebuilt on servicenow_incident (2026-07-20)
-- =============================================================================
-- STATUS: ACTIVE. Promoted to production 2026-07-20, replacing the
-- cta_servicenow_incident-based build. The prior version is archived as
-- 15_incident_history__create_v1_cta_DEPRECATED.sql in this same folder --
-- kept for reference/rollback, not run by anything.
--
-- WHY THIS CHANGED:
-- cta_servicenow_incident (v1's source) had regressed from a documented
-- 7,028,048 rows (load 2026-06-24, see the archived v1 file's header) to a
-- live 278,001 rows, and had essentially no data in the 2023-07-01 ..
-- 2023-12-31 window (4 rows) needed to extend the PS1 training window back
-- from 2024-01-01 to 2023-07-01.
--
-- servicenow_incident was checked as an alternative (2026-07-20 validation):
--   - u_wm_asset -> dim_device.DEVICE_ID join (the SAME join key v1 already
--     used): 57.33% overall match rate (172,577 / 301,034 rows), and NONE of
--     those matches land outside TVM/GATE (137,769 TVM + 34,808 GATE = the
--     full matched count) -- confirms this source reaches the same TVM/GATE
--     population v1 targeted, just with far healthier volume.
--   - TVM-matched rows go back to 2020-10-26 (22,800 rows in the 2023 H2
--     window).
--   - GATE-matched rows only go back to 2023-06-30 (2,911 rows in the 2023 H2
--     window) -- this source has no GATE history before mid-2023 either, but
--     that's still full coverage for the 2023-07-01 target date.
--
-- CMDB ENRICHMENT: CONFIRMED NOT AVAILABLE (checked 2026-07-20) --------------
-- serial_number, install_date, manufacturer, model_category (the fields
-- driving S15 v1's documented PS3 +25% / PS5 +20% lift) are NOT wired in
-- below, and cannot be from any source checked so far:
--   1. servicenow_incident.cmdb_ci_sys_id was joined against all 5 XML-based
--      CMDB tables (servicenow_cmdb_ci_pos_device / card_handling / netgear /
--      onboard_card_interface / acc) -- ZERO matches across all 5. Their
--      columns (install_date/purchase_date/delivery_date/attested_date-style
--      fields only, no transit-hardware structure) suggest these are generic
--      corporate IT asset tables, not CTA transit fare equipment.
--   2. Tried cross-referencing servicenow_incident.cmdb_ci_display_value
--      against the OLD CSV-based cta_servicenow_cmdb_ci.name (the table S15
--      v1 already uses) in case the two export systems shared a naming
--      convention -- 0.30% match rate (520 / 172,577 TVM+GATE incidents),
--      i.e. noise, not a real link. The two systems name CIs incompletely
--      differently; there's no cross-reference path between them.
-- Net: this table has full incident-core data (timing, priority, severity,
-- category, cause, chargeable, description, device linkage) but no
-- CMDB-derived columns. This is sufficient for PS1 incident_frequency/MTTR
-- features; PS3/PS5 CMDB-dependent features would need a different source not
-- yet identified, or would have to keep reading from S15 v1 (currently
-- depleted, see above) until one is found.
--
-- DEDUP NOTE: unlike cta_servicenow_incident (CSV export, no sys_id, dedup by
-- incident number -- see S15 v1's "33 incident_number duplicates" comment),
-- servicenow_incident is an XML/CDC-style export with a real sys_id and an
-- _action = 'INSERT_OR_UPDATE' pattern suggesting repeat snapshots per record.
-- Dedup below keys off sys_id first (inner CTE), keeping each record's most
-- recently updated version; the outer QUALIFY by incident_number is kept as a
-- defensive backstop matching S15 v1's own pattern, in case dim_device's join
-- ever fans out.
--
-- SCOPE: TVM + GATE only, by explicit device-category filter below -- same
-- scope as S15 v1 (see that file's footer note: "S15 remains TVM + GATE only
-- by design"). VALIDATOR incident coverage remains silver.incident_task_ci_link
-- (S25), unaffected by this file.
-- =============================================================================

DROP TABLE IF EXISTS mars_dev.silver.incident_history;

CREATE TABLE mars_dev.silver.incident_history
USING DELTA
PARTITIONED BY (city_id)
AS
WITH

-- -- SOURCE: servicenow_incident, deduped to one row per sys_id -------------
inc AS (
SELECT
    sys_id,
    number                                            AS incident_number,
    CAST(incident_state AS INT)                       AS incident_state,
    u_maintenance_type                                AS maintenance_type,

    -- Timing
    CAST(opened_at   AS TIMESTAMP)                    AS opened_dtm,
    CAST(resolved_at AS TIMESTAMP)                    AS resolved_dtm,
    CAST(closed_at   AS TIMESTAMP)                    AS closed_dtm,

    CASE
        WHEN resolved_at IS NOT NULL AND opened_at IS NOT NULL
        THEN (unix_timestamp(CAST(resolved_at AS TIMESTAMP))
            - unix_timestamp(CAST(opened_at   AS TIMESTAMP))) / 60.0
        ELSE NULL
    END                                                AS time_to_resolve_minutes,

    CASE
        WHEN closed_at IS NOT NULL AND opened_at IS NOT NULL
        THEN (unix_timestamp(CAST(closed_at AS TIMESTAMP))
            - unix_timestamp(CAST(opened_at AS TIMESTAMP))) / 60.0
        ELSE NULL
    END                                                AS time_to_close_minutes,

    -- Priority / Severity -- already numeric here, unlike cta_servicenow_incident's
    -- "1 - Critical" STRING format (no SPLIT() needed)
    CAST(priority AS INT)                             AS priority,
    CAST(severity AS INT)                             AS severity,

    -- Categories
    category,
    subcategory,
    u_category,
    cause,

    -- Descriptions
    short_description,
    description,
    close_notes                                       AS resolution_notes,
    close_code,

    -- Device linkage (same join key S15 v1 already uses)
    u_wm_asset                                         AS wm_asset,
    cmdb_ci_sys_id,
    cmdb_ci_display_value,
    u_ncs_device_id                                    AS ncs_device_id,
    location_display_value                             AS location_name,
    assignment_group_display_value                     AS assignment_group,
    assigned_to_display_value                          AS assigned_to,
    caller_id_display_value                            AS caller,

    -- Event data -- already split into id/display_value by the ingestion's
    -- flatten_reference_columns() (per S25 header) -- no manual " - " parse
    -- needed here, unlike cta_servicenow_incident's raw u_event_code string.
    u_event_code_sys_id                                AS event_code_id,
    u_event_code_display_value                         AS event_code_name,
    CAST(u_event_cleared AS TIMESTAMP)                 AS event_cleared_dtm,

    -- Chargeable -- native BOOLEAN in servicenow_incident, cast to INT (not the
    -- LOWER(TRIM(...)) = 'true' string-comparison v1 used on cta_servicenow_incident's
    -- BOOLEAN column -- that pattern was fragile; a direct CAST is not). Kept
    -- numeric (0/1) rather than passed through as BOOLEAN because S24 and gold
    -- PS5's device_inc_lifetime both do SUM(is_chargeable)/SUM(is_major_incident)
    -- against this table -- Spark's SUM() requires a numeric input type, so a
    -- native BOOLEAN column breaks both at CreateTableAsSelect analysis time
    -- (confirmed live: DATATYPE_MISMATCH on S24 2026-07-20).
    CAST(u_chargeable AS INT)                          AS is_chargeable,
    u_chargeable_level_display_value                   AS chargeable_level,
    CAST(u_chargeable_override AS INT)                 AS is_chargeable_override,

    CAST(reopen_count AS INT)                          AS reopen_count,
    CAST(u_major_incident AS INT)                      AS is_major_incident,

    u_reason_code                                      AS reason_code,
    business_impact,
    contact_type,
    u_estimated_time                                   AS estimated_time_hours

FROM mars_dev.bronze.servicenow_incident
WHERE CAST(opened_at AS DATE) >= '2023-07-01'
QUALIFY ROW_NUMBER() OVER (PARTITION BY sys_id ORDER BY sys_updated_on DESC) = 1
)

-- -- FINAL SELECT -----------------------------------------------------------
SELECT
    i.incident_number,
    i.incident_state,
    i.maintenance_type,

    i.opened_dtm,
    i.resolved_dtm,
    i.closed_dtm,
    CAST(DATE(i.opened_dtm) AS DATE)                   AS incident_date,
    i.time_to_resolve_minutes,
    i.time_to_close_minutes,

    i.priority,
    i.severity,

    i.category,
    i.subcategory,
    i.u_category,
    i.cause,

    i.short_description,
    i.description,
    i.resolution_notes,
    i.close_code,

    i.wm_asset,
    i.cmdb_ci_sys_id,
    i.cmdb_ci_display_value,
    i.ncs_device_id,
    i.location_name,
    i.assignment_group,
    i.assigned_to,
    i.caller,

    i.event_code_id,
    i.event_code_name,
    i.event_cleared_dtm,

    i.is_chargeable,
    i.chargeable_level,
    i.is_chargeable_override,

    i.reopen_count,
    i.is_major_incident,
    i.reason_code,
    i.business_impact,
    i.contact_type,
    i.estimated_time_hours,

    -- No CMDB enrichment columns (serial_number, install_date, manufacturer,
    -- model_category) -- confirmed no viable source exists for this incident
    -- table; see header note "CMDB ENRICHMENT: CONFIRMED NOT AVAILABLE".

    dd.DEVICE_KEY,
    dd.mars_device_category,
    dd.FACILITY_NAME,
    dd.FACILITY_ID,
    dd.OPERATOR_ID,

    'CHICAGO' AS city_id

FROM inc i
LEFT JOIN mars_dev.silver.dim_device dd
    ON dd.DEVICE_ID = UPPER(TRIM(i.wm_asset))
   AND dd.is_current = TRUE
WHERE dd.mars_device_category IN ('TVM', 'GATE')
QUALIFY ROW_NUMBER() OVER (
    PARTITION BY i.incident_number
    ORDER BY i.resolved_dtm DESC NULLS LAST,
             i.closed_dtm  DESC NULLS LAST,
             i.opened_dtm  DESC NULLS LAST
) = 1;

-- Post-load optimisation:
-- OPTIMIZE mars_dev.silver.incident_history ZORDER BY (incident_date, wm_asset);

-- Suggested validation queries after building this table:
-- SELECT mars_device_category, COUNT(*), MIN(incident_date), MAX(incident_date),
--        SUM(CASE WHEN incident_date BETWEEN '2023-07-01' AND '2023-12-31' THEN 1 ELSE 0 END) AS h2_2023_rows
-- FROM mars_dev.silver.incident_history
-- GROUP BY mars_device_category;
