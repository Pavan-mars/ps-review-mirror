#!/usr/bin/env python3
# =============================================================================
# run_processing_job.py -- submit the PS5 v5.6 notebook as a SageMaker
# Processing job (single ml.m5.2xlarge; the engine is sklearn/pandas +
# scikit-survival/lifelines, not Spark; measured 27.4 min in Studio, capped
# at 45 min here).
#
# What the job does: the cubic-mars-ps5-processing image papermill-executes
# the notebook end to end. The notebook publishes its own outputs to
# s3://<gold>/<city>/ps5/notebook_outputs/ from Cell 7 WHEN the job passes
# PS5_PUBLISH=true; the job's ProcessingOutput is only the executed .ipynb -
# the run record - not the data.
#
# Environment: every PS5_* variable set in the CALLING shell is passed
# through to the container unchanged, so
#     PS5_RUN_DATE=2026-08-25 PS5_PUBLISH=true python run_processing_job.py ...
# is the whole production interface. Nothing set means the notebook's
# long-standing defaults (CHI, 2026-04-11 vintage, dry-run publish).
#
# Usage:
#   python run_processing_job.py \
#     --role-arn arn:aws:iam::170202974600:role/<sagemaker-exec-role> \
#     --image-uri 170202974600.dkr.ecr.us-east-1.amazonaws.com/cubic-mars-ps5-processing:v5.6 \
#     [--dry-run]
# =============================================================================
import argparse
import json
import os
import sys
import time
from pathlib import Path

DEFAULT_BUCKET = "cubic-mars-pm-s3-datalake-dev-gold-170202974600"
NOTEBOOK_NAME = "PS5_Reliability_Survival_v5_6.ipynb"
DEFAULT_NOTEBOOK = (Path(__file__).resolve().parents[3]
                    / "notebooks" / "ps5_reliability_survival" / NOTEBOOK_NAME)


def parse_args():
    ap = argparse.ArgumentParser(description="Submit the PS5 v5.6 notebook as a SageMaker Processing job")
    ap.add_argument("--role-arn", required=True,
                    help="execution role for the Processing job (S3 read/write on the gold bucket + ECR pull)")
    ap.add_argument("--image-uri", required=True,
                    help="ECR URI of the cubic-mars-ps5-processing image")
    ap.add_argument("--region", default="us-east-1")
    ap.add_argument("--job-name", default=None,
                    help="default: cubic-mars-ps5-notebook-<UTC timestamp>")
    ap.add_argument("--notebook", default=str(DEFAULT_NOTEBOOK),
                    help="local notebook to upload and run")
    ap.add_argument("--code-bucket", default=DEFAULT_BUCKET,
                    help="bucket the notebook is staged to and the run record lands in")
    ap.add_argument("--code-prefix", default="chicago/ps5/processing_code",
                    help="staging prefix for the notebook input")
    ap.add_argument("--runs-prefix", default="chicago/ps5/processing_runs",
                    help="prefix for the executed-notebook run record")
    ap.add_argument("--instance-type", default="ml.m5.2xlarge")
    ap.add_argument("--instance-count", type=int, default=1)
    ap.add_argument("--volume-gb", type=int, default=50)
    ap.add_argument("--max-runtime-sec", type=int, default=2700,
                    help="45 min; the Studio measurement was 27.4 min single-node")
    ap.add_argument("--wait", action="store_true",
                    help="poll until the job finishes and exit nonzero on failure")
    ap.add_argument("--dry-run", action="store_true",
                    help="print the create_processing_job request, upload nothing, submit nothing")
    return ap.parse_args()


def collect_env():
    """Pass through every PS5_* variable from the calling shell, verbatim."""
    env = {k: v for k, v in sorted(os.environ.items()) if k.startswith("PS5_")}
    if env.get("PS5_PUBLISH", "").strip().lower() == "true":
        print("NOTE: PS5_PUBLISH=true -- this run WILL write to "
              "s3://<gold>/<city>/ps5/notebook_outputs/ and the 07:20 UTC "
              "loader will pick it up. Supervise it.")
    return env


def main():
    args = parse_args()
    job_name = args.job_name or time.strftime("cubic-mars-ps5-notebook-%Y%m%d-%H%M%S", time.gmtime())
    nb_local = Path(args.notebook)
    if not nb_local.is_file():
        sys.exit(f"notebook not found: {nb_local}")

    code_key = f"{args.code_prefix}/{job_name}/{NOTEBOOK_NAME}"
    code_s3_prefix = f"s3://{args.code_bucket}/{args.code_prefix}/{job_name}"
    runs_s3_uri = f"s3://{args.code_bucket}/{args.runs_prefix}/{job_name}"
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
        # The run record only: papermill saves the executed .ipynb here.
        # The engine's data outputs are the notebook's OWN S3 writes (Cell 7),
        # which is exactly where cubic-mars-ps5-rds-loader already reads.
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
            time.sleep(30)
            desc = sm.describe_processing_job(ProcessingJobName=job_name)
            status = desc["ProcessingJobStatus"]
            print(f"  {time.strftime('%H:%M:%S')} {status}")
            if status in ("Completed", "Failed", "Stopped"):
                if status != "Completed":
                    sys.exit(f"{job_name}: {status} -- {desc.get('FailureReason', 'no FailureReason')}")
                break


if __name__ == "__main__":
    main()
