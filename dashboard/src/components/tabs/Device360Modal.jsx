import React, { useState, useEffect } from 'react';
import { compLabelShort } from '../shared/DashboardKit';

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

// One causation method, one column. Kept deliberately plain: four small ranked
// lists read faster than four charts, and the comparison the reader needs is
// "do these methods name the same consequent", which is a scan down four
// columns rather than anything a chart improves on.
function CausationList({ title, rows, render }) {
  return (
    <div>
      <div style={{ fontSize: 11, fontWeight: 700, marginBottom: 6, color: 'var(--text-secondary, #475569)' }}>{title}</div>
      {!rows || !rows.length ? (
        <div style={{ fontSize: 12, color: 'var(--text-secondary, #475569)', opacity: 0.7 }}>No rows for this anchor.</div>
      ) : rows.map((r, i) => (
        <div key={`${r.to_sub}-${r.window_bucket || i}`}
          style={{ display: 'flex', justifyContent: 'space-between', gap: 10, fontSize: 12,
            padding: '3px 0', borderBottom: i === rows.length - 1 ? 'none' : '1px solid var(--border, #e2e8f0)' }}>
          <span style={{ fontFamily: 'monospace', fontWeight: 600 }}>{r.to_sub}</span>
          <span style={{ color: 'var(--text-secondary, #475569)', whiteSpace: 'nowrap' }}>{render(r)}</span>
        </div>
      ))}
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

  const xps = data?.cross_ps;
  const causation = data?.causation;
  const ps1 = data?.ps1 || {}, ps2 = data?.ps2 || {}, ps3 = data?.ps3 || {}, ps4 = data?.ps4 || {}, ps5 = data?.ps5 || {};
  const band = ps1.risk_band;
  const maxShap = Math.max(1e-6, ...(ps1.drivers || []).map((d) => Math.abs(parseFloat(d.shap_value) || 0)));

  return (
    <div onClick={onClose} style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.6)', zIndex: 1000,
      display: 'flex', alignItems: 'flex-start', justifyContent: 'center', overflowY: 'auto', padding: '4vh 2vw' }}>
      {/* 2026-07-26 -- the modal declares its own ink INSIDE the card rather than
          inheriting the app tokens. It was authored against a dark shell
          (#0f172a surface, #e2e8f0 text, slate-400 secondary) while the dashboard
          renders light, so every small label came out slate-400 on white: roughly
          2.6:1 contrast, under the 4.5:1 WCAG AA floor for body text.

          Overriding --text-primary / --text-secondary HERE means the 26 nested
          `var(--text-secondary, ...)` uses all darken at once, without touching
          the app-wide theme or hunting every call site. */}
      <div onClick={(e) => e.stopPropagation()} style={{
        background: 'var(--surface, #ffffff)',
        color: 'var(--text-primary, #0f172a)',
        '--text-primary': 'var(--modal-ink, #0f172a)',
        '--text-secondary': 'var(--modal-ink-muted, #475569)',
        width: 'min(900px, 96vw)', borderRadius: 12,
        boxShadow: '0 20px 60px rgba(15,23,42,0.28)', padding: 20,
        border: '1px solid var(--border, #e2e8f0)' }}>

        {/* Header */}
        <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', marginBottom: 14 }}>
          <div>
            <div style={{ fontSize: 20, fontWeight: 800, fontFamily: 'monospace' }}>{deviceId}</div>
            <div style={{ fontSize: 12, color: 'var(--text-secondary, #475569)', marginTop: 2 }}>
              Device 360 · cross-PS analysis {ps1.device_category ? `· ${ps1.device_category}` : ''} {ps2.facility ? `· ${ps2.facility}` : ''}
            </div>
          </div>
          <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
            {band && <span style={{ fontSize: 12, fontWeight: 800, padding: '4px 12px', borderRadius: 20, background: BAND_COLOR[band], color: '#fff' }}>{band}</span>}
            <button onClick={onClose} style={{ background: 'transparent', color: 'var(--text-secondary, #475569)', border: '1px solid var(--border, #cbd5e1)', borderRadius: 8, padding: '4px 12px', cursor: 'pointer', fontSize: 16 }}>✕</button>
          </div>
        </div>

        {loading && <div style={{ padding: 40, textAlign: 'center', color: 'var(--text-secondary, #475569)' }}>Loading cross-PS analysis…</div>}
        {error && <div style={{ padding: 20, color: '#ef4444' }}>Failed to load: {error}</div>}

        {!loading && !error && data && (
          <>
            {/* Recommendation */}
            <div style={{ background: 'rgba(99,102,241,0.10)', border: '1px solid rgba(99,102,241,0.3)', borderRadius: 10, padding: '12px 14px', marginBottom: 16 }}>
              <div style={{ fontSize: 11, fontWeight: 700, color: '#818cf8', marginBottom: 4, letterSpacing: 0.5 }}>RECOMMENDATION</div>
              <div style={{ fontSize: 13, lineHeight: 1.5 }}>
                {data.recommendation || 'No cross-PS recommendation for this device: none of the '
                  + 'five models returned a record for it in the latest run. That is a coverage '
                  + 'gap, not a clean bill of health.'}
              </div>
            </div>

            {/* 27-Jul-2026. Cross-PS corroboration. The five panels below are
                independent models; this states where they agree, which is the
                only thing in the modal that is stronger than any single one of
                them. Computed server-side in _cross_ps so the wording cannot
                drift between this modal and the ServiceNow payload. */}
            {xps && (
              <div style={{ border: '1px solid rgba(148,163,184,0.4)', borderRadius: 10,
                padding: '12px 14px', marginBottom: 16,
                background: xps.subsystem_verdict === 'agree' ? 'rgba(34,197,94,0.08)'
                  : xps.subsystem_verdict === 'disagree' ? 'rgba(245,158,11,0.08)' : 'transparent' }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 10,
                  flexWrap: 'wrap', marginBottom: 6 }}>
                  <span style={{ fontSize: 11, fontWeight: 700, letterSpacing: 0.5,
                    color: 'var(--text-secondary, #475569)' }}>CROSS-PS CORROBORATION</span>
                  <span style={{ fontSize: 11, fontWeight: 700, padding: '2px 8px', borderRadius: 10,
                    background: xps.subsystem_verdict === 'agree' ? 'rgba(34,197,94,0.18)'
                      : xps.subsystem_verdict === 'disagree' ? 'rgba(245,158,11,0.2)' : 'rgba(148,163,184,0.2)',
                    color: xps.subsystem_verdict === 'agree' ? '#15803d'
                      : xps.subsystem_verdict === 'disagree' ? '#b45309' : '#475569' }}>
                    {xps.subsystem_verdict === 'agree' ? 'PS2 and PS3 agree'
                      : xps.subsystem_verdict === 'disagree' ? 'PS2 and PS3 disagree'
                      : xps.subsystem_verdict === 'single_source' ? 'one source only' : 'no subsystem named'}
                  </span>
                  {xps.signal_count > 1 && (
                    <span style={{ fontSize: 11, fontWeight: 700, padding: '2px 8px', borderRadius: 10,
                      background: 'rgba(239,68,68,0.15)', color: '#b91c1c' }}>
                      flagged by {xps.signal_count} independent models
                    </span>
                  )}
                </div>
                <div style={{ fontSize: 12.5, lineHeight: 1.6 }}>{xps.subsystem_detail}</div>
                {(xps.signals || []).length > 0 && (
                  <div style={{ marginTop: 8, display: 'flex', gap: 6, flexWrap: 'wrap' }}>
                    {xps.signals.map((sg, i) => (
                      <span key={i} style={{ fontSize: 11, padding: '2px 8px', borderRadius: 6,
                        background: 'rgba(99,102,241,0.12)', color: '#4f46e5', fontWeight: 600 }}>{sg}</span>
                    ))}
                  </div>
                )}
              </div>
            )}

            {/* PS1 */}
            <Section title="PS1 · Failure Prediction" level={ps1.level} tint="#6366f1">
            {data.ps1_state && (
              <div style={{ margin: '0 0 10px', padding: 10, borderRadius: 8, fontSize: 12.5,
                background: data.ps1_state.device_state === 'IN_SPELL' ? '#fef2f2'
                  : data.ps1_state.device_state === 'NEW_ONSET' ? '#fffbeb'
                  : data.ps1_state.device_state === 'RECOVERED' ? '#f0f9ff' : '#f0fdf4',
                borderLeft: '4px solid ' + (
                  data.ps1_state.device_state === 'IN_SPELL' ? '#dc2626'
                  : data.ps1_state.device_state === 'NEW_ONSET' ? '#f59e0b'
                  : data.ps1_state.device_state === 'RECOVERED' ? '#0ea5e9' : '#16a34a') }}>
                <strong>{
                  data.ps1_state.device_state === 'IN_SPELL' ? 'Out of service now'
                  : data.ps1_state.device_state === 'NEW_ONSET' ? 'Out-of-service window just opened'
                  : data.ps1_state.device_state === 'RECOVERED' ? 'Back in service'
                  : 'No out-of-service event in the scored period'}</strong>
                {' \u2014 '}{data.ps1_state.state_note}
                {/* The framing, on the device panel. The PS1 score marks
                    out-of-service STATE, not arrival: 95-98% of positive label
                    days follow another positive day. Without this line, a
                    probability here reads as a failure forecast. */}
                <div style={{ marginTop: 6, color: '#64748b' }}>
                  Spells {data.ps1_state.n_spells ?? '\u2014'}
                  {' \u00b7 total days out '}{data.ps1_state.total_oos_days ?? '\u2014'}
                  {data.ps1_state.days_since_spell_end != null && (
                    <span>{' \u00b7 last spell ended '}{data.ps1_state.days_since_spell_end}
                    {' day(s) before the latest score'}</span>)}
                </div>
              </div>
            )}
              {ps1.found ? (
                <>
                  <div style={{ display: 'flex', gap: 20, flexWrap: 'wrap', marginBottom: 10 }}>
                    <div><div style={{ fontSize: 11, color: 'var(--text-secondary, #475569)' }}>Failure probability</div><div style={{ fontSize: 22, fontWeight: 800, color: BAND_COLOR[band] }}>{pct(ps1.failure_probability)}</div></div>
                    <div><div style={{ fontSize: 11, color: 'var(--text-secondary, #475569)' }}>Decision threshold</div><div style={{ fontSize: 22, fontWeight: 700 }}>{pct(ps1.decision_threshold)}</div></div>
                    <div><div style={{ fontSize: 11, color: 'var(--text-secondary, #475569)' }}>Model call</div><div style={{ fontSize: 16, fontWeight: 700, color: ps1.predicted_label ? '#ef4444' : '#22c55e' }}>{ps1.predicted_label ? 'FAIL (>= threshold)' : 'OK'}</div></div>
                    <div><div style={{ fontSize: 11, color: 'var(--text-secondary, #475569)' }}>Prediction date</div><div style={{ fontSize: 14 }}>{ps1.prediction_date}</div></div>
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
                            <div style={{ flex: 1, background: 'var(--border, #e2e8f0)', borderRadius: 4, height: 14 }}>
                              <div style={{ width: `${w}%`, background: '#6366f1', height: 14, borderRadius: 4 }} />
                            </div>
                            <div style={{ width: 70, fontSize: 11, textAlign: 'right', fontVariantNumeric: 'tabular-nums' }}>{num(d.shap_value, 4)}</div>
                            {d.feature_value != null && <div style={{ width: 60, fontSize: 11, fontWeight: 600, color: 'var(--text-secondary, #475569)', textAlign: 'right' }}>val {d.feature_value}</div>}
                          </div>
                        );
                      })}
                    </div>
                  )}
                </>
              ) : (
                <div style={{ fontSize: 13, color: 'var(--text-secondary, #475569)' }}>
                  {/* 27-Jul-2026. Never render an empty section. If the API sends
                      no note the panel used to collapse to a bare heading, which
                      reads as a broken dashboard rather than as an absent record. */}
                  {ps1.note || 'No PS1 prediction for this device in the latest run. '
                    + 'PS1 scored VALIDATOR devices only on the 26-Jul run \u2014 all three '
                    + 'notebooks wrote to one unpartitioned gold path, so the last to '
                    + 'run overwrote the TVM and GATE predictions.'}
                </div>
              )}
            </Section>

            {/* PS2 */}
            <Section title="PS2 · Cascading Failure" level={ps2.level} tint="#10b981">
              {ps2.in_catalog || ps2.in_top_devices ? (
                <>
                  <div style={{ display: 'flex', gap: 20, flexWrap: 'wrap', marginBottom: 8 }}>
                    <div><div style={{ fontSize: 11, color: 'var(--text-secondary, #475569)' }}>Cascade days</div><div style={{ fontSize: 18, fontWeight: 700 }}>{ps2.cascade_days ?? '—'}</div></div>
                    {ps2.cascade_rank && <div><div style={{ fontSize: 11, color: 'var(--text-secondary, #475569)' }}>Cascade rank</div><div style={{ fontSize: 18, fontWeight: 700, color: '#f97316' }}>#{ps2.cascade_rank}</div></div>}
                    <div><div style={{ fontSize: 11, color: 'var(--text-secondary, #475569)' }}>Dominant error code</div><div style={{ fontSize: 15, fontWeight: 700, fontFamily: 'monospace' }}>{ps2.dom_error_code || '—'}</div></div>
                    <div><div style={{ fontSize: 11, color: 'var(--text-secondary, #475569)' }}>Dominant subsystem</div><div style={{ fontSize: 15 }}>{ps2.dom_subsystem || '—'}</div></div>
                  </div>
                  {/* 27-Jul-2026. Chain shape and propagation speed. Both were
                      fetched by the API and never rendered, and they are the two
                      things that decide what you DO about a cascade-active
                      device: how long the chains get, and how fast they run. */}
                  <div style={{ display: 'flex', gap: 20, flexWrap: 'wrap', marginBottom: 10 }}>
                    {ps2.chain_profile?.avg_chain_len != null && (
                      <div><div style={{ fontSize: 11, color: 'var(--text-secondary, #475569)' }}>Chain length</div>
                        <div style={{ fontSize: 15, fontWeight: 700 }}>
                          {num(ps2.chain_profile.avg_chain_len, 1)} mean · {ps2.chain_profile.max_chain_len ?? '—'} worst
                        </div></div>
                    )}
                    {ps2.propagation_speed && (
                      <div><div style={{ fontSize: 11, color: 'var(--text-secondary, #475569)' }}>Propagation</div>
                        <div style={{ fontSize: 15, fontWeight: 700,
                          color: ps2.propagation_speed === 'fast' ? '#ef4444'
                            : ps2.propagation_speed === 'slow' ? '#22c55e' : '#f59e0b' }}>
                          {ps2.propagation_speed}
                          {ps2.pct_cascades_under_15min != null
                            && <span style={{ fontWeight: 400, fontSize: 12 }}> · {pct(ps2.pct_cascades_under_15min)} under 15 min</span>}
                        </div></div>
                    )}
                    {ps2.cascade_entry_subsystem && (
                      <div><div style={{ fontSize: 11, color: 'var(--text-secondary, #475569)' }}>Cascades start at</div>
                        <div style={{ fontSize: 15, fontWeight: 700 }}>{ps2.cascade_entry_subsystem}</div></div>
                    )}
                    {/* 27-Jul-2026. Business impact and chronic recurrence, now
                        that the device feed is v_ps2_device_cascade rather than
                        the one-row ps2_top_devices seed. */}
                    {ps2.total_impact != null && (
                      <div><div style={{ fontSize: 11, color: 'var(--text-secondary, #475569)' }}>Business impact</div>
                        <div style={{ fontSize: 15, fontWeight: 700 }}>
                          {Number(ps2.total_impact).toLocaleString()}
                          {ps2.avg_impact != null && <span style={{ fontWeight: 400, fontSize: 12 }}> · {num(ps2.avg_impact, 1)} avg</span>}
                        </div></div>
                    )}
                    {ps2.chronic !== undefined && (
                      <div><div style={{ fontSize: 11, color: 'var(--text-secondary, #475569)' }}>Recurrence</div>
                        <div style={{ fontSize: 15, fontWeight: 700, color: ps2.chronic ? '#ef4444' : undefined }}>
                          {ps2.chronic ? 'Chronic' : 'Not chronic'}
                          {ps2.recurrence_cascade_days != null
                            && <span style={{ fontWeight: 400, fontSize: 12 }}> · {ps2.recurrence_cascade_days} days</span>}
                        </div></div>
                    )}
                  </div>

                  {/* Where the chains land in time. A bar per window beats five
                      raw counts -- the shape is the message. */}
                  {ps2.windows && ps2.cascade_window_total > 0 && (
                    <div style={{ marginBottom: 10 }}>
                      <div style={{ fontSize: 12, fontWeight: 700, marginBottom: 5 }}>
                        Cascade timing — {ps2.cascade_window_total} chain(s)
                      </div>
                      <div style={{ display: 'flex', gap: 4 }}>
                        {[['w0_5', '0–5 min'], ['w5_15', '5–15'], ['w15_30', '15–30'],
                          ['w30_60', '30–60'], ['w60plus', '60+']].map(([k, lab]) => {
                          const v = Number(ps2.windows[k] || 0);
                          const share = v / (ps2.cascade_window_total || 1);
                          return (
                            <div key={k} style={{ flex: 1, textAlign: 'center' }}
                              title={`${lab}: ${v} chain(s), ${(share * 100).toFixed(1)}%`}>
                              <div style={{ height: 46, display: 'flex', alignItems: 'flex-end' }}>
                                <div style={{ width: '100%', height: `${Math.max(3, share * 100)}%`,
                                  background: k === 'w0_5' || k === 'w5_15' ? '#ef4444' : '#6366f1',
                                  borderRadius: '4px 4px 0 0' }} />
                              </div>
                              <div style={{ fontSize: 11, fontWeight: 700 }}>{v}</div>
                              <div style={{ fontSize: 10, color: 'var(--text-secondary, #475569)' }}>{lab}</div>
                            </div>
                          );
                        })}
                      </div>
                    </div>
                  )}

                  {ps2.worst_cascade_path && <div style={{ fontSize: 12, marginBottom: 8 }}><span style={{ color: 'var(--text-secondary, #475569)' }}>Worst path: </span><span style={{ fontFamily: 'monospace' }}>{ps2.worst_cascade_path}</span>{ps2.chain_profile?.worst_window && <span style={{ color: 'var(--text-secondary, #475569)' }}> · {ps2.chain_profile.worst_window}</span>}</div>}
                  {(ps2.recent_cascades || []).length > 0 && (
                    <div>
                      <div style={{ fontSize: 12, fontWeight: 700, marginBottom: 4 }}>Recent cascades</div>
                      <table className="data-table" style={{ fontSize: 11 }}>
                        {/* Subsystem chain added: the event-code chain is precise but
                            unreadable, and the subsystem chain is what a technician
                            can act on. Both shown, codes second. */}
                        <thead><tr><th>Day</th><th>Subsystem chain</th><th>Event-code chain</th><th>Len</th><th>Span (min)</th></tr></thead>
                        <tbody>
                          {ps2.recent_cascades.map((c, i) => (
                            <tr key={i}>
                              <td>{c.transit_day}</td>
                              <td style={{ fontWeight: 600 }}>{c.subsystem_chain || '—'}</td>
                              <td style={{ fontFamily: 'monospace', color: 'var(--text-secondary, #475569)' }}>{c.event_code_chain}</td>
                              <td>{c.chain_length}</td><td>{c.chain_span_min}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  )}
                  {(ps2.facility || ps2.serial || ps2.operator) && (
                    <div style={{ marginTop: 8, fontSize: 11, color: 'var(--text-secondary, #475569)',
                      display: 'flex', gap: 14, flexWrap: 'wrap' }}>
                      {ps2.facility && <span>Facility {ps2.facility}</span>}
                      {ps2.serial && <span>Serial {ps2.serial}</span>}
                      {ps2.operator && <span>Operator {ps2.operator}</span>}
                      {ps2.control_group && <span>Control group {ps2.control_group}</span>}
                    </div>
                  )}
                </>
              ) : <div style={{ fontSize: 13, color: 'var(--text-secondary, #475569)' }}>Not among the top cascade-active devices (no PS2 cascade record for this device).</div>}
            </Section>

            {/* 27-Jul-2026. Causation. Everything above says WHAT happened on
                this device; this says "and then what". Four independent methods
                on one anchor subsystem, shown side by side rather than blended
                into a single score -- lift and probability answer different
                questions, and a pair that is highly probable but barely lifted
                is a common subsystem, not a causal link. */}
            {causation && !causation.anchor_subsystem && (
              <Section title="Causation · what follows this device's entry subsystem" level="device" tint="#ec4899">
                <div style={{ fontSize: 13, color: 'var(--text-secondary, #475569)' }}>{causation.note}</div>
              </Section>
            )}
            {causation && causation.anchor_subsystem && (
              <Section title="Causation · what follows this device's entry subsystem" level="device" tint="#ec4899">
                {causation.lead_link && (
                  <div style={{ background: 'rgba(236,72,153,0.08)', border: '1px solid rgba(236,72,153,0.25)',
                    borderRadius: 10, padding: '10px 12px', marginBottom: 10, fontSize: 13 }}>
                    <strong style={{ fontFamily: 'monospace' }}>{causation.lead_link.from_sub}</strong>
                    {' → '}
                    <strong style={{ fontFamily: 'monospace' }}>{causation.lead_link.to_sub}</strong>
                    {causation.lead_link.lift != null && <> · lift <strong>{num(causation.lead_link.lift, 2)}×</strong></>}
                    {causation.lead_link.median_minutes != null
                      && <> · typically <strong>{num(causation.lead_link.median_minutes, 0)} min</strong> later</>}
                    {causation.lead_link.n_observations != null
                      && <span style={{ color: 'var(--text-secondary, #475569)' }}> · {Number(causation.lead_link.n_observations).toLocaleString()} observations</span>}
                  </div>
                )}
                <div style={{ fontSize: 11, color: 'var(--text-secondary, #475569)', marginBottom: 8 }}>
                  Anchored on <strong>{causation.anchor_subsystem}</strong> — {causation.anchor_source}
                </div>
                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(230px, 1fr))', gap: 12 }}>
                  <CausationList title="Association rules (lift)" rows={causation.rules}
                    render={(r) => `${num(r.lift, 2)}×  conf ${pct(r.confidence)}`} />
                  <CausationList title="Next-state probability (Markov)" rows={causation.transitions}
                    render={(r) => pct(r.prob)} />
                  <CausationList title="Lead / lag timing (minutes)" rows={causation.timing}
                    render={(r) => `${num(r.median, 0)} med · ${num(r.p25, 0)}–${num(r.p75, 0)}`} />
                  <CausationList title="Conditional P(B|A) by window" rows={causation.conditional}
                    render={(r) => `${pct(r.p_b_given_a)}  ${r.window_bucket || ''}`} />
                </div>
                <div style={{ marginTop: 10, fontSize: 11, color: 'var(--text-secondary, #475569)', fontStyle: 'italic' }}>
                  {causation.caveat}
                </div>
              </Section>
            )}

            <div className="grid-2" style={{ gap: 14 }}>
              {/* PS3 */}
              {/* 27-Jul-2026. This used to show the CATEGORY's test metrics --
                  macro-F1 and accuracy for all TVMs -- which told you nothing
                  about the device you had just clicked. It now shows this
                  device's own incidents, severity and attributed subsystem, and
                  the components actually fitted to it. */}
              <Section title="PS3 · Root Cause & Severity" level={ps3.level} tint="#a855f7">
                {ps3.found ? (
                  <>
                    <div style={{ display: 'flex', gap: 20, flexWrap: 'wrap' }}>
                      <div><div style={{ fontSize: 11, color: 'var(--text-secondary, #475569)' }}>OOS incidents</div>
                        <div style={{ fontSize: 18, fontWeight: 700 }}>{ps3.n_incidents ?? '—'}</div></div>
                      <div><div style={{ fontSize: 11, color: 'var(--text-secondary, #475569)' }}>Critical share</div>
                        <div style={{ fontSize: 18, fontWeight: 700 }}>
                          {ps3.severity_shippable === false
                            ? <span style={{ fontSize: 12, color: '#f59e0b' }}>gated</span>
                            : pct(ps3.pct_critical_pred)}
                        </div></div>
                      <div><div style={{ fontSize: 11, color: 'var(--text-secondary, #475569)' }}>Attributed subsystem</div>
                        <div style={{ fontSize: 15, fontWeight: 700 }}>
                          {ps3.rootcause_shippable === false ? 'gated' : compLabelShort(ps3.dominant_pred_component)}
                        </div></div>
                    </div>
                    {(ps3.components || []).length > 0 && (
                      <div style={{ marginTop: 10 }}>
                        <div style={{ fontSize: 12, fontWeight: 700, marginBottom: 4 }}>
                          Components fitted ({ps3.components.length})
                        </div>
                        <table className="data-table" style={{ fontSize: 11 }}>
                          <thead><tr><th>Component</th><th>Serial</th><th style={{ textAlign: 'right' }}>Age (d)</th></tr></thead>
                          <tbody>
                            {ps3.components.map((c, i) => (
                              <tr key={i}>
                                <td style={{ fontWeight: 600 }}>{c.component_description || '—'}</td>
                                <td style={{ fontFamily: 'monospace', fontSize: 10 }}>{c.matched_serial_nbr}</td>
                                <td style={{ textAlign: 'right' }}>{num(c.component_age_days, 0)}</td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </div>
                    )}
                  </>
                ) : (
                  <div style={{ fontSize: 12, color: 'var(--text-secondary, #475569)', lineHeight: 1.6 }}>
                    {ps3.not_in_feed && (
                      <div style={{ fontWeight: 700, color: '#b45309', marginBottom: 4 }}>
                        This device type is not in the PS3 feed — no failure severity exists for it.
                      </div>
                    )}
                    {ps3.note || 'No PS3 incident attributed to this device in the latest run.'}
                    {ps3.alternative_coverage && (
                      <div style={{ marginTop: 4 }}><em>Covered instead by:</em> {ps3.alternative_coverage}</div>
                    )}
                  </div>
                )}
              </Section>

              {/* PS5 */}
              <Section title="PS5 · Reliability / RUL" level={ps5.level} tint="#0ea5e9">
                {/* 27-Jul-2026. Device-level RUL first. The category concordance
                    index tells you how good the model is; the RUL tells you what
                    it says about THIS device, which is what the reader came for. */}
                {ps5.found ? (
                  <div>
                    <div style={{ display: 'flex', gap: 20, flexWrap: 'wrap' }}>
                      <div><div style={{ fontSize: 11, color: 'var(--text-secondary, #475569)' }}>RUL (standard)</div>
                        <div style={{ fontSize: 20, fontWeight: 800 }}>{num(ps5.rul_standard_days, 0)}<span style={{ fontSize: 12, fontWeight: 500 }}> d</span></div></div>
                      <div><div style={{ fontSize: 11, color: 'var(--text-secondary, #475569)' }}>RUL (conservative)</div>
                        <div style={{ fontSize: 20, fontWeight: 800 }}>{num(ps5.rul_conservative_days, 0)}<span style={{ fontSize: 12, fontWeight: 500 }}> d</span></div></div>
                      {ps5.rul_spread_days != null && (
                        <div><div style={{ fontSize: 11, color: 'var(--text-secondary, #475569)' }}>Uncertainty</div>
                          <div style={{ fontSize: 15, fontWeight: 700,
                            color: ps5.rul_uncertainty === 'wide' ? '#f59e0b' : '#22c55e' }}
                            title="The gap between the standard and conservative estimates is the model's own uncertainty. A wide spread should temper any date scheduled off the standard figure.">
                            {ps5.rul_uncertainty || '—'}
                            <span style={{ fontWeight: 400, fontSize: 12 }}> · {num(ps5.rul_spread_days, 0)} d spread</span>
                          </div></div>
                      )}
                    </div>
                    <div style={{ display: 'flex', gap: 20, flexWrap: 'wrap', marginTop: 10 }}>
                      {ps5.reader_fault_count_30d != null && (
                        <div><div style={{ fontSize: 11, color: 'var(--text-secondary, #475569)' }}>Reader faults (30d)</div>
                          <div style={{ fontSize: 15, fontWeight: 700 }}>{ps5.reader_fault_count_30d}</div></div>
                      )}
                      <div><div style={{ fontSize: 11, color: 'var(--text-secondary, #475569)' }}>Data-quality gate</div>
                        <div style={{ fontSize: 14, fontWeight: 700,
                          color: ps5.data_quality_gate_passed ? '#22c55e' : '#f59e0b' }}>
                          {ps5.data_quality_gate_passed ? 'PASSED' : 'NOT PASSED'}</div></div>
                      {ps5.reliability?.concordance_index != null && (
                        <div><div style={{ fontSize: 11, color: 'var(--text-secondary, #475569)' }}>Model concordance ({ps5.category})</div>
                          <div style={{ fontSize: 15, fontWeight: 700 }}>{num(ps5.reliability.concordance_index, 3)}</div></div>
                      )}
                      {ps5.as_of_date && (
                        <div><div style={{ fontSize: 11, color: 'var(--text-secondary, #475569)' }}>As of</div>
                          <div style={{ fontSize: 14 }}>{String(ps5.as_of_date).slice(0, 10)}</div></div>
                      )}
                    </div>
                    {ps5.reliability?.blockers && <div style={{ fontSize: 10, color: '#f59e0b', marginTop: 8 }}>Blockers: {ps5.reliability.blockers}</div>}
                  </div>
                ) : ps5.reliability ? (
                  <div>
                    <div style={{ display: 'flex', gap: 20 }}>
                      <div><div style={{ fontSize: 11, color: 'var(--text-secondary, #475569)' }}>Concordance ({ps5.category})</div><div style={{ fontSize: 18, fontWeight: 700 }}>{num(ps5.reliability.concordance_index, 3)}</div></div>
                      <div><div style={{ fontSize: 11, color: 'var(--text-secondary, #475569)' }}>Gate</div><div style={{ fontSize: 14, fontWeight: 700, color: ps5.reliability.dashboard_ready ? '#22c55e' : '#ef4444' }}>{ps5.reliability.dashboard_ready ? 'OPEN' : 'CLOSED'}</div></div>
                    </div>
                    {ps5.reliability.blockers && <div style={{ fontSize: 10, color: '#f59e0b', marginTop: 6 }}>Blockers: {ps5.reliability.blockers}</div>}
                  </div>
                ) : <div style={{ fontSize: 12, color: 'var(--text-secondary, #475569)' }}>No PS5 record for this device or category.</div>}
                <div style={{ fontSize: 11, fontWeight: 600, color: 'var(--text-secondary, #475569)', marginTop: 8 }}>{ps5.note}</div>
              </Section>
            </div>


            {/* BUS IDENTITY (29-Jul-2026). First thing in the modal: a crew is
                dispatched to a VEHICLE, not to a device id. */}
            {(() => {
              const b = (data || {}).bus_identity || {};
              return (
                <div style={{ display: 'flex', gap: 16, flexWrap: 'wrap', padding: '9px 12px',
                  background: '#f8fafc', border: '1px solid #e2e8f0', borderRadius: 8, marginBottom: 12 }}>
                  {[['BUS', b.bus_label || '-'], ['DEVICE', deviceId],
                    ['SERIAL', b.serial_number || '-'], ['COMPONENT', b.component_serial_nbr || '-'],
                    ['TYPE', b.component_type || '-'], ['GARAGE', b.facility_name || '-'],
                    ['OPERATOR', b.operator_name || '-']].map(([k, v]) => (
                    <div key={k}>
                      <div style={{ fontSize: 9.5, color: '#64748b', fontWeight: 700 }}>{k}</div>
                      <div style={{ fontSize: 12, color: k === 'BUS' ? '#0f172a' : '#334155',
                        fontWeight: k === 'BUS' ? 700 : 500, fontFamily: 'monospace' }}>{v}</div>
                    </div>))}
                  {b.on_vehicle === false && (
                    <div style={{ fontSize: 11, color: '#b45309', alignSelf: 'center' }}>
                      Not fitted to a vehicle - spare or bench unit.</div>)}
                </div>);
            })()}

            {/* PS3 v2 - ROOT CAUSE, INCIDENTS, COMPONENT RISK (29-Jul-2026) */}
            {(() => {
              const rc = (data || {}).ps3_v2_rootcause_360 || {};
              const a4 = (data || {}).ps4_v3_360 || {};
              const T = { padding: '4px 7px', fontSize: 11, borderBottom: '1px solid #f1f5f9' };
              const TH2 = { textAlign: 'left', padding: '4px 7px', fontSize: 10, fontWeight: 700,
                            color: '#475569', borderBottom: '1px solid #e2e8f0' };
              const n3 = (v, k = 3) => (v == null || Number.isNaN(Number(v))) ? '-' : Number(v).toFixed(k);
              return (
                <>
                  <Section title="PS3 · Root Cause (observed attribution)" level={rc.found ? 'device' : 'none'} tint="#a855f7">
                    <div style={{ fontSize: 11, color: '#64748b', marginBottom: 6 }}>{rc.basis}</div>
                    {(rc.rootcause || []).length > 0 ? (
                      <table className="data-table" style={{ fontSize: 11, width: '100%' }}>
                        <thead><tr><th style={TH2}>Component</th><th style={TH2}>Serial</th>
                          <th style={TH2}>Incidents</th><th style={TH2}>Critical rate</th>
                          <th style={TH2}>30d</th><th style={TH2}>Label check</th></tr></thead>
                        <tbody>{rc.rootcause.map((r, i) => (
                          <tr key={i}><td style={{ ...T, fontWeight: 600 }}>{r.component_label}</td>
                            <td style={{ ...T, fontFamily: 'monospace' }}>{r.serial_number || '-'}</td>
                            <td style={T}>{r.incident_count}</td><td style={T}>{n3(r.critical_rate)}</td>
                            <td style={T}>{r.recurrence_30d ?? '-'}</td>
                            <td style={{ ...T, color: '#64748b' }}>{r.taxonomy_note || '-'}</td></tr>))}
                        </tbody></table>
                    ) : <div style={{ fontSize: 12, color: '#64748b' }}>No component attribution for this device in the hardened run.</div>}
                    {(rc.concentration || []).map((c, i) => (
                      <div key={i} style={{ marginTop: 6, fontSize: 11, padding: '5px 8px',
                        background: String(c.concentration_verdict || '').startsWith('SINGLE') ? '#fef2f2' : '#f8fafc',
                        border: '1px solid #e2e8f0', borderRadius: 5 }}>
                        <strong>{c.serial_number || 'serial'}</strong> — {c.total_incidents} incidents across{' '}
                        {c.distinct_components} component(s), concentration {n3(c.concentration, 2)}.{' '}
                        {c.concentration_verdict}
                      </div>))}
                  </Section>

                  <Section title="PS3 · Incidents & severity queue" level={(rc.incidents || []).length ? 'device' : 'none'} tint="#c026d3">
                    {(rc.incidents || []).length > 0 ? (
                      <table className="data-table" style={{ fontSize: 11, width: '100%' }}>
                        <thead><tr><th style={TH2}>When</th><th style={TH2}>Predicted</th><th style={TH2}>Band</th>
                          <th style={TH2}>P(critical)</th><th style={TH2}>Confidence</th>
                          <th style={TH2}>Priority</th><th style={TH2}>Recurrence</th></tr></thead>
                        <tbody>{rc.incidents.map((r, i) => (
                          <tr key={i}><td style={T}>{String(r.source_event_timestamp || '').slice(0, 16)}</td>
                            <td style={{ ...T, fontWeight: 700 }}>{r.predicted_severity}</td>
                            <td style={T}>{r.action_band || '-'}</td>
                            <td style={T}>{n3(r.critical_probability)}</td>
                            <td style={T}>{r.confidence_band || '-'}</td>
                            <td style={{ ...T, fontWeight: 700 }}>{n3(r.action_priority_score, 2)}</td>
                            <td style={{ ...T, fontSize: 10 }}>{r.recurrence_note}</td></tr>))}
                        </tbody></table>
                    ) : <div style={{ fontSize: 12, color: '#64748b' }}>No incidents in the hardened-run queue for this device.</div>}
                    {(rc.drivers || []).length > 0 && (
                      <div style={{ marginTop: 8 }}>
                        <div style={{ fontSize: 11, fontWeight: 700, marginBottom: 4 }}>Why the model called it that</div>
                        {rc.drivers.map((r, i) => (
                          <div key={i} style={{ fontSize: 11, color: '#334155' }}>
                            <span style={{ fontFamily: 'monospace' }}>{r.feature}</span>
                            {r.feature_value != null ? ` = ${r.feature_value}` : ''}{' '}
                            <strong style={{ color: Number(r.shap_value) >= 0 ? '#b91c1c' : '#15803d' }}>
                              {Number(r.shap_value) >= 0 ? 'raises' : 'lowers'} severity ({n3(r.shap_value)})</strong>
                          </div>))}
                      </div>)}
                  </Section>

                  <Section title="PS3 · Component risk by serial" level={(rc.serial_risk || []).length ? 'serial' : 'none'} tint="#7c3aed">
                    {(rc.serial_risk || []).length > 0 ? (
                      <table className="data-table" style={{ fontSize: 11, width: '100%' }}>
                        <thead><tr><th style={TH2}>Serial</th><th style={TH2}>Incidents</th>
                          <th style={TH2}>Critical</th><th style={TH2}>Rate</th>
                          <th style={TH2}>Worst 30d</th><th style={TH2}>Latest</th></tr></thead>
                        <tbody>{rc.serial_risk.map((r, i) => (
                          <tr key={i}><td style={{ ...T, fontFamily: 'monospace', fontWeight: 600 }}>{r.serial_number || '-'}</td>
                            <td style={T}>{r.incident_count}</td><td style={T}>{r.critical_incidents}</td>
                            <td style={{ ...T, fontWeight: 700 }}>{n3(r.critical_rate)}</td>
                            <td style={T}>{r.max_prior_incidents_30d ?? '-'}</td>
                            <td style={T}>{String(r.latest_incident_at || '').slice(0, 16)}</td></tr>))}
                        </tbody></table>
                    ) : <div style={{ fontSize: 12, color: '#64748b' }}>No serial-level risk rows.</div>}
                  </Section>

                  <Section title="PS4 · Weekly anomaly & behaviour cluster" level={a4.found ? 'device' : 'none'} tint="#8b5cf6">
                    <div style={{ fontSize: 11, color: '#64748b', marginBottom: 6 }}>{a4.basis}</div>
                    {(a4.cluster || []).length > 0 && (
                      <div style={{ padding: '7px 9px', background: '#f4f2fa', border: '1px solid #a99bd1',
                        borderRadius: 6, marginBottom: 8, fontSize: 11.5 }}>
                        <strong>Cluster {a4.cluster[0].cluster_id}</strong> ({a4.cluster[0].device_type}) —
                        holds {n3(a4.cluster[0].train_cluster_share, 3)} of the training population,
                        actionable rate {n3(a4.cluster[0].actionable_rate, 4)}.{' '}
                        Separation {n3(a4.cluster[0].silhouette)} ({a4.cluster[0].quality_source}).{' '}
                        {Number(a4.cluster[0].silhouette) < 0.5
                          ? 'Below the strong line — rank on distance, not cluster membership.'
                          : 'Well separated — cluster membership is meaningful.'}
                      </div>)}
                    {(a4.persistent || []).length > 0 && (
                      <div style={{ padding: '6px 9px', background: '#fef2f2', border: '1px solid #fecaca',
                        borderRadius: 6, marginBottom: 8, fontSize: 11.5 }}>
                        <strong>Repeat offender</strong> — actionable in {a4.persistent[0].actionable_weeks} weeks
                        ({String(a4.persistent[0].first_week).slice(0, 10)} to {String(a4.persistent[0].last_week).slice(0, 10)}),
                        worst distance ratio {n3(a4.persistent[0].worst_distance_ratio)}.
                      </div>)}
                    {(a4.weeks || []).length > 0 ? (
                      <table className="data-table" style={{ fontSize: 11, width: '100%' }}>
                        <thead><tr><th style={TH2}>Week</th><th style={TH2}>Severity</th>
                          <th style={TH2}>Actionable</th><th style={TH2}>Distance ratio</th>
                          <th style={TH2}>Max score</th><th style={TH2}>Max |z|</th>
                          <th style={TH2}>Cluster</th><th style={TH2}>Signals</th><th style={TH2}>Coverage</th></tr></thead>
                        <tbody>{a4.weeks.map((r, i) => (
                          <tr key={i}><td style={T}>{String(r.week_start).slice(0, 10)}</td>
                            <td style={{ ...T, fontWeight: 700 }}>{r.severity || '-'}</td>
                            <td style={T}>{r.is_actionable_week === 1 ? `yes (${r.actionable_days}d)` : 'no'}</td>
                            <td style={{ ...T, fontWeight: 700,
                              color: Number(r.cluster_distance_ratio_max) > 1 ? '#b91c1c' : '#1e293b' }}>
                              {n3(r.cluster_distance_ratio_max)}</td>
                            <td style={T}>{n3(r.anomaly_score_max, 4)}</td>
                            <td style={T}>{n3(r.max_abs_z, 2)}</td>
                            <td style={T}>{r.dominant_cluster_id ?? '-'}</td>
                            <td style={{ ...T, fontSize: 10 }}>{r.anomaly_types || '-'}</td>
                            <td style={T}>{r.partial_week ? 'PARTIAL' : 'full'}</td></tr>))}
                        </tbody></table>
                    ) : <div style={{ fontSize: 12, color: '#64748b' }}>No PS4 v3 weekly rows for this device.</div>}
                  </Section>
                </>);
            })()}

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
              ) : <div style={{ fontSize: 13, color: 'var(--text-secondary, #475569)' }}>No PS4 anomaly alerts for this device. <span style={{ fontSize: 10 }}>{ps4.note}</span></div>}
            </Section>

            {/* ServiceNow (staged) */}
            <div className="card" style={{ borderLeft: '3px solid #ef4444' }}>
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                <div>
                  <div className="card-header" style={{ marginBottom: 2 }}>ServiceNow · Scheduled Maintenance</div>
                  <div style={{ fontSize: 11, color: 'var(--text-secondary, #475569)' }}>Stages an incident payload for review. No live post until the SN API is wired.</div>
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
