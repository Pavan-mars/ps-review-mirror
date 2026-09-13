# Databricks notebook source
# MAGIC %md
# MAGIC # 14 — RECHECK: Cubic backfill verification  (Oracle-side · read-only)
# MAGIC
# MAGIC Cubic's data team reports that EIGHT tables have been updated "with data till today":
# MAGIC CASHBOX_TRACKING, SALE_TRANSACTION, AVAILABILITY_EVENTS, AVAILABILITY_RELIEF, DEVICE_METRIC,
# MAGIC KPI_SUMMARY_BY_DAY, KPI_DETAIL_EVENTS_BY_DAY, DEVICE_END_OF_DAY.
# MAGIC
# MAGIC This notebook verifies that claim against the 07-Sep-2026 audit baseline:
# MAGIC 1. **Continuity 12-Apr-2026 → today** for exactly those 8 tables (same per-day queries as NB13,
# MAGIC    indexed columns only) — each result printed as WAS → NOW → verdict, with April recovered days.
# MAGIC 2. **The same 26 data-quality checks** re-run verbatim (garbage dates, duplicate/NULL keys,
# MAGIC    April backfill, restamps, VARCHAR dates) — so pass/fail is directly comparable.
# MAGIC
# MAGIC Read-only against Oracle. Outputs: recheck CSVs via the portable save chain (DBFS -> S3 _audit),
# MAGIC JSON to mars_dev.audit.probe_results. Run on the Ventra (VPN) compute. ~20-40 min.

# COMMAND ----------
# ============================== CELL 1 : CONFIG + ORACLE HELPER ==============================
import datetime as _dt, json
from pyspark.sql import functions as F

WINDOW_START = "2026-04-12"
TODAY     = _dt.date.today()
TOMORROW  = (TODAY + _dt.timedelta(days=1)).isoformat()
TKEY_LO, TKEY_HI = int(WINDOW_START.replace("-", "")), int(TOMORROW.replace("-", ""))
CURRENT_LAG_OK = 2                     # last data within N days of today counts as "current till date"
TS        = _dt.datetime.now().strftime("%Y%m%d_%H%M%S")
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

S3_AUDIT = "s3://cubic-mars-pm-s3-datalake-dev-raw-170202974600/chicago_ventra/_audit/recheck_" + TS
SAVED = {}
def save_csv(name, text):
    import os as _os
    errs = []
    try:
        d = f"/dbfs/FileStore/chicago_continuity/recheck_{TS}"
        _os.makedirs(d, exist_ok=True)
        with open(f"{d}/{name}", "w") as f: f.write(text)
        SAVED[name] = f"/FileStore/chicago_continuity/recheck_{TS}/{name}"
        print(f"saved {name} -> {SAVED[name]}"); return
    except Exception as e: errs.append(f"fuse: {str(e).splitlines()[0][:50]}")
    try:
        p = f"dbfs:/FileStore/chicago_continuity/recheck_{TS}/{name}"
        dbutils.fs.put(p, text, True); SAVED[name] = p
        print(f"saved {name} -> {p}"); return
    except Exception as e: errs.append(f"dbutils: {str(e).splitlines()[0][:50]}")
    try:
        p = f"{S3_AUDIT}/{name}"
        dbutils.fs.put(p, text, True); SAVED[name] = p
        print(f"saved {name} -> {p}  (fetch: aws s3 cp '{p}' .)"); return
    except Exception as e: errs.append(f"s3: {str(e).splitlines()[0][:50]}")
    print(f"COULD NOT SAVE {name} ({'; '.join(errs)}) — content follows:"); print(text)

ora("(SELECT 1 OK FROM dual) q", 20).collect()
print(f"NB14 recheck | window {WINDOW_START} -> {TODAY} | current = last data within {CURRENT_LAG_OK}d of today")

# COMMAND ----------
# ============================== CELL 2 : THE 8 CLAIMED TABLES + 07-SEP BASELINE ==============================
def dtm_daily(ot, col):
    return (f"(SELECT TRUNC({col}) D, COUNT(*) N FROM {ot} "
            f"WHERE {col} >= DATE '{WINDOW_START}' AND {col} < DATE '{TOMORROW}' GROUP BY TRUNC({col})) q")
def daykey_daily(ot, col):
    return (f"(SELECT {col} D, COUNT(*) N FROM {ot} "
            f"WHERE {col} >= {TKEY_LO} AND {col} < {TKEY_HI} GROUP BY {col}) q")
def biz_from_pull(ot, pull, biz):
    return (f"(SELECT TRUNC({biz}) D, COUNT(*) N FROM {ot} "
            f"WHERE {pull} >= DATE '{WINDOW_START}' AND {pull} < DATE '{TOMORROW}' GROUP BY TRUNC({biz})) q")
def biz_from_daykey_pull(ot, pull, biz):
    return (f"(SELECT TRUNC({biz}) D, COUNT(*) N FROM {ot} "
            f"WHERE {pull} >= {TKEY_LO} AND {pull} < {TKEY_HI} GROUP BY TRUNC({biz})) q")

# (label, series, sql, timeout)  — same query shapes as the 07-Sep audit, restricted to the 8 tables
SERIES_PLAN = [
 ("ncs_stage_cashbox_tracking", "inserted", dtm_daily("NCS_STAGE.CASHBOX_TRACKING", "INSERTED_DTM"), 1800),
 ("ncs_stage_cashbox_tracking", "business", biz_from_pull("NCS_STAGE.CASHBOX_TRACKING", "INSERTED_DTM", "EVENT_DTM"), 1800),
 ("ncs_stage_sale_transaction", "inserted", daykey_daily("NCS_STAGE.SALE_TRANSACTION", "DW_INSERTED_DAY"), 1800),
 ("ncs_stage_sale_transaction", "business", biz_from_daykey_pull("NCS_STAGE.SALE_TRANSACTION", "DW_INSERTED_DAY", "TRANSACTION_DTM"), 1800),
 ("edw_availability_events",    "business", daykey_daily("EDW.AVAILABILITY_EVENTS", "TRANSIT_DAY_KEY"), 900),
 ("edw_availability_relief",    "business", dtm_daily("EDW.AVAILABILITY_RELIEF", "START_DTM"), 900),
 ("edw_device_metric",          "inserted", dtm_daily("EDW.DEVICE_METRIC", "EDW_INSERTED_DTM"), 1800),
 ("edw_kpi_summary_by_day",     "business", daykey_daily("EDW.KPI_SUMMARY_BY_DAY", "TRANSIT_DAY_KEY"), 900),
 ("edw_kpi_detail_events_by_day","business", dtm_daily("EDW.KPI_DETAIL_EVENTS_BY_DAY", "START_DTM"), 900),
 ("ncs_stage_device_end_of_day","business", daykey_daily("NCS_STAGE.DEVICE_END_OF_DAY", "TRANSIT_DAY_KEY"), 1800),
]

# 07-Sep-2026 audit baseline: last data day + missing April days (business series), per table
BASELINE = {
 "ncs_stage_cashbox_tracking":  {"last": "2026-08-17", "apr_missing": set(range(13, 30))},
 "ncs_stage_sale_transaction":  {"last": "2026-08-17", "apr_missing": {12,13,14,15,17,18,19,21,22,24,25,26,27,29}},
 "edw_availability_events":     {"last": "2026-08-17", "apr_missing": set(range(12, 31))},
 "edw_availability_relief":     {"last": "2026-08-20", "apr_missing": None},   # sparse table - freshness only
 "edw_device_metric":           {"last": "2026-08-17", "apr_missing": set()},
 "edw_kpi_summary_by_day":      {"last": "2026-08-16", "apr_missing": {12,14,16,18}|set(range(20, 31))},
 "edw_kpi_detail_events_by_day":{"last": "2026-08-16", "apr_missing": set(range(12, 26))|{28,29}},
 "ncs_stage_device_end_of_day": {"last": "2026-08-17", "apr_missing": set()},
}
print(f"{len(SERIES_PLAN)} series over 8 claimed tables; baseline = 07-Sep-2026 audit")

# COMMAND ----------
# ============================== CELL 3 : CONTINUITY RECHECK — WAS -> NOW -> VERDICT ==============================
def _iso(d):
    if d is None: return None
    s = str(d)
    if "-" in s: return s[:10]
    s = s.split(".")[0]
    return f"{s[:4]}-{s[4:6]}-{s[6:8]}" if len(s) == 8 and s.isdigit() else s[:10]

W0 = _dt.date.fromisoformat(WINDOW_START)
def day_range(a, b):
    d, out = a, []
    while d <= b: out.append(d); d += _dt.timedelta(days=1)
    return out
def compress(days):
    if not days: return ""
    runs, s, p = [], days[0], days[0]
    for d in days[1:]:
        if (d - p).days > 1: runs.append((s, p)); s = d
        p = d
    runs.append((s, p))
    return "; ".join(a.isoformat() if a == b else f"{a.isoformat()}..{b.isoformat()}" for a, b in runs)

SERIES, RESULTS, ERRORS = {}, [], {}
for label, sname, sql, secs in SERIES_PLAN:
    key = f"{label}::{sname}"
    try:
        rows = ora(sql, secs).collect()
        pts = sorted((_iso(r["D"]), int(r["N"])) for r in rows if r["D"] is not None)
        SERIES[key] = pts
        print(f"OK   {key:50s} {len(pts):4d} daily points" + (f"  [{pts[0][0]} -> {pts[-1][0]}]" if pts else "  [EMPTY]"))
    except Exception as e:
        ERRORS[key] = str(e).splitlines()[0][:120]
        print(f"FAIL {key:50s} {ERRORS[key][:80]}")

print("\n" + "=" * 112)
print(f"{'table':30s} {'last WAS':10s} {'last NOW':10s} {'lag':>4s}  {'Apr missing WAS':>15s} {'NOW':>4s}  verdict")
print("-" * 112)
for label, base in BASELINE.items():
    key = f"{label}::business" if f"{label}::business" in SERIES else f"{label}::inserted"
    pts = SERIES.get(key)
    if pts is None:
        RESULTS.append((label, base["last"], None, None, None, None, "", f"UNKNOWN ({ERRORS.get(key,'no data')[:50]})")); continue
    if not pts:
        RESULTS.append((label, base["last"], None, None, None, None, "", "EMPTY WINDOW — claim NOT confirmed")); continue
    last = _dt.date.fromisoformat(pts[-1][0])
    lag = (TODAY - last).days
    have = {p[0] for p in pts}
    apr_now = sorted(d for d in range(12, 31)
                     if _dt.date(2026, 4, d) <= last and f"2026-04-{d:02d}" not in have) if base["apr_missing"] is not None else None
    # full-window gaps beyond April, for the CSV
    missing_all = [d for d in day_range(max(W0, _dt.date.fromisoformat(pts[0][0])), last) if d.isoformat() not in have]
    was_apr = len(base["apr_missing"]) if base["apr_missing"] is not None else None
    now_apr = len(apr_now) if apr_now is not None else None
    recovered = (sorted(base["apr_missing"] - set(apr_now)) if base["apr_missing"] is not None else [])
    current = lag <= CURRENT_LAG_OK
    apr_ok = (now_apr == 0) if now_apr is not None else True
    if current and apr_ok and base["apr_missing"]:            verdict = "FULLY RESOLVED — current AND April recovered"
    elif current and apr_ok:                                  verdict = "RESOLVED — current till date"
    elif current and not apr_ok:                              verdict = f"CURRENT, but April still missing {now_apr} day(s): {','.join(str(x) for x in apr_now)}"
    elif not current and apr_ok and base["apr_missing"]:      verdict = f"April recovered, but NOT current (last {last}, {lag}d behind)"
    elif last.isoformat() > base["last"]:                     verdict = f"IMPROVED but NOT current (last {last}, {lag}d behind)" + (f"; April still missing {now_apr}d" if now_apr else "")
    else:                                                     verdict = f"UNCHANGED since 07-Sep audit (last {last}) — claim NOT confirmed"
    RESULTS.append((label, base["last"], last.isoformat(), lag, was_apr, now_apr, compress(missing_all), verdict))
    print(f"{label:30s} {base['last']:10s} {last.isoformat():10s} {lag:3d}d  {str(was_apr):>15s} {str(now_apr):>4s}  {verdict}")
    if recovered: print(f"{'':30s} April days recovered: {','.join(str(x) for x in recovered)}")
print("=" * 112)

# COMMAND ----------
# ============================== CELL 4 : THE SAME 26 DQ CHECKS (verbatim from the 07-Sep audit) ==============================
WS, TM, TKH = WINDOW_START, TOMORROW, TKEY_HI
def one(sql, secs=600): return ora(sql, secs).first().asDict()
def n0(x):
    try: return int(x or 0)
    except Exception: return 0

DQ = []
def add(test, table, value, detail, bad):
    DQ.append((test, table, str(value), detail, "ISSUE" if bad else "OK"))
    print(f"{'ISSUE' if bad else 'ok   '} {test:26s} {table:38s} {value}  {detail}")

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
try:
    r = one(f"(SELECT COUNT(*) N, MAX(DW_INSERTED_DAY) MX FROM NCS_STAGE.SALE_TRANSACTION WHERE DW_INSERTED_DAY >= {TKH}) q")
    add("future_dated_rows", "ncs_stage_sale_transaction", n0(r["N"]), f"max DW_INSERTED_DAY = {r['MX']}", n0(r["N"]) > 0)
except Exception as e:
    add("garbage_date_probe", "ncs_stage_sale_transaction", "-", str(e).splitlines()[0][:70], False)
try:
    r = one(f"(SELECT COUNT(*) N, MAX(TRANSIT_DAY_KEY) MX FROM EDW.DEVICE_METRIC "
            f"WHERE EDW_INSERTED_DTM >= DATE '{WS}' AND TRANSIT_DAY_KEY >= {TKH}) q", 1800)
    add("future_business_key", "edw_device_metric", n0(r["N"]), f"max TRANSIT_DAY_KEY = {r['MX']} (in post-12-Apr inserts)", n0(r["N"]) > 0)
except Exception as e:
    add("future_business_key", "edw_device_metric", "-", str(e).splitlines()[0][:70], False)
try:
    r = one("(SELECT SUM(CASE WHEN TRANSIT_DAY_KEY >= %d THEN 1 ELSE 0 END) FUT,"
            " SUM(CASE WHEN TRANSIT_DAY_KEY < 20000101 THEN 1 ELSE 0 END) OLD,"
            " MIN(TRANSIT_DAY_KEY) MN, MAX(TRANSIT_DAY_KEY) MX FROM NCS_STAGE.DEVICE_END_OF_DAY) q" % TKH, 1800)
    add("future_dated_rows", "ncs_stage_device_end_of_day", n0(r["FUT"]), f"max key = {r['MX']}", n0(r["FUT"]) > 0)
    add("pre2000_rows", "ncs_stage_device_end_of_day", n0(r["OLD"]), f"min key = {r['MN']}", n0(r["OLD"]) > 0)
except Exception as e:
    add("garbage_date_probe", "ncs_stage_device_end_of_day", "-", str(e).splitlines()[0][:70], False)
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
try:
    r = one(f"(SELECT MIN(EVENT_DTM) MN, SUM(CASE WHEN EVENT_DTM < DATE '{WS}' THEN 1 ELSE 0 END) OLDN,"
            f" SUM(CASE WHEN EVENT_DTM >= DATE '{WS}' AND EVENT_DTM < DATE '2026-05-01' THEN 1 ELSE 0 END) APR,"
            f" COUNT(*) N FROM NCS_STAGE.CASHBOX_TRACKING WHERE INSERTED_DTM >= DATE '{WS}' AND INSERTED_DTM < DATE '{TM}') q", 1800)
    add("apr12_30_events_backfilled", "ncs_stage_cashbox_tracking", n0(r["APR"]),
        f"window inserts carry {n0(r['APR']):,} events dated 12-30 Apr (earliest {r['MN']}); pre-window: {n0(r['OLDN']):,}  [was 1,258 on 07-Sep]",
        n0(r["APR"]) == 0)
except Exception as e:
    add("apr12_30_events_backfilled", "ncs_stage_cashbox_tracking", "-", str(e).splitlines()[0][:70], False)
try:
    r = one(f"(SELECT MIN(TRANSACTION_DTM) MN, SUM(CASE WHEN TRANSACTION_DTM >= DATE '{WS}' AND TRANSACTION_DTM < DATE '2026-05-01' THEN 1 ELSE 0 END) APR,"
            f" COUNT(*) N FROM NCS_STAGE.SALE_TRANSACTION WHERE DW_INSERTED_DAY >= {TKEY_LO} AND DW_INSERTED_DAY < {TKH}) q", 1800)
    add("apr12_30_events_backfilled", "ncs_stage_sale_transaction", n0(r["APR"]),
        f"window inserts carry {n0(r['APR']):,} transactions dated 12-30 Apr (earliest {r['MN']})  [was 106 on 07-Sep]", n0(r["APR"]) == 0)
except Exception as e:
    add("apr12_30_events_backfilled", "ncs_stage_sale_transaction", "-", str(e).splitlines()[0][:70], False)
try:
    r = one("(SELECT COUNT(*) N, MIN(INSERTED_DTM) MN, MAX(INSERTED_DTM) MX FROM EDW.AVAILABILITY_RELIEF) q")
    restamped = r["MN"] is not None and str(r["MN"])[:10] == str(r["MX"])[:10]
    add("inserted_dtm_restamped", "edw_availability_relief", str(r["MN"])[:10],
        (f"ALL {n0(r['N']):,} rows share one INSERTED_DTM date [{str(r['MN'])[:10]}] - column unusable as CDC watermark" if restamped
         else f"{n0(r['N']):,} rows, INSERTED_DTM spans [{str(r['MN'])[:10]} .. {str(r['MX'])[:10]}] - restamp cleared"), restamped)
except Exception as e:
    add("inserted_dtm_restamped", "edw_availability_relief", "-", str(e).splitlines()[0][:70], False)
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

print(f"\nDQ checks done: {sum(1 for d in DQ if d[4]=='ISSUE')} issues / {len(DQ)} checks  [07-Sep baseline: 4 issues / 26 checks]")

# COMMAND ----------
# ============================== CELL 5 : VERDICT SUMMARY + CSVs + AUDIT LOG ==============================
print("=" * 100)
print("RECHECK VERDICT vs CUBIC'S CLAIM ('8 tables updated with data till today')")
print("=" * 100)
ok = sum(1 for r in RESULTS if r[7].startswith(("FULLY RESOLVED", "RESOLVED")))
print(f"\nContinuity: {ok} of {len(RESULTS)} claimed tables verified current" +
      (f" (lag tolerance {CURRENT_LAG_OK}d)" if ok else ""))
for r in RESULTS: print(f"  - {r[0]}: {r[7]}")
print("\nData quality (same 26 checks as 07-Sep):")
base_dq = {("duplicate_keys_window","ncs_stage_cashbox_tracking"): "ISSUE (3,825,395 dups / 7 ids)",
           ("future_dated_rows","ncs_stage_device_end_of_day"): "ISSUE (72 rows)",
           ("inserted_dtm_restamped","edw_availability_relief"): "ISSUE (all 27-Aug)",
           ("date_stored_as_varchar","cta_servicenow_data_from_jumpbox"): "ISSUE (VARCHAR)"}
for t in DQ:
    was = base_dq.get((t[0], t[1]))
    if t[4] == "ISSUE" or was:
        print(f"  - {t[1]}: {t[0]} = {t[2]} [{t[4]}]" + (f"  (07-Sep: {was})" if was else "  (NEW since 07-Sep)"))
print("=" * 100)

buf = ["table,last_day_07sep,last_day_now,days_behind_today,april_missing_07sep,april_missing_now,all_missing_ranges_now,verdict"]
for r in RESULTS:
    buf.append(",".join('"' + str(x if x is not None else "") + '"' for x in r))
save_csv("recheck_summary.csv", "\n".join(buf) + "\n")
buf = ["table,series,day,rows"]
for key, pts in sorted(SERIES.items()):
    label, sname = key.split("::")
    for d, n in pts: buf.append(f"{label},{sname},{d},{n}")
save_csv("recheck_daily_detail.csv", "\n".join(buf) + "\n")
buf = ["test,table,value,detail,verdict"]
for t in DQ: buf.append(",".join('"' + str(x).replace('"', "'") + '"' for x in t))
save_csv("recheck_dq_findings.csv", "\n".join(buf) + "\n")

payload = json.dumps({"probe": "14_RECHECK_backfill", "run_ts": TS, "results": RESULTS,
                      "series": SERIES, "dq": DQ, "errors": ERRORS}, default=str)
spark.createDataFrame([("14_RECHECK_backfill", TS, payload)], "probe string, run_ts string, payload string") \
     .write.format("delta").mode("append").saveAsTable("mars_dev.audit.probe_results")
for i in range(0, len(payload), 40000):
    print(f"<<<PART {i//40000 + 1}>>>"); print(payload[i:i+40000])
print("\nCSV locations:"); [print(f"  {k}: {v}") for k, v in SAVED.items()]
