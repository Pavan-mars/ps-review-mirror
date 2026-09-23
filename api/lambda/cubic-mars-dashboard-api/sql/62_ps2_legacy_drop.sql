-- =====================================================================
-- PS2 legacy retirement -- DROP. This one is irreversible.
-- 23-Sep-2026
--
-- WHAT THESE 16 TABLES WERE. They backed the pre-V4 dashboard at
-- /dashboard/city/:cityId, removed earlier today, and the 22 API routes that
-- served only it. Nothing reads them now: no route, no view, no dashboard
-- component, no other problem statement.
--
-- HOW THAT WAS ESTABLISHED, because a repo grep has been wrong twice:
--   * Code:     every table name searched across the handler (comments
--               stripped), all 96 SQL views parsed ONE STATEMENT AT A TIME --
--               a regex spanning statement boundaries had previously
--               attributed one view to nearly every table on the list.
--   * Database: the read-only `depends` action -- pg_depend for views,
--               matviews and rules, plus inbound foreign keys.
--   * Control:  ps2_network_centrality was run through the same checks and
--               came back BLOCKED by v_ps2_network_centrality, which is what
--               it should be -- V4 reads it through /ps2/network. It is NOT
--               on this list.
--
-- THREE THAT LOOKED DROPPABLE AND ARE NOT:
--   ps2_leadlag_timing   the database reported no dependents, and
--                        /device/360's causation panel reads it in code.
--                        That is the whole reason `depends` now answers
--                        "no_database_dependents" instead of "safe_to_drop".
--   ps2_recurrence       blocked by a view, and read in code.
--   ps2_network_centrality  the control above.
--
-- SIX MORE ARE SIMPLY ABSENT. ps2_device_cmdb_map_device and the five
-- ps2_v25_*_audit tables were never created in Aurora -- the producers write
-- them to S3 and the loader has never had a target, so it reports them as
-- no_target on every run. Nothing to drop; the producers stop writing them in
-- the same commit as this file.
--
-- ROWS DESTROYED: about 162,000, all at computed_date 2026-08-29, all
-- reproducible by re-running the serial-grain notebook -- IF the write_output
-- calls are restored, which this commit also removes.
--
-- THIS ENDS FACILITY-LEVEL CONTAGION ANALYSIS. ps2_facility_contagion_facility
-- is the table this morning's contagion fix writes into: the one that
-- published a per-device count through a percent formatter and reached
-- 78,217.3%. That fix, and the columns sql/60 added for it, go with the
-- table. PK asked for it explicitly on 23-Sep after being shown the
-- consequence.
--
-- ORDER MATTERS: the one dependent view goes before its table. No CASCADE is
-- used anywhere -- a DROP that fails because something unexpected depends on
-- it is the outcome we want, not one that silently takes the dependent too.
--
-- NOT DROPPED, listed so the next cleanup does not re-raise them:
--   ps2_network_centrality   read by V4 via /ps2/network -> v_ps2_network_centrality
--   ps2_leadlag_timing       read by /device/360 causation, handler ~:1221
--   ps2_recurrence           read in code, and carries a dependent view
--   ps2_cascade_sankey_subsystem        V4, /ps2/serial/sankey
--   ps2_ignition_termination_subsystem  V4, /ps2/serial/ignition
--   ps2_phi_matrix           V4, /ps2/phi
--   ps2_markov_transitions   /device/360 fleet causation
--   the 20 ps2_v25_* / ps2_v2_* tables  the V4 PS2 tab
--
-- ROW COUNTS AT THE TIME OF WRITING (measured 23-Sep via `depends`):
--   ps2_association_rules_serial               11,836 rows
--   ps2_business_impact_serial                 4,549
--   ps2_cascade_velocity_by_age_serial         5
--   ps2_chronic_recurrence_serial              4,547
--   ps2_conditional_prob_serial                96,329  the largest
--   ps2_cross_ps_attribution_serial            1
--   ps2_hmm_regimes_serial                     4,453
--   ps2_leadlag_timing_serial                  2,590
--   ps2_markov_self_transition_serial          4,522
--   ps2_phi_matrix_serial                      31,197
--   ps2_phi_outliers_serial                    1,563
--   ps2_suppression_summary_serial             1
--   ps2_cascade_velocity                       5
--   ps2_cross_ps_attribution_device            1
--   ps2_hmm_regimes                            3
--   ps2_facility_contagion_facility            227
--
-- Ship through the handler's apply_sql action, dry-run first.
--
-- This file ends on a statement, not a comment: split_sql emits a trailing
-- comment block as its own statement and apply_sql counts anything that
-- errors as a failure.
-- =====================================================================

-- ---------------------------------------------------------------------
-- 1. The only dependent view on anything in this set.
--    Its sole reader was /ps2/serial/facility, removed with the 22 routes.
-- ---------------------------------------------------------------------
DROP VIEW IF EXISTS v_ps2_facility_contagion_facility;

-- ---------------------------------------------------------------------
-- 2. Serial-grain families. The serial producer wrote 27 tables; after the
--    legacy dashboard went, V4 read two of them -- cascade_sankey_subsystem
--    and ignition_termination_subsystem. These are the rest.
-- ---------------------------------------------------------------------
DROP TABLE IF EXISTS ps2_association_rules_serial;
DROP TABLE IF EXISTS ps2_business_impact_serial;
DROP TABLE IF EXISTS ps2_cascade_velocity_by_age_serial;
DROP TABLE IF EXISTS ps2_chronic_recurrence_serial;
DROP TABLE IF EXISTS ps2_conditional_prob_serial;
DROP TABLE IF EXISTS ps2_cross_ps_attribution_serial;
DROP TABLE IF EXISTS ps2_hmm_regimes_serial;
DROP TABLE IF EXISTS ps2_leadlag_timing_serial;
DROP TABLE IF EXISTS ps2_markov_self_transition_serial;
DROP TABLE IF EXISTS ps2_phi_matrix_serial;
DROP TABLE IF EXISTS ps2_phi_outliers_serial;
DROP TABLE IF EXISTS ps2_suppression_summary_serial;

-- ---------------------------------------------------------------------
-- 3. Device- and facility-grain tables whose only routes are gone.
--    ps2_cascade_velocity is the table whose mean_velocity_min_per_fault
--    held its own reciprocal -- fixed in the producer this morning, and the
--    fix now has no target. ps2_hmm_regimes held three rows.
-- ---------------------------------------------------------------------
DROP TABLE IF EXISTS ps2_cascade_velocity;
DROP TABLE IF EXISTS ps2_cross_ps_attribution_device;
DROP TABLE IF EXISTS ps2_hmm_regimes;
DROP TABLE IF EXISTS ps2_facility_contagion_facility;
