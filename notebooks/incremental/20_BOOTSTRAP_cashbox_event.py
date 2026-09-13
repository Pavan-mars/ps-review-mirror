# Databricks notebook source
# MAGIC %md
# MAGIC # 20 — BOOTSTRAP `NCS_STAGE.CASHBOX_EVENT` into raw + bronze + the contract
# MAGIC
# MAGIC A one-time notebook, because NB16 cannot bootstrap a new table: its engine starts every
# MAGIC table with `spark.table(mars_dev.bronze.<t>)`, which throws when the bronze table does not
# MAGIC exist yet, and it only iterates tables that already have a contract row. This creates both.
# MAGIC After it runs, `CASHBOX_EVENT` is an ordinary contract table and NB16 refreshes it like
# MAGIC any other `full` snapshot.
# MAGIC
# MAGIC | cell | does | writes? |
# MAGIC |---|---|---|
# MAGIC | 2 | resolve owner, profile columns, count rows — **refuses to continue if it isn't a lookup** | no |
# MAGIC | 3 | does its key decode the seven `CASHBOX_EVENT_ID` values? (evidence for the Cubic thread) | no |
# MAGIC | 4 | full pull → raw Parquet → bronze Delta, three-way reconciled | **yes** |
# MAGIC | 5 | register the contract row, cloned from an existing `full` lookup so the schema matches | **yes** |
# MAGIC | 6 | verify both layers and print what changed | no |
# MAGIC
# MAGIC Loads **the whole table from the beginning** — it is a `full` snapshot strategy, so there is
# MAGIC no watermark and no 11-Apr floor. Safe to re-run: raw goes to a dated partition, bronze is
# MAGIC `CREATE OR REPLACE`, and the contract insert is skipped if the row already exists.

# COMMAND ----------
# ============================== CELL 1 : CONFIG ==============================
import datetime as _dt, json
from pyspark.sql import functions as F

TABLE_NAME   = "CASHBOX_EVENT"          # resolved to its real owner in cell 2
BRONZE_NAME  = None                     # derived once the owner is known
DRY_RUN      = False                    # tiny lookup; cells 2-3 are read-only regardless
MAX_FULL_ROWS = 5_000_000               # above this it is not a lookup - stop and rethink

CAT, BRONZE_DB = "mars_dev", "mars_dev.bronze"
RAW_ROOT   = "s3://cubic-mars-pm-s3-datalake-dev-raw-170202974600/chicago_ventra"
CONTRACT   = "mars_dev.audit.bronze_data_contract"
RUN_LOG    = "mars_dev.audit.incremental_run_log"
TEMPLATE_ROW = "ncs_stage_event"        # an existing static `full` lookup to clone the schema from
ODS_HOST, ODS_PORT, SCOPE_SECRET = "10.3.10.30", 1521, "cubic"
SDU, QUERY_TO, READ_TO, FETCH_SIZE = 512, 900, "2100000", 10000
WINDOW     = "2026-04-12 00:00:00"
BATCH_ID   = _dt.datetime.now().strftime("%Y%m%d_%H%M%S")
TODAY      = _dt.date.today()

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
            .option("fetchsize", str(FETCH_SIZE))
            .option("sessionInitStatement", "ALTER SESSION SET NLS_DATE_FORMAT='YYYY-MM-DD HH24:MI:SS'")
            .load())

def audit_cols(df, owner_table):
    """Same audit stamp the NB16 engine applies, so bronze stays consistent."""
    d = F.current_date()
    return (df.withColumn("_ingest_ts", F.current_timestamp())
              .withColumn("_batch_id", F.lit(BATCH_ID))
              .withColumn("_source_system", F.lit("chicago_oracle_ods"))
              .withColumn("_source_table", F.lit(owner_table))
              .withColumn("year", F.year(d)).withColumn("month", F.month(d))
              .withColumn("day", F.dayofmonth(d)))

ora("(SELECT 1 OK FROM dual) q", 20).collect()
print(f"NB20 bootstrap {TABLE_NAME} | batch={BATCH_ID} | DRY_RUN={DRY_RUN}")

# COMMAND ----------
# ============================== CELL 2 : EXISTENCE + PROFILE (read-only) ==============================
found = ora(f"""(SELECT owner, table_name, num_rows, last_analyzed
                   FROM all_tables WHERE table_name = '{TABLE_NAME}'
                  ORDER BY owner) q""", 120).collect()
print("=" * 104)
if not found:
    OWNER = SRC = BRONZE_NAME = None
    N_ORA = 0
    print(f"{TABLE_NAME} DOES NOT EXIST in any schema visible to us.")
    print("Nothing to bootstrap. The seven CASHBOX_EVENT_ID values remain unexplained -")
    print("keep that question open with Cubic.")
else:
    for r in found:
        print(f"  found {r['OWNER']}.{r['TABLE_NAME']}  optimizer_rows={r['NUM_ROWS']}  "
              f"analyzed={r['LAST_ANALYZED']}")
    OWNER = found[0]["OWNER"]
    SRC = f"{OWNER}.{TABLE_NAME}"
    BRONZE_NAME = f"{OWNER.lower()}_{TABLE_NAME.lower()}"
    print(f"\n  source  : {SRC}")
    print(f"  bronze  : {BRONZE_DB}.`{BRONZE_NAME}`")
    print(f"  raw     : {RAW_ROOT}/{OWNER.lower()}/{TABLE_NAME.lower()}/load_date={TODAY}/")

    cols = ora(f"""(SELECT column_id, column_name, data_type, data_length, nullable
                      FROM all_tab_columns
                     WHERE owner='{OWNER}' AND table_name='{TABLE_NAME}'
                     ORDER BY column_id) q""", 120).collect()
    print(f"\n  columns ({len(cols)}):")
    for c in cols:
        print(f"    {c['COLUMN_ID']:>3} {c['COLUMN_NAME']:34s} {c['DATA_TYPE']}({c['DATA_LENGTH']})"
              f"  null={c['NULLABLE']}")

    N_ORA = int(ora(f"(SELECT COUNT(*) N FROM {SRC}) q", 300).collect()[0]["N"])
    print(f"\n  live row count: {N_ORA:,}")
    if N_ORA > MAX_FULL_ROWS:
        print(f"\n  ** STOP ** {N_ORA:,} rows is above the {MAX_FULL_ROWS:,} lookup threshold.")
        print("  This is not a small dimension - it needs a watermark and a CDC strategy,")
        print("  not a full snapshot. Do not run cell 4; come back with a strategy decision.")
    elif N_ORA <= 500:
        print(f"\n  FULL CONTENTS ({N_ORA} rows) - this is what CASHBOX_EVENT_ID points at:")
        ora(f"(SELECT * FROM {SRC}) q", 300).show(N_ORA, truncate=False)

# COMMAND ----------
# ============================== CELL 3 : DOES IT DECODE THE SEVEN VALUES? (read-only) ==============================
DECODES = None
MAX_FK_TEST_ROWS = 50_000   # the FK test pulls the dimension to the driver - keep it small
if OWNER and 0 < N_ORA <= MAX_FK_TEST_ROWS:
    print("=" * 104)
    print("FK TEST — do this table's keys cover the CASHBOX_EVENT_ID values seen in the increment?")
    print("=" * 104)
    live = ora(f"""(SELECT cashbox_event_id, COUNT(*) N_ROWS
                      FROM ncs_stage.cashbox_tracking
                     WHERE inserted_dtm >= TIMESTAMP '{WINDOW}'
                     GROUP BY cashbox_event_id ORDER BY N_ROWS DESC) q""", 900).collect()
    live_vals = set()
    for r in live:
        v = r["CASHBOX_EVENT_ID"]
        if v is not None:
            live_vals.add(int(v))
        print(f"    id={str(v):>6s}  rows={int(r['N_ROWS']):>12,}")
    pdim = ora(f"(SELECT * FROM {SRC}) q", 300).toPandas()
    hits = []
    for col in pdim.columns:
        try:
            vals = set(int(x) for x in pdim[col].dropna().tolist())
        except (ValueError, TypeError):
            continue
        if live_vals and live_vals.issubset(vals):
            hits.append(col)
            print(f"\n    ** {col} covers ALL {len(live_vals)} live values "
                  f"({len(vals)} distinct in the dimension) **")
    DECODES = hits[0] if hits else None
    if DECODES:
        print(f"\n  VERDICT: CASHBOX_EVENT_ID is a FOREIGN KEY into {SRC}.{DECODES}.")
        print("  Seven distinct values across 5.27M rows is CORRECT for a type code, not a defect.")
        print("  -> withdraw the CASHBOX_EVENT_ID item from the Cubic thread.")
    else:
        print(f"\n  No column of {SRC} covers the live values - the FK theory does not hold here.")
else:
    print(f"(FK test skipped - table absent, empty, or above the {MAX_FK_TEST_ROWS:,}-row")
    print(" driver-collect threshold. The load in cell 4 is unaffected.)")

# COMMAND ----------
# ============================== CELL 4 : LOAD — RAW PARQUET + BRONZE DELTA ==============================
LOADED = False
if not OWNER:
    print("nothing to load")
elif N_ORA > MAX_FULL_ROWS:
    print("refusing to full-load a table above the lookup threshold - see cell 2")
elif DRY_RUN:
    print(f"DRY_RUN - would pull {N_ORA:,} rows from {SRC} into:")
    print(f"  raw    {RAW_ROOT}/{OWNER.lower()}/{TABLE_NAME.lower()}/load_date={TODAY}/")
    print(f"  bronze {BRONZE_DB}.`{BRONZE_NAME}`  (CREATE OR REPLACE, Delta)")
else:
    raw_dir = f"{RAW_ROOT}/{OWNER.lower()}/{TABLE_NAME.lower()}/"
    sub = f"{raw_dir}load_date={TODAY.isoformat()}/"
    try:
        pre_v = spark.sql(f"DESCRIBE HISTORY {BRONZE_DB}.`{BRONZE_NAME}` LIMIT 1").first()["version"]
        print(f"bronze table exists — rollback: RESTORE TABLE {BRONZE_DB}.`{BRONZE_NAME}` "
              f"TO VERSION AS OF {pre_v}")
    except Exception:
        print("bronze table does not exist yet — this run creates it")

    print(f"\npulling {N_ORA:,} rows from {SRC} ...")
    pull = ora(f"(SELECT * FROM {SRC}) q", 900)
    # RAW: plain Parquet, dated partition, no year/month/day (matches the engine's full path)
    audit_cols(pull, SRC).drop("year", "month", "day") \
        .write.mode("overwrite").option("compression", "snappy").parquet(sub)
    n_raw = spark.read.parquet(sub).count()
    print(f"  raw   -> {sub}   ({n_raw:,} rows)")

    # BRONZE: Delta, rebuilt from raw so both layers are provably the same bytes
    raw_back = spark.read.parquet(sub).drop("_ingest_ts", "_batch_id", "_source_system", "_source_table")
    (audit_cols(raw_back, SRC)
       .write.format("delta").mode("overwrite").option("overwriteSchema", "true")
       .saveAsTable(f"{BRONZE_DB}.`{BRONZE_NAME}`"))
    n_bz = spark.sql(f"SELECT COUNT(*) n FROM {BRONZE_DB}.`{BRONZE_NAME}`").first()["n"]
    print(f"  bronze-> {BRONZE_DB}.`{BRONZE_NAME}`   ({n_bz:,} rows)")

    reconciled = (N_ORA == n_raw == n_bz)
    print(f"\n  THREE-WAY RECONCILE: oracle={N_ORA:,}  raw={n_raw:,}  bronze={n_bz:,}  "
          f"-> {'PASS' if reconciled else 'FAIL'}")
    LOADED = reconciled

# COMMAND ----------
# ============================== CELL 5 : REGISTER THE CONTRACT ROW ==============================
if not (OWNER and LOADED):
    print("skipped - nothing loaded, so nothing to register")
else:
    existing = spark.table(CONTRACT).where(F.col("table") == BRONZE_NAME).count()
    if existing:
        print(f"contract already has a row for {BRONZE_NAME} - leaving it alone (idempotent)")
    else:
        # Clone an existing static `full` lookup so every column of the contract schema is
        # populated with the right shape, then override the identity fields.
        tmpl = spark.table(CONTRACT).where(F.col("table") == TEMPLATE_ROW).limit(1)
        if tmpl.count() == 0:
            print(f"** template row '{TEMPLATE_ROW}' not found - register manually **")
        else:
            new = (tmpl.withColumn("table", F.lit(BRONZE_NAME))
                       .withColumn("source", F.lit(SRC))
                       .withColumn("load_strategy", F.lit("full"))
                       .withColumn("bronze_write", F.lit("CREATE OR REPLACE"))
                       .withColumn("status", F.lit("measured_v2")))
            for c, v in [("scope_col", "-"), ("pull_wm", "-"), ("pull_wm_indexed", "-"),
                         ("bronze_base_hint", "-"), ("merge_key", "-"), ("dedupe_key", "-"),
                         ("chunking", "-")]:
                if c in new.columns:
                    new = new.withColumn(c, F.lit(v))
            ev = (f"bootstrapped by NB20 on {TODAY}: {N_ORA:,}-row lookup"
                  + (f"; decodes CASHBOX_TRACKING.CASHBOX_EVENT_ID via {DECODES}" if DECODES else ""))
            for c in new.columns:
                if c.startswith("evidence"):
                    new = new.withColumn(c, F.lit(ev))
            new.write.format("delta").mode("append").saveAsTable(CONTRACT)
            print(f"registered contract row for {BRONZE_NAME}:")
            spark.table(CONTRACT).where(F.col("table") == BRONZE_NAME).show(1, truncate=False, vertical=True)
            print("NB16 will now pick this table up in STAGE='A_FAST' as an ordinary full snapshot.")

# COMMAND ----------
# ============================== CELL 6 : VERIFY BOTH LAYERS ==============================
if OWNER and LOADED:
    print("=" * 104)
    d = spark.sql(f"DESCRIBE DETAIL {BRONZE_DB}.`{BRONZE_NAME}`").first()
    n = spark.sql(f"SELECT COUNT(*) n FROM {BRONZE_DB}.`{BRONZE_NAME}`").first()["n"]
    print(f"BRONZE  {BRONZE_DB}.`{BRONZE_NAME}`")
    print(f"  format={d['format']}  rows={n:,}  files={d['numFiles']}  "
          f"size={(d['sizeInBytes'] or 0)/1024:,.1f} KB")
    print(f"  location={d['location']}")
    parts = sorted([f.name.rstrip('/') for f in
                    dbutils.fs.ls(f"{RAW_ROOT}/{OWNER.lower()}/{TABLE_NAME.lower()}/")], reverse=True)
    print(f"\nRAW     {RAW_ROOT}/{OWNER.lower()}/{TABLE_NAME.lower()}/")
    print(f"  partitions: {', '.join(parts[:5])}")

    rows = [(BATCH_ID, _dt.datetime.utcnow().isoformat(), BRONZE_NAME, str(DRY_RUN), "bootstrap",
             str(N_ORA), json.dumps({"status": "BOOTSTRAPPED", "oracle_rows": N_ORA,
                                     "bronze_rows": n, "decodes_cashbox_event_id": DECODES},
                                    default=str))]
    try:
        (spark.createDataFrame(rows, "batch_id string, run_ts string, table string, dry_run string, "
                                     "bronze_version_before string, rows_before string, result string")
         .write.format("delta").mode("append").saveAsTable(RUN_LOG))
        print(f"\nrun log -> {RUN_LOG} (batch {BATCH_ID})")
    except Exception as e:
        print(f"\n(run-log write skipped: {str(e).splitlines()[0][:70]})")

    print("\nDONE. Both layers hold the full table from the beginning, the contract row is")
    print("registered, and NB16 will refresh it from now on with the other full snapshots.")
    if DECODES:
        print(f"\nAlso settled: CASHBOX_EVENT_ID decodes via {SRC}.{DECODES} - that DQ item")
        print("should be withdrawn from the Cubic thread.")
else:
    print("nothing to verify")
