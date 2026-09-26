# ps5-rds-loader — source recovered from the live function (25-Aug-2026)

This directory holds the source of the LIVE Lambda `ps5-rds-loader` (the bare name,
NOT `cubic-mars-ps5-rds-loader`, which is a different function with its own source
under `api/lambda/cubic-mars-ps5-rds-loader/`).

Provenance: downloaded 25-Aug-2026 from the function's Code.Location URL
(zip size 448,482 bytes, matching the function's CodeSize exactly). Until this
commit there was NO copy of this code anywhere — not in the repo, not in any
deploy script, and the function is invisible to tooling/out/cubic_inventory_v3
sweeps because they filter names on `cubic|mars`.

What it is: the writer half of the "shadow PS5 stack" —
  writes ps5_reliability_estimates / ps5_serial_reliability / ps5_scoring_runs
  → views v_ps5_reliability_oos_latest / v_ps5_serial_oos_latest
  → Lambda ps5-api (api/lambda/ps5-api/) → API GW b1s4xxlddb
  → dashboard/src/components/tabs/PS5SLAReliabilityTab.jsx (V1 shell).

sql/ carries the three migrations that were bundled INSIDE the deployed zip:
  05_phase1c_ps5_reliability_survival.sql
  06_phase1d_ps5_oos_set_reliability.sql   (also restored to dashboard/backfill/ by
                                            branch fix/restore-ps5-oos-ddl)
  07_phase1e_ps5_edv_widen.sql             (existed NOWHERE else — it DROPs and
                                            recreates both live views with
                                            event_def_version VARCHAR(64), so the
                                            LIVE view definitions are the 07
                                            versions, superseding 06's)

Vendored dependencies (pg8000, scramp, asn1crypto, dateutil, six) are NOT
committed — rebuild with `pip install pg8000 -t .` if this ever needs redeploying.
Retirement of this stack is HOLD per the 24-Aug adjudicated cleanup inventory
(release condition: remove the hardcoded b1s4xxlddb fallback + V1 routes, rebuild
the dashboard image, verify zero requests, then retire gateway + both Lambdas).
