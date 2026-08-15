#!/usr/bin/env bash
# =====================================================================
#  PS1 PROVENANCE FIX -- follow-up to the 2026-08-11 go-live.
#
#  Closes the two gaps the deploy exposed:
#    target=None            ps1_model_performance.target_col was never written
#    serving_matches=None   its run_id was never written either, AND the
#                           serving lookup was reading batch_score rows
#
#  Deploys the corrected dashboard-api handler and applies sql/52.
#  Zip-swap only. No IAM, env, layers, rules or bucket notifications.
#
#      bash ps1_provenance_fix.sh          # dry run
#      APPLY=1 bash ps1_provenance_fix.sh  # execute
# =====================================================================
set -uo pipefail
REGION=us-east-1
FN=cubic-mars-dashboard-api
# 2026-08-11. Was ps1_golive_*.zip, which silently matched the EARLIER go-live
# bundle -- the one carrying sql/50 and sql/51 but not sql/52 -- and the run
# refused at the staging check. A default that can resolve to the wrong artefact
# is worse than no default. Prefer this fix's own bundle, fall back to any
# ps1_* zip, and always print what was chosen so a wrong pick is visible before
# anything is deployed rather than after.
UPLOAD=${UPLOAD:-$(ls -1t "$HOME"/ps1_provfix_*.zip "$HOME"/ps1_*.zip 2>/dev/null | head -1)}
APPLY=${APPLY:-0}
STAMP=$(date -u +%Y%m%dT%H%M%SZ)
BK="$HOME/ps1_prov_$STAMP"; mkdir -p "$BK"; WORK=$(mktemp -d)
die(){ echo; echo "!! $1"; echo "!! ROLLBACK: aws lambda update-function-code --function-name $FN --region $REGION --zip-file fileb://$BK/$FN.zip"; exit 1; }
API=$(aws apigatewayv2 get-apis --region $REGION --query "Items[?Name=='$FN'].ApiEndpoint" --output text 2>/dev/null | head -1)

show(){ echo; echo "---- $1 ----"; curl -s --max-time 30 "$API/ps1/model-performance?city=CHI" | python3 -c "
import json,sys
try: d=json.load(sys.stdin)
except Exception: print('   unparseable'); raise SystemExit
for r in (d if isinstance(d,list) else []):
    print('   %-10s target=%-22s serving_run=%-22s kind=%-12s matches=%s' % (
        r.get('device_category'), r.get('target'), r.get('serving_run_id'),
        r.get('serving_run_kind'), r.get('serving_matches_scorecard')))
"; }

echo "== PS1 PROVENANCE FIX $STAMP  mode=$([ "$APPLY" = 1 ] && echo APPLY || echo DRY-RUN)"
[ -n "${UPLOAD:-}" ] && [ -f "$UPLOAD" ] || die "no ps1_golive_*.zip found in \$HOME -- set UPLOAD=/path/to/zip"
echo "   using upload: $UPLOAD"
unzip -l "$UPLOAD" 2>/dev/null | grep -q "52_ps1_model_performance_provenance.sql" \
  || { echo "   [!] that zip does NOT contain sql/52 -- pass the right one with UPLOAD=..."; }
show "BEFORE"

U=$(aws lambda get-function --function-name $FN --region $REGION --query 'Code.Location' --output text) || die "cannot read $FN"
curl -s -o "$BK/$FN.zip" "$U" || die "cannot back up"
echo "   backup: $BK/$FN.zip"

mkdir -p "$WORK/new"; unzip -qo "$UPLOAD" -d "$WORK/new" || die "cannot unzip"
for F in dashboard-api/handler.py dashboard-api/sql/52_ps1_model_performance_provenance.sql; do
  [ -f "$WORK/new/$F" ] || die "upload is missing $F"; done
python3 -c "import ast;ast.parse(open('$WORK/new/dashboard-api/handler.py').read())" || die "handler does not parse"
echo "   staged and parsed"

if [ "$APPLY" != 1 ]; then echo; echo "DRY RUN COMPLETE -- nothing changed. APPLY=1 to execute."; exit 0; fi

mkdir -p "$WORK/pkg"; unzip -qo "$BK/$FN.zip" -d "$WORK/pkg" || die "cannot unpack backup"
cp "$WORK/new/dashboard-api/handler.py" "$WORK/pkg/handler.py"
mkdir -p "$WORK/pkg/sql"; cp "$WORK/new/dashboard-api/sql/"*.sql "$WORK/pkg/sql/"
( cd "$WORK/pkg" && zip -qr "$WORK/d.zip" . ) || die "cannot rezip"
aws lambda update-function-code --function-name $FN --region $REGION --zip-file "fileb://$WORK/d.zip" --query LastModified --output text || die "deploy failed"
aws lambda wait function-updated --function-name $FN --region $REGION
echo "   code deployed"

for M in dry_run apply; do
  P='{"action":"apply_sql","file":"52_ps1_model_performance_provenance.sql"'
  [ "$M" = dry_run ] && P="$P,\"dry_run\":true"
  aws lambda invoke --function-name $FN --region $REGION --cli-binary-format raw-in-base64-out \
    --payload "$P}" "$WORK/$M.json" >/dev/null 2>&1
  python3 -c "
import json;d=json.load(open('$WORK/$M.json'))
b=json.loads(d.get('body','{}')) if isinstance(d.get('body'),str) else d
a=b.get('apply_sql',b)
print('   $M: statements=%s applied=%s tolerated=%s failed=%s'%(a.get('statements'),a.get('applied'),a.get('tolerated'),a.get('failed')))
for e in (a.get('errors') or []): print('      ERROR:',e[:200])
"
done

show "AFTER"
cat <<'NOTE'

   EXPECTED
     target        = will_hardware_oos_3d   on all three fleets
     serving_run   = ps1_sklearn_20260726   on all three
     kind          = train                  (NOT batch_score)
     matches       = True                   on all three

   TVM previously reported serving_run=ps1_20260810. That was a batch_score row
   written by Path B this morning being read as if it were the served model.

   VALIDATOR recall_floor stays NULL on purpose -- no VALIDATOR floor is
   documented anywhere in this programme. v_ps1_provenance_gaps says so.
NOTE
echo "   ROLLBACK: aws lambda update-function-code --function-name $FN --region $REGION --zip-file fileb://$BK/$FN.zip"
