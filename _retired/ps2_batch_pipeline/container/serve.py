# =============================================================================
# serve.py -- SageMaker BYOC inference for the PS2 cascade scorer (Batch
# Transform). Implements GET /ping and POST /invocations. Mirrors
# sagemaker/ps5/batch/container/serve.py's shape exactly.
#
# Loads params for all three scoreable families from /opt/ml/model at startup:
#   *_markov_params.json      (one per category: TVM/GATE/VALIDATOR)
#   ps2_hmm_params.json       (single fleet-wide model)
#   ps2_recurrence_params.json (single fleet-wide thresholds)
#
# Routes each request by a required 'family' column value (markov|hmm|
# recurrence) -- unlike PS5 (which auto-detects device vs serial by columns),
# PS2's three families take different input shapes, so the caller states which
# one it wants. run_batch_transform.py sends one Transform job per family.
# =============================================================================
import os, io, glob, json

import pandas as pd
from flask import Flask, request, Response

from ps2_scoring_core import score, read_table, EVENT_DEF_FALLBACK

MODEL_DIR = os.environ.get("SM_MODEL_DIR", "/opt/ml/model")

app = Flask(__name__)


def _load_params():
    markov = {}
    for p in glob.glob(os.path.join(MODEL_DIR, "*_markov_params.json")):
        d = json.load(open(p)); markov[str(d.get("category", "")).upper()] = d
    hmm_path = os.path.join(MODEL_DIR, "ps2_hmm_params.json")
    hmm = json.load(open(hmm_path)) if os.path.exists(hmm_path) else None
    rec_path = os.path.join(MODEL_DIR, "ps2_recurrence_params.json")
    rec = json.load(open(rec_path)) if os.path.exists(rec_path) else None
    return markov, hmm, rec


MARKOV_PARAMS, HMM_PARAMS, RECURRENCE_PARAMS = _load_params()
print(f"[serve] loaded markov params for {sorted(MARKOV_PARAMS)} | "
      f"hmm={'yes' if HMM_PARAMS else 'no'} | recurrence={'yes' if RECURRENCE_PARAMS else 'no'} | "
      f"event={EVENT_DEF_FALLBACK}")


def _out(df):
    buf = io.BytesIO(); df.to_parquet(buf, index=False)
    return Response(buf.getvalue(), status=200, mimetype="application/x-parquet")


@app.route("/ping", methods=["GET"])
def ping():
    healthy = bool(MARKOV_PARAMS) or HMM_PARAMS is not None or RECURRENCE_PARAMS is not None
    return Response("ok" if healthy else "no params loaded", status=200 if healthy else 500, mimetype="text/plain")


@app.route("/invocations", methods=["POST"])
def invocations():
    try:
        df = read_table(request.get_data())
    except Exception as e:
        return Response(f"cannot read payload: {e}", status=400, mimetype="text/plain")
    if df is None or not len(df):
        return Response("empty payload", status=400, mimetype="text/plain")

    family = os.environ.get("PS2_FAMILY", "").lower() or (
        str(df["family"].iloc[0]).lower() if "family" in df.columns else "")
    if family not in ("markov", "hmm", "recurrence"):
        return Response(f"missing/invalid 'family' column or PS2_FAMILY env (got '{family}'); "
                        f"expected markov|hmm|recurrence", status=400, mimetype="text/plain")

    out = []
    if family == "markov":
        if "mars_device_category" not in df.columns:
            return Response("markov scoring requires a mars_device_category column", status=400, mimetype="text/plain")
        for cat, g in df.groupby(df["mars_device_category"].astype(str).str.upper()):
            p = MARKOV_PARAMS.get(cat)
            if p is None:
                return Response(f"no markov params for category '{cat}' (loaded: {sorted(MARKOV_PARAMS)})",
                                status=400, mimetype="text/plain")
            out.append(score("markov", p, g))
    elif family == "hmm":
        if HMM_PARAMS is None:
            return Response("no hmm params loaded", status=400, mimetype="text/plain")
        out.append(score("hmm", HMM_PARAMS, df))
    else:
        if RECURRENCE_PARAMS is None:
            return Response("no recurrence params loaded", status=400, mimetype="text/plain")
        out.append(score("recurrence", RECURRENCE_PARAMS, df))

    result = pd.concat(out, ignore_index=True) if out else pd.DataFrame()
    return _out(result)


if __name__ == "__main__":
    # local dev only; in SageMaker the `serve` launcher runs gunicorn
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "8080")))
