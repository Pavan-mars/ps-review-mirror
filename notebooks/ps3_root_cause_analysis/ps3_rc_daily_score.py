#!/usr/bin/env python3
"""
ps3_rc_daily_score.py -- daily root-cause inference for PS3.

    spine parquet (S3)  +  packaged model (S3)
        -> predictions CSV (S3, rootcause_outputs/<run_id>/)
        -> cubic-mars-ps3-rc-loader
        -> Aurora ps3_*  -> the dashboard

RUN IT ANYWHERE THAT HAS THE SPINE. SageMaker Processing, a Glue Python-shell
job, an EC2 cron, or a laptop with credentials. Deliberately NOT a Lambda:
scoring reads the whole device-day spine to rebuild each device's history, and
a 15-minute ceiling with a 512 MB /tmp is the wrong shape for that. The
Lambda in this pipeline is the LOADER, which is small and I/O-bound.

    python ps3_rc_daily_score.py --model-run ps3_oos_20260803 --score-date 2026-04-11
    python ps3_rc_daily_score.py --model-run latest --score-date latest --invoke-loader

WHAT MAKES IT SAFE TO RUN UNATTENDED
------------------------------------
  * The feature builder is imported from ps3_rc_features, the same module the
    training notebook used. There is no second copy to drift.
  * The artifact records the feature ORDER and the contract version. Both are
    checked before a single row is scored. A mismatch aborts; it does not
    "handle" it.
  * A class the model never saw cannot appear in the output, because the
    prior_share_* columns are pinned to the TRAINED class list rather than
    rebuilt from whatever the scoring window happens to contain.
  * Predictions are written under a NEW run_id. Nothing overwrites the
    training run, and ps3_model_runs keeps both, so a bad scoring day can be
    pointed at and dropped rather than reconstructed.
"""
import argparse
import datetime as dt
import io
import json
import os
import sys

import boto3
import joblib
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ps3_rc_features as F  # noqa: E402

REGION = os.environ.get("AWS_REGION", "us-east-1")
ARTIFACT_BUCKET = os.environ.get(
    "ARTIFACT_BUCKET", "cubic-mars-pm-s3-datalake-dev-artifacts-170202974600")
MODEL_PREFIX = os.environ.get("PS3_MODEL_PREFIX", "chicago/ps3/models")
OUT_PREFIX = os.environ.get("PS3_RC_PREFIX", "chicago/ps3/rootcause_outputs")
SPINE_PREFIX = os.environ.get("PS3_SPINE_PREFIX", "chicago/ps3/spine")
CITY = os.environ.get("CITY_ID", "CHI")
LOADER_FN = os.environ.get("PS3_RC_LOADER", "cubic-mars-ps3-rc-loader")

FLEETS = {"TVM": "tvm", "GATE": "gates", "VALIDATOR": "validators"}

s3 = boto3.client("s3", region_name=REGION)


def s3_list_dirs(prefix):
    p = s3.get_paginator("list_objects_v2")
    out = set()
    for page in p.paginate(Bucket=ARTIFACT_BUCKET, Prefix=prefix.rstrip("/") + "/",
                           Delimiter="/"):
        for cp in page.get("CommonPrefixes") or []:
            out.add(cp["Prefix"].rstrip("/").rsplit("/", 1)[-1])
    return sorted(out)


def s3_get(key):
    return s3.get_object(Bucket=ARTIFACT_BUCKET, Key=key)["Body"].read()


def s3_put(key, data, content_type="text/csv"):
    s3.put_object(Bucket=ARTIFACT_BUCKET, Key=key, Body=data, ContentType=content_type)
    return "s3://%s/%s" % (ARTIFACT_BUCKET, key)


def load_artifact(model_run, fleet):
    base = "%s/%s/%s" % (MODEL_PREFIX.rstrip("/"), model_run, fleet)
    meta = json.loads(s3_get(base + "/metadata.json"))
    bundle = joblib.load(io.BytesIO(s3_get(base + "/model.joblib")))

    # The contract check. Not a warning -- an abort. A model scored on
    # shifted columns produces confident, plausible, wrong answers, and
    # nothing downstream can tell that from a weak model.
    if meta.get("feature_contract_version") != F.FEATURE_CONTRACT_VERSION:
        raise SystemExit(
            "feature contract mismatch for %s: artifact was fitted under %r, this "
            "code is %r. Re-train, or check out the matching revision of "
            "ps3_rc_features.py. Do NOT score across this gap."
            % (fleet, meta.get("feature_contract_version"), F.FEATURE_CONTRACT_VERSION))
    return bundle, meta


def read_spine(fleet_folder, local_dir):
    """Prefer a local spine (SageMaker working dir); fall back to S3."""
    local = os.path.join(local_dir, "%s_ps3_spine.parquet" % fleet_folder)
    if os.path.exists(local):
        return pd.read_parquet(local), local
    key = "%s/%s_ps3_spine.parquet" % (SPINE_PREFIX.rstrip("/"), fleet_folder)
    return pd.read_parquet(io.BytesIO(s3_get(key))), "s3://%s/%s" % (ARTIFACT_BUCKET, key)


def score_fleet(fleet, model_run, score_dates, local_dir):
    bundle, meta = load_artifact(model_run, fleet)
    clf, enc = bundle["clf"], bundle["encoder"]
    classes = meta["classes"]
    feat_num, feat_cat = meta["feat_num"], meta["feat_cat"]

    spine, src = read_spine(FLEETS[fleet], local_dir)
    rows, _ = F.prepare_for_scoring(spine, score_dates, classes)
    if rows.empty:
        print("  %-10s no rows on %s (source %s)" % (fleet, sorted(score_dates)[:3], src))
        return None

    X = F.design_matrix(rows, feat_num, feat_cat, enc, fit=False)
    pred = clf.predict(X)
    proba = clf.predict_proba(X)
    conf = proba.max(axis=1)

    out = rows[["device_id", "transit_day"]].copy()
    out["pred_root_cause"] = pred
    out["pred_confidence"] = np.round(conf, 4)
    print("  %-10s scored %6d device-days across %5d devices  (source %s)"
          % (fleet, len(out), out["device_id"].nunique(), src))
    return {"fleet": fleet, "meta": meta, "rows": out}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model-run", default="latest",
                    help="model run_id under the model prefix, or 'latest'")
    ap.add_argument("--score-date", default="latest",
                    help="YYYY-MM-DD, a comma-separated list, or 'latest' for the "
                         "most recent day present in each fleet's spine")
    ap.add_argument("--spine-dir", default="PS3_spine_outputs",
                    help="local directory holding {fleet}_ps3_spine.parquet")
    ap.add_argument("--run-id", default=None,
                    help="run_id to publish under (default ps3_score_<today>)")
    ap.add_argument("--invoke-loader", action="store_true",
                    help="invoke the RDS loader once the artifacts are written")
    ap.add_argument("--dry-run", action="store_true",
                    help="score and report, write nothing to S3")
    a = ap.parse_args()

    model_run = a.model_run
    if model_run == "latest":
        runs = s3_list_dirs(MODEL_PREFIX)
        if not runs:
            raise SystemExit("no packaged models under s3://%s/%s"
                             % (ARTIFACT_BUCKET, MODEL_PREFIX))
        model_run = runs[-1]

    run_id = a.run_id or ("ps3_score_%s" % dt.datetime.now().strftime("%Y%m%d"))
    print("=" * 78)
    print("PS3 ROOT-CAUSE DAILY SCORING")
    print("=" * 78)
    print("  model run : %s" % model_run)
    print("  publish as: %s" % run_id)

    results = []
    for fleet in FLEETS:
        try:
            if a.score_date == "latest":
                spine, _ = read_spine(FLEETS[fleet], a.spine_dir)
                dates = [pd.to_datetime(spine["transit_day"]).max().normalize()]
            else:
                dates = [pd.to_datetime(x.strip()) for x in a.score_date.split(",")]
            r = score_fleet(fleet, model_run, dates, a.spine_dir)
            if r:
                r["score_dates"] = [str(d.date()) for d in dates]
                results.append(r)
        except SystemExit:
            raise
        except Exception as e:
            # One fleet failing must not cost the other two their scoring
            # day. The failure is printed and the run continues; the loader's
            # completeness check is what decides whether the partial result is
            # publishable.
            print("  %-10s FAILED: %s" % (fleet, str(e)[:200]))

    if not results:
        raise SystemExit("no fleet produced predictions; nothing to publish")

    as_of = max(max(r["score_dates"]) for r in results)
    now = dt.datetime.now().isoformat(timespec="seconds")

    # ---- ps3_device_predictions -------------------------------------------
    # Device grain, one row per device per run -- the same shape the training
    # export wrote, so the dashboard route needs no change. dominant_pred is
    # the modal prediction across the device's scored days.
    dev = []
    for r in results:
        agg = (r["rows"].groupby("device_id")
               .agg(n_incidents=("device_id", "size"),
                    dominant_pred_component=("pred_root_cause",
                                             lambda s: s.value_counts().index[0]),
                    last_incident_dtm=("transit_day", "max"))
               .reset_index())
        agg["city_id"] = CITY
        agg["run_id"] = run_id
        agg["mars_device_category"] = r["fleet"]
        agg["computed_date"] = as_of
        dev.append(agg)
    dev = pd.concat(dev, ignore_index=True)[
        ["city_id", "run_id", "device_id", "mars_device_category", "n_incidents",
         "dominant_pred_component", "last_incident_dtm", "computed_date"]]

    # ---- ps3_head_summary --------------------------------------------------
    # Carries the TRAINING metrics verbatim, because a scoring run measures
    # nothing -- there are no labels for the day it just scored. Publishing
    # the training numbers under run_kind='batch_score' keeps the dashboard's
    # quality panel honest about which run those numbers came from instead of
    # implying they were re-measured today.
    head = pd.DataFrame([{
        "city_id": CITY, "run_id": run_id, "device_category": r["fleet"],
        "head": "root_cause", "modeled": True,
        "target_col": "component_subsystem",
        "champion": r["meta"]["champion"],
        "n_classes": len(r["meta"]["classes"]),
        "class_labels": ",".join(r["meta"]["classes"]),
        "test_f1_macro": r["meta"]["metrics"]["f1_macro"],
        "test_f1_weighted": r["meta"]["metrics"]["f1_weighted"],
        "test_accuracy": r["meta"]["metrics"]["accuracy"],
        "test_balanced_accuracy": r["meta"]["metrics"]["balanced_accuracy"],
        "test_cohen_kappa": r["meta"]["metrics"]["cohen_kappa"],
        "test_mcc": r["meta"]["metrics"]["mcc"],
        "macro_f1_floor": r["meta"]["macro_f1_floor"],
        "gate_pass": bool(r["meta"]["metrics"]["f1_macro"] >= r["meta"]["macro_f1_floor"]),
        "n_train": r["meta"]["n_train"], "n_test": r["meta"]["n_test"],
        "n_features": len(r["meta"]["feat_num"]) + len(r["meta"]["feat_cat"]),
        "rootcause_source": "device_event_enriched",
        "as_of_date": as_of,
    } for r in results])

    # ---- ps3_model_runs ----------------------------------------------------
    runs_df = pd.DataFrame([{
        "city_id": CITY, "run_id": run_id, "run_ts": now, "run_kind": "batch_score",
        "source_notebook": "ps3_rc_daily_score.py",
        "n_incidents": int(sum(len(r["rows"]) for r in results)),
        "device_scope": ",".join(r["fleet"] for r in results),
        "mlflow_version": "v3", "severity_collapse_verified": False,
        "notes": ("Daily batch scoring from packaged model run %s. Metrics on "
                  "ps3_head_summary are the TRAINING metrics of that run -- a "
                  "scoring day has no labels and measures nothing. Feature "
                  "contract %s." % (model_run, F.FEATURE_CONTRACT_VERSION)),
        "as_of_date": as_of,
    }])

    print("\n  ps3_model_runs         %d row" % len(runs_df))
    print("  ps3_head_summary       %d rows" % len(head))
    print("  ps3_device_predictions %d rows" % len(dev))

    if a.dry_run:
        print("\n  --dry-run: nothing written")
        return

    base = "%s/%s" % (OUT_PREFIX.rstrip("/"), run_id)
    written = []
    for name, df in (("ps3_model_runs", runs_df), ("ps3_head_summary", head),
                     ("ps3_device_predictions", dev)):
        buf = io.StringIO()
        df.to_csv(buf, index=False)
        written.append(s3_put("%s/%s.csv" % (base, name), buf.getvalue().encode("utf-8")))

    manifest = {
        "run_id": run_id, "run_kind": "batch_score", "city_id": CITY,
        "model_run": model_run, "as_of_date": as_of, "generated": now,
        "feature_contract_version": F.FEATURE_CONTRACT_VERSION,
        "score_dates": sorted({d for r in results for d in r["score_dates"]}),
        "artifacts": {"ps3_model_runs": len(runs_df), "ps3_head_summary": len(head),
                      "ps3_device_predictions": len(dev)},
        "fleets": [r["fleet"] for r in results],
    }
    written.append(s3_put("%s/manifest.json" % base,
                          json.dumps(manifest, indent=2).encode("utf-8"),
                          "application/json"))
    print("\n  written:")
    for w in written:
        print("    %s" % w)

    if a.invoke_loader:
        lam = boto3.client("lambda", region_name=REGION)
        r = lam.invoke(FunctionName=LOADER_FN, InvocationType="RequestResponse",
                       Payload=json.dumps({"run_id": run_id}).encode())
        body = json.loads(r["Payload"].read())
        print("\n  loader -> HTTP %s" % body.get("statusCode"))
        try:
            print("  %s" % json.dumps(json.loads(body["body"])["summary"], indent=2))
        except Exception:
            print("  %s" % str(body)[:600])


if __name__ == "__main__":
    main()
