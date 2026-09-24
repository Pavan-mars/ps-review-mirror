# =============================================================================
# PS1 source-freshness + component-serial probe -- 24-Sep-2026
#
# TWO QUESTIONS, ONE READ-ONLY PASS.
#
# ---------------------------------------------------------------------------
# Q1. WHY DID THE GATE NOTEBOOK DEGRADE AFTER 12-APR?
#
#   PS1_3d_GATE_OOS_Optimized_SageMaker_v3_3 reads FOURTEEN sources from the S3
#   exports, with SOURCE_END_EXPR = current_date() -- it reads to today whatever the
#   source actually holds. We already know some of those sources stopped:
#
#     metric_daily + usage_lifecycle_daily  -> ZERO devices from 2026-05 (TVM, measured)
#     hw_config_current                     -> stale=True, watermark 2026-04-11
#     and four export-path breaks were recorded on 17-Sep while loaders stayed green
#
#   If the label window runs to 29-Aug while a chunk of the feature matrix stops at
#   11-Apr, every post-April row is scored on null or stale features. That is a
#   data-plumbing failure presenting as a model failure, and it fits "worked well
#   before the incremental update" exactly.
#
#   This checks BOTH the UC table AND the S3 export the notebook actually reads,
#   because those can disagree -- that is what an export-path break IS.
#
# ---------------------------------------------------------------------------
# Q2. CAN COMPONENT AGE AT FAILURE BE BUILT? (the thing nobody has tried)
#
#   COMPONENT_SERIAL_NBR exists in BOTH:
#     - silver.device_event_enriched   per EVENT  (which component failed, and when)
#     - silver.hw_config_current       per DEVICE-COMPONENT (when it was installed)
#
#   Nobody joins them on serial. gold.device_ps3_incident joins on DEVICE_ID and then
#   RANKS: priority 0 is a text LIKE on COMPONENT_DESCRIPTION against the incident's
#   affected_component -- which is 78.32% BLANK -- and the fallback for the other 78%
#   is "the most recently changed component". That is a guess, and because
#   hw_config_current is a CURRENT snapshot the guessed component may have been
#   installed AFTER the incident it is attributed to.
#
#   A serial-level join needs none of that:
#       event.COMPONENT_SERIAL_NBR = hw.COMPONENT_SERIAL_NBR
#       component_age_at_failure = DATEDIFF(event_date, hw.REPORTED_CHANGED_DTM)
#
#   That is a genuine wear proxy. PS1 has never had one; PS5 needs one and its
#   current component_age_days is DATEDIFF(CURRENT_DATE(), ...) -- age at BUILD time,
#   constant per component, growing daily against a snapshot frozen in April.
#
#   THE CATCH, AND IT IS WHY THIS IS A PROBE AND NOT A PATCH:
#     (a) cardinality. One prior reading put COMPONENT_SERIAL_NBR at only ~1,056
#         distinct values across 12.1M events. If true these are part/model numbers,
#         not instance serials, and a serial join fans out instead of identifying.
#         evq_comp_serials_* being SHAP #1 argues the column carries real signal --
#         both can be true if it behaves as a fine-grained TYPE. MEASURE IT.
#     (b) survivorship. hw_config_current only holds components still installed. An
#         event whose component was later swapped will NOT join. So a non-null age
#         silently means "this component survived to today", which is information
#         about the future. The VALUE is clean; the MISSINGNESS is not. Section 4
#         measures how large that bias could be before anyone builds a feature on it.
#
# WHERE TO RUN
#   Any PS1 notebook kernel after Spark is up, or a plain Databricks Python cell.
#   Read-only. No writes.
# =============================================================================
from pyspark.sql import functions as F

CATALOG = "mars_dev"
GOLD_S3 = "s3://cubic-mars-pm-s3-datalake-dev-gold-170202974600/chicago/gold"
SILVER_S3 = "s3://cubic-mars-pm-s3-datalake-dev-gold-170202974600/chicago/silver"

# the fourteen sources the GATE notebook reads, with the date column each is judged on
SOURCES = [
    ("gold",   "device_ps1_daily",                "transit_day"),
    ("gold",   "device_ps2_chains",               "transit_day"),
    ("gold",   "device_ps4_hourly",               "transit_day"),
    ("silver", "device_outage",                   "transit_day"),
    ("silver", "dim_event_matrix",                None),
    ("silver", "dim_event_type",                  None),
    ("silver", "tap_event_daily",                 "transit_day"),
    ("silver", "use_revenue_daily",               "transit_day"),
    ("silver", "metric_daily",                    "transit_day"),
    ("silver", "device_uptime_intervals",         "transit_day"),
    ("silver", "maintenance_ledger",              "ledger_date"),
    ("silver", "device_incident_features_daily",  "transit_day"),
    ("silver", "usage_lifecycle_daily",           "transit_day"),
    ("silver", "station_network_daily",           "transit_day"),
    ("silver", "device_event_enriched",           "transit_day"),
    ("silver", "hw_config_current",               "LAST_REPORTED_DTM"),
]

print("=" * 104)
print("1. SOURCE FRESHNESS -- UC TABLE vs the S3 EXPORT the notebook actually reads")
print("=" * 104)
print(f"  {'source':34s} {'UC max date':>13s} {'UC rows':>12s} {'S3 max date':>13s} {'S3 rows':>12s}  verdict")

rows = []
for layer, tbl, datecol in SOURCES:
    uc_max = uc_rows = s3_max = s3_rows = None
    try:
        df = spark.table(f"{CATALOG}.{layer}.{tbl}")
        uc_rows = df.count()
        if datecol:
            uc_max = df.agg(F.max(F.to_date(F.col(datecol)))).collect()[0][0]
    except Exception as exc:
        uc_rows = f"ERR {type(exc).__name__}"
    try:
        root = GOLD_S3 if layer == "gold" else SILVER_S3
        df3 = spark.read.parquet(f"{root}/{tbl}/")
        s3_rows = df3.count()
        if datecol:
            s3_max = df3.agg(F.max(F.to_date(F.col(datecol)))).collect()[0][0]
    except Exception as exc:
        s3_rows = f"ERR {type(exc).__name__}"

    verdict = ""
    if isinstance(uc_rows, int) and isinstance(s3_rows, int):
        if uc_max and s3_max and uc_max != s3_max:
            verdict = f"EXPORT STALE by {(uc_max - s3_max).days}d"
        elif uc_rows != s3_rows:
            verdict = f"row delta {uc_rows - s3_rows:+,}"
        else:
            verdict = "in sync"
    rows.append((tbl, s3_max))
    print(f"  {layer + '.' + tbl:34s} {str(uc_max):>13s} {str(uc_rows):>12s} "
          f"{str(s3_max):>13s} {str(s3_rows):>12s}  {verdict}")

_dates = [d for _, d in rows if d]
if _dates:
    print(f"\n  EARLIEST export max-date across all sources: {min(_dates)}")
    print("  Any source stopping before the label's 2026-08-29 end feeds NULL or stale")
    print("  features to every row after it. That is the degradation mechanism to rule in or out.")

# ---------------------------------------------------------------------------
print("\n" + "=" * 104)
print("2. IS COMPONENT_SERIAL_NBR AN INSTANCE SERIAL OR A PART NUMBER?")
print("=" * 104)
dee = spark.table(f"{CATALOG}.silver.device_event_enriched")
hw = spark.table(f"{CATALOG}.silver.hw_config_current")

for label, df, col, extra in (
        ("device_event_enriched", dee, "COMPONENT_SERIAL_NBR", "mars_device_category"),
        ("hw_config_current", hw, "COMPONENT_SERIAL_NBR", "mars_device_category")):
    n = df.count()
    nn = df.where(F.col(col).isNotNull()).count()
    nd = df.select(col).where(F.col(col).isNotNull()).distinct().count()
    ndev = df.select("DEVICE_ID").distinct().count()
    print(f"  {label:24s} rows {n:>12,}  non-null {nn:>12,} ({nn/n if n else 0:6.1%})"
          f"  distinct serials {nd:>8,}  devices {ndev:>7,}")
    print(f"  {'':24s} -> {nd/ndev if ndev else 0:.2f} distinct serials per device"
          f"   ({'looks like an INSTANCE serial' if (ndev and nd/ndev > 0.8) else 'looks like a PART/MODEL number -- a join on it will FAN OUT'})")

print("\n  Also: distinct COMPONENT_TYPE_NAME in the event stream, for contrast")
print(f"    {dee.select('COMPONENT_TYPE_NAME').where(F.col('COMPONENT_TYPE_NAME').isNotNull()).distinct().count():,} distinct types")

# ---------------------------------------------------------------------------
print("\n" + "=" * 104)
print("3. DO EVENT SERIALS JOIN TO hw_config_current? (the untried feature)")
print("=" * 104)
ev = (dee.where(F.col("COMPONENT_SERIAL_NBR").isNotNull())
        .select(F.col("DEVICE_ID").alias("d"),
                F.col("COMPONENT_SERIAL_NBR").alias("s"),
                F.to_date(F.col("EVENT_DTM")).alias("ev_day"),
                F.col("mars_device_category").alias("cat")))
hwj = (hw.where(F.col("COMPONENT_SERIAL_NBR").isNotNull())
         .select(F.col("DEVICE_ID").alias("hd"),
                 F.col("COMPONENT_SERIAL_NBR").alias("hs"),
                 F.to_date(F.col("REPORTED_CHANGED_DTM")).alias("installed")))

# join on DEVICE + SERIAL -- the tight key. A device-only join is what PS3 does and
# it is why PS3 has to guess.
j = ev.join(hwj, (F.col("d") == F.col("hd")) & (F.col("s") == F.col("hs")), "left")
tot = ev.count()
matched = j.where(F.col("hs").isNotNull()).count()
print(f"  events with a serial      : {tot:,}")
print(f"  joined on (device,serial) : {matched:,}  ({matched/tot if tot else 0:.1%})")
print(f"  fan-out factor            : {j.count()/tot if tot else 0:.3f}  (1.000 = clean, >1 = the serial is not unique per device)")

print("\n  per fleet:")
for cat in ("GATE", "TVM", "VALIDATOR"):
    sub = j.where(F.col("cat") == cat)
    st = sub.count(); sm = sub.where(F.col("hs").isNotNull()).count()
    print(f"    {cat:10s} {st:>12,} events, {sm:>12,} joined ({sm/st if st else 0:6.1%})")

# ---------------------------------------------------------------------------
print("\n" + "=" * 104)
print("4. THE SURVIVORSHIP BIAS -- how much future does a non-null age leak?")
print("=" * 104)
print("  hw_config_current holds only components STILL INSTALLED. An event whose")
print("  component was later swapped cannot join, so 'has an age' silently encodes")
print("  'this component survived to today'. If replacement correlates with failure,")
print("  that is future information. Measure it before building on it.\n")
aged = (j.where(F.col("installed").isNotNull())
         .withColumn("age_at_event", F.datediff(F.col("ev_day"), F.col("installed"))))
neg = aged.where(F.col("age_at_event") < 0).count()
pos = aged.where(F.col("age_at_event") >= 0).count()
print(f"  events with a computable age : {pos + neg:,}")
print(f"    age >= 0 (usable)          : {pos:,}")
print(f"    age <  0 (component installed AFTER the event -- MUST be excluded): {neg:,}"
      f"  ({neg/(pos+neg) if (pos+neg) else 0:.1%})")
print("\n  A high negative share means the current snapshot is badly mismatched to history")
print("  and a serial-level age is only safe on a recent window. A low share means the")
print("  components in the event stream are largely the ones still installed, and the")
print("  feature is buildable -- with the missingness caveat above still standing.")
print("=" * 104)
