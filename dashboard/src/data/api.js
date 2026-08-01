// ============================================================================
// Live API client for the CUBIC MARS dashboard.
// Fetches from the cubic-mars-dashboard-api Lambda (via API Gateway) set in
// VITE_API_BASE_URL. Each fetcher maps the flat API rows back into the exact
// shape the existing tabs expect, so tab JSX is unchanged.
//
// 2026-07-26 -- PS1/PS3/PS5 no longer fall back to mock data. A failed or
// unconfigured request now THROWS ApiError; callers must render an explicit
// unavailable state. Silently substituting fabricated numbers for live ones is
// indistinguishable from real data on screen and is treated as a data leak.
// PS2/PS4 still use the legacy fallback until their own cleanup pass.
//   Deployed 2026-07-13: https://a9yuqt9j9b.execute-api.us-east-1.amazonaws.com
// ============================================================================
import { useState, useEffect } from 'react';
import {
  getPS2CascadeWindowDistribution,
  getPS2SubsystemHub,
  getPS2FacilityContagion,
  getPS2AssociationRules,
  getPS2HMMRegimes,
  getPS2WindowDetail,
  getPS2TopDevices,
} from './mockData';

const BASE = (import.meta.env.VITE_API_BASE_URL || '').replace(/\/$/, '');
export const API_ENABLED = !!BASE;

const N = (v) => (v === null || v === undefined ? null : Number(v));

/** Thrown by every live-only (PS1/PS3/PS5) fetcher. Carries enough detail for a
 *  panel to tell the user exactly which route is unavailable. */
export class ApiError extends Error {
  constructor(route, reason, status) {
    super(`${route}: ${reason}`);
    this.name = 'ApiError';
    this.route = route;
    this.reason = reason;
    this.status = status ?? null;
  }
}

/** Live-only GET. Never returns a sentinel that a caller could mistake for
 *  "use mock" - an unconfigured base or a bad response raises. */
async function apiGetStrict(path, city) {
  if (!BASE) throw new ApiError(path, 'VITE_API_BASE_URL is not configured', null);
  let res;
  try {
    res = await fetch(`${BASE}${path}?city=${encodeURIComponent(city)}`);
  } catch (e) {
    throw new ApiError(path, `network error: ${e && e.message ? e.message : e}`, null);
  }
  if (!res.ok) throw new ApiError(path, `HTTP ${res.status}`, res.status);
  return res.json();
}

async function apiGet(path, city) {
  if (!BASE) return undefined; // signal "no API configured" -> caller uses mock
  const res = await fetch(`${BASE}${path}?city=${encodeURIComponent(city)}`);
  if (!res.ok) throw new Error(`API ${path} -> ${res.status}`);
  return res.json();
}

const WINDOW_ORDER = ['0-5min', '5-15min', '15-30min', '30-60min', '60min+'];

// ---- PS2 fetchers (map API -> mock-shaped objects; fall back to mock) -------
export async function apiPS2Windows(city) {
  try {
    const rows = await apiGet('/ps2/windows', city);
    if (rows === undefined) return getPS2CascadeWindowDistribution(city);
    if (!Array.isArray(rows) || rows.length === 0) return null;
    const sorted = [...rows].sort(
      (a, b) => WINDOW_ORDER.indexOf(a.window_bucket) - WINDOW_ORDER.indexOf(b.window_bucket)
    );
    return {
      total_cascade_days: N(sorted[0].total_cascade_days),
      windows: sorted.map((r) => ({ window: r.window_bucket, days: N(r.cascade_days), pct: N(r.pct) })),
      slow_vs_fast_fault_multiplier: N(sorted[0].slow_fast_fault_mult),
      slow_vs_fast_duration_multiplier: N(sorted[0].slow_fast_duration_mult),
    };
  } catch { return getPS2CascadeWindowDistribution(city); }
}

export async function apiPS2Hub(city) {
  try {
    const d = await apiGet('/ps2/hub', city);
    if (d === undefined) return getPS2SubsystemHub(city);
    if (!d || !Array.isArray(d.nodes) || d.nodes.length === 0) return null;
    return {
      hub_pair: d.nodes.filter((n) => n.is_hub).map((n) => n.node_id),
      nodes: d.nodes.map((n) => ({ id: n.node_id, freq: N(n.freq) })),
      edges: (d.edges || []).map((e) => ({ source: e.source_sub, target: e.target_sub, phi: N(e.phi) })),
    };
  } catch { return getPS2SubsystemHub(city); }
}

export async function apiPS2Facility(city) {
  try {
    const d = await apiGet('/ps2/facility', city);
    if (d === undefined) return getPS2FacilityContagion(city);
    if (!d || d.total_facility_cascade_days === undefined) return null;
    return {
      total_facility_cascade_days: N(d.total_facility_cascade_days),
      multi_device_contagion_pct: N(d.multi_device_contagion_pct),
      monthly_contagion_rate_trend: { start_pct: N(d.trend_start_pct), end_pct: N(d.trend_end_pct) },
      top_hotspot: {
        facility_id: N(d.hotspot_facility_id),
        facility_name: d.hotspot_facility_name,
        min_devices_same_day: N(d.hotspot_min_devices),
        max_devices_same_day: N(d.hotspot_max_devices),
      },
    };
  } catch { return getPS2FacilityContagion(city); }
}

export async function apiPS2AssociationRules(city) {
  try {
    const rows = await apiGet('/ps2/associations', city);
    if (rows === undefined) return getPS2AssociationRules(city);
    if (!Array.isArray(rows) || rows.length === 0) return null;
    return rows.map((r) => ({
      antecedent: r.antecedent_subsystem,
      consequent: r.consequent_subsystem,
      support: N(r.support), confidence: N(r.confidence), lift: N(r.lift), conviction: N(r.conviction),
    }));
  } catch { return getPS2AssociationRules(city); }
}

export async function apiPS2HMMRegimes(city) {
  try {
    const rows = await apiGet('/ps2/hmm', city);
    if (rows === undefined) return getPS2HMMRegimes(city);
    if (!Array.isArray(rows) || rows.length === 0) return null;
    return rows.map((r) => ({
      regime: r.regime, pct: N(r.pct),
      dwell_days_min: N(r.dwell_days_min), dwell_days_max: N(r.dwell_days_max),
    }));
  } catch { return getPS2HMMRegimes(city); }
}

const DEVICE_LABEL = { gates: 'Gates', tvms: 'TVMs', validators: 'Validators' };

// PS5: device rows come live from the API; the static explanatory shared_blockers /
// interpretation copy stays sourced from mockData (it is documentation, not model output).
export async function apiPS5Status(city) {
  const rows = await apiGetStrict('/ps5/status', city);
  if (!Array.isArray(rows) || rows.length === 0) return null;
  return {
    devices: rows.map((d) => ({
      device: DEVICE_LABEL[d.device_type] || d.device_type,
      concordance_index: N(d.concordance_index),
      registry_status: d.registry_status,
      dashboard_ready: d.dashboard_ready,
      blockers: d.blockers ? [d.blockers] : [],
    })),
    shared_blockers: Array.isArray(rows[0].shared_blockers) ? rows[0].shared_blockers : [],
    interpretation: rows[0].interpretation || '',
  };
}

// PS5 v5 reliability (RUL/survival on the hardware-OOS-Set event). Device rows come live from
// /ps5/reliability; falls back to the mock detail (illustrative pending the live v5 run).
export async function apiPS5Reliability(city) {
  const d = await apiGetStrict('/ps5/reliability', city);
  if (!d || !Array.isArray(d.devices) || d.devices.length === 0) return null;
  return {
    event_definition: d.event_definition,
    event_def_version: d.event_def_version,
    window: d.window,
    floor: N(d.floor) ?? 0.65,
    is_sample: false,
    devices: d.devices.map((x) => ({
      device: DEVICE_LABEL[x.device_type] || x.device,
      cv_cindex: N(x.cv_cindex),
      champion: x.champion,
      gate_pass: x.gate_pass === true || x.gate_pass === 't' || x.gate_pass === 'true',
      rul_median_days: N(x.rul_median_days),
      rul_p10_days: N(x.rul_p10_days),
      rul_p90_days: N(x.rul_p90_days),
      days_since_fail: N(x.days_since_fail),
      roll_fail_30d: N(x.roll_fail_30d),
      n_events: N(x.n_events),
      ibs: N(x.ibs),
      survival: Array.isArray(x.survival) ? x.survival.map((p) => ({ day: N(p.day), surv: N(p.surv) })) : [],
    })),
    note: d.note || '',
  };
}

const CAT_LABEL = { TVM: 'TVMs', GATE: 'Gates', VALIDATOR: 'Validators' };

// PS5 v5.1 device-grain RUL (hardware-OOS-Set). Live from /ps5/devices; mock fallback (labelled sample).
export async function apiPS5DeviceRUL(city) {
  const d = await apiGetStrict('/ps5/devices', city);
  if (!d || !Array.isArray(d.devices) || d.devices.length === 0) return null;
  return {
    is_sample: false, event_definition: d.event_definition, event_def_version: d.event_def_version,
    floor: N(d.floor) ?? 0.65, window: d.window,
    devices: d.devices.map((x) => ({
      device_id: x.device_id,
      device_type: CAT_LABEL[x.mars_device_category] || x.device_type || x.mars_device_category,
      mars_device_category: x.mars_device_category, facility_id: x.facility_id,
      current_age_days: N(x.current_healthy_age_days ?? x.current_age_days),
      rul_days: N(x.rul_standard_days ?? x.rul_days),
      rul_p10: N(x.rul_p10 ?? x.rul_p10_days), rul_p90: N(x.rul_p90 ?? x.rul_p90_days),
      hazard_score: N(x.hazard_score), risk_band: x.risk_band,
      days_since_hw_oos: N(x.days_since_hw_oos), roll_fail_30d: N(x.roll_fail_30d),
      is_overdue: x.is_overdue === true || x.is_overdue === 't' || x.is_overdue === 'true',
      cv_cindex: N(x.concordance_index ?? x.cv_cindex),
      gate_pass: x.data_quality_gate_passed === true || x.gate_pass === true,
    })),
    note: d.note || '',
  };
}

// PS5 v5.1 serial-grain health (hardware-OOS-Set). Live from /ps5/serials; mock fallback (labelled sample).
export async function apiPS5SerialHealth(city) {
  const d = await apiGetStrict('/ps5/serials', city);
  if (!d || !Array.isArray(d.serials) || d.serials.length === 0) return null;
  return {
    is_sample: false, event_def_version: d.event_def_version,
    serials: d.serials.map((x) => ({
      device_id: x.device_id, serial: x.component_serial_nbr ?? x.serial,
      component_type: x.component_type ?? x.component_type_name, mars_device_category: x.mars_device_category,
      component_age_days: N(x.component_age_days),
      oos_failures: N(x.device_oos_failures_total ?? x.oos_failures),
      risk_score: N(x.risk_score), risk_tier: x.risk_tier,
      component_rul_days: N(x.expected_component_rul_days ?? x.component_rul_days),
      is_overdue: x.is_overdue === true || x.is_overdue === 't' || x.is_overdue === 'true',
    })),
    note: d.note || '',
  };
}

// ---- PS3 FAILURE SEVERITY (severity classifier, NOT root cause) -------------
export async function apiPS3Summary(city) {
  const d = await apiGetStrict('/ps3/summary', city);
  if (!d || !d.champion_model) return null;
  return {
    champion_model: d.champion_model,
    test_auc_macro: N(d.test_auc_macro),
    test_f1_macro: N(d.test_f1_macro),
    test_accuracy: N(d.test_accuracy),
    n_incidents: N(d.n_incidents),
    n_major: N(d.n_major),
    n_critical: N(d.n_critical),
    device_note: d.device_note,
    date_start: d.date_start,
    date_end: d.date_end,
    feasibility_pct: N(d.feasibility_pct),
    is_root_cause: d.is_root_cause,
    true_rootcause_status: d.true_rootcause_status,
    dominant_feature: d.dominant_feature,
    dominant_feature_shap: N(d.dominant_feature_shap),
    endpoint_name: d.endpoint_name,
    mlflow_version: d.mlflow_version,
    sm_package: d.sm_package,
    serving_image: d.serving_image,
    dashboard_ready: d.dashboard_ready,
  };
}

export async function apiPS3Drivers(city) {
  const rows = await apiGetStrict('/ps3/drivers', city);
  if (!Array.isArray(rows) || rows.length === 0) return null;
  return rows.map((r) => ({
    feature: r.feature,
    shap_importance: N(r.shap_importance),
    solo_auc: N(r.solo_auc),
    driver_rank: N(r.driver_rank),
  }));
}

export async function apiPS2WindowDetail(city) {
  try {
    const rows = await apiGet('/ps2/windowdetail', city);
    if (rows === undefined) return getPS2WindowDetail(city);
    if (!Array.isArray(rows) || rows.length === 0) return null;
    const sorted = [...rows].sort(
      (a, b) => WINDOW_ORDER.indexOf(a.window_bucket) - WINDOW_ORDER.indexOf(b.window_bucket)
    );
    return sorted.map((r) => ({
      window: r.window_bucket, cascade_days: N(r.cascade_days),
      chain_len_mean: N(r.chain_len_mean), chain_len_median: N(r.chain_len_median), chain_len_max: N(r.chain_len_max),
      span_min_mean: N(r.span_min_mean), span_min_median: N(r.span_min_median), velocity: N(r.velocity_min_per_fault),
    }));
  } catch { return getPS2WindowDetail(city); }
}

export async function apiPS2TopDevices(city) {
  try {
    const rows = await apiGet('/ps2/topdevices', city);
    if (rows === undefined) return getPS2TopDevices(city);
    if (!Array.isArray(rows) || rows.length === 0) return null;
    // 27-Jul-2026. /ps2/topdevices was repointed from ps2_top_devices -- one
    // stale seed row that the pipeline never loaded -- to v_ps2_device_cascade,
    // built from the 4,673 devices the 27-Jul S3 load actually put in Aurora.
    //
    // w0_5..w60plus stay mapped but are NOT defaulted to zero. The PS2 export
    // carries no per-device time-window split; the only velocity breakdown in
    // the run is fleet-level. N(undefined) is null here, so the panel can ask
    // "do I have this?" and say so, rather than drawing five empty bars that
    // would read as "this device never cascaded quickly".
    return rows.map((r) => ({
      device_id: r.device_id, category: r.category, cascade_days: N(r.cascade_days),
      total_impact: N(r.total_impact), avg_impact: N(r.avg_impact),
      recurrence_cascade_days: N(r.recurrence_cascade_days),
      chronic: r.chronic === true || r.chronic === 'true' || r.chronic === 'True',
      impact_rank: N(r.impact_rank),
      impact_rank_in_category: N(r.impact_rank_in_category),
      w0_5: N(r.w0_5), w5_15: N(r.w5_15), w15_30: N(r.w15_30), w30_60: N(r.w30_60), w60plus: N(r.w60plus),
      dev_rank: N(r.dev_rank),
    }));
  } catch { return getPS2TopDevices(city); }
}

// ---- hook: instant mock render, then swap to live data when it arrives ------
/** Live-only data hook: no seeded mock, explicit loading/error/empty states.
 *  Returns { data, loading, error }. `error` is an ApiError when the route is
 *  unavailable; panels must render that rather than showing anything invented. */
export function useLiveQuery(fetcher, deps) {
  const [state, setState] = useState({ data: undefined, loading: true, error: null });
  useEffect(() => {
    let alive = true;
    setState({ data: undefined, loading: true, error: null });
    Promise.resolve()
      .then(fetcher)
      .then((v) => { if (alive) setState({ data: v, loading: false, error: null }); })
      .catch((e) => { if (alive) setState({ data: undefined, loading: false, error: e }); });
    return () => { alive = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);
  return state;
}

export function useLiveData(initial, fetcher, deps) {
  const [data, setData] = useState(initial);
  useEffect(() => {
    let alive = true;
    Promise.resolve(fetcher())
      .then((v) => { if (alive && v !== undefined) setData(v); })
      .catch(() => {});
    return () => { alive = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);
  return data;
}

// ============================================================================
// Phase-1e/1f rich PS2 fetchers (cascade paths, ignition->termination, business
// impact, correlation, conditional prob, markov, network, error codes, devices).
// Same contract: map API rows -> the shape the components expect; fall back to
// the real-anchored mock on unset/unreachable API.
// ============================================================================
import {
  getPS2CascadePaths, getPS2IgnitionTermination, getPS2BusinessImpact,
  getPS2Phi, getPS2Network, getPS2Markov, getPS2Conditional, getPS2ErrorCodes, getPS2Devices,
} from './mockData';

export async function apiPS2Paths(city) {
  try {
    const rows = await apiGet('/ps2/paths', city);
    if (rows === undefined) return getPS2CascadePaths();
    if (!Array.isArray(rows)) return getPS2CascadePaths();
    return rows.map((d) => ({
      path_rank: N(d.path_rank), cascade_path: String(d.cascade_path || '').replace(/->/g, ' -> '),
      first_subsystem: d.first_subsystem, last_subsystem: d.last_subsystem,
      occurrences: N(d.occurrences), pct_of_chains: N(d.pct_of_chains),
    }));
  } catch { return getPS2CascadePaths(); }
}
export async function apiPS2Ignition(city) {
  try {
    const rows = await apiGet('/ps2/ignition', city);
    if (rows === undefined) return getPS2IgnitionTermination();
    if (!Array.isArray(rows)) return getPS2IgnitionTermination();
    return rows.map((d) => ({
      subsystem: d.subsystem, rank: N(d.rank), ignition_days: N(d.ignition_days),
      termination_days: N(d.termination_days), ignition_pct: N(d.ignition_pct),
      termination_pct: N(d.termination_pct), net_role: d.net_role,
    }));
  } catch { return getPS2IgnitionTermination(); }
}
export async function apiPS2Impact(city) {
  try {
    const rows = await apiGet('/ps2/impact', city);
    if (rows === undefined) return getPS2BusinessImpact();
    if (!Array.isArray(rows)) return getPS2BusinessImpact();
    return rows.map((d) => ({
      impact_rank: N(d.impact_rank), device_id: d.device_id, category: d.category,
      cascade_days: N(d.cascade_days), total_impact: N(d.total_impact),
      avg_impact: N(d.avg_impact), max_impact: N(d.max_impact),
    }));
  } catch { return getPS2BusinessImpact(); }
}
export async function apiPS2Phi(city) {
  try {
    const rows = await apiGet('/ps2/phi', city);
    if (rows === undefined || !Array.isArray(rows)) return getPS2Phi();
    return rows.map((d) => ({ sub_a: d.sub_a, sub_b: d.sub_b, phi: N(d.phi) }));
  } catch { return getPS2Phi(); }
}
export async function apiPS2Markov(city) {
  try {
    const rows = await apiGet('/ps2/markov', city);
    if (rows === undefined || !Array.isArray(rows)) return getPS2Markov();
    return rows.map((d) => ({ from_sub: d.from_sub, to_sub: d.to_sub, prob: N(d.prob) }));
  } catch { return getPS2Markov(); }
}
export async function apiPS2Conditional(city) {
  try {
    const rows = await apiGet('/ps2/conditional', city);
    if (rows === undefined || !Array.isArray(rows)) return getPS2Conditional();
    return rows.map((d) => ({ sub_a: d.sub_a, sub_b: d.sub_b, window_bucket: d.window_bucket, p_b_given_a: N(d.p_b_given_a) }));
  } catch { return getPS2Conditional(); }
}
export async function apiPS2Network(city) {
  try {
    const rows = await apiGet('/ps2/network', city);
    if (rows === undefined || !Array.isArray(rows)) return getPS2Network();
    return rows.map((d) => ({ node_id: d.node_id, betweenness: N(d.betweenness), pagerank: N(d.pagerank),
      in_degree: N(d.in_degree), out_degree: N(d.out_degree), role: d.role }));
  } catch { return getPS2Network(); }
}
export async function apiPS2ErrorCodes(city) {
  try {
    const r = await apiGet('/ps2/errorcodes', city);
    if (r === undefined || !r) return getPS2ErrorCodes();
    return {
      codes: (r.codes || []).map((d) => ({ error_code: String(d.error_code), occurrences: N(d.occurrences), top_subsystem: d.top_subsystem, pct: N(d.pct) })),
      transitions: (r.transitions || []).map((d) => ({ from_code: String(d.from_code), to_code: String(d.to_code), occurrences: N(d.occurrences) })),
    };
  } catch { return getPS2ErrorCodes(); }
}
export async function apiPS2Devices(city) {
  try {
    const rows = await apiGet('/ps2/devices', city);
    if (rows === undefined || !Array.isArray(rows)) return getPS2Devices();
    return rows;
  } catch { return getPS2Devices(); }
}
export async function apiPS2DeviceCascades(city, deviceId) {
  try {
    if (!BASE) return [];
    const res = await fetch(`${BASE}/ps2/devicecascades?city=${encodeURIComponent(city)}&device=${encodeURIComponent(deviceId || '')}`);
    if (!res.ok) return [];
    const rows = await res.json();
    return Array.isArray(rows) ? rows : [];
  } catch { return []; }
}

// ---- PS1 training-results fetchers (honest not-promoted view) ----------------

export async function apiPS1Summary(city) {
  const rows = await apiGetStrict('/ps1/summary', city);
  if (!Array.isArray(rows) || rows.length === 0) return null;
  return rows.map((r) => ({
    device: r.device, champion_model: r.champion_model,
    test_auc: N(r.test_auc), test_ap: N(r.test_ap), test_accuracy: N(r.test_accuracy),
    test_f1: N(r.test_f1), test_precision: N(r.test_precision), test_recall: N(r.test_recall),
    op_threshold: N(r.op_threshold), op_precision: N(r.op_precision), op_recall: N(r.op_recall), op_f2: N(r.op_f2),
    recall_floor: N(r.recall_floor), quality_gate: r.quality_gate, promoted: r.promoted === true || r.promoted === 't' || r.promoted === 'true',
    auc_cal: N(r.auc_cal), map_score: N(r.map_score), overfit_flag: r.overfit_flag === true || r.overfit_flag === 't' || r.overfit_flag === 'true',
    base_rate_pct: N(r.base_rate_pct), n_train: N(r.n_train), n_test: N(r.n_test), n_test_pos: N(r.n_test_pos),
    endpoint_name: r.endpoint_name, mlflow_version: r.mlflow_version, target: r.target, run_id: r.run_id,
  }));
}
export async function apiPS1Leaderboard(city) {
  const rows = await apiGetStrict('/ps1/leaderboard', city);
  if (!Array.isArray(rows) || rows.length === 0) return null;
  return rows.map((r) => ({
    device: r.device, model: r.model, auc: N(r.auc), ap: N(r.ap), f1: N(r.f1), prec: N(r.prec), rec: N(r.rec),
    lb_rank: N(r.lb_rank), is_champion: r.is_champion === true || r.is_champion === 't' || r.is_champion === 'true', note: r.note || '',
  }));
}
export async function apiPS1Features(city) {
  const rows = await apiGetStrict('/ps1/features', city);
  if (!Array.isArray(rows) || rows.length === 0) return null;
  return rows.map((r) => ({
    device: r.device, feature: r.feature, mean_abs_shap: N(r.mean_abs_shap), pct_total: N(r.pct_total), feat_rank: N(r.feat_rank),
  }));
}

// ============================================================================
// PS3 DEEP-DIVE fetchers (21-Jul-2026) -- device/serial risk leaderboards,
// per-entity SHAP drivers, component x severity cross-tab, rolling risk
// trend, on-demand "Score new data" (real-time endpoint via /ps3/infer), and
// the ServiceNow dispatcher. Backs PS3DeepDiveAnalytics.jsx. Same
// fetch-with-honest-empty-fallback shape as every other fetcher above; these
// are brand-new tables with no existing mock convention, so "no data yet, run
// the notebook" is more honest here than inventing numbers (same choice the
// PS2 serial-grain additions made).
// ============================================================================
export async function apiPS3Infer(city, instances) {
  if (!BASE) return { status: 'api_not_configured', predictions: [] };
  try {
    const res = await fetch(`${BASE}/ps3/infer?city=${encodeURIComponent(city)}`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ instances }),
    });
    const body = await res.json().catch(() => ({}));
    if (!res.ok) return { status: 'error', httpStatus: res.status, error: body.error || 'request failed', predictions: [] };
    return { status: 'ok', ...body };
  } catch (e) { return { status: 'error', error: String(e), predictions: [] }; }
}

export async function apiPS2SerialMetric(city, metric, params = {}) {
  if (!BASE) return [];
  try {
    const qs = new URLSearchParams({ city, ...params }).toString();
    const res = await fetch(`${BASE}/ps2/serial/${encodeURIComponent(metric)}?${qs}`);
    if (!res.ok) return [];
    const rows = await res.json();
    return Array.isArray(rows) ? rows : [];
  } catch { return []; }
}

export async function apiPS2ServiceNowStatus(city, correlationId) {
  if (!BASE || !correlationId) return { status: 'unknown' };
  try {
    const res = await fetch(`${BASE}/ps2/servicenow/status?city=${encodeURIComponent(city)}&correlation_id=${encodeURIComponent(correlationId)}`);
    if (!res.ok) return { status: 'unknown' };
    return await res.json();
  } catch { return { status: 'unknown' }; }
}

// ---- PS2-only ServiceNow sample-payload fallback (scoped strictly to PS2; ----
// ---- does not touch apiPS1*, apiPS3ServiceNow*, or apiPS5* below/above) -----
// Deterministic fake INC# from a correlation_id -- same device/window always
// reproduces the same sample number, rather than a fresh random one per click.
function ps2SampleIncNumber(correlationId) {
  let h = 0;
  for (let i = 0; i < correlationId.length; i++) h = (h * 31 + correlationId.charCodeAt(i)) >>> 0;
  return `INC${String(1000000 + (h % 9000000))}`;
}

// The exact fixed-value incwowot payload shape from the PS2 ServiceNow design
// (u_company / u_category / u_contact_type fixed; u_config_item is the
// cmdb_ci sys_id resolved from ps2_device_cmdb_map -- shown as unresolved
// while that table is still empty, per the 2026-07-26 verification).
function buildPS2SampleServiceNowPayload(city, { deviceId, serialId, window: win } = {}) {
  return {
    u_company: '199390310f4a3100471983fc22050e7a',
    u_category: 'Preventative Maintenance',
    u_subcategory: 'Preventative Maintenance',
    u_contact_type: 'Monitoring',
    u_correlation_id: `${deviceId}|PS2|${win}`,
    u_config_item: '(unresolved -- ps2_device_cmdb_map has 0 rows as of 2026-07-26)',
    short_description: `PS2 cascading-failure alert -- device ${deviceId}${serialId ? ` (serial ${serialId})` : ''}`,
    city,
  };
}

export async function apiPS2ServiceNowCreateIncident(city, { deviceId, serialId, window: win }) {
  const correlationId = `${deviceId}|PS2|${win}`;
  const sample = () => ({
    status: 'sample',
    is_sample: true,
    inc_number: ps2SampleIncNumber(correlationId),
    correlation_id: correlationId,
    submitted_payload: buildPS2SampleServiceNowPayload(city, { deviceId, serialId, window: win }),
    note: 'ServiceNow Integration awaited -- sample payload shown, not filed against ctsdev2cubic (live credentials not yet provisioned; ps2_device_cmdb_map also has 0 rows).',
  });
  if (!BASE) return sample();
  try {
    const res = await fetch(`${BASE}/ps2/servicenow/create-incident?city=${encodeURIComponent(city)}`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ device_id: deviceId, serial_id: serialId, window: win, ps_source: 'PS2' }),
    });
    const body = await res.json().catch(() => ({}));
    if (!res.ok) return sample();
    if (!body || !body.status) return sample();
    return { status: body.status || 'created', inc_number: body.inc_number, already_filed: body.already_filed === true };
  } catch (e) { return sample(); }
}

// ============================================================================
// Serial -> device resolution (22-Jul-2026), for the "Analyse" button rollout.
// PS2's serial-grain tables (chronic recurrence, lead/lag, association rules,
// etc.) carry serial_id only -- Device360Modal is device-keyed, so opening a
// cross-PS analysis from a serial row means resolving serial_id -> device_id
// first. ps2_device_cmdb_map already carries both columns (device_id,
// serial_id, cmdb_ci_sys_id) -- it was built for the ServiceNow dispatcher's
// cmdb_ci lookup, and doubles perfectly as this map. Reads it via the same
// generic /ps2/serial/:metric route -- the 'cmdb' entry is confirmed present
// in the allow-list on cubic-mars-dashboard-api (verified 24-Jul-2026 via the
// deployed handler.py). Row-level population of ps2_device_cmdb_map itself has
// NOT yet been smoke-tested end-to-end -- if that table is still empty,
// apiPS2SerialMetric(city, 'cmdb') returns [] -> the map is empty -> every
// AnalyseButton on a serial-only row renders disabled with an honest "no
// device mapping yet" tooltip rather than guessing or crashing.
export function useSerialDeviceMap(city) {
  const [map, setMap] = useState({});
  useEffect(() => {
    let alive = true;
    apiPS2SerialMetric(city, 'cmdb').then((rows) => {
      if (!alive) return;
      const m = {};
      (rows || []).forEach((r) => { if (r.serial_id && r.device_id) m[r.serial_id] = r.device_id; });
      setMap(m);
    });
    return () => { alive = false; };
  }, [city]);
  return map;
}

// 2026-07-26 -- removed: apiPS3DeepdiveMetric, apiPS3Device360Deepdive, apiPS3ServiceNowStatus, apiPS3ServiceNowCreateIncident.
// Each called a /ps3/... route with no server handler, so every invocation
// 404'd. Nothing referenced them. PS3 ServiceNow staging is now a real
// route (/ps3/servicenow-stage) and is called from DashboardKit's
// ServiceNowButton, which both PS1 and PS3 use.
