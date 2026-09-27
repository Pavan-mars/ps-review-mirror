# Retired 27-Sep-2026 -- PS2 July demo schema and seed rows

`02_phase1_ps2_ps5_backfill.sql`, `05_phase1d_ps2_device.sql`, `07_phase1e_ps2_new.sql`, `08_ps2_run_backfill.sql` were in the
dashboard API's `migrate` list, so every API deploy re-CREATEd tables that sql/62 and sql/63 had dropped (23-Sep) and
re-INSERTed hand-typed July rows (computed_date 2026-07-11 / 2026-07-14). That is why /ps2/status still listed 21
"unmanaged" tables on 27-Sep. The only live object among them, ps2_business_impact (read by v_ps2_device_cascade),
now has its DDL in sql/27. `sql/75_ps2_retire.sql` drops the rest. Kept for the record only.
