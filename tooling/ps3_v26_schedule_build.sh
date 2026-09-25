#!/usr/bin/env bash
# =====================================================================
# EventBridge Scheduler trigger for the PS3 V26 Processing job, plus the
# loader rule that has never existed.
#
# WHAT THIS COMPLETES. PS3's chain is manual at BOTH ends. The producer
# (PS3_V26_PRODUCTION.ipynb) is run by hand in the mars-train-ps3 Studio
# space, and cubic-mars-ps3-v25-loader is invoked by hand afterwards --
# V4Shell.jsx:419 says so on the screen. The two PAUSED databricks.yml jobs
# named "PS3 daily pre-score" and "PS3 export + event only" are a DIFFERENT
# path: they feed the batch-transform prefix, which no dashboard route reads.
# Scheduling those would not refresh the tab. This does the two pieces that
# would.
#
# CREATED DISABLED, DELIBERATELY. Four measured reasons:
#
#   1. There is no correct value for PS3_DATA_AS_OF_DATE today. Unset, the
#      notebook's 2026-04-11 default applies and a daily job republishes April
#      forever -- which is what 454be72 and the 24-Sep config guard exist to
#      stop. Fixed at 2026-08-29, every firing republishes an identical answer
#      under a fresh run_id. Derived from today, the as-of gate raises on every
#      run: the source stops at 2026-08-29, ASOF_MAX_LEAD_DAYS is 3, and the
#      calendar is already 26 days past. None of the three is a working job.
#
#   2. Upstream is frozen. Gold ends 2026-08-29; Oracle has not moved since
#      11-Apr; ServiceNow stops at 30-May. There is nothing new to analyse.
#
#   3. The PS3 evidence lanes cannot be read from any runtime we have. They
#      are configured correctly and return "not_ready -- Spark is unavailable":
#      the OOS spine is read with pyarrow and the Studio space has no Java,
#      while the optional lanes go through Spark. The Processing image DOES
#      carry pyspark, so a scheduled run may behave differently from every
#      hand run to date. That difference has never been observed and must be,
#      once, before a schedule is trusted with it.
#
#   4. No PS3 Processing job has ever succeeded. Every run so far has been a
#      person in Studio pressing Run All. Enabling a schedule for a path with
#      no successful execution is how a green cron hides a broken chain.
#
# THE RUN_MODE TRAP, specific to PS3.                            24-Sep-2026
# 71b4a6c flipped the notebook's PS3_RUN_MODE default to REPLAY so that opening
# it in Studio and pressing Run All could not reach the prefix the v25 loader
# watches. run_processing_job.py compensates with
# env.setdefault("PS3_RUN_MODE", "PRODUCTION") -- submitting a job IS the intent
# to publish. A Scheduler target does NOT go through that script. So the
# environment block below sets PS3_RUN_MODE explicitly. Omit it and every
# scheduled run lands in REPLAY on an isolated prefix, publishes nothing the
# loader can see, and reports success.
#
# WHY A SCHEDULER SCHEDULE AND NOT AN EVENTS RULE, for the producer. The
# universal target calls sagemaker:CreateProcessingJob directly, so no
# intermediate Lambda is needed, and it supplies <aws.scheduler.execution-id>
# -- a hyphen-safe UUID. SageMaker reserves processing-job names forever,
# failed ones included, so a fixed name works exactly once and then fails with
# ResourceInUseException. PS3's own batch transform lost a day to that.
# <aws.scheduler.scheduled-time> would NOT do: it substitutes an ISO-8601
# instant containing COLONS, and ProcessingJobName is ^[a-zA-Z0-9](-*[a-zA-Z0-9]){0,62}$.
#
# ENABLE ORDER, when the incremental feed lands:
#   1. Confirm the new gold maximum, and set AS_OF_DATE below to it (or
#      replace this with a derived-date submitter -- see THE DATE PROBLEM).
#   2. Stage the current notebook to the scheduled prefix (step 2 below).
#   3. Run the producer ONCE by hand through the Processing job:
#        PS3_DATA_AS_OF_DATE=<new> python sagemaker/ps3/processing/run_processing_job.py \
#          --role-arn ... --image-uri ... --wait
#      Confirm 20 tables at the new computed_date, and READ the evidence audit:
#      reason 3 above is settled by that run, not by argument.
#   4. Invoke cubic-mars-ps3-v25-loader pinned to that run_id; confirm
#      expected 20 / loaded 20 / no_target 0.
#   5. Enable the producer schedule:
#        aws scheduler update-schedule --name "$SCHED" --state ENABLED ...
#   6. Enable the loader rule LAST:
#        aws events enable-rule --name "$LOADER_RULE"
#      Producer first, loader second. The reverse order means the loader fires
#      against whatever the previous run left and reports a clean commit.
#
# Idempotent: re-running updates both in place. It never enables either.
# =====================================================================
set -euo pipefail
REGION=us-east-1
SCHED=cubic-mars-ps3-v26-daily
LOADER_RULE=cubic-mars-ps3-v25-loader-daily
LOADER_FN=cubic-mars-ps3-v25-loader
ROLE_NAME=cubic-mars-ps3-scheduler-invoke-dev
EXEC_ROLE=cubic-mars-role-sagemaker-exec-dev
IMAGE_TAG=v26

# 07:15 America/Chicago. After the 06:00 gold job (databricks.yml, same clock)
# and after PS2's two producers at 06:45 and 06:55, so PS3 reads a settled
# silver export rather than racing it. PS3 does not depend on PS2's output --
# it reads the PS1-compatible silver spine directly -- but sharing one window
# keeps every tab on one vintage, which is the thing that went wrong when the
# PS2 serial notebook had no schedule and drifted five weeks from its sibling.
SCHEDULE_EXPR="cron(15 7 * * ? *)"
TZ_ID="America/Chicago"

# The loader is an EventBridge RULE, and put-rule takes NO timezone parameter
# -- its cron is always UTC. Do not "fix" this to look like the producer's
# local time; they are different clocks.
#
# CORRECTED 24-Sep. The first version claimed 13:45 UTC cleared "the runtime
# cap in both DST regimes". It did not: MAX_RUNTIME_SEC is 10800 = 180 minutes.
#   CDT  producer starts 12:15 UTC, cap expires 15:15 -> 13:45 is  90 min early
#   CST  producer starts 13:15 UTC, cap expires 16:15 -> 13:45 is 150 min early,
#        i.e. only THIRTY minutes after the job starts
# Thirty minutes has to cover provisioning an ml.m5.4xlarge, pulling a ~2 GB
# image and the ~10-minute notebook. On overrun the loader takes 'the first
# COMPLETE run', which is YESTERDAY's, and reports a clean commit -- exactly the
# silent staleness this file's four disabled-by-default reasons exist to stop.
# 16:30 UTC clears 13:15 + 180 min in both regimes.
#
# The real fix is to chain the loader off the producer's COMPLETION rather than
# off a clock; this repo already has the gold-complete EventBridge pattern.
LOADER_CRON="cron(30 16 * * ? *)"

# The as-of date the run will claim. THE single line that must change every
# time upstream moves, and the reason this is not yet daily operation.
AS_OF_DATE=2026-08-29

# MUST match run_processing_job.py's --max-runtime-sec default (10800), so a
# job behaves identically scheduled and by hand. PS2 carried 5400 against a
# 14400 default for a while: the same notebook would be killed at 90 minutes
# on a schedule and allowed four hours from a shell.
# MEASURED: the 24-Sep Studio runs took ~9-10 minutes wall clock on the
# space's own compute. No PROCESSING-job duration has ever been recorded, so
# 10800 stays a generous guess until step 3 of the enable order supplies one.
MAX_RUNTIME_SEC=10800

ACCT=$(aws sts get-caller-identity --query Account --output text)
BUCKET=cubic-mars-pm-s3-datalake-dev-artifacts-170202974600
IMAGE="$ACCT.dkr.ecr.$REGION.amazonaws.com/cubic-mars-ps3-processing:$IMAGE_TAG"

echo ">> [1/6] the image the schedule would run must exist"
aws ecr describe-images --repository-name cubic-mars-ps3-processing \
  --image-ids imageTag="$IMAGE_TAG" --region "$REGION" \
  --query 'imageDetails[0].{tag:imageTags[0],pushed:imagePushedAt}' --output text \
  || echo "   NOT BUILT. Build it before enabling; the schedule is created disabled either way."

echo ">> [2/6] the notebook must be staged where a scheduled run will read it"
echo "   A scheduled run reads a FIXED prefix. run_processing_job.py uploads"
echo "   per job name, so the schedule needs its own stable copy:"
_KEY=chicago/ps3/processing_code/scheduled/PS3_V26_PRODUCTION.ipynb
# The ENTRYPOINT hardcodes .../input/notebook/PS3_V26_PRODUCTION.ipynb and the
# input is an S3Prefix, so exactly ONE object with exactly that basename must sit
# directly under the prefix, unnested. A non-empty prefix is not enough, and the
# prefix without a trailing slash also matches siblings like scheduled-backup/.
if aws s3api head-object --bucket "$BUCKET" --key "$_KEY" >/dev/null 2>&1; then
  echo "   staged: s3://$BUCKET/$_KEY"
  # AND THE STAGED COPY CAN SILENTLY BEAT THE JOB. Cell 2 builds
  # PS3_STUDIO_INTENT from PS3_STUDIO_RUN and does os.environ[k] = v -- a HARD
  # assignment, running BEFORE the setdefault block -- so a copy with that dict
  # filled in overrides the schedule's Environment while the job still reports
  # Completed. The committed file has all five values blank; a Studio working
  # copy usually does not, and that is the copy nearest to hand when staging.
  aws s3 cp "s3://$BUCKET/$_KEY" - 2>/dev/null > /tmp/_ps3nb.json &&
  python3 - /tmp/_ps3nb.json <<'PYCHK'
import json, re, sys
src = "".join(json.load(open(sys.argv[1]))["cells"][2]["source"])
vals = re.findall(r'"(PS3_[A-Z_]+)":\s*"([^"]*)"', src)
bad = [k for k, v in vals if v.strip()]
if bad:
    print("   STOP: the staged notebook hard-sets " + ", ".join(bad))
    print("   It will override the schedule Environment. Stage the committed copy.")
else:
    print("   PS3_STUDIO_RUN all blank -- the job Environment wins, as intended.")
PYCHK
  rm -f /tmp/_ps3nb.json
else
  echo "   NOT STAGED at the exact key the ENTRYPOINT reads. Publish first:"
  echo "     aws s3 cp notebooks/ps3_root_cause_analysis/PS3_V26_PRODUCTION.ipynb \\"
  echo "       s3://$BUCKET/chicago/ps3/processing_code/scheduled/"
  echo "   (continuing -- created disabled either way)"
fi

echo ">> [3/6] role the scheduler assumes to call SageMaker"
if ! aws iam get-role --role-name "$ROLE_NAME" >/dev/null 2>&1; then
  # Trust scoped with aws:SourceArn/SourceAccount to close the confused-deputy
  # path. NOTE: this comment sits ABOVE the command -- a comment line between a
  # trailing backslash and the next argument splices onto the command line and
  # silently truncates it ("the following arguments are required").
  aws iam create-role --role-name "$ROLE_NAME" \
    --assume-role-policy-document "{\"Version\":\"2012-10-17\",\"Statement\":[{\"Effect\":\"Allow\",\"Principal\":{\"Service\":\"scheduler.amazonaws.com\"},\"Action\":\"sts:AssumeRole\",\"Condition\":{\"StringEquals\":{\"aws:SourceAccount\":\"$ACCT\"},\"ArnLike\":{\"aws:SourceArn\":\"arn:aws:scheduler:$REGION:$ACCT:schedule/default/$SCHED\"}}}]}" \
    --description "EventBridge Scheduler -> CreateProcessingJob for the PS3 V26 notebook" >/dev/null
  echo "   created $ROLE_NAME"
else
  echo "   $ROLE_NAME exists"
fi
# CreateProcessingJob plus PassRole on the execution role only -- the schedule
# must not be able to hand SageMaker any other identity.
aws iam put-role-policy --role-name "$ROLE_NAME" --policy-name ps3-v26-submit \
  --policy-document "{\"Version\":\"2012-10-17\",\"Statement\":[
    {\"Effect\":\"Allow\",\"Action\":\"sagemaker:CreateProcessingJob\",\"Resource\":\"arn:aws:sagemaker:$REGION:$ACCT:processing-job/cubic-mars-ps3-v26-*\"},
    {\"Effect\":\"Allow\",\"Action\":\"iam:PassRole\",\"Resource\":\"arn:aws:iam::$ACCT:role/$EXEC_ROLE\",
     \"Condition\":{\"StringEquals\":{\"iam:PassedToService\":\"sagemaker.amazonaws.com\"}}}]}"
echo "   policy attached"

echo ">> [4/6] create/update the producer schedule, DISABLED"
# Rendered ONCE and handed to both branches. PS2's fallback used to pass the
# EXISTING target read back off the schedule, so AS_OF_DATE and IMAGE_TAG were
# inert on every re-run and the header's "set the date and re-run" instruction
# did nothing.
TARGET_JSON="{
    \"Arn\":\"arn:aws:scheduler:::aws-sdk:sagemaker:createProcessingJob\",
    \"RoleArn\":\"arn:aws:iam::$ACCT:role/$ROLE_NAME\",
    \"Input\":\"{\\\"ProcessingJobName\\\":\\\"cubic-mars-ps3-v26-<aws.scheduler.execution-id>\\\",\\\"RoleArn\\\":\\\"arn:aws:iam::$ACCT:role/$EXEC_ROLE\\\",\\\"AppSpecification\\\":{\\\"ImageUri\\\":\\\"$IMAGE\\\"},\\\"ProcessingResources\\\":{\\\"ClusterConfig\\\":{\\\"InstanceCount\\\":1,\\\"InstanceType\\\":\\\"ml.m5.4xlarge\\\",\\\"VolumeSizeInGB\\\":100}},\\\"ProcessingInputs\\\":[{\\\"InputName\\\":\\\"notebook\\\",\\\"S3Input\\\":{\\\"S3Uri\\\":\\\"s3://$BUCKET/chicago/ps3/processing_code/scheduled\\\",\\\"LocalPath\\\":\\\"/opt/ml/processing/input/notebook\\\",\\\"S3DataType\\\":\\\"S3Prefix\\\",\\\"S3InputMode\\\":\\\"File\\\"}}],\\\"ProcessingOutputConfig\\\":{\\\"Outputs\\\":[{\\\"OutputName\\\":\\\"executed-notebook\\\",\\\"S3Output\\\":{\\\"S3Uri\\\":\\\"s3://$BUCKET/chicago/ps3/processing_runs/scheduled-<aws.scheduler.execution-id>\\\",\\\"LocalPath\\\":\\\"/opt/ml/processing/output\\\",\\\"S3UploadMode\\\":\\\"EndOfJob\\\"}}]},\\\"Environment\\\":{\\\"PS3_DATA_AS_OF_DATE\\\":\\\"$AS_OF_DATE\\\",\\\"PS3_RUN_MODE\\\":\\\"PRODUCTION\\\",\\\"AWS_DEFAULT_REGION\\\":\\\"$REGION\\\",\\\"AWS_REGION\\\":\\\"$REGION\\\"},\\\"StoppingCondition\\\":{\\\"MaxRuntimeInSeconds\\\":$MAX_RUNTIME_SEC}}\"
    ,\"RetryPolicy\":{\"MaximumRetryAttempts\":0,\"MaximumEventAgeInSeconds\":300}
  }"

# Scheduler's DEFAULT is 185 attempts over 24 hours, and both readings of that
# are bad. If <aws.scheduler.execution-id> is stable across retries, all 185
# re-submit the same reserved job name and fail ResourceInUseException,
# invisibly. If it is regenerated per attempt, that is up to 185
# ml.m5.4xlarge jobs in a day. Zero retries: a missed run stays a visible one.

# UpdateSchedule and PutRule are FULL REPLACES. Hardcoding DISABLED on the
# update path would mean the lifecycle this file documents -- enable, feed
# moves, change AS_OF_DATE, re-run -- silently switches both back off with no
# message. A NEW schedule is still created DISABLED; an existing one keeps the
# state someone set by hand.
PREV_SCHED_STATE=$(aws scheduler get-schedule --name "$SCHED" --region "$REGION" \
                     --query State --output text 2>/dev/null || echo DISABLED)
echo "   existing schedule state: $PREV_SCHED_STATE (a NEW schedule is created DISABLED)"

# EventBridge Scheduler validates role assumability at create time, and IAM
# propagation takes seconds. Without this wait the first run fails
# ValidationException on create, that error is swallowed by 2>/dev/null, and
# the update fallback then fails ResourceNotFoundException -- two errors,
# neither of which explains the other.
for _ in 1 2 3 4 5 6; do
  aws iam get-role --role-name "$ROLE_NAME" >/dev/null 2>&1 && break
  sleep 5
done
sleep 10

aws scheduler create-schedule --name "$SCHED" --region "$REGION" \
  --schedule-expression "$SCHEDULE_EXPR" \
  --schedule-expression-timezone "$TZ_ID" \
  --state DISABLED \
  --flexible-time-window '{"Mode":"OFF"}' \
  --target "$TARGET_JSON" 2>/dev/null \
  || aws scheduler update-schedule --name "$SCHED" --region "$REGION" \
       --schedule-expression "$SCHEDULE_EXPR" \
       --schedule-expression-timezone "$TZ_ID" \
       --state "$PREV_SCHED_STATE" \
       --flexible-time-window '{"Mode":"OFF"}' \
       --target "$TARGET_JSON"

echo ">> [5/6] create/update the loader rule, DISABLED"
# The loader has never had a schedule. An unpinned invoke lets the loader pick
# the partition itself: it sorts (computed_date, run_id) descending and takes
# the first COMPLETE run, so within one computed_date the winner is decided by
# lexical UUID order. That is survivable for a scheduled daily run -- there is
# one new run per day and it must be complete to be chosen -- but it is the
# same mechanism that made a PS2 re-run a one-in-four coin flip on 23-Sep.
# Pin explicitly whenever invoking by hand.
PREV_RULE_STATE=$(aws events describe-rule --name "$LOADER_RULE" --region "$REGION" \
                    --query State --output text 2>/dev/null || echo DISABLED)
echo "   existing rule state: $PREV_RULE_STATE (a NEW rule is created DISABLED)"
aws events put-rule --name "$LOADER_RULE" --region "$REGION" \
  --schedule-expression "$LOADER_CRON" --state "$PREV_RULE_STATE" \
  --description "Daily PS3 v25 load. DISABLED until the producer schedule is enabled -- see tooling/ps3_v26_schedule_build.sh" >/dev/null
# The old form sent stdout AND stderr to /dev/null and printed "already
# present" on ANY failure -- including ResourceNotFoundException for a renamed,
# deleted or wrong-region function. That reports a permission on a Lambda that
# is not there, and it only surfaces the day someone enables the rule.
aws lambda get-function --function-name "$LOADER_FN" --region "$REGION" >/dev/null 2>&1 \
  || { echo "   STOP: lambda $LOADER_FN not found in $REGION"; exit 1; }
_perm=$(aws lambda add-permission --function-name "$LOADER_FN" --region "$REGION" \
  --statement-id "${LOADER_RULE}-invoke" --action lambda:InvokeFunction \
  --principal events.amazonaws.com \
  --source-arn "arn:aws:events:$REGION:$ACCT:rule/$LOADER_RULE" 2>&1) \
  && echo "   invoke permission added" \
  || case "$_perm" in
       *ResourceConflictException*) echo "   invoke permission already present" ;;
       *) echo "   FAILED: $_perm"; exit 1 ;;
     esac
# Input is the literal {} -- trigger shape 2 in the loader's own header,
# "EventBridge schedule / {} -> newest complete run". JSON form, not shorthand:
# the shorthand parser splits on commas and does not handle {} as a value.
aws events put-targets --rule "$LOADER_RULE" --region "$REGION" \
  --targets "[{\"Id\":\"1\",\"Arn\":\"arn:aws:lambda:$REGION:$ACCT:function:$LOADER_FN\",\"Input\":\"{}\"}]" \
  --query 'FailedEntryCount' --output text

echo ">> [6/6] verify both exist and are DISABLED"
aws scheduler get-schedule --name "$SCHED" --region "$REGION" \
  --query '{name:Name,state:State,expr:ScheduleExpression,tz:ScheduleExpressionTimezone}' --output table
aws events describe-rule --name "$LOADER_RULE" --region "$REGION" \
  --query '{name:Name,state:State,expr:ScheduleExpression}' --output table

cat <<'NOTE'

THE DATE PROBLEM -- read before enabling.

PS3_DATA_AS_OF_DATE is baked into the schedule as a literal. That is honest
while upstream is frozen -- the answer genuinely does not change -- and wrong
the moment it moves: every firing republishes the same date under a new
run_id, and the loader dutifully picks the newest of a series of identical
answers.

A daily schedule needs the date DERIVED, not declared, and PS3 cannot derive
it from inside the notebook. ps3v21_observed_source_max reads the maximum from
facts ALREADY FILTERED to the declared window, so it can catch a date running
ahead of the data and cannot discover one behind it. That asymmetry is
deliberate and documented; it also means the notebook can never widen its own
window.

So the fix has to sit OUTSIDE:

  (a) A small submitter Lambda that reads the silver maximum before calling
      CreateProcessingJob with it. The schedule targets the Lambda instead of
      SageMaker directly. Same shape as every other loader here.

  (b) A cheap date probe published by whatever refreshes silver, which the
      schedule reads. Better, because it makes the vintage a fact the platform
      states rather than one each consumer rediscovers.

Neither is built. Until one is, this schedule records the intent, the IAM, the
unique-name mechanism and the run-mode fix -- it is not a working daily job,
and enabling it would publish 2026-08-29 every morning.
NOTE
