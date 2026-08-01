-- ============================================================================
-- seed_from_06_phase1d_ps3_device.sql
-- Extracted 2026-07-26 from sql/06_phase1d_ps3_device.sql
--
-- ####################################################################
-- ##  NOT A MIGRATION. Kept out of the numbered sql/NN_*.sql         ##
-- ##  sequence on purpose, so migrate() cannot run it.               ##
-- ####################################################################
--
-- WHY THIS WAS MOVED
-- ------------------
-- These are hardcoded literal metric rows for city CHI, dated 2026-07-13. They
-- were sitting inside a schema migration, and migrate() runs on EVERY deploy --
-- so any PS1/PS3 purge was undone by the next deploy. Worse, the guards are
-- `ON CONFLICT ... DO NOTHING`, which means they re-insert precisely when the
-- rows are absent: immediately after a wipe.
--
-- They are also the origin of two numbers already removed from the API surface:
--   * ps1_model_performance carries train_auc 1.00 / val_auc 1.00 for GATE
--     against a held-out 0.9038 -- the in-sample fit the dashboard no longer shows.
--   * ps3_device_metrics carries 'train' and 'val' split rows, which /ps3/devices
--     now filters out.
--
-- A schema migration should create schema. Seeding measured model output belongs
-- to the loader that reads the run artifacts. Run this file by hand ONLY if you
-- deliberately want the 13-Jul demo numbers back:
--     psql "$CONN" -f manual/seed_from_06_phase1d_ps3_device.sql
-- ============================================================================

INSERT INTO ps3_device_metrics (city_id,device,split,n_incidents,f1_macro,accuracy,as_of_date) VALUES
 ('CHI','TVM','train',25183,0.9200,0.9220,DATE '2026-07-13'),
 ('CHI','TVM','val',3280,0.8910,0.8940,DATE '2026-07-13'),
 ('CHI','TVM','test',4454,0.8980,0.9050,DATE '2026-07-13'),
 ('CHI','Gates','train',1399,0.4980,0.9930,DATE '2026-07-13'),
 ('CHI','Gates','val',136,0.4810,0.9260,DATE '2026-07-13'),
 ('CHI','Gates','test',244,0.4930,0.9710,DATE '2026-07-13');

