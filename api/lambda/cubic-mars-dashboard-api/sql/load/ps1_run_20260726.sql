-- ==========================================================================
-- PS1 hardware-OOS run ps1_20260726 -- Chicago / CTA-Ventra
-- Parsed from console_{gate,tvm,validator}.log of the 26-Jul-2026 run.
--
-- SCOPE. This is the MODEL tier: champion, leaderboard, threshold, confusion,
-- calibration, SHAP drivers, top-K lift and monthly drift. Per-device scores
-- are NOT here -- the v3 notebooks write no prediction file; the 357,727 test
-- predictions go to gold device_ps1_cross_wired_daily and need a batch reader.
-- ps1_failure_predictions is deliberately left empty rather than filled with
-- anything approximate.
--
-- Target is will_hardware_oos_3d. Chargeable events are a contract
-- classification applied after the physical event and are NOT the target.
-- ==========================================================================
DELETE FROM ps1_feature_importance WHERE city_id='CHI' AND computed_date=DATE '2026-07-26';
DELETE FROM ps1_threshold_sweep    WHERE city_id='CHI' AND computed_date=DATE '2026-07-26';
DELETE FROM ps1_risk_bands         WHERE city_id='CHI' AND computed_date=DATE '2026-07-26';
DELETE FROM ps1_confusion          WHERE city_id='CHI' AND computed_date=DATE '2026-07-26';
DELETE FROM ps1_model_performance  WHERE city_id='CHI' AND computed_date=DATE '2026-07-26';
DELETE FROM ps1_risk_trend         WHERE city_id='CHI' AND computed_date=DATE '2026-07-26';
DELETE FROM ps1_leaderboard        WHERE city_id='CHI' AND as_of_date=DATE '2026-07-26';
DELETE FROM ps1_inference_runs     WHERE city_id='CHI' AND run_id='ps1_20260726';

-- 2026-08-11. Same omission as the sklearn load, same fix. See
-- sql/load/ps1_sklearn_20260726.sql for the full reasoning.
INSERT INTO ps1_model_performance (city_id, device_category, model_name, algorithm, train_auc, train_ap, train_f1, val_auc, val_ap, val_f1, test_auc, test_ap, test_f1, test_prec, test_rec, decision_threshold, mlflow_version, endpoint_name, n_features, quality_gate, promoted, computed_date, run_id, target_col, label_revision, recall_floor) VALUES
  ('CHI', 'GATE', 'chicago-ps1-3d-gate-failure', 'spark_xgb_optuna', NULL, NULL, NULL, NULL, NULL, NULL, 0.8868, 0.9495, 0.8707, 0.7710, 0.9999, 0.1349, 'v2', 'chicago-ps1-3d-gate-failure-v1', 67, 'PASS', TRUE, '2026-07-26', 'ps1_20260726', 'will_hardware_oos_3d', 'R7-1', 0.70),
  ('CHI', 'TVM', 'chicago-ps1-3d-tvm-failure', 'spark_xgb_optuna', NULL, NULL, NULL, NULL, NULL, NULL, 0.9106, 0.9852, 0.9762, 0.9539, 0.9995, 0.0255, 'v2', 'chicago-ps1-3d-tvm-failure-v1', 67, 'PASS', TRUE, '2026-07-26', 'ps1_20260726', 'will_hardware_oos_3d', 'R7-1', 0.80),
  ('CHI', 'VALIDATOR', 'chicago-ps1-3d-validator-failure', 'spark_xgb_optuna', NULL, NULL, NULL, NULL, NULL, NULL, 0.9956, 0.9909, 0.9825, 0.9705, 0.9948, 0.4513, 'v2', 'chicago-ps1-3d-validator-failure-v1', 43, 'PASS', TRUE, '2026-07-26', 'ps1_20260726', 'will_hardware_oos_3d', 'R7-1', NULL);

INSERT INTO ps1_leaderboard (city_id, device, model, auc, ap, f1, prec, rec, lb_rank, is_champion, note, as_of_date) VALUES
  ('CHI', 'GATE', 'spark_xgb_optuna', 0.886809, 0.949459, NULL, NULL, NULL, 1, TRUE, 'champion; val_auc 0.880305; ECE 0.1013; thr 0.1349', '2026-07-26'),
  ('CHI', 'GATE', 'spark_gbt', 0.884457, 0.949100, NULL, NULL, NULL, 2, FALSE, 'val_auc 0.877915', '2026-07-26'),
  ('CHI', 'GATE', 'spark_rf', 0.884015, 0.945001, NULL, NULL, NULL, 3, FALSE, 'val_auc 0.874685', '2026-07-26'),
  ('CHI', 'GATE', 'spark_xgb', 0.868523, 0.944009, NULL, NULL, NULL, 4, FALSE, 'val_auc 0.872309', '2026-07-26'),
  ('CHI', 'GATE', 'spark_lr', 0.771375, 0.870708, NULL, NULL, NULL, 5, FALSE, 'val_auc 0.756202', '2026-07-26'),
  ('CHI', 'TVM', 'spark_xgb_optuna', 0.910602, 0.985186, NULL, NULL, NULL, 1, TRUE, 'champion; val_auc 0.872745; ECE 0.0721; thr 0.0255', '2026-07-26'),
  ('CHI', 'TVM', 'spark_xgb', 0.906392, 0.984554, NULL, NULL, NULL, 2, FALSE, 'val_auc 0.869578', '2026-07-26'),
  ('CHI', 'TVM', 'spark_gbt', 0.904434, 0.982907, NULL, NULL, NULL, 3, FALSE, 'val_auc 0.867396', '2026-07-26'),
  ('CHI', 'TVM', 'spark_rf', 0.896084, 0.981134, NULL, NULL, NULL, 4, FALSE, 'val_auc 0.869332', '2026-07-26'),
  ('CHI', 'TVM', 'spark_lr', 0.849875, 0.974726, NULL, NULL, NULL, 5, FALSE, 'val_auc 0.798180', '2026-07-26'),
  ('CHI', 'VALIDATOR', 'spark_xgb_optuna', 0.995611, 0.990869, NULL, NULL, NULL, 1, TRUE, 'champion; val_auc 0.999089; ECE 0.0097; thr 0.4513', '2026-07-26'),
  ('CHI', 'VALIDATOR', 'spark_xgb', 0.995495, 0.990572, NULL, NULL, NULL, 2, FALSE, 'val_auc 0.999045', '2026-07-26'),
  ('CHI', 'VALIDATOR', 'spark_gbt', 0.994749, 0.989051, NULL, NULL, NULL, 3, FALSE, 'val_auc 0.998887', '2026-07-26'),
  ('CHI', 'VALIDATOR', 'spark_rf', 0.994261, 0.987498, NULL, NULL, NULL, 4, FALSE, 'val_auc 0.998672', '2026-07-26'),
  ('CHI', 'VALIDATOR', 'spark_lr', 0.992123, 0.981091, NULL, NULL, NULL, 5, FALSE, 'val_auc 0.997370', '2026-07-26');

INSERT INTO ps1_confusion (city_id, device_category, tp, fp, tn, fn, computed_date) VALUES
  ('CHI', 'GATE', 61811, 18355, 550, 4, '2026-07-26'),
  ('CHI', 'TVM', 41708, 2015, 1936, 22, '2026-07-26'),
  ('CHI', 'VALIDATOR', 88626, 2694, 139541, 465, '2026-07-26');

INSERT INTO ps1_feature_importance (city_id, device_category, feature_name, avg_importance, avg_shap, feat_rank, computed_date) VALUES
  ('CHI', 'GATE', 'hardware_oos_count_prior_sum_7d', 0.810642, 0.810642, 1, '2026-07-26'),
  ('CHI', 'GATE', 'usage_days_since_last_failure', 0.466397, 0.466397, 2, '2026-07-26'),
  ('CHI', 'GATE', 'usage_days_in_service', 0.384128, 0.384128, 3, '2026-07-26'),
  ('CHI', 'GATE', 'metric_rolling_7d_avg_ms', 0.333034, 0.333034, 4, '2026-07-26'),
  ('CHI', 'GATE', 'usage_cumulative_failure_count', 0.312691, 0.312691, 5, '2026-07-26'),
  ('CHI', 'GATE', 'usage_cumulative_outage_min', 0.179042, 0.179042, 6, '2026-07-26'),
  ('CHI', 'GATE', 'availability_pct_7d', 0.105624, 0.105624, 7, '2026-07-26'),
  ('CHI', 'GATE', 'metric_p95_txn_ms', 0.077199, 0.077199, 8, '2026-07-26'),
  ('CHI', 'GATE', 'comms_events_prior_sum_30d', 0.021696, 0.021696, 9, '2026-07-26'),
  ('CHI', 'GATE', 'comms_events_prior_sum_7d', 0.019610, 0.019610, 10, '2026-07-26'),
  ('CHI', 'GATE', 'peak_hour_tap_count', 0.016067, 0.016067, 11, '2026-07-26'),
  ('CHI', 'GATE', 'tap_count', 0.011661, 0.011661, 12, '2026-07-26'),
  ('CHI', 'GATE', 'metric_slow_tap_count', 0.011500, 0.011500, 13, '2026-07-26'),
  ('CHI', 'GATE', 'chain_length', 0.011129, 0.011129, 14, '2026-07-26'),
  ('CHI', 'GATE', 'use_revenue_cents', 0.010177, 0.010177, 15, '2026-07-26'),
  ('CHI', 'TVM', 'roll_fail_90d', 1.188279, 1.188279, 1, '2026-07-26'),
  ('CHI', 'TVM', 'hardware_oos_count_prior_sum_7d', 0.552745, 0.552745, 2, '2026-07-26'),
  ('CHI', 'TVM', 'usage_cumulative_failure_count', 0.460817, 0.460817, 3, '2026-07-26'),
  ('CHI', 'TVM', 'days_since_fail', 0.438161, 0.438161, 4, '2026-07-26'),
  ('CHI', 'TVM', 'chargeable_outage_count_prior_sum_30d', 0.338755, 0.338755, 5, '2026-07-26'),
  ('CHI', 'TVM', 'scrst_events_prior_sum_7d', 0.288732, 0.288732, 6, '2026-07-26'),
  ('CHI', 'TVM', 'usage_days_in_service', 0.274662, 0.274662, 7, '2026-07-26'),
  ('CHI', 'TVM', 'comms_events_prior_sum_7d', 0.236351, 0.236351, 8, '2026-07-26'),
  ('CHI', 'TVM', 'chu_events_prior_sum_30d', 0.210962, 0.210962, 9, '2026-07-26'),
  ('CHI', 'TVM', 'chain_length', 0.162976, 0.162976, 10, '2026-07-26'),
  ('CHI', 'TVM', 'usage_cumulative_outage_min', 0.131952, 0.131952, 11, '2026-07-26'),
  ('CHI', 'TVM', 'comms_events_prior_sum_30d', 0.128019, 0.128019, 12, '2026-07-26'),
  ('CHI', 'TVM', 'bankcard_events_prior_sum_30d', 0.099758, 0.099758, 13, '2026-07-26'),
  ('CHI', 'TVM', 'scrst_events_prior_sum_30d', 0.094379, 0.094379, 14, '2026-07-26'),
  ('CHI', 'TVM', 'chu_events_prior_sum_7d', 0.086046, 0.086046, 15, '2026-07-26'),
  ('CHI', 'VALIDATOR', 'days_since_fail', 3.079347, 3.079347, 1, '2026-07-26'),
  ('CHI', 'VALIDATOR', 'fail_free_streak', 0.869544, 0.869544, 2, '2026-07-26'),
  ('CHI', 'VALIDATOR', 'roll_fail_7d', 0.861041, 0.861041, 3, '2026-07-26'),
  ('CHI', 'VALIDATOR', 'availability_pct_7d', 0.534885, 0.534885, 4, '2026-07-26'),
  ('CHI', 'VALIDATOR', 'tap_count', 0.373150, 0.373150, 5, '2026-07-26'),
  ('CHI', 'VALIDATOR', 'metric_rolling_7d_avg_ms', 0.346791, 0.346791, 6, '2026-07-26'),
  ('CHI', 'VALIDATOR', 'metric_p95_txn_ms', 0.210878, 0.210878, 7, '2026-07-26'),
  ('CHI', 'VALIDATOR', 'metric_slow_tap_count', 0.208340, 0.208340, 8, '2026-07-26'),
  ('CHI', 'VALIDATOR', 'mttr_failure_days_30d', 0.208001, 0.208001, 9, '2026-07-26'),
  ('CHI', 'VALIDATOR', 'roll_fail_30d', 0.200724, 0.200724, 10, '2026-07-26'),
  ('CHI', 'VALIDATOR', 'peak_hour_tap_count', 0.174660, 0.174660, 11, '2026-07-26'),
  ('CHI', 'VALIDATOR', 'roll_fail_90d', 0.162498, 0.162498, 12, '2026-07-26'),
  ('CHI', 'VALIDATOR', 'comms_events_prior_sum_30d', 0.159595, 0.159595, 13, '2026-07-26'),
  ('CHI', 'VALIDATOR', 'use_revenue_cents', 0.157458, 0.157458, 14, '2026-07-26'),
  ('CHI', 'VALIDATOR', 'mttr_failure_days_90d', 0.142591, 0.142591, 15, '2026-07-26');

INSERT INTO ps1_threshold_sweep (city_id, device_category, threshold, precision, recall, alert_rate, f1, computed_date) VALUES
  ('CHI', 'GATE', 0.13, 0.7710, 0.9999, 0.9931, 0.8707, '2026-07-26'),
  ('CHI', 'TVM', 0.03, 0.9539, 0.9995, 0.9571, 0.9762, '2026-07-26'),
  ('CHI', 'VALIDATOR', 0.45, 0.9705, 0.9948, 0.3948, 0.9825, '2026-07-26');

INSERT INTO ps1_risk_bands (city_id, device_category, band, device_count, pct, computed_date) VALUES
  ('CHI', 'GATE', 'Top 1%', 80720, 96.78, '2026-07-26'),
  ('CHI', 'GATE', 'Top 5%', 80720, 96.31, '2026-07-26'),
  ('CHI', 'GATE', 'Top 10%', 80720, 96.18, '2026-07-26'),
  ('CHI', 'TVM', 'Top 1%', 45681, 99.34, '2026-07-26'),
  ('CHI', 'TVM', 'Top 5%', 45681, 99.17, '2026-07-26'),
  ('CHI', 'TVM', 'Top 10%', 45681, 98.86, '2026-07-26'),
  ('CHI', 'VALIDATOR', 'Top 1%', 231326, 99.7, '2026-07-26'),
  ('CHI', 'VALIDATOR', 'Top 5%', 231326, 99.61, '2026-07-26'),
  ('CHI', 'VALIDATOR', 'Top 10%', 231326, 99.41, '2026-07-26');

INSERT INTO ps1_risk_trend (city_id, trend_date, device_category, avg_prob_pct, failures, total, computed_date) VALUES
  ('CHI', '2026-01-01', 'GATE', 83.149, 20680, 24871, '2026-07-26'),
  ('CHI', '2026-02-01', 'GATE', 76.055, 17012, 22368, '2026-07-26'),
  ('CHI', '2026-03-01', 'GATE', 75.31, 18585, 24678, '2026-07-26'),
  ('CHI', '2026-04-01', 'GATE', 62.91, 5538, 8803, '2026-07-26'),
  ('CHI', '2026-01-01', 'TVM', 92.535, 13176, 14239, '2026-07-26'),
  ('CHI', '2026-02-01', 'TVM', 91.66, 11847, 12925, '2026-07-26'),
  ('CHI', '2026-03-01', 'TVM', 92.999, 12765, 13726, '2026-07-26'),
  ('CHI', '2026-04-01', 'TVM', 82.279, 3942, 4791, '2026-07-26'),
  ('CHI', '2026-01-01', 'VALIDATOR', 36.906, 26164, 70893, '2026-07-26'),
  ('CHI', '2026-02-01', 'VALIDATOR', 39.635, 25598, 64584, '2026-07-26'),
  ('CHI', '2026-03-01', 'VALIDATOR', 39.982, 28353, 70915, '2026-07-26'),
  ('CHI', '2026-04-01', 'VALIDATOR', 35.999, 8976, 24934, '2026-07-26');

INSERT INTO ps1_inference_runs (city_id, run_id, run_ts, run_kind, device_category, scoring_date, endpoint_name, serving_image, model_version, mlflow_version, target_col, decision_threshold, n_devices_scored, n_flagged, gold_snapshot_s3, status, error_text, duration_s, computed_date) VALUES
  ('CHI', 'ps1_20260726', TIMESTAMPTZ '2026-07-26 16:40:00+00', 'train', 'GATE', DATE '2026-07-26', 'chicago-ps1-3d-gate-failure-v1', NULL, 'v2', NULL, 'will_hardware_oos_3d', 0.1349, 80720, 61811, 's3://cubic-mars-pm-s3-datalake-dev-gold-170202974600/chicago/gold/device_ps1_cross_wired_daily', 'success', NULL, NULL, '2026-07-26'),
  ('CHI', 'ps1_20260726', TIMESTAMPTZ '2026-07-26 16:40:00+00', 'train', 'TVM', DATE '2026-07-26', 'chicago-ps1-3d-tvm-failure-v1', NULL, 'v2', NULL, 'will_hardware_oos_3d', 0.0255, 45681, 41708, 's3://cubic-mars-pm-s3-datalake-dev-gold-170202974600/chicago/gold/device_ps1_cross_wired_daily', 'success', NULL, NULL, '2026-07-26'),
  ('CHI', 'ps1_20260726', TIMESTAMPTZ '2026-07-26 16:40:00+00', 'train', 'VALIDATOR', DATE '2026-07-26', 'chicago-ps1-3d-validator-failure-v1', NULL, 'v2', NULL, 'will_hardware_oos_3d', 0.4513, 231326, 88626, 's3://cubic-mars-pm-s3-datalake-dev-gold-170202974600/chicago/gold/device_ps1_cross_wired_daily', 'success', NULL, NULL, '2026-07-26');
