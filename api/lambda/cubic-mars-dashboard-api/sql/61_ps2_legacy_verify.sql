-- =====================================================================
-- PS2 legacy retirement -- VERIFY ONLY. This file drops nothing.
-- 23-Sep-2026
--
-- Run this BEFORE 62_ps2_legacy_drop.sql and read every result. It answers
-- the three questions a repo grep cannot:
--
--   1. Does any VIEW still depend on these tables? A view is the indirection
--      that put seven live tables on a retirement list on 20-Sep, and
--      DROP TABLE without CASCADE fails on a dependent view -- which is the
--      good case. The bad case is DROP ... CASCADE silently taking the view
--      with it.
--   2. Do they hold rows, and from which run? A table with rows from the
--      current run is still being written by a producer, whatever the repo
--      says about who reads it.
--   3. Does anything OUTSIDE this list reference them -- a foreign key, a
--      trigger, a rule?
--
-- Every statement is read-only.
-- =====================================================================

-- ---------------------------------------------------------------------
-- 1. VIEW DEPENDENCIES. Anything returned here must be resolved before the
--    drop: either the view goes too, or the table stays.
--    Expected: v_ps2_facility_contagion_facility only.
-- ---------------------------------------------------------------------
SELECT DISTINCT
       v.table_name  AS dependent_view,
       t.table_name  AS depends_on
  FROM information_schema.view_table_usage v
  JOIN information_schema.tables t
    ON t.table_name = v.table_name
 WHERE v.table_schema = 'public'
   AND v.table_name IN (
        'ps2_suppression_summary_serial','ps2_chronic_recurrence_serial',
        'ps2_recurrence_serial','ps2_leadlag_timing_serial',
        'ps2_association_rules_serial','ps2_business_impact_serial',
        'ps2_cascade_velocity_by_age_serial','ps2_cross_ps_attribution_serial',
        'ps2_phi_matrix_serial','ps2_conditional_prob_serial',
        'ps2_hmm_regimes_serial','ps2_markov_self_transition_serial',
        'ps2_facility_contagion_facility')
 ORDER BY 1, 2;

-- The same question asked of pg_depend, which also catches materialized
-- views, rules and anything view_table_usage misses.
SELECT DISTINCT
       dependent.relname AS dependent_object,
       dependent.relkind AS kind,
       source.relname    AS depends_on
  FROM pg_depend d
  JOIN pg_rewrite r      ON r.oid = d.objid
  JOIN pg_class dependent ON dependent.oid = r.ev_class
  JOIN pg_class source    ON source.oid = d.refobjid
 WHERE source.relname IN (
        'ps2_suppression_summary_serial','ps2_chronic_recurrence_serial',
        'ps2_recurrence_serial','ps2_leadlag_timing_serial',
        'ps2_association_rules_serial','ps2_business_impact_serial',
        'ps2_cascade_velocity_by_age_serial','ps2_cross_ps_attribution_serial',
        'ps2_phi_matrix_serial','ps2_conditional_prob_serial',
        'ps2_hmm_regimes_serial','ps2_markov_self_transition_serial',
        'ps2_facility_contagion_facility')
   AND dependent.relname <> source.relname
 ORDER BY 1, 3;

-- ---------------------------------------------------------------------
-- 2. FOREIGN KEYS pointing AT them. A drop would fail, or cascade.
--    Expected: none.
-- ---------------------------------------------------------------------
SELECT tc.table_name AS referencing_table, kcu.column_name,
       ccu.table_name AS referenced_table
  FROM information_schema.table_constraints tc
  JOIN information_schema.key_column_usage kcu
    ON kcu.constraint_name = tc.constraint_name
  JOIN information_schema.constraint_column_usage ccu
    ON ccu.constraint_name = tc.constraint_name
 WHERE tc.constraint_type = 'FOREIGN KEY'
   AND ccu.table_name IN (
        'ps2_suppression_summary_serial','ps2_chronic_recurrence_serial',
        'ps2_recurrence_serial','ps2_leadlag_timing_serial',
        'ps2_association_rules_serial','ps2_business_impact_serial',
        'ps2_cascade_velocity_by_age_serial','ps2_cross_ps_attribution_serial',
        'ps2_phi_matrix_serial','ps2_conditional_prob_serial',
        'ps2_hmm_regimes_serial','ps2_markov_self_transition_serial',
        'ps2_facility_contagion_facility')
 ORDER BY 1;

-- ---------------------------------------------------------------------
-- 3. WHAT IS ACTUALLY IN THEM, and from which run. Rows at the current
--    computed_date mean a producer is still filling the table -- which is
--    true here and is NOT a reason to keep it: the serial-grain notebook
--    writes 27 tables and, after the legacy dashboard went, V4 reads two.
--    Dropping these is a decision that the serial-grain analysis has no
--    audience, not a claim that it is stale.
-- ---------------------------------------------------------------------
SELECT 'ps2_suppression_summary_serial' AS t, COUNT(*) n, MAX(computed_date) d FROM ps2_suppression_summary_serial
UNION ALL SELECT 'ps2_chronic_recurrence_serial',      COUNT(*), MAX(computed_date) FROM ps2_chronic_recurrence_serial
UNION ALL SELECT 'ps2_recurrence_serial',              COUNT(*), MAX(computed_date) FROM ps2_recurrence_serial
UNION ALL SELECT 'ps2_leadlag_timing_serial',          COUNT(*), MAX(computed_date) FROM ps2_leadlag_timing_serial
UNION ALL SELECT 'ps2_association_rules_serial',       COUNT(*), MAX(computed_date) FROM ps2_association_rules_serial
UNION ALL SELECT 'ps2_business_impact_serial',         COUNT(*), MAX(computed_date) FROM ps2_business_impact_serial
UNION ALL SELECT 'ps2_cascade_velocity_by_age_serial', COUNT(*), MAX(computed_date) FROM ps2_cascade_velocity_by_age_serial
UNION ALL SELECT 'ps2_cross_ps_attribution_serial',    COUNT(*), MAX(computed_date) FROM ps2_cross_ps_attribution_serial
UNION ALL SELECT 'ps2_phi_matrix_serial',              COUNT(*), MAX(computed_date) FROM ps2_phi_matrix_serial
UNION ALL SELECT 'ps2_conditional_prob_serial',        COUNT(*), MAX(computed_date) FROM ps2_conditional_prob_serial
UNION ALL SELECT 'ps2_hmm_regimes_serial',             COUNT(*), MAX(computed_date) FROM ps2_hmm_regimes_serial
UNION ALL SELECT 'ps2_markov_self_transition_serial',  COUNT(*), MAX(computed_date) FROM ps2_markov_self_transition_serial
UNION ALL SELECT 'ps2_facility_contagion_facility',    COUNT(*), MAX(computed_date) FROM ps2_facility_contagion_facility
 ORDER BY 1;

-- ---------------------------------------------------------------------
-- 4. THE CONTROL. ps2_network_centrality is NOT on the drop list and must
--    not be. It looked orphaned through /ps2/serial/network, but V4 reads it
--    through /ps2/network via v_ps2_network_centrality. If this returns no
--    view, stop -- the assumption behind keeping it is wrong and the rest of
--    the analysis deserves re-checking too.
-- ---------------------------------------------------------------------
SELECT 'ps2_network_centrality' AS control_table,
       (SELECT COUNT(*) FROM ps2_network_centrality)                AS rows_now,
       (SELECT COUNT(*) FROM information_schema.views
         WHERE table_schema='public' AND table_name='v_ps2_network_centrality') AS view_exists;
