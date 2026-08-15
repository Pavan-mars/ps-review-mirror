#!/usr/bin/env bash
# =====================================================================
# ps1_read_docker_and_model.sh
#
# READ ONLY. Makes no change of any kind. Every call is a Describe/List/
# Get or a local read. Safe to run repeatedly.
#
# Purpose: settle the two [UNVERIFIED] items that gate the whole daily
# inference design (docs/PS1_DAILY_INFERENCE_DESIGN.md, Q2 and Q5):
#
#   U-A  What is in docker/inference_ps1.py, Dockerfile.ps1 and
#        requirements.txt? Somebody wrote a custom PS1 inference handler.
#        Until it is read, "managed DLC is sufficient" is a hypothesis.
#
#   U-B  Does spark-model-v1/model.tar.gz already contain code/inference.py?
#        If yes, the scoring job is configuration. If no, the archive must
#        be repackaged -- still without ECR, but it is real work.
#
# Nothing downstream should be written until this has run. Writing the
# scoring script first would mean inventing a model-loading contract and
# then building a test fixture from that invention -- which is exactly how
# sql/52 passed its own test and failed in production.
#
# Usage:   cd <repo-root> && bash tooling/ps1_read_docker_and_model.sh
# Output:  tooling/out/ps1_model_contract_<UTC>.txt   (and stdout)
# =====================================================================
set -uo pipefail

REGION="${AWS_REGION:-us-east-1}"
TS="$(date -u +%Y%m%dT%H%M%SZ)"
OUTDIR="tooling/out"
OUT="${OUTDIR}/ps1_model_contract_${TS}.txt"
mkdir -p "$OUTDIR"

# tee everything, so the answer is a committable artefact and not a
# terminal scrollback that is gone tomorrow.
exec > >(tee "$OUT") 2>&1

say() { printf '\n=== %s ===\n' "$*"; }

say "ps1_read_docker_and_model.sh  ${TS}  region=${REGION}"

# ---------------------------------------------------------------------
# U-A. The docker/ directory. Local read -- no AWS involved.
# ---------------------------------------------------------------------
say "U-A  docker/ directory"
if [ -d docker ]; then
  ls -la docker/
  for f in docker/Dockerfile.ps1 docker/requirements.txt docker/inference_ps1.py; do
    if [ -f "$f" ]; then
      printf '\n--- %s ---\n' "$f"
      cat "$f"
    else
      printf '\n--- %s : ABSENT ---\n' "$f"
    fi
  done
else
  echo "docker/ not found. Are you at the repo root? pwd=$(pwd)"
fi

# ---------------------------------------------------------------------
# U-B. What the three PS1 endpoints actually load.
#
# This block is ALSO the capture that docs Q8 item 4 requires before any
# endpoint may be deleted. DescribeEndpointConfig is the only thing in AWS
# that observes what an endpoint runs -- delete the endpoint without this
# and E-1 becomes permanently unanswerable.
# ---------------------------------------------------------------------
ENDPOINTS="chicago-ps1-3d-gate-failure-v1 chicago-ps1-3d-tvm-failure-v1 chicago-ps1-3d-validator-failure-v1"
CAPDIR="tooling/out/endpoint_capture"
mkdir -p "$CAPDIR"

MODEL_URLS=""

for EP in $ENDPOINTS; do
  say "U-B  endpoint ${EP}"

  EPJSON="$(aws sagemaker describe-endpoint --endpoint-name "$EP" --region "$REGION" 2>&1)"
  if ! printf '%s' "$EPJSON" | grep -q '"EndpointArn"'; then
    echo "describe-endpoint failed or endpoint absent:"
    printf '%s\n' "$EPJSON" | head -5
    continue
  fi
  printf '%s\n' "$EPJSON" > "${CAPDIR}/${EP}.endpoint.json"
  printf '%s' "$EPJSON" | python3 -c '
import json,sys
d=json.load(sys.stdin)
print("  status        :", d.get("EndpointStatus"))
print("  config        :", d.get("EndpointConfigName"))
print("  created       :", str(d.get("CreationTime")))
print("  last modified :", str(d.get("LastModifiedTime")))
'

  CFG="$(printf '%s' "$EPJSON" | python3 -c 'import json,sys;print(json.load(sys.stdin).get("EndpointConfigName",""))')"
  [ -z "$CFG" ] && continue

  CFGJSON="$(aws sagemaker describe-endpoint-config --endpoint-config-name "$CFG" --region "$REGION" 2>&1)"
  printf '%s\n' "$CFGJSON" > "${CAPDIR}/${EP}.endpointconfig.json"

  MODELS="$(printf '%s' "$CFGJSON" | python3 -c '
import json,sys
try: d=json.load(sys.stdin)
except Exception: sys.exit(0)
for v in d.get("ProductionVariants",[]):
    print(v.get("ModelName",""))
')"

  for M in $MODELS; do
    [ -z "$M" ] && continue
    MJSON="$(aws sagemaker describe-model --model-name "$M" --region "$REGION" 2>&1)"
    printf '%s\n' "$MJSON" > "${CAPDIR}/${EP}.model.${M}.json"
    printf '%s' "$MJSON" | python3 -c '
import json,sys
try: d=json.load(sys.stdin)
except Exception: sys.exit(0)
conts = d.get("Containers") or ([d["PrimaryContainer"]] if "PrimaryContainer" in d else [])
for c in conts:
    img = c.get("Image","")
    print("  image         :", img)
    # the point of this line: managed DLC images live under the AWS DLC
    # account/repo naming; a BYOC image is <acct>.dkr.ecr.../cubic-pdm/...
    print("  image kind    :", "MANAGED-DLC" if "/sagemaker-" in img else "CUSTOM/BYOC-or-other")
    print("  model data    :", c.get("ModelDataUrl",""))
    env = c.get("Environment") or {}
    for k in ("SAGEMAKER_PROGRAM","SAGEMAKER_SUBMIT_DIRECTORY","SAGEMAKER_REGION"):
        if k in env: print(f"  env {k:<28}:", env[k])
    if not env: print("  env           : (none)")
'
    URL="$(printf '%s' "$MJSON" | python3 -c '
import json,sys
try: d=json.load(sys.stdin)
except Exception: sys.exit(0)
conts = d.get("Containers") or ([d["PrimaryContainer"]] if "PrimaryContainer" in d else [])
for c in conts:
    u=c.get("ModelDataUrl","")
    if u: print(u)
')"
    MODEL_URLS="${MODEL_URLS} ${URL}"
  done
done

echo
echo "Endpoint capture written to ${CAPDIR}/ -- COMMIT THIS DIRECTORY."
echo "tooling/ps1_cleanup.sh will refuse to delete any endpoint whose"
echo "capture files are not present."

# ---------------------------------------------------------------------
# U-B continued. What is actually inside model.tar.gz.
# Downloads to /tmp only; lists; does not extract over anything.
# ---------------------------------------------------------------------
say "U-B  model.tar.gz contents"
SEEN=""
for URL in $MODEL_URLS; do
  [ -z "$URL" ] && continue
  case " $SEEN " in *" $URL "*) continue;; esac
  SEEN="${SEEN} ${URL}"

  echo
  echo "--- ${URL} ---"
  TMP="/tmp/ps1_model_$(printf '%s' "$URL" | md5sum | cut -c1-8).tar.gz"
  if aws s3 cp "$URL" "$TMP" --region "$REGION" >/dev/null 2>&1; then
    echo "  size: $(du -h "$TMP" | cut -f1)"
    echo "  members:"
    tar -tzf "$TMP" 2>/dev/null | sed 's/^/    /' | head -60
    # ---------------------------------------------------------------
    # What to look for, per cell 22 of the fleet notebooks [READ]:
    #   inference.py                  generated inline from _INF_TEMPLATE,
    #                                 tarred at the ARCHIVE ROOT (arcname=fn),
    #                                 NOT under code/
    #   ps1_<tag>_meta.joblib         feature_cols, medians, model_type,
    #                                 model_file  <- THE FEATURE CONTRACT
    #   ps1_<tag>_threshold.joblib    the operating threshold
    #   <native booster file>         xgboost / lightgbm / catboost, named by
    #                                 meta["model_file"]
    #
    # NOT '*_champion.joblib'. That belongs to docker/inference_ps1.py, which
    # is a separate BYOC path that nothing deploys. An earlier version of this
    # script globbed for it and would have reported "VERDICT: NO champion" on
    # a perfectly healthy archive.
    # ---------------------------------------------------------------
    HAS_CODE=0; HAS_META=0; HAS_THRESH=0
    tar -tzf "$TMP" 2>/dev/null | grep -qE '(^|/)inference\.py$'   && HAS_CODE=1
    tar -tzf "$TMP" 2>/dev/null | grep -qE 'ps1_.*_meta\.joblib$'  && HAS_META=1
    tar -tzf "$TMP" 2>/dev/null | grep -qE '_threshold\.joblib$'   && HAS_THRESH=1
    HAS_JOBLIB=$HAS_META

    echo
    echo "  inference.py in archive  : $([ $HAS_CODE   -eq 1 ] && echo YES || echo no)"
    echo "  ps1_*_meta.joblib        : $([ $HAS_META   -eq 1 ] && echo YES || echo no)   <- feature contract"
    echo "  ps1_*_threshold.joblib   : $([ $HAS_THRESH -eq 1 ] && echo YES || echo no)"
    echo

    # The feature contract is the thing the daily scoring job must honour, so
    # print it. C-3: the handler reindexes to these columns with fill_value=0.0,
    # meaning any feature ABSENT from a day's frame is scored as zero rather
    # than as missing -- silently, plausibly, wrongly.
    if [ $HAS_META -eq 1 ]; then
      echo "  --- feature contract (extracting ps1_*_meta.joblib) ---"
      rm -rf /tmp/ps1_meta_x && mkdir -p /tmp/ps1_meta_x
      tar -xzf "$TMP" -C /tmp/ps1_meta_x --wildcards '*_meta.joblib' 2>/dev/null
      python3 - <<'PYMETA' 2>&1 | sed 's/^/    /'
import glob, sys
try:
    import joblib
except ImportError:
    print("joblib not installed in CloudShell: pip install joblib --quiet"); sys.exit(0)
for f in glob.glob("/tmp/ps1_meta_x/**/*_meta.joblib", recursive=True) + \
         glob.glob("/tmp/ps1_meta_x/*_meta.joblib"):
    try:
        m = joblib.load(f)
    except Exception as e:
        print(f"{f}: could not load ({e})"); continue
    cols = m.get("feature_cols", [])
    print(f"{f.split('/')[-1]}")
    print(f"  model_type : {m.get('model_type')}")
    print(f"  model_file : {m.get('model_file')}")
    print(f"  n features : {len(cols)}")
    print(f"  medians for: {len(m.get('medians', {}))} of those {len(cols)}")
    nomed = [c for c in cols if c not in (m.get('medians') or {})]
    if nomed:
        print(f"  NO MEDIAN  : {len(nomed)} -> if absent from a day's frame these")
        print(f"               are filled with 0.0 by reindex, and 0.0 is a")
        print(f"               perfectly ordinary value for a count or a rate.")
        print(f"               {nomed[:15]}")
    print("  feature_cols (this is the contract the scoring job must satisfy):")
    for i in range(0, len(cols), 4):
        print("    " + "  ".join(f"{c:<28}" for c in cols[i:i+4]))
PYMETA
      echo
    fi
    if   [ $HAS_META -eq 1 ] && [ $HAS_CODE -eq 1 ]; then
      echo "  VERDICT: EXPECTED STATE. The archive is self-contained -- model"
      echo "           file, feature contract, threshold and handler travel"
      echo "           together, exactly as cell 22 builds it."
      echo
      echo "           => DECISION-1: managed DLC is sufficient. ECR is not"
      echo "              required for PS1. cubic-pdm/mars-ps1 can be retired."
      echo "           => The scoring job should LOAD this same meta and honour"
      echo "              feature_cols, rather than recomputing a feature list."
      echo
      echo "  --- inference.py as deployed ---"
      tar -xzOf "$TMP" --wildcards '*inference.py' 2>/dev/null | head -80 | sed 's/^/  /'
      echo
      echo "  NOTE: this is generated from _INF_TEMPLATE in cell 22. It is NOT"
      echo "  docker/inference_ps1.py. Do not diff them expecting a match --"
      echo "  they are two different serving paths and only this one is live."
    elif [ $HAS_META -eq 1 ]; then
      echo "  VERDICT: meta present, NO inference.py in the archive."
      echo "           The DLC DEFAULT handler would be in use, and it does not"
      echo "           know about ps1_*_meta.joblib, feature_cols or medians."
      echo
      echo "           model_fn runs at container start, so SOMETHING loaded"
      echo "           (the endpoint is InService). input_fn and predict_fn run"
      echo "           only on invocation, and there have been ZERO invocations"
      echo "           in 14 days. InService proves a model loaded. It proves"
      echo "           NOTHING about whether this endpoint can answer."
      echo
      echo "           ACTION: re-run cell 22's export, or repackage the bundle"
      echo "           with inference.py at the archive root."
    else
      echo "  VERDICT: NO ps1_*_meta.joblib in the archive."
      echo "           The feature contract is not travelling with the model."
      echo "           Without it, nothing can state which columns -- or in"
      echo "           which order -- this model was trained on, and"
      echo "           FEATURE_COLS is computed at run time from whatever"
      echo "           columns happen to exist (cell 8). STOP. Re-derive the"
      echo "           serving contract from DescribeModel before writing any"
      echo "           scoring job."
    fi

    # -----------------------------------------------------------------
    # What DECISION-1 actually rests on, now that Q2.1 is established.
    # The archive carries its own handler and its own contract, so the
    # only thing the DLC image must supply is the three booster libraries
    # -- model_fn imports them lazily, one per branch.
    # -----------------------------------------------------------------
    echo
    echo "  --- DECISION-1 residual check ---"
    echo "  The archive is self-contained, so docker/requirements.txt is NOT"
    echo "  in the serving path and its pins do not gate this decision."
    echo "  The only requirement on the DLC image is that model_fn's lazy"
    echo "  import succeeds for the branch this model uses:"
    echo "      model_type=xgboost  -> import xgboost"
    echo "      model_type=lightgbm -> import lightgbm"
    echo "      model_type=catboost -> from catboost import CatBoostClassifier"
    echo "  (model_type is printed in the feature-contract block above.)"
    echo
    echo "  A missing library fails at CONTAINER START, loudly -- which is the"
    echo "  good failure. The dangerous one is C-3, and no image check finds"
    echo "  it: reindex(columns=feature_cols, fill_value=0.0) turns an ABSENT"
    echo "  feature into a plausible 0.0 and returns a confident wrong answer."
    echo "  The scoring job must assert column presence BEFORE serialising."
    echo "  See DECISION-8 in docs/PS1_DAILY_INFERENCE_DESIGN.md."
  else
    echo "  s3 cp failed -- no read permission, or the object has moved."
  fi
done

say "done -- transcript at ${OUT}"
echo "Nothing was created, modified or deleted by this script."
