# GOLD CREATE Script Validation — CUBIC MARS Chicago / Ventra
**Layer:** `mars_dev.gold.*` (Databricks Spark SQL / Unity Catalog)
**Scope:** 5 finalized GOLD CREATE scripts (one per PS), validated against design checklist
**Date:** 2026-06-23
**Source of truth read:** all 5 gold files + all referenced silver DDLs (S01–S14)

---

## 0. Executive Summary

| File | Target table | PS | Grain | Build | Status |
|------|--------------|----|-------|-------|--------|
| device_ps1_daily__create.sql | `mars_dev.gold.device_ps1_daily` | PS1 Failure Prediction | (device_id, transit_day) | DROP + CREATE TABLE | **HIGH** (label semantics) |
| device_ps2_chains__create.sql | `mars_dev.gold.device_ps2_chains` | PS2 Cascading Failure | (device_id, transit_day), ≥2 fault onsets | DROP + CREATE TABLE | **OK** (minor) |
| device_ps3_incident__create.sql | `mars_dev.gold.device_ps3_incident` | PS3 Root Cause | (device_id, availability_event_id) | DROP + CREATE TABLE | **OK** |
| device_ps4_hourly__create.sql | `mars_dev.gold.device_ps4_hourly` | PS4 Anomaly | (device_id, hour_bucket) | DROP + CREATE TABLE | **OK** (minor) |
| device_ps5_component__create.sql | `mars_dev.gold.device_ps5_component` | PS5 RUL / Survival | (device_id, component_serial_nbr) | DROP + CREATE TABLE | **MEDIUM** (component grain caveat) |

**All 3-part UC names correct (`mars_dev.gold.*`). All read from SILVER only** (PS2/PS5 additionally read `mars_dev.bronze.ncs_stage_cashbox_tracking` for cashbox context — a governed bronze Delta table, acceptable; not raw S3). City handled as metadata (no per-city tables); device_id is the join key. No CREATE INDEX (Delta-safe). Pre-2024 excluded on the event-driven tables.

### Source-table existence check (SILVER) — ALL PASS
Every silver table a gold script reads EXISTS in `sql/silver/` with a matching `CREATE TABLE mars_dev.silver.<name>`:

| Silver table | File | Read by |
|--------------|------|---------|
| dim_device | 01 | PS1, PS2, PS4 |
| device_event_enriched | 03 | PS1, PS2, PS3, PS4 |
| device_outage | 04 | PS1, PS5 |
| hw_config_current | 06 | PS3, PS5 |
| metric_daily | 07 | PS1 |
| kpi_daily | 09 | PS1 |
| tap_event_daily | 10 | PS1, PS4 |
| incident_root_cause | 11 | PS3 |
| tvm_sale_daily | 13 | PS1 |
| metric_hourly | 14 | PS4 |

No missing source tables. No missing source columns detected (every referenced column was confirmed in the silver final SELECT lists — details under each file).

### Severity counts (issues found)
- BLOCKER: **0**
- HIGH: **2** (both in PS1 — label leakage + label-name/scope mismatch)
- MEDIUM: **3** (PS5 grain caveat; PS5 dead 'READER' filter value; PS5/PS2 cashbox bronze read = governance note)
- LOW: **4** (PS1 kpi/metric/tap/sales feature leakage-lite, PS4 minor, doc/comment drift, expected-row comments stale)
- OK: the structural design of all 5 (grain, sources, 3-part names, city-as-metadata, no-index) is correct.

### Explicit checklist answers (evidence quoted)
- **(a) PS1 label = FULL material-failure definition?**  → **NO — PARTIAL.** PS1 does NOT encode the prescribed label. See §1.
- **(b) PS3 target = FAILURE_LEVEL?**  → **YES.** See §3.
- **(c) PS5 = component-grain time-to-event RUL?**  → **YES (grain + time-to-event), with a real-world caveat** that the component identity comes from `COMPONENT_SERIAL_NBR` which is ~91.8% NULL in this tenant. See §5.

---

## 1. device_ps1_daily — PS1 Failure Prediction  [STATUS: HIGH]

- **Target table:** `mars_dev.gold.device_ps1_daily`
- **PS:** PS1 (predictive failure)
- **Grain:** `(DEVICE_ID, transit_day)` — daily device grain. CORRECT.
- **Source SILVER tables (all EXIST):** dim_device (S01), device_event_enriched (S03), device_outage (S04), metric_daily (S07), kpi_daily (S09), tap_event_daily (S10), tvm_sale_daily (S13).
- **Device scope:** `mars_device_category IN ('TVM','GATE','VALIDATOR')`, `is_current = TRUE`. READER correctly excluded (reframed as component). CORRECT per 2026-06-23 taxonomy.
- **Filter / training window:** spine `WHERE ed.transit_day >= '2024-01-01'`. CORRECT (2024+).

### Label / target — the key finding
The script's label is:
```
CASE WHEN old.DEVICE_ID IS NOT NULL THEN 1 ELSE 0 END AS will_fail_7d
```
where `outage_label_days` (`old`) is built as:
```
FROM mars_dev.silver.device_outage do2
CROSS JOIN (... 1..7 ...) seq
WHERE do2.duration_min > 0
  AND do2.mars_device_category IN ('TVM','GATE','VALIDATOR')
```
i.e. **label = "device has ANY outage with duration_min > 0 within the next 7 days."**

**This does NOT match the prescribed material-hardware-failure definition:**
> `is_hardware_oos AND NOT (maintenance OR commanded OOS) AND NOT excluded AND NOT relieved AND failure_level IN (1,2,3,4,5,16)`

Assessment of how much is actually encoded:
- `is_hardware_oos`  → **PARTIALLY, upstream.** `silver.device_outage` is itself built from `is_hardware_oos_event = TRUE` (S04 header: "Filter to is_hardware_oos_event = TRUE … Commanded/maintenance codes excluded (106,110,151,208,519,1603,1604)"). So **"hardware OOS" AND "NOT commanded/maintenance" are inherited from S04** — that part is satisfied transitively.
- `NOT excluded`  → **NOT applied.** No `EXCLUDED`-flag filter anywhere; device_outage carries no exclusion concept.
- `NOT relieved`  → **NOT applied.** No relief/RELIEF_VALUE join. (The `failure_ledger` keystone has the relief logic; PS1 here does not use failure_ledger at all.)
- `failure_level IN (1,2,3,4,5,16)`  → **NOT applied.** device_outage has no FAILURE_LEVEL column; the label is duration-based only, NOT failure-level-gated. The script header even renames the concept: "Target: will_fail_7d — 1 if device has a real outage (duration_min > 0)".

**Conclusion (a):** PARTIAL. Two of the five clauses (hardware-OOS, not-commanded/maintenance) are inherited from S04; the **NOT-excluded, NOT-relieved, and failure_level∈(1,2,3,4,5,16) clauses are absent.** The label is materially looser than the design and will admit non-material outages. The design contract defines the material-failure label via the `failure_ledger` spine (avail_events + relief + levels + failure_level IN (1,2,3,4,5,16)); this PS1 builder bypasses that and labels straight off `device_outage.duration_min > 0`.

> NOTE: this is a deliberate, documented choice in the file (FIX 8 / V08): the severity-based label gave only 17 positives, so they switched to `duration_min > 0` (~3.66M label pairs). It fixed the "too few positives" problem but it is **not** the prescribed material-failure label. The header's own positive-rate expectation ("positive_rate_pct 20-60%") is far above the design's "~1-2%", which corroborates that the label is too broad.

### Label leakage  [HIGH]
The 7-day label is an **explode-BACKWARD** construction: for each future outage day it stamps `DATE_ADD(transit_day, -n)` for n=1..7, then equi-joins `old.label_day = sp.transit_day`. Mechanically this correctly puts a *future* outage's signal onto *earlier* feature rows (label looks forward, good). **However:**
- The **same-day features are computed from the same `device_outage`/event data that can also produce the label.** Specifically the feature block includes `outage_count`, `total_outage_min`, `max_outage_min`, `oos_event_count`, `hardware_oos_count`, and the 7-day rolling `outages_7d` / `outage_min_7d` / `hardware_oos_events_7d`. Because the label fires when there is an outage on **any of days t+1..t+7**, and the rolling windows are **trailing** (ROWS 6 PRECEDING … CURRENT), a row at day *t* whose label=1 (outage at t+1) will frequently also be a row that, at day *t+1..t+7*, carries non-zero outage features — but those are **different rows**. For row *t* itself the trailing features do NOT see t+1. So the rolling/trailing features are **not** directly leaky.
- The real leakage risk is **`outage_count`/`total_outage_min`/`hardware_oos_count` on day *t* when the label for an *earlier* row (t-1..t-7) points at this same outage.** That is expected (the earlier row is allowed to know a failure is coming only via the label, not via features) — and those features live on row *t*, not the earlier row, so again no direct same-row leak.
- **Bottom line:** there is **no hard same-row target leak** (features at *t* never include t+1..t+7). The HIGH flag is for **(i)** the label-definition mismatch above and **(ii)** a softer concern: `oos_event_count` / `hardware_oos_count` / `outage_count` on the SAME day as a label=1 fire (outage today AND outage in next 7d) are highly collinear with the outcome and effectively encode "device is currently failing," which for a 7-day-ahead predictor is borderline. Recommend the modeller drop or lag the same-day outage-magnitude features, or confirm they are intended as "current state" inputs.

### Feature columns (summary)
Daily event counts + subsystem breakdown (BHU/CHU/PRINTER/GATE_MECH/CSC_READER/BANKCARD/SYSTEM/SCRST/COMMS), oos/hardware-oos counts; outage_count/total/max; 7d & 30d rolling event/critical/comms/oos/outage features + availability_pct_7d; tap features (tap_count, unique_cards, reject_rate, peak_hour); kpi features (avg/max/count/met/missed); metric_401 features; tvm sales features (count/error/cash/revenue/active_hours/7d avg/decline_flag). All referenced silver columns CONFIRMED present (m401_* in S07; tap cols in S10; sales cols in S13; kpi KPI_VALUE/KPI_ID/meets_target in S09; severity/component_subsystem/is_hardware_oos_event/EVENT_STATE_TYPE_NAME/transit_day in S03).

### Other notes
- Fan-out controls look correct: kpi pre-aggregated (FIX 2), tap future-date filter (FIX 5/`<= CURRENT_DATE()`), `is_current = TRUE` on dim_device, metric_daily joined on (DEVICE_ID, transit_day). The LEFT JOIN to `metric_daily` is on DEVICE_ID — note S07 keys on DEVICE_KEY internally but exposes DEVICE_ID via its dim_device join, so the join is valid (coverage ~26% as documented).  [OK / LOW]
- ISSUES:
  - **HIGH** — label is not the prescribed material-failure definition (missing NOT-excluded, NOT-relieved, failure_level∈(1,2,3,4,5,16)); positive rate will be ~20-60% not ~1-2%.
  - **HIGH** — same-day outage-magnitude features collinear with a 7-day-ahead label (borderline leak; advise lag/drop).
  - **LOW** — header comment block is long but accurate; verification block expects 20-60% positives (confirms over-broad label).

---

## 2. device_ps2_chains — PS2 Cascading / Fault-Chain  [STATUS: OK, minor]

- **Target table:** `mars_dev.gold.device_ps2_chains`
- **PS:** PS2 (cascading failure; feeds FP-growth / Markov)
- **Grain:** `(DEVICE_ID, transit_day)` for device-days with **≥2 fault-onset OOS events** (`days_with_cascade … HAVING COUNT(*) >= 2`). CORRECT for sequence/co-occurrence mining.
- **Source SILVER (all EXIST):** dim_device (S01), device_event_enriched (S03). Plus bronze `ncs_stage_cashbox_tracking` (cashbox context).
- **Sequence + time window:** builds ordered per-device-day chains:
  - `subsystem_chain`, `event_type_chain`, `severity_chain` via `array_join(transform(array_sort(collect_list(struct(EVENT_DTM, val))), x->x.val), '->')` → **ordered by event time within the day** = the sequence FP-growth/Markov needs. CORRECT.
  - Time window = the **transit_day** plus `chain_span_min = (unix_timestamp(MAX(EVENT_DTM)) - unix_timestamp(MIN(EVENT_DTM)))/60.0`, `chain_start_dtm`/`chain_end_dtm`. The "window" is therefore a calendar-day bucket with intra-day ordering and span — a reasonable cascade window.  [OK]
- **Fault filter:** `is_hardware_oos_event = TRUE AND EVENT_STATE_TYPE_NAME = 'Set' AND transit_day >= '2024-01-01'`. Fault-onset only (not Clear), hardware-only, 2024+. CORRECT and consistent with PS1/PS3 logic.
- **Target column:** `subsystem_chain` (ordered sequence) + flags (has_cascade, has_cash_cascade, has_printer/gate_mech/csc_reader/dopp/scrst_in_chain, distinct_subsystems, first/last_subsystem). Appropriate for cascade modelling.
- **Column existence:** all S03 columns used (DEVICE_KEY, mars_device_category, EVENT_DTM, DW_DEVICE_EVENT_ID, component_subsystem, severity, EVENT_TYPE_ID, EVENT_TYPE_NAME, EVENT_STATE_TYPE_NAME, COMPONENT_TYPE_NAME, COMPONENT_SERIAL_NBR, is_hardware_oos_event) CONFIRMED in S03 final SELECT. Cashbox cols (TRANSIT_DAY_KEY, CASHBOX_TYPE_ID, VALUE_CASH, DUMP_COUNT) are asserted from V07a schema (bronze, not re-verified here — bronze table not in sql/silver).
- **Leakage risk:** N/A in the supervised sense — PS2 is unsupervised sequence mining; there is no forward label. OK.
- **ISSUES:**
  - **MEDIUM** — cashbox features read `mars_dev.bronze.ncs_stage_cashbox_tracking` directly from a GOLD script. Functionally fine (governed bronze Delta), but it is a *gold-reads-bronze* exception to the "gold reads silver only" rule. Consistent with PS5. Recommend either (a) accept as documented exception, or (b) stage cashbox into a silver table. (Same item appears in PS5.)
  - **LOW** — `has_cash_cascade` requires BHU AND CHU both present; correct, but GATE rows will always be FALSE (no cash subsystems) — expected.
  - **LOW** — `SVN_STAGE = 0 rows` → CI-dependency features hard-coded NULL/FALSE (`ci_related_devices`, `shared_facility_chain`, `svn_ci_data_available = FALSE`). Documented data gap, not a defect.

---

## 3. device_ps3_incident — PS3 Root Cause  [STATUS: OK]

- **Target table:** `mars_dev.gold.device_ps3_incident`
- **PS:** PS3 (root-cause / incident classification)
- **Grain:** `(device_id, availability_event_id)` — exactly 1 row per incident. CORRECT.
- **Source SILVER (all EXIST):** incident_root_cause (S11) primary; device_event_enriched (S03) for pre-incident windows; hw_config_current (S06) for component-age. All confirmed.
- **Device scope:** `mars_device_category IN ('TVM','GATE','VALIDATOR')` (READER removed). 2024+ filter applied. CORRECT.

### Target — checklist answer (b)
**Target = FAILURE_LEVEL (structured multiclass). YES.** Evidence:
- The incident filter:
  ```
  AND AE_FAILURE_LEVEL IN (1, 2, 3, 4, 5, 16)
  ```
- The exposed prediction target:
  ```
  ai.AE_FAILURE_LEVEL  AS failure_level,
  ai.failure_level_label,
  ai.root_cause_category,
  ```
The structured `AE_FAILURE_LEVEL` (Ventra KPI Failure Levels, per Michael 2026-06-22; levels 1/2/3/4/5/16 = cat-2 hardware faults) is the target — NOT free-text. The free-text fields (`AE_FAULT_DESCRIPTION`, `AE_SYMPTOM`, `AE_PROBLEM`, `AE_RESOLUTION`, `affected_component`) are carried as **secondary NLP features**, exactly as intended. `component_type`/component context is present via `hw_best_match` (matched_component, matched_serial_nbr, component_age_days). **Matches the design.**

- **Column existence:** S11 confirmed to emit AE_FAILURE_LEVEL, failure_level_label, root_cause_category, availability_event_id (= AE_EVENT_ID), AE_START_DTM/AE_END_DTM, incident_duration_min, affected_component (= SN_U_AFFECTED_COMPONENT), wot_state, request_type, transit_day, transit_day_key, DEVICE_KEY, DEVICE_NAME, DEVICE_TYPE_NAME, AE_FACILITY_ID/AE_OPERATOR_ID, FACILITY_NAME/OPERATOR_NAME, DEVICE_SERIAL_NUMBER, SN_U_EVENT_ID, SN_SYS_ID, AE_FAULT_STATE, from_cta_sn_mirror, from_svn_stage. **All present.** hw_config_current (S06) confirmed to emit COMPONENT_DESCRIPTION, COMPONENT_SERIAL_NBR, component_age_days, REPORTED_CHANGED_DTM, DEVICE_ID, mars_device_category. **All present.**
- **Fan-out control:** `hw_best_match` uses `ROW_NUMBER() … WHERE rn = 1` (priority: description match, then most-recent) → exactly 1 hw row per incident (FIX 7). The two pre-incident CTEs (`events_24h_prior`, `critical_7d_prior`) are GROUP BY availability_event_id, so the 3 LEFT JOINs are 1:1. No fan-out.  [OK]
- **Leakage risk:** pre-incident event features are strictly windowed BEFORE the incident:
  ```
  AND dee.EVENT_DTM >= ai.AE_START_DTM - INTERVAL 24 HOURS
  AND dee.EVENT_DTM <  ai.AE_START_DTM
  ```
  (and the 7-day version) — i.e. features end strictly before `AE_START_DTM`. **No look-ahead leakage.** Good design. The text/`affected_component` fields are observed-at-incident attributes used to predict the *category*, which is acceptable for root-cause classification. OK.
- **ISSUES:**
  - **LOW** — header "Expected output: ~122K rows" vs the verification block's "~66,962 total / major ~61K" are internally inconsistent comments (the post-build SELECT comment predates the FIX-8 levels-4/5/16 inclusion). Cosmetic only.
  - **LOW** — `INTERVAL 24 HOURS` / `INTERVAL 7 DAYS` are valid Spark SQL; confirmed correct.
  - OK overall.

---

## 4. device_ps4_hourly — PS4 Anomaly  [STATUS: OK, minor]

- **Target table:** `mars_dev.gold.device_ps4_hourly`
- **PS:** PS4 (anomaly; feeds IsolationForest / LOF / AE / SPC)
- **Grain:** `(DEVICE_ID, hour_bucket)` where `hour_bucket = date_trunc('hour', EVENT_DTM)`. **Hourly grain — CORRECT** and ideal for SPC/AE.
- **Source SILVER (all EXIST):** device_event_enriched (S03; has `hour_bucket`, confirmed S03 L159), tap_event_daily (S10), **metric_hourly (S14)**. The design hint ("ideally from a silver metric_hourly table") is **satisfied** — S14 was purpose-built (FIX 15) to remove the prior gold-reads-S3-parquet governance violation. 
- **Hourly source check:** `hourly_events` aggregates S03 to (DEVICE_ID, DEVICE_KEY, category, hour_bucket, transit_day); `metric_hourly` CTE reads `mars_dev.silver.metric_hourly` (DEVICE_KEY, hour_bucket, metric_401_tap_count_hour, metric_401_avg_ms_hour, metric_401_max_ms_hour) — **all 3 metric_401 columns CONFIRMED in S14 final SELECT.** Hour-grain baseline windows `ROWS BETWEEN 28*24 PRECEDING AND 1 PRECEDING` (trailing, excludes current row → no same-row leak). CORRECT.
- **Target / signals:** 3 anomaly signals → ensemble:
  - Signal 1 event_rate_anomaly (hourly events > mean + 2*stddev of same-hour baseline),
  - Signal 2 metric_anomaly (|metric_401_avg_ms - baseline| > 2*stddev),
  - Signal 3 reject_rate_anomaly (tap_reject_rate > 5%, daily),
  - `anomaly_signal_count` + `ensemble_anomaly_flag = (count >= 2)`. Appropriate unsupervised target/labels. CORRECT.
- **Filters:** `mars_device_category IN ('TVM','GATE','VALIDATOR')`, `hour_bucket IS NOT NULL`, `transit_day >= '2024-01-01'`; tap subquery `transit_day <= CURRENT_DATE()`. CORRECT (2024+, future-date guard).
- **Column existence:** S03 hour_bucket/severity/component_subsystem/is_hardware_oos_event confirmed; S10 tap_reject_rate_pct/peak_hour_tap_count confirmed; S14 metric_401_* confirmed; dim_device `bus_device_flag` confirmed (S01 L81) and `BUS_ID` confirmed. **All present.**
- **Leakage risk:** baselines are strictly trailing (`… AND 1 PRECEDING`), signals compare current hour to PAST-only baseline → **no leakage.** OK.
- **ISSUES:**
  - **LOW** — Signal 3 (`tap_reject_rate_daily`) is a **daily** value broadcast onto every hour of that day (tap LEFT JOIN on transit_day, not hour). So the "hourly" anomaly partly mixes a daily feature. Documented ("Tap features (daily grain)") — acceptable, but the modeller should know reject_rate_anomaly is constant within a day.
  - **LOW** — duplicated source-comment block at top (S01/S03/S10 listed twice). Cosmetic.
  - **LOW** — metric/tap coverage is low (metric_401 ≈ 41% of DEVICE_KEYs; tap only bus-capable devices) → COALESCE(...,0) makes Signal 2/3 default to "no anomaly" for uncovered device-hours. Expected, documented.
  - OK overall.

---

## 5. device_ps5_component — PS5 RUL / Survival  [STATUS: MEDIUM]

- **Target table:** `mars_dev.gold.device_ps5_component`
- **PS:** PS5 (RUL / survival; Weibull/Cox/DeepSurv)
- **Grain:** `(DEVICE_ID, COMPONENT_SERIAL_NBR)` — one row per installed component. **Component grain — CORRECT in intent.**
- **Source SILVER (all EXIST):** hw_config_current (S06), device_outage (S04). Plus bronze `ncs_stage_cashbox_tracking`.

### Checklist answer (c) — component-grain time-to-event RUL? YES (with caveat)
- **Component grain:** driven from `all_hw` = `silver.hw_config_current WHERE COMPONENT_SERIAL_NBR IS NOT NULL`, one row per (DEVICE_ID, COMPONENT_SERIAL_NBR). Failures joined from `component_failures` (derived from `silver.device_outage`, grouped by DEVICE_ID + COMPONENT_SERIAL_NBR + COMPONENT_TYPE_NAME). **Component-level. YES.**
- **Time-to-event construction (the survival target):**
  ```
  CASE
    WHEN cf.failure_count > 0 AND hw.REPORTED_CHANGED_DTM IS NOT NULL
    THEN GREATEST(0.0,
         (unix_timestamp(cf.first_failure_dtm)
          - unix_timestamp(CAST(hw.REPORTED_CHANGED_DTM AS TIMESTAMP))) / 86400.0)
    ELSE hw.component_age_days
  END AS days_to_failure,
  (cf.failure_count IS NULL OR cf.failure_count = 0) AS is_censored,
  cf.avg_days_between_failures AS mtbf_days,
  ```
  → `days_to_failure` = days from component install (`REPORTED_CHANGED_DTM`) to **first** failure; `is_censored = TRUE` when no failure found (still running, age = duration). **This is a correct time-to-event + right-censoring survival setup.** `mtbf_days` supplied as a recurrent-event extra. **YES.**
- **READER = reader-component slice:** the header states reader failures are modelled inside parent devices via `COMPONENT_TYPE` events; `COMPONENT_TYPE_NAME` is sourced from `device_outage` (S04, confirmed present) via `component_failures`. Conceptually aligned with the Michael reframe.
- **NO parts data:** correctly acknowledged — `has_work_order_data = FALSE`, `has_maintenance_log = FALSE`, `hw_from_ncs_stage = TRUE`; lifetime derived from `REPORTED_CHANGED_DTM` + OOS outages as failure proxy. Matches design ("no parts data").
- **Column existence:** S06 confirmed to emit DEVICE_ID, DEVICE_KEY, DEVICE_NAME, FACILITY_ID, FACILITY_NAME, OPERATOR_ID, OPERATOR_NAME, device_serial_number, COMPONENT_DESCRIPTION, COMPONENT_SERIAL_NBR, component_age_days, LAST_REPORTED_DTM, REPORTED_CHANGED_DTM, hw_source, mars_device_category. **All present.** S04 confirmed to emit COMPONENT_SERIAL_NBR, COMPONENT_TYPE_NAME, outage_start, duration_min, component_subsystem, mars_device_category. **All present.** The script's FIX-7/8/9 notes (COMPONENT_TYPE_NAME not in S06 → sourced from S04) are CORRECT and match the actual silver schemas.

### ISSUES
- **MEDIUM (real-world validity, not a SQL bug)** — component grain depends on `COMPONENT_SERIAL_NBR`, which silver dim_device's own header flags as **"91.8% NULL confirmed across both EDW and NCS — true data gap … PS5 must operate at device level (DEVICE_KEY), not component level."** `all_hw` filters `COMPONENT_SERIAL_NBR IS NOT NULL`, so the gold table will keep only the ~8% of components that *have* a serial. The script is internally correct, but **the design note says PS5 should fall back to device level**; this builder does the opposite (component-only). Flag for PK: confirm whether the ~8%-serial component population is sufficient, or add a device-level grain. This is the single most important PS5 finding.
- **MEDIUM** — `all_hw` and `component_outages_lead` filter `mars_device_category IN ('TVM','GATE','READER','VALIDATOR')`. **'READER' is a DEAD value**: as of 2026-06-23 silver dim_device no longer emits a 'READER' category (S01: "-READER category removed 2026-06-23; RSV/CSC are COMPONENT_TYPE not DEVICE_TYPE"; categories are TVM/GATE/VALIDATOR/OTHER). So the 'READER' branch matches 0 rows — harmless to output but **inconsistent with PS1/PS2/PS3/PS4** which all correctly use the 3-value list. Recommend dropping 'READER' from the two IN-lists for consistency/clarity. (Also present once in PS3's `hw_best_match` comment but PS3's actual filter is the correct 3-value list.)
- **MEDIUM** — cashbox features read `mars_dev.bronze.ncs_stage_cashbox_tracking` directly (same gold-reads-bronze exception as PS2). Accept-or-stage decision.
- **LOW** — `days_to_failure` uses only the **first** failure vs install; for components with recurrent outages this models time-to-first-event (standard for basic survival). Fine, but note recurrent-event models (e.g. Andersen-Gill) would want the full gap sequence (`avg_days_between_failures` partially covers this).
- **LOW** — final `WHERE hw.component_age_days IS NOT NULL` drops components whose install AND last-reported are both NULL → those can't yield a survival time anyway. Correct.

---

## 6. Cross-cutting checklist (all 5)

| Check | Result |
|-------|--------|
| 3-part UC names `mars_dev.gold.*` | PASS (all 5) |
| Build = DROP + CREATE TABLE (idempotent) | PASS (all 5) |
| GOLD reads SILVER only | PASS for PS1/PS3/PS4; PS2 & PS5 also read governed bronze `ncs_stage_cashbox_tracking` (documented exception — MEDIUM) |
| Join on device_id | PASS (device_id / device_key as appropriate; is_current guards present) |
| City = metadata column, not per-city tables | PASS (no per-city tables; OPERATOR/FACILITY carried as columns) |
| 2024+ training window | PASS on event-driven tables (PS1 spine, PS2 fault_events, PS3 all_incidents, PS4 hourly_events). PS5 is lifetime-based (no 2024 cut — correct for survival, uses full install history) |
| No CREATE INDEX (Delta) | PASS (all removed; OPTIMIZE/ZORDER in comments) |
| READER excluded as device category | PASS in PS1/PS2/PS3/PS4; PS5 leaves a dead 'READER' IN-value (MEDIUM) |
| Source tables exist in silver | PASS — 100% (all 10 referenced silver tables present with matching CREATE) |
| Source columns exist in silver | PASS — every referenced column confirmed in the silver final SELECTs |
| Fan-out controls (is_current, pre-agg, rn=1) | PASS (all 5) |
| Label/target leakage | PS3/PS4 leak-free (strictly trailing/pre-incident); PS2/PS5 unsupervised (n/a); **PS1 = label mismatch + borderline same-day features (HIGH)** |

---

## 7. Recommendations (priority order)

1. **PS1 label [HIGH].** Decide: keep the documented `duration_min > 0` 7-day label (looser, ~20-60% positive) OR rebuild to the prescribed material-failure label `is_hardware_oos AND NOT excluded AND NOT relieved AND failure_level IN (1,2,3,4,5,16)` via the `failure_ledger` keystone (which already encodes relief + levels). The current label inherits hardware-OOS + not-commanded from S04 but is missing NOT-excluded, NOT-relieved, and the failure-level gate. If the design's ~1-2% positive rate is required, the label MUST change.
2. **PS1 same-day features [HIGH].** Confirm intent of same-day `outage_count/total_outage_min/max_outage_min/oos_event_count/hardware_oos_count` alongside a 7-day-ahead label; lag or drop if they are not meant as "current state" inputs.
3. **PS5 grain [MEDIUM].** Reconcile with dim_device's "PS5 must operate at device level (91.8% serial NULL)" note — either accept the ~8%-serial component table or add a device-level grain fallback.
4. **PS5 'READER' dead value [MEDIUM].** Remove 'READER' from the two `mars_device_category IN (...)` lists for consistency with the other 4 scripts.
5. **PS2 & PS5 gold-reads-bronze [MEDIUM].** Ratify the `ncs_stage_cashbox_tracking` exception or stage it into silver to keep "gold reads silver only" strict.
6. **Cosmetic [LOW].** PS3 stale "~122K vs ~66,962" expected-row comments; PS4 duplicated source-comment block.

