// ============================================================================
// PS2RichAnalytics.jsx  ->  src/components/tabs/PS2RichAnalytics.jsx
// Phase-1f rich PS2 visuals: Correlation heatmap, Conditional-probability grid,
// Markov transitions, HMM regimes, Error-code panel, Device catalog + drilldown.
// The interactive cascade network lives in ./PS2CascadeNetwork (imported here).
// Self-contained useLiveData + inline pastel/navy theme. Drop into the PS2 tab:
//     import PS2RichAnalytics from './PS2RichAnalytics';
//     <PS2RichAnalytics city={city} />
// ============================================================================
import React, { useState, useEffect, useMemo } from 'react';
import {
  apiPS2Phi, apiPS2Markov, apiPS2Conditional, apiPS2ErrorCodes,
  apiPS2Devices, apiPS2DeviceCascades,
} from '../../data/api';
import {
  getPS2Phi, getPS2Markov, getPS2Conditional, getPS2ErrorCodes, getPS2Devices,
} from '../../data/mockData';
import PS2CascadeNetwork from './PS2CascadeNetwork';

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

// ---- 1. Correlation (Phi) heatmap -----------------------------------------
function CorrelationHeatmap({ city }) {
  const rows = useLiveData(getPS2Phi(), () => apiPS2Phi(city), [city]);
  const subs = useMemo(() => Array.from(new Set(rows.flatMap((r) => [r.sub_a, r.sub_b]))).sort(), [rows]);
  const lut = useMemo(() => { const m = {}; rows.forEach((r) => { m[`${r.sub_a}|${r.sub_b}`] = r.phi; }); return m; }, [rows]);
  const maxAbs = useMemo(() => Math.max(1, ...rows.filter((r) => r.sub_a !== r.sub_b).map((r) => Math.abs(r.phi || 0))), [rows]);
  const color = (v) => { if (v == null) return '#F4F7FB'; const a = Math.min(1, Math.abs(v) / maxAbs);
    return v >= 0 ? `rgba(78,138,87,${0.12 + 0.8 * a})` : `rgba(209,92,77,${0.12 + 0.8 * a})`; };
  return (
    <div style={card}>
      <h3 style={h3}>Subsystem Correlation (Phi)</h3>
      <p style={note}>Do two subsystems fail together in the same cascade? Green = co-fail, red = mutually exclusive.</p>
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
    </div>
  );
}

// ---- 2. Conditional probability P(B|A,T) ----------------------------------
function ConditionalProbGrid({ city }) {
  const rows = useLiveData(getPS2Conditional(), () => apiPS2Conditional(city), [city]);
  return (
    <div style={card}>
      <h3 style={h3}>Conditional Probability P(B | A, T)</h3>
      <p style={note}>If subsystem A fails, how likely does B fail within time-window T? Top co-failure couplings.</p>
      <table style={{ width:'100%', borderCollapse:'collapse' }}>
        <thead><tr><th style={th}>A (antecedent)</th><th style={th}>B (consequent)</th><th style={th}>Window</th><th style={{ ...th, width:'34%' }}>P(B|A)</th></tr></thead>
        <tbody>{rows.slice(0, 15).map((r, i) => (
          <tr key={i}><td style={{ ...td, fontFamily:'monospace' }}>{r.sub_a}</td><td style={{ ...td, fontFamily:'monospace' }}>{r.sub_b}</td>
            <td style={td}>{r.window_bucket}</td>
            <td style={td}><div style={{ display:'flex', alignItems:'center', gap:8 }}>
              <div style={{ background:'#F0F4F8', borderRadius:6, height:10, flex:1 }}>
                <div style={{ width:`${(r.p_b_given_a || 0) * 100}%`, height:10, borderRadius:6, background:P.blue }} /></div>
              <span style={{ minWidth:44, color:INK }}>{((r.p_b_given_a || 0) * 100).toFixed(1)}%</span></div></td>
          </tr>))}</tbody>
      </table>
    </div>
  );
}

// ---- 3. Markov transitions -------------------------------------------------
function MarkovView({ city }) {
  const rows = useLiveData(getPS2Markov(), () => apiPS2Markov(city), [city]);
  const max = Math.max(0.01, ...rows.map((r) => r.prob || 0));
  return (
    <div style={card}>
      <h3 style={h3}>Markov Cascade Transitions</h3>
      <p style={note}>P(next subsystem | current subsystem) — the dominant hop-by-hop cascade routes.</p>
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
    </div>
  );
}

// ---- 4. HMM regimes --------------------------------------------------------
const HMM_FALLBACK = [
  { regime:'Minor',    pct:21.0, dwell_days_min:2.1, dwell_days_max:4.2 },
  { regime:'Moderate', pct:76.4, dwell_days_min:3.8, dwell_days_max:6.3 },
  { regime:'Critical', pct:2.6,  dwell_days_min:1.5, dwell_days_max:2.4 },
];
function HMMRegimePanel({ city }) {
  const rows = useLiveData(HMM_FALLBACK, async () => {
    try { const b = (import.meta?.env?.VITE_API_BASE_URL) || ''; if (!b) return HMM_FALLBACK;
      const r = await fetch(`${b}/ps2/hmm?city=${city || 'CHI'}`); const j = await r.json();
      return (Array.isArray(j) && j.length) ? j : HMM_FALLBACK; } catch { return HMM_FALLBACK; }
  }, [city]);
  const col = { Minor:P.green, Moderate:P.amber, Critical:P.red };
  return (
    <div style={card}>
      <h3 style={h3}>Cascade Severity Regimes (HMM)</h3>
      <p style={note}>Three hidden severity states, their prevalence, and expected dwell (consecutive days).</p>
      <div style={{ display:'grid', gridTemplateColumns:'repeat(3,1fr)', gap:14 }}>
        {rows.map((r) => (
          <div key={r.regime} style={{ border:`1px solid ${LINE}`, borderTop:`4px solid ${col[r.regime] || P.blue}`, borderRadius:10, padding:'14px 16px' }}>
            <div style={{ fontSize:15, fontWeight:700, color:NAVY }}>{r.regime}</div>
            <div style={{ fontSize:24, fontWeight:700, color:NAVY, marginTop:4 }}>{Number(r.pct).toFixed(1)}%</div>
            <div style={{ fontSize:11.5, color:INK }}>of cascade days</div>
            <div style={{ fontSize:11.5, color:INK, marginTop:6 }}>dwell {r.dwell_days_min}-{r.dwell_days_max} days</div>
          </div>))}
      </div>
    </div>
  );
}

// ---- 5. Error-code panel ---------------------------------------------------
function ErrorCodePanel({ city }) {
  const data = useLiveData(getPS2ErrorCodes(), () => apiPS2ErrorCodes(city), [city]);
  const codes = data.codes || [];
  const max = Math.max(1, ...codes.map((c) => c.occurrences || 0));
  return (
    <div style={card}>
      <h3 style={h3}>Error-Code Intelligence</h3>
      <p style={note}>Actual device event codes (event_type_chain) driving cascades, with their dominant subsystem.</p>
      <div style={{ display:'grid', gridTemplateColumns:'1.4fr 1fr', gap:16 }}>
        <table style={{ width:'100%', borderCollapse:'collapse' }}>
          <thead><tr><th style={th}>Code</th><th style={th}>Subsystem</th><th style={{ ...th, width:'46%' }}>Occurrences</th></tr></thead>
          <tbody>{codes.slice(0, 15).map((c, i) => (
            <tr key={i}><td style={{ ...td, fontFamily:'monospace', fontWeight:700, color:NAVY }}>{c.error_code}</td>
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

// ---- 6. Device catalog + drilldown ----------------------------------------
function DeviceCatalog({ city }) {
  const devices = useLiveData(getPS2Devices(), () => apiPS2Devices(city), [city]);
  const [sel, setSel] = useState(null);
  const [cascades, setCascades] = useState([]);
  useEffect(() => { if (!sel) { setCascades([]); return; } let live = true;
    apiPS2DeviceCascades(city, sel).then((r) => { if (live) setCascades(r || []); });
    return () => { live = false; };
  }, [sel, city]);
  const maxD = Math.max(1, ...devices.map((d) => d.cascade_days || 0));
  const catcol = { TVM:P.green, GATE:P.amber, VALIDATOR:P.teal };
  return (
    <div style={card}>
      <h3 style={h3}>Device Catalog &amp; Drill-down</h3>
      <p style={note}>Every cascade-active device with type, serial, facility, dominant error code. Click a row for its recent cascade chains (with error codes).</p>
      <table style={{ width:'100%', borderCollapse:'collapse' }}>
        <thead><tr><th style={th}>Device</th><th style={th}>Type</th><th style={th}>Serial</th><th style={th}>Facility</th>
          <th style={{ ...th, width:'22%' }}>Cascade days</th><th style={th}>Dom. code</th></tr></thead>
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
          </tr>))}</tbody>
      </table>
      {sel && (
        <div style={{ marginTop:14, borderTop:`2px solid ${LINE}`, paddingTop:12 }}>
          <div style={{ fontSize:13, fontWeight:700, color:NAVY, marginBottom:6 }}>Recent cascades — {sel}</div>
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

export default function PS2RichAnalytics({ city = 'CHI' }) {
  return (
    <div>
      <CorrelationHeatmap city={city} />
      <ConditionalProbGrid city={city} />
      <MarkovView city={city} />
      <PS2CascadeNetwork city={city} />
      <HMMRegimePanel city={city} />
      <ErrorCodePanel city={city} />
      <DeviceCatalog city={city} />
    </div>
  );
}
