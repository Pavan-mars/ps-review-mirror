#!/usr/bin/env bash
# =====================================================================
# cubic-mars-dim-loader  —  deploy from AWS CloudShell (us-east-1)
#
#   SageMaker -> S3 (chicago/ml_outputs/ps4/<run_id>/) -> THIS LAMBDA (in the
#   RDS VPC) -> Aurora ps4_* -> dashboard-api
#
# Idempotent. Resource IDs mirror cubic-mars-ps1-rds-push/deploy.sh.
#
# Run the SCHEMA first, from CloudShell, via the dashboard-api:
#   aws lambda invoke --function-name cubic-mars-dashboard-api \
#     --cli-binary-format raw-in-base64-out \
#     --payload '{"action":"migrate"}' /dev/stdout
# sql/22_dim_device_serial.sql creates every table this loader writes. The loader
# itself has no DDL and cannot create them.
# =====================================================================
set -euo pipefail
REGION=us-east-1
FN=cubic-mars-dim-loader
ROLE=cubic-mars-dim-loader-role-dev
SGNAME=cubic-mars-dashboard-api-sg            # reuse: same VPC egress needs
VPC=vpc-0a7775adc7d382fbb
SUBNETS="subnet-0830633f6cab1b8a1 subnet-01ad20b3b49bf59de subnet-0cd6e4bca78eaab8d"
RDS_HOST=cubic-mars-rds-aurora-dev.cluster-cgdk4y4ewxzi.us-east-1.rds.amazonaws.com
SECRET_NAME=cubic-mars-secret-rds-dev
DB_NAME="${DB_NAME:-postgres}"
GOLD_BUCKET=cubic-mars-pm-s3-datalake-dev-gold-170202974600
DIM_PREFIX=chicago/dim/device_serial
KEEP_SNAPSHOTS="${KEEP_SNAPSHOTS:-3}"

ACCT=$(aws sts get-caller-identity --query Account --output text)
SECRET_ARN=$(aws secretsmanager describe-secret --secret-id "$SECRET_NAME" --query ARN --output text)
echo ">> Account=$ACCT Region=$REGION DB=$DB_NAME"

echo ">> [1/7] IAM role"
if ! aws iam get-role --role-name "$ROLE" >/dev/null 2>&1; then
  aws iam create-role --role-name "$ROLE" \
    --assume-role-policy-document '{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"lambda.amazonaws.com"},"Action":"sts:AssumeRole"}]}' >/dev/null
  NEW=1
fi
aws iam attach-role-policy --role-name "$ROLE" \
  --policy-arn arn:aws:iam::aws:policy/service-role/AWSLambdaVPCAccessExecutionRole
aws iam put-role-policy --role-name "$ROLE" --policy-name dim-loader-inline --policy-document "{
  \"Version\":\"2012-10-17\",\"Statement\":[
    {\"Effect\":\"Allow\",\"Action\":\"secretsmanager:GetSecretValue\",\"Resource\":\"$SECRET_ARN\"},
    {\"Effect\":\"Allow\",\"Action\":[\"s3:GetObject\",\"s3:ListBucket\"],
     \"Resource\":[\"arn:aws:s3:::$GOLD_BUCKET\",\"arn:aws:s3:::$GOLD_BUCKET/$DIM_PREFIX/*\"]}]}"
ROLE_ARN=arn:aws:iam::$ACCT:role/$ROLE

echo ">> [2/7] security group"
SG=$(aws ec2 describe-security-groups --filters Name=group-name,Values=$SGNAME Name=vpc-id,Values=$VPC \
     --query "SecurityGroups[0].GroupId" --output text 2>/dev/null || true)
[ "$SG" = "None" ] && SG=""
if [ -z "$SG" ]; then
  SG=$(aws ec2 create-security-group --group-name $SGNAME --description "cubic-mars lambda egress" --vpc-id $VPC --query GroupId --output text)
fi
echo "   SG=$SG"

# ---------------------------------------------------------------------
# [3/7] AWS SDK for pandas managed layer (supplies pyarrow)
#
# 26-Jul-2026 FIX. The PS1 loader does:
#     aws lambda list-layer-versions --layer-name AWSSDKPandas-Python312
# which lists layers OWNED BY THIS ACCOUNT only. AWSSDKPandas is published by
# AWS under account 336392948345, so that call returns None, the script falls
# through to the 3.11 branch which returns None too, and the deploy then sends
# the literal string "None" to --layers:
#     ValidationException: Value '[None]' at 'layers' failed to satisfy constraint
# That is the error that killed step 3/6 of deploy_e2e.sh.
#
# Passing the FULL ARN as --layer-name resolves a layer owned by another account,
# which is what this needs.
# ---------------------------------------------------------------------
echo ">> [3/7] AWS SDK for pandas layer (supplies pyarrow)"
# 27-Jul-2026. list-layer-versions FAILED here even with the full cross-account
# ARN, and the old `2>/dev/null || echo None` swallowed the reason -- so the
# script could only report "could not resolve" without saying why.
#
# The cause is the API, not the ARN. AWS publishes the AWSSDKPandas layer with a
# resource policy that grants lambda:GetLayerVersion to everyone, but NOT
# lambda:ListLayerVersions. Listing another account's layer versions is therefore
# denied however the name is spelled. get-layer-version-by-arn is the call that
# is actually permitted, and it needs an explicit version number.
#
# So: try the fast path, keep its error visible, then probe versions descending
# with the call that is allowed. SDK_PANDAS_LAYER_ARN short-circuits everything
# if you already know the ARN.
AWS_SDK_PANDAS_ACCT=336392948345
LAYER_ARN=""; RUNTIME=""

if [ -n "${SDK_PANDAS_LAYER_ARN:-}" ]; then
  LAYER_ARN="$SDK_PANDAS_LAYER_ARN"
  RUNTIME="${SDK_PANDAS_RUNTIME:-python3.12}"
  echo "   using SDK_PANDAS_LAYER_ARN override"
else
  # 27-Jul-2026, MEASURED in this account:
  #   list-layer-versions        -> AccessDeniedException, "no resource-based
  #                                 policy allows the lambda:ListLayerVersions
  #                                 action" -- denied for EVERY cross-account
  #                                 layer, so the fast path can never work here
  #   get-layer-version-by-arn:29 -> returns the ARN
  # Version 29 is therefore tried FIRST, and the descending probe is the fallback
  # for when AWS publishes a newer one and 29 is eventually deprecated. That is
  # one API call on the happy path instead of thirty.
  SDK_PANDAS_KNOWN_VERSION=${SDK_PANDAS_KNOWN_VERSION:-29}
  for PYV in 312 311 313; do
    BASE=arn:aws:lambda:$REGION:$AWS_SDK_PANDAS_ACCT:layer:AWSSDKPandas-Python$PYV
    if aws lambda get-layer-version-by-arn --arn "$BASE:$SDK_PANDAS_KNOWN_VERSION" \
         --region "$REGION" --query 'LayerVersionArn' --output text >/dev/null 2>&1; then
      LAYER_ARN="$BASE:$SDK_PANDAS_KNOWN_VERSION"; RUNTIME="python3.${PYV:1}"
      break
    fi
    # newer or older than the pin -- walk down from 40
    for V in $(seq 40 -1 1); do
      if aws lambda get-layer-version-by-arn --arn "$BASE:$V" --region "$REGION" \
           --query 'LayerVersionArn' --output text >/dev/null 2>&1; then
        LAYER_ARN="$BASE:$V"; RUNTIME="python3.${PYV:1}"
        echo "   resolved by probe: version $V (pin $SDK_PANDAS_KNOWN_VERSION unavailable)"
        break
      fi
    done
    [ -n "$LAYER_ARN" ] && break
  done
fi

if [ -z "$LAYER_ARN" ]; then
  echo "!! Could not resolve the AWSSDKPandas layer in $REGION."
  echo "   list-layer-versions is DENIED for cross-account layers in this"
  echo "   account (measured), so only get-layer-version-by-arn can resolve it."
  echo "   Try one of:"
  echo "     aws lambda get-layer-version-by-arn --arn arn:aws:lambda:$REGION:$AWS_SDK_PANDAS_ACCT:layer:AWSSDKPandas-Python312:20"
  echo "     SDK_PANDAS_LAYER_ARN=<arn> bash deploy.sh"
  echo "   Refusing to deploy with an unresolved layer rather than sending 'None'."
  exit 1
fi
echo "   $LAYER_ARN  ($RUNTIME)"

echo ">> [4/7] package (handler + pg8000 only)"
rm -rf build fn.zip && mkdir build && cp handler.py build/
pip3 install "pg8000>=1.31,<2" -t build/ -q
(cd build && zip -qr ../fn.zip .)
echo "   $(du -h fn.zip | cut -f1)"

echo ">> [5/7] create/update Lambda (VPC)"
SUBNET_CSV=$(echo $SUBNETS | tr ' ' ',')
ENVV="Variables={RDS_SECRET_ID=$SECRET_NAME,RDS_HOST=$RDS_HOST,RDS_DATABASE=$DB_NAME,GOLD_BUCKET=$GOLD_BUCKET,DIM_PREFIX=$DIM_PREFIX,CITY_ID=CHI,KEEP_SNAPSHOTS=$KEEP_SNAPSHOTS}"

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
  aws lambda update-function-configuration --function-name "$FN" --timeout 900 --memory-size 3008 \
    --layers "$LAYER_ARN" --vpc-config "SubnetIds=$SUBNET_CSV,SecurityGroupIds=$SG" --environment "$ENVOPT" >/dev/null
else
  [ "${NEW:-0}" = "1" ] && sleep 12
  aws lambda create-function --function-name "$FN" --runtime $RUNTIME --handler handler.lambda_handler \
    --role "$ROLE_ARN" --zip-file fileb://fn.zip --timeout 900 --memory-size 3008 \
    --layers "$LAYER_ARN" --vpc-config "SubnetIds=$SUBNET_CSV,SecurityGroupIds=$SG" --environment "$ENVOPT" >/dev/null
fi
aws lambda wait function-updated --function-name "$FN"
echo "   Lambda ready"

echo ">> [6/7] EventBridge schedule (05:45 UTC daily — before the PS1 push at 06:15)"
RULE=cubic-mars-dim-daily-refresh
aws events put-rule --name $RULE --schedule-expression "cron(45 5 * * ? *)" \
  --description "Daily device-serial dimension refresh -> Aurora" --state ENABLED >/dev/null
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
echo "   rule=$RULE  (empty payload -> loads the newest run prefix)"

# ---------------------------------------------------------------------
# [7/7] S3 ObjectCreated on the manifest.
#
# put-bucket-notification-configuration REPLACES the whole config, so the PS1
# trigger would be wiped if we posted only ours. This reads the existing config,
# merges our entry by Id, and writes the union back.
# ---------------------------------------------------------------------
echo ">> [7/7] S3 ObjectCreated trigger on $DIM_PREFIX/*/manifest.json"
aws lambda add-permission --function-name "$FN" --statement-id s3-invoke-dim \
  --action lambda:InvokeFunction --principal s3.amazonaws.com \
  --source-arn arn:aws:s3:::$GOLD_BUCKET --source-account $ACCT >/dev/null 2>&1 || true

aws s3api get-bucket-notification-configuration --bucket $GOLD_BUCKET > /tmp/dim_notif_cur.json 2>/dev/null \
  || echo '{}' > /tmp/dim_notif_cur.json

python3 - "$REGION" "$ACCT" "$FN" "$DIM_PREFIX" <<'PY'
import json, sys
region, acct, fn, prefix = sys.argv[1:5]
cfg = json.load(open("/tmp/dim_notif_cur.json"))
cfg.pop("ResponseMetadata", None)
ours = {
    "Id": "dim-device-serial-refresh",
    "LambdaFunctionArn": f"arn:aws:lambda:{region}:{acct}:function:{fn}",
    "Events": ["s3:ObjectCreated:*"],
    "Filter": {"Key": {"FilterRules": [
        {"Name": "prefix", "Value": f"{prefix}/"},
        {"Name": "suffix", "Value": "manifest.json"}]}},
}
lst = [c for c in cfg.get("LambdaFunctionConfigurations", []) if c.get("Id") != ours["Id"]]
lst.append(ours)
cfg["LambdaFunctionConfigurations"] = lst
json.dump(cfg, open("/tmp/dim_notif.json", "w"), indent=2)
print(f"   merged: {len(lst)} lambda notification(s) will be configured")
PY

aws s3api put-bucket-notification-configuration --bucket $GOLD_BUCKET \
  --notification-configuration file:///tmp/dim_notif.json 2>/dev/null \
  && echo "   S3 trigger set (existing notifications preserved)" \
  || echo "   [warn] S3 notification not set — the EventBridge schedule still works"

echo
echo "DONE."
echo "  1. schema (once, from CloudShell):"
echo "     aws lambda invoke --function-name cubic-mars-dashboard-api \\"
echo "       --cli-binary-format raw-in-base64-out --payload '{\"action\":\"migrate\"}' /dev/stdout"
echo "  2. dry run (reads S3, writes nothing):"
echo "     aws lambda invoke --function-name $FN --cli-binary-format raw-in-base64-out \\"
echo "       --payload '{\"dry_run\":true}' /tmp/dim.json && cat /tmp/dim.json | head -c 2000"
echo "  3. load for real:"
echo "     aws lambda invoke --function-name $FN --cli-binary-format raw-in-base64-out \\"
echo "       --payload '{}' /tmp/dim.json && cat /tmp/dim.json | head -c 2000"
