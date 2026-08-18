# =============================================================================
# PS3 -- WHY DID RECONCILIATION FAIL?  (paste as a new cell, run it, read it)
#
# Three candidate causes, in order of likelihood. This tells you which.
#
#   1. STALE FRAMES -- cell 3 (module v2) was re-run but cell 4 was not, so
#      `frames` still holds the v1 build. Most likely. Fix: re-run cell 4.
#   2. STALE MODULE -- cell 3 was not re-run, so collapse_to_device_day is
#      still v1. Fix: re-run cell 3, then cell 4.
#   3. GENUINE DIVERGENCE -- v2 is active and rebuilt, and coverage still does
#      not match calibration. Then my baseline or my rule is wrong, not the run.
# =============================================================================
print("=" * 78)
print("RECONCILIATION DIAGNOSTIC")
print("=" * 78)

# --- 1. which module version is loaded? -------------------------------------
print(f"\n  SPINE_VERSION loaded in kernel : {SPINE_VERSION}")
_v2_module = SPINE_VERSION.endswith("v2")
print(f"  module is v2                   : {_v2_module}")
if not _v2_module:
    print("  => CAUSE 2. Cell 3 was not re-run. Re-run cell 3, then cell 4, then 4b.")

# --- 2. were the frames built by v2? ----------------------------------------
# v2 adds label_from_event_n and informative_in_day; v1 has neither.
_fr = next(iter(frames.values()))
_cols = set(_fr.columns)
_v2_frames = "label_from_event_n" in _cols and "informative_in_day" in _cols
print(f"\n  frames carry v2 columns        : {_v2_frames}")
print(f"    label_from_event_n present   : {'label_from_event_n' in _cols}")
print(f"    informative_in_day present   : {'informative_in_day' in _cols}")
if _v2_module and not _v2_frames:
    print("  => CAUSE 1. Module is v2 but frames are v1. RE-RUN CELL 4, then 4b.")

# --- 3. if v2 really is active, show what it actually produced --------------
if _v2_module and _v2_frames:
    print("\n  => CAUSE 3. v2 is active and frames were rebuilt. Measured coverage:")
    CAL = {"TVM": 88.3, "GATE": 93.7, "VALIDATOR": 55.7}
    print(f"\n  {'fleet':10s} {'days':>10s} {'LABELLED':>10s} {'GENERIC':>9s} {'MISSING':>9s} "
          f"{'pct':>7s} {'calib':>7s} {'delta':>7s}")
    for fleet, fr in frames.items():
        n = fr.count()
        st = {r["root_cause_state"]: r["n"] for r in
              fr.groupBy("root_cause_state").agg(F.count("*").alias("n")).collect()}
        lab = st.get("LABELLED", 0); gen = st.get("GENERIC", 0); mis = st.get("MISSING", 0)
        pct = lab / max(n, 1) * 100
        print(f"  {fleet:10s} {n:10,} {lab:10,} {gen:9,} {mis:9,} "
              f"{pct:6.1f}% {CAL[fleet]:6.1f}% {pct-CAL[fleet]:+6.1f}")

    # The decisive question: on device-days that ended GENERIC or MISSING, was
    # there an informative event available that we failed to pick?
    print("\n  Did any device-day have an informative event we did NOT use?")
    print("  (if >0, the picker is broken. if 0, no informative event existed --")
    print("   the label genuinely is not there and my calibration baseline is wrong.)")
    for fleet, fr in frames.items():
        missed = fr.where((F.col("root_cause_state") != "LABELLED")
                          & (F.col("informative_in_day") > 0)).count()
        nolab  = fr.where((F.col("root_cause_state") != "LABELLED")
                          & (F.col("informative_in_day") == 0)).count()
        print(f"    {fleet:10s} picker missed {missed:>8,} | genuinely no informative event {nolab:>8,}")

print("\n" + "=" * 78)
print("WHAT TO DO")
print("=" * 78)
print("""  CAUSE 1 or 2 -> re-run the cells named above. Two minutes.
  CAUSE 3      -> the run is right and my 88.3 / 55.7 baseline is not
                  comparable. Calibration took the first NON-NULL subsystem and
                  then asked if it was informative; v2 searches for the first
                  INFORMATIVE one, which should score HIGHER, not lower. If it
                  scores lower with picker-missed = 0, the two are counting
                  different device-day populations and the baseline should be
                  restated from this run rather than from calibration.

  EITHER WAY, YOU ARE NOT BLOCKED. The assertion is a guard, not a gate. Run
  the cell below to downgrade it to a warning and proceed -- but quote the
  MEASURED coverage to the client, not the calibration figure.""")
print("=" * 78)
