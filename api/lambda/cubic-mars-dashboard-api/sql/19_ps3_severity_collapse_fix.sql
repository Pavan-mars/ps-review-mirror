-- ============================================================================
-- 19_ps3_severity_collapse_fix.sql             CUBIC MARS Chicago  2026-07-26
--
-- FIXES: pct_critical_pred = 0.0000 on every PS3 device and serial row.
--
-- ROOT CAUSE (measured on the 21-Jul run bundle, Level2/PS3):
--   pred_severity_collapsed came out MAJOR on 32,842 / 32,842 TVM incidents and
--   1,770 / 1,770 GATE incidents -- 100%. There was no CRITICAL row anywhere, so
--   every downstream  AVG(collapsed = 'CRITICAL')  evaluated to exactly 0.
--   The collapse map was keyed on human-readable severity names, but
--   gold.device_ps3_incident.failure_level_label carries CODES. Nothing matched,
--   and the map's default was MAJOR, so every incident silently became MAJOR.
--
-- THE MAP, keyed on the codes that are actually present. Enumerated from the run
-- bundle -- these four are the complete set of observed values:
--     PURCHASE_CARD      18,777   -> MAJOR      (card purchase path down)
--     ALL_PURCHASE       11,179   -> CRITICAL   (every purchase path down)
--     ALL_FUNCTIONS       3,394   -> CRITICAL   (whole device down)
--     PURCHASE_PRODUCT    1,262   -> MAJOR      (one product path down)
--   plus, for completeness, codes the model can emit but which had 0 actual
--   occurrences in this run:
--     NONPAYMENT               -> MAJOR
--     BUS_READER               -> CRITICAL
--     BUS_READER_ASSEMBLY      -> CRITICAL
--     OTHER / anything else    -> UNKNOWN  (NOT MAJOR -- see below)
--
--   Applied to the 21-Jul bundle this yields CRITICAL = 14,573 / 34,612 = 42.10%
--   of incidents, against the 0.00% currently stored.
--
-- WHY UNKNOWN AND NOT MAJOR:
--   Defaulting the unmapped bucket to MAJOR is precisely what caused this bug.
--   UNKNOWN rows are excluded from the pct_critical denominator, so an unmapped
--   code shows up as a shrinking denominator rather than as a confident MAJOR.
--
-- BUSINESS RULE, NOT A DATA FACT:
--   failure_level_label describes WHAT stopped working, not a severity tier. The
--   CRITICAL/MAJOR split below encodes the judgement that "all purchase paths
--   down" and "whole device down" are operationally critical while a single
--   payment path down is major. That is a call for the CTA/Cubic operations
--   owner to confirm. If they classify differently, edit ONLY the CASE below and
--   CONFIG["SEVERITY_COLLAPSE"] in the PS3 v2 notebook -- and keep the two
--   identical, or the dashboard and the model will disagree about "critical".
--
-- IDEMPOTENT. Safe to re-run; safe on empty tables (updates 0 rows). Registered
-- in migrate() so any future run that lands with the old collapse self-corrects.
-- ============================================================================

-- ---------------------------------------------------------------------
-- 1. Recompute the collapsed severity at the incident grain, from the CODE.
-- ---------------------------------------------------------------------
UPDATE ps3_incident_predictions
SET pred_severity_collapsed = CASE UPPER(BTRIM(pred_severity))
      WHEN 'ALL_FUNCTIONS'       THEN 'CRITICAL'
      WHEN 'ALL_PURCHASE'        THEN 'CRITICAL'
      WHEN 'BUS_READER'          THEN 'CRITICAL'
      WHEN 'BUS_READER_ASSEMBLY' THEN 'CRITICAL'
      WHEN 'PURCHASE_CARD'       THEN 'MAJOR'
      WHEN 'PURCHASE_PRODUCT'    THEN 'MAJOR'
      WHEN 'NONPAYMENT'          THEN 'MAJOR'
      ELSE 'UNKNOWN'
    END
WHERE pred_severity IS NOT NULL
  AND pred_severity_collapsed IS DISTINCT FROM CASE UPPER(BTRIM(pred_severity))
      WHEN 'ALL_FUNCTIONS'       THEN 'CRITICAL'
      WHEN 'ALL_PURCHASE'        THEN 'CRITICAL'
      WHEN 'BUS_READER'          THEN 'CRITICAL'
      WHEN 'BUS_READER_ASSEMBLY' THEN 'CRITICAL'
      WHEN 'PURCHASE_CARD'       THEN 'MAJOR'
      WHEN 'PURCHASE_PRODUCT'    THEN 'MAJOR'
      WHEN 'NONPAYMENT'          THEN 'MAJOR'
      ELSE 'UNKNOWN'
    END;

-- ---------------------------------------------------------------------
-- 2. Rebuild pct_critical_pred on the DEVICE rollup from the corrected
--    incident rows. UNKNOWN is excluded from the denominator, so the share is
--    "critical among incidents we can classify", not "critical among all rows".
-- ---------------------------------------------------------------------
UPDATE ps3_device_predictions d
SET pct_critical_pred = s.pct_crit,
    dominant_pred_severity = COALESCE(s.dominant, d.dominant_pred_severity)
FROM (
  SELECT p.city_id, p.run_id, p.device_id,
         ROUND(
           COUNT(*) FILTER (WHERE p.pred_severity_collapsed = 'CRITICAL')::numeric
           / NULLIF(COUNT(*) FILTER (WHERE p.pred_severity_collapsed IN ('CRITICAL','MAJOR')), 0)
         , 4) AS pct_crit,
         MODE() WITHIN GROUP (ORDER BY p.pred_severity) AS dominant
  FROM ps3_incident_predictions p
  WHERE p.pred_severity IS NOT NULL
  GROUP BY p.city_id, p.run_id, p.device_id
) s
WHERE d.city_id = s.city_id AND d.run_id = s.run_id AND d.device_id = s.device_id
  AND d.pct_critical_pred IS DISTINCT FROM s.pct_crit;

-- ---------------------------------------------------------------------
-- 3. Rebuild pct_critical_pred on the SERIAL rollup, same rule, at
--    (device_id, matched_serial_nbr).
-- ---------------------------------------------------------------------
UPDATE ps3_serial_predictions x
SET pct_critical_pred = s.pct_crit,
    dominant_pred_component = COALESCE(s.dominant_comp, x.dominant_pred_component)
FROM (
  SELECT p.city_id, p.run_id, p.device_id, p.matched_serial_nbr,
         ROUND(
           COUNT(*) FILTER (WHERE p.pred_severity_collapsed = 'CRITICAL')::numeric
           / NULLIF(COUNT(*) FILTER (WHERE p.pred_severity_collapsed IN ('CRITICAL','MAJOR')), 0)
         , 4) AS pct_crit,
         MODE() WITHIN GROUP (ORDER BY p.pred_component) AS dominant_comp
  FROM ps3_incident_predictions p
  WHERE p.pred_severity IS NOT NULL AND p.matched_serial_nbr IS NOT NULL
  GROUP BY p.city_id, p.run_id, p.device_id, p.matched_serial_nbr
) s
WHERE x.city_id = s.city_id AND x.run_id = s.run_id
  AND x.device_id = s.device_id AND x.matched_serial_nbr = s.matched_serial_nbr
  AND x.pct_critical_pred IS DISTINCT FROM s.pct_crit;

-- ---------------------------------------------------------------------
-- 4. Standing check. Query this after any PS3 load: if collapse_share is 1.0000
--    for a single label, the collapse has regressed to a constant again.
-- ---------------------------------------------------------------------
CREATE OR REPLACE VIEW v_ps3_collapse_health AS
SELECT city_id,
       run_id,
       mars_device_category,
       pred_severity_collapsed,
       COUNT(*)                                                   AS n,
       ROUND(COUNT(*)::numeric / SUM(COUNT(*)) OVER (
               PARTITION BY city_id, run_id, mars_device_category), 4) AS collapse_share
FROM ps3_incident_predictions
GROUP BY city_id, run_id, mars_device_category, pred_severity_collapsed;
