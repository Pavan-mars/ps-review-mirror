# Databricks notebook source
# MAGIC %md
# MAGIC # 05 — HEAVY-TABLE MONTHLY COUNTS, THE PATIENT RETRY  (read-only)
# MAGIC
# MAGIC **Why probes 01/04 showed ORA-01013 on the big tables — and why it is NOT a connection problem.**
# MAGIC `ORA-01013 = "user requested cancel"` is our own `queryTimeout` firing. The logon, dictionary queries
# MAGIC and mid-size counts all succeeded in the same runs — the tunnel is fine. A COUNT spanning the whole
# MAGIC Apr→Sep window on a 1–2B-row table is simply more work than 300s allows over this VPN.
# MAGIC
# MAGIC This retry changes three things, per table:
# MAGIC 1. **One month per query** (an indexed range scan of ~1/5th the window each time),
# MAGIC 2. **Pull watermark from probe 04's INDEX findings** (e.g. abp_tap on `EDW_UPDATED_DTM`,
# MAGIC    NOT `TRANSACTION_DTM` which has no index; device_metric on `EDW_INSERTED_DTM`),
# MAGIC 3. **30-minute timeouts** and per-query progress prints, so slow ≠ dead.
# MAGIC
# MAGIC `msg_count` (159M, no useful index) is LAST and skippable — set `INCLUDE_SCAN_TABLES=False` during
# MAGIC business hours; its count is a full scan however we phrase it, so run it off-hours.
# MAGIC Results append to `mars_dev.audit.probe_results`. Safe to Run All; safe to re-run.

# COMMAND ----------
# ============================== CELL 1 : CONFIG ==============================
import datetime as _dt, json, time
ODS_HOST, ODS_PORT, SCOPE = "10.3.10.30", 1521, "cubic"
SDU = 512
QUERY_TO   = 1800                 # 30 min per query
READ_TO    = "2100000"            # ms — a shade above queryTimeout
INCR_FROM  = "2026-04-12"
SENTINEL   = (_dt.date.today() + _dt.timedelta(days=1)).strftime("%Y-%m-%d")
RESULTS_TB = "mars_dev.audit.probe_results"
INCLUDE_SCAN_TABLES = True        # False = skip the no-index full-scan tables (run those off-hours)

# (bronze, OWNER, TABLE, pull_wm, kind, indexed?) — pull_wm chosen from probe 04 ALL_IND_COLUMNS results.
RETRY = [
 ("edw_device_event",           "EDW",      "DEVICE_EVENT",       "EDW_UPDATED_DTM",  "dtm",    True),
 ("edw_abp_tap",                "EDW",      "ABP_TAP",            "EDW_UPDATED_DTM",  "dtm",    True),
 ("edw_device_metric",          "EDW",      "DEVICE_METRIC",      "EDW_INSERTED_DTM", "dtm",    True),
 ("ncs_stage_cashbox_tracking", "NCS_STAGE","CASHBOX_TRACKING",   "INSERTED_DTM",     "dtm",    True),
 ("ncs_stage_sale_transaction", "NCS_STAGE","SALE_TRANSACTION",   "DW_INSERTED_DAY",  "daykey", True),
]
SCAN_TABLES = [  # no helpful index — every predicate full-scans; off-hours only
 ("ncs_stage_device_end_of_day_msg_count","NCS_STAGE","DEVICE_END_OF_DAY_MSG_COUNT","TRANSIT_DAY_KEY","daykey", False),
 ("edw_metric_summary_by_day",           "EDW",      "METRIC_SUMMARY_BY_DAY",      "TRANSIT_DAY_KEY","daykey", True),  # indexed but timed out at 300s — give it 1800s
]
ALLR = RETRY + (SCAN_TABLES if INCLUDE_SCAN_TABLES else [])

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
            .option("oracle.net.CONNECT_TIMEOUT", "10000").option("oracle.jdbc.ReadTimeout", READ_TO)
            .option("sessionInitStatement", "ALTER SESSION SET NLS_DATE_FORMAT='YYYY-MM-DD HH24:MI:SS'")
            .load())

def month_edges(start, end_excl):
    """[(lo, hi_excl), ...] calendar-month slices between two ISO dates."""
    out, d = [], _dt.date.fromisoformat(start)
    end = _dt.date.fromisoformat(end_excl)
    while d < end:
        nxt = (d.replace(day=1) + _dt.timedelta(days=32)).replace(day=1)
        out.append((d.isoformat(), min(nxt, end).isoformat()))
        d = min(nxt, end)
    return out

ora("(SELECT 1 OK FROM dual) q", 20).collect()
print(f"05_retry | {len(ALLR)} tables | {INCR_FROM}..{SENTINEL} | {QUERY_TO}s/query | "
      f"months={ [m[0][:7] for m in month_edges(INCR_FROM, SENTINEL)] }")

# COMMAND ----------
# ============================== CELL 2 : PER-MONTH COUNTS (patient, indexed watermarks) ===============
RES = []
for b, o, t, wm, kind, idx in ALLR:
    per, total, err = {}, 0, None
    t0 = time.time()
    print(f"\n### {b}  [{o}.{t}]  wm={wm} ({'indexed' if idx else 'NO INDEX - full scans'})")
    for lo, hi in month_edges(INCR_FROM, SENTINEL):
        try:
            if kind == "daykey":
                pred = f"{wm} >= {int(lo.replace('-',''))} AND {wm} < {int(hi.replace('-',''))}"
            else:
                pred = f"{wm} >= TIMESTAMP '{lo} 00:00:00' AND {wm} < TIMESTAMP '{hi} 00:00:00'"
            q0 = time.time()
            n = int(ora(f"(SELECT COUNT(*) N FROM {o}.{t} WHERE {pred}) q").collect()[0]["N"])
            per[lo[:7]] = n; total += n
            print(f"    {lo[:7]}: {n:>12,}   ({time.time()-q0:5.0f}s)")
        except Exception as e:
            err = f"{lo[:7]}: {str(e).splitlines()[0][:80]}"
            print(f"    {lo[:7]}: ERROR {err}"); break     # stop on first failure — later months will fail the same way
    mx = None
    if not err:
        try:
            if kind == "daykey":
                mx = str(ora(f"(SELECT MAX({wm}) HI FROM {o}.{t} WHERE {wm} >= {int(INCR_FROM.replace('-',''))} "
                             f"AND {wm} < {int(SENTINEL.replace('-',''))}) q").collect()[0]["HI"])
            else:
                mx = str(ora(f"(SELECT MAX({wm}) HI FROM {o}.{t} WHERE {wm} >= TIMESTAMP '{INCR_FROM} 00:00:00' "
                             f"AND {wm} < DATE '{SENTINEL}') q").collect()[0]["HI"])
        except Exception as e:
            err = f"max: {str(e).splitlines()[0][:80]}"
    RES.append({"bronze": b, "src": f"{o}.{t}", "wm": wm, "indexed": idx,
                "incr_since_0412": total if not err else None, "monthly": per,
                "max": mx, "error": err, "elapsed_s": round(time.time()-t0)})
    print(f"    => total={total:,}  max={mx}  elapsed={round(time.time()-t0)}s" + (f"  ERR={err}" if err else ""))

# COMMAND ----------
# ============================== CELL 3 : PERSIST + COMPACT SUMMARY ==============================
out = {"probe": "05_HEAVY_monthly_counts_retry", "run_ts": _dt.datetime.utcnow().isoformat() + "Z",
       "incr_from": INCR_FROM, "sentinel": SENTINEL, "results": RES}
payload = json.dumps(out, default=str)
(spark.createDataFrame([(out["probe"], out["run_ts"], payload)], "probe string, run_ts string, payload string")
 .write.format("delta").mode("append").saveAsTable(RESULTS_TB))
print(f"persisted -> {RESULTS_TB}\n")
print(f"{'bronze table':40s} {'incr since 12-Apr':>18s}  {'max(wm)':22s} note")
print("-" * 100)
for r in RES:
    n = f"{r['incr_since_0412']:,}" if r["incr_since_0412"] is not None else "STILL UNKNOWN"
    print(f"{r['bronze']:40s} {n:>18s}  {str(r['max']):22s} {r['error'] or 'ok'}")
CH = 40000
print(f"\n<<<PROBE05_JSON parts={(len(payload)+CH-1)//CH}>>>")
for i in range(0, len(payload), CH):
    print(f"<<<PART {i//CH + 1}>>>"); print(payload[i:i+CH])
print("<<<PROBE05_JSON_END>>>")
