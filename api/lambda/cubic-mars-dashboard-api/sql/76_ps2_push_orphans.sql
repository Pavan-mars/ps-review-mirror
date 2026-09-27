-- 76_ps2_push_orphans.sql                                              27-Sep-2026
--
-- Tables the retired cubic-mars-ps2-rds-push Lambda auto-created on 26-Jul under the RAW source names, plus its
-- audit table. The current loader writes these sources into the aliased targets (ps2_subsystem_associations,
-- ps2_business_impact, ps2_leadlag_timing, ps2_network_centrality, ps2_recurrence), so these copies stopped at
-- 2026-07-26; hmm_regimes_device and cascade_velocity_device are no longer produced at all. No route, view,
-- dashboard file or other Lambda reads any of them. Run `depends` first. No CASCADE. Idempotent.

DROP TABLE IF EXISTS ps2_association_rules_device;
DROP TABLE IF EXISTS ps2_business_impact_device;
DROP TABLE IF EXISTS ps2_cascade_velocity_device;
DROP TABLE IF EXISTS ps2_hmm_regimes_device;
DROP TABLE IF EXISTS ps2_leadlag_timing_device;
DROP TABLE IF EXISTS ps2_network_centrality_subsystem;
DROP TABLE IF EXISTS ps2_recurrence_device;
DROP TABLE IF EXISTS ps2_load_audit;
