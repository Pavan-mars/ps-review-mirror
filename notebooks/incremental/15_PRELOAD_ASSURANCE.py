# Databricks notebook source
# MAGIC %md
# MAGIC # 15 — PRE-LOAD ASSURANCE  (final green-light checks before the NB12 apply · read-only)
# MAGIC
# MAGIC The 08-Sep recheck proved Cubic REBUILT the tables (backdated insert timestamps) rather than
# MAGIC appending. That creates risks the earlier audits never had to test, plus two in-scope tables
# MAGIC have never been tested at all. Five groups:
# MAGIC
# MAGIC | # | Test | Why |
# MAGIC |---|---|---|
# MAGIC | 1 | **History integrity** — Oracle vs bronze row counts BEFORE 12-Apr (same predicate both sides) | CDC never touches pre-watermark rows; if the rebuild changed history, bronze diverges forever |
# MAGIC | 2 | **Overlap rewrite detection** — 07-Sep vs 08-Sep saved daily series joined on (table, day) | catches re-issued / altered / double-delivered days inside the window; zero Oracle load |
# MAGIC | 3 | **Fleet coverage** — distinct devices on sample April vs August days | a backfill can be day-complete but device-partial |
# MAGIC | 4 | **Never-tested tables** — EDW.USE_TRANSACTION (dictionary: partitions + indexes) and DEVICE_END_OF_DAY_MSG_COUNT (one off-hours scan, gated) | both feed NB12 steps blind today |
# MAGIC | 5 | **Loose ends** — abp_tap exact freshness (indexed MAX); device_event NULL check, chunked monthly so it finally completes | close the two timeouts |
# MAGIC
# MAGIC Run on the Ventra (VPN) compute. ~30 min with INCLUDE_OFFHOURS=False (default); the msg_count
# MAGIC scan adds one 159M-row pass — run that off-hours only. PASS on groups 1-3 = green light for NB12.

# COMMAND ----------
# ============================== CELL 1 : CONFIG + HELPERS ==============================
import datetime as _dt, json
from pyspark.sql import functions as F

INCLUDE_OFFHOURS = False       # True (off-hours only) to include the msg_count 159M-row scan
WINDOW_START = "2026-04-12"
TODAY    = _dt.date.today()
TOMORROW = (TODAY + _dt.timedelta(days=1)).isoformat()
TKEY_LO, TKEY_HI = int(WINDOW_START.replace("-", "")), int(TOMORROW.replace("-", ""))
TS       = _dt.datetime.now().strftime("%Y%m%d_%H%M%S")
AUDIT    = "s3://cubic-mars-pm-s3-datalake-dev-raw-170202974600/chicago_ventra/_audit"
BASE_0907 = f"{AUDIT}/continuity_20260907_025806/daily_detail.csv"
BASE_0908 = f"{AUDIT}/recheck_20260908_144339/recheck_daily_detail.csv"
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
def one(sql, secs=1800): return ora(sql, secs).first().asDict()
def n0(x):
    try: return int(x or 0)
    except Exception: return 0

TESTS = []   # (group, test, detail, verdict)
def rec(group, test, detail, verdict):
    TESTS.append((group, test, detail, verdict))
    print(f"[{verdict:9s}] {group:18s} {test:34s} {detail}")

ora("(SELECT 1 OK FROM dual) q", 20).collect()
print(f"NB15 pre-load assurance | {TODAY} | INCLUDE_OFFHOURS={INCLUDE_OFFHOURS}")

# COMMAND ----------
# ============================== CELL 2 : GROUP 1 — HISTORY INTEGRITY (scope-aware, v2) ==============================
# v2 FIX: bronze was loaded SCOPED (~2023-07+); v1 wrongly compared it to Oracle's FULL history
# (end_of_day reaches 2012). Now the comparison window is derived from bronze's own span of the
# indexed column, and the SAME window is counted on both sides.
CHECKS = [
 ("ncs_stage_cashbox_tracking", "NCS_STAGE.CASHBOX_TRACKING",  "INSERTED_DTM",     "ts"),
 ("ncs_stage_sale_transaction", "NCS_STAGE.SALE_TRANSACTION",  "DW_INSERTED_DAY",  "key"),
 ("edw_device_metric",          "EDW.DEVICE_METRIC",           "EDW_INSERTED_DTM", "ts"),
 ("ncs_stage_device_end_of_day","NCS_STAGE.DEVICE_END_OF_DAY", "TRANSIT_DAY_KEY",  "key"),
]
for bt, ot, col, kind in CHECKS:
    try:
        b = spark.table(f"mars_dev.bronze.`{bt}`")
        cols = {c.lower(): c for c in b.columns}
        bc = cols.get(col.lower())
        if bc is None:
            rec("hist_integrity", bt, f"bronze lacks column {col}", "UNKNOWN"); continue
        lo = b.agg(F.min(bc)).first()[0]
        if lo is None:
            rec("hist_integrity", bt, "bronze empty", "UNKNOWN"); continue
        if kind == "key":
            lo_v = int(float(lo)); hi_v = TKEY_LO
            pred = f"{col} >= {lo_v} AND {col} < {hi_v}"
            n_brz = b.where((F.col(bc) >= lo_v) & (F.col(bc) < hi_v)).count()
            win = f"[{lo_v}..{hi_v})"
        else:
            lo_s = str(lo)[:19]
            pred = f"{col} >= TIMESTAMP '{lo_s}' AND {col} < DATE '{WINDOW_START}'"
            n_brz = b.where((F.col(bc) >= lo_s) & (F.col(bc) < WINDOW_START)).count()
            win = f"[{lo_s}..{WINDOW_START})"
        n_ora = n0(one(f"(SELECT COUNT(*) N FROM {ot} WHERE {pred}) q", 1800)["N"])
        diff = n_ora - n_brz
        pct = abs(diff) / max(n_brz, 1) * 100
        detail = f"window {win}: oracle={n_ora:,} bronze={n_brz:,} diff={diff:+,} ({pct:.3f}%)"
        rec("hist_integrity", bt, detail, "PASS" if pct <= 0.1 else ("WARN" if pct <= 1.0 else "FAIL"))
    except Exception as e:
        rec("hist_integrity", bt, str(e).splitlines()[0][:80], "UNKNOWN")

# COMMAND ----------
# ============================== CELL 3 : GROUP 2 — OVERLAP CHANGES, CLASSIFIED (v2, informational) ==============================
# v2 FIX: v1 flagged the backfill itself. Changes are now classified; and since bronze holds NOTHING
# after 11-Apr yet, changes inside the load window cannot diverge bronze — we pull current state.
# Only substantial REMOVALS are surfaced (rows that existed on 07-Sep and are now gone), as WARN.
try:
    old = spark.read.option("header", True).csv(BASE_0907).withColumnRenamed("rows", "rows_0907")
    new = spark.read.option("header", True).csv(BASE_0908).withColumnRenamed("rows", "rows_0908")
    j = (old.join(new, ["table", "series", "day"], "inner")
            .where(F.col("day") <= "2026-09-06")
            .withColumn("r7", F.col("rows_0907").cast("long"))
            .withColumn("r8", F.col("rows_0908").cast("long"))
            .withColumn("diff", F.col("r8") - F.col("r7"))
            .withColumn("pct", F.abs(F.col("diff")) / F.greatest(F.col("r7"), F.lit(1)) * 100)
            .withColumn("class",
                F.when((F.col("r7") < 1000) & (F.col("r8") >= 1000), "EXPECTED_BACKFILL")
                 .when(F.col("day") == "2026-08-17", "BOUNDARY_DAY_COMPLETED")
                 .when(F.col("diff") > 0, "ADDED")
                 .when(F.col("diff") < 0, "REMOVED")
                 .otherwise("UNCHANGED")))
    n_days = j.count()
    changed = j.where((F.col("pct") > 1.0) & (F.abs(F.col("diff")) > 100))
    summary = {r["class"]: r["n"] for r in changed.groupBy("class").agg(F.count("*").alias("n")).collect()}
    rec("overlap_rewrite", "days_compared", f"{n_days:,} points; changed>1%: {summary or 'none'}", "INFO")
    removed = changed.where((F.col("class") == "REMOVED") & (F.col("pct") > 2.0) & (F.col("r7") > 1000))
    n_rm = removed.count()
    if n_rm == 0:
        rec("overlap_rewrite", "unexplained_removals",
            "0 substantial removals — all changes are the backfill, the completed 17-Aug boundary, or small additions; "
            "all inside the not-yet-loaded window, so bronze cannot diverge", "PASS")
    else:
        rec("overlap_rewrite", "unexplained_removals", f"{n_rm} substantial removed day(s) — inspect (informational; window not yet loaded)", "WARN")
        removed.orderBy(F.desc("pct")).select("table", "series", "day", "r7", "r8", "diff").show(30, False)
except Exception as e:
    rec("overlap_rewrite", "csv_compare", str(e).splitlines()[0][:90], "UNKNOWN")

# COMMAND ----------
# ============================== CELL 4 : GROUP 3 — FLEET COVERAGE ON BACKFILLED APRIL DAYS ==============================
# A backfill can be day-complete but device-partial. Distinct devices on sample April vs August days.
APR_DAYS = ["2026-04-14", "2026-04-22"]
AUG_DAYS = ["2026-08-20", "2026-09-02"]
def dev_count_ts(ot, col, day, secs=900):
    nxt = (_dt.date.fromisoformat(day) + _dt.timedelta(days=1)).isoformat()
    return n0(one(f"(SELECT COUNT(DISTINCT DEVICE_ID) N FROM {ot} "
                  f"WHERE {col} >= DATE '{day}' AND {col} < DATE '{nxt}') q", secs)["N"])
try:
    cov = {d: dev_count_ts("NCS_STAGE.CASHBOX_TRACKING", "INSERTED_DTM", d) for d in APR_DAYS + AUG_DAYS}
    apr, aug = min(cov[d] for d in APR_DAYS), max(cov[d] for d in AUG_DAYS)
    detail = "; ".join(f"{d}: {cov[d]:,}" for d in APR_DAYS + AUG_DAYS)
    rec("fleet_coverage", "cashbox distinct devices", detail,
        "PASS" if aug and apr >= 0.9 * aug else "FAIL")
except Exception as e:
    rec("fleet_coverage", "cashbox distinct devices", str(e).splitlines()[0][:80], "UNKNOWN")
try:  # end_of_day: one scan over the four day-keys
    keys = ",".join(d.replace("-", "") for d in APR_DAYS + AUG_DAYS)
    rows = ora(f"(SELECT TRANSIT_DAY_KEY K, COUNT(DISTINCT DEVICE_ID) N FROM NCS_STAGE.DEVICE_END_OF_DAY "
               f"WHERE TRANSIT_DAY_KEY IN ({keys}) GROUP BY TRANSIT_DAY_KEY) q", 1800).collect()
    cov = {str(int(float(r["K"]))): n0(r["N"]) for r in rows}
    apr_v = [cov.get(d.replace("-", ""), 0) for d in APR_DAYS]
    aug_v = [cov.get(d.replace("-", ""), 0) for d in AUG_DAYS]
    detail = "; ".join(f"{d}: {cov.get(d.replace('-', ''), 0):,}" for d in APR_DAYS + AUG_DAYS)
    rec("fleet_coverage", "end_of_day distinct devices", detail,
        "PASS" if max(aug_v) and min(apr_v) >= 0.9 * max(aug_v) else "FAIL")
except Exception as e:
    rec("fleet_coverage", "end_of_day distinct devices", str(e).splitlines()[0][:80], "UNKNOWN")

# COMMAND ----------
# ============================== CELL 5 : GROUP 4 — THE TWO NEVER-TESTED TABLES ==============================
# EDW.USE_TRANSACTION (5.9B rows): dictionary-only — partitioning + indexes + newest partitions.
try:
    p = one("(SELECT COUNT(*) N FROM all_part_tables WHERE owner='EDW' AND table_name='USE_TRANSACTION') q", 120)
    if n0(p["N"]) > 0:
        parts = ora("(SELECT partition_name, partition_position, num_rows, TO_CHAR(last_analyzed,'YYYY-MM-DD') LA "
                    "FROM all_tab_partitions WHERE table_owner='EDW' AND table_name='USE_TRANSACTION' "
                    "ORDER BY partition_position DESC FETCH FIRST 8 ROWS ONLY) q", 120).collect()
        detail = "PARTITIONED; newest: " + "; ".join(f"{r['PARTITION_NAME']}(rows~{r['NUM_ROWS']})" for r in parts[:4])
        rec("never_tested", "USE_TRANSACTION partitions", detail[:150], "INFO")
    else:
        rec("never_tested", "USE_TRANSACTION partitions", "NOT partitioned (5.9B heap table)", "INFO")
    idx = ora("(SELECT index_name, column_name, column_position FROM all_ind_columns "
              "WHERE table_owner='EDW' AND table_name='USE_TRANSACTION' AND column_position=1) q", 120).collect()
    leads = sorted({r["COLUMN_NAME"] for r in idx})
    rec("never_tested", "USE_TRANSACTION indexes", f"leading index cols: {leads or 'NONE'}", "INFO")
    if "TRANSIT_DAY_KEY" in leads:  # unbounded MAX = instant index min/max descent (a range bound forces a scan)
        r = one("(SELECT MAX(TRANSIT_DAY_KEY) MX FROM EDW.USE_TRANSACTION) q", 300)
        mx = int(float(r["MX"])) if r["MX"] else None
        note = " (>= tomorrow: sentinel values present — largest REAL day not derivable cheaply)" if mx and mx >= TKEY_HI else ""
        rec("never_tested", "USE_TRANSACTION freshness", f"max TRANSIT_DAY_KEY = {mx}{note}", "INFO")
    else:
        rec("never_tested", "USE_TRANSACTION freshness",
            "day-key not indexed — verify freshness via the aggregate refresh's own per-month counts instead", "INFO")
except Exception as e:
    rec("never_tested", "USE_TRANSACTION dictionary", str(e).splitlines()[0][:80], "UNKNOWN")

# DEVICE_END_OF_DAY_MSG_COUNT (159M rows): ONE full scan, gated off-hours.
if INCLUDE_OFFHOURS:
    try:
        r = one(f"(SELECT MAX(CASE WHEN TRANSIT_DAY_KEY < {TKEY_HI} THEN TRANSIT_DAY_KEY END) MX,"
                f" SUM(CASE WHEN TRANSIT_DAY_KEY >= {TKEY_HI} THEN 1 ELSE 0 END) FUT,"
                f" SUM(CASE WHEN TRANSIT_DAY_KEY >= {TKEY_LO} AND TRANSIT_DAY_KEY < {TKEY_HI} THEN 1 ELSE 0 END) WIN,"
                f" COUNT(*) N FROM NCS_STAGE.DEVICE_END_OF_DAY_MSG_COUNT) q", 1800)
        mx = str(int(float(r["MX"]))) if r["MX"] else "none"
        lag = (TODAY - _dt.date(int(mx[:4]), int(mx[4:6]), int(mx[6:8]))).days if len(mx) == 8 else None
        rec("never_tested", "MSG_COUNT freshness",
            f"total={n0(r['N']):,}; window rows={n0(r['WIN']):,}; last real day={mx} ({lag}d behind); future rows={n0(r['FUT'])}",
            "PASS" if lag is not None and lag <= 2 else "WARN")
    except Exception as e:
        rec("never_tested", "MSG_COUNT freshness", str(e).splitlines()[0][:80], "UNKNOWN")
else:
    rec("never_tested", "MSG_COUNT freshness", "SKIPPED — set INCLUDE_OFFHOURS=True and rerun this cell off-hours", "SKIPPED")

# COMMAND ----------
# ============================== CELL 6 : GROUP 5 — LOOSE ENDS ==============================
# abp_tap exact freshness (indexed MAX, instant — upgrades the monthly-grain measurement)
try:
    # unbounded MAX = instant index descent; safe because the DQ battery measured 0 future values on this column
    r = one("(SELECT MAX(EDW_UPDATED_DTM) MX FROM EDW.ABP_TAP) q", 300)
    last = str(r["MX"])[:10]
    lag = (TODAY - _dt.date.fromisoformat(last)).days if r["MX"] else None
    rec("loose_ends", "abp_tap freshness", f"max EDW_UPDATED_DTM = {r['MX']} ({lag}d behind)",
        "PASS" if lag is not None and lag <= 2 else "WARN")
except Exception as e:
    rec("loose_ends", "abp_tap freshness", str(e).splitlines()[0][:80], "UNKNOWN")

# device_event NULL check — chunked monthly so it finally completes (was ORA-01013 twice)
try:
    months, d = [], _dt.date.fromisoformat(WINDOW_START)
    end = TODAY + _dt.timedelta(days=1)
    while d < end:
        nxt = (d.replace(day=1) + _dt.timedelta(days=32)).replace(day=1)
        months.append((d.isoformat(), min(nxt, end).isoformat())); d = min(nxt, end)
    nk = nd = tot = 0
    for lo, hi in months:
        r = one(f"(SELECT SUM(CASE WHEN DW_DEVICE_EVENT_ID IS NULL THEN 1 ELSE 0 END) NK,"
                f" SUM(CASE WHEN EVENT_DTM IS NULL THEN 1 ELSE 0 END) ND, COUNT(*) N FROM EDW.DEVICE_EVENT"
                f" WHERE EDW_UPDATED_DTM >= DATE '{lo}' AND EDW_UPDATED_DTM < DATE '{hi}') q", 1800)
        nk += n0(r["NK"]); nd += n0(r["ND"]); tot += n0(r["N"])
        print(f"    {lo[:7]}: rows={n0(r['N']):,} null_id={n0(r['NK'])} null_dtm={n0(r['ND'])}")
    rec("loose_ends", "device_event NULL keys", f"window rows={tot:,}; NULL ids={nk}; NULL event times={nd}",
        "PASS" if nk == 0 and nd == 0 else "FAIL")
except Exception as e:
    rec("loose_ends", "device_event NULL keys", str(e).splitlines()[0][:80], "UNKNOWN")

# COMMAND ----------
# ============================== CELL 7 : GREEN-LIGHT VERDICT + PERSIST ==============================
print("=" * 104)
print("PRE-LOAD ASSURANCE VERDICT")
print("=" * 104)
gate = [t for t in TESTS if t[0] in ("hist_integrity", "fleet_coverage")]  # overlap is informational in v2
fails = [t for t in gate if t[3] == "FAIL"]
unknowns = [t for t in gate if t[3] == "UNKNOWN"]
for g in ("hist_integrity", "overlap_rewrite", "fleet_coverage", "never_tested", "loose_ends"):
    for t in TESTS:
        if t[0] == g: print(f"  [{t[3]:9s}] {t[0]:18s} {t[1]:34s} {t[2][:110]}")
print("-" * 104)
if not fails and not unknowns:
    print("GREEN LIGHT: history integrity + fleet coverage PASS -> cashbox contract UPDATE, then NB12 apply.")
elif fails:
    print(f"NO-GO: {len(fails)} gate FAIL(s) above — a failed hist_integrity table needs full re-baseline, not CDC.")
else:
    print(f"CAUTION: {len(unknowns)} gate check(s) UNKNOWN — resolve or consciously accept before apply.")
print("=" * 104)

buf = ["group,test,detail,verdict"]
for t in TESTS: buf.append(",".join('"' + str(x).replace('"', "'") + '"' for x in t))
csv_text = "\n".join(buf) + "\n"
try:
    dbutils.fs.put(f"{AUDIT}/preload_{TS}/preload_assurance.csv", csv_text, True)
    print(f"saved {AUDIT}/preload_{TS}/preload_assurance.csv")
except Exception as e:
    print("csv save failed:", str(e).splitlines()[0][:80]); print(csv_text)
payload = json.dumps({"probe": "15_PRELOAD_ASSURANCE", "run_ts": TS, "tests": TESTS}, default=str)
spark.createDataFrame([("15_PRELOAD_ASSURANCE", TS, payload)], "probe string, run_ts string, payload string") \
     .write.format("delta").mode("append").saveAsTable("mars_dev.audit.probe_results")
for i in range(0, len(payload), 40000):
    print(f"<<<PART {i//40000 + 1}>>>"); print(payload[i:i+40000])
