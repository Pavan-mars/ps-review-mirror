-- 75_ps2_retire.sql -- PS2 stale-element retirement, made to stick.        27-Sep-2026
--
-- sql/62 and sql/63 (23-Sep) dropped these, but every API deploy ran `migrate`, whose files CREATEd them again
-- (sql/01, 05, 07, 09, 27) and re-INSERTed July demo rows (sql/02, 05, 07, 08). On 27-Sep /ps2/status still listed
-- 21 "unmanaged" tables, 12 frozen at 2026-07-11/14. Those statements are now out of migrate (sql/02, 05, 07, 08
-- retired to _retired/ps2_seed_backfills; the CREATE blocks removed from 01, 09, 27), so this drop holds.
--
-- Also dropped: two ad-hoc 2-row tables with no DDL in the repo and no reader (ps2_cascade_path_explainability,
-- ps2_cascade_risk_assessments). ps2_business_impact is KEPT: v_ps2_device_cascade (Device-360) reads it.
-- KEPT although frozen at 2026-07-14: ps2_device_catalog (/ps2/devices, Device-360, PS1 top-risk join) and
-- ps2_device_cascades (Device-360) -- still read; see docs/review/PS2_REVIEW_27Sep2026.md.
--
-- Views first, then tables. No CASCADE. Idempotent. Run `depends` on the tables first.

DROP VIEW  IF EXISTS v_ps2_facility_contagion_facility;
DROP TABLE IF EXISTS ps2_association_rules_serial;
DROP TABLE IF EXISTS ps2_business_impact_serial;
DROP TABLE IF EXISTS ps2_cascade_chains_daily;
DROP TABLE IF EXISTS ps2_cascade_path_explainability;
DROP TABLE IF EXISTS ps2_cascade_paths;
DROP TABLE IF EXISTS ps2_cascade_risk_assessments;
DROP TABLE IF EXISTS ps2_cascade_velocity;
DROP TABLE IF EXISTS ps2_cascade_velocity_by_age_serial;
DROP TABLE IF EXISTS ps2_cascade_window_summary;
DROP TABLE IF EXISTS ps2_chronic_recurrence_serial;
DROP TABLE IF EXISTS ps2_conditional_prob_serial;
DROP TABLE IF EXISTS ps2_cross_ps_attribution_device;
DROP TABLE IF EXISTS ps2_cross_ps_attribution_serial;
DROP TABLE IF EXISTS ps2_error_code_transitions;
DROP TABLE IF EXISTS ps2_error_codes;
DROP TABLE IF EXISTS ps2_facility_contagion_daily;
DROP TABLE IF EXISTS ps2_facility_contagion_facility;
DROP TABLE IF EXISTS ps2_facility_contagion_summary;
DROP TABLE IF EXISTS ps2_hmm_regimes;
DROP TABLE IF EXISTS ps2_hmm_regimes_serial;
DROP TABLE IF EXISTS ps2_ignition_termination;
DROP TABLE IF EXISTS ps2_leadlag_timing_serial;
DROP TABLE IF EXISTS ps2_markov_self_transition_serial;
DROP TABLE IF EXISTS ps2_phi_matrix_serial;
DROP TABLE IF EXISTS ps2_phi_outliers_serial;
DROP TABLE IF EXISTS ps2_subsystem_hub_edges;
DROP TABLE IF EXISTS ps2_subsystem_hub_summary;
DROP TABLE IF EXISTS ps2_suppression_summary_serial;
DROP TABLE IF EXISTS ps2_top_devices;
DROP TABLE IF EXISTS ps2_v2_run_quality;
DROP TABLE IF EXISTS ps2_window_detail;
