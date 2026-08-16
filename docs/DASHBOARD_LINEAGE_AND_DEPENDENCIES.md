# CUBIC MARS Chicago — Dashboard Lineage, Dependencies and Operations

**Version 1.1 — 2026-08-16 (evening).** Written for the delivery team: everything
between a SageMaker notebook and a rendered dashboard panel, in one place.

Changelog v1.1: PR #13 merged to `main` (`a80cee3`); repo stale-file sweep applied
per PK instruction (see §10) — recovery point = git tag `archive-sweep-base`.

Evidence tags: `[M <date>]` measured live on that date, `[R]` read from the repo at
commit `f043c60`+ (branch `feat/v4-readme-collapse-guard`), `[D]` documented decision,
`[U]` unverified — check live before relying on it. Numbers are observations with
dates, not constants.

Companion documents in this repo: `docs/PS1_CANONICAL.md`,
`docs/PS1_OPERATIONAL_INVENTORY.md`, `docs/PS1_DECISIONS_15Aug2026.md`,
`docs/V4_DASHBOARD_PS1_AUDIT_16Aug2026.md`, `README_V4.md`.

---

## 1. The big picture

```
SageMaker / Databricks notebook (per PS)
      | writes run artifacts (Parquet or CSV)
      v
S3    gold      s3://cubic-mars-pm-s3-datalake-dev-gold-170202974600
      artifacts s3://cubic-mars-pm-s3-datalake-dev-artifacts-170202974600
      | read by one per-PS loader Lambda (VPC-attached, pg8000, python3.12)
      v
Aurora PostgreSQL 16.4  cluster cubic-mars-rds-aurora-dev
      writer instance cubic-mars-rds-aurora-1-dev, database appdb  [M 16-Aug]
      | read by the API Lambda
      v
Lambda cubic-mars-dashboard-api  (route families for PS1-PS5 + dims)
      | HTTP API Gateway  https://a9yuqt9j9b.execute-api.us-east-1.amazonaws.com
      v
React dashboard  dashboard/  (V4 UI in dashboard/src/v4/, 19 files)
```

Account `170202974600`, region `us-east-1`, VPC `vpc-0a7775adc7d382fbb`.
All five problem statements follow this route. There is no notebook-to-RDS path and
no dashboard-to-S3 path. A second HTTP API GW (`b1s4xxlddb`, single `$default`
route) fronts a separate ps5-api Lambda used by the UNMERGED
`feat/ps5-hardware-oos-dashboard` branch — the V4 on `main` does not use it; its
route map lives inside that Lambda `[M 09-Aug]`.

Note on names: an older revision of the lineage doc said database `postgres`; the
live application database is **`appdb`** `[M 16-Aug, psql]`.

---

## 2. (a) SageMaker / Databricks notebooks per problem statement

Production = deploys/registers models or produces the served artifacts. Everything
lives under `notebooks/` in this repo `[R]`.

### PS1 — failure prediction (3-day hardware-OOS onset, `will_hardware_oos_3d`)
| Notebook / job | Role |
|---|---|
| `PS1_3d_GATE_SageMaker_MLflow_FeatureStore.ipynb` | PRODUCTION trainer+deployer, GATE |
| `PS1_3d_TVM_SageMaker_MLflow_FeatureStore.ipynb` | PRODUCTION, TVM |
| `PS1_3d_VALIDATOR_SageMaker_MLflow_FeatureStore.ipynb` | PRODUCTION, VALIDATOR |
| `PS1_3d_GATE_OOS_Optimized_SageMaker_v4.ipynb` | analysis only (no deploy code) |
| `Chicago_PS1_Predictive_Failure.ipynb` | early analysis |
| `PS1_VALIDATOR_Device_Bus_Serial_Map.ipynb` | bus/serial mapping utility (kept, D-10) |
| `notebooks/cross_wired_daily_job.py` | Databricks job: builds the cross-wired export |
| `notebooks/ps1_build_features_daily.py` | NEW 16-Aug: daily feature-frame task (design D-2/D-3) |
| `notebooks/ps1_gold_complete_event.py` | NEW 16-Aug: PutEvents trigger after verifying gold is non-empty |
| `notebooks/ps1_batch_score_daily.py` | daily scoring job (Processing-job design; loads the endpoint's own model.tar.gz) |

### PS2 — cascading failure
| Notebook | Role |
|---|---|
| `PS2_Failure_Patterns_v2_5_4_Union_Minutes.ipynb` | current v2.5 production run family |
| `PS2_Failure_Patterns_v2_5_3_Storage_Safe.ipynb` | prior v2.5 iteration |
| `PS2_Serial_Grain_Analysis_v1_FIXED.ipynb` | serial-grain delivery (21-Jul) |
| `PS2_SageMaker_MLflow_FeatureStore.ipynb` | earlier SageMaker family |
| `PS2_Episode_Closure_Diagnostic_v1.ipynb`, `PS2_Overlap_Diagnostic_v2-4.ipynb` | diagnostics |
| `Chicago_PS2_Cascade_Analysis.ipynb` | early analysis |

### PS3 — root cause + severity
| Notebook | Role |
|---|---|
| `PS3_RootCause_Severity_SageMaker_Source_First_V26.ipynb` | PRODUCTION (V26). The Studio copy was destroyed 03-Aug; **the repo copy is the only one that exists** |
| `PS3_01_OOS_Spine_Build.ipynb`, `PS3_02_Taxonomy_And_Gaps.ipynb`, `PS3_03_RootCause_Models(_v2).ipynb` | staged v2 build family |
| `PS3_Calibration_Native_vs_ServiceNow.ipynb` | calibration study |
| `notebooks/ps3_root_cause_analysis/databricks_daily/` | daily incremental pipeline |
| `PS3_OOS_Contract_Verification_Databricks.ipynb` | read-only contract verification (reproduced 7,612,916 rows / 2,806 devices `[M 09-Aug]`) |

### PS4 — anomaly / fault clustering
| Notebook / job | Role |
|---|---|
| `PS4_FaultClustering_{GATE,TVM,VALIDATOR}(_PySpark).ipynb` | clustering per fleet |
| `PS4_SageMaker_MLflow_FeatureStore(_PySpark).ipynb` | SageMaker family |
| `Chicago_PS4_Anomaly_Detection.ipynb` | early analysis |
| `notebooks/ps4/ps4_device_daily_export.py`, `ps4_export_to_s3.py`, `ps4_fault_clustering/ps4_cluster_s3_export.py` | S3 export jobs |

### PS5 — reliability / remaining useful life
| Notebook | Role |
|---|---|
| `PS5_Reliability_Survival_v5_6.ipynb` | CURRENT production run (v5.6: 27.4 min, contract OK, leak-check PASS `[M 08-Aug]`) |
| `PS5_Reliability_Survival_v5_1 ... v5_5.ipynb` | version history (kept) |
| `PS5_OOS_Spine_and_Serial_Builder.ipynb` | spine/serial builder |
| `ps5_reliability_engine_v51.py` | engine module |
| `PS5_{GATE,TVM,VALIDATOR}_Device_Reliability_Analysis.ipynb`, `Chicago_PS5_Survival_RUL.ipynb` | earlier analysis family |

### Shared / platform
`notebooks/dim/export_device_serial_daily.py` (device-serial dim export),
`export_gold_to_s3.py`, `export_silver_to_s3.py`, `run_layer_silver.py`,
`run_layer_gold.py`, `validate_tables.py`, `validation/validate_{silver,gold}.py`,
`catalog/generate_data_catalog.py`, `ingestion/*` (Oracle ingestion),
`100/101_ServiceNow_*.py` (ServiceNow conformance).

---

## 3. (b) S3 locations for notebook outputs

Buckets: **gold** `cubic-mars-pm-s3-datalake-dev-gold-170202974600` (also holds
`chicago/gold` and `chicago/silver` lakehouse exports), **artifacts**
`cubic-mars-pm-s3-datalake-dev-artifacts-170202974600`.

| PS | S3 prefix (bucket) | Format | Read by |
|---|---|---|---|
| PS1 Path A (LIVE) | `chicago/device_ps1_cross_wired_daily/{gate,tvm,validator}` (artifacts) — the fleet is the OBJECT KEY, not a folder | Parquet | `ps1-xw-loader` |
| PS1 Path B (RETIRED 08-10) | `chicago/gold/device_ps1_cross_wired_daily` (gold) | Parquet | `ps1-rds-push` (cron disabled) |
| PS1 daily features (DESIGN) | `chicago/ps1/features/asof=<date>/fleet=<slug>/` | Parquet | batch scorer (not live yet) `[D]` |
| PS2 | `chicago/ps2_outputs` (both buckets granted) — migrated 08-Aug from `ps2_outputs`; **notebook still writes the OLD prefix until `PS2_PRODUCTION_EXPORT_PREFIX` is set** | Parquet | `ps2-rds-loader` |
| PS3 v2.5/V26 | `chicago/ps3_outputs` (artifacts) — production run `6a787954-…` landed 09-Aug; replay sandbox `ps3_replay_outputs` retained | Parquet | `ps3-v25-loader` |
| PS3 root-cause deep-dive | `chicago/ps3/rootcause_outputs` | CSV | `ps3-rc-loader` |
| PS3 hardened remediation | `chicago/ps3_hardened_remediation/runs` | Parquet | `ps3-v2-loader` |
| PS3 deep-dive (legacy) | `chicago/ps3_deepdive` | Parquet+CSV | `ps3-inference` |
| PS4 | `chicago/ps4`, `chicago/ps4/clustering/<device_type>/asof=<date>/` + `manifest/` (always read paths FROM the manifest) | Parquet | `ps4-rds-loader` |
| PS4 v3 | `chicago/ps4/v3` (reads `READY.json`) | Parquet | `ps4-v3-loader` |
| PS5 | `chicago/ps5/notebook_outputs/{gates,tvm,validators}/` — 36 files/run: 5 served CSVs per fleet + params JSONs + PNGs + `ps5_rds_load_manifest.json` | CSV (Parquet migration planned post-Boston) | `ps5-rds-loader` |
| DIM | `chicago/dim/device_serial` | Parquet | `dim-loader` |

IAM note that bites every migration: **every loader role is prefix-fenced** — an S3
move is also an IAM change. PS3 fences ListBucket with an `s3:prefix` condition
(fails instantly at survey); PS2 fences only GetObject (lists happily, fails at
first read). Read each policy; never infer one loader's failure mode from another.

---

## 4. (c) SageMaker endpoints for the PS1 and PS3 models

**Being Updated.**

(Per programme decision D-4 the serving approach is being reworked — daily batch
scoring via SageMaker Processing jobs triggered from Databricks (D-2/D-3) replaces
always-on endpoints. Until the change lands, the measured serving contract, image,
thresholds and endpoint inventory live in `docs/PS1_CANONICAL.md` §serving and
`docs/PS1_DECISIONS_15Aug2026.md`. This section will be rewritten when the new
serving path is deployed.)

---

## 5. (d) EventBridge — schedules and triggers, PS1-PS5

From the deploy scripts `[R]`, cross-checked against the 09-Aug live audit `[M]`.
All crons are UTC.

| Rule target (Lambda) | Schedule | State |
|---|---|---|
| `cubic-mars-dim-loader` | cron(45 5 * * ? *) — 05:45 | ENABLED `[M 09-Aug]` |
| `cubic-mars-ps1-rds-push` | cron(15 6 * * ? *) — 06:15 | **DISABLED 2026-08-10 11:55Z** (Path B retired; deploy.sh can re-enable — requires `PATHB_REVIVE=1` guard) |
| `cubic-mars-ps1-xw-loader` | cron(40 6 * * ? *) — 06:40 | ENABLED `[M 11-Aug]` |
| `cubic-mars-ps2-rds-loader` | cron(10 7 * * ? *) — 07:10 | ENABLED — never leave PS2 half-migrated overnight |
| `cubic-mars-ps4-rds-loader` | cron(10 7 * * ? *) — 07:10 | ENABLED |
| `cubic-mars-ps4-v3-loader` | cron(0 8 ? * MON *) — Mon 08:00 | ENABLED |
| `cubic-mars-ps5-rds-loader` | cron(20 7 * * ? *) — 07:20 | ENABLED |
| all three PS3 loaders (`rc`, `v2`, `v25`) | none | manual by design — safe to park mid-migration |
| `ps5_daily_scorer` | `[U]` — new Lambda in repo, schedule unverified | `[U]` |

Planned trigger (not live): Databricks fires **EventBridge PutEvents** after
verifying the gold layer is non-empty (`ps1_gold_complete_event.py`), starting the
PS1 daily scoring chain. Never wire S3 bucket notifications for this — the bucket
notification document is replace-not-update and already routes to
`ps1-cross-wired-push` `[D 15-Aug]`.

Also note: a nightly EventBridge-triggered loader re-loading UNCHANGED S3 bytes is
indistinguishable from a real refresh. PS1 sources have been static since 29-Jul
(data as-of 2026-04-11); the crons re-load the same artifacts daily by design until
the incremental Oracle dump lands.

---

## 6. (e) Loaders, schedulers, dispatchers, handlers, routes — the RDS push layer

### 6.1 Lambda inventory (repo `api/lambda/` `[R]`; 14 deployed with run evidence `[M 09-Aug]`)

| Lambda | Job | Invocation | Idempotency |
|---|---|---|---|
| `cubic-mars-ps1-xw-loader` | PS1 Path A loader — THE REFERENCE PATTERN | cron 06:40 | BEGIN / per-fleet SAVEPOINT / fleet-scoped DELETE; self-declaring contract EXPECTED total 786,525; ETag freshness verdict; lineage row outside the txn |
| `cubic-mars-ps1-rds-push` | PS1 Path B loader (retired) | disabled cron | DELETE-then-INSERT per run; **DELETE not category-scoped — fixed in commit `78c7118`, deploy state `[U]`** |
| `cubic-mars-ps2-rds-loader` | PS2 v2.5 loader (47 tables) | cron 07:10 | per-file computed_date delete |
| `cubic-mars-ps3-v25-loader` | PS3 V26/v2.5 loader (20 S3 tables -> Aurora `ps3_v25_*`; prefixes on the way in) | manual | per-run; `choose_run()` picks newest COMPLETE run — partial runs are skipped, never half-loaded |
| `cubic-mars-ps3-rc-loader` | PS3 root-cause/deep-dive CSV loader | manual | per-run; registered run `ps3_oos_20260804` in `ps3_model_runs` |
| `cubic-mars-ps3-v2-loader` | PS3 hardened-remediation loader | manual, run_id arg | per-run |
| `cubic-mars-ps3-rds-push` | older PS3 two-head loader (26-Jul family) | manual | per-run |
| `cubic-mars-ps3-inference` | PS3 deep-dive/inference support | manual | — |
| `cubic-mars-ps4-rds-loader` | PS4 clustering loader (manifest-driven) | cron 07:10 | per-run |
| `cubic-mars-ps4-v3-loader` | PS4 v3 loader | Mon 08:00 | reads READY.json |
| `cubic-mars-ps5-rds-loader` | PS5 CSV loader | cron 07:20 | per-table savepoints; `dry_run` doubles as S3-vs-Aurora reconciliation |
| `cubic-mars-dim-loader` | device/serial dimension loader | cron 05:45 | upsert |
| `cubic-mars-dashboard-api` | THE API — all dashboard routes + migrate/catalog/inspect actions | API GW `a9yuqt9j9b` | n/a |
| `ps5_daily_scorer` | PS5 daily scoring (new) | `[U]` | `[U]` |

ServiceNow dispatcher: `POST /ps1/servicenow-stage` writes `servicenow_staging` in
Aurora (a stand-in — the SQS FIFO -> ServiceNow REST integration has never been
built `[M 09-Aug, 6th audit]`).

### 6.2 The API layer (dashboard-api)

One Lambda, `handler.py` + `ps2_v25_routes.py`, behind HTTP API GW `a9yuqt9j9b`.
Route families (counts from the 08-Aug parse `[R]`): PS1 36 routes (incl. the 12
`xw-*` cross-wired family and `/ps1/device-360`, which fans out ~25 queries across
PS1-PS5 and runs 5.7-7.6 s against a 30 s API GW cap); PS2 ~18 core + ~25 v2.5;
PS3 ~23 core + 19 v2.5 (`/ps3/v25/<metric>` generic family over the ps3_v25_*
tables) + `/ps3/status`; PS4 12; PS5 9; dims + facilities + health.

Admin actions by payload (NOT HTTP): `migrate`, `purge`, `catalog`, `inspect`,
`recreate`. There is deliberately NO `sql` action.

Operational rules learned the hard way:
- `deploy.sh` for dashboard-api **runs migrate() unconditionally** (re-applies
  sql/08 re-seeding hardcoded PS2 rows, sql/18 deleting from
  ps3_severity_predictions) and **resets memory to 256 + replaces the whole env
  map**. To ship one SQL file use the `apply_sql` path; for code-only changes use
  `aws lambda update-function-code`.
- Migrations `sql/01-48` are registered in migrate(); **`sql/50-55` are NOT — they
  are applied manually via psql** (CloudShell VPC environment). sql/55 (16-Aug) is
  the collapse-health guard fix.
- Aurora is reachable ONLY from the VPC: use the CloudShell VPC environment for
  psql; fetch the secret (`cubic-mars-secret-rds-dev`) in the REGULAR CloudShell
  (the VPC one has no internet egress) and cross-paste the exports.

### 6.3 Run-pointer machinery (the #65 lesson, 16-Aug)

`ps3_model_runs` is the run registry; `v_ps3_latest_run` = newest train/batch_score
row. The 04-Aug rc-loader registration moved that pointer to a run that never loads
`ps3_incident_predictions`, and the `/ps3/collapse-health` guard silently served
`[]` for 12 days. Fixed by `sql/55`: the guard now serves the newest data-bearing
run under the pointer's run_id with true origin in `source_run_id`, and
`v_ps3_v25_severity_maturity` records the v25 family's severity coverage (today:
0% — all 54,239 episodes `(unlabelled)` / `unavailable_no_linked_label` `[M 16-Aug]`).
General rule: **"the latest X" is not "the X that produced this row"** — any route
that pins to a latest-run pointer must tolerate runs that don't feed its table.

---

## 7. (f) Dashboard elements per problem statement

### 7.1 What's on `main` today

`dashboard/` is one Vite + React 19 app (`cubic-dashboards`). Current UI =
**V4**, `dashboard/src/v4/` (19 files) mounted via `App.jsx` -> `V4Shell`.
Auth: `LoginPage` + `ProtectedRoute` (`src/auth`, `src/pages`). Runtime API base:
`.env` `VITE_API_BASE_URL` baked at build, overridable per environment at container
start via `docker-entrypoint.sh` -> `/config.js` -> `src/runtimeConfig.js`.

| Tab | Component | Primary feeds |
|---|---|---|
| Failure Prediction (PS1) | `V4PS1Overview.jsx` | `/ps1/predictions`, `/ps1/station-summary`, `/ps1/risk-trend`, `/ps1/leaderboard`, `/ps1/confusion`, `/ps1/threshold-sweep`, 12x `/ps1/xw-*` |
| Cascades (PS2) | `V4PS2Overview.jsx` | `/ps2/phi`, `/network`, `/paths`, `/ignition`, `/status` + 21x `/ps2/v25/*` |
| Root Cause (PS3) | `V4PS3Overview.jsx` | `/ps3/status` + 18x `/ps3/v25/*` (the v25 family) |
| Anomaly (PS4) | `V4PS4Overview.jsx`, `V4PS4Clusters.jsx` | `/ps4/weekly*`, `/ps4/cluster-*`, `/ps4/v3-status` |
| Remaining Life (PS5) | `V4PS5Overview.jsx` | `/ps5/device-rul`, `/ps5/serial-rul`, `/ps5/component-summary`, `/ps5/leaderboard`, `/ps5/importance`, `/ps5/coverage`, `/ps5/summary`, `/ps5/status`, `/ps5/serial-grain` |
| Device 360 | `V4Device360.jsx` + popup | `/ps1/device-360` (25 queries across PS1-PS5 + dims) |
| shared | `V4Locations.js` | `/ps1/facilities` (single naming authority: `dim_device_station` 2,183 facilities + `dim_station` 18 seeded) |
| shared | `V4Evidence.js` | recomposes `cross_ps` from device-360 — no endpoint of its own |
| kit | `V4Kit/Charts/ChartsPlus/DataTable/DeviceTable/GlobalSearch/theme` | — |

The **V1 shell is still routed** (`/dashboard/overview`, `/dashboard/city/:cityId`
-> `src/pages`, `src/components`) and hosts `PS3RootCauseTab.jsx`, which is the
screen that renders `/ps3/collapse-health` — the V4 PS3 tab uses the v25 family
instead. Do not delete `src/pages`/`components`/`context`/`data` as "old": they are
live routes.

### 7.2 Retired generations and unwired pieces

| Generation | Where |
|---|---|
| V1 | `src/pages` + `src/components` — still routed |
| V2 (final, real) | git history only since the 16-Aug sweep: `git show archive-sweep-base:dashboard/archive/v2-final/<file>` |
| V3 | git history: `git show archive-sweep-base:dashboard/archive/v3-final/<file>` |
| V4 unwired | git history: `git show archive-sweep-base:dashboard/archive/v4-unwired/<file>` |

Seven V2 panels still have no V4 equivalent (notably **Raise a work order**);
`/ps1/component-age` and fitment endpoints are still called with nothing rendering
them. Full list: lineage skill §10.3 / `V4_VISUAL_AUDIT.md`.

### 7.3 Known rendering caveats (16-Aug audit — read before demoing)

1. The V4 PS1 screen has **no data-vintage indicator** (PS2/PS3 carry a StatusBar
   with "Analysis as of ..."). Four of five PS1 sub-tabs carry no date at all.
2. `/ps1/station-summary` and `/ps1/risk-trend` are **26-Jul hand-seeded tables**
   — every PS1 headline number and the Estate card render from that seed.
3. V4 renders PS1 model stats from UI-layer constants; `/ps1/model-performance`
   serves the real scorecard (3 fleets, PASS, promoted `[M 16-Aug]`) but V4 does
   not call it.
4. Six PS1 routes still read Path B's frozen tables (`crosstab`, `coverage`,
   `serial-predictions`, `runs`, `load-audit`, part of `model-performance`).
5. PS1 data vintage is 2026-04-11 (sources static since 29-Jul).

---

## 8. Wired vs not-wired — live snapshot (16-Aug-2026)

LIVE and verified today `[M 16-Aug]`:
- `/ps3/summary` — full severity scorecard (lightgbm_multiclass, AUC-macro 0.9666,
  F1-macro 0.905, 34,696 incidents)
- `/ps3/collapse-health` — RESTORED today via sql/55 (TVM 57.89/42.11; GATE
  NULL-severity by design — the known degenerate GATE head)
- `/ps1/summary` + `/ps1/model-performance` — real 3-fleet scorecard
  (GATE AUC 0.8961/AP 0.9486, TVM 0.9040/0.9835, VALIDATOR 0.9956/0.9903)
- `/ps1/coverage` — leaderboard GATE 5 / TVM 5 / VALIDATOR 5
- Aurora content: `ps3_v25_*` 22 tables populated (episode facts 54,239, devices
  2,806, serials 2,762, facilities 366); `ps3_incident_predictions` 34,612

DARK or degraded (all with a cause, none mysterious):
- `/ps3/rootcause/drivers`, `/ps3/severity/drivers`, `/ps3/drivers/head` — return
  `[]` because `ps3_head_feature_importance` has 0 rows `[M 16-Aug]` (candidate
  #66 for the tracker)
- `ps3_severity_summary/_drivers/_predictions` — empty tables, blank legacy panels
- `ps3_v25_prediction_explainability` — 3 status rows for the whole estate (shap
  not installed in the SageMaker env; `pip install shap` fixes one of three causes)
- `ps1_prediction_explainability` — 26 rows only
- `ps5_weibull_params`, `ps5_cox_hazard_ratios` — declared, never loaded (params
  live in JSONs no loader reads)
- PS5 population gap: 3,899 of 5,434 roster devices have components but no RUL
  estimate — deliberately documentation-only, NOT a dashboard change `[D 08-Aug]`
- PS1 panels riding the 26-Jul seed (§7.3)

Browser-level pass (clicking through the open tab, console + network capture) is
pending the browser automation extension connecting; the matrix above is from code +
live API + database evidence.

---

## 9. Publishing the dashboard to ECR — the team runbook

**The buildable unit is `dashboard/`, not `dashboard/src/v4`.** src/v4 is source
components; the Docker build needs `package.json`, `index.html`, `App.jsx`, `src/`,
`public/`, `nginx.conf`, `docker-entrypoint.sh` — all under `dashboard/`. The
multi-stage Dockerfile builds the Vite bundle with Node 20 and serves it with
nginx on **port 8080** (`/healthz` endpoint for ALB/ECS checks). One image serves
every environment: `API_BASE_URL` env at container start rewrites `/config.js`
(build-time `VITE_API_BASE_URL` is only the fallback).

```bash
# from the repo root, on a machine with Docker (or CodeBuild)
cd dashboard
GIT_SHA=$(git rev-parse --short HEAD)
ACCOUNT=170202974600
REGION=us-east-1
REPO=dashboard/reactui        # verify: aws ecr describe-repositories

aws ecr get-login-password --region $REGION |
  docker login --username AWS --password-stdin $ACCOUNT.dkr.ecr.$REGION.amazonaws.com

docker build \
  --build-arg VITE_API_BASE_URL=https://a9yuqt9j9b.execute-api.us-east-1.amazonaws.com \
  -t $ACCOUNT.dkr.ecr.$REGION.amazonaws.com/$REPO:$GIT_SHA .

docker push $ACCOUNT.dkr.ecr.$REGION.amazonaws.com/$REPO:$GIT_SHA
```

Rules: tag with the git SHA (never `latest` — the mutable `:latest` pin is a known
defect on the PS3 side); run the container with `API_BASE_URL` set per environment;
ECS task must expose 8080 and use `/healthz`. The `dashboard/reactui` ECR repo held
3 images at the 09-Aug audit `[M]`.

---

## 10. Stale-file sweep — EXECUTED 16-Aug (evening), PK instruction

Recovery point for everything below: **git tag `archive-sweep-base`** (= `a80cee3`,
the last pre-sweep commit, pushed to origin). Recover any file with
`git show archive-sweep-base:<path> > <file>`.

| Item | Status |
|---|---|
| `dashboard/archive/` (24 files: v2-final 18, v3-final 4, v4-unwired 2) | REMOVED from the tree 16-Aug — supersedes the 08-Aug keep decision |
| `dashboard/backfill/` (4 SQL) | REMOVED 16-Aug — canonical copies live in `api/lambda/cubic-mars-dashboard-api/sql/` |
| `dashboard/patch_ps1_validator.py`, `dashboard/V3_BUILD_SPEC.md` | REMOVED 16-Aug |
| `notebooks/ps1_failure_prediction/archive/` (17 files incl. the mlflow purge/restore + teardown scripts) | REMOVED 16-Aug |
| `src/v2`, `src/_retired`, `src/_v51verify`, `v4/_to_delete`, `dist/`, `.vite/` | removed locally 16-Aug (empty dirs + gitignored build junk, ~12 MB — never tracked) |
| Tracked-junk scan (`__pycache__`, `.pyc`, `.bak`, `.tmp`, locks) | CLEAN — zero hits across all tracked files `[M 16-Aug]` |
| `.gitattributes` | still missing — the CRLF trap remains for any Linux checkout (`* text=auto eol=lf` is the fix, deferred) |

---

## 11. Verification commands (safe, read-only)

```bash
# live table/column/row-count catalog, straight from information_schema
aws lambda invoke --function-name cubic-mars-dashboard-api \
  --cli-binary-format raw-in-base64-out \
  --payload '{"action":"catalog"}' catalog.json --region us-east-1

# API smokes (regular CloudShell — the VPC one has no internet)
API=https://a9yuqt9j9b.execute-api.us-east-1.amazonaws.com
curl -s "$API/ps3/summary?city=CHI" | head -c 300
curl -s "$API/ps3/collapse-health?city=CHI"
curl -s "$API/ps1/summary?city=CHI" | head -c 300
curl -s "$API/ps1/facilities?city=CHI" | python3 -c \
 "import sys,json;d=json.load(sys.stdin);print(len(d),'facilities')"

# EventBridge live truth
aws events list-rules --query 'Rules[?contains(Name,`cubic`)].{N:Name,S:State,C:ScheduleExpression}' --output table

# loader run evidence (deployed != ran)
aws logs describe-log-streams --log-group-name /aws/lambda/cubic-mars-ps1-xw-loader \
  --order-by LastEventTime --descending --max-items 1
```

## 12. Keeping this current

Update this file (and bump the date) whenever: a notebook writes to a new prefix or
changes format; a loader is added/retired/repointed; a table, route, or panel feed
changes; an item in §8's dark list is fixed (move it to a dated Resolved note);
the endpoint section (§4) is rewritten after the serving rework lands.
