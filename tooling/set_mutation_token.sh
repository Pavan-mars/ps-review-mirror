#!/usr/bin/env bash
# =====================================================================
# set_mutation_token.sh -- arm the dashboard-api write-route token.
#
# Decision 13-Sep-2026: token now (reversible), ALB + directory sign-in later.
#
# What it does (each step is GET -> merge -> PUT; nothing here replaces a
# whole environment map blindly -- update-function-configuration
# --environment REPLACES the map, which is why we merge first):
#   1. generate a 48-char token (or use $MUTATION_TOKEN if already set)
#   2. store it in Secrets Manager (cubic-mars-secret-dashboard-token-dev)
#      so it can be read back later without printing it
#   3. merge MUTATION_TOKEN into the cubic-mars-dashboard-api environment
#   4. optionally register a new ECS task-definition revision for the
#      dashboard family carrying MUTATION_TOKEN and roll the service
#      (only if DASHBOARD_FAMILY / DASHBOARD_SERVICE / DASHBOARD_CLUSTER
#      are given -- the family is hand-created and not in the repo)
#   5. smoke: POST without the header must now return 401; GET routes 200
#
# The bundle sends the header from /config.js (dashboard/src/v4/V4api.js);
# the container entrypoint writes it from the task's MUTATION_TOKEN env.
# So the order is: this script (API side) -> rebuild image -> task rev.
# Between the two steps the dashboard's Create-ServiceNow-Ticket button
# returns "requires a valid x-cubic-token header" -- by design, briefly.
#
# Rollback: remove MUTATION_TOKEN from the function env (step 3 with an
# empty value) -- the handler treats empty as "no token required".
# =====================================================================
set -euo pipefail
export AWS_PAGER=""
REGION=${REGION:-us-east-1}
FN=${FN:-cubic-mars-dashboard-api}
SECRET=${SECRET:-cubic-mars-secret-dashboard-token-dev}
API_BASE=${API_BASE:-https://a9yuqt9j9b.execute-api.us-east-1.amazonaws.com}
DASHBOARD_FAMILY=${DASHBOARD_FAMILY:-}      # e.g. FrontEndDashboard
DASHBOARD_SERVICE=${DASHBOARD_SERVICE:-}    # e.g. FrontEndDashboard-service
DASHBOARD_CLUSTER=${DASHBOARD_CLUSTER:-cubic-mars-ecs-cluster-dev}
DASHBOARD_IMAGE=${DASHBOARD_IMAGE:-}          # optional: full ECR image URI:tag for the new revision (the rebuilt bundle)
DRY_RUN=${DRY_RUN:-1}                        # 1 = print what would change; 0 = apply

need(){ command -v "$1" >/dev/null || { echo "missing $1"; exit 2; }; }
need aws; need jq; need python3

# 1. token
if [ -z "${MUTATION_TOKEN:-}" ]; then
  MUTATION_TOKEN=$(python3 -c 'import secrets;print(secrets.token_urlsafe(36))')
  echo "[1] generated a new token (${#MUTATION_TOKEN} chars)"
else
  echo "[1] using MUTATION_TOKEN from the environment (${#MUTATION_TOKEN} chars)"
fi

# 2. secret (create or rotate value)
if [ "$DRY_RUN" = "0" ]; then
  if aws secretsmanager describe-secret --secret-id "$SECRET" --region "$REGION" >/dev/null 2>&1; then
    aws secretsmanager put-secret-value --secret-id "$SECRET" --secret-string "$MUTATION_TOKEN" --region "$REGION" >/dev/null
    echo "[2] rotated secret value in $SECRET"
  else
    aws secretsmanager create-secret --name "$SECRET" --description "dashboard-api x-cubic-token (write routes)" \
      --secret-string "$MUTATION_TOKEN" --region "$REGION" >/dev/null
    echo "[2] created secret $SECRET"
  fi
else
  echo "[2] DRY_RUN: would store the token in Secrets Manager secret $SECRET"
fi

# 3. merge into the Lambda env (GET -> merge -> PUT)
CUR=$(aws lambda get-function-configuration --function-name "$FN" --region "$REGION" --query 'Environment.Variables' --output json)
echo "[3] current env keys: $(echo "$CUR" | jq -r 'keys|join(",")')"
NEW=$(echo "$CUR" | jq --arg t "$MUTATION_TOKEN" '. + {MUTATION_TOKEN:$t}')
if [ "$DRY_RUN" = "0" ]; then
  aws lambda update-function-configuration --function-name "$FN" --region "$REGION" \
    --environment "{\"Variables\":$(echo "$NEW" | jq -c .)}" >/dev/null
  aws lambda wait function-updated --function-name "$FN" --region "$REGION"
  echo "[3] MUTATION_TOKEN set on $FN; keys now: $(aws lambda get-function-configuration --function-name "$FN" --region "$REGION" --query 'Environment.Variables' --output json | jq -r 'keys|join(",")')"
else
  echo "[3] DRY_RUN: would set MUTATION_TOKEN on $FN (all other keys preserved)"
fi

# 4. dashboard task definition (optional)
if [ -n "$DASHBOARD_FAMILY" ]; then
  TD=$(aws ecs describe-task-definition --task-definition "$DASHBOARD_FAMILY" --region "$REGION" --query 'taskDefinition' --output json)
  NEWTD=$(echo "$TD" | jq --arg t "$MUTATION_TOKEN" --arg img "$DASHBOARD_IMAGE" '
      .containerDefinitions[0].environment = ((.containerDefinitions[0].environment // []) | map(select(.name!="MUTATION_TOKEN")) + [{name:"MUTATION_TOKEN",value:$t}])
      | (if $img != "" then .containerDefinitions[0].image = $img else . end)
      | del(.taskDefinitionArn,.revision,.status,.requiresAttributes,.compatibilities,.registeredAt,.registeredBy,.deregisteredAt)')
  echo "[4] image in the new revision: $(echo "$NEWTD" | jq -r '.containerDefinitions[0].image')"
  if [ "$DRY_RUN" = "0" ]; then
    REV=$(aws ecs register-task-definition --region "$REGION" --cli-input-json "$NEWTD" --query 'taskDefinition.revision' --output text)
    echo "[4] registered $DASHBOARD_FAMILY:$REV with MUTATION_TOKEN"
    if [ -n "$DASHBOARD_SERVICE" ]; then
      aws ecs update-service --cluster "$DASHBOARD_CLUSTER" --service "$DASHBOARD_SERVICE" --task-definition "$DASHBOARD_FAMILY:$REV" --region "$REGION" >/dev/null
      echo "[4] service $DASHBOARD_SERVICE rolled to :$REV (one-task rule: confirm the old task stops)"
    else
      echo "[4] no DASHBOARD_SERVICE given -- run the task from :$REV by hand (one-task rule)"
    fi
  else
    echo "[4] DRY_RUN: would register a new $DASHBOARD_FAMILY revision with MUTATION_TOKEN and roll it"
  fi
else
  echo "[4] skipped: DASHBOARD_FAMILY not set (the live family is hand-created; pass it explicitly)"
fi

# 5. smoke
if [ "$DRY_RUN" = "0" ]; then
  echo "[5] smoke: GET must be 200, POST without header must be 401, POST with header must NOT be 401"
  curl -s -o /dev/null -w "GET /ps2/windows -> %{http_code}\n" "$API_BASE/ps2/windows?city=CHI"
  curl -s -o /dev/null -w "POST no-header  -> %{http_code}\n" -X POST -H 'content-type: application/json' -d '{}' "$API_BASE/ps1/servicenow-stage?city=CHI"
  curl -s -o /dev/null -w "POST with-header-> %{http_code}\n" -X POST -H 'content-type: application/json' -H "x-cubic-token: $MUTATION_TOKEN" -d '{}' "$API_BASE/ps1/servicenow-stage?city=CHI"
  echo "    (with-header may be 400 on an empty body -- anything but 401 proves the token is accepted)"
else
  echo "[5] DRY_RUN complete. Re-run with DRY_RUN=0 to apply."
fi
