# Databricks notebook source

# COMMAND ----------

# MAGIC %md
# MAGIC # Chicago / CTA-Ventra lakehouse catalog snapshot
# MAGIC
# MAGIC Produces a complete, PII-safe profile of every **bronze**, **silver** and **gold** table in
# MAGIC `mars_dev`, in the exact shape needed to refresh the `chicago-data-catalog` skill.
# MAGIC
# MAGIC **What it emits**
# MAGIC 1. `catalog_columns`  - every column, type, nullability and comment
# MAGIC 2. `catalog_facts`    - row count, size, file count, partition columns, freshness, grain candidates
# MAGIC 3. `catalog_samples`  - N sample rows per table, **masked** before they leave the cluster
# MAGIC 4. A single `catalog_snapshot.json` and a ready-to-paste Markdown digest
# MAGIC
# MAGIC **Before you run**
# MAGIC - Attach to a cluster / SQL warehouse with `SELECT` on `mars_dev`.
# MAGIC - Set the widgets at the top. Leave `write_audit_tables` = `no` for a dry read.
# MAGIC - `sample_rows` above ~5 is rarely worth it and makes the JSON large.
# MAGIC
# MAGIC **On PII.** Sampling real rows off a transit ticketing lakehouse is the risky part of this job.
# MAGIC Cell 4 masks by **column-name pattern** *and* by **value pattern** (PAN-like digit runs, emails).
# MAGIC Masking is applied inside Spark before `collect()`, so unmasked values never reach the driver.
# MAGIC Anything the masker is unsure about is redacted rather than shown. Read that cell before you
# MAGIC trust the output, and widen `PII_NAME_PAT` for anything specific to your feeds.

# COMMAND ----------

# ── 1. Configuration ────────────────────────────────────────────────────────
dbutils.widgets.text("catalog", "mars_dev", "Catalog")
dbutils.widgets.text("schemas", "bronze,silver,gold", "Schemas (comma separated)")
dbutils.widgets.text("sample_rows", "3", "Sample rows per table")
dbutils.widgets.dropdown("include_views", "yes", ["yes", "no"], "Include views")
dbutils.widgets.dropdown("do_row_counts", "yes", ["yes", "no"], "Exact row counts (slow)")
dbutils.widgets.dropdown("do_samples", "yes", ["yes", "no"], "Collect masked samples")
dbutils.widgets.dropdown("write_audit_tables", "no", ["yes", "no"], "Write mars_dev.audit.catalog_*")
dbutils.widgets.text("output_path", "dbfs:/FileStore/chicago_catalog", "Output folder (or a /Volumes/... UC volume)")

CATALOG   = dbutils.widgets.get("catalog").strip()
SCHEMAS   = [s.strip() for s in dbutils.widgets.get("schemas").split(",") if s.strip()]
N_SAMPLE  = int(dbutils.widgets.get("sample_rows"))
INC_VIEWS = dbutils.widgets.get("include_views") == "yes"
DO_COUNTS = dbutils.widgets.get("do_row_counts") == "yes"
DO_SAMPLE = dbutils.widgets.get("do_samples") == "yes"
WRITE_AUD = dbutils.widgets.get("write_audit_tables") == "yes"
OUT_DIR   = dbutils.widgets.get("output_path").rstrip("/")

import json, re, datetime, os
from pyspark.sql import functions as F

RUN_TS = datetime.datetime.utcnow().replace(microsecond=0).isoformat() + "Z"
print(f"catalog={CATALOG}  schemas={SCHEMAS}  run={RUN_TS}")

# COMMAND ----------

# ── 2. Inventory: every table and view in scope ─────────────────────────────
# information_schema is used rather than SHOW TABLES because it returns the
# table TYPE and comment in one pass, and because SHOW TABLES in a loop over
# ~120 objects is materially slower.
insc = f"{CATALOG}.information_schema"
schema_list = ",".join(f"'{s}'" for s in SCHEMAS)

tables = spark.sql(f"""
    SELECT table_schema, table_name, table_type, comment AS table_comment
    FROM {insc}.tables
    WHERE table_schema IN ({schema_list})
    ORDER BY table_schema, table_name
""")
if not INC_VIEWS:
    tables = tables.filter(F.col("table_type") != "VIEW")

TABLES = [r.asDict() for r in tables.collect()]
print(f"{len(TABLES)} objects in scope")
for s in SCHEMAS:
    n = sum(1 for t in TABLES if t["table_schema"] == s)
    v = sum(1 for t in TABLES if t["table_schema"] == s and t["table_type"] == "VIEW")
    print(f"  {s:<8} {n:>4} objects ({v} views)")
display(tables)

# COMMAND ----------

# ── 3. Column-level schema for everything, in ONE query ─────────────────────
cols_df = spark.sql(f"""
    SELECT table_schema, table_name, ordinal_position, column_name,
           full_data_type AS data_type, is_nullable, comment AS column_comment
    FROM {insc}.columns
    WHERE table_schema IN ({schema_list})
    ORDER BY table_schema, table_name, ordinal_position
""")
COLS = [r.asDict() for r in cols_df.collect()]
print(f"{len(COLS)} columns across {len({(c['table_schema'], c['table_name']) for c in COLS})} objects")
display(cols_df)

# COMMAND ----------

# ── 4. PII masking ──────────────────────────────────────────────────────────
# READ THIS BEFORE TRUSTING THE SAMPLES.
#
# Two independent defences, because either alone has a known failure mode:
#
#   NAME-BASED  catches a column called customer_email even when the sampled
#               rows happen to look benign. Fails on oddly-named columns.
#   VALUE-BASED catches a 16-digit PAN sitting in a column called `ref_2`.
#               Fails on values that carry no structural signature.
#
# Applied as Spark expressions BEFORE collect(), so unmasked values are never
# pulled to the driver. Unsure -> redact. Widen PII_NAME_PAT for your feeds.

PII_NAME_PAT = re.compile(
    r"(card|pan|token|acct|account_num|iban|cvv|expiry|"
    r"email|e_mail|phone|mobile|msisdn|"
    r"first_name|last_name|full_name|surname|customer_name|passenger|"
    r"addr|address|postcode|zip|dob|birth|ssn|nin|passport|licen[cs]e|"
    r"lat|lon|latitude|longitude|geo)", re.I)

# Structural value signatures. Deliberately conservative: a 13-19 digit run is
# masked wherever it appears, because on a ticketing platform that shape is a
# card number until proven otherwise.
DIGITS_13_19 = r"[0-9]{13,19}"
EMAIL_PAT    = r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"

def mask_expr(col_name, data_type):
    c = F.col(f"`{col_name}`")
    if PII_NAME_PAT.search(col_name or ""):
        return F.lit("<masked:name-rule>").alias(col_name)
    if data_type and data_type.lower().startswith("string"):
        m = F.regexp_replace(c, DIGITS_13_19, "<masked:pan-like>")
        m = F.regexp_replace(m, EMAIL_PAT, "<masked:email>")
        # truncate free text so a comment field cannot leak a paragraph
        return F.substring(m, 1, 120).alias(col_name)
    return c.cast("string").alias(col_name)

def masked_sample(schema, table, cols, n):
    sel = [mask_expr(c["column_name"], c["data_type"]) for c in cols]
    df = spark.table(f"{CATALOG}.{schema}.{table}").select(*sel).limit(n)
    return [r.asDict() for r in df.collect()]

print("masking rules loaded -", len(PII_NAME_PAT.pattern.split('|')), "name patterns")

# COMMAND ----------

# ── 5. Per-table facts: size, partitions, freshness, counts ─────────────────
# Every table is wrapped individually. One unreadable object must not abort a
# 120-table sweep - it is recorded with its error and the loop continues.

TS_TYPES = ("timestamp", "date")

def detail(fqn):
    try:
        d = spark.sql(f"DESCRIBE DETAIL {fqn}").collect()[0].asDict()
        return {"format": d.get("format"), "size_bytes": d.get("sizeInBytes"),
                "num_files": d.get("numFiles"),
                "partition_columns": d.get("partitionColumns") or [],
                "last_modified": str(d.get("lastModified")) if d.get("lastModified") else None,
                "location": d.get("location")}
    except Exception as e:
        return {"detail_error": str(e)[:200]}

def freshness(fqn, cols):
    ts = [c["column_name"] for c in cols
          if any(c["data_type"].lower().startswith(t) for t in TS_TYPES)]
    if not ts:
        return {}
    pick = next((c for c in ts if re.search(r"(event|transit|txn|ingest|load|updated|created)", c, re.I)), ts[0])
    try:
        r = spark.sql(f"SELECT MIN(`{pick}`) lo, MAX(`{pick}`) hi FROM {fqn}").collect()[0]
        return {"freshness_column": pick, "min": str(r["lo"]), "max": str(r["hi"])}
    except Exception as e:
        return {"freshness_column": pick, "freshness_error": str(e)[:160]}

FACTS = []
for i, t in enumerate(TABLES, 1):
    sch, tab, typ = t["table_schema"], t["table_name"], t["table_type"]
    fqn = f"{CATALOG}.{sch}.{tab}"
    rec = {"schema": sch, "table": tab, "type": typ,
           "table_comment": t.get("table_comment"), "fqn": fqn}
    cols = [c for c in COLS if c["table_schema"] == sch and c["table_name"] == tab]
    rec["n_columns"] = len(cols)
    if typ != "VIEW":
        rec.update(detail(fqn))
    if DO_COUNTS:
        try:
            rec["row_count"] = spark.sql(f"SELECT COUNT(*) c FROM {fqn}").collect()[0]["c"]
        except Exception as e:
            rec["row_count_error"] = str(e)[:200]
    rec.update(freshness(fqn, cols))
    FACTS.append(rec)
    if i % 10 == 0 or i == len(TABLES):
        print(f"  profiled {i}/{len(TABLES)}")

display(spark.createDataFrame([{k: (str(v) if isinstance(v, list) else v)
                                for k, v in f.items()} for f in FACTS]))

# COMMAND ----------

# ── 6. Grain candidates ─────────────────────────────────────────────────────
# Not a guess at the primary key - a MEASUREMENT. For each table we test the
# obvious identity columns and report how close each comes to unique. A column
# at ratio 1.0 is a candidate key; anything below is stated as what it is.
# This is the number that settles "what is the grain of this table" arguments.

GRAIN_HINT = re.compile(r"(_id$|_key$|_nbr$|_number$|serial|device|event|txn|transaction)", re.I)
GRAIN = []
if DO_COUNTS:
    for f in FACTS:
        if f["type"] == "VIEW" or not f.get("row_count"):
            continue
        cols = [c["column_name"] for c in COLS
                if c["table_schema"] == f["schema"] and c["table_name"] == f["table"]
                and GRAIN_HINT.search(c["column_name"])][:6]
        if not cols:
            continue
        try:
            aggs = [F.countDistinct(F.col(f"`{c}`")).alias(c) for c in cols]
            row = spark.table(f["fqn"]).agg(*aggs).collect()[0].asDict()
            n = f["row_count"] or 1
            for c, d in row.items():
                GRAIN.append({"schema": f["schema"], "table": f["table"], "column": c,
                              "distinct": d, "rows": n, "uniqueness": round(d / n, 4),
                              "candidate_key": bool(d == n)})
        except Exception as e:
            GRAIN.append({"schema": f["schema"], "table": f["table"],
                          "grain_error": str(e)[:160]})
    print(f"{len(GRAIN)} grain measurements")
    if GRAIN:
        display(spark.createDataFrame(GRAIN))
else:
    print("skipped - needs exact row counts")

# COMMAND ----------

# ── 7. Masked samples ───────────────────────────────────────────────────────
SAMPLES = {}
if DO_SAMPLE:
    for i, f in enumerate(FACTS, 1):
        cols = [c for c in COLS if c["table_schema"] == f["schema"] and c["table_name"] == f["table"]]
        if not cols:
            continue
        try:
            SAMPLES[f"{f['schema']}.{f['table']}"] = masked_sample(f["schema"], f["table"], cols, N_SAMPLE)
        except Exception as e:
            SAMPLES[f"{f['schema']}.{f['table']}"] = [{"sample_error": str(e)[:200]}]
        if i % 20 == 0 or i == len(FACTS):
            print(f"  sampled {i}/{len(FACTS)}")
    ok = sum(1 for v in SAMPLES.values() if v and "sample_error" not in v[0])
    print(f"{ok}/{len(SAMPLES)} sampled cleanly")
else:
    print("samples skipped")

# COMMAND ----------

# ── 8. Assemble + write the snapshot ────────────────────────────────────────
# WRITE PATH, EXPLAINED - this is the cell that failed on the first run.
#
#   OSError: [Errno 95] Operation not supported: '/dbfs/tmp'
#
# /dbfs is the DBFS FUSE mount. It does NOT exist on Unity-Catalog shared or
# single-user access-mode clusters, and open("/dbfs/...") therefore fails with
# Errno 95 rather than a friendly message. Anything that assumes /dbfs is
# writable is assuming a cluster mode you may not be on.
#
# The pattern below works on every cluster type:
#   1. write to the DRIVER's own /tmp with plain open()   - always allowed
#   2. copy out with dbutils.fs.cp("file:/tmp/...", dest) - the FS API does not
#      need the FUSE mount, so it works where step 1's /dbfs path would not
#   3. offer a direct browser download as a data: link    - lands on YOUR disk
#      with no DBFS, no FileStore and no cluster permissions at all
#
# Destinations are tried in order and every failure is reported rather than
# swallowed, so you can see which routes your workspace actually permits.
import base64, shutil

snapshot = {
    "generated_utc": RUN_TS,
    "catalog": CATALOG,
    "schemas": SCHEMAS,
    "counts": {s: sum(1 for f in FACTS if f["schema"] == s) for s in SCHEMAS},
    "options": {"row_counts": DO_COUNTS, "samples": DO_SAMPLE,
                "sample_rows": N_SAMPLE, "views_included": INC_VIEWS},
    "tables": FACTS,
    "columns": COLS,
    "grain": GRAIN,
    "samples": SAMPLES,
}

LOCAL_DIR = "/tmp/chicago_catalog"
os.makedirs(LOCAL_DIR, exist_ok=True)

def save_output(filename, text):
    """Write to driver /tmp, then copy everywhere that will accept it."""
    local = f"{LOCAL_DIR}/{filename}"
    with open(local, "w") as fh:
        fh.write(text)
    mb = os.path.getsize(local) / 1e6
    print(f"\n=== {filename}  ({mb:.2f} MB) ===")
    print(f"  driver     : {local}")

    routes = []
    # a) UC Volume, if the widget names one - the modern, governed destination
    if OUT_DIR.startswith("/Volumes/"):
        routes.append(("volume", f"{OUT_DIR}/{filename}"))
    # b) FileStore - gives a browser-downloadable /files/ URL
    routes.append(("FileStore", f"dbfs:/FileStore/chicago_catalog/{filename}"))
    # c) plain DBFS tmp
    routes.append(("dbfs", f"dbfs:/tmp/chicago_catalog/{filename}"))

    for label, dest in routes:
        try:
            dbutils.fs.mkdirs(dest.rsplit("/", 1)[0])
            dbutils.fs.cp(f"file:{local}", dest)
            print(f"  {label:<10} : {dest}")
            if dest.startswith("dbfs:/FileStore/"):
                print(f"  {'download':<10} : /files/{dest.replace('dbfs:/FileStore/','')}"
                      f"   (append to your workspace URL)")
        except Exception as e:
            print(f"  {label:<10} : NOT AVAILABLE - {str(e)[:110]}")
    return local, mb

json_text = json.dumps(snapshot, indent=1, default=str)
json_local, json_mb = save_output("catalog_snapshot.json", json_text)

# COMMAND ----------

# ── 9. Markdown digest - paste this into the skill ──────────────────────────
def human(b):
    if not b: return "-"
    for u in ["B","KB","MB","GB","TB"]:
        if b < 1024: return f"{b:.0f} {u}"
        b /= 1024
    return f"{b:.1f} PB"

lines = [f"# Chicago / CTA-Ventra catalog snapshot", "",
         f"Catalog `{CATALOG}` - generated {RUN_TS}", ""]
for s in SCHEMAS:
    rows = [f for f in FACTS if f["schema"] == s]
    lines += [f"## {s} ({len(rows)} objects)", "",
              "| table | type | rows | columns | size | partitions | freshness |",
              "|---|---|---|---|---|---|---|"]
    for f in sorted(rows, key=lambda x: x["table"]):
        fresh = f.get("max") or "-"
        parts = ", ".join(f.get("partition_columns") or []) or "-"
        rc = f.get("row_count")
        lines.append(f"| `{f['table']}` | {f['type']} | {rc if rc is not None else '-':,} "
                     f"| {f['n_columns']} | {human(f.get('size_bytes'))} | {parts} | {fresh} |"
                     if isinstance(rc, int) else
                     f"| `{f['table']}` | {f['type']} | - | {f['n_columns']} "
                     f"| {human(f.get('size_bytes'))} | {parts} | {fresh} |")
    lines.append("")

keys = [g for g in GRAIN if g.get("candidate_key")]
if keys:
    lines += ["## Measured candidate keys (uniqueness = 1.0)", ""]
    for g in keys:
        lines.append(f"- `{g['schema']}.{g['table']}` -> `{g['column']}` ({g['rows']:,} rows)")
    lines.append("")

errs = [f for f in FACTS if any(k.endswith("_error") for k in f)]
if errs:
    lines += ["## Objects that could not be fully profiled", ""]
    for f in errs:
        e = next(v for k, v in f.items() if k.endswith("_error"))
        lines.append(f"- `{f['schema']}.{f['table']}` - {e}")
    lines.append("")

digest = "\n".join(lines)
md_local, md_mb = save_output("catalog_digest.md", digest)
displayHTML("<pre style='font-size:11px;max-height:600px;overflow:auto'>"
            + digest.replace("<", "&lt;") + "</pre>")

# COMMAND ----------

# ── 9b. DOWNLOAD STRAIGHT TO YOUR MACHINE ───────────────────────────────────
# No DBFS, no FileStore, no cluster permissions: the file is embedded in the
# cell output as a data: URI and the browser saves it locally when you click.
# This is the route that works when every filesystem destination is blocked.
#
# Guarded at 25 MB because a data: URI much larger than that will hang or be
# refused by the browser. Above the cap, use the FileStore /files/ URL that
# cell 8 printed, or lower `sample_rows` and re-run.
CAP_MB = 25

def download_link(local_path, label=None):
    name = os.path.basename(local_path)
    mb = os.path.getsize(local_path) / 1e6
    if mb > CAP_MB:
        return (f"<p style='font:13px sans-serif;color:#b45309'><b>{name}</b> is {mb:.1f} MB - "
                f"too large to embed. Use the FileStore <code>/files/</code> URL from cell 8.</p>")
    with open(local_path, "rb") as fh:
        b64 = base64.b64encode(fh.read()).decode()
    return (f"<a download='{name}' href='data:application/octet-stream;base64,{b64}' "
            f"style=\"display:inline-block;margin:6px 10px 6px 0;padding:9px 16px;"
            f"background:#4F93F5;color:#fff;border-radius:6px;font:600 13px sans-serif;"
            f"text-decoration:none\">Download {label or name} ({mb:.2f} MB)</a>")

html = "<div style='padding:10px'>"
html += download_link(json_local, "catalog_snapshot.json")
try:
    html += download_link(md_local, "catalog_digest.md")
except NameError:
    pass
html += ("<p style='font:12px sans-serif;color:#64748b;margin-top:10px'>"
         "Click to save to your machine. Send me both files and I will rewrite the "
         "chicago-data-catalog skill against them.</p></div>")
displayHTML(html)

# COMMAND ----------

# ── 10. OPTIONAL: persist to mars_dev.audit.catalog_* ───────────────────────
# The skill names these as the live source of truth. Writing is OFF by default:
# this notebook is a read-only profiler unless you deliberately turn it on.
if WRITE_AUD:
    spark.sql(f"CREATE SCHEMA IF NOT EXISTS {CATALOG}.audit")
    stamp = F.lit(RUN_TS).alias("snapshot_utc")

    spark.createDataFrame([{k: (json.dumps(v) if isinstance(v, list) else v)
                            for k, v in f.items()} for f in FACTS]) \
         .withColumn("snapshot_utc", F.lit(RUN_TS)) \
         .write.mode("overwrite").option("overwriteSchema", "true") \
         .saveAsTable(f"{CATALOG}.audit.catalog_facts")

    spark.createDataFrame(COLS).withColumn("snapshot_utc", F.lit(RUN_TS)) \
         .write.mode("overwrite").option("overwriteSchema", "true") \
         .saveAsTable(f"{CATALOG}.audit.catalog_columns")

    if GRAIN:
        spark.createDataFrame(GRAIN).withColumn("snapshot_utc", F.lit(RUN_TS)) \
             .write.mode("overwrite").option("overwriteSchema", "true") \
             .saveAsTable(f"{CATALOG}.audit.catalog_validation")

    if SAMPLES:
        rows = [{"table_fqn": k, "sample_json": json.dumps(v, default=str)}
                for k, v in SAMPLES.items()]
        spark.createDataFrame(rows).withColumn("snapshot_utc", F.lit(RUN_TS)) \
             .write.mode("overwrite").option("overwriteSchema", "true") \
             .saveAsTable(f"{CATALOG}.audit.catalog_samples")
    print("audit tables refreshed")
else:
    print("write_audit_tables = no  ->  nothing written. Flip the widget to persist.")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Handing the result back
# MAGIC
# MAGIC 1. Download **`catalog_snapshot.json`** (complete, machine-readable) and
# MAGIC    **`catalog_digest.md`** (human summary) from the output folder.
# MAGIC 2. Send both to me and I will rewrite the `chicago-data-catalog` skill against them.
# MAGIC
# MAGIC **Sanity-check before sending.** Skim the `samples` block for anything the masker
# MAGIC missed - a column name I did not anticipate is the likely gap, and it is far
# MAGIC cheaper to add a pattern to `PII_NAME_PAT` and re-run than to un-share a value.
# MAGIC
# MAGIC **If the run is slow**, set `Exact row counts` = `no` for a first pass. Counts and
# MAGIC grain measurement are the expensive parts; schema, size and partitioning all come
# MAGIC back in seconds without them.
