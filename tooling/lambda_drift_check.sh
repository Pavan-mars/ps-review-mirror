#!/usr/bin/env bash
# =====================================================================
#  LAMBDA DRIFT CHECK -- is the deployed code the code in this repo?
#  READ-ONLY.  CloudShell, us-east-1.   Created 2026-08-10.
#
#  WHY THIS EXISTS. On 10-Aug-2026 the deployed cubic-mars-ps1-xw-loader was
#  found to be 11,521 bytes AHEAD of the repository. Production code for a
#  loader that writes 786,525 rows into Aurora existed in no version control at
#  all, and nothing had noticed for weeks. Every other loader has exactly the
#  same exposure and none of them had ever been checked.
#
#  This downloads each function's deployed package and diffs handler.py against
#  the repo. Nothing is deployed, modified or uploaded. The only AWS calls are
#  list-functions, get-function and an HTTPS GET of the returned code URL.
#
#  Run from the repo root:
#      bash tooling/lambda_drift_check.sh
#
#  Exit codes:  0 = every function matches      (or is not in the repo)
#               1 = at least one DRIFT detected
#
#  Deliberately generic: it maps functions to repo folders by name, so it
#  covers PS1-PS5 and anything added later without being edited.
# =====================================================================
set -uo pipefail
REGION=${REGION:-us-east-1}
ROOT=${ROOT:-$(pwd)}
LAMBDA_DIR="$ROOT/api/lambda"
STAMP=$(date -u +%Y%m%dT%H%M%SZ)
OUT="${OUT:-$HOME/lambda_drift_$STAMP}"
mkdir -p "$OUT"

if [ ! -d "$LAMBDA_DIR" ]; then
  echo "ERROR: $LAMBDA_DIR not found. Run this from the repository root, or set ROOT=/path/to/repo."
  exit 3
fi

echo "== LAMBDA DRIFT CHECK  $STAMP"
echo "   repo   : $LAMBDA_DIR"
echo "   region : $REGION"
echo "   output : $OUT"
echo "   READ-ONLY: list / get / download only. Nothing is deployed."
echo

DRIFT=0; MATCH=0; NOREPO=0; NOFN=0; ERR=0
printf '%-34s %-12s %-10s %-10s %s\n' "FUNCTION" "VERDICT" "DEPLOYED" "REPO" "NOTE"
printf '%.0s-' {1..100}; echo

for FN in $(aws lambda list-functions --region "$REGION" \
              --query 'Functions[].FunctionName' --output text 2>/dev/null \
            | tr '\t' '\n' | grep -Ei 'cubic|mars' | sort); do

  SRC="$LAMBDA_DIR/$FN/handler.py"
  if [ ! -f "$SRC" ]; then
    printf '%-34s %-12s %-10s %-10s %s\n' "$FN" "NO-REPO-SRC" "-" "-" "no api/lambda/$FN/handler.py"
    NOREPO=$((NOREPO+1)); continue
  fi

  URL=$(aws lambda get-function --function-name "$FN" --region "$REGION" \
          --query 'Code.Location' --output text 2>/dev/null)
  if [ -z "$URL" ] || [ "$URL" = "None" ]; then
    printf '%-34s %-12s %-10s %-10s %s\n' "$FN" "ERROR" "-" "-" "could not get code location"
    ERR=$((ERR+1)); continue
  fi

  Z="$OUT/$FN.zip"; D="$OUT/$FN"
  curl -s -o "$Z" "$URL" || { printf '%-34s %-12s\n' "$FN" "ERROR"; ERR=$((ERR+1)); continue; }
  rm -rf "$D"; mkdir -p "$D"
  unzip -qo "$Z" handler.py -d "$D" 2>/dev/null

  if [ ! -f "$D/handler.py" ]; then
    printf '%-34s %-12s %-10s %-10s %s\n' "$FN" "NO-HANDLER" "-" "$(stat -c%s "$SRC")" "package has no handler.py at its root"
    NOFN=$((NOFN+1)); continue
  fi

  DS=$(stat -c%s "$D/handler.py"); RS=$(stat -c%s "$SRC")
  DM=$(md5sum "$D/handler.py" | cut -d' ' -f1); RM=$(md5sum "$SRC" | cut -d' ' -f1)

  if [ "$DM" = "$RM" ]; then
    printf '%-34s %-12s %-10s %-10s %s\n' "$FN" "MATCH" "$DS" "$RS" "md5 ${DM:0:8}"
    MATCH=$((MATCH+1))
  else
    DELTA=$((DS - RS))
    diff -u "$SRC" "$D/handler.py" > "$OUT/$FN.diff" 2>/dev/null
    NL=$(wc -l < "$OUT/$FN.diff")
    printf '%-34s %-12s %-10s %-10s %s\n' "$FN" "*** DRIFT" "$DS" "$RS" \
      "deployed-repo=${DELTA} bytes, ${NL}-line diff -> $FN.diff"
    DRIFT=$((DRIFT+1))
  fi
done

echo
echo "======================================================================"
printf '  MATCH %d   DRIFT %d   NO-REPO-SRC %d   NO-HANDLER %d   ERROR %d\n' \
       "$MATCH" "$DRIFT" "$NOREPO" "$NOFN" "$ERR"
echo "======================================================================"

if [ "$DRIFT" -gt 0 ]; then
  cat <<'WHY'

  DRIFT means the running code is NOT the code you can read, review or roll
  back to. Before doing anything else, decide which side is correct:

    * deployed is newer  -> someone hot-patched. COMMIT THE DEPLOYED FILE
      first (that is what commit 73df05d had to do for the xw-loader), THEN
      decide whether to keep the change.
    * repo is newer      -> a deploy was never run, or it failed silently.
      Do NOT assume the repo version is live just because it is committed.

  Read the .diff before choosing. Never resolve drift by redeploying blindly:
  that destroys the only copy of whatever was hot-patched.
WHY
fi

if [ "$NOREPO" -gt 0 ]; then
  cat <<'WHY'

  NO-REPO-SRC is its own finding, not a skip. A deployed function with no
  source in this repository cannot be reviewed, diffed or rebuilt. As of
  2026-08-10 cubic-mars-ps2-rds-push was in exactly that state (open item 8).
WHY
fi

echo
echo "  Full report and diffs: $OUT"
[ "$DRIFT" -gt 0 ] && exit 1
exit 0
