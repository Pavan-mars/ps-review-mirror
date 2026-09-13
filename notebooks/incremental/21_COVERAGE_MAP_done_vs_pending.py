# Databricks notebook source
# MAGIC %md
# MAGIC # 21 — CHICAGO COVERAGE MAP: what is loaded, what is pending, and what moves each one
# MAGIC
# MAGIC **Read-only. No Oracle, no VPN.** Everything here comes from Databricks-side state, so it runs
# MAGIC in about a minute on any cluster and can be re-run after every load to see the map change.
# MAGIC
# MAGIC It joins four sources of truth and reconciles them:
# MAGIC
# MAGIC | source | answers |
# MAGIC |---|---|
# MAGIC | `mars_dev.audit.bronze_data_contract` | what is *in scope*, under which strategy and status |
# MAGIC | `mars_dev.audit.incremental_run_log` | what a loader has actually *applied*, and when |
# MAGIC | Unity Catalog `mars_dev.bronze.*` | whether the bronze Delta table *exists*, and its row count |
# MAGIC | S3 raw prefixes | whether the raw Parquet layer *exists*, and its newest partition |
# MAGIC
# MAGIC A table is only reported LOADED when the contract scopes it, bronze holds rows, **and** the
# MAGIC run log records a successful apply. Bronze rows without a run-log entry means the original
# MAGIC loader built it and our incremental engine has never touched it — a different state, and one
# MAGIC worth seeing separately.
# MAGIC
# MAGIC ## The verdicts it assigns
# MAGIC
# MAGIC | verdict | meaning | what moves it |
# MAGIC |---|---|---|
# MAGIC | `LOADED` | contract-scoped, bronze populated, run log confirms | nothing |
# MAGIC | `PENDING_NB16` | `measured_v2`, in the run list, not yet applied | **NB16** stage run |
# MAGIC | `PENDING_NB20` | `draft` on a v2-compatible `full` strategy | **NB20** bootstrap + promote |
# MAGIC | `BLOCKED_STRATEGY` | `draft` on v1 vocabulary (`append`/`scd2`/`merge`) | strategy decision, then NB20 |
# MAGIC | `DELEGATED` | handled by a separate script | `ingest_use_txn_daily.py` |
# MAGIC | `EXCLUDED` | `drop_from_scope` — deliberately out | nothing |
# MAGIC | `NOT_ORACLE` | ServiceNow source, frozen 30-May-2026 | separate ingestion path |
# MAGIC | `LEGACY_DUP` | superseded `'-'`-source row, has an owner-prefixed twin | retire the row |
# MAGIC | `LEGACY_ORPHAN` | superseded row with **no** twin — may be a real gap | investigate |
# MAGIC | `INFRA` | control/log/backup table, not source-backed | nothing |

# COMMAND ----------
# ============================== CELL 1 : CONFIG ==============================
import datetime as _dt, json, re
from pyspark.sql import functions as F

CAT, BRONZE_DB = "mars_dev", "mars_dev.bronze"
RAW_ROOT  = "s3://cubic-mars-pm-s3-datalake-dev-raw-170202974600/chicago_ventra"
CONTRACT  = "mars_dev.audit.bronze_data_contract"
RUN_LOG   = "mars_dev.audit.incremental_run_log"
OUT_DIR   = f"{RAW_ROOT}/_audit/coverage_{_dt.datetime.now().strftime('%Y%m%d_%H%M%S')}"

V2_STRATEGIES = {"full", "full_scoped", "cdc_merge", "cdc_append"}
CHECK_RAW     = True     # set False to skip the S3 listing (a little faster)

# Tables deliberately held, with the reason - kept here so the map explains itself.
HELD = {
    "ncs_stage_cashbox_tracking":         "held pending Cubic - CASHBOX_EVENT_ID now PROVEN a type code (NB19)",
    "ncs_stage_device_end_of_day":        "held pending Cubic - 73 future-dated TRANSIT_DAY_KEY rows",
    "edw_device_metric":                  "held pending Cubic - 25 future-dated TRANSIT_DAY_KEY rows",
    "cta_servicenow_availability_events": "source frozen 11-Apr - a re-pull returns identical rows",
    "cta_servicenow_data_from_jumpbox":   "source frozen 11-Apr - identical rows; VARCHAR dates unresolved",
    "cta_kpi_monthly_summary":            "unmaintained at source (last MONTH_DTM Mar-2015)",
    "cta_sldc_monthly_summary":           "unmaintained at source (0 new rows since Apr)",
    "ncs_stage_device_end_of_day_msg_count": "off-hours only (159M rows, no usable index)",
}
DELEGATED = {"edw_use_transaction_daily": "run ingest_use_txn_daily.py separately (append_aggregate)"}

print(f"NB21 coverage map | {_dt.datetime.now():%Y-%m-%d %H:%M} | READ-ONLY | no Oracle needed")

# COMMAND ----------
# ============================== CELL 2 : CONTRACT — classify every row ==============================
CON = [r.asDict() for r in spark.table(CONTRACT).collect()]
print(f"contract rows: {len(CON)}")

def gen_of(src):
    s = (str(src) if src is not None else "").strip()
    if s in ("", "null", "None"): return "infra"
    if s == "-":                  return "legacy"
    return "live"

for c in CON:
    c["_gen"]  = gen_of(c.get("source"))
    c["_name"] = str(c.get("table"))
    c["_strat"] = str(c.get("load_strategy") or "-")
    c["_status"] = str(c.get("status") or "-")

live   = [c for c in CON if c["_gen"] == "live"]
legacy = [c for c in CON if c["_gen"] == "legacy"]
infra  = [c for c in CON if c["_gen"] == "infra"]
live_names = {c["_name"] for c in live}

# a legacy short-name row is a duplicate if some live row is <owner-prefix> + that name
PREFIX = ("ncs_stage_", "edw_", "cta_", "servicenow_", "ncs_")
def twin(short):
    for ln in live_names:
        for p in PREFIX:
            if ln.startswith(p) and ln[len(p):] == short:
                return ln
        if short.startswith("ncs_") and ln == "ncs_stage_" + short[4:]:
            return ln
    return None

for c in legacy:
    c["_twin"] = twin(c["_name"])

print(f"  live (real source) : {len(live)}")
print(f"  legacy ('-')       : {len(legacy)}   dup={sum(1 for c in legacy if c['_twin'])} "
      f"orphan={sum(1 for c in legacy if not c['_twin'])}")
print(f"  infra (null)       : {len(infra)}")

# COMMAND ----------
# ============================== CELL 3 : RUN LOG — what has actually been applied ==============================
APPLIED = {}
try:
    rl = spark.table(RUN_LOG).where("dry_run = 'False'").collect()
    for r in rl:
        t = str(r["table"])
        try:
            res = json.loads(r["result"]) if r["result"] else {}
        except Exception:
            res = {}
        st = str(res.get("status", ""))
        # NB20 logs a table it found already in sync as PROMOTED_IN_SYNC - it was verified against
        # Oracle and its contract row was promoted, it simply needed no data movement. Omitting
        # that status made 17 correctly-handled tables report as PENDING_NB16 on 11-Sep.
        if not st.startswith(("LOADED", "FULL-RELOADED", "BOOTSTRAPPED", "PROMOTED")):
            continue
        prev = APPLIED.get(t)
        if prev is None or str(r["batch_id"]) > prev["batch"]:
            APPLIED[t] = {"batch": str(r["batch_id"]), "run_ts": str(r["run_ts"]), "status": st,
                          "new_rows": res.get("new_rows"), "bronze_rows": res.get("bronze_rows"),
                          "bronze_after": res.get("bronze_after"),
                          "oracle_rows": res.get("oracle_rows")}
    print(f"run log: {len(rl)} applied entries -> {len(APPLIED)} distinct table(s) successfully loaded")
    for t in sorted(APPLIED):
        a = APPLIED[t]
        n = a["new_rows"] if a["new_rows"] is not None else a["bronze_rows"]
        print(f"   {t:44s} {a['status']:16s} batch {a['batch']}  rows={n}")
except Exception as e:
    print(f"** run log unreadable: {str(e).splitlines()[0][:90]}")

# COMMAND ----------
# ============================== CELL 4 : BRONZE + RAW physical state ==============================
BRONZE, RAW = {}, {}
existing = {r["tableName"] for r in spark.sql(f"SHOW TABLES IN {BRONZE_DB}").collect()}
print(f"bronze tables present in {BRONZE_DB}: {len(existing)}")

targets = [c for c in CON if c["_gen"] == "live"]
for c in targets:
    t = c["_name"]
    if t in existing:
        try:
            n = spark.sql(f"SELECT COUNT(*) n FROM {BRONZE_DB}.`{t}`").first()["n"]
            BRONZE[t] = n
        except Exception as e:
            BRONZE[t] = f"error: {str(e).splitlines()[0][:40]}"
    if CHECK_RAW:
        src = str(c.get("source") or "")
        if "." in src and not src.upper().startswith("SERVICENOW"):
            o, tb = src.split(".", 1)
            try:
                # only partition directories - a bare parquet filename is not a partition and
                # printing one (as happened for availability_periods / stop_point_dimension) is noise
                parts = sorted([f.name.rstrip("/") for f in
                                dbutils.fs.ls(f"{RAW_ROOT}/{o.lower()}/{tb.lower()}/")
                                if f.name.endswith("/") and "=" in f.name], reverse=True)
                RAW[t] = parts[0] if parts else "(files, no partitions)"
            except Exception:
                RAW[t] = None
print(f"bronze counted: {len(BRONZE)}   raw prefixes found: {sum(1 for v in RAW.values() if v)}")

# COMMAND ----------
# ============================== CELL 5 : THE MAP ==============================
def verdict(c):
    t, st, sg = c["_name"], c["_status"], c["_strat"]
    src = str(c.get("source") or "")
    if c["_gen"] == "infra":  return "INFRA", "control/log/backup - not source-backed"
    if c["_gen"] == "legacy":
        return ("LEGACY_DUP", f"superseded by {c['_twin']}") if c.get("_twin") else \
               ("LEGACY_ORPHAN", "no owner-prefixed twin - may be a real gap, investigate")
    if src.upper().startswith("SERVICENOW"):
        return "NOT_ORACLE", "ServiceNow source, frozen 30-May-2026 - separate ingestion path"
    if sg == "drop_from_scope": return "EXCLUDED", "deliberately out of scope"
    if t in DELEGATED:          return "DELEGATED", DELEGATED[t]
    if st == "measured_v2":
        if t in APPLIED:        return "LOADED", f"batch {APPLIED[t]['batch']} ({APPLIED[t]['status']})"
        if t in HELD:           return "PENDING_NB16", HELD[t]
        return "PENDING_NB16", "in NB16 run list, no successful apply recorded"
    if t in APPLIED:            return "LOADED", f"batch {APPLIED[t]['batch']} - but status is still '{st}'"
    if sg in V2_STRATEGIES:     return "PENDING_NB20", f"draft on v2-compatible '{sg}' - bootstrap + promote"
    return "BLOCKED_STRATEGY", f"draft on v1 '{sg}' - not implemented by the v2 engine"

ORDER = ["PENDING_NB16", "PENDING_NB20", "BLOCKED_STRATEGY", "DELEGATED", "LOADED",
         "NOT_ORACLE", "EXCLUDED", "LEGACY_ORPHAN", "LEGACY_DUP", "INFRA"]
MAP = []
for c in CON:
    v, why = verdict(c)
    MAP.append({"verdict": v, "table": c["_name"], "source": str(c.get("source") or ""),
                "strategy": c["_strat"], "status": c["_status"],
                "bronze_rows": BRONZE.get(c["_name"]), "raw_newest": RAW.get(c["_name"]),
                "note": why})

for v in ORDER:
    grp = sorted([m for m in MAP if m["verdict"] == v], key=lambda x: (x["source"], x["table"]))
    if not grp: continue
    print("\n" + "=" * 128)
    print(f"{v}  —  {len(grp)} table(s)")
    print("=" * 128)
    if v in ("INFRA", "LEGACY_DUP"):
        print("   " + ", ".join(m["table"] for m in grp))
        continue
    print(f"{'table':44s} {'strategy':10s} {'bronze rows':>14s} {'raw newest':>22s}  note")
    print("-" * 128)
    for m in grp:
        br = f"{m['bronze_rows']:,}" if isinstance(m["bronze_rows"], int) else (m["bronze_rows"] or "-")
        print(f"{m['table']:44s} {m['strategy']:10s} {str(br):>14s} {str(m['raw_newest'] or '-'):>22s}  "
              f"{m['note'][:44]}")

# COMMAND ----------
# ============================== CELL 6 : SUMMARY + WHAT TO RUN NEXT ==============================
from collections import Counter
cnt = Counter(m["verdict"] for m in MAP)
print("=" * 90)
print("CHICAGO BRONZE COVERAGE SUMMARY")
print("=" * 90)
for v in ORDER:
    if cnt.get(v):
        print(f"  {v:20s} {cnt[v]:>4d}")
print(f"  {'TOTAL':20s} {len(MAP):>4d}")

oracle_scope = cnt.get("LOADED", 0) + cnt.get("PENDING_NB16", 0) + cnt.get("PENDING_NB20", 0) \
             + cnt.get("BLOCKED_STRATEGY", 0) + cnt.get("DELEGATED", 0)
if oracle_scope:
    print(f"\nOracle-sourced, loadable: {oracle_scope}   "
          f"loaded {cnt.get('LOADED',0)} ({cnt.get('LOADED',0)/oracle_scope:.0%})")

print("\n" + "-" * 90)
print("WHAT MOVES EACH BUCKET")
print("-" * 90)
plan = [("PENDING_NB16",     "NB16 v4 — stage runs (E_HELD, B, D, C, F off-hours, G)"),
        ("PENDING_NB20",     "NB20  — set TABLES = FULL_DRAFTS, bootstrap + promote"),
        ("BLOCKED_STRATEGY", "decide a v2 strategy per table, update the contract, then NB20"),
        ("DELEGATED",        "ingest_use_txn_daily.py"),
        ("NOT_ORACLE",       "ServiceNow ingestion path (out of the Oracle loader's scope)"),
        ("LEGACY_ORPHAN",    "investigate — these have no owner-prefixed twin"),
        ("LEGACY_DUP",       "retire once nothing downstream joins on the short names"),
        ("EXCLUDED",         "nothing"), ("INFRA", "nothing"), ("LOADED", "nothing")]
for v, what in plan:
    if cnt.get(v):
        print(f"  {v:20s} {cnt[v]:>3d}  ->  {what}")

# the useful shortcut: the exact TABLES list to paste into NB20
nb20 = sorted(m["source"] for m in MAP if m["verdict"] == "PENDING_NB20")
if nb20:
    print("\n" + "-" * 90)
    print(f"PASTE INTO NB20 cell 1 — the {len(nb20)} tables it can take right now:")
    print("-" * 90)
    print("TABLES = [")
    for i in range(0, len(nb20), 3):
        print("    " + ", ".join(f'"{s}"' for s in nb20[i:i+3]) + ",")
    print("]")

try:
    (spark.createDataFrame([(m["verdict"], m["table"], m["source"], m["strategy"], m["status"],
                             str(m["bronze_rows"]), str(m["raw_newest"]), m["note"]) for m in MAP],
                           "verdict string, table string, source string, strategy string, "
                           "status string, bronze_rows string, raw_newest string, note string")
     .coalesce(1).write.mode("overwrite").option("header", True).csv(f"{OUT_DIR}/coverage_map"))
    print(f"\ncoverage map CSV -> {OUT_DIR}/coverage_map")
except Exception as e:
    print(f"\n(CSV save skipped: {str(e).splitlines()[0][:70]})")
print("\nNothing was changed. Re-run this after every load to watch the map move.")
