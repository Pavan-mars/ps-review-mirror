# PS2 Closure Verification Set - 19-Aug-2026

Closes out PS2 against the target architecture (audit: docs/PS2_AUDIT_16Aug2026.md).
Batches A, B and D run in the REGULAR CloudShell (read-only). Batch C runs psql in
the VPC CloudShell (read-only SELECTs; fetch the secret in the regular tab and
cross-paste the exports, per house procedure). Run one batch at a time, paste the
output back, and sql/56 + the retire/keep decisions get finalized from it.

Already done 19-Aug (for context): notebook set reduced to the production chain
(780b44b), export-prefix landmine closed at source (5bab3b7), draft Databricks
job medallion_ps2_daily added PAUSED (this commit).

Still open, in order: [1] this set's Batch A -> sql/56 for the five audit tables;
[2] Batch B -> retire-or-keep decision on cubic-mars-ps2-rds-push; [3] Batch C ->
PS2-vs-PS1 scoring comparison verdict; [4] SageMaker Studio re-sync
(sagemaker_ps2_cleanup_19Aug2026.sh / the PS4 script - either brings the clone
current); [5] team ratifies + first supervised Databricks run after incremental
ingestion; [6] Batch D after that run = final end-to-end proof.

## Batch A - audit-family parquet schemas + primary keys (feeds sql/56)

The five ps2_v25_*_audit exports (run-quality + PS1-parity evidence, ~20 rows/run)
are written every run and have no Aurora tables, so nothing can query how the
labels were validated. Per the sql/44 rule - EVERY column name and type is READ,
not inferred - this batch dumps the live parquet schema + manifest primary_keys.

```bash
export AWS_PAGER=""
B=cubic-mars-pm-s3-datalake-dev-artifacts-170202974600
pip install -q pyarrow 2>/dev/null
for fam in ps2_v25_category_profile_audit ps2_v25_failure_definition_alignment_audit \
           ps2_v25_failure_label_summary_audit ps2_v25_ps1_label_parity_audit \
           ps2_v25_ps1_model_performance_audit; do
  echo "===== $fam ====="
  key=$(aws s3api list-objects-v2 --bucket $B --prefix chicago/ps2_outputs/$fam/ \
        --query "sort_by(Contents,&LastModified)[-1].Key" --output text)
  base=${key%/*}
  echo "newest run folder: $base"
  aws s3 cp "s3://$B/$base/part-0.parquet" "/tmp/$fam.parquet" >/dev/null
  aws s3 cp "s3://$B/$base/manifest.json" "/tmp/$fam.manifest.json" >/dev/null 2>&1 || echo "(no manifest.json in this folder)"
  python3 - <<EOF
import json, os
import pyarrow.parquet as pq
s = pq.read_schema("/tmp/$fam.parquet")
print("columns:")
for f in s:
    print(f"  {f.name}  {f.type}")
m = "/tmp/$fam.manifest.json"
if os.path.exists(m):
    j = json.load(open(m))
    print("primary_keys:", j.get("primary_keys"))
    print("row_count:", j.get("row_count"))
EOF
done
```

Cross-check while reviewing: the notebook write cell declares keys
category_profile_audit=[device_category_raw, device_category], all others=
[device_category] (append_audit mode, so expect computed_date/run identifiers in
the columns too). sql/56 will follow sql/44 style: purely additive, natural PKs,
no surrogate id. Apply will be MANUAL psql in the VPC CloudShell (sql/49+ are
NOT in migrate(); never ship via deploy.sh). After apply, the scheduled loader
picks the five up automatically (it discovers targets from information_schema;
dry-run no_target should drop 8 -> 3).

## Batch B - cubic-mars-ps2-rds-push: investigate before retiring

25-Jul function, 512 MB, UN-CATALOGED, and its source is NOT in the repo (only
in the deployed function + the 21-Jul delivery zip). It was the S3-triggered
auto-ALTER loader from the serial-grain delivery, watching the BARE ps2_outputs
prefix - which nothing writes anymore after the 19-Aug repoint.

```bash
export AWS_PAGER=""
echo "== config =="
aws lambda get-function-configuration --function-name cubic-mars-ps2-rds-push \
  --query "{Modified:LastModified,Runtime:Runtime,Mem:MemorySize,Env:Environment.Variables,Role:Role}" --output json
echo "== resource policy (does S3 hold invoke permission?) =="
aws lambda get-policy --function-name cubic-mars-ps2-rds-push --output text 2>&1 | head -c 900; echo
echo "== S3 notification configs mentioning ps2, both buckets =="
for BK in cubic-mars-pm-s3-datalake-dev-gold-170202974600 cubic-mars-pm-s3-datalake-dev-artifacts-170202974600; do
  echo "--- $BK"
  aws s3api get-bucket-notification-configuration --bucket $BK --output json | grep -i -B2 -A8 ps2 || echo "(no ps2 notifications)"
done
echo "== last log event (has it EVER fired since 25-Jul?) =="
aws logs describe-log-streams --log-group-name /aws/lambda/cubic-mars-ps2-rds-push \
  --order-by LastEventTime --descending --max-items 1 \
  --query "logStreams[0].lastEventTimestamp" --output text 2>&1
echo "== source download URL (archive the zip before any retire) =="
aws lambda get-function --function-name cubic-mars-ps2-rds-push --query Code.Location --output text
```

Decision rule from the output: if its trigger watches the bare prefix and logs
are stale, the retire path is: download + archive the source zip, remove the S3
notification (REPLACE-not-update - send the complete document, back up first),
then delete the function. Exact commands will be written against the pasted
output; nothing destructive is in this batch.

## Batch C - PS2's independent PS1 scoring vs PS1's own scorecard (VPC psql)

Nobody has compared these; disagreement is signal, not noise. All three tables
are tiny (3-4 rows) - SELECT * is deliberate.

```sql
SELECT * FROM ps2_v25_ps1_model_performance ORDER BY 1;
SELECT * FROM ps2_v25_ps1_label_parity ORDER BY 1;
SELECT * FROM ps1_model_performance ORDER BY 1;
```

If a table name errors, run \dt ps2_v25_* and \d <table> and paste that instead;
do not improvise further.

## Batch D - end-to-end E1 proof (run AFTER the first PRODUCTION notebook run)

```bash
export AWS_PAGER=""
B=cubic-mars-pm-s3-datalake-dev-artifacts-170202974600
echo "== new prefix must grow =="
aws s3 ls "s3://$B/chicago/ps2_outputs/_runs/" --recursive | tail -3
echo "== bare prefix must NOT grow =="
aws s3 ls "s3://$B/ps2_outputs/_runs/" --recursive | tail -3
echo "== loader read-only check (empty payload would be a REAL load - never send {}) =="
aws lambda invoke --function-name cubic-mars-ps2-rds-loader \
  --cli-binary-format raw-in-base64-out --payload '{"dry_run": true}' /tmp/ps2dry.json >/dev/null
python3 -m json.tool /tmp/ps2dry.json | head -40
echo "== dashboard vintage =="
curl -s "https://a9yuqt9j9b.execute-api.us-east-1.amazonaws.com/ps2/status?city=CHI"; echo
```

Expected: new computed_date objects only under chicago/ps2_outputs; dry-run
no_target = 3 (post sql/56); /ps2/status computed_date advances after the next
07:10 UTC load.