# Databricks notebook source
# MAGIC %md
# MAGIC # 16 v2 — SELECTIVE INCREMENTAL LOAD + VERIFY + FROZEN-SOURCE REPORT
# MAGIC
# MAGIC Loads the post-11-Apr-2026 increment in **stages**, and measures how far each source
# MAGIC actually goes — because the 09-Sep dry run proved three of the largest tables are frozen
# MAGIC upstream and no loader can fix that.
# MAGIC
# MAGIC ### v4 — the three client-dependency tables are back in scope
# MAGIC `cashbox_tracking`, `device_end_of_day` and `device_metric` now load. Our own evidence
# MAGIC settled both open questions, and Cubic's answer affects **silver interpretation**, not
# MAGIC whether bronze should hold the rows. Two things still apply:
# MAGIC - **cashbox needs the contract fix first** — the cell-1 gate removes it from the run and
# MAGIC   prints the `UPDATE` until `dedupe_key='-'`, because anti-joining on a 7-value type code
# MAGIC   would discard nearly every new row.
# MAGIC - **`device_metric`'s 25 future-dated rows WILL land** (their `EDW_INSERTED_DTM` is normal)
# MAGIC   and must be excluded in silver. `device_end_of_day`'s 73 will NOT — chunking is capped at
# MAGIC   today, so they are never selected.
# MAGIC
# MAGIC ### v3 fixes from the 10-Sep A_FAST run
# MAGIC - **`fetchsize=10000`** — the run moved ~450 rows/sec because Oracle JDBC fetches ~10 rows
# MAGIC   per round trip by default. This is the main reason A_FAST took 2.7 h for 4.5M rows.
# MAGIC - **Typed nulls in `align_to`** — a bronze column absent from the Oracle source became an
# MAGIC   untyped `NullType`, which the Parquet writer rejects. That is what killed
# MAGIC   `edw_read_transaction`; it is now cast to the bronze column's own type.
# MAGIC - **`_batch_id` vs `_raw_batch_id`** — bronze tables from the original loader carry
# MAGIC   `_raw_batch_id`. The engine now stamps whichever name the target has, so appended rows
# MAGIC   keep their lineage, and cell 5's batch checks no longer crash.
# MAGIC - Full error text is printed on failure instead of the first 90 characters.
# MAGIC
# MAGIC ### What this version does
# MAGIC 1. **Three tables held for the client.** `cashbox_tracking`, `device_end_of_day` and
# MAGIC    `device_metric` are deferred pending Cubic's response to the DQ proof pack, so nothing
# MAGIC    we load can be contradicted by their answer. Their re-entry notes are kept in `CAVEAT`
# MAGIC    and print each run; un-defer by deleting three lines in `DEFERRED`.
# MAGIC 2. **STAGE presets.** One switch sets `ONLY`/`SKIP`/timeouts, so the two heavy tables run
# MAGIC    alone and a long merge can never strand the quick wins.
# MAGIC 3. **Frozen-source detection.** Cell 3 measures days-stale per source, flags FROZEN, and
# MAGIC    Cell 6 writes a CSV you can attach to the Cubic thread.
# MAGIC 4. **Coverage ceiling.** Cell 6 states the date PS1-PS5 can actually be built to — the
# MAGIC    source limit, not the load date.
# MAGIC
# MAGIC ### Stages — run in order, verify between
# MAGIC | STAGE | covers | est. |
# MAGIC |---|---|---|
# MAGIC | `A_FAST` | 20 tables: 17 full + kpi_detail + sale_transaction + read_transaction | done 10-Sep |
# MAGIC | `E_HELD` | `cashbox_tracking` (~5.3M append) + `device_end_of_day` (~384K, unindexed scans) | 1-3 h |
# MAGIC | `B_DEVICE_EVENT` | `edw_device_event` alone (~21M+ merged into 228M) — the PS1-PS5 spine | long |
# MAGIC | `D_DEVICE_METRIC` | `edw_device_metric` alone (~103M window, append) — largest by rows | longest |
# MAGIC | `C_ABP_TAP` | `edw_abp_tap` alone (~36M+ merged into 693M) | long |
# MAGIC | `F_MSG_COUNT` | `..._msg_count` (159M rows, no index) — **off-hours only** | one pass |
# MAGIC | `G_NOOP_REFRESH` | the 4 frozen/unmaintained tables, for parity — optional | minutes |
# MAGIC
# MAGIC Cell 2 now prints a **coverage accounting**: every contract row is either in this stage or
# MAGIC named with the stage that covers it, so nothing can be quietly left behind. `ONLY` is an
# MAGIC explicit instruction — it overrides both deferral and the off-hours gate.
# MAGIC
# MAGIC Times depend entirely on what `fetchsize=10000` buys — measure on the `read_transaction`
# MAGIC rerun before committing a night to the heavy three.
# MAGIC
# MAGIC Set `STAGE`, set `DRY_RUN=False`, Run All. Cells 1-3 are read-only in both modes; the
# MAGIC 09-Sep dry run already covered every table, so a second dry pass is not needed.
# MAGIC
# MAGIC Reversible: raw is append-only; Cell 2 prints `RESTORE TABLE ... TO VERSION AS OF <v>`
# MAGIC for every table before any write. Do not run concurrently with NB05/NB13/NB15 on the VPN.

# COMMAND ----------
# ============================== CELL 1 : CONFIG + STAGE + RUN LIST ==============================
import datetime as _dt, time, json
from pyspark.sql import functions as F

DRY_RUN = True            # flip to False to apply
STAGE   = "E_HELD"        # A_FAST | E_HELD | B_DEVICE_EVENT | D_DEVICE_METRIC |
#                         C_ABP_TAP | F_MSG_COUNT | G_NOOP_REFRESH | ALL | CUSTOM
ONLY, SKIP = [], []       # only honoured when STAGE == "CUSTOM"
INCLUDE_OFFHOURS = False  # msg_count only; set by STAGE="F_MSG_COUNT"

CAT, BRONZE_DB = "mars_dev", "mars_dev.bronze"
RAW_ROOT   = "s3://cubic-mars-pm-s3-datalake-dev-raw-170202974600/chicago_ventra"
CONTRACT   = "mars_dev.audit.bronze_data_contract"
RUN_LOG    = "mars_dev.audit.incremental_run_log"
PROBE_TBL  = "mars_dev.audit.probe_results"
ODS_HOST, ODS_PORT, SCOPE_SECRET = "10.3.10.30", 1521, "cubic"
SDU         = 512
FLOOR       = "2026-04-11 00:00:00"
SCOPE_FLOOR = "2023-07-01"
SENTINEL    = (_dt.date.today() + _dt.timedelta(days=1)).strftime("%Y-%m-%d")
BATCH_ID    = _dt.datetime.now().strftime("%Y%m%d_%H%M%S")
TODAY       = _dt.date.today()
FROZEN_DAYS = 5           # a source with no change in this many days is reported FROZEN
# Oracle JDBC defaults to ~10 rows per network round trip. Over the VPN that capped the
# 10-Sep A_FAST run at roughly 450 rows/sec. Raising the fetch size cuts round trips by
# three orders of magnitude and is the single biggest lever on pull speed.
FETCH_SIZE  = 10000
AUDIT_DIR   = f"{RAW_ROOT}/_audit/nb16_{BATCH_ID}"

HEAVY = ["edw_device_event", "edw_device_metric", "edw_abp_tap"]
HELD    = ["ncs_stage_cashbox_tracking", "ncs_stage_device_end_of_day"]
MSGCNT  = ["ncs_stage_device_end_of_day_msg_count"]
NOOP    = ["cta_servicenow_availability_events", "cta_servicenow_data_from_jumpbox",
           "cta_kpi_monthly_summary", "cta_sldc_monthly_summary"]
STAGES = {
    "A_FAST":          {"only": [],      "skip": HEAVY + HELD, "qto": 1800, "off": False},
    "E_HELD":          {"only": HELD,    "skip": [], "qto": 3600, "off": False},
    "B_DEVICE_EVENT":  {"only": ["edw_device_event"],  "skip": [], "qto": 7200, "off": False},
    "D_DEVICE_METRIC": {"only": ["edw_device_metric"], "skip": [], "qto": 7200, "off": False},
    "C_ABP_TAP":       {"only": ["edw_abp_tap"],       "skip": [], "qto": 7200, "off": False},
    "F_MSG_COUNT":     {"only": MSGCNT,  "skip": [], "qto": 7200, "off": True},   # OFF-HOURS ONLY
    "G_NOOP_REFRESH":  {"only": NOOP,    "skip": [], "qto": 1800, "off": False},  # parity touch
    "ALL":             {"only": [],      "skip": [], "qto": 7200, "off": False},
}
if STAGE != "CUSTOM":
    _s = STAGES[STAGE]
    ONLY, SKIP = list(_s["only"]), list(_s["skip"])
    QUERY_TO, INCLUDE_OFFHOURS = _s["qto"], _s["off"]
else:
    QUERY_TO = 1800
READ_TO = str(max(2_100_000, (QUERY_TO + 600) * 1000))   # socket read timeout must exceed queryTimeout

# ---- DEFERRED ----
DEFERRED = {
    # The three former client-dependency tables are now IN SCOPE (v4). Our own evidence settled
    # both questions: CASHBOX_EVENT_ID is a type code, not a broken key, so plain append is
    # correct; and the future-dated rows are 25-in-103M and 73-in-17.5M. Cubic's answer changes
    # how we interpret them in silver, not whether bronze should hold them.
    # no new data at source - nothing to gain by loading these
    "cta_servicenow_availability_events": "source frozen 11-Apr (150d) - a re-pull returns the identical 375,578 rows",
    "cta_servicenow_data_from_jumpbox":   "source frozen 11-Apr (150d) - identical 604 rows; VARCHAR dates unresolved",
    "cta_kpi_monthly_summary":            "unmaintained at source (last MONTH_DTM Mar-2015)",
    "cta_sldc_monthly_summary":           "unmaintained at source (0 new rows since Apr)",
}
DELEGATED = {"edw_use_transaction_daily": "run ingest_use_txn_daily.py separately (append_aggregate)"}

# ---- CAVEATS: these tables load, but silver MUST honour these notes ----
CAVEAT = {
    "ncs_stage_cashbox_tracking":  "CASHBOX_EVENT_ID is a TYPE CODE (7 values, unchanged since 2021), not a "
                                   "unique key. Loads by plain APPEND - requires the contract fix below.",
    "edw_device_metric":           "25 rows (of ~103M) carry TRANSIT_DAY_KEY 20300816 from one 16-Aug batch. "
                                   "They land in year=2030 raw partitions - EXCLUDE in silver.",
    "ncs_stage_device_end_of_day": "73 rows carry TRANSIT_DAY_KEY after today (to 20340724), spread over 61 "
                                   "dates on BMV devices. TRANSIT_DAY agrees with the key - EXCLUDE in silver.",
}
CASHBOX = "ncs_stage_cashbox_tracking"
CASHBOX_FIX = (f"UPDATE {CONTRACT} SET dedupe_key='-', bronze_write='append' "
               f"WHERE `table`='{CASHBOX}' AND status='measured_v2';")

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
def ora(q, secs=None):
    c = _creds()
    return (spark.read.format("jdbc").option("url", c["url"]).option("dbtable", q)
            .option("user", c["user"]).option("password", c["pwd"])
            .option("driver", "oracle.jdbc.OracleDriver").option("queryTimeout", str(secs or QUERY_TO))
            .option("oracle.net.CONNECT_TIMEOUT", "10000").option("oracle.jdbc.ReadTimeout", READ_TO)
            .option("fetchsize", str(FETCH_SIZE))
            .option("sessionInitStatement", "ALTER SESSION SET NLS_DATE_FORMAT='YYYY-MM-DD HH24:MI:SS'")
            .load())

def save_csv(df, name):
    try:
        dbutils.fs.put(f"{AUDIT_DIR}/{name}", df.toPandas().to_csv(index=False), True)
        print(f"  saved {AUDIT_DIR}/{name}")
    except Exception as e:
        print(f"  (csv save failed for {name}: {str(e).splitlines()[0][:60]})")

def month_edges(start_iso, end_iso):
    out, d = [], _dt.date.fromisoformat(start_iso[:10])
    end = _dt.date.fromisoformat(end_iso[:10])
    while d < end:
        nxt = (d.replace(day=1) + _dt.timedelta(days=32)).replace(day=1)
        out.append((d.isoformat(), min(nxt, end).isoformat())); d = min(nxt, end)
    return out

def _col(df, n): return {c.lower(): c for c in df.columns}.get(n.lower()) if n and n != "-" else None

def _iso(v):
    if v is None: return None
    s = str(v).split(".")[0]
    return f"{s[:4]}-{s[4:6]}-{s[6:8]}" if len(s) == 8 and s.isdigit() else s[:10]

def audit_and_ymd(src, scope_col, owner_table):
    sc = _col(src, scope_col)
    out = (src.withColumn("_ingest_ts", F.current_timestamp())
              .withColumn("_batch_id", F.lit(BATCH_ID))
              .withColumn("_source_system", F.lit("chicago_oracle_ods"))
              .withColumn("_source_table", F.lit(owner_table)))
    if sc and dict(src.dtypes).get(sc, "") in ("timestamp", "date"):
        return (out.withColumn("year", F.year(sc)).withColumn("month", F.month(sc))
                   .withColumn("day", F.dayofmonth(sc)))
    if sc:
        k = F.col(sc).cast("long")
        return (out.withColumn("year", (k / 10000).cast("int"))
                   .withColumn("month", ((k % 10000) / 100).cast("int"))
                   .withColumn("day", (k % 100).cast("int")))
    d = F.current_date()
    return (out.withColumn("year", F.year(d)).withColumn("month", F.month(d)).withColumn("day", F.dayofmonth(d)))

def align_to(df, target_cols, target_types=None):
    """Project df onto target_cols. A column absent from df is filled with a TYPED null -
    an untyped F.lit(None) is NullType, which the Parquet writer refuses outright (this is
    what failed edw_read_transaction on 10-Sep)."""
    have = {c.lower(): c for c in df.columns}
    tt = {k.lower(): v for k, v in (target_types or {}).items()}
    out = []
    for c in target_cols:
        if c.lower() in have:
            out.append(F.col(have[c.lower()]).alias(c))
        else:
            out.append(F.lit(None).cast(tt.get(c.lower(), "string")).alias(c))
    return df.select(*out)

def batch_col(bdf):
    """Bronze tables built by the original loader carry `_raw_batch_id`; ours writes
    `_batch_id`. Use whichever the target actually has so lineage is never lost."""
    for c in ("_batch_id", "_raw_batch_id"):
        got = _col(bdf, c)
        if got:
            return got
    return None

# ---- run list ----
ALLC = [r.asDict() for r in spark.table(CONTRACT).where("status = 'measured_v2'").collect()]
# Naming a table in ONLY is an explicit instruction: deferral and the off-hours gate do not
# apply to it. Only the retired table and the delegated aggregate are never selectable here.
EXPLICIT = set(ONLY)
CON = []
for c in ALLC:
    t = c["table"]
    if c["load_strategy"] in ("drop_from_scope", "append_aggregate"):
        continue                                   # retired, or owned by ingest_use_txn_daily.py
    if t in EXPLICIT:
        CON.append(c); continue                    # named explicitly - load it
    if EXPLICIT:
        continue                                   # ONLY is set and this table is not in it
    if t in DEFERRED:
        continue
    if c.get("chunking") == "single_pass_offhours" and not INCLUDE_OFFHOURS:
        continue
    CON.append(c)

# ---- SAFETY GATE: cashbox must not be loaded while the contract still anti-joins on a type code ----
CASHBOX_BLOCKED = False
_cb = [c for c in CON if c["table"] == CASHBOX]
if _cb and str(_cb[0].get("dedupe_key") or "").strip() not in ("-", "", "None"):
    CASHBOX_BLOCKED = True
    CON = [c for c in CON if c["table"] != CASHBOX]
    print("!" * 100)
    print(f"BLOCKED: {CASHBOX} removed from this run.")
    print(f"  contract still has dedupe_key='{_cb[0].get('dedupe_key')}'. CASHBOX_EVENT_ID holds only 7 distinct")
    print("  values, so an anti-join on it would silently discard almost every new row. Apply this first:")
    print(f"\n  {CASHBOX_FIX}\n")
    print("!" * 100)

if SKIP: CON = [c for c in CON if c["table"] not in SKIP]
assert CON, f"Run list is empty for STAGE={STAGE} (cashbox blocked={CASHBOX_BLOCKED})."

_RANK = {"full": 0, "full_scoped": 1, "cdc_append": 2, "cdc_merge": 3}
def _order(c):
    return (9 + HEAVY.index(c["table"]), c["table"]) if c["table"] in HEAVY else \
           (_RANK.get(c["load_strategy"], 8), c["table"])
CON = sorted(CON, key=_order)

ora("(SELECT 1 OK FROM dual) q", 20).collect()
print(f"NB16 v2 | STAGE={STAGE} | DRY_RUN={DRY_RUN} | {len(CON)} tables | QUERY_TO={QUERY_TO}s | batch={BATCH_ID}")

# COMMAND ----------
# ============================== CELL 2 : RUN PLAN + CAVEATS + ROLLBACK MAP ==============================
print(f"RUN PLAN — STAGE={STAGE} ({len(CON)} tables, in execution order)")
for i, c in enumerate(CON, 1):
    flag = "  <-- CAVEAT" if c["table"] in CAVEAT else ""
    print(f"  {i:2d}. {c['table']:42s} [{c['load_strategy']}]{flag}")

print("\nCAVEATS - silver must honour these for any table marked IN RUN:")
for t, why in CAVEAT.items():
    mark = "IN RUN " if any(c["table"] == t for c in CON) else "not this stage"
    print(f"  [{mark:14s}] {t}\n                   {why}")

print("\nNOT IN THIS STAGE — and how each one gets covered:")
_cover = {}
for c in ALLC:
    t = c["table"]
    if any(x["table"] == t for x in CON):
        continue
    if c["load_strategy"] == "drop_from_scope":
        _cover[t] = "RETIRED - all rows Jan-2018, never ingest"
    elif c["load_strategy"] == "append_aggregate":
        _cover[t] = "run ingest_use_txn_daily.py separately"
    elif c.get("chunking") == "single_pass_offhours":
        _cover[t] = "STAGE='F_MSG_COUNT' (159M rows, no index - run off-hours)"
    elif t in DEFERRED:
        _cover[t] = f"STAGE='G_NOOP_REFRESH' if you want parity - {DEFERRED[t]}"
    elif t in HEAVY:
        _cover[t] = f"STAGE='{'B_DEVICE_EVENT' if t=='edw_device_event' else 'D_DEVICE_METRIC' if t=='edw_device_metric' else 'C_ABP_TAP'}'"
    elif t in HELD:
        _cover[t] = "STAGE='E_HELD'"
    else:
        _cover[t] = "STAGE='A_FAST'"
for t, how in sorted(_cover.items()):
    print(f"  -  {t:42s} {how}")
print(f"\nCOVERAGE: {len(CON)} in this stage + {len(_cover)} elsewhere = {len(ALLC)} contract rows.")

print("\nROLLBACK MAP (undo this run, per table):")
VERSIONS = {}
for c in CON:
    t = c["table"]
    try:
        v = spark.sql(f"DESCRIBE HISTORY {BRONZE_DB}.`{t}` LIMIT 1").first()["version"]
        n = spark.sql(f"SELECT COUNT(*) n FROM {BRONZE_DB}.`{t}`").first()["n"]
        VERSIONS[t] = {"version": v, "rows_before": n}
        print(f"  RESTORE TABLE {BRONZE_DB}.`{t}` TO VERSION AS OF {v};   -- rows now: {n:,}")
    except Exception as e:
        VERSIONS[t] = {"version": None, "rows_before": None}
        print(f"  -- {t}: no bronze table yet ({str(e).splitlines()[0][:50]})")

# COMMAND ----------
# ============================== CELL 3 : PREFLIGHT + FROZEN-SOURCE DETECTION ==============================
# For CDC tables the unbounded MAX on the indexed watermark is an instant index descent. It answers
# two different questions: how far BEHIND bronze is, and how STALE the source itself is.
PRE, FROZEN = {}, []
print(f"{'table':42s} {'strategy':12s} {'verdict':8s} detail")
print("-" * 125)
for c in CON:
    t, strat = c["table"], c["load_strategy"]
    owner, otab = c["source"].split(".", 1)
    pull_wm, scope_col = c.get("pull_wm"), c.get("scope_col")
    try:
        bdf = spark.table(f"{BRONZE_DB}.`{t}`")
        if strat in ("full", "full_scoped"):
            where = ""
            if strat == "full_scoped" and scope_col and scope_col != "-":
                where = f" WHERE {scope_col} >= DATE '{SCOPE_FLOOR}' AND {scope_col} < DATE '{SENTINEL}'"
            n_ora = int(ora(f"(SELECT COUNT(*) N FROM {owner}.{otab}{where}) q", 600).collect()[0]["N"])
            n_b = bdf.count()
            PRE[t] = {"mode": "full", "ora_count": n_ora, "bronze_before": n_b}
            print(f"{t:42s} {strat:12s} {'READY':8s} oracle={n_ora:,}  bronze={n_b:,}  delta={n_ora - n_b:+,}")
        else:
            wc = _col(bdf, pull_wm)
            daykey = wc is not None and dict(bdf.dtypes).get(wc, "") not in ("timestamp", "date")
            base = bdf.where(F.col(wc) < (int(SENTINEL.replace("-", "")) if daykey else F.lit(SENTINEL))) \
                      .agg(F.max(wc)).first()[0] if wc else None
            mx = ora(f"(SELECT MAX({pull_wm}) MX FROM {owner}.{otab}) q", 300).collect()[0]["MX"]
            b_iso, m_iso = _iso(base), _iso(mx)
            gap = (_dt.date.fromisoformat(m_iso) - _dt.date.fromisoformat(b_iso)).days if (b_iso and m_iso) else None
            future_src = bool(m_iso) and m_iso > TODAY.isoformat()
            stale = (TODAY - _dt.date.fromisoformat(m_iso)).days if m_iso else None
            # a day-key watermark polluted by future-dated rows cannot report freshness;
            # chunking is capped at today regardless, so just say so rather than mislead
            frozen = (stale is not None) and (not future_src) and stale >= FROZEN_DAYS
            if frozen:
                FROZEN.append({"table": t, "source": c["source"], "watermark": pull_wm,
                               "source_max": m_iso, "days_stale": stale})
            verdict = "SKIP" if (gap is not None and gap <= 0) else ("READY" if m_iso else "CHECK")
            PRE[t] = {"mode": "cdc", "bronze_base": str(base), "ora_max": str(mx), "ora_max_iso": m_iso,
                      "gap_days": gap, "days_stale": stale, "frozen": frozen,
                      "bronze_before": VERSIONS.get(t, {}).get("rows_before")}
            tag = (f"  ** SOURCE FROZEN {stale}d **" if frozen else
                   "  (max includes future-dated rows; chunking capped at today)" if future_src else "")
            print(f"{t:42s} {strat:12s} {verdict:8s} bronze {pull_wm}={b_iso}  source={m_iso}  "
                  f"behind={gap}d{tag}")
    except Exception as e:
        PRE[t] = {"mode": "error", "error": str(e).splitlines()[0][:90]}
        print(f"{t:42s} {strat:12s} {'ERROR':8s} {str(e).splitlines()[0][:60]}")

bad = [t for t, p in PRE.items() if p["mode"] == "error"]
print("-" * 125)
print(f"preflight: {len(PRE) - len(bad)}/{len(PRE)} ok" + (f"  |  ERRORS: {bad}" if bad else "  |  all clear"))

if FROZEN:
    print("\n" + "=" * 125)
    print(f"FROZEN SOURCES — {len(FROZEN)} table(s) have received no change for {FROZEN_DAYS}+ days.")
    print("No loader can fix this: bronze will be pulled current to the source, and the source has stopped.")
    print(f"{'table':42s} {'watermark':22s} {'last change':12s} days")
    for f in sorted(FROZEN, key=lambda x: -x["days_stale"]):
        print(f"  {f['table']:40s} {f['watermark']:22s} {f['source_max']:12s} {f['days_stale']:>4}")
    print("=" * 125)

# COMMAND ----------
# ============================== CELL 4 : THE LOAD ENGINE ==============================
RES = {}
def log(t, **kw):
    RES[t] = kw; print(f"  => {kw}")

T0 = time.time()
for c in CON:
    t, strat = c["table"], c["load_strategy"]
    owner, otab = c["source"].split(".", 1)
    src = c["source"]
    scope_col, pull_wm = c.get("scope_col"), c.get("pull_wm")
    mk, dk = c.get("merge_key"), c.get("dedupe_key")
    raw_dir = f"{RAW_ROOT}/{owner.lower()}/{otab.lower()}/"
    print(f"\n### {t}  [{strat}]  src={src}  (t+{int(time.time()-T0)}s)")
    if t in CAVEAT:
        print(f"    CAVEAT: {CAVEAT[t]}")
    try:
        bdf = spark.table(f"{BRONZE_DB}.`{t}`")

        # ---------------- FULL / FULL_SCOPED ----------------
        if strat in ("full", "full_scoped"):
            where = ""
            if strat == "full_scoped" and _col(bdf, scope_col):
                where = f" WHERE {scope_col} >= DATE '{SCOPE_FLOOR}' AND {scope_col} < DATE '{SENTINEL}'"
            n_ora = PRE.get(t, {}).get("ora_count")
            if n_ora is None:
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
        base_iso = FLOOR[:10] if base is None else _iso(base)
        # never chunk past what the source actually holds - saves empty round-trips on frozen tables
        src_max_iso = PRE.get(t, {}).get("ora_max_iso")
        end_iso = SENTINEL
        if src_max_iso:
            end_iso = min(SENTINEL, (_dt.date.fromisoformat(src_max_iso) + _dt.timedelta(days=1)).isoformat())
        chunks = month_edges(base_iso, end_iso)
        print(f"  base {pull_wm} = {base}  -> {len(chunks)} monthly chunks, {base_iso} .. {end_iso}"
              + (f"  (source stops {src_max_iso})" if src_max_iso and end_iso < SENTINEL else ""))

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
                    print(f"    {lo[:7]}: count timed out - the PULL still works chunked; proceeding is safe")
                continue
            t_c = time.time()
            pull = ora(f"(SELECT * FROM {owner}.{otab} WHERE {pred}) q").cache()
            n = pull.count()
            per_chunk[lo[:7]] = n; total_new += n
            if n == 0:
                pull.unpersist(); print(f"    {lo[:7]}: 0 rows"); continue
            btypes = dict(bdf.dtypes)
            biz_cols = [x for x in bdf.columns if not x.startswith("_")
                        and x.lower() not in ("year", "month", "day")]
            staged = audit_and_ymd(align_to(pull, biz_cols, btypes), scope_col, src)
            staged.write.mode("append").partitionBy("year", "month", "day").parquet(raw_dir)
            target_cols = bdf.columns
            staged_b = align_to(staged, target_cols, btypes)
            bc = batch_col(bdf)                      # stamp lineage under the name bronze uses
            if bc:
                staged_b = staged_b.withColumn(bc, F.lit(BATCH_ID))
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
            print(f"    {lo[:7]}: pulled {n:,} -> raw + bronze "
                  f"({'merge' if strat=='cdc_merge' else 'append'}) in {int(time.time()-t_c)}s")
        n_after = None if DRY_RUN else spark.sql(f"SELECT COUNT(*) n FROM {BRONZE_DB}.`{t}`").first()["n"]
        log(t, status=("WOULD load" if DRY_RUN else "LOADED"), base=str(base), new_rows=total_new,
            per_chunk=per_chunk, bronze_after=n_after)
    except Exception as e:
        full = str(e)
        print(f"    !! FULL ERROR for {t}:\n{full[:1500]}")
        log(t, status=f"ERROR: {full.splitlines()[0][:200]}")
print(f"\nengine done in {int(time.time()-T0)}s")

# COMMAND ----------
# ============================== CELL 5 : POST-LOAD VERIFICATION ==============================
VER = []
def vlog(t, check, res, detail):
    VER.append({"table": t, "check": check, "result": res, "detail": str(detail)})
    print(f"  {t:42s} {check:22s} {res:5s} {detail}")

if DRY_RUN:
    print("DRY_RUN - verification runs after the apply pass. It will check, per table:")
    print("  freshness (bronze watermark == the SOURCE's max, not today) | count reconciliation")
    print("  duplicate + NULL keys among this batch's rows | row accounting")
    print("  availability_relief restamp guard | future-key census on the caveat tables")
else:
    print(f"{'table':42s} {'check':22s} {'res':5s} detail"); print("-" * 115)
    for c in CON:
        t, strat = c["table"], c["load_strategy"]
        if not str(RES.get(t, {}).get("status", "")).startswith(("LOADED", "FULL-RELOADED")):
            continue
        bdf = spark.table(f"{BRONZE_DB}.`{t}`")
        pull_wm, mk, dk = c.get("pull_wm"), c.get("merge_key"), c.get("dedupe_key")
        if strat in ("full", "full_scoped"):
            n_b, n_o = RES[t].get("bronze_rows"), PRE.get(t, {}).get("ora_count")
            ok = RES[t].get("reconciled") is True
            vlog(t, "count_reconcile", "PASS" if ok else "WARN",
                 f"oracle={n_o:,} bronze={n_b:,} engine_reconciled={RES[t].get('reconciled')}")
            if t == "edw_availability_relief":
                nd = bdf.select(F.countDistinct(F.to_date("INSERTED_DTM")).alias("d")).first()["d"]
                vlog(t, "restamp_guard", "PASS" if nd > 1 else "FAIL",
                     f"{nd} distinct INSERTED_DTM dates (1 = wholesale restamp is back)")
            continue
        # ---- CDC ----
        wc = _col(bdf, pull_wm)
        daykey = wc is not None and dict(bdf.dtypes).get(wc, "") not in ("timestamp", "date")
        new_max = bdf.where(F.col(wc) < (int(SENTINEL.replace("-", "")) if daykey else F.lit(SENTINEL))) \
                     .agg(F.max(wc)).first()[0]
        nm, pm = _iso(new_max), PRE.get(t, {}).get("ora_max_iso")
        fresh = None if pm is None else (nm is not None and nm >= pm)
        stale = PRE.get(t, {}).get("days_stale")
        note = f" [source itself is {stale}d stale - this is the ceiling, not a load defect]" if (
            stale is not None and stale >= FROZEN_DAYS) else ""
        vlog(t, "caught_up_to_source", "PASS" if fresh else ("WARN" if fresh is None else "FAIL"),
             f"bronze {pull_wm} now={nm} vs source max={pm}{note}")
        keycols = [k.strip() for k in (mk if mk and mk != "-" else (dk or "")).split(",") if k.strip() and k.strip() != "-"]
        keycols = [_col(bdf, k) for k in keycols if _col(bdf, k)]
        bc = batch_col(bdf)
        if keycols and bc is None:
            vlog(t, "dup_keys_batch", "WARN", "no _batch_id/_raw_batch_id column - batch checks skipped")
            keycols = []
        if keycols:
            batch = bdf.where(F.col(bc) == BATCH_ID)
            null_cond = " OR ".join(f"`{k}` IS NULL" for k in keycols)
            agg = batch.agg(F.count(F.lit(1)).alias("n"),
                            F.sum(F.when(F.expr(null_cond), 1).otherwise(0)).alias("nulls")).first()
            n_batch, nulls = agg["n"], (agg["nulls"] or 0)
            if n_batch:
                if n_batch <= 20_000_000:
                    bkeys = batch.select(*keycols).distinct()
                    dups = (bdf.join(bkeys, on=keycols, how="inner").groupBy(*keycols).count()
                              .where("count > 1").count())
                    scope_note = "vs whole bronze"
                else:
                    dups = batch.groupBy(*keycols).count().where("count > 1").count()
                    scope_note = "within batch only"
                vlog(t, "dup_keys_batch", "PASS" if dups == 0 else "FAIL",
                     f"{dups} duplicated key group(s) among {n_batch:,} batch rows "
                     f"(key={','.join(keycols)}; {scope_note}; stamp={bc})")
                vlog(t, "null_keys_batch", "PASS" if nulls == 0 else "FAIL", f"{nulls} NULL-key batch rows")
            else:
                vlog(t, "dup_keys_batch", "PASS", "0 batch rows (nothing new this run)")
        pulled, before = RES[t].get("new_rows"), VERSIONS.get(t, {}).get("rows_before")
        after = RES[t].get("bronze_after")
        if isinstance(pulled, int) and before is not None and after is not None:
            delta = after - before
            vlog(t, "row_accounting", "PASS" if delta <= pulled else "FAIL",
                 f"bronze +{delta:,} vs pulled {pulled:,} (merge updates make delta < pulled)")
        # ---- caveat tables: census the future-dated rows now in bronze, for the silver filter ----
        if t in ("edw_device_metric", "ncs_stage_device_end_of_day"):
            kc = _col(bdf, "TRANSIT_DAY_KEY")
            if kc:
                tk = int(TODAY.strftime("%Y%m%d"))
                nf = bdf.where(F.col(kc) > tk).count()
                vlog(t, "future_key_census", "INFO", f"{nf} row(s) in bronze with {kc} > {tk} - EXCLUDE in silver")
    n_fail = sum(1 for v in VER if v["result"] == "FAIL")
    n_warn = sum(1 for v in VER if v["result"] == "WARN")
    print("-" * 115)
    print(f"verification: {len(VER)} checks | FAIL={n_fail} WARN={n_warn}")
    try:
        (spark.createDataFrame([("nb16_verify", _dt.datetime.utcnow().isoformat(),
                                 json.dumps({"batch": BATCH_ID, "stage": STAGE, "checks": VER,
                                             "frozen_sources": FROZEN}, default=str))],
                               "probe string, run_ts string, payload string")
         .write.format("delta").mode("append").saveAsTable(PROBE_TBL))
        print(f"verification payload -> {PROBE_TBL} (probe='nb16_verify', batch {BATCH_ID})")
    except Exception as e:
        print(f"(audit write skipped: {str(e).splitlines()[0][:70]})")

# COMMAND ----------
# ============================== CELL 6 : RUN LOG + COVERAGE CEILING + FROZEN EVIDENCE ==============================
rows = [(BATCH_ID, _dt.datetime.utcnow().isoformat(), t, str(DRY_RUN),
         str(VERSIONS.get(t, {}).get("version")), str(VERSIONS.get(t, {}).get("rows_before")),
         json.dumps(RES.get(t, {}), default=str)) for t in RES]
(spark.createDataFrame(rows, "batch_id string, run_ts string, table string, dry_run string, "
                             "bronze_version_before string, rows_before string, result string")
 .write.format("delta").mode("append").saveAsTable(RUN_LOG))
print(f"run log -> {RUN_LOG} (batch {BATCH_ID}, stage {STAGE})\n" + "=" * 105)
for t in [c["table"] for c in CON]:
    if t in RES:
        print(f"  {t:42s} {RES[t].get('status')}  new={RES[t].get('new_rows', '-')}")
print("=" * 105)

# ---- frozen-source evidence: printed, and saved as a CSV for the Cubic thread ----
if FROZEN:
    print("\nFROZEN SOURCES — evidence for Cubic (these are upstream stops, not load failures):")
    for f in sorted(FROZEN, key=lambda x: -x["days_stale"]):
        print(f"  {f['source']:34s} last change {f['source_max']}  ({f['days_stale']} days ago) "
              f"on {f['watermark']}")
    try:
        save_csv(spark.createDataFrame(
            [(f["table"], f["source"], f["watermark"], f["source_max"], int(f["days_stale"]),
              TODAY.isoformat()) for f in FROZEN],
            "bronze_table string, oracle_source string, change_column string, "
            "last_change_date string, days_since_change int, measured_on string"),
            "frozen_sources.csv")
    except Exception as e:
        print(f"  (csv skipped: {str(e).splitlines()[0][:60]})")

    spine = next((f for f in FROZEN if f["table"] == "edw_device_event"), None)
    if spine:
        print(f"\n  COVERAGE CEILING: EDW.DEVICE_EVENT is the event spine for PS1-PS5. With it frozen at")
        print(f"  {spine['source_max']}, silver and gold can only be built through {spine['source_max']}")
        print(f"  no matter how completely bronze loads. Daily inference cannot go live past that date")
        print(f"  until the feed resumes.")

errs = [t for t in RES if str(RES[t].get("status", "")).startswith("ERROR")]
if DRY_RUN:
    print(f"\nDRY RUN complete for STAGE={STAGE}. Set DRY_RUN=False and Run All to apply.")
elif errs:
    print(f"\nATTENTION: {len(errs)} table(s) errored: {errs} — rerun with STAGE='CUSTOM' and ONLY=[...]; "
          f"the others are safely loaded.")
else:
    n_fail = sum(1 for v in VER if v["result"] == "FAIL")
    print(f"\nSTAGE {STAGE} APPLIED. " + ("All verification checks pass."
          if n_fail == 0 else f"{n_fail} verification FAIL(s) above — investigate before silver."))
    nxt = {"A_FAST": "E_HELD", "E_HELD": "B_DEVICE_EVENT", "B_DEVICE_EVENT": "D_DEVICE_METRIC",
           "D_DEVICE_METRIC": "C_ABP_TAP", "C_ABP_TAP": None}.get(STAGE)
    print(f"NEXT: " + (f"set STAGE='{nxt}' and Run All." if nxt else
          "all stages done — then msg_count off-hours, ingest_use_txn_daily.py, then silver L1->L6 "
          "(fix S17 dedup first) and gold."))
if CASHBOX_BLOCKED:
    print(f"\nREMINDER: {CASHBOX} was skipped. Apply the contract fix, then rerun STAGE='CUSTOM' "
          f"with ONLY=['{CASHBOX}']:\n  {CASHBOX_FIX}")
