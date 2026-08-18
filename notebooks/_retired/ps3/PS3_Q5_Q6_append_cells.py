# =============================================================================
# Q5 -- HOW OFTEN IS SERVICENOW UNINFORMATIVE WHERE THE DEVICE IS NOT?
#
# The top-12 pairings suggested SN says UNKNOWN against a named device subsystem
# in roughly half the overlap. "Roughly, from the top 12" is not a number you
# design on. This measures it exactly, and measures the reverse case too --
# because if the device is ALSO frequently generic, the argument weakens.
#
# Uses `joined` from the Q1 cell, already persisted. No re-scan.
# =============================================================================
print("\n" + "=" * 78)
print("Q5  UNINFORMATIVE-LABEL ANALYSIS -- who actually knows the cause?")
print("=" * 78)

# Values that carry no diagnostic content on either side.
SN_UNINFORMATIVE  = ["UNKNOWN", "OTHER", "NONE", "N/A", "NA", ""]
# Device subsystems that name the whole device rather than a component.
DEV_UNINFORMATIVE = ["SYSTEM", "DEV", "DEVICE", "OTHER", "UNKNOWN", "NONE", ""]

_sn_u  = F.upper(F.trim(F.coalesce(F.col("sn_root_cause"), F.lit(""))))
_dev_u = F.upper(F.trim(F.coalesce(F.col("dev_subsystem"), F.lit(""))))

lab = (joined
       .withColumn("sn_informative",
                   F.when(F.col("sn_root_cause").isNull(), F.lit(False))
                    .when(_sn_u.isin(*SN_UNINFORMATIVE), F.lit(False))
                    .otherwise(F.lit(True)))
       .withColumn("dev_informative",
                   F.when(F.col("dev_subsystem").isNull(), F.lit(False))
                    .when(_dev_u.isin(*DEV_UNINFORMATIVE), F.lit(False))
                    .otherwise(F.lit(True))))

# --- (a) across ALL OOS device-days: who has a usable label at all? ----------
print("\n  (a) ALL OOS device-days -- usable label availability")
lab.groupBy("fleet").agg(
    F.count("*").alias("oos_device_days"),
    F.round(F.avg(F.col("dev_informative").cast("double")) * 100, 1).alias("pct_dev_informative"),
    F.round(F.avg(F.col("sn_informative").cast("double")) * 100, 1).alias("pct_sn_informative"),
).orderBy("fleet").show(truncate=False)
print("      pct_sn_informative is the REAL trainable share PS3 has today --")
print("      lower than raw SN coverage, because UNKNOWN rows are not labels.")

# --- (b) on the OVERLAP only: the four-way breakdown -------------------------
print("\n  (b) OVERLAP only (both tables have a row) -- who knows what")
ov = lab.where(F.col("sn_root_cause").isNotNull())
n_ov = ov.count()
print(f"      overlap rows: {n_ov:,}\n")
if n_ov:
    (ov.groupBy("dev_informative", "sn_informative")
       .agg(F.count("*").alias("n"),
            F.round(F.count("*") / F.lit(n_ov) * 100, 1).alias("pct"))
       .orderBy(F.desc("n")).show(truncate=False))
    dev_only = ov.where(F.col("dev_informative") & ~F.col("sn_informative")).count()
    sn_only  = ov.where(~F.col("dev_informative") & F.col("sn_informative")).count()
    both_i   = ov.where(F.col("dev_informative") & F.col("sn_informative")).count()
    neither  = ov.where(~F.col("dev_informative") & ~F.col("sn_informative")).count()
    print(f"      device knows, SN says UNKNOWN : {dev_only:,} ({dev_only/n_ov*100:.1f}%)")
    print(f"      SN knows, device is generic   : {sn_only:,} ({sn_only/n_ov*100:.1f}%)")
    print(f"      both informative              : {both_i:,} ({both_i/n_ov*100:.1f}%)")
    print(f"      neither informative           : {neither:,} ({neither/n_ov*100:.1f}%)")
    print()
    print("      READ: if device-knows-SN-doesn't >> SN-knows-device-doesn't, then")
    print("      ServiceNow is NOT a superior ground truth being approximated --")
    print("      the device is the better source and SN is the enrichment.")
    print("      If they are comparable, both are needed and neither dominates.")

# --- (c) exactly which SN values are the uninformative ones ------------------
print("\n  (c) SN root_cause_category value distribution on the overlap")
(ov.groupBy(F.upper(F.trim(F.coalesce(F.col("sn_root_cause"), F.lit("<null>")))).alias("sn_root_cause"))
   .agg(F.count("*").alias("n"))
   .withColumn("pct", F.round(F.col("n") / F.lit(n_ov) * 100, 1))
   .orderBy(F.desc("n")).show(25, truncate=False))

# --- (d) and the device side, for symmetry ----------------------------------
print("  (d) device component_subsystem distribution on the overlap")
(ov.groupBy(F.upper(F.trim(F.coalesce(F.col("dev_subsystem"), F.lit("<null>")))).alias("dev_subsystem"))
   .agg(F.count("*").alias("n"))
   .withColumn("pct", F.round(F.col("n") / F.lit(n_ov) * 100, 1))
   .orderBy(F.desc("n")).show(25, truncate=False))


# =============================================================================
# Q6 -- IS SEVERITY DERIVABLE FROM WHICH SUBSYSTEM FAILED?
#
# The device severity COLUMNS are dead (severity == 'INFO' for all 68,121 rows,
# event_type_severity 10.0-or-NULL, event_priority 1.68-2.00). So severity
# cannot come from a device severity field.
#
# But SN's failure_level_label describes FUNCTIONAL IMPACT -- PURCHASE_CARD,
# ALL_PURCHASE, ALL_FUNCTIONS -- i.e. what stopped working. That is plausibly a
# CONSEQUENCE of which subsystem failed, not an independent measurement. A card
# reader failing implies the card purchase path is down.
#
# If each subsystem maps predominantly to one failure level, severity is
# derivable at failure time and the severity head can stay device-native.
# If the distribution is flat, it cannot, and severity stays post-work-order.
#
# The deciding statistic is MODAL SHARE: for each subsystem, what fraction of
# its rows land in its single most common failure level.
# =============================================================================
print("\n" + "=" * 78)
print("Q6  SEVERITY DERIVABILITY -- component_subsystem x failure_level_label")
print("=" * 78)

sev6 = (joined
        .where(F.col("sn_failure_level").isNotNull() & F.col("dev_subsystem").isNotNull())
        .withColumn("subsystem", F.upper(F.trim(F.col("dev_subsystem"))))
        .withColumn("failure_level", F.upper(F.trim(F.col("sn_failure_level")))))
n6 = sev6.count()
print(f"  rows with BOTH subsystem and failure_level: {n6:,}\n")

if n6:
    # --- raw crosstab -------------------------------------------------------
    print("  (a) raw crosstab (rows=subsystem, cols=failure_level)")
    sev6.groupBy("subsystem").pivot("failure_level").count().na.fill(0) \
        .orderBy("subsystem").show(30, truncate=False)

    # --- modal share: THE decision metric -----------------------------------
    print("  (b) MODAL SHARE per subsystem -- the deciding number")
    w_sub = Window.partitionBy("subsystem")
    w_rank = Window.partitionBy("subsystem").orderBy(F.desc("n"))
    modal = (sev6.groupBy("subsystem", "failure_level").agg(F.count("*").alias("n"))
                 .withColumn("subsystem_total", F.sum("n").over(w_sub))
                 .withColumn("rk", F.row_number().over(w_rank))
                 .where(F.col("rk") == 1)
                 .withColumn("modal_share_pct",
                             F.round(F.col("n") / F.col("subsystem_total") * 100, 1))
                 .select("subsystem", "subsystem_total",
                         F.col("failure_level").alias("modal_failure_level"),
                         F.col("n").alias("modal_n"), "modal_share_pct")
                 .orderBy(F.desc("subsystem_total")))
    modal.show(30, truncate=False)

    # --- weighted average modal share = accuracy of a pure lookup table -----
    agg = modal.agg(
        F.sum("modal_n").alias("modal_total"),
        F.sum("subsystem_total").alias("grand_total")).collect()[0]
    lookup_acc = agg["modal_total"] / agg["grand_total"] * 100
    n_sub = modal.count()

    # --- baseline: always predict the single most common failure level ------
    top_overall = (sev6.groupBy("failure_level").agg(F.count("*").alias("n"))
                       .orderBy(F.desc("n")).first())
    base_acc = top_overall["n"] / n6 * 100

    print("\n  (c) VERDICT")
    print(f"      subsystems observed            : {n_sub}")
    print(f"      subsystem -> modal level accuracy: {lookup_acc:.1f}%")
    print(f"      majority-class baseline         : {base_acc:.1f}%  "
          f"(always predict {top_overall['failure_level']})")
    print(f"      lift over baseline              : {lookup_acc - base_acc:+.1f} pts")
    print()
    if lookup_acc >= 70 and (lookup_acc - base_acc) >= 15:
        print("      => STRONG. Subsystem largely determines functional impact.")
        print("         Severity can be a DEVICE-NATIVE head, scored at failure time,")
        print("         with the mapping published as a lookup and a model on top.")
    elif (lookup_acc - base_acc) >= 8:
        print("      => PARTIAL. Subsystem carries real severity signal but does not")
        print("         determine it. A device-native severity head is viable WITH")
        print("         additional features (device type, event type, time of day).")
    else:
        print("      => WEAK. Subsystem does not determine functional impact.")
        print("         Severity must stay ServiceNow-derived, which means the")
        print("         severity head is a POST-WORK-ORDER model even though the")
        print("         root-cause head scores at failure time. Two different SLAs.")

    # --- does adding fleet help? severity is device-type dependent ----------
    print("\n  (d) same, split by fleet -- a card reader on a TVM and on a gate")
    print("      do not have the same functional impact")
    w_sf   = Window.partitionBy("fleet", "subsystem")
    w_sfr  = Window.partitionBy("fleet", "subsystem").orderBy(F.desc("n"))
    modal_f = (sev6.groupBy("fleet", "subsystem", "failure_level").agg(F.count("*").alias("n"))
                   .withColumn("tot", F.sum("n").over(w_sf))
                   .withColumn("rk", F.row_number().over(w_sfr))
                   .where(F.col("rk") == 1))
    aggf = modal_f.agg(F.sum("n").alias("m"), F.sum("tot").alias("t")).collect()[0]
    lookup_acc_f = aggf["m"] / aggf["t"] * 100
    print(f"      (fleet, subsystem) -> modal level accuracy: {lookup_acc_f:.1f}%")
    print(f"      gain from adding fleet: {lookup_acc_f - lookup_acc:+.1f} pts")
    modal_f.select("fleet", "subsystem", F.col("failure_level").alias("modal_level"),
                   "n", "tot",
                   F.round(F.col("n") / F.col("tot") * 100, 1).alias("modal_share_pct")) \
           .orderBy("fleet", F.desc("tot")).show(40, truncate=False)

print("\n" + "=" * 78)
print("Q5/Q6 COMPLETE")
print("=" * 78)
