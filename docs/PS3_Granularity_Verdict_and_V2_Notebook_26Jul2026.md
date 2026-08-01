# PS3 granularity verdict, V2 notebook, and the ECR/endpoint answer
CUBIC MARS — Chicago / CTA-Ventra · 26 Jul 2026

Measured directly against the run bundle in `Level2/PS3` (the notebook output that
feeds RDS) and probed against the live API. Nothing below is inferred.

> **Note on the other thread.** I can't read the "PS3 component delivery pipeline"
> conversation — each session is isolated, so I have no access to its history. If
> it contains a decision or artifact that changes anything here, upload the file
> or paste the relevant part and I'll fold it in. Everything in this document is
> derived from the run bundle and the repo, which I *can* see.

---

## 1. Is device-level granularity available in the current PS3? **Yes.**

| Grain | TVM | GATE | VALIDATOR | Verdict |
|---|---:|---:|---:|---|
| incident rows | 32,842 | 1,770 | 0 | real |
| device rows (unique `device_id`) | 472 | 455 | 0 | **real** |
| serial rows | 472 | 455 | 0 | present in shape only |
| distinct `matched_serial_nbr` | **223** | 448 | 0 | see below |
| serials per device (min / max / mean) | 1 / 1 / **1.000** | 1 / 1 / **1.000** | — | **collapsed** |

**Device grain is genuinely there.** `tvm_device_predictions.csv` and
`gate_device_predictions.csv` carry `device_id`, `n_incidents`,
`pct_critical_pred`, `dominant_pred_severity`, `dominant_pred_component`,
`avg_component_age_days`, `last_incident_dtm`, `mars_device_category` — 927
devices in total, all real.

**Serial grain is not.** Three measurements, any one of which is conclusive:

1. Serials per device is **exactly 1.000** — min 1, max 1, on both device types.
2. TVM has **472 devices but only 223 distinct serials**, so a serial is attached
   to roughly two devices on average. A component serial number cannot be
   physically installed in two machines.
3. `pct_critical_pred` on the serial file equals the device file's value on
   **472/472** TVM rows and **455/455** GATE rows — the serial row is a copy of
   its device row.

Cause, unchanged from the earlier audit: gold's `hw_best_match` CTE ends
`WHERE rn = 1`, which keeps one component per incident. Every downstream serial
rollup then has exactly one component to roll up.

**VALIDATOR has zero PS3 rows, and that is structural, not a bug.**
`Validator/validator_ps3_stub.json` states it plainly: PS3 is sourced from
`silver.incident_root_cause` (ServiceNow availability events), and validator/BMV
failures are not recorded as availability events — they surface as `device_event`
out-of-service *Set* events. So the PS3 feed has 0 validator rows by construction.
A true PS3 validator model needs Cubic to emit validator availability events or
fault codes. This is exactly why PS1's target is OOS: validators have no
chargeable events either.

---

## 2. Two further defects the audit turned up

### `pct_critical_pred` is 0.0000 on every single row

Because `pred_severity_collapsed` is **MAJOR on 32,842/32,842 TVM incidents and
1,770/1,770 GATE incidents**. The collapse map was keyed on human-readable
labels, but `failure_level_label` carries codes, so every value fell through to
the MAJOR default. There is no class named `CRITICAL` in the output at all.

Any dashboard that bands risk on `pct_critical_pred` therefore bands the entire
fleet as zero-risk. The v2 engine already carries the fix — `SEVERITY_COLLAPSE`
keyed on codes, with unmapped codes resolving to `UNKNOWN` rather than defaulting
to MAJOR. Applied to the current run's class counts, that yields ≈33.9% CRITICAL
for TVM (`ALL_PURCHASE` 9,935 + `ALL_FUNCTIONS` 1,192 of 32,842).

The rebuilt PS3 tab does not assume that column is usable. It tests it, and when
it is degenerate it collapses the real 4-class `dominant_pred_severity` on screen
using the identical map, and says so in a banner.

### The GATE severity head is not shippable

Macro-F1 **0.4978** against a floor of 0.55, AUC-OVR **0.4358** — below chance.
Its 99.11% accuracy is just the `ALL_FUNCTIONS` base rate; the leaderboard shows
RandomForest, HistGBM and LightGBM all tying at exactly 0.49777, which is what
happens when every model predicts the majority class. The gate-aware views mask
it and the dashboard shows "gated".

Passing heads: TVM severity 0.7648 (floor 0.55) and TVM root-cause 0.6730
(floor 0.45).

---

## 3. Does the V2 notebook register a model endpoint in ECR like PS1?

**As shipped: no.** With `CONFIG["DEPLOY_ENDPOINT"] = False` — the default — the
notebook trains, scores, writes CSV/Parquet and `manifest.json`, and stops. It
creates no SageMaker Model, no EndpointConfig, no Endpoint, and touches no ECR
repository. That is deliberate: it is what makes the run provably unable to
disturb `chicago-ps3-rootcause-v1` while that endpoint is serving.

**I have now added the opt-in path**, new in this version — Section 16. Set
`CONFIG["DEPLOY_ENDPOINT"] = True` and the notebook will:

1. **Refuse to run** if `V2_ENDPOINT_NAME` equals `LIVE_ENDPOINT_DO_NOT_TOUCH`.
2. Package every `{cat}_{head}_bundle.joblib` plus a generated `inference.py`
   into `model.tar.gz` and upload it to `SM_MODEL_S3_PREFIX`.
3. Resolve the **AWS-managed** sklearn inference image via
   `sagemaker.image_uris.retrieve()` — the same mechanism the PS1 notebooks use.
   **No custom image is built and nothing is pushed to a private ECR repo.** The
   URI returned is AWS-owned, e.g.
   `683313688378.dkr.ecr.us-east-1.amazonaws.com/sagemaker-scikit-learn:1.2-1`.
4. Create Model → EndpointConfig → Endpoint under **`chicago-ps3-oos-v2`**.

Audited on the delivered notebook:

| Token | Occurrences | Where |
|---|---:|---|
| `create_endpoint` | 3 code | inside `deploy_v2_endpoint()`, guarded |
| `create_endpoint_config` | 1 code | same function |
| `update_endpoint` | 0 code | prose only |
| `delete_endpoint` | 0 code | prose only |
| `.deploy(` | 0 | — |
| `chicago-ps3-rootcause-v1` | — | only as the guard value |

Only `create_endpoint` is ever called, so an endpoint already named
`chicago-ps3-oos-v2` makes the call fail loudly rather than silently replace
something.

One request body serves both heads:

```json
{"device_category": "TVM", "head": "severity",
 "instances": [{"component_age_days": 97, "oos_onsets_24h": 2}]}
```

**Recommendation:** run once with `DEPLOY_ENDPOINT = False` and confirm the
serial fan-out is real (see the check below) before spending an endpoint on it.
Deploying a model whose serial grain is still 1:1 just moves the defect behind an
API.

---

## 4. The one check to run after the notebook finishes

```python
import pandas as pd
d = pd.read_csv("PS3_v2_outputs/tvm/tvm_device_predictions.csv")
s = pd.read_csv("PS3_v2_outputs/tvm/tvm_serial_predictions.csv")
print("devices        :", d.device_id.nunique())
print("serial rows    :", len(s))
print("distinct serial:", s.matched_serial_nbr.nunique())
print("fan-out        :", round(len(s)/d.device_id.nunique(), 3))
assert len(s) > len(d),                    "serial grain still collapsed"
assert s.matched_serial_nbr.nunique() >= d.device_id.nunique(), "serials shared across devices"
assert d.pct_critical_pred.max() > 0,      "severity collapse still defaulting to MAJOR"
```

On the test frame the module produced 1,063 incidents → 2,465 incident-component
rows (2.32×) → 139 serial rows from 60 devices. If fan-out comes back at 1.000
again, the notebook did not reach `silver.hw_config_current` — check
`HWCONFIG_PARQUET`.

---

## 5. API — what is now served, and at what grain

New this pass:

| Route | Grain | Purpose |
|---|---|---|
| `/ps1/serial-predictions` | device × serial | PS1 serial grain — the table was being written but had no route |
| `/ps1/runs` | run | batch lineage: endpoint, model version, threshold, devices scored, flagged |
| `/ps1/load-audit` | load | what the rds-push Lambda did, incl. skipped columns |
| `/ps3/rollup` | device category | macro view — devices, incidents, serial rows, distinct serials, gated count |
| `/ps3/severity-mix` | category × class | the real 4-class mix plus held-out agreement |
| `/ps3/component-mix` | category × component | the real Pareto source plus per-component agreement |
| `/ps3/severity/drivers` | head × feature | severity-head SHAP (root-cause head already had one) |

`/ps1/serial-predictions` pins to the latest **successful** run
(`ps1_inference_runs … status='success' ORDER BY run_ts DESC LIMIT 1`) so a
half-loaded batch can't leak onto the page.

The two rollup routes aggregate in SQL rather than in the browser. That matters:
the device route is `LIMIT 300`, so a client-side fleet summary would silently
describe the first page instead of the fleet.

---

## 6. Dashboard — macro → device → serial → incident

`PS3RootCauseTab.jsx` now has five views in the PS5 idiom:

1. **Fleet Overview** — per-category cards (devices, incidents, serial rows,
   distinct serials, **serials-per-device**), the real 4-class severity mix, the
   collapsed CRITICAL/MAJOR split, and the component Pareto with per-component
   held-out agreement.
2. **Model Scorecard** — two-head scorecard, champion-first, gate status against
   the floor.
3. **Device Risk** — 927-row `device_id` grain, sortable, paged, filterable.
4. **Serial Risk** — `device_id × matched_serial_nbr`, with the fan-out ratio as
   a first-class KPI. When fan-out is 1.00 the view says so in an amber banner
   rather than presenting device rows as component rows.
5. **Incidents** — `availability_event_id` grain with facility, model confidence,
   and predicted vs actual for both heads, coloured green on agreement and red on
   disagreement.

Filters (search, device type, severity, facility, min incidents) apply across all
four data views and are wired to the global `FilterContext`, as PS5 does.

---

## 7. Verification

| Check | Result |
|---|---|
| notebook: nbformat validate | VALID |
| notebook: 18 code cells, AST parse | 0 syntax errors |
| notebook: `update_endpoint` / `delete_endpoint` / `.deploy(` as code | 0 / 0 / 0 |
| `ps3_oos_engine_v2.py` AST | OK |
| `ps3_v2_deploy_cell.py` AST | OK |
| `handler.py` py_compile | OK |
| `PS3RootCauseTab.jsx` esbuild | pass |
| full dashboard graph bundle (`CityDashboard.jsx` entry) | **BUILD OK** |
| `Math.sin/cos/random`, `PREVENTABLE`, `MARKOV`, `SIMULATED` in PS1/PS3 tabs | 0 outside comments |

---

## 8. Still open

1. **VALIDATOR will never appear in PS3** from the availability-event source.
   Options are the PS4 anomaly head (unsupervised, covers all three types) or the
   OOS-proxy severity from `silver.device_failures`. Both need your call.
2. **GATE severity stays gated** until the head clears 0.55 macro-F1. Its
   root-cause head is worth checking separately after the v2 run.
3. **`ps1_serial_predictions` is still disabled in the loader `ORDER`** — the
   route now exists, but the table will stay empty until the loader writes it.
4. **PS2 and PS4 still fall back to mock data** in `src/data/api.js`.
5. **`.git/index.lock`** is still blocking commits.
