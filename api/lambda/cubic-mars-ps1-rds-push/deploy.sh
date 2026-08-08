#!/usr/bin/env bash
# =====================================================================
# cubic-mars-ps1-rds-push  —  deploy from AWS CloudShell (us-east-1)
# S3 (gold parquet) -> this Lambda -> Aurora ps1_* tables -> dashboard-api
# Idempotent. Resource IDs mirror api/lambda/cubic-mars-dashboard-api/deploy.sh.
# =====================================================================
set -euo pipefail
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
echo ">> [3/7] AWS SDK for pandas layer (supplies pyarrow)"
AWS_SDK_PANDAS_ACCT=336392948345
LAYER_ARN=""; RUNTIME=""
for PYV in 312 311; do
  CAND=$(aws lambda list-layer-versions \
           --layer-name arn:aws:lambda:$REGION:$AWS_SDK_PANDAS_ACCT:layer:AWSSDKPandas-Python$PYV \
           --region $REGION --query 'LayerVersions[0].LayerVersionArn' \
           --output text 2>/dev/null || echo None)
  if [ -n "$CAND" ] && [ "$CAND" != "None" ]; then
    LAYER_ARN="$CAND"; RUNTIME="python3.${PYV:1}"; break
  fi
done
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
aws events put-rule --name $RULE --schedule-expression "cron(15 6 * * ? *)" \
  --description "Daily PS1 gold -> Aurora push" --state ENABLED >/dev/null
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
aws s3api put-bucket-notification-configuration --bucket $GOLD_BUCKET \
  --notification-configuration file:///tmp/notif.json 2>/dev/null \
  && echo "   S3 trigger set" \
  || echo "   [warn] S3 notification not set (existing config would be overwritten, or no permission) — the EventBridge schedule still works"

echo
echo "DONE. Smoke test:"
echo "  aws lambda invoke --function-name $FN --cli-binary-format raw-in-base64-out \\"
echo "    --payload '{\"dry_run\":true}' /tmp/ps1.json && cat /tmp/ps1.json | head -c 1500"
