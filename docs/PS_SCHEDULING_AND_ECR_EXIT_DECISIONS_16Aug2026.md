# PS1-PS5 — Scheduling Decisions, ECR Exit Plan, and Prioritized Fixes

**16-Aug-2026 (evening).** Companion to the five per-PS audits
(`docs/PS1..PS5_AUDIT_16Aug2026.md`). Tags: `[M]` measured, `[R]` repo, `[D]`
decision, `[U]` unverified (in the verification set).

## 1. Decisions recorded today (PK, 16-Aug)

- **Incremental ingestion** is owned by Cubic + the teams; expected start in
  about ONE WEEK. The Oracle VPN feed for 12-Apr-2026 -> present arrives as a
  one-time dump, then stays current through the Databricks medallion
  architecture. Everything in section 4 sequences AFTER this.
- **D-4 CONFIRMED:** delete the three PS1 real-time endpoints after the
  Processing job's first supervised run. **The same disposition applies to the
  PS3 endpoint.**
- **Scheduling mechanism per PS** — recommendations below to be ratified by the
  team.

## 2. Per-PS scheduling decision note (evidence-based)

The question for PS2/PS4/PS5: Databricks scheduled job vs SageMaker
scheduled-notebook job vs Lambda. Deciding evidence measured from the repo
16-Aug (`Select-String` over the notebooks):

| PS | Evidence | RECOMMENDATION | The one check that settles it |
|---|---|---|---|
| PS1 | settled `[D 15-Aug]` | Daily scoring = SageMaker PROCESSING JOB (managed sklearn DLC, pinned deps); features from a Databricks task; trigger = Databricks PutEvents. Retrain = SageMaker notebooks, on demand | none — settled; build it |
| PS2 | `PS2_Failure_Patterns_v2_5_4` has **31 pyspark/SparkSession hits** — Spark-native `[R]` | **Databricks scheduled job**, chained directly after the Gold build completes (same workflow, so "trigger" is free). SageMaker stays for ad-hoc analysis only | none — the Spark evidence is decisive |
| PS3 | V26 is a monolithic SageMaker train+publish notebook; `databricks_daily/` components exist in repo, deploy state unknown | Daily inference = the SAME Processing-job pattern as PS1 (score-only path extracted from V26); trigger shared with PS1 | inspect `notebooks/ps3_root_cause_analysis/databricks_daily/` contents to see how much is already built `[U]` |
| PS4 | plain variants have **0 spark hits**; `_PySpark` variants + job-style exporters (`ps4_device_daily_export.py`, `ps4_cluster_s3_export.py`) exist `[R]` | **Databricks scheduled job** running the PySpark variants + exporters (per-fleet parallelism, same-workflow trigger). Fallback if cluster capacity is a concern: SageMaker notebook jobs on the plain variants | which variant produced the CURRENT S3 outputs (manifest `champion_pipeline`) `[U — batch D]` |
| PS5 | v5.6 is pure-Python survival (51 lifelines/Cox/Weibull hits, ~0 spark) `[R]`; **`api/lambda/ps5_daily_scorer/` already implements the daily-serve half** | **Split cadence:** DAILY serve = deploy `cubic-mars-ps5-daily-scorer` Lambda on the gold-complete trigger; WEEKLY retrain = SageMaker scheduled-notebook job running v5.6 (27-min runtime `[M 08-Aug]`) | scorer deploy state + whether v5.6 publishes params/state to the prefixes the scorer expects `[U — batch D]` |

### What ps5_daily_scorer is (PK's question, answered from source `[R]`)

`api/lambda/ps5_daily_scorer/ps5_daily_scorer.py` (328 lines) is the SERVE half of
a train/score split for PS5: TRAIN (notebook, weekly) fits Weibull + Cox and
persists SLIM parameters + a device state feed to S3; SERVE (this Lambda, daily)
loads params + latest state, recomputes RUL with numpy only (no scikit-survival at
serve time — a coefficient dot-product plus a Weibull residual-life integral,
milliseconds per fleet), and upserts device + serial RUL into the RDS tables.
It has a pure, unit-testable scoring core and a self-test.

Two things to verify before relying on it `[U]`:
1. Is a Lambda named `cubic-mars-ps5-daily-scorer` deployed at all?
2. Prefix alignment: the scorer defaults to `chicago/ps5/params/` and
   `chicago/ps5/state/`, while v5.6 writes `*_device_survival_params.json` under
   `chicago/ps5/notebook_outputs/<fleet>/`. Either the env vars are set to the
   notebook_outputs paths, or a small publish step (or notebook change) is needed.
   Do NOT deploy it blind.

## 3. The ECR-inference exit plan for PS1 + PS3 (how to move away completely)

First, the naming correction so the team aims at the right target: **PS1 does not
actually serve from a custom ECR image today** — the three endpoints run AWS's
managed sklearn container; the `cubic-pdm/mars-ps1` ECR repo is referenced by
nothing `[M 15-Aug]`. PS3's endpoint DOES pin ECR `cubic-pdm/mars-ps3:latest`
(mutable tag — a real defect). "Moving away from ECR inferencing" therefore means:
**retire always-on endpoint serving entirely** and score in batch. Steps, in order:

1. Freeze evidence: commit the endpoint captures (`tooling/out/endpoint_capture/`)
   — done for PS1 `[R]`; produce the PS3 equivalent before any deletion.
2. Build the PS1 batch chain (all components committed, none deployed):
   Databricks feature-frame task (extract notebook CELLS 6-8 — never re-implement
   in Python) -> `ps1_batch_score_daily.py` as a SageMaker Processing job (managed
   DLC, PINNED deps — also pin `xgboost` inside the bundle template) ->
   `ps1_gold_complete_event.py` PutEvents trigger (NEVER S3 bucket notifications).
3. First supervised run after the incremental data lands: compare batch scores
   with endpoint outputs on the same day's frame (parity by construction — the
   scorer loads the endpoint's own model.tar.gz). This resolves E-1: served
   thresholds and the published scorecard become consistent.
4. Wire the batch outputs through the existing loader path (they land in the same
   S3 contract the xw-loader already reads) and confirm the dashboard refreshes.
5. DELETE the three PS1 endpoints (D-4 — confirmed 16-Aug) + endpoint configs;
   keep model packages in the registry.
6. Repeat 1-5 for PS3 (score-only path from V26; same trigger; then delete the
   PS3 endpoint per today's confirmation). Fix or retire the `mars-ps3:latest`
   pin as part of it.
7. Retire ECR `cubic-pdm/mars-ps1` (D-1: lifecycle policy + 30-day re-audit);
   review `mars-ps3` after step 6.
8. Update the serving sections of the lineage doc + audits ("Being Updated" ->
   the batch architecture).

## 4. Issues and fixes, per PS, in order of sequential importance

**Cross-PS, in execution order:**

1. PS2 export-prefix landmine — set `PS2_PRODUCTION_EXPORT_PREFIX=chicago/ps2_outputs`
   BEFORE anyone reruns PS2 (one env var; silent dashboard freeze otherwise).
2. Incremental ingestion 12-Apr -> present (Cubic + teams, ~1 week out) with
   per-layer reconciliation evidence. Gates every model refresh and every
   "daily" ambition.
3. PS1 batch-scoring chain (section 3, steps 2-5).
4. PS3 batch-scoring chain (section 3, step 6).
5. PS2/PS4 Databricks scheduled jobs + PS5 scorer deploy per section 2.
6. Model refresh on the extended window; re-gate with metric-appropriate gates
   (PS1 AP/PR-AUC + recall floor; PS3 macro-F1; PS4 lead-indicator
   precision/recall; PS5 C-index >= 0.65). Never raw accuracy.
7. Dashboard hygiene: PS1 vintage badge; wire `/ps1/model-performance`; replace
   the 26-Jul seed tables; label or retire the six frozen Path B routes.

**Per PS (bullets, most important first):**

- PS1: no daily scoring (chain committed, undeployed) -> build section 3;
  E-1 threshold/scorecard mismatch -> resolved by the chain; endpoints unused ->
  delete after first supervised run (confirmed); `xgboost>=2.0` unpinned in
  requirements AND the bundle template -> pin both; PS1 screen rides 26-Jul seed
  tables with no vintage badge -> dashboard hygiene pass; six routes on frozen
  Path B tables -> label or retire.
- PS2: export-prefix landmine -> env var TODAY; no scheduled run -> Databricks
  job after Gold; five `ps2_v25_*_audit` outputs have no Aurora tables -> create
  the table pair so label-validation evidence is queryable; nobody has compared
  PS2's independent PS1 scoring (`ps2_v25_ps1_*`) with `ps1_model_performance` ->
  run the comparison once, disagreement is signal.
- PS3: severity is structurally unlabelled in v25 (100% `(unlabelled)`) -> ship
  the severity head before re-pointing any severity guard at v25;
  `ps3_head_feature_importance` empty -> the three drivers routes serve `[]`
  (tracker #66) -> reload or retire the two-head drivers family;
  explainability = 3 status rows -> `pip install shap` in the SageMaker env fixes
  one of three causes; `mars-ps3:latest` mutable pin -> fix during section 3
  step 6; model-package sprawl (14) -> prune to one Approved champion per group.
- PS4: no scheduled run -> Databricks job (ratify variant question first);
  29-Jul `ps4v2_*` additive migration never executed -> decide run/revise/retire;
  #52 threshold verification in loaded results -> include in the verification
  set follow-up.
- PS5: scorer Lambda undeployed/unwired + prefix mismatch risk -> verify then
  deploy on the trigger; `ps5_weibull_params`/`ps5_cox_hazard_ratios` declared
  but never loaded (params live only in JSONs) -> either load them or drop the
  tables (the scorer reads the JSONs, so tables may be unnecessary — decide);
  population gap (3,899 devices with components, no estimate) -> resolve cause
  (`is_current` vs telemetry window), then apply the conditional "no RUL risk"
  wording in documentation; live C-index vs 0.65 (#53) -> stamp during
  verification; CSV -> Parquet standardisation deferred to Boston window (keep).

## 5. What happens next

PK runs the verification set (`docs/VERIFICATION_SET_16Aug2026.md`, five small
batches) and pastes outputs; the five audits then get RDS schemas + live row
counts folded in and every remaining `[U]` stamped `[M]` or corrected. The team
ratifies section 2; builds proceed in section 4 order.


---

## Addendum — Cross-cutting live findings, 16-Aug-2026 evening [M]

1. **UNDOCUMENTED DAILY TRAINING PIPELINE DISCOVERED:** EventBridge rule
   `cubic-mars-events-training-daily-dev`, ENABLED, cron(0 2 * * ? *) — targets Step
   Functions state machine `cubic-mars-sfn-training-pipeline-dev`. This appears in NO
   tracker, audit, or memory to date. It has been eligible to fire daily at 02:00 UTC.
   INVESTIGATE FIRST (follow-up command below): what it runs, whether executions succeed
   or fail daily, and whether it mutates anything.
2. **ECR estate (10 repos):** 6 are EMPTY (`cubic-pdm/mars-ps2`, `mars-ps4`, `mars-ps5`,
   `cubic-mars-dev-repo`, `cubic-mars-ecr-inference-dev`, `cubic-mars-ecr-training-dev`)
   — cleanup candidates. `cubic-pdm/mars-ps1` (3 images, newest 13-Jul) referenced by
   nothing — retire per D-1. `cubic-pdm/mars-ps3` — exactly one image, `latest`, 13-Jul.
   `dashboard/reactui` — newest image is tagged `latest`, pushed **15-Jul**: it PRE-DATES
   the entire V4 merge, so anything running that image serves an outdated UI; rebuild from
   current `main` with a git-SHA tag before any publish. `fastapi/backend` — newest 21-Jul.
3. **Loader run evidence (log last-event):** every ENABLED daily rule ran on schedule
   today (dim 05:45Z-, xw 06:43Z, ps2 07:11Z, ps4 08:23Z, ps5 07:14Z); ps4-v3 weekly ran
   Mon 10-Aug; ps1-rds-push last ran 10-Aug (its disable date) and its reserved
   concurrency = 0 is now CONFIRMED live; PS3 loaders idle since their manual runs
   (29-Jul / 04-Aug / 09-Aug).
4. **PS4 rule-time drift:** live `cubic-mars-ps4-daily-load` fires 07:35 UTC; the repo
   deploy script writes 07:10. Reconcile.
5. **PS5 RED FLAG:** served `/ps5/status` C-indexes (gates 0.5906 / tvms 0.5071, tvms
   `broken_champion_selection`, `dashboard_ready:false`) sit BELOW the 0.65 floor —
   possibly stale v1 status rows beside the v5.6 leaderboard; reconcile before client use
   (#53 OPEN-RED).
6. **RDS totals:** 216 tables + 90 views, 51 empty tables; 22 of the empty ones are the
   legacy app scaffold (`anomalies`, `devices`, `cascade_events`, ...) — post-go-live
   cleanup candidates. Full inventory: `docs/reference/RDS_LIVE_INVENTORY_16Aug2026.md`.

### Follow-up mini-set (4 commands, regular CloudShell)

```bash
export AWS_PAGER=""
aws sagemaker describe-model --model-name chicago-ps3-root-cause-2026-07-13-07-02-42-300 \
  --query "{Primary:PrimaryContainer.Image,Containers:Containers[].Image}" --output json

aws stepfunctions list-executions \
  --state-machine-arn arn:aws:states:us-east-1:170202974600:stateMachine:cubic-mars-sfn-training-pipeline-dev \
  --max-items 5 --query "executions[].{n:name,s:status,start:startDate}" --output table

aws s3 cp s3://cubic-mars-pm-s3-datalake-dev-gold-170202974600/chicago/ps4/clustering/manifest/asof=2026-07-28/gate_manifest.json - | python3 -m json.tool

curl -s "https://a9yuqt9j9b.execute-api.us-east-1.amazonaws.com/ps5/status?city=CHI"; echo
curl -s "https://a9yuqt9j9b.execute-api.us-east-1.amazonaws.com/ps5/leaderboard?city=CHI"; echo
```
