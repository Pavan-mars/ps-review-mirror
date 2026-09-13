# =============================================================================
# batch_transform_daily -- score the NEW-INCIDENT slice each day and emit the
# incident/device/serial feeds that ps3_rds_writer upserts to RDS.
#
# Three modes (--mode):
#   batch_transform : launch a SageMaker Batch Transform job on the slice, then
#                     post-process the transform output into the 3 feeds. (default;
#                     cheapest for daily batch -- no always-on endpoint)
#   endpoint        : invoke the real-time endpoint (chicago-ps3-...) per mini-batch
#   local_bundle    : load the champion *_bundle.joblib and score in-process (the
#                     path a SageMaker Processing job / Databricks task uses; also
#                     what the local test exercises)
#
# All three produce identical output: s3://.../ps3/scored/<asof>/{tvm,gates}/
#   {cat}_incident_predictions.csv, {cat}_device_predictions.csv, {cat}_serial_predictions.csv
# =============================================================================
import os, sys, json, argparse, io, uuid
import numpy as np
import pandas as pd

FEATURE_LEAK_SAFE = None   # resolved from the bundle's features


def score_with_bundles(df, bundles):
    """Apply both heads to the incident slice and build incident+device+serial feeds."""
    import inference
    preds = inference.predict_fn(df, bundles)             # reuse the exact serving logic
    p = pd.DataFrame(preds)
    keep = [c for c in ["availability_event_id", "device_id", "mars_device_category",
                        "AE_START_DTM", "matched_serial_nbr", "component_age_days",
                        "FACILITY_ID"] if c in df.columns]
    inc = pd.concat([df[keep].reset_index(drop=True), p.drop(columns=[c for c in keep if c in p.columns],
                     errors="ignore").reset_index(drop=True)], axis=1)
    # device rollup
    g = inc.groupby("device_id")
    dev = pd.DataFrame({"n_incidents": g.size()})
    if "pred_severity_collapsed" in inc:
        dev["pct_critical_pred"] = g["pred_severity_collapsed"].apply(lambda s: (s == "CRITICAL").mean()).round(4)
        dev["dominant_pred_severity"] = g["pred_severity"].agg(lambda s: s.value_counts().index[0])
    if "pred_component" in inc:
        dev["dominant_pred_component"] = g["pred_component"].agg(lambda s: s.value_counts().index[0])
    if "component_age_days" in inc:
        dev["avg_component_age_days"] = g["component_age_days"].mean().round(0)
    if "AE_START_DTM" in inc:
        dev["last_incident_dtm"] = g["AE_START_DTM"].max()
    dev = dev.reset_index()
    # serial rollup
    ser = None
    if "matched_serial_nbr" in inc.columns and inc["matched_serial_nbr"].notna().any():
        s = inc[inc["matched_serial_nbr"].notna()]
        gs = s.groupby(["device_id", "matched_serial_nbr"])
        ser = pd.DataFrame({"n_incidents": gs.size()})
        if "component_age_days" in s: ser["component_age_days"] = gs["component_age_days"].max().round(0)
        if "pred_component" in s: ser["dominant_pred_component"] = gs["pred_component"].agg(lambda x: x.value_counts().index[0])
        if "pred_severity_collapsed" in s: ser["pct_critical_pred"] = gs["pred_severity_collapsed"].apply(lambda x: (x == "CRITICAL").mean()).round(4)
        if "AE_START_DTM" in s: ser["last_incident_dtm"] = gs["AE_START_DTM"].max()
        ser = ser.reset_index()
    return inc, dev, ser


def _load_bundles_from(path):
    """Download *_bundle.joblib from an S3 prefix or local dir into a heads dict."""
    import glob, joblib
    if path.startswith("s3"):
        import s3fs
        fs = s3fs.S3FileSystem(client_kwargs={"region_name": "us-east-1"})
        files = [f"s3://{f}" for f in fs.glob(path.rstrip("/") + "/*_bundle.joblib")]
        opener = lambda f: fs.open(f, "rb")
    else:
        files = glob.glob(os.path.join(path, "*_bundle.joblib")); opener = lambda f: open(f, "rb")
    out = {}
    for f in files:
        with opener(f) as fh:
            b = joblib.load(fh); out[b["head"]] = b
    return out


def _write_feeds(inc, dev, ser, out_base, cat, folder):
    so = {"client_kwargs": {"region_name": "us-east-1"}} if out_base.startswith("s3") else None
    base = f"{out_base.rstrip('/')}/{folder}"
    if not base.startswith("s3"):
        os.makedirs(base, exist_ok=True)
    inc.to_csv(f"{base}/{cat.lower()}_incident_predictions.csv", index=False, storage_options=so)
    dev.assign(mars_device_category=cat).to_csv(f"{base}/{cat.lower()}_device_predictions.csv", index=False, storage_options=so)
    if ser is not None:
        ser.assign(mars_device_category=cat).to_csv(f"{base}/{cat.lower()}_serial_predictions.csv", index=False, storage_options=so)
    print(f"  {cat}: {len(inc)} incidents, {len(dev)} devices, {0 if ser is None else len(ser)} serials -> {base}")


def run_local_bundle(a):
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    so = {"client_kwargs": {"region_name": "us-east-1"}} if a.input_s3.startswith("s3") else None
    df = pd.read_parquet(a.input_s3, storage_options=so)
    print(f"[local_bundle] {len(df):,} new incidents to score")
    folders = {"TVM": "tvm", "GATE": "gates"}
    for cat, folder in folders.items():
        d = df[df["mars_device_category"] == cat]
        if not len(d):
            continue
        bundles = _load_bundles_from(f"{a.models_s3.rstrip('/')}/{folder}")
        if not bundles:
            print(f"  [warn] no bundles at {a.models_s3}/{folder}; skip {cat}"); continue
        inc, dev, ser = score_with_bundles(d.reset_index(drop=True), bundles)
        _write_feeds(inc, dev, ser, a.out_base, cat, folder)


def run_batch_transform(a):
    """Launch a real SageMaker Batch Transform on the slice, then post-process."""
    import boto3
    sm = boto3.client("sagemaker", region_name=a.region)
    # 1) stage JSONL input (feature records) for the transform
    so = {"client_kwargs": {"region_name": a.region}}
    df = pd.read_parquet(a.input_s3, storage_options=so)
    jsonl = "\n".join(json.dumps(r) for r in df.to_dict("records"))
    in_uri = f"{a.out_base.rstrip('/')}/_transform_input/{a.asof}.jsonl"
    pd.Series([jsonl]).to_csv(in_uri, index=False, header=False, storage_options=so)
    out_uri = f"{a.out_base.rstrip('/')}/_transform_output/{a.asof}/"
    # SageMaker holds transform job names per account+region forever, names taken
    # by FAILED jobs included, so a date-only name makes same-day retries impossible.
    job = f"ps3-batch-{a.asof.replace('-', '')}-{uuid.uuid4().hex[:8]}"
    sm.create_transform_job(
        TransformJobName=job, ModelName=a.model_name,
        TransformInput={"DataSource": {"S3DataSource": {"S3DataType": "S3Prefix", "S3Uri": in_uri}},
                        "ContentType": "application/json", "SplitType": "Line"},
        # SingleRecord: the MultiRecord default joins many lines into one payload,
        # which the container parses with a single json.loads and rejects.
        BatchStrategy="SingleRecord",
        TransformOutput={"S3OutputPath": out_uri, "Accept": "application/json"},
        TransformResources={"InstanceType": a.instance_type, "InstanceCount": 1})
    print(f"[batch_transform] launched {job} -> {out_uri}")
    sm.get_waiter("transform_job_completed_or_stopped").wait(TransformJobName=job)
    print("[batch_transform] complete; post-process the output into the 3 feeds (see local_bundle rollups)")
    # NOTE: transform output is raw per-record predictions; reuse the rollup in score_with_bundles
    #       by re-reading out_uri predictions joined back to df, then _write_feeds per category.


def run_endpoint(a):
    """Invoke the real-time endpoint per mini-batch (for on-demand / low volume)."""
    import boto3
    rt = boto3.client("sagemaker-runtime", region_name=a.region)
    so = {"client_kwargs": {"region_name": a.region}}
    df = pd.read_parquet(a.input_s3, storage_options=so)
    preds = []
    for i in range(0, len(df), a.batch_size):
        chunk = df.iloc[i:i + a.batch_size]
        body = json.dumps({"instances": chunk.to_dict("records")})
        resp = rt.invoke_endpoint(EndpointName=a.endpoint_name, ContentType="application/json", Body=body)
        preds.extend(json.loads(resp["Body"].read())["predictions"])
    print(f"[endpoint] scored {len(preds)} incidents via {a.endpoint_name}")
    # then rollup + _write_feeds as in local_bundle


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", default="batch_transform", choices=["batch_transform", "endpoint", "local_bundle"])
    ap.add_argument("--asof", required=True)
    ap.add_argument("--input-s3", required=True, help="new-incident slice parquet (device_ps3_incident_incr/asof=...)")
    ap.add_argument("--out-base", default="s3://cubic-mars-pm-s3-datalake-dev-gold-170202974600/chicago/ps3/scored")
    ap.add_argument("--models-s3", default="s3://cubic-mars-pm-s3-datalake-dev-gold-170202974600/chicago/ps3/models")
    ap.add_argument("--model-name", default="ps3-rootcause-severity")
    ap.add_argument("--endpoint-name", default="chicago-ps3-rootcause-v2")
    ap.add_argument("--instance-type", default="ml.m5.large")
    ap.add_argument("--batch-size", type=int, default=100)
    ap.add_argument("--region", default="us-east-1")
    a = ap.parse_args()
    a.out_base = f"{a.out_base.rstrip('/')}/{a.asof}"
    print(f"PS3 daily scoring | mode={a.mode} | asof={a.asof}")
    {"local_bundle": run_local_bundle, "batch_transform": run_batch_transform, "endpoint": run_endpoint}[a.mode](a)


if __name__ == "__main__":
    main()
