# Aurora migrations — cubic-mars-dashboard-api

Applied by `handler.py::migrate()` in the order listed in its file tuple.
Every file is idempotent and ADDITIVE. Never renumber an applied migration.

| # | File | Scope |
|---|---|---|
| 01 | 01_schema_core.sql | cities, dim_station, core shared types (`city_code`) |
| 02 | 02_phase1_ps2_ps5_backfill.sql | PS2 + PS5 phase-1 backfill |
| 03 | 03_phase1b_ps3_severity.sql | PS3 single-head city-level severity summary (2026-07-13 era) |
| 04 | 04_phase1c_ps1_failure.sql | PS1 failure summary / leaderboard / features |
| 05 | 05_phase1d_ps2_device.sql | PS2 device grain |
| 06 | 06_phase1d_ps3_device.sql | PS3 device metrics |
| 07 | 07_phase1e_ps2_new.sql | PS2 analytics |
| 08 | 08_ps2_run_backfill.sql | PS2 run backfill |
| 09 | 09_phase1f_ps2_rich.sql | PS2 rich analytics |
| 10 | (free) | never used |
| 11 | 11_phase1g_ps1_serving.sql | PS1 serving: predictions, model_performance, risk_trend, ... |
| 12 | (free) | never used |
| 13 | 13_phase2_device360.sql | PS1 device-360 |
| 14 | 14_phase3_dim_station.sql | dim_station phase 3 |
| 15 | 15_phase2a_ps3_two_head.sql | **PS3 two-head**: model_runs, head_summary/class_metrics/leaderboard/feature_importance, leakage_scan, incident/device/serial predictions, v_ps3_two_head_scorecard |
| 16 | 16_phase2b_ps1_batch_lineage.sql | PS1 batch lineage |
| 17 | 17_phase2c_ps3_two_head_views.sql | PS3 gate-aware views: v_ps3_device_360, v_ps3_device_risk, v_ps3_serial_risk |

## DO NOT APPLY — superseded external file

`PS3_delivery_pavan_updated_21Jul2026.zip → rds/04_phase1b_ps3_rootcause_and_serial.sql`
is **superseded by migration 15 and must never be applied.** It creates the same three
table names (`ps3_incident_predictions`, `ps3_device_predictions`, `ps3_serial_predictions`)
with an incompatible design:

| | bundle rds/04 | migration 15 (canonical) |
|---|---|---|
| run_id | `UUID` -> ps3_scoring_runs | `VARCHAR(48)` -> ps3_model_runs |
| incident grain | one row **per head** (`prediction_head`, `predicted_class`) | one row **per incident**, both heads as columns |
| severity col | `predicted_collapsed` | `pred_severity_collapsed` |
| date col | `as_of_date` | `computed_date` |
| timestamp | `incident_dtm` | `ae_start_dtm` |
| features | — | `features JSONB` |

Both use `CREATE TABLE IF NOT EXISTS`, so applying both does **not** raise an error —
whichever runs first silently wins and the other writer then fails at INSERT time with an
undefined-column error. `tooling/ps3_backfill_from_run.py` and the `/ps3/*` two-head routes
both target migration 15.

Assessed 2026-07-26.
