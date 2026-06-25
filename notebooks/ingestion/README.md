# Round-2 ingestion — Oracle EDW → S3 raw → bronze Delta

Pulls the EDW tables Michael confirmed (Round-2, 2026-06) from the Ventra Oracle ODS
(`10.3.10.30:1521`, over the VPN) → S3 raw parquet → **bronze Delta** (a managed Unity-Catalog
table is Delta by default, so `CREATE OR REPLACE TABLE … AS SELECT * FROM parquet.\`<raw>\`` is all
that's needed).

## Notebooks here

| Notebook | What it does |
|---|---|
| `ingest_round2_tables.py` | Reference/dimension tables (KPI_RULES / KPI / KPI_TARGET / STOP_POINT_DIMENSION). Small, full copies. |
| `ingest_use_txn_daily.py` | **`USE_TRANSACTION` revenue as a device-day aggregate** (the heavy-fact pattern — see below). |
| `oracle_connectivity_diagnostics.py` | Bounded network root-cause tests for the VPN/Oracle path (TCP, listener probe, MTU). Run this first if anything times out. |

## Status (2026-06-24)

| Bronze table | Oracle source | Rows | State |
|---|---|---|---|
| `edw_kpi_rules` | `EDW.KPI_RULES` | 123 | ✅ landed (failure_level → KPI map) |
| `edw_kpi` | `EDW.KPI` | 60 | ✅ landed |
| `edw_kpi_target` | `EDW.KPI_TARGET` | 236 | ✅ landed |
| `edw_stop_point_dimension` | `EDW.STOP_POINT_DIMENSION` | 12,888 | ✅ landed (address + lat/long, geo) |
| `edw_use_transaction_daily` | `EDW.USE_TRANSACTION` (aggregated) | — | ⏳ pending (blocked on VPN — see ops note) |

## `USE_TRANSACTION` → device-day aggregate (`ingest_use_txn_daily.py`)

`EDW.USE_TRANSACTION` is **~5.95 billion rows**. We do **not** copy it raw. For the per-device revenue
feature we land a **device-day aggregate** — the `GROUP BY DEVICE_ID, TRANSIT_DAY_KEY → SUM(FARE_DUE)` is
**pushed into Oracle**, so the DB aggregates and we transfer ~millions of rows, not billions.

Locked rules (each = a lesson already paid for):
- **Chunk/filter on the INDEXED `TRANSIT_DAY_KEY`, never `EDW_INSERTED_DTM`** (the latter is unindexed for
  range scans → MIN/MAX and `WHERE` full-scan and trip the timeout).
- One **per-month** query (date filter *inside* each query so Oracle scans only that month); Spark runs them
  as parallel tasks. Widgets: `start_ym` / `end_ym` / `mode` (overwrite|append).
- **Recent window first** (e.g. 2026 YTD), backfill older months **off-hours** — the source is a shared
  reporting Oracle and a full 2024+ scan is heavy.
- `REVENUE_OR_TEST = 'REVENUE'` (full word, not `'R'`).

## Prerequisites
- Run from the **Databricks Git folder** on `cubic-mars-dev`, cluster with `oracle.jdbc.OracleDriver`.
- Secret scope **`cubic`** (`ods_user`, `ods_pwd`, `ods_service`).
- **VPN reachability + a healthy Oracle login** to `10.3.10.30:1521` — confirm with the diagnostics notebook
  first (see ops note); a `SELECT 1 FROM DUAL` must return cleanly before any pull.

## ⚠ Connectivity (read `docs/ops/Oracle_VPN_MTU_Connectivity_Finding_2026-06-24.md`)
If Oracle calls hang at `T4CConnection.logon` (`Authentication lapse 0 ms`) it is a **connection** failure,
not a slow query. Root cause found 2026-06-24: a **VPN-tunnel MTU black-hole** (path MTU 1422; full-size
packets dropped after the T2 failover). Fix = **MSS clamp to ~1379** on the tunnel (Mars infra).
Client-side band-aid that lets queries through meanwhile: add **`SDU=512`** to the JDBC connect descriptor
(`jdbc:oracle:thin:@(DESCRIPTION=(SDU=512)(ADDRESS=…)(CONNECT_DATA=…))`).

## Ops notes
- Always set **both** `oracle.net.CONNECT_TIMEOUT` **and** `oracle.jdbc.ReadTimeout` (without ReadTimeout a
  hung logon blocks forever; with short values a dead DB fails fast instead of looking "slow").
- Each `spark.read.jdbc` opens a **new** connection (no pooling) — batch probes into few connections.
- Re-runnable: bronze uses `CREATE OR REPLACE TABLE`. Raw layout: `s3://…-raw-…/chicago_ventra/edw/<table>/`.
- After ingest: silver enrichments (`dim_event_type` from the event matrix, `use_transaction_daily`,
  `dim_stop_point`, KPI_RULES join) then the gold PS rebuilds.
