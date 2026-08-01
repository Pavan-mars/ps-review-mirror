# PS1 & PS3 dashboard rebuild
CUBIC MARS — Chicago / CTA-Ventra · 26 Jul 2026

Built against your list (a)–(m). Rule (m) — *data availability decides the
structure* — is the one that shaped everything else: each view renders only when
its source table has rows, and otherwise names the table and the run that fills
it. Empty is shown as empty, never as zero.

---

## What was built

**`src/components/shared/DashboardKit.jsx`** (new, ~640 lines) — one shared
implementation of filtering, sorting, pagination, virtualisation, drill-down,
cross-tab and ServiceNow staging, so PS1 and PS3 cannot drift apart. Both tabs
now import from it rather than carrying private copies.

**`PS3RootCauseTab.jsx`** — rewritten. Six views: Fleet Overview, Model
Scorecard, Device Risk, Component Risk, Incidents, Cross-tab.

**`PS1FailurePredictionTab.jsx`** — rewritten. Seven views: Fleet Overview,
Model Scorecard, Device Predictions, Component Risk, Stations, Cross-tab, Runs &
Lineage.

**`handler.py`** — five new routes (below).

---

## Against your list

| | Item | Where it landed |
|---|---|---|
| a | Visuals | Treemap (component mix, stations), Funnel (OOS → MAJOR+ → CRITICAL), Bubble/scatter (incidents vs age; calibration; component risk vs age), stacked Area (failures over time), stacked/grouped Bar, Line/composed (threshold sweep), Pareto, and a shaded pivot matrix with row/column margins |
| b | High-level + granular | Fleet → device → component/serial → incident, each its own view, plus per-station rollups |
| c | No irrelevant content | Audited below — zero fabricated series remain, and four dead API helpers were deleted |
| d | Filters | Device ID, serial no., device type, failure type, component, station/facility, risk band, min incidents, component age, min probability, flagged-only — every option list built from loaded rows, so a value absent from the data is never offered |
| e | Cross-tab | New `/ps3/crosstab` and `/ps1/crosstab`, aggregated **server-side over the whole run**, with a dimension picker |
| f, j | Analyse + cross-linkage | Analyse opens Device 360; ServiceNow staging carries the full row as payload |
| g | ServiceNow push | `ServiceNowButton` on every device and component row in both tabs, backed by the new `/ps3/servicenow-stage` |
| h | Drill-down | Click a treemap tile, bar segment, Pareto bar, bubble, pivot cell, device ID or component to push a filter; active filters show as removable breadcrumbs |
| i | Search | Free-text across device, serial, component, severity, station, error code, event ID |
| k | Tracked follow-ups | Listed at the end — none blocking |
| l | Pagination / virtualisation | Every table paginated (25/50/100/250 per page) and windowed above 150 rows, so the DOM never holds more than a page |
| m | Data availability drives structure | Each view is gated on its own source; `/ps1/coverage` states which tables actually hold rows per device type |

---

## New API routes

| Route | What it does |
|---|---|
| `/ps3/crosstab?rows=&cols=` | Server-side pivot over `ps3_incident_predictions`. Dimensions come from a fixed whitelist — anything outside it is rejected, not escaped |
| `/ps1/crosstab?rows=&cols=` | Same over the latest `ps1_failure_predictions` batch |
| `/ps3/servicenow-stage` (POST) | Mirrors `/ps1/servicenow-stage`. Writes to `servicenow_staging` |
| `/ps3/servicenow-staged` | Lists staged PS3 incidents |
| `/ps3/collapse-health` | Share of each collapsed severity label per category — the standing check that `pct_critical_pred` has not gone flat again |

**On the ServiceNow button, one thing to be clear about:** it says *"Stage in
ServiceNow"*, not *"Create incident"*. The server records the payload in
`servicenow_staging` and returns a `staged_id`; it does not post to ServiceNow,
because there is no live endpoint wired yet. A button labelled "create incident"
that silently does nothing would be worse than no button — an operator would
believe a ticket exists. When the ServiceNow endpoint is available, the submit
call goes in that one route and the button needs no change.

---

## Cleanup done under (c)

Removed from the PS1 tab, each with its reason recorded in the file:

| Removed | Why |
|---|---|
| `FEATURE_IMPORTANCE_OVER_TIME` | `0.20 + 0.10·sin(t) + 0.05·cos(t)` over five invented feature names; no SHAP-over-time table exists |
| `precisionRecallCurve` | A closed-form sine curve, not a sweep of the model — `/ps1/threshold-sweep` serves the real one, now plotted |
| `fpFnTrend` | sin/cos over a mock accuracy series |
| `correlationMatrix` | Values from `(feature-name length × 31) % 100` |
| `failureTypeBreakdown` | Invented failure types × invented factors |
| "Models by Device Type" pie | N equal wedges — encoded a model count while looking like a distribution |
| Train/Validation/Test chart | Plotted in-sample fit beside held-out numbers (GATE: train AUC 1.0000 vs test 0.9038) |

Removed from the PS3 tab: `SEQUENTIAL_PATTERNS`, `MARKOV_STATES`/`MARKOV_MATRIX`,
`PREVENTABLE_FAILURES`, `REPLACEMENT_PRIORITIES`, `getRootCauseFactors()`,
`getTemporalPatterns()`, and the calendar heat-map whose cell value was
`|sin(day·1.7 + hour·0.43) · cos(day·0.3 + hour·0.8)|`.

Removed from `src/data/api.js`: `apiPS3DeepdiveMetric`,
`apiPS3Device360Deepdive`, `apiPS3ServiceNowStatus`,
`apiPS3ServiceNowCreateIncident` — each called a `/ps3/...` route with no server
handler, so every invocation 404'd. Nothing referenced them.

Scan result across both tabs and the kit: **zero** occurrences of `Math.sin`,
`Math.cos`, `Math.random`, `mockData`, `SIMULATED`, `PREVENTABLE`, `MARKOV` or
`SEQUENTIAL_PATTERNS` — including in comments, except where a comment records
what was deleted and why.

---

## Two design rules the charts follow

**One y-axis per chart.** Two measures of different scale get two charts, never
two scales on one. A dual axis lets you place the crossover point anywhere, so
it can be made to show any relationship you like.

**Colour follows the entity, never its rank.** Device type keeps its hue when a
filter removes other types, so a filtered chart is not silently repainted.
Categorical hues are assigned in fixed order and never cycled.

Accuracy is deliberately absent from both scorecards. On these labels it
measures the class balance, not the model — the GATE severity head scored 99.11%
accuracy with macro-F1 0.4978, balanced accuracy exactly 0.5000, and Cohen's κ
and MCC exactly 0. In its place: macro-F1 read against its promotion floor *and*
the 1/k line a constant predictor cannot beat, plus the base rate beside the PS1
numbers so 99.50% reads next to "0.54% base rate".

---

## Verification

| Check | Result |
|---|---|
| Full dependency graph, esbuild bundle from `CityDashboard.jsx` | **BUILD OK** |
| Full build with real `recharts@3.10.1` + `react@19` resolved (validates every named import) | **BUILD OK**, 1,898,611 bytes |
| `handler.py` py_compile | OK |
| All 21 feature checks (a, d, e, f, g, h, i, l) | present |
| Fabricated-content scan | 0 hits |
| Chart types confirmed available in recharts 3.10.1 | Treemap, Funnel, Scatter, ZAxis, Area, RadialBar, Sankey, LabelList, Brush all OK |

---

## Tracked follow-ups (k) — none blocking

1. **The PS1 batch scorer.** The three v3 notebooks are training/diagnostics
   only: zero `to_parquet`, `put_object`, `create_endpoint` or `manifest`, and
   `predict_proba` is called only on `X_test`. Prediction rows in RDS therefore
   come from an undocumented path, which is why their thresholds match no
   registry table. The Runs & Lineage view now makes that visible.
2. **`ps1_serial_predictions` is still not written** by the loader. The route and
   the Component Risk view exist and will populate the moment it is.
3. **VALIDATOR model metadata.** ~495k predictions with no summary, leaderboard,
   feature-importance or quality gate. The Fleet view states this explicitly
   rather than showing an empty chart.
4. **Live ServiceNow submit** — one route, when the endpoint is available.
5. **PS2 and PS4 still fall back to mock data** in `api.js`. PS1, PS3 and PS5
   throw instead. Out of scope for this pass.
6. **`.git/index.lock`** still blocking commits.
