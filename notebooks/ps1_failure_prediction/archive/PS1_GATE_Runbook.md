# PS1 GATE Failure Prediction — Notebook Runbook

`PS1_GATE_Failure_Prediction.ipynb` — CUBIC MARS Chicago / Ventra. Predicts a **chargeable SLA
failure within 3 days** (`will_fail_3d`) for **GATE** devices (RVG/HBG/SAG), scored per DEVICE_ID
and attributed to each device's component serials. Grounded in
`sql/gold/device_ps1_daily__create.sql` (107 cols) + the 2026-07-17 data catalog.

## How to run

**Default path — SageMaker Studio reading the S3 gold/silver export** (no Spark needed):
1. In Databricks, run `notebooks/export_gold_to_s3.py` **and** `notebooks/export_silver_to_s3.py`
   once. They write parquet to
   `s3://cubic-mars-pm-s3-datalake-dev-gold-170202974600/chicago/{gold,silver}/<table>`
   (`device_ps1_daily` and `hw_config_current`).
2. In Studio: `%pip install "numpy<2" pyarrow s3fs xgboost lightgbm catboost optuna shap sagemaker-mlflow`.
3. `DATA_SOURCE="auto"` (default) already points `GOLD_PARQUET` / `HWCONFIG_PARQUET` at those S3
   folders — no config change needed. **Run All.** The loader uses pyarrow predicate pushdown to read
   only the device category's rows (not all ~2.9M device-days). The **env probe** cell prints exactly
   what it can see (Spark? parquet path? SQL params?) so a mis-set source is obvious immediately.

**Alt — run on a Databricks single-user cluster:** attach + Run All; `spark` is auto-detected, zero
config. **Alt — databricks-sql:** set `DBX_HOST` / `DBX_HTTP_PATH` + the `DATABRICKS_TOKEN` env var +
`DATA_SOURCE="databricks_sql"`.

Expect ~15–40 min on the full slice; lower `N_OPTUNA_TRIALS` / `TUNE_SAMPLE_ROWS` to go faster.

## What it does (why it's robust)
- **No future leakage by construction** — every gold feature is same-day-or-past; the label only
  looks 1–3 days forward. Guards defend against *concurrency* and tuning/test leakage:
  - Same-day outage/OOS columns (`chargeable_outage_*`, `max_failure_level`, `oos_event_count`, …)
    are **held out of the champion by default** (`USE_CONCURRENT_FEATURES=False`) so the model
    predicts *before* the outage. An ablation toggle re-adds them to show the lift.
  - **Solo-AUC leakage scan** drops any single feature with AUC > 0.95 (v7.0 gate).
  - **Purged, embargoed temporal split** — most-recent 20% of dates is the untouched test set; the
    last 14 days of train are purged so forward labels can't bleed across the boundary.
- **Device-grouped, strictly-past feature engineering** — lag/rolling/volatility/trend/tenure +
  a comms×unavailability stress proxy + light calendar (quarter omitted to curb GATE calendar
  over-reliance).
- **Bake-off**: LogReg, RandomForest, HistGBM, XGBoost, LightGBM, CatBoost, a weighted vote
  (RF 0.45 / XGB 0.35 / Cat 0.20) and a **time-aware stacked** model.
- **Optuna** tunes the boosters on **cross-validated AP over the purged folds** — never on a fixed
  val set or the test set (this is the fix for the 2026-07-13 GATE `val AUC 1.000` over-fit).
- **Calibration**: Platt/sigmoid (isotonic collapsed GATE AUC before, so it's avoided), with a
  guard that rejects any calibration that degrades AUC.
- **Gates (v7.0, never raw accuracy)**: AP/PR-AUC + ROC-AUC + **recall floor 0.70** at the
  operating threshold + precision@K. Operating threshold = meets the recall floor at max precision.
- **Scoring + serial attribution**: most-recent day per device → 3-day risk (+ 7/14-day secondary),
  risk band, decision, top-3 SHAP drivers; then exploded to component serials via
  `silver.hw_config_current` for work-order routing.

## Feature sources (in-notebook enrichment)

Beyond `device_ps1_daily`, the notebook joins four extra sources **in-notebook** (no new gold table),
each leakage-safe (prior-day windows) and independently guarded — a missing S3 export just prints
"skipped" and the run continues:

- `silver.usage_lifecycle_daily` (S20) — cumulative wear + maintenance activity (7-day-prior rolling
  tech-logins / maint-events / maint-mode).
- `silver.station_network_daily` (S27) — prior-7-day station co-failure stress at the device's facility.
- `silver.read_tap_daily` (S22) — reader reject-rate + null-status rate (same-day + prior-7d).
- `silver.hw_config_current` — device-level component-age min/mean/max/count.

On top of these it builds **cross-source derived features**: usage intensity / acceleration,
maintenance burden, component-age span, reader reject/null-rate delta vs the 7-day baseline, a
station-stress composite, and age×recent-failure / wear×criticals interactions. Everything flows
through the same solo-AUC leakage scan + NZV prune, so anything that doesn't hold up per device is
dropped automatically. Toggle any source off via the `ENRICH_*` CONFIG flags; set `ENRICH_SOURCES=False`
to fall back to gold-only features. **You must also export the silver tables** (`export_silver_to_s3.py`)
for the joins to resolve.

## Key CONFIG knobs
| Knob | Default | Note |
|---|---|---|
| `DATA_SOURCE` | `auto` | spark → databricks-sql → parquet |
| `RECALL_FLOOR` | 0.70 | GATE (TVM = 0.80) |
| `USE_CONCURRENT_FEATURES` | False | True = include same-day outage signals (ablation) |
| `EMBARGO_DAYS` | 14 | purge = max label horizon |
| `N_OPTUNA_TRIALS` | 30 | raise for a deeper search |
| `TUNE_SAMPLE_ROWS` | 400000 | cap tuning rows for speed |
| `REGISTER_MODEL` / `MLFLOW_TRACKING_URI` | False / "" | set the SageMaker MLflow ARN to log/register |
| `WRITE_SCORES_TO_DBX` | False | True writes scored + attribution tables back to UC |

## Outputs

Each notebook writes to a **per-device folder** under `PS1_outputs_pavan/` (relative to the
notebook's working directory): `PS1_outputs_pavan/gates/`, `PS1_outputs_pavan/tvm/`,
`PS1_outputs_pavan/validators/`. Point elsewhere by editing `OUT_DIR` in CONFIG.

Files (GATE example, `gate_` prefix): `run_console_log.txt` (**the full printed output of every
cell**, captured live) · `gate_leaderboard.csv` · `gate_leakage_scan.csv` · `gate_feature_manifest.json` ·
`gate_tuned_params.json` · `gate_champion_summary.json` (champion + gates + operating point) ·
`gate_feature_importance.csv` + `gate_top_drivers.png` · `gate_device_scores.csv` (per DEVICE_ID) ·
`gate_component_attribution.csv` (per component serial) · `gate_champion_bundle.joblib` +
`gate_serving_manifest.json`. (TVM/VALIDATOR use `tvm_`/`validator_` prefixes.)

## Serving (deployment gate — do not skip)
The champion serializes to `gate_champion_bundle.joblib`. Before any endpoint: build `inference.py`
(see `docker/inference_ps1.py`), pin `requirements.txt` to the training kernel with `numpy<2`, and
run a **local `/ping` + `/invocations` test** — this is exactly what the failed 2026-07 endpoints
skipped. Then register to `cubic-mars-models-gate-ps1-dev` and deploy per the `chicago-next-steps`
skill.

## TVM & Validator (same engine, next)
Identical notebook; only the CONFIG block + a few category notes change:
- **TVM** — `DEVICE_CATEGORY="TVM"`, `RECALL_FLOOR=0.80`; TVM-rich features are sales/revenue
  (`tvm_sale_daily`, `use_revenue_daily`) and comms; tap/M401 are ~0 for TVM.
- **VALIDATOR** — `DEVICE_CATEGORY="VALIDATOR"`; label comes from the **`device_event` OOS
  Set** source (R6-1) already in `device_ps1_daily`, not the availability feed. Validate the
  positive rate/label balance first (v7.0 lists validators as PS1-excluded pre-rebuild — the
  July foundation added the OOS label).
