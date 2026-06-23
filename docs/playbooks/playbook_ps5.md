# Playbook: PS5 — Survival Analysis / Component Remaining Useful Life (RUL)

## 1. Problem Statement

For each physical component installed in a Chicago Ventra fare-collection device, estimate:

1. **`days_to_failure`** (regression) — How many days until this component next fails?
2. **Survival probability S(t)** — What is the probability that a component survives beyond time *t*?
3. **Mean Time Between Failures (MTBF)** — At the fleet level, what is the expected inter-failure interval?

**Censoring is a first-class concern:**
- **Uncensored** (71 components): failure was observed → `days_to_failure` is the actual failure duration
- **Censored** (1,429 components): no failure observed → the component has survived at least `component_age_days` — we do not know when (or if) it will fail

Using only the 71 uncensored rows for regression would throw away 95% of the data.
Survival models (Kaplan-Meier, Cox PH, Weibull AFT) correctly incorporate both censored and uncensored observations.

**Why it matters:** Proactive component replacement before failure avoids unplanned device downtime,
reduces maintenance cost, and ensures SLA compliance for the Chicago Transit Authority.

**Grain:** One row per (DEVICE_ID, COMPONENT_SERIAL_NBR).
**Row count:** 1,500 components.

---

## 2. Data Requirements

| Item | Detail |
|------|--------|
| Gold table | `device_ps5_component.parquet` |
| Row count | 1,500 |
| Grain | (DEVICE_ID, COMPONENT_SERIAL_NBR) |
| Censored rows | 1,429 (95.3%) — `is_censored = True` |
| Uncensored rows | 71 (4.7%) — `is_censored = False` |
| Device categories | SAG, HBG, TVM, RMV, FBX, CSC READER, BMV, RVG, FMVD, DCU |
| Component types | OTHER (717), CSC_READER (492), GATE_MECH (182), PRINTER (40), BHU (37), CHU (32) |

**Gold table path:**
```
D:\Sathish\Chicago_Synthetic data\chicago_oracle_real_data\gold\device_ps5_component.parquet
```

**Key data quality notes:**
- `mtbf_days` is null for 1,468 rows (98%) — only populated when `failures_total ≥ 2`
- `first_failure_dtm` and `last_failure_dtm` are null for censored components
- `total_failure_min` and `avg_failure_min` are null for censored components — fill with 0 before modelling
- `COMPONENT_TYPE_NAME = 'OTHER'` is the dominant class (48%) and is a heterogeneous catch-all

---

## 3. Feature Reference

### Component Identity
| Feature | Type | Description |
|---------|------|-------------|
| `DEVICE_ID` | object | Ventra device identifier |
| `COMPONENT_SERIAL_NBR` | object | Unique component serial number (grain key) |
| `COMPONENT_DESCRIPTION` | object | Free-text description of component model |
| `COMPONENT_TYPE_NAME` | object | Functional type: CHU, BHU, CSC_READER, GATE_MECH, PRINTER, OTHER |
| `DEVICE_NAME` | object | Human-readable device name |
| `DEVICE_SERIAL_NUMBER` | object | Physical device serial number |

### Temporal / Age
| Feature | Type | Description |
|---------|------|-------------|
| `component_age_days` | int64 | Days since component was first reported (observation window) |
| `LAST_REPORTED_DTM` | datetime | Last timestamp the component was reported active |
| `REPORTED_CHANGED_DTM` | datetime | Date of last status change |
| `first_failure_dtm` | datetime | Timestamp of first observed failure (null if censored) |
| `last_failure_dtm` | datetime | Timestamp of most recent failure (null if censored) |

### Failure History
| Feature | Type | Description |
|---------|------|-------------|
| `failures_total` | int64 | Total number of failure events observed for this component |
| `total_failure_min` | float64 | Cumulative failure duration in minutes (null if censored) |
| `avg_failure_min` | float64 | Average duration per failure event (null if censored) |
| `mtbf_days` | float64 | Mean Time Between Failures in days (null if < 2 failures) |
| `failure_subsystems` | object | Comma-separated list of subsystems involved in failures |

### Device Context
| Feature | Type | Description |
|---------|------|-------------|
| `mars_device_category` | object | Device type (TVM, SAG, etc.) — label-encode → `device_cat_enc` |
| `FACILITY_ID` / `FACILITY_NAME` | int/object | Station/garage — label-encode → `facility_enc` |
| `OPERATOR_NAME` | object | Operating agency (CTA, Pace, etc.) |

### Survival Model Columns
| Feature | Derived | Description |
|---------|---------|-------------|
| `observation_duration` | `days_to_failure if uncensored else component_age_days` | Duration to failure or censoring (input to KM/Cox/AFT) |
| `event_flag` | `(~is_censored).astype(int)` | 1 = failure observed, 0 = censored |

### Target Columns
| Column | Type | Description |
|--------|------|-------------|
| `days_to_failure` | int64 | Days from installation to observed failure (only valid when `is_censored=False`) |
| `is_censored` | bool | True = no failure observed in observation window |

### Engineered Features (notebook-derived)
| Feature | Formula | Description |
|---------|---------|-------------|
| `risk_score` | `failures_total / max(component_age_days, 1)` | Failure rate per age-day — primary operational risk metric |
| `failure_intensity` | `avg_failure_min.fillna(0)` | Average downtime per failure event |
| `failure_time_density` | `total_failure_min / max(age_days, 1)` | Cumulative downtime per age-day |
| `is_high_risk` | `(failures_total > 2).astype(int)` | Binary flag for components with > 2 failures |
| `comp_desc_enc` | `LabelEncoder on top-20 + OTHER` | Encoded component model (handles high cardinality) |

---

## 4. Model Architecture

Four complementary models in order of statistical correctness for censored data:

### Model A: Kaplan-Meier (Non-parametric Survival Curves)
```
All components (censored + uncensored)
        │
  KaplanMeierFitter.fit(observation_duration, event_flag)
        │
  S(t) = probability of surviving beyond day t
        │
  Stratified by: mars_device_category, COMPONENT_TYPE_NAME
  Statistical test: log-rank test between groups
```
- **No covariates** — purely time-based estimate
- Produces survival curves; confidence intervals via Greenwood formula
- Use to compare device types and component types

### Model B: Cox Proportional Hazards (Semi-parametric)
```
All components + covariate features
        │
  CoxPHFitter(penalizer=0.1)
  Features: component_age_days, failures_total, failure_intensity,
            failure_time_density, risk_score, device_cat_enc, component_type_enc
        │
  Hazard ratio (exp(coef)) per feature
  Concordance index (C-statistic)
```
- **Assumption:** Proportional hazards — relative hazard between groups is constant over time
- `penalizer=0.1` for L2 regularisation (important with small event count)
- Use for **feature attribution** and identifying which components are at highest relative risk

### Model C: Weibull AFT (Parametric Accelerated Failure Time)
```
All components + covariate features
        │
  WeibullAFTFitter(penalizer=0.01)
  Same features as Cox
        │
  predicted median survival time (days) per component
```
- Weibull distribution fits increasing failure rates (appropriate for aging components)
- AFT interpretation: positive coefficient → component "accelerates" to failure faster
- Use for **scheduling** (predict median survival per component → plan replacement at 70%)

### Model D: LightGBM + RandomForest Regression (Uncensored only)
```
71 uncensored rows only
        │
  RandomForestRegressor + LGBMRegressor
  Target: days_to_failure
  Features: 12 engineered columns
        │
  MAE / RMSE / R² on held-out test rows
  SHAP feature importance
```
- **Caveat:** Only 71 rows — high variance; use as a supplement to survival models
- Useful for SHAP interpretation and understanding which features correlate with short RUL

---

## 5. Training Protocol

1. **Load** `device_ps5_component.parquet`
2. **Derive survival columns:** `observation_duration`, `event_flag`
3. **Feature engineering:** compute `risk_score`, `failure_intensity`, `failure_time_density`, cyclical encodings, label encodings
4. **Handle nulls:** `total_failure_min`, `avg_failure_min` → `fillna(0)`; `mtbf_days` stays null (informative missingness)
5. **Clip extremes:** `risk_score`, `failure_intensity` clipped at 99th percentile before Cox/AFT fitting
6. **Survival models (A–C):** use **all 1,500 rows** (censored + uncensored)
7. **Regression model (D):** subset to `is_censored == False` (71 rows); sort by `component_age_days`; 80/20 split
8. **SHAP:** fit LightGBM on all 71 uncensored rows; compute SHAP on same set

### lifelines installation
```powershell
D:\Sathish\ML_Device_Telemetry\venv\Scripts\pip install lifelines
```
The notebook includes a `try/except` fallback — survival models are skipped gracefully if lifelines is not installed, and a manual empirical survival function is used instead.

---

## 6. Evaluation Criteria

### Survival Models
| Model | Metric | Target | Notes |
|-------|--------|--------|-------|
| Kaplan-Meier | Log-rank p-value | < 0.05 between component types | Confirms different survival distributions |
| Cox PH | Concordance index (C-stat) | ≥ 0.70 | Higher = better discrimination of high vs low risk components |
| Weibull AFT | AIC / BIC | Minimise | Compare vs Exponential and LogNormal AFT |

### Regression (uncensored only)
| Metric | Target | Notes |
|--------|--------|-------|
| MAE (days) | ≤ 300 days | With 71 rows, expect high variance |
| RMSE (days) | ≤ 500 days | Sensitive to outliers (long-surviving components) |
| R² | ≥ 0.30 | Low R² is expected with small n; not a failure |

### Operational Risk Ranking
- `risk_score` should correlate with `is_censored == False` (failed components should have higher risk scores)
- Top 10% by `risk_score` should contain disproportionate share of actual failures

---

## 7. How to Run

### Prerequisites
```
Python venv: D:\Sathish\ML_Device_Telemetry\venv\Scripts\python.exe
Required packages: pandas, numpy, lightgbm, scikit-learn, shap, matplotlib, seaborn
Optional (survival models): lifelines
```

### Install lifelines (if not already installed)
```powershell
& "D:\Sathish\ML_Device_Telemetry\venv\Scripts\pip.exe" install lifelines
```

### Run the notebook
```powershell
D:\Sathish\ML_Device_Telemetry\venv\Scripts\activate
jupyter notebook "D:\Sathish\Chicago_Synthetic data\chicago_oracle_real_data\notebooks\Chicago_PS5_Survival_RUL.ipynb"
```

Run all cells top-to-bottom. Expected runtime: 3–8 minutes.
- Without lifelines: ~2–3 minutes (regression + SHAP only)
- With lifelines: 5–8 minutes (full survival pipeline)

---

## 8. How to Interpret Results

### Kaplan-Meier Curves
- The y-axis is **S(t) = probability of surviving to time t** (not failing before t)
- Steeper drop = higher failure rate for that group
- Wide confidence intervals = few events in that group (unreliable estimate)
- If two curves cross, the proportional hazards assumption for Cox may be violated

### Cox PH Hazard Ratios
| Interpretation | Meaning |
|----------------|---------|
| `exp(coef) > 1` | Feature **increases** hazard (accelerates failure) |
| `exp(coef) < 1` | Feature **decreases** hazard (protective of failure) |
| `exp(coef) = 1` | Feature has no effect |

- `failures_total exp(coef) >> 1`: Prior failures significantly increase failure hazard — prioritise components with failure history
- `component_age_days exp(coef) > 1`: Aging increases hazard — fleet-wide, older components are higher risk
- Large `component_type_enc` hazard ratio: Certain component types (CSC_READER) have inherently higher failure risk

### Weibull AFT Predicted Median Survival
- Schedule inspection at **70% of predicted median survival days**
- Example: If `aft_median_survival = 500 days` for a component type → schedule inspection at day 350
- Components where current `component_age_days > aft_median_survival` are **overdue** — prioritise immediately

### Risk Score Tiers
| Risk Score | Tier | Action |
|-----------|------|--------|
| > 0.002 | Critical | Immediate replacement / inspection |
| 0.001 – 0.002 | High | Schedule within 30 days |
| 0.0001 – 0.001 | Medium | Schedule within 90 days |
| < 0.0001 | Low | Normal maintenance cycle |

### SHAP Interpretation (Regression)
- **`failures_total` high positive SHAP:** Components with more past failures have shorter predicted RUL — the strongest actionable signal
- **`component_age_days` high positive SHAP:** Older components tend to fail sooner
- **`failure_intensity` (avg_failure_min) high positive SHAP:** Longer per-failure durations indicate severe underlying hardware issues
- **`component_type_enc` high SHAP:** Component type is a structural predictor — not actionable but validates model sense-check

---

## 9. Known Limitations / Data Gaps

| Limitation | Impact | Mitigation |
|-----------|--------|-----------|
| 95.3% censoring rate (only 71 failures) | Survival curves have wide CIs; regression is high-variance | Use more data (longer observation window); use Cox/AFT which handle censoring correctly |
| Only 1 month (March 2025) of snapshot data | Component age is correct but failure event timing is compressed | Extend to 12–24 months rolling window for stable KM/Cox estimates |
| `mtbf_days` null for 98% of rows | Cannot use MTBF directly as a feature | Use `risk_score` proxy; compute MTBF only for multi-failure components |
| `COMPONENT_TYPE_NAME = 'OTHER'` is 48% of rows | Other category masks component-specific patterns | Investigate COMPONENT_DESCRIPTION to sub-classify OTHER into meaningful types |
| `failure_subsystems` is a comma-separated string | Not used directly — would require parsing | Parse and one-hot encode in v2 for more granular hazard modelling |
| No installation date column (only `component_age_days`) | Cannot anchor component lifecycle to calendar time | Use `REPORTED_CHANGED_DTM` as installation proxy if available |
| Synthetic data: failure events are algorithmically assigned | Real failure patterns may differ significantly | Validate survival curves against actual Cubic MARS work orders post-production |
| Cox PH convergence with small event count | Model may fail to converge without penaliser | Use `penalizer=0.1` (L2 ridge); increase to 1.0 if still failing |
| AFT assumes Weibull distribution | Real failure modes may be better fit by LogNormal or Exponential | Compare AIC across distributions: `WeibullAFTFitter`, `LogNormalAFTFitter` |
| LightGBM regression on 71 rows | Severe overfitting risk | Use strong regularisation; treat R² as indicative; prefer survival models |

---

## 10. Refresh Cadence

| Action | Frequency | Notes |
|--------|-----------|-------|
| Score new components (risk_score) | Daily | Append new component records from silver pipeline |
| Refit Kaplan-Meier curves | Monthly | Cumulative data; each month adds new failure events |
| Refit Cox PH / AFT models | Monthly (or when ≥ 10 new events) | More events = better coefficient estimates |
| Retrain LightGBM regression | When ≥ 20 new uncensored rows accumulate | 71 → 100+ rows = meaningful improvement in regression stability |
| Update `risk_score` tiers | Quarterly | Review tier thresholds against actual maintenance cost data |
| SHAP analysis review | Quarterly | Confirm feature importance stability across refreshes |
| Gold table rebuild | After each silver pipeline run | PS5 gold depends on `device_ps1_daily` and availability event silver tables |
| Validate against Cubic MARS work orders | When live data available | Ground-truth validation of predicted vs actual failure dates |
