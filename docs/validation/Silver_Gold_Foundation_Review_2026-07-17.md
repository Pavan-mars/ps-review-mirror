# Silver / Gold Foundation Review — PS1–PS5

**Date:** 2026-07-17  ·  **Reviewed commit:** `c870875` (branch `main`)
**Scope:** silver `S03, S06, S06b, S07, S10, S26, S27, S28, S29` + gold `device_ps1_daily`, `device_ps2_chains`, `device_ps3_incident`, `device_ps4_hourly`, `device_ps5_component`
**Reference:** `CUBIC_MARS_Chicago_Data_Foundation_PS1-PS5` (device identity, serial mapping, per-device-type failure sources, decoders)

---

## 1. Verdict

The silver/gold rebuild is **essentially complete and correct**. Almost every decision from the 16–17 Jul data-foundation work is already implemented — several tables were updated the same day (S10 v2, S26 VALIDATOR fix, PS1 R7, S27/S28/S29 new). Code quality is high: contract-driven builds, explicit fix logs, grain-dedup guards, and honest data-gap documentation throughout.

Two substantive items remain open (Section 6): the **AVM classification in S06**, and the **PS5 device-vs-component** decision. Everything else is either done or a model-notebook discipline (PS3 leakage scan, PS4 rate validation).

Status legend: **OK** = correct as-is · **FLAG** = needs a decision or a follow-up validation.

---

## 2. Silver layer

| Table | Status | Notes |
|---|---|---|
| **S03 `dim_event_matrix`** | OK | Authoritative OOS flags (`is_oos`, `is_commanded_oos`, `oos_counted_{gate,bus,fmvd,central}_kpi`, `event_priority`). Header states the correct rule: *"Matrix is the definitive OOS/KPI flag source. Use this table, not SEVERITY logic."* |
| **S07 `dim_event_type`** | OK | `is_oos_event` rewritten (2026-06-16) from the Cubic doc OOS whitelist → 102 OOS events (was 7 via severity). `component_subsystem` maps all 441 event types (no `OTHER`). Correctly abandons the SEVERITY approach (94.8% NULL). |
| **S06 `dim_device`** | **FLAG** | SCD2 (`is_current`), OPERATOR_ID guard for BMV bus, R2/R3 scope changes, orphan rescue (S06b) — all good. **But `AVM`/`EVM` are still mapped to `'TVM'`** (R3 comment: "AVM/EVM remain in TVM"). This predates Michael's 16-Jul call ("AVM = legacy CTA vending, not Ventra → drop"). Consequence: the "TVM" fleet is inflated to ~1,019 (≈513 real TVM + 431 AVM + ~75 EVM). See Section 6.1. |
| **S06b `dim_device` completion** | OK | Read-only diagnostic + guarded synthetic-row backfill for in-scope orphans (sentinel OPERATOR_ID). Idempotent. |
| **S10 `metric_daily` v2** | OK / minor | Correctly M401-only; documents 701–705 absent at source; adds `p95/p99`, `slow_tap_count/pct`, `rolling_7d_avg`, `z_score_vs_28d`; has the `<= CURRENT_DATE()` future-date filter. **Minor:** the "stalled after 2025-11-07" comment is stale — bronze `edw_device_metric` M401 now has data through ~Apr 2026 (diagnostic 2026-07-17), so a fresh S10 run should reach Apr 2026; re-run and update the note. **Note for PS1/PS4:** TVM has 0 M401 rows — TVM timing is comms-event-only. |
| **S26 `device_failures`** | OK (excellent) | Unified chargeable-failure spine. TVM/GATE from `edw_availability_events` (`FAILURE_LEVEL>0 AND EXCLUDED=0`); **VALIDATOR from `device_event_enriched` OOS `Set` events via the matrix flag** (`is_hardware_oos_event`) — not severity, not a keyword classifier. Header correctly states "BMV failures are NOT in availability_events." AVM guarded (`NOT LIKE 'AVM%'`). |
| **S27 `station_network_daily`** | OK | Station-level co-failure summary per (FACILITY_ID, category, day); `is_coordinated_failure` (≥3 devices), `is_major_station_event` (≥5). Real replacement for the empty SVN_STAGE CI stubs; feeds PS2. |
| **S28 `device_mttr`** | OK | Rolling MTTR per device-failure-day using **`RANGE BETWEEN INTERVAL N DAYS`** (calendar-correct windows). Covers all categories (VALIDATOR from `duration_to_clear_min`), filling the S24 VALIDATOR 0% gap. |
| **S29 `device_survival_intervals`** | OK | Proper survival grain: `past_intervals` (both ends known), `ongoing_intervals` (right-censored to CURRENT_DATE), `first_intervals` (left-censored from 2024-01-01), with `is_ongoing`/`is_first_interval`. Never-failed devices intentionally have no rows (handle as one long censored interval downstream). |

---

## 3. Gold layer

### PS1 — `device_ps1_daily` — OK (depends on AVM decision)
- **R6-1 (2026-07-17)** pivots VALIDATOR into PS1 via `device_failures` OOS `Set` dates — validators enter PS1 for the first time.
- M401 substitute features wired (`p95/p99`, `slow_tap`, `rolling`, `z_score`); AFC-switch labels excluded as network infra.
- Labels `will_fail_{3,7,14}d`; chargeable = `is_hardware_oos AND is_chargeable AND failure_level IN (2,3,4,5)`.
- **FLAG:** the spine filters `mars_device_category IN ('TVM','GATE','VALIDATOR')` with no AVM guard — so if S06 keeps AVM=TVM, **431 AVM ghosts enter the PS1 TVM spine** (the header even asserts "AVM excluded by category='OTHER'", which is currently false). Resolve via Section 6.1.
- Housekeeping: stale header lines still say "VALIDATOR excluded" above the R6-1 block that includes it.

### PS2 — `device_ps2_chains` — OK (excellent)
- Fault-chain features per device-day (≥2 OOS `Set` onsets), TVM/GATE/VALIDATOR.
- Integrates **S27 station co-failure** (`is_coordinated_station_failure`, `station_devices_failed`) and **S29 survival age** (`days_healthy_before_chain`) — both new 2026-07-17.
- OOS matrix `Set`-onset filter (no severity), BMV multi-`DEVICE_KEY`-per-day handled cleanly, SVN_STAGE CI features honestly NULL but replaced by real station evidence.
- Descriptive/analysis table (no endpoint), consistent with program canon.

### PS3 — `device_ps3_incident` — OK, with model-side leakage discipline required
- **Target = structured `failure_level`** (cat-2 severity 1/2/3/4/5/16, Michael-confirmed taxonomy) — honest; not free-text root cause.
- Grain-dedup (`QUALIFY ROW_NUMBER` on `availability_event_id`, the 4× SN fan-out) and hw-match fan-out (1.41×) both fixed.
- Structured cause paths added: `component_derived` (regex + `dim_event_type`) and `kpi_cause_class` (`edw_kpi_rules` EVENT_ID + failure_level → KPI_ID).
- **FLAG (notebook):** the table still carries incident text (`AE_FAULT_DESCRIPTION/SYMPTOM/PROBLEM/RESOLUTION`) and derived keyword flags incl. **post-incident `AE_RESOLUTION`** (`desc_replacement_flag`). The **leakage scan must run in the PS3 notebook** — drop `AE_RESOLUTION`/post-outcome text, keep the solo-AUC < 0.95 guard (this is the earlier "fake 1.00" lesson). Also **relabel "Failure Severity", not "Root Cause"** (true 9-class root-cause remains blocked on SVN_STAGE = 0 rows). **VALIDATOR = 0 incidents** (validators are in device_event, not incidents) → PS3 is effectively TVM+GATE. Validate `kpi_rule_id` fill rate (was 0%).

### PS4 — `device_ps4_hourly` — OK (excellent)
- 3-signal ensemble: (1) event-rate anomaly, (2) **metric anomaly on METRIC_401**, (3) reject-rate anomaly.
- **Signal 3 recalibrated** from a static 5% cutoff to a **per-device adaptive rolling-28-day 2σ** threshold — directly fixes the earlier over-firing (36% ensemble rate). Revenue-zero override retained.
- **FLAG (validate):** re-check the new ensemble rate (expect low single digits). **Note:** TVM has 0 M401 → Signal 2 is TVM-inactive (TVM anomaly = event-rate + reject-rate); Signal 2 depends on the S05/S10 metric refresh (M401 stall).

### PS5 — `device_ps5_component` — FLAG (decision needed)
- Still **component-level** (grain = device_id × component_serial_nbr), sourced from `hw_config_current` + `device_outage`, with `days_to_failure` + `is_censored`.
- **Does not use the new device-level `device_survival_intervals` (S29).** Component matching is what produced ~100% censoring previously. Decision: add a **device-level PS5 off S29** (honest primary — TVM/GATE/BMV all trainable) and keep component-level as an enhancement; or, if keeping component-level, **validate the censoring rate is not ~100%**. Also confirm `REPORTED_CHANGED_DTM` reliability (moves only on real swaps).

---

## 4. What the foundation asked for vs what's built

| Foundation decision | Implemented? |
|---|---|
| BMV failures from `device_event` OOS (not availability/incidents) | Yes — S26 + PS1 R6-1 |
| Use `dim_event_matrix` OOS flag, not SEVERITY | Yes — S03/S07, used in S26/PS2/PS3/PS4 |
| M401-only timing + reader/comms events; 701–705 dropped | Yes — S10 v2, PS1, PS4 |
| Future-date filter (`<= CURRENT_DATE()`) | Yes — S10, PS4 |
| Adaptive PS4 anomaly thresholds (fix static 5%) | Yes — PS4 Signal 3 |
| Station-network cascade signal | Yes — S27 → PS2 |
| Device-level survival + MTTR incl. validators | Yes — S28, S29 (PS5 gold not yet wired to S29) |
| AFC-switch labels excluded as network infra | Yes — PS1 |
| Drop AVM as legacy vending | **Not yet** — S06 still AVM→TVM |
| PS3 = structured severity, not text root cause | Yes (target); leakage guard is notebook-side |

---

## 5. Cross-cutting findings

1. **AVM classification inconsistency (highest priority).** `S06` maps `AVM`/`EVM` → `TVM`, but the newer `S26` and `device_ps1_daily` assume `AVM = OTHER`. Since `DEVICE_TYPE_NAME='AVM'` exists, S06 currently produces AVM=TVM, inflating the TVM fleet to ~1,019 and pushing 431 AVM ghosts into the PS1 spine (only S26 has a `NOT LIKE 'AVM%'` band-aid).
2. **M401 freshness.** The stall note in S10 is stale vs the current bronze (data to ~Apr 2026). A fresh S10 run restores currency; verify + update the note. TVM has no M401 at all.
3. **PS3 leakage is deferred to the model.** The gold is a feature superset (incl. post-incident text); the honest-model discipline (drop resolution text, run the leakage scan) must live in the PS3 notebook.
4. **PS5 is not yet on the device-level survival table** that was built for it (S29).

---

## 6. Open decisions / action items

### 6.1 AVM in S06 — decision required (not patched here)
Verify:
```sql
SELECT mars_device_category, COUNT(*)
FROM mars_dev.silver.dim_device
WHERE DEVICE_ID LIKE 'AVM%' AND is_current = TRUE
GROUP BY 1;
```
If it returns `TVM`, patch S06 to move `AVM` (and confirm `EVM`) out of the TVM branches into `OTHER`. That drops "TVM" to ~513 and clears the ghosts in S26, S27, PS1, PS4.

### 6.2 PS5 device-vs-component — decision required
Add a device-level PS5 gold built from `device_survival_intervals` (S29) as the honest primary; retain component-level as an enhancement. Or validate the current component-level censoring rate.

### 6.3 Validations to run
- New PS4 ensemble rate by category (expect low single digits).
- PS3 `kpi_rule_id` fill rate (was 0%).
- S10 max `transit_day` after re-run (expect ~Apr 2026).
- `device_failures` device counts: TVM (post-AVM decision), GATE ~1,382, VALIDATOR ~4,218.

---

## 7. Per-PS modeling readiness

| PS | Gold ready? | Before training |
|---|---|---|
| PS1 | Yes | Resolve AVM (6.1); gate on PR-AUC/recall; TVM timing = comms-events only |
| PS2 | Yes (descriptive) | Validate chain + station-cascade features |
| PS3 | Yes | Run leakage scan in notebook; relabel Severity; validate kpi_rule_id; TVM+GATE only |
| PS4 | Yes | Validate ensemble rate; TVM Signal 2 inactive; refresh S05/S10 |
| PS5 | Partial | Decide device-level (S29) vs component; validate censoring |

---

*Reviewer: Mars-Techs (PK). Grounded in the repo SQL at commit `c870875` and the live `mars_dev` bronze probes (16–17 Jul 2026).*
