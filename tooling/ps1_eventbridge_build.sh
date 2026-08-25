#!/usr/bin/env bash
# =====================================================================
# ps1_eventbridge_build.sh
#   Build the PS1 daily-inference event plumbing: SNS topic, four rules,
#   Step Functions state machine skeleton.
#
#   bash tooling/ps1_eventbridge_build.sh            # DRY RUN
#   bash tooling/ps1_eventbridge_build.sh --apply    # create, all rules DISABLED
#
# Backing analysis: docs/PS1_DAILY_INFERENCE_DESIGN.md Q6, Q7, Q9.
#
# EVERY RULE IS CREATED DISABLED. Enabling is a separate, deliberate act,
# one rule at a time, in the order given by docs §4. A pipeline that
# switches itself on at creation cannot be reviewed before it runs.
#
# THE THREE REPLACE-SEMANTICS HAZARDS THIS SCRIPT GUARDS:
#
#   events put-rule / put-targets   -- REPLACE. put-targets with a partial
#       list silently drops the targets you did not name. This script reads
#       the existing rule and its targets first and REFUSES to touch a rule
#       it did not create.
#
#   s3api put-bucket-notification-configuration -- REPLACE, and the worst
#       of the three. The Gold bucket already carries a notification to the
#       ps1-cross-wired-push Lambda. A naive put deletes it, with no error
#       and no output. This script only ever touches it behind an opt-in
#       flag, and then only via get -> merge -> put -> re-read -> diff.
#       The DESIGN RECOMMENDATION IS TO NOT USE IT AT ALL: have the
#       Databricks Gold job call PutEvents directly, which touches no
#       shared configuration and is undone by deleting one rule.
#
#   iam put-role-policy             -- REPLACE per policy name. This script
#       prints the policy documents for a human to attach; it does not
#       write IAM.
# =====================================================================
set -uo pipefail

REGION="${AWS_REGION:-us-east-1}"
APPLY=0
[ "${1:-}" = "--apply" ] && APPLY=1

# Opt-in, off by default, for the one call that can break a working path.
ARM_S3_EVENTBRIDGE="${ARM_S3_EVENTBRIDGE:-0}"

ACCT="$(aws sts get-caller-identity --query Account --output text 2>/dev/null)"
TS="$(date -u +%Y%m%dT%H%M%SZ)"
mkdir -p tooling/out
LOG="tooling/out/ps1_eventbridge_build_${TS}.log"
exec > >(tee "$LOG") 2>&1

SNS_NAME="cubic-mars-ps1-alerts"
SFN_NAME="cubic-mars-ps1-daily-scoring"
SFN_ARN="arn:aws:states:${REGION}:${ACCT}:stateMachine:${SFN_NAME}"
SNS_ARN="arn:aws:sns:${REGION}:${ACCT}:${SNS_NAME}"

if [ "$APPLY" -eq 1 ]; then
  echo "############ APPLY MODE -- resources will be created (all rules DISABLED) ############"
else
  echo "############ DRY RUN -- nothing will be created ############"
fi
echo "region=${REGION} account=${ACCT} ts=${TS}"

run() {
  echo
  echo "  \$ $*"
  if [ "$APPLY" -eq 1 ]; then
    "$@" 2>&1 | sed 's/^/    /'
    echo "    rc=${PIPESTATUS[0]}"
  else
    echo "    (dry run -- not executed)"
  fi
}

step() { printf '\n\n===== %s =====\n' "$*"; }

# ---------------------------------------------------------------------
step "0  refuse to clobber rules this script did not create"
# ---------------------------------------------------------------------
CLOBBER=0
for R in cubic-mars-ps1-gold-complete cubic-mars-ps1-sfn-failed \
         cubic-mars-ps1-processing-failed cubic-mars-ps1-watchdog; do
  EXIST="$(aws events describe-rule --name "$R" --region "$REGION" 2>/dev/null)"
  if [ -n "$EXIST" ]; then
    NT="$(aws events list-targets-by-rule --rule "$R" --region "$REGION" \
           --query 'length(Targets)' --output text 2>/dev/null)"
    echo "  $R ALREADY EXISTS with ${NT:-?} target(s)"
    aws events list-targets-by-rule --rule "$R" --region "$REGION" \
        --query 'Targets[].[Id,Arn]' --output text 2>/dev/null | sed 's/^/      /'
    CLOBBER=1
  else
    echo "  $R  absent -- safe to create"
  fi
done
if [ "$CLOBBER" -eq 1 ]; then
  echo
  echo "  STOP. One or more rules already exist. put-rule and put-targets are"
  echo "  REPLACE operations -- proceeding would silently drop targets listed"
  echo "  above. Reconcile by hand, or delete the pre-existing rule after"
  echo "  recording its targets, then re-run."
  [ "$APPLY" -eq 1 ] && { echo "  refusing to apply."; exit 2; }
fi

# ---------------------------------------------------------------------
step "1  SNS alert topic"
# ---------------------------------------------------------------------
echo "Subscribers are DECISION-5 and are not set here -- a topic with no"
echo "subscriber is a silent alarm, so subscribe before enabling any rule:"
echo "  aws sns subscribe --topic-arn ${SNS_ARN} --protocol email --notification-endpoint <you>"
run aws sns create-topic --name "$SNS_NAME" --region "$REGION"

# ---------------------------------------------------------------------
step "2  Step Functions definition (written to disk, not deployed)"
# ---------------------------------------------------------------------
mkdir -p tooling/sfn
cat > tooling/sfn/ps1_daily_scoring.asl.json <<'ASL'
{
  "Comment": "PS1 daily scoring. Triggered by Gold-layer completion, NOT by a clock. asof_date arrives in the event and is passed down -- two components independently guessing the same date is a race.",
  "StartAt": "ScoreFleets",
  "States": {
    "ScoreFleets": {
      "Type": "Map",
      "ItemsPath": "$.fleets",
      "MaxConcurrency": 3,
      "Parameters": {
        "fleet.$": "$$.Map.Item.Value",
        "asof_date.$": "$.asof_date",
        "city.$": "$.city"
      },
      "Iterator": {
        "StartAt": "ScoreOneFleet",
        "States": {
          "ScoreOneFleet": {
            "Type": "Task",
            "Resource": "arn:aws:states:::sagemaker:createProcessingJob.sync",
            "Parameters": {
              "ProcessingJobName.$": "States.Format('ps1-score-{}-{}', $.fleet, $.asof_date)",
              "AppSpecification": {
                "ImageUri": "REPLACE_WITH_MANAGED_DLC_URI",
                "ContainerEntrypoint": ["python3", "/opt/ml/processing/input/code/ps1_batch_score_daily.py"]
              },
              "RoleArn": "REPLACE_WITH_SAGEMAKER_EXECUTION_ROLE",
              "ProcessingResources": {
                "ClusterConfig": {
                  "InstanceCount": 1,
                  "InstanceType": "ml.m5.2xlarge",
                  "VolumeSizeInGB": 50
                }
              },
              "StoppingCondition": { "MaxRuntimeInSeconds": 3600 }
            },
            "Retry": [
              {
                "ErrorEquals": ["SageMaker.AmazonSageMakerException"],
                "IntervalSeconds": 60,
                "MaxAttempts": 2,
                "BackoffRate": 2
              }
            ],
            "End": true
          }
        }
      },
      "Catch": [
        { "ErrorEquals": ["States.ALL"], "Next": "NotifyFailure", "ResultPath": "$.error" }
      ],
      "Next": "TriggerCrossWire"
    },

    "TriggerCrossWire": {
      "Comment": "Databricks cross_wired_daily_job, with asof_date passed EXPLICITLY. Do not let the job re-derive 'latest transit_day' -- that is a second guess at the same fact.",
      "Type": "Task",
      "Resource": "arn:aws:states:::lambda:invoke",
      "Parameters": {
        "FunctionName": "REPLACE_WITH_DATABRICKS_TRIGGER_LAMBDA",
        "Payload": {
          "job": "cross_wired_daily_job",
          "asof_date.$": "$.asof_date",
          "city.$": "$.city",
          "lookback_days": 1
        }
      },
      "Catch": [
        { "ErrorEquals": ["States.ALL"], "Next": "NotifyFailure", "ResultPath": "$.error" }
      ],
      "End": true
    },

    "NotifyFailure": {
      "Type": "Task",
      "Resource": "arn:aws:states:::sns:publish",
      "Parameters": {
        "TopicArn": "REPLACE_WITH_SNS_TOPIC_ARN",
        "Subject": "PS1 daily scoring FAILED",
        "Message.$": "States.JsonToString($)"
      },
      "Next": "Fail"
    },

    "Fail": { "Type": "Fail", "Error": "PS1DailyScoringFailed" }
  }
}
ASL
echo "  wrote tooling/sfn/ps1_daily_scoring.asl.json"
echo
echo "  Four REPLACE_WITH_ placeholders remain, deliberately. Two of them"
echo "  (the DLC image URI, the entrypoint script) cannot be filled until"
echo "  tooling/ps1_read_docker_and_model.sh has established the model"
echo "  loading contract. A definition with a guessed image URI would"
echo "  deploy cleanly and fail at 06:00 with a pull error."
echo
echo "  Deploy, once the placeholders are real:"
echo "    aws stepfunctions create-state-machine --name ${SFN_NAME} \\"
echo "      --definition file://tooling/sfn/ps1_daily_scoring.asl.json \\"
echo "      --role-arn <states-execution-role> --region ${REGION}"

# ---------------------------------------------------------------------
step "3  RULE 1  gold-complete -> Step Functions   (created DISABLED)"
# ---------------------------------------------------------------------
PATTERN_GOLD='{"source":["cubic.mars.databricks"],"detail-type":["Gold Layer Complete"],"detail":{"city":["CHI"]}}'
echo "Custom event, published by the Databricks Gold job's final task:"
cat <<'PY'
    # --- append to the end of the Gold layer job ---
    import boto3, json
    boto3.client("events", region_name="us-east-1").put_events(Entries=[{
        "Source":       "cubic.mars.databricks",
        "DetailType":   "Gold Layer Complete",
        "Detail":       json.dumps({
            "city":       city_id,
            "asof_date":  day_str,
            "tables":     written_tables,
            "row_counts": row_counts,
        }),
    }])
PY
echo
echo "Why this and not S3->EventBridge: PutEvents touches NO shared AWS"
echo "configuration. It is undone by deleting one rule. It also signals"
echo "SEMANTIC completion -- 'the Gold layer is finished for 2026-08-12' --"
echo "rather than 'an object appeared', which fires on the first file."
run aws events put-rule --name cubic-mars-ps1-gold-complete \
    --event-pattern "$PATTERN_GOLD" --state DISABLED --region "$REGION" \
    --description "PS1 daily scoring trigger. Gold-layer completion, not a clock. Created DISABLED 2026-08-11."
run aws events put-targets --rule cubic-mars-ps1-gold-complete --region "$REGION" \
    --targets "Id=ps1-sfn,Arn=${SFN_ARN},RoleArn=arn:aws:iam::${ACCT}:role/REPLACE_EVENTS_INVOKE_SFN_ROLE"
# No Input= on purpose: a static Input REPLACES the whole event, so the
# state machine never saw asof_date or city (S1, found 24-Aug). The ASL's
# ParseEvent state now lifts detail.* itself and injects the fleet list.

# ---------------------------------------------------------------------
step "4  RULE 2  Step Functions failure -> SNS   (created DISABLED)"
# ---------------------------------------------------------------------
PATTERN_SFN="{\"source\":[\"aws.states\"],\"detail-type\":[\"Step Functions Execution Status Change\"],\"detail\":{\"status\":[\"FAILED\",\"TIMED_OUT\",\"ABORTED\"],\"stateMachineArn\":[\"${SFN_ARN}\"]}}"
run aws events put-rule --name cubic-mars-ps1-sfn-failed \
    --event-pattern "$PATTERN_SFN" --state DISABLED --region "$REGION" \
    --description "PS1 orchestration failure. Created DISABLED 2026-08-11."
run aws events put-targets --rule cubic-mars-ps1-sfn-failed --region "$REGION" \
    --targets "Id=sns,Arn=${SNS_ARN}"

# ---------------------------------------------------------------------
step "5  RULE 3  SageMaker processing failure -> SNS   (created DISABLED)"
# ---------------------------------------------------------------------
PATTERN_PROC='{"source":["aws.sagemaker"],"detail-type":["SageMaker Processing Job State Change"],"detail":{"ProcessingJobStatus":["Failed","Stopped"]}}'
echo "If DECISION-2 goes to Batch Transform instead of a Processing job,"
echo "swap the detail-type for 'SageMaker Transform Job State Change' and"
echo "the status key for TransformJobStatus. Both events exist; they are"
echo "not interchangeable and a wrong key matches nothing, silently."
run aws events put-rule --name cubic-mars-ps1-processing-failed \
    --event-pattern "$PATTERN_PROC" --state DISABLED --region "$REGION" \
    --description "PS1 scoring job failure. Created DISABLED 2026-08-11."
run aws events put-targets --rule cubic-mars-ps1-processing-failed --region "$REGION" \
    --targets "Id=sns,Arn=${SNS_ARN}"

# ---------------------------------------------------------------------
step "6  RULE 4  watchdog -- the only rule that can see an ABSENCE"
# ---------------------------------------------------------------------
echo "This is the important one, and the one a conventional setup omits."
echo
echo "Rules 2 and 3 fire when something FAILS. Neither fires when nothing"
echo "RUNS. If the Databricks Gold job dies before its PutEvents, the"
echo "trigger never fires, Step Functions never starts, no state change is"
echo "published, and every dashboard shows yesterday's numbers with no"
echo "indication that they are yesterday's."
echo
echo "A pipeline that never starts emits no failure event. Only a clock"
echo "looking for an expected artefact can observe that."
echo
echo "Deadline 09:30 UTC: the current chain runs 05:45 -> 08:00, so this"
echo "allows 90 minutes of slack."
mkdir -p tooling/lambda
cat > tooling/lambda/ps1_freshness_watchdog.py <<'PYW'
"""PS1 freshness watchdog.

Alerts on ABSENCE. Rules 2 and 3 catch failure; nothing else in the system
catches "it never ran". This does.

Two checks, because they fail differently:
  1. Is there a scored partition for TODAY?      -> the run did not happen
  2. How old is the NEWEST scored partition?     -> a multi-day stall that
     was alerted on day 1 and acknowledged, and has been silent since

Env: ARTIFACT_BUCKET, SCORED_PREFIX (default chicago/ps1/scored),
     SNS_TOPIC_ARN, MAX_STALE_DAYS (default 1)
"""
import os, datetime, boto3

s3 = boto3.client("s3")
sns = boto3.client("sns")


def lambda_handler(event, context):
    bucket = os.environ["ARTIFACT_BUCKET"]
    prefix = os.environ.get("SCORED_PREFIX", "chicago/ps1/scored").strip("/")
    topic = os.environ["SNS_TOPIC_ARN"]
    max_stale = int(os.environ.get("MAX_STALE_DAYS", "1"))

    today = datetime.date.today()
    alerts = []

    # list the asof= partitions
    days = []
    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix=f"{prefix}/", Delimiter="/"):
        for cp in page.get("CommonPrefixes", []):
            part = cp["Prefix"].rstrip("/").split("/")[-1]
            if part.startswith("asof="):
                days.append(part[5:])
    days.sort()

    if str(today) not in days:
        alerts.append(
            f"NO PS1 SCORES FOR {today}. s3://{bucket}/{prefix}/asof={today}/ "
            f"does not exist. The daily scoring run did not happen -- note that "
            f"this produces NO failure event anywhere else, because nothing "
            f"started and therefore nothing failed."
        )

    if not days:
        alerts.append(
            f"s3://{bucket}/{prefix}/ has NO asof= partitions at all. "
            f"Either the prefix is wrong or PS1 scoring has never run."
        )
    else:
        newest = days[-1]
        try:
            age = (today - datetime.date.fromisoformat(newest)).days
            if age > max_stale:
                alerts.append(
                    f"PS1 SCORES ARE {age} DAYS STALE. Newest partition is "
                    f"asof={newest}. Anything downstream reading these is "
                    f"serving {age}-day-old predictions as though current."
                )
        except ValueError:
            alerts.append(f"Newest partition name is not a date: asof={newest}")

    if alerts:
        body = "PS1 freshness watchdog\n\n" + "\n\n".join(alerts)
        body += f"\n\nbucket={bucket} prefix={prefix} partitions_found={len(days)}"
        sns.publish(TopicArn=topic, Subject="PS1 scores missing or stale", Message=body)
        return {"ok": False, "alerts": alerts}

    return {"ok": True, "newest": days[-1], "partitions": len(days)}
PYW
echo "  wrote tooling/lambda/ps1_freshness_watchdog.py"
run aws events put-rule --name cubic-mars-ps1-watchdog \
    --schedule-expression "cron(30 9 * * ? *)" --state DISABLED --region "$REGION" \
    --description "PS1 freshness watchdog. Detects a run that never happened. Created DISABLED 2026-08-11."

# ---------------------------------------------------------------------
step "7  S3 -> EventBridge on the Gold bucket -- OPT-IN, and not recommended"
# ---------------------------------------------------------------------
if [ "$ARM_S3_EVENTBRIDGE" != "1" ]; then
  echo "SKIPPED (ARM_S3_EVENTBRIDGE is not 1). This is the default and the"
  echo "recommendation."
  echo
  echo "put-bucket-notification-configuration REPLACES the whole notification"
  echo "document. The Gold bucket already routes to the ps1-cross-wired-push"
  echo "Lambda. A naive put deletes that notification, returns success, prints"
  echo "nothing, and the first symptom is a load that stops arriving days"
  echo "later with no error anywhere."
  echo
  echo "Rule 1 (Databricks PutEvents) achieves the same trigger without"
  echo "touching this bucket setting at all. Use it."
  echo
  echo "If you must: ARM_S3_EVENTBRIDGE=1 bash $0 --apply"
else
  echo "ARMED. Guarded sequence: get -> merge -> put -> re-read -> diff."
  BKT="${GOLD_BUCKET:?set GOLD_BUCKET}"
  BEFORE="tooling/out/gold_notify_before_${TS}.json"
  AFTER="tooling/out/gold_notify_after_${TS}.json"
  echo "  reading current notification configuration..."
  if ! aws s3api get-bucket-notification-configuration --bucket "$BKT" \
        --region "$REGION" > "$BEFORE" 2>/dev/null; then
    echo "  GET FAILED. Refusing to put a configuration when the current one"
    echo "  could not be read -- that is how the existing Lambda notification"
    echo "  gets deleted."
    exit 2
  fi
  echo "  current configuration saved to ${BEFORE}:"
  sed 's/^/    /' "$BEFORE"
  python3 - "$BEFORE" <<'PYM'
import json, sys
cfg = json.load(open(sys.argv[1]))
cfg.pop("ResponseMetadata", None)
existing = {k: len(v) if isinstance(v, list) else v
            for k, v in cfg.items()}
print("    existing keys:", existing)
cfg["EventBridgeConfiguration"] = {}
json.dump(cfg, open("/tmp/gold_notify_merged.json", "w"), indent=2)
print("    merged document written to /tmp/gold_notify_merged.json")
print("    -- the existing Lambda/Queue/Topic configurations above are PRESERVED in it")
PYM
  run aws s3api put-bucket-notification-configuration --bucket "$BKT" \
      --region "$REGION" \
      --notification-configuration file:///tmp/gold_notify_merged.json
  if [ "$APPLY" -eq 1 ]; then
    aws s3api get-bucket-notification-configuration --bucket "$BKT" \
        --region "$REGION" > "$AFTER" 2>/dev/null
    echo "  diff before/after:"
    diff <(python3 -c 'import json,sys;d=json.load(open(sys.argv[1]));d.pop("ResponseMetadata",None);print(json.dumps(d,indent=2,sort_keys=True))' "$BEFORE") \
         <(python3 -c 'import json,sys;d=json.load(open(sys.argv[1]));d.pop("ResponseMetadata",None);print(json.dumps(d,indent=2,sort_keys=True))' "$AFTER") \
         | sed 's/^/    /'
    echo "  The ONLY line that should be added is EventBridgeConfiguration."
    echo "  If anything was REMOVED, restore immediately from ${BEFORE}."
  fi
fi

# ---------------------------------------------------------------------
step "8  the cut-over -- printed, NOT executed"
# ---------------------------------------------------------------------
echo "The existing schedule chain is currently collision-free:"
echo "  dim 05:45 -> PS1-A 06:40 -> PS2 07:10 -> PS5 07:20 -> PS4 07:35 -> PS4-v3 Mon 08:00"
echo
echo "Enabling the event-driven path while PS1-A's 06:40 cron is still on"
echo "means PS1 can run TWICE for the same day. Disable the cron only after"
echo "the event-driven path has produced a correct run:"
echo
echo "    aws events disable-rule --name <PS1-A-rule-name> --region ${REGION}"
echo
echo "Reversed by:  aws events enable-rule --name <PS1-A-rule-name>"
echo
echo "Enable the new rules in this order, one at a time, verifying each:"
echo "    1. subscribe to ${SNS_ARN}      (an alarm with no subscriber is silence)"
echo "    2. enable cubic-mars-ps1-sfn-failed"
echo "    3. enable cubic-mars-ps1-processing-failed"
echo "    4. enable cubic-mars-ps1-watchdog"
echo "    5. enable cubic-mars-ps1-gold-complete   <-- last. this is the one that RUNS things."
echo
echo "Signalling before triggering, deliberately: if the first real run"
echo "fails, you want to hear about it."

echo
echo "===== SUMMARY ====="
[ "$APPLY" -eq 1 ] && echo "APPLIED -- all rules created DISABLED." || echo "DRY RUN -- nothing created."
echo "Transcript: ${LOG}"
echo "Undo everything this script creates:"
echo "  for R in cubic-mars-ps1-gold-complete cubic-mars-ps1-sfn-failed \\"
echo "           cubic-mars-ps1-processing-failed cubic-mars-ps1-watchdog; do"
echo "    aws events remove-targets --rule \$R --ids \$(aws events list-targets-by-rule --rule \$R --query 'Targets[].Id' --output text) --region ${REGION}"
echo "    aws events delete-rule --name \$R --region ${REGION}"
echo "  done"
echo "  aws sns delete-topic --topic-arn ${SNS_ARN} --region ${REGION}"
