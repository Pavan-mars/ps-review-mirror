-- 69_ps4_device360_v3.sql -- Device-360 reads the SAME PS4 run the PS4 tab shows.   26-Sep-2026
--
-- 1. v_ps4_cluster_latest read ps4_cluster_assignments, which only the retired daily loader
--    (cubic-mars-ps4-rds-loader, last run 28-Jul, deleted 13-Sep) ever wrote. So every device's
--    360 page showed a frozen v2 cluster id, while the PS4 tab shows the weekly v3 clusters.
--    It now takes dominant_cluster_id from the current v3 weekly summary. Column names and types
--    are unchanged, so v_device_360 needs no change. There is no per-device confidence in v3.
-- 2. v_ps4_device_latest took the latest week across ALL pipeline_versions (part of the key), so
--    two versions loading the same week made the pick arbitrary. Both views now read only the
--    current version, exactly as the /ps4/weekly-* views do (v_ps4_v3_current).
CREATE OR REPLACE VIEW v_ps4_device_latest AS
SELECT DISTINCT ON (w.city_id, w.device_id)
       w.city_id::text AS city_id, w.device_id,
       w.week_start          AS ps4_week_start,
       w.anomaly_score_max   AS ps4_anomaly_score_max,
       w.anomaly_score_p95   AS ps4_anomaly_score_p95,
       w.severity            AS ps4_severity,
       w.is_actionable_week  AS ps4_is_actionable_week,
       w.anomaly_types       AS ps4_anomaly_types,
       w.dominant_cluster_id AS ps4_dominant_cluster_id
FROM ps4_weekly_device_summary w
JOIN v_ps4_v3_current cur
  ON cur.city_id = w.city_id AND cur.pipeline_version = w.pipeline_version
ORDER BY w.city_id, w.device_id, w.week_start DESC;

CREATE OR REPLACE VIEW v_ps4_cluster_latest AS
SELECT DISTINCT ON (w.city_id, w.device_id)
       w.city_id::text              AS city_id,
       w.device_id::varchar(40)     AS device_id,
       w.dominant_cluster_id        AS ps4_cluster_id,
       NULL::numeric(10,6)          AS ps4_cluster_confidence,
       'weekly_v3'::varchar(16)     AS ps4_cluster_engine,
       w.week_start                 AS ps4_cluster_asof
FROM ps4_weekly_device_summary w
JOIN v_ps4_v3_current cur
  ON cur.city_id = w.city_id AND cur.pipeline_version = w.pipeline_version
WHERE w.dominant_cluster_id IS NOT NULL
ORDER BY w.city_id, w.device_id, w.week_start DESC;
