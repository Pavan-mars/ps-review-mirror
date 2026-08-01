# PS2 Cascade — SageMaker Batch Transform pipeline (device + serial grain, v1.0)

A once-a-day fleet score, mirroring PS5's architecture exactly per PK's direction:
**state-refresh → S3 → Batch Transform (BYOC ECR image) → S3 → RDS loader → RDS → dashboard**.
No always-on endpoint. **No Databricks gold/silver table is touched by this pipeline** — everything
reads read-only Parquet mirrors of `gold.device_ps2_chains` + `silver.hw_config_current` from S3, and
all new state lives in SageMaker (this folder) and RDS.

```
                    weekly-ish (retrain)                       daily (score)
  SageMaker notebook ──► fitted params + model.tar.gz ──► ECR image (cubic-mars-ps2-scorer)
        │                        │                              │
        │                  register_mlflow                (built once per model)
        ▼                        ▼                              ▼
  refresh_device_state ─► s3://…/ps2/state ─► Batch Transform ─► s3://…/ps2/scored ─► load_…_to_rds ─► RDS ─► dashboard
        │
        └─► s3://…/ps2/population  (phi, cond-prob, assoc-rules, centrality, contagion — computed
                                     directly in this same step, no model, no Batch Transform)
```

## Family split (confirmed with PK) — this is the key design decision
Of the eleven PS2 analysis families, only three have *fitted parameters* worth serving behind a
model artifact; the rest are population-level statistics recomputed fresh from the day's state.

| Family | Type | Where it runs | Grain |
|---|---|---|---|
| Markov transition matrix | **SCOREABLE** (fitted params) | Batch Transform, `cubic-mars-ps2-scorer` image | device **and** serial |
| HMM 3-state regime (hmmlearn) | **SCOREABLE** (fitted params) | Batch Transform, `cubic-mars-ps2-scorer` image | device **and** serial |
| Chronic-recurrence classification | **SCOREABLE** (fitted params) | Batch Transform, `cubic-mars-ps2-scorer` image | device **and** serial |
| Phi-correlation | POPULATION-LEVEL (no model) | `refresh_device_state.py`, directly | fleet |
| Conditional probability | POPULATION-LEVEL (no model) | `refresh_device_state.py`, directly | fleet |
| Association rules (mlxtend) | POPULATION-LEVEL (no model) | `refresh_device_state.py`, directly | fleet |
| Network centrality (networkx betweenness) | POPULATION-LEVEL (no model) | `refresh_device_state.py`, directly | fleet |
| Facility contagion | POPULATION-LEVEL (no model) | `refresh_device_state.py`, directly | fleet |

Serial-grain state does **not** exist as a Databricks gold table — it is derived in-pipeline by
`to_serial_grain()` in `refresh_device_state.py`, which joins device-grain state onto the
authoritative `silver.hw_config_current` catalog (never the sparse per-event
`COMPONENT_SERIAL_NBR` column on the raw chain events).

## Two container images (mirrors the project's "notebook does the heavy lifting, the served
container stays minimal" convention)
| Image | Purpose | Contents |
|---|---|---|
| `cubic-mars-ps2-scorer` | BYOC Batch Transform serving image | `ps2_scoring_core.py` + `serve.py`/Flask/gunicorn — numpy/pandas/pyarrow only, no mlxtend/networkx/hmmlearn |
| `cubic-mars-ps2-pipeline` | Daily orchestration (Processing Jobs) | `refresh_device_state.py`, `run_batch_transform.py`, `load_transform_output_to_rds.py`, `fit_ps2_models.py`, `package_model.py`, `register_mlflow.py` — mlxtend, networkx, hmmlearn, sagemaker SDK, psycopg2, mlflow |

## Components (all in `sagemaker/ps2/batch/`)
| File | Role | Verified |
|---|---|---|
| `container/ps2_scoring_core.py` | shared scoring core for markov/hmm/recurrence, both grains | `py_compile` + self-test, 6 assertions incl. serial-identity pass-through, pass on-device |
| `container/serve.py` + `Dockerfile` + `serve` + `requirements.txt` | BYOC image: `/ping` + `/invocations`, routes by `family` column | mirrors PS5's serve pattern |
| `pipeline/fit_ps2_models.py` | fits markov/hmm/recurrence params from the notebook's outputs | `py_compile` |
| `pipeline/package_model.py` | bundles fitted params → `model.tar.gz` → S3 | `py_compile` |
| `pipeline/register_mlflow.py` | MLflow pyfunc register (lineage), wraps the same `ps2_scoring_core` module used by the container | `py_compile` |
| `pipeline/run_batch_transform.py` | SageMaker `Model` + `Transformer`, iterates `FAMILIES=[markov,hmm,recurrence] × GRAINS=[device,serial]` (6 combos) off one shared `Model` object | `py_compile` |
| `pipeline/refresh_device_state.py` | daily device state, `to_serial_grain()` join onto `silver.hw_config_current`, + all five population-level families computed directly | `py_compile` |
| `pipeline/load_transform_output_to_rds.py` | idempotent S3→RDS load for all three scoreable families at both grains (`FAMILY_SER_SQL`/`_ser_row`) | `py_compile` |
| `pipeline/Dockerfile` | pipeline orchestration image; build context = `sagemaker/ps2/batch/` (parent of `pipeline/` + `container/`) | rewritten this pass, see bugs fixed below |
| `state_machine.asl.json` | Step Function ASL: `StateRefresh → BatchTransform → RDSLoad`, each a `sagemaker:createProcessingJob.sync` task with Retry/Catch → shared `PipelineFailed` | `python3 -m json.tool` OK |
| `deploy_pipeline.py` | renders the ASL template's `${...}` placeholders, creates/updates the state machine, wires a daily EventBridge rule | `py_compile` |

## RDS migration
`dashboard/backfill/07_phase1e_ps2_cascade_scoring.sql` — 100% additive, `CREATE TABLE IF NOT EXISTS`
throughout, safe to re-run, same idiom as migration 06. Adds/backstops 11 tables: `ps2_scoring_runs`
(lineage), `ps2_markov_device_scores` + `ps2_markov_serial_scores`, `ps2_hmm_regimes` (fleet
prevalence) + `ps2_hmm_device_regime` + `ps2_hmm_serial_regime`, `ps2_device_recurrence_scores` +
`ps2_serial_recurrence_scores`, `ps2_subsystem_associations`, `ps2_conditional_prob` (new),
`ps2_event_definition`. Verified on-device: 231 lines, transfers cleanly.

## Two bugs found and fixed during this transfer pass (worth flagging explicitly)
1. **Serial-identity columns were silently dropped during scoring.** None of `score_markov`,
   `score_hmm`, `score_recurrence` in `ps2_scoring_core.py` propagated `COMPONENT_SERIAL_NBR` /
   `COMPONENT_DESCRIPTION` from input rows to output rows — meaning serial-grain Batch Transform
   output for **all three** scoreable families would have lost serial identity, and the serial-grain
   RDS load would have had nothing to join on. Fixed with a shared `_carry()` helper applied in all
   three scoring functions; added explicit self-test coverage (`ALL SELFTESTS PASSED`, 6 assertions,
   re-verified on-device).
2. **Only markov had serial-grain RDS wiring**, despite `run_batch_transform.py` scoring all three
   families at both grains identically. Closed by adding `ps2_hmm_serial_regime` and
   `ps2_serial_recurrence_scores` to the migration and generalizing the loader
   (`FAMILY_SER_SQL`/`_ser_row`) to handle all three symmetrically.
3. **`pipeline/Dockerfile` had an invalid `COPY ../container/...` instruction** — Docker's build
   context can't be escaped with `../`. Fixed by restructuring so the pipeline image builds from
   `sagemaker/ps2/batch/` (the parent of both `pipeline/` and `container/`); see the corrected build
   command below.

## One-time setup (you run these — no AWS/Databricks access from this session)
**1. Build + push the scorer image:**
```
ACCT=170202974600 REGION=us-east-1 REPO=cubic-mars-ps2-scorer TAG=v1.0
cd sagemaker/ps2/batch/container
# aws ecr create-repository --repository-name $REPO --region $REGION   (if not already created)
docker build -t $REPO:$TAG .
docker tag $REPO:$TAG $ACCT.dkr.ecr.$REGION.amazonaws.com/$REPO:$TAG
aws ecr get-login-password --region $REGION | docker login --username AWS --password-stdin $ACCT.dkr.ecr.$REGION.amazonaws.com
docker push $ACCT.dkr.ecr.$REGION.amazonaws.com/$REPO:$TAG
```
**2. Build + push the pipeline (orchestration) image — note the build context is one level up:**
```
REPO=cubic-mars-ps2-pipeline TAG=v1.0
cd sagemaker/ps2/batch          # build context = parent of pipeline/ and container/
docker build -f pipeline/Dockerfile -t $REPO:$TAG .
docker tag $REPO:$TAG $ACCT.dkr.ecr.$REGION.amazonaws.com/$REPO:$TAG
docker push $ACCT.dkr.ecr.$REGION.amazonaws.com/$REPO:$TAG
```
**3. Apply RDS migration** `dashboard/backfill/07_phase1e_ps2_cascade_scoring.sql` (after migration 06).

## Per-retrain (whenever the notebook re-fits — no fixed cadence yet, propose weekly to match PS5)
```
python sagemaker/ps2/batch/pipeline/fit_ps2_models.py --gold-s3 s3://$B/chicago/gold/device_ps2_chains \
   --hwconfig-s3 s3://$B/chicago/silver/hw_config_current --out-dir ./ps2_params
python sagemaker/ps2/batch/pipeline/package_model.py --params-dir ./ps2_params \
   --bucket $B --prefix chicago/ps2/model --tag v1.0
python sagemaker/ps2/batch/pipeline/register_mlflow.py --params-dir ./ps2_params \
   --tracking-uri $MLFLOW_TRACKING_URI --name cubic-mars-ps2-cascade \
   --image-uri $ACCT.dkr.ecr.$REGION.amazonaws.com/cubic-mars-ps2-scorer:v1.0
```

## Per-day (score) — what the Step Function actually runs
```
ASOF=$(date -u +%F); B=cubic-mars-pm-s3-datalake-dev-gold-170202974600
# 1. state-refresh: device state + serial-grain join + all 5 population-level families, direct-computed
python sagemaker/ps2/batch/pipeline/refresh_device_state.py \
   --gold-s3 s3://$B/chicago/gold/device_ps2_chains --hwconfig-s3 s3://$B/chicago/silver/hw_config_current \
   --asof $ASOF --bucket $B --state-prefix chicago/ps2/state --population-prefix chicago/ps2/population
# 2. Batch Transform: markov/hmm/recurrence × device/serial (6 combos, one shared Model object)
python sagemaker/ps2/batch/pipeline/run_batch_transform.py \
   --image-uri $ACCT.dkr.ecr.$REGION.amazonaws.com/cubic-mars-ps2-scorer:v1.0 \
   --model-data s3://$B/chicago/ps2/model/model_v1.0.tar.gz --role $SM_EXEC_ROLE \
   --state-prefix s3://$B/chicago/ps2/state --output s3://$B/chicago/ps2/scored --asof $ASOF
# 3. load to RDS (idempotent; all 3 scoreable families × 2 grains + population tables)
python sagemaker/ps2/batch/pipeline/load_transform_output_to_rds.py \
   --scored s3://$B/chicago/ps2/scored --population s3://$B/chicago/ps2/population \
   --asof $ASOF --city CHI --secret cubic/rds/dashboard
```

## Orchestration — built this pass
`state_machine.asl.json` wraps the three daily steps as `sagemaker:createProcessingJob.sync` Task
states (`StateRefresh → BatchTransform → RDSLoad`), each with a `Retry` (2 attempts, 30s, backoff
2.0x) and a `Catch` routing to a shared `PipelineFailed` Fail state. The "asof" date is computed
**inside each Processing Job container** via `date -u +%F` at run time, so the EventBridge schedule
itself stays static — no date needs to be threaded through Step Functions input.

Deploy with `deploy_pipeline.py` (one-time, idempotent — re-run to update):
```
python sagemaker/ps2/batch/deploy_pipeline.py \
   --state-machine-name cubic-mars-ps2-cascade-daily \
   --asl-template sagemaker/ps2/batch/state_machine.asl.json \
   --sagemaker-exec-role-arn arn:aws:iam::170202974600:role/<sagemaker-exec-role> \
   --states-exec-role-arn arn:aws:iam::170202974600:role/<step-functions-exec-role> \
   --eventbridge-role-arn arn:aws:iam::170202974600:role/<eventbridge-invoke-states-role> \
   --pipeline-image-uri $ACCT.dkr.ecr.$REGION.amazonaws.com/cubic-mars-ps2-pipeline:v1.0 \
   --scorer-image-uri   $ACCT.dkr.ecr.$REGION.amazonaws.com/cubic-mars-ps2-scorer:v1.0 \
   --model-tag v1.0 --gold-bucket cubic-mars-pm-s3-datalake-dev-gold-170202974600 \
   --rds-secret-id cubic/rds/dashboard --cron "cron(0 9 * * ? *)"   # 09:00 UTC daily
```
Use `--dry-run` first to render + validate the ASL without making any AWS calls.

## Guardrails
Dev account `170202974600`, region `us-east-1`. **No Databricks gold/silver table is ever written or
altered by this pipeline** — all reads are read-only S3 Parquet mirrors of
`gold.device_ps2_chains` / `silver.hw_config_current`; all new state lives in S3 (`chicago/ps2/*`)
and RDS. Aurora is the app-tier DB only, never the ML source of truth. Scope the SageMaker execution
role to the `chicago/ps2/{state,model,scored,population}` prefixes + ECR pull; the loader role to
Secrets Manager + VPC-to-Aurora, same pattern as PS5.

## Verified in-sandbox (this session, no AWS access — everything below is local/on-device verification only)
- `ps2_scoring_core.py`: `py_compile` clean, self-test `ALL SELFTESTS PASSED` (6 assertions incl.
  serial-identity pass-through for markov/hmm/recurrence), reproduced identically on-device after
  transfer.
- `fit_ps2_models.py`, `package_model.py`, `register_mlflow.py`, `run_batch_transform.py`,
  `refresh_device_state.py`, `load_transform_output_to_rds.py`: `py_compile` clean on-device.
- `pipeline/Dockerfile`: build-context bug fixed; not yet built (needs PK's `docker build` run).
- `state_machine.asl.json`: `python3 -m json.tool` valid on-device.
- `deploy_pipeline.py`: `py_compile` clean on-device; not yet run against real AWS (needs PK).
- `dashboard/backfill/07_phase1e_ps2_cascade_scoring.sql`: transferred, 231 lines, 11 `CREATE TABLE
  IF NOT EXISTS` statements confirmed; not yet applied to RDS (needs PK).

**Not yet done, needs PK:** build+push both ECR images, apply migration 07, run `fit_ps2_models.py`
once against real gold/silver Parquet exports to produce initial params, run
`package_model.py`+`register_mlflow.py`, run `deploy_pipeline.py` to stand up the Step Function +
EventBridge rule, then let the first daily run populate RDS end-to-end.
