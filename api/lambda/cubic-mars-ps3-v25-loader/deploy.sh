#!/usr/bin/env bash
# cubic-mars-ps3-v25-loader -- deploy from AWS CloudShell (us-east-1)
#
# A NEW function with a NEW role and a NEW IAM policy. It can read exactly one
# S3 prefix and write exactly the ps3_v25_* tables. It shares nothing with
# cubic-mars-ps3-v2-loader or cubic-mars-ps3-rds-push, which keep filling the
# tables the deployed PS3 screens read today. Deleting this function is a
# complete rollback and leaves the plan-B feed running.
#
# No duckdb here. The PS3 v2 loader needs it because its parquet is zstd and the
# managed pyarrow layer ships without that codec; PS3 v25 writes plain snappy
# through pandas.to_parquet, which the layer reads natively. One fewer native
# wheel built for the wrong interpreter is one fewer way to deploy clean and die
# at runtime with No module named '_duckdb'.
set -euo pipefail
REGION=us-east-1
FN=cubic-mars-ps3-v25-loader
ROLE=cubic-mars-ps3-v25-loader-role-dev
SGNAME=cubic-mars-dashboard-api-sg
VPC=vpc-0a7775adc7d382fbb
SUBNETS="subnet-0830633f6cab1b8a1 subnet-01ad20b3b49bf59de subnet-0cd6e4bca78eaab8d"
RDS_HOST=cubic-mars-rds-aurora-dev.cluster-cgdk4y4ewxzi.us-east-1.rds.amazonaws.com
SECRET_NAME=cubic-mars-secret-rds-dev
ARTIFACT_BUCKET=cubic-mars-pm-s3-datalake-dev-artifacts-170202974600
# ps3_replay_outputs for the V26 REPLAY run already in the bucket; switch to
# ps3_outputs when the first PRODUCTION run lands. The notebook refuses any
# other prefix tail, so these are the only two legal values.
PS3_PREFIX=${PS3_PREFIX:-chicago/ps3_outputs}

ACCT=$(aws sts get-caller-identity --query Account --output text)
SECRET_ARN=$(aws secretsmanager describe-secret --secret-id "$SECRET_NAME" --query ARN --output text)

echo ">> [1/4] IAM role"
if ! aws iam get-role --role-name "$ROLE" >/dev/null 2>&1; then
  aws iam create-role --role-name "$ROLE" --assume-role-policy-document '{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"lambda.amazonaws.com"},"Action":"sts:AssumeRole"}]}' >/dev/null
  NEW=1
fi
aws iam attach-role-policy --role-name "$ROLE" --policy-arn arn:aws:iam::aws:policy/service-role/AWSLambdaVPCAccessExecutionRole
# Read-only on S3, and scoped to BOTH prefixes so flipping PS3_PREFIX from
# replay to production needs no policy edit -- an edit that would otherwise be
# made under time pressure on the day the first production run lands.
aws iam put-role-policy --role-name "$ROLE" --policy-name ps3-v25-loader-inline --policy-document "{
  \"Version\":\"2012-10-17\",\"Statement\":[
    {\"Effect\":\"Allow\",\"Action\":\"secretsmanager:GetSecretValue\",\"Resource\":\"$SECRET_ARN\"},
    {\"Effect\":\"Allow\",\"Action\":[\"s3:ListBucket\"],
     \"Resource\":\"arn:aws:s3:::$ARTIFACT_BUCKET\",
     \"Condition\":{\"StringLike\":{\"s3:prefix\":[\"ps3_outputs/*\",\"ps3_replay_outputs/*\"]}}},
    {\"Effect\":\"Allow\",\"Action\":[\"s3:GetObject\"],
     \"Resource\":[\"arn:aws:s3:::$ARTIFACT_BUCKET/ps3_outputs/*\",
                   \"arn:aws:s3:::$ARTIFACT_BUCKET/ps3_replay_outputs/*\"]}]}"
ROLE_ARN=arn:aws:iam::$ACCT:role/$ROLE

echo ">> [2/4] security group"
SG=$(aws ec2 describe-security-groups --filters Name=group-name,Values=$SGNAME Name=vpc-id,Values=$VPC --query "SecurityGroups[0].GroupId" --output text 2>/dev/null || true)
[ "$SG" = "None" ] && SG=""
[ -z "$SG" ] && SG=$(aws ec2 create-security-group --group-name $SGNAME --description "cubic-mars lambda egress" --vpc-id $VPC --query GroupId --output text)
echo "   SG=$SG"

echo ">> [3/4] layer + package"
AWS_SDK_PANDAS_ACCT=336392948345; LAYER_ARN=""; RUNTIME=""
for PYV in 312 311; do
  BASE=arn:aws:lambda:$REGION:$AWS_SDK_PANDAS_ACCT:layer:AWSSDKPandas-Python$PYV
  if aws lambda get-layer-version-by-arn --arn "$BASE:29" --region $REGION --query LayerVersionArn --output text >/dev/null 2>&1; then
    LAYER_ARN="$BASE:29"; RUNTIME="python3.${PYV:1}"; break; fi
done
[ -z "$LAYER_ARN" ] && { echo "!! layer unresolved"; exit 1; }
echo "   $LAYER_ARN ($RUNTIME)"
rm -rf build fn.zip && mkdir build && cp handler.py build/
pip3 install "pg8000>=1.31,<2" -t build/ -q
(cd build && zip -qr ../fn.zip .); echo "   $(du -h fn.zip | cut -f1)"

echo ">> [4/4] deploy"
SUBNET_CSV=$(echo $SUBNETS | tr ' ' ',')
# 3008 MB: peak RSS was measured at 742 MB loading the widest table (54,239
# rows x 79 columns, held twice during shaping). Lambda CPU also scales with
# memory, which matters for the CSV encoding COPY does in python.
# 900 s: the full 20-table load plus a DELETE per table. The widest table took
# 28.9s via COPY on a local PostgreSQL 16, so this is headroom, not a target.
ENVV="Variables={RDS_SECRET_ID=$SECRET_NAME,RDS_HOST=$RDS_HOST,ARTIFACT_BUCKET=$ARTIFACT_BUCKET,PS3_PREFIX=$PS3_PREFIX,CITY_ID=CHI,PG_TIMEOUT=300}"

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
  aws lambda update-function-configuration --function-name "$FN" --timeout 900 --memory-size 3008 --layers "$LAYER_ARN" --vpc-config "SubnetIds=$SUBNET_CSV,SecurityGroupIds=$SG" --environment "$ENVOPT" >/dev/null
else
  [ "${NEW:-0}" = "1" ] && sleep 12
  aws lambda create-function --function-name "$FN" --runtime $RUNTIME --handler handler.lambda_handler --role "$ROLE_ARN" --zip-file fileb://fn.zip --timeout 900 --memory-size 3008 --layers "$LAYER_ARN" --vpc-config "SubnetIds=$SUBNET_CSV,SecurityGroupIds=$SG" --environment "$ENVOPT" >/dev/null
fi
aws lambda wait function-updated --function-name "$FN"

cat <<EOF
======================================================================
 DONE. Run these in order, and read the dry run before loading.

 1) DRY RUN -- reads S3, shapes every row, runs all four guards, writes
    nothing. Check: status=dry_run_ok, summary.loaded=20, refused=0, and
    that device_episode_fact reports _episode_row_id under columns_dropped.

    aws lambda invoke --function-name $FN --cli-binary-format raw-in-base64-out \\
      --cli-read-timeout 900 --payload '{"dry_run":true}' /dev/stdout

 2) LOAD

    aws lambda invoke --function-name $FN --cli-binary-format raw-in-base64-out \\
      --cli-read-timeout 900 --payload '{}' /dev/stdout

 3) CONFIRM in Aurora

    SELECT * FROM v_ps3_v25_status ORDER BY table_name;

 The existing PS3 tables, loaders and endpoints are untouched.
======================================================================
EOF
