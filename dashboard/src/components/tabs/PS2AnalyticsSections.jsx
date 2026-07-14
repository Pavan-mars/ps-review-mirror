// ============================================================================
// PS2AnalyticsSections.jsx  ->  src/components/tabs/PS2AnalyticsSections.jsx
// Phase-1e richer PS2 analytics: Cascade Paths, Ignition->Termination, Business
// Impact. Drop into the Cascading-Failure tab with ONE import + ONE line:
//     import PS2AnalyticsSections from './PS2AnalyticsSections';
//     <PS2AnalyticsSections city={city} />
// Self-contained: local useLiveData (instant mock render, then live swap) and
// the api fetchers with mock fallback, so it renders whether or not the live
// API is wired. CUBIC MARS white-bg + pastel/navy identity (inline styles).
// ============================================================================
import React, { useState, useEffect } from 'react';
import { apiPS2Paths, apiPS2Ignition, apiPS2Impact } from '../../data/api';
import {
  getPS2CascadePaths,
  getPS2IgnitionTermination,
  getPS2BusinessImpact,
} from '../../data/mockData';

const NAVY = '#1E3A5F', INK = '#5A6B7D', LINE = '#E1E9F1';
const PASTEL = {
  blue: '#9DC3E6', green: '#A9D18E', amber: '#F4CE7A',
  red: '#F1A9A0', purple: '#C9A9DA', teal: '#8FCFC9',
};
const ROLE_COLOR = { Ignitor: PASTEL.red, Terminator: PASTEL.blue, Relay: PASTEL.amber };

function useLiveData(initial, fetcher, deps) {
  const [data, setData] = useState(initial);
  useEffect(() => {
    let live = true;
    Promise.resolve(fetcher()).then((d) => { if (live && d) setData(d); }).catch(() => {});
    return () => { live = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);
  return data;
}

const card = {
  background: '#fff', border: `1px solid ${LINE}`, borderRadius: 12,
  padding: '18px 20px', margin: '16px 0', boxShadow: '0 4px 14px rgba(30,58,95,.05)',
};
const h3 = { margin: '0 0 2px', fontSize: 16, color: NAVY, fontWeight: 700 };
const note = { margin: '0 0 12px', fontSize: 12.5, color: INK };
const th = { textAlign: 'left', padding: '8px 10px', background: '#EEF3F9', color: NAVY, fontWeight: 600, fontSize: 12.5 };
const td = { padding: '7px 10px', borderTop: `1px solid #EEF2F7`, fontSize: 12.5, color: '#33475B' };
const fmt = (n) => (n == null ? '--' : Number(n).toLocaleString());

function Bar({ value, max, color }) {
  const pct = max > 0 ? Math.max(3, (value / max) * 100) : 0;
  return (
    <div style={{ background: '#F0F4F8', borderRadius: 6, height: 10, width: '100%' }}>
      <div style={{ width: `${pct}%`, height: 10, borderRadius: 6, background: color }} />
    </div>
  );
}

export default function PS2AnalyticsSections({ city = 'CHI' }) {
  const paths = useLiveData(getPS2CascadePaths(), () => apiPS2Paths(city), [city]);
  const ign = useLiveData(getPS2IgnitionTermination(), () => apiPS2Ignition(city), [city]);
  const impact = useLiveData(getPS2BusinessImpact(), () => apiPS2Impact(city), [city]);

  const maxOcc = Math.max(...paths.map((p) => p.occurrences || 0), 1);
  const maxDays = Math.max(...impact.map((d) => d.cascade_days || 0), 1);
  const ignitors = ign.filter((s) => s.net_role === 'Ignitor');
  const terminators = ign.filter((s) => s.net_role === 'Terminator');
  const relays = ign.filter((s) => s.net_role === 'Relay');

  return (
    <div>
      {/* ---- Cascade Paths (Markov) ---- */}
      <div style={card}>
        <h3 style={h3}>Top Cascade Paths</h3>
        <p style={note}>
          Most frequent subsystem-to-subsystem cascade sequences (Markov). Occurrences and share are
          grounded in the real Gold run; longer paths refresh from the PS2 notebook export.
        </p>
        <table style={{ width: '100%', borderCollapse: 'collapse' }}>
          <thead><tr><th style={th}>#</th><th style={th}>Cascade path</th>
            <th style={th}>Occurrences</th><th style={{ ...th, width: '32%' }}>Share of chains</th></tr></thead>
          <tbody>
            {paths.map((p) => (
              <tr key={p.path_rank}>
                <td style={{ ...td, fontWeight: 700, color: NAVY }}>{p.path_rank}</td>
                <td style={{ ...td, fontFamily: 'monospace' }}>{p.cascade_path}</td>
                <td style={td}>{fmt(p.occurrences)}</td>
                <td style={td}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                    <Bar value={p.occurrences} max={maxOcc} color={PASTEL.blue} />
                    <span style={{ minWidth: 42, color: INK }}>
                      {p.pct_of_chains == null ? '--' : `${p.pct_of_chains}%`}
                    </span>
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* ---- Ignition -> Termination ---- */}
      <div style={card}>
        <h3 style={h3}>Cascade Ignition &rarr; Termination</h3>
        <p style={note}>
          Where cascades start (ignitors) versus where they settle (terminators / sinks). Per-subsystem
          day counts populate on the next PS2 notebook run; roles are from the locked association structure.
        </p>
        <div style={{ display: 'grid', gridTemplateColumns: '1fr auto 1fr', gap: 16, alignItems: 'center' }}>
          <div>
            <div style={{ fontSize: 11, textTransform: 'uppercase', letterSpacing: 1, color: INK, marginBottom: 6 }}>Ignitors (start)</div>
            {ignitors.map((s) => (
              <span key={s.subsystem} style={{ display: 'inline-block', margin: '3px 6px 3px 0', padding: '4px 12px',
                borderRadius: 16, background: ROLE_COLOR.Ignitor, color: NAVY, fontSize: 12.5, fontWeight: 600 }}>
                {s.subsystem}{s.ignition_days != null ? ` (${fmt(s.ignition_days)})` : ''}
              </span>
            ))}
            {relays.length > 0 && (
              <div style={{ marginTop: 8 }}>
                {relays.map((s) => (
                  <span key={s.subsystem} style={{ display: 'inline-block', margin: '3px 6px 3px 0', padding: '4px 12px',
                    borderRadius: 16, background: ROLE_COLOR.Relay, color: NAVY, fontSize: 12, fontWeight: 600 }}>
                    {s.subsystem} · relay
                  </span>
                ))}
              </div>
            )}
          </div>
          <div style={{ fontSize: 26, color: PASTEL.purple, fontWeight: 700 }}>&rarr;</div>
          <div>
            <div style={{ fontSize: 11, textTransform: 'uppercase', letterSpacing: 1, color: INK, marginBottom: 6 }}>Terminators (settle)</div>
            {terminators.map((s) => (
              <span key={s.subsystem} style={{ display: 'inline-block', margin: '3px 6px 3px 0', padding: '4px 12px',
                borderRadius: 16, background: ROLE_COLOR.Terminator, color: NAVY, fontSize: 12.5, fontWeight: 600 }}>
                {s.subsystem}{s.termination_days != null ? ` (${fmt(s.termination_days)})` : ''}
              </span>
            ))}
          </div>
        </div>
      </div>

      {/* ---- Business Impact ---- */}
      <div style={card}>
        <h3 style={h3}>Highest Business-Impact Devices</h3>
        <p style={note}>
          Cascade-day burden ranking (real). Impact score = chain length &times; fault-type weight,
          populated by the PS2 notebook run.
        </p>
        <table style={{ width: '100%', borderCollapse: 'collapse' }}>
          <thead><tr><th style={th}>#</th><th style={th}>Device</th><th style={th}>Type</th>
            <th style={{ ...th, width: '38%' }}>Cascade days</th><th style={th}>Impact score</th></tr></thead>
          <tbody>
            {impact.map((d) => (
              <tr key={d.device_id}>
                <td style={{ ...td, fontWeight: 700, color: NAVY }}>{d.impact_rank}</td>
                <td style={{ ...td, fontFamily: 'monospace' }}>{d.device_id}</td>
                <td style={td}>
                  <span style={{ padding: '2px 9px', borderRadius: 12, fontSize: 11.5, fontWeight: 600, color: NAVY,
                    background: d.category === 'TVM' ? PASTEL.green : PASTEL.teal }}>{d.category}</span>
                </td>
                <td style={td}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                    <Bar value={d.cascade_days} max={maxDays} color={PASTEL.amber} />
                    <span style={{ minWidth: 34, color: INK }}>{fmt(d.cascade_days)}</span>
                  </div>
                </td>
                <td style={td}>{d.total_impact == null ? <span style={{ color: '#B08A2E' }}>run-pending</span> : fmt(d.total_impact)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
