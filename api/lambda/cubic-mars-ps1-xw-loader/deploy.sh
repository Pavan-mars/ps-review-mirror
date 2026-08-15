#!/usr/bin/env bash
# =====================================================================
# cubic-mars-ps1-xw-loader  --  deploy from AWS CloudShell (us-east-1)
#
#   SageMaker PS1 notebooks
#     -> s3://<artifacts>/chicago/device_ps1_cross_wired_daily/{gate,tvm,validator}
#     -> THIS LAMBDA (in the RDS VPC)
#     -> Aurora ps1_cross_wired_daily
#
# Idempotent. Resource IDs mirror cubic-mars-ps4-rds-loader/deploy.sh.
#
# RUN THE SCHEMA FIRST. This loader has no DDL and cannot create its table:
#   aws lambda invoke --function-name cubic-mars-dashboard-api \
#     --cli-binary-format raw-in-base64-out \
#     --payload '{"action":"migrate"}' /tmp/mig.json && cat /tmp/mig.json
# sql/34_ps1_cross_wired.sql creates ps1_cross_wired_daily and its five views.
#
# Do NOT pipe invoke output to /dev/stdout. The AWS CLI writes TWO JSON
# documents there -- the function response and its own status blob -- and every
# JSON parser chokes on the second. Write to a file and cat it.
# =====================================================================
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

REGION=us-east-1
FN=cubic-mars-ps1-xw-loader
ROLE=cubic-mars-ps1-xw-loader-role-dev
SGNAME=cubic-mars-dashboard-api-sg
VPC=vpc-0a7775adc7d382fbb
SUBNETS="subnet-0830633f6cab1b8a1 subnet-01ad20b3b49bf59de subnet-0cd6e4bca78eaab8d"
RDS_HOST=cubic-mars-rds-aurora-dev.cluster-cgdk4y4ewxzi.us-east-1.rds.amazonaws.com
SECRET_NAME=cubic-mars-secret-rds-dev
DB_NAME="${DB_NAME:-postgres}"
ARTIFACT_BUCKET=cubic-mars-pm-s3-datalake-dev-artifacts-170202974600
PS1_XW_PREFIX=chicago/device_ps1_cross_wired_daily

ACCT=$(aws sts get-caller-identity --query Account --output text)
echo ">> account $ACCT  region $REGION"

# ---------------------------------------------------------------------
echo ">> [1/6] IAM role"
if ! aws iam get-role --role-name "$ROLE" >/dev/null 2>&1; then
  aws iam create-role --role-name "$ROLE" --assume-role-policy-document '{
    "Version":"2012-10-17","Statement":[{"Effect":"Allow",
    "Principal":{"Service":"lambda.amazonaws.com"},"Action":"sts:AssumeRole"}]}' >/dev/null
  NEW=1
fi
ROLE_ARN=$(aws iam get-role --role-name "$ROLE" --query 'Role.Arn' --output text)
aws iam attach-role-policy --role-name "$ROLE" \
  --policy-arn arn:aws:iam::aws:policy/service-role/AWSLambdaVPCAccessExecutionRole >/dev/null

# S3 read is scoped to the ONE prefix this loader reads. A wildcard on the whole
# artifacts bucket would also grant every PS2/PS4/PS5 export, which this function
# has no business reading.
aws iam put-role-policy --role-name "$ROLE" --policy-name ps1xw-inline \
  --policy-document "{\"Version\":\"2012-10-17\",\"Statement\":[
    {\"Effect\":\"Allow\",\"Action\":[\"s3:GetObject\"],
     \"Resource\":\"arn:aws:s3:::$ARTIFACT_BUCKET/$PS1_XW_PREFIX/*\"},
    {\"Effect\":\"Allow\",\"Action\":[\"s3:ListBucket\"],
     \"Resource\":\"arn:aws:s3:::$ARTIFACT_BUCKET\",
     \"Condition\":{\"StringLike\":{\"s3:prefix\":[\"$PS1_XW_PREFIX/*\"]}}},
    {\"Effect\":\"Allow\",\"Action\":[\"secretsmanager:GetSecretValue\"],
     \"Resource\":\"arn:aws:secretsmanager:$REGION:$ACCT:secret:$SECRET_NAME*\"}]}" >/dev/null
echo "   $ROLE_ARN"

# ---------------------------------------------------------------------
echo ">> [2/6] security group"
SG=$(aws ec2 describe-security-groups --filters "Name=group-name,Values=$SGNAME" \
      "Name=vpc-id,Values=$VPC" --query 'SecurityGroups[0].GroupId' --output text)
if [ "$SG" = "None" ] || [ -z "$SG" ]; then
  echo "!! security group $SGNAME not found in $VPC -- deploy the dashboard-api first"; exit 1
fi
echo "   $SG"

# ---------------------------------------------------------------------
# The AWSSDKPandas managed layer supplies pandas + pyarrow. Resolution notes are
# in cubic-mars-ps4-rds-loader/deploy.sh: list-layer-versions is DENIED for
# cross-account layers in this account, so get-layer-version-by-arn is the only
# call that works. Version 29 is tried first because it is the one measured to
# exist here; the descending probe is the fallback for when AWS deprecates it.
# ---------------------------------------------------------------------
echo ">> [3/6] AWSSDKPandas layer (pandas + pyarrow)"
AWS_SDK_PANDAS_ACCT=336392948345
LAYER_ARN=""; RUNTIME=""
if [ -n "${SDK_PANDAS_LAYER_ARN:-}" ]; then
  LAYER_ARN="$SDK_PANDAS_LAYER_ARN"; RUNTIME="${SDK_PANDAS_RUNTIME:-python3.12}"
  echo "   using SDK_PANDAS_LAYER_ARN override"
else
  PIN=${SDK_PANDAS_KNOWN_VERSION:-29}
  for PYV in 312 311 313; do
    BASE=arn:aws:lambda:$REGION:$AWS_SDK_PANDAS_ACCT:layer:AWSSDKPandas-Python$PYV
    if aws lambda get-layer-version-by-arn --arn "$BASE:$PIN" --region "$REGION" \
         --query 'LayerVersionArn' --output text >/dev/null 2>&1; then
      LAYER_ARN="$BASE:$PIN"; RUNTIME="python3.${PYV:1}"; break
    fi
    for V in $(seq 40 -1 1); do
      if aws lambda get-layer-version-by-arn --arn "$BASE:$V" --region "$REGION" \
           --query 'LayerVersionArn' --output text >/dev/null 2>&1; then
        LAYER_ARN="$BASE:$V"; RUNTIME="python3.${PYV:1}"
        echo "   resolved by probe: version $V"; break
      fi
    done
    [ -n "$LAYER_ARN" ] && break
  done
fi
if [ -z "$LAYER_ARN" ]; then
  echo "!! could not resolve AWSSDKPandas. Refusing to deploy with an unresolved"
  echo "   layer rather than sending the literal string 'None' to --layers."
  echo "   SDK_PANDAS_LAYER_ARN=<arn> bash deploy.sh"
  exit 1
fi
echo "   $LAYER_ARN  ($RUNTIME)"

# ---------------------------------------------------------------------
echo ">> [4/6] package (handler + pg8000; pandas comes from the layer)"
rm -rf build fn.zip && mkdir build && cp handler.py build/
pip3 install "pg8000>=1.31,<2" -t build/ -q
(cd build && zip -qr ../fn.zip .)
echo "   $(du -h fn.zip | cut -f1)"

# ---------------------------------------------------------------------
# 3008 MB / 900 s. 786,428 rows x 30 columns is roughly 350 MB as a pandas frame
# plus the shaped tuples, and the insert runs at a few thousand rows/sec through
# pg8000. Both are inside these limits with margin -- unlike PS4's 3.5M rows,
# which needed the streaming rewrite.
echo ">> [5/6] create/update Lambda (VPC)"
SUBNET_CSV=$(echo $SUBNETS | tr ' ' ',')
ENVV="Variables={RDS_SECRET_ID=$SECRET_NAME,RDS_HOST=$RDS_HOST,RDS_DATABASE=$DB_NAME,ARTIFACT_BUCKET=$ARTIFACT_BUCKET,PS1_XW_PREFIX=$PS1_XW_PREFIX,CITY_ID=CHI}"

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
  aws lambda update-function-configuration --function-name "$FN" \
    --timeout 900 --memory-size 3008 --layers "$LAYER_ARN" \
    --vpc-config "SubnetIds=$SUBNET_CSV,SecurityGroupIds=$SG" --environment "$ENVOPT" >/dev/null
else
  [ "${NEW:-0}" = "1" ] && sleep 12
  aws lambda create-function --function-name "$FN" --runtime $RUNTIME \
    --handler handler.lambda_handler --role "$ROLE_ARN" --zip-file fileb://fn.zip \
    --timeout 900 --memory-size 3008 --layers "$LAYER_ARN" \
    --vpc-config "SubnetIds=$SUBNET_CSV,SecurityGroupIds=$SG" --environment "$ENVOPT" >/dev/null
fi
aws lambda wait function-updated --function-name "$FN"
echo "   Lambda ready"

# ---------------------------------------------------------------------
echo ">> [6/6] daily schedule (06:40 UTC, after the PS1 notebooks, before PS4 at 07:10)"
RULE=cubic-mars-ps1-xw-daily-load
aws events put-rule --name $RULE --schedule-expression "cron(40 6 * * ? *)" \
  --description "PS1 cross-wired export -> Aurora" --state ENABLED >/dev/null
aws lambda add-permission --function-name "$FN" --statement-id ${RULE}-invoke \
  --action lambda:InvokeFunction --principal events.amazonaws.com \
  --source-arn arn:aws:events:$REGION:$ACCT:rule/$RULE >/dev/null 2>&1 || true
# Input must be a JSON *string*. Shorthand "Input={}" makes the CLI parse the
# braces as a nested structure and reject them:
#   Invalid type for parameter Targets[0].Input, value: {}, type: <class 'dict'>
aws events put-targets --rule $RULE \
  --targets "[{\"Id\":\"1\",\"Arn\":\"arn:aws:lambda:$REGION:$ACCT:function:$FN\",\"Input\":\"{}\"}]" >/dev/null
echo "   rule=$RULE"

cat <<'EOF'

DONE. Now, IN THIS ORDER:

  1) schema
     aws lambda invoke --function-name cubic-mars-dashboard-api \
       --cli-binary-format raw-in-base64-out \
       --payload '{"action":"migrate"}' /tmp/mig.json >/dev/null && \
       python3 -c "import json;d=json.load(open('/tmp/mig.json'));print(json.dumps(d)[:1200])"

  2) DRY RUN -- reads S3, writes nothing, reports the real column list
     aws lambda invoke --function-name cubic-mars-ps1-xw-loader \
       --cli-binary-format raw-in-base64-out \
       --payload '{"action":"dry_run"}' /tmp/xw_dry.json >/dev/null && \
       python3 -c "
import json;d=json.load(open('/tmp/xw_dry.json'))
print('errors:',d.get('errors'))
print('total rows in S3:',d.get('total_rows_in_s3'),' expected:',d['expected_from_console']['total'])
for k,v in d['files'].items():
    print(f\"  {k:10s} {v['rows_in_s3']:>8,} rows  {v['n_columns']} cols  {v['bytes']:>9,} bytes\")
    print('     unmapped ->extra:', v.get('unmapped_to_extra'))
"

  3) SMOKE -- 5,000 rows per type, proves the INSERT path before the full run
     aws lambda invoke --function-name cubic-mars-ps1-xw-loader \
       --cli-binary-format raw-in-base64-out \
       --payload '{"action":"load","limit":5000}' /tmp/xw_smoke.json >/dev/null && \
       python3 -c "import json;d=json.load(open('/tmp/xw_smoke.json'));print(d.get('total_rows_loaded'),d.get('errors'))"

  4) FULL LOAD
     aws lambda invoke --function-name cubic-mars-ps1-xw-loader \
       --cli-binary-format raw-in-base64-out \
       --payload '{"action":"load"}' /tmp/xw_load.json >/dev/null && \
       python3 -c "import json;d=json.load(open('/tmp/xw_load.json'));print(d.get('total_rows_loaded'),d.get('errors'))"

  Step 4 must report 786,428 and an empty errors object. Anything else, stop and
  send me /tmp/xw_load.json -- do not build panels on a partial load.
EOF
