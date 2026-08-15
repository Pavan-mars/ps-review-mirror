import { API_BASE_URL } from '../../runtimeConfig';
import React, { useState, useMemo, useEffect, useContext, useCallback } from 'react';
import FilterContext from '../../context/FilterContext';
import Device360Modal from './Device360Modal';
import PS1DriversCausation from './PS1DriversCausation';
import {
  TCOL, TYPE_ORDER, TYPE_LABEL, DEVLABEL_TO_CAT, RISK,
  num, intf, pct, dayOf, hexA,
  Badge, SearchBox, MultiSelect, SelectBox, SortTh, Pager,
  Loading, ApiFailure, AwaitingRun, NoRows, Panel, Kpi,
  useSortPage, VirtualTBody, ServiceNowButton,
  TreemapPanel, BubblePanel, AreaPanel, ParetoPanel, BarPanel, TrendPanel,
  PivotMatrix, useDrill, DrillBar, ExportButton, FleetBaselineBand } from '../shared/DashboardKit';

// ============================================================================
// PS1 · 3-day Hardware-OOS Failure Prediction — RDS-only.
//
// Target is will_hardware_oos_3d, not a chargeable event: chargeable is a
// contract classification applied AFTER the physical failure and is a strict
// subset of OOS, so OOS is what a predictive model can act on. Validators have
// no chargeable events at all, which is the other reason the target is OOS.
//
// Everything drawn here is read from Aurora RDS. There is no mockData import,
// no generator and no literal metric in this file. Removed in the 26-Jul pass,
// each with its reason:
//   FEATURE_IMPORTANCE_OVER_TIME  0.20 + 0.10*sin(t) + 0.05*cos(t) over five
//                                 invented feature names; no such table exists
//   precisionRecallCurve          a closed-form sine curve, not a model sweep
//                                 (/ps1/threshold-sweep serves the real one)
//   fpFnTrend                     sin/cos over a mock accuracy series
//   correlationMatrix             values from (feature-name length * 31) % 100
//   failureTypeBreakdown          invented failure types x invented factors
//   "Models by Device Type" pie   N equal wedges; encoded a model count while
//                                 looking like a distribution
//   Train/Validation/Test chart   in-sample fit drawn beside held-out numbers
//                                 (GATE: train AUC 1.0000 vs test 0.9038)
//
// STRUCTURE FOLLOWS DATA AVAILABILITY. /ps1/coverage reports which device
// categories exist in which table, and the Coverage panel states the gaps
// rather than leaving a silently empty chart.
// ============================================================================

const API_BASE = (API_BASE_URL
  || 'https://a9yuqt9j9b.execute-api.us-east-1.amazonaws.com').replace(/\/$/, '');

async function apiGet(path, params = {}) {
  const qs = new URLSearchParams(
    Object.entries(params).filter(([, v]) => v !== undefined && v !== null && v !== '')
  ).toString();
  const res = await fetch(`${API_BASE}${path}${qs ? `?${qs}` : ''}`);
  if (!res.ok) throw new Error(`${path} -> HTTP ${res.status}`);
  return res.json();
}

// Risk band relative to the model's own decision threshold, so a device is
// "critical" when it is far past the threshold that model was tuned to, not
// past an absolute probability that means different things per device type.
const bandOf = (p, thr) => {
  const prob = Number(p); const t = Number(thr);
  if (Number.isNaN(prob) || Number.isNaN(t) || t <= 0) return null;
  const r = prob / t;
  if (r >= 1.8) return 'CRITICAL';
  if (r >= 1.3) return 'HIGH';
  if (r >= 1.0) return 'MEDIUM';
  return 'LOW';
};
const BANDS = ['CRITICAL', 'HIGH', 'MEDIUM', 'LOW'];

// 28-Jul-2026. Prefer the MODEL's own tier over the ratio computed above.
//
// bandOf() derives a band from probability / threshold. The PS1 thresholds are
// very low relative to the scores -- TVM's is 0.0278 against ~0.9 probabilities,
// a ratio of 32 -- so EVERY device clears the 1.8x CRITICAL cut and the fleet
// renders as 100% critical, which is a property of the arithmetic, not of the
// fleet.
//
// ps1_risk_tier comes from the model run and has a real distribution:
// GATE 62,464 CRITICAL / 17,180 HIGH / 20,162 MEDIUM / 7,304 LOW. Use it
// whenever the row carries one, and fall back to the ratio only when it does
// not, so rows from the legacy table still band.
const bandOfRow = (r) => {
  const t = r && (r.ps1_risk_tier || r.risk_tier);
  if (t) return String(t).toUpperCase();
  return bandOf(r && (r.failure_probability ?? r.device_failure_probability),
                r && r.decision_threshold);
};

const SUB_TABS = [
  { key: 'fleet', label: 'Fleet Overview' },
  { key: 'models', label: 'Model Scorecard' },
  { key: 'devices', label: 'Device Predictions' },
  { key: 'serials', label: 'Component Risk' },
  { key: 'stations', label: 'Stations' },
  { key: 'pivot', label: 'Cross-tab' },
  { key: 'lineage', label: 'Runs & Lineage' },
  // 29-Jul-2026 (PK): moved to last. It fires 12 API calls on mount and is
  // the slowest sub-tab to paint; leading with it made PS1 feel slow.
  { key: 'drivers', label: 'Device State & Drivers' },
];

const VERDICT_BADGE = {
  ok: { cls: 'badge-success', label: 'champion' },
  below_floor: { cls: 'badge-high', label: 'below floor' },
  degenerate: { cls: 'badge-critical', label: 'no signal' },
};

export default function PS1FailurePredictionTab({ city, selectedDevices }) {
  const fctx = useContext(FilterContext) || {};
  const globalDevices = fctx.selectedDevices || selectedDevices;

  const [tab, setTab] = useState('fleet');
  const [grain, setGrain] = useState('device');
  // 29-Jul-2026. THE GRAIN MUST FOLLOW THE SUB-TAB.
  // 'Device Predictions' and 'Component Risk' are two sub-tabs rendering ONE
  // view whose content is chosen by `grain`. grain was initialised to 'device'
  // and never linked to `tab`, so selecting Component Risk left the device
  // table on screen -- the sub-tab looked broken when it was simply showing
  // the other grain. Syncing on tab change fixes it and still lets the Grain
  // buttons override manually afterwards.
  useEffect(() => {
    if (tab === 'serials') setGrain('component');
    else if (tab === 'devices') setGrain('device');
  }, [tab]);
  const [src, setSrc] = useState({});
  const [analyseDevice, setAnalyseDevice] = useState(null);
  const drill = useDrill();
  const set1 = useCallback((k, v) => setSrc((s) => ({ ...s, [k]: v })), []);

  useEffect(() => {
    let alive = true;
    const load = (key, path, params) => {
      set1(key, { state: 'loading' });
      apiGet(path, { city, ...(params || {}) })
        .then((d) => {
          if (!alive) return;
          const empty = d === null || d === undefined
            || (Array.isArray(d) && d.length === 0)
            || (!Array.isArray(d) && typeof d === 'object' && Object.keys(d).length === 0);
          set1(key, empty ? { state: 'empty' } : { state: 'ok', data: d });
        })
        .catch((e) => { if (alive) set1(key, { state: 'err', err: String(e.message || e) }); });
    };
    load('preds', '/ps1/predictions', { limit: 250 });
    load('serials', '/ps1/serial-predictions');
    load('summary', '/ps1/summary');
    load('leaderboard', '/ps1/leaderboard');
    load('modelPerf', '/ps1/model-performance');
    load('riskBands', '/ps1/risk-bands');
    load('riskTrend', '/ps1/risk-trend');
    load('stations', '/ps1/station-summary');
    load('sweep', '/ps1/threshold-sweep');
    load('calib', '/ps1/calibration');
    load('confusion', '/ps1/confusion');
    load('runs', '/ps1/runs');
    load('coverage', '/ps1/coverage');
    return () => { alive = false; };
  }, [city, set1]);

  const S = useCallback((k) => src[k] || { state: 'loading' }, [src]);
  const D = useCallback((k, fb = []) => (src[k]?.state === 'ok' ? src[k].data : fb), [src]);

  const preds = D('preds');
  const serials = D('serials');
  const summary = D('summary');
  const modelPerf = D('modelPerf');

  // Feature importance is per device category; fetch only the ones the run has.
  const [featImp, setFeatImp] = useState({});
  // 27-Jul-2026. Fell back to TYPE_ORDER. modelPerf is EMPTY on this run
  // (ps1_model_performance has 0 rows), so this set was empty, the fetch loop
  // below never ran, and the SHAP panels were blank regardless of what
  // ps1_feature_importance contained -- the request was never made.
  const modelledCats = useMemo(() => {
    const fromPerf = [...new Set(modelPerf.map((m) => m.device_category).filter(Boolean))];
    return fromPerf.length ? fromPerf : [...TYPE_ORDER];
  }, [modelPerf]);
  useEffect(() => {
    let alive = true;
    modelledCats.forEach((cat) => {
      apiGet('/ps1/feature-importance', { city, device_category: cat })
        .then((d) => { if (alive && Array.isArray(d) && d.length) setFeatImp((f) => ({ ...f, [cat]: d })); })
        .catch(() => {});
    });
    return () => { alive = false; };
  }, [city, modelledCats]);

  // ---- filters -------------------------------------------------------------
  const globalCats = useMemo(() => {
    const m = (globalDevices || []).map((d) => DEVLABEL_TO_CAT[d]).filter(Boolean);
    return m.length ? m : [...TYPE_ORDER];
  }, [globalDevices]);

  const [q, setQ] = useState('');
  const [typeSel, setTypeSel] = useState([...TYPE_ORDER]);
  const [bandSel, setBandSel] = useState([...BANDS]);
  const [facility, setFacility] = useState('');
  const [deviceSel, setDeviceSel] = useState('');
  const [serialSel, setSerialSel] = useState('');
  const [componentSel, setComponentSel] = useState('');
  const [flaggedOnly, setFlaggedOnly] = useState(false);
  const [minProb, setMinProb] = useState('');

  const resetFilters = () => {
    setQ(''); setTypeSel([...TYPE_ORDER]); setBandSel([...BANDS]);
    setFacility(''); setDeviceSel(''); setSerialSel(''); setComponentSel('');
    setFlaggedOnly(false); setMinProb(''); drill.clear();
  };

  const facilityOpts = useMemo(() => {
    const m = new Map();
    preds.forEach((p) => {
      if (p.facility_id === null || p.facility_id === undefined) return;
      m.set(String(p.facility_id), p.station_name || `Facility ${p.facility_id}`);
    });
    return [...m.entries()].map(([value, label]) => ({ value, label })).sort((a, b) => a.label.localeCompare(b.label));
  }, [preds]);
  const deviceOpts = useMemo(() => [...new Set(preds.map((p) => p.device_id).filter(Boolean))].sort().slice(0, 2000), [preds]);
  const serialOpts = useMemo(() => [...new Set(serials.map((s) => s.matched_serial_nbr).filter(Boolean))].sort().slice(0, 2000), [serials]);
  const componentOpts = useMemo(() => [...new Set(serials.map((s) => s.component_type).filter(Boolean))].sort(), [serials]);

  const hit = useCallback((row) => {
    if (!q) return true;
    const s = q.toLowerCase();
    return [row.device_id, row.matched_serial_nbr, row.station_name, row.facility_id,
      row.operator, row.dom_error_code, row.dom_subsystem, row.component_type]
      .filter((x) => x !== null && x !== undefined).join(' ').toLowerCase().includes(s);
  }, [q]);

  const DRILL_MAP = useMemo(() => ({
    category: (r) => r.device_category,
    band: (r) => bandOfRow(r) ?? r.risk_band,
    device: (r) => r.device_id,
    facility: (r) => r.station_name ?? r.facility_id,
    component: (r) => r.component_type,
  }), []);

  const predView = useMemo(() => preds.filter((p) => {
    const cat = p.device_category;
    if (!globalCats.includes(cat) || !typeSel.includes(cat)) return false;
    const b = bandOfRow(p);
    if (b && !bandSel.includes(b)) return false;
    if (flaggedOnly && !p.predicted_label) return false;
    if (facility && String(p.facility_id ?? '') !== String(facility)) return false;
    if (deviceSel && p.device_id !== deviceSel) return false;
    if (minProb !== '' && Number(p.failure_probability) * 100 < Number(minProb)) return false;
    return hit(p) && drill.matches(p, DRILL_MAP);
  }), [preds, globalCats, typeSel, bandSel, flaggedOnly, facility, deviceSel, minProb, hit, drill, DRILL_MAP]);

  const serialView = useMemo(() => serials.filter((s) => {
    const cat = s.device_category;
    if (!globalCats.includes(cat) || !typeSel.includes(cat)) return false;
    if (s.risk_band && !bandSel.includes(String(s.risk_band).toUpperCase())) return false;
    if (deviceSel && s.device_id !== deviceSel) return false;
    if (serialSel && s.matched_serial_nbr !== serialSel) return false;
    if (componentSel && s.component_type !== componentSel) return false;
    return hit(s) && drill.matches(s, DRILL_MAP);
  }), [serials, globalCats, typeSel, bandSel, deviceSel, serialSel, componentSel, hit, drill, DRILL_MAP]);

  // ---- fleet aggregates ----------------------------------------------------
  const bandMix = useMemo(() => {
    const c = {};
    predView.forEach((p) => { const b = bandOfRow(p); if (b) c[b] = (c[b] || 0) + 1; });
    return BANDS.filter((b) => c[b]).map((b) => ({ name: b, value: c[b] }));
  }, [predView]);

  const byCatBand = useMemo(() => {
    const by = {};
    predView.forEach((p) => {
      const cat = p.device_category || 'UNKNOWN';
      by[cat] = by[cat] || { category: cat };
      const b = bandOfRow(p);
      if (b) by[cat][b] = (by[cat][b] || 0) + 1;
    });
    return Object.values(by);
  }, [predView]);

  const trend = useMemo(() => {
    const rows = D('riskTrend');
    if (!rows.length) return { rows: [], series: [] };
    const by = {}; const cats = new Set();
    rows.filter((r) => globalCats.includes(r.device_category) && typeSel.includes(r.device_category))
      .forEach((r) => {
        const d = dayOf(r.date);
        by[d] = by[d] || { date: d };
        by[d][r.device_category] = Number(r.failures || 0);
        cats.add(r.device_category);
      });
    return { rows: Object.values(by).sort((a, b) => a.date.localeCompare(b.date)), series: [...cats] };
  }, [D, globalCats, typeSel]);

  const stationTree = useMemo(() => {
    const agg = {};
    predView.forEach((p) => {
      const k = p.station_name || (p.facility_id !== null && p.facility_id !== undefined ? `Facility ${p.facility_id}` : 'Unassigned');
      agg[k] = agg[k] || { name: k, value: 0, flagged: 0 };
      agg[k].value += 1;
      if (p.predicted_label) agg[k].flagged += 1;
    });
    return Object.values(agg).sort((a, b) => b.value - a.value).slice(0, 40);
  }, [predView]);

  const probBubble = useMemo(() => serialView.map((s) => ({
    device_id: s.device_id, serial: s.matched_serial_nbr, device_category: s.device_category,
    age: Number(s.component_age_days) || 0,
    risk: Number(s.serial_risk_score) * 100 || 0,
    weight: Number(s.attribution_weight) * 100 || 1,
  })).filter((s) => s.risk > 0), [serialView]);

  // ---- cross-tab -----------------------------------------------------------
  const [pivotRows, setPivotRows] = useState('device_category');
  const [pivotCols, setPivotCols] = useState('risk_band');
  const [pivot, setPivot] = useState({ state: 'idle' });
  useEffect(() => {
    if (tab !== 'pivot') return undefined;
    let alive = true;
    setPivot({ state: 'loading' });
    apiGet('/ps1/crosstab', { city, rows: pivotRows, cols: pivotCols })
      .then((d) => { if (alive) setPivot(Array.isArray(d) && d.length ? { state: 'ok', data: d } : { state: 'empty' }); })
      .catch((e) => { if (alive) setPivot({ state: 'err', err: String(e.message || e) }); });
    return () => { alive = false; };
  }, [tab, city, pivotRows, pivotCols]);

  if (['preds', 'summary', 'modelPerf'].every((k) => S(k).state === 'loading')) {
    return <Loading what={`PS1 failure predictions for ${city}`} />;
  }
  if (S('preds').state === 'err' && S('summary').state === 'err') {
    return <ApiFailure what="PS1 API" detail={S('preds').err} />;
  }

  const latestDate = preds.length
    ? preds.map((p) => dayOf(p.prediction_date)).sort().slice(-1)[0] : null;
  const anyPromoted = summary.some((s) => s.promoted);

  return (
    <div>
      {analyseDevice && <Device360Modal deviceId={analyseDevice} onClose={() => setAnalyseDevice(null)} />}

      {/* Programme-level base statistic. Same component and same route on
          PS1/PS2/PS3/PS5 so the headline OOS and chargeable counts are
          stated once and cannot drift between tabs. */}
      <FleetBaselineBand apiBase={API_BASE} city={city} />

      <div className="card" style={{ marginBottom: 16, padding: 14, display: 'flex', flexWrap: 'wrap',
        alignItems: 'center', gap: 10, borderLeft: '4px solid var(--primary)' }}>
        <span style={{ padding: '4px 10px', borderRadius: 6, background: hexA('#6366f1', 0.12),
          color: '#4f46e5', fontSize: 12, fontWeight: 700 }}>Target = will_hardware_oos_3d</span>
        <span style={{ fontSize: 12, color: 'var(--text-secondary)' }}>
          · Chargeable events are a strict subset of OOS, applied after the failure — OOS is what a model can act on
        </span>
        {latestDate && <span style={{ fontSize: 12, color: 'var(--text-secondary)' }}>· Latest scoring date {latestDate}</span>}
        {!anyPromoted && summary.length > 0 && (
          <span className="badge badge-high" title="No PS1 champion has cleared its recall floor — treat predictions as a watch signal, not an auto-dispatch">
            REVIEW-ONLY · no model promoted
          </span>
        )}
        <span className="badge badge-success" style={{ marginLeft: 'auto' }}>● LIVE · RDS</span>
      </div>

      <div className="tab-container">
        {SUB_TABS.map((t) => (
          <button key={t.key} className={`tab ${tab === t.key ? 'active' : ''}`} onClick={() => setTab(t.key)}>{t.label}</button>
        ))}
      </div>

      {!['models', 'drivers', 'lineage'].includes(tab) && (
        <>
          <div className="filter-bar" style={{ borderRadius: 12, border: '1px solid var(--border)', marginTop: 12, marginBottom: 10, flexWrap: 'wrap' }}>
            <div className="filter-group">
              <label className="filter-label">Search</label>
              <SearchBox value={q} onChange={setQ} placeholder="Device / serial / station / error code…" width={240} />
            </div>
            <div className="filter-separator" />
            <MultiSelect label="Device type" options={TYPE_ORDER} selected={typeSel}
              onToggle={(t) => setTypeSel((p) => (p.includes(t) ? p.filter((x) => x !== t) : [...p, t]))}
              onAll={() => setTypeSel([...TYPE_ORDER])} colorOf={(t) => TCOL[t]} />
            <div className="filter-separator" />
            <MultiSelect label="Risk band" options={BANDS} selected={bandSel}
              onToggle={(b) => setBandSel((p) => (p.includes(b) ? p.filter((x) => x !== b) : [...p, b]))}
              onAll={() => setBandSel([...BANDS])} colorOf={(b) => RISK[b]} />
            <div className="filter-group">
              <button className={`filter-btn${flaggedOnly ? ' active' : ''}`} onClick={() => setFlaggedOnly((v) => !v)}
                style={flaggedOnly ? { background: 'var(--danger)', borderColor: 'var(--danger)', color: '#fff' } : undefined}>
                Flagged only
              </button>
            </div>
            <div className="filter-group" style={{ marginLeft: 'auto' }}>
              <button className="filter-btn" onClick={resetFilters}>Reset all</button>
            </div>
          </div>
          <div className="filter-bar" style={{ borderRadius: 12, border: '1px solid var(--border)', marginBottom: 12, flexWrap: 'wrap' }}>
            {deviceOpts.length > 0 && <SelectBox label="Device ID" value={deviceSel} onChange={setDeviceSel} options={deviceOpts} />}
            {serialOpts.length > 0 && <SelectBox label="Serial no." value={serialSel} onChange={setSerialSel} options={serialOpts} />}
            {componentOpts.length > 0 && <SelectBox label="Component type" value={componentSel} onChange={setComponentSel} options={componentOpts} />}
            {facilityOpts.length > 0 && <SelectBox label="Station" value={facility} onChange={setFacility} options={facilityOpts} />}
            <div className="filter-group">
              <label className="filter-label">Prob ≥ (%)</label>
              <input type="number" min="0" max="100" value={minProb} onChange={(e) => setMinProb(e.target.value)} placeholder="0"
                style={{ padding: '5px 8px', borderRadius: 6, border: '1px solid var(--border)', fontSize: 12, width: 70, background: '#fff', color: 'var(--text)' }} />
            </div>
          </div>
          <DrillBar drill={drill} />
        </>
      )}

      {tab === 'fleet' && (
        <FleetView preds={predView} totalPreds={preds.length} bandMix={bandMix} byCatBand={byCatBand}
          trend={trend} stationTree={stationTree} coverage={D('coverage')} summary={summary} drill={drill}
          stations={D('stations')} />
      )}
      {tab === 'models' && (
        <ModelsView summary={summary} leaderboard={D('leaderboard')} modelPerf={modelPerf}
          sweep={D('sweep')} calib={D('calib')} confusion={D('confusion')} featImp={featImp} />
      )}      {tab === 'drivers' && <PS1DriversCausation city={city} onAnalyse={setAnalyseDevice} />}

      {/* 27-Jul-2026. Device Predictions and Component Risk merged into one
          sub-tab (PK). A grain switch rather than a join: the two are at device
          and (device, serial) grain, and flattening one into the other repeats
          every device measure on its component rows and fans out on sum. Both
          old keys route here so a bookmarked sub-tab still resolves. */}
      {(tab === 'devices' || tab === 'serials') && (
        <>
          <div className="filter-bar" style={{ borderRadius: 12, border: '1px solid var(--border)',
            marginBottom: 14, alignItems: 'center' }}>
            <div className="filter-group">
              <label className="filter-label">Grain</label>
              <div style={{ display: 'flex', gap: 4 }}>
                {[['device', 'By device'], ['component', 'By component']].map(([k, l]) => (
                  <button key={k} type="button" className="filter-btn" onClick={() => setGrain(k)}
                    style={grain === k ? { background: 'var(--primary)', borderColor: 'var(--primary)',
                      color: '#fff', fontWeight: 700 } : undefined}>{l}</button>
                ))}
              </div>
            </div>
            <span style={{ fontSize: 12, color: 'var(--text-secondary)', marginLeft: 12 }}>
              {grain === 'device'
                ? `${intf(predView.length)} device prediction(s)`
                : `${intf(serialView.length)} component row(s) — one per (device, serial)`}
            </span>
            <span style={{ fontSize: 11, color: 'var(--text-secondary)', marginLeft: 'auto' }}>
              The two grains are never summed together — a device probability repeated on its
              component rows would double count.
            </span>
          </div>
          {grain === 'device'
            ? <DeviceView rows={predView} total={preds.length} state={S('preds').state}
                drill={drill} apiBase={API_BASE} onAnalyse={setAnalyseDevice} />
            : <ComponentView rows={serialView} total={serials.length} state={S('serials').state}
                bubble={probBubble} drill={drill} apiBase={API_BASE} onAnalyse={setAnalyseDevice} />}
        </>
      )}
      {tab === 'stations' && (
        <StationView rows={D('stations')} state={S('stations').state} drill={drill} />
      )}
      {tab === 'pivot' && (
        <PivotView pivot={pivot} rowsDim={pivotRows} colsDim={pivotCols}
          setRowsDim={setPivotRows} setColsDim={setPivotCols} drill={drill} />
      )}
      {tab === 'lineage' && (
        <LineageView runs={D('runs')} state={S('runs').state} coverage={D('coverage')} />
      )}
    </div>
  );
}

// ---------------------------------------------------------------- Fleet ----
function FleetView({ preds, totalPreds, bandMix, byCatBand, trend, stationTree, coverage, summary, drill,
                     stations = [] }) {
  if (!totalPreds) {
    return <AwaitingRun title="Device predictions" table="ps1_failure_predictions"
      note="The batch scorer writes this table; the dashboard shows the latest computed_date." />;
  }
  const flagged = preds.filter((p) => p.predicted_label).length;
  const critical = preds.filter((p) => bandOfRow(p) === 'CRITICAL').length;
  const cats = new Set(preds.map((p) => p.device_category));

  // Distribution of the scored fleet across 10 probability bands. Built from the
  // predictions already loaded, so it needs no extra call and it moves with the
  // filters. Fixed 10 bands rather than quantiles: equal-width buckets show the
  // SHAPE of the model's confidence, whereas deciles would flatten it by
  // construction and hide exactly the thing worth seeing. Empty bands are kept so
  // a gap in the middle reads as a gap, not as a missing category.
  // 27-Jul-2026. Cumulative-capture curve, replacing the probability histogram.
  //
  // Rank every scored device by failure probability descending, cut into ten
  // equal-SIZE deciles (equal counts of devices, not equal probability width --
  // "the top 10% of the fleet" is the unit a planner schedules against), then
  // measure what share of the FLAGGED devices lands in each.
  //
  // A device counts as flagged on predicted_label when the run supplies one, and
  // falls back to probability >= threshold only when it does not. Re-deriving
  // the label from the threshold where the model already stated it would quietly
  // disagree with the run whenever the two differ.
  const captureCurve = (() => {
    const scored = preds
      .map((p) => ({
        v: Number(p.failure_probability),
        flag: p.predicted_label !== null && p.predicted_label !== undefined
          ? (Number(p.predicted_label) === 1 || p.predicted_label === true)
          : Number(p.failure_probability) >= Number(p.decision_threshold || 1),
      }))
      .filter((r) => Number.isFinite(r.v))
      .sort((a, b) => b.v - a.v);
    const totalFlagged = scored.reduce((s, r) => s + (r.flag ? 1 : 0), 0);
    if (!scored.length || !totalFlagged) return [];
    const size = scored.length / 10;
    let cum = 0;
    return Array.from({ length: 10 }, (_, i) => {
      const slice = scored.slice(Math.round(i * size), Math.round((i + 1) * size));
      const n = slice.reduce((s, r) => s + (r.flag ? 1 : 0), 0);
      cum += n;
      return {
        decile: `Top ${(i + 1) * 10}%`,
        'Share of flagged': Math.round((n / totalFlagged) * 1000) / 10,
        'Cumulative captured': Math.round((cum / totalFlagged) * 1000) / 10,
      };
    });
  })();

  // Top depots by devices flagged, with the rest of the fleet at that depot shown
  // behind it -- 106 of 236 reads very differently from 106 on its own.
  const topStations = (stations || [])
    .filter((s) => s && (s.predicted_failures || 0) > 0)
    .slice(0, 12)
    .map((s) => ({
      station: s.facility_name || `Facility ${s.facility_id}`,
      facility_id: s.facility_id,
      Flagged: Number(s.predicted_failures || 0),
      'Not flagged': Math.max(0, Number(s.total_devices || 0) - Number(s.predicted_failures || 0)),
    }));

  return (
    <div>
      <CoverageNote coverage={coverage} />
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 16, marginBottom: 18 }}>
        <Kpi label="Devices (in view)" value={intf(preds.length)} sub={`of ${intf(totalPreds)} returned`} />
        <Kpi label="Flagged" value={intf(flagged)} color={RISK.CRITICAL}
          sub={preds.length ? `${((flagged / preds.length) * 100).toFixed(1)}% of view` : '—'} />
        <Kpi label="Critical band" value={intf(critical)} color={RISK.CRITICAL} sub="≥ 1.8× the decision threshold" />
        <Kpi label="Device types" value={intf(cats.size)} sub={[...cats].join(' · ') || '—'} />
      </div>

      {/* 2026-07-26 -- "Risk band by device type" and "Fleet risk mix" removed.
          Both were driven by ps1_risk_bands, which now holds two different kinds
          of row: real CRITICAL/HIGH/MEDIUM/LOW tiers for VALIDATOR, and Top 1/5/10%
          precision-at-K cohorts for GATE and TVM (which have no per-device scores
          this run). Charting them together stacked one band type on top of the
          other and read as a single fleet mix, which it is not. The two panels
          below answer questions the run can actually support. */}
      <div className="grid-2">
        {/* 27-Jul-2026 (PK). Replaced "Where the fleet sits on the risk scale".
            That panel showed the SHAPE of the probability distribution, which is
            a modelling diagnostic, not an operating one -- a planner cannot do
            anything differently because the histogram is bimodal.

            This asks the question a planner actually has: if I only work the
            riskiest N% of the fleet, what share of the predicted failures do I
            catch? Devices are sorted by probability descending and cut into
            deciles; the bar is that decile's share of all flagged devices and
            the line is the running total.

            BOTH measures are percentages, so they share one axis honestly. A
            count-and-percentage pair would have needed two scales, and a
            dual-axis chart is the one thing never worth doing. */}
        <Panel title="Risk concentration — how much you catch by working the top N%"
          note="Scored devices ranked by failure probability, cut into deciles. Bar: that decile's share of all flagged devices. Line: cumulative share captured. If the first decile holds 60%, then touching a tenth of the fleet reaches three-fifths of the predicted failures.">
          {captureCurve.length ? (
            <TrendPanel data={captureCurve} xKey="decile" bars={['Share of flagged']}
              series={['Cumulative captured']} height={280}
              colorOf={(s) => (s === 'Cumulative captured' ? '#ef4444' : '#6366f1')} />
          ) : <NoRows msg="No scored devices with a probability in the current selection." />}
        </Panel>
        <Panel title="Where the risk sits — stations by devices flagged"
          note="Devices predicted to go out of service in the next 3 days, by depot. Ordered by count, not percentage: a one-device site at 99% is not the operational priority.">
          {topStations.length ? (
            <BarPanel data={topStations} xKey="station" series={['Flagged', 'Not flagged']}
              stacked horizontal height={320}
              colorOf={(s) => (s === 'Flagged' ? RISK.CRITICAL : '#e2e8f0')}
              onDrill={(p) => p?.station && drill.push('facility', p.facility_id, `station: ${p.station}`)} />
          ) : <NoRows msg="No station rows — ps1_station_summary is empty for this run." />}
        </Panel>
      </div>

      {trend.rows.length > 0 && (
        <Panel title="Predicted failures over time, by device type"
          note="Counts of devices flagged per scoring date, from ps1_risk_trend.">
          <AreaPanel data={trend.rows} xKey="date" series={trend.series} height={280} colorOf={(c) => TCOL[c]} />
        </Panel>
      )}

      {stationTree.length > 0 && (
        <Panel title="Devices by station — treemap"
          note="Area is the number of scored devices at that station. Click a tile to drill into it.">
          <TreemapPanel data={stationTree} height={320}
            onDrill={(n) => n?.name && drill.push('facility', n.name, `station: ${n.name}`)} />
        </Panel>
      )}

      {summary.length > 0 && <PromotionNote summary={summary} />}
    </div>
  );
}

function CoverageNote({ coverage }) {
  const gaps = useMemo(() => {
    if (!coverage || !coverage.length) return [];
    const byTable = {};
    coverage.forEach((t) => {
      if (t.error || !t.categories) return;
      byTable[t.table] = new Set(t.categories.map((c) => String(c.category).toUpperCase()));
    });
    const predCats = byTable.ps1_failure_predictions;
    if (!predCats) return [];
    const out = [];
    predCats.forEach((cat) => {
      const missing = ['ps1_failure_summary', 'ps1_leaderboard', 'ps1_model_performance', 'ps1_feature_importance']
        .filter((tb) => {
          const s = byTable[tb];
          if (!s) return false;
          // ps1_failure_summary/leaderboard use the display name ("Gates"), the
          // rest use the code ("GATE") — match on prefix so both forms resolve.
          return ![...s].some((v) => v.startsWith(cat.slice(0, 3)));
        });
      if (missing.length) out.push({ cat, missing });
    });
    return out;
  }, [coverage]);
  if (!gaps.length) return null;
  return (
    <div className="card" style={{ marginBottom: 14, padding: '10px 14px', borderLeft: '4px solid #f59e0b' }}>
      <span style={{ fontSize: 12, color: 'var(--text-secondary)', lineHeight: 1.6 }}>
        <strong>Model metadata missing for {gaps.map((g) => g.cat).join(', ')}.</strong>{' '}
        These categories have prediction rows but no row in {gaps[0].missing.map((m) => (
          <code key={m} style={{ fontFamily: 'monospace' }}>{m} </code>
        ))}— so there is no champion, no promotion gate and no feature importance behind those predictions. The notebooks produce these
        artifacts; the loader has not written them yet.
      </span>
    </div>
  );
}

function PromotionNote({ summary }) {
  const failing = summary.filter((s) => !s.promoted);
  if (!failing.length) return null;
  return (
    <Panel title="Promotion status" live={false}
      note="A model below its recall floor is a watch signal, not a dispatch trigger. Recall is the gate because a missed failure costs a truck roll and an outage; a false alarm costs an inspection.">
      <table className="data-table">
        <thead><tr><th>Device</th><th>Champion</th><th style={{ textAlign: 'right' }}>Test recall</th><th style={{ textAlign: 'right' }}>Recall floor</th><th style={{ textAlign: 'right' }}>Base rate</th><th>Gate</th></tr></thead>
        <tbody>
          {failing.map((s) => (
            <tr key={s.device}>
              <td style={{ fontWeight: 700 }}>{s.device}</td>
              <td>{s.champion_model}</td>
              <td style={{ textAlign: 'right', fontWeight: 700, color: '#ef4444' }}>{num(s.test_recall, 4)}</td>
              <td style={{ textAlign: 'right', color: 'var(--text-secondary)' }}>{num(s.recall_floor, 2)}</td>
              <td style={{ textAlign: 'right', color: 'var(--text-secondary)' }}
                title="Share of the fleet that actually fails. Accuracy above (100 − base rate)% is what predicting 'never fails' scores.">
                {num(s.base_rate_pct, 2)}%
              </td>
              <td><span className="badge badge-critical">{s.quality_gate}</span></td>
            </tr>
          ))}
        </tbody>
      </table>
    </Panel>
  );
}

// --------------------------------------------------------------- Models ----
// 27-Jul-2026 (PK, item 2c). Gates accuracy is pinned for this run rather than
// read from the artifacts. It is the ONLY hardcoded metric anywhere in the tab,
// it is keyed by device category so it cannot leak onto another type, and the
// tile carries a visible marker plus a tooltip saying where the number came
// from. A metric that disagrees with its source and does not say so is the
// single easiest way to lose a room's trust in a dashboard.
const ACCURACY_PIN = { GATE: 0.904 };

function ModelsView({ summary, leaderboard, modelPerf, sweep, calib, confusion, featImp }) {
  // 27-Jul-2026 (PK, item 3). Fleet causation, fetched here rather than threaded
  // down from the tab, because it is the only panel in PS1 that needs it.
  //
  // Two independent methods over the SAME subsystem pairs, from the 27-Jul S3
  // load: association-rule lift (co-occurrence above chance) and Markov
  // transition probability (how often B actually follows A). Shown side by side
  // and never averaged -- a pair can be highly probable and barely lifted, which
  // means B is simply common, not that A causes it. Averaging hides exactly that.
  const [assoc, setAssoc] = useState([]);
  const [markov, setMarkov] = useState([]);
  useEffect(() => {
    let alive = true;
    apiGet('/ps2/associations', { city: 'CHI' })
      .then((d) => { if (alive && Array.isArray(d)) setAssoc(d); }).catch(() => {});
    apiGet('/ps2/markov', { city: 'CHI' })
      .then((d) => { if (alive && Array.isArray(d)) setMarkov(d); }).catch(() => {});
    return () => { alive = false; };
  }, []);

  // Join the two on the pair. An inner join is deliberate: a rule with no
  // transition probability, or a transition with no lift, is half an answer and
  // would sit in the table looking like a finding.
  const causePairs = useMemo(() => {
    const mk = {};
    markov.forEach((r) => { mk[`${r.from_sub}>${r.to_sub}`] = Number(r.prob); });
    return assoc
      .map((r) => ({
        from_sub: r.antecedent_subsystem,
        to_sub: r.consequent_subsystem,
        lift: Number(r.lift),
        confidence: Number(r.confidence),
        support: Number(r.support),
        prob: mk[`${r.antecedent_subsystem}>${r.consequent_subsystem}`],
      }))
      .filter((r) => Number.isFinite(r.lift) && Number.isFinite(r.prob))
      .sort((a, b) => b.lift - a.lift)
      .slice(0, 12);
  }, [assoc, markov]);

  const [hide, setHide] = useState(true);
  const byDevice = useMemo(() => {
    const g = {};
    leaderboard.forEach((r) => { (g[r.device] = g[r.device] || []).push(r); });
    return g;
  }, [leaderboard]);

  // 27-Jul-2026 (PK, items 2a/2b). Three boxes, always, one per device type in
  // TVM / GATE / VALIDATOR order.
  //
  // The previous version mapped straight over modelPerf, so a category missing
  // from that endpoint simply had no box -- the layout silently shrank to two
  // and gave no hint the third type existed. Now the row is built from
  // TYPE_ORDER and each card pulls from modelPerf first, then falls back to the
  // matching ps1_failure_summary row, then renders an explicit "not in this run"
  // state. The box count is a constant; only its contents vary.
  const cards = useMemo(() => {
    const perfByCat = {};
    (modelPerf || []).forEach((m) => { if (m.device_category) perfByCat[m.device_category] = m; });
    // summary is champion-first from the API, so the first row per category wins.
    const sumByCat = {};
    (summary || []).forEach((s) => {
      const k = DEVLABEL_TO_CAT[s.device] || String(s.device || '').toUpperCase();
      if (k && !(k in sumByCat)) sumByCat[k] = s;
    });
    // Third source, and on the 26-Jul run the ONLY one that fires:
    // ps1_model_performance is EMPTY (0 rows -- load_run's rows_after confirms
    // it), so /ps1/model-performance returns nothing. That is why the three
    // boxes disappeared: the old `modelPerf.length > 0 &&` guard hid the whole
    // row. The champion leaderboard row carries auc/ap/f1/prec/rec per type.
    const lbByCat = {};
    (leaderboard || []).forEach((r) => {
      const k = DEVLABEL_TO_CAT[r.device] || String(r.device || '').toUpperCase();
      if (!k) return;
      // champion wins; otherwise first seen (the API already orders champion-first)
      if (!(k in lbByCat) || (r.is_champion && !lbByCat[k].is_champion)) lbByCat[k] = r;
    });
    // Accuracy from the run's REAL confusion matrix: (TP+TN)/N. Derived, not
    // asserted, and the only honest way to fill this tile while
    // ps1_model_performance is empty. The base rate comes from the same matrix
    // -- the majority-class share, which is what accuracy has to be read
    // against and is NOT the positive rate when positives are the minority.
    const confByCat = {};
    (confusion || []).forEach((r) => {
      const tp = Number(r.tp) || 0, fp = Number(r.fp) || 0;
      const tn = Number(r.tn) || 0, fn = Number(r.fn) || 0;
      const n = tp + fp + tn + fn;
      if (!n || !r.device_category) return;
      const pos = tp + fn;
      confByCat[r.device_category] = {
        accuracy: (tp + tn) / n,
        base_rate_pct: (Math.max(pos, n - pos) / n) * 100,
        n_test: n,
      };
    });
    const pick = (...v) => v.find((x) => x !== null && x !== undefined && x !== '');
    return TYPE_ORDER.map((catKey) => {
      const m = perfByCat[catKey] || {};
      const s = sumByCat[catKey] || {};
      const lb = lbByCat[catKey] || {};
      const cf = confByCat[catKey] || {};
      const s3 = m.s3_metrics || {};
      const pinned = Object.prototype.hasOwnProperty.call(ACCURACY_PIN, catKey);
      const derivedAcc = pick(m.test_accuracy, m.accuracy, s.test_accuracy, cf.accuracy);
      const baseRate = pick(m.base_rate_pct, s.base_rate_pct, cf.base_rate_pct);
      const shownAcc = pinned ? ACCURACY_PIN[catKey] : derivedAcc;
      // Lift is recomputed from whatever accuracy is actually SHOWN, so the
      // colour and the "+N pts" note can never contradict the tile above them.
      const lift = (shownAcc != null && baseRate != null)
        ? (Number(shownAcc) * 100 - Number(baseRate)) : null;
      return {
        cat: catKey,
        present: Boolean(perfByCat[catKey] || sumByCat[catKey] || lbByCat[catKey]),
        model_name: pick(m.model_name, s.champion_model, lb.model),
        algorithm: pick(m.algorithm, s.champion_model, lb.model),
        model_version: m.model_version,
        status: pick(m.status, s.quality_gate),
        promoted: pick(m.promoted, s.promoted, lb.is_champion),
        endpoint_name: pick(m.endpoint_name, s.endpoint_name),
        n_features: pick(m.n_features, s.n_features),
        n_test: pick(m.n_test, s.n_test, cf.n_test),
        base_rate_pct: baseRate,
        accuracy_lift_over_base: lift,
        // The five PK asked for, in his order.
        pr_auc: pick(s3.test_ap, m.test_pr_auc, s.test_ap, lb.ap),
        f1: pick(s3.test_f1, m.test_f1, s.test_f1, lb.f1),
        precision: pick(s3.test_prec, m.test_precision, s.test_precision, lb.prec),
        recall: pick(s3.test_rec, m.test_recall, s.test_recall, lb.rec),
        accuracy: shownAcc,
        accuracy_pinned: pinned,
        accuracy_source: pinned ? derivedAcc : null,
      };
    });
  }, [modelPerf, summary, leaderboard, confusion]);

  // The same five metrics for all three types on one 0-1 axis. This is what
  // replaces the threshold sweep: the three cards each answer "how good is this
  // model", and this answers the question they cannot, which is "compared with
  // what". One axis is honest here because all five measures share the 0-1 range.
  const compare = useMemo(() => ([
    ['PR-AUC', 'pr_auc'], ['F1', 'f1'], ['Precision', 'precision'],
    ['Recall', 'recall'], ['Accuracy', 'accuracy'],
  ].map(([label, key]) => {
    const row = { metric: label };
    cards.forEach((c) => {
      const v = Number(c[key]);
      if (Number.isFinite(v)) row[TYPE_LABEL[c.cat] || c.cat] = Math.round(v * 10000) / 10000;
    });
    return row;
  }).filter((r) => Object.keys(r).length > 1)), [cards]);

  if (!summary.length && !leaderboard.length) {
    return <AwaitingRun title="Model scorecard" table="ps1_failure_summary / ps1_leaderboard"
      note="Populated from the run artifacts each training notebook writes." />;
  }
  return (
    <div style={{ marginTop: 16 }}>
      {/* 27-Jul-2026 (PK, item 2a). Three boxes upfront, one per device type,
          always rendered in TVM / GATE / VALIDATOR order. Driven by `cards`
          rather than by modelPerf directly, so a category the endpoint omits
          still gets a box that says so instead of vanishing from the row. */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(340px, 1fr))', gap: 16, marginBottom: 18 }}>
        {cards.map((m) => {
          const c = TCOL[m.cat] || '#64748b';
          return (
            <div key={m.cat} className="card" style={{ borderTop: `3px solid ${c}`, opacity: m.present ? 1 : 0.75 }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 12 }}>
                <div>
                  <span style={{ fontSize: 11, fontWeight: 700, color: c, textTransform: 'uppercase', letterSpacing: 0.5 }}>
                    {TYPE_LABEL[m.cat] || m.cat}
                  </span>
                  <div style={{ fontSize: 15, fontWeight: 700, marginTop: 2 }}>{m.model_name || '—'}</div>
                  {/* 27-Jul-2026. Only render the algorithm line when it says
                      something the name above does not. Both fall back to the
                      same leaderboard column, so without this the card printed
                      "xgboost" directly under "xgboost". */}
                  {(m.model_version || (m.algorithm && m.algorithm !== m.model_name)) && (
                    <div style={{ fontSize: 11, color: 'var(--text-secondary)', marginTop: 2 }}>
                      {m.algorithm && m.algorithm !== m.model_name ? m.algorithm : ''}
                      {m.algorithm && m.algorithm !== m.model_name && m.model_version ? ' · ' : ''}
                      {m.model_version ? `v${m.model_version}` : ''}
                    </div>
                  )}
                </div>
                {m.status && (
                  <span className={`badge ${m.promoted ? 'badge-success' : 'badge-high'}`}>{m.status}</span>
                )}
              </div>

              {m.present ? (
                <>
                  {/* 27-Jul-2026 (PK, item 2b). The five he asked for, in his
                      order, on every card: PR-AUC, F1, Precision, Recall,
                      Accuracy. AUC-ROC and the operating threshold came out --
                      they are still in the leaderboard table below, and five
                      tiles across a 300px card is already the practical limit
                      before the numbers stop being readable. */}
                  <div style={{ display: 'grid', gridTemplateColumns: 'repeat(5, 1fr)', gap: 5 }}>
                    <MiniStat label="PR-AUC" value={num(m.pr_auc, 4)} color="#3b82f6" />
                    <MiniStat label="F1" value={num(m.f1, 4)} />
                    <MiniStat label="Precision" value={num(m.precision, 4)} color="#3b82f6" />
                    <MiniStat label="Recall" value={num(m.recall, 4)} color="#3b82f6" />
                    <MiniStat
                      label={m.accuracy_pinned ? 'Accuracy *' : 'Accuracy'}
                      value={num(m.accuracy, 4)}
                      title={m.accuracy_pinned
                        ? `Set for this run at ${Number(m.accuracy).toFixed(3)}.${
                          m.accuracy_source != null
                            ? ` The run artifact reports ${Number(m.accuracy_source).toFixed(4)}.` : ''}`
                        : undefined}
                      color={m.accuracy_lift_over_base == null ? undefined
                        : Number(m.accuracy_lift_over_base) >= 10 ? '#22c55e'
                          : Number(m.accuracy_lift_over_base) >= 2 ? '#f59e0b' : '#ef4444'} />
                  </div>

                  {m.accuracy_pinned && (
                    <div style={{ marginTop: 8, fontSize: 10.5, color: 'var(--text-secondary)' }}>
                      * Accuracy set for this run at {Number(m.accuracy).toFixed(3)}
                      {m.accuracy_source != null
                        && ` · run artifact reports ${Number(m.accuracy_source).toFixed(4)}`}
                    </div>
                  )}

                  {/* Accuracy without its baseline is the most misleading number
                      on a model card, so the two never part company. */}
                  {m.base_rate_pct != null && (
                    <div style={{ marginTop: 8, fontSize: 11, color: 'var(--text-secondary)' }}>
                      Majority-class baseline {Number(m.base_rate_pct).toFixed(2)}%
                      {m.accuracy_lift_over_base != null && (
                        <strong style={{ marginLeft: 6,
                          color: Number(m.accuracy_lift_over_base) >= 10 ? '#22c55e'
                            : Number(m.accuracy_lift_over_base) >= 2 ? '#f59e0b' : '#ef4444' }}>
                          {Number(m.accuracy_lift_over_base) >= 0 ? '+' : ''}
                          {Number(m.accuracy_lift_over_base).toFixed(1)} pts
                        </strong>
                      )}
                      {m.n_test ? ` · ${intf(m.n_test)} held-out rows` : ''}
                    </div>
                  )}
                  <div style={{ marginTop: 10, fontSize: 11, color: 'var(--text-secondary)' }}>
                    Held-out metrics only{m.n_features ? ` · ${intf(m.n_features)} features` : ''}
                    {' · '}endpoint {m.endpoint_name || '—'}
                  </div>
                </>
              ) : (
                <div style={{ fontSize: 12, color: 'var(--text-secondary)', padding: '8px 0' }}>
                  No model row for this device type in the latest run — neither
                  ps1_model_performance nor ps1_failure_summary returned one.
                </div>
              )}
            </div>
          );
        })}
      </div>

      {/* 27-Jul-2026 (PK, item 2). The three "<device> leaderboard — deployed
          champion first" tables are removed. Each listed every candidate model
          per device type; the champion's numbers are already on the card above,
          and the non-champions are a training-time record rather than anything a
          reader acts on.

          What takes their place is item 3: feature importance and causation --
          why the model fires, and what follows when it does. */}

      {causePairs.length > 0 && (
        <Panel title="Causation — which subsystem failure pulls another after it"
          note={'Association-rule lift beside Markov transition probability for the same subsystem pair. '
            + 'Lift is how much more often the pair co-occurs than chance would give; probability is how '
            + 'often B actually follows A. Both are needed: a pair can be highly probable and barely '
            + 'lifted, which means B is common rather than caused. Sequential association over event '
            + 'chains is evidence for causation, not proof of it.'}>
          <table className="data-table">
            <thead>
              <tr>
                <th>Antecedent</th><th>Consequent</th>
                <th style={{ textAlign: 'right' }}>Lift</th>
                <th style={{ textAlign: 'right' }}>P(B follows A)</th>
                <th style={{ textAlign: 'right' }}>Confidence</th>
                <th style={{ textAlign: 'right' }}>Support</th>
              </tr>
            </thead>
            <tbody>
              {causePairs.map((r) => (
                <tr key={`${r.from_sub}-${r.to_sub}`}>
                  <td style={{ fontFamily: 'monospace', fontWeight: 600 }}>{r.from_sub}</td>
                  <td style={{ fontFamily: 'monospace', fontWeight: 600 }}>{r.to_sub}</td>
                  {/* Coloured on lift, not on probability. Lift above ~2 is the
                      one that says "more than chance"; a high probability on a
                      lift near 1 is a common subsystem and must not read green. */}
                  <td style={{ textAlign: 'right', fontWeight: 700,
                    color: r.lift >= 2 ? '#ef4444' : r.lift >= 1.2 ? '#f59e0b' : 'var(--text-secondary)' }}>
                    {num(r.lift, 2)}×
                  </td>
                  <td style={{ textAlign: 'right', fontWeight: 700 }}>{pct(r.prob)}</td>
                  <td style={{ textAlign: 'right', color: 'var(--text-secondary)' }}>{pct(r.confidence)}</td>
                  <td style={{ textAlign: 'right', color: 'var(--text-secondary)' }}>{num(r.support, 4)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Panel>
      )}

      {/* 27-Jul-2026 (PK, item 2d). Replaced the threshold sweep.
          The sweep answered "what happens if we move the cut-off", which is a
          tuning question that gets settled once per run and then stops changing.
          The three cards above each describe one model in isolation; nothing on
          the page put them beside each other.

          This does. Five metrics, three device types, one 0-1 axis -- legitimate
          here precisely because PR-AUC, F1, precision, recall and accuracy all
          live on the same scale, so no second axis is needed and none is used.
          Read across a group and the weakest model is obvious; read down and you
          can see which metric a given model is carried by. */}
      {compare.length > 0 && (
        <Panel title="Model comparison — the same five metrics across all three device types"
          note="Held-out metrics from this run, grouped by metric so the three models sit side by side. All five measures share the 0–1 range, so a single axis is correct and no second scale is introduced.">
          <BarPanel data={compare} xKey="metric"
            series={cards.filter((c) => c.present).map((c) => TYPE_LABEL[c.cat] || c.cat)}
            height={320} colorOf={(s) => TCOL[DEVLABEL_TO_CAT[s]] || '#6366f1'} />
        </Panel>
      )}

      {calib.length > 0 && (
        <Panel title="Calibration — predicted probability vs observed failure rate"
          note="A well-calibrated model tracks the diagonal. Systematic deviation means the probability is a ranking, not a probability.">
          <Calibration rows={calib} />
        </Panel>
      )}

      {confusion.length > 0 && <ConfusionPanel rows={confusion} />}

      {Object.entries(featImp).map(([cat, rows]) => (
        <Panel key={cat} title={`Feature importance (SHAP) — ${cat}`}>
          <BarPanel horizontal
            data={rows.slice(0, 20).map((f) => ({ feature: f.feature_name, importance: Number(f.avg_shap ?? f.avg_importance) || 0 }))}
            xKey="feature" series={['importance']} colorOf={() => TCOL[cat] || '#6366f1'}
            height={Math.max(240, Math.min(rows.length, 20) * 28)} />
        </Panel>
      ))}
    </div>
  );
}

// title (27-Jul-2026) surfaces provenance on hover. Used by the pinned Gates
// accuracy so the number can be traced to a decision rather than looking like it
// came straight out of the run artifact. Font drops 19 -> 18 because the
// scorecard now fits five tiles across a card instead of four.
function MiniStat({ label, value, color, title }) {
  return (
    <div title={title} style={{ textAlign: 'center', padding: '9px 4px', background: 'var(--bg)',
      borderRadius: 8, cursor: title ? 'help' : undefined }}>
      <div style={{ fontSize: 17, fontWeight: 800, color: color || 'var(--text-primary)',
        fontVariantNumeric: 'tabular-nums', letterSpacing: -0.2 }}>{value}</div>
      <div style={{ fontSize: 9.5, color: 'var(--text-secondary)', marginTop: 3,
        textTransform: 'uppercase', letterSpacing: 0.3, fontWeight: 600 }}>{label}</div>
    </div>
  );
}

// 27-Jul-2026: ThresholdSweep removed with its panel (PK, item 2d). The
// /ps1/threshold-sweep route and ps1_threshold_sweep table are untouched, so
// restoring the panel is a component away if the tuning question comes back.

function Calibration({ rows }) {
  const cats = [...new Set(rows.map((r) => r.device_category))];
  const [cat, setCat] = useState(cats[0]);
  const data = rows.filter((r) => r.device_category === cat)
    .map((r) => ({ predicted: Number(r.pred_mean), observed: Number(r.actual_rate), n: Number(r.n) }))
    .sort((a, b) => a.predicted - b.predicted);
  return (
    <>
      <div className="filter-group" style={{ marginBottom: 10 }}>
        <label className="filter-label">Device type</label>
        {cats.map((c) => (
          <button key={c} className={`filter-btn${cat === c ? ' active' : ''}`} onClick={() => setCat(c)}
            style={cat === c ? { background: TCOL[c] || '#6366f1', borderColor: TCOL[c] || '#6366f1', color: '#fff' } : undefined}>{c}</button>
        ))}
      </div>
      <BubblePanel data={data} xKey="predicted" yKey="observed" zKey="n"
        xLabel="Mean predicted probability" yLabel="Observed failure rate" zLabel="Devices in bin" height={320} />
    </>
  );
}

function ConfusionPanel({ rows }) {
  return (
    <Panel title="Confusion matrix — held-out"
      note="False negatives are the costly cell for PS1: a missed failure is an outage and a truck roll, while a false positive is an inspection.">
      <table className="data-table">
        <thead><tr><th>Device</th><th style={{ textAlign: 'right' }}>True pos</th><th style={{ textAlign: 'right' }}>False pos</th><th style={{ textAlign: 'right' }}>False neg</th><th style={{ textAlign: 'right' }}>True neg</th><th style={{ textAlign: 'right' }}>Recall</th><th style={{ textAlign: 'right' }}>Precision</th></tr></thead>
        <tbody>
          {rows.map((r) => {
            const tp = Number(r.tp), fp = Number(r.fp), fn = Number(r.fn), tn = Number(r.tn);
            const rec = tp + fn ? tp / (tp + fn) : null;
            const prec = tp + fp ? tp / (tp + fp) : null;
            return (
              <tr key={r.device_category}>
                <td style={{ fontWeight: 700, color: TCOL[r.device_category] }}>{r.device_category}</td>
                <td style={{ textAlign: 'right', color: '#22c55e', fontWeight: 600 }}>{intf(tp)}</td>
                <td style={{ textAlign: 'right', color: '#f59e0b' }}>{intf(fp)}</td>
                <td style={{ textAlign: 'right', color: '#ef4444', fontWeight: 700 }}>{intf(fn)}</td>
                <td style={{ textAlign: 'right', color: 'var(--text-secondary)' }}>{intf(tn)}</td>
                <td style={{ textAlign: 'right', fontWeight: 700 }}>{rec === null ? '—' : num(rec, 4)}</td>
                <td style={{ textAlign: 'right' }}>{prec === null ? '—' : num(prec, 4)}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </Panel>
  );
}

// -------------------------------------------------------------- Devices ----
function DeviceView({ rows, total, state, drill, apiBase, onAnalyse }) {
  const t = useSortPage(rows, { key: 'failure_probability', dir: 'desc' }, 50);

  // 27-Jul-2026. The table sorts by probability descending, so the first screen
  // is necessarily the top of the distribution -- which read as "every validator
  // is about to fail". Measured over the 1,922 loaded rows the truth is the
  // opposite shape: median 0.30%, 1,085 devices under 1%, 591 over 99%, almost
  // nothing between. The sort was manufacturing the impression.
  //
  // The fix is context, not deletion: the fleet shape is stated above the table
  // and drawn as a strip, so a reader sees where the visible rows sit within it.
  const shape = useMemo(() => {
    const v = rows.map((r) => Number(r.failure_probability))
      .filter((x) => Number.isFinite(x)).sort((a, b) => a - b);
    if (!v.length) return null;
    const thr = Number(rows[0]?.decision_threshold) || null;
    // Ten equal probability bands. Equal-width, not quantile: the point is to
    // show that the mass piles into the two end bands, and quantile bins would
    // hide exactly that by construction.
    const bins = Array.from({ length: 10 }, (_, i) => ({
      lo: i / 10, hi: (i + 1) / 10, n: 0,
      label: `${i * 10}–${(i + 1) * 10}%`,
    }));
    v.forEach((x) => { bins[Math.min(9, Math.floor(x * 10))].n += 1; });
    return {
      n: v.length, median: v[Math.floor(v.length / 2)],
      under01: v.filter((x) => x < 0.01).length,
      over099: v.filter((x) => x > 0.99).length,
      aboveThr: thr === null ? null : v.filter((x) => x >= thr).length,
      threshold: thr, bins, max: Math.max(...bins.map((b) => b.n)),
    };
  }, [rows]);

  if (state !== 'ok') {
    return <AwaitingRun title="Device predictions" table="ps1_failure_predictions"
      note="Predictions are ranked within each device_category, so every modelled type appears rather than the highest-probability type filling the response." />;
  }
  return (
    <>
    {shape && (
      <div className="card" style={{ marginBottom: 14, padding: 14 }}>
        <div className="card-header">Where this fleet actually sits on the probability scale</div>
        <div style={{ display: 'flex', gap: 24, flexWrap: 'wrap', margin: '8px 0 12px' }}>
          <div><div className="kpi-label">Median probability</div>
            <div style={{ fontSize: 20, fontWeight: 800 }}>{pct(shape.median, 2)}</div></div>
          <div><div className="kpi-label">Under 1%</div>
            <div style={{ fontSize: 20, fontWeight: 800, color: '#22c55e' }}>
              {intf(shape.under01)}<span style={{ fontSize: 12, fontWeight: 500 }}> of {intf(shape.n)}</span></div></div>
          <div><div className="kpi-label">Over 99%</div>
            <div style={{ fontSize: 20, fontWeight: 800, color: RISK.CRITICAL }}>
              {intf(shape.over099)}</div></div>
          {shape.aboveThr !== null && (
            <div><div className="kpi-label">Above threshold {pct(shape.threshold, 1)}</div>
              <div style={{ fontSize: 20, fontWeight: 800 }}>{intf(shape.aboveThr)}
                <span style={{ fontSize: 12, fontWeight: 500 }}> ({((shape.aboveThr / shape.n) * 100).toFixed(1)}%)</span></div></div>
          )}
        </div>
        <div style={{ display: 'flex', gap: 3, alignItems: 'flex-end', height: 60 }}>
          {shape.bins.map((b) => (
            <div key={b.lo} style={{ flex: 1, textAlign: 'center' }}
              title={`${b.label}: ${b.n.toLocaleString()} device(s)`}>
              <div style={{ height: 46, display: 'flex', alignItems: 'flex-end' }}>
                <div style={{ width: '100%', height: `${Math.max(2, (b.n / (shape.max || 1)) * 100)}%`,
                  background: shape.threshold !== null && b.lo >= shape.threshold ? RISK.CRITICAL : '#6366f1',
                  borderRadius: '3px 3px 0 0' }} />
              </div>
              <div style={{ fontSize: 9, color: 'var(--text-secondary)' }}>{b.lo * 100}</div>
            </div>
          ))}
        </div>
        <div style={{ fontSize: 11.5, color: 'var(--text-secondary)', marginTop: 10, lineHeight: 1.6 }}>
          The model is <strong>bimodal</strong> — it answers near-0 or near-1 and rarely hedges. The table
          below is sorted highest-first, so it opens on the right-hand spike; that is the sort, not the
          fleet. Devices above the decision threshold are red. Flagging {shape.aboveThr !== null
            ? `${((shape.aboveThr / shape.n) * 100).toFixed(1)}%` : 'this share'} of devices is consistent
          with the measured 38.51% base rate of 3-day OOS on the validator test set, so the volume is
          expected rather than alarming.
        </div>
      </div>
    )}
    <Panel title="Device predictions — ranked within each device type"
      right={<ExportButton rows={t.sorted} filename="ps1_device_predictions.csv" />}
      note="Rank is per device type on purpose: a fleet-wide sort by probability returns one category only, because probability scales differ per model. Analyse opens the Device 360; Stage records the payload in servicenow_staging for review — it does not post to ServiceNow.">
      <table className="data-table">
        <thead>
          <tr>
            <SortTh label="Rank" col="cat_rank" sort={t.sort} setSort={t.setSort} align="right" />
            <SortTh label="Device" col="device_id" sort={t.sort} setSort={t.setSort} />
            <SortTh label="Type" col="device_category" sort={t.sort} setSort={t.setSort} />
            <SortTh label="Station" col="station_name" sort={t.sort} setSort={t.setSort} />
            <SortTh label="Failure prob." col="failure_probability" sort={t.sort} setSort={t.setSort} align="right" />
            <SortTh label="Threshold" col="decision_threshold" sort={t.sort} setSort={t.setSort} align="right" />
            <th>Band</th>
            <th>Flagged</th>
            <SortTh label="Error code" col="dom_error_code" sort={t.sort} setSort={t.setSort} />
            <SortTh label="Scored" col="prediction_date" sort={t.sort} setSort={t.setSort} />
            <th>Actions</th>
          </tr>
        </thead>
        <VirtualTBody rows={t.slice} renderRow={(p) => {
          const band = bandOfRow(p);
          return (
            <tr key={p.prediction_id}>
              <td style={{ textAlign: 'right', color: 'var(--text-secondary)' }}>{intf(p.cat_rank)}</td>
              <td style={{ fontFamily: 'monospace', fontWeight: 600, cursor: 'pointer' }}
                onClick={() => drill.push('device', p.device_id, `device: ${p.device_id}`)}>{p.device_id}</td>
              <td><span className="badge badge-info" style={{ background: hexA(TCOL[p.device_category] || '#64748b', 0.12), color: TCOL[p.device_category] || '#64748b' }}>{p.device_category}</span></td>
              <td style={{ fontSize: 12 }}>{p.station_name || (p.facility_id ?? '—')}</td>
              <td style={{ textAlign: 'right', fontWeight: 700, color: band ? RISK[band] : undefined }}>{pct(p.failure_probability, 2)}</td>
              <td style={{ textAlign: 'right', color: 'var(--text-secondary)' }}>{pct(p.decision_threshold, 2)}</td>
              <td><Badge band={band} /></td>
              <td>{p.predicted_label ? <span className="badge badge-critical">yes</span> : <span className="badge badge-info">no</span>}</td>
              <td style={{ fontFamily: 'monospace', fontSize: 11 }}>{p.dom_error_code || '—'}</td>
              <td style={{ fontSize: 12, color: 'var(--text-secondary)' }}>{dayOf(p.prediction_date)}</td>
              <td>
                <div style={{ display: 'flex', gap: 6 }}>
                  <button onClick={() => onAnalyse(p.device_id)}
                    style={{ background: '#6366f1', color: '#fff', border: 'none', borderRadius: 6, padding: '3px 9px', fontSize: 11, fontWeight: 600, cursor: 'pointer' }}>
                    Analyse
                  </button>
                  <ServiceNowButton apiBase={apiBase} psId="ps1" compact
                    deviceId={p.device_id} deviceCategory={p.device_category}
                    shortDescription={`PS1 ${p.device_category} ${p.device_id}: ${(Number(p.failure_probability) * 100).toFixed(1)}% 3-day OOS risk (threshold ${(Number(p.decision_threshold) * 100).toFixed(1)}%)`}
                    payload={{ ps: 'PS1', grain: 'device', ...p }} />
                </div>
              </td>
            </tr>
          );
        }} />
      </table>
      <Pager page={t.page} pages={t.pages} total={t.sorted.length} setPage={t.setPage} label="devices"
        pageSize={t.pageSize} setPageSize={t.setPageSize} />
    </Panel>
    </>
  );
}

// ------------------------------------------------------------ Components ----
function ComponentView({ rows, total, state, bubble, drill, apiBase, onAnalyse }) {
  const t = useSortPage(rows, { key: 'serial_risk_score', dir: 'desc' }, 50);
  if (state !== 'ok') {
    return <AwaitingRun title="Component / serial risk" table="ps1_serial_predictions"
      note="Keyed (city_id, run_id, device_id, matched_serial_nbr). The route exists; the loader must write the table for this view to populate." />;
  }
  const devs = new Set(rows.map((r) => r.device_id)).size;
  return (
    <div>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 16, marginBottom: 18 }}>
        <Kpi label="Component rows (in view)" value={intf(rows.length)} sub={`of ${intf(total)} scored`} />
        <Kpi label="Devices covered" value={intf(devs)} sub="distinct device_id" />
        <Kpi label="Components per device" value={devs ? num(rows.length / devs, 2) : '—'} sub="mean fan-out" />
        <Kpi label="Component types" value={intf(new Set(rows.map((r) => r.component_type)).size)} sub="distinct component_type" />
      </div>
      {bubble.length > 0 && (
        <Panel title="Component risk vs age"
          note="Bubble area is the attribution weight — the share of the device's risk assigned to that component. Risk score is the device probability times that weight, so a small component on a high-risk device does not inherit the whole risk.">
          <BubblePanel data={bubble} xKey="age" yKey="risk" zKey="weight"
            xLabel="Component age (days)" yLabel="Serial risk score (%)" zLabel="Attribution weight (%)"
            colorKey="device_category" height={340}
            onDrill={(p) => p?.device_id && drill.push('device', p.device_id, `device: ${p.device_id}`)} />
        </Panel>
      )}
      <Panel title="Component risk — device_id × matched_serial_nbr"
        right={<ExportButton rows={t.sorted} filename="ps1_component_risk.csv" />}>
        <table className="data-table">
          <thead>
            <tr>
              <SortTh label="Device" col="device_id" sort={t.sort} setSort={t.setSort} />
              <SortTh label="Serial" col="matched_serial_nbr" sort={t.sort} setSort={t.setSort} />
              <SortTh label="Type" col="device_category" sort={t.sort} setSort={t.setSort} />
              <SortTh label="Component" col="component_type" sort={t.sort} setSort={t.setSort} />
              <SortTh label="Age (d)" col="component_age_days" sort={t.sort} setSort={t.setSort} align="right" />
              <SortTh label="Device prob." col="device_failure_probability" sort={t.sort} setSort={t.setSort} align="right" />
              <SortTh label="Attribution" col="attribution_weight" sort={t.sort} setSort={t.setSort} align="right" />
              <SortTh label="Serial risk" col="serial_risk_score" sort={t.sort} setSort={t.setSort} align="right" />
              <th>Band</th>
              <th>Actions</th>
            </tr>
          </thead>
          <VirtualTBody rows={t.slice} renderRow={(s) => (
            <tr key={`${s.device_id}-${s.matched_serial_nbr}`}>
              <td style={{ fontFamily: 'monospace', fontWeight: 600, cursor: 'pointer' }}
                onClick={() => drill.push('device', s.device_id, `device: ${s.device_id}`)}>{s.device_id}</td>
              <td style={{ fontFamily: 'monospace', fontSize: 11 }}>{s.matched_serial_nbr}</td>
              <td><span className="badge badge-info" style={{ background: hexA(TCOL[s.device_category] || '#64748b', 0.12), color: TCOL[s.device_category] || '#64748b' }}>{s.device_category}</span></td>
              <td style={{ cursor: s.component_type ? 'pointer' : 'default' }}
                onClick={s.component_type ? () => drill.push('component', s.component_type, `component: ${s.component_type}`) : undefined}>
                {s.component_type || '—'}
              </td>
              <td style={{ textAlign: 'right' }}>{num(s.component_age_days, 0)}</td>
              <td style={{ textAlign: 'right' }}>{pct(s.device_failure_probability, 2)}</td>
              <td style={{ textAlign: 'right', color: 'var(--text-secondary)' }}>{pct(s.attribution_weight, 1)}</td>
              <td style={{ textAlign: 'right', fontWeight: 700 }}>{pct(s.serial_risk_score, 2)}</td>
              <td><Badge band={s.risk_band ? String(s.risk_band).toUpperCase() : null} /></td>
              <td>
                <div style={{ display: 'flex', gap: 6 }}>
                  <button onClick={() => onAnalyse(s.device_id)}
                    style={{ background: '#6366f1', color: '#fff', border: 'none', borderRadius: 6, padding: '3px 9px', fontSize: 11, fontWeight: 600, cursor: 'pointer' }}>
                    Analyse
                  </button>
                  <ServiceNowButton apiBase={apiBase} psId="ps1" compact
                    deviceId={s.device_id} deviceCategory={s.device_category}
                    shortDescription={`PS1 ${s.device_category} ${s.device_id} component ${s.component_type || s.matched_serial_nbr}: serial risk ${(Number(s.serial_risk_score) * 100).toFixed(1)}%`}
                    payload={{ ps: 'PS1', grain: 'serial', ...s }} />
                </div>
              </td>
            </tr>
          )} />
        </table>
        <Pager page={t.page} pages={t.pages} total={t.sorted.length} setPage={t.setPage} label="component rows"
          pageSize={t.pageSize} setPageSize={t.setPageSize} />
      </Panel>
    </div>
  );
}

// ------------------------------------------------------------- Stations ----
function StationView({ rows, state, drill }) {
  const t = useSortPage(rows, { key: 'predicted_failures', dir: 'desc' }, 50);
  if (state !== 'ok') {
    return <AwaitingRun title="Station summary" table="ps1_station_summary" />;
  }
  const top = [...rows].sort((a, b) => Number(b.predicted_failures) - Number(a.predicted_failures)).slice(0, 15)
    .map((r) => ({ name: `Facility ${r.facility_id}`, value: Number(r.predicted_failures) }));
  return (
    <div>
      <Panel title="Stations by predicted failures"
        note="Counts of devices flagged at each station in the latest batch.">
        <ParetoPanel data={top} labelKey="name" valueKey="value" valueName="Predicted failures"
          onDrill={(p) => p?.name && drill.push('facility', p.name.replace('Facility ', ''), `station: ${p.name}`)} />
      </Panel>
      <Panel title="Station detail" right={<ExportButton rows={t.sorted} filename="ps1_stations.csv" />}>
        <table className="data-table">
          <thead>
            <tr>
              <SortTh label="Facility" col="facility_id" sort={t.sort} setSort={t.setSort} />
              <SortTh label="Devices" col="total_devices" sort={t.sort} setSort={t.setSort} align="right" />
              <SortTh label="Predicted failures" col="predicted_failures" sort={t.sort} setSort={t.setSort} align="right" />
              <SortTh label="Avg risk %" col="avg_risk_pct" sort={t.sort} setSort={t.setSort} align="right" />
              <SortTh label="Critical" col="critical_count" sort={t.sort} setSort={t.setSort} align="right" />
              <SortTh label="High" col="high_count" sort={t.sort} setSort={t.setSort} align="right" />
              <SortTh label="Last inference" col="last_inference_date" sort={t.sort} setSort={t.setSort} />
            </tr>
          </thead>
          <VirtualTBody rows={t.slice} renderRow={(r) => (
            <tr key={r.facility_id}>
              <td style={{ fontWeight: 600, cursor: 'pointer' }}
                onClick={() => drill.push('facility', r.facility_id, `station: ${r.facility_id}`)}>{r.facility_id}</td>
              <td style={{ textAlign: 'right' }}>{intf(r.total_devices)}</td>
              <td style={{ textAlign: 'right', fontWeight: 700 }}>{intf(r.predicted_failures)}</td>
              <td style={{ textAlign: 'right' }}>{num(r.avg_risk_pct, 2)}%</td>
              <td style={{ textAlign: 'right', color: RISK.CRITICAL }}>{intf(r.critical_count)}</td>
              <td style={{ textAlign: 'right', color: RISK.HIGH }}>{intf(r.high_count)}</td>
              <td style={{ fontSize: 12, color: 'var(--text-secondary)' }}>{dayOf(r.last_inference_date)}</td>
            </tr>
          )} />
        </table>
        <Pager page={t.page} pages={t.pages} total={t.sorted.length} setPage={t.setPage} label="stations"
          pageSize={t.pageSize} setPageSize={t.setPageSize} />
      </Panel>
    </div>
  );
}

// ------------------------------------------------------------- Cross-tab ---
const PIVOT_DIMS = [
  { value: 'device_category', label: 'Device type' },
  { value: 'risk_band', label: 'Risk band' },
  { value: 'facility', label: 'Facility' },
  { value: 'predicted_label', label: 'Flagged' },
  { value: 'prediction_date', label: 'Scoring date' },
  { value: 'device', label: 'Device ID' },
];
const DIM_TO_DRILL = { device_category: 'category', risk_band: 'band', facility: 'facility', device: 'device' };

// 27-Jul-2026. The cross-tab was a bare rows x cols picker. Powerful, but it
// asked the reader to already know which pair was worth looking at, so it stayed
// on the default and read as filler. These presets are the questions the rest of
// the PS1 tab raises and cannot answer, each of which is exactly one pivot.
const PIVOT_PRESETS = [
  { key: 'where', label: 'Which stations hold the risk?', rows: 'facility', cols: 'risk_band',
    why: 'Station against risk band. A station concentrating CRITICAL devices is a routing decision — send one engineer to one place rather than five to five.' },
  { key: 'flag', label: 'Where is the model actually firing?', rows: 'device_category', cols: 'predicted_label',
    why: 'Device type against the flag. Read the flagged share against the base rate: on validators 38.51% of device-days really do go OOS within 3 days, so ~40% flagged is expected, not alarming.' },
  { key: 'drift', label: 'Is risk moving over time?', rows: 'prediction_date', cols: 'risk_band',
    why: 'Scoring date against risk band. A band mix that shifts between runs is either the fleet changing or the model drifting — and you want to know which before the client asks.' },
  { key: 'typeband', label: 'Do the device types differ in risk?', rows: 'device_category', cols: 'risk_band',
    why: 'Device type against risk band. Probability scales differ per model, so compare the band mix across types rather than raw probabilities.' },
  { key: 'devband', label: 'Which devices sit at the top?', rows: 'device', cols: 'risk_band',
    why: 'Device against risk band, busiest first. Use it to pull a shortlist without leaving the aggregate view.' },
];

function PivotView({ pivot, rowsDim, colsDim, setRowsDim, setColsDim, drill }) {
  const lab = (v) => PIVOT_DIMS.find((d) => d.value === v)?.label || v;
  // Counts answer "how many", row-% answers "what share". Share is what makes a
  // small station readable beside a large one. Same server-side aggregate.
  const [asPct, setAsPct] = useState(false);
  const active = PIVOT_PRESETS.find((x) => x.rows === rowsDim && x.cols === colsDim);
  return (
    <div>
      <div className="card" style={{ marginBottom: 14, padding: 14 }}>
        <div className="card-header">Start from a question</div>
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, marginTop: 10 }}>
          {PIVOT_PRESETS.map((x) => (
            <button key={x.key} type="button" className="filter-btn"
              onClick={() => { setRowsDim(x.rows); setColsDim(x.cols); }} title={x.why}
              style={active?.key === x.key ? { background: 'var(--primary)',
                borderColor: 'var(--primary)', color: '#fff', fontWeight: 700 } : undefined}>
              {x.label}
            </button>
          ))}
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
              <button key={String(v)} type="button" className="filter-btn" onClick={() => setAsPct(v)}
                style={asPct === v ? { background: 'var(--primary)', borderColor: 'var(--primary)', color: '#fff' } : undefined}>
                {l}
              </button>
            ))}
          </div>
        </div>
      </div>
      <Panel title={`Cross-tab — ${lab(rowsDim)} × ${lab(colsDim)}`}
        note="Aggregated server-side over the whole latest batch, not a client-side summary of one page. Click a cell to drill both dimensions.">
        {pivot.state === 'loading' && <NoRows msg="Loading cross-tab…" />}
        {pivot.state === 'err' && <NoRows msg={`Cross-tab unavailable: ${pivot.err}`} />}
        {pivot.state === 'empty' && <NoRows msg="No prediction rows for this batch yet." />}
        {pivot.state === 'ok' && (
          <PivotMatrix cells={pivot.data} rowLabel={lab(rowsDim)} colLabel={lab(colsDim)} asPct={asPct}
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

// --------------------------------------------------------------- Lineage ---
function LineageView({ runs, state, coverage }) {
  return (
    <div style={{ marginTop: 16 }}>
      {state === 'ok' && runs.length > 0 ? (
        <Panel title="Inference runs — most recent first"
          note="From ps1_inference_runs. Every prediction row belongs to exactly one run_id, so a batch can be traced back to the endpoint, model version and threshold that produced it.">
          <table className="data-table">
            <thead>
              <tr>
                <th>Run</th><th>When</th><th>Kind</th><th>Device type</th><th>Scored for</th>
                <th>Endpoint</th><th>Version</th>
                <th style={{ textAlign: 'right' }}>Threshold</th>
                <th style={{ textAlign: 'right' }}>Devices</th>
                <th style={{ textAlign: 'right' }}>Flagged</th>
                <th>Status</th>
              </tr>
            </thead>
            <tbody>
              {runs.map((r) => (
                <tr key={`${r.run_id}-${r.device_category}`}>
                  <td style={{ fontFamily: 'monospace', fontSize: 11 }}>{r.run_id}</td>
                  <td style={{ fontSize: 12 }}>{dayOf(r.run_ts)}</td>
                  <td><Badge>{r.run_kind}</Badge></td>
                  <td style={{ fontWeight: 600, color: TCOL[r.device_category] }}>{r.device_category}</td>
                  <td style={{ fontSize: 12 }}>{dayOf(r.scoring_date)}</td>
                  <td style={{ fontFamily: 'monospace', fontSize: 11 }}>{r.endpoint_name || '—'}</td>
                  <td>{r.model_version || '—'}</td>
                  <td style={{ textAlign: 'right' }}>{pct(r.decision_threshold, 2)}</td>
                  <td style={{ textAlign: 'right' }}>{intf(r.n_devices_scored)}</td>
                  <td style={{ textAlign: 'right', fontWeight: 700 }}>{intf(r.n_flagged)}</td>
                  <td><span className={`badge ${r.status === 'success' ? 'badge-success' : 'badge-critical'}`}>{r.status}</span></td>
                </tr>
              ))}
            </tbody>
          </table>
        </Panel>
      ) : (
        <AwaitingRun title="Inference runs" table="ps1_inference_runs"
          note="Written by the batch scorer. Without it, a prediction cannot be traced to the model version and threshold that produced it." />
      )}

      {coverage && coverage.length > 0 && (
        <Panel title="Table coverage by device type"
          note="Which PS1 tables actually hold rows for each device category. A category with predictions but no summary, leaderboard or feature-importance row has no model metadata behind its numbers.">
          <table className="data-table">
            <thead><tr><th>Table</th><th>Categories present</th></tr></thead>
            <tbody>
              {coverage.map((t) => (
                <tr key={t.table}>
                  <td style={{ fontFamily: 'monospace', fontSize: 12 }}>{t.table}</td>
                  <td>
                    {t.error ? <span style={{ color: 'var(--text-secondary)' }}>{t.error}</span>
                      : (t.categories && t.categories.length
                        ? t.categories.map((c) => (
                          <span key={String(c.category)} className="badge badge-info" style={{ marginRight: 6 }}>
                            {String(c.category)} · {Number(c.n).toLocaleString()}
                          </span>
                        ))
                        : <span style={{ color: '#f59e0b' }}>no rows</span>)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </Panel>
      )}
    </div>
  );
}
