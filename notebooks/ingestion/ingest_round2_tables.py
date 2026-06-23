# Databricks notebook source
# MAGIC %md
# MAGIC # Round-2 ingestion — new EDW tables (Oracle → S3 raw → bronze Delta)
# MAGIC Adds the tables Michael confirmed on 2026-06-23:
# MAGIC | Bronze | Oracle | Type | Why |
# MAGIC |---|---|---|---|
# MAGIC | `edw_use_transaction` | `EDW.USE_TRANSACTION` | heavy fact (2024+) | `FARE_DUE` = per-device revenue (Q10) |
# MAGIC | `edw_stop_point_dimension` | `EDW.STOP_POINT_DIMENSION` | dimension (full) | address + lat/long (Q11) |
# MAGIC | `edw_kpi_rules` | `EDW.KPI_RULES` | reference (full) | failure_level → KPI map, source of truth (Q4) |
# MAGIC | `edw_kpi` / `edw_kpi_target` | `EDW.KPI` / `EDW.KPI_TARGET` | reference (full) | KPI engine controls |
# MAGIC
# MAGIC Mirrors the existing `*_end_to_end` notebooks: JDBC via secret scope `cubic`, raw parquet under
# MAGIC `chicago_ventra/edw/<table>/`, bronze = `mars_dev.bronze.edw_<table>` (DIRECT `CREATE OR REPLACE`).
# MAGIC **Re-runnable.** Safe by default: dims run; the heavy fact and the contract write are gated by flags.
# MAGIC Confirm the items flagged `# CONFIRM` (Oracle names, watermark, PKs) before the heavy pull.

# COMMAND ----------
import datetime as dt
from pyspark.sql import functions as F

spark.sql("USE CATALOG mars_dev")          # warm-up: avoids intermittent NO_SUCH_CATALOG

# ── run flags ────────────────────────────────────────────────────────────────
RUN_DIMS       = True     # small reference/dimension tables (cheap, low-risk)
RUN_HEAVY      = False    # EDW.USE_TRANSACTION (large) — flip to True to pull
APPLY_CONTRACT = False    # write rows into mars_dev.audit.bronze_data_contract (confirm schema first)

# ── Oracle ODS (identical to the proven ingestion notebooks) ─────────────────
ODS_HOST, ODS_PORT = "10.3.10.30", 1521
_SCOPE      = "cubic"                       # Databricks secret scope: keys ods_user / ods_pwd / ods_service
OWNER       = "EDW"                          # Oracle owner/schema
RAW_ROOT    = "s3://cubic-mars-pm-s3-datalake-dev-raw-170202974600/chicago_ventra"
BRONZE_DB   = "mars_dev.bronze"
CONTRACT    = "mars_dev.audit.bronze_data_contract"
SESSION_TAG = "mars_round2_ingest"

def _secret(k): return dbutils.secrets.get(_SCOPE, k)
def _creds():
    return {"user": _secret("ods_user"), "pwd": _secret("ods_pwd"),
            "url":  f"jdbc:oracle:thin:@//{ODS_HOST}:{ODS_PORT}/{_secret('ods_service')}"}

_JDBC_OPTS = {
    "driver": "oracle.jdbc.OracleDriver",
    "fetchsize": "50000",
    "oracle.net.CONNECT_TIMEOUT": "15000",
    "oracle.jdbc.ReadTimeout":    "1800000",   # 30 min (thin-driver correct prop)
    "queryTimeout": "1800",
    "v$session.program": SESSION_TAG,           # tag our sessions for the DBA
}

def _read(dbtable, part=None):
    r = (spark.read.format("jdbc")
         .option("url", _creds()["url"]).option("user", _creds()["user"])
         .option("password", _creds()["pwd"]).option("dbtable", dbtable))
    for k, v in _JDBC_OPTS.items():
        r = r.option(k, v)
    if part:
        for k, v in part.items():
            r = r.option(k, str(v))
    return r.load()

# COMMAND ----------
# MAGIC %md ## helpers — raw write + bronze build

def _raw_path(table): return f"{RAW_ROOT}/edw/{table.lower()}"

def _to_bronze(table):
    raw = _raw_path(table)
    b   = f"{BRONZE_DB}.edw_{table.lower()}"
    spark.sql(f"CREATE OR REPLACE TABLE {b} AS SELECT * FROM parquet.`{raw}`")
    n = spark.table(b).count()
    print(f"  bronze {b}: {n:,} rows")
    return b, n

def ingest_full(table):
    """Single-shot extract for dimensions / reference tables (small)."""
    print(f"[FULL] {OWNER}.{table}")
    df = _read(f"{OWNER}.{table}")
    df.write.mode("overwrite").parquet(_raw_path(table))
    print(f"  raw written -> {_raw_path(table)}")
    return _to_bronze(table)

def ingest_heavy(table, wm_col, start="2024-01-01", nparts=8):
    """Partitioned extract for heavy facts, filtered to >= start on the watermark column.
    NOTE: EDW_INSERTED_DTM is unindexed on the big facts — for very large pulls prefer the
    existing hardened *_end_to_end engine (day-chunk + circuit breaker)."""
    print(f"[HEAVY] {OWNER}.{table}  wm={wm_col}  >= {start}  parts={nparts}")
    hi = dt.date.today().isoformat()
    q  = (f"(SELECT * FROM {OWNER}.{table} "
          f"WHERE {wm_col} >= TO_DATE('{start}','YYYY-MM-DD')) t")
    part = {"partitionColumn": wm_col,
            "lowerBound": f"{start} 00:00:00", "upperBound": f"{hi} 00:00:00",
            "numPartitions": nparts}
    df = _read(q, part)
    df.write.mode("overwrite").parquet(_raw_path(table))
    print(f"  raw written -> {_raw_path(table)}")
    return _to_bronze(table)

# COMMAND ----------
# MAGIC %md ## 1) Reference + dimension tables (small — run first)
# CONFIRM Oracle table names with Cubic (EDW owner assumed):
DIM_TABLES = ["KPI_RULES", "KPI", "KPI_TARGET", "STOP_POINT_DIMENSION"]
if RUN_DIMS:
    for t in DIM_TABLES:
        try:
            ingest_full(t)
        except Exception as e:
            print(f"  !! {t} FAILED: {str(e)[:240]}")
else:
    print("RUN_DIMS=False — skipped")

# COMMAND ----------
# MAGIC %md ## 2) USE_TRANSACTION — heavy fact (revenue / FARE_DUE), 2024+
# CONFIRM: watermark column (EDW_INSERTED_DTM assumed, like ABP_TAP / DEVICE_EVENT / DEVICE_METRIC)
if RUN_HEAVY:
    ingest_heavy("USE_TRANSACTION", wm_col="EDW_INSERTED_DTM", start="2024-01-01", nparts=8)
else:
    print("RUN_HEAVY=False — USE_TRANSACTION not pulled (flip RUN_HEAVY=True when ready)")

# COMMAND ----------
# MAGIC %md ## 3) Register in the bronze data contract  (gated by APPLY_CONTRACT)
# rows: (table_name, scope, scope_col, pk, watermark, load_strategy, ml_role)   # CONFIRM pk cols + contract schema
R2_CONTRACT = [
 ("edw_kpi_rules",            "whole",    None,               "KPI_RULE_ID",      None,              "full",   "reference: failure_level->KPI map"),
 ("edw_kpi",                  "whole",    None,               "KPI_ID",           None,              "full",   "reference: KPI definitions"),
 ("edw_kpi_target",           "whole",    None,               "KPI_ID",           None,              "full",   "reference: KPI targets"),
 ("edw_stop_point_dimension", "whole",    None,               "STOP_POINT_KEY",   None,              "full",   "dim: geo (address/lat/long)"),
 ("edw_use_transaction",      "2024plus", "EDW_INSERTED_DTM", None,               "EDW_INSERTED_DTM","append", "fact: revenue (FARE_DUE)"),
]
if APPLY_CONTRACT:
    schema = "table_name string, scope string, scope_col string, pk string, watermark string, load_strategy string, ml_role string"
    spark.createDataFrame(R2_CONTRACT, schema).createOrReplaceTempView("_r2_contract")
    # CONFIRM: column set must match mars_dev.audit.bronze_data_contract (NB73). Adjust the MERGE if it differs.
    spark.sql(f"""
        MERGE INTO {CONTRACT} t USING _r2_contract s
        ON t.table_name = s.table_name
        WHEN MATCHED THEN UPDATE SET *
        WHEN NOT MATCHED THEN INSERT *
    """)
    print("bronze_data_contract updated for round-2 tables")
else:
    print("APPLY_CONTRACT=False — printing intended contract rows only:")
    for r in R2_CONTRACT: print("  ", r)

# COMMAND ----------
# MAGIC %md ## verify
for t in ["edw_kpi_rules","edw_kpi","edw_kpi_target","edw_stop_point_dimension","edw_use_transaction"]:
    try:
        print(f"{t:30s} {spark.table(f'{BRONZE_DB}.{t}').count():>12,} rows")
    except Exception as e:
        print(f"{t:30s} MISSING ({str(e)[:60]})")
