#!/usr/bin/env bash
# =====================================================================
# cubic-mars-ps5-rds-loader  --  deploy from AWS CloudShell (us-east-1)
#
#   s3://<gold>/chicago/ps5/notebook_outputs/  ->  THIS LAMBDA (in the RDS VPC)
#   ->  Aurora ps5_*  ->  dashboard-api
#
# NO AWSSDKPandas layer. The PS5 exports are CSV, read with the stdlib csv
# module, so there is no pyarrow dependency -- which removes the cross-account
# ListLayerVersions probe that cost several rounds on the PS2 and PS4 deploys.
#
# Run the SCHEMA first:
#   aws lambda invoke --function-name cubic-mars-dashboard-api \
#     --cli-binary-format raw-in-base64-out --payload '{"action":"migrate"}' /dev/null
# sql/29_ps5_outputs.sql creates every table this loader writes. The loader has
# no DDL and cannot create them.
# =====================================================================
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"; cd "$SCRIPT_DIR"
REGION=us-east-1
FN=cubic-mars-ps5-rds-loader
ROLE=cubic-mars-ps5-rds-loader-role-dev
SG_NAME=cubic-mars-dashboard-api-sg
VPC=vpc-0a7775adc7d382fbb
SUBNETS="subnet-0830633f6cab1b8a1 subnet-01ad20b3b49bf59de subnet-0cd6e4bca78eaab8d"
RDS_HOST=cubic-mars-rds-aurora-dev.cluster-cgdk4y4ewxzi.us-east-1.rds.amazonaws.com
SECRET_NAME=cubic-mars-secret-rds-dev
GOLD=cubic-mars-pm-s3-datalake-dev-gold-170202974600
PS5_PREFIX=chicago/ps5/notebook_outputs
ACCT=$(aws sts get-caller-identity --query Account --output text)
echo ">> Account=$ACCT Region=$REGION"

echo ">> [1/5] IAM role"
aws iam get-role --role-name "$ROLE" >/dev/null 2>&1 || aws iam create-role --role-name "$ROLE" \
  --assume-role-policy-document '{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"lambda.amazonaws.com"},"Action":"sts:AssumeRole"}]}' >/dev/null
aws iam attach-role-policy --role-name "$ROLE" \
  --policy-arn arn:aws:iam::aws:policy/service-role/AWSLambdaVPCAccessExecutionRole >/dev/null 2>&1 || true
SECRET_ARN=$(aws secretsmanager describe-secret --secret-id "$SECRET_NAME" --query ARN --output text)
aws iam put-role-policy --role-name "$ROLE" --policy-name ps5-loader-inline --policy-document "{
  \"Version\":\"2012-10-17\",\"Statement\":[
    {\"Effect\":\"Allow\",\"Action\":\"secretsmanager:GetSecretValue\",\"Resource\":\"$SECRET_ARN\"},
    {\"Effect\":\"Allow\",\"Action\":[\"s3:GetObject\",\"s3:ListBucket\"],
     \"Resource\":[\"arn:aws:s3:::$GOLD\",\"arn:aws:s3:::$GOLD/$PS5_PREFIX/*\"]}]}"
ROLE_ARN=arn:aws:iam::$ACCT:role/$ROLE
sleep 8

echo ">> [2/5] security group"
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
ENVV="Variables={RDS_SECRET_ID=$SECRET_NAME,RDS_HOST=$RDS_HOST,GOLD_BUCKET=$GOLD,PS5_PREFIX=$PS5_PREFIX,CITY_ID=CHI}"

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

SUBNET_CSV=$(echo $SUBNETS | tr ' ' ',')
if aws lambda get-function --function-name "$FN" >/dev/null 2>&1; then
  aws lambda update-function-code --function-name "$FN" --zip-file fileb://fn.zip >/dev/null
  aws lambda wait function-updated --function-name "$FN"
  aws lambda update-function-configuration --function-name "$FN" --timeout 900 --memory-size 1024 \
    --vpc-config "SubnetIds=$SUBNET_CSV,SecurityGroupIds=$SG" --environment "$ENVOPT" >/dev/null
else
  aws lambda create-function --function-name "$FN" --runtime python3.12 \
    --handler handler.lambda_handler --role "$ROLE_ARN" --zip-file fileb://fn.zip \
    --timeout 900 --memory-size 1024 \
    --vpc-config "SubnetIds=$SUBNET_CSV,SecurityGroupIds=$SG" --environment "$ENVOPT" >/dev/null
fi
aws lambda wait function-updated --function-name "$FN"
echo "   Lambda ready"

echo ">> [5/5] EventBridge daily schedule (07:20 UTC, after the PS2 load at 07:10)"
RULE=cubic-mars-ps5-daily-load
aws events put-rule --name $RULE --schedule-expression "cron(20 7 * * ? *)" >/dev/null
aws lambda add-permission --function-name "$FN" --statement-id ev-ps5-daily \
  --action lambda:InvokeFunction --principal events.amazonaws.com \
  --source-arn "arn:aws:events:$REGION:$ACCT:rule/$RULE" >/dev/null 2>&1 || true
# Input must be a JSON STRING. The CLI shorthand Input={} parses as a dict and
# is rejected -- the same trap hit on all three earlier loaders.
aws events put-targets --rule $RULE \
  --targets "[{\"Id\":\"1\",\"Arn\":\"arn:aws:lambda:$REGION:$ACCT:function:$FN\",\"Input\":\"{}\"}]" >/dev/null
echo "   rule=$RULE"

cat <<'EOT'
======================================================================
 DONE.  Next:
   # dry run = reads S3, writes nothing, AND reports S3 vs Aurora row counts
   aws lambda invoke --function-name cubic-mars-ps5-rds-loader \
     --cli-read-timeout 900 --cli-binary-format raw-in-base64-out \
     --payload '{"dry_run":true}' /tmp/ps5.json >/dev/null
   python3 -c "
import json
b=json.loads(json.load(open('/tmp/ps5.json'))['body'])
print('status :', b.get('status')); print('summary:', b.get('summary'))
for k,v in (b.get('reconcile') or {}).items():
    print(f'  {k:<34} s3={v[\"rows_in_s3\"]:>7,}  rds={v[\"rows_in_rds_before\"]}')
for k,v in (b.get('no_target') or {}).items(): print('  NO TARGET', k, v['columns'])
for k,v in (b.get('refused')  or {}).items(): print('  REFUSED  ', k, str(v.get('reason'))[:160])
"
======================================================================
EOT
