# Unit tests for cubic-mars-ps3-inference/handler.py -- mocks sagemaker-runtime
# and S3 (no live AWS needed). Run: python3 -m pytest test_handler.py -v
import json
from unittest.mock import MagicMock, patch

import pytest

import handler


def _mock_endpoint_response(preds):
    body = MagicMock()
    body.read.return_value = json.dumps({"predictions": preds}).encode("utf-8")
    return {"Body": body}


def test_direct_invoke_instances_shape():
    """Case 1: direct payload with 'instances' -- the batch/EventBridge shape."""
    event = {"instances": [{"device_id": "TVM-0001", "matched_serial_nbr": "SER-1",
                            "mars_device_category": "TVM", "component_age_days": 400}]}
    fake_pred = [{"device_id": "TVM-0001", "pred_severity": "ALL_FUNCTIONS", "pred_severity_conf": 0.81,
                 "pred_component": "CSC_READER", "pred_component_conf": 0.62}]
    with patch.object(handler, "_sm_rt") as mock_rt, patch.object(handler, "OUTPUT_BUCKET", ""):
        mock_rt.invoke_endpoint.return_value = _mock_endpoint_response(fake_pred)
        mock_rt.exceptions.ModelError = Exception
        result = handler.handler(event, None)
    assert result["n_scored"] == 1
    row = result["predictions"][0]
    # ALL_FUNCTIONS must collapse to CRITICAL under the CORRECTED (code-keyed) map --
    # this is the exact 18-Jul bug this delivery must not repeat.
    assert row["pred_severity_collapsed"] == "CRITICAL"
    assert row["device_id"] == "TVM-0001"


def test_api_gateway_body_shape():
    """Case 2: API Gateway HTTP proxy event -- body is a JSON string."""
    event = {"requestContext": {"http": {"method": "POST"}},
             "body": json.dumps({"instances": [{"device_id": "GATE-0007", "mars_device_category": "GATE"}]})}
    fake_pred = [{"pred_severity": "PURCHASE_CARD", "pred_severity_conf": 0.55,
                 "pred_component": None, "pred_component_conf": None}]
    with patch.object(handler, "_sm_rt") as mock_rt, patch.object(handler, "OUTPUT_BUCKET", ""):
        mock_rt.invoke_endpoint.return_value = _mock_endpoint_response(fake_pred)
        mock_rt.exceptions.ModelError = Exception
        result = handler.handler(event, None)
    assert result["statusCode"] == 200
    body = json.loads(result["body"])
    assert body["predictions"][0]["pred_severity_collapsed"] == "MAJOR"  # PURCHASE_CARD -> MAJOR
    assert result["headers"]["Access-Control-Allow-Origin"] == "*"


def test_no_instances_returns_400_on_api_gw():
    event = {"requestContext": {}, "body": json.dumps({})}
    result = handler.handler(event, None)
    assert result["statusCode"] == 400


def test_no_instances_returns_error_dict_on_direct_invoke():
    result = handler.handler({}, None)
    assert result["status"] == "error"


def test_s3_write_called_when_bucket_configured():
    """When OUTPUT_S3_BUCKET is set, results must be written as Parquet + manifest.json
    in the exact shape cubic-mars-ps3-rds-push expects (table_name/grain/computed_date/
    run_id/row_count/columns/s3_data_key)."""
    event = {"instances": [{"device_id": "TVM-0002", "mars_device_category": "TVM"}]}
    fake_pred = [{"pred_severity": "ALL_PURCHASE", "pred_severity_conf": 0.9,
                 "pred_component": "BHU", "pred_component_conf": 0.7}]
    with patch.object(handler, "_sm_rt") as mock_rt, \
         patch.object(handler, "_s3") as mock_s3, \
         patch.object(handler, "OUTPUT_BUCKET", "test-bucket"):
        mock_rt.invoke_endpoint.return_value = _mock_endpoint_response(fake_pred)
        mock_rt.exceptions.ModelError = Exception
        result = handler.handler(event, None)
    assert result["s3_write"] is not None
    assert result["s3_write"]["row_count"] == 1
    put_calls = [c for c in mock_s3.put_object.call_args_list]
    assert len(put_calls) == 2  # one parquet object, one manifest.json object
    manifest_call = next(c for c in put_calls if c.kwargs["Key"].endswith("manifest_" + result["run_id"][:8] + ".json"))
    manifest = json.loads(manifest_call.kwargs["Body"])
    assert manifest["table_name"] == "ps3_ondemand_inference_log"
    assert manifest["row_count"] == 1
    assert manifest["s3_data_key"].endswith(".parquet")


def test_endpoint_model_error_returns_502_on_api_gw():
    event = {"requestContext": {}, "body": json.dumps({"instances": [{"device_id": "X"}]})}
    with patch.object(handler, "_sm_rt") as mock_rt, patch.object(handler, "OUTPUT_BUCKET", ""):
        mock_rt.exceptions.ModelError = type("ModelError", (Exception,), {})
        mock_rt.invoke_endpoint.side_effect = mock_rt.exceptions.ModelError("bad input")
        result = handler.handler(event, None)
    assert result["statusCode"] == 502


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-v"]))
