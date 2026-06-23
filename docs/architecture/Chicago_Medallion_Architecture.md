# Chicago Oracle EDW — Medallion Architecture

## Overview

This document describes the Bronze -> Silver -> Gold medallion architecture for the Chicago Transit Authority (CTA) fare device predictive maintenance project, built on top of the Oracle EDW (`EDW`, `NCS_STAGE`, `CTA`, `SVN_STAGE`) source schemas.

**Infrastructure**
- at ``, database `postgres`
- Bronze schema: `bronze`
- Silver schema: `silver`
- Gold schema: `gold`

---

## Bronze Layer

### Purpose
Raw data loaded from Oracle EDW into with no transformation. Column names and data types preserved from Oracle (normalised to lowercase where needed).

### Sources
Oracle schemas: `EDW`, `NCS_STAGE`, `CTA`
(ALL 35 `SVN_STAGE` tables = 0 rows — excluded)

### Summary: 61 Tables with Real Data

**REMOVED tables (2026-06-12 — 2 removed, net -2):**
- `NCS_STAGE.DEVICE_CURRENT_HW_CONFIG` (147,593 rows) — **DROPPED FROM PIPELINE**. S07 hw_config_current now sources from `EDW.DEVICE_CURRENT_HW_CONFIG` (11,736 rows) only.
- `CTA.CTA_REAL_TIME_BUS_DATA` (530M rows) — **DROPPED FROM PIPELINE**. Silver S15 bus_realtime_daily removed entirely.

**NEWLY ADDED tables (2026-06-10 — 2 new, net):**
- `NCS_STAGE.SALE_TRANSACTION` (152M rows) — TVM daily sale volume, revenue, error rate → silver.tvm_sale_daily

**PREVIOUSLY ADDED tables vs original 44:**
- `NCS_STAGE.DEVICE_EVENT_HISTORY` (591M rows) — direct SEVERITY column per event
- `NCS_STAGE.TRANSIT_FACILITY` (1,862 rows) — REPLACES zero-row EDW.FACILITY_DIMENSION
- `NCS_STAGE.DEVICE_TYPE` (83 rows) — REPLACES zero-row EDW.DEVICE_TYPE_DIMENSION (BUS_DEVICE_FLAG)
- `CTA.MM_DAILY_TRANSACTION_TIMING` (531K rows) — per-device daily tap timing
- `NCS_STAGE.CASHBOX_MANUAL_COUNTS` (442K rows) — TVM cashbox maintenance visits (PS5)
- `CTA.AVAILABILITY_LEVELS` (41 rows) — failure_level decode (1=Critical, 2=Major, 3=Minor)
- `CTA.KPI_TVM_TABLE` (445 rows) — TVM station mapping
- `EDW.AJ_METRIC_FACT` (1M rows) — metrics with actual TRANSACTION_DTM
- 13 more reference/decode tables

| Oracle Table | Rows | Bronze Table | Primary PS Use
|---|---|---|---
| EDW.DEVICE_EVENT | 1.12 billion | bronze.device_event | PS1 PS2 PS3 PS4
| NCS_STAGE.DEVICE_EVENT_HISTORY | 591 million | bronze.ncs_device_event_history | PS1 PS2 PS3 PS4
| EDW.ABP_TAP | 2.05 billion | bronze.abp_tap | PS1 PS2
| EDW.DEVICE_METRIC | 592 million | bronze.device_metric | PS4
| EDW.METRIC_SUMMARY_BY_DAY | 104 million | bronze.metric_summary_by_day | PS4
| NCS_STAGE.DEVICE_END_OF_DAY_MSG_COUNT | 159 million | bronze.ncs_device_end_of_day_msg_count | PS1 PS4
| NCS_STAGE.CASHBOX_TRACKING | 140 million | bronze.ncs_cashbox_tracking | PS2 PS5
| CTA.ABP_USE_TRAN_TIMING_DATA | 89.4 million | bronze.cta_abp_use_tran_timing_data | PS4
| NCS_STAGE.DEVICE_END_OF_DAY | 14.9 million | bronze.ncs_device_end_of_day | PS1 PS4
| NCS_STAGE.SALE_TRANSACTION_DEVICE_MSG | 27 million | bronze.ncs_sale_transaction_device_msg | PS1 PS4
| CTA.KPI_TVM_DATE_TABLE | 19.9 million | bronze.cta_kpi_tvm_date_table | PS1
| EDW.DEVICE_LOCATION_HISTORY | 3.66 million | bronze.edw_device_location_history | PS5
| NCS_STAGE.SALE_TRANSACTION | 152 million | bronze.ncs_sale_transaction | PS1 (TVM)
| ~~CTA.CTA_REAL_TIME_BUS_DATA~~ | ~~530 million~~ | ~~bronze.cta_real_time_bus_data~~ | **REMOVED 2026-06-12**
| EDW.KPI_DETAIL_EVENTS_BY_DAY | 1.13 million | bronze.kpi_detail_events_by_day | PS1
| EDW.DEVICE_LAST_SET_EVENT | 2.07 million | bronze.device_last_set_event | PS1 PS2
| EDW.AJ_METRIC_FACT | 1.03 million | bronze.edw_aj_metric_fact | PS4
| CTA.MM_DAILY_TRANSACTION_TIMING | 531,414 | bronze.cta_mm_daily_transaction_timing | PS1 PS4
| NCS_STAGE.CASHBOX_MANUAL_COUNTS | 442,201 | bronze.ncs_cashbox_manual_counts | PS5
| EDW.AVAILABILITY_EVENTS | 645,410 | bronze.availability_events | PS1 PS3
| EDW.KPI_SUMMARY_BY_DAY | 221,465 | bronze.kpi_summary_by_day | PS1
| CTA.SERVICENOW_AVAILABILITY_EVENTS | 295,960 | bronze.cta_servicenow_availability_events | PS3
| ~~NCS_STAGE.DEVICE_CURRENT_HW_CONFIG~~ | ~~147,593~~ | ~~bronze.ncs_device_current_hw_config~~ | **REMOVED 2026-06-12 — EDW-only**
| EDW.DEVICE_DIMENSION | 189,767 | bronze.device_dimension | All PS
| EDW.DEVICE_CURRENT_TABLES | 103,924 | bronze.device_current_tables | PS4
| EDW.DEVICE_CURRENT_SW_CONFIG | 59,968 | bronze.device_current_sw_config | PS4
| EDW.DATE_DIMENSION | 72,827 | bronze.date_dimension | All PS
| EDW.DEVICE_LAST_STATE | 90,689 | bronze.device_last_state | PS1
| NCS_STAGE.DAILY_CASHBOX_DETAIL | 43,884 | bronze.ncs_daily_cashbox_detail | PS2
| EDW.DEVICE_CURRENT_HW_CONFIG | 11,736 | bronze.edw_device_current_hw_config | PS3 PS5
| NCS_STAGE.CASHBOX_INVENTORY | 9,794 | bronze.ncs_cashbox_inventory | PS2
| NCS_STAGE.DEVICE | 7,859 | bronze.ncs_device | All PS
| NCS_STAGE.DEVICE_CONTROL_GROUP | 914 | bronze.ncs_device_control_group | All PS
| EDW.AVAILABILITY_RELIEF | 1,604 | bronze.availability_relief | PS1
| NCS_STAGE.STOP_POINT | 12,884 | bronze.ncs_stop_point | PS1 PS3
| NCS_STAGE.TRANSIT_FACILITY | 1,862 | bronze.ncs_transit_facility | All PS
| CTA.SLDC_MONTHLY_SUMMARY | 12,788 | bronze.cta_sldc_monthly_summary | PS1
| CTA.KPI_MONTHLY_SUMMARY | 851 | bronze.cta_kpi_monthly_summary | PS1
| CTA.KPI_VARIABLE_FEE | 631 | bronze.cta_kpi_variable_fee | PS1
| CTA.SERVICENOW_DATA_FROM_JUMPBOX | 604 | bronze.cta_servicenow_data_from_jumpbox | PS3
| NCS_STAGE.ACTIVITY_CODE | 167 | bronze.ncs_activity_code | PS5
| EDW.ACTIVITY_CODE_DIMENSION | 189 | bronze.edw_activity_code_dimension | PS5
| EDW.KPI_TARGET | 216 | bronze.kpi_target | PS1
| EDW.AVAILABILITY_PERIODS | 16 | bronze.availability_periods | PS1
| NCS_STAGE.DEVICE_CONTROL_GROUP_TYPE | 5 | bronze.ncs_device_control_group_type | All PS
| EDW.LATE_TRANSACTION_DETAIL | 12,609 | bronze.late_transaction_detail | PS1
| EDW.EVENT_TYPE_DIMENSION | 441 | bronze.event_type_dimension | All PS
| NCS_STAGE.EVENT | 422 | bronze.ncs_event | All PS
| EDW.KPI | 60 | bronze.kpi | PS1
| EDW.KPI_RULES | 117 | bronze.kpi_rules | PS1
| CTA.KPI_AGENCY_MAP | 92 | bronze.cta_kpi_agency_map | PS1
| NCS_STAGE.DEVICE_TYPE | 83 | bronze.ncs_device_type | All PS
| EDW.METRIC_DIMENSION | 62 | bronze.metric_dimension | PS4
| EDW.DEVICE_MESSAGE_TYPE_DIMENSION | 55 | bronze.edw_device_message_type_dimension | PS1 PS4
| NCS_STAGE.COMPONENT_TYPE | 44 | bronze.ncs_component_type | PS5
| CTA.KPI_TVM_TABLE | 445 | bronze.cta_kpi_tvm_table | PS1
| CTA.METRIX_FACILITY_MAP | 32 | bronze.cta_metrix_facility_map | All PS
| CTA.AVAILABILITY_LEVELS | 41 | bronze.cta_availability_levels | PS3
| NCS_STAGE.CASHBOX_TYPE | 15 | bronze.ncs_cashbox_type | PS2
| EDW.CASHBOX_TYPE_DIMENSION | 15 | bronze.edw_cashbox_type_dimension | PS2
| NCS_STAGE.CASHBOX_EVENT | 20 | bronze.ncs_cashbox_event | PS2
| EDW.CASHBOX_EVENT_DIMENSION | 20 | bronze.edw_cashbox_event_dimension | PS2
| NCS_STAGE.STOP_POINT_TYPE | 8 | bronze.ncs_stop_point_type | PS1
| NCS_STAGE.DEVICE_CONTROL_GROUP_TYPE | 5 | (already listed above) | All PS

### Excluded Tables (0 rows in Oracle)
- ALL 35 `SVN_STAGE` tables — ServiceNow staging, all empty
- `EDW.DEVICE_TYPE_DIMENSION` (0) — REPLACED by `NCS_STAGE.DEVICE_TYPE` (83 rows)
- `EDW.FACILITY_DIMENSION` (0) — REPLACED by `NCS_STAGE.TRANSIT_FACILITY` (1,862 rows)
- `EDW.METRIC_HISTORY_PIVOTED` (0) — REPLACED by `EDW.DEVICE_METRIC` pivot
- `EDW.MAINTENANCE_ACTIVITY` (0) — no direct replacement; `NCS_STAGE.CASHBOX_MANUAL_COUNTS` covers TVM cashbox
- `EDW.MAINTENANCE_ACTIVITY_COUNT` (0) — no replacement

---

## Silver Layer (silver)

### Purpose
Cleaned, de-duplicated, and enriched tables with derived business fields. Joins device context onto all fact tables. Converts Oracle integer day keys (YYYYMMDD) to date type.

### 13 Silver Tables

| Table | Source Bronze Tables | Key Derivations
|---|---|---
| `silver.dim_device` | bronze.device_dimension + bronze.ncs_device + bronze.ncs_device_type | `mars_device_category`, `bus_device_flag` (from NCS_STAGE.DEVICE_TYPE), `is_current`
| `silver.dim_facility` | bronze.ncs_transit_facility (1,862 rows) | NEW — replaces zero-row EDW.FACILITY_DIMENSION; `latitude`, `longitude`, `transit_mode`
| `silver.dim_event_type` | bronze.event_type_dimension, bronze.ncs_event | `component_subsystem`, `severity_label`, `is_oos_event`
| `silver.device_event_enriched` | bronze.device_event + bronze.ncs_device_event_history + dim tables | `transit_day`, `hour_bucket`, `effective_severity` (NCS direct SEVERITY), `ncs_severity`
| `silver.device_outage` | device_event_enriched (is_oos_event=TRUE) | `outage_start/end`, `duration_min`, LEAD-based end estimation
| `silver.device_uptime_intervals` | bronze.device_last_state + bronze.ncs_device_end_of_day_msg_count | `last_heartbeat`, `downtime_proxy_seconds`
| `silver.hw_config_current` | bronze.edw_device_current_hw_config (EDW-only, 11,736 rows) + bronze.ncs_cashbox_manual_counts | `component_age_days`, `cashbox_service_count` (TVM only) — NCS_STAGE source removed 2026-06-12
| `silver.metric_daily` | bronze.device_metric + bronze.metric_dimension + bronze.aj_metric_fact + bronze.cta_mm_daily_transaction_timing | Pivot 800/810/401, `delta`, `counter_reset_flag`, `avg_tap_timing_ms`
| `silver.kpi_avail_enriched` | bronze.availability_events + bronze.cta_servicenow_availability_events + bronze.cta_availability_levels | `failure_level_desc` (from AVAILABILITY_LEVELS), `svn_data_available`
| `silver.kpi_daily` | bronze.kpi_detail_events_by_day + KPI tables + bronze.cta_kpi_monthly_summary | `kpi_vs_target_gap`, `sldc_monthly_trend`, `kpi_breach_flag`
| `silver.tap_event_daily` | bronze.abp_tap + bronze.cta_abp_use_tran_timing_data + bronze.cta_mm_daily_transaction_timing | `tap_reject_rate_pct`, `avg_tap_timing_ms`, `p95_timing_ms`
| `silver.incident_root_cause` | bronze.cta_servicenow_availability_events (CTA mirror only — SVN_STAGE=0) | `root_cause_category`, `failure_level` (1/2/3)
| `silver.tvm_sale_daily` | bronze.ncs_sale_transaction (152M rows) | `daily_sales_count`, `error_txn_rate_pct`, `cash_sales_pct`, `total_revenue_cents`
| ~~`silver.bus_realtime_daily`~~ | ~~bronze.cta_real_time_bus_data (530M rows)~~ | **REMOVED FROM PIPELINE 2026-06-12** — CTA_REAL_TIME_BUS_DATA dropped

### Key Derived Fields

**mars_device_category** (4 values: TVM, GATE, READER, VALIDATOR)
Derived from `DEVICE_TYPE_NAME` + `DEVICE_CONTROL_GROUP_TYPE_NAME` + `NCS_STAGE.DEVICE_TYPE.BUS_DEVICE_FLAG`

**bus_device_flag** (TRUE for VALIDATOR/READER) — from `NCS_STAGE.DEVICE_TYPE` (replaces 0-row `EDW.DEVICE_TYPE_DIMENSION`)

**component_subsystem** (from EVENT_TYPE_ID ranges)
SYSTEM (100-199), CSC_READER (200-299), SCRST (300-399), BHU (400-499), CHU (500-599), PIN_PAD (800-899), PRINTER (900-999), GATE_MECH (1200-1299), ALARM (1400-1499), BANKCARD (1600-1699), COMMS (>=50000)

**effective_severity** = `COALESCE(ncs_severity, event_type_severity)` — uses direct NCS SEVERITY first

**transit_day** = `TO_DATE(day_key::text, 'YYYYMMDD')::date`

---

## Gold Layer (gold)

### Purpose
ML-ready feature tables, one per problem statement covering all device types. Targets computed in SQL. Naming: `gold.device_ps{n}_{grain}`.

### 5 Unified Gold Tables (1 per PS — all device types)

| Gold Table | Grain | Target | Device Types |
|---|---|---|---
| `device_ps1_daily` | (device_id, transit_day) | `will_fail_7d` (binary) | TVM / GATE / VALIDATOR / READER |
| `device_ps2_chains` | (device_id, transit_day) ≥2 faults | `subsystem_chain` (deterministic) | All device types |
| `device_ps3_incident` | (device_id, avail_event_id) | `failure_level_label` (MINOR/MAJOR/CRITICAL) | All device types |
| `device_ps4_hourly` | (device_id, hour_bucket) | `ensemble_anomaly_flag` (binary, 3 signals) | All device types |
| `device_ps5_component` | (device_id, component_description) | `days_to_failure` + `is_censored` | All device types |

### PS Feature Summary

**PS1 — Predictive Failure** (~95% feasible)
Key features: `event_count`, `critical_events`, subsystem event counts, `events_7d/30d`, `total_outage_min`, `tap_count`, `tap_reject_rate`, `avg_tap_timing_ms`, `kpi_value`, `kpi_breach_flag`, `metric_800_delta`, `sldc_trend`, `facility_name`, `station` (TVM)
- TVM: `daily_sales_count`, `sales_error_rate_pct`, `cash_sales_pct`, `daily_revenue_cents`, `sales_decline_flag` (from NCS_STAGE.SALE_TRANSACTION)
- VALIDATOR/READER: ~~`bus_active_ratio`, `bus_in_service_ratio`, `bus_has_work_order`, `bus_unavailable_flag`~~ — **REMOVED 2026-06-12** (CTA.CTA_REAL_TIME_BUS_DATA dropped from pipeline)

**PS2 — Cascade Chain Analysis** (~85% feasible)
Key features: `subsystem_chain` (STRING_AGG), `effective_severity` (direct NCS SEVERITY), `cascade_severity_score`, `markov_entropy`. SVN CMDB not available.

**PS3 — Root Cause Classification** (~50% feasible, SIMPLIFIED)
Simplified target: `failure_level` (1/2/3 from CTA.AVAILABILITY_LEVELS decode) instead of fc_description/ac_description.
Key features: `events_24h_prior`, `critical_7d_prior`, `component_age_days`, `fault_description` text, `failure_level_desc` (NEW decode).

**PS4 — Anomaly Detection** (~95% feasible)
3-signal ensemble:
- Signal 1: `event_rate_anomaly` (28d rolling SPC 3-sigma)
- Signal 2: `metric_anomaly` (metric_800 deviation > 2-sigma from baseline)
- Signal 3: `reject_rate_anomaly` (tap reject > 5%)
`ensemble_anomaly_flag` = 1 if >= 2 of 3 signals active.

**PS5 — Survival Analysis** (~45% feasible)
Key features: `component_age_days`, `failures_total`, `mtbf_proxy_days`, `device_location_tenure_days`.
NEW: `cashbox_service_count` (TVM only — from NCS_STAGE.CASHBOX_MANUAL_COUNTS 442K rows).
GATE/READER/VALIDATOR: still limited by SVN_STAGE=0 rows.

---

## Build Order (13 Silver + 5 unified Gold)

```
-- Step 1: Silver dimensions (no upstream silver dependencies)
01_dim_device__create.sql          -- uses: DEVICE_DIMENSION + ncs_device + ncs_device_type
02_dim_event_type__create.sql
12_dim_facility__create.sql        -- NEW: uses NCS_STAGE.TRANSIT_FACILITY

-- Step 2: Silver facts (depend on silver dims)
03_device_event_enriched__create.sql   -- uses: DEVICE_EVENT + ncs_device_event_history
04_device_outage__create.sql
05_device_uptime_intervals__create.sql
06_hw_config_current__create.sql       -- uses: edw_device_current_hw_config (EDW-only) + ncs_cashbox_manual_counts (TVM cashbox)
07_metric_daily__create.sql            -- uses: DEVICE_METRIC + MM_DAILY_TRANSACTION_TIMING
08_kpi_avail_enriched__create.sql      -- uses: cta_availability_levels (NEW)
09_kpi_daily__create.sql               -- uses: cta_kpi_monthly_summary (NEW)
10_tap_event_daily__create.sql         -- uses: cta_mm_daily_transaction_timing (NEW)
11_incident_root_cause__create.sql
13_tvm_sale_daily__create.sql          -- uses ncs_sale_transaction (152M rows)
-- 14_bus_realtime_daily__create.sql  -- REMOVED 2026-06-12 (cta_real_time_bus_data dropped)

-- Step 3: Gold tables (depend on all silver tables) — 5 unified files (one per PS)
-- PS1 (1 unified file) device_ps1_daily          -- TVM +8 sales cols; VALIDATOR/READER +bus_ops+pace_tap cols
-- PS2 (1 unified file) device_ps2_chains
-- PS3 (1 unified file) device_ps3_incident        -- uses failure_level_label from AVAILABILITY_LEVELS
-- PS4 (1 unified file) device_ps4_hourly           -- 3-signal ensemble (event_rate_anomaly, metric_anomaly, reject_rate_anomaly)
-- PS5 (1 unified file) device_ps5_component        -- cashbox_service_count for TVM; survival analysis all types
```
