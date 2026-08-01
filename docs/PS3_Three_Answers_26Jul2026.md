# Serial grain, the pct_critical fix, and the model filter
CUBIC MARS — Chicago / CTA-Ventra · 26 Jul 2026

---

## 1. Serial grain — you're right, and that's exactly the point

A device carrying up to 12 components is precisely why the current output is
wrong. The 21-Jul run reports:

| | TVM | GATE |
|---|---:|---:|
| devices | 472 | 455 |
| serial rows | 472 | 455 |
| **serials per device** | **1.000** | **1.000** |
| distinct serials | **223** | 448 |

If devices carry up to 12 components, 472 TVMs should produce something in the
low thousands of serial rows, not 472. And 223 distinct serials across 472
devices means a serial number is attached to about two machines — impossible for
a physical component.

The v2 fan-out module has no cap. `expand_serial_grain()` inner-joins every
`silver.hw_config_current` row for a device onto each incident and its only
dedup is `drop_duplicates(subset=["DEVICE_ID", "COMPONENT_SERIAL_NBR"])`, which
removes a repeated serial on the same device — not a second component. Twelve
components produce twelve rows. On the synthetic test frame it gave 1,063
incidents → 2,465 incident-component rows (2.32×) → 139 serial rows from 60
devices.

It also separates two things the collapsed version conflated:

- `attribution_method = "description_match"` — the incident text names this
  component, so the failure is *attributed* to it
- `attribution_method = "exposure"` — the component was merely installed at the
  time

`pct_critical_attributed` uses only attributed rows; `pct_critical_exposed` is
carried separately. Without that split, a device with 12 components would spread
one failure across all 12 and every component would look mildly guilty.

**Nothing to change here — the fix is already in the notebook you're about to
run.** The check afterwards is `serial_rows > device_rows` and
`distinct_serials >= devices`.

---

## 2. `pct_critical_pred` = 0.0000 — cause and fix

### Cause

`pred_severity_collapsed` came out **MAJOR on 32,842/32,842 TVM incidents and
1,770/1,770 GATE incidents**. There is no CRITICAL row anywhere in the run, so
every `AVG(collapsed = 'CRITICAL')` evaluates to exactly 0.

The collapse map was keyed on human-readable severity names, but
`gold.device_ps3_incident.failure_level_label` carries **codes**. Nothing
matched, and the map's default was MAJOR — so every incident silently became
MAJOR. A silent default turned a total lookup failure into a plausible-looking
answer.

### The complete label set (enumerated from the run, not assumed)

| code | n | collapses to |
|---|---:|---|
| `PURCHASE_CARD` | 18,777 | MAJOR |
| `ALL_PURCHASE` | 11,179 | **CRITICAL** |
| `ALL_FUNCTIONS` | 3,394 | **CRITICAL** |
| `PURCHASE_PRODUCT` | 1,262 | MAJOR |

Four values, all mapped — nothing falls through. Applied to this run:
**CRITICAL = 14,573 / 34,612 = 42.10%** of incidents, against 0.00% stored today.

### Fix, in three places

1. **Notebook (source).** `CONFIG["SEVERITY_COLLAPSE"]` is already keyed on
   codes in the v2 engine, and unmapped codes now resolve to `UNKNOWN` — not
   MAJOR — and are excluded from the `pct_critical` denominator. Nothing for you
   to edit; it takes effect on the run.
2. **RDS (repair + safety net).** New
   `sql/19_ps3_severity_collapse_fix.sql`, registered in `migrate()`. It
   recomputes `pred_severity_collapsed` from the code at the incident grain, then
   rebuilds `pct_critical_pred` on both the device and serial rollups from the
   corrected rows. Idempotent, and a no-op on empty tables — so it repairs
   whatever is loaded now *and* self-corrects any future run that lands with the
   old collapse.
3. **Standing check.** `v_ps3_collapse_health` and route
   `/ps3/collapse-health` report the share of each collapsed label per category.
   **If any single label shows `collapse_share` = 1.0000, the collapse has gone
   flat again.** That is the one query to run after every PS3 load.

### One decision for you

`failure_level_label` describes *what stopped working*, not a severity tier. The
split above encodes a judgement — "all purchase paths down" and "whole device
down" are critical; a single payment path down is major. That is an operations
call, not a data fact. If CTA classifies differently, change **only** the `CASE`
in `sql/19` and `CONFIG["SEVERITY_COLLAPSE"]` in the notebook, and keep the two
identical or the dashboard and the model will disagree about the word "critical".

---

## 3. The 99.11% model — I checked, and the number is real. So is the problem.

You're right to find it suspicious. It isn't a data error, though: 99.11%
accuracy *with* macro-F1 0.4978 is the exact, arithmetically necessary signature
of a model that has learned nothing.

### The proof

GATE severity test split: **335 ALL_FUNCTIONS + 3 OTHER = 338 rows.** Training
set had **1,410 ALL_FUNCTIONS and 9 OTHER** — nine examples of the minority class.

A model that outputs `ALL_FUNCTIONS` unconditionally scores:

| metric | constant predictor | reported |
|---|---|---|
| accuracy | 335/338 = **0.99112** | 0.99112 |
| macro-F1 | (0.9955 + 0)/2 = **0.49777** | 0.49777 |
| weighted-F1 | **0.98671** | 0.98671 |
| balanced accuracy | **0.5000** | 0.5 |
| Cohen's κ | **0.0** | 0.0 |
| MCC | **0.0** | 0.0 |

All six match to every digit. The per-class breakdown confirms it directly:
`ALL_FUNCTIONS` recall **1.0000** (predicted every time), `OTHER` precision
**0.0000**, recall **0.0000**, F1 **0.0000** on 3 supports — never predicted once.

And five different algorithms — RandomForest, HistGBM, LightGBM, XGBoost,
CatBoost — reported **identical** accuracy, macro-F1 and weighted-F1 to five
decimal places. Independent learners cannot coincide like that unless all five
are emitting the same constant.

So: the accuracy is genuine, and it is worthless. It measures the class balance,
not the model.

### Why the filter you asked for would backfire

"Above 90% accuracy and above 80% F1/precision/recall", applied to what exists:

| model | accuracy | macro-F1 | passes? |
|---|---:|---:|---|
| PS3 GATE severity (RandomForest) | 99.11% | 0.4978 | ✗ F1 |
| PS3 TVM severity (XGBoost_optuna) | 88.36% | **0.7648** | ✗ both |
| PS3 TVM root-cause (XGBoost) | 85.98% | **0.6730** | ✗ both |
| PS1 Gates (LightGBM Optuna) | 99.50% | 0.4481 | ✗ F1 |
| PS1 TVM (LightGBM Optuna) | 70.33% | 0.4593 | ✗ both |

**Nothing survives. The dashboard would render zero models.**

Worse, it drops the two heads that actually *pass* their gates — TVM severity
0.7648 against a 0.55 floor, TVM root-cause 0.6730 against 0.45 — while the two
highest-accuracy entries in the table are the two closest to being constant
predictors. PS1 Gates' 99.50% sits on a **0.54% base rate**: "never fails" scores
99.46%. An accuracy threshold selects *for* imbalance.

There is also a case the threshold gets exactly backwards. GATE severity LogReg
scores accuracy **0.8077** — the worst in its table — but **AUC 0.6358**, the
only model in that group that ranks better than chance. It looks worst and is
the only one with signal.

### What I built instead

Every model row now carries a `verdict` and a plain-English `verdict_evidence`:

- **`degenerate`** — provably no better than a constant predictor. Multi-class
  rule: **macro-F1 ≤ 1/k**. A constant predictor with majority share *p* scores
  (1/k)·2p/(1+p), whose supremum as *p*→1 is 1/k — so scoring at or under 1/k is
  a proof of no class-discriminating information, at any accuracy. Binary rule:
  recall = 0, or AUC ≤ 0.5.
- **`below_floor`** — a real model that missed its promotion gate. This also
  catches the LogReg case: low macro-F1 *but* AUC > 0.55 is reported as a
  threshold/imbalance problem, not a dead model — different remedy.
- **`ok`** — cleared its gate.

Verified against the live numbers:

```
GATE severity RandomForest -> degenerate   macro-F1 0.4978 <= 1/2 = 0.5000
GATE severity LogReg       -> below_floor  ... but AUC 0.6358 shows the ranking
                                            does carry signal
TVM severity               -> ok
TVM root_cause             -> ok
```

In the dashboard: the scorecard **hides no-signal models by default**, with a
visible count and a one-click toggle to reveal them — hidden, not deleted, so the
failure stays auditable. **Accuracy is no longer shown on the PS3 scorecard at
all**; in its place is macro-F1 read against two bars: its promotion floor, and
the 1/k line a constant predictor cannot beat. The PS1 leaderboard now carries
`base_rate_pct` alongside accuracy, so 99.50% reads next to "0.54% base rate".

API: `?exclude_degenerate=true` on `/ps1/leaderboard`, `/ps3/severity/summary`,
`/ps3/rootcause/summary`.

Notebook: the engine now emits `constant_predictor`,
`constant_predictor_baseline_macro_f1`, `majority_class_share_test` and
`n_classes_never_predicted` on every head summary — so this is caught at the
source on the next run, not on the dashboard afterwards.

### The real fix for GATE

Nine minority training examples cannot support a classifier. Options, in order of
how much they'd actually help:

1. **Widen the window or pool device types** for GATE severity, to get more than
   9 minority examples.
2. **Model GATE severity as binary** ALL_FUNCTIONS-vs-rest with class weights and
   a threshold tuned on recall, instead of the 4-class collapse.
3. **Leave GATE severity gated.** Its root-cause head is a separate question and
   is worth re-checking after the v2 run — GATE root-cause has a genuine spread
   (None 242, COMMS 109, SYSTEM 38, CSC_READER 36, GATE_MECH 23).

---

## 4. Also found: PS1 was hiding TVM and GATE entirely

Probing `/ps1/predictions` returned **500 rows, every one VALIDATOR**. The route
was `ORDER BY failure_probability DESC LIMIT 500` across the whole fleet, and
validator probabilities sit at ~0.99997 — so they filled the entire response and
TVM and GATE never appeared. The device filter had nothing to filter.

Fixed: predictions are now ranked **within** each `device_category`
(`ROW_NUMBER() OVER (PARTITION BY device_category ...)`), so every modelled type
gets its own top-N slice.

Second gap, which I can surface but not fill: **VALIDATOR has no model metadata
at all.**

| route | categories returned |
|---|---|
| `/ps1/predictions` | VALIDATOR (was), now all three |
| `/ps1/summary` | Gates, TVM — **no VALIDATOR** |
| `/ps1/leaderboard` | Gates, TVM — **no VALIDATOR** |
| `/ps1/model-performance` | GATE, TVM — **no VALIDATOR** |
| `/ps1/feature-importance?device_category=VALIDATOR` | **0 rows** |

So validators have ~495k predictions with no champion, no leaderboard, no
feature importance and no quality gate behind them. New route `/ps1/coverage`
reports which categories exist in each PS1 table, so the gap is stated rather
than showing as an empty panel. **Loading the VALIDATOR run's summary/leaderboard/
feature rows into RDS is the outstanding item** — the notebooks produced them.

---

## 5. Verification

| Check | Result |
|---|---|
| constant-predictor arithmetic vs reported GATE metrics | 6/6 match to every digit |
| `model_verdict()` unit-tested on 7 real model rows | correct on all 7 |
| collapse map vs enumerated labels | 4/4 mapped, 0 unmapped |
| notebook nbformat validate | VALID |
| notebook 18 code cells AST | 0 syntax errors |
| notebook `update_endpoint` / `delete_endpoint` / `.deploy(` as code | 0 / 0 / 0 |
| `ps3_oos_engine_v2.py` AST | OK |
| `handler.py` py_compile | OK |
| full dashboard graph bundle | **BUILD OK** |
