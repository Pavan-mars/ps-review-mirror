# RUNBOOK — apply the Aurora migrations from CloudShell

**What this does:** applies `sql/01 … 16` to the dev Aurora PostgreSQL cluster and
verifies the result, without needing CloudShell to reach the database directly.

**Why it works this way.** `cubic-mars-rds-aurora-dev` is VPC-private, and plain
CloudShell has no route into that VPC. But `cubic-mars-dashboard-api` is *already*
attached to the RDS VPC and already reads the DB credentials from Secrets Manager.
`lambda_handler()` short-circuits on a specific payload:

```python
if isinstance(event, dict) and event.get("action") == "migrate":
    return migrate(event)
```

So CloudShell only needs `lambda:InvokeFunction` and `lambda:UpdateFunctionCode`.
No VPC-enabled CloudShell environment, no bastion, no public DB endpoint.

`migrate()` reads the `.sql` files **from the deployment package** —
`os.path.join(os.path.dirname(__file__), fn)` — so the zip must contain `sql/`.
Deploying `handler.py` alone silently skips every migration with
`{"skipped": "file not present"}`.

Region is `us-east-1` throughout. Sign in with SSO first.

---

## 0. Pre-flight

```bash
FN=cubic-mars-dashboard-api
REGION=us-east-1

aws lambda get-function-configuration --function-name "$FN" --region "$REGION" \
  --query '{Runtime:Runtime,Timeout:Timeout,Memory:MemorySize,VpcSubnets:VpcConfig.SubnetIds,SecurityGroups:VpcConfig.SecurityGroupIds,Env:Environment.Variables}' \
  --output json
```

Confirm before going further:

- `VpcSubnets` is **non-empty** — an empty list means the function is not in the RDS
  VPC and cannot reach Aurora.
- `Env` contains `SECRET_ARN`, `RDS_HOST`, and optionally `RDS_PORT` / `DB_NAME`.
  Those are the only four the handler reads.
- `Timeout` is **at least 120s**. `01_schema_core.sql` alone is ~95 statements; the
  full 01→16 chain is ~230. The default 3s timeout will kill it mid-migration.

```bash
# raise the timeout if needed (idempotent)
aws lambda update-function-configuration --function-name "$FN" --region "$REGION" \
  --timeout 300 --memory-size 512
aws lambda wait function-updated --function-name "$FN" --region "$REGION"
```

---

## 1. Package and deploy handler + SQL

Run from the repo root (clone it in CloudShell, or upload the folder).

```bash
cd api/lambda/cubic-mars-dashboard-api

rm -rf build && mkdir -p build
cp handler.py build/
cp -r sql build/

# pg8000 is a pure-python driver, so no manylinux wheel juggling is needed.
python3 -m pip install --quiet --target build pg8000

cd build && zip -qr ../function.zip . && cd ..
unzip -l function.zip | grep -cE 'sql/[0-9]+_.*\.sql'   # expect 14
```

That count **must be 14**. If it is 0 you are about to deploy a handler with no
migrations and `migrate` will report every file as skipped.

```bash
aws lambda update-function-code --function-name "$FN" --region "$REGION" \
  --zip-file fileb://function.zip --publish
aws lambda wait function-updated --function-name "$FN" --region "$REGION"
```

---

## 2. Run the migration

```bash
aws lambda invoke --function-name "$FN" --region "$REGION" \
  --cli-binary-format raw-in-base64-out \
  --payload '{"action":"migrate"}' \
  --cli-read-timeout 300 \
  migrate-out.json >/dev/null

python3 - <<'PY'
import json
body = json.loads(json.load(open("migrate-out.json"))["body"])["migrate"]
bad = 0
for fn, r in body.items():
    if "skipped" in r:
        print(f"  {fn:42} SKIPPED ({r['skipped']})")
        continue
    flag = "  <== FAILURES" if r.get("failed") else ""
    print(f"  {fn:42} applied={r.get('applied'):>4} tolerated={r.get('tolerated'):>3} "
          f"failed={r.get('failed')}{flag}")
    bad += r.get("failed", 0)
    for e in r.get("errors", []):
        print(f"       ! {e}")
print("\nTOTAL HARD FAILURES:", bad)
PY
```

### Reading the result

- **`tolerated`** is expected and healthy — it counts `already exists` /
  `does not exist, skipping`, which is what makes re-running safe.
- **`applied=0, tolerated=high`** on a second run is the correct signature of an
  idempotent migration.
- `sql/10_*` and `sql/12_*` will report **`SKIPPED (file not present)`**. Those
  numbers were never used; that is expected, not an error.
- **`failed > 0` is the only thing that matters.** Baseline for a clean first run,
  measured locally against real PostgreSQL 16:

  | file | applied | tolerated | failed |
  |---|---|---|---|
  | 01_schema_core | 93 | 2 | 0 |
  | 15_phase2a_ps3_two_head | 16 | 0 | 0 |
  | 16_phase2b_ps1_batch_lineage | 21 | 0 | 0 |
  | **all 14 files** | **229** | **2** | **0** |

If `01_schema_core` fails on `uuid_generate_v4()`, the `uuid-ossp` extension was not
created — check the Lambda's DB user has rights to `CREATE EXTENSION`.

---

## 3. Verify the new PS1/PS3 objects exist

```bash
aws lambda invoke --function-name "$FN" --region "$REGION" \
  --cli-binary-format raw-in-base64-out \
  --payload '{"rawPath":"/ps3/summary","requestContext":{"http":{"method":"GET"}},"queryStringParameters":{"city":"CHI"}}' \
  ps3-out.json >/dev/null && cat ps3-out.json
```

A `200` with an empty/near-empty body is the **correct** state at this point — the
tables exist but no run has been loaded yet. A `500` naming a missing relation means
the migration did not actually apply.

---

## 4. Load the PS3 run into Aurora

The loader recomputes `pred_severity_collapsed` and `pct_critical_pred` rather than
trusting the run's own columns (the 19-Jul run has them 100% MAJOR / 0.0 fleet-wide).
It needs network access to the DB, so run it from somewhere inside the VPC, or
temporarily via an SSM port-forward.

```bash
export PS3_DSN='postgresql://USER:PASSWORD@HOST:5432/postgres'

python3 tooling/ps3_backfill_from_run.py \
  --run-dir /path/to/ML_outputs/Level2/PS3 \
  --city CHI --as-of 2026-07-19 --dry-run      # inspect first

python3 tooling/ps3_backfill_from_run.py \
  --run-dir /path/to/ML_outputs/Level2/PS3 \
  --city CHI --as-of 2026-07-19 --dsn "$PS3_DSN"
```

Expected, from the verified local run against the real artifacts:

```
ps3_incident_predictions   34612
ps3_device_predictions       927
ps3_serial_predictions       927
ps3_head_summary               6
ps3_head_leaderboard          26
ps3_leakage_scan              30
```

with `12897/34612 incident rows corrected`. Re-running is safe — it deletes by
`(city_id, run_id)` before inserting.

`ps3_head_feature_importance` will be **0** — that run exported no SHAP CSVs.

---

## 5. Rollback

Migrations 15 and 16 are strictly additive (`CREATE TABLE IF NOT EXISTS`,
`ALTER TABLE ... ADD COLUMN IF NOT EXISTS`), so there is nothing to undo for a
failed run — fix and re-invoke. To remove the PS3 data only:

```sql
DELETE FROM ps3_incident_predictions WHERE city_id='CHI' AND run_id='ps3_20260719';
-- repeat for ps3_device_predictions, ps3_serial_predictions, ps3_head_summary,
-- ps3_head_class_metrics, ps3_head_leaderboard, ps3_leakage_scan, ps3_model_runs
```

To roll the Lambda back to the previous published version:

```bash
aws lambda list-versions-by-function --function-name "$FN" --region "$REGION" \
  --query 'Versions[-3:].[Version,LastModified]' --output table
aws lambda update-alias --function-name "$FN" --name live \
  --function-version <PREVIOUS> --region "$REGION"
```

---

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| every file `SKIPPED (file not present)` | `sql/` not in the zip | re-package; the `unzip -l` count must be 14 |
| `Task timed out after 3.00 seconds` | default timeout | set `--timeout 300` (step 0) |
| `could not connect to server` / timeout at 15s | Lambda not in the RDS VPC, or SG blocks 5432 | check `VpcConfig.SubnetIds`; allow the Lambda SG inbound on the DB SG |
| `SECRET_ARN` KeyError | env var missing | set it on the function configuration |
| `function uuid_generate_v4() does not exist` | `uuid-ossp` not created | grant `CREATE EXTENSION` to the DB user, re-invoke |
| `/ps3/summary` 500 `relation does not exist` | migration did not apply | re-check step 2 output for hard failures |
