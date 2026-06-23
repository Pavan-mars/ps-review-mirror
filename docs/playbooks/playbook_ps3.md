# Playbook: PS3 — Root Cause Classification

## 1. Problem Statement

Given an availability event (incident) for a fare-collection device, classify:

**Task A — Failure Severity:** How severe is this incident?
- **Real Oracle data (2026-06-23 onward):** `AE_FAILURE_LEVEL` uses the confirmed Ventra taxonomy — hardware faults (`is_device_fault=TRUE`): 1=NONPAYMENT, 2=PURCHASE_CARD, 3=PURCHASE_PRODUCT, 4=ALL_PURCHASE, 5=ALL_FUNCTIONS, 16=BUS_READER_ASSEMBLY. Decoded via `silver.dim_failure_level` (S18).
- **Synthetic gold data:** `failure_level_label` is all `Unknown`; notebook derives `failure_severity` via tertile-binning as a proxy.

**Task B — Root Cause Category:** What subsystem caused this failure?  
- 8-class multiclass: `BHU_FAULT`, `CHU_FAULT`, `GATE_FAULT`, `READER_FAULT`, `COMMS_FAULT`, `POWER_FAULT`, `PRINTER_FAULT`, `OTHER`

**Why it matters:** Knowing the root cause at the moment of incident creation (without waiting for a technician visit) enables automatic work-order routing to the right specialist team, reducing mean time to repair (MTTR) and improving SLA compliance.

**Grain:** One row per (device_id, availability_event_id).  
**Row count:** 25,411 incidents.

---

## 2. Data Requirements

| Item | Detail |
|------|--------|
| Gold table | `device_ps3_incident.parquet` |
| Row count | 25,411 |
| Grain | (device_id, availability_event_id) |
| Date range | 2025-03-01 → 2026-04-30 (approx) |
| Device categories | RMV, FBX, HBG, SAG, BMV, RVG, TVM, DCU, FMVD (CSC READER maps to `mars_device_category=OTHER` as of 2026-06-23) |

**Gold table path:**
```
D:\Sathish\Chicago_Synthetic data\chicago_oracle_real_data\gold\device_ps3_incident.parquet
```

**Note on `failure_level`:** The raw column is a continuous numeric severity score (range ~80–100,000). The `failure_level_label` column is `Unknown` for all rows in the synthetic gold data. The notebook derives `failure_severity` via tertile binning.

**Note on `incident_duration_min`:** Contains negative values in the raw data (clock/timezone artifact from synthetic generation). Always take `abs()` before using.

---

## 3. Feature Reference

### Pre-Incident Event Counters (24-hour window)
| Feature | Description |
|---------|-------------|
| `events_24h_prior` | Total events in the 24 hours before incident start |
| `critical_24h_prior` | Critical-severity events in the 24h window |
| `bhu_events_24h` | Bill Handling Unit events in 24h window |
| `chu_events_24h` | Coin Handling Unit events in 24h window |
| `printer_events_24h` | Printer events in 24h window |
| `gate_mech_events_24h` | Gate mechanical events in 24h window |
| `csc_reader_events_24h` | CSC reader events in 24h window |
| `comms_events_24h` | Communications events in 24h window |

### Device Context
| Feature | Description |
|---------|-------------|
| `component_age_days` | Age of device component at time of incident (days since installation) |
| `mars_device_category` | Device type (TVM, SAG, HBG, etc.) — label-encoded as `device_cat_enc` |
| `DEVICE_TYPE_NAME` | Finer-grained device type name (FYI, not a model feature) |

### Incident Attributes
| Feature | Description |
|---------|-------------|
| `incident_duration_abs` | Absolute value of `incident_duration_min` (minutes) |

### Targets
| Column | Description |
|--------|-------------|
| `failure_level` | Raw numeric severity score (not used directly as target) |
| `failure_severity` | Derived: 0=Minor, 1=Major, 2=Critical (tertile bins on `failure_level`) |
| `root_cause_category` | 8-class root cause string — encoded as `root_cause_enc` |

### Engineered Features (notebook-derived)
| Feature | Formula |
|---------|---------|
| `incident_duration_abs` | `abs(incident_duration_min)` |
| `age_x_events` | `component_age_days × events_24h_prior` |
| `critical_rate_24h` | `critical_24h_prior / max(events_24h_prior, 1)` |
| `hardware_events_24h` | `bhu + chu + printer + gate_mech` events |
| `comms_reader_events_24h` | `csc_reader + comms` events |

---

## 4. Model Architecture

Two parallel multiclass classification tasks, each with three models.

### Task A: Failure Severity (3 classes)
```
Input Features (15 cols)
        │
  ┌─────┼──────────────┐
  │     │              │
LightGBM XGBoost  RandomForest
  │     │              │
  └─────┴──────────────┘
   Majority vote / best model
        │
  failure_severity  (0/1/2)
```

### Task B: Root Cause Category (8 classes)
```
Input Features (15 cols)
        │
  ┌─────┼──────────────┐
  │     │              │
LightGBM XGBoost  RandomForest
  │     │              │
  └─────┴──────────────┘
   Majority vote / best model
        │
  root_cause_category  (8 classes)
```

### Hyperparameters

| Model | Key Params (both tasks) |
|-------|------------------------|
| LightGBM | n_estimators=400, lr=0.05, num_leaves=63, class_weight='balanced', objective='multiclass' |
| XGBoost | n_estimators=400, lr=0.05, max_depth=6, eval_metric='mlogloss' |
| RandomForest | n_estimators=300, max_depth=12, class_weight='balanced' |

---

## 5. Training Protocol

1. **Derive targets:**
   - `failure_severity = pd.cut(failure_level, bins=[-inf, Q33, Q67, inf], labels=[0,1,2])`
   - `root_cause_enc = LabelEncoder().fit_transform(root_cause_category)`
2. **Encode** `mars_device_category` with `LabelEncoder` → `device_cat_enc`
3. **Compute engineered features** (see Feature Reference)
4. **Fill NaNs with 0** on all feature columns
5. **Sort** globally by `transit_day` — no shuffle
6. **TimeSeriesSplit(n_splits=5)** — use final fold as hold-out
7. **Train** LightGBM, XGBoost, RandomForest independently on each task
8. **Evaluate** separately for Task A and Task B

### Tertile Cut-Points (gold synthetic data)
| Percentile | Approx value |
|-----------|-------------|
| Q33 (33rd) | ~25,000 |
| Q67 (67th) | ~75,000 |

Recompute cut-points on each training refresh — do not hard-code these values.

---

## 6. Evaluation Criteria

### Primary: Macro F1
| Task | Target Macro F1 | Rationale |
|------|----------------|-----------|
| Failure Severity (3-class) | ≥ 0.55 | Balanced classes via tertile binning |
| Root Cause (8-class) | ≥ 0.50 | `OTHER` class (39%) depresses macro F1 |

### Per-Class Targets
| Root Cause | Expected F1 | Notes |
|-----------|-------------|-------|
| BHU_FAULT | ≥ 0.60 | Distinct event signature (bhu_events_24h) |
| CHU_FAULT | ≥ 0.60 | Distinct event signature (chu_events_24h) |
| GATE_FAULT | ≥ 0.60 | gate_mech_events_24h is a strong signal |
| READER_FAULT | ≥ 0.55 | csc_reader_events_24h signal |
| COMMS_FAULT | ≥ 0.45 | Harder — shares generic event patterns |
| POWER_FAULT | ≥ 0.40 | Hardest — no dedicated event counter |
| PRINTER_FAULT | ≥ 0.50 | printer_events_24h available |
| OTHER | ≥ 0.60 | Dominant class — easier by volume |

### Confusion Matrix Checks
- For **severity**: ensure Minor class is not systematically misclassified as Critical
- For **root cause**: `COMMS_FAULT` and `POWER_FAULT` confusion is expected; verify these two classes have dedicated escalation workflows independent of the model

---

## 7. How to Run

### Prerequisites
```
Python venv: D:\Sathish\ML_Device_Telemetry\venv\Scripts\python.exe
Packages: pandas, numpy, lightgbm, xgboost, scikit-learn, shap, matplotlib, seaborn
```

### Steps
```bash
D:\Sathish\ML_Device_Telemetry\venv\Scripts\activate
jupyter notebook "D:\Sathish\Chicago_Synthetic data\chicago_oracle_real_data\notebooks\Chicago_PS3_Root_Cause.ipynb"
```

Run all cells top-to-bottom. Expected runtime: 8–15 minutes (25K rows × two tasks × three models).

---

## 8. How to Interpret Results

### Failure Severity Predictions
| Predicted class | Action |
|----------------|--------|
| Critical (2) | Immediate dispatch; escalate to field supervisor |
| Major (1) | Schedule maintenance within 4–8 hours |
| Minor (0) | Log and monitor; next scheduled maintenance window |

### Root Cause Predictions
| Predicted class | Routing |
|----------------|---------|
| BHU_FAULT / CHU_FAULT | Dispatch cash-handling specialist (TVM / BMV) |
| GATE_FAULT | Dispatch mechanical engineer (SAG / HBG / TVM gate) |
| READER_FAULT | Dispatch contactless/CSC specialist |
| COMMS_FAULT | Dispatch network/comms team; check station router |
| POWER_FAULT | Dispatch electrician; check station power supply |
| PRINTER_FAULT | Dispatch ticket-tech; carry printer ribbon and paper |
| OTHER | General technician; on-site diagnosis required |

### SHAP Interpretation
- **`device_cat_enc` high SHAP for root cause:** The device type is the strongest predictor — this is expected (GATE_FAULT only possible on gate devices)
- **`bhu_events_24h` positive SHAP for BHU_FAULT:** Higher BHU events increase likelihood of BHU root cause — logical validation of model
- If `incident_duration_abs` has high SHAP for severity: duration is partially post-hoc (the incident must be closed before duration is known) — consider excluding from real-time scoring

### Cross-Validation Stability
- If Fold 5 macro F1 is much lower than Folds 1–4, there may be concept drift in later months (newer device firmware changes failure signatures)

---

## 9. Known Limitations / Data Gaps

| Limitation | Impact | Mitigation |
|-----------|--------|-----------|
| `failure_level_label` is all `Unknown` | Cannot use label directly; use tertile-binned proxy | Use tertile binning; update cut-points when real labels arrive |
| `incident_duration_min` has negative values | Cannot use raw column | Always use `abs()` version; investigate source in silver layer |
| `OTHER` dominates root cause (39%) | Depresses macro F1; `OTHER` is a catch-all | Explore text features from `AE_FAULT_DESCRIPTION` to sub-classify `OTHER` |
| No real work-order validation | Root cause labels are synthetic | Cross-validate against Cubic MARS work-order data post-production |
| `incident_duration_abs` is partially post-hoc | Should not be used in real-time scoring | Remove for operational deployment; keep for offline analysis |
| Device category encoded as integer | Ordinal encoding introduces false ordering | Consider one-hot encoding for LR; tree models handle it correctly |
| Small class counts for rare root causes | `POWER_FAULT`, `PRINTER_FAULT` each < 1,500 rows | Use `class_weight='balanced'`; consider oversampling with caution |
| No PS3 incident text features used | `AE_FAULT_DESCRIPTION`, `AE_SYMPTOM` columns available but unused | Add TF-IDF or sentence embedding features in v2 |

---

## 10. Refresh Cadence

| Action | Frequency | Notes |
|--------|-----------|-------|
| Score new incidents | On-demand (incident creation time) | REST endpoint scoring preferred over batch |
| Retrain models | Monthly | Append last 30 days; maintain 18-month rolling window |
| Update tertile cut-points | Monthly | Failure_level distribution shifts as fleet ages |
| Retrain LabelEncoder | When new device types added | CSC READER, FMVD are newer categories |
| SHAP analysis review | Quarterly | Verify feature importance stability |
| Gold table rebuild | After each silver pipeline run | PS3 gold depends on silver S11 (`incident_root_cause`) + S18 (`dim_failure_level`) + AE tables |
