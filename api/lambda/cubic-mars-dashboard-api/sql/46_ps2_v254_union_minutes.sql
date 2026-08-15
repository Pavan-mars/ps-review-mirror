-- =====================================================================
-- 46_ps2_v254_union_minutes.sql
--
-- PS2 v2.5.4 adds wall-clock device downtime beside the existing episode sum.
--
-- WHY. hardware_oos_minutes is sum(outage_duration_min) across episodes. A
-- device is routinely in several concurrent OOS episodes, so that sum counts
-- the same wall-clock minute once per concurrent episode. Measured on the
-- 210-day window: gates 4.63x, TVMs 2.53x, validators 2.05x, with 51-78% of the
-- summed minutes double counted. The published validator figure implied 22.9
-- out-of-service hours per device per day.
--
-- WHAT THIS DOES NOT DO. It does not drop or redefine hardware_oos_minutes.
-- Summing episode durations remains the correct measure of COMPONENT burden --
-- two components failing together did cost two component-hours even though the
-- device was down once. Only availability needs the union. Both are stored, so
-- the dashboard can show device downtime honestly and component burden
-- separately, and the ratio makes the difference visible.
--
-- ADDITIVE AND IDEMPOTENT. Every statement is ADD COLUMN IF NOT EXISTS. No
-- column is dropped, no type is changed, no row is touched. Existing rows keep
-- NULL in the new columns until the v2.5.4 loader run fills them, so nothing
-- that reads the old columns breaks in the meantime.
-- =====================================================================

ALTER TABLE ps2_v2_device_deterioration
  ADD COLUMN IF NOT EXISTS "hardware_oos_union_minutes"            DOUBLE PRECISION,
  ADD COLUMN IF NOT EXISTS "hardware_oos_onsets_distinct_interval" BIGINT,
  ADD COLUMN IF NOT EXISTS "oos_minutes_overlap_factor"            DOUBLE PRECISION;

ALTER TABLE ps2_v2_daily_oos_trend
  ADD COLUMN IF NOT EXISTS "hardware_oos_union_minutes"            DOUBLE PRECISION,
  ADD COLUMN IF NOT EXISTS "hardware_oos_onsets_distinct_interval" BIGINT,
  ADD COLUMN IF NOT EXISTS "oos_minutes_overlap_factor"            DOUBLE PRECISION;

COMMENT ON COLUMN ps2_v2_device_deterioration."hardware_oos_minutes" IS
  'Sum of episode durations. Counts concurrent episodes separately, so it is component burden, NOT device downtime. Use hardware_oos_union_minutes for availability.';
COMMENT ON COLUMN ps2_v2_device_deterioration."hardware_oos_union_minutes" IS
  'Wall-clock minutes the device was out of service: the union of its OOS intervals, split across calendar days. Bounded by 1440 per device-day by construction.';
COMMENT ON COLUMN ps2_v2_device_deterioration."hardware_oos_onsets_distinct_interval" IS
  'Onsets counted on the distinct (outage_start, duration) interval rather than the source event id. The source carries near-duplicate outage records that differ only by id: 55.4% of TVM rows, and 98.7% of duplicated GATE intervals are otherwise indistinguishable.';
COMMENT ON COLUMN ps2_v2_device_deterioration."oos_minutes_overlap_factor" IS
  'hardware_oos_minutes / hardware_oos_union_minutes. 1.0 means no concurrency. Measured fleet medians on the 2026-04-11 window: GATE 4.63, TVM 2.53, VALIDATOR 2.05.';

COMMENT ON COLUMN ps2_v2_daily_oos_trend."hardware_oos_union_minutes" IS
  'Wall-clock device-minutes out of service for the fleet on that date: the sum over devices of each device''s own union. Not a sum of episode durations.';
