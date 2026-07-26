-- PS2 device-level: cascade window detail + top cascade-active devices. Runs after 01-04. Idempotent.
CREATE TABLE IF NOT EXISTS ps2_window_detail (
  city_id city_code NOT NULL REFERENCES cities(id),
  window_bucket VARCHAR(10) NOT NULL,
  cascade_days BIGINT, chain_len_mean NUMERIC(7,3), chain_len_median NUMERIC(6,2), chain_len_max INT,
  span_min_mean NUMERIC(10,3), span_min_median NUMERIC(10,3), velocity_min_per_fault NUMERIC(10,3),
  computed_date DATE NOT NULL,
  PRIMARY KEY (city_id, window_bucket, computed_date)
);
CREATE TABLE IF NOT EXISTS ps2_top_devices (
  city_id city_code NOT NULL REFERENCES cities(id),
  device_id VARCHAR(20) NOT NULL, category VARCHAR(12), cascade_days INT,
  w0_5 INT, w5_15 INT, w15_30 INT, w30_60 INT, w60plus INT, dev_rank SMALLINT,
  computed_date DATE NOT NULL,
  PRIMARY KEY (city_id, device_id, computed_date)
);
INSERT INTO ps2_window_detail (city_id,window_bucket,cascade_days,chain_len_mean,chain_len_median,chain_len_max,span_min_mean,span_min_median,velocity_min_per_fault,computed_date) VALUES
 ('CHI','0-5min',377865,3.715,2.0,484,0.436,0.000,0.091,DATE '2026-07-11'),
 ('CHI','5-15min',136893,2.471,2.0,406,12.587,12.933,11.800,DATE '2026-07-11'),
 ('CHI','15-30min',97218,2.983,2.0,340,19.540,18.533,15.824,DATE '2026-07-11'),
 ('CHI','30-60min',47479,3.956,3.0,393,43.712,42.983,22.510,DATE '2026-07-11'),
 ('CHI','60min+',1539093,9.352,7.0,3876,820.167,856.383,168.924,DATE '2026-07-11')
ON CONFLICT (city_id,window_bucket,computed_date) DO UPDATE SET cascade_days=EXCLUDED.cascade_days;
DELETE FROM ps2_top_devices WHERE city_id='CHI' AND computed_date=DATE '2026-07-11';
INSERT INTO ps2_top_devices (city_id,device_id,category,cascade_days,w0_5,w5_15,w15_30,w30_60,w60plus,dev_rank,computed_date) VALUES
 ('CHI','TVM01703','TVM',810,177,3,219,3,408,1,DATE '2026-07-11'),
 ('CHI','TVM03901','TVM',810,289,28,286,8,199,2,DATE '2026-07-11'),
 ('CHI','TVM10801','TVM',808,292,25,280,11,200,3,DATE '2026-07-11'),
 ('CHI','TVM11402','TVM',808,98,60,54,2,594,4,DATE '2026-07-11'),
 ('CHI','TVM18101','TVM',808,191,5,201,5,406,5,DATE '2026-07-11'),
 ('CHI','TVM04501','TVM',807,246,27,230,8,296,6,DATE '2026-07-11'),
 ('CHI','TVM04901','TVM',807,131,5,164,7,500,7,DATE '2026-07-11'),
 ('CHI','TVM05401','TVM',806,139,6,141,6,514,8,DATE '2026-07-11'),
 ('CHI','TVM17001','TVM',805,553,1,6,17,228,9,DATE '2026-07-11'),
 ('CHI','TVM18103','TVM',804,212,13,215,5,359,10,DATE '2026-07-11'),
 ('CHI','TVM00122','TVM',803,301,155,100,4,243,11,DATE '2026-07-11'),
 ('CHI','TVM12101','TVM',763,165,2,113,2,481,12,DATE '2026-07-11'),
 ('CHI','BMV02629','VALIDATOR',759,14,19,46,25,655,13,DATE '2026-07-11'),
 ('CHI','BMV01417','VALIDATOR',756,10,13,40,17,676,14,DATE '2026-07-11'),
 ('CHI','BMV04130','VALIDATOR',755,14,57,29,18,637,15,DATE '2026-07-11'),
 ('CHI','BMV04488','VALIDATOR',753,12,36,18,15,672,16,DATE '2026-07-11'),
 ('CHI','BMV03221','VALIDATOR',752,10,45,24,12,661,17,DATE '2026-07-11'),
 ('CHI','BMV03864','VALIDATOR',752,10,62,33,12,635,18,DATE '2026-07-11'),
 ('CHI','BMV04252','VALIDATOR',751,4,72,31,9,635,19,DATE '2026-07-11'),
 ('CHI','BMV05866','VALIDATOR',749,3,99,28,9,610,20,DATE '2026-07-11');
