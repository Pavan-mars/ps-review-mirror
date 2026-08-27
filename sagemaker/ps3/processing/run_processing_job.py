#!/usr/bin/env python3
# =============================================================================
# run_processing_job.py -- submit the PS3 V26 production notebook as a
# SageMaker Processing job.
#
# What this replaces: nothing re-runs PS3_V26_PRODUCTION.ipynb, so
# chicago/ps3_outputs/ has been static since 09-Aug and every ps3_v25_* table
# the dashboard serves is frozen with it. This job is the daily refresh.
#
# What the job does: the cubic-mars-ps3-processing image papermill-executes the
# notebook end to end. The notebook writes its OWN outputs to
# chicago/ps3_outputs/<table>/computed_date=<date>/run_id=<uuid>/ -- the exact
# layout cubic-mars-ps3-v25-loader reads -- so the ProcessingOutput here is only
# the executed notebook, the run record, not the data.
#
# Environment: every PS3_* variable set in the CALLING shell is passed through
# to the container unchanged, so
#     PS3_DATA_AS_OF_DATE=2026-08-27 python run_processing_job.py ...
# is the whole production interface. Nothing set means the notebook's own
# defaults (PRODUCTION mode, the 2026-04-11 vintage).
#
# Usage:
#   python run_processing_job.py \
#     --role-arn arn:aws:iam::170202974600:role/<sagemaker-exec-role> \
#     --image-uri 170202974600.dkr.ecr.us-east-1.amazonaws.com/cubic-mars-ps3-processing:v26 \
#     [--dry-run] [--wait]
# =============================================================================
import argparse
import json
import os
import sys
import time
from pathlib import Path

ARTIFACT_BUCKET = "cubic-mars-pm-s3-datalake-dev-artifacts-170202974600"
NOTEBOOK_NAME = "PS3_V26_PRODUCTION.ipynb"
DEFAULT_NOTEBOOK = (Path(__file__).resolve().parents[3]
                    / "notebooks" / "ps3_root_cause_analysis" / NOTEBOOK_NAME)


def parse_args():
    ap = argparse.ArgumentParser(
        description="Submit the PS3 V26 production notebook as a SageMaker Processing job")
    ap.add_argument("--role-arn", required=True,
                    help="execution role: read on the gold bucket's silver exports and on "
                         "chicago/ps3_inputs, write on chicago/ps3_outputs, plus ECR pull")
    ap.add_argument("--image-uri", required=True,
                    help="ECR URI of the cubic-mars-ps3-processing image")
    ap.add_argument("--region", default="us-east-1")
    ap.add_argument("--job-name", default=None,
                    help="default: cubic-mars-ps3-v26-<UTC timestamp>. SageMaker holds job "
                         "names per account+region FOREVER, failed ones included, so this must "
                         "stay unique per attempt -- never derive it from the as-of date alone.")
    ap.add_argument("--notebook", default=str(DEFAULT_NOTEBOOK),
                    help="local notebook to upload and run")
    ap.add_argument("--code-bucket", default=ARTIFACT_BUCKET,
                    help="bucket the notebook is staged to and the run record lands in")
    ap.add_argument("--code-prefix", default="chicago/ps3/processing_code")
    ap.add_argument("--runs-prefix", default="chicago/ps3/processing_runs")
    # Sized for a local[*] Spark over the silver device_event_enriched export.
    # UNMEASURED outside Studio -- treat the first supervised run as the
    # measurement and tighten both numbers afterwards.
    ap.add_argument("--instance-type", default="ml.m5.4xlarge")
    ap.add_argument("--instance-count", type=int, default=1,
                    help="must stay 1: the notebook's Spark is local[*], so extra instances "
                         "would each re-run the whole notebook rather than share the work")
    ap.add_argument("--volume-gb", type=int, default=100,
                    help="local Spark spills to disk; 100 GB is headroom, not a measurement")
    ap.add_argument("--max-runtime-sec", type=int, default=10800, help="3 hours")
    ap.add_argument("--wait", action="store_true",
                    help="poll until the job finishes and exit nonzero on failure")
    ap.add_argument("--dry-run", action="store_true",
                    help="print the create_processing_job request, upload nothing, submit nothing")
    return ap.parse_args()


def collect_env():
    """Pass through every PS3_* variable from the calling shell, verbatim."""
    env = {k: v for k, v in sorted(os.environ.items()) if k.startswith("PS3_")}
    print("PS3_* passed through: " + (json.dumps(env) if env else "(none -- notebook defaults apply)"))
    if not env.get("PS3_DATA_AS_OF_DATE", "").strip():
        print("NOTE: no PS3_DATA_AS_OF_DATE set, so the notebook's 2026-04-11 default applies and "
              "this run re-publishes the April vintage under a NEW run_id. That is the correct "
              "rehearsal until the incremental feed lands -- just do not read it as fresh data.")
    return env


def main():
    args = parse_args()
    job_name = args.job_name or time.strftime("cubic-mars-ps3-v26-%Y%m%d-%H%M%S", time.gmtime())
    nb_local = Path(args.notebook)
    if not nb_local.is_file():
        sys.exit("notebook not found: " + str(nb_local))

    code_key = args.code_prefix + "/" + job_name + "/" + NOTEBOOK_NAME
    code_s3_prefix = "s3://" + args.code_bucket + "/" + args.code_prefix + "/" + job_name
    runs_s3_uri = "s3://" + args.code_bucket + "/" + args.runs_prefix + "/" + job_name
    env = collect_env()

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
        # Run record only. The data outputs are the notebook's own S3 writes.
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
        print("DRY RUN -- nothing uploaded, nothing submitted (region " + args.region + ").")
        print("would upload : " + str(nb_local) + " -> s3://" + args.code_bucket + "/" + code_key)
        print("would submit :")
        print(json.dumps(request, indent=2))
        return

    import boto3  # deferred so --dry-run works without credentials
    s3 = boto3.client("s3", region_name=args.region)
    s3.upload_file(str(nb_local), args.code_bucket, code_key)
    print("uploaded " + nb_local.name + " -> s3://" + args.code_bucket + "/" + code_key)

    sm = boto3.client("sagemaker", region_name=args.region)
    sm.create_processing_job(**request)
    print("submitted " + job_name)
    print("run record -> " + runs_s3_uri + "/")
    print("follow     -> aws sagemaker describe-processing-job --processing-job-name "
          + job_name + " --query ProcessingJobStatus --region " + args.region)

    if args.wait:
        while True:
            time.sleep(30)
            desc = sm.describe_processing_job(ProcessingJobName=job_name)
            status = desc["ProcessingJobStatus"]
            print("  " + time.strftime("%H:%M:%S") + " " + status)
            if status in ("Completed", "Failed", "Stopped"):
                if status != "Completed":
                    sys.exit(job_name + ": " + status + " -- "
                             + desc.get("FailureReason", "no FailureReason"))
                break
        # A Completed job is not evidence the dashboard moved. Three facts, in order.
        print("")
        print("Completed. That alone does NOT mean the dashboard moved -- verify in order:")
        print("  1. aws s3 ls s3://" + ARTIFACT_BUCKET
              + "/chicago/ps3_outputs/ps3_run_control/ --recursive | tail")
        print("  2. invoke cubic-mars-ps3-v25-loader with a dry_run payload and read runs_considered")
        print("  3. then a real load, then /ps3/status for a single fresh computed_date")


if __name__ == "__main__":
    main()
