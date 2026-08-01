# =============================================================================
# PS3 inference handler -- serves the severity and root-cause heads produced by
# PS3_RootCause_Severity_SageMaker_RDS_Dashboard_Ready.ipynb.
#
# SageMaker framework-handler contract: model_fn -> input_fn -> predict_fn ->
# output_fn. serve.py wraps these into /ping + /invocations for BYOC.
#
# ARTIFACT LAYOUT (tar.gz unpacked into model_dir). The notebook's
# fit_final_serving_bundle() writes, per device category and head:
#     {category}/{head}/serving_bundle.joblib
#     {category}/{head}/champion_summary.json      (optional, carries gate_pass)
# Multi-model endpoint: each tar holds ONE {category}/{head} pair.
# Single endpoint: one tar may hold several; all are loaded and keyed.
#
# BUNDLE KEYS (exact, from the notebook):
#   model, preprocessor, numeric_features, categorical_features, features,
#   label_encoder, head, device_category, target, feature_time_contract
#
# 2026-07-26 -- this file was rewritten. The previous version expected the OLD
# bundle shape (`prep`, `category`, flat `features`, `*_bundle.joblib`) and
# raised KeyError in model_fn, which made /ping return 503 and the endpoint
# never reach InService. It also carried the label-keyed SEVERITY_COLLAPSE that
# silently marked 100% of incidents MAJOR. Both are fixed here.
# =============================================================================
import glob
import json
import os

import joblib
import numpy as np
import pandas as pd

# Keyed on failure_level_label CODES -- never on human-readable labels, and
# never defaulted to MAJOR. An unmapped code is reported as UNKNOWN so a
# downstream consumer can see it rather than silently counting it as non-critical.
SEVERITY_COLLAPSE = {
    "PURCHASE_CARD": "MAJOR",
    "PURCHASE_PRODUCT": "MAJOR",
    "NONPAYMENT": "MAJOR",
    "ALL_PURCHASE": "CRITICAL",
    "ALL_FUNCTIONS": "CRITICAL",
    "BUS_READER": "CRITICAL",
    "BUS_READER_ASSEMBLY": "CRITICAL",
}
UNKNOWN_SEVERITY = "UNKNOWN"

# A head whose promotion gate failed must not be served. GATE severity is the
# known case (macro-F1 0.4978 vs 0.55 floor, AUC 0.4358). Set
# PS3_SERVE_FAILED_HEADS=1 only for deliberate debugging.
SERVE_FAILED_HEADS = os.environ.get("PS3_SERVE_FAILED_HEADS", "0") == "1"


def collapse_severity(code):
    return SEVERITY_COLLAPSE.get(str(code).strip().upper(), UNKNOWN_SEVERITY)


def _gate_pass_for(bundle_path):
    """Read the sibling champion_summary.json if present. Absent -> allow."""
    summary = os.path.join(os.path.dirname(bundle_path), "champion_summary.json")
    if not os.path.exists(summary):
        return True, "no champion_summary.json alongside bundle"
    try:
        with open(summary, encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception as exc:                                   # noqa: BLE001
        return True, f"champion_summary unreadable ({exc}); allowing"
    if "gate_pass" not in data:
        return True, "champion_summary has no gate_pass"
    ok = bool(data["gate_pass"])
    return ok, (
        f"gate_pass={ok} f1_macro={data.get('test_f1_macro')} "
        f"floor={data.get('macro_f1_floor')}"
    )


def model_fn(model_dir):
    """Load every serving_bundle.joblib under model_dir, keyed '<CATEGORY>/<head>'."""
    paths = sorted(glob.glob(os.path.join(model_dir, "**", "serving_bundle.joblib"),
                             recursive=True))
    if not paths:
        raise RuntimeError(
            f"no serving_bundle.joblib under {model_dir}. Expected "
            f"{{category}}/{{head}}/serving_bundle.joblib from the PS3 notebook."
        )
    bundles, skipped = {}, []
    for path in paths:
        bundle = joblib.load(path)
        missing = [k for k in ("model", "preprocessor", "label_encoder", "head",
                               "device_category") if k not in bundle]
        if missing:
            raise RuntimeError(f"{path}: bundle missing required keys {missing}")

        ok, why = _gate_pass_for(path)
        cat = str(bundle["device_category"]).upper()
        head = str(bundle["head"]).lower()
        key = f"{cat}/{head}"
        if not ok and not SERVE_FAILED_HEADS:
            skipped.append(f"{key} ({why})")
            continue
        bundle["_key"] = key
        bundles[key] = bundle
        print(f"[model_fn] loaded {key}: target={bundle.get('target')} "
              f"n_features={len(bundle.get('features') or [])} | {why}")

    for s in skipped:
        print(f"[model_fn] SKIPPED (failed promotion gate, not served): {s}")
    if not bundles:
        raise RuntimeError(
            "every bundle failed its promotion gate; refusing to serve. "
            f"Skipped: {skipped}"
        )
    return bundles


def input_fn(request_body, content_type="application/json"):
    """JSON records / {'instances': [...]} / CSV -> DataFrame."""
    if isinstance(request_body, (bytes, bytearray)):
        request_body = request_body.decode("utf-8")
    ctype = (content_type or "application/json").split(";")[0].strip()
    if ctype in ("application/json", "application/jsonlines"):
        payload = json.loads(request_body)
        if isinstance(payload, dict) and "instances" in payload:
            payload = payload["instances"]
        if isinstance(payload, dict):
            payload = [payload]
        if not isinstance(payload, list):
            raise ValueError("JSON body must be an object, a list, or {'instances': [...]}")
        return pd.DataFrame(payload)
    if ctype in ("text/csv", "application/csv"):
        from io import StringIO
        return pd.read_csv(StringIO(request_body))
    raise ValueError(f"unsupported content_type: {content_type}")


def _frame_for(bundle, df):
    """Rebuild the exact training design matrix: numeric coerced, categoricals as str.

    The previous handler coerced EVERY column with pd.to_numeric, which turned
    categorical features into NaN and silently degraded predictions.
    """
    num = list(bundle.get("numeric_features") or [])
    cat = list(bundle.get("categorical_features") or [])
    if not num and not cat:
        num = list(bundle.get("features") or [])

    out = pd.DataFrame(index=df.index)
    for c in num:
        out[c] = pd.to_numeric(df[c], errors="coerce") if c in df.columns else np.nan
    for c in cat:
        out[c] = df[c].astype("object").where(df[c].notna(), None) if c in df.columns else None
    ordered = list(bundle.get("features") or (num + cat))
    return out.reindex(columns=[c for c in ordered if c in out.columns])


def _predict_head(bundle, df):
    X = _frame_for(bundle, df)
    Xt = bundle["preprocessor"].transform(X)
    model = bundle["model"]
    if hasattr(model, "predict_proba"):
        proba = np.asarray(model.predict_proba(Xt))
        idx = proba.argmax(axis=1)
        conf = proba.max(axis=1)
    else:
        idx = np.asarray(model.predict(Xt)).astype(int)
        conf = np.full(len(idx), np.nan)
    labels = bundle["label_encoder"].inverse_transform(idx)
    return labels, conf


def predict_fn(df, bundles):
    """One record per input row, scored ONLY by bundles for that row's device category.

    A row is never scored by another device type's model. When several categories
    are loaded in one artifact and a row's category cannot be resolved, the row is
    returned unscored with a reason instead of being silently mis-scored.
    """
    n = len(df)
    out = [{} for _ in range(n)]
    for idcol in ("availability_event_id", "device_id", "matched_serial_nbr",
                  "mars_device_category"):
        if idcol in df.columns:
            values = df[idcol].tolist()
            for i in range(n):
                out[i][idcol] = values[i]

    loaded_cats = {b["device_category"].upper() for b in bundles.values()}
    if "mars_device_category" in df.columns:
        row_cat = df["mars_device_category"].astype(str).str.upper().tolist()
    elif len(loaded_cats) == 1:
        row_cat = [next(iter(loaded_cats))] * n          # unambiguous: MME, one category
    else:
        row_cat = [None] * n

    for i in range(n):
        if row_cat[i] is None:
            out[i]["scored"] = False
            out[i]["reason"] = ("mars_device_category absent and artifact serves "
                                f"{sorted(loaded_cats)}; cannot resolve which model applies")
        elif row_cat[i] not in loaded_cats:
            out[i]["scored"] = False
            out[i]["reason"] = (f"no served model for device category {row_cat[i]!r} "
                                f"(available: {sorted(loaded_cats)})")

    for key, bundle in bundles.items():
        cat = bundle["device_category"].upper()
        head = bundle["head"].lower()
        mask = np.array([rc == cat for rc in row_cat], dtype=bool)
        if not mask.any():
            continue
        sub = df.loc[mask]
        labels, conf = _predict_head(bundle, sub)
        for j, i in enumerate(np.flatnonzero(mask)):
            label = str(labels[j])
            c = None if conf[j] is None or (isinstance(conf[j], float) and np.isnan(conf[j])) \
                else round(float(conf[j]), 5)
            if head == "severity":
                out[i]["pred_severity"] = label
                out[i]["pred_severity_conf"] = c
                out[i]["pred_severity_collapsed"] = collapse_severity(label)
            elif head == "root_cause":
                out[i]["pred_component"] = label
                out[i]["pred_component_conf"] = c
            else:
                out[i][f"pred_{head}"] = label
                out[i][f"pred_{head}_conf"] = c
            out[i]["scored"] = True
            out[i].setdefault("served_by", []).append(key)
    return out


def output_fn(prediction, accept="application/json"):
    return json.dumps({"predictions": prediction}, default=str), "application/json"
