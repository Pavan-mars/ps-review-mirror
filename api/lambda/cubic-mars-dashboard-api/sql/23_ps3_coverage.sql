-- =====================================================================
-- 23_ps3_coverage.sql  --  which device types PS3 actually covers, and why
-- CUBIC MARS Chicago / CTA-Ventra
--
-- DDL ONLY. Applied by action=migrate. No INSERT lives here -- migrate() runs on
-- every deploy and a seed here would refill the table behind any purge. Rows are
-- loaded by sql/load/ps3_coverage_*.sql, generated from the run artefacts.
--
-- ---------------------------------------------------------------------
-- WHY THIS TABLE EXISTS
-- ---------------------------------------------------------------------
-- The PS3 dashboard showed nothing at all for VALIDATOR devices, and nothing on
-- screen said why. A reader could only conclude the tab was broken, or that
-- validators do not exist in the fleet. Neither is true.
--
-- PS3 is sourced from silver.incident_root_cause -- ServiceNow AVAILABILITY
-- EVENTS. Validator/BMV failures are not recorded as availability events at all;
-- they surface as device_event out-of-service 'Set' rows (the dim_event_matrix
-- OOS flags). So the PS3 feed holds 0 VALIDATOR rows BY CONSTRUCTION, not
-- because a job failed. Source: Level2/PS3/Validator/validator_ps3_stub.json,
-- written by the 26-Jul-2026 run itself, and ps3_run_summary.json which records
-- device_scope [TVM, GATE, VALIDATOR] with modeled=false for VALIDATOR.
--
-- Recording that as data rather than as a code comment means the dashboard can
-- state the coverage gap in the same breath as the numbers, every category is
-- accounted for on screen, and the day Cubic starts emitting validator
-- availability events the tab fills in without a front-end change.
-- =====================================================================

CREATE TABLE IF NOT EXISTS ps3_category_coverage (
  city_id             city_code    NOT NULL REFERENCES cities(id),
  run_id              VARCHAR(40)  NOT NULL,
  device_category     VARCHAR(12)  NOT NULL,
  -- TRUE only when a severity or root-cause head was actually trained for this
  -- category in this run. A category with modeled=FALSE must never be shown
  -- with model metrics attached to it.
  modeled             BOOLEAN      NOT NULL,
  n_ps3_incidents     BIGINT       NOT NULL DEFAULT 0,
  n_devices_scored    INT          NOT NULL DEFAULT 0,
  n_serials_scored    INT          NOT NULL DEFAULT 0,
  -- The feed PS3 reads for this category. Different per category is the whole
  -- point: TVM and GATE come from availability events, VALIDATOR would have to
  -- come from somewhere else.
  source_table        VARCHAR(160),
  -- NULL when modeled. Otherwise the reason there are zero rows, in words a
  -- client can read. NOT a stack trace and NOT "no data".
  exclusion_reason    VARCHAR(600),
  -- Where this device type IS covered today, so the gap is bounded rather than
  -- open-ended.
  alternative_coverage VARCHAR(300),
  -- What has to change upstream for PS3 to cover it.
  remediation         VARCHAR(400),
  as_of_date          DATE         NOT NULL,
  PRIMARY KEY (city_id, run_id, device_category)
);
CREATE INDEX IF NOT EXISTS idx_ps3_coverage_cat
  ON ps3_category_coverage (city_id, device_category);

-- Latest run only, joined to the fleet size so a category with no PS3 incidents
-- still shows how many devices of that type exist. n_fleet_devices comes from
-- dim_device_serial (the daily refresh), which covers all three categories --
-- that is what lets the tab say "3,329 validators in the fleet, 0 in the PS3
-- feed" instead of showing an empty panel.
CREATE OR REPLACE VIEW v_ps3_category_coverage AS
SELECT c.city_id,
       c.run_id,
       c.device_category,
       c.modeled,
       c.n_ps3_incidents,
       c.n_devices_scored,
       c.n_serials_scored,
       c.source_table,
       c.exclusion_reason,
       c.alternative_coverage,
       c.remediation,
       f.n_fleet_devices,
       f.n_fleet_serials,
       f.n_fleet_components,
       CASE WHEN f.n_fleet_devices IS NULL OR f.n_fleet_devices = 0 THEN NULL
            ELSE ROUND(c.n_devices_scored::numeric / f.n_fleet_devices, 4)
       END AS device_coverage_ratio,
       c.as_of_date
FROM ps3_category_coverage c
LEFT JOIN (
  SELECT city_id,
         mars_device_category,
         COUNT(DISTINCT device_id) AS n_fleet_devices,
         COUNT(DISTINCT serial_id) AS n_fleet_serials,
         COUNT(DISTINCT component_description) AS n_fleet_components
  FROM v_device_serial
  GROUP BY city_id, mars_device_category
) f ON f.city_id = c.city_id AND f.mars_device_category = c.device_category
WHERE c.run_id = (SELECT run_id FROM v_ps3_latest_run WHERE city_id = c.city_id);
