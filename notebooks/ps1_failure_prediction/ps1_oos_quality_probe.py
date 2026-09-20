# =============================================================================
# PS1 OOS-quality probe -- 20-Sep-2026
#
# WHAT THIS ANSWERS
#   silver.device_event_enriched carries several columns describing HOW an OOS Set
#   ended, and nothing in the repo consumes any of them (all zero references in
#   ps1_features.py):
#
#     AUTOMATIC_CLEAR_FLAG   did the device recover by itself?
#     MATCHED_FLAG           was the Set ever matched to a Clear?
#     CLEAR_EMPLOYEE_KEY     who cleared it -- non-null means a technician attended
#     CLEAR_DTM / CLEAR_EVENT_STATE_TYPE_NAME / duration_to_clear_min
#     EVENT_REASON_CODE_ID / EVENT_STATUS_PATH_NAME
#     EXTENDED_DATA_LONG / _SHORT / _STRING
#
#   The PS3 episode fact reports any_automatic_clear as 0.0% on all 51,557 episodes
#   across all three fleets. This probe distinguishes the three explanations:
#     (a) AUTOMATIC_CLEAR_FLAG is never populated in the source  -> Michael question
#     (b) it is populated but never true                         -> genuine, and odd
#     (c) PS3's aggregation loses it                             -> PS3 defect
#
# WHERE TO RUN
#   Inside any PS1 fleet notebook's kernel, AFTER Spark is up. Run cells 0-5 only
#   (config + Spark bootstrap, no ETL), then in a new cell:
#
#       exec(open("notebooks/ps1_failure_prediction/ps1_oos_quality_probe.py").read())
#
#   Use an idle space -- mars-train-ps4 -- so it does not contend with a training run.
#   Read-only. No writes, no Aurora, no registration.
# =============================================================================
from pyspark.sql import functions as F

PROBE_START = "2025-09-01"          # 12 months is ample for fill rates and much faster
PROBE_END = "2026-08-29"

_silver = globals().get("S3_SILVER_RUNTIME") or globals().get("s3_silver")
if not _silver:
    raise RuntimeError(
        "S3_SILVER_RUNTIME is not defined -- run the notebook's CELL 3 (config) first."
    )
print(f"[probe] silver : {_silver}")
print(f"[probe] window : {PROBE_START} -> {PROBE_END}\n")

dee = spark.read.parquet(f"{_silver}/device_event_enriched/")
lc = {c.casefold(): c for c in dee.columns}


def col(name):
    """Resolve a column case-insensitively; None if the export does not carry it."""
    return lc.get(name.casefold())


REQUIRED = ["transit_day", "mars_device_category", "EVENT_STATE_TYPE_NAME", "is_oos_event"]
missing = [c for c in REQUIRED if col(c) is None]
if missing:
    raise RuntimeError(f"device_event_enriched is missing {missing}; columns: {dee.columns[:25]}")

QUALITY = [
    "AUTOMATIC_CLEAR_FLAG", "MATCHED_FLAG", "CLEAR_EMPLOYEE_KEY", "CLEAR_DTM",
    "CLEAR_EVENT_STATE_TYPE_NAME", "duration_to_clear_min", "EVENT_REASON_CODE_ID",
    "EVENT_STATUS_PATH_NAME", "EXTENDED_DATA_LONG", "EXTENDED_DATA_SHORT",
    "EXTENDED_DATA_STRING", "COMPONENT_TYPE_NAME", "COMPONENT_SERIAL_NBR",
    "EMPLOYEE_KEY", "LOCAL_LOGON_ID", "SEVERITY",
]
present = [c for c in QUALITY if col(c)]
absent = [c for c in QUALITY if not col(c)]
print(f"[probe] present in the export ({len(present)}): {present}")
if absent:
    print(f"[probe] ABSENT from the export ({len(absent)}): {absent}")
print()

win = (F.to_date(F.col(col("transit_day"))).between(F.lit(PROBE_START), F.lit(PROBE_END)))
base = dee.where(win)
oos = base.where(F.col(col("is_oos_event")).eqNullSafe(True)
                 & (F.col(col("EVENT_STATE_TYPE_NAME")) == "Set"))

# ---------------------------------------------------------------------------
# 1. Is AUTOMATIC_CLEAR_FLAG populated AT ALL -- on every event, not just OOS Sets?
#    This is the (a)/(b) discriminator: a column that is 100% NULL was never
#    populated; one that is 0% true but 100% non-null is genuinely always false.
# ---------------------------------------------------------------------------
print("=" * 78)
print("1. AUTOMATIC_CLEAR_FLAG across ALL events in the window (not just OOS Sets)")
print("=" * 78)
acf = col("AUTOMATIC_CLEAR_FLAG")
if acf is None:
    print("  column is not in the export at all -> the silver build drops it")
else:
    r = base.agg(
        F.count(F.lit(1)).alias("rows"),
        F.count(F.col(acf)).alias("non_null"),
        F.sum(F.when(F.col(acf).cast("string").isin("true", "True", "1", "Y"), 1)
               .otherwise(0)).alias("truthy"),
        F.countDistinct(F.col(acf)).alias("distinct_vals"),
    ).first()
    print(f"  rows {r['rows']:,}   non-null {r['non_null']:,} "
          f"({r['non_null'] / max(1, r['rows']):.2%})   truthy {r['truthy']:,}   "
          f"distinct values {r['distinct_vals']}")
    print("  distinct values observed:")
    for x in base.select(F.col(acf).cast("string").alias("v")).groupBy("v").count() \
                 .orderBy(F.desc("count")).limit(10).collect():
        print(f"     {str(x['v'])[:40]:42s} {x['count']:,}")
    if r["non_null"] == 0:
        print("\n  VERDICT (a): never populated -> raise with Michael. An availability")
        print("  system in which no OOS ever auto-clears is not plausible, and it also")
        print("  weakens confidence in the Set/Clear pairing generally.")
    elif r["truthy"] == 0:
        print("\n  VERDICT (b): populated but never true -> genuine, and worth confirming")
        print("  with Cubic that manual clearance is by design.")
    else:
        print("\n  VERDICT (c): it IS true sometimes here -> PS3's episode aggregation is")
        print("  losing it. any_automatic_clear = 0.0% is a PS3 defect, not a data gap.")

# ---------------------------------------------------------------------------
# 2. Fill and spread of every quality column, on the OOS Set population
# ---------------------------------------------------------------------------
print("\n" + "=" * 78)
print("2. Quality columns on OOS 'Set' events")
print("=" * 78)
aggs = [F.count(F.lit(1)).alias("rows")]
for c in present:
    a = col(c)
    aggs.append(F.count(F.col(a)).alias(f"nn__{c}"))
    aggs.append(F.countDistinct(F.col(a)).alias(f"nd__{c}"))
row = oos.agg(*aggs).first()
n = row["rows"]
print(f"  OOS Set events in window: {n:,}\n")
print(f"  {'column':30s} {'non-null':>10s} {'fill':>8s} {'distinct':>9s}  verdict")
for c in present:
    nn, nd = row[f"nn__{c}"], row[f"nd__{c}"]
    fill = nn / max(1, n)
    if nn == 0:
        v = "EMPTY - never populated"
    elif nd <= 1:
        v = "CONSTANT - no signal"
    elif fill < 0.05:
        v = "too sparse to use"
    else:
        v = "USABLE"
    print(f"  {c:30s} {nn:10,} {fill:8.1%} {nd:9,}  {v}")

# ---------------------------------------------------------------------------
# 3. Does any usable flag actually discriminate the Ventra-KPI population?
#    A signal that splits KPI-counted from non-counted events is telling us
#    something the label does not already know.
# ---------------------------------------------------------------------------
print("\n" + "=" * 78)
print("3. Discrimination against the Ventra-KPI event, per fleet")
print("=" * 78)
KPI_BY_FLEET = {"GATE": "oos_counted_gate_kpi", "TVM": "oos_counted_fmvd_kpi",
                "VALIDATOR": "oos_counted_bus_kpi"}
cat = col("mars_device_category")
for fleet, kpiname in KPI_BY_FLEET.items():
    k = col(kpiname)
    if k is None:
        print(f"\n  {fleet}: {kpiname} not in the export -- skipped")
        continue
    sub = oos.where(F.col(cat) == fleet)
    exprs = [F.count(F.lit(1)).alias("rows"),
             F.sum(F.when(F.col(k).eqNullSafe(True), 1).otherwise(0)).alias("kpi_counted")]
    for c in ("MATCHED_FLAG", "CLEAR_EMPLOYEE_KEY", "CLEAR_DTM", "AUTOMATIC_CLEAR_FLAG"):
        a = col(c)
        if not a:
            continue
        cond = (F.col(a).isNotNull() if c in ("CLEAR_EMPLOYEE_KEY", "CLEAR_DTM")
                else F.col(a).cast("string").isin("true", "True", "1", "Y"))
        exprs.append(F.sum(F.when(cond, 1).otherwise(0)).alias(f"n__{c}"))
        exprs.append(F.sum(F.when(cond & F.col(k).eqNullSafe(True), 1)
                            .otherwise(0)).alias(f"k__{c}"))
    r = sub.agg(*exprs).first()
    rows, kc = r["rows"], r["kpi_counted"]
    if not rows:
        print(f"\n  {fleet}: no rows")
        continue
    print(f"\n  {fleet}  OOS Sets {rows:,}   KPI-counted {kc:,} ({kc / rows:.1%})")
    print(f"    {'signal':24s} {'true':>10s} {'rate':>7s} {'KPI-rate|true':>14s}  lift")
    for c in ("MATCHED_FLAG", "CLEAR_EMPLOYEE_KEY", "CLEAR_DTM", "AUTOMATIC_CLEAR_FLAG"):
        if f"n__{c}" not in r.asDict():
            continue
        nt, kt = r[f"n__{c}"], r[f"k__{c}"]
        if nt == 0:
            print(f"    {c:24s} {0:10,} {0.0:7.1%} {'-':>14s}  never true")
            continue
        base_rate = kc / rows
        cond_rate = kt / nt
        lift = cond_rate / base_rate if base_rate else float("nan")
        print(f"    {c:24s} {nt:10,} {nt / rows:7.1%} {cond_rate:14.1%}  {lift:.2f}x")

# ---------------------------------------------------------------------------
# 4. What is actually inside EXTENDED_DATA_* -- an unexplored payload
# ---------------------------------------------------------------------------
print("\n" + "=" * 78)
print("4. EXTENDED_DATA_* content sample")
print("=" * 78)
for c in ("EXTENDED_DATA_STRING", "EXTENDED_DATA_SHORT", "EXTENDED_DATA_LONG"):
    a = col(c)
    if not a:
        print(f"  {c}: absent"); continue
    top = (oos.where(F.col(a).isNotNull())
              .select(F.col(a).cast("string").alias("v"))
              .groupBy("v").count().orderBy(F.desc("count")).limit(8).collect())
    if not top:
        print(f"  {c}: always NULL on OOS Sets")
        continue
    print(f"  {c}: top values")
    for x in top:
        print(f"     {str(x['v'])[:56]:58s} {x['count']:,}")

print("\n" + "=" * 78)
print("Read the verdict column in section 2 first. A column that is EMPTY or")
print("CONSTANT cannot help however good the idea behind it is -- that is the")
print("lesson requires_service_call taught (constant-true on 100% of TVM and")
print("VALIDATOR episodes). Section 3's lift is what says a signal carries")
print("information the Ventra-KPI rule does not already have.")
print("=" * 78)
