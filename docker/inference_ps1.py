import os, io, csv, glob, sys
import numpy as np
import joblib

try:
    from sklearn.base import BaseEstimator, ClassifierMixin
    from catboost import CatBoostClassifier
    class _CBWrapper(BaseEstimator, ClassifierMixin):
        def __init__(self, iterations=300, learning_rate=0.05,
                     class_weights=None, random_seed=42, verbose=0):
            self.iterations=iterations; self.learning_rate=learning_rate
            self.class_weights=class_weights; self.random_seed=random_seed
            self.verbose=verbose
        def fit(self, X, y, **kw):
            self._cb = CatBoostClassifier(
                iterations=self.iterations, learning_rate=self.learning_rate,
                class_weights=self.class_weights, random_seed=self.random_seed,
                verbose=self.verbose)
            self._cb.fit(X, y, **kw)
            self.classes_ = np.array([0, 1]); return self
        def predict(self, X): return self._cb.predict(X).flatten().astype(int)
        def predict_proba(self, X): return self._cb.predict_proba(X)
    sys.modules['__main__']._CBWrapper = _CBWrapper
except ImportError:
    pass


def model_fn(model_dir):
    try:
        import lightgbm
    except ImportError:
        pass
    hits = glob.glob(os.path.join(model_dir, '*_champion.joblib'))
    if not hits:
        raise FileNotFoundError(
            'No *_champion.joblib in {}: {}'.format(model_dir, os.listdir(model_dir)))
    model = joblib.load(hits[0])
    if isinstance(model, dict) and 'model' in model:
        model = model['model']
    return model


def input_fn(request_body, content_type):
    if content_type == 'text/csv':
        return np.array(list(csv.reader(io.StringIO(request_body))), dtype=float)
    if content_type == 'application/json':
        import json as _j
        return np.array(_j.loads(request_body), dtype=float)
    raise ValueError('Unsupported content type: ' + content_type)


def predict_fn(input_data, model):
    proba = model.predict_proba(input_data)
    return proba[:, 1] if proba.ndim == 2 else proba


def output_fn(prediction, accept):
    return '\n'.join('{:.6f}'.format(p) for p in prediction), 'text/csv'


if __name__ == '__main__':
    from flask import Flask, request, Response

    app = Flask(__name__)
    _model = model_fn('/opt/ml/model')

    @app.route('/ping', methods=['GET'])
    def ping():
        return Response('', status=200)

    @app.route('/invocations', methods=['POST'])
    def invoke():
        ct = request.content_type or 'text/csv'
        body = request.get_data(as_text=True)
        data = input_fn(body, ct)
        preds = predict_fn(data, _model)
        accept = request.accept_mimetypes.best_match(
            ['text/csv', 'application/json']) or 'text/csv'
        result, out_ct = output_fn(preds, accept)
        return Response(result, content_type=out_ct)

    app.run(host='0.0.0.0', port=8080)
