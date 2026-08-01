#!/usr/bin/env bash
# cubic-mars-ps3-v2-loader -- deploy from AWS CloudShell (us-east-1)
# NEW function. Shares nothing with the loaders that fill the existing PS3
# tables; deleting it is a complete rollback.
set -euo pipefail
REGION=us-east-1
FN=cubic-mars-ps3-v2-loader
ROLE=cubic-mars-ps3-v2-loader-role-dev
SGNAME=cubic-mars-dashboard-api-sg
VPC=vpc-0a7775adc7d382fbb
SUBNETS="subnet-0830633f6cab1b8a1 subnet-01ad20b3b49bf59de subnet-0cd6e4bca78eaab8d"
RDS_HOST=cubic-mars-rds-aurora-dev.cluster-cgdk4y4ewxzi.us-east-1.rds.amazonaws.com
SECRET_NAME=cubic-mars-secret-rds-dev
PS3_BUCKET=cubic-mars-pm-s3-datalake-dev-gold-170202974600
PS3_ROOT=chicago/ps3_hardened_remediation/runs
ACCT=$(aws sts get-caller-identity --query Account --output text)
SECRET_ARN=$(aws secretsmanager describe-secret --secret-id "$SECRET_NAME" --query ARN --output text)
echo ">> [1/4] IAM role"
if ! aws iam get-role --role-name "$ROLE" >/dev/null 2>&1; then
  aws iam create-role --role-name "$ROLE" --assume-role-policy-document '{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"lambda.amazonaws.com"},"Action":"sts:AssumeRole"}]}' >/dev/null
  NEW=1
fi
aws iam attach-role-policy --role-name "$ROLE" --policy-arn arn:aws:iam::aws:policy/service-role/AWSLambdaVPCAccessExecutionRole
aws iam put-role-policy --role-name "$ROLE" --policy-name ps3-v2-loader-inline --policy-document "{
  \"Version\":\"2012-10-17\",\"Statement\":[
    {\"Effect\":\"Allow\",\"Action\":\"secretsmanager:GetSecretValue\",\"Resource\":\"$SECRET_ARN\"},
    {\"Effect\":\"Allow\",\"Action\":[\"s3:GetObject\",\"s3:ListBucket\"],
     \"Resource\":[\"arn:aws:s3:::$PS3_BUCKET\",\"arn:aws:s3:::$PS3_BUCKET/$PS3_ROOT/*\"]}]}"
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
rm -rf build fn.zip && mkdir build && cp handler.py spec.json build/
pip3 install "pg8000>=1.31,<2" -t build/ -q
# duckdb MUST be built for the LAMBDA's python, not CloudShell's -- a wheel for
# the wrong interpreter deploys clean then dies with No module named '_duckdb'.
PYVER="${RUNTIME#python}"; PYTAG="$(echo "$PYVER" | tr -d '.')"
pip3 install "duckdb==1.2.2" -t build/ -q --platform manylinux2014_x86_64 --python-version "$PYVER" --only-binary=:all:
if ! ls build/duckdb/duckdb.cpython-${PYTAG}-*.so >/dev/null 2>&1; then
  echo "!! duckdb native extension for $RUNTIME missing -- refusing to deploy"; exit 1; fi
echo "   duckdb: $(basename $(ls build/duckdb/duckdb.cpython-${PYTAG}-*.so))"
(cd build && zip -qr ../fn.zip .); echo "   $(du -h fn.zip | cut -f1)"
echo ">> [4/4] deploy"
SUBNET_CSV=$(echo $SUBNETS | tr ' ' ',')
ENVV="Variables={RDS_SECRET_ID=$SECRET_NAME,RDS_HOST=$RDS_HOST,PS3_BUCKET=$PS3_BUCKET,PS3_ROOT=$PS3_ROOT,CITY_ID=CHI}"
if aws lambda get-function --function-name "$FN" >/dev/null 2>&1; then
  aws lambda update-function-code --function-name "$FN" --zip-file fileb://fn.zip >/dev/null
  aws lambda wait function-updated --function-name "$FN"
  aws lambda update-function-configuration --function-name "$FN" --timeout 900 --memory-size 3008 --layers "$LAYER_ARN" --vpc-config "SubnetIds=$SUBNET_CSV,SecurityGroupIds=$SG" --environment "$ENVV" >/dev/null
else
  [ "${NEW:-0}" = "1" ] && sleep 12
  aws lambda create-function --function-name "$FN" --runtime $RUNTIME --handler handler.lambda_handler --role "$ROLE_ARN" --zip-file fileb://fn.zip --timeout 900 --memory-size 3008 --layers "$LAYER_ARN" --vpc-config "SubnetIds=$SUBNET_CSV,SecurityGroupIds=$SG" --environment "$ENVV" >/dev/null
fi
aws lambda wait function-updated --function-name "$FN"
echo "======================================================================"
echo " DONE. Next:"
echo "   aws lambda invoke --function-name $FN --cli-binary-format raw-in-base64-out \\"
echo "     --cli-read-timeout 0 --payload '{\"action\":\"dry_run\"}' /dev/stdout"
echo "   then action=load, then action=verify"
echo " The existing PS3 tables and loaders are untouched."
echo "======================================================================"
