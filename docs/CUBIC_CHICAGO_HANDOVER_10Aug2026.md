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
| 1 | 4 commits unpushed: 6e84cdb, 9f41aaa, 45b4342, a38fda3 | ready |
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
