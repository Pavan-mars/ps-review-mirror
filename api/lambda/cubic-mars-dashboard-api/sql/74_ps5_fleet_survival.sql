-- 74_ps5_fleet_survival.sql                                            27-Sep-2026
--
-- Survival-curve panel. One row per fleet: the pooled Weibull the notebook
-- already fits and exports as <fleet>_device_survival_params.json. The PS5
-- loader writes it; /ps5/survival turns it into S(t) and "chance of an OOS
-- within N days of returning to service".
--
-- Also retires ps5_weibull_params and ps5_cox_hazard_ratios (sql/01): declared,
-- never written, fault_code grain the model has never produced. Nothing reads
-- them (grep of the handler and dashboard). Run the `depends` action on them
-- first; apply_sql reports a blocked drop as failed and moves on.
--
-- Idempotent.

CREATE TABLE IF NOT EXISTS ps5_fleet_survival (
  city_id            city_code     NOT NULL REFERENCES cities(id),
  device_type        VARCHAR(12)   NOT NULL,
  weibull_shape      NUMERIC(10,6) NOT NULL,
  weibull_scale      NUMERIC(14,6) NOT NULL,
  cv_cindex          NUMERIC(6,4),
  gate_pass          BOOLEAN,
  cindex_floor       NUMERIC(4,2),
  run_date           DATE,
  event_def_version  VARCHAR(40),
  loaded_at          TIMESTAMPTZ   NOT NULL DEFAULT now(),
  PRIMARY KEY (city_id, device_type)
);

DROP TABLE IF EXISTS ps5_cox_hazard_ratios;
DROP TABLE IF EXISTS ps5_weibull_params;
