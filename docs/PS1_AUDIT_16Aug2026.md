# PS1 — Complete Audit vs the Target Daily-Inference Architecture

**16-Aug-2026.** Audience: the Chicago delivery team. Structure follows PK's audit
request (a-f), then verdicts against the stated target architecture.

Evidence tags: `[M <date>]` measured live on that date, `[R]` read from the repo
(main), `[D]` documented decision, `[U]` unverified — confirm in CloudShell before
relying on it. Nothing in this document is assumed.

**The target architecture (PK, 16-Aug):** EventBridge trigger fires when daily
ingestion + medallion build (Raw -> Bronze -> Silver -> Gold) completes -> SageMaker
container runs daily inference -> outputs land in S3 -> Lambda loaders push latest
outputs to RDS -> dashboard serves the daily refresh.

**Headline verdict:** the BOTTOM HALF of that chain (S3 -> loader -> RDS -> routes ->
V4 dashboard) is built, scheduled and verified. The TOP HALF (gold-complete trigger,
daily inference, incremental data) is designed and partially committed but NOT live.
Today PS1 re-loads the same 29-Jul artifacts nightly; every number is as-of
2026-04-11 — consistent with PK's own note that incremental data (11-Apr -> present)
has not yet been ingested.

---

## (a) PS1 SageMaker notebooks and jobs

All under `notebooks/` in the repo `[R]`.

| Notebook / job | Role | Status |
|---|---|---|
| `ps1_failure_prediction/PS1_3d_GATE_SageMaker_MLflow_FeatureStore.ipynb` | PRODUCTION trainer + registrar + deployer, GATE | current |
| `ps1_failure_prediction/PS1_3d_TVM_SageMaker_MLflow_FeatureStore.ipynb` | PRODUCTION, TVM | current |
| `ps1_failure_prediction/PS1_3d_VALIDATOR_SageMaker_MLflow_FeatureStore.ipynb` | PRODUCTION, VALIDATOR | current |
| `ps1_failure_prediction/PS1_3d_GATE_OOS_Optimized_SageMaker_v4.ipynb` | analysis only — contains no deploy/register code | kept |
| `ps1_failure_prediction/Chicago_PS1_Predictive_Failure.ipynb` | early analysis | kept |
| `ps1_failure_prediction/PS1_VALIDATOR_Device_Bus_Serial_Map.ipynb` | bus/serial mapping utility | kept per decision D-10 |
| `cross_wired_daily_job.py` | Databricks job — builds the cross-wired export the live loader reads | live path |
| `ps1_build_features_daily.py` | NEW (committed 16-Aug) — daily feature-frame task for the scoring chain | committed, NOT deployed `[U]` |
| `ps1_gold_complete_event.py` | NEW (committed 16-Aug) — fires EventBridge PutEvents after VERIFYING gold is non-empty for the as-of date | committed, NOT deployed `[U]` |
| `ps1_batch_score_daily.py` | Daily scoring job (Processing-job design; loads the endpoint's own model.tar.gz for parity by construction) | committed, NOT deployed `[U]` |

Superseded baselines (`PS1_3d_*_v3`, `PS1_{fleet}_Failure_Prediction`, Evaluation_Fix,
mlflow purge/teardown scripts) were removed in the 16-Aug repo sweep; recoverable via
git tag `archive-sweep-base`.

**Critical fact about "daily":** the production notebooks' CELL 20 stores the held-out
TEST SPLIT as `predictions` and CELL 24 exports exactly that. "Daily" in
`device_ps1_cross_wired_daily` names the GRAIN (device-day rows), not a cadence. No
notebook, job, or Lambda scores a NEW day today `[R, docs/PS1_DAILY_INFERENCE_DESIGN.md]`.

## (b) S3 locations for PS1 outputs

Buckets: artifacts `cubic-mars-pm-s3-datalake-dev-artifacts-170202974600`,
gold `cubic-mars-pm-s3-datalake-dev-gold-170202974600`.

| Path | What | Status |
|---|---|---|
| artifacts `chicago/device_ps1_cross_wired_daily/{gate,tvm,validator}` | Path A — the LIVE feed. The fleet is the S3 OBJECT KEY, not a folder or partition | live; objects dated 29-Jul; 786,525 rows total (GATE 107,110 / TVM 184,483 / VALIDATOR 494,932) `[M 11-Aug]` |
| gold `chicago/gold/device_ps1_cross_wired_daily` | Path B — legacy feed | frozen; loader cron DISABLED 10-Aug `[M]` |
| artifacts `chicago/ps1/features/asof=<date>/fleet=<slug>/` | planned daily feature frames for the scoring job | DESIGN only `[D]` |
| model bundles | `model.tar.gz` per fleet (booster JSON + meta + threshold + inference.py) attached to the endpoints | live `[M 15-Aug]` |
| gold bucket orphans | `{tvm,gate}_lgb_fixed_*.pkl` + `thresholds_*.json` written by an archived evaluation notebook | orphaned — inventory before deleting `[R]` |

## (c) SageMaker endpoints for the PS1 models

Three real-time endpoints exist, InService `[M 09-Aug audit, 3rd clean week]`:
`chicago-ps1-3d-gate-failure-v1`, `chicago-ps1-3d-tvm-failure-v1`,
`chicago-ps1-3d-validator-failure-v1`.

Measured serving contract `[M 15-Aug]`:

| fleet | features | deployed threshold | classifier lineage |
|---|---|---|---|
| GATE | 47 | 0.12284049 | SparkXGBClassifierModel |
| TVM | 40 | 0.02648935 | SparkXGBClassifierModel |
| VALIDATOR | 40 | 0.39651793 | SparkXGBClassifierModel |

Facts the team must hold onto:

1. **The container is AWS's managed sklearn DLC** (account 683313688378), not a
   custom ECR image. The `cubic-pdm/mars-ps1` ECR repo is referenced by zero model
   packages and zero endpoints — decision D-1: retire it.
2. **E-1, quantified:** the deployed thresholds and feature counts match NO row of
   the scorecard the dashboard serves (`v3-sklearn` run: thresholds 0.648/0.495/0.605).
   GATE's endpoint fires at 0.1228 while `/ps1/threshold-sweep` publishes 0.65 —
   flagged-device counts derived from the published threshold are wrong, in the
   unsafe direction.
3. **Zero invocations in 14 days** `[M]` — no code path anywhere calls a PS1
   endpoint. Every dashboard number is batch notebook output loaded into Aurora.
4. **Decision D-4 (15-Aug, settled):** delete all three endpoints once the capture
   is committed; daily scoring becomes a SageMaker **Processing job** (D-2) in the
   managed DLC with pinned dependencies (D-9), triggered by Databricks PutEvents
   (D-3). PK's stated "use SageMaker container for daily inference" is satisfied by
   the Processing-job design — it does NOT require keeping always-on endpoints.
5. `xgboost>=2.0` is unpinned in requirements.txt AND in the template that generates
   future bundles — pin both.

## (d) EventBridge for PS1

| Rule / trigger | Schedule (UTC) | State |
|---|---|---|
| `ps1-xw-loader` daily load (S3 -> RDS) | cron(40 6 * * ? *) | ENABLED `[M 11-Aug]` |
| `ps1-rds-push` daily load (Path B) | cron(15 6 * * ? *) | DISABLED 10-Aug 11:55Z; S3 ObjectCreated trigger still armed but throttled by reserved concurrency 0 — that value is asserted only in a script comment, verify live `[U]` |
| Gold-complete trigger (PutEvents from Databricks) | event-driven | DESIGNED + committed (`ps1_gold_complete_event.py`), NOT deployed `[U]` |

**There is NO trigger from medallion completion today.** The 06:40 cron re-loads
whatever sits at the Path A prefix — same bytes since 29-Jul. Do NOT implement the
gold trigger as an S3 bucket notification: the bucket's notification document is
replace-not-update and already routes to another consumer `[D 15-Aug]`.

## (e) Loaders, schedulers, dispatchers, handlers, routes for RDS

| Component | Role | Key facts |
|---|---|---|
| `cubic-mars-ps1-xw-loader` (Lambda) | THE live PS1 loader — Path A S3 -> Aurora | reference pattern for all loaders: BEGIN / per-fleet SAVEPOINT, fleet-scoped DELETE, refuses empty shapes, self-declaring EXPECTED contract (786,525), ETag freshness verdict, lineage row outside the txn. Actions: dry_run/load/verify/audit/reload_source/copy_rows/csv_cell/freshness `[R]` |
| `cubic-mars-ps1-rds-push` (Lambda) | Path B loader, retired | DELETE was not category-scoped (a gates run wiped TVM); fix committed, deploy state `[U]`; deploy.sh refuses to re-enable without PATHB_REVIVE=1 |
| `cubic-mars-dashboard-api` (Lambda) | serves ALL dashboard routes | 36 `/ps1/*` routes; only `/ps1/servicenow-stage` is method-gated (POST) |
| ServiceNow dispatcher | `POST /ps1/servicenow-stage` -> `servicenow_staging` table | stand-in only — the SQS FIFO -> ServiceNow integration has never been built `[M 09-Aug, 6th audit]` |
| Aurora | `appdb` on cluster `cubic-mars-rds-aurora-dev` (writer `-1-dev`) `[M 16-Aug]` | |

Route/table state that matters:

- `/ps1/summary` + `/ps1/model-performance` serve the REAL 3-fleet scorecard
  (all PASS, promoted, target `will_hardware_oos_3d`, revision R7-1): GATE AUC
  0.8961 / AP 0.9486, TVM 0.9040 / 0.9835, VALIDATOR 0.9956 / 0.9903 `[M 16-Aug]`.
- `ps1_failure_summary` is RETIRED (sql/51) — 2 hand-typed rows, never loader-fed.
- SIX routes still read Path B's frozen tables (`crosstab`, `coverage`,
  `serial-predictions`, `runs`, `load-audit`, part of `model-performance`) and serve
  data frozen at the 10-Aug shutdown, beside live panels, with nothing on screen
  distinguishing them `[R]`.
- `ps1_inference_runs` mixes `run_kind` train/batch_score AND holds TWO runs dated
  26-Jul (Spark v2 at 16:40Z, sklearn v3 at 12:00Z) — discriminate on
  `mlflow_version`, never on `run_ts DESC`.
- Migrations sql/49-55 are applied MANUALLY via psql (CloudShell VPC env); they are
  NOT in `migrate()`.

## (f) PS1 elements on the dashboard

V4 (`dashboard/src/v4/`): `V4PS1Overview.jsx` (5 sub-tabs), `V4Device360.jsx` +
popup, shared `V4Locations.js` (via `/ps1/facilities` — the single naming
authority) and `V4Evidence.js`. V4api calls 26 PS1 routes incl. the 12-route
`xw-*` cross-wired family `[R]`.

Rendering caveats, all verified 16-Aug (`docs/V4_DASHBOARD_PS1_AUDIT_16Aug2026.md`):

1. The PS1 screen has NO data-vintage indicator (PS2/PS3 carry a StatusBar).
2. `/ps1/station-summary` + `/ps1/risk-trend` are 26-Jul HAND-SEEDED tables — every
   PS1 headline number and the Estate card render from that seed.
3. V4 renders PS1 model stats from UI-layer constants and never calls
   `/ps1/model-performance` — the real scorecard exists but is not what users see.
4. `/ps1/device-360` costs 5.7-7.6 s against a 30 s API GW cap; optimum 2 concurrent.

---

## Verdict vs the target architecture

| Target step | Reality | Verdict |
|---|---|---|
| (a) EventBridge on medallion-complete | Designed (PutEvents + gold-non-empty guard), committed, not deployed | MISSING — build next |
| (b) SageMaker container daily inference | Does not exist; notebooks export the test split; settled design = Processing job, not endpoints | MISSING — the feature-frame task is the one blocker |
| (c) Outputs to S3 | Path A prefix live and contract-true | RIGHT (but static since 29-Jul) |
| (d) Lambda -> RDS | xw-loader live, scheduled, reference-grade | RIGHT |
| (e) Dashboard daily refresh | V4 live; but headline panels ride a 26-Jul seed and data is as-of 11-Apr | PARTIAL — wiring right, freshness and PS1-seed panels wrong |

**The single prerequisite underneath everything: incremental Oracle data
(12-Apr -> present) has not been ingested.** Until Bronze/Silver/Gold carry new days,
a perfect daily-inference chain would score nothing new. Sequence: ingestion first,
then the scoring chain, then model refresh/retrain on the extended window.
