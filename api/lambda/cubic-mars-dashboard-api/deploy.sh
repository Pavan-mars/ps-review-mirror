#!/usr/bin/env bash
# =====================================================================
# CUBIC MARS Chicago — Phase-1 deploy (run INSIDE AWS CloudShell, us-east-1)
# Creates the cubic-mars-dashboard-api VPC Lambda, runs the DB migration
# (schema + PS2/PS5 backfill) against the private Aurora, and stands up an
# HTTP API Gateway. Idempotent — safe to re-run.
# All resource IDs below were confirmed by the 2026-07-12 live audit.
# =====================================================================
set -euo pipefail
REGION=us-east-1
FN=cubic-mars-dashboard-api
ROLE=cubic-mars-dashboard-api-role-dev
SGNAME=cubic-mars-dashboard-api-sg
VPC=vpc-0a7775adc7d382fbb
SUBNETS="subnet-0830633f6cab1b8a1 subnet-01ad20b3b49bf59de subnet-0cd6e4bca78eaab8d"   # 3 private (NAT egress)
RDS_HOST=cubic-mars-rds-aurora-dev.cluster-cgdk4y4ewxzi.us-east-1.rds.amazonaws.com
SECRET_NAME=cubic-mars-secret-rds-dev
DB_NAME="${DB_NAME:-postgres}"     # override: DB_NAME=cubic_mars bash deploy.sh
ACCT=$(aws sts get-caller-identity --query Account --output text)
SECRET_ARN=$(aws secretsmanager describe-secret --secret-id "$SECRET_NAME" --query ARN --output text)
echo ">> Account=$ACCT  Region=$REGION  DB=$DB_NAME"

echo ">> [1/6] IAM role"
if ! aws iam get-role --role-name "$ROLE" >/dev/null 2>&1; then
  aws iam create-role --role-name "$ROLE" \
    --assume-role-policy-document '{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"lambda.amazonaws.com"},"Action":"sts:AssumeRole"}]}' >/dev/null
  NEWROLE=1
fi
aws iam attach-role-policy --role-name "$ROLE" --policy-arn arn:aws:iam::aws:policy/service-role/AWSLambdaVPCAccessExecutionRole
aws iam put-role-policy --role-name "$ROLE" --policy-name read-rds-secret \
  --policy-document "{\"Version\":\"2012-10-17\",\"Statement\":[{\"Effect\":\"Allow\",\"Action\":\"secretsmanager:GetSecretValue\",\"Resource\":\"$SECRET_ARN\"}]}"
ROLE_ARN=arn:aws:iam::$ACCT:role/$ROLE

echo ">> [2/6] security group (egress-only; Aurora SG already allows the VPC CIDR)"
SG=$(aws ec2 describe-security-groups --filters Name=group-name,Values=$SGNAME Name=vpc-id,Values=$VPC --query "SecurityGroups[0].GroupId" --output text 2>/dev/null || true)
if [ "$SG" = "None" ] || [ -z "$SG" ]; then
  SG=$(aws ec2 create-security-group --group-name $SGNAME --description "cubic-mars dashboard-api lambda" --vpc-id $VPC --query GroupId --output text)
fi
echo "   SG=$SG"

echo ">> [3/6] package (handler + sql + pg8000)"
rm -rf build fn.zip && mkdir build
cp handler.py build/ && cp -r sql build/
pip3 install pg8000 -t build/ -q
(cd build && zip -qr ../fn.zip .)
echo "   $(du -h fn.zip | cut -f1) zip"

echo ">> [4/6] create/update Lambda (VPC)"
SUBNET_CSV=$(echo $SUBNETS | tr ' ' ',')
ENVVARS="Variables={SECRET_ARN=$SECRET_ARN,RDS_HOST=$RDS_HOST,DB_NAME=$DB_NAME}"
if aws lambda get-function --function-name "$FN" >/dev/null 2>&1; then
  aws lambda update-function-code --function-name "$FN" --zip-file fileb://fn.zip >/dev/null
  aws lambda wait function-updated --function-name "$FN"
  aws lambda update-function-configuration --function-name "$FN" --timeout 120 --memory-size 256 \
    --vpc-config "SubnetIds=$SUBNET_CSV,SecurityGroupIds=$SG" --environment "$ENVVARS" >/dev/null
else
  sleep 12   # allow new role to become assumable
  aws lambda create-function --function-name "$FN" --runtime python3.12 --handler handler.lambda_handler \
    --role "$ROLE_ARN" --zip-file fileb://fn.zip --timeout 120 --memory-size 256 \
    --vpc-config "SubnetIds=$SUBNET_CSV,SecurityGroupIds=$SG" --environment "$ENVVARS" >/dev/null
fi
aws lambda wait function-active --function-name "$FN"
aws lambda wait function-updated --function-name "$FN" 2>/dev/null || true
echo "   Lambda active."

echo ">> [5/6] run DB migration (schema 01-10: Phase-1e (07) + Phase-1f rich DDL (09) + optional notebook backfills (08,10)) via the Lambda (in-VPC)"
aws lambda invoke --function-name "$FN" --cli-binary-format raw-in-base64-out --payload '{"action":"migrate"}' migrate_out.json >/dev/null
echo "   --- migrate result ---"; cat migrate_out.json; echo

echo ">> [6/6] HTTP API Gateway"
API_ID=$(aws apigatewayv2 get-apis --query "Items[?Name=='$FN'].ApiId | [0]" --output text)
if [ "$API_ID" = "None" ] || [ -z "$API_ID" ]; then
  API_ID=$(aws apigatewayv2 create-api --name "$FN" --protocol-type HTTP \
    --target "arn:aws:lambda:$REGION:$ACCT:function:$FN" --query ApiId --output text)
fi
aws lambda add-permission --function-name "$FN" --statement-id apigw-invoke \
  --action lambda:InvokeFunction --principal apigateway.amazonaws.com \
  --source-arn "arn:aws:execute-api:$REGION:$ACCT:$API_ID/*/*" >/dev/null 2>&1 || true
APIURL=$(aws apigatewayv2 get-api --api-id "$API_ID" --query ApiEndpoint --output text)
echo "======================================================================"
echo " DONE.  API base URL:  $APIURL"
echo " Smoke tests (incl. Phase-1d device-level PS2/PS3):"
echo "   curl \"$APIURL/ps2/windows?city=CHI\""
echo "   curl \"$APIURL/ps2/topdevices?city=CHI\"      # NEW: top-20 cascade devices"
echo "   curl \"$APIURL/ps2/windowdetail?city=CHI\"    # NEW: chain-len/span/velocity"
echo "   curl \"$APIURL/ps3/devices?city=CHI\"         # per-device TVM/Gates severity"
echo "   curl \"$APIURL/ps2/paths?city=CHI\"          # NEW 1e: top cascade paths (Markov)"
echo "   curl \"$APIURL/ps2/ignition?city=CHI\"       # NEW 1e: ignition -> termination"
echo "   curl \"$APIURL/ps2/impact?city=CHI\"         # NEW 1e: device business-impact ranking"
echo "   curl \"$APIURL/ps2/devices?city=CHI\"        # NEW 1f: device catalog (type/id/serial/error code)"
echo "   curl \"$APIURL/ps2/errorcodes?city=CHI\"     # NEW 1f: error codes + transitions"
echo "   curl \"$APIURL/ps2/phi?city=CHI\"            # NEW 1f: subsystem correlation matrix"
echo "   curl \"$APIURL/ps2/network?city=CHI\"        # NEW 1f: cascade network centrality"
echo "   curl \"$APIURL/ps5/status?city=CHI\""
echo " (.env already has VITE_API_BASE_URL — npm run dev shows the live device-level tabs)"
echo "======================================================================"
