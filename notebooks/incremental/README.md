# Chicago ODS incremental (12-Apr-2026 →) — discovery pack

**Built 01-Sep-2026.** Scope deliberately limited to the **Databricks medallion** (raw → bronze → silver → gold)
in `mars_dev`. AWS/SageMaker/dashboard impact is noted but not addressed here.

## Evidence basis — read this before trusting anything below

Every claim in `00_SCOPE_tables_in_scope.csv` is derived from files, not memory:

| Claim | Source |
|---|---|
| which bronze tables are *actually consumed* | comment-stripped `FROM`/`JOIN` parse of all 31 `sql/silver/*.sql` + 6 `sql/gold/*.sql` in `sathish_repo`, cross-checked against a table-reference scan of 21 `notebooks/**/*.py` **and all 15 `notebooks/**/*.ipynb`** (PS1–PS5 SageMaker notebooks, cell sources parsed from JSON). Three independent scans, same answer. |
| Oracle source for each bronze table | `bronze_rebuild/bronze_61_manifest_seed.csv` (61 rows) |
| watermark / load strategy candidates | `chicago_ventra_platform/databricks/wave1_final_config.csv` + `73_BRONZE_DATA_CONTRACT.py` curated SEED + `validate_partition_columns.sql` |
| build order + per-table bronze sources | `chicago-data-catalog/references/build_order.md` (verified vs repo DDL 2026-07-21) |
| watermark/raw-layout defects | `CUBIC_MARS_Chicago_Bronze_Watermark_and_RawFormat_Resolution_v1_28Aug2026.md` |

**Nothing here was measured against live Oracle, live S3 or live Databricks.** That is exactly what the
three probe notebooks are for. Do not change a contract, write DDL, or ingest anything off this document alone.

## The headline

The list sent to Cubic has **81 tables**. The bronze layer holds **85**. The silver/gold DDL actually reads **33**.
Of those 33, **31 come from the Oracle ODS** (EDW / NCS_STAGE / CTA) and **5 come from the ServiceNow
one-time dumps** — which you asked to exclude (3 of them overlap the incident family).

So the incremental feed needs to cover **31 tables, not 81**. Roughly a 62% reduction in scope.

Two findings worth acting on independent of the increment:

1. **`bronze.ncs_stage_device_event_history` — 1.25 billion rows, 46.7 GB, the single largest table in the
   lakehouse — is read by nothing.** Zero references across the silver DDL, the gold DDL, the 21 Python
   notebooks and the 15 SageMaker `.ipynb` notebooks. It is the most expensive object in the platform and
   currently has no consumer. Ten more tables are in the same position (tier 3 in the scope CSV) — together
   roughly **half the bronze footprint**.
2. **Two tables on the "ServiceNow" tab of your spreadsheet are actually Oracle tables.**
   `cta_servicenow_availability_events` and `cta_servicenow_data_from_jumpbox` are `CTA.SERVICENOW_*` in the
   Oracle CTA schema (per `bronze_61_manifest_seed.csv` rows 18 and 52), not SaaS/XML dumps. They feed
   `silver.incident_root_cause`, which feeds PS3. **They belong in the incremental scope.**

## Run order

Each notebook is read-only and prints a `<<<PROBEnn_JSON_START>>> … END` block. Paste those back.

| # | Notebook | Needs | ~Time | Answers |
|---|---|---|---|---|
| 1 | `01_ORACLE_incremental_probe.py` | Databricks + VPN + `cubic` secret scope | 20–40 min | asks 1, 2 — existence, real Oracle watermarks, row counts since 12-Apr, monthly shape |
| 2 | `02_BRONZE_state_and_contract_probe.py` | Databricks + UC + S3 (no Oracle) | 10–20 min | ask 3 — current raw + bronze data contracts, declared-vs-real contract flags, raw layout |
| 3 | `03_SCHEMA_DRIFT_and_sample.py` | Databricks + VPN | 30–50 min | asks 4, 5, 6 — drift matrix, drift verdict, sample of the new rows, blast radius |

Run **2 before 1 if the VPN is down** — it needs no Oracle and still delivers ask 3.

### Connection settings baked in (do not remove)

- `SDU=512` in the JDBC descriptor — the VPN MTU black-hole band-aid from the 24-Jun-2026 finding.
  Remove only once the MSS clamp to ~1379 is live on the tunnel.
- **Both** `oracle.net.CONNECT_TIMEOUT` and `oracle.jdbc.ReadTimeout` are set. Without ReadTimeout a hung
  logon blocks forever.
- Every `MAX()` is bounded by `< SENTINEL` (tomorrow). Bronze carries known future-dated sentinel rows
  (`edw_device_metric` → 2028-05-13; several NCS tables → 2032–2034). An unbounded `MAX()` reports 2034 and
  makes a stale table look fresh.
- Watermarks are resolved from `ALL_TAB_COLUMNS`, **never** from the bronze DataFrame, and any column
  starting with `_` is rejected. That inversion is the root cause of the 16-table `_ingest_ts` defect.

## Decisions I need from you

**D1 — scope.** Ingest incrementally only the **31 consumed** tables (recommended), or all **61** in the
manifest? Every extra table is VPN time, S3 cost and a contract row to maintain. My recommendation: 31 now,
and treat the other 30 as a separate "do we still need this?" list — starting with
`ncs_stage_device_event_history`.

**D2 — `edw_availability_events` load strategy conflict.** `73_BRONZE_DATA_CONTRACT.py` curated SEED declares
it `full` / `whole` / no watermark. `wave1_final_config.csv` declares it `incremental` on `UPDATED_DTM`.
It is 645K rows and the label source for PS1/PS3 — a wrong choice here corrupts the failure labels, not just
row counts. NB01 Cell 3 settles it from Oracle.

**D3 — `EDW.READ_TRANSACTION`.** The project memory notes say it is a real base table at 11.4M rows; the mlops SKILL says
it is probably a column inside `USE_TRANSACTION`. `silver.read_tap_daily` reads it. NB01 Cell 3 settles this
with `ALL_TABLES`. If it does not exist, `silver/22` and `silver/30` are building on nothing.

**D4 — what "Cubic has started providing" actually means.** Are the new rows (a) appearing in the same Oracle
ODS tables we already pull, or (b) arriving as a separate delivery (files to S3, a new schema)? The probes
detect either — NB01 measures Oracle, NB02 Cell 4 measures what has landed in raw — but the answer changes
the update notebooks entirely. If you already know, tell me and I will skip the branch.

**D5 — the ServiceNow incident family.** `silver.incident_history`, `incident_root_cause` and
`incident_task_ci_link` depend on ServiceNow dumps frozen at 30-May-2026. An ODS-only incremental refreshes
their *other* inputs but leaves the incident side frozen, so `gold.device_ps3_incident` and
`gold.device_ps5_component` will mix fresh device data with stale incidents. Options: accept and label it,
pause those two golds, or get a ServiceNow incremental. This needs a decision before PS3/PS5 are re-scored.

## What comes next (ask 7)

The update notebooks are **not** in this pack, on purpose. They should be generated from the corrected
contract, not from assumptions — writing them now would repeat the exact defect the 28-Aug resolution
document identifies. Once probes 1–3 come back I will produce:

- `10_CONTRACT_REPOINT.py` — fix watermark detection to read Oracle, extend `WATERMARK_PREFERENCE` with the
  real Chicago names, add the leading-underscore rejection guard, reclassify the full-reload tables.
- `11_RAW_LAYOUT_NORMALISE.py` — only for the prefixes NB02 Cell 4 reports as MIXED.
- `12_INCREMENTAL_LOAD.py` — contract-driven append / merge / scd2 / full, per table, DRY_RUN by default.
- `13_SILVER_GOLD_REBUILD.py` — layer-ordered L1 → L6 rebuild with row-count reconciliation at each layer.

## Known issues these probes will surface (predictions — verify, don't assume)

- `silver.incident_root_cause` has 670 duplicate rows on `availability_event_id` (the only validation FAIL
  as of 2026-07-17). Fix at S17 before the increment multiplies it.
- `servicenow_incident_conformed` is referenced as **`bronze.`** in `sql/silver/15`, `sql/silver/25` and
  `notebooks/dim/export_device_incident_cmdb_daily.py`, but the catalog skill documents it as
  **`silver.servicenow_incident_conformed`**. One of the two is wrong. Worth settling in the same pass.
- `gold.dim_device_incident_cmdb` and `gold.tap_event_daily_rolling` are written/read by notebooks but are not
  among the 5 gold tables in the catalog. Either the catalog is stale or these are undocumented objects.
