# CloudShell Verification Set — stamps the remaining [U] items across the PS1-PS5 audits

**16-Aug-2026.** Five SMALL batches (long pastes can crash the CloudShell tab —
run them one at a time). Batches A-D run in the REGULAR CloudShell (internet +
AWS APIs). Nothing here writes anything: every command is read-only.

After running: paste the outputs back (or upload the generated files to the chat),
and the five audit documents get updated with RDS schemas + row counts and every
[U] resolved.

---

## Batch A — RDS tables, schemas, row counts (audit item 5, all PS)

```bash
export AWS_PAGER=""
aws lambda invoke --function-name cubic-mars-dashboard-api \
  --cli-binary-format raw-in-base64-out \
  --payload '{"action":"catalog"}' catalog.json --region us-east-1
python3 - <<'EOF'
import json
d = json.load(open("catalog.json"))
# print a compact line per table: name, rows, n_columns (shape depends on the
# handler's catalog format; fall back to raw keys if unexpected)
def walk(x):
    if isinstance(x, list): return x
    for k in ("tables","catalog","result","body"): 
        if isinstance(x, dict) and k in x: return walk(x[k])
    return x
t = walk(d)
try:
    for row in t:
        name = row.get("table") or row.get("table_name") or row.get("name")
        rows = row.get("rows") or row.get("row_count") or row.get("n_rows")
        cols = row.get("columns") or row.get("n_columns") or row.get("column_count")
        ncols = len(cols) if isinstance(cols, list) else cols
        print(f"{name}\t{rows}\t{ncols}")
except Exception as e:
    print("UNEXPECTED SHAPE:", type(t), str(d)[:400])
EOF
```

Then UPLOAD `catalog.json` to the chat (it carries the full column lists needed
for the schema sections). If it is huge, `gzip catalog.json` and upload the .gz.

## Batch B — endpoints + ECR truth for PS1 & PS3 (audit item 6)

```bash
export AWS_PAGER=""
echo "== endpoints =="
aws sagemaker list-endpoints --query "Endpoints[].{N:EndpointName,S:EndpointStatus}" --output table
echo "== endpoint configs -> models -> images =="
for e in $(aws sagemaker list-endpoints --query "Endpoints[].EndpointName" --output text); do
  c=$(aws sagemaker describe-endpoint --endpoint-name "$e" --query EndpointConfigName --output text)
  m=$(aws sagemaker describe-endpoint-config --endpoint-config-name "$c" --query "ProductionVariants[0].ModelName" --output text)
  img=$(aws sagemaker describe-model --model-name "$m" --query "PrimaryContainer.Image" --output text)
  echo "$e -> $m -> $img"
done
echo "== ECR repos + newest 3 tags each =="
for repo in $(aws ecr describe-repositories --query "repositories[].repositoryName" --output text); do
  echo "--- $repo"
  aws ecr describe-images --repository-name "$repo" \
    --query "sort_by(imageDetails,&imagePushedAt)[-3:].{tags:imageTags,pushed:imagePushedAt}" --output json
done
```

Expected/at-issue: 4 endpoints InService (3x PS1 + 1x PS3); PS1 images should be
the AWS managed sklearn DLC (account 683313688378); PS3 expected to reference
cubic-pdm/mars-ps3 with the MUTABLE :latest tag (the defect to confirm);
cubic-pdm/mars-ps1 expected referenced by nothing.

## Batch C — EventBridge rules + did each loader actually RUN (audit item 7)

```bash
export AWS_PAGER=""
echo "== all cubic rules =="
aws events list-rules --query "Rules[?contains(Name,'cubic') || contains(Name,'mars') || contains(Name,'ps')].{N:Name,S:State,Sched:ScheduleExpression}" --output table
echo "== rule targets =="
for rname in $(aws events list-rules --query "Rules[?contains(Name,'cubic') || contains(Name,'mars')].Name" --output text); do
  echo "--- $rname"; aws events list-targets-by-rule --rule "$rname" --query "Targets[].Arn" --output text
done
```

```bash
export AWS_PAGER=""
echo "== last log event per Lambda (deployed != ran) =="
for fn in cubic-mars-dim-loader cubic-mars-ps1-xw-loader cubic-mars-ps1-rds-push \
          cubic-mars-ps2-rds-loader cubic-mars-ps3-rc-loader cubic-mars-ps3-v25-loader \
          cubic-mars-ps4-rds-loader cubic-mars-ps4-v3-loader cubic-mars-ps5-rds-loader \
          cubic-mars-dashboard-api; do
  ts=$(aws logs describe-log-streams --log-group-name "/aws/lambda/$fn" \
       --order-by LastEventTime --descending --max-items 1 \
       --query "logStreams[0].lastEventTimestamp" --output text 2>/dev/null)
  echo "$fn  lastEvent=$ts"
done
```

At-issue items to confirm: ps1-rds-push rule DISABLED (and its reserved
concurrency — next batch); all three PS3 loaders with NO rule; anything firing
that should not, or ENABLED rules whose Lambda shows no recent log events.

## Batch D — the specific [U] items

```bash
export AWS_PAGER=""
echo "== ps5_daily_scorer deployed? =="
aws lambda get-function-configuration --function-name cubic-mars-ps5-daily-scorer \
  --query "{Name:FunctionName,Modified:LastModified,Env:Environment.Variables}" --output json 2>&1 | head -30
echo "== ps1-rds-push reserved concurrency (expect 0 per the design; verify) =="
aws lambda get-function-concurrency --function-name cubic-mars-ps1-rds-push --output json
echo "== PS5 scorer prefix alignment: params/state locations =="
B=cubic-mars-pm-s3-datalake-dev-gold-170202974600
aws s3 ls s3://$B/chicago/ps5/params/ 2>&1 | head -5
aws s3 ls s3://$B/chicago/ps5/state/ 2>&1 | head -5
aws s3 ls s3://$B/chicago/ps5/notebook_outputs/gates/ | head -8
echo "== PS4: which variant produced current outputs (champion_pipeline from newest manifest) =="
aws s3 ls s3://$B/chicago/ps4/clustering/manifest/ --recursive | tail -3
# take the newest key printed above and:
# aws s3 cp s3://$B/<newest-manifest-key> - | python3 -m json.tool | head -20
```

## Batch E — API smokes (fast re-confirmation of the serving layer)

```bash
API=https://a9yuqt9j9b.execute-api.us-east-1.amazonaws.com
for p in "/ps1/summary" "/ps1/coverage" "/ps2/status" "/ps3/summary" "/ps3/collapse-health" "/ps4/v3-status" "/ps5/status"; do
  echo "== $p =="; curl -s "$API$p?city=CHI" | head -c 250; echo; done
```

---

**Reminder:** Aurora psql is NOT needed for this set — Batch A's `catalog` action
returns tables/columns/row counts through the Lambda. If any batch errors oddly,
paste the error as-is; do not improvise flags.
