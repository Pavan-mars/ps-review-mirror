#!/usr/bin/env python3
# =============================================================================
# fit_ps2_models.py -- the RETRAIN-time half of the PS2 pipeline (run from the
# SageMaker notebook, weekly/monthly -- same cadence split as PS5). Fits the
# three PS2 families that have genuine per-device fitted params and exports
# them as JSON, ready for package_model.py. Logic ported directly from
# notebooks/ps2_cascading_failure/PS2_SageMaker_MLflow_FeatureStore.ipynb
# (build_markov / HMM cell / device_recurrence cell) -- same math, no
# plotting/mlflow-logging cruft (register_mlflow.py handles MLflow lineage
# separately, after this script produces the params).
#
# Does NOT touch Databricks gold/silver -- reads gold.device_ps2_chains as a
# read-only Parquet export from S3 (same convention the existing PS2 notebook
# already uses: s3://<gold-bucket>/chicago/gold/device_ps2_chains).
#
# Usage:
#   python fit_ps2_models.py --gold-s3 s3://cubic-mars-pm-s3-datalake-dev-gold-170202974600/chicago/gold/device_ps2_chains \
#     --out-dir ./ps2_model_out
#   # offline dry-run: add --synthetic (no S3, builds a small synthetic frame)
# =============================================================================
import argparse, json, os

import numpy as np
import pandas as pd


def parse_chain(chain_str, sep="->"):
    if pd.isna(chain_str) or not chain_str:
        return []
    return [s.strip() for s in str(chain_str).split(sep) if s.strip()]


# ----------------------------------------------------------------------------
# MARKOV -- fit a transition matrix per device category (ported from build_markov)
# ----------------------------------------------------------------------------
def build_markov(df_sub, chain_col="subsystem_chain"):
    chains = (df_sub[chain_col].dropna().apply(parse_chain).tolist())
    chains = [c for c in chains if len(c) >= 2]
    if not chains:
        return None, [], 0
    states = sorted(set(s for c in chains for s in c))
    idx = {s: i for i, s in enumerate(states)}
    n = len(states)
    trans = np.zeros((n, n))
    for chain in chains:
        for a, b in zip(chain[:-1], chain[1:]):
            trans[idx[a], idx[b]] += 1
    row_sums = trans.sum(axis=1, keepdims=True)
    row_sums[row_sums == 0] = 1
    return trans / row_sums, states, len(chains)


def fit_markov(df, out_dir):
    written = []
    if "mars_device_category" not in df.columns:
        print("  [markov] no mars_device_category column -- skipped"); return written
    for cat in sorted(df["mars_device_category"].dropna().unique()):
        tp, states, n_chains = build_markov(df[df["mars_device_category"] == cat])
        if tp is None:
            print(f"  [markov] {cat}: no multi-subsystem chains -- skipped"); continue
        params = {"category": cat, "states": states, "matrix": tp.tolist(), "n_chains": n_chains,
                  "event_definition": "cascade_chain", "event_def_version": "2026-07-24.v1"}
        path = os.path.join(out_dir, f"{cat.lower()}_markov_params.json")
        json.dump(params, open(path, "w"), indent=2)
        print(f"  [markov] {cat}: {len(states)} states, {n_chains} chains -> {os.path.basename(path)}")
        written.append(path)
    return written


# ----------------------------------------------------------------------------
# HMM -- fit a single fleet-wide 3-state Gaussian HMM (ported from HMM cell)
# ----------------------------------------------------------------------------
def fit_hmm(df, out_dir):
    from hmmlearn import hmm as _hmm

    feats = [c for c in ["chain_length", "critical_in_chain", "oos_in_chain",
                          "chain_span_min", "distinct_subsystems"] if c in df.columns]
    if len(feats) < 2:
        print("  [hmm] insufficient features -- skipped"); return None

    hmm_data = (df.sort_values(["DEVICE_ID", "transit_day"]).groupby("DEVICE_ID")[feats]
                  .apply(lambda x: x.values.tolist()).reset_index().rename(columns={0: "sequence"}))
    sequences = [np.array(seq) for seq in hmm_data["sequence"] if len(seq) >= 3]
    if not sequences:
        print("  [hmm] no device has >=3 cascade days -- skipped"); return None

    X = np.vstack(sequences); lengths = [len(s) for s in sequences]
    model = _hmm.GaussianHMM(n_components=3, covariance_type="diag", n_iter=150, random_state=42, verbose=False)
    model.fit(X, lengths)

    cl_idx = feats.index("chain_length") if "chain_length" in feats else 0
    order = np.argsort(model.means_[:, cl_idx])
    state_names = {str(order[0]): "Minor cascade", str(order[1]): "Moderate cascade", str(order[2]): "Critical cascade"}

    params = {
        "features": feats,
        "means": model.means_.tolist(),
        "covars": (model.covars_ if model.covars_.ndim == 2 else model.covars_.reshape(3, -1)).tolist(),
        "transmat": model.transmat_.tolist(),
        "state_names": state_names,
        "n_sequences": len(sequences), "n_observations": int(X.shape[0]),
        "event_definition": "cascade_chain", "event_def_version": "2026-07-24.v1",
    }
    path = os.path.join(out_dir, "ps2_hmm_params.json")
    json.dump(params, open(path, "w"), indent=2)
    print(f"  [hmm] {len(sequences)} sequences, {X.shape[0]} observations -> {os.path.basename(path)}")
    return path


# ----------------------------------------------------------------------------
# RECURRENCE -- fit the chronic threshold + fleet rate percentiles (ported
# from the device_recurrence cell)
# ----------------------------------------------------------------------------
def fit_recurrence(df, out_dir):
    dev_stats = df.groupby("DEVICE_ID").agg(
        cascade_days=("transit_day", "count"), first_cascade=("transit_day", "min"),
        last_cascade=("transit_day", "max"))
    dev_stats["active_days"] = (dev_stats["last_cascade"] - dev_stats["first_cascade"]).dt.days + 1
    dev_stats["cascade_rate"] = dev_stats["cascade_days"] / dev_stats["active_days"].clip(lower=1)

    chronic_thresh = float(dev_stats["cascade_days"].quantile(0.90))
    rate_p50, rate_p90, rate_p99 = (float(dev_stats["cascade_rate"].quantile(q)) for q in (0.5, 0.9, 0.99))
    params = {"chronic_threshold_days": chronic_thresh, "rate_p50": rate_p50, "rate_p90": rate_p90,
              "rate_p99": rate_p99, "n_devices_fit": int(len(dev_stats)),
              "event_definition": "cascade_chain", "event_def_version": "2026-07-24.v1"}
    path = os.path.join(out_dir, "ps2_recurrence_params.json")
    json.dump(params, open(path, "w"), indent=2)
    print(f"  [recurrence] chronic_threshold={chronic_thresh:.0f}d, "
          f"rate p50/p90/p99={rate_p50:.3f}/{rate_p90:.3f}/{rate_p99:.3f} -> {os.path.basename(path)}")
    return path


def make_synthetic(n=200, seed=7):
    rng = np.random.default_rng(seed)
    subs = ["BHU", "CHU", "PRINTER", "CSC_READER", "SCRST"]
    cats = ["TVM", "GATE", "VALIDATOR"]
    rows = []
    days = pd.date_range("2025-01-01", periods=180, freq="D")
    for i in range(n):
        dev = f"{rng.choice(cats)}{i:05d}"
        cat = dev[:3] if dev[:3] in cats else "TVM"
        n_days = rng.integers(1, 60)
        for d in rng.choice(days, size=n_days, replace=False):
            k = rng.integers(2, 5)
            chain = rng.choice(subs, size=k, replace=True)
            rows.append({
                "DEVICE_ID": dev, "mars_device_category": cat, "transit_day": pd.Timestamp(d),
                "subsystem_chain": "->".join(chain), "chain_length": k,
                "critical_in_chain": rng.integers(0, 2), "oos_in_chain": rng.integers(0, 2),
                "chain_span_min": float(rng.integers(1, 90)), "distinct_subsystems": len(set(chain)),
            })
    return pd.DataFrame(rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--gold-s3", default=None, help="s3://.../chicago/gold/device_ps2_chains")
    ap.add_argument("--out-dir", default="ps2_model_out")
    ap.add_argument("--region", default="us-east-1")
    ap.add_argument("--synthetic", action="store_true")
    args = ap.parse_args()

    os.makedirs(args.out_dir, exist_ok=True)
    if args.synthetic:
        df = make_synthetic()
        print(f"[fit] synthetic frame: {df.shape}")
    else:
        if not args.gold_s3:
            raise SystemExit("--gold-s3 is required unless --synthetic")
        df = pd.read_parquet(args.gold_s3, storage_options={"client_kwargs": {"region_name": args.region}})
        df["transit_day"] = pd.to_datetime(df["transit_day"])
        print(f"[fit] loaded {args.gold_s3}: {df.shape}")

    written = []
    written += fit_markov(df, args.out_dir)
    hp = fit_hmm(df, args.out_dir)
    if hp:
        written.append(hp)
    written.append(fit_recurrence(df, args.out_dir))
    print(f"FIT done: {len(written)} param files in {args.out_dir}")


if __name__ == "__main__":
    main()
