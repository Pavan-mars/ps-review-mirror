# Databricks notebook source
# =============================================================================
# export_sn_signals_to_s3 -- ServiceNow signals for the PS1 feature builder
#
# Two parquet exports under the gold bucket's chicago/silver/ prefix, read by
# notebooks/ps1_features.py in the PS1 SageMaker notebooks:
#   servicenow_cta_chargability/  classified chargability tickets, one row per WOT
#                                 event. Feeds PS1_ENABLE_CHARGABILITY_FEATURES and
#                                 the PS1_EXCLUDE_NONDEVICE_OUTAGES label flag.
#   incident_task_ci_link/        S25 VALIDATOR tickets (bus-number link). Feeds
#                                 PS1_ENABLE_VALIDATOR_TICKET_FEATURES and the
#                                 VALIDATOR side of PS1_ENABLE_SN_REPAIR_FEATURES.
#
# Export contract, servicenow_cta_chargability/ (ps1_features.py reads these names):
#   device_id      STRING     upper(trim(u_device_id))
#   event_id       STRING     u_event_id (WOT number), one row per event
#   device_type    STRING     u_device_type
#   start_dtm      TIMESTAMP  u_start_dtm
#   end_dtm        TIMESTAMP  u_end_dtm; features key on to_date(end_dtm), so a
#                             ticket becomes visible only once it has closed
#   res_class      STRING     reset | replace | nff | adjust | other   (u_resolution)
#   req_class      STRING     corrective | vandal_customer | planned | other
#                             (u_request_type; u_resolution when that is null)
#   component      STRING     u_affected_component
#   failure_level  STRING     u_failure_level ('' -> NULL; the source uses '' for missing)
#   repair_min     DOUBLE     end - start in minutes, clamped to [0, 30 days]
# No free-text column leaves this notebook: u_resolution and u_request_type are
# reduced to classes here, and the description / caller fields are never selected.
# Timestamps are written as instants; the day a ticket lands on follows the
# reader's Spark session timezone.
#
# READ-ONLY on Unity Catalog. Writes are parquet, mode overwrite, and ride the
# Unity Catalog storage credentials (external location), not the cluster's
# instance profile. Both outputs are regenerable exports: re-run this notebook to
# rebuild them. incident_task_ci_link/ is also written, in full, by
# export_silver_to_s3.py -- whichever ran last is what S3 holds.
# =============================================================================
from typing import Any

from pyspark.sql import functions as F, Window

dbutils: Any = globals().get("dbutils")
spark: Any = globals().get("spark")

CATALOG = "mars_dev"
BUCKET = "cubic-mars-pm-s3-datalake-dev-gold-170202974600"
SILVER_S3 = f"s3://{BUCKET}/chicago/silver"

CHG_SRC = f"{CATALOG}.bronze.servicenow_cta_chargability"
CHG_DEST = f"{SILVER_S3}/servicenow_cta_chargability"
LINK_SRC = f"{CATALOG}.silver.incident_task_ci_link"
LINK_DEST = f"{SILVER_S3}/incident_task_ci_link"

MAX_REPAIR_MIN = 60.0 * 24 * 30
TOP_N = 25
TRUNC = 60

spark.sql(f"USE CATALOG {CATALOG}")
# Parquet onto a path that may once have held a _delta_log (same guard as
# export_silver_to_s3.py; stale S3 listings can otherwise block the write).
spark.conf.set("spark.databricks.delta.formatCheck.enabled", "false")


def pick(df, *cands, required=True, label=""):
    """Resolve a column by candidate list, case-insensitively; loud failure if required."""
    lower = {c.lower(): c for c in df.columns}
    for c in cands:
        if c.lower() in lower:
            return lower[c.lower()]
    if required:
        raise ValueError(f"[{label}] none of {cands} found in {sorted(df.columns)[:60]}")
    return None


def blank_to_null(c):
    s = F.trim(c.cast("string"))
    return F.when(s == "", F.lit(None).cast("string")).otherwise(s)


def write_parquet(df, dest):
    """Overwrite dest with parquet. Clears only a stale _delta_log; overwrite handles the rest."""
    try:
        dbutils.fs.rm(f"{dest}/_delta_log", recurse=True)
    except Exception:
        pass
    df.write.format("parquet").mode("overwrite").save(dest)
    n = spark.read.parquet(dest).count()
    print(f"  wrote {n:,} rows -> {dest}")
    return n


def show_dist(df, col):
    """Class distribution with shares; returns the share in 'other'."""
    rows = df.groupBy(col).count().orderBy(F.desc("count")).collect()
    total = sum(r["count"] for r in rows)
    for r in rows:
        share = r["count"] / total if total else 0.0
        print(f"  {str(r[col]):<18} {r['count']:>10,}  {share:6.1%}")
    other = sum(r["count"] for r in rows if r[col] == "other")
    other_share = other / total if total else 0.0
    print(f"  share in 'other': {other_share:.1%} of {total:,}")
    return other_share


print(f"source      : {CHG_SRC}")
print(f"destination : {CHG_DEST}")

# COMMAND ----------

# ---- 1. Chargability: resolve columns, review the raw vocabularies ----------
chg_raw = spark.table(CHG_SRC)
c_evt = pick(chg_raw, "u_event_id", label="chargability")
c_dev = pick(chg_raw, "u_device_id", label="chargability")
c_typ = pick(chg_raw, "u_device_type", required=False)
c_start = pick(chg_raw, "u_start_dtm", label="chargability")
c_end = pick(chg_raw, "u_end_dtm", label="chargability")
c_res = pick(chg_raw, "u_resolution", required=False)
c_req = pick(chg_raw, "u_request_type", required=False)
c_comp = pick(chg_raw, "u_affected_component", required=False)
c_fl = pick(chg_raw, "u_failure_level", required=False)
c_fs = pick(chg_raw, "u_fault_state", required=False)
# Dedup tie-break, as in S17 jb_base: u_event_id is not 1:1 on this table.
c_upd = pick(chg_raw, "sys_updated_on", required=False)
c_cre = pick(chg_raw, "sys_created_on", required=False)
c_sys = pick(chg_raw, "sys_id", required=False)

_dtypes = dict(chg_raw.dtypes)
print(f"rows        : {chg_raw.count():,}")
for _c in (c_evt, c_dev, c_typ, c_start, c_end, c_res, c_req, c_comp, c_fl, c_fs):
    print(f"  {str(_c):<24} {_dtypes.get(_c, '-') if _c else 'ABSENT'}")


def top_values(col, label):
    """Top values with counts for review. Grouped on the full value, displayed truncated."""
    print(f"\n-- top {TOP_N} {label} ({col or 'ABSENT'}) --")
    if col is None:
        return
    v = F.trim(F.col(col).cast("string"))
    rows = (chg_raw.groupBy(v.alias("v")).count()
            .orderBy(F.desc("count")).limit(TOP_N).collect())
    for r in rows:
        shown = "<NULL>" if r["v"] is None else ("<empty>" if r["v"] == "" else r["v"][:TRUNC])
        print(f"  {r['count']:>10,}  {shown}")


top_values(c_res, "u_resolution")
top_values(c_req, "u_request_type")
top_values(c_comp, "u_affected_component")
top_values(c_fs, "u_fault_state")

# COMMAND ----------

# ---- 2. Classify, derive repair minutes, write the contract columns ---------
def ts_col(name):
    """TIMESTAMP as-is; strings parsed with try_* so a bad value is NULL, never an ANSI error."""
    t = _dtypes[name]
    if t == "timestamp":
        return F.col(name)
    if t == "date":
        return F.col(name).cast("timestamp")
    return F.coalesce(
        F.expr(f"try_cast(trim(`{name}`) AS TIMESTAMP)"),
        F.expr(f"try_to_timestamp(trim(`{name}`), 'dd-MM-yyyy HH:mm:ss')"),
        F.expr(f"try_to_timestamp(trim(`{name}`), 'MM/dd/yyyy HH:mm:ss')"),
    )


def res_rule(txt):
    """Resolution outcome, from the vocabulary seen on 26-Sep (top values in the cell above):
    replace ("Replaced", "Replaced BMV", "Replaced Battery", "Replaced Component"),
    reset ("Reset Remotely", "Reset", "Reintialized", "Reseated Connector"),
    nff -- no action needed ("Event self-cleared", "Recovered", "Passed", "No Fault Found",
    "Ping - Server Acknowledged"), adjust -- hands-on clear or adjust ("Cleared Feed Errors",
    "Cleared Foreign Material", "Cleared Worn/Torn Bill", "Cleared", "Adjusted Barrier").
    Order matters: a swap outranks a reboot, and "self-cleared" is nff, not adjust."""
    t = F.lower(txt)
    return (F.when(t.rlike("replac|swap|exchang|new part|changed out"), "replace")
             .when(t.rlike("reset|reboot|restart|power cycl|reinitiali|reintiali|re-?seat"), "reset")
             .when(t.rlike("no fault|nff|self-cleared|self cleared|recovered|passed|ping"
                           "|cannot dup|could not dup|unable to dup|no issue|tested ok"), "nff")
             .when(t.rlike("adjust|clean|clear|realign|tighten|lubric|foreign material|jam"), "adjust")
             .otherwise("other"))


def req_rule(req_txt, res_txt):
    """Request type is "Corrective Maintenance" on 99.4% of tickets, so it cannot separate
    planned work. The resolution can: "Level 1 PM" (10,301 tickets) is preventive maintenance.
    vandal_customer is kept for the contract but only an explicit request type can set it --
    resolution notes use "customer" and "damage" on ordinary repairs."""
    q, r = F.lower(F.coalesce(req_txt, F.lit(""))), F.lower(F.coalesce(res_txt, F.lit("")))
    return (F.when(r.rlike("level [0-9]+ pm|preventive"), "planned")
             .when(q.rlike("vandal|graffiti|abuse"), "vandal_customer")
             .when(q.rlike("planned|preventive|scheduled"), "planned")
             .when(q.rlike("corrective|repair|fault|break|fix|incident"), "corrective")
             .otherwise("other"))


_null = F.lit(None).cast("string")
res_txt = blank_to_null(F.col(c_res)) if c_res else _null
req_txt = blank_to_null(F.col(c_req)) if c_req else _null

base = chg_raw.select(
    blank_to_null(F.col(c_dev)).alias("_dev"),
    blank_to_null(F.col(c_evt)).alias("event_id"),
    (blank_to_null(F.col(c_typ)) if c_typ else _null).alias("device_type"),
    ts_col(c_start).alias("start_dtm"),
    ts_col(c_end).alias("end_dtm"),
    res_txt.alias("_res_txt"),
    req_txt.alias("_req_txt"),
    (blank_to_null(F.col(c_comp)) if c_comp else _null).alias("component"),
    (blank_to_null(F.col(c_fl)) if c_fl else _null).alias("failure_level"),
    (F.col(c_upd) if c_upd else F.lit(None).cast("timestamp")).alias("_upd"),
    (F.col(c_cre) if c_cre else F.lit(None).cast("timestamp")).alias("_cre"),
    (F.col(c_sys).cast("string") if c_sys else _null).alias("_sys"),
    F.col(c_start).isNotNull().alias("_start_raw"),
    F.col(c_end).isNotNull().alias("_end_raw"),
).withColumn("device_id", F.upper(F.col("_dev")))
# Bus validators are filed under the bare bus number ("1087"); the fleet key is BMV + the bus
# number padded to five digits ("BMV01087") -- the same mapping silver S25 uses for task_ci.
# Without it only 1 of 2,557 BMV devices joined the PS1 spine.
base = base.withColumn(
    "device_id",
    F.when((F.upper(F.coalesce(F.col("device_type"), F.lit(""))) == "BMV")
           & F.col("device_id").rlike("^[0-9]{1,5}$"),
           F.concat(F.lit("BMV"), F.lpad(F.col("device_id"), 5, "0")))
     .otherwise(F.col("device_id")))

# One row per event: most recently updated wins, sys_id breaks ties so reruns are
# stable. Rows without an event id are kept as they are -- they cannot be collapsed.
w = Window.partitionBy("event_id").orderBy(F.col("_dev").isNull().asc(),  # keep a row that has a device
                                           F.col("_upd").desc_nulls_last(),
                                           F.col("_cre").desc_nulls_last(),
                                           F.col("_sys").asc_nulls_last())
keyed = (base.where(F.col("event_id").isNotNull())
             .withColumn("_rn", F.row_number().over(w)).where(F.col("_rn") == 1).drop("_rn"))
chg = keyed.unionByName(base.where(F.col("event_id").isNull()))

_mins = (F.unix_timestamp("end_dtm") - F.unix_timestamp("start_dtm")) / 60.0
chg = (chg
       .withColumn("_raw_min", _mins)
       # greatest/least skip NULLs, so an unparsed timestamp would clamp to 0 without the guard
       .withColumn("repair_min",
                   F.when(F.col("_raw_min").isNotNull(),
                          F.least(F.greatest(F.col("_raw_min"), F.lit(0.0)),
                                  F.lit(MAX_REPAIR_MIN))).cast("double"))
       .withColumn("res_class", res_rule(F.coalesce(F.col("_res_txt"), F.lit(""))))
       .withColumn("req_class", req_rule(F.col("_req_txt"), F.col("_res_txt"))))

_q = chg.agg(
    F.count(F.lit(1)).alias("rows"),
    F.sum(F.col("device_id").isNull().cast("int")).alias("no_device"),
    F.sum(F.col("event_id").isNull().cast("int")).alias("no_event"),
    F.sum((F.col("_start_raw") & F.col("start_dtm").isNull()).cast("int")).alias("start_unparsed"),
    F.sum((F.col("_end_raw") & F.col("end_dtm").isNull()).cast("int")).alias("end_unparsed"),
    F.sum(F.col("end_dtm").isNull().cast("int")).alias("no_end"),
    F.sum((F.col("_raw_min") < 0).cast("int")).alias("neg_clamped"),
    F.sum((F.col("_raw_min") > MAX_REPAIR_MIN).cast("int")).alias("long_clamped"),
    F.sum((F.col("_req_txt").isNull() & F.col("_res_txt").isNotNull()).cast("int")).alias("req_from_res"),
).first()
print(f"after dedup          : {_q['rows']:,} rows (source {chg_raw.count():,})")
print(f"no device_id         : {_q['no_device']:,}  (dropped -- cannot join)")
print(f"no event_id          : {_q['no_event']:,}  (kept, not deduplicated)")
print(f"start/end unparsed   : {_q['start_unparsed']:,} / {_q['end_unparsed']:,}")
print(f"no end_dtm           : {_q['no_end']:,}  (never visible to features)")
print(f"repair_min clamped   : {_q['neg_clamped']:,} negative -> 0, "
      f"{_q['long_clamped']:,} over 30 days -> 30 days")
print(f"req_class from u_resolution (u_request_type null): {_q['req_from_res']:,}")

print("\n-- res_class --")
show_dist(chg, "res_class")
print("\n-- req_class --")
show_dist(chg, "req_class")

out = chg.where(F.col("device_id").isNotNull()).select(
    F.col("device_id").cast("string"),
    F.col("event_id").cast("string"),
    F.col("device_type").cast("string"),
    F.col("start_dtm").cast("timestamp"),
    F.col("end_dtm").cast("timestamp"),
    F.col("res_class").cast("string"),
    F.col("req_class").cast("string"),
    F.col("component").cast("string"),
    F.col("failure_level").cast("string"),
    F.col("repair_min").cast("double"),
)
print()
chg_rows = write_parquet(out, CHG_DEST)

# COMMAND ----------

# ---- 3. incident_task_ci_link: VALIDATOR tickets for the SageMaker side -----
# The ps1_features contract columns, plus the incident/task keys, facility and
# operator that PS1_VALIDATOR_Device_Bus_Serial_Map.ipynb reads from this same
# path. The keys also let a reader deduplicate task_ci fan-out (one incident can
# link several CIs). No free text (short_description, close_notes) is copied.
LINK_CONTRACT = ["DEVICE_ID", "DEVICE_KEY", "mars_device_category", "incident_date",
                 "opened_at", "resolved_at", "closed_at", "time_to_resolve_minutes",
                 "category", "close_code"]
LINK_KEYS = ["incident_sys_id", "incident_number", "linked_incident_sys_id",
             "linked_incident_number", "task_ci_sys_id", "FACILITY_ID", "FACILITY_NAME",
             "OPERATOR_ID"]

link = spark.table(LINK_SRC)
_have = {c.lower(): c for c in link.columns}
keep = [_have[c.lower()] for c in LINK_CONTRACT + LINK_KEYS if c.lower() in _have]
missing = [c for c in LINK_CONTRACT if c.lower() not in _have]
print(f"source      : {LINK_SRC}")
print(f"destination : {LINK_DEST}")
print(f"columns     : {keep}")
if missing:
    print(f"!! contract columns absent from {LINK_SRC}: {missing}")
link_rows = write_parquet(link.select(*keep), LINK_DEST)

# COMMAND ----------

# ---- 4. Read both exports back ----------------------------------------------
c_back = spark.read.parquet(CHG_DEST)
print(f"== {CHG_DEST}")
r = c_back.agg(F.count(F.lit(1)).alias("rows"),
               F.countDistinct("device_id").alias("devices"),
               F.min("start_dtm").alias("min_start"), F.max("start_dtm").alias("max_start"),
               F.min("end_dtm").alias("min_end"), F.max("end_dtm").alias("max_end"),
               F.sum(F.col("repair_min").isNull().cast("int")).alias("no_repair_min")).first()
print(f"  rows {r['rows']:,}  devices {r['devices']:,}  repair_min NULL {r['no_repair_min']:,}")
print(f"  start_dtm {r['min_start']} .. {r['max_start']}")
print(f"  end_dtm   {r['min_end']} .. {r['max_end']}")
print("  per device_type:")
for row in c_back.groupBy("device_type").count().orderBy(F.desc("count")).collect():
    print(f"    {str(row['device_type']):<20} {row['count']:>10,}")
print("  res_class x req_class:")
for row in (c_back.groupBy("res_class", "req_class").count()
            .orderBy("res_class", "req_class").collect()):
    print(f"    {row['res_class']:<8} {row['req_class']:<16} {row['count']:>10,}")

l_back = spark.read.parquet(LINK_DEST)
print(f"\n== {LINK_DEST}")
_lc = {c.lower(): c for c in l_back.columns}
_aggs = [F.count(F.lit(1)).alias("rows")]
if "device_id" in _lc:
    _aggs += [F.sum(F.col(_lc["device_id"]).isNotNull().cast("int")).alias("with_device"),
              F.countDistinct(_lc["device_id"]).alias("devices")]
for _d in ("incident_date", "resolved_at"):
    if _d in _lc:
        _aggs += [F.min(_lc[_d]).alias(f"min_{_d}"), F.max(_lc[_d]).alias(f"max_{_d}")]
r = l_back.agg(*_aggs).first().asDict()
print(f"  rows {r['rows']:,}  with DEVICE_ID {r.get('with_device', 0) or 0:,}  "
      f"devices {r.get('devices', 0) or 0:,}")
for _d in ("incident_date", "resolved_at"):
    if f"min_{_d}" in r:
        print(f"  {_d:<14} {r[f'min_{_d}']} .. {r[f'max_{_d}']}")
if "mars_device_category" in _lc:
    print("  per mars_device_category:")
    for row in (l_back.groupBy(_lc["mars_device_category"]).count()
                .orderBy(F.desc("count")).collect()):
        print(f"    {str(row[0]):<20} {row['count']:>10,}")
