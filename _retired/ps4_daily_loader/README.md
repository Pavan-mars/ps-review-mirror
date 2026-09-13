# Retired 13-Sep-2026: the DAILY PS4 loader path

Decision (PK, 13-Sep-2026): PS4 runs WEEKLY. A daily run is not feasible for the clustering workload,
and the weekly path (`notebooks/ps4_weekly_v3/…` → `chicago/ps4/v3/runs/run_id=…` + `READY.json` →
`cubic-mars-ps4-v3-loader`, Monday 08:00 UTC) is the one every `/ps4/*` route reads.

The daily loader `cubic-mars-ps4-rds-loader` never completed a run after its 28-Jul rewrite (900-second
timeout, one cron firing plus two async retries, every day) and no dashboard route ever read its five
tables (`ps4_anomaly_timeline`, `ps4_cluster_assignments`, `ps4_cluster_summary`, `ps4_device_day`,
`ps4_device_lifetime`). Its rule was disabled 13-Sep 13:50 UTC.

Kept here: the loader as it was, plus `handler_copy_idempotent_unused.py` — a working fix (COPY-based
bulk load, already-loaded skip, per-feed logging) that was never deployed because the path was retired
instead. AWS removal: `tooling/retire_ps4_daily_loader.sh` (captures, then deletes the rule, the
artifacts-bucket manifest notification entry, the function and its role). The five Aurora tables are left
in place; dropping them follows the R1 order (remove their CREATE from `sql/25` and `sql/33`, adjust
`migrate()`, deploy, then DROP).

Producers that now have no consumer: the daily anomaly export to `chicago/ps4/scored/asof=…` and the
clustering export to `chicago/ps4/clustering/…` (notebooks under `ps4_anomaly_detection/` and
`ps4_fault_clustering/`). They stay until the weekly v3 job is ratified as the sole PS4 producer.
