import { API_BASE_URL } from '../../runtimeConfig';
import React, { useState, useMemo, useEffect, useContext, useCallback } from 'react';
import FilterContext from '../../context/FilterContext';
import {
  TCOL, TYPE_ORDER, TYPE_LABEL, DEVLABEL_TO_CAT, RISK,
  num, intf, pct, dayOf, hexA, compLabel, compLabelShort,
  Badge, SearchBox, MultiSelect, SelectBox, SortTh, Pager,
  Loading, ApiFailure, AwaitingRun, NoRows, Panel, Kpi,
  useSortPage, VirtualTBody,
  TreemapPanel, FunnelPanel, BubblePanel, ParetoPanel, BarPanel,
  PivotMatrix, useDrill, DrillBar, ExportButton, FleetBaselineBand } from '../shared/DashboardKit';
import {
  CoverageBanner, CoverageView, TrendSection, FacilitySection, AgeRiskSection,
  ConfusionSection } from './PS3CoverageSections';
// 27-Jul-2026. PS1 and PS2 have shipped Analyse -> Device 360 since 22-Jul; PS3
// never wired it, so the one tab that knows WHICH COMPONENT failed had no route
// to the cross-PS view of the device it failed on.
import AnalyseButton from '../shared/AnalyseButton';
// 27-Jul-2026. Device Risk and Component Risk merged into one sub-tab
// (PK's request). Master-detail rather than a join -- see that file's header for
// why flattening the two grains together would double count.
import DeviceComponentView from './PS3DeviceComponentView';
import Device360Modal from './Device360Modal';
import PS3RootCauseV2 from './PS3RootCauseV2';

// ============================================================================
// PS3 · Root Cause & Severity — RDS-only: fleet -> device -> component -> incident.
//
// Every figure is read from Aurora RDS via cubic-mars-dashboard-api. There are no
// literals, no generators and no mockData import in this file. The panels that
// once carried invented content (Markov transition matrix, sequential ERR_*
// patterns, "% preventable", dollar savings, and a calendar heat-map whose cell
// value was |sin(day*1.7 + hour*0.43) * cos(day*0.3 + hour*0.8)|) are gone.
//
// STRUCTURE FOLLOWS DATA AVAILABILITY. Each view renders only when its source
// table has rows, and otherwise names the table and the run that fills it.
// Empty is shown as empty, never as zero.
//
//   Fleet      /ps3/rollup, /ps3/severity-mix, /ps3/component-mix, /ps3/collapse-health
//   Models     /ps3/{severity,rootcause}/summary (+ verdict), /ps3/{...}/drivers
//   Devices    /ps3/device-predictions      (v_ps3_device_risk)
//   Components /ps3/serial-predictions      (v_ps3_serial_risk)
//   Incidents  /ps3/incident-predictions    (availability_event grain)
//   Cross-tab  /ps3/crosstab                (server-side pivot over the full run)
//
// pct_critical_pred is NUMERIC(7,4) on a 0..1 scale (sql/15). It was 0 on every
// row of the 21-Jul run because pred_severity_collapsed came out MAJOR for 100%
// of incidents. This file therefore tests the column before trusting it, and
// otherwise collapses the real 4-class label itself using the same map the v2
// notebook and sql/19 apply — so all three agree on what "critical" means.
//
// Gate-aware masking: v_ps3_device_risk / v_ps3_serial_risk NULL severity fields
// when a head missed its macro-F1 floor. Nothing here re-derives a masked value.
// ============================================================================

const API_BASE = (API_BASE_URL
  || 'https://a9yuqt9j9b.execute-api.us-east-1.amazonaws.com').replace(/\/$/, '');

// 27-Jul-2026 (PK, item 5). Causation for PS3.
//
// PS3 attributes a failure to a subsystem. This says what that subsystem pulls
// after it -- association-rule lift beside Markov transition probability for the
// same pair, from the 27-Jul S3 load.
//
// The two are shown side by side and never averaged. A pair can be highly
// probable and barely lifted, which means the consequent is simply a common
// subsystem rather than one this failure causes; a single blended score would
// hide exactly that case. Inner-joined on the pair for the same reason -- a rule
// with no transition probability is half an answer.
function CausationPanel({ assoc, markov }) {
  const pairs = useMemo(() => {
    const mk = {};
    (markov || []).forEach((r) => { mk[`${r.from_sub}>${r.to_sub}`] = Number(r.prob); });
    return (assoc || [])
      .map((r) => ({
        from_sub: r.antecedent_subsystem,
        to_sub: r.consequent_subsystem,
        lift: Number(r.lift),
        confidence: Number(r.confidence),
        prob: mk[`${r.antecedent_subsystem}>${r.consequent_subsystem}`],
      }))
      .filter((r) => Number.isFinite(r.lift) && Number.isFinite(r.prob))
      .sort((a, b) => b.lift - a.lift)
      .slice(0, 12);
  }, [assoc, markov]);

  if (!pairs.length) return null;
  return (
    <Panel title="Causation — what follows an attributed subsystem failure"
      note={'Lift is how much more often the pair co-occurs than chance alone would give; '
        + 'probability is how often the consequent actually follows. Both are needed: high '
        + 'probability on a lift near 1 means the consequent is common, not caused. Sequential '
        + 'association over event chains is evidence for causation, not proof of it.'}>
      <table className="data-table">
        <thead>
          <tr>
            <th>Attributed subsystem</th><th>Pulls after it</th>
            <th style={{ textAlign: 'right' }}>Lift</th>
            <th style={{ textAlign: 'right' }}>P(follows)</th>
            <th style={{ textAlign: 'right' }}>Confidence</th>
          </tr>
        </thead>
        <tbody>
          {pairs.map((r) => (
            <tr key={`${r.from_sub}-${r.to_sub}`}>
              <td style={{ fontFamily: 'monospace', fontWeight: 600 }}>{r.from_sub}</td>
              <td style={{ fontFamily: 'monospace', fontWeight: 600 }}>{r.to_sub}</td>
              <td style={{ textAlign: 'right', fontWeight: 700,
                color: r.lift >= 2 ? '#ef4444' : r.lift >= 1.2 ? '#f59e0b' : 'var(--text-secondary)' }}>
                {num(r.lift, 2)}x
              </td>
              <td style={{ textAlign: 'right', fontWeight: 700 }}>{pct(r.prob)}</td>
              <td style={{ textAlign: 'right', color: 'var(--text-secondary)' }}>{pct(r.confidence)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </Panel>
  );
}

async function apiGet(path, params = {}) {
  const qs = new URLSearchParams(
    Object.entries(params).filter(([, v]) => v !== undefined && v !== null && v !== '')
  ).toString();
  const res = await fetch(`${API_BASE}${path}${qs ? `?${qs}` : ''}`);
  if (!res.ok) throw new Error(`${path} -> HTTP ${res.status}`);
  return res.json();
}

// Identical to CONFIG["SEVERITY_COLLAPSE"] in the v2 notebook and the CASE in
// sql/19. Keep the three in step, or the dashboard and the model will disagree
// about the word "critical". Unmapped -> UNKNOWN, never a silent MAJOR.
const SEVERITY_COLLAPSE = {
  PURCHASE_CARD: 'MAJOR', PURCHASE_PRODUCT: 'MAJOR', NONPAYMENT: 'MAJOR',
  ALL_PURCHASE: 'CRITICAL', ALL_FUNCTIONS: 'CRITICAL',
  BUS_READER: 'CRITICAL', BUS_READER_ASSEMBLY: 'CRITICAL',
};
const collapse = (l) => (l ? (SEVERITY_COLLAPSE[String(l).trim().toUpperCase()] || 'UNKNOWN') : null);
const BANDS = ['CRITICAL', 'MAJOR', 'UNKNOWN'];

const SUB_TABS = [
  // 27-Jul-2026. Coverage leads, because the first question this tab has to
  // answer is which device types it can speak for at all. Validators are 3,329
  // of the fleet and 0 of the PS3 feed; that has to be visible before any chart.
  { key: 'coverage', label: 'Coverage & Fleet' },
  { key: 'fleet', label: 'Fleet Overview' },
  { key: 'models', label: 'Model Scorecard' },
  { key: 'risk', label: 'Device & Component Risk' },
  { key: 'incidents', label: 'Incidents' },
  { key: 'pivot', label: 'Cross-tab' },
  { key: 'rcv2', label: 'Root Cause (v2)' },
];

const VERDICT_BADGE = {
  ok: { cls: 'badge-success', label: 'champion' },
  below_floor: { cls: 'badge-high', label: 'below floor' },
  degenerate: { cls: 'badge-critical', label: 'no signal' },
};

export default function PS3RootCauseTab({ city, selectedDevices }) {
  const fctx = useContext(FilterContext) || {};
  const globalDevices = fctx.selectedDevices || selectedDevices;

  const [tab, setTab] = useState('coverage');
  // One modal instance for the whole tab. Every table below raises the same
  // device id into it, so Analyse behaves identically wherever it is clicked.
  const [analyseDevice, setAnalyseDevice] = useState(null);
  const [src, setSrc] = useState({});
  const drill = useDrill();
  const set1 = useCallback((k, v) => setSrc((s) => ({ ...s, [k]: v })), []);

  useEffect(() => {
    let alive = true;
    const load = (key, path) => {
      set1(key, { state: 'loading' });
      apiGet(path, { city })
        .then((d) => {
          if (!alive) return;
          const empty = d === null || d === undefined
            || (Array.isArray(d) && d.length === 0)
            || (!Array.isArray(d) && typeof d === 'object' && Object.keys(d).length === 0);
          set1(key, empty ? { state: 'empty' } : { state: 'ok', data: d });
        })
        .catch((e) => { if (alive) set1(key, { state: 'err', err: String(e.message || e) }); });
    };
    load('rollup', '/ps3/rollup');
    load('sevMix', '/ps3/severity-mix');
    load('compMix', '/ps3/component-mix');
    load('sevHead', '/ps3/severity/summary');
    load('rcHead', '/ps3/rootcause/summary');
    load('devices', '/ps3/device-predictions');
    load('serials', '/ps3/serial-predictions');
    load('incidents', '/ps3/incident-predictions');
    load('drivers', '/ps3/rootcause/drivers');
    load('sevDrivers', '/ps3/severity/drivers');
    load('shipped', '/ps3/summary');
    load('shippedDev', '/ps3/devices');
    load('health', '/ps3/collapse-health');
    // 27-Jul-2026 additions. coverage answers "why is there no Validator data",
    // inventory and fleetDevices supply the device/component detail that DOES
    // exist for every category, and timeline/facilities/ageRisk are aggregates
    // the tab never had a source for.
    load('coverage', '/ps3/coverage');
    load('inventory', '/ps3/fleet-inventory');
    load('timeline', '/ps3/timeline');
    load('facilities', '/ps3/facility-rollup');
    load('ageRisk', '/ps3/age-risk');
    // 27-Jul-2026 (PK, item 5). Causation on the PS3 tab itself, not only behind
    // the Analyse button. PS3 answers "which subsystem failed"; these two answer
    // "and what does that pull after it", which is the same question one step on.
    load('assoc', '/ps2/associations');
    load('markov', '/ps2/markov');
    return () => { alive = false; };
  }, [city, set1]);

  const S = useCallback((k) => src[k] || { state: 'loading' }, [src]);
  const D = useCallback((k, fb = []) => (src[k]?.state === 'ok' ? src[k].data : fb), [src]);

  const rollRows = D('rollup');
  const devices = D('devices');
  const serials = D('serials');
  const incRows = D('incidents');
  const sevRows = D('sevHead');
  const rcRows = D('rcHead');
  const twoHead = sevRows.length > 0 || rcRows.length > 0;
  const shipped = src.shipped?.state === 'ok' ? src.shipped.data : null;

  const pctUsable = useMemo(() => {
    const v = devices.map((d) => d.pct_critical_pred).filter((x) => x !== null && x !== undefined);
    return v.length > 0 && v.some((x) => Number(x) > 0);
  }, [devices]);

  const bandOf = useCallback((row) => {
    if (row.severity_shippable === false) return null;
    if (pctUsable) {
      const v = row.pct_critical_pred;
      if (v === null || v === undefined) return null;
      const n = Number(v);
      return n >= 0.60 ? 'CRITICAL' : n >= 0.30 ? 'MAJOR' : n > 0 ? 'MINOR' : 'UNKNOWN';
    }
    return collapse(row.dominant_pred_severity);
  }, [pctUsable]);

  // ---- filters -------------------------------------------------------------
  const globalCats = useMemo(() => {
    const m = (globalDevices || []).map((d) => DEVLABEL_TO_CAT[d]).filter(Boolean);
    return m.length ? m : [...TYPE_ORDER];
  }, [globalDevices]);

  const [q, setQ] = useState('');
  const [typeSel, setTypeSel] = useState([...TYPE_ORDER]);
  const [bandSel, setBandSel] = useState([...BANDS]);
  const [componentSel, setComponentSel] = useState('');
  const [installedSel, setInstalledSel] = useState('');
  const [failureSel, setFailureSel] = useState('');
  const [facility, setFacility] = useState('');
  const [deviceSel, setDeviceSel] = useState('');
  const [serialSel, setSerialSel] = useState('');
  const [minInc, setMinInc] = useState('');
  const [ageMax, setAgeMax] = useState('');

  const resetFilters = () => {
    setQ(''); setTypeSel([...TYPE_ORDER]); setBandSel([...BANDS]);
    setComponentSel(''); setInstalledSel(''); setFailureSel(''); setFacility('');
    setDeviceSel(''); setSerialSel(''); setMinInc(''); setAgeMax('');
    drill.clear();
  };

  // Option lists are built from loaded rows, so a value absent from the data is
  // never offered as a filter choice.
  const componentOpts = useMemo(() => {
    const s = new Set();
    devices.forEach((d) => d.dominant_pred_component && s.add(d.dominant_pred_component));
    serials.forEach((d) => d.dominant_pred_component && s.add(d.dominant_pred_component));
    incRows.forEach((d) => d.pred_component && s.add(d.pred_component));
    return [...s].sort();
  }, [devices, serials, incRows]);

  const installedOpts = useMemo(
    () => [...new Set(serials.map((x) => x.component_description).filter(Boolean))].sort(), [serials]);

  const failureOpts = useMemo(() => {
    const s = new Set();
    devices.forEach((d) => d.dominant_pred_severity && s.add(d.dominant_pred_severity));
    incRows.forEach((d) => d.pred_severity && s.add(d.pred_severity));
    return [...s].sort();
  }, [devices, incRows]);

  const deviceOpts = useMemo(
    () => [...new Set(devices.map((d) => d.device_id).filter(Boolean))].sort().slice(0, 2000), [devices]);
  const serialOpts = useMemo(
    () => [...new Set(serials.map((s) => s.matched_serial_nbr).filter(Boolean))].sort().slice(0, 2000), [serials]);
  const facilityOpts = useMemo(() => {
    const m = new Map();
    incRows.forEach((r) => { if (r.facility_id !== null && r.facility_id !== undefined) m.set(String(r.facility_id), r.facility_name || `Facility ${r.facility_id}`); });
    return [...m.entries()].map(([value, label]) => ({ value, label })).sort((a, b) => a.label.localeCompare(b.label));
  }, [incRows]);

  const hit = useCallback((row) => {
    if (!q) return true;
    const s = q.toLowerCase();
    return [row.device_id, row.matched_serial_nbr, row.component_description,
      row.dominant_pred_component, row.pred_component,
      row.dominant_pred_severity, row.pred_severity, row.facility_name, row.facility_id,
      row.availability_event_id]
      .filter((x) => x !== null && x !== undefined).join(' ').toLowerCase().includes(s);
  }, [q]);

  const DRILL_MAP = useMemo(() => ({
    category: (r) => r.mars_device_category,
    component: (r) => r.dominant_pred_component ?? r.pred_component,
    severity: (r) => r.dominant_pred_severity ?? r.pred_severity,
    device: (r) => r.device_id,
    facility: (r) => r.facility_name ?? r.facility_id,
    // The physical component fitted to the serial, distinct from the subsystem
    // the model attributes. Only serial rows carry it; DRILL_MAP returning
    // undefined on other rows would exclude them, so it falls back to the row's
    // own value and lets non-serial views pass through untouched.
    installed_component: (r) => r.component_description,
  }), []);

  const aggFilter = useCallback((r) => {
    const cat = r.mars_device_category;
    if (!globalCats.includes(cat) || !typeSel.includes(cat)) return false;
    const b = bandOf(r);
    if (b && !bandSel.includes(b)) return false;
    if (componentSel && (r.dominant_pred_component ?? r.pred_component) !== componentSel) return false;
    if (installedSel && r.component_description !== installedSel) return false;
    if (failureSel && (r.dominant_pred_severity ?? r.pred_severity) !== failureSel) return false;
    if (deviceSel && r.device_id !== deviceSel) return false;
    if (serialSel && r.matched_serial_nbr !== serialSel) return false;
    if (minInc !== '' && Number(r.n_incidents || 0) < Number(minInc)) return false;
    if (ageMax !== '' && Number(r.component_age_days ?? r.avg_component_age_days ?? 0) > Number(ageMax)) return false;
    return hit(r) && drill.matches(r, DRILL_MAP);
  }, [globalCats, typeSel, bandSel, componentSel, installedSel, failureSel, deviceSel, serialSel, minInc, ageMax, bandOf, hit, drill, DRILL_MAP]);

  const devView = useMemo(() => devices.filter(aggFilter), [devices, aggFilter]);
  const serView = useMemo(() => serials.filter(aggFilter), [serials, aggFilter]);
  const incView = useMemo(() => incRows.filter((r) => {
    const cat = r.mars_device_category;
    if (!globalCats.includes(cat) || !typeSel.includes(cat)) return false;
    const b = collapse(r.pred_severity);
    if (b && !bandSel.includes(b)) return false;
    if (componentSel && r.pred_component !== componentSel) return false;
    if (failureSel && r.pred_severity !== failureSel) return false;
    if (deviceSel && r.device_id !== deviceSel) return false;
    if (serialSel && r.matched_serial_nbr !== serialSel) return false;
    if (facility && String(r.facility_id) !== String(facility)) return false;
    if (ageMax !== '' && Number(r.component_age_days || 0) > Number(ageMax)) return false;
    return hit(r) && drill.matches(r, DRILL_MAP);
  }), [incRows, globalCats, typeSel, bandSel, componentSel, failureSel, deviceSel, serialSel, facility, ageMax, hit, drill, DRILL_MAP]);

  // ---- fleet aggregates ----------------------------------------------------
  const catOk = useCallback((r) => globalCats.includes(r.device_category) && typeSel.includes(r.device_category)
    && (!drill.get('category') || drill.get('category') === r.device_category), [globalCats, typeSel, drill]);

  const sevMixRows = useMemo(() => D('sevMix').filter(catOk), [D, catOk]);
  const compMixRows = useMemo(() => D('compMix').filter(catOk), [D, catOk]);

  const sevByCat = useMemo(() => {
    const by = {}, labels = new Set();
    sevMixRows.forEach((r) => {
      const c = r.device_category;
      by[c] = by[c] || { category: c };
      const l = r.pred_severity || 'Unclassified';
      by[c][l] = (by[c][l] || 0) + Number(r.n || 0);
      labels.add(l);
    });
    return { rows: Object.values(by), labels: [...labels].sort() };
  }, [sevMixRows]);

  const funnel = useMemo(() => {
    const c = {};
    sevMixRows.forEach((r) => { const b = collapse(r.pred_severity) || 'UNKNOWN'; c[b] = (c[b] || 0) + Number(r.n || 0); });
    const total = Object.values(c).reduce((a, b) => a + b, 0);
    if (!total) return [];
    const majorPlus = (c.CRITICAL || 0) + (c.MAJOR || 0);
    return [
      { name: 'All OOS incidents', value: total, label: `All OOS · ${total.toLocaleString()}` },
      { name: 'MAJOR or worse', value: majorPlus, label: `MAJOR+ · ${majorPlus.toLocaleString()}` },
      { name: 'CRITICAL', value: c.CRITICAL || 0, label: `CRITICAL · ${(c.CRITICAL || 0).toLocaleString()}` },
    ].filter((s) => s.value > 0);
  }, [sevMixRows]);

  const compTree = useMemo(() => {
    const agg = {};
    compMixRows.forEach((r) => {
      const k = r.pred_component || 'Unclassified';
      // Key on the raw class so drill-down still filters on the value the API
      // stores; label separately for display. Keeping the two apart is what
      // lets the tile read "No component attributed" while the drill still
      // sends pred_component='None' to the server.
      agg[k] = agg[k] || { name: compLabelShort(k), raw: k, value: 0, agree: 0, labelled: 0 };
      agg[k].value += Number(r.n || 0);
      agg[k].agree += Number(r.n_agree || 0);
      agg[k].labelled += Number(r.n_labelled || 0);
    });
    return Object.values(agg)
      .map((x) => ({ ...x, agreement: x.labelled ? Math.round((x.agree / x.labelled) * 1000) / 10 : null }))
      .sort((a, b) => b.value - a.value);
  }, [compMixRows]);

  const bubble = useMemo(() => devView.map((d) => ({
    device_id: d.device_id,
    mars_device_category: d.mars_device_category,
    age: Number(d.avg_component_age_days) || 0,
    incidents: Number(d.n_incidents) || 0,
  })).filter((d) => d.incidents > 0), [devView]);

  // ---- cross-tab -----------------------------------------------------------
  const [pivotRows, setPivotRows] = useState('device_category');
  const [pivotCols, setPivotCols] = useState('component');
  const [pivot, setPivot] = useState({ state: 'idle' });
  useEffect(() => {
    if (tab !== 'pivot') return undefined;
    let alive = true;
    setPivot({ state: 'loading' });
    apiGet('/ps3/crosstab', { city, rows: pivotRows, cols: pivotCols })
      .then((d) => { if (alive) setPivot(Array.isArray(d) && d.length ? { state: 'ok', data: d } : { state: 'empty' }); })
      .catch((e) => { if (alive) setPivot({ state: 'err', err: String(e.message || e) }); });
    return () => { alive = false; };
  }, [tab, city, pivotRows, pivotCols]);

  // ---- coverage & fleet (server-side search, so a device outside the first
  // page is still findable rather than reported as absent) --------------------
  const coverage = D('coverage');
  const [fleetCat, setFleetCat] = useState('');
  const [fleetQ, setFleetQ] = useState('');
  const [fleetDev, setFleetDev] = useState({ state: 'loading', data: [] });
  useEffect(() => {
    if (tab !== 'coverage') return undefined;
    let alive = true;
    setFleetDev((p) => ({ ...p, state: 'loading' }));
    const id = setTimeout(() => {
      apiGet('/ps3/fleet-devices', { city, device_category: fleetCat, q: fleetQ, limit: 1000 })
        .then((d) => { if (alive) setFleetDev({ state: 'ok', data: Array.isArray(d) ? d : [] }); })
        .catch(() => { if (alive) setFleetDev({ state: 'err', data: [] }); });
    }, fleetQ ? 300 : 0);   // debounce typing, fire immediately on a filter change
    return () => { alive = false; clearTimeout(id); };
  }, [tab, city, fleetCat, fleetQ]);

  // ---- confusion matrix (its own fetch: the generic cross-tab could pivot
  // these dimensions but mixed 32,842 TVM rows with 1,770 GATE rows into one
  // unreadable grid) ----------------------------------------------------------
  const [cmHead, setCmHead] = useState('severity');
  const [cmCat, setCmCat] = useState('TVM');
  const [cm, setCm] = useState({ state: 'idle' });
  useEffect(() => {
    if (tab !== 'pivot') return undefined;
    let alive = true;
    setCm({ state: 'loading' });
    apiGet('/ps3/crosstab', {
      city, device_category: cmCat,
      rows: cmHead === 'severity' ? 'severity' : 'component',
      cols: cmHead === 'severity' ? 'actual_severity' : 'actual_component',
    })
      .then((d) => { if (alive) setCm({ state: 'ok', data: Array.isArray(d) ? d : [] }); })
      .catch(() => { if (alive) setCm({ state: 'err' }); });
    return () => { alive = false; };
  }, [tab, city, cmHead, cmCat]);

  const modelledCats = useMemo(
    () => coverage.filter((r) => r.modeled).map((r) => r.device_category), [coverage]);

  if (['rollup', 'devices', 'shipped'].every((k) => S(k).state === 'loading')) {
    return <Loading what={`PS3 root-cause and severity data for ${city}`} />;
  }
  if (S('rollup').state === 'err' && S('shipped').state === 'err' && !twoHead) {
    return <ApiFailure what="PS3 API" detail={S('rollup').err} />;
  }

  const runId = (sevRows[0] || rcRows[0] || {}).run_id;
  const runTs = (sevRows[0] || rcRows[0] || {}).run_ts;

  return (
    <div>

      {/* Programme-level base statistic. Same component and same route on
          PS1/PS2/PS3/PS5, so the headline OOS and chargeable counts are
          stated once and cannot drift between tabs. */}
      <FleetBaselineBand apiBase={API_BASE} city={city} />

      <div className="card" style={{ marginBottom: 16, padding: 14, display: 'flex', flexWrap: 'wrap',
        alignItems: 'center', gap: 10, borderLeft: '4px solid var(--primary)' }}>
        <span style={{ padding: '4px 10px', borderRadius: 6, background: hexA('#6366f1', 0.12),
          color: '#4f46e5', fontSize: 12, fontWeight: 700 }}>Failure = hardware OOS (Set)</span>
        <span style={{ fontSize: 12, color: 'var(--text-secondary)' }}>
          · Chargeable events are a strict subset of OOS and are not the PS3 target
        </span>
        {twoHead ? (
          <>
            <span style={{ fontSize: 12, color: 'var(--text-secondary)' }}>· Run {runId || '—'}</span>
            <span style={{ fontSize: 12, color: 'var(--text-secondary)' }}>· As-of {dayOf(runTs)}</span>
          </>
        ) : (
          <span style={{ fontSize: 12, color: 'var(--text-secondary)' }}>
            · Two-head run not loaded — showing the shipped severity model{shipped?.mlflow_version ? ` (${shipped.mlflow_version})` : ''}
          </span>
        )}
        <span className="badge badge-success" style={{ marginLeft: 'auto' }}>● LIVE · RDS</span>
      </div>

      {/* Above the sub-tabs on purpose. Which device types PS3 can speak for is
          not a detail inside one view -- it qualifies every number on the tab. */}
      <CoverageBanner rows={coverage} selected={fleetCat}
        onSelect={(c) => { setFleetCat(c); setTab('coverage'); }} />

      <div className="tab-container">
        {SUB_TABS.map((t) => (
          <button key={t.key} className={`tab ${tab === t.key ? 'active' : ''}`} onClick={() => setTab(t.key)}>{t.label}</button>
        ))}
      </div>

      {/* Drill state is global to the tab, so the bar (and its Back button)
          renders on every sub-tab -- including Coverage, where a drill pushed
          from a fleet-inventory tile previously left no visible way out. */}
      {tab === 'coverage' && <DrillBar drill={drill} />}
      {tab !== 'models' && tab !== 'coverage' && (
        <>
          <div className="filter-bar" style={{ borderRadius: 12, border: '1px solid var(--border)', marginTop: 12, marginBottom: 10, flexWrap: 'wrap' }}>
            <div className="filter-group">
              <label className="filter-label">Search</label>
              <SearchBox value={q} onChange={setQ} placeholder="Device / serial / component / facility / event…" width={250} />
            </div>
            <div className="filter-separator" />
            <MultiSelect label="Device type" options={TYPE_ORDER} selected={typeSel}
              onToggle={(t) => setTypeSel((p) => (p.includes(t) ? p.filter((x) => x !== t) : [...p, t]))}
              onAll={() => setTypeSel([...TYPE_ORDER])} colorOf={(t) => TCOL[t]} />
            <div className="filter-separator" />
            <MultiSelect label="Severity band" options={BANDS} selected={bandSel}
              onToggle={(b) => setBandSel((p) => (p.includes(b) ? p.filter((x) => x !== b) : [...p, b]))}
              onAll={() => setBandSel([...BANDS])} colorOf={(b) => RISK[b]} />
            <div className="filter-group" style={{ marginLeft: 'auto' }}>
              <button className="filter-btn" onClick={resetFilters}>Reset all</button>
            </div>
          </div>
          <div className="filter-bar" style={{ borderRadius: 12, border: '1px solid var(--border)', marginBottom: 12, flexWrap: 'wrap' }}>
            {installedOpts.length > 0 && <SelectBox label="Component name" value={installedSel} onChange={setInstalledSel} options={installedOpts} />}
            {componentOpts.length > 0 && <SelectBox label="Attributed subsystem" value={componentSel} onChange={setComponentSel} options={componentOpts} />}
            {failureOpts.length > 0 && <SelectBox label="Failure type" value={failureSel} onChange={setFailureSel} options={failureOpts} />}
            {deviceOpts.length > 0 && <SelectBox label="Device ID" value={deviceSel} onChange={setDeviceSel} options={deviceOpts} />}
            {serialOpts.length > 0 && <SelectBox label="Serial no." value={serialSel} onChange={setSerialSel} options={serialOpts} />}
            {facilityOpts.length > 0 && <SelectBox label="Facility" value={facility} onChange={setFacility} options={facilityOpts} />}
            <div className="filter-group">
              <label className="filter-label">Min incidents</label>
              <input type="number" min="0" value={minInc} onChange={(e) => setMinInc(e.target.value)} placeholder="0"
                style={{ padding: '5px 8px', borderRadius: 6, border: '1px solid var(--border)', fontSize: 12, width: 70, background: '#fff', color: 'var(--text)' }} />
            </div>
            <div className="filter-group">
              <label className="filter-label">Age ≤ (days)</label>
              <input type="number" min="0" value={ageMax} onChange={(e) => setAgeMax(e.target.value)} placeholder="∞"
                style={{ padding: '5px 8px', borderRadius: 6, border: '1px solid var(--border)', fontSize: 12, width: 74, background: '#fff', color: 'var(--text)' }} />
            </div>
          </div>
          <DrillBar drill={drill} />
        </>
      )}

      {!pctUsable && devices.length > 0 && tab !== 'models' && (
        <div className="card" style={{ marginBottom: 14, padding: '10px 14px', borderLeft: '4px solid #f59e0b' }}>
          <span style={{ fontSize: 12, color: 'var(--text-secondary)', lineHeight: 1.6 }}>
            <strong>Severity banded from the 4-class label, not <code style={{ fontFamily: 'monospace' }}>pct_critical_pred</code>.</strong>{' '}
            That column is 0 on every loaded row because <code style={{ fontFamily: 'monospace' }}>pred_severity_collapsed</code> came out
            MAJOR for 100% of incidents — the collapse map was keyed on labels while the data carries codes. Bands here use the same map the
            v2 notebook and <code style={{ fontFamily: 'monospace' }}>sql/19</code> apply, and the tab switches back automatically once a
            corrected run lands.
          </span>
        </div>
      )}

      {tab === 'coverage' && (
        <>
          <CoverageView coverage={coverage} inventory={D('inventory')}
            fleetDevices={fleetDev.data} fleetState={fleetDev.state}
            cat={fleetCat} setCat={setFleetCat} q={fleetQ} setQ={setFleetQ}
            apiBase={API_BASE} drill={drill} onAnalyse={setAnalyseDevice} />
          <CausationPanel assoc={D('assoc')} markov={D('markov')} />
        </>
      )}
      {tab === 'fleet' && (
        <FleetView roll={rollRows} loaded={S('rollup').state === 'ok'} typeSel={typeSel} globalCats={globalCats}
          sevByCat={sevByCat} funnel={funnel} compTree={compTree} bubble={bubble}
          drill={drill} health={D('health')}
          timeline={D('timeline')} facilities={D('facilities')} ageRisk={D('ageRisk')}
          activeCats={typeSel.filter((t) => globalCats.includes(t))} />
      )}
      {tab === 'models' && (
        <ModelsView sevRows={sevRows} rcRows={rcRows} shipped={shipped}
          shippedDev={D('shippedDev')} drivers={D('drivers')} sevDrivers={D('sevDrivers')} twoHead={twoHead} />
      )}
      {(tab === 'risk' || tab === 'devices' || tab === 'serials') && (
        <DeviceComponentView
          deviceRows={devView} deviceTotal={devices.length} deviceState={S('devices').state}
          serialRows={serView} serialTotal={serials.length} serialState={S('serials').state}
          bandOf={bandOf} pctUsable={pctUsable} drill={drill} apiBase={API_BASE}
          onAnalyse={setAnalyseDevice} apiGet={apiGet} city={city} />
      )}
      {tab === 'incidents' && (
        <IncidentView rows={incView} total={incRows.length} state={S('incidents').state}
          drill={drill} onAnalyse={setAnalyseDevice} />
      )}
      {tab === 'rcv2' && (
        <PS3RootCauseV2 city={city} onAnalyse={setAnalyseDevice} />
      )}
      {analyseDevice && (
        <Device360Modal deviceId={analyseDevice} onClose={() => setAnalyseDevice(null)} />
      )}

      {tab === 'pivot' && (
        <>
          <ConfusionSection cells={cm.data} state={cm.state}
            head={cmHead} setHead={setCmHead} cat={cmCat} setCat={setCmCat}
            cats={modelledCats} />
          <PivotView pivot={pivot} rowsDim={pivotRows} colsDim={pivotCols}
            setRowsDim={setPivotRows} setColsDim={setPivotCols} drill={drill} />
        </>
      )}
    </div>
  );
}

// ---------------------------------------------------------------- Fleet ----
function FleetView({ roll, loaded, typeSel, globalCats, sevByCat, funnel, compTree, bubble, drill, health,
  timeline, facilities, ageRisk, activeCats }) {
  if (!loaded) {
    return <AwaitingRun title="Fleet rollup" table="ps3_device_predictions / ps3_serial_predictions"
      note="The macro view aggregates the same rows the Device and Component views page through." />;
  }
  const shown = roll.filter((r) => globalCats.includes(r.device_category) && typeSel.includes(r.device_category));
  const flat = health.filter((h) => Number(h.collapse_share) >= 0.9999);
  return (
    <div>
      {flat.length > 0 && (
        <div className="card" style={{ marginBottom: 14, padding: '10px 14px', borderLeft: '4px solid #ef4444' }}>
          <span style={{ fontSize: 12, color: 'var(--text-secondary)', lineHeight: 1.6 }}>
            <strong>Collapse health check failed.</strong>{' '}
            {flat.map((h) => `${h.device_category}: 100% ${h.pred_severity_collapsed}`).join(' · ')}. One collapsed label holding an entire
            category means the severity collapse has gone constant again, which forces
            <code style={{ fontFamily: 'monospace' }}> pct_critical_pred</code> to 0. Re-apply
            <code style={{ fontFamily: 'monospace' }}> sql/19</code>, or re-run with the corrected map.
          </span>
        </div>
      )}

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(270px, 1fr))', gap: 16, marginBottom: 18 }}>
        {shown.map((r) => {
          const c = TCOL[r.device_category] || '#64748b';
          const fan = Number(r.n_serials) && Number(r.n_devices) ? Number(r.n_serials) / Number(r.n_devices) : null;
          const collapsed = fan !== null && fan <= 1.0001;
          // 27-Jul-2026. modelled comes from v_ps3_rollup_all. A card for a
          // category with no availability-event feed shows its real fleet and
          // component counts and says so where the incident count would be --
          // it does NOT show 0, which would read as "no failures".
          const modelled = r.modelled !== false;
          return (
            <div key={r.device_category} className="card"
              style={{ borderTop: `3px solid ${c}`, cursor: 'pointer',
                opacity: modelled ? 1 : 0.92 }}
              onClick={() => drill.push('category', r.device_category, TYPE_LABEL[r.device_category] || r.device_category)}
              title="Click to drill this tab into the device type">
              <div className="card-header" style={{ color: c, display: 'flex',
                alignItems: 'center', justifyContent: 'space-between', gap: 8 }}>
                <span>{TYPE_LABEL[r.device_category] || r.device_category}</span>
                {!modelled && <span className="badge badge-high">no severity model</span>}
              </div>
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10, marginTop: 8 }}>
                <div><div className="kpi-value" style={{ fontSize: 26 }}>{intf(r.n_devices)}</div><div className="kpi-label">devices</div></div>
                <div>
                  {modelled
                    ? <><div className="kpi-value" style={{ fontSize: 26 }}>{intf(r.n_incidents)}</div>
                        <div className="kpi-label">OOS incidents</div></>
                    : <><div className="kpi-value" style={{ fontSize: 15, color: '#b45309', lineHeight: 1.4 }}>Not in PS3 feed</div>
                        <div className="kpi-label">no availability events</div></>}
                </div>
                <div><div className="kpi-value" style={{ fontSize: 20 }}>{intf(r.n_serials)}</div><div className="kpi-label">component rows · {intf(r.n_distinct_serials)} distinct</div></div>
                <div>
                  <div className="kpi-value" style={{ fontSize: 20, color: collapsed ? '#f59e0b' : undefined }}>{fan === null ? '—' : num(fan, 2)}</div>
                  <div className="kpi-label">components per device</div>
                </div>
              </div>
              {collapsed && (
                <div style={{ marginTop: 10, fontSize: 11, color: '#b45309', background: hexA('#f59e0b', 0.09), padding: '8px 10px', borderRadius: 6, lineHeight: 1.5 }}>
                  Fan-out is 1.00 — one component per device, where a device carries up to 12. That is gold&apos;s
                  <code style={{ fontFamily: 'monospace' }}> hw_best_match</code> <code style={{ fontFamily: 'monospace' }}>WHERE rn = 1</code> collapse,
                  not a real inventory.
                </div>
              )}
              <div style={{ marginTop: 10, fontSize: 11, color: 'var(--text-secondary)', display: 'flex', flexWrap: 'wrap', gap: 12 }}>
                <span>Mean component age {num(r.avg_component_age_days, 0)} d</span>
                {Number(r.n_gated) > 0 && <span style={{ color: '#f59e0b' }}>{intf(r.n_gated)} gated</span>}
                {modelled
                  ? <span>Last incident {dayOf(r.last_incident_dtm)}</span>
                  : <span style={{ color: '#b45309' }}>No failure severity — this device type emits no availability events</span>}
              </div>
            </div>
          );
        })}
      </div>

      {/* 27-Jul-2026: the tab had no time axis at all, though ae_start_dtm has
          been on ps3_incident_predictions since the v2 load. */}
      <TrendSection timeline={timeline} cats={activeCats} />

      <div className="grid-2">
        <Panel title="Predicted severity class by device type"
          note="The classes the model actually emits, not the 2-class collapse. Click a segment to drill.">
          <BarPanel data={sevByCat.rows} xKey="category" series={sevByCat.labels} stacked height={260}
            onDrill={(p, s) => s && drill.push('severity', s, `severity: ${s}`)} />
        </Panel>
        <Panel title="Severity funnel — OOS → MAJOR+ → CRITICAL"
          note="Each stage is a strict subset of the one above it, so the widths are directly comparable.">
          <FunnelPanel data={funnel} height={260} />
        </Panel>
      </div>

      <Panel title="Predicted component — treemap by incident volume"
        note="Area is incident count. Click a tile to drill every view on this tab into that component.">
        <TreemapPanel data={compTree} height={320}
          onDrill={(n) => n?.raw && drill.push('component', n.raw, `component: ${compLabelShort(n.raw)}`)} />
      </Panel>

      <div className="grid-2">
        <Panel title="Component Pareto"
          note="Agreement between predicted and actual_component is in the Cross-tab view — read it before acting on a component ranking.">
          <ParetoPanel data={compTree.slice(0, 12)} labelKey="name" valueKey="value" valueName="Incidents"
            onDrill={(p) => p?.raw && drill.push('component', p.raw, `component: ${compLabelShort(p.raw)}`)} />
        </Panel>
        <Panel title="Device risk — incidents vs component age"
          note="Bubble area is incident count, colour is device type. A cluster at high age and high volume is the replacement shortlist.">
          <BubblePanel data={bubble} xKey="age" yKey="incidents" zKey="incidents"
            xLabel="Mean component age (days)" yLabel="OOS incidents" zLabel="Incidents"
            colorKey="mars_device_category" height={340}
            onDrill={(p) => p?.device_id && drill.push('device', p.device_id, `device: ${p.device_id}`)} />
        </Panel>
      </div>

      {/* Station view and the age question. facility_name was on every incident
          row and had only ever been used as a filter value, never aggregated. */}
      <FacilitySection facilities={facilities} cats={activeCats} drill={drill} />
      <AgeRiskSection ageRisk={ageRisk} cats={activeCats} />
    </div>
  );
}

// --------------------------------------------------------------- Models ----
function ModelsView({ sevRows, rcRows, shipped, shippedDev, drivers, sevDrivers, twoHead }) {
  const [hide, setHide] = useState(true);
  return (
    <div style={{ marginTop: 16 }}>
      {twoHead ? (
        <>
          <Scorecard title="Severity head — champion first" rows={sevRows} hide={hide} setHide={setHide} />
          <Scorecard title="Root-cause head — champion first" rows={rcRows} hide={hide} setHide={setHide} />
        </>
      ) : (
        <>
          <div className="card" style={{ marginBottom: 16, padding: 14, borderLeft: '4px solid #f59e0b' }}>
            <div className="card-header">Two-head scorecard not loaded</div>
            <p style={{ fontSize: 13, color: 'var(--text-secondary)', marginTop: 6, lineHeight: 1.6 }}>
              <code style={{ fontFamily: 'monospace' }}>ps3_head_summary</code> has no rows yet. Below is the currently shipped severity
              model from <code style={{ fontFamily: 'monospace' }}>ps3_severity_summary</code> — real measured metrics from the deployed run,
              not a placeholder.
            </p>
          </div>
          {shipped && <ShippedModel s={shipped} />}
          {shippedDev.length > 0 && <ShippedDeviceTable rows={shippedDev} />}
        </>
      )}
      {drivers.length > 0 && <DriverPanel title="Root-cause drivers — SHAP importance" rows={drivers} />}
      {sevDrivers.length > 0 && <DriverPanel title="Severity drivers — SHAP importance" rows={sevDrivers} />}
    </div>
  );
}

function Scorecard({ title, rows, hide, setHide }) {
  if (!rows || !rows.length) return null;
  const nDeg = rows.filter((r) => r.verdict === 'degenerate').length;
  const shown = hide ? rows.filter((r) => r.verdict !== 'degenerate') : rows;
  return (
    <Panel
      title={title}
      right={nDeg > 0 ? (
        <button className={`filter-btn${hide ? ' active' : ''}`} onClick={() => setHide((v) => !v)}
          style={hide ? { background: 'var(--primary)', borderColor: 'var(--primary)', color: '#fff' } : undefined}
          title="A model whose macro-F1 is at or below 1/n_classes scores no better than always predicting the majority class">
          {hide ? `${nDeg} no-signal hidden` : `Hide ${nDeg} no-signal`}
        </button>
      ) : null}
      note={'Macro-F1, its promotion floor and the 1/k constant-predictor line are no longer shown on this table (PK, 27-Jul). The gate '
        + 'still runs on macro-F1 server-side — the Verdict column is its result — so a head that scores at or below the level of always '
        + 'predicting the majority class is still marked "no signal" and still has its outputs suppressed. Read accuracy together with '
        + 'that verdict, never on its own: the GATE severity head reached 99.11% accuracy while carrying no class-discriminating '
        + 'information at all.'}
    >
      <table className="data-table">
        <thead>
          <tr>
            <th>Device</th><th>Champion</th><th>Target</th><th>Classes</th>
            <th style={{ textAlign: 'right' }}>Accuracy</th>
            <th style={{ textAlign: 'right' }}>AUC-OVR</th>
            <th style={{ textAlign: 'right' }}>PR-AUC</th>
            <th style={{ textAlign: 'right' }}>n test</th>
            <th>Verdict</th>
          </tr>
        </thead>
        <tbody>
          {shown.map((r, i) => {
            const v = r.verdict || (r.gate_pass ? 'ok' : 'below_floor');
            const b = VERDICT_BADGE[v] || VERDICT_BADGE.below_floor;
            const k = Number(r.n_classes) || null;
            return (
              <React.Fragment key={`${r.device_category}-${r.head}`}>
                <tr style={i === 0 && v === 'ok' ? { background: hexA('#22c55e', 0.06) }
                  : v === 'degenerate' ? { background: hexA('#ef4444', 0.05) } : undefined}>
                  <td style={{ fontWeight: 700, color: TCOL[r.device_category] || 'var(--text-primary)' }}>{r.device_category}</td>
                  <td>{r.modeled === false ? <span style={{ color: 'var(--text-secondary)' }}>not modelled</span> : (r.champion || '—')}</td>
                  <td style={{ fontFamily: 'monospace', fontSize: 12 }}>{r.target_col || '—'}</td>
                  <td>{r.n_classes ?? '—'}</td>
                  <td style={{ textAlign: 'right', fontWeight: 700 }}>{num(r.test_accuracy, 4)}</td>
                  <td style={{ textAlign: 'right', color: Number(r.test_auc_macro_ovr) <= 0.5 ? '#ef4444' : undefined }}>{num(r.test_auc_macro_ovr, 4)}</td>
                  <td style={{ textAlign: 'right' }}>{num(r.test_pr_auc_macro, 4)}</td>
                  <td style={{ textAlign: 'right' }}>{intf(r.n_test)}</td>
                  <td><span className={`badge ${b.cls}`}>{b.label}</span></td>
                </tr>
                {r.verdict_evidence && v !== 'ok' && (
                  <tr><td colSpan={9} style={{ fontSize: 11, color: 'var(--text-secondary)', paddingTop: 0, borderTop: 'none' }}>↳ {r.verdict_evidence}</td></tr>
                )}
              </React.Fragment>
            );
          })}
        </tbody>
      </table>
    </Panel>
  );
}

function ShippedModel({ s }) {
  return (
    <>
      <div className="grid-4" style={{ marginBottom: 16 }}>
        <Kpi label="AUC macro" value={num(s.test_auc_macro, 4)} sub="held-out" color="#22c55e" />
        <Kpi label="Macro-F1" value={num(s.test_f1_macro, 4)} sub="held-out" />
        <Kpi label="OOS incidents" value={intf(s.n_incidents)} sub={`${s.date_start} → ${s.date_end}`} />
        <Kpi label="Top driver" value={s.dominant_feature || '—'} sub={`mean |SHAP| ${num(s.dominant_feature_shap, 4)}`} />
      </div>
      <div className="card" style={{ marginBottom: 16, padding: '12px 20px' }}>
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 24, fontSize: 12, color: 'var(--text-secondary)' }}>
          <span><strong>Champion:</strong> {s.champion_model || '—'}</span>
          <span><strong>Endpoint:</strong> {s.endpoint_name || '—'}</span>
          <span><strong>MLflow:</strong> {s.mlflow_version || '—'}</span>
          <span style={{ color: '#f59e0b' }}><strong>Scope:</strong> {s.is_root_cause ? 'root cause' : 'severity classification — not root cause'}</span>
        </div>
      </div>
    </>
  );
}

function ShippedDeviceTable({ rows }) {
  return (
    <Panel title="Shipped model — held-out performance by device, best first"
      note="Held-out split only. Train and validation rows are not served: in-sample fit beside held-out performance reads as model quality when it is memorisation.">
      <table className="data-table">
        <thead><tr><th>Device</th><th>Split</th><th style={{ textAlign: 'right' }}>Incidents</th><th style={{ textAlign: 'right' }}>Macro-F1</th><th style={{ textAlign: 'right' }}>Accuracy</th></tr></thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={`${r.device}-${r.split}`} style={i === 0 ? { background: hexA('#22c55e', 0.06) } : undefined}>
              <td style={{ fontWeight: 700 }}>{r.device}{i === 0 ? <span className="badge badge-success" style={{ marginLeft: 8 }}>best</span> : null}</td>
              <td><Badge>{r.split}</Badge></td>
              <td style={{ textAlign: 'right' }}>{intf(r.n_incidents)}</td>
              <td style={{ textAlign: 'right', fontWeight: 700 }}>{num(r.f1_macro, 4)}</td>
              <td style={{ textAlign: 'right' }}>{num(r.accuracy, 4)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </Panel>
  );
}

function DriverPanel({ title, rows }) {
  const bars = rows
    .map((d) => ({ feature: d.feature_name || d.feature, shap: Number(d.shap_importance) || 0, rank: Number(d.feature_rank ?? d.driver_rank) }))
    .sort((a, b) => a.rank - b.rank).slice(0, 20);
  if (!bars.length) return null;
  return (
    <Panel title={title}>
      <BarPanel data={bars} xKey="feature" series={['shap']} horizontal
        height={Math.max(240, bars.length * 28)} colorOf={() => '#6366f1'} />
    </Panel>
  );
}

// ------------------------------------------------------------ Incidents ----
function IncidentView({ rows, total, state, drill, onAnalyse }) {
  const t = useSortPage(rows, { key: 'ae_start_dtm', dir: 'desc' }, 50);
  if (state !== 'ok') {
    return <AwaitingRun title="Incident-grain predictions" table="ps3_incident_predictions"
      note="One row per availability event, with predicted and actual severity and component, model confidence and facility." />;
  }
  const sevLab = rows.filter((r) => r.actual_severity);
  const compLab = rows.filter((r) => r.actual_component);
  const conf = rows.map((r) => Number(r.pred_severity_conf)).filter((v) => !Number.isNaN(v));
  return (
    <div>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 16, marginBottom: 18 }}>
        <Kpi label="Incidents (in view)" value={intf(rows.length)} sub={`of ${intf(total)} returned`} />
        <Kpi label="Severity agreement"
          value={sevLab.length ? `${((sevLab.filter((r) => r.actual_severity === r.pred_severity).length / sevLab.length) * 100).toFixed(1)}%` : '—'}
          sub={`on ${intf(sevLab.length)} labelled`} />
        <Kpi label="Component agreement"
          value={compLab.length ? `${((compLab.filter((r) => r.actual_component === r.pred_component).length / compLab.length) * 100).toFixed(1)}%` : '—'}
          sub={`on ${intf(compLab.length)} labelled`} />
        <Kpi label="Mean confidence" value={conf.length ? num(conf.reduce((a, b) => a + b, 0) / conf.length, 3) : '—'} sub="severity head" />
      </div>
      <Panel title="Incident predictions — predicted vs actual"
        right={<ExportButton rows={t.sorted} filename="ps3_incidents.csv" />}
        note="Actual values are green on agreement and red on disagreement. The API returns the most recent 300 incidents of the latest run — this is a sample of the run, not the whole run. The Cross-tab view aggregates the full run server-side.">
        <table className="data-table">
          <thead>
            <tr>
              <SortTh label="Event" col="availability_event_id" sort={t.sort} setSort={t.setSort} />
              <SortTh label="Device" col="device_id" sort={t.sort} setSort={t.setSort} />
              <SortTh label="Serial" col="matched_serial_nbr" sort={t.sort} setSort={t.setSort} />
              <SortTh label="Type" col="mars_device_category" sort={t.sort} setSort={t.setSort} />
              <SortTh label="Facility" col="facility_name" sort={t.sort} setSort={t.setSort} />
              <SortTh label="Pred severity" col="pred_severity" sort={t.sort} setSort={t.setSort} />
              <th>Actual</th>
              <SortTh label="Conf" col="pred_severity_conf" sort={t.sort} setSort={t.setSort} align="right" />
              <SortTh label="Attributed subsystem" col="pred_component" sort={t.sort} setSort={t.setSort} />
              <th>Actual</th>
              <SortTh label="Started" col="ae_start_dtm" sort={t.sort} setSort={t.setSort} />
              <th>Action</th>
            </tr>
          </thead>
          <VirtualTBody rows={t.slice} renderRow={(r) => {
            const sevOk = r.actual_severity ? r.actual_severity === r.pred_severity : null;
            const compOk = r.actual_component ? r.actual_component === r.pred_component : null;
            const mark = (ok) => (ok === null ? 'var(--text-secondary)' : ok ? '#22c55e' : '#ef4444');
            return (
              <tr key={r.availability_event_id}>
                <td style={{ fontFamily: 'monospace', fontSize: 11 }}>{r.availability_event_id}</td>
                <td style={{ fontFamily: 'monospace', fontWeight: 600, cursor: 'pointer' }}
                  onClick={() => drill.push('device', r.device_id, `device: ${r.device_id}`)}>{r.device_id}</td>
                <td style={{ fontFamily: 'monospace', fontSize: 11 }}>{r.matched_serial_nbr || '—'}</td>
                <td><span className="badge badge-info" style={{ background: hexA(TCOL[r.mars_device_category] || '#64748b', 0.12), color: TCOL[r.mars_device_category] || '#64748b' }}>{r.mars_device_category}</span></td>
                <td style={{ fontSize: 12 }}>{r.facility_name || r.facility_id || '—'}</td>
                <td style={{ fontWeight: 600 }}>{r.pred_severity || '—'}</td>
                <td style={{ color: mark(sevOk), fontWeight: sevOk === false ? 700 : 400 }}>{r.actual_severity || '—'}</td>
                <td style={{ textAlign: 'right' }}>{num(r.pred_severity_conf, 3)}</td>
                <td style={{ fontWeight: 600 }} title={compLabel(r.pred_component)}>{compLabelShort(r.pred_component)}</td>
                <td style={{ color: mark(compOk), fontWeight: compOk === false ? 700 : 400 }}
                  title={compLabel(r.actual_component)}>{compLabelShort(r.actual_component)}</td>
                <td style={{ fontSize: 12, color: 'var(--text-secondary)' }}>{dayOf(r.ae_start_dtm)}</td>
                <td>
                  <AnalyseButton compact onClick={() => onAnalyse(r.device_id)}
                    title="Open Device 360 for the device this incident was raised against" />
                </td>
              </tr>
            );
          }} />
        </table>
        <Pager page={t.page} pages={t.pages} total={t.sorted.length} setPage={t.setPage} label="incidents"
          pageSize={t.pageSize} setPageSize={t.setPageSize} />
      </Panel>
    </div>
  );
}

// -------------------------------------------------------------- Cross-tab --
const PIVOT_DIMS = [
  { value: 'device_category', label: 'Device type' },
  { value: 'severity', label: 'Predicted severity' },
  { value: 'component', label: 'Predicted component' },
  { value: 'facility', label: 'Facility' },
  { value: 'actual_severity', label: 'Actual severity' },
  { value: 'actual_component', label: 'Actual component' },
  { value: 'device', label: 'Device ID' },
  { value: 'serial', label: 'Serial no.' },
];
const DIM_TO_DRILL = { device_category: 'category', severity: 'severity', component: 'component', device: 'device', facility: 'facility' };

// 27-Jul-2026. The cross-tab was a bare rows x cols picker: powerful, but it
// asked the reader to already know which pair of dimensions was worth looking
// at, so in practice it got left on the default and read as filler. These
// presets are the questions the rest of the tab raises but cannot answer,
// each of which is exactly one pivot.
const PIVOT_PRESETS = [
  { key: 'agreement', label: 'Is the model right?', rows: 'component', cols: 'actual_component',
    why: 'Predicted subsystem against the labelled truth. The diagonal is agreement; everything off it is a misattribution you would have acted on.' },
  { key: 'where', label: 'Where does each subsystem fail?', rows: 'facility', cols: 'component',
    why: 'Station against attributed subsystem. A station that concentrates one subsystem is a stocking and scheduling decision, not a modelling one.' },
  { key: 'sevcomp', label: 'Which subsystem causes the worst failures?', rows: 'component', cols: 'severity',
    why: 'Attributed subsystem against predicted severity class. This is the ranking that decides what to fix first.' },
  { key: 'devsev', label: 'How does severity differ by device type?', rows: 'device_category', cols: 'severity',
    why: 'Device type against severity class. Read it with the coverage strip: a type absent from the feed cannot appear here at all.' },
  { key: 'worst', label: 'Which devices carry which subsystem?', rows: 'device', cols: 'component',
    why: 'Device against attributed subsystem, capped at the busiest rows. Use it to find a device whose failures are all one subsystem.' },
];

function PivotView({ pivot, rowsDim, colsDim, setRowsDim, setColsDim, drill }) {
  const lab = (v) => PIVOT_DIMS.find((d) => d.value === v)?.label || v;
  // Counts answer "how many", row-% answers "what share" -- and share is what
  // makes a small category readable next to a large one. Both from the same
  // server-side aggregate; no second request.
  const [asPct, setAsPct] = useState(false);
  const active = PIVOT_PRESETS.find((x) => x.rows === rowsDim && x.cols === colsDim);
  const cells = useMemo(() => {
    if (pivot.state !== 'ok') return [];
    // Relabel the literal class "None" for display. row_key/col_key are what the
    // drill sends back to the server, so only the visible text changes.
    return pivot.data.map((c) => ({
      ...c,
      row_key: (rowsDim === 'component' || rowsDim === 'actual_component')
        ? compLabelShort(c.row_key) : c.row_key,
      col_key: (colsDim === 'component' || colsDim === 'actual_component')
        ? compLabelShort(c.col_key) : c.col_key,
    }));
  }, [pivot, rowsDim, colsDim]);

  return (
    <div>
      <div className="card" style={{ marginBottom: 14, padding: 14 }}>
        <div className="card-header">Start from a question</div>
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, marginTop: 10 }}>
          {PIVOT_PRESETS.map((x) => {
            const on = active?.key === x.key;
            return (
              <button key={x.key} type="button" className="filter-btn"
                onClick={() => { setRowsDim(x.rows); setColsDim(x.cols); }}
                title={x.why}
                style={on ? { background: 'var(--primary)', borderColor: 'var(--primary)',
                  color: '#fff', fontWeight: 700 } : undefined}>
                {x.label}
              </button>
            );
          })}
        </div>
        {active && (
          <p style={{ fontSize: 12, color: 'var(--text-secondary)', marginTop: 10, lineHeight: 1.6 }}>
            {active.why}
          </p>
        )}
      </div>
      <div className="filter-bar" style={{ borderRadius: 12, border: '1px solid var(--border)', marginBottom: 14 }}>
        <div className="filter-group">
          <label className="filter-label">Rows</label>
          <select value={rowsDim} onChange={(e) => setRowsDim(e.target.value)}
            style={{ padding: '5px 10px', borderRadius: 6, border: '1px solid var(--border)', fontSize: 12, background: '#fff', color: 'var(--text)' }}>
            {PIVOT_DIMS.map((d) => <option key={d.value} value={d.value} disabled={d.value === colsDim}>{d.label}</option>)}
          </select>
        </div>
        <div className="filter-group">
          <label className="filter-label">Columns</label>
          <select value={colsDim} onChange={(e) => setColsDim(e.target.value)}
            style={{ padding: '5px 10px', borderRadius: 6, border: '1px solid var(--border)', fontSize: 12, background: '#fff', color: 'var(--text)' }}>
            {PIVOT_DIMS.map((d) => <option key={d.value} value={d.value} disabled={d.value === rowsDim}>{d.label}</option>)}
          </select>
        </div>
        <div className="filter-group" style={{ marginLeft: 'auto' }}>
          <label className="filter-label">Show</label>
          <div style={{ display: 'flex', gap: 4 }}>
            {[[false, 'Counts'], [true, 'Row %']].map(([v, l]) => (
              <button key={String(v)} type="button" className="filter-btn"
                onClick={() => setAsPct(v)}
                style={asPct === v ? { background: 'var(--primary)', borderColor: 'var(--primary)', color: '#fff' } : undefined}>
                {l}
              </button>
            ))}
          </div>
        </div>
      </div>
      <Panel title={`Cross-tab — ${lab(rowsDim)} × ${lab(colsDim)}`}
        note="Counted at the incident grain across the whole latest run and aggregated server-side — not a client-side summary of one page. Cell shade is relative to the largest cell. Click a cell to drill both dimensions at once.">
        {pivot.state === 'loading' && <NoRows msg="Loading cross-tab…" />}
        {pivot.state === 'err' && <NoRows msg={`Cross-tab unavailable: ${pivot.err}`} />}
        {pivot.state === 'empty' && <NoRows msg="No incident rows for this run yet." />}
        {pivot.state === 'ok' && (
          <PivotMatrix cells={cells} rowLabel={lab(rowsDim)} colLabel={lab(colsDim)} asPct={asPct}
            onDrill={({ row, col }) => {
              const rd = DIM_TO_DRILL[rowsDim]; const cd = DIM_TO_DRILL[colsDim];
              if (rd) drill.push(rd, row, `${lab(rowsDim)}: ${row}`);
              if (cd) drill.push(cd, col, `${lab(colsDim)}: ${col}`);
            }} />
        )}
      </Panel>
    </div>
  );
}
