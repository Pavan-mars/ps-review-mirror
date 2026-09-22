#!/usr/bin/env bash
# =====================================================================
# EventBridge Scheduler trigger for the PS2 serial-grain Processing job.
#
# WHAT THIS COMPLETES. PS2 has two producers writing to chicago/ps2_outputs.
# The patterns notebook runs as a Databricks task (medallion_ps2_daily, 06:45
# America/Chicago, PAUSED). The serial-grain notebook had no schedule at all,
# which is why its 27 tables sat at computed_date 2026-07-26 while its
# sibling's 20 reached 2026-08-29 -- both on one dashboard tab, unlabelled.
# This is that missing half.
#
# CREATED DISABLED, DELIBERATELY. Three measured reasons:
#
#   1. computed_date must follow the DATA, not the calendar. The notebook
#      refuses to publish when PS2SG_COMPUTED_DATE disagrees with the source
#      maximum -- that guard exists because a run on 26-Jul once published
#      April data under a July label. A fixed date in a schedule republishes
#      the same day forever; no date at all falls back to today and is
#      correctly refused. Neither is a working daily job.
#
#   2. Upstream is frozen. Gold ends 2026-08-29 and Oracle has not moved since
#      11-Apr; ServiceNow stops at 30-May. Until the incremental feed lands
#      there is nothing new to analyse, and an enabled schedule would burn an
#      r7i every morning to republish an identical answer.
#
#   3. Its sibling is PAUSED. Enabling this one alone re-opens the vintage
#      split from the other direction.
#
# WHY A SCHEDULER SCHEDULE AND NOT AN EVENTS RULE. The universal target can
# call sagemaker:CreateProcessingJob directly, so no intermediate Lambda is
# needed. It also supplies <aws.scheduler.scheduled-time>, which is what keeps
# the job NAME unique -- SageMaker keeps processing-job names forever, failed
# ones included, so a fixed name works exactly once and then fails with
# ResourceInUseException. PS3's batch transform lost a day to that.
#
# ENABLE ORDER, when the incremental feed lands:
#   1. Set PS2SG_COMPUTED_DATE below to the new gold maximum (or replace this
#      schedule with one that derives it -- see THE DATE PROBLEM at the end).
#   2. Unpause medallion_ps2_daily, let it run, confirm the 20 ps2_v25_*
#      tables reach the new date.
#   3. Run this job once by hand:
#        python sagemaker/ps2/processing/run_processing_job.py --wait ...
#      and confirm 27 tables at the new computed_date.
#   4. Invoke cubic-mars-ps2-rds-loader; confirm 47 loaded, 0 refused, and
#      that the tie-break log line names the new run_id.
#   5. Only then:  aws scheduler update-schedule --name "$SCHED" --state ENABLED
#
# Idempotent: re-running updates the schedule in place. It never enables one.
# =====================================================================
set -euo pipefail
REGION=us-east-1
SCHED=cubic-mars-ps2-serialgrain-daily
ROLE_NAME=cubic-mars-ps2-scheduler-invoke-dev
EXEC_ROLE=cubic-mars-role-sagemaker-exec-dev
IMAGE_TAG=v1
# 06:55 America/Chicago: ten minutes after medallion_ps2_daily's 06:45 slot so
# the two producers land in the same window, and before the 07:10 UTC loader.
SCHEDULE_EXPR="cron(55 6 * * ? *)"
TZ_ID="America/Chicago"

# The as-of date the run will claim. See THE DATE PROBLEM below -- this is the
# single line that has to change every time upstream moves, and the reason
# this schedule is not yet a solution to daily operation.
COMPUTED_DATE=2026-08-29

ACCT=$(aws sts get-caller-identity --query Account --output text)
BUCKET=cubic-mars-pm-s3-datalake-dev-artifacts-170202974600
IMAGE="$ACCT.dkr.ecr.$REGION.amazonaws.com/cubic-mars-ps2-processing:$IMAGE_TAG"

echo ">> [1/5] the image the schedule would run must exist"
aws ecr describe-images --repository-name cubic-mars-ps2-processing \
  --image-ids imageTag="$IMAGE_TAG" --region "$REGION" \
  --query 'imageDetails[0].{tag:imageTags[0],pushed:imagePushedAt}' --output text

echo ">> [2/5] the notebook must already be staged where the job will read it"
echo "   NOTE: a scheduled run reads a FIXED S3 prefix. run_processing_job.py"
echo "   uploads per job name, so the schedule needs its own stable copy:"
echo "     s3://$BUCKET/chicago/ps2/processing_code/scheduled/"
aws s3 ls "s3://$BUCKET/chicago/ps2/processing_code/scheduled/" || {
  echo "   NOT STAGED. Publish it before enabling:"
  echo "     aws s3 cp notebooks/ps2_cascading_failure/PS2_Serial_Grain_Analysis_v1_FIXED.ipynb \\"
  echo "       s3://$BUCKET/chicago/ps2/processing_code/scheduled/"
  echo "   (continuing -- the schedule is created disabled either way)"
}

echo ">> [3/5] role the scheduler assumes to call SageMaker"
if ! aws iam get-role --role-name "$ROLE_NAME" >/dev/null 2>&1; then
  aws iam create-role --role-name "$ROLE_NAME" \
    --assume-role-policy-document '{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"scheduler.amazonaws.com"},"Action":"sts:AssumeRole"}]}' \
    --description "EventBridge Scheduler -> CreateProcessingJob for the PS2 serial-grain notebook" >/dev/null
  echo "   created $ROLE_NAME"
else
  echo "   $ROLE_NAME exists"
fi
# CreateProcessingJob plus PassRole on the execution role only -- the schedule
# must not be able to hand SageMaker any other identity.
aws iam put-role-policy --role-name "$ROLE_NAME" --policy-name ps2-serialgrain-submit \
  --policy-document "{\"Version\":\"2012-10-17\",\"Statement\":[
    {\"Effect\":\"Allow\",\"Action\":\"sagemaker:CreateProcessingJob\",\"Resource\":\"arn:aws:sagemaker:$REGION:$ACCT:processing-job/cubic-mars-ps2-serialgrain-*\"},
    {\"Effect\":\"Allow\",\"Action\":\"iam:PassRole\",\"Resource\":\"arn:aws:iam::$ACCT:role/$EXEC_ROLE\",
     \"Condition\":{\"StringEquals\":{\"iam:PassedToService\":\"sagemaker.amazonaws.com\"}}}]}"
echo "   policy attached"

echo ">> [4/5] create/update the schedule, DISABLED"
# <aws.scheduler.scheduled-time> makes the job name unique per firing.
aws scheduler create-schedule --name "$SCHED" --region "$REGION" \
  --schedule-expression "$SCHEDULE_EXPR" \
  --schedule-expression-timezone "$TZ_ID" \
  --state DISABLED \
  --flexible-time-window '{"Mode":"OFF"}' \
  --target "{
    \"Arn\":\"arn:aws:scheduler:::aws-sdk:sagemaker:createProcessingJob\",
    \"RoleArn\":\"arn:aws:iam::$ACCT:role/$ROLE_NAME\",
    \"Input\":\"{\\\"ProcessingJobName\\\":\\\"cubic-mars-ps2-serialgrain-<aws.scheduler.scheduled-time>\\\",\\\"RoleArn\\\":\\\"arn:aws:iam::$ACCT:role/$EXEC_ROLE\\\",\\\"AppSpecification\\\":{\\\"ImageUri\\\":\\\"$IMAGE\\\"},\\\"ProcessingResources\\\":{\\\"ClusterConfig\\\":{\\\"InstanceCount\\\":1,\\\"InstanceType\\\":\\\"ml.r7i.2xlarge\\\",\\\"VolumeSizeInGB\\\":50}},\\\"ProcessingInputs\\\":[{\\\"InputName\\\":\\\"notebook\\\",\\\"S3Input\\\":{\\\"S3Uri\\\":\\\"s3://$BUCKET/chicago/ps2/processing_code/scheduled\\\",\\\"LocalPath\\\":\\\"/opt/ml/processing/input/notebook\\\",\\\"S3DataType\\\":\\\"S3Prefix\\\",\\\"S3InputMode\\\":\\\"File\\\"}}],\\\"ProcessingOutputConfig\\\":{\\\"Outputs\\\":[{\\\"OutputName\\\":\\\"executed-notebook\\\",\\\"S3Output\\\":{\\\"S3Uri\\\":\\\"s3://$BUCKET/chicago/ps2/processing_runs/scheduled\\\",\\\"LocalPath\\\":\\\"/opt/ml/processing/output\\\",\\\"S3UploadMode\\\":\\\"EndOfJob\\\"}}]},\\\"Environment\\\":{\\\"PS2SG_COMPUTED_DATE\\\":\\\"$COMPUTED_DATE\\\",\\\"AWS_DEFAULT_REGION\\\":\\\"$REGION\\\",\\\"AWS_REGION\\\":\\\"$REGION\\\"},\\\"StoppingCondition\\\":{\\\"MaxRuntimeInSeconds\\\":5400}}\"
  }" 2>/dev/null \
  || aws scheduler update-schedule --name "$SCHED" --region "$REGION" \
       --schedule-expression "$SCHEDULE_EXPR" \
       --schedule-expression-timezone "$TZ_ID" \
       --state DISABLED \
       --flexible-time-window '{"Mode":"OFF"}' \
       --target "$(aws scheduler get-schedule --name "$SCHED" --region "$REGION" --query Target --output json)"

echo ">> [5/5] verify it exists and is DISABLED"
aws scheduler get-schedule --name "$SCHED" --region "$REGION" \
  --query '{name:Name,state:State,expr:ScheduleExpression,tz:ScheduleExpressionTimezone}' --output table

cat <<'NOTE'

THE DATE PROBLEM -- read before enabling.

PS2SG_COMPUTED_DATE is baked into the schedule as a literal. That is honest
while upstream is frozen (the answer genuinely does not change) and WRONG the
moment it moves: every firing would republish the same date under a new
run_id, and the loader's tie-break would dutifully pick the newest of a series
of identical answers.

A daily schedule therefore needs the date DERIVED, not declared. Two ways:

  (a) A small submitter Lambda that reads the gold maximum -- one
      list_objects_v2 on chicago/gold/device_ps2_chains -- and calls
      CreateProcessingJob with it. The schedule then targets the Lambda.
      This is the same shape as every other loader here.

  (b) A first cell in the notebook that derives the date itself and drops
      PS2SG_COMPUTED_DATE to an override. That moves the guard's authority
      into the thing being guarded, which is why it was not done that way.

(a) is the right answer and is NOT built. Until it is, this schedule is a
placeholder that records the intent, the IAM and the unique-name mechanism --
not a working daily job.
NOTE
