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
