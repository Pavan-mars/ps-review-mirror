# Databricks notebook source
# MAGIC %md
# MAGIC # 12 — INCREMENTAL LOAD ENGINE v2  (contract-driven · raw → bronze · DRY_RUN by default)
# MAGIC
# MAGIC Loads the 12-Apr-2026→today increment for every `status='measured_v2'` row in
# MAGIC `mars_dev.audit.bronze_data_contract` (written by NB10). Per strategy:
# MAGIC
# MAGIC | strategy | pull | raw | bronze |
# MAGIC |---|---|---|---|
# MAGIC | `full` / `full_scoped` | whole table (or scoped) in one query | `.../table/load_date=<today>/` overwrite | `CREATE OR REPLACE` |
# MAGIC | `cdc_merge` | `pull_wm > bronze max(pull_wm)`, one query per month, **indexed wm from probe 04** | append under `year=/month=/day=` of `scope_col` | `MERGE` on the Oracle PK, target pruned |
# MAGIC | `cdc_append` | same monthly pull | same | `APPEND` (+ anti-join dedupe on `dedupe_key` when set) |
# MAGIC | `append_aggregate` | — delegated: run `notebooks/ingestion/ingest_use_txn_daily.py` (proven NB90 pattern) **after NB11 fixes its raw prefix** | — | — |
# MAGIC | `drop_from_scope` | skipped (cta_kpi_tvm_date_table: all rows are Jan-2018) | — | — |
# MAGIC
# MAGIC **Run order:** NB10 (contract) → NB11 (use_txn raw fix) → this, `DRY_RUN=True` → review → `DRY_RUN=False`.
# MAGIC Heavy scan table `ncs_stage_device_end_of_day_msg_count` (`chunking=single_pass_offhours`) only runs
# MAGIC when `INCLUDE_OFFHOURS=True` — leave False during business hours.
# MAGIC Every run appends per-table results to `mars_dev.audit.incremental_run_log`. Reversible: raw is
# MAGIC append-only; every bronze table can `RESTORE TABLE ... TO VERSION AS OF <pre-run version>` (versions
# MAGIC are printed before any write).

# COMMAND ----------
# ============================== CELL 1 : CONFIG + HELPERS ==============================
import datetime as _dt, time, json
from pyspark.sql import functions as F

DRY_RUN          = True
INCLUDE_OFFHOURS = False
ONLY             = []            # [] = all measured_v2; or ["edw_device_event"] to target
CAT, BRONZE_DB   = "mars_dev", "mars_dev.bronze"
RAW_ROOT         = "s3://cubic-mars-pm-s3-datalake-dev-raw-170202974600/chicago_ventra"
CONTRACT         = "mars_dev.audit.bronze_data_contract"
RUN_LOG          = "mars_dev.audit.incremental_run_log"
ODS_HOST, ODS_PORT, SCOPE_SECRET = "10.3.10.30", 1521, "cubic"
SDU              = 512
QUERY_TO, READ_TO = 1800, "2100000"     # patient by design — ORA-01013 in probes was our own timeout
FLOOR            = "2026-04-11 00:00:00"   # fallback watermark base (frozen ODS extract)
SCOPE_FLOOR      = "2023-07-01"            # 2024plus scope floor incl. NB88 extension — target pruning
SENTINEL         = (_dt.date.today() + _dt.timedelta(days=1)).strftime("%Y-%m-%d")
BATCH_ID         = _dt.datetime.now().strftime("%Y%m%d_%H%M%S")

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

def month_edges(start_iso, end_iso):
    out, d = [], _dt.date.fromisoformat(start_iso[:10])
    end = _dt.date.fromisoformat(end_iso[:10])
    while d < end:
        nxt = (d.replace(day=1) + _dt.timedelta(days=32)).replace(day=1)
        out.append((d.isoformat(), min(nxt, end).isoformat())); d = min(nxt, end)
    return out

def _col(df, n): return {c.lower(): c for c in df.columns}.get(n.lower()) if n and n != "-" else None

def audit_and_ymd(src, scope_col, owner_table):
    """Add pipeline audit cols + year/month/day partitions derived from scope_col."""
    sc = _col(src, scope_col)
    out = (src.withColumn("_ingest_ts", F.current_timestamp())
              .withColumn("_batch_id", F.lit(BATCH_ID))
              .withColumn("_source_system", F.lit("chicago_oracle_ods"))
              .withColumn("_source_table", F.lit(owner_table)))
    if sc and dict(src.dtypes).get(sc, "") in ("timestamp", "date"):
        return (out.withColumn("year", F.year(sc)).withColumn("month", F.month(sc))
                   .withColumn("day", F.dayofmonth(sc)))
    if sc:  # day-key int YYYYMMDD
        k = F.col(sc).cast("long")
        return (out.withColumn("year", (k / 10000).cast("int"))
                   .withColumn("month", ((k % 10000) / 100).cast("int"))
                   .withColumn("day", (k % 100).cast("int")))
    d = F.current_date()
    return (out.withColumn("year", F.year(d)).withColumn("month", F.month(d)).withColumn("day", F.dayofmonth(d)))

def align_to(df, target_cols):
    """Project df onto the target column list (case-insensitive), NULL-filling gaps."""
    have = {c.lower(): c for c in df.columns}
    return df.select(*[F.col(have[c.lower()]).alias(c) if c.lower() in have else F.lit(None).alias(c)
                       for c in target_cols])

CON = [r.asDict() for r in spark.table(CONTRACT).where("status = 'measured_v2'").collect()]
if ONLY: CON = [c for c in CON if c["table"] in ONLY]
assert CON, "No measured_v2 rows — run NB10 first."
ora("(SELECT 1 OK FROM dual) q", 20).collect()
print(f"NB12 v2 | DRY_RUN={DRY_RUN} | {len(CON)} contract rows | batch={BATCH_ID} | sentinel={SENTINEL}")

# COMMAND ----------
# ============================== CELL 2 : PRE-RUN BRONZE VERSIONS (the rollback map) ==============================
VERSIONS = {}
for c in CON:
    t = c["table"]
    try:
        v = spark.sql(f"DESCRIBE HISTORY {BRONZE_DB}.`{t}` LIMIT 1").first()["version"]
        n = spark.sql(f"SELECT COUNT(*) n FROM {BRONZE_DB}.`{t}`").first()["n"]
        VERSIONS[t] = {"version": v, "rows_before": n}
        print(f"{t:42s} v{v}  rows={n:,}   rollback: RESTORE TABLE {BRONZE_DB}.`{t}` TO VERSION AS OF {v}")
    except Exception as e:
        VERSIONS[t] = {"version": None, "rows_before": None}
        print(f"{t:42s} (no bronze table yet: {str(e).splitlines()[0][:50]})")

# COMMAND ----------
# ============================== CELL 3 : THE ENGINE ==============================
RES = {}
def log(t, **kw):
    RES[t] = kw; print(f"  => {kw}")

for c in sorted(CON, key=lambda x: x["load_strategy"]):
    t, strat = c["table"], c["load_strategy"]
    src = c["source"] if "." in str(c.get("source", "")) else None
    owner, otab = (src.split(".", 1) if src else (None, None))
    scope_col, pull_wm = c.get("scope_col"), c.get("pull_wm")
    mk, dk = c.get("merge_key"), c.get("dedupe_key")
    raw_dir = f"{RAW_ROOT}/{owner.lower()}/{otab.lower()}/" if owner else None
    print(f"\n### {t}  [{strat}]  src={src}")
    try:
        # ---------------- skip / delegate ----------------
        if strat == "drop_from_scope":
            log(t, status="SKIPPED - retired (all rows Jan-2018)"); continue
        if strat == "append_aggregate":
            log(t, status="DELEGATED", note="run notebooks/ingestion/ingest_use_txn_daily.py "
                f"with start_ym=202605 end_ym={SENTINEL[:7].replace('-','')} mode=append — AFTER NB11 raw fix")
            continue
        if c.get("chunking") == "single_pass_offhours" and not INCLUDE_OFFHOURS:
            log(t, status="DEFERRED - set INCLUDE_OFFHOURS=True and run outside business hours"); continue

        bdf = spark.table(f"{BRONZE_DB}.`{t}`")

        # ---------------- FULL / FULL_SCOPED ----------------
        if strat in ("full", "full_scoped"):
            where = ""
            if strat == "full_scoped" and _col(bdf, scope_col):
                where = f" WHERE {scope_col} >= DATE '{SCOPE_FLOOR}' AND {scope_col} < DATE '{SENTINEL}'"
            n_ora = int(ora(f"(SELECT COUNT(*) N FROM {owner}.{otab}{where}) q", 600).collect()[0]["N"])
            if DRY_RUN:
                log(t, status="WOULD full re-pull", oracle_rows=n_ora); continue
            pull = ora(f"(SELECT * FROM {owner}.{otab}{where}) q")
            sub = f"{raw_dir}load_date={_dt.date.today().isoformat()}/"
            audit_and_ymd(pull, scope_col, src).drop("year", "month", "day") \
                .write.mode("overwrite").option("compression", "snappy").parquet(sub)
            raw_back = spark.read.parquet(sub)
            (audit_and_ymd(raw_back.drop("_ingest_ts", "_batch_id", "_source_system", "_source_table"), scope_col, src)
             .write.format("delta").mode("overwrite").option("overwriteSchema", "true")
             .saveAsTable(f"{BRONZE_DB}.`{t}`"))
            n_b = spark.sql(f"SELECT COUNT(*) n FROM {BRONZE_DB}.`{t}`").first()["n"]
            log(t, status="FULL-RELOADED", oracle_rows=n_ora, bronze_rows=n_b,
                reconciled=(n_ora == spark.read.parquet(sub).count() == n_b)); continue

        # ---------------- CDC (merge / append) ----------------
        wc = _col(bdf, pull_wm)
        daykey = wc is not None and dict(bdf.dtypes).get(wc, "") not in ("timestamp", "date")
        base = None
        if wc:
            base = bdf.where(F.col(wc) < (int(SENTINEL.replace("-", "")) if daykey else F.lit(SENTINEL))) \
                      .agg(F.max(wc)).first()[0]
        if base is None:
            base_iso = FLOOR[:10]
        elif daykey:
            s = str(int(base)); base_iso = f"{s[:4]}-{s[4:6]}-{s[6:8]}"
        else:
            base_iso = str(base)[:10]
        chunks = month_edges(base_iso, SENTINEL)
        print(f"  base {pull_wm} = {base}  -> {len(chunks)} monthly chunks from {base_iso}")

        total_new, per_chunk = 0, {}
        for lo, hi in chunks:
            if daykey:
                pred = f"{pull_wm} > {int(str(base)[:8]) if base is not None else int(lo.replace('-',''))} " \
                       f"AND {pull_wm} >= {int(lo.replace('-',''))} AND {pull_wm} < {int(hi.replace('-',''))}"
            else:
                b = str(base)[:19] if base is not None else f"{FLOOR}"
                pred = f"{pull_wm} > TIMESTAMP '{b}' AND {pull_wm} >= TIMESTAMP '{lo} 00:00:00' " \
                       f"AND {pull_wm} < TIMESTAMP '{hi} 00:00:00'"
            if DRY_RUN:
                try:
                    n = int(ora(f"(SELECT COUNT(*) N FROM {owner}.{otab} WHERE {pred}) q").collect()[0]["N"])
                    per_chunk[lo[:7]] = n; total_new += n
                    print(f"    {lo[:7]}: would pull {n:,}")
                except Exception as e:
                    per_chunk[lo[:7]] = f"count-unknown ({str(e).splitlines()[0][:40]})"
                    print(f"    {lo[:7]}: count timed out — the PULL still works chunked; proceeding is safe")
                continue
            pull = ora(f"(SELECT * FROM {owner}.{otab} WHERE {pred}) q").cache()
            n = pull.count()
            per_chunk[lo[:7]] = n; total_new += n
            if n == 0:
                pull.unpersist(); continue
            staged = audit_and_ymd(align_to(pull, [x for x in bdf.columns if not x.startswith("_")
                                                   and x.lower() not in ("year", "month", "day")]), scope_col, src)
            staged.write.mode("append").partitionBy("year", "month", "day").parquet(raw_dir)   # raw lineage
            target_cols = bdf.columns
            staged_b = align_to(staged, target_cols)
            if strat == "cdc_merge" and mk and mk != "-":
                staged_b.createOrReplaceTempView("_stg")
                on = " AND ".join(f"t.`{k.strip()}` = s.`{k.strip()}`" for k in mk.split(","))
                prune = ""
                sc2 = _col(bdf, scope_col)
                if sc2:
                    prune = (f" AND t.`{sc2}` >= {int(SCOPE_FLOOR.replace('-', ''))}" if daykey and sc2 == wc else
                             f" AND t.`{sc2}` >= '{SCOPE_FLOOR}'" if dict(bdf.dtypes).get(sc2) in ("timestamp", "date") else "")
                spark.sql(f"MERGE INTO {BRONZE_DB}.`{t}` t USING _stg s ON {on}{prune} "
                          f"WHEN MATCHED THEN UPDATE SET * WHEN NOT MATCHED THEN INSERT *")
            elif dk and dk != "-" and _col(bdf, dk):
                key = _col(bdf, dk)
                existing_keys = bdf.where(F.col(wc) > F.lit(str(base)[:19]) if not daykey else F.col(wc) >= int(lo.replace("-", ""))) \
                                   .select(key).distinct()
                staged_b.join(existing_keys, on=key, how="left_anti") \
                        .write.format("delta").mode("append").saveAsTable(f"{BRONZE_DB}.`{t}`")
            else:
                staged_b.write.format("delta").mode("append").saveAsTable(f"{BRONZE_DB}.`{t}`")
            pull.unpersist()
            print(f"    {lo[:7]}: pulled {n:,} -> raw + bronze ({'merge' if strat=='cdc_merge' else 'append'})")
        n_after = None if DRY_RUN else spark.sql(f"SELECT COUNT(*) n FROM {BRONZE_DB}.`{t}`").first()["n"]
        log(t, status=("WOULD load" if DRY_RUN else "LOADED"), base=str(base), new_rows=total_new,
            per_chunk=per_chunk, bronze_after=n_after)
    except Exception as e:
        log(t, status=f"ERROR: {str(e).splitlines()[0][:90]}")

# COMMAND ----------
# ============================== CELL 4 : RUN LOG + SUMMARY ==============================
rows = [(BATCH_ID, _dt.datetime.utcnow().isoformat(), t, str(DRY_RUN),
         str(VERSIONS.get(t, {}).get("version")), str(VERSIONS.get(t, {}).get("rows_before")),
         json.dumps(RES.get(t, {}), default=str)) for t in RES]
(spark.createDataFrame(rows, "batch_id string, run_ts string, table string, dry_run string, "
                             "bronze_version_before string, rows_before string, result string")
 .write.format("delta").mode("append").saveAsTable(RUN_LOG))
print(f"run log -> {RUN_LOG} (batch {BATCH_ID})\n")
print("=" * 100)
for t in sorted(RES):
    print(f"  {t:42s} {RES[t].get('status')}  new={RES[t].get('new_rows', '-')}")
print("=" * 100)
print("NEXT after a clean APPLY: run_layer_silver.py (L1->L6; fix S17 dedup first), then run_layer_gold.py,")
print("then notebooks/catalog/generate_data_catalog.py. PS3/PS5 outputs stay labelled per D5.")
