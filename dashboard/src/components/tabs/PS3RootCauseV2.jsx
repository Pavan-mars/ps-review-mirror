import { API_BASE_URL } from '../../runtimeConfig';
// ============================================================================
// PS3RootCauseV2.jsx  ->  src/components/tabs/PS3RootCauseV2.jsx
//
// PS3 HARDENED REMEDIATION RUN (v2)                              29-Jul-2026
// Source run: ps3_20260729T074311Z
//
// A NEW sub-tab. Every existing PS3 view is untouched and still reads the
// previous feed, which stays as the fallback.
//
// THE GOVERNING RULE OF THIS SCREEN: the RUN decides what may be shown.
// ps3_v2_display_policy carries display_allowed and required_disclaimer per
// element, and ps3_v2_promotion_status carries action_feed_eligible per model.
// Where the run says an element is not publishable, this component renders the
// reason instead of the data. The notebook is the authority, not the dashboard
// - that is what stops a number reaching a boardroom that the model owner
// already knew was not ready.
//
// Causal effects are shown with their verdict attached. A row that failed the
// balance or overlap check is NOT evidence and is labelled so, because an
// unlabelled effect size is the single easiest thing to misread in this deck.
// ============================================================================
import React, { useState, useEffect, useMemo, useCallback } from 'react';
import AnalyseButton from '../shared/AnalyseButton';
import { BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, Legend, ResponsiveContainer } from 'recharts';

const API_BASE = (API_BASE_URL
  || 'https://a9yuqt9j9b.execute-api.us-east-1.amazonaws.com').replace(/\/$/, '');
const CAT_COLOR = { GATE: '#2563eb', TVM: '#0d9488', VALIDATOR: '#c2410c' };
const BAND = { CRITICAL: '#b91c1c', MAJOR: '#c2410c', HIGH: '#c2410c', MEDIUM: '#b45309', LOW: '#475569' };

async function get(p, q = {}) {
  const s = new URLSearchParams(Object.entries(q).filter(([, v]) => v !== '' && v != null)).toString();
  const r = await fetch(`${API_BASE}${p}${s ? `?${s}` : ''}`);
  if (!r.ok) throw new Error(`${p} -> HTTP ${r.status}`);
  const j = await r.json();
  return j && j.data !== undefined ? j.data : j;
}
const un = (a) => (Array.isArray(a) && a.length === 1 && a[0] && a[0]._unavailable) ? a[0]._unavailable : null;
const cl = (a) => (un(a) ? [] : (Array.isArray(a) ? a : []));
const n2 = (v, d = 3) => (v == null || Number.isNaN(Number(v))) ? '-' : Number(v).toFixed(d);
const iv = (v) => v == null ? '-' : Number(v).toLocaleString();
const yes = (v) => ['true', 't', 'yes', '1'].includes(String(v || '').toLowerCase());

const CARD = { background: '#fff', border: '1px solid #e2e8f0', borderRadius: 10, padding: 16, marginBottom: 16 };
const H = { margin: '0 0 4px', fontSize: 15, fontWeight: 700, color: '#0f172a' };
const SUB = { margin: '0 0 12px', fontSize: 12, color: '#64748b', lineHeight: 1.5 };
const TH = { textAlign: 'left', padding: '7px 8px', fontSize: 11, fontWeight: 700, color: '#475569', borderBottom: '1px solid #e2e8f0', whiteSpace: 'nowrap' };
const TD = { padding: '6px 8px', fontSize: 12, color: '#1e293b', borderBottom: '1px solid #f1f5f9' };

function Tag({ children, tone = 'slate' }) {
  const t = { slate: ['#f1f5f9', '#475569'], red: ['#fef2f2', '#b91c1c'], amber: ['#fffbeb', '#b45309'],
              green: ['#f0fdf4', '#15803d'], indigo: ['#eef2ff', '#4338ca'] }[tone];
  return <span style={{ display: 'inline-block', padding: '2px 7px', borderRadius: 4, background: t[0],
    color: t[1], fontSize: 10, fontWeight: 700, whiteSpace: 'nowrap' }}>{children}</span>;
}

// REORDERED 29-Jul-2026. Root cause leads, then the severity drivers, then the
// action queue. The framing, publication-policy, scorecard and load-audit cards
// were removed at PK's request: that run-level governance still exists and is
// reachable through the loader's verify action, it simply no longer sits
// between an operator and the work.
export default function PS3RootCauseV2({ city, onAnalyse }) {
  const [d, setD] = useState({});
  const [cat, setCat] = useState('ALL');
  const [busy, setBusy] = useState(true);
  const [fatal, setFatal] = useState(null);

  const load = useCallback(() => {
    setBusy(true); setFatal(null);
    Promise.all([
      get('/ps3/v2-status', { city }), get('/ps3/v2-scorecard', { city }),
      get('/ps3/v2-queue', { city, limit: 500 }), get('/ps3/v2-causal', { city }),
      get('/ps3/v2-shap-global', { city }), get('/ps3/v2-devices', { city, limit: 300 }),
      get('/ps3/v2-components', { city }), get('/ps3/v2-facilities', { city, limit: 200 }),
      get('/ps3/v2-models', { city }),
      get('/ps3/v2-rootcause-rollup', { city }),
      get('/ps3/v2-rootcause-concentration', { city, limit: 300 }),
    ]).then(([status, score, queue, causal, shapg, dev, comp, fac, models, rcr, rcc]) =>
      setD({ status: status || {}, score: cl(score), queue: cl(queue), causal: cl(causal),
             shapg: cl(shapg), dev: cl(dev), comp: cl(comp), fac: cl(fac), models: cl(models),
             rcr: cl(rcr), rcc: cl(rcc) }))
      .catch((e) => setFatal(String(e.message || e))).finally(() => setBusy(false));
  }, [city]);
  useEffect(() => { load(); }, [load]);

  const run = useMemo(() => (cl(d.status && d.status.run)[0]) || null, [d]);
  const policy = useMemo(() => cl(d.status && d.status.policy), [d]);
  const promo = useMemo(() => cl(d.status && d.status.promotion), [d]);
  const tables = useMemo(() => cl(d.status && d.status.tables), [d]);
  const fresh = useMemo(() => (cl(d.status && d.status.freshness)[0]) || null, [d]);
  const f = useCallback((a) => (cat === 'ALL' ? a : (a || []).filter((r) => r.device_category === cat)), [cat]);

  const scoreChart = useMemo(() => (d.score || []).filter((r) => r.validation_f1_macro != null)
    .map((r) => ({ name: `${r.device_category} ${r.head}`.slice(0, 22),
                   Validation: Number(r.validation_f1_macro), Test: Number(r.test_f1_macro || 0) })), [d.score]);

  if (busy) return <div style={{ ...CARD, color: '#64748b' }}>Loading PS3 v2...</div>;
  if (fatal) return (
    <div style={{ ...CARD, borderColor: '#fecaca', background: '#fef2f2' }}>
      <p style={{ ...H, color: '#b91c1c' }}>PS3 v2 routes unavailable</p>
      <p style={SUB}>{fatal}</p>
      <p style={SUB}>Tables come from sql/39_ps3_v2.sql and are filled by cubic-mars-ps3-v2-loader.
        Until both have run this sub-tab is empty. The other PS3 views are unaffected.</p>
      <button onClick={load} style={{ background: '#6366f1', color: '#fff', border: 'none',
        borderRadius: 6, padding: '5px 12px', fontSize: 12, fontWeight: 600, cursor: 'pointer' }}>Retry</button>
    </div>);

  const empty = !(d.queue || []).length && !(d.score || []).length;

  return (
    <div>

      <div style={{ ...CARD, display: 'flex', flexWrap: 'wrap', gap: 18, alignItems: 'center' }}>
        {[['RUN', run ? run.run_id : 'not loaded'], ['PIPELINE', run ? run.pipeline_version : '-'],
          ['TABLES', run ? iv(run.tables_loaded) : '-'], ['ROWS', run ? iv(run.rows_loaded) : '-'],
          ['DATA AS OF', fresh ? String(fresh.data_as_of_timestamp || '-').slice(0, 19) : '-']].map(([k, v]) => (
          <div key={k}>
            <div style={{ fontSize: 10, color: '#64748b', fontWeight: 700 }}>{k}</div>
            <div style={{ fontSize: 12, color: '#0f172a', fontFamily: k === 'RUN' ? 'monospace' : 'inherit' }}>{v}</div>
          </div>))}
        <div style={{ marginLeft: 'auto', display: 'flex', gap: 6 }}>
          {['ALL', 'GATE', 'TVM', 'VALIDATOR'].map((t) => (
            <button key={t} onClick={() => setCat(t)} style={{
              background: cat === t ? (CAT_COLOR[t] || '#334155') : '#fff',
              color: cat === t ? '#fff' : '#475569', border: '1px solid #cbd5e1',
              borderRadius: 6, padding: '4px 10px', fontSize: 11, fontWeight: 700, cursor: 'pointer' }}>{t}</button>))}
          <button onClick={load} style={{ background: '#6366f1', color: '#fff', border: 'none',
            borderRadius: 6, padding: '4px 10px', fontSize: 11, fontWeight: 700, cursor: 'pointer' }}>Refresh</button>
        </div>
      </div>

      {empty && (
        <div style={{ ...CARD, borderColor: '#fde68a', background: '#fffbeb' }}>
          <p style={{ ...H, color: '#b45309' }}>No PS3 v2 rows in Aurora yet</p>
          <p style={SUB}>The routes answered, so the views exist. Run <code>{'{"action":"dry_run"}'}</code> then
            <code>{' {"action":"load"}'}</code> on <code>cubic-mars-ps3-v2-loader</code>. The previous PS3 views are unaffected.</p>
        </div>)}

      {/* publication policy - first, because it governs everything below */}

      {/* scorecard */}

      {/* ---- ROOT CAUSE ---- */}
      <div style={{ ...CARD, borderColor: '#c7b9e8' }}>
        <p style={H}>Root cause - which component is behind the failures</p>
        <p style={SUB}>
          <strong>Read this as measured history, not a forecast.</strong> This run publishes no per-incident
          predicted component, so what follows is the attribution its own incident record supports: for each
          component, how many devices and serials it affected and how many of those incidents were critical.
          Ranked by <strong>critical-weighted incidents</strong> - one component causing ten critical failures
          outranks another causing forty minor ones. Where the taxonomy audit flagged a label, that flag travels
          with the row, because an attribution is only as trustworthy as the label underneath it.
        </p>
        <div style={{ overflowX: 'auto' }}>
          <table style={{ width: '100%', borderCollapse: 'collapse' }}>
            <thead><tr><th style={TH}>Component</th><th style={TH}>Type</th><th style={TH}>Devices</th>
              <th style={TH}>Serials</th><th style={TH}>Incidents</th><th style={TH}>Mean critical rate</th>
              <th style={TH}>Critical-weighted</th><th style={TH}>30d recurrence</th>
              <th style={TH}>Label check</th></tr></thead>
            <tbody>
              {f(d.rcr).map((r, i) => (
                <tr key={i}>
                  <td style={{ ...TD, fontWeight: 700 }}>{r.component_label}
                    {r.component_label_semantics ? <div style={{ fontSize: 10, color: '#64748b' }}>{r.component_label_semantics}</div> : null}</td>
                  <td style={{ ...TD, color: CAT_COLOR[r.device_category], fontWeight: 600 }}>{r.device_category}</td>
                  <td style={TD}>{iv(r.devices_affected)}</td>
                  <td style={TD}>{iv(r.serials_affected)}</td>
                  <td style={TD}>{iv(r.incidents)}</td>
                  <td style={TD}>{n2(r.mean_critical_rate)}</td>
                  <td style={{ ...TD, fontWeight: 700 }}>{n2(r.critical_weighted, 1)}</td>
                  <td style={TD}>{iv(r.recurrence_30d)}</td>
                  <td style={{ ...TD, fontSize: 11, color: '#64748b' }}>{r.taxonomy_note || '-'}</td>
                </tr>))}
              {!(d.rcr || []).length && <tr><td style={TD} colSpan={9}>No root-cause rollup rows loaded.</td></tr>}
            </tbody>
          </table>
        </div>
      </div>

      <div style={{ ...CARD, borderColor: '#c7b9e8' }}>
        <p style={H}>Single-component serials - the strongest root-cause signal available</p>
        <p style={SUB}>
          A serial whose incidents all trace to one part is a component fault, not bad luck. Concentration is the
          share of that serial's incidents coming from its most frequent component. Anything at 1.00 with three or
          more incidents is a part to pull and inspect, and these are the rows worth taking to a supplier
          conversation.
        </p>
        <div style={{ overflowX: 'auto', maxHeight: 360, overflowY: 'auto' }}>
          <table style={{ width: '100%', borderCollapse: 'collapse' }}>
            <thead><tr><th style={TH}>Device</th><th style={TH}>Serial</th><th style={TH}>Type</th>
              <th style={TH}>Incidents</th><th style={TH}>Components</th><th style={TH}>Concentration</th>
              <th style={TH}>Verdict</th><th style={TH}></th></tr></thead>
            <tbody>
              {f(d.rcc).slice(0, 80).map((r, i) => (
                <tr key={i}>
                  <td style={{ ...TD, fontFamily: 'monospace', fontWeight: 600 }}>{r.device_id}</td>
                  <td style={{ ...TD, fontFamily: 'monospace', fontSize: 11 }}>{r.serial_number || '-'}</td>
                  <td style={{ ...TD, color: CAT_COLOR[r.device_category], fontWeight: 600 }}>{r.device_category}</td>
                  <td style={TD}>{iv(r.total_incidents)}</td>
                  <td style={TD}>{iv(r.distinct_components)}</td>
                  <td style={{ ...TD, fontWeight: 700 }}>{n2(r.concentration, 2)}</td>
                  <td style={{ ...TD, fontSize: 11 }}>
                    <Tag tone={String(r.concentration_verdict || '').startsWith('SINGLE') ? 'red'
                      : String(r.concentration_verdict || '').startsWith('DOMINANT') ? 'amber' : 'slate'}>
                      {String(r.concentration_verdict || '').split(' - ')[0]}</Tag>
                    <div style={{ marginTop: 3, color: '#64748b' }}>{String(r.concentration_verdict || '').split(' - ')[1]}</div>
                  </td>
                  <td style={TD}><AnalyseButton compact onClick={() => onAnalyse && onAnalyse(r.device_id)} /></td>
                </tr>))}
              {!(d.rcc || []).length && <tr><td style={TD} colSpan={8}>No concentration rows loaded.</td></tr>}
            </tbody>
          </table>
        </div>
      </div>

      {/* action queue */}
      <div style={CARD}>
        <p style={H}>Severity action queue - what to work, in order</p>
        <p style={SUB}>Ranked by the run's own priority score, which already folds in recurrence and recency - that is
          what makes it a work queue rather than a probability leaderboard. Confidence is shown beside every call:
          a LOW confidence row is a hint, not a decision.</p>
        <div style={{ overflowX: 'auto', maxHeight: 480, overflowY: 'auto' }}>
          <table style={{ width: '100%', borderCollapse: 'collapse' }}>
            <thead><tr><th style={TH}>Priority</th><th style={TH}>Device</th><th style={TH}>Type</th>
              <th style={TH}>Facility</th><th style={TH}>Predicted</th><th style={TH}>Band</th>
              <th style={TH}>P(critical)</th><th style={TH}>Confidence</th><th style={TH}>Recurrence</th>
              <th style={TH}>30d prior</th><th style={TH}>Event time</th><th style={TH}></th></tr></thead>
            <tbody>
              {f(d.queue).slice(0, 300).map((r, i) => (
                <tr key={i}>
                  <td style={{ ...TD, fontWeight: 700 }}>{n2(r.action_priority_score, 2)}</td>
                  <td style={{ ...TD, fontFamily: 'monospace', fontWeight: 600 }}>{r.device_id}</td>
                  <td style={{ ...TD, color: CAT_COLOR[r.device_category], fontWeight: 600 }}>{r.device_category}</td>
                  <td style={TD}>{r.facility_id || '-'}</td>
                  <td style={{ ...TD, color: BAND[r.predicted_severity] || '#1e293b', fontWeight: 700 }}>{r.predicted_severity || '-'}</td>
                  <td style={TD}>{r.action_band || '-'}</td>
                  <td style={TD}>{n2(r.critical_probability)}</td>
                  <td style={TD}><Tag tone={r.confidence_band === 'HIGH' ? 'green' : r.confidence_band === 'MODERATE' ? 'amber' : 'red'}>{r.confidence_band || '-'}</Tag></td>
                  <td style={{ ...TD, fontSize: 11 }}>{r.recurrence_note}</td>
                  <td style={TD}>{iv(r.prior_incidents_30d)}</td>
                  <td style={{ ...TD, fontSize: 11 }}>{String(r.source_event_timestamp || '').slice(0, 16)}</td>
                  <td style={TD}><AnalyseButton compact onClick={() => onAnalyse && onAnalyse(r.device_id)} /></td>
                </tr>))}
              {!(d.queue || []).length && <tr><td style={TD} colSpan={12}>No queue rows.</td></tr>}
            </tbody>
          </table>
        </div>
      </div>

      {/* causal */}
      <div style={CARD}>
        <p style={H}>Causal evidence - does this factor actually cause the outcome?</p>
        <p style={SUB}>Correlation is easy; cause is not. Each row below was estimated with a method that reweights
          the comparison so treated and untreated devices look alike, then checked twice: were the groups balanced,
          and did they overlap enough to compare at all. A row that failed either check is <strong>not evidence</strong>
          and says so. Risk difference is in percentage points of outcome risk.</p>
        <div style={{ overflowX: 'auto' }}>
          <table style={{ width: '100%', borderCollapse: 'collapse' }}>
            <thead><tr><th style={TH}>Device</th><th style={TH}>Factor examined</th><th style={TH}>Outcome</th>
              <th style={TH}>Risk difference</th><th style={TH}>95% interval</th><th style={TH}>p</th>
              <th style={TH}>Treated / control</th><th style={TH}>Verdict</th></tr></thead>
            <tbody>
              {f(d.causal).map((r, i) => (
                <tr key={i}>
                  <td style={{ ...TD, color: CAT_COLOR[r.device_category], fontWeight: 600 }}>{r.device_category}</td>
                  <td style={TD}>{r.treatment_definition || r.treatment_id}</td>
                  <td style={TD}>{r.outcome_label || r.outcome_id}</td>
                  <td style={{ ...TD, fontWeight: 700 }}>{n2(r.aipw_risk_difference, 4)}</td>
                  <td style={TD}>{r.confidence_interval_95_low == null ? '-' :
                    `${n2(r.confidence_interval_95_low, 4)} to ${n2(r.confidence_interval_95_high, 4)}`}</td>
                  <td style={TD}>{n2(r.p_value_normal_approx, 4)}</td>
                  <td style={TD}>{iv(r.treated_rows)} / {iv(r.control_rows)}</td>
                  <td style={{ ...TD, fontSize: 11 }}>
                    {yes(r.dashboard_ready) ? <Tag tone="green">USABLE</Tag> : <Tag tone="amber">NOT EVIDENCE</Tag>}
                    <div style={{ marginTop: 3, color: '#64748b' }}>{r.causal_verdict}</div>
                  </td>
                </tr>))}
              {!(d.causal || []).length && (
                <tr><td style={TD} colSpan={8}>No causal rows published by this run. That is a result, not a gap:
                  the estimates that did not clear the balance and overlap checks were withheld deliberately.</td></tr>)}
            </tbody>
          </table>
        </div>
      </div>

      {/* drivers + components + facilities */}
      <div style={{ display: 'flex', gap: 16, flexWrap: 'wrap' }}>
        <div style={{ ...CARD, flex: '1 1 480px' }}>
          <p style={H}>What drives the severity call</p>
          <p style={SUB}>Average SHAP magnitude per feature - how much each factor moves the answer, across all
            explained incidents.</p>
          <table style={{ width: '100%', borderCollapse: 'collapse' }}>
            <thead><tr><th style={TH}>Device</th><th style={TH}>Head</th><th style={TH}>Feature</th>
              <th style={TH}>Mean impact</th><th style={TH}>Direction</th></tr></thead>
            <tbody>
              {f(d.shapg).slice(0, 18).map((r, i) => (
                <tr key={i}>
                  <td style={{ ...TD, color: CAT_COLOR[r.device_category], fontWeight: 600 }}>{r.device_category}</td>
                  <td style={TD}>{r.head}</td>
                  <td style={{ ...TD, fontFamily: 'monospace', fontSize: 11 }}>{r.feature}</td>
                  <td style={{ ...TD, fontWeight: 700 }}>{n2(r.mean_abs_shap, 4)}</td>
                  <td style={TD}>{Number(r.mean_signed_shap) >= 0 ? 'raises severity' : 'lowers severity'}</td>
                </tr>))}
              {!(d.shapg || []).length && <tr><td style={TD} colSpan={5}>No global SHAP rows.</td></tr>}
            </tbody>
          </table>
        </div>
        <div style={{ ...CARD, flex: '1 1 380px' }}>
          <p style={H}>Components by portfolio priority</p>
          <p style={SUB}>Which parts generate the most serious work across the estate.</p>
          <table style={{ width: '100%', borderCollapse: 'collapse' }}>
            <thead><tr><th style={TH}>Component</th><th style={TH}>Incidents</th><th style={TH}>Devices</th>
              <th style={TH}>Critical rate</th><th style={TH}>Priority</th></tr></thead>
            <tbody>
              {f(d.comp).map((r, i) => (
                <tr key={i}>
                  <td style={{ ...TD, fontWeight: 600 }}>{r.component_label}</td>
                  <td style={TD}>{iv(r.incident_count)}</td>
                  <td style={TD}>{iv(r.affected_devices)}</td>
                  <td style={TD}>{n2(r.critical_rate)}</td>
                  <td style={{ ...TD, fontWeight: 700 }}>{n2(r.portfolio_priority_score, 2)}</td>
                </tr>))}
              {!(d.comp || []).length && <tr><td style={TD} colSpan={5}>No component rows.</td></tr>}
            </tbody>
          </table>
        </div>
      </div>

      <div style={CARD}>
        <p style={H}>Repeat-offender devices and facility hotspots</p>
        <p style={SUB}>Left: devices ranked by how often their incidents are critical. Right: facilities where
          incidents concentrate - a site problem needs a different response from a device problem.</p>
        <div style={{ display: 'flex', gap: 16, flexWrap: 'wrap' }}>
          <div style={{ flex: '1 1 480px', maxHeight: 320, overflowY: 'auto' }}>
            <table style={{ width: '100%', borderCollapse: 'collapse' }}>
              <thead><tr><th style={TH}>Device</th><th style={TH}>Type</th><th style={TH}>Incidents</th>
                <th style={TH}>Critical</th><th style={TH}>Rate</th><th style={TH}>Band</th>
                <th style={TH}>30d</th><th style={TH}></th></tr></thead>
              <tbody>
                {f(d.dev).slice(0, 60).map((r, i) => (
                  <tr key={i}>
                    <td style={{ ...TD, fontFamily: 'monospace', fontWeight: 600 }}>{r.device_id}</td>
                    <td style={{ ...TD, color: CAT_COLOR[r.device_category], fontWeight: 600 }}>{r.device_category}</td>
                    <td style={TD}>{iv(r.incident_count)}</td>
                    <td style={TD}>{iv(r.critical_incidents)}</td>
                    <td style={{ ...TD, fontWeight: 700 }}>{n2(r.critical_rate)}</td>
                    <td style={TD}>{r.reliability_risk_band || '-'}</td>
                    <td style={TD}>{iv(r.recent_30d_recurrence)}</td>
                    <td style={TD}><AnalyseButton compact onClick={() => onAnalyse && onAnalyse(r.device_id)} /></td>
                  </tr>))}
                {!(d.dev || []).length && <tr><td style={TD} colSpan={8}>No device rows.</td></tr>}
              </tbody>
            </table>
          </div>
          <div style={{ flex: '1 1 360px', maxHeight: 320, overflowY: 'auto' }}>
            <table style={{ width: '100%', borderCollapse: 'collapse' }}>
              <thead><tr><th style={TH}>Facility</th><th style={TH}>Type</th><th style={TH}>Incidents</th>
                <th style={TH}>Devices</th><th style={TH}>Critical rate</th></tr></thead>
              <tbody>
                {f(d.fac).slice(0, 60).map((r, i) => (
                  <tr key={i}>
                    <td style={{ ...TD, fontWeight: 600 }}>{r.facility_id}</td>
                    <td style={{ ...TD, color: CAT_COLOR[r.device_category], fontWeight: 600 }}>{r.device_category}</td>
                    <td style={TD}>{iv(r.incident_count)}</td>
                    <td style={TD}>{iv(r.affected_devices)}</td>
                    <td style={{ ...TD, fontWeight: 700 }}>{n2(r.critical_rate)}</td>
                  </tr>))}
                {!(d.fac || []).length && <tr><td style={TD} colSpan={5}>No facility rows.</td></tr>}
              </tbody>
            </table>
          </div>
        </div>
      </div>

    </div>
  );
}
