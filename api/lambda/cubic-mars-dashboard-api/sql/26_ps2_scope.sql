-- =====================================================================
-- 26_ps2_scope.sql   27-Jul-2026
--
-- WHY THIS EXISTS
-- ---------------
-- The PS2 load aborted with
--
--   duplicate key value violates unique constraint "ps2_network_centrality_pkey"
--   Key (city_id, node_id, computed_date)=(CHI, PRINTER, 2026-07-26) already exists.
--
-- and because that fired inside the batch transaction, the ROLLBACK discarded
-- the twenty-one tables that had already loaded cleanly. Nothing reached Aurora.
--
-- The cause is not a bug in the loader's SQL. It is a grain mismatch. The export
-- ps2_network_centrality_subsystem is 27 rows:
--
--     scope=ALL        10 subsystems
--     scope=TVM         8
--     scope=GATE        5
--     scope=VALIDATOR   4
--
-- so PRINTER legitimately appears four times -- once for the fleet overall and
-- once for each device type it is present on. scope is part of the grain. The
-- table's primary key did not include it, so four distinct rows competed for one
-- key slot.
--
-- WHY scope IS ADDED RATHER THAN FILTERED AWAY
-- --------------------------------------------
-- The cheap fix is to keep only scope='ALL' and load 10 rows. That would drop
-- precisely the breakdown this dashboard has been asked for repeatedly: which
-- subsystems are hubs ON VALIDATORS as against on gates and TVMs. VALIDATOR has
-- only 4 subsystems in the cascade graph against TVM's 8 -- that contrast IS the
-- finding, and the ALL row averages it out of existence.
--
-- So scope becomes a real column and joins the key. `role` is left alone: it was
-- meant for a node's graph role (hub vs bridge), and an earlier version of the
-- loader parked scope in it, which preserved the value while doing nothing about
-- the key. That alias is removed in the loader alongside this file.
--
-- Idempotent: the DROP CONSTRAINT precedes the ADD every time, so re-running is
-- safe and migrate() may execute this on every deploy.
-- =====================================================================

ALTER TABLE ps2_network_centrality
  ADD COLUMN IF NOT EXISTS scope VARCHAR(16) NOT NULL DEFAULT 'ALL';

ALTER TABLE ps2_network_centrality
  DROP CONSTRAINT IF EXISTS ps2_network_centrality_pkey;

ALTER TABLE ps2_network_centrality
  ADD CONSTRAINT ps2_network_centrality_pkey
  PRIMARY KEY (city_id, node_id, scope, computed_date);

-- Reading view. Two things a raw SELECT does not give you:
--   1. is_fleet separates the fleet-wide row from the per-device-type rows, so a
--      chart cannot accidentally sum ALL together with TVM+GATE+VALIDATOR and
--      double-count every subsystem.
--   2. pagerank_rank is computed WITHIN a scope. Ranking across scopes would
--      compare a validator subsystem's pagerank against a TVM one, and those are
--      normalised over different graphs -- the numbers are not on one scale.
CREATE OR REPLACE VIEW v_ps2_network_centrality AS
SELECT
  n.city_id,
  n.computed_date,
  n.scope,
  (n.scope = 'ALL')                                  AS is_fleet,
  n.node_id,
  n.betweenness,
  n.pagerank,
  n.in_degree,
  n.out_degree,
  n.in_degree + n.out_degree                         AS total_degree,
  RANK() OVER (PARTITION BY n.city_id, n.computed_date, n.scope
               ORDER BY n.pagerank DESC NULLS LAST)  AS pagerank_rank,
  RANK() OVER (PARTITION BY n.city_id, n.computed_date, n.scope
               ORDER BY n.betweenness DESC NULLS LAST) AS betweenness_rank,
  COUNT(*)    OVER (PARTITION BY n.city_id, n.computed_date, n.scope)
                                                     AS n_nodes_in_scope
FROM ps2_network_centrality n;
