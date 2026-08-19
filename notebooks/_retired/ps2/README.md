# PS2 retired notebooks

Moved here 2026-08-19 - not part of the live pipeline. Nothing deleted; reversible via `git mv`.

## Still live (in `notebooks/ps2_cascading_failure/`)

| File | Role |
|---|---|
| `PS2_Failure_Patterns_v2_5_4_Union_Minutes.ipynb` | Production v2.5 run - produced the 47-table `chicago/ps2_outputs` export served by `cubic-mars-ps2-rds-loader` (daily 07:10 UTC) |
| `PS2_Serial_Grain_Analysis_v1_FIXED.ipynb` | Serial-grain + cross-PS delivery (21-Jul) - source of the live `ps2_*_serial` / cross-PS Aurora tables |
| `ps2_v254_union_verify_cell.py` | Verification helper for the v2_5_4 export |

## Retired here

| File | Why |
|---|---|
| `PS2_Failure_Patterns_v2_5_3_Storage_Safe.ipynb` | Prior v2.5 iteration; superseded by v2_5_4 |
| `PS2_SageMaker_MLflow_FeatureStore.ipynb` | Earlier SageMaker family; superseded (referenced only as provenance comments by `sagemaker/ps2/batch/`) |
| `PS2_Episode_Closure_Diagnostic_v1.ipynb` | One-off diagnostic |
| `PS2_Overlap_Diagnostic_v2.ipynb` | One-off diagnostic series |
| `PS2_Overlap_Diagnostic_v3.ipynb` | One-off diagnostic series |
| `PS2_Overlap_Diagnostic_v4.ipynb` | One-off diagnostic series |
| `Chicago_PS2_Cascade_Analysis.ipynb` | Early analysis |
| `ps2_overlap_resume_cell.py` | Helper for the retired overlap diagnostics |

Reminder before the next production run: set `PS2_PRODUCTION_EXPORT_PREFIX=chicago/ps2_outputs` (the daily loader reads the migrated prefix; see `docs/PS2_AUDIT_16Aug2026.md`).