import { API_BASE_URL, MUTATION_TOKEN } from '../runtimeConfig';
// =====================================================================
// v2/v2api.js -- fetch helpers for the v2 screens.
//
// Reuses the SAME base URL the existing api.js reads, so there is one
// place to point at a different environment and v1/v2 can never drift
// onto different backends.
//
// Every helper returns data or a documented empty value. None of them
// throw into a render path -- a panel that cannot load must show its own
// empty state, not unmount the tab. That exact failure (a ReferenceError
// inside one modal taking the whole Failure Prediction subtree down) is why this rule
// exists.
// =====================================================================
export const BASE = API_BASE_URL || '';

function qstr(params) {
  return new URLSearchParams(
    Object.entries(params).filter(([, v]) => v !== undefined && v !== null && v !== '')
  ).toString();
}

async function raw(path, params = {}) {
  if (!BASE) return { ok: false, status: 0, data: null };
  const qs = qstr(params);
  try {
    const r = await fetch(`${BASE}${path}${qs ? `?${qs}` : ''}`);
    const data = await r.json().catch(() => null);
    return { ok: r.ok, status: r.status, data };
  } catch (e) {
    return { ok: false, status: 0, data: null, err: String((e && e.message) || e) };
  }
}

export async function get(path, params = {}) {
  const { ok, data } = await raw(path, params);
  return ok ? data : null;
}

// THROWS on an HTTP failure. That is deliberate and it is the whole point.
//
// This used to return [] for a 500 exactly as it does for a genuinely empty
// table, so a timed-out request rendered as "Nothing to show for the current
// selection" -- the UI reporting a server failure as a finding about the
// fleet. Three panels on the Why tab did precisely that on 01-Aug while the
// routes behind them held 20, 20 and 3 rows.
//
// Now the caller's catch decides. useFeeds() turns a throw into that panel's
// amber "could not load" state and leaves the neutral empty state to mean
// what it says.
export async function getRows(path, params = {}) {
  const { ok, status, data, err } = await raw(path, params);
  if (!ok) throw new Error(err || `${path} returned HTTP ${status || 'no response'}`);
  return Array.isArray(data) ? data : [];
}

// Objects default to {} for the same reason.
export async function getObj(path, params = {}) {
  const j = await get(path, params);
  return j && typeof j === 'object' && !Array.isArray(j) ? j : {};
}

// POST returns {ok, data, error} rather than throwing, because the only
// caller is a button the user pressed and the failure has to be shown to
// them in place rather than swallowed.
export async function post(path, body, params = {}) {
  if (!BASE) return { ok: false, error: 'No API base URL configured' };
  const qs = new URLSearchParams(
    Object.entries(params).filter(([, v]) => v !== undefined && v !== null && v !== '')
  ).toString();
  try {
    // The API's write routes require x-cubic-token once MUTATION_TOKEN is set
    // on the function. The value comes from /config.js at boot (runtime), so
    // the same image serves every environment. Empty = header omitted, which
    // keeps a token-less API working unchanged.
    const headers = { 'content-type': 'application/json' };
    if (MUTATION_TOKEN) headers['x-cubic-token'] = MUTATION_TOKEN;
    const r = await fetch(`${BASE}${path}${qs ? `?${qs}` : ''}`, {
      method: 'POST',
      headers,
      body: JSON.stringify(body || {}),
    });
    const data = await r.json().catch(() => ({}));
    if (!r.ok) return { ok: false, error: (data && data.error) || `HTTP ${r.status}`, data };
    return { ok: true, data };
  } catch (e) {
    return { ok: false, error: String(e && e.message ? e.message : e) };
  }
}

// ---- the handful of Failure Prediction endpoints the v2 template uses ---------------
export const ps1 = {
  predictions: (city) => getRows('/ps1/predictions', { city }),
  serialPredictions: (city) => getRows('/ps1/serial-predictions', { city }),
  riskBands: (city) => getRows('/ps1/risk-bands', { city }),
  riskTrend: (city) => getRows('/ps1/risk-trend', { city }),
  stations: (city) => getRows('/ps1/station-summary', { city }),
  // The facility DIMENSION, not the PS1 rollup: every facility_id in
  // dim_device_station with its name, so PS2-PS5 can resolve ids PS1 never
  // scored. Added 06-Aug-2026; see V4Locations for the fallback path.
  facilities: (city) => getRows('/ps1/facilities', { city }),
  leaderboard: (city) => getRows('/ps1/leaderboard', { city }),
  confusion: (city) => getRows('/ps1/confusion', { city }),
  thresholdSweep: (city) => getRows('/ps1/threshold-sweep', { city }),
  crosstab: (city) => getRows('/ps1/crosstab', { city }),
  // Cross-wired family. These are the ones with real content -- note that
  // /ps1/feature-importance is EMPTY in Chicago and xw-drivers is not, so
  // the driver story is sourced here and nowhere else.
  xwSummary: (city) => getRows('/ps1/xw-summary', { city }),
  xwTiers: (city) => getRows('/ps1/xw-tiers', { city }),
  xwDrivers: (city) => getRows('/ps1/xw-drivers', { city }),
  xwStateMix: (city) => getRows('/ps1/xw-state-mix', { city }),
  // Critical rate inside a fault chain vs outside one.
  // VALIDATOR 99.2% vs 8.1% (lift 12.3x). TVM 95.9% vs 86.8% (1.1x).
  xwCausation: (city) => getRows('/ps1/xw-causation', { city }),
  xwFlagReason: (city) => getRows('/ps1/xw-flag-reason', { city }),
  xwPerformance: (city) => getRows('/ps1/xw-performance', { city }),
  xwPerformanceOnset: (city) => getRows('/ps1/xw-performance-onset', { city }),
  // Onsets vs stored positive days. inflation_factor is the honest one:
  // GATE stores 76.8% of days as "failure" while only 3.9% are new onsets,
  // an inflation of 19.7x. That single number explains why accuracy on this
  // problem is meaningless and why the label had to be redefined.
  xwBaseRate: (city) => getRows('/ps1/xw-base-rate', { city }),
  xwChronic: (city) => getRows('/ps1/xw-chronic', { city }),
  xwFacility: (city) => getRows('/ps1/xw-facility', { city }),
  xwActNow: (city) => getRows('/ps1/xw-act-now', { city }),
  device360: (city, deviceId) => getObj('/ps1/device-360', { city, device_id: deviceId }),
  componentInventory: (city) => getRows('/fleet/component-inventory', { city }),
  // Whole-fleet roster off the conformed device dimension (dim_device_incident_cmdb,
  // 27-Aug): 6,619 devices vs the 1,536 the PS5 feed covers. Four columns, one call.
  deviceCentralRoster: (city) => getRows('/device/central', { city, roster: 1 }),
};


// ---- Anomaly & Outlier Analysis v3 (weekly anomaly) -----------------------------------------
// Every one of these answers in under 0.6s, because the notebook already
// aggregates to device-WEEKS (7,742 rows) rather than device-days. This is
// the shape Failure Prediction's xw_* views should be in and are not.
//
// TWO CAPS TO RESPECT:
//   /ps4/weekly        defaults to LIMIT 500 of 7,742. A browse list, never
//                      a denominator. Pass limit to widen it.
//   /ps4/weekly-alerts returns the COMPLETE actionable set (1,108 rows,
//                      922 distinct devices), so totals come from here.
// Fleet observed/actionable counts come from /ps4/weekly-timeline, which is
// the per-week-per-fleet roll-up and the only honest denominator.
export const ps4 = {
  status: (city) => getObj('/ps4/v3-status', { city }),
  weekly: (city, limit = 2000) => getRows('/ps4/weekly', { city, limit }),
  alerts: (city) => getRows('/ps4/weekly-alerts', { city, limit: 5000 }),
  timeline: (city) => getRows('/ps4/weekly-timeline', { city }),
  facility: (city) => getRows('/ps4/weekly-facility', { city }),
  persistent: (city) => getRows('/ps4/weekly-persistent', { city }),
  clusterProfile: (city) => getRows('/ps4/cluster-profile', { city }),
  clusterQuality: (city) => getRows('/ps4/cluster-quality', { city }),
};

// ---- Remaining Useful Life & SLA Breach (survival / remaining useful life) ---------------------------
// TWO KINDS OF ROUTE HERE AND THEY ARE NOT INTERCHANGEABLE.
//
//   summary / componentSummary  aggregate in SQL over the WHOLE view. These
//                               are the only honest Remaining Useful Life & SLA Breach totals.
//   deviceRul / serialRul       ORDERED, CAPPED browse lists (act_now first,
//                               then shortest remaining life). Counting them
//                               counts what was returned, not what exists,
//                               and because the healthy end is what gets cut
//                               a naive count OVERSTATES risk.
//
// deviceRul and serialRul take a device type on purpose. Without it the
// server 3,000-row cap is shared across three fleets and the largest one
// eats it -- an unsplit call returns 2,539 validators, 416 TVMs and 45 of
// 452 gates, so 90% of the gate fleet disappears silently. One call per
// fleet gives each its own cap.
//
// limit is sent optimistically. A deployment that predates the limit param
// ignores it and returns its own cap; the caller detects a full page and
// says so rather than presenting a slice as a total.
export const ps5 = {
  summary: (city) => getRows('/ps5/summary', { city }),
  componentSummary: (city) => getRows('/ps5/component-summary', { city }),
  deviceRul: (city, deviceType, limit = 12000) =>
    getRows('/ps5/device-rul', { city, device_type: deviceType, limit }),
  serialRul: (city, deviceType, limit = 12000) =>
    getRows('/ps5/serial-rul', { city, device_type: deviceType, limit }),
  leaderboard: (city) => getRows('/ps5/leaderboard', { city }),
  importance: (city) => getRows('/ps5/importance', { city }),
  coverage: (city) => getRows('/ps5/coverage', { city }),
  serialGrain: (city) => getRows('/ps5/serial-grain', { city }),
  // The MLflow registry state. Its concordance_index is a DIFFERENT
  // measurement from the leaderboard oot_cindex -- promoted model vs best
  // candidate -- so the two are shown side by side and never averaged.
  status: (city) => getRows('/ps5/status', { city }),
};

// ---- ServiceNow -------------------------------------------------------
// The route STAGES an incident row; it does not post to ServiceNow. The
// UI says exactly that rather than implying a ticket was created.
export const servicenow = {
  stage: (city, deviceId, deviceCategory, shortDescription, payload) =>
    post('/ps1/servicenow-stage', {
      device_id: deviceId,
      device_category: deviceCategory,
      short_description: shortDescription,
      payload,
    }, { city }),
  staged: (city) => getRows('/ps1/servicenow-staged', { city }),
};
