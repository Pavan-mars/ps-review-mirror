-- PS5 SQL check. Leaves NOTHING behind: everything runs in a scratch schema inside one
-- transaction that ends in ROLLBACK. Safe against dev Aurora (needs public.cities with 'CHI'
-- and the city_code type from sql/01) or any Postgres with sql/01 applied.
--   psql "$RDS_URL" -f tests/ps5/check_sql.sql        (run from the repo root)
\set ON_ERROR_STOP on
BEGIN;
CREATE SCHEMA ps5_check;
SET LOCAL search_path = ps5_check, public;
\i api/lambda/cubic-mars-dashboard-api/sql/29_ps5_outputs.sql
\i api/lambda/cubic-mars-dashboard-api/sql/30_ps5_serial_rul.sql
\i api/lambda/cubic-mars-dashboard-api/sql/31_ps5_serial_rul_grain.sql
\i api/lambda/cubic-mars-dashboard-api/sql/32_ps5_serial_dedup.sql
\i api/lambda/cubic-mars-dashboard-api/sql/67_ps5_act_now_probability.sql
\i api/lambda/cubic-mars-dashboard-api/sql/67_ps5_act_now_probability.sql
\i api/lambda/cubic-mars-dashboard-api/sql/71_ps5_act_now_1d.sql
\i api/lambda/cubic-mars-dashboard-api/sql/71_ps5_act_now_1d.sql

-- G_LO has a saturated 7-day probability but a low 1-day one: act_now must follow the 1-day value.
INSERT INTO ps5_device_rul (city_id, device_type, device_id, rul_standard_days, is_overdue, p_oos_7d, p_oos_1d, feature_asof_date)
VALUES ('CHI', 'GATE', 'G_HI', 0.8, TRUE, 0.99, 0.70, '2026-08-29'),
       ('CHI', 'GATE', 'G_LO', 0.8, TRUE, 0.99, 0.20, '2026-08-29'),
       ('CHI', 'TVM',  'T_OLD', 0.8, TRUE, 0.99, NULL, '2026-04-11');
INSERT INTO ps5_serial_rul (city_id, device_type, device_id, component_serial_nbr, expected_component_rul_days, is_overdue)
VALUES ('CHI', 'GATE', 'G_HI', 'S1', 5, TRUE), ('CHI', 'GATE', 'G_HI', 'S1', 5, TRUE),
       ('CHI', 'GATE', 'G_LO', 'S2', 5, TRUE);

DO $$
DECLARE bad TEXT;
BEGIN
  SELECT string_agg(device_id || '=' || act_now, ', ') INTO bad FROM v_ps5_device_rul
   WHERE (device_id = 'G_HI' AND act_now IS NOT TRUE)
      OR (device_id IN ('G_LO', 'T_OLD') AND act_now IS NOT FALSE);
  IF bad IS NOT NULL THEN RAISE EXCEPTION 'device act_now wrong: %', bad; END IF;
  IF EXISTS (SELECT 1 FROM v_ps5_device_rul WHERE act_now_horizon_days IS DISTINCT FROM 1
                                               OR act_now_p IS DISTINCT FROM p_oos_1d) THEN
    RAISE EXCEPTION 'policy not moved to the 1-day horizon';
  END IF;

  IF (SELECT count(*) FROM v_ps5_serial_rul WHERE device_id = 'G_HI') <> 1 THEN
    RAISE EXCEPTION 'serial view not deduplicated';
  END IF;
  SELECT string_agg(component_serial_nbr || '=' || act_now, ', ') INTO bad FROM v_ps5_serial_rul
   WHERE (component_serial_nbr = 'S1' AND act_now IS NOT TRUE) OR (component_serial_nbr = 'S2' AND act_now IS NOT FALSE);
  IF bad IS NOT NULL THEN RAISE EXCEPTION 'component act_now does not follow host device: %', bad; END IF;

  UPDATE ps5_act_now_policy SET p_threshold = 0.15 WHERE city_id = 'CHI' AND device_type = 'GATE';
  IF NOT (SELECT act_now FROM v_ps5_device_rul WHERE device_id = 'G_LO') THEN
    RAISE EXCEPTION 'threshold change did not take effect';
  END IF;
  RAISE NOTICE 'PS5 SQL CHECK PASSED';
END $$;
ROLLBACK;
