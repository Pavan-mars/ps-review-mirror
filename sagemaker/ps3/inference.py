# =============================================================================
# PS3 inference handler -- serves BOTH heads (severity + root-cause/component)
# from one model artifact. SageMaker framework-handler contract:
#   model_fn -> input_fn -> predict_fn -> output_fn.
# serve.py wraps these into /ping + /invocations for BYOC.
#
# Model artifact layout (tar.gz unpacked into model_dir), from the notebook:
#   {cat}_severity_bundle.joblib     = {prep, model, label_encoder, features, head, category, target}
#   {cat}_root_cause_bundle.joblib   = same shape, head='root_cause'
# =============================================================================
import os, json, glob
import numpy as np
import pandas as pd
import joblib

SEVERITY_COLLAPSE = {
    "Purchase Card Functions": "MAJOR", "Purchase Product Functions": "MAJOR",
    "All Purchase Functions": "CRITICAL", "All Functions": "CRITICAL",
    "Bus Reader Assembly Fail": "CRITICAL", "Nonpayment Functions": "MAJOR",
}


def model_fn(model_dir):
    """Load every *_bundle.joblib in the artifact; key them by head."""
    bundles = {}
    for path in glob.glob(os.path.join(model_dir, "*_bundle.joblib")):
        b = joblib.load(path)
        bundles[b["head"]] = b            # 'severity' and/or 'root_cause'
    if not bundles:
        raise RuntimeError(f"no *_bundle.joblib found in {model_dir}")
    print(f"[model_fn] loaded heads: {list(bundles.keys())} | "
          f"category={next(iter(bundles.values())).get('category')}")
    return bundles


def input_fn(request_body, content_type="application/json"):
    """Accept JSON (records or {'instances': [...]}) or CSV -> DataFrame."""
    if content_type == "application/json":
        payload = json.loads(request_body)
        if isinstance(payload, dict) and "instances" in payload:
            payload = payload["instances"]
        if isinstance(payload, dict):
            payload = [payload]
        return pd.DataFrame(payload)
    if content_type in ("text/csv", "application/csv"):
        from io import StringIO
        return pd.read_csv(StringIO(request_body))
    raise ValueError(f"unsupported content_type: {content_type}")


def _predict_head(bundle, df):
    feats = bundle["features"]
    X = df.reindex(columns=feats).apply(pd.to_numeric, errors="coerce").astype("float64")
    proba = bundle["model"].predict_proba(bundle["prep"].transform(X))
    idx = proba.argmax(axis=1)
    labels = bundle["label_encoder"].inverse_transform(idx)
    conf = proba.max(axis=1)
    return labels, conf


def predict_fn(df, bundles):
    """Return one record per input row with both heads' predictions."""
    out = [{} for _ in range(len(df))]
    # carry ids through if present
    for idcol in ("availability_event_id", "device_id", "matched_serial_nbr"):
        if idcol in df.columns:
            for i, v in enumerate(df[idcol].tolist()):
                out[i][idcol] = v
    if "severity" in bundles:
        labs, conf = _predict_head(bundles["severity"], df)
        for i in range(len(df)):
            out[i]["pred_severity"] = str(labs[i])
            out[i]["pred_severity_conf"] = round(float(conf[i]), 5)
            out[i]["pred_severity_collapsed"] = SEVERITY_COLLAPSE.get(str(labs[i]), "MAJOR")
    if "root_cause" in bundles:
        labs, conf = _predict_head(bundles["root_cause"], df)
        for i in range(len(df)):
            out[i]["pred_component"] = str(labs[i])
            out[i]["pred_component_conf"] = round(float(conf[i]), 5)
    return out


def output_fn(prediction, accept="application/json"):
    return json.dumps({"predictions": prediction}), "application/json"
