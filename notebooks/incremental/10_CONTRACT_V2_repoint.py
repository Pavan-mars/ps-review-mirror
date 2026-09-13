# Databricks notebook source
# MAGIC %md
# MAGIC # 10 — CONTRACT v2 REPOINT  (writes the MEASURED contract into `mars_dev.audit.bronze_data_contract`)
# MAGIC
# MAGIC Replaces guessed strategy fields with the probe-01/04-measured ones for the 32 in-scope tables,
# MAGIC **preserving every other row and every extra column** (the DQ fields someone added after NB73 —
# MAGIC `null_check_mandatory`, `dq_basis`, … — survive untouched).
# MAGIC
# MAGIC What it changes, per table: `load_strategy`, `pull_wm` (+indexed flag), `merge_key`, `dedupe_key`,
# MAGIC `chunking`, `bronze_write`, `scope_col`, `status='measured_v2'`. New columns are added with
# MAGIC `ALTER TABLE ADD COLUMNS` (nullable) — never `overwriteSchema`.
# MAGIC
# MAGIC Guards enforced at write: a watermark starting with `_` OR in the bronze-audit set
# MAGIC (`LOAD_DATE, SRC_YEAR, YEAR, MONTH, DAY, load_date, ingest_date, load_ts, load_dtm`) is REFUSED loudly.
# MAGIC DRY_RUN=True prints the diff; set False to apply. Reversible: prior state saved to
# MAGIC `audit.bronze_data_contract_backup_<ts>` before any change.

# COMMAND ----------
# ============================== CELL 1 : THE MEASURED V2 ROWS ==============================
import datetime as _dt
from pyspark.sql import functions as F

DRY_RUN  = True
CONTRACT = "mars_dev.audit.bronze_data_contract"

# (table, load_strategy, scope_col, pull_wm, pull_wm_indexed, merge_key, dedupe_key, chunking, bronze_write)
V2 = [
 ("edw_device_event",          "cdc_merge",  "EVENT_DTM",      "EDW_UPDATED_DTM", "Y", "DW_DEVICE_EVENT_ID", "-", "monthly:EDW_UPDATED_DTM", "merge"),
 ("edw_abp_tap",               "cdc_merge",  "TRANSACTION_DTM","EDW_UPDATED_DTM", "Y", "TAP_ID,SOURCE",      "-", "monthly:EDW_UPDATED_DTM", "merge"),
 ("edw_read_transaction",      "cdc_merge",  "TRANSIT_DAY_KEY","EDW_UPDATED_DTM", "Y", "DW_TRANSACTION_ID",  "-", "monthly:EDW_UPDATED_DTM", "merge"),
 ("edw_device_metric",         "cdc_append", "TRANSIT_DAY_KEY","EDW_INSERTED_DTM","Y", "-", "-",                   "monthly:EDW_INSERTED_DTM","append"),
 ("ncs_stage_cashbox_tracking","cdc_append", "EVENT_DTM",      "INSERTED_DTM",    "Y", "-", "CASHBOX_EVENT_ID",    "monthly:INSERTED_DTM",    "append_dedupe"),
 ("ncs_stage_sale_transaction","cdc_append", "TRANSACTION_DTM","DW_INSERTED_DAY", "Y", "-", "DW_TRANSACTION_ID",   "daykey:DW_INSERTED_DAY",  "append_dedupe"),
 ("ncs_stage_device_end_of_day","cdc_merge", "TRANSIT_DAY_KEY","TRANSIT_DAY_KEY", "N", "DEVICE_ID,TRANSIT_DAY,LAST_EOD_DATE", "-", "monthly:TRANSIT_DAY_KEY", "merge"),
 ("ncs_stage_device_end_of_day_msg_count","cdc_merge","TRANSIT_DAY_KEY","TRANSIT_DAY_KEY","N","DEVICE_ID,TRANSIT_DAY,LAST_EOD_DATE,MESSAGE_NAME","-","single_pass_offhours","merge"),
 ("edw_use_transaction_daily", "append_aggregate","TRANSIT_DAY_KEY","TRANSIT_DAY_KEY","-","-","-","monthly_pushdown_aggregate","replaceWhere"),
 ("edw_kpi_detail_events_by_day","full_scoped","START_DTM","-","-","-","-","-","create_or_replace"),
] + [(t, "full", "-", "-", "-", "-", "-", "-", "create_or_replace") for t in
 ["edw_availability_events","edw_availability_relief","edw_device_dimension","edw_device_last_state",
  "edw_device_current_hw_config","edw_kpi_summary_by_day","edw_kpi_rules","edw_kpi","edw_kpi_target",
  "edw_event_type_dimension","edw_metric_dimension","ncs_stage_device","ncs_stage_device_type",
  "ncs_stage_event","ncs_stage_stop_point","ncs_stage_transit_facility",
  "cta_servicenow_availability_events","cta_servicenow_data_from_jumpbox","cta_kpi_agency_map",
  "cta_kpi_monthly_summary","cta_sldc_monthly_summary"]] + \
 [("cta_kpi_tvm_date_table", "drop_from_scope", "-", "-", "-", "-", "-", "-", "none")]

BAD_WM = {"LOAD_DATE","SRC_YEAR","YEAR","MONTH","DAY","LOAD_TS","LOAD_DTM","INGEST_DATE"}
for r in V2:
    for wm in (r[3],):
        assert not (wm.startswith("_") or wm.upper() in BAD_WM), f"REFUSED: audit column '{wm}' as watermark for {r[0]}"
print(f"NB10 | {len(V2)} measured rows | DRY_RUN={DRY_RUN}")

# COMMAND ----------
# ============================== CELL 2 : BACKUP + ADD COLUMNS + MERGE ==============================
ts = _dt.datetime.utcnow().strftime("%Y%m%d_%H%M%S")
existing = spark.table(CONTRACT)
have = {c.lower() for c in existing.columns}
NEW_COLS = [("pull_wm","string"),("pull_wm_indexed","string"),("merge_key","string"),("dedupe_key","string"),
            ("chunking","string"),("bronze_write","string"),("contract_version","string"),("measured_ts","string")]
if not DRY_RUN:
    spark.sql(f"CREATE TABLE mars_dev.audit.bronze_data_contract_backup_{ts} AS SELECT * FROM {CONTRACT}")
    print(f"backup -> bronze_data_contract_backup_{ts}")
    for c, typ in NEW_COLS:
        if c not in have:
            spark.sql(f"ALTER TABLE {CONTRACT} ADD COLUMNS ({c} {typ})")
    v2df = spark.createDataFrame(V2, "table string, load_strategy string, scope_col string, pull_wm string, "
                                     "pull_wm_indexed string, merge_key string, dedupe_key string, chunking string, bronze_write string") \
        .withColumn("contract_version", F.lit("v2_measured_02Sep2026")) \
        .withColumn("measured_ts", F.lit(_dt.datetime.utcnow().isoformat()))
    v2df.createOrReplaceTempView("_v2")
    spark.sql(f"""MERGE INTO {CONTRACT} t USING _v2 s ON t.table = s.table
                  WHEN MATCHED THEN UPDATE SET
                    t.load_strategy=s.load_strategy, t.scope_col=s.scope_col, t.pull_wm=s.pull_wm,
                    t.pull_wm_indexed=s.pull_wm_indexed, t.merge_key=s.merge_key, t.dedupe_key=s.dedupe_key,
                    t.chunking=s.chunking, t.bronze_write=s.bronze_write, t.status='measured_v2',
                    t.contract_version=s.contract_version, t.measured_ts=s.measured_ts""")
    n = spark.sql(f"SELECT COUNT(*) n FROM {CONTRACT} WHERE status='measured_v2'").first()["n"]
    print(f"APPLIED: {n} rows now status=measured_v2 (expected {len(V2)})")
else:
    print("DRY_RUN diff (current -> v2):")
    cur = {r["table"]: r.asDict() for r in existing.collect()}
    for r in V2:
        c = cur.get(r[0], {})
        print(f"  {r[0]:40s} {str(c.get('load_strategy')):10s}->{r[1]:16s} wm {str(c.get('insert_wm'))}/{str(c.get('update_wm'))} -> pull_wm={r[3]}")
    missing = [r[0] for r in V2 if r[0] not in cur]
    if missing: print("  !! not in contract yet (MERGE will skip; add via NB73 first):", missing)
print("Rollback: CREATE OR REPLACE TABLE mars_dev.audit.bronze_data_contract AS SELECT * FROM <backup table>")
