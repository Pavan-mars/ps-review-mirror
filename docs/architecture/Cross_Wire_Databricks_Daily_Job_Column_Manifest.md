# Cross-Wire Databricks Daily Job — Column Manifest

**Purpose:** Define every column required to build the **daily Databricks job** that feeds the cross-wire layer (PS1–PS5 unified at device / component / incident / station grain) for **S3 → Dispatcher → Handler → RDS → Dashboard**.

**Architecture (target flow):**

```
Databricks (daily job)
    → S3  (scored / cross-wired parquet)
        → Dispatcher (Lambda / EventBridge)
            → Handler (Lambda — S3→RDS upsert)
                → RDS (PostgreSQL)
                    → Dashboard API
```

**Primary cross-wire output (recommended gold table):**

`s3://<gold-bucket>/chicago/gold/device_cross_wired_daily/`  
Grain: `(city_id, DEVICE_ID, DEVICE_KEY, transit_day, COMPONENT_SERIAL_NBR)` — one row per device-day-component; device-only rows when no serial.

---

## 1. Diagram entities → required columns

Your architecture diagram lists seven entity groups on the left. Map them as follows.

| Diagram entity | Required columns | Primary source(s) | Notes |
|---|---|---|---|
| **OOS** | `is_hardware_oos`, `hardware_oos_count`, `oos_events_7d`, `will_hardware_oos_3d`, `days_since_hw_oos` | `silver.device_outage`, `gold.device_ps1_daily`, PS1 inference, `ps5_reliability_estimates` | Hardware OOS Set vs chargeable SLA are different labels |
| **Chargeable events** | `is_chargeable`, `chargeable_outage_count`, `chargeable_outage_min`, `chargeable_count_7d_past`, `chargeable_count_30d_past` | `silver.device_outage`, `gold.device_ps1_daily`, `silver.device_incident_features_daily` | Use for SLA / PS3 severity context |
| **Total devices** | `DEVICE_ID`, `DEVICE_KEY`, `mars_device_category`, `device_type_dashboard`, `is_active`, `device_age_days` | `silver.dim_device`, spine counts | `device_type_dashboard` = readers/tvms/gates/validators enum for RDS |
| **Total Serial IDs** | `COMPONENT_SERIAL_NBR`, `COMPONENT_TYPE`, `COMPONENT_PART_NBR`, `COMPONENT_MANUFACTURER` | `silver.hw_config_current` | Expand device → component rows for dispatch |
| **Sys IDs** | `SN_SYS_ID`, `availability_event_id`, `CMDB_CI_SYS_ID` (if available) | `gold.device_ps3_incident`, ServiceNow mirror | Join key for incident ↔ device |
| **Incident IDs** | `availability_event_id`, `AE_NUMBER`, `WOT_NUMBER` (work order), `failure_level`, `failure_level_label` | `gold.device_ps3_incident` | PS3 grain = one row per incident |
| **Station names** | `FACILITY_ID`, `FACILITY_NAME`, `STOP_POINT_ID`, `STOP_POINT_NAME`, `TRANSIT_ARRAY_ID`, `ARRAY_POSITION` | `silver.dim_device`, `silver.dim_stop_point` | GATE array context for cascade |

---

## 2. Cross-wire join keys (mandatory in every export partition)

| Column | Type | Required | Role |
|---|---|---|---|
| `city_id` | string | **Y** | Always `CHI` for Chicago pilot; RDS `city_code` enum |
| `transit_day` | date | **Y** | Daily grain spine |
| `asof_date` | date | **Y** | Job run / feature snapshot date (partition key) |
| `DEVICE_ID` | string | **Y** | Ventra device identifier (TVM*, RVG*, etc.) |
| `DEVICE_KEY` | bigint | **Y** | EDW surrogate; join to silver S20/S28/PS5 |
| `mars_device_category` | string | **Y** | `TVM`, `GATE`, `VALIDATOR` |
| `device_type_dashboard` | string | **Y** | Map: TVM→`tvms`, GATE→`gates`, VALIDATOR→`validators` |

---

## 3. Device & station context (dim_device + stop point)

| Column | Type | Source | RDS / dashboard use |
|---|---|---|---|
| `DEVICE_NAME` | string | `dim_device` | Device 360, alerts |
| `FACILITY_ID` | string | `dim_device` | Station filter, PS2 contagion |
| `FACILITY_NAME` | string | `dim_device` | **Station names** in UI |
| `STOP_POINT_ID` | string | `dim_stop_point` | Geo / station analytics |
| `STOP_POINT_NAME` | string | `dim_stop_point` | Display label |
| `OPERATOR_ID` | string | `dim_device` | Operator context |
| `BUS_ID` | string | `dim_device` | VALIDATOR bus devices |
| `bus_device_flag` | int | `dim_device` | VALIDATOR filter |
| `TRANSIT_ARRAY_ID` | string | `dim_device` | GATE gate-bank / PS2 cascade |
| `ARRAY_POSITION` | int | `dim_device` | Peer gate position |
| `is_current` | boolean | `dim_device` | Filter active devices only |
| `device_age_days` | int | `gold.device_ps1_daily` / lifecycle | PS1 feature + display |

---

## 4. Component / serial grain (hw_config_current)

| Column | Type | Source | Required for dispatch |
|---|---|---|---|
| `COMPONENT_SERIAL_NBR` | string | `hw_config_current` | **Y** — serial-level RUL / work orders |
| `COMPONENT_TYPE` | string | `hw_config_current` | BHU, CHU, reader, printer, etc. |
| `COMPONENT_PART_NBR` | string | `hw_config_current` | Spares / CMDB |
| `COMPONENT_MANUFACTURER` | string | `hw_config_current` | Vendor context |
| `LAST_REPORTED_DTM` | timestamp | `hw_config_current` | Staleness check |

**Aggregates for dashboard KPIs (job must compute daily):**

| Column | Type | Definition |
|---|---|---|
| `total_devices_city` | int | `COUNT(DISTINCT DEVICE_ID)` per city/day/category |
| `total_serials_city` | int | `COUNT(DISTINCT COMPONENT_SERIAL_NBR)` per city/day/category |
| `devices_with_oos_today` | int | devices with `hardware_oos_count > 0` |
| `devices_chargeable_today` | int | devices with chargeable outage today |

---

## 5. OOS & chargeable event columns

| Column | Type | Source | PS |
|---|---|---|---|
| `is_hardware_oos` | int/bool | `device_outage` / PS1 gold | PS1, PS5 |
| `is_chargeable` | int/bool | `device_outage` | PS1, PS3 |
| `failure_level` | int | `device_outage` / PS3 | PS3 target taxonomy |
| `hardware_oos_count` | int | `device_ps1_daily` | PS1 (exclude same-day from features if OOS label) |
| `chargeable_outage_count` | int | `device_ps1_daily` | PS1 |
| `chargeable_outage_min` | double | `device_ps1_daily` | PS5 / SLA |
| `oos_events_7d` | int | rolling from outage | PS1 |
| `hardware_oos_events_7d` | int | rolling | PS1 |
| `outage_min_7d` | double | rolling | PS1 / PS5 |
| `availability_pct_7d` | double | KPI / metric | PS5 SLA tab |
| `will_hardware_oos_3d` | int | PS1 OOS label (built in notebook or gold) | PS1 |
| `will_fail_3d` | int | chargeable SLA label (optional cross-check) | PS1 legacy |
| `days_since_hw_oos` | double | PS5 scorer | PS5 |
| `days_since_last_incident` | int | S24 incident features | PS1, PS4 |

---

## 6. Incident / Sys-ID columns (PS3)

Grain: `(availability_event_id)` — join to device-day via `DEVICE_ID` + date window.

| Column | Type | Source | Notes |
|---|---|---|---|
| `availability_event_id` | string | `device_ps3_incident` | **Primary incident ID** |
| `SN_SYS_ID` | string | `device_ps3_incident` | ServiceNow sys_id |
| `AE_NUMBER` | string | SN availability event | Display / ticket link |
| `AE_START_DTM` | timestamp | PS3 | Incident start |
| `AE_END_DTM` | timestamp | PS3 | Incident end |
| `AE_FAILURE_LEVEL` | int | PS3 | Target: 1,2,3,4,5,16 |
| `failure_level_label` | string | PS3 | Human label |
| `derived_component_type` | string | PS3 | BHU, READER, GATE, etc. |
| `desc_bill_handler_flag` | int | PS3 | Component flags for root cause |
| `desc_card_reader_flag` | int | PS3 | |
| `desc_printer_flag` | int | PS3 | |
| `kpi_rule_id` | string | PS3 | Governance (often sparse) |
| `incident_count_7d_past` | int | S24 | Rolling incident volume |
| `chargeable_count_7d_past` | int | S24 | Chargeable history |
| `avg_mttr_30d_past` | double | S24 | MTTR context |

**PS3 RDS targets (existing backfill schema):** `ps3_severity_summary`, `ps3_severity_drivers`, `ps3_severity_predictions`.

---

## 7. PS1 — failure prediction (cross-wire core)

From PS1 inference + gold spine (see `device_ps1_cross_wired_daily` in PS1 notebooks CELL 24).

| Column | Type | Required | Source |
|---|---|---|---|
| `ps1_fail_prob` | double | **Y** | Model score |
| `ps1_predicted` | int | **Y** | Binary at threshold |
| `threshold_used` | double | **Y** | Operating threshold |
| `ps1_risk_tier` | string | **Y** | CRITICAL/HIGH/MEDIUM/LOW |
| `is_prob_anomaly` | bool | N | Score > device p95 history |
| `ps1_p95_hist` | double | N | Device historical p95 |
| `device_category` | string | **Y** | TVM/GATE/VALIDATOR model tag |
| `champion_model` | string | **Y** | e.g. catboost_02, lgb_01 |
| `model_version` | string | **Y** | v4, notebook run id |
| `shap_feat1` … `shap_feat3` | string | N | Top SHAP features |
| `shap_val1` … `shap_val3` | double | N | SHAP values |
| `gate_pass` | bool | N | Quality gate flag |

**RDS target:** `failure_predictions`, `device_health`, `prediction_timeseries`.

---

## 8. PS2 — cascade / coordinated failure

From `gold.device_ps2_chains` (join on `DEVICE_KEY`, `transit_day`).

| Column | Type | Source |
|---|---|---|
| `is_coordinated_station_failure` | int | PS2 |
| `is_major_station_event` | int | PS2 |
| `station_devices_failed` | int | PS2 |
| `days_healthy_before_chain` | int | PS2 |
| `no_prior_failure_in_window` | int | PS2 |
| `chain_length` | int | PS2 |
| `chain_failure_count` | int | PS2 |
| `failure_acceleration_rate` | double | PS2 |
| `inter_failure_days_mean` | double | PS2 |

**RDS targets:** `ps2_cascade_window_summary`, `ps2_subsystem_hub_summary`, `ps2_facility_contagion_summary`, `cascade_events`.

---

## 9. PS4 — anomaly detection (hourly → daily roll-up for cross-wire)

Source: `gold.device_ps4_hourly` or `s3://.../chicago/ps4/scored/asof=<date>/`.

| Column | Type | Grain | Notes |
|---|---|---|---|
| `hour_bucket` / `hour_dt` | timestamp | hourly | PS4 native grain |
| `ensemble_anomaly_flag` | int | hourly | **Primary PS4 target** (≥2 of 3 signals) |
| `event_rate_anomaly` | int | hourly | Signal 1 |
| `metric_anomaly` | int | hourly | Signal 2 |
| `reject_rate_anomaly` | int | hourly | Signal 3 (adaptive 2σ) |
| `signal_active_count` | int | hourly | Count of fired signals |
| `anomaly_type` | string | hourly | e.g. spc+metric |
| `severity` | string | hourly | Critical/High/Medium |
| `anomaly_score` | double | hourly | if_score or signal ratio |
| `anomaly_hours_7d` | int | daily roll-up | For device-day cross-wire |
| `anomaly_hours_30d` | int | daily roll-up | Trend tab |

**RDS targets:** `anomalies`, `anomaly_timeline`, `outlier_scores`.

---

## 10. PS5 — reliability / RUL (device + serial)

From batch scorer / `ps5_reliability_estimates` + `ps5_serial_reliability`.

| Column | Type | Grain | Source |
|---|---|---|---|
| `rul_standard_days` | double | device | PS5 scorer |
| `predicted_median_survival_days` | double | device | PS5 |
| `hazard_score` | double | device | PS5 |
| `risk_band` | string | device | PS5 |
| `is_overdue` | bool | device | PS5 |
| `n_prior_failures` | int | device | PS5 |
| `concordance_index` | double | device | Model metadata |
| `weibull_shape` | double | device | Model metadata |
| `component_age_days` | double | serial | PS5 serial |
| `expected_component_rul_days` | double | serial | PS5 serial |
| `risk_score` | double | serial | PS5 serial |
| `risk_tier` | string | serial | PS5 / gold PS5 component |
| `ps5_risk_tier` | string | serial | Gold join in PS1 CELL 24 |
| `avg_rolling_mttr_30d_min` | double | serial | Gold PS5 component |
| `event_def_version` | string | both | e.g. `2026-07-23.v1` |
| `feature_asof_date` | date | both | Aligns daily job partition |

**RDS targets:** `ps5_reliability_estimates`, `ps5_serial_reliability`, `ps5_reliability_status`.

---

## 11. Recommended unified cross-wire export schema

**Table:** `mars_dev.gold.device_cross_wired_daily`  
**S3:** `s3://<gold-bucket>/chicago/gold/device_cross_wired_daily/asof=<YYYY-MM-DD>/`  
**Partition:** `asof_date`, `mars_device_category`

### Minimum viable column set (MVP for Dispatcher/Handler)

```
-- Keys
city_id, asof_date, transit_day, DEVICE_ID, DEVICE_KEY, mars_device_category,
device_type_dashboard, COMPONENT_SERIAL_NBR,

-- Station / device context
FACILITY_ID, FACILITY_NAME, STOP_POINT_NAME, TRANSIT_ARRAY_ID,

-- Counts (diagram KPIs)
total_devices_city, total_serials_city, devices_with_oos_today, devices_chargeable_today,

-- OOS / chargeable
is_hardware_oos, is_chargeable, hardware_oos_count, chargeable_outage_count,
will_hardware_oos_3d, days_since_hw_oos,

-- Incident (nullable — device-day may have 0..N incidents; use latest or array JSON)
availability_event_id, SN_SYS_ID, AE_FAILURE_LEVEL, failure_level_label,

-- PS1
ps1_fail_prob, ps1_predicted, ps1_risk_tier, threshold_used, champion_model,

-- PS2
is_coordinated_station_failure, station_devices_failed,

-- PS4 (daily roll-up)
anomaly_hours_7d, ensemble_anomaly_flag_daily, max_anomaly_score_7d,

-- PS5 device
rul_standard_days, hazard_score, risk_band, is_overdue,

-- PS5 serial
COMPONENT_TYPE, expected_component_rul_days, ps5_risk_tier,

-- Lineage
run_ts, pipeline_version, source_tables_json
```

### Full column set

Include all columns listed in sections 3–10 above (107+ PS1 features optional in a separate `device_ps1_features_daily` table to keep cross-wire row narrow).

---

## 12. S3 layout by problem statement (existing + proposed)

| PS | S3 prefix | Written by | RDS loader |
|---|---|---|---|
| Gold spine | `chicago/gold/device_ps1_daily/` | Databricks export | Future PS1 loader |
| PS1 cross-wire | `chicago/device_ps1_cross_wired_daily/{gate\|tvm\|validator}/` on artifacts bucket | SageMaker PS1 CELL 24 (per device notebook) | **To build** |
| PS1 scored | `chicago/ps1/scored/asof=<date>/` | SageMaker / batch | **To build** |
| PS2 | `chicago/gold/device_ps2_chains/` | Databricks gold | `phase1_ps2_ps5_backfill.sql` pattern |
| PS3 | `chicago/ps3/scored/` (proposed) | Databricks / notebook | `03_phase1b_ps3_severity.sql` |
| PS4 | `chicago/ps4/scored/asof=<date>/` | PS4 PySpark CELL 19 | **To build** |
| PS5 | `chicago/ps5/scored/` | Batch Transform | `load_transform_output_to_rds.py` |
| **Unified** | `chicago/cross_wired/daily/asof=<date>/` | **Databricks daily job** | **New handler** |

---

## 13. Databricks daily job — suggested task list

1. **Read** gold tables: `device_ps1_daily`, `device_ps2_chains`, `device_ps4_hourly`, `device_ps3_incident`, `device_ps5_component`.
2. **Read** silver: `dim_device` (is_current), `hw_config_current`, `dim_stop_point`, `device_outage`.
3. **Build spine:** `(DEVICE_ID, DEVICE_KEY, transit_day)` for `transit_day = asof_date - lag`.
4. **Expand serials:** LEFT JOIN `hw_config_current` → component rows.
5. **Attach PS1 scores** from latest `device_ps1_cross_wired_daily/{gate|tvm|validator}/` or inference partition.
6. **Roll PS4 hourly → daily** per device (`SUM(ensemble_anomaly_flag)`, `MAX(anomaly_score)`).
7. **Attach latest PS5** device + serial scores for `asof_date`.
8. **Compute KPI counts** (total devices, total serials, OOS count, chargeable count).
9. **Write** parquet to `chicago/cross_wired/daily/asof=<date>/`.
10. **Emit** manifest JSON for Dispatcher (`row_counts`, `paths`, `schema_version`).

---

## 14. Dispatcher / Handler contract

**Dispatcher input (EventBridge / S3 event):**
```json
{
  "job": "cross_wired_daily",
  "city": "CHI",
  "asof_date": "2026-04-11",
  "s3_uri": "s3://<bucket>/chicago/cross_wired/daily/asof=2026-04-11/",
  "manifest_uri": "s3://<bucket>/chicago/cross_wired/manifest/asof=2026-04-11/manifest.json"
}
```

**Handler responsibilities:**
- Idempotent upsert by `(city_id, DEVICE_ID, DEVICE_KEY, transit_day, COMPONENT_SERIAL_NBR, asof_date)`
- Route PS-specific columns to: `failure_predictions`, `anomalies`, `ps5_reliability_estimates`, `ps5_serial_reliability`, `ps3_severity_predictions`
- Preserve `event_def_version` and `feature_asof_date` lineage (PS5 pattern)

---

## 15. Validation checks (job must fail if violated)

| Check | Rule |
|---|---|
| Grain uniqueness | No dupes on `(DEVICE_ID, transit_day, COMPONENT_SERIAL_NBR)` |
| Device coverage | `total_devices_city` within ±5% of prior day |
| Serial coverage | `total_serials_city` > 0 for TVM/GATE |
| OOS sanity | `devices_with_oos_today` ≥ 0 and not 100% of fleet |
| PS4 roll-up | `ensemble_anomaly_flag_daily` rate between 0.01% and 5% |
| PS1 scores | `ps1_fail_prob` ∈ [0, 1] |
| Station join | ≥ 90% rows have non-null `FACILITY_NAME` for fixed devices |
| Incident join | PS3 rows have non-null `availability_event_id` when incident_count > 0 |

---

## 16. References in repo

| Artifact | Path |
|---|---|
| Gold schemas | `docs/data_dictionary/chicago_gold_schemas.md` |
| RDS dashboard schema | `dashboard/docs/schema.sql` |
| PS1 cross-wire notebook cell | `PS1_3d_*_SageMaker_MLflow_FeatureStore.ipynb` CELL 24 |
| PS4 S3 export | `PS4_SageMaker_MLflow_FeatureStore_PySpark.ipynb` CELL 19 |
| PS5 S3→RDS loader | `sagemaker/ps5/batch/pipeline/load_transform_output_to_rds.py` |
| Cross-wire Databricks job | `notebooks/cross_wired_daily_job.py` |
| Databricks job definition | `databricks.yml` → job `cross_wired_daily` |

---

*Version: 1.0 — 2026-07-27 — Chicago pilot (CHI)*
