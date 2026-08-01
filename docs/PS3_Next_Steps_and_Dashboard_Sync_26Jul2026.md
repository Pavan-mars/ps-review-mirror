# PS3 next steps, PS1 fixes, and the RDS-only dashboard pass
CUBIC MARS — Chicago / CTA-Ventra · 26 Jul 2026

Everything below is grounded in a file in the repo or a live response from
`https://a9yuqt9j9b.execute-api.us-east-1.amazonaws.com`. Where a number is a
live probe it is marked **(probed)**.

---

## 1. Where PS3 actually stands right now

Probed just now, city `CHI`:

| Route | Result | Reading |
|---|---|---|
| `/ps3/severity/summary` | `[]` **(probed)** | two-head scorecard has no rows |
| `/ps3/rootcause/summary` | `[]` **(probed)** | " |
| `/ps3/device-predictions` | `[]` **(probed)** | no device grain in RDS |
| `/ps3/serial-predictions` | `[]` **(probed)** | no serial grain in RDS |
| `/ps3/rootcause/drivers` | `[]` **(probed)** | no per-head SHAP |
| `/ps3/summary` | real row **(probed)** | shipped severity model: macro-AUC 0.9666, macro-F1 0.9050, 34,696 OOS incidents, endpoint `chicago-ps3-rootcause-v1`, MLflow v8 |
| `/ps3/drivers` | 8 real rows **(probed)** | `sn_event_code_id` SHAP 0.7916 leads |
| `/ps3/devices` | real rows **(probed)** | TVM test macro-F1 0.898, Gates test macro-F1 0.493 |
| `/ps3/predictions` | 5 rows keyed `INC-PS1-0001…` **(probed)** | **test data** from the 13-Jul bridge smoke test |

So: PS3 has **real model-level metrics** in RDS and **no device or serial grain
at all**. That gap is exactly what the PS3 v2 notebook was built to close.

---

## 2. PS3 — the run order

The v2 package is already written to `notebooks/ps3_root_cause_analysis/`:

- `PS3_v2_OOS_Serial_SageMaker.ipynb` — 37 cells, nbformat-valid, every code cell parses
- `ps3_serial_grain.py` — the component fan-out module (functionally tested)
- `ps3_oos_engine_v2.py` — 1,421 lines, AST clean
- `make_ps3_v2_engine.py` / `build_ps3_v2_notebook.py` — the derivation scripts

**Step 1 — run the notebook in SageMaker.**
It writes to `PS3_v2_outputs/` with `RDS_TABLE_PREFIX = "ps3v2_"` and
`V2_SUFFIX = "oos_v2"`. It cannot touch the live endpoint: `create_endpoint`,
`delete_endpoint`, `update_endpoint` and `.deploy(` all appear **zero** times,
and `chicago-ps3-rootcause-v1` appears only as `LIVE_ENDPOINT_DO_NOT_TOUCH`.

Two assertions will stop the run rather than produce a wrong answer:
- `ASSERT_OOS_SPINE` raises if the incident spine has been pre-filtered to
  chargeable events.
- `FORBID_CHARGEABLE_FILTER` raises if a chargeable predicate reaches the
  feature build.

**Step 2 — confirm the serial fan-out is real.**
On the test frame the module produced 1,063 incidents → 2,465
incident-component rows (2.32×) → 139 serial rows across 60 devices. The check
to run on the real output is simply: `n_serial_rows > n_device_rows`. The
previous run failed this (927 = 927, identical values on all 927 pairs) because
gold's `hw_best_match` CTE ends `WHERE rn = 1`, collapsing to one component per
incident. v2 reads `silver.hw_config_current` directly at notebook level — **no
gold change**, as you required.

**Step 3 — the bridge fires.**
The notebook writes `manifest.json`; `cubic-mars-ps3-rds-push` triggers on it
and loads `ps3_head_scorecard`, `ps3_head_feature_importance`,
`ps3_incident_predictions`, `ps3_device_predictions`, `ps3_serial_predictions`.

**Step 4 — apply migrations, then reload the tab.**
`sql/15` + `sql/17` are already in `migrate()`. `sql/18` is new (below). Once
rows land, all three PS3 sub-tabs populate with no further code change.

---

## 3. What I changed today

### `api/lambda/cubic-mars-dashboard-api/handler.py`

**Champion models now lead.** This was the concrete defect: `/ps1/leaderboard`
ordered by `device, lb_rank`, which for Gates put **CatBoost at rank 1
(`is_champion: false`)** and buried the deployed champion **LightGBM (Optuna)
at rank 4** **(probed)**. Now:

- `/ps1/leaderboard` → `ORDER BY device, is_champion DESC, auc DESC NULLS LAST, ap DESC NULLS LAST, lb_rank`
- `/ps1/summary` → `ORDER BY promoted DESC, test_auc DESC NULLS LAST, device`
- `/ps1/model-performance` → sorted `promoted` first, then test AUC descending
- `/ps3/severity/summary` and `/ps3/rootcause/summary` → `ORDER BY gate_pass DESC, test_f1_macro DESC, device_category`
- `/ps3/devices` → `ORDER BY f1_macro DESC, device`

**Two leakage vectors closed.**

1. `s3_metrics` no longer carries `train_*` / `val_*`. GATE reported **train AUC
   1.0000 and val AUC 1.0000** against a held-out 0.9038 **(probed)** — drawing
   those three bars side by side on a client screen presents memorisation as
   model quality. The over-fit fact is not hidden; it is still served as the
   boolean `ps1_failure_summary.overfit_flag`.
2. `/ps3/devices` now serves the **test split only**. It was serving train and
   val rows for the same device.

**Hardcoded numbers removed.** `_acc = {"TVM": 0.7033, "GATE": 0.9950}` is gone.
`ps1_model_performance` has no accuracy column, so accuracy is now `null` and the
tab renders an em-dash rather than a literal with no row behind it. Accuracy is
in any case not a gate for PS1 — at 0.5% prevalence, "never fails" scores 99.5%.

**Test data fenced off.** `/ps3/predictions` now excludes `incident_id LIKE
'INC-PS1-%'` — the synthetic rows the 13-Jul PS1→PS3 bridge smoke test left
behind (invented device ids, `actual_label` NULL on every row).

### `api/lambda/cubic-mars-dashboard-api/sql/18_purge_ps3_bridge_test_rows.sql` (new)

Deletes those rows outright. Idempotent, registered in `migrate()`. It does not
touch `ps3_severity_summary` / `ps3_severity_drivers` / `ps3_device_metrics`,
which hold the real 19-Jul measured metrics.

### `dashboard/src/components/tabs/PS3RootCauseTab.jsx` — rewritten, RDS-only

Rebuilt in the **PS5 idiom**: provenance band → sub-tabs → filter bar → KPI row
→ charts → sortable, paged tables, with explicit loading / empty / error states.
Same tokens as `PS5SLAReliabilityTab.jsx` (`RISK`, `TCOL`, `TYPE_ORDER`, `Chip`,
`SortTh`, `Pager`, `.card` / `.badge` / `.data-table` / `.filter-btn`).

Removed, with the reason recorded in the file header:

| Removed | Why it was not real |
|---|---|
| `SEQUENTIAL_PATTERNS` | invented `ERR_*` codes, frequencies, span timings |
| `MARKOV_STATES` / `MARKOV_MATRIX` | invented transition probabilities |
| `PREVENTABLE_FAILURES` | invented "% preventable" and maintenance advice |
| `REPLACEMENT_PRIORITIES` | invented components, windows, dollar savings |
| calendar heat-map | cell value was `abs(sin(day*1.7 + hour*0.43) * cos(day*0.3 + hour*0.8))` — a picture of a trig function labelled "failure intensity" |
| `getRootCauseFactors()` | mock Pareto / software versions / environmental correlations |
| `getTemporalPatterns()` | mock hourly / daily / monthly series |

Kept — as you asked, the panel *shapes* survive where a real source exists, now
fed from the notebook outputs:

- **Component Pareto** ← `dominant_pred_component` × `n_incidents` on real device rows
- **Risk band distribution** ← `pct_critical_pred` (NUMERIC(7,4), 0..1 scale per `sql/15`)
- **Severity mix by device category** ← `dominant_pred_severity`
- **SHAP drivers** ← `/ps3/rootcause/drivers` when the v2 run is loaded, otherwise the shipped model's `ps3_severity_drivers`
- **Device table** ← `v_ps3_device_risk`
- **Serial table** ← `v_ps3_serial_risk`, at `device_id × matched_serial_nbr`

Two behaviours worth knowing:

- **Gate-aware masking is honoured.** When a head misses its macro-F1 floor the
  views NULL its severity fields and set `severity_shippable = false`. The tab
  shows **"gated"**, never a re-derived number. A gated device still appears in
  the row count, so nothing disappears silently.
- **Empty ≠ zero.** Until the v2 run lands, the Device and Serial tabs name the
  exact table that is empty and the run that will fill it, instead of drawing a
  placeholder. The Model Scorecard tab meanwhile shows the **real shipped
  model** from `ps3_severity_summary` — different table, still RDS, clearly
  labelled as the pre-v2 model.

### `dashboard/src/components/tabs/PS1FailurePredictionTab.jsx`

- Removed the mock generator imports (`getPredictionSummary`, `getAccuracyTrend`,
  `getModelPerformance`, `getConfusionMatrix`, `getFeatureImportance`). Only
  `CITIES` remains, purely as an id → display-name lookup.
- Removed `FEATURE_IMPORTANCE_OVER_TIME` — `0.20 + 0.10·sin(t) + 0.05·cos(t)`
  over five invented feature names. PS1 has no SHAP-over-time table; the real,
  point-in-time SHAP is already drawn from `/ps1/feature-importance` and
  `/ps1/explainability`.
- Removed `precisionRecallCurve` (a closed-form sine curve, not a sweep of the
  model — `/ps1/threshold-sweep` serves the real one), `fpFnTrend`,
  `correlationMatrix` (values from `(feature-name length × 31) % 100`),
  `failureTypeBreakdown`, `accuracyGrouped`, `confMetrics`, and their helpers.
  All were dead code; leaving them meant one edit could put fabricated numbers
  back on a client screen.
- Removed the **"Train / Validation / Test AUC — S3 Artifacts"** chart and its
  3-rows-per-model table (the leakage vector above). The held-out numbers remain
  in "Champion Model Test-Set Performance", now sorted **best AUC first**.
- Removed the **"Models by Device Type"** pie. It plotted one slice of value 1
  per model, so it always rendered as N equal wedges — it encoded the model
  count and nothing else while looking like a distribution. The registry table
  above it already states that fact exactly.

---

## 4. Verification run

| Check | Result |
|---|---|
| `esbuild` parse `PS3RootCauseTab.jsx` | pass |
| `esbuild` parse `PS1FailurePredictionTab.jsx` | pass |
| `python3 -m py_compile handler.py` | pass |
| grep `Math.sin\|Math.cos\|Math.random` in both tabs | zero, outside comments |
| grep `PREVENTABLE\|REPLACEMENT_PRIOR\|MARKOV\|SEQUENTIAL_PATTERNS\|SIMULATED` | zero, outside comments |
| grep `0.7033\|0.9950` in `handler.py` | zero, outside the comment recording the removal |

Brace-counting is unreliable on JSX — these are real compiles, not heuristics.

---

## 5. What is still open

1. **The PS3 v2 notebook has not been run.** Nothing in RDS changes until it is.
   This is the single blocking item for the PS3 tab.
2. **`ps1_serial_predictions` is still disabled** in the loader `ORDER`, and
   there is no `/ps1/serial-predictions` route. PS1 therefore has device grain
   but no serial grain on the dashboard. Say the word and I will add the route
   and re-enable the loader.
3. **Seeded tables.** `ps1_model_performance`, `ps1_feature_importance`,
   `ps1_failure_summary`, `ps1_leaderboard`, `ps1_features`,
   `ps3_severity_summary`, `ps3_severity_drivers`, `ps3_device_metrics` were
   hand-loaded rather than written by a pipeline. Their **values are real**
   (they match the run artifacts), but they will not refresh on the next run
   until each is wired to a manifest. Worth doing before this becomes a
   standing-data problem.
4. **PS5 is structurally in sync but has no shippable model.** All three device
   types report `dashboard_ready: false` (C-index 0.5906 / 0.5071 / 0.5970,
   floor not met). The PS3 tab now matches its layout and its honesty rules;
   PS5's own numbers are a modelling question, not a dashboard one.
5. **PS2 and PS4 still fall back to mock data** in `src/data/api.js`. PS1, PS3
   and PS5 now throw instead. If "everything served from RDS" is to hold across
   the whole dashboard, PS2/PS4 need the same pass — it is a larger job and I
   have not touched it.
6. **Four orphaned PS3 endpoints** in `api.js` (`/ps3/deepdive/{metric}`,
   `/ps3/device-360-deepdive`, `/ps3/servicenow/status`,
   `/ps3/servicenow/create-incident`) have no server route. They are unreferenced
   by the rewritten tab, so nothing calls them, but they should be deleted.
7. **`.git/index.lock`** (0 bytes, 26-Jul 08:53) is still blocking commits.
