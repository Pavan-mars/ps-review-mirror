# Chicago — data-correctness fixes, CloudShell runbook

08-Aug-2026. Repo side is done and committed as `78c7118`. Everything below
runs in **CloudShell**, in this order. The order is not arbitrary — step 1
must land before step 3 or step 3 silently reverts.

Account `170202974600`, region `us-east-1`.

---

## STEP 0 — Check three things before changing anything

These are read-only. Do all three, then decide.

### 0.1 Is PS3 v25 reading production or replay?

`handler.py` defaults `PS3_PREFIX` to `ps3_outputs`. `deploy.sh` sets it to
`ps3_replay_outputs`. **Whichever ran last is what the dashboard is showing.**

```bash
aws lambda get-function-configuration \
  --function-name cubic-mars-ps3-v25-loader \
  --query 'Environment.Variables.PS3_PREFIX' --output text
```

- `ps3_outputs` → production, as intended.
- `ps3_replay_outputs` → **the PS3 v2.5 panels are being fed from a replay
  run.** Stop and tell me; that is a client-visible correctness problem and
  changes what we do next.
- `None` → the variable is unset and the handler default (`ps3_outputs`)
  applies. Fine.

### 0.2 Do the EventBridge rules actually exist and are they enabled?

Seven deploy scripts create a rule. That is not proof one is live.

```bash
for r in cubic-mars-dim-daily-refresh cubic-mars-ps1-daily-push \
         cubic-mars-ps1-xw-daily-load cubic-mars-ps2-daily-load \
         cubic-mars-ps4-daily-load cubic-mars-ps4-v3-weekly \
         cubic-mars-ps5-daily-load; do
  printf "%-34s " "$r"
  aws events describe-rule --name "$r" \
    --query '[State,ScheduleExpression]' --output text 2>/dev/null || echo "NOT FOUND"
done
```

Anything reporting `NOT FOUND` or `DISABLED` means that feed is frozen and
the dashboard will show a stale `as_of_date` indefinitely.

**No PS3 loader has a rule at all** — `ps3-rc-loader`, `ps3-v2-loader` and
`ps3-v25-loader` are manual-only by construction. That is the real
EventBridge gap.

### 0.3 What is actually under the two unprefixed roots?

```bash
B=cubic-mars-pm-s3-datalake-dev-artifacts-170202974600
aws s3 ls "s3://$B/ps2_outputs/" | head
aws s3 ls "s3://$B/ps3_outputs/" | head
echo "--- object counts and size ---"
aws s3 ls "s3://$B/ps2_outputs/" --recursive --summarize | tail -3
aws s3 ls "s3://$B/ps3_outputs/" --recursive --summarize | tail -3
```

Record both numbers. Step 3 verifies against them.

---

## STEP 1 — Redeploy with the additive-environment fix. Do this first.

Until this lands, any environment variable you set by hand is erased by the
next deploy. That includes the prefix override in step 3.

Each script now prints `preserved N pre-existing env var(s): …` when it keeps
something it does not own. **Read that line** — it tells you what was
previously being silently destroyed.

```bash
cd api/lambda
for d in cubic-mars-ps1-rds-push cubic-mars-ps2-rds-loader \
         cubic-mars-ps3-v25-loader cubic-mars-dashboard-api; do
  echo "=== $d"; ( cd "$d" && bash deploy.sh )
done
```

Start with those four. The other seven can follow once you are satisfied the
merge behaves.

**Verify the merge did not lose anything** — compare against what 0.1 showed:

```bash
aws lambda get-function-configuration --function-name cubic-mars-ps2-rds-loader \
  --query 'Environment.Variables' --output json
```

**Rollback:** the pre-change scripts are in
`_to_delete/bak/deploy_pre_merge_08aug/`. Copy one back and re-run.

---

## STEP 2 — PS1 loader, category-scoped delete

`cubic-mars-ps1-rds-push` now deletes only the categories present in the
payload instead of the whole `computed_date`.

```bash
cd api/lambda/cubic-mars-ps1-rds-push && bash deploy.sh
```

**Prove the fix on real data.** Score two fleets against the same date and
confirm both survive — this is the exact case that used to lose one:

```bash
# before
aws lambda invoke --function-name cubic-mars-dashboard-api \
  --cli-binary-format raw-in-base64-out \
  --payload '{"action":"sql","q":"SELECT device_category, COUNT(*) FROM ps1_failure_predictions WHERE city_id='"'"'CHI'"'"' GROUP BY 1 ORDER BY 1"}' \
  /tmp/before.json >/dev/null && cat /tmp/before.json
```

Run the second fleet's load, then re-run the same query. **Every category
that was there before must still be there.** Previously only the
last-loaded one survived.

The loader response now carries a `warn` list. Two new messages:

- `payload empty - nothing deleted, nothing inserted` — the read produced no
  rows. Previously this emptied the table and reported success. Investigate
  the source, do not ignore it.
- `device_category is NULL on at least one row …` — the delete could not be
  scoped and fell back to clearing the whole date. Fix the notebook to emit
  the category.

---

## STEP 3 — PS2 / PS3 city prefix: copy, verify, repoint

**Never move. Copy, verify, then repoint.** The old prefix stays as a
fallback until you are satisfied, and rollback is a one-line env change.

Only two roots need this. `ps3-rc-loader` (`chicago/ps3/rootcause_outputs`)
and `ps3-v2-loader` (`chicago/ps3_hardened_remediation/runs`) are already
city-prefixed and must not be touched.

### 3.1 Copy

```bash
B=cubic-mars-pm-s3-datalake-dev-artifacts-170202974600
aws s3 sync "s3://$B/ps2_outputs/" "s3://$B/chicago/ps2_outputs/"
aws s3 sync "s3://$B/ps3_outputs/" "s3://$B/chicago/ps3_outputs/"
```

### 3.2 Verify before repointing

Counts must match what 0.3 recorded:

```bash
aws s3 ls "s3://$B/chicago/ps2_outputs/" --recursive --summarize | tail -3
aws s3 ls "s3://$B/chicago/ps3_outputs/" --recursive --summarize | tail -3
```

Then spot-check that a copied object is byte-identical, not merely present:

```bash
K=$(aws s3 ls "s3://$B/ps2_outputs/" --recursive | awk 'NR==1{print $4}')
aws s3api head-object --bucket "$B" --key "$K"                  --query ETag --output text
aws s3api head-object --bucket "$B" --key "chicago/$K"          --query ETag --output text
```

Two identical ETags. **If the counts or the ETags disagree, stop here** —
nothing has been repointed yet, so there is nothing to undo.

### 3.3 Repoint the loaders

Edit two lines in the repo, then redeploy:

| File | Line | From | To |
|---|---|---|---|
| `api/lambda/cubic-mars-ps2-rds-loader/deploy.sh` | 40 | `PS2_PREFIX=ps2_outputs` | `PS2_PREFIX=chicago/ps2_outputs` |
| `api/lambda/cubic-mars-ps3-v25-loader/deploy.sh` | 28 | `PS3_PREFIX=${PS3_PREFIX:-ps3_replay_outputs}` | `PS3_PREFIX=${PS3_PREFIX:-chicago/ps3_outputs}` |

The second line also resolves the production-vs-replay conflict from 0.1 —
**do not make that change until you have told me what 0.1 returned.**

```bash
( cd api/lambda/cubic-mars-ps2-rds-loader  && bash deploy.sh )
( cd api/lambda/cubic-mars-ps3-v25-loader  && bash deploy.sh )
```

### 3.4 Repoint the notebooks

Set these in the SageMaker execution environment before the next run. Both
notebook guards were checked against the new values and still pass — PS3
validates the prefix *tail*, PS2 compares export against production prefix,
and both hold.

```
PS2_PRODUCTION_EXPORT_PREFIX = chicago/ps2_outputs
PS3_S3_OUTPUT_PREFIX         = s3://cubic-mars-pm-s3-datalake-dev-artifacts-170202974600/chicago/ps3_outputs
```

### 3.5 Prove the chain end to end

```bash
aws lambda invoke --function-name cubic-mars-ps2-rds-loader \
  --cli-binary-format raw-in-base64-out --payload '{"dry_run":true}' /tmp/ps2.json >/dev/null
python3 -m json.tool /tmp/ps2.json | head -20
```

The response echoes the prefix it swept. It must read
`s3://…/chicago/ps2_outputs` and must find the same tables as before.

**Rollback:** put the old value back in `deploy.sh` and redeploy. The
original data was never moved.

### 3.6 Only after a clean run

Leave the old prefixes in place for at least one full successful cycle. Do
not delete them in the same session you repoint.

---

## STEP 4 — PS3 scheduling

The three PS3 loaders have no EventBridge rule. Decide whether PS3 is meant
to be daily. If it is, the pattern from the PS2 script transfers directly:

```bash
FN=cubic-mars-ps3-v25-loader
RULE=cubic-mars-ps3-v25-daily-load
aws events put-rule --name $RULE --schedule-expression "cron(30 7 * * ? *)" \
  --description "Daily PS3 v2.5 outputs -> Aurora" --state ENABLED
aws lambda add-permission --function-name "$FN" --statement-id ${RULE}-invoke \
  --action lambda:InvokeFunction --principal events.amazonaws.com \
  --source-arn "$(aws events describe-rule --name $RULE --query Arn --output text)"
aws events put-targets --rule $RULE \
  --targets "Id=1,Arn=$(aws lambda get-function --function-name $FN --query Configuration.FunctionArn --output text)"
```

07:30 UTC sits after PS2 (07:10) and before PS5 (07:20) would contend — check
the spacing against what 0.2 reported before committing to a time.

**Do not schedule this until step 3 is verified.** A scheduled loader
pointing at a half-migrated prefix turns a manual problem into a nightly one.

---

## Fix one thing while you are in there

`api/lambda/cubic-mars-ps2-rds-loader/deploy.sh` line 177 describes its rule
as `"Daily PS4 run outputs -> Aurora"`. It is the PS2 rule. Cosmetic, but the
file was clearly cloned from the PS4 script and it is worth checking nothing
else came across with it.
