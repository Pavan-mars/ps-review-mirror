-- =====================================================================
-- 36_ps1_state_framing.sql   28-Jul-2026
--
-- SAY WHAT THE MODEL ACTUALLY DETECTS.
--
-- Decision taken 28-Jul: the PS1 models stay trained on will_hardware_oos_3d as
-- a STATE label, because daily batch inference over Chicago runs against the
-- models already built. That is a reasonable call -- but it means the score is
-- not a failure FORECAST. It is an out-of-service STATE detector.
--
-- Measured on the 786,525 loaded rows:
--   95-98% of positive days merely follow another positive day
--   real onset rates are GATE 3.89%, TVM 1.65%, VALIDATOR 0.94%
--   GATE flags 99.4% of device-days at a precision lift of 1.006
--
-- So a CRITICAL score overwhelmingly means "this device is in, or just past, an
-- out-of-service spell" -- and in many cases the spell has already ended and the
-- device is back in service. The dashboard has to SAY that, in the panel, not in
-- a footnote. These views make it sayable with counted numbers.
--
-- Depends on v_ps1_xw_onset from sql/35. Views only; nothing is modified.
-- =====================================================================

-- ---------------------------------------------------------------------------
-- WHY IS THE MODEL FLAGGING? Decompose every positive prediction.
--
-- This is the evidence for the framing. If continuation_share of the flags is
-- high, the score is being driven by devices that were ALREADY out of service
-- when they were scored -- duration, not onset.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE VIEW v_ps1_xw_flag_reason AS
SELECT
  city_id,
  device_type,
  COUNT(*) FILTER (WHERE ps1_predicted = 1)                       AS n_flagged,
  -- The failure had NOT yet started on this day. This is the only slice where
  -- the model is doing prediction in any useful sense.
  COUNT(*) FILTER (WHERE ps1_predicted = 1 AND is_onset = 1)      AS flag_on_onset,
  -- The device was already inside an out-of-service spell when scored.
  COUNT(*) FILTER (WHERE ps1_predicted = 1 AND in_spell)          AS flag_during_spell,
  -- Flagged with no OOS at all in the window.
  COUNT(*) FILTER (WHERE ps1_predicted = 1
                     AND will_hardware_oos_3d = 0)                AS flag_no_event,
  ROUND(COUNT(*) FILTER (WHERE ps1_predicted = 1 AND in_spell)::numeric
        / NULLIF(COUNT(*) FILTER (WHERE ps1_predicted = 1), 0), 4)
                                                                  AS share_during_spell,
  ROUND(COUNT(*) FILTER (WHERE ps1_predicted = 1 AND is_onset = 1)::numeric
        / NULLIF(COUNT(*) FILTER (WHERE ps1_predicted = 1), 0), 4)
                                                                  AS share_on_onset
FROM v_ps1_xw_onset
GROUP BY city_id, device_type;

-- ---------------------------------------------------------------------------
-- CURRENT STATE PER DEVICE, on its most recent scored day.
--
-- Four states, and the distinction that matters operationally is the last two:
--
--   IN_SPELL    still inside an out-of-service window on its latest day.
--               ACT -- the device is down now.
--   NEW_ONSET   the window opened on its latest day. ACT -- this is the event.
--   RECOVERED   flagged earlier, but its last spell has ENDED. Very often the
--               device has already been repaired. A CRITICAL tier on one of
--               these is the model describing history, not risk.
--   HEALTHY     no out-of-service window in the loaded period.
--
-- days_since_spell_end is what tells a planner whether a CRITICAL badge is worth
-- acting on. Large value + CRITICAL = stale signal.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE VIEW v_ps1_xw_device_state AS
WITH latest AS (
  SELECT DISTINCT ON (city_id, device_id)
         city_id, device_id, device_type, facility_id, transit_day,
         ps1_fail_prob, ps1_risk_tier, ps1_predicted, threshold_used,
         will_hardware_oos_3d, is_onset, in_spell, spell_day
  FROM v_ps1_xw_onset
  ORDER BY city_id, device_id, transit_day DESC
),
last_spell AS (
  SELECT city_id, device_id,
         MAX(transit_day) AS last_oos_day,
         COUNT(DISTINCT spell_id) AS n_spells,
         SUM(1) AS total_oos_days
  FROM v_ps1_xw_onset
  WHERE will_hardware_oos_3d = 1
  GROUP BY city_id, device_id
)
SELECT
  l.city_id, l.device_id, l.device_type, l.facility_id,
  l.transit_day                                        AS last_scored_day,
  l.ps1_fail_prob, l.ps1_risk_tier, l.threshold_used,
  s.last_oos_day, s.n_spells, s.total_oos_days,
  (l.transit_day - s.last_oos_day)                     AS days_since_spell_end,
  CASE
    WHEN l.is_onset = 1                THEN 'NEW_ONSET'
    WHEN l.in_spell                    THEN 'IN_SPELL'
    WHEN s.last_oos_day IS NOT NULL    THEN 'RECOVERED'
    ELSE                                    'HEALTHY'
  END                                                  AS device_state,
  l.spell_day                                          AS current_spell_day,
  -- The line a panel can print verbatim.
  CASE
    WHEN l.is_onset = 1 THEN 'Out-of-service window opened on the latest scored day'
    WHEN l.in_spell     THEN 'Currently out of service - day ' || COALESCE(l.spell_day, 0)
    WHEN s.last_oos_day IS NOT NULL
      THEN 'Back in service - last out-of-service day was '
           || (l.transit_day - s.last_oos_day) || ' day(s) before the latest score'
    ELSE 'No out-of-service window in the scored period'
  END                                                  AS state_note
FROM latest l
LEFT JOIN last_spell s ON s.city_id = l.city_id AND s.device_id = l.device_id;

-- ---------------------------------------------------------------------------
-- THE HEADLINE. Of the devices sitting at CRITICAL right now, how many are
-- actually down, and how many are already back in service?
--
-- This is the number that makes the framing concrete on screen: a CRITICAL count
-- that is mostly RECOVERED devices is a description of last week, not a work
-- queue for today.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE VIEW v_ps1_xw_state_mix AS
SELECT
  city_id, device_type, ps1_risk_tier, device_state,
  COUNT(*)                                             AS n_devices,
  ROUND(COUNT(*)::numeric
        / SUM(COUNT(*)) OVER (PARTITION BY city_id, device_type, ps1_risk_tier), 4)
                                                       AS share_of_tier,
  ROUND(AVG(ps1_fail_prob), 4)                         AS mean_prob,
  ROUND(AVG(days_since_spell_end), 1)                  AS mean_days_since_spell_end
FROM v_ps1_xw_device_state
GROUP BY city_id, device_type, ps1_risk_tier, device_state;

-- The actionable subset, and the only list that should drive dispatch: devices
-- that are down NOW or whose window opened on the latest scored day.
CREATE OR REPLACE VIEW v_ps1_xw_act_now AS
SELECT *
FROM v_ps1_xw_device_state
WHERE device_state IN ('IN_SPELL', 'NEW_ONSET');

-- ---------------------------------------------------------------------------
-- WHICH PS1 TABLES ACTUALLY HOLD ANYTHING.
--
-- Six PS1 tables are empty and have no S3 source -- the notebooks write those
-- artefacts to a local SageMaker folder that is never uploaded. They cannot be
-- dropped: migrate() recreates them with CREATE TABLE IF NOT EXISTS on every
-- deploy, so a DROP is undone by the next deploy, and the routes that read them
-- sit inside the Model Scorecard path.
--
-- So instead of hiding the gap, state it. A panel reading this view can say
-- "not populated - superseded by <x>" rather than rendering an empty chart that
-- looks like a bug.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE VIEW v_ps1_table_status AS
SELECT t.table_name, t.n_rows, t.superseded_by, (t.n_rows = 0) AS is_empty
FROM (
  VALUES
    ('ps1_cross_wired_daily',  (SELECT COUNT(*) FROM ps1_cross_wired_daily),  NULL),
    ('ps1_feature_importance', (SELECT COUNT(*) FROM ps1_feature_importance), 'v_ps1_shap_importance'),
    ('ps1_model_performance',  (SELECT COUNT(*) FROM ps1_model_performance),  'v_ps1_xw_performance'),
    ('ps1_calibration',        (SELECT COUNT(*) FROM ps1_calibration),        'v_ps1_xw_tier_calibration'),
    ('ps1_explainability',     (SELECT COUNT(*) FROM ps1_explainability),     'v_ps1_device_drivers'),
    ('ps1_features',           (SELECT COUNT(*) FROM ps1_features),           'ps1_cross_wired_daily'),
    ('ps1_failure_summary',    (SELECT COUNT(*) FROM ps1_failure_summary),    'v_ps1_xw_summary')
) AS t(table_name, n_rows, superseded_by);
