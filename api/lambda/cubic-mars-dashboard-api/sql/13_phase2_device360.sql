-- Phase-2 : ServiceNow STAGING (staged only; no live post until Robin's SN API is live).
CREATE TABLE IF NOT EXISTS servicenow_staging (
  id UUID PRIMARY KEY,
  city_id city_code NOT NULL REFERENCES cities(id),
  device_id VARCHAR(30) NOT NULL,
  device_category VARCHAR(12),
  short_description VARCHAR(240),
  payload_json TEXT,
  status VARCHAR(20) NOT NULL DEFAULT 'staged',
  created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS ix_sn_stage ON servicenow_staging (city_id, created_at DESC);
