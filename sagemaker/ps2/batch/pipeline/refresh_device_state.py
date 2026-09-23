#!/usr/bin/env python3
# =============================================================================
# refresh_device_state.py -- the DAILY-moving half of the PS2 pipeline.
#
# Two jobs, same script (mirrors sagemaker/ps5/batch/pipeline/refresh_device_state.py):
#  1. SCOREABLE families (markov, hmm, recurrence): emit each device's CURRENT
#     state as of the scoring date -> <family>_device_state.parquet, which
#     Batch Transform then scores against the fitted params from fit_ps2_models.py.
#     Also emits a SERIAL-grain view by joining onto silver.hw_config_current
#     (the authoritative device<->serial catalog -- NOT device_ps2_chains'
#     sparse per-event serial column; same fix applied to the OOS/chargeable
#     mapping earlier this project, 2026-07-24). Serial rows repeat the
#     device's score per known component, same caveat as PS5's serial view.
#  2. POPULATION-level families (phi-correlation, conditional probability,
#     association rules, network centrality, facility contagion): these have
#     no per-record prediction, so they are computed directly here (no model,
#     no Batch Transform) and written straight to S3 for the RDS loader.
#
# Does NOT touch Databricks gold/silver -- reads read-only Parquet exports:
#   gold.device_ps2_chains          s3://<gold-bucket>/chicago/gold/device_ps2_chains
#   silver.hw_config_current        s3://<gold-bucket>/chicago/silver/hw_config_current
# (same bucket/prefix convention already used by the existing PS2 notebook and
# by ps5_reliability_engine_v51.py's load_table() S3 fallback.)
#
# Usage:
#   python refresh_device_state.py --gold-s3 s3://.../chicago/gold/device_ps2_chains \
#     --hwconfig-s3 s3://.../chicago/silver/hw_config_current \
#     --asof 2026-07-24 --bucket cubic-mars-pm-s3-datalake-dev-gold-170202974600 \
#     --state-prefix chicago/ps2/state --population-prefix chicago/ps2/population
#   # offline dry-run: add --synthetic (no S3, writes locally only)
# =============================================================================
import argparse, itertools, os

import numpy as np
import pandas as pd


def parse_chain(chain_str, sep="->"):
    if pd.isna(chain_str) or not chain_str:
        return []
    return [s.strip() for s in str(chain_str).split(sep) if s.strip()]


# ----------------------------------------------------------------------------
# SCOREABLE-FAMILY DEVICE STATE
# ----------------------------------------------------------------------------
def markov_device_state(df):
    """Each device's most recent chain -> its current (last) subsystem."""
    latest = df.sort_values("transit_day").groupby("DEVICE_ID").tail(1).copy()
    latest["current_subsystem"] = latest["subsystem_chain"].apply(
        lambda s: (parse_chain(s) or [None])[-1])
    out = latest[["DEVICE_ID", "mars_device_category", "current_subsystem"]].dropna(subset=["current_subsystem"])
    out["family"] = "markov"
    return out.reset_index(drop=True)


def hmm_device_state(df, feats):
    latest = df.sort_values("transit_day").groupby("DEVICE_ID").tail(1).copy()
    cols = ["DEVICE_ID", "mars_device_category"] + [f for f in feats if f in latest.columns]
    out = latest[cols].copy()
    out["family"] = "hmm"
    return out.reset_index(drop=True)


def recurrence_device_state(df, asof):
    asof_ts = pd.Timestamp(asof)
    hist = df[df["transit_day"] <= asof_ts]
    dev_stats = hist.groupby("DEVICE_ID").agg(
        cascade_days_total=("transit_day", "count"),
        first_cascade=("transit_day", "min"), last_cascade=("transit_day", "max"))
    dev_stats["active_days"] = (asof_ts - dev_stats["first_cascade"]).dt.days.clip(lower=1) + 1
    dev_stats["cascade_rate"] = dev_stats["cascade_days_total"] / dev_stats["active_days"]
    cat = hist.drop_duplicates("DEVICE_ID").set_index("DEVICE_ID")["mars_device_category"]
    dev_stats["mars_device_category"] = dev_stats.index.map(cat)
    out = dev_stats.reset_index()[["DEVICE_ID", "mars_device_category", "cascade_days_total", "cascade_rate"]]
    out["family"] = "recurrence"
    return out


# ----------------------------------------------------------------------------
# SERIAL-GRAIN JOIN -- via hw_config_current (authoritative catalog), NOT the
# sparse per-event serial column. Repeats the device's score per known serial.
# ----------------------------------------------------------------------------
def to_serial_grain(device_state_df, hwconfig_df):
    if hwconfig_df is None or not len(hwconfig_df) or "COMPONENT_SERIAL_NBR" not in hwconfig_df.columns:
        return None
    hw = hwconfig_df[hwconfig_df["COMPONENT_SERIAL_NBR"].notna()][
        ["DEVICE_ID", "COMPONENT_SERIAL_NBR", "COMPONENT_DESCRIPTION"]].drop_duplicates()
    return hw.merge(device_state_df, on="DEVICE_ID", how="inner")


# ----------------------------------------------------------------------------
# POPULATION-LEVEL FAMILIES -- no per-record prediction, computed directly.
# ----------------------------------------------------------------------------
def phi_corr(x, y):
    n = len(x)
    n11 = (x & y).sum(); n10 = (x & ~y).sum(); n01 = (~x & y).sum(); n00 = (~x & ~y).sum()
    n1_, n0_, n_1, n_0 = n11 + n10, n01 + n00, n11 + n01, n10 + n00
    # SECOND COPY OF THE int64 OVERFLOW.                        23-Sep-2026
    # Identical expression to the serial-grain notebook's phi_corr, fixed
    # there the same day. At PS2 row counts the four-way int64 product wraps:
    # negative wraps fail `denom > 0` and return 0.0, positive ones leave a
    # tiny denominator and phi escapes [-1, 1]. This path feeds
    # ps2_subsystem_hub_edges.
    denom = np.sqrt(float(n1_) * float(n0_) * float(n_1) * float(n_0))
    if not np.isfinite(denom) or denom <= 0:
        return 0.0
    phi = (float(n) * float(n11) - float(n1_) * float(n_1)) / denom
    assert -1.0000001 <= phi <= 1.0000001, (
        f"phi out of range: {phi} (n={int(n)}, "
        f"marginals={int(n1_)},{int(n0_)},{int(n_1)},{int(n_0)})")
    return phi


def compute_phi_matrix(df):
    """Edge list: source_sub, target_sub, phi -- feeds ps2_subsystem_hub_edges directly."""
    subs = sorted(set(s for c in df["subsystem_chain"].dropna().apply(parse_chain) for s in c))
    if len(subs) < 2:
        return pd.DataFrame(columns=["source_sub", "target_sub", "phi"])
    flags = pd.DataFrame({s: df["subsystem_chain"].apply(lambda c, s=s: s in parse_chain(c)) for s in subs})
    rows = []
    for a, b in itertools.combinations(subs, 2):
        rows.append({"source_sub": a, "target_sub": b, "phi": round(float(phi_corr(flags[a], flags[b])), 4)})
    return pd.DataFrame(rows)


def compute_conditional_prob(df):
    """P(B follows A within the same chain) -- confidence = count(A before B) / count(A present)."""
    chains = df["subsystem_chain"].dropna().apply(parse_chain).tolist()
    a_count, ab_count = {}, {}
    for c in chains:
        present = set(c)
        for a in present:
            a_count[a] = a_count.get(a, 0) + 1
        for i, a in enumerate(c):
            for b in set(c[i + 1:]):
                if a != b:
                    ab_count[(a, b)] = ab_count.get((a, b), 0) + 1
    rows = [{"antecedent": a, "consequent": b, "conditional_prob": round(cnt / a_count[a], 4), "support_count": cnt}
             for (a, b), cnt in ab_count.items() if a_count.get(a, 0) > 0]
    return pd.DataFrame(rows).sort_values("conditional_prob", ascending=False) if rows else pd.DataFrame(
        columns=["antecedent", "consequent", "conditional_prob", "support_count"])


def compute_association_rules(df, min_support=0.02):
    """mlxtend apriori/association_rules on the chain-level binary subsystem flags."""
    try:
        from mlxtend.frequent_patterns import apriori, association_rules
        from mlxtend.preprocessing import TransactionEncoder
    except ImportError:
        print("  [assoc] mlxtend not installed -- skipped (pip install mlxtend to enable)")
        return pd.DataFrame()
    txns = df["subsystem_chain"].dropna().apply(parse_chain).tolist()
    txns = [t for t in txns if len(t) >= 2]
    if not txns:
        return pd.DataFrame()
    te = TransactionEncoder()
    te_ary = te.fit(txns).transform(txns)
    flags = pd.DataFrame(te_ary, columns=te.columns_)
    freq = apriori(flags, min_support=min_support, use_colnames=True)
    if not len(freq):
        return pd.DataFrame()
    rules = association_rules(freq, metric="lift", min_threshold=1.0)
    rules = rules[rules["antecedents"].apply(len) == 1]
    rules = rules[rules["consequents"].apply(len) == 1]
    out = pd.DataFrame({
        "antecedent_subsystem": rules["antecedents"].apply(lambda s: next(iter(s))),
        "consequent_subsystem": rules["consequents"].apply(lambda s: next(iter(s))),
        "support": rules["support"].round(4), "confidence": rules["confidence"].round(4),
        "lift": rules["lift"].round(4), "conviction": rules["conviction"].round(2),
    })
    return out.sort_values("lift", ascending=False).reset_index(drop=True)


def compute_network_centrality(df):
    """Betweenness centrality over the directed subsystem-transition graph -- feeds ps2_subsystem_hub_summary."""
    try:
        import networkx as nx
    except ImportError:
        print("  [centrality] networkx not installed -- skipped (pip install networkx to enable)")
        return pd.DataFrame()
    g = nx.DiGraph()
    freq = {}
    for c in df["subsystem_chain"].dropna().apply(parse_chain):
        for a, b in zip(c[:-1], c[1:]):
            g.add_edge(a, b)
            freq[a] = freq.get(a, 0) + 1
    if not g.number_of_nodes():
        return pd.DataFrame(columns=["node_id", "freq", "is_hub"])
    bc = nx.betweenness_centrality(g)
    hub_thresh = np.percentile(list(bc.values()), 75) if bc else 0
    rows = [{"node_id": n, "freq": freq.get(n, 0), "betweenness": round(bc.get(n, 0.0), 4),
             "is_hub": bool(bc.get(n, 0.0) >= hub_thresh)} for n in g.nodes()]
    return pd.DataFrame(rows).sort_values("betweenness", ascending=False).reset_index(drop=True)


def compute_facility_contagion(df):
    """Within-facility co-cascade rate per day -- feeds ps2_facility_contagion_summary."""
    if "FACILITY_ID" not in df.columns:
        print("  [contagion] FACILITY_ID not present -- skipped")
        return None
    fac_counts = df.groupby(["FACILITY_ID", "transit_day"]).size().reset_index(name="n_devices_cascading")
    multi = fac_counts[fac_counts["n_devices_cascading"] > 1]
    total_facility_cascade_days = int(len(fac_counts))
    multi_pct = round(100.0 * len(multi) / max(total_facility_cascade_days, 1), 2)
    fn = df.drop_duplicates("FACILITY_ID").set_index("FACILITY_ID")["FACILITY_NAME"] if "FACILITY_NAME" in df.columns else None
    hotspot = (fac_counts.groupby("FACILITY_ID")["n_devices_cascading"].agg(["min", "max"])
               .sort_values("max", ascending=False))
    hotspot_id = hotspot.index[0] if len(hotspot) else None
    summary = {
        "total_facility_cascade_days": total_facility_cascade_days,
        "multi_device_contagion_pct": multi_pct,
        "hotspot_facility_id": int(hotspot_id) if hotspot_id is not None else None,
        "hotspot_facility_name": (fn.get(hotspot_id) if fn is not None and hotspot_id is not None else None),
        "hotspot_min_devices": int(hotspot.loc[hotspot_id, "min"]) if hotspot_id is not None else None,
        "hotspot_max_devices": int(hotspot.loc[hotspot_id, "max"]) if hotspot_id is not None else None,
    }
    return pd.DataFrame([summary])


def make_synthetic(n=200, seed=7):
    rng = np.random.default_rng(seed)
    subs = ["BHU", "CHU", "PRINTER", "CSC_READER", "SCRST"]
    cats = ["TVM", "GATE", "VALIDATOR"]
    rows = []
    days = pd.date_range("2025-01-01", periods=180, freq="D")
    for i in range(n):
        cat = cats[i % 3]
        dev = f"{cat[:3]}{i:05d}"
        n_days = rng.integers(1, 60)
        for d in rng.choice(days, size=n_days, replace=False):
            k = rng.integers(2, 5)
            chain = list(rng.choice(subs, size=k, replace=True))
            rows.append({
                "DEVICE_ID": dev, "mars_device_category": cat, "transit_day": pd.Timestamp(d),
                "subsystem_chain": "->".join(chain), "chain_length": k,
                "critical_in_chain": int(rng.integers(0, 2)), "oos_in_chain": int(rng.integers(0, 2)),
                "chain_span_min": float(rng.integers(1, 90)), "distinct_subsystems": len(set(chain)),
                "FACILITY_ID": int(rng.integers(1, 15)), "FACILITY_NAME": f"Facility {int(rng.integers(1, 15))}",
            })
    gold = pd.DataFrame(rows)
    hw = pd.DataFrame([
        {"DEVICE_ID": d, "COMPONENT_SERIAL_NBR": f"SN{d}-{j}", "COMPONENT_DESCRIPTION": c}
        for d in gold["DEVICE_ID"].unique() for j, c in enumerate(["cbxid", "coinacceptor"])
    ])
    return gold, hw


HMM_FEATS = ["chain_length", "critical_in_chain", "oos_in_chain", "chain_span_min", "distinct_subsystems"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--gold-s3", default=None)
    ap.add_argument("--hwconfig-s3", default=None)
    ap.add_argument("--asof", required=True)
    ap.add_argument("--out-dir", default="ps2_state_out")
    ap.add_argument("--bucket", default=None)
    ap.add_argument("--state-prefix", default="chicago/ps2/state")
    ap.add_argument("--population-prefix", default="chicago/ps2/population")
    ap.add_argument("--region", default="us-east-1")
    ap.add_argument("--synthetic", action="store_true")
    args = ap.parse_args()

    os.makedirs(args.out_dir, exist_ok=True)
    ro = {"client_kwargs": {"region_name": args.region}}
    if args.synthetic:
        df, hw = make_synthetic()
        print(f"[state] synthetic frame: {df.shape}, hwconfig: {hw.shape}")
    else:
        if not args.gold_s3:
            raise SystemExit("--gold-s3 is required unless --synthetic")
        df = pd.read_parquet(args.gold_s3, storage_options=ro)
        df["transit_day"] = pd.to_datetime(df["transit_day"])
        hw = pd.read_parquet(args.hwconfig_s3, storage_options=ro) if args.hwconfig_s3 else None
        print(f"[state] loaded {args.gold_s3}: {df.shape}"
              + (f" | hwconfig {args.hwconfig_s3}: {hw.shape}" if hw is not None else " | no hwconfig source given"))

    written = {"device": {}, "serial": {}, "population": {}}

    # ---- scoreable families: device + serial state ----
    dev_states = {
        "markov": markov_device_state(df),
        "hmm": hmm_device_state(df, HMM_FEATS),
        "recurrence": recurrence_device_state(df, args.asof),
    }
    for fam, state_df in dev_states.items():
        p = os.path.join(args.out_dir, f"{fam}_device_state.parquet")
        state_df.to_parquet(p, index=False)
        written["device"][fam] = p
        print(f"  [state] {fam}/device: {len(state_df)} devices -> {os.path.basename(p)}")
        ser_df = to_serial_grain(state_df, hw)
        if ser_df is not None and len(ser_df):
            sp = os.path.join(args.out_dir, f"{fam}_serial_state.parquet")
            ser_df.to_parquet(sp, index=False)
            written["serial"][fam] = sp
            print(f"  [state] {fam}/serial: {len(ser_df)} rows -> {os.path.basename(sp)}")
        else:
            print(f"  [state] {fam}/serial: no hwconfig join available -- skipped")

    # ---- population-level families: no model, computed directly ----
    pop = {
        "phi_matrix": compute_phi_matrix(df),
        "conditional_prob": compute_conditional_prob(df),
        "association_rules": compute_association_rules(df),
        "network_centrality": compute_network_centrality(df),
        "facility_contagion": compute_facility_contagion(df),
    }
    for name, pdf in pop.items():
        if pdf is None or not len(pdf):
            print(f"  [population] {name}: empty -- skipped"); continue
        p = os.path.join(args.out_dir, f"{name}.parquet")
        pdf.to_parquet(p, index=False)
        written["population"][name] = p
        print(f"  [population] {name}: {len(pdf)} rows -> {os.path.basename(p)}")

    if args.bucket:
        import boto3
        s3 = boto3.client("s3", region_name=args.region)
        for grain in ("device", "serial"):
            for fam, path in written[grain].items():
                key = f"{args.state_prefix}/{fam}/{grain}/asof={args.asof}/{os.path.basename(path)}"
                s3.upload_file(path, args.bucket, key)
                print(f"  [s3] {os.path.basename(path)} -> s3://{args.bucket}/{key}")
        for name, path in written["population"].items():
            key = f"{args.population_prefix}/{name}/asof={args.asof}/{os.path.basename(path)}"
            s3.upload_file(path, args.bucket, key)
            print(f"  [s3] {os.path.basename(path)} -> s3://{args.bucket}/{key}")

    n_files = sum(len(g) for g in written.values())
    print(f"STATE_REFRESH done: {n_files} outputs (asof {args.asof})")


if __name__ == "__main__":
    main()
