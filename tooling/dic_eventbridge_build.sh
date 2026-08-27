#!/usr/bin/env bash
# =====================================================================
# EventBridge trigger for cubic-mars-dim-incident-loader.
#
# Mirrors the rule that already drives cubic-mars-dim-loader
# (cubic-mars-dim-daily-refresh, cron(45 5 * * ? *), Input "{}") so the two
# dimension loaders behave identically and one runbook covers both.
#
# CREATED DISABLED, DELIBERATELY. Two reasons, both measured:
#   * The Databricks producer (dim_incident_cmdb_daily) is PAUSED, so nothing
#     writes a new manifest -- an enabled rule would re-load the same snapshot
#     every morning and make a frozen pipeline look like a live one.
#   * Both upstreams ARE frozen. The device spine sits at the 11-Apr-2026
#     Oracle vintage, and the ServiceNow side stops at 30-May-2026 (measured
#     28-Aug across the ten highest-ticket devices: every latest ticket falls
#     between 22 and 30 May, nothing after). Until at least one of those moves,
#     a daily run has nothing new to read.
#
# 06:15 UTC, thirty minutes after the Databricks job's 05:15 UTC slot and
# inside the existing 05:45-08:23 loader window, so it lands with the others
# rather than opening a new operational hour.
#
# ENABLE ORDER, when the feeds move:
#   1. Unpause dim_incident_cmdb_daily in databricks.yml, deploy, let it run.
#   2. Confirm a NEW as_of_date lands under chicago/dim/device_incident_cmdb/.
#   3. Invoke the loader by hand once against it; check rows_after.
#   4. Only then:  aws events enable-rule --name cubic-mars-dic-daily-refresh
#
# Idempotent: re-running updates the rule and target in place. It never
# enables a rule -- enabling is a separate, deliberate command.
# =====================================================================
set -euo pipefail
REGION=us-east-1
RULE=cubic-mars-dic-daily-refresh
FN=cubic-mars-dim-incident-loader
SCHEDULE="cron(15 6 * * ? *)"
ACCT=$(aws sts get-caller-identity --query Account --output text)
FN_ARN="arn:aws:lambda:$REGION:$ACCT:function:$FN"

echo ">> [1/4] the target Lambda must exist before a rule points at it"
aws lambda get-function --function-name "$FN" --region "$REGION" \
  --query 'Configuration.{name:FunctionName,modified:LastModified}' --output text

echo ">> [2/4] put rule (DISABLED)"
aws events put-rule --name "$RULE" --region "$REGION" \
  --schedule-expression "$SCHEDULE" \
  --state DISABLED \
  --description "Daily device-incident-CMDB dimension load -> Aurora (disabled until the upstream feeds move)" \
  --query 'RuleArn' --output text

echo ">> [3/4] allow EventBridge to invoke the function"
aws lambda add-permission --function-name "$FN" --region "$REGION" \
  --statement-id "${RULE}-invoke" \
  --action lambda:InvokeFunction \
  --principal events.amazonaws.com \
  --source-arn "arn:aws:events:$REGION:$ACCT:rule/$RULE" >/dev/null 2>&1 \
  && echo "   permission added" || echo "   permission already present"

echo ">> [4/4] put target (empty Input = load the newest manifest, same as dim-loader)"
aws events put-targets --rule "$RULE" --region "$REGION" \
  --targets "Id=1,Arn=$FN_ARN,Input='{}'" \
  --query 'FailedEntryCount' --output text

echo
echo "state and target, read back:"
aws events describe-rule --name "$RULE" --region "$REGION" \
  --query '{name:Name,state:State,schedule:ScheduleExpression}' --output table
aws events list-targets-by-rule --rule "$RULE" --region "$REGION" \
  --query 'Targets[].{id:Id,arn:Arn,input:Input}' --output table
echo
echo "Rule is DISABLED. FailedEntryCount above must read 0."
echo "Do not enable it until the enable order in this file's header is done."
