# Retired 27-Sep-2026 -- PS2 hand-seeded rows

`02_phase1_ps2_ps5_backfill.sql` (computed_date 2026-07-11) and `08_ps2_run_backfill.sql` (2026-07-14) were in
the dashboard API's `migrate` list, so every deploy DELETEd and re-INSERTed these hardcoded rows. The PS2 loader
(`cubic-mars-ps2-rds-loader`) owns all ps2_* data. `sql/75_ps2_retire.sql` removes the seeded rows from tables that
hold a newer loader vintage. Kept here for the record only.
