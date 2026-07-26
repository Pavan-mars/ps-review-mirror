import React, { useState, useMemo, useEffect, useContext, useCallback } from 'react';
import {
  BarChart, Bar, Cell, XAxis, YAxis, CartesianGrid, Tooltip, Legend,
  ResponsiveContainer, PieChart, Pie,
} from 'recharts';
import FilterContext from '../../context/FilterContext';

// PS5 live data comes straight from the Aurora-backed ps5-api (its own API
// Gateway) and is fetched inline here, so this tab needs NO change to the shared
// src/data/api.js — which the PS1 & PS3 work streams also evolve. /ps5/devices
// and /ps5/serials are only served by this gateway (not the main dashboard-api).
// Override the base with VITE_PS5_API_BASE_URL if the gateway ever changes.
const PS5_BASE = (import.meta.env.VITE_PS5_API_BASE_URL
  || 'https://b1s4xxlddb.execute-api.us-east-1.amazonaws.com').replace(/\/$/, '');
async function ps5Get(path, city) {
  const res = await fetch(`${PS5_BASE}${path}?city=${encodeURIComponent(city)}`);
  if (res.status === 404) return null; // no PS5 run for this city (CHI-only pilot)
  if (!res.ok) throw new Error(`PS5 API ${path} -> ${res.status}`);
  return res.json();
}
async function apiPS5DeviceRUL(city) {
  const d = await ps5Get('/ps5/devices', city);
  return (d && Array.isArray(d.devices) && d.devices.length) ? d : null;
}
async function apiPS5SerialHealth(city) {
  const d = await ps5Get('/ps5/serials', city);
  return (d && Array.isArray(d.serials) && d.serials.length) ? d : null;
}

// ============================================================================
// PS5 · Reliability / RUL — native real-data analytics view.
// Replaces the six legacy mock SLA sub-tabs (SLA Overview / Downtime / Failure
// Metrics / Breach / Compliance / Reliability Metrics) with a single, live view
// driven entirely by the Aurora-backed ps5-api (/ps5/devices + /ps5/serials).
// No sample/mock data: it shows fresh RDS rows or a clean loading/empty/error
// state. Styling reuses the dashboard design tokens (index.css .card/.badge/
// .data-table/.filter-btn) and the severity colour scale so it reads native.
// ============================================================================

// Risk palette == index.css severity scale (--severity-critical/high/medium/low)
const RISK = { CRITICAL: '#ef4444', HIGH: '#f97316', MEDIUM: '#eab308', LOW: '#3b82f6' };
const RISK_ORDER = ['CRITICAL', 'HIGH', 'MEDIUM', 'LOW'];
const RISK_BADGE = { CRITICAL: 'badge-critical', HIGH: 'badge-high', MEDIUM: 'badge-medium', LOW: 'badge-low' };
// Device-type accents — deliberately outside the severity reds/ambers so type
// and risk never collide visually (primary indigo / sky / violet).
const TCOL = { TVM: '#6366f1', GATE: '#0ea5e9', VALIDATOR: '#8b5cf6' };
const TYPE_ORDER = ['TVM', 'GATE', 'VALIDATOR'];
const TYPE_LABEL = { TVM: 'TVMs', GATE: 'Gates', VALIDATOR: 'Validators' };
// map the global FilterBar device labels -> ps5-api mars_device_category
const DEVLABEL_TO_CAT = { TVMs: 'TVM', Gates: 'GATE', Validators: 'VALIDATOR' };
const COMP_LABEL = {
  tvmsbc: 'TVM SBC', cbxid: 'CBX ID', billacceptor: 'Bill Acceptor',
  coinacceptor: 'Coin Acceptor', AV2_SAM: 'AV2 SAM', SIM_ICCID: 'SIM ICCID',
  mpos: 'mPOS', None: 'Unclassified', null: 'Unclassified', '': 'Unclassified',
};

const num = (v, d = 1) => (v === null || v === undefined || Number.isNaN(v) ? '—' : Number(v).toFixed(d));
const intf = (v) => (v === null || v === undefined ? '—' : Number(v).toLocaleString());
const facLabel = (f) => (f === null || f === undefined || f === '' ? '—' : String(f).replace(/\.0$/, ''));
const compLabel = (c) => COMP_LABEL[c] ?? (c || 'Unclassified');
const hexA = (hex, a) => {
  const h = hex.replace('#', '');
  const r = parseInt(h.slice(0, 2), 16), g = parseInt(h.slice(2, 4), 16), b = parseInt(h.slice(4, 6), 16);
  return `rgba(${r},${g},${b},${a})`;
};
const median = (arr) => {
  const a = arr.filter((x) => x !== null && x !== undefined && !Number.isNaN(x)).sort((x, y) => x - y);
  if (!a.length) return null;
  const m = Math.floor(a.length / 2);
  return a.length % 2 ? a[m] : (a[m - 1] + a[m]) / 2;
};
// RUL health colour (matches the CityOverview health scale)
const rulColor = (d) => (d == null ? '#64748b' : d <= 7 ? '#ef4444' : d <= 30 ? '#f97316' : d <= 90 ? '#eab308' : '#10b981');

const RUL_BINS = [
  { key: '0–7d', lo: 0, hi: 7, color: '#ef4444' },
  { key: '7–30d', lo: 7, hi: 30, color: '#f97316' },
  { key: '30–90d', lo: 30, hi: 90, color: '#eab308' },
  { key: '90–365d', lo: 90, hi: 365, color: '#3b82f6' },
  { key: '365d+', lo: 365, hi: Infinity, color: '#10b981' },
];

const PAGE = 25;

// ---- small presentational helpers ------------------------------------------
function Chip({ active, color, onClick, children, title }) {
  return (
    <button
      className={`filter-btn${active ? ' active' : ''}`}
      onClick={onClick}
      title={title}
      style={active && color ? { background: color, borderColor: color, color: '#fff' } : undefined}
    >
      {children}
    </button>
  );
}

function RiskBadge({ band }) {
  if (!band) return <span className="badge badge-info">—</span>;
  return <span className={`badge ${RISK_BADGE[band] || 'badge-info'}`}>{band}</span>;
}

function SortTh({ label, col, sort, setSort, align }) {
  const active = sort.key === col;
  return (
    <th
      onClick={() => setSort((s) => ({ key: col, dir: s.key === col && s.dir === 'asc' ? 'desc' : 'asc' }))}
      style={{ cursor: 'pointer', whiteSpace: 'nowrap', textAlign: align || 'left', userSelect: 'none' }}
      title="Click to sort"
    >
      {label}{active ? (sort.dir === 'asc' ? ' ▲' : ' ▼') : ''}
    </th>
  );
}

function Pager({ page, pages, total, setPage, label }) {
  if (total === 0) return null;
  return (
    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginTop: 12, fontSize: 12, color: 'var(--text-secondary)' }}>
      <span>{total.toLocaleString()} {label} · page {page + 1} of {pages}</span>
      <div style={{ display: 'flex', gap: 6 }}>
        <button className="filter-btn" disabled={page === 0} onClick={() => setPage(0)} style={{ opacity: page === 0 ? 0.4 : 1 }}>« First</button>
        <button className="filter-btn" disabled={page === 0} onClick={() => setPage((p) => Math.max(0, p - 1))} style={{ opacity: page === 0 ? 0.4 : 1 }}>‹ Prev</button>
        <button className="filter-btn" disabled={page >= pages - 1} onClick={() => setPage((p) => Math.min(pages - 1, p + 1))} style={{ opacity: page >= pages - 1 ? 0.4 : 1 }}>Next ›</button>
        <button className="filter-btn" disabled={page >= pages - 1} onClick={() => setPage(pages - 1)} style={{ opacity: page >= pages - 1 ? 0.4 : 1 }}>Last »</button>
      </div>
    </div>
  );
}

// ---- lightweight, dependency-free Sankey (device type -> risk band) ---------
function TypeRiskSankey({ matrix, typeCounts, riskCounts, total, onFlow }) {
  const W = 720, H = 300, PAD = 18, COLW = 150, x1 = COLW, x2 = W - COLW;
  const types = TYPE_ORDER.filter((t) => typeCounts[t] > 0);
  const risks = RISK_ORDER.filter((r) => riskCounts[r] > 0);
  if (!total || !types.length || !risks.length) {
    return <div style={{ padding: 24, textAlign: 'center', color: 'var(--text-secondary)', fontSize: 13 }}>No rows in the current selection.</div>;
  }
  const gap = 10;
  const availL = H - 2 * PAD - gap * Math.max(0, types.length - 1);
  const availR = H - 2 * PAD - gap * Math.max(0, risks.length - 1);
  const scaleL = availL / total, scaleR = availR / total;
  // left nodes
  const lNodes = {}; let ly = PAD;
  types.forEach((t) => { const h = typeCounts[t] * scaleL; lNodes[t] = { y: ly, h, off: 0 }; ly += h + gap; });
  // right nodes
  const rNodes = {}; let ry = PAD;
  risks.forEach((r) => { const h = riskCounts[r] * scaleR; rNodes[r] = { y: ry, h, off: 0 }; ry += h + gap; });
  const ribbons = [];
  types.forEach((t) => {
    risks.forEach((r) => {
      const c = (matrix[t] && matrix[t][r]) || 0;
      if (!c) return;
      const wl = c * scaleL, wr = c * scaleR;
      const y1 = lNodes[t].y + lNodes[t].off; lNodes[t].off += wl;
      const y2 = rNodes[r].y + rNodes[r].off; rNodes[r].off += wr;
      const xm = (x1 + x2) / 2;
      const d = `M ${x1} ${y1} C ${xm} ${y1}, ${xm} ${y2}, ${x2} ${y2} `
              + `L ${x2} ${y2 + wr} C ${xm} ${y2 + wr}, ${xm} ${y1 + wl}, ${x1} ${y1 + wl} Z`;
      ribbons.push({ d, t, r, c });
    });
  });
  return (
    <svg viewBox={`0 0 ${W} ${H}`} width="100%" height={H} style={{ maxWidth: '100%' }} role="img" aria-label="Device type to risk band flow">
      {ribbons.map((rb, i) => (
        <path key={i} d={rb.d} fill={hexA(RISK[rb.r], 0.45)} stroke="none"
          style={{ cursor: onFlow ? 'pointer' : 'default' }}
          onClick={onFlow ? () => onFlow(rb.t, rb.r) : undefined}>
          <title>{`${TYPE_LABEL[rb.t]} → ${rb.r}: ${rb.c.toLocaleString()} devices`}</title>
        </path>
      ))}
      {types.map((t) => (
        <g key={t}>
          <rect x={x1 - 10} y={lNodes[t].y} width={10} height={Math.max(1, lNodes[t].h)} rx={2} fill={TCOL[t]} />
          <text x={x1 - 16} y={lNodes[t].y + lNodes[t].h / 2} textAnchor="end" dominantBaseline="middle" fontSize={12} fontWeight={600} fill="#1e293b">
            {TYPE_LABEL[t]} ({typeCounts[t].toLocaleString()})
          </text>
        </g>
      ))}
      {risks.map((r) => (
        <g key={r}>
          <rect x={x2} y={rNodes[r].y} width={10} height={Math.max(1, rNodes[r].h)} rx={2} fill={RISK[r]} />
          <text x={x2 + 16} y={rNodes[r].y + rNodes[r].h / 2} textAnchor="start" dominantBaseline="middle" fontSize={12} fontWeight={600} fill="#1e293b">
            {r} ({riskCounts[r].toLocaleString()})
          </text>
        </g>
      ))}
    </svg>
  );
}

// ---- device drill-down modal (light, matches .card design) ------------------
function DeviceModal({ device, serials, meta, onClose }) {
  const [showPayload, setShowPayload] = useState(false);
  const comps = useMemo(
    () => serials.filter((s) => s.device_id === device.device_id).sort((a, b) => (b.risk_score || 0) - (a.risk_score || 0)),
    [serials, device.device_id],
  );
  const payload = useMemo(() => ({
    source: 'CUBIC MARS PS5 · Reliability/RUL',
    event_def_version: meta?.event_def_version,
    as_of_date: meta?.as_of_date,
    device_id: device.device_id,
    device_category: device.mars_device_category,
    facility_id: facLabel(device.facility_id),
    risk_band: device.risk_band,
    rul_standard_days: device.rul_standard_days,
    rul_p10_days: device.rul_p10_days,
    rul_p90_days: device.rul_p90_days,
    hazard_score: device.hazard_score,
    is_overdue: device.is_overdue,
    short_description: `[PS5] ${device.risk_band} reliability risk on ${device.mars_device_category} ${device.device_id}`
      + ` — RUL ${num(device.rul_standard_days)}d${device.is_overdue ? ' (OVERDUE)' : ''}`,
    high_risk_components: comps.filter((c) => c.risk_tier === 'CRITICAL' || c.risk_tier === 'HIGH')
      .slice(0, 20).map((c) => ({ serial: c.component_serial_nbr, type: c.component_type, tier: c.risk_tier, expected_rul_days: c.expected_component_rul_days })),
  }), [device, comps, meta]);

  return (
    <div onClick={onClose} style={{ position: 'fixed', inset: 0, background: 'rgba(15,23,42,0.55)', zIndex: 1000, display: 'flex', alignItems: 'flex-start', justifyContent: 'center', overflowY: 'auto', padding: '4vh 2vw' }}>
      <div onClick={(e) => e.stopPropagation()} className="card" style={{ width: 'min(960px, 96vw)', padding: 22, boxShadow: '0 20px 60px rgba(15,23,42,0.35)' }}>
        <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', marginBottom: 16 }}>
          <div>
            <div style={{ fontSize: 20, fontWeight: 800, fontFamily: 'monospace', color: 'var(--text)' }}>{device.device_id}</div>
            <div style={{ fontSize: 12, color: 'var(--text-secondary)', marginTop: 3 }}>
              {TYPE_LABEL[device.mars_device_category] || device.mars_device_category} · Facility {facLabel(device.facility_id)} · PS5 device 360
            </div>
          </div>
          <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
            <RiskBadge band={device.risk_band} />
            {device.is_overdue && <span className="badge badge-critical">OVERDUE</span>}
            <button onClick={onClose} className="filter-btn" style={{ fontSize: 16, lineHeight: 1, padding: '4px 10px' }}>✕</button>
          </div>
        </div>

        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 12, marginBottom: 16 }}>
          <div className="card" style={{ padding: 14 }}>
            <div className="card-header" style={{ marginBottom: 6 }}>RUL (standard)</div>
            <div className="kpi-value" style={{ fontSize: 24, color: rulColor(device.rul_standard_days) }}>{num(device.rul_standard_days)}<span style={{ fontSize: 13, color: 'var(--text-secondary)' }}> d</span></div>
            <div className="kpi-label">P10–P90 {num(device.rul_p10_days)}–{num(device.rul_p90_days)}d</div>
          </div>
          <div className="card" style={{ padding: 14 }}>
            <div className="card-header" style={{ marginBottom: 6 }}>Hazard score</div>
            <div className="kpi-value" style={{ fontSize: 24 }}>{num(device.hazard_score, 3)}</div>
            <div className="kpi-label">C-index {num(device.concordance_index, 3)}</div>
          </div>
          <div className="card" style={{ padding: 14 }}>
            <div className="card-header" style={{ marginBottom: 6 }}>Since HW-OOS</div>
            <div className="kpi-value" style={{ fontSize: 24 }}>{num(device.days_since_hw_oos, 0)}<span style={{ fontSize: 13, color: 'var(--text-secondary)' }}> d</span></div>
            <div className="kpi-label">Fails / 30d: {intf(device.roll_fail_30d)}</div>
          </div>
          <div className="card" style={{ padding: 14 }}>
            <div className="card-header" style={{ marginBottom: 6 }}>Data-quality gate</div>
            <div style={{ marginTop: 4 }}><span className={`badge ${device.data_quality_gate_passed ? 'badge-success' : 'badge-critical'}`}>{device.data_quality_gate_passed ? 'Gate pass' : 'Below floor'}</span></div>
            <div className="kpi-label" style={{ marginTop: 8 }}>Healthy age {num(device.current_healthy_age_days, 0)}d</div>
          </div>
        </div>

        <div className="card" style={{ padding: 16, marginBottom: 16 }}>
          <div className="card-header">Components on this device ({comps.length})</div>
          {comps.length ? (
            <div style={{ overflowX: 'auto', maxHeight: 260, overflowY: 'auto' }}>
              <table className="data-table">
                <thead><tr><th>Serial</th><th>Component</th><th>Age (d)</th><th>OOS fails</th><th>Risk score</th><th>Tier</th><th>Exp. RUL (d)</th></tr></thead>
                <tbody>
                  {comps.map((c, i) => (
                    <tr key={i}>
                      <td style={{ fontFamily: 'monospace', fontSize: 11 }}>{c.component_serial_nbr}</td>
                      <td>{compLabel(c.component_type)}</td>
                      <td>{num(c.component_age_days, 0)}</td>
                      <td>{intf(c.device_oos_failures_total)}</td>
                      <td style={{ fontWeight: 600 }}>{num(c.risk_score, 1)}</td>
                      <td><RiskBadge band={c.risk_tier} /></td>
                      <td>{num(c.expected_component_rul_days)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : <div style={{ fontSize: 13, color: 'var(--text-secondary)', padding: 8 }}>No component-serial rows for this device.</div>}
        </div>

        <div className="card" style={{ padding: 16, borderLeft: '3px solid var(--secondary)' }}>
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap', gap: 8 }}>
            <div>
              <div className="card-header" style={{ marginBottom: 2 }}>ServiceNow · scheduled maintenance</div>
              <div style={{ fontSize: 11, color: 'var(--text-secondary)' }}>Builds an incident payload for review. No live post until the client SN API is provisioned.</div>
            </div>
            <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
              <span className="badge badge-medium" title="Awaiting client ServiceNow API access">ServiceNow integration — in progress</span>
              <button className="filter-btn" onClick={() => setShowPayload((s) => !s)}>{showPayload ? 'Hide' : 'View'} payload</button>
            </div>
          </div>
          {showPayload && (
            <pre style={{ marginTop: 12, background: '#0f172a', color: '#e2e8f0', padding: 12, borderRadius: 8, fontSize: 11, overflowX: 'auto', maxHeight: 260 }}>{JSON.stringify(payload, null, 2)}</pre>
          )}
        </div>
      </div>
    </div>
  );
}

export default function PS5SLAReliabilityTab({ city, selectedDevices }) {
  // Read the always-on global FilterBar without hard-coupling (harness-safe).
  const fctx = useContext(FilterContext) || {};
  const globalDevices = fctx.selectedDevices;         // ['TVMs','Gates','Validators'] | undefined
  const deviceQuery = fctx.deviceQuery ?? '';
  const setDeviceQuery = fctx.setDeviceQuery || (() => {});

  // ---- live data (real RDS rows only; own loading/empty/error state) --------
  const [dev, setDev] = useState({ state: 'loading' });
  const [ser, setSer] = useState({ state: 'loading' });

  useEffect(() => {
    let alive = true;
    setDev({ state: 'loading' }); setSer({ state: 'loading' });
    apiPS5DeviceRUL(city)
      .then((d) => { if (alive) setDev(d ? { state: 'ok', data: d } : { state: 'empty' }); })
      .catch((e) => { if (alive) setDev({ state: 'err', err: String(e) }); });
    apiPS5SerialHealth(city)
      .then((d) => { if (alive) setSer(d ? { state: 'ok', data: d } : { state: 'empty' }); })
      .catch((e) => { if (alive) setSer({ state: 'err', err: String(e) }); });
    return () => { alive = false; };
  }, [city]);

  const meta = dev.data || {};
  const devices = useMemo(() => (dev.state === 'ok' ? dev.data.devices : []), [dev]);
  const serials = useMemo(() => (ser.state === 'ok' ? ser.data.serials : []), [ser]);

  // ---- filter state ---------------------------------------------------------
  const [typeSel, setTypeSel] = useState([...TYPE_ORDER]);
  const [riskSel, setRiskSel] = useState([...RISK_ORDER]);
  const [compSel, setCompSel] = useState(null);   // null = all (set once serials load)
  const [facility, setFacility] = useState('');
  const [overdueOnly, setOverdueOnly] = useState(false);
  const [rulMax, setRulMax] = useState('');
  const [sortDev, setSortDev] = useState({ key: 'rul_standard_days', dir: 'asc' });
  const [sortSer, setSortSer] = useState({ key: 'risk_score', dir: 'desc' });
  const [devPage, setDevPage] = useState(0);
  const [serPage, setSerPage] = useState(0);
  const [drill, setDrill] = useState(null);
  const [showInsights, setShowInsights] = useState(false);
  const [showBatch, setShowBatch] = useState(false);

  const compTypes = useMemo(() => {
    const s = new Set(serials.map((r) => r.component_type ?? 'None'));
    return Array.from(s).sort((a, b) => compLabel(a).localeCompare(compLabel(b)));
  }, [serials]);
  useEffect(() => { if (compSel === null && compTypes.length) setCompSel([...compTypes]); }, [compTypes, compSel]);

  const facilities = useMemo(() => {
    const s = new Set(devices.map((d) => facLabel(d.facility_id)));
    return Array.from(s).filter((x) => x !== '—').sort((a, b) => Number(a) - Number(b));
  }, [devices]);

  // global device-type gate (mapped) intersected with local chips
  const globalCats = useMemo(() => {
    if (!Array.isArray(globalDevices)) return null; // no provider -> allow all
    return new Set(globalDevices.map((d) => DEVLABEL_TO_CAT[d]).filter(Boolean));
  }, [globalDevices]);

  const q = deviceQuery.trim().toLowerCase();
  const rulCap = rulMax === '' ? Infinity : Number(rulMax);

  const typePass = useCallback((cat) => typeSel.includes(cat) && (!globalCats || globalCats.has(cat)), [typeSel, globalCats]);

  const fDevices = useMemo(() => devices.filter((d) => {
    if (!typePass(d.mars_device_category)) return false;
    if (!riskSel.includes(d.risk_band)) return false;
    if (facility && facLabel(d.facility_id) !== facility) return false;
    if (overdueOnly && !d.is_overdue) return false;
    if (d.rul_standard_days != null && d.rul_standard_days > rulCap) return false;
    if (q && !(String(d.device_id).toLowerCase().includes(q) || facLabel(d.facility_id).toLowerCase().includes(q))) return false;
    return true;
  }), [devices, typePass, riskSel, facility, overdueOnly, rulCap, q]);

  const compPass = useCallback((c) => !compSel || compSel.includes(c ?? 'None'), [compSel]);
  const fSerials = useMemo(() => serials.filter((s) => {
    if (!typePass(s.mars_device_category)) return false;
    if (!compPass(s.component_type)) return false;
    if (riskSel.length < RISK_ORDER.length && !riskSel.includes(s.risk_tier)) return false;
    if (overdueOnly && !s.is_overdue) return false;
    if (q && !(String(s.device_id).toLowerCase().includes(q) || String(s.component_serial_nbr).toLowerCase().includes(q) || compLabel(s.component_type).toLowerCase().includes(q))) return false;
    return true;
  }), [serials, typePass, compPass, riskSel, overdueOnly, q]);

  useEffect(() => { setDevPage(0); }, [typeSel, riskSel, facility, overdueOnly, rulMax, q]);
  useEffect(() => { setSerPage(0); }, [typeSel, riskSel, compSel, overdueOnly, q]);

  // ---- derived analytics ----------------------------------------------------
  const kpi = useMemo(() => {
    const n = fDevices.length;
    const overdue = fDevices.filter((d) => d.is_overdue).length;
    const critical = fDevices.filter((d) => d.risk_band === 'CRITICAL').length;
    const gatePass = fDevices.filter((d) => d.data_quality_gate_passed).length;
    const med = median(fDevices.map((d) => d.rul_standard_days));
    return { n, overdue, critical, gatePass, med, overduePct: n ? Math.round((overdue / n) * 100) : 0, gatePct: n ? Math.round((gatePass / n) * 100) : 0 };
  }, [fDevices]);

  const { matrix, typeCounts, riskCounts } = useMemo(() => {
    const m = {}; const tc = {}; const rc = {};
    TYPE_ORDER.forEach((t) => { m[t] = {}; tc[t] = 0; RISK_ORDER.forEach((r) => { m[t][r] = 0; }); });
    RISK_ORDER.forEach((r) => { rc[r] = 0; });
    fDevices.forEach((d) => {
      const t = d.mars_device_category, r = d.risk_band;
      if (m[t] && m[t][r] !== undefined) { m[t][r] += 1; tc[t] += 1; rc[r] += 1; }
    });
    return { matrix: m, typeCounts: tc, riskCounts: rc };
  }, [fDevices]);

  const riskByType = useMemo(
    () => TYPE_ORDER.filter((t) => typeCounts[t] > 0).map((t) => ({ type: TYPE_LABEL[t], cat: t, ...matrix[t] })),
    [matrix, typeCounts],
  );

  const rulHist = useMemo(() => RUL_BINS.map((b) => ({
    key: b.key, color: b.color,
    count: fDevices.filter((d) => d.rul_standard_days != null && d.rul_standard_days >= b.lo && d.rul_standard_days < b.hi).length,
  })), [fDevices]);

  const facilityTop = useMemo(() => {
    const by = {};
    fDevices.forEach((d) => {
      const f = facLabel(d.facility_id);
      if (!by[f]) by[f] = { facility: f, CRITICAL: 0, HIGH: 0, MEDIUM: 0, LOW: 0, total: 0 };
      by[f][d.risk_band] = (by[f][d.risk_band] || 0) + 1; by[f].total += 1;
    });
    return Object.values(by).sort((a, b) => (b.CRITICAL - a.CRITICAL) || (b.total - a.total)).slice(0, 12);
  }, [fDevices]);

  const compMix = useMemo(() => {
    const by = {};
    fSerials.forEach((s) => {
      const c = compLabel(s.component_type);
      if (!by[c]) by[c] = { comp: c, CRITICAL: 0, HIGH: 0, MEDIUM: 0, LOW: 0, total: 0 };
      by[c][s.risk_tier] = (by[c][s.risk_tier] || 0) + 1; by[c].total += 1;
    });
    return Object.values(by).sort((a, b) => b.total - a.total);
  }, [fSerials]);

  const insights = useMemo(() => {
    const crit = fDevices.filter((d) => d.risk_band === 'CRITICAL');
    const overdueCrit = crit.filter((d) => d.is_overdue);
    const byFac = {};
    crit.forEach((d) => { const f = facLabel(d.facility_id); byFac[f] = (byFac[f] || 0) + 1; });
    const topFac = Object.entries(byFac).sort((a, b) => b[1] - a[1]).slice(0, 3);
    const critDevIds = new Set(fDevices.filter((d) => d.risk_band === 'CRITICAL').map((d) => d.device_id));
    const linkedComps = fSerials.filter((s) => s.risk_tier === 'CRITICAL' && critDevIds.has(s.device_id)).length;
    return { critCount: crit.length, overdueCrit: overdueCrit.length, topFac, linkedComps };
  }, [fDevices, fSerials]);

  const batchPayload = useMemo(() => {
    const candidates = fDevices.filter((d) => d.risk_band === 'CRITICAL' && d.is_overdue)
      .sort((a, b) => (a.rul_standard_days ?? 1e9) - (b.rul_standard_days ?? 1e9)).slice(0, 50);
    return {
      source: 'CUBIC MARS PS5 · Reliability/RUL',
      event_def_version: meta.event_def_version,
      as_of_date: meta.as_of_date,
      city,
      selection_summary: { filtered_devices: fDevices.length, critical: insights.critCount, critical_overdue: insights.overdueCrit },
      candidate_count: candidates.length,
      candidates: candidates.map((d) => ({
        device_id: d.device_id, device_category: d.mars_device_category, facility_id: facLabel(d.facility_id),
        risk_band: d.risk_band, rul_standard_days: d.rul_standard_days, is_overdue: d.is_overdue,
        short_description: `[PS5] ${d.risk_band} reliability risk on ${d.mars_device_category} ${d.device_id} — RUL ${num(d.rul_standard_days)}d (OVERDUE)`,
      })),
    };
  }, [fDevices, insights, meta, city]);

  // ---- sorting + pagination -------------------------------------------------
  const sortedDev = useMemo(() => {
    const arr = [...fDevices];
    const { key, dir } = sortDev; const s = dir === 'asc' ? 1 : -1;
    arr.sort((a, b) => {
      const av = a[key], bv = b[key];
      if (av == null) return 1; if (bv == null) return -1;
      if (typeof av === 'string' || typeof bv === 'string') return String(av).localeCompare(String(bv)) * s;
      return (av - bv) * s;
    });
    return arr;
  }, [fDevices, sortDev]);
  const devPages = Math.max(1, Math.ceil(sortedDev.length / PAGE));
  const devRows = sortedDev.slice(devPage * PAGE, devPage * PAGE + PAGE);

  const sortedSer = useMemo(() => {
    const arr = [...fSerials];
    const { key, dir } = sortSer; const s = dir === 'asc' ? 1 : -1;
    arr.sort((a, b) => {
      const av = a[key], bv = b[key];
      if (av == null) return 1; if (bv == null) return -1;
      if (typeof av === 'string' || typeof bv === 'string') return String(av).localeCompare(String(bv)) * s;
      return (av - bv) * s;
    });
    return arr;
  }, [fSerials, sortSer]);
  const serPages = Math.max(1, Math.ceil(sortedSer.length / PAGE));
  const serRows = sortedSer.slice(serPage * PAGE, serPage * PAGE + PAGE);

  // ---- filter mutators ------------------------------------------------------
  const toggleType = (t) => setTypeSel((p) => (p.includes(t) ? p.filter((x) => x !== t) : [...p, t]));
  const toggleRisk = (r) => setRiskSel((p) => (p.includes(r) ? p.filter((x) => x !== r) : [...p, r]));
  const toggleComp = (c) => setCompSel((p) => (p && p.includes(c) ? p.filter((x) => x !== c) : [...(p || []), c]));
  const resetFilters = () => {
    setTypeSel([...TYPE_ORDER]); setRiskSel([...RISK_ORDER]); setCompSel([...compTypes]);
    setFacility(''); setOverdueOnly(false); setRulMax(''); setDeviceQuery('');
  };

  // ---- states: loading / empty / error --------------------------------------
  const cityName = (fctx.selectedCities && city) || city;
  if (dev.state === 'loading') {
    return <div className="card" style={{ padding: 40, textAlign: 'center', color: 'var(--text-secondary)' }}>Loading PS5 reliability data for {cityName}…</div>;
  }
  if (dev.state === 'err') {
    return (
      <div className="card" style={{ padding: 28, borderLeft: '4px solid var(--danger)' }}>
        <div className="card-header" style={{ color: 'var(--danger)' }}>PS5 API unreachable</div>
        <p style={{ fontSize: 13, color: 'var(--text-secondary)', marginTop: 8 }}>Could not load live reliability data: {dev.err}</p>
      </div>
    );
  }
  if (dev.state === 'empty') {
    return (
      <div className="card" style={{ padding: 32, textAlign: 'center' }}>
        <div className="card-header">PS5 · Reliability / RUL</div>
        <p style={{ color: 'var(--text-secondary)', marginTop: 12 }}>No PS5 reliability run exists for {cityName} yet. Chicago is currently the only city with a completed PS5 run.</p>
      </div>
    );
  }

  const allTypes = typeSel.length === TYPE_ORDER.length;
  const allRisks = riskSel.length === RISK_ORDER.length;
  const allComps = compSel && compTypes.length && compSel.length === compTypes.length;

  return (
    <div>
      {/* provenance band */}
      <div className="card" style={{ marginBottom: 16, padding: 14, display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: 10, borderLeft: '4px solid var(--primary)' }}>
        <span style={{ padding: '4px 10px', borderRadius: 6, background: hexA('#6366f1', 0.12), color: '#4f46e5', fontSize: 12, fontWeight: 700 }}>
          Failure = {(meta.event_definition || 'hardware OOS (Set)').replace(/^\s*failure\s*=\s*/i, '')}
</span>
        <span style={{ fontSize: 12, color: 'var(--text-secondary)' }}>v{meta.event_def_version}</span>
        <span style={{ fontSize: 12, color: 'var(--text-secondary)' }}>· Window: {meta.window}</span>
        <span style={{ fontSize: 12, color: 'var(--text-secondary)' }}>· Promotion floor C-index ≥ {meta.floor}</span>
        <span style={{ fontSize: 12, color: 'var(--text-secondary)' }}>· As-of {meta.as_of_date}</span>
        <span className="badge badge-success" style={{ marginLeft: 'auto' }} title="Live from Aurora RDS">● LIVE · RDS</span>
        <span className="badge badge-medium" title="Awaiting client ServiceNow API access">ServiceNow integration — in progress</span>
      </div>

      {/* filter bar (reuses the global .filter-* look) */}
      <div className="filter-bar" style={{ borderRadius: 12, border: '1px solid var(--border)', marginBottom: 16 }}>
        <div className="filter-group">
          <label className="filter-label">Search</label>
          <input type="text" value={deviceQuery} onChange={(e) => setDeviceQuery(e.target.value)} placeholder="Device ID / serial / facility…"
            style={{ padding: '5px 10px', borderRadius: 6, border: '1px solid var(--border)', fontSize: 12, minWidth: 210, color: 'var(--text)', background: '#fff' }} />
          {deviceQuery && <button className="filter-btn" onClick={() => setDeviceQuery('')} title="Clear search">×</button>}
        </div>
        <div className="filter-separator" />
        <div className="filter-group">
          <label className="filter-label">Device type</label>
          <button className={`filter-btn${allTypes ? ' all-active' : ''}`} onClick={() => setTypeSel([...TYPE_ORDER])}>All</button>
          {TYPE_ORDER.map((t) => <Chip key={t} active={typeSel.includes(t)} color={TCOL[t]} onClick={() => toggleType(t)}>{TYPE_LABEL[t]}</Chip>)}
        </div>
        <div className="filter-separator" />
        <div className="filter-group">
          <label className="filter-label">Risk band</label>
          <button className={`filter-btn${allRisks ? ' all-active' : ''}`} onClick={() => setRiskSel([...RISK_ORDER])}>All</button>
          {RISK_ORDER.map((r) => <Chip key={r} active={riskSel.includes(r)} color={RISK[r]} onClick={() => toggleRisk(r)}>{r}</Chip>)}
        </div>
        <div className="filter-separator" />
        <div className="filter-group">
          <label className="filter-label">Facility</label>
          <select value={facility} onChange={(e) => setFacility(e.target.value)}
            style={{ padding: '5px 10px', borderRadius: 6, border: '1px solid var(--border)', fontSize: 12, color: 'var(--text)', background: '#fff', cursor: 'pointer' }}>
            <option value="">All ({facilities.length})</option>
            {facilities.map((f) => <option key={f} value={f}>Facility {f}</option>)}
          </select>
        </div>
        <div className="filter-group">
          <label className="filter-label">RUL ≤ (days)</label>
          <input type="number" min="0" value={rulMax} onChange={(e) => setRulMax(e.target.value)} placeholder="∞"
            style={{ padding: '5px 8px', borderRadius: 6, border: '1px solid var(--border)', fontSize: 12, width: 74, color: 'var(--text)', background: '#fff' }} />
        </div>
        <div className="filter-group">
          <button className={`filter-btn${overdueOnly ? ' active' : ''}`} onClick={() => setOverdueOnly((v) => !v)}
            style={overdueOnly ? { background: 'var(--danger)', borderColor: 'var(--danger)', color: '#fff' } : undefined}>Overdue only</button>
        </div>
        <div className="filter-group" style={{ marginLeft: 'auto' }}>
          <button className="filter-btn" onClick={resetFilters}>Reset</button>
          <button className="filter-btn" onClick={() => setShowInsights(true)}
            style={{ background: 'var(--primary)', borderColor: 'var(--primary)', color: '#fff', fontWeight: 600 }}>Analyse selection</button>
        </div>
      </div>

      {/* component-type facet (appears once serials load) */}
      {compTypes.length > 0 && (
        <div className="filter-bar" style={{ borderRadius: 12, border: '1px solid var(--border)', marginBottom: 16 }}>
          <div className="filter-group">
            <label className="filter-label">Component type</label>
            <button className={`filter-btn${allComps ? ' all-active' : ''}`} onClick={() => setCompSel([...compTypes])}>All</button>
            {compTypes.map((c) => <Chip key={c} active={!!compSel && compSel.includes(c)} onClick={() => toggleComp(c)}>{compLabel(c)}</Chip>)}
          </div>
        </div>
      )}

      {/* KPI row */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(5, 1fr)', gap: 16, marginBottom: 20 }}>
        <div className="card"><div className="card-header">Devices (in view)</div><div className="kpi-value">{kpi.n.toLocaleString()}</div><div className="kpi-label">of {devices.length.toLocaleString()} scored</div></div>
        <div className="card"><div className="card-header">Overdue</div><div className="kpi-value" style={{ color: 'var(--danger)' }}>{kpi.overdue.toLocaleString()}</div><div className="kpi-label">{kpi.overduePct}% of view</div></div>
        <div className="card"><div className="card-header">Critical risk</div><div className="kpi-value" style={{ color: RISK.CRITICAL }}>{kpi.critical.toLocaleString()}</div><div className="kpi-label">highest-priority band</div></div>
        <div className="card"><div className="card-header">Median RUL</div><div className="kpi-value" style={{ color: rulColor(kpi.med) }}>{kpi.med == null ? '—' : num(kpi.med)}<span style={{ fontSize: 14, color: 'var(--text-secondary)' }}> d</span></div><div className="kpi-label">standard-window</div></div>
        <div className="card"><div className="card-header">Data-quality gate</div><div className="kpi-value" style={{ color: kpi.gatePct >= 90 ? 'var(--success)' : 'var(--secondary)' }}>{kpi.gatePct}%</div><div className="kpi-label">{kpi.gatePass.toLocaleString()} passing</div></div>
      </div>

      {/* overview charts */}
      <div className="grid-2" style={{ marginBottom: 20 }}>
        <div className="card">
          <div className="card-header">Risk band by device type — click a segment to filter</div>
          <ResponsiveContainer width="100%" height={300}>
            <BarChart data={riskByType} margin={{ top: 8, right: 12, bottom: 4, left: 4 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" />
              <XAxis dataKey="type" tick={{ fontSize: 12 }} />
              <YAxis tick={{ fontSize: 11 }} allowDecimals={false} />
              <Tooltip />
              <Legend />
              {RISK_ORDER.map((r) => (
                <Bar key={r} dataKey={r} name={r} stackId="a" fill={RISK[r]} cursor="pointer"
                  onClick={() => setRiskSel([r])} radius={r === 'LOW' ? [4, 4, 0, 0] : undefined} />
              ))}
            </BarChart>
          </ResponsiveContainer>
        </div>
        <div className="card">
          <div className="card-header">Remaining-useful-life distribution (days)</div>
          <ResponsiveContainer width="100%" height={300}>
            <BarChart data={rulHist} margin={{ top: 8, right: 12, bottom: 4, left: 4 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" />
              <XAxis dataKey="key" tick={{ fontSize: 11 }} />
              <YAxis tick={{ fontSize: 11 }} allowDecimals={false} />
              <Tooltip />
              <Bar dataKey="count" name="Devices" radius={[4, 4, 0, 0]}>
                {rulHist.map((b, i) => <Cell key={i} fill={b.color} />)}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        </div>
      </div>

      <div className="grid-2" style={{ marginBottom: 20 }}>
        <div className="card">
          <div className="card-header">Device type → risk band flow</div>
          <TypeRiskSankey matrix={matrix} typeCounts={typeCounts} riskCounts={riskCounts} total={kpi.n}
            onFlow={(t, r) => { setTypeSel([t]); setRiskSel([r]); }} />
        </div>
        <div className="card">
          <div className="card-header">Top facilities by critical devices — click a bar to focus</div>
          <ResponsiveContainer width="100%" height={300}>
            <BarChart data={facilityTop} layout="vertical" margin={{ top: 4, right: 12, bottom: 4, left: 8 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" />
              <XAxis type="number" tick={{ fontSize: 11 }} allowDecimals={false} />
              <YAxis type="category" dataKey="facility" width={70} tick={{ fontSize: 11 }} tickFormatter={(f) => `Fac ${f}`} />
              <Tooltip />
              <Legend />
              {RISK_ORDER.map((r) => (
                <Bar key={r} dataKey={r} name={r} stackId="f" fill={RISK[r]} cursor="pointer" onClick={(d) => d && setFacility(d.facility)} />
              ))}
            </BarChart>
          </ResponsiveContainer>
        </div>
      </div>

      {/* pivot + component mix */}
      <div className="grid-2" style={{ marginBottom: 20 }}>
        <div className="card" style={{ overflowX: 'auto' }}>
          <div className="card-header">Pivot · device type × risk band — click a cell to drill</div>
          <table className="data-table">
            <thead>
              <tr><th>Type</th>{RISK_ORDER.map((r) => <th key={r} style={{ textAlign: 'center' }}>{r}</th>)}<th style={{ textAlign: 'center' }}>Total</th></tr>
            </thead>
            <tbody>
              {TYPE_ORDER.map((t) => {
                const rowTotal = RISK_ORDER.reduce((a, r) => a + (matrix[t]?.[r] || 0), 0);
                const mx = Math.max(1, ...RISK_ORDER.map((r) => matrix[t]?.[r] || 0));
                return (
                  <tr key={t}>
                    <td style={{ fontWeight: 600 }}><span style={{ display: 'inline-block', width: 8, height: 8, borderRadius: 2, background: TCOL[t], marginRight: 8 }} />{TYPE_LABEL[t]}</td>
                    {RISK_ORDER.map((r) => {
                      const v = matrix[t]?.[r] || 0;
                      return (
                        <td key={r} onClick={() => v && (setTypeSel([t]), setRiskSel([r]))}
                          style={{ textAlign: 'center', cursor: v ? 'pointer' : 'default', fontWeight: 600, background: v ? hexA(RISK[r], 0.12 + 0.5 * (v / mx)) : undefined, color: v && (v / mx) > 0.6 ? '#fff' : 'var(--text)' }}>
                          {v ? v.toLocaleString() : '·'}
                        </td>
                      );
                    })}
                    <td style={{ textAlign: 'center', fontWeight: 700 }}>{rowTotal.toLocaleString()}</td>
                  </tr>
                );
              })}
              <tr style={{ background: '#f8fafc' }}>
                <td style={{ fontWeight: 700 }}>Total</td>
                {RISK_ORDER.map((r) => <td key={r} style={{ textAlign: 'center', fontWeight: 700, color: RISK[r] }}>{(riskCounts[r] || 0).toLocaleString()}</td>)}
                <td style={{ textAlign: 'center', fontWeight: 700 }}>{kpi.n.toLocaleString()}</td>
              </tr>
            </tbody>
          </table>
        </div>
        <div className="card">
          <div className="card-header">Component risk mix by type ({fSerials.length.toLocaleString()} serials)</div>
          {compMix.length ? (
            <ResponsiveContainer width="100%" height={300}>
              <BarChart data={compMix} layout="vertical" margin={{ top: 4, right: 12, bottom: 4, left: 8 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" />
                <XAxis type="number" tick={{ fontSize: 11 }} allowDecimals={false} />
                <YAxis type="category" dataKey="comp" width={96} tick={{ fontSize: 10 }} />
                <Tooltip />
                <Legend />
                {RISK_ORDER.map((r) => <Bar key={r} dataKey={r} name={r} stackId="c" fill={RISK[r]} />)}
              </BarChart>
            </ResponsiveContainer>
          ) : <div style={{ padding: 24, textAlign: 'center', color: 'var(--text-secondary)', fontSize: 13 }}>No component-serial rows in the current selection.</div>}
        </div>
      </div>

      {/* devices table */}
      <div className="card" style={{ marginBottom: 20, overflowX: 'auto' }}>
        <div className="card-header">Devices · per-unit RUL ({sortedDev.length.toLocaleString()})</div>
        <table className="data-table">
          <thead>
            <tr>
              <SortTh label="Device" col="device_id" sort={sortDev} setSort={setSortDev} />
              <SortTh label="Type" col="mars_device_category" sort={sortDev} setSort={setSortDev} />
              <SortTh label="Facility" col="facility_id" sort={sortDev} setSort={setSortDev} />
              <SortTh label="RUL (d)" col="rul_standard_days" sort={sortDev} setSort={setSortDev} align="right" />
              <th style={{ whiteSpace: 'nowrap' }}>P10–P90</th>
              <SortTh label="Risk" col="risk_band" sort={sortDev} setSort={setSortDev} />
              <SortTh label="Hazard" col="hazard_score" sort={sortDev} setSort={setSortDev} align="right" />
              <SortTh label="Fails/30d" col="roll_fail_30d" sort={sortDev} setSort={setSortDev} align="right" />
              <SortTh label="C-index" col="concordance_index" sort={sortDev} setSort={setSortDev} align="right" />
              <th>Gate</th><th></th>
            </tr>
          </thead>
          <tbody>
            {devRows.map((d, i) => (
              <tr key={i}>
                <td style={{ fontFamily: 'monospace', fontSize: 12, fontWeight: 600 }}>{d.device_id}{d.is_overdue && <span className="badge badge-critical" style={{ marginLeft: 6, fontSize: 9 }}>OD</span>}</td>
                <td><span style={{ display: 'inline-block', width: 8, height: 8, borderRadius: 2, background: TCOL[d.mars_device_category], marginRight: 6 }} />{TYPE_LABEL[d.mars_device_category] || d.mars_device_category}</td>
                <td>{facLabel(d.facility_id)}</td>
                <td style={{ textAlign: 'right', fontWeight: 600, color: rulColor(d.rul_standard_days) }}>{num(d.rul_standard_days)}</td>
                <td style={{ fontSize: 11, color: 'var(--text-secondary)' }}>{num(d.rul_p10_days)}–{num(d.rul_p90_days)}</td>
                <td><RiskBadge band={d.risk_band} /></td>
                <td style={{ textAlign: 'right' }}>{num(d.hazard_score, 3)}</td>
                <td style={{ textAlign: 'right' }}>{intf(d.roll_fail_30d)}</td>
                <td style={{ textAlign: 'right' }}>{num(d.concordance_index, 3)}</td>
                <td><span className={`badge ${d.data_quality_gate_passed ? 'badge-success' : 'badge-critical'}`}>{d.data_quality_gate_passed ? 'pass' : 'below'}</span></td>
                <td><button className="filter-btn" style={{ background: 'var(--primary)', borderColor: 'var(--primary)', color: '#fff', fontWeight: 600 }} onClick={() => setDrill(d)}>Analyse</button></td>
              </tr>
            ))}
            {devRows.length === 0 && <tr><td colSpan={11} style={{ textAlign: 'center', color: 'var(--text-secondary)', padding: 24 }}>No devices match the current filters.</td></tr>}
          </tbody>
        </table>
        <Pager page={devPage} pages={devPages} total={sortedDev.length} setPage={setDevPage} label="devices" />
      </div>

      {/* components table */}
      <div className="card" style={{ overflowX: 'auto' }}>
        <div className="card-header">Component serials · per-unit health ({sortedSer.length.toLocaleString()})</div>
        <table className="data-table">
          <thead>
            <tr>
              <SortTh label="Device" col="device_id" sort={sortSer} setSort={setSortSer} />
              <SortTh label="Serial" col="component_serial_nbr" sort={sortSer} setSort={setSortSer} />
              <SortTh label="Component" col="component_type" sort={sortSer} setSort={setSortSer} />
              <SortTh label="Type" col="mars_device_category" sort={sortSer} setSort={setSortSer} />
              <SortTh label="Age (d)" col="component_age_days" sort={sortSer} setSort={setSortSer} align="right" />
              <SortTh label="OOS fails" col="device_oos_failures_total" sort={sortSer} setSort={setSortSer} align="right" />
              <SortTh label="Risk score" col="risk_score" sort={sortSer} setSort={setSortSer} align="right" />
              <SortTh label="Tier" col="risk_tier" sort={sortSer} setSort={setSortSer} />
              <SortTh label="Exp. RUL (d)" col="expected_component_rul_days" sort={sortSer} setSort={setSortSer} align="right" />
            </tr>
          </thead>
          <tbody>
            {serRows.map((s, i) => (
              <tr key={i}>
                <td style={{ fontFamily: 'monospace', fontSize: 12 }}>{s.device_id}</td>
                <td style={{ fontFamily: 'monospace', fontSize: 11 }}>{s.component_serial_nbr}</td>
                <td>{compLabel(s.component_type)}</td>
                <td>{TYPE_LABEL[s.mars_device_category] || s.mars_device_category}</td>
                <td style={{ textAlign: 'right' }}>{num(s.component_age_days, 0)}</td>
                <td style={{ textAlign: 'right' }}>{intf(s.device_oos_failures_total)}</td>
                <td style={{ textAlign: 'right', fontWeight: 600 }}>{num(s.risk_score, 1)}</td>
                <td><RiskBadge band={s.risk_tier} /></td>
                <td style={{ textAlign: 'right' }}>{num(s.expected_component_rul_days)}</td>
              </tr>
            ))}
            {serRows.length === 0 && <tr><td colSpan={9} style={{ textAlign: 'center', color: 'var(--text-secondary)', padding: 24 }}>No component serials match the current filters.</td></tr>}
          </tbody>
        </table>
        <Pager page={serPage} pages={serPages} total={sortedSer.length} setPage={setSerPage} label="serials" />
      </div>

      {drill && <DeviceModal device={drill} serials={serials} meta={meta} onClose={() => setDrill(null)} />}

      {/* analyse-selection insights panel */}
      {showInsights && (
        <div onClick={() => setShowInsights(false)} style={{ position: 'fixed', inset: 0, background: 'rgba(15,23,42,0.55)', zIndex: 1000, display: 'flex', alignItems: 'flex-start', justifyContent: 'center', overflowY: 'auto', padding: '5vh 2vw' }}>
          <div onClick={(e) => e.stopPropagation()} className="card" style={{ width: 'min(820px, 96vw)', padding: 22, boxShadow: '0 20px 60px rgba(15,23,42,0.35)' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 14 }}>
              <div style={{ fontSize: 18, fontWeight: 700 }}>Analyse selection · {kpi.n.toLocaleString()} devices</div>
              <button className="filter-btn" onClick={() => setShowInsights(false)} style={{ fontSize: 16, padding: '4px 10px' }}>✕</button>
            </div>
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 12, marginBottom: 16 }}>
              <div className="card" style={{ padding: 14 }}><div className="card-header" style={{ marginBottom: 6 }}>Critical</div><div className="kpi-value" style={{ fontSize: 24, color: RISK.CRITICAL }}>{insights.critCount.toLocaleString()}</div></div>
              <div className="card" style={{ padding: 14 }}><div className="card-header" style={{ marginBottom: 6 }}>Critical + overdue</div><div className="kpi-value" style={{ fontSize: 24, color: 'var(--danger)' }}>{insights.overdueCrit.toLocaleString()}</div></div>
              <div className="card" style={{ padding: 14 }}><div className="card-header" style={{ marginBottom: 6 }}>Overdue (all)</div><div className="kpi-value" style={{ fontSize: 24 }}>{kpi.overdue.toLocaleString()}</div></div>
              <div className="card" style={{ padding: 14 }}><div className="card-header" style={{ marginBottom: 6 }}>Linked critical parts</div><div className="kpi-value" style={{ fontSize: 24 }}>{insights.linkedComps.toLocaleString()}</div></div>
            </div>
            <div className="card" style={{ padding: 14, marginBottom: 16 }}>
              <div className="card-header">Top facilities by critical devices</div>
              {insights.topFac.length ? (
                <table className="data-table"><thead><tr><th>Facility</th><th style={{ textAlign: 'right' }}>Critical devices</th><th></th></tr></thead>
                  <tbody>{insights.topFac.map(([f, c]) => (
                    <tr key={f}><td>Facility {f}</td><td style={{ textAlign: 'right', fontWeight: 600, color: RISK.CRITICAL }}>{c}</td>
                      <td><button className="filter-btn" onClick={() => { setFacility(f); setShowInsights(false); }}>Focus</button></td></tr>
                  ))}</tbody></table>
              ) : <div style={{ fontSize: 13, color: 'var(--text-secondary)', padding: 8 }}>No critical devices in the current selection.</div>}
            </div>
            <div className="card" style={{ padding: 14, borderLeft: '3px solid var(--secondary)' }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: 8 }}>
                <div>
                  <div className="card-header" style={{ marginBottom: 2 }}>ServiceNow · batch scheduled-maintenance</div>
                  <div style={{ fontSize: 11, color: 'var(--text-secondary)' }}>Stages a batch payload for the {batchPayload.candidate_count} critical + overdue candidates. No live post until the client SN API is provisioned.</div>
                </div>
                <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
                  <span className="badge badge-medium">ServiceNow integration — in progress</span>
                  <button className="filter-btn" onClick={() => setShowBatch((s) => !s)}>{showBatch ? 'Hide' : 'View'} payload</button>
                </div>
              </div>
              {showBatch && <pre style={{ marginTop: 12, background: '#0f172a', color: '#e2e8f0', padding: 12, borderRadius: 8, fontSize: 11, overflowX: 'auto', maxHeight: 300 }}>{JSON.stringify(batchPayload, null, 2)}</pre>}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
