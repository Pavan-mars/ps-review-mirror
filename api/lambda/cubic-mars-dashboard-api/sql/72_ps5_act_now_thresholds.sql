-- 72_ps5_act_now_thresholds.sql                                       27-Sep-2026
--
-- Per-fleet act_now thresholds on the 1-day probability (sql/71), set from the
-- 27-Sep load (after the p_event_within underflow fix), at about each fleet's p90
-- so act_now is roughly the top 10% work list:
--   GATE       p90 0.493  p95 0.515  -> 0.49  (>= 0.50 would flag 70 of 860)
--   TVM        p90 0.668  p95 0.714  -> 0.66  (>= 0.70 would flag 27 of 450)
--   VALIDATOR  p90 0.514  p95 0.523  -> 0.51  (below the 0.65 C-index floor;
--              spread is narrow, so treat the list as a ranking, not a forecast)
-- Re-check after each refit: counts per threshold come from /ps5/device-rul.
-- Idempotent.

UPDATE ps5_act_now_policy SET p_threshold = 0.49, horizon_days = 1, updated_at = now(),
       note = 'sql/72 27-Sep-2026: ~p90 of p_oos_1d (0.493); ~10% of gates'
 WHERE city_id = 'CHI' AND device_type = 'GATE';

UPDATE ps5_act_now_policy SET p_threshold = 0.66, horizon_days = 1, updated_at = now(),
       note = 'sql/72 27-Sep-2026: ~p90 of p_oos_1d (0.668); ~10% of TVMs'
 WHERE city_id = 'CHI' AND device_type = 'TVM';

UPDATE ps5_act_now_policy SET p_threshold = 0.51, horizon_days = 1, updated_at = now(),
       note = 'sql/72 27-Sep-2026: ~p90 of p_oos_1d (0.514); model below C-index floor, ranking only'
 WHERE city_id = 'CHI' AND device_type = 'VALIDATOR';
