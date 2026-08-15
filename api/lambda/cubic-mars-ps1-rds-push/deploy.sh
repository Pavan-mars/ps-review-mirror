#!/usr/bin/env bash
# =====================================================================
# cubic-mars-ps1-rds-push  —  deploy from AWS CloudShell (us-east-1)
# S3 (gold parquet) -> this Lambda -> Aurora ps1_* tables -> dashboard-api
# Idempotent. Resource IDs mirror api/lambda/cubic-mars-dashboard-api/deploy.sh.
# =====================================================================
set -euo pipefail

# =====================================================================
#  PATH B IS DISABLED. THIS SCRIPT WILL NOT RUN WITHOUT AN EXPLICIT OPT-IN.
#  Added 2026-08-10.
#
#  WHY THIS GUARD EXISTS. On 10-Aug-2026 at 11:55Z the PS1 legacy path was
#  deliberately switched off: cubic-mars-ps1-daily-push was DISABLED and this
#  function was throttled to reserved concurrency 0. The decision is recorded
#  in docs/CUBIC_CHICAGO_HANDOVER_10Aug2026.md section 8.
#
#  This script used to undo half of that silently. Two lines did it:
#     step 6   put-rule ... --state ENABLED     <- re-enables the daily cron
#     step 7   put-bucket-notification-config   <- re-arms the S3 trigger
#  Anyone running this for an unrelated reason -- to fix the pandas layer, to
#  redeploy after a code change -- would have walked away believing they had
#  changed nothing, while the console showed the rule ENABLED again.
#
#  Reserved concurrency 0 would still have throttled the invocations, so no
#  bad data would have loaded. That is luck, not design, and it depends on a
#  second setting this script never checks.
#
#  TO REVIVE PATH B ON PURPOSE:  PATHB_REVIVE=1 bash deploy.sh
#  Reviving also requires restoring concurrency by hand -- see the footer.
# =====================================================================
if [ "${PATHB_REVIVE:-0}" != "1" ]; then
  cat <<'REFUSE'
REFUSING TO RUN.

  cubic-mars-ps1-rds-push is the PS1 LEGACY loader (Path B). It was disabled
  on 2026-08-10 at 11:55Z. Running this script re-enables its EventBridge rule
  and re-arms the S3 trigger on the gold bucket.

  Path A -- cubic-mars-ps1-xw-loader -- is the live PS1 loader and is
  unaffected by this script. If you came here to fix PS1, you almost certainly
  want that one instead.

  If you genuinely intend to bring Path B back:

      PATHB_REVIVE=1 bash deploy.sh

  Read docs/PS1_STATUS_10Aug2026_EOD.md section 2 first. Path B has never
  once held all three fleets and holds zero GATE rows.
REFUSE
  exit 2
fi
echo "!! PATHB_REVIVE=1 -- deliberately reviving the PS1 legacy path"

REGION=us-east-1
FN=cubic-mars-ps1-rds-push
ROLE=cubic-mars-ps1-rds-push-role-dev
SGNAME=cubic-mars-dashboard-api-sg            # reuse: same VPC egress needs
VPC=vpc-0a7775adc7d382fbb
SUBNETS="subnet-0830633f6cab1b8a1 subnet-01ad20b3b49bf59de subnet-0cd6e4bca78eaab8d"
RDS_HOST=cubic-mars-rds-aurora-dev.cluster-cgdk4y4ewxzi.us-east-1.rds.amazonaws.com
SECRET_NAME=cubic-mars-secret-rds-dev
DB_NAME="${DB_NAME:-postgres}"
GOLD_BUCKET=cubic-mars-pm-s3-datalake-dev-gold-170202974600
GOLD_KEY=chicago/gold/device_ps1_cross_wired_daily

ACCT=$(aws sts get-caller-identity --query Account --output text)
SECRET_ARN=$(aws secretsmanager describe-secret --secret-id "$SECRET_NAME" --query ARN --output text)
echo ">> Account=$ACCT Region=$REGION DB=$DB_NAME"

echo ">> [1/7] IAM role"
if ! aws iam get-role --role-name "$ROLE" >/dev/null 2>&1; then
  aws iam create-role --role-name "$ROLE" \
    --assume-role-policy-document '{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"lambda.amazonaws.com"},"Action":"sts:AssumeRole"}]}' >/dev/null
  NEW=1
fi
aws iam attach-role-policy --role-name "$ROLE" --policy-arn arn:aws:iam::aws:policy/service-role/AWSLambdaVPCAccessExecutionRole
aws iam put-role-policy --role-name "$ROLE" --policy-name ps1-push-inline --policy-document "{
  \"Version\":\"2012-10-17\",\"Statement\":[
    {\"Effect\":\"Allow\",\"Action\":\"secretsmanager:GetSecretValue\",\"Resource\":\"$SECRET_ARN\"},
    {\"Effect\":\"Allow\",\"Action\":[\"s3:GetObject\",\"s3:ListBucket\"],
     \"Resource\":[\"arn:aws:s3:::$GOLD_BUCKET\",\"arn:aws:s3:::$GOLD_BUCKET/chicago/gold/*\"]}]}"
ROLE_ARN=arn:aws:iam::$ACCT:role/$ROLE

echo ">> [2/7] security group"
SG=$(aws ec2 describe-security-groups --filters Name=group-name,Values=$SGNAME Name=vpc-id,Values=$VPC \
     --query "SecurityGroups[0].GroupId" --output text 2>/dev/null || true)
[ "$SG" = "None" ] && SG=""
if [ -z "$SG" ]; then
  SG=$(aws ec2 create-security-group --group-name $SGNAME --description "cubic-mars lambda egress" --vpc-id $VPC --query GroupId --output text)
fi
echo "   SG=$SG"

# 26-Jul-2026 FIX. `list-layer-versions --layer-name AWSSDKPandas-Python312` lists
# layers OWNED BY THIS ACCOUNT. AWSSDKPandas is published by AWS under account
# 336392948345, so both lookups returned None and the deploy then sent the literal
# string "None" to --layers:
#     ValidationException: Value '[None]' at 'layers' failed to satisfy constraint
# That is what killed step 3/6 of deploy_e2e.sh on 26-Jul. Passing the FULL ARN as
# --layer-name resolves a layer owned by another account, and an unresolved layer
# now aborts instead of deploying something broken.
# 10-Aug-2026 FIX. The 26-Jul change above passed the full ARN as --layer-name,
# but list-layer-versions STILL fails on another account's layer: it needs
# lambda:ListLayerVersions on that resource, which AWS's public layers do not
# grant. So this script could never resolve the layer and always hit the abort
# below -- while ps2, ps3, ps4, ps1-xw and dim all deploy fine, because they use
# get-layer-version-by-arn, which public layers DO allow. PS1 was the only
# loader using list-layer-versions alone, and the only one that could not deploy.
#
# Resolve by ARN first (pinned to :29, the version every other deployed loader
# is running, so PS1 does not silently drift onto a different one), then fall
# back to the newest version, then to the old list call. LAYER_ARN can be set in
# the environment to override all of it.
echo ">> [3/7] AWS SDK for pandas layer (supplies pyarrow)"
AWS_SDK_PANDAS_ACCT=336392948345
PINNED_LAYER_VERSION=${PINNED_LAYER_VERSION:-29}
LAYER_ARN="${LAYER_ARN:-}"; RUNTIME="${RUNTIME:-}"
if [ -n "$LAYER_ARN" ]; then
  RUNTIME="${RUNTIME:-python3.12}"
  echo "   using LAYER_ARN from the environment"
else
  for PYV in 312 311; do
    BASE=arn:aws:lambda:$REGION:$AWS_SDK_PANDAS_ACCT:layer:AWSSDKPandas-Python$PYV
    if aws lambda get-layer-version-by-arn --arn "$BASE:$PINNED_LAYER_VERSION" \
         --region $REGION --query LayerVersionArn --output text >/dev/null 2>&1; then
      LAYER_ARN="$BASE:$PINNED_LAYER_VERSION"; RUNTIME="python3.${PYV:1}"; break
    fi
    CAND=$(aws lambda list-layer-versions --layer-name "$BASE" \
             --region $REGION --query 'LayerVersions[0].LayerVersionArn' \
             --output text 2>/dev/null || echo None)
    if [ -n "$CAND" ] && [ "$CAND" != "None" ]; then
      LAYER_ARN="$CAND"; RUNTIME="python3.${PYV:1}"; break
    fi
  done
fi
if [ -z "$LAYER_ARN" ]; then
  echo "!! Could not resolve the AWSSDKPandas layer in $REGION -- refusing to deploy."
  exit 1
fi
echo "   $LAYER_ARN  ($RUNTIME)"

echo ">> [4/7] package (handler + pg8000 only)"
rm -rf build fn.zip && mkdir build && cp handler.py build/
pip3 install "pg8000>=1.31,<2" -t build/ -q
(cd build && zip -qr ../fn.zip .)
echo "   $(du -h fn.zip | cut -f1)"

echo ">> [5/7] create/update Lambda"
SUBNET_CSV=$(echo $SUBNETS | tr ' ' ',')
ENVV="Variables={RDS_SECRET_ID=$SECRET_NAME,RDS_HOST=$RDS_HOST,RDS_DATABASE=$DB_NAME,GOLD_BUCKET=$GOLD_BUCKET,GOLD_KEY=$GOLD_KEY,CITY_ID=CHI}"

# --- ADDITIVE ENVIRONMENT (08-Aug-2026) ------------------------------------
# `--environment "Variables={...}"` REPLACES the function's entire environment
# map. Any variable set outside this script -- a prefix override, a feature
# flag, a tuning knob -- was therefore erased on the next deploy, silently.
# The symptom surfaces days later as a loader reading the wrong prefix, or a
# disabled code path switching itself back on.
#
# This block reads the live environment and merges the values below ON TOP of
# it: this script wins for the keys it owns, every other key survives. On a
# first create there is no live config and the merge is a no-op.
#
# The JSON file form is deliberate -- the Variables={k=v,...} shorthand cannot
# express a value containing a comma or an equals sign.
ENVFILE="$(mktemp /tmp/lambda-env-XXXXXX.json)"
aws lambda get-function-configuration --function-name "$FN" \
    --query 'Environment.Variables' --output json 2>/dev/null > "$ENVFILE.live" || true
[ -s "$ENVFILE.live" ] || echo '{}' > "$ENVFILE.live"
cat > "$ENVFILE.py" <<'PYMERGE'
import json, sys
live_path, desired, out_path = sys.argv[1], sys.argv[2], sys.argv[3]
try:
    live = json.load(open(live_path))
    if not isinstance(live, dict):
        live = {}
except Exception:
    live = {}
inner = desired.strip()
if inner.startswith("Variables={") and inner.endswith("}"):
    inner = inner[len("Variables={"):-1]
new = {}
for pair in inner.split(","):
    if "=" in pair:
        k, v = pair.split("=", 1)
        if k.strip():
            new[k.strip()] = v
merged = dict(live)
merged.update(new)
kept = sorted(set(live) - set(new))
if kept:
    print("   preserved %d pre-existing env var(s): %s" % (len(kept), ", ".join(kept)))
json.dump({"Variables": merged}, open(out_path, "w"))
PYMERGE
python3 "$ENVFILE.py" "$ENVFILE.live" "$ENVV" "$ENVFILE"
ENVOPT="file://$ENVFILE"
# ---------------------------------------------------------------------------

if aws lambda get-function --function-name "$FN" >/dev/null 2>&1; then
  aws lambda update-function-code --function-name "$FN" --zip-file fileb://fn.zip >/dev/null
  aws lambda wait function-updated --function-name "$FN"
  aws lambda update-function-configuration --function-name "$FN" --timeout 600 --memory-size 2048 \
    --layers "$LAYER_ARN" --vpc-config "SubnetIds=$SUBNET_CSV,SecurityGroupIds=$SG" --environment "$ENVOPT" >/dev/null
else
  [ "${NEW:-0}" = "1" ] && sleep 12
  aws lambda create-function --function-name "$FN" --runtime $RUNTIME --handler handler.lambda_handler \
    --role "$ROLE_ARN" --zip-file fileb://fn.zip --timeout 600 --memory-size 2048 \
    --layers "$LAYER_ARN" --vpc-config "SubnetIds=$SUBNET_CSV,SecurityGroupIds=$SG" --environment "$ENVOPT" >/dev/null
fi
aws lambda wait function-updated --function-name "$FN"
echo "   Lambda ready"

echo ">> [6/7] EventBridge daily schedule (06:15 UTC)"
RULE=cubic-mars-ps1-daily-push
# 2026-08-10. Was --state ENABLED, which silently undid the 11:55Z disable.
# The rule is still CREATED so the wiring is complete and reviewable, but it is
# created switched OFF. Turning it on is now a separate, deliberate, auditable
# act rather than a side effect of running a deploy script.
RULE_STATE=DISABLED
[ "${PATHB_ENABLE_SCHEDULE:-0}" = "1" ] && RULE_STATE=ENABLED
aws events put-rule --name $RULE --schedule-expression "cron(15 6 * * ? *)" \
  --description "Daily PS1 gold -> Aurora push (DISABLED 2026-08-10, Path B retired)" \
  --state $RULE_STATE >/dev/null
echo "   rule state: $RULE_STATE  (PATHB_ENABLE_SCHEDULE=1 to arm the cron)"
aws lambda add-permission --function-name "$FN" --statement-id ${RULE}-invoke \
  --action lambda:InvokeFunction --principal events.amazonaws.com \
  --source-arn arn:aws:events:$REGION:$ACCT:rule/$RULE >/dev/null 2>&1 || true
# 27-Jul-2026. Shorthand syntax cannot express this. In
#     --targets "Id=1,Arn=...,Input={}"
# the CLI parses {} as a nested STRUCTURE and rejects it:
#     Invalid type for parameter Targets[0].Input, value: {}, type: <class 'dict'>,
#     valid types: <class 'str'>
# EventBridge's Input is a JSON STRING containing the event, so the braces have to
# survive as literal characters. JSON syntax with the payload quoted is the only
# form that does that.
aws events put-targets --rule $RULE \
  --targets "[{\"Id\":\"1\",\"Arn\":\"arn:aws:lambda:$REGION:$ACCT:function:$FN\",\"Input\":\"{}\"}]" >/dev/null
echo "   rule=$RULE"

echo ">> [7/7] S3 ObjectCreated trigger on the gold prefix"
aws lambda add-permission --function-name "$FN" --statement-id s3-invoke-ps1 \
  --action lambda:InvokeFunction --principal s3.amazonaws.com \
  --source-arn arn:aws:s3:::$GOLD_BUCKET --source-account $ACCT >/dev/null 2>&1 || true
cat > /tmp/notif.json <<JSON
{"LambdaFunctionConfigurations":[{
  "Id":"ps1-cross-wired-push",
  "LambdaFunctionArn":"arn:aws:lambda:$REGION:$ACCT:function:$FN",
  "Events":["s3:ObjectCreated:*"],
  "Filter":{"Key":{"FilterRules":[{"Name":"prefix","Value":"chicago/gold/device_ps1_cross_wired"}]}}}]}
JSON
# 2026-08-10. This overwrites the ENTIRE bucket notification configuration --
# put-bucket-notification-configuration is a REPLACE, not an append -- and it
# re-arms a trigger on a path that feeds the retired loader. It is now opt-in.
if [ "${PATHB_ARM_S3_TRIGGER:-0}" = "1" ]; then
  echo "   [!] replacing the gold bucket notification configuration"
  aws s3api put-bucket-notification-configuration --bucket $GOLD_BUCKET \
    --notification-configuration file:///tmp/notif.json 2>/dev/null \
    && echo "   S3 trigger set" \
    || echo "   [warn] S3 notification not set (no permission, or existing config)"
else
  echo "   S3 trigger NOT armed (PATHB_ARM_S3_TRIGGER=1 to arm it)"
  echo "   Existing configuration on $GOLD_BUCKET left exactly as it is."
fi

echo
echo "DONE. Smoke test:"
echo "  aws lambda invoke --function-name $FN --cli-binary-format raw-in-base64-out \\"
echo "    --payload '{\"dry_run\":true}' /tmp/ps1.json && cat /tmp/ps1.json | head -c 1500"

# =====================================================================
#  IF YOU REVIVED PATH B, YOU ARE NOT DONE.
#
#  This script does NOT touch reserved concurrency, on purpose -- it is the
#  last brake and it should not be released by a deploy script either. After
#  PATHB_REVIVE=1, the function is deployed but still throttled to 0. To
#  actually let it run:
#
#      aws lambda put-function-concurrency --function-name cubic-mars-ps1-rds-push \
#        --reserved-concurrent-executions 1 --region us-east-1
#
#  It was 1 before the disable, NOT unset. Do not use
#  delete-function-concurrency -- that removes the reservation entirely and is
#  not the state this function was in.
#
#  And before any of that, satisfy yourself that Path B has a data source at
#  all. As of 2026-08-10 NOTHING IN THIS REPOSITORY WRITES
#  chicago/gold/device_ps1_cross_wired_daily. cross_wired_daily_job.py only
#  READS it, as a legacy fallback. A revived loader with no producer will load
#  whatever stale object is still sitting there.
# =====================================================================
