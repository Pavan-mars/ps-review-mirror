# PS2 (Cascading Failure Patterns) -- health check and stale-element retirement, 27-Sep-2026

Chain: `PS2_Failure_Patterns_v2_5_4_Union_Minutes.ipynb` + `PS2_Serial_Grain_Analysis_v1_FIXED.ipynb`
-> `s3://<artifacts>/chicago/ps2_outputs/<table>/computed_date=<as-of>/run_id=...` (+ `_runs/as_of_date=.../run_complete.json`)
-> `cubic-mars-ps2-rds-loader` (EventBridge `cubic-mars-ps2-daily-load`, cron 07:10 UTC, newest computed_date, one vintage)
-> Aurora ps2_* -> `cubic-mars-dashboard-api` (26 `/ps2/*` routes) -> `V4PS2Overview.jsx`, global search (`/ps2/devices`),
Device-360 (`v_ps2_device_latest`, `v_ps2_device_cascade`, `ps2_device_catalog`, `ps2_device_cascades`).

## Health (live, 27-Sep)
- S3: newest run `_runs/as_of_date=2026-08-29`.
- Loader: committed daily 24-27 Sep 07:17 UTC -- 47 tables, 0 errors, 288,726 rows (the two 23-Sep failures were manual tests).
- API: all 26 routes the dashboard calls return 200 with rows; data to 2026-08-29 (label routes to 2026-08-26 = 3-day label horizon).

## Root cause of the stale tables
sql/62 and sql/63 (23-Sep) dropped 31 legacy/orphan objects, but `migrate` -- run on every API deploy -- re-CREATEd them
(sql/01, 05, 07, 09, 27, 42) and re-INSERTed July demo rows (sql/02, 05, 07, 08). /ps2/status therefore still showed 21
"unmanaged" tables, 12 frozen at 2026-07-11/14.

## Changes
| Change | Detail |
|---|---|
| migrate list | sql/02, 05, 07, 08 removed (moved to `_retired/ps2_seed_backfills`); non-existent sql/10 entry removed |
| sql/01, 09, 27, 42 | CREATE/INDEX statements for the retired tables removed; verified on PG16: same errors before/after, exactly 8 objects no longer created |
| sql/27 | `ps2_business_impact` DDL moved in (read by `v_ps2_device_cascade`); fresh database now builds with 0 errors |
| sql/75 | re-applies the 62/63 drops (1 view, 31 tables) + 2 ad-hoc 2-row tables with no DDL and no reader |
| `cubic-mars-ps2-rds-push` | source moved to `_retired/ps2_rds_push`; **[AWS]** delete the function + role if still deployed |

## Open (not retired -- still read, but frozen at 2026-07-14)
`ps2_device_catalog` (global search `/ps2/devices`, Device-360 PS2 panel, PS1 top-risk station/operator join,
`v_ps2_device_latest`) and `ps2_device_cascades` (Device-360 recent chains) came from the retired July pipeline; no current
producer writes them. Device-360's cascade rank already moved to `v_ps2_device_cascade` (current). Next: repoint these readers
to current device-grain outputs, or add the catalog/chains to the v2.5.4 export. Also: migrate lists `sql/12_ps1_serving_backfill.sql`,
which does not exist (PS1, harmless, reported as missing).

## Deployed 27-Sep (evening)
sql/75 applied: 32 statements, 0 failed (1 view + 31 tables; `ps2_v2_run_quality` was already absent). `cubic-mars-ps2-rds-push`
confirmed deleted (ResourceNotFound). Side effect found and fixed: `/ps2/status` compared every table's `city_id` with one
parameter whose type Postgres takes from the FIRST table in its UNION; after the drop the first table's `city_id` was varchar, so
every `city_code` table failed ("operator does not exist: city_code = text") and the route reported 0 tables. The route now
reads every UNION column as text (`city_id`, `run_id`, `computed_date`, `notebook_version`, `as_of_ts`): loader-made tables
store some of them as text/varchar while DDL tables use city_code/date/uuid/timestamp, and a UNION needs one type per column.

`sql/76_ps2_push_orphans.sql`: 8 tables the retired push Lambda auto-created on 26-Jul under raw source names
(`*_device`, `ps2_network_centrality_subsystem`) plus its `ps2_load_audit` -- frozen at 2026-07-26, no reader.
