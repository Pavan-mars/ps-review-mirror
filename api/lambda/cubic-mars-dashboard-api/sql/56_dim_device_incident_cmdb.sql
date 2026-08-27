-- 27-Aug-2026. dim_device_incident_cmdb : device -> incident -> CMDB CI map
-- (the July one-time "device_incidents" CSV, now pipeline-owned). Fed daily by
-- the dim_incident_cmdb_daily bundle job -> S3 -> cubic-mars-dim-incident-loader,
-- which embeds this same idempotent DDL; this file exists so a cold rebuild
-- recreates the table without the loader (no live-only objects).
-- Apply via:  {"action":"apply_sql","file":"56_dim_device_incident_cmdb.sql"}
-- (dry_run first). NOT added to the migrate() tuple -- that list is frozen at
-- sql/48 by design. Purely additive; idempotent.
CREATE TABLE IF NOT EXISTS dim_device_incident_cmdb (
    city_id               text        NOT NULL DEFAULT 'CHI',
    device_id             text        NOT NULL,
    device_key            text,
    device_name           text,
    bus_id                text,
    bus_device_flag       boolean,
    serial_number         text,
    device_serial_number  text,
    component_serial_nbr  text,
    component_type        text,
    facility_id           text,
    facility_name         text,
    operator_id           text,
    operator_name         text,
    mars_device_category  text,
    device_type_name      text,
    transit_mode_name     text,
    incident_number       text,          -- latest incident (by opened_at)
    incident_sys_id       text,
    cmdb_ci_sys_id        text,          -- primary CI (latest incident's CI, else first mapped CI)
    opened_at             timestamptz,   -- latest incident opened
    closed_at             timestamptz,
    all_incident_numbers  text,          -- comma-joined rollups (matches the CSV shape)
    all_incident_sys_ids  text,
    all_cmdb_ci_sys_ids   text,
    incident_count        bigint      NOT NULL DEFAULT 0,
    as_of_date            date        NOT NULL,
    run_id                text,
    loaded_at             timestamptz DEFAULT now(),
    PRIMARY KEY (city_id, device_id, as_of_date)
);

CREATE INDEX IF NOT EXISTS ix_ddic_cmdb_ci  ON dim_device_incident_cmdb (cmdb_ci_sys_id);
CREATE INDEX IF NOT EXISTS ix_ddic_category ON dim_device_incident_cmdb (city_id, mars_device_category, as_of_date);
