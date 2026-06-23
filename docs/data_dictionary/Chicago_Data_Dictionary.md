# Chicago Oracle EDW — Data Dictionary

## 1. Key Column Definitions

### EDW.DEVICE_EVENT (bronze.device_event) — 49 cols, 1.12B rows

| Column | Type | Description
|---|---|---
| DW_DEVICE_EVENT_ID | NUMBER | Surrogate primary key — used for de-duplication
| EVENT_DAY_KEY | NUMBER | Transit day as YYYYMMDD integer (not a DATE)
| EVENT_TYPE_KEY | NUMBER | FK to EDW.EVENT_TYPE_DIMENSION
| DEVICE_KEY | NUMBER | FK to EDW.DEVICE_DIMENSION
| EVENT_DTM | TIMESTAMP | Timestamp of the event
| CLEAR_DTM | TIMESTAMP | Timestamp when event was cleared/resolved
| AUTOMATIC_CLEAR_FLAG | NUMBER(1) | 1 = system auto-cleared
| EVENT_STATE_TYPE_ID | NUMBER | Numeric state type code
| EVENT_STATE_TYPE_NAME | VARCHAR2 | State name string (e.g. "Fault", "Out of Service", "Critical")
| COMPONENT_SERIAL_NBR | VARCHAR2 | Serial number of the specific component involved
| COMPONENT_TYPE_ID | NUMBER | FK to component type reference
| COMPONENT_TYPE_NAME | VARCHAR2 | Human-readable component name
| EXTENDED_DATA_SHORT | VARCHAR2 | Short diagnostic data string
| EXTENDED_DATA_LONG | VARCHAR2 | Long diagnostic data string (may contain JSON or pipe-delimited)
| DEVICE_ID | NUMBER | Business key for the device
| OPERATOR_ID | NUMBER | Transit operator ID
| FACILITY_ID | NUMBER | Facility where device is located
| BUS_ID | NUMBER | Bus vehicle ID (for VALIDATOR devices)
| ROUTE_NUMBER | NUMBER | Route number (for bus-mounted devices)
| STOP_POINT_ID | NUMBER | Stop point where event occurred
| LATITUDE | NUMBER | GPS latitude (where available)
| LONGITUDE | NUMBER | GPS longitude (where available)
| EDW_UPDATED_DTM | TIMESTAMP | Last EDW update — used for de-dup ordering
| POSTING_DAY_KEY | NUMBER | Day key for EDW posting

### EDW.DEVICE_DIMENSION (bronze.device_dimension) — 66 cols, 189,767 rows

| Column | Type | Description
|---|---|---
| DEVICE_KEY | NUMBER | Surrogate PK (join to event tables)
| DEVICE_ID | NUMBER | Business device identifier
| DEVICE_NAME | VARCHAR2 | Display name of device
| DEVICE_TYPE_ID | NUMBER | Numeric device type
| DEVICE_TYPE_NAME | VARCHAR2 | Device type name — used for mars_device_category derivation
| DEVICE_TYPE_DESC | VARCHAR2 | Device type description
| DEVICE_CONTROL_GROUP_ID | NUMBER | Control group ID
| DEVICE_CONTROL_GROUP_NAME | VARCHAR2 | Control group name
| DEVICE_CONTROL_GROUP_TYPE_ID | NUMBER | Control group type ID
| DEVICE_CONTROL_GROUP_TYPE_NAME | VARCHAR2 | Used for GATE category derivation (RVG/HBG/SAG)
| TRANSIT_MODE_ID | NUMBER | Transit mode (rail/bus)
| TRANSIT_MODE_NAME | VARCHAR2 | "RAIL" or "BUS"
| AGENCY_ID | NUMBER | Agency ID
| AGENCY_NAME | VARCHAR2 | Agency name (CTA)
| FACILITY_ID | NUMBER | Current facility
| FACILITY_NAME | VARCHAR2 | Facility display name
| OPERATOR_ID | NUMBER | Operator ID
| OPERATOR_NAME | VARCHAR2 | Operator name
| CURRENT_FLAG | NUMBER(1) | 1 = current active record in SCD
| DEVICE_STATUS_ID | NUMBER | 1 = active device
| DEVICE_SERIAL_NUMBER | VARCHAR2 | Physical hardware serial number
| BUS_ID | NUMBER | Bus vehicle ID (VALIDATOR devices)
| INSERTED_DTM | TIMESTAMP | Record insertion date (effective_from)
| UPDATED_DTM | TIMESTAMP | Last update date (effective_to)

### EDW.EVENT_TYPE_DIMENSION (bronze.event_type_dimension) — 6 cols, 441 rows

| Column | Type | Description
|---|---|---
| EVENT_TYPE_KEY | NUMBER | Surrogate PK
| EVENT_TYPE_ID | NUMBER | Business event type code (Cubic-defined)
| EVENT_SOURCE | VARCHAR2 | Source system of the event
| EVENT_TYPE_NAME | VARCHAR2 | Short event name
| EVENT_TYPE_DESC | VARCHAR2 | Full description
| SEVERITY | NUMBER(3,0) | 0=debug, 1=info, 2=warn, 3=critical

### EDW.AVAILABILITY_EVENTS (bronze.availability_events) — 24 cols, 645,410 rows

| Column | Type | Description
|---|---|---
| TRANSIT_DAY_KEY | NUMBER | Day key YYYYMMDD
| EVENT_ID | NUMBER | Availability event business key (joins to CTA SN mirror)
| RMA | VARCHAR2 | Return merchandise authorization code
| DEVICE_ID | NUMBER | Device business key
| DEVICE_KEY | NUMBER | Device surrogate key
| FAULT_STATE | VARCHAR2 | State name at time of fault
| FAILURE_LEVEL | NUMBER | 1=Minor, 2=Major, 3=Critical
| START_DTM | TIMESTAMP | Outage start time
| END_DTM | TIMESTAMP | Outage end time (NULL if unresolved)
| FAULT_DESCRIPTION | VARCHAR2 | Free-text fault description
| SYMPTOM | VARCHAR2 | Observed symptom
| PROBLEM | VARCHAR2 | Diagnosed problem
| RESOLUTION | VARCHAR2 | Resolution text
| EXCLUDED | NUMBER(1) | 1 = excluded from KPI calculation
| ARRAY_ID | NUMBER | Gate array identifier (GATE devices)
| ARRAY_SIZE | NUMBER | Number of gates in the array
| ARRAY_POSITION | NUMBER | Position within the gate array

### CTA.SERVICENOW_AVAILABILITY_EVENTS (bronze.cta_servicenow_availability_events) — 57 cols, 295,960 rows

| Column | Type | Description
|---|---|---
| SN_U_EVENT_ID | VARCHAR2 | ServiceNow event identifier
| SN_SYS_ID | VARCHAR2 | ServiceNow system ID (UUID)
| SN_U_DEVICE_TYPE | VARCHAR2 | Device type as seen in ServiceNow
| SN_U_DEVICE_ID | VARCHAR2 | Device ID as seen in ServiceNow
| AE_DEVICE_ID | NUMBER | Availability events DEVICE_ID (join key)
| AE_TRANSIT_DAY_KEY | NUMBER | Transit day key YYYYMMDD
| AE_EVENT_ID | NUMBER | FK to EDW.AVAILABILITY_EVENTS.EVENT_ID
| AE_FAULT_STATE | VARCHAR2 | Fault state from availability event
| AE_FAILURE_LEVEL | NUMBER | Ventra failure level (confirmed by Michael 2026-06-23). Hardware faults (is_device_fault=TRUE): 1=NONPAYMENT, 2=PURCHASE_CARD, 3=PURCHASE_PRODUCT, 4=ALL_PURCHASE, 5=ALL_FUNCTIONS, 16=BUS_READER_ASSEMBLY. Decoded in silver.dim_failure_level (S18). PS3 prediction target.
| AE_START_DTM | TIMESTAMP | Outage start
| AE_END_DTM | TIMESTAMP | Outage end
| AE_FAULT_DESCRIPTION | VARCHAR2 | Primary text feature for PS3 NLP
| AE_SYMPTOM | VARCHAR2 | Symptom text feature
| AE_PROBLEM | VARCHAR2 | Problem text feature
| AE_RESOLUTION | VARCHAR2 | Resolution text feature
| SN_U_AFFECTED_COMPONENT | VARCHAR2 | Component name from ServiceNow
| SN_U_WOT_STATE | VARCHAR2 | Work order task state
| SN_U_REQUEST_TYPE | VARCHAR2 | Request type (break-fix, preventive, etc.)
| EDW_INSERTED_DTM | TIMESTAMP | When CTA loaded this into EDW
| EDW_UPDATED_DTM | TIMESTAMP | Last update in EDW

### NCS_STAGE.SALE_TRANSACTION (bronze.ncs_sale_transaction) — 89 cols, 152M rows [NEW 2026-06-10]

| Column | Type | Description
|---|---|---
| DEVICE_ID | NUMBER | TVM device business key
| TRANSACTION_DTM | DATE | Transaction timestamp — truncated to derive transit_day
| TRANSACTION_STATUS_CD | NUMBER | 0 = success; non-zero = error/failed transaction
| NET_VALUE | NUMBER | Net transaction value in cents
| CASH_COLLECTED | NUMBER | Cash amount collected in cents (>0 = cash sale)
| CR_DB_AMOUNT | NUMBER | Credit/debit amount in cents (>0 = card sale)
| OPERATOR_ID | NUMBER | Operator ID
| FACID | NUMBER | Facility ID
| SALE_TRANSACTION_TYPE | VARCHAR2 | Transaction type code (TVM vs other)

**Silver output:** `silver.tvm_sale_daily` — key features: `daily_sales_count`, `error_txn_rate_pct`, `cash_sales_pct`, `total_revenue_cents`, `sales_active_hours`
**Gold use:** `gold.device_ps1_daily` — sales decline is a leading TVM failure indicator

---

### ~~CTA.CTA_REAL_TIME_BUS_DATA~~ — **REMOVED FROM PIPELINE (2026-06-12)**

This table (530M rows, bronze.cta_real_time_bus_data) and its Silver output `silver.bus_realtime_daily` have been **fully removed** from the Chicago pipeline as of 2026-06-12. Bus operational health features (`active_ratio`, `in_service_ratio`, `has_work_order`, `bus_unavailable_flag`) are no longer included in any Gold table.

---

### ~~NCS_STAGE.DEVICE_CURRENT_HW_CONFIG~~ — **REMOVED FROM PIPELINE (2026-06-12)**

This table (147,593 rows, bronze.ncs_device_current_hw_config) has been **dropped from the pipeline** as of 2026-06-12. Silver S07 `hw_config_current` now sources exclusively from `EDW.DEVICE_CURRENT_HW_CONFIG` (11,736 rows, bronze.edw_device_current_hw_config). The `REPORTED_CHANGED_DTM` installation date proxy for PS5 is obtained from the EDW table only.

---

## 2. Derived Fields Reference

### mars_device_category

Derived in `silver.dim_device` from `DEVICE_TYPE_NAME` and `DEVICE_CONTROL_GROUP_TYPE_NAME`.

| Category | DEVICE_TYPE_NAME Contains | DEVICE_CONTROL_GROUP_TYPE_NAME
|---|---|---
| TVM | `%TVM%` OR `%FMVD%` | —
| GATE | `%GATE%` OR — | `RVG` OR `HBG` OR `SAG`
| VALIDATOR | `%VALIDATOR%` OR `%BMV%` OR `%FBX%` OR `%DCU%` | —
| OTHER | none of the above | —

Evaluation order: TVM → GATE → VALIDATOR → OTHER.
**READER removed 2026-06-23** — RSV/CSC devices are COMPONENT_TYPE entries inside parent VALIDATOR/GATE devices, not standalone devices. Any residual READER-named device type maps to OTHER.

### component_subsystem

Derived in `silver.dim_event_type` from `EVENT_TYPE_ID` ranges.

| Subsystem | EVENT_TYPE_ID Range | Description
|---|---|---
| SYSTEM | 100–199 | System-level events
| CSC_READER | 200–299 | Card/Contactless reader
| SCRST | 300–399 | Coin/SCRST mechanism
| BHU | 400–499 | Bill handling unit
| CHU | 500–599 | Coin handling unit
| PIN_PAD | 800–899 | PIN pad
| PRINTER | 900–999 | Receipt/ticket printer
| GATE_MECH | 1200–1299 | Gate mechanical arm
| ALARM | 1400–1499 | Alarm/security events
| BANKCARD | 1600–1699 | Bank card reader
| DOPP | 2200–2299 | DOPP module
| COMMS | ≥50000 | Communications / network
| OTHER | all others | Uncategorised

### severity (event-level)

Derived in `silver.device_event_enriched` combining `EVENT_TYPE_DIMENSION.SEVERITY` and `EVENT_STATE_TYPE_NAME`.

```sql
CASE
  WHEN et.SEVERITY >= 3 OR de.EVENT_STATE_TYPE_NAME ILIKE '%critical%' THEN 'CRITICAL'
  WHEN et.SEVERITY = 2  OR de.EVENT_STATE_TYPE_NAME ILIKE '%warn%'     THEN 'WARN'
  ELSE 'INFO'
END
```

### is_oos_event / is_hardware_oos_event / is_commanded_oos_event

All three flags are derived in `silver.dim_event_type` (S02) from the explicit Cubic doc 9604-60007 OOS whitelist (rewritten 2026-06-16). Previous SEVERITY-based logic flagged only 7 events; the whitelist captures 60+.

**`is_oos_event`** — ALL OOS events (hardware + commanded + maintenance). 102 codes total.
```sql
et.EVENT_TYPE_ID IN (101, 106, 108, 109, 110, 113, 118, 119, 131, 132, 138, 141, 143,
    144, 151, 152, 153, 158, 171, 172, 201, 204, 205, 208, 209, 212, 220, 221, 222,
    223, 228, 229, 230, 231, 304, 305, 308--317, 401--410, 504--543, 603, 604, 611,
    1203, 1228, 1401--1409, 2102, 2201--2213, 50101, ...)
OR UPPER(EVENT_TYPE_NAME) LIKE '%OOS%'
OR UPPER(EVENT_TYPE_DESC) LIKE '%OUT OF SERVICE%'
```
**Do NOT use `is_oos_event` for PS1 failure labels** — it includes commanded/maintenance OOS.

**`is_hardware_oos_event`** — Hardware failures only. Commanded codes removed.
```sql
-- Same whitelist as is_oos_event MINUS:
--   106 (Employee Logon), 110 (Commanded OOS), 151 (Maintenance Mode),
--   208 (SCT Commanded OOS), 519 (CHU OOS by Command),
--   1603 (No Credit by Cmd), 1604 (No Debit by Cmd)
OR (UPPER(name) LIKE '%OOS%' AND EVENT_TYPE_ID NOT IN (106,110,151,208,519,1603,1604))
```
**Use `is_hardware_oos_event` for PS1 labels and silver.device_outage (S04).**

**`is_commanded_oos_event`** — Operator-triggered OOS only (not hardware failures).
```sql
et.EVENT_TYPE_ID IN (106, 110, 151, 208, 519, 1603, 1604)
```
Useful for PS3 root-cause context. Exclude from all failure label computation.

### transit_day (day key conversion)

Oracle EDW stores transit days as `NUMBER` in `YYYYMMDD` format (e.g., 20240315).

conversion:
```sql
TO_DATE(transit_day_key::text, 'YYYYMMDD')::date
```

### TIME_INCREMENT_KEY (15-min block)

Used in `EDW.DEVICE_METRIC`. Values 1–96 where each increment = 15 minutes.
Hour reconstruction:
```sql
(TIME_INCREMENT_KEY - 1) / 4   -- integer division gives 0-based hour
```
Full hour bucket reconstruction:
```sql
TO_DATE(TRANSIT_DAY_KEY::text, 'YYYYMMDD')::date
+ (((TIME_INCREMENT_KEY - 1) / 4) * INTERVAL '1 hour')
```

### component_age_days

Computed in `silver.hw_config_current` (source: `EDW.DEVICE_CURRENT_HW_CONFIG` only, 11,736 rows — `NCS_STAGE.DEVICE_CURRENT_HW_CONFIG` removed 2026-06-12):
```sql
CASE
  WHEN REPORTED_CHANGED_DTM IS NOT NULL
  THEN (CURRENT_DATE - REPORTED_CHANGED_DTM::date)
  WHEN LAST_REPORTED_DTM IS NOT NULL
  THEN (CURRENT_DATE - LAST_REPORTED_DTM::date)
  ELSE NULL
END
```

### days_to_failure (PS5 survival target)

```sql
CASE
  WHEN failure_count > 0 AND REPORTED_CHANGED_DTM IS NOT NULL
  THEN GREATEST(0,
    EXTRACT(EPOCH FROM (first_failure_dtm - REPORTED_CHANGED_DTM)) / 86400.0)
  ELSE component_age_days   -- censored
END
```

### will_fail_7d (PS1 binary target)

```sql
CASE WHEN EXISTS (
    SELECT 1 FROM silver.device_outage fo
    WHERE fo.DEVICE_ID  = device_id
      AND fo.outage_day  > transit_day
      AND fo.outage_day <= transit_day + INTERVAL '7 days'
      AND fo.severity IN ('CRITICAL','WARN')
) THEN 1 ELSE 0 END
```

### ensemble_anomaly_flag (PS4 target)

Three signals (all device types): `event_rate_anomaly`, `metric_anomaly`, `reject_rate_anomaly`.

Flag = TRUE when sum of active signals >= 2.

Note: The formerly planned fourth signal `bus_ops_anomaly` (VALIDATOR/READER) was removed when `silver.bus_realtime_daily` was dropped from the pipeline on 2026-06-12. PS4 is a 3-signal ensemble for all device types.

### sales_decline_flag (PS1 TVM feature — NEW 2026-06-10)

```sql
CASE
  WHEN 7d_prior_avg > 0
  AND daily_sales_count < 0.5 * 7d_prior_avg
  THEN TRUE ELSE FALSE
END
```
Sourced from `silver.tvm_sale_daily`. A drop below 50% of the 7-day prior average is a leading indicator of TVM degradation.

### subsystem_chain (PS2 target)

```sql
STRING_AGG(component_subsystem, '->' ORDER BY EVENT_DTM)
```
Applied to WARN/CRITICAL events within (device_id, transit_day) where count >= 2.

---

## 3. Bronze Table Name Mapping

Oracle source tables do not always map to obvious bronze table names. The loader uses the following naming convention:

| Oracle Table | Bronze Table
|---|---
| EDW.DEVICE_EVENT | bronze.device_event
| EDW.DEVICE_DIMENSION | bronze.device_dimension
| EDW.EVENT_TYPE_DIMENSION | bronze.event_type_dimension
| EDW.AVAILABILITY_EVENTS | bronze.availability_events
| EDW.AVAILABILITY_PERIODS | bronze.availability_periods
| EDW.AVAILABILITY_RELIEF | bronze.availability_relief
| EDW.DEVICE_LAST_STATE | bronze.device_last_state
| EDW.DEVICE_METRIC | bronze.device_metric
| EDW.METRIC_DIMENSION | bronze.metric_dimension
| EDW.METRIC_SUMMARY_BY_DAY | bronze.metric_summary_by_day
| EDW.KPI | bronze.kpi
| EDW.KPI_RULES | bronze.kpi_rules
| EDW.KPI_TARGET | bronze.kpi_target
| EDW.KPI_DETAIL_EVENTS_BY_DAY | bronze.kpi_detail_events_by_day
| EDW.KPI_SUMMARY_BY_DAY | bronze.kpi_summary_by_day
| EDW.DEVICE_CURRENT_HW_CONFIG | bronze.edw_device_current_hw_config
| EDW.DEVICE_CURRENT_SW_CONFIG | bronze.device_current_sw_config
| EDW.DEVICE_CURRENT_TABLES | bronze.device_current_tables
| EDW.DEVICE_LOCATION_HISTORY | bronze.edw_device_location_history
| EDW.DEVICE_LAST_SET_EVENT | bronze.device_last_set_event
| EDW.ABP_TAP | bronze.abp_tap
| EDW.DATE_DIMENSION | bronze.date_dimension
| EDW.LATE_TRANSACTION_DETAIL | bronze.late_transaction_detail
| NCS_STAGE.DEVICE | bronze.ncs_device
| NCS_STAGE.DEVICE_CONTROL_GROUP | bronze.ncs_device_control_group
| NCS_STAGE.DEVICE_CONTROL_GROUP_TYPE | bronze.ncs_device_control_group_type
| ~~NCS_STAGE.DEVICE_CURRENT_HW_CONFIG~~ | ~~bronze.ncs_device_current_hw_config~~ **REMOVED 2026-06-12**
| NCS_STAGE.DEVICE_END_OF_DAY | bronze.ncs_device_end_of_day
| NCS_STAGE.DEVICE_END_OF_DAY_MSG_COUNT | bronze.ncs_device_end_of_day_msg_count
| NCS_STAGE.CASHBOX_TRACKING | bronze.ncs_cashbox_tracking
| NCS_STAGE.CASHBOX_INVENTORY | bronze.ncs_cashbox_inventory
| NCS_STAGE.DAILY_CASHBOX_DETAIL | bronze.ncs_daily_cashbox_detail
| NCS_STAGE.EVENT | bronze.ncs_event
| NCS_STAGE.STOP_POINT | bronze.ncs_stop_point
| NCS_STAGE.ACTIVITY_CODE | bronze.ncs_activity_code
| NCS_STAGE.COMPONENT_TYPE | bronze.ncs_component_type
| CTA.SERVICENOW_AVAILABILITY_EVENTS | bronze.cta_servicenow_availability_events
| CTA.SERVICENOW_DATA_FROM_JUMPBOX | bronze.cta_servicenow_data_from_jumpbox
| CTA.ABP_USE_TRAN_TIMING_DATA | bronze.cta_abp_use_tran_timing_data
| CTA.KPI_AGENCY_MAP | bronze.cta_kpi_agency_map
| CTA.KPI_TVM_DATE_TABLE | bronze.cta_kpi_tvm_date_table
| CTA.SLDC_MONTHLY_SUMMARY | bronze.cta_sldc_monthly_summary
| NCS_STAGE.SALE_TRANSACTION | bronze.ncs_sale_transaction
| ~~CTA.CTA_REAL_TIME_BUS_DATA~~ | ~~bronze.cta_real_time_bus_data~~ **REMOVED 2026-06-12**

---

## 4. Key Business Rules

### De-duplication
`EDW.DEVICE_EVENT` has duplicate `DW_DEVICE_EVENT_ID` values due to EDW incremental loads. De-dup logic in `silver.device_event_enriched`:
```sql
ROW_NUMBER() OVER (PARTITION BY DW_DEVICE_EVENT_ID ORDER BY EDW_UPDATED_DTM DESC) = 1
```

### GATE BCR Exclusion (not applied here)
In the TOC project (London), BCR-type gate events were excluded from PS1. For Chicago, no equivalent exclusion flag exists in the schema; `EXCLUDED` flag in `EDW.AVAILABILITY_EVENTS` should be applied when filtering availability events for KPI compliance.

### CURRENT_FLAG
`EDW.DEVICE_DIMENSION.CURRENT_FLAG = 1` identifies the current version of the SCD2 device record. All silver and gold tables filter on `is_current = TRUE` when joining device context.

### Metric Key Linkage
`EDW.DEVICE_METRIC.METRIC_KEY` is a surrogate; `EDW.METRIC_DIMENSION.METRIC_ID` is the business key. Always join on `METRIC_KEY` and filter by `METRIC_ID` for specific metric extraction.

### Transit Day vs. Posting Day
`EDW.DEVICE_EVENT` has both `EVENT_DAY_KEY` (the actual day the event occurred) and `POSTING_DAY_KEY` (the day EDW recorded the event). For feature engineering, use `EVENT_DAY_KEY` for behavioural features and `POSTING_DAY_KEY` only for load-lag analysis.
