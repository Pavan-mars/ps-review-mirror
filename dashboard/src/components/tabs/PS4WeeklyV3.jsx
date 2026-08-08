import { API_BASE_URL } from '../../runtimeConfig';
// ============================================================================
// PS4WeeklyV3.jsx  ->  src/components/tabs/PS4WeeklyV3.jsx
//
// PS4 v3 WEEKLY ANOMALY + CLUSTERING                             29-Jul-2026
//
// Renders the v3 pipeline (run ps4-20260728T213153Z-bf4609d4, 14,817,096
// device-hour rows) from Aurora via the /ps4/weekly* routes. It is a NEW
// sub-tab. The existing PS4 sub-tabs (Real-Time Alerts, Deviation Scoring,
// Outlier Analysis, Trend Monitoring) are untouched and still read the old
// feed, which stays as the fallback.
//
// THREE FRAMING RULES, ENFORCED IN THIS FILE
//
// 1. ANOMALY IS NOT FAILURE. PS4 is unsupervised. It says a device is unlike
//    its peers. It does not say the device will fail -- that is PS1's claim,
//    with PS1's label and PS1's error bars. The word "probability" does not
//    appear on this screen, and no percentage here is a chance of anything.
//
// 2. NO CLUSTER FIGURE WITHOUT ITS SILHOUETTE. A cluster id with no separation
//    metric is a decoration. Every cluster panel prints the silhouette, the
//    verdict, and where the number came from, right next to the number.
//
// 3. A PARTIAL WEEK IS LABELLED. A device with two observed days is not quiet,
//    it is missing. has_low_coverage_day and observed_days drive an explicit
//    PARTIAL badge rather than being averaged away.
//
// Charts: one y-axis each, never two. Categorical colour assigned by device
// type in fixed order and never recycled, so a filter that removes a type does
// not repaint the survivors.
// ============================================================================
import React, { useState, useEffect, useMemo, useCallback } from 'react';
import AnalyseButton from '../shared/AnalyseButton';
import {
  BarChart, LineChart, Bar, Line, XAxis, YAxis, CartesianGrid,
  Tooltip, Legend, ResponsiveContainer, ScatterChart, Scatter, ZAxis,
} from 'recharts';

const API_BASE = (API_BASE_URL
  || 'https://a9yuqt9j9b.execute-api.us-east-1.amazonaws.com').replace(/\/$/, '');

// Fixed order. GATE, TVM, VALIDATOR always take the same hue regardless of
// which of them the current filter leaves on screen.
const TYPE_COLOR = { GATE: '#2563eb', TVM: '#0d9488', VALIDATOR: '#c2410c' };
const TYPES = ['GATE', 'TVM', 'VALIDATOR'];

const SEV_COLOR = { Critical: '#b91c1c', High: '#c2410c', Normal: '#475569' };

async function apiGet(path, params = {}) {
  const qs = new URLSearchParams(
    Object.entries(params).filter(([, v]) => v !== undefined && v !== null && v !== '')
  ).toString();
  const res = await fetch(`${API_BASE}${path}${qs ? `?${qs}` : ''}`);
  if (!res.ok) throw new Error(`${path} -> HTTP ${res.status}`);
  const j = await res.json();
  return j && j.data !== undefined ? j.data : j;
}

const num = (v, d = 2) =>
  (v === null || v === undefined || Number.isNaN(Number(v))) ? '-' : Number(v).toFixed(d);
const int = (v) =>
  (v === null || v === undefined) ? '-' : Number(v).toLocaleString();
const pct = (v, d = 1) =>
  (v === null || v === undefined || Number.isNaN(Number(v))) ? '-' : `${(Number(v) * 100).toFixed(d)}%`;

// A row carrying _unavailable is the API telling us the view is missing. Not an
// exception, not an empty result -- a stated reason, which is displayed.
const unavailable = (arr) =>
  Array.isArray(arr) && arr.length === 1 && arr[0] && arr[0]._unavailable
    ? arr[0]._unavailable : null;
const clean = (arr) => (unavailable(arr) ? [] : (Array.isArray(arr) ? arr : []));

const CARD = {
  background: '#fff', border: '1px solid #e2e8f0', borderRadius: 10,
  padding: 16, marginBottom: 16,
};
const H = { margin: '0 0 4px', fontSize: 15, fontWeight: 700, color: '#0f172a' };
const SUB = { margin: '0 0 12px', fontSize: 12, color: '#64748b', lineHeight: 1.5 };
const TH = {
  textAlign: 'left', padding: '7px 8px', fontSize: 11, fontWeight: 700,
  color: '#475569', borderBottom: '1px solid #e2e8f0', whiteSpace: 'nowrap',
};
const TD = { padding: '6px 8px', fontSize: 12, color: '#1e293b', borderBottom: '1px solid #f1f5f9' };

function Badge({ children, tone = 'slate' }) {
  const tones = {
    slate: ['#f1f5f9', '#475569'], red: ['#fef2f2', '#b91c1c'],
    amber: ['#fffbeb', '#b45309'], green: ['#f0fdf4', '#15803d'],
    indigo: ['#eef2ff', '#4338ca'],
  };
  const [bg, fg] = tones[tone] || tones.slate;
  return (
    <span style={{
      display: 'inline-block', padding: '2px 7px', borderRadius: 4, background: bg,
      color: fg, fontSize: 10, fontWeight: 700, letterSpacing: 0.2, whiteSpace: 'nowrap',
    }}>{children}</span>
  );
}

// CLUSTER MAP 29-Jul-2026. Framing and separation-table cards removed at PK's
// request. The silhouette now travels INSIDE the cluster map header, so the
// quality number is never separated from the picture it qualifies.
export default function PS4WeeklyV3({ city, onAnalyse }) {
  const [status, setStatus] = useState(null);
  const [timeline, setTimeline] = useState([]);
  const [profile, setProfile] = useState([]);
  const [alerts, setAlerts] = useState([]);
  const [persistent, setPersistent] = useState([]);
  const [facility, setFacility] = useState([]);
  const [typeFilter, setTypeFilter] = useState('ALL');
  const [loading, setLoading] = useState(true);
  const [fatal, setFatal] = useState(null);

  const load = useCallback(() => {
    setLoading(true); setFatal(null);
    Promise.all([
      apiGet('/ps4/v3-status', { city }),
      apiGet('/ps4/weekly-timeline', { city }),
      apiGet('/ps4/cluster-profile', { city }),
      apiGet('/ps4/weekly-alerts', { city, limit: 500 }),
      apiGet('/ps4/weekly-persistent', { city, limit: 200 }),
      apiGet('/ps4/weekly-facility', { city, limit: 200 }),
    ]).then(([st, tl, cp, al, pe, fa]) => {
      setStatus(st || null);
      setTimeline(clean(tl)); setProfile(clean(cp));
      setAlerts(clean(al)); setPersistent(clean(pe)); setFacility(clean(fa));
    }).catch((e) => setFatal(String(e.message || e)))
      .finally(() => setLoading(false));
  }, [city]);

  useEffect(() => { load(); }, [load]);

  const run = useMemo(() => {
    const r = status && clean(status.run);
    return (r && r[0]) || null;
  }, [status]);

  const quality = useMemo(() => clean(status && status.quality), [status]);
  const tables = useMemo(() => clean(status && status.tables), [status]);
  const reconcile = useMemo(() => clean(status && status.reconcile), [status]);

  const filt = useCallback(
    (arr) => (typeFilter === 'ALL' ? arr : arr.filter((r) => r.device_type === typeFilter)),
    [typeFilter]);

  // Timeline pivoted to one column per device type. Recharts needs one row per
  // x value; the pivot is done here rather than in SQL so the same view can
  // serve a table view unchanged.
  const timelineChart = useMemo(() => {
    const byWeek = new Map();
    timeline.forEach((r) => {
      const k = String(r.week_start).slice(0, 10);
      if (!byWeek.has(k)) byWeek.set(k, { week: k });
      byWeek.get(k)[`${r.device_type}_actionable`] = Number(r.actionable_devices || 0);
      byWeek.get(k)[`${r.device_type}_observed`] = Number(r.devices_observed || 0);
    });
    return Array.from(byWeek.values()).sort((a, b) => a.week.localeCompare(b.week));
  }, [timeline]);

  // Cluster scatter: training share on x, actionable rate on y, bubble = scored
  // device-days. One axis pair, no dual scale -- both are shares, so they are
  // directly comparable.
  const clusterPoints = useMemo(() => filt(profile).map((r) => ({
    x: Number(r.train_cluster_share || 0),
    y: Number(r.actionable_rate || 0),
    z: Number(r.scored_device_days || 0),
    device_type: r.device_type,
    cluster_id: r.cluster_id,
  })), [profile, filt]);

  const clusterByType = useMemo(() => {
    const m = {};
    clusterPoints.forEach((p) => { (m[p.device_type] = m[p.device_type] || []).push(p); });
    return m;
  }, [clusterPoints]);

  const alertsF = useMemo(() => filt(alerts), [alerts, filt]);
  const persistentF = useMemo(() => filt(persistent), [persistent, filt]);

  if (loading) {
    return <div style={{ ...CARD, color: '#64748b' }}>Loading PS4 v3 weekly data...</div>;
  }
  if (fatal) {
    return (
      <div style={{ ...CARD, borderColor: '#fecaca', background: '#fef2f2' }}>
        <p style={{ ...H, color: '#b91c1c' }}>PS4 v3 routes unavailable</p>
        <p style={{ ...SUB, marginBottom: 8 }}>{fatal}</p>
        <p style={SUB}>
          The v3 tables are created by sql/38_ps4_weekly_v3.sql and filled by
          cubic-mars-ps4-v3-loader. Until both have run, this sub-tab is empty --
          the other PS4 sub-tabs are unaffected and still show the previous feed.
        </p>
        <button onClick={load} style={{
          background: '#6366f1', color: '#fff', border: 'none', borderRadius: 6,
          padding: '5px 12px', fontSize: 12, fontWeight: 600, cursor: 'pointer',
        }}>Retry</button>
      </div>
    );
  }

  const noRows = !alerts.length && !timeline.length && !profile.length;

  return (
    <div>
      {/* ---------------------------------------------------------------
          FRAMING. First thing on the screen, before any number, because the
          single most expensive misreading available here is "anomaly = about
          to fail".
         --------------------------------------------------------------- */}

      {/* ---- run provenance + filter ---- */}
      <div style={{ ...CARD, display: 'flex', flexWrap: 'wrap', gap: 18, alignItems: 'center' }}>
        <div>
          <div style={{ fontSize: 10, color: '#64748b', fontWeight: 700 }}>RUN</div>
          <div style={{ fontSize: 12, fontFamily: 'monospace', color: '#0f172a' }}>
            {run ? run.run_id : 'not loaded'}
          </div>
        </div>
        <div>
          <div style={{ fontSize: 10, color: '#64748b', fontWeight: 700 }}>PIPELINE</div>
          <div style={{ fontSize: 12, color: '#0f172a' }}>{run ? run.pipeline_version : '-'}</div>
        </div>
        <div>
          <div style={{ fontSize: 10, color: '#64748b', fontWeight: 700 }}>AS OF</div>
          <div style={{ fontSize: 12, color: '#0f172a' }}>
            {run && run.asof_date ? String(run.asof_date).slice(0, 10) : '-'}
          </div>
        </div>
        <div>
          <div style={{ fontSize: 10, color: '#64748b', fontWeight: 700 }}>SOURCE ROWS</div>
          <div style={{ fontSize: 12, color: '#0f172a' }}>{int(run && run.source_rows)}</div>
        </div>
        <div style={{ marginLeft: 'auto', display: 'flex', gap: 6, alignItems: 'center' }}>
          {['ALL', ...TYPES].map((t) => (
            <button key={t} onClick={() => setTypeFilter(t)} style={{
              background: typeFilter === t ? (TYPE_COLOR[t] || '#334155') : '#fff',
              color: typeFilter === t ? '#fff' : '#475569',
              border: '1px solid #cbd5e1', borderRadius: 6, padding: '4px 10px',
              fontSize: 11, fontWeight: 700, cursor: 'pointer',
            }}>{t}</button>
          ))}
          <button onClick={load} style={{
            background: '#6366f1', color: '#fff', border: 'none', borderRadius: 6,
            padding: '4px 10px', fontSize: 11, fontWeight: 700, cursor: 'pointer',
          }}>Refresh</button>
        </div>
      </div>

      {noRows && (
        <div style={{ ...CARD, borderColor: '#fde68a', background: '#fffbeb' }}>
          <p style={{ ...H, color: '#b45309' }}>No v3 rows in Aurora yet</p>
          <p style={SUB}>
            The routes answered, so the views exist -- the tables are simply empty.
            Run the loader:
            {' '}<code>{'{"action":"dry_run"}'}</code> then <code>{'{"action":"load"}'}</code>
            {' '}on <code>cubic-mars-ps4-v3-loader</code>. The previous PS4 sub-tabs are
            unaffected.
          </p>
        </div>
      )}

      {/* ---------------------------------------------------------------
          CLUSTER QUALITY. Deliberately ABOVE the cluster charts. A reader who
          scrolls past a cluster picture and only then meets the silhouette has
          already formed the belief the metric was meant to qualify.
         --------------------------------------------------------------- */}

      {/* ---- weekly volume ---- */}
      <div style={CARD}>
        <p style={H}>Actionable devices per week</p>
        <p style={SUB}>
          Counts, not rates. The observed-device denominator is in the table below the
          chart -- 40 actionable out of 60 observed and 40 out of 2,000 are the same
          bar and opposite situations, so the denominator is never hidden.
        </p>
        <ResponsiveContainer width="100%" height={260}>
          <BarChart data={timelineChart} margin={{ top: 8, right: 16, left: 0, bottom: 4 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="#eef2f7" vertical={false} />
            <XAxis dataKey="week" tick={{ fontSize: 11, fill: '#64748b' }} />
            <YAxis tick={{ fontSize: 11, fill: '#64748b' }}
                   label={{ value: 'actionable devices', angle: -90, position: 'insideLeft',
                            style: { fontSize: 11, fill: '#64748b' } }} />
            <Tooltip contentStyle={{ fontSize: 12 }} />
            <Legend wrapperStyle={{ fontSize: 11 }} />
            {TYPES.filter((t) => typeFilter === 'ALL' || typeFilter === t).map((t) => (
              <Bar key={t} dataKey={`${t}_actionable`} name={t}
                   fill={TYPE_COLOR[t]} radius={[4, 4, 0, 0]} stroke="#fff" strokeWidth={2} />
            ))}
          </BarChart>
        </ResponsiveContainer>
        <div style={{ overflowX: 'auto', marginTop: 10 }}>
          <table style={{ width: '100%', borderCollapse: 'collapse' }}>
            <thead><tr>
              <th style={TH}>Week</th><th style={TH}>Type</th>
              <th style={TH}>Devices observed</th><th style={TH}>Actionable</th>
              <th style={TH}>Actionable share</th><th style={TH}>Candidate device-days</th>
              <th style={TH}>Mean score</th><th style={TH}>Max score</th>
            </tr></thead>
            <tbody>
              {filt(timeline).slice(0, 40).map((r, i) => (
                <tr key={i}>
                  <td style={TD}>{String(r.week_start).slice(0, 10)}</td>
                  <td style={{ ...TD, color: TYPE_COLOR[r.device_type], fontWeight: 600 }}>
                    {r.device_type}
                  </td>
                  <td style={TD}>{int(r.devices_observed)}</td>
                  <td style={{ ...TD, fontWeight: 700 }}>{int(r.actionable_devices)}</td>
                  <td style={TD}>{pct(r.actionable_share)}</td>
                  <td style={TD}>{int(r.candidate_device_days)}</td>
                  <td style={TD}>{num(r.mean_anomaly_score, 4)}</td>
                  <td style={TD}>{num(r.max_anomaly_score, 4)}</td>
                </tr>
              ))}
              {timeline.length === 0 && (
                <tr><td style={TD} colSpan={8}>No timeline rows.</td></tr>
              )}
            </tbody>
          </table>
        </div>
      </div>

      {/* ---- cluster profile ---- */}
      <div style={CARD}>
        <p style={H}>Cluster map -- how the behaviour groups sit, and whether they are real</p>
        {/* CLUSTER QUALITY STRIP. The silhouette sits ON the chart card, not in a
            separate table further down: a cluster picture read without its
            separation score is the single easiest thing to over-trust here. */}
        <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', marginBottom: 10 }}>
          {quality.map((q) => (
            <div key={q.device_type} style={{ flex: '1 1 190px', padding: '8px 11px',
              borderRadius: 8, background: '#f8fafc',
              borderLeft: `4px solid ${TYPE_COLOR[q.device_type] || '#94a3b8'}`,
              border: '1px solid #e2e8f0' }}>
              <div style={{ fontSize: 11, fontWeight: 700, color: TYPE_COLOR[q.device_type] }}>
                {q.device_type}</div>
              <div style={{ fontSize: 21, fontWeight: 700, color: '#0f172a', lineHeight: 1.15 }}>
                {num(q.silhouette, 3)}</div>
              <div style={{ fontSize: 10, color: '#64748b' }}>
                {q.k_selected} groups &middot; {q.separation_verdict}</div>
              <div style={{ height: 5, borderRadius: 3, background: '#e2e8f0', marginTop: 5 }}>
                <div style={{ width: `${Math.max(0, Math.min(100, Number(q.silhouette || 0) * 100))}%`,
                  height: 5, borderRadius: 3, background: TYPE_COLOR[q.device_type] || '#94a3b8' }} />
              </div>
              <div style={{ fontSize: 9.5, color: '#94a3b8', marginTop: 3 }}>
                0.50 = strong &middot; source {q.quality_source}</div>
            </div>))}
        </div>
        <p style={SUB}>
          Both axes are shares of the same kind, so they sit on one scale and can be
          read against each other. A cluster low on training share but high on
          actionable rate is the interesting one: a small, distinct behaviour that
          keeps producing work. A cluster holding most of the training population with
          a near-zero actionable rate is simply "normal", and naming it is the whole
          value it provides.
        </p>
        <ResponsiveContainer width="100%" height={280}>
          <ScatterChart margin={{ top: 8, right: 20, left: 4, bottom: 16 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="#eef2f7" />
            <XAxis type="number" dataKey="x" name="training share"
                   tick={{ fontSize: 11, fill: '#64748b' }}
                   tickFormatter={(v) => `${(v * 100).toFixed(0)}%`}
                   label={{ value: 'share of training population', position: 'insideBottom',
                            offset: -8, style: { fontSize: 11, fill: '#64748b' } }} />
            <YAxis type="number" dataKey="y" name="actionable rate"
                   tick={{ fontSize: 11, fill: '#64748b' }}
                   tickFormatter={(v) => `${(v * 100).toFixed(0)}%`}
                   label={{ value: 'actionable rate', angle: -90, position: 'insideLeft',
                            style: { fontSize: 11, fill: '#64748b' } }} />
            <ZAxis type="number" dataKey="z" range={[60, 400]} name="scored device-days" />
            <Tooltip contentStyle={{ fontSize: 12 }}
                     formatter={(v, n) => (n === 'scored device-days'
                       ? int(v) : `${(Number(v) * 100).toFixed(2)}%`)}
                     labelFormatter={() => ''} />
            <Legend wrapperStyle={{ fontSize: 11 }} />
            {Object.entries(clusterByType).map(([t, pts]) => (
              <Scatter key={t} name={t} data={pts} fill={TYPE_COLOR[t] || '#64748b'}
                       stroke="#fff" strokeWidth={2} />
            ))}
          </ScatterChart>
        </ResponsiveContainer>
        <div style={{ overflowX: 'auto', marginTop: 10 }}>
          <table style={{ width: '100%', borderCollapse: 'collapse' }}>
            <thead><tr>
              <th style={TH}>Type</th><th style={TH}>Cluster</th><th style={TH}>Silhouette</th>
              <th style={TH}>Scored device-days</th><th style={TH}>Training share</th>
              <th style={TH}>Candidate rate</th><th style={TH}>Actionable rate</th>
              <th style={TH}>Mean distance</th><th style={TH}>Train p99</th>
              <th style={TH}>Mean ratio</th>
            </tr></thead>
            <tbody>
              {filt(profile).map((r, i) => (
                <tr key={i}>
                  <td style={{ ...TD, color: TYPE_COLOR[r.device_type], fontWeight: 600 }}>
                    {r.device_type}
                  </td>
                  <td style={{ ...TD, fontWeight: 700 }}>{r.cluster_id}</td>
                  <td style={TD}>
                    {num(r.silhouette, 3)}{' '}
                    {r.quality_source ? <Badge tone={r.quality_source === 'manifest' ? 'indigo' : 'amber'}>{r.quality_source}</Badge> : null}
                  </td>
                  <td style={TD}>{int(r.scored_device_days)}</td>
                  <td style={TD}>{pct(r.train_cluster_share)}</td>
                  <td style={TD}>{pct(r.candidate_rate, 2)}</td>
                  <td style={{ ...TD, fontWeight: 700 }}>{pct(r.actionable_rate, 2)}</td>
                  <td style={TD}>{num(r.mean_cluster_distance, 3)}</td>
                  <td style={TD}>{num(r.train_cluster_distance_p99, 3)}</td>
                  <td style={TD}>{num(r.mean_distance_ratio, 3)}</td>
                </tr>
              ))}
              {profile.length === 0 && (
                <tr><td style={TD} colSpan={10}>No cluster profile rows.</td></tr>
              )}
            </tbody>
          </table>
        </div>
      </div>

      {/* ---- repeat offenders ---- */}
      <div style={CARD}>
        <p style={H}>Repeat offenders -- actionable in more than one week</p>
        <p style={SUB}>
          A device anomalous in a single week is usually noise. A device anomalous
          four weeks running is a maintenance decision. This list is the one worth
          dispatching against, and it is short on purpose.
        </p>
        <div style={{ overflowX: 'auto' }}>
          <table style={{ width: '100%', borderCollapse: 'collapse' }}>
            <thead><tr>
              <th style={TH}>Device</th><th style={TH}>Type</th><th style={TH}>Facility</th>
              <th style={TH}>Actionable weeks</th><th style={TH}>First</th><th style={TH}>Last</th>
              <th style={TH}>Worst distance ratio</th><th style={TH}>Severities</th><th style={TH}></th>
            </tr></thead>
            <tbody>
              {persistentF.slice(0, 60).map((r, i) => (
                <tr key={i}>
                  <td style={{ ...TD, fontFamily: 'monospace', fontWeight: 600 }}>{r.device_id}</td>
                  <td style={{ ...TD, color: TYPE_COLOR[r.device_type], fontWeight: 600 }}>{r.device_type}</td>
                  <td style={TD}>{r.facility_id || '-'}</td>
                  <td style={{ ...TD, fontWeight: 700 }}>{r.actionable_weeks}</td>
                  <td style={TD}>{String(r.first_week).slice(0, 10)}</td>
                  <td style={TD}>{String(r.last_week).slice(0, 10)}</td>
                  <td style={{ ...TD, fontWeight: 700,
                               color: Number(r.worst_distance_ratio) > 1 ? '#b91c1c' : '#1e293b' }}>
                    {num(r.worst_distance_ratio, 3)}
                  </td>
                  <td style={TD}>{r.severities || '-'}</td>
                  <td style={TD}>
                    <AnalyseButton compact onClick={() => onAnalyse && onAnalyse(r.device_id)} />
                  </td>
                </tr>
              ))}
              {persistentF.length === 0 && (
                <tr><td style={TD} colSpan={9}>
                  No device was actionable in more than one week
                  {typeFilter !== 'ALL' ? ` for ${typeFilter}` : ''}.
                </td></tr>
              )}
            </tbody>
          </table>
        </div>
      </div>

      {/* ---- alert feed ---- */}
      <div style={CARD}>
        <p style={H}>Weekly alert feed</p>
        <p style={SUB}>
          Ranked by distance ratio, which is normalised per cluster and therefore
          comparable across device types. PARTIAL marks a week with fewer than five
          observed days -- the score on those rows is built on less data and should
          not be compared like-for-like with a full week.
        </p>
        <div style={{ overflowX: 'auto', maxHeight: 520, overflowY: 'auto' }}>
          <table style={{ width: '100%', borderCollapse: 'collapse' }}>
            <thead><tr>
              <th style={TH}>Device</th><th style={TH}>Type</th><th style={TH}>Week</th>
              <th style={TH}>Severity</th><th style={TH}>Distance ratio</th>
              <th style={TH}>Max score</th><th style={TH}>p95 score</th>
              <th style={TH}>Max |z|</th><th style={TH}>Actionable days</th>
              <th style={TH}>Observed days</th><th style={TH}>Cluster</th>
              <th style={TH}>Signals</th><th style={TH}>Coverage</th><th style={TH}></th>
            </tr></thead>
            <tbody>
              {alertsF.slice(0, 300).map((r, i) => {
                const partial = r.has_low_coverage_day === 1 || Number(r.observed_days) < 5;
                return (
                  <tr key={i}>
                    <td style={{ ...TD, fontFamily: 'monospace', fontWeight: 600 }}>{r.device_id}</td>
                    <td style={{ ...TD, color: TYPE_COLOR[r.device_type], fontWeight: 600 }}>{r.device_type}</td>
                    <td style={TD}>{String(r.week_start).slice(0, 10)}</td>
                    <td style={TD}>
                      <span style={{ color: SEV_COLOR[r.severity] || '#475569', fontWeight: 700 }}>
                        {r.severity || '-'}
                      </span>
                    </td>
                    <td style={{ ...TD, fontWeight: 700,
                                 color: Number(r.cluster_distance_ratio_max) > 1 ? '#b91c1c' : '#1e293b' }}>
                      {num(r.cluster_distance_ratio_max, 3)}
                    </td>
                    <td style={TD}>{num(r.anomaly_score_max, 4)}</td>
                    <td style={TD}>{num(r.anomaly_score_p95, 4)}</td>
                    <td style={TD}>{num(r.max_abs_z, 2)}</td>
                    <td style={{ ...TD, fontWeight: 700 }}>{int(r.actionable_days)}</td>
                    <td style={TD}>{int(r.observed_days)}</td>
                    <td style={TD}>{r.dominant_cluster_id ?? '-'}</td>
                    <td style={{ ...TD, maxWidth: 180, whiteSpace: 'normal' }}>{r.anomaly_types || '-'}</td>
                    <td style={TD}>{partial ? <Badge tone="amber">PARTIAL</Badge> : <Badge tone="green">FULL</Badge>}</td>
                    <td style={TD}>
                      <AnalyseButton compact onClick={() => onAnalyse && onAnalyse(r.device_id)} />
                    </td>
                  </tr>
                );
              })}
              {alertsF.length === 0 && (
                <tr><td style={TD} colSpan={14}>No actionable weeks
                  {typeFilter !== 'ALL' ? ` for ${typeFilter}` : ''}.</td></tr>
              )}
            </tbody>
          </table>
        </div>
      </div>

      {/* ---- facility concentration ---- */}
      <div style={CARD}>
        <p style={H}>Facility concentration</p>
        <p style={SUB}>
          Whether this is a device problem or a site problem. A facility where most
          observed devices are actionable is an environment or install question, and
          replacing individual units there will not fix it.
        </p>
        <div style={{ overflowX: 'auto', maxHeight: 340, overflowY: 'auto' }}>
          <table style={{ width: '100%', borderCollapse: 'collapse' }}>
            <thead><tr>
              <th style={TH}>Facility</th><th style={TH}>Type</th>
              <th style={TH}>Devices</th><th style={TH}>Actionable devices</th>
              <th style={TH}>Share</th><th style={TH}>Mean distance ratio</th>
              <th style={TH}>Last week</th>
            </tr></thead>
            <tbody>
              {filt(facility).slice(0, 80).map((r, i) => (
                <tr key={i}>
                  <td style={{ ...TD, fontWeight: 600 }}>{r.facility_id}</td>
                  <td style={{ ...TD, color: TYPE_COLOR[r.device_type], fontWeight: 600 }}>{r.device_type}</td>
                  <td style={TD}>{int(r.devices)}</td>
                  <td style={{ ...TD, fontWeight: 700 }}>{int(r.actionable_devices)}</td>
                  <td style={TD}>
                    {r.devices ? pct(Number(r.actionable_devices) / Number(r.devices)) : '-'}
                  </td>
                  <td style={TD}>{num(r.mean_distance_ratio, 3)}</td>
                  <td style={TD}>{r.last_week ? String(r.last_week).slice(0, 10) : '-'}</td>
                </tr>
              ))}
              {facility.length === 0 && (
                <tr><td style={TD} colSpan={7}>No facility rows.</td></tr>
              )}
            </tbody>
          </table>
        </div>
      </div>

      {/* ---- load audit ---- */}
      <div style={CARD}>
        <p style={H}>Load audit</p>
        <p style={SUB}>
          Row counts as they stand in Aurora, and the reconciliation between the
          published alerts file and the actionable rows of the published summary. If
          those two columns disagree, the two exports disagree, and this is where it
          shows rather than on a slide.
        </p>
        <div style={{ display: 'flex', gap: 24, flexWrap: 'wrap' }}>
          <div style={{ minWidth: 260 }}>
            <table style={{ borderCollapse: 'collapse' }}>
              <thead><tr><th style={TH}>Table</th><th style={TH}>Rows</th></tr></thead>
              <tbody>
                {tables.map((t) => (
                  <tr key={t.table_name}>
                    <td style={{ ...TD, fontFamily: 'monospace' }}>{t.table_name}</td>
                    <td style={{ ...TD, fontWeight: 700 }}>{int(t.n_rows)}</td>
                  </tr>
                ))}
                {tables.length === 0 && <tr><td style={TD} colSpan={2}>No status rows.</td></tr>}
              </tbody>
            </table>
          </div>
          <div style={{ minWidth: 380 }}>
            <table style={{ borderCollapse: 'collapse' }}>
              <thead><tr>
                <th style={TH}>Type</th><th style={TH}>Summary rows</th>
                <th style={TH}>Summary actionable</th><th style={TH}>Alerts rows</th>
                <th style={TH}>Agree</th>
              </tr></thead>
              <tbody>
                {reconcile.map((r, i) => (
                  <tr key={i}>
                    <td style={TD}>{r.device_type}</td>
                    <td style={TD}>{int(r.summary_rows)}</td>
                    <td style={TD}>{int(r.summary_actionable)}</td>
                    <td style={TD}>{int(r.alerts_rows)}</td>
                    <td style={TD}>
                      {Number(r.summary_actionable) === Number(r.alerts_rows)
                        ? <Badge tone="green">MATCH</Badge>
                        : <Badge tone="red">DIFFERS</Badge>}
                    </td>
                  </tr>
                ))}
                {reconcile.length === 0 && <tr><td style={TD} colSpan={5}>No reconciliation rows.</td></tr>}
              </tbody>
            </table>
          </div>
        </div>
      </div>
    </div>
  );
}
