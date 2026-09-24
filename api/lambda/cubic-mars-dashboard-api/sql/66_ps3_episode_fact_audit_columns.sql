-- =====================================================================
-- PS3 episode fact: three columns the producer emits and Aurora has no
-- home for. 24-Sep-2026.
--
-- WHAT HAPPENED. The 24-Sep PRODUCTION load committed 20 of 20 tables and
-- 111,976 rows, and reported this in passing:
--
--   "columns_dropped": ["_episode_row_id", "asof_lead_days",
--                       "observed_source_max_date", "severity_definition"]
--
-- The loader matches source columns to target columns by name and DROPS
-- whatever it cannot place. That is the right default -- a producer adding a
-- column should not break a load -- but it means a column can go missing for
-- weeks without anything failing. These three appear in NO file under sql/,
-- so they have never had a home. _episode_row_id is an internal join key and
-- is correctly dropped; it is not added here.
--
-- WHY THE OTHER THREE MATTER.
--
-- severity_definition carries the literal band text, e.g.
--   "duration_band: MINOR < 60min <= MAJOR < 240min <= CRITICAL"
-- built from the configured thresholds rather than written as a literal,
-- precisely so the stated definition cannot drift from the parameter that
-- produced it. severity_status DOES load, so the tab can say a value came
-- from observed_duration_band_native -- but not what the bands were. For a
-- figure going to a transit operator that is the difference between
-- "CRITICAL" and "CRITICAL, meaning out of service four hours or more", and
-- on the 24-Sep run 69.3% of GATE episodes are CRITICAL.
--
-- asof_lead_days and observed_source_max_date are the audit pair added by
-- 71b4a6c, whose stated purpose was that "the vintage claim is auditable
-- downstream rather than resting on the gate alone". The gate works -- it is
-- what caught a run declaring 2026-08-29 over an April window. Its two
-- downstream columns never arrived. The intent was half-delivered, silently,
-- and only reading a loader log revealed it.
--
-- TYPES follow the columns already beside them in sql/45: computed_date is
-- DATE, data_freshness_days and right_censored_tail_days are BIGINT, and
-- severity_status is TEXT. observed_source_max_date is produced by
-- ps3v21_observed_source_max as a .date().isoformat(), a plain ISO date with
-- no time part, so DATE is exact rather than convenient.
--
-- ADDITIVE AND IDEMPOTENT. Three ADD COLUMN IF NOT EXISTS and their comments.
-- No column dropped, no type changed, no row rewritten. Existing rows hold
-- NULL in the new columns until the next load.
--
-- NO NOTEBOOK RE-RUN IS NEEDED to fill them. The loader does
-- DELETE FROM <table> WHERE city_id=:c and re-inserts, so re-invoking it
-- pinned to the SAME run_id repopulates every row from the same parquet
-- already in S3:
--
--   aws lambda invoke --invocation-type Event \
--     --function-name cubic-mars-ps3-v25-loader \
--     --payload '{"computed_date":"2026-08-29",
--                 "run_id":"6ab4f075-58b7-463b-b5da-cadc09cf4435"}' \
--     --cli-binary-format raw-in-base64-out /tmp/ps3load.json
--
-- Acceptance is columns_dropped shrinking from four names to one
-- (_episode_row_id), not the migration reporting applied=6.
--
-- Ship through the handler's apply_sql action, dry-run first. This file ends
-- on a statement: split_sql emits a trailing comment block as its own
-- statement and apply_sql counts anything that errors as a failure.
-- =====================================================================

-- ---------------------------------------------------------------------
-- 1. The band definition behind every native severity value.
-- ---------------------------------------------------------------------
ALTER TABLE ps3_v25_device_episode_fact
  ADD COLUMN IF NOT EXISTS severity_definition TEXT;

COMMENT ON COLUMN ps3_v25_device_episode_fact.severity_definition IS
  'How this row''s severity was derived. For a ServiceNow-linked label, '
  '"servicenow_linked_incident_severity". For a native one, the literal band '
  'text built from the run''s own thresholds, e.g. "duration_band: MINOR < '
  '60min <= MAJOR < 240min <= CRITICAL". Read with severity_status, which '
  'names the tier; this column says what the tier meant on that run.';

-- ---------------------------------------------------------------------
-- 2. The audit pair from 71b4a6c. Both describe the run, not the episode,
--    so they repeat down the table -- which is what makes them readable
--    from any single row a reviewer happens to pull.
-- ---------------------------------------------------------------------
ALTER TABLE ps3_v25_device_episode_fact
  ADD COLUMN IF NOT EXISTS observed_source_max_date DATE;

COMMENT ON COLUMN ps3_v25_device_episode_fact.observed_source_max_date IS
  'Newest episode date actually read from the source on this run. Compare '
  'against data_as_of_date: equal means the declared vintage is the observed '
  'one. It cannot detect a declared date sitting BEHIND fresher data, because '
  'the source is filtered to the declared window on the way in; that case is '
  'covered by PS3_RUN_MODE defaulting to REPLAY.';

ALTER TABLE ps3_v25_device_episode_fact
  ADD COLUMN IF NOT EXISTS asof_lead_days BIGINT;

COMMENT ON COLUMN ps3_v25_device_episode_fact.asof_lead_days IS
  'data_as_of_date minus observed_source_max_date, in days. Zero means the '
  'declared vintage matches the data. The run raises above '
  'PS3_ASOF_MAX_LEAD_DAYS (default 3) rather than publishing, so a stored '
  'value here is always within that bound -- the column exists so a reviewer '
  'can confirm that from the data instead of trusting the gate ran.';
