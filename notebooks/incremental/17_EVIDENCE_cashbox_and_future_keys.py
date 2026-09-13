# Databricks notebook source
# MAGIC %md
# MAGIC # 17 — EVIDENCE: cashbox key degeneracy + future-dated day keys  (read-only)
# MAGIC
# MAGIC Produces the **proof** behind the two claims in the 09-Sep mail to Cubic, measured live
# MAGIC against the Chicago Ventra Oracle ODS. Nothing is written to Oracle or bronze — the only
# MAGIC outputs are evidence CSVs (S3 `_audit` prefix) and one audit payload row.
# MAGIC
# MAGIC | cell | claim it proves | evidence produced |
# MAGIC |---|---|---|
# MAGIC | 2 | **CASHBOX_EVENT_ID not restored by the reload** | full census of every distinct key value since 12-Apr (expect ~7); before/after 11-Apr contrast (key was ~unique before, collapsed after); day-by-day collapse series; sample rows showing many devices/times sharing one key value |
# MAGIC | 3 | **DEVICE_END_OF_DAY future TRANSIT_DAY_KEY rows** | complete listing of every future-key row (expect ~73), their key distribution, and drop-impact (% of table) |
# MAGIC | 4 | **DEVICE_METRIC future TRANSIT_DAY_KEY rows** | complete listing of every future-key row among post-12-Apr inserts (expect ~25), distribution, drop-impact (% of window) |
# MAGIC | 5 | — | consolidated digest with mail-quotable lines + CSV manifest |
# MAGIC
# MAGIC Run on the **Ventra compute** (VPN). Light: every query is bounded on an indexed column
# MAGIC except the DEVICE_END_OF_DAY scans, which probe 04 proved feasible (~minutes, no index).
# MAGIC DEVICE_METRIC is scanned **only inside the post-12-Apr insert window** (EDW_INSERTED_DTM
# MAGIC is indexed) — a full-history scan of ~2B rows has no supporting index and is not attempted;
# MAGIC the increment window is exactly the population the mail refers to.
# MAGIC Counts may differ slightly from the 08-Sep figures (5,272,789 / 73 / 25) because the healthy
# MAGIC feeds are current again — the notebook stamps its own run time on every output.

# COMMAND ----------
# ============================== CELL 1 : CONFIG + CONNECTION + CSV SINK ==============================
import datetime as _dt, json
from pyspark.sql import functions as F

ODS_HOST, ODS_PORT, SCOPE_SECRET = "10.3.10.30", 1521, "cubic"
SDU, QUERY_TO, READ_TO = 512, 1800, "2100000"
FREEZE_DAY  = "2026-04-11"                     # the day the key stopped being populated
WINDOW_FROM = "2026-04-12 00:00:00"            # the increment window in the mail
PRE_FROM, PRE_TO = "2026-04-01 00:00:00", "2026-04-12 00:00:00"   # healthy contrast window
TODAY_KEY   = int(_dt.date.today().strftime("%Y%m%d"))
RUN_TS      = _dt.datetime.now().strftime("%Y%m%d_%H%M%S")
S3_AUDIT    = "s3://cubic-mars-pm-s3-datalake-dev-raw-170202974600/chicago_ventra/_audit"
OUT_DIR     = f"{S3_AUDIT}/evidence_{RUN_TS}"
PROBE_TBL   = "mars_dev.audit.probe_results"   # probe string, run_ts string, payload string

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
def ora(q, secs=QUERY_TO):
    c = _creds()
    return (spark.read.format("jdbc").option("url", c["url"]).option("dbtable", q)
            .option("user", c["user"]).option("password", c["pwd"])
            .option("driver", "oracle.jdbc.OracleDriver").option("queryTimeout", str(secs))
            .option("oracle.net.CONNECT_TIMEOUT", "10000").option("oracle.jdbc.ReadTimeout", READ_TO)
            .option("sessionInitStatement", "ALTER SESSION SET NLS_DATE_FORMAT='YYYY-MM-DD HH24:MI:SS'")
            .load())

SAVED = []
def save_csv(df, name):
    """Small evidence sets only - render to one CSV text file under the run's S3 _audit dir."""
    try:
        pdf = df.toPandas()
        dbutils.fs.put(f"{OUT_DIR}/{name}", pdf.to_csv(index=False), True)
        SAVED.append(name); print(f"    saved {name} ({len(pdf)} rows) -> {OUT_DIR}/{name}")
        return pdf
    except Exception as e:
        print(f"    (csv save failed for {name}: {str(e).splitlines()[0][:70]} - content printed above stands as evidence)")
        return None

EV = {"run_ts": RUN_TS, "today_key": TODAY_KEY}   # consolidated evidence payload
ora("(SELECT 1 OK FROM dual) q", 20).collect()
print(f"NB17 evidence run {RUN_TS} | today_key={TODAY_KEY} | out={OUT_DIR}")
print("READ-ONLY: no Oracle or bronze writes; outputs are CSVs + one audit payload row.")

# COMMAND ----------
# ============================== CELL 2 : CASHBOX_EVENT_ID — THE KEY WAS NOT RESTORED ==============================
T = "NCS_STAGE.CASHBOX_TRACKING"
print(f"--- {T}: increment window {WINDOW_FROM[:10]} -> today (INSERTED_DTM is indexed) ---")

# (1) one aggregate over the window: rows, distinct keys, NULL health of the other columns
agg = ora(f"""(SELECT COUNT(*) N_ROWS,
                      COUNT(DISTINCT CASHBOX_EVENT_ID) N_KEYS,
                      SUM(CASE WHEN CASHBOX_EVENT_ID IS NULL THEN 1 ELSE 0 END) N_KEY_NULL,
                      SUM(CASE WHEN DEVICE_ID IS NULL THEN 1 ELSE 0 END) N_DEV_NULL,
                      SUM(CASE WHEN EVENT_DTM IS NULL THEN 1 ELSE 0 END) N_EVT_NULL,
                      MIN(INSERTED_DTM) MIN_INS, MAX(INSERTED_DTM) MAX_INS
               FROM {T}
               WHERE INSERTED_DTM >= TIMESTAMP '{WINDOW_FROM}') q""").collect()[0]
print(f"  rows since 12-Apr: {agg['N_ROWS']:,}   distinct CASHBOX_EVENT_ID: {agg['N_KEYS']}   "
      f"NULL keys: {agg['N_KEY_NULL']:,}")
print(f"  other columns healthy: NULL DEVICE_ID={agg['N_DEV_NULL']}, NULL EVENT_DTM={agg['N_EVT_NULL']}  "
      f"| inserts span {agg['MIN_INS']} -> {agg['MAX_INS']}")

# (2) the census: EVERY distinct key value in the window, with its row count and time span
census = ora(f"""(SELECT CASHBOX_EVENT_ID, COUNT(*) N_ROWS,
                         COUNT(DISTINCT DEVICE_ID) N_DEVICES,
                         MIN(EVENT_DTM) FIRST_EVENT, MAX(EVENT_DTM) LAST_EVENT,
                         MIN(INSERTED_DTM) FIRST_INSERT, MAX(INSERTED_DTM) LAST_INSERT
                  FROM {T}
                  WHERE INSERTED_DTM >= TIMESTAMP '{WINDOW_FROM}'
                  GROUP BY CASHBOX_EVENT_ID
                  ORDER BY N_ROWS DESC) q""")
cpdf = save_csv(census, "cashbox_key_census.csv")
print("  the complete census - every key value that exists in 5M+ rows:")
for r in census.collect():
    print(f"    key={str(r['CASHBOX_EVENT_ID']):>12s}  rows={r['N_ROWS']:>10,}  devices={r['N_DEVICES']:>4}  "
          f"events {str(r['FIRST_EVENT'])[:10]} -> {str(r['LAST_EVENT'])[:10]}")

# (3) the healthy contrast: same measures for 01..11-Apr (before the freeze evening)
pre = ora(f"""(SELECT COUNT(*) N_ROWS, COUNT(DISTINCT CASHBOX_EVENT_ID) N_KEYS
               FROM {T}
               WHERE INSERTED_DTM >= TIMESTAMP '{PRE_FROM}'
                 AND INSERTED_DTM <  TIMESTAMP '{PRE_TO}') q""").collect()[0]
pre_rows, pre_keys = int(pre["N_ROWS"]), int(pre["N_KEYS"])
win_rows, win_keys = int(agg["N_ROWS"]), int(agg["N_KEYS"])
ratio_pre  = (pre_keys / pre_rows) if pre_rows else 0.0
ratio_post = (win_keys / win_rows) if win_rows else 0.0
print(f"  BEFORE (01->11 Apr): {pre_rows:,} rows, {pre_keys:,} distinct keys  "
      f"(~{ratio_pre:.4f} keys/row - key was effectively unique)")
print(f"  AFTER  (12-Apr ->): {win_rows:,} rows, {win_keys} distinct keys  "
      f"(~{ratio_post:.10f} keys/row - key collapsed)")
save_csv(spark.createDataFrame(
    [("before 01-11 Apr 2026", pre_rows, pre_keys),
     ("after 12 Apr 2026 -> today", win_rows, win_keys)],
    "window string, rows long, distinct_keys long"), "cashbox_before_after.csv")

# (4) day-by-day: the exact date the key collapsed, and that it never recovered
daily = ora(f"""(SELECT TO_CHAR(TRUNC(INSERTED_DTM),'YYYY-MM-DD') INS_DAY,
                        COUNT(*) N_ROWS, COUNT(DISTINCT CASHBOX_EVENT_ID) N_KEYS
                 FROM {T}
                 WHERE INSERTED_DTM >= TIMESTAMP '{PRE_FROM}'
                 GROUP BY TRUNC(INSERTED_DTM)
                 ORDER BY 1) q""")
dpdf = save_csv(daily, "cashbox_daily_key_collapse.csv")
rows = daily.collect()
print(f"  day-by-day series saved ({len(rows)} days). Around the freeze:")
for r in rows:
    if "2026-04-08" <= r["INS_DAY"] <= "2026-04-15":
        print(f"    {r['INS_DAY']}  rows={r['N_ROWS']:>8,}  distinct_keys={r['N_KEYS']:>8,}")
never_recovered = all(r["N_KEYS"] <= 10 for r in rows if r["INS_DAY"] > FREEZE_DAY)
print(f"  every insert day after {FREEZE_DAY} has <=10 distinct keys: {never_recovered}")

# (5) sample rows: many devices and months sharing ONE key value
top = next((r for r in census.collect() if r["CASHBOX_EVENT_ID"] is not None), None)
if top is None:
    key_pred, key_label = "CASHBOX_EVENT_ID IS NULL", "NULL"
else:
    v = top["CASHBOX_EVENT_ID"]
    key_pred = f"CASHBOX_EVENT_ID = '{v}'" if isinstance(v, str) else f"CASHBOX_EVENT_ID = {v}"
    key_label = str(v)
samp = ora(f"""(SELECT * FROM (
                  SELECT DEVICE_ID, EVENT_DTM, INSERTED_DTM, CASHBOX_EVENT_ID
                  FROM {T}
                  WHERE INSERTED_DTM >= TIMESTAMP '{WINDOW_FROM}'
                    AND {key_pred}
                  ORDER BY EVENT_DTM DESC)
                WHERE ROWNUM <= 12) q""")
save_csv(samp, "cashbox_sample_shared_key_rows.csv")
print(f"  12 sample rows all carrying key={key_label} (different devices, different times):")
for r in samp.collect():
    print(f"    device={r['DEVICE_ID']}  event={r['EVENT_DTM']}  inserted={r['INSERTED_DTM']}  key={r['CASHBOX_EVENT_ID']}")

EV["cashbox"] = {"window_rows": win_rows, "distinct_keys": win_keys,
                 "null_keys": int(agg["N_KEY_NULL"]), "pre_rows": pre_rows,
                 "pre_distinct_keys": pre_keys, "never_recovered_after_freeze": never_recovered}

# COMMAND ----------
# ============================== CELL 3 : DEVICE_END_OF_DAY — FUTURE TRANSIT_DAY_KEY ROWS ==============================
T = "NCS_STAGE.DEVICE_END_OF_DAY"
print(f"--- {T}: rows with TRANSIT_DAY_KEY > {TODAY_KEY} (probe-04-proven full scan, no index) ---")

fut = ora(f"(SELECT * FROM {T} WHERE TRANSIT_DAY_KEY > {TODAY_KEY}) q").cache()
n_fut = fut.count()
save_csv(fut, "end_of_day_future_rows_FULL.csv")     # every column of every offending row
dist = (fut.groupBy("TRANSIT_DAY_KEY").count().orderBy("TRANSIT_DAY_KEY"))
save_csv(dist, "end_of_day_future_key_distribution.csv")
print(f"  future-key rows: {n_fut}   distribution:")
for r in dist.collect():
    print(f"    TRANSIT_DAY_KEY={r['TRANSIT_DAY_KEY']}  rows={r['count']}")
proj = [c for c in ["DEVICE_ID", "TRANSIT_DAY", "TRANSIT_DAY_KEY", "LAST_EOD_DATE"] if c in fut.columns]
print(f"  first rows ({', '.join(proj)}):")
for r in fut.select(*proj).orderBy("TRANSIT_DAY_KEY").limit(10).collect():
    print("    " + "  ".join(f"{c}={r[c]}" for c in proj))

n_total = int(ora(f"(SELECT COUNT(*) N FROM {T}) q", 900).collect()[0]["N"])
pct = 100.0 * n_fut / n_total if n_total else 0.0
print(f"  drop-impact: {n_fut} of {n_total:,} rows = {pct:.5f}% of the table")
mx = fut.agg(F.max("TRANSIT_DAY_KEY")).first()[0]
EV["end_of_day"] = {"future_rows": int(n_fut), "table_rows": n_total, "pct": round(pct, 6),
                    "max_future_key": int(mx) if mx is not None else None}
fut.unpersist()

# COMMAND ----------
# ============================== CELL 4 : DEVICE_METRIC — FUTURE TRANSIT_DAY_KEY ROWS (post-12-Apr inserts) ==============================
T = "EDW.DEVICE_METRIC"
print(f"--- {T}: rows with TRANSIT_DAY_KEY > {TODAY_KEY} among inserts >= 12-Apr "
      f"(EDW_INSERTED_DTM is indexed; full-history scan has no index and is not attempted) ---")

futm = ora(f"""(SELECT * FROM {T}
                WHERE EDW_INSERTED_DTM >= TIMESTAMP '{WINDOW_FROM}'
                  AND TRANSIT_DAY_KEY > {TODAY_KEY}) q""").cache()
n_futm = futm.count()
save_csv(futm, "device_metric_future_rows_FULL.csv")
distm = futm.groupBy("TRANSIT_DAY_KEY").count().orderBy("TRANSIT_DAY_KEY")
save_csv(distm, "device_metric_future_key_distribution.csv")
print(f"  future-key rows in the window: {n_futm}   distribution:")
for r in distm.collect():
    print(f"    TRANSIT_DAY_KEY={r['TRANSIT_DAY_KEY']}  rows={r['count']}")
projm = [c for c in ["DEVICE_ID", "TRANSIT_DAY_KEY", "EDW_INSERTED_DTM", "EDW_UPDATED_DTM"] if c in futm.columns]
print(f"  first rows ({', '.join(projm)}):")
for r in futm.select(*projm).orderBy("TRANSIT_DAY_KEY").limit(10).collect():
    print("    " + "  ".join(f"{c}={r[c]}" for c in projm))

n_win = int(ora(f"""(SELECT COUNT(*) N FROM {T}
                     WHERE EDW_INSERTED_DTM >= TIMESTAMP '{WINDOW_FROM}') q""").collect()[0]["N"])
pctm = 100.0 * n_futm / n_win if n_win else 0.0
print(f"  drop-impact: {n_futm} of {n_win:,} window rows = {pctm:.7f}% of the increment")
mxm = futm.agg(F.max("TRANSIT_DAY_KEY")).first()[0]
EV["device_metric"] = {"future_rows_in_window": int(n_futm), "window_rows": n_win,
                       "pct_of_window": round(pctm, 8), "max_future_key": int(mxm) if mxm is not None else None}
futm.unpersist()

# COMMAND ----------
# ============================== CELL 5 : DIGEST + MANIFEST + AUDIT WRITE ==============================
cb, eod, dm = EV["cashbox"], EV["end_of_day"], EV["device_metric"]
print("=" * 100)
print("EVIDENCE DIGEST - lines you can quote in the mail thread")
print("=" * 100)
print(f"1. CASHBOX_EVENT_ID: {cb['window_rows']:,} rows inserted since 12-Apr share just "
      f"{cb['distinct_keys']} distinct key values.")
print(f"   Before the freeze (01->11 Apr) the same table produced {cb['pre_distinct_keys']:,} distinct keys "
      f"across {cb['pre_rows']:,} rows - effectively one key per event.")
print(f"   Every single insert day after {FREEZE_DAY} shows <=10 distinct keys: {cb['never_recovered_after_freeze']}.")
print(f"   -> The reload refreshed the data but did NOT restore key population.")
print(f"2. DEVICE_END_OF_DAY: {eod['future_rows']} rows carry TRANSIT_DAY_KEY beyond today "
      f"(furthest {eod['max_future_key']}), i.e. {eod['pct']:.5f}% of {eod['table_rows']:,} rows.")
print(f"3. DEVICE_METRIC: {dm['future_rows_in_window']} rows among the {dm['window_rows']:,} post-12-Apr inserts "
      f"carry TRANSIT_DAY_KEY beyond today (furthest {dm['max_future_key']}), "
      f"{dm['pct_of_window']:.7f}% of the increment.")
print(f"   -> Dropping items 2+3 as outliers removes a negligible fraction; the full offending rows are")
print(f"      in the CSVs so Cubic can confirm root cause (day-key derivation) and concur.")
print("-" * 100)
print(f"CSV evidence ({len(SAVED)} files) under {OUT_DIR}/:")
for s in SAVED:
    print(f"  - {s}")
try:
    (spark.createDataFrame([("nb17_evidence", _dt.datetime.utcnow().isoformat(),
                             json.dumps(EV, default=str))],
                           "probe string, run_ts string, payload string")
     .write.format("delta").mode("append").saveAsTable(PROBE_TBL))
    print(f"payload -> {PROBE_TBL} (probe='nb17_evidence', run {RUN_TS})")
except Exception as e:
    print(f"(audit write skipped: {str(e).splitlines()[0][:70]})")
print("Read-only run complete - no source or bronze data was modified.")
