# V4 dashboard — PS1 data-vintage audit

**2026-08-16 · source-code audit, no AWS access · every claim quotes the line it came from**

Commissioned because `dashboard/` had not been committed since 09-Aug while the PS1
backend changed substantially underneath it. The commit gap turned out not to be the
problem. **The dashboard is fully committed and clean.** What it *renders* is the problem.

---

## 0. The three findings that matter

**F-1 — The V4 PS1 screen has no vintage indicator of any kind.** PS2 and PS3 each
carry a `StatusBar` with an "Analysis as of …" date and the line *"Source extract ends
11 Apr 2026. Nothing here describes the estate after that date."* `V4PS1Overview.jsx`
has **no StatusBar, no date line, and no badge** except `tone="good">champion`. Four of
its five sub-tabs carry no date at all.

**F-2 — A third data vintage exists, and it feeds every headline number.**
`/ps1/station-summary` and `/ps1/risk-trend` are **not** Path A and **not** Path B —
they are 26-Jul hand-seeded tables, and `ps1_station_summary` was itself computed *from*
Path B at run `ps1_20260726`. This corrects the earlier working assumption that only six
routes were stale. **Every fleet total on the PS1 screen and on the estate card comes
from this seed.**

**F-3 — The estate shell asserts "Daily inference" behind a green `live` badge.**
`V4Shell.jsx:403`. Daily inference does not exist (see docs/PS1_DAILY_INFERENCE_DESIGN.md). This is the single
most misleading element on the dashboard, because it is a positive claim of freshness
rendered in the colour that means "healthy".

---

## 1. Panel-by-panel vintage — V4 PS1 (`/v4`)

**LIVE** = Path A `ps1_cross_wired_daily`. **FROZEN** = Path B, shut down 10-Aug 11:55Z.
**SEED** = 26-Jul `sql/load`.

| Sub-tab | Panel | Route | Vintage |
|---|---|---|---|
| Fleet status | "From fleet to work order" funnel | `/ps1/station-summary` | **SEED** |
| Fleet status | KPIs: Devices in service / Needing a work order / Critical | `/ps1/station-summary` | **SEED** |
| Fleet status | Risk mix Donut | `/ps1/station-summary` | **SEED** |
| Fleet status | "Failures over time" Trend | `/ps1/risk-trend` | **SEED** |
| Fleet status | KPI "Repeat offenders" | `/ps1/xw-chronic` | LIVE |
| Fleet status | "How much better than chance" | `/ps1/xw-tiers` | LIVE |
| **Location** | **all four panels + table** | `/ps1/station-summary` | **SEED** |
| Devices | risk mix, location, histogram, work list | `/ps1/predictions` | LIVE |
| Devices | parts / worst-part columns | `/ps1/serial-predictions` | **FROZEN** |
| Devices | chronic bubble | `/ps1/xw-chronic` | LIVE |
| Why | all seven panels | `/ps1/xw-*` | LIVE |
| **How we know** | precision / recall / accuracy | `/ps1/confusion` | **SEED** |
| **How we know** | model scorecard | `/ps1/leaderboard` | **SEED** |
| **How we know** | **"Where the alert line is set"** | `/ps1/threshold-sweep` | **SEED** |
| Estate Overview | PS1 card, 4 stats | `/ps1/station-summary` | **SEED** |
| Device 360 / Analyse | risk banner, PS1 panel | `/ps1/device-360` | **MIXED** — legacy first, cross-wired fallback; the screen never says which |

**Net: the Fleet status, Location and How-we-know tabs are entirely 26-Jul seed. Live
Path-A data appears only on Devices and Why.** Nothing on screen distinguishes them.

---

## 2. The threshold problem, in the one place a reader goes to check

`V4PS1Overview.jsx:1070-1090` renders **"Where the alert line is set"** on the *How we
know* tab, columns `Alert line` and `Share of fleet flagged`, straight from
`/ps1/threshold-sweep`. Those rows are hardcoded in
`sql/load/ps1_sklearn_20260726.sql:76-79`:

| fleet | dashboard "Alert line" | dashboard "Share flagged" | **deployed threshold** |
|---|---|---|---|
| GATE | 0.6500 | 78.6% | **0.12284049** |
| TVM | 0.4900 | 94.2% | **0.02648935** |
| VALIDATOR | 0.6000 | 39.7% | **0.39651793** |

GATE's endpoint fires at **0.1228** while the dashboard publishes **0.65**. The share
flagged is wrong in the unsafe direction for every fleet — a much lower threshold flags
far more devices.

**Every number on the How-we-know tab inherits this.** `/ps1/confusion`'s TP/FP/TN/FN
were computed at those same sklearn thresholds, and the panel titles are operational
questions — *"When we flag a device, how often are we right?"* — so they read as
statements about production behaviour rather than about a July experiment.

**Two different thresholds, three clicks apart.** The Devices work list shows an
`Alerts above` column (`V4PS1Overview.jsx:827`) fed by Path A's `threshold_used`. The
How-we-know tab shows `Alert line` from the seed. Same concept, different numbers, no
reconciliation on screen.

**Device 360 is worse — the field is silently overwritten server-side.**
`handler.py:894-907` sets `decision_threshold` from the prediction row, then does
`out["ps1"].update(...)` from `ps1_model_performance`, replacing it with 0.648 / 0.495 /
0.605. `V4Evidence.js:115-119` then renders it as a sentence: *"risk 82% is above the
65% action threshold."* On a device that falls through to the cross-wired path, the same
label carries a different number. The payload has a `source` key; the UI never reads it.

---

## 3. The disclosure that exists and reaches no screen

`handler.py:2662-2687` computes `serving_matches_scorecard`, `serving_run_id`,
`serving_run_kind` and a plain-English `serving_caveat`: *"These metrics are NOT what
that endpoint would return."*

Grepping `dashboard/src/` for `serving_caveat|serving_matches_scorecard|serving_run_id`
returns **zero hits**. V4 never calls `/ps1/model-performance` at all.

This is the third layer of the same disclosure going unread: `sql/54` put the gap in the
data layer as `v_ps1_serving_gap` "where it cannot be lost to a handler bug"; the handler
computes and publishes it; the dashboard does not fetch it. **A disclosure nobody renders
is not a disclosure.**

---

## 4. Smaller defects, each with a quoted line

| # | Defect | Evidence |
|---|---|---|
| D-1 | Green `live` badge + "Daily inference" on the estate card | `V4Shell.jsx:403` |
| D-2 | `ps1_risk_trend` holds **12 monthly rows**; panel hint says "by day" | `V4PS1Overview.jsx:728`, data in `ps1_run_20260726.sql:116` |
| D-3 | `scoredDate` = `stationRows[0].last_inference_date` — first row of a list ordered by `predicted_failures DESC`, i.e. the **busiest depot's** date, not the fleet max. `V4Shell.jsx:300-305` uses `max()` for the same field | `V4PS1Overview.jsx:414` |
| D-4 | Analyse modal headline hardcoded "In the next 3 days" while the window renders **Apr 12–14, 2026** | `V4Device360Popup.jsx:400-411` |
| D-5 | Estate banner says "All three analyses" above **five** PS cards | `V4Shell.jsx:328-334` |
| D-6 | Work-list table has no `prediction_date` column — 4,217 rows, no date | `V4DeviceTable.jsx` |
| D-7 | `/ps1/xw-act-now` fetched, never rendered | `V4PS1Overview.jsx:125` |
| D-8 | `V4_VISUAL_AUDIT.md` stale — claims 6 PS1 sub-tabs incl. Components; there are 5 | doc dated 04-Aug, screen changed 08-Aug |
| D-9 | Dead code: `PS1Enhancements.jsx` (173 lines), `api.js:454-482` helpers, `V4api.js` `crosstab`/`riskBands`/`componentInventory`/`servicenow.staged` | zero references in `src/` |
| D-10 | Legacy V3 tab still routed at `/dashboard/city/:cityId` and calls all six FROZEN routes | `CityDashboard.jsx:22` |

---

## 5. The pattern the dashboard already gets right

`V4PS1Overview.jsx:556` — `ACCURACY_OVERRIDE = { GATE: 0.901 }` — is marked with an
asterisk, a hover title showing the computed value, and a stated end condition
(L1043-1056). Someone found a number they did not trust, corrected it, and **left the
correction visible with the reason attached.**

That is exactly the treatment the threshold panels need and do not have. The fix is not
to hide the seed data; it is to label it.

---

## 6. Recommended fixes, in order

| # | Fix | Effort |
|---|---|---|
| 1 | **Remove the green `live` badge / "Daily inference" claim** from `V4Shell.jsx:403` | one line |
| 2 | **Add a `StatusBar` to `V4PS1Overview`**, copying `V4PS3Overview.jsx:1753-1791`. PS3's already says the right thing about the 11-Apr extract | ~40 lines, pattern exists |
| 3 | **Per-panel vintage chips** — SEED and FROZEN panels get a `Badge tone="warning"` reading "26-Jul scorecard, not a live score" / "frozen 10-Aug" | ~1 line per panel |
| 4 | **Threshold panel** — either show the deployed thresholds beside the published ones, or badge the panel as describing the 26-Jul sklearn candidate rather than the served model | small |
| 5 | **Render `serving_caveat`** — call `/ps1/model-performance` and surface it on How-we-know | small; the API already returns it |
| 6 | Fix `scoredDate` to `max()`, matching `V4Shell` | one line |
| 7 | Make the Analyse-modal headline derive from `last_scored_day` instead of hardcoding "next 3 days" | small |
| 8 | Add `prediction_date` to the work-list table | one line |
| 9 | Retire `PS1Enhancements.jsx` and the dead API helpers | mechanical |

Fixes 1–3 are the ones that change what a reader believes. The rest are hygiene.

---

## 7. What this audit did not establish

- **The literal value of `ps1_cross_wired_daily.threshold_used`.** Written by the
  notebook export; no repo file pins it. Needs a query.
- **Whether the frozen Path-B tables still hold rows in Aurora today.** Source-code audit
  only — `tooling/sql/ps1_inventory_counts.sql` answers it.
- **Whether `/ps1/facilities` and `/ps1/table-status` are Path A, Path B or seed.**
  `/ps1/facilities` reads `dim_device_station`, a dimension rather than a PS1 path.

Route count correction: the handler defines **13** `/ps1/xw-*` routes, not 14. The
36-route total is unchanged.
