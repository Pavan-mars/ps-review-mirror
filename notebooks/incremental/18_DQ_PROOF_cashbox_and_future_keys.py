# Databricks notebook source
# MAGIC %md
# MAGIC # 18 — DQ PROOF PACK: cashbox key + future business dates  (read-only)
# MAGIC
# MAGIC Runs the **identical query text** we handed to the Cubic Chicago team in
# MAGIC `CUBIC_MARS_Chicago_DQ_Proof_Queries_v1.sql`, so our numbers and their numbers come from
# MAGIC the same SQL. Every cell prints the query first, then its result — the notebook output
# MAGIC *is* the evidence.
# MAGIC
# MAGIC | section | proves |
# MAGIC |---|---|
# MAGIC | T0 | we are pointing at the same three tables (owner + optimizer stats) |
# MAGIC | A1–A5 | **Issue 1**: ~7 distinct `CASHBOX_EVENT_ID` values across the ~5.27M rows loaded since 12-Apr — headline, full key census, before/after contrast, day-by-day collapse, sample rows |
# MAGIC | B1–B4 | **Issue 2**: future-dated `TRANSIT_DAY_KEY` rows in `DEVICE_END_OF_DAY` and `DEVICE_METRIC` — counts, % of population, and every offending row |
# MAGIC | C1 | the six-row scorecard we asked Cubic to return |
# MAGIC
# MAGIC Read-only: only SELECTs against Oracle. Outputs are CSVs on the S3 `_audit` prefix plus one
# MAGIC audit payload row. Run on the **Ventra compute** (VPN). B1/B2 scan `DEVICE_END_OF_DAY`
# MAGIC (no index on `TRANSIT_DAY_KEY`) — minutes; everything else is seconds.

# COMMAND ----------
# ============================== CELL 1 : CONFIG + THE SHARED SQL ==============================
import datetime as _dt, json
from pyspark.sql import functions as F

ODS_HOST, ODS_PORT, SCOPE_SECRET = "10.3.10.30", 1521, "cubic"
SDU, QUERY_TO, READ_TO = 512, 1800, "2100000"
RUN_TS    = _dt.datetime.now().strftime("%Y%m%d_%H%M%S")
S3_AUDIT  = "s3://cubic-mars-pm-s3-datalake-dev-raw-170202974600/chicago_ventra/_audit"
OUT_DIR   = f"{S3_AUDIT}/dq_proof_{RUN_TS}"
PROBE_TBL = "mars_dev.audit.probe_results"      # probe string, run_ts string, payload string

# ---- verbatim from CUBIC_MARS_Chicago_DQ_Proof_Queries_v1.sql (bodies only, no trailing ';') ----
SQL = {
    "T0_OBJECTS": "SELECT owner, table_name, num_rows AS optimizer_num_rows, last_analyzed\n  FROM all_tables\n WHERE table_name IN ('CASHBOX_TRACKING','DEVICE_END_OF_DAY','DEVICE_METRIC')\n ORDER BY table_name, owner",
    "A1_HEADLINE": "SELECT COUNT(*)                                                   AS n_rows,\n       COUNT(DISTINCT cashbox_event_id)                          AS n_distinct_keys,\n       ROUND(COUNT(*) / NULLIF(COUNT(DISTINCT cashbox_event_id),0)) AS rows_per_key,\n       SUM(CASE WHEN cashbox_event_id IS NULL THEN 1 ELSE 0 END) AS n_null_keys,\n       COUNT(DISTINCT device_id)                                 AS n_distinct_devices,\n       SUM(CASE WHEN device_id IS NULL THEN 1 ELSE 0 END)        AS n_null_device_id,\n       SUM(CASE WHEN event_dtm  IS NULL THEN 1 ELSE 0 END)       AS n_null_event_dtm,\n       MIN(inserted_dtm)                                         AS first_insert,\n       MAX(inserted_dtm)                                         AS last_insert,\n       ROUND(MONTHS_BETWEEN(MAX(inserted_dtm), MIN(inserted_dtm)), 1) AS months_spanned\n  FROM ncs_stage.cashbox_tracking\n WHERE inserted_dtm >= TIMESTAMP '2026-04-12 00:00:00'",
    "A2_CENSUS": "SELECT cashbox_event_id,\n       COUNT(*)                  AS n_rows,\n       COUNT(DISTINCT device_id) AS n_devices,\n       MIN(event_dtm)            AS first_event,\n       MAX(event_dtm)            AS last_event,\n       MIN(inserted_dtm)         AS first_insert,\n       MAX(inserted_dtm)         AS last_insert\n  FROM ncs_stage.cashbox_tracking\n WHERE inserted_dtm >= TIMESTAMP '2026-04-12 00:00:00'\n GROUP BY cashbox_event_id\n ORDER BY n_rows DESC",
    "A3_BEFORE_AFTER": "SELECT 'BEFORE  01-Apr to 11-Apr-2026' AS period,\n       COUNT(*)                                                     AS n_rows,\n       COUNT(DISTINCT cashbox_event_id)                             AS n_distinct_keys,\n       ROUND(COUNT(*) / NULLIF(COUNT(DISTINCT cashbox_event_id),0), 2) AS rows_per_key\n  FROM ncs_stage.cashbox_tracking\n WHERE inserted_dtm >= TIMESTAMP '2026-04-01 00:00:00'\n   AND inserted_dtm <  TIMESTAMP '2026-04-12 00:00:00'\nUNION ALL\nSELECT 'AFTER   12-Apr-2026 to today' AS period,\n       COUNT(*)                                                     AS n_rows,\n       COUNT(DISTINCT cashbox_event_id)                             AS n_distinct_keys,\n       ROUND(COUNT(*) / NULLIF(COUNT(DISTINCT cashbox_event_id),0), 2) AS rows_per_key\n  FROM ncs_stage.cashbox_tracking\n WHERE inserted_dtm >= TIMESTAMP '2026-04-12 00:00:00'",
    "A4_DAILY": "SELECT TRUNC(inserted_dtm)              AS insert_day,\n       COUNT(*)                          AS n_rows,\n       COUNT(DISTINCT cashbox_event_id)  AS n_distinct_keys\n  FROM ncs_stage.cashbox_tracking\n WHERE inserted_dtm >= TIMESTAMP '2026-04-01 00:00:00'\n GROUP BY TRUNC(inserted_dtm)\n ORDER BY 1",
    "A5_SAMPLE": "SELECT * FROM (\n  SELECT device_id, event_dtm, inserted_dtm, cashbox_event_id\n    FROM ncs_stage.cashbox_tracking\n   WHERE inserted_dtm >= TIMESTAMP '2026-04-12 00:00:00'\n     AND cashbox_event_id = (SELECT cashbox_event_id FROM (\n                               SELECT cashbox_event_id, COUNT(*) AS n\n                                 FROM ncs_stage.cashbox_tracking\n                                WHERE inserted_dtm >= TIMESTAMP '2026-04-12 00:00:00'\n                                GROUP BY cashbox_event_id\n                                ORDER BY n DESC)\n                              WHERE ROWNUM = 1)\n   ORDER BY event_dtm DESC)\n WHERE ROWNUM <= 20",
    "B1_EOD_SUMMARY": "SELECT COUNT(*)                                                         AS n_total_rows,\n       SUM(CASE WHEN transit_day_key > TO_NUMBER(TO_CHAR(TRUNC(SYSDATE),'YYYYMMDD'))\n                THEN 1 ELSE 0 END)                                       AS n_future_rows,\n       ROUND(100 * SUM(CASE WHEN transit_day_key > TO_NUMBER(TO_CHAR(TRUNC(SYSDATE),'YYYYMMDD'))\n                THEN 1 ELSE 0 END) / NULLIF(COUNT(*),0), 6)              AS pct_of_table,\n       MAX(CASE WHEN transit_day_key > TO_NUMBER(TO_CHAR(TRUNC(SYSDATE),'YYYYMMDD'))\n                THEN transit_day_key END)                                AS max_future_key,\n       MIN(transit_day_key)                                              AS min_key_in_table,\n       TO_NUMBER(TO_CHAR(TRUNC(SYSDATE),'YYYYMMDD'))                     AS todays_key\n  FROM ncs_stage.device_end_of_day",
    "B2_EOD_DETAIL": "SELECT device_id,\n       transit_day,\n       transit_day_key,\n       SUBSTR(TO_CHAR(transit_day_key,'FM99999999'), 1, 4) AS key_year,\n       SUBSTR(TO_CHAR(transit_day_key,'FM99999999'), 5, 2) AS key_month,\n       SUBSTR(TO_CHAR(transit_day_key,'FM99999999'), 7, 2) AS key_day,\n       last_eod_date\n  FROM ncs_stage.device_end_of_day\n WHERE transit_day_key > TO_NUMBER(TO_CHAR(TRUNC(SYSDATE),'YYYYMMDD'))\n ORDER BY transit_day_key, device_id",
    "B3_DM_SUMMARY": "SELECT COUNT(*)                                                         AS n_window_rows,\n       SUM(CASE WHEN transit_day_key > TO_NUMBER(TO_CHAR(TRUNC(SYSDATE),'YYYYMMDD'))\n                THEN 1 ELSE 0 END)                                       AS n_future_rows,\n       ROUND(100 * SUM(CASE WHEN transit_day_key > TO_NUMBER(TO_CHAR(TRUNC(SYSDATE),'YYYYMMDD'))\n                THEN 1 ELSE 0 END) / NULLIF(COUNT(*),0), 8)              AS pct_of_window,\n       MAX(CASE WHEN transit_day_key > TO_NUMBER(TO_CHAR(TRUNC(SYSDATE),'YYYYMMDD'))\n                THEN transit_day_key END)                                AS max_future_key,\n       TO_NUMBER(TO_CHAR(TRUNC(SYSDATE),'YYYYMMDD'))                     AS todays_key\n  FROM edw.device_metric\n WHERE edw_inserted_dtm >= TIMESTAMP '2026-04-12 00:00:00'",
    "B4_DM_DETAIL": "SELECT device_id,\n       transit_day_key,\n       SUBSTR(TO_CHAR(transit_day_key,'FM99999999'), 1, 4) AS key_year,\n       SUBSTR(TO_CHAR(transit_day_key,'FM99999999'), 5, 2) AS key_month,\n       SUBSTR(TO_CHAR(transit_day_key,'FM99999999'), 7, 2) AS key_day,\n       edw_inserted_dtm,\n       edw_updated_dtm\n  FROM edw.device_metric\n WHERE edw_inserted_dtm >= TIMESTAMP '2026-04-12 00:00:00'\n   AND transit_day_key  > TO_NUMBER(TO_CHAR(TRUNC(SYSDATE),'YYYYMMDD'))\n ORDER BY transit_day_key, device_id",
    "C1_SCORECARD": "SELECT 'A. CASHBOX_TRACKING rows loaded since 12-Apr-2026'            AS check_item,\n       TO_CHAR(COUNT(*))                                               AS measured_value,\n       'volume is healthy - this row is context, not a defect'         AS expected_if_healthy\n  FROM ncs_stage.cashbox_tracking WHERE inserted_dtm >= TIMESTAMP '2026-04-12 00:00:00'\nUNION ALL\nSELECT 'B. ...distinct CASHBOX_EVENT_ID values in those rows',\n       TO_CHAR(COUNT(DISTINCT cashbox_event_id)),\n       'one per event (i.e. equal to the row count above)'\n  FROM ncs_stage.cashbox_tracking WHERE inserted_dtm >= TIMESTAMP '2026-04-12 00:00:00'\nUNION ALL\nSELECT 'C. ...average events sharing ONE identifier',\n       TO_CHAR(ROUND(COUNT(*) / NULLIF(COUNT(DISTINCT cashbox_event_id),0))),\n       '1'\n  FROM ncs_stage.cashbox_tracking WHERE inserted_dtm >= TIMESTAMP '2026-04-12 00:00:00'\nUNION ALL\nSELECT 'D. ...same measure for 01-Apr to 11-Apr-2026 (baseline)',\n       TO_CHAR(ROUND(COUNT(*) / NULLIF(COUNT(DISTINCT cashbox_event_id),0), 2)),\n       '1 - and this baseline period does return 1'\n  FROM ncs_stage.cashbox_tracking\n WHERE inserted_dtm >= TIMESTAMP '2026-04-01 00:00:00' AND inserted_dtm < TIMESTAMP '2026-04-12 00:00:00'\nUNION ALL\nSELECT 'E. DEVICE_END_OF_DAY rows with TRANSIT_DAY_KEY after today',\n       TO_CHAR(COUNT(*)),\n       '0'\n  FROM ncs_stage.device_end_of_day\n WHERE transit_day_key > TO_NUMBER(TO_CHAR(TRUNC(SYSDATE),'YYYYMMDD'))\nUNION ALL\nSELECT 'F. DEVICE_METRIC rows (loaded since 12-Apr) with TRANSIT_DAY_KEY after today',\n       TO_CHAR(COUNT(*)),\n       '0'\n  FROM edw.device_metric\n WHERE edw_inserted_dtm >= TIMESTAMP '2026-04-12 00:00:00'\n   AND transit_day_key  > TO_NUMBER(TO_CHAR(TRUNC(SYSDATE),'YYYYMMDD'))"
}

TITLES = {
    "T0_OBJECTS": "Object resolution - confirm we are looking at the same three tables",
    "A1_HEADLINE": "ISSUE 1 headline - rows vs distinct CASHBOX_EVENT_ID since 12-Apr-2026",
    "A2_CENSUS": "ISSUE 1 census - EVERY distinct key value that exists in the window",
    "A3_BEFORE_AFTER": "ISSUE 1 contrast - the same measure before and after 11-Apr-2026",
    "A4_DAILY": "ISSUE 1 daily series - the exact day the identifier stopped being assigned",
    "A5_SAMPLE": "ISSUE 1 sample rows - unrelated events carrying the identical identifier",
    "B1_EOD_SUMMARY": "ISSUE 2a summary - DEVICE_END_OF_DAY rows dated in the future",
    "B2_EOD_DETAIL": "ISSUE 2a detail - every future-dated DEVICE_END_OF_DAY row",
    "B3_DM_SUMMARY": "ISSUE 2b summary - DEVICE_METRIC rows dated in the future (rows loaded since 12-Apr-2026)",
    "B4_DM_DETAIL": "ISSUE 2b detail - every future-dated DEVICE_METRIC row in the same window",
    "C1_SCORECARD": "SCORECARD - all findings in one result set (run this one if you run nothing else)"
}

NOTES = {
    "T0_OBJECTS": "Resolves the owning schema of each table in YOUR environment. Run this first: if DEVICE_METRIC resolves to a different owner than EDW, change the owner in tests B3/B4 to match.",
    "A1_HEADLINE": "One row. N_ROWS is the volume loaded since 12-Apr; N_DISTINCT_KEYS is how many distinct event identifiers those rows carry; ROWS_PER_KEY is the average number of events sharing one identifier. In a healthy period ROWS_PER_KEY is 1. MONTHS_SPANNED shows the period covered.",
    "A2_CENSUS": "Expected to return only a handful of rows. Each row shows one identifier value and how many events, devices and days share it. A single identifier covering millions of rows across hundreds of devices cannot be a valid per-event key.",
    "A3_BEFORE_AFTER": "Two rows from the SAME table and the SAME column. The 'BEFORE' row is the healthy baseline (effectively one key per row). The 'AFTER' row is the current behaviour. This is the core of the finding: the column's behaviour changed, the table did not.",
    "A4_DAILY": "One row per insert day from 01-Apr-2026. Read down the N_DISTINCT_KEYS column: it tracks N_ROWS until 11-Apr and collapses to a near-constant small number from 12-Apr onward, including after the September reload. This shows the change has a date, and that it never recovered.",
    "A5_SAMPLE": "20 actual rows sharing the most frequent identifier value. Different devices, different event times, same identifier - so the identifier cannot distinguish one cashbox event from another.",
    "B1_EOD_SUMMARY": "One row. N_FUTURE_ROWS counts rows whose TRANSIT_DAY_KEY is greater than today's date key. PCT_OF_TABLE shows how small the affected population is. MAX_FUTURE_KEY is the furthest date.",
    "B2_EOD_DETAIL": "The complete list of offending rows, one line each. The key is split into year/month/day with SUBSTR rather than converted with TO_DATE, so a malformed key cannot abort the query. Compare TRANSIT_DAY against TRANSIT_DAY_KEY on each row: where the date column is sane and only the derived key is not, the defect is in the key derivation. This is the population we propose to exclude as outliers.",
    "B3_DM_SUMMARY": "Same measure, restricted to rows inserted since 12-Apr-2026 so the query stays on the indexed EDW_INSERTED_DTM column and returns quickly. NOTE: our contract resolves DEVICE_METRIC to the EDW schema. If test T0 shows a different owner in your environment, change it here and in B4.",
    "B4_DM_DETAIL": "The complete list of offending rows. Small enough to review line by line. EDW_INSERTED_DTM shows when each row arrived, so it is clear these are recent loads rather than historical residue.",
    "C1_SCORECARD": "Six rows. MEASURED_VALUE is what your database returns right now; EXPECTED_IF_HEALTHY is what a healthy feed would return. Copy the output back to us and we can close or revise each item."
}

ORDER = [
    "T0_OBJECTS",
    "A1_HEADLINE",
    "A2_CENSUS",
    "A3_BEFORE_AFTER",
    "A4_DAILY",
    "A5_SAMPLE",
    "B1_EOD_SUMMARY",
    "B2_EOD_DETAIL",
    "B3_DM_SUMMARY",
    "B4_DM_DETAIL",
    "C1_SCORECARD"
]

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
def ora_sql(body, secs=QUERY_TO):
    c = _creds()
    return (spark.read.format("jdbc").option("url", c["url"]).option("dbtable", f"({body}) q")
            .option("user", c["user"]).option("password", c["pwd"])
            .option("driver", "oracle.jdbc.OracleDriver").option("queryTimeout", str(secs))
            .option("oracle.net.CONNECT_TIMEOUT", "10000").option("oracle.jdbc.ReadTimeout", READ_TO)
            .option("sessionInitStatement", "ALTER SESSION SET NLS_DATE_FORMAT='YYYY-MM-DD HH24:MI:SS'")
            .load())

SAVED, RESULTS = [], {}
def run(tag, secs=QUERY_TO, show=40, save=True):
    """Print the exact SQL, run it, print and persist the result."""
    print("=" * 110)
    print(f"[{tag}]  {TITLES[tag]}")
    print(f"        {NOTES[tag]}")
    print("-" * 110)
    print(SQL[tag])
    print("-" * 110)
    t0 = _dt.datetime.now()
    df = ora_sql(SQL[tag], secs)
    pdf = df.toPandas()
    secs_taken = (_dt.datetime.now() - t0).total_seconds()
    with __import__("pandas").option_context("display.max_columns", None, "display.width", 200):
        print(pdf.head(show).to_string(index=False))
    if len(pdf) > show:
        print(f"... {len(pdf) - show} more rows (full set in the CSV)")
    print(f"[{tag}] {len(pdf)} row(s) in {secs_taken:.1f}s")
    RESULTS[tag] = pdf
    if save:
        try:
            dbutils.fs.put(f"{OUT_DIR}/{tag}.csv", pdf.to_csv(index=False), True)
            SAVED.append(f"{tag}.csv")
            print(f"[{tag}] saved -> {OUT_DIR}/{tag}.csv")
        except Exception as e:
            print(f"[{tag}] csv save failed ({str(e).splitlines()[0][:60]}) - printed output above stands as evidence")
    return pdf

ora_sql("SELECT 1 ok FROM dual", 20).collect()
print(f"NB18 DQ proof pack | run {RUN_TS} | {len(ORDER)} queries | out={OUT_DIR}")
print("READ-ONLY: SELECT statements only; identical text to the .sql handed to Cubic.")

# COMMAND ----------
# ============================== CELL 2 : T0 — OBJECT RESOLUTION ==============================
t0 = run("T0_OBJECTS", secs=120)
print("\nIf DEVICE_METRIC resolves to an owner other than EDW, update B3_DM_SUMMARY / B4_DM_DETAIL")
print("in BOTH this notebook and the .sql handed to Cubic before quoting those numbers.")

# COMMAND ----------
# ============================== CELL 3 : ISSUE 1 — CASHBOX_EVENT_ID ==============================
a1 = run("A1_HEADLINE", secs=900)
a2 = run("A2_CENSUS",  secs=900)
a3 = run("A3_BEFORE_AFTER", secs=900)
a4 = run("A4_DAILY", secs=900, show=200)
a5 = run("A5_SAMPLE", secs=900)

# plain-language reading of what came back
print("\n" + "=" * 110)
print("READING OF ISSUE 1")
try:
    r = a1.iloc[0]
    print(f"  {int(r['N_ROWS']):,} rows loaded since 12-Apr-2026 ({r['MONTHS_SPANNED']} months) carry only "
          f"{int(r['N_DISTINCT_KEYS'])} distinct CASHBOX_EVENT_ID values.")
    print(f"  That is ~{int(r['ROWS_PER_KEY']):,} events sharing each identifier, across "
          f"{int(r['N_DISTINCT_DEVICES']):,} devices.")
    print(f"  The rows themselves are healthy: {int(r['N_NULL_DEVICE_ID'])} NULL DEVICE_ID, "
          f"{int(r['N_NULL_EVENT_DTM'])} NULL EVENT_DTM, {int(r['N_NULL_KEYS'])} NULL keys.")
    before = a3[a3["PERIOD"].str.startswith("BEFORE")].iloc[0]
    after = a3[a3["PERIOD"].str.startswith("AFTER")].iloc[0]
    print(f"  Baseline 01-11 Apr: {float(before['ROWS_PER_KEY']):.2f} rows per key. "
          f"After 12-Apr: {float(after['ROWS_PER_KEY']):,.0f} rows per key.")
    post = a4[a4["INSERT_DAY"].astype(str) > "2026-04-11"]
    if len(post):
        print(f"  Of {len(post)} insert days after 11-Apr, "
              f"{int((post['N_DISTINCT_KEYS'] <= 10).sum())} show 10 or fewer distinct keys "
              f"(max on any single day: {int(post['N_DISTINCT_KEYS'].max())}).")
    print("  => The data was refreshed; the identifier was not restored.")
except Exception as e:
    print(f"  (narrative skipped: {e}) - the query output above is the evidence.")

# COMMAND ----------
# ============================== CELL 4 : ISSUE 2 — FUTURE BUSINESS DAY KEYS ==============================
b1 = run("B1_EOD_SUMMARY", secs=1800)
b2 = run("B2_EOD_DETAIL",  secs=1800, show=100)
b3 = run("B3_DM_SUMMARY",  secs=1800)
b4 = run("B4_DM_DETAIL",   secs=1800, show=100)

print("\n" + "=" * 110)
print("READING OF ISSUE 2")
try:
    e, d = b1.iloc[0], b3.iloc[0]
    print(f"  DEVICE_END_OF_DAY: {int(e['N_FUTURE_ROWS'])} of {int(e['N_TOTAL_ROWS']):,} rows are dated after "
          f"today ({float(e['PCT_OF_TABLE']):.6f}%); furthest key {e['MAX_FUTURE_KEY']}.")
    print(f"  DEVICE_METRIC:     {int(d['N_FUTURE_ROWS'])} of {int(d['N_WINDOW_ROWS']):,} rows loaded since "
          f"12-Apr are dated after today ({float(d['PCT_OF_WINDOW']):.8f}%); furthest key {d['MAX_FUTURE_KEY']}.")
    print("  Every offending row is listed in B2/B4 above and in the CSVs, so the rows can be")
    print("  inspected individually before agreeing to exclude them as outliers.")
except Exception as ex:
    print(f"  (narrative skipped: {ex}) - the query output above is the evidence.")

# COMMAND ----------
# ============================== CELL 5 : SCORECARD + MANIFEST + AUDIT WRITE ==============================
c1 = run("C1_SCORECARD", secs=1800)

payload = {"run_ts": RUN_TS,
           "cashbox": {k: (None if a1.iloc[0][k] is None else str(a1.iloc[0][k])) for k in a1.columns},
           "end_of_day": {k: (None if b1.iloc[0][k] is None else str(b1.iloc[0][k])) for k in b1.columns},
           "device_metric": {k: (None if b3.iloc[0][k] is None else str(b3.iloc[0][k])) for k in b3.columns},
           "scorecard": c1.to_dict(orient="records"),
           "csvs": SAVED}
print("\n" + "=" * 110)
print(f"CSV evidence ({len(SAVED)} files) under {OUT_DIR}/:")
for s in SAVED:
    print(f"  - {s}")
try:
    (spark.createDataFrame([("nb18_dq_proof", _dt.datetime.utcnow().isoformat(),
                             json.dumps(payload, default=str))],
                           "probe string, run_ts string, payload string")
     .write.format("delta").mode("append").saveAsTable(PROBE_TBL))
    print(f"payload -> {PROBE_TBL} (probe='nb18_dq_proof', run {RUN_TS})")
except Exception as e:
    print(f"(audit write skipped: {str(e).splitlines()[0][:70]})")
print("\nRead-only run complete - no source or lakehouse data was modified.")
print("Hand Cubic: CUBIC_MARS_Chicago_DQ_Proof_Queries_v1.sql (same query text as above).")
