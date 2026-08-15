import { API_BASE_URL } from '../../runtimeConfig';
import React, { useState, useEffect, useContext, useMemo } from 'react';
import FilterContext from '../../context/FilterContext';
import {
  TCOL, TYPE_ORDER, TYPE_LABEL, RISK,
  num, intf, pct, hexA,
  Badge, Loading, ApiFailure, NoRows, Panel, Kpi, ExportButton,
} from '../shared/DashboardKit';

// ============================================================================
// PS1 · Device State, Drivers & Causation                         28-Jul-2026
//
// Reads ps1_cross_wired_daily -- 786,525 device x component x day rows loaded
// from the three PS1 notebook exports. Every number here is COUNTED from those
// rows. Nothing is generated, nothing is inherited from a training artefact.
//
// THE MODEL SCORECARD SUB-TAB IS NOT TOUCHED BY THIS FILE.
//
// WHY THIS SCREEN LEADS WITH FRAMING
// -----------------------------------
// The PS1 target will_hardware_oos_3d marks the DURATION of an out-of-service
// spell, not its arrival. Measured on the loaded rows:
//
//   continuation share of positive days   GATE 94.9%  TVM 98.2%  VALIDATOR 97.6%
//   real onset rate                       GATE 3.89%  TVM 1.65%  VALIDATOR 0.94%
//   stored label rate                     GATE 76.8%  TVM 91.4%  VALIDATOR 38.7%
//
// So a high score overwhelmingly means "this device is in, or just past, an
// out-of-service spell" -- frequently one that has already been resolved. The
// models stay trained that way on purpose: daily batch inference over Chicago
// runs against the models already built. That is a valid operating decision,
// but it changes what the number MEANS, and the screen has to say so before it
// shows a single probability.
//
// Hence the order here: framing, then current fleet state, then the action
// list, then why the model fires, and only then the metrics. A probability
// shown before that context reads as a failure forecast, which it is not.
//
// There is no accuracy figure anywhere in this file. At a 1-4% onset rate,
// predicting "no failure" everywhere scores 96-99% and catches nothing.
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

const TIER_ORDER = ['LOW', 'MEDIUM', 'HIGH', 'CRITICAL'];

const STATE_META = {
  IN_SPELL:  { label: 'Out of service now', color: '#dc2626', act: true,
               why: 'The device is inside an out-of-service window on its most recent scored day.' },
  NEW_ONSET: { label: 'Window just opened', color: '#f59e0b', act: true,
               why: 'The out-of-service window opened on the most recent scored day. This is the event itself.' },
  RECOVERED: { label: 'Back in service',    color: '#0ea5e9', act: false,
               why: 'Flagged earlier, but the last out-of-service window has ended. Often already repaired -- a CRITICAL badge here describes history, not risk.' },
  HEALTHY:   { label: 'No event in period', color: '#16a34a', act: false,
               why: 'No out-of-service window anywhere in the scored period.' },
};
const STATE_ORDER = ['IN_SPELL', 'NEW_ONSET', 'RECOVERED', 'HEALTHY'];

function liftVerdict(lift) {
  const L = Number(lift);
  if (!Number.isFinite(L)) return { cls: 'badge-medium', label: 'not measurable' };
  if (L < 1.2) return { cls: 'badge-critical', label: 'no discrimination' };
  if (L < 2)   return { cls: 'badge-high',     label: 'weak' };
  return { cls: 'badge-success', label: 'discriminating' };
}

function Bar({ value, max = 1, color, height = 10, title }) {
  const w = Math.max(0, Math.min(100, (Number(value) / (max || 1)) * 100));
  return (
    <div title={title} style={{ background: 'var(--border)', borderRadius: 4, height, width: '100%', overflow: 'hidden' }}>
      <div style={{ width: `${w}%`, height: '100%', background: color, borderRadius: 4 }} />
    </div>
  );
}

// 29-Jul-2026. Was PS1DriversCausation() with NO parameters while the body
// referenced `city`, so every request went out as city=undefined and the
// panel sat on 'Loading ... for undefined'. The parent renders inside a
// city dashboard and already holds the value; it is now passed in, with a
// CHI default so the component still works if mounted standalone.
export default function PS1DriversCausation({ city = 'CHI', onAnalyse }) {
  // FilterContext exposes selectedCities, not `city` -- this destructure always
  // produced undefined, which is why every request went out as city=undefined.
  // The prop wins; the context is only a fallback for a standalone mount.
  const _fc = useContext(FilterContext) || {};
  const cityId = city || _fc.city || (_fc.selectedCities && _fc.selectedCities[0]) || 'CHI';
  const [st, setSt] = useState({ state: 'loading' });

  useEffect(() => {
    let alive = true;
    setSt({ state: 'loading' });
    const g = (p, q) => apiGet(p, { city: cityId, ...(q || {}) }).catch(() => []);
    Promise.all([
      g('/ps1/xw-summary'), g('/ps1/xw-base-rate'), g('/ps1/xw-state-mix'),
      g('/ps1/xw-act-now', { top: 100 }), g('/ps1/xw-flag-reason'),
      g('/ps1/xw-performance'), g('/ps1/xw-performance-onset'),
      g('/ps1/xw-tiers'), g('/ps1/xw-drivers', { top: 8 }),
      g('/ps1/xw-causation'), g('/ps1/xw-chronic', { top: 20 }),
      g('/ps1/xw-facility', { top: 15 }),
    ]).then((a) => {
      if (!alive) return;
      const [summary, baseRate, stateMix, actNow, flagReason, perf, perfOnset,
             tiers, drivers, causation, chronic, facility] = a;
      setSt(a.some((x) => Array.isArray(x) && x.length)
        ? { state: 'ok', summary, baseRate, stateMix, actNow, flagReason, perf,
            perfOnset, tiers, drivers, causation, chronic, facility }
        : { state: 'empty' });
    }).catch((e) => { if (alive) setSt({ state: 'err', err: String(e.message || e) }); });
    return () => { alive = false; };
  }, [cityId]);

  if (st.state === 'loading') return <Loading what={`PS1 device state and drivers for ${cityId}`} />;
  if (st.state === 'err') return <ApiFailure what="PS1 cross-wired API" detail={st.err} />;
  if (st.state === 'empty') {
    return <NoRows what="ps1_cross_wired_daily is empty — run cubic-mars-ps1-xw-loader" />;
  }

  return (
    <div>
      {/* REORDERED (PK, 29-Jul-2026). Tier calibration, SHAP drivers and
          causation lead, because those three answer 'is this model worth
          believing and what is it reacting to'. Act-now moved below them:
          it is currently an empty list (nothing is mid-outage as of the
          2026-04-11 scoring date), and an empty panel at the top of a screen
          reads as a broken tab rather than as good news. */}
      <Framing baseRate={st.baseRate} summary={st.summary} />
      <TierCalibration tiers={st.tiers} />
      <Drivers drivers={st.drivers} />
      <Causation causation={st.causation} />
      <FleetState stateMix={st.stateMix} />
      <ActNow rows={st.actNow} />
      <FlagReason rows={st.flagReason} />
      <Performance perf={st.perf} perfOnset={st.perfOnset} />
      <Chronic rows={st.chronic} />
      <FacilityConcentration facility={st.facility} />
    </div>
  );
}

// ---------------------------------------------------------------- framing --
// FIRST THING ON THE SCREEN, ON PURPOSE. Every figure below is a probability,
// and a probability presented without this reads as a failure forecast.
function Framing({ baseRate, summary }) {
  const br = Array.isArray(baseRate) ? baseRate : [];
  const sm = Array.isArray(summary) ? summary : [];
  const total = sm.reduce((a, r) => a + (Number(r.n_rows) || 0), 0);
  const devices = sm.reduce((a, r) => a + (Number(r.n_device_ids) || 0), 0);

  return (
    <>
      <div className="card" style={{ marginBottom: 16, padding: 16,
        borderLeft: '4px solid var(--primary)', background: hexA('#6366f1', 0.04) }}>
        <div style={{ fontWeight: 800, fontSize: 15, marginBottom: 8 }}>
          What this model detects
        </div>
        <div style={{ fontSize: 13.5, lineHeight: 1.65, color: 'var(--text)' }}>
          PS1 is trained on <code>will_hardware_oos_3d</code>, which marks every day
          a device is <strong>out of service</strong> — not the day the fault begins.
          Measured on the loaded rows, <strong>95–98% of positive days simply follow
          another positive day</strong>. So a high score means
          {' '}<strong>“this device is in, or just past, an out-of-service spell”</strong> —
          often one that has already been resolved. It is a <strong>state detector</strong>,
          not a failure forecast.
          <br /><br />
          That is deliberate: daily batch inference runs against the models already
          built. It is stated here so the probabilities below are read for what they
          measure. The <em>Fleet state</em> panel separates devices that are down now
          from those already back in service.
        </div>
      </div>

      <Panel title="The real event rate"
        note="Onsets are transitions INTO an out-of-service window — the events a predictive model would have to catch. The stored label counts every day of the window, which is why the two differ by the factor shown."
        right={<ExportButton rows={br} name="ps1_xw_base_rate" />}>
        <div className="grid-3" style={{ marginBottom: 12 }}>
          <Kpi label="Scored device-days" value={intf(total)} color="var(--primary)" />
          <Kpi label="Devices" value={intf(devices)} sub="GATE · TVM · VALIDATOR" />
          <Kpi label="Onsets across fleet"
            value={intf(br.reduce((a, r) => a + (Number(r.onsets) || 0), 0))}
            sub="actual out-of-service events" />
        </div>
        <table className="data-table">
          <thead>
            <tr>
              <th>Device type</th><th className="num">Device-days</th>
              <th className="num">Days marked OOS</th><th className="num">Stored rate</th>
              <th className="num">Onsets</th><th className="num">Real event rate</th>
              <th className="num">Overstated by</th>
            </tr>
          </thead>
          <tbody>
            {br.map((r) => (
              <tr key={r.device_type}>
                <td><Badge cls="badge-info" style={{ background: hexA(TCOL[r.device_type] || '#64748b', 0.14) }}>
                  {TYPE_LABEL[r.device_type] || r.device_type}</Badge></td>
                <td className="num">{intf(r.device_days)}</td>
                <td className="num">{intf(r.stored_positive_days)}</td>
                <td className="num" style={{ color: 'var(--text-secondary)' }}>
                  {pct(Number(r.stored_base_rate))}
                </td>
                <td className="num">{intf(r.onsets)}</td>
                <td className="num" style={{ fontWeight: 800 }}>
                  {pct(Number(r.onset_rate_eligible))}
                </td>
                <td className="num" style={{ fontWeight: 700, color: 'var(--danger)' }}>
                  {num(Number(r.inflation_factor), 1)}×
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </Panel>
    </>
  );
}

// ------------------------------------------------------------ fleet state --
function FleetState({ stateMix }) {
  const rows = Array.isArray(stateMix) ? stateMix : [];

  // Hooks BEFORE any early return. React counts hook calls per render, so a
  // `return null` above a useMemo makes the count change the moment data
  // arrives -- "Rendered fewer hooks than expected".
  const byState = useMemo(() => {
    const m = {};
    rows.forEach((r) => { m[r.device_state] = (m[r.device_state] || 0) + Number(r.n_devices || 0); });
    return m;
  }, [rows]);

  // The headline. Of the devices sitting at CRITICAL right now, how many are
  // actually down? A CRITICAL count that is mostly RECOVERED is a description
  // of last week, not a work queue for today.
  const crit = rows.filter((r) => r.ps1_risk_tier === 'CRITICAL');
  const critTotal = crit.reduce((a, r) => a + Number(r.n_devices || 0), 0);
  const critDown = crit.filter((r) => STATE_META[r.device_state]?.act)
                       .reduce((a, r) => a + Number(r.n_devices || 0), 0);
  const critRecovered = crit.filter((r) => r.device_state === 'RECOVERED')
                            .reduce((a, r) => a + Number(r.n_devices || 0), 0);

  const byType = useMemo(() => {
    const m = {};
    rows.forEach((r) => {
      const t = (m[r.device_type] = m[r.device_type] || {});
      t[r.device_state] = (t[r.device_state] || 0) + Number(r.n_devices || 0);
    });
    return m;
  }, [rows]);

  if (!rows.length) return null;

  return (
    <Panel title="Fleet state on the latest scored day"
      note="Each device placed by its own most recent scored day. This is the panel that separates a live problem from a historical one."
      right={<ExportButton rows={rows} name="ps1_xw_state_mix" />}>

      <div className="grid-4" style={{ marginBottom: 14 }}>
        {STATE_ORDER.map((s) => (
          <Kpi key={s} label={STATE_META[s].label} value={intf(byState[s] || 0)}
            color={STATE_META[s].color} title={STATE_META[s].why}
            sub={STATE_META[s].act ? 'actionable' : 'no action'} />
        ))}
      </div>

      {critTotal > 0 && (
        <div style={{ marginBottom: 14, padding: 12, borderRadius: 8, fontSize: 13.5,
          background: hexA('#0ea5e9', 0.08), borderLeft: '4px solid #0ea5e9' }}>
          Of <strong>{intf(critTotal)}</strong> devices currently scored CRITICAL,
          {' '}<strong>{intf(critDown)}</strong> ({pct(critTotal ? critDown / critTotal : 0)})
          {' '}are out of service now or opened a window on the latest day, and
          {' '}<strong>{intf(critRecovered)}</strong> ({pct(critTotal ? critRecovered / critTotal : 0)})
          {' '}are already back in service. The second group is the model describing
          what happened, not what will.
        </div>
      )}

      <table className="data-table">
        <thead>
          <tr>
            <th>Device type</th>
            {STATE_ORDER.map((s) => <th key={s} className="num">{STATE_META[s].label}</th>)}
            <th style={{ minWidth: 180 }}>Mix</th>
          </tr>
        </thead>
        <tbody>
          {Object.entries(byType).map(([t, counts]) => {
            const tot = STATE_ORDER.reduce((a, s) => a + (counts[s] || 0), 0) || 1;
            return (
              <tr key={t}>
                <td><Badge cls="badge-info" style={{ background: hexA(TCOL[t] || '#64748b', 0.14) }}>
                  {TYPE_LABEL[t] || t}</Badge></td>
                {STATE_ORDER.map((s) => (
                  <td key={s} className="num"
                    style={STATE_META[s].act ? { fontWeight: 700 } : undefined}>
                    {intf(counts[s] || 0)}
                  </td>
                ))}
                <td>
                  {/* One stacked bar, one scale. Segments in fixed state order so
                      the colour always means the same thing across rows. */}
                  <div style={{ display: 'flex', height: 10, borderRadius: 4, overflow: 'hidden' }}>
                    {STATE_ORDER.map((s) => (counts[s] ? (
                      <div key={s} title={`${STATE_META[s].label}: ${intf(counts[s])}`}
                        style={{ width: `${(counts[s] / tot) * 100}%`,
                          background: STATE_META[s].color, marginRight: 2 }} />
                    ) : null))}
                  </div>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </Panel>
  );
}

// ---------------------------------------------------------------- act now --
function ActNow({ rows }) {
  const list = Array.isArray(rows) ? rows : [];
  return (
    <Panel title="Act now — devices out of service or newly opened"
      note="The only list that should drive dispatch. Devices already back in service are deliberately excluded, whatever their risk tier says."
      right={<ExportButton rows={list} name="ps1_xw_act_now" />}>
      {!list.length && <NoRows what="no device is out of service on its latest scored day" />}
      {list.length > 0 && (
        <table className="data-table">
          <thead>
            <tr>
              <th>Device</th><th>Type</th><th>Station</th><th>State</th>
              <th className="num">Day of spell</th><th className="num">Score</th>
              <th className="num">Threshold</th><th>Tier</th>
              <th className="num">Spells</th><th className="num">Total days out</th>
              <th>Last scored</th>
            </tr>
          </thead>
          <tbody>
            {list.map((r) => {
              const m = STATE_META[r.device_state] || {};
              return (
                <tr key={`${r.device_id}-${r.last_scored_day}`}>
                  <td style={{ fontWeight: 600 }}>{r.device_id}</td>
                  <td>{TYPE_LABEL[r.device_type] || r.device_type}</td>
                  <td>{r.facility_id || '—'}</td>
                  <td><Badge cls="badge-info" title={m.why}
                    style={{ background: hexA(m.color || '#64748b', 0.16),
                      color: m.color, fontWeight: 700 }}>{m.label || r.device_state}</Badge></td>
                  <td className="num">{r.current_spell_day || '—'}</td>
                  <td className="num">{num(Number(r.ps1_fail_prob), 3)}</td>
                  <td className="num" style={{ color: 'var(--text-secondary)' }}>
                    {num(Number(r.threshold_used), 3)}
                  </td>
                  <td><Badge cls="badge-info"
                    style={{ background: hexA(RISK[r.ps1_risk_tier] || '#64748b', 0.14) }}>
                    {r.ps1_risk_tier}</Badge></td>
                  <td className="num">{intf(r.n_spells)}</td>
                  <td className="num">{intf(r.total_oos_days)}</td>
                  <td style={{ fontSize: 12, color: 'var(--text-secondary)' }}>{r.last_scored_day}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}
    </Panel>
  );
}

// ------------------------------------------------------------ flag reason --
function FlagReason({ rows }) {
  const list = Array.isArray(rows) ? rows : [];
  if (!list.length) return null;
  return (
    <Panel title="Why the model is flagging"
      note="Every positive prediction split by what was true on that day. This is the evidence behind the framing at the top of the screen."
      right={<ExportButton rows={list} name="ps1_xw_flag_reason" />}>
      <table className="data-table">
        <thead>
          <tr>
            <th>Device type</th><th className="num">Flagged days</th>
            <th className="num">Fault began that day</th>
            <th className="num">Already out of service</th>
            <th className="num">No OOS in window</th>
            <th style={{ minWidth: 170 }}>Split</th>
            <th className="num">Driven by duration</th>
          </tr>
        </thead>
        <tbody>
          {list.map((r) => {
            const n = Number(r.n_flagged) || 1;
            return (
              <tr key={r.device_type}>
                <td><Badge cls="badge-info" style={{ background: hexA(TCOL[r.device_type] || '#64748b', 0.14) }}>
                  {TYPE_LABEL[r.device_type] || r.device_type}</Badge></td>
                <td className="num">{intf(r.n_flagged)}</td>
                <td className="num" style={{ fontWeight: 700, color: '#16a34a' }}>{intf(r.flag_on_onset)}</td>
                <td className="num" style={{ fontWeight: 700, color: '#dc2626' }}>{intf(r.flag_during_spell)}</td>
                <td className="num">{intf(r.flag_no_event)}</td>
                <td>
                  <div style={{ display: 'flex', height: 10, borderRadius: 4, overflow: 'hidden' }}>
                    <div title={`fault began: ${intf(r.flag_on_onset)}`}
                      style={{ width: `${(Number(r.flag_on_onset) / n) * 100}%`, background: '#16a34a', marginRight: 2 }} />
                    <div title={`already out of service: ${intf(r.flag_during_spell)}`}
                      style={{ width: `${(Number(r.flag_during_spell) / n) * 100}%`, background: '#dc2626', marginRight: 2 }} />
                    <div title={`no OOS: ${intf(r.flag_no_event)}`}
                      style={{ width: `${(Number(r.flag_no_event) / n) * 100}%`, background: '#94a3b8' }} />
                  </div>
                </td>
                <td className="num" style={{ fontWeight: 800 }}>{pct(Number(r.share_during_spell))}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
      <div style={{ marginTop: 10, fontSize: 11.5, color: 'var(--text-secondary)' }}>
        Green = the out-of-service window opened that day, the only slice where the score is
        anticipating anything. Red = the device was already down when it was scored.
      </div>
    </Panel>
  );
}

// ----------------------------------------------------------- performance ---
function Performance({ perf, perfOnset }) {
  const a = Array.isArray(perf) ? perf : [];
  const b = Array.isArray(perfOnset) ? perfOnset : [];
  if (!a.length && !b.length) return null;
  const byType = {};
  a.forEach((r) => { (byType[r.device_type] = byType[r.device_type] || {}).stored = r; });
  b.forEach((r) => { (byType[r.device_type] = byType[r.device_type] || {}).onset = r; });

  return (
    <Panel title="Measured performance, against both targets"
      note="Left: against the stored label (every day of an out-of-service window). Right: against onsets only, excluding days where the device was already down. Lift = precision ÷ base rate; 1.0 means no better than flagging at random."
      right={<ExportButton rows={[...a, ...b]} name="ps1_xw_performance_both" />}>
      <table className="data-table">
        <thead>
          <tr>
            <th rowSpan={2}>Device type</th>
            <th colSpan={4} style={{ textAlign: 'center', borderBottom: '1px solid var(--border)' }}>
              Stored label — “is it out of service”
            </th>
            <th colSpan={4} style={{ textAlign: 'center', borderBottom: '1px solid var(--border)' }}>
              Onset only — “did it start today”
            </th>
          </tr>
          <tr>
            <th className="num">Base</th><th className="num">Precision</th>
            <th className="num">Recall</th><th className="num">Lift</th>
            <th className="num">Base</th><th className="num">Precision</th>
            <th className="num">Recall</th><th className="num">Lift</th>
          </tr>
        </thead>
        <tbody>
          {Object.entries(byType).map(([t, v]) => {
            const s = v.stored || {}; const o = v.onset || {};
            const vs = liftVerdict(s.precision_lift); const vo = liftVerdict(o.precision_lift);
            return (
              <tr key={t}>
                <td><Badge cls="badge-info" style={{ background: hexA(TCOL[t] || '#64748b', 0.14) }}>
                  {TYPE_LABEL[t] || t}</Badge></td>
                <td className="num">{pct(Number(s.base_rate))}</td>
                <td className="num">{pct(Number(s.precision_at_threshold))}</td>
                <td className="num">{pct(Number(s.recall_at_threshold))}</td>
                <td className="num"><Badge cls={vs.cls} title={vs.label}>
                  {num(Number(s.precision_lift), 2)}×</Badge></td>
                <td className="num">{pct(Number(o.base_rate))}</td>
                <td className="num">{pct(Number(o.precision_at_threshold))}</td>
                <td className="num">{pct(Number(o.recall_at_threshold))}</td>
                <td className="num"><Badge cls={vo.cls} title={vo.label}>
                  {num(Number(o.precision_lift), 2)}×</Badge></td>
              </tr>
            );
          })}
        </tbody>
      </table>
      <div style={{ marginTop: 10, fontSize: 11.5, color: 'var(--text-secondary)' }}>
        The right-hand block is expected to be weaker: the models were trained on the
        stored label, so scoring them against onsets measures them on a task they were
        never given. That gap is how much of the left-hand block is the label rather
        than the model. No accuracy column — at a 1–4% onset rate, predicting “no
        failure” everywhere scores 96–99% and catches nothing.
      </div>
    </Panel>
  );
}

// ------------------------------------------------------- tier calibration --
function TierCalibration({ tiers }) {
  const rows = Array.isArray(tiers) ? tiers : [];
  const byType = useMemo(() => {
    const m = {};
    rows.forEach((r) => { (m[r.device_type] = m[r.device_type] || []).push(r); });
    Object.values(m).forEach((x) => x.sort(
      (p, q) => TIER_ORDER.indexOf(p.ps1_risk_tier) - TIER_ORDER.indexOf(q.ps1_risk_tier)));
    return m;
  }, [rows]);
  const mono = (arr) => arr.every((r, i) => i === 0
    || Number(r.positive_rate) >= Number(arr[i - 1].positive_rate));
  if (!rows.length) return null;

  return (
    <Panel title="Do the risk tiers mean anything?"
      note="Observed out-of-service rate per tier, counted from the label. A tier only helps if the observed rate climbs LOW → MEDIUM → HIGH → CRITICAL."
      right={<ExportButton rows={rows} name="ps1_xw_tier_calibration" />}>
      <div className="grid-3">
        {Object.entries(byType).map(([type, arr]) => (
          <div key={type} className="card" style={{ padding: 14 }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 10 }}>
              <span style={{ width: 10, height: 10, borderRadius: 3, background: TCOL[type] || '#64748b' }} />
              <strong style={{ fontSize: 13 }}>{TYPE_LABEL[type] || type}</strong>
              <Badge cls={mono(arr) ? 'badge-success' : 'badge-critical'}>
                {mono(arr) ? 'monotonic' : 'NOT monotonic'}
              </Badge>
            </div>
            {arr.map((r) => (
              <div key={r.ps1_risk_tier} style={{ marginBottom: 9 }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 11.5, marginBottom: 3 }}>
                  <span style={{ color: RISK[r.ps1_risk_tier] || 'var(--text)', fontWeight: 700 }}>
                    {r.ps1_risk_tier}</span>
                  <span style={{ color: 'var(--text-secondary)' }}>
                    {pct(Number(r.positive_rate))} observed · {intf(r.n_rows)} days</span>
                </div>
                <Bar value={r.positive_rate} color={RISK[r.ps1_risk_tier] || 'var(--primary)'} height={9} />
                <div style={{ marginTop: 2 }}>
                  <Bar value={r.mean_predicted_prob} color={hexA('#64748b', 0.55)} height={4}
                    title={`mean predicted ${pct(Number(r.mean_predicted_prob))}`} />
                </div>
              </div>
            ))}
          </div>
        ))}
      </div>
      <div style={{ marginTop: 8, fontSize: 11.5, color: 'var(--text-secondary)' }}>
        Thick bar = observed rate. Thin grey bar = mean predicted probability. Where they
        match, the tier is calibrated.
      </div>
    </Panel>
  );
}

// -------------------------------------------------------------- drivers ----
function Drivers({ drivers }) {
  const rows = Array.isArray(drivers) ? drivers : [];
  const byType = useMemo(() => {
    const m = {};
    rows.forEach((r) => { (m[r.device_type] = m[r.device_type] || []).push(r); });
    Object.values(m).forEach((a) => a.sort((x, y) => Number(x.importance_rank) - Number(y.importance_rank)));
    return m;
  }, [rows]);
  // Detected at render: if no stored SHAP value is ever negative, the export is
  // taking the top 3 by VALUE, not magnitude, so protective features can never
  // appear. Retitles itself the moment the notebook fix ships.
  const oneSided = rows.length > 0
    && rows.every((r) => Math.abs(Number(r.mean_abs_shap) - Number(r.mean_signed_shap)) < 1e-9);
  if (!rows.length) return null;

  return (
    <Panel title={oneSided ? 'Top contributors pushing toward out-of-service (SHAP)' : 'Feature importance (SHAP)'}
      note="Unpivoted from the top-3 SHAP slots on every scored row and ranked by mean |SHAP|. This is what drove the scored population, not a training-time list."
      right={<ExportButton rows={rows} name="ps1_shap_importance" />}>
      {oneSided && (
        <div style={{ marginBottom: 12, padding: 10, borderRadius: 8, fontSize: 12.5,
          background: hexA('#f59e0b', 0.09), borderLeft: '4px solid #f59e0b' }}>
          Every stored SHAP value is positive — the export keeps the top three by
          <em> value</em>, not magnitude, so features that push a device <em>away</em> from
          failure are never recorded. Read as “what pushed toward”, not a complete
          ranking. Fixed by ranking on <code>abs()</code> in the PS1 notebooks.
        </div>
      )}
      <div className="grid-3">
        {Object.entries(byType).map(([type, arr]) => {
          const max = Math.max(...arr.map((r) => Number(r.mean_abs_shap) || 0), 0.0001);
          return (
            <div key={type} className="card" style={{ padding: 14 }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 10 }}>
                <span style={{ width: 10, height: 10, borderRadius: 3, background: TCOL[type] || '#64748b' }} />
                <strong style={{ fontSize: 13 }}>{TYPE_LABEL[type] || type}</strong>
              </div>
              {arr.map((r) => (
                <div key={r.feature_name} style={{ marginBottom: 8 }}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8, fontSize: 11.5, marginBottom: 3 }}>
                    <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}
                      title={r.feature_name}>{r.feature_name}</span>
                    <span style={{ color: 'var(--text-secondary)', flexShrink: 0 }}>
                      {num(Number(r.mean_abs_shap), 3)}</span>
                  </div>
                  <Bar value={r.mean_abs_shap} max={max} color={TCOL[type] || 'var(--primary)'} height={8} />
                </div>
              ))}
            </div>
          );
        })}
      </div>
    </Panel>
  );
}

// ------------------------------------------------------------ causation ----
function Causation({ causation }) {
  const rows = Array.isArray(causation) ? causation : [];
  const missing = TYPE_ORDER.filter((t) => !rows.some((r) => r.device_type === t));
  return (
    <Panel title="Causation — does station contagion carry PS1 risk?"
      note="Lift of CRITICAL given a coordinated station failure versus without. Device types with fewer than 30 device-days on either arm are excluded rather than shown from a handful of days."
      right={<ExportButton rows={rows} name="ps1_xw_causation" />}>
      {!rows.length && <NoRows what="no device type has 30+ device-days on both arms" />}
      {rows.length > 0 && (
        <table className="data-table">
          <thead>
            <tr>
              <th>Device type</th><th className="num">Days in chain</th><th className="num">Days not in chain</th>
              <th className="num">CRITICAL in chain</th><th className="num">CRITICAL otherwise</th>
              <th className="num">Lift</th><th className="num">Devices per event</th><th>Read with care</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => {
              const suspect = Number(r.critical_lift) >= 5
                && Number(r.mean_days_healthy_before_chain) === 0;
              return (
                <tr key={r.device_type}>
                  <td><Badge cls="badge-info" style={{ background: hexA(TCOL[r.device_type] || '#64748b', 0.14) }}>
                    {TYPE_LABEL[r.device_type] || r.device_type}</Badge></td>
                  <td className="num">{intf(r.n_chain)}</td>
                  <td className="num">{intf(r.n_no_chain)}</td>
                  <td className="num">{pct(Number(r.critical_rate_in_chain))}</td>
                  <td className="num">{pct(Number(r.critical_rate_no_chain))}</td>
                  <td className="num" style={{ fontWeight: 800 }}>{num(Number(r.critical_lift), 2)}×</td>
                  <td className="num">{num(Number(r.mean_station_devices_failed), 1)}</td>
                  <td>{suspect
                    ? <Badge cls="badge-high" title="Zero healthy days before the chain means the chain flag and the risk tier move together on the same day — likely two views of one signal. Needs a leakage check before being presented as causation.">possible leakage</Badge>
                    : <span style={{ fontSize: 11.5, color: 'var(--text-secondary)' }}>association</span>}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}
      {missing.length > 0 && (
        <div style={{ marginTop: 10, fontSize: 12, color: 'var(--text-secondary)' }}>
          Not shown: {missing.map((t) => TYPE_LABEL[t] || t).join(', ')} — fewer than 30
          device-days on one arm. An absent row means “not enough evidence”, never “no effect”.
        </div>
      )}
    </Panel>
  );
}

// -------------------------------------------------------------- chronic ----
function Chronic({ rows }) {
  const list = Array.isArray(rows) ? rows : [];
  if (!list.length) return null;
  const max = Math.max(...list.map((r) => Number(r.total_days_out) || 0), 1);
  return (
    <Panel title="Chronic devices — longest total time out of service"
      note="Ranked by total days out, not by number of spells: one 40-day outage costs more than four 3-day ones. A device with long spells is a parts or dispatch problem, not a prediction problem."
      right={<ExportButton rows={list} name="ps1_xw_chronic_devices" />}>
      <table className="data-table">
        <thead>
          <tr>
            <th>Device</th><th>Type</th><th>Station</th>
            <th className="num">Spells</th><th style={{ minWidth: 150 }}>Total days out</th>
            <th className="num">Days</th><th className="num">Longest</th>
            <th className="num">Mean</th><th className="num">Isolated</th><th>Last ended</th>
          </tr>
        </thead>
        <tbody>
          {list.map((r) => (
            <tr key={r.device_id}>
              <td style={{ fontWeight: 600 }}>{r.device_id}</td>
              <td>{TYPE_LABEL[r.device_type] || r.device_type}</td>
              <td>{r.facility_id || '—'}</td>
              <td className="num">{intf(r.n_spells)}</td>
              <td><Bar value={r.total_days_out} max={max} color="var(--danger)" height={8} /></td>
              <td className="num" style={{ fontWeight: 700 }}>{intf(r.total_days_out)}</td>
              <td className="num">{intf(r.longest_spell)}</td>
              <td className="num">{num(Number(r.mean_spell_days), 1)}</td>
              <td className="num">{intf(r.n_isolated)}</td>
              <td style={{ fontSize: 12, color: 'var(--text-secondary)' }}>{r.last_spell_end}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </Panel>
  );
}

// -------------------------------------------------- station concentration --
function FacilityConcentration({ facility }) {
  const rows = Array.isArray(facility) ? facility : [];
  if (!rows.length) return null;
  const max = Math.max(...rows.map((r) => Number(r.n_critical) || 0), 1);
  return (
    <Panel title="Where the risk sits — station concentration"
      note="facility_id ships in the PS1 export, so this is a direct rollup with no join and no fan-out."
      right={<ExportButton rows={rows} name="ps1_xw_facility" />}>
      <table className="data-table">
        <thead>
          <tr>
            <th>Station</th><th className="num">Devices</th><th className="num">Device-days</th>
            <th style={{ minWidth: 150 }}>CRITICAL days</th><th className="num">CRITICAL</th>
            <th className="num">Mean score</th><th className="num">OOS within 3d</th>
            <th className="num">Chain days</th><th>Last day</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.facility_id}>
              <td style={{ fontWeight: 600 }}>{r.facility_id}</td>
              <td className="num">{intf(r.n_devices)}</td>
              <td className="num">{intf(r.n_device_days)}</td>
              <td><Bar value={r.n_critical} max={max} color="var(--danger)" height={8} /></td>
              <td className="num">{intf(r.n_critical)}</td>
              <td className="num">{num(Number(r.mean_fail_prob), 3)}</td>
              <td className="num">{intf(r.n_oos_3d)}</td>
              <td className="num">{intf(r.n_chain_days)}</td>
              <td style={{ fontSize: 12, color: 'var(--text-secondary)' }}>{r.last_day}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </Panel>
  );
}
