-- =============================================================================
-- S15: silver.incident_history
-- S-code: S15  |  Build file: 17  |  Status: READY (CTA Export CSVs loaded to bronze 2026-06-24)
-- NOTE: Renumbered from S05 → S15 (2026-06-23). S05 = metric_hourly, S19 = maintenance_ledger,
--       S20 = usage_lifecycle_daily.
--
-- PURPOSE:
--   Full ServiceNow incident history for Chicago/CTA.
--   Replaces simplified silver.incident_root_cause (V1) which uses only
--   CTA.SERVICENOW_AVAILABILITY_EVENTS (295,960 rows, 57 cols).
--
-- SOURCES (4 CTA Export CSV files loaded to bronze 2026-06-24):
--   1. incident              — 7,028,048 rows, 44 columns
--   2. cmdb_ci               — 43,130 rows, 108 columns
--   3. cmdb_model            — 99,966 rows, 95 columns
--   4. cmdb_model_category   — 426 rows, 21 columns
--
-- Bronze catalog tables (CSV exports registered as Delta tables in mars_dev.bronze):
--   mars_dev.bronze.cta_servicenow_incident           (7,028,048 rows, 44 cols)
--   mars_dev.bronze.cta_servicenow_cmdb_ci            (43,130 rows, 108 cols)
--   mars_dev.bronze.cta_servicenow_cmdb_model         (99,966 rows, 95 cols)
--   mars_dev.bronze.cta_servicenow_cmdb_model_category (426 rows, 21 cols)
--
-- Bronze registration (run once in Databricks before executing this SQL):
--   CREATE TABLE mars_dev.bronze.cta_servicenow_incident
--     USING DELTA LOCATION 's3://cubic-mars-pm-s3-datalake-dev-bronze-170202974600/chicago_ventra/sn/incident/';
--   CREATE TABLE mars_dev.bronze.cta_servicenow_cmdb_ci
--     USING DELTA LOCATION 's3://cubic-mars-pm-s3-datalake-dev-bronze-170202974600/chicago_ventra/sn/cmdb_ci/';
--   CREATE TABLE mars_dev.bronze.cta_servicenow_cmdb_model
--     USING DELTA LOCATION 's3://cubic-mars-pm-s3-datalake-dev-bronze-170202974600/chicago_ventra/sn/cmdb_model/';
--   CREATE TABLE mars_dev.bronze.cta_servicenow_cmdb_model_category
--     USING DELTA LOCATION 's3://cubic-mars-pm-s3-datalake-dev-bronze-170202974600/chicago_ventra/sn/cmdb_model_category/';
--
-- JOIN LOGIC (no sys_id in any CSV export — all joins on display/name values):
--   incident.cmdb_ci          → cmdb_ci.name          (e.g. "SAG00901 TRNSTL ASY-TWA GDI UPGRD,CTA, SAG")
--   cmdb_ci.model_id          → cmdb_model.name        (e.g. "VENDOR ASSY-CTA OSFS")
--   cmdb_model.cmdb_model_category → cmdb_model_category.name  (e.g. "Gate/Turnstile")
--   incident.u_wm_asset       → dim_device.DEVICE_ID   (e.g. "SAG00901")
--
-- KEY COLUMN CORRECTIONS vs original design:
--   REMOVED (not in CSV): sys_id, type, urgency, impact, u_duration, u_ettr,
--     u_cause_category, u_cause_subcategory, u_event_duration, u_out_of_service,
--     u_outage_start_time, u_outage_end_time, u_downtime, u_resolution_method,
--     u_summary_of_cause, opened_by, u_customer, u_out_of_hours_callout,
--     u_safety_issue, u_vandalism, made_sla, escalate, major_incident_state,
--     u_potentially_revenue_impacting, u_kpi_impacting, u_kpi_level, cmdb_ci_sys_id
--   ADDED (in CSV, not in original design):
--     u_ncs_device_id, u_chargeable_level, u_reason_code, severity,
--     calendar_duration, description, reopen_count, u_major_incident
--   FIXED: SPLIT_PART (PostgreSQL) → SPLIT()[index] (Databricks)
--
-- PS IMPACT:
--   PS1 +12%: incident_frequency, calendar_duration (MTTR proxy), repeat_incident flags
--   PS3 +25%: category, subcategory, cause, u_chargeable_level, cmdb_model context
--   PS5 +20%: incident history per device via CMDB_CI serial_number + install_date
--
-- Michael R2 confirmation (2026-06-23):
--   is_chargeable = u_chargeable = 'true' (or failure_level > 0 from AVAILABILITY_EVENTS)
--   Planned vs real: category = 'Corrective Maintenance' = real breakdown
--   Event code format: "NNN - Description" in u_event_code field
-- =============================================================================

DROP TABLE IF EXISTS mars_dev.silver.incident_history;

CREATE TABLE mars_dev.silver.incident_history
USING DELTA
PARTITIONED BY (city_id)
AS
WITH

-- ── SOURCE 1: INCIDENT (7,028,048 rows) ──────────────────────────────────────
inc AS (
    SELECT
        -- Primary key (no sys_id in export — use incident number)
        number                                                      AS incident_number,
        incident_state,

        -- Maintenance type (Corrective Maintenance / Planned Maintenance)
        u_maintenance_type                                          AS maintenance_type,

        -- Timing
        CAST(opened_at    AS TIMESTAMP)                             AS opened_dtm,
        CAST(resolved_at  AS TIMESTAMP)                             AS resolved_dtm,
        CAST(closed_at    AS TIMESTAMP)                             AS closed_dtm,

        -- Duration: calendar_duration is in seconds
        CAST(calendar_duration AS BIGINT)                           AS duration_seconds,

        -- Derived: time to resolve in minutes (precise, not day-granular)
        CASE
            WHEN resolved_at IS NOT NULL AND opened_at IS NOT NULL
            THEN (unix_timestamp(CAST(resolved_at AS TIMESTAMP))
                  - unix_timestamp(CAST(opened_at  AS TIMESTAMP))) / 60.0
            ELSE NULL
        END                                                         AS time_to_resolve_minutes,

        CASE
            WHEN closed_at IS NOT NULL AND opened_at IS NOT NULL
            THEN (unix_timestamp(CAST(closed_at  AS TIMESTAMP))
                  - unix_timestamp(CAST(opened_at AS TIMESTAMP))) / 60.0
            ELSE NULL
        END                                                         AS time_to_close_minutes,

        -- Priority / Severity  (format: "1 - Critical", "3 - Low")
        -- SPLIT returns 0-indexed array in Databricks
        CAST(SPLIT(priority, ' - ')[0] AS INT)                      AS priority,
        SPLIT(priority, ' - ')[1]                                   AS priority_label,
        CAST(SPLIT(severity, ' - ')[0] AS INT)                      AS severity,
        SPLIT(severity, ' - ')[1]                                   AS severity_label,

        -- Categories (what type of incident)
        category                                                    AS category,
        subcategory                                                  AS subcategory,
        u_category                                                  AS u_category,

        -- Cause (free text from ServiceNow — what caused the incident)
        cause                                                       AS cause,

        -- Descriptions
        short_description,
        description,
        close_notes                                                 AS resolution_notes,
        close_code,

        -- Device linkage
        u_wm_asset                                                  AS wm_asset,       -- e.g. SAG00901
        cmdb_ci                                                     AS cmdb_ci_name,   -- join key to cmdb_ci.name
        u_ncs_device_id                                             AS ncs_device_id,  -- NCS device ID
        location                                                    AS location_name,
        assignment_group,
        assigned_to,
        caller_id                                                   AS caller,

        -- Event data (format: "220 - Missing CSC Keys")
        u_event_code                                                AS event_code_raw,
        CASE
            WHEN u_event_code IS NOT NULL AND INSTR(u_event_code, ' - ') > 0
            THEN CAST(TRIM(SPLIT(u_event_code, ' - ')[0]) AS INT)
        END                                                         AS event_code_id,
        CASE
            WHEN u_event_code IS NOT NULL AND INSTR(u_event_code, ' - ') > 0
            THEN TRIM(SPLIT(u_event_code, ' - ')[1])
            ELSE u_event_code
        END                                                         AS event_code_name,
        CAST(u_event_cleared AS TIMESTAMP)                          AS event_cleared_dtm,

        -- Chargeable (from R2: is_chargeable = failure_level > 0)
        CASE WHEN LOWER(TRIM(u_chargeable)) = 'true' THEN 1 ELSE 0 END  AS is_chargeable,
        u_chargeable_level                                          AS chargeable_level,
        CASE WHEN LOWER(TRIM(u_chargeable_override)) = 'true' THEN 1 ELSE 0 END AS is_chargeable_override,

        -- Incident quality
        CAST(reopen_count AS INT)                                   AS reopen_count,
        CASE WHEN LOWER(TRIM(u_major_incident)) = 'true' THEN 1 ELSE 0 END AS is_major_incident,

        -- Reason / additional classification
        u_reason_code                                               AS reason_code,
        business_impact,
        contact_type,
        u_estimated_time                                            AS estimated_time_hours

    FROM mars_dev.bronze.cta_servicenow_incident
    WHERE CAST(opened_at AS DATE) >= '2024-01-01'
),

-- ── SOURCE 2: CMDB_CI (43,130 rows — one row per CTA device/component) ───────
-- Join key: cmdb_ci.name = incident.cmdb_ci (display value)
-- Key columns for PS5: serial_number, install_date, operational_status
ci AS (
    SELECT
        name                                                        AS ci_name,        -- join key
        asset_tag                                                   AS ci_asset_tag,
        model_id                                                    AS ci_model_id,    -- join key to cmdb_model.name
        operational_status                                          AS ci_operational_status,
        install_status                                              AS ci_install_status,
        CAST(install_date AS DATE)                                  AS ci_install_date,
        location                                                    AS ci_location,
        serial_number                                               AS ci_serial_number,   -- PS5: physical serial
        u_ncs_device_id                                             AS ci_ncs_device_id,
        u_ncs_device_name                                           AS ci_ncs_device_name,
        u_component_id                                              AS ci_component_id,
        u_component_position                                        AS ci_component_position,
        manufacturer                                                AS ci_manufacturer,
        CAST(warranty_expiration AS DATE)                           AS ci_warranty_expiration,
        sys_class_name                                              AS ci_class_name,
        CAST(fault_count AS INT)                                    AS ci_fault_count
    FROM mars_dev.bronze.cta_servicenow_cmdb_ci
),

-- ── SOURCE 3: CMDB_MODEL (99,966 rows — model catalog) ───────────────────────
-- Join key: cmdb_model.name = cmdb_ci.model_id (display value)
mdl AS (
    SELECT
        name                                                        AS model_name,     -- join key
        cmdb_model_category                                         AS model_category, -- join key to cmdb_model_category.name
        u_model_type                                                AS model_type,
        u_life_expectancy                                           AS model_life_expectancy,
        CASE WHEN LOWER(TRIM(u_repairable)) = 'true' THEN 1 ELSE 0 END AS model_is_repairable,
        CASE WHEN LOWER(TRIM(u_rotable))    = 'true' THEN 1 ELSE 0 END AS model_is_rotable,
        status                                                      AS model_status,
        sys_class_name                                              AS model_class_name
    FROM mars_dev.bronze.cta_servicenow_cmdb_model
),

-- ── SOURCE 4: CMDB_MODEL_CATEGORY (426 rows — category hierarchy) ────────────
-- Join key: cmdb_model_category.name = cmdb_model.cmdb_model_category
-- Note: source column 'parent_cateogry' has a typo (missing 'e') — use as-is
mcat AS (
    SELECT
        name                                                        AS model_category_name, -- join key
        parent_cateogry                                             AS model_parent_category,
        cmdb_ci_class                                               AS cmdb_ci_class,
        asset_class                                                 AS asset_class
    FROM mars_dev.bronze.cta_servicenow_cmdb_model_category
)

-- ── FINAL SELECT ─────────────────────────────────────────────────────────────
SELECT
    -- ── Incident core ─────────────────────────────────────────────────────
    i.incident_number,
    i.incident_state,
    i.maintenance_type,

    -- Timing
    i.opened_dtm,
    i.resolved_dtm,
    i.closed_dtm,
    CAST(DATE(i.opened_dtm) AS DATE)                                AS incident_date,
    i.duration_seconds,
    i.time_to_resolve_minutes,
    i.time_to_close_minutes,

    -- Priority / Severity
    i.priority,
    i.priority_label,
    i.severity,
    i.severity_label,

    -- Categories
    i.category,
    i.subcategory,
    i.u_category,
    i.cause,

    -- Description
    i.short_description,
    i.description,
    i.resolution_notes,
    i.close_code,

    -- Device linkage
    i.wm_asset,
    i.cmdb_ci_name,
    i.ncs_device_id,
    i.location_name,
    i.assignment_group,
    i.assigned_to,
    i.caller,

    -- Event code
    i.event_code_raw,
    i.event_code_id,
    i.event_code_name,
    i.event_cleared_dtm,

    -- Chargeable / billing
    i.is_chargeable,
    i.chargeable_level,
    i.is_chargeable_override,

    -- Incident quality
    i.reopen_count,
    i.is_major_incident,
    i.reason_code,
    i.business_impact,
    i.contact_type,
    i.estimated_time_hours,

    -- ── CMDB_CI enrichment ────────────────────────────────────────────────
    ci.ci_asset_tag,
    ci.ci_operational_status,
    ci.ci_install_status,
    ci.ci_install_date,
    ci.ci_serial_number,         -- PS5: physical serial number
    ci.ci_ncs_device_id,
    ci.ci_ncs_device_name,
    ci.ci_component_id,
    ci.ci_component_position,
    ci.ci_manufacturer,
    ci.ci_warranty_expiration,
    ci.ci_class_name,
    ci.ci_fault_count,

    -- ── CMDB_MODEL enrichment ─────────────────────────────────────────────
    mdl.model_name              AS cmdb_model_name,
    mdl.model_type              AS cmdb_model_type,
    mdl.model_life_expectancy   AS cmdb_model_life_expectancy,
    mdl.model_is_repairable     AS cmdb_model_is_repairable,
    mdl.model_is_rotable        AS cmdb_model_is_rotable,

    -- ── CMDB_MODEL_CATEGORY enrichment ───────────────────────────────────
    mcat.model_category_name    AS cmdb_model_category,
    mcat.model_parent_category  AS cmdb_model_parent_category,

    -- ── dim_device enrichment (join on u_wm_asset = DEVICE_ID) ───────────
    dd.DEVICE_KEY,
    dd.mars_device_category,
    dd.FACILITY_NAME,
    dd.FACILITY_ID,
    dd.OPERATOR_ID,

    -- Partition
    'CHICAGO'                                                       AS city_id

FROM inc i

-- CMDB_CI: join via incident.cmdb_ci display value = cmdb_ci.name
LEFT JOIN ci
    ON i.cmdb_ci_name = ci.ci_name

-- CMDB_MODEL: via CI model_id display value = model name
LEFT JOIN mdl
    ON ci.ci_model_id = mdl.model_name

-- CMDB_MODEL_CATEGORY: via model category name
LEFT JOIN mcat
    ON mdl.model_category = mcat.model_category_name

-- dim_device: wm_asset (SAG00901) = DEVICE_ID
LEFT JOIN mars_dev.silver.dim_device dd
    ON dd.DEVICE_ID = UPPER(TRIM(i.wm_asset))
    AND dd.is_current = TRUE;

-- Post-load optimisation:
-- OPTIMIZE mars_dev.silver.incident_history ZORDER BY (incident_date, wm_asset);
