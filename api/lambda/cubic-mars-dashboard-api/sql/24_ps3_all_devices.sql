-- =====================================================================
-- 24_ps3_all_devices.sql  --  all three device types in the PS3 risk views
-- CUBIC MARS Chicago / CTA-Ventra
--
-- DDL ONLY (views). Applied by action=migrate. Must run AFTER sql/22
-- (v_device_serial) and sql/23 (v_ps3_category_coverage).
--
-- ---------------------------------------------------------------------
-- THE PROBLEM THESE VIEWS SOLVE
-- ---------------------------------------------------------------------
-- v_ps3_device_risk and v_ps3_serial_risk are built from ps3_device_predictions
-- and ps3_serial_predictions, so they contain ONLY the categories the PS3 run
-- modelled: TVM and GATE. Every validator in the fleet -- 3,329 devices, 6,991
-- serials, 69% of the device population -- was therefore missing from the Device
-- Risk and Component Risk tables entirely, with no row and no explanation.
--
-- These views UNION the fleet in from dim_device_serial so all three types are
-- present, and mark each row with coverage_status so the front end can render a
-- validator with its real identity and BLANK model columns.
--
-- ---------------------------------------------------------------------
-- WHY THE MODEL COLUMNS ARE NULL AND NOT ZERO
-- ---------------------------------------------------------------------
-- n_incidents is NULL for an unmodelled category, never 0. Zero would read on a
-- chart and in a sort as "this device had no failures" -- a clinical claim we
-- cannot make. The truth is weaker and different: no availability events for
-- this device type ever reach the PS3 feed, so the count is UNKNOWN, not zero.
-- NULL sorts last, aggregates out of AVG/SUM, and forces the UI to render a
-- word rather than a number. That is the intended behaviour.
--
-- pct_critical_pred, dominant_pred_severity and dominant_pred_component are NULL
-- for the same reason, and severity_shippable / rootcause_shippable are FALSE --
-- identical to how the existing views mask a head that missed its macro-F1 floor.
-- Nothing downstream has to learn a new rule.
--
-- ---------------------------------------------------------------------
-- WHAT IS REAL ON AN UNMODELLED ROW
-- ---------------------------------------------------------------------
-- Everything that comes from the hardware dimension: device_id, device category,
-- serial numbers, component descriptions and component ages. A validator row is
-- not a placeholder -- it carries genuine inventory, and only the model columns
-- are empty.
-- =====================================================================

-- ---------------------------------------------------------------------
-- Device grain. One row per device in the fleet OR in the PS3 run.
--
-- FULL OUTER JOIN, not LEFT: a device scored by PS3 but absent from the current
-- hardware dimension snapshot must still appear (TVM is 472 scored against 475
-- in the dimension, and the three-device difference is real, not rounding).
-- ---------------------------------------------------------------------
CREATE OR REPLACE VIEW v_ps3_device_all AS
WITH fleet AS (
  SELECT city_id,
         device_id,
         mars_device_category,
         COUNT(DISTINCT serial_id)                                   AS n_serials,
         COUNT(DISTINCT component_description)                       AS n_component_types,
         STRING_AGG(DISTINCT component_description, ', ')            AS components,
         ROUND(AVG(component_age_days)
               FILTER (WHERE NOT age_is_negative)::numeric, 0)       AS fleet_avg_age_days,
         MAX(as_of_date)                                             AS fleet_as_of_date
  FROM v_device_serial
  GROUP BY city_id, device_id, mars_device_category
),
modelled AS (
  SELECT device_category FROM ps3_category_coverage
  WHERE modeled AND run_id = (SELECT run_id FROM v_ps3_latest_run
                              WHERE city_id = ps3_category_coverage.city_id)
)
SELECT
  COALESCE(d.city_id, f.city_id)                             AS city_id,
  COALESCE(d.device_id, f.device_id)                         AS device_id,
  COALESCE(d.mars_device_category, f.mars_device_category)   AS mars_device_category,
  -- scored          : PS3 produced a prediction for this device
  -- not_in_feed     : this device's category emits no availability events at all
  -- in_feed_unscored: the category IS modelled but this device produced no rows
  CASE WHEN d.device_id IS NOT NULL THEN 'scored'
       WHEN f.mars_device_category IN (SELECT device_category FROM modelled)
            THEN 'in_feed_unscored'
       ELSE 'not_in_feed'
  END                                                        AS coverage_status,
  d.run_id,
  d.n_incidents,                 -- NULL when unscored. Never 0. See header.
  d.pct_critical_pred,
  d.dominant_pred_severity,
  d.dominant_pred_component,
  -- Prefer the model's own mean age where it exists so a scored row does not
  -- change value; otherwise fall back to the hardware dimension.
  COALESCE(d.avg_component_age_days, f.fleet_avg_age_days)   AS avg_component_age_days,
  d.last_incident_dtm,
  d.computed_date,
  COALESCE(d.severity_shippable,  FALSE)                     AS severity_shippable,
  COALESCE(d.rootcause_shippable, FALSE)                     AS rootcause_shippable,
  d.severity_f1_macro, d.severity_floor,
  d.rootcause_f1_macro, d.rootcause_floor,
  f.n_serials, f.n_component_types, f.components, f.fleet_as_of_date
FROM v_ps3_device_risk d
FULL OUTER JOIN fleet f
  ON f.city_id = d.city_id AND f.device_id = d.device_id;

-- ---------------------------------------------------------------------
-- Serial / component grain. One row per (device, serial).
--
-- Joined on device_id AND serial, so a scored serial keeps its model columns and
-- every other installed component appears beside it with those columns blank.
-- ---------------------------------------------------------------------
CREATE OR REPLACE VIEW v_ps3_serial_all AS
WITH modelled AS (
  SELECT device_category FROM ps3_category_coverage
  WHERE modeled AND run_id = (SELECT run_id FROM v_ps3_latest_run
                              WHERE city_id = ps3_category_coverage.city_id)
)
SELECT
  COALESCE(s.city_id, v.city_id)                             AS city_id,
  COALESCE(s.device_id, v.device_id)                         AS device_id,
  COALESCE(s.matched_serial_nbr, v.serial_id)                AS matched_serial_nbr,
  COALESCE(s.mars_device_category, v.mars_device_category)   AS mars_device_category,
  CASE WHEN s.matched_serial_nbr IS NOT NULL THEN 'scored'
       WHEN v.mars_device_category IN (SELECT device_category FROM modelled)
            THEN 'in_feed_unscored'
       ELSE 'not_in_feed'
  END                                                        AS coverage_status,
  s.run_id,
  s.n_incidents,                 -- NULL when unscored. Never 0.
  COALESCE(s.component_age_days, v.component_age_days)       AS component_age_days,
  -- The installed component's real name, which ps3_serial_predictions only
  -- carries for scored rows. This is the column that makes a validator row
  -- worth showing at all.
  v.component_description,
  v.age_is_negative,
  s.dominant_pred_component,
  s.pct_critical_pred,
  s.last_incident_dtm,
  s.computed_date,
  COALESCE(s.severity_shippable,  FALSE)                     AS severity_shippable,
  COALESCE(s.rootcause_shippable, FALSE)                     AS rootcause_shippable,
  v.as_of_date                                               AS fleet_as_of_date
FROM v_ps3_serial_risk s
FULL OUTER JOIN v_device_serial v
  ON v.city_id = s.city_id
 AND v.device_id = s.device_id
 AND v.serial_id = s.matched_serial_nbr;

-- ---------------------------------------------------------------------
-- Fleet rollup covering all three types. Feeds the Fleet Overview cards, which
-- previously rendered two cards and gave no hint a third device type existed.
--
-- n_incidents is SUM over scored rows only and comes back NULL for a category
-- with none, so a validator card shows "not in feed" where a TVM card shows a
-- count. COUNT(*) FILTER is used rather than COUNT(col) so the device totals
-- stay honest when every model column is NULL.
-- ---------------------------------------------------------------------
CREATE OR REPLACE VIEW v_ps3_rollup_all AS
SELECT a.city_id,
       a.mars_device_category                                       AS device_category,
       COUNT(*)                                                     AS n_devices,
       COUNT(*) FILTER (WHERE a.coverage_status = 'scored')         AS n_devices_scored,
       SUM(a.n_incidents)                                           AS n_incidents,
       COUNT(*) FILTER (WHERE a.pct_critical_pred IS NOT NULL)      AS n_scored,
       COUNT(*) FILTER (WHERE a.severity_shippable IS FALSE
                          AND a.coverage_status = 'scored')         AS n_gated,
       ROUND(AVG(a.pct_critical_pred)::numeric, 4)                  AS avg_pct_critical,
       ROUND(AVG(a.avg_component_age_days)::numeric, 1)             AS avg_component_age_days,
       MAX(a.last_incident_dtm)                                     AS last_incident_dtm,
       MAX(a.computed_date)                                         AS computed_date,
       SUM(a.n_serials)                                             AS n_serials,
       SUM(a.n_serials)                                             AS n_distinct_serials,
       BOOL_OR(a.coverage_status = 'scored')                        AS modelled
FROM v_ps3_device_all a
GROUP BY a.city_id, a.mars_device_category;
