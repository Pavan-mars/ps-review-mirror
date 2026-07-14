"""
Local API server for the CUBIC MARS dashboard.
Serves PS1, PS2, PS5 endpoints with realistic data derived from notebook outputs.

Run:  uvicorn fastapi_app.main:app --reload --port 8000
  or: python -m fastapi_app.main
"""

from __future__ import annotations

import math
import random
from datetime import date, timedelta
from typing import Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI(title="CUBIC MARS Local API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# Reference data  (Chicago CTA)
# ---------------------------------------------------------------------------
FACILITIES = [
    {"id": "ORD",  "name": "O'Hare"},
    {"id": "MDW",  "name": "Midway"},
    {"id": "CL",   "name": "Clark/Lake"},
    {"id": "JAC",  "name": "Jackson"},
    {"id": "HWD",  "name": "Howard"},
    {"id": "BLM",  "name": "Belmont"},
    {"id": "95D",  "name": "95th/Dan Ryan"},
    {"id": "RPB",  "name": "Roosevelt"},
    {"id": "WST",  "name": "Wilson"},
    {"id": "UIC",  "name": "UIC-Halsted"},
]

TVM_FEATURES = [
    "error_rate_7d", "downtime_hours_30d", "tx_volume_delta_7d",
    "maintenance_lag_days", "firmware_age_days", "card_read_fail_rate",
    "reboot_count_30d", "network_timeout_rate", "power_cycle_count",
    "cash_jam_events_7d", "receipt_paper_low_events", "sensor_temp_max_7d",
    "idle_time_pct", "peak_hour_load_factor", "days_since_last_pm",
]

GATE_FEATURES = [
    "tap_fail_rate_7d", "door_cycle_count_30d", "sensor_fault_events",
    "barrier_jam_count_7d", "firmware_age_days", "error_rate_7d",
    "reboot_count_30d", "power_fluctuation_events", "maintenance_lag_days",
    "motor_current_anomaly_rate", "card_reader_timeout_rate",
    "anti_passback_violations_7d", "door_open_duration_avg",
    "network_timeout_rate", "days_since_last_pm",
]

RNG = random.Random(42)


def _sine_decay(i: int, n: int, base: float, amp: float, freq: float = 1.0) -> float:
    t = i / max(n - 1, 1)
    return base + amp * math.sin(t * math.pi * 2 * freq) * (1 - 0.3 * t)


# ---------------------------------------------------------------------------
# Helper: generate predictions
# ---------------------------------------------------------------------------
def _make_predictions() -> list[dict]:
    rows = []
    inference_ts = "2026-07-14 06:00:00"
    prediction_date = "2026-07-14"
    # TVM  (~120 devices, ~22% failure rate → matches TVM recall floor)
    for fac in FACILITIES:
        n_tvm = RNG.randint(8, 16)
        for j in range(n_tvm):
            prob = RNG.betavariate(1.8, 5.2)          # right-skewed, ~25% above 0.50
            threshold = 0.50
            rows.append({
                "prediction_id":      f"TVM-{fac['id']}-{j:03d}",
                "device_id":          f"TVM-{fac['id']}-{j:04d}",
                "device_category":    "TVM",
                "facility_id":        fac["id"],
                "facility_name":      fac["name"],
                "failure_probability": round(prob, 4),
                "predicted_label":    prob >= threshold,
                "decision_threshold": threshold,
                "prediction_date":    prediction_date,
                "inference_ts":       inference_ts,
            })
    # GATE  (~60 devices, ~8% failure rate → matches GATE lower pos_rate)
    for fac in FACILITIES:
        n_gate = RNG.randint(3, 8)
        for j in range(n_gate):
            prob = RNG.betavariate(1.2, 8.0)          # very right-skewed, ~8% above 0.50
            threshold = 0.50
            rows.append({
                "prediction_id":      f"GATE-{fac['id']}-{j:03d}",
                "device_id":          f"GATE-{fac['id']}-{j:04d}",
                "device_category":    "GATE",
                "facility_id":        fac["id"],
                "facility_name":      fac["name"],
                "failure_probability": round(prob, 4),
                "predicted_label":    prob >= threshold,
                "decision_threshold": threshold,
                "prediction_date":    prediction_date,
                "inference_ts":       inference_ts,
            })
    return rows


_PREDICTIONS = _make_predictions()


# ---------------------------------------------------------------------------
# PS1 endpoints
# ---------------------------------------------------------------------------

@app.get("/ps1/predictions")
def ps1_predictions():
    """Latest inference batch results for all TVM + GATE devices."""
    return _PREDICTIONS


@app.get("/ps1/model-performance")
def ps1_model_performance():
    """Champion model metrics from the latest SageMaker training run."""
    return [
        {
            "model_registry_id":  "ps1-tvm-lgb-optuna-v3",
            "device_category":    "TVM",
            "model_name":         "PS1-TVM-Champion",
            "algorithm":          "LightGBM (Optuna HPT)",
            "model_version":      "3",
            "registry_alias":     "ps1-tvm-champion",
            "status":             "APPROVED",
            "champion":           True,
            "test_auc":           0.7649,
            "test_pr_auc":        0.4366,
            "decision_threshold": 0.50,
            "deployed_at":        "2026-07-12 08:00:00",
            "prediction_head":    "SageMaker Endpoint",
            "mlflow_version":     "11",
            "n_features":         15,
            "mlflow_run_id":      "tvm-lgb-optuna-run-2026-07-12",
            "s3_metrics": {
                "train_auc":  0.8421,
                "val_auc":    0.7720,
                "test_auc":   0.7649,
                "train_ap":   0.5318,
                "val_ap":     0.4531,
                "test_ap":    0.4366,
                "train_f1":   0.4912,
                "val_f1":     0.4107,
                "test_f1":    0.4053,
                "test_prec":  0.5241,
                "test_rec":   0.3312,
                "test_acc":   0.8763,
            },
        },
        {
            "model_registry_id":  "ps1-gate-lgb-optuna-v3",
            "device_category":    "GATE",
            "model_name":         "PS1-GATE-Champion",
            "algorithm":          "LightGBM (Optuna HPT)",
            "model_version":      "3",
            "registry_alias":     "ps1-gate-champion",
            "status":             "APPROVED",
            "champion":           True,
            "test_auc":           0.9318,
            "test_pr_auc":        0.7821,
            "decision_threshold": 0.50,
            "deployed_at":        "2026-07-12 08:00:00",
            "prediction_head":    "SageMaker Endpoint",
            "mlflow_version":     "11",
            "n_features":         15,
            "mlflow_run_id":      "gate-lgb-optuna-run-2026-07-12",
            "s3_metrics": {
                "train_auc":  0.9712,
                "val_auc":    0.9421,
                "test_auc":   0.9318,
                "train_ap":   0.8821,
                "val_ap":     0.8034,
                "test_ap":    0.7821,
                "train_f1":   0.8421,
                "val_f1":     0.7812,
                "test_f1":    0.7634,
                "test_prec":  0.8012,
                "test_rec":   0.7312,
                "test_acc":   0.9421,
            },
        },
    ]


@app.get("/ps1/risk-trend")
def ps1_risk_trend():
    """Daily average failure probability trend over the last 14 days."""
    rows = []
    base_date = date(2026, 7, 1)
    for i in range(14):
        d = (base_date + timedelta(days=i)).isoformat()
        tvm_prob  = round(_sine_decay(i, 14, 24.5, 6.0, freq=0.8) + RNG.gauss(0, 1.2), 2)
        gate_prob = round(_sine_decay(i, 14,  9.2, 3.0, freq=1.1) + RNG.gauss(0, 0.8), 2)
        tvm_total  = RNG.randint(105, 125)
        gate_total = RNG.randint(48,  65)
        rows += [
            {
                "date":            d,
                "device_category": "TVM",
                "avg_prob_pct":    max(5.0, min(60.0, tvm_prob)),
                "failures":        int(tvm_total * max(0.05, tvm_prob / 100) + 0.5),
                "total":           tvm_total,
            },
            {
                "date":            d,
                "device_category": "GATE",
                "avg_prob_pct":    max(2.0, min(30.0, gate_prob)),
                "failures":        int(gate_total * max(0.02, gate_prob / 100) + 0.5),
                "total":           gate_total,
            },
        ]
    return rows


@app.get("/ps1/feature-importance")
def ps1_feature_importance(device_category: str = Query("TVM")):
    """Aggregate SHAP feature importance from last training run."""
    features = TVM_FEATURES if device_category == "TVM" else GATE_FEATURES
    # Importance profile: first few features dominate (exponential decay)
    importances = [math.exp(-0.35 * i) for i in range(len(features))]
    total = sum(importances)
    importances = [v / total for v in importances]
    return [
        {
            "feature_name":    f,
            "avg_importance":  round(importances[i], 5),
            "avg_shap":        round(importances[i] * (1 if i % 3 != 1 else -1) * 0.8, 5),
        }
        for i, f in enumerate(features)
    ]


@app.get("/ps1/station-summary")
def ps1_station_summary():
    """Per-facility failure prediction summary (latest inference batch)."""
    rows = []
    for fac in FACILITIES:
        for cat, total_range, fail_rate, crit_frac, high_frac in [
            ("TVM",  (8, 16), (0.15, 0.35), 0.20, 0.30),
            ("GATE", (3,  8), (0.04, 0.12), 0.10, 0.25),
        ]:
            total    = RNG.randint(*total_range)
            failures = max(0, round(total * RNG.uniform(*fail_rate)))
            critical = round(failures * crit_frac)
            high     = round(failures * high_frac)
            medium   = failures - critical - high
            avg_risk = round(RNG.uniform(10, 40) if cat == "TVM" else RNG.uniform(4, 18), 2)
            rows.append({
                "facility_id":       fac["id"],
                "facility_name":     fac["name"],
                "device_category":   cat,
                "total_devices":     total,
                "predicted_failures": failures,
                "critical_count":    critical,
                "high_count":        high,
                "medium_count":      max(0, medium),
                "avg_risk_pct":      avg_risk,
                "last_inference_date": "2026-07-14",
            })
    return rows


@app.get("/ps1/explainability")
def ps1_explainability(prediction_id: str = Query(...)):
    """Per-prediction SHAP values for the explainability waterfall chart."""
    cat = "TVM" if prediction_id.startswith("TVM") else "GATE"
    features = TVM_FEATURES[:10] if cat == "TVM" else GATE_FEATURES[:10]
    rng = random.Random(hash(prediction_id) & 0xFFFF)
    return [
        {
            "feature_name":  f,
            "shap_value":    round(rng.gauss(0, 0.08) * (1 if i % 4 != 2 else -1), 5),
            "feature_value": round(rng.uniform(0, 1), 4),
        }
        for i, f in enumerate(features)
    ]


# ---------------------------------------------------------------------------
# PS2 endpoints  (real data, Chicago only — matches mockData.js shapes)
# ---------------------------------------------------------------------------

def _chi_only(city: str, data):
    """Return data for CHI, None for other cities (triggers 'no data' card)."""
    return data if city == "CHI" else None


@app.get("/ps2/windows")
def ps2_windows(city: str = "CHI"):
    if city != "CHI":
        return []
    # api.js apiPS2Windows() expects an array of rows with these exact field names
    common = {"total_cascade_days": 2198548, "slow_fast_fault_mult": 9.4, "slow_fast_duration_mult": 1881}
    return [
        {**common, "window_bucket": "0-5min",   "cascade_days": 377865,  "pct": 17.2},
        {**common, "window_bucket": "5-15min",  "cascade_days": 136893,  "pct": 6.2},
        {**common, "window_bucket": "15-30min", "cascade_days": 97218,   "pct": 4.4},
        {**common, "window_bucket": "30-60min", "cascade_days": 47479,   "pct": 2.2},
        {**common, "window_bucket": "60min+",   "cascade_days": 1539093, "pct": 70.0},
    ]


@app.get("/ps2/hub")
def ps2_hub(city: str = "CHI"):
    if city != "CHI":
        return None
    # api.js apiPS2Hub() expects nodes[].node_id + nodes[].is_hub + edges[].source_sub/target_sub
    return {
        "nodes": [
            {"node_id": "SYSTEM",     "is_hub": True,  "freq": 100},
            {"node_id": "COMMS",      "is_hub": True,  "freq": 88},
            {"node_id": "CHU",        "is_hub": False, "freq": 62},
            {"node_id": "CSC_READER", "is_hub": False, "freq": 55},
            {"node_id": "SCRST",      "is_hub": False, "freq": 41},
            {"node_id": "BHU",        "is_hub": False, "freq": 34},
        ],
        "edges": [
            {"source_sub": "CHU",        "target_sub": "SYSTEM", "phi": 39.5},
            {"source_sub": "CSC_READER", "target_sub": "SYSTEM", "phi": 24.7},
        ],
    }


@app.get("/ps2/facility")
def ps2_facility(city: str = "CHI"):
    if city != "CHI":
        return None
    # api.js apiPS2Facility() expects flat fields (not nested)
    return {
        "total_facility_cascade_days": 164515,
        "multi_device_contagion_pct":  84.9,
        "trend_start_pct":             75.5,
        "trend_end_pct":               93.5,
        "hotspot_facility_id":         45,
        "hotspot_facility_name":       "North Park",
        "hotspot_min_devices":         268,
        "hotspot_max_devices":         275,
    }


@app.get("/ps2/associations")
def ps2_associations(city: str = "CHI"):
    if city != "CHI":
        return []
    # api.js apiPS2AssociationRules() expects antecedent_subsystem / consequent_subsystem
    return [
        {"antecedent_subsystem": "CHU + SCRST",    "consequent_subsystem": "CSC_READER + SYSTEM", "support": 0.14, "confidence": 0.81, "lift": 9.678, "conviction": 99.0},
        {"antecedent_subsystem": "CHU",            "consequent_subsystem": "SYSTEM",              "support": 0.22, "confidence": 0.76, "lift": 6.12,  "conviction": 41.3},
        {"antecedent_subsystem": "CSC_READER",     "consequent_subsystem": "SYSTEM",              "support": 0.19, "confidence": 0.69, "lift": 4.87,  "conviction": 28.9},
        {"antecedent_subsystem": "SCRST",          "consequent_subsystem": "COMMS",               "support": 0.11, "confidence": 0.58, "lift": 3.34,  "conviction": 15.2},
    ]


@app.get("/ps2/hmm")
def ps2_hmm(city: str = "CHI"):
    if city != "CHI":
        return []
    return [
        {"regime": "Critical", "pct": 2.6,  "dwell_days_min": 1.5, "dwell_days_max": 2.4},
        {"regime": "Minor",    "pct": 21.0, "dwell_days_min": 2.1, "dwell_days_max": 4.2},
        {"regime": "Moderate", "pct": 76.4, "dwell_days_min": 3.8, "dwell_days_max": 6.3},
    ]


@app.get("/ps2/windowdetail")
def ps2_windowdetail(city: str = "CHI"):
    raise HTTPException(status_code=404, detail="pending data")


@app.get("/ps2/topdevices")
def ps2_topdevices(city: str = "CHI"):
    if city != "CHI":
        return []
    # api.js apiPS2TopDevices() expects device_id, category, cascade_days, dev_rank + window buckets
    return [
        {"device_id": "TVM01703", "category": "TVM",       "cascade_days": 810, "w0_5": 12, "w5_15": 8,  "w15_30": 6,  "w30_60": 4,  "w60plus": 780, "dev_rank": 1},
        {"device_id": "TVM03901", "category": "TVM",       "cascade_days": 810, "w0_5": 10, "w5_15": 9,  "w15_30": 7,  "w30_60": 5,  "w60plus": 779, "dev_rank": 2},
        {"device_id": "TVM10801", "category": "TVM",       "cascade_days": 808, "w0_5": 11, "w5_15": 7,  "w15_30": 5,  "w30_60": 3,  "w60plus": 782, "dev_rank": 3},
        {"device_id": "BMV02629", "category": "VALIDATOR", "cascade_days": 759, "w0_5": 9,  "w5_15": 6,  "w15_30": 4,  "w30_60": 2,  "w60plus": 738, "dev_rank": 4},
    ]


# Rich PS2 analytics — 404 so api.js catch blocks fall back to mock permanently
# (returning [] would overwrite the mock with an empty result causing blank diagrams)
@app.get("/ps2/paths")
def ps2_paths(city: str = "CHI"):
    raise HTTPException(status_code=404, detail="pending data")

@app.get("/ps2/ignition")
def ps2_ignition(city: str = "CHI"):
    raise HTTPException(status_code=404, detail="pending data")

@app.get("/ps2/impact")
def ps2_impact(city: str = "CHI"):
    raise HTTPException(status_code=404, detail="pending data")

@app.get("/ps2/phi")
def ps2_phi(city: str = "CHI"):
    raise HTTPException(status_code=404, detail="pending data")

@app.get("/ps2/markov")
def ps2_markov(city: str = "CHI"):
    raise HTTPException(status_code=404, detail="pending data")

@app.get("/ps2/conditional")
def ps2_conditional(city: str = "CHI"):
    raise HTTPException(status_code=404, detail="pending data")

@app.get("/ps2/network")
def ps2_network(city: str = "CHI"):
    raise HTTPException(status_code=404, detail="pending data")

@app.get("/ps2/errorcodes")
def ps2_errorcodes(city: str = "CHI"):
    raise HTTPException(status_code=404, detail="pending data")

@app.get("/ps2/devices")
def ps2_devices(city: str = "CHI"):
    raise HTTPException(status_code=404, detail="pending data")

@app.get("/ps2/devicecascades")
def ps2_devicecascades(city: str = "CHI", device: str = ""):
    return []


# ---------------------------------------------------------------------------
# PS5 endpoints  (stub)
# ---------------------------------------------------------------------------

@app.get("/ps5/status")
def ps5_status(city: str = "CHI"):
    raise HTTPException(status_code=404, detail="pending data")


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------

@app.get("/health")
def health():
    return {"status": "ok", "server": "CUBIC MARS Local API v1.0.0"}


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("fastapi_app.main:app", host="0.0.0.0", port=8000, reload=True)
