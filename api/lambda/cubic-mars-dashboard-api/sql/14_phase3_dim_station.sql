-- Phase-3 full-coverage: authoritative facility_id -> station_name reference.
-- Seeded with the real facility names surfaced by the PS2 facility-contagion run.
-- Full coverage: drop the Databricks export as sql/dim_station_seed.csv (handler loads it on migrate).
CREATE TABLE IF NOT EXISTS dim_station (
  city_id      city_code    NOT NULL REFERENCES cities(id),
  facility_id  VARCHAR(20)  NOT NULL,
  station_name VARCHAR(120) NOT NULL,
  operator     VARCHAR(80),
  short_name   VARCHAR(120),
  latitude     NUMERIC(9,6),
  longitude    NUMERIC(9,6),
  source       VARCHAR(40)  DEFAULT 'ps2_run',
  loaded_at    TIMESTAMPTZ  DEFAULT NOW(),
  PRIMARY KEY (city_id, facility_id)
);

INSERT INTO dim_station (city_id, facility_id, station_name, source) VALUES
 ('CHI','4','Washington/Wells','ps2_run'),
 ('CHI','7','Repair Depot Warm Rack - CTA Bus','ps2_run'),
 ('CHI','14','CentralPark_Douglas','ps2_run'),
 ('CHI','26','North','ps2_run'),
 ('CHI','31','Northwest','ps2_run'),
 ('CHI','33','Southwest','ps2_run'),
 ('CHI','36','South','ps2_run'),
 ('CHI','38','West','ps2_run'),
 ('CHI','40','Forest Glen','ps2_run'),
 ('CHI','41','Kedzie','ps2_run'),
 ('CHI','42','Chicago','ps2_run'),
 ('CHI','45','North Park','ps2_run'),
 ('CHI','87','Roosevelt_Orange','ps2_run'),
 ('CHI','94','Midway','ps2_run'),
 ('CHI','116','King_Green','ps2_run'),
 ('CHI','121','Oakton','ps2_run'),
 ('CHI','131','Chicago_Brown','ps2_run')
ON CONFLICT (city_id, facility_id) DO UPDATE SET station_name=EXCLUDED.station_name, source=EXCLUDED.source, loaded_at=NOW();
