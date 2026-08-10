# CUBIC MARS Chicago — Operational Handover, 10-Aug-2026

Written because context does not survive between sessions and the memory tools
are disabled on this account. If it is not in this repo, it does not exist for
the next session. Treat this file as the starting point, not anyone's recall.

Provenance tags used throughout:
  [M] MEASURED — verified against AWS, Aurora or the live API on the stated date
  [I] INFERRED — reasoned from code or naming; NOT verified. Verify before acting.

---

## 1. Data vintage — read this first

`silver.device_event_enriched` ends **2026-04-11 17:51:34**; 123,466 rows on
11-Apr, none after. [M 10-Aug]

This is BY DESIGN. Chicago holds a historical dump to 11-Apr-2026. Sequence:
  1. historical dump to 11-Apr-2026   <-- CURRENT STATE
  2. incremental dump; models refresh against it
  3. daily ingestion commences

Do NOT raise the gap as staleness, an incident or a go-live blocker. It looks
like a broken pipeline and is not. Full detail: CUBIC_CHICAGO_DATA_LINEAGE.md
section 10.14.

---

## 2. What is live, as measured 10-Aug-2026

12 Lambdas, 8 cubic EventBridge rules (all ENABLED), 2 HTTP APIs, 4 SageMaker
endpoints, Aurora PostgreSQL 16.4, 216 tables + 87 views (53 empty). [M]

Full machine-readable inventory: `cubic_inventory_v3_20260810T054803Z.json` at
the repo root, and `rds_inventory.csv` (303 objects: name, kind, rows, columns,
primary key). Regenerate with the Phase A collector if stale.

### Schedules [M]
    dim-daily-refresh     05:45   cubic-mars-dim-loader
    ps1-daily-push        06:15   cubic-mars-ps1-rds-push
    ps1-xw-daily-load     06:40   cubic-mars-ps1-xw-loader
    ps2-daily-load        07:10   cubic-mars-ps2-rds-loader
    ps4-daily-load        07:35   cubic-mars-ps4-rds-loader   (moved 10-Aug)
    ps5-daily-load        07:20   cubic-mars-ps5-rds-loader
    ps4-v3-weekly         Mon 08:00
    events-training-daily 02:00   NO TARGET — fires into nothing
    ps4-v3-weekly                 NO TARGET — fires into nothing

**There is no EventBridge rule for any PS3 loader.** All three PS3 loaders are
manual-invoke only. [M]

### SageMaker endpoints [M]
    chicago-ps1-3d-gate-failure-v1        InService
    chicago-ps1-3d-tvm-failure-v1         InService
    chicago-ps1-3d-validator-failure-v1   InService
    chicago-ps3-rootcause-v1              InService  (the OLD v1 model, 13-Jul)
PS2, PS4 and PS5 have NO endpoints — notebook -> S3 -> RDS only.

### PS3 is loaded and current [M 09-Aug]
Run `6a787954-9c03-422b-8f9d-ad7d7efd4f8d`, revision
`source_first_ps1_ps2_ps3_label_aligned_v26`, run_mode PRODUCTION, 20/20 tables,
117,377 rows, single run_id across all status rows.
`is_current_operational_score` is false and that is CORRECT — see section 1.

---

## 3. Defects found and fixed on 10-Aug

### PS1 — four stacked defects, broken since 26-Jul (15 days, silently)
1. **AWSSDKPandas layer missing** from the live function -> `ImportModuleError:
   No module named 'pyarrow'`, dying in 450 ms. FIXED live.
2. **GOLD_KEY pointed at a CSV** (`chicago/gold/ps1_staging/…​.csv`) while the
   handler only calls `pq.read_table`. Magic bytes: CSV starts `"DEV`, the
   Parquet at the deploy.sh default starts `PAR1` and is 2 days newer. FIXED.
3. **deploy.sh could never resolve the layer** — it used `list-layer-versions`,
   which needs `lambda:ListLayerVersions` on another account's public layer and
   is denied. Every other loader uses `get-layer-version-by-arn`, which works.
   So the layer broke and the only script that could restore it aborted first.
   FIXED in commit a38fda3.
4. **Serial dedup keyed on (device, serial, DAY)** while
   `ps1_serial_predictions_pkey` is `(city_id, run_id, device_id,
   matched_serial_nbr)` — no date. A daily multi-date file therefore always
   violated the key. FIXED in 45b4342; collapses to each device's latest
   prediction_date. Tested against 6 shapes.

PS1 completed a full load 10-Aug: 45,681 device-day + 1,897 serial rows. [M]

### PS4 — schedule collision
`ps2-daily-load` and `ps4-daily-load` both fired at **07:10**. PS2 succeeded;
PS4 connected to Aurora then blocked for the full 900 s timeout, 21/21 failures
over 7 days. PS4 moved to 07:35. **The lock-contention explanation is [I], NOT
confirmed** — a manual invoke while PS2 is idle would settle it in <=15 min.

---

## 4. Open items

| # | Item | State |
|---|------|-------|
| 1 | 11 commits unpushed, 6e84cdb..HEAD (was 4 when this row was written; 7 more landed later on 10-Aug) | ready |
| 2 | PS4 timeout — reschedule applied, cause unconfirmed | needs test |
| 3 | `MAX(computed_date)` hides categories (see 5.3) | needs decision |
| 4 | PS2 notebook still writes `ps2_outputs`, loader reads `chicago/ps2_outputs` | blocks #5 |
| 5 | 1,067 dead S3 objects + 4 orphan PS3 replay runs | blocked by #4 |
| 6 | PS3 canonical lineage: Source-First V26 vs the 04-Aug numbered series | PK decision |
| 7 | PS4 clustering uses `fit_predict` — cluster IDs unstable run to run | needs fix before any endpoint |
| 8 | `cubic-mars-ps2-rds-push` deployed with NO source in this repo | gap |
| 9 | `cubic-mars-ps3-inference` in repo, NOT deployed | relevant to PS3 endpoint work |
| 10 | PS1 notebook cell 24 needs `partition_cols=['device_category']` | root cause of 5.3 |
| 11 | `is_current_operational_score` hardcoded False in 3 places | fix before stage 2 |
| 12 | PS1 loader not transactional — no BEGIN/COMMIT, pg8000 autocommits | partial loads possible |
| 13 | `n_devices_scored` 470 vs `n_flagged` 176,644 in ps1_inference_runs | at least one is wrong |
| 14 | `appdb` vs `postgres` — ps2-rds-push uses appdb, everything else postgres | unexplained |
| 15 | System reference document, items 1-6 | items 2,3,4 data captured |
| 16 | **PS1 scorecard retirement + causation fix: code committed, NOT applied to Aurora and NOT deployed. See §9.** `/ps1/summary` is still serving the 13-Jul seed and `v_ps1_xw_causation` still omits GATE until `tooling/ps1_retire_apply.sh` is run with `APPLY=1` | ready to apply |
| 17 | `ps1_leaderboard` model rows are still the 13-Jul seed. §9 repointed only the recall-floor SOURCE, not the leaderboard itself | needs decision |

---

## 5. Recurring failure patterns — check these first

### 5.1 Replace-not-update
These AWS calls REPLACE the whole field. Anything not restated is erased:
  - `lambda update-function-configuration --environment`  (bit us 08-Aug)
  - `lambda update-function-configuration --layers`       (bit us 26-Jul, cost 15 days)
  - `iam put-role-policy`
  - `events put-rule`                                     (state + description)
Always read live, merge, write. The 11 deploy.sh scripts carry an additive
environment merge block; `--layers` has no equivalent guard yet.

### 5.2 A comment is not evidence of live state
Twice today a committed comment described a fix that had never been executed:
the PS3 notebook trigger gate, and the 26-Jul layer-resolution "fix". Both hid a
live defect for weeks. **A fix is not done until it has run against the account.**

### 5.3 Categories hide each other via MAX(computed_date)
`78c7118` stopped per-category loads DELETING each other. Nothing stops them
HIDING each other: two routes filter `ps1_failure_predictions` on
`(SELECT MAX(computed_date) …)` — `/ps1/crosstab` and `/ps1/device-360` line 685
— so only the most recently loaded category is visible. Root cause is the PS1
notebook writing one category per object (item 10).

### 5.4 Two independent PS1 paths — do not conflate them
  A: gold single object -> `ps1-rds-push` -> `ps1_failure_predictions`
     [M] TVM 45,681 + VALIDATOR 1,922. NO GATE, ever.
  B: artifacts, 3 per-fleet objects -> `ps1-xw-loader` -> `ps1_cross_wired_daily`
     [M] 786,525 rows, all three fleets. This is what the dashboard reads via
     `v_ps1_predictions_xw`.
Most path-A tables are marked `superseded_by` views over path B. A statement
about "PS1 data" must name the path or it will be wrong.

### 5.5 Never deploy from an unpacked zip
`~/cubic_deploy` in CloudShell holds handler.py at 15,025 bytes = commit
`e0480c1`, which PREDATES `78c7118` (the cross-fleet DELETE fix). Deploying from
it would silently reintroduce a bug that wipes two fleets daily. Renamed to
`STALE_cubic_deploy_DO_NOT_USE_predates_78c7118`. Deploy only from a git clone,
where `git log --oneline -1` can tell you what you have.

### 5.6 Device-bridge git limits
`unlink()` is not permitted, so git leaves stale `.git/index.lock` and
`.git/HEAD.lock` it cannot clear. Move them aside (`mv` works) rather than
retrying. `git checkout --` fails; use `git show HEAD:<path> > <path>`.

---

## 6. Verification rules

1. Dry run, or a read-only equivalent, BEFORE anything that writes. The PS1
   handler supports `{"dry_run": true}`; the PS3 loader the same. On 10-Aug an
   un-dry invoke committed a partial run and changed the dashboard.
2. Label every claim [M] or [I]. Inference stated as fact cost hours today —
   the IAM fence, the git tag, the replay prefix, "GATE isn't there at all".
3. Check a table's primary key before comparing a count to it.
4. Compare blob hashes, not `git status` letters, when the bridge is involved.
5. `git diff --ignore-cr-at-eol --numstat` honours the flag; `--name-only`
   does NOT.
6. When a claim is challenged, re-measure before answering.

---

## 7. Where the evidence lives

  cubic_inventory_v3_20260810T054803Z.json   full AWS inventory [M 10-Aug]
  rds_inventory.csv                          303 Aurora objects w/ rows+schema
  docs/CUBIC_CHICAGO_DATA_LINEAGE.md         S3 -> Lambda -> RDS -> dashboard
  docs/RUNBOOK_data_corruption_fixes_08Aug2026.md
  ~/ps3_repoint_20260809T135905Z/            PS3 repoint rollback bundle
  ~/phase_c_20260810T062725Z/                versioning + duplicate audit
  ~/ps1_env_backup.json                      PS1 env before the GOLD_KEY change
  ~/ps4_rule_backup.json                     PS4 rule before the reschedule

All datalake buckets are VERSIONED [M 10-Aug] — a delete leaves a marker and is
restorable. That is the safety net for the S3 cleanup, but do not rely on
version archaeology as the recovery plan: copy to an archive prefix first.

---

## 8. PATH B DISABLED FOR PS1 -- 10-Aug-2026 11:55Z

`cubic-mars-ps1-rds-push` and everything it feeds is retired. PS1 now runs on
PATH A only (artifacts -> ps1-xw-loader -> ps1_cross_wired_daily -> the xw-*
routes and v_ps1_predictions_xw).

WHY, measured not asserted. Path B never once delivered all three fleets. Its
entire load history, from ml_batch_load_audit:

    ps1_20260726   ps1_failure_predictions   9,260 read ->  4,217 loaded
    ps1_demo_v2    ps1_failure_predictions   9,260 read ->  4,217 loaded
    ps1_20260810   ps1_failure_predictions 184,386 read -> 45,681 loaded
    ps1_20260810   ps1_serial_predictions  184,386 read ->  1,897 loaded

Four PS1 loads ever. One of them is named `ps1_demo_v2`. ps1_serial_predictions
has exactly ONE audited load in its history. GATE has NEVER had a prediction row
in that table. A complete Path B would hold 357,727 rows (GATE 80,720 + TVM
45,681 + VALIDATOR 231,326); it holds 47,603, which is 13%.

Root cause: all three PS1 notebooks write the SAME gold key
chicago/gold/device_ps1_cross_wired_daily, so each fleet overwrites the last. No
PS1 notebook uses partition_cols. Path A works because it writes one key per
fleet.

WHAT WAS CHANGED (both reversible, nothing deleted):
  1. put-function-concurrency --reserved-concurrent-executions 0
     Blocks BOTH invocation paths at once: the 06:15 cron AND the S3
     ObjectCreated trigger on chicago/gold/device_ps1_cross_wired.
  2. disable-rule cubic-mars-ps1-daily-push

WHAT WAS DELIBERATELY NOT CHANGED:
  * The S3 bucket notification on the gold bucket. Left ARMED on purpose. If a
    notebook still writes to that prefix, S3 will try to invoke and fail loudly
    as a throttle, which is a SIGNAL that something still feeds the dead path.
    Removing the notification would make the same event silent. It is also the
    only entry on that bucket, so removing it would have been safe: this was a
    choice, not a constraint.
  * The three tables. ps1_failure_predictions (47,603), ps1_serial_predictions
    (6,004) and ps1_inference_runs (7) keep every row. They stop changing.

RESTORE, if ever needed. Concurrency was 1, NOT unset. Restore to 1; do NOT use
delete-function-concurrency, which removes the reservation entirely:

    aws lambda put-function-concurrency --function-name cubic-mars-ps1-rds-push \
      --reserved-concurrent-executions 1 --region us-east-1
    aws events enable-rule --name cubic-mars-ps1-daily-push --region us-east-1

Snapshot and UNDO.txt: ~/pathB_disable_20260810T115519Z/ in CloudShell.

STILL OPEN. Five routes still read the now-frozen Path B tables and will serve
13%-complete data indefinitely: /ps1/crosstab, /ps1/device-360 (its PS1 block,
handler.py:685), /ps1/serial-predictions, /ps1/coverage, /ps1/runs. They need
repointing to Path A or retiring. Not done here.

---

## 9. PS1 SCORECARD RETIREMENT + CAUSATION PANEL -- WRITTEN AND TESTED, NOT YET APPLIED -- 10-Aug-2026

> ### STATUS AS OF THIS COMMIT: NOT DEPLOYED. NOT APPLIED. NOTHING IN AURORA HAS CHANGED.
>
> | | State right now |
> |---|---|
> | Code committed to `feat/dashboard-v2` | YES |
> | Tested against PostgreSQL 16.13 with production table shapes | YES |
> | Lambda `cubic-mars-dashboard-api` updated | **NO** -- still running the previous package |
> | `sql/50` applied to Aurora | **NO** -- `v_ps1_xw_causation` still omits GATE |
> | `sql/51` applied to Aurora | **NO** -- `ps1_failure_summary` carries no retirement comment |
> | `/ps1/summary` in production | **STILL SERVING THE 13-JUL SEED** |
>
> Everything in 9.2 describes what the committed code WILL do once
> `tooling/ps1_retire_apply.sh` is run with `APPLY=1`. Until then the production
> defects described in 9.1 are all still live. The only place any of this
> behaviour exists is the throwaway PostgreSQL container described in 9.4.
>
> **When this is applied, replace this block with the run's date, the
> `apply_sql` applied/tolerated/failed counts, and the AFTER route output.**
> A section that still says NOT APPLIED after it has been applied is the same
> defect this section exists to correct, pointing the other way.

The test evidence in 9.4 was executed against a real PostgreSQL 16.13 with the
production table shapes before it was written down. Nothing in this section is
inferred from reading code. That is a statement about the TESTING, not about
production -- see the status block above.

### 9.1 What is wrong -- STILL LIVE IN PRODUCTION AS OF THIS COMMIT

**`/ps1/summary` serves a hand-typed 13-Jul seed as the current state of PS1.**

`ps1_failure_summary` holds two rows, TVM and Gates, typed out of a console log
by `sql/04` (the INSERT since moved to `sql/manual/`). They say
`quality_gate = FAIL`, `promoted = false`, and `target = 'will_fail_3d'` -- a
label name that exists nowhere in this system. The trained target is
`will_hardware_oos_3d`. VALIDATOR is absent from the table entirely.

No loader has ever written to it. There is no refresh path, so it can only age.
Meanwhile `ps1_model_performance` -- populated 26-Jul by the sklearn run through
`sql/load/` -- holds all three fleets, PASS, promoted, and the correct target
column, and `/ps1/summary` does not read it.

**`v_ps1_xw_causation` deletes GATE from the panel rather than reporting it.**

`sql/34` ends the view with `WHERE n_chain >= 30 AND n_no_chain >= 30`. The
intent is right: a lift ratio built from a handful of device-days is noise
wearing a decimal point. The implementation is not. A WHERE clause does not say
"not enough data" -- it says nothing at all. GATE vanishes, and a reader
concludes PS1 has no GATE model or that GATE was never cross-wired. Both false.
GATE carries its full share of the 786,525 device-days in
`ps1_cross_wired_daily`. What it lacks is enough days on BOTH sides of the split.

Silence and insufficiency look identical on a dashboard. They are not the same
finding, and the panel must not render them the same way.

### 9.2 What the committed code changes -- TAKES EFFECT ONLY ON APPLY

| File | Change, once applied |
|---|---|
| `sql/50_ps1_xw_causation_all_fleets.sql` | One row per fleet ALWAYS, with `sufficient_data` (bool) and `min_cell` appended. The three rate columns become NULL when the 30-per-cell bar is not cleared, so no number appears that the data did not earn. |
| `sql/51_ps1_failure_summary_retire.sql` | `COMMENT ON TABLE/COLUMN` marking `ps1_failure_summary` retired, so the fact survives in the catalog and not only in `handler.py`. Corrects `v_ps1_table_status`, which maps it to `v_ps1_xw_summary` -- wrong, that is a row-count reconciliation with no model metric in it. Also corrects two rows that went stale on 26-Jul when `sql/load/` populated `ps1_model_performance` (0->3) and `ps1_feature_importance` (0->45). |
| `handler.py` `/ps1/summary` | Will read `ps1_model_performance` + `ps1_confusion`. Legacy column contract preserved exactly (`api.js:apiPS1Summary` and the legacy tab both read those names). |
| `handler.py` `/ps1/leaderboard` | The recall-floor lookup -- the SECOND reader of the retired table -- will read `ps1_model_performance`. |
| `handler.py` `/ps1/model-performance` | Will serve `target_col`, `label_revision`, `recall_floor`, `recall_floor_met`, `base_rate_pct_recorded`, `run_id`. `sql/16` added these five columns in July and nothing has ever returned them. |
| `handler.py` `/ps1/xw-causation` | `ORDER BY sufficient_data DESC, critical_lift DESC NULLS LAST, min_cell DESC` -- insufficient fleets will sort last with their shortfall visible instead of being buried among genuine low-lift fleets. |
| `handler.py` `_PS1_DISPLAY` / `_PS1_CATEGORY` | One map for the fleet-naming split. The 13-Jul tables spell fleets as display names (`Gates`); every table written by a notebook since spells them as codes (`GATE`). Nothing reconciled the two, which is why the coverage note in `PS1FailurePredictionTab.jsx` matches on the first three characters. |

**The accuracy and operating point will be derived, not asserted.**
`ps1_confusion` was computed AT `decision_threshold`, so precision and recall
read off it ARE the operating precision and recall. `/ps1/summary` derives
`test_accuracy`, `op_precision`, `op_recall`, `op_f2`, `op_fleet_pct`, `n_test`,
`n_test_pos` and `base_rate_pct` from the run's own TP/FP/TN/FN.

**Returned NULL on purpose:** `brier_raw`, `brier_cal`, `auc_cal`, `prec_at_k`,
`rec_at_k`, `lift_at_k`, `map_score`, `sm_registered`, `overfit_flag`,
`n_train`. The run that wrote `ps1_model_performance` did not measure them. They
are NOT back-filled from the retired row. A null reads as "not measured"; a
stale number reads as fact.

> **PS1 has NO served overfit signal -- before or after this change.**
> `/ps1/model-performance` deliberately withholds train/val AUC (GATE reported
> train 1.0000 / val 1.0000 -- drawing that beside held-out numbers reads as
> quality when it is memorisation), and the boolean that carried the signal
> lives on the table being retired. A comment in `handler.py` claimed the flag
> was "still served on /ps1/summary"; that claim would become false the moment
> the route moves, and it has been corrected in the same commit. The fix is for
> the notebook to write the flag, not for the route to infer one.

### 9.3 Reversibility

Nothing is dropped, deleted or altered. `ps1_failure_summary` keeps its two rows.

1. `/ps1/summary` FALLS BACK to the retired table when `ps1_model_performance`
   has no row for a city, and says so in the `source_table` field of every row
   it returns. No city that has not been re-run yet goes dark. Deleting the mp
   branch restores the 13-Jul behaviour byte for byte. Tested: emptying
   `ps1_model_performance` returns the two legacy rows carrying the retirement
   note, and the leaderboard floors fall back with them.
2. `sql/50` rollback: `DROP VIEW v_ps1_xw_causation;` then re-apply `sql/34`.
   `CREATE OR REPLACE` alone will NOT work -- verified, PostgreSQL raises
   `42P16 cannot drop columns from view`. The DROP is required and is written
   into the file's rollback footer.
3. `sql/51` rollback: three `COMMENT ... IS NULL` / `DROP VIEW` statements, then
   re-apply `sql/36`. Neither file writes a row, so that is the entire undo.
4. Both `DROP VIEW` statements are NOT `CASCADE`. If something has come to
   depend on either view the DROP fails loudly and the `CREATE OR REPLACE`
   behind it still applies. A CASCADE would have removed the dependent object
   silently -- the exact class of damage these files argue against.
5. Lambda code rollback: `ps1_retire_apply.sh` downloads the currently deployed
   package to `~/ps1_retire_backup/` BEFORE it swaps anything. Restoring is one
   `update-function-code` with that zip.

### 9.4 Test evidence -- LOCAL POSTGRES, NOT AURORA

PostgreSQL 16.13 in a throwaway container, seeded with the production table
shapes and fixture rows. `handler.py`'s own `split_sql()` was used to split the
files exactly as `apply_sql()` will split them. This is evidence that the code
is correct. It is NOT evidence about the state of production.

```
sql/50   4 statements, all parse (pglast) and execute; idempotent on re-apply
         column prefix preserved: True  (11 sql/34 columns unchanged, 2 appended)
         GATE   n_chain=6    lift=None  sufficient=False  min_cell=6   <- appears
         TVM    n_chain=100  lift=1.0   sufficient=True   min_cell=100
         VALID  n_chain=100  lift=1.0   sufficient=True   min_cell=100

sql/51   6 statements, all parse and execute; idempotent on re-apply
         column prefix preserved: True  (4 sql/36 columns unchanged, 1 appended)
         ps1_failure_summary -> superseded_by=ps1_model_performance, retired=true

/ps1/summary            3 rows, all fleets, PASS, promoted,
                        target=will_hardware_oos_3d
                        accuracy + operating point derived from ps1_confusion
/ps1/leaderboard        floors resolve across the naming split (Gates -> GATE)
/ps1/model-performance  target_col, label_revision, recall_floor,
                        recall_floor_met, base_rate_pct_recorded, run_id present
/ps1/xw-causation       3 fleets, insufficient one sorted last, min_cell visible
```

The fixture numbers above (n_chain=6/100/100) are FIXTURE values chosen to force
one fleet below the bar. They are NOT Chicago's real counts. GATE's real
`min_cell` is unknown until `sql/50` is applied and the route is read -- that
figure is one of the things the apply run is for.

The trailing comment-only block in each `.sql` file becomes its own statement
under `split_sql()`. Confirmed it executes cleanly under pg8000 rather than
counting as a failure -- checked because `apply_sql()` reports per-statement
failures and a spurious one would have looked like a real error.

### 9.5 How to apply it

`tooling/ps1_retire_apply.sh` (CloudShell). Dry-run by default; `APPLY=1` to
execute. It deliberately does NOT use `deploy.sh`, which runs `migrate()`
unconditionally and would re-apply `sql/08` (re-seeds hardcoded PS2 rows for
2026-07-14) and `sql/18` (DELETEs from `ps3_severity_predictions`). Retiring a
PS1 table must not put PS2 or PS3 data at risk.

**Order matters, and there is a window.** The script swaps the Lambda code
FIRST, then dry-runs, then stops. Between the code swap and `sql/50` being
applied, `/ps1/xw-causation` WILL ERROR -- the new route selects
`sufficient_data` and `min_cell`, which the old view does not have. That window
lasts as long as you take to read the dry-run output. If that is not acceptable,
apply `sql/50` and `sql/51` to the CURRENT deployment first (both are additive
and the old route ignores the new columns), then swap the code.

### 9.6 Open, not fixed here

* `ps1_leaderboard` is still the 13-Jul seed. Only its FLOOR SOURCE was
  repointed; the model rows themselves are unchanged and still stale.
* `/ps1/calibration`, `/ps1/explainability`, `/ps1/features` remain empty.
* The legacy `PS1FailurePredictionTab` is still routed and still reads
  `/ps1/summary`. It will receive live data after apply, but renders columns
  (Brier, top-k, n_train) that are legitimately null and will show as blanks
  until that tab is retired or reworked.
* VALIDATOR has no `recall_floor`. `sql/16` documents the policy as TVM 0.80 /
  GATE 0.70 and names none for VALIDATOR, so it is left NULL rather than
  invented. Parked by decision on 10-Aug, not an oversight.
