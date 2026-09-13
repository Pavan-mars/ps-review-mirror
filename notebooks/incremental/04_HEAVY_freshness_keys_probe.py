# Databricks notebook source
# MAGIC %md
# MAGIC # 04 — HEAVY-TABLE FRESHNESS + KEYS + INDEX PROBE  (read-only; closes probe-01's gaps)
# MAGIC
# MAGIC Probe 01 (run 02-Sep-2026) settled 29 of 41 tables but **12 heavy tables timed out with ORA-01013**,
# MAGIC because their COUNT ran on the *unindexed* update watermark (a remote full scan). This probe fixes that
# MAGIC and closes the remaining unknowns, using only fast dictionary + indexed-range queries:
# MAGIC
# MAGIC | Cell | What | Why |
# MAGIC |---|---|---|
# MAGIC | 3 | `ALL_INDEXES` + `ALL_IND_COLUMNS` for all 12 | proves which date column is INDEXED → the chunk column |
# MAGIC | 4 | `ALL_CONSTRAINTS` P/U keys for the whole 41-table scope | settles merge keys (or proves none exist) |
# MAGIC | 5 | `ALL_TABLES.NUM_ROWS` + `LAST_ANALYZED` | totals from optimizer stats — instant, no COUNT(*) |
# MAGIC | 6 | freshness on the **indexed business column** (`>= 2026-04-12`) + monthly shape | the number probe 01 could not get |
# MAGIC | 7 | fixes: `cta_kpi_tvm_date_table.DT` as DATE; jumpbox VARCHAR dates | the two datatype errors from probe 01 |
# MAGIC | 8 | results → `mars_dev.audit.probe_results` (Delta) + chunked stdout | Databricks truncated probe 01/02/03 stdout at ~50KB — never print one giant JSON again |
# MAGIC
# MAGIC Read-only against Oracle; the ONLY write is the audit results table.

# COMMAND ----------
# ============================== CELL 1 : CONFIG ==============================
import datetime as _dt, json
from pyspark.sql import functions as F

ODS_HOST, ODS_PORT, SCOPE = "10.3.10.30", 1521, "cubic"
SDU, CONNECT_TO, READ_TO = 512, "10000", "300000"
QUERY_TO   = 300
INCR_FROM  = "2026-04-12"
SENTINEL   = (_dt.date.today() + _dt.timedelta(days=1)).strftime("%Y-%m-%d")
RESULTS_TB = "mars_dev.audit.probe_results"

# The 12 that timed out in probe 01, with the INDEX-CANDIDATE business column to try first.
# (candidate col, kind: dtm|daykey). Cell 3 verifies the index before Cell 6 trusts it.
HEAVY = [
 ("edw_device_event",                     "EDW",      "DEVICE_EVENT",                "EVENT_DTM",            "dtm"),
 ("edw_abp_tap",                          "EDW",      "ABP_TAP",                     "TRANSACTION_DTM",      "dtm"),
 ("edw_device_metric",                    "EDW",      "DEVICE_METRIC",               "TRANSIT_DAY_KEY",      "daykey"),
 ("edw_read_transaction",                 "EDW",      "READ_TRANSACTION",            "TRANSIT_DAY_KEY",      "daykey"),
 ("edw_kpi_summary_by_day",               "EDW",      "KPI_SUMMARY_BY_DAY",          "TRANSIT_DAY_KEY",      "daykey"),
 ("ncs_stage_cashbox_tracking",           "NCS_STAGE","CASHBOX_TRACKING",            "EVENT_DTM",            "dtm"),
 ("ncs_stage_sale_transaction",           "NCS_STAGE","SALE_TRANSACTION",            "TRANSACTION_DTM",      "dtm"),
 ("ncs_stage_device_end_of_day",          "NCS_STAGE","DEVICE_END_OF_DAY",           "TRANSIT_DAY_KEY",      "daykey"),
 ("ncs_stage_device_end_of_day_msg_count","NCS_STAGE","DEVICE_END_OF_DAY_MSG_COUNT", "TRANSIT_DAY_KEY",      "daykey"),
 ("ncs_stage_device_event_history",       "NCS_STAGE","DEVICE_EVENT_HISTORY",        "EVENT_DTM",            "dtm"),
 ("edw_metric_summary_by_day",            "EDW",      "METRIC_SUMMARY_BY_DAY",       "TRANSIT_DAY_KEY",      "daykey"),
 ("cta_abp_use_tran_timing_data",         "CTA",      "ABP_USE_TRAN_TIMING_DATA",    "TRANSACTION_DTM_HOUR", "dtm"),
]
# Whole 41-table scope for key/stats discovery (dictionary queries are cheap).
ALL41 = [("EDW", t) for t in
  ["DEVICE_EVENT","ABP_TAP","DEVICE_METRIC","AVAILABILITY_EVENTS","AVAILABILITY_RELIEF","DEVICE_DIMENSION",
   "DEVICE_LAST_STATE","DEVICE_CURRENT_HW_CONFIG","READ_TRANSACTION","KPI_DETAIL_EVENTS_BY_DAY",
   "KPI_SUMMARY_BY_DAY","KPI_RULES","KPI","KPI_TARGET","EVENT_TYPE_DIMENSION","METRIC_DIMENSION",
   "METRIC_SUMMARY_BY_DAY","DEVICE_LAST_SET_EVENT","DEVICE_LOCATION_HISTORY","DEVICE_CURRENT_TABLES",
   "DEVICE_CURRENT_SW_CONFIG","DATE_DIMENSION"]] + \
 [("NCS_STAGE", t) for t in
  ["CASHBOX_TRACKING","SALE_TRANSACTION","DEVICE_END_OF_DAY","DEVICE_END_OF_DAY_MSG_COUNT","DEVICE",
   "DEVICE_TYPE","EVENT","STOP_POINT","TRANSIT_FACILITY","DEVICE_EVENT_HISTORY",
   "SALE_TRANSACTION_DEVICE_MSG","CASHBOX_MANUAL_COUNTS"]] + \
 [("CTA", t) for t in
  ["SERVICENOW_AVAILABILITY_EVENTS","SERVICENOW_DATA_FROM_JUMPBOX","KPI_AGENCY_MAP","KPI_MONTHLY_SUMMARY",
   "SLDC_MONTHLY_SUMMARY","KPI_TVM_DATE_TABLE","ABP_USE_TRAN_TIMING_DATA"]]

_C = None
def _creds():
    global _C
    if _C is None:
        svc = dbutils.secrets.get(SCOPE, "ods_service")
        url = (f"jdbc:oracle:thin:@(DESCRIPTION=(SDU={SDU})(ADDRESS=(PROTOCOL=TCP)"
               f"(HOST={ODS_HOST})(PORT={ODS_PORT}))(CONNECT_DATA=(SERVICE_NAME={svc})))")
        _C = {"url": url, "user": dbutils.secrets.get(SCOPE, "ods_user"),
              "pwd": dbutils.secrets.get(SCOPE, "ods_pwd")}
    return _C
def ora(q, secs=QUERY_TO):
    c = _creds()
    return (spark.read.format("jdbc").option("url", c["url"]).option("dbtable", q)
            .option("user", c["user"]).option("password", c["pwd"])
            .option("driver", "oracle.jdbc.OracleDriver").option("queryTimeout", str(secs))
            .option("oracle.net.CONNECT_TIMEOUT", CONNECT_TO).option("oracle.jdbc.ReadTimeout", READ_TO)
            .option("sessionInitStatement", "ALTER SESSION SET NLS_DATE_FORMAT='YYYY-MM-DD HH24:MI:SS'")
            .load())

def in_list(owner):
    return ",".join(f"'{t}'" for o, t in ALL41 if o == owner)

print(f"04_HEAVY_freshness_keys_probe | {len(HEAVY)} heavy + {len(ALL41)} dictionary scope | window {INCR_FROM}..{SENTINEL}")

# COMMAND ----------
# ============================== CELL 2 : PREFLIGHT ==============================
try:
    ora("(SELECT 1 OK FROM dual) q", 20).collect(); print("Oracle logon OK")
except Exception as e:
    raise AssertionError(f"Oracle unreachable: {str(e).splitlines()[0][:120]}")

# COMMAND ----------
# ============================== CELL 3 : WHICH COLUMNS ARE ACTUALLY INDEXED ==============================
IDX = {}
for owner in ("EDW", "NCS_STAGE", "CTA"):
    q = (f"(SELECT ic.TABLE_NAME, ic.INDEX_NAME, ic.COLUMN_NAME, ic.COLUMN_POSITION, i.UNIQUENESS "
         f"FROM ALL_IND_COLUMNS ic JOIN ALL_INDEXES i "
         f"  ON i.OWNER = ic.INDEX_OWNER AND i.INDEX_NAME = ic.INDEX_NAME "
         f"WHERE ic.TABLE_OWNER = '{owner}' AND ic.TABLE_NAME IN ({in_list(owner)})) q")
    for r in ora(q).collect():
        IDX.setdefault(f"{owner}.{r['TABLE_NAME']}", []).append(
            (r["INDEX_NAME"], int(r["COLUMN_POSITION"]), r["COLUMN_NAME"], r["UNIQUENESS"]))

print(f"{'table':44s} leading indexed columns (position 1)")
print("-" * 110)
LEAD = {}
for k in sorted(IDX):
    lead = sorted({c for _, pos, c, _ in IDX[k] if pos == 1})
    LEAD[k] = lead
    print(f"{k:44s} {', '.join(lead) or '(none)'}")
for k in sorted({f"{o}.{t}" for o, t in ALL41} - set(IDX)):
    LEAD[k] = []
    print(f"{k:44s} (NO INDEXES AT ALL)")

print("\nHEAVY chunk-column verdicts:")
CHUNK_OK = {}
for b, o, t, col, kind in HEAVY:
    ok = col in LEAD.get(f"{o}.{t}", [])
    CHUNK_OK[b] = ok
    print(f"  {b:40s} {col:22s} {'INDEXED - safe to chunk' if ok else 'NOT a leading index column - chunk with care / expect scans'}")

# COMMAND ----------
# ============================== CELL 4 : PRIMARY / UNIQUE KEYS (merge keys) ==============================
KEYS = {}
for owner in ("EDW", "NCS_STAGE", "CTA"):
    q = (f"(SELECT c.TABLE_NAME, c.CONSTRAINT_NAME, c.CONSTRAINT_TYPE, cc.COLUMN_NAME, cc.POSITION "
         f"FROM ALL_CONSTRAINTS c JOIN ALL_CONS_COLUMNS cc "
         f"  ON cc.OWNER = c.OWNER AND cc.CONSTRAINT_NAME = c.CONSTRAINT_NAME "
         f"WHERE c.OWNER = '{owner}' AND c.CONSTRAINT_TYPE IN ('P','U') "
         f"  AND c.TABLE_NAME IN ({in_list(owner)})) q")
    for r in ora(q).orderBy("TABLE_NAME", "CONSTRAINT_NAME", "POSITION").collect():
        KEYS.setdefault(f"{owner}.{r['TABLE_NAME']}", {}).setdefault(
            (r["CONSTRAINT_NAME"], r["CONSTRAINT_TYPE"]), []).append(r["COLUMN_NAME"])

print(f"{'table':44s} P/U keys")
print("-" * 110)
for o, t in ALL41:
    k = f"{o}.{t}"
    if k in KEYS:
        for (cn, ct), cols in KEYS[k].items():
            print(f"{k:44s} [{ct}] {cn}: {', '.join(cols)}")
    else:
        print(f"{k:44s} NO P/U CONSTRAINT -> no reliable merge key; use append + window-refresh")

# COMMAND ----------
# ============================== CELL 5 : TOTALS FROM OPTIMIZER STATS (instant) ==============================
STATS = {}
for owner in ("EDW", "NCS_STAGE", "CTA"):
    q = (f"(SELECT TABLE_NAME, NUM_ROWS, LAST_ANALYZED FROM ALL_TABLES "
         f"WHERE OWNER='{owner}' AND TABLE_NAME IN ({in_list(owner)})) q")
    for r in ora(q).collect():
        STATS[f"{owner}.{r['TABLE_NAME']}"] = {"num_rows": r["NUM_ROWS"], "last_analyzed": str(r["LAST_ANALYZED"])}
print(f"{'table':44s} {'stats NUM_ROWS':>16s}  LAST_ANALYZED   (approximate — optimizer stats, not COUNT)")
print("-" * 110)
for k in sorted(STATS):
    s = STATS[k]
    n = f"{int(s['num_rows']):,}" if s["num_rows"] is not None else "?"
    print(f"{k:44s} {n:>16s}  {s['last_analyzed']}")

# COMMAND ----------
# ============================== CELL 6 : HEAVY FRESHNESS on the indexed business column ==============================
LIM_KEY = int(SENTINEL.replace("-", ""))
HFRESH = []
for b, o, t, col, kind in HEAVY:
    try:
        if kind == "daykey":
            pred  = f"{col} >= {int(INCR_FROM.replace('-',''))} AND {col} < {LIM_KEY}"
            mmq   = f"(SELECT MAX({col}) HI FROM {o}.{t} WHERE {col} < {LIM_KEY}) q"
            shq   = (f"(SELECT FLOOR({col}/100) YM, COUNT(*) N FROM {o}.{t} WHERE {pred} GROUP BY FLOOR({col}/100)) q")
        else:
            pred  = f"{col} >= TIMESTAMP '{INCR_FROM} 00:00:00' AND {col} < DATE '{SENTINEL}'"
            mmq   = f"(SELECT MAX({col}) HI FROM {o}.{t} WHERE {col} >= TIMESTAMP '{INCR_FROM} 00:00:00' AND {col} < DATE '{SENTINEL}') q"
            shq   = (f"(SELECT TO_CHAR({col},'YYYYMM') YM, COUNT(*) N FROM {o}.{t} WHERE {pred} GROUP BY TO_CHAR({col},'YYYYMM')) q")
        inc = int(ora(f"(SELECT COUNT(*) N FROM {o}.{t} WHERE {pred}) q").collect()[0]["N"])
        hi  = ora(mmq).collect()[0]["HI"]
        sh  = {str(r["YM"]): int(r["N"]) for r in ora(shq).collect()} if inc else {}
        HFRESH.append({"bronze": b, "src": f"{o}.{t}", "chunk_col": col, "indexed": CHUNK_OK.get(b),
                       "incr_since_0412": inc, "max": str(hi), "monthly": sh})
        print(f"{b:40s} {col:22s} incr={inc:>13,}  max={hi}  " +
              ("  ".join(f"{k}:{v:,}" for k, v in sorted(sh.items())) if sh else "(no new rows)"))
    except Exception as e:
        HFRESH.append({"bronze": b, "src": f"{o}.{t}", "chunk_col": col,
                       "error": str(e).splitlines()[0][:100]})
        print(f"{b:40s} ERROR {str(e).splitlines()[0][:90]}")

# COMMAND ----------
# ============================== CELL 7 : THE TWO DATATYPE FIXES FROM PROBE 01 ==============================
FIX = {}
try:  # DT is a DATE (probe 01 compared it to a number -> ORA-00932)
    r = ora(f"(SELECT COUNT(*) N FROM CTA.KPI_TVM_DATE_TABLE WHERE DT >= DATE '{INCR_FROM}' AND DT < DATE '{SENTINEL}') q").collect()[0]
    m = ora(f"(SELECT MIN(DT) LO, MAX(DT) HI FROM CTA.KPI_TVM_DATE_TABLE) q").collect()[0]
    FIX["cta_kpi_tvm_date_table"] = {"incr_since_0412": int(r["N"]), "min": str(m["LO"]), "max": str(m["HI"])}
    print(f"cta_kpi_tvm_date_table    DT as DATE: incr={int(r['N']):,}  range=[{m['LO']} .. {m['HI']}]")
except Exception as e:
    FIX["cta_kpi_tvm_date_table"] = {"error": str(e).splitlines()[0][:100]}; print("kpi_tvm_date_table ERROR", e)
try:  # jumpbox stores dates as VARCHAR (ORA-01861) — inspect the raw string format, don't cast
    r = ora("(SELECT U_START_DTM FROM CTA.SERVICENOW_DATA_FROM_JUMPBOX WHERE U_START_DTM IS NOT NULL AND ROWNUM <= 5) q").collect()
    FIX["cta_servicenow_data_from_jumpbox"] = {"u_start_dtm_samples": [str(x["U_START_DTM"]) for x in r],
                                               "ruling": "VARCHAR date -> full reload (604 rows); parse in bronze, never push a date predicate"}
    print("cta_servicenow_data_from_jumpbox U_START_DTM samples:", [str(x["U_START_DTM"]) for x in r])
except Exception as e:
    FIX["cta_servicenow_data_from_jumpbox"] = {"error": str(e).splitlines()[0][:100]}; print("jumpbox ERROR", e)

# COMMAND ----------
# ============================== CELL 8 : PERSIST RESULTS (no giant stdout — probe 01/02/03 lesson) =====
out = {"probe": "04_HEAVY_freshness_keys_probe",
       "run_ts": _dt.datetime.utcnow().isoformat() + "Z",
       "incr_from": INCR_FROM, "sentinel": SENTINEL,
       "leading_index_cols": LEAD, "keys": {k: {f"{ct}:{cn}": cols for (cn, ct), cols in v.items()} for k, v in KEYS.items()},
       "stats": STATS, "heavy_freshness": HFRESH, "fixes": FIX}
payload = json.dumps(out, default=str)
spark.sql("CREATE SCHEMA IF NOT EXISTS mars_dev.audit")
(spark.createDataFrame([(out["probe"], out["run_ts"], payload)], "probe string, run_ts string, payload string")
 .write.format("delta").mode("append").saveAsTable(RESULTS_TB))
print(f"persisted -> {RESULTS_TB} (run_ts={out['run_ts']}, {len(payload):,} chars)")
print("Retrieve any time:  SELECT payload FROM mars_dev.audit.probe_results WHERE probe='04_HEAVY_freshness_keys_probe' ORDER BY run_ts DESC LIMIT 1")
# chunked stdout, 40KB parts — Databricks truncates a single big print at ~50KB
CH = 40000
print(f"<<<PROBE04_JSON parts={ (len(payload)+CH-1)//CH }>>>")
for i in range(0, len(payload), CH):
    print(f"<<<PART {i//CH + 1}>>>")
    print(payload[i:i+CH])
print("<<<PROBE04_JSON_END>>>")
