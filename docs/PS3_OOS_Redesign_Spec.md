# PS3 redesign — hybrid OOS/availability spine + binary severity

**Date:** 2026-07-26 · **Decisions taken:** hybrid spine (severity on availability events,
root cause on the hardware-OOS spine); severity target = binary MAJOR/CRITICAL collapse.

Every table, column and row count below is from the `chicago-data-catalog` skill
(snapshot 2026-07-17) or the repo DDL — none of it is estimated.

---

## 1. Why the 26-Jul run failed, in one number

| Source | Rows | Window |
|---|---|---|
| `silver.incident_root_cause` | **375,533** | 2017-03-17 → 2026-04-11 |
| `gold.device_ps3_incident` (what PS3 trains on) | **34,612** | 2024-01-01 → 2026-04-11 |

The gold build discards ~341,000 rows — **91% of its own source** — through two filters in
`sql/gold/device_ps3_incident__create.sql`:

```sql
WHERE AE_FAILURE_LEVEL IN (1,2,3,4,5,16)   -- hardcoded "cat-2 hardware faults"
  AND transit_day >= '2023-07-01'
```

All four heads then failed their gate:

| Head | Champion | Val macro-F1 | Floor | Test macro-F1 | Failure reason |
|---|---|---|---|---|---|
| TVM / severity | XGBoost_Optuna | 0.478 | 0.55 | 0.533 | below floor; train-val gap 0.330 |
| TVM / root_cause | CatBoost | 0.349 | 0.45 | 0.346 | below floor; train-val gap 0.453 |
| GATE / severity | **BaselineMostFrequent** | 0.499 | 0.55 | 0.498 | below floor; delta vs baseline 0.000 |
| GATE / root_cause | CatBoost | 0.551 | 0.45 | 0.562 | gap 0.449; test ECE 0.192 |

Data quality was perfect (score 100.0, gate pass). This is a **population and target-design
problem, not a data-quality or modelling-effort problem.**

---

## 2. Target architecture

### Severity head — availability spine, widened, binary target

Spine stays `incident_root_cause` (it is the only source carrying `AE_FAILURE_LEVEL`).
Three changes:

1. **Read the silver table, not the pre-filtered gold.** The notebook already knows the path
   (`SILVER_PREFIX`); it currently loads `incident_root_cause` only for the ServiceNow
   conformance probe. Point the training loader at it.
2. **Replace the hardcoded level list with a `dim_failure_level` join.** That dim carries
   `is_device_fault|BOOLEAN`, `is_operational|BOOLEAN` and `severity_ordinal|INT` — the
   principled version of `IN (1,2,3,4,5,16)`. Filter `is_device_fault = TRUE`.
3. **Target = the binary MAJOR/CRITICAL collapse**, not `failure_level_label`. Nothing
   downstream consumes the raw multiclass label — the dashboard reads
   `pred_severity_collapsed`. Two roughly balanced classes instead of 4-5 skewed ones lifts
   macro-F1 structurally, and removes the collapse-mapping bug class entirely (we have been
   bitten by it twice).

Excluding chargeable events, if wanted, is now a one-line filter — `incident_root_cause`
carries `is_chargeable|BOOLEAN` alongside `is_device_fault|BOOLEAN`. **This is the part of
"OOS not chargeable" that is achievable without losing the severity target.**

### Root-cause head — hardware-OOS spine

| Table | Rows | Why |
|---|---|---|
| `silver.device_failures` | 668,418 | OOS-derived; **covers VALIDATOR (1,535 devices)** |
| `silver.device_event_enriched` | 190,874,858 | the raw OOS spine; `is_hardware_oos_event = TRUE AND EVENT_STATE_TYPE_NAME = 'Set'` |

Target `derived_component_type` comes from the `hw_config_current` join
(`COMPONENT_DESCRIPTION` / `COMPONENT_SERIAL_NBR`), which attaches to OOS events perfectly
well — it does not depend on the availability event. This is what finally makes **VALIDATOR
modellable**, and it aligns PS3 with PS1's R7-1 hardware-OOS label and PS5's hybrid spine.

> **This head needs a new gold table.** It cannot be done as a notebook-only change —
> `device_ps3_incident` is grained on `availability_event_id`. Proposal:
> `gold.device_ps3_oos_component`, grained `(device_id, oos_event_id)`, built at L4 after
> `device_failures`. Notebook change alone is not sufficient here; be clear-eyed about that.

---

## 3. Fixes to make in the same pass

| # | Fix | Evidence |
|---|---|---|
| 1 | **Retire GATE/severity.** | Champion is `BaselineMostFrequent` — the bake-off could not beat guessing. Train balance 1,410 vs 9. No signal exists. |
| 2 | **Consolidate TVM root-cause classes.** | 11 classes incl. literal `'None'` (2,057 rows, 7.8%), `OTHER` (1 row), `PRINTER` (41). macro-F1 averages over classes, so near-empty classes cap it near 0.35 regardless of model. |
| 3 | **Regularise.** | 3 of 4 heads failed on train-val gap (0.330 / 0.453 / 0.449); `RandomForest` hit train F1 = 1.000. Cap `max_depth`, `min_samples_leaf`; early stopping. |
| 4 | **Calibrate.** | GATE/root_cause fails only on gap + ECE 0.192 vs 0.15. Isotonic/Platt on the validation fold. **Closest head to passing.** |
| 5 | **Fix `severity_bucket`'s MAJOR fallback.** | It still ends `return "MAJOR"` for any unmapped code — a softer form of the bug that zeroed `pct_critical_pred`. Should return `UNKNOWN`. |
| 6 | **Dedup `incident_root_cause`.** | The catalog's **only validation FAIL** — 670 dups on `availability_event_id`. Widening the spine makes this bite harder; dedup at S17. |
| 7 | **Drop `kpi_rule_id`.** | 0.0% fill (catalog action item #3). Dead column. |

---

## 4. Feature additions from the catalog

Already joined and working (26-Jul coverage in brackets):
`usage_lifecycle_daily` [94.5% TVM / 98.8% GATE] · `station_network_daily` [85.4% / **36.8%**]
· `metric_daily` [94.5% / 98.8%] · `device_uptime_intervals`.

> `station_network_daily` at **36.8%** on GATE is weak — worth checking the
> `FACILITY_ID + mars_device_category` join key before trusting those five features for GATE.

Not yet used, available, and relevant:

| Source | Columns to add | Rationale |
|---|---|---|
| `dim_failure_level` | `severity_ordinal`, `is_device_fault`, `is_operational`, `metric_category_name` | Replaces the hardcoded level list; ordinal severity is a far better structure than unordered classes |
| `device_survival_intervals` (175,447) | time-since-previous-failure, interval index | Direct recurrence signal; already powers PS5 |
| `device_mttr` | rolling MTTR per device | Repair-burden context |
| `incident_history` (68 cols) | `priority`, `severity`, `reopen_count`, `is_major_incident`, `cmdb_model_*`, `ci_fault_count`, `ci_warranty_expiration` | Model/warranty//repairability context — genuinely new signal, none of it currently used |
| `dim_event_matrix` (185 rows) | subsystem / OOS-flag decode | Maps raw events to subsystems for the OOS root-cause head |
| `maintenance_ledger` | `is_commanded_oos`, `is_maintenance_oos`, `component_subsystem` | **Separates planned from genuine failures** — likely a real accuracy win |

Leakage discipline is unchanged: every join stays `merge_asof(direction="backward")` with the
existing tolerance, and each new column must clear the single-feature audit. Note
`incident_history` fields are **post-incident** for the incident they describe — they may only
be used as *prior-window history for earlier incidents*, never for the row itself.

---

## 5. Sequence

1. **Profiling notebook first** — count rows under each candidate filter (`is_device_fault`,
   `is_chargeable`, per `severity_ordinal`, per category, per year) before committing. Cheap,
   and it turns the widening from an estimate into a measurement.
2. Notebook change: silver spine + binary severity + fixes 3/4/5 + `dim_failure_level`,
   `device_survival_intervals`, `maintenance_ledger` features. Re-run → this alone should move
   TVM severity materially.
3. Retire GATE/severity (decision, not code).
4. New `gold.device_ps3_oos_component` + root-cause head on the OOS spine → VALIDATOR coverage.
5. Only then wire to the dashboard.

Step 2 is notebook-only and testable on the synthetic fixture. Step 4 needs Databricks.

## 6. Honest limits

Everything above is grounded in the catalog snapshot and the executed run's own metrics. What
is **not** verified: the actual row counts surviving each candidate filter (step 1 exists to
measure them), whether the widened population is temporally comparable to the current window,
and whether `device_failures`' component attribution is dense enough for a VALIDATOR root-cause
target. None of these can be checked from here — they need a Databricks run.
