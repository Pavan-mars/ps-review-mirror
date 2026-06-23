# Chicago Oracle EDW — Data Gaps Analysis

## Executive Summary

The Oracle EDW source environment has **two classes of data gaps** that materially affect ML feature richness:

1. **SVN_STAGE: All 35 tables have 0 rows** — The ServiceNow staging schema is completely empty. This eliminates work-order lifecycle data, CMDB CI data, and incident management detail across all problem statements.
2. **EDW maintenance and facility tables have 0 rows** — `EDW.MAINTENANCE_ACTIVITY`, `EDW.MAINTENANCE_ACTIVITY_COUNT`, `EDW.FACILITY_DIMENSION`, `EDW.DEVICE_TYPE_DIMENSION`, `EDW.DEVICE_STATE_TYPE_DIMENSION`, and `EDW.METRIC_HISTORY_PIVOTED` are all empty.

---

## Gap 1: SVN_STAGE — All 35 Tables Empty (0 rows)

### Affected Tables

| Table | Expected Content | PS Impact |
|---|---|---|
| SVN_STAGE.INCIDENT | ServiceNow incident headers | PS3 (primary source) |
| SVN_STAGE.FAULT | Fault records per incident | PS3, PS1 |
| SVN_STAGE.WORK_ORDER | Work order headers | PS5 (maintenance history) |
| SVN_STAGE.WORK_ORDER_TASK | Work order line items | PS5 |
| SVN_STAGE.CMDB_CI | Configuration item records | PS2, PS3 |
| SVN_STAGE.CMDB_REL | CI relationships | PS2 |
| SVN_STAGE.CHANGE_REQUEST | Change management records | PS4 |
| SVN_STAGE.PROBLEM | Problem records | PS3 |
| SVN_STAGE.PROBLEM_TASK | Problem task detail | PS3 |
| SVN_STAGE.SYS_USER | Technician records | PS5 |
| SVN_STAGE.SYS_USER_GROUP | Assignment groups | PS5 |
| + 24 more SVN_STAGE tables | Various SN data | PS1–PS5 |

### Root Cause (Known)
SVN_STAGE is populated by a ServiceNow→Oracle ETL job. At the time of the data extract, the ETL staging job had not loaded data. Cubic confirmed the schema exists but the load had not been executed.

### Workarounds Applied

**PS3 — Root Cause Classification:**
`CTA.SERVICENOW_AVAILABILITY_EVENTS` (295,960 rows, 57 cols) is a CTA-maintained mirror of ServiceNow data pre-joined to availability events. Used as the primary incident source in `silver.incident_root_cause` and the `device_ps3_incident` gold table.

`CTA.SERVICENOW_DATA_FROM_JUMPBOX` (604 rows, 27 cols) provides supplemental ServiceNow fields (incident number, state, category, assigned_to, priority) for a small subset of devices.

**PS2 — Cascade Analysis:**
CMDB CI dependency features (related-device cascade via shared CI relationships) are marked `NULL` with `svn_ci_data_available = FALSE` in all PS2 gold tables. The chain analysis proceeds as event-sequence within a single device only.

**PS5 — Survival Analysis:**
Work-order history unavailable. Component lifetime estimated from:
- `REPORTED_CHANGED_DTM` in `EDW.DEVICE_CURRENT_HW_CONFIG` (installation date proxy — NCS_STAGE source removed 2026-06-12)
- `silver.device_outage` OOS events (is_hardware_oos_event=TRUE) as failure proxy
- `is_censored = TRUE` where no OOS failure observed

All PS5 gold tables include `has_work_order_data = FALSE` and `has_maintenance_log = FALSE` flags.

### Feature Impact by Problem Statement

| PS | Expected SVN Feature | Gap Impact | Workaround |
|---|---|---|---|
| PS1 | Open incident count at prediction time | Moderate — reduces leading indicator richness | Not available |
| PS1 | Mean time to repair (MTTR) from work orders | High | Not available |
| PS2 | CI dependency graph for cross-device cascade | High — limits to within-device chains | NULL placeholder columns |
| PS3 | Full incident lifecycle timestamps | High | CTA SN mirror (295K rows) |
| PS3 | Technician assignment, resolution notes | Moderate | CTA jumpbox (604 rows, partial) |
| PS4 | Change window flag (change freeze periods) | Low | Not available |
| PS5 | Work order maintenance history | Critical | Component age + outage proxy |
| PS5 | Maintenance action codes | Critical | NCS_STAGE.ACTIVITY_CODE (167 rows, no links) |

---

## Gap 2: EDW Zero-Row Tables

### EDW.DEVICE_TYPE_DIMENSION (0 rows)
**Expected content:** Device type master with type descriptions.
**Workaround:** Device type information is embedded in `EDW.DEVICE_DIMENSION` columns `DEVICE_TYPE_ID`, `DEVICE_TYPE_NAME`, `DEVICE_TYPE_DESC`. No separate join required.

### EDW.FACILITY_DIMENSION (0 rows)
**Expected content:** Facility master with address, location coordinates.
**Workaround:** Facility information (`FACILITY_ID`, `FACILITY_NAME`) is embedded in `EDW.DEVICE_DIMENSION` and in `EDW.AVAILABILITY_EVENTS`. Geographic coordinates are partially available in `EDW.DEVICE_EVENT.LATITUDE` / `LONGITUDE`.

### EDW.DEVICE_STATE_TYPE_DIMENSION (0 rows)
**Expected content:** State type lookup for `DEVICE_STATE_TYPE_KEY`.
**Workaround:** `DEVICE_STATE_TYPE_KEY` in `EDW.DEVICE_LAST_STATE` cannot be decoded. The key is carried as-is in `silver.device_uptime_intervals`. Text-based severity derivation uses `EVENT_STATE_TYPE_NAME` strings from `EDW.DEVICE_EVENT` instead.

### EDW.MAINTENANCE_ACTIVITY (0 rows)
### EDW.MAINTENANCE_ACTIVITY_COUNT (0 rows)
**Expected content:** Formal maintenance log entries and counts.
**Impact:** PS5 survival analysis lacks work order/maintenance history entirely.
**Workaround:** NCS_STAGE hardware config change dates used as installation date proxy.

### EDW.METRIC_HISTORY_PIVOTED (0 rows)
**Expected content:** Pre-pivoted wide-format metric table.
**Workaround:** `EDW.DEVICE_METRIC` (592M rows at 15-min grain) used instead. Aggregated to daily in `silver.metric_daily` using `EDW.METRIC_SUMMARY_BY_DAY` (104M rows) as primary source.

---

## Gap 3: CTA.SERVICENOW_AVAILABILITY_EVENTS Coverage

While the CTA SN mirror (295,960 rows) provides a usable PS3 source, it has known limitations:

| Dimension | CTA Mirror | Full SVN_STAGE |
|---|---|---|
| Row count | 295,960 | Unknown (not loaded) |
| Columns available | 57 (key SN fields) | ~150+ across tables |
| Work order detail | No | Yes |
| Technician assignment | No | Yes |
| Incident sub-tasks | No | Yes |
| CMDB CI linkage | No | Yes |
| Time span | Aligned with availability events period | Full SN history |

**`svn_data_available` flag** in `silver.kpi_avail_enriched` indicates which availability events have a matching CTA SN mirror record. For rows where `svn_data_available = FALSE`, the incident was recorded in EDW availability events but has no ServiceNow cross-reference in the CTA mirror.

---

## Gap 4: Device Type Coverage Gaps

### VALIDATOR devices
Bus validators have limited KPI coverage. `CTA.KPI_TVM_DATE_TABLE` covers TVM service calendars only. Validator KPI rows in `EDW.KPI_DETAIL_EVENTS_BY_DAY` may have lower coverage than TVM rows.

### GATE devices
`EDW.AVAILABILITY_EVENTS` carries `ARRAY_ID`/`ARRAY_SIZE` for gate arrays (turnstile banks), but `ARRAY_ID` linkage to individual gate rows requires additional Cubic clarification.

---

---

## Gap 5: Tables Removed from Pipeline (2026-06-12)

### NCS_STAGE.DEVICE_CURRENT_HW_CONFIG (147,593 rows) — REMOVED

**Decision (2026-06-12):** `NCS_STAGE.DEVICE_CURRENT_HW_CONFIG` dropped from Bronze and Silver pipelines.

**Root cause:** The NCS_STAGE copy contained device-level snapshots that could not be reliably deduplicated against the EDW copy. Row counts (147,593 vs 11,736) suggest the NCS_STAGE table carries historical change records rather than current-state config, introducing installation-date ambiguity in PS5 survival analysis.

**Resolution:** Silver S07 `hw_config_current` now sources exclusively from `EDW.DEVICE_CURRENT_HW_CONFIG` (11,736 rows). The EDW table provides current-state hardware configuration with cleaner `REPORTED_CHANGED_DTM` semantics for component age calculation.

**PS impact:**
- PS3: `component_age_days` still available (EDW source). Reduced row count acceptable.
- PS5: `REPORTED_CHANGED_DTM` still available from EDW source. Survival analysis unaffected architecturally.

### CTA.CTA_REAL_TIME_BUS_DATA (530M rows) + silver.bus_realtime_daily — FULLY REMOVED

**Decision (2026-06-12):** `CTA.CTA_REAL_TIME_BUS_DATA` dropped from Bronze. `silver.bus_realtime_daily` (S15) removed entirely. Bus health features removed from all Gold tables.

**Root cause:** The 530M-row bus data table's `BUS_ID` linkage to VALIDATOR/READER fare devices was unreliable — the fare device schema does not maintain a consistent bus-to-device mapping that could be joined at prediction time without introducing significant data leakage.

**Resolution:** Bus operational context features (`bus_active_ratio`, `bus_in_service_ratio`, `bus_has_work_order`, `bus_unavailable_flag`) removed from PS1 VALIDATOR feature set. PS4 ensemble remains a pure 3-signal model. Bronze count reduced from 63 to 61; Silver count reduced from 14 to 13 at this point, then expanded to 18 (S01–S18) with additions on 2026-06-22/23 (S14 metric_hourly, S15 maintenance_ledger, S16 usage_lifecycle_daily, S17 incident_history, S18 dim_failure_level).

**PS impact:**
- PS1: VALIDATOR/READER feature set reduced (bus health features removed). Core failure prediction still feasible from event + metric signals.
- PS4: 3-signal ensemble only (bus_ops_anomaly fourth signal dropped). `ensemble_anomaly_flag` definition unchanged.

---

## Summary Table: Feature Availability by PS

| Feature Category | PS1 | PS2 | PS3 | PS4 | PS5 |
|---|---|---|---|---|---|
| Device event history (1.12B rows) | FULL | FULL | FULL | FULL | PARTIAL |
| KPI/availability data | FULL | — | FULL | — | — |
| Tap transaction volume (2.05B) | FULL | — | — | FULL | PARTIAL |
| Hardware config / component age | — | — | PARTIAL (EDW-only 11,736 rows) | — | PARTIAL (EDW-only) |
| ServiceNow incident data | PARTIAL | PARTIAL | PARTIAL (CTA mirror only) | — | — |
| Work order / maintenance history | NONE | NONE | NONE | NONE | NONE |
| CMDB CI dependencies | NONE | NONE | NONE | NONE | NONE |
| Metric time series (592M) | PARTIAL | — | — | FULL | — |
| Network monitoring (SolarWinds) | — | — | — | REMOVED | — |
