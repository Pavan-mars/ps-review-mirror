-- =============================================================================
-- S17: silver.incident_history  (DESIGN ONLY — PENDING bronze load of 4 SN tables)
-- S-code: S17  |  Build file: 17  |  Status: PENDING (awaiting Robin — 4 SN tables)
-- NOTE: Renumbered from S14 → S17 (2026-06-23). S14 = metric_hourly (14_metric_hourly__create.sql)
--       S15 = maintenance_ledger, S16 = usage_lifecycle_daily.
--
-- PURPOSE:
--   Full ServiceNow incident history for Chicago/CTA, Jan 2024 – May 2026.
--   Replaces the simplified silver.incident_root_cause (V1) which uses only
--   CTA.SERVICENOW_AVAILABILITY_EVENTS (295,960 rows, 57 cols).
--
-- SOURCES (all 4 tables to be loaded to bronze by Robin):
--   1. INCIDENT          — primary incident records (2,632 rows / 13 days in sample → ~172K full)
--   2. CMDB_CI           — configuration item master (one row per CTA device)
--   3. CMDB_MODEL        — model catalog (VENDOR ASSY-CTA OSFS, TRNSTL ASY-TWA GDI UPGRD etc.)
--   4. CMDB_MODEL_CATEGORY — model category hierarchy (TVM, Gate/Turnstile, etc.)
--
-- Expected bronze paths (confirm with Robin):
--   parquet.`s3://cubic-mars-pm-s3-datalake-dev-bronze-170202974600/chicago_ventra/sn/incident/`
--   parquet.`s3://cubic-mars-pm-s3-datalake-dev-bronze-170202974600/chicago_ventra/sn/cmdb_ci/`
--   parquet.`s3://cubic-mars-pm-s3-datalake-dev-bronze-170202974600/chicago_ventra/sn/cmdb_model/`
--   parquet.`s3://cubic-mars-pm-s3-datalake-dev-bronze-170202974600/chicago_ventra/sn/cmdb_model_category/`
--
-- SAMPLE ANALYSIS (CTA Sample Incident.xls — Jan 19-31, 2024):
--   Rows      : 2,632 incidents (all Closed in 13-day window)
--   Columns   : 190 in XLS export → 53 selected for silver
--   Devices   : WM Asset = TVM/SAG/BMV/BTP prefix format (e.g. TVM12102)
--               Joins to silver.dim_device.DEVICE_ID directly — NO CTA\d{5} format
--   Event codes: 56 unique in 13 days; "NNN - Name" format → parsed to code_id + code_name
--   OOS rate  : 4.9% of incidents have Out of Service = 1 (129/2,632)
--   Priority  : 75% Low (4), 12% High (2), 11% Critical (1), 2% Moderate (3)
--
-- COLUMNS NULL IN 13-DAY SAMPLE (may be populated in full Jan'24–May'26 export):
--   Cause Category, Cause Subcategory  — filled post-resolution by analysts
--   Resolution method                  — operational field, filled at close
--   Outage Start/End Time              — only P1/P2 incidents with formal outage
--   NCS Device ID                      — replaced by WM Asset as device identifier
--   KPI Level, KPI Adjustment          — KPI-impacting incidents only
--   Summary of Cause                   — management escalation incidents only
--
-- CMDB TABLE DESIGN (from Configuration Item column in sample):
--   CMDB_CI:
--     - One row per CTA device (TVM12102, SAG11501, BMVxxxxx etc.)
--     - sys_id (PK), name (= WM Asset = DEVICE_ID), model_id (FK to CMDB_MODEL)
--     - operational_status, location, asset_tag
--   CMDB_MODEL:
--     - One row per model type (VENDOR ASSY-CTA OSFS, TRNSTL ASY-TWA GDI UPGRD etc.)
--     - sys_id (PK), name, category_id (FK to CMDB_MODEL_CATEGORY)
--   CMDB_MODEL_CATEGORY:
--     - Hierarchy: TVM, Gate/Turnstile (SAG), Bus Validator (BMV/BTP), Reader (ABP), etc.
--
-- PS IMPACT AFTER INCORPORATING 4 TABLES:
--   PS1 +12%: incident_frequency, mean_time_to_repair, repeat_incident flags
--   PS3 +25%: cause_category, cause_subcategory, resolution_method, cmdb_model context
--   PS5 +20%: incident_history per component (via CMDB_CI), outage duration per device
--
-- COLUMN NAMES:
--   Standard ServiceNow fields use SN API names (sys_id, number, opened_at etc.)
--   Custom CTA fields use u_ prefix (u_wm_asset, u_event_code, u_out_of_service etc.)
--   CONFIRM EXACT NAMES against actual bronze schema once loaded.
--
-- NOTE: Until INCIDENT+CMDB tables are loaded to bronze, use silver.incident_root_cause (V1).
-- =============================================================================

CREATE OR REPLACE TABLE mars_dev.silver.incident_history
USING DELTA
PARTITIONED BY (city_id)
AS
WITH

-- ── SOURCE: INCIDENT (primary table, ~172K rows Jan 2024 – May 2026) ────────
inc AS (
    SELECT
        sys_id                                                  AS incident_sys_id,
        number                                                  AS incident_number,
        incident_state,
        type                                                    AS incident_type,
        u_maintenance_type                                      AS maintenance_type,

        -- Timing
        CAST(opened_at    AS TIMESTAMP)                         AS opened_dtm,
        CAST(resolved_at  AS TIMESTAMP)                         AS resolved_dtm,
        CAST(closed_at    AS TIMESTAMP)                         AS closed_dtm,
        CAST(u_duration   AS BIGINT)                            AS duration_seconds,
        CAST(u_ettr       AS DOUBLE)                            AS ettr_minutes,

        -- Resolution timing metrics (derived)
        -- DATEDIFF returns whole days in Databricks — multiplying by 1440 gives day-granular
        -- minutes (e.g. 1.5-day incident → 1440 min, not 2160). Use unix_timestamp for precision.
        CASE
            WHEN resolved_at IS NOT NULL AND opened_at IS NOT NULL
            THEN (unix_timestamp(CAST(resolved_at AS TIMESTAMP))
                  - unix_timestamp(CAST(opened_at   AS TIMESTAMP))) / 60.0
            ELSE NULL
        END                                                     AS time_to_resolve_minutes,
        CASE
            WHEN closed_at IS NOT NULL AND opened_at IS NOT NULL
            THEN (unix_timestamp(CAST(closed_at   AS TIMESTAMP))
                  - unix_timestamp(CAST(opened_at   AS TIMESTAMP))) / 60.0
            ELSE NULL
        END                                                     AS time_to_close_minutes,

        -- Priority / Severity
        -- SPLIT_PART is PostgreSQL-only; Databricks uses SPLIT() which returns 0-indexed array
        CAST(SPLIT(priority, ' - ')[0] AS INT)                  AS priority,
        CAST(SPLIT(urgency,  ' - ')[0] AS INT)                  AS urgency,
        CAST(SPLIT(impact,   ' - ')[0] AS INT)                  AS impact,

        -- Categories
        category                                                AS category,
        subcategory                                             AS subcategory,
        u_cause_category                                        AS cause_category,
        u_cause_subcategory                                     AS cause_subcategory,

        -- Descriptions
        short_description,
        close_notes                                             AS resolution_notes,
        close_code,

        -- Device / CI linkage
        u_wm_asset                                              AS wm_asset,
        cmdb_ci                                                 AS configuration_item_name,
        location                                                AS location_name,
        assignment_group,
        assigned_to,

        -- Event data (XLS label: "Event Code"; format = "NNN - Description")
        u_event_code                                            AS event_code_raw,
        CASE
            WHEN u_event_code IS NOT NULL AND INSTR(u_event_code, ' - ') > 0
            THEN TRIM(SPLIT_PART(u_event_code, ' - ', 1))
        END                                                     AS event_code_id,
        CASE
            WHEN u_event_code IS NOT NULL AND INSTR(u_event_code, ' - ') > 0
            THEN TRIM(SPLIT_PART(u_event_code, ' - ', 2))
            ELSE u_event_code
        END                                                     AS event_code_name,
        CAST(u_event_duration AS BIGINT)                        AS event_duration_seconds,
        CAST(u_event_cleared  AS TIMESTAMP)                     AS event_cleared_dtm,

        -- OOS / Outage (Outage Start/End null in 13-day sample; populated for P1/P2)
        CAST(u_out_of_service     AS INT)                       AS is_oos,
        CAST(u_outage_start_time  AS TIMESTAMP)                 AS outage_start_dtm,
        CAST(u_outage_end_time    AS TIMESTAMP)                 AS outage_end_dtm,
        CAST(u_downtime           AS BIGINT)                    AS downtime_seconds,

        -- Resolution (null in 13-day sample; filled by analysts at resolution)
        u_resolution_method                                     AS resolution_method,
        u_summary_of_cause                                      AS summary_of_cause,

        -- People
        opened_by,
        u_customer                                              AS customer,
        caller_id                                               AS caller,

        -- Flags
        CAST(u_chargeable                      AS INT)          AS is_chargeable,
        CAST(u_out_of_hours_callout            AS INT)          AS is_out_of_hours,
        CAST(u_safety_issue                    AS INT)          AS is_safety_issue,
        CAST(u_vandalism                       AS INT)          AS is_vandalism,
        CAST(made_sla                          AS INT)          AS made_sla,
        CAST(escalate                          AS INT)          AS is_escalated,
        CAST(major_incident_state              AS INT)          AS is_major_incident,
        CAST(u_potentially_revenue_impacting   AS INT)          AS is_potentially_revenue_impacting,

        -- KPI (null in sample; populated for KPI-impacting incidents)
        u_kpi_impacting                                         AS kpi_impacting,
        u_kpi_level                                             AS kpi_level,

        -- CMDB CI foreign key (join to CMDB_CI table)
        cmdb_ci_sys_id

    FROM parquet.`s3://cubic-mars-pm-s3-datalake-dev-bronze-170202974600/chicago_ventra/sn/incident/`
    WHERE CAST(opened_at AS DATE) >= '2024-01-01'
      AND CAST(opened_at AS DATE) <  '2026-06-01'
),

-- ── CMDB_CI: one row per CTA device ─────────────────────────────────────────
-- WM Asset = CMDB_CI.name = silver.dim_device.DEVICE_ID (TVM12102, SAG11501 etc.)
ci AS (
    SELECT
        sys_id          AS ci_sys_id,
        name            AS ci_asset_tag,
        model_id        AS ci_model_sys_id,
        operational_status AS ci_operational_status,
        location        AS ci_location
    FROM parquet.`s3://cubic-mars-pm-s3-datalake-dev-bronze-170202974600/chicago_ventra/sn/cmdb_ci/`
),

-- ── CMDB_MODEL: model catalog ─────────────────────────────────────────────────
-- Examples: "VENDOR ASSY-CTA OSFS" (TVM), "TRNSTL ASY-TWA GDI UPGRD,CTA, SAG" (Gate)
mdl AS (
    SELECT
        sys_id          AS model_sys_id,
        name            AS model_name,
        category_id     AS model_category_sys_id
    FROM parquet.`s3://cubic-mars-pm-s3-datalake-dev-bronze-170202974600/chicago_ventra/sn/cmdb_model/`
),

-- ── CMDB_MODEL_CATEGORY: category hierarchy ──────────────────────────────────
mcat AS (
    SELECT
        sys_id          AS category_sys_id,
        name            AS model_category_name
    FROM parquet.`s3://cubic-mars-pm-s3-datalake-dev-bronze-170202974600/chicago_ventra/sn/cmdb_model_category/`
)

-- ── FINAL JOIN ───────────────────────────────────────────────────────────────
SELECT
    -- INCIDENT core
    i.incident_sys_id,
    i.incident_number,
    i.incident_state,
    i.incident_type,
    i.maintenance_type,

    -- Timing
    i.opened_dtm,
    i.resolved_dtm,
    i.closed_dtm,
    CAST(DATE(i.opened_dtm) AS DATE)            AS incident_date,
    i.duration_seconds,
    i.ettr_minutes,
    i.time_to_resolve_minutes,
    i.time_to_close_minutes,

    -- Priority
    i.priority,
    i.urgency,
    i.impact,

    -- Categories
    i.category,
    i.subcategory,
    i.cause_category,
    i.cause_subcategory,

    -- Description
    i.short_description,
    i.resolution_notes,
    i.close_code,

    -- Device / CI
    i.wm_asset,
    i.configuration_item_name,
    i.location_name,
    i.assignment_group,
    i.assigned_to,
    ci.ci_operational_status,

    -- Event
    i.event_code_raw,
    i.event_code_id,
    i.event_code_name,
    i.event_duration_seconds,
    i.event_cleared_dtm,

    -- OOS / Outage
    i.is_oos,
    i.outage_start_dtm,
    i.outage_end_dtm,
    i.downtime_seconds,

    -- Resolution
    i.resolution_method,
    i.summary_of_cause,

    -- People
    i.opened_by,
    i.customer,
    i.caller,

    -- Flags
    i.is_chargeable,
    i.is_out_of_hours,
    i.is_safety_issue,
    i.is_vandalism,
    i.made_sla,
    i.is_escalated,
    i.is_major_incident,
    i.is_potentially_revenue_impacting,

    -- KPI
    i.kpi_impacting,
    i.kpi_level,

    -- CMDB enrichment
    ci.ci_sys_id,
    ci.ci_asset_tag,
    mdl.model_name                              AS cmdb_model_name,
    mcat.model_category_name                    AS cmdb_model_category,

    -- dim_device enrichment (join on wm_asset = DEVICE_ID)
    dd.DEVICE_KEY,
    dd.mars_device_category,
    dd.FACILITY_NAME,
    dd.FACILITY_ID,
    dd.OPERATOR_ID,

    -- Partition
    'CHICAGO'                                   AS city_id

FROM inc i

-- CMDB_CI: join via cmdb_ci_sys_id stored on the incident
LEFT JOIN ci
    ON i.cmdb_ci_sys_id = ci.ci_sys_id

-- CMDB_MODEL: via CI model FK
LEFT JOIN mdl
    ON ci.ci_model_sys_id = mdl.model_sys_id

-- CMDB_MODEL_CATEGORY: via model category FK
LEFT JOIN mcat
    ON mdl.model_category_sys_id = mcat.category_sys_id

-- dim_device: WM Asset = device ID (TVM12102 etc.)
LEFT JOIN mars_dev.silver.dim_device dd
    ON dd.DEVICE_ID = UPPER(TRIM(i.wm_asset))
    AND dd.is_current = TRUE;

-- Post-load:
-- OPTIMIZE mars_dev.silver.incident_history ZORDER BY (incident_date, wm_asset);
