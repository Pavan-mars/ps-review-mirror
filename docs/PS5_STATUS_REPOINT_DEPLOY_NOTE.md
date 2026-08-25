# PS5 status repoint -- deploy note (25-Aug-2026)

## What changed
- `api/lambda/cubic-mars-dashboard-api/handler.py` -- `/ps5/status` and the
  device-360 PS5 rollup no longer read `ps5_reliability_status` (three rows
  hand-seeded 11-Jul, never refreshed: v1-era C-indexes 0.5906/0.5071/0.5970).
  Both now derive from `ps5_cindex_leaderboard` (refreshed daily 07:20 UTC by
  cubic-mars-ps5-rds-loader) via the new `_ps5_registry_rows()`: best
  `oot_cindex` per fleet = the v5.6 champion. Response shape is unchanged
  (lowercase-plural `device_type`, `dashboard_ready` stays FALSE -- no fleet is
  signed off; this feed remains what marks the model a prioritisation aid, not
  a scheduler). An empty or missing leaderboard returns `[]`, never a 500.
- `api/lambda/cubic-mars-dashboard-api/sql/02_phase1_ps2_ps5_backfill.sql` --
  the seed INSERT into `ps5_reliability_status` is DELETED. sql/02 is in the
  migrate() replay tuple, so leaving it would resurrect the stale rows on every
  deploy. The CREATE TABLE stays until the manual drop below is done.
- No front-end files changed: the shape is byte-compatible.

## Deploy order (the safe path)
1. Deploy by ZIP-SWAP (`aws lambda update-function-code`), NOT `deploy.sh` --
   see tooling/ps1_go_live.sh: deploy.sh runs migrate() unconditionally, and
   the replay re-seeds sql/08's PS2 rows and runs sql/18's DELETE. Build the
   zip from the full function dir so the package carries the edited sql/02;
   any later migrate() then has nothing left to re-seed.
2. Verify:
   `curl "$APIURL/ps5/status?city=CHI"` -- expect 3 rows, registry_status
   `v5_6_champion`, C-indexes ~0.678 gates / ~0.798 tvms / ~0.649 validators,
   dashboard_ready false, blockers empty except validators ("misses the 0.65
   C-index floor by 0.001").
3. Only after 2 passes, drop by hand (psql, in this order -- the LIVE 7-column
   `v_ps5_dashboard_ready` is the legacy ps5-rds-loader sql/05 version and
   reads the table, so a bare DROP TABLE would fail on the dependency):
   `DROP VIEW IF EXISTS v_ps5_dashboard_ready;`
   `DROP TABLE IF EXISTS ps5_reliability_status;`

## What still reads the table after this change
Nothing that breaks. Checked: dashboard-api handler.py (both reads repointed);
the legacy ps5-rds-loader's run_inspect only lists it via information_schema
and reports `exists: false` cleanly; no code reads `v_ps5_dashboard_ready`.
Residual, harmless: sql/02 and legacy sql/05 keep their CREATEs, so a later
migrate() replay recreates the table EMPTY (and sql/01 recreates the view in
its 5-column ps5_reliability_estimates form) -- unread either way.
