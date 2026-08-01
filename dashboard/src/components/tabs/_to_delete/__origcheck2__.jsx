// ============================================================================
// PS2RichAnalytics.jsx  ->  src/components/tabs/PS2RichAnalytics.jsx
// Phase-1f rich PS2 visuals: Correlation heatmap, Conditional-probability grid,
// Markov transitions, HMM regimes, Error-code panel, Device catalog + drilldown.
// The interactive cascade network lives in ./PS2CascadeNetwork (imported here).
// Self-contained useLiveData + inline pastel/navy theme. Drop into the PS2 tab:
//     import PS2RichAnalytics from './PS2RichAnalytics';
//     <PS2RichAnalytics city={city} />
//
// 22-Jul-2026 update: CorrelationHeatmap, ConditionalProbGrid, MarkovView and
// HMMRegimePanel now offer a Device / Serial grain toggle, pulling serial-grain
// rows from the new generic /ps2/serial/:metric route (via apiPS2SerialMetric)
// once the serial-grain PS2 notebook has run. Markov/HMM have a genuinely
// different shape at serial grain (per-serial self-transition-rate / criticality
// summary rather than a from->to transition table or 3-regime breakdown), so
// their serial view is a distinct compact table rather than a forced reuse of
// the device-grain chart. Also adds PS2ServiceNowButton (real dispatcher, see
// api.js) wired into DeviceCatalog's row-click selection.
// ============================================================================
import React, { useState, useEffect, useMemo } from 'react';
import {
  apiPS2Phi, apiPS2Markov, apiPS2Conditional, apiPS2ErrorCodes,
  apiPS2Devices, apiPS2DeviceCascades,
  apiPS2SerialMetric, apiPS2ServiceNowStatus, apiPS2ServiceNowCreateIncident,
  useSerialDeviceMap,
} from '../../data/api';
import {
  getPS2Phi, getPS2Markov, getPS2Conditional, getPS2ErrorCodes, getPS2Devices,
} from '../../data/mockData';
import PS2CascadeNetwork from './PS2CascadeNetwork';
import AnalyseButton from '../shared/AnalyseButton';
import { useFilters } from '../../context/FilterContext';
import { applyPS2Filters, isAnyPS2FilterActive } from '../../utils/ps2Filters';

const NAVY = '#1E3A5F', INK = '#5A6B7D', LINE = '#E1E9F1';
const P = { blue:'#9DC3E6', green:'#A9D18E', amber:'#F4CE7A', red:'#F1A9A0', purple:'#C9A9DA', teal:'#8FCFC9' };

function useLiveData(initial, fetcher, deps) {
  const [d, setD] = useState(initial);
  useEffect(() => { let live = true;
    Promise.resolve(fetcher()).then((x) => { if (live && x) setD(x); }).catch(() => {});
    return () => { live = false; };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);
  return d;
}
const card = { background:'#fff', border:`1px solid ${LINE}`, borderRadius:12, padding:'18px 20px', margin:'16px 0', boxShadow:'0 4px 14px rgba(30,58,95,.05)' };
const h3 = { margin:'0 0 2px', fontSize:16, color:NAVY, fontWeight:700 };
const note = { margin:'0 0 12px', fontSize:12.5, color:INK };
const th = { textAlign:'left', padding:'7px 9px', background:'#EEF3F9', color:NAVY, fontWeight:600, fontSize:12 };
const td = { padding:'6px 9px', borderTop:`1px solid #EEF2F7`, fontSize:12, color:'#33475B' };
const fmt = (n) => (n == null ? '--' : Number(n).toLocaleString());

// ---- shared grain-toggle pill (Device / Serial) -----------------------------
function GrainToggle({ grain, setGrain }) {
  const pill = (active) => ({
    padding: '3px 12px', borderRadius: 14, fontSize: 11.5, fontWeight: 600, cursor: 'pointer', border: 'none',
    background: active ? NAVY : '#EEF3F9', color: active ? '#fff' : NAVY,
  });
  return (
    <div style={{ display: 'inline-flex', gap: 6, marginBottom: 10 }}>
      <button style={pill(grain === 'device')} onClick={() => setGrain('device')}>Device grain</button>
      <button style={pill(grain === 'serial')} onClick={() => setGrain('serial')}>Serial grain</button>
    </div>
  );
}

function SerialPicker({ options, value, onChange }) {
  if (!options.length) return null;
  return (
    <select value={value || ''} onChange={(e) => onChange(e.target.value)}
      style={{ marginLeft: 10, fontSize: 12, padding: '3px 8px', borderRadius: 8, border: `1px solid ${LINE}`, color: NAVY }}>
      {options.map((s) => <option key={s} value={s}>{s}</option>)}
    </select>
  );
}

// ---- 1. Correlation (Phi) heatmap -----------------------------------------
function CorrelationHeatmap({ city, serialRoster, serialDeviceMap, onAnalyse }) {
  const [grain, setGrain] = useState('device');
  const serialOptions = useMemo(() => serialRoster.map((r) => r.serial_id).filter(Boolean), [serialRoster]);
  const [serialId, setSerialId] = useState(null);
  useEffect(() => { if (!serialId && serialOptions.length) setSerialId(serialOptions[0]); }, [serialOptions, serialId]);

  const deviceRows = useLiveData(getPS2Phi(), () => apiPS2Phi(city), [city]);
  const [serialRows, setSerialRows] = useState([]);
  useEffect(() => {
    if (grain !== 'serial' || !serialId) return;
    let alive = true;
    apiPS2SerialMetric(city, 'phi', { serial_id: serialId }).then((r) => { if (alive) setSerialRows(r); });
    return () => { alive = false; };
  }, [city, grain, serialId]);

  const filters = useFilters();
  const allRows = grain === 'serial' ? serialRows : deviceRows;
  const rows = useMemo(() => applyPS2Filters(allRows, filters), [allRows, filters]);
  const subs = useMemo(() => Array.from(new Set(rows.flatMap((r) => [r.sub_a, r.sub_b]))).sort(), [rows]);
  const lut = useMemo(() => { const m = {}; rows.forEach((r) => { m[`${r.sub_a}|${r.sub_b}`] = r.phi; }); return m; }, [rows]);
  const maxAbs = useMemo(() => Math.max(1, ...rows.filter((r) => r.sub_a !== r.sub_b).map((r) => Math.abs(r.phi || 0))), [rows]);
  const color = (v) => { if (v == null) return '#F4F7FB'; const a = Math.min(1, Math.abs(v) / maxAbs);
    return v >= 0 ? `rgba(78,138,87,${0.12 + 0.8 * a})` : `rgba(209,92,77,${0.12 + 0.8 * a})`; };
  return (
    <div style={card}>
      <h3 style={h3}>Subsystem Correlation (Phi)</h3>
      <p style={note}>Do two subsystems fail together in the same cascade? Green = co-fail, red = mutually exclusive.</p>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
        <GrainToggle grain={grain} setGrain={setGrain} />
        {grain === 'serial' && <SerialPicker options={serialOptions} value={serialId} onChange={setSerialId} />}
        {grain === 'serial' && serialId && onAnalyse && (
          <AnalyseButton
            onClick={() => onAnalyse(serialDeviceMap[serialId])}
            disabled={!serialDeviceMap[serialId]}
            title={serialDeviceMap[serialId] ? `Analyse device ${serialDeviceMap[serialId]}` : 'No device mapping yet for this serial'}
            compact
          />
        )}
      </div>
      {grain === 'serial' && serialOptions.length === 0 && (
        <p style={{ ...note, color: '#B08A2E' }}>No serial-grain phi data yet — run the PS2 serial-grain notebook.</p>
      )}
      {(grain === 'device' || (grain === 'serial' && subs.length > 0)) && (
        <div style={{ overflowX:'auto' }}>
          <table style={{ borderCollapse:'collapse', fontSize:10.5 }}>
            <thead><tr><th style={{ ...th, background:'#fff' }}></th>
              {subs.map((s) => <th key={s} style={{ ...th, writingMode:'vertical-rl', transform:'rotate(180deg)', textAlign:'center' }}>{s}</th>)}</tr></thead>
            <tbody>{subs.map((a) => (
              <tr key={a}><td style={{ ...td, fontWeight:700, color:NAVY, whiteSpace:'nowrap' }}>{a}</td>
                {subs.map((b) => { const v = lut[`${a}|${b}`]; return (
                  <td key={b} title={`${a} / ${b}: ${v ?? 0}`} style={{ background:color(v), textAlign:'center', padding:'6px 5px', color:'#2A2A2A', border:'1px solid #fff', minWidth:34 }}>
                    {v == null ? '' : (Math.abs(v) >= 10 ? Math.round(v) : v.toFixed(1))}
                  </td>); })}
              </tr>))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

// ---- 2. Conditional probability P(B|A,T) ----------------------------------
function ConditionalProbGrid({ city, serialRoster, serialDeviceMap, onAnalyse }) {
  const [grain, setGrain] = useState('device');
  const serialOptions = useMemo(() => serialRoster.map((r) => r.serial_id).filter(Boolean), [serialRoster]);
  const [serialId, setSerialId] = useState(null);
  useEffect(() => { if (!serialId && serialOptions.length) setSerialId(serialOptions[0]); }, [serialOptions, serialId]);

  const deviceRows = useLiveData(getPS2Conditional(), () => apiPS2Conditional(city), [city]);
  const [serialRows, setSerialRows] = useState([]);
  useEffect(() => {
    if (grain !== 'serial' || !serialId) return;
    let alive = true;
    apiPS2SerialMetric(city, 'condprob', { serial_id: serialId }).then((r) => { if (alive) setSerialRows(r); });
    return () => { alive = false; };
  }, [city, grain, serialId]);

  const filters = useFilters();
  const allRows = grain === 'serial' ? serialRows : deviceRows;
  const rows = useMemo(() => applyPS2Filters(allRows, filters), [allRows, filters]);
  return (
    <div style={card}>
      <h3 style={h3}>Conditional Probability P(B | A, T)</h3>
      <p style={note}>If subsystem A fails, how likely does B fail within time-window T? Top co-failure couplings.</p>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
        <GrainToggle grain={grain} setGrain={setGrain} />
        {grain === 'serial' && <SerialPicker options={serialOptions} value={serialId} onChange={setSerialId} />}
        {grain === 'serial' && serialId && onAnalyse && (
          <AnalyseButton
            onClick={() => onAnalyse(serialDeviceMap[serialId])}
            disabled={!serialDeviceMap[serialId]}
            title={serialDeviceMap[serialId] ? `Analyse device ${serialDeviceMap[serialId]}` : 'No device mapping yet for this serial'}
            compact
          />
        )}
      </div>
      {grain === 'serial' && rows.length === 0 ? (
        <p style={{ ...note, color: '#B08A2E' }}>No serial-grain conditional-probability data yet for this serial.</p>
      ) : (
        <table style={{ width:'100%', borderCollapse:'collapse' }}>
          <thead><tr><th style={th}>A (antecedent)</th><th style={th}>B (consequent)</th><th style={th}>Window</th><th style={{ ...th, width:'34%' }}>P(B|A)</th></tr></thead>
          <tbody>{rows.slice(0, 15).map((r, i) => (
            <tr key={i}><td style={{ ...td, fontFamily:'monospace' }}>{r.sub_a}</td><td style={{ ...td, fontFamily:'monospace' }}>{r.sub_b}</td>
              <td style={td}>{r.window_bucket || r.window}</td>
              <td style={td}><div style={{ display:'flex', alignItems:'center', gap:8 }}>
                <div style={{ background:'#F0F4F8', borderRadius:6, height:10, flex:1 }}>
                  <div style={{ width:`${(r.p_b_given_a || 0) * 100}%`, height:10, borderRadius:6, background:P.blue }} /></div>
                <span style={{ minWidth:44, color:INK }}>{((r.p_b_given_a || 0) * 100).toFixed(1)}%</span></div></td>
            </tr>))}</tbody>
        </table>
      )}
    </div>
  );
}

// ---- 3. Markov transitions -------------------------------------------------
function MarkovView({ city, serialRoster, serialDeviceMap, onAnalyse }) {
  const [grain, setGrain] = useState('device');
  const filters = useFilters();
  const rowsRaw = useLiveData(getPS2Markov(), () => apiPS2Markov(city), [city]);
  const rows = useMemo(() => applyPS2Filters(rowsRaw, filters), [rowsRaw, filters]);
  const filteredSerialRoster = useMemo(() => applyPS2Filters(serialRoster, filters), [serialRoster, filters]);
  const max = Math.max(0.01, ...rows.map((r) => r.prob || 0));

  return (
    <div style={card}>
      <h3 style={h3}>Markov Cascade Transitions</h3>
      <p style={note}>
        {grain === 'device'
          ? 'P(next subsystem | current subsystem) — the dominant hop-by-hop cascade routes.'
          : 'Per-serial self-transition rate — how often a cascade re-enters the same subsystem for that serial (a different shape than the device-grain transition table, so shown as its own summary).'}
      </p>
      <GrainToggle grain={grain} setGrain={setGrain} />
      {grain === 'device' ? (
        <table style={{ width:'100%', borderCollapse:'collapse' }}>
          <thead><tr><th style={th}>From</th><th style={th}>To</th><th style={{ ...th, width:'50%' }}>Transition probability</th></tr></thead>
          <tbody>{rows.slice(0, 15).map((r, i) => (
            <tr key={i}><td style={{ ...td, fontFamily:'monospace' }}>{r.from_sub}</td><td style={{ ...td, fontFamily:'monospace' }}>{r.to_sub}</td>
              <td style={td}><div style={{ display:'flex', alignItems:'center', gap:8 }}>
                <div style={{ background:'#F0F4F8', borderRadius:6, height:10, flex:1 }}>
                  <div style={{ width:`${((r.prob || 0) / max) * 100}%`, height:10, borderRadius:6, background:P.amber }} /></div>
                <span style={{ minWidth:44, color:INK }}>{((r.prob || 0) * 100).toFixed(1)}%</span></div></td>
            </tr>))}</tbody>
        </table>
      ) : filteredSerialRoster.length === 0 ? (
        <p style={{ ...note, color: '#B08A2E' }}>{serialRoster.length === 0 ? 'No serial-grain Markov data yet — run the PS2 serial-grain notebook.' : 'No serials match the current filters.'}</p>
      ) : (
        <table style={{ width:'100%', borderCollapse:'collapse' }}>
          <thead><tr><th style={th}>Serial</th><th style={th}>Chains observed</th><th style={{ ...th, width:'50%' }}>Self-transition rate</th><th style={th}></th></tr></thead>
          <tbody>{filteredSerialRoster.map((r, i) => (
            <tr key={i}><td style={{ ...td, fontFamily:'monospace', fontWeight:700, color:NAVY }}>{r.serial_id}</td>
              <td style={td}>{fmt(r.n_chains)}</td>
              <td style={td}><div style={{ display:'flex', alignItems:'center', gap:8 }}>
                <div style={{ background:'#F0F4F8', borderRadius:6, height:10, flex:1 }}>
                  <div style={{ width:`${(r.self_transition_rate || 0) * 100}%`, height:10, borderRadius:6, background:P.amber }} /></div>
                <span style={{ minWidth:44, color:INK }}>{((r.self_transition_rate || 0) * 100).toFixed(1)}%</span></div></td>
              <td style={td}>{onAnalyse && (
                <AnalyseButton
                  onClick={() => onAnalyse(serialDeviceMap[r.serial_id])}
                  disabled={!serialDeviceMap[r.serial_id]}
                  title={serialDeviceMap[r.serial_id] ? `Analyse device ${serialDeviceMap[r.serial_id]}` : 'No device mapping yet for this serial'}
                  compact
                />
              )}</td>
            </tr>))}</tbody>
        </table>
      )}
    </div>
  );
}

// ---- 4. HMM regimes --------------------------------------------------------
const HMM_FALLBACK = [
  { regime:'Minor',    pct:21.0, dwell_days_min:2.1, dwell_days_max:4.2 },
  { regime:'Moderate', pct:76.4, dwell_days_min:3.8, dwell_days_max:6.3 },
  { regime:'Critical', pct:2.6,  dwell_days_min:1.5, dwell_days_max:2.4 },
];
function HMMRegimePanel({ city, serialDeviceMap, onAnalyse }) {
  const [grain, setGrain] = useState('device');
  const rows = useLiveData(HMM_FALLBACK, async () => {
    try { const b = (import.meta?.env?.VITE_API_BASE_URL) || ''; if (!b) return HMM_FALLBACK;
      const r = await fetch(`${b}/ps2/hmm?city=${city || 'CHI'}`); const j = await r.json();
      return (Array.isArray(j) && j.length) ? j : HMM_FALLBACK; } catch { return HMM_FALLBACK; }
  }, [city]);
  const col = { Minor:P.green, Moderate:P.amber, Critical:P.red };

  const [serialRows, setSerialRows] = useState([]);
  useEffect(() => {
    if (grain !== 'serial') return;
    let alive = true;
    apiPS2SerialMetric(city, 'hmm').then((r) => { if (alive) setSerialRows(r); });
    return () => { alive = false; };
  }, [city, grain]);

  return (
    <div style={card}>
      <h3 style={h3}>Cascade Severity Regimes (HMM)</h3>
      <p style={note}>
        {grain === 'device'
          ? 'Three hidden severity states, their prevalence, and expected dwell (consecutive days).'
          : 'Per-serial criticality summary from the 3-state HMM fit at serial grain — a different shape than the device-grain regime breakdown, so shown as its own table.'}
      </p>
      <GrainToggle grain={grain} setGrain={setGrain} />
      {grain === 'device' ? (
        <div style={{ display:'grid', gridTemplateColumns:'repeat(3,1fr)', gap:14 }}>
          {rows.map((r) => (
            <div key={r.regime} style={{ border:`1px solid ${LINE}`, borderTop:`4px solid ${col[r.regime] || P.blue}`, borderRadius:10, padding:'14px 16px' }}>
              <div style={{ fontSize:15, fontWeight:700, color:NAVY }}>{r.regime}</div>
              <div style={{ fontSize:24, fontWeight:700, color:NAVY, marginTop:4 }}>{Number(r.pct).toFixed(1)}%</div>
              <div style={{ fontSize:11.5, color:INK }}>of cascade days</div>
              <div style={{ fontSize:11.5, color:INK, marginTop:6 }}>dwell {r.dwell_days_min}-{r.dwell_days_max} days</div>
            </div>))}
        </div>
      ) : serialRows.length === 0 ? (
        <p style={{ ...note, color: '#B08A2E' }}>No serial-grain HMM data yet — run the PS2 serial-grain notebook.</p>
      ) : (
        <table style={{ width:'100%', borderCollapse:'collapse' }}>
          <thead><tr><th style={th}>Serial</th><th style={th}>Observations</th><th style={th}>% time critical</th>
            <th style={th}>Mean chain length</th><th style={th}>Converged</th><th style={th}></th></tr></thead>
          <tbody>{serialRows.map((r, i) => (
            <tr key={i}><td style={{ ...td, fontFamily:'monospace', fontWeight:700, color:NAVY }}>{r.serial_id}</td>
              <td style={td}>{fmt(r.n_obs)}</td>
              <td style={td}>{((r.pct_time_critical || 0) * 100).toFixed(1)}%</td>
              <td style={td}>{Number(r.mean_chain_length || 0).toFixed(2)}</td>
              <td style={td}>{r.converged ? <span style={{ color:'#4E8A57' }}>yes</span> : <span style={{ color:'#B08A2E' }}>no</span>}</td>
              <td style={td}>{onAnalyse && (
                <AnalyseButton
                  onClick={() => onAnalyse(serialDeviceMap[r.serial_id])}
                  disabled={!serialDeviceMap[r.serial_id]}
                  title={serialDeviceMap[r.serial_id] ? `Analyse device ${serialDeviceMap[r.serial_id]}` : 'No device mapping yet for this serial'}
                  compact
                />
              )}</td>
            </tr>))}</tbody>
        </table>
      )}
    </div>
  );
}

// ---- 5. Error-code panel ---------------------------------------------------
function ErrorCodePanel({ city }) {
  const filters = useFilters();
  const data = useLiveData(getPS2ErrorCodes(), () => apiPS2ErrorCodes(city), [city]);
  const codesRaw = data.codes || [];
  const codes = useMemo(() => applyPS2Filters(codesRaw, filters), [codesRaw, filters]);
  const max = Math.max(1, ...codes.map((c) => c.occurrences || 0));
  // Drill-down: clicking a code row isolates the shared Failure-Type filter
  // on exactly that code, so every other filter-aware PS2 panel narrows too.
  const focusErrorCode = (code) => {
    if (!filters.failureTypeOptions.length) return;
    filters.failureTypeOptions.forEach((ft) => {
      const shouldBeSelected = ft === String(code);
      const isSelected = filters.selectedFailureTypes.includes(ft);
      if (shouldBeSelected !== isSelected) filters.toggleFailureType(ft);
    });
  };
  return (
    <div style={card}>
      <h3 style={h3}>Error-Code Intelligence</h3>
      <p style={note}>Actual device event codes (event_type_chain) driving cascades, with their dominant subsystem. Click a row to drill down (isolates the Failure Type filter on that code).</p>
      <div style={{ display:'grid', gridTemplateColumns:'1.4fr 1fr', gap:16 }}>
        <table style={{ width:'100%', borderCollapse:'collapse' }}>
          <thead><tr><th style={th}>Code</th><th style={th}>Subsystem</th><th style={{ ...th, width:'46%' }}>Occurrences</th></tr></thead>
          <tbody>{codes.slice(0, 15).map((c, i) => (
            <tr key={i} onClick={() => focusErrorCode(c.error_code)} style={{ cursor: 'pointer' }} title="Click to focus the Failure Type filter on this code">
              <td style={{ ...td, fontFamily:'monospace', fontWeight:700, color:NAVY }}>{c.error_code}</td>
              <td style={td}>{c.top_subsystem}</td>
              <td style={td}>{c.occurrences == null ? <span style={{ color:'#B08A2E' }}>run-pending</span> :
                <div style={{ display:'flex', alignItems:'center', gap:8 }}>
                  <div style={{ background:'#F0F4F8', borderRadius:6, height:10, flex:1 }}>
                    <div style={{ width:`${((c.occurrences || 0) / max) * 100}%`, height:10, borderRadius:6, background:P.red }} /></div>
                  <span style={{ minWidth:52, color:INK }}>{fmt(c.occurrences)}</span></div>}</td>
            </tr>))}</tbody>
        </table>
        <div>
          <div style={{ fontSize:11, textTransform:'uppercase', letterSpacing:1, color:INK, marginBottom:6 }}>Code -&gt; code transitions</div>
          {(data.transitions || []).slice(0, 10).map((t, i) => (
            <div key={i} style={{ padding:'6px 10px', border:`1px solid ${LINE}`, borderRadius:8, marginBottom:6, fontFamily:'monospace', fontSize:12, color:NAVY }}>
              {t.from_code} &rarr; {t.to_code} <span style={{ color:INK, float:'right' }}>{fmt(t.occurrences)}</span></div>))}
        </div>
      </div>
    </div>
  );
}

// ---- 6. PS2 ServiceNow push button (real dispatcher) ------------------------
// Mirrors PS3ServiceNowButton (PS3DeepDiveAnalytics.jsx) but styled inline
// (pastel/navy) to match this file's convention rather than the class-based
// badge system PS3 uses. Hits the real /ps2/servicenow/* routes -- proven
// end-to-end 22-Jul-2026 against the live ctsdev2cubic incwowot API (currently
// returns a real 401 since the client hasn't provided live credentials yet;
// once cubic-mars-secret-servicenow-dev is updated, this same button starts
// returning real INC# numbers with zero code changes).
export function PS2ServiceNowButton({ city = 'CHI', deviceId, serialId, window: win }) {
  const correlationId = `${deviceId}|PS2|${win}`;
  const [state, setState] = useState({ status: 'loading' });

  useEffect(() => {
    let alive = true;
    apiPS2ServiceNowStatus(city, correlationId).then((r) => { if (alive) setState(r); });
    return () => { alive = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [city, correlationId]);

  const handleClick = async () => {
    setState({ status: 'submitting' });
    const r = await apiPS2ServiceNowCreateIncident(city, { deviceId, serialId, window: win });
    setState(r);
  };

  const pill = (bg, txt, extra) => (
    <span style={{ padding:'3px 12px', borderRadius:14, fontSize:11.5, fontWeight:600, background:bg, color:txt, ...extra }} />
  );

  if (state.status === 'loading') return <span style={{ fontSize: 11.5, color: INK }}>checking ServiceNow status…</span>;
  if (state.status === 'created' || state.status === 'already_filed') {
    return <span style={{ padding:'3px 12px', borderRadius:14, fontSize:11.5, fontWeight:700, background:P.green, color:NAVY }}>{state.inc_number || 'filed'}</span>;
  }
  if (state.status === 'submitting') return <span style={{ fontSize: 11.5, color: INK }}>filing…</span>;
  if (state.status === 'sample') {
    const payloadJson = JSON.stringify(state.submitted_payload || {}, null, 2);
    return (
      <span style={{ display:'inline-flex', alignItems:'center', gap:7 }}>
        <span
          title={`${state.note || 'ServiceNow Integration awaited -- sample payload'}\n\nSample payload:\n${payloadJson}`}
          style={{ padding:'3px 12px', borderRadius:14, fontSize:11.5, fontWeight:700, background:P.amber, color:NAVY }}
        >
          {state.inc_number} <span style={{ fontWeight:800, letterSpacing:0.3 }}>(SAMPLE)</span>
        </span>
        <span style={{ fontSize:10.5, color:INK }}>ServiceNow Integration awaited</span>
      </span>
    );
  }
  if (state.status === 'error') {
    return (
      <button onClick={handleClick} title={`ServiceNow Integration awaited -- ${state.error || 'live credentials not yet provisioned'}`}
        style={{ border:'none', cursor:'pointer', padding:'4px 14px', borderRadius:14, fontSize:11.5, fontWeight:700, background:P.amber, color:NAVY }}>
        ServiceNow Integration awaited — retry
      </button>
    );
  }
  return (
    <button onClick={handleClick} title="ServiceNow Integration awaited -- the dispatcher is fully wired end-to-end, but live ctsdev2cubic credentials have not been provisioned yet."
      style={{ border:'none', cursor:'pointer', padding:'4px 14px', borderRadius:14, fontSize:11.5, fontWeight:700, background:P.amber, color:NAVY }}>
      ServiceNow Integration awaited
    </button>
  );
}

// ---- 7. Device catalog + drilldown (+ ServiceNow) --------------------------
function DeviceCatalog({ city, onAnalyse }) {
  const filters = useFilters();
  const devicesRaw = useLiveData(getPS2Devices(), () => apiPS2Devices(city), [city]);
  const devices = useMemo(() => applyPS2Filters(devicesRaw, filters), [devicesRaw, filters]);
  const [sel, setSel] = useState(null);
  const [cascades, setCascades] = useState([]);
  const today = useMemo(() => new Date().toISOString().slice(0, 10), []);
  useEffect(() => { if (!sel) { setCascades([]); return; } let live = true;
    apiPS2DeviceCascades(city, sel).then((r) => { if (live) setCascades(r || []); });
    return () => { live = false; };
  }, [sel, city]);
  const maxD = Math.max(1, ...devices.map((d) => d.cascade_days || 0));
  const catcol = { TVM:P.green, GATE:P.amber, VALIDATOR:P.teal };
  const selDevice = devices.find((d) => d.device_id === sel);
  return (
    <div style={card}>
      <h3 style={h3}>Device Catalog &amp; Drill-down</h3>
      <p style={note}>Every cascade-active device with type, serial, facility, dominant error code. Click a row for its recent cascade chains (with error codes) and to push a ServiceNow incident.</p>
      {isAnyPS2FilterActive(filters) && (
        <p style={{ fontSize: 11, color: '#B08A2E', fontWeight: 600, margin: '0 0 8px' }}>Showing {devices.length} of {devicesRaw.length} devices — narrowed by the active filters.</p>
      )}
      <table style={{ width:'100%', borderCollapse:'collapse' }}>
        <thead><tr><th style={th}>Device</th><th style={th}>Type</th><th style={th}>Serial</th><th style={th}>Facility</th>
          <th style={{ ...th, width:'22%' }}>Cascade days</th><th style={th}>Dom. code</th><th style={th}></th></tr></thead>
        <tbody>{devices.slice(0, 20).map((d) => (
          <tr key={d.device_id} onClick={() => setSel(sel === d.device_id ? null : d.device_id)}
              style={{ cursor:'pointer', background: sel === d.device_id ? '#EEF6FF' : 'transparent' }}>
            <td style={{ ...td, fontFamily:'monospace', fontWeight:700, color:NAVY }}>{d.device_id}</td>
            <td style={td}><span style={{ padding:'2px 8px', borderRadius:12, fontSize:11, fontWeight:600, color:NAVY, background:catcol[d.category] || P.blue }}>{d.category}</span></td>
            <td style={{ ...td, fontFamily:'monospace' }}>{d.serial || '--'}</td>
            <td style={td}>{d.facility || '--'}</td>
            <td style={td}><div style={{ display:'flex', alignItems:'center', gap:8 }}>
              <div style={{ background:'#F0F4F8', borderRadius:6, height:10, flex:1 }}>
                <div style={{ width:`${((d.cascade_days || 0) / maxD) * 100}%`, height:10, borderRadius:6, background:P.blue }} /></div>
              <span style={{ minWidth:34, color:INK }}>{fmt(d.cascade_days)}</span></div></td>
            <td style={{ ...td, fontFamily:'monospace' }}>{d.dom_error_code || '--'}</td>
            <td style={td} onClick={(e) => e.stopPropagation()}>
              {onAnalyse && <AnalyseButton onClick={() => onAnalyse(d.device_id)} compact />}
            </td>
          </tr>))}</tbody>
      </table>
      {sel && (
        <div style={{ marginTop:14, borderTop:`2px solid ${LINE}`, paddingTop:12 }}>
          <div style={{ display:'flex', alignItems:'center', justifyContent:'space-between', marginBottom:10 }}>
            <div style={{ fontSize:13, fontWeight:700, color:NAVY }}>Recent cascades — {sel}</div>
            <div style={{ display:'flex', alignItems:'center', gap:10 }}>
              <PS2ServiceNowButton city={city} deviceId={sel} serialId={selDevice?.serial} window={today} />
              <button onClick={() => setSel(null)} style={{ fontSize:11.5, background:'none', border:'none', color:INK, cursor:'pointer' }}>clear selection</button>
            </div>
          </div>
          {cascades.length === 0 ? <p style={note}>No live cascade rows yet (deploy sql/09 + notebook backfill sql/10).</p> : (
            <table style={{ width:'100%', borderCollapse:'collapse' }}>
              <thead><tr><th style={th}>Day</th><th style={th}>Subsystem chain</th><th style={th}>Error-code chain</th><th style={th}>Severity</th><th style={th}>Len</th><th style={th}>Span (min)</th></tr></thead>
              <tbody>{cascades.slice(0, 25).map((c, i) => (
                <tr key={i}><td style={td}>{String(c.transit_day).slice(0, 10)}</td>
                  <td style={{ ...td, fontFamily:'monospace' }}>{c.subsystem_chain}</td>
                  <td style={{ ...td, fontFamily:'monospace' }}>{c.event_code_chain}</td>
                  <td style={td}>{c.severity_chain}</td><td style={td}>{c.chain_length}</td><td style={td}>{c.chain_span_min}</td></tr>))}</tbody>
            </table>)}
        </div>)}
    </div>
  );
}

export default function PS2RichAnalytics({ city = 'CHI', onAnalyse }) {
  // Fetched once here and passed down: the compact serial roster (one row per
  // serial_id, from ps2_markov_self_transition_serial) doubles as both the
  // MarkovView serial-grain table AND the dropdown source for the phi/condprob
  // per-serial selector, avoiding 3 separate roster fetches.
  const serialRoster = useLiveData([], () => apiPS2SerialMetric(city, 'markov'), [city]);
  // Serial -> device lookup (ps2_device_cmdb_map via the 'cmdb' allow-list
  // entry) so every serial-grain row's Analyse button can open the same
  // cross-PS Device360Modal a device-grain row uses. Empty until that
  // allow-list entry is deployed server-side -- see api.js's
  // useSerialDeviceMap for the graceful-empty behavior in that case. Always
  // called (never conditionally) to respect the Rules of Hooks -- it's cheap
  // (one fetch, cached rows) even on the rare render where onAnalyse is unset.
  const serialDeviceMap = useSerialDeviceMap(city);

  return (
    <div>
      <CorrelationHeatmap city={city} serialRoster={serialRoster} serialDeviceMap={serialDeviceMap} onAnalyse={onAnalyse} />
      <ConditionalProbGrid city={city} serialRoster={serialRoster} serialDeviceMap={serialDeviceMap} onAnalyse={onAnalyse} />
      <MarkovView city={city} serialRoster={serialRoster} serialDeviceMap={serialDeviceMap} onAnalyse={onAnalyse} />
      <PS2CascadeNetwork city={city} />
      <HMMRegimePanel city={city} serialDeviceMap={serialDeviceMap} onAnalyse={onAnalyse} />
      <ErrorCodePanel city={city} />
      <DeviceCatalog city={city} onAnalyse={onAnalyse} />
    </div>
  );
}
