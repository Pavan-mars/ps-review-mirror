-- PS4 v2 -- ADDITIVE ONLY, rewritten 29-Jul against the REAL parquet schema
-- (confirmed live from S3, not guessed from notebook code). Supersedes the
-- earlier draft of this file, which assumed a weekly_device_summary /
-- weekly_alerts / weekly_timeline shape that does not exist on S3 today --
-- if you already ran that earlier version, those ps4v2_weekly_* tables are
-- harmless and unused; leave them, this file does not touch them.
--
-- Real shape: s3://.../chicago/ps4/clustering/<device_type>/asof=<date>/
--   assignments/      -- one row per device: DEVICE_KEY, DEVICE_ID, will_fail_3d,
--                         cluster_id, city_id, device_type, champion_pipeline,
--                         champion_run_id, champion_silhouette, asof_date, engine
--   cluster_summary/  -- one row per cluster: cluster_id, device_count,
--                         mean_will_fail_3d, city_id, device_type, asof_date,
--                         champion_pipeline
-- plus a manifest JSON per device_type/date with run_id, n_devices, n_clusters.
-- No anomaly_score, no week_start/week_end, no facility_id in what's actually
-- written today -- do not add columns for those; they don't exist yet.

CREATE TABLE IF NOT EXISTS ps4v2_clustering_assignments (
    city_id             TEXT             NOT NULL,
    device_type         TEXT             NOT NULL,
    device_id           TEXT             NOT NULL,
    device_key          NUMERIC,
    asof_date           DATE             NOT NULL,
    cluster_id          INTEGER          NOT NULL,
    will_fail_3d        DOUBLE PRECISION,
    champion_pipeline   TEXT,
    champion_run_id     TEXT,
    champion_silhouette DOUBLE PRECISION,
    engine               TEXT,
    manifest_run_id       TEXT,
    loaded_at             TIMESTAMPTZ    NOT NULL DEFAULT now(),
    PRIMARY KEY (city_id, device_type, device_id, asof_date)
);

CREATE TABLE IF NOT EXISTS ps4v2_clustering_summary (
    city_id            TEXT             NOT NULL,
    device_type        TEXT             NOT NULL,
    asof_date          DATE             NOT NULL,
    cluster_id         INTEGER          NOT NULL,
    device_count       BIGINT,
    mean_will_fail_3d  DOUBLE PRECISION,
    champion_pipeline  TEXT,
    manifest_run_id    TEXT,
    loaded_at          TIMESTAMPTZ      NOT NULL DEFAULT now(),
    PRIMARY KEY (city_id, device_type, asof_date, cluster_id)
);

-- promotion gate: assignments row count should equal manifest n_devices,
-- distinct cluster_id count in summary should equal manifest n_clusters.
-- The loader recomputes both independently and records here -- do not trust
-- the manifest's own counts without checking them against the actual rows.
CREATE TABLE IF NOT EXISTS ps4v2_load_audit (
    device_type          TEXT,
    asof_date             DATE,
    manifest_run_id        TEXT,
    n_devices_manifest      INTEGER,
    n_devices_loaded        INTEGER,
    n_clusters_manifest      INTEGER,
    n_clusters_loaded        INTEGER,
    champion_pipeline         TEXT,
    champion_silhouette       DOUBLE PRECISION,
    promoted                    BOOLEAN,
    notes                         TEXT,
    loaded_at                     TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (device_type, asof_date)
);
