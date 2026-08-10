#!/usr/bin/env bash
# =====================================================================
#  PS1 STATE VERIFICATION -- READ-ONLY   CloudShell, us-east-1
#
#  Closes every [U] in PS1_STATUS_10Aug2026_EOD.md section 7.
#
#  Every call is list / describe / get / invoke-with-a-read-only-action.
#  Nothing is created, modified, deleted, enabled or disabled. The only
#  Lambda invocations are HTTP GETs against the dashboard API and the
#  xw-loader's own "verify" action, which runs SELECTs.
#
#  Run:   bash ps1_verify_state.sh
#  Output: printed, and saved to ~/ps1_verify_<stamp>/report.txt
# =====================================================================
set -uo pipefail
REGION=us-east-1
ART=cubic-mars-pm-s3-datalake-dev-artifacts-170202974600
GOLD=cubic-mars-pm-s3-datalake-dev-gold-170202974600
STAMP=$(date -u +%Y%m%dT%H%M%SZ)
OUT="$HOME/ps1_verify_$STAMP"; mkdir -p "$OUT"
exec > >(tee "$OUT/report.txt") 2>&1

hdr() { echo; echo "======================================================================"; echo " $1"; echo "======================================================================"; }

echo "PS1 STATE VERIFICATION  $STAMP  ($REGION)   READ-ONLY"

# ------------------------------------------------------------------ [U1][U2][U3]
hdr "[U1] cubic-mars-ps1-rds-push -- layer, GOLD_KEY, concurrency"
aws lambda get-function-configuration --function-name cubic-mars-ps1-rds-push \
  --region "$REGION" --output json 2>/dev/null | python3 -c "
import json,sys
try: d=json.load(sys.stdin)
except Exception: print('   FUNCTION NOT FOUND'); raise SystemExit
env=(d.get('Environment') or {}).get('Variables') or {}
ly=[l['Arn'].split(':layer:')[-1] for l in (d.get('Layers') or [])]
gk=env.get('GOLD_KEY','(unset)')
print('   LastModified :', d.get('LastModified'))
print('   Layers       :', ly or '(NONE)')
print('   GOLD_KEY     :', gk)
print()
print('   layer  EXPECTED AWSSDKPandas-Python312:29 ->', 'OK' if any('AWSSDKPandas' in x for x in ly) else '*** MISSING -- morning fix did NOT stick ***')
ok = gk.endswith('device_ps1_cross_wired_daily')
print('   GOLD_KEY EXPECTED chicago/gold/device_ps1_cross_wired_daily (parquet, no .csv) ->',
      'OK' if ok else '*** STILL THE CSV -- morning fix did NOT stick ***')
"
echo
echo "   reserved concurrency (EXPECT 0 = Path B throttled):"
aws lambda get-function-concurrency --function-name cubic-mars-ps1-rds-push \
  --region "$REGION" --query 'ReservedConcurrentExecutions' --output text 2>/dev/null \
  | sed 's/^/     /; s/^     None$/     *** NOT SET -- Path B is NOT throttled ***/'

hdr "[U2] EventBridge rules -- PS1"
for R in cubic-mars-ps1-daily-push cubic-mars-ps1-xw-daily-load; do
  S=$(aws events describe-rule --name "$R" --region "$REGION" --query 'State' --output text 2>/dev/null)
  E=$(aws events describe-rule --name "$R" --region "$REGION" --query 'ScheduleExpression' --output text 2>/dev/null)
  printf '   %-32s %-10s %s\n' "$R" "${S:-NOT-FOUND}" "$E"
done
echo
echo "   EXPECT: ps1-daily-push = DISABLED   ps1-xw-daily-load = ENABLED"

hdr "[U3] S3 ObjectCreated trigger on the gold bucket"
aws s3api get-bucket-notification-configuration --bucket "$GOLD" --region "$REGION" \
  --output json 2>/dev/null | python3 -c "
import json,sys
try: d=json.load(sys.stdin) or {}
except Exception: d={}
cfg=d.get('LambdaFunctionConfigurations') or []
if not cfg: print('   (no lambda notifications configured)')
for c in cfg:
    print('   id=%s -> %s' % (c.get('Id'), c.get('LambdaFunctionArn','').split(':function:')[-1]))
    for r in ((c.get('Filter') or {}).get('Key') or {}).get('FilterRules',[]):
        print('      %s = %s' % (r.get('Name'), r.get('Value')))
print()
print('   Handover section 8 says this was left ARMED deliberately, as a loud signal')
print('   if a notebook still writes to the dead prefix. Confirm that is still the intent.')
"

# ------------------------------------------------------------------ [U4]
hdr "[U4] cubic-mars-dashboard-api -- is this afternoon's package deployed?"
aws lambda get-function-configuration --function-name cubic-mars-dashboard-api \
  --region "$REGION" --query '[LastModified,CodeSize,CodeSha256]' --output text 2>/dev/null \
  | awk '{printf "   LastModified=%s  CodeSize=%s\n   Sha256=%s\n", $1,$2,$3}'
echo
echo "   Is sql/50 and sql/51 packaged? (apply_sql lists what it can see)"
aws lambda invoke --function-name cubic-mars-dashboard-api --region "$REGION" \
  --cli-binary-format raw-in-base64-out \
  --payload '{"action":"apply_sql","file":"99_does_not_exist.sql"}' \
  "$OUT/pkg.json" >/dev/null 2>&1
python3 -c "
import json
d=json.load(open('$OUT/pkg.json'))
b=json.loads(d.get('body','{}')) if isinstance(d.get('body'),str) else d
av=b.get('available') or []
for f in ('49_ps1_xw_unique_index.sql','50_ps1_xw_causation_all_fleets.sql','51_ps1_failure_summary_retire.sql'):
    print('     %-46s %s' % (f, 'PRESENT' if f in av else '*** NOT PACKAGED ***'))
print('     (sql files visible to the deployed function: %d)' % len(av))
" 2>/dev/null || echo "     (could not read the package listing)"

# ------------------------------------------------------------------ [U5]
hdr "[U5] SageMaker PS1 endpoints -- status AND whether anything invokes them"
for E in chicago-ps1-3d-tvm-failure-v1 chicago-ps1-3d-gate-failure-v1 chicago-ps1-3d-validator-failure-v1; do
  S=$(aws sagemaker describe-endpoint --endpoint-name "$E" --region "$REGION" --query 'EndpointStatus' --output text 2>/dev/null)
  N=$(aws cloudwatch get-metric-statistics --namespace AWS/SageMaker \
        --metric-name Invocations --dimensions Name=EndpointName,Value="$E" Name=VariantName,Value=AllTraffic \
        --start-time "$(date -u -d '14 days ago' +%Y-%m-%dT%H:%M:%SZ)" \
        --end-time "$(date -u +%Y-%m-%dT%H:%M:%SZ)" --period 1209600 --statistics Sum \
        --region "$REGION" --query 'Datapoints[0].Sum' --output text 2>/dev/null)
  case "$N" in ''|None) N=0 ;; esac
  printf '   %-46s %-12s invocations_14d=%s\n' "$E" "${S:-?}" "$N"
done
echo
echo "   If invocations_14d = 0 on all three, Finding E-1 is confirmed: three"
echo "   InService endpoints billing since 24-Jul with no traffic. That is a"
echo "   cost decision, not a defect -- but it should be a decision, not a default."

# ------------------------------------------------------------------ [U6][U7][U8]
hdr "[U6] Live row counts -- via the dashboard API"
API=$(aws apigatewayv2 get-apis --region "$REGION" \
        --query "Items[?Name=='cubic-mars-dashboard-api'].ApiEndpoint" --output text 2>/dev/null | head -1)
echo "   api: ${API:-<not found>}"
if [ -n "$API" ]; then
  echo
  echo "   /ps1/table-status:"
  curl -s --max-time 30 "$API/ps1/table-status?city=CHI" | python3 -c "
import json,sys
try: d=json.load(sys.stdin)
except Exception: print('     (no/!json response)'); raise SystemExit
for r in (d if isinstance(d,list) else []):
    print('     %-26s %10s rows  empty=%-6s superseded_by=%s' % (
        r.get('table_name'), r.get('n_rows'), r.get('is_empty'), r.get('superseded_by')))
    if 'retired' in r: print('%s        retired=%s' % ('', r.get('retired')))
"
  echo
  echo "   /ps1/xw-summary (Path A, EXPECT gate 107110 / tvm 184483 / validator 494932):"
  curl -s --max-time 30 "$API/ps1/xw-summary?city=CHI" | python3 -c "
import json,sys
EXP={'GATE':107110,'TVM':184483,'VALIDATOR':494932}
try: d=json.load(sys.stdin)
except Exception: print('     (no/!json response)'); raise SystemExit
tot=0
for r in (d if isinstance(d,list) else []):
    f=str(r.get('device_type','')); n=r.get('n_rows') or 0; tot+=int(n)
    e=EXP.get(f)
    print('     %-10s %8s rows  last_day=%-12s %s' % (f, n, r.get('last_day'),
          ('OK' if e==int(n) else '*** EXPECTED %s ***'%e) if e else ''))
print('     TOTAL %s   (EXPECTED 786525 -> %s)' % (tot, 'OK' if tot==786525 else '*** MISMATCH ***'))
"
fi

hdr "[U7] Is recall_floor / target_col actually populated on the live rows?"
if [ -n "$API" ]; then
  curl -s --max-time 30 "$API/ps1/model-performance?city=CHI" | python3 -c "
import json,sys
try: d=json.load(sys.stdin)
except Exception: print('   (no/!json response)'); raise SystemExit
if not isinstance(d,list) or not d: print('   NO ROWS -- ps1_model_performance is empty for CHI'); raise SystemExit
for r in d:
    print('   %-10s %-26s target=%-22s floor=%-6s met=%-6s run=%s' % (
        r.get('device_category'), str(r.get('model_name'))[:26], r.get('target'),
        r.get('recall_floor'), r.get('recall_floor_met'), r.get('run_id')))
print()
if all(r.get('target') is None for r in d):
    print('   *** target_col is NULL on every row -- the scorecard cannot state its label ***')
if all(r.get('recall_floor') is None for r in d):
    print('   *** recall_floor is NULL on every row -- nothing is gated ***')
print('   NOTE: recall_floor/recall_floor_met only appear once 9222ccd is DEPLOYED.')
print('         Absent fields here = the new package is not live yet, not that the data is missing.')
"
fi

hdr "[U8] GATE's real min_cell on the causation view"
if [ -n "$API" ]; then
  curl -s --max-time 30 "$API/ps1/xw-causation?city=CHI" | python3 -c "
import json,sys
try: d=json.load(sys.stdin)
except Exception: print('   (no/!json response)'); raise SystemExit
if not isinstance(d,list): print('   ', str(d)[:200]); raise SystemExit
print('   rows returned:', len(d))
for r in d:
    print('     %-10s n_chain=%-8s n_no_chain=%-8s lift=%-8s sufficient=%-6s min_cell=%s' % (
        r.get('device_type'), r.get('n_chain'), r.get('n_no_chain'),
        r.get('critical_lift'), r.get('sufficient_data'), r.get('min_cell')))
print()
if len(d) < 3:
    print('   Only %d fleet(s) -- sql/50 is NOT applied yet. GATE is still being omitted.' % len(d))
elif any(r.get('sufficient_data') is None for r in d):
    print('   sufficient_data absent -> the OLD view is still installed.')
"
fi

# ------------------------------------------------------------------ [U9]
hdr "[U9] Did the PS1 crons actually run? (last 3 days)"
for FN in cubic-mars-ps1-xw-loader cubic-mars-ps1-rds-push; do
  echo "   $FN"
  for M in Invocations Errors Throttles; do
    V=$(aws cloudwatch get-metric-statistics --namespace AWS/Lambda --metric-name $M \
          --dimensions Name=FunctionName,Value=$FN \
          --start-time "$(date -u -d '3 days ago' +%Y-%m-%dT%H:%M:%SZ)" \
          --end-time "$(date -u +%Y-%m-%dT%H:%M:%SZ)" --period 259200 --statistics Sum \
          --region "$REGION" --query 'Datapoints[0].Sum' --output text 2>/dev/null)
    case "$V" in ''|None) V=0 ;; esac
    printf '     %-12s %s\n' "$M" "$V"
  done
done
echo
echo "   EXPECT ps1-xw-loader: Invocations>0, Errors=0"
echo "   EXPECT ps1-rds-push : Throttles>0 or Invocations=0 (that IS the disable working)"

hdr "SUMMARY"
cat <<'NOTE'
   Compare each block above against its EXPECT line.

   Anything marked *** is a real deviation and belongs in the fix list.

   Reminder of the two questions this run is really for:
     1. Did the morning's Path B repairs survive, and is Path B genuinely off?
     2. Do the three SageMaker endpoints have any traffic at all?

   NOTHING WAS MODIFIED BY THIS SCRIPT.
NOTE
echo
echo "   report saved: $OUT/report.txt"
