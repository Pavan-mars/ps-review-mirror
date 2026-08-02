// =====================================================================
// v2/AnalyseModal.jsx -- the Analyse Now panel, rebuilt.
//
// Everything here is read from ONE call, /ps1/device-360, whose response
// was inspected field by field before this file was written. No field is
// assumed. Sections whose `found` is false are hidden rather than shown
// empty, because a client reads five blank panels as a broken product.
//
// TEXT IS DELIBERATELY SPARSE. The API returns long `note` and `caveat`
// strings; those are collapsed behind "Why" and never shown by default.
// A number with a bar beside it says more than a paragraph.
//
// THE SERVICENOW BUTTON STAGES, IT DOES NOT POST. The route inserts into
// servicenow_staging and returns a note saying the live endpoint is not
// wired. The button says "Stage work order" and the confirmation repeats
// it, so nobody leaves the room believing a ticket was raised.
// =====================================================================
import React, { useEffect, useMemo, useState } from 'react';
import { Check, ExternalLink, Loader2, Send, X } from 'lucide-react';
import { CARD, CAT, INK, INK_2, INK_3, LINE, STATUS, deviceName, font, nfmt, pct, radius } from './theme';
import { Badge, Chip, Empty, Note } from './Kit';
import { ColumnBars, RankBars } from './Charts';
import { ps1 as api, servicenow } from './v2api';

const SECTIONS = [
  { key: 'risk', label: 'Risk' },
  { key: 'history', label: 'Service history' },
  { key: 'cascade', label: 'Cascade' },
  { key: 'anomaly', label: 'Anomaly' },
  { key: 'component', label: 'Components' },
  { key: 'action', label: 'Action' },
];

// A probability against its own decision threshold. The threshold differs
// per device type (0.164 gates, 0.028 TVMs, 0.458 validators), so a raw
// percentage without it is unreadable -- 45% is below the line for a
// validator and far above it for a TVM.
function ThresholdBar({ p, threshold, band }) {
  const v = Math.max(0, Math.min(1, Number(p) || 0));
  const t = Math.max(0, Math.min(1, Number(threshold) || 0));
  const tone = STATUS[String(band || '').toLowerCase()] || STATUS.neutral;
  return (
    <div>
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 8, marginBottom: 8 }}>
        <span style={{ fontSize: 30, fontWeight: 700, color: INK, ...font.num, letterSpacing: '-0.02em' }}>{pct(v)}</span>
        <Badge tone={String(band || '').toLowerCase() in STATUS ? String(band).toLowerCase() : 'neutral'}>{band || 'not scored'}</Badge>
      </div>
      <div style={{ position: 'relative', height: 10, background: '#F1F5F9', borderRadius: 6 }}>
        <div style={{ position: 'absolute', inset: 0, width: `${v * 100}%`, background: tone.fill, borderRadius: 6 }} />
        <div title={`Decision threshold ${pct(t, 2)}`}
             style={{ position: 'absolute', left: `${t * 100}%`, top: -4, width: 2, height: 18, background: INK, borderRadius: 1 }} />
      </div>
      <div style={{ display: 'flex', justifyContent: 'space-between', marginTop: 6, fontSize: 11, color: INK_3 }}>
        <span>0%</span>
        <span>Alerts above {pct(t, 2)}</span>
        <span>100%</span>
      </div>
    </div>
  );
}

function Row({ label, value, mono }) {
  if (value === null || value === undefined || value === '') return null;
  return (
    <div style={{ display: 'flex', justifyContent: 'space-between', gap: 14, padding: '5px 0', borderBottom: `1px solid #F5F8FB` }}>
      <span style={{ fontSize: 12, color: INK_3 }}>{label}</span>
      <span style={{ fontSize: 12.5, color: INK, fontWeight: 600, textAlign: 'right', ...(mono ? { fontFamily: 'ui-monospace,monospace' } : {}), ...font.num }}>
        {String(value)}
      </span>
    </div>
  );
}

function Block({ title, children, right }) {
  return (
    <div style={{ border: `1px solid ${LINE}`, borderRadius: 12, padding: '14px 16px', background: CARD }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 10 }}>
        <h4 style={{ ...font.h3, fontSize: 13.5, margin: 0 }}>{title}</h4>
        {right}
      </div>
      {children}
    </div>
  );
}

// Long API prose lives here and nowhere else.
function Why({ children }) {
  const [open, setOpen] = useState(false);
  if (!children) return null;
  return (
    <div style={{ marginTop: 10 }}>
      <button type="button" onClick={() => setOpen((o) => !o)}
              style={{ border: 'none', background: 'none', padding: 0, color: INK_3, fontSize: 11.5, fontWeight: 600, cursor: 'pointer' }}>
        {open ? 'Hide detail' : 'Why'}
      </button>
      {open && <p style={{ ...font.note, fontSize: 12, margin: '6px 0 0' }}>{children}</p>}
    </div>
  );
}

export default function AnalyseModal({ city = 'CHI', deviceId, onClose, onOpenDevice }) {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [active, setActive] = useState(SECTIONS.map((s) => s.key));
  const [staging, setStaging] = useState(false);
  const [staged, setStaged] = useState(null);
  const [payloadOpen, setPayloadOpen] = useState(false);

  useEffect(() => {
    let alive = true;
    setLoading(true); setStaged(null); setData(null);
    api.device360(city, deviceId).then((d) => { if (alive) { setData(d || {}); setLoading(false); } });
    return () => { alive = false; };
  }, [city, deviceId]);

  useEffect(() => {
    const h = (e) => { if (e.key === 'Escape') onClose && onClose(); };
    window.addEventListener('keydown', h);
    return () => window.removeEventListener('keydown', h);
  }, [onClose]);

  const d = data || {};
  const p1 = d.ps1 || {};
  const st = d.ps1_state || {};
  const p2 = d.ps2 || {};
  const p4v3 = d.ps4_v3_360 || {};
  const bus = d.bus_identity || {};
  const xp = d.cross_ps || {};
  const cause = d.causation || {};
  const rc = d.ps3_v2_rootcause_360 || {};
  const on = (k) => active.includes(k);
  const toggle = (k) => setActive((a) => (a.includes(k) ? a.filter((x) => x !== k) : [...a, k]));

  const weeks = useMemo(
    () => (p4v3.weeks || []).map((w) => ({
      week: String(w.week_start || '').slice(5),
      score: Number(w.anomaly_score_max) || 0,
      actionable: w.is_actionable_week ? 1 : 0,
      severity: w.severity,
    })),
    [p4v3.weeks]
  );

  const transitions = useMemo(
    () => (cause.transitions || []).map((t) => ({ name: t.to_sub, value: Number(t.prob) || 0 })),
    [cause.transitions]
  );

  const doStage = async () => {
    setStaging(true);
    const res = await servicenow.stage(
      city, d.device_id, p1.device_category || p2.category,
      (d.servicenow_payload && d.servicenow_payload.short_description) || `Predictive maintenance - ${d.device_id}`,
      d.servicenow_payload || {}
    );
    setStaging(false);
    setStaged(res);
  };

  return (
    <div
      onClick={(e) => { if (e.target === e.currentTarget && onClose) onClose(); }}
      style={{
        position: 'fixed', inset: 0, zIndex: 200, background: 'rgba(15,23,42,.42)',
        backdropFilter: 'blur(3px)', display: 'flex', alignItems: 'flex-start', justifyContent: 'center',
        padding: '4vh 16px', overflowY: 'auto',
      }}
    >
      <div style={{ width: 'min(1080px,100%)', background: '#FCFCFB', borderRadius: radius + 4, boxShadow: '0 30px 80px rgba(15,23,42,.30)', overflow: 'hidden' }}>

        {/* ---- header ---------------------------------------------- */}
        <div style={{ background: CARD, borderBottom: `1px solid ${LINE}`, padding: '16px 20px' }}>
          <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: 16 }}>
            <div style={{ minWidth: 0 }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
                <h2 style={{ ...font.h2, margin: 0, fontFamily: 'ui-monospace,monospace' }}>{deviceId}</h2>
                {p1.risk_band && <Badge tone={String(p1.risk_band).toLowerCase()}>{p1.risk_band} risk</Badge>}
                {st.device_state && <Badge tone={st.device_state === 'OUT_OF_SERVICE' ? 'critical' : 'good'}>{String(st.device_state).replace(/_/g, ' ').toLowerCase()}</Badge>}
              </div>
              <p style={{ ...font.note, margin: '6px 0 0' }}>
                {deviceName(p1.device_category || p2.category)}
                {p2.facility ? ` - ${p2.facility}` : ''}
                {bus.bus_id ? ` - Bus ${bus.bus_id}` : ''}
                {p2.operator ? ` - ${p2.operator}` : ''}
              </p>
            </div>
            <button type="button" onClick={onClose} aria-label="Close"
                    style={{ border: `1px solid ${LINE}`, background: CARD, borderRadius: 9, padding: 7, cursor: 'pointer', color: INK_2, lineHeight: 0 }}>
              <X size={16} />
            </button>
          </div>

          {/* in-modal filters: which sections to keep on screen */}
          <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginTop: 13 }}>
            {SECTIONS.map((s) => (
              <Chip key={s.key} active={on(s.key)} onClick={() => toggle(s.key)}>{s.label}</Chip>
            ))}
          </div>
        </div>

        {/* ---- body ------------------------------------------------ */}
        <div style={{ padding: 18 }}>
          {loading ? (
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 8, height: 240, color: INK_3, fontSize: 13 }}>
              <Loader2 size={16} className="spin" /> Loading device detail
            </div>
          ) : !d.device_id ? (
            <Empty height={180}>No record for this device in the current scoring run.</Empty>
          ) : (
            <div style={{ display: 'grid', gap: 14 }}>

              {/* recommendation: the API's own sentence, shown once, at the top */}
              {d.recommendation && (
                <div style={{ background: CARD, border: `1px solid ${LINE}`, borderLeft: `3px solid ${CAT[0]}`, borderRadius: 12, padding: '13px 16px' }}>
                  <div style={{ ...font.micro, marginBottom: 5 }}>Recommendation</div>
                  <p style={{ ...font.body, margin: 0 }}>{d.recommendation}</p>
                </div>
              )}

              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(310px,1fr))', gap: 14 }}>

                {on('risk') && p1.found && (
                  <Block title="Failure risk">
                    <ThresholdBar p={p1.failure_probability} threshold={p1.decision_threshold} band={p1.risk_band} />
                    <div style={{ marginTop: 12 }}>
                      <Row label="Scored on" value={p1.prediction_date} />
                      <Row label="Depot" value={p2.facility || p1.facility_id} />
                      <Row label="Signals agreeing" value={xp.signal_count} />
                    </div>
                    <Why>{p1.note}</Why>
                  </Block>
                )}

                {on('history') && (st.n_spells !== undefined || st.total_oos_days !== undefined) && (
                  <Block title="Service history">
                    <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10, marginBottom: 8 }}>
                      <div>
                        <div style={{ ...font.micro }}>Days out of service</div>
                        <div style={{ fontSize: 25, fontWeight: 700, color: INK, ...font.num }}>{nfmt(st.total_oos_days)}</div>
                      </div>
                      <div>
                        <div style={{ ...font.micro }}>Separate outages</div>
                        <div style={{ fontSize: 25, fontWeight: 700, color: INK, ...font.num }}>{nfmt(st.n_spells)}</div>
                      </div>
                    </div>
                    <Row label="Last scored" value={st.last_scored_day} />
                    <Row label="Days since last outage" value={st.days_since_spell_end} />
                    <Why>{st.state_note}</Why>
                  </Block>
                )}

                {on('cascade') && p2.in_catalog && (
                  <Block title="Fault cascade profile">
                    <Row label="Days with a cascade" value={nfmt(p2.cascade_days)} />
                    <Row label="Typical chain length" value={p2.avg_chain_len} />
                    <Row label="Longest chain" value={p2.max_chain_len} />
                    <Row label="Usual first subsystem" value={p2.dom_subsystem} mono />
                    <Row label="Most frequent fault code" value={p2.dom_error_code} mono />
                    {transitions.length > 0 && (
                      <div style={{ marginTop: 12 }}>
                        <div style={{ ...font.micro, marginBottom: 6 }}>What {cause.anchor_subsystem} leads to next</div>
                        <RankBars data={transitions} xKey="value" yKey="name" height={Math.max(90, transitions.length * 34)}
                                  color={CAT[2]} fmt={(v) => pct(v)} unit="Probability" />
                      </div>
                    )}
                    <Why>{cause.caveat}</Why>
                  </Block>
                )}

                {on('anomaly') && p4v3.found && (
                  <Block title="Behaviour vs its peers">
                    {weeks.length > 0 ? (
                      <ColumnBars data={weeks} xKey="week" height={150}
                                  series={[{ key: 'score', label: 'Weekly anomaly score', color: CAT[2] }]} />
                    ) : <Empty height={90}>No weekly anomaly rows.</Empty>}
                    {(p4v3.cluster || []).slice(0, 1).map((c, i) => (
                      <div key={i} style={{ marginTop: 10 }}>
                        <Row label="Peer group" value={`Cluster ${c.cluster_id}`} />
                        <Row label="Group separation" value={`${Number(c.silhouette).toFixed(3)} (${c.quality_source})`} />
                        <Row label="Distance from group centre" value={Number(c.mean_cluster_distance).toFixed(2)} />
                      </div>
                    ))}
                    {(p4v3.persistent || []).slice(0, 1).map((p, i) => (
                      <Row key={i} label="Weeks flagged" value={p.actionable_weeks} />
                    ))}
                    <Note>This measures how unlike its peers the device behaves. It is not a probability of failure.</Note>
                  </Block>
                )}

                {on('component') && (bus.component_serial_nbr || bus.bus_id) && (
                  <Block title="Physical identity">
                    <Row label="Bus" value={bus.bus_label || bus.bus_id || 'not assigned'} />
                    <Row label="Depot" value={bus.facility_name} />
                    <Row label="Operator" value={bus.operator_name} />
                    <Row label="Component type" value={bus.component_type} mono />
                    <Row label="Component serial" value={bus.component_serial_nbr} mono />
                  </Block>
                )}

                {on('component') && rc.found && (rc.rootcause || []).length > 0 && (
                  <Block title="Observed root cause">
                    <RankBars data={(rc.rootcause || []).slice(0, 8).map((r) => ({ name: r.root_cause || r.cause || 'unknown', value: Number(r.n_incidents || r.n || 0) }))}
                              xKey="value" yKey="name" height={180} color={CAT[1]} unit="Incidents" />
                    <Why>{rc.basis}</Why>
                  </Block>
                )}

                {on('risk') && (xp.signals || []).length > 0 && (
                  <Block title="Cross-check">
                    <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, marginBottom: 10 }}>
                      {(xp.signals || []).map((s, i) => <Chip key={i} active>{String(s)}</Chip>)}
                    </div>
                    <Row label="Subsystem agreement" value={String(xp.subsystem_verdict || '').replace(/_/g, ' ')} />
                    <Row label="Named subsystem" value={xp.ps2_subsystem} mono />
                    <Why>{xp.subsystem_detail}</Why>
                  </Block>
                )}
              </div>

              {/* ---- action ------------------------------------------ */}
              {on('action') && (
                <Block
                  title="Raise a work order"
                  right={
                    d.servicenow_payload ? (
                      <button type="button" onClick={() => setPayloadOpen((o) => !o)}
                              style={{ border: 'none', background: 'none', color: INK_3, fontSize: 11.5, fontWeight: 600, cursor: 'pointer' }}>
                        {payloadOpen ? 'Hide payload' : 'View payload'}
                      </button>
                    ) : null
                  }
                >
                  {payloadOpen && (
                    <pre style={{ background: '#F8FAFC', border: `1px solid ${LINE}`, borderRadius: 9, padding: 12, fontSize: 11, overflowX: 'auto', margin: '0 0 12px', maxHeight: 220 }}>
                      {JSON.stringify(d.servicenow_payload || {}, null, 2)}
                    </pre>
                  )}
                  <div style={{ display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
                    <button
                      type="button"
                      onClick={doStage}
                      disabled={staging || !d.servicenow_payload}
                      style={{
                        display: 'inline-flex', alignItems: 'center', gap: 7, border: 'none',
                        background: staging ? INK_3 : INK, color: '#FFFFFF', borderRadius: 10,
                        padding: '10px 16px', fontSize: 13, fontWeight: 700,
                        cursor: staging || !d.servicenow_payload ? 'default' : 'pointer',
                      }}
                    >
                      {staging ? <Loader2 size={15} /> : <Send size={15} />}
                      {staging ? 'Staging' : 'Stage work order'}
                    </button>
                    {staged && staged.ok && (
                      <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6, color: STATUS.good.fill, fontSize: 12.5, fontWeight: 600 }}>
                        <Check size={15} /> Staged as {String(staged.data.staged_id).slice(0, 8)}
                      </span>
                    )}
                    {staged && !staged.ok && (
                      <span style={{ color: STATUS.critical.fill, fontSize: 12.5, fontWeight: 600 }}>{staged.error}</span>
                    )}
                    {onOpenDevice && (
                      <button type="button" onClick={() => onOpenDevice(deviceId)}
                              style={{ marginLeft: 'auto', display: 'inline-flex', alignItems: 'center', gap: 6, border: `1px solid ${LINE}`, background: CARD, color: INK_2, borderRadius: 10, padding: '9px 13px', fontSize: 12.5, fontWeight: 600, cursor: 'pointer' }}>
                        <ExternalLink size={14} /> Open in fleet view
                      </button>
                    )}
                  </div>
                  <Note>
                    This stages the work order in MARS for review. It does not create the ServiceNow ticket -- the live
                    endpoint is not connected yet.
                  </Note>
                </Block>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
