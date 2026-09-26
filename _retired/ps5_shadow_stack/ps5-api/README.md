# ps5-api — source recovered from the live function (25-Aug-2026)

Source of the LIVE Lambda `ps5-api` behind API Gateway b1s4xxlddb (`ps5-api-gw`).
Downloaded 25-Aug-2026 from the function's Code.Location URL (zip 430,202 bytes =
the function's CodeSize). No other copy existed; the function is invisible to the
cubic|mars-filtered inventory sweeps.

Read-only: serves /ps5/devices and /ps5/serials from the two v_ps5_*_oos_latest
views. Consumed by PS5SLAReliabilityTab.jsx (V1 shell), which hard-codes the
b1s4xxlddb URL as a build-time fallback. Reads env PS5_RDS_SECRET (a different
contract from the dashboard-api's SECRET_ARN/DB_NAME).

Vendored deps (pg8000 et al.) not committed. Retirement is HOLD per the 24-Aug
adjudicated cleanup inventory — see api/lambda/ps5-rds-loader/README.md.
