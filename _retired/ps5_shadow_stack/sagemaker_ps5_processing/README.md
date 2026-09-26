# PS5 v5.6 — notebook as a SageMaker Processing job

Runs `notebooks/ps5_reliability_survival/PS5_Reliability_Survival_v5_6.ipynb`
headless, end to end, on a single `ml.m5.2xlarge` (the engine is
sklearn/pandas + scikit-survival/lifelines — no Spark, no cluster). Outputs
land where `cubic-mars-ps5-rds-loader` (daily 07:20 UTC) already reads:
`s3://<gold_bucket>/chicago/ps5/notebook_outputs/`. The trigger is manual for
now; the gold-complete event wires in later without changing anything here.

What made this possible in the notebook itself (same commit):

- **Cell 1 is conditional** — imports the pinned stack first, installs only
  when something is missing. On this image everything is baked, so Run All
  (and therefore papermill) works with no kernel restart.
- **CONFIG reads `PS5_*` env vars** with the long-standing values as
  defaults — unset means the exact behaviour the notebook always had.
- **Publish is env-gated** — `PS5_PUBLISH=true` in the job environment, never
  a hand-edit. Default is the safe dry run (lists what would go, writes
  nothing), same convention as `PS2_RUN_MODE`.

## Environment variables (all optional)

| variable | default | meaning |
|---|---|---|
| `PS5_CITY_ID` | `CHI` | drives the output prefix (`chicago/ps5/...`); Boston = `BOS` |
| `PS5_RUN_DATE` | `2026-04-11` | telemetry hygiene cutoff; a scheduled job passes the real date; `""` keeps the default |
| `PS5_GOLD_BUCKET` | `cubic-mars-pm-s3-datalake-dev-gold-170202974600` | source + destination bucket |
| `PS5_TELEMETRY_START` | `2024-01-01` | telemetry-era window start |
| `PS5_CINDEX_FLOOR` | `0.65` | promotion gate, parsed as float |
| `PS5_PUBLISH` | unset (= dry run) | `true` = Cell 7 uploads to `<city>/ps5/notebook_outputs/` |

`run_processing_job.py` passes every `PS5_*` variable from the calling shell
into the container verbatim — the shell IS the job configuration.

## The chain, in order

1. **Bake the image** — CodeBuild, no local Docker (CloudShell has no
   daemon): zip this folder to the artifacts bucket and `start-build` on a
   privileged `amazonlinux standard:5.0` project, the same recipe that built
   the PS3 image. Push to a NEW ECR repo `cubic-mars-ps5-processing` — do not
   reuse the `cubic-pdm/*` serving repos.
2. **`run_processing_job.py --dry-run`** — prints the exact
   `create_processing_job` request; uploads nothing, submits nothing.
3. **Real run, `PS5_PUBLISH` unset** — the notebook executes fully, models
   fit, Cell 7 lists what WOULD be published and writes nothing. Read the
   executed notebook in `chicago/ps5/processing_runs/<job>/` and check the
   C-index lines and the publish manifest.
4. **Supervised run with `PS5_PUBLISH=true`** — Cell 7 uploads and then
   read-back-verifies the object count against S3.
5. **The 07:20 UTC loader** picks the outputs up on its next pass; verify via
   `/ps5/device-rul` and `/ps5/serial-rul`.

```
# step 2
python run_processing_job.py --dry-run \
  --role-arn arn:aws:iam::170202974600:role/<sagemaker-exec-role> \
  --image-uri 170202974600.dkr.ecr.us-east-1.amazonaws.com/cubic-mars-ps5-processing:v5.6

# step 4 (after step 3 checked out)
PS5_PUBLISH=true python run_processing_job.py --wait \
  --role-arn ... --image-uri ...
```

## Files

| file | role |
|---|---|
| `requirements.txt` | the notebook's Cell 1 pins + papermill/ipykernel (execution) + boto3 (Cell 7 publish) |
| `Dockerfile` | python:3.11-slim + the stack, papermill entrypoint; input/output at the standard `/opt/ml/processing/` mounts |
| `run_processing_job.py` | boto3 submitter: 1x ml.m5.2xlarge, 45-min cap, `PS5_*` env passthrough, `--dry-run`, `--wait` |

The job's `ProcessingOutput` is only the **executed notebook** (the run
record). The data outputs are the notebook's own Cell 7 S3 writes — that
separation is deliberate: the loader contract stays exactly what it is today.

## Unverified, stated plainly

- **The image has never been built.** The CodeBuild run, the ECR repo
  `cubic-mars-ps5-processing`, and the papermill entrypoint are all unproven
  until build day. scikit-survival 0.26.0 wheel availability on
  python:3.11-slim is expected but not demonstrated.
- **The 45-minute cap is an estimate** from one 27.4-min Studio measurement
  on comparable hardware; the first real job run is the actual measurement.
- **No job has executed the notebook via papermill yet** — Run-All safety was
  validated structurally (conditional Cell 1, env-driven CONFIG, ast/nbformat
  checks), not by a completed container run.
- **The execution role is not defined here** (`--role-arn` is deliberately a
  parameter): it needs gold-bucket read/write and ECR pull, and whether an
  existing SageMaker exec role suffices is unchecked.
