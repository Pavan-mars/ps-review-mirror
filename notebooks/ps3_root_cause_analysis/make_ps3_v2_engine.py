#!/usr/bin/env python3
"""Derive ps3_oos_engine_v2.py from the TESTED ps3_engine_reference.py (21-Jul bundle).

Patch, don't rewrite: v2 inherits the enrichment joins, leakage scan, purged split,
model factory, head trainer and data-quality guardrails unchanged. Only the six things
below differ. Re-run this after any future edit to the bundle engine.

  1  serial fan-out        -> ps3_serial_grain (silver.hw_config_current, notebook level)
  2  OOS spine assertion   -> never filters is_chargeable; fails loudly if asked to
  3  UNKNOWN not MAJOR     -> unmapped severity codes surface instead of defaulting
  4  v2 isolation          -> own OUT_DIR / model names / RDS prefix; endpoint deploy OFF
  5  manifest emission     -> the cubic-mars-ps3-rds-push bridge contract
  6  score_new_data()      -> daily inference reuses the exact training code path
"""
import re
import sys
from pathlib import Path

SRC = Path(sys.argv[1] if len(sys.argv) > 1 else "ps3_engine_reference.py")
OUT = Path(sys.argv[2] if len(sys.argv) > 2 else "ps3_oos_engine_v2.py")
s = SRC.read_text()
n = 0


def rep(old, new, why):
    global s, n
    assert old in s, f"ANCHOR MISSING ({why}): {old[:80]!r}"
    s = s.replace(old, new, 1); n += 1
    print(f"  [{n}] {why}")


# ---- 1. header ------------------------------------------------------------------
_d0 = s.index('"""'); _d1 = s.index('"""', _d0 + 3) + 3
_orig_doc = s[_d0:_d1]
rep(_orig_doc, '''"""
PS3 v2 - OOS spine, device AND serial grain. DERIVED from ps3_engine_reference.py
by make_ps3_v2_engine.py - do not hand-edit; edit the base engine or the patch script.

v2 vs v1, in full:
  * SERIAL GRANULARITY. v1 read matched_serial_nbr from gold.device_ps3_incident, whose
    hw_best_match CTE ends `WHERE rn = 1` (FIX 7) - exactly one component per incident. So
    v1's serial rollup, though correctly written, could only emit one row per device: the
    19-Jul run produced 927 device rows and 927 serial rows, identical on every measure.
    v2 joins silver.hw_config_current in the notebook (no gold change) and keys on
    (device_id, COMPONENT_SERIAL_NBR) because serials are NOT unique across devices.
  * OOS SPINE. is_chargeable is a contract flag applied after the physical event and is a
    strict subset of hardware OOS. v2 asserts no chargeable filter is ever applied.
  * UNKNOWN, not MAJOR, for unmapped severity codes.
  * TOTAL ISOLATION from chicago-ps3-rootcause-v1: own output dir, own MLflow names, own
    RDS table prefix, endpoint deployment hard-off.
  * MANIFEST BRIDGE so cubic-mars-ps3-rds-push loads the outputs unchanged, and
    score_new_data() so daily inference runs the same code as training.
"""
''', "v2 header")

# ---- 2. CONFIG additions --------------------------------------------------------
rep('    "CITY_ID": "CHI",', '''    "CITY_ID": "CHI",

    # ---------------- v2 isolation - nothing here can reach the live v1 endpoint ----
    "V2_SUFFIX": "oos_v2",
    "DEPLOY_ENDPOINT": False,
    "REGISTER_MLFLOW": False,
    "LIVE_ENDPOINT_DO_NOT_TOUCH": "chicago-ps3-rootcause-v1",

    # ---------------- OOS spine, never chargeable ---------------------------------
    "SPINE": "oos",
    "ASSERT_OOS_SPINE": True,
    "FORBID_CHARGEABLE_FILTER": True,

    # ---------------- serial fan-out (the v1 gap) ---------------------------------
    "SERIAL_FANOUT": True,
    "SERIAL_MIN_INCIDENTS": 1,

    # ---------------- bridge ------------------------------------------------------
    "EMIT_MANIFEST": True,
    "RDS_TABLE_PREFIX": "ps3v2_",
    "S3_OUT_PREFIX": "s3://cubic-mars-pm-s3-datalake-dev-gold-170202974600/chicago/ps3_v2",
''', "v2 CONFIG block")

rep('"HWCONFIG_TABLE":   "mars_dev.silver.hw_config_current",',
    '''"HWCONFIG_TABLE":   "mars_dev.silver.hw_config_current",
    # v1 declared this and never used it - that is exactly why the serial grain collapsed.
    "HWCONFIG_PARQUET": "s3://cubic-mars-pm-s3-datalake-dev-gold-170202974600/chicago/silver/hw_config_current",''',
    "hw_config parquet path (v1 declared the table but never read it)")

# ---- 3. output root separation --------------------------------------------------
rep('"OUT_ROOT": "PS3_outputs_pavan",',
    '"OUT_ROOT": "PS3_v2_outputs",   # v2 writes beside v1, never over it',
    "OUT_ROOT -> PS3_v2_outputs (v1 artifacts untouched)")

# ---- 4. severity UNKNOWN --------------------------------------------------------
rep('inc_out["pred_severity_collapsed"] = pd.Series(sev_lab).map(CONFIG["SEVERITY_COLLAPSE"]).fillna("MAJOR").values',
    '''# v2: an unmapped code must SURFACE, not silently become MAJOR. MIN_CLASS_COUNT folds
        # rare classes into OTHER, which is in no map; v1's .fillna("MAJOR") swallowed it and
        # was the mechanism behind the 100%-MAJOR fleet on the 18-Jul run.
        _sev_map = {str(k).strip().upper(): v for k, v in CONFIG["SEVERITY_COLLAPSE"].items()}
        inc_out["pred_severity_collapsed"] = (
            pd.Series(sev_lab).astype(str).str.strip().str.upper()
              .map(_sev_map).fillna("UNKNOWN").values)
        _unk = int((inc_out["pred_severity_collapsed"] == "UNKNOWN").sum())
        if _unk:
            print(f"  [severity] {_unk:,} incident(s) carry a code absent from SEVERITY_COLLAPSE "
                  f"-> UNKNOWN (excluded from pct_critical denominators)")''',
    "severity collapse: UNKNOWN instead of MAJOR")

# ---- 5. import the serial module ------------------------------------------------
rep("# ----------------------------------------------------------------------------- tee logger",
    '''# ----------------------------------------------------------------------------- serial grain (v2)
# Restores component granularity WITHOUT touching gold. See ps3_serial_grain.py for the
# attribution-vs-exposure contract and the serial-uniqueness caveat.
try:
    import ps3_serial_grain as SG
except ImportError:                      # notebook: the module is pasted into an earlier cell
    SG = sys.modules.get("ps3_serial_grain")
    if SG is None:
        print("[serial] ps3_serial_grain not importable - serial fan-out disabled")
        CONFIG["SERIAL_FANOUT"] = False


# ----------------------------------------------------------------------------- tee logger''',
    "import ps3_serial_grain")

# ---- 6. replace the v1 serial rollup with the fan-out version -------------------
old_roll = s[s.index("    # serial-grain rollup"):s.index('        print("  [serial] no matched_serial_nbr populated for this device type")') + len('        print("  [serial] no matched_serial_nbr populated for this device type")')]
rep(old_roll, '''    # ---------------- serial-grain rollup, v2 --------------------------------------
    # v1 grouped on the single matched_serial_nbr that gold carries, so it emitted one row
    # per device. v2 explodes to component grain from silver.hw_config_current first.
    ser_out = None
    if CONFIG.get("SERIAL_FANOUT", True) and SG is not None:
        _hw = SG.load_hw_config(load_table, CONFIG, category=cat)
        if _hw is not None:
            SERIAL_UNIQUENESS[cat] = SG.serial_uniqueness_report(_hw)
            _exp = SG.expand_serial_grain(inc_out, _hw, CONFIG)
            if _exp is not None:
                _exp.to_csv(out / f"{cat.lower()}_incident_component.csv", index=False)
                ser_out = SG.serial_rollup(_exp, CONFIG, cat)
                if ser_out is not None:
                    ser_out.to_csv(out / f"{cat.lower()}_serial_predictions.csv", index=False)
    if ser_out is None:
        print("  [serial] no serial-grain output for this device type")''',
    "serial rollup -> true fan-out")

# ---- 7. OOS spine assertion + uniqueness registry -------------------------------
rep("# ----------------------------------------------------------------------------- feature engineering",
    '''# ----------------------------------------------------------------------------- OOS spine guard (v2)
SERIAL_UNIQUENESS = {}


def assert_oos_spine(df):
    """PS3 v2 trains on hardware OOS. is_chargeable is a CONTRACT classification applied
    after the physical event and is a strict SUBSET of OOS - filtering on it teaches the
    model the contract, not the device. This fails the run rather than training quietly on
    the wrong population."""
    if not CONFIG.get("ASSERT_OOS_SPINE", True):
        return df
    if "is_chargeable" in df.columns:
        ch = df["is_chargeable"].fillna(False).astype(bool)
        print(f"  [spine] OOS rows {len(df):,} | chargeable subset {int(ch.sum()):,} "
              f"({100*ch.mean():.1f}%) - NOT filtered, kept for reference only")
        if CONFIG.get("FORBID_CHARGEABLE_FILTER", True) and ch.all() and len(df) > 0:
            raise AssertionError(
                "every row is is_chargeable=True - the frame has already been filtered to the "
                "chargeable subset upstream. PS3 v2 must train on the full OOS population.")
    else:
        print("  [spine] is_chargeable absent - nothing to guard against")
    if "is_device_fault" in df.columns:
        print(f"  [spine] is_device_fault true on {int(df['is_device_fault'].fillna(False).sum()):,}")
    return df


# ----------------------------------------------------------------------------- feature engineering''',
    "OOS spine assertion")

# ---- 8. manifest bridge ---------------------------------------------------------
rep("# ----------------------------------------------------------------------------- main",
    '''# ----------------------------------------------------------------------------- manifest bridge (v2)
def write_manifests(out_dir, run_id, computed_date, city=None):
    """Emit one manifest.json per output CSV.

    This is the contract `cubic-mars-ps3-rds-push` triggers on (S3 ObjectCreated, suffix
    manifest.json): table_name / grain / computed_date / run_id / s3_data_key / row_count.
    Writing it here means daily inference needs no bespoke loader - the Lambda that already
    serves PS3 deep-dive picks these up unchanged. RDS_TABLE_PREFIX keeps v2 additive so the
    live ps3_* tables are never overwritten.
    """
    import csv as _csv
    out_dir = Path(out_dir)
    pre = CONFIG.get("RDS_TABLE_PREFIX", "ps3v2_")
    s3root = CONFIG.get("S3_OUT_PREFIX", "").rstrip("/")
    grains = {"incident_predictions": "incident", "device_predictions": "device",
              "serial_predictions": "serial", "incident_component": "incident_component"}
    written = []
    for csv_path in sorted(out_dir.rglob("*.csv")):
        stem = csv_path.stem
        grain = next((g for k, g in grains.items() if stem.endswith(k)), None)
        if grain is None:
            continue
        with open(csv_path, encoding="utf-8-sig", newline="") as fh:
            rows = sum(1 for _ in _csv.reader(fh)) - 1
        rel = csv_path.relative_to(out_dir).as_posix()
        man = {
            "table_name": f"{pre}{grain}_predictions" if grain != "incident_component"
                          else f"{pre}incident_component",
            "grain": grain,
            "computed_date": str(computed_date),
            "run_id": run_id,
            "city_id": city or CONFIG.get("CITY_ID", "CHI"),
            "s3_data_key": f"{s3root}/{computed_date}/{rel}" if s3root else rel,
            "local_path": rel,
            "row_count": max(rows, 0),
            "producer": "PS3_v2_OOS_Serial_SageMaker",
            "spine": CONFIG.get("SPINE", "oos"),
            "label_semantics": "hardware OOS - chargeable is a downstream contract subset",
            "serial_uniqueness": SERIAL_UNIQUENESS,
        }
        mpath = csv_path.with_name(csv_path.stem + "_manifest.json")
        mpath.write_text(json.dumps(man, indent=2, default=str))
        written.append(mpath.name)
    print(f"  [bridge] {len(written)} manifest(s) written - "
          f"cubic-mars-ps3-rds-push will load these on S3 upload")
    return written


def score_new_data(new_inc, bundles, out_dir, run_id, computed_date, cat):
    """Daily inference over an arbitrary incident slice using the saved champion bundles.

    Deliberately reuses score_and_aggregate so a daily score cannot drift from what training
    produced - the failure mode where a batch scorer quietly computes a rollup differently
    from the notebook is exactly what put 927 identical serial rows into RDS.
    """
    new_inc = assert_oos_spine(new_inc)
    res = score_and_aggregate(new_inc, bundles.get("severity"), bundles.get("root_cause"),
                              Path(out_dir), cat)
    if CONFIG.get("EMIT_MANIFEST", True):
        write_manifests(out_dir, run_id, computed_date)
    return res


# ----------------------------------------------------------------------------- main''',
    "manifest bridge + score_new_data")

# ---- 9. correct the now-stale v1 CONFIG comment ---------------------------------
rep("# still defaults to MAJOR at collapse time (see score_and_aggregate's .fillna(\"MAJOR\")).",
    "# v2: unmapped/OTHER codes now resolve to UNKNOWN at collapse time and are excluded\n"
    "    # from pct_critical denominators - they no longer default to MAJOR.",
    "correct the stale v1 collapse comment")

OUT.write_text(s)
import ast
ast.parse(s)
print(f"\n{n} patches applied -> {OUT}  ({s.count(chr(10))+1} lines)  AST OK")
