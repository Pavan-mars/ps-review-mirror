-- PS3 per-device (TVM/GATE) metrics by split. Runs after 01-05. Idempotent.
CREATE TABLE IF NOT EXISTS ps3_device_metrics (
  city_id city_code NOT NULL REFERENCES cities(id),
  device VARCHAR(10) NOT NULL, split VARCHAR(8) NOT NULL,
  n_incidents INT, f1_macro NUMERIC(6,4), accuracy NUMERIC(6,4),
  as_of_date DATE NOT NULL,
  PRIMARY KEY (city_id, device, split, as_of_date)
);
DELETE FROM ps3_device_metrics WHERE city_id='CHI' AND as_of_date=DATE '2026-07-13';
INSERT INTO ps3_device_metrics (city_id,device,split,n_incidents,f1_macro,accuracy,as_of_date) VALUES
 ('CHI','TVM','train',25183,0.9200,0.9220,DATE '2026-07-13'),
 ('CHI','TVM','val',3280,0.8910,0.8940,DATE '2026-07-13'),
 ('CHI','TVM','test',4454,0.8980,0.9050,DATE '2026-07-13'),
 ('CHI','Gates','train',1399,0.4980,0.9930,DATE '2026-07-13'),
 ('CHI','Gates','val',136,0.4810,0.9260,DATE '2026-07-13'),
 ('CHI','Gates','test',244,0.4930,0.9710,DATE '2026-07-13');
