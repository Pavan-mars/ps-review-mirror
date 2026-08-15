# =============================================================================
# ps3_oos_spine.py -- PS3 canonical OOS spine + native labels + ServiceNow audit
#
# CONTRACT: docs/OOS_EVENT_CONTRACT.md  (2026-08-03.v5)
#   source      mars_dev.silver.device_event_enriched   (SILVER, hosted under the
#                                                        GOLD bucket at chicago/silver/)
#   primary     is_hardware_oos_event = TRUE
#   state gate  UPPER(TRIM(EVENT_STATE_TYPE_NAME)) = 'SET'
#   scope       dim_device.is_current = TRUE
#   grain       calendar day -- IDENTICAL to PS1, so a PS3 root-cause row joins
#               1:1 to a PS1 failure-day row on (device_id, transit_day)
#
# WHY PS3 MOVED. The previous PS3 sat on silver.incident_root_cause (ServiceNow
# availability events, FAILURE_LEVEL>0 AND EXCLUDED=0 -- the CHARGEABLE subset).
# Calibration on 2026-08-03 measured what that cost:
#
#   trainable label coverage      GATE 1.9%   TVM 10.5%   VALIDATOR 0.0%
#   OOS device-days               68,121 with SN  ->  1,059,634 native
#
# MEASURED BY THIS SPINE (v2, 2026-08-03) -- higher than calibration implied:
#   device-native coverage        GATE 95.5%  TVM 99.0%   VALIDATOR 96.8%
#   labelled device-days          1,027,390 of 1,059,634 (97.0%)  = 15.1x
#
# The calibration figures (93.7 / 88.3 / 55.7) took the first NON-NULL subsystem
# per device-day and then asked whether it was informative. v2 searches for the
# first INFORMATIVE one, so it scores higher -- +41 points on VALIDATOR alone.
# Both are honest; v2 is the better rule. Quote the MEASURED row.
#   device specific / SN UNKNOWN  41.6%   vs   SN specific / device generic 4.3%
#   ServiceNow usable classes     3 (UNKNOWN 53.8%, CARD_READER, COMMS)
#   device usable classes         9
#
# So ServiceNow is NOT a superior ground truth being approximated. It is the
# enrichment. The device is the label.
#
# VALIDATORS. ServiceNow has no availability events for validators at all -- 0 of
# 513,519 OOS device-days. Their ServiceNow root cause is, and will remain, NULL.
# That is not a bug to hide: it is the coverage gap PS3 exists to close, and the
# emitted frame labels it explicitly (sn_label_state = 'NO_SN_RECORD') so it can
# be shown rather than silently imputed.
# =============================================================================
from pyspark.sql import functions as F, Window

# --- contract ----------------------------------------------------------------
GOLD_BUCKET     = "cubic-mars-pm-s3-datalake-dev-gold-170202974600"
SCHEME          = "s3a"
RUN_DATE        = "2026-04-11"      # ODS watermark, matches PS1/PS2/PS5
TELEMETRY_START = "2024-01-01"
SCOPE           = ["TVM", "GATE", "VALIDATOR"]
SPINE_VERSION   = "ps3_oos_spine.2026-08-03.v2"
EVENT_DEF_VERSION = "2026-08-03.v5"

def SILVER(t):
    return f"{SCHEME}://{GOLD_BUCKET}/chicago/silver/{t}"

# Device subsystems that name the whole device rather than a component. Kept as a
# named constant because "is this label useful" is a modelling decision, not a
# detail -- VALIDATOR sits at 55.7% informative largely because of these.
UNINFORMATIVE_SUBSYSTEM = ["SYSTEM", "DEV", "DEVICE", "OTHER", "UNKNOWN", "NONE", ""]
UNINFORMATIVE_SN        = ["UNKNOWN", "OTHER", "NONE", "N/A", "NA", ""]

# ServiceNow speaks a different vocabulary for the same components. Measured from
# the 2026-08-03 calibration crosstab; used for AUDIT ONLY -- it never rewrites a
# native label, it only decides whether the two sources agree.
SN_CROSSWALK = {
    "CARD_READER":   ["CSC_READER", "SCRST"],
    "COMMS":         ["COMMS", "DOPP"],
    "PRINTER":       ["PRINTER"],
    "CASH_HANDLING": ["BHU", "CHU"],
    "POWER":         ["POWER"],
    "SOFTWARE":      ["SYSTEM"],
}

# The four failure_level_label values that describe real functional impact.
# BACK_OFFICE_OR_UNKNOWN and FULLY_FUNCTIONAL are excluded from the severity
# TARGET: the first is a catch-all, the second means the device still works.
# Including them is what made severity look underivable (+3.6 pts); on these four
# alone, (fleet, subsystem) reaches 74.7%, +10.0 pts over baseline.
IMPACT_CLASSES = ["PURCHASE_CARD", "ALL_PURCHASE", "ALL_FUNCTIONS", "PURCHASE_PRODUCT"]


def _contract_guard(spine_count, source_count, applied, fleet):
    """Refuse to return a frame built on the wrong events.

    Same discipline as the PS5 engine. The PS5 v5.2.3 run printed
    'filters: NONE APPLIED' and published champions, Weibull fits and CRITICAL
    tiers off unfiltered rows because a warning on stdout stops nothing.
    """
    joined = " | ".join(applied)
    if not applied:
        raise RuntimeError(
            f"EVENT CONTRACT VIOLATION [{fleet}]: no filters applied. All "
            f"{source_count:,} source rows accepted as hardware-OOS. Refusing.")
    if "is_hardware_oos_event" not in joined:
        raise RuntimeError(
            f"EVENT CONTRACT VIOLATION [{fleet}]: primary flag never applied. "
            f"Applied: {joined}")
    if "EVENT_STATE_TYPE_NAME" not in joined:
        raise RuntimeError(
            f"EVENT CONTRACT VIOLATION [{fleet}]: 'Set' state gate never applied. "
            f"Applied: {joined}")
    if spine_count == 0:
        raise RuntimeError(
            f"EVENT CONTRACT VIOLATION [{fleet}]: spine is empty. Either the "
            f"fleet name is wrong or the window excludes everything.")
    print(f"    [contract OK] {EVENT_DEF_VERSION} | {fleet} | {joined}")


def build_oos_spine(spark, device_category, run_date=RUN_DATE,
                    telemetry_start=TELEMETRY_START):
    """The canonical OOS event spine for one fleet. Two clauses, nothing else.

    Returns EVENT grain (not yet collapsed to device-day) so callers can inspect
    intra-day structure if they need it. build_ps3_frame() collapses to day.
    """
    dee = spark.read.parquet(SILVER("device_event_enriched"))
    dim = spark.read.parquet(SILVER("dim_device"))
    n0  = None  # counted lazily only if the guard needs it

    current = dim.where(F.col("is_current") == True).select("DEVICE_KEY").distinct()
    applied = []

    s = dee.alias("e").join(F.broadcast(current).alias("d"),
                            F.col("e.DEVICE_KEY") == F.col("d.DEVICE_KEY"))
    s = s.where(F.col("e.is_hardware_oos_event") == True)
    applied.append("is_hardware_oos_event=TRUE")
    s = s.where(F.upper(F.trim(F.col("e.EVENT_STATE_TYPE_NAME").cast("string"))) == "SET")
    applied.append("EVENT_STATE_TYPE_NAME='Set'")
    s = s.where(F.upper(F.col("e.mars_device_category").cast("string")) == device_category.upper())
    applied.append(f"mars_device_category={device_category.upper()}")
    s = s.where((F.col("e.transit_day") >= F.lit(telemetry_start))
                & (F.col("e.transit_day") <= F.lit(run_date)))
    applied.append(f"transit_day in [{telemetry_start},{run_date}]")

    spine = s.select(
        F.col("e.DEVICE_ID").cast("string").alias("device_id"),
        F.col("e.DEVICE_KEY").cast("string").alias("device_key"),
        F.col("e.transit_day").alias("transit_day"),
        F.col("e.EVENT_DTM").alias("event_dtm"),
        F.upper(F.col("e.mars_device_category").cast("string")).alias("fleet"),
        # --- native root-cause signal (available at failure time) ---
        F.upper(F.trim(F.col("e.component_subsystem").cast("string"))).alias("component_subsystem"),
        F.col("e.COMPONENT_TYPE_NAME").cast("string").alias("component_type_name"),
        F.col("e.COMPONENT_SERIAL_NBR").cast("string").alias("component_serial_nbr"),
        F.col("e.COMPONENT_POSITION").cast("int").alias("component_position"),
        # --- event context (features) ---
        F.col("e.EVENT_TYPE_NAME").cast("string").alias("event_type_name"),
        F.col("e.EVENT_TYPE_DESC").cast("string").alias("event_type_desc"),
        F.col("e.EVENT_TYPE_ID").cast("int").alias("event_type_id"),
        F.col("e.event_priority").cast("int").alias("event_priority"),
        F.col("e.DEVICE_TYPE_NAME").cast("string").alias("device_type_name"),
        F.col("e.DEVICE_CONTROL_GROUP_NAME").cast("string").alias("control_group"),
        F.col("e.TRANSIT_MODE_NAME").cast("string").alias("transit_mode"),
        # --- freshness (proves at-failure-time scoring is possible) ---
        F.col("e.EDW_INSERTED_DTM").alias("edw_inserted_dtm"),
    )
    n_spine = spine.count()
    _contract_guard(n_spine, 0, applied, device_category.upper())
    print(f"    spine: {n_spine:,} OOS 'Set' events")
    return spine


def collapse_to_device_day(spine):
    """Calendar-day grain -- identical to PS1, so PS3 joins 1:1 to PS1 labels.

    SELECTION RULE: the first event of the day that names a real COMPONENT wins;
    only if no event that day names one does the first event overall win.

    The first version of this ordered purely by event_dtm and took row 1. That
    silently discarded 153,693 labels against the calibration baseline --
    VALIDATOR fell 55.7% -> 30.7% (128,141 rows) and TVM 88.3% -> 77.6%. The
    cause: a device's first OOS event of the day often carries a NULL or generic
    subsystem while a later event the same day names the actual component. The
    calibration used first-NON-NULL and so kept those; ordering by time alone
    threw them away.

    Preferring the informative event is also the better rule on its own terms:
    PS3 answers "which component failed", and "SYSTEM" is not an answer when the
    same device-day also reported "CSC_READER".

    events_in_day is retained so a busy day stays visible, and
    label_from_event_n records WHICH event in the day supplied the label -- 1
    means the first, higher means we reached past a generic onset to find a real
    component. Auditable rather than silent.
    """
    _informative = (F.col("component_subsystem").isNotNull()
                    & (~F.upper(F.trim(F.col("component_subsystem"))).isin(*UNINFORMATIVE_SUBSYSTEM)))
    w_pick = (Window.partitionBy("device_id", "transit_day")
                    .orderBy(F.desc(_informative.cast("int")), F.asc("event_dtm")))
    w_time = (Window.partitionBy("device_id", "transit_day").orderBy(F.asc("event_dtm")))
    w_day  = Window.partitionBy("device_id", "transit_day")
    return (spine
            .withColumn("_rk",   F.row_number().over(w_pick))
            .withColumn("_trk",  F.row_number().over(w_time))
            .withColumn("events_in_day",        F.count("*").over(w_day))
            .withColumn("informative_in_day",   F.sum(_informative.cast("int")).over(w_day))
            .where(F.col("_rk") == 1)
            .withColumnRenamed("_trk", "label_from_event_n")
            .drop("_rk"))


def attach_native_labels(day):
    """Device-native labels. 100% coverage by construction -- the label IS the event.

    root_cause          component_subsystem, verbatim. No imputation.
    root_cause_state    LABELLED | GENERIC   -- GENERIC means the subsystem names
                        the whole device (SYSTEM/DEV/...) rather than a part.
                        VALIDATOR is ~44% GENERIC. Surfaced, not hidden.
    """
    _rc = F.upper(F.trim(F.coalesce(F.col("component_subsystem"), F.lit(""))))
    return (day
            .withColumn("root_cause", F.when(_rc == "", F.lit(None)).otherwise(_rc))
            .withColumn("root_cause_state",
                        F.when(F.col("root_cause").isNull(), F.lit("MISSING"))
                         .when(_rc.isin(*UNINFORMATIVE_SUBSYSTEM), F.lit("GENERIC"))
                         .otherwise(F.lit("LABELLED")))
            .withColumn("hour_of_day", F.hour("event_dtm"))
            .withColumn("day_of_week", F.dayofweek("event_dtm"))
            .withColumn("edw_lag_min",
                        (F.unix_timestamp("edw_inserted_dtm")
                         - F.unix_timestamp("event_dtm")) / 60.0))


def attach_servicenow(day, spark, run_date=RUN_DATE, telemetry_start=TELEMETRY_START):
    """ServiceNow as ENRICHMENT, VALIDATION and AUDIT TRAIL -- never as the label.

    Adds:
      sn_root_cause     the technician's classification, where one exists
      sn_severity       failure_level_label, restricted to the four IMPACT_CLASSES
                        (BACK_OFFICE_OR_UNKNOWN / FULLY_FUNCTIONAL are not severity)
      sn_label_state    NO_SN_RECORD | SN_UNKNOWN | SN_LABELLED
      sn_agrees         does SN's class map to the device subsystem via SN_CROSSWALK
      severity_target   sn_severity where present -- the ONLY severity ground truth
                        that exists. Train the severity head on these rows, score
                        every row. VALIDATOR contributes zero of them, by design.
    """
    irc = spark.read.parquet(SILVER("incident_root_cause"))
    sn = (irc
          .where((F.col("transit_day") >= F.lit(telemetry_start))
                 & (F.col("transit_day") <= F.lit(run_date)))
          .select(
              F.col("device_id").cast("string").alias("device_id"),
              F.col("transit_day").alias("transit_day"),
              F.upper(F.trim(F.col("root_cause_category").cast("string"))).alias("sn_root_cause"),
              F.upper(F.trim(F.col("failure_level_label").cast("string"))).alias("_sn_fl"),
              F.col("AE_FAULT_DESCRIPTION").cast("string").alias("sn_fault_desc"),
              F.col("is_chargeable").alias("sn_is_chargeable"),
              F.col("is_device_fault").alias("sn_is_device_fault"),
          )
          .dropDuplicates(["device_id", "transit_day"]))

    out = day.join(sn, ["device_id", "transit_day"], "left")

    # severity target: impact classes only
    out = out.withColumn(
        "sn_severity",
        F.when(F.col("_sn_fl").isin(*IMPACT_CLASSES), F.col("_sn_fl")).otherwise(F.lit(None)))

    out = out.withColumn(
        "sn_label_state",
        F.when(F.col("sn_root_cause").isNull(), F.lit("NO_SN_RECORD"))
         .when(F.upper(F.trim(F.col("sn_root_cause"))).isin(*UNINFORMATIVE_SN), F.lit("SN_UNKNOWN"))
         .otherwise(F.lit("SN_LABELLED")))

    # agreement via the crosswalk -- AUDIT ONLY, never rewrites root_cause
    agree = F.lit(False)
    for sn_cls, dev_list in SN_CROSSWALK.items():
        agree = agree | ((F.col("sn_root_cause") == F.lit(sn_cls))
                         & F.col("root_cause").isin(*dev_list))
    out = out.withColumn(
        "sn_agrees",
        F.when(F.col("sn_label_state") != "SN_LABELLED", F.lit(None).cast("boolean"))
         .otherwise(agree))

    return (out.drop("_sn_fl")
               .withColumn("severity_target", F.col("sn_severity"))
               .withColumn("spine_version", F.lit(SPINE_VERSION))
               .withColumn("event_def_version", F.lit(EVENT_DEF_VERSION)))


def build_ps3_frame(spark, device_category, run_date=RUN_DATE,
                    telemetry_start=TELEMETRY_START, verbose=True):
    """End to end for one fleet. This is the function the notebooks call."""
    fleet = device_category.upper()
    if verbose:
        print("=" * 78)
        print(f"PS3 SPINE -- {fleet}   ({SPINE_VERSION})")
        print("=" * 78)
    spine = build_oos_spine(spark, fleet, run_date, telemetry_start)
    day   = collapse_to_device_day(spine)
    day   = attach_native_labels(day)
    frame = attach_servicenow(day, spark, run_date, telemetry_start).persist()

    if verbose:
        n = frame.count()
        print(f"    device-days: {n:,}")
        print("\n    root-cause label state (device-native):")
        frame.groupBy("root_cause_state").agg(
            F.count("*").alias("n"),
            F.round(F.count("*") / F.lit(n) * 100, 1).alias("pct")
        ).orderBy(F.desc("n")).show(truncate=False)
        print("    ServiceNow label state (enrichment / audit):")
        frame.groupBy("sn_label_state").agg(
            F.count("*").alias("n"),
            F.round(F.count("*") / F.lit(n) * 100, 1).alias("pct")
        ).orderBy(F.desc("n")).show(truncate=False)
        n_sev = frame.where(F.col("severity_target").isNotNull()).count()
        print(f"    severity ground truth available: {n_sev:,} of {n:,} "
              f"({n_sev/max(n,1)*100:.1f}%)")
        if n_sev == 0:
            print("    NOTE: no severity ground truth for this fleet. Expected for")
            print("          VALIDATOR -- ServiceNow has no availability events for")
            print("          them at all. The severity head cannot be trained or")
            print("          validated here; report it as a coverage gap, do not")
            print("          impute a value.")
        agr = frame.where(F.col("sn_agrees").isNotNull())
        n_agr = agr.count()
        if n_agr:
            ok = agr.where(F.col("sn_agrees")).count()
            print(f"    SN/device agreement (crosswalk): {ok:,}/{n_agr:,} "
                  f"({ok/n_agr*100:.1f}%) where SN gave a real class")
        print("=" * 78)
    return frame
