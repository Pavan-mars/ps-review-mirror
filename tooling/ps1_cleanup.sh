#!/usr/bin/env bash
# =====================================================================
# ps1_cleanup.sh   --  PS1 retirement, in risk order, dry-run by default
#
#   bash tooling/ps1_cleanup.sh              # DRY RUN. prints, changes nothing.
#   bash tooling/ps1_cleanup.sh --apply      # executes. still refuses unsafe steps.
#
# Backing analysis: docs/PS1_DAILY_INFERENCE_DESIGN.md Q8.
#
# DESIGN RULES, each one bought with a mistake earlier in this programme:
#
#  1. Dry run is the default. --apply is a deliberate act.
#  2. Every mutating command is PRINTED before it is run, in full.
#  3. Nothing is deleted that cannot be recreated, except where the doc
#     says so explicitly and a capture exists first.
#  4. Endpoint deletion is BLOCKED unless tooling/out/endpoint_capture/
#     already holds that endpoint's describe-endpoint-config JSON.
#     DescribeEndpointConfig is the only thing in AWS that can answer
#     E-1 -- delete the endpoint without it and the question is closed
#     forever with the wrong answer.
#  5. NO ECR repository holding images is deleted here. cubic-pdm/mars-ps1
#     gets a LIFECYCLE POLICY and a re-audit date, not a delete. And
#     cubic-pdm/mars-ps3 is LIVE (model package 14 references it) -- it is
#     named here only so that nobody sweeps it up by pattern-matching
#     "cubic-pdm/mars-*".
# =====================================================================
set -uo pipefail

REGION="${AWS_REGION:-us-east-1}"
APPLY=0
DELETE_EMPTY_REPOS=0
for a in "$@"; do
  case "$a" in
    --apply)              APPLY=1 ;;
    --delete-empty-repos) DELETE_EMPTY_REPOS=1 ;;
  esac
done

TS="$(date -u +%Y%m%dT%H%M%SZ)"
mkdir -p tooling/out
LOG="tooling/out/ps1_cleanup_${TS}.log"
exec > >(tee "$LOG") 2>&1

if [ "$APPLY" -eq 1 ]; then
  echo "############ APPLY MODE -- changes will be made ############"
else
  echo "############ DRY RUN -- nothing will be changed ############"
  echo "############ re-run with --apply to execute      ############"
fi
echo "region=${REGION}  ts=${TS}"

run() {
  echo
  echo "  \$ $*"
  if [ "$APPLY" -eq 1 ]; then
    "$@" 2>&1 | sed 's/^/    /'
    echo "    rc=${PIPESTATUS[0]}"
  else
    echo "    (dry run -- not executed)"
  fi
}

step() { printf '\n\n===== STEP %s =====\n' "$*"; }

# ---------------------------------------------------------------------
step "1  empty ECR repositories -- LIST by default, delete only on --delete-empty-repos"
# ---------------------------------------------------------------------
echo "An empty repository is recreatable, but it is not necessarily litter:"
echo "a CI job that pushes to a repo you deleted fails, and the failure is"
echo "someone else's, in a pipeline you cannot see. This account has empty"
echo "repos belonging to PS2/PS3/PS4/PS5 work that is not finished. So the"
echo "default here is to LIST. Pass --delete-empty-repos once you have read"
echo "the list and recognised every name on it."
echo
echo "Listing repositories and their image counts. Read-only."
REPOS="$(aws ecr describe-repositories --region "$REGION" \
          --query 'repositories[].repositoryName' --output text 2>/dev/null)"
if [ -z "$REPOS" ]; then
  echo "  no repositories returned (no permission, or none exist)"
else
  for R in $REPOS; do
    N="$(aws ecr list-images --repository-name "$R" --region "$REGION" \
          --query 'length(imageIds)' --output text 2>/dev/null)"
    N="${N:-?}"
    printf '  %-40s images=%s\n' "$R" "$N"
    if [ "$N" = "0" ]; then
      case "$R" in
        cubic-pdm/mars-ps3)
          echo "      SKIP -- listed as 0 but PS3 is live; verify by hand, do not delete"
          ;;
        *)
          if [ "$DELETE_EMPTY_REPOS" -eq 1 ]; then
            run aws ecr delete-repository --repository-name "$R" --region "$REGION"
          else
            echo "      empty -- would delete (pass --delete-empty-repos to arm)"
          fi
          ;;
      esac
    fi
  done
fi

# ---------------------------------------------------------------------
step "2  the 02:00 no-op training pipeline -- DISABLE the rule, keep everything"
# ---------------------------------------------------------------------
echo "Measured: cubic-mars-sfn-training-pipeline-dev succeeds daily at 02:00"
echo "having launched ZERO training jobs; its training image repo"
echo "cubic-mars-ecr-training-dev is empty; it points at"
echo "s3://cubic-mars-artifacts-dev/ which is not the known artifacts bucket."
echo
echo "A daily green tick for work that never happens is worse than a red one."
echo "Disabling the rule is fully reversible (enable-rule). The state machine"
echo "and its execution history are left untouched -- they are the evidence."
MATCHES="$(aws events list-rules --region "$REGION" \
             --query "Rules[?contains(Name,'training-pipeline')].[Name,State]" \
             --output text 2>/dev/null)"
NMATCH="$(printf '%s' "$MATCHES" | grep -c . || true)"
echo
echo "  rules matching 'training-pipeline':"
printf '%s\n' "$MATCHES" | sed 's/^/    /'
if [ "${NMATCH:-0}" -gt 1 ]; then
  echo
  echo "  STOP -- ${NMATCH} rules matched a SUBSTRING. Only the 02:00 dev"
  echo "  training pipeline was measured as a no-op. Disabling a rule that"
  echo "  belongs to PS2/PS3/PS4/PS5 training would stop real work with no"
  echo "  visible symptom until someone notices a model went stale."
  echo "  Name the rule explicitly and disable it by hand."
else
  for RULE in $(printf '%s' "$MATCHES" | awk '{print $1}'); do
    [ -z "$RULE" ] && continue
    run aws events disable-rule --name "$RULE" --region "$REGION"
  done
fi

# ---------------------------------------------------------------------
step "3  cubic-pdm/mars-ps1 (2.38 GB, referenced by 0/28 MPGs, 0 endpoints)"
# ---------------------------------------------------------------------
echo "NOT deleted. A lifecycle policy expiring untagged images older than"
echo "90 days, then a re-audit in 30 days, then delete. 2.38 GB of ECR is"
echo "a small monthly cost; an image nobody can reproduce is not."
echo
echo "Re-audit on: $(date -u -d '+30 days' +%Y-%m-%d 2>/dev/null || echo '30 days from today')"
LIFECYCLE='{"rules":[{"rulePriority":1,"description":"expire untagged >90d (ps1 retirement, 2026-08-11)","selection":{"tagStatus":"untagged","countType":"sinceImagePushed","countUnit":"days","countNumber":90},"action":{"type":"expire"}}]}'
echo
echo "  existing policy, if any (read-only):"
EXISTING_LC="$(aws ecr get-lifecycle-policy --repository-name cubic-pdm/mars-ps1 \
                 --region "$REGION" 2>&1)"
printf '%s\n' "$EXISTING_LC" | sed 's/^/    /' | head -20
echo
if printf '%s' "$EXISTING_LC" | grep -q '"lifecyclePolicyText"'; then
  echo "  BLOCKED -- a lifecycle policy already exists on this repository."
  echo "  put-lifecycle-policy REPLACES the whole document; it does not merge."
  echo "  Applying ours would silently delete the rules printed above and"
  echo "  change what gets expired, with no error and no diff. Merge by hand."
elif ! printf '%s' "$EXISTING_LC" | grep -q 'LifecyclePolicyNotFoundException'; then
  # UNREADABLE DOMINATES. The only safe reading of "the GET did not clearly
  # say there is no policy" is "there may be a policy I cannot see" -- a
  # permission error, a throttle, a missing binary. Treating an unreadable
  # answer as an empty one is how a REPLACE deletes something nobody knew
  # was there. Same rule the freshness verdict already enforces.
  echo "  BLOCKED -- could not read the current policy, and 'unreadable' is"
  echo "  not 'absent'. Output above is neither a policy document nor a clean"
  echo "  LifecyclePolicyNotFoundException. Resolve the read first."
else
  echo "  confirmed absent (LifecyclePolicyNotFoundException) -- safe to put"
  run aws ecr put-lifecycle-policy --repository-name cubic-pdm/mars-ps1 \
      --region "$REGION" --lifecycle-policy-text "$LIFECYCLE"
fi

# ---------------------------------------------------------------------
step "4  the three real-time endpoints -- CAPTURE-GATED deletion"
# ---------------------------------------------------------------------
CAPDIR="tooling/out/endpoint_capture"
for EP in chicago-ps1-3d-gate-failure-v1 \
          chicago-ps1-3d-tvm-failure-v1 \
          chicago-ps1-3d-validator-failure-v1; do
  echo
  echo "  $EP"
  if [ ! -f "${CAPDIR}/${EP}.endpointconfig.json" ]; then
    echo "    BLOCKED -- no capture at ${CAPDIR}/${EP}.endpointconfig.json"
    echo "    Run tooling/ps1_read_docker_and_model.sh first, and COMMIT the"
    echo "    capture. DescribeEndpointConfig is the only observation of what"
    echo "    this endpoint actually runs. E-1 (scorecard says"
    echo "    ps1_sklearn_20260726, registry says ps1_20260726) cannot be"
    echo "    settled from the database -- sql/54's view uses a PROXY and"
    echo "    says so. Delete this endpoint uncaptured and the question is"
    echo "    unanswerable, permanently."
    continue
  fi
  echo "    capture present -- deletion permitted"
  echo "    deleting the ENDPOINT only. The endpoint-config and the model are"
  echo "    kept: they cost nothing and they are the record."
  run aws sagemaker delete-endpoint --endpoint-name "$EP" --region "$REGION"
done

# ---------------------------------------------------------------------
step "5  Path B  cubic-mars-ps1-rds-push -- verify, change nothing"
# ---------------------------------------------------------------------
echo "Measured state to preserve: rule DISABLED, reserved concurrency 0,"
echo "no invocation since 2026-08-10 12:00Z. This is a deliberately parked"
echo "path, not litter. Verifying only."
aws lambda get-function-concurrency --function-name cubic-mars-ps1-rds-push \
    --region "$REGION" 2>&1 | sed 's/^/    /' | head -5
for RULE in $(aws events list-rules --region "$REGION" \
                --query "Rules[?contains(Name,'ps1-rds-push')].[Name,State]" \
                --output text 2>/dev/null); do
  echo "    $RULE"
done
echo "    (no action taken)"

# ---------------------------------------------------------------------
step "6  legacy cross-wire prefix -- KEEP"
# ---------------------------------------------------------------------
echo "s3://<gold>/chicago/gold/device_ps1_cross_wired_daily is still the"
echo "fallback source in cross_wired_daily_job.py. Keep it until batch"
echo "scoring has run clean for 14 consecutive days. Removing a fallback"
echo "before the primary is proven is how an outage becomes an incident."
echo "    (no action taken)"

# ---------------------------------------------------------------------
echo
echo
echo "===== SUMMARY ====="
if [ "$APPLY" -eq 1 ]; then
  echo "APPLIED. Transcript: ${LOG}"
else
  echo "DRY RUN. Nothing changed. Transcript: ${LOG}"
  echo "Review the printed commands, then: bash tooling/ps1_cleanup.sh --apply"
fi
echo
echo "NOT touched by this script, deliberately:"
echo "  cubic-pdm/mars-ps3   -- LIVE, model package 14 references it."
echo "                          Separately: it pins the MUTABLE :latest tag,"
echo "                          so an image push silently changes what a"
echo "                          registered model package resolves to. That is"
echo "                          a real PS3 defect. It is not a PS1 cleanup."
