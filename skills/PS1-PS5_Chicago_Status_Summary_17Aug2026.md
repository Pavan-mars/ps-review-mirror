# CUBIC MARS Chicago — PS1-PS5 Consolidated Status Summary

**Compiled 17-Aug-2026** from the seven documents PK uploaded (dated 16-Aug-2026):
`PS1_AUDIT`, `PS2_AUDIT`, `PS3_AUDIT`, `PS4_AUDIT`, `PS5_AUDIT`,
`PS_SCHEDULING_AND_ECR_EXIT_DECISIONS`, and `PS1_PS5_RIGHT_WRONG_FIX`.

Evidence tags carried over from the source docs: `[M]` = measured live,
`[R]` = read from repo, `[D]` = documented decision, `[U]` = unverified.
Nothing below is a number I generated — every figure is copied from one of
the seven source files.

---

## 1. PS1 — Failure Prediction (TVM / GATE / VALIDATOR) — everything to know

**Target architecture (PK):** EventBridge fires when daily ingestion + medallion
build (Raw→Bronze→Silver→Gold) completes → SageMaker container runs daily
inference → outputs to S3 → Lambda loaders push to RDS → dashboard serves the
daily refresh.

**Headline:** the bottom half of that chain (S3 → loader → RDS → routes → V4
dashboard) is built, scheduled, and verified. The top half (gold-complete
trigger, daily inference, incremental data) is designed and partially
committed but not live. PS1 has re-loaded the same 29-Jul artifacts every
night since; every number on screen is as-of 2026-04-11.

**Notebooks/jobs** (`notebooks/ps1_failure_prediction/`): three production
trainer/registrar/deployer notebooks (GATE, TVM, VALIDATOR — `PS1_3d_*_SageMaker_MLflow_FeatureStore.ipynb`).
`cross_wired_daily_job.py` is the live Databricks job building the feed the
loader reads. Three NEW components committed 16-Aug but **not deployed**:
`ps1_build_features_daily.py` (daily feature-frame task), `ps1_gold_complete_event.py`
(fires EventBridge PutEvents after verifying gold is non-empty for the as-of
date), `ps1_batch_score_daily.py` (the Processing-job scorer, loads the
endpoint's own model.tar.gz for parity). Critical fact: production notebook
CELL 20 stores the held-out TEST SPLIT and CELL 24 exports exactly that —
"daily" in the table name `device_ps1_cross_wired_daily` names the row GRAIN,
not a cadence. Nothing scores a new day today.

**S3:** Path A (live) = artifacts bucket `chicago/device_ps1_cross_wired_daily/{gate,tvm,validator}`
— objects dated 29-Jul, 786,525 rows total (GATE 107,110 / TVM 184,483 /
VALIDATOR 494,932). The fleet is the S3 object key, not a folder/partition.
Path B (legacy) is frozen in the gold bucket, loader cron disabled 10-Aug.

**SageMaker endpoints:** three real-time endpoints InService — `chicago-ps1-3d-gate-failure-v1`,
`-tvm-failure-v1`, `-validator-failure-v1`. Measured serving contract: GATE 47
features / threshold 0.12284049; TVM 40 features / 0.02648935; VALIDATOR 40
features / 0.39651793 (all SparkXGBClassifierModel). All three run AWS's
managed sklearn DLC (`sagemaker-scikit-learn:1.2-1-cpu-py3`) — **not** a
custom ECR image; `cubic-pdm/mars-ps1` ECR repo (3 images, newest 13-Jul) is
referenced by nothing. **0 invocations in 30 days** on all three (re-measured
twice). **E-1 defect:** deployed thresholds/feature counts match no row of
the published scorecard (dashboard publishes 0.648/0.495/0.605); flagged-device
counts derived from the published threshold are wrong in the unsafe direction.
`xgboost>=2.0` is unpinned in requirements.txt and the bundle template.

**EventBridge:** `ps1-xw-daily-load` cron(40 6 UTC) ENABLED, ran on schedule
16-Aug 06:43Z. `ps1-rds-push` (Path B) DISABLED 10-Aug, reserved concurrency=0
now confirmed live. No gold-complete trigger exists yet — and the team
decided it must be EventBridge PutEvents from Databricks, never an S3 bucket
notification (the bucket's notification document is replace-not-update and
already routes elsewhere).

**Loaders/routes:** `cubic-mars-ps1-xw-loader` is the reference-pattern
loader for the whole program (per-fleet SAVEPOINT, refuses empty shapes,
self-declaring contract, ETag freshness check). 36 `/ps1/*` routes served by
`cubic-mars-dashboard-api`. `/ps1/summary` + `/ps1/model-performance` serve
the real 3-fleet scorecard, all PASS/promoted: GATE AUC 0.8961 / AP 0.9486,
TVM 0.9040 / 0.9835, VALIDATOR 0.9956 / 0.9903. Six routes still read Path
B's frozen tables beside live panels with no on-screen distinction.
ServiceNow integration is a stand-in only (`POST /ps1/servicenow-stage`) —
the real SQS FIFO→ServiceNow pipe has never been built.

**Dashboard:** V4PS1Overview (5 sub-tabs) + V4Device360. Three known display
defects: no data-vintage indicator on the PS1 screen (PS2/PS3 have one);
`/ps1/station-summary` and `/ps1/risk-trend` are 26-Jul HAND-SEEDED tables —
every PS1 headline number and the Estate card render from that seed, not from
the real scorecard; the UI renders model stats from hardcoded constants and
never calls `/ps1/model-performance`.

**Live RDS (17 tables, 840,300 rows, 23 views):** `ps1_cross_wired_daily`
786,525 rows (matches the loader's expected contract exactly); `ps1_failure_predictions`
47,603; `ps1_serial_predictions` 6,004; `ps1_feature_importance` 45 (corrected
from an earlier "empty" note); `ps1_model_performance` 3 rows; three empty
tables (`ps1_calibration`, `ps1_explainability`, `ps1_features`).

**SET W confirmations (16-Aug late):** xw-loader `verify` action ran clean,
contract count 786,525 confirmed in Aurora; S3 objects unchanged since 29-Jul
04:18-04:25Z; endpoints re-confirmed at 0 invocations over a full 30-day
window — this is the deletion-safety evidence for decision D-4.

**Verdict table:** trigger MISSING (designed, uncommitted); daily inference
MISSING (feature-frame task is the one blocker); S3 outputs RIGHT but static
since 29-Jul; Lambda→RDS RIGHT; dashboard refresh PARTIAL (wiring correct,
freshness and the 26-Jul seed panels wrong). **The single prerequisite under
everything: incremental Oracle data (12-Apr→present) has not been ingested.**

---

## 2. PS2 — Cascading Failure — everything to know

**Target:** EventBridge on medallion completion → run the PS2 SageMaker
notebook on a daily schedule → S3 → Lambda loader → RDS → daily dashboard
refresh. PS2 needs a daily RUN, not per-event inference.

**Headline:** same shape as PS1 — S3→loader→RDS→dashboard half is built,
scheduled daily, and verified clean; the notebook itself is manual; no
medallion-complete trigger exists; and there is one live landmine.

**Notebooks:** `PS2_Failure_Patterns_v2_5_4_Union_Minutes.ipynb` is the
current production family (produced the 47-table export the loader serves).
No PS2 notebook has ever run on a schedule — every run to date is manual.

**S3 — THE LANDMINE:** the scheduled loader reads `chicago/ps2_outputs`
(migrated 08-Aug, 709 objects / 16,397,197 bytes, byte-exact copy). The
notebook's export env var `PS2_PRODUCTION_EXPORT_PREFIX` has **not** been
repointed — it still writes to the OLD bare `ps2_outputs` prefix. The next
manual run would land somewhere the scheduled loader no longer reads, and the
dashboard would freeze silently (no error, `computed_date` just stops
moving). Fix is one env var: `PS2_PRODUCTION_EXPORT_PREFIX=chicago/ps2_outputs`,
set BEFORE the next run. As of 16-Aug evening this landmine is still open,
but timeline analysis shows no divergence has happened yet (last notebook
export was 03-Aug, captured by the 08-Aug migration copy).

Also: five `ps2_v25_*_audit` prefixes (category_profile, failure_definition_alignment,
failure_label_summary, ps1_label_parity, ps1_model_performance) are written
every run with no Aurora tables to receive them — that evidence is stranded
in S3 today.

**EventBridge:** `cubic-mars-ps2-daily-load` (loader) cron(10 7 UTC) ENABLED,
ran on schedule 16-Aug 07:11Z. No medallion-complete trigger and no scheduled
notebook execution exist for PS2.

**Loaders/routes:** `cubic-mars-ps2-rds-loader` sweeps 47 tables; last
verified dry run 294,749 rows, 0 refused/errors/skipped. IAM shape differs
from PS3's: `ListBucket` is unconditioned so a repoint LISTS happily and
fails later at first READ — do not assume one loader's failure mode applies
to another. ~18 core `/ps2/*` routes + ~25 v2.5 routes. PS2's screen carries
a StatusBar with vintage disclosure (PS1 lacks this). Cross-PS note:
`ps2_v25_ps1_label_parity` / `ps2_v25_ps1_model_performance` are PS2's own
independent second scoring of PS1 — nobody has ever compared them to
`ps1_model_performance`; disagreement would be signal, not noise.

**Live RDS (72 tables, 334,865 rows, 5 views):** largest single table
`ps2_conditional_prob_serial` 89,990 rows; `ps2_v2_cofailure_clusters` 83,184;
`ps2_v2_repair_effectiveness` 38,395. `/ps2/status` confirms 20/20 expected
tables, coherent=true, computed_date 2026-04-11.

**SET W confirmations:** dry-run reproduced the 08-Aug picture exactly (47
tables/294,749 rows); two computed_date families in one sweep (core cascade =
2026-07-26, ps2_v2/v25 families = 2026-04-11); PS2 data exists ONLY in the
artifacts bucket (both gold-bucket PS2 prefixes are empty — the gold grant is
unused); **new discovery** — Lambda `cubic-mars-ps2-rds-push` exists live
(deployed 25-Jul, 512 MB) and appears in NO audit or lineage doc — role
unverified, needs investigation or retirement (parallels the already-retired
`ps1-rds-push`).

**Verdict table:** trigger MISSING; scheduled notebook run MISSING (scheduler
choice still open); S3 outputs RIGHT with a landmine (1-line fix); Lambda→RDS
RIGHT; dashboard refresh RIGHT (content static until the trigger/run exist).

---

## 3. PS3 — Root Cause Analysis — everything to know

**Target:** medallion-complete EventBridge trigger → SageMaker container
daily inference → S3 → Lambda → RDS → dashboard. PS3, like PS1, needs true
daily inference.

**Headline:** the strongest data plane of the five after PS5 — the V26
production run is loaded and verified to the row, the dashboard family
serves, and the 16-Aug guard fix restored the last dark check. But all three
PS3 loaders are manual by design, no trigger exists, and the v25 severity
signal is structurally unlabelled until a severity head ships.

**Notebooks:** `PS3_RootCause_Severity_SageMaker_Source_First_V26.ipynb` is
production (hardware-OOS contract) — its Studio copy was destroyed 03-Aug
(renamed .invalid, zero bytes), so **the repo copy is the only one in
existence**. Run-mode interlocks are deliberate: RUN_MODE defaults to REPLAY,
non-production runs redirect to a sandbox prefix — never flip that default
for convenience. `databricks_daily/` components for a daily incremental
pipeline exist in repo; deployment state is unverified.

**S3:** `chicago/ps3_outputs` (V26/v2.5 production, run `6a787954-…`, 09-Aug,
20 tables/run, matches the 03-Aug replay figures exactly) → `ps3-v25-loader`.
`chicago/ps3/rootcause_outputs` → `ps3-rc-loader`. `chicago/ps3_hardened_remediation/runs`
(in the GOLD bucket, not artifacts) → `ps3-v2-loader`. `chicago/ps3_deepdive`
(legacy) → `ps3-inference`.

**SageMaker endpoint:** one PS3 endpoint InService (`chicago-ps3-rootcause-v1`,
3rd clean week as of 09-Aug). Two defects distinct from PS1's: ECR
`cubic-pdm/mars-ps3` IS live and pins the mutable `:latest` tag (never sweep
it into a PS1 ECR cleanup); model-package sprawl stands at 14 unpruned
versions (tracker #44). W1 follow-up confirmed the model wraps model-package
`chicago-ps3-root-cause/14` (not a direct image) — so the `mars-ps3:latest`
pin actually lives inside that package's InferenceSpecification, to be fixed
there during the exit plan. **0 invocations in 30 days** (first-ever
measurement, completes the D-4-extension deletion-safety evidence).

**EventBridge:** none. All three PS3 loaders are unscheduled by design (safe
to park mid-migration). Target: reuse the PS1 PutEvents pattern once built.

**Loaders/routes:** `ps3-v25-loader` loads 20 S3 tables as `ps3_v25_*`,
`choose_run()` skips partial runs. `ps3-rc-loader`'s run registration
(`ps3_oos_20260804`) is what silently blanked `/ps3/collapse-health` for 12
days — tracker #65, now CLOSED via sql/55 (guard re-keyed to the newest
data-bearing run; TVM MAJOR 0.5789 / CRITICAL 0.4211 reproduced exactly).
`/ps3/summary` is FIXED — full severity scorecard, lightgbm_multiclass,
AUC-macro 0.9666, F1-macro 0.905, 34,696 incidents. **Dark routes:**
`/ps3/rootcause/drivers`, `/ps3/severity/drivers`, `/ps3/drivers/head` all
return `[]` because `ps3_head_feature_importance` holds 0 rows (tracker #66)
— SET W confirmed a fresh rc-loader run cannot fix this (the table isn't even
in that artifact set); it needs a fresh notebook export or the three routes
should be retired. `ps3_v25_device_episode_fact.dashboard_severity` is
'(unlabelled)' on ALL 54,239 episodes — do not re-point the collapse guard's
severity shares at v25 until the severity head ships. Explainability:
`ps3_v25_prediction_explainability` = only 3 status rows across 2,806 devices
— shap isn't installed in the SageMaker env (one `pip install shap` fixes a
third of the causes).

**Live RDS (53 tables, 167,029 rows, 24 views):** largest —
`ps3_v25_device_day` 54,239, `ps3_v25_device_episode_fact` 54,239,
`ps3_incident_predictions` 34,612. Five empty tables including
`ps3_head_feature_importance` (0 rows) and `ps3_severity_drivers`/`ps3_severity_predictions` (0).

**SET W confirmations:** v25 loader dry-run — exactly ONE complete run
(20/20 tables, 117,377 rows, 0 errors). **WARNING:** the newest v2 hardened
run (`ps3_20260801T191241Z`) is mostly empty — 13 of 17 datasets read 0 rows
— never invoke `load` on the v2 loader while this hollow run is newest.
Endpoint image binding resolved: model has no direct image, wraps
model-package `/14`.

**Verdict table:** trigger MISSING; daily inference MISSING (manual only);
S3 outputs RIGHT; Lambda→RDS RIGHT (scheduled later, deliberately); dashboard
PARTIAL (v25 family + fixed guard live; drivers dark #66; severity unlabelled
pending the head).

---

## 4. PS4 — Fault Clustering / Anomaly Detection — everything to know

**Target:** medallion-complete trigger → daily scheduled notebook run once
Gold updates → S3 → Lambda → RDS → dashboard. No per-event inference needed.

**Headline:** loaders are the most automated of the five (two ENABLED
schedules including a weekly v3 cadence), manifest discipline is exemplary —
but every notebook run is manual, no trigger exists, and a 29-Jul migration
was delivered and never executed.

**Notebooks:** `PS4_FaultClustering_{GATE,TVM,VALIDATOR}.ipynb` (+ PySpark
variants) plus `PS4_SageMaker_MLflow_FeatureStore.ipynb` family and export
jobs (`ps4_device_daily_export.py`, `ps4_cluster_s3_export.py`, etc.).

**S3:** gold bucket `chicago/ps4/clustering/<device_type>/asof=<date>/{assignments,cluster_summary}/`
plus a manifest at `chicago/ps4/clustering/manifest/asof=<date>/<device_type>_manifest.json`
— always read paths from the manifest, never construct them. Confirmed: no
`anomaly_score`, `week_start/week_end`, or `facility_id` field exists in what
is actually written — an earlier weekly_* schema assumption was wrong and
should not be resurrected.

**EventBridge:** `ps4-rds-loader` daily, live rule fires **07:35 UTC**
(rule-time drift — the repo deploy script writes 07:10; needs reconciling).
`ps4-v3-loader` weekly, cron(0 8 Mon) ENABLED, last ran on schedule 10-Aug.
No medallion-complete trigger or scheduled notebook run exists.

**Loaders/routes:** `cubic-mars-ps4-rds-loader` recomputes n_devices/n_clusters
independently rather than trusting the manifest. `cubic-mars-ps4-v3-loader`
gates on `READY.json`, weekly cadence. 12 `/ps4/*` routes. Two open items:
the 29-Jul v2 additive migration (`ps4_v2_additive_migration.sql` +
`ps4_v2_backfill_loader.py`) was delivered but **never run against live
RDS** — `ps4v2_*` tables confirmed NOT to exist in the database; decide
run/revise/retire. Tracker #52 (threshold verification) still open, folded
into that decision.

**Live RDS (25 tables, 20,927 rows, 20 views; 13 tables empty):**
`ps4_cluster_assignments` 8,978; `ps4_weekly_device_summary` 7,742;
`ps4_anomaly_timeline` 3,048; `ps4_weekly_alerts` 1,108.

**SET W confirmations:** daily loader dry-run shows Aurora holds prior
generations (4,467 read vs 8,978 in RDS is expected — not an error). The
refused-by-design feeds are now quantified: `anomalies` = 17,568,514 rows =
**47.96% of all device-hours flagged** (deterministic rule, "neither
Lambda-loadable nor meaningful yet"); `outliers` = 36,629,754 rows (~5.5 GB).
Dual vintage confirmed: `scored_asof=2026-04-11` (data) vs
`cluster_asof=2026-07-28` (run date) — must be labelled separately on screen.
v3 loader shows **perfect S3-vs-RDS parity** on every figure. **Variant
question RATIFIED:** manifest declares `"engine": "pyspark"` — current
production clustering comes from the PySpark variants (champion
`P5_IF_TSNE_KMeans`, silhouette 0.6058) — the Databricks-scheduled-job
recommendation is now evidence-complete.

**Verdict table:** trigger MISSING; scheduled run MISSING (scheduler decided —
Databricks job, PySpark variants); S3 outputs RIGHT; Lambda→RDS RIGHT (two
schedules ENABLED); dashboard RIGHT (content static until runs are
scheduled).

---

## 5. PS5 — Reliability / Remaining Useful Life (RUL) — everything to know

**Target:** medallion-complete trigger → daily scheduled notebook run once
Gold updates → S3 → Lambda → RDS → dashboard. PS5 does not need per-event
inference.

**Headline:** the most COMPLETE chain of the five. Notebook family is
mature (v5.6), artifact contract is manifest-verified, the loader has the
best dry-run discipline of any PS, daily load is ENABLED, and the dashboard
is fully wired across 9 routes. Missing the same trigger/schedule gap as
everyone else, plus two PS5-specific open items: the survival-parameter
tables never load, and a device/component population gap needs documentation.

**Notebooks:** `PS5_Reliability_Survival_v5_6.ipynb` is the current
production run — 27.4 min runtime, contract OK, leak-check PASS (08-Aug).
Gate is concordance index (C-index) with a 0.65 floor.

**S3:** gold bucket `chicago/ps5/notebook_outputs/{gates,tvm,validators}/` —
36 files, 2.47 MB per run. Loaded to Aurora: device_rul, serial_reliability,
cindex_leaderboard, permutation_importance, enrich_coverage CSVs. **NOT
loaded:** `*_device_survival_params.json` / `*_serial_params.json` — Weibull
shape/scale and Cox coefficients live only in these JSONs; the declared
`ps5_weibull_params` / `ps5_cox_hazard_ratios` tables have no loader (both
confirmed 0 rows live). PS5 is the CSV outlier among the five PS (others
export Parquet) — migration is specified but deliberately deferred to the
Boston window.

**"ps5_daily_scorer" — what it is:** `api/lambda/ps5_daily_scorer/ps5_daily_scorer.py`
(328 lines) is the SERVE half of a train/score split: TRAIN (notebook,
weekly) fits Weibull + Cox and persists slim parameters + device state to S3;
SERVE (this Lambda, daily) loads those params + latest state, recomputes RUL
with numpy only (no scikit-survival at serve time, milliseconds per fleet),
and upserts device + serial RUL into RDS. **Confirmed NOT deployed**
(ResourceNotFoundException on lookup — confirmed twice). **Prefix mismatch
CONFIRMED:** the scorer expects flat keys under `chicago/ps5/params/` and
`chicago/ps5/state/` — both are empty. The v5.6 notebook actually writes
nested per-fleet folders under `chicago/ps5/notebook_outputs/<fleet>/`.
Either add a small publish step to the notebook, or repoint the scorer's env
prefixes — do not deploy blind.

**EventBridge:** `ps5-rds-loader` daily, cron(20 7 UTC) ENABLED. No
medallion-complete trigger or scheduled notebook run exists.

**Loaders/routes:** `cubic-mars-ps5-rds-loader` — CSV via stdlib, schema-adaptive
from information_schema, per-table SAVEPOINTs, refuses collapsing loads. 9
routes (`/ps5/device-rul`, `/serial-rul`, `/component-summary`, `/leaderboard`,
`/importance`, `/coverage`, `/summary`, `/status`, `/serial-grain`). A
separate, unmerged `ps5-api` Lambda behind a different API GW belongs to an
unmerged branch and is not used by the live V4 dashboard.

**Live RDS (13 tables, 30,476 rows, 8 views — corrected to include
previously-uncounted populated legacy tables):** `ps5_serial_reliability`
12,904 rows and `ps5_reliability_estimates` 4,103 rows are **populated**
(an earlier "dead by schema defect" note is superseded — writer unverified);
`ps5_serial_rul` 11,718; `ps5_device_rul` 1,536; `ps5_cox_hazard_ratios`,
`ps5_weibull_params`, `ps5_feature_alignment_audit` all 0 rows.

**RED FLAG resolved during SET W (#53):** `/ps5/status` was serving STALE
v1-era rows (gates C-index 0.5906, tvms 0.5071 with `broken_champion_selection`,
`dashboard_ready:false`) — below the 0.65 floor. The CURRENT v5.6 leaderboard
tells a different story: **GATE 0.67795 PASS, TVM 0.79848 PASS, VALIDATOR
0.64926** — 0.001 under the 0.65 floor (sd 0.0036), i.e. statistically at the
floor. Action needed: re-point or retire the `/ps5/status` route/table (the
v5.6 loader never writes it), and decide waiver-vs-lift-pass for VALIDATOR.
Tracker #53 moved from OPEN-RED to AMBER.

**Population gap (deliberately not on the dashboard):** the component roster
covers 5,434 devices but only ~1,536 carry an RUL estimate — 3,899 devices
(72%) have components and no estimate, one-directional. Candidate causes:
`is_current` not applied to the component roster, and/or the 2024-01-01
telemetry window. The agreed "no RUL risk" wording is conditional on
confirming those devices have zero hardware-OOS events — resolve the cause
before using that wording anywhere.

**SET W confirmations:** dry-run shows EXACT S3-vs-RDS parity on every
loaded family (device_rul 1,536, serial_rul 11,718, cindex_leaderboard 18,
permutation_importance 164, enrich_coverage 24) — delete-then-insert
semantics proven live.

**Verdict table:** trigger MISSING; scheduled run MISSING (27-min runtime is
schedule-friendly; mechanism now decided — split cadence); S3 outputs RIGHT
(CSV, Parquet deferred by decision); Lambda→RDS RIGHT; dashboard refresh
RIGHT (all 9 routes wired).

---

## 6. Decisions that need to be taken for PS1

From the audits plus the scheduling/decisions and right/wrong/fix docs, here
is everything still requiring a PK/team decision for PS1 specifically —
already-settled items are marked so nobody relitigates them.

**Already settled — do not relitigate:**
- D-1: retire ECR `cubic-pdm/mars-ps1` (lifecycle policy + 30-day re-audit) —
  it is referenced by zero model packages and zero endpoints.
- D-2: daily scoring = a SageMaker Processing job (managed DLC, pinned
  dependencies, also pin `xgboost` in the bundle template), not always-on
  endpoints.
- D-3: the gold-complete trigger must be Databricks EventBridge PutEvents —
  never an S3 bucket notification (the bucket's notification doc is
  replace-not-update and already routes elsewhere).
- D-4: delete all three PS1 real-time endpoints after the Processing job's
  first supervised run. The 0-invocations-in-30-days evidence for all three
  is now complete, so this is safe to execute as soon as the batch chain
  produces its first parity run.

**Still open — need a decision:**
1. **Endpoint deletion timing** — D-4 itself is settled, but exactly *when*
   to pull the trigger (immediately after the first parity run vs. after N
   days of parity monitoring) hasn't been stated as a number.
2. **`xgboost>=2.0` pin** — decide/confirm the exact pinned version for both
   requirements.txt and the bundle template before the next endpoint rebuild.
3. **PS1 dashboard hygiene package** — several independent calls bundled
   together and none formally scheduled: add a vintage badge to the PS1
   screen, wire the UI to call the real `/ps1/model-performance` instead of
   hardcoded constants, replace the 26-Jul hand-seeded `station-summary` /
   `risk-trend` tables, and decide whether to label or fully retire the six
   routes still serving frozen Path B data.
4. **Path B (`ps1-rds-push`) fate** — its DELETE-not-category-scoped bug is
   fixed in code but deploy state is unverified, and `deploy.sh` requires
   `PATHB_REVIVE=1` to re-enable. Decide formally: keep it retired (current
   RECOMMENDATION) or revive it once fixed.
5. **ServiceNow integration** — the real SQS FIFO→ServiceNow pipe has never
   been built; `/ps1/servicenow-stage` is a stand-in. Needs a build/no-build
   decision and, if build, a priority slot.
6. **Gold-bucket orphan files** (`{tvm,gate}_lgb_fixed_*.pkl` + `thresholds_*.json`
   from an archived evaluation notebook) — inventory before deleting; no
   decision recorded yet on disposal.
7. **Metric-appropriate re-gate** — when PS1 is retrained on the extended
   (12-Apr→present) window, the gate is AP/PR-AUC + a recall floor, never raw
   accuracy — this is policy-settled, but the actual recall-floor number for
   PS1's re-gate still needs to be set by the team when that retrain happens.

All of the still-open items above are independent of each other and can be
decided in any order; none of them blocks or is blocked by the incremental
ingestion timeline.

---

## 7. All next steps required for PS1 and PS2

### PS1 — in order

1. **Do this week, independent of everything else:** confirm the `xgboost`
   pin decision (#2 above) so it's ready before any bundle rebuild.
2. **Gate (~23-Aug):** incremental Oracle ingestion 12-Apr→present lands
   (owned by Cubic + the teams), with per-layer reconciliation evidence. This
   gates every step below — a perfect daily-inference chain would score
   nothing new without it.
3. **Build the PS1 batch chain** (all three components are committed but
   undeployed):
   - Databricks feature-frame task — extract notebook CELLS 6-8 directly,
     never re-implement the logic in Python.
   - `ps1_batch_score_daily.py` as a SageMaker Processing job (managed DLC,
     pinned deps).
   - `ps1_gold_complete_event.py` PutEvents trigger (never S3 bucket
     notifications).
4. **First supervised run** after the incremental data lands: compare batch
   scores against the existing endpoints' outputs on the same day's frame —
   this is parity-by-construction (the scorer loads the endpoint's own
   model.tar.gz) and resolves the E-1 threshold/scorecard mismatch by
   construction.
5. **Wire the batch outputs** through the existing xw-loader S3 contract and
   confirm the dashboard refreshes from real daily scores.
6. **Delete the three PS1 endpoints** + endpoint configs (D-4); keep the
   model packages in the registry.
7. **Retire ECR `cubic-pdm/mars-ps1`** (D-1: lifecycle policy + 30-day
   re-audit).
8. **Dashboard hygiene pass** (can run in parallel with the above): add a
   vintage badge, wire `/ps1/model-performance` into the UI, replace the
   26-Jul seed tables, label or retire the six frozen Path B routes.
9. **Re-gate on the extended window** once retrained: AP/PR-AUC + recall
   floor, never raw accuracy.
10. Freeze evidence step (already done): the endpoint captures under
    `tooling/out/endpoint_capture/` are committed for PS1 — no action needed,
    listed here only for completeness since PS3 needs the equivalent before
    its own endpoint deletion.

### PS2 — in order

1. **Today, before anyone reruns the notebook:** set
   `PS2_PRODUCTION_EXPORT_PREFIX=chicago/ps2_outputs` — one environment
   variable. This is the single highest-priority PS2 action in either
   document; skipping it causes a silent dashboard freeze with no error
   surfaced anywhere. PS2 is on an ENABLED 07:10 UTC loader schedule, so this
   cannot be left half-migrated overnight.
2. **Gate (~23-Aug):** same incremental ingestion dependency as PS1 (shared
   gate, Cubic + teams).
3. **Build the Databricks scheduled job** for the PS2 notebook, chained
   directly after the Gold build completes in the same workflow (so the
   trigger is free — no separate EventBridge wiring needed). This is the
   evidence-decided recommendation (31 pyspark/SparkSession hits in
   `PS2_Failure_Patterns_v2_5_4` — Spark-native). Keep SageMaker available
   for ad-hoc analysis only, not the scheduled path.
4. **Create Aurora tables** for the five `ps2_v25_*_audit` prefixes
   (category_profile, failure_definition_alignment, failure_label_summary,
   ps1_label_parity, ps1_model_performance) so each run's label-validation
   evidence becomes queryable instead of stranded in S3. Low cost, called out
   as valuable in both documents.
5. **Run the PS1-vs-PS2 comparison once:** PS2 carries its own independent
   scoring of PS1 (`ps2_v25_ps1_*` tables) that has never been compared
   against `ps1_model_performance`. Any disagreement is signal, not noise —
   worth doing as soon as convenient, does not depend on ingestion.
6. **Investigate `cubic-mars-ps2-rds-push`** — a live, deployed Lambda
   (25-Jul, 512 MB) with no entry in any audit or lineage doc. Determine its
   role or retire it (parallel to the already-retired `ps1-rds-push`).
7. **Re-gate on the extended window** once retrained: macro-F1 is PS3's gate,
   not PS2's — PS2's own gate criteria should be confirmed against the
   metric-appropriate table when this step is reached (the source docs give
   PS1/PS3/PS4/PS5 explicit gates but do not state PS2's separately; flag
   this as a gap to close with the team rather than assume one).

Both PS1 and PS2's builds (steps 3 onward in each list) are sequenced
strictly after the shared ~23-Aug ingestion gate; everything listed as "today"
or independent above should happen now, without waiting.

---

## Cross-cutting items that touch both PS1 and PS2 (for awareness)

- **Do first, this week, independent of the above:** rotate
  `cubic-mars-secret-rds-dev` — the RDS password was exposed in chat on
  16-Aug and has never been rotated (LastChanged 22-Jul, no rotation
  configured). This is the single highest-severity open item across the
  entire program, escalated to do-first (item A8). Re-run the four loader
  dry-runs afterward as the connectivity proof.
- **Disable the dead 02:00 UTC training pipeline**
  (`cubic-mars-events-training-daily-dev` → `cubic-mars-sfn-training-pipeline-dev`):
  confirmed to run ~45 ms daily against an empty `$.models` list — it has
  never trained anything and cannot as configured. Record as retired
  scaffold; do not reuse it for the real PS1/PS2 scheduling builds above.
- Both PS1 and PS2 loaders are examples of the program-wide operational rule
  discovered in SET W: **an empty payload `{}` is a REAL LOAD**, not a
  no-op — always use the explicit dry-run form when invoking any loader
  manually.
