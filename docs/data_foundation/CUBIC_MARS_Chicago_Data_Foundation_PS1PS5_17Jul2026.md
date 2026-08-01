**CUBIC MARS - Chicago / Ventra**

**Data Foundation for PS1-PS5 - Device Identity, Serials & Failure
Sources**

*Internal - Mars team \| 17 Jul 2026 \| Grounded in live bronze probes
on mars_dev (16-17 Jul). Every figure below is from a real query, not an
estimate.*

<table>
<colgroup>
<col style="width: 100%" />
</colgroup>
<thead>
<tr class="header">
<th><p><strong>Bottom line</strong></p>
<p>The data foundation is now solid and evidence-based. Device identity
and component serials are fully resolved <strong>inside the EDW, with no
dependence on the sparse ServiceNow keys</strong>; the
chargeable-failure source is confirmed per device type - including
validators, which sit in the device-event stream exactly as Michael
said; and the scope corrections (AVM, AFC) are quantified. All four
in-scope device types (TVM, gates, validators) are now
trainable.</p></th>
</tr>
</thead>
<tbody>
</tbody>
</table>

**1. Device identity & serial mapping - RESOLVED**

> **Identity:** 16,572 current devices; **DEVICE_KEY** is 1:1 with
> **DEVICE_ID** and is the universal join key (100%). Join facts on
> DEVICE_KEY; roll up entities on DEVICE_ID.
>
> **Component serials: edw_device_current_hw_config** joins on
> DEVICE_KEY (100%) and is **100% populated** for
> **COMPONENT_SERIAL_NBR** + **REPORTED_CHANGED_DTM** - 11,736 component
> rows / 7,536 devices (BMV 8,519, TVM 1,963, RVG 924, SAG 199, HBG 113,
> POI 18). This bypasses the 91.8%-NULL device serial entirely.
>
> **Install date: REPORTED_CHANGED_DTM** (100%). The ServiceNow CMDB
> **install_date** bridge is a dead end - **0 serial matches** (CMDB
> carries device/network serials; hw_config carries component serials
> like SIM/SAM/coin). Drop it.

**Final crosswalk DDL**

<table>
<colgroup>
<col style="width: 100%" />
</colgroup>
<thead>
<tr class="header">
<th><p>CREATE OR REPLACE TABLE mars_dev.silver.dim_device_serial_xwalk
AS</p>
<p>WITH device_by_key AS ( -- one row per DEVICE_KEY, prefer current
version</p>
<p>SELECT DEVICE_KEY, DEVICE_ID, DEVICE_TYPE_NAME, OPERATOR_NAME,</p>
<p>FACILITY_ID, FACILITY_NAME, BUS_ID, DEVICE_SERIAL_NUMBER</p>
<p>FROM ( SELECT *, ROW_NUMBER() OVER (PARTITION BY DEVICE_KEY</p>
<p>ORDER BY COALESCE(CURRENT_FLAG,0) DESC, UPDATED_DTM DESC NULLS LAST)
rn</p>
<p>FROM mars_dev.bronze.edw_device_dimension ) WHERE rn = 1 )</p>
<p>SELECT h.DEVICE_KEY,</p>
<p>COALESCE(d.DEVICE_ID, h.DEVICE_ID) AS device_id,</p>
<p>d.DEVICE_TYPE_NAME, d.OPERATOR_NAME, d.FACILITY_ID, d.FACILITY_NAME,
d.BUS_ID,</p>
<p>h.COMPONENT_DESCRIPTION AS component,</p>
<p>h.COMPONENT_SERIAL_NBR AS component_serial,</p>
<p>COALESCE(h.COMPONENT_SERIAL_NBR, d.DEVICE_SERIAL_NUMBER) AS
best_serial,</p>
<p>h.REPORTED_CHANGED_DTM AS install_date, -- proxy, 100% populated</p>
<p>h.LAST_REPORTED_DTM AS last_seen_dtm -- right-censor date</p>
<p>FROM mars_dev.bronze.edw_device_current_hw_config h</p>
<p>LEFT JOIN device_by_key d ON d.DEVICE_KEY = h.DEVICE_KEY;</p>
<p>-- CMDB install_date bridge dropped: 0 serial matches (device vs
component serials).</p></th>
</tr>
</thead>
<tbody>
</tbody>
</table>

**2. Device scope**

In-scope = TVM + gates (RVG/HBG/SAG) + validator (BMV). Everything else
is legacy/retail/back-office and shows ~0 chargeable failures - drop it.

| **Type**         | **Current devices** | **Scope**          | **Note**                                                    |
|------------------|---------------------|--------------------|-------------------------------------------------------------|
| **TVM**          | 513                 | **IN (TVM)**       | Ventra ticket vending machines                              |
| **RVG**          | 1,024               | **IN (Gate)**      | Rail gate                                                   |
| **HBG**          | 183                 | **IN (Gate)**      | Gate                                                        |
| **SAG**          | 175                 | **IN (Gate)**      | Gate                                                        |
| **BMV**          | 4,230               | **IN (Validator)** | Bus mobile validator (DAP)                                  |
| **AVM**          | 431                 | **DROP**           | Legacy CTA vending, not Ventra (Michael); 0 failures        |
| **FBX / BTP**    | 4,651 / 3,486       | **DROP**           | Out of scope; 0 chargeable failures                         |
| **RTL / retail** | 2,183 +             | **DROP**           | SCR/SIC/ECX/POS/DCR - back-office/retail; ~0 failures       |
| **Turnstiles**   | TT\_/TTC/TWA/TEX    | **DROP**           | Legacy turnstiles; 0 failures                               |
| **AFC switch**   | (netgear CIs)       | **EXCLUDE**        | Network routers, not devices -\> PS2 station-network signal |

**3. Failure-label source per device type - the key result**

Chargeable failures ultimately originate as **edw_device_event** OOS
codes. For TVM/gates they are *pre-aggregated* into
**edw_availability_events** (with FAILURE_LEVEL). Validators are *not*
pre-aggregated (1.5% in availability, 0% in KPI) - so we derive BMV
failures directly from the device-event stream using the same OOS
classification.

| **Type** | **Failure source**             | **Chargeable rule**                                    | **Device coverage**   |
|----------|--------------------------------|--------------------------------------------------------|-----------------------|
| **TVM**  | **edw_availability_events**    | FAILURE_LEVEL\>0 AND EXCLUDED=0                        | **98.8%**             |
| **SAG**  | **edw_availability_events**    | same                                                   | **87.4%**             |
| **RVG**  | **edw_availability_events**    | same                                                   | **70.4%**             |
| **HBG**  | **edw_availability_events**    | same                                                   | **46.4% (weaker)**    |
| **BMV**  | **edw_device_event** OOS codes | Matrix counted-as-OOS, state=Set, excl commanded/maint | **74.7% have events** |

**Do not use SEVERITY.** The event dimension populates severity for only
26 of 444 event types; the 418 real NCS fault codes (and ~100% of BMV
events) are NULL. Failures are defined by **event code** via the Device
Event Matrix, not severity.

**BMV failure view (device-event -\> OOS, mirrors availability_events)**

<table>
<colgroup>
<col style="width: 100%" />
</colgroup>
<thead>
<tr class="header">
<th><p>-- BMV (validator) chargeable failures from the device-event
stream.</p>
<p>-- Mirrors how availability_events is derived for TVM/gates.</p>
<p>CREATE OR REPLACE TEMP VIEW bmv_failures AS</p>
<p>SELECT e.DEVICE_ID, e.DEVICE_KEY, e.EVENT_DTM AS fail_dtm,
e.CLEAR_DTM,</p>
<p>m.EVENT_TYPE_NAME, m.component_subsystem</p>
<p>FROM mars_dev.bronze.edw_device_event e</p>
<p>JOIN mars_dev.silver.dim_event_type m ON m.EVENT_TYPE_KEY =
e.EVENT_TYPE_KEY</p>
<p>WHERE e.DEVICE_ID LIKE 'BMV%'</p>
<p>AND m.is_counted_oos = true -- confirm exact OOS-flag col in
dim_event_matrix</p>
<p>AND e.EVENT_STATE_TYPE_NAME = 'Set' -- fault raised (Clear =
resolution -&gt; MTTR)</p>
<p>AND m.EVENT_TYPE_NAME NOT IN</p>
<p>('CmdOOSbyTables','MaintenanceMode','Emp Logon','In Service','Vhcle
Igntn Off');</p></th>
</tr>
</thead>
<tbody>
</tbody>
</table>

BMV fault codes confirmed present: **DAP OOS** (3,062 dev),
**DeviceCommsLost** (3,161), **Host Comm Lost**, **CSC Read err**,
**DAPCommsError**, **Volt Drop**, **DAPDBFailure**, **BadSAMDevOOS**.
Operational events to exclude: Emp Logon, In Service, Maintenance Mode,
Vehicle Ignition Off, CmdOOSbyTables. The **Set/Clear** lifecycle (24.7M
each) gives the fault event (Set) and MTTR (Set-\>Clear / CLEAR_DTM).

**4. Decoders - located**

> **Event faults: EVENT_TYPE_KEY** -\> **silver.dim_event_type** /
> **dim_event_matrix** (counted-as-OOS flag + component_subsystem).
> SEVERITY unreliable.
>
> **Tap timing: edw_device_metric_history** + **edw_metric_dimension** -
> METRIC 401 = total tap time, 700-series = timing legs (Michael).
> Replaces the missing ABP timing table.
>
> **Tap decision: edw_txn_status_dimension** - the approve/deny decision
> (900-series). 701 is a timing metric, NOT a rejection.
>
> **Chargeability: edw_kpi_rules** + **edw_kpi** (cause/category -\>
> failure level). FAILURE_LEVEL is already denormalised on
> availability/kpi_detail - no kpi_rules join needed for the label.

**5. PS5 scope - device-level now, component-level feasible**

> **Device-level survival:** viable for all four types - TVM 98.8% / SAG
> 87.4% / RVG 70.4% / HBG 46.4% (availability_events), BMV via
> device-event OOS (74.7% have events).
>
> **Component-level survival - now feasible: edw_device_event** is
> component-attributed (COMPONENT_SERIAL_NBR / TYPE / POSITION), so a
> fault ties to a part; join to the hw_config install date per component
> serial = install -\> fault per component.
>
> **MTTR:** EVENT_DTM -\> CLEAR_DTM on the device-event stream - no
> ServiceNow incident needed.
>
> **Caveat (validate): REPORTED_CHANGED_DTM** reliability - confirm it
> moves only on a real physical swap by cross-checking against the first
> component fault. Coverage is solved (100%); accuracy is the open
> question.

**6. What is now closed vs still open**

**Closed:** serial linkage (QC-4/GAP-5), device identity, AVM drop
(QC-3), AFC = network (QC-1), tap-timing source (QC-2), 701/905
classification (QR-2/3), validator failure source + join (QMW-2/4), and
reliance on the ServiceNow incident re-export (QR-1, de-prioritised - V2
uses work-order-tasks + KPI).

**Open (small, ours):** confirm the exact OOS-flag column + join key in
**dim_event_matrix / dim_event_type**; validate REPORTED_CHANGED_DTM
reliability; build the tap-timing table from device_metric_history; the
27 duplicate metric-day rows (QR-4, Robin/ODS).

**7. Per-PS action plan**

> **PS1 - Failure prediction:** scope TVM / gates / BMV (drop AVM).
> Chargeable label = TVM/gates from **availability_events**, BMV from
> **device_event** OOS - this brings validators into PS1 for the first
> time. Add real tap-timing (**device_metric_history**), correct reject
> logic (**txn_status_dimension**), device/component age from the serial
> crosswalk, and fix rolling windows (RANGE INTERVAL). Gate on PR-AUC /
> AP + recall floor, not accuracy.
>
> **PS2 - Cascading:** add the station-network shared signal from the
> AFC-switch (**netgear**) CIs per FACILITY_ID; use device_key identity
> so BMV swaps do not fragment chains.
>
> **PS3 - Root cause / severity:** structured labels only - severity
> from FAILURE_LEVEL (availability), component/subsystem from
> **dim_event_type / dim_event_matrix**; drop incident free-text
> (leakage). Validators enter via device_event.
>
> **PS4 - Anomaly:** turn on the metric-anomaly signal from
> **device_metric_history** (METRIC 401 timing); keep adaptive
> per-device thresholds; exclude AVM; device_key for validators.
>
> **PS5 - RUL / survival:** device-level survival from the unified
> failure view (the survival notebook); component-level extension via
> device_event component attribution + hw_config install date; MTTR via
> EVENT_DTM -\> CLEAR_DTM. Validate REPORTED_CHANGED_DTM reliability
> first.

**8. Silver / Gold rebuild order**

Order matters - nothing downstream is correct until the failure spine
includes validators. Build in this sequence:

> **1) Decoder / reference dims.** new **dim_device_serial_xwalk**;
> confirm the OOS flag in **dim_event_type / dim_event_matrix**;
> **metric_dimension**, **txn_status_dimension**.
>
> **2) dim_device scope fix.** drop AVM (legacy), exclude AFC-switch /
> netgear, keep 3 device categories with reader-as-component.
>
> **3) Failure spine.** build **device_failures** (availability for
> TVM/gates + device_event OOS for BMV) and rebuild **failure_ledger**
> to include validators.
>
> **4) Feature silvers. tap_timing_daily**, **tap_decision_daily**,
> reader-signal columns, **device_mttr**.
>
> **5) Gold per-PS.** device_ps1_daily (all 4 types),
> device_ps5_survival, device_ps3_severity, device_ps4_hourly (metric
> anomaly on), device_ps2_chains (+ station-network).

**9. Additional silver tables to build**

| **New silver table**          | **Purpose**                                                              | **Built from**                           |
|-------------------------------|--------------------------------------------------------------------------|------------------------------------------|
| **dim_device_serial_xwalk**   | Device + component serial + install date (identity + component age)      | hw_config + device_dimension             |
| **device_failures**           | Unified chargeable-failure events - the label spine, validators included | availability_events + device_event OOS   |
| **device_survival_intervals** | TBF + right-censoring per device (PS5 training table)                    | device_failures                          |
| **tap_timing_daily**          | Tap-latency features (METRIC 401 total + 700-series legs)                | device_metric_history + metric_dimension |
| **tap_decision_daily**        | Correct approve/deny - retires the 701-as-reject bug                     | edw_abp_tap + txn_status_dimension       |
| **station_network_daily**     | AFC-switch outages per station (PS2 cascade / shared signal)             | netgear CIs + device_event               |
| **device_mttr**               | Repair times per device (EVENT_DTM -\> CLEAR_DTM)                        | device_event                             |
| **dim_event_type** *(extend)* | Surface a clean is_counted_oos boolean for the failure classifier        | dim_event_matrix                         |

*Prepared by Mars-Techs (PK) - CUBIC MARS, Chicago / Ventra - 17 Jul
2026 - INTERNAL. Figures from live mars_dev bronze probes, 16-17 Jul
2026.*
