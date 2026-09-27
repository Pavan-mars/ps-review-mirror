-- =====================================================================
-- 42_ps2_serial_grain.sql                                  01-Aug-2026
--
-- TWO tables. Nothing is dropped, altered or retired.
--
-- WHY THESE TWO EXIST
--   The PS2 loader refused two of the 27 exported tables on 01-Aug-2026:
--     ps2_ignition_termination_subsystem  2/10 columns matched (20%)
--     ps2_facility_contagion_facility     3/9  columns matched (33%)
--   The refusal was correct but the diagnosis is not "bad data". Both were
--   ALIASED onto device-grain tables that hold a different thing:
--     ps2_ignition_termination_subsystem -> ps2_ignition_termination
--         target wants ignition_DAYS + a derived rank/pct/net_role;
--         the source carries ignition_COUNT. Counts are not days.
--     ps2_facility_contagion_facility    -> ps2_facility_contagion_summary
--         target is a ONE-ROW rollup (hotspot_facility_id, trend_start_pct);
--         the source is PER-FACILITY detail. Different shapes entirely.
--   Both sources are the serial-grain run's own tables, and the front end
--   already asks for exactly their columns:
--     IgnitionTerminationSubsystemPanel  -> subsystem, ignition_count,
--                                           termination_count
--     FacilityContagionPanel             -> facility_id, cascade_days,
--                                           distinct_devices, contagion_rate
--   So the fix is to give them their own tables under their own names and
--   remove the two aliases. The device-grain tables they were pointed at are
--   untouched and keep serving /ps2/ignition and /ps2/facility.
--
-- COLUMN NAMES ARE THE SOURCE'S OWN, NOT RENAMED. A rename here would put
-- event counts under a column called "days" and print a wrong number on a
-- client screen. The panel labels them "Raw ignition/termination event
-- counts" for that reason.
--
-- PK is a surrogate BIGSERIAL. No natural unique constraint: the loader does
-- DELETE-by-city then INSERT, so a natural key buys nothing and a wrong one
-- costs a 23505 that rolls back every table staged before it.
-- =====================================================================

CREATE TABLE IF NOT EXISTS ps2_ignition_termination_subsystem (
  id                 BIGSERIAL PRIMARY KEY,
  city_id            city_code   NOT NULL REFERENCES cities(id),
  subsystem          VARCHAR(60),
  ignition_count     BIGINT,
  termination_count  BIGINT,
  grain              VARCHAR(20),
  run_id             VARCHAR(80),
  notebook_version   VARCHAR(40),
  computed_date      DATE
);

CREATE INDEX IF NOT EXISTS ix_ps2_ign_term_sub_city_date
  ON ps2_ignition_termination_subsystem (city_id, computed_date DESC);

-- (27-Sep-2026) ps2_facility_contagion_facility statement removed -- retired (sql/62, 63, 75); no reader.


-- (27-Sep-2026) ps2_facility_contagion_facility statement removed -- retired (sql/62, 63, 75); no reader.


-- Read-side convenience only. The API selects the tables directly; these
-- exist so the same "newest computed_date only" rule can be checked by hand
-- in psql without retyping the correlated subquery.
CREATE OR REPLACE VIEW v_ps2_ignition_termination_subsystem AS
SELECT subsystem, ignition_count, termination_count, grain, run_id,
       computed_date, city_id
FROM ps2_ignition_termination_subsystem t
WHERE computed_date = (SELECT MAX(computed_date)
                       FROM ps2_ignition_termination_subsystem
                       WHERE city_id = t.city_id);

-- (27-Sep-2026) ps2_facility_contagion_facility statement removed -- retired (sql/62, 63, 75); no reader.

