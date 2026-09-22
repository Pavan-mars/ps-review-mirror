# =============================================================================
# PS1 TVM duplicate-ingestion check -- 22-Sep-2026
#
# WHAT THIS ANSWERS
#   TVM KPI-counted OOS events run 60-72K a month, then spike to 186,505 /
#   192,513 / 127,188 in Dec-25 / Jan-26 / Feb-26 -- roughly 2.7x -- while the
#   device count holds at 465-469 and the event-code MIX barely moves.
#
#   A proportional inflation across every code, with the mix preserved, is what
#   DUPLICATE INGESTION looks like. Genuine fleet-wide degradation would normally
#   skew toward particular failure modes.
#
#   Three tests, because a reload can hide from the first one:
#     1. ROW vs DW_DEVICE_EVENT_ID   catches a straight re-insert
#     2. ROW vs NATURAL KEY          catches a reload that minted NEW surrogate
#                                    keys -- the DW ids all look distinct, but
#                                    (device, timestamp, event type) repeats
#     3. EDW_INSERTED_DTM spread     a backfill lands on one or two dates; normal
#                                    ingestion spreads across the month
#
#   Test 2 is the one that matters. Test 1 alone would clear a reload.
#
# WHERE TO RUN
#   Inside the TVM notebook's kernel, AFTER Spark is up. Run cells 0-5 only, then:
#
#       exec(open("ps1_tvm_duplicate_check.py").read())
#
#   Read-only. No writes, no Aurora, no registration.
# =============================================================================
from pyspark.sql import functions as F

FLEET = "TVM"
START, END = "2025-09-01", "2026-04-30"   # the spike plus baseline either side
SUSPECT = ("2025-12", "2026-01", "2026-02")

_silver = globals().get("S3_SILVER_RUNTIME") or globals().get("s3_silver")
if not _silver:
    raise RuntimeError("S3_SILVER_RUNTIME is not defined -- run CELL 3 first.")
print(f"[dupe] fleet   : {FLEET}")
print(f"[dupe] window  : {START} -> {END}")
print(f"[dupe] suspect : {', '.join(SUSPECT)}\n")

dee = spark.read.parquet(f"{_silver}/device_event_enriched/")
lc = {c.casefold(): c for c in dee.columns}
col = lambda n: lc.get(n.casefold())

C_DEV, C_DAY = col("DEVICE_ID"), col("transit_day")
C_CAT, C_STATE, C_OOS = col("mars_device_category"), col("EVENT_STATE_TYPE_NAME"), col("is_oos_event")
C_KPI, C_TYPE, C_DTM = col("oos_counted_fmvd_kpi"), col("EVENT_TYPE_ID"), col("EVENT_DTM")
C_ID = col("DW_DEVICE_EVENT_ID")
C_INS = col("EDW_INSERTED_DTM")
C_MSG = col("MESSAGE_ID")

for name, c in (("DW_DEVICE_EVENT_ID", C_ID), ("EVENT_DTM", C_DTM),
                ("EDW_INSERTED_DTM", C_INS)):
    if c is None:
        print(f"  !! {name} absent from the export -- that test will be skipped")

kpi = (dee.where(F.col(C_CAT) == FLEET)
          .where(F.to_date(F.col(C_DAY)).between(F.lit(START), F.lit(END)))
          .where(F.col(C_OOS).eqNullSafe(True) & (F.col(C_STATE) == "Set")))
if C_KPI:
    kpi = kpi.where(F.col(C_KPI).eqNullSafe(True))
kpi = kpi.withColumn("_m", F.date_format(F.to_date(F.col(C_DAY)), "yyyy-MM"))

# ---------------------------------------------------------------------------
# 1 + 2. Rows against the surrogate key AND against the natural key
# ---------------------------------------------------------------------------
print("=" * 92)
print("1+2. Rows vs surrogate key vs natural key, per month")
print("=" * 92)
nat = [c for c in (C_DEV, C_DTM, C_TYPE) if c]
aggs = [F.count(F.lit(1)).alias("rows")]
if C_ID:
    aggs.append(F.countDistinct(F.col(C_ID)).alias("dw_ids"))
aggs.append(F.countDistinct(F.concat_ws("|", *[F.col(c).cast("string") for c in nat]))
             .alias("nat_keys"))
if C_MSG:
    aggs.append(F.countDistinct(F.col(C_MSG)).alias("msg_ids"))

res = kpi.groupBy("_m").agg(*aggs).orderBy("_m").collect()
hdr = f"  {'month':8s} {'rows':>10s}"
if C_ID:
    hdr += f" {'dw_ids':>10s} {'dup x':>7s}"
hdr += f" {'nat_keys':>10s} {'dup x':>7s}  verdict"
print(hdr)
for r in res:
    rows, nk = r["rows"], r["nat_keys"]
    line = f"  {r['_m']:8s} {rows:10,}"
    if C_ID:
        line += f" {r['dw_ids']:10,} {rows / max(1, r['dw_ids']):7.2f}"
    nx = rows / max(1, nk)
    line += f" {nk:10,} {nx:7.2f}"
    mark = " <== SUSPECT" if r["_m"] in SUSPECT else ""
    line += ("  DUPLICATED" if nx > 1.05 else "  clean") + mark
    print(line)

print("\n  dup x near 1.00 on BOTH keys  -> the spike is real device behaviour")
print("  dup x > 1 on the natural key  -> reloaded rows, new surrogate keys")

# ---------------------------------------------------------------------------
# 3. Ingestion timing -- a backfill lands on one or two dates
# ---------------------------------------------------------------------------
if C_INS:
    print("\n" + "=" * 92)
    print("3. EDW_INSERTED_DTM spread -- backfills are concentrated, live feeds are not")
    print("=" * 92)
    ins = (kpi.withColumn("_ins", F.to_date(F.col(C_INS)))
              .groupBy("_m")
              .agg(F.countDistinct("_ins").alias("insert_days"),
                   F.min("_ins").alias("first"), F.max("_ins").alias("last"))
              .orderBy("_m").collect())
    print(f"  {'month':8s} {'insert days':>12s} {'first':>12s} {'last':>12s} {'top day share':>14s}")
    for r in ins:
        m = r["_m"]
        top = (kpi.where(F.col("_m") == m)
                  .withColumn("_ins", F.to_date(F.col(C_INS)))
                  .groupBy("_ins").count().orderBy(F.desc("count")).limit(1).collect())
        share = (top[0]["count"] / kpi.where(F.col("_m") == m).count()) if top else 0
        mark = " <== SUSPECT" if m in SUSPECT else ""
        note = "  BACKFILL SHAPE" if (r["insert_days"] <= 3 or share > 0.5) else ""
        print(f"  {m:8s} {r['insert_days']:12,} {str(r['first']):>12s} {str(r['last']):>12s} "
              f"{share:13.1%}{note}{mark}")

# ---------------------------------------------------------------------------
# 4. If the natural key duplicates, show what the repeats look like
# ---------------------------------------------------------------------------
print("\n" + "=" * 92)
print("4. Multiplicity of repeated natural keys in the suspect months")
print("=" * 92)
sus = kpi.where(F.col("_m").isin(*SUSPECT))
gk = (sus.groupBy(*[F.col(c).alias(f"k{i}") for i, c in enumerate(nat)])
         .agg(F.count(F.lit(1)).alias("n")))
hist = gk.groupBy("n").count().orderBy("n").limit(10).collect()
tot = sum(r["count"] for r in hist)
if tot:
    print(f"  {'copies':>7s} {'keys':>12s} {'share':>8s}")
    for r in hist:
        print(f"  {r['n']:7,} {r['count']:12,} {r['count'] / tot:8.1%}")
    worst = gk.where(F.col("n") > 1).count()
    print(f"\n  natural keys appearing more than once: {worst:,}")
    if worst:
        print("  sample:")
        for r in gk.where(F.col("n") > 1).orderBy(F.desc("n")).limit(5).collect():
            vals = " | ".join(str(r[f"k{i}"])[:24] for i in range(len(nat)))
            print(f"    {r['n']:>4} copies   {vals}")

print("\n" + "=" * 92)
print("If test 2 shows dup x ~1.00, the Dec-Feb spike is REAL and belongs in a")
print("question to Michael about what changed on the TVM fleet. If it shows > 1,")
print("it is a loading defect in our own pipeline and the affected months should")
print("be excluded from training until it is repaired.")
print("=" * 92)
