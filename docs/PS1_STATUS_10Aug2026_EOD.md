# PS1 — COMPLETE STATUS, end of 10-Aug-2026

Supersedes `PS1_COMPLETE_AUDIT_10Aug2026.md` (written this morning) wherever the
two disagree. That document is still valid as the morning snapshot; this one
records what changed after it and what is true now.

---

## 0. HOW TO READ THIS — evidence tags

Every factual line carries one. Nothing is stated without one.

| Tag | Means | Can I prove it right now? |
|---|---|---|
| **[V]** | Verified first-hand in this session — read from the pushed repo, or produced by executing the code | **Yes** |
| **[M 05:48Z]** | Measured from the live AWS inventory captured today at 05:48Z (`cubic_inventory_v3_20260810T054803Z.json`) | Yes, but it is a 05:48Z snapshot |
| **[M am]** | Measured live this morning against Aurora and the API, recorded in the morning audit | No — I am trusting this morning's measurement |
| **[U]** | **UNKNOWN.** Cannot be checked without CloudShell | **No** |

**The single most important thing on this page:** the 05:48Z inventory predates
three sets of changes made later the same day — the Path B repair, the Path B
disable at 11:55Z, and the afternoon dashboard-api redeploy. So for anything
touching `cubic-mars-ps1-rds-push` or `cubic-mars-dashboard-api`, the inventory
is **stale by design** and the current value is **[U]**. Section 8 is a
read-only script that turns every [U] on this page into a measurement.

---

## 1. BOTTOM LINE

PS1 runs on **two independent paths from the same notebooks**. As of tonight:

| | Path A — cross-wired | Path B — legacy predictions |
|---|---|---|
| Loader | `cubic-mars-ps1-xw-loader` | `cubic-mars-ps1-rds-push` |
| Status | **LIVE and healthy** [M am] | **DELIBERATELY DISABLED 11:55Z** [M am] |
| Rows | 786,525, all three fleets [M am] | 47,603 + 6,004 + 7, frozen [M am] |
| Completeness | 100% vs the loader's own EXPECTED | 13% — never held all three fleets |
| Dashboard | ~20 `xw-*` routes, all three fleets | 5 routes still read its frozen tables |

**PS1 is serving correct data on Path A and stale-but-frozen data on five Path B
routes.** No PS1 route is down. The model tier — three SageMaker endpoints — is
InService and untouched.

Today moved PS1 from *four stacked defects, broken 15 days silently* to *one
healthy path, one deliberately-frozen path, and a written list of what is left*.
Nothing from this afternoon's work has been applied to Aurora yet.

---

## 2. THE TWO PATHS — why PS1 has two of everything

Both paths originate in the **same three notebooks**. The split is in what gets
exported and who loads it. [V — read from both loaders' source]

```
  PS1_3d_{TVM,GATE,VALIDATOR}_SageMaker_MLflow_FeatureStore.ipynb
                    |
        +-----------+------------------------+
        |                                    |
   PATH A (live)                        PATH B (frozen)
   artifacts bucket                     gold bucket
   chicago/device_ps1_cross_wired_daily/   chicago/gold/ps1_staging/...csv
   3 objects, one per fleet             1 object
        |                                    |
   cubic-mars-ps1-xw-loader             cubic-mars-ps1-rds-push
   cron(40 6) ENABLED                   cron(15 6) DISABLED 11:55Z
   BEGIN/SAVEPOINT/COMMIT               no transaction, pg8000 autocommit
        |                                    |
   ps1_cross_wired_daily                ps1_failure_predictions
   786,525 rows, 3 fleets               ps1_serial_predictions
        |                                ps1_inference_runs
   ~20 xw-* routes                       |
   ALL THREE FLEETS                     5 routes, 13% complete, FROZEN
```

**Why Path B was abandoned, in one line:** in its entire life it performed 4
loads, one of them named `ps1_demo_v2`, and it never once held all three fleets
simultaneously — it holds zero GATE rows. [M am]

---

## 3. CURRENT STATE, COMPONENT BY COMPONENT

### 3.1 S3 [M 05:48Z]

| Bucket | Prefix | Objects | Bytes | Newest |
|---|---|---:|---:|---|
| artifacts | `chicago/device_ps1_cross_wired_daily/` | **3** | 7,482,535 | **2026-07-29 04:25Z** |
| gold | `chicago/gold/` | 115 | 1,806,929,156 | 2026-07-28 07:06Z |

Three objects = one per fleet, matching the loader's one-key-per-fleet read. [V]

> **Finding S-1 — FIXED 10-Aug, commit `6b78b20`.** The loader now reports a
> per-fleet freshness verdict (`UNCHANGED` / `CHANGED` / `NO_PRIOR_LOAD` /
> `UNREADABLE`) by comparing each object's ETag against the last successful
> load recorded in `ml_batch_load_audit`, and writes the lineage row it never
> wrote before. New read-only action: `{"action":"freshness"}`. Original
> finding, for the record:
>
> **The "daily" PS1 load has had nothing new to load since 29-Jul.**
> The source objects are static. The 06:40 cron re-reads the same three files
> every morning and rewrites the same 786,525 rows. This is **correct given the
> 11-Apr-2026 data cutoff**, not a fault — but it means a green daily run proves
> only that the loader works, *not* that data is flowing. Do not read the cron
> succeeding as evidence of freshness. Reassess when the incremental dump lands.

`chicago/gold/ps1_staging/device_ps1_cross_wired_daily.csv` is the stale Path B
CSV (morning audit 2.10). Still present. Retire it — but **only after** §5
Finding L-2 is closed, because the S3 ObjectCreated trigger on that prefix is
still armed. [M am / V]

### 3.2 Git [V — all read from the pushed repo]

Branch `feat/dashboard-v2`, HEAD `9222ccd`, **fully pushed**, `fsck` clean.

**Folders**

```
api/lambda/cubic-mars-ps1-xw-loader/     handler.py deploy.sh requirements.txt
api/lambda/cubic-mars-ps1-rds-push/      handler.py deploy.sh requirements.txt
                                         README.md test_handler.py
notebooks/ps1_failure_prediction/        4 live notebooks + 12 archived
api/lambda/cubic-mars-dashboard-api/     handler.py + sql/ (52 files) + sql/load/
```

**The six PS1 commits made today**, oldest first:

| Commit | What it fixes |
|---|---|
| `45b4342` | Serial dedup keyed on (device, serial, **day**) while the PK has no date — every multi-date file violated the key |
| `a38fda3` | `deploy.sh` used `list-layer-versions`, denied on another account's public layer, so the only script that could restore the layer aborted first |
| `0ad24e8` | The morning audit (389 lines) + Path B disable record |
| `73df05d` | The **deployed** xw-loader — 11,521 bytes of production code that existed in no repository |
| `f52b7fb` | Fleet-blind top-N: `ORDER BY prob DESC LIMIT n` across all fleets returned 500 VALIDATOR rows and zero TVM/GATE |
| `a039b84` | Unique index on the cross-wired grain + `apply_sql` |
| `9222ccd` | Scorecard retirement + causation panel (**committed, NOT applied**) |

> **Finding G-1 — `cubic-mars-ps1-xw-loader/handler.py` was production code with no
> source of record until today.** The deployed function had drifted 11,521 bytes
> ahead of the repo. It is now committed at md5 `a51fae2746bc4e36b58ab81bcbd5936b`,
> 28,028 bytes, matching the deployed artefact. [V]
> **Nothing guarantees it stays matched.** There is no CI check comparing
> deployed code to the repo — see Finding G-2.

> **Finding G-2 — nothing detects loader drift.** G-1 went unnoticed for weeks.
> Every other loader has the same exposure and none has been checked.
> **Fix:** a scheduled job that downloads each function's package and diffs
> `handler.py` against the repo, failing loudly on mismatch. Not built.

### 3.3 SageMaker endpoints [M 05:48Z]

| Endpoint | Status | Last modified |
|---|---|---|
| `chicago-ps1-3d-tvm-failure-v1` | InService | 2026-07-24 07:46Z |
| `chicago-ps1-3d-gate-failure-v1` | InService | 2026-07-24 07:53Z |
| `chicago-ps1-3d-validator-failure-v1` | InService | 2026-07-24 09:33Z |

All three fleets have a live endpoint. Untouched today.

> **Finding E-1 — the endpoints are InService and, as far as this repo shows,
> nothing invokes them.** Every PS1 number on the dashboard comes from batch
> notebook output loaded into Aurora, not from these endpoints. Three InService
> endpoints have been billing since 24-Jul for no traffic I can find.
> **This is [U], not a conclusion** — I searched the repo, not CloudWatch.
> §8 counts their invocations. If it is zero, the decision is real money:
> keep them for the go-live inference story, or stop them until PS1 actually
> serves real-time.

### 3.4 Lambdas [M 05:48Z — and stale for `ps1-rds-push`, see below]

**`cubic-mars-ps1-xw-loader`** — Path A, healthy

```
runtime python3.12   mem 3008   timeout 900   modified 2026-07-28T15:35Z
layer  AWSSDKPandas-Python312:29
env    ARTIFACT_BUCKET = ...-artifacts-170202974600
       PS1_XW_PREFIX   = chicago/device_ps1_cross_wired_daily
       CITY_ID = CHI   RDS_HOST = ...aurora-dev...   RDS_SECRET_ID = ...
IAM    GetObject on  artifacts/chicago/device_ps1_cross_wired_daily/*
       ListBucket on artifacts (prefix-conditioned)
VPC    3 subnets
```

Actions: `dry_run`, `load`, `verify`, `audit`, `reload_source`, `copy_rows`,
`csv_cell`. Transactional: `BEGIN` / per-fleet `SAVEPOINT` / `COMMIT`, `ROLLBACK`
on failure, and it **refuses to delete on an empty shape** — *"0 rows shaped,
refusing to delete existing rows for an empty load"*. DELETE is fleet-scoped.
It carries its own contract: `EXPECTED = {gate 107110, tvm 184483,
validator 494932, total 786525}`. [V — read from the committed source]

**This is the reference implementation. Every other loader should look like it.**

**`cubic-mars-ps1-rds-push`** — Path B, disabled

At 05:48Z: `layers=(none)`, `GOLD_KEY=chicago/gold/ps1_staging/…​.csv`,
LastModified **2026-07-26** — i.e. neither of the morning's two fixes had been
applied yet at that moment. [M 05:48Z]
The morning audit records both as FIXED and a successful 45,681-row load
afterwards [M am], so the repairs landed between 05:48Z and the load, and the
function was then throttled to reserved concurrency 0 with its rule disabled at
11:55Z. **Current values are [U]** — §8 measures them.

> **Finding L-1 — the repo's `deploy.sh` and the live function disagree on
> `GOLD_KEY`.** Repo says `chicago/gold/device_ps1_cross_wired_daily` (Parquet);
> live at 05:48Z said `chicago/gold/ps1_staging/…​.csv`. The handler only calls
> `pq.read_table`, so the CSV value is the defect that killed Path B for 15 days.
> [V for the repo value, M 05:48Z for the live value]

> **Finding L-2 — `cubic-mars-ps1-rds-push/deploy.sh` will RESURRECT Path B.
> This is the most dangerous thing in PS1 tonight.** [V — read from the file]
> ```
> line 167-168  aws events put-rule --name cubic-mars-ps1-daily-push \
>                 --schedule-expression "cron(15 6 * * ? *)" --state ENABLED
> line 195      aws s3api put-bucket-notification-configuration --bucket $GOLD_BUCKET
> ```
> Anyone who runs that script — to fix the layer, to redeploy, for any reason —
> silently re-enables the cron rule that was deliberately disabled today and
> re-arms the S3 trigger. Reserved concurrency 0 would still throttle the
> invocations, so the damage is bounded, **but the console would show the rule
> ENABLED and the disable would look undone.**
> **Fix (10 minutes):** add a refuse-to-run guard at the top of that `deploy.sh`
> requiring an explicit `PATHB_REVIVE=1`, change `--state ENABLED` to
> `--state DISABLED`, and have it assert reserved concurrency is still 0.
> **Not done. I recommend this is fix #1 tomorrow** — it protects a decision
> already made, costs nothing, and the window is open right now.

### 3.5 EventBridge [M 05:48Z, both ENABLED at that time]

| Rule | Schedule | Target | State now |
|---|---|---|---|
| `cubic-mars-ps1-xw-daily-load` | `cron(40 6 * * ? *)` | `ps1-xw-loader` | ENABLED [M 05:48Z] |
| `cubic-mars-ps1-daily-push` | `cron(15 6 * * ? *)` | `ps1-rds-push` | **disabled 11:55Z** [M am], now **[U]** |

No collision with PS2/PS4/PS5 (07:10 / 07:35 / 07:20). PS1 at 06:15 and 06:40,
after `dim-daily-refresh` at 05:45 — the ordering is correct. [V]

### 3.6 Aurora [M 05:48Z / M am]

`cubic-mars-rds-aurora-dev`, aurora-postgresql **16.4**, available.
Reachable only through CloudShell via VPC Lambdas.

| Table | Rows | Path | State |
|---|---:|---|---|
| `ps1_cross_wired_daily` | 786,525 | A | healthy, matches EXPECTED exactly [M am] |
| `ps1_model_performance` | 3 | — | loaded today via `sql/load`, was 0 [M am] |
| `ps1_feature_importance` | 45 | — | loaded today via `sql/load`, was 0 [M am] |
| `ps1_failure_predictions` | 47,603 | B | frozen 11:55Z [M am] |
| `ps1_serial_predictions` | 6,004 | B | frozen [M am] |
| `ps1_inference_runs` | 7 | B | frozen [M am] |
| `ps1_failure_summary` | 2 | — | **retired in code, not yet in the DB** [V] |
| `ps1_calibration` / `ps1_explainability` / `ps1_features` | 0 | — | empty, no S3 source [M am] |

`sql/49`'s unique index on the cross-wired grain was applied today with
`NULLS NOT DISTINCT` — required because `component_serial_nbr` is NULL for 63%
of rows (validators). [M am]

### 3.7 Dashboard [V — counted from the committed handler]

**36 `/ps1/*` routes.** 34 returned 200 this morning; `/ps1/device-360` and
`/ps1/xw-device-drivers` correctly return 400 without `device_id`. [M am]

| Group | Count | Source | State |
|---|---:|---|---|
| `xw-*` cross-wired | ~20 | Path A views | **healthy, all three fleets** [M am] |
| Path B readers | 5 | frozen tables | `crosstab`, `device-360`, `serial-predictions`, `coverage`, `runs` — serve 13%-complete data |
| Empty | 3 | tables with no S3 source | `calibration`, `explainability`, `features` |
| Scorecard | 3 | `summary`, `leaderboard`, `model-performance` | fixed in code, **not applied** |

> **Finding D-1 — both API Gateways have `auth=NONE`.** [M 05:48Z]
> `a9yuqt9j9b` (dashboard-api) and `b1s4xxlddb` (ps5-api-gw), route `$default`,
> `AuthorizationType: NONE`. Anyone with the URL reads every PS1–PS5 number,
> and `apply_sql` / `migrate` / `purge` / `recreate` are reachable as Lambda
> actions rather than HTTP routes — so they are *not* exposed through the open
> gateway, but the data is. **This is a go-live blocker and it is not on any
> PS1 list.** Raising it here because you asked for everything, not because it
> is PS1's to fix.

---

## 4. WHAT IS RIGHT

1. **Path A is complete and exact.** 786,525 rows = the loader's own EXPECTED constant, to the row, all three fleets, `last_day 2026-04-11` matching the programme vintage. [M am]
2. **The xw-loader is correctly engineered** — transactional, fleet-scoped deletes, refuses empty loads, self-declaring contract. [V]
3. **Three SageMaker endpoints InService**, one per fleet. [M 05:48Z]
4. **Schedules do not collide** and run in dependency order. [V + M 05:48Z]
5. **IAM is tightly scoped** — the xw-loader can read exactly one prefix, `ListBucket` is prefix-conditioned. [M 05:48Z]
6. **The fleet-blind top-N bug is fixed and verified live** — 300/60 rows across all fleets, was 500 VALIDATOR and nothing else. [M am]
7. **The cross-wired grain now has a unique index**, with `NULLS NOT DISTINCT`. [M am]
8. **The deployed loader is finally in version control.** [V]
9. **Path B is frozen reversibly** — concurrency was 1 and is restorable; the snapshot and UNDO.txt are in CloudShell. [M am]

---

## 5. WHAT IS WRONG — and how to fix it

Ordered by what I would do first, not by severity alone.

| # | Finding | Evidence | Fix | Effort |
|---|---|---|---|---|
| ~~1~~ | ~~L-2: `ps1-rds-push/deploy.sh` re-enables the Path B rule~~ **FIXED 10-Aug, commit `6b78b20`** — refuses without `PATHB_REVIVE=1` (verified: exits before the first aws call), rule created `DISABLED`, S3 trigger opt-in | [V] | done | done |
| **2** | Scorecard + causation fixes committed but **not applied** — `/ps1/summary` still serves the 13-Jul seed, GATE still absent from causation | [V] | `tooling/ps1_retire_apply.sh` with `APPLY=1` | 30 min |
| ~~3~~ | ~~2.2: the gold export overwrites fleets~~ **REFUTED 10-Aug evening.** Cell 24 already writes one prefix per fleet; nothing in the repo writes the gold key at all. **Do not change the notebooks.** See audit 2.2 correction | [V] | none — the defect does not exist | — |
| **4** | 5 routes read frozen Path B tables and will serve 13%-complete data indefinitely | [M am] | Repoint to Path A views, or retire the routes | half day |
| **5** | **2.3: `MAX(computed_date)` hides fleets** — per-fleet loads at different dates mean only the newest fleet is visible | [M am] | Per-category `MAX` in `/ps1/crosstab` and `/ps1/device-360` | 1 hour |
| **6** | **G-2: no drift detection** between deployed Lambda code and the repo | [V] | Scheduled diff job, all loaders | 2 hours |
| **7** | 3 routes empty — `calibration`, `explainability`, `features` — tables have no S3 source | [M am] | Notebooks must export them, or retire the routes | notebook change |
| **8** | **2.5: `ps1-rds-push` is not transactional** — no BEGIN/COMMIT, pg8000 autocommits, partial loads commit as `success` | [M am] | Copy the xw-loader pattern — **only if Path B is ever revived** | 2 hours |
| **9** | E-1: three endpoints InService with no traffic I can find | [U] | Measure invocations, then decide | 15 min to measure |
| **10** | 2.8: serial grain keeps only the latest date per device | [M am] | Decide: keep, or migrate the PK to include a date | decision |
| **11** | 2.9: `n_devices_scored` 470 vs `n_flagged` 176,644 — at least one is wrong | [M am] | Reconcile in `handler.py` | 1 hour |
| **12** | 2.10: stale `ps1_staging` CSV in S3 | [M 05:48Z] | Delete — **after** #1 | 5 min |
| **13** | 2.11: legacy `PS1FailurePredictionTab` still routed, renders nulls as blanks | [V] | Decide: retire the tab, or rework it | decision |
| **14** | VALIDATOR has no `recall_floor` — gated on nothing | [V] | Parked by your decision 10-Aug | — |
| **15** | D-1: API Gateway `auth=NONE` | [M 05:48Z] | Programme-level, not PS1's to fix | go-live blocker |

> **The "one root cause behind four symptoms" claim is withdrawn.** The morning
> audit tied 2.2, 2.3, 2.6 and 2.7 to a single notebook defect. That defect does
> not exist — cell 24 already partitions by fleet, which is why Path A carries
> all three. The four symptoms are therefore **not** known to share a cause, and
> each needs its own diagnosis.
>
> **NEW OPEN QUESTION, replacing it: what produced Path B's source object?**
> Nothing in this repository writes `chicago/gold/device_ps1_cross_wired_daily`.
> Something did once — the object exists and Path B loaded 47,603 rows from it.
> Until that producer is identified, reviving Path B would load whatever stale
> object is still sitting there, and "why does Path B have zero GATE rows"
> stays unanswered. Not urgent while Path B is disabled; blocking if it is ever
> revived.

---

## 6. WHAT CHANGED TODAY

**Applied live** [M am]: PS1 layer restored · `GOLD_KEY` repointed CSV→Parquet ·
first full PS1 load since 26-Jul (45,681 device-day + 1,897 serial rows) ·
`sql/49` unique index · dashboard-api redeployed by zip-swap · `sql/load`
packaged so `load_run` worked for the first time (`ps1_model_performance` 0→3,
`ps1_feature_importance` 0→45) · fleet-blind top-N fix verified · Path B
disabled at 11:55Z (concurrency 0 + rule disabled).

**Committed, NOT applied** [V]: `sql/50` (causation shows every fleet) ·
`sql/51` (scorecard retirement) · `/ps1/summary`, `/ps1/leaderboard`,
`/ps1/model-performance`, `/ps1/xw-causation` route changes ·
`tooling/ps1_retire_apply.sh`.

**Corrected today** [V]: the morning audit's fix #5 said *"repoint `/ps1/summary`
to `v_ps1_xw_summary`"* — **wrong**, that view is a row-count reconciliation with
no model metric in it. Struck through in place with the correction beside it.

---

## 7. WHAT I COULD NOT VERIFY — [U]

I have no AWS credentials in this session, the device bridge has no network, and
Aurora is CloudShell-only. Everything below is genuinely unknown to me tonight:

1. Current `cubic-mars-ps1-rds-push` config — layer, `GOLD_KEY`, reserved concurrency
2. Current state of `cubic-mars-ps1-daily-push` (disabled at 11:55Z, unverified since)
3. Whether the S3 ObjectCreated trigger on the gold bucket is still armed
4. Current `cubic-mars-dashboard-api` deployed package (redeployed this afternoon)
5. SageMaker invocation counts for the three PS1 endpoints — Finding E-1 hinges on this
6. Live row counts for every table in §3.6 — all are morning figures
7. Whether `recall_floor` / `target_col` are actually populated on the 3 live `ps1_model_performance` rows
8. GATE's real `min_cell` on the causation view — the 6 in my tests was a fixture
9. Whether last night's 06:40 xw cron and 06:15 push cron ran, and with what result

---

## 8. THE SCRIPT THAT CLOSES SECTION 7

`tooling/ps1_verify_state.sh` — read-only, `list`/`describe`/`get` only, nothing
invoked, created, modified or deleted. One run answers all nine.

---

## 9. RECOMMENDED ORDER TOMORROW

1. **Run §8's verification script.** Do not fix anything until §7 is measured — several fixes below depend on facts I do not have.
2. **Fix L-2** (the `deploy.sh` Path B trap). Ten minutes, protects a decision already made.
3. **Apply the scorecard + causation work** via `ps1_retire_apply.sh`, and record the real numbers back into handover §9 replacing the NOT-APPLIED block.
4. **Fix the notebook `partition_cols`** — kills four symptoms at the root.
5. Then the five Path B routes, then `MAX(computed_date)`, then drift detection.

Once 1–5 are closed, PS1 is done and PS2 gets the same treatment.
