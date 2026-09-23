# PS3: Publishability Gate vs Contracted Acceptance Threshold

Date: 23-Sep-2026
Scope: Chicago CTA / Ventra pilot, PS3 (Root Cause Analysis)
Status: Internal position paper, written so that we say this first rather than
answer it later.

## Conclusion

PS3 carries two numeric thresholds and they are not the same instrument.

- PS3's own publishability gate requires macro-F1 >= 0.45.
- The contracted acceptance threshold is macro-F1 >= 0.80.

On the most recent published run, one model scope out of four clears the first.
None clears the second. GATE passes the publishability gate and fails the
acceptance threshold. That sentence is the whole position, and it should be said
in those words.

Both thresholds are legitimate. They answer different questions:

| Instrument | Value | The question it asks | Who set it |
|---|---|---|---|
| PS3 publishability gate | macro-F1 >= 0.45, plus two conditions below | Is this better than nothing, and better than guessing? | Mars-Techs, inside the notebook |
| Contracted acceptance threshold | macro-F1 >= 0.80 | Is this good enough to act on? | Contract, Table 13 of the solution document |

Presenting 0.45 as if it were the acceptance bar would not be honest. Nothing in
this document argues that 0.45 should replace 0.80.

## What the internal gate actually tests

The gate is three conditions, all of which must hold. From
`notebooks/ps3_root_cause_analysis/PS3_V26_PRODUCTION.ipynb`:

```
passed = macro >= MIN_MACRO_F1
         and (macro - majority_macro) >= MIN_MACRO_F1_LIFT
         and balanced >= MIN_BALANCED_ACCURACY
```

with the defaults, environment-overridable, set in the same notebook:

| Constant | Default | Environment variable |
|---|---|---|
| `MIN_MACRO_F1` | 0.45 | `PS3_MIN_MACRO_F1` |
| `MIN_MACRO_F1_LIFT` | 0.05 | `PS3_MIN_MACRO_F1_LIFT` |
| `MIN_BALANCED_ACCURACY` | 0.40 | `PS3_MIN_BALANCED_ACCURACY` |

The second condition is the one that matters most and is the least visible. It
compares the model against always predicting the majority class on the same
validation split. A model can score respectably and still add nothing.

## Measured, published run of 2026-08-29

Target `component_attribution`, per `model_scope`:

| Scope | Candidate | macro-F1 | Lift over majority | Verdict against 0.45 gate | Verdict against 0.80 contract |
|---|---|---|---|---|---|
| GATE | ExtraTrees_balanced | 0.677 | 0.477 | PASSED | Fails |
| GATE | RandomForest_balanced | 0.650 | 0.449 | PASSED | Fails |
| TVM | ExtraTrees_balanced | 0.237 | not recorded here | Fails the 0.45 floor outright | Fails |
| TVM | RandomForest_balanced | 0.214 | not recorded here | Fails the 0.45 floor outright | Fails |
| VALIDATOR | ExtraTrees_balanced | 0.541 | 0.047 | Clears 0.45, fails the 0.05 lift | Fails |
| VALIDATOR | RandomForest_balanced | 0.512 | 0.018 | Clears 0.45, fails the 0.05 lift | Fails |
| POOLED_FALLBACK | ExtraTrees_balanced | 0.261 | not recorded here | Fails | Fails |
| POOLED_FALLBACK | RandomForest_balanced | 0.231 | not recorded here | Fails | Fails |

The VALIDATOR rows are the ones most likely to be misread. A macro-F1 of 0.541
looks like a working model until it is set beside a majority-class baseline it
beats by 0.047. At that lift the model is, in practice, an expensive way of
naming the most common component.

The pooled fallback exists so that a fleet without its own model still gets a
prediction. It fails its own gate too, so there is no backstop.

## Consequence

Only GATE has a publishable component-attribution model. TVM and VALIDATOR have
none, and the pooled fallback that would have covered them has none either.

On the run committed in this repository (see below), TVM and VALIDATOR together
account for 15,791 of 54,239 OOS episodes, or 29.1 percent. Roughly three in ten
out-of-service episodes therefore have no publishable component-attribution
model of any kind. The equivalent per-fleet split for the 2026-08-29 run is not
in this repository, so the exact share for that run is not restated here.

## Corroboration from the run committed in this repository

The 2026-08-29 scorecard is a published-run artefact and lives in the serving
database (`ps3_v25_model_scorecard`, served by `/ps3/v25/model-scorecard`). It
is not a file in this repository, so the eight figures in the table above cannot
be recomputed from the repository alone.

The run whose outputs *are* committed in the notebook is an earlier one:
executed 2026-08-09, with `DATA_AS_OF_DATE` 2026-04-11 and 54,239 OOS episodes.
Its scorecard reaches exactly the same verdict:

| Scope | Candidate | macro-F1 | Lift | Gate |
|---|---|---|---|---|
| GATE | ExtraTrees_balanced | 0.545 | 0.360 | passed |
| GATE | RandomForest_balanced | 0.583 | 0.398 | passed |
| TVM | ExtraTrees_balanced | 0.196 | 0.112 | not_passed |
| TVM | RandomForest_balanced | 0.196 | 0.112 | not_passed |
| VALIDATOR | ExtraTrees_balanced | 0.517 | 0.022 | not_passed |
| VALIDATOR | RandomForest_balanced | 0.495 | 0.000 | not_passed |
| POOLED_FALLBACK | ExtraTrees_balanced | 0.245 | 0.132 | not_passed |
| POOLED_FALLBACK | RandomForest_balanced | 0.222 | 0.110 | not_passed |

Two independent runs, four months apart in as-of date, land on the same
structure: GATE publishable, everything else not. The VALIDATOR RandomForest row
scores 0.495 against a majority baseline of 0.495, a lift of exactly zero. The
pattern is a property of the data, not of one run.

## The caveat that must travel with any PS3 performance number

An earlier PS3 model scored macro-F1 0.905 on `failure_level_label`. That number
must not be quoted as a target, a baseline, or a regression, for one reason:

- The label was EDW-native. The benchmark was not.
- Approximately 79 percent of that model was driven by `sn_event_code_id`, the
  incident's own ServiceNow event code (SHAP 0.7916, next feature 0.14; solo AUC
  0.758). Recorded in
  `api/lambda/cubic-mars-dashboard-api/sql/03_phase1b_ps3_severity.sql`.
- It was also a different task. It was a two-class severity classifier
  (MAJOR vs CRITICAL), deliberately named `ps3_severity_*` rather than
  `ps3_root_cause_*` so the two would never be conflated. The current work is
  multi-class component attribution.

A native-feature rebuild does not have `sn_event_code_id` available and is not
trying to predict the same thing. Comparing 0.677 to 0.905 compares a model that
infers the answer with a model that was substantially told it, on a different
question. If 0.905 appears in any PS3 discussion, this paragraph goes with it.

## What is verified against this repository, and what is not

Verified here:

- The three gate constants and the expression that combines them:
  `notebooks/ps3_root_cause_analysis/PS3_V26_PRODUCTION.ipynb`.
- The 2026-08-09 / as-of 2026-04-11 scorecard, episode total 54,239, and the
  per-fleet episode split (GATE 38,448, TVM 5,665, VALIDATOR 10,126): committed
  output cells in the same notebook.
- The 0.905 / `sn_event_code_id` / 79 percent caveat:
  `api/lambda/cubic-mars-dashboard-api/sql/03_phase1b_ps3_severity.sql`.

Not verified here:

- The eight measured figures for the 2026-08-29 run. They are carried from that
  run's published scorecard; the repository holds no artefact from it.
- The contracted floor of macro-F1 >= 0.80. No file in this repository contains
  Table 13 or states an 0.80 acceptance threshold. The figure is carried from
  the contract and is not reproducible from this repository. It should be
  re-read from the solution document before this paper is used externally.
