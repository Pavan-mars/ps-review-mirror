# =============================================================================
# ps3_serial_grain -- restore TRUE component/serial granularity at NOTEBOOK level.
#
# WHY THIS EXISTS
# ---------------
# gold.device_ps3_incident carries exactly ONE component per incident. That is by
# design: the gold DDL's `hw_best_match` CTE ends `WHERE rn = 1` (FIX 7), added to
# stop a 1.41x fan-out that was duplicating incident rows. Correct for the incident
# grain -- but it means `matched_serial_nbr` is one value per incident, so any
# groupby(["device_id","matched_serial_nbr"]) downstream can only ever emit ONE row
# per device.
#
# Measured on the 19-Jul run: 927 device rows and 927 serial rows, identical on
# pct_critical_pred / n_incidents / dominant_pred_component (927/927 each). TVM had
# 472 devices against 223 distinct serials -- 2.12 devices per serial.
#
# This module re-derives the fan-out by reading silver.hw_config_current directly.
# NOTHING in silver or gold is modified. Both the training notebook and
# batch_transform_daily.py import this, so training and daily inference cannot drift.
#
# ATTRIBUTION vs EXPOSURE - read this before presenting serial numbers
# --------------------------------------------------------------------
# 78.32% of incidents have a blank `affected_component` (V04 finding, quoted in the
# gold DDL header). For those we cannot say which component failed -- only which
# components were INSTALLED at the time. Every output row therefore carries
# `attribution_method`:
#     description_match : the component description matched affected_component
#     exposure          : the component was merely installed on the device
# Rollups report attributed and exposed counts SEPARATELY. A serial-level risk
# ranking must use the attributed counts, or say plainly that it is exposure.
#
# SERIALS ARE NOT GLOBALLY UNIQUE
# -------------------------------
# COMPONENT_SERIAL_NBR repeats across devices in the source (144 TVM serials sit on
# more than one device, affecting 393 of 472). Every key here is therefore
# (device_id, COMPONENT_SERIAL_NBR), never the serial alone. `serial_uniqueness_report`
# quantifies it so the number is visible instead of assumed.
# =============================================================================
import numpy as np
import re
import pandas as pd

HW_COLS = ["DEVICE_ID", "COMPONENT_SERIAL_NBR", "COMPONENT_DESCRIPTION",
           "COMPONENT_TYPE", "COMPONENT_PART_NBR", "COMPONENT_MANUFACTURER",
           "component_age_days", "REPORTED_CHANGED_DTM", "mars_device_category"]


def _norm(s):
    return s.astype(str).str.strip().str.upper()


# Accepted spellings for each column this module needs. Databricks, the Glue
# exporter and the parquet writer do not agree on case, and some paths prefix
# hardware columns with hw_. Rather than assume one spelling and die on a bare
# KeyError deep in the join, resolve case-insensitively up front and fail with a
# message that names what WAS found.
_COL_ALIASES = {
    "DEVICE_ID":              ("device_id", "deviceid", "hw_device_id", "dev_id"),
    "COMPONENT_SERIAL_NBR":   ("component_serial_nbr", "componentserialnbr",
                               "component_serial_number", "serial_nbr", "serial_number",
                               "matched_serial_nbr", "hw_component_serial_nbr"),
    "COMPONENT_DESCRIPTION":  ("component_description", "componentdescription",
                               "component_desc", "description"),
    "COMPONENT_TYPE":         ("component_type", "componenttype", "comp_type"),
    "COMPONENT_PART_NBR":     ("component_part_nbr", "part_nbr", "part_number"),
    "COMPONENT_MANUFACTURER": ("component_manufacturer", "manufacturer", "mfr"),
    "component_age_days":     ("component_age_days", "componentagedays", "age_days"),
    "REPORTED_CHANGED_DTM":   ("reported_changed_dtm", "reportedchangeddtm",
                               "changed_dtm", "install_dtm"),
    "mars_device_category":   ("mars_device_category", "device_category",
                               "marsdevicecategory", "category"),
}
_REQUIRED = ("DEVICE_ID", "COMPONENT_SERIAL_NBR")


def canonicalise_hw_columns(hw):
    """Rename whatever hw_config_current actually arrived with onto the canonical
    names this module uses. Returns (renamed_frame, resolved_map)."""
    lower = {str(c).strip().lower(): c for c in hw.columns}
    ren, resolved = {}, {}
    for canon, aliases in _COL_ALIASES.items():
        cand = [canon.lower()] + [a.lower() for a in aliases]
        for a in cand:
            if a in lower:
                ren[lower[a]] = canon
                resolved[canon] = lower[a]
                break
    missing = [c for c in _REQUIRED if c not in resolved]
    if missing:
        raise KeyError(
            "hw_config_current is missing required column(s) "
            f"{missing}. Columns actually present: {sorted(map(str, hw.columns))}. "
            "Fix CONFIG['HWCONFIG_PARQUET'] / CONFIG['HWCONFIG_TABLE'] to point at "
            "silver.hw_config_current, or add the spelling to _COL_ALIASES in "
            "ps3_serial_grain.py."
        )
    return hw.rename(columns=ren), resolved


def load_hw_config(load_table, config, category=None):
    """Read silver.hw_config_current. `load_table` is the engine's own loader
    (Spark -> SQL -> parquet fallback), so this inherits its auth and retry path."""
    hw = load_table(config["HWCONFIG_TABLE"], config.get("HWCONFIG_PARQUET"))
    if hw is None or len(hw) == 0:
        print("  [serial] hw_config_current unavailable - serial fan-out SKIPPED "
              "(the run continues at device grain)")
        return None
    hw, resolved = canonicalise_hw_columns(hw)
    renamed = {k: v for k, v in resolved.items() if k != v}
    if renamed:
        print(f"  [serial] hw_config_current column names normalised: {renamed}")
    keep = [c for c in HW_COLS if c in hw.columns]
    hw = hw[keep].copy()
    if category and "mars_device_category" in hw.columns:
        hw = hw[hw["mars_device_category"].astype(str).str.upper() == category.upper()]
        if hw.empty:
            print(f"  [serial] hw_config_current has no rows for category "
                  f"{category!r} - serial fan-out SKIPPED for this device type")
            return None
    for c in ("DEVICE_ID", "COMPONENT_SERIAL_NBR"):
        hw[c] = hw[c].astype(str).str.strip()
    hw = hw[hw["COMPONENT_SERIAL_NBR"].notna()
            & ~hw["COMPONENT_SERIAL_NBR"].isin(["", "nan", "None", "NaN", "NULL", "null"])]
    if hw.empty:
        print("  [serial] every COMPONENT_SERIAL_NBR was null/blank - serial fan-out SKIPPED")
        return None
    hw = hw.drop_duplicates(subset=["DEVICE_ID", "COMPONENT_SERIAL_NBR"])
    print(f"  [serial] hw_config_current: {len(hw):,} device-component rows, "
          f"{hw['DEVICE_ID'].nunique():,} devices, "
          f"{hw['COMPONENT_SERIAL_NBR'].nunique():,} distinct serials")
    return hw


def serial_uniqueness_report(hw):
    """Quantify the non-uniqueness rather than assuming it away."""
    if hw is None or hw.empty:
        return {}
    per_serial = hw.groupby("COMPONENT_SERIAL_NBR")["DEVICE_ID"].nunique()
    shared = per_serial[per_serial > 1]
    per_device = hw.groupby("DEVICE_ID")["COMPONENT_SERIAL_NBR"].nunique()
    rep = {
        "devices": int(hw["DEVICE_ID"].nunique()),
        "distinct_serials": int(hw["COMPONENT_SERIAL_NBR"].nunique()),
        "components_per_device_mean": round(float(per_device.mean()), 3),
        "components_per_device_max": int(per_device.max()),
        "serials_on_multiple_devices": int(len(shared)),
        "pct_serials_shared": round(100.0 * len(shared) / max(len(per_serial), 1), 2),
        "worst_serial_device_count": int(shared.max()) if len(shared) else 1,
    }
    print(f"  [serial] components/device mean {rep['components_per_device_mean']} "
          f"max {rep['components_per_device_max']}")
    print(f"  [serial] serials on >1 device: {rep['serials_on_multiple_devices']:,} "
          f"({rep['pct_serials_shared']}%) - keys are (device_id, serial), never serial alone")
    if rep["components_per_device_mean"] <= 1.0:
        print("  [serial] WARNING: no fan-out in hw_config_current either - the serial grain "
              "will still be 1:1 with device. Do NOT publish a serial risk ranking.")
    return rep


# Candidate incident-side columns naming the component implicated in the failure,
# best evidence first. `affected_component` was the only one tried before, and it
# does not exist on this gold table -- so every row silently fell through to
# "exposure" and the 26-Jul run reported 0.0% attributed.
_ATTR_SOURCES = ("affected_component", "actual_component", "derived_component_type",
                 "pred_component", "component_type", "failure_component")

def _tokens(s_):
    """Alphanumeric tokens of length >= 3, upper-cased. 'CSC_READER' -> {'CSC','READER'}."""
    return [set(t for t in re.split(r"[^A-Z0-9]+", v) if len(t) >= 3)
            for v in s_.astype(str).str.upper()]

def _attribute(exp):
    """Mark each incident-component row as description_match or exposure.

    A component is ATTRIBUTED when the incident names it; otherwise it was merely
    INSTALLED at the time (exposure). Keeping the two apart is the whole point --
    a device with 12 components would otherwise spread one failure across all 12
    and every component would look mildly guilty.
    """
    src = next((c for c in _ATTR_SOURCES if c in exp.columns), None)
    if src is None or "COMPONENT_DESCRIPTION" not in exp.columns:
        exp["attribution_method"] = "exposure"
        have = [c for c in _ATTR_SOURCES if c in exp.columns]
        print(f"  [serial] no attribution source available (looked for {list(_ATTR_SOURCES)}; "
              f"found {have or 'none'}; COMPONENT_DESCRIPTION "
              f"{'present' if 'COMPONENT_DESCRIPTION' in exp.columns else 'MISSING'}) "
              f"-- every row is exposure-only")
        return exp

    a_raw = exp[src].fillna("").astype(str)
    d_raw = exp["COMPONENT_DESCRIPTION"].fillna("").astype(str)
    a, d = _norm(a_raw), _norm(d_raw)
    # containment in EITHER direction: 'BHU' inside 'BHU MODULE', and equally
    # 'BILL ACCEPTOR' inside an incident text naming the bill acceptor.
    contains = np.array([bool(x) and bool(y) and (x in y or y in x)
                         for x, y in zip(a, d)], dtype=bool)
    # token overlap on tokens of 3+ chars, so CSC_READER matches 'CSC READER ASSY'
    at, dt = _tokens(a_raw), _tokens(d_raw)
    overlap = np.array([bool(x & y) for x, y in zip(at, dt)], dtype=bool)
    matched = contains | overlap
    exp["attribution_method"] = np.where(matched, "description_match", "exposure")
    rate = 100.0 * matched.sum() / max(len(exp), 1)
    print(f"  [serial] attribution source: {src!r} vs COMPONENT_DESCRIPTION -> {rate:.1f}% matched")
    if matched.sum() == 0:
        pairs = (pd.DataFrame({"incident": a_raw, "component": d_raw})
                 .value_counts().head(5))
        print("  [serial] NOTHING matched. The two vocabularies do not overlap; "
              "most common unmatched pairs (incident vs hw description):")
        for (iv, cv), n in pairs.items():
            print(f"             {n:>7,}x  {iv!r:28s} vs {cv!r}")
        print("  [serial] serial rows will carry pct_critical_pred = NULL and "
              "attribution_basis = 'exposure_only'. A crosswalk from the incident "
              "component vocabulary to COMPONENT_DESCRIPTION is what unlocks it.")
    return exp

def expand_serial_grain(inc, hw, config):
    """Explode the incident frame to component grain.

    inc : scored incident frame (must carry device_id; affected_component optional)
    hw  : output of load_hw_config
    -> one row per (incident, component) with attribution_method and match_rank.
    """
    if hw is None or hw.empty or not config.get("SERIAL_FANOUT", True):
        return None
    if "device_id" not in inc.columns:
        print("  [serial] incident frame has no device_id - fan-out skipped")
        return None

    left = inc.copy()
    left["_dev"] = left["device_id"].astype(str).str.strip()
    # FIX 26-Jul-2026: gold's incident frame already carries `matched_serial_nbr` --
    # the single collapsed serial that hw_best_match's WHERE rn = 1 leaves behind.
    # Renaming COMPONENT_SERIAL_NBR onto that same name below produced TWO columns
    # called matched_serial_nbr, so exp["matched_serial_nbr"] returned a DataFrame
    # and groupby raised "Grouper for 'matched_serial_nbr' not 1-dimensional".
    # Move gold's out of the way first and keep it: comparing gold's single pick
    # against the real fan-out is exactly the evidence that the collapse was real.
    if "matched_serial_nbr" in left.columns:
        left = left.rename(columns={"matched_serial_nbr": "gold_matched_serial_nbr"})
    right = hw.rename(columns={"DEVICE_ID": "_dev"})
    exp = left.merge(right, on="_dev", how="inner", suffixes=("", "_hw"))
    if exp.empty:
        print("  [serial] no incident joined to hw_config_current - fan-out produced 0 rows")
        return None

    exp = _attribute(exp)
    exp["match_rank"] = np.where(exp["attribution_method"] == "description_match", 0, 1)
    exp = exp.rename(columns={"COMPONENT_SERIAL_NBR": "matched_serial_nbr"})
    dupes = exp.columns[exp.columns.duplicated()].unique().tolist()
    if dupes:
        raise ValueError(
            f"expand_serial_grain produced duplicate column name(s) {dupes}. "
            "groupby would fail with 'not 1-dimensional'. Rename the incident-side "
            "column before the hw rename, as is done for matched_serial_nbr above.")
    if "component_age_days_hw" in exp.columns:
        exp["component_age_days"] = exp["component_age_days_hw"]
    exp = exp.drop(columns=[c for c in exp.columns if c.endswith("_hw")] + ["_dev"],
                   errors="ignore")

    n_att = int((exp["attribution_method"] == "description_match").sum())
    print(f"  [serial] fan-out: {len(inc):,} incidents -> {len(exp):,} incident-component rows "
          f"({len(exp)/max(len(inc),1):.2f}x); {n_att:,} attributed by description "
          f"({100*n_att/max(len(exp),1):.1f}%), rest exposure")
    return exp


def serial_rollup(exp, config, cat):
    """Serial-grain rollup keyed on (device_id, matched_serial_nbr).

    Attributed and exposure counts stay separate so a consumer can never mistake
    'this component was installed' for 'this component failed'.
    """
    if exp is None or exp.empty:
        return None
    att = exp["attribution_method"] == "description_match"
    g = exp.groupby(["device_id", "matched_serial_nbr"])

    out = pd.DataFrame({"n_incidents_exposed": g.size()})
    out["n_incidents_attributed"] = exp[att].groupby(
        ["device_id", "matched_serial_nbr"]).size().reindex(out.index).fillna(0).astype(int)

    for src, dst, fn in [("component_age_days", "component_age_days", "max"),
                         ("COMPONENT_TYPE", "component_type", "first"),
                         ("COMPONENT_DESCRIPTION", "component_description", "first"),
                         ("COMPONENT_MANUFACTURER", "component_manufacturer", "first")]:
        if src in exp.columns:
            out[dst] = getattr(g[src], fn)()

    if "pred_component" in exp.columns:
        out["dominant_pred_component"] = g["pred_component"].agg(
            lambda x: x.value_counts().index[0] if len(x) else None)
    if "pred_severity_collapsed" in exp.columns:
        # attributed rows only - an exposure row says nothing about this component's risk
        sub = exp[att]
        if len(sub):
            pc = sub.groupby(["device_id", "matched_serial_nbr"])["pred_severity_collapsed"].apply(
                lambda x: (x == "CRITICAL").mean()).round(4)
            out["pct_critical_attributed"] = pc.reindex(out.index)
        else:
            out["pct_critical_attributed"] = np.nan
        out["pct_critical_exposed"] = g["pred_severity_collapsed"].apply(
            lambda x: (x == "CRITICAL").mean()).round(4)
    if "AE_START_DTM" in exp.columns:
        out["last_incident_dtm"] = g["AE_START_DTM"].max()

    out = out.reset_index()
    out["mars_device_category"] = cat
    out["attribution_basis"] = np.where(out["n_incidents_attributed"] > 0,
                                        "description_match", "exposure_only")
    out = out[out["n_incidents_exposed"] >= config.get("SERIAL_MIN_INCIDENTS", 1)]
    out = out.sort_values(["n_incidents_attributed", "n_incidents_exposed"], ascending=False)

    # ---- canonical column names for ps3_serial_predictions (added 26-Jul-2026) ----
    # The rollup's own names are deliberately explicit (exposed vs attributed), but
    # the RDS table and the dashboard read `n_incidents` and `pct_critical_pred`.
    # Without this mapping the load succeeds and the Component Risk view still shows
    # nothing, which is the worst of both outcomes.
    out["n_incidents"] = out["n_incidents_exposed"]
    if "pct_critical_attributed" in out.columns:
        # ATTRIBUTED ONLY, on purpose. An exposure-derived share is the DEVICE's
        # critical rate copied onto every component installed on it -- it carries no
        # component-specific information, so publishing it as a per-component risk
        # would invent a signal. Exposure-only rows get NULL here and keep
        # pct_critical_exposed alongside, with attribution_basis saying which it is.
        out["pct_critical_pred"] = out["pct_critical_attributed"]
    n_att = int((out["n_incidents_attributed"] > 0).sum())
    print(f"  [serial] rollup: {len(out):,} (device, serial) rows | "
          f"{n_att:,} have a description-matched incident")
    if n_att == 0 and len(out):
        print("  [serial] all rows are exposure-only -> pct_critical_pred is NULL for every "
              "row. n_incidents, component age and dominant_pred_component are still real; "
              "the per-component CRITICAL share is not derivable without attribution.")
    return out
