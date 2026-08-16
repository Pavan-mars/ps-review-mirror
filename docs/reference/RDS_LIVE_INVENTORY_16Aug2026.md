# RDS Live Inventory — appdb, measured 16-Aug-2026 (evening)

Source: dashboard-api `{"action":"catalog"}` (information_schema, read-only),
run by PK 16-Aug. Totals: **216 tables, 90 views, 51 empty tables**.
Row counts are as-of the capture. This file is generated — regenerate from a fresh
catalog.json rather than hand-editing.

## PS1 — 17 tables (840,300 rows), 23 views

| object | kind | rows | cols | primary key |
|---|---|---:|---:|---|
| ps1_cross_wired_daily | table | 786,525 | 40 | xw_id |
| ps1_failure_predictions | table | 47,603 | 26 | city_id, prediction_id, computed_date |
| ps1_serial_predictions | table | 6,004 | 13 | city_id, run_id, device_id, matched_serial_nbr |
| ps1_feature_importance | table | 45 | 7 | city_id, device_category, feature_name, computed_date |
| ps1_station_summary | table | 43 | 10 | city_id, facility_id, computed_date |
| ps1_prediction_explainability | table | 26 | 6 | - |
| ps1_leaderboard | table | 15 | 12 | city_id, device, model, as_of_date |
| ps1_risk_trend | table | 12 | 7 | city_id, trend_date, device_category, computed_date |
| ps1_risk_bands | table | 9 | 6 | city_id, device_category, band, computed_date |
| ps1_inference_runs | table | 7 | 19 | city_id, run_id, device_category |
| ps1_confusion | table | 3 | 7 | city_id, device_category, computed_date |
| ps1_model_performance | table | 3 | 27 | city_id, device_category, computed_date |
| ps1_threshold_sweep | table | 3 | 8 | city_id, device_category, threshold, computed_date |
| ps1_failure_summary | table | 2 | 36 | city_id, device, as_of_date |
| ps1_calibration | table | 0 | 8 | city_id, device_category, bin_lo, computed_date |
| ps1_explainability | table | 0 | 6 | city_id, prediction_id, feature_name, computed_date |
| ps1_features | table | 0 | 7 | city_id, device, feat_rank, as_of_date |
| v_ps1_device_drivers | view | - | 17 | - |
| v_ps1_label_frame | view | - | 6 | - |
| v_ps1_predictions_xw | view | - | 14 | - |
| v_ps1_predictions_xw_coverage | view | - | 5 | - |
| v_ps1_provenance_gaps | view | - | 8 | - |
| v_ps1_serving_gap | view | - | 9 | - |
| v_ps1_shap_importance | view | - | 10 | - |
| v_ps1_table_status | view | - | 5 | - |
| v_ps1_xw_act_now | view | - | 15 | - |
| v_ps1_xw_base_rate | view | - | 10 | - |
| v_ps1_xw_causation | view | - | 13 | - |
| v_ps1_xw_chronic_devices | view | - | 10 | - |
| v_ps1_xw_device_state | view | - | 15 | - |
| v_ps1_xw_facility | view | - | 9 | - |
| v_ps1_xw_flag_reason | view | - | 8 | - |
| v_ps1_xw_grain | view | - | 11 | - |
| v_ps1_xw_onset | view | - | 46 | - |
| v_ps1_xw_performance | view | - | 15 | - |
| v_ps1_xw_performance_onset | view | - | 15 | - |
| v_ps1_xw_spells | view | - | 11 | - |
| v_ps1_xw_state_mix | view | - | 8 | - |
| v_ps1_xw_summary | view | - | 17 | - |
| v_ps1_xw_tier_calibration | view | - | 9 | - |

### ps1_cross_wired_daily (table, rows=786525)

| column | type | nullable | default |
|---|---|---|---|
| xw_id | bigint(64) | N | nextval('ps1_cross_wired_daily_xw_id_seq'::regclass) |
| city_id | USER-DEFINED | N |  |
| device_type | character varying(12) | N |  |
| device_key | character varying(64) | N |  |
| device_id | character varying(40) | Y |  |
| component_serial_nbr | character varying(64) | Y |  |
| component_type | character varying(80) | Y |  |
| device_category | character varying(40) | Y |  |
| facility_id | character varying(40) | Y |  |
| operator_id | character varying(40) | Y |  |
| transit_day | date | N |  |
| event_date | date | Y |  |
| ps1_fail_prob | numeric(9) | Y |  |
| ps1_predicted | smallint(16) | Y |  |
| threshold_used | numeric(9) | Y |  |
| ps1_risk_tier | character varying(12) | Y |  |
| score | numeric(14) | Y |  |
| ps1_p95_hist | numeric(9) | Y |  |
| is_prob_anomaly | boolean | Y |  |
| will_hardware_oos_3d | smallint(16) | Y |  |
| is_coordinated_station_failure | boolean | Y |  |
| is_major_station_event | boolean | Y |  |
| station_devices_failed | integer(32) | Y |  |
| days_healthy_before_chain | numeric(10) | Y |  |
| avg_rolling_mttr_30d_min | numeric(12) | Y |  |
| avg_rolling_mttr_90d_min | numeric(12) | Y |  |
| max_downtime_ever_min | numeric(14) | Y |  |
| total_failure_days_s28 | integer(32) | Y |  |
| last_failure_date_s28 | date | Y |  |
| component_age_days | numeric(10) | Y |  |
| no_prior_failure_in_window | boolean | Y |  |
| shap_feat1 | character varying(120) | Y |  |
| shap_val1 | numeric(14) | Y |  |
| shap_feat2 | character varying(120) | Y |  |
| shap_val2 | numeric(14) | Y |  |
| shap_feat3 | character varying(120) | Y |  |
| shap_val3 | numeric(14) | Y |  |
| extra | jsonb | Y |  |
| asof_date | date | N |  |
| run_id | character varying(64) | Y |  |

### ps1_failure_predictions (table, rows=47603)

| column | type | nullable | default |
|---|---|---|---|
| city_id | USER-DEFINED | N |  |
| prediction_id | character varying(48) | N |  |
| device_category | character varying(12) | Y |  |
| device_id | character varying(20) | Y |  |
| facility_id | character varying(20) | Y |  |
| failure_probability | numeric(7) | Y |  |
| predicted_label | smallint(16) | Y |  |
| decision_threshold | numeric(7) | Y |  |
| prediction_date | date | Y |  |
| inference_ts | character varying(32) | Y |  |
| computed_date | date | N |  |
| run_id | character varying(48) | Y |  |
| model_version | character varying(24) | Y |  |
| target_col | character varying(32) | Y |  |
| risk_band | character varying(10) | Y |  |
| matched_serial_nbr | character varying(64) | Y |  |
| avg_rolling_mttr_30d_min | numeric(12) | Y |  |
| avg_rolling_mttr_90d_min | numeric(12) | Y |  |
| total_failure_days_s28 | numeric(10) | Y |  |
| max_downtime_ever_min | numeric(12) | Y |  |
| last_failure_date_s28 | character varying(32) | Y |  |
| is_coordinated_station_failure | boolean | Y |  |
| is_major_station_event | boolean | Y |  |
| station_devices_failed | numeric(10) | Y |  |
| is_prob_anomaly | boolean | Y |  |
| ps1_p95_hist | numeric(9) | Y |  |

### ps1_serial_predictions (table, rows=6004)

| column | type | nullable | default |
|---|---|---|---|
| city_id | USER-DEFINED | N |  |
| run_id | character varying(48) | N |  |
| device_id | character varying(40) | N |  |
| matched_serial_nbr | character varying(64) | N |  |
| device_category | character varying(12) | Y |  |
| component_type | character varying(48) | Y |  |
| component_age_days | numeric(10) | Y |  |
| device_failure_probability | numeric(7) | Y |  |
| attribution_weight | numeric(7) | Y |  |
| serial_risk_score | numeric(7) | Y |  |
| risk_band | character varying(10) | Y |  |
| prediction_date | date | Y |  |
| computed_date | date | N |  |

### ps1_feature_importance (table, rows=45)

| column | type | nullable | default |
|---|---|---|---|
| city_id | USER-DEFINED | N |  |
| device_category | character varying(12) | N |  |
| feature_name | character varying(60) | N |  |
| avg_importance | numeric(9) | Y |  |
| avg_shap | numeric(9) | Y |  |
| feat_rank | smallint(16) | Y |  |
| computed_date | date | N |  |

### ps1_station_summary (table, rows=43)

| column | type | nullable | default |
|---|---|---|---|
| city_id | USER-DEFINED | N |  |
| facility_id | character varying(20) | N |  |
| total_devices | integer(32) | Y |  |
| predicted_failures | integer(32) | Y |  |
| avg_risk_pct | numeric(7) | Y |  |
| critical_count | integer(32) | Y |  |
| high_count | integer(32) | Y |  |
| medium_count | integer(32) | Y |  |
| last_inference_date | date | Y |  |
| computed_date | date | N |  |

### ps1_prediction_explainability (table, rows=26)

| column | type | nullable | default |
|---|---|---|---|
| explainability_id | bigint(64) | N |  |
| prediction_id | bigint(64) | N |  |
| feature_rank | smallint(16) | N |  |
| feature_name | character varying(60) | N |  |
| shap_value | numeric(9) | N |  |
| feature_value | numeric(16) | Y |  |

### ps1_leaderboard (table, rows=15)

| column | type | nullable | default |
|---|---|---|---|
| city_id | USER-DEFINED | N |  |
| device | character varying(10) | N |  |
| model | character varying(40) | N |  |
| auc | numeric(6) | Y |  |
| ap | numeric(6) | Y |  |
| f1 | numeric(6) | Y |  |
| prec | numeric(6) | Y |  |
| rec | numeric(6) | Y |  |
| lb_rank | smallint(16) | Y |  |
| is_champion | boolean | Y | false |
| note | character varying(200) | Y |  |
| as_of_date | date | N |  |

### ps1_risk_trend (table, rows=12)

| column | type | nullable | default |
|---|---|---|---|
| city_id | USER-DEFINED | N |  |
| trend_date | date | N |  |
| device_category | character varying(12) | N |  |
| avg_prob_pct | numeric(7) | Y |  |
| failures | integer(32) | Y |  |
| total | integer(32) | Y |  |
| computed_date | date | N |  |

### ps1_risk_bands (table, rows=9)

| column | type | nullable | default |
|---|---|---|---|
| city_id | USER-DEFINED | N |  |
| device_category | character varying(12) | N |  |
| band | character varying(10) | N |  |
| device_count | integer(32) | Y |  |
| pct | numeric(6) | Y |  |
| computed_date | date | N |  |

### ps1_inference_runs (table, rows=7)

| column | type | nullable | default |
|---|---|---|---|
| city_id | USER-DEFINED | N |  |
| run_id | character varying(48) | N |  |
| run_ts | timestamp with time zone | N |  |
| run_kind | character varying(16) | N | 'batch_score'::character varying |
| device_category | character varying(12) | N |  |
| scoring_date | date | Y |  |
| endpoint_name | character varying(80) | Y |  |
| serving_image | character varying(200) | Y |  |
| model_version | character varying(24) | Y |  |
| mlflow_version | character varying(16) | Y |  |
| target_col | character varying(32) | Y |  |
| decision_threshold | numeric(7) | Y |  |
| n_devices_scored | integer(32) | Y |  |
| n_flagged | integer(32) | Y |  |
| gold_snapshot_s3 | character varying(300) | Y |  |
| status | character varying(16) | N | 'running'::character varying |
| error_text | text | Y |  |
| duration_s | numeric(10) | Y |  |
| computed_date | date | N |  |

### ps1_confusion (table, rows=3)

| column | type | nullable | default |
|---|---|---|---|
| city_id | USER-DEFINED | N |  |
| device_category | character varying(12) | N |  |
| tp | integer(32) | Y |  |
| fp | integer(32) | Y |  |
| tn | integer(32) | Y |  |
| fn | integer(32) | Y |  |
| computed_date | date | N |  |

### ps1_model_performance (table, rows=3)

| column | type | nullable | default |
|---|---|---|---|
| city_id | USER-DEFINED | N |  |
| device_category | character varying(12) | N |  |
| model_name | character varying(60) | Y |  |
| algorithm | character varying(40) | Y |  |
| train_auc | numeric(7) | Y |  |
| train_ap | numeric(7) | Y |  |
| train_f1 | numeric(7) | Y |  |
| val_auc | numeric(7) | Y |  |
| val_ap | numeric(7) | Y |  |
| val_f1 | numeric(7) | Y |  |
| test_auc | numeric(7) | Y |  |
| test_ap | numeric(7) | Y |  |
| test_f1 | numeric(7) | Y |  |
| test_prec | numeric(7) | Y |  |
| test_rec | numeric(7) | Y |  |
| decision_threshold | numeric(7) | Y |  |
| mlflow_version | character varying(16) | Y |  |
| endpoint_name | character varying(80) | Y |  |
| n_features | integer(32) | Y |  |
| quality_gate | character varying(8) | Y |  |
| promoted | boolean | Y |  |
| computed_date | date | N |  |
| target_col | character varying(32) | Y |  |
| label_revision | character varying(16) | Y |  |
| recall_floor | numeric(6) | Y |  |
| base_rate_pct | numeric(6) | Y |  |
| run_id | character varying(48) | Y |  |

### ps1_threshold_sweep (table, rows=3)

| column | type | nullable | default |
|---|---|---|---|
| city_id | USER-DEFINED | N |  |
| device_category | character varying(12) | N |  |
| threshold | numeric(5) | N |  |
| precision | numeric(7) | Y |  |
| recall | numeric(7) | Y |  |
| alert_rate | numeric(7) | Y |  |
| f1 | numeric(7) | Y |  |
| computed_date | date | N |  |

### ps1_failure_summary (table, rows=2)

| column | type | nullable | default |
|---|---|---|---|
| city_id | USER-DEFINED | N |  |
| device | character varying(10) | N |  |
| champion_model | character varying(48) | Y |  |
| test_auc | numeric(6) | Y |  |
| test_ap | numeric(6) | Y |  |
| test_accuracy | numeric(6) | Y |  |
| test_f1 | numeric(6) | Y |  |
| test_precision | numeric(6) | Y |  |
| test_recall | numeric(6) | Y |  |
| op_threshold | numeric(7) | Y |  |
| op_fleet_pct | numeric(5) | Y |  |
| op_precision | numeric(6) | Y |  |
| op_recall | numeric(6) | Y |  |
| op_f2 | numeric(6) | Y |  |
| recall_floor | numeric(6) | Y |  |
| quality_gate | character varying(8) | Y |  |
| promoted | boolean | Y | false |
| brier_raw | numeric(7) | Y |  |
| brier_cal | numeric(7) | Y |  |
| auc_cal | numeric(6) | Y |  |
| prec_at_k | numeric(6) | Y |  |
| rec_at_k | numeric(6) | Y |  |
| lift_at_k | numeric(6) | Y |  |
| map_score | numeric(6) | Y |  |
| mlflow_version | character varying(16) | Y |  |
| sm_registered | boolean | Y | false |
| endpoint_name | character varying(64) | Y |  |
| overfit_flag | boolean | Y | false |
| n_train | integer(32) | Y |  |
| n_test | integer(32) | Y |  |
| n_test_pos | integer(32) | Y |  |
| base_rate_pct | numeric(6) | Y |  |
| target | character varying(20) | Y |  |
| run_id | character varying(24) | Y |  |
| as_of_date | date | N |  |
| label_revision | character varying(16) | Y |  |

### ps1_calibration (table, rows=0)

| column | type | nullable | default |
|---|---|---|---|
| city_id | USER-DEFINED | N |  |
| device_category | character varying(12) | N |  |
| bin_lo | numeric(7) | N |  |
| bin_hi | numeric(7) | Y |  |
| pred_mean | numeric(7) | Y |  |
| actual_rate | numeric(7) | Y |  |
| n | integer(32) | Y |  |
| computed_date | date | N |  |

### ps1_explainability (table, rows=0)

| column | type | nullable | default |
|---|---|---|---|
| city_id | USER-DEFINED | N |  |
| prediction_id | character varying(48) | N |  |
| feature_name | character varying(60) | N |  |
| shap_value | numeric(10) | Y |  |
| feature_value | character varying(40) | Y |  |
| computed_date | date | N |  |

### ps1_features (table, rows=0)

| column | type | nullable | default |
|---|---|---|---|
| city_id | USER-DEFINED | N |  |
| device | character varying(10) | N |  |
| feature | character varying(48) | N |  |
| mean_abs_shap | numeric(9) | Y |  |
| pct_total | numeric(6) | Y |  |
| feat_rank | smallint(16) | N |  |
| as_of_date | date | N |  |

### v_ps1_device_drivers (view, rows=None)

| column | type | nullable | default |
|---|---|---|---|
| city_id | USER-DEFINED | Y |  |
| device_key | character varying(64) | Y |  |
| device_id | character varying(40) | Y |  |
| device_type | character varying(12) | Y |  |
| component_type | character varying(80) | Y |  |
| facility_id | character varying(40) | Y |  |
| transit_day | date | Y |  |
| ps1_fail_prob | numeric(9) | Y |  |
| ps1_risk_tier | character varying(12) | Y |  |
| threshold_used | numeric(9) | Y |  |
| will_hardware_oos_3d | smallint(16) | Y |  |
| shap_feat1 | character varying(120) | Y |  |
| shap_val1 | numeric(14) | Y |  |
| shap_feat2 | character varying(120) | Y |  |
| shap_val2 | numeric(14) | Y |  |
| shap_feat3 | character varying(120) | Y |  |
| shap_val3 | numeric(14) | Y |  |

### v_ps1_label_frame (view, rows=None)

| column | type | nullable | default |
|---|---|---|---|
| city_id | USER-DEFINED | Y |  |
| first_scored_day | date | Y |  |
| last_scored_day | date | Y |  |
| label_horizon_days | integer(32) | Y |  |
| evaluable_day | date | Y |  |
| n_scored_days | bigint(64) | Y |  |

### v_ps1_predictions_xw (view, rows=None)

| column | type | nullable | default |
|---|---|---|---|
| prediction_id | text | Y |  |
| city_id | USER-DEFINED | Y |  |
| device_category | character varying(12) | Y |  |
| device_id | character varying(40) | Y |  |
| facility_id | character varying(40) | Y |  |
| operator_id | character varying(40) | Y |  |
| failure_probability | numeric(9) | Y |  |
| predicted_label | smallint(16) | Y |  |
| decision_threshold | numeric(9) | Y |  |
| prediction_date | date | Y |  |
| inference_ts | timestamp without time zone | Y |  |
| ps1_risk_tier | character varying(12) | Y |  |
| component_type | character varying(80) | Y |  |
| will_hardware_oos_3d | smallint(16) | Y |  |

### v_ps1_predictions_xw_coverage (view, rows=None)

| column | type | nullable | default |
|---|---|---|---|
| city_id | USER-DEFINED | Y |  |
| device_category | character varying(12) | Y |  |
| n_devices | bigint(64) | Y |  |
| earliest | date | Y |  |
| latest | date | Y |  |

### v_ps1_provenance_gaps (view, rows=None)

| column | type | nullable | default |
|---|---|---|---|
| city_id | USER-DEFINED | Y |  |
| device_category | character varying(12) | Y |  |
| computed_date | date | Y |  |
| missing_run_id | boolean | Y |  |
| missing_target_col | boolean | Y |  |
| missing_label_revision | boolean | Y |  |
| missing_recall_floor | boolean | Y |  |
| assessment | text | Y |  |

### v_ps1_serving_gap (view, rows=None)

| column | type | nullable | default |
|---|---|---|---|
| city_id | USER-DEFINED | Y |  |
| device_category | character varying(12) | Y |  |
| endpoint_name | character varying(80) | Y |  |
| scorecard_mlflow_version | character varying(16) | Y |  |
| scorecard_run_id | character varying(48) | Y |  |
| serving_run_id | character varying(48) | Y |  |
| serving_run_ts | timestamp with time zone | Y |  |
| scorecard_is_serving_run | boolean | Y |  |
| assessment | text | Y |  |

### v_ps1_shap_importance (view, rows=None)

| column | type | nullable | default |
|---|---|---|---|
| city_id | USER-DEFINED | Y |  |
| device_type | character varying(12) | Y |  |
| feature_name | character varying(120) | Y |  |
| n_rows | bigint(64) | Y |  |
| mean_abs_shap | numeric | Y |  |
| mean_signed_shap | numeric | Y |  |
| max_abs_shap | numeric | Y |  |
| n_pushes_toward_failure | bigint(64) | Y |  |
| n_pushes_away | bigint(64) | Y |  |
| importance_rank | bigint(64) | Y |  |

### v_ps1_table_status (view, rows=None)

| column | type | nullable | default |
|---|---|---|---|
| table_name | text | Y |  |
| n_rows | bigint(64) | Y |  |
| superseded_by | text | Y |  |
| is_empty | boolean | Y |  |
| retired | boolean | Y |  |

### v_ps1_xw_act_now (view, rows=None)

| column | type | nullable | default |
|---|---|---|---|
| city_id | USER-DEFINED | Y |  |
| device_id | character varying(40) | Y |  |
| device_type | character varying(12) | Y |  |
| facility_id | character varying(40) | Y |  |
| last_scored_day | date | Y |  |
| ps1_fail_prob | numeric | Y |  |
| ps1_risk_tier | character varying(20) | Y |  |
| threshold_used | numeric | Y |  |
| last_oos_day | date | Y |  |
| n_spells | bigint(64) | Y |  |
| total_oos_days | bigint(64) | Y |  |
| days_since_spell_end | integer(32) | Y |  |
| device_state | text | Y |  |
| current_spell_day | bigint(64) | Y |  |
| state_note | text | Y |  |

### v_ps1_xw_base_rate (view, rows=None)

| column | type | nullable | default |
|---|---|---|---|
| city_id | USER-DEFINED | Y |  |
| device_type | character varying(12) | Y |  |
| device_days | bigint(64) | Y |  |
| eligible_days | bigint(64) | Y |  |
| stored_positive_days | bigint(64) | Y |  |
| onsets | bigint(64) | Y |  |
| stored_base_rate | numeric | Y |  |
| onset_rate_all_days | numeric | Y |  |
| onset_rate_eligible | numeric | Y |  |
| inflation_factor | numeric | Y |  |

### v_ps1_xw_causation (view, rows=None)

| column | type | nullable | default |
|---|---|---|---|
| city_id | USER-DEFINED | Y |  |
| device_type | character varying(12) | Y |  |
| n_chain | bigint(64) | Y |  |
| n_no_chain | bigint(64) | Y |  |
| critical_rate_in_chain | numeric | Y |  |
| critical_rate_no_chain | numeric | Y |  |
| critical_lift | numeric | Y |  |
| mean_prob_chain | numeric | Y |  |
| mean_prob_no_chain | numeric | Y |  |
| mean_days_healthy_before_chain | numeric | Y |  |
| mean_station_devices_failed | numeric | Y |  |
| sufficient_data | boolean | Y |  |
| min_cell | bigint(64) | Y |  |

### v_ps1_xw_chronic_devices (view, rows=None)

| column | type | nullable | default |
|---|---|---|---|
| city_id | USER-DEFINED | Y |  |
| device_type | character varying(12) | Y |  |
| device_id | character varying(40) | Y |  |
| n_spells | bigint(64) | Y |  |
| total_days_out | numeric | Y |  |
| longest_spell | bigint(64) | Y |  |
| mean_spell_days | numeric | Y |  |
| n_isolated | bigint(64) | Y |  |
| last_spell_end | date | Y |  |
| facility_id | text | Y |  |

### v_ps1_xw_device_state (view, rows=None)

| column | type | nullable | default |
|---|---|---|---|
| city_id | USER-DEFINED | Y |  |
| device_id | character varying(40) | Y |  |
| device_type | character varying(12) | Y |  |
| facility_id | character varying(40) | Y |  |
| last_scored_day | date | Y |  |
| ps1_fail_prob | numeric | Y |  |
| ps1_risk_tier | character varying(20) | Y |  |
| threshold_used | numeric | Y |  |
| last_oos_day | date | Y |  |
| n_spells | bigint(64) | Y |  |
| total_oos_days | bigint(64) | Y |  |
| days_since_spell_end | integer(32) | Y |  |
| device_state | text | Y |  |
| current_spell_day | bigint(64) | Y |  |
| state_note | text | Y |  |

### v_ps1_xw_facility (view, rows=None)

| column | type | nullable | default |
|---|---|---|---|
| city_id | USER-DEFINED | Y |  |
| facility_id | character varying(40) | Y |  |
| n_devices | bigint(64) | Y |  |
| n_device_days | bigint(64) | Y |  |
| n_critical | bigint(64) | Y |  |
| mean_fail_prob | numeric | Y |  |
| n_oos_3d | bigint(64) | Y |  |
| n_chain_days | bigint(64) | Y |  |
| last_day | date | Y |  |

### v_ps1_xw_flag_reason (view, rows=None)

| column | type | nullable | default |
|---|---|---|---|
| city_id | USER-DEFINED | Y |  |
| device_type | character varying(12) | Y |  |
| n_flagged | bigint(64) | Y |  |
| flag_on_onset | bigint(64) | Y |  |
| flag_during_spell | bigint(64) | Y |  |
| flag_no_event | bigint(64) | Y |  |
| share_during_spell | numeric | Y |  |
| share_on_onset | numeric | Y |  |

### v_ps1_xw_grain (view, rows=None)

| column | type | nullable | default |
|---|---|---|---|
| city_id | USER-DEFINED | Y |  |
| device_type | character varying(12) | Y |  |
| device_key | character varying(64) | Y |  |
| component_serial_nbr | character varying(64) | Y |  |
| transit_day | date | Y |  |
| n_rows | bigint(64) | Y |  |
| n_probs | bigint(64) | Y |  |
| n_tiers | bigint(64) | Y |  |
| n_thresholds | bigint(64) | Y |  |
| n_categories | bigint(64) | Y |  |
| n_runs | bigint(64) | Y |  |

### v_ps1_xw_onset (view, rows=None)

| column | type | nullable | default |
|---|---|---|---|
| xw_id | bigint(64) | Y |  |
| city_id | USER-DEFINED | Y |  |
| device_type | character varying(12) | Y |  |
| device_key | character varying(64) | Y |  |
| device_id | character varying(40) | Y |  |
| component_serial_nbr | character varying(64) | Y |  |
| component_type | character varying(80) | Y |  |
| device_category | character varying(40) | Y |  |
| facility_id | character varying(40) | Y |  |
| operator_id | character varying(40) | Y |  |
| transit_day | date | Y |  |
| event_date | date | Y |  |
| ps1_fail_prob | numeric(9) | Y |  |
| ps1_predicted | smallint(16) | Y |  |
| threshold_used | numeric(9) | Y |  |
| ps1_risk_tier | character varying(12) | Y |  |
| score | numeric(14) | Y |  |
| ps1_p95_hist | numeric(9) | Y |  |
| is_prob_anomaly | boolean | Y |  |
| will_hardware_oos_3d | smallint(16) | Y |  |
| is_coordinated_station_failure | boolean | Y |  |
| is_major_station_event | boolean | Y |  |
| station_devices_failed | integer(32) | Y |  |
| days_healthy_before_chain | numeric(10) | Y |  |
| avg_rolling_mttr_30d_min | numeric(12) | Y |  |
| avg_rolling_mttr_90d_min | numeric(12) | Y |  |
| max_downtime_ever_min | numeric(14) | Y |  |
| total_failure_days_s28 | integer(32) | Y |  |
| last_failure_date_s28 | date | Y |  |
| component_age_days | numeric(10) | Y |  |
| no_prior_failure_in_window | boolean | Y |  |
| shap_feat1 | character varying(120) | Y |  |
| shap_val1 | numeric(14) | Y |  |
| shap_feat2 | character varying(120) | Y |  |
| shap_val2 | numeric(14) | Y |  |
| shap_feat3 | character varying(120) | Y |  |
| shap_val3 | numeric(14) | Y |  |
| extra | jsonb | Y |  |
| asof_date | date | Y |  |
| run_id | character varying(64) | Y |  |
| prev_label | smallint(16) | Y |  |
| spell_grp | bigint(64) | Y |  |
| is_onset | integer(32) | Y |  |
| in_spell | boolean | Y |  |
| spell_id | bigint(64) | Y |  |
| spell_day | bigint(64) | Y |  |

### v_ps1_xw_performance (view, rows=None)

| column | type | nullable | default |
|---|---|---|---|
| city_id | USER-DEFINED | Y |  |
| device_type | character varying(12) | Y |  |
| n_scored | bigint(64) | Y |  |
| n_pos | bigint(64) | Y |  |
| tp | bigint(64) | Y |  |
| fp | bigint(64) | Y |  |
| fn | bigint(64) | Y |  |
| tn | bigint(64) | Y |  |
| threshold_min | numeric | Y |  |
| threshold_max | numeric | Y |  |
| base_rate | numeric | Y |  |
| precision_at_threshold | numeric | Y |  |
| recall_at_threshold | numeric | Y |  |
| f1_at_threshold | numeric | Y |  |
| precision_lift | numeric | Y |  |

### v_ps1_xw_performance_onset (view, rows=None)

| column | type | nullable | default |
|---|---|---|---|
| city_id | USER-DEFINED | Y |  |
| device_type | character varying(12) | Y |  |
| n_eligible | bigint(64) | Y |  |
| n_pos | bigint(64) | Y |  |
| tp | bigint(64) | Y |  |
| fp | bigint(64) | Y |  |
| fn | bigint(64) | Y |  |
| tn | bigint(64) | Y |  |
| threshold_used | numeric | Y |  |
| base_rate | numeric | Y |  |
| flagged_share | numeric | Y |  |
| precision_at_threshold | numeric | Y |  |
| recall_at_threshold | numeric | Y |  |
| f1_at_threshold | numeric | Y |  |
| precision_lift | numeric | Y |  |

### v_ps1_xw_spells (view, rows=None)

| column | type | nullable | default |
|---|---|---|---|
| city_id | USER-DEFINED | Y |  |
| device_type | character varying(12) | Y |  |
| device_id | character varying(40) | Y |  |
| component_serial_nbr | character varying(64) | Y |  |
| spell_id | bigint(64) | Y |  |
| spell_start | date | Y |  |
| spell_end | date | Y |  |
| spell_days | bigint(64) | Y |  |
| is_isolated_event | boolean | Y |  |
| mean_prob_in_spell | numeric | Y |  |
| facility_id | text | Y |  |

### v_ps1_xw_state_mix (view, rows=None)

| column | type | nullable | default |
|---|---|---|---|
| city_id | USER-DEFINED | Y |  |
| device_type | character varying(12) | Y |  |
| ps1_risk_tier | character varying(20) | Y |  |
| device_state | text | Y |  |
| n_devices | bigint(64) | Y |  |
| share_of_tier | numeric | Y |  |
| mean_prob | numeric | Y |  |
| mean_days_since_spell_end | numeric | Y |  |

### v_ps1_xw_summary (view, rows=None)

| column | type | nullable | default |
|---|---|---|---|
| city_id | USER-DEFINED | Y |  |
| device_type | character varying(12) | Y |  |
| n_rows | bigint(64) | Y |  |
| n_device_keys | bigint(64) | Y |  |
| n_serials | bigint(64) | Y |  |
| first_day | date | Y |  |
| last_day | date | Y |  |
| n_critical | bigint(64) | Y |  |
| n_high | bigint(64) | Y |  |
| n_medium | bigint(64) | Y |  |
| n_low | bigint(64) | Y |  |
| n_device_ids | bigint(64) | Y |  |
| n_facilities | bigint(64) | Y |  |
| n_with_shap | bigint(64) | Y |  |
| n_positive_label | bigint(64) | Y |  |
| label_base_rate | numeric | Y |  |
| asof_date | date | Y |  |

### v_ps1_xw_tier_calibration (view, rows=None)

| column | type | nullable | default |
|---|---|---|---|
| city_id | USER-DEFINED | Y |  |
| device_type | character varying(12) | Y |  |
| ps1_risk_tier | character varying(12) | Y |  |
| n_rows | bigint(64) | Y |  |
| n_positive | bigint(64) | Y |  |
| positive_rate | numeric | Y |  |
| mean_predicted_prob | numeric | Y |  |
| min_prob | numeric | Y |  |
| max_prob | numeric | Y |  |

## PS2 — 72 tables (334,865 rows), 5 views

| object | kind | rows | cols | primary key |
|---|---|---:|---:|---|
| ps2_conditional_prob_serial | table | 89,990 | 13 | - |
| ps2_v2_cofailure_clusters | table | 83,184 | 24 | city_id, event_date, cluster_scope, cluster_id, device_category |
| ps2_v2_repair_effectiveness | table | 38,395 | 21 | city_id, repair_id |
| ps2_phi_matrix_serial | table | 30,430 | 9 | - |
| ps2_business_impact_device | table | 18,692 | 10 | - |
| ps2_recurrence_device | table | 18,692 | 9 | - |
| ps2_association_rules_serial | table | 11,046 | 12 | - |
| ps2_business_impact | table | 4,673 | 9 | city_id, device_id, computed_date |
| ps2_recurrence | table | 4,673 | 5 | city_id, device_id, computed_date |
| ps2_business_impact_serial | table | 4,522 | 9 | - |
| ps2_chronic_recurrence_serial | table | 4,522 | 13 | - |
| ps2_recurrence_serial | table | 4,522 | 8 | - |
| ps2_markov_self_transition_serial | table | 4,486 | 8 | - |
| ps2_hmm_regimes_serial | table | 4,415 | 11 | - |
| ps2_leadlag_timing_serial | table | 2,505 | 13 | - |
| ps2_v2_device_deterioration | table | 2,208 | 26 | city_id, device_id, event_date, alert_reason |
| ps2_phi_outliers_serial | table | 1,532 | 12 | - |
| ps2_device_cascades | table | 1,000 | 12 | id |
| ps2_association_rules_device | table | 736 | 11 | - |
| ps2_v2_customer_exposure | table | 633 | 19 | city_id, event_date, device_category |
| ps2_v2_daily_oos_trend | table | 633 | 23 | city_id, event_date, device_category |
| ps2_v25_failure_label_daily | table | 624 | 24 | city_id, label_date, device_category |
| ps2_conditional_prob | table | 450 | 12 | city_id, sub_a, sub_b, window_bucket, computed_date |
| ps2_leadlag_timing_device | table | 320 | 12 | - |
| ps2_facility_contagion_facility | table | 228 | 9 | - |
| ps2_load_audit | table | 215 | 11 | id |
| ps2_device_catalog | table | 200 | 16 | city_id, device_id, computed_date |
| ps2_subsystem_associations | table | 184 | 9 | id |
| ps2_v2_pattern_drift | table | 140 | 24 | city_id, device_category, component_subsystem, next_subsystem |
| ps2_v2_leadlag_timing | table | 123 | 23 | city_id, device_category, component_subsystem, next_subsystem |
| ps2_v2_precursor_patterns | table | 123 | 33 | city_id, device_category, component_subsystem, next_subsystem |
| ps2_network_centrality_subsystem | table | 108 | 11 | - |
| ps2_phi_matrix | table | 100 | 8 | city_id, sub_a, sub_b, computed_date |
| ps2_cascade_sankey_subsystem | table | 80 | 10 | - |
| ps2_leadlag_timing | table | 80 | 9 | city_id, sub_a, sub_b, computed_date |
| ps2_v2_component_serial_patterns | table | 78 | 28 | city_id, device_category, component_subsystem, component_serial_id |
| ps2_markov_transitions | table | 36 | 8 | city_id, from_sub, to_sub, computed_date |
| ps2_network_centrality | table | 27 | 9 | city_id, node_id, scope, computed_date |
| ps2_top_devices | table | 21 | 11 | city_id, device_id, computed_date |
| ps2_cascade_velocity_device | table | 20 | 9 | - |
| ps2_error_code_transitions | table | 20 | 5 | city_id, from_code, to_code, computed_date |
| ps2_error_codes | table | 20 | 6 | city_id, error_code, computed_date |
| ps2_v2_topology_nodes | table | 19 | 21 | city_id, device_category, subsystem |
| ps2_v25_run_quality | table | 16 | 19 | city_id, check_name |
| ps2_v2_oos_governance | table | 16 | 19 | city_id, device_category, oos_evidence_class, failure_evidence_class |
| ps2_cascade_paths | table | 14 | 9 | city_id, path_rank, computed_date |
| ps2_hmm_regimes_device | table | 12 | 9 | - |
| ps2_cascade_window_summary | table | 10 | 8 | city_id, window_bucket, computed_date |
| ps2_ignition_termination_subsystem | table | 10 | 8 | - |
| ps2_window_detail | table | 10 | 10 | city_id, window_bucket, computed_date |
| ps2_ignition_termination | table | 9 | 9 | city_id, subsystem, computed_date |
| ps2_subsystem_hub_summary | table | 9 | 5 | city_id, node_id, computed_date |
| ps2_v25_failure_horizon_profile | table | 9 | 19 | city_id, device_category, lead_day |
| ps2_cascade_velocity | table | 5 | 6 | city_id, window_bucket, computed_date |
| ps2_v25_category_profile | table | 5 | 21 | city_id, device_category_raw, device_category |
| ps2_v25_failure_definition_alignment | table | 4 | 22 | city_id, device_category |
| ps2_v25_failure_label_summary | table | 4 | 29 | city_id, device_category |
| ps2_v25_ps1_model_performance | table | 4 | 32 | city_id, device_category |
| ps2_cascade_velocity_by_age_serial | table | 3 | 8 | - |
| ps2_hmm_regimes | table | 3 | 6 | city_id, regime, computed_date |
| ps2_v25_ps1_label_parity | table | 3 | 24 | city_id, device_category |
| ps2_v2_cross_ps_alignment | table | 3 | 21 | city_id, source |
| ps2_cascade_path_explainability | table | 2 | 7 | - |
| ps2_cascade_risk_assessments | table | 2 | 13 | - |
| ps2_facility_contagion_summary | table | 2 | 10 | city_id, computed_date |
| ps2_subsystem_hub_edges | table | 2 | 5 | city_id, source_sub, target_sub, computed_date |
| ps2_cross_ps_attribution_device | table | 1 | 13 | - |
| ps2_cross_ps_attribution_serial | table | 1 | 14 | - |
| ps2_suppression_summary_serial | table | 1 | 9 | - |
| ps2_cascade_chains_daily | table | 0 | 12 | id |
| ps2_device_cmdb_map | table | 0 | 9 | - |
| ps2_facility_contagion_daily | table | 0 | 7 | city_id, facility_id, contagion_date |
| v_ps2_device_cascade | view | - | 11 | - |
| v_ps2_facility_contagion_facility | view | - | 8 | - |
| v_ps2_ignition_termination_subsystem | view | - | 7 | - |
| v_ps2_network_centrality | view | - | 13 | - |
| v_ps2_v25_status | view | - | 7 | - |

### ps2_conditional_prob_serial (table, rows=89990)

| column | type | nullable | default |
|---|---|---|---|
| serial_id | text | Y |  |
| sub_a | text | Y |  |
| sub_b | text | Y |  |
| window | text | Y |  |
| n_a | bigint(64) | Y |  |
| n_ab | bigint(64) | Y |  |
| p_b_given_a | double precision(53) | Y |  |
| grain | text | Y |  |
| run_id | text | Y |  |
| computed_date | text | Y |  |
| notebook_version | text | Y |  |
| city_id | text | Y |  |
| window_bucket | text | Y |  |

### ps2_v2_cofailure_clusters (table, rows=83184)

| column | type | nullable | default |
|---|---|---|---|
| city_id | USER-DEFINED | N |  |
| event_date | date | N |  |
| cluster_scope | text | N |  |
| cluster_id | text | N |  |
| device_category | text | N |  |
| facility_id | text | Y |  |
| cofailing_devices | bigint(64) | Y |  |
| hardware_oos_onsets | bigint(64) | Y |  |
| observed_group_devices | double precision(53) | Y |  |
| cofailure_share | double precision(53) | Y |  |
| coordinated_station_flag | boolean | Y |  |
| major_station_flag | boolean | Y |  |
| run_id | text | Y |  |
| run_ts_utc | timestamp without time zone | Y |  |
| as_of_ts | timestamp without time zone | Y |  |
| source_start_ts | timestamp without time zone | Y |  |
| source_end_ts | timestamp without time zone | Y |  |
| notebook_version | text | Y |  |
| quality_status | text | Y |  |
| run_mode | text | Y |  |
| run_disposition | text | Y |  |
| output_scope | text | Y |  |
| is_production | boolean | Y |  |
| computed_date | date | Y |  |

### ps2_v2_repair_effectiveness (table, rows=38395)

| column | type | nullable | default |
|---|---|---|---|
| city_id | USER-DEFINED | N |  |
| repair_id | text | N |  |
| maintenance_component_subsystem | text | Y |  |
| ledger_type | text | Y |  |
| maintenance_date | date | Y |  |
| pre_30d_oos_onsets | bigint(64) | Y |  |
| post_30d_oos_onsets | bigint(64) | Y |  |
| post_vs_pre_change | double precision(53) | Y |  |
| interpretation_note | text | Y |  |
| run_id | text | Y |  |
| run_ts_utc | timestamp without time zone | Y |  |
| as_of_ts | timestamp without time zone | Y |  |
| source_start_ts | timestamp without time zone | Y |  |
| source_end_ts | timestamp without time zone | Y |  |
| notebook_version | text | Y |  |
| quality_status | text | Y |  |
| run_mode | text | Y |  |
| run_disposition | text | Y |  |
| output_scope | text | Y |  |
| is_production | boolean | Y |  |
| computed_date | date | Y |  |

### ps2_phi_matrix_serial (table, rows=30430)

| column | type | nullable | default |
|---|---|---|---|
| serial_id | text | Y |  |
| sub_a | text | Y |  |
| sub_b | text | Y |  |
| phi | double precision(53) | Y |  |
| grain | text | Y |  |
| run_id | text | Y |  |
| computed_date | text | Y |  |
| notebook_version | text | Y |  |
| city_id | text | Y |  |

### ps2_business_impact_device (table, rows=18692)

| column | type | nullable | default |
|---|---|---|---|
| entity_id | text | Y |  |
| cascade_days | bigint(64) | Y |  |
| total_impact | bigint(64) | Y |  |
| avg_impact | double precision(53) | Y |  |
| mars_device_category | text | Y |  |
| grain | text | Y |  |
| run_id | text | Y |  |
| computed_date | text | Y |  |
| notebook_version | text | Y |  |
| city_id | text | Y |  |

### ps2_recurrence_device (table, rows=18692)

| column | type | nullable | default |
|---|---|---|---|
| device_id | text | Y |  |
| cascade_days | bigint(64) | Y |  |
| chronic | boolean | Y |  |
| mars_device_category | text | Y |  |
| grain | text | Y |  |
| run_id | text | Y |  |
| computed_date | text | Y |  |
| notebook_version | text | Y |  |
| city_id | text | Y |  |

### ps2_association_rules_serial (table, rows=11046)

| column | type | nullable | default |
|---|---|---|---|
| serial_id | text | Y |  |
| antecedents | text | Y |  |
| consequents | text | Y |  |
| support | double precision(53) | Y |  |
| confidence | double precision(53) | Y |  |
| lift | double precision(53) | Y |  |
| conviction | double precision(53) | Y |  |
| grain | text | Y |  |
| run_id | text | Y |  |
| computed_date | text | Y |  |
| notebook_version | text | Y |  |
| city_id | text | Y |  |

### ps2_business_impact (table, rows=4673)

| column | type | nullable | default |
|---|---|---|---|
| city_id | USER-DEFINED | N |  |
| device_id | character varying(20) | N |  |
| category | character varying(12) | Y |  |
| total_impact | numeric(14) | Y |  |
| cascade_days | integer(32) | Y |  |
| avg_impact | numeric(10) | Y |  |
| max_impact | numeric(12) | Y |  |
| impact_rank | smallint(16) | Y |  |
| computed_date | date | N |  |

### ps2_recurrence (table, rows=4673)

| column | type | nullable | default |
|---|---|---|---|
| city_id | USER-DEFINED | N |  |
| device_id | character varying(40) | N |  |
| cascade_days | integer(32) | Y |  |
| chronic | boolean | Y |  |
| computed_date | date | N |  |

### ps2_business_impact_serial (table, rows=4522)

| column | type | nullable | default |
|---|---|---|---|
| entity_id | text | Y |  |
| cascade_days | bigint(64) | Y |  |
| total_impact | bigint(64) | Y |  |
| avg_impact | double precision(53) | Y |  |
| grain | text | Y |  |
| run_id | text | Y |  |
| computed_date | text | Y |  |
| notebook_version | text | Y |  |
| city_id | text | Y |  |

### ps2_chronic_recurrence_serial (table, rows=4522)

| column | type | nullable | default |
|---|---|---|---|
| serial_id | text | Y |  |
| device_category | text | Y |  |
| reference_period_days | bigint(64) | Y |  |
| reference_period_source | text | Y |  |
| n_cascades | bigint(64) | Y |  |
| recurrence_rate_per_day | double precision(53) | Y |  |
| peer_pct_rank | double precision(53) | Y |  |
| chronicity_flag | boolean | Y |  |
| grain | text | Y |  |
| run_id | text | Y |  |
| computed_date | text | Y |  |
| notebook_version | text | Y |  |
| city_id | text | Y |  |

### ps2_recurrence_serial (table, rows=4522)

| column | type | nullable | default |
|---|---|---|---|
| serial_id | text | Y |  |
| cascade_days | bigint(64) | Y |  |
| chronic | boolean | Y |  |
| grain | text | Y |  |
| run_id | text | Y |  |
| computed_date | text | Y |  |
| notebook_version | text | Y |  |
| city_id | text | Y |  |

### ps2_markov_self_transition_serial (table, rows=4486)

| column | type | nullable | default |
|---|---|---|---|
| serial_id | text | Y |  |
| n_chains | bigint(64) | Y |  |
| self_transition_rate | double precision(53) | Y |  |
| grain | text | Y |  |
| run_id | text | Y |  |
| computed_date | text | Y |  |
| notebook_version | text | Y |  |
| city_id | text | Y |  |

### ps2_hmm_regimes_serial (table, rows=4415)

| column | type | nullable | default |
|---|---|---|---|
| serial_id | text | Y |  |
| n_obs | bigint(64) | Y |  |
| pct_time_critical | double precision(53) | Y |  |
| mean_chain_length | double precision(53) | Y |  |
| converged | boolean | Y |  |
| n_iter_run | bigint(64) | Y |  |
| grain | text | Y |  |
| run_id | text | Y |  |
| computed_date | text | Y |  |
| notebook_version | text | Y |  |
| city_id | text | Y |  |

### ps2_leadlag_timing_serial (table, rows=2505)

| column | type | nullable | default |
|---|---|---|---|
| serial_id | text | Y |  |
| sub_a | text | Y |  |
| sub_b | text | Y |  |
| n | bigint(64) | Y |  |
| mean | double precision(53) | Y |  |
| median | double precision(53) | Y |  |
| p25 | double precision(53) | Y |  |
| p75 | double precision(53) | Y |  |
| grain | text | Y |  |
| run_id | text | Y |  |
| computed_date | text | Y |  |
| notebook_version | text | Y |  |
| city_id | text | Y |  |

### ps2_v2_device_deterioration (table, rows=2208)

| column | type | nullable | default |
|---|---|---|---|
| city_id | USER-DEFINED | N |  |
| device_id | text | N |  |
| device_category | text | Y |  |
| event_date | date | N |  |
| hardware_oos_onsets | bigint(64) | Y |  |
| hardware_oos_minutes | double precision(53) | Y |  |
| validated_failure_onsets | bigint(64) | Y |  |
| baseline_mean_28d | double precision(53) | Y |  |
| baseline_std_28d | double precision(53) | Y |  |
| oos_zscore_28d | double precision(53) | Y |  |
| alert_reason | text | N |  |
| run_id | text | Y |  |
| run_ts_utc | timestamp without time zone | Y |  |
| as_of_ts | timestamp without time zone | Y |  |
| source_start_ts | timestamp without time zone | Y |  |
| source_end_ts | timestamp without time zone | Y |  |
| notebook_version | text | Y |  |
| quality_status | text | Y |  |
| run_mode | text | Y |  |
| run_disposition | text | Y |  |
| output_scope | text | Y |  |
| is_production | boolean | Y |  |
| computed_date | date | Y |  |
| hardware_oos_union_minutes | double precision(53) | Y |  |
| hardware_oos_onsets_distinct_interval | bigint(64) | Y |  |
| oos_minutes_overlap_factor | double precision(53) | Y |  |

### ps2_phi_outliers_serial (table, rows=1532)

| column | type | nullable | default |
|---|---|---|---|
| serial_id | text | Y |  |
| sub_a | text | Y |  |
| sub_b | text | Y |  |
| phi | double precision(53) | Y |  |
| pair_mean_phi | double precision(53) | Y |  |
| pair_std_phi | double precision(53) | Y |  |
| z_score | double precision(53) | Y |  |
| grain | text | Y |  |
| run_id | text | Y |  |
| computed_date | text | Y |  |
| notebook_version | text | Y |  |
| city_id | text | Y |  |

### ps2_device_cascades (table, rows=1000)

| column | type | nullable | default |
|---|---|---|---|
| id | bigint(64) | N | nextval('ps2_device_cascades_id_seq'::regclass) |
| city_id | USER-DEFINED | N |  |
| device_id | character varying(20) | N |  |
| transit_day | date | N |  |
| subsystem_chain | character varying(220) | Y |  |
| event_code_chain | character varying(220) | Y |  |
| severity_chain | character varying(140) | Y |  |
| chain_length | integer(32) | Y |  |
| chain_span_min | numeric(10) | Y |  |
| first_subsystem | character varying(30) | Y |  |
| last_subsystem | character varying(30) | Y |  |
| computed_date | date | N |  |

### ps2_association_rules_device (table, rows=736)

| column | type | nullable | default |
|---|---|---|---|
| antecedents | text | Y |  |
| consequents | text | Y |  |
| support | double precision(53) | Y |  |
| confidence | double precision(53) | Y |  |
| lift | double precision(53) | Y |  |
| conviction | double precision(53) | Y |  |
| grain | text | Y |  |
| run_id | text | Y |  |
| computed_date | text | Y |  |
| notebook_version | text | Y |  |
| city_id | text | Y |  |

### ps2_v2_customer_exposure (table, rows=633)

| column | type | nullable | default |
|---|---|---|---|
| city_id | USER-DEFINED | N |  |
| event_date | date | N |  |
| device_category | text | N |  |
| hardware_oos_onsets | bigint(64) | Y |  |
| hardware_oos_minutes | double precision(53) | Y |  |
| transactions_exposed | double precision(53) | Y |  |
| revenue_cents_exposed | double precision(53) | Y |  |
| run_id | text | Y |  |
| run_ts_utc | timestamp without time zone | Y |  |
| as_of_ts | timestamp without time zone | Y |  |
| source_start_ts | timestamp without time zone | Y |  |
| source_end_ts | timestamp without time zone | Y |  |
| notebook_version | text | Y |  |
| quality_status | text | Y |  |
| run_mode | text | Y |  |
| run_disposition | text | Y |  |
| output_scope | text | Y |  |
| is_production | boolean | Y |  |
| computed_date | date | Y |  |

### ps2_v2_daily_oos_trend (table, rows=633)

| column | type | nullable | default |
|---|---|---|---|
| city_id | USER-DEFINED | N |  |
| event_date | date | N |  |
| device_category | text | N |  |
| hardware_oos_onsets | bigint(64) | Y |  |
| affected_devices | bigint(64) | Y |  |
| hardware_oos_minutes | double precision(53) | Y |  |
| validated_failure_onsets | bigint(64) | Y |  |
| chargeable_oos_onsets | bigint(64) | Y |  |
| run_id | text | Y |  |
| run_ts_utc | timestamp without time zone | Y |  |
| as_of_ts | timestamp without time zone | Y |  |
| source_start_ts | timestamp without time zone | Y |  |
| source_end_ts | timestamp without time zone | Y |  |
| notebook_version | text | Y |  |
| quality_status | text | Y |  |
| run_mode | text | Y |  |
| run_disposition | text | Y |  |
| output_scope | text | Y |  |
| is_production | boolean | Y |  |
| computed_date | date | Y |  |
| hardware_oos_union_minutes | double precision(53) | Y |  |
| hardware_oos_onsets_distinct_interval | bigint(64) | Y |  |
| oos_minutes_overlap_factor | double precision(53) | Y |  |

### ps2_v25_failure_label_daily (table, rows=624)

| column | type | nullable | default |
|---|---|---|---|
| city_id | USER-DEFINED | N |  |
| label_date | date | N |  |
| device_category | text | N |  |
| eligible_device_days | bigint(64) | Y |  |
| positive_device_days | bigint(64) | Y |  |
| eligible_devices | bigint(64) | Y |  |
| positive_devices | bigint(64) | Y |  |
| future_hardware_oos_set_events | bigint(64) | Y |  |
| median_hours_to_next_oos | double precision(53) | Y |  |
| p90_hours_to_next_oos | double precision(53) | Y |  |
| negative_device_days | bigint(64) | Y |  |
| label_positive_rate | double precision(53) | Y |  |
| run_id | text | Y |  |
| run_ts_utc | timestamp without time zone | Y |  |
| as_of_ts | timestamp without time zone | Y |  |
| source_start_ts | timestamp without time zone | Y |  |
| source_end_ts | timestamp without time zone | Y |  |
| notebook_version | text | Y |  |
| quality_status | text | Y |  |
| run_mode | text | Y |  |
| run_disposition | text | Y |  |
| output_scope | text | Y |  |
| is_production | boolean | Y |  |
| computed_date | date | Y |  |

### ps2_conditional_prob (table, rows=450)

| column | type | nullable | default |
|---|---|---|---|
| city_id | USER-DEFINED | N |  |
| sub_a | character varying(30) | N |  |
| sub_b | character varying(30) | N |  |
| window_bucket | character varying(12) | N |  |
| p_b_given_a | numeric(7) | Y |  |
| computed_date | date | N |  |
| window | text | Y |  |
| n_a | bigint(64) | Y |  |
| n_ab | bigint(64) | Y |  |
| grain | text | Y |  |
| run_id | text | Y |  |
| notebook_version | text | Y |  |

### ps2_leadlag_timing_device (table, rows=320)

| column | type | nullable | default |
|---|---|---|---|
| sub_a | text | Y |  |
| sub_b | text | Y |  |
| n | bigint(64) | Y |  |
| mean | double precision(53) | Y |  |
| median | double precision(53) | Y |  |
| p25 | double precision(53) | Y |  |
| p75 | double precision(53) | Y |  |
| grain | text | Y |  |
| run_id | text | Y |  |
| computed_date | text | Y |  |
| notebook_version | text | Y |  |
| city_id | text | Y |  |

### ps2_facility_contagion_facility (table, rows=228)

| column | type | nullable | default |
|---|---|---|---|
| facility_id | text | Y |  |
| cascade_days | bigint(64) | Y |  |
| distinct_devices | bigint(64) | Y |  |
| contagion_rate | double precision(53) | Y |  |
| grain | text | Y |  |
| run_id | text | Y |  |
| computed_date | text | Y |  |
| notebook_version | text | Y |  |
| city_id | text | Y |  |

### ps2_load_audit (table, rows=215)

| column | type | nullable | default |
|---|---|---|---|
| id | bigint(64) | N | nextval('ps2_load_audit_id_seq'::regclass) |
| table_name | text | N |  |
| grain | text | Y |  |
| computed_date | text | Y |  |
| run_id | text | Y |  |
| rows_loaded | integer(32) | Y |  |
| columns_added | text | Y |  |
| columns_conflicted | text | Y |  |
| status | text | Y |  |
| error_detail | text | Y |  |
| loaded_at | timestamp with time zone | Y | now() |

### ps2_device_catalog (table, rows=200)

| column | type | nullable | default |
|---|---|---|---|
| city_id | USER-DEFINED | N |  |
| device_id | character varying(20) | N |  |
| device_name | character varying(80) | Y |  |
| serial | character varying(60) | Y |  |
| category | character varying(12) | Y |  |
| control_group | character varying(60) | Y |  |
| facility | character varying(100) | Y |  |
| operator | character varying(80) | Y |  |
| cascade_days | integer(32) | Y |  |
| avg_chain_len | numeric(7) | Y |  |
| max_chain_len | integer(32) | Y |  |
| dom_subsystem | character varying(30) | Y |  |
| dom_error_code | character varying(20) | Y |  |
| worst_cascade_path | character varying(220) | Y |  |
| worst_window | character varying(12) | Y |  |
| computed_date | date | N |  |

### ps2_subsystem_associations (table, rows=184)

| column | type | nullable | default |
|---|---|---|---|
| id | bigint(64) | N | nextval('ps2_subsystem_associations_id_seq'::regclass) |
| city_id | USER-DEFINED | N |  |
| antecedent_subsystem | character varying(30) | N |  |
| consequent_subsystem | character varying(30) | N |  |
| support | numeric(6) | Y |  |
| confidence | numeric(6) | Y |  |
| lift | numeric(8) | Y |  |
| conviction | numeric(8) | Y |  |
| computed_date | date | N |  |

### ps2_v2_pattern_drift (table, rows=140)

| column | type | nullable | default |
|---|---|---|---|
| city_id | USER-DEFINED | N |  |
| device_category | text | N |  |
| component_subsystem | text | N |  |
| next_subsystem | text | N |  |
| baseline_count | bigint(64) | Y |  |
| recent_count | bigint(64) | Y |  |
| baseline_pre_oos_count | bigint(64) | Y |  |
| recent_pre_oos_count | bigint(64) | Y |  |
| baseline_pre_oos_rate | double precision(53) | Y |  |
| recent_pre_oos_rate | double precision(53) | Y |  |
| rate_change | double precision(53) | Y |  |
| drift_flag | boolean | Y |  |
| run_id | text | Y |  |
| run_ts_utc | timestamp without time zone | Y |  |
| as_of_ts | timestamp without time zone | Y |  |
| source_start_ts | timestamp without time zone | Y |  |
| source_end_ts | timestamp without time zone | Y |  |
| notebook_version | text | Y |  |
| quality_status | text | Y |  |
| run_mode | text | Y |  |
| run_disposition | text | Y |  |
| output_scope | text | Y |  |
| is_production | boolean | Y |  |
| computed_date | date | Y |  |

### ps2_v2_leadlag_timing (table, rows=123)

| column | type | nullable | default |
|---|---|---|---|
| city_id | USER-DEFINED | N |  |
| device_category | text | N |  |
| component_subsystem | text | N |  |
| next_subsystem | text | N |  |
| pattern_key | text | Y |  |
| edge_support | bigint(64) | Y |  |
| median_edge_lag_seconds | bigint(64) | Y |  |
| p95_edge_lag_seconds | bigint(64) | Y |  |
| pre_oos_rate | double precision(53) | Y |  |
| pre_oos_lift_vs_category | double precision(53) | Y |  |
| evidence_tier | text | Y |  |
| run_id | text | Y |  |
| run_ts_utc | timestamp without time zone | Y |  |
| as_of_ts | timestamp without time zone | Y |  |
| source_start_ts | timestamp without time zone | Y |  |
| source_end_ts | timestamp without time zone | Y |  |
| notebook_version | text | Y |  |
| quality_status | text | Y |  |
| run_mode | text | Y |  |
| run_disposition | text | Y |  |
| output_scope | text | Y |  |
| is_production | boolean | Y |  |
| computed_date | date | Y |  |

### ps2_v2_precursor_patterns (table, rows=123)

| column | type | nullable | default |
|---|---|---|---|
| city_id | USER-DEFINED | N |  |
| device_category | text | N |  |
| component_subsystem | text | N |  |
| next_subsystem | text | N |  |
| edge_support | bigint(64) | Y |  |
| pre_oos_edge_count | bigint(64) | Y |  |
| validated_failure_edge_count | bigint(64) | Y |  |
| median_edge_lag_seconds | bigint(64) | Y |  |
| p95_edge_lag_seconds | bigint(64) | Y |  |
| baseline_edge_count | bigint(64) | Y |  |
| recent_edge_count | bigint(64) | Y |  |
| baseline_pre_oos_count | bigint(64) | Y |  |
| recent_pre_oos_count | bigint(64) | Y |  |
| baseline_pre_oos_rate | double precision(53) | Y |  |
| pre_oos_rate | double precision(53) | Y |  |
| pre_oos_wilson_lower_95 | double precision(53) | Y |  |
| pre_oos_lift_vs_category | double precision(53) | Y |  |
| validated_failure_rate | double precision(53) | Y |  |
| pattern_key | text | Y |  |
| evidence_tier | text | Y |  |
| priority_score | double precision(53) | Y |  |
| run_id | text | Y |  |
| run_ts_utc | timestamp without time zone | Y |  |
| as_of_ts | timestamp without time zone | Y |  |
| source_start_ts | timestamp without time zone | Y |  |
| source_end_ts | timestamp without time zone | Y |  |
| notebook_version | text | Y |  |
| quality_status | text | Y |  |
| run_mode | text | Y |  |
| run_disposition | text | Y |  |
| output_scope | text | Y |  |
| is_production | boolean | Y |  |
| computed_date | date | Y |  |

### ps2_network_centrality_subsystem (table, rows=108)

| column | type | nullable | default |
|---|---|---|---|
| scope | text | Y |  |
| subsystem | text | Y |  |
| betweenness | double precision(53) | Y |  |
| pagerank | double precision(53) | Y |  |
| in_degree | bigint(64) | Y |  |
| out_degree | bigint(64) | Y |  |
| grain | text | Y |  |
| run_id | text | Y |  |
| computed_date | text | Y |  |
| notebook_version | text | Y |  |
| city_id | text | Y |  |

### ps2_phi_matrix (table, rows=100)

| column | type | nullable | default |
|---|---|---|---|
| city_id | USER-DEFINED | N |  |
| sub_a | character varying(30) | N |  |
| sub_b | character varying(30) | N |  |
| phi | numeric(12) | Y |  |
| computed_date | date | N |  |
| grain | text | Y |  |
| run_id | text | Y |  |
| notebook_version | text | Y |  |

### ps2_cascade_sankey_subsystem (table, rows=80)

| column | type | nullable | default |
|---|---|---|---|
| subsystem_from | text | Y |  |
| subsystem_to | text | Y |  |
| cascade_count | bigint(64) | Y |  |
| total_business_impact | bigint(64) | Y |  |
| avg_severity | double precision(53) | Y |  |
| grain | text | Y |  |
| run_id | text | Y |  |
| computed_date | text | Y |  |
| notebook_version | text | Y |  |
| city_id | text | Y |  |

### ps2_leadlag_timing (table, rows=80)

| column | type | nullable | default |
|---|---|---|---|
| city_id | USER-DEFINED | N |  |
| sub_a | character varying(30) | N |  |
| sub_b | character varying(30) | N |  |
| n_events | bigint(64) | Y |  |
| mean | numeric(12) | Y |  |
| median | numeric(12) | Y |  |
| p25 | numeric(12) | Y |  |
| p75 | numeric(12) | Y |  |
| computed_date | date | N |  |

### ps2_v2_component_serial_patterns (table, rows=78)

| column | type | nullable | default |
|---|---|---|---|
| city_id | USER-DEFINED | N |  |
| device_category | text | N |  |
| component_subsystem | text | N |  |
| component_serial_id | text | N |  |
| hardware_oos_episode_count | bigint(64) | Y |  |
| validated_failure_count | bigint(64) | Y |  |
| hardware_oos_minutes | double precision(53) | Y |  |
| observed_oos_days | bigint(64) | Y |  |
| last_oos_ts | timestamp without time zone | Y |  |
| serial_evidence_tier | text | Y |  |
| component_priority_score | double precision(53) | Y |  |
| current_component_age_days | double precision(53) | Y |  |
| hardware_component_description | text | Y |  |
| hardware_source | text | Y |  |
| current_config_device_count | double precision(53) | Y |  |
| hardware_age_enrichment | text | Y |  |
| run_id | text | Y |  |
| run_ts_utc | timestamp without time zone | Y |  |
| as_of_ts | timestamp without time zone | Y |  |
| source_start_ts | timestamp without time zone | Y |  |
| source_end_ts | timestamp without time zone | Y |  |
| notebook_version | text | Y |  |
| quality_status | text | Y |  |
| run_mode | text | Y |  |
| run_disposition | text | Y |  |
| output_scope | text | Y |  |
| is_production | boolean | Y |  |
| computed_date | date | Y |  |

### ps2_markov_transitions (table, rows=36)

| column | type | nullable | default |
|---|---|---|---|
| city_id | USER-DEFINED | N |  |
| from_sub | character varying(30) | N |  |
| to_sub | character varying(30) | N |  |
| prob | numeric(7) | Y |  |
| computed_date | date | N |  |
| grain | text | Y |  |
| run_id | text | Y |  |
| notebook_version | text | Y |  |

### ps2_network_centrality (table, rows=27)

| column | type | nullable | default |
|---|---|---|---|
| city_id | USER-DEFINED | N |  |
| node_id | character varying(30) | N |  |
| betweenness | numeric(7) | Y |  |
| pagerank | numeric(7) | Y |  |
| in_degree | integer(32) | Y |  |
| out_degree | integer(32) | Y |  |
| role | character varying(16) | Y |  |
| computed_date | date | N |  |
| scope | character varying(16) | N | 'ALL'::character varying |

### ps2_top_devices (table, rows=21)

| column | type | nullable | default |
|---|---|---|---|
| city_id | USER-DEFINED | N |  |
| device_id | character varying(20) | N |  |
| category | character varying(12) | Y |  |
| cascade_days | integer(32) | Y |  |
| w0_5 | integer(32) | Y |  |
| w5_15 | integer(32) | Y |  |
| w15_30 | integer(32) | Y |  |
| w30_60 | integer(32) | Y |  |
| w60plus | integer(32) | Y |  |
| dev_rank | smallint(16) | Y |  |
| computed_date | date | N |  |

### ps2_cascade_velocity_device (table, rows=20)

| column | type | nullable | default |
|---|---|---|---|
| window | text | Y |  |
| n | bigint(64) | Y |  |
| mean_chain_length | double precision(53) | Y |  |
| mean_velocity_min_per_fault | double precision(53) | Y |  |
| grain | text | Y |  |
| run_id | text | Y |  |
| computed_date | text | Y |  |
| notebook_version | text | Y |  |
| city_id | text | Y |  |

### ps2_error_code_transitions (table, rows=20)

| column | type | nullable | default |
|---|---|---|---|
| city_id | USER-DEFINED | N |  |
| from_code | character varying(20) | N |  |
| to_code | character varying(20) | N |  |
| occurrences | bigint(64) | Y |  |
| computed_date | date | N |  |

### ps2_error_codes (table, rows=20)

| column | type | nullable | default |
|---|---|---|---|
| city_id | USER-DEFINED | N |  |
| error_code | character varying(20) | N |  |
| occurrences | bigint(64) | Y |  |
| top_subsystem | character varying(30) | Y |  |
| pct | numeric(7) | Y |  |
| computed_date | date | N |  |

### ps2_v2_topology_nodes (table, rows=19)

| column | type | nullable | default |
|---|---|---|---|
| city_id | USER-DEFINED | N |  |
| device_category | text | N |  |
| subsystem | text | N |  |
| outgoing_edge_volume | bigint(64) | Y |  |
| out_degree | bigint(64) | Y |  |
| outgoing_pre_oos_rate | double precision(53) | Y |  |
| incoming_edge_volume | bigint(64) | Y |  |
| in_degree | bigint(64) | Y |  |
| flow_centrality_score | double precision(53) | Y |  |
| run_id | text | Y |  |
| run_ts_utc | timestamp without time zone | Y |  |
| as_of_ts | timestamp without time zone | Y |  |
| source_start_ts | timestamp without time zone | Y |  |
| source_end_ts | timestamp without time zone | Y |  |
| notebook_version | text | Y |  |
| quality_status | text | Y |  |
| run_mode | text | Y |  |
| run_disposition | text | Y |  |
| output_scope | text | Y |  |
| is_production | boolean | Y |  |
| computed_date | date | Y |  |

### ps2_v25_run_quality (table, rows=16)

| column | type | nullable | default |
|---|---|---|---|
| city_id | USER-DEFINED | N |  |
| check_name | text | N |  |
| passed | boolean | Y |  |
| observed_value | double precision(53) | Y |  |
| threshold | text | Y |  |
| severity | text | Y |  |
| metric_context | text | Y |  |
| quality_status | text | Y |  |
| run_mode | text | Y |  |
| run_disposition | text | Y |  |
| run_id | text | Y |  |
| run_ts_utc | timestamp without time zone | Y |  |
| as_of_ts | timestamp without time zone | Y |  |
| source_start_ts | timestamp without time zone | Y |  |
| source_end_ts | timestamp without time zone | Y |  |
| notebook_version | text | Y |  |
| output_scope | text | Y |  |
| is_production | boolean | Y |  |
| computed_date | date | Y |  |

### ps2_v2_oos_governance (table, rows=16)

| column | type | nullable | default |
|---|---|---|---|
| city_id | USER-DEFINED | N |  |
| device_category | text | N |  |
| oos_evidence_class | text | N |  |
| failure_evidence_class | text | N |  |
| event_count | bigint(64) | Y |  |
| device_count | bigint(64) | Y |  |
| outage_minutes | double precision(53) | Y |  |
| run_id | text | Y |  |
| run_ts_utc | timestamp without time zone | Y |  |
| as_of_ts | timestamp without time zone | Y |  |
| source_start_ts | timestamp without time zone | Y |  |
| source_end_ts | timestamp without time zone | Y |  |
| notebook_version | text | Y |  |
| quality_status | text | Y |  |
| run_mode | text | Y |  |
| run_disposition | text | Y |  |
| output_scope | text | Y |  |
| is_production | boolean | Y |  |
| computed_date | date | Y |  |

### ps2_cascade_paths (table, rows=14)

| column | type | nullable | default |
|---|---|---|---|
| city_id | USER-DEFINED | N |  |
| path_rank | smallint(16) | N |  |
| cascade_path | character varying(220) | N |  |
| path_len | smallint(16) | Y |  |
| first_subsystem | character varying(30) | Y |  |
| last_subsystem | character varying(30) | Y |  |
| occurrences | bigint(64) | Y |  |
| pct_of_chains | numeric(7) | Y |  |
| computed_date | date | N |  |

### ps2_hmm_regimes_device (table, rows=12)

| column | type | nullable | default |
|---|---|---|---|
| state | text | Y |  |
| pct_time | double precision(53) | Y |  |
| converged | boolean | Y |  |
| n_iter_run | bigint(64) | Y |  |
| grain | text | Y |  |
| run_id | text | Y |  |
| computed_date | text | Y |  |
| notebook_version | text | Y |  |
| city_id | text | Y |  |

### ps2_cascade_window_summary (table, rows=10)

| column | type | nullable | default |
|---|---|---|---|
| city_id | USER-DEFINED | N |  |
| window_bucket | character varying(10) | N |  |
| cascade_days | bigint(64) | N |  |
| pct | numeric(5) | N |  |
| total_cascade_days | bigint(64) | N |  |
| slow_fast_fault_mult | numeric(6) | Y |  |
| slow_fast_duration_mult | integer(32) | Y |  |
| computed_date | date | N |  |

### ps2_ignition_termination_subsystem (table, rows=10)

| column | type | nullable | default |
|---|---|---|---|
| subsystem | text | Y |  |
| ignition_count | bigint(64) | Y |  |
| termination_count | bigint(64) | Y |  |
| grain | text | Y |  |
| run_id | text | Y |  |
| computed_date | text | Y |  |
| notebook_version | text | Y |  |
| city_id | text | Y |  |

### ps2_window_detail (table, rows=10)

| column | type | nullable | default |
|---|---|---|---|
| city_id | USER-DEFINED | N |  |
| window_bucket | character varying(10) | N |  |
| cascade_days | bigint(64) | Y |  |
| chain_len_mean | numeric(7) | Y |  |
| chain_len_median | numeric(6) | Y |  |
| chain_len_max | integer(32) | Y |  |
| span_min_mean | numeric(10) | Y |  |
| span_min_median | numeric(10) | Y |  |
| velocity_min_per_fault | numeric(10) | Y |  |
| computed_date | date | N |  |

### ps2_ignition_termination (table, rows=9)

| column | type | nullable | default |
|---|---|---|---|
| city_id | USER-DEFINED | N |  |
| subsystem | character varying(30) | N |  |
| rank | smallint(16) | Y |  |
| ignition_days | bigint(64) | Y |  |
| termination_days | bigint(64) | Y |  |
| ignition_pct | numeric(7) | Y |  |
| termination_pct | numeric(7) | Y |  |
| net_role | character varying(12) | Y |  |
| computed_date | date | N |  |

### ps2_subsystem_hub_summary (table, rows=9)

| column | type | nullable | default |
|---|---|---|---|
| city_id | USER-DEFINED | N |  |
| node_id | character varying(30) | N |  |
| freq | smallint(16) | Y |  |
| is_hub | boolean | Y | false |
| computed_date | date | N |  |

### ps2_v25_failure_horizon_profile (table, rows=9)

| column | type | nullable | default |
|---|---|---|---|
| city_id | USER-DEFINED | N |  |
| device_category | text | N |  |
| lead_day | integer(32) | N |  |
| positive_device_days_at_lead | bigint(64) | Y |  |
| eligible_device_days | bigint(64) | Y |  |
| positive_rate_at_lead | double precision(53) | Y |  |
| label_definition | text | Y |  |
| run_id | text | Y |  |
| run_ts_utc | timestamp without time zone | Y |  |
| as_of_ts | timestamp without time zone | Y |  |
| source_start_ts | timestamp without time zone | Y |  |
| source_end_ts | timestamp without time zone | Y |  |
| notebook_version | text | Y |  |
| quality_status | text | Y |  |
| run_mode | text | Y |  |
| run_disposition | text | Y |  |
| output_scope | text | Y |  |
| is_production | boolean | Y |  |
| computed_date | date | Y |  |

### ps2_cascade_velocity (table, rows=5)

| column | type | nullable | default |
|---|---|---|---|
| city_id | USER-DEFINED | N |  |
| window_bucket | character varying(16) | N |  |
| n_events | bigint(64) | Y |  |
| mean_chain_length | numeric(10) | Y |  |
| mean_velocity_min_per_fault | numeric(12) | Y |  |
| computed_date | date | N |  |

### ps2_v25_category_profile (table, rows=5)

| column | type | nullable | default |
|---|---|---|---|
| city_id | USER-DEFINED | N |  |
| device_category_raw | text | N |  |
| device_category | text | N |  |
| event_count | bigint(64) | Y |  |
| device_count | bigint(64) | Y |  |
| first_event_ts | timestamp without time zone | Y |  |
| last_event_ts | timestamp without time zone | Y |  |
| is_mapped | boolean | Y |  |
| is_target_scope | boolean | Y |  |
| run_id | text | Y |  |
| run_ts_utc | timestamp without time zone | Y |  |
| as_of_ts | timestamp without time zone | Y |  |
| source_start_ts | timestamp without time zone | Y |  |
| source_end_ts | timestamp without time zone | Y |  |
| notebook_version | text | Y |  |
| quality_status | text | Y |  |
| run_mode | text | Y |  |
| run_disposition | text | Y |  |
| output_scope | text | Y |  |
| is_production | boolean | Y |  |
| computed_date | date | Y |  |

### ps2_v25_failure_definition_alignment (table, rows=4)

| column | type | nullable | default |
|---|---|---|---|
| city_id | USER-DEFINED | N |  |
| silver_ps1_failure_device_days | bigint(64) | Y |  |
| governed_oos_episode_device_days | bigint(64) | Y |  |
| overlap_device_days | bigint(64) | Y |  |
| silver_only_device_days | bigint(64) | Y |  |
| governed_only_device_days | bigint(64) | Y |  |
| device_category | text | N |  |
| silver_to_governed_overlap_rate | double precision(53) | Y |  |
| governed_to_silver_overlap_rate | double precision(53) | Y |  |
| definition_jaccard | double precision(53) | Y |  |
| run_id | text | Y |  |
| run_ts_utc | timestamp without time zone | Y |  |
| as_of_ts | timestamp without time zone | Y |  |
| source_start_ts | timestamp without time zone | Y |  |
| source_end_ts | timestamp without time zone | Y |  |
| notebook_version | text | Y |  |
| quality_status | text | Y |  |
| run_mode | text | Y |  |
| run_disposition | text | Y |  |
| output_scope | text | Y |  |
| is_production | boolean | Y |  |
| computed_date | date | Y |  |

### ps2_v25_failure_label_summary (table, rows=4)

| column | type | nullable | default |
|---|---|---|---|
| city_id | USER-DEFINED | N |  |
| eligible_device_days | bigint(64) | Y |  |
| positive_device_days | bigint(64) | Y |  |
| eligible_devices | bigint(64) | Y |  |
| positive_devices | bigint(64) | Y |  |
| future_hardware_oos_set_events | bigint(64) | Y |  |
| mean_hours_to_next_oos | double precision(53) | Y |  |
| median_hours_to_next_oos | double precision(53) | Y |  |
| p90_hours_to_next_oos | double precision(53) | Y |  |
| positive_days_with_commanded_oos | bigint(64) | Y |  |
| device_category | text | N |  |
| negative_device_days | bigint(64) | Y |  |
| label_positive_rate | double precision(53) | Y |  |
| positive_device_share | double precision(53) | Y |  |
| label_horizon_days | integer(32) | Y |  |
| label_cutoff_date | date | Y |  |
| label_definition | text | Y |  |
| run_id | text | Y |  |
| run_ts_utc | timestamp without time zone | Y |  |
| as_of_ts | timestamp without time zone | Y |  |
| source_start_ts | timestamp without time zone | Y |  |
| source_end_ts | timestamp without time zone | Y |  |
| notebook_version | text | Y |  |
| quality_status | text | Y |  |
| run_mode | text | Y |  |
| run_disposition | text | Y |  |
| output_scope | text | Y |  |
| is_production | boolean | Y |  |
| computed_date | date | Y |  |

### ps2_v25_ps1_model_performance (table, rows=4)

| column | type | nullable | default |
|---|---|---|---|
| city_id | USER-DEFINED | N |  |
| device_category | text | N |  |
| evaluated_device_days | bigint(64) | Y |  |
| eligible_device_days | bigint(64) | Y |  |
| prediction_coverage | double precision(53) | Y |  |
| true_positive | bigint(64) | Y |  |
| false_positive | bigint(64) | Y |  |
| true_negative | bigint(64) | Y |  |
| false_negative | bigint(64) | Y |  |
| actual_positive_rate | double precision(53) | Y |  |
| predicted_positive_rate | double precision(53) | Y |  |
| precision | double precision(53) | Y |  |
| recall | double precision(53) | Y |  |
| specificity | double precision(53) | Y |  |
| f1_score | double precision(53) | Y |  |
| balanced_accuracy | double precision(53) | Y |  |
| brier_score | double precision(53) | Y |  |
| roc_auc | double precision(53) | Y |  |
| pr_auc | double precision(53) | Y |  |
| evaluation_status | text | Y |  |
| run_id | text | Y |  |
| run_ts_utc | timestamp without time zone | Y |  |
| as_of_ts | timestamp without time zone | Y |  |
| source_start_ts | timestamp without time zone | Y |  |
| source_end_ts | timestamp without time zone | Y |  |
| notebook_version | text | Y |  |
| quality_status | text | Y |  |
| run_mode | text | Y |  |
| run_disposition | text | Y |  |
| output_scope | text | Y |  |
| is_production | boolean | Y |  |
| computed_date | date | Y |  |

### ps2_cascade_velocity_by_age_serial (table, rows=3)

| column | type | nullable | default |
|---|---|---|---|
| age_bucket | text | Y |  |
| n | bigint(64) | Y |  |
| mean_velocity_min_per_fault | double precision(53) | Y |  |
| grain | text | Y |  |
| run_id | text | Y |  |
| computed_date | text | Y |  |
| notebook_version | text | Y |  |
| city_id | text | Y |  |

### ps2_hmm_regimes (table, rows=3)

| column | type | nullable | default |
|---|---|---|---|
| city_id | USER-DEFINED | N |  |
| regime | character varying(20) | N |  |
| pct | numeric(5) | N |  |
| dwell_days_min | numeric(5) | Y |  |
| dwell_days_max | numeric(5) | Y |  |
| computed_date | date | N |  |

### ps2_v25_ps1_label_parity (table, rows=3)

| column | type | nullable | default |
|---|---|---|---|
| city_id | USER-DEFINED | N |  |
| device_category | text | N |  |
| eligible_device_days | bigint(64) | Y |  |
| comparable_device_days | bigint(64) | Y |  |
| matching_device_days | bigint(64) | Y |  |
| mismatching_device_days | bigint(64) | Y |  |
| source_label_positive_rate | double precision(53) | Y |  |
| rebuilt_label_positive_rate | double precision(53) | Y |  |
| legacy_sla_positive_rate | double precision(53) | Y |  |
| parity_rate | double precision(53) | Y |  |
| parity_status | text | Y |  |
| rebuilt_target | text | Y |  |
| run_id | text | Y |  |
| run_ts_utc | timestamp without time zone | Y |  |
| as_of_ts | timestamp without time zone | Y |  |
| source_start_ts | timestamp without time zone | Y |  |
| source_end_ts | timestamp without time zone | Y |  |
| notebook_version | text | Y |  |
| quality_status | text | Y |  |
| run_mode | text | Y |  |
| run_disposition | text | Y |  |
| output_scope | text | Y |  |
| is_production | boolean | Y |  |
| computed_date | date | Y |  |

### ps2_v2_cross_ps_alignment (table, rows=3)

| column | type | nullable | default |
|---|---|---|---|
| city_id | USER-DEFINED | N |  |
| truth_positive_count | bigint(64) | Y |  |
| signal_positive_count | double precision(53) | Y |  |
| matched_positive_count | bigint(64) | Y |  |
| precision | double precision(53) | Y |  |
| recall | double precision(53) | Y |  |
| population_unit | text | Y |  |
| alignment_status | text | Y |  |
| source | text | N |  |
| run_id | text | Y |  |
| run_ts_utc | timestamp without time zone | Y |  |
| as_of_ts | timestamp without time zone | Y |  |
| source_start_ts | timestamp without time zone | Y |  |
| source_end_ts | timestamp without time zone | Y |  |
| notebook_version | text | Y |  |
| quality_status | text | Y |  |
| run_mode | text | Y |  |
| run_disposition | text | Y |  |
| output_scope | text | Y |  |
| is_production | boolean | Y |  |
| computed_date | date | Y |  |

### ps2_cascade_path_explainability (table, rows=2)

| column | type | nullable | default |
|---|---|---|---|
| explainability_id | bigint(64) | N |  |
| assessment_id | bigint(64) | N |  |
| path_rank | smallint(16) | N |  |
| source_subsystem | character varying(30) | N |  |
| target_subsystem | character varying(30) | N |  |
| transition_probability | numeric(6) | N |  |
| avg_window_minutes | numeric(8) | Y |  |

### ps2_cascade_risk_assessments (table, rows=2)

| column | type | nullable | default |
|---|---|---|---|
| assessment_id | bigint(64) | N |  |
| device_id | character varying(20) | N |  |
| device_category | character varying(20) | N |  |
| facility_id | character varying(20) | Y |  |
| assessment_date | date | N |  |
| hmm_regime | character varying(20) | N |  |
| regime_probability | numeric(6) | Y |  |
| expected_dwell_days | numeric(5) | Y |  |
| predicted_chain_length | numeric(6) | Y |  |
| dominant_window_bucket | character varying(12) | Y |  |
| model_registry_id | bigint(64) | Y |  |
| scored_ts | timestamp without time zone | N |  |
| scoring_mode | character varying(10) | N |  |

### ps2_facility_contagion_summary (table, rows=2)

| column | type | nullable | default |
|---|---|---|---|
| city_id | USER-DEFINED | N |  |
| total_facility_cascade_days | bigint(64) | Y |  |
| multi_device_contagion_pct | numeric(5) | Y |  |
| trend_start_pct | numeric(5) | Y |  |
| trend_end_pct | numeric(5) | Y |  |
| hotspot_facility_id | integer(32) | Y |  |
| hotspot_facility_name | character varying(100) | Y |  |
| hotspot_min_devices | smallint(16) | Y |  |
| hotspot_max_devices | smallint(16) | Y |  |
| computed_date | date | N |  |

### ps2_subsystem_hub_edges (table, rows=2)

| column | type | nullable | default |
|---|---|---|---|
| city_id | USER-DEFINED | N |  |
| source_sub | character varying(30) | N |  |
| target_sub | character varying(30) | N |  |
| phi | numeric(8) | Y |  |
| computed_date | date | N |  |

### ps2_cross_ps_attribution_device (table, rows=1)

| column | type | nullable | default |
|---|---|---|---|
| entity_grain | text | Y |  |
| n_ps2_ignition_entities | bigint(64) | Y |  |
| n_matched_in_cross_ps | bigint(64) | Y |  |
| match_rate | double precision(53) | Y |  |
| grain | text | Y |  |
| run_id | text | Y |  |
| computed_date | text | Y |  |
| notebook_version | text | Y |  |
| city_id | text | Y |  |
| ps1_high_risk_co_occur_n | bigint(64) | Y |  |
| ps1_high_risk_co_occur_rate | double precision(53) | Y |  |
| ps4_anomaly_co_occur_n | bigint(64) | Y |  |
| ps4_anomaly_co_occur_rate | double precision(53) | Y |  |

### ps2_cross_ps_attribution_serial (table, rows=1)

| column | type | nullable | default |
|---|---|---|---|
| entity_grain | text | Y |  |
| n_ps2_ignition_entities | bigint(64) | Y |  |
| n_matched_in_cross_ps | bigint(64) | Y |  |
| match_rate | double precision(53) | Y |  |
| ps1_high_risk_co_occur_n | bigint(64) | Y |  |
| ps1_high_risk_co_occur_rate | double precision(53) | Y |  |
| ps4_anomaly_co_occur_n | bigint(64) | Y |  |
| ps4_anomaly_co_occur_rate | double precision(53) | Y |  |
| note | text | Y |  |
| grain | text | Y |  |
| run_id | text | Y |  |
| computed_date | text | Y |  |
| notebook_version | text | Y |  |
| city_id | text | Y |  |

### ps2_suppression_summary_serial (table, rows=1)

| column | type | nullable | default |
|---|---|---|---|
| family | text | Y |  |
| grain | text | Y |  |
| min_support_floor | bigint(64) | Y |  |
| cells_suppressed | bigint(64) | Y |  |
| cells_reported | bigint(64) | Y |  |
| run_id | text | Y |  |
| computed_date | text | Y |  |
| notebook_version | text | Y |  |
| city_id | text | Y |  |

### ps2_cascade_chains_daily (table, rows=0)

| column | type | nullable | default |
|---|---|---|---|
| id | bigint(64) | N | nextval('ps2_cascade_chains_daily_id_seq'::regclass) |
| city_id | USER-DEFINED | N |  |
| trigger_device_id | character varying(30) | N |  |
| trigger_device_type | USER-DEFINED | N |  |
| toc_code | USER-DEFINED | Y |  |
| trigger_ts | timestamp with time zone | N |  |
| window_bucket | character varying(10) | N |  |
| device_count_in_chain | smallint(16) | N |  |
| fault_count_in_chain | smallint(16) | N |  |
| hub_subsystem | character varying(30) | Y |  |
| cascade_duration_min | integer(32) | Y |  |
| cascade_date | date | N |  |

### ps2_device_cmdb_map (table, rows=0)

| column | type | nullable | default |
|---|---|---|---|
| device_id | text | Y |  |
| serial_id | text | Y |  |
| cmdb_ci_sys_id | text | N |  |
| grain | text | N | 'device'::text |
| run_id | text | Y |  |
| computed_date | text | Y |  |
| notebook_version | text | Y |  |
| city_id | text | N | 'CHI'::text |
| loaded_at | timestamp with time zone | Y | now() |

### ps2_facility_contagion_daily (table, rows=0)

| column | type | nullable | default |
|---|---|---|---|
| city_id | USER-DEFINED | N |  |
| facility_id | integer(32) | N |  |
| facility_name | character varying(100) | Y |  |
| contagion_date | date | N |  |
| device_count_cascading | smallint(16) | N |  |
| contagion_rate | numeric(5) | N |  |
| is_hotspot_flag | boolean | N | false |

### v_ps2_device_cascade (view, rows=None)

| column | type | nullable | default |
|---|---|---|---|
| city_id | USER-DEFINED | Y |  |
| computed_date | date | Y |  |
| device_id | character varying(20) | Y |  |
| category | character varying(12) | Y |  |
| total_impact | numeric(14) | Y |  |
| avg_impact | numeric(10) | Y |  |
| impact_cascade_days | integer(32) | Y |  |
| recurrence_cascade_days | integer(32) | Y |  |
| chronic | boolean | Y |  |
| impact_rank | bigint(64) | Y |  |
| impact_rank_in_category | bigint(64) | Y |  |

### v_ps2_facility_contagion_facility (view, rows=None)

| column | type | nullable | default |
|---|---|---|---|
| facility_id | text | Y |  |
| cascade_days | bigint(64) | Y |  |
| distinct_devices | bigint(64) | Y |  |
| contagion_rate | double precision(53) | Y |  |
| grain | text | Y |  |
| run_id | text | Y |  |
| computed_date | text | Y |  |
| city_id | text | Y |  |

### v_ps2_ignition_termination_subsystem (view, rows=None)

| column | type | nullable | default |
|---|---|---|---|
| subsystem | text | Y |  |
| ignition_count | bigint(64) | Y |  |
| termination_count | bigint(64) | Y |  |
| grain | text | Y |  |
| run_id | text | Y |  |
| computed_date | text | Y |  |
| city_id | text | Y |  |

### v_ps2_network_centrality (view, rows=None)

| column | type | nullable | default |
|---|---|---|---|
| city_id | USER-DEFINED | Y |  |
| computed_date | date | Y |  |
| scope | character varying(16) | Y |  |
| is_fleet | boolean | Y |  |
| node_id | character varying(30) | Y |  |
| betweenness | numeric(7) | Y |  |
| pagerank | numeric(7) | Y |  |
| in_degree | integer(32) | Y |  |
| out_degree | integer(32) | Y |  |
| total_degree | integer(32) | Y |  |
| pagerank_rank | bigint(64) | Y |  |
| betweenness_rank | bigint(64) | Y |  |
| n_nodes_in_scope | bigint(64) | Y |  |

### v_ps2_v25_status (view, rows=None)

| column | type | nullable | default |
|---|---|---|---|
| table_name | text | Y |  |
| city_id | USER-DEFINED | Y |  |
| run_id | text | Y |  |
| computed_date | date | Y |  |
| notebook_version | text | Y |  |
| as_of_ts | timestamp without time zone | Y |  |
| row_count | bigint(64) | Y |  |

## PS3 — 53 tables (167,029 rows), 24 views

| object | kind | rows | cols | primary key |
|---|---|---:|---:|---|
| ps3_v25_device_day | table | 54,239 | 13 | city_id, device_id, mars_device_category, event_date |
| ps3_v25_device_episode_fact | table | 54,239 | 79 | city_id, oos_episode_id |
| ps3_incident_predictions | table | 34,612 | 20 | city_id, run_id, availability_event_id |
| ps3_v2_shap_incident | table | 4,800 | 24 | id |
| ps3_device_predictions | table | 3,412 | 11 | city_id, run_id, device_id |
| ps3_v25_device_reliability | table | 2,806 | 18 | city_id, device_id, mars_device_category |
| ps3_v25_device_summary | table | 2,806 | 11 | city_id, device_id, mars_device_category |
| ps3_v25_serial_reliability | table | 2,762 | 12 | city_id, component_serial_nbr, device_id, mars_device_category |
| ps3_serial_predictions | table | 2,515 | 17 | city_id, run_id, device_id, matched_serial_nbr |
| ps3_v2_device_serial_component | table | 1,413 | 14 | id |
| ps3_v2_device_reliability | table | 927 | 18 | id |
| ps3_v2_device_serial | table | 927 | 13 | id |
| ps3_v2_severity_action_queue | table | 472 | 28 | id |
| ps3_v25_facility_rollup | table | 366 | 13 | city_id, facility_id, mars_device_category |
| ps3_v2_facility_hotspots | table | 320 | 12 | id |
| ps3_leakage_scan | table | 59 | 7 | city_id, run_id, device_category, feature_name |
| ps3_v25_causal_balance | table | 54 | 6 | city_id, treatment_component, covariate |
| ps3_v2_shap_global | table | 44 | 18 | id |
| ps3_v2_component_taxonomy | table | 29 | 11 | id |
| ps3_head_leaderboard | table | 28 | 14 | city_id, run_id, device_category, head, model |
| ps3_v2_driver_importance | table | 22 | 14 | id |
| ps3_v25_run_status | table | 19 | 15 | city_id, table_name |
| ps3_head_class_metrics | table | 16 | 11 | city_id, run_id, device_category, head, class_label |
| ps3_v25_component_summary | table | 14 | 9 | city_id, mars_device_category, component_attribution, dashboard_root_cause_domain, dashboard_severity |
| ps3_v25_repeat_interval | table | 14 | 13 | city_id, component_attribution, mars_device_category |
| ps3_v25_model_feature_importance | table | 12 | 7 | city_id, target, model, feature, model_scope |
| ps3_v2_model_comparison | table | 10 | 35 | id |
| ps3_v2_component_reliability | table | 9 | 16 | id |
| ps3_v25_model_scorecard | table | 8 | 15 | city_id, target, candidate_model, model_scope |
| ps3_v25_run_stage_audit | table | 8 | 11 | city_id, stage |
| ps3_head_summary | table | 7 | 25 | city_id, run_id, device_category, head |
| ps3_v25_root_cause_evidence_audit | table | 7 | 7 | city_id, source |
| ps3_v25_source_column_profile | table | 7 | 11 | city_id, column |
| ps3_v25_causal_effects | table | 6 | 24 | city_id, treatment_component, outcome |
| ps3_v2_display_policy | table | 6 | 11 | id |
| ps3_v2_run_scorecard | table | 5 | 17 | id |
| ps3_v2_causal_effects | table | 4 | 31 | id |
| ps3_v2_promotion_status | table | 4 | 13 | id |
| ps3_category_coverage | table | 3 | 12 | city_id, run_id, device_category |
| ps3_v25_commanded_split | table | 3 | 7 | city_id, mars_device_category |
| ps3_v25_label_maturity | table | 3 | 10 | city_id, mars_device_category |
| ps3_v25_prediction_explainability | table | 3 | 14 | city_id, target, model_output, oos_episode_id, feature |
| ps3_v2_readiness | table | 3 | 13 | id |
| ps3_model_runs | table | 2 | 15 | city_id, run_id |
| ps3_severity_summary | table | 1 | 22 | city_id, as_of_date |
| ps3_v25_oos_source_audit | table | 1 | 14 | city_id, source |
| ps3_v2_data_freshness | table | 1 | 12 | id |
| ps3_v2_runs | table | 1 | 7 | city_id, pipeline_version, run_id |
| ps3_device_metrics | table | 0 | 7 | city_id, device, split, as_of_date |
| ps3_head_feature_importance | table | 0 | 8 | city_id, run_id, device_category, head, feature_rank |
| ps3_prediction_explainability | table | 0 | 6 | - |
| ps3_severity_drivers | table | 0 | 6 | city_id, feature, as_of_date |
| ps3_severity_predictions | table | 0 | 12 | id |
| v_ps3_category_coverage | view | - | 16 | - |
| v_ps3_collapse_health | view | - | 7 | - |
| v_ps3_device_360 | view | - | 20 | - |
| v_ps3_device_all | view | - | 22 | - |
| v_ps3_device_risk | view | - | 17 | - |
| v_ps3_head_gates | view | - | 10 | - |
| v_ps3_latest_run | view | - | 8 | - |
| v_ps3_rollup_all | view | - | 14 | - |
| v_ps3_run_registry | view | - | 11 | - |
| v_ps3_serial_all | view | - | 17 | - |
| v_ps3_serial_risk | view | - | 13 | - |
| v_ps3_two_head_scorecard | view | - | 21 | - |
| v_ps3_v25_severity_maturity | view | - | 7 | - |
| v_ps3_v25_status | view | - | 4 | - |
| v_ps3_v2_causal | view | - | 32 | - |
| v_ps3_v2_current | view | - | 7 | - |
| v_ps3_v2_policy | view | - | 11 | - |
| v_ps3_v2_queue | view | - | 30 | - |
| v_ps3_v2_rootcause | view | - | 17 | - |
| v_ps3_v2_rootcause_concentration | view | - | 10 | - |
| v_ps3_v2_rootcause_rollup | view | - | 12 | - |
| v_ps3_v2_scorecard | view | - | 19 | - |
| v_ps3_v2_shap | view | - | 24 | - |
| v_ps3_v2_table_status | view | - | 2 | - |

### ps3_v25_device_day (table, rows=54239)

| column | type | nullable | default |
|---|---|---|---|
| city_id | USER-DEFINED | N |  |
| computed_date | date | Y |  |
| device_id | text | N |  |
| mars_device_category | text | N |  |
| event_date | date | N |  |
| oos_episode_starts | bigint(64) | Y |  |
| oos_set_events | bigint(64) | Y |  |
| set_signal_span_minutes | double precision(53) | Y |  |
| commanded_signal_episodes | bigint(64) | Y |  |
| observed_severity_episodes | bigint(64) | Y |  |
| confirmed_root_cause_episodes | bigint(64) | Y |  |
| failure_only_episode_starts | bigint(64) | Y |  |
| grain | text | Y |  |

### ps3_v25_device_episode_fact (table, rows=54239)

| column | type | nullable | default |
|---|---|---|---|
| city_id | USER-DEFINED | N |  |
| computed_date | date | Y |  |
| oos_episode_id | text | N |  |
| device_id | text | Y |  |
| mars_device_category | text | Y |  |
| episode_start | timestamp without time zone | Y |  |
| episode_last_signal | timestamp without time zone | Y |  |
| oos_set_event_count | bigint(64) | Y |  |
| first_oos_event_id | text | Y |  |
| contains_commanded_oos_signal | boolean | Y |  |
| observed_event_component | text | Y |  |
| component_subsystem | text | Y |  |
| component_position | text | Y |  |
| facility_id | text | Y |  |
| facility_name | text | Y |  |
| bus_id | text | Y |  |
| component_serial_nbr | text | Y |  |
| event_type_name | text | Y |  |
| event_type_id | text | Y |  |
| observed_event_severity | text | Y |  |
| event_type_severity | text | Y |  |
| event_priority | text | Y |  |
| requires_service_call | boolean | Y |  |
| any_automatic_clear | boolean | Y |  |
| distinct_serials_in_episode | bigint(64) | Y |  |
| distinct_components_in_episode | bigint(64) | Y |  |
| set_signal_span_minutes | double precision(53) | Y |  |
| oos_fact_definition | text | Y |  |
| oos_minutes_union | double precision(53) | Y |  |
| oos_minutes_naive_sum | double precision(53) | Y |  |
| events_with_clear | double precision(53) | Y |  |
| events_clear_clamped | double precision(53) | Y |  |
| episode_scope_status | text | Y |  |
| episode_scope_start | timestamp without time zone | Y |  |
| linked_severity | text | Y |  |
| linked_component | text | Y |  |
| linked_root_cause | text | Y |  |
| linked_root_cause_domain | text | Y |  |
| linked_confidence | double precision(53) | Y |  |
| linked_source | text | Y |  |
| link_method | text | Y |  |
| observed_severity_label | text | Y |  |
| severity_status | text | Y |  |
| component_attribution | text | Y |  |
| component_attribution_status | text | Y |  |
| confirmed_root_cause_label | text | Y |  |
| confirmed_root_cause_domain | text | Y |  |
| root_cause_confidence | double precision(53) | Y |  |
| root_cause_evidence_source | text | Y |  |
| root_cause_link_method | text | Y |  |
| root_cause_status | text | Y |  |
| candidate_root_cause_raw | text | Y |  |
| evidence_conflict_status | text | Y |  |
| event_month | bigint(64) | Y |  |
| event_day_of_week | bigint(64) | Y |  |
| event_hour | bigint(64) | Y |  |
| log_oos_set_event_count | double precision(53) | Y |  |
| log_set_signal_span_minutes | double precision(53) | Y |  |
| prior_episodes_7d | double precision(53) | Y |  |
| prior_episodes_30d | double precision(53) | Y |  |
| prior_episodes_90d | double precision(53) | Y |  |
| days_since_prior_episode | double precision(53) | Y |  |
| device_oos_recency_status | text | Y |  |
| predicted_component | text | Y |  |
| predicted_component_confidence | double precision(53) | Y |  |
| component_model_status | text | Y |  |
| run_id | text | Y |  |
| computed_at_utc | text | Y |  |
| data_as_of_date | text | Y |  |
| data_freshness_days | bigint(64) | Y |  |
| is_current_operational_score | boolean | Y |  |
| freshness_status | text | Y |  |
| right_censored_tail_days | bigint(64) | Y |  |
| chargeability_policy | text | Y |  |
| shap_interpretation | text | Y |  |
| dashboard_root_cause_domain | text | Y |  |
| dashboard_root_cause_status | text | Y |  |
| dashboard_severity | text | Y |  |
| dashboard_severity_status | text | Y |  |

### ps3_incident_predictions (table, rows=34612)

| column | type | nullable | default |
|---|---|---|---|
| city_id | USER-DEFINED | N |  |
| run_id | character varying(48) | N |  |
| availability_event_id | character varying(48) | N |  |
| device_id | character varying(40) | Y |  |
| mars_device_category | character varying(12) | Y |  |
| ae_start_dtm | timestamp with time zone | Y |  |
| matched_serial_nbr | character varying(64) | Y |  |
| component_age_days | numeric(10) | Y |  |
| facility_id | character varying(20) | Y |  |
| facility_name | character varying(120) | Y |  |
| pred_severity | character varying(48) | Y |  |
| pred_severity_conf | numeric(7) | Y |  |
| pred_severity_collapsed | character varying(16) | Y |  |
| actual_severity | character varying(48) | Y |  |
| pred_component | character varying(48) | Y |  |
| pred_component_conf | numeric(7) | Y |  |
| actual_component | character varying(48) | Y |  |
| features | jsonb | Y |  |
| scored_at | timestamp with time zone | Y | now() |
| computed_date | date | N |  |

### ps3_v2_shap_incident (table, rows=4800)

| column | type | nullable | default |
|---|---|---|---|
| id | bigint(64) | N | nextval('ps3_v2_shap_incident_id_seq'::regclass) |
| city_id | text | N |  |
| pipeline_version | text | N |  |
| run_id | text | Y |  |
| device_category | text | Y |  |
| head | text | Y |  |
| availability_event_id | text | Y |  |
| device_id | text | Y |  |
| serial_number | text | Y |  |
| facility_id | text | Y |  |
| event_timestamp | text | Y |  |
| actual_label | text | Y |  |
| predicted_label | text | Y |  |
| predicted_probability | double precision(53) | Y |  |
| feature | text | Y |  |
| feature_value | text | Y |  |
| shap_value | double precision(53) | Y |  |
| abs_shap_value | double precision(53) | Y |  |
| feature_rank | bigint(64) | Y |  |
| model | text | Y |  |
| explainer | text | Y |  |
| publication_status | text | Y |  |
| explanation_scope | text | Y |  |
| loaded_at | timestamp with time zone | Y | now() |

### ps3_device_predictions (table, rows=3412)

| column | type | nullable | default |
|---|---|---|---|
| city_id | USER-DEFINED | N |  |
| run_id | character varying(48) | N |  |
| device_id | character varying(40) | N |  |
| mars_device_category | character varying(12) | Y |  |
| n_incidents | integer(32) | Y |  |
| pct_critical_pred | numeric(7) | Y |  |
| dominant_pred_severity | character varying(48) | Y |  |
| dominant_pred_component | character varying(48) | Y |  |
| avg_component_age_days | numeric(10) | Y |  |
| last_incident_dtm | timestamp with time zone | Y |  |
| computed_date | date | N |  |

### ps3_v25_device_reliability (table, rows=2806)

| column | type | nullable | default |
|---|---|---|---|
| city_id | USER-DEFINED | N |  |
| computed_date | date | Y |  |
| device_id | text | N |  |
| mars_device_category | text | N |  |
| oos_episode_count | bigint(64) | Y |  |
| oos_set_event_count | bigint(64) | Y |  |
| critical_episodes | bigint(64) | Y |  |
| first_episode_at | timestamp without time zone | Y |  |
| latest_episode_at | timestamp without time zone | Y |  |
| mean_interval_hours | double precision(53) | Y |  |
| median_interval_hours | double precision(53) | Y |  |
| confirmed_root_cause_episodes | bigint(64) | Y |  |
| observed_severity_episodes | bigint(64) | Y |  |
| commanded_signal_episodes | bigint(64) | Y |  |
| critical_rate | double precision(53) | Y |  |
| failure_only_episode_count | bigint(64) | Y |  |
| reliability_risk_band | text | Y |  |
| band_basis | text | Y |  |

### ps3_v25_device_summary (table, rows=2806)

| column | type | nullable | default |
|---|---|---|---|
| city_id | USER-DEFINED | N |  |
| computed_date | date | Y |  |
| device_id | text | N |  |
| mars_device_category | text | N |  |
| oos_episode_count | bigint(64) | Y |  |
| first_oos_episode_start | timestamp without time zone | Y |  |
| latest_oos_episode_start | timestamp without time zone | Y |  |
| latest_dashboard_severity | text | Y |  |
| latest_dashboard_root_cause_domain | text | Y |  |
| confirmed_root_cause_episode_count | bigint(64) | Y |  |
| observed_severity_episode_count | bigint(64) | Y |  |

### ps3_v25_serial_reliability (table, rows=2762)

| column | type | nullable | default |
|---|---|---|---|
| city_id | USER-DEFINED | N |  |
| computed_date | date | Y |  |
| component_serial_nbr | text | N |  |
| device_id | text | N |  |
| mars_device_category | text | N |  |
| oos_episode_count | bigint(64) | Y |  |
| first_episode_at | timestamp without time zone | Y |  |
| latest_episode_at | timestamp without time zone | Y |  |
| component_attributions | bigint(64) | Y |  |
| confirmed_root_cause_episodes | bigint(64) | Y |  |
| observed_span_days | double precision(53) | Y |  |
| episodes_per_100_observed_days | double precision(53) | Y |  |

### ps3_serial_predictions (table, rows=2515)

| column | type | nullable | default |
|---|---|---|---|
| city_id | USER-DEFINED | N |  |
| run_id | character varying(48) | N |  |
| device_id | character varying(40) | N |  |
| matched_serial_nbr | character varying(64) | N |  |
| mars_device_category | character varying(12) | Y |  |
| n_incidents | integer(32) | Y |  |
| component_age_days | numeric(10) | Y |  |
| dominant_pred_component | character varying(48) | Y |  |
| pct_critical_pred | numeric(7) | Y |  |
| last_incident_dtm | timestamp with time zone | Y |  |
| computed_date | date | N |  |
| component_description | character varying(120) | Y |  |
| attribution_basis | character varying(24) | Y |  |
| n_incidents_exposed | integer(32) | Y |  |
| n_incidents_attributed | integer(32) | Y |  |
| pct_critical_exposed | numeric(7) | Y |  |
| pct_critical_attributed | numeric(7) | Y |  |

### ps3_v2_device_serial_component (table, rows=1413)

| column | type | nullable | default |
|---|---|---|---|
| id | bigint(64) | N | nextval('ps3_v2_device_serial_component_id_seq'::regclass) |
| city_id | text | N |  |
| pipeline_version | text | N |  |
| run_id | text | Y |  |
| device_id | text | Y |  |
| serial_number | text | Y |  |
| component_label | text | Y |  |
| incident_count | bigint(64) | Y |  |
| critical_rate | double precision(53) | Y |  |
| component_label_semantics | text | Y |  |
| latest_incident_at | text | Y |  |
| recurrence_30d | bigint(64) | Y |  |
| device_category | text | Y |  |
| loaded_at | timestamp with time zone | Y | now() |

### ps3_v2_device_reliability (table, rows=927)

| column | type | nullable | default |
|---|---|---|---|
| id | bigint(64) | N | nextval('ps3_v2_device_reliability_id_seq'::regclass) |
| city_id | text | N |  |
| pipeline_version | text | N |  |
| run_id | text | Y |  |
| device_id | text | Y |  |
| incident_count | bigint(64) | Y |  |
| critical_incidents | bigint(64) | Y |  |
| critical_rate | double precision(53) | Y |  |
| latest_incident_at | text | Y |  |
| mean_interval_hours | double precision(53) | Y |  |
| median_interval_hours | double precision(53) | Y |  |
| reliability_risk_band | text | Y |  |
| first_incident_at | text | Y |  |
| recent_24h_recurrence | bigint(64) | Y |  |
| recent_30d_recurrence | bigint(64) | Y |  |
| days_since_latest_incident | double precision(53) | Y |  |
| device_category | text | Y |  |
| loaded_at | timestamp with time zone | Y | now() |

### ps3_v2_device_serial (table, rows=927)

| column | type | nullable | default |
|---|---|---|---|
| id | bigint(64) | N | nextval('ps3_v2_device_serial_id_seq'::regclass) |
| city_id | text | N |  |
| pipeline_version | text | N |  |
| run_id | text | Y |  |
| device_id | text | Y |  |
| serial_number | text | Y |  |
| incident_count | bigint(64) | Y |  |
| critical_incidents | bigint(64) | Y |  |
| critical_rate | double precision(53) | Y |  |
| latest_incident_at | text | Y |  |
| max_prior_incidents_30d | bigint(64) | Y |  |
| device_category | text | Y |  |
| loaded_at | timestamp with time zone | Y | now() |

### ps3_v2_severity_action_queue (table, rows=472)

| column | type | nullable | default |
|---|---|---|---|
| id | bigint(64) | N | nextval('ps3_v2_severity_action_queue_id_seq'::regclass) |
| city_id | text | N |  |
| pipeline_version | text | N |  |
| run_id | text | Y |  |
| device_category | text | Y |  |
| device_id | text | Y |  |
| serial_number | text | Y |  |
| facility_id | text | Y |  |
| source_event_timestamp | text | Y |  |
| data_as_of_timestamp | text | Y |  |
| availability_event_id | text | Y |  |
| predicted_severity | text | Y |  |
| critical_probability | double precision(53) | Y |  |
| major_probability | double precision(53) | Y |  |
| prediction_confidence | double precision(53) | Y |  |
| decision_threshold | double precision(53) | Y |  |
| action_band | text | Y |  |
| action_priority_score | double precision(53) | Y |  |
| prior_incidents_24h | bigint(64) | Y |  |
| prior_incidents_7d | bigint(64) | Y |  |
| prior_incidents_30d | bigint(64) | Y |  |
| prior_critical_rate_30d | double precision(53) | Y |  |
| hours_since_prior_incident | double precision(53) | Y |  |
| model | text | Y |  |
| model_status | text | Y |  |
| score_generated_at_utc | text | Y |  |
| prediction_scope | text | Y |  |
| loaded_at | timestamp with time zone | Y | now() |

### ps3_v25_facility_rollup (table, rows=366)

| column | type | nullable | default |
|---|---|---|---|
| city_id | USER-DEFINED | N |  |
| computed_date | date | Y |  |
| facility_id | text | N |  |
| mars_device_category | text | N |  |
| devices | bigint(64) | Y |  |
| oos_episodes | bigint(64) | Y |  |
| first_episode | timestamp without time zone | Y |  |
| latest_episode | timestamp without time zone | Y |  |
| active_days | bigint(64) | Y |  |
| commanded_signal_episodes | bigint(64) | Y |  |
| confirmed_root_cause_episodes | bigint(64) | Y |  |
| episodes_per_device | double precision(53) | Y |  |
| failure_only_episodes | bigint(64) | Y |  |

### ps3_v2_facility_hotspots (table, rows=320)

| column | type | nullable | default |
|---|---|---|---|
| id | bigint(64) | N | nextval('ps3_v2_facility_hotspots_id_seq'::regclass) |
| city_id | text | N |  |
| pipeline_version | text | N |  |
| run_id | text | Y |  |
| facility_id | text | Y |  |
| incident_count | bigint(64) | Y |  |
| affected_devices | bigint(64) | Y |  |
| critical_rate | double precision(53) | Y |  |
| latest_incident_at | text | Y |  |
| max_facility_prior_incidents_24h | bigint(64) | Y |  |
| device_category | text | Y |  |
| loaded_at | timestamp with time zone | Y | now() |

### ps3_leakage_scan (table, rows=59)

| column | type | nullable | default |
|---|---|---|---|
| city_id | USER-DEFINED | N |  |
| run_id | character varying(48) | N |  |
| device_category | character varying(12) | N |  |
| feature_name | character varying(80) | N |  |
| solo_auc | numeric(7) | Y |  |
| flagged | boolean | Y |  |
| as_of_date | date | N |  |

### ps3_v25_causal_balance (table, rows=54)

| column | type | nullable | default |
|---|---|---|---|
| city_id | USER-DEFINED | N |  |
| computed_date | date | Y |  |
| treatment_component | text | N |  |
| covariate | text | N |  |
| standardised_mean_difference | double precision(53) | Y |  |
| balance_status | text | Y |  |

### ps3_v2_shap_global (table, rows=44)

| column | type | nullable | default |
|---|---|---|---|
| id | bigint(64) | N | nextval('ps3_v2_shap_global_id_seq'::regclass) |
| city_id | text | N |  |
| pipeline_version | text | N |  |
| run_id | text | Y |  |
| device_category | text | Y |  |
| head | text | Y |  |
| target_class | text | Y |  |
| feature | text | Y |  |
| mean_abs_shap | double precision(53) | Y |  |
| mean_signed_shap | double precision(53) | Y |  |
| feature_rank | bigint(64) | Y |  |
| explained_partition | text | Y |  |
| model | text | Y |  |
| explainer | text | Y |  |
| n_explained_rows | bigint(64) | Y |  |
| publication_status | text | Y |  |
| explanation_scope | text | Y |  |
| loaded_at | timestamp with time zone | Y | now() |

### ps3_v2_component_taxonomy (table, rows=29)

| column | type | nullable | default |
|---|---|---|---|
| id | bigint(64) | N | nextval('ps3_v2_component_taxonomy_id_seq'::regclass) |
| city_id | text | N |  |
| pipeline_version | text | N |  |
| run_id | text | Y |  |
| device_category | text | Y |  |
| component_label | text | Y |  |
| split | text | Y |  |
| taxonomy_status | text | Y |  |
| action | text | Y |  |
| rows | bigint(64) | Y |  |
| loaded_at | timestamp with time zone | Y | now() |

### ps3_head_leaderboard (table, rows=28)

| column | type | nullable | default |
|---|---|---|---|
| city_id | USER-DEFINED | N |  |
| run_id | character varying(48) | N |  |
| device_category | character varying(12) | N |  |
| head | character varying(12) | N |  |
| model | character varying(48) | N |  |
| f1_macro | numeric(7) | Y |  |
| f1_weighted | numeric(7) | Y |  |
| accuracy | numeric(7) | Y |  |
| auc_macro_ovr | numeric(7) | Y |  |
| pr_auc_macro | numeric(7) | Y |  |
| fit_s | numeric(10) | Y |  |
| lb_rank | smallint(16) | Y |  |
| is_champion | boolean | Y | false |
| as_of_date | date | N |  |

### ps3_v2_driver_importance (table, rows=22)

| column | type | nullable | default |
|---|---|---|---|
| id | bigint(64) | N | nextval('ps3_v2_driver_importance_id_seq'::regclass) |
| city_id | text | N |  |
| pipeline_version | text | N |  |
| run_id | text | Y |  |
| device_category | text | Y |  |
| head | text | Y |  |
| model | text | Y |  |
| feature | text | Y |  |
| mean_macro_f1_drop | double precision(53) | Y |  |
| std_macro_f1_drop | double precision(53) | Y |  |
| n_validation_rows | bigint(64) | Y |  |
| importance_scope | text | Y |  |
| interpretation | text | Y |  |
| loaded_at | timestamp with time zone | Y | now() |

### ps3_v25_run_status (table, rows=19)

| column | type | nullable | default |
|---|---|---|---|
| city_id | USER-DEFINED | N |  |
| computed_date | date | Y |  |
| run_id | text | Y |  |
| revision | text | Y |  |
| table_name | text | N |  |
| publish_status | text | Y |  |
| rows | bigint(64) | Y |  |
| path | text | Y |  |
| run_mode | text | Y |  |
| data_as_of_date | text | Y |  |
| is_current_operational_score | boolean | Y |  |
| computed_at_utc | text | Y |  |
| run_is_coherent | boolean | Y |  |
| tables_published | bigint(64) | Y |  |
| tables_total | bigint(64) | Y |  |

### ps3_head_class_metrics (table, rows=16)

| column | type | nullable | default |
|---|---|---|---|
| city_id | USER-DEFINED | N |  |
| run_id | character varying(48) | N |  |
| device_category | character varying(12) | N |  |
| head | character varying(12) | N |  |
| class_label | character varying(48) | N |  |
| precision_val | numeric(7) | Y |  |
| recall_val | numeric(7) | Y |  |
| f1 | numeric(7) | Y |  |
| support | integer(32) | Y |  |
| train_count | integer(32) | Y |  |
| as_of_date | date | N |  |

### ps3_v25_component_summary (table, rows=14)

| column | type | nullable | default |
|---|---|---|---|
| city_id | USER-DEFINED | N |  |
| computed_date | date | Y |  |
| mars_device_category | text | N |  |
| component_attribution | text | N |  |
| dashboard_root_cause_domain | text | N |  |
| dashboard_severity | text | N |  |
| oos_episode_count | bigint(64) | Y |  |
| device_count | bigint(64) | Y |  |
| confirmed_root_cause_count | bigint(64) | Y |  |

### ps3_v25_repeat_interval (table, rows=14)

| column | type | nullable | default |
|---|---|---|---|
| city_id | USER-DEFINED | N |  |
| computed_date | date | Y |  |
| component_attribution | text | N |  |
| mars_device_category | text | N |  |
| attributed_episodes | bigint(64) | Y |  |
| devices | bigint(64) | Y |  |
| episodes_with_a_next | bigint(64) | Y |  |
| median_days_to_next | double precision(53) | Y |  |
| p25_days_to_next | double precision(53) | Y |  |
| mean_days_to_next | double precision(53) | Y |  |
| repeat_rate_within_horizon | double precision(53) | Y |  |
| horizon_days | bigint(64) | Y |  |
| basis | text | Y |  |

### ps3_v25_model_feature_importance (table, rows=12)

| column | type | nullable | default |
|---|---|---|---|
| city_id | USER-DEFINED | N |  |
| computed_date | date | Y |  |
| target | text | N |  |
| model | text | N |  |
| feature | text | N |  |
| importance | double precision(53) | Y |  |
| model_scope | text | N |  |

### ps3_v2_model_comparison (table, rows=10)

| column | type | nullable | default |
|---|---|---|---|
| id | bigint(64) | N | nextval('ps3_v2_model_comparison_id_seq'::regclass) |
| city_id | text | N |  |
| pipeline_version | text | N |  |
| run_id | text | Y |  |
| device_category | text | Y |  |
| head | text | Y |  |
| model | text | Y |  |
| train_f1_macro | double precision(53) | Y |  |
| validation_f1_macro | double precision(53) | Y |  |
| validation_accuracy | double precision(53) | Y |  |
| validation_balanced_accuracy | double precision(53) | Y |  |
| train_validation_f1_gap | double precision(53) | Y |  |
| validation_stability_pass | text | Y |  |
| validation_stability_reasons | text | Y |  |
| test_f1_macro | double precision(53) | Y |  |
| promotion_gate_pass | text | Y |  |
| promotion_gate_reasons | text | Y |  |
| validation_ece | double precision(53) | Y |  |
| validation_roc_auc_macro_ovr | double precision(53) | Y |  |
| decision_threshold | double precision(53) | Y |  |
| fit_status | text | Y |  |
| selected_on_validation_only | text | Y |  |
| test_used_for_model_selection | text | Y |  |
| test_accuracy | double precision(53) | Y |  |
| test_balanced_accuracy | double precision(53) | Y |  |
| test_ece | double precision(53) | Y |  |
| test_constant_predictor | text | Y |  |
| test_macro_precision | double precision(53) | Y |  |
| test_macro_recall | double precision(53) | Y |  |
| test_support | bigint(64) | Y |  |
| test_brier | double precision(53) | Y |  |
| test_log_loss | double precision(53) | Y |  |
| test_roc_auc_macro_ovr | double precision(53) | Y |  |
| test_validation_f1_drop | double precision(53) | Y |  |
| loaded_at | timestamp with time zone | Y | now() |

### ps3_v2_component_reliability (table, rows=9)

| column | type | nullable | default |
|---|---|---|---|
| id | bigint(64) | N | nextval('ps3_v2_component_reliability_id_seq'::regclass) |
| city_id | text | N |  |
| pipeline_version | text | N |  |
| run_id | text | Y |  |
| component_label | text | Y |  |
| incident_count | bigint(64) | Y |  |
| affected_devices | bigint(64) | Y |  |
| affected_serials | bigint(64) | Y |  |
| critical_rate | double precision(53) | Y |  |
| component_label_semantics | text | Y |  |
| portfolio_priority_score | double precision(53) | Y |  |
| critical_incidents | bigint(64) | Y |  |
| latest_incident_at | text | Y |  |
| max_prior_incidents_30d | bigint(64) | Y |  |
| device_category | text | Y |  |
| loaded_at | timestamp with time zone | Y | now() |

### ps3_v25_model_scorecard (table, rows=8)

| column | type | nullable | default |
|---|---|---|---|
| city_id | USER-DEFINED | N |  |
| computed_date | date | Y |  |
| target | text | N |  |
| candidate_model | text | N |  |
| f1_macro | double precision(53) | Y |  |
| f1_weighted | double precision(53) | Y |  |
| balanced_accuracy | double precision(53) | Y |  |
| accuracy | double precision(53) | Y |  |
| mcc | double precision(53) | Y |  |
| majority_f1_macro | double precision(53) | Y |  |
| macro_f1_lift | double precision(53) | Y |  |
| label_coverage | double precision(53) | Y |  |
| quality_gate | text | Y |  |
| detail | text | Y |  |
| model_scope | text | N |  |

### ps3_v25_run_stage_audit (table, rows=8)

| column | type | nullable | default |
|---|---|---|---|
| city_id | USER-DEFINED | N |  |
| computed_date | date | Y |  |
| stage | text | N |  |
| status | text | Y |  |
| detail | text | Y |  |
| at_utc | text | Y |  |
| rows | double precision(53) | Y |  |
| mode | text | Y |  |
| evidence_sources | double precision(53) | Y |  |
| taxonomy_rows | double precision(53) | Y |  |
| model_runs | jsonb | Y |  |

### ps3_head_summary (table, rows=7)

| column | type | nullable | default |
|---|---|---|---|
| city_id | USER-DEFINED | N |  |
| run_id | character varying(48) | N |  |
| device_category | character varying(12) | N |  |
| head | character varying(12) | N |  |
| modeled | boolean | N | true |
| target_col | character varying(40) | Y |  |
| champion | character varying(48) | Y |  |
| n_classes | smallint(16) | Y |  |
| class_labels | text | Y |  |
| test_f1_macro | numeric(7) | Y |  |
| test_f1_weighted | numeric(7) | Y |  |
| test_accuracy | numeric(7) | Y |  |
| test_balanced_accuracy | numeric(7) | Y |  |
| test_auc_macro_ovr | numeric(7) | Y |  |
| test_pr_auc_macro | numeric(7) | Y |  |
| test_cohen_kappa | numeric(7) | Y |  |
| test_mcc | numeric(7) | Y |  |
| test_log_loss | numeric(9) | Y |  |
| macro_f1_floor | numeric(7) | Y |  |
| gate_pass | boolean | Y |  |
| n_train | integer(32) | Y |  |
| n_test | integer(32) | Y |  |
| n_features | integer(32) | Y |  |
| rootcause_source | character varying(40) | Y |  |
| as_of_date | date | N |  |

### ps3_v25_root_cause_evidence_audit (table, rows=7)

| column | type | nullable | default |
|---|---|---|---|
| city_id | USER-DEFINED | N |  |
| computed_date | date | Y |  |
| source | text | N |  |
| status | text | Y |  |
| reference | text | Y |  |
| rows | double precision(53) | Y |  |
| detail | text | Y |  |

### ps3_v25_source_column_profile (table, rows=7)

| column | type | nullable | default |
|---|---|---|---|
| city_id | USER-DEFINED | N |  |
| computed_date | date | Y |  |
| column | text | N |  |
| status | text | Y |  |
| rows | bigint(64) | Y |  |
| non_null | bigint(64) | Y |  |
| null_rate | double precision(53) | Y |  |
| distinct_values | bigint(64) | Y |  |
| deterministic_given_event_type | boolean | Y |  |
| usable_as_observed_label | boolean | Y |  |
| note | text | Y |  |

### ps3_v25_causal_effects (table, rows=6)

| column | type | nullable | default |
|---|---|---|---|
| city_id | USER-DEFINED | N |  |
| computed_date | date | Y |  |
| treatment_component | text | N |  |
| outcome | text | N |  |
| estimator | text | Y |  |
| average_treatment_effect | double precision(53) | Y |  |
| standard_error | double precision(53) | Y |  |
| ci_low_95 | double precision(53) | Y |  |
| ci_high_95 | double precision(53) | Y |  |
| significant_95 | boolean | Y |  |
| episodes_used | bigint(64) | Y |  |
| treated_episodes | bigint(64) | Y |  |
| control_episodes | bigint(64) | Y |  |
| overlap_share | double precision(53) | Y |  |
| treated_prevalence | double precision(53) | Y |  |
| propensity_p01 | double precision(53) | Y |  |
| propensity_p99 | double precision(53) | Y |  |
| models_converged | boolean | Y |  |
| status | text | Y |  |
| interpretation | text | Y |  |
| p_value_two_sided | double precision(53) | Y |  |
| p_value_holm | double precision(53) | Y |  |
| significant_95_holm | boolean | Y |  |
| multiplicity_note | text | Y |  |

### ps3_v2_display_policy (table, rows=6)

| column | type | nullable | default |
|---|---|---|---|
| id | bigint(64) | N | nextval('ps3_v2_display_policy_id_seq'::regclass) |
| city_id | text | N |  |
| pipeline_version | text | N |  |
| run_id | text | Y |  |
| dashboard_element | text | Y |  |
| dataset | text | Y |  |
| display_allowed | text | Y |  |
| row_count | bigint(64) | Y |  |
| display_label | text | Y |  |
| required_disclaimer | text | Y |  |
| loaded_at | timestamp with time zone | Y | now() |

### ps3_v2_run_scorecard (table, rows=5)

| column | type | nullable | default |
|---|---|---|---|
| id | bigint(64) | N | nextval('ps3_v2_run_scorecard_id_seq'::regclass) |
| city_id | text | N |  |
| pipeline_version | text | N |  |
| run_id | text | Y |  |
| device_category | text | Y |  |
| head | text | Y |  |
| status | text | Y |  |
| champion | text | Y |  |
| validation_f1_macro | double precision(53) | Y |  |
| test_f1_macro | double precision(53) | Y |  |
| promotion_gate_pass | text | Y |  |
| shap_status | text | Y |  |
| shap_rows | bigint(64) | Y |  |
| causal_status | text | Y |  |
| causal_dashboard_ready_rows | bigint(64) | Y |  |
| reason | text | Y |  |
| loaded_at | timestamp with time zone | Y | now() |

### ps3_v2_causal_effects (table, rows=4)

| column | type | nullable | default |
|---|---|---|---|
| id | bigint(64) | N | nextval('ps3_v2_causal_effects_id_seq'::regclass) |
| city_id | text | N |  |
| pipeline_version | text | N |  |
| run_id | text | Y |  |
| device_category | text | Y |  |
| treatment_id | text | Y |  |
| treatment_column | text | Y |  |
| treatment_definition | text | Y |  |
| outcome_id | text | Y |  |
| outcome_label | text | Y |  |
| target_semantics | text | Y |  |
| estimation_status | text | Y |  |
| n_crossfit_rows | bigint(64) | Y |  |
| treated_rows | bigint(64) | Y |  |
| control_rows | bigint(64) | Y |  |
| event_rows | bigint(64) | Y |  |
| non_event_rows | bigint(64) | Y |  |
| aipw_risk_difference | double precision(53) | Y |  |
| confidence_interval_95_low | double precision(53) | Y |  |
| confidence_interval_95_high | double precision(53) | Y |  |
| standard_error | double precision(53) | Y |  |
| p_value_normal_approx | double precision(53) | Y |  |
| propensity_overlap_share | double precision(53) | Y |  |
| max_abs_weighted_smd_numeric | double precision(53) | Y |  |
| balance_pass | text | Y |  |
| overlap_pass | text | Y |  |
| dashboard_ready | text | Y |  |
| evidence_level | text | Y |  |
| scope | text | Y |  |
| reason | text | Y |  |
| loaded_at | timestamp with time zone | Y | now() |

### ps3_v2_promotion_status (table, rows=4)

| column | type | nullable | default |
|---|---|---|---|
| id | bigint(64) | N | nextval('ps3_v2_promotion_status_id_seq'::regclass) |
| city_id | text | N |  |
| pipeline_version | text | N |  |
| run_id | text | Y |  |
| device_category | text | Y |  |
| head | text | Y |  |
| status | text | Y |  |
| champion_selected_on_validation | text | Y |  |
| action_feed_eligible | text | Y |  |
| target | text | Y |  |
| target_semantics | text | Y |  |
| reason | text | Y |  |
| loaded_at | timestamp with time zone | Y | now() |

### ps3_category_coverage (table, rows=3)

| column | type | nullable | default |
|---|---|---|---|
| city_id | USER-DEFINED | N |  |
| run_id | character varying(40) | N |  |
| device_category | character varying(12) | N |  |
| modeled | boolean | N |  |
| n_ps3_incidents | bigint(64) | N | 0 |
| n_devices_scored | integer(32) | N | 0 |
| n_serials_scored | integer(32) | N | 0 |
| source_table | character varying(160) | Y |  |
| exclusion_reason | character varying(600) | Y |  |
| alternative_coverage | character varying(300) | Y |  |
| remediation | character varying(400) | Y |  |
| as_of_date | date | N |  |

### ps3_v25_commanded_split (table, rows=3)

| column | type | nullable | default |
|---|---|---|---|
| city_id | USER-DEFINED | N |  |
| computed_date | date | Y |  |
| mars_device_category | text | N |  |
| oos_episodes | bigint(64) | Y |  |
| commanded_signal_episodes | bigint(64) | Y |  |
| failure_only_episodes | bigint(64) | Y |  |
| basis | text | Y |  |

### ps3_v25_label_maturity (table, rows=3)

| column | type | nullable | default |
|---|---|---|---|
| city_id | USER-DEFINED | N |  |
| computed_date | date | Y |  |
| mars_device_category | text | N |  |
| oos_episode_count | bigint(64) | Y |  |
| observed_severity_count | bigint(64) | Y |  |
| confirmed_root_cause_count | bigint(64) | Y |  |
| candidate_root_cause_count | bigint(64) | Y |  |
| component_attribution_count | bigint(64) | Y |  |
| severity_coverage | double precision(53) | Y |  |
| confirmed_root_cause_coverage | double precision(53) | Y |  |

### ps3_v25_prediction_explainability (table, rows=3)

| column | type | nullable | default |
|---|---|---|---|
| city_id | USER-DEFINED | N |  |
| computed_date | date | Y |  |
| target | text | N |  |
| model_output | text | N |  |
| oos_episode_id | text | N |  |
| device_id | text | Y |  |
| mars_device_category | text | Y |  |
| predicted_label | text | Y |  |
| feature | text | N |  |
| shap_value | double precision(53) | Y |  |
| abs_shap_value | double precision(53) | Y |  |
| explanation_status | text | Y |  |
| explanation_note | text | Y |  |
| model_scope | text | Y |  |

### ps3_v2_readiness (table, rows=3)

| column | type | nullable | default |
|---|---|---|---|
| id | bigint(64) | N | nextval('ps3_v2_readiness_id_seq'::regclass) |
| city_id | text | N |  |
| pipeline_version | text | N |  |
| run_id | text | Y |  |
| device_category | text | Y |  |
| gold_rows | bigint(64) | Y |  |
| severity_label_coverage | double precision(53) | Y |  |
| component_label_coverage | double precision(53) | Y |  |
| configured_enrichment_sources | text | Y |  |
| loaded_enrichment_sources | text | Y |  |
| data_readiness_status | text | Y |  |
| reason | text | Y |  |
| loaded_at | timestamp with time zone | Y | now() |

### ps3_model_runs (table, rows=2)

| column | type | nullable | default |
|---|---|---|---|
| city_id | USER-DEFINED | N |  |
| run_id | character varying(48) | N |  |
| run_ts | timestamp with time zone | N |  |
| run_kind | character varying(16) | N | 'train'::character varying |
| source_notebook | character varying(160) | Y |  |
| git_sha | character varying(40) | Y |  |
| n_incidents | integer(32) | Y |  |
| device_scope | character varying(60) | Y |  |
| endpoint_name | character varying(80) | Y |  |
| serving_image | character varying(200) | Y |  |
| mlflow_version | character varying(16) | Y |  |
| sm_package_arn | character varying(200) | Y |  |
| severity_collapse_verified | boolean | Y | false |
| notes | text | Y |  |
| as_of_date | date | N |  |

### ps3_severity_summary (table, rows=1)

| column | type | nullable | default |
|---|---|---|---|
| city_id | USER-DEFINED | N |  |
| champion_model | character varying(48) | N |  |
| test_auc_macro | numeric(6) | Y |  |
| test_f1_macro | numeric(6) | Y |  |
| test_accuracy | numeric(6) | Y |  |
| n_incidents | integer(32) | Y |  |
| n_major | integer(32) | Y |  |
| n_critical | integer(32) | Y |  |
| device_note | character varying(120) | Y |  |
| date_start | date | Y |  |
| date_end | date | Y |  |
| feasibility_pct | smallint(16) | Y |  |
| is_root_cause | boolean | N | false |
| true_rootcause_status | character varying(200) | Y |  |
| dominant_feature | character varying(48) | Y |  |
| dominant_feature_shap | numeric(5) | Y |  |
| endpoint_name | character varying(64) | Y |  |
| mlflow_version | character varying(16) | Y |  |
| sm_package | character varying(64) | Y |  |
| serving_image | character varying(160) | Y |  |
| dashboard_ready | boolean | N | true |
| as_of_date | date | N |  |

### ps3_v25_oos_source_audit (table, rows=1)

| column | type | nullable | default |
|---|---|---|---|
| city_id | USER-DEFINED | N |  |
| computed_date | date | Y |  |
| source | text | N |  |
| status | text | Y |  |
| reference | text | Y |  |
| detail | text | Y |  |
| engine | text | Y |  |
| pushed_down | text | Y |  |
| scanned_rows | bigint(64) | Y |  |
| window_start | text | Y |  |
| window_end | text | Y |  |
| current_device_filter | text | Y |  |
| rows_removed_by_current_filter | bigint(64) | Y |  |
| selected_rows | bigint(64) | Y |  |

### ps3_v2_data_freshness (table, rows=1)

| column | type | nullable | default |
|---|---|---|---|
| id | bigint(64) | N | nextval('ps3_v2_data_freshness_id_seq'::regclass) |
| city_id | text | N |  |
| pipeline_version | text | N |  |
| run_id | text | Y |  |
| data_as_of_timestamp | text | Y |  |
| run_timestamp_utc | text | Y |  |
| input_rows | bigint(64) | Y |  |
| eligible_rows | bigint(64) | Y |  |
| event_start | text | Y |  |
| event_end | text | Y |  |
| freshness_scope | text | Y |  |
| loaded_at | timestamp with time zone | Y | now() |

### ps3_v2_runs (table, rows=1)

| column | type | nullable | default |
|---|---|---|---|
| city_id | text | N |  |
| pipeline_version | text | N |  |
| run_id | text | N |  |
| s3_prefix | text | Y |  |
| tables_loaded | integer(32) | Y |  |
| rows_loaded | bigint(64) | Y |  |
| loaded_at | timestamp with time zone | Y | now() |

### ps3_device_metrics (table, rows=0)

| column | type | nullable | default |
|---|---|---|---|
| city_id | USER-DEFINED | N |  |
| device | character varying(10) | N |  |
| split | character varying(8) | N |  |
| n_incidents | integer(32) | Y |  |
| f1_macro | numeric(6) | Y |  |
| accuracy | numeric(6) | Y |  |
| as_of_date | date | N |  |

### ps3_head_feature_importance (table, rows=0)

| column | type | nullable | default |
|---|---|---|---|
| city_id | USER-DEFINED | N |  |
| run_id | character varying(48) | N |  |
| device_category | character varying(12) | N |  |
| head | character varying(12) | N |  |
| feature_rank | smallint(16) | N |  |
| feature_name | character varying(80) | N |  |
| shap_importance | numeric(12) | Y |  |
| as_of_date | date | N |  |

### ps3_prediction_explainability (table, rows=0)

| column | type | nullable | default |
|---|---|---|---|
| explainability_id | bigint(64) | N |  |
| prediction_id | bigint(64) | N |  |
| feature_rank | smallint(16) | N |  |
| feature_name | character varying(60) | N |  |
| shap_value | numeric(9) | N |  |
| feature_value | numeric(16) | Y |  |

### ps3_severity_drivers (table, rows=0)

| column | type | nullable | default |
|---|---|---|---|
| city_id | USER-DEFINED | N |  |
| feature | character varying(48) | N |  |
| shap_importance | numeric(6) | Y |  |
| solo_auc | numeric(6) | Y |  |
| driver_rank | smallint(16) | N |  |
| as_of_date | date | N |  |

### ps3_severity_predictions (table, rows=0)

| column | type | nullable | default |
|---|---|---|---|
| id | bigint(64) | N | nextval('ps3_severity_predictions_id_seq'::regclass) |
| city_id | USER-DEFINED | N |  |
| device_id | character varying(40) | Y |  |
| mars_device_category | character varying(12) | Y |  |
| incident_id | character varying(40) | Y |  |
| incident_dtm | timestamp with time zone | Y |  |
| sn_event_code_id | integer(32) | Y |  |
| predicted_label | character varying(10) | Y |  |
| proba_critical | numeric(6) | Y |  |
| actual_label | character varying(10) | Y |  |
| model_version | character varying(16) | Y | 'v8'::character varying |
| scored_at | timestamp with time zone | Y | now() |

### v_ps3_category_coverage (view, rows=None)

| column | type | nullable | default |
|---|---|---|---|
| city_id | USER-DEFINED | Y |  |
| run_id | character varying(40) | Y |  |
| device_category | character varying(12) | Y |  |
| modeled | boolean | Y |  |
| n_ps3_incidents | bigint(64) | Y |  |
| n_devices_scored | integer(32) | Y |  |
| n_serials_scored | integer(32) | Y |  |
| source_table | character varying(160) | Y |  |
| exclusion_reason | character varying(600) | Y |  |
| alternative_coverage | character varying(300) | Y |  |
| remediation | character varying(400) | Y |  |
| n_fleet_devices | bigint(64) | Y |  |
| n_fleet_serials | bigint(64) | Y |  |
| n_fleet_components | bigint(64) | Y |  |
| device_coverage_ratio | numeric | Y |  |
| as_of_date | date | Y |  |

### v_ps3_collapse_health (view, rows=None)

| column | type | nullable | default |
|---|---|---|---|
| city_id | USER-DEFINED | Y |  |
| run_id | character varying(48) | Y |  |
| mars_device_category | character varying(12) | Y |  |
| pred_severity_collapsed | character varying(16) | Y |  |
| n | bigint(64) | Y |  |
| collapse_share | numeric | Y |  |
| source_run_id | character varying(48) | Y |  |

### v_ps3_device_360 (view, rows=None)

| column | type | nullable | default |
|---|---|---|---|
| city_id | USER-DEFINED | Y |  |
| run_id | character varying(48) | Y |  |
| device_id | character varying(40) | Y |  |
| mars_device_category | character varying(12) | Y |  |
| n_incidents | integer(32) | Y |  |
| pct_critical_pred | numeric | Y |  |
| dominant_pred_severity | character varying | Y |  |
| dominant_pred_component | character varying | Y |  |
| avg_component_age_days | numeric(10) | Y |  |
| last_incident_dtm | timestamp with time zone | Y |  |
| computed_date | date | Y |  |
| severity_shippable | boolean | Y |  |
| rootcause_shippable | boolean | Y |  |
| severity_f1_macro | numeric | Y |  |
| severity_floor | numeric | Y |  |
| rootcause_f1_macro | numeric | Y |  |
| rootcause_floor | numeric | Y |  |
| n_serials | bigint(64) | Y |  |
| run_ts | timestamp with time zone | Y |  |
| severity_collapse_verified | boolean | Y |  |

### v_ps3_device_all (view, rows=None)

| column | type | nullable | default |
|---|---|---|---|
| city_id | USER-DEFINED | Y |  |
| device_id | character varying(40) | Y |  |
| mars_device_category | character varying(12) | Y |  |
| coverage_status | text | Y |  |
| run_id | character varying(48) | Y |  |
| n_incidents | integer(32) | Y |  |
| pct_critical_pred | numeric | Y |  |
| dominant_pred_severity | character varying | Y |  |
| dominant_pred_component | character varying | Y |  |
| avg_component_age_days | numeric | Y |  |
| last_incident_dtm | timestamp with time zone | Y |  |
| computed_date | date | Y |  |
| severity_shippable | boolean | Y |  |
| rootcause_shippable | boolean | Y |  |
| severity_f1_macro | numeric | Y |  |
| severity_floor | numeric | Y |  |
| rootcause_f1_macro | numeric | Y |  |
| rootcause_floor | numeric | Y |  |
| n_serials | bigint(64) | Y |  |
| n_component_types | bigint(64) | Y |  |
| components | text | Y |  |
| fleet_as_of_date | date | Y |  |

### v_ps3_device_risk (view, rows=None)

| column | type | nullable | default |
|---|---|---|---|
| city_id | USER-DEFINED | Y |  |
| run_id | character varying(48) | Y |  |
| device_id | character varying(40) | Y |  |
| mars_device_category | character varying(12) | Y |  |
| n_incidents | integer(32) | Y |  |
| pct_critical_pred | numeric | Y |  |
| dominant_pred_severity | character varying | Y |  |
| dominant_pred_component | character varying | Y |  |
| avg_component_age_days | numeric(10) | Y |  |
| last_incident_dtm | timestamp with time zone | Y |  |
| computed_date | date | Y |  |
| severity_shippable | boolean | Y |  |
| rootcause_shippable | boolean | Y |  |
| severity_f1_macro | numeric | Y |  |
| severity_floor | numeric | Y |  |
| rootcause_f1_macro | numeric | Y |  |
| rootcause_floor | numeric | Y |  |

### v_ps3_head_gates (view, rows=None)

| column | type | nullable | default |
|---|---|---|---|
| city_id | USER-DEFINED | Y |  |
| run_id | character varying(48) | Y |  |
| device_category | character varying(12) | Y |  |
| severity_shippable | boolean | Y |  |
| rootcause_shippable | boolean | Y |  |
| severity_f1_macro | numeric | Y |  |
| rootcause_f1_macro | numeric | Y |  |
| severity_floor | numeric | Y |  |
| rootcause_floor | numeric | Y |  |
| both_heads_modeled | boolean | Y |  |

### v_ps3_latest_run (view, rows=None)

| column | type | nullable | default |
|---|---|---|---|
| city_id | USER-DEFINED | Y |  |
| run_id | character varying(48) | Y |  |
| run_ts | timestamp with time zone | Y |  |
| as_of_date | date | Y |  |
| n_incidents | integer(32) | Y |  |
| device_scope | character varying(60) | Y |  |
| severity_collapse_verified | boolean | Y |  |
| run_kind | character varying(16) | Y |  |

### v_ps3_rollup_all (view, rows=None)

| column | type | nullable | default |
|---|---|---|---|
| city_id | USER-DEFINED | Y |  |
| device_category | character varying(12) | Y |  |
| n_devices | bigint(64) | Y |  |
| n_devices_scored | bigint(64) | Y |  |
| n_incidents | bigint(64) | Y |  |
| n_scored | bigint(64) | Y |  |
| n_gated | bigint(64) | Y |  |
| avg_pct_critical | numeric | Y |  |
| avg_component_age_days | numeric | Y |  |
| last_incident_dtm | timestamp with time zone | Y |  |
| computed_date | date | Y |  |
| n_serials | numeric | Y |  |
| n_distinct_serials | numeric | Y |  |
| modelled | boolean | Y |  |

### v_ps3_run_registry (view, rows=None)

| column | type | nullable | default |
|---|---|---|---|
| city_id | USER-DEFINED | Y |  |
| run_id | character varying(48) | Y |  |
| run_kind | character varying(16) | Y |  |
| run_ts | timestamp with time zone | Y |  |
| as_of_date | date | Y |  |
| device_scope | character varying(60) | Y |  |
| source_notebook | character varying(160) | Y |  |
| is_current | boolean | Y |  |
| n_head_rows | bigint(64) | Y |  |
| n_device_rows | bigint(64) | Y |  |
| n_serial_rows | bigint(64) | Y |  |

### v_ps3_serial_all (view, rows=None)

| column | type | nullable | default |
|---|---|---|---|
| city_id | USER-DEFINED | Y |  |
| device_id | character varying(40) | Y |  |
| matched_serial_nbr | character varying(64) | Y |  |
| mars_device_category | character varying(12) | Y |  |
| coverage_status | text | Y |  |
| run_id | character varying(48) | Y |  |
| n_incidents | integer(32) | Y |  |
| component_age_days | numeric | Y |  |
| component_description | character varying(120) | Y |  |
| age_is_negative | boolean | Y |  |
| dominant_pred_component | character varying | Y |  |
| pct_critical_pred | numeric | Y |  |
| last_incident_dtm | timestamp with time zone | Y |  |
| computed_date | date | Y |  |
| severity_shippable | boolean | Y |  |
| rootcause_shippable | boolean | Y |  |
| fleet_as_of_date | date | Y |  |

### v_ps3_serial_risk (view, rows=None)

| column | type | nullable | default |
|---|---|---|---|
| city_id | USER-DEFINED | Y |  |
| run_id | character varying(48) | Y |  |
| device_id | character varying(40) | Y |  |
| matched_serial_nbr | character varying(64) | Y |  |
| mars_device_category | character varying(12) | Y |  |
| n_incidents | integer(32) | Y |  |
| component_age_days | numeric(10) | Y |  |
| pct_critical_pred | numeric | Y |  |
| dominant_pred_component | character varying | Y |  |
| last_incident_dtm | timestamp with time zone | Y |  |
| computed_date | date | Y |  |
| severity_shippable | boolean | Y |  |
| rootcause_shippable | boolean | Y |  |

### v_ps3_two_head_scorecard (view, rows=None)

| column | type | nullable | default |
|---|---|---|---|
| city_id | USER-DEFINED | Y |  |
| run_id | character varying(48) | Y |  |
| device_category | character varying(12) | Y |  |
| head | character varying(12) | Y |  |
| modeled | boolean | Y |  |
| champion | character varying(48) | Y |  |
| target_col | character varying(40) | Y |  |
| n_classes | smallint(16) | Y |  |
| class_labels | text | Y |  |
| test_f1_macro | numeric(7) | Y |  |
| test_auc_macro_ovr | numeric(7) | Y |  |
| test_pr_auc_macro | numeric(7) | Y |  |
| macro_f1_floor | numeric(7) | Y |  |
| gate_pass | boolean | Y |  |
| n_train | integer(32) | Y |  |
| n_test | integer(32) | Y |  |
| n_features | integer(32) | Y |  |
| run_ts | timestamp with time zone | Y |  |
| endpoint_name | character varying(80) | Y |  |
| serving_image | character varying(200) | Y |  |
| mlflow_version | character varying(16) | Y |  |

### v_ps3_v25_severity_maturity (view, rows=None)

| column | type | nullable | default |
|---|---|---|---|
| city_id | USER-DEFINED | Y |  |
| run_id | text | Y |  |
| mars_device_category | text | Y |  |
| dashboard_severity | text | Y |  |
| severity_status | text | Y |  |
| n | bigint(64) | Y |  |
| share | numeric | Y |  |

### v_ps3_v25_status (view, rows=None)

| column | type | nullable | default |
|---|---|---|---|
| table_name | text | Y |  |
| city_id | USER-DEFINED | Y |  |
| computed_date | date | Y |  |
| rows | bigint(64) | Y |  |

### v_ps3_v2_causal (view, rows=None)

| column | type | nullable | default |
|---|---|---|---|
| id | bigint(64) | Y |  |
| city_id | text | Y |  |
| pipeline_version | text | Y |  |
| run_id | text | Y |  |
| device_category | text | Y |  |
| treatment_id | text | Y |  |
| treatment_column | text | Y |  |
| treatment_definition | text | Y |  |
| outcome_id | text | Y |  |
| outcome_label | text | Y |  |
| target_semantics | text | Y |  |
| estimation_status | text | Y |  |
| n_crossfit_rows | bigint(64) | Y |  |
| treated_rows | bigint(64) | Y |  |
| control_rows | bigint(64) | Y |  |
| event_rows | bigint(64) | Y |  |
| non_event_rows | bigint(64) | Y |  |
| aipw_risk_difference | double precision(53) | Y |  |
| confidence_interval_95_low | double precision(53) | Y |  |
| confidence_interval_95_high | double precision(53) | Y |  |
| standard_error | double precision(53) | Y |  |
| p_value_normal_approx | double precision(53) | Y |  |
| propensity_overlap_share | double precision(53) | Y |  |
| max_abs_weighted_smd_numeric | double precision(53) | Y |  |
| balance_pass | text | Y |  |
| overlap_pass | text | Y |  |
| dashboard_ready | text | Y |  |
| evidence_level | text | Y |  |
| scope | text | Y |  |
| reason | text | Y |  |
| loaded_at | timestamp with time zone | Y |  |
| causal_verdict | text | Y |  |

### v_ps3_v2_current (view, rows=None)

| column | type | nullable | default |
|---|---|---|---|
| city_id | text | Y |  |
| pipeline_version | text | Y |  |
| run_id | text | Y |  |
| s3_prefix | text | Y |  |
| tables_loaded | integer(32) | Y |  |
| rows_loaded | bigint(64) | Y |  |
| loaded_at | timestamp with time zone | Y |  |

### v_ps3_v2_policy (view, rows=None)

| column | type | nullable | default |
|---|---|---|---|
| id | bigint(64) | Y |  |
| city_id | text | Y |  |
| pipeline_version | text | Y |  |
| run_id | text | Y |  |
| dashboard_element | text | Y |  |
| dataset | text | Y |  |
| display_allowed | text | Y |  |
| row_count | bigint(64) | Y |  |
| display_label | text | Y |  |
| required_disclaimer | text | Y |  |
| loaded_at | timestamp with time zone | Y |  |

### v_ps3_v2_queue (view, rows=None)

| column | type | nullable | default |
|---|---|---|---|
| id | bigint(64) | Y |  |
| city_id | text | Y |  |
| pipeline_version | text | Y |  |
| run_id | text | Y |  |
| device_category | text | Y |  |
| device_id | text | Y |  |
| serial_number | text | Y |  |
| facility_id | text | Y |  |
| source_event_timestamp | text | Y |  |
| data_as_of_timestamp | text | Y |  |
| availability_event_id | text | Y |  |
| predicted_severity | text | Y |  |
| critical_probability | double precision(53) | Y |  |
| major_probability | double precision(53) | Y |  |
| prediction_confidence | double precision(53) | Y |  |
| decision_threshold | double precision(53) | Y |  |
| action_band | text | Y |  |
| action_priority_score | double precision(53) | Y |  |
| prior_incidents_24h | bigint(64) | Y |  |
| prior_incidents_7d | bigint(64) | Y |  |
| prior_incidents_30d | bigint(64) | Y |  |
| prior_critical_rate_30d | double precision(53) | Y |  |
| hours_since_prior_incident | double precision(53) | Y |  |
| model | text | Y |  |
| model_status | text | Y |  |
| score_generated_at_utc | text | Y |  |
| prediction_scope | text | Y |  |
| loaded_at | timestamp with time zone | Y |  |
| recurrence_note | text | Y |  |
| confidence_band | text | Y |  |

### v_ps3_v2_rootcause (view, rows=None)

| column | type | nullable | default |
|---|---|---|---|
| city_id | text | Y |  |
| device_category | text | Y |  |
| device_id | text | Y |  |
| serial_number | text | Y |  |
| component_label | text | Y |  |
| component_label_semantics | text | Y |  |
| incident_count | bigint(64) | Y |  |
| critical_rate | double precision(53) | Y |  |
| recurrence_30d | bigint(64) | Y |  |
| latest_incident_at | text | Y |  |
| critical_weighted | numeric | Y |  |
| taxonomy_status | text | Y |  |
| taxonomy_action | text | Y |  |
| taxonomy_note | text | Y |  |
| basis | text | Y |  |
| pipeline_version | text | Y |  |
| run_id | text | Y |  |

### v_ps3_v2_rootcause_concentration (view, rows=None)

| column | type | nullable | default |
|---|---|---|---|
| city_id | text | Y |  |
| device_category | text | Y |  |
| device_id | text | Y |  |
| serial_number | text | Y |  |
| total_incidents | numeric | Y |  |
| top_component_incidents | bigint(64) | Y |  |
| distinct_components | bigint(64) | Y |  |
| pipeline_version | text | Y |  |
| concentration | numeric | Y |  |
| concentration_verdict | text | Y |  |

### v_ps3_v2_rootcause_rollup (view, rows=None)

| column | type | nullable | default |
|---|---|---|---|
| city_id | text | Y |  |
| device_category | text | Y |  |
| component_label | text | Y |  |
| component_label_semantics | text | Y |  |
| devices_affected | bigint(64) | Y |  |
| serials_affected | bigint(64) | Y |  |
| incidents | numeric | Y |  |
| mean_critical_rate | numeric | Y |  |
| critical_weighted | numeric | Y |  |
| recurrence_30d | numeric | Y |  |
| taxonomy_note | text | Y |  |
| pipeline_version | text | Y |  |

### v_ps3_v2_scorecard (view, rows=None)

| column | type | nullable | default |
|---|---|---|---|
| id | bigint(64) | Y |  |
| city_id | text | Y |  |
| pipeline_version | text | Y |  |
| run_id | text | Y |  |
| device_category | text | Y |  |
| head | text | Y |  |
| status | text | Y |  |
| champion | text | Y |  |
| validation_f1_macro | double precision(53) | Y |  |
| test_f1_macro | double precision(53) | Y |  |
| promotion_gate_pass | text | Y |  |
| shap_status | text | Y |  |
| shap_rows | bigint(64) | Y |  |
| causal_status | text | Y |  |
| causal_dashboard_ready_rows | bigint(64) | Y |  |
| reason | text | Y |  |
| loaded_at | timestamp with time zone | Y |  |
| gate_note | text | Y |  |
| val_minus_test | numeric | Y |  |

### v_ps3_v2_shap (view, rows=None)

| column | type | nullable | default |
|---|---|---|---|
| id | bigint(64) | Y |  |
| city_id | text | Y |  |
| pipeline_version | text | Y |  |
| run_id | text | Y |  |
| device_category | text | Y |  |
| head | text | Y |  |
| availability_event_id | text | Y |  |
| device_id | text | Y |  |
| serial_number | text | Y |  |
| facility_id | text | Y |  |
| event_timestamp | text | Y |  |
| actual_label | text | Y |  |
| predicted_label | text | Y |  |
| predicted_probability | double precision(53) | Y |  |
| feature | text | Y |  |
| feature_value | text | Y |  |
| shap_value | double precision(53) | Y |  |
| abs_shap_value | double precision(53) | Y |  |
| feature_rank | bigint(64) | Y |  |
| model | text | Y |  |
| explainer | text | Y |  |
| publication_status | text | Y |  |
| explanation_scope | text | Y |  |
| loaded_at | timestamp with time zone | Y |  |

### v_ps3_v2_table_status (view, rows=None)

| column | type | nullable | default |
|---|---|---|---|
| table_name | text | Y |  |
| n_rows | bigint(64) | Y |  |

## PS4 — 25 tables (20,927 rows), 20 views

| object | kind | rows | cols | primary key |
|---|---|---:|---:|---|
| ps4_cluster_assignments | table | 8,978 | 11 | city_id, asof_date, device_type, device_key |
| ps4_weekly_device_summary | table | 7,742 | 24 | city_id, device_id, device_type, week_start, pipeline_version |
| ps4_anomaly_timeline | table | 3,048 | 6 | city_id, asof_date, bucket_time, device_type |
| ps4_weekly_alerts | table | 1,108 | 24 | city_id, device_id, device_type, week_start, pipeline_version |
| ps4_cluster_summary | table | 25 | 9 | city_id, asof_date, device_type, cluster_id |
| ps4_cluster_profile | table | 9 | 13 | city_id, device_type, cluster_id, pipeline_version |
| ps4_weekly_timeline | table | 6 | 13 | city_id, device_type, week_start, pipeline_version |
| ps4_anomaly_signal_detail | table | 3 | 6 | - |
| ps4_cluster_quality | table | 3 | 10 | city_id, device_type, pipeline_version |
| ps4_anomaly_predictions | table | 2 | 10 | - |
| ps4_anomaly_rate_forecast | table | 2 | 8 | - |
| ps4_v3_runs | table | 1 | 12 | city_id, pipeline_version, run_id |
| ps4_anomalies | table | 0 | 14 | city_id, asof_date, device_id, detected_at |
| ps4_anomaly_alerts | table | 0 | 15 | id |
| ps4_device_daily | table | 0 | 14 | city_id, run_id, device_id, transit_day |
| ps4_device_day | table | 0 | 18 | city_id, device_id, transit_day |
| ps4_device_hourly | table | 0 | 12 | city_id, run_id, device_id, hour_bucket |
| ps4_device_lifetime | table | 0 | 14 | city_id, device_id |
| ps4_leakage_scan | table | 0 | 6 | city_id, run_id, feature_name |
| ps4_model_leaderboard | table | 0 | 18 | city_id, run_id, model |
| ps4_outlier_scores | table | 0 | 9 | city_id, asof_date, device_id, recorded_at |
| ps4_runs | table | 0 | 21 | city_id, run_id |
| ps4_signal_summary | table | 0 | 10 | city_id, run_id, signal_name, device_category, split_name |
| ps4_spc_thresholds | table | 0 | 8 | city_id, run_id, device_category |
| ps4_station_anomaly | table | 0 | 9 | city_id, run_id, facility_id, device_category |
| v_ps4_anomalies | view | - | 14 | - |
| v_ps4_cluster_profile | view | - | 10 | - |
| v_ps4_cluster_quality | view | - | 11 | - |
| v_ps4_day_anomalies | view | - | 14 | - |
| v_ps4_device_anomaly | view | - | 13 | - |
| v_ps4_device_status | view | - | 17 | - |
| v_ps4_latest_asof | view | - | 2 | - |
| v_ps4_latest_run | view | - | 5 | - |
| v_ps4_leaderboard | view | - | 12 | - |
| v_ps4_outlier_scores | view | - | 11 | - |
| v_ps4_timeline | view | - | 6 | - |
| v_ps4_v3_cluster_profile | view | - | 16 | - |
| v_ps4_v3_current | view | - | 10 | - |
| v_ps4_v3_table_status | view | - | 2 | - |
| v_ps4_weekly_alert_reconcile | view | - | 6 | - |
| v_ps4_weekly_alerts | view | - | 23 | - |
| v_ps4_weekly_device | view | - | 24 | - |
| v_ps4_weekly_facility | view | - | 8 | - |
| v_ps4_weekly_persistent | view | - | 11 | - |
| v_ps4_weekly_timeline | view | - | 13 | - |

### ps4_cluster_assignments (table, rows=8978)

| column | type | nullable | default |
|---|---|---|---|
| city_id | USER-DEFINED | N |  |
| asof_date | date | N |  |
| device_type | character varying(12) | N |  |
| device_key | character varying(64) | N |  |
| device_id | character varying(40) | Y |  |
| cluster_id | integer(32) | N |  |
| cluster_confidence | numeric(10) | Y |  |
| champion_pipeline | character varying(80) | Y |  |
| champion_run_id | character varying(64) | Y |  |
| champion_silhouette | numeric(10) | Y |  |
| engine | character varying(16) | Y |  |

### ps4_weekly_device_summary (table, rows=7742)

| column | type | nullable | default |
|---|---|---|---|
| city_id | text | N |  |
| device_id | text | N |  |
| device_type | text | N |  |
| week_start | date | N |  |
| pipeline_version | text | N |  |
| week_end | date | Y |  |
| facility_id | text | Y |  |
| observed_days | integer(32) | Y |  |
| observed_hours | integer(32) | Y |  |
| candidate_days | integer(32) | Y |  |
| actionable_days | integer(32) | Y |  |
| is_actionable_week | smallint(16) | Y |  |
| anomaly_score_max | double precision(53) | Y |  |
| anomaly_score_mean | double precision(53) | Y |  |
| anomaly_score_p95 | double precision(53) | Y |  |
| max_abs_z | double precision(53) | Y |  |
| cluster_distance_ratio_max | double precision(53) | Y |  |
| has_low_coverage_day | smallint(16) | Y |  |
| dominant_cluster_id | integer(32) | Y |  |
| anomaly_types | text | Y |  |
| severity | text | Y |  |
| asof_date | date | Y |  |
| run_id | text | Y |  |
| loaded_at | timestamp with time zone | Y | now() |

### ps4_anomaly_timeline (table, rows=3048)

| column | type | nullable | default |
|---|---|---|---|
| city_id | USER-DEFINED | N |  |
| asof_date | date | N |  |
| bucket_time | timestamp with time zone | N |  |
| device_type | character varying(12) | N |  |
| anomaly_count | bigint(64) | Y |  |
| avg_score | numeric(10) | Y |  |

### ps4_weekly_alerts (table, rows=1108)

| column | type | nullable | default |
|---|---|---|---|
| city_id | text | N |  |
| device_id | text | N |  |
| device_type | text | N |  |
| week_start | date | N |  |
| pipeline_version | text | N |  |
| week_end | date | Y |  |
| facility_id | text | Y |  |
| observed_days | integer(32) | Y |  |
| observed_hours | integer(32) | Y |  |
| candidate_days | integer(32) | Y |  |
| actionable_days | integer(32) | Y |  |
| is_actionable_week | smallint(16) | Y |  |
| anomaly_score_max | double precision(53) | Y |  |
| anomaly_score_mean | double precision(53) | Y |  |
| anomaly_score_p95 | double precision(53) | Y |  |
| max_abs_z | double precision(53) | Y |  |
| cluster_distance_ratio_max | double precision(53) | Y |  |
| has_low_coverage_day | smallint(16) | Y |  |
| dominant_cluster_id | integer(32) | Y |  |
| anomaly_types | text | Y |  |
| severity | text | Y |  |
| asof_date | date | Y |  |
| run_id | text | Y |  |
| loaded_at | timestamp with time zone | Y | now() |

### ps4_cluster_summary (table, rows=25)

| column | type | nullable | default |
|---|---|---|---|
| city_id | USER-DEFINED | N |  |
| asof_date | date | N |  |
| device_type | character varying(12) | N |  |
| cluster_id | integer(32) | N |  |
| device_count | bigint(64) | Y |  |
| mean_target | numeric(14) | Y |  |
| mean_confidence | numeric(10) | Y |  |
| champion_pipeline | character varying(80) | Y |  |
| champion_silhouette | numeric(10) | Y |  |

### ps4_cluster_profile (table, rows=9)

| column | type | nullable | default |
|---|---|---|---|
| city_id | text | N |  |
| device_type | text | N |  |
| cluster_id | integer(32) | N |  |
| pipeline_version | text | N |  |
| scored_device_days | bigint(64) | Y |  |
| candidate_rate | double precision(53) | Y |  |
| actionable_rate | double precision(53) | Y |  |
| mean_cluster_distance | double precision(53) | Y |  |
| train_cluster_distance_p99 | double precision(53) | Y |  |
| train_cluster_share | double precision(53) | Y |  |
| asof_date | date | Y |  |
| run_id | text | Y |  |
| loaded_at | timestamp with time zone | Y | now() |

### ps4_weekly_timeline (table, rows=6)

| column | type | nullable | default |
|---|---|---|---|
| city_id | text | N |  |
| device_type | text | N |  |
| week_start | date | N |  |
| pipeline_version | text | N |  |
| week_end | date | Y |  |
| devices_observed | integer(32) | Y |  |
| actionable_devices | integer(32) | Y |  |
| candidate_device_days | integer(32) | Y |  |
| mean_anomaly_score | double precision(53) | Y |  |
| max_anomaly_score | double precision(53) | Y |  |
| asof_date | date | Y |  |
| run_id | text | Y |  |
| loaded_at | timestamp with time zone | Y | now() |

### ps4_anomaly_signal_detail (table, rows=3)

| column | type | nullable | default |
|---|---|---|---|
| detail_id | bigint(64) | N |  |
| prediction_id | bigint(64) | N |  |
| signal_name | character varying(30) | N |  |
| signal_fired | boolean | N |  |
| signal_value | numeric(12) | Y |  |
| signal_description | character varying(120) | Y |  |

### ps4_cluster_quality (table, rows=3)

| column | type | nullable | default |
|---|---|---|---|
| city_id | text | N |  |
| device_type | text | N |  |
| pipeline_version | text | N |  |
| k_selected | integer(32) | Y |  |
| silhouette | double precision(53) | Y |  |
| train_rows | bigint(64) | Y |  |
| quality_source | text | Y |  |
| asof_date | date | Y |  |
| run_id | text | Y |  |
| loaded_at | timestamp with time zone | Y | now() |

### ps4_anomaly_predictions (table, rows=2)

| column | type | nullable | default |
|---|---|---|---|
| prediction_id | bigint(64) | N |  |
| device_id | character varying(20) | N |  |
| device_category | character varying(20) | N |  |
| facility_id | character varying(20) | Y |  |
| observation_hour | timestamp without time zone | N |  |
| model_registry_id | bigint(64) | N |  |
| ensemble_anomaly_flag | boolean | N |  |
| active_signal_count | smallint(16) | N |  |
| isolation_forest_score | numeric(9) | Y |  |
| inference_ts | timestamp without time zone | N |  |

### ps4_anomaly_rate_forecast (table, rows=2)

| column | type | nullable | default |
|---|---|---|---|
| forecast_id | bigint(64) | N |  |
| device_category | character varying(20) | N |  |
| forecast_date | date | N |  |
| predicted_anomaly_rate | numeric(6) | N |  |
| ci_lower | numeric(6) | Y |  |
| ci_upper | numeric(6) | Y |  |
| model_registry_id | bigint(64) | Y |  |
| generated_at | timestamp without time zone | N |  |

### ps4_v3_runs (table, rows=1)

| column | type | nullable | default |
|---|---|---|---|
| city_id | text | N |  |
| pipeline_version | text | N |  |
| run_id | text | N |  |
| asof_date | date | Y |  |
| manifest_key | text | Y |  |
| ready_key | text | Y |  |
| source_rows | bigint(64) | Y |  |
| rows_summary | integer(32) | Y |  |
| rows_alerts | integer(32) | Y |  |
| rows_timeline | integer(32) | Y |  |
| rows_cluster | integer(32) | Y |  |
| loaded_at | timestamp with time zone | Y | now() |

### ps4_anomalies (table, rows=0)

| column | type | nullable | default |
|---|---|---|---|
| city_id | USER-DEFINED | N |  |
| asof_date | date | N |  |
| device_id | character varying(40) | N |  |
| device_type | character varying(12) | Y |  |
| detected_at | timestamp with time zone | N |  |
| anomaly_type | character varying(120) | Y |  |
| severity | character varying(12) | Y |  |
| anomaly_score | numeric(10) | Y |  |
| signal_active_count | smallint(16) | Y |  |
| status | character varying(16) | N | 'active'::character varying |
| transit_day | date | Y |  |
| facility_id | character varying(24) | Y |  |
| facility_name | character varying(120) | Y |  |
| if_score | numeric(10) | Y |  |

### ps4_anomaly_alerts (table, rows=0)

| column | type | nullable | default |
|---|---|---|---|
| id | uuid | N | uuid_generate_v4() |
| city_id | USER-DEFINED | N |  |
| device_id | character varying(30) | N |  |
| device_type | USER-DEFINED | N |  |
| detected_at | timestamp with time zone | N |  |
| triggering_signal | character varying(30) | N |  |
| anomaly_score | numeric(6) | N |  |
| severity | USER-DEFINED | N |  |
| description | text | Y |  |
| status | USER-DEFINED | N | 'active'::ps4_alert_status |
| acknowledged_by | uuid | Y |  |
| acknowledged_at | timestamp with time zone | Y |  |
| resolved_at | timestamp with time zone | Y |  |
| calibration_version | character varying(20) | N |  |
| created_at | timestamp with time zone | Y | now() |

### ps4_device_daily (table, rows=0)

| column | type | nullable | default |
|---|---|---|---|
| city_id | USER-DEFINED | N |  |
| run_id | character varying(48) | N |  |
| device_id | character varying(40) | N |  |
| transit_day | date | N |  |
| device_category | character varying(12) | Y |  |
| facility_id | character varying(20) | Y |  |
| total_hours | integer(32) | Y |  |
| anomaly_hours | integer(32) | Y |  |
| anomaly_rate | numeric(7) | Y |  |
| spc_violation_hours | integer(32) | Y |  |
| if_anomaly_hours | integer(32) | Y |  |
| max_anomaly_score | numeric(9) | Y |  |
| avg_anomaly_score | numeric(9) | Y |  |
| computed_date | date | N |  |

### ps4_device_day (table, rows=0)

| column | type | nullable | default |
|---|---|---|---|
| city_id | USER-DEFINED | N |  |
| device_type | character varying(12) | N |  |
| device_id | character varying(40) | N |  |
| transit_day | date | N |  |
| total_hours | integer(32) | Y |  |
| anomaly_hours | integer(32) | Y |  |
| is_anomaly_day | boolean | Y |  |
| signal_active_count_max | integer(32) | Y |  |
| severity_rank | integer(32) | Y |  |
| anomaly_score_mean | numeric(12) | Y |  |
| anomaly_score_max | numeric(12) | Y |  |
| if_score_mean | numeric(12) | Y |  |
| if_score_max | numeric(12) | Y |  |
| event_count_mean | numeric(14) | Y |  |
| first_detected_at | timestamp without time zone | Y |  |
| last_detected_at | timestamp without time zone | Y |  |
| anomaly_types | text | Y |  |
| asof_date | date | N |  |

### ps4_device_hourly (table, rows=0)

| column | type | nullable | default |
|---|---|---|---|
| city_id | USER-DEFINED | N |  |
| run_id | character varying(48) | N |  |
| device_id | character varying(40) | N |  |
| hour_bucket | timestamp with time zone | N |  |
| device_category | character varying(12) | Y |  |
| facility_id | character varying(20) | Y |  |
| event_count | integer(32) | Y |  |
| anomaly_score | numeric(9) | Y |  |
| is_anomaly | boolean | Y |  |
| spc_violation | boolean | Y |  |
| if_anomaly | boolean | Y |  |
| computed_date | date | N |  |

### ps4_device_lifetime (table, rows=0)

| column | type | nullable | default |
|---|---|---|---|
| city_id | USER-DEFINED | N |  |
| device_type | character varying(12) | N |  |
| device_id | character varying(40) | N |  |
| n_days | integer(32) | Y |  |
| total_hours | bigint(64) | Y |  |
| anomaly_hours | bigint(64) | Y |  |
| n_anomaly_days | integer(32) | Y |  |
| flagged_rate | numeric(7) | Y |  |
| anomaly_score_max | numeric(12) | Y |  |
| if_score_max | numeric(12) | Y |  |
| first_day | date | Y |  |
| last_day | date | Y |  |
| window_days | integer(32) | Y |  |
| asof_date | date | N |  |

### ps4_leakage_scan (table, rows=0)

| column | type | nullable | default |
|---|---|---|---|
| city_id | USER-DEFINED | N |  |
| run_id | character varying(48) | N |  |
| feature_name | character varying(80) | N |  |
| solo_auc | numeric(7) | Y |  |
| flagged | boolean | Y |  |
| as_of_date | date | N |  |

### ps4_model_leaderboard (table, rows=0)

| column | type | nullable | default |
|---|---|---|---|
| city_id | USER-DEFINED | N |  |
| run_id | character varying(48) | N |  |
| model | character varying(48) | N |  |
| val_ap | numeric(7) | Y |  |
| test_ap | numeric(7) | Y |  |
| val_auc | numeric(7) | Y |  |
| test_auc | numeric(7) | Y |  |
| test_f1 | numeric(7) | Y |  |
| test_prec | numeric(7) | Y |  |
| test_rec | numeric(7) | Y |  |
| test_accuracy | numeric(7) | Y |  |
| base_rate | numeric(7) | Y |  |
| ap_lift_over_base | numeric(8) | Y |  |
| fit_s | numeric(10) | Y |  |
| lb_rank | smallint(16) | Y |  |
| is_champion | boolean | Y | false |
| note | character varying(200) | Y |  |
| as_of_date | date | N |  |

### ps4_outlier_scores (table, rows=0)

| column | type | nullable | default |
|---|---|---|---|
| city_id | USER-DEFINED | N |  |
| asof_date | date | N |  |
| device_id | character varying(40) | N |  |
| device_type | character varying(12) | Y |  |
| recorded_at | timestamp with time zone | N |  |
| x_metric | numeric(14) | Y |  |
| y_metric | numeric(10) | Y |  |
| z_score | numeric(10) | Y |  |
| is_outlier | boolean | Y |  |

### ps4_runs (table, rows=0)

| column | type | nullable | default |
|---|---|---|---|
| city_id | USER-DEFINED | N |  |
| run_id | character varying(48) | N |  |
| run_ts | timestamp with time zone | N |  |
| run_kind | character varying(16) | N | 'train'::character varying |
| source_notebook | character varying(160) | Y |  |
| gold_snapshot_s3 | character varying(300) | Y |  |
| s3_run_prefix | character varying(300) | Y |  |
| champion | character varying(48) | Y |  |
| target_col | character varying(48) | Y |  |
| n_rows_total | bigint(64) | Y |  |
| n_rows_train | bigint(64) | Y |  |
| n_rows_val | bigint(64) | Y |  |
| n_rows_test | bigint(64) | Y |  |
| n_features | integer(32) | Y |  |
| target_rate | numeric(7) | Y |  |
| data_start | timestamp with time zone | Y |  |
| data_end | timestamp with time zone | Y |  |
| hourly_window_days | integer(32) | Y |  |
| leakage_checked | boolean | Y | false |
| notes | text | Y |  |
| computed_date | date | N |  |

### ps4_signal_summary (table, rows=0)

| column | type | nullable | default |
|---|---|---|---|
| city_id | USER-DEFINED | N |  |
| run_id | character varying(48) | N |  |
| signal_name | character varying(40) | N |  |
| device_category | character varying(12) | N |  |
| split_name | character varying(12) | N |  |
| activation_rate | numeric(7) | Y |  |
| n_hours | bigint(64) | Y |  |
| n_active | bigint(64) | Y |  |
| threshold_used | numeric(12) | Y |  |
| as_of_date | date | N |  |

### ps4_spc_thresholds (table, rows=0)

| column | type | nullable | default |
|---|---|---|---|
| city_id | USER-DEFINED | N |  |
| run_id | character varying(48) | N |  |
| device_category | character varying(12) | N |  |
| mean_events | numeric(12) | Y |  |
| ucl | numeric(12) | Y |  |
| lcl | numeric(12) | Y |  |
| violation_rate | numeric(7) | Y |  |
| as_of_date | date | N |  |

### ps4_station_anomaly (table, rows=0)

| column | type | nullable | default |
|---|---|---|---|
| city_id | USER-DEFINED | N |  |
| run_id | character varying(48) | N |  |
| facility_id | character varying(20) | N |  |
| device_category | character varying(12) | N | 'ALL'::character varying |
| n_devices | integer(32) | Y |  |
| total_hours | bigint(64) | Y |  |
| anomaly_hours | bigint(64) | Y |  |
| anomaly_rate | numeric(7) | Y |  |
| as_of_date | date | N |  |

### v_ps4_anomalies (view, rows=None)

| column | type | nullable | default |
|---|---|---|---|
| city_id | USER-DEFINED | Y |  |
| asof_date | date | Y |  |
| device_id | character varying(40) | Y |  |
| device_type | character varying(12) | Y |  |
| detected_at | timestamp with time zone | Y |  |
| anomaly_type | character varying(120) | Y |  |
| severity | character varying(12) | Y |  |
| anomaly_score | numeric(10) | Y |  |
| signal_active_count | smallint(16) | Y |  |
| status | character varying(16) | Y |  |
| transit_day | date | Y |  |
| facility_id | character varying(24) | Y |  |
| facility_name | character varying(120) | Y |  |
| if_score | numeric(10) | Y |  |

### v_ps4_cluster_profile (view, rows=None)

| column | type | nullable | default |
|---|---|---|---|
| city_id | USER-DEFINED | Y |  |
| asof_date | date | Y |  |
| device_type | character varying(12) | Y |  |
| cluster_id | integer(32) | Y |  |
| n_devices | bigint(64) | Y |  |
| n_devices_with_anomalies | bigint(64) | Y |  |
| avg_anomaly_hours | numeric | Y |  |
| avg_anomaly_score | numeric | Y |  |
| champion_silhouette | numeric | Y |  |
| champion_pipeline | text | Y |  |

### v_ps4_cluster_quality (view, rows=None)

| column | type | nullable | default |
|---|---|---|---|
| city_id | text | Y |  |
| device_type | text | Y |  |
| pipeline_version | text | Y |  |
| k_selected | integer(32) | Y |  |
| silhouette | double precision(53) | Y |  |
| train_rows | bigint(64) | Y |  |
| quality_source | text | Y |  |
| asof_date | date | Y |  |
| run_id | text | Y |  |
| separation_verdict | text | Y |  |
| separation_note | text | Y |  |

### v_ps4_day_anomalies (view, rows=None)

| column | type | nullable | default |
|---|---|---|---|
| city_id | USER-DEFINED | Y |  |
| device_type | character varying(12) | Y |  |
| device_id | character varying(40) | Y |  |
| transit_day | date | Y |  |
| anomaly_hours | integer(32) | Y |  |
| total_hours | integer(32) | Y |  |
| signal_active_count_max | integer(32) | Y |  |
| severity_rank | integer(32) | Y |  |
| anomaly_score | numeric(12) | Y |  |
| anomaly_types | text | Y |  |
| detected_at | timestamp without time zone | Y |  |
| is_outlier | boolean | Y |  |
| status | character varying(12) | Y |  |
| asof_date | date | Y |  |

### v_ps4_device_anomaly (view, rows=None)

| column | type | nullable | default |
|---|---|---|---|
| city_id | USER-DEFINED | Y |  |
| asof_date | date | Y |  |
| device_id | character varying(40) | Y |  |
| device_type | character varying(12) | Y |  |
| facility_id | character varying(24) | Y |  |
| facility_name | character varying(120) | Y |  |
| anomaly_hours | bigint(64) | Y |  |
| max_anomaly_score | numeric | Y |  |
| avg_anomaly_score | numeric | Y |  |
| last_detected_at | timestamp with time zone | Y |  |
| n_critical | bigint(64) | Y |  |
| n_high | bigint(64) | Y |  |
| dominant_anomaly_type | character varying | Y |  |

### v_ps4_device_status (view, rows=None)

| column | type | nullable | default |
|---|---|---|---|
| city_id | USER-DEFINED | Y |  |
| device_type | character varying(12) | Y |  |
| device_id | character varying(40) | Y |  |
| n_days | integer(32) | Y |  |
| anomaly_hours | bigint(64) | Y |  |
| total_hours | bigint(64) | Y |  |
| flagged_rate | numeric(7) | Y |  |
| n_anomaly_days | integer(32) | Y |  |
| first_day | date | Y |  |
| last_day | date | Y |  |
| window_days | integer(32) | Y |  |
| asof_date | date | Y |  |
| window_days_present | bigint(64) | Y |  |
| window_anomaly_days | bigint(64) | Y |  |
| window_flagged_rate | numeric | Y |  |
| last_flagged_day | date | Y |  |
| rank_in_type | bigint(64) | Y |  |

### v_ps4_latest_asof (view, rows=None)

| column | type | nullable | default |
|---|---|---|---|
| city_id | USER-DEFINED | Y |  |
| asof_date | date | Y |  |

### v_ps4_latest_run (view, rows=None)

| column | type | nullable | default |
|---|---|---|---|
| city_id | USER-DEFINED | Y |  |
| run_id | character varying(48) | Y |  |
| run_ts | timestamp with time zone | Y |  |
| target_rate | numeric(7) | Y |  |
| leakage_checked | boolean | Y |  |

### v_ps4_leaderboard (view, rows=None)

| column | type | nullable | default |
|---|---|---|---|
| city_id | USER-DEFINED | Y |  |
| run_id | character varying(48) | Y |  |
| model | character varying(48) | Y |  |
| val_ap | numeric(7) | Y |  |
| test_ap | numeric(7) | Y |  |
| test_auc | numeric(7) | Y |  |
| test_accuracy | numeric(7) | Y |  |
| base_rate | numeric(7) | Y |  |
| ap_lift_over_base | numeric(8) | Y |  |
| lb_rank | smallint(16) | Y |  |
| is_champion | boolean | Y |  |
| verdict | text | Y |  |

### v_ps4_outlier_scores (view, rows=None)

| column | type | nullable | default |
|---|---|---|---|
| city_id | USER-DEFINED | Y |  |
| device_type | character varying(12) | Y |  |
| device_id | character varying(40) | Y |  |
| transit_day | date | Y |  |
| bucket_time | timestamp without time zone | Y |  |
| recorded_at | timestamp without time zone | Y |  |
| x_metric | numeric | Y |  |
| y_metric | numeric(12) | Y |  |
| z_score | numeric(12) | Y |  |
| is_anomaly_day | boolean | Y |  |
| asof_date | date | Y |  |

### v_ps4_timeline (view, rows=None)

| column | type | nullable | default |
|---|---|---|---|
| city_id | USER-DEFINED | Y |  |
| asof_date | date | Y |  |
| bucket_time | timestamp with time zone | Y |  |
| device_type | character varying(12) | Y |  |
| anomaly_count | bigint(64) | Y |  |
| avg_score | numeric(10) | Y |  |

### v_ps4_v3_cluster_profile (view, rows=None)

| column | type | nullable | default |
|---|---|---|---|
| city_id | text | Y |  |
| device_type | text | Y |  |
| cluster_id | integer(32) | Y |  |
| scored_device_days | bigint(64) | Y |  |
| candidate_rate | double precision(53) | Y |  |
| actionable_rate | double precision(53) | Y |  |
| mean_cluster_distance | double precision(53) | Y |  |
| train_cluster_distance_p99 | double precision(53) | Y |  |
| train_cluster_share | double precision(53) | Y |  |
| mean_distance_ratio | numeric | Y |  |
| silhouette | double precision(53) | Y |  |
| k_selected | integer(32) | Y |  |
| quality_source | text | Y |  |
| pipeline_version | text | Y |  |
| asof_date | date | Y |  |
| run_id | text | Y |  |

### v_ps4_v3_current (view, rows=None)

| column | type | nullable | default |
|---|---|---|---|
| city_id | text | Y |  |
| pipeline_version | text | Y |  |
| run_id | text | Y |  |
| asof_date | date | Y |  |
| manifest_key | text | Y |  |
| source_rows | bigint(64) | Y |  |
| rows_summary | integer(32) | Y |  |
| rows_alerts | integer(32) | Y |  |
| rows_timeline | integer(32) | Y |  |
| rows_cluster | integer(32) | Y |  |

### v_ps4_v3_table_status (view, rows=None)

| column | type | nullable | default |
|---|---|---|---|
| table_name | text | Y |  |
| n_rows | bigint(64) | Y |  |

### v_ps4_weekly_alert_reconcile (view, rows=None)

| column | type | nullable | default |
|---|---|---|---|
| city_id | text | Y |  |
| pipeline_version | text | Y |  |
| device_type | text | Y |  |
| summary_actionable | bigint(64) | Y |  |
| alerts_rows | bigint(64) | Y |  |
| summary_rows | bigint(64) | Y |  |

### v_ps4_weekly_alerts (view, rows=None)

| column | type | nullable | default |
|---|---|---|---|
| city_id | text | Y |  |
| device_id | text | Y |  |
| device_type | text | Y |  |
| facility_id | text | Y |  |
| week_start | date | Y |  |
| week_end | date | Y |  |
| severity | text | Y |  |
| anomaly_types | text | Y |  |
| actionable_days | integer(32) | Y |  |
| candidate_days | integer(32) | Y |  |
| observed_days | integer(32) | Y |  |
| observed_hours | integer(32) | Y |  |
| anomaly_score_max | double precision(53) | Y |  |
| anomaly_score_mean | double precision(53) | Y |  |
| anomaly_score_p95 | double precision(53) | Y |  |
| max_abs_z | double precision(53) | Y |  |
| cluster_distance_ratio_max | double precision(53) | Y |  |
| dominant_cluster_id | integer(32) | Y |  |
| has_low_coverage_day | smallint(16) | Y |  |
| coverage_note | text | Y |  |
| pipeline_version | text | Y |  |
| asof_date | date | Y |  |
| run_id | text | Y |  |

### v_ps4_weekly_device (view, rows=None)

| column | type | nullable | default |
|---|---|---|---|
| city_id | text | Y |  |
| device_id | text | Y |  |
| device_type | text | Y |  |
| facility_id | text | Y |  |
| week_start | date | Y |  |
| week_end | date | Y |  |
| severity | text | Y |  |
| anomaly_types | text | Y |  |
| observed_days | integer(32) | Y |  |
| observed_hours | integer(32) | Y |  |
| candidate_days | integer(32) | Y |  |
| actionable_days | integer(32) | Y |  |
| is_actionable_week | smallint(16) | Y |  |
| anomaly_score_max | double precision(53) | Y |  |
| anomaly_score_mean | double precision(53) | Y |  |
| anomaly_score_p95 | double precision(53) | Y |  |
| max_abs_z | double precision(53) | Y |  |
| cluster_distance_ratio_max | double precision(53) | Y |  |
| dominant_cluster_id | integer(32) | Y |  |
| has_low_coverage_day | smallint(16) | Y |  |
| partial_week | boolean | Y |  |
| pipeline_version | text | Y |  |
| asof_date | date | Y |  |
| run_id | text | Y |  |

### v_ps4_weekly_facility (view, rows=None)

| column | type | nullable | default |
|---|---|---|---|
| city_id | text | Y |  |
| pipeline_version | text | Y |  |
| facility_id | text | Y |  |
| device_type | text | Y |  |
| devices | bigint(64) | Y |  |
| actionable_devices | bigint(64) | Y |  |
| mean_distance_ratio | numeric | Y |  |
| last_week | date | Y |  |

### v_ps4_weekly_persistent (view, rows=None)

| column | type | nullable | default |
|---|---|---|---|
| city_id | text | Y |  |
| device_id | text | Y |  |
| device_type | text | Y |  |
| facility_id | text | Y |  |
| actionable_weeks | bigint(64) | Y |  |
| first_week | date | Y |  |
| last_week | date | Y |  |
| worst_distance_ratio | double precision(53) | Y |  |
| worst_score | double precision(53) | Y |  |
| severities | text | Y |  |
| pipeline_version | text | Y |  |

### v_ps4_weekly_timeline (view, rows=None)

| column | type | nullable | default |
|---|---|---|---|
| city_id | text | Y |  |
| device_type | text | Y |  |
| week_start | date | Y |  |
| week_end | date | Y |  |
| devices_observed | integer(32) | Y |  |
| actionable_devices | integer(32) | Y |  |
| candidate_device_days | integer(32) | Y |  |
| mean_anomaly_score | double precision(53) | Y |  |
| max_anomaly_score | double precision(53) | Y |  |
| actionable_share | numeric | Y |  |
| pipeline_version | text | Y |  |
| asof_date | date | Y |  |
| run_id | text | Y |  |

## PS5 — 13 tables (30,476 rows), 8 views

| object | kind | rows | cols | primary key |
|---|---|---:|---:|---|
| ps5_serial_reliability | table | 12,904 | 22 | id |
| ps5_serial_rul | table | 11,718 | 17 | - |
| ps5_reliability_estimates | table | 4,103 | 24 | id |
| ps5_device_rul | table | 1,536 | 17 | city_id, device_id |
| ps5_permutation_importance | table | 164 | 5 | city_id, device_type, feature_name |
| ps5_enrich_coverage | table | 24 | 9 | city_id, device_type, source_name, table_name |
| ps5_cindex_leaderboard | table | 18 | 7 | city_id, device_type, model, feats |
| ps5_scoring_runs | table | 4 | 15 | run_id |
| ps5_reliability_status | table | 3 | 7 | city_id, device_type, as_of_date |
| ps5_event_definition | table | 2 | 9 | event_def_version |
| ps5_cox_hazard_ratios | table | 0 | 5 | city_id, device_type, fault_code, computed_date |
| ps5_feature_alignment_audit | table | 0 | 10 | id |
| ps5_weibull_params | table | 0 | 7 | city_id, device_type, fault_code, computed_date |
| v_ps5_dashboard_ready | view | - | 7 | - |
| v_ps5_device_360 | view | - | 13 | - |
| v_ps5_device_rul | view | - | 20 | - |
| v_ps5_reliability_oos_latest | view | - | 24 | - |
| v_ps5_serial_dupes | view | - | 10 | - |
| v_ps5_serial_fanout | view | - | 6 | - |
| v_ps5_serial_oos_latest | view | - | 16 | - |
| v_ps5_serial_rul | view | - | 21 | - |

### ps5_serial_reliability (table, rows=12904)

| column | type | nullable | default |
|---|---|---|---|
| id | bigint(64) | N | nextval('ps5_serial_reliability_id_seq'::regclass) |
| city_id | USER-DEFINED | N |  |
| run_id | uuid | Y |  |
| device_id | character varying(40) | N |  |
| component_serial_nbr | character varying(64) | N |  |
| component_type | character varying(40) | Y |  |
| mars_device_category | character varying(12) | N |  |
| component_age_days | numeric(10) | Y |  |
| failures_total | integer(32) | Y |  |
| chargeable_failure_count | integer(32) | Y |  |
| risk_score | numeric(12) | Y |  |
| risk_tier | character varying(10) | Y |  |
| expected_component_rul_days | numeric(10) | Y |  |
| predicted_median_survival_days | numeric(10) | Y |  |
| is_overdue | boolean | Y |  |
| failure_observed | boolean | Y |  |
| as_of_date | date | N |  |
| scored_at | timestamp with time zone | N | now() |
| device_oos_failures_total | integer(32) | Y |  |
| event_definition | character varying(24) | Y |  |
| event_def_version | character varying(64) | Y |  |
| feature_asof_date | date | Y |  |

### ps5_serial_rul (table, rows=11718)

| column | type | nullable | default |
|---|---|---|---|
| city_id | USER-DEFINED | N |  |
| device_type | character varying(12) | N |  |
| device_id | character varying(40) | N |  |
| component_serial_nbr | character varying(64) | Y |  |
| component_type_name | character varying(120) | Y |  |
| mars_device_category | character varying(12) | Y |  |
| component_age_days | numeric(10) | Y |  |
| device_oos_failures_total | integer(32) | Y |  |
| risk_score | numeric(12) | Y |  |
| risk_tier | character varying(16) | Y |  |
| expected_component_rul_days | numeric(10) | Y |  |
| predicted_median_survival_days | numeric(10) | Y |  |
| is_overdue | boolean | Y |  |
| event_definition | character varying(40) | Y |  |
| event_def_version | character varying(60) | Y |  |
| feature_asof_date | date | Y |  |
| serial_source | character varying(60) | Y |  |

### ps5_reliability_estimates (table, rows=4103)

| column | type | nullable | default |
|---|---|---|---|
| id | bigint(64) | N | nextval('ps5_reliability_estimates_id_seq'::regclass) |
| city_id | USER-DEFINED | N |  |
| run_id | uuid | Y |  |
| device_id | character varying(40) | N |  |
| mars_device_category | character varying(12) | N |  |
| current_healthy_age_days | numeric(10) | Y |  |
| rul_standard_days | numeric(10) | Y |  |
| predicted_median_survival_days | numeric(10) | Y |  |
| hazard_score | numeric(6) | Y |  |
| risk_band | character varying(10) | Y |  |
| is_overdue | boolean | Y |  |
| n_prior_failures | integer(32) | Y |  |
| concordance_index | numeric(6) | Y |  |
| champion_model | character varying(48) | Y |  |
| weibull_shape | numeric(8) | Y |  |
| data_quality_gate_passed | boolean | N | false |
| facility_id | character varying(20) | Y |  |
| as_of_date | date | N |  |
| scored_at | timestamp with time zone | N | now() |
| days_since_hw_oos | numeric(10) | Y |  |
| roll_fail_30d | integer(32) | Y |  |
| event_definition | character varying(24) | Y |  |
| event_def_version | character varying(64) | Y |  |
| feature_asof_date | date | Y |  |

### ps5_device_rul (table, rows=1536)

| column | type | nullable | default |
|---|---|---|---|
| city_id | USER-DEFINED | N |  |
| device_type | character varying(12) | N |  |
| device_id | character varying(40) | N |  |
| mars_device_category | character varying(12) | Y |  |
| current_healthy_age_days | numeric(10) | Y |  |
| rul_standard_days | numeric(10) | Y |  |
| predicted_median_survival_days | numeric(10) | Y |  |
| hazard_score | numeric(12) | Y |  |
| risk_band | character varying(16) | Y |  |
| is_overdue | boolean | Y |  |
| days_since_hw_oos | numeric(10) | Y |  |
| roll_fail_30d | numeric(10) | Y |  |
| n_prior_oos | integer(32) | Y |  |
| facility_id | character varying(20) | Y |  |
| feature_asof_date | date | Y |  |
| event_definition | character varying(40) | Y |  |
| event_def_version | character varying(60) | Y |  |

### ps5_permutation_importance (table, rows=164)

| column | type | nullable | default |
|---|---|---|---|
| city_id | USER-DEFINED | N |  |
| device_type | character varying(12) | N |  |
| feature_name | character varying(120) | N |  |
| cindex_drop | numeric(12) | Y |  |
| is_enriched | boolean | Y |  |

### ps5_enrich_coverage (table, rows=24)

| column | type | nullable | default |
|---|---|---|---|
| city_id | USER-DEFINED | N |  |
| device_type | character varying(12) | N |  |
| source_name | character varying(80) | N |  |
| table_name | character varying(120) | N |  |
| status | character varying(30) | Y |  |
| pct_matched | numeric(7) | Y |  |
| tel_min | date | Y |  |
| tel_max | date | Y |  |
| n_feats | integer(32) | Y |  |

### ps5_cindex_leaderboard (table, rows=18)

| column | type | nullable | default |
|---|---|---|---|
| city_id | USER-DEFINED | N |  |
| device_type | character varying(12) | N |  |
| model | character varying(80) | N |  |
| feats | character varying(200) | N |  |
| oot_cindex | numeric(8) | Y |  |
| sd | numeric(8) | Y |  |
| window_label | character varying(40) | Y |  |

### ps5_scoring_runs (table, rows=4)

| column | type | nullable | default |
|---|---|---|---|
| run_id | uuid | N | uuid_generate_v4() |
| city_id | USER-DEFINED | N |  |
| run_ts | timestamp with time zone | N | now() |
| gold_source_partition | character varying(20) | Y |  |
| n_devices_scored | integer(32) | Y |  |
| n_serials_scored | integer(32) | Y |  |
| model_version | character varying(16) | Y |  |
| scoring_mode | character varying(12) | N | 'BATCH'::character varying |
| status | character varying(16) | N | 'SUCCESS'::character varying |
| note | character varying(240) | Y |  |
| event_definition | character varying(24) | Y |  |
| event_def_version | character varying(64) | Y |  |
| event_filter | jsonb | Y |  |
| run_date | date | Y |  |
| scorer | character varying(32) | Y |  |

### ps5_reliability_status (table, rows=3)

| column | type | nullable | default |
|---|---|---|---|
| city_id | USER-DEFINED | N |  |
| device_type | USER-DEFINED | N |  |
| concordance_index | numeric(6) | Y |  |
| registry_status | character varying(40) | Y |  |
| dashboard_ready | boolean | N | false |
| blockers | text | Y |  |
| as_of_date | date | N |  |

### ps5_event_definition (table, rows=2)

| column | type | nullable | default |
|---|---|---|---|
| event_def_version | character varying(64) | N |  |
| event_definition | character varying(24) | N |  |
| label | character varying(80) | N |  |
| criteria | jsonb | N |  |
| window_mode | character varying(24) | Y |  |
| telemetry_start | date | Y |  |
| cindex_floor | numeric(4) | Y |  |
| effective_date | date | N | CURRENT_DATE |
| created_at | timestamp with time zone | N | now() |

### ps5_cox_hazard_ratios (table, rows=0)

| column | type | nullable | default |
|---|---|---|---|
| city_id | USER-DEFINED | N |  |
| device_type | USER-DEFINED | N |  |
| fault_code | character varying(50) | N |  |
| hazard_ratio | numeric(10) | Y |  |
| computed_date | date | N |  |

### ps5_feature_alignment_audit (table, rows=0)

| column | type | nullable | default |
|---|---|---|---|
| id | bigint(64) | N | nextval('ps5_feature_alignment_audit_id_seq'::regclass) |
| city_id | USER-DEFINED | N |  |
| run_date | date | N |  |
| mars_device_category | character varying(12) | Y |  |
| event_def_version | character varying(24) | Y |  |
| leak_check_passed | boolean | Y |  |
| n_intervals_checked | integer(32) | Y |  |
| n_mismatches | integer(32) | Y |  |
| asof_gap_days_median | numeric(10) | Y |  |
| created_at | timestamp with time zone | N | now() |

### ps5_weibull_params (table, rows=0)

| column | type | nullable | default |
|---|---|---|---|
| city_id | USER-DEFINED | N |  |
| device_type | USER-DEFINED | N |  |
| fault_code | character varying(50) | N |  |
| beta_shape | numeric(6) | Y |  |
| eta_scale | numeric(14) | Y |  |
| fault_count | bigint(64) | Y |  |
| computed_date | date | N |  |

### v_ps5_dashboard_ready (view, rows=None)

| column | type | nullable | default |
|---|---|---|---|
| city_id | USER-DEFINED | Y |  |
| device_type | USER-DEFINED | Y |  |
| concordance_index | numeric(6) | Y |  |
| dashboard_ready | boolean | Y |  |
| registry_status | character varying(40) | Y |  |
| blockers | text | Y |  |
| as_of_date | date | Y |  |

### v_ps5_device_360 (view, rows=None)

| column | type | nullable | default |
|---|---|---|---|
| city_id | USER-DEFINED | Y |  |
| device_id | character varying(40) | Y |  |
| mars_device_category | character varying(12) | Y |  |
| current_healthy_age_days | numeric(10) | Y |  |
| rul_standard_days | numeric(10) | Y |  |
| predicted_median_survival_days | numeric(10) | Y |  |
| hazard_score | numeric(6) | Y |  |
| risk_band | character varying(10) | Y |  |
| is_overdue | boolean | Y |  |
| n_prior_failures | integer(32) | Y |  |
| concordance_index | numeric(6) | Y |  |
| data_quality_gate_passed | boolean | Y |  |
| as_of_date | date | Y |  |

### v_ps5_device_rul (view, rows=None)

| column | type | nullable | default |
|---|---|---|---|
| city_id | USER-DEFINED | Y |  |
| device_type | character varying(12) | Y |  |
| device_id | character varying(40) | Y |  |
| mars_device_category | character varying(12) | Y |  |
| current_healthy_age_days | numeric(10) | Y |  |
| rul_standard_days | numeric(10) | Y |  |
| predicted_median_survival_days | numeric(10) | Y |  |
| hazard_score | numeric(12) | Y |  |
| risk_band | character varying(16) | Y |  |
| is_overdue | boolean | Y |  |
| days_since_hw_oos | numeric(10) | Y |  |
| roll_fail_30d | numeric(10) | Y |  |
| n_prior_oos | integer(32) | Y |  |
| facility_id | character varying(20) | Y |  |
| feature_asof_date | date | Y |  |
| event_definition | character varying(40) | Y |  |
| event_def_version | character varying(60) | Y |  |
| rul_rank_in_type | bigint(64) | Y |  |
| n_devices_in_type | bigint(64) | Y |  |
| act_now | boolean | Y |  |

### v_ps5_reliability_oos_latest (view, rows=None)

| column | type | nullable | default |
|---|---|---|---|
| city_id | USER-DEFINED | Y |  |
| device_id | character varying(40) | Y |  |
| mars_device_category | character varying(12) | Y |  |
| current_healthy_age_days | numeric(10) | Y |  |
| rul_standard_days | numeric(10) | Y |  |
| predicted_median_survival_days | numeric(10) | Y |  |
| hazard_score | numeric(6) | Y |  |
| risk_band | character varying(10) | Y |  |
| is_overdue | boolean | Y |  |
| n_prior_failures | integer(32) | Y |  |
| concordance_index | numeric(6) | Y |  |
| champion_model | character varying(48) | Y |  |
| weibull_shape | numeric(8) | Y |  |
| data_quality_gate_passed | boolean | Y |  |
| days_since_hw_oos | numeric(10) | Y |  |
| roll_fail_30d | integer(32) | Y |  |
| event_definition | character varying(24) | Y |  |
| event_def_version | character varying(64) | Y |  |
| feature_asof_date | date | Y |  |
| as_of_date | date | Y |  |
| event_label | character varying(80) | Y |  |
| window_mode | character varying(24) | Y |  |
| telemetry_start | date | Y |  |
| cindex_floor | numeric(4) | Y |  |

### v_ps5_serial_dupes (view, rows=None)

| column | type | nullable | default |
|---|---|---|---|
| city_id | USER-DEFINED | Y |  |
| device_type | character varying(12) | Y |  |
| device_id | character varying(40) | Y |  |
| component_serial_nbr | character varying(64) | Y |  |
| n_rows | bigint(64) | Y |  |
| n_component_types | bigint(64) | Y |  |
| n_risk_tiers | bigint(64) | Y |  |
| n_asof_dates | bigint(64) | Y |  |
| n_serial_sources | bigint(64) | Y |  |
| n_rul_values | bigint(64) | Y |  |

### v_ps5_serial_fanout (view, rows=None)

| column | type | nullable | default |
|---|---|---|---|
| city_id | USER-DEFINED | Y |  |
| device_type | character varying(12) | Y |  |
| rows_loaded | bigint(64) | Y |  |
| distinct_components | bigint(64) | Y |  |
| surplus_rows | bigint(64) | Y |  |
| fanout_factor | numeric | Y |  |

### v_ps5_serial_oos_latest (view, rows=None)

| column | type | nullable | default |
|---|---|---|---|
| city_id | USER-DEFINED | Y |  |
| device_id | character varying(40) | Y |  |
| component_serial_nbr | character varying(64) | Y |  |
| component_type | character varying(40) | Y |  |
| mars_device_category | character varying(12) | Y |  |
| component_age_days | numeric(10) | Y |  |
| device_oos_failures_total | integer(32) | Y |  |
| risk_score | numeric(12) | Y |  |
| risk_tier | character varying(10) | Y |  |
| expected_component_rul_days | numeric(10) | Y |  |
| predicted_median_survival_days | numeric(10) | Y |  |
| is_overdue | boolean | Y |  |
| event_definition | character varying(24) | Y |  |
| event_def_version | character varying(64) | Y |  |
| feature_asof_date | date | Y |  |
| as_of_date | date | Y |  |

### v_ps5_serial_rul (view, rows=None)

| column | type | nullable | default |
|---|---|---|---|
| city_id | USER-DEFINED | Y |  |
| device_type | character varying(12) | Y |  |
| device_id | character varying(40) | Y |  |
| component_serial_nbr | character varying(64) | Y |  |
| component_type_name | character varying(120) | Y |  |
| mars_device_category | character varying(12) | Y |  |
| component_age_days | numeric(10) | Y |  |
| device_oos_failures_total | integer(32) | Y |  |
| risk_score | numeric(12) | Y |  |
| risk_tier | character varying(16) | Y |  |
| expected_component_rul_days | numeric(10) | Y |  |
| predicted_median_survival_days | numeric(10) | Y |  |
| is_overdue | boolean | Y |  |
| event_definition | character varying(40) | Y |  |
| event_def_version | character varying(60) | Y |  |
| feature_asof_date | date | Y |  |
| serial_source | character varying(60) | Y |  |
| has_serial | boolean | Y |  |
| rul_rank_in_type | bigint(64) | Y |  |
| n_components_in_type | bigint(64) | Y |  |
| act_now | boolean | Y |  |

## Dimensions / shared — 11 tables (47,355 rows), 5 views

| object | kind | rows | cols | primary key |
|---|---|---:|---:|---|
| dim_device_station | table | 18,616 | 9 | city_id, device_id |
| dim_device_component | table | 11,718 | 6 | city_id, device_id, component_serial_nbr |
| dim_device_serial | table | 11,718 | 9 | city_id, device_id, serial_id |
| dim_device_bus | table | 4,218 | 17 | city_id, device_id |
| dim_station | table | 1,011 | 9 | city_id, facility_id |
| ml_batch_load_audit | table | 61 | 13 | id |
| ml_model_registry | table | 6 | 15 | - |
| cities | table | 4 | 7 | id |
| ml_models | table | 3 | 17 | id |
| servicenow_incidents | table | 0 | 15 | id |
| servicenow_staging | table | 0 | 8 | id |
| v_component_inventory | view | - | 10 | - |
| v_device_bus | view | - | 13 | - |
| v_device_serial | view | - | 8 | - |
| v_device_serial_counts | view | - | 7 | - |
| v_fleet_event_baseline | view | - | 11 | - |

### dim_device_station (table, rows=18616)

| column | type | nullable | default |
|---|---|---|---|
| city_id | USER-DEFINED | N |  |
| device_id | character varying(40) | N |  |
| device_key | numeric(12) | Y |  |
| mars_device_category | character varying(12) | Y |  |
| facility_id | character varying(20) | Y |  |
| facility_name | character varying(120) | Y |  |
| operator_id | character varying(20) | Y |  |
| operator_name | character varying(80) | Y |  |
| as_of_date | date | N |  |

### dim_device_component (table, rows=11718)

| column | type | nullable | default |
|---|---|---|---|
| city_id | USER-DEFINED | N |  |
| device_id | character varying(40) | N |  |
| component_serial_nbr | character varying(64) | N |  |
| component_description | character varying(120) | Y |  |
| component_age_days | numeric(10) | Y |  |
| as_of_date | date | N |  |

### dim_device_serial (table, rows=11718)

| column | type | nullable | default |
|---|---|---|---|
| city_id | USER-DEFINED | N |  |
| device_id | character varying(40) | N |  |
| serial_id | character varying(64) | N |  |
| mars_device_category | character varying(12) | Y |  |
| component_description | character varying(120) | Y |  |
| component_age_days | integer(32) | Y |  |
| age_is_negative | boolean | Y |  |
| source_table | character varying(120) | Y |  |
| as_of_date | date | N |  |

### dim_device_bus (table, rows=4218)

| column | type | nullable | default |
|---|---|---|---|
| city_id | text | N |  |
| device_id | text | N |  |
| device_key | text | Y |  |
| device_name | text | Y |  |
| bus_id | text | Y |  |
| bus_device_flag | text | Y |  |
| component_serial_nbr | text | Y |  |
| component_type | text | Y |  |
| facility_id | text | Y |  |
| facility_name | text | Y |  |
| operator_id | text | Y |  |
| operator_name | text | Y |  |
| device_category | text | Y |  |
| device_type_name | text | Y |  |
| transit_mode_name | text | Y |  |
| serial_number | text | Y |  |
| loaded_at | timestamp with time zone | Y | now() |

### dim_station (table, rows=1011)

| column | type | nullable | default |
|---|---|---|---|
| city_id | USER-DEFINED | N |  |
| facility_id | character varying(20) | N |  |
| station_name | character varying(120) | N |  |
| operator | character varying(80) | Y |  |
| short_name | character varying(120) | Y |  |
| latitude | numeric(9) | Y |  |
| longitude | numeric(9) | Y |  |
| source | character varying(40) | Y | 'ps2_run'::character varying |
| loaded_at | timestamp with time zone | Y | now() |

### ml_batch_load_audit (table, rows=61)

| column | type | nullable | default |
|---|---|---|---|
| id | bigint(64) | N | nextval('ml_batch_load_audit_id_seq'::regclass) |
| city_id | USER-DEFINED | N |  |
| ps_id | character varying(8) | N |  |
| run_id | character varying(48) | Y |  |
| target_table | character varying(64) | N |  |
| s3_source | character varying(300) | Y |  |
| rows_read | integer(32) | Y |  |
| rows_loaded | integer(32) | Y |  |
| columns_added | text | Y |  |
| columns_skipped | text | Y |  |
| status | character varying(16) | N |  |
| error_text | text | Y |  |
| loaded_at | timestamp with time zone | Y | now() |

### ml_model_registry (table, rows=6)

| column | type | nullable | default |
|---|---|---|---|
| model_registry_id | bigint(64) | N |  |
| ps_id | character varying(10) | N |  |
| device_category | character varying(20) | Y |  |
| prediction_head | character varying(40) | Y |  |
| model_name | character varying(100) | N |  |
| model_version | integer(32) | N |  |
| algorithm | character varying(60) | N |  |
| registry_alias | character varying(20) | Y |  |
| training_run_id | character varying(40) | Y |  |
| test_auc | numeric(6) | Y |  |
| test_pr_auc | numeric(6) | Y |  |
| test_f1_macro | numeric(6) | Y |  |
| decision_threshold | numeric(6) | Y |  |
| deployed_at | timestamp without time zone | Y |  |
| status | character varying(20) | N |  |

### cities (table, rows=4)

| column | type | nullable | default |
|---|---|---|---|
| id | USER-DEFINED | N |  |
| name | character varying(64) | N |  |
| region | character varying(64) | Y |  |
| timezone | character varying(48) | Y | 'UTC'::character varying |
| is_pilot | boolean | Y | false |
| go_live | date | Y |  |
| created_at | timestamp with time zone | Y | now() |

### ml_models (table, rows=3)

| column | type | nullable | default |
|---|---|---|---|
| id | uuid | N | uuid_generate_v4() |
| model_name | character varying(128) | N |  |
| problem_stmt | character varying(8) | N |  |
| city_id | USER-DEFINED | N |  |
| device_type | USER-DEFINED | N |  |
| version | character varying(16) | N | '1.0.0'::character varying |
| algorithm | character varying(64) | Y |  |
| accuracy | numeric(5) | Y |  |
| f1_score | numeric(5) | Y |  |
| training_date | timestamp with time zone | Y |  |
| s3_artifact_uri | text | Y |  |
| is_active | boolean | Y | true |
| deployed_env | USER-DEFINED | Y |  |
| created_at | timestamp with time zone | Y | now() |
| is_champion | boolean | Y | false |
| primary_metric_value | numeric(8) | Y |  |
| primary_metric_name | character varying(20) | Y |  |

### servicenow_incidents (table, rows=0)

| column | type | nullable | default |
|---|---|---|---|
| id | bigint(64) | N | nextval('servicenow_incidents_id_seq'::regclass) |
| correlation_id | text | N |  |
| device_id | text | N |  |
| serial_id | text | Y |  |
| ps_source | text | N |  |
| category | text | Y |  |
| payload_hash | text | Y |  |
| inc_number | text | Y |  |
| return_code | integer(32) | Y |  |
| status | USER-DEFINED | N | 'created'::servicenow_incident_status |
| error_detail | text | Y |  |
| city_id | text | N | 'CHI'::text |
| created_by | text | Y |  |
| created_at | timestamp with time zone | Y | now() |
| updated_at | timestamp with time zone | Y | now() |

### servicenow_staging (table, rows=0)

| column | type | nullable | default |
|---|---|---|---|
| id | uuid | N |  |
| city_id | USER-DEFINED | N |  |
| device_id | character varying(30) | N |  |
| device_category | character varying(12) | Y |  |
| short_description | character varying(240) | Y |  |
| payload_json | text | Y |  |
| status | character varying(20) | N | 'staged'::character varying |
| created_at | timestamp with time zone | Y | now() |

### v_component_inventory (view, rows=None)

| column | type | nullable | default |
|---|---|---|---|
| city_id | USER-DEFINED | Y |  |
| mars_device_category | character varying(12) | Y |  |
| component_description | character varying(120) | Y |  |
| n_components | bigint(64) | Y |  |
| n_devices | bigint(64) | Y |  |
| avg_age_days | numeric | Y |  |
| min_age_days | integer(32) | Y |  |
| max_age_days | integer(32) | Y |  |
| n_replaced_after_event | bigint(64) | Y |  |
| as_of_date | date | Y |  |

### v_device_bus (view, rows=None)

| column | type | nullable | default |
|---|---|---|---|
| city_id | text | Y |  |
| device_id | text | Y |  |
| bus_id | text | Y |  |
| serial_number | text | Y |  |
| component_serial_nbr | text | Y |  |
| component_type | text | Y |  |
| facility_id | text | Y |  |
| facility_name | text | Y |  |
| operator_name | text | Y |  |
| device_category | text | Y |  |
| transit_mode_name | text | Y |  |
| bus_label | text | Y |  |
| on_vehicle | boolean | Y |  |

### v_device_serial (view, rows=None)

| column | type | nullable | default |
|---|---|---|---|
| city_id | USER-DEFINED | Y |  |
| device_id | character varying(40) | Y |  |
| serial_id | character varying(64) | Y |  |
| mars_device_category | character varying(12) | Y |  |
| component_description | character varying(120) | Y |  |
| component_age_days | integer(32) | Y |  |
| age_is_negative | boolean | Y |  |
| as_of_date | date | Y |  |

### v_device_serial_counts (view, rows=None)

| column | type | nullable | default |
|---|---|---|---|
| city_id | USER-DEFINED | Y |  |
| mars_device_category | character varying(12) | Y |  |
| n_devices | bigint(64) | Y |  |
| n_serial_rows | bigint(64) | Y |  |
| n_distinct_serials | bigint(64) | Y |  |
| serials_per_device | numeric | Y |  |
| as_of_date | date | Y |  |

### v_fleet_event_baseline (view, rows=None)

| column | type | nullable | default |
|---|---|---|---|
| city_id | USER-DEFINED | Y |  |
| scope | character varying(40) | Y |  |
| device_category | character varying | Y |  |
| period_start | date | Y |  |
| period_end | date | Y |  |
| total_oos_events | numeric | Y |  |
| chargeable_events | numeric | Y |  |
| chargeable_pct | numeric | Y |  |
| n_devices | bigint(64) | Y |  |
| source_table | character varying | Y |  |
| as_of_date | date | Y |  |

## Legacy app scaffold (mostly empty) — 25 tables (21 rows), 5 views

| object | kind | rows | cols | primary key |
|---|---|---:|---:|---|
| toc_companies | table | 11 | 5 | code |
| schema_migrations | table | 5 | 3 | version |
| system_config | table | 5 | 5 | key |
| anomalies | table | 0 | 12 | id |
| anomaly_timeline | table | 0 | 8 | id |
| audit_log | table | 0 | 9 | id |
| cascade_events | table | 0 | 10 | id |
| compliance_trend | table | 0 | 7 | id |
| contributing_factors | table | 0 | 9 | id |
| device_correlations | table | 0 | 8 | id |
| device_event_totals | table | 0 | 11 | city_id, device_id, as_of_date |
| device_health | table | 0 | 9 | id |
| devices | table | 0 | 9 | id |
| failure_predictions | table | 0 | 10 | id |
| fleet_event_baseline | table | 0 | 13 | city_id, scope, device_category, as_of_date |
| outlier_scores | table | 0 | 9 | id |
| performance_metrics | table | 0 | 10 | id |
| prediction_timeseries | table | 0 | 8 | id |
| refresh_tokens | table | 0 | 6 | id |
| root_causes | table | 0 | 10 | id |
| severity_distribution | table | 0 | 7 | id |
| sla_breaches | table | 0 | 12 | id |
| sla_metrics | table | 0 | 12 | id |
| user_permissions | table | 0 | 6 | id |
| users | table | 0 | 10 | id |
| v_active_anomalies | view | - | 4 | - |
| v_bus_fleet | view | - | 6 | - |
| v_ml_batch_freshness | view | - | 7 | - |
| v_prediction_summary | view | - | 6 | - |
| v_sla_compliance | view | - | 4 | - |

### toc_companies (table, rows=11)

| column | type | nullable | default |
|---|---|---|---|
| code | USER-DEFINED | N |  |
| full_name | character varying(128) | N |  |
| region | character varying(64) | Y |  |
| active | boolean | Y | true |
| created_at | timestamp with time zone | Y | now() |

### schema_migrations (table, rows=5)

| column | type | nullable | default |
|---|---|---|---|
| version | character varying(14) | N |  |
| name | character varying(256) | N |  |
| applied_at | timestamp with time zone | Y | now() |

### system_config (table, rows=5)

| column | type | nullable | default |
|---|---|---|---|
| key | character varying(128) | N |  |
| value | jsonb | N |  |
| description | text | Y |  |
| updated_by | uuid | Y |  |
| updated_at | timestamp with time zone | Y | now() |

### anomalies (table, rows=0)

| column | type | nullable | default |
|---|---|---|---|
| id | uuid | N | uuid_generate_v4() |
| city_id | USER-DEFINED | N |  |
| device_type | USER-DEFINED | N |  |
| toc_code | USER-DEFINED | Y |  |
| anomaly_type | character varying(64) | N |  |
| severity | USER-DEFINED | N |  |
| anomaly_score | numeric(5) | N |  |
| description | text | Y |  |
| detected_at | timestamp with time zone | N | now() |
| resolved_at | timestamp with time zone | Y |  |
| status | USER-DEFINED | Y | 'active'::alert_status |
| created_at | timestamp with time zone | Y | now() |

### anomaly_timeline (table, rows=0)

| column | type | nullable | default |
|---|---|---|---|
| id | uuid | N | uuid_generate_v4() |
| city_id | USER-DEFINED | N |  |
| device_type | USER-DEFINED | N |  |
| toc_code | USER-DEFINED | Y |  |
| bucket_time | timestamp with time zone | N |  |
| count | integer(32) | N | 0 |
| avg_score | numeric(5) | Y |  |
| created_at | timestamp with time zone | Y | now() |

### audit_log (table, rows=0)

| column | type | nullable | default |
|---|---|---|---|
| id | bigint(64) | N | nextval('audit_log_id_seq'::regclass) |
| user_id | uuid | Y |  |
| action | character varying(64) | N |  |
| target_type | character varying(64) | Y |  |
| target_id | character varying(128) | Y |  |
| details | jsonb | Y |  |
| ip_address | inet | Y |  |
| user_agent | text | Y |  |
| created_at | timestamp with time zone | Y | now() |

### cascade_events (table, rows=0)

| column | type | nullable | default |
|---|---|---|---|
| id | uuid | N | uuid_generate_v4() |
| chain_id | uuid | N |  |
| city_id | USER-DEFINED | N |  |
| source_device | USER-DEFINED | N |  |
| target_device | USER-DEFINED | N |  |
| toc_code | USER-DEFINED | Y |  |
| propagation_time_sec | integer(32) | N |  |
| impact_score | numeric(5) | Y |  |
| event_time | timestamp with time zone | N |  |
| created_at | timestamp with time zone | Y | now() |

### compliance_trend (table, rows=0)

| column | type | nullable | default |
|---|---|---|---|
| id | uuid | N | uuid_generate_v4() |
| city_id | USER-DEFINED | N |  |
| device_type | USER-DEFINED | N |  |
| toc_code | USER-DEFINED | Y |  |
| compliance_pct | numeric(5) | N |  |
| period_date | date | N |  |
| created_at | timestamp with time zone | Y | now() |

### contributing_factors (table, rows=0)

| column | type | nullable | default |
|---|---|---|---|
| id | uuid | N | uuid_generate_v4() |
| city_id | USER-DEFINED | N |  |
| device_type | USER-DEFINED | N |  |
| toc_code | USER-DEFINED | Y |  |
| factor_name | character varying(128) | N |  |
| importance | numeric(5) | N |  |
| direction | character varying(16) | Y | 'positive'::character varying |
| model_version | character varying(16) | Y |  |
| computed_at | timestamp with time zone | N | now() |

### device_correlations (table, rows=0)

| column | type | nullable | default |
|---|---|---|---|
| id | uuid | N | uuid_generate_v4() |
| city_id | USER-DEFINED | N |  |
| toc_code | USER-DEFINED | Y |  |
| device_a | USER-DEFINED | N |  |
| device_b | USER-DEFINED | N |  |
| correlation | numeric(5) | N |  |
| sample_size | integer(32) | Y |  |
| computed_at | timestamp with time zone | N | now() |

### device_event_totals (table, rows=0)

| column | type | nullable | default |
|---|---|---|---|
| city_id | USER-DEFINED | N |  |
| device_id | character varying(40) | N |  |
| mars_device_category | character varying(12) | Y |  |
| total_hardware_oos_events | bigint(64) | Y |  |
| total_chargeable_events | bigint(64) | Y |  |
| chargeable_pct | numeric(7) | Y |  |
| period_start | date | Y |  |
| period_end | date | Y |  |
| grain_note | character varying(200) | N |  |
| source_table | character varying(120) | Y |  |
| as_of_date | date | N |  |

### device_health (table, rows=0)

| column | type | nullable | default |
|---|---|---|---|
| id | uuid | N | uuid_generate_v4() |
| device_id | integer(32) | Y |  |
| city_id | USER-DEFINED | N |  |
| device_type | USER-DEFINED | N |  |
| toc_code | USER-DEFINED | Y |  |
| health_score | numeric(5) | N |  |
| status | character varying(16) | Y | 'healthy'::character varying |
| last_checked_at | timestamp with time zone | N | now() |
| created_at | timestamp with time zone | Y | now() |

### devices (table, rows=0)

| column | type | nullable | default |
|---|---|---|---|
| id | integer(32) | N | nextval('devices_id_seq'::regclass) |
| device_type | USER-DEFINED | N |  |
| label | character varying(64) | N |  |
| city_id | USER-DEFINED | Y |  |
| toc_code | USER-DEFINED | Y |  |
| asset_id | character varying(64) | Y |  |
| installed | date | Y |  |
| active | boolean | Y | true |
| created_at | timestamp with time zone | Y | now() |

### failure_predictions (table, rows=0)

| column | type | nullable | default |
|---|---|---|---|
| id | uuid | N | uuid_generate_v4() |
| city_id | USER-DEFINED | N |  |
| device_type | USER-DEFINED | N |  |
| toc_code | USER-DEFINED | Y |  |
| predicted_at | timestamp with time zone | N | now() |
| probability | numeric(5) | N |  |
| severity | USER-DEFINED | N |  |
| confidence | numeric(5) | Y |  |
| model_version | character varying(16) | Y | '1.0'::character varying |
| created_at | timestamp with time zone | Y | now() |

### fleet_event_baseline (table, rows=0)

| column | type | nullable | default |
|---|---|---|---|
| city_id | USER-DEFINED | N |  |
| scope | character varying(40) | N |  |
| device_category | character varying(12) | N |  |
| period_start | date | Y |  |
| period_end | date | Y |  |
| total_oos_events | bigint(64) | Y |  |
| chargeable_events | bigint(64) | Y |  |
| chargeable_pct | numeric(7) | Y |  |
| n_devices | integer(32) | Y |  |
| source_table | character varying(120) | Y |  |
| source_run_id | character varying(48) | Y |  |
| notes | text | Y |  |
| as_of_date | date | N |  |

### outlier_scores (table, rows=0)

| column | type | nullable | default |
|---|---|---|---|
| id | uuid | N | uuid_generate_v4() |
| city_id | USER-DEFINED | N |  |
| device_type | USER-DEFINED | N |  |
| toc_code | USER-DEFINED | Y |  |
| x_metric | numeric(10) | N |  |
| y_metric | numeric(10) | N |  |
| is_outlier | boolean | Y | false |
| z_score | numeric(6) | Y |  |
| recorded_at | timestamp with time zone | N | now() |

### performance_metrics (table, rows=0)

| column | type | nullable | default |
|---|---|---|---|
| id | uuid | N | uuid_generate_v4() |
| city_id | USER-DEFINED | N |  |
| device_type | USER-DEFINED | N |  |
| toc_code | USER-DEFINED | Y |  |
| metric_name | character varying(128) | N |  |
| current_value | numeric(12) | N |  |
| previous_value | numeric(12) | Y |  |
| change_pct | numeric(6) | Y |  |
| trend_direction | character varying(8) | Y | 'stable'::character varying |
| recorded_at | timestamp with time zone | N | now() |

### prediction_timeseries (table, rows=0)

| column | type | nullable | default |
|---|---|---|---|
| id | uuid | N | uuid_generate_v4() |
| city_id | USER-DEFINED | N |  |
| device_type | USER-DEFINED | N |  |
| toc_code | USER-DEFINED | Y |  |
| ts | timestamp with time zone | N |  |
| value | numeric(10) | N |  |
| metric_name | character varying(64) | N | 'failure_prob'::character varying |
| created_at | timestamp with time zone | Y | now() |

### refresh_tokens (table, rows=0)

| column | type | nullable | default |
|---|---|---|---|
| id | uuid | N | uuid_generate_v4() |
| user_id | uuid | N |  |
| token_hash | text | N |  |
| expires_at | timestamp with time zone | N |  |
| revoked | boolean | Y | false |
| created_at | timestamp with time zone | Y | now() |

### root_causes (table, rows=0)

| column | type | nullable | default |
|---|---|---|---|
| id | uuid | N | uuid_generate_v4() |
| city_id | USER-DEFINED | N |  |
| device_type | USER-DEFINED | N |  |
| toc_code | USER-DEFINED | Y |  |
| cause_category | character varying(128) | N |  |
| occurrence_count | integer(32) | N | 0 |
| severity | USER-DEFINED | Y |  |
| avg_resolution_hrs | numeric(8) | Y |  |
| analyzed_at | timestamp with time zone | N | now() |
| created_at | timestamp with time zone | Y | now() |

### severity_distribution (table, rows=0)

| column | type | nullable | default |
|---|---|---|---|
| id | uuid | N | uuid_generate_v4() |
| city_id | USER-DEFINED | N |  |
| device_type | USER-DEFINED | N |  |
| toc_code | USER-DEFINED | Y |  |
| severity | USER-DEFINED | N |  |
| count | integer(32) | N | 0 |
| snapshot_at | timestamp with time zone | N | now() |

### sla_breaches (table, rows=0)

| column | type | nullable | default |
|---|---|---|---|
| id | uuid | N | uuid_generate_v4() |
| city_id | USER-DEFINED | N |  |
| device_type | USER-DEFINED | N |  |
| toc_code | USER-DEFINED | Y |  |
| breach_type | character varying(128) | N |  |
| severity | USER-DEFINED | N |  |
| duration_hrs | numeric(8) | Y |  |
| root_cause | text | Y |  |
| remediation | text | Y |  |
| breached_at | timestamp with time zone | N |  |
| resolved_at | timestamp with time zone | Y |  |
| created_at | timestamp with time zone | Y | now() |

### sla_metrics (table, rows=0)

| column | type | nullable | default |
|---|---|---|---|
| id | uuid | N | uuid_generate_v4() |
| city_id | USER-DEFINED | N |  |
| device_type | USER-DEFINED | N |  |
| toc_code | USER-DEFINED | Y |  |
| metric_name | character varying(128) | N |  |
| target_value | numeric(10) | N |  |
| actual_value | numeric(10) | N |  |
| unit | character varying(16) | Y | '%'::character varying |
| is_breached | boolean | Y |  |
| period_start | timestamp with time zone | N |  |
| period_end | timestamp with time zone | N |  |
| created_at | timestamp with time zone | Y | now() |

### user_permissions (table, rows=0)

| column | type | nullable | default |
|---|---|---|---|
| id | uuid | N | uuid_generate_v4() |
| user_id | uuid | N |  |
| city_id | USER-DEFINED | Y |  |
| device_type | USER-DEFINED | Y |  |
| toc_code | USER-DEFINED | Y |  |
| granted_at | timestamp with time zone | Y | now() |

### users (table, rows=0)

| column | type | nullable | default |
|---|---|---|---|
| id | uuid | N | uuid_generate_v4() |
| username | character varying(64) | N |  |
| email | character varying(256) | N |  |
| password_hash | text | N |  |
| full_name | character varying(128) | Y |  |
| role | USER-DEFINED | N | 'city_manager'::user_role |
| is_active | boolean | Y | true |
| last_login | timestamp with time zone | Y |  |
| created_at | timestamp with time zone | Y | now() |
| updated_at | timestamp with time zone | Y | now() |

### v_active_anomalies (view, rows=None)

| column | type | nullable | default |
|---|---|---|---|
| city_id | USER-DEFINED | Y |  |
| device_type | USER-DEFINED | Y |  |
| severity | USER-DEFINED | Y |  |
| active_count | bigint(64) | Y |  |

### v_bus_fleet (view, rows=None)

| column | type | nullable | default |
|---|---|---|---|
| city_id | text | Y |  |
| bus_id | text | Y |  |
| operator_name | text | Y |  |
| devices_on_bus | bigint(64) | Y |  |
| distinct_serials | bigint(64) | Y |  |
| facility_name | text | Y |  |

### v_ml_batch_freshness (view, rows=None)

| column | type | nullable | default |
|---|---|---|---|
| ps_id | character varying(8) | Y |  |
| city_id | USER-DEFINED | Y |  |
| device_category | character varying(12) | Y |  |
| last_run_ts | timestamp with time zone | Y |  |
| last_scoring_date | date | Y |  |
| is_stale | boolean | Y |  |
| failed_runs | bigint(64) | Y |  |

### v_prediction_summary (view, rows=None)

| column | type | nullable | default |
|---|---|---|---|
| city_id | USER-DEFINED | Y |  |
| device_type | USER-DEFINED | Y |  |
| severity | USER-DEFINED | Y |  |
| prediction_count | bigint(64) | Y |  |
| avg_probability | numeric | Y |  |
| avg_confidence | numeric | Y |  |

### v_sla_compliance (view, rows=None)

| column | type | nullable | default |
|---|---|---|---|
| city_id | USER-DEFINED | Y |  |
| device_type | USER-DEFINED | Y |  |
| compliance_pct | numeric | Y |  |
| breach_count | bigint(64) | Y |  |
