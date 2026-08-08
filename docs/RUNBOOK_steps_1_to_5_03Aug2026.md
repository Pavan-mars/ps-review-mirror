# Steps 1–5 — command sheet

03-Aug-2026. Run these in order. Each step has a check; do not move on until it
passes.

**One change to your plan, and it saves you a deploy.** Two of the Device 360
fixes (step 5) live in `handler.py`, so I put them in *before* you deploy in
step 1. Deploying step 1 now ships them. If you had deployed first and then
asked for Device 360, you would have deployed the API twice.

---

## Pre-flight (already done — no action)

I checked the three PS3 target tables against the deployed DDL so the loader
cannot refuse on you mid-run:

```
table                    matched   required  coverage  verdict
ps3_model_runs           11        15        73%       PASS
ps3_head_summary         22        25        88%       PASS
ps3_device_predictions   8         11        73%       PASS
```

Every `NOT NULL` column is filled, no identity column is missing, and no
column is written that the table does not have. The loader's floor is 50%.
Columns left empty are all nullable and all genuinely absent from this head
(`test_auc_macro_ovr`, `test_pr_auc_macro`, `test_log_loss`, `git_sha`,
`endpoint_name`, `serving_image`, `sm_package_arn`, `pct_critical_pred`,
`dominant_pred_severity`, `avg_component_age_days`).

---

## Step 1 — deploy the patched `handler.py`

Now carries **five** changes, not three:

1. `/ps5/device-rul` and `/ps5/serial-rul` honour `?limit` (default 3,000,
   ceiling 12,000)
2. `SELECT DISTINCT` on `/ps5/serial-rul` — collapses the validator roster
   fan-out
3. new `/ps5/summary` and `/ps5/component-summary` — the SQL denominators
4. **NEW** `/ps1/device-360` PS5 block now reads `v_ps5_device_rul` and
   `v_ps5_serial_rul` instead of `ps5_reliability_estimates`
5. **NEW** `/ps1/device-360` PS3 root-cause block deduplicated

```bash
# AWS CloudShell, us-east-1
rm -f ~/handler.py            # the upload fails silently if the file exists
# upload api/lambda/cubic-mars-dashboard-api/handler.py
cd ~/dash 2>/dev/null || mkdir -p ~/dash && cd ~/dash
cp ~/handler.py . && bash deploy.sh
```

**Check** — `_cb` so a warm container cannot answer for a cold one:

```bash
B=https://a9yuqt9j9b.execute-api.us-east-1.amazonaws.com
curl -s "$B/ps5/summary?city=CHI&_cb=$RANDOM" | python3 -m json.tool
curl -s "$B/ps1/device-360?city=CHI&device_id=BMV02633&_cb=$RANDOM" \
  | python3 -c "import json,sys; d=json.load(sys.stdin)['ps5']; print(json.dumps(d,indent=2)[:700])"
```

Expect `/ps5/summary` to return three rows, and BMV02633's `ps5` block to
change from

```json
{"level": "category", "found": false, "note": "No device-level RUL row for this device"}
```

to `level: "device"`, `risk_band: "CRITICAL"`, `rul_standard_days: 0.9`,
`rul_rank_in_type: 1`, `n_devices_in_type: 3235`, plus a `components` array.
That device is the single most urgent validator in the fleet and its own 360
page has been saying "no record".

Then rebuild the front end — `PS5Overview.jsx` and the Device 360 changes ship
together:

```powershell
cd "D:\work data\NAM_development\AWS\development_project\sathish_repo\dashboard"
npm run build
```

---

## Step 2 — deploy `cubic-mars-ps3-rc-loader`

```bash
# CloudShell — upload handler.py, deploy.sh, requirements.txt from
# api/lambda/cubic-mars-ps3-rc-loader/
cd ~ && rm -rf ps3rc && mkdir ps3rc && cd ps3rc
# (upload the three files here)
bash deploy.sh
```

**Check** — the dry run is expected to **refuse** at this point:

```bash
AWS_MAX_ATTEMPTS=1 aws lambda invoke --function-name cubic-mars-ps3-rc-loader \
  --cli-read-timeout 900 --cli-binary-format raw-in-base64-out \
  --payload '{"dry_run":true}' /tmp/ps3rc.json >/dev/null
python3 -m json.tool /tmp/ps3rc.json | head -30
```

You should see `"status": "refused"` and *"no run under
chicago/ps3/rootcause_outputs carries all 3 artifacts"*. **That is the correct
answer** — nothing has been published yet. A `committed` here would mean the
loader found something it should not have.

---

## Step 3 — run the v2 notebook end to end

Upload `PS3_03_RootCause_Models_v2.ipynb` to the PS3 Studio space, alongside
the existing `PS3_spine_outputs/` directory. Run cells 1 → 5.

- Cell 1 writes `ps3_rc_features.py` next to the notebook
- Cell 2 retrains (metrics may move in the third decimal vs v1 — the
  `prior_share_*` columns are now in sorted order rather than order of
  appearance)
- Cell 3 writes the SQL fallback, unchanged
- Cell 4 publishes the CSVs + manifest, and **prints the exact invoke for its
  own run_id**
- Cell 5 packages the models

**The SageMaker execution role needs `s3:PutObject` on
`cubic-mars-pm-s3-datalake-dev-artifacts-170202974600`.** If cell 4 fails with
AccessDenied, that is why — not a code fault.

**Check** — re-run the step 2 dry run. It should now find the run and report
`dry_run_ok` with S3 vs Aurora row counts. Then load for real with the payload
cell 4 printed.

---

## Step 4 — prove the daily loop

Upload `ps3_rc_daily_score.py` into the same directory (cell 1 already wrote
`ps3_rc_features.py`, which it imports).

```bash
python ps3_rc_daily_score.py --model-run latest --score-date latest --dry-run
python ps3_rc_daily_score.py --model-run latest --score-date latest --invoke-loader
```

Do the `--dry-run` first — it scores and reports without writing anything, so
a contract mismatch surfaces before it touches S3.

**`--invoke-loader` needs `lambda:InvokeFunction` on the SageMaker role.** If
it is not granted, drop the flag and invoke the loader by hand with the
`run_id` the script printed.

**One thing to know about run_ids.** Training publishes `ps3_oos_<date>`,
scoring publishes `ps3_score_<date>`. The loader picks the newest complete run
by lexical sort, and `ps3_score_*` sorts above `ps3_oos_*` — so an unpinned
`{}` invoke prefers the scoring run. That is what you want day to day, but it
means a bare `{}` after step 4 will not reload the training run. Pin the
`run_id` when you want a specific one.

---

## Step 5 — Device 360

Two of the three pieces are **already in** the handler you deploy in step 1.
Here is what was actually wrong, since "populate Device 360" turned out to
mean three different things:

### 5a — the PS5 block was reading the wrong table  ✅ done

It queried `ps5_reliability_estimates`, the first-generation table. The
survival run publishes to `v_ps5_device_rul` / `v_ps5_serial_rul`. The live
view is now tried first and the old table kept as a fallback, so a device that
exists only in the legacy estimates still resolves.

The front end was the other half of that problem: the PS5 panel rendered only
the *category* fields (concordance, registry status, blockers), so even after
the backend fix it would have shown nothing new. `Device360.jsx` now renders
act-now, risk band, remaining life, median survival, overdue, rank within its
own fleet, healthy age, days since last OOS, prior OOS count and OOS in the
last 30 days — plus a new **"PS5 components on this device"** table.

Rank is printed as *"1 of 3,235"* rather than as a bare rank, because the three
survival models are fitted separately and a bare rank invites a cross-fleet
comparison that is not valid.

### 5b — the root-cause block repeated rows  ✅ done

`ps3_v2_rootcause_360` returned the same `(component, serial)` several times —
on TVM08212 the same `BHU / fc6850` row came back repeatedly, which reads on
screen as several separate findings about one part. Now `SELECT DISTINCT` plus
a Python dedupe on `(component_label, serial_number)`, with the LIMIT raised
to 20 so deduping does not shrink the list below 10.

### 5c — the PS3 block still describes a limitation you have fixed  ⏳ after step 4

For any validator, the `ps3` block currently returns:

> *"PS3 is sourced from silver.incident_root_cause (ServiceNow availability
> events). VALIDATOR/BMV failures are NOT recorded as availability events …
> the PS3 availability-event feed has 0 rows for this device type by
> construction."*

That was true of the 26-Jul run. Your new spine has **497,327 labelled
validator device-days at 96.8% coverage**. So the 360 page is telling the
client about a gap you closed. I have not changed it yet because it should
point at the new run's rows, which do not exist in Aurora until step 4
finishes. Say the word once the loader has committed and I will wire it and
replace that note.

---

## Also worth knowing

- Backups of every edited file sit beside them: `*.bak_ps5wire`, `*.bak_d360`,
  `*.bak_d360ui`.
- I left a scratch file at `_to_delete/_d360_check_v2.jsx` in the repo root —
  I can move files but not delete them on your machine. Safe to remove.
- The v1 tab `components/tabs/PS3RootCauseTab.jsx` still calls
  `/ps3/device-predictions`, which serves the superseded 26-Jul run: 300 rows,
  all TVM, `dominant_pred_component = "None"` on every one, while advertising
  `rootcause_shippable: true` and `rootcause_f1_macro: 0.7136`. Step 4 replaces
  the data behind it. Until then, do not open that tab in front of the client.
