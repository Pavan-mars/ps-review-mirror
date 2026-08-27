# Databricks notebook source
# MAGIC %md
# MAGIC # dim_device_incident_cmdb — daily build + S3 export  (v2, 27-Aug-2026)
# MAGIC
# MAGIC Rebuilds the device -> incident -> CMDB CI map (the schema of the one-time
# MAGIC `device_incidents` CSV) and exports it for the RDS loader.
# MAGIC
# MAGIC **v2 replicates the canonical join logic of the ORIGINAL CSV generator**
# MAGIC (`PS1_VALIDATOR_Device_Bus_Serial_Map.ipynb`, SageMaker) in Spark over UC tables:
# MAGIC
# MAGIC   silver.dim_device (is_current)           device spine, TVM/GATE/VALIDATOR
# MAGIC   silver.hw_config_current                 COMPONENT_SERIAL_NBR/TYPE via DEVICE_KEY
# MAGIC   silver.incident_task_ci_link (S25)       VALIDATOR incidents (DEVICE_ID resolved)
# MAGIC   bronze.servicenow_task_ci                task_ci sys_id -> ci_item_sys_id (= cmdb_ci_sys_id)
# MAGIC   silver.incident_history (S15)            TVM/GATE incidents (wm_asset -> DEVICE_ID)
# MAGIC
# MAGIC Env contract (all optional, behaviour-identical defaults):
# MAGIC   DIC_AS_OF_DATE, DIC_GOLD_BUCKET, DIC_S3_PREFIX,
# MAGIC   DIC_ENABLE_S3_EXPORT (default true), DIC_WRITE_DELTA (default true)

# COMMAND ----------

import os, json, uuid, datetime as dt
from pyspark.sql import functions as F, Window

CATALOG     = os.environ.get("DIC_CATALOG", "mars_dev")
AS_OF       = os.environ.get("DIC_AS_OF_DATE") or dt.datetime.utcnow().date().isoformat()
BUCKET      = os.environ.get("DIC_GOLD_BUCKET", "cubic-mars-pm-s3-datalake-dev-gold-170202974600")
PREFIX      = os.environ.get("DIC_S3_PREFIX", "chicago/dim/device_incident_cmdb")
EXPORT      = os.environ.get("DIC_ENABLE_S3_EXPORT", "true").lower() == "true"
WRITE_DELTA = os.environ.get("DIC_WRITE_DELTA", "true").lower() == "true"
RUN_ID      = uuid.uuid4().hex[:12]
TARGET      = f"s3://{BUCKET}/{PREFIX}/as_of_date={AS_OF}/run_id={RUN_ID}"

spark.sql(f"USE CATALOG {CATALOG}")
print(f"as_of={AS_OF} run_id={RUN_ID} export={EXPORT} target={TARGET}")

# COMMAND ----------

def pick(df, *cands, required=True, label=""):
    """Resolve a column by candidate list (case-insensitive); loud failure if required."""
    lower = {c.lower(): c for c in df.columns}
    for c in cands:
        if c.lower() in lower:
            return lower[c.lower()]
    if required:
        raise ValueError(f"[{label}] none of {cands} found in {df.columns[:40]}")
    return None

def col_or_null(name, alias):
    return (F.col(name) if name else F.lit(None).cast("string")).alias(alias)

def norm_id(c):
    """The original notebook's _norm_id: trim + upper, empty/NAN-ish -> NULL."""
    s = F.upper(F.trim(c.cast("string")))
    return F.when(s.isin("", "NAN", "NONE", "NAT"), None).otherwise(s)

# COMMAND ----------

# ---- 1. Device spine (current devices, TVM/GATE/VALIDATOR) ------------------
dev = spark.table("silver.dim_device")
cur = pick(dev, "is_current", "CURRENT_FLAG", required=False, label="dim_device")
if cur:
    dev = dev.where(F.col(cur).cast("string").isin("true", "True", "1"))

c_id   = pick(dev, "DEVICE_ID", label="dim_device")
c_key  = pick(dev, "DEVICE_KEY", label="dim_device")
c_name = pick(dev, "DEVICE_NAME", required=False) or c_id
c_bus  = pick(dev, "BUS_ID", required=False)
c_bflag= pick(dev, "bus_device_flag", required=False)
c_ser  = pick(dev, "DEVICE_SERIAL_NUMBER", required=False)
c_fac  = pick(dev, "FACILITY_ID", required=False)
c_facn = pick(dev, "FACILITY_NAME", required=False)
c_op   = pick(dev, "OPERATOR_ID", required=False)
c_opn  = pick(dev, "OPERATOR_NAME", required=False)
c_cat  = pick(dev, "mars_device_category", label="dim_device")
c_typ  = pick(dev, "DEVICE_TYPE_NAME", "DEVICE_TYPE", required=False)
c_mode = pick(dev, "TRANSIT_MODE_NAME", required=False)

devices = dev.select(
    norm_id(F.col(c_id)).alias("device_id"),
    F.col(c_key).cast("string").alias("device_key"),
    F.col(c_name).alias("device_name"),
    col_or_null(c_bus, "bus_id"),
    (F.col(c_bflag).cast("string").isin("true", "True", "1") if c_bflag
     else F.col(c_bus).isNotNull() if c_bus else F.lit(False)).alias("bus_device_flag"),
    col_or_null(c_ser, "device_serial_number"),
    col_or_null(c_fac, "facility_id"),
    col_or_null(c_facn, "facility_name"),
    col_or_null(c_op, "operator_id"),
    col_or_null(c_opn, "operator_name"),
    F.upper(F.trim(F.col(c_cat))).alias("mars_device_category"),
    col_or_null(c_typ, "device_type_name"),
    col_or_null(c_mode, "transit_mode_name"),
).where(F.col("mars_device_category").isin("TVM", "GATE", "VALIDATOR")) \
 .where(F.col("device_id").isNotNull()) \
 .dropDuplicates(["device_id"])

print("devices:", devices.count())

# ---- 2. Component serial (hw_config_current, latest per DEVICE_KEY) ---------
hw = spark.table("silver.hw_config_current")
h_key = pick(hw, "DEVICE_KEY", "DEVICE_ID", label="hw_config_current")
h_csn = pick(hw, "COMPONENT_SERIAL_NBR", "SERIAL_NUMBER", "COMPONENT_SERIAL", label="hw_config_current")
h_typ = pick(hw, "COMPONENT_DESCRIPTION", "COMPONENT_TYPE", "COMPONENT_PART_NBR", required=False)
h_dtm = pick(hw, "REPORTED_CHANGED_DTM", required=False)

w = Window.partitionBy(h_key).orderBy(F.col(h_dtm).desc() if h_dtm else F.lit(1))
hw1 = (hw.where(F.col(h_csn).isNotNull())
         .withColumn("_rn", F.row_number().over(w)).where("_rn = 1")
         .select(F.col(h_key).cast("string").alias("device_key"),
                 F.col(h_csn).alias("component_serial_nbr"),
                 col_or_null(h_typ, "component_type")))

devices = devices.join(hw1, "device_key", "left") \
    .withColumn("serial_number",
                F.coalesce(F.col("component_serial_nbr"), F.col("device_serial_number")))
# (serial precedence = component-first, matching the original notebook)

# COMMAND ----------

# ---- 3. Incident events per device — mirrors the original CSV generator -----

# 3a. VALIDATOR path: silver.incident_task_ci_link (DEVICE_ID already resolved)
link = spark.table("silver.incident_task_ci_link")
l_dev  = pick(link, "DEVICE_ID", label="incident_task_ci_link")
l_num  = pick(link, "incident_number", required=False)
l_lnum = pick(link, "linked_incident_number", required=False)
l_sys  = pick(link, "incident_sys_id", required=False)
l_lsys = pick(link, "linked_incident_sys_id", required=False)
l_tci  = pick(link, "task_ci_sys_id", required=False)
l_op   = pick(link, "opened_at", required=False)
l_cl   = pick(link, "closed_at", required=False)

def coalesce_cols(a, b):
    if a and b:  return F.coalesce(F.col(a), F.col(b))
    if a:        return F.col(a)
    if b:        return F.col(b)
    return F.lit(None).cast("string")

val_ev = link.select(
    norm_id(F.col(l_dev)).alias("device_id"),
    coalesce_cols(l_num, l_lnum).cast("string").alias("incident_number"),
    coalesce_cols(l_sys, l_lsys).cast("string").alias("incident_sys_id"),
    col_or_null(l_tci, "task_ci_sys_id"),
    (F.col(l_op).cast("timestamp") if l_op else F.lit(None).cast("timestamp")).alias("opened_at"),
    (F.col(l_cl).cast("timestamp") if l_cl else F.lit(None).cast("timestamp")).alias("closed_at"),
).where(F.col("device_id").isNotNull())

# task_ci sys_id -> ci_item_sys_id  (= cmdb_ci_sys_id, per the original notebook)
tc = spark.table("bronze.servicenow_task_ci")
t_id = pick(tc, "sys_id", "task_ci_sys_id", label="servicenow_task_ci")
t_ci = pick(tc, "ci_item_sys_id", "cmdb_ci_sys_id", "ci_item", required=False)
if t_ci:
    cmdb_map = (tc.select(F.col(t_id).cast("string").alias("task_ci_sys_id"),
                          F.col(t_ci).cast("string").alias("cmdb_ci_sys_id"))
                  .where(F.col("task_ci_sys_id").isNotNull())
                  .dropDuplicates(["task_ci_sys_id"]))
    val_ev = val_ev.join(cmdb_map, "task_ci_sys_id", "left")
else:
    print("[WARN] servicenow_task_ci has no ci_item_sys_id — cmdb_ci_sys_id will be null on the VALIDATOR path")
    val_ev = val_ev.withColumn("cmdb_ci_sys_id", F.lit(None).cast("string"))
val_ev = val_ev.drop("task_ci_sys_id")

# 3b. TVM/GATE path: silver.incident_history (wm_asset -> DEVICE_ID)
ih = spark.table("silver.incident_history")
i_dev = pick(ih, "wm_asset", "DEVICE_ID", label="incident_history")
i_num = pick(ih, "incident_number", "number", required=False)
i_sys = pick(ih, "incident_sys_id", "sys_id", required=False)
i_op  = pick(ih, "opened_dtm", "opened_at", required=False)
i_cl  = pick(ih, "closed_dtm", "closed_at", required=False)

tg_ev = ih.select(
    norm_id(F.col(i_dev)).alias("device_id"),
    col_or_null(i_num, "incident_number").cast("string").alias("incident_number"),
    col_or_null(i_sys, "incident_sys_id").cast("string").alias("incident_sys_id"),
    F.lit(None).cast("string").alias("cmdb_ci_sys_id"),   # no CI on this path (as in the original)
    (F.col(i_op).cast("timestamp") if i_op else F.lit(None).cast("timestamp")).alias("opened_at"),
    (F.col(i_cl).cast("timestamp") if i_cl else F.lit(None).cast("timestamp")).alias("closed_at"),
).where(F.col("device_id").isNotNull())

# 3b-enrich: recover cmdb_ci_sys_id (and incident_sys_id) for TVM/GATE via
# incident_number -> incident sys_id (bronze.servicenow_incident_conformed)
# -> servicenow_task_ci.task -> ci_item_sys_id. The July CSV had CIs on GATE
# rows, so its generator variant did this; defensive — warns and no-ops if the
# columns are absent.
t_task = pick(tc, "task", "task_sys_id", "task_id", required=False)
if t_task and t_ci:
    inc_conf = spark.table("bronze.servicenow_incident_conformed")
    n_col = pick(inc_conf, "number", "incident_number", required=False)
    s_col = pick(inc_conf, "sys_id", required=False)
    if n_col and s_col:
        num2sys = (inc_conf.select(F.trim(F.col(n_col)).cast("string").alias("incident_number"),
                                   F.col(s_col).cast("string").alias("_inc_sys"))
                   .where(F.col("incident_number").isNotNull() & F.col("_inc_sys").isNotNull())
                   .dropDuplicates(["incident_number"]))
        task2ci = (tc.select(F.col(t_task).cast("string").alias("_inc_sys"),
                             F.col(t_ci).cast("string").alias("_ci"))
                   .where(F.col("_inc_sys").isNotNull() & F.col("_ci").isNotNull())
                   .dropDuplicates(["_inc_sys"]))
        tg_ev = (tg_ev.withColumn("incident_number", F.trim(F.col("incident_number")))
                 .join(num2sys, "incident_number", "left")
                 .join(task2ci, "_inc_sys", "left")
                 .withColumn("incident_sys_id", F.coalesce("incident_sys_id", "_inc_sys"))
                 .withColumn("cmdb_ci_sys_id", F.coalesce("cmdb_ci_sys_id", "_ci"))
                 .drop("_inc_sys", "_ci"))
        print("TVM/GATE CI enrichment: applied (incident_conformed + task_ci.task)")
    else:
        print("[WARN] TVM/GATE CI enrichment skipped: incident_conformed lacks number/sys_id")
else:
    print(f"[WARN] TVM/GATE CI enrichment skipped: servicenow_task_ci lacks a task ref "
          f"(cols include: {tc.columns[:25]})")

# 3c. Scope each path to its fleet slice, union, dedupe per device x incident
val_ids = devices.where("mars_device_category = 'VALIDATOR'").select("device_id")
tg_ids  = devices.where("mars_device_category IN ('TVM','GATE')").select("device_id")

events = (val_ev.join(val_ids, "device_id", "inner")
          .unionByName(tg_ev.join(tg_ids, "device_id", "inner"))
          .withColumn("incident_key",
                      F.coalesce(F.nullif(F.trim("incident_number"), F.lit("")),
                                 F.nullif(F.trim("incident_sys_id"), F.lit(""))))
          .where(F.col("incident_key").isNotNull()))

w_ev = Window.partitionBy("device_id", "incident_key").orderBy(F.col("opened_at").asc_nulls_last())
events = events.withColumn("_rn", F.row_number().over(w_ev)).where("_rn = 1").drop("_rn")
print("device x incident events:", events.count(),
      "| devices with incidents:", events.select("device_id").distinct().count())

# COMMAND ----------

# ---- 4. Collapse to one row per device: latest incident + ordered lists -----
w_latest = Window.partitionBy("device_id").orderBy(
    F.col("opened_at").desc_nulls_last(), F.col("closed_at").desc_nulls_last())
latest = (events.withColumn("_rn", F.row_number().over(w_latest)).where("_rn = 1")
          .select("device_id", "incident_number", "incident_sys_id",
                  "cmdb_ci_sys_id", "opened_at", "closed_at"))

def ordered_list(src_col):
    # chronological (opened_at asc) unique comma list, matching _comma_unique(ordered=True)
    arr = F.array_sort(F.collect_list(F.struct(F.col("opened_at"), F.col(src_col).alias("v"))))
    return F.array_join(F.array_distinct(F.filter(
        F.transform(arr, lambda x: x["v"]), lambda v: v.isNotNull() & (v != ""))), ", ")

rollup = events.groupBy("device_id").agg(
    ordered_list("incident_number").alias("all_incident_numbers"),
    ordered_list("incident_sys_id").alias("all_incident_sys_ids"),
    ordered_list("cmdb_ci_sys_id").alias("all_cmdb_ci_sys_ids"),
    F.countDistinct("incident_key").alias("incident_count"))

out = (devices
       .join(latest, "device_id", "left")
       .join(rollup, "device_id", "left")
       .withColumn("incident_count", F.coalesce("incident_count", F.lit(0)))
       .fillna("", subset=["all_incident_numbers", "all_incident_sys_ids", "all_cmdb_ci_sys_ids"])
       .select("device_id", "device_key", "device_name", "bus_id", "bus_device_flag",
               "serial_number", "device_serial_number", "component_serial_nbr", "component_type",
               "facility_id", "facility_name", "operator_id", "operator_name",
               "mars_device_category", "device_type_name", "transit_mode_name",
               "incident_number", "incident_sys_id", "cmdb_ci_sys_id",
               "opened_at", "closed_at",
               "all_incident_numbers", "all_incident_sys_ids", "all_cmdb_ci_sys_ids",
               "incident_count")
       .withColumn("as_of_date", F.lit(AS_OF))
       .withColumn("run_id", F.lit(RUN_ID)))

n_out = out.count()
n_with_inc = out.where("incident_count > 0").count()
n_ci = out.where("cmdb_ci_sys_id IS NOT NULL").count()
total_inc = out.agg(F.sum("incident_count")).first()[0]
print(f"OUT rows={n_out}  with-incidents={n_with_inc}  latest-has-ci={n_ci}  total-incidents={total_inc}")
out.groupBy("mars_device_category").agg(
    F.count("*").alias("n_devices"), F.sum("incident_count").alias("total_incidents")).show()
assert n_out > 0, "empty output — refusing to export"

# spot-check against the July CSV: HBG00011 had 104 incidents, CI c97cb829...
out.where("device_id = 'HBG00011'").select(
    "device_id", "incident_count", "incident_number", "cmdb_ci_sys_id").show(truncate=False)

# COMMAND ----------

# ---- 5. Persist: Delta (lineage) + S3 parquet + manifest.json ---------------
if WRITE_DELTA:
    out.write.mode("overwrite").option("overwriteSchema", "true") \
       .saveAsTable("gold.dim_device_incident_cmdb")
    print("delta: gold.dim_device_incident_cmdb overwritten")

if EXPORT:
    out.coalesce(1).write.mode("overwrite").parquet(TARGET + "/data")
    manifest = {
        "table": "dim_device_incident_cmdb",
        "source_table": "gold.dim_device_incident_cmdb",
        "grain": "device",
        "as_of_date": AS_OF,
        "run_id": RUN_ID,
        "row_count": n_out,
        "city_id": "CHI",
        "columns": out.columns,
        "data_prefix": f"{TARGET}/data",
        "generated_at_utc": dt.datetime.utcnow().isoformat() + "Z",
    }
    # manifest LAST — it is the loader trigger, data must already be in place
    dbutils.fs.put(TARGET + "/manifest.json", json.dumps(manifest, indent=2), overwrite=True)
    print("exported:", TARGET)
else:
    print("EXPORT disabled (DIC_ENABLE_S3_EXPORT=false) — build+validate only")
