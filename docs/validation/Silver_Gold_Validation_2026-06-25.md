# Silver (23) + Gold (6) Validation — 2026-06-25 (full re-run)

**Repo:** SathishMars/Chicago-Ventra-Mars-Cubic-Analysis · **Branch:** `main` @ merge `96a446e` (includes team push `d61a7f9` "S21-S23, R3 device universe")
**Scope:** all `sql/silver/01-23` (23 CREATE scripts) + all `sql/gold/device_ps{1-5}` (5 PS tables + `ps1_label_compare`)
**Method:** Every file re-read in full from the live working tree (no line-count reuse). Validated against the bronze data contract, the 23 deployed silver tables, and the known issue classes (SCD2 `dim_device` fan-out, `device_id` vs `DEVICE_KEY`, label/temporal leakage, the 2032 `TRANSIT_DAY_KEY` sentinel, sparse-source nulls, frozen `cta_kpi_monthly_summary`, Spark-vs-Postgres syntax).

**Drift check: CLEAN** — all table references resolve; **zero references to `failure_ledger`** (labels correctly built from `device_outage` + `dim_failure_level` + `incident_root_cause`). The `DEVICE_KEY → device_id` fix (commit `0fa5ac9`) is correctly applied: metric tables join on `DEVICE_KEY` (1:1), all other dim joins on `device_id` + `is_current = TRUE`.

---

## EXECUTIVE VERDICT: NOT-READY for the SageMaker run

Two gold feature tables won't even parse, two silver scripts have hard SQL errors, and the PS1 label plus several PS3/PS5 feature columns have correctness/leakage problems. None are deep design failures — they're fixable — but they must be cleared first.

**Silver:** 3 PASS · 17 PASS-WITH-NOTES · **3 NEEDS-FIX** (`07_dim_event_type`, `14_tvm_sale_daily`, `22_read_tap_daily`)
**Gold:** 1 PASS-WITH-NOTES (PS2) · 1 PASS-WITH-NOTES (PS5) · **4 NEEDS-FIX** (PS1, PS1-label-compare, PS3, PS4)

---

## BLOCKERS (must fix before the run)

1. **[BLOCKER] `gold/device_ps1_daily__create.sql` is TRUNCATED on disk.** 324 lines / 18,542 bytes; file ends mid-comment `-- TARGET: 1 if device has a real outage within nex` with no newline. The `will_fail_7d` column is never emitted, and there is **no `FROM spine … JOIN …` block and no `;`**. The final SELECT's last line ends in a comma. This is *the* PS1 label table — it cannot run. **Restore the complete file** (the pre-merge `0fa5ac9` version was complete at 320 lines with `will_fail_7d` at L286 + full FROM/JOINs; re-apply the new S21 `use_revenue` columns on top).
2. **[BLOCKER] `gold/device_ps4_hourly__create.sql` is TRUNCATED on disk.** 257 lines / 13,935 bytes; ends mid-token `tap_reje`. The closing `tap` subquery (Signal 3 source), its `ON he.DEVICE_ID=tap.DEVICE_ID AND he.transit_day=tap.transit_day` join, and the `;` are missing; parens unbalanced. Restore the complete file.
3. **[BLOCKER] `silver/07_dim_event_type` — `applies_to_gate` flags CHU events (45,46,52) as gate-applicable** (inner CASE, ~L361). CHU = coin-handling unit; gates have no coin hardware. This pollutes every gate OOS-event feature. Remove that one `WHEN … IN (45,46,52) THEN 'CHU'` from the **gate** block only (it is correctly present in `applies_to_tvm`, correctly absent from `applies_to_bus`).
4. **[BLOCKER] `silver/14_tvm_sale_daily` — `EXTRACT(HOUR FROM st.TRANSACTION_DTM)` (~L58) is not valid Spark SQL.** Use `HOUR(st.TRANSACTION_DTM)`. (Same fix was applied to S13 but missed here.)
5. **[BLOCKER] `silver/22_read_tap_daily` — 2032 sentinel not excluded** (`WHERE TRANSIT_DAY_KEY >= 20240101` has no upper bound; `edw_read_transaction` max = `20321214`). Add `AND rt.TRANSIT_DAY_KEY <= 20261231`. **Also: `TAP_STATUS_ID IN (1,900,904)` approval codes are borrowed from `ABP_TAP` and unverified for `READ_TRANSACTION`** — profile `SELECT TAP_STATUS_ID, COUNT(*) … GROUP BY 1` and confirm the column+codes (or `approved/rejected_read_count` will be silently wrong).

## HIGH (fix before trusting model output)

6. **[HIGH] PS1 label is the wrong target.** `outage_label_days` gates on `duration_min > 0` (any outage), **not** `is_chargeable`/`failure_level IN (1,2,3,4,5,16)`. Per the Week-7 reconciliation the spec target is the ~**1.52%** chargeable-failure rate; this label lands ~20–60% positive. If the chargeable target stands, add `AND is_chargeable = TRUE` (prefer the explicit `failure_level IN (1,2,3,4,5,16)` for strict cat-2 alignment). **This is a decision for you/Michael — confirm intent.**
7. **[HIGH] `gold/device_ps1_daily__label_compare` errors** — reads `AE_FAILURE_LEVEL` from `kpi_avail_enriched`, which exposes `FAILURE_LEVEL` (and `sn_failure_level`), not `AE_FAILURE_LEVEL`. Repoint to `silver.incident_root_cause` (which has `AE_FAILURE_LEVEL`/`is_device_fault`). This is the very diagnostic meant to settle #6.
8. **[HIGH] PS3 ships target-leaking columns in the feature set** — `failure_level_label` (the target as a string), `is_device_fault` (≡ the row-inclusion filter, constant TRUE), `root_cause_category`, `AE_RESOLUTION`, `AE_PROBLEM`. Keep these only as target/decode; train features on the pre-incident `events_24h/7d_prior` aggregates + `component_age_days`. Also PS3 class imbalance is extreme (level 1 ≈ 6 rows) — collapse/merge rare levels.
9. **[HIGH] PS5 leakage + non-reproducible censoring** — survival features `failures_total`/`mtbf_days`/`COMPONENT_TYPE_NAME` are collinear with `is_censored`; and censored `days_to_failure` uses `DATEDIFF(CURRENT_DATE, install)`, so it drifts every run. Pin censoring to a fixed `as_of_date`; exclude post-failure aggregates from X.
10. **[HIGH] `silver/05_metric_hourly` — scalar `(SELECT METRIC_KEY … WHERE METRIC_ID=401 LIMIT 1)`** silently drops data if >1 key exists; replace with a JOIN on `METRIC_ID=401`. Also the `TO_DATE(CAST(TRANSIT_DAY_KEY AS STRING),…)` filter defeats partition pruning on 643M rows — bound on the integer key instead.
11. **[HIGH] `silver/09_hw_config_current` reads only EDW**, not the spec'd `ncs_stage_device_current_hw_config` — confirm intentional or NCS-only devices get NULL component data.
12. **[HIGH] `silver/13_tap_event_daily` has no `REVENUE_OR_TEST` filter** (verify the column exists on `edw_abp_tap`; if so, test taps inflate counts). **`silver/23` `CAST('Y'/'N' AS BOOLEAN)` returns NULL in Spark** — use `CASE WHEN='Y' THEN TRUE WHEN='N' THEN FALSE END` for `is_compound`/`is_persistent`.

---

## Silver — per-table verdicts (23)

| # | table | verdict | top finding |
|---|---|---|---|
| 01 | dim_failure_level | PASS-WITH-NOTES | header "41 levels" vs 31 coded; cat-2 set (1-5,16) correct |
| 02 | dim_stop_point | PASS-WITH-NOTES | no dedup guard on `STOP_POINT_ID`; EDW geo source pending |
| 03 | dim_event_matrix | PASS-WITH-NOTES | 185 rows vs "expect 155" verification — reconcile vs Excel; SET/CLEAR pairing; code 156 |
| 04 | dim_facility | PASS | clean |
| 05 | metric_hourly | PASS-WITH-NOTES | [HIGH] scalar METRIC_KEY subquery → JOIN; [MED] partition-prune defeated; metric stall ~Nov-2025 |
| 06 | dim_device | PASS-WITH-NOTES | [MED] NCS join no dedup → SCD2 fan-out risk; [MED] GATE `LIKE '%GATE%'` over-match; dead AVM/EVM branch. R3 category logic correct |
| **07** | **dim_event_type** | **NEEDS-FIX** | **[BLOCKER] `applies_to_gate` includes CHU (45,46,52)**; [HIGH] silent-null if S03 not built first; code 156 inconsistency |
| 08 | device_uptime_intervals | PASS-WITH-NOTES | `msg_counts` no future-date guard; EOD grain not deduped; uptime cols all NULL |
| 09 | hw_config_current | PASS-WITH-NOTES | [HIGH] NCS hw_config source not joined (EDW-only); multi-row-per-device grain not warned for gold |
| 10 | metric_daily | PASS-WITH-NOTES | [HIGH-ish] DEVICE_KEY join lacks `is_current` (1:1 in practice; defense-in-depth); LAG default inconsistency; no 2024 floor |
| 11 | kpi_avail_enriched | PASS-WITH-NOTES | relief range-join fan-out; jumpbox dedup is device-level not event-level; sn_events no AE_EVENT_ID dedup check |
| 12 | kpi_daily | PASS-WITH-NOTES | [HIGH] EVENTS-grain not daily (name misleads) → 5.37× gold fan-out; mitigated in PS1 via pre-agg CTE — verify every consumer pre-aggregates |
| 13 | tap_event_daily | PASS-WITH-NOTES | [HIGH] no `REVENUE_OR_TEST` filter (verify col); BUS_ID grain multi-row/day; double full-scan; header 2.05B vs ~573M |
| **14** | **tvm_sale_daily** | **NEEDS-FIX** | **[BLOCKER] `EXTRACT(HOUR FROM …)` → `HOUR(…)`**; [MED] no REVENUE filter; NULL `TRANSACTION_STATUS_CD` handling |
| 15 | incident_history | PASS-WITH-NOTES | [HIGH] CMDB display-name joins brittle (add `TRIM`/`UPPER`); 2024 filter hard-coded in silver; `calendar_duration` format assumption; no dedup on `number` |
| 16 | device_event_enriched | PASS-WITH-NOTES | header "1.12B" vs known 190.9M; dedup (ROW_NUMBER on DW_DEVICE_EVENT_ID) correct; DEVICE_KEY passthrough caveat |
| 17 | incident_root_cause | PASS-WITH-NOTES | [MED] NULL-unsafe `is_chargeable`/`is_device_fault` → wrap `COALESCE(…,FALSE)`; CTE misnamed `s17_first` (reads S15); no incident-duration cap |
| 18 | device_outage | PASS-WITH-NOTES | depends on S17 (build order); `AUTOMATIC_CLEAR_FLAG=1` NULL-unsafe; LEAD outage_end partitioned by device only |
| 19 | maintenance_ledger | PASS-WITH-NOTES | [MED] UNION ALL type mismatch (`failure_level`, `source_event_id`) — add explicit CASTs; full repair history intentional |
| 20 | usage_lifecycle_daily | PASS | DEVICE_KEY-partitioned cumulative counters restart on SCD2 change (consider DEVICE_ID); scope VALIDATOR+GATE (~2,794; TVM RUL=0) |
| 21 | use_revenue_daily (NEW) | PASS-WITH-NOTES | correct use of `edw_use_transaction_daily`; [MED] no dedup guard on grain; [LOW] dollar cols break cents-only silver convention |
| **22** | **read_tap_daily (NEW)** | **NEEDS-FIX** | **[BLOCKER] 2032 sentinel not excluded; [BLOCKER] TAP_STATUS_ID codes unverified**; [HIGH] GROUP BY (OPERATOR/FACILITY/BUS) breaks declared grain; not yet consumed by gold |
| 23 | kpi_monthly_benchmark (NEW) | PASS-WITH-NOTES | [HIGH] `CAST('Y'/'N' AS BOOLEAN)`=NULL → use CASE; **correctly self-documented as governance-only (frozen 2015 data, NOT a PS feature)** — verified gold doesn't join it |

---

## Gold — per-table verdicts (6)

| table | verdict | top finding |
|---|---|---|
| **device_ps1_daily** | **NEEDS-FIX** | **[BLOCKER] file truncated on disk** (no FROM/target/`;`); [HIGH] label gates any-outage not chargeable; [MED] same-day outage features collinear with forward label |
| **device_ps1_daily__label_compare** | **NEEDS-FIX** | [HIGH] reads non-existent `AE_FAILURE_LEVEL` on `kpi_avail_enriched` → errors; forward window itself is leak-free |
| device_ps2_chains | PASS-WITH-NOTES | leak-free; [MED] gold-reads-bronze `ncs_stage_cashbox_tracking` (no DDL, unverified cols); verify SCD2 `is_current` uniqueness (no INSERTED_DTM ties) |
| **device_ps3_incident** | **NEEDS-FIX** | [HIGH] target leakage (`failure_level_label`,`is_device_fault`,`root_cause_category`,`AE_RESOLUTION`); [MED] class imbalance (level 1 = 6 rows). Pre-incident windows leak-free |
| **device_ps4_hourly** | **NEEDS-FIX** | **[BLOCKER] file truncated on disk** (tap subquery cut, parens unbalanced); baselines leak-free; [MED] daily signals broadcast across hours |
| device_ps5_component | PASS-WITH-NOTES | [HIGH] non-reproducible censoring (`CURRENT_DATE`); [MED] survival features leak the event; [MED] gold-reads-bronze cashbox unverified; ~8% serial-bearing grain |

---

## What is sound (on record)

- **No `failure_ledger` drift; all references resolve.**
- **`DEVICE_KEY → device_id` fix is correct and complete** across gold — metric tables on `DEVICE_KEY` (1:1), all other dim joins on `device_id` + `is_current=TRUE`.
- **No hard same-row temporal leakage in the (parseable) gold tables** — PS3 pre-incident windows strictly backward (`EVENT_DTM < AE_START_DTM`); PS4 anomaly baselines exclude the current row (`… AND 1 PRECEDING`); PS5 is a proper right-censored survival build; PS1's visible rolling windows are trailing.
- **S21 and S23 are well-engineered** — S21 uses the `edw_use_transaction_daily` aggregate correctly; **S23 independently documents the frozen-2015 `cta_kpi_monthly_summary` and refuses to use it as an ML feature** (matches our independent finding).

## Cross-cutting items

- DDL hygiene: most silver use `DROP TABLE + CREATE TABLE` (non-atomic) vs S09's `CREATE OR REPLACE` — standardize.
- Header row-count discrepancies to reconcile: S13 (2.05B vs ~573M), S16 (1.12B vs 190.9M), S03 (185 vs "155").
- `ncs_stage_cashbox_tracking` (PS2/PS5) has no silver/bronze DDL in the repo and is read directly from bronze (gold-reads-bronze) — promote a silver `cashbox_daily` and verify columns.
- NULL-unsafe boolean expressions (`= 1`, `IN (...)` on nullable cols) recur (S17/S18/S23) — wrap in `COALESCE(…, FALSE)`.

*Validation performed read-only; no SQL files were modified by this review.*
