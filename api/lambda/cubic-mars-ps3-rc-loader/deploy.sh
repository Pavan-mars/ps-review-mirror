#!/usr/bin/env bash
# =====================================================================
# cubic-mars-ps3-rc-loader  --  deploy from AWS CloudShell (us-east-1)
#
#   s3://<artifacts>/chicago/ps3/rootcause_outputs/<run_id>/  ->  THIS LAMBDA
#   ->  Aurora ps3_model_runs / ps3_head_summary / ps3_device_predictions
#   ->  dashboard-api
#
# This replaces the path where the PS3 root-cause run reached Aurora as a
# 797 KB file of INSERTs baked into the dashboard-api's deployment package.
# The dashboard-api is UNTOUCHED by this deploy; its sql/load path still
# works, so rolling back is "stop invoking this Lambda".
#
# NO AWSSDKPandas layer. The artifacts are CSV, read with the stdlib csv
# module, so there is no pyarrow dependency -- which removes the
# cross-account ListLayerVersions probe that cost several rounds on the PS2
# and PS4 deploys.
#
# THE TABLES MUST EXIST FIRST. This loader has no DDL and cannot create them.
# sql/15_phase2a_ps3_two_head.sql already did, on 26-Jul. If a fresh
# environment needs them:
#   aws lambda invoke --function-name cubic-mars-dashboard-api \
#     --cli-binary-format raw-in-base64-out --payload '{"action":"migrate"}' /dev/null
# =====================================================================
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"; cd "$SCRIPT_DIR"
REGION=us-east-1
FN=cubic-mars-ps3-rc-loader
ROLE=cubic-mars-ps3-rc-loader-role-dev
SG_NAME=cubic-mars-dashboard-api-sg
VPC=vpc-0a7775adc7d382fbb
SUBNETS="subnet-0830633f6cab1b8a1 subnet-01ad20b3b49bf59de subnet-0cd6e4bca78eaab8d"
RDS_HOST=cubic-mars-rds-aurora-dev.cluster-cgdk4y4ewxzi.us-east-1.rds.amazonaws.com
SECRET_NAME=cubic-mars-secret-rds-dev
ARTIFACTS=cubic-mars-pm-s3-datalake-dev-artifacts-170202974600
RC_PREFIX=chicago/ps3/rootcause_outputs
ACCT=$(aws sts get-caller-identity --query Account --output text)
echo ">> Account=$ACCT Region=$REGION"

echo ">> [1/5] IAM role"
aws iam get-role --role-name "$ROLE" >/dev/null 2>&1 || aws iam create-role --role-name "$ROLE" \
  --assume-role-policy-document '{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"lambda.amazonaws.com"},"Action":"sts:AssumeRole"}]}' >/dev/null
aws iam attach-role-policy --role-name "$ROLE" \
  --policy-arn arn:aws:iam::aws:policy/service-role/AWSLambdaVPCAccessExecutionRole >/dev/null 2>&1 || true
SECRET_ARN=$(aws secretsmanager describe-secret --secret-id "$SECRET_NAME" --query ARN --output text)
# Read is scoped to the run prefix. This Lambda has no reason to see the rest
# of the artifacts bucket and no write permission anywhere.
aws iam put-role-policy --role-name "$ROLE" --policy-name ps3-rc-loader-inline --policy-document "{
  \"Version\":\"2012-10-17\",\"Statement\":[
    {\"Effect\":\"Allow\",\"Action\":\"secretsmanager:GetSecretValue\",\"Resource\":\"$SECRET_ARN\"},
    {\"Effect\":\"Allow\",\"Action\":[\"s3:GetObject\",\"s3:ListBucket\"],
     \"Resource\":[\"arn:aws:s3:::$ARTIFACTS\",\"arn:aws:s3:::$ARTIFACTS/$RC_PREFIX/*\"]}]}"
ROLE_ARN=arn:aws:iam::$ACCT:role/$ROLE
sleep 8

echo ">> [2/5] security group (reuses the dashboard-api SG; Aurora already allows it)"
SG=$(aws ec2 describe-security-groups --filters "Name=group-name,Values=$SG_NAME" \
       "Name=vpc-id,Values=$VPC" --query 'SecurityGroups[0].GroupId' --output text)
echo "   SG=$SG"

echo ">> [3/5] package (handler + pg8000)"
rm -rf build fn.zip && mkdir -p build
pip install -q -r requirements.txt -t build --only-binary=:all: 2>/dev/null \
  || pip install -q -r requirements.txt -t build
cp handler.py build/
(cd build && zip -qr ../fn.zip .)
du -h fn.zip | cut -f1 | sed 's/^/   /'

echo ">> [4/5] create/update Lambda (VPC)"
ENVV="Variables={RDS_SECRET_ID=$SECRET_NAME,RDS_HOST=$RDS_HOST,ARTIFACT_BUCKET=$ARTIFACTS,PS3_RC_PREFIX=$RC_PREFIX,CITY_ID=CHI,PG_TIMEOUT=300}"
SUBNET_CSV=$(echo $SUBNETS | tr ' ' ',')
if aws lambda get-function --function-name "$FN" >/dev/null 2>&1; then
  aws lambda update-function-code --function-name "$FN" --zip-file fileb://fn.zip >/dev/null
  aws lambda wait function-updated --function-name "$FN"
  aws lambda update-function-configuration --function-name "$FN" --timeout 900 --memory-size 1024 \
    --vpc-config "SubnetIds=$SUBNET_CSV,SecurityGroupIds=$SG" --environment "$ENVV" >/dev/null
else
  aws lambda create-function --function-name "$FN" --runtime python3.12 \
    --handler handler.lambda_handler --role "$ROLE_ARN" --zip-file fileb://fn.zip \
    --timeout 900 --memory-size 1024 \
    --vpc-config "SubnetIds=$SUBNET_CSV,SecurityGroupIds=$SG" --environment "$ENVV" >/dev/null
fi
aws lambda wait function-updated --function-name "$FN"
echo "   Lambda ready"

# NO EventBridge SCHEDULE HERE, AND THAT IS DELIBERATE.
#
# The other loaders run on a clock because their upstream publishes on one.
# PS3's root-cause run does not yet -- the scorer runs where the spine is
# built, and it invokes this Lambda itself when it has finished writing. A
# clock-based rule would fire against whatever happened to be in S3 at 07:20
# and, on a day the scorer failed, silently re-load yesterday's run under
# yesterday's run_id, which looks identical to a successful day on every
# screen. Add the schedule when the scorer is on a schedule, not before.
#
# To wire the S3 trigger instead (fires on publish, so a manual backfill or a
# re-publish loads itself with nobody remembering to invoke anything):
#
#   aws lambda add-permission --function-name $FN --statement-id s3-ps3rc \
#     --action lambda:InvokeFunction --principal s3.amazonaws.com \
#     --source-arn arn:aws:s3:::$ARTIFACTS --source-account $ACCT
#   aws s3api put-bucket-notification-configuration --bucket $ARTIFACTS \
#     --notification-configuration "{\"LambdaFunctionConfigurations\":[{
#       \"LambdaFunctionArn\":\"arn:aws:lambda:$REGION:$ACCT:function:$FN\",
#       \"Events\":[\"s3:ObjectCreated:*\"],
#       \"Filter\":{\"Key\":{\"FilterRules\":[
#         {\"Name\":\"prefix\",\"Value\":\"$RC_PREFIX/\"},
#         {\"Name\":\"suffix\",\"Value\":\"manifest.json\"}]}}}]}"
#
# put-bucket-notification-configuration REPLACES the whole notification
# config on the bucket. Read the existing one first and merge, or the PS3 v25
# feed's trigger disappears without a word.

echo ">> [5/5] done"
cat <<'EOT'
======================================================================
 NEXT -- always dry-run first. It reads S3, applies every guard, writes
 nothing, and reports S3 row counts against what is already in Aurora.

   aws lambda invoke --function-name cubic-mars-ps3-rc-loader \
     --cli-read-timeout 900 --cli-binary-format raw-in-base64-out \
     --payload '{"dry_run":true}' /tmp/ps3rc.json >/dev/null
   python3 -c "
import json
b=json.loads(json.load(open('/tmp/ps3rc.json'))['body'])
print('status :', b.get('status'))
print('run_id :', b.get('run_id'))
print('summary:', b.get('summary'))
for k,v in (b.get('reconcile') or {}).items():
    print(f'  {k:<26} s3={v[\"rows_in_s3\"]:>7,}  rds_before={v[\"rows_in_rds_before\"]}')
for k,v in (b.get('refused')   or {}).items(): print('  REFUSED  ', k, str(v.get('reason'))[:160])
for k,v in (b.get('no_target') or {}).items(): print('  NO TARGET', k, v['columns'])
for e in (b.get('runs_considered') or []):
    if e.get('skipped'): print('  skipped  ', e['run_id'], e['skipped'], e['missing'])
"

 Then load for real:
   aws lambda invoke --function-name cubic-mars-ps3-rc-loader \
     --cli-read-timeout 900 --cli-binary-format raw-in-base64-out \
     --payload '{}' /tmp/ps3rc.json >/dev/null

 AWS_MAX_ATTEMPTS=1 in front of these is worth the habit. The CLI's 60s read
 timeout plus retries once fired four concurrent PS2 loads at Aurora.
======================================================================
EOT
