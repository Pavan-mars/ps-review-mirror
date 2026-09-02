# Databricks notebook source
# MAGIC %md
# MAGIC # 03 — SCHEMA DRIFT (Oracle vs raw vs bronze) + SAMPLE OF THE NEW INCREMENT
# MAGIC
# MAGIC Answers asks **4, 5 and 6** in one pass. Still **read-only** — nothing is written to raw, bronze or Oracle.
# MAGIC
# MAGIC | Cell | Output |
# MAGIC |---|---|
# MAGIC | 2 | Oracle column catalogue for the in-scope tables (authoritative side) |
# MAGIC | 3 | **Drift matrix**: per column — present in Oracle / raw / bronze, and type agreement |
# MAGIC | 4 | **Drift verdict** per table: `CLEAN` · `ADDITIVE` · `TYPE_CHANGE` · `BREAKING` · `LAYOUT` |
# MAGIC | 5 | **Sample of the new rows** (post-2026-04-12) straight from Oracle, PII-safe |
# MAGIC | 6 | **Downstream blast radius**: which silver/gold tables each drifting table feeds |
# MAGIC | 7 | paste-back JSON |
# MAGIC
# MAGIC Run **01** and **02** first — this notebook re-derives what it needs, but their output is the audit trail.

# COMMAND ----------
# ============================== CELL 1 : CONFIG (same connection rules as NB01) ======================
import datetime as _dt, json, re
from pyspark.sql import functions as F

ODS_HOST, ODS_PORT, SCOPE_SECRET = "10.3.10.30", 1521, "cubic"
SDU, CONNECT_TO, READ_TO, QUERY_TO = 512, "10000", "180000", 180
CAT, BRONZE_DB = "mars_dev", "mars_dev.bronze"
RAW_ROOT  = "s3://cubic-mars-pm-s3-datalake-dev-raw-170202974600/chicago_ventra"
INCR_FROM = "2026-04-12"
SENTINEL  = (_dt.date.today() + _dt.timedelta(days=1)).strftime("%Y-%m-%d")
SAMPLE_N  = 20

# bronze_table -> (OWNER, TABLE, raw_owner_dir, raw_table_dir).  Tier-1 only — drift on tables nothing
# reads is not urgent, and each Oracle round trip costs VPN time.
MAP = {
 "edw_device_event":                     ("EDW","DEVICE_EVENT","edw","device_event"),
 "edw_abp_tap":                          ("EDW","ABP_TAP","edw","abp_tap"),
 "edw_device_metric":                    ("EDW","DEVICE_METRIC","edw","device_metric"),
 "edw_availability_events":              ("EDW","AVAILABILITY_EVENTS","edw","availability_events"),
 "edw_availability_relief":              ("EDW","AVAILABILITY_RELIEF","edw","availability_relief"),
 "edw_device_dimension":                 ("EDW","DEVICE_DIMENSION","edw","device_dimension"),
 "edw_device_last_state":                ("EDW","DEVICE_LAST_STATE","edw","device_last_state"),
 "edw_device_current_hw_config":         ("EDW","DEVICE_CURRENT_HW_CONFIG","edw","device_current_hw_config"),
 "edw_read_transaction":                 ("EDW","READ_TRANSACTION","edw","read_transaction"),
 "edw_kpi_detail_events_by_day":         ("EDW","KPI_DETAIL_EVENTS_BY_DAY","edw","kpi_detail_events_by_day"),
 "edw_kpi_summary_by_day":               ("EDW","KPI_SUMMARY_BY_DAY","edw","kpi_summary_by_day"),
 "edw_kpi_rules":                        ("EDW","KPI_RULES","edw","kpi_rules"),
 "edw_kpi":                              ("EDW","KPI","edw","kpi"),
 "edw_kpi_target":                       ("EDW","KPI_TARGET","edw","kpi_target"),
 "edw_event_type_dimension":             ("EDW","EVENT_TYPE_DIMENSION","edw","event_type_dimension"),
 "edw_metric_dimension":                 ("EDW","METRIC_DIMENSION","edw","metric_dimension"),
 "ncs_stage_cashbox_tracking":           ("NCS_STAGE","CASHBOX_TRACKING","ncs_stage","cashbox_tracking"),
 "ncs_stage_sale_transaction":           ("NCS_STAGE","SALE_TRANSACTION","ncs_stage","sale_transaction"),
 "ncs_stage_device_end_of_day":          ("NCS_STAGE","DEVICE_END_OF_DAY","ncs_stage","device_end_of_day"),
 "ncs_stage_device_end_of_day_msg_count":("NCS_STAGE","DEVICE_END_OF_DAY_MSG_COUNT","ncs_stage","device_end_of_day_msg_count"),
 "ncs_stage_device":                     ("NCS_STAGE","DEVICE","ncs_stage","device"),
 "ncs_stage_device_type":                ("NCS_STAGE","DEVICE_TYPE","ncs_stage","device_type"),
 "ncs_stage_event":                      ("NCS_STAGE","EVENT","ncs_stage","event"),
 "ncs_stage_stop_point":                 ("NCS_STAGE","STOP_POINT","ncs_stage","stop_point"),
 "ncs_stage_transit_facility":           ("NCS_STAGE","TRANSIT_FACILITY","ncs_stage","transit_facility"),
 "cta_servicenow_availability_events":   ("CTA","SERVICENOW_AVAILABILITY_EVENTS","cta","servicenow_availability_events"),
 "cta_servicenow_data_from_jumpbox":     ("CTA","SERVICENOW_DATA_FROM_JUMPBOX","cta","servicenow_data_from_jumpbox"),
 "cta_kpi_agency_map":                   ("CTA","KPI_AGENCY_MAP","cta","kpi_agency_map"),
 "cta_kpi_monthly_summary":              ("CTA","KPI_MONTHLY_SUMMARY","cta","kpi_monthly_summary"),
 "cta_sldc_monthly_summary":             ("CTA","SLDC_MONTHLY_SUMMARY","cta","sldc_monthly_summary"),
}

# Which silver/gold objects read each bronze table — from repo sql/{silver,gold} FROM/JOIN analysis.
# Used in Cell 6 to turn "column X changed" into "these models are at risk".
DOWNSTREAM = {
 "edw_device_event":                     ["silver.device_event_enriched (SPINE -> all of L4-L6)"],
 "edw_abp_tap":                          ["silver.tap_event_daily"],
 "edw_device_metric":                    ["silver.metric_hourly","silver.metric_daily"],
 "edw_availability_events":              ["silver.kpi_avail_enriched","silver.maintenance_ledger","silver.device_failures (HUB)"],
 "edw_availability_relief":              ["silver.kpi_avail_enriched"],
 "edw_device_dimension":                 ["silver.dim_device (L1 root)"],
 "edw_device_last_state":                ["silver.device_uptime_intervals"],
 "edw_device_current_hw_config":         ["silver.hw_config_current"],
 "edw_read_transaction":                 ["silver.read_tap_daily"],
 "edw_kpi_detail_events_by_day":         ["silver.kpi_daily"],
 "edw_kpi_summary_by_day":               ["silver.kpi_daily"],
 "edw_kpi_rules":                        ["silver.dim_failure_level","silver.kpi_daily","gold.device_ps3_incident"],
 "edw_kpi":                              ["silver.kpi_daily","silver.kpi_monthly_benchmark","gold.device_ps3_incident"],
 "edw_kpi_target":                       ["silver.kpi_daily"],
 "edw_event_type_dimension":             ["silver.dim_event_type"],
 "edw_metric_dimension":                 ["silver.metric_hourly","silver.metric_daily"],
 "ncs_stage_cashbox_tracking":           ["gold.device_ps2_chains","gold.device_ps5_component"],
 "ncs_stage_sale_transaction":           ["silver.tvm_sale_daily"],
 "ncs_stage_device_end_of_day":          ["silver.device_uptime_intervals"],
 "ncs_stage_device_end_of_day_msg_count":["silver.device_uptime_intervals"],
 "ncs_stage_device":                     ["silver.dim_device"],
 "ncs_stage_device_type":                ["silver.dim_device"],
 "ncs_stage_event":                      ["silver.dim_event_type"],
 "ncs_stage_stop_point":                 ["silver.dim_stop_point"],
 "ncs_stage_transit_facility":           ["silver.dim_facility"],
 "cta_servicenow_availability_events":   ["silver.kpi_avail_enriched","silver.incident_root_cause"],
 "cta_servicenow_data_from_jumpbox":     ["silver.kpi_avail_enriched","silver.incident_root_cause"],
 "cta_kpi_agency_map":                   ["silver.kpi_daily"],
 "cta_kpi_monthly_summary":              ["silver.kpi_monthly_benchmark"],
 "cta_sldc_monthly_summary":             ["silver.kpi_daily"],
}

# Oracle -> Spark type families, for "is this the same type or not"
def fam_ora(t):
    t = (t or "").upper()
    if t.startswith("TIMESTAMP") or t == "DATE":                     return "temporal"
    if t in ("NUMBER","FLOAT","BINARY_FLOAT","BINARY_DOUBLE","INTEGER"): return "numeric"
    if t in ("VARCHAR2","CHAR","NVARCHAR2","NCHAR","CLOB","LONG"):   return "string"
    if t in ("RAW","BLOB"):                                          return "binary"
    return f"other:{t}"
def fam_spark(t):
    t = (t or "").lower()
    if t in ("date","timestamp","timestamp_ntz"):                    return "temporal"
    if t.startswith("decimal") or t in ("int","bigint","smallint","tinyint","double","float"): return "numeric"
    if t == "string":                                                return "string"
    if t == "binary":                                                return "binary"
    if t == "boolean":                                               return "numeric"
    return f"other:{t}"

_C = None
def _creds():
    global _C
    if _C is None:
        svc = dbutils.secrets.get(SCOPE_SECRET, "ods_service")
        url = (f"jdbc:oracle:thin:@(DESCRIPTION=(SDU={SDU})(ADDRESS=(PROTOCOL=TCP)"
               f"(HOST={ODS_HOST})(PORT={ODS_PORT}))(CONNECT_DATA=(SERVICE_NAME={svc})))") if SDU \
              else f"jdbc:oracle:thin:@//{ODS_HOST}:{ODS_PORT}/{svc}"
        _C = {"url": url, "user": dbutils.secrets.get(SCOPE_SECRET, "ods_user"),
              "pwd": dbutils.secrets.get(SCOPE_SECRET, "ods_pwd")}
    return _C
def ora(q, secs=QUERY_TO):
    c = _creds()
    return (spark.read.format("jdbc").option("url", c["url"]).option("dbtable", q)
            .option("user", c["user"]).option("password", c["pwd"])
            .option("driver","oracle.jdbc.OracleDriver").option("queryTimeout", str(secs))
            .option("oracle.net.CONNECT_TIMEOUT", CONNECT_TO).option("oracle.jdbc.ReadTimeout", READ_TO)
            .option("sessionInitStatement","ALTER SESSION SET NLS_DATE_FORMAT='YYYY-MM-DD HH24:MI:SS'").load())

try:
    ora("(SELECT 1 OK FROM dual) q", 20).collect(); ORACLE_UP = True
except Exception as e:
    ORACLE_UP = False; print("Oracle logon FAILED:", str(e).splitlines()[0][:150])
print(f"03_SCHEMA_DRIFT | {len(MAP)} tables | Oracle={'UP' if ORACLE_UP else 'DOWN'}")

# COMMAND ----------
# ============================== CELL 2 : ORACLE COLUMN CATALOGUE ==============================
assert ORACLE_UP, "Oracle down — bring the VPN up; drift cannot be measured one-sided."
ORA_COLS = {}
for owner in sorted({v[0] for v in MAP.values()}):
    tabs = sorted({v[1] for v in MAP.values() if v[0] == owner})
    lst  = ",".join(f"'{t}'" for t in tabs)
    q = (f"(SELECT TABLE_NAME, COLUMN_NAME, DATA_TYPE, NULLABLE, DATA_LENGTH, DATA_PRECISION, "
         f"DATA_SCALE, COLUMN_ID FROM ALL_TAB_COLUMNS "
         f"WHERE OWNER='{owner}' AND TABLE_NAME IN ({lst})) q")
    for r in ora(q).orderBy("TABLE_NAME","COLUMN_ID").collect():
        ORA_COLS.setdefault(f"{owner}.{r['TABLE_NAME']}", []).append(
            {"c": r["COLUMN_NAME"], "t": r["DATA_TYPE"], "null": r["NULLABLE"],
             "len": r["DATA_LENGTH"], "p": r["DATA_PRECISION"], "s": r["DATA_SCALE"]})
print(f"Oracle catalogue: {len(ORA_COLS)} tables, {sum(len(v) for v in ORA_COLS.values())} columns")

# COMMAND ----------
# ============================== CELL 3 : DRIFT MATRIX ==============================
AUDIT_OK = ("_ingest_ts","_raw_ingest_ts","_batch_id","_raw_batch_id","_bronze_ingest_ts",
            "_bronze_batch_id","_source_system","_source_table","_source_file","load_ts","load_dtm",
            "ingest_date","year","month","day")
MATRIX, TABLE_DRIFT = {}, {}

for b, (o, t, ro, rt) in sorted(MAP.items()):
    src = f"{o}.{t}"
    oc = {c["c"].upper(): c for c in ORA_COLS.get(src, [])}
    try:
        bs = {f.name.upper(): f.dataType.simpleString() for f in spark.table(f"{BRONZE_DB}.`{b}`").schema.fields}
    except Exception as e:
        TABLE_DRIFT[b] = {"verdict": "BRONZE_MISSING", "detail": str(e).splitlines()[0][:80]}
        MATRIX[b] = []; continue
    try:
        rs = {f.name.upper(): f.dataType.simpleString() for f in spark.read.parquet(f"{RAW_ROOT}/{ro}/{rt}/").schema.fields}
        raw_ok = True
    except Exception as e:
        rs, raw_ok = {}, False
        raw_err = str(e).splitlines()[0][:120]

    rows, added, dropped, retyped = [], [], [], []
    for c in sorted(set(oc) | set(bs) | set(rs)):
        in_o, in_r, in_b = c in oc, c in rs, c in bs
        is_audit = c.lower() in AUDIT_OK or c.startswith("_")
        of = fam_ora(oc[c]["t"]) if in_o else None
        bf = fam_spark(bs[c])   if in_b else None
        same = (of == bf) if (in_o and in_b) else None
        rows.append({"col": c, "oracle": oc[c]["t"] if in_o else None,
                     "raw": rs.get(c), "bronze": bs.get(c),
                     "type_family_match": same, "audit": is_audit})
        if in_o and not in_b and not is_audit:  added.append(c)     # NEW in Oracle -> bronze must widen
        if in_b and not in_o and not is_audit:  dropped.append(c)   # gone from Oracle -> silver may break
        if same is False and not is_audit:      retyped.append(f"{c}:{oc[c]['t']}->{bs[c]}")

    if not raw_ok:                       verdict = "LAYOUT"          # raw prefix unreadable as one DF
    elif retyped:                        verdict = "TYPE_CHANGE"
    elif dropped:                        verdict = "BREAKING"
    elif added:                          verdict = "ADDITIVE"
    else:                                verdict = "CLEAN"
    TABLE_DRIFT[b] = {"verdict": verdict, "new_in_oracle": added, "missing_from_oracle": dropped,
                      "retyped": retyped, "raw_readable": raw_ok,
                      **({"raw_error": raw_err} if not raw_ok else {}),
                      "n_oracle": len(oc), "n_raw": len(rs), "n_bronze": len(bs)}
    MATRIX[b] = rows
    print(f"{b:40s} {verdict:12s} ora={len(oc):3d} raw={len(rs):3d} bronze={len(bs):3d} "
          f"| +{len(added)} -{len(dropped)} ~{len(retyped)}")

# COMMAND ----------
# ============================== CELL 4 : DRIFT VERDICT DETAIL ==============================
order = {"BREAKING":0,"TYPE_CHANGE":1,"LAYOUT":2,"ADDITIVE":3,"BRONZE_MISSING":4,"CLEAN":5}
for b in sorted(TABLE_DRIFT, key=lambda x: (order.get(TABLE_DRIFT[x]["verdict"], 9), x)):
    d = TABLE_DRIFT[b]
    if d["verdict"] == "CLEAN":
        continue
    print(f"\n### {b}  ->  {d['verdict']}")
    if d.get("new_in_oracle"):       print(f"    NEW in Oracle, absent in bronze : {', '.join(d['new_in_oracle'])}")
    if d.get("missing_from_oracle"): print(f"    In bronze, GONE from Oracle    : {', '.join(d['missing_from_oracle'])}")
    if d.get("retyped"):             print(f"    TYPE CHANGED                   : {', '.join(d['retyped'])}")
    if not d.get("raw_readable", True): print(f"    RAW UNREADABLE                 : {d.get('raw_error')}")
print("\nCLEAN:", ", ".join(b for b in sorted(TABLE_DRIFT) if TABLE_DRIFT[b]["verdict"] == "CLEAN") or "(none)")

# COMMAND ----------
# ============================== CELL 5 : SAMPLE THE NEW INCREMENT (post 2026-04-12) =================
# Pulls a bounded sample of the ACTUAL new rows so the data can be eyeballed, not just counted.
# String columns whose name suggests free text / person data are hashed before display.
PII_HINT = ("CALLER","NAME","EMAIL","PHONE","NOTE","DESCRIPTION","COMMENT","ASSIGNED","USER","ADDRESS")
WM_PREF  = ["EDW_UPDATED_DTM","DW_UPDATED_DTM","UPDATED_DTM","EDW_INSERTED_DTM","DW_INSERTED_DTM",
            "INSERTED_DTM","SUMM_DTM","REPORTED_CHANGED_DTM","EVENT_DTM","TRANSACTION_DTM","START_DTM"]
KEY_PREF = ["TRANSIT_DAY_KEY","EVENT_DAY_KEY","POSTING_DAY_KEY"]

SAMPLES = {}
for b, (o, t, ro, rt) in sorted(MAP.items()):
    cols = [c["c"].upper() for c in ORA_COLS.get(f"{o}.{t}", [])]
    if not cols:
        SAMPLES[b] = {"note": "no Oracle columns"}; continue
    wm  = next((c for c in WM_PREF  if c in cols), None)
    key = next((c for c in KEY_PREF if c in cols), None)
    if wm:
        pred = f"{wm} >= TIMESTAMP '{INCR_FROM} 00:00:00' AND {wm} < DATE '{SENTINEL}'"
    elif key:
        pred = f"{key} >= {int(INCR_FROM.replace('-',''))} AND {key} < {int(SENTINEL.replace('-',''))}"
    else:
        SAMPLES[b] = {"note": "static/reference table — full reload, no increment to sample"}; continue
    try:
        df = ora(f"(SELECT * FROM {o}.{t} WHERE {pred} AND ROWNUM <= {SAMPLE_N}) q", 240)
        for c in df.columns:
            if any(h in c.upper() for h in PII_HINT) and dict(df.dtypes)[c] == "string":
                df = df.withColumn(c, F.sha2(F.col(c).cast("string"), 256).substr(1, 12))
        recs = [r.asDict() for r in df.limit(SAMPLE_N).collect()]
        SAMPLES[b] = {"predicate": pred, "n": len(recs), "rows": recs}
        print(f"\n### {b}  ({len(recs)} sample rows, {pred})")
        if recs:
            display(df.limit(SAMPLE_N))
        else:
            print("    !! ZERO new rows — Cubic's increment has not reached this table")
    except Exception as e:
        SAMPLES[b] = {"predicate": pred, "error": str(e).splitlines()[0][:120]}
        print(f"{b:40s} SAMPLE ERROR {str(e).splitlines()[0][:90]}")

# COMMAND ----------
# ============================== CELL 6 : BLAST RADIUS ==============================
print(f"{'bronze table':40s} {'verdict':13s} downstream at risk")
print("-" * 130)
BLAST = {}
for b in sorted(TABLE_DRIFT, key=lambda x: (order.get(TABLE_DRIFT[x]["verdict"], 9), x)):
    v = TABLE_DRIFT[b]["verdict"]
    if v == "CLEAN":
        continue
    ds = DOWNSTREAM.get(b, ["(nothing downstream — ingestion-only)"])
    BLAST[b] = {"verdict": v, "downstream": ds}
    print(f"{b:40s} {v:13s} {'; '.join(ds)}")
if not BLAST:
    print("no drift — no downstream impact")

# COMMAND ----------
# ============================== CELL 7 : PASTE-BACK BLOCK ==============================
out = {"probe": "03_SCHEMA_DRIFT_and_sample",
       "run_ts": _dt.datetime.utcnow().isoformat() + "Z",
       "incr_from": INCR_FROM, "sentinel": SENTINEL,
       "table_drift": TABLE_DRIFT, "drift_matrix": MATRIX,
       "samples": SAMPLES, "blast_radius": BLAST}
print("=" * 100)
print("<<<PROBE03_JSON_START>>>")
print(json.dumps(out, default=str))
print("<<<PROBE03_JSON_END>>>")
print("=" * 100)
