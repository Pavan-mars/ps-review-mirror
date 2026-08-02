// =====================================================================
// v2/PS4Overview.jsx -- PS4 weekly anomaly, macro to micro, six sub-tabs.
//
//   Fleet status -> Depots -> Devices -> Peer groups -> Repeat flags -> How we know
//
// -------------------------------------------------------------------
// THE ONE SENTENCE THAT MUST SURVIVE THE MEETING
// -------------------------------------------------------------------
// PS4 says a device is UNLIKE ITS PEERS. It does not say the device will
// fail. No number on this screen is a probability of anything. Every
// panel that could be misread as a failure forecast carries that line.
//
// -------------------------------------------------------------------
// WHICH ENDPOINT MEANS WHAT
// -------------------------------------------------------------------
//   /ps4/weekly-timeline   THE DENOMINATOR. Per week, per fleet:
//                          devices_observed and actionable_devices.
//                          Week of 30-Mar: 809 gates / 466 TVMs / 2,589
//                          validators observed; 63 / 128 / 381 actionable.
//   /ps4/weekly-alerts     THE COMPLETE actionable set: 1,108 rows,
//                          922 distinct devices, Critical 256 / High 852.
//                          Safe for totals.
//   /ps4/weekly            LIMIT 500 by default of 7,742 device-weeks.
//                          A BROWSE LIST, NEVER A DENOMINATOR. Same trap
//                          that put "600 of 600" on the PS1 screen.
//   /ps4/weekly-facility   Device-level per facility x fleet, deduped:
//                          220 facilities, 3,938 devices, 922 actionable.
//   /ps4/cluster-quality   Ships its own separation_verdict and
//                          separation_note. Those strings are shown as
//                          written -- they are the honest reading and it
//                          is not the dashboard's place to soften them.
//
// Every PS4 route answers in under 0.6s: the notebook aggregates to
// device-WEEKS, not device-days. That is why this tab loads instantly and
// PS1's Why tab does not.
// =====================================================================
import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { RefreshCw, TriangleAlert } from 'lucide-react';
import {
  CAT, INK, INK_2, INK_3, LINE, STATUS, deviceColor, deviceName, deviceShort,
  dfmt, font, nfmt, pct, pct100,
} from './theme';
import {
  Badge, Breadcrumb, Card, Chip, DrilldownProvider, Empty, Grid, Hero, Loading,
  Note, Panel, Section, Stat, useDrill,
} from './Kit';
import { Bubble, ColumnBars, Donut, FunnelView, RankBars, TreemapChart, Trend } from './Charts';
import DataTable from './DataTable';
import AnalyseModal from './AnalyseModal';
import PS4Clusters from './PS4Clusters';
import { BASE, ps4 as api } from './v2api';

const TYPES = ['GATE', 'TVM', 'VALIDATOR'];
const SEVERITIES = ['Critical', 'High', 'Normal'];
const SEV_TONE = { Critical: 'critical', High: 'serious', Normal: 'good' };

const VIEWS = [
  { key: 'overview', label: 'Fleet status' },
  { key: 'depots', label: 'Depots' },
  { key: 'devices', label: 'Devices' },
  { key: 'clusters', label: 'Peer groups' },
  { key: 'persistent', label: 'Repeat flags' },
  { key: 'evidence', label: 'How we know' },
];

const FEED_FN = {
  timeline: api.timeline,
  alerts: api.alerts,
  facility: api.facility,
  persistent: api.persistent,
  clusterProfile: api.clusterProfile,
  clusterQuality: api.clusterQuality,
  weekly: api.weekly,
  status: api.status,
};

const VIEW_FEEDS = {
  overview: ['timeline', 'alerts', 'clusterQuality'],
  depots: ['facility'],
  devices: ['alerts'],
  clusters: ['clusterQuality', 'clusterProfile', 'weekly'],
  persistent: ['persistent'],
  evidence: ['status', 'clusterQuality'],
};

const ALL_KEYS = Object.keys(FEED_FN);
const IS_OBJ = new Set(['status']);

function useFeeds(city) {
  const [feeds, setFeeds] = useState(
    () => Object.fromEntries(ALL_KEYS.map((k) => [k, { rows: [], obj: {}, loading: false, error: null, idle: true }]))
  );
  const [wanted, setWanted] = useState([]);
  const [nonce, setNonce] = useState(0);

  const request = useCallback((keys) => {
    setWanted((prev) => {
      const add = keys.filter((k) => !prev.includes(k));
      return add.length ? [...prev, ...add] : prev;
    });
  }, []);

  useEffect(() => {
    let alive = true;
    const todo = wanted.filter((k) => feeds[k] && feeds[k].idle);
    if (!todo.length) return undefined;
    setFeeds((s) => {
      const next = { ...s };
      todo.forEach((k) => { next[k] = { rows: [], obj: {}, loading: true, error: null, idle: false }; });
      return next;
    });
    let cursor = 0;
    const worker = async () => {
      for (;;) {
        const i = cursor;
        cursor += 1;
        if (i >= todo.length || !alive) return;
        const key = todo[i];
        let out = null;
        let error = null;
        try { out = await FEED_FN[key](city); }
        catch (e) { error = String((e && e.message) || e); }
        if (!alive) return;
        setFeeds((s) => ({
          ...s,
          [key]: IS_OBJ.has(key)
            ? { rows: [], obj: out || {}, loading: false, error, idle: false }
            : { rows: out || [], obj: {}, loading: false, error, idle: false },
        }));
      }
    };
    Promise.all([worker(), worker(), worker()]);
    return () => { alive = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [wanted, city, nonce]);

  const refetch = useCallback((key) => {
    setFeeds((s) => ({ ...s, [key]: { rows: [], obj: {}, loading: false, error: null, idle: true } }));
    setNonce((n) => n + 1);
  }, []);

  const refresh = useCallback(() => {
    setFeeds((s) => {
      const next = { ...s };
      Object.keys(next).forEach((k) => { next[k] = { rows: [], obj: {}, loading: false, error: null, idle: true }; });
      return next;
    });
    setNonce((n) => n + 1);
  }, []);

  return [feeds, refresh, request, refetch];
}

function Feed({ feed, height = 200, empty = 'Nothing to show for the current selection.', onRetry, children }) {
  if (!feed) return <Empty height={height}>{empty}</Empty>;
  if (feed.loading || feed.idle) return <Loading height={height} />;
  if (feed.error) {
    return (
      <div style={{ height, display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', gap: 10, border: `1px dashed ${LINE}`, borderRadius: 10, padding: '0 20px', textAlign: 'center' }}>
        <span style={{ display: 'inline-flex', alignItems: 'center', gap: 8, color: STATUS.warning.fill, fontSize: 12.5 }}>
          <TriangleAlert size={15} /> This panel timed out.
        </span>
        {onRetry && (
          <button type="button" onClick={onRetry}
                  style={{ display: 'inline-flex', alignItems: 'center', gap: 6, border: `1px solid ${LINE}`, background: '#FFF', color: INK_2, borderRadius: 9, padding: '6px 13px', fontSize: 12, fontWeight: 600, cursor: 'pointer' }}>
            <RefreshCw size={13} /> Try again
          </button>
        )}
      </div>
    );
  }
  if (!feed.rows.length && !Object.keys(feed.obj || {}).length) return <Empty height={height}>{empty}</Empty>;
  return children;
}

export default function PS4Overview({ city = 'CHI' }) {
  return (
    <DrilldownProvider rootLabel="Whole fleet">
      <Inner city={city} />
    </DrilldownProvider>
  );
}

function Inner({ city }) {
  const [F, refresh, request, refetch] = useFeeds(city);
  const { scope, push, reset } = useDrill();
  const [view, setView] = useState('overview');
  const [types, setTypes] = useState([]);
  const [sevs, setSevs] = useState([]);
  const [analyse, setAnalyse] = useState(null);

  useEffect(() => { request(VIEW_FEEDS[view] || []); }, [view, request]);

  const typeOn = useCallback(
    (t) => !types.length || types.includes(String(t || '').toUpperCase()),
    [types]
  );

  // ---- weeks and the CURRENT week -----------------------------------
  const weeks = useMemo(() => {
    const set = new Set((F.timeline.rows || []).map((r) => r.week_start));
    return [...set].sort();
  }, [F.timeline.rows]);
  const latestWeek = weeks.length ? weeks[weeks.length - 1] : null;

  // ---- FLEET TOTALS. timeline only. Never /ps4/weekly. ---------------
  const fleet = useMemo(() => {
    const rows = (F.timeline.rows || []).filter((r) => r.week_start === latestWeek && typeOn(r.device_type)
      && (!scope.device_type || String(r.device_type).toUpperCase() === String(scope.device_type).toUpperCase()));
    const t = rows.reduce((a, r) => ({
      observed: a.observed + (Number(r.devices_observed) || 0),
      actionable: a.actionable + (Number(r.actionable_devices) || 0),
      candidateDays: a.candidateDays + (Number(r.candidate_device_days) || 0),
    }), { observed: 0, actionable: 0, candidateDays: 0 });
    t.share = t.observed ? t.actionable / t.observed : 0;
    return t;
  }, [F.timeline.rows, latestWeek, typeOn, scope.device_type]);

  const byType = useMemo(() => (F.timeline.rows || [])
    .filter((r) => r.week_start === latestWeek && typeOn(r.device_type))
    .map((r) => ({
      type: r.device_type,
      name: deviceShort(r.device_type),
      observed: Number(r.devices_observed) || 0,
      actionable: Number(r.actionable_devices) || 0,
      share: Number(r.actionable_share) || 0,
      meanScore: Number(r.mean_anomaly_score) || 0,
    })), [F.timeline.rows, latestWeek, typeOn]);

  // Week over week, per fleet. Two weeks so far; the chart grows on its own.
  const trend = useMemo(() => weeks.map((w) => {
    const o = { week: dfmt(w).replace(/ \d{4}$/, '') };
    TYPES.forEach((t) => {
      const hit = (F.timeline.rows || []).find((r) => r.week_start === w && String(r.device_type).toUpperCase() === t);
      if (hit) o[deviceShort(t)] = Number(hit.actionable_devices) || 0;
    });
    return o;
  }), [weeks, F.timeline.rows]);

  // ---- the alert work list ------------------------------------------
  const alerts = useMemo(() => (F.alerts.rows || []).filter((r) => {
    if (!typeOn(r.device_type)) return false;
    if (scope.device_type && String(r.device_type).toUpperCase() !== String(scope.device_type).toUpperCase()) return false;
    if (scope.facility_id && String(r.facility_id) !== String(scope.facility_id)) return false;
    if (sevs.length && !sevs.includes(r.severity)) return false;
    return true;
  }), [F.alerts.rows, typeOn, scope, sevs]);

  // THE OVERVIEW IS ONE WEEK. The hero says "572 flagged this week"; a
  // severity donut counting 1,108 flags across both scored weeks next to it
  // is two denominators on one screen, and it is the kind of thing a client
  // spots immediately. Everything on Fleet status is the latest week.
  // The Devices tab keeps every week on purpose and carries a Week column.
  const alertsThisWeek = useMemo(
    () => alerts.filter((r) => !latestWeek || r.week_start === latestWeek),
    [alerts, latestWeek]
  );

  const sevMix = useMemo(() => SEVERITIES
    .map((s) => ({ name: s, value: alertsThisWeek.filter((r) => r.severity === s).length }))
    .filter((d) => d.value > 0), [alertsThisWeek]);

  // anomaly_types arrives as a concatenated string like
  // "event_spc+latency+oos+cluster_tail+none". Split, drop the "none"
  // placeholder, and count each distinct signal once per alert.
  const triggers = useMemo(() => {
    const c = {};
    alertsThisWeek.forEach((r) => {
      const seen = new Set(String(r.anomaly_types || '').split('+').map((x) => x.trim()).filter((x) => x && x !== 'none'));
      seen.forEach((k) => { c[k] = (c[k] || 0) + 1; });
    });
    const LABEL = {
      cluster_tail: 'Unlike its peer group',
      event_spc: 'Event rate out of control',
      oos: 'Out of service',
      latency: 'Slow response',
      hardware: 'Hardware fault logged',
    };
    return Object.entries(c)
      .map(([k, v]) => ({ name: LABEL[k] || k, value: v }))
      .sort((a, b) => b.value - a.value);
  }, [alertsThisWeek]);

  // ---- depots ---------------------------------------------------------
  const depots = useMemo(() => {
    const by = {};
    (F.facility.rows || []).forEach((r) => {
      if (!typeOn(r.device_type)) return;
      const k = String(r.facility_id);
      by[k] = by[k] || { facility_id: k, name: `Facility ${k}`, devices: 0, actionable: 0, ratio: 0, n: 0 };
      by[k].devices += Number(r.devices) || 0;
      by[k].actionable += Number(r.actionable_devices) || 0;
      by[k].ratio += Number(r.mean_distance_ratio) || 0;
      by[k].n += 1;
    });
    return Object.values(by)
      .map((d) => ({ ...d, ratio: d.n ? d.ratio / d.n : 0, share: d.devices ? d.actionable / d.devices : 0 }))
      .filter((d) => (scope.facility_id ? d.facility_id === String(scope.facility_id) : true))
      .sort((a, b) => b.actionable - a.actionable || b.devices - a.devices);
  }, [F.facility.rows, typeOn, scope.facility_id]);

  // ---- peer groups ----------------------------------------------------
  const quality = useMemo(() => (F.clusterQuality.rows || []).filter((r) => typeOn(r.device_type)), [F.clusterQuality.rows, typeOn]);
  const profile = useMemo(() => (F.clusterProfile.rows || []).filter((r) => typeOn(r.device_type)), [F.clusterProfile.rows, typeOn]);

  const persistent = useMemo(() => (F.persistent.rows || []).filter((r) => typeOn(r.device_type)), [F.persistent.rows, typeOn]);

  if (!BASE) {
    return <Card><Empty height={160}>No API base URL is configured for this build (VITE_API_BASE_URL).</Empty></Card>;
  }

  const funnel = [
    { name: 'Devices observed', value: fleet.observed, fill: CAT[0] },
    { name: 'Flagged this week', value: fleet.actionable, fill: CAT[2] },
    { name: 'Critical severity', value: alertsThisWeek.filter((r) => r.severity === 'Critical').length, fill: STATUS.critical.fill },
  ];

  return (
    <div>
      <Card pad="12px 14px" style={{ marginBottom: 14 }}>
        <div style={{ display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap' }}>
          <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
            {TYPES.map((t) => (
              <Chip key={t} color={deviceColor(t)} active={types.includes(t)}
                    onClick={() => setTypes((s) => (s.includes(t) ? s.filter((x) => x !== t) : [...s, t]))}>
                {deviceShort(t)}
              </Chip>
            ))}
          </div>
          <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
            {SEVERITIES.map((s) => (
              <Chip key={s} color={STATUS[SEV_TONE[s]].fill} active={sevs.includes(s)}
                    onClick={() => setSevs((x) => (x.includes(s) ? x.filter((y) => y !== s) : [...x, s]))}>
                {s}
              </Chip>
            ))}
          </div>
          <span style={{ ...font.micro, marginLeft: 4 }}>
            {latestWeek ? `Week of ${dfmt(latestWeek)}` : ''}
          </span>
          <button type="button" onClick={() => { setTypes([]); setSevs([]); reset(); refresh(); }}
                  style={{ marginLeft: 'auto', display: 'inline-flex', alignItems: 'center', gap: 6, border: `1px solid ${LINE}`, background: '#FFF', color: INK_2, borderRadius: 9, padding: '7px 12px', fontSize: 12, fontWeight: 600, cursor: 'pointer' }}>
            <RefreshCw size={13} /> Reset
          </button>
        </div>
      </Card>

      <div style={{ display: 'flex', gap: 4, borderBottom: `1px solid ${LINE}`, marginBottom: 18, overflowX: 'auto' }}>
        {VIEWS.map((v) => {
          const on = view === v.key;
          return (
            <button key={v.key} type="button" onClick={() => setView(v.key)}
                    style={{
                      border: 'none', background: 'none', cursor: 'pointer', padding: '9px 15px 11px',
                      borderBottom: `2px solid ${on ? INK : 'transparent'}`, marginBottom: -1,
                      color: on ? INK : INK_2, fontSize: 13.5, fontWeight: on ? 700 : 500, whiteSpace: 'nowrap',
                    }}>
              {v.label}
            </button>
          );
        })}
      </div>

      <Breadcrumb />

      {/* ================= FLEET STATUS ============================== */}
      {view === 'overview' && (
        <>
          <Section eyebrow="Which devices are behaving unusually" title="Fleet status"
                   sub={latestWeek
                     ? `Week of ${dfmt(latestWeek)}. A device is flagged when its behaviour stands apart from devices of the same type for enough of the week to be worth a look.`
                     : 'Loading the latest scored week.'}>
            <Grid cols="minmax(300px,1.1fr) minmax(320px,1fr)" style={{ marginBottom: 14 }}>
              {F.timeline.loading || F.timeline.idle ? (
                <Card><Loading height={140} label="Loading the week" /></Card>
              ) : (
                <Hero label="Devices flagged this week"
                      value={nfmt(fleet.actionable)}
                      unit={`of ${nfmt(fleet.observed)} observed`}
                      sub={`${pct(fleet.share)} of the fleet. This measures how unlike its peers a device is behaving - it is not a prediction that the device will fail.`} />
              )}
              <Panel title="From fleet to shortlist" hint="Each stage is a subset of the one above it.">
                <Feed feed={F.timeline} height={190} onRetry={() => refetch('timeline')}>
                  <FunnelView data={funnel} height={190} />
                </Feed>
              </Panel>
            </Grid>

            <Feed feed={F.timeline} height={90} onRetry={() => refetch('timeline')}>
              <Grid cols="repeat(auto-fit,minmax(210px,1fr))">
                {byType.map((t) => (
                  <Stat key={t.type} label={`${deviceName(t.type)} (${t.type})`}
                        value={nfmt(t.actionable)} unit={`of ${nfmt(t.observed)}`}
                        tone={t.share > 0.2 ? 'critical' : t.share > 0.1 ? 'warning' : 'good'}
                        foot={`${pct(t.share)} of that fleet`}
                        onClick={() => push(deviceShort(t.type), { device_type: t.type })} />
                ))}
              </Grid>
            </Feed>
          </Section>

          <Section eyebrow="Macro" title="What the week looks like">
            <Grid cols="repeat(auto-fit,minmax(320px,1fr))">
              <Panel title="Severity of the flags" hint="This week only. Critical means the behaviour was extreme, not that failure is imminent.">
                <Feed feed={F.alerts} height={220} onRetry={() => refetch('alerts')}>
                  <Donut data={sevMix} height={220}
                         colors={(d) => STATUS[SEV_TONE[d.name] || 'neutral'].fill}
                         centerValue={nfmt(alertsThisWeek.length)} centerLabel="flags this week" />
                </Feed>
              </Panel>

              <Panel title="What triggered the flag"
                     hint="This week only. One device can trip more than one signal; each is counted once.">
                <Feed feed={F.alerts} height={240} onRetry={() => refetch('alerts')}>
                  <RankBars data={triggers} xKey="value" yKey="name" height={240} color={CAT[2]} unit="Flags" />
                </Feed>
              </Panel>

              <Panel title="Flagged devices week over week" hint="Counts per fleet, by week scored.">
                <Feed feed={F.timeline} height={220} onRetry={() => refetch('timeline')}>
                  <ColumnBars data={trend} xKey="week" height={220}
                              series={TYPES.map((t) => ({ key: deviceShort(t), label: deviceShort(t), color: deviceColor(t) }))} />
                </Feed>
              </Panel>

              <Panel title="Can we trust the peer groups?"
                     hint="Each fleet is clustered separately. The verdict below is the model's own, not an interpretation.">
                <Feed feed={F.clusterQuality} height={200} onRetry={() => refetch('clusterQuality')}>
                  <div style={{ display: 'grid', gap: 10 }}>
                    {quality.map((q) => (
                      <div key={q.device_type} style={{ border: `1px solid ${LINE}`, borderRadius: 10, padding: '10px 12px' }}>
                        <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 4 }}>
                          <span style={{ width: 8, height: 8, borderRadius: 4, background: deviceColor(q.device_type) }} />
                          <strong style={{ fontSize: 13, color: INK }}>{deviceShort(q.device_type)}</strong>
                          <span style={{ ...font.num, fontSize: 13, color: INK_2 }}>{Number(q.silhouette).toFixed(3)}</span>
                          <Badge tone={Number(q.silhouette) >= 0.5 ? 'good' : 'warning'}>{q.separation_verdict}</Badge>
                        </div>
                        <p style={{ ...font.note, fontSize: 12, margin: 0 }}>{q.separation_note}</p>
                      </div>
                    ))}
                  </div>
                </Feed>
              </Panel>
            </Grid>
          </Section>
        </>
      )}

      {/* ================= DEPOTS ==================================== */}
      {view === 'depots' && (
        <Section eyebrow="Where the flags are" title="Depots and stations"
                 sub="Devices flagged per facility. Click a depot to narrow the other tabs to it.">
          <Grid cols="minmax(340px,1.1fr) minmax(320px,1fr)" style={{ marginBottom: 14 }}>
            <Panel title="Depots by flagged devices" hint="Click a bar to drill in.">
              <Feed feed={F.facility} height={320} onRetry={() => refetch('facility')}>
                <RankBars data={depots.slice(0, 14).map((d) => ({ name: d.name, value: d.actionable, facility_id: d.facility_id }))}
                          xKey="value" yKey="name" height={320} color={CAT[2]} unit="Flagged"
                          onDrill={(row) => row && push(row.name, { facility_id: row.facility_id })} />
              </Feed>
            </Panel>
            <Panel title="Fleet size by depot" hint="Tile area is devices observed; click to drill in.">
              <Feed feed={F.facility} height={320} onRetry={() => refetch('facility')}>
                <TreemapChart data={depots.slice(0, 26).map((d) => ({ name: d.name, value: d.devices, facility_id: d.facility_id }))}
                              height={320} colors={(d, i) => CAT[i % CAT.length]}
                              onDrill={(row) => row && row.facility_id && push(row.name, { facility_id: row.facility_id })} />
              </Feed>
            </Panel>
          </Grid>

          <Panel title="Size against how unusual" hint="Depots to the upper right have both many devices and unusually behaving ones.">
            <Feed feed={F.facility} height={300} onRetry={() => refetch('facility')}>
              <Bubble data={depots.map((d) => ({ ...d, x: d.devices, y: Number((d.ratio).toFixed(3)), z: Math.max(1, d.actionable) }))}
                      xKey="x" yKey="y" zKey="z" nameKey="name"
                      xLabel="Devices at the depot" yLabel="Average distance from peer group"
                      height={300} colorBy={() => CAT[2]}
                      onDrill={(d) => d && d.facility_id && push(d.name, { facility_id: d.facility_id })} />
            </Feed>
          </Panel>

          <div style={{ marginTop: 14 }}>
            <Feed feed={F.facility} height={200} onRetry={() => refetch('facility')}>
              <DataTable rows={depots} height={360} pageSize={100} exportName={`ps4-depots-${city}`}
                         onRowClick={(r) => push(r.name, { facility_id: r.facility_id })}
                         columns={[
                           { key: 'facility_id', label: 'Depot', width: 120 },
                           { key: 'devices', label: 'Devices', num: true, width: 100 },
                           { key: 'actionable', label: 'Flagged', num: true, width: 100 },
                           { key: 'share', label: 'Share flagged', num: true, width: 130, render: (r) => pct(r.share) },
                           { key: 'ratio', label: 'Distance from peers', num: true, width: 160, d: 3 },
                         ]} />
            </Feed>
          </div>
        </Section>
      )}

      {/* ================= DEVICES =================================== */}
      {view === 'devices' && (
        <Section eyebrow="The shortlist" title="Flagged devices"
                 sub="Every device-week the model flagged as worth a look, across all scored weeks. Use the Week column to read a single week; Fleet status is the latest week only.">
          <Note>
            A flag means the device behaved unlike its peers for enough of the week to stand out. It is not a
            probability of failure, and the score is not a percentage of anything.
          </Note>
          <div style={{ marginTop: 14 }}>
            <Feed feed={F.alerts} height={300} onRetry={() => refetch('alerts')}>
              <DataTable
                rows={alerts.slice().sort((a, b) => Number(b.cluster_distance_ratio_max) - Number(a.cluster_distance_ratio_max))}
                height={520} pageSize={250} exportName={`ps4-flags-${city}`}
                onRowClick={(r) => setAnalyse(r.device_id)}
                rowKey={(r, i) => `${r.device_id}-${r.week_start}-${i}`}
                columns={[
                  { key: 'device_id', label: 'Device', width: 130, render: (r) => <span style={{ fontFamily: 'ui-monospace,monospace', fontWeight: 700, color: INK }}>{r.device_id}</span> },
                  { key: 'device_type', label: 'Fleet', width: 140, render: (r) => (
                    <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
                      <span style={{ width: 8, height: 8, borderRadius: 4, background: deviceColor(r.device_type) }} />
                      {deviceShort(r.device_type)}
                    </span>) },
                  { key: 'facility_id', label: 'Depot', width: 90 },
                  { key: 'week_start', label: 'Week', width: 110, render: (r) => dfmt(r.week_start).replace(/ \d{4}$/, '') },
                  { key: 'severity', label: 'Severity', width: 110, render: (r) => <Badge tone={SEV_TONE[r.severity] || 'neutral'}>{r.severity}</Badge> },
                  { key: 'actionable_days', label: 'Days flagged', num: true, width: 120 },
                  { key: 'cluster_distance_ratio_max', label: 'Distance from peers', num: true, width: 160, d: 2 },
                ]} />
            </Feed>
          </div>
        </Section>
      )}

      {/* ================= PEER GROUPS =============================== */}
      {view === 'clusters' && (
        <Section eyebrow="How the comparison is made" title="Peer groups"
                 sub="Each fleet is clustered on its own behaviour. A device is compared only with devices of its own type.">
          <Feed feed={F.clusterQuality} height={160} onRetry={() => refetch('clusterQuality')}>
            <Grid cols="repeat(auto-fit,minmax(300px,1fr))" style={{ marginBottom: 14 }}>
              {quality.map((q) => (
                <Card key={q.device_type} style={{ borderLeft: `3px solid ${deviceColor(q.device_type)}` }}>
                  <div style={{ ...font.micro, marginBottom: 6 }}>{deviceName(q.device_type)}</div>
                  <div style={{ display: 'flex', alignItems: 'baseline', gap: 8 }}>
                    <span style={{ ...font.hero, ...font.num, fontSize: 28 }}>{Number(q.silhouette).toFixed(3)}</span>
                    <Badge tone={Number(q.silhouette) >= 0.5 ? 'good' : 'warning'}>{q.separation_verdict}</Badge>
                  </div>
                  <p style={{ ...font.note, fontSize: 12, margin: '8px 0 0' }}>{q.separation_note}</p>
                  <p style={{ ...font.note, fontSize: 11.5, margin: '6px 0 0', color: INK_3 }}>
                    Built from {nfmt(q.train_rows)} device-days - figure from the {q.quality_source}.
                  </p>
                </Card>
              ))}
            </Grid>
          </Feed>

          <Feed feed={F.clusterProfile} height={280} onRetry={() => refetch('clusterProfile')}>
            <PS4Clusters quality={quality} profile={profile} weeklyFeed={F.weekly}
                         typeOn={typeOn} onDevice={setAnalyse} />
          </Feed>

          <div style={{ height: 14 }} />

          <Grid cols="minmax(340px,1fr) minmax(340px,1fr)">
            <Panel title="How often each group is flagged"
                   hint="The share of scored device-days in each peer group that ended up actionable.">
              <Feed feed={F.clusterProfile} height={280} onRetry={() => refetch('clusterProfile')}>
                <RankBars data={profile.map((p) => ({
                  name: `${deviceShort(p.device_type)} - group ${p.cluster_id}`,
                  value: Number((Number(p.actionable_rate) * 100).toFixed(2)),
                  type: p.device_type,
                }))} xKey="value" yKey="name" height={280} unit="%"
                  colorBy={(d) => deviceColor(d.type)} fmt={(v) => `${v}%`} />
              </Feed>
            </Panel>

            <Panel title="How far each group sits from its own centre"
                   hint="Average distance against the 99th percentile seen during training. Above the training line is genuinely unusual.">
              <Feed feed={F.clusterProfile} height={280} onRetry={() => refetch('clusterProfile')}>
                <ColumnBars data={profile.map((p) => ({
                  name: `${deviceShort(p.device_type)} g${p.cluster_id}`,
                  'Average now': Number(Number(p.mean_cluster_distance).toFixed(2)),
                  'Training 99th pct': Number(Number(p.train_cluster_distance_p99).toFixed(2)),
                }))} xKey="name" height={280}
                  series={[
                    { key: 'Average now', label: 'Average now', color: CAT[2] },
                    { key: 'Training 99th pct', label: 'Training 99th pct', color: INK_3 },
                  ]} />
              </Feed>
            </Panel>
          </Grid>

          <div style={{ marginTop: 14 }}>
            <Feed feed={F.clusterProfile} height={200} onRetry={() => refetch('clusterProfile')}>
              <DataTable rows={profile} height={300} pageSize={50} searchable={false} exportName={`ps4-clusters-${city}`}
                         columns={[
                           { key: 'device_type', label: 'Fleet', width: 150, render: (r) => deviceShort(r.device_type) },
                           { key: 'cluster_id', label: 'Group', num: true, width: 80 },
                           { key: 'scored_device_days', label: 'Device-days', num: true, width: 130 },
                           { key: 'train_cluster_share', label: 'Share of training', num: true, width: 150, render: (r) => pct(r.train_cluster_share) },
                           { key: 'candidate_rate', label: 'Candidate rate', num: true, width: 140, render: (r) => pct(r.candidate_rate, 2) },
                           { key: 'actionable_rate', label: 'Flagged rate', num: true, width: 130, render: (r) => pct(r.actionable_rate, 2) },
                         ]} />
            </Feed>
          </div>

          <Note>
            Where separation is weak - bus validators at 0.459 - prioritise on distance from the group centre rather
            than on which group a device belongs to. The boundaries there are soft.
          </Note>
        </Section>
      )}

      {/* ================= REPEAT FLAGS ============================== */}
      {view === 'persistent' && (
        <Section eyebrow="Not a one-off" title="Repeat flags"
                 sub="Devices flagged in more than one week. A single odd week can be noise; a repeat is a pattern.">
          <Grid cols="minmax(340px,1fr) minmax(340px,1fr)" style={{ marginBottom: 14 }}>
            <Panel title="Worst repeat offenders" hint="Ranked by the furthest they strayed from their peer group.">
              <Feed feed={F.persistent} height={300} onRetry={() => refetch('persistent')}>
                <RankBars data={persistent.slice()
                  .sort((a, b) => Number(b.worst_distance_ratio) - Number(a.worst_distance_ratio))
                  .slice(0, 14)
                  .map((r) => ({ name: r.device_id, value: Number(Number(r.worst_distance_ratio).toFixed(2)), type: r.device_type, device_id: r.device_id }))}
                  xKey="value" yKey="name" height={300} unit="Distance"
                  colorBy={(d) => deviceColor(d.type)}
                  onDrill={(row) => row && row.device_id && setAnalyse(row.device_id)} />
              </Feed>
            </Panel>
            <Panel title="How many weeks in a row" hint="Devices grouped by the number of weeks they were flagged.">
              <Feed feed={F.persistent} height={300} onRetry={() => refetch('persistent')}>
                <ColumnBars
                  data={[...new Set(persistent.map((r) => Number(r.actionable_weeks)))].sort((a, b) => a - b)
                    .map((w) => ({ name: `${w} week${w === 1 ? '' : 's'}`, Devices: persistent.filter((r) => Number(r.actionable_weeks) === w).length }))}
                  xKey="name" height={300} series={[{ key: 'Devices', label: 'Devices', color: CAT[2] }]} />
              </Feed>
            </Panel>
          </Grid>

          <Feed feed={F.persistent} height={200} onRetry={() => refetch('persistent')}>
            <DataTable rows={persistent.slice().sort((a, b) => Number(b.actionable_weeks) - Number(a.actionable_weeks) || Number(b.worst_distance_ratio) - Number(a.worst_distance_ratio))}
                       height={420} pageSize={200} exportName={`ps4-repeat-${city}`}
                       onRowClick={(r) => setAnalyse(r.device_id)}
                       columns={[
                         { key: 'device_id', label: 'Device', width: 130, render: (r) => <span style={{ fontFamily: 'ui-monospace,monospace', fontWeight: 700, color: INK }}>{r.device_id}</span> },
                         { key: 'device_type', label: 'Fleet', width: 140, render: (r) => deviceShort(r.device_type) },
                         { key: 'facility_id', label: 'Depot', width: 90 },
                         { key: 'actionable_weeks', label: 'Weeks flagged', num: true, width: 130 },
                         { key: 'severities', label: 'Severity seen', width: 140 },
                         { key: 'worst_distance_ratio', label: 'Worst distance', num: true, width: 140, d: 2 },
                       ]} />
          </Feed>
        </Section>
      )}

      {/* ================= HOW WE KNOW =============================== */}
      {view === 'evidence' && (
        <Section eyebrow="What this model is and is not" title="How we know this"
                 sub="PS4 is unsupervised. It was never shown a failure and never learned what one looks like.">
          <Card style={{ marginBottom: 14, borderLeft: `3px solid ${CAT[2]}` }}>
            <p style={{ ...font.body, margin: 0 }}>
              PS4 answers one question: <strong>is this device behaving unlike others of its own type?</strong> It
              does not estimate a chance of failure, and no figure on these tabs is a probability. Where a device is
              flagged and PS1 also rates it high risk, the two agree by coincidence of evidence, not by design.
            </p>
          </Card>

          <Feed feed={F.clusterQuality} height={160} onRetry={() => refetch('clusterQuality')}>
            <Panel title="Separation of the peer groups"
                   hint="Above 0.50 the groups can be named and acted on. Below it, use distance instead of membership.">
              <DataTable rows={quality.map((q) => ({
                fleet: deviceShort(q.device_type),
                silhouette: Number(q.silhouette),
                verdict: q.separation_verdict,
                train_rows: Number(q.train_rows),
                source: q.quality_source,
                asof: q.asof_date,
              }))} height={200} pageSize={20} searchable={false}
                columns={[
                  { key: 'fleet', label: 'Fleet' },
                  { key: 'silhouette', label: 'Separation', num: true, d: 3 },
                  { key: 'verdict', label: 'Verdict' },
                  { key: 'train_rows', label: 'Device-days used', num: true },
                  { key: 'source', label: 'Figure from' },
                  { key: 'asof', label: 'Scored to' },
                ]} />
            </Panel>
          </Feed>

          <div style={{ marginTop: 14 }}>
            <Feed feed={F.status} height={200} onRetry={() => refetch('status')}>
              <Grid cols="repeat(auto-fit,minmax(300px,1fr))">
                <Panel title="What was loaded" hint="Row counts in the tables behind these tabs.">
                  <DataTable rows={(F.status.obj.tables || [])} height={230} pageSize={20} searchable={false}
                             columns={[
                               { key: 'table_name', label: 'Table' },
                               { key: 'n_rows', label: 'Rows', num: true, width: 110 },
                             ]} />
                </Panel>
                <Panel title="Reconciliation" hint="What the notebook published against what landed in the database.">
                  <DataTable rows={(F.status.obj.reconcile || [])} height={230} pageSize={20} searchable={false}
                             columns={Object.keys((F.status.obj.reconcile || [{}])[0] || {})
                               .filter((k) => k !== 'city_id' && k !== 'pipeline_version')
                               .slice(0, 5)
                               .map((k) => ({ key: k, label: k.replace(/_/g, ' '), num: typeof ((F.status.obj.reconcile || [{}])[0] || {})[k] === 'number' }))} />
                </Panel>
              </Grid>
            </Feed>
          </div>
        </Section>
      )}

      {analyse && <AnalyseModal city={city} deviceId={analyse} onClose={() => setAnalyse(null)} />}
    </div>
  );
}
