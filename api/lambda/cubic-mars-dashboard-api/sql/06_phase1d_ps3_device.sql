-- PS3 per-device (TVM/GATE) metrics by split. Runs after 01-05. Idempotent.
CREATE TABLE IF NOT EXISTS ps3_device_metrics (
  city_id city_code NOT NULL REFERENCES cities(id),
  device VARCHAR(10) NOT NULL, split VARCHAR(8) NOT NULL,
  n_incidents INT, f1_macro NUMERIC(6,4), accuracy NUMERIC(6,4),
  as_of_date DATE NOT NULL,
  PRIMARY KEY (city_id, device, split, as_of_date)
);
DELETE FROM ps3_device_metrics WHERE city_id='CHI' AND as_of_date=DATE '2026-07-13';
-- [seed INSERT INTO ps3_device_metrics moved 2026-07-26 to manual/seed_from_06_phase1d_ps3_device.sql -- migrate() runs on every deploy, so leaving
--  hardcoded 13-Jul metric rows here silently undid every purge.]