# Dashboard V4 — where it lives, how to run it, what to know

**Status (2026-08-16): V4 is the current dashboard, fully merged to `main` (merge `f043c60`), tags `v4.0.0-rc1` / `v4.0.0-rc2`. V2 is retired.**

## Where

The complete dashboard app is `dashboard/` (single Vite + React 19 app, `cubic-dashboards`).
The V4 UI lives in **`dashboard/src/v4/`** — 19 source files:

- Shell + kit: `V4Shell.jsx`, `V4Kit.jsx`, `V4theme.js`, `V4Charts.jsx`, `V4ChartsPlus.jsx`, `V4DataTable.jsx`, `V4DeviceTable.jsx`, `V4GlobalSearch.jsx`
- Per-PS screens: `V4PS1Overview.jsx`, `V4PS2Overview.jsx`, `V4PS3Overview.jsx`, `V4PS4Overview.jsx`, `V4PS4Clusters.jsx`, `V4PS5Overview.jsx`
- Device 360: `V4Device360.jsx`, `V4Device360Popup.jsx`
- Data/API helpers: `V4api.js`, `V4Evidence.js`, `V4Locations.js`

Legacy `src/v2/`, `src/_retired/`, `src/_v51verify/` are retired leftovers (kept empty; safe to delete).

## Run it

```
cd dashboard
npm install
npm run dev        # local dev server (Vite)
npm run build      # production build -> dist/
```

API base URL: `dashboard/.env` -> `VITE_API_BASE_URL=https://a9yuqt9j9b.execute-api.us-east-1.amazonaws.com`
(runtime override supported via `src/runtimeConfig.js` + `public/config.js`).

Container path: `Dockerfile` + `nginx.conf` + `docker-entrypoint.sh` (ECS task-ready; image tag = commit SHA).

## Data comes from

API Gateway `a9yuqt9j9b` -> `cubic-mars-dashboard-api` Lambda -> Aurora (`appdb`), loaded from S3 model
outputs by the per-PS loader Lambdas. Full lineage: see the `chicago-data-lineage` skill and `api/lambda/`.

## Known caveats (from docs/V4_DASHBOARD_PS1_AUDIT_16Aug2026.md — read it before demoing PS1)

1. The V4 PS1 screen carries **no data-vintage indicator** (PS2/PS3 have a StatusBar with as-of date; PS1 does not).
2. `/ps1/station-summary` and `/ps1/risk-trend` serve **26-Jul hand-seeded tables** — every PS1 headline number
   and the Estate card come from that seed, not from the live loaders.
3. V4 never calls `/ps1/model-performance`; PS1 model stats shown are hardcoded in the UI layer.
4. PS1 S3 artifacts are dated 29-Jul; nightly loaders re-read the same objects until the notebooks are re-run.

## For the team

Pull `main`, `npm install`, set `.env` if your API differs, `npm run dev`. The PS1 caveat fixes are
tracked in the audit doc's item list; backend/API work lives under `api/lambda/`.
