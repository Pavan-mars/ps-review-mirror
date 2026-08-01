-- ==========================================================================
-- PS1 v3 sklearn bake-off, run ps1_sklearn_20260726 -- Chicago / CTA-Ventra
--
-- Parsed from the CELL OUTPUTS of
--   PS1_3d_{GATE,TVM,VALIDATOR}_OOS_Optimized_SageMaker_v3_download.ipynb
-- Every value comes from the notebooks' own per-algorithm x per-split table
-- (26 columns incl. tn/fp/fn/tp and the decision threshold). The champion row is
-- cross-checked against the printed "Locked TEST summary" block; a disagreement
-- beyond 5e-4 aborts the generator rather than picking one.
--
-- SUPERSEDES the Spark run for display. Both trained the same target on the same
-- gold table, but on DIFFERENT test splits -- GATE majority 0.7489 here versus
-- 0.7658 there -- so their accuracies are not directly comparable. base_rate is
-- therefore stored per row and never inferred.
--
-- NOTE ON THE ENDPOINTS. chicago-ps1-3d-{gate,tvm,validator}-failure-v1 are still
-- serving the SPARK champion. Until those are re-registered, this scorecard
-- describes the selected model, not the one answering inference calls. That gap
-- is recorded in ps1_inference_runs.error_text so it is visible in the data.
-- ==========================================================================
-- ps1_leaderboard.note is VARCHAR(60) in sql/04. The Spark run's notes fit by
-- luck; these carry MCC and balanced accuracy as well and do not. Widening beats
-- truncating -- MCC is the metric that separates these four near-tied models
-- (GATE: AP within 0.0006 of each other, MCC spread 0.624..0.649).
ALTER TABLE ps1_leaderboard ALTER COLUMN note TYPE VARCHAR(200);

DELETE FROM ps1_leaderboard        WHERE city_id='CHI' AND as_of_date=DATE '2026-07-26';
DELETE FROM ps1_model_performance  WHERE city_id='CHI' AND computed_date=DATE '2026-07-26';
DELETE FROM ps1_confusion          WHERE city_id='CHI' AND computed_date=DATE '2026-07-26';
DELETE FROM ps1_threshold_sweep    WHERE city_id='CHI' AND computed_date=DATE '2026-07-26';
DELETE FROM ps1_inference_runs     WHERE city_id='CHI' AND run_id='ps1_sklearn_20260726';

INSERT INTO ps1_leaderboard (city_id, device, model, auc, ap, f1, prec, rec, lb_rank, is_champion, note, as_of_date) VALUES
  ('CHI', 'TVM', 'hist_gradient_boosting', 0.904008, 0.983508, 0.976885, 0.960429, 0.993914, 1, TRUE, 'champion; MCC 0.7059; bal-acc 0.7883; acc 0.9571 vs maj 0.9106', '2026-07-26'),
  ('CHI', 'TVM', 'catboost', 0.900615, 0.983143, 0.976140, 0.959192, 0.993698, 2, FALSE, 'MCC 0.6948; bal-acc 0.7814; acc 0.9557 vs maj 0.9106', '2026-07-26'),
  ('CHI', 'TVM', 'lightgbm', 0.891363, 0.979845, 0.976114, 0.960684, 0.992048, 3, FALSE, 'MCC 0.6966; bal-acc 0.7891; acc 0.9557 vs maj 0.9106', '2026-07-26'),
  ('CHI', 'TVM', 'xgboost', 0.888063, 0.978978, 0.974528, 0.963124, 0.986205, 4, FALSE, 'MCC 0.6836; bal-acc 0.8007; acc 0.9530 vs maj 0.9106', '2026-07-26'),
  ('CHI', 'TVM', 'sgd_elasticnet', 0.817232, 0.968181, 0.958276, 0.946803, 0.970031, 5, FALSE, 'MCC 0.4730; bal-acc 0.7073; acc 0.9230 vs maj 0.9106', '2026-07-26'),
  ('CHI', 'GATE', 'xgboost', 0.896146, 0.948575, 0.915127, 0.893707, 0.937600, 1, TRUE, 'champion; MCC 0.6394; bal-acc 0.8025; acc 0.8697 vs maj 0.7488', '2026-07-26'),
  ('CHI', 'GATE', 'lightgbm', 0.896404, 0.948627, 0.912561, 0.887012, 0.939625, 2, FALSE, 'MCC 0.6240; bal-acc 0.7913; acc 0.8651 vs maj 0.7488', '2026-07-26'),
  ('CHI', 'GATE', 'hist_gradient_boosting', 0.893473, 0.948336, 0.912996, 0.891718, 0.935314, 3, FALSE, 'MCC 0.6303; bal-acc 0.7983; acc 0.8665 vs maj 0.7488', '2026-07-26'),
  ('CHI', 'GATE', 'catboost', 0.895244, 0.948002, 0.917032, 0.896685, 0.938325, 4, FALSE, 'MCC 0.6486; bal-acc 0.8079; acc 0.8728 vs maj 0.7488', '2026-07-26'),
  ('CHI', 'GATE', 'sgd_elasticnet', 0.534863, 0.759233, 0.856406, 0.748873, 1.000000, 5, FALSE, 'MCC 0.0000; bal-acc 0.5000; acc 0.7488 vs maj 0.7488', '2026-07-26'),
  ('CHI', 'VALIDATOR', 'xgboost', 0.995581, 0.990287, 0.983067, 0.972708, 0.993650, 1, TRUE, 'champion; MCC 0.9722; bal-acc 0.9879; acc 0.9866 vs maj 0.6109', '2026-07-26'),
  ('CHI', 'VALIDATOR', 'lightgbm', 0.995339, 0.989613, 0.983134, 0.972803, 0.993687, 2, FALSE, 'MCC 0.9723; bal-acc 0.9879; acc 0.9867 vs maj 0.6109', '2026-07-26'),
  ('CHI', 'VALIDATOR', 'hist_gradient_boosting', 0.995291, 0.989282, 0.983070, 0.973594, 0.992732, 3, FALSE, 'MCC 0.9722; bal-acc 0.9877; acc 0.9866 vs maj 0.6109', '2026-07-26'),
  ('CHI', 'VALIDATOR', 'catboost', 0.994735, 0.987085, 0.983450, 0.974364, 0.992708, 4, FALSE, 'MCC 0.9728; bal-acc 0.9880; acc 0.9870 vs maj 0.6109', '2026-07-26'),
  ('CHI', 'VALIDATOR', 'sgd_elasticnet', 0.986379, 0.968481, 0.976047, 0.966319, 0.985973, 5, FALSE, 'MCC 0.9606; bal-acc 0.9820; acc 0.9811 vs maj 0.6109', '2026-07-26');

INSERT INTO ps1_model_performance (city_id, device_category, model_name, algorithm, train_auc, train_ap, train_f1, val_auc, val_ap, val_f1, test_auc, test_ap, test_f1, test_prec, test_rec, decision_threshold, mlflow_version, endpoint_name, n_features, quality_gate, promoted, computed_date) VALUES
  ('CHI', 'TVM', 'chicago-ps1-3d-tvm-failure', 'hist_gradient_boosting', NULL, NULL, NULL, NULL, NULL, NULL, 0.904008, 0.983508, 0.976885, 0.960429, 0.993914, 4.948419e-01, 'v3-sklearn', 'chicago-ps1-3d-tvm-failure-v1', NULL, 'PASS', TRUE, '2026-07-26'),
  ('CHI', 'GATE', 'chicago-ps1-3d-gate-failure', 'xgboost', NULL, NULL, NULL, NULL, NULL, NULL, 0.896146, 0.948575, 0.915127, 0.893707, 0.937600, 0.648467, 'v3-sklearn', 'chicago-ps1-3d-gate-failure-v1', NULL, 'PASS', TRUE, '2026-07-26'),
  ('CHI', 'VALIDATOR', 'chicago-ps1-3d-validator-failure', 'xgboost', NULL, NULL, NULL, NULL, NULL, NULL, 0.995581, 0.990287, 0.983067, 0.972708, 0.993650, 0.604953, 'v3-sklearn', 'chicago-ps1-3d-validator-failure-v1', NULL, 'PASS', TRUE, '2026-07-26');

INSERT INTO ps1_confusion (city_id, device_category, tp, fp, tn, fn, computed_date) VALUES
  ('CHI', 'TVM', 36746, 1514, 2114, 225, '2026-07-26'),
  ('CHI', 'GATE', 50456, 6001, 12045, 3358, '2026-07-26'),
  ('CHI', 'VALIDATOR', 80119, 2248, 124346, 512, '2026-07-26');

INSERT INTO ps1_threshold_sweep (city_id, device_category, threshold, precision, recall, alert_rate, f1, computed_date) VALUES
  ('CHI', 'TVM', 0.49, 0.960429, 0.993914, 0.942388, 0.976885, '2026-07-26'),
  ('CHI', 'GATE', 0.65, 0.893707, 0.937600, 0.785653, 0.915127, '2026-07-26'),
  ('CHI', 'VALIDATOR', 0.6, 0.972708, 0.993650, 0.397476, 0.983067, '2026-07-26');

INSERT INTO ps1_inference_runs (city_id, run_id, run_ts, run_kind, device_category, scoring_date, endpoint_name, serving_image, model_version, mlflow_version, target_col, decision_threshold, n_devices_scored, n_flagged, gold_snapshot_s3, status, error_text, duration_s, computed_date) VALUES
  ('CHI', 'ps1_sklearn_20260726', TIMESTAMPTZ '2026-07-26 12:00:00+00', 'train', 'TVM', DATE '2026-07-26', 'chicago-ps1-3d-tvm-failure-v1', NULL, 'v3-sklearn', NULL, 'will_hardware_oos_3d', 4.948419e-01, 40599, 36746, 's3://cubic-mars-pm-s3-datalake-dev-gold-170202974600/chicago/gold/device_ps1_daily', 'success', 'SCORECARD ONLY. The live endpoint still serves the Spark champion spark_xgb_optuna; re-register before treating this as the serving model.', NULL, '2026-07-26'),
  ('CHI', 'ps1_sklearn_20260726', TIMESTAMPTZ '2026-07-26 12:00:00+00', 'train', 'GATE', DATE '2026-07-26', 'chicago-ps1-3d-gate-failure-v1', NULL, 'v3-sklearn', NULL, 'will_hardware_oos_3d', 0.648467, 71860, 50456, 's3://cubic-mars-pm-s3-datalake-dev-gold-170202974600/chicago/gold/device_ps1_daily', 'success', 'SCORECARD ONLY. The live endpoint still serves the Spark champion spark_xgb_optuna; re-register before treating this as the serving model.', NULL, '2026-07-26'),
  ('CHI', 'ps1_sklearn_20260726', TIMESTAMPTZ '2026-07-26 12:00:00+00', 'train', 'VALIDATOR', DATE '2026-07-26', 'chicago-ps1-3d-validator-failure-v1', NULL, 'v3-sklearn', NULL, 'will_hardware_oos_3d', 0.604953, 207225, 80119, 's3://cubic-mars-pm-s3-datalake-dev-gold-170202974600/chicago/gold/device_ps1_daily', 'success', 'SCORECARD ONLY. The live endpoint still serves the Spark champion spark_xgb_optuna; re-register before treating this as the serving model.', NULL, '2026-07-26');
