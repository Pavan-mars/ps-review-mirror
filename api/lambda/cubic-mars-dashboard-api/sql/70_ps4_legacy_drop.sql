-- 70_ps4_legacy_drop.sql -- PS4 daily/v1-v2 generation retirement. DROP. Irreversible.   26-Sep-2026
--
-- WHAT THESE WERE. The daily PS4 path (sql/20, 25, 33) fed by cubic-mars-ps4-rds-loader, which
-- never completed a run after 28-Jul and was deleted 13-Sep (tooling/retire_ps4_daily_loader.sh),
-- plus ps4_anomaly_alerts (sql/01), which was never wired to scoring. The PS4 product is the weekly
-- v3 path: cubic-mars-ps4-v3-loader -> ps4_weekly_* / ps4_cluster_* -> /ps4/* -> V4PS4Overview.
--
-- HOW THAT WAS ESTABLISHED
--   Code: no reference outside comments in any live loader, handler route, view or dashboard file.
--         The last readers were removed in the same commit: /ps4/alerts (+ PATCH) and
--         /ps4/weekly-device (no dashboard caller), and v_ps4_cluster_latest, repointed to the
--         weekly summary by sql/69 -- APPLY 69 FIRST, or the ps4_cluster_assignments drop fails.
--   DB:   run {"action":"depends","tables":[...]} on the table names below BEFORE this file.
--         No CASCADE: an unexpected dependent makes this fail instead of silently widening it.
-- The CREATE blocks are gone from the repo (sql/20, 25, 33 deleted; 01 edited), so a migrate
-- replay cannot recreate them. gold.device_ps4_hourly (lakehouse, feeds PS1) is NOT touched.
-- views dependents-first (v_ps4_cluster_profile -> v_ps4_device_anomaly -> v_ps4_anomalies)
DROP VIEW  IF EXISTS v_ps4_cluster_profile;
DROP VIEW  IF EXISTS v_ps4_device_anomaly;
DROP VIEW  IF EXISTS v_ps4_anomalies;
DROP VIEW  IF EXISTS v_ps4_timeline;
DROP VIEW  IF EXISTS v_ps4_latest_asof;
DROP VIEW  IF EXISTS v_ps4_day_anomalies;
DROP VIEW  IF EXISTS v_ps4_outlier_scores;
DROP VIEW  IF EXISTS v_ps4_device_status;
DROP VIEW  IF EXISTS v_ps4_leaderboard;
DROP VIEW  IF EXISTS v_ps4_latest_run;
DROP TABLE IF EXISTS ps4_anomalies;
DROP TABLE IF EXISTS ps4_anomaly_timeline;
DROP TABLE IF EXISTS ps4_cluster_assignments;
DROP TABLE IF EXISTS ps4_cluster_summary;
DROP TABLE IF EXISTS ps4_outlier_scores;
DROP TABLE IF EXISTS ps4_device_day;
DROP TABLE IF EXISTS ps4_device_lifetime;
DROP TABLE IF EXISTS ps4_device_daily;
DROP TABLE IF EXISTS ps4_device_hourly;
DROP TABLE IF EXISTS ps4_leakage_scan;
DROP TABLE IF EXISTS ps4_model_leaderboard;
DROP TABLE IF EXISTS ps4_runs;
DROP TABLE IF EXISTS ps4_signal_summary;
DROP TABLE IF EXISTS ps4_spc_thresholds;
DROP TABLE IF EXISTS ps4_station_anomaly;
DROP TABLE IF EXISTS ps4_anomaly_alerts;
DROP TYPE  IF EXISTS ps4_alert_status;
