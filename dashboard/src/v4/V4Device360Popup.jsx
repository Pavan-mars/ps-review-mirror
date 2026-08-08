// =====================================================================
// v4/V4Device360Popup.jsx -- Device 360, built to the PS1 Risk mockup.
//
// Layout is the mockup's: title block, risk banner with the score gauge,
// a row of eyebrow-and-headline cards, then the Take Action bar.
//
// TEXT IS MINIMAL BY RULE. Label plus value. The mockup carries exactly
// one sentence in the banner and one in the action bar; so does this.
// Two short caveats survive because they change what the reader may
// conclude: the REVIEW-ONLY watch flag, and "observed, not confirmed"
// on the incident-attributed part.
//
// ONE call: /ps1/device-360. `causation` is fleet scope and is never
// rendered on a device panel.
// =====================================================================
import React, { useEffect, useMemo, useState } from 'react';
import {
  ArrowLeft, Bell, Calendar, CalendarDays, ChevronRight, Clock,
  Loader2, MapPin, Send, ShieldCheck, TrendingUp, Wrench, X,
} from 'lucide-react';
import { ACTION, CARD, INK, INK_2, INK_3, LINE, PAGE, deviceShort, font, nfmt, pct } from './V4theme';
import { ps1 as api, servicenow } from './V4api';
import { useLocations } from './V4Locations';
import { evidenceLines, componentsOf, focusOf } from './V4Evidence';

// --- speed: cache + in-flight dedupe + hover prefetch -----------------
const CACHE = new Map();
const INFLIGHT = new Map();
const ckey = (c, d) => `${c}|${d}`;

export function primeDevice360(city, deviceId) {
  if (!deviceId) return Promise.resolve(null);
  const k = ckey(city, deviceId);
  if (CACHE.has(k)) return Promise.resolve(CACHE.get(k));
  if (INFLIGHT.has(k)) return INFLIGHT.get(k);
  const p = api.device360(city, deviceId)
    .then((r) => {
      const v = r || {};
      if (CACHE.size >= 40) CACHE.delete(CACHE.keys().next().value);
      CACHE.set(k, v); INFLIGHT.delete(k); return v;
    })
    .catch(() => { INFLIGHT.delete(k); return null; });
  INFLIGHT.set(k, p);
  return p;
}

// Mockup palette: indigo accent, tinted callouts, near-black headings.
const IND = '#4338CA';
const IND_BG = '#EEF2FF';
const RED = '#DC2626';
const RED_BG = '#FEF2F2';
const AMB_BG = '#FFFBEB';
const AMB = '#B45309';

const DRIVER = {
  usage_cumulative_failure_count: 'Lifetime failures',
  roll_fail_90d: 'Failures, 90 days',
  roll_fail_30d: 'Failures, 30 days',
  hardware_oos_count_prior_sum_7d: 'Out of service, 7 days',
  days_since_hw_oos: 'Since last outage',
  current_healthy_age_days: 'Days fault-free',
  n_prior_oos: 'Prior outages',
  component_age_days: 'Part age',
  cascade_days: 'Cascade days',
};
const dlabel = (f) => DRIVER[f] || String(f || '').replace(/_/g, ' ');
const has = (v) => v !== null && v !== undefined && v !== '';

const tone = (t) => {
  const k = String(t || '').toLowerCase();
  if (k === 'critical') return { fill: RED, bg: RED_BG, word: 'High' };
  if (k === 'high' || k === 'serious') return { fill: '#EA580C', bg: '#FFF7ED', word: 'High' };
  if (k === 'medium' || k === 'warning') return { fill: '#B45309', bg: AMB_BG, word: 'Medium' };
  if (k === 'low' || k === 'good') return { fill: '#047857', bg: '#ECFDF5', word: 'Low' };
  return { fill: INK_3, bg: '#F9FAFB', word: 'Unscored' };
};

// Mockup's arc gauge, right of the risk score.
function Gauge({ v, c }) {
  const p = Math.max(0, Math.min(1, Number(v) || 0));
  const R = 22;
  const len = Math.PI * R;
  return (
    <svg width="62" height="40" viewBox="0 0 62 40" aria-hidden="true">
      <path d={`M 9 32 A ${R} ${R} 0 0 1 53 32`} fill="none" stroke={`${c}22`} strokeWidth="7" strokeLinecap="round" />
      <path d={`M 9 32 A ${R} ${R} 0 0 1 53 32`} fill="none" stroke={c} strokeWidth="7" strokeLinecap="round"
            strokeDasharray={`${len * p} ${len}`} />
      <text x="31" y="30" textAnchor="middle" fontSize="15" fontWeight="800" fill={c}>!</text>
    </svg>
  );
}

// --- card primitives, matched to the mockup --------------------------
function IconTile({ children, bg = IND_BG, color = IND }) {
  return (
    <span style={{
      width: 32, height: 32, borderRadius: 9, background: bg, color,
      display: 'inline-flex', alignItems: 'center', justifyContent: 'center', flex: '0 0 auto',
    }}>{children}</span>
  );
}

function Card({ icon, eyebrow, headline, children, link, onLink }) {
  return (
    <section style={{
      background: CARD, border: `1px solid ${LINE}`, borderRadius: 14,
      padding: '14px 15px 12px', display: 'flex', flexDirection: 'column',
      boxShadow: '0 1px 2px rgba(17,24,39,.04)',
    }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 9 }}>
        <IconTile>{icon}</IconTile>
        <div style={{ minWidth: 0 }}>
          <div style={{ fontSize: 10.5, fontWeight: 700, color: INK_3, letterSpacing: '0.1em' }}>{eyebrow}</div>
          <div style={{ fontSize: 15.5, fontWeight: 750, color: INK, letterSpacing: '-0.01em', lineHeight: 1.25 }}>
            {headline}
          </div>
        </div>
      </div>
      <div style={{ flex: 1 }}>{children}</div>
      {link && (
        <button type="button" onClick={onLink}
          style={{
            marginTop: 11, border: 'none', background: 'none', padding: 0, cursor: 'pointer',
            display: 'inline-flex', alignItems: 'center', gap: 4,
            color: IND, fontSize: 11.5, fontWeight: 700,
          }}>
          {link} <ChevronRight size={13} />
        </button>
      )}
    </section>
  );
}

function Row({ icon, k, v, c }) {
  if (!has(v)) return null;
  return (
    <div style={{
      display: 'flex', alignItems: 'center', gap: 8, padding: '7px 0',
      borderBottom: `1px solid #F3F4F6`,
    }}>
      {icon && <span style={{ color: INK_3, lineHeight: 0 }}>{icon}</span>}
      <span style={{ fontSize: 11.8, color: INK_2 }}>{k}</span>
      <span style={{ marginLeft: 'auto', fontSize: 12, fontWeight: 700, color: c || INK, ...font.num }}>{String(v)}</span>
    </div>
  );
}

// The mockup's tinted callout: icon, small label, bold value.
function Callout({ icon, label, value, bg = IND_BG, color = INK, sub }) {
  return (
    <div style={{ background: bg, borderRadius: 10, padding: '10px 12px', display: 'flex', alignItems: 'center', gap: 10 }}>
      {icon && <span style={{ color, lineHeight: 0, flex: '0 0 auto' }}>{icon}</span>}
      <div style={{ minWidth: 0 }}>
        {label && <div style={{ fontSize: 11, color: INK_2, marginBottom: 1 }}>{label}</div>}
        <div style={{ fontSize: 13.5, fontWeight: 750, color, lineHeight: 1.3 }}>{value}</div>
        {sub && <div style={{ fontSize: 11, color: INK_2, marginTop: 1 }}>{sub}</div>}
      </div>
    </div>
  );
}

function Bar({ label, w }) {
  return (
    <div style={{ marginBottom: 8 }}>
      <div style={{ fontSize: 11.5, color: INK_2, marginBottom: 3 }}>{label}</div>
      <div style={{ height: 6, background: '#F1F2F4', borderRadius: 3 }}>
        <div style={{ width: `${w}%`, height: '100%', background: IND, borderRadius: 3 }} />
      </div>
    </div>
  );
}

function Sk({ w = '100%', h = 9 }) {
  return <div style={{ width: w, height: h, marginBottom: 8, borderRadius: 3, background: '#F1F2F4', animation: 'v4p 1.1s ease-in-out infinite' }} />;
}

// last_scored_day + 1..3 -- the window the 3-day model actually covers.
const win = (day) => {
  if (!day) return null;
  const d = new Date(`${String(day).slice(0, 10)}T00:00:00Z`);
  if (Number.isNaN(d.getTime())) return null;
  const f = (n) => {
    const x = new Date(d.getTime() + n * 86400000);
    return x.toLocaleDateString('en-US', { month: 'short', day: 'numeric', timeZone: 'UTC' });
  };
  return `${f(1)} – ${f(3)}, ${d.getUTCFullYear()}`;
};

// =====================================================================
export default function Device360Popup({ city = 'CHI', deviceId, onClose, onOpenDevice, preloaded }) {
  const seed = (preloaded && preloaded.device_id === deviceId ? preloaded : null) || CACHE.get(ckey(city, deviceId)) || null;
  const [data, setData] = useState(seed);
  const [loading, setLoading] = useState(!seed);
  const [staging, setStaging] = useState(false);
  const [staged, setStaged] = useState(null);

  useEffect(() => {
    let alive = true;
    setStaged(null);
    const hit = (preloaded && preloaded.device_id === deviceId ? preloaded : null) || CACHE.get(ckey(city, deviceId));
    if (hit) { setData(hit); setLoading(false); return () => { alive = false; }; }
    setLoading(true); setData(null);
    primeDevice360(city, deviceId).then((r) => { if (alive) { setData(r || {}); setLoading(false); } });
    return () => { alive = false; };
  }, [city, deviceId, preloaded]);

  useEffect(() => {
    const h = (e) => { if (e.key === 'Escape' && onClose) onClose(); };
    window.addEventListener('keydown', h);
    return () => window.removeEventListener('keydown', h);
  }, [onClose]);

  const d = data || {};
  const p1 = d.ps1 || {};
  const st = d.ps1_state || {};
  const p2 = d.ps2 || {};
  const p5 = d.ps5 || {};
  const p4 = d.ps4_v3_360 || {};
  const rc = d.ps3_v2_rootcause_360 || {};
  const xp = d.cross_ps || {};

  const part = useMemo(() => {
    const r = Array.isArray(p5.components) ? p5.components : [];
    return r.length ? r.slice().sort((a, b) => Number(a.expected_component_rul_days ?? 1e9) - Number(b.expected_component_rul_days ?? 1e9))[0] : null;
  }, [p5.components]);

  const drivers = useMemo(() => {
    const r = Array.isArray(p1.drivers) ? p1.drivers : [];
    return r.filter((x) => x && has(x.feature_name)).slice(0, 3)
      .map((x) => ({ n: dlabel(x.feature_name), v: Math.abs(Number(x.shap_value) || 0) }));
  }, [p1.drivers]);
  const dmax = drivers.length ? Math.max(...drivers.map((x) => x.v)) : 1;

  const week = useMemo(() => {
    const w = Array.isArray(p4.weeks) ? p4.weeks : [];
    return w.length ? w[w.length - 1] : null;
  }, [p4.weeks]);

  const loc = useLocations(city);
  const cat = p1.device_category || p5.category || st.device_type;
  const T = tone(p1.risk_band);
  const prob = Number(p1.failure_probability) || 0;
  const watch = /REVIEW-ONLY/i.test(String(d.recommendation || ''));
  const depot = p2.facility || st.facility_id || p1.facility_id || p5.facility_id;
  // The place has a NAME. "Depot 108" is an id with a word in front of it and
  // means nothing to the person being dispatched; PS1's station dimension
  // calls it something. Falls back to the id when it is genuinely unmapped.
  const place = loc.known(depot) ? loc.name(depot) : (has(depot) ? `Depot ${depot}` : 'Not recorded');

  // ---- COMPONENTS, NOT COMPONENT TYPES.                  07-Aug-2026
  // TVMSBC and AV2_SAM are component TYPES -- a class of board. BHU and
  // CSC_READER are the actual components inside them, and they are what a
  // technician removes and replaces. The card led with the type, so it told
  // someone to "Inspect TVMSBC" when the evidence pointed at the BHU. The
  // attributed COMPONENT now leads and the type is context beneath it.
  const parts = useMemo(() => componentsOf(d), [d]);
  const focus = useMemo(() => focusOf(d), [d]);
  const lead = parts.length ? parts[0] : null;

  // ---- WHY, IN WORDS.                                     07-Aug-2026
  // cross_ps.signals arrives pre-formatted as "PS1 above threshold" /
  // "PS3 130 OOS incident(s)". Those are internal problem-statement labels: a
  // depot supervisor does not know what PS3 is. Rebuilt here from the SAME
  // structured fields the API used to compose them (ps1.predicted_label,
  // ps2.cascade_rank, ps3.n_incidents, ps4.alert_count), so the wording is
  // dynamic and no second source of truth is introduced. Falls back to the
  // raw signal strings if a field is missing.
  const why = useMemo(() => evidenceLines(d), [d]);
  const full = () => { if (onOpenDevice) onOpenDevice(deviceId); else { try { window.location.hash = `device:${deviceId}`; } catch (e) { /* non-fatal */ } if (onClose) onClose(); } };

  const doStage = async () => {
    setStaging(true);
    const r = await servicenow.stage(
      city, d.device_id, cat,
      (d.servicenow_payload && d.servicenow_payload.short_description) || `Predictive maintenance - ${d.device_id}`,
      d.servicenow_payload || {},
    );
    setStaging(false); setStaged(r);
  };

  return (
    <div
      onClick={(e) => { if (e.target === e.currentTarget && onClose) onClose(); }}
      style={{
        position: 'fixed', inset: 0, zIndex: 200, background: 'rgba(17,24,39,.42)',
        backdropFilter: 'blur(2px)', display: 'flex', alignItems: 'flex-start',
        justifyContent: 'center', padding: '5vh 18px 4vh', overflowY: 'auto',
      }}
    >
      <div style={{
        width: 'min(1060px,100%)', background: PAGE, borderRadius: 16,
        boxShadow: '0 28px 70px rgba(17,24,39,.30)', overflow: 'hidden',
      }}>
        <style>{'@keyframes v4p{0%,100%{opacity:1}50%{opacity:.45}}'}</style>

        {/* ---- title block ------------------------------------------ */}
        <div style={{ background: CARD, padding: '14px 20px 13px', display: 'flex', alignItems: 'flex-start', gap: 12 }}>
          <button type="button" onClick={onClose} aria-label="Back" title="Back"
            style={{ border: 'none', background: 'none', padding: 4, marginTop: 1, cursor: 'pointer', color: INK, lineHeight: 0 }}>
            <ArrowLeft size={18} />
          </button>
          <div style={{ minWidth: 0 }}>
            <div style={{ fontSize: 20, fontWeight: 780, color: INK, letterSpacing: '-0.02em', lineHeight: 1.15 }}>{deviceId}</div>
            <div style={{ fontSize: 12, color: INK_2, marginTop: 2 }}>
              {place}
              {loc.operator(depot) ? <span style={{ color: INK_3 }}> &middot; {loc.operator(depot)}</span> : null}
              <span style={{ color: '#D1D5DB' }}> | </span>{deviceShort(cat)}
            </div>
          </div>
          <button type="button" onClick={full}
            style={{
              marginLeft: 'auto', display: 'inline-flex', alignItems: 'center', gap: 7,
              border: `1px solid ${LINE}`, background: CARD, borderRadius: 10,
              padding: '8px 13px', fontSize: 12, fontWeight: 700, color: INK, cursor: 'pointer',
            }}>
            Full record
          </button>
          {/* BACK AND CLOSE ARE NOT THE SAME AFFORDANCE.      06-Aug-2026
              The arrow reads as "step back to where I came from"; a reader
              who opened this from a table wants an X to dismiss it. Both
              dismiss the overlay -- there is only one dismissal path -- but
              the X is the control people look for at the top right, and its
              absence is why this felt like a screen you were stuck on. */}
          <button type="button" onClick={onClose} aria-label="Close" title="Close (Esc)"
            style={{
              display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
              width: 32, height: 32, marginTop: 1,
              border: `1px solid ${LINE}`, background: CARD, borderRadius: 9,
              color: INK_2, cursor: 'pointer', lineHeight: 0,
            }}>
            <X size={16} />
          </button>
        </div>

        <div style={{ padding: '0 18px 18px' }}>
          {loading ? (
            <div style={{ display: 'grid', gap: 12, paddingTop: 12 }}>
              <div style={{ height: 74, borderRadius: 12, background: '#F1F2F4', animation: 'v4p 1.1s ease-in-out infinite' }} />
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3,1fr)', gap: 12 }}>
                {[0, 1, 2].map((i) => (
                  <div key={i} style={{ background: CARD, border: `1px solid ${LINE}`, borderRadius: 14, padding: '14px 15px' }}>
                    <Sk w="55%" h={15} /><Sk w="88%" /><Sk w="72%" /><Sk w="80%" /><Sk w="45%" />
                  </div>
                ))}
              </div>
            </div>
          ) : !d.device_id ? (
            <div style={{ height: 160, display: 'grid', placeItems: 'center', color: INK_3, fontSize: 12.5 }}>
              Not in the current run.
            </div>
          ) : (
            <div style={{ display: 'grid', gap: 12, paddingTop: 12 }}>

              {/* ---- risk banner ---------------------------------- */}
              <div style={{
                background: T.bg, border: `1px solid ${T.fill}22`, borderRadius: 12,
                padding: '13px 18px', display: 'flex', alignItems: 'center', gap: 14,
              }}>
                <span style={{
                  width: 30, height: 30, borderRadius: '50%', background: T.fill, color: '#FFF',
                  display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
                  fontSize: 17, fontWeight: 800, flex: '0 0 auto', lineHeight: 1,
                }}>!</span>
                <div style={{ minWidth: 0 }}>
                  <div style={{ fontSize: 15, fontWeight: 780, color: T.fill, letterSpacing: '-0.01em' }}>
                    {T.word} failure risk
                  </div>
                  <div style={{ fontSize: 12.5, color: INK_2, marginTop: 1 }}>
                    Expected to fail within the next 3 days.
                    {watch && <span style={{ color: AMB, fontWeight: 650 }}> Watch signal only.</span>}
                  </div>
                </div>
                <div style={{ marginLeft: 'auto', display: 'flex', alignItems: 'center', gap: 12 }}>
                  <div style={{ textAlign: 'right' }}>
                    <div style={{ ...font.num, fontSize: 27, fontWeight: 800, color: T.fill, lineHeight: 1 }}>{pct(prob, 0)}</div>
                    <div style={{ fontSize: 11, color: INK_2, marginTop: 2 }}>Risk score</div>
                  </div>
                  <Gauge v={prob} c={T.fill} />
                </div>
              </div>

              {/* ---- three cards ---------------------------------- */}
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3,1fr)', gap: 12 }}>

                {/* WHERE */}
                <Card
                  icon={<MapPin size={16} />}
                  eyebrow="WHERE"
                  headline={place}
                  link="Fleet view" onLink={full}
                >
                  <Row k="Location" v={has(depot) ? (loc.known(depot) ? `${loc.name(depot)} (${depot})` : `${depot} -- unmapped`) : null} />
                  <Row k="Operator" v={loc.operator(depot) || null} />
                  <Row k="Type" v={deviceShort(cat)} />
                  <Row k="Rank in fleet" v={has(p5.rul_rank_in_type) ? `${nfmt(p5.rul_rank_in_type)} of ${nfmt(p5.n_devices_in_type)}` : null} />
                  <Row k="Cascade rank" v={p2.in_top_devices && has(p2.cascade_rank) ? `#${nfmt(p2.cascade_rank)}` : null} />
                </Card>

                {/* WHEN */}
                <Card
                  icon={<Calendar size={16} />}
                  eyebrow="WHEN"
                  headline="In the next 3 days"
                  link="Service history" onLink={full}
                >
                  {win(st.last_scored_day || p1.prediction_date) && (
                    <Callout
                      icon={<CalendarDays size={17} />}
                      label="Expected failure window"
                      value={win(st.last_scored_day || p1.prediction_date)}
                    />
                  )}
                  <div style={{ marginTop: 9 }}>
                    <Row icon={<Clock size={13} />} k="Last scored" v={st.last_scored_day || p1.prediction_date} />
                    <Row icon={<TrendingUp size={13} />} k="Days to next OOS"
                         v={has(p5.rul_standard_days) ? `${nfmt(p5.rul_standard_days, 1)} days` : null}
                         c={p5.is_overdue ? RED : undefined} />
                    <Row icon={<CalendarDays size={13} />} k="Out of service"
                         v={has(st.current_spell_day) ? `day ${nfmt(st.current_spell_day)}` : null} c={RED} />
                    <Row icon={<CalendarDays size={13} />} k="Days since last outage"
                         v={!has(st.current_spell_day) && has(st.days_since_spell_end) ? nfmt(st.days_since_spell_end) : null} />
                  </div>
                </Card>

                {/* WHAT */}
                <Card
                  icon={<Wrench size={16} />}
                  eyebrow="WHAT"
                  headline="Likely issue"
                  link="Part details" onLink={full}
                >
                  {/* THE COMPONENT LEADS -- OR THE SUBSYSTEM.   07-Aug-2026
                      AV2_SAM and TVMSBC are component TYPES: a class of board.
                      BHU, CSC_READER and COMMS are what is actually observed
                      and what a technician is sent to. When no component is
                      attributed, the SUBSYSTEM a method points at is still
                      more specific than the type, so the type is the last
                      resort and is labelled as one when it appears. */}
                  {focus ? (
                    <Callout
                      bg={focus.kind === 'type' ? '#F9FAFB' : RED_BG}
                      color={focus.kind === 'type' ? INK_2 : RED}
                      icon={<Wrench size={17} />}
                      value={focus.name}
                      sub={focus.sub}
                    />
                  ) : (
                    <Callout bg="#F9FAFB" color={INK_2} value="No component identified" />
                  )}

                  <div style={{ marginTop: 9 }}>
                    {parts.slice(1).map((x) => (
                      <Row key={x.name} k={x.name} v={`${nfmt(x.n)} incident${x.n === 1 ? '' : 's'}`} />
                    ))}
                    {/* REMAINING LIFE BELONGS TO A SERIAL-NUMBERED COMPONENT.
                        It was printed against the component TYPE, which reads
                        as "every AV2_SAM has 2.5 days left". The survival run
                        scores one physical part; name that part. */}
                    {part && has(part.expected_component_rul_days) && (
                      <Row
                        k={part.component_serial_nbr ? `Component ${part.component_serial_nbr}` : 'Shortest-life component'}
                        v={`${nfmt(part.expected_component_rul_days, 1)} days left`}
                        c={RED}
                      />
                    )}
                    {part && part.component_type_name && (
                      <Row k="Its type" v={String(part.component_type_name).toUpperCase()} />
                    )}
                    {focus && focus.kind === 'component' && parts[0] && parts[0].serial && (
                      <Row k="Attributed serial" v={parts[0].serial} />
                    )}
                  </div>

                  <div style={{ marginTop: 10 }}>
                    <Callout
                      bg={AMB_BG} color={AMB}
                      icon={<ShieldCheck size={17} />}
                      value={focus ? `Inspect ${focus.kind === 'type' ? '' : 'the '}${focus.name}` : 'Inspect on next visit'}
                      sub={focus && focus.kind === 'component' && part && part.component_type_name
                        ? `Inside the ${String(part.component_type_name).toUpperCase()}. Schedule preventive maintenance.`
                        : 'Schedule preventive maintenance'}
                    />
                  </div>
                </Card>
              </div>

              {/* ---- why: one narrow strip, evidence only ---------- */}
              <div style={{
                background: CARD, border: `1px solid ${LINE}`, borderRadius: 14,
                padding: '13px 16px', display: 'grid', gridTemplateColumns: '150px 1fr', gap: 18, alignItems: 'center',
              }}>
                <div>
                  <div style={{ fontSize: 10.5, fontWeight: 700, color: INK_3, letterSpacing: '0.1em' }}>WHY</div>
                  <div style={{ fontSize: 15.5, fontWeight: 750, color: INK, letterSpacing: '-0.01em' }}>Evidence</div>
                </div>
                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(190px,1fr))', gap: 16, alignItems: 'start' }}>
                  <div>
                    {drivers.length > 0
                      ? drivers.map((x, i) => <Bar key={i} label={x.n} w={(x.v / dmax) * 100} />)
                      : (
                        <>
                          <Row k="Days out of service" v={has(st.total_oos_days) ? nfmt(st.total_oos_days) : null} />
                          <Row k="Separate outages" v={has(st.n_spells) ? nfmt(st.n_spells) : null} />
                        </>
                      )}
                  </div>
                  {/* SENTENCES, NOT PROBLEM-STATEMENT CODES. These were
                      chips reading "PS1 above threshold" and "PS3 130 OOS
                      incident(s)". A depot supervisor does not know what PS3
                      is, and the chip made them ask rather than act. Each line
                      is composed in `why` from the same structured fields the
                      API used, so the numbers stay live. */}
                  <div style={{ display: 'grid', gap: 6 }}>
                    {why.map((w, i) => (
                      <div key={i} style={{ display: 'flex', gap: 7, alignItems: 'flex-start' }}>
                        <span style={{
                          width: 6, height: 6, borderRadius: 3, marginTop: 6, flex: '0 0 auto',
                          background: w.tone === 'bad' ? RED : w.tone === 'warn' ? AMB
                                    : w.tone === 'ok' ? '#047857' : INK_3,
                        }} />
                        <span style={{
                          fontSize: 11.6, lineHeight: 1.45,
                          color: w.tone === 'flat' ? INK_3 : INK_2,
                        }}>{w.t}</span>
                      </div>
                    ))}
                    {!why.length && (
                      <span style={{ fontSize: 11.6, color: INK_3 }}>No cross-model signal for this device.</span>
                    )}
                  </div>
                </div>
              </div>

              {/* ---- take action ---------------------------------- */}
              <div style={{
                background: IND_BG, borderRadius: 12, padding: '13px 18px',
                display: 'flex', alignItems: 'center', gap: 14, flexWrap: 'wrap',
              }}>
                <span style={{ color: IND, lineHeight: 0 }}><Bell size={22} /></span>
                <div style={{ minWidth: 0 }}>
                  <div style={{ fontSize: 14.5, fontWeight: 780, color: IND, letterSpacing: '-0.01em' }}>Take action</div>
                  <div style={{ fontSize: 12, color: INK_2, marginTop: 1 }}>
                    Stage a work order for review. No ticket is raised.
                  </div>
                </div>
                <div style={{ marginLeft: 'auto', display: 'flex', alignItems: 'center', gap: 10 }}>
                  {staged && (
                    <span style={{ fontSize: 11.5, fontWeight: 650, color: staged.ok ? '#047857' : RED }}>
                      {staged.ok ? `Staged ${String(staged.data && staged.data.staged_id).slice(0, 8)}` : staged.error}
                    </span>
                  )}
                  <button type="button" onClick={full}
                    style={{
                      border: `1px solid ${IND}55`, background: CARD, color: IND, borderRadius: 10,
                      padding: '9px 15px', fontSize: 12.5, fontWeight: 700, cursor: 'pointer',
                    }}>
                    Full record
                  </button>
                  <button
                    type="button" onClick={doStage} disabled={staging || !d.servicenow_payload}
                    style={{
                      display: 'inline-flex', alignItems: 'center', gap: 7, border: 'none',
                      background: staging || !d.servicenow_payload ? INK_3 : ACTION, color: '#FFF',
                      borderRadius: 10, padding: '9px 16px', fontSize: 12.5, fontWeight: 700,
                      cursor: staging || !d.servicenow_payload ? 'default' : 'pointer',
                    }}>
                    {staging ? <Loader2 size={14} className="spin" /> : <Send size={14} />}
                    {staging ? 'Staging' : 'Create work order'}
                  </button>
                </div>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
