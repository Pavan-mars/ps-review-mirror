# fastapi_app - local mock server, NOT DEPLOYED, kept on purpose

Do not delete. Do not deploy. 23-Sep-2026.

## What it is

`main.py` is a 25-route FastAPI server - `/health`, six `/ps1/*`, seventeen
`/ps2/*`, one `/ps5/*` - that returns **entirely synthetic data**. Its only
imports are `math`, `random`, `datetime`, `typing` and `fastapi`. There is no
`pg8000`, no `psycopg`, no `boto3`, no Secrets Manager and no SQL anywhere in
it. Every value is generated from `RNG = random.Random(42)`, so the output is
stable across runs and looks like real telemetry.

It exists so the dashboard can be rendered with no VPN, no Aurora and no
credentials. That is a genuinely useful thing to have, which is why it stays.

## The hazard, stated plainly

It answers the **same paths** the real API answers. `/ps2/network`, `/ps2/phi`,
`/ps2/devices` and fourteen more are served here from a seeded RNG and in
production from Aurora. A dashboard pointed at this one renders fabricated
numbers with no error, no empty state and no badge. Nothing on the screen, and
nothing in a screenshot of the screen, distinguishes the two.

So: never behind a shared hostname, and never as `API_BASE_URL` for anything a
client can see.

## Status

The `BackendFastApi-V3` ECS task was already stopped on 25-Aug-2026 and its
task definition captured to
`tooling/out/backendfastapi_capture_25Aug2026.json`. Nothing runs this today.

## It is also now divergent from the real API

On 23-Sep-2026, `sql/62` and `sql/63` dropped 29 PS2 tables and the live PS2
surface was cut to six route families: `/ps2/status`, `/ps2/devices`,
`/ps2/network`, `/ps2/phi`, `/ps2/serial/sankey`, `/ps2/serial/ignition`, plus
20 `/ps2/v25/{metric}`.

Seventeen paths here no longer exist there - `/ps2/windows`, `/ps2/topdevices`,
`/ps2/paths`, `/ps2/hub`, `/ps2/errorcodes`, `/ps2/facility`, `/ps2/hmm` and
the rest. This server will answer them cheerfully long after the real API
returns 404.

That divergence is **not a defect to fix here**. Re-syncing the routes would
make the mock resemble production more closely, which makes the hazard above
worse, not better. Leave them different.

## ps5_reliability_routes.py is not part of this app

Nothing imports it. There is no `include_router` call anywhere in `main.py`,
and the file's own header says it is for the dashboard-api. Unlike `main.py`,
it *does* connect to Aurora - `boto3` + `psycopg2` + Secrets Manager - and
reads `v_ps5_reliability_oos_latest`. It is a snippet parked in the wrong
folder. Anyone reasoning about "what the FastAPI app does" should ignore it,
and anyone looking for that PS5 query should look here for it.

## Correction on the record

An earlier note said redeploying this would "serve 17 routes onto dropped
tables". That was wrong: it never read a table. The names that looked like
tables - `ps2_devices`, `ps2_hub`, `ps2_errorcodes` - are the Python function
names behind the routes. The real risk was never a broken query; it was a
convincing answer.
