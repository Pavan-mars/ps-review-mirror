#!/usr/bin/env bash
# =====================================================================
# CUBIC MARS Chicago — END-TO-END deploy + smoke test
#   Notebook -> S3 -> Lambda -> RDS -> Lambda(API) -> Dashboard
# Run from the repo root in AWS CloudShell (us-east-1). Idempotent.
# =====================================================================
set -uo pipefail
REGION=us-east-1
API_FN=cubic-mars-dashboard-api
PS1_FN=cubic-mars-ps1-rds-push
API=${API:-https://a9yuqt9j9b.execute-api.us-east-1.amazonaws.com}
CITY=${CITY:-CHI}
step(){ echo; echo "================ $* ================"; }

step "1/6  dashboard-api  (routes + sql/01..17)"
( cd api/lambda/cubic-mars-dashboard-api && bash deploy.sh ) || { echo "!! api deploy failed"; exit 1; }

step "2/6  apply migrations (action=migrate)"
aws lambda invoke --function-name $API_FN --region $REGION \
  --cli-binary-format raw-in-base64-out --payload '{"action":"migrate"}' /tmp/mig.json >/dev/null
python3 - <<'PY'
import json
m = json.load(open("/tmp/mig.json"))
r = json.loads(m["body"])["migrate"] if "body" in m else m.get("migrate", {})
bad = 0
for f, v in r.items():
    if isinstance(v, dict) and v.get("failed"):
        print(f"  FAILED {v['failed']:>3}  {f}"); [print("      ", e) for e in v.get("errors", [])[:3]]; bad += 1
    elif isinstance(v, dict):
        print(f"  ok     {v.get('applied','-'):>3}  {f}")
print("  >> migrations clean" if not bad else f"  >> {bad} file(s) with failures - review above")
PY

step "3/6  PS1 loader Lambda + EventBridge + S3 trigger"
( cd api/lambda/cubic-mars-ps1-rds-push && bash deploy.sh ) || { echo "!! ps1 deploy failed"; exit 1; }

step "4/6  load PS1 from gold (dry run first, then real)"
aws lambda invoke --function-name $PS1_FN --region $REGION --cli-binary-format raw-in-base64-out \
  --payload '{"dry_run":true}' /tmp/ps1dry.json >/dev/null && head -c 1200 /tmp/ps1dry.json; echo
read -r -p "  proceed with the real load? [y/N] " go
if [ "${go:-N}" = "y" ]; then
  aws lambda invoke --function-name $PS1_FN --region $REGION --cli-binary-format raw-in-base64-out \
    --payload '{}' /tmp/ps1.json >/dev/null && head -c 1200 /tmp/ps1.json; echo
fi

step "5/6  load PS3 from the run directory (host-side, needs the artifacts locally)"
if [ -n "${PS3_RUN_DIR:-}" ] && [ -n "${PS3_DSN:-}" ]; then
  python3 tooling/ps3_backfill_from_run.py --run-dir "$PS3_RUN_DIR" --city $CITY \
    --run-id ps3_20260719 --as-of 2026-07-19 --dsn "$PS3_DSN"
else
  echo "  skipped - set PS3_RUN_DIR and PS3_DSN to load the PS3 run"
fi

step "6/6  SMOKE TEST - every route the dashboard calls"
for p in \
  "/ps1/summary" "/ps1/leaderboard" "/ps1/features" "/ps1/predictions" \
  "/ps1/model-performance" "/ps1/risk-trend" "/ps1/risk-bands" "/ps1/station-summary" \
  "/ps3/severity/summary" "/ps3/rootcause/summary" "/ps3/rootcause/drivers" \
  "/ps3/incident-predictions" "/ps3/device-predictions" "/ps3/serial-predictions"
do
  code=$(curl -s -o /tmp/r.json -w '%{http_code}' "$API$p?city=$CITY")
  n=$(python3 -c "import json;d=json.load(open('/tmp/r.json'));print(len(d) if isinstance(d,(list,dict)) else 1)" 2>/dev/null || echo "?")
  printf "  %-3s %-32s rows/keys=%s\n" "$code" "$p" "$n"
done
echo
echo "GATE severity must be masked - expect severity_shippable=false and a NULL pct_critical_pred:"
curl -s "$API/ps3/device-predictions?city=$CITY&device_category=GATE" | head -c 400; echo
echo
echo "DONE."
