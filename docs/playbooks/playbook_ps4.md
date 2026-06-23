# Playbook: PS4 — Anomaly Detection (Hourly, 3-Signal Ensemble)

## 1. Problem Statement

Given a (device_id, hour_bucket) record from the Chicago Ventra fare-collection fleet, detect whether
that hour is **operationally anomalous** — a condition requiring investigation or escalation before
a full device failure occurs.

**Target:** `ensemble_anomaly_flag` (binary 0/1)
- **1 = Anomalous:** ≥ 2 of 3 independent signals fired in that hour
- **0 = Normal:** 0 or 1 signals fired

**3-Signal logic:**

| Signal | Column | Condition |
|--------|--------|-----------|
| Event-rate anomaly | `event_rate_anomaly` | Hourly event count deviates > 2σ from rolling baseline |
| Metric anomaly | `metric_anomaly` | metric_800 deviates > 2σ from device baseline |
| Reject-rate anomaly | `reject_rate_anomaly` | Daily tap reject rate exceeds 5% |

**Why it matters:** Early-hour anomaly detection lets operations dispatch technicians *before* a
device goes fully out-of-service, reducing passenger-impact minutes and SLA penalties.

**Grain:** One row per (DEVICE_ID, hour_bucket).
**Row count:** 28,001 rows (March 2025).
**Class imbalance:** Only 8 anomalous hours (0.03%) — use `scale_pos_weight` or `class_weight='balanced'`.

---

## 2. Data Requirements

| Item | Detail |
|------|--------|
| Gold table | `device_ps4_hourly.parquet` |
| Row count | 28,001 |
| Grain | (DEVICE_ID, hour_bucket) |
| Date range | 2025-03-01 00:00 → 2025-03-31 23:00 |
| Device categories | SAG, TVM, HBG, RMV, FBX, CSC READER, DCU, BMV, FMVD, RVG |
| Unique devices | ~300 |

**Gold table path:**
```
D:\Sathish\Chicago_Synthetic data\chicago_oracle_real_data\gold\device_ps4_hourly.parquet
```

**Column availability notes:**
- `metric_anomaly` is **not present** in older parquet snapshots — handle with `if 'metric_anomaly' in df.columns`

---

## 3. Feature Reference

### Raw Signal Columns (binary flags)
| Feature | Type | Description |
|---------|------|-------------|
| `event_rate_anomaly` | int32 (0/1) | Event count deviated > 2σ from baseline mean |
| `metric_anomaly` | int32 (0/1) | metric_800 deviated > 2σ from device baseline |
| `reject_rate_anomaly` | int32 (0/1) | Daily tap reject rate above 5% threshold |
| `anomaly_signal_count` | int32 | Count of signals fired (0–3) |

### Temporal Features
| Feature | Type | Description |
|---------|------|-------------|
| `hour_bucket` | datetime | Start of the hour (temporal key for sorting) |
| `transit_day` | date | Date of the transit service day |
| `hour_of_day` | int32 | 0–23 |
| `day_of_week` | int32 | 0=Mon … 6=Sun |

### Operational Event Counters (per hour)
| Feature | Type | Description |
|---------|------|-------------|
| `event_count_hour` | int64 | Total events recorded in this hour |
| `critical_count_hour` | float64 | Critical-severity events |
| `oos_count_hour` | float64 | Out-of-service events |
| `bhu_count_hour` | float64 | Bill Handling Unit events |
| `chu_count_hour` | float64 | Coin Handling Unit events |
| `printer_count_hour` | float64 | Printer events |
| `gate_mech_count_hour` | float64 | Gate mechanical events |
| `csc_reader_count_hour` | float64 | CSC Reader events |
| `comms_count_hour` | float64 | Communications events |
| `scrst_count_hour` | float64 | Screen/restart events |
| `max_severity_hour` | int32 | Maximum severity code seen in this hour |

### Baseline Statistics
| Feature | Type | Description |
|---------|------|-------------|
| `baseline_mean_events` | float64 | Rolling 7-day mean event count for this device |
| `baseline_stddev_events` | float64 | Rolling 7-day std-dev of event count |

### Tap Reject Metrics
| Feature | Type | Description |
|---------|------|-------------|
| `tap_reject_rate_daily` | float64 | Daily tap transaction reject rate (0.0–1.0) |
| `avg_tap_timing_ms` | float64 | Average tap transaction processing time (ms) |

### Device Context
| Feature | Type | Description |
|---------|------|-------------|
| `DEVICE_ID` | object | Ventra device identifier |
| `mars_device_category` | object | TVM, SAG, HBG, etc. (label-encode → `device_cat_enc`) |
| `FACILITY_NAME` | object | Station/facility name |
| `OPERATOR_NAME` | object | Operating agency (CTA, Pace, etc.) |

### Engineered Features (notebook-derived)
| Feature | Formula | Description |
|---------|---------|-------------|
| `event_zscore` | `(event_count_hour - baseline_mean) / baseline_std` | Standard deviations from baseline |
| `hardware_event_density` | `sum(bhu + chu + printer + gate_mech + csc_reader)` | Total hardware fault events |
| `comms_event_density` | `comms_count + scrst_count` | Communications/restart events |
| `hour_sin` / `hour_cos` | `sin/cos(2π × hour_of_day / 24)` | Cyclical hour encoding |
| `dow_sin` / `dow_cos` | `sin/cos(2π × day_of_week / 7)` | Cyclical day-of-week encoding |
| `critical_share_hour` | `critical_count / max(event_count, 1)` | Fraction of events that are critical |
| `oos_share_hour` | `oos_count / max(event_count, 1)` | Fraction of events that are OOS |

### Target
| Column | Type | Description |
|--------|------|-------------|
| `ensemble_anomaly_flag` | int32 (0/1) | **Target:** 1 if ≥2 signals fired, else 0 |

---

## 4. Model Architecture

Two complementary approaches:

### Model A: IsolationForest (Unsupervised)
```
All hourly features (no label required)
        │
  IsolationForest(n_estimators=200, contamination=observed_rate)
        │
  anomaly score  (-score_samples → higher = more anomalous)
        │
  threshold cut → binary flag
```
- Useful for **ranking** device-hours even when no true labels exist in the test period
- `contamination` = observed anomaly rate (floor at 0.0003 to avoid zero)

### Model B: LightGBM Classifier (Supervised)
```
Labelled hourly features (ensemble_anomaly_flag known)
        │
  LightGBMClassifier(scale_pos_weight = neg/pos ratio, n_estimators=500)
        │
  predict_proba → P(anomaly) per device-hour
        │
  threshold at 0.5 (or tuned for Recall/Precision trade-off)
```
- `scale_pos_weight` compensates for extreme class imbalance
- Outputs probability scores for downstream ranking/alerting

### Hybrid Operational Logic
```
signal_count = event_rate_anomaly + metric_anomaly + reject_rate_anomaly

if signal_count >= 2:          → CONFIRMED ANOMALY (rule-based)
elif lgbm_prob > threshold:    → PROBABLE ANOMALY  (ML-based watch-list)
elif iso_score > percentile95: → ELEVATED RISK     (unsupervised watch-list)
else:                          → NORMAL
```

---

## 5. Training Protocol

1. **Load and sort** `device_ps4_hourly.parquet` by `hour_bucket` (ascending)
2. **Handle missing metric_anomaly:** `if 'metric_anomaly' not in df.columns: df['metric_anomaly'] = 0`
3. **Feature engineering:** compute `event_zscore`, density features, cyclical encodings
5. **Fill NaNs with 0** on all numeric feature columns
6. **LabelEncode** `mars_device_category` → `device_cat_enc`
7. **TimeSeriesSplit(n_splits=5):** use final fold as hold-out
8. **IsolationForest:** fit on train rows; score all rows
9. **LightGBM:** compute `scale_pos_weight = (train negatives) / max(train positives, 1)`, then fit
10. **Evaluate** on hold-out test fold

### Critical note on class imbalance
With only 8 true anomalies in 28,001 rows, the test fold may contain 0 anomalous rows.
In this case:
- IsolationForest: evaluate using anomaly score percentile alignment with full-data ground truth
- LightGBM: report Average Precision (PR-AUC) on the full dataset; cross-validate on all 5 folds

---

## 6. Evaluation Criteria

### Primary Metrics
| Metric | Target | Rationale |
|--------|--------|-----------|
| Recall (anomaly class) | ≥ 0.85 | Missing a true anomaly has high operational cost |
| Precision (anomaly class) | ≥ 0.20 | Acceptable false-positive rate for watch-list |
| Average Precision (PR-AUC) | ≥ 0.50 | Better than AUC-ROC for imbalanced data |
| F1 (anomaly class) | ≥ 0.30 | Harmonic mean at 0.5 threshold |

> **Note:** With only 8 true anomalies in 28,001 rows, any single-fold test set may have 0 positives.
> Use full-dataset PR-AUC or 5-fold CV as the primary evaluation, not a single train/test split.

### Rule-Based Baseline
The ensemble logic (signal_count ≥ 2) is already a perfect classifier on its own definition.
Use it as the **oracle** and evaluate ML models on their ability to identify hours with
`anomaly_signal_count = 1` that are likely to escalate to `signal_count = 2`.

### IsolationForest Calibration Check
- Verify that the top 0.03% of anomaly scores correspond to the 8 true anomalies
- If not, adjust `contamination` parameter

---

## 7. How to Run

### Prerequisites
```
Python venv: D:\Sathish\ML_Device_Telemetry\venv\Scripts\python.exe
Packages: pandas, numpy, lightgbm, scikit-learn, shap, matplotlib, seaborn
```

### Steps
```powershell
D:\Sathish\ML_Device_Telemetry\venv\Scripts\activate
jupyter notebook "D:\Sathish\Chicago_Synthetic data\chicago_oracle_real_data\notebooks\Chicago_PS4_Anomaly_Detection.ipynb"
```

Run all cells top-to-bottom. Expected runtime: 3–6 minutes (28K rows, IsolationForest + LightGBM + SHAP).

---

## 8. How to Interpret Results

### Anomaly Score Tiers
| Tier | Condition | Action |
|------|-----------|--------|
| **Confirmed** | `ensemble_anomaly_flag = 1` (rule-based) | Immediate dispatch |
| **Probable** | `lgbm_anomaly_prob > 0.5` | Add to watch-list; monitor next 2 hours |
| **Elevated** | `iso_anomaly_score > 95th percentile` | Log for pattern analysis |
| **Normal** | All other | No action |

### SHAP Interpretation
- **`event_zscore` high SHAP:** This hour's event count is far from the device's normal baseline — most important predictor
- **`reject_rate_anomaly = 1` high SHAP:** Payment system under stress; check card reader or cash module
- **`critical_share_hour` high SHAP:** Critical events are a disproportionate share of hourly activity — hardware fault likely
- **`oos_count_hour` high SHAP:** Multiple out-of-service events in a single hour — imminent full OOS risk

### Per-Device Heatmap (device_type × hour_of_day)
- Dark cells (high signal count) during off-peak hours (0–5am) indicate **maintenance / reboot events** rather than genuine failures
- Dark cells during peak hours (7–9am, 4–6pm) are **high-priority** — passenger-impact risk is greatest
- Consistent elevated rows across all hours for a device type indicate **systemic hardware issues** specific to that type

### Cross-Device Comparison
- Compare `iso_anomaly_score` box plots by device type to identify **device categories with structural elevated risk**
- Device categories with median score > 75th percentile of fleet = candidates for preventive maintenance programme

---

## 9. Known Limitations / Data Gaps

| Limitation | Impact | Mitigation |
|-----------|--------|-----------|
| Only 8 true anomalies in 28,001 rows | Supervised models may not generalise; single test fold may have 0 positives | Use full-data PR-AUC; treat rule-based signal count as ground truth |
| `metric_anomaly` absent in older parquet snapshots | Ensemble falls back to 2-signal mode | Set to 0 if column absent: `df.get('metric_anomaly', 0)` |
| Synthetic data: anomalies are algorithmically placed | Real data will have different anomaly patterns | Recalibrate baseline_mean/stddev parameters on first 90 days of live data |
| Baseline statistics have many NaNs (new devices) | `event_zscore` = 0 for devices without baseline history | Fill with 0; flag new devices (< 7 days in service) separately |
| March 2025 only (1 month) | No seasonal patterns captured | Extend to 12 months for reliable hour-of-day / day-of-week patterns |
| Single DEVICE_ID time series not aligned | Multiple devices share the same hour_bucket | Do not use lag features across device rows; device-level lag requires groupby |

---

## 10. Refresh Cadence

| Action | Frequency | Notes |
|--------|-----------|-------|
| Score new device-hours | Hourly (near real-time) | Stream from silver pipeline; score via REST endpoint |
| Recompute `baseline_mean_events` / `baseline_stddev_events` | Daily (7-day rolling window) | Managed in silver SQL layer |
| Retrain IsolationForest | Monthly | Append last 30 days; keep 90-day training window |
| Retrain LightGBM | Monthly (or when ≥ 10 new confirmed anomalies accumulate) | More data = better precision/recall calibration |
| Review `reject_rate_anomaly` threshold | Quarterly | Threshold should track seasonal payment rejection baseline |
| Gold table rebuild | After each silver pipeline run | PS4 gold depends on `device_event_enriched`, `metric_daily`, and `tap_event_daily` silver tables |
