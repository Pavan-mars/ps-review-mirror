-- =====================================================================
-- CUBIC MARS Chicago -- sql/50
-- v_ps1_xw_causation: publish every fleet; flag the ones with too little data
-- Date: 2026-08-10   Idempotent. Reversible -- rollback SQL in the footer.
--
-- WHAT WAS WRONG
-- --------------
-- sql/34 ended the view with
--       WHERE n_chain >= 30 AND n_no_chain >= 30
-- The intent was correct: a lift ratio computed from a handful of device-days
-- is noise wearing a decimal point, and publishing it is worse than publishing
-- nothing. The implementation was not correct. A WHERE clause does not say
-- "not enough data" -- it says nothing at all. The fleet disappears from the
-- causation panel, and a reader who has not read this file concludes either
-- that PS1 has no GATE model or that GATE was never cross-wired with PS2.
--
-- Both readings are false. GATE carries its full share of the 786,525
-- device-days in ps1_cross_wired_daily. What GATE lacks is enough device-days
-- on BOTH sides of the coordinated-station-failure split for the ratio between
-- them to carry information.
--
-- Silence and insufficiency look identical on a dashboard. They are not the
-- same finding, and the panel must not render them the same way.
--
-- THE FIX
-- -------
-- Keep the row. Drop the row's conclusion.
--
--   sufficient_data   did this fleet clear the 30-per-cell bar?
--   min_cell          LEAST(n_chain, n_no_chain) -- HOW short it fell, not
--                     merely that it fell short. A fleet at 28 and a fleet at
--                     2 are different problems and get different answers.
--   critical_rate_in_chain / critical_rate_no_chain / critical_lift
--                     NULL when sufficient_data is false, so there is no
--                     number on the panel that the data did not earn.
--
-- The mean_* columns are NOT nulled. They are descriptive statistics of the
-- rows that exist, published beside the n_chain / n_no_chain counts that
-- qualify them; they assert no relationship. critical_lift is a ratio of two
-- rates and it does assert one, which is why it is the column that is withheld.
--
-- COLUMN CONTRACT
-- ---------------
-- The eleven columns sql/34 published keep their names, order and types.
-- sufficient_data and min_cell are APPENDED at the end -- the only shape
-- change CREATE OR REPLACE VIEW permits. Any existing consumer that selects
-- by name is unaffected.
--
-- The DROP is deliberate and is NOT a CASCADE. If anything in the database has
-- come to depend on this view since sql/34, the DROP fails loudly and the
-- CREATE OR REPLACE that follows still applies the change. A CASCADE would
-- have removed the dependent object silently, which is exactly the class of
-- damage this file exists to argue against.
-- =====================================================================

DROP VIEW IF EXISTS v_ps1_xw_causation;

CREATE OR REPLACE VIEW v_ps1_xw_causation AS
WITH base AS (
  SELECT
    city_id, device_type,
    COUNT(*) FILTER (WHERE is_coordinated_station_failure)                    AS n_chain,
    COUNT(*) FILTER (WHERE NOT is_coordinated_station_failure
                        OR is_coordinated_station_failure IS NULL)            AS n_no_chain,
    COUNT(*) FILTER (WHERE is_coordinated_station_failure
                       AND ps1_risk_tier = 'CRITICAL')                        AS n_crit_chain,
    COUNT(*) FILTER (WHERE (NOT is_coordinated_station_failure
                            OR is_coordinated_station_failure IS NULL)
                       AND ps1_risk_tier = 'CRITICAL')                        AS n_crit_no_chain,
    ROUND(AVG(ps1_fail_prob) FILTER (WHERE is_coordinated_station_failure), 6) AS mean_prob_chain,
    ROUND(AVG(ps1_fail_prob) FILTER (WHERE NOT is_coordinated_station_failure
                                        OR is_coordinated_station_failure IS NULL), 6)
                                                                              AS mean_prob_no_chain,
    ROUND(AVG(days_healthy_before_chain), 2)                                  AS mean_days_healthy_before_chain,
    ROUND(AVG(station_devices_failed::numeric), 2)                            AS mean_station_devices_failed
  FROM ps1_cross_wired_daily
  GROUP BY city_id, device_type
),
-- One source of truth for the bar, so the flag and the NULLing can never
-- disagree with each other the way a repeated literal eventually does.
bar AS (SELECT 30::bigint AS min_n),
flagged AS (
  SELECT b.*, r.min_n,
         (b.n_chain >= r.min_n AND b.n_no_chain >= r.min_n) AS sufficient_data,
         LEAST(b.n_chain, b.n_no_chain)                     AS min_cell
  FROM base b CROSS JOIN bar r
)
SELECT
  city_id,
  device_type,
  n_chain,
  n_no_chain,
  CASE WHEN sufficient_data
       THEN ROUND(n_crit_chain::numeric / NULLIF(n_chain, 0), 4) END      AS critical_rate_in_chain,
  CASE WHEN sufficient_data
       THEN ROUND(n_crit_no_chain::numeric / NULLIF(n_no_chain, 0), 4) END AS critical_rate_no_chain,
  CASE WHEN sufficient_data
       THEN ROUND( (n_crit_chain::numeric    / NULLIF(n_chain, 0))
                 / NULLIF(n_crit_no_chain::numeric / NULLIF(n_no_chain, 0), 0), 3) END AS critical_lift,
  mean_prob_chain,
  mean_prob_no_chain,
  mean_days_healthy_before_chain,
  mean_station_devices_failed,
  sufficient_data,
  min_cell
FROM flagged;

COMMENT ON VIEW v_ps1_xw_causation IS
  'PS1 x PS2 contagion lift per fleet. One row per (city_id, device_type) ALWAYS. '
  'sufficient_data=false means fewer than 30 device-days on one side of the '
  'coordinated-station-failure split; the three rate columns are NULL in that case '
  'and min_cell shows the shortfall. sql/50, 2026-08-10 -- replaces sql/34, which '
  'omitted the row entirely and made insufficiency indistinguishable from absence.';

-- ---------------------------------------------------------------------------
-- ROLLBACK. Restores the sql/34 definition exactly, including the WHERE clause.
-- Paste into apply_sql-equivalent or psql; nothing else in sql/50 writes data,
-- so this is the whole undo.
--
--   DROP VIEW IF EXISTS v_ps1_xw_causation;
--   -- then re-apply sql/34_ps1_cross_wired.sql, which recreates the original
--   -- 11-column definition with WHERE n_chain >= 30 AND n_no_chain >= 30.
--
-- sql/34 is idempotent and creates tables with IF NOT EXISTS, so re-applying it
-- is safe: it rebuilds the views and touches no row of ps1_cross_wired_daily.
-- ---------------------------------------------------------------------------
