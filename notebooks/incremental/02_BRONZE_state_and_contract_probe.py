# Databricks notebook source
# MAGIC %md
# MAGIC # 02 — BRONZE / RAW STATE + DATA CONTRACT PROBE  (read-only, no Oracle, no VPN)
# MAGIC
# MAGIC **Purpose.** Capture the *current* Databricks-side contract so drift can be measured against it:
# MAGIC
# MAGIC | Cell | Answers |
# MAGIC |---|---|
# MAGIC | 2 | every in-scope bronze table: rows, size, files, partitions, **true max watermark** |
# MAGIC | 3 | full column-level schema of **bronze** (the live data contract as-built) |
# MAGIC | 4 | full column-level schema of **raw** parquet (what actually landed in S3) |
# MAGIC | 5 | the declared contract `mars_dev.audit.bronze_data_contract`, and where it disagrees with reality |
# MAGIC | 6 | paste-back JSON |
# MAGIC
# MAGIC Runs on any cluster with Unity Catalog + S3 read. **Writes nothing.**
# MAGIC
# MAGIC > Note on "max watermark": every max is taken with `< SENTINEL` applied. Chicago bronze carries
# MAGIC > known future-dated sentinel rows (`edw_device_metric` → 2028-05-13; several NCS tables → 2032–2034;
# MAGIC > one 1949 / 1969 min). A naive `MAX()` reports 2034 and makes a stale table look fresh.

# COMMAND ----------
# ============================== CELL 1 : CONFIG ==============================
import datetime as _dt, json
from pyspark.sql import functions as F

CAT       = "mars_dev"
BRONZE_DB = f"{CAT}.bronze"
RAW_ROOT  = "s3://cubic-mars-pm-s3-datalake-dev-raw-170202974600/chicago_ventra"
CONTRACT  = f"{CAT}.audit.bronze_data_contract"
INCR_FROM = "2026-04-12"
SENTINEL  = (_dt.date.today() + _dt.timedelta(days=1)).strftime("%Y-%m-%d")

# bronze_table -> (raw_owner_dir, raw_table_dir)
SCOPE = {
 "edw_device_event":                     ("edw","device_event"),
 "edw_abp_tap":                          ("edw","abp_tap"),
 "edw_device_metric":                    ("edw","device_metric"),
 "edw_availability_events":              ("edw","availability_events"),
 "edw_availability_relief":              ("edw","availability_relief"),
 "edw_device_dimension":                 ("edw","device_dimension"),
 "edw_device_last_state":                ("edw","device_last_state"),
 "edw_device_current_hw_config":         ("edw","device_current_hw_config"),
 "edw_read_transaction":                 ("edw","read_transaction"),
 "edw_use_transaction_daily":            ("edw","use_transaction_daily"),
 "edw_kpi_detail_events_by_day":         ("edw","kpi_detail_events_by_day"),
 "edw_kpi_summary_by_day":               ("edw","kpi_summary_by_day"),
 "edw_kpi_rules":                        ("edw","kpi_rules"),
 "edw_kpi":                              ("edw","kpi"),
 "edw_kpi_target":                       ("edw","kpi_target"),
 "edw_event_type_dimension":             ("edw","event_type_dimension"),
 "edw_metric_dimension":                 ("edw","metric_dimension"),
 "ncs_stage_cashbox_tracking":           ("ncs_stage","cashbox_tracking"),
 "ncs_stage_sale_transaction":           ("ncs_stage","sale_transaction"),
 "ncs_stage_device_end_of_day":          ("ncs_stage","device_end_of_day"),
 "ncs_stage_device_end_of_day_msg_count":("ncs_stage","device_end_of_day_msg_count"),
 "ncs_stage_device":                     ("ncs_stage","device"),
 "ncs_stage_device_type":                ("ncs_stage","device_type"),
 "ncs_stage_event":                      ("ncs_stage","event"),
 "ncs_stage_stop_point":                 ("ncs_stage","stop_point"),
 "ncs_stage_transit_facility":           ("ncs_stage","transit_facility"),
 "cta_servicenow_availability_events":   ("cta","servicenow_availability_events"),
 "cta_servicenow_data_from_jumpbox":     ("cta","servicenow_data_from_jumpbox"),
 "cta_kpi_agency_map":                   ("cta","kpi_agency_map"),
 "cta_kpi_monthly_summary":              ("cta","kpi_monthly_summary"),
 "cta_sldc_monthly_summary":             ("cta","sldc_monthly_summary"),
 "cta_kpi_tvm_date_table":               ("cta","kpi_tvm_date_table"),
 # tier-3, measured so the STOP/KEEP call has numbers
 "ncs_stage_device_event_history":       ("ncs_stage","device_event_history"),
 "edw_metric_summary_by_day":            ("edw","metric_summary_by_day"),
 "cta_abp_use_tran_timing_data":         ("cta","abp_use_tran_timing_data"),
 "edw_device_last_set_event":            ("edw","device_last_set_event"),
 "edw_device_location_history":          ("edw","device_location_history"),
 "edw_device_current_tables":            ("edw","device_current_tables"),
 "edw_device_current_sw_config":         ("edw","device_current_sw_config"),
 "ncs_stage_sale_transaction_device_msg":("ncs_stage","sale_transaction_device_msg"),
 "ncs_stage_cashbox_manual_counts":      ("ncs_stage","cashbox_manual_counts"),
 "edw_date_dimension":                   ("edw","date_dimension"),
}
AUDIT_PREFIX = "_"          # our own columns: _ingest_ts, _raw_ingest_ts, _batch_id, _source_system, ...
print(f"02_BRONZE_state_probe | {len(SCOPE)} tables | sentinel={SENTINEL}")

# COMMAND ----------
# ============================== CELL 2 : ROWS / SIZE / MAX-WATERMARK PER BRONZE TABLE ================
def real_date_cols(df):
    return [f.name for f in df.schema.fields
            if f.dataType.simpleString() in ("date", "timestamp")
            and not f.name.startswith(AUDIT_PREFIX)
            and f.name.lower() not in ("year", "month", "day")]
def daykey_cols(df):
    return [f.name for f in df.schema.fields
            if (f.name.upper().endswith("_DAY_KEY") or f.name.upper() == "DT")
            and f.dataType.simpleString() in ("int", "bigint", "decimal(8,0)", "double")]

FACTS = []
for b in sorted(SCOPE):
    fq = f"{BRONZE_DB}.`{b}`"
    try:
        df = spark.table(fq)
    except Exception as e:
        FACTS.append({"table": b, "state": f"NOT_FOUND:{str(e).splitlines()[0][:50]}"}); continue
    try:
        n = spark.sql(f"SELECT COUNT(*) n FROM {fq}").first()["n"]
    except Exception:
        n = -1
    maxes, incr = {}, {}
    for c in real_date_cols(df):
        try:
            m = df.where(F.col(c) < F.lit(SENTINEL)).agg(F.max(c)).first()[0]
            maxes[c] = str(m) if m else None
            incr[c] = df.where((F.col(c) >= F.lit(INCR_FROM)) & (F.col(c) < F.lit(SENTINEL))).count()
        except Exception as e:
            maxes[c] = f"ERR:{str(e)[:40]}"
    for c in daykey_cols(df):
        try:
            lim = int(SENTINEL.replace("-", ""))
            m = df.where(F.col(c) < lim).agg(F.max(c)).first()[0]
            maxes[c] = str(m)
            incr[c] = df.where((F.col(c) >= int(INCR_FROM.replace("-", ""))) & (F.col(c) < lim)).count()
        except Exception as e:
            maxes[c] = f"ERR:{str(e)[:40]}"
    det = {}
    try:
        d = spark.sql(f"DESCRIBE DETAIL {fq}").first().asDict()
        det = {"format": d.get("format"), "numFiles": d.get("numFiles"),
               "sizeBytes": d.get("sizeInBytes"), "partitionColumns": d.get("partitionColumns"),
               "location": d.get("location")}
    except Exception:
        pass
    FACTS.append({"table": b, "state": "ok", "rows": n, "n_cols": len(df.columns),
                  "max_by_col": maxes, "rows_since_0412_by_col": incr, **det})
    print(f"{b:40s} rows={n:>14,} cols={len(df.columns):3d} files={det.get('numFiles')} "
          f"max={ {k:v for k,v in list(maxes.items())[:3]} }")

# COMMAND ----------
# ============================== CELL 3 : BRONZE COLUMN CONTRACT (as-built) ==========================
BRONZE_SCHEMA = {}
for b in sorted(SCOPE):
    try:
        BRONZE_SCHEMA[b] = [{"c": f.name, "t": f.dataType.simpleString(), "null": f.nullable,
                             "audit": f.name.startswith(AUDIT_PREFIX)}
                            for f in spark.table(f"{BRONZE_DB}.`{b}`").schema.fields]
    except Exception as e:
        BRONZE_SCHEMA[b] = [{"ERROR": str(e).splitlines()[0][:60]}]
print(f"captured bronze schema for {len([k for k,v in BRONZE_SCHEMA.items() if 'ERROR' not in v[0]])} tables")
for b in sorted(BRONZE_SCHEMA)[:3]:
    print(f"\n{b}: " + ", ".join(f"{c['c']}:{c['t']}" for c in BRONZE_SCHEMA[b][:12]) + " ...")

# COMMAND ----------
# ============================== CELL 4 : RAW LAYER — layout + schema ===============================
# Classifies each raw prefix as ymd / flat / load_YYYYMM / MIXED, and captures the parquet schema.
# MIXED is what makes a prefix unreadable as one DataFrame ("Conflicting partition column names").
import re
def classify(prefix):
    try:
        kids = [x.name.rstrip("/") for x in dbutils.fs.ls(prefix)]
    except Exception as e:
        return "NO_PREFIX", [], str(e).splitlines()[0][:60]
    kinds = set()
    for k in kids:
        if k.startswith("_") or k.startswith("."):      continue
        if re.match(r"^year=\d{4}$", k):                kinds.add("ymd")
        elif re.match(r"^load_\d{6}_\d{6}$", k):        kinds.add("load_YYYYMM")
        elif k.endswith(".parquet") or k.endswith(".snappy.parquet"): kinds.add("flat")
        else:                                           kinds.add(f"other:{k}")
    if not kinds:                return "EMPTY", kids, ""
    if len(kinds) == 1:          return kinds.pop(), kids, ""
    return "MIXED(" + "|".join(sorted(kinds)) + ")", kids, ""

RAW = {}
for b, (o, t) in sorted(SCOPE.items()):
    p = f"{RAW_ROOT}/{o}/{t}/"
    layout, kids, err = classify(p)
    entry = {"prefix": p, "layout": layout, "n_children": len(kids),
             "sample_children": kids[:6], "err": err}
    if layout not in ("NO_PREFIX", "EMPTY"):
        try:
            rs = spark.read.parquet(p).schema
            entry["schema"] = [{"c": f.name, "t": f.dataType.simpleString()} for f in rs.fields]
            entry["readable_as_one_df"] = True
        except Exception as e:
            entry["schema"] = []
            entry["readable_as_one_df"] = False
            entry["read_error"] = str(e).splitlines()[0][:120]
    RAW[b] = entry
    flag = "" if entry.get("readable_as_one_df", True) else "  <-- UNREADABLE"
    print(f"{b:40s} {layout:26s} children={len(kids):4d}{flag}")

# COMMAND ----------
# ============================== CELL 5 : DECLARED CONTRACT vs REALITY ==============================
DECLARED = []
try:
    DECLARED = [r.asDict() for r in spark.table(CONTRACT).collect()]
    print(f"{CONTRACT}: {len(DECLARED)} rows "
          f"({sum(1 for r in DECLARED if r.get('status')=='curated')} curated / "
          f"{sum(1 for r in DECLARED if r.get('status')=='draft')} draft)")
except Exception as e:
    print(f"!! contract table unreadable: {str(e).splitlines()[0][:100]}")

decl = {r["table"]: r for r in DECLARED}
print(f"\n{'table':40s} {'declared_wm(ins/upd)':40s} {'strategy':9s} {'status':8s} verdict")
print("-" * 140)
CONTRACT_FLAGS = []
for b in sorted(SCOPE):
    d = decl.get(b)
    if not d:
        CONTRACT_FLAGS.append((b, "NOT_IN_CONTRACT"))
        print(f"{b:40s} {'-':40s} {'-':9s} {'-':8s} NOT IN CONTRACT"); continue
    ins, upd, strat, st = d.get("insert_wm"), d.get("update_wm"), d.get("load_strategy"), d.get("status")
    flags = []
    for nm, v in (("insert_wm", ins), ("update_wm", upd), ("scope_col", d.get("scope_col"))):
        if v and v != "-" and v.startswith("_"):
            flags.append(f"AUDIT_COL_AS_{nm.upper()}({v})")   # the Issue-1 defect, structurally
    bcols = {c["c"].lower() for c in BRONZE_SCHEMA.get(b, []) if "c" in c}
    for nm, v in (("insert_wm", ins), ("update_wm", upd)):
        if v and v != "-" and not v.startswith("_") and v.lower() not in bcols:
            flags.append(f"{nm}_NOT_IN_BRONZE({v})")
    CONTRACT_FLAGS.append((b, ",".join(flags) or "ok"))
    print(f"{b:40s} {str(ins)+' / '+str(upd):40s} {str(strat):9s} {str(st):8s} {','.join(flags) or 'ok'}")

# COMMAND ----------
# ============================== CELL 6 : PASTE-BACK BLOCK ==============================
out = {
  "probe": "02_BRONZE_state_and_contract_probe",
  "run_ts": _dt.datetime.utcnow().isoformat() + "Z",
  "incr_from": INCR_FROM, "sentinel": SENTINEL,
  "bronze_facts": FACTS,
  "bronze_schema": BRONZE_SCHEMA,
  "raw": RAW,
  "declared_contract": DECLARED,
  "contract_flags": dict(CONTRACT_FLAGS),
}
print("=" * 100)
print("<<<PROBE02_JSON_START>>>")
print(json.dumps(out, default=str))
print("<<<PROBE02_JSON_END>>>")
print("=" * 100)
