#!/usr/bin/env python3
# =============================================================================
# refresh_device_state.py -- the DAILY-moving half of the pipeline.
#
# The model params change only on retrain; the device STATE (current age,
# days_since_hw_oos, roll_fail_*, telemetry features) changes every day. This job
# recomputes each device's CURRENT feature vector + the serial roster as-of the
# scoring date and writes <type>_device_state.parquet / <type>_serial_roster.parquet
# to S3, which Batch Transform then scores. It REUSES the v5.1 engine's exact
# feature pipeline (build_frame -> enrich -> derive -> static -> interactions), so
# serve-time features match training by construction.
#
# Run daily for freshest RUL; or drop it and run train+score weekly if within-week
# staleness is acceptable (score cadence must match state-refresh cadence).
#
# Usage:
#   python refresh_device_state.py --engine ../../notebooks/.../ps5_reliability_engine_v51.py \
#     --params-dir ./params --asof 2026-04-11 \
#     --bucket cubic-mars-pm-s3-datalake-dev-gold-170202974600 --state-prefix chicago/ps5/state
#   # offline dry-run: add --synthetic (no S3, writes locally)
# =============================================================================
import argparse, glob, importlib.util, json, os, sys

import numpy as np  # noqa: F401
import pandas as pd


def load_engine(path):
    spec = importlib.util.spec_from_file_location("ps5_engine_v51", path)
    mod = importlib.util.module_from_spec(spec); sys.modules["ps5_engine_v51"] = mod
    spec.loader.exec_module(mod)
    return mod


def build_feature_frame(eng, cat):
    """Replicate run_type's feature assembly (no CV / no fitting) -> the current enriched frame."""
    iv = eng.build_frame(cat)
    if eng.CONFIG["WINDOW_MODE"] == "telemetry_era":
        w = iv[iv["interval_start_date"] >= pd.Timestamp(eng.CONFIG["TELEMETRY_START"])]
        if int(w["event_observed"].sum()) >= eng.CONFIG["ERA_MIN_EVENTS"]:
            iv = w.copy()
    if eng.CONFIG.get("ENRICH"):
        iv, _enr, _cov = eng.enrich(iv, cat)
    if eng.CONFIG.get("DERIVE_ENGINEERED"):
        iv, _ = eng.derive_engineered(iv)
    if eng.CONFIG.get("STATIC_CMDB"):
        iv, _ = eng.build_static_covariates(iv, cat)
    if eng.CONFIG.get("INTERACTIONS"):
        iv, _ = eng.build_interactions(iv)
    return iv


def emit_device_state(eng, cat, params, out_dir, asof):
    iv = build_feature_frame(eng, cat)
    feats = (params.get("cox") or {}).get("features", [])
    ong = iv[iv["is_ongoing"].astype(bool)].sort_values("interval_start_date").groupby("DEVICE_ID").tail(1).copy()
    if not len(ong):
        ong = iv.sort_values("interval_start_date").groupby("DEVICE_ID").tail(1).copy()
    keep = ["DEVICE_ID"] + [f for f in feats if f in ong.columns]      # exactly the model features present
    st = ong[keep].copy()
    st["current_healthy_age_days"] = ong["interval_days"].astype(float).clip(lower=0).values
    st["mars_device_category"] = cat                                  # <- lets the container route to this type's params
    st["facility_id"] = ong["FACILITY_ID"].values if "FACILITY_ID" in ong.columns else None
    rec = eng._asof_recency(eng.load_hw_oos_failures(cat), pd.Timestamp(asof))   # current recency as-of the scoring date
    dids = ong["DEVICE_ID"].values
    st["days_since_hw_oos"] = [rec.get(d, (float(a), {}))[0]
                              for d, a in zip(dids, st["current_healthy_age_days"].values)]
    st["roll_fail_30d"] = [rec.get(d, (0.0, {}))[1].get(30, 0) for d in dids]
    if "failure_seq" in ong.columns:
        st["failure_seq"] = ong["failure_seq"].astype(int).values
    miss = [f for f in feats if f not in ong.columns]
    path = os.path.join(out_dir, f"{eng.CONFIG['SUBFOLDER'][cat]}_device_state.parquet")
    st.to_parquet(path, index=False)
    print(f"  [state] {cat}: {len(st)} devices, {len(keep)-1}/{len(feats)} features present"
          + (f" (missing {len(miss)} -> container fills with param mean)" if miss else "") + f" -> {os.path.basename(path)}")
    return path


def emit_serial_roster(eng, cat, out_dir, asof):
    cp = None
    if eng.CONFIG.get("SERIAL_SOURCE", "auto") in ("auto", "component_table"):
        cp = eng.load_table(eng.CONFIG["COMPONENT_TABLE"], cat=cat, catcol="mars_device_category", required=False)
    if cp is None or not len(cp):
        cp = eng.load_table(eng.CONFIG["HWCONFIG_TABLE"], cat=cat, required=False)
    if cp is None or not len(cp) or "COMPONENT_SERIAL_NBR" not in cp.columns:
        print(f"  [roster] {cat}: no component roster; skipping serial"); return None
    cp = eng.coerce(cp).copy(); cp["mars_device_category"] = cat
    cp["component_age_days"] = pd.to_numeric(cp.get("component_age_days", 0.0), errors="coerce").fillna(0.0).clip(lower=0)
    cp["COMPONENT_TYPE_NAME"] = cp.get("COMPONENT_TYPE_NAME", pd.Series(["OTHER"] * len(cp))).astype(str).fillna("OTHER")
    devf = eng.load_hw_oos_failures(cat).groupby("DEVICE_ID").size().rename("device_oos_failures_total")
    cp = cp.merge(devf, on="DEVICE_ID", how="left")
    cp["device_oos_failures_total"] = cp["device_oos_failures_total"].fillna(0).astype(int)
    cols = ["DEVICE_ID", "COMPONENT_SERIAL_NBR", "COMPONENT_TYPE_NAME", "mars_device_category",
            "component_age_days", "device_oos_failures_total"]
    ros = cp[[c for c in cols if c in cp.columns]]
    path = os.path.join(out_dir, f"{eng.CONFIG['SUBFOLDER'][cat]}_serial_roster.parquet")
    ros.to_parquet(path, index=False)
    print(f"  [roster] {cat}: {len(ros)} components -> {os.path.basename(path)}")
    return path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--engine", required=True, help="path to ps5_reliability_engine_v51.py")
    ap.add_argument("--params-dir", required=True, help="dir with <type>_device_survival_params.json")
    ap.add_argument("--asof", required=True)
    ap.add_argument("--out-dir", default="ps5_state_out")
    ap.add_argument("--bucket", default=None)
    ap.add_argument("--state-prefix", default="chicago/ps5/state")
    ap.add_argument("--synthetic", action="store_true", help="offline: use the engine's synthetic tables")
    args = ap.parse_args()

    eng = load_engine(args.engine)
    eng.CONFIG["RUN_DATE"] = args.asof                     # ages + recency computed as-of the scoring date
    if args.synthetic:
        setattr(eng, "_SYNTH", eng.make_synthetic())       # engine's load_table looks up globals()['_SYNTH']
    os.makedirs(args.out_dir, exist_ok=True)
    written = []
    for cat in eng.CONFIG["DEVICE_SCOPE"]:
        pf = os.path.join(args.params_dir, f"{eng.CONFIG['SUBFOLDER'][cat]}_device_survival_params.json")
        if not os.path.exists(pf):
            print(f"  [skip] {cat}: no params at {pf}"); continue
        params = json.load(open(pf))
        try:
            written.append(emit_device_state(eng, cat, params, args.out_dir, args.asof))
        except Exception as e:
            print(f"  [state] {cat} FAILED: {type(e).__name__}: {str(e)[:120]}")
        try:
            r = emit_serial_roster(eng, cat, args.out_dir, args.asof)
            if r:
                written.append(r)
        except Exception as e:
            print(f"  [roster] {cat} FAILED: {type(e).__name__}: {str(e)[:120]}")

    if args.bucket:
        import boto3
        s3 = boto3.client("s3")
        for p in written:
            sub = "device" if "device_state" in p else "serial"
            key = f"{args.state_prefix}/{sub}/{os.path.basename(p)}"
            s3.upload_file(p, args.bucket, key)
            print(f"  [s3] {os.path.basename(p)} -> s3://{args.bucket}/{key}")
    print(f"STATE_REFRESH done: {len(written)} files (asof {args.asof})")


if __name__ == "__main__":
    main()
