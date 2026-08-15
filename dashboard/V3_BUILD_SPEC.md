# V3 dashboard — build spec

Written 04-Aug-2026 at the end of a session, for the session that builds V3.
Everything here is measured against the live deployment, not assumed.

---

## 1. Decisions already taken

- **Seven screens**, from PK's mockups: Fleet Status landing, Top depots /
  critical issues, Repeat offenders, Device-wise list, Components, Actions,
  and the Device 360 popup.
- **Live API, real numbers.** No mocked figures. Every tile reads a deployed
  endpoint. PK chose this explicitly over the mockups' bus framing.
- **Keep the mockups' layout, not their vocabulary.** The mockups say "buses"
  and name CTA depots. Chicago's fleet is TVMs, fare gates and validators
  across numbered depots. Use the mockup's *structure* — KPI row, icon-headed
  section cards, WHERE/WHAT/WHEN triptych, funnel — with the real fleet's
  nouns. `bus_identity.bus_label` exists per device and IS a real bus number
  where the device is vehicle-mounted; use it there and only there.
- **The Device 360 popup is already built** — `v2/DeviceBrief.jsx`. It follows
  the mockup: header, risk banner, WHERE/WHEN/WHAT cards, one recommendation,
  staged action footer. Reuse it; do not rebuild it.

---

## 2. Where things live

- App routes are in `dashboard/src/App.jsx`. v2 is mounted at **`/v2`**
  (`<V2Shell city="CHI" />`), plus `/v2/ps1`, `/v2/ps2`, `/v2/ps4`.
- **There is a second, older dashboard** at `/dashboard/city/:cityId` with its
  own PS1 page. It is NOT the v2 code. Editing v2 has no effect on it. This
  caused a long false trail — if someone reports "the fix didn't work", check
  which URL they are on before anything else.
- V3 should mount at **`/v3`** alongside v2, not replace it. v2 stays working
  through the demo.

---

## 3. Endpoints, with measured timings

Base: `VITE_API_BASE_URL` (currently the `a9yuqt9j9b` API Gateway, us-east-1).

| Route | Rows | Time | Feeds which screen |
|---|---|---|---|
| `/ps1/station-summary` | 44 depots | fast | Fleet Status KPIs, depot table |
| `/ps1/predictions` | 600 | ~2s | Device list, Actions |
| `/ps1/xw-tiers` | 12 | 1.4s | Risk mix |
| `/ps1/xw-chronic` | 20 | 5.9s | Repeat offenders |
| `/ps1/xw-state-mix` | 38 | 11.9s | Condition by risk tier |
| `/ps1/xw-act-now` | 100 | ~12s | Work list |
| `/ps1/xw-flag-reason` | 3 | ~2s | When the alert fired |
| `/ps1/xw-base-rate` | 3 | ~2s | Observed failure rate |
| `/ps5/device-rul?device_type=` | ~1.3k each | 0.5s, **187 KB** | Components, device roster |
| `/ps1/device-360?device_id=` | object | 0.8–8.6s | Device 360 popup |

**API Gateway has a hard 30s cap.** Anything approaching it returns 503. Two
concurrent requests is the measured sweet spot; past three they get slower,
not faster, and start exceeding the cap.

---

## 4. Field names that matter

`/ps1/predictions` rows:
`device_id, device_category, facility_id, failure_probability,
decision_threshold, predicted_label, ps1_risk_tier, prediction_date,
station_name, operator, dom_error_code, cat_rank`

`/ps1/device-360` top level:
`device_id, city, ps1, ps1_state, ps2, ps3, ps4, ps5, bus_identity,
ps3_v2_rootcause_360, ps4_v3_360, cross_ps, recommendation, servicenow_payload`

- `ps1_state`: `device_state` (HEALTHY / IN_SPELL / NEW_ONSET / RECOVERED),
  `ps1_fail_prob`, `ps1_risk_tier`, `threshold_used`, `last_scored_day`,
  `n_spells`, `total_oos_days`, `days_since_spell_end`, `state_note`
- `bus_identity`: `bus_label`, `facility_name`, `operator_name`,
  `transit_mode_name`, `component_serial_nbr`, `component_type`
- `ps5.components[]`: `component_type_name`, `component_serial_nbr`,
  `component_age_days`, `risk_tier`, `expected_component_rul_days`,
  `is_overdue`, `act_now`

`/ps1/xw-chronic`: `device_id, device_type, n_spells, total_days_out,
longest_spell`

---

## 5. Real figures the mockups should show

Measured today, Chicago, scored to 11 Apr 2026:

- 1,951 devices in service across 44 depots
- 799 above alert threshold (41%), 775 in the critical band (40%)
- 20 repeat offenders (out of service more than once)
- Device states: 1,925 in spell, 39 new onset, 472 recovered, 1,781 healthy
  (totals 4,217 — the state view covers the wider scored roster, NOT the
  1,951 in-service count; do not mix the two on one tile)
- 4,103 devices in the PS5 roster across three fleets

---

## 6. Traps found the hard way

1. **Feed loader cancellation.** v2's `useFeeds` aborted in-flight requests on
   every tab switch, leaving panels spinning forever. Fixed by tying liveness
   to the component (a ref), not to each effect run. Do not reintroduce
   `let alive = true` inside an effect keyed on the request list.
2. **Roster cost.** `/ps5/device-rul` is 187 KB per fleet. Fetch fleets
   sequentially, cache per session, and never let the roster race the detail
   call — browsers cap connections per host and the roster will starve it.
3. **Slow panels need honest placeholders.** `xw-state-mix` takes ~12s. A bare
   spinner reads as broken. Say "about 15 seconds" in the placeholder.
4. **Charts render as SVG**, so headless text-based tests cannot prove a chart
   filled. Verify screens in a real browser.
5. **`STATUS.warning` is an object.** Use `STATUS.warning.fill` for colours.

---

## 7. Suggested build order

1. V3 design primitives — KPI tile, icon-headed section card, data table with
   pagination + CSV, filter bar. Everything else composes from these.
2. Fleet Status landing (the demo opener).
3. Device-wise list + Repeat offenders (same table primitive, different feed).
4. Components + Actions (both off `/ps5/device-rul` and `/ps1/predictions`).
5. Wire `DeviceBrief` to open from every row and bubble.
6. Browser verification pass on all screens.

Build the primitives first. Six of the seven screens are the same four
components rearranged, and the mockups are consistent about it.
