#!/usr/bin/env bash
# CUBIC MARS -- PS2/PS1/PS3/PS5 dashboard -- ECS Fargate go-live runbook.
#
# This is a RUNBOOK, not an idempotent automation -- run each phase yourself
# in CloudShell (or your terminal with the AdminRole SSO session active),
# reading the output before moving to the next phase. Fill in every
# REPLACE_ME before running the command it appears in.
#
# Known-live facts this runbook assumes (CloudShell-confirmed 2026-07-2x):
#   Account 170202974600, region us-east-1
#   VPC vpc-0a7775adc7d382fbb
#   Private subnets: subnet-0830633f6cab1b8a1 subnet-01ad20b3b49bf59de subnet-0cd6e4bca78eaab8d
#   ECS cluster: cubic-mars-ecs-cluster-dev (exists; services unconfirmed)
#   API GW (already live, dashboard already points at it): https://a9yuqt9j9b.execute-api.us-east-1.amazonaws.com
set -euo pipefail
export AWS_PAGER=""
REGION=us-east-1
VPC_ID=vpc-0a7775adc7d382fbb
SUBNETS="subnet-0830633f6cab1b8a1 subnet-01ad20b3b49bf59de subnet-0cd6e4bca78eaab8d"
CLUSTER=cubic-mars-ecs-cluster-dev

# =====================================================================
# PHASE 0 -- discovery (read-only). Run this FIRST -- every later phase
# depends on an answer from here. Nothing here changes any AWS state.
# =====================================================================
echo "--- ECS cluster capacity + existing services ---"
aws ecs describe-clusters --clusters "$CLUSTER" --include ATTACHMENTS --region $REGION
aws ecs list-services --cluster "$CLUSTER" --region $REGION

echo "--- existing ALBs (is there already an internal ALB to reuse?) ---"
aws elbv2 describe-load-balancers --region $REGION \
  --query 'LoadBalancers[].{Name:LoadBalancerName,Scheme:Scheme,DNS:DNSName,VpcId:VpcId}'

echo "--- Cognito user pools (does SAML/IdP auth already exist?) ---"
aws cognito-idp list-user-pools --max-results 20 --region $REGION

echo "--- ACM certs in this region (needed for an HTTPS ALB listener) ---"
aws acm list-certificates --region $REGION \
  --query 'CertificateSummaryList[].{Domain:DomainName,Arn:CertificateArn,Status:Status}'

echo "--- existing ECR repos (reuse cubic-pdm/* naming?) ---"
aws ecr describe-repositories --region $REGION --query 'repositories[].repositoryName'

# ACTION: before Phase 1, confirm with PK/network team the VPN / corporate
# CIDR that should be allowed to reach the internal ALB (README says
# "VPN-only" -- do NOT default this to 0.0.0.0/0). Set it here:
VPN_CIDR="REPLACE_ME_e.g._10.x.x.x/22"

# =====================================================================
# PHASE 1 -- ECR repo + build/push the image (from a machine with Docker,
# e.g. CloudShell or PK's own machine -- NOT the Linux device-bridge VM,
# which has a platform-mismatched node_modules for this exact reason).
# =====================================================================
ECR_REPO=cubic-pdm/ps2-dashboard
aws ecr create-repository --repository-name "$ECR_REPO" --region $REGION \
  --image-scanning-configuration scanOnPush=true \
  --encryption-configuration encryptionType=KMS || true

ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
ECR_URI="$ACCOUNT_ID.dkr.ecr.$REGION.amazonaws.com/$ECR_REPO"
aws ecr get-login-password --region $REGION | docker login --username AWS --password-stdin "$ACCOUNT_ID.dkr.ecr.$REGION.amazonaws.com"

# run from the dashboard/ directory (has the Dockerfile written this session)
IMAGE_TAG=$(date +%Y%m%d-%H%M)   # or a git short-SHA once this is committed
docker build \
  --build-arg VITE_API_BASE_URL=https://a9yuqt9j9b.execute-api.us-east-1.amazonaws.com \
  -t "$ECR_URI:$IMAGE_TAG" -t "$ECR_URI:latest" .
docker push "$ECR_URI:$IMAGE_TAG"
docker push "$ECR_URI:latest"

# =====================================================================
# PHASE 2 -- IAM roles (skip if these generic ECS roles already exist --
# check first: aws iam get-role --role-name ecsTaskExecutionRole)
# =====================================================================
# Execution role only needs ECR pull + CloudWatch Logs -- AWS's managed
# policy covers it. The task role can be empty/minimal: this container
# never calls an AWS API itself (all data comes from the public-inside-VPC
# HTTP API GW over plain HTTPS), so don't over-grant it.
aws iam create-role --role-name ecsTaskExecutionRole \
  --assume-role-policy-document '{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"ecs-tasks.amazonaws.com"},"Action":"sts:AssumeRole"}]}' || true
aws iam attach-role-policy --role-name ecsTaskExecutionRole \
  --policy-arn arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy || true

# =====================================================================
# PHASE 3 -- security groups
# =====================================================================
ALB_SG=$(aws ec2 create-security-group --group-name cubic-mars-ps2-dashboard-alb-sg \
  --description "Internal ALB for PS2 dashboard -- VPN CIDR only" --vpc-id $VPC_ID \
  --region $REGION --query GroupId --output text)
aws ec2 authorize-security-group-ingress --group-id "$ALB_SG" --protocol tcp --port 443 \
  --cidr "$VPN_CIDR" --region $REGION
aws ec2 authorize-security-group-ingress --group-id "$ALB_SG" --protocol tcp --port 80 \
  --cidr "$VPN_CIDR" --region $REGION   # only if you want an http->https redirect listener

TASK_SG=$(aws ec2 create-security-group --group-name cubic-mars-ps2-dashboard-task-sg \
  --description "PS2 dashboard Fargate tasks -- ALB only" --vpc-id $VPC_ID \
  --region $REGION --query GroupId --output text)
aws ec2 authorize-security-group-ingress --group-id "$TASK_SG" --protocol tcp --port 8080 \
  --source-group "$ALB_SG" --region $REGION

# =====================================================================
# PHASE 4 -- internal ALB + target group (+ Cognito auth if a user pool
# already exists from Phase 0 discovery -- fill in ARNs; if no pool
# exists yet, that's a separate provisioning step, don't skip it silently)
# =====================================================================
ALB_ARN=$(aws elbv2 create-load-balancer --name cubic-mars-ps2-dashboard-alb \
  --scheme internal --type application --subnets $SUBNETS --security-groups "$ALB_SG" \
  --region $REGION --query 'LoadBalancers[0].LoadBalancerArn' --output text)

TG_ARN=$(aws elbv2 create-target-group --name cubic-mars-ps2-dashboard-tg \
  --protocol HTTP --port 8080 --vpc-id $VPC_ID --target-type ip \
  --health-check-path /healthz --health-check-interval-seconds 15 \
  --region $REGION --query 'TargetGroups[0].TargetGroupArn' --output text)

CERT_ARN="REPLACE_ME_from_Phase_0_ACM_output"
COGNITO_USER_POOL_ARN="REPLACE_ME_from_Phase_0_or_new_pool"
COGNITO_CLIENT_ID="REPLACE_ME"
COGNITO_DOMAIN="REPLACE_ME"

# HTTPS listener with a Cognito authenticate action gating the forward --
# this is what makes the ALB itself enforce SAML/Cognito login before any
# request reaches the container (matches the README's "Cognito SAML" line).
aws elbv2 create-listener --load-balancer-arn "$ALB_ARN" --protocol HTTPS --port 443 \
  --certificates CertificateArn="$CERT_ARN" \
  --default-actions '[
    {"Type":"authenticate-cognito","Order":1,"AuthenticateCognitoConfig":{
      "UserPoolArn":"'"$COGNITO_USER_POOL_ARN"'",
      "UserPoolClientId":"'"$COGNITO_CLIENT_ID"'",
      "UserPoolDomain":"'"$COGNITO_DOMAIN"'"}},
    {"Type":"forward","Order":2,"TargetGroupArn":"'"$TG_ARN"'"}
  ]' --region $REGION

# =====================================================================
# PHASE 5 -- register task def + create the ECS service
# =====================================================================
# fill in infrastructure/app_spoke/ecs-task-def.dashboard.json's REPLACE_*
# fields (execution role ARN, ECR image URI:tag from Phase 1) before this:
aws ecs register-task-definition \
  --cli-input-json file://infrastructure/app_spoke/ecs-task-def.dashboard.json \
  --region $REGION

SUBNETS_CSV=$(echo $SUBNETS | tr ' ' ',')
aws ecs create-service --cluster "$CLUSTER" --service-name ps2-dashboard \
  --task-definition cubic-mars-ps2-dashboard --desired-count 2 --launch-type FARGATE \
  --network-configuration "awsvpcConfiguration={subnets=[$SUBNETS_CSV],securityGroups=[$TASK_SG],assignPublicIp=DISABLED}" \
  --load-balancers "targetGroupArn=$TG_ARN,containerName=dashboard,containerPort=8080" \
  --deployment-configuration "deploymentCircuitBreaker={enable=true,rollback=true},maximumPercent=200,minimumHealthyPercent=100" \
  --region $REGION

# =====================================================================
# PHASE 6 -- verify (definition of "deployed for real" per the tracker's
# refresh SOP: endpoint reachable + real data on screen, not just "up")
# =====================================================================
echo "--- wait for service stable, then check target health ---"
aws ecs wait services-stable --cluster "$CLUSTER" --services ps2-dashboard --region $REGION
aws elbv2 describe-target-health --target-group-arn "$TG_ARN" --region $REGION
echo "ALB DNS: $(aws elbv2 describe-load-balancers --load-balancer-arns $ALB_ARN --region $REGION --query 'LoadBalancers[0].DNSName' --output text)"
# Then from a VPN-connected machine: open https://<alb-dns>, sign in via
# Cognito, and confirm each PS1/PS2/PS3/PS5 tab renders real numbers (not
# a blank panel / thrown ApiError toast -- that IS the live-only pattern
# working as designed if VITE_API_BASE_URL or the API GW is misconfigured).
