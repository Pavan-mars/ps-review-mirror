-- =====================================================================
-- 35_ps1_label_onset.sql   28-Jul-2026
--
-- THE LABEL IS A STATE, NOT AN ONSET. MEASURED, NOT ASSUMED.
--
-- From the loader's label_shape diagnostic, run against the 786,525 loaded rows:
--
--   device_type  device_days  positive  continuation  share   onsets  onset_rate
--   GATE             107,110    82,299        78,131  0.9494   4,168     3.89%
--   TVM              184,483   168,643       165,596  0.9819   3,047     1.65%
--   VALIDATOR        494,932   191,624       186,989  0.9758   4,635     0.94%
--
-- 95-98% of positive days merely FOLLOW another positive day. will_hardware_oos_3d
-- marks the DURATION of an out-of-service spell, not its arrival: a device out of
-- service for three weeks has all 21 days labelled 1, plus the 3 days before it.
--
-- A model trained on that learns "is this device currently broken" -- trivially
-- easy, and worthless for pre-emption, because the failure has already happened.
-- It is also why GATE reports recall 1.0000 with 2 false negatives out of 82,299,
-- and why GATE's precision lift is 1.006.
--
-- The spell-length distribution says the same thing from the other side. GATE has
-- 49 one-day and 14 two-day runs but 1,009 three-day runs -- a 3-day spike is
-- exactly what one isolated failure produces under a 3-day lookahead. Everything
-- past 3 days is the outage persisting, and the tail runs beyond 30 days.
--
-- WHAT THIS FILE DOES, AND WHAT IT DELIBERATELY DOES NOT
-- ------------------------------------------------------
-- It recomputes the label CORRECTLY at serving level, so the dashboard can state
-- the real event rate and measure the model against the right target today,
-- without a notebook re-run.
--
-- It does NOT fix the model. The model was TRAINED on the state label, so its
-- probabilities remain "is this device broken now" scores. Measuring them against
-- the onset label will show poor performance -- that is the honest result, not a
-- defect in this file. A model that predicts onset requires retraining with:
--   1. the label defined as the TRANSITION into a spell,
--   2. device-days already inside a spell EXCLUDED from train and score,
--   3. the threshold re-tuned against the corrected base rate.
--
-- Nothing here is destructive. ps1_cross_wired_daily is unchanged; every object
-- below is a view. If the notebooks are fixed later, these views keep working and
-- simply agree with the stored label.
--
-- GRAIN NOTE. Every window partitions by (device_id, component_serial_nbr).
-- The table is device x component x day; partitioning by device alone would
-- interleave two components' histories and invent transitions that never
-- happened. COALESCE on the serial keeps NULL-serial validator rows in their own
-- partition rather than collapsing them all into one.
-- =====================================================================

-- ---------------------------------------------------------------------------
-- Row-level: the corrected label beside the stored one.
--
--   is_onset       1 on the FIRST day of a positive run. This is the event.
--   in_spell       TRUE when the previous day was already positive -- these rows
--                  must be EXCLUDED from any evaluation, because on them the
--                  failure has already occurred and "predicting" it is hindsight.
--   spell_id       groups the days of one run, via the classic
--                  row_number-minus-row_number trick.
--   spell_day      1-based day within the run.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE VIEW v_ps1_xw_onset AS
WITH lagged AS (
  SELECT
    x.*,
    LAG(x.will_hardware_oos_3d) OVER (
      PARTITION BY x.city_id, x.device_id, COALESCE(x.component_serial_nbr, '~')
      ORDER BY x.transit_day) AS prev_label
  FROM ps1_cross_wired_daily x
),
grouped AS (
  SELECT
    l.*,
    ROW_NUMBER() OVER (PARTITION BY l.city_id, l.device_id,
                                    COALESCE(l.component_serial_nbr, '~')
                       ORDER BY l.transit_day)
    - ROW_NUMBER() OVER (PARTITION BY l.city_id, l.device_id,
                                      COALESCE(l.component_serial_nbr, '~'),
                                      l.will_hardware_oos_3d
                         ORDER BY l.transit_day) AS spell_grp
  FROM lagged l
)
SELECT
  g.*,
  CASE WHEN g.will_hardware_oos_3d = 1
        AND COALESCE(g.prev_label, 0) = 0 THEN 1 ELSE 0 END       AS is_onset,
  (g.will_hardware_oos_3d = 1 AND g.prev_label = 1)               AS in_spell,
  CASE WHEN g.will_hardware_oos_3d = 1
       THEN g.spell_grp END                                       AS spell_id,
  CASE WHEN g.will_hardware_oos_3d = 1
       THEN ROW_NUMBER() OVER (PARTITION BY g.city_id, g.device_id,
                                            COALESCE(g.component_serial_nbr, '~'),
                                            g.spell_grp, g.will_hardware_oos_3d
                               ORDER BY g.transit_day) END        AS spell_day
FROM grouped g;

-- ---------------------------------------------------------------------------
-- THE REAL BASE RATE. This is the number to quote, and the number a retrained
-- model should be evaluated against.
--
-- Two denominators, both reported, because they answer different questions:
--   onset_rate_all_days      onsets / every device-day. Comparable to the old
--                            headline rate, and shows how far off it was.
--   onset_rate_eligible      onsets / device-days where a failure COULD start,
--                            i.e. excluding days already inside a spell. This is
--                            the honest modelling base rate -- the population a
--                            predictive model is actually asked to score.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE VIEW v_ps1_xw_base_rate AS
SELECT
  city_id,
  device_type,
  COUNT(*)                                                   AS device_days,
  COUNT(*) FILTER (WHERE NOT COALESCE(in_spell, FALSE))      AS eligible_days,
  SUM(will_hardware_oos_3d)                                  AS stored_positive_days,
  SUM(is_onset)                                              AS onsets,
  -- What the stored label reports. Kept side by side ON PURPOSE: a reader who
  -- has seen the old 77% / 91% figures needs to see both to understand the
  -- correction rather than think one of them is a typo.
  ROUND(AVG(will_hardware_oos_3d::numeric), 5)               AS stored_base_rate,
  ROUND(SUM(is_onset)::numeric / NULLIF(COUNT(*), 0), 5)     AS onset_rate_all_days,
  ROUND(SUM(is_onset)::numeric
        / NULLIF(COUNT(*) FILTER (WHERE NOT COALESCE(in_spell, FALSE)), 0), 5)
                                                             AS onset_rate_eligible,
  ROUND(AVG(will_hardware_oos_3d::numeric)
        / NULLIF(SUM(is_onset)::numeric / NULLIF(COUNT(*), 0), 0), 1)
                                                             AS inflation_factor
FROM v_ps1_xw_onset
GROUP BY city_id, device_type;

-- ---------------------------------------------------------------------------
-- Performance against the CORRECT target, on the ELIGIBLE population.
--
-- Expect these numbers to be poor. The model was trained on the state label;
-- scoring it against onsets is measuring it on a task it was never given. That
-- gap is the point of this view -- it quantifies how much of the reported
-- performance was the label rather than the model.
--
-- Still no accuracy column. At a 1-4% base rate, predicting "no failure"
-- everywhere scores 96-99% and catches nothing.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE VIEW v_ps1_xw_performance_onset AS
WITH c AS (
  SELECT
    city_id, device_type,
    COUNT(*)                                                        AS n_eligible,
    SUM(is_onset)                                                   AS n_pos,
    COUNT(*) FILTER (WHERE ps1_predicted = 1 AND is_onset = 1)      AS tp,
    COUNT(*) FILTER (WHERE ps1_predicted = 1 AND is_onset = 0)      AS fp,
    COUNT(*) FILTER (WHERE ps1_predicted = 0 AND is_onset = 1)      AS fn,
    COUNT(*) FILTER (WHERE ps1_predicted = 0 AND is_onset = 0)      AS tn,
    MIN(threshold_used)                                             AS threshold_used
  FROM v_ps1_xw_onset
  WHERE NOT COALESCE(in_spell, FALSE)
    AND ps1_predicted IS NOT NULL
  GROUP BY city_id, device_type
)
SELECT
  city_id, device_type, n_eligible, n_pos, tp, fp, fn, tn, threshold_used,
  ROUND(n_pos::numeric / NULLIF(n_eligible, 0), 5)      AS base_rate,
  ROUND((tp + fp)::numeric / NULLIF(n_eligible, 0), 4)  AS flagged_share,
  ROUND(tp::numeric / NULLIF(tp + fp, 0), 4)            AS precision_at_threshold,
  ROUND(tp::numeric / NULLIF(tp + fn, 0), 4)            AS recall_at_threshold,
  ROUND(2.0 * tp / NULLIF(2 * tp + fp + fn, 0), 4)      AS f1_at_threshold,
  ROUND((tp::numeric / NULLIF(tp + fp, 0))
        / NULLIF(n_pos::numeric / NULLIF(n_eligible, 0), 0), 3) AS precision_lift
FROM c;

-- ---------------------------------------------------------------------------
-- Spell-length profile. Diagnostic, and a maintenance signal in its own right:
-- a device whose spells run 20+ days is not a prediction problem, it is a parts
-- or dispatch problem, and no model will help it.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE VIEW v_ps1_xw_spells AS
SELECT
  city_id, device_type, device_id, component_serial_nbr,
  spell_id,
  MIN(transit_day)                                      AS spell_start,
  MAX(transit_day)                                      AS spell_end,
  COUNT(*)                                              AS spell_days,
  -- A 3-day spell is one isolated failure seen through the 3-day lookahead.
  -- Anything longer is the device staying out of service.
  (COUNT(*) <= 3)                                       AS is_isolated_event,
  ROUND(AVG(ps1_fail_prob), 5)                          AS mean_prob_in_spell,
  MAX(facility_id)                                      AS facility_id
FROM v_ps1_xw_onset
WHERE will_hardware_oos_3d = 1
GROUP BY city_id, device_type, device_id, component_serial_nbr, spell_id;

-- Devices whose outages persist. Ranked by total days out, not by count, because
-- one 40-day outage costs more than four 3-day ones.
CREATE OR REPLACE VIEW v_ps1_xw_chronic_devices AS
SELECT
  city_id, device_type, device_id,
  COUNT(*)                                              AS n_spells,
  SUM(spell_days)                                       AS total_days_out,
  MAX(spell_days)                                       AS longest_spell,
  ROUND(AVG(spell_days), 1)                             AS mean_spell_days,
  COUNT(*) FILTER (WHERE is_isolated_event)             AS n_isolated,
  MAX(spell_end)                                        AS last_spell_end,
  MAX(facility_id)                                      AS facility_id
FROM v_ps1_xw_spells
GROUP BY city_id, device_type, device_id;
