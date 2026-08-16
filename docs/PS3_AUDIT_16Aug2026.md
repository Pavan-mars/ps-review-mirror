# PS3 — Complete Audit vs the Target Daily-Inference Architecture

**16-Aug-2026.** Same framework as the PS1/PS2 audits. Tags: `[M]` measured live,
`[R]` repo, `[D]` decision, `[U]` unverified.

**Target (PK):** medallion-complete EventBridge trigger -> SageMaker container daily
inference -> S3 -> Lambda -> RDS -> dashboard. PS3, like PS1, needs true daily
inference.

**Headline verdict:** the strongest data plane of the five after PS5 — the V26
production run is loaded and verified to the row, the dashboard family serves, and
the 16-Aug guard fix restored the last dark check. But all three PS3 loaders are
manual BY DESIGN, no trigger exists, and the v25 severity signal is structurally
unlabelled until the severity head ships.

## (a) PS3 SageMaker notebooks

`notebooks/ps3_root_cause_analysis/` `[R]`:

| Notebook / asset | Role | Status |
|---|---|---|
| `PS3_RootCause_Severity_SageMaker_Source_First_V26.ipynb` | PRODUCTION (V26, hardware-OOS contract). The Studio copy was destroyed 03-Aug (renamed .invalid, zero bytes) — **the repo copy is the only one in existence** | current |
| `PS3_01_OOS_Spine_Build.ipynb`, `PS3_02_Taxonomy_And_Gaps.ipynb`, `PS3_03_RootCause_Models(_v2).ipynb` | staged v2 build family | kept |
| `PS3_Calibration_Native_vs_ServiceNow.ipynb` | calibration study | kept |
| `databricks_daily/` | daily incremental pipeline components | present `[R]`, deployment state `[U]` |
| `ps3_oos_engine_v2.py`, `ps3_oos_spine.py`, `build_ps3_v2_notebook.py`, `make_ps3_v2_engine.py` | engine + builders | kept |
| Contract verification notebook (read-only, Databricks) | independently reproduced 7,612,916 rows / 2,806 devices from silver | run 09-Aug `[M]` |

V26 run-mode interlocks are deliberate `[D 08-Aug]`: RUN_MODE defaults to REPLAY,
non-production runs are prefix-redirected to the replay sandbox, completion markers
withheld. **Never flip the REPLAY default for convenience.**

## (b) S3 locations for PS3 outputs

| Path (artifacts bucket unless noted) | What | Loader |
|---|---|---|
| `chicago/ps3_outputs` | V26/v2.5 production — run `6a787954-…` (09-Aug PRODUCTION, 20 tables/run; equals the 03-Aug replay figures exactly) `[M]` | `ps3-v25-loader` |
| `ps3_replay_outputs` | replay sandbox — retained deliberately | same loader (parked historically) |
| `chicago/ps3/rootcause_outputs` | root-cause / deep-dive CSVs | `ps3-rc-loader` |
| `chicago/ps3_hardened_remediation/runs` | hardened remediation runs | `ps3-v2-loader` |
| `chicago/ps3_deepdive` | legacy deep-dive | `ps3-inference` |

## (c) SageMaker endpoint for the PS3 model

One PS3 endpoint InService (the 4th of the account's 4) `[M 09-Aug, 3rd clean
week]`. Two known defects distinct from PS1's: ECR `cubic-pdm/mars-ps3` IS live and
**pins the mutable `:latest` tag** (never sweep it up in a PS1 ECR cleanup, and fix
the pin when touched) `[R 15-Aug canon]`; PS3 model-package sprawl stands at 14
unpruned `[M 09-Aug]` (tracker #44). The serving rework (Processing-job pattern
from PS1's D-2) is expected to cover PS3's daily inference — endpoint disposition
for PS3 is **not yet formally decided** `[U — decide during rollout]`.

## (d) EventBridge for PS3

**None.** All three PS3 loaders have no schedule BY DESIGN (safe to park
mid-migration, unlike PS2) `[M 08/09-Aug]`. No medallion-complete trigger exists.
Target chain: reuse the PS1 PutEvents design (D-3) once deployed; add loader rules
only after a daily run exists.

## (e) Loaders, dispatchers, handlers, routes for RDS

| Component | Key facts |
|---|---|
| `cubic-mars-ps3-v25-loader` | loads the 20 S3 tables into Aurora as `ps3_v25_*` (prefixes on the way in); `choose_run()` takes the newest COMPLETE run — partial runs skipped, never half-loaded; echoes its prefix (unlike PS2's) `[R+M]` |
| `cubic-mars-ps3-rc-loader` | root-cause/deep-dive CSV loader (created 04-Aug). Its run registration `ps3_oos_20260804` in `ps3_model_runs` moved the latest-run pointer and silently blanked `/ps3/collapse-health` for 12 days (#65) `[M 16-Aug]` |
| `cubic-mars-ps3-v2-loader` | hardened-remediation loader, run_id arg |
| `cubic-mars-ps3-rds-push` | 26-Jul two-head family loader (legacy) |
| `cubic-mars-ps3-inference` | deep-dive/inference support |

Routes: `/ps3/summary` (FIXED — full severity scorecard: lightgbm_multiclass,
AUC-macro 0.9666, F1-macro 0.905, 34,696 incidents `[M 16-Aug]`); `/ps3/status`;
19x `/ps3/v25/<metric>` generic family; legacy two-head family incl.
`/ps3/rootcause|severity/summary` (gate-aware, GATE severity masked NULL by
design); `/ps3/collapse-health` — RESTORED 16-Aug by sql/55 (guard re-keyed to the
newest data-bearing run, provenance in `source_run_id`; verified TVM MAJOR 0.5789 /
CRITICAL 0.4211). **DARK:** `/ps3/rootcause/drivers`, `/ps3/severity/drivers`,
`/ps3/drivers/head` return `[]` because `ps3_head_feature_importance` holds 0 rows
`[M 16-Aug]` (tracker candidate #66).

Data-quality facts to brief: `ps3_v25_prediction_explainability` = 3 status rows
across 2,806 devices (shap not installed in the SageMaker env — one `pip install
shap` fixes a third of the causes); `ps3_v25_device_episode_fact.dashboard_severity`
is '(unlabelled)' on ALL 54,239 episodes (`severity_status =
unavailable_no_linked_label`) — coverage tracked by the new
`v_ps3_v25_severity_maturity` view; **do not re-point the collapse guard's shares
at v25 until the severity head ships** `[M 16-Aug]`.

## (f) PS3 elements on the dashboard

`V4PS3Overview.jsx` -> `/ps3/status` + the `/ps3/v25/*` family (episodes,
device-summary, device-reliability, serial-reliability, facility-rollup,
model-scorecard, feature-importance, label-maturity, causal-balance/effects,
commanded-split, component-summary, repeat-interval, run-status, run-stage-audit,
evidence-audit, source-audit, column-profile) `[R, 16-Aug grep]`. Carries a vintage
StatusBar. The V1 shell's PS3RootCauseTab (still routed) is the screen that renders
`/ps3/collapse-health`. PS3 episode grain is SETTLED: 3-day gap-sessionised causal
episodes, 1:N to PS1 device-days — amend contract text, never the code `[D 09-Aug,
PK ruling]`.

## Verdict vs target

| Target step | Reality | Verdict |
|---|---|---|
| Trigger on medallion-complete | none (reuse PS1 PutEvents design) | MISSING |
| Daily inference in SageMaker container | V26 runs are manual; daily incremental pipeline components exist in repo, undeployed | MISSING |
| Outputs to S3 | production prefix verified to the row | RIGHT |
| Lambda -> RDS | v25 loader proven (20/20 tables); manual by design | RIGHT (schedule later, deliberately) |
| Dashboard refresh | v25 family + fixed guard live; drivers dark (#66); severity unlabelled pending the head | PARTIAL |


---

## Addendum A — Live verification, 16-Aug-2026 evening [M]

**RDS (PS3 family — 53 tables, 167,029 rows, 24 views):**

| object | kind | rows | cols |
|---|---|---:|---:|
| ps3_v25_device_day | table | 54,239 | 13 |
| ps3_v25_device_episode_fact | table | 54,239 | 79 |
| ps3_incident_predictions | table | 34,612 | 20 |
| ps3_v2_shap_incident | table | 4,800 | 24 |
| ps3_device_predictions | table | 3,412 | 11 |
| ps3_v25_device_reliability | table | 2,806 | 18 |
| ps3_v25_device_summary | table | 2,806 | 11 |
| ps3_v25_serial_reliability | table | 2,762 | 12 |
| ps3_serial_predictions | table | 2,515 | 17 |
| ps3_v2_device_serial_component | table | 1,413 | 14 |
| ps3_v2_device_reliability | table | 927 | 18 |
| ps3_v2_device_serial | table | 927 | 13 |
| ps3_v2_severity_action_queue | table | 472 | 28 |
| ps3_v25_facility_rollup | table | 366 | 13 |
| ps3_v2_facility_hotspots | table | 320 | 12 |
| ps3_leakage_scan | table | 59 | 7 |
| ps3_v25_causal_balance | table | 54 | 6 |
| ps3_v2_shap_global | table | 44 | 18 |
| ps3_v2_component_taxonomy | table | 29 | 11 |
| ps3_head_leaderboard | table | 28 | 14 |
| ps3_v2_driver_importance | table | 22 | 14 |
| ps3_v25_run_status | table | 19 | 15 |
| ps3_head_class_metrics | table | 16 | 11 |
| ps3_v25_component_summary | table | 14 | 9 |
| ps3_v25_repeat_interval | table | 14 | 13 |
| ps3_v25_model_feature_importance | table | 12 | 7 |
| ps3_v2_model_comparison | table | 10 | 35 |
| ps3_v2_component_reliability | table | 9 | 16 |
| ps3_v25_model_scorecard | table | 8 | 15 |
| ps3_v25_run_stage_audit | table | 8 | 11 |
| ps3_head_summary | table | 7 | 25 |
| ps3_v25_root_cause_evidence_audit | table | 7 | 7 |
| ps3_v25_source_column_profile | table | 7 | 11 |
| ps3_v25_causal_effects | table | 6 | 24 |
| ps3_v2_display_policy | table | 6 | 11 |
| ps3_v2_run_scorecard | table | 5 | 17 |
| ps3_v2_causal_effects | table | 4 | 31 |
| ps3_v2_promotion_status | table | 4 | 13 |
| ps3_category_coverage | table | 3 | 12 |
| ps3_v25_commanded_split | table | 3 | 7 |
| ps3_v25_label_maturity | table | 3 | 10 |
| ps3_v25_prediction_explainability | table | 3 | 14 |
| ps3_v2_readiness | table | 3 | 13 |
| ps3_model_runs | table | 2 | 15 |
| ps3_severity_summary | table | 1 | 22 |
| ps3_v25_oos_source_audit | table | 1 | 14 |
| ps3_v2_data_freshness | table | 1 | 12 |
| ps3_v2_runs | table | 1 | 7 |
| ps3_device_metrics | table | 0 | 7 |
| ps3_head_feature_importance | table | 0 | 8 |
| ps3_prediction_explainability | table | 0 | 6 |
| ps3_severity_drivers | table | 0 | 6 |
| ps3_severity_predictions | table | 0 | 12 |
| v_ps3_category_coverage | view | - | 16 |
| v_ps3_collapse_health | view | - | 7 |
| v_ps3_device_360 | view | - | 20 |
| v_ps3_device_all | view | - | 22 |
| v_ps3_device_risk | view | - | 17 |
| v_ps3_head_gates | view | - | 10 |
| v_ps3_latest_run | view | - | 8 |
| v_ps3_rollup_all | view | - | 14 |
| v_ps3_run_registry | view | - | 11 |
| v_ps3_serial_all | view | - | 17 |
| v_ps3_serial_risk | view | - | 13 |
| v_ps3_two_head_scorecard | view | - | 21 |
| v_ps3_v25_severity_maturity | view | - | 7 |
| v_ps3_v25_status | view | - | 4 |
| v_ps3_v2_causal | view | - | 32 |
| v_ps3_v2_current | view | - | 7 |
| v_ps3_v2_policy | view | - | 11 |
| v_ps3_v2_queue | view | - | 30 |
| v_ps3_v2_rootcause | view | - | 17 |
| v_ps3_v2_rootcause_concentration | view | - | 10 |
| v_ps3_v2_rootcause_rollup | view | - | 12 |
| v_ps3_v2_scorecard | view | - | 19 |
| v_ps3_v2_shap | view | - | 24 |
| v_ps3_v2_table_status | view | - | 2 |

Full column-level schemas for every object: `docs/reference/RDS_LIVE_INVENTORY_16Aug2026.md`
(generated from the live catalog capture).

**Confirmations and findings:**
- No EventBridge rule for any PS3 loader — CONFIRMED (by-design manual). Loader last-run
  evidence: v2-loader 29-Jul, rc-loader 04-Aug, v25-loader 09-Aug — matches the manual run history.
- Endpoint `chicago-ps3-rootcause-v1` InService -> model
  `chicago-ps3-root-cause-2026-07-13-07-02-42-300` — **`PrimaryContainer.Image` returned
  None**, meaning the model is defined with a `Containers[]` list; the exact image binding
  needs one follow-up (`describe-model` full). ECR `cubic-pdm/mars-ps3` holds EXACTLY ONE
  image, tagged `latest`, pushed 13-Jul 06:56Z — six minutes before the model's creation
  timestamp. Probable but UNCONFIRMED binding; the mutable-`latest` defect stands either way.
- `/ps3/summary` and `/ps3/collapse-health` both serving (re-confirmed in the same batch).

---

## Addendum B — SET W self-checks, 16-Aug-2026 late evening [M]

- v25 loader `{"dry_run": true}`: exactly ONE complete run visible
  (computed_date 2026-04-11, run 6a787954), 20/20 tables, 117,377 rows, 0 errors,
  unexpected: `[_runs]` only — `choose_run()` healthy.
- rc loader `{"dry_run": true}`: would load run `ps3_oos_20260804` (manifest
  run_kind=train; 3 artifacts). **`ps3_serial_predictions.csv` is SKIPPED — "not
  readable"** — only 3 CSVs exist at the prefix; the 2,515 RDS serial rows are from
  the earlier family. **`ps3_head_feature_importance` is NOT in the rc artifact
  set** — so #66 cannot be fixed by re-loading this run; it needs a fresh export
  from the notebook or retirement of the three drivers routes.
- **v2 loader WARNING:** the newest hardened run `ps3_20260801T191241Z` is mostly
  EMPTY — 13 of 17 datasets read 0 rows (only run_scorecard 3, display_policy 13,
  readiness 3, data_freshness 1). The populated `ps3_v2_*` content in Aurora
  (shap_incident 4,800, action_queue 472, ...) came from earlier runs. **Do NOT
  invoke `{"action":"load"}` on the v2 loader while this hollow run is newest.**
- Correction to (b): hardened-remediation runs live in the **GOLD** bucket
  (`chicago/ps3_hardened_remediation/runs/` — loader echo), not artifacts.
- Deployed-function inventory: of (e)'s five components only rc / v2 / v25 exist
  live — `cubic-mars-ps3-rds-push` and `cubic-mars-ps3-inference` are NOT deployed
  (repo-only or since deleted).
- **PS3 endpoint: 0 invocations in 30 days** (first-ever measurement) — the
  D-4-extension deletion evidence for `chicago-ps3-rootcause-v1` is complete.
- W1 (re-paste received): `describe-model` shows the model carries NO direct
  image — it wraps **model-package `chicago-ps3-root-cause/14`** (Mode
  SingleModel; the newest of the 14 sprawled versions). The `mars-ps3:latest`
  pin therefore sits inside the package's InferenceSpecification — fix it there
  during exit-plan step 6; an optional `describe-model-package` on version 14
  closes the final inch. (`DeploymentRecommendation: FAILED` is
  inference-recommender noise, not a serving fault.)
