# CUBIC MARS Chicago — Canonical Hardware-OOS Event Contract

**Status:** AUTHORITATIVE. This document overrides any skill, solution document, notebook
comment, or DDL comment that contradicts it.
**Version:** `2026-08-03.v3`
**Scope:** Chicago / CTA-Ventra, PS1–PS5, all device categories.
**Ruling by:** PK, 2026-08-03.

---

## 1. The ruling in one sentence

> A hardware-OOS `Set` event **is** the failure. The device is out of service at that
> instant. There is no separate "failure" concept to reconcile against it.

Everything below follows from that.

---

## 2. The canonical predicate

Every problem statement that needs a failure/OOS event MUST derive it from exactly this:

```sql
FROM   mars_dev.silver.device_event_enriched          -- S16, SILVER layer
WHERE  is_hardware_oos_event = TRUE                   -- PRIMARY FLAG
  AND  UPPER(TRIM(EVENT_STATE_TYPE_NAME)) = 'SET'     -- STATE GATE
  AND  <device is in scope: dim_device.is_current = TRUE>
```

**Two clauses. Nothing else.** Any additional filter narrows the event set below PS1 and
must be justified in writing at the point of use.

### 2.0 LAYER AND PHYSICAL LOCATION — read this before anything else

`device_event_enriched` is a **SILVER** table. Failure and OOS truth for this programme
lives in **silver**, never in bronze and never in gold.

It is physically stored **under the GOLD bucket**, at a `chicago/silver/` prefix:

```
s3://cubic-mars-pm-s3-datalake-dev-gold-170202974600/chicago/silver/device_event_enriched/
                                  ^^^^                       ^^^^^^
                                  BUCKET name says gold      LAYER is silver
```

**The bucket name is not the layer.** Anyone reading the path alone will mis-classify this
as a gold table. It is silver. The gold bucket is simply where the silver prefix is hosted.

Two access paths exist, and both resolve to the same silver table:

| context | how it is read |
|---|---|
| Databricks / Unity Catalog | `spark.table("mars_dev.silver.device_event_enriched")` |
| SageMaker / direct S3 | `spark.read.parquet("s3://<GOLD_BUCKET>/chicago/silver/device_event_enriched/")` |

PS5's resolver makes the mapping explicit — `ps5_reliability_engine_v51.py`:

```python
def _puri(t):
    p = t.split("."); return f"s3://{CONFIG['GOLD_BUCKET']}/chicago/{p[-2]}/{p[-1]}"
```

so `mars_dev.silver.device_event_enriched` → `.../chicago/silver/device_event_enriched`.
PS1 does the same via `BUCKET = GOLD_BUCKET` and `S3_SILVER_RUNTIME`.

Either path is compliant. Referring to this table as a *gold* asset is not.

### 2.1 What is already excluded at source — do NOT re-filter

`is_hardware_oos_event` is defined in `dim_event_type` (S07) and **already excludes**
commanded and maintenance event codes `106 / 110 / 151 / 208 / 519 / 1603 / 1604`.
Re-applying `is_commanded_oos_event` / `is_maintenance_oos` is harmless belt-and-braces,
but it is not required and must never be presented as the primary control.

### 2.2 Filters that are FORBIDDEN without a written exception

| forbidden filter | why |
|---|---|
| `is_device_fault = TRUE` | The set is already device faults by definition. Only drops true failures. |
| `failure_level IN (1,2,3,4,5,16)` | Same. PS1 applies no failure_level predicate. |
| `is_chargeable = TRUE` | Chargeable is an SLA **contract classification applied after** the physical event. It is a strict SUBSET of OOS. |
| `FAILURE_LEVEL > 0 AND EXCLUDED = 0` | This IS the chargeable definition, sourced from `edw_availability_events`. Not a hardware-OOS source. |
| `duration_to_clear_min IS NOT NULL` | Drops confirmed failures whose recovery is unknown — i.e. the most recent and most severe. The failure TIME (the Set) is always known; whether it cleared is a different question. |

### 2.3 Sources that are NOT valid hardware-OOS sources

- `silver.device_failures` — TVM/GATE rows come from `edw_availability_events`
  (`FAILURE_LEVEL>0 AND EXCLUDED=0`), i.e. chargeable. Only its VALIDATOR rows are
  genuine hardware-OOS. Its materialised columns drop `fault_state` /
  `is_hardware_oos_event` / `is_chargeable`, so the definition cannot be re-derived at runtime.
- `silver.kpi_avail_enriched` — availability/chargeable events.
- `bronze.edw_availability_events` — as above.

Chargeable figures may be carried **alongside** OOS for analysis
(`total_chargeable_events`, `chargeable_pct`). They must never define the event population.

---

## 3. The contract table — PS1 to PS5

| | **Source table** | **Primary flag** | **State gate** | **Episode grain** | **Status** |
|---|---|---|---|---|---|
| **PS1** Failure prediction | `silver.device_event_enriched` | `is_hardware_oos_event = TRUE` | `EVENT_STATE_TYPE_NAME = 'Set'` | **calendar day** — `DISTINCT(DEVICE_ID, failure_date)` | ✅ REFERENCE |
| **PS2** Cascading failure | `silver.device_event_enriched` | `is_hardware_oos_event = TRUE` | `UPPER(TRIM(EVENT_STATE_TYPE_NAME)) = 'SET'` | **governed episode** — `outage_episode_id` + `is_primary_hardware_oos_onset` | ✅ ALIGNED |
| **PS3** Root cause & severity — **TARGET** | `silver.device_event_enriched` | `is_hardware_oos_event = TRUE` | `UPPER(TRIM(EVENT_STATE_TYPE_NAME)) = 'SET'` | **calendar day** — `DISTINCT(DEVICE_ID, failure_date)`, identical to PS1 | 🔧 **TO BUILD** |
| ~~PS3 — current~~ | ~~`silver.incident_root_cause`~~ | ~~*none*~~ | ~~*none* — `FAILURE_LEVEL>0 AND EXCLUDED=0`~~ | ~~raw `availability_event_id`~~ | ❌ **NON-COMPLIANT — retire** |
| **PS4** Anomaly detection | `gold.device_ps4_hourly` | *n/a — not an OOS consumer* | *n/a* | device-hour → day rollup | ⚪ OUT OF SCOPE (see §5) |
| **PS5** Reliability / RUL | `silver.device_event_enriched` | `is_hardware_oos_event = TRUE` | `EVENT_STATE_TYPE_NAME` \| `fault_state` = `'Set'` | **calendar day** — `FAILURE_EPISODE_DEDUP='day'` | ✅ ALIGNED as of `2026-08-03.v3` |

### 3.1 Episode grain differs BY DESIGN — and must be declared

Grain is the one dimension where PS1–PS5 legitimately differ, because they answer
different questions:

- **calendar day** (PS1, PS5) — "did this device fail on this day" / "how long between failures"
- **governed episode** (PS2) — "how many distinct outages, and how do they overlap"

This is not drift. But every PS **must state its grain explicitly** in its config, and must
never silently change it. Counts will not reconcile across grains, and that is expected.

### 3.2 Column-name note

PS1 uses `EVENT_STATE_TYPE_NAME` with a **case-sensitive, untrimmed** comparison
(`== "Set"`). PS2 uses `UPPER(TRIM(...)) == 'SET'`. **PS2's form is the standard** — it is
robust to whitespace and casing drift in the source. PS5 accepts either column name via an
ordered fallback (`EVENT_STATE_TYPE_NAME` → `fault_state`), which is acceptable because it
is logged.

New code MUST use the `UPPER(TRIM(...))` form.

---

## 4. PS5 — change applied 2026-08-03

`notebooks/ps5_reliability_survival/ps5_reliability_engine_v51.py`

```python
"require_device_fault": False,      # was True
"EVENT_DEF_VERSION": "2026-08-03.v3"
```

Effective predicate now:

| | value |
|---|---|
| `require_oos` | `True` |
| `fault_state_set_value` | `"Set"` |
| `require_device_fault` | **`False`** ← changed |
| `require_chargeable` | `False` |
| `exclude_commanded_oos` | `True` |
| `exclude_maintenance_oos` | `True` |
| `min_outage_min` | `0` |

**Consequence:** PS5's event set widens to equal PS1's. Every published PS5 metric will
move. Re-run is mandatory before any PS5 number is shown.

### 4.1 PS5 spine builder — DEPRECATED

`notebooks/ps5_reliability_survival/PS5_OOS_Spine_and_Serial_Builder.ipynb` is
**non-compliant and must not be run**:

- GATE is sourced from `kpi_avail_enriched` with `FAILURE_LEVEL>0 AND EXCLUDED=0` —
  the chargeable subset — while TVM/VALIDATOR use `device_event_enriched`. Two fleets,
  two definitions, one notebook.
- No `Set` state gate anywhere (0 occurrences of `EVENT_STATE_TYPE_NAME`, `fault_state`, or `'Set'`).
- `OPS_EXCLUDE` is declared and never applied.
- `duration_to_clear_min IS NOT NULL` drops unclosed episodes.

It writes only to local scratch (`PS5_oos_spine_outputs`) and **the PS5 engine does not
read it** — verified: zero references. No published PS5 result is contaminated. Mark it
deprecated in cell 0 so it is not picked up later.

---

## 5. PS4 — correctly out of scope, separately broken

PS4 does not consume the OOS event, and should not: it detects anomalies and outliers.
**That exclusion is correct and is not a compliance gap.**

PS4 has an unrelated open defect, recorded here so it is not lost:
its label is `signal_active_count >= 2` — a deterministic function of its own input
features. Tree models reconstruct it exactly (test AP 0.9994 vs 0.9367 for logistic
regression; that gap is the tell). Base rate 0.4796 (17.6M of 36.6M device-hours).
The PS4 loader already refuses to serve it.

The repo's own proposed remedy is to retarget PS4 at **hardware OOS in the next 24h** —
which would bring PS4 onto this contract. Not yet implemented. Decide separately.

---

## 6. PS3 — rework required

### 6.1 What is wrong

PS3 sits on ServiceNow availability events (`FAILURE_LEVEL>0 AND EXCLUDED=0`), i.e. the
**chargeable subset**, with no OOS flag and no state gate. Consequences:

- **VALIDATOR coverage is zero, by construction.** Validators do not generate availability
  events. The PS3 feed has 0 validator rows and always will.
- PS3 counts a different population from PS1/PS2/PS5, so nothing reconciles.

### 6.2 The event fix — PS3 target state, binding

PS3's event definition is hereby **identical to PS1's**, with `device_category` as a
parameter. Not "similar to", not "aligned with" — identical.

| | **PS3 target — BINDING** |
|---|---|
| **Source** | `mars_dev.silver.device_event_enriched` (SILVER layer; see §2.0 for the physical path) |
| **Primary flag** | `is_hardware_oos_event = TRUE` |
| **State gate** | `UPPER(TRIM(EVENT_STATE_TYPE_NAME)) = 'SET'` |
| **Device scope** | `dim_device.is_current = TRUE` |
| **Episode grain** | **calendar day** — `DISTINCT(DEVICE_ID, failure_date)` |
| **Fleet scope** | `mars_device_category = <parameter>` — TVM, GATE, VALIDATOR |
| **Chargeable filter** | none |
| **failure_level / is_device_fault filter** | none |
| **duration filter** | none |

Reference implementation to port verbatim — `attach_hardware_oos_label()`, byte-identical
across the TVM/GATE/VALIDATOR PS1 notebooks (4,010 chars, md5 `c67f46424fdb`):

```python
failure_days = (
    dee_raw.alias("dee")
    .join(F.broadcast(current_devices).alias("d_cur"),
          F.col(f"dee.{dee_dk}") == F.col("d_cur.DEVICE_KEY"))
    .where(F.col(f"dee.{dee_hw_oos}") == True)          # is_hardware_oos_event
    .where(F.col(f"dee.{dee_state}") == "Set")          # EVENT_STATE_TYPE_NAME  <-- use UPPER(TRIM(...)) per §3.2
    .where(F.col(f"dee.{dee_cat}") == device_category)  # fleet is a PARAMETER
    .select(F.col(f"dee.{dee_dev}").alias("DEVICE_ID"),
            F.to_date(F.col(f"dee.{dee_dtm}")).alias("failure_date"))
    .distinct()                                          # <-- CALENDAR-DAY EPISODE GRAIN
)
```

There is no "validator approach" and no separate "gate/TVM approach". There is one
approach, parameterised by fleet.

**Why calendar day for PS3:** it matches PS1 exactly, so a PS3 root-cause row joins 1:1 to
a PS1 failure-day row on `(DEVICE_ID, failure_date)`. That join is the whole point of
alignment — it is what lets "PS1 says this device will fail" and "PS3 says this is why"
refer to the same event. Governed-episode grain (PS2) would not join cleanly.

**Consequence — this is the win:** VALIDATOR gains PS3 coverage for the first time.
Validators generate no availability events, so the current feed has 0 validator rows
by construction. On `device_event_enriched` they are first-class.

### 6.3 The label problem — OPEN, needs a decision

Fixing the event population does **not** give PS3 its targets. PS3 predicts
`failure_level_label` and `derived_component_type`; both come from the ServiceNow incident
record. Move PS3 onto OOS events and validators gain events while still having no
root-cause labels.

Three options, to be decided before code moves:

1. **Join incidents onto OOS episodes where they exist**; treat the remainder as
   unlabelled. Preserves the SN taxonomy; leaves validators mostly unlabelled.
2. **Keep incidents as the label source, redefine the event population as OOS.**
   Same taxonomy, wider population, explicit missingness.
3. **Derive severity from OOS characteristics** (duration, subsystem, array size) instead
   of the SN taxonomy. Covers validators; abandons the established labels.

### 6.4 Notebook structure

**One shared label/spine module + three thin per-fleet training notebooks.**

- The spine MUST NOT be duplicated per fleet. Duplication is how drift happens: there are
  currently 12 PS1 notebook copies, each carrying its own copy of a function that must
  stay byte-identical to remain correct.
- Model training SHOULD be per fleet — root-cause classes genuinely differ (TVM has 11:
  `ALARM, BANKCARD, BHU, CHU, COMMS, CSC_READER, OTHER, PRINTER, SCRST, SYSTEM`), and
  validators may need a different target entirely.

Pattern to follow: `ps5_reliability_engine_v51.py` + its notebook.

---

## 7. Compliance checklist for any new or changed PS code

- [ ] Source is `silver.device_event_enriched`
- [ ] `is_hardware_oos_event = TRUE`
- [ ] `UPPER(TRIM(EVENT_STATE_TYPE_NAME)) = 'SET'`
- [ ] Device scope via `dim_device.is_current = TRUE`
- [ ] No chargeable filter
- [ ] No `is_device_fault` / `failure_level` filter
- [ ] No `duration_to_clear_min IS NOT NULL` filter
- [ ] Episode grain declared explicitly in config
- [ ] `EVENT_DEF_VERSION` stamped into every output row
- [ ] Any deviation carries a written reason at the point of use

---

## 8. Provenance

Derived by direct source inspection on 2026-08-03:

- **PS1** — `PS1_3d_{VALIDATOR,GATE,TVM}_SageMaker_MLflow_FeatureStore` (2026-08-03 downloads).
  `attach_hardware_oos_label()` verified byte-identical across all three, md5 `c67f46424fdb`.
- **PS2** — `PS2_Failure_Patterns_v2_5_4_Union_Minutes`, PRODUCTION run
  `6a705387-f3ab-4a80-95cb-9b5583d32179`, loaded to Aurora 2026-08-03.
- **PS3** — reconstructed from `sql/15_phase2a_ps3_two_head.sql`, `sql/19_ps3_severity_collapse_fix.sql`,
  `sql/load/ps3_run_20260726.sql`, and the validator stub. **The PS3 notebook is not in the
  repo** — `PS3_v2_OOS_Serial_SageMaker.ipynb` must be obtained to confirm verbatim.
- **PS4** — `notebooks/ps4/ps4_device_daily_export.py` (2026-07-27, authoritative),
  `sql/25_ps4_scored.sql`, `sql/33_ps4_device_daily.sql`.
- **PS5** — `ps5_reliability_engine_v51.py`, read in full and amended.

**Known gaps in this document:** `sql/gold/device_ps1_daily__create.sql` and the S07
`dim_event_type` DDL are not in the repo. PS1's row is reconstructed from its notebooks
and PS2's parity re-implementation. Obtain both to close.
