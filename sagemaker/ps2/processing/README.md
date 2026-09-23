# PS2 serial-grain Processing job

Runs `notebooks/ps2_cascading_failure/PS2_Serial_Grain_Analysis_v1_FIXED.ipynb`
as a SageMaker Processing job.

## Why this exists

PS2 has **two** producers writing to the same `chicago/ps2_outputs` prefix:

| Notebook | Engine | Tables | Runs where |
|---|---|---|---|
| `PS2_Failure_Patterns_v2_5_4_Union_Minutes.ipynb` | Spark (1,061 pyspark refs) | 20 `ps2_v25_*` / `ps2_v2_*` | Databricks task in `medallion_ps2_daily` |
| `PS2_Serial_Grain_Analysis_v1_FIXED.ipynb` | pandas (0 pyspark refs) | 27 serial/device/subsystem | **nowhere — this kit is the fix** |

With no schedule of its own the second one fell five weeks behind the first.
Its tables sat at `computed_date=2026-07-26` while its sibling's reached
`2026-08-29`, both published onto one dashboard tab with nothing on screen
saying which was which.

It is **not** a Databricks task because it is not a Spark workload: zero
pyspark references, eight `sagemaker` imports, a SageMaker MLflow ARN and three
pip-install cells. A four-worker Spark job cluster is the wrong runtime, and
putting it there would have looked like a fix and failed on first run.

## The date is the whole safety mechanism

`computed_date` used to come from `datetime.date.today()`, so a partition was
named after the day the job ran rather than the day the data covers. That is
how a 26-Jul partition came to hold April data.

`PS2SG_COMPUTED_DATE` now sets it, and the notebook **refuses to publish** when
it disagrees with the source maximum. `run_processing_job.py` refuses to submit
without it.

This notebook has **no REPLAY mode and no isolated prefix**. Unlike the patterns
notebook, every run writes straight to the production prefix the loader reads.
There is no rehearsal — the date guard is the only thing standing between a
wrong label and the dashboard.

## Build

CloudShell has no docker daemon and Docker Desktop is unusable here, so build
through CodeBuild on a privileged `amazonlinux2-x86_64-standard:5.0` project,
same as the PS3 image.

```bash
B=cubic-mars-pm-s3-datalake-dev-artifacts-170202974600
cd sagemaker/ps2/processing && zip -qr /tmp/ps2-proc-src.zip . -x '__pycache__/*'
aws s3 cp /tmp/ps2-proc-src.zip s3://$B/codebuild/ps2-proc-src.zip
aws codebuild start-build --project-name <privileged-project> \
  --source-location-override "$B/codebuild/ps2-proc-src.zip" \
  --query 'build.id' --output text
```

The buildspec is **self-testing**: it boots the image and pushes only if four
gates pass — every engine imports with numpy<2, pyspark is *absent* (this is a
pandas image and should stay one), papermill drives `gate_notebook.ipynb`
through the real kernel including the degenerate-HMM path, and the ENTRYPOINT
points at the real notebook rather than the gate one.

Repository is `cubic-mars-ps2-processing`, created IMMUTABLE with scanOnPush.
Never a `cubic-pdm/*` repo — those are endpoint-serving images with a
`/ping` + `/invocations` contract this image does not have.

## Run

```bash
PS2SG_COMPUTED_DATE=2026-08-29 python run_processing_job.py \
  --role-arn arn:aws:iam::170202974600:role/<sagemaker-exec-role> \
  --image-uri 170202974600.dkr.ecr.us-east-1.amazonaws.com/cubic-mars-ps2-processing:v1 \
  --dry-run
```

Drop `--dry-run` to submit; add `--wait` to poll. `ml.r7i.2xlarge` by default
because the notebook loads `device_ps2_chains` whole — 2,975,907 rows x 57
columns into pandas — then computes per-serial phi matrices, HMM fits and
association rules across ~4,549 serials. Its own comment says a kernel death
here is almost always OOM and to prefer a memory-optimized size.

Do **not** set `ml.r5.2xlarge`. This account's *Processing* quota for it is
**zero** — Processing quotas are a separate pool from the Studio and training
ones, so an instance type you use daily in Studio can still be unavailable
here — and a submit fails with `ResourceLimitExceeded`. This document used to
recommend it, against the code's own default. The first run on r7i completed
in 16 minutes with a peak RSS of 20 GB on a 128 GiB instance.

## Acceptance

A `Completed` status proves nothing on its own. All three:

1. `Completed`, and the executed notebook lands under
   `chicago/ps2/processing_runs/<job>/`
2. objects under `chicago/ps2_outputs/<table>/computed_date=<the date you set>/`
3. the loader commits them, and `/ps2/serial/sankey` returns that `computed_date`

## Verified 22-Sep-2026, and what is still not

The image IS built and HAS run. `cubic-mars-ps2-processing:v1`, 3,457,010,889
bytes, pushed 2026-09-22 05:29 UTC. Three Processing jobs ran that morning --
`...-20260922-060637` and `...-20260922-073237` Failed with
`AlgorithmError: , exit code: 1`, and `...-20260922-073331` **Completed** in
17m52s (07:34:15 -> 07:52:07). All three predate the first commit of this kit
at 10:33 UTC, so the two failures were debugged from a working copy and their
fixes are what those commits are.

The proven configuration is NOT this file's stated default. The run that worked
used **`ml.r7i.4xlarge`** with role
`arn:aws:iam::170202974600:role/cubic-mars-role-sagemaker-exec-dev` and
`PS2SG_COMPUTED_DATE=2026-08-29`. `run_processing_job.py` defaults to
`ml.r7i.2xlarge`, which a 20 GB peak RSS would fit but which nothing has
actually run. Pass `--instance-type ml.r7i.4xlarge` to reproduce the known-good
case.

THE NOTEBOOK IS NOT IN THE IMAGE, which is why a rebuild is not needed after a
notebook change. The Dockerfile COPYs only `requirements.txt` and
`gate_notebook.ipynb`; `run_processing_job.py:200` uploads the local notebook to
`chicago/ps2/processing_code/<job>/` at submit time and mounts it as the
`notebook` ProcessingInput at `/opt/ml/processing/input/notebook`, exactly where
the ENTRYPOINT reads it. The image supplies the runtime, nothing more.

Still unverified:

- **The notebook is six commits ahead of the version that last ran.** 128d603,
  53b0c23, c1e4b62, e1f445e and 443649d all landed after the Completed run.
  The last of those retired 17 of its 27 write calls, so a run now publishes 11
  families, not 27.
- **MLflow.** The notebook sets a SageMaker MLflow tracking ARN and calls
  `set_experiment` at config time. That needs both the plugin (in the image) and
  IAM on the execution role. If the role cannot reach the tracking server, that
  cell fails and the run dies early.
- **Runtime.** `--max-runtime-sec` defaults to 4h as a guess. The only timings
  available are from Studio on a different instance type.
- **The serial source.** Serial identity resolves through
  `cta_servicenow_cmdb_ci`, and ServiceNow is frozen at 30-May-2026 — so serials
  are months older than the events they label. That is a data caveat this kit
  does not change.
