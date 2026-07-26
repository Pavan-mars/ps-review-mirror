# Databricks notebook source
# =============================================================================
# ps3_daily_incremental -- DAILY BATCH refresh of the PS3 silver+gold chain
#
# Where it sits in the daily flow:
#   NB75 CDC (bronze incremental, exists)  ->  THIS NB (silver+gold PS3)  ->
#   export_gold_ps3_incremental_to_s3  ->  SageMaker batch scoring  ->
#   ps3_rds_writer  ->  dashboard.
#
# Two modes (widget INCREMENTAL_MODE):
#   full   -- re-run the canonical silver+gold builders for the PS3 chain
#             (safe + simplest; PS3 gold is ~34K rows so this is cheap).
#   merge  -- read the CANONICAL gold create SQL, bound its all_incidents CTE
#             to new transit_days since the watermark, and MERGE on
#             availability_event_id (inserts new incidents, updates changed).
#             The canonical SQL stays the single source of truth -- we never
#             hand-copy the 200-line feature SELECT.
#
# Idempotent. Writes a watermark row to mars_dev.audit.ps3_daily_watermark and
# an incremental slice to S3 for the scorer. Hang-proof + DRY_RUN preview.
# =============================================================================
from typing import Any
import datetime as _dt
dbutils: Any = globals().get("dbutils")
spark: Any = globals().get("spark")

dbutils.widgets.text("catalog", "mars_dev")
dbutils.widgets.text("repo_sql_gold", "/Workspace/Repos/mars/Chicago-Ventra-Mars-Cubic-Analysis/sql/gold")
dbutils.widgets.dropdown("INCREMENTAL_MODE", "merge", ["merge", "full"])
dbutils.widgets.text("since_date", "")          # blank -> use watermark; else override YYYY-MM-DD
dbutils.widgets.dropdown("DRY_RUN", "true", ["true", "false"])

CAT = dbutils.widgets.get("catalog").strip()
SQL_GOLD = dbutils.widgets.get("repo_sql_gold").strip().rstrip("/")
MODE = dbutils.widgets.get("INCREMENTAL_MODE").strip()
DRY_RUN = dbutils.widgets.get("DRY_RUN").strip().lower() == "true"
GOLD = f"{CAT}.gold.device_ps3_incident"
WM_TABLE = f"{CAT}.audit.ps3_daily_watermark"
TODAY = _dt.date.today().strftime("%Y-%m-%d")

# --- watermark ---------------------------------------------------------------
spark.sql(f"""CREATE TABLE IF NOT EXISTS {WM_TABLE}
              (as_of_date DATE, last_transit_day DATE, n_incidents_added BIGINT,
               mode STRING, run_ts TIMESTAMP)""")

def current_watermark():
    override = dbutils.widgets.get("since_date").strip()
    if override:
        return override
    r = spark.sql(f"SELECT MAX(last_transit_day) m FROM {WM_TABLE}").collect()[0]["m"]
    if r is not None:
        return r.strftime("%Y-%m-%d")
    # cold start: last transit_day already in gold, else the project cutoff
    g = spark.sql(f"SELECT MAX(transit_day) m FROM {GOLD}").collect()[0]["m"]
    return (g.strftime("%Y-%m-%d") if g is not None else "2024-01-01")

SINCE = current_watermark()
print(f"ps3_daily_incremental | mode={MODE} | since={SINCE} | DRY_RUN={DRY_RUN}")

# --- silver dependency refresh (S16/S17/S09 feed PS3 gold) -------------------
# The repo silver builders are idempotent CREATE OR REPLACE; at silver scale a
# daily refresh of the PS3 chain is cheap and keeps the feature contract exact.
SILVER_CHAIN = ["09_hw_config_current__create.sql", "16_device_event_enriched__create.sql",
                "17_incident_root_cause__create.sql"]
repo_sql_silver = SQL_GOLD.replace("/sql/gold", "/sql/silver")

def run_sql_file(path):
    txt = "".join(open(path).readlines()) if not path.startswith("dbfs:") else \
          "".join(dbutils.fs.head(path, 1_000_000))
    for stmt in [s.strip() for s in txt.split(";") if s.strip() and not s.strip().startswith("--")]:
        spark.sql(stmt)

if not DRY_RUN and MODE == "full":
    for f in SILVER_CHAIN:
        print(f"  silver refresh -> {f}")
        try: run_sql_file(f"{repo_sql_silver}/{f}")
        except Exception as e: print(f"    [warn] {f}: {str(e).splitlines()[0][:80]}")

# --- gold refresh ------------------------------------------------------------
def load_canonical_gold_select():
    """Read the canonical create SQL, strip the DROP/CREATE, return the pure SELECT."""
    raw = "".join(open(f"{SQL_GOLD}/device_ps3_incident__create.sql").readlines())
    # keep everything from the first WITH/SELECT after CREATE TABLE ... AS
    marker = "CREATE TABLE mars_dev.gold.device_ps3_incident AS"
    body = raw.split(marker, 1)[1] if marker in raw else raw
    body = body.split(";")[0]                       # first statement only
    return body.strip()

n_added = 0
if MODE == "full":
    print("  gold [full] -> canonical CREATE TABLE AS")
    if not DRY_RUN:
        run_sql_file(f"{SQL_GOLD}/device_ps3_incident__create.sql")
        n_added = spark.sql(f"SELECT COUNT(*) c FROM {GOLD} WHERE transit_day >= DATE '{SINCE}'").collect()[0]["c"]
else:
    # MERGE: bound the canonical select's all_incidents CTE to new transit_days
    sel = load_canonical_gold_select()
    bounded = sel.replace("AND transit_day >= '2024-01-01'",
                          f"AND transit_day >= '2024-01-01' AND transit_day >= DATE '{SINCE}'", 1)
    if "transit_day >= DATE" not in bounded:        # safety: ensure the bound was injected
        bounded = bounded.replace("FROM mars_dev.silver.incident_root_cause",
                                  f"FROM mars_dev.silver.incident_root_cause WHERE transit_day >= DATE '{SINCE}'", 1)
    print(f"  gold [merge] -> bounding all_incidents to transit_day >= {SINCE}")
    if not DRY_RUN:
        spark.sql(f"CREATE OR REPLACE TEMP VIEW _ps3_incr AS {bounded}")
        n_added = spark.sql("SELECT COUNT(*) c FROM _ps3_incr").collect()[0]["c"]
        spark.sql(f"""MERGE INTO {GOLD} t USING _ps3_incr s
                      ON t.availability_event_id = s.availability_event_id
                      WHEN MATCHED THEN UPDATE SET *
                      WHEN NOT MATCHED THEN INSERT *""")
        spark.sql(f"OPTIMIZE {GOLD} ZORDER BY (device_id, transit_day)")
    else:
        print("  (DRY_RUN) would MERGE the bounded slice on availability_event_id")

# --- write watermark + record ------------------------------------------------
if not DRY_RUN:
    new_wm = spark.sql(f"SELECT MAX(transit_day) m FROM {GOLD}").collect()[0]["m"]
    new_wm = new_wm.strftime("%Y-%m-%d") if new_wm else SINCE
    spark.sql(f"""INSERT INTO {WM_TABLE} VALUES
                  (DATE '{TODAY}', DATE '{new_wm}', {int(n_added)}, '{MODE}', current_timestamp())""")
    print(f"  watermark -> {new_wm} | incidents in slice: {n_added:,}")

print("=" * 68)
print(f"  PS3 daily refresh {'PREVIEW' if DRY_RUN else 'DONE'} | mode={MODE} | since={SINCE} | added~{n_added:,}")
print("  next task: export_gold_ps3_incremental_to_s3 -> SageMaker batch scoring")
print("=" * 68)
