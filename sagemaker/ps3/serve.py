# =============================================================================
# serve.py -- BYOC entrypoint: wraps inference.py handlers into the SageMaker
# /ping + /invocations HTTP contract (Flask + gunicorn). This is the exact
# surface SageMaker health-checks; the local test hits it before any endpoint.
# =============================================================================
import os
from flask import Flask, request, Response
import inference

MODEL_DIR = os.environ.get("SM_MODEL_DIR", "/opt/ml/model")
app = Flask(__name__)
_MODEL = None


def _model():
    global _MODEL
    if _MODEL is None:
        _MODEL = inference.model_fn(MODEL_DIR)
    return _MODEL


@app.route("/ping", methods=["GET"])
def ping():
    try:
        _model()                       # 200 ONLY if the artifact loads (heads present)
        return Response(status=200)
    except Exception as e:
        return Response(f"model not loaded: {e}", status=503)


@app.route("/invocations", methods=["POST"])
def invocations():
    ctype = request.content_type or "application/json"
    accept = request.headers.get("Accept", "application/json")
    try:
        df = inference.input_fn(request.data.decode("utf-8"), ctype)
        preds = inference.predict_fn(df, _model())
        body, out_ctype = inference.output_fn(preds, accept)
        return Response(body, status=200, mimetype=out_ctype)
    except Exception as e:
        return Response(f'{{"error": "{type(e).__name__}: {e}"}}', status=400,
                        mimetype="application/json")


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
