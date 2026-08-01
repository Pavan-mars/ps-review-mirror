-- =====================================================================
-- 41_dim_device_bus.sql                                      29-Jul-2026
--
-- BUS IDENTITY FOR THE VALIDATOR FLEET.
--
-- Operations do not think in device ids. A crew is sent to a BUS. Until now the
-- dashboard could name a device and a component serial but not the vehicle they
-- are bolted to, which meant every dispatch needed a manual lookup.
--
-- Source: validator_device_bus_serial_map.xlsx (4,218 devices, 1,832 buses).
-- DDL only -- the 4,218 rows live in sql/load/bus_map_20260729.sql and are
-- applied by action=load_run, never by migrate().
--
-- GRAIN. One row per DEVICE_ID; the file is unique on it (4,218 of 4,218).
-- BUS_ID is NOT unique -- a bus carries more than one validator - and it is
-- NULLABLE, because spares and bench units are not fitted to a vehicle. Both
-- facts are why the primary key is the device, not the bus.
-- =====================================================================
CREATE TABLE IF NOT EXISTS dim_device_bus (
  city_id              TEXT NOT NULL,
  device_id            TEXT NOT NULL,
  device_key           TEXT,
  device_name          TEXT,
  bus_id               TEXT,
  bus_device_flag      TEXT,
  component_serial_nbr TEXT,
  component_type       TEXT,
  facility_id          TEXT,
  facility_name        TEXT,
  operator_id          TEXT,
  operator_name        TEXT,
  device_category      TEXT,
  device_type_name     TEXT,
  transit_mode_name    TEXT,
  serial_number        TEXT,
  loaded_at            TIMESTAMPTZ DEFAULT NOW(),
  PRIMARY KEY (city_id, device_id)
);
CREATE INDEX IF NOT EXISTS ix_dev_bus_bus ON dim_device_bus (city_id, bus_id);
CREATE INDEX IF NOT EXISTS ix_dev_bus_ser ON dim_device_bus (city_id, serial_number);

-- The lookup every other query should join to. bus_label is pre-computed so no
-- caller has to decide how to render a missing bus - "not assigned" beats a
-- blank cell, which reads as a data error.
CREATE OR REPLACE VIEW v_device_bus AS
SELECT city_id, device_id, bus_id, serial_number, component_serial_nbr,
       component_type, facility_id, facility_name, operator_name,
       device_category, transit_mode_name,
       COALESCE(bus_id, 'not assigned')                       AS bus_label,
       (bus_id IS NOT NULL)                                   AS on_vehicle
FROM dim_device_bus;

-- Fleet view by bus: how many validators a vehicle carries. A bus with several
-- devices failing is a vehicle problem, not a component problem, and that is a
-- different work order.
CREATE OR REPLACE VIEW v_bus_fleet AS
SELECT city_id, bus_id, operator_name,
       COUNT(*)                                  AS devices_on_bus,
       COUNT(DISTINCT serial_number)             AS distinct_serials,
       MAX(facility_name)                        AS facility_name
FROM dim_device_bus
WHERE bus_id IS NOT NULL
GROUP BY city_id, bus_id, operator_name;
