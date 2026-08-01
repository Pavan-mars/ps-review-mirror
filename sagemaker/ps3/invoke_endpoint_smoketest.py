# =============================================================================
# invoke_endpoint_smoketest.py -- once deploy_endpoint.py reports InService,
# run this to sanity-check the REAL endpoint the same way
# local_ping_invocations_test.py sanity-checked the container locally. This is
# the honest "did the endpoint actually come up correctly" check before the new
# cubic-mars-ps3-inference Lambda (or the dashboard) starts depending on it.
#
# Usage:
#   python3 invoke_endpoint_smoketest.py --endpoint-name cubic-mars-ps3-two-head-dev
# =============================================================================
import argparse
import json
import sys

import boto3

SAMPLE_PAYLOAD = {
    "instances": [
        {"device_id": "TVM-0001", "availability_event_id": "SMOKETEST-1", "matched_serial_nbr": "SER-00001",
         "events_24h_prior": 3, "oos_onsets_24h": 0, "bhu_events_24h": 1, "chu_events_24h": 0,
         "printer_events_24h": 0, "gate_mech_events_24h": 0, "csc_reader_events_24h": 2,
         "comms_events_24h": 0, "oos_onsets_7d_prior": 1, "events_7d_prior": 9,
         "component_age_days": 420, "ae_hour": 14, "ae_dow": 2, "ae_month": 7,
         "facility_incident_freq": 5},
    ]
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--endpoint-name", required=True)
    ap.add_argument("--region", default="us-east-1")
    a = ap.parse_args()

    rt = boto3.client("sagemaker-runtime", region_name=a.region)
    print(f"Invoking {a.endpoint_name} with a synthetic sample row...")
    resp = rt.invoke_endpoint(
        EndpointName=a.endpoint_name,
        ContentType="application/json",
        Accept="application/json",
        Body=json.dumps(SAMPLE_PAYLOAD),
    )
    body = json.loads(resp["Body"].read())
    print(json.dumps(body, indent=2))

    preds = body.get("predictions", [])
    if not preds:
        print("FAIL: no predictions returned", file=sys.stderr)
        sys.exit(1)
    p = preds[0]
    ok = ("pred_severity" in p) or ("pred_component" in p)
    if not ok:
        print("FAIL: response has neither pred_severity nor pred_component -- check model artifact/bundles", file=sys.stderr)
        sys.exit(1)
    print("\nPASS -- endpoint responds with at least one head's prediction.")


if __name__ == "__main__":
    main()
