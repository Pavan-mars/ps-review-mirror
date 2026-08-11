#!/usr/bin/env bash
# =====================================================================
#  PS1 GO-LIVE -- deploy what is committed, apply what is written,
#                 then measure everything.   CloudShell, us-east-1.
#
#  Closes the gap between the repository and production:
#    * dashboard-api  -> scorecard repoint, causation route, write guard,
#                        serving disclosure, PS4 PATCH fix, sql/50 + sql/51
#    * ps1-xw-loader  -> source-freshness verdict + load lineage
#    * then runs ps1_verify_state.sh and lambda_drift_check.sh
#
#  DEPLOYS BY ZIP-SWAP, NOT deploy.sh, for both functions. deploy.sh for
#  dashboard-api runs migrate() unconditionally, re-applying sql/01-25 --
#  including sql/08, which re-seeds hardcoded PS2 rows for 2026-07-14, and
#  sql/18, which DELETEs from ps3_severity_predictions. Shipping a PS1 fix
#  must not touch PS2 or PS3 data. Zip-swap changes code and nothing else:
#  no IAM, no env, no layers, no EventBridge rule, no bucket notification.
#
#  STAGE 0 backs up BOTH deployed packages first. Rollback is one command
#  per function, printed at the end and again on any failure.
#
#  Usage:
#      bash ps1_go_live.sh            # dry run: backs up, reports, changes NOTHING
#      APPLY=1 bash ps1_go_live.sh    # execute
#
#  Prerequisite: upload ps1_golive_20260810.zip via Actions -> Upload file.
#  Expected contents:
#      dashboard-api/handler.py
#      dashboard-api/sql/50_ps1_xw_causation_all_fleets.sql
#      dashboard-api/sql/51_ps1_failure_summary_retire.sql
#      xw-loader/handler.py
#      tooling/ps1_verify_state.sh
#      tooling/lambda_drift_check.sh
# =====================================================================
set -uo pipefail
REGION=us-east-1
API_FN=cubic-mars-dashboard-api
XW_FN=cubic-mars-ps1-xw-loader
UPLOAD=${UPLOAD:-$HOME/ps1_golive_20260810.zip}
APPLY=${APPLY:-0}
STAMP=$(date -u +%Y%m%dT%H%M%SZ)
BK="$HOME/ps1_golive_$STAMP"; mkdir -p "$BK"
WORK=$(mktemp -d)
FAILED=0

say()  { echo; echo "=====================================================================" ; echo " $1"; echo "====================================================================="; }
note() { printf '   %s\n' "$1"; }
die()  { echo; echo "!! $1"; echo "!! ROLLBACK:"; echo "     aws lambda update-function-code --function-name $API_FN --region $REGION --zip-file fileb://$BK/${API_FN}.zip"; echo "     aws lambda update-function-code --function-name $XW_FN  --region $REGION --zip-file fileb://$BK/${XW_FN}.zip"; exit 1; }

API_URL=$(aws apigatewayv2 get-apis --region "$REGION" \
   --query "Items[?Name=='$API_FN'].ApiEndpoint" --output text 2>/dev/null | head -1)

probe() {
  local tag="$1"
  say "ROUTE PROBE -- $tag"
  [ -z "$API_URL" ] && { note "(no API endpoint found)"; return; }
  note "GET /ps1/summary"
  curl -s --max-time 30 "$API_URL/ps1/summary?city=CHI" | python3 -c "
import json,sys
try: d=json.load(sys.stdin)
except Exception: print('     unparseable'); raise SystemExit
if not isinstance(d,list) or not d: print('     no rows'); raise SystemExit
print('     rows:',len(d))
for r in d:
    print('       %-10s gate=%-5s prom=%-5s target=%-22s' % (r.get('device'),r.get('quality_gate'),r.get('promoted'),r.get('target')))
print('       source:', d[0].get('source_table'))
"
  note "GET /ps1/xw-causation"
  curl -s --max-time 30 "$API_URL/ps1/xw-causation?city=CHI" | python3 -c "
import json,sys
try: d=json.load(sys.stdin)
except Exception: print('     unparseable'); raise SystemExit
if not isinstance(d,list): print('     ',str(d)[:160]); raise SystemExit
print('     fleets:',len(d))
for r in d:
    print('       %-10s lift=%-8s sufficient=%-6s min_cell=%s' % (r.get('device_type'),r.get('critical_lift'),r.get('sufficient_data'),r.get('min_cell')))
if d and d[0].get('schema_note'): print('       NOTE: sql/50 not applied yet')
"
  note "GET /ps1/model-performance  (serving disclosure)"
  curl -s --max-time 30 "$API_URL/ps1/model-performance?city=CHI" | python3 -c "
import json,sys
try: d=json.load(sys.stdin)
except Exception: print('     unparseable'); raise SystemExit
for r in (d if isinstance(d,list) else []):
    print('       %-10s serving_matches=%-6s serving_run=%s' % (r.get('device_category'),r.get('serving_matches_scorecard'),r.get('serving_run_id')))
"
}

say "PS1 GO-LIVE  $STAMP   mode=$([ "$APPLY" = 1 ] && echo APPLY || echo DRY-RUN)"
note "api: ${API_URL:-<none>}"

probe "BEFORE"

# ------------------------------------------------------------------ 0 backup
say "[0/6] BACK UP BOTH DEPLOYED PACKAGES"
for FN in "$API_FN" "$XW_FN"; do
  U=$(aws lambda get-function --function-name "$FN" --region "$REGION" --query 'Code.Location' --output text 2>/dev/null) \
    || die "cannot read $FN"
  curl -s -o "$BK/$FN.zip" "$U" || die "cannot download $FN package"
  printf '   %-32s %s\n' "$FN" "$(du -h "$BK/$FN.zip" | cut -f1)  -> $BK/$FN.zip"
done

# ------------------------------------------------------------------ 1 stage
say "[1/6] STAGE THE NEW FILES"
[ -f "$UPLOAD" ] || die "missing $UPLOAD -- upload it via Actions -> Upload file"
mkdir -p "$WORK/new"; unzip -qo "$UPLOAD" -d "$WORK/new" || die "cannot unzip upload"
for F in dashboard-api/handler.py \
         dashboard-api/sql/50_ps1_xw_causation_all_fleets.sql \
         dashboard-api/sql/51_ps1_failure_summary_retire.sql \
         xw-loader/handler.py; do
  [ -f "$WORK/new/$F" ] || die "upload is missing $F"
  printf '   %-56s %8s bytes\n' "$F" "$(stat -c%s "$WORK/new/$F")"
done
python3 -c "import ast;ast.parse(open('$WORK/new/dashboard-api/handler.py').read())" || die "dashboard-api handler.py does not parse"
python3 -c "import ast;ast.parse(open('$WORK/new/xw-loader/handler.py').read())"     || die "xw-loader handler.py does not parse"
note "both handlers parse"

if [ "$APPLY" != "1" ]; then
  say "DRY RUN COMPLETE -- NOTHING WAS CHANGED"
  note "Backups are in $BK (taken even on a dry run, deliberately)."
  note "Re-run with:  APPLY=1 bash $0"
  exit 0
fi

# ------------------------------------------------------------------ 2 api code
say "[2/6] DEPLOY dashboard-api (code only)"
mkdir -p "$WORK/api"; unzip -qo "$BK/$API_FN.zip" -d "$WORK/api" || die "cannot unpack api backup"
cp "$WORK/new/dashboard-api/handler.py" "$WORK/api/handler.py"
mkdir -p "$WORK/api/sql"
cp "$WORK/new/dashboard-api/sql/"*.sql "$WORK/api/sql/"
( cd "$WORK/api" && zip -qr "$WORK/api.zip" . ) || die "cannot rezip api"
aws lambda update-function-code --function-name "$API_FN" --region "$REGION" \
    --zip-file "fileb://$WORK/api.zip" --query 'LastModified' --output text || die "api code update failed"
aws lambda wait function-updated --function-name "$API_FN" --region "$REGION"
note "deployed. Config, env, layers, IAM and the gateway were NOT touched."
note "The causation route is order-independent: it works with or without sql/50."

# ------------------------------------------------------------------ 3 sql
say "[3/6] APPLY sql/50 AND sql/51"
for F in 50_ps1_xw_causation_all_fleets.sql 51_ps1_failure_summary_retire.sql; do
  note "-- $F  (dry run first)"
  aws lambda invoke --function-name "$API_FN" --region "$REGION" --cli-binary-format raw-in-base64-out \
    --payload "{\"action\":\"apply_sql\",\"file\":\"$F\",\"dry_run\":true}" "$WORK/d_$F.json" >/dev/null 2>&1
  python3 -c "
import json;d=json.load(open('$WORK/d_$F.json'))
b=json.loads(d.get('body','{}')) if isinstance(d.get('body'),str) else d
a=b.get('apply_sql',b)
print('      statements:',a.get('statements'))
if 'error' in b: print('      ERROR:',b['error'])
"
  note "-- $F  (applying)"
  aws lambda invoke --function-name "$API_FN" --region "$REGION" --cli-binary-format raw-in-base64-out \
    --payload "{\"action\":\"apply_sql\",\"file\":\"$F\"}" "$WORK/a_$F.json" >/dev/null 2>&1
  python3 -c "
import json,sys;d=json.load(open('$WORK/a_$F.json'))
b=json.loads(d.get('body','{}')) if isinstance(d.get('body'),str) else d
a=b.get('apply_sql',b)
print('      statements=%s applied=%s tolerated=%s failed=%s'%(a.get('statements'),a.get('applied'),a.get('tolerated'),a.get('failed')))
for e in (a.get('errors') or []): print('      ERROR:',e[:200])
sys.exit(1 if (a.get('failed') or 0) else 0)
" || { echo "   !! $F reported failures -- STOPPING before the loader deploy"; FAILED=1; break; }
done
[ "$FAILED" = 1 ] && die "SQL apply failed. The api code is deployed and is order-independent, so the panel still serves. Fix the SQL, re-run."

# ------------------------------------------------------------------ 4 loader
say "[4/6] DEPLOY ps1-xw-loader (code only)"
mkdir -p "$WORK/xw"; unzip -qo "$BK/$XW_FN.zip" -d "$WORK/xw" || die "cannot unpack loader backup"
cp "$WORK/new/xw-loader/handler.py" "$WORK/xw/handler.py"
( cd "$WORK/xw" && zip -qr "$WORK/xw.zip" . ) || die "cannot rezip loader"
aws lambda update-function-code --function-name "$XW_FN" --region "$REGION" \
    --zip-file "fileb://$WORK/xw.zip" --query 'LastModified' --output text || die "loader code update failed"
aws lambda wait function-updated --function-name "$XW_FN" --region "$REGION"
note "deployed. Layer, env, IAM, VPC and the cron rule were NOT touched."

note "freshness probe (read-only, no load, no write):"
aws lambda invoke --function-name "$XW_FN" --region "$REGION" --cli-binary-format raw-in-base64-out \
  --payload '{"action":"freshness"}' "$WORK/fresh.json" >/dev/null 2>&1
python3 -c "
import json
d=json.load(open('$WORK/fresh.json'))
f=(d.get('source_freshness') or {})
print('      verdict:', f.get('verdict'))
for k,v in sorted((f.get('per_fleet') or {}).items()):
    print('        %-10s %-14s age=%-8s etag=%s' % (k, v.get('state'), v.get('age_days'), str(v.get('etag'))[:12]))
if not f: print('      (no source_freshness in response)'); print(str(d)[:300])
"

# ------------------------------------------------------------------ 5 verify
say "[5/6] VERIFY"
probe "AFTER"

# ------------------------------------------------------------------ 6 sweeps
say "[6/6] STATE VERIFICATION + DRIFT SWEEP"
for S in ps1_verify_state.sh lambda_drift_check.sh; do
  if [ -f "$WORK/new/tooling/$S" ]; then
    cp "$WORK/new/tooling/$S" "$HOME/$S"; chmod +x "$HOME/$S"
    note "staged $HOME/$S"
  fi
done
note "Run these next -- both are read-only and neither is invoked automatically,"
note "so their output is not buried inside this deploy log:"
note "    bash ~/ps1_verify_state.sh"
note "    cd <repo-root> && bash ~/lambda_drift_check.sh     # needs api/lambda/ to diff against"

say "DONE"
cat <<EOF
   EXPECTED AFTER STATE
     /ps1/summary          3 rows, Validator/TVM/Gates, gate=PASS, promoted=True
                           target=will_hardware_oos_3d  (NOT will_fail_3d)
                           source: ps1_model_performance + ps1_confusion
     /ps1/xw-causation     3 fleets. GATE present, sufficient_data=false,
                           lift=None, min_cell showing the real shortfall.
     /ps1/model-performance  serving_matches_scorecard populated per fleet.

   IF /ps1/summary STILL SAYS source: ps1_failure_summary ... RETIRED
     that is the FALLBACK, not a failure: ps1_model_performance has no row for
     CHI. It means the 26-Jul sql/load run is missing, not that this deploy
     broke anything.

   ROLLBACK (code only -- neither SQL file writes a data row):
     aws lambda update-function-code --function-name $API_FN --region $REGION \\
       --zip-file fileb://$BK/$API_FN.zip
     aws lambda update-function-code --function-name $XW_FN --region $REGION \\
       --zip-file fileb://$BK/$XW_FN.zip
   ROLLBACK (views): re-apply sql/34 and sql/36. Each needs its DROP VIEW first;
     CREATE OR REPLACE alone raises 42P16 cannot drop columns from view.
EOF
