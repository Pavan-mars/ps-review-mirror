# =============================================================================
# serve.py -- SageMaker BYOC inference for the PS5 RUL scorer (Batch Transform).
# Implements GET /ping and POST /invocations. Loads the slim params from
# /opt/ml/model at startup and scores device-state OR serial-roster payloads with
# the shared, verified ps5_scoring_core (params-only numpy -- no sklearn).
#
# Auto-detects the payload:
#   * has COMPONENT_SERIAL_NBR            -> serial scoring   (serial params)
#   * has current_healthy_age_days / feats-> device scoring   (device params)
# Routes each row to the right device type via a 'mars_device_category' column;
# if that column is absent, falls back to env PS5_INPUT_TYPE (TVM|GATE|VALIDATOR).
#
# Input/Output: parquet by default (compact, typed). CSV accepted on input.
# =============================================================================
import os, io, glob, json

import numpy as np  # noqa: F401  (kept for parity / debugging)
import pandas as pd
from flask import Flask, request, Response

from ps5_scoring_core import score_devices, score_serials, read_table, EVENT_DEF_FALLBACK

MODEL_DIR = os.environ.get("SM_MODEL_DIR", "/opt/ml/model")
DEFAULT_TYPE = os.environ.get("PS5_INPUT_TYPE", "").upper()   # optional fallback when no category column

app = Flask(__name__)


def _load_params():
    dev, ser = {}, {}
    for p in glob.glob(os.path.join(MODEL_DIR, "*_device_survival_params.json")):
        d = json.load(open(p)); dev[str(d.get("category", "")).upper()] = d
    for p in glob.glob(os.path.join(MODEL_DIR, "*_serial_params.json")):
        d = json.load(open(p)); ser[str(d.get("category", "")).upper()] = d
    return dev, ser


DEV_PARAMS, SER_PARAMS = _load_params()
print(f"[serve] loaded device params for {sorted(DEV_PARAMS)} | serial params for {sorted(SER_PARAMS)} | "
      f"event={next(iter(DEV_PARAMS.values()), {}).get('event_def_version', EVENT_DEF_FALLBACK[1]) if DEV_PARAMS else 'n/a'}")


def _out(df):
    buf = io.BytesIO(); df.to_parquet(buf, index=False)
    return Response(buf.getvalue(), status=200, mimetype="application/x-parquet")


def _groups(df):
    """Yield (category, sub_df) -- by mars_device_category col, else the DEFAULT_TYPE fallback, else all-as-one."""
    if "mars_device_category" in df.columns and df["mars_device_category"].notna().any():
        for cat, g in df.groupby(df["mars_device_category"].astype(str).str.upper()):
            yield cat, g
    elif DEFAULT_TYPE:
        yield DEFAULT_TYPE, df
    else:
        yield None, df


@app.route("/ping", methods=["GET"])
def ping():
    healthy = len(DEV_PARAMS) > 0 or len(SER_PARAMS) > 0
    return Response("ok" if healthy else "no params loaded", status=200 if healthy else 500, mimetype="text/plain")


@app.route("/invocations", methods=["POST"])
def invocations():
    try:
        df = read_table(request.get_data())
    except Exception as e:
        return Response(f"cannot read payload: {e}", status=400, mimetype="text/plain")
    if df is None or not len(df):
        return Response("empty payload", status=400, mimetype="text/plain")

    is_serial = "COMPONENT_SERIAL_NBR" in df.columns or "component_serial_nbr" in df.columns
    params_by_cat = SER_PARAMS if is_serial else DEV_PARAMS
    score = score_serials if is_serial else score_devices
    if is_serial and "COMPONENT_SERIAL_NBR" not in df.columns and "component_serial_nbr" in df.columns:
        df = df.rename(columns={"component_serial_nbr": "COMPONENT_SERIAL_NBR",
                                "component_type_name": "COMPONENT_TYPE_NAME", "device_id": "DEVICE_ID"})

    out = []
    for cat, g in _groups(df):
        p = params_by_cat.get(str(cat).upper()) if cat else (next(iter(params_by_cat.values())) if len(params_by_cat) == 1 else None)
        if p is None:
            return Response(f"no {'serial' if is_serial else 'device'} params for category '{cat}' "
                            f"(loaded: {sorted(params_by_cat)}); set a mars_device_category column or PS5_INPUT_TYPE",
                            status=400, mimetype="text/plain")
        out.append(score(p, g))
    result = pd.concat(out, ignore_index=True) if out else pd.DataFrame()
    return _out(result)


if __name__ == "__main__":
    # local dev only; in SageMaker the `serve` launcher runs gunicorn
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "8080")))
