# Databricks notebook source
# MAGIC %md
# MAGIC # Chicago Data Catalog + Validation Generator
# MAGIC Single source-of-truth generator for `mars_dev` **bronze / silver / gold**. Produces:
# MAGIC 1. **Schemas** - every column, type, nullability (information_schema)
# MAGIC 2. **Facts** - exact row count, column count, size, files, partitions, freshness (min/max date)
# MAGIC 3. **Samples** - 15 rows/table, **PII-masked** (caller / work-notes / names / assigned_to hashed)
# MAGIC 4. **Validations** - per-table (grain, key-nulls, freshness) + **cross-layer reconciliation**
# MAGIC
# MAGIC Persists to `mars_dev.audit.catalog_columns / catalog_facts / catalog_validation` and writes
# MAGIC markdown + CSV to `/dbfs/FileStore/chicago_catalog/` (download -> commit into `docs/`).
# MAGIC **This is the refresh engine for the Chicago data-catalog knowledge-base skill.**
# MAGIC
# MAGIC Run: attach to the shared cluster, **Run All**. Exact counts on the billion-row bronze facts
# MAGIC (device_event_history ~1.25B, abp_tap ~570M) take time - progress + per-table timing print live.

# COMMAND ----------
from datetime import datetime, timezone
import time, re
spark.sql("USE CATALOG mars_dev")

CAT      = "mars_dev"
SCHEMAS  = ["bronze", "silver", "gold"]
AUDIT    = "mars_dev.audit"
OUT_DIR  = "/dbfs/FileStore/chicago_catalog"          # download at <workspace>/files/chicago_catalog/
DBFS_DIR = "dbfs:/FileStore/chicago_catalog"
SAMPLE_N = 15
RUN_TS   = datetime.now(timezone.utc).isoformat()

# PII columns to hash in samples (case-insensitive substring match)
PII_PATTERNS = ["caller","opened_by","closed_by","assigned_to","assigned","work_note","comments",
                "short_description","u_display_value","u_ncs_device_name","sys_created_by",
                "sys_updated_by","employee","contact","email","phone","description"]

# Grain keys for grain-uniqueness checks (bronze pulled from bronze_data_contract below).
SILVER_GRAIN = {
  "device_failures": ["DEVICE_KEY","device_category","failure_date"],
  "metric_daily": ["DEVICE_KEY","transit_day"], "device_mttr": ["DEVICE_KEY","failure_date"],
  "device_survival_intervals": ["DEVICE_KEY","interval_start_date"],
  "station_network_daily": ["FACILITY_ID","device_category","transit_day"],
  "device_event_enriched": ["DW_DEVICE_EVENT_ID"], "hw_config_current": ["DEVICE_KEY","COMPONENT_SERIAL_NBR"],
  "incident_history": ["incident_number"], "incident_root_cause": ["availability_event_id"],
  "kpi_daily": None,  # events-grain by design
}
GOLD_GRAIN = {
  "device_ps1_daily": ["DEVICE_ID","transit_day"], "device_ps2_chains": ["DEVICE_ID","transit_day"],
  "device_ps3_incident": ["availability_event_id"],
  "device_ps4_hourly": ["DEVICE_ID","DEVICE_KEY","hour_bucket","transit_day"],
  "device_ps5_component": ["DEVICE_ID","COMPONENT_SERIAL_NBR"],
}

RESULTS = []   # (run_ts, layer, object, check, status, detail)
def log(layer, obj, check, status, detail=""):
    RESULTS.append((RUN_TS, layer, obj, check, status, str(detail)))
    print(f"[{status:4}] {layer:6} {obj:36} {check:26} {detail}")

def list_tables(sch):
    return [r.table_name for r in spark.sql(
        f"SELECT table_name FROM {CAT}.information_schema.tables "
        f"WHERE table_schema='{sch}' ORDER BY table_name").collect()]

def get_cols(sch, tbl):
    return spark.sql(
        f"SELECT ordinal_position, column_name, data_type, is_nullable "
        f"FROM {CAT}.information_schema.columns WHERE table_schema='{sch}' AND table_name='{tbl}' "
        f"ORDER BY ordinal_position").collect()

try:
    dbutils.fs.mkdirs(DBFS_DIR)
except Exception:
    pass
print("catalog:", spark.sql("SELECT current_catalog()").first()[0], "| out:", OUT_DIR)

# COMMAND ----------
# MAGIC %md ## Part A - Schema catalog (all layers)

# COMMAND ----------
col_rows = []
for sch in SCHEMAS:
    for t in list_tables(sch):
        for c in get_cols(sch, t):
            col_rows.append((sch, t, c.ordinal_position, c.column_name, c.data_type, c.is_nullable))
cols_df = spark.createDataFrame(col_rows, "layer string, table_name string, ordinal int, column_name string, data_type string, is_nullable string")
cols_df.write.mode("overwrite").option("overwriteSchema","true").saveAsTable(f"{AUDIT}.catalog_columns")
print(f"catalog_columns: {cols_df.count():,} columns across {cols_df.select('layer','table_name').distinct().count()} tables")
display(spark.sql(f"SELECT layer, COUNT(DISTINCT table_name) tables, COUNT(*) columns FROM {AUDIT}.catalog_columns GROUP BY layer ORDER BY layer"))

# COMMAND ----------
# MAGIC %md ## Part B - Facts: exact row count, columns, size, partitions, freshness

# COMMAND ----------
DATE_RE = re.compile(r"(transit_day$|_dtm$|event_dtm|start_dtm|hour_bucket|_ts$|load_date|failure_date|day_key$)", re.I)
fact_rows = []
for sch in SCHEMAS:
    tbls = list_tables(sch)
    print(f"\n===== {sch}: {len(tbls)} tables =====")
    for t in tbls:
        fq = f"`{CAT}`.`{sch}`.`{t}`"
        cols = get_cols(sch, t)
        ncol = len(cols)
        t0 = time.time()
        try:
            n = spark.table(f"{CAT}.{sch}.{t}").count()          # EXACT count (per PK)
        except Exception as e:
            log(sch, t, "row_count", "WARN", f"count failed: {str(e).splitlines()[0][:70]}"); n = -1
        secs = time.time() - t0
        size_mb = nfiles = None; part = ""
        try:
            d = spark.sql(f"DESCRIBE DETAIL {fq}").first()
            size_mb = round((d["sizeInBytes"] or 0)/1048576.0, 1); nfiles = d["numFiles"]
            part = ",".join(d["partitionColumns"] or [])
        except Exception:
            pass
        # freshness: min/max of best date-like column
        dcol = next((c.column_name for c in cols if DATE_RE.search(c.column_name)), None)
        mn = mx = None; stale = None
        if dcol and n > 0:
            try:
                expr = (f"MIN(TO_DATE(CAST(`{dcol}` AS STRING),'yyyyMMdd')), MAX(TO_DATE(CAST(`{dcol}` AS STRING),'yyyyMMdd'))"
                        if dcol.lower().endswith("day_key")
                        else f"MIN(CAST(`{dcol}` AS DATE)), MAX(CAST(`{dcol}` AS DATE))")
                r = spark.sql(f"SELECT {expr} FROM {fq}").first()
                mn, mx = str(r[0]), str(r[1])
                stale = None if mx is None else (spark.sql(f"SELECT datediff(current_date(), DATE'{mx}')").first()[0] > 45)
            except Exception:
                pass
        fact_rows.append((sch, t, n, ncol, size_mb, nfiles, part, dcol, mn, mx, bool(stale) if stale is not None else None, round(secs,1)))
        print(f"  {t:40} rows={n:>13,} cols={ncol:>3} size={str(size_mb)+'MB':>10} fresh={mx}  ({secs:.1f}s)")

facts_df = spark.createDataFrame(fact_rows,
    "layer string, table_name string, row_count long, col_count int, size_mb double, num_files int, "
    "partition_cols string, date_col string, min_date string, max_date string, is_stale boolean, count_secs double")
facts_df.write.mode("overwrite").option("overwriteSchema","true").saveAsTable(f"{AUDIT}.catalog_facts")
display(spark.sql(f"SELECT layer, COUNT(*) tables, SUM(row_count) total_rows, ROUND(SUM(size_mb)/1024,1) total_gb FROM {AUDIT}.catalog_facts WHERE row_count>=0 GROUP BY layer ORDER BY layer"))
print("\nStale tables (max date > 45d old):")
display(spark.sql(f"SELECT layer, table_name, max_date FROM {AUDIT}.catalog_facts WHERE is_stale = TRUE ORDER BY layer, table_name"))

# COMMAND ----------
# MAGIC %md ## Part C - PII-masked samples (15 rows/table)

# COMMAND ----------
def masked_select(sch, tbl):
    names = [c.column_name for c in get_cols(sch, tbl)]
    exprs = []
    for nm in names:
        if any(p in nm.lower() for p in PII_PATTERNS):
            exprs.append(f"CASE WHEN `{nm}` IS NULL THEN NULL ELSE concat('MASK:', substr(sha2(CAST(`{nm}` AS STRING),256),1,8)) END AS `{nm}`")
        else:
            exprs.append(f"`{nm}`")
    return "SELECT " + ", ".join(exprs) + f" FROM `{CAT}`.`{sch}`.`{tbl}` LIMIT {SAMPLE_N}"

sample_md = {sch: [f"# Chicago sample data - {sch} (PII-masked)\n_Generated {RUN_TS}. PII columns shown as MASK:<hash>._\n"] for sch in SCHEMAS}
for sch in SCHEMAS:
    for t in list_tables(sch):
        try:
            sdf = spark.sql(masked_select(sch, t))
            pdf = sdf.limit(SAMPLE_N).toPandas()
            sample_md[sch].append(f"\n## {sch}.{t}\n\n" + (pdf.head(SAMPLE_N).to_markdown(index=False) if len(pdf) else "_(0 rows)_") + "\n")
        except Exception as e:
            sample_md[sch].append(f"\n## {sch}.{t}\n_sample failed: {str(e).splitlines()[0][:80]}_\n")
print("sample markdown built for", {k: len(v)-1 for k,v in sample_md.items()}, "tables")
display(spark.sql(masked_select("gold","device_ps1_daily")))   # spot-check one

# COMMAND ----------
# MAGIC %md ## Part D - Validations: per-table (grain / nulls / freshness) + cross-layer reconciliation

# COMMAND ----------
# bronze grain/PK from the data contract if present
bronze_grain = {}
try:
    for r in spark.sql(f"SELECT table_name, primary_key FROM {AUDIT}.bronze_data_contract WHERE primary_key IS NOT NULL").collect():
        bronze_grain[r.table_name] = [c.strip() for c in re.split(r"[,;]", r.primary_key) if c.strip()]
    print("bronze grain keys from contract:", len(bronze_grain))
except Exception as e:
    print("bronze_data_contract not read:", str(e).splitlines()[0][:80])

def grain_for(sch, t):
    return bronze_grain.get(t) if sch=="bronze" else (SILVER_GRAIN.get(t) if sch=="silver" else GOLD_GRAIN.get(t))

for sch in SCHEMAS:
    for t in list_tables(sch):
        fq = f"`{CAT}`.`{sch}`.`{t}`"
        rc = spark.sql(f"SELECT row_count FROM {AUDIT}.catalog_facts WHERE layer='{sch}' AND table_name='{t}'").first()
        n = rc["row_count"] if rc else -1
        log(sch, t, "row_count", "PASS" if n>0 else ("WARN" if n==0 else "FAIL"), f"{n:,} rows")
        g = grain_for(sch, t)
        if g:
            try:
                cols = {c.column_name.lower() for c in get_cols(sch, t)}
                if all(k.lower() in cols for k in g):
                    dup = spark.sql(f"SELECT COALESCE(SUM(c-1),0) d FROM (SELECT count(*) c FROM {fq} GROUP BY {', '.join('`'+k+'`' for k in g) } HAVING count(*)>1)").first()["d"]
                    log(sch, t, "grain_unique", "PASS" if dup==0 else "FAIL", f"{dup:,} dup rows on ({', '.join(g)})")
                else:
                    log(sch, t, "grain_unique", "WARN", f"grain cols not all present: {g}")
            except Exception as e:
                log(sch, t, "grain_unique", "WARN", str(e).splitlines()[0][:70])

# COMMAND ----------
# Cross-layer reconciliation + key model metrics (INFO/PASS/WARN)
RECON = [
 ("dim_device.category_dist",
  "SELECT concat_ws(' ', collect_list(concat(mars_device_category,'=',cnt))) v FROM "
  "(SELECT mars_device_category, count(*) cnt FROM mars_dev.silver.dim_device WHERE is_current GROUP BY 1)", "INFO"),
 ("device_failures.devices_by_cat",
  "SELECT concat_ws(' ', collect_list(concat(device_category,'=',d))) v FROM "
  "(SELECT device_category, count(distinct DEVICE_KEY) d FROM mars_dev.silver.device_failures GROUP BY 1)", "INFO"),
 ("ps1.label_positive_rate_3d",
  "SELECT concat(round(100.0*avg(CASE WHEN will_fail_3d=1 THEN 1 ELSE 0 END),3),'%') v FROM mars_dev.gold.device_ps1_daily", "INFO"),
 ("ps1.avm_ghosts_in_spine",
  "SELECT count(*) v FROM mars_dev.gold.device_ps1_daily WHERE DEVICE_ID LIKE 'AVM%'", "WARN0"),
 ("ps3.rows_and_levels",
  "SELECT concat('rows=',count(*),' lvl2=',sum(CASE WHEN failure_level=2 THEN 1 ELSE 0 END)) v FROM mars_dev.gold.device_ps3_incident", "INFO"),
 ("ps3.kpi_rule_id_fill",
  "SELECT concat(round(100.0*count(kpi_rule_id)/greatest(count(*),1),1),'%') v FROM mars_dev.gold.device_ps3_incident", "INFO"),
 ("ps4.ensemble_rate",
  "SELECT concat(round(100.0*avg(ensemble_anomaly_flag),2),'%') v FROM mars_dev.gold.device_ps4_hourly", "INFO"),
 ("ps5.censoring_rate",
  "SELECT concat(round(100.0*avg(CASE WHEN is_censored THEN 1 ELSE 0 END),1),'%') v FROM mars_dev.gold.device_ps5_component", "INFO"),
 ("silver.device_event_enriched_vs_bronze_device_event",
  "SELECT concat('silver=',(SELECT count(*) FROM mars_dev.silver.device_event_enriched),' bronze=',(SELECT count(*) FROM mars_dev.bronze.edw_device_event)) v", "INFO"),
]
for name, sql, mode in RECON:
    try:
        v = spark.sql(sql).first()["v"]
        status = "INFO"
        if mode == "WARN0":
            status = "PASS" if str(v) in ("0","0.0") else "WARN"
        log("recon", name, "metric", status, v)
    except Exception as e:
        log("recon", name, "metric", "WARN", str(e).splitlines()[0][:80])

val_df = spark.createDataFrame(RESULTS, "run_ts string, layer string, object string, check string, status string, detail string")
val_df.write.mode("overwrite").option("overwriteSchema","true").saveAsTable(f"{AUDIT}.catalog_validation")
print("\nValidation summary:")
display(spark.sql(f"SELECT status, COUNT(*) n FROM {AUDIT}.catalog_validation GROUP BY status ORDER BY status"))
print("FAILs (if any):")
display(spark.sql(f"SELECT layer, object, check, detail FROM {AUDIT}.catalog_validation WHERE status='FAIL' ORDER BY layer, object"))

# COMMAND ----------
# MAGIC %md ## Part E - Write knowledge-base files (commit into docs/)

# COMMAND ----------
# UC-safe publish: this workspace disables the public DBFS root AND the /dbfs FUSE mount,
# so we (1) ALWAYS persist the docs to a UC table, and (2) OPTIONALLY export files to a
# Unity Catalog Volume if you set VOLUME_DIR. Nothing here can fail the run.
from pyspark.sql import Row

VOLUME_DIR = None   # optional: e.g. "/Volumes/mars_dev/audit/catalog_files" if you have a writable UC Volume

def _md(df):
    try:
        return df.to_markdown(index=False)          # needs 'tabulate'
    except Exception:
        return df.to_string(index=False)

def build_catalog_md(sch):
    facts = {r.table_name: r for r in spark.sql(f"SELECT * FROM {AUDIT}.catalog_facts WHERE layer='{sch}'").collect()}
    lines = [f"# Chicago Data Catalog - {sch.upper()}", f"_Generated {RUN_TS} from mars_dev._\n"]
    for t in list_tables(sch):
        f_ = facts.get(t)
        rc = f"{f_.row_count:,}" if f_ and f_.row_count is not None else "?"
        meta = (f"rows **{rc}** · cols {f_.col_count} · {f_.size_mb} MB · part [{f_.partition_cols}] · fresh {f_.max_date}"
                if f_ else "")
        lines.append(f"\n### `{sch}.{t}`\n{meta}\n")
        lines.append("| # | column | type | null |\n|---|---|---|---|")
        for c in get_cols(sch, t):
            lines.append(f"| {c.ordinal_position} | {c.column_name} | {c.data_type} | {c.is_nullable} |")
        lines.append("")
    return "\n".join(lines)

# build every doc in memory
docs = {}
for sch in SCHEMAS:
    docs[f"chicago_catalog_{sch}.md"] = build_catalog_md(sch)
    docs[f"chicago_samples_{sch}.md"] = "\n".join(sample_md[sch])
docs["chicago_catalog_facts.csv"] = spark.sql(
    f"SELECT * FROM {AUDIT}.catalog_facts ORDER BY layer, table_name").toPandas().to_csv(index=False)
_sc = spark.sql(f"SELECT layer, object, check, status, detail FROM {AUDIT}.catalog_validation "
                f"ORDER BY (status='FAIL') DESC, layer, object").toPandas()
docs["chicago_validation_scorecard.md"] = f"# Chicago Validation Scorecard\n_Generated {RUN_TS}._\n\n" + _md(_sc)

# (1) ALWAYS persist -> UC table (query/export anytime; never blocked on this workspace)
spark.createDataFrame([Row(doc_name=k, content=v, gen_ts=RUN_TS) for k, v in docs.items()]) \
     .write.mode("overwrite").option("overwriteSchema", "true").saveAsTable(f"{AUDIT}.catalog_docs")
print(f"Persisted {len(docs)} docs -> {AUDIT}.catalog_docs")
print(f"  retrieve one:  SELECT content FROM {AUDIT}.catalog_docs WHERE doc_name='chicago_catalog_gold.md'")

# (2) OPTIONAL file export to a UC Volume (set VOLUME_DIR above). Safe/skipped if unavailable.
if VOLUME_DIR:
    try:
        import os
        os.makedirs(VOLUME_DIR, exist_ok=True)
        for k, v in docs.items():
            with open(f"{VOLUME_DIR}/{k}", "w") as fh:
                fh.write(v)
        print("Wrote files ->", VOLUME_DIR)
    except Exception as e:
        print("Volume export skipped:", str(e).splitlines()[0][:120])
else:
    print("VOLUME_DIR not set -> file export skipped. Get the docs from audit.catalog_docs, "
          "or copy the ONE-SHOT cell output below.")

# COMMAND ----------
# MAGIC %md
# MAGIC ## ONE-SHOT consolidated output
# MAGIC The single cell below prints the **entire catalog** (summary + facts + full schemas + validation
# MAGIC scorecard) as one copy-pasteable block. Run it last; copy the whole output to share/archive.
# MAGIC (Row-level samples stay in the written `chicago_samples_*.md` files - too large for one print.)

# COMMAND ----------
from collections import defaultdict
_facts = spark.sql(f"SELECT * FROM {AUDIT}.catalog_facts ORDER BY layer, table_name").collect()
_cols  = spark.sql(f"SELECT layer, table_name, ordinal, column_name, data_type, is_nullable "
                   f"FROM {AUDIT}.catalog_columns ORDER BY layer, table_name, ordinal").collect()
_val   = spark.sql(f"SELECT layer, object, check, status, detail FROM {AUDIT}.catalog_validation "
                   f"ORDER BY (status='FAIL') DESC, (status='WARN') DESC, layer, object").collect()
_cols_by = defaultdict(list)
for _c in _cols:
    _cols_by[(_c.layer, _c.table_name)].append(_c)

def _num(x):
    try: return f"{int(x):,}"
    except Exception: return str(x)

L = ["#" * 78,
     "########## CHICAGO DATA CATALOG - ONE-SHOT (copy this entire output) ##########",
     "#" * 78,
     f"generated_utc={RUN_TS}   catalog=mars_dev"]

L.append("\n===== SUMMARY  (layer | tables | columns | total_rows | total_MB) =====")
for r in spark.sql(f"SELECT layer, COUNT(*) t, SUM(col_count) c, SUM(row_count) rw, ROUND(SUM(size_mb),1) mb "
                   f"FROM {AUDIT}.catalog_facts GROUP BY layer ORDER BY layer").collect():
    L.append(f"{r.layer:7} tables={r.t:<4} columns={r.c:<6} rows={_num(r.rw):>16} size={r.mb} MB")

L.append("\n===== FACTS  (layer.table | rows | cols | MB | files | [partitions] | date_col | min..max | stale) =====")
for f in _facts:
    L.append(f"{f.layer}.{f.table_name} | {_num(f.row_count)} | {f.col_count} | {f.size_mb} | {f.num_files} | "
             f"[{f.partition_cols}] | {f.date_col} | {f.min_date}..{f.max_date} | stale={f.is_stale}")

L.append("\n===== SCHEMAS  (name|type|nullable per table) =====")
for f in _facts:
    cc = _cols_by.get((f.layer, f.table_name), [])
    L.append(f"\n## {f.layer}.{f.table_name}  ({len(cc)} cols)")
    for c in cc:
        L.append(f"{c.column_name}|{c.data_type}|{c.is_nullable}")

L.append("\n===== VALIDATION SCORECARD  (FAIL/WARN first) =====")
_counts = defaultdict(int)
for v in _val:
    _counts[v.status] += 1
L.append("counts: " + "  ".join(f"{k}={_counts[k]}" for k in sorted(_counts)))
for v in _val:
    L.append(f"[{v.status:4}] {v.layer}.{v.object} :: {v.check} :: {v.detail}")

L.append("\n" + "#" * 30 + " END ONE-SHOT " + "#" * 30)
print("\n".join(L))

# COMMAND ----------
# MAGIC %md
# MAGIC ## Refresh SOP (knowledge-base skill)
# MAGIC 1. Run this notebook (Run All) after any silver/gold rebuild.
# MAGIC 2. It refreshes `audit.catalog_columns / catalog_facts / catalog_validation` (query anytime).
# MAGIC 3. Download `/FileStore/chicago_catalog/*` and commit into `docs/` (paths printed in Part E).
# MAGIC 4. Re-package the `chicago-data-catalog` skill from the refreshed docs.
# MAGIC
# MAGIC **Live queries:**
# MAGIC ```sql
# MAGIC SELECT * FROM mars_dev.audit.catalog_facts      WHERE layer='gold' ORDER BY row_count DESC;
# MAGIC SELECT * FROM mars_dev.audit.catalog_validation WHERE status IN ('FAIL','WARN');
# MAGIC SELECT * FROM mars_dev.audit.catalog_columns    WHERE table_name='device_ps1_daily';
# MAGIC ```
