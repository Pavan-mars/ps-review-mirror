# dashboard/src — what is what

Chicago (CTA-Ventra). Last reorganised **08-Aug-2026**.

There are **two** applications in this tree, and only two. V2 and V3 were
removed on 08-Aug-2026 — see "Removed" below.

---

## V4 — the CURRENT Chicago dashboard  →  `v4/`

**This is what ships.** Mounted at `/v4`, and `/` and `*` both redirect there,
so a bare hostname lands on it.

PS1–PS5 plus Device 360, with the sub-tab structure the client reviews:
Fleet status, Location, Devices, Components, Model quality, How we know.

- `V4Shell.jsx` — tab rail and layout
- `V4PS1Overview … V4PS5Overview.jsx` — one file per problem statement
- `V4Device360.jsx` / `V4Device360Popup.jsx` — the device record and its popup
- `V4Kit.jsx`, `V4Charts.jsx`, `V4ChartsPlus.jsx`, `V4DataTable.jsx` — shared primitives
- `V4Locations.js` — **one** facility-id → location-name lookup, fed by
  `/ps1/facilities`, so PS1 is the single authority on what a place is called
- `V4Evidence.js` — **one** place that turns cross-model signals into plain
  sentences, so the popup and the full tab cannot describe a device differently
- `V4api.js`, `V4theme.js` — routes and design tokens

**V4 is self-contained.** It imports nothing from V1 (`pages/`, `components/`,
`context/`, `data/`). Verified 08-Aug-2026. Keep it that way — the shared
`auth/` and `runtimeConfig.js` are the only crossings.

## V1 — the original Chicago dashboard  →  `pages/` + `components/` + `context/` + `data/`

Still routed and still working: `/dashboard/overview`, `/dashboard/city/:cityId`,
`/admin`, `/login`.

- `pages/` — ExecutiveOverview, CityDashboard, AdminConsole, LoginPage
- `components/` — `layout/` (Sidebar, FilterBar), `shared/`, `tabs/`
- `context/FilterContext.jsx` — **load-bearing.** V1 tables reconcile their rows
  against the selections FilterBar owns; unmounting it filters every table to
  zero while the API returns rows. It was removed once, on 29-Jul-2026, and put
  straight back.
- `data/` — `api.js` plus `mockData.js` / `ps5ReliabilityMock.js`

## Shared by both

- `auth/` — `AuthContext`, `ProtectedRoute`, `mockAuthAPI`.
  **`ProtectedRoute` is a UX affordance, not a security control** — it checks
  localStorage. The real gate is `authenticate-cognito` on the ALB listener.
- `runtimeConfig.js` — resolves the API base URL at **runtime** from
  `/config.js`, falling back to the build-time `VITE_API_BASE_URL`. This is what
  lets **one container image serve dev, UAT and prod**. Do not reintroduce a
  build-time-only read.
- `App.jsx` — routing. `main.jsx` — entry point.

---

## Removed on 08-Aug-2026

- **V2** (`v2/`, 18 files) — quarantined to `_to_delete/dirs/dashboard_src_v2/`.
  Its four routes (`/v2`, `/v2/ps1`, `/v2/ps4`, `/v2/ps2`) and four imports were
  stripped from `App.jsx`. Nothing else referenced it.
- **V3** (`v3/`) — quarantined to `_to_delete/dirs/`. Already unreferenced.

Some V4 file headers still say "v2/Charts.jsx" and similar. That is honest
provenance — V4 was derived from V2 — and not a live dependency.

`_to_delete/` is gitignored and is a staging area for deletion, not an archive.
Recover from git history instead once these changes are committed.
