#!/usr/bin/env python3
# =============================================================================
# run_processing_job.py -- submit the PS2 serial-grain notebook as a SageMaker
# Processing job.
#
# WHY A PROCESSING JOB AND NOT A DATABRICKS TASK.
# PS2 has two producers writing to chicago/ps2_outputs. The patterns notebook
# is Spark-native (1,061 pyspark references) and already runs as a Databricks
# task in medallion_ps2_daily. THIS notebook has zero pyspark references, eight
# sagemaker imports and a SageMaker MLflow ARN -- pandas end to end. It had no
# schedule at all, which is why its 27 tables sat at computed_date 2026-07-26
# while its sibling's 20 reached 2026-08-29, unlabelled, on the same tab.
#
# WHAT THE JOB PRODUCES. The image papermill-executes the notebook, which
# writes its 27 tables to s3://<artifacts>/chicago/ps2_outputs/ itself, with
# boto3, from write_output(). The ProcessingOutput here is ONLY the executed
# .ipynb -- the run record. cubic-mars-ps2-rds-loader already watches that
# prefix and needs no change.
#
# THE DATE IS NOT OPTIONAL. computed_date used to come from
# datetime.date.today(), so the partition was named after the day the job ran
# rather than the day the data covers -- which is how a 26-Jul partition came
# to hold April data. PS2SG_COMPUTED_DATE now sets it and the notebook refuses
# to publish when it disagrees with the source maximum. A scheduled run must
# therefore pass it, and must update it when upstream moves.
#
# MEMORY, NOT CORES. The notebook loads device_ps2_chains whole -- 2,975,907
# rows x 57 columns into pandas -- then computes per-serial phi matrices, HMM
# fits and association rules across ~4,549 serials. Its own comment says a
# kernel death here is almost always OOM and to prefer a memory-optimized
# size. Hence an r-family default rather than an m5.
#
# CHECK THE QUOTA BEFORE CHANGING IT. Processing-job quotas are separate from
# the Studio and training ones, and most types in this account sit at zero --
# ml.r5.2xlarge among them, which is what this defaulted to until a submit
# failed with ResourceLimitExceeded. What has quota:
#   aws service-quotas list-service-quotas --service-code sagemaker --max-items 500 --output text --query "Quotas[?contains(QuotaName,'processing job usage')].[QuotaName,Value]" | sort -k2 -rn
#
# Usage:
#   PS2SG_COMPUTED_DATE=2026-08-29 python run_processing_job.py \
#     --role-arn arn:aws:iam::170202974600:role/<sagemaker-exec-role> \
#     --image-uri 170202974600.dkr.ecr.us-east-1.amazonaws.com/cubic-mars-ps2-processing:v1 \
#     [--dry-run] [--wait]
# =============================================================================
import argparse
import json
import os
import re
import sys
import time
from pathlib import Path

DEFAULT_BUCKET = "cubic-mars-pm-s3-datalake-dev-artifacts-170202974600"
NOTEBOOK_NAME = "PS2_Serial_Grain_Analysis_v1_FIXED.ipynb"
DEFAULT_NOTEBOOK = (Path(__file__).resolve().parents[3]
                    / "notebooks" / "ps2_cascading_failure" / NOTEBOOK_NAME)


def parse_args():
    ap = argparse.ArgumentParser(
        description="Submit the PS2 serial-grain notebook as a SageMaker Processing job")
    ap.add_argument("--role-arn", required=True,
                    help="execution role: read gold+silver+bronze, write the artifacts bucket, "
                         "pull from ECR, and reach the SageMaker MLflow server")
    ap.add_argument("--image-uri", required=True,
                    help="ECR URI of the cubic-mars-ps2-processing image")
    ap.add_argument("--region", default="us-east-1")
    ap.add_argument("--job-name", default=None,
                    help="default: cubic-mars-ps2-serialgrain-<UTC timestamp>. "
                         "SageMaker keeps job names forever, failed ones included, "
                         "so a fixed name can be used exactly once.")
    ap.add_argument("--notebook", default=str(DEFAULT_NOTEBOOK))
    ap.add_argument("--code-bucket", default=DEFAULT_BUCKET)
    ap.add_argument("--code-prefix", default="chicago/ps2/processing_code")
    ap.add_argument("--runs-prefix", default="chicago/ps2/processing_runs")
    # Memory-optimized by the notebook's own advice; see the header.
    # ml.r5.2xlarge was the obvious pick and this account's Processing quota
    # for it is ZERO -- the provisioned quotas are Studio and training ones.
    # ml.r7i.2xlarge is the same 8 vCPU / 64 GiB a generation newer, quota 5.
    # For a first run prefer --instance-type ml.r7i.4xlarge (128 GiB): OOM is
    # this notebook's named failure mode, it prints peak RSS after every
    # family, and one run on headroom tells you what to set here permanently.
    ap.add_argument("--instance-type", default="ml.r7i.2xlarge")
    ap.add_argument("--instance-count", type=int, default=1)
    ap.add_argument("--volume-gb", type=int, default=50)
    ap.add_argument("--max-runtime-sec", type=int, default=14400,
                    help="4h. UNMEASURED on this runtime -- the only timings we have are "
                         "from Studio on a different instance. Narrow it once a real run lands.")
    ap.add_argument("--allow-missing-date", action="store_true",
                    help="submit without PS2SG_COMPUTED_DATE, letting the notebook fall back to "
                         "today's date. It will then refuse to publish unless today happens to "
                         "equal the source maximum. Only useful for a deliberate failure test.")
    ap.add_argument("--wait", action="store_true",
                    help="poll until the job finishes and exit nonzero on failure")
    ap.add_argument("--dry-run", action="store_true",
                    help="print the create_processing_job request, upload nothing, submit nothing")
    return ap.parse_args()


def collect_env(allow_missing_date, region):
    """Pass through every PS2SG_* variable from the calling shell, verbatim."""
    env = {k: v for k, v in sorted(os.environ.items()) if k.startswith("PS2SG_")}

    # THE REGION HAS TO BE IN THE ENVIRONMENT.                    22-Sep-2026
    # Studio sets AWS_DEFAULT_REGION; a Processing container does not, and job
    # cubic-mars-ps2-serialgrain-20260922-060637 died 2m33s in with
    #   NoRegionError: You must specify a region
    # out of botocore's endpoint resolver. The notebook hands s3fs a region
    # explicitly, but its bare boto3.client() calls and MLflow's have nothing
    # to resolve from. Setting both spellings costs nothing and fixes every
    # client at once, without editing the notebook for its runtime.
    env["AWS_DEFAULT_REGION"] = region
    env["AWS_REGION"] = region

    d = env.get("PS2SG_COMPUTED_DATE", "").strip()
    if not d:
        msg = ("PS2SG_COMPUTED_DATE is not set. The notebook would fall back to the job's own "
               "run date and then refuse to publish, because it checks that date against the "
               "source maximum. Set it to the last day gold actually carries.")
        if not allow_missing_date:
            sys.exit("refusing to submit: " + msg)
        print("WARNING: " + msg + " Continuing because --allow-missing-date was passed.")
    elif not re.fullmatch(r"\d{4}-\d{2}-\d{2}", d):
        sys.exit(f"refusing to submit: PS2SG_COMPUTED_DATE must be YYYY-MM-DD, got {d!r}")

    if env.get("PS2SG_ALLOW_DATE_MISMATCH", "").strip().lower() == "true":
        print("NOTE: PS2SG_ALLOW_DATE_MISMATCH=true -- the notebook will publish a partition "
              "named after a day it may not cover. That is the defect this guard exists to "
              "prevent; be certain the mismatch is deliberate.")

    print("NOTE: this notebook ALWAYS publishes. It has no REPLAY mode and no isolated prefix -- "
          "it writes 27 tables straight to chicago/ps2_outputs/, which the RDS loader reads. "
          "There is no rehearsal; the date guard is the only thing between a wrong label and "
          "the dashboard.")
    return env


def main():
    args = parse_args()
    job_name = args.job_name or time.strftime("cubic-mars-ps2-serialgrain-%Y%m%d-%H%M%S",
                                              time.gmtime())
    nb_local = Path(args.notebook)
    if not nb_local.is_file():
        sys.exit(f"notebook not found: {nb_local}")

    code_key = f"{args.code_prefix}/{job_name}/{NOTEBOOK_NAME}"
    code_s3_prefix = f"s3://{args.code_bucket}/{args.code_prefix}/{job_name}"
    runs_s3_uri = f"s3://{args.code_bucket}/{args.runs_prefix}/{job_name}"
    env = collect_env(args.allow_missing_date, args.region)

    request = {
        "ProcessingJobName": job_name,
        "RoleArn": args.role_arn,
        "AppSpecification": {"ImageUri": args.image_uri},
        "ProcessingResources": {
            "ClusterConfig": {
                "InstanceCount": args.instance_count,
                "InstanceType": args.instance_type,
                "VolumeSizeInGB": args.volume_gb,
            }
        },
        "ProcessingInputs": [
            {
                "InputName": "notebook",
                "S3Input": {
                    "S3Uri": code_s3_prefix,
                    "LocalPath": "/opt/ml/processing/input/notebook",
                    "S3DataType": "S3Prefix",
                    "S3InputMode": "File",
                },
            }
        ],
        # The run record only. The engine's data outputs are the notebook's own
        # 27 S3 writes, which is where cubic-mars-ps2-rds-loader already reads.
        "ProcessingOutputConfig": {
            "Outputs": [
                {
                    "OutputName": "executed-notebook",
                    "S3Output": {
                        "S3Uri": runs_s3_uri,
                        "LocalPath": "/opt/ml/processing/output",
                        "S3UploadMode": "EndOfJob",
                    },
                }
            ]
        },
        "Environment": env,
        "StoppingCondition": {"MaxRuntimeInSeconds": args.max_runtime_sec},
    }

    if args.dry_run:
        print(f"DRY RUN -- nothing uploaded, nothing submitted (region {args.region}).")
        print(f"would upload : {nb_local} -> s3://{args.code_bucket}/{code_key}")
        print("would submit :")
        print(json.dumps(request, indent=2))
        return

    import boto3  # deferred so --dry-run works without credentials
    s3 = boto3.client("s3", region_name=args.region)
    s3.upload_file(str(nb_local), args.code_bucket, code_key)
    print(f"uploaded {nb_local.name} -> s3://{args.code_bucket}/{code_key}")

    sm = boto3.client("sagemaker", region_name=args.region)
    sm.create_processing_job(**request)
    print(f"submitted {job_name}")
    print(f"run record -> {runs_s3_uri}/")
    print(f"follow     -> aws sagemaker describe-processing-job --processing-job-name {job_name} "
          f"--query ProcessingJobStatus --region {args.region}")

    if args.wait:
        while True:
            time.sleep(60)
            desc = sm.describe_processing_job(ProcessingJobName=job_name)
            status = desc["ProcessingJobStatus"]
            print(f"  {time.strftime('%H:%M:%S')} {status}")
            if status in ("Completed", "Failed", "Stopped"):
                if status != "Completed":
                    sys.exit(f"{job_name}: {status} -- "
                             f"{desc.get('FailureReason', 'no FailureReason')}")
                break
        # Completed is necessary, not sufficient: acceptance for this chain is
        # terminal status AND objects under the expected partition AND rows in
        # the target tables for that date.
        print("\nCompleted. Acceptance is NOT this status alone -- confirm:")
        print("  1. aws s3 ls s3://%s/chicago/ps2_outputs/ps2_cascade_sankey_subsystem/"
              % args.code_bucket)
        print("     shows computed_date=%s" % (env.get("PS2SG_COMPUTED_DATE") or "<the run's date>"))
        print("  2. the loader commits it, and /ps2/serial/sankey returns that computed_date")


if __name__ == "__main__":
    main()
