# PS5 RUL — SageMaker Batch Transform pipeline (hardware-OOS-Set, v5.1)

A once-a-day fleet score: **state-refresh → S3 → Batch Transform (BYOC ECR image) → S3 → RDS loader → RDS → dashboard**.
No always-on endpoint. The scoring core is the same params-only numpy verified to reproduce the trained RUL (corr 1.000).

```
                    weekly (retrain)                          daily (score)
  SageMaker notebook ──► params + model.tar.gz ──► ECR image
        │                       │                     │
        │                 register_mlflow            (built once per model)
        ▼                       ▼                     ▼
  refresh_device_state ─► s3://…/ps5/state ─► Batch Transform ─► s3://…/ps5/scored ─► load_…_to_rds ─► RDS ─► /ps5/devices,/ps5/serials ─► dashboard
```

## Two cadences (the thing to get right)
- **Model/params/image** change only on **retrain** — weekly/monthly. Re-run the v5.1 notebook, re-`package_model`, re-build the image (only if code changed), `register_mlflow`.
- **Device state** changes **daily** — ages tick, new OOS events land. `refresh_device_state.py` recomputes it and Batch Transform scores it. Daily scoring only adds value with a daily state refresh; if within-week staleness is fine, run train+score weekly and skip the daily job.

## Components (all in this folder)
| File | Role | Verified |
|---|---|---|
| `container/ps5_scoring_core.py` | the one params-only scoring core (shared by container, loader, Lambda) | self-test corr 1.000 (device+serial) |
| `container/serve.py` + `Dockerfile` + `serve` + `requirements.txt` | BYOC image: `/ping` + `/invocations`, routes by `mars_device_category`, auto-detects device vs serial | `/invocations` tested via Flask client, matches training |
| `sagemaker/package_model.py` | bundle the 6 param JSONs → `model.tar.gz` → S3 | — |
| `sagemaker/register_mlflow.py` | MLflow pyfunc register (lineage, tags: event_def_version, cv_cindex, gate_pass, image_uri) | AST |
| `sagemaker/run_batch_transform.py` | SageMaker `Model` + `Transformer` (device + serial) | AST |
| `sagemaker/refresh_device_state.py` | daily state + serial roster from the engine's exact feature pipeline | e2e on synthetic → container |
| `sagemaker/load_transform_output_to_rds.py` | idempotent, gated S3→RDS upsert (+ scoring-run lineage) | cols match migration 05+06 |
| `api/ps5_reliability_routes.py` | `/ps5/devices` + `/ps5/serials` for the dashboard-api | shape matches the dashboard fetchers |

## One-time setup
**1. Build + push the ECR image** (see the header of `container/Dockerfile` for the exact commands):
```
ACCT=170202974600 REGION=us-east-1 REPO=cubic-mars-ps5-scorer TAG=v5.1
# aws ecr create-repository … ; docker build … ; docker push …
# -> 170202974600.dkr.ecr.us-east-1.amazonaws.com/cubic-mars-ps5-scorer:v5.1
```
**2. Apply RDS migration** `dashboard/backfill/06_phase1d_ps5_oos_set_reliability.sql` (after 05).

## Per-retrain (weekly)
```
# in the SageMaker notebook, after SMOKE_TEST=False run produced PS5_reliability_v5_outputs/
python sagemaker/package_model.py --outputs PS5_reliability_v5_outputs \
   --bucket cubic-mars-pm-s3-datalake-dev-gold-170202974600 --prefix chicago/ps5/model --tag v5.1
python sagemaker/register_mlflow.py --outputs PS5_reliability_v5_outputs \
   --tracking-uri $MLFLOW_TRACKING_URI --name cubic-mars-ps5-rul \
   --image-uri 170202974600.dkr.ecr.us-east-1.amazonaws.com/cubic-mars-ps5-scorer:v5.1
```

## Per-day (score)
```
ASOF=$(date +%F); B=cubic-mars-pm-s3-datalake-dev-gold-170202974600
# 1. refresh state (reads recent silver; writes <type>_device_state.parquet + _serial_roster.parquet to S3)
python sagemaker/refresh_device_state.py --engine notebooks/ps5_reliability_survival/ps5_reliability_engine_v51.py \
   --params-dir ./params --asof $ASOF --bucket $B --state-prefix chicago/ps5/state
# 2. batch transform (device + serial) on the ECR image
python sagemaker/run_batch_transform.py \
   --image-uri 170202974600.dkr.ecr.us-east-1.amazonaws.com/cubic-mars-ps5-scorer:v5.1 \
   --model-data s3://$B/chicago/ps5/model/model_v5.1.tar.gz --role $SM_EXEC_ROLE \
   --device-input s3://$B/chicago/ps5/state/device/ --serial-input s3://$B/chicago/ps5/state/serial/ \
   --output s3://$B/chicago/ps5/scored/ --asof $ASOF --max-payload 20
# 3. load to RDS (idempotent, gated by the model's C-index)
python sagemaker/load_transform_output_to_rds.py --scored s3://$B/chicago/ps5/scored \
   --asof $ASOF --city CHI --secret cubic/rds/dashboard      # add --dry-run to preview
```

## Orchestration (recommended)
Wrap the three daily steps in a **Step Function** (`refresh → transform → load`) on a daily **EventBridge** rule — you get retries and a visible run history. The `run_batch_transform` step waits for the job; the `load` step runs on success. Alternatively an EventBridge→Lambda that starts the transform and an S3-event→Lambda that loads.

## Wire the API routes
Add to the dashboard-api router: `GET /ps5/devices → ps5_devices(city)` and `GET /ps5/serials → ps5_serials(city)` (from `api/ps5_reliability_routes.py`). The module also ships a self-contained `lambda_handler` + Secrets-Manager connection if you deploy it as its own function. Once these return 200, the dashboard Device RUL / Serial Health tabs drop the SAMPLE ribbon automatically.

## Guardrails
Dev account **170202974600**, region **us-east-1**, SSO/OIDC roles (no long-lived keys), encrypt-by-default (SSE-KMS on the S3 prefixes). Scope the SageMaker execution role to the `chicago/ps5/{state,model,scored}` prefixes + ECR pull; the loader/route roles to Secrets Manager + VPC-to-Aurora. Aurora is the app-tier DB only — never the ML source.

## Gate policy (honest-by-default)
Scoring always runs; **promotion** is gated. `gate_pass` (C-index ≥ 0.65) flows params → scored output → `data_quality_gate_passed` in RDS, so the dashboard keeps the "below-floor / not promoted" state until the real-run C-index clears the floor.

## Verified in-sandbox
- scoring core: numpy-serve == sksurv-train, **corr 1.00000** (device), worst |Δ| 0.0d (serial)
- container `/invocations`: device multi-type routing + serial auto-detect, matches training (worst 0.1d)
- state-refresh → container: 520 devices / 1617 serials scored, event stamped, sensible band spread
- loader: INSERT columns/placeholders match migration 05+06
- API routes: output shape matches `apiPS5DeviceRUL` / `apiPS5SerialHealth`
