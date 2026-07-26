-- =============================================================================
-- gold.device_ps3_incident -- INCREMENTAL MERGE (reference)
--
-- The daily driver (ps3_daily_incremental.py, MODE=merge) builds this at runtime
-- by reading the CANONICAL sql/gold/device_ps3_incident__create.sql, bounding its
-- all_incidents CTE to new transit_days, and MERGEing on availability_event_id.
-- That keeps ONE source of truth for the 60-column feature SELECT.
--
-- This file documents the MERGE wrapper for humans / manual runs. Replace
-- <CANONICAL_SELECT> with the SELECT body from device_ps3_incident__create.sql
-- (everything after "CREATE TABLE ... AS"), with this one line added inside the
-- all_incidents CTE WHERE clause:  AND transit_day >= DATE '${since_date}'
-- =============================================================================

-- Parameter: ${since_date} = last processed transit_day (from ps3_daily_watermark)

CREATE OR REPLACE TEMP VIEW _ps3_incr AS
-- <CANONICAL_SELECT with all_incidents bounded to transit_day >= '${since_date}'>
SELECT * FROM mars_dev.gold.device_ps3_incident WHERE 1=0;   -- placeholder; driver injects the real select

MERGE INTO mars_dev.gold.device_ps3_incident t
USING _ps3_incr s
  ON t.availability_event_id = s.availability_event_id
WHEN MATCHED THEN UPDATE SET *
WHEN NOT MATCHED THEN INSERT *;

OPTIMIZE mars_dev.gold.device_ps3_incident ZORDER BY (device_id, transit_day);

-- Why availability_event_id is the MERGE key:
--   PS3 gold grain = 1 row per availability_event_id (QUALIFY ROW_NUMBER dedup in the
--   canonical build). A late-arriving ServiceNow update to an existing incident updates
--   the row; a brand-new incident inserts. No duplicates, no full rebuild.
