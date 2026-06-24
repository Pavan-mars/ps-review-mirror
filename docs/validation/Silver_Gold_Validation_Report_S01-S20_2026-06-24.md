# Silver + Gold Validation Report — canonical S01–S20 (post‑Round‑2 rework)

**Repo:** SathishMars/Chicago‑Ventra‑Mars‑Cubic‑Analysis · **Validated commit:** `a973674` "feat(silver/gold): canonical S01‑S20 reorder + 3 new tables + gold DDL update" (2026‑06‑24)
**Scope:** 20 silver CREATE scripts (`sql/silver/01..20`) + 6 gold scripts (`sql/gold/device_ps{1..5}` + `ps1 label_compare`)
**Method:** full read of all 26 scripts, validated against the Round‑2 Cubic‑validated rules (PS1 `failure_level>0`; PS3 target = `FAILURE_LEVEL`; READER = 200‑series component; GATE = RVG/HBG/SAG; maintenance = events 151/106), governed‑bronze sourcing, SCD2 `is_current` guards, grain/fan‑out, and PS scope.

---

## 1. Executive summary

The Round‑2 rework is **real and substantial** — most of the data‑model and governance work is done. What remains is a tight, mostly one‑line‑each punch‑list of **label and scope wiring** that didn't make it into this commit.

| Layer | PASS | PASS‑WITH‑NOTES | NEEDS‑FIX |
|---|---|---|---|
| Silver (20) | 6 | 10 | **4** — S02, S06, S10, S15 |
| Gold (6) | 1 | 1 | **4** — PS1, PS2, PS4, PS5 |

**What the rework genuinely fixed (verified):**
- **Zero raw‑parquet reads in silver** — every table reads governed `mars_dev.bronze.{edw_|ncs_stage_|cta_}…`. The old `parquet.\`s3://…\`` paths are gone.
- **`15_incident_history` promoted design→runnable** with both prior blockers fixed: governed bronze sources + `SPLIT_PART` (Postgres) replaced by Spark `split()[idx]`.
- **`device_outage` ~84× over‑count fixed** via `WHERE is_hardware_oos_event = TRUE`.
- **PS3 target bug fixed**: `IN (1,2,3)` → the correct material set `IN (1,2,3,4,5,16)`, target = structured `FAILURE_LEVEL`, fan‑out eliminated via `ROW_NUMBER…rn=1`.
- **PS4 raw‑parquet read fixed** → now reads `silver.metric_hourly`.
- **READER reframed as a component** (200‑series), not a 4th device category, across silver + all gold.
- **New tables landed**: `dim_failure_level` (taxonomy), `dim_event_matrix` (the 187‑row Device Event Matrix with OOS/maintenance/reader/priority/KPI flags), `dim_stop_point` (geo), `incident_history` (SN incident view).
- **SCD2 `is_current` guards** present on dim_device attribute joins **everywhere except S10** and the two gold DEVICE_KEY joins.

**The single most important finding (cross‑cutting):** `06_dim_device` still maps **turnstiles (`TT_/TTC/TWA/TEX`) to `GATE`** — the opposite of the Round‑2 rule (GATE = RVG/HBG/SAG only; turnstiles legacy → drop). Because all 6 gold scripts trust `mars_device_category`, this **propagates into every GATE model**. This is the highest‑leverage fix in the whole set.

---

## 2. Silver — per‑table verdicts

| # | Table | Verdict | Headline issue |
|---|---|---|---|
| 01 | dim_failure_level | PASS‑W‑NOTES | `is_device_fault = {1,2,3,4,5,16}` ✓. Doc says 41 levels, VALUES has ~29 (cat‑2 complete; back‑office partial). |
| 02 | dim_stop_point | **NEEDS‑FIX** | Reads `ncs_stage_stop_point` and calls the EDW table "pending" — but Round‑2 **landed `edw_stop_point_dimension` (12,888 rows)**. Wrong source; missing ADDRESS/lat‑long; no `STOP_POINT_ID` dedup. |
| 03 | dim_event_matrix | PASS‑W‑NOTES | Faithfully encodes the matrix (OOS/maintenance 151+106/reader/priority/KPI flags) ✓. `is_reader_event = 200–299` (confirm scope); code 156 disagrees with S07. |
| 04 | dim_facility | PASS | Clean retail/reload dim; geo NULL by design (S02 is the geo source). |
| 05 | metric_hourly | PASS‑W‑NOTES | Governed (parquet read removed) ✓. Has a 2024‑01‑01 lower bound that S10 lacks — align them. |
| 06 | dim_device | **NEEDS‑FIX** | **[BLOCKER]** turnstiles `TT_/TTC/TWA/TEX` → `GATE`; **[HIGH]** category emits `OTHER` not `EXCLUDED`; **[HIGH]** `ncs_stage_device` join not deduped (fan‑out on top of SCD2); verify `DEVICE_STATUS_ID` exists. |
| 07 | dim_event_type | PASS‑W‑NOTES | `is_hardware_oos_event` correctly excludes commanded/maint ✓. `is_reader_event 200–299` + code 156 vs S03; `applies_to_*` re‑approximated by range instead of using S03's per‑code flags. |
| 08 | device_uptime_intervals | PASS‑W‑NOTES | SCD2‑guarded ✓. `uptime_pct/hours` are NULL placeholders (cols absent in bronze); verify `last_state` is 1‑row‑per‑device. |
| 09 | hw_config_current | PASS | SCD2‑guarded, component‑grain (intended) ✓. |
| 10 | metric_daily | **NEEDS‑FIX** | **[HIGH]** dim_device join on `DEVICE_KEY` with **no `is_current` guard** — the prior issue is **not** fixed; add guard or join on `device_id`. |
| 11 | kpi_avail_enriched | PASS‑W‑NOTES | jumpbox fan‑out fixed ✓. But `sn_events` (`AE_EVENT_ID`) and `relief` joins are **not** deduped — dry‑run the row count. |
| 12 | kpi_daily | PASS | Grain honestly disclosed (per‑event, not per‑day); all lookups pre‑aggregated; SCD2‑guarded. |
| 13 | tap_event_daily | PASS‑W‑NOTES | Governed + guarded ✓. Grain is `(device,day,operator,bus)`; peak‑hour metric pinned at device‑day level; double full‑scan of ABP_TAP (2B). |
| 14 | tvm_sale_daily | PASS | Governed + guarded; TVM‑vs‑VALIDATOR filter correctly deferred to gold. |
| 15 | incident_history | **NEEDS‑FIX** | Both prior blockers fixed (parquet + SPLIT_PART) ✓. But **[HIGH]** the three CMDB display‑value joins (`name`/`model_name`/`category`) are **un‑deduped on a 7M‑row base** → fan‑out; **[MED]** several `u_*` columns referenced but not in the confirmed CSV schema. |
| 16 | device_event_enriched | PASS | Deduped spine, SCD2‑guarded, all Round‑2 flags wired (hw_oos / reader / matrix flags). |
| 17 | incident_root_cause | PASS | PS3 label on `FAILURE_LEVEL IN (1,2,3,4,5,16)` ✓, free‑text only as features, S15 fan‑out deduped. |
| 18 | device_outage | PASS‑W‑NOTES | Hardware‑OOS filter ✓. **[MED]** `is_chargeable`/`failure_level` come from a **same‑day** availability‑events join — hardware‑OOS events with no same‑day availability row default to `failure_level=0` (non‑chargeable) → can **under‑label PS1 positives**. Dry‑run the match rate. |
| 19 | maintenance_ledger | PASS‑W‑NOTES | Derives from events ✓, but the set is the **superset `{106,110,151,208,519}`**, not strict Round‑2 `{151,106}` — 110/208/519 leak into S20's cumulative maintenance counts. Confirm intent. |
| 20 | usage_lifecycle_daily | PASS‑W‑NOTES | Hardware‑failure‑correct ✓. **[MED]** cumulative/lifecycle windows partition by `DEVICE_KEY` while joins key on `DEVICE_ID` — if one device has >1 SCD2 key, lifetime wear counters fragment. Partition by `DEVICE_ID`. |

---

## 3. Gold — per‑PS verdicts, and which prior problems were fixed

| File | PS | Verdict | Prior problem → status |
|---|---|---|---|
| ps3_incident | PS3 | **PASS‑W‑NOTES** | `IN (1,2,3)` → `IN (1,2,3,4,5,16)` target = FAILURE_LEVEL — **FIXED** ✓; fan‑out eliminated ✓. |
| ps1_label_compare | PS1 | PASS | Correct read‑only A/B compare (proves the right `failure_level>0` label). |
| ps1_daily__create | PS1 | **NEEDS‑FIX** | Label still the **outage proxy** (`duration_min>0`), **not `failure_level>0`** — material data is present as *features* but not wired to the target. **STILL PRESENT.** |
| ps2_chains | PS2 | **NEEDS‑FIX** | Reads **bronze** `ncs_stage_cashbox_tracking` (not silver); dim_device join on **`DEVICE_KEY`** (NULL device context incl. gate‑bank `ARRAY_ID`). **BOTH STILL PRESENT.** |
| ps4_hourly | PS4 | **NEEDS‑FIX** | **No maintenance (151/106) suppression** → planned maintenance fires false anomalies; dim_device join on **`DEVICE_KEY`**. **BOTH STILL PRESENT.** |
| ps5_component | PS5 | **NEEDS‑FIX** | Survival "failure" proxy counts **any** outage (missing `failure_level IN (1,2,3,4,5,16)`); reads **bronze** cashbox. **BOTH STILL PRESENT.** |

Of the 5 specific prior problems, **only PS3 is resolved.** The other four are all still present — but the material data is now plumbed into PS1/PS5 as features, so each remaining fix is a small, localized change (one `WHERE`/one join key).

---

## 4. Prioritized fix list

**BLOCKER / HIGH — fix before building:**
1. **S06 dim_device — turnstiles → GATE.** Route `TT_/TTC/TWA/TEX` to the excluded bucket; GATE = RVG/HBG/SAG only. Also rename the 4th category `OTHER` → `EXCLUDED`. *(Propagates to all gold — fix first.)*
2. **S02 dim_stop_point — repoint to `mars_dev.bronze.edw_stop_point_dimension`** (the 12,888‑row geo table we ingested Jun‑24), add a `STOP_POINT_ID` dedup. The current NCS source is missing address/lat‑long.
3. **S06 dim_device — dedup the `ncs_stage_device` join** to one row per `DEVICE_ID` (prevents fan‑out on top of SCD2).
4. **S10 metric_daily — add `is_current` guard** (or join on `device_id`) to the dim_device join.
5. **S15 incident_history — dedup the 3 CMDB joins** (`ROW_NUMBER…rn=1` per `name`) and confirm the `u_*` columns exist in `bronze.cta_servicenow_incident` before first run.
6. **PS2 + PS5 — replace the bronze cashbox read** with a governed `silver` cashbox source (promote `ncs_stage_cashbox_tracking` → a silver table, mirroring PS4's FIX‑15).
7. **PS2 + PS4 — change the dim_device join from `DEVICE_KEY` → `device_id`** (DEVICE_KEY is unpopulated → NULL device context, including the PS2 gate‑bank `ARRAY_ID`).
8. **PS4 — add maintenance‑window suppression** (exclude/flag events 151 + 106) so planned maintenance doesn't trip the anomaly ensemble.
9. **PS1 — wire the label to `failure_level>0`** (run `__label_compare` first to confirm Label B volume, then gate `outage_label_days` on `failure_level>0`).
10. **PS5 — add `failure_level IN (1,2,3,4,5,16)`** (or `is_chargeable`) to the survival failure proxy so `days_to_failure`/`is_censored` reflect material faults.

**MED — confirm/verify (often a decision, not a bug):**
- S18 — dry‑run the % of hardware‑OOS device‑days that match a same‑day availability row (label‑coverage for PS1).
- S19 — decide strict `{151,106}` vs the current superset; if strict, narrow the Source‑B filter.
- S20 — partition cumulative windows by `DEVICE_ID` (or assert metric_daily is 1 DEVICE_KEY per DEVICE_ID).
- S03/S07 — reconcile code **156** (commanded in S03, not in S07) and confirm the reader scope (whole 200‑series vs `{206,221,222}` primary).
- S05/S10 — align the 2024 lower‑bound between the two metric tables.

---

## 5. Views & suggestions

- **The split is encouraging.** Everything that's *structural* (governance, taxonomy tables, READER reframe, PS3, hardware‑OOS filter) is done well and consistently. What's left is **label/scope wiring** — almost all one‑line changes. A focused half‑day pass clears the entire HIGH list.
- **Fix `dim_device` first.** The turnstile→GATE and `OTHER`/`EXCLUDED` items aren't just a silver bug — every gold model filters on `mars_device_category`, so this is the highest‑leverage change. After it, re‑confirm the GATE population count.
- **Standardize on `device_id` + `is_current`** as the one dim_device join contract, and lint for `DEVICE_KEY` joins (S10, PS2, PS4, and the S20 partition). `device_key` is largely unpopulated, so every `DEVICE_KEY` join is silently dropping device context today.
- **The PS1 label decision is a 2‑step, not a guess.** Run `__label_compare` (it's correct and read‑only) to quantify Label A vs Label B, then flip PS1 to `failure_level>0`. The compare existing but the build not adopting it suggests this was deliberately deferred — worth closing now.
- **`device_outage.is_chargeable` is the quiet risk.** Because it's set from a *same‑day* availability‑events join, PS1's positives are only as complete as the availability‑events coverage of the 1.12B‑row hardware‑OOS slice. Measure the match rate before training — it underpins PS1, PS2, and PS5 labels.
- **Promote cashbox to silver once**, then both PS2 and PS5 (and any future cashbox feature) are governed in one move.
- **Add a tiny CI check**: assert `mars_device_category` ∈ {TVM,GATE,VALIDATOR,EXCLUDED} and that no gold script references `bronze.` — both of today's recurring issues would have been caught automatically.

---

## 6. Open questions to confirm (likely deliberate choices, not bugs)

1. **Turnstiles:** my Round‑2 note says drop them; `dim_device` keeps them as GATE (comment: "+389 added 2026‑06‑23"). Did you intend to keep turnstiles in GATE scope, or is this the pre‑Round‑2 mapping that still needs reverting?
2. **Reader slice:** is the READER component the **whole 200‑series** (current `is_reader_event = 200–299`) or just the primary `{206,221,222}`?
3. **Maintenance set:** strict `{151,106}` per Round‑2, or the current superset `{106,110,151,208,519}`?
4. **PS1 label:** is the outage‑proxy intended as the v1 target (with `failure_level>0` as a fast‑follow), or should it ship as `failure_level>0` now?
5. **`dim_failure_level` count:** 29 vs the documented 41/43 — is the back‑office remainder still pending from Michael?

---

*Validation note: this report is against local `main` merged to origin `a973674`. The only difference from origin is two un‑pushed ingestion commits (notebooks/docs) that don't touch `sql/`.*
