# Playbook: PS1 — Predictive Failure (7-Day Binary)

## 1. Problem Statement

Predict whether a fare-collection device will fail within the next 7 calendar days, using daily telemetry aggregates. A positive label (`will_fail_7d = 1`) means at least one availability event was recorded for that device in the 7-day window following `transit_day`.

**Why it matters:** Early identification of at-risk devices (TVM, GATE, READER, VALIDATOR) enables preventive maintenance scheduling, reducing unplanned out-of-service time and improving passenger throughput at Chicago transit stations.

**Grain:** One row per (DEVICE_ID, transit_day).  
**Target:** `will_fail_7d` — binary, 0 = no failure in next 7 days, 1 = failure expected.  
**Class distribution (gold data):** ~90% positive, ~10% negative (heavily imbalanced).

---

## 2. Data Requirements

| Item | Detail |
|------|--------|
| Gold table | `device_ps1_daily.parquet` |
| Row count | 8,924 (gold synthetic) |
| Grain | (DEVICE_ID, transit_day) |
| Date range | 2025-03-01 → 2026-04-30 (approx) |
| Required columns | See Feature Reference below |
| Forbidden at prediction time | Any column computed from future events (e.g. next-day counts) |

**Gold table path:**
```
D:\Sathish\Chicago_Synthetic data\chicago_oracle_real_data\gold\device_ps1_daily.parquet
```

---

## 3. Feature Reference

### Core Event Counters (same-day)
| Feature | Description |
|---------|-------------|
| `critical_events` | Count of CRITICAL-severity events on transit_day |
| `oos_event_count` | Count of Out-of-Service events on transit_day |
| `bhu_events` | Bill Handling Unit events |
| `chu_events` | Coin Handling Unit events |
| `printer_events` | Printer/ticket dispenser events |
| `gate_mech_events` | Gate mechanical events |
| `csc_reader_events` | CSC reader / contactless events |
| `comms_events` | Communication / network events |
| `bankcard_events` | Bank card processing events |
| `system_events` | System-level events |
| `scrst_events` | Screen/touchscreen events |
| `event_count` | Total event count on transit_day |

### Historical / Rolling Features (pre-computed in gold SQL)
| Feature | Description |
|---------|-------------|
| `critical_events_7d` | Count of critical events in the rolling 7-day window |
| `critical_events_30d` | Count of critical events in the rolling 30-day window |
| `events_7d` | Total events in the rolling 7-day window |
| `oos_events_7d` | OOS events in the rolling 7-day window |

### Operational KPIs
| Feature | Description |
|---------|-------------|
| `tap_count` | Total tap transactions on transit_day |
| `tap_reject_rate` | Percentage of taps rejected |
| `kpi_availability_pct` | Device availability % from KPI table (0–100) |
| `outage_count` | Number of outage periods on the day |

### Engineered Features (notebook-derived)
| Feature | Description |
|---------|-------------|
| `critical_rolling_3d` | 3-day rolling mean of `critical_events` per device |
| `oos_rolling_3d` | 3-day rolling mean of `oos_event_count` per device |
| `total_events_rolling_7d` | 7-day rolling mean of `event_count` per device |
| `severity_x_unavail` | `kpi_fault_count × (100 − availability_pct_7d)` |
| `event_density` | `event_count / (kpi_availability_pct / 100)` |
| `device_cat_enc` | Label-encoded `mars_device_category` |

---

## 4. Model Architecture

Five-model binary ensemble stacked with a Logistic Regression meta-learner.

```
Input Features (19 cols + 6 engineered = 25 total)
        │
  ┌─────┼─────────────────────────┐
  │     │     │      │            │
LightGBM XGBoost CatBoost RandomForest LogisticRegression
  │     │     │      │            │
  └─────┴─────┴──────┴────────────┘
            Stacking (cv=3)
                  │
         LR Meta-Learner
                  │
          P(will_fail_7d=1)
```

### Hyperparameters

| Model | Key Params |
|-------|-----------|
| LightGBM | n_estimators=400, lr=0.05, num_leaves=63, scale_pos_weight=class_ratio |
| XGBoost | n_estimators=400, lr=0.05, max_depth=6, scale_pos_weight=class_ratio |
| CatBoost | iterations=400, lr=0.05, depth=6, scale_pos_weight=class_ratio |
| RandomForest | n_estimators=300, max_depth=10, class_weight='balanced' |
| LogisticRegression | max_iter=1000, class_weight='balanced' |
| Meta-Learner (LR) | max_iter=1000, class_weight='balanced' |

`class_ratio = count(y=0) / count(y=1)` — computed on training split.

---

## 5. Training Protocol

1. **Sort** the dataset globally by `transit_day` (no shuffle).
2. **TimeSeriesSplit** with `n_splits=5`. Use only the final fold (fold 5) as hold-out test.
3. **Train** all 5 base models on the train portion.
4. **Stack** using `StackingClassifier(cv=3, stack_method='predict_proba')`.
5. **Rolling features** (`critical_rolling_3d`, etc.) must be computed *within* each device's time series before the train/test split to prevent future leakage.

### No-Shuffle Rule
TimeSeriesSplit preserves temporal order. Never use `shuffle=True` or `KFold` for this problem — future device state must not appear in training data.

---

## 6. Evaluation Criteria

### Primary
| Metric | Target | Rationale |
|--------|--------|-----------|
| ROC-AUC | ≥ 0.75 | Threshold-independent, handles class imbalance |
| Average Precision (PR-AUC) | ≥ 0.90 | Important when positive class dominates |

### Secondary
| Metric | Notes |
|--------|-------|
| Macro F1 | Monitor per-device-type |
| Confusion Matrix | Track false negatives (missed failures) — more costly than false positives |
| Per-fold AUC std | Should be < 0.05 across 5 folds |

### Operational Threshold
Default decision threshold = 0.5 on predicted probability. Adjust based on acceptable false-negative rate in deployment.

---

## 7. How to Run

### Prerequisites
```
Python venv: D:\Sathish\ML_Device_Telemetry\venv\Scripts\python.exe
Packages: pandas, numpy, lightgbm, xgboost, catboost, scikit-learn, shap, matplotlib, seaborn
```

### Steps
```bash
# Activate environment
D:\Sathish\ML_Device_Telemetry\venv\Scripts\activate

# Launch notebook
jupyter notebook "D:\Sathish\Chicago_Synthetic data\chicago_oracle_real_data\notebooks\Chicago_PS1_Predictive_Failure.ipynb"
```

### Run All Cells
Execute cells top-to-bottom. Expected runtime: 5–10 minutes on a modern laptop (GPU optional).

---

## 8. How to Interpret Results

### ROC Curve
- A curve hugging the top-left corner is ideal (AUC → 1.0)
- If AUC < 0.6 for a device type, suspect insufficient event diversity in training data

### SHAP Plots
- **Bar plot:** Overall feature importance — top features drive most predictions
- **Beeswarm:** Direction of impact — pink dots = high feature value, blue = low value
- If `critical_events` has low SHAP importance, re-examine event bucketing in the silver layer
- Key features to watch: `critical_events`, `critical_events_7d`, `critical_events_30d`, `oos_event_count`, `kpi_availability_pct`

### Per-Device-Type AUC
- AUC < 0.5 for a device type means the model is performing below random for that type — may indicate:
  - Insufficient training samples for that device type
  - Different failure signature not captured by current features
  - Data quality issues in the gold table for that device type

### Confusion Matrix
- **False Negatives (FN):** Actual failures predicted as non-failures — operationally dangerous
- **False Positives (FP):** Unnecessary maintenance dispatches — costly but acceptable
- Tune decision threshold to minimise FN if SLA penalties are high

---

## 9. Known Limitations / Data Gaps

| Limitation | Impact | Mitigation |
|-----------|--------|-----------|
| Real Oracle 50-row samples | Gold table = 0 rows (DEVICE_KEY non-overlap between dim_device and event tables) | Full Oracle data will populate; pipeline is architecturally correct |
| Class imbalance (~90% positive) | Standard accuracy is misleading | Use ROC-AUC and PR-AUC as primary metrics |
| `will_fail_7d` is a forward-looking label | Cannot be used as a feature; temporal ordering critical | Enforce TimeSeriesSplit |
| No weather / event features | External shocks (Lollapalooza, polar vortex) not captured | Add weather API integration post-production |
| Device-level rolling features computed globally | Rolling window crosses device boundaries if not grouped | Code uses `groupby('DEVICE_ID')` for rolling — verify on update |
| `kpi_availability_pct` may be NULL | Devices not in KPI table have no availability score | Use `availability_pct_7d` (outage-derived) as fallback; both fill NaN→0 |
| `incident_duration_min` not available at prediction time | Must be excluded from feature set | Not included in `FEATURE_COLS` |

---

## 10. Refresh Cadence

| Action | Frequency | Notes |
|--------|-----------|-------|
| Score new daily rows | Daily (next morning) | Append yesterday's telemetry to gold table, re-score |
| Retrain model | Monthly | Use rolling 18-month window of training data |
| Full hyperparameter search | Quarterly | Run Optuna study on latest data |
| Feature review | Quarterly | Check SHAP stability; deprecate low-importance features |
| Gold table rebuild | After each silver pipeline run | Silver layer runs nightly; gold rebuild triggered by dbt/Airflow |
