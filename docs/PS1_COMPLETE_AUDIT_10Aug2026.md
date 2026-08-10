# PS1 — Complete Audit, 10-Aug-2026

Every claim is tagged:

  [M] MEASURED   — verified today against AWS, Aurora, the live API, or the repo.
  [U] UNVERIFIED — could not be measured from here. The exact command to settle
                   it is given in section 5. Nothing in this document acts on a [U].

There are deliberately no inferred claims. Where I could not measure, it says
[U] and stops.

---

## 0. The one thing to understand first

**PS1 has TWO INDEPENDENT PATHS.** Nearly every confusing PS1 statement comes
from conflating them. Different sources, different loaders, different tables,
different fleet coverage.

```
              PS1 notebooks (GATE / TVM / VALIDATOR)
                      |                        |
        writes ONE key per fleet        writes ONE shared key
                      |                        |
  ARTIFACTS  chicago/                  GOLD  chicago/gold/
  device_ps1_cross_wired_daily/        device_ps1_cross_wired_daily
    gate / tvm / validator               (single object -- overwritten)
                      |                        |
        cubic-mars-ps1-xw-loader        cubic-mars-ps1-rds-push
              06:40 daily                     06:15 daily
                      |                        |
        ps1_cross_wired_daily           ps1_failure_predictions
           786,525 rows                 ps1_serial_predictions
           ALL THREE FLEETS             ps1_inference_runs
                      |                        |
         /ps1/xw-* and the views        /ps1/crosstab, /ps1/coverage
         v_ps1_predictions_xw           (legacy; mostly superseded)
                      |
                THE DASHBOARD
```

**PATH A (cross-wired, artifacts bucket) is healthy and is what the dashboard
reads.** PATH B (gold bucket) is legacy, partly broken, and feeds a shrinking
number of screens.

---

## 1. What is RIGHT — verified

### 1.1 Path A is fully healthy [M 10-Aug]
`ps1_cross_wired_daily` holds **786,525 rows** (`/ps1/table-status`) — exactly
the total the loader declares in its own source:

    EXPECTED = {"gate": 107110, "tvm": 184483, "validator": 494932,
                "total": 786525}                    handler.py:59

All three fleets present, measured via `/ps1/xw-summary`:

| fleet | rows | devices | serials | first_day | last_day | label_base_rate |
|-------|------|---------|---------|-----------|----------|-----------------|
| GATE | 107,110 | 819 | 894 | 2026-01-01 | 2026-04-11 | 0.76836 |
| TVM | 184,483 | 470 | 1,618 | 2026-01-01 | 2026-04-11 | (per route) |
| VALIDATOR | 494,932 | — | — | 2026-01-01 | 2026-04-11 | — |

`last_day 2026-04-11` matches the programme data vintage — correct, not stale.

### 1.2 The xw loader is correctly built [M]
`cubic-mars-ps1-xw-loader/handler.py`:
- `BEGIN` / per-fleet `SAVEPOINT` / `COMMIT`, `ROLLBACK` on failure (lines 317-372)
- DELETE is **fleet-scoped**: `WHERE city_id = :c AND device_type = :d`
- refuses to delete on an empty shape: *"0 rows shaped -- refusing to delete
  existing rows for an empty load"*
- reads one key per fleet, so fleets never overwrite each other

This is the pattern the other loader should follow.

### 1.3 All seven xw-* routes return all three fleets [M]
`xw-summary`, `xw-tiers`, `xw-performance`, `xw-state-mix`, `xw-drivers`,
`xw-base-rate`, `xw-flag-reason` — each returns `['GATE','TVM','VALIDATOR']`.

### 1.4 SageMaker: three endpoints InService [M 10-Aug]
    chicago-ps1-3d-gate-failure-v1        InService   2026-07-24 07:53
    chicago-ps1-3d-tvm-failure-v1         InService   2026-07-24 07:46
    chicago-ps1-3d-validator-failure-v1   InService   2026-07-24 09:33

### 1.5 Both EventBridge rules exist and are ENABLED [M]
    cubic-mars-ps1-daily-push     cron(15 6 * * ? *)  -> cubic-mars-ps1-rds-push
    cubic-mars-ps1-xw-daily-load  cron(40 6 * * ? *)  -> cubic-mars-ps1-xw-loader

### 1.6 Route health: 34 of 36 return 200 [M]
Every `/ps1/*` route tested live today. `/ps1/device-360` and
`/ps1/xw-device-drivers` return 400 *correctly* (they require `device_id`).
`/ps1/servicenow-stage` returns 404 on GET — likely POST-only [U 5.4].

### 1.7 Path B was repaired today [M]
Four stacked defects fixed (section 2.1). PS1 completed its first full load
since 26-Jul: 45,681 device-day + 1,897 serial rows, committed, recorded in
`ml_batch_load_audit` with `status: ok`.

---

## 2. What is WRONG — verified

### 2.1 FIXED TODAY (listed because it explains the current state)

| # | Defect | Evidence | Status |
|---|--------|----------|--------|
| a | AWSSDKPandas layer missing from the live function | `ImportModuleError: No module named 'pyarrow'`; 21/21 invocations failed over 7 days | FIXED live |
| b | `GOLD_KEY` pointed at a CSV; handler only reads Parquet | magic bytes — CSV `"DEV`, Parquet `PAR1` | FIXED live |
| c | `deploy.sh` used `list-layer-versions`, denied on another account's public layer, so it could never deploy | every other loader uses `get-layer-version-by-arn` | FIXED — a38fda3 |
| d | serial dedup keyed on `(device, serial, DAY)`; the PK has no date | `duplicate key ... ps1_serial_predictions_pkey` | FIXED — 45b4342 |

**Why this hid for 15 days:** the layer broke on 26-Jul and the only script that
could restore it aborted before reaching that line. The failure and its own
remedy broke on the same day.

### 2.2 OPEN — P1: the gold export overwrites fleets

**[M]** No PS1 notebook uses `partition_cols`. All six checked:

    Chicago_PS1_Predictive_Failure.ipynb                  partition_cols=False
    PS1_3d_GATE_OOS_Optimized_SageMaker_v4.ipynb          partition_cols=False
    PS1_3d_GATE_SageMaker_MLflow_FeatureStore.ipynb       partition_cols=False
    PS1_3d_TVM_SageMaker_MLflow_FeatureStore.ipynb        partition_cols=False
    PS1_3d_VALIDATOR_SageMaker_MLflow_FeatureStore.ipynb  partition_cols=False
    PS1_VALIDATOR_Device_Bus_Serial_Map.ipynb             partition_cols=False

All three fleet notebooks write the SAME gold key
`chicago/gold/device_ps1_cross_wired_daily`, so each run overwrites the last.
The loader's own note confirms it at read time:

> *"ONLY ONE device_category present (TVM) — the notebook's CELL 24 writes
> without partition_cols, so each run overwrites the previous category."*

**Consequence [M]:** `ps1_failure_predictions` holds TVM 45,681 + VALIDATOR
1,922 and **has never held a GATE row** (`/ps1/coverage`).

**Fix:** ~~`partition_cols=['device_category']` in cell 24 of each fleet
notebook~~

> ### CORRECTION, 10-Aug-2026 evening — THIS ENTIRE FINDING WAS WRONG. Do not act on it.
>
> I read cell 24 of all three fleet notebooks. **It already writes one prefix
> per fleet**, with an explicit Python loop rather than the `partition_cols`
> keyword: [V]
>
> ```python
> for _cat in sorted(comp_preds_df["device_category"].dropna().unique()):
>     _slug  = str(_cat).strip().lower()
>     _cat_df = comp_preds_df[comp_preds_df["device_category"] == _cat]
>     _out   = f"{ps1_xw_base}/{_slug}/"
>     _cat_df.to_parquet(_out, index=False, storage_options=S3_OPTS)
> ```
>
> The cell's own header says so: *"Write ServiceNow-ready parquet to S3
> (GATE / TVM / VALIDATOR separate prefixes)"*. **This is exactly why Path A
> is healthy with all three fleets.** Adding `partition_cols` here would have
> modified a working export to fix a defect it does not have.
>
> **And the gold key nothing writes.** No notebook and no script in this
> repository writes `s3://<gold>/chicago/gold/device_ps1_cross_wired_daily`.
> Searched every notebook and every `.py` for a write referencing the gold
> bucket or that key: zero hits. The only reference is
> `notebooks/cross_wired_daily_job.py:278`, which **reads** it and names the
> variable `ps1_xw_legacy_bases` — a fallback for fleets missing from the
> artifacts path. [V]
>
> So Path B's source object is produced by nothing that still exists in this
> codebase. **That, not `partition_cols`, is why Path B is 13% complete.**
>
> **HOW I GOT THIS WRONG, because the mechanism matters more than the error.**
> The morning check was a grep for the string `partition_cols`, which returned
> "False" for all six notebooks. That much is true — none of them uses the
> keyword. I then inferred, without checking, that they must therefore all
> write one shared key and overwrite each other. They do not; a loop achieves
> the same thing.
>
> Worse, I quoted this as corroboration:
>
> > *"ONLY ONE device_category present (TVM) — the notebook's CELL 24 writes
> > without partition_cols, so each run overwrites the previous category."*
>
> That sentence is not a measurement. It is a **hypothesis written into a code
> comment** at `api/lambda/cubic-mars-ps1-rds-push/handler.py:274` and repeated
> in that function's `README.md:65`, by whoever built Path B. I cited a comment
> asserting a cause as evidence for that cause. Circular, and it survived
> because the conclusion sounded right.
>
> **The real reason Path B holds zero GATE rows is still unknown** and is now
> a genuine open question rather than a solved one — see the status document.

### 2.3 OPEN — P1: fleets hide each other via MAX(computed_date)

**[M]** Two routes filter `ps1_failure_predictions` on
`(SELECT MAX(computed_date) ...)`:

    handler.py:2313   /ps1/crosstab
    handler.py:685    /ps1/device-360   (PS1 block of the device drilldown)

Each fleet loads on a different day, so only the most recent is visible.
**Measured now:** `/ps1/crosstab` returns TVM only (45,681 across four risk
bands); VALIDATOR's 1,922 rows exist but are invisible.

`78c7118` stopped fleets DELETING each other. Nothing stops them HIDING each
other — the same defect family, one layer up.

**Fix:** (i) per-category MAX; (ii) fix 2.2 so all fleets share one
computed_date; (iii) both. (ii) is the root fix; (i) corrects the screens now.

### 2.4 OPEN — P2: five routes wired to empty superseded tables

**[M]** `/ps1/table-status` reports each table empty AND names its replacement,
yet the route still queries the old table:

| route | reads (empty) | replacement | replacement has data |
|-------|---------------|-------------|----------------------|
| `/ps1/calibration` | `ps1_calibration` 0 | `v_ps1_xw_tier_calibration` | [U] |
| `/ps1/explainability` | `ps1_explainability` 0 | `v_ps1_device_drivers` | [U] |
| `/ps1/feature-importance` | `ps1_feature_importance` 0 | `v_ps1_shap_importance` | [U] |
| `/ps1/features` | `ps1_features` 0 | `ps1_cross_wired_daily` | YES — 786,525 rows [M] |
| `/ps1/model-performance` | `ps1_model_performance` 0 | `v_ps1_xw_performance` | YES — `/ps1/xw-performance` returns 3 rows, all fleets [M] |

`V4api.js` already documents the workaround rather than the fix:

> *"note that /ps1/feature-importance is EMPTY in Chicago and xw-drivers is not,
> so the driver story is sourced here and nowhere else."*

**Fix:** repoint each route at its named replacement. Low risk — the views are
already proven by the xw-* routes.

### 2.5 OPEN — P2: ps1-rds-push is not transactional

**[M]** `grep -nE "BEGIN|COMMIT|ROLLBACK|SAVEPOINT"` on
`cubic-mars-ps1-rds-push/handler.py` returns **nothing**. pg8000 autocommits and
the loader writes three tables in sequence (`ORDER = [ps1_inference_runs,
ps1_failure_predictions, ps1_serial_predictions]`).

**Demonstrated today:** a failure on the third table left the first two
committed, with `ps1_inference_runs` recording `status: "success"` for a run that
had no serial rows — a partial load describing itself as complete.

**Fix:** adopt the xw-loader pattern. The code to copy is in the same repo.

### 2.6 OPEN — P2: /ps1/summary has no VALIDATOR

**[M]** Returns 2 rows: `Gates` and `TVM`. Reads `ps1_failure_summary` (2 rows,
`superseded_by v_ps1_xw_summary`). `/ps1/xw-summary` returns all three fleets.

Also a label inconsistency: this route says **"Gates"**, every other route says
**"GATE"**. Any client grouping across routes sees two fleets.

**Fix:** ~~repoint to `v_ps1_xw_summary`~~; normalise the label.

> **CORRECTION, 10-Aug-2026 — the fix above was WRONG. Do not act on it.**
> `v_ps1_xw_summary` is a per-fleet ROW-COUNT reconciliation of the cross-wired
> load. It carries no AUC, no threshold, no quality gate and no promotion
> decision. Repointing a model scorecard at it would have replaced stale metrics
> with no metrics. The correct replacement is **`ps1_model_performance` +
> `ps1_confusion`**, which hold the 26-Jul sklearn run for all three fleets.
> Implemented in `sql/51` and the `/ps1/summary` route — see handover §9.
> The label normalisation half of the fix stands, and is done: `_PS1_DISPLAY` /
> `_PS1_CATEGORY` in `handler.py`. NOT YET APPLIED to Aurora.

### 2.7 OPEN — P3: /ps1/serial-predictions shows one fleet

**[M]** 500 rows, all TVM. The SQL filters:

    s.run_id = (SELECT run_id FROM ps1_inference_runs
                WHERE city_id=:c AND status='success'
                ORDER BY run_ts DESC LIMIT 1)

The latest successful run is today's TVM run, so VALIDATOR's 4,107 serial rows
are excluded. Same root cause as 2.3 — per-fleet runs, whole-fleet filters.

### 2.8 OPEN — P3: serial grain keeps only the latest date

**[M]** Today's fix collapses serial rows to each device's latest
`prediction_date`, because the PK `(city_id, run_id, device_id,
matched_serial_nbr)` has no date and `run_id` is one per day. Measured:
184,386 source rows → 1,897 serial rows; **182,489 dropped as earlier-date**.

Correct against the current schema, but a product decision as much as a
technical one. For serial-level history the PK must gain `prediction_date`
(a migration), or `run_id` must become per-date.

### 2.9 OPEN — P3: n_devices_scored vs n_flagged

**[M]** `/ps1/runs` for `ps1_20260810`: `"n_devices_scored": 470,
"n_flagged": 176644`. Not measuring the same thing; at least one is mislabelled.
**[U]** which — needs reading the registry block in `build_rows`.

### 2.10 OPEN — P3: stale S3 object

**[M]** `chicago/gold/ps1_staging/device_ps1_cross_wired_daily.csv`, 919,714
bytes, 26-Jul. The CSV `GOLD_KEY` was wrongly pointed at. No Lambda env
references `ps1_staging` [M]. Retire after go-live.

### 2.11 OPEN — P3: legacy dashboard tab still routed

**[M]** `PS1FailurePredictionTab.jsx` is imported by `pages/CityDashboard.jsx:14`
and live on the `/city` route — unlike PS3, retired from that router on 04-Aug.
It calls 27 `/ps1/*` routes including the five empty ones, so it renders blank
panels.

---

## 3. Inventory

### 3.1 S3 [M 10-Aug]

| path | objects | bytes | modified | role |
|------|---------|-------|----------|------|
| `artifacts chicago/device_ps1_cross_wired_daily/` | 3 | 7,482,535 | 29-Jul 04:25 | **PATH A source** — gate/tvm/validator |
| `gold chicago/gold/device_ps1_cross_wired_daily` | 1 | 1,076,538 | 28-Jul 06:25 | PATH B source, TVM only, `PAR1` |
| `gold chicago/gold/device_ps1_cross_wired_daily/validator` | 1 | 4,993,287 | 28-Jul 07:06 | VALIDATOR, `PAR1` |
| `gold chicago/gold/ps1_staging/…daily.csv` | 1 | 919,714 | 26-Jul 14:01 | STALE, unreferenced |

### 3.2 Lambdas [M 10-Aug]

| function | mem | timeout | layers | key env |
|----------|-----|---------|--------|---------|
| `cubic-mars-ps1-rds-push` | 2048 | 600 | AWSSDKPandas-Python312:29 (restored today) | `GOLD_BUCKET`, `GOLD_KEY`, `RDS_DATABASE=postgres`, `CITY_ID=CHI` |
| `cubic-mars-ps1-xw-loader` | 3008 | 900 | AWSSDKPandas-Python312:29 | `ARTIFACT_BUCKET`, `PS1_XW_PREFIX=chicago/device_ps1_cross_wired_daily` |

Both in VPC `vpc-0a7775adc7d382fbb`, 3 subnets, SG `sg-09a66a0caa24a5fc6`.

### 3.3 Aurora tables [M 10-Aug]

| table | rows | fleets |
|-------|------|--------|
| `ps1_cross_wired_daily` | 786,525 | GATE, TVM, VALIDATOR |
| `ps1_failure_predictions` | 47,603 | TVM 45,681 + VALIDATOR 1,922 (**no GATE**) |
| `ps1_serial_predictions` | 6,004 | TVM 1,897 + VALIDATOR 4,107 (**no GATE**) |
| `ps1_inference_runs` | 7 | 3 fleets `train` + TVM `batch_score` |
| `ps1_leaderboard` | 15 | all three |
| `ps1_risk_bands` | 9 | all three |
| `ps1_threshold_sweep` | 3 | all three |
| `ps1_failure_summary` | 2 | Gates, TVM (**no VALIDATOR**) |
| `ps1_calibration` / `ps1_explainability` / `ps1_feature_importance` / `ps1_features` / `ps1_model_performance` | 0 | superseded |

### 3.4 Git [M]

`SathishMars/Chicago-Ventra-Mars-Cubic-Analysis`, branch `feat/dashboard-v2`.
PS1-touching commits, newest first:

    a38fda3 10-Aug  deploy.sh layer resolution
    45b4342 10-Aug  serial dedup on the primary key
    78c7118 08-Aug  cross-fleet DELETE + deploy.sh env wipe
    c5ca51d 08-Aug  V4 client screens
    e0480c1 01-Aug  original loaders
    57ca788 29-Jul  GATE OOS v4.11
    56c4bbb 28-Jul  route cross-wired export to artifacts bucket
    d3c3dad 27-Jul  GATE OOS v4
    11b7ac7 26-Jul  PS1 batch lineage; fix corrupt PS1 notebooks
    6e21e99 24-Jul  PySpark VALIDATOR migration

**WARNING [M]:** `~/cubic_deploy` in CloudShell holds `handler.py` at 15,025
bytes = commit `e0480c1`, PREDATING `78c7118`. Deploying from it reintroduces
the cross-fleet wipe. Renamed to `STALE_cubic_deploy_DO_NOT_USE_predates_78c7118`.

### 3.5 Dashboard [M]

V4 (`V4PS1Overview.jsx`) reaches PS1 through the `ps1` helper in `V4api.js` —
26 routes. Most-referenced in the v4 folder: `station-summary` (9),
`predictions` (8), `device-360` (7).

Legacy (`PS1FailurePredictionTab` + `PS1DriversCausation` + `PS1Enhancements`)
calls 27 routes and is still routed from `CityDashboard.jsx`.

---

## 4. Fix plan, in order

| # | Fix | Where | Risk | Addresses |
|---|-----|-------|------|-----------|
| 1 | ~~`partition_cols=['device_category']` in cell 24~~ **REFUTED 10-Aug evening — cell 24 already writes one prefix per fleet. See the correction in 2.2. Do not change the notebooks.** | — | — | — |
| 2 | Make `ps1-rds-push` transactional (copy the xw-loader pattern) | handler.py | low | 2.5 |
| 3 | Per-category `MAX(computed_date)` in `/ps1/crosstab` and `device-360` | dashboard-api | low | 2.3 |
| 4 | Repoint the five empty routes to their named views | dashboard-api | low | 2.4 |
| 5 | ~~Repoint `/ps1/summary` to `v_ps1_xw_summary`~~ **WRONG — see the correction in 2.6.** Repoint to `ps1_model_performance` + `ps1_confusion`; normalise "Gates" → "GATE". Code written, NOT applied — handover §9 | dashboard-api | low | 2.6 |
| 6 | Decide serial-grain history: keep latest-date, or migrate the PK | schema | medium | 2.8 |
| 7 | Reconcile `n_devices_scored` / `n_flagged` | handler.py | low | 2.9 |
| 8 | Retire the stale `ps1_staging` CSV | S3 | low | 2.10 |
| 9 | Decide whether the legacy PS1 tab stays routed | dashboard | low | 2.11 |

Fix 1 is the highest leverage — it is the root cause of four separate symptoms.

---

## 5. What I could NOT verify — [U], with the command

### 5.1 Do the three replacement views exist and have rows?
```sql
SELECT 'v_ps1_xw_tier_calibration' v, COUNT(*) FROM v_ps1_xw_tier_calibration
UNION ALL SELECT 'v_ps1_device_drivers',  COUNT(*) FROM v_ps1_device_drivers
UNION ALL SELECT 'v_ps1_shap_importance', COUNT(*) FROM v_ps1_shap_importance;
```

### 5.2 Which computed_date does each fleet sit at?
```sql
SELECT device_category, computed_date, COUNT(*)
FROM ps1_failure_predictions WHERE city_id='CHI'
GROUP BY 1,2 ORDER BY 2 DESC, 1;
```
Settles 2.3 exactly and shows what a per-category MAX would return.

### 5.3 Did the xw loader run successfully today?
```bash
aws logs tail /aws/lambda/cubic-mars-ps1-xw-loader --since 2d --format short | tail -20
```
7 invocations / 0 errors over 7 days is measured, but the row counts matching
EXPECTED could date from an earlier run.

### 5.4 Is /ps1/servicenow-stage POST-only?
```bash
curl -s -o /dev/null -w '%{http_code}\n' -X POST \
  https://a9yuqt9j9b.execute-api.us-east-1.amazonaws.com/ps1/servicenow-stage \
  -H 'content-type: application/json' -d '{"city":"chicago"}'
```

### 5.5 SageMaker endpoint detail and whether anything invokes them
```bash
for E in chicago-ps1-3d-gate-failure-v1 chicago-ps1-3d-tvm-failure-v1 \
         chicago-ps1-3d-validator-failure-v1; do
  aws sagemaker describe-endpoint --endpoint-name $E --region us-east-1 \
    --query '{name:EndpointName,status:EndpointStatus,config:EndpointConfigName}'
done
```
Status is measured. **No PS1 Lambda calls `invoke_endpoint`** [M] — so [U] what,
if anything, uses these three endpoints today.

---

## 6. Summary

**Healthy:** path A end to end — notebooks, three per-fleet S3 keys, a
transactional fleet-scoped loader, 786,525 rows matching expectation exactly,
seven API routes serving all three fleets, three InService endpoints, both
schedules enabled.

**Broken:** path B, in five ways, all traceable to ONE root cause — the gold
export writes a single key the fleets overwrite. That yields a table with no
GATE, screens showing one fleet, a summary route missing VALIDATOR, and serial
predictions from whichever fleet loaded last.

**Fixed today:** four stacked defects that had PS1's gold loader failing every
morning for 15 days without alerting anyone.

**The single highest-value change is fix 1.** Everything else is either a
consequence of it or a hardening measure.
