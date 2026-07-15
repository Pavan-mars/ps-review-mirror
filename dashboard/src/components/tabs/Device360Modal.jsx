import React, { useState, useEffect } from 'react';

// Device-360: cross-PS drill-down for a single device. Fetches /ps1/device-360
// (PS1 prediction + SHAP drivers device-level; PS2 cascade + event codes device-level;
// PS3 severity + PS5 reliability category-level; PS4 anomaly device-level) and offers a
// STAGED ServiceNow scheduled-maintenance incident (no live post until Robin's SN API is wired).
const API_BASE = 'https://a9yuqt9j9b.execute-api.us-east-1.amazonaws.com';

const BAND_COLOR = { Critical: '#ef4444', High: '#f97316', Medium: '#f59e0b', Low: '#3b82f6', Info: '#6b7280' };
const num = (v, d = 1) => (v === null || v === undefined || v === '' ? '—' : (parseFloat(v) * (d === 0 ? 1 : 1)).toFixed(d));
const pct = (v) => (v === null || v === undefined || v === '' ? '—' : `${(parseFloat(v) * 100).toFixed(1)}%`);

function Section({ title, level, children, tint }) {
  return (
    <div className="card" style={{ marginBottom: 14, borderLeft: `3px solid ${tint || '#6366f1'}` }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 8 }}>
        <div className="card-header" style={{ marginBottom: 0 }}>{title}</div>
        {level && (
          <span style={{ fontSize: 10, fontWeight: 700, padding: '2px 8px', borderRadius: 10,
            background: level === 'device' ? 'rgba(34,197,94,0.14)' : 'rgba(245,158,11,0.14)',
            color: level === 'device' ? '#22c55e' : '#f59e0b' }}>
            {level === 'device' ? 'DEVICE-LEVEL' : 'CATEGORY-LEVEL'}
          </span>
        )}
      </div>
      {children}
    </div>
  );
}

export default function Device360Modal({ deviceId, onClose }) {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [staged, setStaged] = useState(null);
  const [staging, setStaging] = useState(false);
  const [showPayload, setShowPayload] = useState(false);

  useEffect(() => {
    let alive = true;
    setLoading(true); setError(null); setStaged(null);
    fetch(`${API_BASE}/ps1/device-360?device_id=${encodeURIComponent(deviceId)}&city=CHI`)
      .then((r) => r.json())
      .then((d) => { if (alive) { setData(d); setLoading(false); } })
      .catch((e) => { if (alive) { setError(String(e)); setLoading(false); } });
    return () => { alive = false; };
  }, [deviceId]);

  function stageServiceNow() {
    if (!data) return;
    setStaging(true);
    fetch(`${API_BASE}/ps1/servicenow-stage`, {
      method: 'POST', headers: { 'content-type': 'application/json' },
      body: JSON.stringify({
        device_id: deviceId, device_category: data.ps1?.device_category,
        short_description: data.servicenow_payload?.short_description, payload: data.servicenow_payload,
      }),
    }).then((r) => r.json()).then((r) => { setStaged(r); setStaging(false); })
      .catch((e) => { setStaged({ error: String(e) }); setStaging(false); });
  }

  const ps1 = data?.ps1 || {}, ps2 = data?.ps2 || {}, ps3 = data?.ps3 || {}, ps4 = data?.ps4 || {}, ps5 = data?.ps5 || {};
  const band = ps1.risk_band;
  const maxShap = Math.max(1e-6, ...(ps1.drivers || []).map((d) => Math.abs(parseFloat(d.shap_value) || 0)));

  return (
    <div onClick={onClose} style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.6)', zIndex: 1000,
      display: 'flex', alignItems: 'flex-start', justifyContent: 'center', overflowY: 'auto', padding: '4vh 2vw' }}>
      <div onClick={(e) => e.stopPropagation()} style={{ background: 'var(--bg-primary, #0f172a)', color: 'var(--text-primary,#e2e8f0)',
        width: 'min(900px, 96vw)', borderRadius: 12, boxShadow: '0 20px 60px rgba(0,0,0,0.5)', padding: 20, border: '1px solid rgba(255,255,255,0.08)' }}>

        {/* Header */}
        <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', marginBottom: 14 }}>
          <div>
            <div style={{ fontSize: 20, fontWeight: 800, fontFamily: 'monospace' }}>{deviceId}</div>
            <div style={{ fontSize: 12, color: 'var(--text-secondary,#94a3b8)', marginTop: 2 }}>
              Device 360 · cross-PS analysis {ps1.device_category ? `· ${ps1.device_category}` : ''} {ps2.facility ? `· ${ps2.facility}` : ''}
            </div>
          </div>
          <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
            {band && <span style={{ fontSize: 12, fontWeight: 800, padding: '4px 12px', borderRadius: 20, background: BAND_COLOR[band], color: '#fff' }}>{band}</span>}
            <button onClick={onClose} style={{ background: 'transparent', color: 'var(--text-secondary,#94a3b8)', border: '1px solid rgba(255,255,255,0.15)', borderRadius: 8, padding: '4px 12px', cursor: 'pointer', fontSize: 16 }}>✕</button>
          </div>
        </div>

        {loading && <div style={{ padding: 40, textAlign: 'center', color: 'var(--text-secondary,#94a3b8)' }}>Loading cross-PS analysis…</div>}
        {error && <div style={{ padding: 20, color: '#ef4444' }}>Failed to load: {error}</div>}

        {!loading && !error && data && (
          <>
            {/* Recommendation */}
            <div style={{ background: 'rgba(99,102,241,0.10)', border: '1px solid rgba(99,102,241,0.3)', borderRadius: 10, padding: '12px 14px', marginBottom: 16 }}>
              <div style={{ fontSize: 11, fontWeight: 700, color: '#818cf8', marginBottom: 4, letterSpacing: 0.5 }}>RECOMMENDATION</div>
              <div style={{ fontSize: 13, lineHeight: 1.5 }}>{data.recommendation}</div>
            </div>

            {/* PS1 */}
            <Section title="PS1 · Failure Prediction" level={ps1.level} tint="#6366f1">
              {ps1.found ? (
                <>
                  <div style={{ display: 'flex', gap: 20, flexWrap: 'wrap', marginBottom: 10 }}>
                    <div><div style={{ fontSize: 11, color: 'var(--text-secondary,#94a3b8)' }}>Failure probability</div><div style={{ fontSize: 22, fontWeight: 800, color: BAND_COLOR[band] }}>{pct(ps1.failure_probability)}</div></div>
                    <div><div style={{ fontSize: 11, color: 'var(--text-secondary,#94a3b8)' }}>Decision threshold</div><div style={{ fontSize: 22, fontWeight: 700 }}>{pct(ps1.decision_threshold)}</div></div>
                    <div><div style={{ fontSize: 11, color: 'var(--text-secondary,#94a3b8)' }}>Model call</div><div style={{ fontSize: 16, fontWeight: 700, color: ps1.predicted_label ? '#ef4444' : '#22c55e' }}>{ps1.predicted_label ? 'FAIL (>= threshold)' : 'OK'}</div></div>
                    <div><div style={{ fontSize: 11, color: 'var(--text-secondary,#94a3b8)' }}>Prediction date</div><div style={{ fontSize: 14 }}>{ps1.prediction_date}</div></div>
                  </div>
                  <div style={{ fontSize: 11, color: '#f59e0b', background: 'rgba(245,158,11,0.1)', padding: '6px 10px', borderRadius: 6, marginBottom: 10 }}>⚠ {ps1.note}</div>
                  {(ps1.drivers || []).length > 0 && (
                    <div>
                      <div style={{ fontSize: 12, fontWeight: 700, marginBottom: 6 }}>Top risk drivers (SHAP)</div>
                      {ps1.drivers.map((d, i) => {
                        const w = Math.abs(parseFloat(d.shap_value) || 0) / maxShap * 100;
                        return (
                          <div key={i} style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 4 }}>
                            <div style={{ width: 190, fontSize: 11, fontFamily: 'monospace', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{d.feature_name}</div>
                            <div style={{ flex: 1, background: 'rgba(255,255,255,0.06)', borderRadius: 4, height: 14 }}>
                              <div style={{ width: `${w}%`, background: '#6366f1', height: 14, borderRadius: 4 }} />
                            </div>
                            <div style={{ width: 70, fontSize: 11, textAlign: 'right', fontVariantNumeric: 'tabular-nums' }}>{num(d.shap_value, 4)}</div>
                            {d.feature_value != null && <div style={{ width: 60, fontSize: 10, color: 'var(--text-secondary,#94a3b8)', textAlign: 'right' }}>val {d.feature_value}</div>}
                          </div>
                        );
                      })}
                    </div>
                  )}
                </>
              ) : <div style={{ fontSize: 13, color: 'var(--text-secondary,#94a3b8)' }}>{ps1.note}</div>}
            </Section>

            {/* PS2 */}
            <Section title="PS2 · Cascading Failure" level={ps2.level} tint="#10b981">
              {ps2.in_catalog || ps2.in_top_devices ? (
                <>
                  <div style={{ display: 'flex', gap: 20, flexWrap: 'wrap', marginBottom: 8 }}>
                    <div><div style={{ fontSize: 11, color: 'var(--text-secondary,#94a3b8)' }}>Cascade days</div><div style={{ fontSize: 18, fontWeight: 700 }}>{ps2.cascade_days ?? '—'}</div></div>
                    {ps2.cascade_rank && <div><div style={{ fontSize: 11, color: 'var(--text-secondary,#94a3b8)' }}>Cascade rank</div><div style={{ fontSize: 18, fontWeight: 700, color: '#f97316' }}>#{ps2.cascade_rank}</div></div>}
                    <div><div style={{ fontSize: 11, color: 'var(--text-secondary,#94a3b8)' }}>Dominant error code</div><div style={{ fontSize: 15, fontWeight: 700, fontFamily: 'monospace' }}>{ps2.dom_error_code || '—'}</div></div>
                    <div><div style={{ fontSize: 11, color: 'var(--text-secondary,#94a3b8)' }}>Dominant subsystem</div><div style={{ fontSize: 15 }}>{ps2.dom_subsystem || '—'}</div></div>
                  </div>
                  {ps2.worst_cascade_path && <div style={{ fontSize: 12, marginBottom: 8 }}><span style={{ color: 'var(--text-secondary,#94a3b8)' }}>Worst path: </span><span style={{ fontFamily: 'monospace' }}>{ps2.worst_cascade_path}</span></div>}
                  {(ps2.recent_cascades || []).length > 0 && (
                    <div>
                      <div style={{ fontSize: 12, fontWeight: 700, marginBottom: 4 }}>Recent event-code chains</div>
                      <table className="data-table" style={{ fontSize: 11 }}>
                        <thead><tr><th>Day</th><th>Event-code chain</th><th>Len</th><th>Span (min)</th></tr></thead>
                        <tbody>
                          {ps2.recent_cascades.map((c, i) => (
                            <tr key={i}><td>{c.transit_day}</td><td style={{ fontFamily: 'monospace' }}>{c.event_code_chain}</td><td>{c.chain_length}</td><td>{c.chain_span_min}</td></tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  )}
                </>
              ) : <div style={{ fontSize: 13, color: 'var(--text-secondary,#94a3b8)' }}>Not among the top cascade-active devices (no PS2 cascade record for this device).</div>}
            </Section>

            <div className="grid-2" style={{ gap: 14 }}>
              {/* PS3 */}
              <Section title="PS3 · Failure Severity" level={ps3.level} tint="#a855f7">
                {ps3.metrics ? (
                  <div style={{ display: 'flex', gap: 20 }}>
                    <div><div style={{ fontSize: 11, color: 'var(--text-secondary,#94a3b8)' }}>Macro-F1 ({ps3.category})</div><div style={{ fontSize: 18, fontWeight: 700 }}>{num(ps3.metrics.f1_macro, 3)}</div></div>
                    <div><div style={{ fontSize: 11, color: 'var(--text-secondary,#94a3b8)' }}>Accuracy</div><div style={{ fontSize: 18, fontWeight: 700 }}>{num(ps3.metrics.accuracy, 3)}</div></div>
                    <div><div style={{ fontSize: 11, color: 'var(--text-secondary,#94a3b8)' }}>Test incidents</div><div style={{ fontSize: 18 }}>{ps3.metrics.n_incidents ?? '—'}</div></div>
                  </div>
                ) : <div style={{ fontSize: 12, color: 'var(--text-secondary,#94a3b8)' }}>No PS3 metrics for this category.</div>}
                <div style={{ fontSize: 10, color: 'var(--text-secondary,#94a3b8)', marginTop: 8 }}>{ps3.note}</div>
              </Section>

              {/* PS5 */}
              <Section title="PS5 · Reliability / RUL" level={ps5.level} tint="#0ea5e9">
                {ps5.reliability ? (
                  <div>
                    <div style={{ display: 'flex', gap: 20 }}>
                      <div><div style={{ fontSize: 11, color: 'var(--text-secondary,#94a3b8)' }}>Concordance</div><div style={{ fontSize: 18, fontWeight: 700 }}>{num(ps5.reliability.concordance_index, 3)}</div></div>
                      <div><div style={{ fontSize: 11, color: 'var(--text-secondary,#94a3b8)' }}>Gate</div><div style={{ fontSize: 14, fontWeight: 700, color: ps5.reliability.dashboard_ready ? '#22c55e' : '#ef4444' }}>{ps5.reliability.dashboard_ready ? 'OPEN' : 'CLOSED'}</div></div>
                    </div>
                    {ps5.reliability.blockers && <div style={{ fontSize: 10, color: '#f59e0b', marginTop: 6 }}>Blockers: {ps5.reliability.blockers}</div>}
                  </div>
                ) : <div style={{ fontSize: 12, color: 'var(--text-secondary,#94a3b8)' }}>No PS5 record for this category.</div>}
                <div style={{ fontSize: 10, color: 'var(--text-secondary,#94a3b8)', marginTop: 8 }}>{ps5.note}</div>
              </Section>
            </div>

            {/* PS4 */}
            <Section title="PS4 · Anomaly Detection" level={ps4.level} tint="#f59e0b">
              {(ps4.alerts || []).length > 0 ? (
                <table className="data-table" style={{ fontSize: 11 }}>
                  <thead><tr><th>Detected</th><th>Signal</th><th>Score</th><th>Severity</th><th>Status</th></tr></thead>
                  <tbody>
                    {ps4.alerts.map((a, i) => (
                      <tr key={i}><td>{String(a.detected_at).slice(0, 16)}</td><td>{a.triggering_signal}</td><td>{num(a.anomaly_score, 3)}</td><td>{a.severity}</td><td>{a.status}</td></tr>
                    ))}
                  </tbody>
                </table>
              ) : <div style={{ fontSize: 13, color: 'var(--text-secondary,#94a3b8)' }}>No PS4 anomaly alerts for this device. <span style={{ fontSize: 10 }}>{ps4.note}</span></div>}
            </Section>

            {/* ServiceNow (staged) */}
            <div className="card" style={{ borderLeft: '3px solid #ef4444' }}>
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                <div>
                  <div className="card-header" style={{ marginBottom: 2 }}>ServiceNow · Scheduled Maintenance</div>
                  <div style={{ fontSize: 11, color: 'var(--text-secondary,#94a3b8)' }}>Stages an incident payload for review. No live post until the SN API is wired.</div>
                </div>
                <div style={{ display: 'flex', gap: 8 }}>
                  <button onClick={() => setShowPayload((s) => !s)} style={{ background: 'transparent', color: '#818cf8', border: '1px solid rgba(129,140,248,0.4)', borderRadius: 8, padding: '6px 12px', cursor: 'pointer', fontSize: 12 }}>{showPayload ? 'Hide' : 'View'} payload</button>
                  <button onClick={stageServiceNow} disabled={staging} style={{ background: staged && !staged.error ? '#22c55e' : '#6366f1', color: '#fff', border: 'none', borderRadius: 8, padding: '6px 14px', cursor: staging ? 'wait' : 'pointer', fontSize: 12, fontWeight: 700 }}>
                    {staging ? 'Staging…' : staged && !staged.error ? '✓ Staged' : 'Stage incident'}
                  </button>
                </div>
              </div>
              {staged && !staged.error && <div style={{ fontSize: 11, color: '#22c55e', marginTop: 8 }}>Staged (id {String(staged.staged_id).slice(0, 8)}…). {staged.note}</div>}
              {staged && staged.error && <div style={{ fontSize: 11, color: '#ef4444', marginTop: 8 }}>Error: {staged.error}</div>}
              {showPayload && <pre style={{ marginTop: 10, background: 'rgba(0,0,0,0.3)', padding: 10, borderRadius: 6, fontSize: 10, overflowX: 'auto', maxHeight: 200 }}>{JSON.stringify(data.servicenow_payload, null, 2)}</pre>}
            </div>
          </>
        )}
      </div>
    </div>
  );
}
