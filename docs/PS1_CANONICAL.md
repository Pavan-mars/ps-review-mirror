# PS1 — CANONICAL STATUS
### CUBIC MARS Chicago (CTA-Ventra) · failure prediction · 10-Aug-2026, end of day

**This is the single PS1 source of truth.** It supersedes:

| Document | Status |
|---|---|
| `PS1_COMPLETE_AUDIT_10Aug2026.md` | HISTORICAL — the morning snapshot. Two of its findings are refuted here (§7). Keep for the reasoning trail. |
| `PS1_STATUS_10Aug2026_EOD.md` | SUPERSEDED by this document. |
| `CUBIC_CHICAGO_HANDOVER_10Aug2026.md` §9 | Still current for the scorecard-retirement detail. |

---

## 0. HOW TO READ THIS

| Tag | Means | Provable right now? |
|---|---|---|
| **[V]** | Verified first-hand — read from the pushed repo, or produced by executing the code in this session | **Yes** |
| **[T]** | Tested — executed against PostgreSQL 16.13 with production table shapes, or against stubbed AWS | **Yes** |
| **[M 05:48Z]** | Measured from the live AWS inventory captured 2026-08-10 05:48Z | Snapshot only |
| **[M am]** | Measured live this morning against Aurora and the API | Trusting the morning measurement |
| **[U]** | **UNKNOWN** — needs CloudShell | **No** |

**Two standing caveats, stated once:**

1. The 05:48Z inventory **predates** the Path B repair, the 11:55Z Path B disable, and the afternoon dashboard-api redeploy. Anything about `cubic-mars-ps1-rds-push` or `cubic-mars-dashboard-api` from that snapshot is stale by design.
2. **Nothing committed after `9222ccd` has been deployed or applied.** All of §5's code fixes are in git and tested; none is running in AWS. `/ps1/summary` is still serving the 13-Jul seed in production right now.

---

## 1. BOTTOM LINE

PS1 runs **two independent paths from the same three notebooks.**

| | **Path A — cross-wired** | **Path B — legacy predictions** |
|---|---|---|
| Loader | `cubic-mars-ps1-xw-loader` | `cubic-mars-ps1-rds-push` |
| Source | `artifacts/chicago/device_ps1_cross_wired_daily/{gate,tvm,validator}/` | `gold/chicago/gold/ps1_staging/…​.csv` |
| Schedule | `cron(40 6)` ENABLED | `cron(15 6)` **DISABLED 11:55Z** |
| Rows | **786,525** — exact match to the loader's own EXPECTED | 47,603 + 6,004 + 7, frozen |
| Fleets | GATE + TVM + VALIDATOR | TVM + VALIDATOR, **zero GATE, ever** |
| Completeness | 100% | 13% |
| Dashboard | ~20 `xw-*` routes | 5 routes still read its frozen tables |

**Path A is healthy and exact. Path B is deliberately frozen. No PS1 route is down. Three SageMaker endpoints are InService.**

Today PS1 went from *four stacked defects, silently broken for 15 days* to *one healthy path, one safely-frozen path, nine fixes committed, two false findings withdrawn, and an honest list of what is left.*

---

## 2. ARCHITECTURE

```
   PS1_3d_{TVM,GATE,VALIDATOR}_SageMaker_MLflow_FeatureStore.ipynb
                      cell 24 / 26
                            │
                            │  writes ONE PREFIX PER FLEET  [V]
                            │  for _cat in device_category.unique():
                            │      _cat_df.to_parquet(f"{base}/{slug}/")
                            ▼
   s3://…-artifacts/chicago/device_ps1_cross_wired_daily/{gate,tvm,validator}/
                            │  3 objects · newest 2026-07-29 · STATIC  [M 05:48Z]
                            ▼
                  cubic-mars-ps1-xw-loader          ◀── PATH A, LIVE
                  BEGIN / SAVEPOINT per fleet / COMMIT
                            ▼
                  ps1_cross_wired_daily  786,525 rows
                            ▼
                  ~20  /ps1/xw-*  routes — all three fleets


   s3://…-gold/chicago/gold/ps1_staging/…​.csv     ◀── PATH B, FROZEN
                            │  ⚠ WRITTEN BY NOTHING IN THIS REPO  [V]
                            ▼
                  cubic-mars-ps1-rds-push
                  no transaction · pg8000 autocommits
                            ▼
        ps1_failure_predictions / ps1_serial_predictions / ps1_inference_runs
                            ▼
        5 routes: crosstab · device-360 · serial-predictions · coverage · runs
```

**Why Path B was abandoned:** four loads in its entire life, one named `ps1_demo_v2`, never once holding all three fleets. [M am]

---

## 3. COMPONENT INVENTORY

### 3.1 S3 [M 05:48Z]

| Bucket | Prefix | Obj | Bytes | Newest |
|---|---|---:|---:|---|
| artifacts | `chicago/device_ps1_cross_wired_daily/` | 3 | 7,482,535 | **2026-07-29 04:25Z** |
| gold | `chicago/gold/` | 115 | 1,806,929,156 | 2026-07-28 07:06Z |

Three objects = one per fleet, matching the loader's one-key-per-fleet read. [V]

`chicago/gold/ps1_staging/device_ps1_cross_wired_daily.csv` — the stale Path B CSV. Retire **only after** the S3 ObjectCreated trigger on that prefix is confirmed disarmed.

### 3.2 Git [V]

Branch `feat/dashboard-v2`. Remote at `9222ccd`; **4 commits unpushed**.

```
api/lambda/cubic-mars-ps1-xw-loader/     handler.py deploy.sh requirements.txt
api/lambda/cubic-mars-ps1-rds-push/      handler.py deploy.sh requirements.txt
                                         README.md test_handler.py
api/lambda/cubic-mars-dashboard-api/     handler.py + sql/ (52) + sql/load/
notebooks/ps1_failure_prediction/        4 live + 12 archived notebooks
tooling/                                 ps1_verify_state.sh  ps1_retire_apply.sh
                                         lambda_drift_check.sh
```

**Today's PS1 commits, oldest first** [V]

| Commit | Pushed | What |
|---|:-:|---|
| `45b4342` | ✅ | Serial dedup keyed on (device, serial, **day**) while the PK has no date |
| `a38fda3` | ✅ | `deploy.sh` used `list-layer-versions` — denied on another account's public layer |
| `0ad24e8` | ✅ | Morning audit + Path B disable record |
| `73df05d` | ✅ | The **deployed** xw-loader — 11,521 bytes existing in no repository |
| `f52b7fb` | ✅ | Fleet-blind top-N: 500 VALIDATOR rows, zero TVM/GATE |
| `a039b84` | ✅ | Unique index on the cross-wired grain + `apply_sql` |
| `9222ccd` | ✅ | Scorecard retirement + causation panel |
| `93a92e3` | ❌ | EOD status doc + `ps1_verify_state.sh` |
| `6b78b20` | ❌ | **L-2** deploy.sh guard + **S-1** source freshness |
| `e811b29` | ❌ | `partition_cols` refutation + `lambda_drift_check.sh` |
| `c9089ea` | ❌ | **D-1** write guard + **E-1** serving disclosure + PS4 PATCH fix |

### 3.3 SageMaker [M 05:48Z]

| Endpoint | Status | Modified |
|---|---|---|
| `chicago-ps1-3d-tvm-failure-v1` | InService | 2026-07-24 07:46Z |
| `chicago-ps1-3d-gate-failure-v1` | InService | 2026-07-24 07:53Z |
| `chicago-ps1-3d-validator-failure-v1` | InService | 2026-07-24 09:33Z |

**All three serve the SPARK champion. The dashboard scorecard shows the 26-Jul sklearn run.** They are not the same model — see §5 E-1.

### 3.4 Lambdas

**`cubic-mars-ps1-xw-loader`** — Path A, healthy [M 05:48Z + V]

```
python3.12 · 3008 MB · 900 s · layer AWSSDKPandas-Python312:29 · 3 subnets
ARTIFACT_BUCKET=…-artifacts   PS1_XW_PREFIX=chicago/device_ps1_cross_wired_daily
IAM: GetObject on that prefix only; ListBucket prefix-conditioned
EXPECTED = {gate 107110, tvm 184483, validator 494932, total 786525}
actions: dry_run · load · verify · audit · reload_source · copy_rows · csv_cell
         + freshness  (NEW today)
```

Transactional: `BEGIN` / per-fleet `SAVEPOINT` / `COMMIT` / `ROLLBACK`, fleet-scoped DELETE, and it **refuses to delete on an empty shape**. **This is the reference implementation — every other loader should look like it.**

**`cubic-mars-ps1-rds-push`** — Path B, disabled. Current live config is **[U]**; at 05:48Z it still had no layer and the CSV `GOLD_KEY`, LastModified 26-Jul.

### 3.5 EventBridge [M 05:48Z]

| Rule | Schedule | Target | State |
|---|---|---|---|
| `cubic-mars-ps1-xw-daily-load` | `cron(40 6)` | xw-loader | ENABLED |
| `cubic-mars-ps1-daily-push` | `cron(15 6)` | rds-push | disabled 11:55Z → **[U]** |

No collision with PS2 (07:10) / PS4 (07:35) / PS5 (07:20); runs after `dim-daily-refresh` (05:45). [V]

### 3.6 Aurora — `cubic-mars-rds-aurora-dev`, aurora-postgresql 16.4 [M 05:48Z]

| Table | Rows | Path | State |
|---|---:|---|---|
| `ps1_cross_wired_daily` | 786,525 | A | healthy, exact [M am] |
| `ps1_model_performance` | 3 | — | loaded today, was 0 |
| `ps1_feature_importance` | 45 | — | loaded today, was 0 |
| `ps1_failure_predictions` | 47,603 | B | frozen |
| `ps1_serial_predictions` | 6,004 | B | frozen |
| `ps1_inference_runs` | 7 | B | frozen |
| `ps1_failure_summary` | 2 | — | retired in code, **not yet in the DB** |
| `ps1_calibration` / `ps1_explainability` / `ps1_features` | 0 | — | no S3 source |

### 3.7 Dashboard [V]

**117 HTTP routes total; 36 are `/ps1/*`.** 34 returned 200 this morning; two correctly 400 without `device_id`. [M am]

| Group | N | State |
|---|---:|---|
| `xw-*` cross-wired | ~20 | healthy, all three fleets |
| Path B readers | 5 | 13%-complete data |
| Empty | 3 | `calibration`, `explainability`, `features` |
| Scorecard | 3 | fixed in code, **not applied** |

---

## 4. WHAT IS RIGHT

1. **Path A is complete and exact** — 786,525 rows = the loader's own EXPECTED constant, to the row. [M am]
2. **The xw-loader is correctly engineered** — transactional, fleet-scoped, refuses empty loads, self-declaring contract. [V]
3. **Notebook exports are correct** — cell 24 writes one prefix per fleet. [V]
4. **Three endpoints InService**, one per fleet. [M 05:48Z]
5. **Schedules do not collide** and run in dependency order. [V]
6. **IAM is tightly scoped** — one prefix, prefix-conditioned ListBucket. [M 05:48Z]
7. **Fleet-blind top-N fixed and verified live** — 300/60 rows across all fleets. [M am]
8. **Cross-wired grain has a unique index**, `NULLS NOT DISTINCT` (needed: 63% of rows have NULL serial). [M am]
9. **The deployed loader is in version control.** [V]
10. **Path B is frozen reversibly** — concurrency was 1, restorable; snapshot + UNDO.txt in CloudShell. [M am]

---

## 5. WHAT WAS FIXED TODAY — what, how, why, and how it was tested

### 5.1 The four stacked defects that had Path B silently broken for 15 days [M am]

| # | Defect | Why it mattered | Fix |
|---|---|---|---|
| 1 | AWSSDKPandas layer missing | `ImportModuleError: No module named 'pyarrow'`, dying in 450 ms | restored live |
| 2 | `GOLD_KEY` pointed at a CSV | handler only calls `pq.read_table`. Magic bytes: CSV starts `"DEV`, Parquet starts `PAR1` | repointed live |
| 3 | `deploy.sh` could never resolve the layer | used `list-layer-versions`, denied on another account's public layer. **The only script that could restore the layer aborted before it could** | `a38fda3` — `get-layer-version-by-arn` |
| 4 | Serial dedup keyed on (device, serial, **day**) | `ps1_serial_predictions_pkey` has no date, so any multi-date file violated the key | `45b4342` — collapse to latest date per device |

Result: first full PS1 load since 26-Jul — 45,681 device-day + 1,897 serial rows.

### 5.2 Fleet-blind top-N — `f52b7fb`

**Why:** `ORDER BY prob DESC LIMIT 500` across all fleets. VALIDATOR probabilities sit at ~0.99997, so all 500 rows were VALIDATOR and **neither TVM nor GATE appeared at all**.
**How:** `ROW_NUMBER() OVER (PARTITION BY device_type ORDER BY …)`, ranking within each fleet.
**Tested:** live — 300/60 rows, all fleets present. [M am]

### 5.3 Cross-wired grain unique index — `a039b84`

**Why:** no constraint enforced the grain; duplicate loads were possible.
**How:** `sql/49`, with a DO-block that **refuses to create the index if duplicates already exist** rather than failing halfway. `NULLS NOT DISTINCT` because `component_serial_nbr` is NULL for 63% of rows.
**Tested:** applied live. [M am]

### 5.4 Scorecard retirement + causation panel — `9222ccd` *(committed, NOT applied)*

**Why `/ps1/summary` was wrong:** it read `ps1_failure_summary` — two rows hand-typed from a console log on 13-Jul, `quality_gate FAIL`, `promoted false`, `target='will_fail_3d'` (a label that exists nowhere; the real one is `will_hardware_oos_3d`), no VALIDATOR, no loader, no refresh path. It could only age.
**How:** repointed to `ps1_model_performance` + `ps1_confusion`. Accuracy and the whole operating point are **derived** from the run's own TP/FP/TN/FN — the confusion matrix was computed *at* `decision_threshold`, so precision/recall read off it *are* the operating numbers. Unmeasured fields (Brier, top-k, `n_train`, `overfit_flag`) return **null, never back-filled** — a null reads as "not measured", a stale number reads as fact.
**Why the causation panel was wrong:** `sql/34` ended with `WHERE n_chain >= 30 AND n_no_chain >= 30`. The intent was right — a lift from a handful of device-days is noise wearing a decimal point. But **a WHERE clause does not say "not enough data", it says nothing at all.** GATE vanished, and a reader concluded PS1 had no GATE model.
**How:** `sql/50` publishes one row per fleet *always*, with `sufficient_data` and `min_cell` appended and the rate columns NULL when the bar isn't cleared.
**Tested [T]:** PostgreSQL 16.13, production table shapes, split with the handler's own `split_sql()`. Both files parse under `pglast`, execute, idempotent on re-apply, column prefix preserved. Rollback verified to **require** a DROP first — `CREATE OR REPLACE` raises `42P16 cannot drop columns from view`.

### 5.5 L-2 — `deploy.sh` could resurrect Path B — `6b78b20`

**Why it mattered:** `put-rule … --state ENABLED` plus `put-bucket-notification-configuration` meant anyone running that script *for any reason* silently re-enabled the cron disabled at 11:55Z and re-armed the S3 trigger. Reserved concurrency 0 would still throttle it — **that is luck, not design**, and rests on a second setting the script never checks.
**How:** refuses without `PATHB_REVIVE=1`; rule created `--state DISABLED`; S3 trigger behind `PATHB_ARM_S3_TRIGGER=1`. Concurrency deliberately untouched — it is the last brake and a deploy script must not release it.
**Tested [T]:** stubbed `aws` to print on invocation → **zero AWS calls** before the refusal, exit 2. Opt-in path confirmed to proceed.

### 5.6 S-1 — a green cron that proved nothing — `6b78b20`

**Why it mattered:** the source objects have been static since 29-Jul. The 06:40 cron re-read them, rewrote the same 786,525 rows and reported success — **every morning for twelve days.** A re-load of identical bytes and a genuine refresh produced byte-identical reports. Correct today (data stops 11-Apr), catastrophic the day daily ingestion starts and silently doesn't.
**How:** `head_object` per fleet (metadata only, no transfer), ETag compared against the last **successful** load in `ml_batch_load_audit.s3_source` encoded `<key>@<etag>`. ETag not LastModified: re-uploading identical content moves the timestamp but not the ETag. Emits per-fleet `UNCHANGED` / `CHANGED` / `NO_PRIOR_LOAD` / `UNREADABLE` and a plain-language verdict. New read-only `{"action":"freshness"}`. **This loader wrote no lineage row at all before today** — that absence is exactly why twelve identical runs were indistinguishable.
**Tested [T]:** five scenarios on PostgreSQL 16.13 with stubbed S3. **Scenario 5 caught a bug in my own logic** — the first version let two healthy fleets outvote one missing one and announced *"genuinely new data for every fleet"* while GATE was absent from S3. `UNREADABLE` now dominates. Also verified `last_loaded_etags` reads only `status='success'` rows, so a failed load cannot become the baseline that hides the next one.

### 5.7 G-2 — no drift detection — `e811b29`

**Why:** the deployed xw-loader was 11,521 bytes ahead of the repo and nothing noticed for weeks. Every loader has the same exposure.
**How:** `tooling/lambda_drift_check.sh` downloads each deployed package and diffs `handler.py`. Generic by name-match, so it covers PS2–PS5 unedited.
**Tested [T]:** stubbed AWS with purpose-built fixtures — MATCH, DRIFT (writes diff, exit 1), NO-HANDLER, NO-REPO-SRC all correct.

### 5.8 D-1 — the anonymous write surface — `c9089ea`

**Why it mattered:** both gateways carry `AuthorizationType: NONE`. Of 117 routes, three are not read-only and all are reachable by anyone with the URL: two `servicenow-stage` POSTs (INSERT) and `PATCH /ps4/alerts/{id}` (UPDATE). `servicenow_staging` is **a queue that becomes real work orders** — the route's own response says *"Wire Robin's ServiceNow endpoint to submit."* `payload_json` is unbounded TEXT, so one caller in a loop is a disk-fill DoS. And anyone can mark any anomaly alert `resolved` — silencing an alert is worse than reading one.
**How:** 16 KB payload ceiling, `device_id` charset/length validation, per-city hourly staging quota (429, writes nothing) — all unconditional. Plus optional `x-cubic-token`, **off by default** so arming it cannot break the dashboard. `hmac.compare_digest` because a prefix leak lets a caller discover the token a character at a time; case-insensitive header lookup because API Gateway lowercases and curl doesn't.
**This is defence in depth, NOT the fix.** The fix is gateway authorization — an architecture decision, not a code change. A token in a public React bundle is not a secret; that is written into the code so nobody concludes the API is secured.
**Tested [T]:** legitimate staging still 200; 20 KB payload → 413, nothing written; path-traversal / over-length / quote-injection device_ids → 400; 12 flood attempts against quota 5 → 4 accepted, 8 × 429, row count bounded; token path → 401 no header, 401 wrong, pass on mixed-case header; quota still applies with a valid token; **all reads unchanged throughout.**

### 5.9 E-1 — the endpoints serve a different model than the dashboard shows — `c9089ea`

**Why it mattered:** `sql/load/ps1_sklearn_20260726.sql` already said it — in a comment: *"…still serving the SPARK champion. Until those are re-registered, this scorecard describes the selected model, not the one answering inference calls."* **A comment in a migration file is not a disclosure.** Anyone reading AUC 0.9040 assumes that is what the endpoint returns. It isn't, and the gap has been open since 26-Jul.
**How:** `/ps1/model-performance` now **derives** the comparison — each scorecard row's `run_id` against the run recorded for that endpoint in `ps1_inference_runs` — and returns `serving_matches_scorecard`, `serving_run_id`, `serving_caveat`.
**Also confirmed [V]:** **no PS1 code path invokes a PS1 endpoint.** The only `invoke_endpoint` calls in the repo are PS3's, in a Lambda that is not deployed. Every PS1 number comes from batch output. And `endpoint_name` in `ps1_inference_runs` is built by **string concatenation** from `PS1_ENDPOINT_PREFIX` — it asserts a serving link nothing verifies.
**Tested [T]:** three states — mismatch, match, and no record at all. **Scenario C caught me repeating the morning's mistake:** a fleet with no `ps1_inference_runs` row fell through as a silent `None` with no caveat, making "absent record" and "verified match" render identically — the exact silence-vs-insufficiency failure `sql/50` exists to fix, made again eight hours later. Absent records now carry their own caveat.

### 5.10 PS4 PATCH bug, found while testing D-1 — `c9089ea`

`PATCH /ps4/alerts/{id}` **has never worked**: `42P08 inconsistent types deduced for parameter $1: text versus character varying`. `:s` is assigned to a VARCHAR column and compared against text literals; PostgreSQL cannot deduce one type. Every PATCH has returned 500 since it was written. Explicit casts on both sides. **Verified failing before and passing after on PostgreSQL 16.13** [T]. A PS4 finding, a day early.

---

## 6. WHAT IS STILL WRONG

| # | Issue | Evidence | Fix | Effort |
|---|---|---|---|---|
| 1 | **Nothing from `93a92e3`…`c9089ea` is deployed or applied.** `/ps1/summary` still serves the 13-Jul seed; GATE still absent from causation | [V] | `ps1_retire_apply.sh APPLY=1`, then redeploy dashboard-api and xw-loader | 1 hr |
| 2 | 5 routes read frozen Path B tables — 13% complete, indefinitely | [M am] | Repoint to Path A views, or retire | half day |
| 3 | `MAX(computed_date)` hides fleets in `/ps1/crosstab` and `/ps1/device-360` | [M am] | Per-category `MAX` | 1 hr |
| 4 | 3 routes empty — tables have no S3 source | [M am] | Notebooks must export, or retire the routes | notebook change |
| 5 | **D-1 proper: gateway `auth=NONE`** — §5.8 only bounds it | [M 05:48Z] | JWT authorizer / IAM auth / WAF — **architecture decision** | PK call |
| 6 | **E-1 proper: endpoints serve the Spark champion** — §5.9 only discloses it | [V] | Re-register the sklearn models, or stop presenting them as serving | PK call |
| 7 | `ps1-rds-push` not transactional — pg8000 autocommits, partial loads commit as `success` | [M am] | Copy the xw-loader pattern — **only if Path B is revived** | 2 hr |
| 8 | `ps1_leaderboard` model rows still the 13-Jul seed; only the floor *source* was repointed | [V] | Repoint or retire | half day |
| 9 | `n_devices_scored` 470 vs `n_flagged` 176,644 — at least one is wrong | [M am] | Reconcile | 1 hr |
| 10 | Stale `ps1_staging` CSV in S3 | [M 05:48Z] | Delete — **after** confirming the S3 trigger is disarmed | 5 min |
| 11 | Legacy `PS1FailurePredictionTab` still routed; renders legitimate nulls as blanks | [V] | Retire or rework | decision |
| 12 | VALIDATOR has no `recall_floor` — gated on nothing | [V] | Parked by your decision, 10-Aug | — |
| 13 | **What produced Path B's source object?** Nothing in the repo writes it | [V] | Identify before any revival | investigation |

---

## 7. FINDINGS I GOT WRONG, AND WITHDREW

Recorded because the *mechanism* matters more than the error.

### 7.1 "The gold export overwrites fleets — `partition_cols`" — **REFUTED**

Claimed as the root cause of four symptoms and the top fix. Cell 24 **already writes one prefix per fleet** via an explicit loop; the cell header says *"GATE / TVM / VALIDATOR separate prefixes"*. That is precisely why Path A is healthy. **Nothing in the repo writes the gold key at all** — the only reference reads it as `ps1_xw_legacy_bases`.

**How the error happened:** the morning check grepped for the string `partition_cols` and correctly found it absent. I then **inferred without looking** that the notebooks must therefore share one key. Then I quoted as corroboration a sentence from `cubic-mars-ps1-rds-push/handler.py:274` — which is **a hypothesis written into a code comment** by whoever built Path B. I cited a comment asserting a cause as evidence for that cause. It survived because the conclusion sounded right.

### 7.2 "Repoint `/ps1/summary` to `v_ps1_xw_summary`" — **REFUTED**

`v_ps1_xw_summary` is a row-count reconciliation with no AUC, no threshold, no gate, no promotion decision. Acting on it would have traded stale metrics for **no** metrics. Correct replacement: `ps1_model_performance` + `ps1_confusion`.

---

## 8. UNKNOWNS — and the one script that closes them

Run `tooling/ps1_verify_state.sh` (read-only). Nine open items:

1. Current `ps1-rds-push` config — layer, `GOLD_KEY`, reserved concurrency
2. Current state of `cubic-mars-ps1-daily-push`
3. Whether the S3 ObjectCreated trigger is still armed
4. Current `dashboard-api` deployed package
5. **SageMaker invocation counts** — E-1's cost decision hinges on this
6. Live row counts for every table in §3.6
7. Whether `recall_floor` / `target_col` are populated on the live `ps1_model_performance` rows
8. **GATE's real `min_cell`** — the 6 in my tests was a fixture, not Chicago
9. Whether last night's crons ran, and with what result

Then run `tooling/lambda_drift_check.sh` — it will answer the same drift question for PS2–PS5 in one shot.

---

## 9. RECURRING FAILURE PATTERNS — check these first, on every problem statement

1. **Silence ≠ insufficiency.** A `WHERE` clause that drops a row does not say "not enough data" — it says nothing. Publish the row, withhold the conclusion. *Made twice in one day: `sql/34`'s causation guard, and again in the E-1 serving check eight hours later.*
2. **A comment is not a measurement.** Code comments asserting a cause are hypotheses. Never cite one as evidence — trace to the artefact.
3. **Fleet-blind top-N.** `ORDER BY prob DESC LIMIT n` across fleets → one fleet takes every slot. Always `PARTITION BY device_type`.
4. **`MAX(computed_date)` hides fleets** loaded on different dates.
5. **Replace-not-update AWS calls.** `update-function-configuration --environment` and `--layers`, `iam put-role-policy`, `events put-rule`, `put-bucket-notification-configuration` all **replace wholesale**.
6. **IAM prefix anchoring.** `ps3_outputs/*` does **not** match `chicago/ps3_outputs/`.
7. **Green ≠ fresh.** A successful load of unchanged bytes looks identical to a real refresh unless something compares content.
8. **Deployed ≠ committed.** Check drift before trusting any repo file to describe production.
9. **`NULLS NOT DISTINCT`** (PG15+) is required wherever a grain column is nullable — 63% of PS1 rows have NULL `component_serial_nbr`.
10. **Null beats a stale number.** A null reads "not measured"; a carried-forward figure reads as fact.

---

## 10. ORDER OF WORK

1. **Run both scripts** — `ps1_verify_state.sh`, then `lambda_drift_check.sh`. Do not fix anything until §8 is measured.
2. **Push the 4 commits.**
3. **Apply and deploy** §6 item 1 — this is the largest single gap between what is written and what is running.
4. **Decide** §6 items 5 and 6 — gateway auth, and whether the endpoints get re-registered or retired.
5. Then Path B route repointing, `MAX(computed_date)`, and the rest.

Once 1–4 are closed, PS1 is done and PS2 gets the same treatment.
