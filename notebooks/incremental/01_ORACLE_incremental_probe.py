# Databricks notebook source
# MAGIC %md
# MAGIC # 01 — ORACLE INCREMENTAL PROBE  (read-only, Chicago ODS)
# MAGIC
# MAGIC **Purpose.** Answer, from Oracle itself, four questions for every in-scope table:
# MAGIC
# MAGIC 1. Does the table exist? (settles `EDW.READ_TRANSACTION`)
# MAGIC 2. What DATE/TIMESTAMP + day-key columns does it *really* have? (settles the watermark, from the
# MAGIC    ORACLE side — never from the bronze DataFrame; that inversion is what created the
# MAGIC    `_ingest_ts` defect, see `CUBIC_MARS_Chicago_Bronze_Watermark_and_RawFormat_Resolution_v1_28Aug2026.md` §2)
# MAGIC 3. How many rows landed **on/after 2026-04-12** (the day after the frozen watermark)?
# MAGIC 4. What is the per-month shape of that increment, and the true MIN/MAX?
# MAGIC
# MAGIC **Nothing is written.** No DDL, no S3, no bronze. Safe to Run All.
# MAGIC
# MAGIC Prereqs: cluster with `oracle.jdbc.OracleDriver`; secret scope `cubic`
# MAGIC (`ods_user`,`ods_pwd`,`ods_service`); VPN up to `10.3.10.30:1521`.
# MAGIC Paste the **PASTE-BACK BLOCK** from the last cell into the Cowork session.

# COMMAND ----------
# ============================== CELL 1 : CONFIG ==============================
import datetime as _dt, json
from pyspark.sql import functions as F

ODS_HOST, ODS_PORT, SCOPE = "10.3.10.30", 1521, "cubic"
SDU          = 512           # MTU black-hole band-aid (28-Aug finding). Set None once MSS clamp is live.
CONNECT_TO   = "10000"       # ms  — oracle.net.CONNECT_TIMEOUT
READ_TO      = "180000"      # ms  — oracle.jdbc.ReadTimeout  (BOTH are required, see ingestion/README)
QUERY_TO     = 180           # s   — queryTimeout

INCR_FROM    = "2026-04-12"                                   # day after the frozen ODS watermark
SENTINEL     = (_dt.date.today() + _dt.timedelta(days=1)).strftime("%Y-%m-%d")
INCR_FROM_KEY= int(INCR_FROM.replace("-", ""))                # 20260412, for *_DAY_KEY integer columns

# (bronze_table, OWNER, TABLE) — tier-1 in-scope set, 31 tables.
# Derived from: repo sql/{silver,gold} FROM/JOIN analysis + bronze_61_manifest_seed.csv + wave1_final_config.csv
TABLES = [
 ("edw_device_event",                     "EDW",      "DEVICE_EVENT"),
 ("edw_abp_tap",                          "EDW",      "ABP_TAP"),
 ("edw_device_metric",                    "EDW",      "DEVICE_METRIC"),
 ("edw_availability_events",              "EDW",      "AVAILABILITY_EVENTS"),
 ("edw_availability_relief",              "EDW",      "AVAILABILITY_RELIEF"),
 ("edw_device_dimension",                 "EDW",      "DEVICE_DIMENSION"),
 ("edw_device_last_state",                "EDW",      "DEVICE_LAST_STATE"),
 ("edw_device_current_hw_config",         "EDW",      "DEVICE_CURRENT_HW_CONFIG"),
 ("edw_read_transaction",                 "EDW",      "READ_TRANSACTION"),          # existence DISPUTED
 ("edw_kpi_detail_events_by_day",         "EDW",      "KPI_DETAIL_EVENTS_BY_DAY"),
 ("edw_kpi_summary_by_day",               "EDW",      "KPI_SUMMARY_BY_DAY"),
 ("edw_kpi_rules",                        "EDW",      "KPI_RULES"),
 ("edw_kpi",                              "EDW",      "KPI"),
 ("edw_kpi_target",                       "EDW",      "KPI_TARGET"),
 ("edw_event_type_dimension",             "EDW",      "EVENT_TYPE_DIMENSION"),
 ("edw_metric_dimension",                 "EDW",      "METRIC_DIMENSION"),
 ("ncs_stage_cashbox_tracking",           "NCS_STAGE","CASHBOX_TRACKING"),
 ("ncs_stage_sale_transaction",           "NCS_STAGE","SALE_TRANSACTION"),
 ("ncs_stage_device_end_of_day",          "NCS_STAGE","DEVICE_END_OF_DAY"),
 ("ncs_stage_device_end_of_day_msg_count","NCS_STAGE","DEVICE_END_OF_DAY_MSG_COUNT"),
 ("ncs_stage_device",                     "NCS_STAGE","DEVICE"),
 ("ncs_stage_device_type",                "NCS_STAGE","DEVICE_TYPE"),
 ("ncs_stage_event",                      "NCS_STAGE","EVENT"),
 ("ncs_stage_stop_point",                 "NCS_STAGE","STOP_POINT"),
 ("ncs_stage_transit_facility",           "NCS_STAGE","TRANSIT_FACILITY"),
 ("cta_servicenow_availability_events",   "CTA",      "SERVICENOW_AVAILABILITY_EVENTS"),
 ("cta_servicenow_data_from_jumpbox",     "CTA",      "SERVICENOW_DATA_FROM_JUMPBOX"),
 ("cta_kpi_agency_map",                   "CTA",      "KPI_AGENCY_MAP"),
 ("cta_kpi_monthly_summary",              "CTA",      "KPI_MONTHLY_SUMMARY"),
 ("cta_sldc_monthly_summary",             "CTA",      "SLDC_MONTHLY_SUMMARY"),
 ("cta_kpi_tvm_date_table",               "CTA",      "KPI_TVM_DATE_TABLE"),        # tier-3, probed for DT type
]

# Tier-3 "ingested but nothing downstream reads it" — probed so the STOP/KEEP decision has numbers.
TABLES_TIER3 = [
 ("ncs_stage_device_event_history",       "NCS_STAGE","DEVICE_EVENT_HISTORY"),
 ("edw_metric_summary_by_day",            "EDW",      "METRIC_SUMMARY_BY_DAY"),
 ("cta_abp_use_tran_timing_data",         "CTA",      "ABP_USE_TRAN_TIMING_DATA"),
 ("edw_device_last_set_event",            "EDW",      "DEVICE_LAST_SET_EVENT"),
 ("edw_device_location_history",          "EDW",      "DEVICE_LOCATION_HISTORY"),
 ("edw_device_current_tables",            "EDW",      "DEVICE_CURRENT_TABLES"),
 ("edw_device_current_sw_config",         "EDW",      "DEVICE_CURRENT_SW_CONFIG"),
 ("ncs_stage_sale_transaction_device_msg","NCS_STAGE","SALE_TRANSACTION_DEVICE_MSG"),
 ("ncs_stage_cashbox_manual_counts",      "NCS_STAGE","CASHBOX_MANUAL_COUNTS"),
 ("edw_date_dimension",                   "EDW",      "DATE_DIMENSION"),
]

INCLUDE_TIER3 = True         # set False for a faster tier-1-only run
ALL = TABLES + (TABLES_TIER3 if INCLUDE_TIER3 else [])
OWNERS = sorted({o for _, o, _ in ALL})

_C = None
def _creds():
    global _C
    if _C is None:
        svc = dbutils.secrets.get(SCOPE, "ods_service")
        if SDU:
            url = (f"jdbc:oracle:thin:@(DESCRIPTION=(SDU={SDU})"
                   f"(ADDRESS=(PROTOCOL=TCP)(HOST={ODS_HOST})(PORT={ODS_PORT}))"
                   f"(CONNECT_DATA=(SERVICE_NAME={svc})))")
        else:
            url = f"jdbc:oracle:thin:@//{ODS_HOST}:{ODS_PORT}/{svc}"
        _C = {"url": url, "user": dbutils.secrets.get(SCOPE, "ods_user"),
              "pwd": dbutils.secrets.get(SCOPE, "ods_pwd")}
    return _C

def ora(q, secs=QUERY_TO):
    c = _creds()
    return (spark.read.format("jdbc")
            .option("url", c["url"]).option("dbtable", q)
            .option("user", c["user"]).option("password", c["pwd"])
            .option("driver", "oracle.jdbc.OracleDriver")
            .option("queryTimeout", str(secs))
            .option("oracle.net.CONNECT_TIMEOUT", CONNECT_TO)
            .option("oracle.jdbc.ReadTimeout", READ_TO)
            .option("sessionInitStatement",
                    "ALTER SESSION SET NLS_DATE_FORMAT='YYYY-MM-DD HH24:MI:SS'")
            .load())

print(f"01_ORACLE_incremental_probe | {len(ALL)} tables | increment from {INCR_FROM} (key {INCR_FROM_KEY}) "
      f"| sentinel {SENTINEL} | SDU={SDU}")

# COMMAND ----------
# ============================== CELL 2 : PREFLIGHT (fail fast, don't hang) ==============================
import socket
_s = socket.socket(); _s.settimeout(5)
try:
    _s.connect((ODS_HOST, ODS_PORT)); TCP = "OPEN"
except Exception as e:
    TCP = f"FAILED: {e}"
finally:
    _s.close()

ORACLE_UP = False
try:
    ora("(SELECT 1 AS OK FROM dual) q", 20).collect(); ORACLE_UP = True
except Exception as e:
    print("logon error:", str(e).splitlines()[0][:160])

print(f"TCP={TCP}  |  logon={'OK' if ORACLE_UP else 'FAILED'}")
assert ORACLE_UP, ("Oracle unreachable. Run 'oracle_connectivity_diagnostics.py' first. "
                   "If logon hangs at T4CConnection.logon this is the VPN MTU black-hole — keep SDU=512.")

# COMMAND ----------
# ============================== CELL 3 : EXISTENCE + COLUMN CATALOGUE (one round trip per owner) ======
# ONE query per owner against ALL_TAB_COLUMNS. This is the authoritative watermark source.
want = {}
for b, o, t in ALL:
    want.setdefault(o, set()).add(t)

COLS = {}      # (OWNER,TABLE) -> [(col, dtype, nullable, len, prec, scale)]
EXISTS = {}
for o in OWNERS:
    lst = ",".join(f"'{t}'" for t in sorted(want[o]))
    q = (f"(SELECT TABLE_NAME, COLUMN_NAME, DATA_TYPE, NULLABLE, DATA_LENGTH, "
         f"DATA_PRECISION, DATA_SCALE, COLUMN_ID "
         f"FROM ALL_TAB_COLUMNS WHERE OWNER='{o}' AND TABLE_NAME IN ({lst})) q")
    for r in ora(q).orderBy("TABLE_NAME", "COLUMN_ID").collect():
        COLS.setdefault((o, r["TABLE_NAME"]), []).append(
            (r["COLUMN_NAME"], r["DATA_TYPE"], r["NULLABLE"], r["DATA_LENGTH"],
             r["DATA_PRECISION"], r["DATA_SCALE"]))
    q2 = (f"(SELECT TABLE_NAME, 'TABLE' AS KIND FROM ALL_TABLES WHERE OWNER='{o}' AND TABLE_NAME IN ({lst}) "
          f"UNION ALL SELECT VIEW_NAME, 'VIEW' FROM ALL_VIEWS WHERE OWNER='{o}' AND VIEW_NAME IN ({lst})) q")
    for r in ora(q2).collect():
        EXISTS[(o, r["TABLE_NAME"])] = r["KIND"]

DATE_TYPES = ("DATE", "TIMESTAMP")
def date_cols(o, t):
    return [c for c, d, *_ in COLS.get((o, t), []) if any(d.startswith(x) for x in DATE_TYPES)]
def daykey_cols(o, t):
    return [c for c, d, *_ in COLS.get((o, t), []) if c.endswith("_DAY_KEY") or c == "DT"]

print(f"{'bronze_table':40s} {'exists':6s} {'#cols':>5s}  date/timestamp cols")
print("-" * 130)
for b, o, t in ALL:
    k = EXISTS.get((o, t), "MISSING")
    n = len(COLS.get((o, t), []))
    dc = date_cols(o, t) + daykey_cols(o, t)
    print(f"{b:40s} {k:6s} {n:5d}  {', '.join(dc) if dc else '(none)'}")

# COMMAND ----------
# ============================== CELL 4 : PICK THE WATERMARK — FROM ORACLE, NEVER FROM BRONZE =========
# Ordered by what actually exists in Chicago (Resolution doc §4.1). A leading "_" is REJECTED outright:
# _ingest_ts / _raw_ingest_ts are OUR audit columns and can never exist in Oracle (§4.2 guard).
UPDATE_PREF = ["EDW_UPDATED_DTM", "DW_UPDATED_DTM", "UPDATED_DTM", "MSG_UPDATED_DTM",
               "REPORTED_CHANGED_DTM", "CHANGE_DTM", "SUMM_DTM", "EDW_UPDATE_DTM"]
INSERT_PREF = ["EDW_INSERTED_DTM", "DW_INSERTED_DTM", "INSERTED_DTM", "CREATED_DTM",
               "SUMM_DTM", "LOAD_DTM"]
BIZ_PREF    = ["EVENT_DTM", "TRANSACTION_DTM", "START_DTM", "SN_U_END_DTM", "U_START_DTM",
               "MONTH_DTM", "LAST_REPORTED_DTM", "MSG_EFFECTIVE_DTM", "DTM"]
KEY_PREF    = ["TRANSIT_DAY_KEY", "EVENT_DAY_KEY", "POSTING_DAY_KEY", "DT"]

def _first(cands, avail):
    up = {c.upper(): c for c in avail}
    for c in cands:
        if c in up:
            return up[c]
    return None

PLAN = []
for b, o, t in ALL:
    if (o, t) not in EXISTS:
        PLAN.append((b, o, t, None, None, "MISSING_IN_ORACLE")); continue
    avail = [c for c, *_ in COLS[(o, t)]]
    assert not any(c.startswith("_") for c in avail), f"{o}.{t}: audit-looking column in Oracle?!"
    upd = _first(UPDATE_PREF, avail)
    ins = _first(INSERT_PREF, avail)
    biz = _first(BIZ_PREF, avail)
    key = _first(KEY_PREF, avail)
    wm, kind = (upd, "update_wm") if upd else \
               (ins, "insert_wm") if ins else \
               (biz, "business_dtm") if biz else \
               (key, "day_key") if key else (None, "NO_WATERMARK->full_reload")
    PLAN.append((b, o, t, wm, kind, "ok"))

print(f"{'bronze_table':40s} {'watermark':24s} {'kind':22s} note")
print("-" * 120)
for b, o, t, wm, kind, note in PLAN:
    print(f"{b:40s} {str(wm):24s} {str(kind):22s} {note}")

# COMMAND ----------
# ============================== CELL 5 : THE INCREMENT — counts since 2026-04-12 =====================
# One bounded COUNT per table. `< SENTINEL` excludes the known future-dated sentinel rows
# (edw_device_metric to 2028; several NCS tables to 2032-2034) — see catalog key_facts "Freshness note".
ROWS = []
for b, o, t, wm, kind, note in PLAN:
    if note != "ok":
        ROWS.append((b, f"{o}.{t}", "-", "-", None, None, None, None, note)); continue
    try:
        if wm is None:
            tot = int(ora(f"(SELECT COUNT(*) N FROM {o}.{t}) q", 90).collect()[0]["N"])
            ROWS.append((b, f"{o}.{t}", "-", "full_reload", tot, None, None, None, "no watermark")); continue
        if kind == "day_key":
            pred = f"{wm} >= {INCR_FROM_KEY} AND {wm} < {int(SENTINEL.replace('-',''))}"
            mm = ora(f"(SELECT MIN({wm}) LO, MAX({wm}) HI FROM {o}.{t} WHERE {wm} < {int(SENTINEL.replace('-',''))}) q", 240).collect()[0]
        else:
            pred = f"{wm} >= TIMESTAMP '{INCR_FROM} 00:00:00' AND {wm} < DATE '{SENTINEL}'"
            mm = ora(f"(SELECT MIN({wm}) LO, MAX({wm}) HI FROM {o}.{t} WHERE {wm} < DATE '{SENTINEL}') q", 240).collect()[0]
        tot = int(ora(f"(SELECT COUNT(*) N FROM {o}.{t}) q", 240).collect()[0]["N"])
        inc = int(ora(f"(SELECT COUNT(*) N FROM {o}.{t} WHERE {pred}) q", 240).collect()[0]["N"])
        ROWS.append((b, f"{o}.{t}", wm, kind, tot, inc, str(mm["LO"]), str(mm["HI"]), "ok"))
        print(f"{b:40s} wm={wm:22s} total={tot:>13,} incr={inc:>12,}  [{mm['LO']} .. {mm['HI']}]")
    except Exception as e:
        ROWS.append((b, f"{o}.{t}", str(wm), str(kind), None, None, None, None,
                     f"ERROR:{str(e).splitlines()[0][:70]}"))
        print(f"{b:40s} ERROR {str(e).splitlines()[0][:90]}")

# COMMAND ----------
# ============================== CELL 6 : MONTHLY SHAPE OF THE INCREMENT ==============================
# Only for tables with a real increment — tells us whether Cubic delivered a clean contiguous feed
# or a lumpy one, and whether there is a gap between 2026-04-11 and the first new row.
SHAPE = []
for b, src, wm, kind, tot, inc, lo, hi, note in ROWS:
    if note != "ok" or not inc:
        continue
    o, t = src.split(".")
    try:
        if kind == "day_key":
            q = (f"(SELECT FLOOR({wm}/100) YM, COUNT(*) N FROM {o}.{t} "
                 f"WHERE {wm} >= {INCR_FROM_KEY} AND {wm} < {int(SENTINEL.replace('-',''))} "
                 f"GROUP BY FLOOR({wm}/100)) q")
        else:
            q = (f"(SELECT TO_CHAR({wm},'YYYYMM') YM, COUNT(*) N FROM {o}.{t} "
                 f"WHERE {wm} >= TIMESTAMP '{INCR_FROM} 00:00:00' AND {wm} < DATE '{SENTINEL}' "
                 f"GROUP BY TO_CHAR({wm},'YYYYMM')) q")
        d = {str(r["YM"]): int(r["N"]) for r in ora(q, 300).collect()}
        SHAPE.append((b, d))
        print(f"{b:40s} " + "  ".join(f"{k}:{v:,}" for k, v in sorted(d.items())))
    except Exception as e:
        SHAPE.append((b, {"ERROR": str(e).splitlines()[0][:60]}))
        print(f"{b:40s} ERROR {str(e).splitlines()[0][:80]}")

# COMMAND ----------
# ============================== CELL 7 : PASTE-BACK BLOCK ==============================
out = {
  "probe": "01_ORACLE_incremental_probe",
  "run_ts": _dt.datetime.utcnow().isoformat() + "Z",
  "incr_from": INCR_FROM, "sentinel": SENTINEL,
  "existence": {f"{o}.{t}": EXISTS.get((o, t), "MISSING") for _, o, t in ALL},
  "watermark_plan": [{"bronze": b, "src": f"{o}.{t}", "wm": wm, "kind": k, "note": n}
                     for b, o, t, wm, k, n in PLAN],
  "counts": [{"bronze": b, "src": s, "wm": w, "kind": k, "oracle_total": tt,
              "incr_since_0412": i, "min": lo, "max": hi, "note": n}
             for b, s, w, k, tt, i, lo, hi, n in ROWS],
  "monthly": {b: d for b, d in SHAPE},
  "oracle_columns": {f"{o}.{t}": [{"c": c, "t": d, "null": nl, "len": ln, "p": p, "s": sc}
                                  for c, d, nl, ln, p, sc in COLS.get((o, t), [])]
                     for _, o, t in ALL},
}
print("=" * 100)
print("PASTE EVERYTHING BETWEEN THE MARKERS BACK INTO THE COWORK SESSION")
print("<<<PROBE01_JSON_START>>>")
print(json.dumps(out, default=str))
print("<<<PROBE01_JSON_END>>>")
print("=" * 100)
