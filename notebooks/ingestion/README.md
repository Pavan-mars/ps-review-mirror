# Round-2 ingestion — `ingest_round2_tables`

Pulls the new EDW tables Michael confirmed on 2026-06-23 from Oracle ODS → S3 raw → bronze Delta,
using the same JDBC pattern as the existing `*_end_to_end` notebooks.

| Bronze table | Oracle source | Type | Purpose |
|---|---|---|---|
| `edw_kpi_rules` | `EDW.KPI_RULES` | reference (full) | failure_level → KPI map (source of truth) |
| `edw_kpi` / `edw_kpi_target` | `EDW.KPI` / `EDW.KPI_TARGET` | reference (full) | KPI engine controls |
| `edw_stop_point_dimension` | `EDW.STOP_POINT_DIMENSION` | dimension (full) | address + lat/long (geo) |
| `edw_use_transaction` | `EDW.USE_TRANSACTION` | heavy fact (2024+) | `FARE_DUE` = per-device revenue |

## Prerequisites
- Run from the **Databricks Git folder** (`/Repos/…/Chicago-Ventra-Mars-Cubic-Analysis/notebooks/ingestion/`)
  on the `cubic-mars-dev` workspace.
- Secret scope **`cubic`** with keys `ods_user`, `ods_pwd`, `ods_service` (already present).
- Cluster with the **Oracle JDBC driver** (`oracle.jdbc.OracleDriver`) and **VPN reachability** to `10.3.10.30:1521`.

## How to run (incremental, safe)
1. Pull latest into the Git folder, attach a cluster, open the notebook.
2. **Dims first** (default `RUN_DIMS=True`): runs `KPI_RULES`, `KPI`, `KPI_TARGET`, `STOP_POINT_DIMENSION`
   — small, low-risk; validates the raw→bronze path end-to-end.
3. **Confirm** the `# CONFIRM` items (exact Oracle names; the `USE_TRANSACTION` watermark = `EDW_INSERTED_DTM`; PKs).
4. **Heavy fact:** set `RUN_HEAVY=True` to pull `USE_TRANSACTION` (2024+). For a very large pull, prefer the
   existing hardened `*_end_to_end` engine (day-chunk + circuit breaker) rather than the single partitioned read.
5. **Contract:** set `APPLY_CONTRACT=True` to register the tables in `mars_dev.audit.bronze_data_contract`
   (verify the column set matches NB73 first).

## Notes
- Re-runnable: bronze uses `CREATE OR REPLACE TABLE … AS SELECT * FROM parquet.\`<raw>\``.
- Raw layout: `s3://…-raw-…/chicago_ventra/edw/<table>/`.
- After ingest, the silver enrichments (dim_event_type from the event matrix, `use_transaction_daily`,
  `dim_stop_point`, KPI_RULES join) and the gold PS rebuilds follow (tasks #74–#78).
