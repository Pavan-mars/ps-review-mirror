"""Local /ping + /invocations gate for the PS3 BYOC container.

Builds serving bundles in the EXACT schema fit_final_serving_bundle() writes in
PS3_RootCause_Severity_SageMaker_RDS_Dashboard_Ready.ipynb, then drives the real
serve.py Flask app. This is the mandatory pre-ECR gate: if /ping is not 200 here
it will not be 200 in SageMaker, and the endpoint will never reach InService.
"""
import json, os, shutil, sys, tempfile
import numpy as np, pandas as pd, joblib
from sklearn.compose import ColumnTransformer
from sklearn.pipeline import Pipeline
from sklearn.impute import SimpleImputer
from sklearn.preprocessing import StandardScaler, OneHotEncoder, LabelEncoder
from sklearn.ensemble import RandomForestClassifier

NUM = ["component_age_days", "events_24h_prior", "oos_onsets_7d_prior", "facility_incident_freq"]
CAT = ["mars_device_category", "FACILITY_ID"]
FEATURES = NUM + CAT
rng = np.random.default_rng(7)


def make_frame(n, cat_value):
    return pd.DataFrame({
        "availability_event_id": [f"AE{i:05d}" for i in range(n)],
        "device_id": [f"DEV{i%40:04d}" for i in range(n)],
        "matched_serial_nbr": [f"SN{i%37:05d}" for i in range(n)],
        "component_age_days": rng.uniform(1, 2000, n),
        "events_24h_prior": rng.integers(0, 40, n),
        "oos_onsets_7d_prior": rng.integers(0, 12, n),
        "facility_incident_freq": rng.uniform(0, 1, n),
        "mars_device_category": [cat_value] * n,
        "FACILITY_ID": rng.choice(["F01", "F02", "F03"], n),
    })


def build_bundle(out_dir, category, head, labels, gate_pass):
    os.makedirs(out_dir, exist_ok=True)
    df = make_frame(400, category)
    y = rng.choice(labels, len(df))
    enc = LabelEncoder().fit(y)
    prep = ColumnTransformer([
        ("num", Pipeline([("imp", SimpleImputer(strategy="median")), ("sc", StandardScaler())]), NUM),
        ("cat", Pipeline([("imp", SimpleImputer(strategy="most_frequent")),
                          ("oh", OneHotEncoder(handle_unknown="ignore"))]), CAT),
    ])
    X = df[FEATURES].copy()
    Xt = prep.fit_transform(X)
    model = RandomForestClassifier(n_estimators=25, random_state=0).fit(Xt, enc.transform(y))
    joblib.dump({
        "model": model, "preprocessor": prep,
        "numeric_features": NUM, "categorical_features": CAT, "features": FEATURES,
        "label_encoder": enc, "head": head, "device_category": category,
        "target": "failure_level_label" if head == "severity" else "derived_component_type",
        "feature_time_contract": {f: "as_of" for f in FEATURES},
        "training_note": "synthetic fixture",
    }, os.path.join(out_dir, "serving_bundle.joblib"))
    with open(os.path.join(out_dir, "champion_summary.json"), "w") as fh:
        json.dump({"device_category": category, "head": head, "gate_pass": gate_pass,
                   "test_f1_macro": 0.76 if gate_pass else 0.4978,
                   "macro_f1_floor": 0.55}, fh)


root = tempfile.mkdtemp()
model_dir = os.path.join(root, "model")
build_bundle(f"{model_dir}/TVM/severity",   "TVM",  "severity",
             ["ALL_FUNCTIONS", "ALL_PURCHASE", "PURCHASE_CARD", "PURCHASE_PRODUCT"], True)
build_bundle(f"{model_dir}/TVM/root_cause", "TVM",  "root_cause",
             ["BHU", "CHU", "COMMS", "CSC_READER", "PRINTER"], True)
build_bundle(f"{model_dir}/GATE/severity",  "GATE", "severity", ["ALL_FUNCTIONS", "OTHER"], False)
build_bundle(f"{model_dir}/GATE/root_cause", "GATE", "root_cause", ["COMMS", "CSC_READER", "GATE_MECH"], True)
print(f"fixtures built under {model_dir}\n" + "=" * 72)

os.environ["SM_MODEL_DIR"] = model_dir
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import serve  # noqa: E402  (reads SM_MODEL_DIR at import)

client = serve.app.test_client()
fails = []

r = client.get("/ping")
print(f"GET  /ping                -> {r.status_code}")
if r.status_code != 200:
    print("   body:", r.data.decode()[:400]); fails.append("ping")

served = sorted(serve._model().keys())
print(f"heads served              -> {served}")
if "GATE/severity" in served:
    fails.append("GATE/severity was served despite gate_pass=false")
else:
    print("GATE/severity correctly REFUSED (failed promotion gate)")

payload = make_frame(5, "TVM").drop(columns=["matched_serial_nbr"]).to_dict(orient="records")
r = client.post("/invocations", data=json.dumps({"instances": payload}),
                content_type="application/json")
print(f"POST /invocations (json)  -> {r.status_code}")
if r.status_code != 200:
    print("   body:", r.data.decode()[:600]); fails.append("invocations-json")
else:
    preds = json.loads(r.data)["predictions"]
    print(f"   rows returned: {len(preds)}")
    print("   sample:", json.dumps(preds[0], indent=None)[:260])
    if len(preds) != 5:
        fails.append("row count mismatch")
    got = {p.get("pred_severity_collapsed") for p in preds}
    if not got <= {"MAJOR", "CRITICAL", "UNKNOWN"}:
        fails.append(f"bad collapsed values {got}")
    # the collapse must agree with the code-keyed map, not default to MAJOR
    for p in preds:
        exp = {"ALL_FUNCTIONS": "CRITICAL", "ALL_PURCHASE": "CRITICAL",
               "PURCHASE_CARD": "MAJOR", "PURCHASE_PRODUCT": "MAJOR"}.get(p["pred_severity"])
        if exp and p["pred_severity_collapsed"] != exp:
            fails.append(f"collapse wrong: {p['pred_severity']} -> {p['pred_severity_collapsed']}")

    # cross-category safety: a TVM row must NOT be scored by a GATE model
    for p_ in preds:
        sb = p_.get("served_by", [])
        if any(k.startswith("GATE/") for k in sb):
            fails.append(f"TVM row scored by a GATE model: {sb}")
    if preds and sorted(preds[0].get("served_by", [])) != ["TVM/root_cause", "TVM/severity"]:
        fails.append(f"unexpected served_by: {preds[0].get('served_by')}")

# a GATE row must get GATE/root_cause only (GATE/severity is gated out)
r = client.post("/invocations", data=json.dumps(make_frame(3, "GATE").to_dict(orient="records")),
                content_type="application/json")
gp = json.loads(r.data)["predictions"]
print(f"POST /invocations (GATE)  -> {r.status_code}  served_by={gp[0].get('served_by')}")
if sorted(gp[0].get("served_by", [])) != ["GATE/root_cause"]:
    fails.append(f"GATE row served_by wrong: {gp[0].get('served_by')}")
if "pred_severity" in gp[0]:
    fails.append("GATE row got a severity prediction despite the failed gate")

# an unknown category must be returned unscored, not mis-scored
r = client.post("/invocations", data=json.dumps(make_frame(2, "VALIDATOR").to_dict(orient="records")),
                content_type="application/json")
vp = json.loads(r.data)["predictions"]
print(f"POST /invocations (VALID) -> {r.status_code}  scored={vp[0].get('scored')} reason={str(vp[0].get('reason'))[:60]}")
if vp[0].get("scored") is not False:
    fails.append("VALIDATOR row was scored despite no VALIDATOR model")

csv_body = make_frame(3, "TVM").to_csv(index=False)
r = client.post("/invocations", data=csv_body, content_type="text/csv")
print(f"POST /invocations (csv)   -> {r.status_code}")
if r.status_code != 200:
    print("   body:", r.data.decode()[:400]); fails.append("invocations-csv")

# missing feature columns must not 500 -- real payloads are often partial
r = client.post("/invocations", data=json.dumps([{"device_id": "DEV0001"}]),
                content_type="application/json")
print(f"POST /invocations (sparse)-> {r.status_code}")
if r.status_code != 200:
    print("   body:", r.data.decode()[:400]); fails.append("sparse-payload")

print("=" * 72)
print("RESULT:", "ALL GREEN - safe to build/push ECR" if not fails else f"FAILURES: {fails}")
shutil.rmtree(root, ignore_errors=True)
sys.exit(1 if fails else 0)
