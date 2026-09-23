-- =====================================================================
-- PS2 producer-defect batch -- the columns the corrected notebook emits.
-- 23-Sep-2026
--
-- WHY THIS FILE EXISTS. Five arithmetic defects were fixed in
-- PS2_Serial_Grain_Analysis_v1_FIXED.ipynb in one batch. Three of them add a
-- column to a published table. The loader keeps only columns that already
-- exist on the target -- handler.py:444, `use = [x for x in tcols if x in
-- src_cols]` -- and reports the rest as unused_from_source WITHOUT refusing
-- the load, because coverage stays above MIN_MATCH. So a new column would
-- land in S3 and in the local CSVs, and silently never reach Aurora.
-- This migration must therefore be applied BEFORE the next producer run.
--
-- ADDITIVE AND IDEMPOTENT. Every statement is ADD COLUMN IF NOT EXISTS or a
-- CREATE OR REPLACE VIEW that only appends columns at the end (Postgres
-- permits no other change through REPLACE). No column is dropped, no type is
-- changed, no row is touched. contagion_rate is KEPT even though the producer
-- stops emitting it, because the 29-Aug rows hold it and dropping it would
-- destroy them.
--
-- SHIP IT through the dashboard-api handler's `apply_sql` action, dry-run
-- first. Do NOT add it to the migrate() tuple, which is frozen at sql/48 by
-- design (AURORA RULE R1).
--
-- NOT IN THIS FILE, deliberately:
--   * ps2_cascade_velocity_by_age_serial also gains n_zero_span_excluded, but
--     that table has no CREATE TABLE anywhere in the repo -- it exists in
--     Aurora only because the retired cubic-mars-ps2-rds-push auto-created it
--     from parquet. It belongs to the undeclared-tables batch, which has to
--     write the whole DDL rather than ALTER something the repo never defined.
--   * ps2_phi_matrix and ps2_network_centrality need no schema change: the
--     phi overflow and the betweenness inversion are value defects, so a
--     corrected producer run fixes them in place.
--
-- This file ends on a statement, not on a comment: split_sql() emits a
-- trailing comment block as its own "statement" and the apply loop counts
-- anything that errors as a failure, which would turn the whole call 500.
-- =====================================================================

-- ---------------------------------------------------------------------
-- 1. Cascade velocity -- how many chains were excluded for a zero span.
--
-- The velocity expression was the reciprocal of its own name: chain_length /
-- chain_span_min is FAULTS PER MINUTE, published as mean_velocity_min_per_fault.
-- It is now a ratio of sums over positive spans, which is minutes per fault
-- and weights a 12-hour chain above a 2-minute one. Rows with a zero span are
-- excluded rather than NaN'd, and the count is published so the exclusion is
-- visible instead of implicit.
-- ---------------------------------------------------------------------
ALTER TABLE ps2_cascade_velocity
  ADD COLUMN IF NOT EXISTS n_zero_span_excluded BIGINT;

COMMENT ON COLUMN ps2_cascade_velocity.n_zero_span_excluded IS
  'Chains in this window with chain_span_min <= 0, excluded from the velocity ratio. Added 23-Sep-2026 with the minutes-per-fault correction.';

COMMENT ON COLUMN ps2_cascade_velocity.mean_velocity_min_per_fault IS
  'Minutes per fault: SUM(chain_span_min) / SUM(chain_length) over chains with a positive span. Before 23-Sep-2026 this column held the reciprocal (faults per minute) computed as a mean of per-chain ratios. Rows at computed_date <= 2026-08-29 carry the old quantity until the producer is re-run.';

-- ---------------------------------------------------------------------
-- 2. Recurrence -- carry the chronic threshold in the row.
--
-- The chronic flag used a single fleet-wide 90th percentile. Validators are
-- 3,361 of 4,710 devices, so that cut is a validator threshold: on the 29-Aug
-- run it landed at 946 cascade-days while GATE's maximum is 808, and not one
-- of the 874 gates could clear it -- the dashboard read "Chronic: no" for
-- every gate in the estate. The cut is now taken WITHIN device_category, and
-- travels in the row so "no" can be read as "below its category's cut of N"
-- rather than as "healthy".
-- ---------------------------------------------------------------------
ALTER TABLE ps2_recurrence
  ADD COLUMN IF NOT EXISTS chronic_cut NUMERIC(12,3);

COMMENT ON COLUMN ps2_recurrence.chronic_cut IS
  'Cascade-days threshold this device was tested against: the 90th percentile WITHIN its device_category, or the fleet-wide 90th percentile where no category is known. Added 23-Sep-2026.';

-- ---------------------------------------------------------------------
-- 3. Facility contagion -- a real rate beside the count that was misnamed.
--
-- contagion_rate was cascade_days / distinct_devices: mean cascade-DAYS PER
-- DEVICE, an unbounded count, rendered through a percent formatter -- which
-- is how facility 44 reached 78,217.3%. It also ranked single-device
-- facilities first, because one device carrying every cascade-day maximises
-- the ratio, and a facility with one device cannot exhibit contagion at all.
-- The producer now emits that quantity under an honest name and computes the
-- real one the way refresh_device_state.compute_facility_contagion does.
-- ---------------------------------------------------------------------
ALTER TABLE ps2_facility_contagion_facility
  ADD COLUMN IF NOT EXISTS cascade_days_per_device     NUMERIC(12,6);
ALTER TABLE ps2_facility_contagion_facility
  ADD COLUMN IF NOT EXISTS facility_cascade_days       BIGINT;
ALTER TABLE ps2_facility_contagion_facility
  ADD COLUMN IF NOT EXISTS multi_device_days           BIGINT;
ALTER TABLE ps2_facility_contagion_facility
  ADD COLUMN IF NOT EXISTS multi_device_contagion_rate NUMERIC(6,4);
ALTER TABLE ps2_facility_contagion_facility
  ADD COLUMN IF NOT EXISTS contagion_eligible          BOOLEAN;

COMMENT ON COLUMN ps2_facility_contagion_facility.contagion_rate IS
  'RETIRED 23-Sep-2026, kept only so the rows at computed_date <= 2026-08-29 survive. It is cascade_days / distinct_devices -- a per-device count, not a rate, and never a percentage. Read cascade_days_per_device (same quantity, honest name) or multi_device_contagion_rate (the real one).';

COMMENT ON COLUMN ps2_facility_contagion_facility.multi_device_contagion_rate IS
  'Share of this facility''s cascade-days on which 2 or more distinct devices cascaded. Bounded 0-1. NULL where the facility has fewer than 2 devices.';

COMMENT ON COLUMN ps2_facility_contagion_facility.contagion_eligible IS
  'distinct_devices >= 2. A single-device facility cannot exhibit contagion; such rows are ranked last rather than dropped.';

-- The read-side view. CREATE OR REPLACE may only APPEND columns, so the new
-- ones go on the end and the existing order is untouched.
CREATE OR REPLACE VIEW v_ps2_facility_contagion_facility AS
SELECT facility_id, cascade_days, distinct_devices, contagion_rate, grain,
       run_id, computed_date, city_id,
       cascade_days_per_device, facility_cascade_days, multi_device_days,
       multi_device_contagion_rate, contagion_eligible
FROM ps2_facility_contagion_facility t
WHERE computed_date = (SELECT MAX(computed_date)
                       FROM ps2_facility_contagion_facility
                       WHERE city_id = t.city_id);
