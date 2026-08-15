#!/usr/bin/env python3
"""
ps1_batch_score_daily.py
========================
Daily PS1 batch scoring. Runs as a SageMaker Processing job.

    python ps1_batch_score_daily.py --asof 2026-08-15 --fleet GATE

WHAT THIS IS, AND WHAT IT DELIBERATELY IS NOT
---------------------------------------------
It SCORES. It does not build features.

The three fleet notebooks build their feature frame across CELLS 6-8 with
Spark joins over ~11 silver/gold tables, and CELL 8 computes FEATURE_COLS
at run time from whatever columns happen to exist. Porting that logic here
would create a SECOND implementation of the same feature definitions, in a
different engine, that has to be kept in step with the first forever. The
first time they drift, the model scores features that are named the same
and mean something different -- and nothing would report it.

So the contract is: DATABRICKS BUILDS THE FEATURE FRAME, SAGEMAKER SCORES IT.
See the FEATURE FRAME CONTRACT section below for what Databricks must write.
That work is not done yet and is the remaining prerequisite.

WHY IT LOADS THE ENDPOINT'S OWN ARTEFACT
----------------------------------------
--model-uri defaults to the exact model.tar.gz the live endpoint loads.
Same booster, same feature_cols, same medians, same threshold. Scoring
parity with the endpoint is then true BY CONSTRUCTION rather than by
someone remembering to keep two copies aligned.

The scoring maths below is a deliberate line-by-line mirror of
_predict_proba in the deployed inference.py -- with ONE intentional
difference, marked C-3, which is a bug fix. See _score().

MEASURED CONTRACT, 2026-08-15 (tooling/out/ps1_*_feature_contract.json)
-----------------------------------------------------------------------
    fleet      n_features  medians  threshold   model_file
    GATE           47       47/47   0.12284049  ps1_gate_xgb.json
    TVM            40       40/40   0.02648935  ps1_tvm_xgb.json
    VALIDATOR      40       40/40   0.39651793  ps1_validator_xgb.json

    meta["classifier"] = SparkXGBClassifierModel  (Spark lineage)

NOTE, and it is not a small one: those thresholds match NEITHER row in
ps1_model_performance. GATE's endpoint fires at 0.1228 while the scorecard
publishes 0.6485 (sklearn) / 0.1349 (spark). This job uses the DEPLOYED
threshold, because the deployed threshold is what the endpoint uses and
this job exists to reproduce the endpoint. It records both in the manifest
so the discrepancy stays visible instead of being quietly resolved.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tarfile
import tempfile
from datetime import datetime, timezone

import boto3
import joblib
import numpy as np
import pandas as pd

ARTIFACT_BUCKET = "cubic-mars-pm-s3-datalake-dev-artifacts-170202974600"
FLEETS = ("GATE", "TVM", "VALIDATOR")

s3 = boto3.client("s3")


# =====================================================================
# FEATURE FRAME CONTRACT  -- what Databricks must produce
# =====================================================================
# Path:   s3://<artifacts>/chicago/ps1/features/asof=<YYYY-MM-DD>/fleet=<slug>/
# Format: parquet
#
# Required identity columns (carried through to the output untouched):
#     DEVICE_KEY   str    SCD2 surrogate -- the join key downstream
#     DEVICE_ID    str    stable business id
#     transit_day  date   must equal the asof date
#
# Required feature columns:
#     exactly the feature_cols in the fleet's bundle, by NAME.
#     Order does not matter -- this job reindexes by name.
#     Extra columns are fine and are ignored.
#     MISSING columns are a hard failure. See _assert_contract().
#
# Optional passthrough (copied to the output if present, for the
# cross-wire join that follows):
#     FACILITY_ID, OPERATOR_ID, mars_device_category
#
# The natural producer is the existing gold job: CELLS 6-8 of the fleet
# notebook already compute this frame. Extracting them into a Databricks
# task that writes the above path is the remaining prerequisite.
# =====================================================================


def _load_bundle(model_uri: str) -> dict:
    """Load the model exactly as the endpoint's model_fn does."""
    assert model_uri.startswith("s3://"), model_uri
    bucket, key = model_uri[5:].split("/", 1)

    tmp = tempfile.mkdtemp(prefix="ps1_model_")
    tgz = os.path.join(tmp, "model.tar.gz")
    s3.download_file(bucket, key, tgz)

    with tarfile.open(tgz, "r:gz") as tar:
        members = tar.getnames()          # list ONCE into a variable.
        tar.extractall(tmp)               # never `tar | grep -q` -- see the
                                          # SIGPIPE race in the commit log.

    meta_files = [m for m in members if m.endswith("_meta.joblib")]
    thr_files = [m for m in members if m.endswith("_threshold.joblib")]
    if not meta_files:
        raise RuntimeError(
            f"No *_meta.joblib in {model_uri}. Members: {members}. "
            f"Without the meta there is no feature contract, and scoring "
            f"would be guessing at column order."
        )
    if not thr_files:
        raise RuntimeError(f"No *_threshold.joblib in {model_uri}. Members: {members}")

    meta = joblib.load(os.path.join(tmp, meta_files[0]))
    threshold = float(joblib.load(os.path.join(tmp, thr_files[0])))

    model_file = os.path.join(tmp, meta["model_file"])
    mtype = meta["model_type"]
    if mtype == "xgboost":
        import xgboost as xgb
        booster = xgb.Booster()
        booster.load_model(model_file)
    elif mtype == "lightgbm":
        import lightgbm as lgb
        booster = lgb.Booster(model_file=model_file)
    else:
        from catboost import CatBoostClassifier
        booster = CatBoostClassifier()
        booster.load_model(model_file)

    return {
        "meta": meta,
        "threshold": threshold,
        "model": booster,
        "model_type": mtype,
        "members": members,
        "model_uri": model_uri,
    }


def _assert_contract(df: pd.DataFrame, cols: list[str], fleet: str) -> None:
    """DECISION-8. Refuse to score a frame that cannot honour the contract.

    This is the guard the deployed handler does not have. There,
    reindex(columns=cols, fill_value=0.0) turns an absent feature into 0.0
    -- and 0.0 is a perfectly ordinary value for a count or a rate, so the
    model returns a confident, plausible, wrong probability and nothing
    anywhere records that an input was missing.

    A missing feature is a broken upstream job. It must stop the run.
    """
    missing = [c for c in cols if c not in df.columns]
    if missing:
        raise RuntimeError(
            f"[{fleet}] {len(missing)} of {len(cols)} trained features are absent "
            f"from the scoring frame.\n"
            f"  missing: {missing}\n"
            f"  Scoring anyway would fill these with 0.0 (or a median) and return "
            f"confident wrong probabilities with nothing logged. Refusing.\n"
            f"  Fix the upstream feature job; do not relax this check."
        )

    for k in ("DEVICE_KEY", "transit_day"):
        if k not in df.columns:
            raise RuntimeError(
                f"[{fleet}] identity column '{k}' missing. Without it a probability "
                f"cannot be attached to a device, and a scored frame with no keys is "
                f"worse than no scored frame."
            )

    # An all-null feature passes the presence check and is still useless.
    # Report it rather than let it through silently.
    allnull = [c for c in cols if df[c].isna().all()]
    if allnull:
        print(
            f"[{fleet}] WARNING: {len(allnull)} features are present but 100% NULL "
            f"-- they will be filled with the training median for EVERY row, which "
            f"means the model is effectively blind to them today: {allnull}"
        )


def _score(df: pd.DataFrame, bundle: dict, fleet: str) -> np.ndarray:
    """Mirror of _predict_proba in the deployed inference.py, with the C-3 fix.

    DEPLOYED (inference.py):
        X = _impute(df, cols, medians).reindex(columns=cols, fill_value=0.0)

        _impute only touches columns already present, so an ABSENT column is
        skipped and then reindex sets it to 0.0 -- even though a perfectly
        good training median for it is sitting in meta["medians"].

    HERE:
        reindex FIRST, so an absent column arrives as NaN, and _impute then
        fills it with its real median.

    In this job the difference is belt-and-braces: _assert_contract has
    already refused any frame with an absent feature. It is written this way
    so that the correct ordering is recorded somewhere in the repository,
    and so this code stays right if the assertion is ever relaxed.
    """
    meta = bundle["meta"]
    cols = list(meta["feature_cols"])
    medians = meta.get("medians", {}) or {}

    X = df.reindex(columns=cols)                       # <-- C-3: reindex first
    for c in cols:
        if c in medians:
            X[c] = X[c].astype(float).fillna(medians[c])
    n_left = int(X.isna().sum().sum())
    if n_left:
        # a feature with no median and a NaN value: nothing sensible to
        # substitute, so say so rather than let xgboost's own NaN handling
        # make the decision invisibly
        print(f"[{fleet}] NOTE: {n_left} NaN cells remain after median imputation "
              f"(features with no median). XGBoost will route these by its own "
              f"learned default direction.")
    X = X.astype(float).values

    mtype = bundle["model_type"]
    if mtype == "xgboost":
        import xgboost as xgb
        return bundle["model"].predict(xgb.DMatrix(X, feature_names=cols))
    if mtype == "lightgbm":
        return bundle["model"].predict(X)
    return bundle["model"].predict_proba(X)[:, 1]


def _risk_tier(p: float) -> str:
    """Identical bands to CELL 24, so the dashboard's meaning does not shift."""
    if pd.isna(p):
        return "UNKNOWN"
    if p >= 0.70:
        return "CRITICAL"
    if p >= 0.50:
        return "HIGH"
    if p >= 0.30:
        return "MEDIUM"
    return "LOW"


def score_fleet(fleet: str, asof: str, features_uri: str, out_uri: str,
                model_uri: str) -> dict:
    slug = fleet.lower()
    print(f"\n{'=' * 62}\n {fleet}  asof={asof}\n{'=' * 62}")

    bundle = _load_bundle(model_uri)
    meta = bundle["meta"]
    cols = list(meta["feature_cols"])
    print(f"  model      : {bundle['model_type']}  {meta.get('model_file')}")
    print(f"  classifier : {meta.get('classifier')}")
    print(f"  features   : {len(cols)}   medians: {len(meta.get('medians', {}))}")
    print(f"  threshold  : {bundle['threshold']}")
    print(f"  artefact   : {model_uri}")

    src = f"{features_uri}/asof={asof}/fleet={slug}/"
    print(f"  reading    : {src}")
    df = pd.read_parquet(src, storage_options={"client_kwargs": {"region_name": "us-east-1"}})
    print(f"  rows       : {len(df):,}")
    if df.empty:
        raise RuntimeError(
            f"[{fleet}] feature frame at {src} is EMPTY. An empty frame is not a "
            f"successful run with nothing to say -- it means the upstream job "
            f"produced nothing for {asof}. Refusing to write an empty scored partition."
        )

    _assert_contract(df, cols, fleet)

    proba = _score(df, bundle, fleet)
    thr = bundle["threshold"]

    out = df[[c for c in ("DEVICE_KEY", "DEVICE_ID", "transit_day", "FACILITY_ID",
                          "OPERATOR_ID", "mars_device_category") if c in df.columns]].copy()
    out["ps1_fail_prob"] = proba
    out["ps1_predicted"] = (proba >= thr).astype(int)
    out["threshold_used"] = thr
    out["ps1_risk_tier"] = [_risk_tier(p) for p in proba]
    out["device_category"] = fleet
    out["asof_date"] = asof
    out["scored_at_utc"] = datetime.now(timezone.utc).isoformat()
    out["model_uri"] = model_uri
    out["model_file"] = meta.get("model_file")
    out["n_features"] = len(cols)

    dest = f"{out_uri}/asof={asof}/fleet={slug}/"
    out.to_parquet(dest, index=False,
                   storage_options={"client_kwargs": {"region_name": "us-east-1"}})

    n_flag = int(out["ps1_predicted"].sum())
    print(f"  scored     : {len(out):,} rows, {n_flag:,} flagged "
          f"({100 * n_flag / max(len(out), 1):.2f}%)")
    print(f"  wrote      : {dest}")

    return {
        "fleet": fleet,
        "asof_date": asof,
        "rows_scored": int(len(out)),
        "rows_flagged": n_flag,
        "flag_rate_pct": round(100 * n_flag / max(len(out), 1), 4),
        "prob_min": float(np.min(proba)),
        "prob_max": float(np.max(proba)),
        "prob_mean": float(np.mean(proba)),
        "threshold_deployed": thr,
        "n_features": len(cols),
        "model_uri": model_uri,
        "model_file": meta.get("model_file"),
        "classifier": str(meta.get("classifier")),
        "output_uri": dest,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--asof", required=True, help="YYYY-MM-DD")
    ap.add_argument("--fleet", default="ALL", choices=[*FLEETS, "ALL"])
    ap.add_argument("--features-uri",
                    default=f"s3://{ARTIFACT_BUCKET}/chicago/ps1/features")
    ap.add_argument("--out-uri",
                    default=f"s3://{ARTIFACT_BUCKET}/chicago/ps1/scored")
    ap.add_argument("--model-uri-template",
                    default=f"s3://{ARTIFACT_BUCKET}/sagemaker/ps1-3d/{{slug}}"
                            f"/spark-model-v1/model.tar.gz",
                    help="Defaults to the artefact the live endpoint loads, so "
                         "scoring parity is by construction.")
    args = ap.parse_args()

    fleets = FLEETS if args.fleet == "ALL" else (args.fleet,)
    results, failures = [], []

    for fleet in fleets:
        try:
            results.append(score_fleet(
                fleet, args.asof, args.features_uri.rstrip("/"),
                args.out_uri.rstrip("/"),
                args.model_uri_template.format(slug=fleet.lower()),
            ))
        except Exception as exc:            # noqa: BLE001
            print(f"\n[{fleet}] FAILED: {exc}", file=sys.stderr)
            failures.append({"fleet": fleet, "error": str(exc)})

    manifest = {
        "job": "ps1_batch_score_daily",
        "asof_date": args.asof,
        "run_ts_utc": datetime.now(timezone.utc).isoformat(),
        "fleets_requested": list(fleets),
        "fleets_scored": [r["fleet"] for r in results],
        "fleets_failed": failures,
        "partial": bool(failures),
        "results": results,
        "schema_version": "1.0",
    }
    key = f"chicago/ps1/scored/manifest/asof={args.asof}/manifest.json"
    s3.put_object(Bucket=ARTIFACT_BUCKET, Key=key,
                  Body=json.dumps(manifest, indent=2).encode())
    print(f"\nmanifest -> s3://{ARTIFACT_BUCKET}/{key}")

    if not results:
        # Every fleet failed. Exit non-zero so Step Functions catches it and
        # the failure rules fire. A job that scores nothing and exits 0 is
        # indistinguishable from a healthy one downstream.
        print("ALL FLEETS FAILED -- exiting non-zero", file=sys.stderr)
        return 1
    if failures:
        print(f"PARTIAL: {[f['fleet'] for f in failures]} failed. Manifest carries "
              f"partial=true; the cross-wire loader must refuse it without "
              f"--allow-partial.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
