# Databricks notebook source
# MAGIC %md
# MAGIC # 13 — DATA CONTINUITY AUDIT  (Oracle-side · read-only · evidence for the Chicago team)
# MAGIC
# MAGIC Measures whether each in-scope ODS table holds **continuous data from 12-Apr-2026 to today**,
# MAGIC and where it stops. Produces a per-table daily (or monthly) series, detects gaps, and writes:
# MAGIC - a one-line-per-table **summary** (verdict: CONTINUOUS / GAPS / FROZEN / SPARSE / UNKNOWN)
# MAGIC - the full **daily detail** series
# MAGIC both as CSVs under `/dbfs/FileStore/chicago_continuity/<ts>/` (downloadable via the workspace
# MAGIC `/files/...` URL — attach these to the mail) and as JSON in `mars_dev.audit.probe_results`.
# MAGIC
# MAGIC Design rules (measured, not guessed — see probes 01/04):
# MAGIC - Window-bounded queries **only on verified indexed columns** for the heavies → index range scans.
# MAGIC - Where the *insert* column differs from the *business* column (cashbox EVENT_DTM,
# MAGIC   sale TRANSACTION_DTM, device_event EVENT_DTM), a SECOND series groups the same increment by
# MAGIC   the business date — this distinguishes "feed paused" from "events missing".
# MAGIC - `edw_abp_tap` runs at MONTHLY grain (its per-month COUNTs time out even at 1800s; graceful UNKNOWN).
# MAGIC - `msg_count` scan is skipped unless INCLUDE_HEAVY_SCANS=True (159M rows, no useful index).
# MAGIC - Every bound `< tomorrow` (sentinel dates 2028/2032/2034 excluded by construction).
# MAGIC - READ-ONLY against Oracle; writes only CSVs + the audit log table.

# COMMAND ----------
# ============================== CELL 1 : CONFIG + ORACLE HELPER ==============================
import datetime as _dt, json
from pyspark.sql import functions as F

INCLUDE_HEAVY_SCANS = False          # msg_count + EDW.USE_TRANSACTION full scans — off-hours only
WINDOW_START = "2026-04-12"
TODAY     = _dt.date.today()
TOMORROW  = (TODAY + _dt.timedelta(days=1)).isoformat()
TKEY_LO, TKEY_HI = int(WINDOW_START.replace("-", "")), int(TOMORROW.replace("-", ""))
TS        = _dt.datetime.now().strftime("%Y%m%d_%H%M%S")
OUT_DIR   = f"/dbfs/FileStore/chicago_continuity/{TS}"
ODS_HOST, ODS_PORT, SCOPE_SECRET, SDU = "10.3.10.30", 1521, "cubic", 512

_C = None
def _creds():
    global _C
    if _C is None:
        svc = dbutils.secrets.get(SCOPE_SECRET, "ods_service")
        url = (f"jdbc:oracle:thin:@(DESCRIPTION=(SDU={SDU})(ADDRESS=(PROTOCOL=TCP)"
               f"(HOST={ODS_HOST})(PORT={ODS_PORT}))(CONNECT_DATA=(SERVICE_NAME={svc})))")
        _C = {"url": url, "user": dbutils.secrets.get(SCOPE_SECRET, "ods_user"),
              "pwd": dbutils.secrets.get(SCOPE_SECRET, "ods_pwd")}
    return _C

def ora(q, secs=1800):
    c = _creds()
    return (spark.read.format("jdbc").option("url", c["url"]).option("dbtable", q)
            .option("user", c["user"]).option("password", c["pwd"])
            .option("driver", "oracle.jdbc.OracleDriver").option("queryTimeout", str(secs))
            .option("oracle.net.CONNECT_TIMEOUT", "10000").option("oracle.jdbc.ReadTimeout", "2100000")
            .option("sessionInitStatement", "ALTER SESSION SET NLS_DATE_FORMAT='YYYY-MM-DD HH24:MI:SS'")
            .load())

ora("(SELECT 1 OK FROM dual) q", 20).collect()
print(f"NB13 continuity audit | window {WINDOW_START} -> {TODAY} | out {OUT_DIR}")

# COMMAND ----------
# ============================== CELL 2 : THE AUDIT PLAN ==============================
# Series queries. D must come back as a DATE (TRUNC) or a YYYYMMDD number; N is the row count.
def dtm_daily(ot, col, lo=WINDOW_START):
    return (f"(SELECT TRUNC({col}) D, COUNT(*) N FROM {ot} "
            f"WHERE {col} >= DATE '{lo}' AND {col} < DATE '{TOMORROW}' GROUP BY TRUNC({col})) q")
def dtm_monthly(ot, col):
    return (f"(SELECT TRUNC({col},'MM') D, COUNT(*) N FROM {ot} "
            f"WHERE {col} >= DATE '{WINDOW_START}' AND {col} < DATE '{TOMORROW}' GROUP BY TRUNC({col},'MM')) q")
def daykey_daily(ot, col):
    return (f"(SELECT {col} D, COUNT(*) N FROM {ot} "
            f"WHERE {col} >= {TKEY_LO} AND {col} < {TKEY_HI} GROUP BY {col}) q")
def biz_from_pull(ot, pull, biz, biz_is_daykey=False):
    g = biz if biz_is_daykey else f"TRUNC({biz})"
    return (f"(SELECT {g} D, COUNT(*) N FROM {ot} "
            f"WHERE {pull} >= DATE '{WINDOW_START}' AND {pull} < DATE '{TOMORROW}' GROUP BY {g}) q")
def biz_from_daykey_pull(ot, pull, biz):
    return (f"(SELECT TRUNC({biz}) D, COUNT(*) N FROM {ot} "
            f"WHERE {pull} >= {TKEY_LO} AND {pull} < {TKEY_HI} GROUP BY TRUNC({biz})) q")

# (label, series_name, sql, grain, timeout_s)  — grain 'daily'|'monthly'
SERIES_PLAN = [
 ("edw_device_event",           "updated",  dtm_daily("EDW.DEVICE_EVENT", "EDW_UPDATED_DTM"),   "daily", 1800),
 ("edw_device_event",           "business", biz_from_pull("EDW.DEVICE_EVENT", "EDW_UPDATED_DTM", "EVENT_DTM"), "daily", 1800),
 ("edw_read_transaction",       "updated",  dtm_daily("EDW.READ_TRANSACTION", "EDW_UPDATED_DTM"), "daily", 1800),
 ("edw_device_metric",          "inserted", dtm_daily("EDW.DEVICE_METRIC", "EDW_INSERTED_DTM"), "daily", 1800),
 ("edw_abp_tap",                "updated",  dtm_monthly("EDW.ABP_TAP", "EDW_UPDATED_DTM"),      "monthly", 1800),
 ("ncs_stage_cashbox_tracking", "inserted", dtm_daily("NCS_STAGE.CASHBOX_TRACKING", "INSERTED_DTM"), "daily", 1800),
 ("ncs_stage_cashbox_tracking", "business", biz_from_pull("NCS_STAGE.CASHBOX_TRACKING", "INSERTED_DTM", "EVENT_DTM"), "daily", 1800),
 ("ncs_stage_sale_transaction", "inserted", daykey_daily("NCS_STAGE.SALE_TRANSACTION", "DW_INSERTED_DAY"), "daily", 1800),
 ("ncs_stage_sale_transaction", "business", biz_from_daykey_pull("NCS_STAGE.SALE_TRANSACTION", "DW_INSERTED_DAY", "TRANSACTION_DTM"), "daily", 1800),
 ("ncs_stage_device_end_of_day","business", daykey_daily("NCS_STAGE.DEVICE_END_OF_DAY", "TRANSIT_DAY_KEY"), "daily", 1800),
 ("edw_availability_events",    "business", daykey_daily("EDW.AVAILABILITY_EVENTS", "TRANSIT_DAY_KEY"), "daily", 900),
 ("edw_availability_relief",    "business", dtm_daily("EDW.AVAILABILITY_RELIEF", "START_DTM"),  "daily", 900),
 ("edw_kpi_detail_events_by_day","business",dtm_daily("EDW.KPI_DETAIL_EVENTS_BY_DAY", "START_DTM"), "daily", 900),
 ("edw_kpi_summary_by_day",     "business", daykey_daily("EDW.KPI_SUMMARY_BY_DAY", "TRANSIT_DAY_KEY"), "daily", 900),
 ("cta_servicenow_availability_events", "feed", dtm_daily("CTA.SERVICENOW_AVAILABILITY_EVENTS", "SN_SYS_UPDATED_ON"), "daily", 900),
]
if INCLUDE_HEAVY_SCANS:
    SERIES_PLAN += [
     ("ncs_stage_device_end_of_day_msg_count", "business", daykey_daily("NCS_STAGE.DEVICE_END_OF_DAY_MSG_COUNT", "TRANSIT_DAY_KEY"), "daily", 1800),
     ("edw_use_transaction_base", "business", daykey_daily("EDW.USE_TRANSACTION", "TRANSIT_DAY_KEY"), "daily", 1800),
    ]

# Freshness-only checks (dims / reference / oddballs) — one tiny query each.
FRESH_PLAN = [
 ("edw_device_dimension",        "(SELECT COUNT(*) N, MAX(UPDATED_DTM) MX FROM EDW.DEVICE_DIMENSION) q"),
 ("edw_device_last_state",       "(SELECT COUNT(*) N, MAX(EDW_UPDATED_DTM) MX FROM EDW.DEVICE_LAST_STATE) q"),
 ("edw_device_current_hw_config","(SELECT COUNT(*) N, MAX(LAST_REPORTED_DTM) MX FROM EDW.DEVICE_CURRENT_HW_CONFIG) q"),
 ("edw_kpi_rules",               "(SELECT COUNT(*) N, MAX(INSERTED_DTM) MX FROM EDW.KPI_RULES) q"),
 ("edw_kpi",                     "(SELECT COUNT(*) N, CAST(NULL AS DATE) MX FROM EDW.KPI) q"),
 ("edw_kpi_target",              "(SELECT COUNT(*) N, CAST(NULL AS DATE) MX FROM EDW.KPI_TARGET) q"),
 ("edw_event_type_dimension",    "(SELECT COUNT(*) N, CAST(NULL AS DATE) MX FROM EDW.EVENT_TYPE_DIMENSION) q"),
 ("edw_metric_dimension",        "(SELECT COUNT(*) N, CAST(NULL AS DATE) MX FROM EDW.METRIC_DIMENSION) q"),
 ("ncs_stage_device",            "(SELECT COUNT(*) N, MAX(UPDATED_DTM) MX FROM NCS_STAGE.DEVICE) q"),
 ("ncs_stage_device_type",       "(SELECT COUNT(*) N, CAST(NULL AS DATE) MX FROM NCS_STAGE.DEVICE_TYPE) q"),
 ("ncs_stage_event",             "(SELECT COUNT(*) N, CAST(NULL AS DATE) MX FROM NCS_STAGE.EVENT) q"),
 ("ncs_stage_stop_point",        "(SELECT COUNT(*) N, MAX(UPDATED_DTM) MX FROM NCS_STAGE.STOP_POINT) q"),
 ("ncs_stage_transit_facility",  "(SELECT COUNT(*) N, CAST(NULL AS DATE) MX FROM NCS_STAGE.TRANSIT_FACILITY) q"),
 ("cta_kpi_agency_map",          "(SELECT COUNT(*) N, MAX(UPDATED_DTM) MX FROM CTA.KPI_AGENCY_MAP) q"),
 ("cta_kpi_monthly_summary",     "(SELECT COUNT(*) N, MAX(MONTH_DTM) MX FROM CTA.KPI_MONTHLY_SUMMARY) q"),
 ("cta_sldc_monthly_summary",    "(SELECT COUNT(*) N, MAX(MONTH_DTM) MX FROM CTA.SLDC_MONTHLY_SUMMARY) q"),
 ("cta_servicenow_availability_events", "(SELECT COUNT(*) N, MAX(SN_SYS_UPDATED_ON) MX FROM CTA.SERVICENOW_AVAILABILITY_EVENTS) q"),
 ("cta_servicenow_data_from_jumpbox",   "(SELECT COUNT(*) N, MAX(U_START_DTM) MX FROM CTA.SERVICENOW_DATA_FROM_JUMPBOX) q"),  # VARCHAR: lexicographic max works for 'YYYY-MM-DD ...'
]
print(f"{len(SERIES_PLAN)} series queries + {len(FRESH_PLAN)} freshness checks")

# COMMAND ----------
# ============================== CELL 3 : RUN THE AUDIT (read-only) ==============================
def _iso(d):
    if d is None: return None
    s = str(d)
    if "-" in s: return s[:10]          # already a date/timestamp string
    s = s.split(".")[0]                  # int / float / Decimal day-key
    return f"{s[:4]}-{s[4:6]}-{s[6:8]}" if len(s) == 8 and s.isdigit() else s[:10]

SERIES, ERRORS = {}, {}
for label, sname, sql, grain, secs in SERIES_PLAN:
    key = f"{label}::{sname}"
    try:
        rows = ora(sql, secs).collect()
        SERIES[key] = {"grain": grain, "points": sorted((_iso(r["D"]), int(r["N"])) for r in rows if r["D"] is not None)}
        pts = SERIES[key]["points"]
        print(f"OK   {key:55s} {len(pts):4d} {grain} points"
              + (f"  [{pts[0][0]} -> {pts[-1][0]}]" if pts else "  [EMPTY — nothing in window]"))
    except Exception as e:
        ERRORS[key] = str(e).splitlines()[0][:120]
        print(f"FAIL {key:55s} {ERRORS[key][:80]}")

FRESH = {}
for label, sql in FRESH_PLAN:
    try:
        r = ora(sql, 600).first()
        FRESH[label] = {"rows": int(r["N"]), "max_dtm": str(r["MX"])[:19] if r["MX"] is not None else None}
        print(f"FRESH {label:42s} rows={FRESH[label]['rows']:>12,}  max={FRESH[label]['max_dtm']}")
    except Exception as e:
        ERRORS[f"{label}::fresh"] = str(e).splitlines()[0][:120]
        print(f"FAIL  {label:42s} {ERRORS[f'{label}::fresh'][:80]}")

# COMMAND ----------
# ============================== CELL 4 : GAPS, VERDICTS, CSVs, SUMMARY ==============================
W0 = _dt.date.fromisoformat(WINDOW_START)

def day_range(a, b):
    d, out = a, []
    while d <= b: out.append(d); d += _dt.timedelta(days=1)
    return out

def compress(days):  # [date,...] -> "12-18 Apr, 03 May" style ranges
    if not days: return ""
    runs, s, p = [], days[0], days[0]
    for d in days[1:]:
        if (d - p).days > 1: runs.append((s, p)); s = d
        p = d
    runs.append((s, p))
    return "; ".join(a.isoformat() if a == b else f"{a.isoformat()}..{b.isoformat()}" for a, b in runs)

SUMMARY = []
for key, sd in sorted(SERIES.items()):
    label, sname = key.split("::")
    pts = sd["points"]
    if not pts:
        SUMMARY.append((label, sname, sd["grain"], None, None, 0, 0, "", "NO_DATA_IN_WINDOW")); continue
    first, last = _dt.date.fromisoformat(pts[0][0]), _dt.date.fromisoformat(pts[-1][0])
    total = sum(n for _, n in pts)
    if sd["grain"] == "monthly":
        verdict = "FROZEN" if (TODAY - last).days > 45 else "PRESENT (monthly grain)"
        SUMMARY.append((label, sname, "monthly", pts[0][0], pts[-1][0], total, len(pts), "", verdict)); continue
    have = {p[0] for p in pts}
    missing = [d for d in day_range(max(W0, first), last) if d.isoformat() not in have]
    stale = (TODAY - last).days
    avg = total / max(len(pts), 1)
    if stale > 14:                      verdict = f"FROZEN (last {last.isoformat()}, {stale}d stale)"
    elif avg < 50 and missing:          verdict = f"SPARSE (low-volume table; {len(missing)} empty days)"
    elif len(missing) == 0:             verdict = "CONTINUOUS"
    else:                               verdict = f"GAPS ({len(missing)} missing days)"
    SUMMARY.append((label, sname, "daily", pts[0][0], pts[-1][0], total, len(missing), compress(missing), verdict))
for key, err in ERRORS.items():
    label, sname = key.split("::")
    SUMMARY.append((label, sname, "-", None, None, None, None, "", f"UNKNOWN ({err[:60]})"))

S3_AUDIT = "s3://cubic-mars-pm-s3-datalake-dev-raw-170202974600/chicago_ventra/_audit/continuity_" + TS
SAVED = {}
def save_csv(name, text):
    """Write a CSV where this cluster allows: DBFS FUSE -> dbutils dbfs -> S3 _audit prefix."""
    import os as _os
    errs = []
    try:
        d = f"/dbfs/FileStore/chicago_continuity/{TS}"
        _os.makedirs(d, exist_ok=True)
        with open(f"{d}/{name}", "w") as f: f.write(text)
        SAVED[name] = f"/FileStore/chicago_continuity/{TS}/{name}"
        print(f"saved {name} -> {SAVED[name]}  (download: <workspace-url>/files/chicago_continuity/{TS}/{name})"); return
    except Exception as e: errs.append(f"fuse: {str(e).splitlines()[0][:50]}")
    try:
        p = f"dbfs:/FileStore/chicago_continuity/{TS}/{name}"
        dbutils.fs.put(p, text, True)
        SAVED[name] = p
        print(f"saved {name} -> {p}  (download: <workspace-url>/files/chicago_continuity/{TS}/{name})"); return
    except Exception as e: errs.append(f"dbutils: {str(e).splitlines()[0][:50]}")
    try:
        p = f"{S3_AUDIT}/{name}"
        dbutils.fs.put(p, text, True)
        SAVED[name] = p
        print(f"saved {name} -> {p}  (fetch in CloudShell: aws s3 cp '{p}' .)"); return
    except Exception as e: errs.append(f"s3: {str(e).splitlines()[0][:50]}")
    print(f"COULD NOT SAVE {name} ({'; '.join(errs)}) — content follows:"); print(text)

buf = ["table,series,grain,first_day,last_day,rows_in_window,missing_days,missing_ranges,verdict"]
for r in SUMMARY:
    buf.append(",".join('"' + str(x if x is not None else "") + '"' for x in r))
save_csv("summary.csv", "\n".join(buf) + "\n")
buf = ["table,series,day,rows"]
for key, sd in sorted(SERIES.items()):
    label, sname = key.split("::")
    for d, n in sd["points"]: buf.append(f"{label},{sname},{d},{n}")
save_csv("daily_detail.csv", "\n".join(buf) + "\n")
buf = ["table,rows,max_dtm,days_stale"]
for label, v in sorted(FRESH.items()):
    stale = (TODAY - _dt.date.fromisoformat(v["max_dtm"][:10])).days if v["max_dtm"] else ""
    buf.append(f"{label},{v['rows']},{v['max_dtm']},{stale}")
save_csv("freshness.csv", "\n".join(buf) + "\n")

print(f"\n{'table':40s} {'series':9s} {'first':10s} {'last':10s} {'rows':>12s} {'gap_days':>8s}  verdict")
print("-" * 110)
for r in sorted(SUMMARY):
    print(f"{r[0]:40s} {r[1]:9s} {str(r[3]):10s} {str(r[4]):10s} "
          f"{(f'{r[5]:,}' if isinstance(r[5], int) else '-'):>12s} {str(r[6]):>8s}  {r[8]}")

payload = json.dumps({"probe": "13_DATA_CONTINUITY", "run_ts": TS, "window_start": WINDOW_START,
                      "series": SERIES, "freshness": FRESH, "errors": ERRORS, "summary": SUMMARY}, default=str)
spark.createDataFrame([("13_DATA_CONTINUITY", TS, payload)], "probe string, run_ts string, payload string") \
     .write.format("delta").mode("append").saveAsTable("mars_dev.audit.probe_results")
for i in range(0, len(payload), 40000):
    print(f"<<<PART {i//40000 + 1}>>>"); print(payload[i:i+40000])
print("\nCSV locations:"); [print(f"  {k}: {v}") for k, v in SAVED.items()]

# COMMAND ----------
# ============================== CELL 5 : SOURCE DATA-QUALITY TESTS ==============================
# One bounded, read-only query per test. Window predicates ride the probe-04-verified indexed
# columns; the only full scans are DEVICE_END_OF_DAY (proven feasible) and the tiny tables.
# Categories:
#   A garbage dates   - future-dated (> tomorrow) and pre-2000 values on change-tracking columns,
#                       plus future business day-keys inside the increment (the 2028/2032/2034 class)
#   B duplicate keys  - dup CASHBOX_EVENT_ID / DW_TRANSACTION_ID inside the window (feeds with no PK)
#   C NULL criticals  - NULL PKs / device ids / event times inside the window
#   D backfill check  - do the May+ inserts carry the missing 12-30 Apr events, or are they gone?
#   E relief restamp  - EDW.AVAILABILITY_RELIEF INSERTED_DTM mass-rewrite (kills CDC on that table)
#   F jumpbox typing  - dates stored as VARCHAR + any unparseable values
WS, TM, TKH = WINDOW_START, TOMORROW, TKEY_HI
def one(sql, secs=600): return ora(sql, secs).first().asDict()
def n0(x):
    try: return int(x or 0)
    except Exception: return 0

DQ = []   # (test, table, value, detail, verdict)
def add(test, table, value, detail, bad):
    DQ.append((test, table, str(value), detail, "ISSUE" if bad else "OK"))
    print(f"{'ISSUE' if bad else 'ok   '} {test:26s} {table:38s} {value}  {detail}")

# ---- A) garbage dates on indexed change-tracking columns (index range probes)
for label, ot, col in [
    ("edw_device_event",           "EDW.DEVICE_EVENT",           "EDW_UPDATED_DTM"),
    ("edw_read_transaction",       "EDW.READ_TRANSACTION",       "EDW_UPDATED_DTM"),
    ("edw_abp_tap",                "EDW.ABP_TAP",                "EDW_UPDATED_DTM"),
    ("edw_device_metric",          "EDW.DEVICE_METRIC",          "EDW_INSERTED_DTM"),
    ("ncs_stage_cashbox_tracking", "NCS_STAGE.CASHBOX_TRACKING", "INSERTED_DTM")]:
    try:
        r = one(f"(SELECT COUNT(*) N, MAX({col}) MX FROM {ot} WHERE {col} >= DATE '{TM}') q")
        add("future_dated_rows", label, n0(r["N"]), f"max {col} = {r['MX']}", n0(r["N"]) > 0)
        r = one(f"(SELECT COUNT(*) N, MIN({col}) MN FROM {ot} WHERE {col} < DATE '2000-01-01') q")
        add("pre2000_rows", label, n0(r["N"]), f"min {col} = {r['MN']}", n0(r["N"]) > 0)
    except Exception as e:
        add("garbage_date_probe", label, "-", str(e).splitlines()[0][:70], False)
try:  # sale: indexed day-key
    r = one(f"(SELECT COUNT(*) N, MAX(DW_INSERTED_DAY) MX FROM NCS_STAGE.SALE_TRANSACTION WHERE DW_INSERTED_DAY >= {TKH}) q")
    add("future_dated_rows", "ncs_stage_sale_transaction", n0(r["N"]), f"max DW_INSERTED_DAY = {r['MX']}", n0(r["N"]) > 0)
except Exception as e:
    add("garbage_date_probe", "ncs_stage_sale_transaction", "-", str(e).splitlines()[0][:70], False)
try:  # future business day-keys hiding inside the increment (device_metric 2028-class)
    r = one(f"(SELECT COUNT(*) N, MAX(TRANSIT_DAY_KEY) MX FROM EDW.DEVICE_METRIC "
            f"WHERE EDW_INSERTED_DTM >= DATE '{WS}' AND TRANSIT_DAY_KEY >= {TKH}) q", 1800)
    add("future_business_key", "edw_device_metric", n0(r["N"]), f"max TRANSIT_DAY_KEY = {r['MX']} (in post-12-Apr inserts)", n0(r["N"]) > 0)
except Exception as e:
    add("future_business_key", "edw_device_metric", "-", str(e).splitlines()[0][:70], False)
try:  # end_of_day sentinels — ONE bounded full scan
    r = one("(SELECT SUM(CASE WHEN TRANSIT_DAY_KEY >= %d THEN 1 ELSE 0 END) FUT,"
            " SUM(CASE WHEN TRANSIT_DAY_KEY < 20000101 THEN 1 ELSE 0 END) OLD,"
            " MIN(TRANSIT_DAY_KEY) MN, MAX(TRANSIT_DAY_KEY) MX FROM NCS_STAGE.DEVICE_END_OF_DAY) q" % TKH, 1800)
    add("future_dated_rows", "ncs_stage_device_end_of_day", n0(r["FUT"]), f"max key = {r['MX']}", n0(r["FUT"]) > 0)
    add("pre2000_rows", "ncs_stage_device_end_of_day", n0(r["OLD"]), f"min key = {r['MN']}", n0(r["OLD"]) > 0)
except Exception as e:
    add("garbage_date_probe", "ncs_stage_device_end_of_day", "-", str(e).splitlines()[0][:70], False)

# ---- B) duplicate keys inside the window (feeds that have NO primary key in Oracle)
try:
    r = one(f"(SELECT COUNT(*) K, NVL(SUM(C-1),0) X FROM (SELECT CASHBOX_EVENT_ID, COUNT(*) C "
            f"FROM NCS_STAGE.CASHBOX_TRACKING WHERE INSERTED_DTM >= DATE '{WS}' AND INSERTED_DTM < DATE '{TM}' "
            f"GROUP BY CASHBOX_EVENT_ID HAVING COUNT(*) > 1)) q", 1800)
    add("duplicate_keys_window", "ncs_stage_cashbox_tracking", n0(r["X"]), f"{n0(r['K'])} CASHBOX_EVENT_IDs duplicated", n0(r["X"]) > 0)
except Exception as e:
    add("duplicate_keys_window", "ncs_stage_cashbox_tracking", "-", str(e).splitlines()[0][:70], False)
try:
    r = one(f"(SELECT COUNT(*) K, NVL(SUM(C-1),0) X FROM (SELECT DW_TRANSACTION_ID, COUNT(*) C "
            f"FROM NCS_STAGE.SALE_TRANSACTION WHERE DW_INSERTED_DAY >= {TKEY_LO} AND DW_INSERTED_DAY < {TKH} "
            f"GROUP BY DW_TRANSACTION_ID HAVING COUNT(*) > 1)) q", 1800)
    add("duplicate_keys_window", "ncs_stage_sale_transaction", n0(r["X"]), f"{n0(r['K'])} DW_TRANSACTION_IDs duplicated", n0(r["X"]) > 0)
except Exception as e:
    add("duplicate_keys_window", "ncs_stage_sale_transaction", "-", str(e).splitlines()[0][:70], False)

# ---- C) NULL critical columns inside the window
for label, sql in [
    ("edw_device_event",
     f"(SELECT SUM(CASE WHEN DW_DEVICE_EVENT_ID IS NULL THEN 1 ELSE 0 END) NK,"
     f" SUM(CASE WHEN EVENT_DTM IS NULL THEN 1 ELSE 0 END) ND, COUNT(*) N FROM EDW.DEVICE_EVENT"
     f" WHERE EDW_UPDATED_DTM >= DATE '{WS}' AND EDW_UPDATED_DTM < DATE '{TM}') q"),
    ("ncs_stage_cashbox_tracking",
     f"(SELECT SUM(CASE WHEN DEVICE_ID IS NULL THEN 1 ELSE 0 END) NK,"
     f" SUM(CASE WHEN EVENT_DTM IS NULL THEN 1 ELSE 0 END) ND, COUNT(*) N FROM NCS_STAGE.CASHBOX_TRACKING"
     f" WHERE INSERTED_DTM >= DATE '{WS}' AND INSERTED_DTM < DATE '{TM}') q"),
    ("ncs_stage_sale_transaction",
     f"(SELECT SUM(CASE WHEN DW_TRANSACTION_ID IS NULL THEN 1 ELSE 0 END) NK,"
     f" SUM(CASE WHEN TRANSACTION_DTM IS NULL THEN 1 ELSE 0 END) ND, COUNT(*) N FROM NCS_STAGE.SALE_TRANSACTION"
     f" WHERE DW_INSERTED_DAY >= {TKEY_LO} AND DW_INSERTED_DAY < {TKH}) q")]:
    try:
        r = one(sql, 1800)
        add("null_key_in_window", label, n0(r["NK"]), f"of {n0(r['N']):,} window rows", n0(r["NK"]) > 0)
        add("null_event_time_in_window", label, n0(r["ND"]), f"of {n0(r['N']):,} window rows", n0(r["ND"]) > 0)
    except Exception as e:
        add("null_check", label, "-", str(e).splitlines()[0][:70], False)

# ---- D) were the missing 12-30 Apr events backfilled by the May+ inserts?
try:
    r = one(f"(SELECT MIN(EVENT_DTM) MN, SUM(CASE WHEN EVENT_DTM < DATE '{WS}' THEN 1 ELSE 0 END) OLDN,"
            f" SUM(CASE WHEN EVENT_DTM >= DATE '{WS}' AND EVENT_DTM < DATE '2026-05-01' THEN 1 ELSE 0 END) APR,"
            f" COUNT(*) N FROM NCS_STAGE.CASHBOX_TRACKING WHERE INSERTED_DTM >= DATE '{WS}' AND INSERTED_DTM < DATE '{TM}') q", 1800)
    add("apr12_30_events_backfilled", "ncs_stage_cashbox_tracking", n0(r["APR"]),
        f"window inserts carry {n0(r['APR']):,} events dated 12-30 Apr (earliest event {r['MN']}); pre-window events: {n0(r['OLDN']):,}",
        n0(r["APR"]) == 0)
except Exception as e:
    add("apr12_30_events_backfilled", "ncs_stage_cashbox_tracking", "-", str(e).splitlines()[0][:70], False)
try:
    r = one(f"(SELECT MIN(TRANSACTION_DTM) MN, SUM(CASE WHEN TRANSACTION_DTM >= DATE '{WS}' AND TRANSACTION_DTM < DATE '2026-05-01' THEN 1 ELSE 0 END) APR,"
            f" COUNT(*) N FROM NCS_STAGE.SALE_TRANSACTION WHERE DW_INSERTED_DAY >= {TKEY_LO} AND DW_INSERTED_DAY < {TKH}) q", 1800)
    add("apr12_30_events_backfilled", "ncs_stage_sale_transaction", n0(r["APR"]),
        f"window inserts carry {n0(r['APR']):,} transactions dated 12-30 Apr (earliest {r['MN']})", n0(r["APR"]) == 0)
except Exception as e:
    add("apr12_30_events_backfilled", "ncs_stage_sale_transaction", "-", str(e).splitlines()[0][:70], False)

# ---- E) availability_relief INSERTED_DTM mass-restamp (breaks CDC on this table)
try:
    r = one("(SELECT COUNT(*) N, MIN(INSERTED_DTM) MN, MAX(INSERTED_DTM) MX FROM EDW.AVAILABILITY_RELIEF) q")
    restamped = r["MN"] is not None and str(r["MN"])[:7] >= "2026-08"
    add("inserted_dtm_restamped", "edw_availability_relief", str(r["MN"])[:10],
        f"ALL {n0(r['N']):,} rows have INSERTED_DTM in [{str(r['MN'])[:10]} .. {str(r['MX'])[:10]}] - column unusable as CDC watermark" if restamped
        else f"{n0(r['N']):,} rows, INSERTED_DTM spans [{str(r['MN'])[:10]} .. {str(r['MX'])[:10]}]", restamped)
except Exception as e:
    add("inserted_dtm_restamped", "edw_availability_relief", "-", str(e).splitlines()[0][:70], False)

# ---- F) jumpbox: dates stored as VARCHAR + unparseable values
try:
    r = one("(SELECT COUNT(*) N,"
            " SUM(CASE WHEN NOT REGEXP_LIKE(U_START_DTM, '^[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]') THEN 1 ELSE 0 END) BAD,"
            " MIN(U_START_DTM) MN, MAX(U_START_DTM) MX FROM CTA.SERVICENOW_DATA_FROM_JUMPBOX) q")
    add("date_stored_as_varchar", "cta_servicenow_data_from_jumpbox", "U_START_DTM",
        f"{n0(r['N']):,} rows; range [{r['MN']} .. {r['MX']}] held as text, not DATE", True)
    add("unparseable_date_strings", "cta_servicenow_data_from_jumpbox", n0(r["BAD"]),
        "rows whose U_START_DTM does not start YYYY-MM-DD", n0(r["BAD"]) > 0)
except Exception as e:
    add("jumpbox_typing", "cta_servicenow_data_from_jumpbox", "-", str(e).splitlines()[0][:70], False)

print(f"\nDQ tests done: {sum(1 for d in DQ if d[4]=='ISSUE')} issues / {len(DQ)} checks")

# COMMAND ----------
# ============================== CELL 6 : ORACLE STATS SNAPSHOT + CONSOLIDATED DIGEST ==============================
EDW_T = ["DEVICE_EVENT","ABP_TAP","READ_TRANSACTION","DEVICE_METRIC","AVAILABILITY_EVENTS","AVAILABILITY_RELIEF",
         "DEVICE_DIMENSION","DEVICE_LAST_STATE","DEVICE_CURRENT_HW_CONFIG","KPI_DETAIL_EVENTS_BY_DAY",
         "KPI_SUMMARY_BY_DAY","KPI_RULES","KPI","KPI_TARGET","EVENT_TYPE_DIMENSION","METRIC_DIMENSION","USE_TRANSACTION"]
NCS_T = ["CASHBOX_TRACKING","SALE_TRANSACTION","DEVICE_END_OF_DAY","DEVICE_END_OF_DAY_MSG_COUNT","DEVICE",
         "DEVICE_TYPE","EVENT","STOP_POINT","TRANSIT_FACILITY"]
CTA_T = ["SERVICENOW_AVAILABILITY_EVENTS","SERVICENOW_DATA_FROM_JUMPBOX","KPI_AGENCY_MAP","KPI_MONTHLY_SUMMARY","SLDC_MONTHLY_SUMMARY"]
STATS = []
try:
    q = ("(SELECT owner, table_name, num_rows, TO_CHAR(last_analyzed,'YYYY-MM-DD') LA FROM all_tables WHERE "
         f"(owner='EDW' AND table_name IN ('" + "','".join(EDW_T) + "')) OR "
         f"(owner='NCS_STAGE' AND table_name IN ('" + "','".join(NCS_T) + "')) OR "
         f"(owner='CTA' AND table_name IN ('" + "','".join(CTA_T) + "'))) q")
    STATS = [r.asDict() for r in ora(q, 120).collect()]
    print(f"dictionary stats: {len(STATS)} tables")
except Exception as e:
    print("stats snapshot failed:", str(e).splitlines()[0][:80])

buf = ["test,table,value,detail,verdict"]
for t in DQ: buf.append(",".join('"' + str(x).replace('"', "'") + '"' for x in t))
save_csv("dq_findings.csv", "\n".join(buf) + "\n")
buf = ["owner,table_name,num_rows,last_analyzed"]
for s in STATS: buf.append(f"{s['OWNER']},{s['TABLE_NAME']},{s['NUM_ROWS']},{s['LA']}")
save_csv("oracle_stats.csv", "\n".join(buf) + "\n")

print("\n" + "=" * 100)
print("CONSOLIDATED ISSUE DIGEST (paste-ready for the mail)")
print("=" * 100)
print("\n[1] CONTINUITY - frozen feeds and gaps:")
for r in sorted(SUMMARY):
    if any(k in r[8] for k in ("FROZEN", "GAPS", "NO_DATA")):
        print(f"  - {r[0]} ({r[1]}): {r[8]}" + (f"  missing: {r[7]}" if r[7] else ""))
print("\n[2] FRESHNESS - tables stale > 14 days:")
for label, v in sorted(FRESH.items()):
    if v["max_dtm"]:
        stale = (TODAY - _dt.date.fromisoformat(v["max_dtm"][:10])).days
        if stale > 14: print(f"  - {label}: last change {v['max_dtm'][:10]} ({stale}d stale)")
print("\n[3] DATA QUALITY - source-side issues:")
for t in DQ:
    if t[4] == "ISSUE": print(f"  - {t[1]}: {t[0]} = {t[2]}  ({t[3]})")
print("\n[4] ERRORS / UNKNOWN (could not measure):")
for k, e in ERRORS.items(): print(f"  - {k}: {e[:80]}")
print("=" * 100)

payload2 = json.dumps({"probe": "13_DQ_TESTS", "run_ts": TS, "dq": DQ, "stats": STATS}, default=str)
spark.createDataFrame([("13_DQ_TESTS", TS, payload2)], "probe string, run_ts string, payload string") \
     .write.format("delta").mode("append").saveAsTable("mars_dev.audit.probe_results")
for i in range(0, len(payload2), 40000):
    print(f"<<<PART {i//40000 + 1}>>>"); print(payload2[i:i+40000])
print("\nAll CSV locations:"); [print(f"  {k}: {v}") for k, v in SAVED.items()]
