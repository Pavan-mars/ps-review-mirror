// =====================================================================
// V4DeviceW -- WHERE / WHEN / WHAT / WHY for one device and its parts.
// Pure functions only; the renderers live in V4DeviceWCards.jsx so this
// module can be imported by the popup, the tab and any test without React.
//
// WHY THIS FILE EXISTS.                                        11-Sep-2026
// The Device 360 tab rendered one panel per analysis and the popup rendered
// three cards, and neither answered the four questions a technician asks in
// the order they ask them. Worse, the two screens read different sources:
// the popup took its station from whichever PS block happened to carry a
// facility_id, so a device Failure Prediction never scored showed "Not
// recorded" for a station the device dimension knows perfectly well. The
// component-level view was Remaining Useful Life only; the Root Cause
// Analysis attribution per serial was summarised into one sentence and its
// per-part table never reached the screen.
//
// ONE NORMALISER, TWO RENDERERS. `deviceProfile` and `mergeComponents` read
// the precomputed row (device_level_aggregation, sql/59) first and fall back
// to the composite (/ps1/device-360) field by field, so an API without the
// fast path still fills every question it can. Both the tab and the popup
// call the same two functions -- the same rule V4Evidence enforces for the
// evidence lines -- so the two views of a device cannot disagree.
//
// GRAIN. Device cards are one device. The component table is ONE ROW PER
// SERIAL, never per (serial, label): a serial can carry several attributed
// components (12f6850 carries BHU and CHU), and repeating its remaining-life
// figures on each label row is the fan-out this estate has already paid for
// once. Labels are listed inside the row instead.
//
// TWO KINDS OF WHY, and the words keep them apart. SHAP drivers are MODEL
// EXPLANATION. Component attribution is OBSERVED from incident history and
// PS3 says so in its own field (component_label_semantics). Confirmed root
// cause would come from ServiceNow incident linkage, which has not run, so
// the card says that rather than leaving a blank the reader might fill in.
// =====================================================================
import { nfmt } from './V4theme';

const has = (v) => v !== null && v !== undefined && v !== '' && v !== '--' && v !== 'None' && v !== 'nan' && v !== 'NaN';
const num = (v) => (has(v) && Number.isFinite(Number(v)) ? Number(v) : null);
const first = (...xs) => xs.find(has);
const arr = (v) => (Array.isArray(v) ? v : []);

// Plain-language names for Failure Prediction's feature columns. One map,
// used by the tab and the popup.
export const DRIVER_LABEL = {
  usage_cumulative_failure_count: 'Lifetime failures',
  roll_fail_90d: 'Failures, 90 days',
  roll_fail_30d: 'Failures, 30 days',
  roll_fail_7d: 'Failures, 7 days',
  hardware_oos_count_prior_sum_7d: 'Out of service, 7 days',
  days_since_hw_oos: 'Since last outage',
  days_since_fail: 'Days since last failure',
  availability_pct_7d: 'Availability, 7 days',
  current_healthy_age_days: 'Days fault-free',
  n_prior_oos: 'Prior outages',
  component_age_days: 'Part age',
  cascade_days: 'Cascade days',
  device_age_days: 'Device age',
  device_fail_rate_30d: 'Failure rate, 30 days',
};
export const driverLabel = (f) => DRIVER_LABEL[f] || String(f || '').replace(/_/g, ' ');

const ATTRIBUTION_NOTE = 'Observed attribution from incident history, not a confirmed root cause.';
const LINKED_NOTE = 'A confirmed cause would come from ServiceNow incident linkage, which has not run for this estate.';

// ---------------------------------------------------------------------
// The normaliser. `agg` is the /device/aggregate row (or null), `d` the
// composite. Every field falls back individually.
// ---------------------------------------------------------------------
export function deviceProfile(agg, d) {
  const a = agg || {};
  const o = d || {};
  const ident = o.identity || {};
  const p1 = o.ps1 || {};
  const p1s = o.ps1_state || {};
  const p2 = o.ps2 || {};
  const p3 = o.ps3 || {};
  const p4 = o.ps4_v3_360 || {};
  const p5 = o.ps5 || {};
  const xp = o.cross_ps || {};
  const snh = o.servicenow_history || {};

  const comps = mergeComponents(agg, d);
  const latestAttributed = comps.map((c) => c.latest_incident_at).filter(has).sort().pop() || null;

  const weeks = arr(p4.weeks);
  const lastWeek = weeks.length ? weeks[weeks.length - 1] : null;
  // Ranked on max_fault_z, falling back to max_abs_z for rows written before
  // 23-Sep-2026. max_abs_z is |z| across all five metrics while three of the
  // five signals are one-sided, so an unusually HEALTHY week can carry the
  // largest max_abs_z and win this reduce -- which then reports its severity
  // (Normal) as the device's worst. See V4Device360.jsx for the full note.
  const w4rank = (w) => (num(w.max_fault_z != null ? w.max_fault_z : w.max_abs_z) || 0);
  const worstWeek = weeks.length
    ? weeks.reduce((x, y) => (w4rank(y) > w4rank(x) ? y : x))
    : null;

  const where = {
    facility_id: first(a.facility_id, ident.facility_id, p1.facility_id, p5.facility_id, p2.facility),
    facility_name: first(a.facility_name, ident.facility_name),
    operator: first(a.operator_name, ident.operator_name),
    mode: first(a.transit_mode_name),
    bus_id: first(a.bus_id, (o.bus_identity || {}).bus_id),
    fleet: first(a.mars_device_category, ident.mars_device_category, p1.device_category, p5.category),
    type: first(a.device_type_name, ident.device_type_name, p1s.device_type),
    serial: first(a.serial_number, ident.serial_number, a.component_serial_nbr, ident.component_serial_nbr),
    component_type: first(a.component_type, ident.component_type),
    cmdb_ci: first(a.cmdb_ci_sys_id, ident.cmdb_ci_sys_id, snh.cmdb_ci_sys_id),
    source_date: first(a.dim_as_of_date, ident.as_of_date),
  };

  const when = {
    ps1_scored: first(a.ps1_transit_day, p1s.last_scored_day, p1.prediction_date),
    ps1_last_failure: first(a.ps1_last_failure_date),
    ps3_latest_incident: first(latestAttributed, p3.last_incident_dtm),
    ps4_week: first(a.ps4_week_start, lastWeek && lastWeek.week_start),
    ps5_asof: first(a.ps5_asof_date, p5.as_of_date),
    sn_latest: first(a.sn_latest_opened_at, snh.latest_opened_at),
    dim_asof: first(a.dim_as_of_date, ident.as_of_date),
    vintage_oldest: first(a.vintage_oldest),
    vintage_newest: first(a.vintage_newest),
    vintage_spread: num(a.vintage_spread_days),
  };
  if (when.vintage_spread === null) {
    // No fast row: derive the guard from whatever dates the composite gave.
    const ds = [when.ps1_scored, when.ps4_week, when.ps5_asof, when.dim_asof]
      .filter(has).map((x) => new Date(`${String(x).slice(0, 10)}T00:00:00Z`).getTime())
      .filter((t) => Number.isFinite(t));
    if (ds.length >= 2) {
      const lo = Math.min(...ds); const hi = Math.max(...ds);
      when.vintage_oldest = new Date(lo).toISOString().slice(0, 10);
      when.vintage_newest = new Date(hi).toISOString().slice(0, 10);
      when.vintage_spread = Math.round((hi - lo) / 86400000);
    }
  }

  const what = {
    // agg.signal_count counts FOUR independent analyses and excludes Failure
    // Prediction on purpose; cross_ps.signal_count includes it. They are not
    // the same number, so the label carries the denominator only when it is
    // the four-signal count.
    signal_count: num(a.signal_count),
    signal_of: has(a.signal_count) ? 4 : null,
    xps_signal_count: num(xp.signal_count),
    ps1_prob: first(num(a.ps1_fail_prob), num(p1.failure_probability)),
    ps1_tier: first(a.ps1_risk_tier, p1.risk_band),
    ps1_above: has(a.ps1_predicted) ? Number(a.ps1_predicted) === 1 : (has(p1.predicted_label) ? Number(p1.predicted_label) === 1 : null),
    ps2_cascade_days: num(a.ps2_cascade_days),
    ps2_rank: num(p2.cascade_rank),
    ps3_incidents: first(num(a.ps3_incident_count), num(p3.n_incidents)),
    ps3_action: first(a.ps3_action_band),
    ps3_severity: first(a.ps3_predicted_severity, p3.dominant_pred_severity),
    ps3_recurrence_30d: num(a.ps3_recurrence_30d),
    ps4_severity: first(a.ps4_severity, worstWeek && worstWeek.severity),
    ps4_actionable: has(a.ps4_is_actionable_week) ? Number(a.ps4_is_actionable_week) === 1 : null,
    ps4_types: first(a.ps4_anomaly_types),
    ps5_band: first(a.ps5_risk_band, p5.risk_band),
    ps5_rul: first(num(a.ps5_rul_days), num(p5.rul_standard_days)),
    ps5_median: first(num(a.ps5_median_survival_days), num(p5.predicted_median_survival_days)),
    ps5_overdue: has(a.ps5_is_overdue) ? !!a.ps5_is_overdue : (has(p5.is_overdue) ? !!p5.is_overdue : null),
    sn_count: first(num(a.sn_incident_count), num(snh.incident_count)),
    sn_latest: first(a.incident_number, snh.latest_incident),
  };

  const rawDrivers = arr(a.ps1_drivers_json).length ? arr(a.ps1_drivers_json) : arr(p1.drivers);
  const drivers = rawDrivers
    .map((x) => ({ feature: x.feature || x.feature_name, shap: Math.abs(num(x.shap !== undefined ? x.shap : x.shap_value) || 0) }))
    .filter((x) => has(x.feature))
    .slice(0, 3);
  const why = {
    drivers,
    subsystem_ps3: first(xp.ps3_subsystem),
    subsystem_ps2: first(xp.ps2_subsystem, a.ps2_dom_subsystem),
    subsystem_verdict: first(xp.subsystem_verdict),
    subsystem_detail: first(xp.subsystem_detail),
    // The attributed components, worst first, from the merged rows.
    components: comps
      .flatMap((c) => c.labels.map((l) => ({ ...l, serial: c.serial })))
      .sort((x, y) => (y.incidents || 0) - (x.incidents || 0))
      .slice(0, 3),
    attribution_note: ATTRIBUTION_NOTE,
    linked_note: LINKED_NOTE,
  };

  return { where, when, what, why, components: comps };
}

// ---------------------------------------------------------------------
// Component rows, one per serial. Failure Severity and Device Reliability attribution and the
// Remaining Useful Life estimate are joined on the serial number, which is
// the only key the two runs share.
// ---------------------------------------------------------------------
export function mergeComponents(agg, d) {
  const a = agg || {};
  const o = d || {};
  const rc = o.ps3_v2_rootcause_360 || {};
  const p5 = o.ps5 || {};

  const ps3 = arr(a.ps3_components_json).length ? arr(a.ps3_components_json) : arr(rc.rootcause);
  const ps5 = arr(a.ps5_components_json).length ? arr(a.ps5_components_json) : arr(p5.components);

  // The device's own component serial carries the type on the identity row
  // (hw_config_current); the survival table often leaves it null.
  const ownSerial = String(first(a.component_serial_nbr, (o.identity || {}).component_serial_nbr, a.serial_number, (o.identity || {}).serial_number) || '');
  const ownType = first(a.component_type, (o.identity || {}).component_type);

  const rows = new Map();
  const row = (serial) => {
    const k = String(serial);
    if (!rows.has(k)) {
      rows.set(k, {
        serial: k, labels: [], component_type: null,
        incidents: 0, critical_weighted: 0, recurrence_30d: 0, latest_incident_at: null,
        age_days: null, risk_tier: null, risk_score: null, rul_days: null, median_days: null,
        is_overdue: null, act_now: null, serial_source: null,
        has_ps3: false, has_ps5: false,
      });
    }
    return rows.get(k);
  };

  ps3.forEach((x) => {
    if (!x || !has(x.serial_number)) return;
    const r = row(x.serial_number);
    const n = num(x.incident_count) || 0;
    r.has_ps3 = true;
    r.labels.push({
      label: String(x.component_label || 'unlabelled'),
      incidents: n,
      recurrence_30d: num(x.recurrence_30d) || 0,
      critical_rate: num(x.critical_rate),
      latest_incident_at: first(x.latest_incident_at),
      semantics: first(x.semantics, x.component_label_semantics),
    });
    r.incidents += n;
    r.critical_weighted += n * (num(x.critical_rate) || 0);
    r.recurrence_30d += num(x.recurrence_30d) || 0;
    if (has(x.latest_incident_at) && (!r.latest_incident_at || String(x.latest_incident_at) > String(r.latest_incident_at))) {
      r.latest_incident_at = x.latest_incident_at;
    }
  });

  // The published RUL table can repeat a serial (roster fan-out); keep the
  // worst reading per serial, which is what a technician needs.
  ps5.forEach((x) => {
    if (!x || !has(x.component_serial_nbr)) return;
    const r = row(x.component_serial_nbr);
    const rul = num(x.expected_component_rul_days);
    if (r.has_ps5 && r.rul_days !== null && rul !== null && rul > r.rul_days) return;
    r.has_ps5 = true;
    r.component_type = first(x.component_type_name, r.component_type);
    r.age_days = first(num(x.component_age_days), r.age_days);
    r.risk_tier = first(x.risk_tier, r.risk_tier);
    r.risk_score = first(num(x.risk_score), r.risk_score);
    r.rul_days = rul !== null ? rul : r.rul_days;
    r.median_days = first(num(x.predicted_median_survival_days), r.median_days);
    r.is_overdue = has(x.is_overdue) ? !!x.is_overdue : r.is_overdue;
    r.act_now = has(x.act_now) ? !!x.act_now : r.act_now;
    r.serial_source = first(x.serial_source, r.serial_source);
  });

  return [...rows.values()]
    .map((r) => ({
      ...r,
      component_type: has(r.component_type) ? r.component_type : (r.serial === ownSerial && has(ownType) ? ownType : null),
      labels: r.labels.sort((x, y) => y.incidents - x.incidents),
      critical_rate: r.incidents ? r.critical_weighted / r.incidents : null,
      labels_text: r.labels.map((l) => `${l.label} ${nfmt(l.incidents)}`).join(' · '),
      basis: [r.has_ps3 ? 'observed attribution' : null, r.has_ps5 ? 'survival estimate' : null].filter(Boolean).join(' + '),
    }))
    .sort((x, y) => {
      const ox = x.is_overdue ? 0 : 1; const oy = y.is_overdue ? 0 : 1;
      if (ox !== oy) return ox - oy;
      const rx = x.rul_days === null ? 1e9 : x.rul_days; const ry = y.rul_days === null ? 1e9 : y.rul_days;
      if (rx !== ry) return rx - ry;
      return (y.incidents || 0) - (x.incidents || 0);
    });
}

