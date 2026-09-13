#!/usr/bin/env bash
# =====================================================================
# retire_ps3_batch_transform.sh -- one path per PS.
#
# Decision 13-Sep-2026 (PK): PS3 keeps ONE pipeline -- the V26 notebook
# run as a SageMaker Processing job, published to chicago/ps3_outputs and
# loaded by cubic-mars-ps3-v25-loader (the path the dashboard reads).
# The Batch Transform path (Step Functions -> createTransformJob ->
# chicago/ps3/scored -> a loader fenced against that prefix) is retired.
#
# Order: capture -> disable -> delete. Everything is captured to
# tooling/out/ps3_batch_retire_<stamp>/ first so it can be recreated.
# All three rules were created DISABLED on 25-Aug and never enabled.
# =====================================================================
set -euo pipefail
export AWS_PAGER=""
REGION=${REGION:-us-east-1}; ACCT=${ACCT:-170202974600}
SFN=cubic-mars-ps3-daily-scoring
RULES="cubic-mars-ps3-gold-export-complete cubic-mars-ps3-sfn-failed cubic-mars-ps3-batch-failed"
ROLES="cubic-mars-ps3-sfn-exec-dev cubic-mars-ps3-events-invoke-dev"
DRY_RUN=${DRY_RUN:-1}
OUT=tooling/out/ps3_batch_retire_$(date -u +%Y%m%dT%H%M%SZ); mkdir -p "$OUT"
SFN_ARN="arn:aws:states:$REGION:$ACCT:stateMachine:$SFN"

echo "== capture"
aws stepfunctions describe-state-machine --state-machine-arn "$SFN_ARN" --region "$REGION" --output json > "$OUT/sfn.json" 2>/dev/null || echo "  (state machine absent)"
aws stepfunctions list-executions --state-machine-arn "$SFN_ARN" --region "$REGION" --max-items 50 --output json > "$OUT/sfn_executions.json" 2>/dev/null || true
for R in $RULES; do
  aws events describe-rule --name "$R" --region "$REGION" --output json > "$OUT/rule_$R.json" 2>/dev/null || echo "  (rule $R absent)"
  aws events list-targets-by-rule --rule "$R" --region "$REGION" --output json > "$OUT/targets_$R.json" 2>/dev/null || true
done
for ROLE in $ROLES; do
  aws iam get-role --role-name "$ROLE" --output json > "$OUT/role_$ROLE.json" 2>/dev/null || echo "  (role $ROLE absent)"
  for P in $(aws iam list-role-policies --role-name "$ROLE" --query 'PolicyNames[]' --output text 2>/dev/null); do
    aws iam get-role-policy --role-name "$ROLE" --policy-name "$P" --output json > "$OUT/role_${ROLE}_$P.json"
  done
done
echo "  captured to $OUT"

echo "== what nothing reads any more (listing only; delete via the cleanup register after the 30-day quiet check)"
aws s3 ls "s3://cubic-mars-pm-s3-datalake-dev-artifacts-$ACCT/chicago/ps3/scored/" --recursive --summarize 2>/dev/null | tail -2
aws s3 ls "s3://cubic-mars-pm-s3-datalake-dev-gold-$ACCT/chicago/ps3/" --recursive --summarize 2>/dev/null | tail -2

if [ "$DRY_RUN" != "0" ]; then
  echo "DRY_RUN: would delete rules [$RULES] (targets first), state machine $SFN, roles [$ROLES] (inline policies first). Re-run with DRY_RUN=0."
  exit 0
fi

echo "== delete rules (targets first)"
for R in $RULES; do
  IDS=$(aws events list-targets-by-rule --rule "$R" --region "$REGION" --query 'Targets[].Id' --output text 2>/dev/null || true)
  [ -n "$IDS" ] && aws events remove-targets --rule "$R" --ids $IDS --region "$REGION" >/dev/null
  aws events delete-rule --name "$R" --region "$REGION" 2>/dev/null && echo "  deleted $R" || echo "  $R absent"
done
echo "== delete state machine"
aws stepfunctions delete-state-machine --state-machine-arn "$SFN_ARN" --region "$REGION" && echo "  deleted $SFN"
echo "== delete roles"
for ROLE in $ROLES; do
  for P in $(aws iam list-role-policies --role-name "$ROLE" --query 'PolicyNames[]' --output text 2>/dev/null); do
    aws iam delete-role-policy --role-name "$ROLE" --policy-name "$P"; done
  for A in $(aws iam list-attached-role-policies --role-name "$ROLE" --query 'AttachedPolicies[].PolicyArn' --output text 2>/dev/null); do
    aws iam detach-role-policy --role-name "$ROLE" --policy-arn "$A"; done
  aws iam delete-role --role-name "$ROLE" 2>/dev/null && echo "  deleted $ROLE" || echo "  $ROLE absent"
done
echo "== NOT touched (decide separately): retained Model chicago-ps3-root-cause-2026-07-13-*, model package chicago-ps3-root-cause/14, ECR cubic-pdm/mars-ps3, SNS topic (shared with PS1)."
echo "Repo side: tooling/sfn/ps3_daily_scoring.asl.json, tooling/ps3_eventbridge_build.sh, sagemaker/ps3/batch_transform_daily.py, sagemaker/ps3/deploy_endpoint.py -> _retired/ps3_batch_transform/ (done in the same commit as this script)."
