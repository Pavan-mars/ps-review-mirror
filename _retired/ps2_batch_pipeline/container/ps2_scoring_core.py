#!/usr/bin/env python3
# =============================================================================
# ps2_scoring_core.py -- the shared, params-only scoring core for PS2 cascade
# analysis. Same role as sagemaker/ps5/batch/container/ps5_scoring_core.py:
# used by BOTH the BYOC container (serve.py, real Batch Transform requests) and
# register_mlflow.py (the MLflow pyfunc wrapper, for lineage / standalone use).
#
# Covers the three PS2 families that have genuine per-device fitted params and
# therefore a natural per-record score (confirmed with PK, 2026-07-24):
#   - markov     : subsystem-to-subsystem transition matrix, per category
#   - hmm        : 3-state Gaussian regime model (hmmlearn), fleet-wide
#   - recurrence : chronic-vs-sporadic cascade offender classification
#
# The other PS2 families (phi-correlation, conditional-probability grid,
# association rules, network centrality, facility contagion) are population /
# pairwise aggregates with no per-record prediction -- they are computed
# directly in refresh_device_state.py and loaded straight to RDS, no model
# or Batch Transform involved. Do not add them here.
#
# Logic ported from notebooks/ps2_cascading_failure/PS2_SageMaker_MLflow_FeatureStore.ipynb
# (build_markov, HMM cell, device_recurrence cell) -- same math, no plotting/mlflow calls.
# =============================================================================
import io
import json

import numpy as np
import pandas as pd

EVENT_DEF_FALLBACK = ("cascade_chain", "2026-07-24.v1")

# Identity columns that must survive scoring untouched when present on the
# input row -- serial-grain input carries these (via refresh_device_state.py's
# to_serial_grain() join onto silver.hw_config_current); device-grain input
# does not. Without this pass-through, ALL THREE families would silently drop
# COMPONENT_SERIAL_NBR/COMPONENT_DESCRIPTION during scoring and the serial-grain
# RDS load would have nothing to join on. Found + fixed 2026-07-24.
_CARRY_COLS = ("COMPONENT_SERIAL_NBR", "COMPONENT_DESCRIPTION")


def _carry(row, r):
    for c in _CARRY_COLS:
        if c in r:
            row[c] = r.get(c)
    return row


def read_table(raw_bytes):
    """Best-effort payload reader: parquet first (default), CSV fallback."""
    try:
        return pd.read_parquet(io.BytesIO(raw_bytes))
    except Exception:
        try:
            return pd.read_csv(io.BytesIO(raw_bytes))
        except Exception:
            return None


# ----------------------------------------------------------------------------
# MARKOV -- score a device's current subsystem against the fitted transition
# matrix for its category. Params: {"category", "states": [...], "matrix": [[...]]}
# ----------------------------------------------------------------------------
def score_markov(params, df):
    states = params["states"]
    idx = {s: i for i, s in enumerate(states)}
    mat = np.array(params["matrix"], dtype=float)
    out = []
    for _, r in df.iterrows():
        cur = str(r.get("current_subsystem", "")).strip()
        row = {"DEVICE_ID": r.get("DEVICE_ID"), "mars_device_category": r.get("mars_device_category"),
               "current_subsystem": cur, "family": "markov",
               "event_definition": EVENT_DEF_FALLBACK[0], "event_def_version": EVENT_DEF_FALLBACK[1]}
        if cur in idx:
            probs = mat[idx[cur]]
            nxt_i = int(np.argmax(probs))
            row["predicted_next_subsystem"] = states[nxt_i]
            row["predicted_next_prob"] = float(probs[nxt_i])
            # escalation risk = highest prob of moving to a DIFFERENT subsystem (not a self-loop)
            non_self = [(states[j], probs[j]) for j in range(len(states)) if j != idx[cur]]
            non_self.sort(key=lambda x: -x[1])
            row["escalation_subsystem"] = non_self[0][0] if non_self else None
            row["escalation_prob"] = float(non_self[0][1]) if non_self else 0.0
            row["self_loop_prob"] = float(probs[idx[cur]])
        else:
            row.update({"predicted_next_subsystem": None, "predicted_next_prob": None,
                        "escalation_subsystem": None, "escalation_prob": None, "self_loop_prob": None})
        out.append(_carry(row, r))
    return pd.DataFrame(out)


# ----------------------------------------------------------------------------
# HMM -- score a device's current cascade-day feature vector against the
# fleet-wide 3-state Gaussian HMM. Params carry the raw hmmlearn attributes
# (means_, covars_, transmat_, startprob_) serialized to JSON-safe lists, plus
# state_names / feature order, so the container never needs to unpickle a
# joblib model at serve time (keeps the image slim, matches the PS5 no-heavy-
# ML-lib-at-serve-time convention).
# ----------------------------------------------------------------------------
def _gaussian_logpdf(x, mean, var):
    var = np.clip(var, 1e-9, None)
    return -0.5 * (np.log(2 * np.pi * var) + (x - mean) ** 2 / var).sum()


def score_hmm(params, df):
    feats = params["features"]
    means = np.array(params["means"], dtype=float)          # (3, n_feat)
    covars = np.array(params["covars"], dtype=float)         # (3, n_feat) diag
    transmat = np.array(params["transmat"], dtype=float)      # (3, 3)
    state_names = params["state_names"]                      # {"0": "Minor cascade", ...}
    out = []
    for _, r in df.iterrows():
        x = np.array([float(r.get(f, means[:, i].mean())) for i, f in enumerate(feats)])
        loglik = np.array([_gaussian_logpdf(x, means[s], covars[s]) for s in range(3)])
        loglik -= loglik.max()
        post = np.exp(loglik); post /= post.sum()
        cur_state = int(np.argmax(post))
        exits = [(j, transmat[cur_state, j]) for j in range(3) if j != cur_state]
        exits.sort(key=lambda t: -t[1])
        crit_state = max(range(3), key=lambda s: means[s][feats.index("chain_length")]) if "chain_length" in feats else 2
        row = {
            "DEVICE_ID": r.get("DEVICE_ID"), "mars_device_category": r.get("mars_device_category"),
            "family": "hmm", "current_regime": state_names[str(cur_state)],
            "regime_posterior": json.dumps({state_names[str(s)]: float(post[s]) for s in range(3)}),
            "prob_escalate_to_critical": float(post[crit_state] if cur_state == crit_state
                                                else transmat[cur_state, crit_state]),
            "dwell_days_expected": float(1.0 / max(1e-9, 1.0 - transmat[cur_state, cur_state])),
            "event_definition": EVENT_DEF_FALLBACK[0], "event_def_version": EVENT_DEF_FALLBACK[1],
        }
        out.append(_carry(row, r))
    return pd.DataFrame(out)


# ----------------------------------------------------------------------------
# RECURRENCE -- classify a device's current cascade_rate against the fleet's
# fitted chronic threshold (90th percentile at train time) and report a
# percentile-based hazard score. Params: {"chronic_threshold_days", "rate_p50",
# "rate_p90", "rate_p99"}.
# ----------------------------------------------------------------------------
def score_recurrence(params, df):
    thresh = float(params["chronic_threshold_days"])
    p50, p90, p99 = (float(params.get(k, 0.0)) for k in ("rate_p50", "rate_p90", "rate_p99"))
    out = []
    for _, r in df.iterrows():
        cascade_days = float(r.get("cascade_days_total", 0))
        rate = float(r.get("cascade_rate", 0.0))
        is_chronic = cascade_days >= thresh
        if rate <= p50:
            hazard = 0.25 * (rate / p50) if p50 > 0 else 0.0
        elif rate <= p90:
            hazard = 0.25 + 0.5 * ((rate - p50) / max(p90 - p50, 1e-9))
        else:
            hazard = 0.75 + 0.25 * min(1.0, (rate - p90) / max(p99 - p90, 1e-9))
        row = {
            "DEVICE_ID": r.get("DEVICE_ID"), "mars_device_category": r.get("mars_device_category"),
            "family": "recurrence", "cascade_days_total": cascade_days, "cascade_rate": rate,
            "recurrence_class": "chronic" if is_chronic else "sporadic",
            "recurrence_hazard_score": round(float(np.clip(hazard, 0.0, 1.0)), 4),
            "event_definition": EVENT_DEF_FALLBACK[0], "event_def_version": EVENT_DEF_FALLBACK[1],
        }
        out.append(_carry(row, r))
    return pd.DataFrame(out)


FAMILY_SCORERS = {"markov": score_markov, "hmm": score_hmm, "recurrence": score_recurrence}


def score(family, params, df):
    fn = FAMILY_SCORERS.get(family)
    if fn is None:
        raise ValueError(f"unknown PS2 family '{family}' (expected one of {list(FAMILY_SCORERS)})")
    return fn(params, df)


if __name__ == "__main__":
    # Minimal self-test with synthetic data -- no S3/Databricks needed.
    markov_params = {"category": "TVM", "states": ["BHU", "CHU", "PRINTER"],
                      "matrix": [[0.5, 0.3, 0.2], [0.2, 0.6, 0.2], [0.1, 0.1, 0.8]]}
    dev_df = pd.DataFrame([{"DEVICE_ID": "TVM001", "mars_device_category": "TVM", "current_subsystem": "BHU"}])
    r1 = score("markov", markov_params, dev_df)
    assert r1.loc[0, "predicted_next_subsystem"] == "BHU"  # self-loop is argmax (0.5)
    assert r1.loc[0, "escalation_subsystem"] == "CHU"       # highest non-self transition (0.3)
    print("[selftest] markov OK ->", r1.to_dict("records"))

    # serial-grain pass-through: COMPONENT_SERIAL_NBR/COMPONENT_DESCRIPTION must
    # survive scoring untouched (2026-07-24 fix -- previously silently dropped)
    ser_df = pd.DataFrame([{"DEVICE_ID": "TVM001", "mars_device_category": "TVM", "current_subsystem": "BHU",
                            "COMPONENT_SERIAL_NBR": "SNTVM001-0", "COMPONENT_DESCRIPTION": "cbxid"}])
    r1s = score("markov", markov_params, ser_df)
    assert r1s.loc[0, "COMPONENT_SERIAL_NBR"] == "SNTVM001-0"
    assert r1s.loc[0, "COMPONENT_DESCRIPTION"] == "cbxid"
    print("[selftest] markov serial pass-through OK")

    hmm_params = {"features": ["chain_length", "critical_in_chain"],
                  "means": [[2.0, 0.1], [4.0, 0.4], [8.0, 0.9]],
                  "covars": [[1.0, 0.1], [1.5, 0.2], [2.0, 0.3]],
                  "transmat": [[0.7, 0.2, 0.1], [0.2, 0.6, 0.2], [0.1, 0.3, 0.6]],
                  "state_names": {"0": "Minor cascade", "1": "Moderate cascade", "2": "Critical cascade"}}
    hmm_df = pd.DataFrame([{"DEVICE_ID": "TVM001", "mars_device_category": "TVM",
                             "chain_length": 7.5, "critical_in_chain": 0.8}])
    r2 = score("hmm", hmm_params, hmm_df)
    assert r2.loc[0, "current_regime"] == "Critical cascade"
    print("[selftest] hmm OK ->", r2.to_dict("records"))

    hmm_ser_df = pd.DataFrame([{"DEVICE_ID": "TVM001", "mars_device_category": "TVM",
                                "chain_length": 7.5, "critical_in_chain": 0.8,
                                "COMPONENT_SERIAL_NBR": "SNTVM001-1", "COMPONENT_DESCRIPTION": "coinacceptor"}])
    r2s = score("hmm", hmm_params, hmm_ser_df)
    assert r2s.loc[0, "COMPONENT_SERIAL_NBR"] == "SNTVM001-1"
    print("[selftest] hmm serial pass-through OK")

    rec_params = {"chronic_threshold_days": 50, "rate_p50": 0.05, "rate_p90": 0.3, "rate_p99": 0.6}
    rec_df = pd.DataFrame([{"DEVICE_ID": "TVM001", "mars_device_category": "TVM",
                             "cascade_days_total": 120, "cascade_rate": 0.45}])
    r3 = score("recurrence", rec_params, rec_df)
    assert r3.loc[0, "recurrence_class"] == "chronic"
    print("[selftest] recurrence OK ->", r3.to_dict("records"))

    rec_ser_df = pd.DataFrame([{"DEVICE_ID": "TVM001", "mars_device_category": "TVM",
                                "cascade_days_total": 120, "cascade_rate": 0.45,
                                "COMPONENT_SERIAL_NBR": "SNTVM001-0", "COMPONENT_DESCRIPTION": "cbxid"}])
    r3s = score("recurrence", rec_params, rec_ser_df)
    assert r3s.loc[0, "COMPONENT_SERIAL_NBR"] == "SNTVM001-0"
    print("[selftest] recurrence serial pass-through OK")
    print("ALL SELFTESTS PASSED")
