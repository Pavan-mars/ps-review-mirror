# CUBIC MARS — Silver Layer Validation Report

**Scope:** 18 finalized Databricks (Spark SQL / Unity Catalog) CREATE scripts for the Chicago-Ventra predictive-maintenance medallion Silver layer.
**Location:** `development_project/mars_new_repo/sql/silver/`
**Date:** 2026-06-23
**Catalog/schema:** `mars_dev.silver.*` reading from `mars_dev.bronze.*`
**Validated against:** the design checklist (SCD2 dim_device, mars_device_category taxonomy, fully-prefixed bronze names, FAILURE_LEVEL root cause, availability keystone, READER-as-component, idempotent CREATE OR REPLACE).

---

## A. Executive summary

The Silver build is in good shape. **All bronze source references are correctly fully-prefixed** (`edw_*`, `ncs_stage_*`, `cta_*`) and plausible against the documented bronze inventory. **Runnable files contain zero S3/parquet leaks** — every read goes through governed bronze Delta tables (the prior parquet-path violations are fixed, with the bug logs documenting each). The Cubic data answers are correctly encoded: **READER is modeled as a COMPONENT slice (not a 4th device category)**, **root cause is the structured FAILURE_LEVEL** (S11, S18), and **maintenance history = availability events + maintenance-mode/tech-login device events with no parts/MAXIMO** (S15). Cross-file dependency ordering is sound and all 10 silver tables consumed by gold exist.

**One material finding dominates:** S01 `dim_device` derives `is_current` directly from the source `CURRENT_FLAG = 1` and does **not** apply the ROW_NUMBER-based one-current-row-per-device dedup the design mandates. Because ~13 downstream tables fan-in on `dd.is_current = TRUE` and trust it to be unique per `DEVICE_ID`, any source row with duplicate `CURRENT_FLAG=1` silently multiplies event/fact rows. This is the single highest-priority item. (Note: the canonical builder named in project memory — `silver_dim_device_SCD2_v3.sql` — uses ROW_NUMBER and asserts "one-current-per-device"; this repo's `01_dim_device__create.sql` diverges from that method.)

### Severity counts
| Severity | Count |
|---|---|
| BLOCKER | 0 |
| HIGH | 2 |
| MEDIUM | 6 |
| LOW | 9 |
| OK (no material issue) | 8 files clean |

### File status table
| # | File | Target table | Status |
|---|------|--------------|--------|
| 01 | 01_dim_device__create.sql | `mars_dev.silver.dim_device` | **HIGH** |
| 02 | 02_dim_event_type__create.sql | `mars_dev.silver.dim_event_type` | LOW |
| 03 | 03_device_event_enriched__create.sql | `mars_dev.silver.device_event_enriched` | OK |
| 04 | 04_device_outage__create.sql | `mars_dev.silver.device_outage` | MEDIUM |
| 05 | 05_device_uptime_intervals__create.sql | `mars_dev.silver.device_uptime_intervals` | LOW |
| 06 | 06_hw_config_current__create.sql | `mars_dev.silver.hw_config_current` | OK |
| 07 | 07_metric_daily__create.sql | `mars_dev.silver.metric_daily` | MEDIUM |
| 08 | 08_kpi_avail_enriched__create.sql | `mars_dev.silver.kpi_avail_enriched` | MEDIUM |
| 09 | 09_kpi_daily__create.sql | `mars_dev.silver.kpi_daily` | LOW |
| 10 | 10_tap_event_daily__create.sql | `mars_dev.silver.tap_event_daily` | OK |
| 11 | 11_incident_root_cause__create.sql | `mars_dev.silver.incident_root_cause` | OK |
| 12 | 12_dim_facility__create.sql | `mars_dev.silver.dim_facility` | OK |
| 13 | 13_tvm_sale_daily__create.sql | `mars_dev.silver.tvm_sale_daily` | LOW |
| 14 | 14_metric_hourly__create.sql | `mars_dev.silver.metric_hourly` | LOW |
| 15 | 15_maintenance_ledger__create.sql | `mars_dev.silver.maintenance_ledger` | MEDIUM |
| 16 | 16_usage_lifecycle_daily__create.sql | `mars_dev.silver.usage_lifecycle_daily` | MEDIUM |
| 17 | 17_incident_history__design.sql | `mars_dev.silver.incident_history` | LOW (design) |
| 18 | 18_dim_failure_level__create.sql | `mars_dev.silver.dim_failure_level` | OK |

---

## B. Per-file findings

### S01 — dim_device  *(STATUS: HIGH)*
- **Target:** `mars_dev.silver.dim_device`
- **Sources:** `bronze.edw_device_dimension` (primary), LEFT JOIN `bronze.ncs_stage_device`, LEFT JOIN `bronze.ncs_stage_device_type`. All fully-prefixed and plausible.
- **Join keys:** `nd.DEVICE_ID = d.DEVICE_ID`; `ndt.DEVICE_TYPE_ID = nd.DEVICE_TYPE_ID`.
- **Filter:** `WHERE d.OPERATOR_ID > 0` (sentinel exclusion). The `CURRENT_FLAG=1` filter was intentionally removed to retain full SCD2 history.
- **Grain:** one row per device-version (SCD2 history retained, ~189,570 rows; ~16,375 current).
- **SCD2 logic:** `effective_from = INSERTED_DTM::date`; `effective_to = LEAD(INSERTED_DTM) OVER (PARTITION BY DEVICE_ID ORDER BY INSERTED_DTM)` — correct SCD2 LEAD convention, NULL for the latest row. BUT `is_current = (d.CURRENT_FLAG = 1)` — derived from the raw source flag, with no ROW_NUMBER dedup.
- **mars_device_category:** TVM / GATE / VALIDATOR / OTHER + READER explicitly removed (RSV/CSC/SCR routed to OTHER as sub-components). The READER-as-component reframe is correctly honored.

**ISSUES:**
- **HIGH — `is_current` is not ROW_NUMBER-deduped.** The design checklist requires SCD2 `is_current` built "via ROW_NUMBER-based is_current (exactly ONE current row per device)." This script sets `is_current = (CURRENT_FLAG = 1)` straight from source. If the source `DEVICE_DIMENSION` has any `DEVICE_ID` with more than one `CURRENT_FLAG=1` row (a known SCD2 data-quality hazard), there will be multiple current rows, and every downstream `LEFT JOIN dim_device ... AND is_current=TRUE` (S03, S05, S06, S08, S09, S10, S11, S13, S15 via S03) will fan out. Recommend replacing with `ROW_NUMBER() OVER (PARTITION BY DEVICE_ID ORDER BY INSERTED_DTM DESC) = 1` (matching the canonical `silver_dim_device_SCD2_v3.sql`), or add a post-build assertion that current-row count == distinct in-scope DEVICE_ID count.
- **LOW — category label is `OTHER`, design vocabulary is `EXCLUDED`.** Functionally equivalent (back-office/retail/NULL bucket), but the design context names the 4th bucket `EXCLUDED`. Header comments use both "OTHER" and "EXCLUDE" interchangeably. Align the literal to whatever Gold's `WHERE mars_device_category IN (...)` filters expect.
- **LOW — post-build comment expects 5 categories** (`COUNT(DISTINCT mars_device_category) -- Expect 5`) but only 4 distinct values can be produced now that the READER branch is removed (TVM/GATE/VALIDATOR/OTHER). Stale verification expectation; update to 4.
- **LOW — NCS join not guarded for fan-out.** `ncs_stage_device` is assumed ~1 row per `DEVICE_ID`. If NCS has duplicate device IDs, the LEFT JOIN multiplies EDW rows. Worth a dry-run distinct-count check.

---

### S02 — dim_event_type  *(STATUS: LOW)*
- **Target:** `mars_dev.silver.dim_event_type`
- **Sources:** `bronze.edw_event_type_dimension` (441 rows), LEFT JOIN `bronze.ncs_stage_event` on `ne.EVENT_ID = et.EVENT_TYPE_ID`. Prefixed/plausible.
- **Grain:** one row per event type (441). LEFT JOIN to NCS is 1:1 (97.7% match). No fan-out risk.
- **Derived flags:** `component_subsystem` (full ID-range map, all 441 covered), `severity_label`, `is_oos_event` (60+ explicit codes from Cubic doc 9604-60007 + name fallback), `is_hardware_oos_event` (commanded/maintenance codes 106/110/151/208/519/1603/1604 removed — the correct PS1 label set), `is_commanded_oos_event`, and `applies_to_gate/bus/tvm`. This three-way OOS split is well-designed and directly supports the failure-label discipline.
- **Root-cause alignment:** correctly anchors failure semantics in structured event codes, not free text.

**ISSUES:**
- **LOW — `applies_to_*` flags re-implement the `component_subsystem` CASE inline three times** rather than referencing the already-computed column. Verbose and a maintenance hazard (the three copies can drift). Header already acknowledges a lookup table is the better long-term design. No correctness defect.
- **LOW — documented open confirmations** (code 2011 vs 2211 DOPP typo; SEVERITY=10 MARTA cross-property codes 10106/10110) are noted as pending Cubic confirmation. Acceptable as TODOs; they do not affect Chicago labels.

---

### S03 — device_event_enriched  *(STATUS: OK)*
- **Target:** `mars_dev.silver.device_event_enriched` (the device_event spine).
- **Sources:** `bronze.edw_device_event` (~1.12B), LEFT JOIN `silver.dim_device` (on `DEVICE_ID AND is_current=TRUE`), LEFT JOIN `silver.dim_event_type` (on `EVENT_TYPE_KEY`).
- **De-dup:** `ROW_NUMBER() PARTITION BY DW_DEVICE_EVENT_ID ORDER BY EDW_UPDATED_DTM DESC NULLS LAST`, keep `rn=1`. Correct.
- **Grain:** one row per unique `DW_DEVICE_EVENT_ID`.
- **COMPONENT_TYPE columns:** carries `COMPONENT_TYPE_ID`, `COMPONENT_TYPE_NAME`, `COMPONENT_SERIAL_NBR`, `COMPONENT_POSITION` — satisfies the requirement that this table carry component_type for the reader-component grain.
- **Derived:** `transit_day`, `hour_of_day`, `day_of_week` (0=Sun via `DAYOFWEEK-1`), `duration_to_clear_min` (with `CLEAR_DTM >= EVENT_DTM` guard + 7-day cap), and propagated `is_oos/is_hardware_oos/is_commanded_oos`. Spark-syntax conversions are correct; NULL-safe COALESCEs present.
- **Dependency:** correctly depends on S01, S02 being built first (clearly documented).

**ISSUES:** None material. Depends on the S01 `is_current` uniqueness being fixed (inherits the S01 HIGH risk — the `is_current=TRUE` guard is correct given a unique current row).

---

### S04 — device_outage  *(STATUS: MEDIUM)*
- **Target:** `mars_dev.silver.device_outage`
- **Source:** `silver.device_event_enriched` filtered to `is_hardware_oos_event = TRUE` (hardware-only — correctly excludes commanded/maintenance; avoids the ~84x over-count vs AVAILABILITY_EVENTS).
- **Grain:** one row per hardware-OOS event.
- **Logic:** `outage_end = COALESCE(CLEAR_DTM, LEAD(outage_start) OVER (PARTITION BY DEVICE_ID ORDER BY outage_start))`; `duration_min` capped at 10,080 with `outage_end >= outage_start` guard; resolution flags present.

**ISSUES:**
- **MEDIUM — overlapping-outage / LEAD fallback can mis-measure concurrent component failures.** When `CLEAR_DTM` is NULL, `outage_end` falls to the next OOS event's start on the same device regardless of component. A device with two distinct component faults open simultaneously will have one outage's end set to the other's start, understating duration and potentially producing overlapping intervals. Acceptable as a v1 heuristic (header says "all events in sample had CLEAR_DTM") but should be noted for PS5/PS1 downtime features; consider partitioning the LEAD by `COMPONENT_TYPE_ID` or merging overlapping intervals.
- **LOW — `DEVICE_KEY` is carried from S03** (the event's historical SCD2 key), while `mars_device_category` etc. come from the current dim row. Mixing current attributes with the historical key on the same row is fine for features but is a subtle provenance inconsistency worth a comment.

---

### S05 — device_uptime_intervals  *(STATUS: LOW)*
- **Target:** `mars_dev.silver.device_uptime_intervals`
- **Sources:** `bronze.edw_device_last_state`, `bronze.ncs_stage_device_end_of_day`, `bronze.ncs_stage_device_end_of_day_msg_count`, joined to `silver.dim_device`. All prefixed/plausible.
- **Join keys:** EOD driven; `dim_device ON DEVICE_ID AND is_current=TRUE`; `last_state ON DEVICE_KEY`; `msg_counts ON DEVICE_ID + TRANSIT_DAY_KEY`.
- **Grain:** (device, EOD day).
- **Filters:** `TRANSIT_DAY_KEY <= CURRENT_DATE()` future-date guard (good — source has rows to 2034); `is_silent_device` = >24h since heartbeat.

**ISSUES:**
- **LOW — uptime columns are hardcoded NULL.** `UPTIME_SECONDS/DOWNTIME_SECONDS/TOTAL_SECONDS/uptime_pct/uptime_hours/downtime_hours` are all `CAST(NULL AS ...)` because those columns are absent in bronze (documented 2026-06-18). The table's headline "uptime" metrics are therefore unpopulated; only `COMPLETE_FLAG` + message counts act as the operational proxy. Correct given the data, but the table name oversells what it delivers — flag for downstream consumers so they don't expect uptime_pct.
- **LOW — `msg_counts` join on `DEVICE_ID + TRANSIT_DAY_KEY` is pre-aggregated (GROUP BY)**, so no fan-out. Fine.

---

### S06 — hw_config_current  *(STATUS: OK)*
- **Target:** `mars_dev.silver.hw_config_current` (SCD1 current snapshot).
- **Source:** `silver.dim_device` (driving, `is_current=TRUE`) LEFT JOIN `bronze.edw_device_current_hw_config` on `DEVICE_ID`. The LEFT-JOIN-from-dim_device fix (so devices without HW records still appear, with NULL component cols) is correct for PS5 coverage.
- **Grain:** one row per (device, component) — multiple rows per device expected (documented).
- **Build pattern:** uses `CREATE OR REPLACE TABLE ... USING DELTA PARTITIONED BY (city_id)` — matches the design's idempotent pattern; `'CHICAGO'` literal partition.
- **Derived:** `component_age_days` (REPORTED_CHANGED else LAST_REPORTED), `hw_source` NULL when no record. Spark casts correct.

**ISSUES:** None material. `OPERATOR_ID/FACILITY_ID` correctly sourced from dim_device (not the 6-col bronze HW table). Clean.

---

### S07 — metric_daily  *(STATUS: MEDIUM)*
- **Target:** `mars_dev.silver.metric_daily`
- **Sources:** `bronze.edw_device_metric` (592M), scalar subquery to `bronze.edw_metric_dimension` for `METRIC_ID=401`.
- **Join key:** `dim_device ON DEVICE_KEY` (NOT DEVICE_ID + is_current) — this is correct and intentional: DEVICE_METRIC carries only `DEVICE_KEY`, no DEVICE_ID. Documented 99.88% match.
- **Grain:** (DEVICE_KEY, transit_day). Aggregates count/avg/max of transaction-time; LAG deltas + `volume_drop_flag`.
- **Filter:** `TRANSIT_DAY_KEY <= CURRENT_DATE()` future-date guard.

**ISSUES:**
- **MEDIUM — `dim_device ON DEVICE_KEY` without `is_current` can multiply rows if a DEVICE_KEY is non-unique in dim_device.** In a proper SCD2, `DEVICE_KEY` is the surrogate and should be unique per version, so a key-join returns exactly one (historical) dim row — generally safe and the right choice here. However it is the one place the `is_current` guard is deliberately absent, so it is entirely dependent on `DEVICE_KEY` uniqueness in S01. If S01's row set ever contains duplicate `DEVICE_KEY` values, this fans out. Pairs with the S01 HIGH finding; add a uniqueness check on `dim_device.DEVICE_KEY`.
- **MEDIUM — known data gap (not a code defect): `metric_daily` stalls at 2025-11-07** (documented; bronze ingestion stalled / watermark issue). Downstream PS4 Signal-2 and S16 lifecycle features inherit this gap. Tracked as the `EDW_INSERTED_DTM` watermark item; called out so report consumers know S07/S14/S16 coverage ends Nov-2025.
- **LOW — no 2024+ floor filter.** S07 keeps all history `<= CURRENT_DATE()`; S14 (its hourly sibling) applies `>= '2024-01-01'`. Inconsistent scoping between the two metric tables (see cross-file note CF-3).

---

### S08 — kpi_avail_enriched  *(STATUS: MEDIUM — availability keystone)*
- **Target:** `mars_dev.silver.kpi_avail_enriched`
- **Sources:** `bronze.edw_availability_events` (752,510) + `bronze.edw_availability_relief` + `bronze.cta_servicenow_availability_events` + `bronze.cta_servicenow_data_from_jumpbox`, enriched with `silver.dim_device`. This is exactly the documented availability keystone composition (avail_events + relief + ServiceNow + dim).
- **Join keys:** `dim_device ON DEVICE_ID AND is_current=TRUE`; relief on `DEVICE_ID + FAILURE_LEVEL + date-range overlap`; SN on `AE_EVENT_ID = EVENT_ID`; jumpbox on `DEVICE_ID` (deduped to most-recent via ROW_NUMBER, fixing a confirmed 208-row fan-out).
- **Grain:** intended one row per availability event.
- **Date handling:** mixed-format `TRANSIT_DAY_KEY` (6-digit YYMMDD vs 8-digit YYYYMMDD) handled by a length-branch CASE — correct and a genuine data-shape catch. Negative-duration guard present.
- **Root cause = FAILURE_LEVEL:** carried as `FAILURE_LEVEL` and `relief_failure_level`; ServiceNow free-text retained only as supplemental NLP fields, not as the cause of record.

**ISSUES:**
- **MEDIUM — relief range-join can fan out the keystone.** `relief_lookup` joins on `DEVICE_ID = DEVICE_ID AND FAILURE_LEVEL = FAILURE_LEVEL AND relief_start <= START_DTM AND (relief_end IS NULL OR relief_end >= START_DTM)`. If a device has two or more relief windows at the same FAILURE_LEVEL that both overlap an event's `START_DTM`, the availability event row is duplicated. Relief is small (2,117 rows) so impact is likely tiny, but unlike the jumpbox CTE this one is NOT de-duplicated. Recommend a post-build assertion `output_rows == source avail_events rows` (the header documents that exact check was used to catch the jumpbox fan-out — extend it to confirm relief does not regress) or pre-rank relief to one row per (device, failure_level, event).
- **LOW — SN `AE_EVENT_ID = EVENT_ID` match rate is flagged "verify in dry-run"** (WOT# vs AE EVENT_ID format). Non-blocking; LEFT JOIN, so non-matches null-fill.

---

### S09 — kpi_daily  *(STATUS: LOW)*
- **Target:** `mars_dev.silver.kpi_daily`
- **Sources:** `bronze.edw_kpi_detail_events_by_day` + `edw_kpi` + `edw_kpi_rules` + `edw_kpi_target` + `edw_kpi_summary_by_day` + `cta_kpi_agency_map` + `cta_sldc_monthly_summary`. All prefixed/plausible; `cta_kpi_tvm_date_table` correctly removed (PATH_NOT_FOUND).
- **Fan-out discipline (good):** every dimension that can have >1 row per key is pre-aggregated — `target_bands` GROUP BY (KPI_ID, KPI_BAND); `kpi_rules_agg` GROUP BY KPI_ID; `agency_map` GROUP BY KPI_ID; `sldc_monthly` GROUP BY (KPI_ID, month). Range join on target band is non-overlapping. `dim_device ON DEVICE_ID AND is_current=TRUE`.
- **Grain (important caveat, well-documented):** the source `KPI_DETAIL_EVENTS_BY_DAY` is an events table (~1.74 events per device-KPI-day), so kpi_daily is NOT 1 row per device-KPI-day — the header explicitly warns Gold G01/PS1 must pre-aggregate before joining to avoid 5.37x fan-out. This is a correctly-surfaced design constraint, not a defect.

**ISSUES:**
- **LOW — grain is event-level, not daily, despite the table name `kpi_daily`.** The naming invites a downstream developer to join it as a daily dimension and self-inflict fan-out. The warning is in the header; consider also encoding it in the table comment / a Gold-side guard. No SQL correctness issue.

---

### S10 — tap_event_daily  *(STATUS: OK)*
- **Target:** `mars_dev.silver.tap_event_daily`
- **Source:** `bronze.edw_abp_tap` (~2.05B). `cta_abp_use_tran_timing_data` correctly removed (PATH_NOT_FOUND; timing metrics dropped).
- **Join key:** `dim_device ON DEVICE_ID AND is_current=TRUE` (100% match documented).
- **Grain:** (DEVICE_ID, transit_day, OPERATOR_ID, BUS_ID). `transit_day = DATE(TRANSACTION_DTM)` — correctly uses the transaction timestamp, not the EDW load-date partition columns.
- **Logic:** approved = `TAP_STATUS_ID IN (1,900,904)`, rejected = complement; reject-rate; `peak_hour_tap_count` via a separate hourly subquery CTE (correctly replaces the unsupported LATERAL). Column renames (TOKEN_ID, TAP_STATUS_ID, FARE_DUE, TRANSACTION_DTM, BUS_ID) all validated.

**ISSUES:** None material. The `peak_hour_agg` CTE re-scans the 2.05B-row ABP_TAP a second time — a performance note (two full aggregations of the largest fact), not a correctness issue; could be folded into one pass with a grouping set if scan cost matters.

---

### S11 — incident_root_cause  *(STATUS: OK — PS3 source)*
- **Target:** `mars_dev.silver.incident_root_cause`
- **Sources:** `bronze.cta_servicenow_availability_events` (375,578; primary — the documented SVN_STAGE-is-empty workaround) + `bronze.cta_servicenow_data_from_jumpbox` (604). Correctly uses the CTA ServiceNow mirror.
- **Join keys:** `dim_device ON AE_DEVICE_ID AND is_current=TRUE`; jumpbox on `U_EVENT_ID = SN_U_EVENT_ID` (WOT# 1:1 — fixes a prior DEVICE_ID-join fan-out).
- **Grain:** one row per availability event.
- **Root cause = structured FAILURE_LEVEL (correct):** `failure_level_label` maps the Ventra taxonomy (1/2/3/4/5/16 = device faults; 0/6/98/99 operational; back-office = other), and `is_device_fault = AE_FAILURE_LEVEL IN (1,2,3,4,5,16)` — exactly the design's device-fault label set. The free-text `root_cause_category` CASE is retained as a supplemental feature, not as the cause of record. Component type is carried via `SN_U_AFFECTED_COMPONENT`.

**ISSUES:** None material. Jumpbox is a 2026 extract (0% match on 2025 filters) — documented, acceptable.

---

### S12 — dim_facility  *(STATUS: OK)*
- **Target:** `mars_dev.silver.dim_facility`
- **Source:** `bronze.ncs_stage_transit_facility` (1,986; Ventra retail/reload partner locations — explicitly NOT CTA stations, which live in dim_device).
- **Grain:** one row per FACID (1,986 distinct; 0 dups documented).
- **Schema handling:** absent columns (LATITUDE/LONGITUDE/TRANSIT_MODE_ID) set to typed NULL; `STATE` cast INT (FIPS); column renames (ROAD_NAME->address, POSTAL_CODE->zip, TRANSIT_FACILITY_TYPE_ID) validated against probe.

**ISSUES:** None material. Header is candid that address/city/state/zip are 100% NULL in source — lookup-only dimension; consumers should not expect geospatial features. Not referenced by gold (standalone reference table) — acceptable.

---

### S13 — tvm_sale_daily  *(STATUS: LOW)*
- **Target:** `mars_dev.silver.tvm_sale_daily`
- **Source:** `bronze.ncs_stage_sale_transaction`.
- **Join key:** `dim_device ON DEVICE_ID AND is_current=TRUE`.
- **Grain:** (DEVICE_ID, transit_day, OPERATOR_ID, FACID). Volume/error-rate/cash-pct/revenue; status `<> 0` = error. Cents documented.

**ISSUES:**
- **LOW — table mixes TVM + VALIDATOR rows** (VALIDATOR 13.38%, plus 4 NULL-category sentinel devices at ~4,626 sales/day). Header correctly instructs downstream `gold.tvm_ps1_daily` to filter `WHERE mars_device_category = 'TVM'`. Correct, but the filter is delegated to Gold — if a Gold table forgets it, sentinel/validator rows leak. Consider applying the category filter (or at least excluding NULL-category sentinels) in Silver.

---

### S14 — metric_hourly  *(STATUS: LOW)*
- **Target:** `mars_dev.silver.metric_hourly`
- **Source:** `bronze.edw_device_metric` + `edw_metric_dimension` (METRIC_ID=401). Created specifically to fix a gold PS4 governance violation (gold was reading S3 parquet directly) — now reads governed bronze. Good governance fix.
- **Grain:** (DEVICE_KEY, hour_bucket). `TIME_INCREMENT_KEY` integer->`LPAD` to HHMM->hour extraction. Future-date guard + `>= '2024-01-01'` floor.

**ISSUES:**
- **LOW — `TIME_INCREMENT_KEY` parsing assumes HHMM.** `LEFT(LPAD(key,4,'0'),2)` takes the first 2 of a 4-char zero-padded value as the hour. Correct only if `TIME_INCREMENT_KEY` is genuinely HHMM (e.g. 900->09:00). If it is ever a different encoding (e.g. minutes-of-day or a dimension key), the hour bucket is wrong. Documented as the working assumption; worth a one-time validation that values fall in 0–2359.
- **LOW — this table does not join dim_device at all** — it's a pure aggregate keyed by DEVICE_KEY; gold joins dim later. Fine.

---

### S15 — maintenance_ledger  *(STATUS: MEDIUM — PS5 RUL source)*
- **Target:** `mars_dev.silver.maintenance_ledger`
- **Sources (UNION ALL):** A = `bronze.edw_availability_events` (repair episodes, ~645K) LEFT JOIN `silver.dim_device`; B = `silver.device_event_enriched` filtered `is_commanded_oos_event = TRUE` (tech-login/maintenance-mode events). This is exactly the documented PS5 maintenance source = availability episodes + maintenance-mode/tech-login device events, no parts/MAXIMO.
- **Grain:** one row per maintenance/repair event; `ledger_type` in {REPAIR_EPISODE, TECH_LOGIN, MAINTENANCE_MODE, COMMANDED_OOS}. Mixed `TRANSIT_DAY_KEY` format handled. Source A `failure_level` populated, component NULL; Source B component populated, failure_level NULL — clean union schema.

**ISSUES:**
- **MEDIUM — `TO_DATE(<already a DATE>, 'yyyy-MM-dd')` double-conversion in Source A `ledger_date`.** The inner CASE already returns a DATE (from `TO_DATE(..., 'yyMMdd'/'yyyyMMdd')`), then it is wrapped in `TO_DATE(<date>, 'yyyy-MM-dd')`. In Spark, `TO_DATE(date_expr, fmt)` ignores/mis-applies the format on a DATE input; this is at best a redundant no-op and at worst returns NULL on some engines/configs. Recommend dropping the outer `TO_DATE(...)` and using the CASE result directly (as S08 does). Low blast radius but should be cleaned.
- **LOW — Source B `event_end_dtm = CLEAR_DTM` / `duration_min = duration_to_clear_min`** are inherited from S03's 7-day cap; maintenance windows >7 days would be NULLed. Acceptable, but maintenance-mode can legitimately exceed 7 days — note for PS5.
- **LOW — Source A repair episodes are NOT filtered to 2024+** (full history). Consistent with the keystone but means PS5 sees pre-2024 episodes; confirm that matches the RUL training window decision.

---

### S16 — usage_lifecycle_daily  *(STATUS: MEDIUM — PS5 RUL features)*
- **Target:** `mars_dev.silver.usage_lifecycle_daily`
- **Sources (all silver):** `metric_daily` (S07, driving/base) LEFT JOIN `device_outage` (S04, daily agg) LEFT JOIN `maintenance_ledger` (S15, DEVICE_EVENT rows only). Build order S01->S02->S03->S04->S07->S15->S16 documented.
- **Grain:** (DEVICE_KEY, transit_day). Cumulative window features (days_in_service, cumulative tap/failure/outage/maint via `ROWS UNBOUNDED PRECEDING`), recency via `LAST_VALUE(... IGNORE NULLS)`, rolling 30-day sums. Survival-feature design is sound for Weibull/Cox/DeepSurv.

**ISSUES:**
- **MEDIUM — coverage is bounded by S07's stalled window (2024-01-01 -> 2025-11-07) AND limited to VALIDATOR+GATE devices only** (the base is `metric_daily`, ~2,794 device-keys; TVMs and devices with no METRIC_401 data get zero rows). The header documents both, and notes the fix ("If all devices are needed, drive from dim_device and LEFT JOIN metric_daily"). For a PS5 RUL feature table this is a real scope limit — TVM RUL would have no lifecycle features from this table. Confirm PS5 device scope; consider the dim_device-driven variant.
- **MEDIUM — cumulative usage is undercounted by the metric stall.** Because `cumulative_tap_count` and `days_in_service` are built only from days present in `metric_daily`, the Nov-2025 ingestion gap silently freezes lifetime counters for any device past that date — a wear-feature that drifts from reality the longer the stall persists. Tie to the watermark remediation; flag so PS5 doesn't train on artificially flat wear curves.
- **LOW — `days_since_last_failure`/`days_since_last_maintenance` are NULL before the first event** (documented, expected for right-censored survival data).

---

### S17 — incident_history  *(STATUS: LOW — DESIGN FILE, validate design not executability)*
- **Target (design):** `mars_dev.silver.incident_history` (`CREATE OR REPLACE TABLE ... PARTITIONED BY (city_id)`).
- **Status:** explicitly DESIGN ONLY / PENDING the bronze load of 4 ServiceNow tables (INCIDENT, CMDB_CI, CMDB_MODEL, CMDB_MODEL_CATEGORY) by Robin. Header instructs using S11 `incident_root_cause` (V1) until then. This is the correct interim posture.
- **Design soundness (GOOD):** sources structured ServiceNow incident + CMDB lineage; joins INCIDENT->CMDB_CI->CMDB_MODEL->CMDB_MODEL_CATEGORY then to `silver.dim_device` on `wm_asset = DEVICE_ID` (the WM-Asset = TVM12102/SAG11501 format matches dim_device, no CTA\d{5}). `is_current=TRUE` guard present. Carries cause_category/subcategory + component context (the PS3 +25% enrichment) and per-CI history (PS5 +20%). Failure/priority parsed from "NNN - Name" strings. Conceptually consistent with the FAILURE_LEVEL/structured-cause direction and with S11.

**ISSUES (design-level only; not executability):**
- **LOW (design) — reads bronze via `parquet.`s3://...`` paths, not governed bronze tables.** Every other runnable Silver file was migrated off parquet paths onto `mars_dev.bronze.*`. When these 4 tables land, this DDL should read `mars_dev.bronze.sn_incident` / `cmdb_*` (governed), not raw S3 — otherwise it reintroduces exactly the gold-style governance violation S14 was created to fix. Call out for the eventual build.
- **LOW (design) — mixed string-split dialects.** Uses both Spark `SPLIT(priority,' - ')[0]` (correct) AND `SPLIT_PART(u_event_code, ' - ', 1)` (Postgres-style; the file's own header elsewhere flags SPLIT_PART as Postgres). The two event-code branches must be reconciled to one Spark function (`split(...)[n]` or Databricks `split_part`, which is 1-indexed) before this becomes runnable. Acceptable now because the file is DESIGN and says "CONFIRM EXACT NAMES against actual bronze schema once loaded."
- **LOW (design) — column names are SN-API guesses** (`u_*`), self-documented as needing confirmation against the real schema. Expected for a design file.

---

### S18 — dim_failure_level  *(STATUS: OK)*
- **Target:** `mars_dev.silver.dim_failure_level`
- **Source:** hardcoded `VALUES` (Michael's Ventra KPI Failure Levels sheet; no bronze table exists). Reasonable for a small taxonomy dimension.
- **Grain:** one row per failure_level code.
- **Encodes the required device-fault set (correct):** `metric_category=2` = Device Fault; `is_device_fault = TRUE` exactly for levels 1,2,3,4,5,16 (matches the design's `IN (1,2,3,4,5,16)` label set), with `is_operational` for 0/6/98/99, plus `severity_ordinal` and cat-3/4/5 back-office levels for completeness. The presence of the explicit `is_device_fault` flag is the "plus" the checklist hoped for.
- **Usage documented:** `WHERE is_device_fault = TRUE` for PS3 scope; join to `edw_kpi_rules ON FAILURE_LEVEL`.

**ISSUES:**
- **LOW — header says "41 levels" but the VALUES list enumerates ~31** (10 cat-2 + ~21 cat-3/4/5 representative rows). Self-documented as partial ("add them here when Michael provides the complete list"); the in-scope cat-2 set is complete and correct, so PdM use is unaffected. Reconcile the "41" claim or the row set to avoid confusion.
- **LOW — `metric_category_name` literal is `Device Fault` for cat-2** while project glossary phrases it "device faults / Metric-Category 2." Cosmetic.

---

## C. Cross-file consistency pass

- **CF-1 — Dependency ordering is correct.** S03 needs S01+S02; S04 needs S03; S15 needs S03; S16 needs S07+S04+S15; all are documented and the numeric file order is a valid topological build order. No forward-reference to an uncreated table.
- **CF-2 — Bronze naming is uniformly fully-prefixed.** All 26 distinct `mars_dev.bronze.*` references use `edw_*` / `ncs_stage_*` / `cta_*`. None are non-prefixed or implausible. The earlier mixed/un-prefixed and parquet-path references were all migrated (bug logs document each).
- **CF-3 — Scope-floor inconsistency between the two metric tables.** S14 `metric_hourly` filters `>= '2024-01-01'`; S07 `metric_daily` has NO 2024 floor (keeps all history `<= today`). They share the same bronze source but cover different windows. Decide one scope (the project's "2024+" convention suggests S07 should also floor at 2024-01-01) so daily and hourly metrics reconcile. MEDIUM at the set level.
- **CF-4 — `is_current` uniqueness is a single point of failure for the whole layer.** Eight runnable tables join `dim_device ... AND is_current=TRUE` and three more (S07, S14, S16) join on `DEVICE_KEY`. All correctness depends on S01 producing exactly one current row per DEVICE_ID and unique DEVICE_KEYs. The S01 HIGH finding therefore propagates layer-wide; fixing S01 (ROW_NUMBER dedup + a uniqueness assertion) de-risks every downstream table at once.
- **CF-5 — Build-pattern inconsistency (minor).** 16 of 18 files use `DROP TABLE IF EXISTS` + `CREATE TABLE`; only S06 and S17 use `CREATE OR REPLACE TABLE` (the pattern the design context names). DROP+CREATE is effectively idempotent but non-atomic and drops grants/history on each run. Consider standardizing on `CREATE OR REPLACE TABLE`. LOW.
- **CF-6 — Gold consumes only existing silver tables; no missing dependency.** Gold (`sql/gold/`) reads these 10 silver tables, all present here: `device_event_enriched` (9 refs), `dim_device` (7), `device_outage` (6), `tap_event_daily` (5), `metric_hourly` (4), `hw_config_current` (4), `tvm_sale_daily` (2), `metric_daily` (2), `kpi_daily` (2), `incident_root_cause` (1). No silver table read by gold is missing.
- **CF-7 — Silver tables NOT yet consumed by gold:** S02 `dim_event_type` (consumed transitively through S03), S05 `device_uptime_intervals`, S08 `kpi_avail_enriched` (the availability keystone), S12 `dim_facility`, S15 `maintenance_ledger`, S16 `usage_lifecycle_daily`, S18 `dim_failure_level`. These are legitimate (keystone/reference/PS5-RUL tables whose Gold consumers are PS5/design-stage). Not a defect — noted so the layer isn't assumed fully wired into the current Gold set.
- **CF-8 — READER-as-component is consistently honored.** No file models READER as a `mars_device_category`. S01 routes RSV/CSC/SCR to OTHER; S03 carries `COMPONENT_TYPE_*` for the reader-component grain; comments across S01/S03 reference the Reader_Component_Reframe.
- **CF-9 — Root cause = FAILURE_LEVEL is consistent** across S08 (carries FAILURE_LEVEL + relief_failure_level), S11 (`is_device_fault` IN (1,2,3,4,5,16) + taxonomy labels), and S18 (the authoritative `dim_failure_level` with `is_device_fault`). Free-text is supplemental only.

---

## D. Recommended actions (priority order)

1. **(HIGH, S01)** Replace `is_current = (CURRENT_FLAG = 1)` with `ROW_NUMBER() OVER (PARTITION BY DEVICE_ID ORDER BY INSERTED_DTM DESC) = 1` (or reconcile with the canonical `silver_dim_device_SCD2_v3.sql`). Add a post-build assertion: current-row count == distinct in-scope DEVICE_ID, and DEVICE_KEY is unique. De-risks the whole layer (CF-4).
2. **(MEDIUM, S08)** Add the output-row-count assertion (output == source avail_events rows) to catch relief range-join fan-out; or pre-rank relief to one row per (device, failure_level, event).
3. **(MEDIUM, CF-3 / S07)** Apply the same `>= '2024-01-01'` floor to S07 `metric_daily` as S14, or document why daily keeps full history. Reconcile the two metric windows.
4. **(MEDIUM, S16)** Decide PS5 device scope: if TVM/Reader RUL is needed, switch S16 to drive from `dim_device` LEFT JOIN `metric_daily` (header already names the fix). Flag the Nov-2025 metric stall as a wear-feature risk.
5. **(MEDIUM, S04)** Consider partitioning the outage-end `LEAD` by component (or merging overlapping intervals) so concurrent component faults don't truncate each other.
6. **(MEDIUM, S15)** Remove the redundant outer `TO_DATE(<date>, 'yyyy-MM-dd')` wrapper on `ledger_date` (Source A).
7. **(LOW)** S01 category literal `OTHER` vs `EXCLUDED` and the "Expect 5 categories" -> 4; S13 push the `mars_device_category='TVM'` / sentinel filter into Silver; standardize on `CREATE OR REPLACE TABLE` (CF-5); S18 reconcile the "41 levels" claim.
8. **(LOW, S17 design)** When the 4 SN tables land: read governed `mars_dev.bronze.*` (not S3 parquet), and reconcile `SPLIT_PART` -> Spark `split`/`split_part` before the file becomes runnable.

---

*End of report.*
