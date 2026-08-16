-- =====================================================================
-- CUBIC MARS Chicago -- sql/55
-- Restore /ps3/collapse-health (tracker #65). Date: 2026-08-16.
-- Applied manually to dev Aurora via psql on 2026-08-16 (same convention
-- as sql/50-54: NOT registered in migrate()).
--
-- ROOT CAUSE (measured live 16-Aug, VPC CloudShell psql):
--   The route pins the view to v_ps3_latest_run. On 04-Aug the run registry
--   ps3_model_runs gained `ps3_oos_20260804` (1,027,390 events, the
--   hardware-OOS rebuild, severity_collapse_verified = f), which became the
--   latest run. ps3_incident_predictions -- the guard's source -- holds only
--   `ps3_20260726` (34,612 rows). Pointer moved, source did not: the route
--   asked the view for a run it never had, and returned [] from 08-04 on.
--   (The tracker blamed the 08-08 v25 reload; the actual mover was the 08-04
--   registry insert. The v25 loader keys its tables by manifest UUID --
--   6a787954-... -- and never touches ps3_incident_predictions.)
--
-- WHY THE GUARD IS NOT SIMPLY RE-POINTED AT THE v25 TABLES:
--   ps3_v25_device_episode_fact.dashboard_severity is '(unlabelled)' on ALL
--   54,239 episodes (GATE 38,448 / TVM 5,665 / VALIDATOR 10,126, measured
--   16-Aug). Serving that as the guard's shares would show one label at
--   collapse_share 1.0000 forever -- the exact alarm condition -- until the
--   V26 family grows severity labels. The unlabelled state is recorded
--   honestly in v_ps3_v25_severity_maturity below instead.
--
-- WHAT THIS DOES:
--   v_ps3_collapse_health now computes the distribution from the NEWEST run
--   actually present in ps3_incident_predictions and serves it under the
--   registry-latest run_id so the deployed route matches again -- with the
--   true origin in a new trailing column `source_run_id` (appended column:
--   CREATE OR REPLACE VIEW-safe; the route selects named columns only).
--   MAX(run_id) picks the newest source run: run ids are date-suffixed
--   (ps3_YYYYMMDD) so lexical max = chronological max; revisit if the naming
--   scheme changes.
-- =====================================================================

CREATE OR REPLACE VIEW v_ps3_collapse_health AS
WITH pointer AS (
    SELECT city_id, run_id FROM v_ps3_latest_run
),
src AS (
    SELECT city_id, MAX(run_id) AS run_id
    FROM ps3_incident_predictions
    GROUP BY city_id
),
dist AS (
    SELECT p.city_id,
           p.run_id AS source_run_id,
           p.mars_device_category,
           p.pred_severity_collapsed,
           COUNT(*) AS n,
           ROUND(COUNT(*)::numeric / SUM(COUNT(*)) OVER (
                 PARTITION BY p.city_id, p.run_id, p.mars_device_category), 4) AS collapse_share
    FROM ps3_incident_predictions p
    JOIN src s ON s.city_id = p.city_id AND s.run_id = p.run_id
    GROUP BY p.city_id, p.run_id, p.mars_device_category, p.pred_severity_collapsed
)
SELECT d.city_id,
       COALESCE(pt.run_id, d.source_run_id) AS run_id,
       d.mars_device_category,
       d.pred_severity_collapsed,
       d.n,
       d.collapse_share,
       d.source_run_id
FROM dist d
LEFT JOIN pointer pt ON pt.city_id = d.city_id;

-- The v25 side of the story, NOT wired to any route: how much of the current
-- episode estate carries a severity label at all. Today: 0% -- every row is
-- '(unlabelled)'. When the V26 severity head ships, this view shows coverage
-- climbing; if the guard above ever needs to move to v25, this is the source.

CREATE OR REPLACE VIEW v_ps3_v25_severity_maturity AS
SELECT city_id,
       run_id,
       mars_device_category,
       COALESCE(NULLIF(BTRIM(dashboard_severity), ''), '(unlabelled)') AS dashboard_severity,
       severity_status,
       COUNT(*) AS n,
       ROUND(COUNT(*)::numeric / SUM(COUNT(*)) OVER (
             PARTITION BY city_id, run_id, mars_device_category), 4) AS share
FROM ps3_v25_device_episode_fact
GROUP BY city_id, run_id, mars_device_category,
         COALESCE(NULLIF(BTRIM(dashboard_severity), ''), '(unlabelled)'), severity_status;
