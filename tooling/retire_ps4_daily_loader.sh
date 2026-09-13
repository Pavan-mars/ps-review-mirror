#!/usr/bin/env bash
# =====================================================================
# retire_ps4_daily_loader.sh -- PS4 runs WEEKLY; the daily loader path goes.
#
# Decision (PK, 13-Sep-2026). The daily loader cubic-mars-ps4-rds-loader
# never completed a run after 28-Jul and no dashboard route read its
# tables; the weekly v3 loader is the PS4 product. Its rule was disabled
# 13-Sep 13:50 UTC. This script captures everything, then deletes:
#   1 EventBridge rule cubic-mars-ps4-daily-load (targets first)
#   2 the artifacts-bucket S3 notification entry ps4-scored-manifest-load
#     (GET -> remove that one entry -> PUT; every other entry preserved)
#   3 the Lambda function (code zip captured first)
#   4 the role cubic-mars-ps4-rds-loader-role-dev (inline policies first)
# It does NOT touch the five Aurora tables, the S3 data prefixes, or the
# weekly loader. Dropping the tables follows the R1 order later.
#
#   DRY_RUN=1 (default) prints the plan.   DRY_RUN=0 executes.
# =====================================================================
set -euo pipefail
export AWS_PAGER=""; R=us-east-1; ACCT=170202974600
FN=cubic-mars-ps4-rds-loader; RULE=cubic-mars-ps4-daily-load; ROLE=cubic-mars-ps4-rds-loader-role-dev
ART=cubic-mars-pm-s3-datalake-dev-artifacts-$ACCT; NOTIF_ID=ps4-scored-manifest-load
DRY_RUN=${DRY_RUN:-1}
OUT=tooling/out/ps4_daily_retire_$(date -u +%Y%m%dT%H%M%SZ); mkdir -p "$OUT"
command -v jq >/dev/null || { echo "jq required"; exit 2; }

echo "== capture -> $OUT"
aws events describe-rule --name $RULE --region $R --output json > "$OUT/rule.json" 2>/dev/null || echo "  rule absent"
aws events list-targets-by-rule --rule $RULE --region $R --output json > "$OUT/targets.json" 2>/dev/null || true
aws lambda get-function-configuration --function-name $FN --region $R --output json > "$OUT/function_config.json" 2>/dev/null || echo "  function absent"
URL=$(aws lambda get-function --function-name $FN --region $R --query 'Code.Location' --output text 2>/dev/null || true)
[ -n "$URL" ] && [ "$URL" != "None" ] && curl -s -o "$OUT/function_code.zip" "$URL" && echo "  code zip: $(wc -c < "$OUT/function_code.zip") bytes"
aws lambda get-policy --function-name $FN --region $R --query Policy --output text > "$OUT/function_policy.json" 2>/dev/null || true
aws iam get-role --role-name $ROLE --output json > "$OUT/role.json" 2>/dev/null || echo "  role absent"
for P in $(aws iam list-role-policies --role-name $ROLE --query 'PolicyNames[]' --output text 2>/dev/null); do
  aws iam get-role-policy --role-name $ROLE --policy-name $P --output json > "$OUT/role_policy_$P.json"; done
aws iam list-attached-role-policies --role-name $ROLE --output json > "$OUT/role_attached.json" 2>/dev/null || true
aws s3api get-bucket-notification-configuration --bucket $ART --output json > "$OUT/notification_before.json"
echo "  notification entries now: $(jq -r '.LambdaFunctionConfigurations[]?.Id' "$OUT/notification_before.json" | tr '\n' ' ')"
NEW=$(jq --arg id "$NOTIF_ID" '.LambdaFunctionConfigurations = [.LambdaFunctionConfigurations[]? | select(.Id != $id)]' "$OUT/notification_before.json")
echo "$NEW" > "$OUT/notification_after.json"
echo "  notification entries after: $(jq -r '.LambdaFunctionConfigurations[]?.Id' "$OUT/notification_after.json" | tr '\n' ' ')"

if [ "$DRY_RUN" != 0 ]; then
  echo "DRY_RUN: would delete rule $RULE (+targets), PUT the notification config WITHOUT $NOTIF_ID, delete function $FN, delete role $ROLE. Re-run with DRY_RUN=0."
  exit 0
fi

echo "== 1 rule"
IDS=$(aws events list-targets-by-rule --rule $RULE --region $R --query 'Targets[].Id' --output text 2>/dev/null || true)
[ -n "$IDS" ] && aws events remove-targets --rule $RULE --ids $IDS --region $R >/dev/null
aws events delete-rule --name $RULE --region $R 2>/dev/null && echo "  deleted $RULE" || echo "  rule already absent"

echo "== 2 S3 notification (PUT replaces the whole document -- the merged one keeps every other entry)"
aws s3api put-bucket-notification-configuration --bucket $ART --notification-configuration file://"$OUT/notification_after.json"
aws s3api get-bucket-notification-configuration --bucket $ART --output json | jq -r '.LambdaFunctionConfigurations[]?.Id' | sed 's/^/  now: /'

echo "== 3 function"
aws lambda delete-function --function-name $FN --region $R && echo "  deleted $FN"

echo "== 4 role"
for P in $(aws iam list-role-policies --role-name $ROLE --query 'PolicyNames[]' --output text 2>/dev/null); do aws iam delete-role-policy --role-name $ROLE --policy-name $P; done
for A in $(aws iam list-attached-role-policies --role-name $ROLE --query 'AttachedPolicies[].PolicyArn' --output text 2>/dev/null); do aws iam detach-role-policy --role-name $ROLE --policy-arn $A; done
aws iam delete-role --role-name $ROLE && echo "  deleted $ROLE"

echo "== left in place on purpose: Aurora ps4_anomaly_timeline / ps4_cluster_assignments / ps4_cluster_summary / ps4_device_day / ps4_device_lifetime (drop via R1 order: remove CREATE from sql/25 + sql/33, adjust migrate(), deploy, DROP); S3 chicago/ps4/scored + chicago/ps4/clustering (register: HOLD -> lifecycle); the weekly loader cubic-mars-ps4-v3-loader and its Monday rule."
echo "captures in $OUT"
