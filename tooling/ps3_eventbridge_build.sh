#!/bin/sh
# =====================================================================
# ps3_eventbridge_build.sh -- stand up the PS3 daily-scoring event plumbing.
#
# Mirrors tooling/ps1_eventbridge_build.sh, with the lessons from that
# script's first live contact baked in:
#   * DRY RUN by default; nothing mutates without --apply.
#   * Step 0 refuses to run if any of the four rules already exists,
#     because put-rule/put-targets are REPLACE operations. Unlike the PS1
#     script, the error tells you exactly which rule and how to reconcile.
#   * Every rule is created DISABLED. Enabling is a separate, deliberate
#     step after the state machine has passed a supervised REPLAY run.
#   * The EventBridge target uses the REAL event (no static Input=),
#     because the PS3 state machine's ParseEvent state reads
#     $.detail.{city,asof_date,incr_s3_uri}. A static Input would replace
#     the event and starve ParseEvent -- the exact S1 defect found in the
#     PS1 wiring on 24-Aug.
#
# WHAT THIS CREATES (all in account 170202974600, us-east-1):
#   1. State machine cubic-mars-ps3-daily-scoring from
#      tooling/sfn/ps3_daily_scoring.asl.json (placeholders were pinned
#      25-Aug: retained Model object, chicago/ps3/scored prefix, the
#      shared alerts topic).
#   2. Rule cubic-mars-ps3-gold-export-complete (DISABLED) -- pattern
#      matches notebooks/ps3_gold_complete_event.py's PutEvents exactly.
#   3. Rule cubic-mars-ps3-sfn-failed (DISABLED) -> SNS. Works only
#      because the ASL now routes failures to a Fail state (25-Aug fix);
#      before that fix a failed run reported SUCCEEDED and this rule
#      would never have fired.
#   4. Rule cubic-mars-ps3-batch-failed (DISABLED) -> SNS. NOTE: Batch
#      Transform emits detail-type "SageMaker Transform Job State Change"
#      with TransformJobStatus -- NOT the Processing detail-type the PS1
#      script documents. That difference is why this is its own rule.
#   5. Watchdog rule (cubic-mars-ps3-watchdog) is NOT created here: its
#      Lambda does not exist anywhere (same gap as PS1's watchdog).
#      Create both watchdogs together when that Lambda is built.
#
# USAGE:
#   sh tooling/ps3_eventbridge_build.sh                       # dry run
#   sh tooling/ps3_eventbridge_build.sh --apply ROLE_ARN      # mutate
# where ROLE_ARN is an IAM role EventBridge can assume to start the state
# machine (needs states:StartExecution on the state machine ARN). The
# state-machine execution role needs sagemaker:CreateTransformJob +
# AddTags, lambda:InvokeFunction on cubic-mars-ps3-v25-loader,
# sns:Publish on the alerts topic, and the events managed rule for .sync.
# =====================================================================
set -eu

REGION=us-east-1
ACCOUNT=170202974600
SM_NAME=cubic-mars-ps3-daily-scoring
SM_ARN="arn:aws:states:${REGION}:${ACCOUNT}:stateMachine:${SM_NAME}"
ASL_FILE="$(dirname "$0")/sfn/ps3_daily_scoring.asl.json"
TOPIC_ARN="arn:aws:sns:${REGION}:${ACCOUNT}:cubic-mars-ps1-alerts"
RULE_GOLD=cubic-mars-ps3-gold-export-complete
RULE_SFN=cubic-mars-ps3-sfn-failed
RULE_BATCH=cubic-mars-ps3-batch-failed

APPLY=0
EVENTS_ROLE_ARN="${2:-}"
[ "${1:-}" = "--apply" ] && APPLY=1
if [ "$APPLY" = "1" ] && [ -z "$EVENTS_ROLE_ARN" ]; then
  echo "ERROR: --apply needs the EventBridge invoke role ARN as the 2nd arg." >&2
  exit 2
fi

run() {
  if [ "$APPLY" = "1" ]; then echo "+ $*"; "$@"; else echo "DRY: $*"; fi
}

# ---- step 0: refuse to stomp existing rules (put-rule REPLACES) -------
echo "== step 0: pre-flight =="
for R in "$RULE_GOLD" "$RULE_SFN" "$RULE_BATCH"; do
  if aws events describe-rule --name "$R" --region "$REGION" >/dev/null 2>&1; then
    echo "ERROR: rule $R already exists. put-rule would silently REPLACE it." >&2
    echo "  Reconcile by hand first: aws events delete-rule --name $R (after" >&2
    echo "  removing its targets), or edit this script if the rule is correct." >&2
    exit 2
  fi
done
[ -f "$ASL_FILE" ] || { echo "ERROR: $ASL_FILE not found" >&2; exit 2; }
grep -q PLACEHOLDER "$ASL_FILE" && { echo "ERROR: unfilled PLACEHOLDER in ASL" >&2; exit 2; }
echo "  clean: no conflicting rules, ASL present and fully pinned."

# ---- step 1: state machine -------------------------------------------
echo "== step 1: state machine =="
if aws stepfunctions describe-state-machine --state-machine-arn "$SM_ARN" --region "$REGION" >/dev/null 2>&1; then
  run aws stepfunctions update-state-machine --state-machine-arn "$SM_ARN" \
      --definition "file://$ASL_FILE" --region "$REGION"
else
  echo "NOTE: create needs the SFN execution role. Run (with the right role):"
  echo "  aws stepfunctions create-state-machine --name $SM_NAME \\"
  echo "    --definition file://$ASL_FILE \\"
  echo "    --role-arn arn:aws:iam::${ACCOUNT}:role/<SFN_EXEC_ROLE> --region $REGION"
  [ "$APPLY" = "1" ] && echo "  (not auto-created: choosing/creating that role is a deliberate step)"
fi

# ---- step 2: gold-export-complete -> state machine (DISABLED) ---------
echo "== step 2: $RULE_GOLD =="
PATTERN='{"source":["cubic.mars.databricks"],"detail-type":["PS3 Gold Export Complete"],"detail":{"city":["CHI"]}}'
run aws events put-rule --name "$RULE_GOLD" --state DISABLED \
    --event-pattern "$PATTERN" --region "$REGION" \
    --description "Starts PS3 daily scoring when Databricks confirms the PS3 gold export. Pattern matches ps3_gold_complete_event.py verbatim."
# no InputTransformer and no static Input: the whole event passes through,
# which is what ParseEvent expects.
run aws events put-targets --rule "$RULE_GOLD" --region "$REGION" --targets \
    "Id=ps3-daily-scoring,Arn=${SM_ARN},RoleArn=${EVENTS_ROLE_ARN:-ROLE_ARN_REQUIRED}"

# ---- step 3: sfn-failed -> SNS (DISABLED) -----------------------------
echo "== step 3: $RULE_SFN =="
SFN_PATTERN="{\"source\":[\"aws.states\"],\"detail-type\":[\"Step Functions Execution Status Change\"],\"detail\":{\"status\":[\"FAILED\",\"TIMED_OUT\",\"ABORTED\"],\"stateMachineArn\":[\"${SM_ARN}\"]}}"
run aws events put-rule --name "$RULE_SFN" --state DISABLED \
    --event-pattern "$SFN_PATTERN" --region "$REGION" \
    --description "PS3 daily-scoring execution ended FAILED/TIMED_OUT/ABORTED."
run aws events put-targets --rule "$RULE_SFN" --region "$REGION" --targets \
    "Id=ps3-sfn-failed-sns,Arn=${TOPIC_ARN}"

# ---- step 4: batch-transform-failed -> SNS (DISABLED) -----------------
echo "== step 4: $RULE_BATCH =="
BATCH_PATTERN='{"source":["aws.sagemaker"],"detail-type":["SageMaker Transform Job State Change"],"detail":{"TransformJobStatus":["Failed","Stopped"]}}'
run aws events put-rule --name "$RULE_BATCH" --state DISABLED \
    --event-pattern "$BATCH_PATTERN" --region "$REGION" \
    --description "Any SageMaker Batch Transform job failed or was stopped (account-wide; PS3 is currently the only Transform user)."
run aws events put-targets --rule "$RULE_BATCH" --region "$REGION" --targets \
    "Id=ps3-batch-failed-sns,Arn=${TOPIC_ARN}"

echo "== done =="
echo "Enable order after a supervised REPLAY run passes:"
echo "  aws events enable-rule --name $RULE_SFN --region $REGION"
echo "  aws events enable-rule --name $RULE_BATCH --region $REGION"
echo "  aws events enable-rule --name $RULE_GOLD --region $REGION   # LAST"
echo "Alarms land on ${TOPIC_ARN} (first confirmed subscriber added 25-Aug)."
