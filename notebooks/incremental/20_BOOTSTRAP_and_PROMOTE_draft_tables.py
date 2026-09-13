# Databricks notebook source
# MAGIC %md
# MAGIC # 20 v4 — PROMOTE (and load only where needed) the draft contract tables
# MAGIC
# MAGIC ## What the 10-Sep scan changed
# MAGIC
# MAGIC All 21 `draft` tables **already exist in bronze with data** — the original loader built them;
# MAGIC only our incremental engine has never touched them. And they carry **four different audit
# MAGIC conventions**:
# MAGIC
# MAGIC | # | audit columns | tables |
# MAGIC |---|---|---|
# MAGIC | 1 | `_ingest_date, _ingest_ts, _load_batch_id, _source_row_count, _source_schema, _source_system, _source_table` | 11 |
# MAGIC | 2 | `_ingest_ts, _source_system` | 7 |
# MAGIC | 3 | `_raw_batch_id, _raw_ingest_ts, _source_system, _source_table` | 2 |
# MAGIC | 4 | *(none at all)* | 1 |
# MAGIC
# MAGIC v3 detected one convention from `ncs_stage_event` and applied it to everything. That was
# MAGIC **wrong**: it would have dropped `_load_batch_id`, `_source_row_count` and `_source_schema`
# MAGIC from 11 tables, dropped `_raw_batch_id`/`_raw_ingest_ts` from 2, and added `year/month/day`
# MAGIC to all 21 — a schema rewrite of every table, on the assumption that one sibling speaks for
# MAGIC the rest.
# MAGIC
# MAGIC ## Two corrections
# MAGIC
# MAGIC **1. Convention is read per table, from the table itself.** `SCHEMA_POLICY = "preserve"`
# MAGIC keeps each target's own audit columns, stamping them by *role* — whichever column plays the
# MAGIC batch role gets the batch id, whatever it is called. Writes go out **without**
# MAGIC `overwriteSchema`, so Delta rejects any accidental schema drift rather than silently applying
# MAGIC it. `"normalize"` is available but converts a table to the NB16 shape — only choose it once
# MAGIC you know nothing downstream reads the columns it drops.
# MAGIC
# MAGIC **2. A table already in sync is not reloaded.** Cell 2 compares the live Oracle count against
# MAGIC the bronze count per table:
# MAGIC
# MAGIC | verdict | condition | what happens |
# MAGIC |---|---|---|
# MAGIC | `PROMOTE_ONLY` | bronze exists, counts match | **no load, no schema touch** — contract row only |
# MAGIC | `RELOAD` | bronze exists, counts differ | reload under `SCHEMA_POLICY`, then promote |
# MAGIC | `BOOTSTRAP` | bronze absent | full load + register a new contract row |
# MAGIC | `STOP_GUARD` | above `MAX_FULL_ROWS` | skipped — needs a watermark, not a snapshot |
# MAGIC | `STOP_STRATEGY` | v1 strategy verb | skipped — needs a strategy decision first |
# MAGIC
# MAGIC For static dimensions that have not moved since the original load, `PROMOTE_ONLY` is the
# MAGIC common case: a contract update, no data movement and no schema risk at all.

# COMMAND ----------
# ============================== CELL 1 : CONFIG ==============================
import datetime as _dt, json
from pyspark.sql import functions as F

TABLES = ["NCS_STAGE.CASHBOX_EVENT"]

FULL_DRAFTS = [
    "NCS_STAGE.CASHBOX_EVENT", "EDW.CASHBOX_EVENT_DIMENSION", "EDW.CASHBOX_TYPE_DIMENSION",
    "EDW.ACTIVITY_CODE_DIMENSION", "EDW.DEVICE_MESSAGE_TYPE_DIMENSION", "EDW.OPERATOR_DIMENSION",
    "EDW.ROUTE_DIMENSION", "EDW.STOP_POINT_DIMENSION", "EDW.TIME_INCREMENT_DIMENSION",
    "EDW.TIME_PERIOD_DIMENSION", "EDW.DATE_DIMENSION", "NCS_STAGE.ACTIVITY_CODE",
    "NCS_STAGE.DEVICE_CONTROL_GROUP", "NCS_STAGE.DEVICE_CURRENT_HW_CONFIG",
    "CTA.METRIX_FACILITY_MAP", "CTA.RAIL_DEVICE_IP_ADDRESS_XREF", "CTA.KPI_TVM_TABLE",
    "EDW.AVAILABILITY_PERIODS", "EDW.DEVICE_LOCATION_HISTORY", "EDW.AJ_METRIC_FACT",
    "CTA.MM_DAILY_TRANSACTION_TIMING",
]
# TABLES = FULL_DRAFTS

DRY_RUN       = False
PROMOTE       = True
SCHEMA_POLICY = "preserve"   # preserve = keep each table's own audit columns (SAFE DEFAULT)
                             # normalize = rewrite to the NB16 shape (DROPS columns - see the header)
MAX_FULL_ROWS = 5_000_000

CAT, BRONZE_DB = "mars_dev", "mars_dev.bronze"
RAW_ROOT   = "s3://cubic-mars-pm-s3-datalake-dev-raw-170202974600/chicago_ventra"
CONTRACT   = "mars_dev.audit.bronze_data_contract"
RUN_LOG    = "mars_dev.audit.incremental_run_log"
TEMPLATE_ROW  = "ncs_stage_event"
V2_STRATEGIES = {"full", "full_scoped", "cdc_merge", "cdc_append"}

ODS_HOST, ODS_PORT, SCOPE_SECRET = "10.3.10.30", 1521, "cubic"
SDU, QUERY_TO, READ_TO, FETCH_SIZE = 512, 900, "2100000", 10000
WINDOW   = "2026-04-12 00:00:00"
BATCH_ID = _dt.datetime.now().strftime("%Y%m%d_%H%M%S")
TODAY    = _dt.date.today()

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

# ---------------------------------------------------------------------------------------
# AUDIT COLUMNS BY ROLE, not by name. Bronze carries at least four conventions, so we ask
# what each column MEANS and stamp whichever name the target actually uses.
# ---------------------------------------------------------------------------------------
AUDIT_ROLES = {
    "batch":       ["_load_batch_id", "_raw_batch_id", "_batch_id"],
    "ingest_ts":   ["_raw_ingest_ts", "_ingest_ts", "_ingested_at"],
    "ingest_date": ["_ingest_date"],
    "system":      ["_raw_source_system", "_source_system"],
    "table":       ["_raw_source_table", "_source_table"],
    "row_count":   ["_source_row_count"],
    "schema":      ["_source_schema"],
}
NB16_SHAPE = {"batch": "_batch_id", "ingest_ts": "_ingest_ts", "ingest_date": None,
              "system": "_source_system", "table": "_source_table",
              "row_count": None, "schema": None, "ymd": True}

def convention_of(cols):
    low = {c.lower(): c for c in cols}
    conv = {r: next((low[n] for n in names if n in low), None)
            for r, names in AUDIT_ROLES.items()}
    conv["ymd"] = all(k in low for k in ("year", "month", "day"))
    return conv

def audit_names(conv):
    out = [v for k, v in conv.items() if k != "ymd" and v]
    return out + (["year", "month", "day"] if conv["ymd"] else [])

def stamp(df, conv, src, owner, n_rows, types=None):
    """Write the audit values into whichever columns this target carries."""
    types = types or {}
    def put(col, val):
        return F.lit(val).cast(types[col]) if col in types else F.lit(val)
    out, d = df, F.current_date()
    if conv["batch"]:       out = out.withColumn(conv["batch"],       put(conv["batch"], BATCH_ID))
    if conv["ingest_ts"]:   out = out.withColumn(conv["ingest_ts"],   F.current_timestamp())
    if conv["ingest_date"]: out = out.withColumn(conv["ingest_date"], d)
    if conv["system"]:      out = out.withColumn(conv["system"],      put(conv["system"], "chicago_oracle_ods"))
    if conv["table"]:       out = out.withColumn(conv["table"],       put(conv["table"], src))
    if conv["row_count"]:   out = out.withColumn(conv["row_count"],   put(conv["row_count"], n_rows))
    if conv["schema"]:      out = out.withColumn(conv["schema"],      put(conv["schema"], owner))
    if conv["ymd"]:
        out = (out.withColumn("year", F.year(d)).withColumn("month", F.month(d))
                  .withColumn("day", F.dayofmonth(d)))
    return out

def align_to(df, target_cols, target_types):
    """Project onto the target's exact columns and order. A column the source lacks becomes a
    TYPED null - an untyped F.lit(None) is NullType, which Parquet refuses (killed
    edw_read_transaction on 10-Sep)."""
    have = {c.lower(): c for c in df.columns}
    sel = []
    for c in target_cols:
        if c.lower() in have:
            sel.append(F.col(have[c.lower()]).cast(target_types.get(c, "string")).alias(c)
                       if c in target_types else F.col(have[c.lower()]).alias(c))
        else:
            sel.append(F.lit(None).cast(target_types.get(c, "string")).alias(c))
    return df.select(*sel)

assert SCHEMA_POLICY in ("preserve", "normalize"), "SCHEMA_POLICY must be preserve or normalize"
ora("(SELECT 1 OK FROM dual) q", 20).collect()
print(f"NB20 v4 | batch={BATCH_ID} | {len(TABLES)} table(s)")
print(f"DRY_RUN={DRY_RUN}  PROMOTE={PROMOTE}  SCHEMA_POLICY={SCHEMA_POLICY}")
if SCHEMA_POLICY == "normalize":
    print("\n  ** normalize REWRITES each target to the NB16 shape and DROPS audit columns")
    print("     it does not use. Only run this once you know nothing downstream reads them. **")
else:
    print("\n  preserve: each table keeps its own audit columns; writes omit overwriteSchema,")
    print("  so Delta refuses any accidental schema change instead of applying it silently.")
print(f"targets: {', '.join(TABLES)}")

# COMMAND ----------
# ============================== CELL 2 : PLAN — counts, convention, verdict (read-only) ==============================
CON_ROWS = {str(r["table"]): r.asDict() for r in spark.table(CONTRACT).collect()}
EXISTING = {r["tableName"] for r in spark.sql(f"SHOW TABLES IN {BRONZE_DB}").collect()}
PLAN = []

print("=" * 132)
print(f"{'source':40s} {'oracle':>10s} {'bronze':>10s} {'diff':>9s} {'audit cols':>11s}  verdict")
print("-" * 132)

for spec in TABLES:
    owner_hint, tname = (spec.split(".", 1) if "." in spec else (None, spec))
    where_owner = f" AND owner = '{owner_hint}'" if owner_hint else ""
    hits = ora(f"""(SELECT owner, table_name FROM all_tables
                     WHERE table_name = '{tname}'{where_owner} ORDER BY owner) q""", 120).collect()
    if not hits:
        print(f"{spec:40s} {'-':>10s} {'-':>10s} {'-':>9s} {'-':>11s}  NOT_VISIBLE - skipped")
        continue
    owner = hits[0]["OWNER"]
    src   = f"{owner}.{tname}"
    bname = f"{owner.lower()}_{tname.lower()}"

    n_ora = int(ora(f"(SELECT COUNT(*) N FROM {src}) q", 300).collect()[0]["N"])
    crow  = CON_ROWS.get(bname)
    strat = str(crow.get("load_strategy") or "-") if crow else "full"

    if bname in EXISTING:
        bdf   = spark.table(f"{BRONZE_DB}.`{bname}`")
        n_bz  = bdf.count()
        conv  = convention_of(bdf.columns)
        tcols, ttypes = bdf.columns, dict(bdf.dtypes)
    else:
        n_bz, conv, tcols, ttypes = None, dict(NB16_SHAPE), None, {}

    if n_ora == 0:                       verdict = "SKIP - source empty"
    elif n_ora > MAX_FULL_ROWS:          verdict = f"STOP_GUARD - {n_ora:,} needs a watermark"
    elif crow and strat not in V2_STRATEGIES:
        verdict = f"STOP_STRATEGY - '{strat}' not implemented by the v2 engine"
    elif n_bz is None:                   verdict = "BOOTSTRAP - load + register"
    elif n_bz == n_ora:                  verdict = "PROMOTE_ONLY - in sync, no load"
    else:                                verdict = f"RELOAD - {n_ora - n_bz:+,} vs bronze"

    diff = "-" if n_bz is None else f"{n_ora - n_bz:+,}"
    print(f"{src:40s} {n_ora:>10,} {('-' if n_bz is None else f'{n_bz:,}'):>10s} {diff:>9s} "
          f"{len(audit_names(conv)):>11d}  {verdict}")

    if verdict.startswith(("BOOTSTRAP", "PROMOTE_ONLY", "RELOAD")):
        PLAN.append({"src": src, "owner": owner, "table": tname, "bronze": bname,
                     "n_ora": n_ora, "n_bz": n_bz, "contract": crow, "strategy": strat,
                     "conv": conv, "tcols": tcols, "ttypes": ttypes,
                     "action": verdict.split(" ")[0]})

from collections import Counter
acts = Counter(p["action"] for p in PLAN)
print("-" * 132)
print(f"{len(PLAN)} of {len(TABLES)} cleared:  " +
      "  ".join(f"{k}={v}" for k, v in sorted(acts.items())) or "nothing to do")
if acts.get("PROMOTE_ONLY"):
    print(f"  {acts['PROMOTE_ONLY']} table(s) are already in sync - contract update only, no data movement.")
if PLAN and len(PLAN) <= 3:
    for p in PLAN:
        if p["n_ora"] <= 500:
            print(f"\nFULL CONTENTS — {p['src']} ({p['n_ora']} rows):")
            ora(f"(SELECT * FROM {p['src']}) q", 300).show(p["n_ora"], truncate=False)

# COMMAND ----------
# ============================== CELL 3 : CASHBOX FK DECODE TEST (read-only) ==============================
DECODES = None
_cb = next((p for p in PLAN if p["table"] == "CASHBOX_EVENT"), None)
if _cb and _cb["n_ora"] <= 50_000:
    print("=" * 110)
    print("FK TEST — does CASHBOX_EVENT decode the CASHBOX_EVENT_ID values in the increment?")
    print("=" * 110)
    live = ora(f"""(SELECT cashbox_event_id, COUNT(*) N_ROWS
                      FROM ncs_stage.cashbox_tracking
                     WHERE inserted_dtm >= TIMESTAMP '{WINDOW}'
                     GROUP BY cashbox_event_id ORDER BY N_ROWS DESC) q""", 900).collect()
    live_vals = {int(r["CASHBOX_EVENT_ID"]) for r in live if r["CASHBOX_EVENT_ID"] is not None}
    total = sum(int(r["N_ROWS"]) for r in live)
    pdim = ora(f"(SELECT * FROM {_cb['src']}) q", 300).toPandas()
    key  = next((c for c in pdim.columns if c.upper() == "CASHBOX_EVENT_ID"), None)
    desc = next((c for c in pdim.columns if c.upper() == "DESCRIPTION"), None)
    if key and live_vals.issubset({int(x) for x in pdim[key].dropna().tolist()}):
        DECODES = key
        lut = dict(zip(pdim[key].astype(int), pdim[desc])) if desc else {}
        print(f"\n  {'id':>4s}  {'description':32s} {'rows':>12s}  share")
        for r in live:
            v, c = int(r["CASHBOX_EVENT_ID"]), int(r["N_ROWS"])
            print(f"  {v:>4d}  {str(lut.get(v,'?')):32s} {c:>12,}  {c/total:6.2%}")
        print(f"\n  VERDICT: CASHBOX_EVENT_ID is a FOREIGN KEY into {_cb['src']}.{key}.")
        print(f"  {len(live_vals)} of {len(pdim)} codes exercised post-11-Apr — correct for a type code.")
    else:
        print("\n  FK theory does NOT hold - keep the question open with Cubic.")
else:
    print("(FK test not applicable to this table set)")

# COMMAND ----------
# ============================== CELL 4 : LOAD (only where the plan says so) ==============================
LOADED, FAILED, SKIPPED = [], [], []
to_load = [p for p in PLAN if p["action"] in ("BOOTSTRAP", "RELOAD")]
SKIPPED = [p for p in PLAN if p["action"] == "PROMOTE_ONLY"]

if SKIPPED:
    print(f"{len(SKIPPED)} table(s) already in sync — not touched:")
    for p in SKIPPED:
        print(f"   {p['src']:44s} {p['n_bz']:,} rows, unchanged")
    print()

if DRY_RUN:
    print("DRY_RUN — would load:")
    for p in to_load:
        print(f"   {p['src']:44s} {p['n_ora']:>10,} rows  [{p['action']}, {SCHEMA_POLICY}]")
elif not to_load:
    print("nothing needs loading")
else:
    for p in to_load:
        src, bname = p["src"], p["bronze"]
        print("=" * 116)
        try:
            conv = dict(NB16_SHAPE) if (SCHEMA_POLICY == "normalize" or p["action"] == "BOOTSTRAP") \
                   else p["conv"]
            print(f"{src}  [{p['action']}]  audit -> {', '.join(audit_names(conv)) or '(none)'}")
            if p["action"] == "RELOAD":
                v = spark.sql(f"DESCRIBE HISTORY {BRONZE_DB}.`{bname}` LIMIT 1").first()["version"]
                print(f"  rollback: RESTORE TABLE {BRONZE_DB}.`{bname}` TO VERSION AS OF {v}"
                      f"   -- {p['n_bz']:,} rows now")

            pull = ora(f"(SELECT * FROM {src}) q", 900)
            sub  = f"{RAW_ROOT}/{p['owner'].lower()}/{p['table'].lower()}/load_date={TODAY.isoformat()}/"
            raw_conv = dict(conv); raw_conv["ymd"] = False
            stamp(pull, raw_conv, src, p["owner"], p["n_ora"], p["ttypes"]) \
                .write.mode("overwrite").option("compression", "snappy").parquet(sub)
            n_raw = spark.read.parquet(sub).count()

            back = spark.read.parquet(sub).drop(*audit_names(raw_conv))
            out  = stamp(back, conv, src, p["owner"], p["n_ora"], p["ttypes"])
            w = out.write.format("delta").mode("overwrite")
            if p["action"] == "BOOTSTRAP" or SCHEMA_POLICY == "normalize":
                w = w.option("overwriteSchema", "true")          # new table, or a deliberate rewrite
            else:
                out = align_to(out, p["tcols"], p["ttypes"])     # must match exactly; Delta enforces
                w = out.write.format("delta").mode("overwrite")
            w.saveAsTable(f"{BRONZE_DB}.`{bname}`")

            n_bz = spark.sql(f"SELECT COUNT(*) n FROM {BRONZE_DB}.`{bname}`").first()["n"]
            ok = (p["n_ora"] == n_raw == n_bz)
            print(f"  raw    -> {sub}")
            print(f"  RECONCILE oracle={p['n_ora']:,} raw={n_raw:,} bronze={n_bz:,} "
                  f"-> {'PASS' if ok else 'FAIL'}")
            (LOADED if ok else FAILED).append({**p, "n_raw": n_raw, "n_bz_after": n_bz})
        except Exception as e:
            msg = str(e).splitlines()[0][:140]
            print(f"  ** ERROR {msg}")
            if "schema mismatch" in str(e).lower() or "AnalysisException" in str(type(e).__name__):
                print("     (preserve mode refused a schema change - that is the guard doing its job)")
            FAILED.append({**p, "error": msg})
    print("=" * 116)
    print(f"loaded {len(LOADED)}   failed {len(FAILED)}   untouched {len(SKIPPED)}")

# COMMAND ----------
# ============================== CELL 5 : REGISTER OR PROMOTE THE CONTRACT ROW ==============================
CANDIDATES = ([] if DRY_RUN else LOADED) + ([] if DRY_RUN else SKIPPED)
if DRY_RUN or not PROMOTE:
    print("skipped (DRY_RUN or PROMOTE=False) — NB16 will not see these tables")
elif not CANDIDATES:
    print("nothing to register")
else:
    print("ROLLBACK for everything this cell does:")
    for p in CANDIDATES:
        c = p["contract"]
        print(f"  DELETE FROM {CONTRACT} WHERE `table`='{p['bronze']}';" if c is None
              else f"  UPDATE {CONTRACT} SET status='{c.get('status')}' WHERE `table`='{p['bronze']}';")
    print("-" * 110)

    for p in CANDIDATES:
        bname, c = p["bronze"], p["contract"]
        if c is None:
            tmpl = spark.table(CONTRACT).where(F.col("table") == TEMPLATE_ROW).limit(1)
            if tmpl.count() == 0:
                print(f"{bname}: ** template '{TEMPLATE_ROW}' missing — register by hand **"); continue
            new = (tmpl.withColumn("table", F.lit(bname)).withColumn("source", F.lit(p["src"]))
                       .withColumn("load_strategy", F.lit("full"))
                       .withColumn("bronze_write", F.lit("CREATE OR REPLACE"))
                       .withColumn("status", F.lit("measured_v2")))
            for col, val in [("scope_col", "-"), ("pull_wm", "-"), ("pull_wm_indexed", "-"),
                             ("bronze_base_hint", "-"), ("merge_key", "-"),
                             ("dedupe_key", "-"), ("chunking", "-")]:
                if col in new.columns:
                    new = new.withColumn(col, F.lit(val))
            ev = f"registered by NB20 on {TODAY}: {p['n_ora']:,}-row full snapshot"
            for col in new.columns:
                if col.startswith("evidence"):
                    new = new.withColumn(col, F.lit(ev))
            new.write.format("delta").mode("append").saveAsTable(CONTRACT)
            print(f"{bname}: REGISTERED (full / measured_v2)")
        elif str(c.get("status")) == "measured_v2":
            print(f"{bname}: already measured_v2 — untouched")
        else:
            spark.sql(f"UPDATE {CONTRACT} SET status='measured_v2' WHERE `table`='{bname}'")
            note = "in sync, not reloaded" if p in SKIPPED else "reloaded this run"
            print(f"{bname}: PROMOTED {c.get('status')} -> measured_v2  ({note})")

    print("\ncontract rows for this run:")
    (spark.table(CONTRACT).where(F.col("table").isin([p["bronze"] for p in CANDIDATES]))
       .select("table", "source", "load_strategy", "status").show(50, truncate=False))

# COMMAND ----------
# ============================== CELL 6 : VERIFY + RUN LOG ==============================
if not (LOADED or SKIPPED):
    print("nothing to verify")
else:
    print("=" * 126)
    print(f"{'bronze table':44s} {'rows':>12s} {'files':>7s} {'KB':>10s}  audit columns kept")
    print("-" * 126)
    for p in LOADED + SKIPPED:
        d = spark.sql(f"DESCRIBE DETAIL {BRONZE_DB}.`{p['bronze']}`").first()
        bdf = spark.table(f"{BRONZE_DB}.`{p['bronze']}`")
        n = bdf.count()
        print(f"{p['bronze']:44s} {n:>12,} {d['numFiles']:>7,} "
              f"{(d['sizeInBytes'] or 0)/1024:>10,.1f}  "
              f"{','.join(sorted(c for c in bdf.columns if c.startswith('_'))) or '(none)'}")

    if SCHEMA_POLICY == "preserve" and LOADED:
        print("\nSCHEMA PARITY — each reloaded table must still carry the columns it had before")
        for p in LOADED:
            now = set(spark.table(f"{BRONZE_DB}.`{p['bronze']}`").columns)
            was = set(p["tcols"]) if p["tcols"] else now
            lost = sorted(was - now)
            print(f"  {p['bronze']:44s} {'OK' if not lost else '** LOST ' + ', '.join(lost)}")

    rows = [(BATCH_ID, _dt.datetime.utcnow().isoformat(), p["bronze"], str(DRY_RUN),
             p["action"], str(p["n_ora"]),
             json.dumps({"status": "BOOTSTRAPPED" if p["action"] != "PROMOTE_ONLY" else "PROMOTED_IN_SYNC",
                         "source": p["src"], "oracle_rows": p["n_ora"],
                         "bronze_rows": p.get("n_bz_after", p.get("n_bz")),
                         "schema_policy": SCHEMA_POLICY, "promoted": PROMOTE,
                         "decodes": DECODES}, default=str))
            for p in LOADED + SKIPPED]
    try:
        (spark.createDataFrame(rows, "batch_id string, run_ts string, table string, dry_run string, "
                                     "bronze_version_before string, rows_before string, result string")
         .write.format("delta").mode("append").saveAsTable(RUN_LOG))
        print(f"\nrun log -> {RUN_LOG} (batch {BATCH_ID})")
    except Exception as e:
        print(f"\n(run-log write skipped: {str(e).splitlines()[0][:70]})")

    if FAILED:
        print(f"\n** {len(FAILED)} FAILED — rerun with TABLES set to just these:")
        for p in FAILED:
            print(f"   {p['src']}   {p.get('error', 'reconcile mismatch')}")

    print(f"\nDONE. loaded={len(LOADED)}  promoted-in-sync={len(SKIPPED)}  failed={len(FAILED)}")
    print("Promoted tables enter NB16's run list as ordinary full snapshots from the next run.")
