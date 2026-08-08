-- =====================================================================
-- 48_ps1_state_evaluable_day.sql                            04-Aug-2026
--
-- READ DEVICE STATE ON A DAY THE LABEL CAN ACTUALLY FIRE ON.
--
-- REVISION 2, same day, after the first version went live and was measured
-- against the deployed API. Revision 1 fixed the defect it set out to fix
-- and introduced two of its own. Both are corrected here and both are
-- described below, because the next person to touch this file needs to
-- know which parts were verified and which were assumed.
--
-- ---------------------------------------------------------------------
-- THE ORIGINAL DEFECT
-- ---------------------------------------------------------------------
-- v_ps1_xw_device_state reported IN_SPELL = 0 and NEW_ONSET = 0 for ALL
-- 4,217 devices. sql/36 builds the state from `latest`:
--
--     SELECT DISTINCT ON (city_id, device_id) ... ORDER BY transit_day DESC
--
-- -- the LAST SCORED DAY -- then reads is_onset / in_spell off it. Both
-- derive from will_hardware_oos_3d, a three-day lookahead: true on day D
-- when an OOS event falls in D+1..D+3. Near the end of the window there is
-- progressively less future to look at, so the label stops firing and every
-- device is forced into RECOVERED or HEALTHY.
--
-- This is why BMV02633's Device 360 page said HEALTHY the day after it went
-- out of service, while PS5 ranked it the most urgent validator in the
-- estate.
--
-- MEASURED, NOT ASSUMED: revision 1 asserted the label "structurally cannot
-- fire" in the last three days. The live data says that is too strong.
-- BMV03349 has last_oos_day = 2026-04-09, two days before the window ends
-- on 2026-04-11. The label build sees some events past the last scored day,
-- so the dead zone is real but ragged, not a clean three-day cliff. The
-- fix below does not depend on which it is; the claim has been corrected
-- because a wrong reason in a comment outlives the code it explains.
--
-- ---------------------------------------------------------------------
-- WHAT REVISION 1 BROKE, AND HOW IT WAS CAUGHT
-- ---------------------------------------------------------------------
-- (a) FAN-OUT. Revision 1 selected the evaluable day with a plain filter:
--
--         FROM v_ps1_xw_onset o JOIN frame f ON o.transit_day = f.evaluable_day
--
--     v_ps1_xw_onset is NOT unique on (city_id, device_id, transit_day) --
--     ps1_cross_wired_daily carries repeated rows per device-day. sql/36's
--     DISTINCT ON collapsed them silently; the plain filter did not.
--     /ps1/xw-act-now returned 100 rows for 49 distinct devices, three
--     identical rows each, and the state mix summed to 8,639 devices
--     against a fleet of 4,217. An overstated fleet is worse than the
--     original bug: the original hid work, this invented it.
--     FIX: DISTINCT ON (city_id, device_id) with a deterministic tie-break.
--
-- (b) NEGATIVE days_since_spell_end. 4,539 devices carried a mean of about
--     -1.6 "days since the spell ended" -- meaningless on its face, and
--     only ever on IN_SPELL and NEW_ONSET rows.
--     Two causes, both real: a device still IN a spell has no spell end to
--     count from, and last_oos_day is a whole-window maximum that can fall
--     AFTER the evaluable day (BMV03349 again: last_oos_day 09 Apr,
--     evaluable_day 08 Apr).
--     FIX: count from the last positive day AT OR BEFORE the evaluable day,
--     and return NULL -- not zero -- while the device is in a spell. Zero
--     would read as "the spell ended today", which is a different and
--     false claim.
--
-- (c) total_oos_days was SUM(1) over the same duplicated rows, so it was
--     inflated by the same factor. It is now COUNT(DISTINCT transit_day).
--     This defect predates revision 1 -- it is in sql/36 -- and is fixed
--     here rather than left, because the duplication that caused it is now
--     understood.
--
-- ---------------------------------------------------------------------
-- THE COLUMN LIST IS EXACTLY sql/36'S FIFTEEN, IN ORDER. THIS MATTERS.
-- ---------------------------------------------------------------------
-- Revision 1 appended three columns. That is legal for one CREATE OR
-- REPLACE, but migrate() re-runs EVERY file on EVERY deploy, and sql/36
-- runs BEFORE sql/48. On the next deploy sql/36 would have tried to replace
-- an 18-column view with its own 15-column definition and failed with
--     42P16  cannot drop columns from view
-- -- the same error sql/17 has been throwing for weeks. sql/48 would then
-- have repaired it, so the deploy would still have "worked" while reporting
-- a new permanent failure. That is how a migration list rots.
--
-- So this file changes the DEFINITION and not the CONTRACT. Nothing is
-- added. The two things revision 1 appended that were actually useful --
-- the evaluable day and the horizon -- are published on v_ps1_label_frame
-- below, which a screen can read directly.
--
-- Idempotent. Views only, no data movement. Reverting is removing this file
-- from the tuple in handler.py and redeploying; sql/36 then wins again.
-- =====================================================================

-- ---------------------------------------------------------------------
-- The label frame. Published separately so a screen can show BOTH dates:
-- "scored to 11 Apr, state read at 8 Apr" is understood instantly, whereas
-- a silently shifted date just looks like stale data.
--
-- The horizon is 3 because the label is named will_hardware_oos_3d. It is
-- written once, here.
--
-- Derived PER CITY FROM THE DATA, never from CURRENT_DATE: these runs are
-- replays of an extract ending 11 Apr 2026, so anchoring to wall-clock time
-- would make the view return nothing at all.
-- ---------------------------------------------------------------------
CREATE OR REPLACE VIEW v_ps1_label_frame AS
SELECT
    city_id,
    MIN(transit_day)                    AS first_scored_day,
    MAX(transit_day)                    AS last_scored_day,
    3                                   AS label_horizon_days,
    MAX(transit_day) - 3                AS evaluable_day,
    COUNT(DISTINCT transit_day)         AS n_scored_days
FROM v_ps1_xw_onset
GROUP BY city_id;

COMMENT ON VIEW v_ps1_label_frame IS
  'PS1 scoring window and the last day on which will_hardware_oos_3d can '
  'fire. evaluable_day = last_scored_day - label_horizon_days. Device state '
  'is read on evaluable_day: read on last_scored_day, the 3-day lookahead '
  'has little or no future to see and IN_SPELL/NEW_ONSET collapse to zero.';

-- ---------------------------------------------------------------------
-- DROP AND RECREATE, NOT CREATE OR REPLACE. Revision 3, and this is the
-- reason for it.
--
-- Revision 1 shipped an 18-column view. Revision 2 went back to sql/36's
-- 15 -- correct as a contract, but CREATE OR REPLACE CANNOT NARROW a view
-- that is already live, so revision 2 failed on deploy with
--     42P16  cannot drop columns from view
-- and so did sql/36, for the same reason. The database kept revision 1's
-- broken definition and the deploy reported success at the top.
--
-- A column contract that is right in the file and unreachable in the
-- database is worth nothing. So the three views are dropped in dependency
-- order and rebuilt. v_ps1_xw_state_mix and v_ps1_xw_act_now are recreated
-- verbatim from sql/36 -- they are dropped only because Postgres will not
-- drop a view out from under its dependents, not because they change.
--
-- After this deploy the database holds 15 columns again, so sql/36's own
-- CREATE OR REPLACE succeeds on every future deploy and this file simply
-- redefines the body. The failure is self-clearing, once.
-- ---------------------------------------------------------------------
DROP VIEW IF EXISTS v_ps1_xw_act_now;
DROP VIEW IF EXISTS v_ps1_xw_state_mix;
DROP VIEW IF EXISTS v_ps1_xw_device_state;

-- ---------------------------------------------------------------------
-- Device state, evaluated on the last evaluable day.
-- Fifteen columns, sql/36's names, types and order exactly.
-- ---------------------------------------------------------------------
CREATE VIEW v_ps1_xw_device_state AS
-- ONE PASS OVER v_ps1_xw_onset. Revision 4, and the reason is measured.
--
-- Revision 3 was correct and slow. It referenced v_ps1_xw_onset THREE times
-- (evaluable, roster, last_spell) where sql/36 referenced it twice, and
-- v_ps1_xw_onset is a window-function view over 786,525 rows. The routes
-- built on it went to 28-30 seconds against API Gateway's 30s hard cap, so
-- /ps1/xw-state-mix and /ps1/xw-act-now returned 503 on roughly half of
-- calls -- a screen that loads or does not depending on the roll.
--
-- Isolated before rewriting, not guessed: /ps1/xw-chronic reads the same
-- base view and returns in 5.9s, /ps1/xw-tiers in 1.4s. The base view was
-- not the cost. This view was.
--
-- So everything is computed in a SINGLE grouped scan. The FILTER aggregates
-- do three jobs at once:
--   * pick the evaluable-day row     FILTER (WHERE transit_day = evaluable_day)
--   * collapse its duplicates        MAX() over identical rows is the row
--   * carry the whole-window history FILTER (WHERE will_hardware_oos_3d = 1)
-- MAX() is what removes the fan-out that revision 1 shipped: where a device
-- has several identical rows on the evaluable day, MAX returns one value,
-- not one row each.
WITH frame AS (
  SELECT city_id, evaluable_day, last_scored_day
  FROM v_ps1_label_frame
),
agg AS (
  SELECT
    o.city_id,
    o.device_id,
    f.last_scored_day,
    f.evaluable_day,
    -- (array_agg(x))[1], NOT MAX(x).                          04-Aug-2026
    -- MAX() on a varchar returns TEXT. sql/36 declares device_type as
    -- varchar(12), so its CREATE OR REPLACE then failed with
    --     42P16 cannot change data type of view column "device_type"
    --           from text to character varying(12)
    -- -- the same family of failure as the column-count one above, and just
    -- as invisible: the column LIST matched, only the TYPE had drifted.
    -- array_agg preserves the source column's exact type; MAX does not.
    -- ::varchar(12) EXPLICITLY.                              04-Aug-2026
    -- array_agg preserved "varchar" but dropped the LENGTH MODIFIER, so the
    -- error moved from "text -> varchar(12)" to "varchar -> varchar(12)".
    -- A view column's type includes its modifier; CREATE OR REPLACE compares
    -- both. sql/36 declares varchar(12), so this states varchar(12).
    (array_agg(o.device_type))[1]::varchar(12)                            AS device_type,
    -- facility_id is varchar(40) in the source ps1_cross_wired_daily. It is
    -- declared with FIVE different widths across the sql/ tree (20, 24, 40,
    -- 120, TEXT), so the modifier here is taken from the error Postgres
    -- itself reported rather than from whichever DDL was read first.
    (array_agg(o.facility_id))[1]::varchar(40)                            AS facility_id,
    -- The evaluable-day row.
    MAX(o.ps1_fail_prob)  FILTER (WHERE o.transit_day = f.evaluable_day)  AS eval_prob,
    (array_agg(o.ps1_risk_tier) FILTER (WHERE o.transit_day = f.evaluable_day))[1]::varchar(20) AS eval_tier,
    MAX(o.threshold_used) FILTER (WHERE o.transit_day = f.evaluable_day)  AS eval_thr,
    MAX(o.is_onset)       FILTER (WHERE o.transit_day = f.evaluable_day)  AS eval_is_onset,
    BOOL_OR(o.in_spell)   FILTER (WHERE o.transit_day = f.evaluable_day)  AS eval_in_spell,
    MAX(o.spell_day)      FILTER (WHERE o.transit_day = f.evaluable_day)  AS eval_spell_day,
    -- Fallback for a device not scored on the evaluable day, so it still
    -- appears rather than vanishing from the work list.
    MAX(o.ps1_fail_prob)  FILTER (WHERE o.transit_day = f.last_scored_day) AS last_prob,
    (array_agg(o.ps1_risk_tier) FILTER (WHERE o.transit_day = f.last_scored_day))[1]::varchar(20) AS last_tier,
    MAX(o.threshold_used) FILTER (WHERE o.transit_day = f.last_scored_day) AS last_thr,
    -- Whole-window history.
    MAX(o.transit_day)            FILTER (WHERE o.will_hardware_oos_3d = 1) AS last_oos_day,
    MAX(o.transit_day)            FILTER (WHERE o.will_hardware_oos_3d = 1
                                            AND o.transit_day <= f.evaluable_day)
                                                                            AS last_oos_day_at_eval,
    COUNT(DISTINCT o.spell_id)    FILTER (WHERE o.will_hardware_oos_3d = 1) AS n_spells,
    -- DISTINCT transit_day, not COUNT(*): the duplicate device-day rows
    -- inflate a plain count. This defect is inherited from sql/36.
    COUNT(DISTINCT o.transit_day) FILTER (WHERE o.will_hardware_oos_3d = 1) AS total_oos_days
  FROM v_ps1_xw_onset o
  JOIN frame f ON f.city_id = o.city_id
  GROUP BY o.city_id, o.device_id, f.last_scored_day, f.evaluable_day
)
SELECT
  city_id,
  device_id,
  device_type,
  facility_id,
  last_scored_day,
  COALESCE(eval_prob, last_prob)                       AS ps1_fail_prob,
  COALESCE(eval_tier, last_tier)                       AS ps1_risk_tier,
  COALESCE(eval_thr,  last_thr)                        AS threshold_used,
  last_oos_day,
  -- NULLIF(...,0): a FILTER count returns 0 where sql/36's LEFT JOIN returned
  -- NULL. "Zero spells recorded" and "no spell history" print differently.
  NULLIF(n_spells, 0)                                  AS n_spells,
  NULLIF(total_oos_days, 0)                            AS total_oos_days,
  -- NULL, not 0, while in a spell: 0 would read as "the spell ended today".
  CASE
    WHEN eval_is_onset = 1 OR eval_in_spell THEN NULL
    WHEN last_oos_day_at_eval IS NULL       THEN NULL
    ELSE (evaluable_day - last_oos_day_at_eval)
  END                                                  AS days_since_spell_end,
  CASE
    WHEN eval_is_onset = 1        THEN 'NEW_ONSET'
    WHEN eval_in_spell            THEN 'IN_SPELL'
    WHEN last_oos_day IS NOT NULL THEN 'RECOVERED'
    ELSE                               'HEALTHY'
  END                                                  AS device_state,
  eval_spell_day                                       AS current_spell_day,
  CASE
    WHEN eval_is_onset = 1 THEN 'Out-of-service window opened on the last evaluable day'
    WHEN eval_in_spell     THEN 'Out of service - day ' || COALESCE(eval_spell_day, 0)
                                || ' as at the last evaluable day'
    WHEN last_oos_day_at_eval IS NOT NULL
      THEN 'Back in service - last out-of-service day was '
           || (evaluable_day - last_oos_day_at_eval)
           || ' day(s) before the last evaluable day'
    WHEN last_oos_day IS NOT NULL
      THEN 'Out-of-service days recorded only after the last evaluable day'
    ELSE 'No out-of-service window in the scored period'
  END                                                  AS state_note
FROM agg;


COMMENT ON VIEW v_ps1_xw_device_state IS
  'PS1 device state read on the last EVALUABLE day (last_scored_day minus '
  'the 3-day label horizon), superseding sql/36. One row per device: '
  'v_ps1_xw_onset is not unique on (device, day) and a plain filter fans '
  'out. days_since_spell_end is NULL while the device is in a spell and is '
  'counted from the last positive day at or before the evaluable day. Does '
  'NOT address the validator label-coverage gap -- see the note in sql/48.';

-- ---------------------------------------------------------------------
-- The two dependents, recreated VERBATIM from sql/36. Nothing about them
-- changes; they exist here only because Postgres will not drop a view out
-- from under the views that select from it.
--
-- Keep these byte-identical to sql/36's definitions. If they ever diverge,
-- whichever file ran last silently wins and the two will disagree about
-- what a work list is.
-- ---------------------------------------------------------------------
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

CREATE OR REPLACE VIEW v_ps1_xw_act_now AS
SELECT *
FROM v_ps1_xw_device_state
WHERE device_state IN ('IN_SPELL', 'NEW_ONSET');

-- =====================================================================
-- WHAT THIS DOES NOT DO, AND MUST NOT BE READ AS HAVING DONE
-- =====================================================================
-- It does not close the validator gap. PS1 reports a large share of
-- validators as never having been out of service, while PS5 publishes
-- remaining life for 3,235 validators and marks 2,504 past expected life.
-- Both cannot describe the same fleet.
--
-- That gap is NOT the horizon artefact and this file will not move it: a
-- device with no positive label day anywhere in the window is HEALTHY under
-- either framing. The evidence points upstream, to what PS1's label build
-- counts. The governed contract is
--
--     is_hardware_oos_event = TRUE
--     AND UPPER(TRIM(EVENT_STATE_TYPE_NAME)) = 'SET'
--
-- and PS2, applying that same contract, finds 298,703 validator device-days
-- that PS1's label does not have -- against ZERO in the other direction
-- (jaccard 0.356, PS1-only 0, PS2-only 298,703). A strict subset in one
-- direction is a filter, not noise. The likely culprit is the chargeable /
-- ServiceNow narrowing removed from PS3 and never removed here.
--
-- PK's instruction, 04-Aug: DISCLOSE, DO NOT CORRECT. Widening a label
-- inside a view would move every PS1 number on the estate as a side-effect
-- of a state-framing fix, with nothing on screen to show it happened.
--
-- The reconciliation query, so the question has one agreed form:
--
--   WITH contract AS (
--       SELECT device_id, COUNT(DISTINCT transit_day) AS contract_oos_days
--       FROM device_event_enriched
--       WHERE is_hardware_oos_event = TRUE
--         AND UPPER(TRIM(EVENT_STATE_TYPE_NAME)) = 'SET'
--       GROUP BY device_id),
--   ps1 AS (
--       SELECT device_id,
--              COUNT(DISTINCT transit_day) FILTER (WHERE will_hardware_oos_3d = 1)
--                                                        AS ps1_positive_days
--       FROM v_ps1_xw_onset
--       GROUP BY device_id)
--   SELECT d.mars_device_category,
--          COUNT(*)                                                   AS devices,
--          SUM(COALESCE(c.contract_oos_days, 0))                      AS contract_days,
--          SUM(COALESCE(p.ps1_positive_days, 0))                      AS ps1_days,
--          COUNT(*) FILTER (WHERE COALESCE(c.contract_oos_days, 0) > 0
--                             AND COALESCE(p.ps1_positive_days, 0) = 0)
--                                                                     AS contract_only_devices
--   FROM dim_device d
--   LEFT JOIN contract c ON c.device_id = d.device_id
--   LEFT JOIN ps1      p ON p.device_id = d.device_id
--   WHERE d.is_current = TRUE
--   GROUP BY d.mars_device_category
--   ORDER BY 1;
--
-- contract_only_devices decides it. Large and one-sided for VALIDATOR means
-- the gap is a filter in the PS1 label build, and the fix belongs there --
-- not in this view, and not in the dashboard.
-- =====================================================================
