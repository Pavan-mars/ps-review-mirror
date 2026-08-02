// =====================================================================
// v2/PS2Overview.jsx -- PS2 (cascading / governed hardware-OOS) in the v2 shape.
//
// WHAT THIS TAB IS. PS2 has no served model. The notebook runs on a schedule
// and its 20 tables are refreshed wholesale into Aurora, so this screen is an
// ANALYSIS surface, not a work queue. Verb tense throughout is "what the
// analysis found", not "what to do today".
//
// THE HEADLINE, AND WHY IT IS NOT WHAT YOU EXPECT.
// The obvious hero would be validated failure onsets. Measured on the real
// export, 165,750 of 172,576 of them are validators:
//     GATE        548,644 OOS onsets ->     284 validated  (0.05%)
//     TVM       2,788,508            ->   6,542            (0.23%)
//     VALIDATOR 5,987,966            -> 165,750            (2.77%)
// A hero tile on that number tells a reader 825 fare gates produced 284
// failures in seven months. That is a statement about evidence coverage, not
// about gates. So the hero is MEASURED IMPACT -- outage hours, devices, and
// customer exposure, all counted rather than inferred and all comparable
// across fleets -- and the evidence ladder sits directly beneath it so the
// coverage difference is visible instead of hidden inside an average.
//
// WHAT IS DELIBERATELY NOT ON THE FIRST SCREEN.
// The will_hardware_oos_3d label prevalence (GATE 80.0%, TVM 89.7%,
// VALIDATOR 35.6%) and the PS1 model scorecard live in "How we know". They
// are real and they matter, but "89.7% of ticket machines will fail within
// three days" is a property of the label rule, not a finding about the fleet.
//
// DATA VINTAGE. Every number here is as of 2026-04-11 -- the client's 24-month
// extract ends there. The header states it. Do not let a reader infer today.
//
// THREE COLUMNS ARE NULL IN THE CURRENT EXPORT, verified against the live API
// rather than assumed:
//   hardware_component_description, hardware_source  -- null on all 78 serials
//   observed_group_devices, cofailure_share          -- populated ONLY for the
//                                                       GATE_ARRAY cluster scope
// Panels that would depend on them say so rather than rendering blank.
// =====================================================================
import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { getObj, getRows } from './v2api';
import {
  Badge, Breadcrumb, Card, Chip, DrilldownProvider, Empty, Grid, Hero, Legend,
  Loading, Note, Panel, Section, Stat, useDrill,
} from './Kit';
import { ChartFrame, ColumnBars, Donut, Matrix, RankBars, Trend } from './Charts';
import DataTable from './DataTable';
import {
  CAT, CARD, INK, INK_2, INK_3, LINE, STATUS,
  compact, deviceColor, deviceShort, dfmt, font, nfmt, pct,
} from './theme';

const FLEETS = ['GATE', 'TVM', 'VALIDATOR'];

// ---------------------------------------------------------------------
// Feeds. One entry per route. Limits are the route's own hard caps where the
// table is large -- clusters is 83,184 rows and repairs 38,395, so both are
// browse windows and neither is ever used as a denominator.
// ---------------------------------------------------------------------
const FEED_FN = {
  status:        (city) => getObj('/ps2/status', { city }).then((o) => [o]),
  trend:         (city) => getRows('/ps2/v25/oos-trend', { city, limit: 5000 }),
  exposure:      (city) => getRows('/ps2/v25/exposure', { city, limit: 5000 }),
  governance:    (city) => getRows('/ps2/v25/governance', { city }),
  labelSummary:  (city) => getRows('/ps2/v25/label-summary', { city }),
  clusters:      (city) => getRows('/ps2/v25/clusters', { city, limit: 5000 }),
  deterioration: (city) => getRows('/ps2/v25/deterioration', { city, limit: 5000 }),
  serials:       (city) => getRows('/ps2/v25/serials', { city, limit: 5000 }),
  repairs:       (city) => getRows('/ps2/v25/repairs', { city, limit: 5000 }),
  precursors:    (city) => getRows('/ps2/v25/precursors', { city, limit: 2000 }),
  leadlag:       (city) => getRows('/ps2/v25/leadlag', { city, limit: 2000 }),
  topology:      (city) => getRows('/ps2/v25/topology', { city }),
  drift:         (city) => getRows('/ps2/v25/drift', { city, limit: 2000 }),
  runQuality:    (city) => getRows('/ps2/v25/run-quality', { city }),
  labelDaily:    (city) => getRows('/ps2/v25/label-daily', { city, limit: 5000 }),
  labelHorizon:  (city) => getRows('/ps2/v25/label-horizon', { city }),
  parity:        (city) => getRows('/ps2/v25/label-parity', { city }),
  alignment:     (city) => getRows('/ps2/v25/definition-alignment', { city }),
  modelPerf:     (city) => getRows('/ps2/v25/model-performance', { city }),
  categories:    (city) => getRows('/ps2/v25/category-profile', { city }),
  crossPs:       (city) => getRows('/ps2/v25/cross-ps', { city }),
};
const ALL_KEYS = Object.keys(FEED_FN);

const VIEWS = [
  { key: 'impact',     label: 'Fleet impact',        feeds: ['trend', 'exposure', 'governance', 'labelSummary'] },
  { key: 'where',      label: 'Where it happens',    feeds: ['clusters'] },
  { key: 'devices',    label: 'Devices',             feeds: ['deterioration'] },
  { key: 'components', label: 'Components & repairs', feeds: ['serials', 'repairs'] },
  { key: 'cascades',   label: 'Cascades',            feeds: ['precursors', 'leadlag', 'topology', 'drift'] },
  { key: 'evidence',   label: 'How we know',         feeds: ['runQuality', 'labelSummary', 'labelDaily', 'labelHorizon', 'parity', 'alignment', 'modelPerf', 'categories', 'crossPs'] },
];

// ---------------------------------------------------------------------
// useFeeds -- per-feed {rows, loading, error}. Copied in shape from PS1/PS4
// deliberately: one Promise.all over every route made a single slow call block
// the page and a single failure empty it. Two workers at a time, because past
// three the queries start exceeding API Gateway's 30s cap rather than
// finishing sooner.
// ---------------------------------------------------------------------
// THE DEPENDENCY ARRAY HERE IS LOAD-BEARING. Do not add `feeds` to it.
//
// The first version of this hook did, and the tab loaded forever. setFeeds
// inside the effect mutates feeds, React runs the previous effect's cleanup
// before the next pass, the cleanup flipped an `alive` flag, and both workers
// returned at their guard -- before writing their results and before pulling
// the next key off the queue. Measured in the browser: exactly two requests
// fired (/ps2/status and /ps2/v25/oos-trend), both returned 200, and nothing
// ever rendered.
//
// So: which keys are already in flight lives in a ref, not in state, and
// cancellation is tied to unmount / city change alone rather than to every
// state update.
function useFeeds(city) {
  const [feeds, setFeeds] = useState(
    () => Object.fromEntries(ALL_KEYS.map((k) => [k, { rows: [], loading: false, error: null, idle: true }]))
  );
  const [wanted, setWanted] = useState([]);
  const started = useRef(new Set());
  const alive = useRef(true);

  // The ONLY thing that cancels in-flight work. Switching sub-tabs must not,
  // or a feed requested by the tab you just left stays loading forever.
  useEffect(() => {
    alive.current = true;
    return () => { alive.current = false; };
  }, [city]);

  const request = useCallback((keys) => {
    setWanted((prev) => {
      const add = keys.filter((k) => !prev.includes(k));
      return add.length ? [...prev, ...add] : prev;
    });
  }, []);

  useEffect(() => {
    // The ref also makes this idempotent under StrictMode's double-invoke:
    // the second pass finds every key already started and does nothing.
    const todo = wanted.filter((k) => !started.current.has(k));
    if (!todo.length) return;
    todo.forEach((k) => started.current.add(k));

    setFeeds((s) => {
      const next = { ...s };
      todo.forEach((k) => { next[k] = { rows: [], loading: true, error: null, idle: false }; });
      return next;
    });

    // TWO at a time. Past three, the queries start exceeding API Gateway's
    // 30s cap rather than finishing sooner.
    let cursor = 0;
    const worker = async () => {
      for (;;) {
        const i = cursor;
        cursor += 1;
        if (i >= todo.length) return;
        const key = todo[i];
        let rows = [];
        let error = null;
        try { rows = (await FEED_FN[key](city)) || []; }
        catch (e) { error = String((e && e.message) || e); }
        if (!alive.current) return;
        setFeeds((s) => ({ ...s, [key]: { rows, loading: false, error, idle: false } }));
      }
    };
    Promise.all([worker(), worker()]);
  }, [wanted, city]);

  return { feeds, request };
}

// A panel that knows the difference between "still loading", "the route
// failed" and "the table is genuinely empty". getRows() throws on a non-2xx
// precisely so this distinction survives -- three PS1 panels once reported a
// server timeout as a finding about the fleet.
function Feed({ feed, height = 240, children }) {
  if (!feed || feed.idle || feed.loading) return <Loading height={height} />;
  if (feed.error) {
    return (
      <div style={{ padding: '14px 4px' }}>
        <Badge tone="warning">Could not load</Badge>
        <div style={{ ...font.micro, marginTop: 8, color: INK_2 }}>{feed.error}</div>
      </div>
    );
  }
  if (!feed.rows || !feed.rows.length) return <Empty height={height} />;
  return children(feed.rows);
}

// ---------------------------------------------------------------------
// helpers
// ---------------------------------------------------------------------
const num = (v) => (v === null || v === undefined || Number.isNaN(Number(v)) ? 0 : Number(v));

// [{date, device_category, v}] -> [{date, GATE, TVM, VALIDATOR}]
function pivotByFleet(rows, dateKey, valKey) {
  const map = new Map();
  (rows || []).forEach((r) => {
    const d = r[dateKey];
    if (!map.has(d)) map.set(d, { [dateKey]: d, GATE: 0, TVM: 0, VALIDATOR: 0 });
    const cat = String(r.device_category || '').toUpperCase();
    if (FLEETS.includes(cat)) map.get(d)[cat] += num(r[valKey]);
  });
  return Array.from(map.values()).sort((a, b) => String(a[dateKey]).localeCompare(String(b[dateKey])));
}

function sumBy(rows, key, filter) {
  return (rows || []).reduce((t, r) => (filter && !filter(r) ? t : t + num(r[key])), 0);
}

const FLEET_SERIES = FLEETS.map((f) => ({ key: f, label: deviceShort(f), color: deviceColor(f) }));

function FleetChips({ value, onChange, extra = 'All fleets' }) {
  return (
    <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
      <Chip active={!value} onClick={() => onChange(null)}>{extra}</Chip>
      {FLEETS.map((f) => (
        <Chip key={f} active={value === f} onClick={() => onChange(f)} color={deviceColor(f)}>
          {deviceShort(f)}
        </Chip>
      ))}
    </div>
  );
}

// =====================================================================
// 1. FLEET IMPACT
// =====================================================================
function ImpactView({ feeds }) {
  const trend = feeds.trend.rows;
  const exposure = feeds.exposure.rows;
  const summary = feeds.labelSummary.rows;

  const totals = useMemo(() => {
    const all = (summary || []).find((r) => String(r.device_category).toUpperCase() === 'ALL') || {};
    const days = new Set((trend || []).map((r) => r.event_date)).size;
    const devices = num(all.eligible_devices);
    const hours = sumBy(trend, 'hardware_oos_minutes') / 60;
    // Capacity is the honest denominator: every device, every hour of the
    // window. Recorded OOS time above ~50% of it is not fleet availability,
    // it is episodes that never close -- validators come back at 95.1%.
    const capacity = devices * days * 24;
    return {
      capacity,
      share: capacity ? hours / capacity : 0,
      outageHours: hours,
      onsets: sumBy(trend, 'hardware_oos_onsets'),
      validated: sumBy(trend, 'validated_failure_onsets'),
      chargeable: sumBy(trend, 'chargeable_oos_onsets'),
      devices,
      devicesSeen: num(all.positive_devices),
      txn: sumBy(exposure, 'transactions_exposed'),
      revenue: sumBy(exposure, 'revenue_cents_exposed') / 100,
      days,
    };
  }, [trend, exposure, summary]);

  const byFleet = useMemo(() => {
    const days = new Set((trend || []).map((r) => r.event_date)).size;
    return FLEETS.map((f) => {
      const m = (r) => String(r.device_category).toUpperCase() === f;
      const onsets = sumBy(trend, 'hardware_oos_onsets', m);
      const validated = sumBy(trend, 'validated_failure_onsets', m);
      const hours = sumBy(trend, 'hardware_oos_minutes', m) / 60;
      const row = (summary || []).find((r) => String(r.device_category).toUpperCase() === f);
      const cap = num(row && row.eligible_devices) * days * 24;
      return {
        fleet: deviceShort(f), code: f,
        onsets,
        validated,
        chargeable: sumBy(trend, 'chargeable_oos_onsets', m),
        hours,
        share: cap ? hours / cap : 0,
        coverage: onsets ? validated / onsets : 0,
      };
    });
  }, [trend, summary]);

  const minutesTrend = useMemo(() => pivotByFleet(trend, 'event_date', 'hardware_oos_minutes')
    .map((r) => ({ ...r, GATE: r.GATE / 60, TVM: r.TVM / 60, VALIDATOR: r.VALIDATOR / 60 })), [trend]);
  const onsetTrend = useMemo(() => pivotByFleet(trend, 'event_date', 'hardware_oos_onsets'), [trend]);

  const loading = feeds.trend.loading || feeds.exposure.loading || feeds.labelSummary.loading;

  return (
    <>
      <Section
        eyebrow="Measured impact"
        title="What the estate actually lost"
        sub="Counted from governed hardware-OOS episodes. Every figure below is measured, not modelled."
      >
        <Grid cols="repeat(auto-fit,minmax(220px,1fr))">
          <Hero
            label="Recorded out-of-service time"
            value={loading ? '--' : pct(totals.share, 1)}
            unit="of available device-hours"
            sub={loading ? '' : `${nfmt(totals.outageHours)} device-hours recorded against ${nfmt(totals.capacity)} available, across ${nfmt(totals.days)} days and ${nfmt(totals.devices)} devices`}
          />
          <Stat label="Devices in scope" value={loading ? '--' : nfmt(totals.devices)} foot="TVMs, fare gates and bus validators" />
          <Stat label="Transactions exposed" value={loading ? '--' : compact(totals.txn)} foot="during an open OOS episode" />
          <Stat
            label="Revenue exposed"
            value={loading ? '--' : `$${compact(totals.revenue)}`}
            foot="fare value on affected devices"
          />
        </Grid>
      </Section>

      {!loading && totals.share > 0.5 && (
        <Note>
          That figure is not fleet availability. Recorded out-of-service time above half of the
          calendar cannot describe a running transit system -- it is the signature of episodes that
          open and never close, so their duration accumulates until something else terminates them.
          Validators alone come back at {pct((byFleet.find((f) => f.code === 'VALIDATOR') || {}).share || 0, 1)}.
          Confirmable by counting Set against Clear events per fleet in the direct-Silver export; if
          Clears are materially fewer, the fix is in the episode-collapse rule rather than here.
        </Note>
      )}

      <Section eyebrow="Evidence" title="How much of this is confirmed">
        <Note>
          The three fleets do not carry the same evidence. Validators account for 96% of all
          validated failure onsets; fare gates for 0.2%. That is a difference in what the source
          systems record, not a difference in how the fleets behave -- so these columns are shown
          side by side rather than combined into one rate.
        </Note>
        <Card>
          <Feed feed={feeds.trend} height={260}>
            {() => (
              <ChartFrame
                rows={byFleet}
                cols={[
                  { key: 'fleet', label: 'Fleet' },
                  { key: 'onsets', label: 'OOS onsets', num: true },
                  { key: 'validated', label: 'Validated failures', num: true },
                  { key: 'chargeable', label: 'Chargeable', num: true },
                  { key: 'coverage', label: 'Validated share', num: true, d: 4 },
                  { key: 'share', label: 'Of available time', num: true, d: 4 },
                ]}
                height={260}
              >
                <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 12, padding: '4px 0' }}>
                  {byFleet.map((f) => (
                    <div key={f.code} style={{ border: `1px solid ${LINE}`, borderRadius: 12, padding: '12px 14px' }}>
                      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                        <span style={{ width: 8, height: 8, borderRadius: 4, background: deviceColor(f.code) }} />
                        <span style={{ fontSize: 13, fontWeight: 700, color: INK }}>{f.fleet}</span>
                      </div>
                      <div style={{ marginTop: 10, fontSize: 22, fontWeight: 700, color: INK }}>{nfmt(f.hours)}</div>
                      <div style={{ ...font.micro }}>device-hours out of service</div>
                      <div style={{ marginTop: 12, display: 'grid', gap: 4 }}>
                        <Row k="OOS onsets" v={nfmt(f.onsets)} />
                        <Row k="Validated failures" v={nfmt(f.validated)} />
                        <Row k="Chargeable events" v={nfmt(f.chargeable)} />
                        <Row k="Validated share" v={pct(f.coverage, 2)} tone={f.coverage < 0.01 ? 'warn' : 'normal'} />
                        <Row k="Of available time" v={pct(f.share, 1)} tone={f.share > 0.5 ? 'warn' : 'normal'} />
                      </div>
                    </div>
                  ))}
                </div>
              </ChartFrame>
            )}
          </Feed>
        </Card>
      </Section>

      <Grid cols="repeat(auto-fit,minmax(420px,1fr))">
        <Panel title="Out-of-service hours by day" hint="Episode duration attributed to the day the episode opened">
          <Feed feed={feeds.trend} height={260}>
            {() => <Trend data={minutesTrend} xKey="event_date" series={FLEET_SERIES} height={260} area fmt={(v) => nfmt(v)} />}
          </Feed>
        </Panel>
        <Panel title="Episode onsets by day" hint="A new hardware-OOS episode opening">
          <Feed feed={feeds.trend} height={260}>
            {() => <Trend data={onsetTrend} xKey="event_date" series={FLEET_SERIES} height={260} fmt={(v) => nfmt(v)} />}
          </Feed>
        </Panel>
      </Grid>

      <Panel
        title="Evidence ladder"
        hint="Every governed OOS event, classified by the evidence behind it"
      >
        <Feed feed={feeds.governance} height={260}>
          {(rows) => (
            <Matrix
              rows={rows}
              rowKey="oos_evidence_class"
              colKey="failure_evidence_class"
              valKey="event_count"
              rowLabel="OOS evidence"
              colLabel="Failure evidence"
              fmt={(v) => nfmt(v)}
              height={280}
            />
          )}
        </Feed>
      </Panel>
    </>
  );
}

function Row({ k, v, tone }) {
  return (
    <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 12 }}>
      <span style={{ color: INK_2 }}>{k}</span>
      <span style={{ color: tone === 'warn' ? STATUS.warning : INK, fontWeight: 600 }}>{v}</span>
    </div>
  );
}

// =====================================================================
// 2. WHERE IT HAPPENS
// =====================================================================
// Three cluster scopes, three different column sets. Verified on the live API
// over a 5,000-row sample: FACILITY 3,089 / GATE_ARRAY 1,669 / BUS 242, with
// observed_group_devices and cofailure_share populated on exactly the 1,669
// GATE_ARRAY rows and facility_id null on exactly the 242 BUS rows. One table
// with blank columns would misrepresent that as missing data.
// =====================================================================
const SCOPES = [
  { key: 'FACILITY', label: 'Stations', hint: 'Devices co-failing at the same facility on the same day.' },
  { key: 'GATE_ARRAY', label: 'Gate arrays', hint: 'Gates in one array; the only scope carrying a concentration share.' },
  { key: 'BUS', label: 'Buses', hint: 'Validators on one bus. No facility is recorded for these.' },
];

function WhereView({ feeds }) {
  const [scope, setScope] = useState('FACILITY');
  const rows = feeds.clusters.rows;

  const latestDate = useMemo(() => {
    let m = '';
    (rows || []).forEach((r) => { if (String(r.event_date) > m) m = String(r.event_date); });
    return m;
  }, [rows]);

  const scoped = useMemo(
    () => (rows || []).filter((r) => String(r.cluster_scope).toUpperCase() === scope),
    [rows, scope]
  );

  const latest = useMemo(
    () => scoped.filter((r) => String(r.event_date) === latestDate)
      .sort((a, b) => num(b.cofailing_devices) - num(a.cofailing_devices)),
    [scoped, latestDate]
  );

  const top = useMemo(() => latest.slice(0, 15).map((r) => ({
    name: scope === 'BUS' ? `Bus ${r.cluster_id}` : `${scope === 'FACILITY' ? 'Facility' : 'Array'} ${r.cluster_id}`,
    value: num(r.cofailing_devices),
    code: r.device_category,
  })), [latest, scope]);

  const cols = useMemo(() => {
    const base = [
      { key: 'event_date', label: 'Date' },
      { key: 'cluster_id', label: scope === 'BUS' ? 'Bus' : 'Cluster' },
      { key: 'device_category', label: 'Fleet', render: (r) => deviceShort(r.device_category) },
      { key: 'cofailing_devices', label: 'Co-failing devices', num: true },
      { key: 'hardware_oos_onsets', label: 'OOS onsets', num: true },
    ];
    if (scope !== 'BUS') base.splice(3, 0, { key: 'facility_id', label: 'Facility' });
    if (scope === 'GATE_ARRAY') {
      base.push({ key: 'observed_group_devices', label: 'Devices in array', num: true, d: 0 });
      base.push({ key: 'cofailure_share', label: 'Share co-failing', num: true, d: 3 });
    }
    base.push({ key: 'coordinated_station_flag', label: 'Coordinated', render: (r) => (r.coordinated_station_flag ? 'yes' : '') });
    base.push({ key: 'major_station_flag', label: 'Major', render: (r) => (r.major_station_flag ? 'yes' : '') });
    return base;
  }, [scope]);

  const meta = SCOPES.find((s) => s.key === scope) || SCOPES[0];

  return (
    <>
      <Section
        eyebrow="Concentration"
        title="Where failures arrive together"
        sub="A cluster is more than one device on the same facility, gate array or bus opening a hardware-OOS episode on the same day."
        right={<div style={{ display: 'flex', gap: 6 }}>{SCOPES.map((s) => (
          <Chip key={s.key} active={scope === s.key} onClick={() => setScope(s.key)}>{s.label}</Chip>
        ))}</div>}
      >
        <Note>{meta.hint}{scope !== 'GATE_ARRAY' ? ' Concentration share is only computed for gate arrays, so it is not shown here.' : ''}</Note>
      </Section>

      <Panel title={`Largest clusters on ${latestDate ? dfmt(latestDate) : 'the latest day'}`} hint="Ranked by devices co-failing">
        <Feed feed={feeds.clusters} height={300}>
          {() => (top.length
            ? <RankBars data={top} xKey="value" yKey="name" height={320} colorBy={(d) => deviceColor(d.code)} fmt={(v) => nfmt(v)} unit=" devices" />
            : <Empty height={200}>No {meta.label.toLowerCase()} clusters on the latest day in this sample.</Empty>)}
        </Feed>
      </Panel>

      <Panel title={`${meta.label} clusters`} hint={`${nfmt(scoped.length)} rows in the sample. The full table holds 83,184; this is the most recent window.`}>
        <Feed feed={feeds.clusters} height={420}>
          {() => <DataTable rows={scoped} columns={cols} height={440} pageSize={100} exportName={`ps2_clusters_${scope.toLowerCase()}`} />}
        </Feed>
      </Panel>
    </>
  );
}

// =====================================================================
// 3. DEVICES
// =====================================================================
function DevicesView({ feeds }) {
  const [fleet, setFleet] = useState(null);
  const [reason, setReason] = useState(null);
  const rows = feeds.deterioration.rows;

  const reasons = useMemo(() => {
    const s = new Set();
    (rows || []).forEach((r) => { if (r.alert_reason) s.add(r.alert_reason); });
    return Array.from(s).sort();
  }, [rows]);

  const filtered = useMemo(() => (rows || []).filter((r) => {
    if (fleet && String(r.device_category).toUpperCase() !== fleet) return false;
    if (reason && r.alert_reason !== reason) return false;
    return true;
  }), [rows, fleet, reason]);

  // One bar per DEVICE at its worst day, not one per device-day. The label was
  // "RVG06302 - 16 Mar 2026", which wrapped onto two lines and collided with
  // its neighbours; and a device with several flagged days occupied several
  // bars, which reads as several devices.
  const top = useMemo(() => {
    const best = new Map();
    filtered.forEach((r) => {
      const k = r.device_id;
      const z = num(r.oos_zscore_28d);
      if (!best.has(k) || z > best.get(k).value) {
        best.set(k, { name: String(k), value: z, code: r.device_category, on: r.event_date });
      }
    });
    return Array.from(best.values()).sort((a, b) => b.value - a.value).slice(0, 15);
  }, [filtered]);

  const cols = [
    { key: 'device_id', label: 'Device' },
    { key: 'device_category', label: 'Fleet', render: (r) => deviceShort(r.device_category) },
    { key: 'event_date', label: 'Date' },
    { key: 'alert_reason', label: 'Why it was flagged' },
    { key: 'hardware_oos_onsets', label: 'OOS onsets', num: true },
    { key: 'hardware_oos_minutes', label: 'OOS minutes', num: true, d: 0 },
    { key: 'validated_failure_onsets', label: 'Validated', num: true },
    { key: 'baseline_mean_28d', label: '28d mean', num: true, d: 2 },
    { key: 'oos_zscore_28d', label: 'z-score', num: true, d: 2 },
  ];

  return (
    <>
      <Section
        eyebrow="Device grain"
        title="Devices behaving unlike themselves"
        sub="A device-day is flagged when its OOS volume departs from that device's own 28-day baseline. The comparison is to itself, not to the fleet."
        right={<FleetChips value={fleet} onChange={setFleet} />}
      >
        {reasons.length > 0 && (
          <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginTop: 4 }}>
            <Chip active={!reason} onClick={() => setReason(null)}>All reasons</Chip>
            {reasons.map((r) => <Chip key={r} active={reason === r} onClick={() => setReason(r)}>{r}</Chip>)}
          </div>
        )}
      </Section>

      <Grid cols="repeat(auto-fit,minmax(360px,1fr))">
        <Panel title="Sharpest departures from baseline" hint="Worst day per device, by 28-day z-score">
          <Feed feed={feeds.deterioration} height={320}>
            {() => <RankBars data={top} xKey="value" yKey="name" height={340} colorBy={(d) => deviceColor(d.code)} fmt={(v) => v.toFixed(2)} />}
          </Feed>
        </Panel>
        <Panel title="Flagged device-days by fleet">
          <Feed feed={feeds.deterioration} height={320}>
            {(all) => {
              // Donut takes colors as a FUNCTION colors(d, i), not an array.
              // Passing an array threw "colors is not a function" and unmounted
              // this whole sub-tab -- an exception in render tears the subtree
              // down, so the symptom is the tab vanishing rather than a blank
              // panel. The fleet code rides on the datum so the colour follows
              // the entity and never its position.
              const counts = FLEETS.map((f) => ({
                code: f,
                name: deviceShort(f),
                value: all.filter((r) => String(r.device_category).toUpperCase() === f).length,
              })).filter((d) => d.value > 0);
              return (
                <Donut
                  data={counts}
                  height={300}
                  colors={(d, i) => deviceColor(d && d.code ? d.code : FLEETS[i])}
                  centerLabel="device-days"
                  centerValue={nfmt(all.length)}
                />
              );
            }}
          </Feed>
        </Panel>
      </Grid>

      <Panel title="Flagged device-days" hint={`${nfmt(filtered.length)} of ${nfmt((rows || []).length)} rows`}>
        <Feed feed={feeds.deterioration} height={440}>
          {() => <DataTable rows={filtered} columns={cols} height={460} pageSize={100} exportName="ps2_device_deterioration" />}
        </Feed>
      </Panel>
    </>
  );
}

// =====================================================================
// 4. COMPONENTS & REPAIRS
// =====================================================================
// ps2_v2_repair_effectiveness carries no device_id -- it is keyed on repair_id
// with a subsystem. So repairs cannot be drilled from a device and are shown
// at subsystem level. That is the export's grain, not a UI shortcut.
// =====================================================================
function ComponentsView({ feeds }) {
  const [fleet, setFleet] = useState(null);
  const serials = feeds.serials.rows;
  const repairs = feeds.repairs.rows;

  const scoped = useMemo(
    () => (serials || []).filter((r) => !fleet || String(r.device_category).toUpperCase() === fleet),
    [serials, fleet]
  );

  // component_serial_id '0' is the UNATTRIBUTED bucket, not a component. 14 of
  // the 78 rows carry it, and because each one aggregates every episode with no
  // serial they dominate the ranking -- the top bar was validator SYSTEM at
  // 2,454,520 episodes and a priority score of 30.7M. Ranking them alongside
  // real serials presents "everything we could not attribute" as if it were a
  // part. They are split out below instead.
  const identified = useMemo(() => scoped.filter((r) => String(r.component_serial_id) !== '0'), [scoped]);
  const unattributed = useMemo(() => scoped.filter((r) => String(r.component_serial_id) === '0'), [scoped]);

  const topSerials = useMemo(() => [...identified]
    .sort((a, b) => num(b.component_priority_score) - num(a.component_priority_score))
    .slice(0, 15)
    .map((r) => ({ name: `${r.component_serial_id} (${r.component_subsystem})`, value: num(r.component_priority_score), code: r.device_category })),
  [identified]);

  const topUnattributed = useMemo(() => [...unattributed]
    .sort((a, b) => num(b.hardware_oos_episode_count) - num(a.hardware_oos_episode_count))
    .slice(0, 12)
    .map((r) => ({ name: `${r.component_subsystem} (${deviceShort(r.device_category)})`, value: num(r.hardware_oos_episode_count), code: r.device_category })),
  [unattributed]);

  const bySubsystem = useMemo(() => {
    const m = new Map();
    (repairs || []).forEach((r) => {
      const k = r.maintenance_component_subsystem || 'UNKNOWN';
      if (!m.has(k)) m.set(k, { name: k, n: 0, pre: 0, post: 0 });
      const e = m.get(k);
      e.n += 1; e.pre += num(r.pre_30d_oos_onsets); e.post += num(r.post_30d_oos_onsets);
    });
    return Array.from(m.values())
      .map((e) => ({ ...e, change: e.pre ? (e.post - e.pre) / e.pre : 0 }))
      .sort((a, b) => b.n - a.n)
      .slice(0, 12);
  }, [repairs]);

  const serialCols = [
    { key: 'component_serial_id', label: 'Serial' },
    { key: 'component_subsystem', label: 'Subsystem' },
    { key: 'device_category', label: 'Fleet', render: (r) => deviceShort(r.device_category) },
    { key: 'hardware_oos_episode_count', label: 'OOS episodes', num: true },
    { key: 'validated_failure_count', label: 'Validated', num: true },
    { key: 'hardware_oos_minutes', label: 'OOS minutes', num: true, d: 0 },
    { key: 'observed_oos_days', label: 'Days seen', num: true },
    { key: 'current_component_age_days', label: 'Age (days, unavailable)', num: true, d: 0 },
    { key: 'serial_evidence_tier', label: 'Evidence' },
    { key: 'component_priority_score', label: 'Priority', num: true, d: 3 },
  ];

  const repairCols = [
    { key: 'repair_id', label: 'Repair' },
    { key: 'maintenance_component_subsystem', label: 'Subsystem' },
    { key: 'ledger_type', label: 'Ledger' },
    { key: 'maintenance_date', label: 'Date' },
    { key: 'pre_30d_oos_onsets', label: 'OOS 30d before', num: true },
    { key: 'post_30d_oos_onsets', label: 'OOS 30d after', num: true },
    { key: 'post_vs_pre_change', label: 'Change', num: true, d: 2 },
    { key: 'interpretation_note', label: 'Note' },
  ];

  return (
    <>
      <Section
        eyebrow="Component grain"
        title="Which parts carry the burden"
        sub="Component serials ranked by an explainable priority score built from OOS episodes, validated failures and observed days."
        right={<FleetChips value={fleet} onChange={setFleet} />}
      >
        <Note>
          The hardware-config enrichment produced nothing in this run:
          hardware_component_description, hardware_source and current_component_age_days are null
          on all 78 rows. Subsystem and serial number are shown instead, and component age is not
          available. Separately, 14 of the 78 rows carry serial id 0 -- the unattributed bucket, not
          a part -- so they are held out of the ranking and shown on their own below.
        </Note>
      </Section>

      <Panel title="Highest-burden component serials" hint="Priority score, not a failure count">
        <Feed feed={feeds.serials} height={340}>
          {() => <RankBars data={topSerials} xKey="value" yKey="name" height={360} colorBy={(d) => deviceColor(d.code)} fmt={(v) => v.toFixed(3)} />}
        </Feed>
      </Panel>

      <Panel
        title="Unattributed OOS volume"
        hint="Episodes with no component serial, grouped by subsystem. Not parts -- the gap in serial attribution."
      >
        <Feed feed={feeds.serials} height={280}>
          {() => (topUnattributed.length
            ? <RankBars data={topUnattributed} xKey="value" yKey="name" height={300} colorBy={(d) => deviceColor(d.code)} fmt={(v) => nfmt(v)} unit=" episodes" />
            : <Empty height={200}>Every episode in this run carries a component serial.</Empty>)}
        </Feed>
      </Panel>

      <Panel title="Component serials" hint={`${nfmt(identified.length)} identified serials, plus ${nfmt(unattributed.length)} unattributed rows`}>
        <Feed feed={feeds.serials} height={380}>
          {() => <DataTable rows={scoped} columns={serialCols} height={400} pageSize={100} exportName="ps2_component_serials" />}
        </Feed>
      </Panel>

      <Section
        eyebrow="Maintenance"
        title="Did the repair help"
        sub="OOS onsets in the 30 days before a repair against the 30 days after, at subsystem level. The export carries no device id on a repair, so this cannot be read per device."
      />

      <Grid cols="repeat(auto-fit,minmax(420px,1fr))">
        <Panel title="Before and after, by subsystem" hint="Summed across every repair on that subsystem">
          <Feed feed={feeds.repairs} height={300}>
            {() => (
              <ColumnBars
                data={bySubsystem}
                xKey="name"
                series={[
                  { key: 'pre', label: 'OOS 30d before', color: CAT[3] },
                  { key: 'post', label: 'OOS 30d after', color: CAT[0] },
                ]}
                height={300}
                fmt={(v) => nfmt(v)}
              />
            )}
          </Feed>
        </Panel>
        <Panel title="Repair records" hint={`${nfmt((repairs || []).length)} of 38,395 rows`}>
          <Feed feed={feeds.repairs} height={300}>
            {() => <DataTable rows={repairs} columns={repairCols} height={320} pageSize={100} exportName="ps2_repair_effectiveness" />}
          </Feed>
        </Panel>
      </Grid>
    </>
  );
}

// =====================================================================
// 5. CASCADES
// =====================================================================
function CascadesView({ feeds }) {
  const [fleet, setFleet] = useState(null);
  const keep = useCallback((r) => !fleet || String(r.device_category).toUpperCase() === fleet, [fleet]);

  const precursors = useMemo(() => (feeds.precursors.rows || []).filter(keep), [feeds.precursors.rows, keep]);
  const leadlag = useMemo(() => (feeds.leadlag.rows || []).filter(keep), [feeds.leadlag.rows, keep]);
  const topology = useMemo(() => (feeds.topology.rows || []).filter(keep), [feeds.topology.rows, keep]);
  const drift = useMemo(() => (feeds.drift.rows || []).filter(keep), [feeds.drift.rows, keep]);

  const topPatterns = useMemo(() => [...precursors]
    .sort((a, b) => num(b.priority_score) - num(a.priority_score))
    .slice(0, 15)
    .map((r) => ({ name: r.pattern_key || `${r.component_subsystem} -> ${r.next_subsystem}`, value: num(r.priority_score), code: r.device_category })),
  [precursors]);

  const topNodes = useMemo(() => [...topology]
    .sort((a, b) => num(b.flow_centrality_score) - num(a.flow_centrality_score))
    .slice(0, 15)
    .map((r) => ({ name: `${r.subsystem} (${deviceShort(r.device_category)})`, value: num(r.flow_centrality_score), code: r.device_category })),
  [topology]);

  const precursorCols = [
    { key: 'pattern_key', label: 'Transition' },
    { key: 'device_category', label: 'Fleet', render: (r) => deviceShort(r.device_category) },
    { key: 'edge_support', label: 'Observed', num: true },
    { key: 'pre_oos_rate', label: 'Precedes OOS', num: true, d: 3 },
    { key: 'pre_oos_wilson_lower_95', label: 'Lower 95%', num: true, d: 3 },
    { key: 'pre_oos_lift_vs_category', label: 'Lift vs fleet', num: true, d: 2 },
    { key: 'median_edge_lag_seconds', label: 'Median lag (s)', num: true, d: 0 },
    { key: 'evidence_tier', label: 'Evidence' },
    { key: 'priority_score', label: 'Priority', num: true, d: 3 },
  ];

  const driftCols = [
    { key: 'component_subsystem', label: 'From' },
    { key: 'next_subsystem', label: 'To' },
    { key: 'device_category', label: 'Fleet', render: (r) => deviceShort(r.device_category) },
    { key: 'baseline_pre_oos_rate', label: 'Baseline rate', num: true, d: 3 },
    { key: 'recent_pre_oos_rate', label: 'Recent rate', num: true, d: 3 },
    { key: 'rate_change', label: 'Change', num: true, d: 3 },
    { key: 'drift_flag', label: 'Drifting', render: (r) => (r.drift_flag ? 'yes' : '') },
  ];

  const leadlagCols = [
    { key: 'pattern_key', label: 'Transition' },
    { key: 'device_category', label: 'Fleet', render: (r) => deviceShort(r.device_category) },
    { key: 'edge_support', label: 'Observed', num: true },
    { key: 'median_edge_lag_seconds', label: 'Median lag (s)', num: true, d: 0 },
    { key: 'p95_edge_lag_seconds', label: 'p95 lag (s)', num: true, d: 0 },
    { key: 'pre_oos_rate', label: 'Precedes OOS', num: true, d: 3 },
    { key: 'evidence_tier', label: 'Evidence' },
  ];

  return (
    <>
      <Section
        eyebrow="Fault chains"
        title="What tends to come first"
        sub="Subsystem-to-subsystem transitions observed within the precursor window, scored on how often they precede a hardware-OOS episode and how much that beats the fleet's own base rate."
        right={<FleetChips value={fleet} onChange={setFleet} />}
      >
        <Note>
          Lift is measured against the same fleet's base rate, so a lift of 1.0 means the pattern
          tells you nothing you did not already know from the fleet average. The Wilson lower bound
          is the honest floor on a rate computed from limited observations.
        </Note>
      </Section>

      <Grid cols="repeat(auto-fit,minmax(400px,1fr))">
        <Panel title="Top precursor patterns" hint="By explainable priority score">
          <Feed feed={feeds.precursors} height={340}>
            {() => <RankBars data={topPatterns} xKey="value" yKey="name" height={360} colorBy={(d) => deviceColor(d.code)} fmt={(v) => v.toFixed(3)} />}
          </Feed>
        </Panel>
        <Panel title="Most central subsystems" hint="Flow centrality across the transition graph">
          <Feed feed={feeds.topology} height={340}>
            {() => <RankBars data={topNodes} xKey="value" yKey="name" height={360} colorBy={(d) => deviceColor(d.code)} fmt={(v) => v.toFixed(3)} />}
          </Feed>
        </Panel>
      </Grid>

      <Panel title="Precursor patterns" hint={`${nfmt(precursors.length)} transitions meeting minimum support`}>
        <Feed feed={feeds.precursors} height={380}>
          {() => <DataTable rows={precursors} columns={precursorCols} height={400} pageSize={100} exportName="ps2_precursor_patterns" />}
        </Feed>
      </Panel>

      <Grid cols="repeat(auto-fit,minmax(420px,1fr))">
        <Panel title="Timing" hint="How long the second fault takes to arrive">
          <Feed feed={feeds.leadlag} height={340}>
            {() => <DataTable rows={leadlag} columns={leadlagCols} height={360} pageSize={50} exportName="ps2_leadlag_timing" />}
          </Feed>
        </Panel>
        <Panel title="Pattern drift" hint="Recent window against the baseline window">
          <Feed feed={feeds.drift} height={340}>
            {() => <DataTable rows={drift} columns={driftCols} height={360} pageSize={50} exportName="ps2_pattern_drift" />}
          </Feed>
        </Panel>
      </Grid>
    </>
  );
}

// =====================================================================
// 6. HOW WE KNOW
// =====================================================================
function EvidenceView({ feeds }) {
  const horizon = useMemo(() => {
    const m = new Map();
    (feeds.labelHorizon.rows || []).forEach((r) => {
      const d = num(r.lead_day);
      if (!m.has(d)) m.set(d, { lead_day: d, GATE: 0, TVM: 0, VALIDATOR: 0 });
      const c = String(r.device_category).toUpperCase();
      if (FLEETS.includes(c)) m.get(d)[c] = num(r.positive_rate_at_lead);
    });
    return Array.from(m.values()).sort((a, b) => a.lead_day - b.lead_day);
  }, [feeds.labelHorizon.rows]);

  const labelTrend = useMemo(
    () => pivotByFleet(feeds.labelDaily.rows, 'label_date', 'label_positive_rate'),
    [feeds.labelDaily.rows]
  );

  const qualityCols = [
    { key: 'check_name', label: 'Check' },
    { key: 'passed', label: 'Result', render: (r) => <Badge tone={r.passed ? 'good' : (r.severity === 'critical' ? 'critical' : 'warning')}>{r.passed ? 'pass' : 'fail'}</Badge> },
    { key: 'severity', label: 'Severity' },
    { key: 'observed_value', label: 'Observed', num: true, d: 4 },
    { key: 'threshold', label: 'Threshold' },
    { key: 'metric_context', label: 'What it means' },
  ];

  const summaryCols = [
    { key: 'device_category', label: 'Fleet', render: (r) => deviceShort(r.device_category) },
    { key: 'eligible_device_days', label: 'Eligible device-days', num: true },
    { key: 'positive_device_days', label: 'Positive device-days', num: true },
    { key: 'label_positive_rate', label: 'Positive rate', num: true, d: 3 },
    { key: 'eligible_devices', label: 'Devices', num: true },
    { key: 'positive_devices', label: 'Devices ever positive', num: true },
    { key: 'median_hours_to_next_oos', label: 'Median hours to next OOS', num: true, d: 1 },
    { key: 'label_cutoff_date', label: 'Cutoff' },
  ];

  const perfCols = [
    { key: 'device_category', label: 'Fleet', render: (r) => deviceShort(r.device_category) },
    { key: 'prediction_coverage', label: 'Coverage', num: true, d: 3 },
    { key: 'actual_positive_rate', label: 'Actual positive rate', num: true, d: 3 },
    { key: 'predicted_positive_rate', label: 'Predicted positive rate', num: true, d: 3 },
    { key: 'precision', label: 'Precision', num: true, d: 3 },
    { key: 'recall', label: 'Recall', num: true, d: 3 },
    { key: 'specificity', label: 'Specificity', num: true, d: 3 },
    { key: 'roc_auc', label: 'ROC AUC', num: true, d: 3 },
    { key: 'pr_auc', label: 'PR AUC', num: true, d: 3 },
  ];

  const alignCols = [
    { key: 'device_category', label: 'Fleet', render: (r) => deviceShort(r.device_category) },
    { key: 'silver_ps1_failure_device_days', label: 'PS1 label days', num: true },
    { key: 'governed_oos_episode_device_days', label: 'Governed OOS days', num: true },
    { key: 'overlap_device_days', label: 'Overlap', num: true },
    { key: 'silver_to_governed_overlap_rate', label: 'PS1 covered', num: true, d: 3 },
    { key: 'governed_to_silver_overlap_rate', label: 'Governed covered', num: true, d: 3 },
    { key: 'definition_jaccard', label: 'Jaccard', num: true, d: 3 },
  ];

  const parityCols = [
    { key: 'device_category', label: 'Fleet', render: (r) => deviceShort(r.device_category) },
    { key: 'comparable_device_days', label: 'Comparable days', num: true },
    { key: 'matching_device_days', label: 'Matching', num: true },
    { key: 'source_label_positive_rate', label: 'Source rate', num: true, d: 3 },
    { key: 'rebuilt_label_positive_rate', label: 'Rebuilt rate', num: true, d: 3 },
    { key: 'parity_rate', label: 'Parity', num: true, d: 3 },
    { key: 'parity_status', label: 'Status' },
  ];

  const crossCols = [
    { key: 'source', label: 'Source' },
    { key: 'truth_positive_count', label: 'Truth positives', num: true },
    { key: 'signal_positive_count', label: 'Signal positives', num: true },
    { key: 'matched_positive_count', label: 'Matched', num: true },
    { key: 'precision', label: 'Precision', num: true, d: 3 },
    { key: 'recall', label: 'Recall', num: true, d: 3 },
    { key: 'population_unit', label: 'Unit' },
    { key: 'alignment_status', label: 'Status' },
  ];

  const catCols = [
    { key: 'device_category_raw', label: 'Raw value in source' },
    { key: 'device_category', label: 'Mapped to' },
    { key: 'event_count', label: 'Events', num: true },
    { key: 'device_count', label: 'Devices', num: true },
    { key: 'is_mapped', label: 'Mapped', render: (r) => (r.is_mapped ? 'yes' : 'no') },
    { key: 'is_target_scope', label: 'In scope', render: (r) => (r.is_target_scope ? 'yes' : 'no') },
  ];

  return (
    <>
      <Section
        eyebrow="Method"
        title="How we know, and what we do not"
        sub="Everything on this tab is about the analysis itself rather than about the estate. It is here so the numbers on the other tabs can be argued with."
      />

      <Panel title="Governance checks" hint="Run by the notebook before it was allowed to publish">
        <Feed feed={feeds.runQuality} height={360}>
          {(rows) => <DataTable rows={rows} columns={qualityCols} height={380} pageSize={50} searchable={false} exportName="ps2_run_quality" />}
        </Feed>
      </Panel>

      <Section
        eyebrow="The label"
        title="Why prevalence is not a failure rate"
        sub="will_hardware_oos_3d marks a device-day positive if the device opens any hardware-OOS episode on the next one to three days. On a device that emits OOS events continually, almost every day qualifies."
      >
        <Note>
          Read alongside the governed counts: 9,872,407 hardware-OOS events collapse to 9,325,118
          episode onsets -- the restart-gap rule absorbs only 5.5% -- against 172,576 validated
          failure onsets. The gap between those two numbers is what this section exists to make
          visible, and it is a property of the rule rather than of the fleet.
        </Note>
      </Section>

      <Grid cols="repeat(auto-fit,minmax(420px,1fr))">
        <Panel title="Positive-day rate over time" hint="Share of eligible device-days marked positive">
          <Feed feed={feeds.labelDaily} height={280}>
            {() => <Trend data={labelTrend} xKey="label_date" series={FLEET_SERIES} height={280} fmt={(v) => pct(v, 1)} />}
          </Feed>
        </Panel>
        <Panel title="Positive rate by lookahead day" hint="Day 1, 2 and 3 ahead of the score date">
          <Feed feed={feeds.labelHorizon} height={280}>
            {() => <ColumnBars data={horizon} xKey="lead_day" series={FLEET_SERIES} height={280} fmt={(v) => pct(v, 2)} />}
          </Feed>
        </Panel>
      </Grid>

      <Panel title="Label summary by fleet">
        <Feed feed={feeds.labelSummary} height={220}>
          {(rows) => <DataTable rows={rows} columns={summaryCols} height={240} pageSize={20} searchable={false} exportName="ps2_label_summary" />}
        </Feed>
      </Panel>

      <Section
        eyebrow="PS1 cross-check"
        title="PS2 as an independent auditor of PS1"
        sub="PS2 rebuilds the label from direct Silver and then scores PS1's own predictions against it. This is the only place in the platform where PS1's operating point is measured against an independently constructed target."
      >
        <Note>
          Read specificity before precision. A fleet where the model says yes to almost every
          device-day will show high precision simply because the base rate is high; specificity is
          what reveals whether the threshold is doing any work.
        </Note>
      </Section>

      <Panel title="PS1 model performance, measured by PS2">
        <Feed feed={feeds.modelPerf} height={220}>
          {(rows) => <DataTable rows={rows} columns={perfCols} height={240} pageSize={20} searchable={false} exportName="ps2_ps1_model_performance" />}
        </Feed>
      </Panel>

      <Grid cols="repeat(auto-fit,minmax(420px,1fr))">
        <Panel title="Label parity with PS1" hint="Same device-days, both label constructions">
          <Feed feed={feeds.parity} height={200}>
            {(rows) => <DataTable rows={rows} columns={parityCols} height={220} pageSize={20} searchable={false} exportName="ps2_label_parity" />}
          </Feed>
        </Panel>
        <Panel title="Definition alignment" hint="PS1's label against PS2's governed OOS episodes">
          <Feed feed={feeds.alignment} height={200}>
            {(rows) => <DataTable rows={rows} columns={alignCols} height={220} pageSize={20} searchable={false} exportName="ps2_definition_alignment" />}
          </Feed>
        </Panel>
      </Grid>

      <Grid cols="repeat(auto-fit,minmax(420px,1fr))">
        <Panel title="Cross-problem alignment" hint="PS2's signals against other problem statements">
          <Feed feed={feeds.crossPs} height={200}>
            {(rows) => <DataTable rows={rows} columns={crossCols} height={220} pageSize={20} searchable={false} exportName="ps2_cross_ps_alignment" />}
          </Feed>
        </Panel>
        <Panel title="Device category mapping" hint="How raw source values were canonicalised">
          <Feed feed={feeds.categories} height={200}>
            {(rows) => <DataTable rows={rows} columns={catCols} height={220} pageSize={20} searchable={false} exportName="ps2_category_profile" />}
          </Feed>
        </Panel>
      </Grid>
    </>
  );
}

// =====================================================================
// Shell
// =====================================================================
function StatusBar({ feed }) {
  const s = (feed && feed.rows && feed.rows[0]) || null;
  if (!s) return null;
  const coherent = !!s.coherent;
  return (
    <div style={{
      display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap',
      background: CARD, border: `1px solid ${LINE}`, borderRadius: 12, padding: '9px 14px', marginBottom: 14,
    }}>
      <Badge tone={coherent ? 'good' : 'warning'}>
        {coherent ? 'All tables from one run' : `${(s.run_ids || []).length} run ids across ${s.tables} tables`}
      </Badge>
      <span style={{ fontSize: 12.5, color: INK_2 }}>
        Analysis as of <strong style={{ color: INK }}>{s.computed_date ? dfmt(s.computed_date) : 'unknown'}</strong>
      </span>
      <span style={{ ...font.micro }}>
        {s.tables}/{s.expected_tables} tables &middot; run {String((s.run_ids || [])[0] || '').slice(0, 8)}
      </span>
      <span style={{ ...font.micro, marginLeft: 'auto' }}>
        Source extract ends 11 Apr 2026. Nothing here describes the estate after that date.
      </span>
    </div>
  );
}

export default function PS2Overview({ city = 'CHI' }) {
  const [view, setView] = useState('impact');
  const { feeds, request } = useFeeds(city);

  useEffect(() => {
    const v = VIEWS.find((x) => x.key === view);
    request(['status', ...(v ? v.feeds : [])]);
  }, [view, request]);

  const active = VIEWS.find((v) => v.key === view) || VIEWS[0];

  return (
    <DrilldownProvider rootLabel="PS2 - cascading failure">
      <div style={{ padding: '4px 2px 40px' }}>
        <StatusBar feed={feeds.status} />

        <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginBottom: 18 }}>
          {VIEWS.map((v) => (
            <Chip key={v.key} active={view === v.key} onClick={() => setView(v.key)}>{v.label}</Chip>
          ))}
        </div>

        <Breadcrumb />

        {active.key === 'impact' && <ImpactView feeds={feeds} />}
        {active.key === 'where' && <WhereView feeds={feeds} />}
        {active.key === 'devices' && <DevicesView feeds={feeds} />}
        {active.key === 'components' && <ComponentsView feeds={feeds} />}
        {active.key === 'cascades' && <CascadesView feeds={feeds} />}
        {active.key === 'evidence' && <EvidenceView feeds={feeds} />}
      </div>
    </DrilldownProvider>
  );
}
