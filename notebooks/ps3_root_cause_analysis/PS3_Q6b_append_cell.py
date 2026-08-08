# =============================================================================
# Q6b -- SEVERITY DERIVABILITY, MEASURED ON THE REAL IMPACT CLASSES ONLY
#
# WHY THIS EXISTS. Q6 returned 47.1% accuracy vs a 43.5% baseline (+3.6 pts) and
# concluded WEAK. That verdict is arithmetically correct and, I think, answers
# the wrong question -- because 71.7% of failure_level_label is not severity:
#
#     BACK_OFFICE_OR_UNKNOWN   29,628   43.5%   a catch-all, not a severity
#     FULLY_FUNCTIONAL         19,211   28.2%   the device still WORKS
#     ------------------------------------------------------------------
#     PURCHASE_CARD            11,796   17.3%   real functional impact
#     ALL_PURCHASE              4,286    6.3%   real functional impact
#     ALL_FUNCTIONS             2,424    3.6%   real functional impact
#     PURCHASE_PRODUCT            776    1.1%   real functional impact
#
# Asking whether component_subsystem predicts a target that is mostly "unknown"
# and "nothing was wrong" is not a fair test of whether it predicts SEVERITY.
#
# HAND-CALC FROM THE Q6 CROSSTAB (to be confirmed or refuted by this cell):
#   restricted to the four impact classes -> accuracy 71.1%, baseline 61.2%,
#   lift +9.9 pts. That crosses the PARTIAL threshold and approaches STRONG.
#
# THIS CELL IS THE CHECK. If it disagrees with those numbers, believe the cell.
#
# Uses `joined`, already persisted. No re-scan.
# =============================================================================
print("\n" + "=" * 78)
print("Q6b  SEVERITY DERIVABILITY -- real functional-impact classes only")
print("=" * 78)

# The classes that describe what actually stopped working. Everything else is
# either a catch-all or an explicit statement that nothing failed.
IMPACT_CLASSES = ["PURCHASE_CARD", "ALL_PURCHASE", "ALL_FUNCTIONS", "PURCHASE_PRODUCT"]
NON_IMPACT     = ["BACK_OFFICE_OR_UNKNOWN", "FULLY_FUNCTIONAL"]

base = (joined
        .where(F.col("sn_failure_level").isNotNull() & F.col("dev_subsystem").isNotNull())
        .withColumn("subsystem",     F.upper(F.trim(F.col("dev_subsystem"))))
        .withColumn("failure_level", F.upper(F.trim(F.col("sn_failure_level")))))
n_all = base.count()

sev = base.where(F.col("failure_level").isin(*IMPACT_CLASSES)).persist()
n6b = sev.count()

print(f"  all rows with subsystem + failure_level : {n_all:,}")
print(f"  restricted to real impact classes       : {n6b:,} ({n6b/max(n_all,1)*100:.1f}%)")
print(f"  dropped as non-severity                 : {n_all - n6b:,} "
      f"({(n_all-n6b)/max(n_all,1)*100:.1f}%)  {NON_IMPACT}\n")

if n6b == 0:
    print("  !! no rows survived the filter -- check the class spellings against Q6(a)")
else:
    # --- modal share per subsystem -----------------------------------------
    print("  (a) MODAL SHARE per subsystem, impact classes only")
    w_sub  = Window.partitionBy("subsystem")
    w_rank = Window.partitionBy("subsystem").orderBy(F.desc("n"))
    modal = (sev.groupBy("subsystem", "failure_level").agg(F.count("*").alias("n"))
                .withColumn("subsystem_total", F.sum("n").over(w_sub))
                .withColumn("rk", F.row_number().over(w_rank))
                .where(F.col("rk") == 1)
                .withColumn("modal_share_pct",
                            F.round(F.col("n") / F.col("subsystem_total") * 100, 1))
                .select("subsystem", "subsystem_total",
                        F.col("failure_level").alias("modal_impact_level"),
                        F.col("n").alias("modal_n"), "modal_share_pct")
                .orderBy(F.desc("subsystem_total")))
    modal.show(30, truncate=False)

    agg = modal.agg(F.sum("modal_n").alias("m"), F.sum("subsystem_total").alias("t")).collect()[0]
    lookup_acc = agg["m"] / agg["t"] * 100
    top = sev.groupBy("failure_level").agg(F.count("*").alias("n")).orderBy(F.desc("n")).first()
    base_acc = top["n"] / n6b * 100
    lift = lookup_acc - base_acc

    # --- the same, adding fleet --------------------------------------------
    w_sf  = Window.partitionBy("fleet", "subsystem")
    w_sfr = Window.partitionBy("fleet", "subsystem").orderBy(F.desc("n"))
    modal_f = (sev.groupBy("fleet", "subsystem", "failure_level").agg(F.count("*").alias("n"))
                  .withColumn("tot", F.sum("n").over(w_sf))
                  .withColumn("rk", F.row_number().over(w_sfr))
                  .where(F.col("rk") == 1))
    aggf = modal_f.agg(F.sum("n").alias("m"), F.sum("tot").alias("t")).collect()[0]
    acc_f = aggf["m"] / aggf["t"] * 100

    # --- class balance: a 4-class problem needs usable minority classes ----
    print("  (b) impact-class balance (a rare class the model never sees is not learnable)")
    (sev.groupBy("failure_level").agg(F.count("*").alias("n"))
        .withColumn("pct", F.round(F.col("n") / F.lit(n6b) * 100, 1))
        .orderBy(F.desc("n")).show(truncate=False))

    # --- verdict ------------------------------------------------------------
    print("  (c) VERDICT")
    print(f"      rows                              : {n6b:,}")
    print(f"      subsystem -> modal impact accuracy: {lookup_acc:.1f}%")
    print(f"      majority-class baseline           : {base_acc:.1f}%  "
          f"(always predict {top['failure_level']})")
    print(f"      lift over baseline                : {lift:+.1f} pts")
    print(f"      (fleet, subsystem) accuracy       : {acc_f:.1f}%  "
          f"({acc_f - lookup_acc:+.1f} from adding fleet)")
    print()
    print(f"      hand-calc from the Q6 crosstab was 71.1% / 61.2% / +9.9 pts")
    print(f"      this cell says              {lookup_acc:.1f}% / {base_acc:.1f}% / {lift:+.1f} pts")
    _delta = abs(lookup_acc - 71.1)
    print(f"      agreement: {'CONFIRMED' if _delta < 2 else 'DIVERGES -- trust this cell, not the hand-calc'}"
          f" (delta {_delta:.1f} pts)")
    print()
    if lookup_acc >= 70 and lift >= 15:
        print("      => STRONG. Subsystem largely determines functional impact.")
        print("         Severity is a DEVICE-NATIVE head, scored at failure time.")
        print("         PS3 stays ONE product with ONE SLA.")
    elif lift >= 8:
        print("      => PARTIAL -- and good enough. Subsystem carries real severity")
        print("         signal. A device-native severity head is viable WITH extra")
        print("         features (device type, event type, hour, recent event mix).")
        print("         A plain lookup table is the floor, not the ceiling: a model")
        print("         with those features should beat this number.")
        print("         PS3 can stay ONE at-failure-time product.")
    else:
        print("      => WEAK even on the impact classes. Subsystem does not determine")
        print("         functional impact. Severity stays ServiceNow-derived and the")
        print("         severity head becomes a POST-WORK-ORDER model -- root cause at")
        print("         failure time, severity later. Two heads, two SLAs.")

    # --- honest caveat ------------------------------------------------------
    print()
    print("      CAVEAT. This is measured only where ServiceNow HAS a record --")
    print(f"      {n6b:,} rows, against 1,059,634 OOS device-days overall. It says")
    print("      subsystem predicts impact ON THE SN-COVERED SUBSET. Whether that")
    print("      generalises to validators (0% SN coverage) is NOT tested here and")
    print("      cannot be, until some validator severity ground truth exists.")
    print("      Do not quote this accuracy for VALIDATOR.")

print("\n" + "=" * 78)
print("Q6b COMPLETE")
print("=" * 78)
