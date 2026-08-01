-- =====================================================================
-- 22_dim_device_serial.sql  --  the top-level device <-> serial <-> component map
-- CUBIC MARS Chicago / CTA-Ventra
--
-- Refreshed DAILY by cubic-mars-dim-loader (S3 -> Lambda in the RDS VPC ->
-- Aurora). Read by PS1, PS2, PS3, PS4 and PS5 alike, so every tab resolves a
-- serial to its device, its component type and its age from ONE table.
--
-- DDL ONLY. Applied by `action=migrate` from CloudShell. No INSERT lives here:
-- migrate() runs on every deploy and a seed here would refill the table behind
-- any purge or any daily refresh.
--
-- ---------------------------------------------------------------------
-- WHY THE EVENT COUNTS ARE NOT IN THIS TABLE
-- ---------------------------------------------------------------------
-- The source extract carries total_hardware_oos_events / total_chargeable_events
-- on EVERY serial row of a device -- the same device-level figure repeated 1..7
-- times. Summing the rows inflates the fleet total 2.60x (103,927,823 against a
-- true 39,969,550). A join to this table in any dashboard query would fan those
-- counts out again silently.
--
-- So the grain is kept clean: this table is ONE ROW PER DEVICE+SERIAL and holds
-- no additive measures at all. Device-level event totals live in
-- device_event_totals below, one row per device, where they cannot be
-- double-counted by a join.
-- =====================================================================

CREATE TABLE IF NOT EXISTS dim_device_serial (
  city_id               city_code    NOT NULL REFERENCES cities(id),
  device_id             VARCHAR(40)  NOT NULL,
  serial_id             VARCHAR(64)  NOT NULL,
  mars_device_category  VARCHAR(12),
  component_description VARCHAR(120),
  component_age_days    INT,
  -- An age below zero means the installed component was fitted AFTER the event
  -- being examined -- hw_config holds only the CURRENT configuration, so those
  -- are repair replacements, not the part that failed. Flagged rather than
  -- clamped, and excluded from age averages by the serving views.
  age_is_negative       BOOLEAN GENERATED ALWAYS AS (component_age_days < 0) STORED,
  source_table          VARCHAR(120),
  as_of_date            DATE         NOT NULL,
  PRIMARY KEY (city_id, device_id, serial_id)
);
CREATE INDEX IF NOT EXISTS idx_dim_dev_serial_dev
  ON dim_device_serial (city_id, device_id);
CREATE INDEX IF NOT EXISTS idx_dim_dev_serial_comp
  ON dim_device_serial (city_id, component_description);
CREATE INDEX IF NOT EXISTS idx_dim_dev_serial_cat
  ON dim_device_serial (city_id, mars_device_category);

-- ---------------------------------------------------------------------
-- Device-grain event totals. ONE ROW PER DEVICE -- never joined at serial
-- grain without an aggregate, or the counts fan out.
--
-- grain_note records what one "event" actually is in the source, because the
-- 27-Jul extract counts fault records rather than out-of-service occurrences:
-- the median device shows 8,136 events over a 1,015-day window (~8/day) and the
-- worst TVM 110,668 (~109/day). Those are not outages. The column is NOT NULL so
-- a future load cannot omit the caveat.
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS device_event_totals (
  city_id                   city_code    NOT NULL REFERENCES cities(id),
  device_id                 VARCHAR(40)  NOT NULL,
  mars_device_category      VARCHAR(12),
  total_hardware_oos_events BIGINT,
  total_chargeable_events   BIGINT,
  chargeable_pct            NUMERIC(7,4)
      GENERATED ALWAYS AS (
        ROUND(total_chargeable_events::numeric
              / NULLIF(total_hardware_oos_events, 0), 4)
      ) STORED,
  period_start              DATE,
  period_end                DATE,
  grain_note                VARCHAR(200) NOT NULL,
  source_table              VARCHAR(120),
  as_of_date                DATE         NOT NULL,
  PRIMARY KEY (city_id, device_id, as_of_date)
);
CREATE INDEX IF NOT EXISTS idx_device_event_totals_cat
  ON device_event_totals (city_id, mars_device_category);

-- ---------------------------------------------------------------------
-- Serving views. Latest snapshot only, so a tab never has to know the
-- refresh date.
-- ---------------------------------------------------------------------
CREATE OR REPLACE VIEW v_device_serial AS
SELECT s.city_id, s.device_id, s.serial_id, s.mars_device_category,
       s.component_description, s.component_age_days, s.age_is_negative,
       s.as_of_date
FROM dim_device_serial s
JOIN (SELECT city_id, MAX(as_of_date) AS d FROM dim_device_serial GROUP BY city_id) l
  ON l.city_id = s.city_id AND l.d = s.as_of_date;

-- Component inventory: counts and age spread per component type. Age stats
-- EXCLUDE negative ages; the count of those is reported separately rather than
-- hidden, because it is a data-quality signal in its own right.
CREATE OR REPLACE VIEW v_component_inventory AS
SELECT city_id,
       mars_device_category,
       component_description,
       COUNT(*)                                            AS n_components,
       COUNT(DISTINCT device_id)                           AS n_devices,
       ROUND(AVG(component_age_days)
             FILTER (WHERE NOT age_is_negative)::numeric, 0) AS avg_age_days,
       MIN(component_age_days) FILTER (WHERE NOT age_is_negative) AS min_age_days,
       MAX(component_age_days)                             AS max_age_days,
       COUNT(*) FILTER (WHERE age_is_negative)             AS n_replaced_after_event,
       MAX(as_of_date)                                     AS as_of_date
FROM v_device_serial
GROUP BY city_id, mars_device_category, component_description;

-- Serials per device -- the fan-out factor every serial-grain panel depends on.
CREATE OR REPLACE VIEW v_device_serial_counts AS
SELECT city_id, mars_device_category,
       COUNT(DISTINCT device_id)                                    AS n_devices,
       COUNT(*)                                                     AS n_serial_rows,
       COUNT(DISTINCT serial_id)                                    AS n_distinct_serials,
       ROUND(COUNT(*)::numeric / NULLIF(COUNT(DISTINCT device_id), 0), 2) AS serials_per_device,
       MAX(as_of_date)                                              AS as_of_date
FROM v_device_serial
GROUP BY city_id, mars_device_category;
