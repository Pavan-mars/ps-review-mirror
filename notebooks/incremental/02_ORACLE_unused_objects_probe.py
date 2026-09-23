# Databricks notebook source
# MAGIC %md
# MAGIC # 02 — ORACLE UNUSED-OBJECTS PROBE  (read-only, Chicago ODS)
# MAGIC
# MAGIC **Why.** The 22-Sep Oracle catalog export (`Chicago_tables.csv` + indexes + PKs, 472 tables /
# MAGIC 7,993 columns) surfaced objects at **device-day grain that no silver table reads**. The most
# MAGIC important is a family that looks like a predictive-maintenance feature matrix Cubic already
# MAGIC built:
# MAGIC
# MAGIC ```
# MAGIC CTA.JV_GATE100_REPORT        99 cols   PULL_FOR_REPAIR_SCORE + ~40 E-code threshold flags
# MAGIC CTA.RV_BMV100_REPORT_REV_K  103 cols   same shape
# MAGIC CTA.RV_B100_REPORT_SN        91 cols   same shape
# MAGIC CTA.RV_B100_SN_TABLE         91 cols   same shape (the only one with column stats)
# MAGIC CTA.RV_READ_ERROR_MONITOR    22 cols   READ_ERROR_PERCENT, TIME_OUT_PERCENT, ACTIVE_HRS
# MAGIC ```
# MAGIC
# MAGIC Two of those columns matter beyond their own value. `E104_*` decomposes one event code by root
# MAGIC cause (BADREADER / MISSHEARTBEAT / BADBATTERY / MEMORYUSAGE / CPU / 3GREBOOT / SAM) — the same
# MAGIC axis as component attribution, which is SHAP #1 on both GATE and TVM. And `E2203_*` / `E2207_*`
# MAGIC are thresholded features on the 22xx family that our own Device Event Matrix leaves
# MAGIC unclassified at 10.1% of episodes.
# MAGIC
# MAGIC **What this cannot tell us.** The catalog export EXCLUDES `DEVICE_EVENT`, `DEVICE_METRIC`,
# MAGIC `DEVICE_DIMENSION`, `KPI_DETAIL_EVENTS_BY_DAY`, `USE_TRANSACTION`, `READ_TRANSACTION` and
# MAGIC `EVENT_TYPE_DIMENSION` — our core sources. 472 of ~1,009 ODS tables. So a clean result here is
# MAGIC **not** evidence that nothing was missed elsewhere.
# MAGIC
# MAGIC **The questions, in order of what they decide:**
# MAGIC
# MAGIC 1. Are these **views or tables**, and what do they read FROM? (`ALL_DEPENDENCIES`, free)
# MAGIC 2. Are they **populated at all**? Every catalog row-count was 0, but `RV_`/`JV_` prefixes and a
# MAGIC    null `last_analyzed` mean "no index statistics", NOT "no rows". This is the decisive check.
# MAGIC 3. What **date range** and **device coverage**, and do the device IDs look like ours?
# MAGIC 4. Does `PULL_FOR_REPAIR_SCORE` **vary**? If it is a real score with spread, it is a BENCHMARK:
# MAGIC    Cubic's own maintenance-priority heuristic, measurable against the same label on the same
# MAGIC    devices. That number is worth more than another feature run.
# MAGIC 5. Do the `E*` threshold columns vary, or are they constant/empty?
# MAGIC
# MAGIC **Nothing is written.** No DDL, no S3, no bronze. Read-only, safe to Run All.
# MAGIC
# MAGIC Prereqs: cluster with `oracle.jdbc.OracleDriver`; secret scope `cubic`
# MAGIC (`ods_user`, `ods_pwd`, `ods_service`); VPN up to `10.3.10.30:1521`.
# MAGIC Paste the **PASTE-BACK BLOCK** from the last cell into the Cowork session.

# COMMAND ----------
# ============================== CELL 1 : CONFIG ==============================
import json, re
import datetime as _dt

ODS_HOST, ODS_PORT, SCOPE = "10.3.10.30", 1521, "cubic"
SDU          = 512           # MTU black-hole band-aid (28-Aug finding). Set None once MSS clamp is live.
CONNECT_TO   = "10000"       # ms  — oracle.net.CONNECT_TIMEOUT
READ_TO      = "300000"      # ms  — oracle.jdbc.ReadTimeout  (BOTH are required)
QUERY_TO     = 240           # s   — queryTimeout. Views over big facts can be slow; this caps them.

# Row cap for every data-touching probe. A view that aggregates a billion-row fact will compute the
# whole aggregate before ROWNUM applies, so the cap is a courtesy, not a guarantee — queryTimeout is
# what actually protects us. Keep both.
ROW_CAP      = 100001        # 100001 reads as "at least 100k"
SAMPLE_CAP   = 50000         # for per-column variance checks

# (owner, object, why it is here)
TARGETS = [
    ("CTA", "JV_GATE100_REPORT",          "PULL_FOR_REPAIR_SCORE + ~40 E-code threshold flags"),
    ("CTA", "RV_BMV100_REPORT_REV_K",     "same shape, 103 cols"),
    ("CTA", "RV_B100_REPORT_SN",          "same shape, 91 cols"),
    ("CTA", "RV_B100_SN_TABLE",           "same shape; only one with column stats (analysed 2024-09-28)"),
    ("CTA", "RV_READ_ERROR_MONITOR",      "device-day read-error %, timeout %, ACTIVE_HRS"),
    ("EDW", "CASHBOX_TRACKING_V",         "TVM cash path, 104 cols (bronze has ncs_stage_cashbox_tracking)"),
    ("EDW", "CASHBOX_MANUAL_COUNTS_V",    "TVM cash path, 69 cols"),
    ("CTA", "RAIL_DEVICE_IP_ADDRESS_XREF","device -> IP; a real peer/topology grouping"),
    ("CTA", "VC030_MASTER_EQUIPMENT_MAP", "equipment map, 795 rows"),
    ("EDW", "AJ_METRIC_FACT",             "unused metric fact at DEVICE_KEY + TRANSIT_DAY_KEY"),
    ("CTA", "VC030_RAIL",                 "DEVICE_ID + TRANSIT_DAY_KEY"),
]
NAMES = "(" + ",".join(f"'{t}'" for _, t, _ in TARGETS) + ")"

RESULTS = {}     # everything worth pasting back
def _put(obj, key, val):
    RESULTS.setdefault(obj, {})[key] = val

# COMMAND ----------
# ============================== CELL 2 : CONNECTION ==========================
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
    """q must be a parenthesised subquery with an alias -- it is passed as dbtable."""
    c = _creds()
    return (spark.read.format("jdbc").option("url", c["url"]).option("dbtable", q)
            .option("user", c["user"]).option("password", c["pwd"])
            .option("driver", "oracle.jdbc.OracleDriver")
            .option("queryTimeout", str(secs))
            .option("oracle.net.CONNECT_TIMEOUT", CONNECT_TO)
            .option("oracle.jdbc.ReadTimeout", READ_TO)
            .option("fetchsize", "5000")
            .option("sessionInitStatement",
                    "ALTER SESSION SET NLS_DATE_FORMAT='YYYY-MM-DD HH24:MI:SS'")
            .load())

def probe(label, sql, secs=QUERY_TO):
    """Run one query. Never raises -- a failure here must not stop the rest of the probe."""
    try:
        return ora(sql, secs).collect()
    except Exception as exc:
        msg = str(exc).splitlines()[0][:150]
        print(f"    !! {label}: {msg}")
        return None

# fail fast if the tunnel is down, rather than after ten timeouts
_t0 = _dt.datetime.now()
_ping = probe("connectivity", "(SELECT 1 AS ok FROM dual) t", 30)
print(f"[probe] Oracle reachable: {bool(_ping)}   ({(_dt.datetime.now()-_t0).seconds}s)")
if not _ping:
    raise RuntimeError("No Oracle connection -- check the VPN to 10.3.10.30:1521 before continuing.")

# COMMAND ----------
# ============================== CELL 3 : WHAT ARE THEY =======================
# Dictionary only. Touches no data, costs nothing, and settles view-vs-table.
print("=" * 100)
print("1. OBJECT TYPE, STATUS, AGE")
print("=" * 100)
rows = probe("all_objects", f"""(
  SELECT owner, object_name, object_type, status,
         TO_CHAR(created,'YYYY-MM-DD')      AS created,
         TO_CHAR(last_ddl_time,'YYYY-MM-DD') AS last_ddl
  FROM all_objects
  WHERE owner IN ('CTA','EDW') AND object_name IN {NAMES}
) t""")
seen = {}
if rows:
    print(f"  {'object':44s} {'type':10s} {'status':8s} {'created':11s} {'last DDL':11s}")
    for r in sorted(rows, key=lambda x: (x['OWNER'], x['OBJECT_NAME'])):
        k = f"{r['OWNER']}.{r['OBJECT_NAME']}"
        seen[k] = r['OBJECT_TYPE']
        print(f"  {k:44s} {r['OBJECT_TYPE']:10s} {r['STATUS']:8s} {str(r['CREATED']):11s} {str(r['LAST_DDL']):11s}")
        _put(k, "type", r['OBJECT_TYPE']); _put(k, "status", r['STATUS'])
        _put(k, "created", str(r['CREATED'])); _put(k, "last_ddl", str(r['LAST_DDL']))
missing = [f"{o}.{t}" for o, t, _ in TARGETS if f"{o}.{t}" not in seen]
if missing:
    print(f"\n  ABSENT from all_objects (or not visible to this user): {missing}")

# What do the views read FROM? all_dependencies gives this without touching LONG columns.
print("\n" + "=" * 100)
print("2. WHAT THE VIEWS ARE BUILT FROM  (all_dependencies)")
print("=" * 100)
deps = probe("all_dependencies", f"""(
  SELECT owner, name, referenced_owner, referenced_name, referenced_type
  FROM all_dependencies
  WHERE owner IN ('CTA','EDW') AND name IN {NAMES}
    AND referenced_type IN ('TABLE','VIEW','MATERIALIZED VIEW')
) t""")
if deps:
    by = {}
    for r in deps:
        by.setdefault(f"{r['OWNER']}.{r['NAME']}", []).append(
            f"{r['REFERENCED_OWNER']}.{r['REFERENCED_NAME']}")
    for k in sorted(by):
        src = sorted(set(by[k]))
        print(f"  {k}")
        for s in src:
            print(f"      <- {s}")
        _put(k, "reads_from", src)
else:
    print("  (no dependency rows -- they are base tables, or the view text is not visible)")

# COMMAND ----------
# ============================== CELL 4 : ARE THEY POPULATED ==================
# THE DECISIVE CHECK. Every catalog row-count was 0, but that is "no index statistics",
# not "no rows". Capped by ROWNUM and by queryTimeout.
print("=" * 100)
print(f"3. POPULATION  (capped at {ROW_CAP-1:,}; '{ROW_CAP:,}' means at least that many)")
print("=" * 100)
print(f"  {'object':44s} {'rows':>12s}  note")
for owner, tbl, why in TARGETS:
    k = f"{owner}.{tbl}"
    if k not in seen:
        print(f"  {k:44s} {'--':>12s}  not visible"); continue
    r = probe(k, f"(SELECT COUNT(*) AS n FROM (SELECT 1 FROM {owner}.{tbl} WHERE ROWNUM <= {ROW_CAP})) t")
    if r is None:
        _put(k, "rows", "ERROR"); continue
    n = int(r[0]['N'])
    note = "AT LEAST this many" if n >= ROW_CAP else ("EMPTY" if n == 0 else "exact")
    print(f"  {k:44s} {n:>12,}  {note}")
    _put(k, "rows", n); _put(k, "rows_note", note)

populated = [ (o,t,w) for o,t,w in TARGETS
              if isinstance(RESULTS.get(f"{o}.{t}",{}).get("rows"), int)
              and RESULTS[f"{o}.{t}"]["rows"] > 0 ]
print(f"\n  POPULATED: {len(populated)} of {len(TARGETS)}")
if not populated:
    print("  -> Nothing below will run. The catalog leads are empty objects and this line of")
    print("     enquiry is closed. That is a real answer, not a failure.")

# COMMAND ----------
# ============================== CELL 5 : SHAPE ===============================
# Date range, device coverage, and sample device IDs -- do they look like ours?
DAYCOL = {"JV_GATE100_REPORT":"TRANS_DAY", "RV_BMV100_REPORT_REV_K":"TRANS_DAY",
          "RV_B100_REPORT_SN":"TRANS_DAY", "RV_B100_SN_TABLE":"TRANS_DAY",
          "RV_READ_ERROR_MONITOR":"TRANS_DAY", "CASHBOX_TRACKING_V":"TRANSIT_DAY_KEY",
          "CASHBOX_MANUAL_COUNTS_V":"TRANSIT_DAY_KEY", "AJ_METRIC_FACT":"TRANSIT_DAY_KEY",
          "VC030_RAIL":"TRANSIT_DAY_KEY"}
DEVCOL = {"RV_READ_ERROR_MONITOR":"R_DEVICE_ID", "AJ_METRIC_FACT":"DEVICE_KEY"}

print("=" * 100)
print("4. DATE RANGE, DEVICE COVERAGE, AND WHAT THE DEVICE IDs LOOK LIKE")
print("=" * 100)
for owner, tbl, why in populated:
    k = f"{owner}.{tbl}"
    dev = DEVCOL.get(tbl, "DEVICE_ID")
    day = DAYCOL.get(tbl)
    print(f"\n  {k}   ({why})")
    if day:
        r = probe(k+" range", f"""(
          SELECT MIN({day}) AS lo, MAX({day}) AS hi, COUNT(DISTINCT {day}) AS days
          FROM {owner}.{tbl}) t""")
        if r:
            print(f"      {day}: {r[0]['LO']} .. {r[0]['HI']}   ({r[0]['DAYS']:,} distinct days)")
            _put(k, "day_col", day); _put(k, "day_min", str(r[0]['LO']))
            _put(k, "day_max", str(r[0]['HI'])); _put(k, "day_count", int(r[0]['DAYS']))
    r = probe(k+" devices", f"""(
      SELECT COUNT(DISTINCT {dev}) AS n FROM {owner}.{tbl}) t""")
    if r:
        print(f"      distinct {dev}: {int(r[0]['N']):,}")
        _put(k, "device_col", dev); _put(k, "devices", int(r[0]['N']))
    r = probe(k+" sample", f"""(
      SELECT {dev} AS d FROM {owner}.{tbl} WHERE {dev} IS NOT NULL AND ROWNUM <= 5) t""")
    if r:
        sample = [str(x['D']) for x in r]
        print(f"      sample ids: {sample}")
        _put(k, "sample_ids", sample)
        print("      ^ do these look like our DEVICE_IDs (e.g. TVM01703) or a different namespace?")

# COMMAND ----------
# ============================== CELL 6 : THE BENCHMARK =======================
# PULL_FOR_REPAIR_SCORE is the reason this probe exists. If it is a real score with spread,
# it is Cubic's OWN maintenance-priority heuristic and can be scored against the same label
# on the same devices. That comparison is worth more than another feature run.
print("=" * 100)
print("5. PULL_FOR_REPAIR_SCORE — is it a real score, and does it vary?")
print("=" * 100)
for owner, tbl, _ in populated:
    if tbl not in ("JV_GATE100_REPORT","RV_BMV100_REPORT_REV_K",
                   "RV_B100_REPORT_SN","RV_B100_SN_TABLE"):
        continue
    k = f"{owner}.{tbl}"
    r = probe(k+" score", f"""(
      SELECT COUNT(*) AS n, COUNT(PULL_FOR_REPAIR_SCORE) AS non_null,
             COUNT(DISTINCT PULL_FOR_REPAIR_SCORE) AS distinct_vals,
             MIN(PULL_FOR_REPAIR_SCORE) AS lo, MAX(PULL_FOR_REPAIR_SCORE) AS hi,
             AVG(PULL_FOR_REPAIR_SCORE) AS mean
      FROM {owner}.{tbl}) t""")
    if not r:
        continue
    x = r[0]; n, nn = int(x['N']), int(x['NON_NULL'])
    print(f"\n  {k}")
    print(f"      rows {n:,}   non-null {nn:,} ({nn/n if n else 0:.1%})   "
          f"distinct {int(x['DISTINCT_VALS']):,}")
    print(f"      range {x['LO']} .. {x['HI']}   mean {x['MEAN']}")
    verdict = ("A REAL SCORE -- benchmark it" if int(x['DISTINCT_VALS']) > 5 and nn > 0
               else "constant or empty -- not a benchmark")
    print(f"      -> {verdict}")
    _put(k, "score", {"rows": n, "non_null": nn, "distinct": int(x['DISTINCT_VALS']),
                      "min": str(x['LO']), "max": str(x['HI']), "mean": str(x['MEAN']),
                      "verdict": verdict})

# COMMAND ----------
# ============================== CELL 7 : DO THE E-COLUMNS VARY ===============
# A column that never changes carries no information however promising its name.
# Checked on a bounded sample -- 40+ COUNT(DISTINCT)s over a full view would be expensive.
print("=" * 100)
print(f"6. DO THE E-CODE / SIGNAL COLUMNS VARY?  (sample of {SAMPLE_CAP:,} rows)")
print("=" * 100)
SIGPAT = re.compile(r"^E\d+|READ_ERROR|TIME_OUT|ACTIVE_HRS|TAP|DENIED|SUCCESS|CSC_ERR|LU_COUNT|TOTAL_TXNS", re.I)
for owner, tbl, _ in populated:
    k = f"{owner}.{tbl}"
    cols = probe(k+" cols", f"""(
      SELECT column_name FROM all_tab_columns
      WHERE owner='{owner}' AND table_name='{tbl}'
        AND data_type IN ('NUMBER','FLOAT','INTEGER')
      ORDER BY column_id) t""")
    if not cols:
        continue
    names = [c['COLUMN_NAME'] for c in cols if SIGPAT.search(c['COLUMN_NAME'])]
    if not names:
        continue
    print(f"\n  {k}  — {len(names)} numeric signal columns")
    varying, constant, empty = [], [], []
    for i in range(0, len(names), 20):                       # batches of 20 keep the SQL sane
        batch = names[i:i+20]
        sel = ", ".join(f"COUNT(DISTINCT {c}) AS D{j}, COUNT({c}) AS N{j}"
                        for j, c in enumerate(batch))
        r = probe(f"{k} batch{i}", f"""(
          SELECT {sel} FROM (SELECT * FROM {owner}.{tbl} WHERE ROWNUM <= {SAMPLE_CAP})) t""")
        if not r:
            continue
        for j, c in enumerate(batch):
            d, nn = int(r[0][f'D{j}']), int(r[0][f'N{j}'])
            (empty if nn == 0 else constant if d <= 1 else varying).append(c)
    print(f"      varying  {len(varying):3d}   {varying[:8]}")
    print(f"      constant {len(constant):3d}   {constant[:8]}")
    print(f"      all-null {len(empty):3d}   {empty[:8]}")
    _put(k, "signal_cols", {"varying": varying, "constant": constant, "all_null": empty})

# COMMAND ----------
# ============================== CELL 8 : PASTE-BACK BLOCK ====================
print("=" * 100)
print("PASTE-BACK BLOCK — copy everything between the markers into the Cowork session")
print("=" * 100)
print("<<<PROBE02>>>")
print(json.dumps(RESULTS, indent=1, default=str, sort_keys=True))
print("<<<END>>>")
print()
print("HOW TO READ IT:")
print("  rows == 0 everywhere        -> the catalog leads are empty; this enquiry closes cleanly.")
print("  PULL_FOR_REPAIR_SCORE varies-> Cubic has its OWN maintenance score. Benchmark it against")
print("                                 our label on the same devices. That number reframes the")
print("                                 contract conversation more than any feature run can.")
print("  E-columns mostly varying    -> a ready-made feature matrix, including the E104 root-cause")
print("                                 split (the same axis as component attribution, SHAP #1 on")
print("                                 GATE and TVM) and E2203/E2207 on the 22xx family our own")
print("                                 Device Event Matrix leaves unclassified.")
print("  RV_READ_ERROR_MONITOR full  -> fills the METRIC_401 coverage gap (~41%) and adds ACTIVE_HRS,")
print("                                 an exposure measure PS1 has no equivalent of.")
print()
print("REMEMBER: the catalog export excluded DEVICE_EVENT, DEVICE_METRIC, DEVICE_DIMENSION and")
print("five other core tables, so a clean result here does NOT mean nothing was missed elsewhere.")
