# Databricks notebook source
# MAGIC %md
# MAGIC # 19 — SOURCE INVENTORY vs CONTRACT COVERAGE  (read-only)
# MAGIC
# MAGIC Two questions, one pass, no writes:
# MAGIC
# MAGIC 1. **Does `NCS_STAGE.CASHBOX_EVENT` exist?** If it does and it is a small lookup whose key
# MAGIC    covers the seven values we found in `CASHBOX_TRACKING.CASHBOX_EVENT_ID` (2,3,4,6,7,8,9),
# MAGIC    then that column is a **foreign key working as designed** — not a broken identifier —
# MAGIC    and the DQ item we raised with Cubic should be withdrawn.
# MAGIC 2. **What else is in the source that our 32-row contract never scoped?** Cell 3 diffs every
# MAGIC    table in `NCS_STAGE`, `EDW` and `CTA` against the contract and lists what we have missed.
# MAGIC
# MAGIC Run on the Ventra compute. Every statement is a `SELECT`; the only heavy-ish query is the
# MAGIC census of `CASHBOX_TRACKING`, which is bounded on the indexed `INSERTED_DTM`.

# COMMAND ----------
# ============================== CELL 1 : CONFIG + CONNECTION ==============================
import datetime as _dt, json
from pyspark.sql import functions as F

ODS_HOST, ODS_PORT, SCOPE_SECRET = "10.3.10.30", 1521, "cubic"
SDU, QUERY_TO, READ_TO, FETCH_SIZE = 512, 900, "2100000", 10000
CONTRACT   = "mars_dev.audit.bronze_data_contract"
WINDOW     = "2026-04-12 00:00:00"
SCHEMAS    = ("NCS_STAGE", "EDW", "CTA")
RUN_TS     = _dt.datetime.now().strftime("%Y%m%d_%H%M%S")

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

ora("(SELECT 1 OK FROM dual) q", 20).collect()
print(f"NB19 source inventory | run {RUN_TS} | READ-ONLY")

# COMMAND ----------
# ============================== CELL 2 : DOES CASHBOX_EVENT EXIST? ==============================
print("=" * 112)
print("EVERY CASHBOX* OBJECT VISIBLE TO US")
print("=" * 112)
cb = ora("""(SELECT owner, table_name, num_rows, last_analyzed
               FROM all_tables
              WHERE table_name LIKE 'CASHBOX%'
              ORDER BY owner, table_name) q""", 120)
cb_rows = cb.collect()
if not cb_rows:
    print("  none found - CASHBOX_TRACKING may be the only cashbox object we can see")
for r in cb_rows:
    print(f"  {r['OWNER']}.{r['TABLE_NAME']:32s} optimizer_rows={r['NUM_ROWS']}  "
          f"analyzed={r['LAST_ANALYZED']}")

TARGET = next((f"{r['OWNER']}.{r['TABLE_NAME']}" for r in cb_rows
               if r["TABLE_NAME"] == "CASHBOX_EVENT"), None)

if TARGET is None:
    print("\n  >> NCS_STAGE.CASHBOX_EVENT does NOT exist.")
    print("     The seven values are then either an undocumented enum or a genuine defect;")
    print("     keep the question open with Cubic.")
else:
    print(f"\n  >> FOUND {TARGET} - investigating as the likely lookup for CASHBOX_EVENT_ID")
    cols = ora(f"""(SELECT column_id, column_name, data_type, data_length, nullable
                      FROM all_tab_columns
                     WHERE owner='{TARGET.split('.')[0]}' AND table_name='{TARGET.split('.')[1]}'
                     ORDER BY column_id) q""", 120)
    print(f"\n  columns of {TARGET}:")
    for r in cols.collect():
        print(f"    {r['COLUMN_ID']:>3} {r['COLUMN_NAME']:32s} {r['DATA_TYPE']}({r['DATA_LENGTH']}) "
              f"null={r['NULLABLE']}")
    n = int(ora(f"(SELECT COUNT(*) N FROM {TARGET}) q", 300).collect()[0]["N"])
    print(f"\n  row count: {n:,}")
    if n <= 500:
        dim = ora(f"(SELECT * FROM {TARGET}) q", 300).cache()
        print(f"\n  FULL CONTENTS ({n} rows):")
        dim.show(n, truncate=False)
    else:
        dim = None
        print("  (too large to dump - not a simple lookup)")

# COMMAND ----------
# ============================== CELL 3 : DOES IT DECODE THE SEVEN VALUES? ==============================
# The census we measured on 09-Sep. If a column of CASHBOX_EVENT contains exactly these,
# CASHBOX_EVENT_ID is a foreign key doing its job.
OBSERVED = {2, 3, 4, 6, 7, 8, 9}
print("=" * 112)
print(f"OBSERVED CASHBOX_EVENT_ID VALUES IN THE INCREMENT: {sorted(OBSERVED)}")
print("=" * 112)

live = ora(f"""(SELECT cashbox_event_id, COUNT(*) N_ROWS
                  FROM ncs_stage.cashbox_tracking
                 WHERE inserted_dtm >= TIMESTAMP '{WINDOW}'
                 GROUP BY cashbox_event_id
                 ORDER BY N_ROWS DESC) q""", 900)
live_vals, live_rows = set(), live.collect()
print("  live census:")
for r in live_rows:
    v = r["CASHBOX_EVENT_ID"]
    live_vals.add(int(v) if v is not None else None)
    print(f"    id={str(v):>6s}  rows={int(r['N_ROWS']):>12,}")

if TARGET and dim is not None:
    pdim = dim.toPandas()
    print(f"\n  testing every column of {TARGET} for coverage of the live values...")
    matches = []
    for col in pdim.columns:
        try:
            vals = set(int(x) for x in pdim[col].dropna().tolist())
        except (ValueError, TypeError):
            continue
        covered = live_vals - {None}
        if covered and covered.issubset(vals):
            matches.append((col, len(vals)))
            print(f"    ** {col}: contains ALL {len(covered)} live values "
                  f"(dimension has {len(vals)} distinct) **")
    if matches:
        key = matches[0][0]
        print(f"\n  VERDICT: CASHBOX_EVENT_ID is a FOREIGN KEY into {TARGET}.{key}.")
        print(f"  Seven distinct values across millions of rows is CORRECT behaviour for a type code.")
        print(f"  ACTION: ingest {TARGET} as a small `full` dimension so silver can decode the codes,")
        print(f"          and withdraw the CASHBOX_EVENT_ID item from the Cubic thread.")
    else:
        print(f"\n  No column of {TARGET} covers the live values - the FK theory does not hold.")
        print(f"  Keep the question open with Cubic.")
elif TARGET:
    print("\n  dimension too large to test automatically - inspect the columns above by hand")

# COMMAND ----------
# ============================== CELL 4 : WHAT ELSE HAVE WE NEVER SCOPED? ==============================
print("=" * 112)
print("SOURCE INVENTORY vs OUR CONTRACT")
print("=" * 112)
inv = ora(f"""(SELECT owner, table_name, num_rows, last_analyzed
                 FROM all_tables
                WHERE owner IN ({','.join(f"'{s}'" for s in SCHEMAS)})
                ORDER BY owner, table_name) q""", 300).collect()
in_ora = {f"{r['OWNER']}.{r['TABLE_NAME']}": r for r in inv}

con = [r.asDict() for r in spark.table(CONTRACT).collect()]
in_con = {str(c.get("source") or "").upper() for c in con if "." in str(c.get("source") or "")}

missing = sorted(k for k in in_ora if k not in in_con)
extra = sorted(k for k in in_con if k not in in_ora)

print(f"\nOracle tables in {SCHEMAS}: {len(in_ora)}    contract rows: {len(in_con)}")
print(f"\nIN ORACLE BUT NOT IN OUR CONTRACT — {len(missing)} table(s):")
print(f"  {'table':46s} {'optimizer_rows':>16s}  last_analyzed")
for k in missing:
    r = in_ora[k]
    nr = f"{r['NUM_ROWS']:,}" if r["NUM_ROWS"] is not None else "unanalyzed"
    print(f"  {k:46s} {nr:>16s}  {r['LAST_ANALYZED']}")

if extra:
    print(f"\nIN OUR CONTRACT BUT NOT VISIBLE IN ORACLE — {len(extra)}:")
    for k in extra:
        print(f"  {k}  (dropped at source, renamed, or a permissions gap)")

print("\n" + "-" * 112)
print("Triage the list above: a small unanalyzed table is usually a lookup worth ingesting;")
print("anything large needs a watermark decision before it enters the contract.")
print("Nothing here has been changed - this notebook only reads.")
