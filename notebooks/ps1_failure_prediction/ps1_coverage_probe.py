# =============================================================================
# PS1 device-day coverage probe -- 23-Sep-2026
#
# WHAT THIS ANSWERS
#   The PS1 label sessionises on a >3-CALENDAR-day gap, so a gap in the DATA FEED
#   is indistinguishable from a device recovering: when ingestion drops days, the
#   first failure day after the feed resumes is scored as a new episode start.
#   Four TVM months carry that signature (~1 manufactured start per device against
#   0.11-0.34 in normal months):
#
#       2024-03   953 starts  2.10/device
#       2025-03   482 starts  1.05/device
#       2025-11   671 starts  1.43/device
#       2026-04   505 starts  1.08/device       <- inside the Jan-Aug test window
#
#   Together ~28% of all 9,337 starts in the series.
#
#   The proposed fix counts OBSERVED days rather than calendar days. That requires
#   a coverage signal, and the signal has never been measured. THIS PROBE MEASURES
#   IT, and it is a genuine test that can come back negative:
#
#       If distinct devices reporting per day does NOT drop in those four months,
#       a fleet-level device-count floor would fire on nothing and the guard would
#       fail silently. That is the outcome to watch for.
#
#   The reason for doubt is concrete. In 2024-03 the loss was CODE-SELECTIVE --
#   codes 113/221 fell to 15-19% of neighbours while 222 held at 90-92% and 410 at
#   102-113%. Failure days fell 38%, but a device still emitting 222 and 410 is
#   still PRESENT in the all-events view. So the device-day may never have vanished.
#
#   Three candidate signals are therefore measured side by side, because they can
#   disagree and only one needs to work:
#
#     (1) FLEET   distinct devices reporting any event per day, against the median
#     (2) ROWS    total events per day  -- expected to be a POOR signal: volume
#                 swings 3.45x legitimately (the Dec-25 to Feb-26 excursion, with
#                 duplicate ingestion already ruled out)
#     (3) DEVICE  per-device days-present in the month -- catches a device going
#                 dark individually, which (1) averages away
#
# WHERE TO RUN
#   Inside any PS1 fleet notebook's kernel, AFTER Spark is up. Run the cells up to and
#   including the one that defines S3_SILVER_RUNTIME -- that is CELL 5 on GATE and TVM,
#   CELL 7 on VALIDATOR -- then:
#
#       exec(open("ps1_coverage_probe.py").read())
#
#   The bare filename is correct -- a notebook's working directory is its own folder.
#   FLEET follows the notebook's own DEVICE_CAT; override it below only to cross-check
#   one fleet from another fleet's kernel.
#   Read-only. No writes, no Aurora, no registration.
# =============================================================================
import calendar as _cal
import datetime as _dt

from pyspark.sql import functions as F
from pyspark.sql.window import Window

# Follow the notebook's own fleet unless overridden here.
FLEET = (globals().get("DEVICE_CATEGORY")
         or globals().get("DEVICE_CAT")
         or "TVM")
START, END = "2023-07-01", "2026-08-29"
SUSPECT = ("2024-03", "2025-03", "2025-11", "2026-04")
FLOORS = (0.9, 0.7, 0.5)          # fractions of the median device count
GAP_DAYS = 3                      # PS1_EVENT_SESSION_GAP_DAYS -- the label's own rule

_silver = globals().get("S3_SILVER_RUNTIME") or globals().get("s3_silver")
if not _silver:
    raise RuntimeError("S3_SILVER_RUNTIME is not defined -- run CELL 3 first.")
print(f"[coverage] fleet   : {FLEET}")
print(f"[coverage] silver  : {_silver}")
print(f"[coverage] window  : {START} -> {END}")
print(f"[coverage] suspect : {', '.join(SUSPECT)}\n")

dee = spark.read.parquet(f"{_silver}/device_event_enriched/")
lc = {c.casefold(): c for c in dee.columns}
col = lambda n: lc.get(n.casefold())

C_DEV, C_DAY = col("DEVICE_ID"), col("transit_day")
C_DTM = col("EVENT_DTM")

# DATE BASIS. The label sessionises on to_date(EVENT_DTM) -- ps1_features.py defines
# dee_dtm as EVENT_DTM and derives failure_date from it. transit_day is a DIFFERENT
# column: silver defines it as TO_DATE(CAST(EVENT_DAY_KEY AS STRING),'yyyyMMdd'), and
# the transit day rolls at the service boundary, not midnight. The two therefore
# disagree for every event in the overnight window.
#
# Coverage must be measured on the SAME basis the label uses, or the observed/unobserved
# boundary lands one day off at exactly the gap edges -- which is the only place any of
# this matters. So: EVENT_DTM, and section 0 measures how far the two actually diverge.
if C_DTM is None:
    raise RuntimeError("EVENT_DTM absent from the export -- cannot match the label's date basis")
C_CAT, C_STATE = col("mars_device_category"), col("EVENT_STATE_TYPE_NAME")

# Section 4 must reproduce the LABEL BUILDER's failure-day definition exactly, or its
# start counts will not reconcile with the real label. ps1_features.py uses
# is_hardware_oos_event AND state == "Set", then the fleet's Ventra KPI flag when
# PS1_EVENT_DEFINITION=ventra_kpi (which is what every recent run uses).
C_HW = col("is_hardware_oos_event")
KPI_FLAG_BY_FLEET = {"GATE": "oos_counted_gate_kpi",
                     "TVM": "oos_counted_fmvd_kpi",
                     "VALIDATOR": "oos_counted_bus_kpi"}
C_KPI = col(KPI_FLAG_BY_FLEET.get(FLEET.strip().upper(), "")) if FLEET else None
if C_HW is None:
    raise RuntimeError("is_hardware_oos_event absent from the export -- cannot match the label")
if C_KPI is None:
    print(f"  !! no Ventra KPI flag for fleet {FLEET!r}; section 4 uses hardware_oos only")

# NO current-device filter, NO OOS filter, NO Set filter. Coverage is about whether
# the FEED delivered anything for this fleet that day, not about what it said.
base = (dee.where(F.col(C_CAT) == FLEET)
           .where(F.to_date(F.col(C_DTM)).between(F.lit(START), F.lit(END)))
           .withColumn("_d", F.to_date(F.col(C_DTM)))
           .withColumn("_m", F.date_format(F.to_date(F.col(C_DTM)), "yyyy-MM")))

# ---------------------------------------------------------------------------
# 0. How far apart are the two date columns? If they never disagree the basis
#    question is moot; if they do, everything below had to be on EVENT_DTM.
# ---------------------------------------------------------------------------
if C_DAY:
    _cmp = (base.select(
                F.to_date(F.col(C_DTM)).alias("by_dtm"),
                F.to_date(F.col(C_DAY)).alias("by_transit"))
            .agg(F.count(F.lit(1)).alias("rows"),
                 F.sum(F.when(F.col("by_dtm") != F.col("by_transit"), 1)
                        .otherwise(0)).alias("disagree"),
                 F.sum(F.when(F.col("by_transit").isNull(), 1)
                        .otherwise(0)).alias("null_transit"))
            .collect()[0])
    _r, _dis = _cmp["rows"], _cmp["disagree"] or 0
    print(f"  date basis   : to_date(EVENT_DTM)   [matches the label builder]")
    print(f"  transit_day disagrees on {_dis:,} of {_r:,} rows ({_dis / _r if _r else 0:.2%})"
          f"; NULL transit_day on {_cmp['null_transit'] or 0:,}\n")

daily = (base.groupBy("_m", "_d")
             .agg(F.countDistinct(C_DEV).alias("devices"),
                  F.count(F.lit(1)).alias("rows"))
             .orderBy("_d"))
daily = daily.persist()
_n_days_present = daily.count()

med = daily.approxQuantile("devices", [0.5], 0.001)[0]
med_rows = daily.approxQuantile("rows", [0.5], 0.001)[0]
print(f"  days with at least one row : {_n_days_present:,}")
print(f"  median devices per day     : {med:,.0f}")
print(f"  median rows per day        : {med_rows:,.0f}\n")

# ---------------------------------------------------------------------------
# 1. Monthly coverage -- the decisive table
# ---------------------------------------------------------------------------
print("=" * 112)
print("1. Monthly device-day coverage. Does the feed actually go thin in the suspect months?")
print("=" * 112)

rows = daily.collect()
by_month = {}
for r in rows:
    by_month.setdefault(r["_m"], []).append((r["_d"], r["devices"], r["rows"]))

months = sorted(by_month)
print(f"  {'month':8s} {'cal':>4s} {'present':>8s} {'missing':>8s} "
      + "".join(f"{'thin<' + str(int(f * 100)) + '%':>10s}" for f in FLOORS)
      + f" {'min dev':>8s} {'med dev':>8s} {'rows':>12s}")
for m in months:
    y, mo = int(m[:4]), int(m[5:7])
    cal_days = _cal.monthrange(y, mo)[1]
    days = by_month[m]
    present = len(days)
    devs = sorted(d for _, d, _ in days)
    thin = [sum(1 for d in devs if d < f * med) for f in FLOORS]
    mdev = devs[len(devs) // 2] if devs else 0
    tot_rows = sum(r for _, _, r in days)
    mark = "  <== SUSPECT" if m in SUSPECT else ""
    # A month can be legitimately partial at the window edges.
    if m == months[0] or m == months[-1]:
        mark += "  (window edge, partial by construction)"
    print(f"  {m:8s} {cal_days:4d} {present:8d} {cal_days - present:8d} "
          + "".join(f"{t:10d}" for t in thin)
          + f" {devs[0] if devs else 0:8,} {mdev:8,} {tot_rows:12,}{mark}")

print("\n  missing  = calendar days with ZERO rows for this fleet. The cleanest gap signature.")
print("  thin<N%  = days present but below N% of the median device count.")
print("  READ THIS FIRST: if the four SUSPECT months show missing=0 and thin=0, then a")
print("  fleet-level device-count floor fires on NOTHING and signal (1) is the wrong one.")
print("  In that case the manufactured starts come from a code-selective loss that leaves")
print("  the device present, and the guard must key on signal (3) or on per-code coverage.")

# ---------------------------------------------------------------------------
# 2. The suspect months against their own neighbours
# ---------------------------------------------------------------------------
print("\n" + "=" * 112)
print("2. Each suspect month against the month either side -- a month-level median is")
print("   misleading across a multi-year upward drift, so compare locally")
print("=" * 112)
idx = {m: i for i, m in enumerate(months)}
print(f"  {'month':8s} {'role':>10s} {'present/cal':>12s} {'med dev':>9s} "
      f"{'vs nbr':>8s} {'rows':>12s} {'vs nbr':>8s}")
for s in SUSPECT:
    if s not in idx:
        print(f"  {s:8s}  ABSENT from the window")
        continue
    i = idx[s]
    trio = [months[i - 1] if i > 0 else None, s, months[i + 1] if i + 1 < len(months) else None]
    nbr_dev, nbr_rows = [], []
    for m in (trio[0], trio[2]):
        if m:
            d = sorted(x[1] for x in by_month[m])
            nbr_dev.append(d[len(d) // 2])
            nbr_rows.append(sum(x[2] for x in by_month[m]))
    base_dev = sum(nbr_dev) / len(nbr_dev) if nbr_dev else 0
    base_rows = sum(nbr_rows) / len(nbr_rows) if nbr_rows else 0
    for m, role in zip(trio, ("prior", "SUSPECT", "next")):
        if not m:
            continue
        y, mo = int(m[:4]), int(m[5:7])
        cd = _cal.monthrange(y, mo)[1]
        d = sorted(x[1] for x in by_month[m])
        md = d[len(d) // 2]
        rw = sum(x[2] for x in by_month[m])
        rel_d = f"{md / base_dev:7.2f}x" if base_dev else "      -"
        rel_r = f"{rw / base_rows:7.2f}x" if base_rows else "      -"
        print(f"  {m:8s} {role:>10s} {len(by_month[m]):5d}/{cd:<6d} {md:9,} "
              f"{rel_d:>8s} {rw:12,} {rel_r:>8s}")
    print()

# ---------------------------------------------------------------------------
# 3. Signal (3) -- per-device days-present, which a fleet average hides
# ---------------------------------------------------------------------------
print("=" * 112)
print("3. Per-device days-present per month. A fleet-level count stays flat if MOST")
print("   devices report; this catches a subset going dark.")
print("=" * 112)
dev_month = (base.groupBy("_m", C_DEV)
                 .agg(F.countDistinct("_d").alias("days_present")))
stats = (dev_month.groupBy("_m")
         .agg(F.countDistinct(C_DEV).alias("devices"),
              F.round(F.avg("days_present"), 2).alias("avg_days"),
              F.min("days_present").alias("min_days"),
              F.expr("percentile_approx(days_present, 0.1)").alias("p10_days"),
              F.expr("percentile_approx(days_present, 0.5)").alias("p50_days"))
         .orderBy("_m").collect())
print(f"  {'month':8s} {'cal':>4s} {'devices':>8s} {'avg days':>9s} {'p10':>6s} {'p50':>6s} "
      f"{'avg cover':>10s}")
for r in stats:
    m = r["_m"]
    y, mo = int(m[:4]), int(m[5:7])
    cd = _cal.monthrange(y, mo)[1]
    cover = (r["avg_days"] or 0) / cd
    mark = "  <== SUSPECT" if m in SUSPECT else ""
    print(f"  {m:8s} {cd:4d} {r['devices']:8,} {r['avg_days']:9.2f} "
          f"{r['p10_days']:6d} {r['p50_days']:6d} {cover:10.1%}{mark}")

print("\n  avg cover = mean share of the month's days on which a device reported anything.")
print("  A drop here in a suspect month, with fleet device count flat, means the loss is")
print("  per-device and the guard must be keyed per device, not fleet-wide.")

# ---------------------------------------------------------------------------
# 4. What a fleet-level floor would actually suppress
# ---------------------------------------------------------------------------
print("\n" + "=" * 112)
print("4. Consequence: episode starts that a fleet-level observed-day rule would suppress")
print("=" * 112)
oos = base.where(F.col(C_HW).eqNullSafe(True) & (F.col(C_STATE) == "Set"))
if C_KPI:
    oos = oos.where(F.col(C_KPI).eqNullSafe(True))
fd = oos.select(F.col(C_DEV).alias("dev"), F.col("_d").alias("d")).distinct()
w = Window.partitionBy("dev").orderBy("d")
starts = (fd.withColumn("_prev", F.lag("d").over(w))
            .withColumn("_gapdays", F.datediff(F.col("d"), F.col("_prev")))
            .where(F.col("_prev").isNull() | (F.col("_gapdays") > GAP_DAYS)))
# Persist: the loop below evaluates this once per floor, and without it each pass
# re-scans the whole category slice of device_event_enriched.
starts = starts.persist()
print(f"  episode starts under the CALENDAR rule: {starts.count():,}\n")

for floor in FLOORS:
    observed = {d for _, days in by_month.items() for d, dv, _ in days if dv >= floor * med}
    obs_b = spark.sparkContext.broadcast(observed)
    # This MUST be the rule ps1_features.py actually implements, or the numbers below
    # are not a prediction of anything. The guard builds an index that advances only on
    # observed days and suppresses a start when
    #       obs_idx(day) - obs_idx(previous failure day)  <=  GAP_DAYS
    # which is exactly: COUNT the observed days in (prev, day] and compare to the gap.
    #
    # An earlier revision of this probe used a cruder proxy -- "was ANY day inside the
    # preceding gap unobserved" -- which suppresses far more aggressively and does NOT
    # match the guard. A single unobserved day in a 20-day gap tripped the proxy while
    # the guard correctly keeps that start, because 19 observed days still clear the
    # 3-day rule. Do not reintroduce it.
    _chk = F.udf(lambda d, p: bool(p is not None and sum(
        1 for k in range(1, (d - p).days + 1)
        if (p + _dt.timedelta(days=k)) in obs_b.value) <= GAP_DAYS), "boolean")
    sus = starts.withColumn("_manuf", _chk(F.col("d"), F.col("_prev")))
    agg = (sus.withColumn("_m", F.date_format(F.col("d"), "yyyy-MM"))
              .groupBy("_m")
              .agg(F.count(F.lit(1)).alias("starts"),
                   F.sum(F.when(F.col("_manuf"), 1).otherwise(0)).alias("manufactured"))
              .orderBy("_m").collect())
    tot = sum(r["starts"] for r in agg)
    man = sum(r["manufactured"] for r in agg)
    print(f"\n  floor {floor:.0%} of median ({floor * med:,.0f} devices): "
          f"{man:,} of {tot:,} starts suppressed ({man / tot if tot else 0:.1%})")
    hits = [r for r in agg if r["manufactured"] > 0]
    if not hits:
        print("    nothing suppressed -- this floor is inert for this fleet")
        continue
    for r in sorted(hits, key=lambda x: -x["manufactured"])[:10]:
        mark = "  <== SUSPECT" if r["_m"] in SUSPECT else ""
        print(f"    {r['_m']:8s} {r['manufactured']:6,} of {r['starts']:6,} "
              f"({r['manufactured'] / r['starts']:5.1%}){mark}")

print("\n" + "=" * 112)
print("ACCEPTANCE TEST for the guard: suppression should CONCENTRATE in the four suspect")
print("months. If it is spread evenly across all 38 months, the floor is cutting real")
print("episodes, not manufactured ones, and it is set too high. If it suppresses nothing,")
print("the fleet-level signal is wrong and section 3 tells you whether a per-device rule")
print("would work instead.")
print("=" * 112)
daily.unpersist()
starts.unpersist()
