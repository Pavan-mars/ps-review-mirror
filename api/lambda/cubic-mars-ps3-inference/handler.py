"""
cubic-mars-ps3-inference
=========================
NEW Lambda that scores NEW device/serial data against the PS3 two-head
SageMaker real-time endpoint (`deploy_endpoint.py` in sagemaker/ps3/) and
writes the results to S3 as Parquet + manifest.json in the exact contract
`cubic-mars-ps3-rds-push` (the sibling Lambda in this delivery) consumes --
so "score new data" and "push the outputs to RDS" are two decoupled,
independently-retryable Lambdas, same separation-of-concerns as the PS2
serial-grain delivery's notebook -> rds-push pair.

Two invocation shapes, detected automatically:
  1. API Gateway (synchronous, dashboard "Score new data" panel):
       POST /ps3/infer   body: {"instances": [{...feature row...}, ...]}
     Returns predictions INLINE (fast path for the UI) *and* fires the S3
     write for durability/audit + the RDS backfill.
  2. Direct/EventBridge/S3 invoke (batch, "new data landed" trigger):
       {"instances": [...]}                       -- direct payload, or
       {"s3_bucket": "...", "s3_key": "..."}       -- pointer to a CSV/JSON
     of new rows (e.g. dropped by a Databricks job that only has NEW rows
     since the last daily run, ahead of the next full daily batch score).

Endpoint contract: same inference.py `predict_fn` the endpoint serves --
this Lambda does NOT reimplement scoring logic, it only calls
sagemaker-runtime.invoke_endpoint and reshapes the response.

Environment variables expected at deploy time:
  SAGEMAKER_ENDPOINT_NAME  (e.g. cubic-mars-ps3-two-head-dev)
  OUTPUT_S3_BUCKET         (e.g. cubic-mars-pm-s3-datalake-dev-gold-170202974600)
  OUTPUT_S3_PREFIX         (default chicago/ps3_deepdive)
  CITY_ID                  (default CHI)
  AWS_REGION               (default us-east-1)

Runtime deps beyond boto3 (already in the Lambda runtime): pandas + pyarrow --
attach the AWS-managed `AWSSDKPandas-PythonXXX` layer rather than bundling
these (same choice the PS2 serial-grain notebook's Databricks environment
made implicitly; here it's explicit because this is a raw Lambda, not Glue).
"""
import io
import json
import logging
import os
import time
import uuid
from datetime import datetime, timezone

import boto3

logger = logging.getLogger()
logger.setLevel(logging.INFO)

REGION = os.environ.get("AWS_REGION", "us-east-1")
ENDPOINT_NAME = os.environ.get("SAGEMAKER_ENDPOINT_NAME", "cubic-mars-ps3-two-head-dev")
OUTPUT_BUCKET = os.environ.get("OUTPUT_S3_BUCKET", "")
OUTPUT_PREFIX = os.environ.get("OUTPUT_S3_PREFIX", "chicago/ps3_deepdive")
CITY_ID = os.environ.get("CITY_ID", "CHI")

_sm_rt = boto3.client("sagemaker-runtime", region_name=REGION)
_s3 = boto3.client("s3", region_name=REGION)

# 18-Jul live-run verdict fix -- code-keyed, NOT the buggy human-label map the base
# engine ships. Recomputed here too so an on-demand score is never mis-collapsed.
SEVERITY_COLLAPSE_FIXED = {
    "PURCHASE_CARD": "MAJOR", "PURCHASE_PRODUCT": "MAJOR", "NONPAYMENT": "MAJOR",
    "ALL_PURCHASE": "CRITICAL", "ALL_FUNCTIONS": "CRITICAL",
    "BUS_READER": "CRITICAL", "BUS_READER_ASSEMBLY": "CRITICAL",
}


def _cors_headers():
    return {"Access-Control-Allow-Origin": "*", "Access-Control-Allow-Headers": "Content-Type",
            "Access-Control-Allow-Methods": "POST,OPTIONS"}


def _api_response(status, body):
    return {"statusCode": status, "headers": {**_cors_headers(), "Content-Type": "application/json"},
            "body": json.dumps(body)}


def _load_instances(event):
    """Resolve `instances` from any of the three supported invocation shapes."""
    if "instances" in event:
        return event["instances"]
    if event.get("s3_bucket") and event.get("s3_key"):
        obj = _s3.get_object(Bucket=event["s3_bucket"], Key=event["s3_key"])
        raw = obj["Body"].read()
        if event["s3_key"].endswith(".json"):
            payload = json.loads(raw)
            return payload.get("instances", payload) if isinstance(payload, dict) else payload
        import csv
        text = raw.decode("utf-8")
        return list(csv.DictReader(io.StringIO(text)))
    # API Gateway HTTP API proxy shape
    body = event.get("body")
    if body is not None:
        if event.get("isBase64Encoded"):
            import base64
            body = base64.b64decode(body).decode("utf-8")
        payload = json.loads(body) if isinstance(body, str) else body
        return payload.get("instances", []) if isinstance(payload, dict) else payload
    return []


def _invoke_endpoint(instances):
    if not instances:
        return []
    resp = _sm_rt.invoke_endpoint(
        EndpointName=ENDPOINT_NAME, ContentType="application/json", Accept="application/json",
        Body=json.dumps({"instances": instances}),
    )
    body = json.loads(resp["Body"].read())
    return body.get("predictions", [])


def _enrich_and_reshape(instances, predictions, run_id):
    """Merge the endpoint's raw predictions with the corrected severity collapse
    and scoring metadata -- the exact row shape ps3_ondemand_inference_log expects."""
    now = datetime.now(timezone.utc).isoformat()
    rows = []
    for inst, pred in zip(instances, predictions):
        row = {
            "city_id": CITY_ID, "run_id": run_id,
            "device_id": inst.get("device_id") or pred.get("device_id"),
            "matched_serial_nbr": inst.get("matched_serial_nbr") or pred.get("matched_serial_nbr"),
            "availability_event_id": inst.get("availability_event_id") or pred.get("availability_event_id"),
            "mars_device_category": inst.get("mars_device_category"),
            "pred_severity": pred.get("pred_severity"),
            "pred_severity_conf": pred.get("pred_severity_conf"),
            "pred_severity_collapsed": SEVERITY_COLLAPSE_FIXED.get(str(pred.get("pred_severity")), "MAJOR")
                                       if pred.get("pred_severity") else None,
            "pred_component": pred.get("pred_component"),
            "pred_component_conf": pred.get("pred_component_conf"),
            "endpoint_name": ENDPOINT_NAME,
            "scored_at": now,
            "source": "ondemand",
        }
        rows.append(row)
    return rows


def _write_s3_manifest(rows, run_id):
    """Parquet + manifest.json, same contract as ps3_deepdive_engine.write_output() --
    cubic-mars-ps3-rds-push loads either producer's output identically."""
    if not OUTPUT_BUCKET or not rows:
        logger.info("[s3-write] skipped (OUTPUT_S3_BUCKET unset or no rows)")
        return None
    import pandas as pd
    df = pd.DataFrame(rows)
    df.columns = [c.strip().lower() for c in df.columns]
    computed_date = time.strftime("%Y-%m-%d")
    table_name = "ps3_ondemand_inference_log"
    key_prefix = f"{OUTPUT_PREFIX}/{table_name}"
    data_key = f"{key_prefix}/{table_name}_ondemand_{computed_date}_{run_id[:8]}.parquet"
    manifest_key = f"{key_prefix}/manifest_{run_id[:8]}.json"

    buf = io.BytesIO()
    df.to_parquet(buf, index=False)
    buf.seek(0)
    _s3.put_object(Bucket=OUTPUT_BUCKET, Key=data_key, Body=buf.getvalue())

    manifest = {"table_name": table_name, "grain": "ondemand", "computed_date": computed_date,
                "run_id": run_id, "row_count": len(df), "columns": list(df.columns),
                "s3_data_key": data_key}
    _s3.put_object(Bucket=OUTPUT_BUCKET, Key=manifest_key,
                   Body=json.dumps(manifest, indent=2).encode("utf-8"))
    logger.info("[s3-write] %d rows -> s3://%s/%s (+ %s)", len(df), OUTPUT_BUCKET, data_key, manifest_key)
    return {"data_key": data_key, "manifest_key": manifest_key, "row_count": len(df)}


def handler(event, context):
    run_id = str(uuid.uuid4())
    is_api_gw = "requestContext" in event or "httpMethod" in event

    try:
        instances = _load_instances(event)
        if not instances:
            msg = "no instances resolved from event (expected 'instances', or 's3_bucket'+'s3_key', or an API GW body)"
            logger.warning(msg)
            return _api_response(400, {"error": msg}) if is_api_gw else {"status": "error", "error": msg}

        logger.info("[infer] run_id=%s scoring %d instance(s) against endpoint %s",
                    run_id, len(instances), ENDPOINT_NAME)
        predictions = _invoke_endpoint(instances)
        rows = _enrich_and_reshape(instances, predictions, run_id)
        s3_result = _write_s3_manifest(rows, run_id)

        result = {"run_id": run_id, "endpoint_name": ENDPOINT_NAME, "n_scored": len(rows),
                  "predictions": rows, "s3_write": s3_result}
        return _api_response(200, result) if is_api_gw else result

    except _sm_rt.exceptions.ModelError as e:
        logger.exception("[infer] endpoint returned a model error")
        msg = f"SageMaker endpoint model error: {e}"
        return _api_response(502, {"error": msg, "run_id": run_id}) if is_api_gw else {"status": "error", "error": msg}
    except Exception as e:
        logger.exception("[infer] unhandled error")
        msg = f"{type(e).__name__}: {e}"
        return _api_response(500, {"error": msg, "run_id": run_id}) if is_api_gw else {"status": "error", "error": msg}
