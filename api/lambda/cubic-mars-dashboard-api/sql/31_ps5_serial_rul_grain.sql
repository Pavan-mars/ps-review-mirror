-- =====================================================================
-- 31_ps5_serial_rul_grain.sql   27-Jul-2026
--
-- The partial unique index added in sql/30 rejected two of three device files:
--
--   tvm         duplicate key ... ux_ps5_serial_rul_key (city_id, device_id, component_serial_nbr)
--   validators  duplicate key ... ux_ps5_serial_rul_key
--   gates       loaded fine, 1,664 rows
--
-- WHY THE INDEX IS BEING DROPPED RATHER THAN WIDENED
-- ---------------------------------------------------
-- I assumed (device_id, component_serial_nbr) was the grain of the export. Gates
-- happens to satisfy it; TVM and validators do not. I do not know what separates
-- the duplicate rows, and widening the key to a column I have guessed at would
-- either fail again or -- worse -- succeed for the wrong reason and lock in a
-- grain nobody has verified.
--
-- A uniqueness constraint is a CLAIM about the data. Asserting one that the data
-- does not support is how a load silently drops rows or a join fans out later.
-- So the constraint goes, the rows land intact, and the grain is then measured
-- from the loaded data rather than assumed -- which is what v_ps5_serial_dupes
-- below is for.
--
-- The plain index keeps the lookup performance the unique one provided.
-- =====================================================================

DROP INDEX IF EXISTS ux_ps5_serial_rul_key;

CREATE INDEX IF NOT EXISTS ix_ps5_serial_rul_dev
  ON ps5_serial_rul (city_id, device_id, component_serial_nbr);

-- Grain audit. Answers "is (device, serial) unique, and if not, what varies?"
-- straight from the loaded rows. Read this AFTER the load: if n_rows is 1
-- everywhere the export really is device x serial and the constraint can be
-- restored; if not, the columns whose distinct-count exceeds 1 are the rest of
-- the grain.
CREATE OR REPLACE VIEW v_ps5_serial_dupes AS
SELECT
  city_id,
  device_type,
  device_id,
  component_serial_nbr,
  COUNT(*)                                        AS n_rows,
  COUNT(DISTINCT component_type_name)             AS n_component_types,
  COUNT(DISTINCT risk_tier)                       AS n_risk_tiers,
  COUNT(DISTINCT feature_asof_date)               AS n_asof_dates,
  COUNT(DISTINCT serial_source)                   AS n_serial_sources,
  COUNT(DISTINCT expected_component_rul_days)     AS n_rul_values
FROM ps5_serial_rul
GROUP BY city_id, device_type, device_id, component_serial_nbr
HAVING COUNT(*) > 1;
