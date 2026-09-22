# =============================================================================
# PS1 TVM regime probe -- 22-Sep-2026
#
# WHAT THIS ANSWERS
#   TVM's monthly AUC splits into two regimes and no amount of feature work has
#   moved it:
#
#       2026-01  0.9401   555 positives   3.9% base
#       2026-02  0.9756   647            5.0%
#       2026-03  0.7350  1453           10.6%   <- the break
#       2026-05  0.7427  1051            7.7%
#       2026-07  0.8043  3187           23.2%
#
#   AUC tracks inversely with the monthly positive count, and the monthly base
#   rate swings 6x. So the first question is not "why did the model get worse"
#   but "what changed in the data".
#
#   Four candidates, and this separates them:
#     (A) FLEET      more devices reporting from March
#     (B) VOLUME     same devices, more events each
#     (C) MIX        different event codes firing -- a taxonomy or config change
#     (D) DENSITY    same failures, differently clustered, so the >3-day gap rule
#                    yields more episode STARTS from the same failure days
#
#   (D) is the subtle one and the easiest to miss: episode starts are not
#   proportional to failures. A device failing continuously produces ONE start;
#   the same failure count spread out produces many.
#
# WHERE TO RUN
#   Inside the TVM notebook's kernel, AFTER Spark is up. Run cells 0-5 only, then:
#
#       exec(open("ps1_tvm_regime_probe.py").read())
#
#   The bare filename is correct -- a notebook's working directory is its own folder.
#   Read-only. No writes, no Aurora, no registration.
# =============================================================================
import calendar

from pyspark.sql import functions as F
from pyspark.sql.window import Window

FLEET = "TVM"
START, END = "2023-07-01", "2026-08-29"      # THREE winters: the duplicate check
                                             # cleared ingestion, so the Dec-Feb spike
                                             # is real. If it recurs every winter it is
                                             # seasonal, not an incident.
GAP_DAYS = 3                                  # the label's sessionisation rule

_silver = globals().get("S3_SILVER_RUNTIME") or globals().get("s3_silver")
if not _silver:
    raise RuntimeError("S3_SILVER_RUNTIME is not defined -- run CELL 3 first.")
print(f"[probe] fleet  : {FLEET}")
print(f"[probe] silver : {_silver}")
print(f"[probe] window : {START} -> {END}\n")

dee = spark.read.parquet(f"{_silver}/device_event_enriched/")
lc = {c.casefold(): c for c in dee.columns}
col = lambda n: lc.get(n.casefold())

C_DEV, C_DAY = col("DEVICE_ID"), col("transit_day")
C_CAT, C_STATE = col("mars_device_category"), col("EVENT_STATE_TYPE_NAME")
C_OOS, C_HW = col("is_oos_event"), col("is_hardware_oos_event")
C_KPI = col("oos_counted_fmvd_kpi")            # TVM's Ventra KPI flag
C_TYPE, C_COMP = col("EVENT_TYPE_ID"), col("COMPONENT_TYPE_NAME")

base = (dee.where(F.col(C_CAT) == FLEET)
           .where(F.to_date(F.col(C_DAY)).between(F.lit(START), F.lit(END)))
           .withColumn("_m", F.date_format(F.to_date(F.col(C_DAY)), "yyyy-MM"))
           .withColumn("_d", F.to_date(F.col(C_DAY))))
oos = base.where(F.col(C_OOS).eqNullSafe(True) & (F.col(C_STATE) == "Set"))
kpi = oos.where(F.col(C_KPI).eqNullSafe(True)) if C_KPI else oos.where(F.lit(False))

# ---------------------------------------------------------------------------
# 1. Fleet, volume and the sessionisation yield, month by month
# ---------------------------------------------------------------------------
print("=" * 96)
print("1. What changed -- fleet size, event volume, and episode-start yield")
print("=" * 96)

monthly = (kpi.groupBy("_m")
              .agg(F.countDistinct(C_DEV).alias("devices"),
                   F.count(F.lit(1)).alias("kpi_oos_events"),
                   F.countDistinct(F.concat_ws("|", F.col(C_DEV), F.col("_d"))).alias("failure_days"))
              .orderBy("_m"))

# episode starts: a failure day more than GAP_DAYS after the device's previous one
fd = kpi.select(F.col(C_DEV).alias("dev"), F.col("_d").alias("d")).distinct()
w = Window.partitionBy("dev").orderBy("d")
starts = (fd.withColumn("_prev", F.lag("d").over(w))
            .where(F.col("_prev").isNull()
                   | (F.datediff(F.col("d"), F.col("_prev")) > GAP_DAYS))
            .withColumn("_m", F.date_format(F.col("d"), "yyyy-MM"))
            .groupBy("_m").agg(F.count(F.lit(1)).alias("episode_starts")))

rows = (monthly.join(starts, "_m", "left").orderBy("_m").collect())

# Three different rates, and keeping them apart is the point of this table.
#   ev/fail-day  events / failure-days. The denominator is ENDOGENOUS -- it is
#                derived from the same event stream -- so it moves when failure
#                days move, not only when volume moves. It is NOT fleet exposure.
#                An earlier revision printed this column under the heading
#                "ev/dev-day", which is exactly what it is not: Nov-2025 reads as
#                a 1.3x spike on it while true exposure is flat, +5% on October.
#   ev/dev-day   events / (devices x calendar days). The true fleet exposure.
#   saturation   failure-days / (devices x calendar days). The share of the fleet
#                sitting in a failed state, and the one that governs the LABEL:
#                above ~0.85 the >3-day gap rule can barely fire, episode starts
#                collapse, and any AUC measured there rests on a near-empty
#                positive class.
print(f"  {'month':8s} {'devices':>8s} {'KPI OOS':>10s} {'fail days':>10s} {'starts':>8s} "
      f"{'ev/fail-day':>12s} {'ev/dev-day':>11s} {'saturation':>11s} {'start yield':>12s}")
prev_yield = None
for r in rows:
    ev, fdys = r["kpi_oos_events"], r["failure_days"]
    st = r["episode_starts"] or 0
    _dim = calendar.monthrange(int(r["_m"][:4]), int(r["_m"][5:7]))[1]
    _dev_days = r["devices"] * _dim
    per = ev / fdys if fdys else 0                    # endogenous denominator
    expo = ev / _dev_days if _dev_days else 0         # true fleet exposure
    sat = fdys / _dev_days if _dev_days else 0        # fleet share in a failed state
    yld = st / fdys if fdys else 0
    flag = ""
    if sat > 0.85:
        flag = "  <== SATURATED: label degenerate"
    elif prev_yield and prev_yield > 0 and abs(yld - prev_yield) / prev_yield > 0.35:
        flag = "  <== SHIFT"
    prev_yield = yld
    print(f"  {r['_m']:8s} {r['devices']:8,} {ev:10,} {fdys:10,} {st:8,} "
          f"{per:12.2f} {expo:11.2f} {sat:10.1%} {yld:12.3f}{flag}")

print("\n  devices      -> (A) fleet change")
print("  ev/dev-day   -> (B) volume change. Read THIS, never ev/fail-day.")
print("  saturation   -> above ~0.85 the >3-day gap rule cannot fire. A month flagged")
print("                  SATURATED must not be pooled into a headline AUC.")
print("  start yield  -> (D) DENSITY: starts per failure day. Falling yield means")
print("                  failures are clustering, so the >3-day gap suppresses starts.")

# ---------------------------------------------------------------------------
# 2. Event-code mix -- did a different set of codes start firing?
# ---------------------------------------------------------------------------
print("\n" + "=" * 96)
print("2. (C) MIX -- share of KPI-counted OOS by event code, per month")
print("=" * 96)
top = [r[0] for r in (kpi.groupBy(C_TYPE).count()
                         .orderBy(F.desc("count")).limit(6).collect())]
# cast to string FIRST: EVENT_TYPE_ID is an int, so a bare otherwise("other")
# makes Spark cast the literal to double and the stage dies on CAST_INVALID_INPUT.
_code_s = F.col(C_TYPE).cast("string")
_top_s = [str(t) for t in top]
share = (kpi.withColumn("_code", F.when(_code_s.isin(_top_s), _code_s)
                                  .otherwise(F.lit("other")))
            .groupBy("_m", "_code").count())
tot = kpi.groupBy("_m").agg(F.count(F.lit(1)).alias("t"))
pv = (share.join(tot, "_m")
           .withColumn("pct", F.round(100.0 * F.col("count") / F.col("t"), 1))
           .select("_m", "_code", "pct").orderBy("_m", "_code").collect())
months = sorted({r["_m"] for r in pv})
codes = sorted({str(r["_code"]) for r in pv})
print(f"  {'month':8s}" + "".join(f"{c:>10s}" for c in codes))
for m in months:
    d = {str(r["_code"]): r["pct"] for r in pv if r["_m"] == m}
    print(f"  {m:8s}" + "".join(f"{d.get(c, 0.0):>10.1f}" for c in codes))
print("\n  A column appearing or vanishing at the break is a taxonomy or device")
print("  configuration change, not a model problem.")

# ---------------------------------------------------------------------------
# 3. Did the FEATURE side degrade at the same moment?
# ---------------------------------------------------------------------------
print("\n" + "=" * 96)
print("3. Feature-side coverage by month -- metric_daily is frozen at 11-Apr for TVM")
print("=" * 96)
for tbl, dcol in (("metric_daily", "transit_day"), ("kpi_avail_enriched", "transit_day"),
                  ("warnings_daily", "event_date"), ("usage_lifecycle_daily", "transit_day")):
    try:
        t = spark.read.parquet(f"{_silver}/{tbl}/")
        tl = {c.casefold(): c for c in t.columns}
        dv, dy = tl.get("device_id"), tl.get(dcol.casefold())
        ct = tl.get("mars_device_category")
        if not (dv and dy):
            print(f"  {tbl:24s} no DEVICE_ID/{dcol} -- skipped"); continue
        q = t
        if ct:
            q = q.where(F.col(ct) == FLEET)
        q = (q.where(F.to_date(F.col(dy)).between(F.lit(START), F.lit(END)))
              .withColumn("_m", F.date_format(F.to_date(F.col(dy)), "yyyy-MM"))
              .groupBy("_m").agg(F.countDistinct(dv).alias("devices")).orderBy("_m"))
        got = {r["_m"]: r["devices"] for r in q.collect()}
        line = "  ".join(f"{m[-2:]}:{got.get(m, 0):>4}" for m in months)
        print(f"  {tbl:24s} devices/month  {line}")
    except Exception as exc:
        print(f"  {tbl:24s} skipped ({type(exc).__name__})")

print("\n" + "=" * 96)
print("Read the START YIELD column first. If it moves at the break while devices and")
print("events per device-day hold steady, the regime change is (D) -- the same")
print("failures clustered differently, and the >3-day gap rule turning that into a")
print("different number of episode starts. That is a property of the LABEL, not of")
print("the model or the data feed, and no feature will fix it.")
print("=" * 96)
