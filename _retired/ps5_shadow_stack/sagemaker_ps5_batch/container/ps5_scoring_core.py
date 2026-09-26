# =============================================================================
# ps5_scoring_core -- the ONE verified PS5 RUL scoring core (params-only, numpy).
# Shared by: the BYOC Batch-Transform container (serve.py), the S3->RDS loader,
# and the ps5_daily_scorer Lambda. No sklearn / scikit-survival / boto3 here --
# just numpy + pandas + the survival math. Reproduces the trained RUL to the digit
# (verified: numpy-serve == sksurv-train, corr 1.00000).
#
# Event = hardware OOS 'Set' (event_def_version 2026-07-23.v1).
# =============================================================================
import math
import datetime as _dt

import numpy as np
import pandas as pd

_TRAPZ = getattr(np, "trapezoid", None) or np.trapz   # numpy>=2 renamed trapz -> trapezoid
EVENT_DEF_FALLBACK = ("hw_oos_set", "2026-07-23.v1")


# ---- Weibull survival math (identical to the training engine) ----------------
def weibull_survival(t, shape, scale):
    shape = max(float(shape), 1e-6); scale = max(float(scale), 1e-6)
    return np.exp(-np.power(np.clip(np.asarray(t, float), 0.0, None) / scale, shape))


def weibull_mean(shape, scale):
    return float(scale) * math.gamma(1.0 + 1.0 / max(float(shape), 1e-6))


def conditional_mrl(age, shape, scale, horizon_mult=6.0, n=400, cap=None):
    """Expected residual life E[T-a | T>a] for Weibull(shape, scale) -- the RUL fix."""
    age = max(float(age), 0.0)
    Sa = float(weibull_survival(age, shape, scale))
    if Sa < 1e-6:
        val = float(max(weibull_mean(shape, scale) * 0.05, 1.0))
    else:
        hi = age + horizon_mult * float(scale)
        xs = np.linspace(age, hi, n)
        val = float(max(_TRAPZ(weibull_survival(xs, shape, scale), xs) / Sa, 0.0))
    return float(min(val, cap)) if cap else val


# ---- device-grain scoring (reproduces score_device_rul from the engine) -------
def score_devices(params, state_df, feature_asof_date=None):
    cat = params.get("category", "")
    shape = float(params["weibull"]["shape"]); scale = float(params["weibull"]["scale"])
    cap = float(params.get("rul_cap_days", 3650.0))
    bands = params.get("hazard_pctl_bands", {"CRITICAL": 0.90, "HIGH": 0.70, "MEDIUM": 0.40})
    df = state_df.copy()
    ages = pd.to_numeric(df["current_healthy_age_days"], errors="coerce").fillna(0.0).clip(lower=0).values

    cox = params.get("cox")
    if cox and cox.get("features"):
        feats = cox["features"]
        coef = np.asarray(cox["coef"], float)
        mean = np.asarray(cox["feature_means"], float)
        std = np.asarray(cox["feature_stds"], float); std[std == 0] = 1.0
        lp_med = float(cox.get("lp_median", 0.0))
        X = np.column_stack([
            pd.to_numeric(df[f], errors="coerce").values if f in df.columns else np.full(len(df), mean[j])
            for j, f in enumerate(feats)
        ]).astype(float)
        X = np.where(np.isnan(X), mean, X)
        lp = ((X - mean) / std) @ coef                     # == sksurv/lifelines CoxPH linear predictor
        lpc = np.clip(lp - lp_med, -3.0, 3.0)
        scale_i = scale * np.exp(-lpc / max(shape, 0.3))
        hz = pd.Series(lpc).rank(pct=True).values
    else:
        scale_i = np.full(len(df), scale); hz = np.full(len(df), 0.5)

    rul = np.array([conditional_mrl(a, shape, s, cap=cap) for a, s in zip(ages, scale_i)])
    med_i = np.minimum(scale_i * (math.log(2.0) ** (1.0 / max(shape, 0.3))), cap)
    band = np.where(hz >= bands["CRITICAL"], "CRITICAL",
                    np.where(hz >= bands["HIGH"], "HIGH",
                             np.where(hz >= bands["MEDIUM"], "MEDIUM", "LOW")))
    asof = feature_asof_date or params.get("run_date") or _dt.date.today().isoformat()
    dsh = (pd.to_numeric(df["days_since_hw_oos"], errors="coerce").fillna(0.0).values
           if "days_since_hw_oos" in df.columns else ages)
    rf30 = (pd.to_numeric(df["roll_fail_30d"], errors="coerce").fillna(0).astype(int).values
            if "roll_fail_30d" in df.columns else np.zeros(len(df), dtype=int))
    nprior = (pd.to_numeric(df["failure_seq"], errors="coerce").fillna(0).astype(int).values
              if "failure_seq" in df.columns else np.zeros(len(df), dtype=int))
    fac = (df["facility_id"].values if "facility_id" in df.columns else np.array([None] * len(df), dtype=object))
    return pd.DataFrame({
        "device_id": df["DEVICE_ID"].values, "mars_device_category": cat,
        "current_healthy_age_days": np.round(ages, 1), "rul_standard_days": np.round(rul, 1),
        "predicted_median_survival_days": np.round(med_i, 1),
        "hazard_score": np.round(hz, 4), "risk_band": band, "is_overdue": (ages > med_i),
        "n_prior_oos": nprior, "days_since_hw_oos": np.round(dsh, 1), "roll_fail_30d": rf30, "facility_id": fac,
        "feature_asof_date": asof,
        "event_definition": params.get("event_definition", EVENT_DEF_FALLBACK[0]),
        "event_def_version": params.get("event_def_version", EVENT_DEF_FALLBACK[1]),
        "cv_cindex": params.get("cv_cindex"), "gate_pass": bool(params.get("gate_pass", False)),
        "champion": params.get("champion", "CoxPH (params-only serving)"),
        "weibull_shape": round(shape, 4),
    }).sort_values("rul_standard_days")


# ---- serial-grain scoring (reproduces run_serial_grain from the engine) -------
def score_serials(params, roster_df, feature_asof_date=None):
    cat = params.get("category", "")
    shape = float(params["weibull"]["shape"]); scale = float(params["weibull"]["scale"])
    cap = float(params.get("rul_cap_days", 3650.0))
    tiers = params.get("risk_tiers", [[0.02, "CRITICAL"], [0.008, "HIGH"], [0.002, "MEDIUM"], [-1.0, "LOW"]])

    def tier(rs):
        for thr, name in tiers:
            if rs > thr:
                return name
        return "LOW"

    df = roster_df.copy()
    age = pd.to_numeric(df["component_age_days"], errors="coerce").fillna(0.0).clip(lower=0).values
    fails = pd.to_numeric(df.get("device_oos_failures_total", 0), errors="coerce").fillna(0).values
    rs = np.round(fails / np.clip(age, 1, None), 6)
    med = round(min(scale * (math.log(2.0) ** (1.0 / max(shape, 0.3))), cap), 1)
    return pd.DataFrame({
        "device_id": df["DEVICE_ID"].values,
        "component_serial_nbr": df["COMPONENT_SERIAL_NBR"].values,
        "component_type_name": df.get("COMPONENT_TYPE_NAME", pd.Series(["OTHER"] * len(df))).values,
        "mars_device_category": cat, "component_age_days": np.round(age, 1),
        "device_oos_failures_total": fails.astype(int),
        "risk_score": rs, "risk_tier": [tier(x) for x in rs],
        "expected_component_rul_days": np.round([conditional_mrl(a, shape, scale, cap=cap) for a in age], 1),
        "predicted_median_survival_days": med, "is_overdue": (age > med),
        "feature_asof_date": feature_asof_date or params.get("run_date") or _dt.date.today().isoformat(),
        "event_definition": params.get("event_definition", EVENT_DEF_FALLBACK[0]),
        "event_def_version": params.get("event_def_version", EVENT_DEF_FALLBACK[1]),
    }).sort_values("risk_score", ascending=False)


def read_table(path_or_bytes):
    """Read parquet if possible, else CSV. Accepts a local path or raw bytes (the /invocations payload)."""
    import io
    if isinstance(path_or_bytes, (bytes, bytearray)):
        buf = io.BytesIO(path_or_bytes)
        try:
            return pd.read_parquet(buf)
        except Exception:
            buf.seek(0); return pd.read_csv(buf)
    if str(path_or_bytes).endswith(".csv"):
        return pd.read_csv(path_or_bytes)
    try:
        return pd.read_parquet(path_or_bytes)
    except Exception:
        return pd.read_csv(path_or_bytes)
