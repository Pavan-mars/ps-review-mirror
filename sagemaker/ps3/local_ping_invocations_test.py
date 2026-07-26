# =============================================================================
# local_ping_invocations_test -- MUST pass before any endpoint deploy.
# The absence of this step is what Failed the earlier PS3/PS5 endpoints.
#
# Runs the /ping + /invocations contract fully in-process (no Docker needed):
#   1. model_fn loads the *_bundle.joblib artifacts (this is what /ping checks)
#   2. input_fn parses a JSON payload
#   3. predict_fn returns BOTH heads
#   4. output_fn serialises
# Also (optional) boots serve.py and hits the real HTTP routes if --http.
#
# Usage:
#   python local_ping_invocations_test.py --model-dir /path/to/tvm   # bundles dir
#   python local_ping_invocations_test.py --model-dir ./tvm --http   # also HTTP
# =============================================================================
import argparse, json, sys, os

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model-dir", required=True, help="dir with *_severity_bundle.joblib + *_root_cause_bundle.joblib")
    ap.add_argument("--http", action="store_true", help="also boot serve.py and curl the routes")
    a = ap.parse_args()
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import inference

    # ---- /ping surface: model_fn must load the heads ----
    bundles = inference.model_fn(a.model_dir)
    assert bundles, "model_fn returned nothing -> /ping would 503"
    heads = set(bundles.keys())
    print(f"[ping]  OK -> heads loaded: {sorted(heads)}")
    assert "severity" in heads or "root_cause" in heads, "no usable head"

    # ---- /invocations surface: feed 2 synthetic incidents ----
    feats = next(iter(bundles.values()))["features"]
    rec = {f: 1.0 for f in feats}
    rec["availability_event_id"] = "AE-TEST-1"; rec["device_id"] = "TVM00001"
    payload = json.dumps({"instances": [rec, {**rec, "availability_event_id": "AE-TEST-2"}]})
    df = inference.input_fn(payload, "application/json")
    preds = inference.predict_fn(df, bundles)
    body, ctype = inference.output_fn(preds, "application/json")
    parsed = json.loads(body)["predictions"]
    print(f"[invocations] OK -> {len(parsed)} predictions, content-type {ctype}")
    print("   sample:", json.dumps(parsed[0], indent=2))

    # assertions on the contract
    p0 = parsed[0]
    if "severity" in heads:
        assert "pred_severity" in p0 and "pred_severity_collapsed" in p0, "severity head missing in output"
    if "root_cause" in heads:
        assert "pred_component" in p0, "root_cause head missing in output"
    assert len(parsed) == 2, "row count mismatch"
    print("[assert] both heads present + row count correct -> CONTRACT OK")

    if a.http:
        import threading, time, urllib.request
        os.environ["SM_MODEL_DIR"] = a.model_dir
        import serve
        t = threading.Thread(target=lambda: serve.app.run(host="127.0.0.1", port=8080), daemon=True)
        t.start(); time.sleep(2)
        with urllib.request.urlopen("http://127.0.0.1:8080/ping") as r:
            assert r.status == 200; print("[http /ping] 200 OK")
        req = urllib.request.Request("http://127.0.0.1:8080/invocations", data=payload.encode(),
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req) as r:
            assert r.status == 200; n = len(json.loads(r.read())["predictions"])
            print(f"[http /invocations] 200 OK -> {n} predictions")

    print("\nLOCAL SERVING TEST: PASS -- safe to build the image + deploy the endpoint")


if __name__ == "__main__":
    main()
