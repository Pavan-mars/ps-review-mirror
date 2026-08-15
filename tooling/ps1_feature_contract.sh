#!/usr/bin/env bash
# =====================================================================
# ps1_feature_contract.sh
#   Extract and print the PS1 feature contract for all three fleets.
#   READ ONLY. Downloads to /tmp, prints, changes nothing in AWS.
#
# REPLACES the broken detection in ps1_read_docker_and_model.sh.
#
# THE BUG THIS FIXES
# ------------------
# That script did:
#     set -uo pipefail
#     tar -tzf "$TMP" | grep -qE 'ps1_.*_meta\.joblib$' && HAS_META=1
#
# grep -q exits at the FIRST match. tar is still writing, gets SIGPIPE,
# exits 141. pipefail makes the pipeline return 141. The && never fires.
# Whether it fires at all depends on whether tar finished before grep
# quit -- i.e. on archive size. TVM (68K) won the race; GATE (788K) and
# VALIDATOR (124K) lost it, and the script printed
#     "VERDICT: NO ps1_*_meta.joblib in the archive"
# two lines below a listing containing ps1_gate_meta.joblib.
#
# It reported ABSENCE when it meant I STOPPED LOOKING -- the exact
# failure the script exists to catch, in the script built to catch it.
#
# THE FIX: list once into a variable, then test the variable. No pipe,
# no early exit, no race. If tar itself fails, say so and stop rather
# than treating an unreadable archive as an empty one.
# =====================================================================
set -uo pipefail

REGION="${AWS_REGION:-us-east-1}"
BUCKET="cubic-mars-pm-s3-datalake-dev-artifacts-170202974600"
TS="$(date -u +%Y%m%dT%H%M%SZ)"
mkdir -p tooling/out
OUT="tooling/out/ps1_feature_contract_${TS}.txt"
exec > >(tee "$OUT") 2>&1

echo "ps1_feature_contract.sh  ${TS}  region=${REGION}"
echo

python3 -c 'import joblib' 2>/dev/null || {
  echo "installing joblib (CloudShell does not ship it)..."
  pip install --quiet joblib 2>&1 | tail -2
}
python3 -c 'import joblib; print("joblib", joblib.__version__)' || {
  echo "FATAL: joblib unavailable -- cannot read the meta files."; exit 2; }

for FLEET in gate tvm validator; do
  echo
  echo "=============================================================="
  echo " ${FLEET^^}"
  echo "=============================================================="

  KEY="sagemaker/ps1-3d/${FLEET}/spark-model-v1/model.tar.gz"
  TMP="/tmp/ps1_${FLEET}.tar.gz"
  DIR="/tmp/ps1_${FLEET}_x"

  if ! aws s3 cp "s3://${BUCKET}/${KEY}" "$TMP" --region "$REGION" >/dev/null 2>&1; then
    echo "  CANNOT DOWNLOAD s3://${BUCKET}/${KEY}"
    echo "  Unreadable is NOT absent. Resolve access before concluding anything."
    continue
  fi

  # list ONCE into a variable -- no pipe, no SIGPIPE, no race
  if ! MEMBERS="$(tar -tzf "$TMP" 2>&1)"; then
    echo "  tar could not read the archive:"
    printf '%s\n' "$MEMBERS" | head -3 | sed 's/^/    /'
    echo "  Unreadable is NOT absent. Stopping for this fleet."
    continue
  fi

  echo "  archive: $(du -h "$TMP" | cut -f1)"
  echo "  members:"
  printf '%s\n' "$MEMBERS" | sed 's/^/    /'

  # now test the variable -- deterministic, no subprocess timing involved
  case "$MEMBERS" in *_meta.joblib*)      HAS_META=1 ;; *) HAS_META=0 ;; esac
  case "$MEMBERS" in *_threshold.joblib*) HAS_THR=1  ;; *) HAS_THR=0  ;; esac
  case "$MEMBERS" in *inference.py*)      HAS_INF=1  ;; *) HAS_INF=0  ;; esac
  echo
  echo "  meta=${HAS_META}  threshold=${HAS_THR}  inference.py=${HAS_INF}"

  [ "$HAS_META" -eq 0 ] && { echo "  no meta file -- nothing further to extract"; continue; }

  rm -rf "$DIR" && mkdir -p "$DIR"
  tar -xzf "$TMP" -C "$DIR" 2>/dev/null

  FLEET="$FLEET" python3 - "$DIR" <<'PY'
import glob, os, sys, json
import joblib
d = sys.argv[1]
fleet = os.environ["FLEET"]

for f in sorted(glob.glob(os.path.join(d, "*_meta.joblib"))):
    try:
        m = joblib.load(f)
    except Exception as e:
        print(f"  could not load {os.path.basename(f)}: {e}")
        continue

    cols    = list(m.get("feature_cols", []))
    medians = m.get("medians", {}) or {}
    print(f"  meta file  : {os.path.basename(f)}")
    print(f"  model_type : {m.get('model_type')}")
    print(f"  model_file : {m.get('model_file')}")
    print(f"  n_features : {len(cols)}")
    print(f"  medians    : {len(medians)} of {len(cols)}")
    for k in sorted(m):
        if k not in ("feature_cols", "medians"):
            v = m[k]
            if not isinstance(v, (list, dict)):
                print(f"  {k:<10} : {v}")

    nomed = [c for c in cols if c not in medians]
    if nomed:
        print()
        print(f"  *** {len(nomed)} FEATURES WITH NO MEDIAN ***")
        print(f"  If any of these is ABSENT from a day's scoring frame,")
        print(f"  reindex(fill_value=0.0) makes it 0.0 -- and 0.0 is an")
        print(f"  ordinary value for a count or a rate. Confident, wrong,")
        print(f"  and silent. This is C-3 / DECISION-8.")
        for c in nomed:
            print(f"    {c}")

    print()
    print(f"  feature_cols IN ORDER (the contract the scoring job must satisfy):")
    for i, c in enumerate(cols):
        print(f"    {i:>3}  {c}")

    # machine-readable, so the scoring job can assert against it rather
    # than against a list retyped out of this transcript
    out = os.path.join("tooling", "out", f"ps1_{fleet}_feature_contract.json")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w") as fh:
        json.dump({
            "fleet":        fleet,
            "model_type":   m.get("model_type"),
            "model_file":   m.get("model_file"),
            "feature_cols": cols,
            "medians":      {k: float(v) for k, v in medians.items()},
            "no_median":    nomed,
        }, fh, indent=2)
    print(f"\n  -> wrote {out}")
PY

  THR="$(ls "$DIR"/*_threshold.joblib 2>/dev/null | head -1)"
  [ -n "$THR" ] && python3 -c "
import joblib,sys
print('  threshold  :', joblib.load(sys.argv[1]))" "$THR"
done

# ---------------------------------------------------------------------
# The handler that is ACTUALLY loaded.
#
# DescribeModel shows SAGEMAKER_SUBMIT_DIRECTORY pointing at a
# sourcedir.tar.gz in the SageMaker default bucket. For the managed
# framework containers that directory -- not model.tar.gz -- is where
# SAGEMAKER_PROGRAM is resolved from. So the inference.py sitting inside
# model.tar.gz may never be executed.
#
# Worth settling: if the two differ, the code being served is not the
# code cell 22 wrote, and every conclusion drawn from reading the
# archive copy is about a file that does not run.
# ---------------------------------------------------------------------
echo
echo "=============================================================="
echo " WHICH inference.py ACTUALLY RUNS"
echo "=============================================================="
for FLEET in gate tvm validator; do
  EP="chicago-ps1-3d-${FLEET}-failure-v1"
  SD="$(aws sagemaker describe-endpoint-config \
         --endpoint-config-name "$EP" --region "$REGION" 2>/dev/null \
       | python3 -c '
import json,sys
try: d=json.load(sys.stdin)
except Exception: sys.exit(0)
for v in d.get("ProductionVariants",[]): print(v.get("ModelName",""))' \
       | head -1)"
  [ -z "$SD" ] && { echo "  ${FLEET}: could not resolve model name"; continue; }

  URL="$(aws sagemaker describe-model --model-name "$SD" --region "$REGION" 2>/dev/null \
        | python3 -c '
import json,sys
try: d=json.load(sys.stdin)
except Exception: sys.exit(0)
c=d.get("PrimaryContainer") or (d.get("Containers") or [{}])[0]
print((c.get("Environment") or {}).get("SAGEMAKER_SUBMIT_DIRECTORY",""))')"

  echo
  echo "  ${FLEET}: ${URL:-<none>}"
  [ -z "$URL" ] && continue
  T="/tmp/srcdir_${FLEET}.tar.gz"
  if aws s3 cp "$URL" "$T" --region "$REGION" >/dev/null 2>&1; then
    M="$(tar -tzf "$T" 2>&1)"
    printf '%s\n' "$M" | sed 's/^/      /'
    D="/tmp/srcdir_${FLEET}_x"; rm -rf "$D"; mkdir -p "$D"
    tar -xzf "$T" -C "$D" 2>/dev/null
    A="/tmp/ps1_${FLEET}_x/inference.py"
    B="$D/inference.py"
    if [ -f "$A" ] && [ -f "$B" ]; then
      if diff -q "$A" "$B" >/dev/null 2>&1; then
        echo "      IDENTICAL to the copy inside model.tar.gz -- no ambiguity."
      else
        echo "      *** DIFFERS from the copy inside model.tar.gz ***"
        echo "      The served handler is NOT the one in the archive."
        diff -u "$A" "$B" 2>&1 | head -40 | sed 's/^/        /'
      fi
    fi
    [ -f "$D/requirements.txt" ] && {
      echo "      requirements.txt (this is what installs xgboost into the sklearn DLC):"
      sed 's/^/        /' "$D/requirements.txt"; }
  else
    echo "      could not download -- unreadable is not absent"
  fi
done

echo
echo "transcript: ${OUT}"
