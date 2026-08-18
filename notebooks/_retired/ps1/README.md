# PS1 retired notebooks

Moved here 2026-08-17 — not part of the live pipeline. Nothing deleted; reversible via `git mv`.

## Still live (in `notebooks/ps1_failure_prediction/`)

| File | Role |
|---|---|
| `PS1_3d_GATE_SageMaker_MLflow_FeatureStore.ipynb` | Production train/deploy GATE |
| `PS1_3d_TVM_SageMaker_MLflow_FeatureStore.ipynb` | Production train/deploy TVM |
| `PS1_3d_VALIDATOR_SageMaker_MLflow_FeatureStore.ipynb` | Production train/deploy VALIDATOR |
| `PS1_VALIDATOR_Device_Bus_Serial_Map.ipynb` | Bus/serial map utility → `dim_device_bus` |

## Daily pipeline (in `notebooks/`)

| File | Role |
|---|---|
| `cross_wired_daily_job.py` | Live cross-wire export |
| `ps1_build_features_daily.py` | Daily features (deploy pending) |
| `ps1_batch_score_daily.py` | Daily batch scoring |
| `ps1_gold_complete_event.py` | Gold-complete EventBridge trigger |

## Retired here

| File | Why |
|---|---|
| `PS1_3d_GATE_OOS_Optimized_SageMaker_v4.ipynb` | OOS analysis only; no deploy path |
| `Chicago_PS1_Predictive_Failure.ipynb` | Early 7-day demo |

BYOC docker files: `_retired/docker/` (never deployed).
