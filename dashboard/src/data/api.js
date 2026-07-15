// ============================================================================
// Live API client for the CUBIC MARS dashboard (Phase-1: PS2 + PS3 + PS5).
// Fetches from the cubic-mars-dashboard-api Lambda (via API Gateway) set in
// VITE_API_BASE_URL. Each fetcher maps the flat API rows back into the exact
// shape the existing tabs expect, so tab JSX is unchanged. If the API base is
// unset or a request fails, it transparently falls back to the mock data — so
// `npm run dev` without the API still works, and non-CHI cities behave as before.
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
  getPS5ReliabilityStatus,
  getPS3SeveritySummary,
  getPS3SeverityDrivers,
} from './mockData';

const BASE = (import.meta.env.VITE_API_BASE_URL || '').replace(/\/$/, '');
export const API_ENABLED = !!BASE;

const N = (v) => (v === null || v === undefined ? null : Number(v));

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
  try {
    const rows = await apiGet('/ps5/status', city);
    if (rows === undefined) return getPS5ReliabilityStatus(city);
    if (!Array.isArray(rows) || rows.length === 0) return null;
    const mock = getPS5ReliabilityStatus(city) || {};
    return {
      devices: rows.map((d) => ({
        device: DEVICE_LABEL[d.device_type] || d.device_type,
        concordance_index: N(d.concordance_index),
        registry_status: d.registry_status,
        dashboard_ready: d.dashboard_ready,
        blockers: d.blockers ? [d.blockers] : [],
      })),
      shared_blockers: mock.shared_blockers || [],
      interpretation: mock.interpretation || '',
    };
  } catch { return getPS5ReliabilityStatus(city); }
}

// ---- PS3 FAILURE SEVERITY (severity classifier, NOT root cause) -------------
export async function apiPS3Summary(city) {
  try {
    const d = await apiGet('/ps3/summary', city);
    if (d === undefined) return getPS3SeveritySummary(city);
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
  } catch { return getPS3SeveritySummary(city); }
}

export async function apiPS3Drivers(city) {
  try {
    const rows = await apiGet('/ps3/drivers', city);
    if (rows === undefined) return getPS3SeverityDrivers(city);
    if (!Array.isArray(rows) || rows.length === 0) return null;
    return rows.map((r) => ({
      feature: r.feature,
      shap_importance: N(r.shap_importance),
      solo_auc: N(r.solo_auc),
      driver_rank: N(r.driver_rank),
    }));
  } catch { return getPS3SeverityDrivers(city); }
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
    return rows.map((r) => ({
      device_id: r.device_id, category: r.category, cascade_days: N(r.cascade_days),
      w0_5: N(r.w0_5), w5_15: N(r.w5_15), w15_30: N(r.w15_30), w30_60: N(r.w30_60), w60plus: N(r.w60plus),
      dev_rank: N(r.dev_rank),
    }));
  } catch { return getPS2TopDevices(city); }
}

// ---- hook: instant mock render, then swap to live data when it arrives ------
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
import { getPS1FailureSummary, getPS1Leaderboard, getPS1Features } from './mockData';

export async function apiPS1Summary(city) {
  try {
    const rows = await apiGet('/ps1/summary', city);
    if (rows === undefined || !Array.isArray(rows) || rows.length === 0) return getPS1FailureSummary();
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
  } catch { return getPS1FailureSummary(); }
}
export async function apiPS1Leaderboard(city) {
  try {
    const rows = await apiGet('/ps1/leaderboard', city);
    if (rows === undefined || !Array.isArray(rows) || rows.length === 0) return getPS1Leaderboard();
    return rows.map((r) => ({
      device: r.device, model: r.model, auc: N(r.auc), ap: N(r.ap), f1: N(r.f1), prec: N(r.prec), rec: N(r.rec),
      lb_rank: N(r.lb_rank), is_champion: r.is_champion === true || r.is_champion === 't' || r.is_champion === 'true', note: r.note || '',
    }));
  } catch { return getPS1Leaderboard(); }
}
export async function apiPS1Features(city) {
  try {
    const rows = await apiGet('/ps1/features', city);
    if (rows === undefined || !Array.isArray(rows) || rows.length === 0) return getPS1Features();
    return rows.map((r) => ({
      device: r.device, feature: r.feature, mean_abs_shap: N(r.mean_abs_shap), pct_total: N(r.pct_total), feat_rank: N(r.feat_rank),
    }));
  } catch { return getPS1Features(); }
}
