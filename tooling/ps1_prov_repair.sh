#!/usr/bin/env bash
# =====================================================================
#  PS1 PROVENANCE REPAIR -- applies sql/53, which fixes sql/52's one
#  failed statement.   CloudShell, us-east-1.
#
#  sql/52 applied 5 of 6. Statement 1 failed with
#      42703 column "label_revision" does not exist
#  because it selected label_revision FROM ps1_inference_runs, and that
#  table has no such column. recall_floor and the gaps view DID apply and
#  are not repeated. sql/53 redoes only the run_id / target_col backfill,
#  plus label_revision derived from target_col.
#
#  NO CODE CHANGE. The handler deployed at 06:12 is already correct --
#  the run_kind fix is live, confirmed by kind=train on all three fleets.
#  This only adds sql/53 to the package so apply_sql can see it, then
#  applies it.
#
#      bash ps1_prov_repair.sh            # dry run
#      APPLY=1 bash ps1_prov_repair.sh    # execute
# =====================================================================
set -uo pipefail
REGION=us-east-1
FN=cubic-mars-dashboard-api
UPLOAD=${UPLOAD:-$(ls -1t "$HOME"/ps1_provrepair_*.zip 2>/dev/null | head -1)}
APPLY=${APPLY:-0}
STAMP=$(date -u +%Y%m%dT%H%M%SZ)
BK="$HOME/ps1_repair_$STAMP"; mkdir -p "$BK"; WORK=$(mktemp -d)
die(){ echo; echo "!! $1"; echo "!! ROLLBACK: aws lambda update-function-code --function-name $FN --region $REGION --zip-file fileb://$BK/$FN.zip"; exit 1; }
API=$(aws apigatewayv2 get-apis --region $REGION --query "Items[?Name=='$FN'].ApiEndpoint" --output text 2>/dev/null | head -1)

show(){ echo; echo "---- $1 ----"; curl -s --max-time 30 "$API/ps1/model-performance?city=CHI" | python3 -c "
import json,sys
try: d=json.load(sys.stdin)
except Exception: print('   unparseable'); raise SystemExit
for r in (d if isinstance(d,list) else []):
    print('   %-10s target=%-22s run_id=%-22s serving=%-22s kind=%-7s matches=%s' % (
        r.get('device_category'), r.get('target'), r.get('run_id'),
        r.get('serving_run_id'), r.get('serving_run_kind'), r.get('serving_matches_scorecard')))
"; }

echo "== PS1 PROVENANCE REPAIR $STAMP  mode=$([ "$APPLY" = 1 ] && echo APPLY || echo DRY-RUN)"
[ -n "${UPLOAD:-}" ] && [ -f "$UPLOAD" ] || die "no ps1_provrepair_*.zip in \$HOME -- set UPLOAD=/path/to/zip"
echo "   using upload: $UPLOAD"
unzip -l "$UPLOAD" 2>/dev/null | grep -q "53_ps1_provenance_repair.sql" \
  || die "that zip does NOT contain sql/53 -- wrong bundle"
echo "   contains sql/53: yes"
show "BEFORE"

U=$(aws lambda get-function --function-name $FN --region $REGION --query 'Code.Location' --output text) || die "cannot read $FN"
curl -s -o "$BK/$FN.zip" "$U" || die "cannot back up"
echo "   backup: $BK/$FN.zip"

mkdir -p "$WORK/new"; unzip -qo "$UPLOAD" -d "$WORK/new" || die "cannot unzip upload"

if [ "$APPLY" != 1 ]; then echo; echo "DRY RUN COMPLETE -- nothing changed. APPLY=1 to execute."; exit 0; fi

mkdir -p "$WORK/pkg"; unzip -qo "$BK/$FN.zip" -d "$WORK/pkg" || die "cannot unpack backup"
# 2026-08-11. A Lambda package unzips with the permissions it was zipped with,
# and sql/*.sql came out read-only -- so `cp` over them failed with
#     cp: cannot create regular file ...: Permission denied
# on the previous run. The deploy proceeded anyway because the file being ADDED
# was new, which is precisely the kind of half-success that should not be
# possible. Make the tree writable first and let cp fail loudly if it still cannot.
chmod -R u+w "$WORK/pkg" 2>/dev/null || true
mkdir -p "$WORK/pkg/sql"
cp -f "$WORK/new/dashboard-api/sql/"*.sql "$WORK/pkg/sql/" || die "could not stage sql/ into the package"
echo "   sql/ staged:"; ls -1 "$WORK/pkg/sql/" | grep -E '^5[0-9]' | sed 's/^/      /'

( cd "$WORK/pkg" && zip -qr "$WORK/d.zip" . ) || die "cannot rezip"
aws lambda update-function-code --function-name $FN --region $REGION --zip-file "fileb://$WORK/d.zip" --query LastModified --output text || die "deploy failed"
aws lambda wait function-updated --function-name $FN --region $REGION
echo "   package updated (code unchanged; sql/53 added)"

for M in dry_run apply; do
  P='{"action":"apply_sql","file":"53_ps1_provenance_repair.sql"'
  [ "$M" = dry_run ] && P="$P,\"dry_run\":true"
  aws lambda invoke --function-name $FN --region $REGION --cli-binary-format raw-in-base64-out \
    --payload "$P}" "$WORK/$M.json" >/dev/null 2>&1
  python3 -c "
import json;d=json.load(open('$WORK/$M.json'))
b=json.loads(d.get('body','{}')) if isinstance(d.get('body'),str) else d
a=b.get('apply_sql',b)
print('   $M: statements=%s applied=%s tolerated=%s failed=%s'%(a.get('statements'),a.get('applied'),a.get('tolerated'),a.get('failed')))
for e in (a.get('errors') or []): print('      ERROR:',e[:220])
"
done

show "AFTER"
cat <<'NOTE'

   EXPECTED on all three fleets
     target   = will_hardware_oos_3d
     run_id   = ps1_sklearn_20260726
     serving  = ps1_sklearn_20260726     kind = train
     matches  = True

   If TVM shows serving=ps1_20260810 the run_kind filter is not live -- but it
   already is, confirmed by kind=train on the 06:12 deploy.

   Then, to see what remains genuinely absent:
     curl -s "$API/ps1/table-status?city=CHI"   (and query v_ps1_provenance_gaps)
   VALIDATOR.recall_floor stays NULL by decision -- no floor is documented.
NOTE
echo "   ROLLBACK: aws lambda update-function-code --function-name $FN --region $REGION --zip-file fileb://$BK/$FN.zip"
