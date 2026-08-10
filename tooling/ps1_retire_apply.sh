#!/usr/bin/env bash
# =====================================================================
#  PS1: retire ps1_failure_summary + publish every fleet on the causation panel
#  CloudShell, us-east-1.  10-Aug-2026
#
#  WHAT IT DOES
#    0. Backs up the CURRENTLY DEPLOYED package to ~/ps1_retire_backup/ .
#       Rollback = re-upload that zip. Nothing else is needed.
#    1. Zip-swaps handler.py + sql/50 + sql/51 into the deployed artefact.
#       deploy.sh is NOT used, on purpose: it runs migrate() unconditionally,
#       which re-applies sql/01-25 including sql/08 (re-seeds hardcoded PS2 rows
#       for 2026-07-14) and sql/18 (DELETEs from ps3_severity_predictions).
#       Retiring a PS1 table must not put PS2 or PS3 data at risk.
#    2. apply_sql DRY-RUN for both files -- shows the statements, touches nothing.
#    3. STOPS. You read the dry-run, then re-run with APPLY=1 to execute.
#    4. Curls the four affected routes before and after.
#
#  NOTHING IS DROPPED, DELETED OR ALTERED. Both SQL files only create views and
#  write catalog comments. ps1_failure_summary keeps its two rows.
#
#  PREREQUISITE
#    Upload ps1_retire_20260810.zip via CloudShell Actions -> Upload file.
#    It must contain, at these exact paths:
#        handler.py
#        sql/50_ps1_xw_causation_all_fleets.sql
#        sql/51_ps1_failure_summary_retire.sql
# =====================================================================
set -uo pipefail
REGION=us-east-1
FN=cubic-mars-dashboard-api
UPLOAD=${UPLOAD:-$HOME/ps1_retire_20260810.zip}
APPLY=${APPLY:-0}
STAMP=$(date -u +%Y%m%dT%H%M%SZ)
BK="$HOME/ps1_retire_backup"
WORK=$(mktemp -d)
mkdir -p "$BK"

api_url() {
  aws apigatewayv2 get-apis --region "$REGION" \
    --query "Items[?contains(Name,'cubic')||contains(Name,'mars')].ApiEndpoint" \
    --output text 2>/dev/null | tr '\t' '\n' | head -1
}
API=$(api_url)

show_routes() {
  local tag="$1"
  echo
  echo "---- ROUTES ($tag) ----------------------------------------------"
  if [ -z "$API" ]; then echo "   (no API endpoint found; skipping)"; return; fi
  for P in "/ps1/summary" "/ps1/model-performance" "/ps1/xw-causation"; do
    echo "   GET $P?city=CHI"
    curl -s --max-time 25 "$API$P?city=CHI" \
      | python3 -c "
import json,sys
try: d=json.load(sys.stdin)
except Exception as e: print('     (unparseable):', str(e)[:80]); raise SystemExit
if not isinstance(d,list): print('     ', str(d)[:200]); raise SystemExit
print('     rows:', len(d))
for r in d[:5]:
    if 'device' in r and 'quality_gate' in r:
        print('       %-10s %-26s auc=%-8s gate=%-5s prom=%-5s target=%s' % (
            r.get('device'), str(r.get('champion_model'))[:26], r.get('test_auc'),
            r.get('quality_gate'), r.get('promoted'), r.get('target')))
        print('           source:', r.get('source_table'))
    elif 'device_type' in r:
        print('       %-10s n_chain=%-8s lift=%-8s sufficient=%-6s min_cell=%s' % (
            r.get('device_type'), r.get('n_chain'), r.get('critical_lift'),
            r.get('sufficient_data'), r.get('min_cell')))
    else:
        print('       %-10s %-24s floor=%-6s met=%-6s target=%s run=%s' % (
            r.get('device_category'), str(r.get('model_name'))[:24], r.get('recall_floor'),
            r.get('recall_floor_met'), r.get('target'), r.get('run_id')))
"
  done
}

echo "== PS1 retire + causation, $STAMP"
echo "   function: $FN"
echo "   api     : ${API:-<none found>}"
echo "   mode    : $([ "$APPLY" = 1 ] && echo 'APPLY (will execute)' || echo 'DRY-RUN ONLY')"

show_routes "BEFORE"

# ------------------------------------------------------------ 0. backup
echo
echo ">> [0/4] backing up the deployed package"
URL=$(aws lambda get-function --function-name "$FN" --region "$REGION" \
        --query 'Code.Location' --output text) || exit 1
curl -s -o "$BK/${FN}_$STAMP.zip" "$URL" || exit 1
echo "   $BK/${FN}_$STAMP.zip  ($(du -h "$BK/${FN}_$STAMP.zip" | cut -f1))"
echo "   ROLLBACK: aws lambda update-function-code --function-name $FN \\"
echo "               --region $REGION --zip-file fileb://$BK/${FN}_$STAMP.zip"

# ------------------------------------------------------------ 1. zip-swap
echo
echo ">> [1/4] swapping handler.py + sql/50 + sql/51 into the package"
if [ ! -f "$UPLOAD" ]; then
  echo "   MISSING: $UPLOAD"
  echo "   Upload ps1_retire_20260810.zip first (Actions -> Upload file), then re-run."
  exit 1
fi
mkdir -p "$WORK/pkg" "$WORK/new"
unzip -qo "$BK/${FN}_$STAMP.zip" -d "$WORK/pkg" || exit 1
unzip -qo "$UPLOAD"              -d "$WORK/new" || exit 1
for F in handler.py sql/50_ps1_xw_causation_all_fleets.sql sql/51_ps1_failure_summary_retire.sql; do
  if [ ! -f "$WORK/new/$F" ]; then echo "   MISSING FROM UPLOAD: $F"; exit 1; fi
  mkdir -p "$(dirname "$WORK/pkg/$F")"
  cp "$WORK/new/$F" "$WORK/pkg/$F"
  printf '   %-52s %8s bytes\n' "$F" "$(stat -c%s "$WORK/pkg/$F")"
done
python3 -c "import ast,sys;ast.parse(open('$WORK/pkg/handler.py').read());print('   handler.py parses')" || exit 1
( cd "$WORK/pkg" && zip -qr "$WORK/deploy.zip" . ) || exit 1
aws lambda update-function-code --function-name "$FN" --region "$REGION" \
    --zip-file "fileb://$WORK/deploy.zip" --query 'LastModified' --output text || exit 1
aws lambda wait function-updated --function-name "$FN" --region "$REGION"
echo "   code updated. Configuration, layers, env and IAM were NOT touched."

# ------------------------------------------------------------ 2. dry-run
echo
echo ">> [2/4] apply_sql DRY-RUN (touches nothing)"
for F in 50_ps1_xw_causation_all_fleets.sql 51_ps1_failure_summary_retire.sql; do
  echo "   -- $F"
  aws lambda invoke --function-name "$FN" --region "$REGION" \
    --cli-binary-format raw-in-base64-out \
    --payload "{\"action\":\"apply_sql\",\"file\":\"$F\",\"dry_run\":true}" \
    "$WORK/dry_$F.json" >/dev/null 2>&1
  python3 -c "
import json;d=json.load(open('$WORK/dry_$F.json'))
b=json.loads(d.get('body','{}')) if isinstance(d.get('body'),str) else d
a=b.get('apply_sql',b)
print('      statements:',a.get('statements'))
for i,l in enumerate(a.get('first_lines',[]),1): print('        [%d] %s'%(i,l[:76]))
if 'error' in b: print('      ERROR:', b['error'])
"
done

if [ "$APPLY" != "1" ]; then
  echo
  echo "======================================================================"
  echo " DRY-RUN COMPLETE. Nothing was written to the database."
  echo " The Lambda CODE is updated but /ps1/xw-causation will 500 or return"
  echo " nothing useful until sql/50 is applied -- the route now selects"
  echo " sufficient_data and min_cell, which the old view does not have."
  echo
  echo " Read the statement lists above, then run:"
  echo "     APPLY=1 bash $0"
  echo "======================================================================"
  exit 0
fi

# ------------------------------------------------------------ 3. apply
echo
echo ">> [3/4] APPLYING"
for F in 50_ps1_xw_causation_all_fleets.sql 51_ps1_failure_summary_retire.sql; do
  echo "   -- $F"
  aws lambda invoke --function-name "$FN" --region "$REGION" \
    --cli-binary-format raw-in-base64-out \
    --payload "{\"action\":\"apply_sql\",\"file\":\"$F\"}" \
    "$WORK/app_$F.json" >/dev/null 2>&1
  python3 -c "
import json;d=json.load(open('$WORK/app_$F.json'))
b=json.loads(d.get('body','{}')) if isinstance(d.get('body'),str) else d
a=b.get('apply_sql',b)
print('      statements=%s applied=%s tolerated=%s failed=%s'%(
    a.get('statements'),a.get('applied'),a.get('tolerated'),a.get('failed')))
for e in (a.get('errors') or []): print('      ERROR:', e[:200])
"
done

# ------------------------------------------------------------ 4. verify
echo
echo ">> [4/4] verifying"
show_routes "AFTER"

echo
echo "======================================================================"
echo " EXPECTED AFTER STATE"
echo "   /ps1/summary          3 rows -- Validator, TVM, Gates"
echo "                         gate=PASS  promoted=True"
echo "                         target=will_hardware_oos_3d   (NOT will_fail_3d)"
echo "                         source: ps1_model_performance + ps1_confusion"
echo "   /ps1/model-performance  recall_floor / recall_floor_met / target / run_id present"
echo "   /ps1/xw-causation     3 fleets. GATE present with sufficient_data=false,"
echo "                         lift=None and min_cell showing the shortfall."
echo
echo " IF /ps1/summary STILL SAYS source: ps1_failure_summary ... RETIRED"
echo "   that is the FALLBACK firing -- ps1_model_performance has no row for CHI."
echo "   It is not a failure of this change; it means the 26-Jul load is missing."
echo
echo " ROLLBACK (code):"
echo "   aws lambda update-function-code --function-name $FN \\"
echo "     --region $REGION --zip-file fileb://$BK/${FN}_$STAMP.zip"
echo " ROLLBACK (views): re-apply sql/34 and sql/36 -- both files carry the"
echo "   exact statements in their footers. Each needs its DROP VIEW first;"
echo "   CREATE OR REPLACE alone raises 42P16 (cannot drop columns from view)."
echo "======================================================================"
