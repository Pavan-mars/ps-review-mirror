# PS5 shadow stack — retired 26-Sep-2026

The first-generation PS5 path, replaced by notebook → `cubic-mars-ps5-rds-loader` → `v_ps5_device_rul` /
`v_ps5_serial_rul` → dashboard-api `/ps5/*` → `V4PS5Overview.jsx`.

| Here | Was |
|---|---|
| `ps5-api/` | Lambda `ps5-api` behind API GW `b1s4xxlddb`; only consumer was the removed V1 `PS5SLAReliabilityTab.jsx` |
| `ps5-rds-loader/` | Lambda `ps5-rds-loader` (bare name); wrote `ps5_reliability_estimates`, `ps5_serial_reliability`, `ps5_scoring_runs` |
| `ps5_daily_scorer/` | Lambda `cubic-mars-ps5-daily-scorer` — never deployed |
| `sagemaker_ps5_batch/`, `sagemaker_ps5_processing/` | SageMaker batch/processing path writing the same shadow tables |

Evidence (26-Sep CloudShell): 0 invocations in 14 days for `ps5-api` and `ps5-rds-loader`; the daily scorer does not exist;
the only PS5 EventBridge rule targets `cubic-mars-ps5-rds-loader`. Tables/views dropped by
`api/lambda/cubic-mars-dashboard-api/sql/68_ps5_shadow_drop.sql`. `dashboard/backfill/06,07` were byte-identical copies of
`ps5-rds-loader/sql/06,07` and were deleted. Kept here (not deleted) because this is the only copy of the source of two
functions that still exist in AWS until they are deleted.
