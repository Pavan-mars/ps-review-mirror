# Chicago-Ventra Silver & Gold — Validation Report

**Repo:** `SathishMars/Chicago-Ventra-Mars-Cubic-Analysis` @ `ef47eaa`
**Scope:** `sql/silver/` S01–S18 (16 CREATE + S17 design + S18 dim) and `sql/gold/` PS1–PS5
**Date:** 2026-06-23 · **Reviewer:** automated validation + manual verification of HIGH items
**Verdict:** **No BLOCKERs. The layer is structurally sound and runnable.** 4 HIGH and 9 MEDIUM items to address before training; one is a design decision (PS1 label) the team should ratify.

---

## 1. Executive summary

| Layer | Files | OK | LOW | MEDIUM | HIGH | BLOCKER |
|---|---|---|---|---|---|---|
| Silver | 18 | 8 | 9 | 6 | 2 | 0 |
| Gold | 5 | — | 4 | 3 | 2 | 0 |

**What's right (verified, not assumed):** all bronze references are fully-prefixed (`edw_`/`ncs_stage_`/`cta_`) and resolve; **every silver table consumed by gold exists**; READER is correctly modeled as a **component**, never a 4th device category; root cause is structured **FAILURE_LEVEL** (S08/S11/S18), and S18 encodes `is_device_fault` for exactly levels `1,2,3,4,5,16`; PS3 target = FAILURE_LEVEL ✓; PS5 is a valid time-to-event survival build; PS2/PS3/PS4 are leak-free; dependency ordering is a valid topological build order; gold reads silver with city as a metadata column and a 2024+ training window.

**The four things to fix before training:**
1. **PS1 label** does not match the material-failure definition (design decision — see 3.1).
2. **S01 `is_current`** is not dedup-guaranteed (robustness — see 2.1).
3. **PS5 grain** (component serial, 91.8% NULL) contradicts the project's own "operate at device level" note + a dead `'READER'` filter value (see 3.3).
4. **S08 relief join** can fan out (see 2.2).

---

## 2. Silver findings (S01–S18)

| # | Table | Status | Note |
|---|---|---|---|
| 01 | dim_device | **HIGH** | `is_current = (CURRENT_FLAG=1)`, not ROW_NUMBER-deduped |
| 02 | dim_event_type | LOW | OOS hardware/commanded split is good |
| 03 | device_event_enriched | OK | spine + dedup + COMPONENT_TYPE carried |
| 04 | device_outage | MEDIUM | LEAD fallback ignores COMPONENT_TYPE |
| 05 | device_uptime_intervals | LOW | — |
| 06 | hw_config_current | OK | — |
| 07 | metric_daily | MEDIUM | no 2024 floor (vs S14 has one) |
| 08 | kpi_avail_enriched | MEDIUM | relief range-join not de-duped (fan-out) |
| 09 | kpi_daily | LOW | fan-out pre-agg handled |
| 10 | tap_event_daily | OK | future-date guard present |
| 11 | incident_root_cause | OK | structured FAILURE_LEVEL |
| 12 | dim_facility | OK | — |
| 13 | tvm_sale_daily | LOW | — |
| 14 | metric_hourly | LOW | 2024 floor present |
| 15 | maintenance_ledger | MEDIUM | redundant `TO_DATE(<date>)` can null out |
| 16 | usage_lifecycle_daily | MEDIUM | PS5 wear off metric_daily → VALIDATOR/GATE only, stalls Nov-2025 |
| 17 | incident_history (design) | LOW | design file; sound; pending Robin's 4 ServiceNow tables |
| 18 | dim_failure_level | OK | 41 levels; `is_device_fault` = levels 1,2,3,4,5,16 |

### 2.1 HIGH — S01 `is_current` is not dedup-guaranteed
`(d.CURRENT_FLAG = 1) AS is_current` (line 163). Because ~8 downstream tables join `AND dd.is_current = TRUE`, any DEVICE_ID with two `CURRENT_FLAG=1` rows fans out across the whole layer. The canonical rebuild (`silver_dim_device_SCD2_v3.sql`) instead uses `ROW_NUMBER() OVER (PARTITION BY DEVICE_ID ORDER BY INSERTED_DTM DESC)=1`, which makes "exactly one current per device" a structural invariant rather than a data assumption. **Also note:** this repo file (189,570 rows) differs from the deployed `mars_dev.silver.dim_device` (188,292 via ROW_NUMBER) — the repo SQL and the live table have drifted and should be reconciled.
**Fix:** switch `is_current` to ROW_NUMBER, or add a post-build assertion `MAX(current_per_device)=1` that fails the build.

### 2.2 MEDIUM — S08 relief join fan-out
`relief_lookup` joins on DEVICE_ID + FAILURE_LEVEL + date-range overlap and is not pre-ranked (the jumpbox CTE was). Overlapping relief windows duplicate the availability row. **Fix:** pre-rank/de-dup relief to one row per (device, level, window) or add an output==input row-count assertion.

### 2.3 Other MEDIUMs
- **S07 vs S14 scope:** `metric_hourly` floors at 2024-01-01; `metric_daily` doesn't — reconcile to the 2024+ convention.
- **S16 usage_lifecycle (PS5 wear):** driven off `metric_daily`, so it only covers VALIDATOR+GATE (~2,794 devices) and freezes at the Nov-2025 metric stall. Drive cumulative wear from `dim_device` instead.
- **S04 outage LEAD fallback** ignores `COMPONENT_TYPE`, so concurrent component faults truncate each other.
- **S15 maintenance_ledger:** `TO_DATE(<already DATE>, 'yyyy-MM-dd')` returns NULL on a DATE input — drop the wrapper.

---

## 3. Gold findings (PS1–PS5)

| File | PS | Grain | Status |
|---|---|---|---|
| device_ps1_daily | PS1 Failure | (device_id, transit_day) | **HIGH** (label) |
| device_ps2_chains | PS2 Cascade | (device_id, transit_day), ≥2 onsets | OK (minor) |
| device_ps3_incident | PS3 Root cause | (device_id, availability_event_id) | **OK** |
| device_ps4_hourly | PS4 Anomaly | (device_id, hour_bucket) | OK (minor) |
| device_ps5_component | PS5 RUL | (device_id, component_serial_nbr) | **MEDIUM** |

### 3.1 HIGH / DESIGN DECISION — PS1 label diverges from the material-failure definition
**Current:** `will_fail_7d = 1` if the device has **any** `device_outage` with `duration_min > 0` in the next 7 days (explode-backward equi-join). Documented as FIX 8 because a *severity* filter (`CRITICAL/WARN`) yielded only 17 positives. Expected positive rate **20–60%**.

**Design definition (Cubic-validated keystone):** material hardware failure = `is_hardware_oos AND NOT (maintenance OR commanded OOS) AND NOT excluded AND NOT relieved AND failure_level IN (1,2,3,4,5,16)` — sourced from the availability/FAILURE_LEVEL keystone (here S08 `kpi_avail_enriched`), not raw `device_outage`.

**Why it matters:** the current label predicts "any non-instant outage," which conflates routine/again-cleared outages with genuine hardware failures — the thing Cubic actually cares about. It also bypasses S08 (which already carries `AE_FAILURE_LEVEL`), so the failure-level taxonomy the team built isn't used for the PS1 target. The "17 positives" that motivated FIX 8 came from a **severity** filter, not the **failure_level IN (1,2,3,4,5,16)** set — the latter is broader and should yield a workable positive count without resorting to "any outage."

**Recommendation:** build the PS1 label from S08 with the failure-level material-failure filter and measure its positive rate; keep `duration>0` only as a fallback/secondary label if the failure-level positive count proves too sparse. Decide explicitly (see open decision A).

> Leakage note: PS1 same-day `outage_count/oos/hardware_oos` features are collinear with a 7-day-ahead label (borderline). Prefer trailing/lagged features (the 7d/30d rolling columns are fine); drop or lag the same-day outage columns. PS3/PS4 are strictly trailing → leak-free.

### 3.2 PS3 — correct
Target = `AE_FAILURE_LEVEL` filtered to `IN (1,2,3,4,5,16)`, exposed as `failure_level` (+ label + root_cause_category); free-text fields are secondary features only. Matches the design.

### 3.3 PS5 — grain + dead filter
- **Grain:** `(device_id, COMPONENT_SERIAL_NBR)` while `DEVICE_SERIAL_NUMBER` is **91.8% NULL** and S01's own header says *"PS5 must operate at device level (DEVICE_KEY), not component level."* The builder keeps only the ~8% serial-bearing rows — most devices get no RUL. **Fix:** operate at device level (or component-TYPE level via `device_event_enriched.COMPONENT_TYPE`), reserving serial-level only where present.
- **Dead value:** `mars_device_category IN ('TVM','GATE','READER','VALIDATOR')` — silver no longer emits `'READER'` (removed 2026-06-23), so that value matches 0 rows. Align to the 3-value list the other 4 scripts use.
- **gold-reads-bronze:** PS2 and PS5 read `mars_dev.bronze.ncs_stage_cashbox_tracking` directly — a documented exception to "gold reads silver only." Ratify or stage it into silver.

---

## 4. Prioritized recommendations

1. **Ratify the PS1 label** (decision A) — failure-level material-failure vs broad outage. Highest business impact.
2. **Harden S01 `is_current`** to ROW_NUMBER + a one-current-per-device assertion; reconcile repo SQL with the deployed 188,292-row table.
3. **De-dup the S08 relief join**; add output==input row-count assertions on the keystones (S03/S08).
4. **Fix PS5 grain** to device level + remove the dead `'READER'` value.
5. **Reconcile metric scope** (S07 2024 floor) and **S16 wear coverage** (drive off dim_device).
6. **Lag PS1 same-day outage features** to remove the borderline leak.
7. Minor: S15 `TO_DATE` wrapper; S04 component-aware outage close; ratify the two gold-reads-bronze reads.

Per-file detail: `silver_findings.md`, `gold_findings.md` (same folder).
