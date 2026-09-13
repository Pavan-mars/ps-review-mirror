# =============================================================================
# deploy_endpoint.py -- stand up (or update) the PS3 two-head REAL-TIME SageMaker
# endpoint from the ECR image build_and_push_ecr.sh just pushed, plus the model
# artifact (tar.gz of {tvm,gate}_{severity,root_cause}_bundle.joblib) the base
# PS3 notebook already produced.
#
# Uses boto3 directly (not the sagemaker SDK) -- consistent with the CUBIC MARS
# AWS skill's preference for explicit, auditable API calls in this account, and
# keeps this script's only dependency = boto3 (already in every Lambda/Glue image).
#
# CUBIC MARS guardrails applied here:
#   - us-east-1 only.
#   - VPC-private: the endpoint config takes a VpcConfig (subnets + SG) so the
#     endpoint has no public internet path, matching every other MARS endpoint.
#   - KMS: the endpoint's model data / volume is encrypted (KmsKeyId).
#   - Real-time endpoint (ml.m5.xlarge default) -- NOT a multi-model endpoint;
#     PS3 gets its own endpoint per the "5 MMEs, one per PS" guardrail meaning
#     PS3 is its own dedicated real-time endpoint outside the MME fleet (two-head
#     bundle doesn't fit the MME single-artifact-per-model contract cleanly).
#
# Usage:
#   python3 deploy_endpoint.py \
#     --image-uri <acct>.dkr.ecr.us-east-1.amazonaws.com/cubic-pdm/mars-ps3:v1 \
#     --model-artifact s3://cubic-mars-pm-s3-datalake-dev-gold-.../ps3/model.tar.gz \
#     --role-arn arn:aws:iam::<acct>:role/cubic-mars-sagemaker-execution-role \
#     --subnets subnet-0830633f6cab1b8a1,subnet-01ad20b3b49bf59de,subnet-0cd6e4bca78eaab8d \
#     --security-groups sg-xxxxxxxx \
#     --kms-key-id alias/cubic-mars-kms \
#     [--endpoint-name cubic-mars-ps3-two-head-dev] [--instance-type ml.m5.xlarge]
#     [--dry-run]
# =============================================================================
import argparse
import sys
import time

import boto3


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--image-uri", required=True)
    ap.add_argument("--model-artifact", required=True, help="s3://.../model.tar.gz containing *_bundle.joblib")
    ap.add_argument("--role-arn", required=True)
    ap.add_argument("--subnets", required=True, help="comma-separated subnet ids")
    ap.add_argument("--security-groups", required=True, help="comma-separated SG ids")
    ap.add_argument("--kms-key-id", default=None)
    ap.add_argument("--region", default="us-east-1")
    ap.add_argument("--model-name", default=None)
    ap.add_argument("--endpoint-config-name", default=None)
    ap.add_argument("--endpoint-name", default="cubic-mars-ps3-two-head-dev")
    ap.add_argument("--instance-type", default="ml.m5.xlarge")
    ap.add_argument("--instance-count", type=int, default=1)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    ts = time.strftime("%Y%m%d-%H%M%S")
    model_name = a.model_name or f"cubic-mars-ps3-two-head-{ts}"
    epc_name = a.endpoint_config_name or f"{a.endpoint_name}-config-{ts}"

    sm = boto3.client("sagemaker", region_name=a.region)

    print(f"[1/4] CreateModel: {model_name}")
    model_kwargs = dict(
        ModelName=model_name,
        PrimaryContainer={"Image": a.image_uri, "ModelDataUrl": a.model_artifact,
                          "Environment": {"SAGEMAKER_PROGRAM": "serve.py"}},
        ExecutionRoleArn=a.role_arn,
        VpcConfig={"Subnets": a.subnets.split(","), "SecurityGroupIds": a.security_groups.split(",")},
        Tags=[{"Key": "project", "Value": "cubic-mars"}, {"Key": "problem_statement", "Value": "PS3"},
              {"Key": "city", "Value": "CHI"}],
    )
    if a.dry_run:
        print("  [dry-run] would call CreateModel with:", model_kwargs)
    else:
        sm.create_model(**model_kwargs)
        print(f"  created model {model_name}")

    print(f"[2/4] CreateEndpointConfig: {epc_name}")
    variant = {
        "VariantName": "AllTraffic", "ModelName": model_name,
        "InitialInstanceCount": a.instance_count, "InstanceType": a.instance_type,
        "InitialVariantWeight": 1.0,
    }
    epc_kwargs = dict(EndpointConfigName=epc_name, ProductionVariants=[variant])
    if a.kms_key_id:
        epc_kwargs["KmsKeyId"] = a.kms_key_id
    if a.dry_run:
        print("  [dry-run] would call CreateEndpointConfig with:", epc_kwargs)
    else:
        sm.create_endpoint_config(**epc_kwargs)
        print(f"  created endpoint config {epc_name}")

    print(f"[3/4] Create-or-update endpoint: {a.endpoint_name}")
    if a.dry_run:
        print(f"  [dry-run] would create/update endpoint {a.endpoint_name} -> config {epc_name}")
        return

    existing = None
    try:
        existing = sm.describe_endpoint(EndpointName=a.endpoint_name)
    except sm.exceptions.ClientError:
        pass

    if existing is None:
        sm.create_endpoint(EndpointName=a.endpoint_name, EndpointConfigName=epc_name,
                           Tags=[{"Key": "project", "Value": "cubic-mars"}, {"Key": "problem_statement", "Value": "PS3"}])
        print(f"  CreateEndpoint issued for {a.endpoint_name}")
    else:
        sm.update_endpoint(EndpointName=a.endpoint_name, EndpointConfigName=epc_name)
        print(f"  UpdateEndpoint issued for {a.endpoint_name} (blue/green managed by SageMaker)")

    print("[4/4] Waiting for InService (this can take 5-10 minutes)...")
    waiter = sm.get_waiter("endpoint_in_service")
    try:
        waiter.wait(EndpointName=a.endpoint_name, WaiterConfig={"Delay": 20, "MaxAttempts": 60})
    except Exception as e:
        desc = sm.describe_endpoint(EndpointName=a.endpoint_name)
        print(f"ERROR: endpoint did not reach InService: {e}\nFailureReason={desc.get('FailureReason')}", file=sys.stderr)
        sys.exit(1)

    desc = sm.describe_endpoint(EndpointName=a.endpoint_name)
    print(f"\nEndpoint '{a.endpoint_name}' is {desc['EndpointStatus']}.")
    print(f"Next: python3 invoke_endpoint_smoketest.py --endpoint-name {a.endpoint_name}")


if __name__ == "__main__":
    main()
