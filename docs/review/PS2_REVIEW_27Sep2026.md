# PS2 (Cascading Failure Patterns) -- stale-element retirement, 27-Sep-2026

Chain: `notebooks/ps2_cascading_failure/PS2_Failure_Patterns_v2_5_4_Union_Minutes.ipynb` + `PS2_Serial_Grain_Analysis_v1_FIXED.ipynb`
-> `s3://<artifacts>/chicago/ps2_outputs/<table>/computed_date=<as-of>/run_id=...` (+ `_runs/.../run_complete.json`)
-> `cubic-mars-ps2-rds-loader` (schedule 13:30 UTC, newest computed_date, one vintage per load) -> Aurora ps2_*
-> `cubic-mars-dashboard-api` (26 `/ps2/*` routes) -> `V4PS2Overview.jsx`; Device-360 via `v_ps2_device_latest`, `v_ps2_device_cascade`.

Already clean before today (sql/60-63, 23-Sep): legacy routes, legacy tables and orphans. Every one of the 26 routes the
API serves is called by the dashboard; 29 of 30 ps2 tables are read.

| Retired today | Why | Change |
|---|---|---|
| sql/02, sql/08 seed backfills | hardcoded 11/14-Jul rows re-inserted by `migrate` on every deploy | out of the migrate list, moved to `_retired/ps2_seed_backfills`; sql/75 purges their rows where a newer vintage exists |
| `sql/10_ps2_run_backfill_2.sql` in the migrate list | file does not exist | entry removed |
| `ps2_business_impact` | loaded via an alias and seeded, read by nothing | loader alias + column renames removed (family now reports `no_target`); sql/75 drops the table |
| `cubic-mars-ps2-rds-push` source | retired 19-Aug (tracker #80), source kept beside live code | moved to `_retired/ps2_rds_push`; **[AWS]** delete the function + role if still deployed |

Kept on purpose: `v_ps2_ignition_termination_subsystem` (documented psql convenience view), `ps2_v254_union_verify_cell.py`
(paste-in diagnostic, kept by the 16-Aug audit), `v_ps2_device_latest` (feeds `v_device_360`).

Deploy order: API (migrate list + sql/75) -> `depends` on `ps2_business_impact` -> `apply_sql 75_ps2_retire.sql` -> loader.
