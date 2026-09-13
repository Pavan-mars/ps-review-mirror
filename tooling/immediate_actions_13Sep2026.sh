#!/usr/bin/env bash
# =====================================================================
# immediate_actions_13Sep2026.sh -- the reversible fixes the 13-Sep live
# verification made urgent. Each step is independent; run with
#   DRY_RUN=0 STEP=1 bash tooling/immediate_actions_13Sep2026.sh
# (STEP=all runs 1-5). Every step captures current state first.
#
#  1  bastion RDP: replace 0.0.0.0/0 on sg-0b494063c98a88875 tcp/3389 with
#     ALLOW_CIDR (default: the VPC CIDR; pass your VPN client range).
#  2  PS4 loader: DISABLE rule cubic-mars-ps4-daily-load (times out 900 s x3
#     daily since 30-Aug on data unchanged since 29-Jul). Re-enable with
#     `aws events enable-rule` once the loader is scoped to the newest asof.
#  3  ECS service FrontEndDashboard-service: move to the scoped task SG
#     sg-006ac78c0d893a4c2, desiredCount 1, task definition TASKDEF
#     (default: current family latest). Stops serving from the default SG.
#  4  S3 lifecycle on the five datalake buckets: expire NON-CURRENT versions
#     after 7 days + clean expired delete markers + abort stale multiparts.
#     Does NOT touch current objects. (>=194 GB of dead versions measured.)
#  5  PS4 loader log tail: last stream, last 40 lines (read-only) so the
#     hang point is visible before the loader is fixed.
# =====================================================================
set -euo pipefail
export AWS_PAGER=""; R=us-east-1; ACCT=170202974600
DRY_RUN=${DRY_RUN:-1}; STEP=${STEP:-all}
ALLOW_CIDR=${ALLOW_CIDR:-10.231.88.0/21}
BASTION_SG=sg-0b494063c98a88875
TASK_SG=sg-006ac78c0d893a4c2
CLUSTER=cubic-mars-ecs-cluster-dev; SERVICE=FrontEndDashboard-service; FAMILY=FrontEndDashboard
TASKDEF=${TASKDEF:-$FAMILY}
BUCKETS=${BUCKETS:-"raw bronze silver gold artifacts"}   # step 4: e.g. BUCKETS="gold artifacts" first
OUT=tooling/out/immediate_$(date -u +%Y%m%dT%H%M%SZ); mkdir -p "$OUT"
want(){ [ "$STEP" = all ] || [ "$STEP" = "$1" ]; }
say(){ echo; echo "== $*"; }

if want 1; then
  say "1 bastion RDP rule (capture -> revoke 0.0.0.0/0 -> allow $ALLOW_CIDR)"
  aws ec2 describe-security-groups --group-ids $BASTION_SG --region $R --output json > "$OUT/bastion_sg_before.json"
  aws ec2 describe-security-groups --group-ids $BASTION_SG --region $R --query 'SecurityGroups[0].IpPermissions' --output json
  if [ "$DRY_RUN" = 0 ]; then
    aws ec2 authorize-security-group-ingress --group-id $BASTION_SG --region $R --protocol tcp --port 3389 --cidr "$ALLOW_CIDR" >/dev/null 2>&1 || echo "  (allow rule already present)"
    aws ec2 revoke-security-group-ingress --group-id $BASTION_SG --region $R --protocol tcp --port 3389 --cidr 0.0.0.0/0 && echo "  revoked 0.0.0.0/0"
    aws ec2 describe-security-groups --group-ids $BASTION_SG --region $R --query 'SecurityGroups[0].IpPermissions' --output json
  else echo "  DRY_RUN: would allow tcp/3389 from $ALLOW_CIDR then revoke tcp/3389 from 0.0.0.0/0"; fi
fi

if want 2; then
  say "2 disable cubic-mars-ps4-daily-load"
  aws events describe-rule --name cubic-mars-ps4-daily-load --region $R --output json | tee "$OUT/ps4_rule_before.json" | grep -E '"State"|"ScheduleExpression"'
  if [ "$DRY_RUN" = 0 ]; then aws events disable-rule --name cubic-mars-ps4-daily-load --region $R && echo "  DISABLED (re-enable: aws events enable-rule --name cubic-mars-ps4-daily-load)"; else echo "  DRY_RUN: would disable"; fi
fi

if want 3; then
  say "3 ECS service -> scoped SG $TASK_SG, desiredCount 1, taskdef $TASKDEF"
  aws ecs describe-services --cluster $CLUSTER --services $SERVICE --region $R --output json > "$OUT/service_before.json"
  NC=$(aws ecs describe-services --cluster $CLUSTER --services $SERVICE --region $R --query 'services[0].networkConfiguration' --output json | python3 -c "import sys,json;d=json.load(sys.stdin);d['awsvpcConfiguration']['securityGroups']=['$TASK_SG'];print(json.dumps(d))")
  echo "  network config to apply: $NC"
  if [ "$DRY_RUN" = 0 ]; then
    aws ecs update-service --cluster $CLUSTER --service $SERVICE --region $R --task-definition "$TASKDEF" --desired-count 1 --network-configuration "$NC" --force-new-deployment \
      --query 'service.{desired:desiredCount,td:taskDefinition,sg:networkConfiguration.awsvpcConfiguration.securityGroups}' --output json
    echo "  rolling; watch: aws ecs describe-services --cluster $CLUSTER --services $SERVICE --query 'services[0].deployments'"
    echo "  then stop the orphan run-task if still present: aws ecs list-tasks --cluster $CLUSTER --desired-status RUNNING"
  else echo "  DRY_RUN: would update-service (taskdef $TASKDEF, desired 1, SG $TASK_SG, force new deployment)"; fi
fi

if want 4; then
  say "4 lifecycle: noncurrent versions 7d, expired delete markers, abort multipart 7d (current objects untouched)"
  cat > "$OUT/lifecycle.json" <<'JSON'
{"Rules":[{"ID":"expire-noncurrent-versions-7d","Status":"Enabled","Filter":{"Prefix":""},
  "NoncurrentVersionExpiration":{"NoncurrentDays":7},
  "Expiration":{"ExpiredObjectDeleteMarker":true},
  "AbortIncompleteMultipartUpload":{"DaysAfterInitiation":7}}]}
JSON
  for B in $BUCKETS; do
    BK=cubic-mars-pm-s3-datalake-dev-$B-$ACCT
    aws s3api get-bucket-lifecycle-configuration --bucket $BK 2>/dev/null > "$OUT/lifecycle_before_$B.json" || echo "  $BK: no lifecycle today"
    if [ "$DRY_RUN" = 0 ]; then aws s3api put-bucket-lifecycle-configuration --bucket $BK --lifecycle-configuration file://"$OUT/lifecycle.json" && echo "  $BK: lifecycle set"; else echo "  DRY_RUN: would put lifecycle on $BK"; fi
  done
  echo "  NOTE: bronze/silver/gold __unitystorage roots are UC-managed Delta; expiring NON-CURRENT S3 versions after 7 d is safe (Delta time travel uses current objects + its own log retention), but confirm the UC metastore has no S3-versioning dependency before DRY_RUN=0 on those three buckets."
fi

if want 5; then
  say "5 PS4 loader: last log stream tail (read-only)"
  LG=/aws/lambda/cubic-mars-ps4-rds-loader
  LS=$(aws logs describe-log-streams --log-group-name $LG --region $R --order-by LastEventTime --descending --max-items 1 --query 'logStreams[0].logStreamName' --output text)
  echo "  stream: $LS"
  aws logs get-log-events --log-group-name $LG --log-stream-name "$LS" --region $R --limit 40 --query 'events[].message' --output text | cut -c1-300
fi
echo; echo "captures in $OUT"
