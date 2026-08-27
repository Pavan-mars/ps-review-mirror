# PS3 V26 — daily production run as a SageMaker Processing job

## Why this exists

Nothing re-runs `PS3_V26_PRODUCTION.ipynb`. It is the **only** writer of
`chicago/ps3_outputs/<table>/computed_date=<date>/run_id=<uuid>/`, which
`cubic-mars-ps3-v25-loader` reads to fill the `ps3_v25_*` Aurora tables, which every
`/ps3/v25/*` route serves. So the PS3 tab has been frozen on the 09-Aug run.

This is the daily refresh for that path. It is **not** the Batch Transform path — that one
writes `chicago/ps3/scored/`, which no dashboard route reads (see
`docs/PS3_GO_LIVE_GUIDE.md:206`, and the `ps3-two-paths` note in the project memory).

## Why a Processing job and not a Databricks task

The notebook builds its **own** `SparkSession ... .master("local[*]")` and locates its own
`JAVA_HOME`. It is a standalone/SageMaker-edition notebook, not a cluster notebook. Two things
make it automatable as written:

- **no `%pip` cells** — so there is no mid-notebook kernel restart, the thing that blocks PS5;
- **no widgets** — its whole interface is `PS3_*` environment variables.

## The image

`Dockerfile` bakes python 3.11 + a JRE 17 + pyspark 3.5.1 + the S3A connector jars. Three
versions move together and a mismatch shows up as a `NoSuchMethodError` inside `S3AFileSystem`
rather than as anything version-shaped:

| pyspark | hadoop-aws | aws-java-sdk-bundle |
|---|---|---|
| 3.5.1 (ships a Hadoop 3.3.4 client) | 3.3.4 | 1.12.262 |

The jars are baked rather than downloaded because the notebook's
`spark.jars.packages=org.apache.hadoop:hadoop-aws:3.3.4` triggers an Ivy resolve against Maven
Central on every session start — an external dependency at 07:00 that we do not control. The
image sets `PS3_SPARK_JARS_PACKAGES=""`, and the notebook now skips the resolve when that is
empty. Unset (i.e. in Studio) it behaves exactly as before.

**Build via CodeBuild** — CloudShell has no docker daemon and Docker Desktop is unusable on the
workstation. Same recipe as the dashboard image:

```
zip -r ps3-processing-src.zip .
aws s3 cp ps3-processing-src.zip s3://cubic-mars-pm-s3-datalake-dev-artifacts-170202974600/codebuild/
aws codebuild start-build --project-name <privileged amazonlinux 5.0 project>
```

Push to a **new** repo, `cubic-mars-ps3-processing`. Do not reuse `cubic-pdm/mars-ps3` — that is
the endpoint-serving image and carries a `/ping` + `/invocations` contract this one does not.
Set the repo `IMMUTABLE` + `scanOnPush` at creation.

## The run

```
python run_processing_job.py --role-arn <role> --image-uri <ecr-uri>:v26 --dry-run
PS3_DATA_AS_OF_DATE=2026-08-27 python run_processing_job.py --role-arn <role> --image-uri <ecr-uri>:v26 --wait
```

Every `PS3_*` variable in the calling shell is passed to the container verbatim. With none set,
the notebook's own defaults apply — `PRODUCTION` mode and the **2026-04-11** vintage — which is
the correct rehearsal until the incremental feed lands, but must not be read as fresh data.

`--instance-type ml.m5.4xlarge` and `--volume-gb 100` are **headroom, not measurements**: the
only timing on record is from Studio. Treat the first supervised run as the measurement and
tighten both afterwards. Keep `--instance-count 1` — the notebook's Spark is `local[*]`, so extra
instances would each re-run the whole notebook rather than share the work.

## Execution role

Read `s3://cubic-mars-pm-s3-datalake-dev-gold-.../chicago/silver/*` (the `device_event_enriched`
and `dim_device` exports) and `s3://<artifacts>/chicago/ps3_inputs/*`; write
`s3://<artifacts>/chicago/ps3_outputs/*` and the run-record prefix; plus ECR pull and CloudWatch
Logs. Capture whatever role you use into `tooling/out/` — the PS3 Step Functions and events roles
were built by hand and exist nowhere in the repo, and that gap is already costing us.

## Verification — a Completed job proves nothing on its own

This chain has produced green runs that loaded nothing. Acceptance is three facts, in order:

1. `aws s3 ls s3://<artifacts>/chicago/ps3_outputs/ps3_run_control/ --recursive | tail`
   shows a `manifest.json` under a **new** `computed_date=`/`run_id=`.
2. `cubic-mars-ps3-v25-loader` invoked with `{"dry_run": true}` reports that new run in
   `runs_considered` and finds all expected tables. It refuses a partial run by design — and
   since `55c30ea` it *raises* rather than returning a 500 body, so a refusal is visible.
3. A real load, then `/ps3/status` shows a single fresh `computed_date` (`coherent: true`).

Only then unpause anything.
