#!/usr/bin/env bash
# =============================================================================
# build_and_push_ecr.sh -- containerize the PS3 two-head BYOC image (Dockerfile
# in this same folder, unchanged from the 18-Jul delivery) and push it to ECR.
# Fills the gap the 18-Jul delivery flagged but didn't ship: the Dockerfile
# existed, nothing built/pushed it. This is that missing step.
#
# CUBIC MARS guardrail: us-east-1 only, SSO-only identity -- run this from an
# already-`aws sso login`'d shell. No credentials are read/written by this script.
#
# Usage:
#   ./build_and_push_ecr.sh [image_tag] [model_dir]
#   ./build_and_push_ecr.sh v1 ../../PS3_outputs_pavan/tvm   # tag v1, ping-test against TVM bundles
#   ./build_and_push_ecr.sh                                   # date-stamped tag, ping-test skipped
#
# model_dir (2nd positional arg) must contain the *_severity_bundle.joblib and
# *_root_cause_bundle.joblib the base PS3 notebook produced (e.g.
# PS3_outputs_pavan/tvm/). If omitted, the mandatory local_ping_invocations_test
# is SKIPPED WITH A WARNING, not silently -- do not push to a real endpoint off
# a build that skipped it; re-run with a model_dir once a base run exists.
#
# Env overrides (all optional, sane defaults for the CUBIC MARS dev account):
#   AWS_REGION            (default us-east-1)
#   ECR_REPO_NAME         (default cubic-pdm/mars-ps3)
#   AWS_ACCOUNT_ID         (auto-resolved via `aws sts get-caller-identity` if unset)
# =============================================================================
set -euo pipefail

REGION="${AWS_REGION:-us-east-1}"
REPO_NAME="${ECR_REPO_NAME:-cubic-pdm/mars-ps3}"
TAG="${1:-$(date +%Y%m%d-%H%M%S)}"
MODEL_DIR="${2:-}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "[1/6] Resolving AWS account id..."
ACCOUNT_ID="${AWS_ACCOUNT_ID:-$(aws sts get-caller-identity --query Account --output text --region "$REGION")}"
if [ -z "$ACCOUNT_ID" ]; then
  echo "ERROR: could not resolve AWS account id -- run 'aws sso login' first." >&2
  exit 1
fi
ECR_URI="${ACCOUNT_ID}.dkr.ecr.${REGION}.amazonaws.com"
IMAGE_URI="${ECR_URI}/${REPO_NAME}:${TAG}"
echo "      account=${ACCOUNT_ID} region=${REGION} repo=${REPO_NAME} tag=${TAG}"
echo "      target image: ${IMAGE_URI}"

echo "[2/6] Ensuring ECR repository exists..."
aws ecr describe-repositories --repository-names "$REPO_NAME" --region "$REGION" >/dev/null 2>&1 || \
  aws ecr create-repository \
    --repository-name "$REPO_NAME" \
    --region "$REGION" \
    --image-scanning-configuration scanOnPush=true \
    --encryption-configuration encryptionType=KMS \
    >/dev/null
echo "      repository ready: ${REPO_NAME}"

echo "[3/6] Docker login to ECR..."
aws ecr get-login-password --region "$REGION" | \
  docker login --username AWS --password-stdin "$ECR_URI"

echo "[4/6] Building image (linux/amd64 -- SageMaker endpoints run x86_64)..."
docker build --platform linux/amd64 -t "${REPO_NAME}:${TAG}" -f "${SCRIPT_DIR}/Dockerfile" "$SCRIPT_DIR"
docker tag "${REPO_NAME}:${TAG}" "$IMAGE_URI"
docker tag "${REPO_NAME}:${TAG}" "${ECR_URI}/${REPO_NAME}:latest"

echo "[5/6] MUST-PASS local health check before any push (v7.0 non-negotiable: local"
echo "      /ping + /invocations test before any SageMaker endpoint) ..."
if [ -z "$MODEL_DIR" ]; then
  echo "  WARNING: no model_dir given (2nd arg) -- SKIPPING local_ping_invocations_test." >&2
  echo "  Image will be pushed as :${TAG} but is UNVALIDATED. Do not point deploy_endpoint.py" >&2
  echo "  at it for a real endpoint until this has been run and passed against real bundles." >&2
else
  python3 "${SCRIPT_DIR}/local_ping_invocations_test.py" --model-dir "$MODEL_DIR" || {
    echo "ERROR: local_ping_invocations_test.py failed against ${MODEL_DIR} -- image will NOT be pushed." >&2
    exit 1
  }
fi

echo "[6/6] Pushing to ECR..."
docker push "$IMAGE_URI"
docker push "${ECR_URI}/${REPO_NAME}:latest"

echo ""
echo "Pushed: ${IMAGE_URI}"
echo "Next: python3 deploy_endpoint.py --image-uri ${IMAGE_URI} --model-artifact s3://.../ps3_model.tar.gz"
