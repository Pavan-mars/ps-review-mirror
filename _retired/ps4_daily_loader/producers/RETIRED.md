# PS4 daily-path producers — retired 26-Sep-2026

These notebooks wrote the daily/v1-v2 PS4 outputs (`chicago/ps4/scored/...`, cluster exports, device-daily
export) that only `cubic-mars-ps4-rds-loader` consumed. That loader was deleted 13-Sep (see
`tooling/retire_ps4_daily_loader.sh`); its Aurora tables are dropped by
`api/lambda/cubic-mars-dashboard-api/sql/70_ps4_legacy_drop.sql`. The PS4 product is
`notebooks/ps4_weekly_v3/` → `cubic-mars-ps4-v3-loader`. They READ `gold.device_ps4_hourly` but do not build it
(that is `notebooks/run_layer_gold.py`), so retiring them does not affect PS1. The anomaly-detection notebook
also created a SageMaker Feature Store group — check whether it still exists (cost).
