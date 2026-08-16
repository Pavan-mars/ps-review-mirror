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
2. **PS1 IS FULLY DEPLOYED AND APPLIED as of 2026-08-11 06:54Z.** Nothing
   committed remains un-run. Verified end state on all three fleets:
   `target=will_hardware_oos_3d`, `run_id=ps1_sklearn_20260726`,
   `serving=ps1_20260726`, `serving_run_kind=train`,
   **`serving_matches_scorecard=false`** — E-1 correctly disclosed.
   Original go-live note: **deployed and applied 2026-08-11 05:17Z** via `tooling/ps1_go_live.sh`: the
   dashboard-api code through commit `bcb22da`, plus `sql/50` (4/4 statements) and
   `sql/51` (6/6), plus the xw-loader freshness build. `/ps1/summary` now serves
   `ps1_model_performance + ps1_confusion`; GATE is on the causation panel.
   **Still NOT deployed:** commit `cafecac` — `sql/52` and the run_kind fix — which
   is why `target_col` and `run_id` are still NULL (§8.5).

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
| ~~1~~ | ~~Nothing is deployed~~ **DONE 2026-08-11 05:17Z.** `/ps1/summary` serves 3 fleets from `ps1_model_performance`; GATE is on the causation panel at min_cell=19 | [M 11-Aug] | done | done |
| **1b** | **`cafecac` not deployed** — `target_col`, `recall_floor`, `run_id` NULL on every `ps1_model_performance` row, so the scorecard cannot state its label and the serving comparison cannot compute | [M 11-Aug] | `tooling/ps1_provenance_fix.sh APPLY=1` | 15 min |
| 2 | 5 routes read frozen Path B tables — 13% complete, indefinitely | [M am] | Repoint to Path A views, or retire | half day |
| 3 | `MAX(computed_date)` hides fleets in `/ps1/crosstab` and `/ps1/device-360` | [M am] | Per-category `MAX` | 1 hr |
| 4 | 3 routes empty — tables have no S3 source | [M am] | Notebooks must export, or retire the routes | notebook change |
| 5 | **D-1 proper: gateway `auth=NONE`** — §5.8 only bounds it | [M 05:48Z] | JWT authorizer / IAM auth / WAF — **architecture decision** | PK call |
| 6 | **E-1 CONFIRMED: 0 invocations in 14 days on all three endpoints**, and they serve the Spark champion while the dashboard shows sklearn | [M 11-Aug] | Re-register the sklearn models and wire inference, or stop the endpoints. **Now a measured cost decision, not a hypothesis** | PK call |
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

## 8. MEASURED — the unknowns, closed 2026-08-11 05:56Z  [M 11-Aug 05:56Z]

`tooling/ps1_verify_state.sh` and `tooling/lambda_drift_check.sh`, both read-only.
Report: `~/ps1_verify_20260811T055644Z/report.txt`.

### 8.1 Path B: repairs survived, and it is genuinely off

| Check | Result | Verdict |
|---|---|---|
| layer | `AWSSDKPandas-Python312:29` | **OK** |
| `GOLD_KEY` | `chicago/gold/device_ps1_cross_wired_daily` | **OK — parquet, not the CSV** |
| reserved concurrency | **0** | throttled |
| `cubic-mars-ps1-daily-push` | **DISABLED** | as intended |
| `cubic-mars-ps1-xw-daily-load` | **ENABLED** | as intended |

`ps1-rds-push` LastModified **2026-08-10 07:57:07Z** — which fixes the timeline the
05:48Z inventory could not: the layer and GOLD_KEY repairs landed at 07:57Z,
two hours after that snapshot, and the disable followed at 11:55Z.

**The S3 ObjectCreated trigger is STILL ARMED** — `ps1-cross-wired-push` on prefix
`chicago/gold/device_ps1_cross_wired` → `cubic-mars-ps1-rds-push`. Deliberate per
handover §8, as a loud signal if a notebook still writes to the dead prefix.
Concurrency 0 means it would throttle rather than load. Confirm the intent still holds.

### 8.2 E-1 CONFIRMED — three endpoints, zero traffic

```
chicago-ps1-3d-tvm-failure-v1         InService   invocations_14d = 0.0
chicago-ps1-3d-gate-failure-v1        InService   invocations_14d = 0.0
chicago-ps1-3d-validator-failure-v1   InService   invocations_14d = 0.0
```

Three endpoints InService since 24-Jul with **no traffic at all in 14 days**. This
was [U] and is now measured. It is a **cost decision, not a defect** — but it must
be a decision. Combined with §5.9 (no PS1 code path invokes them) and the fact that
they serve the Spark champion while the dashboard shows the sklearn run, the honest
options are: re-register the sklearn models and wire real-time inference, or stop
the endpoints until PS1 actually serves.

### 8.3 Path A is exact, to the row

```
GATE       107,110    TVM  184,483    VALIDATOR  494,932
TOTAL      786,525    = EXPECTED      last_day = 2026-04-11 on all three
```

`ps1_failure_summary` now reports `retired=True, superseded_by=ps1_model_performance`
— sql/51 applied and visible in the catalog.

### 8.4 GATE's real numbers — the shortfall is far starker than the fixture

| fleet | n_chain | n_no_chain | lift | sufficient | min_cell |
|---|---:|---:|---:|:-:|---:|
| VALIDATOR | 170,337 | 324,595 | **12.257** | true | 170,337 |
| TVM | 2,220 | 182,263 | 1.105 | true | 2,220 |
| **GATE** | **19** | 107,091 | — | **false** | **19** |

**This is the finding sql/50 was built to make visible.** GATE has 107,091
device-days with no coordinated station failure and **19** with one. Nineteen. The
old view's `WHERE n_chain >= 30` deleted the fleet rather than reporting that
number, and a reader concluded PS1 had no GATE model.

The substantive result: **coordinated station failures raise the VALIDATOR CRITICAL
rate more than twelvefold, on 170k device-days each side.** TVM moves 1.105 — nothing.
GATE cannot be assessed, because GATE devices are almost never in one. That is three
different answers, and only one of them was visible before today.

### 8.5 Still NULL — sql/52 is written but NOT applied  [V]

`target_col`, `recall_floor`, `run_id` are NULL on all three
`ps1_model_performance` rows. The scorecard cannot state its label and the
serving comparison cannot be computed. `sql/52` + the corrected handler fix this;
`tooling/ps1_provenance_fix.sh` applies them. **Not yet run.**

### 8.6 Drift sweep — the estate is cleaner than feared

**9 MATCH · 2 DRIFT · 1 NO-REPO-SRC · 0 ERROR**

| Function | Verdict | Assessment |
|---|---|---|
| `cubic-mars-dashboard-api` | DRIFT −1,559 | **Benign.** Deployed = `bcb22da` (264,727), repo = `cafecac` (266,286). Repo newer because the provenance fix was committed after the go-live. Closes on the next deploy. |
| `cubic-mars-ps3-v2-loader` | DRIFT −871 | **Benign — the entire difference is a docstring.** The diff has ZERO `+` lines; 15 deletions, all the `cell()` comment. The int-coercion fix is present in both. Functionally identical. |
| `cubic-mars-ps2-rds-push` | NO-REPO-SRC | **Real.** Deployed with no source in this repo. Open item 8, now measured. |
| 9 others | MATCH | dim-loader, ps1-rds-push, ps1-xw-loader, ps2-rds-loader, ps3-rc-loader, ps3-v25-loader, ps4-rds-loader, ps4-v3-loader, ps5-rds-loader |

Also confirmed by absence: **`cubic-mars-ps3-inference` is in the repo but NOT
deployed** (open item 9), and `ps5_daily_scorer` has no deployed counterpart.

**The xw-loader was the outlier, not the pattern.** Yesterday's evidence suggested
systemic drift; the measurement says nine of twelve match exactly and both
divergences are harmless.

> **A METHOD NOTE, because I got this wrong twice in one hour.** From the −871 byte
> delta I concluded ps3-v2-loader was "worse than a skipped deploy" and that "871
> bytes is not a typo fix". Both were inferences from a size number, and the diff
> refuted them. A byte delta says *something* differs; it cannot say *what*. The
> preserve-then-read discipline caught it. Read the diff before assigning a cause.

### 8.7 The last unknown — CLOSED. Path B has not run since the disable  [M 11-Aug]

```
aws cloudwatch get-metric-statistics --metric-name Invocations \
  --dimensions Name=FunctionName,Value=cubic-mars-ps1-rds-push \
  --start-time 2026-08-10T12:00:00Z ...

|   GetMetricStatistics  |
+--------+---------------+
|  Label |  Invocations  |
+--------+---------------+        <-- EMPTY. Zero datapoints.
```

**Zero invocations of `cubic-mars-ps1-rds-push` since 2026-08-10 12:00Z.** The
disable holds. The EventBridge rule is off, and the still-armed S3 trigger has not
fired either, because nothing has written to `chicago/gold/device_ps1_cross_wired`
since. Path B is not merely configured off — it is measurably idle.

**Why the 3-day aggregate looked alarming and was not.** `ps1-rds-push` over 3 days
read Invocations 14, Errors 11, Throttles 0, against an EXPECT line of
"Throttles>0 or Invocations=0". Neither held, so it read as a failed disable.
**The EXPECT line was wrong, not the disable.** The 3-day window spans the 11:55Z
cutover, so those invocations are pre-disable crons plus the morning repair, and the
11 errors match the missing-layer period that ended at 07:57Z. Throttles is 0
because a disabled rule never fires, so there is nothing to throttle.

> **The lesson is about the check, not the system.** An EXPECT line that cannot
> distinguish "working" from "broken" is worse than no EXPECT line, because it
> manufactures alarm. Any metric assertion spanning a state change must be windowed
> to one side of it. `ps1_verify_state.sh` [U9] should query from the disable
> timestamp forward, not a rolling 3 days.

### 8.8 The sql/52 -> 53 -> 54 chain, and what it cost  [V][T]

Three migrations to fill five columns. Each failure was mine, each was different,
and the sequence is worth keeping because the second and third were caused by the
fix for the first.

| File | Result | What went wrong |
|---|---|---|
| `sql/52` | 5 of 6 applied | Selected `label_revision` FROM `ps1_inference_runs`. That column does not exist there — sql/16 added it to `ps1_model_performance` and `ps1_failure_summary` only. `42703`. |
| `sql/53` | 6 of 6 applied, **wrong answer** | Backfilled `run_id` from "latest train run per endpoint". Two runs landed on 2026-07-26; the Spark run at 16:40 beat the sklearn run at 12:00. Sklearn metrics got the Spark run_id, the serving lookup agreed with itself, and the API asserted **`matches=true`** — the exact claim E-1 exists to prevent. |
| `sql/54` | 5 of 5 applied, **correct** | Discriminated on `mlflow_version`, which each load stamped into the row alongside its own run_id. `matches` returns **false**, as it should. |

**Failure 1 — a fixture built from an assumption cannot falsify it.** `sql/52`
was tested against PostgreSQL 16.13 and reported zero failures. The fixture table
was hand-written from what the schema was *assumed* to be, and included a column
the real table lacks. The test proved the SQL was consistent with an invention.
**Fix: generate fixtures by extracting `CREATE TABLE` text out of the migration
files.** `sql/53` and `sql/54` were tested that way; both runs print the real
column list and assert the absent column before doing anything else.

**Failure 2 — "the latest X" is not "the X that produced this row".** They
coincide only under an assumption nobody stated: one training run per endpoint
per day. Two runs on one date broke it. The query never asked which run wrote the
row, and nothing in the result looked wrong — every fleet returned a plausible
run_id and the comparison came back green.

**Failure 3, the serious one — a confident wrong value is worse than a NULL.**
Before `sql/53`, `run_id` was NULL and the API said *unverified*, which was true.
After `sql/53` it said *matches=true*, which was false. The column looked
healthier and the system became less honest. This codebase has argued all day
that a null reads "not measured" while a stale or wrong number reads as fact —
and then a migration written to improve provenance did precisely the thing being
argued against, within the hour.

**What stopped it:** the AFTER probe printed `run_id=ps1_20260726` where
`ps1_sklearn_20260726` was expected. One field, off by a prefix. Had the script
printed only `matches`, it would have shown `true` and looked like success.
**Print the inputs to a verdict, not only the verdict.**

`sql/54` also adds `v_ps1_serving_gap`, which states the E-1 gap in the DATA
layer so it cannot be erased by a handler bug — which is exactly how it was
erased. And it labels its own limit: `serving_run_id` is the latest *registered*
train run, a proxy. Registration is not deployment; only `DescribeEndpointConfig`
observes what an endpoint actually runs.

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

## 10. ORDER OF WORK — updated 2026-08-11 after measurement

**Done:** both sweeps run (§8) · go-live deployed and applied 05:17Z · every one of
the nine unknowns closed.

1. **Apply `tooling/ps1_provenance_fix.sh`** (`sql/52` + the run_kind fix). This is
   now the only gap between what is committed and what is running, and it is what
   makes `target_col` and `run_id` non-NULL. 15 minutes.
2. **Push the outstanding commits.** Production code living in one place is the
   failure `73df05d` had to repair.
3. **Decide E-1** — measured at 0 invocations over 14 days. Re-register the sklearn
   models and wire inference, or stop the endpoints. Either is defensible; the
   default of paying for three idle endpoints is not.
4. **Decide D-1** — gateway `auth=NONE` with three writable routes. §5.8 bounds the
   blast radius; it does not close it.
5. **Fix `ps1_verify_state.sh` [U9]** to window from the disable timestamp rather
   than a rolling 3 days — see §8.7. A check that manufactures false alarms will be
   ignored, and then it protects nothing.
6. Then the 5 Path B routes, `MAX(computed_date)`, and `ps2-rds-push`'s missing
   source (§8.6).

Once 1–4 are closed, PS1 is done and PS2 gets the same treatment.

---

## 11. THE SERVING CONTRACT — MEASURED 2026-08-15  [M 15-Aug 06:15Z]

Two read-only CloudShell runs closed the last standing unknown about what the
endpoints actually run. Raw artefacts are committed at `tooling/out/`. Full
narrative in `docs/PS1_OPERATIONAL_INVENTORY.md` §14.

### 11.1 There are no ECR endpoints for PS1  [M]

Image on all three: `683313688378.dkr.ecr.us-east-1.amazonaws.com/sagemaker-scikit-learn:1.2-1-cpu-py3`.
Account `683313688378` is **AWS's**, i.e. the managed DLC. `cubic-pdm/mars-ps1`
(2.38 GB) is referenced by 0 of 28 model package groups and 0 endpoints.

**DECISION-1 settled: ECR is not required for PS1.** `docker/Dockerfile.ps1` and
`docker/inference_ps1.py` build a Flask BYOC image that nothing deploys — its
`model_fn` globs `*_champion.joblib`, a filename the real bundle does not contain.

`cubic-pdm/mars-ps3` IS live (model package 14) and pins the **mutable `:latest`
tag**. Real PS3 defect; never sweep it into a PS1 cleanup.

### 11.2 The bundle, and the feature contract  [M]

`model.tar.gz` is byte-identical to `sourcedir.tar.gz` for all three fleets, so
the handler analysed is the handler that runs — a genuine ambiguity, since
`SAGEMAKER_PROGRAM` resolves out of the submit directory for managed containers.

| fleet | features | medians | deployed threshold | classifier |
|---|---|---|---|---|
| GATE | 47 | 47/47 | 0.12284049 | `SparkXGBClassifierModel` |
| TVM | 40 | 40/40 | 0.02648935 | `SparkXGBClassifierModel` |
| VALIDATOR | 40 | 40/40 | 0.39651793 | `SparkXGBClassifierModel` |

14 features are shared by all three; 13/16/14 are fleet-specific. These are three
genuinely different models, not one applied three times.

`requirements.txt` carries `numpy, pandas, xgboost>=2.0` — **unpinned**. A
container restart can install a newer xgboost against the same booster JSON.

### 11.3 E-1 QUANTIFIED — the deployed model matches NO scorecard row  [M]

`v_ps1_serving_gap` has only ever used a **proxy**. This is the first direct
observation.

| fleet | deployed thr | scorecard v2 (Spark) | scorecard v3-sklearn | deployed n_feat | scorecard v2 |
|---|---|---|---|---|---|
| GATE | 0.122840 | 0.1349 | 0.648467 | 47 | 67 |
| TVM | 0.026489 | 0.0255 | 0.494842 | 40 | 67 |
| VALIDATOR | 0.396518 | 0.4513 | 0.604953 | 40 | 43 |

Lineage is Spark, as `sql/load` warned. But the threshold is only *close* to the
Spark row, and `n_features` matches neither row for any fleet. Three readings are
possible and none is chosen here  [U]: an unloaded Spark run; figures transcribed
from a different checkpoint of the same run; or `n_features` counting candidates
rather than selected features.

**Operational consequence, which is not in doubt:** GATE's endpoint fires at
**0.1228** while `/ps1/threshold-sweep` publishes **0.65**. Every flagged-device
count, alert-rate estimate and capacity figure derived from the published
threshold is wrong — and wrong in the unsafe direction, since a far lower
threshold flags far more devices.

**Still not claimable:** that these endpoints have ever answered an inference
request. `InService` proves `model_fn` ran at container start. With 0 invocations
in 14 days, `input_fn`/`predict_fn` are unexercised in production.

### 11.4 C-3 corrected — an ordering bug, not a missing-median bug  [V]

Medians are **complete** (47/47, 40/40, 40/40). The earlier framing — "features
with no median" — described an empty set and is withdrawn.

The defect survives in sharper form. `_impute` only touches columns already
present, so an **absent** column is skipped and then `reindex(fill_value=0.0)`
sets it to `0.0`, **while its correct median sits unused in the same bundle**.
Cause is ordering; fix is one line:

    X = _impute(df.reindex(columns=cols), cols, medians).astype(float).values

`notebooks/ps1_batch_score_daily.py` implements both this and the DECISION-8
presence assertion.

### 11.5 A bug in the audit tool itself  [V]

`ps1_read_docker_and_model.sh` printed `VERDICT: NO ps1_*_meta.joblib in the
archive` two lines below a member listing containing `ps1_gate_meta.joblib`.

Cause: `set -o pipefail` with `tar -tzf … | grep -q`. `grep -q` exits at the
first match, `tar` takes SIGPIPE and returns 141, `pipefail` propagates it, and
`&& HAS_META=1` never runs. A race — the 68 KB archive won it, the 788 KB one did
not. **It reported absence when it meant "I stopped looking", in the script
written to detect exactly that.** Fixed in `tooling/ps1_feature_contract.sh` by
listing once into a variable. Recorded as failure pattern 16 in the skill.
