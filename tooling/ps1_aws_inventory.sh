#!/usr/bin/env bash
# =====================================================================
# ps1_aws_inventory.sh
#
# READ ONLY. Every call is a Describe / List / Get / head-object.
# Nothing is created, modified, deleted, enabled or disabled.
# Safe to run at any time, repeatedly, in production.
#
# Fills the parts of the PS1 inventory that only live AWS can answer:
#   item 2  -- do the S3 output objects actually exist, and how fresh
#   item 6  -- the inference endpoints (NOTE: they are NOT ECR-backed)
#   item 7  -- every EventBridge rule touching PS1, and its state
#   item 3  -- live Lambda config vs what the repo's deploy.sh asserts
#
# Everything else in docs/PS1_OPERATIONAL_INVENTORY.md came out of the
# repository and needs no AWS access.
#
# Usage:   cd <repo-root> && bash tooling/ps1_aws_inventory.sh
# Output:  tooling/out/ps1_aws_inventory_<UTC>.txt  and stdout
#
# A NOTE ON WHY THIS SCRIPT PRINTS SO MUCH PROSE.
# Several checks below can return an empty result, and an empty result
# is ambiguous: "no such rule" and "no permission to list rules" look
# identical in a bare CLI output. Where that ambiguity exists the
# script says so explicitly rather than letting a blank line be read
# as a clean bill of health. This programme has been bitten four times
# by absence rendered as success.
# =====================================================================
set -uo pipefail

REGION="${AWS_REGION:-us-east-1}"
TS="$(date -u +%Y%m%dT%H%M%SZ)"
mkdir -p tooling/out
OUT="tooling/out/ps1_aws_inventory_${TS}.txt"
exec > >(tee "$OUT") 2>&1

ACCT="$(aws sts get-caller-identity --query Account --output text 2>/dev/null || echo UNKNOWN)"
ARTIFACTS="cubic-mars-pm-s3-datalake-dev-artifacts-170202974600"
GOLD="cubic-mars-pm-s3-datalake-dev-gold-170202974600"

hdr() { printf '\n\n========== %s ==========\n' "$*"; }
sub() { printf '\n--- %s ---\n' "$*"; }

echo "ps1_aws_inventory.sh   ${TS}   region=${REGION}   account=${ACCT}"
echo "READ ONLY -- nothing will be changed."

# =====================================================================
hdr "ITEM 2  S3 output locations -- do they exist, and how fresh"
# =====================================================================
echo "The repo says PS1 writes to these prefixes. Confirming each one"
echo "actually holds objects, and when they were last written."
echo
echo "IMPORTANT: silver and gold data both live in the bucket NAMED"
echo "'...-gold-...'. S3_SILVER is built from GOLD_BUCKET in CELL 3."
echo "That is what the notebooks say; it is not a typo here."

check_prefix() {  # check_prefix <bucket> <prefix> <what it is>
  sub "s3://$1/$2"
  echo "    ($3)"
  local RES
  RES="$(aws s3api list-objects-v2 --bucket "$1" --prefix "$2" \
          --max-items 5 --region "$REGION" \
          --query 'Contents[].[Key,Size,LastModified]' --output text 2>&1)"
  if printf '%s' "$RES" | grep -qiE 'AccessDenied|NoSuchBucket|could not be found|error'; then
    echo "    CANNOT READ:"
    printf '%s\n' "$RES" | head -3 | sed 's/^/      /'
    echo "    ^^ this is UNREADABLE, which is NOT the same as EMPTY."
    echo "       Do not record this prefix as absent."
  elif [ -z "$RES" ] || [ "$RES" = "None" ]; then
    echo "    EMPTY -- prefix readable, zero objects."
  else
    printf '%s\n' "$RES" | head -5 | sed 's/^/      /'
    local N
    N="$(aws s3api list-objects-v2 --bucket "$1" --prefix "$2" --region "$REGION" \
          --query 'length(Contents)' --output text 2>/dev/null)"
    echo "    objects under this prefix: ${N:-?}"
  fi
}

echo
echo ">> PS1 cross-wired predictions -- the dashboard feed (Path A source)"
check_prefix "$ARTIFACTS" "chicago/device_ps1_cross_wired_daily/gate"      "notebook CELL 24 output, GATE"
check_prefix "$ARTIFACTS" "chicago/device_ps1_cross_wired_daily/tvm"       "notebook CELL 24 output, TVM"
check_prefix "$ARTIFACTS" "chicago/device_ps1_cross_wired_daily/validator" "notebook CELL 24 output, VALIDATOR"

echo
echo ">> Legacy gold-bucket layout -- the Path B source, frozen"
check_prefix "$GOLD" "chicago/gold/device_ps1_cross_wired_daily" "Path B source, retired 10-Aug"

echo
echo ">> Model artefacts -- what the endpoints load"
check_prefix "$ARTIFACTS" "sagemaker/ps1-3d/" "model.tar.gz per fleet + monitor baselines"

echo
echo ">> Feature Store offline stores"
check_prefix "$ARTIFACTS" "feature-store/ps1-chicago-device-features-dev"        "GATE + TVM"
check_prefix "$ARTIFACTS" "feature-store/chicago-ps1-3d-validator-failure-features" "VALIDATOR"

echo
echo ">> Gold/silver inputs the notebooks read"
check_prefix "$GOLD" "chicago/gold/device_ps1_daily"   "PS1 spine"
check_prefix "$GOLD" "chicago/silver/device_event_enriched" "label source"
check_prefix "$GOLD" "chicago/silver/hw_config_current"     "component inventory, CELL 24 join"

echo
echo ">> The daily cross-wire job's own output + manifest"
check_prefix "$GOLD" "chicago/cross_wired/daily"    "cross_wired_daily_job partitions"
check_prefix "$GOLD" "chicago/cross_wired/manifest" "completion manifests"

echo
echo ">> Does the batch-scored prefix exist yet? (it should NOT -- not built)"
check_prefix "$ARTIFACTS" "chicago/ps1/scored" "planned daily scoring output -- expected EMPTY today"

# =====================================================================
hdr "ITEM 6  Inference endpoints"
# =====================================================================
cat <<'NOTE'
TERMINOLOGY CORRECTION, stated up front because the question asked for
"ECR inference endpoints":

The three PS1 endpoints do NOT use an ECR image. They run the AWS-MANAGED
SageMaker Deep Learning Container and load a self-contained model.tar.gz
from S3 -- built by notebook CELL 22, containing the native booster file,
ps1_<tag>_meta.joblib (the feature contract), ps1_<tag>_threshold.joblib
and a generated inference.py.

The ECR repository cubic-pdm/mars-ps1 (2.38 GB) is referenced by NOTHING:
0 of 28 model package groups, 0 endpoints. docker/Dockerfile.ps1 and
docker/inference_ps1.py in the repo are a parallel BYOC path that was
built and never deployed.

The block below proves or refutes that. Read the "image kind" line.
NOTE

for EP in chicago-ps1-3d-gate-failure-v1 \
          chicago-ps1-3d-tvm-failure-v1 \
          chicago-ps1-3d-validator-failure-v1; do
  sub "endpoint ${EP}"
  EPJSON="$(aws sagemaker describe-endpoint --endpoint-name "$EP" --region "$REGION" 2>&1)"
  if ! printf '%s' "$EPJSON" | grep -q '"EndpointArn"'; then
    echo "    NOT FOUND or not readable:"
    printf '%s\n' "$EPJSON" | head -3 | sed 's/^/      /'
    continue
  fi
  printf '%s' "$EPJSON" | python3 -c '
import json,sys
d=json.load(sys.stdin)
print("    status        :", d.get("EndpointStatus"))
print("    config        :", d.get("EndpointConfigName"))
print("    created       :", str(d.get("CreationTime")))
print("    last modified :", str(d.get("LastModifiedTime")))
for v in d.get("ProductionVariants",[]):
    print("    variant       :", v.get("VariantName"),
          " instances=", v.get("CurrentInstanceCount"),
          " type=", v.get("CurrentServingInstanceType") or v.get("InstanceType"))
'
  CFG="$(printf '%s' "$EPJSON" | python3 -c 'import json,sys;print(json.load(sys.stdin).get("EndpointConfigName",""))')"
  [ -z "$CFG" ] && continue
  aws sagemaker describe-endpoint-config --endpoint-config-name "$CFG" --region "$REGION" 2>/dev/null \
  | python3 -c '
import json,sys
try: d=json.load(sys.stdin)
except Exception: sys.exit(0)
for v in d.get("ProductionVariants",[]):
    print("    model         :", v.get("ModelName"))
    print("    instance      :", v.get("InstanceType"), "x", v.get("InitialInstanceCount"))
'
  for M in $(aws sagemaker describe-endpoint-config --endpoint-config-name "$CFG" --region "$REGION" 2>/dev/null \
             | python3 -c 'import json,sys
try: d=json.load(sys.stdin)
except Exception: sys.exit(0)
for v in d.get("ProductionVariants",[]): print(v.get("ModelName",""))'); do
    [ -z "$M" ] && continue
    aws sagemaker describe-model --model-name "$M" --region "$REGION" 2>/dev/null \
    | python3 -c '
import json,sys
try: d=json.load(sys.stdin)
except Exception: sys.exit(0)
cs = d.get("Containers") or ([d["PrimaryContainer"]] if "PrimaryContainer" in d else [])
for c in cs:
    img=c.get("Image","")
    print("    image         :", img)
    kind = "AWS-MANAGED DLC" if "/sagemaker-" in img or "763104351884" in img else "CUSTOM (ECR/BYOC)"
    print("    image kind    :", kind, " <-- ITEM 6 ANSWER")
    print("    model data    :", c.get("ModelDataUrl",""))
    env=c.get("Environment") or {}
    print("    env           :", env if env else "(none)")
'
  done

  sub "invocations, last 14 days -- ${EP}"
  for MET in Invocations Invocation4XXErrors Invocation5XXErrors ModelLatency; do
    V="$(aws cloudwatch get-metric-statistics --namespace AWS/SageMaker --metric-name "$MET" \
          --dimensions Name=EndpointName,Value="$EP" Name=VariantName,Value=AllTraffic \
          --start-time "$(date -u -d '14 days ago' +%Y-%m-%dT%H:%M:%SZ)" \
          --end-time "$(date -u +%Y-%m-%dT%H:%M:%SZ)" --period 1209600 --statistics Sum \
          --region "$REGION" --query 'Datapoints[0].Sum' --output text 2>/dev/null)"
    case "$V" in ''|None) V=0 ;; esac
    printf '      %-22s %s\n' "$MET" "$V"
  done
  echo "      Invocations=0 is EXPECTED: the daily Chicago data has not"
  echo "      arrived yet, so nothing has had cause to call these."
  echo "      It does NOT mean the endpoints are broken -- and it also"
  echo "      does NOT mean they work. InService proves the model LOADED"
  echo "      at container start. Nothing has exercised input_fn or"
  echo "      predict_fn in production, so 'can it answer' is untested."
done

sub "ECR repositories -- confirming the PS1 image is unreferenced"
aws ecr describe-repositories --region "$REGION" \
    --query 'repositories[].repositoryName' --output text 2>/dev/null \
| tr '\t' '\n' | while read -r R; do
  [ -z "$R" ] && continue
  N="$(aws ecr list-images --repository-name "$R" --region "$REGION" \
        --query 'length(imageIds)' --output text 2>/dev/null)"
  printf '    %-42s images=%s\n' "$R" "${N:-UNREADABLE}"
done
echo
echo "    cubic-pdm/mars-ps1 : expected to hold images and be referenced by nothing."
echo "    cubic-pdm/mars-ps3 : LIVE -- model package 14 references it, and pins the"
echo "                         MUTABLE :latest tag. That is a real PS3 defect. It is"
echo "                         out of scope for PS1 and must not be cleaned up here."

# =====================================================================
hdr "ITEM 7  EventBridge rules touching PS1"
# =====================================================================
echo "Listing EVERY rule, then flagging the PS1-relevant ones. The full"
echo "list matters because the schedule chain must stay collision-free."
echo
printf '    %-46s %-10s %s\n' RULE STATE SCHEDULE
aws events list-rules --region "$REGION" \
    --query 'Rules[].[Name,State,ScheduleExpression]' --output text 2>/dev/null \
| while IFS=$'\t' read -r NAME STATE SCHED; do
    [ -z "$NAME" ] && continue
    printf '    %-46s %-10s %s\n' "$NAME" "$STATE" "${SCHED:-<event pattern>}"
  done
echo
echo "    If the list above is EMPTY, that is almost certainly a permission"
echo "    problem, not an account with no rules. Do not read it as 'none'."

for RULE in cubic-mars-ps1-xw-daily-load cubic-mars-ps1-daily-push; do
  sub "rule ${RULE}  (expected: xw-daily-load ENABLED cron(40 6 *), daily-push DISABLED)"
  aws events describe-rule --name "$RULE" --region "$REGION" 2>&1 \
    | python3 -c '
import json,sys
raw=sys.stdin.read()
try: d=json.loads(raw)
except Exception:
    print("      not found or not readable:"); print("      "+raw.strip()[:200]); sys.exit(0)
print("      State        :", d.get("State"))
print("      Schedule     :", d.get("ScheduleExpression") or d.get("EventPattern"))
print("      Description  :", d.get("Description"))
'
  echo "      targets:"
  aws events list-targets-by-rule --rule "$RULE" --region "$REGION" \
      --query 'Targets[].[Id,Arn]' --output text 2>/dev/null | sed 's/^/        /' \
    || echo "        (none / unreadable)"
done

sub "the 02:00 training pipeline -- succeeds daily having trained nothing"
for R in $(aws events list-rules --region "$REGION" \
             --query "Rules[?contains(Name,'training-pipeline')].Name" --output text 2>/dev/null); do
  echo "      rule: $R"
  aws events describe-rule --name "$R" --region "$REGION" \
      --query '[State,ScheduleExpression]' --output text 2>/dev/null | sed 's/^/        /'
done
echo "      Recent Step Functions executions and the training jobs they launched:"
SM_ARN="$(aws stepfunctions list-state-machines --region "$REGION" \
           --query "stateMachines[?contains(name,'training-pipeline')].stateMachineArn" \
           --output text 2>/dev/null | head -1)"
if [ -n "$SM_ARN" ]; then
  aws stepfunctions list-executions --state-machine-arn "$SM_ARN" --max-items 5 \
      --region "$REGION" --query 'executions[].[name,status,startDate]' --output text 2>/dev/null \
      | sed 's/^/        /'
else
  echo "        no training-pipeline state machine found or not readable"
fi
echo "      Training jobs in the last 14 days (expect ZERO -- that is the finding):"
aws sagemaker list-training-jobs --region "$REGION" --max-results 10 \
    --creation-time-after "$(date -u -d '14 days ago' +%Y-%m-%dT%H:%M:%SZ)" \
    --query 'TrainingJobSummaries[].[TrainingJobName,TrainingJobStatus,CreationTime]' \
    --output text 2>/dev/null | sed 's/^/        /'
echo "        (blank above = no training jobs in 14 days)"

# =====================================================================
hdr "ITEM 3  Live Lambda config vs what the repo asserts"
# =====================================================================
for FN in cubic-mars-ps1-xw-loader cubic-mars-ps1-rds-push cubic-mars-dashboard-api; do
  sub "lambda ${FN}"
  aws lambda get-function-configuration --function-name "$FN" --region "$REGION" 2>&1 \
    | python3 -c '
import json,sys
raw=sys.stdin.read()
try: d=json.loads(raw)
except Exception:
    print("      not found or not readable:"); print("      "+raw.strip()[:200]); sys.exit(0)
print("      Runtime      :", d.get("Runtime"))
print("      Handler      :", d.get("Handler"))
print("      Timeout      :", d.get("Timeout"), "s   Memory:", d.get("MemorySize"), "MB")
print("      LastModified :", d.get("LastModified"))
print("      CodeSize     :", d.get("CodeSize"))
print("      Layers       :", [l.get("Arn","").split(":layer:")[-1] for l in d.get("Layers",[])] or "(none)")
env=(d.get("Environment") or {}).get("Variables",{})
for k in sorted(env):
    v=env[k]
    if any(s in k.upper() for s in ("SECRET","TOKEN","PASSWORD","KEY")):
        v="<redacted>"
    print(f"      env {k:<22}: {v}")
'
  echo "      reserved concurrency:"
  aws lambda get-function-concurrency --function-name "$FN" --region "$REGION" \
      --query 'ReservedConcurrentExecutions' --output text 2>/dev/null | sed 's/^/        /'
  echo "        (for cubic-mars-ps1-rds-push this MUST be 0 -- that is the Path B"
  echo "         throttle. 'None' means UNRESERVED, i.e. the throttle is NOT in"
  echo "         place and Path B could run. The repo only asserts this value in a"
  echo "         comment; deploy.sh deliberately does not set it.)"

  echo "      invocations last 3 days:"
  for M in Invocations Errors Throttles; do
    V="$(aws cloudwatch get-metric-statistics --namespace AWS/Lambda --metric-name "$M" \
          --dimensions Name=FunctionName,Value="$FN" \
          --start-time "$(date -u -d '3 days ago' +%Y-%m-%dT%H:%M:%SZ)" \
          --end-time "$(date -u +%Y-%m-%dT%H:%M:%SZ)" --period 259200 --statistics Sum \
          --region "$REGION" --query 'Datapoints[0].Sum' --output text 2>/dev/null)"
    case "$V" in ''|None) V=0 ;; esac
    printf '        %-12s %s\n' "$M" "$V"
  done
done

sub "S3 notification configuration on the GOLD bucket"
echo "Recorded here because put-bucket-notification-configuration is a"
echo "REPLACE, and any future change must merge onto exactly this."
aws s3api get-bucket-notification-configuration --bucket "$GOLD" --region "$REGION" 2>&1 \
  | sed 's/^/    /' | head -40
echo
echo "    If a Lambda notification to ps1-cross-wired-push appears above,"
echo "    it is LOAD-BEARING. Any later put must GET this first and merge."

hdr "DONE"
echo "Transcript: ${OUT}"
echo "Nothing was created, modified or deleted."
echo
echo "Paste the transcript back and it will be folded into"
echo "docs/PS1_OPERATIONAL_INVENTORY.md, replacing every [AWS-PENDING] marker."
