#!/usr/bin/env bash
# =====================================================================
# cubic-mars-ps4-v3-loader  --  deploy from AWS CloudShell (us-east-1)
#
#   SageMaker PS4 v3 -> s3://<artifacts>/chicago/ps4/v3 -> THIS LAMBDA (in the
#   RDS VPC) -> Aurora ps4_weekly_* / ps4_cluster_* -> dashboard-api
#
# A NEW function. It does not update, replace or share anything with
# cubic-mars-ps4-rds-loader, which keeps running and keeps filling the Plan B
# tables. Deleting this function is a complete rollback.
#
# Run the SCHEMA first:
#   aws lambda invoke --function-name cubic-mars-dashboard-api \
#     --cli-binary-format raw-in-base64-out --cli-read-timeout 0 \
#     --payload '{"action":"migrate"}' /dev/stdout
# sql/38_ps4_weekly_v3.sql creates every table this loader writes. The loader
# has no DDL and cannot create them.
# =====================================================================
set -euo pipefail
REGION=us-east-1
FN=cubic-mars-ps4-v3-loader
ROLE=cubic-mars-ps4-v3-loader-role-dev
SGNAME=cubic-mars-dashboard-api-sg            # reuse: identical VPC egress need
VPC=vpc-0a7775adc7d382fbb
SUBNETS="subnet-0830633f6cab1b8a1 subnet-01ad20b3b49bf59de subnet-0cd6e4bca78eaab8d"
RDS_HOST=cubic-mars-rds-aurora-dev.cluster-cgdk4y4ewxzi.us-east-1.rds.amazonaws.com
SECRET_NAME=cubic-mars-secret-rds-dev
ARTIFACT_BUCKET=cubic-mars-pm-s3-datalake-dev-artifacts-170202974600
PS4_V3_ROOT=chicago/ps4/v3

ACCT=$(aws sts get-caller-identity --query Account --output text)
SECRET_ARN=$(aws secretsmanager describe-secret --secret-id "$SECRET_NAME" --query ARN --output text)
echo ">> Account=$ACCT Region=$REGION"

echo ">> [1/5] IAM role"
if ! aws iam get-role --role-name "$ROLE" >/dev/null 2>&1; then
  aws iam create-role --role-name "$ROLE" \
    --assume-role-policy-document '{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"lambda.amazonaws.com"},"Action":"sts:AssumeRole"}]}' >/dev/null
  NEW=1
fi
aws iam attach-role-policy --role-name "$ROLE" \
  --policy-arn arn:aws:iam::aws:policy/service-role/AWSLambdaVPCAccessExecutionRole
# Read is scoped to the v3 prefix only. This function has no reason to read the
# old PS4 prefixes and is not permitted to, so it cannot damage the Plan B feed
# even by mistake.
aws iam put-role-policy --role-name "$ROLE" --policy-name ps4-v3-loader-inline --policy-document "{
  \"Version\":\"2012-10-17\",\"Statement\":[
    {\"Effect\":\"Allow\",\"Action\":\"secretsmanager:GetSecretValue\",\"Resource\":\"$SECRET_ARN\"},
    {\"Effect\":\"Allow\",\"Action\":[\"s3:GetObject\",\"s3:ListBucket\"],
     \"Resource\":[\"arn:aws:s3:::$ARTIFACT_BUCKET\",
                  \"arn:aws:s3:::$ARTIFACT_BUCKET/$PS4_V3_ROOT/*\"]}]}"
ROLE_ARN=arn:aws:iam::$ACCT:role/$ROLE

echo ">> [2/5] security group"
SG=$(aws ec2 describe-security-groups --filters Name=group-name,Values=$SGNAME Name=vpc-id,Values=$VPC \
     --query "SecurityGroups[0].GroupId" --output text 2>/dev/null || true)
[ "$SG" = "None" ] && SG=""
if [ -z "$SG" ]; then
  SG=$(aws ec2 create-security-group --group-name $SGNAME --description "cubic-mars lambda egress" --vpc-id $VPC --query GroupId --output text)
fi
echo "   SG=$SG"

# AWS publishes the AWSSDKPandas layer (which supplies pyarrow) with a policy
# that allows GetLayerVersion but NOT ListLayerVersions cross-account, so
# list-layer-versions is denied here however the name is spelled. Version 29 is
# tried first because it is the one measured working in this account; the
# descending probe is the fallback for when AWS retires it.
echo ">> [3/5] AWSSDKPandas layer (supplies pyarrow)"
AWS_SDK_PANDAS_ACCT=336392948345
LAYER_ARN=""; RUNTIME=""
if [ -n "${SDK_PANDAS_LAYER_ARN:-}" ]; then
  LAYER_ARN="$SDK_PANDAS_LAYER_ARN"; RUNTIME="${SDK_PANDAS_RUNTIME:-python3.12}"
else
  SDK_PANDAS_KNOWN_VERSION=${SDK_PANDAS_KNOWN_VERSION:-29}
  for PYV in 312 311 313; do
    BASE=arn:aws:lambda:$REGION:$AWS_SDK_PANDAS_ACCT:layer:AWSSDKPandas-Python$PYV
    if aws lambda get-layer-version-by-arn --arn "$BASE:$SDK_PANDAS_KNOWN_VERSION" \
         --region "$REGION" --query 'LayerVersionArn' --output text >/dev/null 2>&1; then
      LAYER_ARN="$BASE:$SDK_PANDAS_KNOWN_VERSION"; RUNTIME="python3.${PYV:1}"; break
    fi
    for V in $(seq 40 -1 1); do
      if aws lambda get-layer-version-by-arn --arn "$BASE:$V" --region "$REGION" \
           --query 'LayerVersionArn' --output text >/dev/null 2>&1; then
        LAYER_ARN="$BASE:$V"; RUNTIME="python3.${PYV:1}"; break
      fi
    done
    [ -n "$LAYER_ARN" ] && break
  done
fi
if [ -z "$LAYER_ARN" ]; then
  echo "!! Could not resolve the AWSSDKPandas layer. Refusing to deploy with an"
  echo "   unresolved layer rather than sending the literal string 'None'."
  echo "   Retry as: SDK_PANDAS_LAYER_ARN=<arn> bash deploy.sh"
  exit 1
fi
echo "   $LAYER_ARN  ($RUNTIME)"

echo ">> [4/5] package + deploy (VPC)"
rm -rf build fn.zip && mkdir build && cp handler.py build/
pip3 install "pg8000>=1.31,<2" -t build/ -q
# duckdb supplies the zstd codec the managed pyarrow layer lacks.
#
# 29-Jul-2026. IT MUST BE BUILT FOR THE LAMBDA'S PYTHON, NOT CLOUDSHELL'S.
# duckdb is a BINARY wheel. A plain `pip3 install duckdb -t build/` in CloudShell
# picks the wheel for CloudShell's own interpreter, packages it, deploys clean --
# and then dies at import inside the Lambda with
#     No module named '_duckdb'
# The 22M zip looked like proof it had worked. pg8000 never showed this because
# it is pure Python and has no compiled extension to mismatch.
#
# The version is PINNED, not floated. duckdb moved its native extension between
# releases (duckdb/duckdb.cpython-*.so vs a top-level _duckdb), so a range would
# silently change the very file the assert below looks for.
PYVER="${RUNTIME#python}"                       # python3.12 -> 3.12
PYTAG="$(echo "$PYVER" | tr -d '.')"            # 3.12       -> 312
pip3 install "duckdb==1.2.2" -t build/ -q \
  --platform manylinux2014_x86_64 --python-version "$PYVER" --only-binary=:all:

# Refuse to ship a package whose native extension is for the wrong interpreter.
# This is the check whose absence cost a full deploy-and-invoke cycle.
if ! ls build/duckdb/duckdb.cpython-${PYTAG}-*.so >/dev/null 2>&1; then
  echo "!! duckdb native extension for $RUNTIME (cpython-$PYTAG) not in build/."
  ls build/duckdb/*.so 2>/dev/null || echo "   no .so at all"
  echo "   Refusing to deploy a package that would fail at import."
  exit 1
fi
echo "   duckdb: $(basename $(ls build/duckdb/duckdb.cpython-${PYTAG}-*.so))"
(cd build && zip -qr ../fn.zip .)
echo "   $(du -h fn.zip | cut -f1)"
SUBNET_CSV=$(echo $SUBNETS | tr ' ' ',')
ENVV="Variables={RDS_SECRET_ID=$SECRET_NAME,RDS_HOST=$RDS_HOST,ARTIFACT_BUCKET=$ARTIFACT_BUCKET,PS4_V3_ROOT=$PS4_V3_ROOT,CITY_ID=CHI}"
if aws lambda get-function --function-name "$FN" >/dev/null 2>&1; then
  aws lambda update-function-code --function-name "$FN" --zip-file fileb://fn.zip >/dev/null
  aws lambda wait function-updated --function-name "$FN"
  aws lambda update-function-configuration --function-name "$FN" --timeout 900 --memory-size 3008 \
    --layers "$LAYER_ARN" --vpc-config "SubnetIds=$SUBNET_CSV,SecurityGroupIds=$SG" --environment "$ENVV" >/dev/null
else
  [ "${NEW:-0}" = "1" ] && sleep 12
  aws lambda create-function --function-name "$FN" --runtime $RUNTIME --handler handler.lambda_handler \
    --role "$ROLE_ARN" --zip-file fileb://fn.zip --timeout 900 --memory-size 3008 \
    --layers "$LAYER_ARN" --vpc-config "SubnetIds=$SUBNET_CSV,SecurityGroupIds=$SG" --environment "$ENVV" >/dev/null
fi
aws lambda wait function-updated --function-name "$FN"
echo "   Lambda ready"

# Weekly, because the pipeline is weekly. Deliberately NOT an S3 notification on
# this pass: an ObjectCreated trigger would fire on the manifest AND on every
# parquet part, and the READY gate would only stop the incomplete ones. Add the
# notification once the weekly cadence is proven.
echo ">> [5/5] EventBridge weekly schedule (Mon 08:00 UTC)"
RULE=cubic-mars-ps4-v3-weekly
aws events put-rule --name $RULE --schedule-expression "cron(0 8 ? * MON *)" \
  --description "Weekly PS4 v3 outputs -> Aurora" --state ENABLED >/dev/null
aws lambda add-permission --function-name "$FN" --statement-id ${RULE}-invoke \
  --action lambda:InvokeFunction --principal events.amazonaws.com \
  --source-arn arn:aws:events:$REGION:$ACCT:rule/$RULE >/dev/null 2>&1 || true
aws events put-targets --rule $RULE \
  --targets "[{\"Id\":\"1\",\"Arn\":\"arn:aws:lambda:$REGION:$ACCT:function:$FN\",\"Input\":\"{\\\"action\\\":\\\"load\\\"}\"}]" >/dev/null
echo "   rule=$RULE"

echo "======================================================================"
echo " DONE. Next, IN THIS ORDER:"
echo "   1) aws lambda invoke --function-name $FN --cli-binary-format raw-in-base64-out \\"
echo "        --cli-read-timeout 0 --payload '{\"action\":\"dry_run\"}' /dev/stdout"
echo "   2) read rows_read and the unmapped block, then:"
echo "      aws lambda invoke --function-name $FN --cli-binary-format raw-in-base64-out \\"
echo "        --cli-read-timeout 0 --payload '{\"action\":\"load\"}' /dev/stdout"
echo "   3) aws lambda invoke --function-name $FN --cli-binary-format raw-in-base64-out \\"
echo "        --cli-read-timeout 0 --payload '{\"action\":\"verify\"}' /dev/stdout"
echo ""
echo " The OLD cubic-mars-ps4-rds-loader is untouched and still scheduled."
echo "======================================================================"
