// =====================================================================
// v2/DeviceBrief.jsx -- the one-screen answer for a single device.
//                                                          04-Aug-2026
// WHERE / WHEN / WHAT, then one recommendation. Nothing else.
//
// Everything here comes from the /ps1/device-360 payload the page has
// already fetched, so opening this costs no extra call.
//
// It deliberately does NOT show model internals, thresholds as raw
// numbers, table names, or the cross-analysis caveats. Those live on the
// full Device 360 page behind it. This is the version you can put in
// front of someone who has thirty seconds.
// =====================================================================
import React from 'react';
import { CARD, INK, INK_2, INK_3, LINE, STATUS, deviceShort, dfmt, font, nfmt, pct } from './theme';

const num = (v) => (v === null || v === undefined || Number.isNaN(Number(v)) ? null : Number(v));

// Three days on from the last scoring day. The label this model is trained
// on asks "does this device go out of service in the next three days", so
// the window IS the horizon -- it is not a guess layered on top of one.
function windowFrom(day) {
  if (!day) return null;
  const d = new Date(`${day}T00:00:00Z`);
  if (Number.isNaN(d.getTime())) return null;
  const a = new Date(d); a.setUTCDate(a.getUTCDate() + 1);
  const b = new Date(d); b.setUTCDate(b.getUTCDate() + 3);
  const f = (x) => x.toISOString().slice(0, 10);
  return { from: f(a), to: f(b) };
}

function Card({ eyebrow, title, icon, accent, children }) {
  return (
    <div style={{ background: CARD, border: `1px solid ${LINE}`, borderRadius: 14, padding: '16px 18px' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 12 }}>
        <span style={{ width: 34, height: 34, borderRadius: 9, background: `${accent}14`,
                       display: 'inline-flex', alignItems: 'center', justifyContent: 'center', fontSize: 17 }}>{icon}</span>
        <div>
          <div style={{ ...font.micro, color: INK_3, letterSpacing: '.08em' }}>{eyebrow}</div>
          <div style={{ fontSize: 17, fontWeight: 700, color: INK, lineHeight: 1.25 }}>{title}</div>
        </div>
      </div>
      {children}
    </div>
  );
}

function Row({ k, v, tone }) {
  if (v === null || v === undefined || v === '') return null;
  return (
    <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12, padding: '7px 0',
                  borderTop: `1px solid ${LINE}`, fontSize: 14 }}>
      <span style={{ color: INK_2 }}>{k}</span>
      <span style={{ color: tone || INK, fontWeight: 650, textAlign: 'right' }}>{v}</span>
    </div>
  );
}

export default function DeviceBrief({ data, onClose, onFull }) {
  if (!data) return null;

  const id = data.device_id;
  const bus = data.bus_identity || {};
  const st = data.ps1_state || {};
  const p1 = data.ps1 || {};
  const p5 = data.ps5 || {};

  const prob = num(st.ps1_fail_prob) ?? num(p1.failure_probability);
  const thr = num(st.threshold_used) ?? num(p1.decision_threshold);
  const tier = String(st.ps1_risk_tier || p1.risk_band || '').toUpperCase();
  const atRisk = prob !== null && thr !== null && prob >= thr;

  const fleet = deviceShort(st.device_type || p1.device_category) || 'Device';
  const place = bus.facility_name || (p1.facility_id ? `Facility ${p1.facility_id}` : null);
  const win = windowFrom(st.last_scored_day || p1.prediction_date);

  // The component that actually needs a hand: the most urgent one PS5 names.
  const comps = Array.isArray(p5.components) ? p5.components : [];
  const worst = comps
    .filter((c) => c && c.component_type_name)
    .sort((a, b) => (num(a.expected_component_rul_days) ?? 1e9) - (num(b.expected_component_rul_days) ?? 1e9))[0];
  const rul = worst ? num(worst.expected_component_rul_days) : null;

  // ONE recommendation, in the imperative, derived from what is actually
  // true of this device. No model vocabulary: a technician should be able to
  // act on it without asking what a threshold is.
  const rec = (() => {
    if (atRisk && worst) return { head: `Inspect the ${String(worst.component_type_name).replace(/_/g, ' ')}`, sub: 'This part is closest to the end of its expected life on a device already flagged for failure.' };
    if (atRisk) return { head: 'Schedule an inspection', sub: 'This device is above its alert line for the next three days.' };
    if (worst && (worst.act_now || worst.is_overdue)) return { head: `Plan a swap of the ${String(worst.component_type_name).replace(/_/g, ' ')}`, sub: 'The device is not flagged, but this part is past its expected life.' };
    if (String(st.device_state) === 'IN_SPELL') return { head: 'Already out of service', sub: 'Confirm the work order is open before raising another.' };
    return { head: 'No action needed now', sub: 'Below the alert line, with no part past its expected life.' };
  })();

  const band = atRisk ? STATUS.critical : (tier === 'MEDIUM' ? STATUS.warning : STATUS.good);

  return (
    <div
      role="dialog" aria-modal="true"
      onClick={onClose}
      style={{ position: 'fixed', inset: 0, zIndex: 1200, background: 'rgba(15,23,42,.46)',
               display: 'flex', alignItems: 'flex-start', justifyContent: 'center', padding: '38px 20px', overflowY: 'auto' }}
    >
      <div onClick={(e) => e.stopPropagation()}
           style={{ width: 'min(1080px,100%)', background: '#F7F8FB', borderRadius: 18, overflow: 'hidden',
                    boxShadow: '0 20px 60px rgba(15,23,42,.28)' }}>

        {/* header */}
        <div style={{ display: 'flex', alignItems: 'flex-start', gap: 14, padding: '20px 24px 16px', background: CARD }}>
          <button type="button" onClick={onClose} aria-label="Close"
                  style={{ border: 'none', background: 'transparent', fontSize: 22, lineHeight: 1, cursor: 'pointer', color: INK_2, marginTop: 2 }}>&larr;</button>
          <div style={{ flex: 1 }}>
            <div style={{ fontSize: 24, fontWeight: 800, color: INK, letterSpacing: '-.01em' }}>
              {bus.bus_label ? `Bus ${bus.bus_label}` : id}
            </div>
            <div style={{ fontSize: 14, color: INK_2, marginTop: 2 }}>
              {[place, bus.operator_name || fleet].filter(Boolean).join('  |  ')}
            </div>
          </div>
          {onFull && (
            <button type="button" onClick={onFull}
                    style={{ border: `1px solid ${LINE}`, background: '#FFF', color: INK, borderRadius: 10,
                             padding: '9px 15px', fontSize: 13.5, fontWeight: 650, cursor: 'pointer' }}>
              Full device record
            </button>
          )}
        </div>

        {/* risk banner */}
        <div style={{ margin: '0 24px', display: 'flex', alignItems: 'center', gap: 16,
                      background: `${band.fill}0F`, border: `1px solid ${band.fill}33`, borderRadius: 12, padding: '15px 18px' }}>
          <span style={{ width: 30, height: 30, borderRadius: '50%', background: band.fill, color: '#FFF',
                         display: 'inline-flex', alignItems: 'center', justifyContent: 'center', fontWeight: 800 }}>!</span>
          <div style={{ flex: 1 }}>
            <div style={{ fontSize: 17, fontWeight: 750, color: band.fill }}>
              {atRisk ? 'High failure risk' : tier === 'MEDIUM' ? 'Watch this device' : 'Low failure risk'}
            </div>
            <div style={{ fontSize: 14, color: INK_2, marginTop: 1 }}>
              {atRisk
                ? 'This device is expected to fail within the next 3 days.'
                : 'No failure expected in the next 3 days.'}
            </div>
          </div>
          {prob !== null && (
            <div style={{ textAlign: 'right' }}>
              <div style={{ ...font.num, fontSize: 26, fontWeight: 800, color: band.fill }}>{pct(prob, 0)}</div>
              <div style={{ ...font.micro, color: INK_3 }}>Risk score</div>
            </div>
          )}
        </div>

        {/* three cards */}
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(290px,1fr))', gap: 14, padding: '16px 24px 4px' }}>
          <Card eyebrow="WHERE" title={place || 'Location not recorded'} icon="📍" accent="#4F46E5">
            <Row k="Depot" v={place} />
            <Row k="Device" v={id} />
            <Row k="Fleet" v={fleet} />
            <Row k="Operator" v={bus.operator_name} />
          </Card>

          <Card eyebrow="WHEN" title={atRisk ? 'In the next 3 days' : 'No window open'} icon="📅" accent="#4F46E5">
            {win && atRisk && (
              <div style={{ background: '#EEF0FB', borderRadius: 10, padding: '11px 13px', marginBottom: 8 }}>
                <div style={{ ...font.micro, color: INK_3 }}>Expected failure window</div>
                <div style={{ fontSize: 16, fontWeight: 750, color: INK }}>{dfmt(win.from)} &ndash; {dfmt(win.to)}</div>
              </div>
            )}
            <Row k="Last scored" v={dfmt(st.last_scored_day || p1.prediction_date)} />
            <Row k="Current state" v={String(st.device_state || '').replace(/_/g, ' ').toLowerCase() || null} />
            <Row k="Times out of service" v={st.n_spells !== null && st.n_spells !== undefined ? nfmt(st.n_spells) : null} />
            <Row k="Days since last outage" v={st.days_since_spell_end !== null && st.days_since_spell_end !== undefined ? nfmt(st.days_since_spell_end) : null} />
          </Card>

          <Card eyebrow="WHAT" title="Likely issue" icon="🔧" accent="#4F46E5">
            {worst ? (
              <>
                <div style={{ background: `${band.fill}0F`, borderRadius: 10, padding: '11px 13px', marginBottom: 10 }}>
                  <div style={{ fontSize: 15, fontWeight: 750, color: INK }}>
                    {String(worst.component_type_name).replace(/_/g, ' ')}
                  </div>
                  <div style={{ fontSize: 13.5, color: INK_2 }}>
                    {rul !== null ? `About ${nfmt(Math.max(0, Math.round(rul)))} days of expected life left` : 'Flagged by remaining-life analysis'}
                  </div>
                </div>
                <div style={{ fontSize: 13.5, fontWeight: 700, color: INK }}>What this means</div>
                <div style={{ fontSize: 13.5, color: INK_2, marginTop: 2, lineHeight: 1.5 }}>
                  This part is the closest to the end of its expected life on this device.
                </div>
              </>
            ) : (
              <div style={{ fontSize: 13.5, color: INK_2, lineHeight: 1.5 }}>
                No single part stands out on this device. The risk score reflects its recent
                out-of-service history rather than one component.
              </div>
            )}
          </Card>
        </div>

        {/* one recommendation */}
        <div style={{ padding: '10px 24px 4px' }}>
          <div style={{ background: '#FFF8E7', border: '1px solid #F3DFA8', borderRadius: 12, padding: '14px 17px' }}>
            <div style={{ ...font.micro, color: INK_3, letterSpacing: '.08em' }}>RECOMMENDED ACTION</div>
            <div style={{ fontSize: 16, fontWeight: 750, color: INK, marginTop: 3 }}>{rec.head}</div>
            <div style={{ fontSize: 13.5, color: INK_2, marginTop: 2, lineHeight: 1.5 }}>{rec.sub}</div>
          </div>
        </div>

        {/* take action -- STAGES a ticket, never raises one. The label says so. */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 14, margin: '14px 24px 24px',
                      background: '#EEF0FB', borderRadius: 12, padding: '16px 18px' }}>
          <div style={{ flex: 1 }}>
            <div style={{ fontSize: 15.5, fontWeight: 750, color: INK }}>Take action</div>
            <div style={{ fontSize: 13.5, color: INK_2 }}>Staged for review - nothing is sent to ServiceNow from this screen.</div>
          </div>
          {onFull && (
            <button type="button" onClick={onFull}
                    style={{ border: 'none', background: '#3730A3', color: '#FFF', borderRadius: 10,
                             padding: '11px 18px', fontSize: 14, fontWeight: 700, cursor: 'pointer' }}>
              Stage a work order
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
