// =====================================================================
// v2/PS2Overview.jsx -- Failure Pattern & Cascade Identification (cascading / governed hardware-OOS) in the v2 shape.
//
// WHAT THIS TAB IS. Failure Pattern & Cascade Identification has no served model. The notebook runs on a schedule
// and its 20 tables are refreshed wholesale into Aurora, so this screen is an
// ANALYSIS surface, not a work queue. Verb tense throughout is "what the
// analysis found", not "what to do today".
//
// THE HEADLINE, AND WHY IT IS NOT WHAT YOU EXPECT.
// The obvious hero would be validated failure onsets. Re-measured on the
// 29-Aug-2026 export (run 6aafe3e0), 154,848 of 159,981 are validators:
//     GATE        506,976 OOS onsets ->     300 validated  (0.06%)
//     TVM       1,681,812            ->   4,833            (0.29%)
//     VALIDATOR 5,305,237            -> 154,848            (2.92%)
// Five months of extra data did not move the shape: gate coverage is still
// under a tenth of a percent.
// A hero tile on that number tells a reader 821 fare gates produced 300
// failures in seven months. That is a statement about evidence coverage, not
// about gates. So the hero is MEASURED IMPACT -- outage hours, devices, and
// customer exposure, all counted rather than inferred and all comparable
// across fleets -- and the evidence ladder sits directly beneath it so the
// coverage difference is visible instead of hidden inside an average.
//
// WHAT IS DELIBERATELY NOT ON THE FIRST SCREEN.
// The will_hardware_oos_3d label prevalence (GATE 80.0%, TVM 89.7%,
// VALIDATOR 35.6%) and the Failure Prediction model scorecard live in "How we know". They
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
import { getObj, getRows } from './V4api';
import {
  Badge, Breadcrumb, Card, Chip, DrilldownProvider, Empty, Grid, Hero, Legend,
  Loading, Note, Panel, Section, Stat, useDrill, Rule, Tabs, V2Style,
} from './V4Kit';
import { ChartFrame, ColumnBars, Donut, Matrix, RankBars, Trend } from './V4Charts';
import DataTable from './V4DataTable';
import { useLocations } from './V4Locations';
import AnalyseModal from './V4Device360Popup';
import { HeatGrid, NetworkGraph, SankeyFlow, SlopeChart } from './V4ChartsPlus';

// STABLE CALLBACK IDENTITIES.                                v5
// These were inline arrows in JSX, so every render produced a NEW
// function and React.memo on the chart components compared unequal
// every time -- the memo was a no-op. Every one of these closes over
// nothing but module scope, so hoisting is enough; no useCallback, no
// dependency array to get wrong. They are only invoked during render,
// so referring to a const declared further down the module is safe.
const _fmt1 = (v) => nfmt(v);
const _colorBy2 = (d) => deviceColor(d.code);
const _fmt3 = (v) => v.toFixed(2);
const _colors4 = (d, i) => deviceColor(d && d.code ? d.code : FLEETS[i]);
const _fmt5 = (v) => v.toFixed(3);
const _fmt6 = (v) => Number(v).toFixed(1);
const _colorBy7 = (d) => (d.value >= 0 ? CAT[0] : STATUS.critical.fill);
const _fmt8 = (v) => Number(v).toFixed(3);
const _fmt9 = (v) => `${Number(v).toFixed(0)}%`;
const _fmt11 = (v) => `${Number(v).toFixed(1)}%`;
const _fmt12 = (v) => pct(v, 1);
const _fmt13 = (v) => pct(v, 2);

import {
  CAT, CARD, INK, INK_2, INK_3, LINE, STATUS,
  compact, deviceColor, deviceShort, dfmt, font, nfmt, pct, A, TAB_COLOR, tint,
} from './V4theme';

const FLEETS = ['GATE', 'TVM', 'VALIDATOR'];

// ---------------------------------------------------------------------
// Feeds. One entry per route. Limits are the route's own hard caps where the
// table is large -- clusters and repairs both run to tens of thousands of
// rows, so both are browse windows and neither is ever used as a denominator.
// The real totals come from /ps2/status row_count via tableRows(); do not
// write them here, they change with every run.
// ---------------------------------------------------------------------
const FEED_FN = {
  status:        (city) => getObj('/ps2/status', { city }).then((o) => [o]),
  // ---- PREVIOUS-GENERATION relationship feeds (04-Aug-2026) --------------
  // These four were live in Aurora and read by nothing. They are the only
  // published source of subsystem-to-subsystem structure, so they are wired
  // here rather than left dark -- but they are SMALL, and the view prints
  // their denominators for that reason.
  phi:           (city) => getRows('/ps2/phi', { city, limit: 5000 }),
  network:       (city) => getRows('/ps2/network', { city, limit: 5000 }),
  // A MIGRATION SEED IS NOT AN OBSERVATION.                  23-Sep-2026
  // /ps2/paths reads ps2_cascade_paths and /ps2/ignition reads
  // ps2_ignition_termination. NEITHER table has a producer: no cell in either
  // PS2 notebook writes them and the loader has no alias for them. Their rows
  // come from sql/07 and sql/08 -- hand-written INSERTs dated 2026-07-11 and
  // 2026-07-14 -- so no run can ever displace them.
  //
  // Both tables were DROPPED later the same day (sql/63), together with the
  // eleven other PS2 tables no current notebook writes. The paragraph above
  // is why, and is kept so the next reader does not re-add a panel for them.
  //
  // The paths panel presented those rows as 'the actual subsystem sequences
  // recorded'; sql/08:58-61 is a ten-row literal in which every path was seen
  // exactly once, and sql/07:52 computes its occurrences as
  // round(support * 2,198,548) from association rules -- an estimate, printed
  // under a column headed 'Times seen'. The panel is gone.
  //
  // Ignition has a real equivalent: ps2_ignition_termination_subsystem, which
  // the serial-grain notebook writes every run. It carries counts rather than
  // the percentages the seeded table had, so the share is computed here where
  // the denominator is visible.
  sankey:        (city) => getRows('/ps2/serial/sankey', { city, limit: 500 }),
  ignition:      (city) => getRows('/ps2/serial/ignition', { city, limit: 500 }),
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
  // Cascades sits SECOND now, not fifth. It is the answer to the question the
  // problem statement is named after, and it was behind three other tabs.
  { key: 'cascades',   label: 'Cascades',            feeds: ['precursors', 'leadlag', 'topology', 'drift'] },
  { key: 'where',      label: 'Location',            feeds: ['clusters'] },
  { key: 'devices',    label: 'Devices',             feeds: ['deterioration'] },
  { key: 'components', label: 'Components & repairs', feeds: ['serials', 'repairs'] },
  { key: 'relationships', label: 'Component relationships', feeds: ['phi', 'network', 'ignition', 'sankey'] },
  { key: 'evidence',   label: 'How we know',         feeds: ['runQuality', 'labelSummary', 'labelDaily', 'labelHorizon', 'parity', 'alignment', 'modelPerf', 'categories', 'crossPs', 'governance'] },
];

// ---------------------------------------------------------------------
// useFeeds -- per-feed {rows, loading, error}. Copied in shape from Failure Prediction/Anomaly & Outlier Analysis
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
// precisely so this distinction survives -- three Failure Prediction panels once reported a
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
    const v = typeof valKey === 'function' ? valKey(r) : r[valKey];
    if (FLEETS.includes(cat)) map.get(d)[cat] += num(v);
  });
  return Array.from(map.values()).sort((a, b) => String(a[dateKey]).localeCompare(String(b[dateKey])));
}

function sumBy(rows, key, filter) {
  const get = typeof key === 'function' ? key : (r) => r[key];
  return (rows || []).reduce((t, r) => (filter && !filter(r) ? t : t + num(get(r))), 0);
}

// AVAILABILITY IS NOT THE SUM OF COMPONENT EPISODES.         23-Sep-2026
// hardware_oos_minutes adds up every component episode separately, so a
// device with three components down through the same hour books three hours
// out of service. The notebook measured the size of that on this very run and
// wrote it into its own source: GATE published 14.66% against 3.17% true
// (4.63x), TVM 30.52% against 12.04% (2.53x), VALIDATOR 95.47% against 46.48%
// (2.05x). It then published hardware_oos_union_minutes -- the wall-clock
// union per device-day -- and the API has served both columns side by side
// ever since, deliberately, "so the difference stays visible rather than
// being silently swapped".
//
// This tab read only the inflated column. Every availability number on the
// page was 2-4.6x too high, and the note beside the hero explained the
// inflated validator figure as real fleet behaviour -- a narrative built on
// an artifact, which is worse than the number alone.
//
// Component burden keeps the raw column, but only where the label says so:
// per-serial minutes cannot double-count against themselves.
const hasUnion = (r) => {
  const u = r.hardware_oos_union_minutes;
  return !(u === null || u === undefined || u === '');
};
const oosUnion = (r) => (hasUnion(r) ? num(r.hardware_oos_union_minutes) : num(r.hardware_oos_minutes));

// A SILENT FALLBACK REINTRODUCES THE DEFECT INVISIBLY.        23-Sep-2026
// sql/46 is additive: rows written before v2.5.4 keep NULL in the union
// columns until a v2.5.4 loader run fills them. Falling back to the episode
// sum keeps the page working on old rows -- and shows the 2-4.6x overstated
// figure again, under a label that now promises wall-clock, with nothing on
// screen to say which one a reader is looking at. So count the fallback and
// print it. Silence here would be the same mistake in a new place.
const unionCoverage = (rows) => {
  const n = (rows || []).length;
  if (!n) return { n: 0, with: 0, share: 1 };
  const w = rows.filter(hasUnion).length;
  return { n, with: w, share: w / n };
};

// THE WHOLE TABLE, NOT THE PAGE.                              20-Sep-2026
// Several captions used to state a row total typed in when the panel was
// written -- 83,184 clusters, 38,395 repairs. Those were true of the 11-Apr
// run and false of every run since, and a caption that contradicts the table
// beside it discredits the table. /ps2/status already carries row_count per
// table for exactly this, and `status` is requested on every view.
// Returns null when unknown, so callers drop the clause instead of printing
// a zero.
function tableRows(feeds, name) {
  const s = (feeds.status && feeds.status.rows && feeds.status.rows[0]) || null;
  const hit = ((s && s.rows) || []).find((r) => r.table_name === name);
  const v = hit ? Number(hit.row_count) : NaN;
  return Number.isFinite(v) ? v : null;
}

// observed_value of one run-quality check, or null.
function checkValue(rows, name) {
  const hit = (rows || []).find((r) => r.check_name === name);
  const v = hit ? Number(hit.observed_value) : NaN;
  return Number.isFinite(v) ? v : null;
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
    const hours = sumBy(trend, oosUnion) / 60;
    // Capacity is the honest denominator: every device, every hour of the
    // window.
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
      const hours = sumBy(trend, oosUnion, m) / 60;
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

  const minutesTrend = useMemo(() => pivotByFleet(trend, 'event_date', oosUnion)
    .map((r) => ({ ...r, GATE: r.GATE / 60, TVM: r.TVM / 60, VALIDATOR: r.VALIDATOR / 60 })), [trend]);
  const onsetTrend = useMemo(() => pivotByFleet(trend, 'event_date', 'hardware_oos_onsets'), [trend]);

  // ZERO IS A MEASUREMENT; NO DATA IS NOT.                    23-Sep-2026
  // This gate only covered `loading`. useFeeds seeds every key idle, and an
  // errored route clears loading too -- so before the feeds started, and
  // whenever they failed, `totals` collapsed to zeros and the headline
  // announced "0.0% of available device-hours ... 0 device-hours recorded
  // against 0 available, across 0 days and 0 devices". A dead route read as
  // an estate that lost nothing.
  const unionCov = useMemo(() => unionCoverage(trend), [trend]);
  // HOW MUCH WAS IT OVERSTATED? The producer publishes the ratio of the two
  // minute columns and no route served it until 23-Sep, so the size of the
  // correction was never on screen -- only its result. Weighted by the union
  // minutes, so a big day counts more than a quiet one.
  const overlapFactor = useMemo(() => {
    const u = sumBy(trend, oosUnion);
    const raw = sumBy(trend, 'hardware_oos_minutes');
    return u > 0 ? raw / u : null;
  }, [trend]);

  const heroPending = ['trend', 'exposure', 'labelSummary']
    .some((k) => feeds[k].loading || feeds[k].idle || feeds[k].error);
  const loading = heroPending;

  return (
    <>
      <Section accent={TAB_COLOR.ps2}
        eyebrow="Measured impact"
        title="What the estate actually lost"
        sub="Counted from governed hardware-OOS episodes. Every figure below is measured, not modelled."
      >
        <Grid cols="repeat(auto-fit,minmax(220px,1fr))">
          <Hero accent={TAB_COLOR.ps2}
            label="Recorded out-of-service time"
            value={loading ? '--' : pct(totals.share, 1)}
            unit="of available device-hours"
            sub={loading ? '' : `${nfmt(totals.outageHours)} device-hours recorded against ${nfmt(totals.capacity)} available, across ${nfmt(totals.days)} days and ${nfmt(totals.devices)} devices`}
          />
          <Stat label="Devices in scope" value={loading ? '--' : nfmt(totals.devices)} foot="TVMs, fare gates and bus validators" />
          <Stat label="Component episode overlap"
                value={loading || !overlapFactor ? '--' : `${overlapFactor.toFixed(2)}x`}
                foot="summed component episodes vs wall clock; 1.00x would mean none overlap" />
          <Stat label="Transactions exposed" value={loading ? '--' : compact(totals.txn)} foot="during an open OOS episode" />
        </Grid>
        {!loading && unionCov.n > 0 && unionCov.share < 1 && (
          <Note accent={STATUS.serious.fill}>
            {unionCov.share === 0
              ? `None of the ${nfmt(unionCov.n)} day-fleet rows carry the wall-clock union column, so every figure above falls back to the sum of component episodes. That sum double-counts concurrent episodes on one device and was measured 2.05-4.63x over the true value. Re-run the loader against a v2.5.4 producer run to fill it.`
              : `${nfmt(unionCov.n - unionCov.with)} of ${nfmt(unionCov.n)} day-fleet rows carry no wall-clock union value and fall back to the sum of component episodes, which overstates their contribution. Those rows predate v2.5.4.`}
          </Note>
        )}
      </Section>


      <Section accent={TAB_COLOR.ps2} eyebrow="Evidence" title="How much of this is confirmed">
        {/* These two shares were typed in from one run. byFleet already holds
            `validated` per fleet, so they are one division away. */}
        <Note>
          The three fleets do not carry the same evidence.
          {totals.validated ? ` Validators account for ${pct(
            (byFleet.find((f) => f.code === 'VALIDATOR') || {}).validated / totals.validated, 0,
          )} of all validated failure onsets; fare gates for ${pct(
            (byFleet.find((f) => f.code === 'GATE') || {}).validated / totals.validated, 1,
          )}.` : ''} That is a difference in what the source
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
                        <span style={{ fontSize: 13.1, fontWeight: 700, color: INK }}>{f.fleet}</span>
                      </div>
                      <div style={{ marginTop: 10, fontSize: 19.8, fontWeight: 700, color: INK }}>{nfmt(f.hours)}</div>
                      <div style={{ ...font.micro }}>device-hours out of service (wall clock)</div>
                      <div style={{ marginTop: 12, display: 'grid', gap: 4 }}>
                        <Row k="OOS onsets" v={nfmt(f.onsets)} />
                        <Row k="Validated failures" v={nfmt(f.validated)} />
                        <Row k="Chargeable events" v={nfmt(f.chargeable)} />
                        <Row k="Validated share *" v={pct(f.coverage, 2)} tone={f.coverage < 0.01 ? 'warn' : 'normal'} />
                        <Row k="Of available time" v={pct(f.share, 1)} tone={f.share > 0.5 ? 'warn' : 'normal'} />
                      </div>
                    </div>
                  ))}
                </div>
              </ChartFrame>
            )}
          </Feed>

          {/* PK's wording, agreed 04-Aug. This is the ONLY one of the three
              asterisks that names ServiceNow, and deliberately: Failure Pattern & Cascade Identification's validated
              share is a ServiceNow join, whereas Root Cause Analysis is device-native now and
              Failure Prediction's absence is about the OOS window. Using the same sentence on
              all three would undo the contract work. */}
          <div style={{ ...font.note, fontSize: 12.2, marginTop: 12 }}>
            <strong style={{ color: INK }}>*</strong> Validated against ServiceNow. Onsets with no
            matching incident, or matched to &lsquo;No issues found&rsquo;, are counted as
            out-of-service but not as validated failures.
          </div>
        </Card>
      </Section>

      <Grid cols="repeat(auto-fit,minmax(420px,1fr))">
        <Panel title="Out-of-service hours by day"
               hint="Wall-clock device-hours per day: concurrent component episodes on one device count once, attributed to the day the episode opened">
          <Feed feed={feeds.trend} height={260}>
            {() => <Trend data={minutesTrend} xKey="event_date" series={FLEET_SERIES} height={260} area fmt={_fmt1} />}
          </Feed>
        </Panel>
        <Panel title="Episode onsets by day" hint="A new hardware-OOS episode opening">
          <Feed feed={feeds.trend} height={260}>
            {() => <Trend data={onsetTrend} xKey="event_date" series={FLEET_SERIES} height={260} fmt={_fmt1} />}
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
              fmt={_fmt1}
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
    <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 12.2 }}>
      <span style={{ color: INK_2 }}>{k}</span>
      {/* .fill, not the STATUS entry. See Device360's KV -- STATUS.warning is an
          object and "[object Object]" is not a colour, so the warn tone on these
          fleet cards has never actually shown. */}
      <span style={{ color: tone === 'warn' ? STATUS.warning.fill : INK, fontWeight: 600 }}>{v}</span>
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

function WhereView({ feeds, city }) {
  const clusterTotal = tableRows(feeds, 'ps2_v2_cofailure_clusters');
  const [scope, setScope] = useState('FACILITY');
  const loc = useLocations(city);
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
    name: scope === 'BUS'
      ? `Bus ${r.cluster_id}`
      : (scope === 'FACILITY' ? loc.name(r.cluster_id) : `Array ${r.cluster_id}`),
    value: num(r.cofailing_devices),
    code: r.device_category,
  })), [latest, scope, loc]);

  const cols = useMemo(() => {
    const base = [
      { key: 'event_date', label: 'Date' },
      { key: 'cluster_id',
        label: scope === 'BUS' ? 'Bus' : (scope === 'FACILITY' ? 'Location' : 'Cluster'),
        flex: scope === 'FACILITY' ? 2 : 1,
        render: scope === 'FACILITY' ? (r) => loc.label(r.cluster_id) : undefined },
      { key: 'device_category', label: 'Fleet', render: (r) => deviceShort(r.device_category) },
      { key: 'cofailing_devices', label: 'Co-failing devices', num: true },
      { key: 'hardware_oos_onsets', label: 'OOS onsets', num: true },
    ];
    if (scope !== 'BUS') base.splice(3, 0, { key: 'facility_id', label: 'Location', flex: 2, render: (r) => loc.label(r.facility_id) });
    if (scope === 'GATE_ARRAY') {
      base.push({ key: 'observed_group_devices', label: 'Devices in array', num: true, d: 0 });
      base.push({ key: 'cofailure_share', label: 'Share co-failing', num: true, d: 3 });
    }
    base.push({ key: 'coordinated_station_flag', label: 'Coordinated', render: (r) => (r.coordinated_station_flag ? 'yes' : '') });
    base.push({ key: 'major_station_flag', label: 'Major', render: (r) => (r.major_station_flag ? 'yes' : '') });
    return base;
  }, [scope, loc]);

  const meta = SCOPES.find((s) => s.key === scope) || SCOPES[0];

  return (
    <>
      <Section accent={TAB_COLOR.ps2}
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
            ? <RankBars data={top} xKey="value" yKey="name" height={320} colorBy={_colorBy2} fmt={_fmt1} unit=" devices" />
            : <Empty height={200}>No {meta.label.toLowerCase()} clusters on the latest day in this sample.</Empty>)}
        </Feed>
      </Panel>

      <Panel title={`${meta.label} clusters`} hint={clusterTotal
          ? `${nfmt(scoped.length)} rows in the sample. The full table holds ${nfmt(clusterTotal)}; this is the most recent window.`
          : `${nfmt(scoped.length)} rows in the sample; this is the most recent window.`}>
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
function DevicesView({ feeds, onAnalyse }) {
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
    // Device-day grain, so this is the double-counted column. The route
    // carries the union beside it; show that, and keep the raw one under a
    // label that says what it actually measures.
    { key: 'hardware_oos_union_minutes', label: 'OOS minutes', num: true, d: 0,
      render: (r) => nfmt(oosUnion(r)) },
    { key: 'hardware_oos_minutes', label: 'Component burden (min)', num: true, d: 0 },
    { key: 'validated_failure_onsets', label: 'Validated', num: true },
    { key: 'baseline_mean_28d', label: '28d mean', num: true, d: 2 },
    { key: 'oos_zscore_28d', label: 'z-score', num: true, d: 2 },
  ];

  return (
    <>
      <Section accent={TAB_COLOR.ps2}
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
            {() => <RankBars data={top} xKey="value" yKey="name" height={340} colorBy={_colorBy2} fmt={_fmt3} />}
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
                  colors={_colors4}
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
          {() => (
            <DataTable
              rows={filtered} columns={cols} height={460} pageSize={100}
              onRowClick={(r) => onAnalyse && onAnalyse(r.device_id)}
              exportName="ps2_device_deterioration"
            />
          )}
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
function ComponentsView({ feeds, onAnalyse }) {
  const repairTotal = tableRows(feeds, 'ps2_v2_repair_effectiveness');
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
  // The notebook stamps this per row as CURRENT_CONFIG_AS_OF_RUN or
  // UNAVAILABLE. Count it rather than asserting every row is unenriched.
  const ageUnavailable = useMemo(
    () => scoped.filter((r) => String(r.hardware_age_enrichment || 'UNAVAILABLE').toUpperCase() !== 'CURRENT_CONFIG_AS_OF_RUN').length,
    [scoped]
  );

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

  // WHAT THIS FEED ACTUALLY CONTAINS, measured rather than assumed.
  //
  // Sampled at offsets 0, 10,000 and 30,000 of the repair table (38,395 rows
  // at the time of sampling, 33,576 on the 29-Aug run): every
  // row returns maintenance_component_subsystem = 'UNKNOWN' and
  // pre_30d_oos_onsets = post_30d_oos_onsets = 0. Fifteen thousand rows, not
  // one non-zero on either side.
  //
  // That is not "repairs made no difference". A pre/post comparison that is
  // zero on BOTH sides has not compared anything -- the repair ledger did not
  // join to the OOS events, so every episode sees an empty window in both
  // directions. The old chart summed those zeros into two flat bars and
  // rendered as if it had a finding.
  const repairSignal = useMemo(() => {
    const rows = repairs || [];
    const nz = rows.filter((r) => num(r.pre_30d_oos_onsets) || num(r.post_30d_oos_onsets));
    const subs = new Set(rows.map((r) => r.maintenance_component_subsystem || 'UNKNOWN'));
    const dates = rows.map((r) => String(r.maintenance_date || '')).filter(Boolean).sort();
    return {
      n: rows.length,
      nWithSignal: nz.length,
      subsystems: [...subs],
      allUnknown: subs.size === 1 && subs.has('UNKNOWN'),
      first: dates[0] || null,
      last: dates[dates.length - 1] || null,
    };
  }, [repairs]);

  // Repair VOLUME is real and varies -- it is the one thing this export can
  // support. Plotted by date so the panel says something true instead of
  // something shaped like an answer.
  const repairsByDate = useMemo(() => {
    const m = new Map();
    (repairs || []).forEach((r) => {
      const d = String(r.maintenance_date || '').slice(0, 10);
      if (!d) return;
      m.set(d, (m.get(d) || 0) + 1);
    });
    return [...m.entries()].sort((a, b) => a[0].localeCompare(b[0]))
      .map(([maintenance_date, repairs_logged]) => ({ maintenance_date, repairs_logged }));
  }, [repairs]);

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
    { key: 'current_component_age_days', label: 'Age (days)', num: true, d: 0 },
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
      <Section accent={TAB_COLOR.ps2}
        eyebrow="Component grain"
        title="Which parts carry the burden"
        sub="Component serials ranked by an explainable priority score built from OOS episodes, validated failures and observed days."
        right={<FleetChips value={fleet} onChange={setFleet} />}
      >
        {/* "78 rows", twice, was the 11-Apr figure; the 29-Aug run publishes
            more. Same failure mode as the six stale totals fixed on 21-Sep,
            missed then because it sits in prose rather than in a hint. The
            enrichment claim was hardcoded too, while the route returns
            hardware_age_enrichment per row. */}
        <Note>
          {ageUnavailable === scoped.length
            ? `The hardware-config enrichment produced nothing in this run: component age is null on all ${nfmt(scoped.length)} rows.`
            : `Component age is unavailable on ${nfmt(ageUnavailable)} of ${nfmt(scoped.length)} rows.`}
          {' '}Subsystem and serial number are shown alongside it. Separately, {nfmt(unattributed.length)} of
          the {nfmt(scoped.length)} rows carry serial id 0 -- the unattributed bucket, not
          a part -- so they are held out of the ranking and shown on their own below.
        </Note>
      </Section>

      <Panel title="Highest-burden component serials" hint="Priority score, not a failure count">
        <Feed feed={feeds.serials} height={340}>
          {() => <RankBars data={topSerials} xKey="value" yKey="name" height={360} colorBy={_colorBy2} fmt={_fmt5} />}
        </Feed>
      </Panel>

      <Panel
        title="Unattributed OOS volume"
        hint="Episodes with no component serial, grouped by subsystem. Not parts -- the gap in serial attribution."
      >
        <Feed feed={feeds.serials} height={280}>
          {() => (topUnattributed.length
            ? <RankBars data={topUnattributed} xKey="value" yKey="name" height={300} colorBy={_colorBy2} fmt={_fmt1} unit=" episodes" />
            : <Empty height={200}>Every episode in this run carries a component serial.</Empty>)}
        </Feed>
      </Panel>

      <Panel title="Component serials" hint={`${nfmt(identified.length)} identified serials, plus ${nfmt(unattributed.length)} unattributed rows`}>
        <Feed feed={feeds.serials} height={380}>
          {() => (
            <DataTable
              rows={scoped} columns={serialCols} height={400} pageSize={100}
              onRowClick={(r) => onAnalyse && onAnalyse(r.device_id)}
              exportName="ps2_component_serials"
            />
          )}
        </Feed>
      </Panel>

      <Section accent={TAB_COLOR.ps2}
        eyebrow="Maintenance"
        title="Did the repair help"
        sub="OOS onsets in the 30 days before a repair against the 30 days after, at subsystem level. The export carries no device id on a repair, so this cannot be read per device."
      />

      {/* THE QUESTION IS "WHO MOVED".
          A before/after table makes the reader subtract one column from the
          other, row by row, and hold the results in their head. A slope draws
          the change: a line sloping down is a repair that worked. */}
      <Panel title="Out-of-service onsets, 30 days before against 30 days after"
             hint="One line per subsystem. Sloping down means fewer onsets after the repair.">
        <Feed feed={feeds.repairs} height={340}>
          {() => {
            const byS = {};
            (repairs || []).forEach((r) => {
              const k = String(r.maintenance_component_subsystem || 'UNKNOWN');
              if (!byS[k]) byS[k] = { name: k, from: 0, to: 0 };
              byS[k].from += Number(r.pre_30d_oos_onsets) || 0;
              byS[k].to += Number(r.post_30d_oos_onsets) || 0;
            });
            const rows = Object.values(byS).filter((r) => r.from || r.to).slice(0, 12);
            return rows.length
              ? <SlopeChart data={rows} nameKey="name" fromKey="from" toKey="to"
                            fromLabel="30 days before" toLabel="30 days after" height={340} />
              : <Empty height={300}>No repair episode in this run carries a before or after count.</Empty>;
          }}
        </Feed>
      </Panel>

      <Grid cols="repeat(auto-fit,minmax(210px,1fr))" style={{ margin: '14px 0' }}>
        <Stat label="Repair episodes returned" value={nfmt(repairSignal.n)}
              foot={repairTotal
                ? `of ${nfmt(repairTotal)} in the table; the route caps at 5,000`
                : 'the route caps at 5,000'} />
        <Stat label="With any before/after signal" value={nfmt(repairSignal.nWithSignal)}
              tone={repairSignal.nWithSignal ? 'neutral' : 'warning'}
              foot="Rows where either window is non-zero" />
        <Stat label="Distinct subsystems" value={nfmt(repairSignal.subsystems.length)}
              tone={repairSignal.allUnknown ? 'warning' : 'neutral'}
              foot={repairSignal.allUnknown ? 'Every row is UNKNOWN' : repairSignal.subsystems.slice(0, 3).join(', ')} />
        {/* The route is ORDER BY maintenance_date DESC with a 5,000 cap, so
            this range is the newest slice, not the ledger. Say which. */}
        <Stat label="Repairs logged between"
              value={repairSignal.first ? dfmt(repairSignal.first) : '--'}
              foot={repairSignal.last
                ? `and ${dfmt(repairSignal.last)}${repairTotal && repairTotal > repairs.length
                    ? ` -- the newest ${nfmt(repairs.length)} of ${nfmt(repairTotal)}` : ''}`
                : ''} />
      </Grid>

      <Grid cols="repeat(auto-fit,minmax(420px,1fr))">
        <Panel
          title={repairSignal.nWithSignal ? 'Before and after, by subsystem' : 'This comparison has no signal to show'}
          hint={repairSignal.nWithSignal
            ? 'Summed across every repair on that subsystem'
            : 'Both windows are zero on every row, which means nothing was compared'}
        >
          <Feed feed={feeds.repairs} height={300}>
            {() => (repairSignal.nWithSignal ? (
              <ColumnBars
                data={bySubsystem}
                xKey="name"
                series={[
                  { key: 'pre', label: 'OOS 30d before', color: CAT[3] },
                  { key: 'post', label: 'OOS 30d after', color: CAT[0] },
                ]}
                height={300}
                fmt={_fmt1}
              />
            ) : (
              <div style={{ padding: '6px 2px' }}>
                <Badge tone="warning">Join failure, not a null result</Badge>
                <div style={{ ...font.note, marginTop: 10 }}>
                  Every one of the {nfmt(repairSignal.n)} repair episodes returned reports
                  <strong> zero OOS onsets in the 30 days before AND zero in the 30 days after</strong>,
                  and {repairSignal.allUnknown
                    ? <>every one carries subsystem <code>UNKNOWN</code></>
                    : <>the subsystems present are {repairSignal.subsystems.slice(0, 5).join(', ')}</>}.
                  {' '}Measured on the {nfmt(repairs.length)} rows this route returned, of which
                  none carry a before/after signal.
                </div>
                <div style={{ ...font.note, marginTop: 10 }}>
                  A pre/post comparison that is zero on both sides has not compared anything. The
                  repair ledger is not joining to the out-of-service events, so each episode sees an
                  empty window in both directions. Read as "repairs made no difference" this would
                  be badly wrong -- the honest reading is that the question cannot be answered from
                  this export yet.
                </div>
                <div style={{ ...font.note, marginTop: 10 }}>
                  What would fix it: a device key on the repair ledger. The export carries none, so
                  the join has to be reconstructed upstream in the notebook.
                </div>
              </div>
            ))}
          </Feed>
        </Panel>

        <Panel title="Repairs logged over time"
               hint="Volume by maintenance date. The one measure this export supports.">
          <Feed feed={feeds.repairs} height={300}>
            {() => (
              <Trend
                data={repairsByDate}
                xKey="maintenance_date"
                series={[{ key: 'repairs_logged', label: 'Repair episodes logged', color: CAT[0] }]}
                height={300}
                area
                fmt={_fmt1}
              />
            )}
          </Feed>
        </Panel>
        <Panel title="Repair records" hint={repairTotal
            ? `${nfmt((repairs || []).length)} of ${nfmt(repairTotal)} rows`
            : `${nfmt((repairs || []).length)} rows`}>
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
    // Transition and Evidence hold the only free text here and were sharing
    // width equally with seven numeric columns, so both ellipsised while the
    // numbers sat in half-empty cells. `flex` shares the free space instead
    // of pinning pixels, so this still collapses gracefully when narrow.
    { key: 'pattern_key', label: 'Transition', flex: 3 },
    { key: 'device_category', label: 'Fleet', render: (r) => deviceShort(r.device_category) },
    { key: 'edge_support', label: 'Observed', num: true },
    { key: 'pre_oos_rate', label: 'Precedes OOS', num: true, d: 3 },
    { key: 'pre_oos_wilson_lower_95', label: 'Lower 95%', num: true, d: 3 },
    { key: 'pre_oos_lift_vs_category', label: 'Lift vs fleet', num: true, d: 2 },
    { key: 'median_edge_lag_seconds', label: 'Median lag (s)', num: true, d: 0 },
    { key: 'evidence_tier', label: 'Evidence', flex: 2 },
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
      <Section accent={TAB_COLOR.ps2}
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
        {/* "A PRECEDES B" IS A GRAPH, NOT A RANKING.
            As twelve ranked rows the reader has to hold every pair in their
            head to notice that one subsystem sits upstream of four others.
            Drawn as a graph that fact is immediate. Edge width is the
            transition's support; the ranked list is still one tab away. */}
        <Panel title="What leads to what" hint="Each line is a subsystem transition observed before an out-of-service event. Thicker means more often.">
          <Feed feed={feeds.precursors} height={360}>
            {() => {
              const edges = (precursors || []).slice(0, 40).map((r) => {
                const parts = String(r.pattern_key || '').split(/->|=>|\u2192/).map((x) => x.trim()).filter(Boolean);
                return parts.length >= 2
                  ? { source: parts[0], target: parts[1], weight: Number(r.edge_support) || 1 }
                  : null;
              }).filter(Boolean);
              const ids = [...new Set(edges.flatMap((e) => [e.source, e.target]))].map((id) => ({ id, label: id }));
              return edges.length
                ? <NetworkGraph nodes={ids} edges={edges} height={360} />
                : <RankBars data={topPatterns} xKey="value" yKey="name" height={360}
                            colorBy={_colorBy2} fmt={_fmt5} />;
            }}
          </Feed>
        </Panel>
        <Panel title="Most central subsystems" hint="Flow centrality across the transition graph">
          <Feed feed={feeds.topology} height={340}>
            {() => <RankBars data={topNodes} xKey="value" yKey="name" height={360} colorBy={_colorBy2} fmt={_fmt5} />}
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

// ---------------------------------------------------------------------
// RELATIONSHIPS -- which subsystems fail together, and which one moves first.
//
// FOUR FEEDS, AND THEY ARE NOT THE SAME SIZE. Read the denominators before
// the percentages:
//
//   /ps2/phi       10 x 10, every ordered pair of the ten subsystems. The
//                  substantial one.
//   /ps2/network   ten nodes, one per subsystem, with centrality.
//   /ps2/ignition  now /ps2/serial/ignition -- the table a run writes.
//
// /ps2/paths IS GONE (23-Sep-2026). Its ten chains, each seen once, were a
// hand-written INSERT in sql/08 dated 2026-07-14, presented on screen as the
// sequences actually recorded. Nothing produces that table.
//
// ps2_phi_matrix, BY CONTRAST, DOES HAVE A PRODUCER. A note here briefly said
// it did not. write_output() keeps three device-grain table names stable --
// ps2_phi_matrix, ps2_markov_transitions, ps2_conditional_prob -- instead of
// appending "_device" to them, so the write is spelled
// write_output(phi_device_long, "phi_matrix", "device") and the literal table
// name never appears next to it. A search for the table name found only the
// stability tuple, and a miscounted grep turned that into an absence.
//
// THE COLUMN CALLED phi IS NOT A CORRELATION COEFFICIENT. Its values here run
// from -160.8 to +210.5. A phi (Matthews) coefficient is bounded to [-1, 1] by
// construction, so whatever this column holds, it is not that -- it behaves
// like an unnormalised association statistic. It is rendered as relative
// strength and never as "r = ...", because printing an out-of-range number
// under a familiar name is how a plausible chart becomes a wrong one.
//
// The measured range is now printed from the data rather than quoted, and the
// producer's own arithmetic is under review: the four-way marginal product is
// computed in int64 and can overflow, which is the leading explanation for
// values this far outside [-1, 1].
// ---------------------------------------------------------------------
function RelationshipsView({ feeds }) {
  // The serial-grain family has no status view of its own -- v_ps2_v25_status
  // unions only the 20 ps2_v25_* tables -- so the vintage has to come off the
  // rows themselves.
  // ONE SENTENCE CANNOT DATE FOUR FEEDS.                      23-Sep-2026
  // These panels draw from two different generations and the section said
  // they were all "the earlier run". Ignition now comes from the
  // serial-grain notebook and is current; phi and network are the older
  // family. Each states its own vintage, from its own rows, and says
  // nothing where the route does not return one.
  const feedDate = (f) => (((f && f.rows && f.rows[0]) || {}).computed_date) || null;
  const sankeyDate = feedDate(feeds.sankey);
  const phiDate = feedDate(feeds.phi);
  const netDate = feedDate(feeds.network);
  const ignDate = feedDate(feeds.ignition);
  const tabDate = (((feeds.status && feeds.status.rows && feeds.status.rows[0]) || {}).computed_date) || null;
  const phiRows = feeds.phi.rows || [];
  const net = feeds.network.rows || [];
  const ign = feeds.ignition.rows || [];

  // Self-pairs carry no information about a RELATIONSHIP and they dominate the
  // colour scale, so they are dropped from the heat grid.
  const grid = useMemo(
    () => phiRows.filter((r) => r.sub_a !== r.sub_b)
                 .map((r) => ({ ...r, v: Number(r.phi) })),
    [phiRows]);

  // Ordered pairs are symmetric in this table, so half of them are the same
  // fact written backwards. Deduped for the ranking; the matrix keeps both
  // halves because a half-empty grid reads as missing data.
  const topPairs = useMemo(() => {
    const seen = new Set(); const out = [];
    [...grid].sort((a, b) => Math.abs(b.v) - Math.abs(a.v)).forEach((r) => {
      const k = [r.sub_a, r.sub_b].sort().join('|');
      if (seen.has(k)) return;
      seen.add(k);
      out.push({ name: `${r.sub_a} + ${r.sub_b}`, value: r.v });
    });
    return out;
  }, [grid]);

  // The caveat below used to quote "-161 to +211" from one run. Measure it.
  const phiRange = useMemo(() => {
    const vs = grid.map((r) => r.v).filter((v) => Number.isFinite(v));
    return vs.length ? { lo: Math.round(Math.min(...vs)), hi: Math.round(Math.max(...vs)) }
                     : { lo: 0, hi: 0 };
  }, [grid]);

  const nSubs = useMemo(
    () => new Set(phiRows.flatMap((r) => [r.sub_a, r.sub_b])).size, [phiRows]);
  // The ignition shares: counts in, percentages out, denominator on screen.
  const ignTot = useMemo(() => ign.reduce((t, r) => t + (Number(r.ignition_count) || 0), 0), [ign]);
  const termTot = useMemo(() => ign.reduce((t, r) => t + (Number(r.termination_count) || 0), 0), [ign]);

  return (
    <>
      <Section accent={TAB_COLOR.ps2}
        eyebrow="Previous generation"
        title="Which subsystems fail together"
        sub={`Pairwise association across the ${nfmt(nSubs)} subsystems, plus which one tends to start a chain and which tends to end it.`}
      >
        <Note>
          These panels are small. All three are rebuilt by the serial-grain run: the association
          matrix ({nfmt(nSubs)} subsystems){phiDate ? <>, as of <strong>{dfmt(phiDate)}</strong></> : null},
          centrality ({nfmt(net.length)} nodes){netDate ? <>, as of <strong>{dfmt(netDate)}</strong></> : null},
          and the ignition roles ({nfmt(ign.length)} subsystems){ignDate ? <>, as of <strong>{dfmt(ignDate)}</strong></> : null}.
          Every percentage below is over these denominators, not over the fleet.
        </Note>
      </Section>

      <Panel
        title="Association between subsystems"
        hint="Darker means the two subsystems co-occur more strongly. Self-pairs are removed; the grid is symmetric, so each pair appears twice."
        style={{ marginBottom: 16 }}
      >
        {/* DIVERGING, NOT SEQUENTIAL.                          06-Aug-2026
            This statistic is SIGNED -- the note below records a range of
            roughly -161 to +211. A single-hue sequential ramp maps "strongly
            negative" and "near zero" to similar light tints, so two
            subsystems that actively avoid each other looked the same as two
            with no relationship at all. A diverging scale puts a neutral
            midpoint at zero and opposite hues either side, which is the only
            encoding where the SIGN is visible. */}
        <Feed feed={feeds.phi} height={380}>
          {() => (
          <HeatGrid
            rows={grid} rowKey="sub_a" colKey="sub_b" valKey="v" height={380}
            rowLabel="Subsystem" diverging
            fmt={_fmt6}
          />
        )}
          </Feed>
        <Note>
          The published column is named <code>phi</code>, but its values here span roughly
          {' '}{nfmt(phiRange.lo)} to {nfmt(phiRange.hi)}. A phi coefficient cannot leave the range
          -1 to +1, so this is an unnormalised
          association statistic and must not be read as a correlation. Compare pairs against each
          other; do not read any single number as a strength on a 0-1 scale.
        </Note>
      </Panel>

      <Grid cols="repeat(auto-fit,minmax(400px,1fr))" style={{ marginBottom: 16 }}>
        <Panel title="Strongest pairs" hint="Ranked by absolute association, deduplicated so each pair appears once.">
          <Feed feed={feeds.phi} height={300}>
          {() => (
            <RankBars
              data={topPairs.slice(0, 10)} xKey="value" yKey="name" height={300}
              colorBy={_colorBy7}
              fmt={_fmt6} unit="Association"
            />
          )}
          </Feed>
          <Note>
            A negative value means the pair co-occurs LESS than chance would predict -- the two
            subsystems tend not to fail on the same device-day. That is a finding, not a missing
            number, so negatives are coloured rather than hidden.
          </Note>
        </Panel>

        <Panel
          title="Which subsystem sits in the middle of a chain"
          hint="Betweenness is how often a subsystem lies on the path between two others. High betweenness means faults route through it."
        >
          <Feed feed={feeds.network} height={300}>
          {() => (
            <RankBars
              data={[...net].sort((a, b) => Number(b.betweenness) - Number(a.betweenness))
                            .map((r) => ({ name: r.node_id, value: Number(r.betweenness) }))}
              xKey="value" yKey="name" height={300} color={CAT[2]}
              fmt={_fmt8} unit="Betweenness"
            />
          )}
          </Feed>
        </Panel>
      </Grid>

      <Panel title="Starts a chain, or ends it" hint="Ignition means the cascade began here; termination means it stopped here." style={{ marginBottom: 16 }}>
        <Feed feed={feeds.ignition} height={240}>
          {() => (ign.length ? (
            <ColumnBars
              data={ign.map((r) => ({
                subsystem: r.subsystem,
                Ignites: ignTot ? (100 * (Number(r.ignition_count) || 0)) / ignTot : 0,
                Terminates: termTot ? (100 * (Number(r.termination_count) || 0)) / termTot : 0,
              }))}
              xKey="subsystem"
              series={[
                { key: 'Ignites', label: 'Starts the chain', color: STATUS.serious.fill },
                { key: 'Terminates', label: 'Ends the chain', color: CAT[3] },
              ]}
              agg="mean"
              height={240}
              fmt={_fmt9}
            />
          ) : <Empty height={240}>No ignition rows published.</Empty>)}
        </Feed>
        <Note>
          {nfmt(ign.length)} subsystems carry a role. Each bar is that subsystem's share of all
          {' '}{nfmt(ignTot)} chain starts, and of all {nfmt(termTot)} chain ends, so the two
          series each total 100% across the chart and are not comparable cell by cell.
        </Note>
      </Panel>

      {/* CASCADE FLOW. Annexure 4 Dashboard 2 names this view by name. The
          table and the route already existed; only the picture was missing.
          It is fed by the serial-grain notebook, NOT by the patterns notebook
          that fills the rest of this tab, so its vintage is stated rather
          than assumed to match. */}
      {/* FIRST AND LAST, NOT NEXT.                                23-Sep-2026
          The producer builds this from first_last(chain) -- the FIRST and LAST
          subsystem of each chain -- and groups on that pair. A chain that ran
          COMMS -> SYSTEM -> BHU contributes one COMMS -> BHU edge and nothing
          about SYSTEM. The panel called it "where a cascade goes next", which
          is a different quantity the same notebook also computes, adjacent-pair,
          for the Markov matrix. Proof of the grain: this table's row marginals
          equal ignition_count and its column marginals equal termination_count,
          unit for unit, in ps2_ignition_termination_subsystem. */}
      <Panel title="Where cascades start and where they end"
             hint="Each ribbon counts cascade chains that BEGAN in the left subsystem and ENDED in the right one. It is not the next hop -- a chain that ran COMMS to SYSTEM to BHU is counted once, as COMMS to BHU.">
        <Feed feed={feeds.sankey} height={400}>
          {(rows) => (
            <>
              <SankeyFlow
                rows={rows}
                sourceKey="subsystem_from"
                targetKey="subsystem_to"
                valueKey="cascade_count"
                height={400}
                unit="cascades"
              />
              <Note>
                {/* This said "last ran for 26 Jul 2026" as a literal. Both PS2
                    producers reached 2026-08-29 on 21-Sep and the sentence went
                    stale the same day. The date now comes from the rows. When
                    the API has not been redeployed with computed_date on this
                    route the clause is dropped rather than guessed. */}
                Produced by the serial-grain analysis, a different notebook from the one behind
                the rest of this tab
                {sankeyDate ? <> — analysed as of <strong>{dfmt(sankeyDate)}</strong></> : null}.
                {sankeyDate && tabDate && sankeyDate !== tabDate
                  ? <> That is <strong>not</strong> the same day as the rest of this tab
                      ({dfmt(tabDate)}); read the shape, not the totals, until it is re-run.</>
                  : <> Counted from the first and last subsystem of each chain, not inferred, and not a hop-by-hop transition.</>}
              </Note>
            </>
          )}
        </Feed>
      </Panel>
    </>
  );
}


// ---------------------------------------------------------------------
// HOW WE KNOW, as pictures. The tables below are unchanged -- these sit
// above them and answer the same questions at a glance.
//
// Every panel here plots a number the run published about ITSELF, not about
// the fleet. That distinction matters: a low parity rate is a statement
// about two label constructions disagreeing, not about devices failing.
// ---------------------------------------------------------------------
function EvidenceVisuals({ feeds }) {
  const checks = feeds.runQuality.rows || [];
  const parity = feeds.parity.rows || [];
  const align = feeds.alignment.rows || [];
  const perf = feeds.modelPerf.rows || [];

  // The alignment feed carries something the validator investigation needs.
  // On VALIDATOR: jaccard 0.356, silver_only 0, governed_only 298,703 --
  // Failure Prediction's validator label set is entirely CONTAINED in Failure Pattern & Cascade Identification's, and Failure Pattern & Cascade Identification holds
  // ~299k device-days Failure Prediction never sees. That is independent corroboration that
  // Failure Prediction's validator label is missing events the contract defines, measured by
  // a different problem statement on the same days.
  const containment = useMemo(() => align.filter(
    (r) => String(r.device_category).toUpperCase() !== 'ALL'
           && num(r.silver_only_device_days) === 0
           && num(r.governed_only_device_days) > 0), [align]);

  const gate = useMemo(() => {
    const isPass = (c) => c.passed === true || String(c.passed) === 'true';
    const pass = checks.filter(isPass).length;
    const first = checks[0] || {};
    const disposition = first.run_disposition || null;
    return {
      pass, fail: checks.length - pass, total: checks.length,
      status: first.quality_status || null,
      disposition,
      // A warning-severity check that did not pass is what separates
      // "PASS" from "PASS_WITH_WARNINGS".
      warned: checks.some((c) => !isPass(c) && String(c.severity) !== 'critical'),
    };
  }, [checks]);

  // Precision and recall derived from the confusion counts Failure Pattern & Cascade Identification measured on
  // Failure Prediction's predictions. Published as raw counts only, so the two numbers a
  // reader actually wants were never on screen.
  // A RUN THAT DID NOT SCORE IS NOT A SCORE OF ZERO.          23-Sep-2026
  // PS2_ENABLE_PS1_PREDICTION_EVAL was defaulted to false on 20-Sep so PS2
  // would stop publishing a verdict on a parked PS1. The notebook does the
  // honest thing -- evaluation_status 'DISABLED', and NULL precision/recall,
  // because _safe_ratio returns None on a zero denominator. This tab threw
  // both away and recomputed from counts that are all zero, so the panel read
  // "0% right when it flags, 0% failures caught" for every fleet: a damning
  // and entirely fictional verdict on a model that was never run.
  //
  // ALL IS A SCOPE, NOT A FOURTH FLEET. The notebook unions an ALL row into
  // this table and into the alignment table. Charted unfiltered it appears as
  // a fourth grey bar beside Fare Gates / TVMs / Validators, and the tiles
  // counted 4 fleets against an estate of 3.
  const scored = useMemo(
    () => perf.filter((r) => String(r.device_category).toUpperCase() !== 'ALL'
                          && String(r.evaluation_status || '').toUpperCase() === 'EVALUATED'),
    [perf]
  );
  const notScored = useMemo(
    () => perf.filter((r) => String(r.device_category).toUpperCase() !== 'ALL'
                          && String(r.evaluation_status || '').toUpperCase() !== 'EVALUATED'),
    [perf]
  );
  const alignFleets = useMemo(
    () => align.filter((r) => String(r.device_category).toUpperCase() !== 'ALL'),
    [align]
  );
  const pr = useMemo(() => scored.map((r) => {
    const tp = num(r.true_positive), fp = num(r.false_positive), fn = num(r.false_negative);
    return {
      name: deviceShort(r.device_category),
      Precision: tp + fp ? (tp / (tp + fp)) * 100 : 0,
      Recall: tp + fn ? (tp / (tp + fn)) * 100 : 0,
      coverage: num(r.prediction_coverage),
    };
  }), [scored]);

  if (!checks.length && !parity.length && !align.length && !perf.length) return null;

  return (
    <>
      <Grid cols="repeat(auto-fit,minmax(210px,1fr))" style={{ marginBottom: 14 }}>
        {/* NOTHING RETURNED IS NOT EVERYTHING PASSED.             23-Sep-2026
            With checks=[] -- route errored, or not yet requested -- gate.fail
            was 0, so this painted green and read "Every check cleared" while
            showing "--" for the count. The 29-Aug run is PASS_WITH_WARNINGS;
            green is the wrong default in both directions. */}
        <Stat label="Governance checks passed"
              value={gate.total ? `${nfmt(gate.pass)} of ${nfmt(gate.total)}` : '--'}
              tone={!gate.total ? 'neutral' : (gate.fail ? 'warning' : 'good')}
              foot={!gate.total ? 'The run-quality route returned no checks'
                    : (gate.fail ? `${nfmt(gate.fail)} did not pass` : 'Every check cleared')} />
        {/* quality_status is PASS whenever no CRITICAL check failed, so a run
            with warnings reads as clean. The distinction lives only in
            run_disposition, which the route already returns and nothing showed.
            The 29-Aug run was PRODUCTION_PASS_WITH_WARNINGS with
            direct_silver_current_device_linkage at 0.56 against a 0.95 floor. */}
        <Stat label="Run disposition"
              value={gate.disposition || '--'}
              tone={gate.warned ? 'warning' : (gate.status === 'PASS' ? 'good' : 'critical')}
              foot={gate.warned
                ? 'Published with warnings — status alone would read as clean'
                : `quality_status ${gate.status || 'unknown'}`} />
        <Stat label="Fleets with an alignment measure" value={nfmt(alignFleets.length)}
              foot="Jaccard between the two definitions" />
        <Stat label="Fleets scored by Failure Pattern & Cascade Identification" value={nfmt(scored.length)}
              foot={notScored.length
                ? `${nfmt(notScored.length)} not scored this run (${notScored.map((r) => String(r.evaluation_status || 'unknown').toLowerCase()).filter((v, i, a) => a.indexOf(v) === i).join(', ')})`
                : "Failure Prediction predictions, measured against Failure Pattern & Cascade Identification's governed episodes"} />
      </Grid>

      {/* The parity panel that used to sit beside this was removed on request
          06-Aug-2026: parity did not run in this publication, so it only ever
          rendered a "Not measured" placeholder. The parity stat above still
          reports 0 fleets, which is where that fact now lives. */}
      <Grid cols="repeat(auto-fit,minmax(420px,1fr))" style={{ marginBottom: 16 }}>
        <Panel title="How much do the two definitions overlap?"
               hint="Jaccard between Failure Prediction's failure device-days and Failure Pattern & Cascade Identification's governed OOS episodes. 1.0 would mean the same set.">
          <ColumnBars
            data={alignFleets.map((r) => ({
              name: deviceShort(r.device_category),
              Overlap: num(r.definition_jaccard) * 100,
              'Failure Prediction only': num(r.silver_only_device_days),
              'Failure Pattern & Cascade Identification only': num(r.governed_only_device_days),
            }))}
            xKey="name"
            series={[{ key: 'Overlap', label: 'Jaccard %', color: CAT[2] }]}
            height={220} fmt={_fmt11}
          />
          <Note>
            Low overlap is the finding that made the OOS contract necessary. The two definitions
            were never the same set, and every cross-problem comparison made before that was
            established has to be read with this number beside it.
          </Note>
        </Panel>
      </Grid>

      {/* PACKED.                                              06-Aug-2026
          Two conditional panels, each of which had a whole horizontal plane
          to itself. auto-fit rather than a fixed 2-column grid, because
          either can be absent -- with auto-fit the survivor takes the full
          width instead of leaving a hole where the other would have been. */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(380px,1fr))',
                    gap: 12, marginBottom: 16 }}>
      {containment.length > 0 && (
        <Panel title="One label set is contained inside the other"
               hint="Where Failure Prediction has no device-days of its own, every day it marks is also marked by Failure Pattern & Cascade Identification -- and Failure Pattern & Cascade Identification marks many more.">
          <div style={{ display: 'grid', gap: 10 }}>
            {containment.map((r) => (
              <div key={r.device_category} style={{
                display: 'flex', gap: 14, alignItems: 'baseline', flexWrap: 'wrap',
                padding: '10px 12px', border: `1px solid ${LINE}`, borderRadius: 10,
                borderLeft: `3px solid ${deviceColor(r.device_category)}`,
              }}>
                <span style={{ fontWeight: 700, color: INK, minWidth: 130 }}>{deviceShort(r.device_category)}</span>
                <Badge tone="warning">{pct(num(r.definition_jaccard), 1)} overlap</Badge>
                <span style={{ ...font.note }}>
                  <strong>{nfmt(r.overlap_device_days)}</strong> device-days in both,{' '}
                  <strong>0</strong> that only Failure Prediction marks, and{' '}
                  <strong>{nfmt(r.governed_only_device_days)}</strong> that only Failure Pattern & Cascade Identification marks.
                </span>
              </div>
            ))}
          </div>
          <Note>
            This is the same gap the device pages show from the other direction. Failure Prediction's label is a
            strict subset here -- it never marks a day Failure Pattern & Cascade Identification does not, but Failure Pattern & Cascade Identification marks far more. Read
            with the Failure Prediction state panel, where a large share of that fleet reads as never having been
            out of service, it says the Failure Prediction label is missing events rather than disagreeing about
            them. It is measured by a different problem statement on the same days, which is why
            it is worth more than either screen alone.
          </Note>
        </Panel>
      )}

      {pr.length > 0 && (
        <Panel title="Failure Prediction's predictions, scored by Failure Pattern & Cascade Identification's episodes"
               hint="Precision and recall derived from the confusion counts. The route publishes the raw counts only.">
          <ColumnBars
            data={pr} xKey="name"
            series={[
              { key: 'Precision', label: 'Right when it flags', color: CAT[0] },
              { key: 'Recall', label: 'Failures caught', color: STATUS.serious.fill },
            ]}
            agg="mean"
            height={260} fmt={_fmt11}
          />
          {/* The note used to send readers to "the table below". That table
              was removed on 06-Aug, so it pointed at nothing, while
              prediction_coverage was carried into `pr` and never rendered.
              Put the coverage on screen instead. */}
          <Note>
            Scored on {pr.map((r) => `${r.name} ${pct(r.coverage, 1)}`).join(', ')} of device-days.
            A fleet scored on a small share of its device-days can post a high precision that
            says very little.
          </Note>
        </Panel>
      )}
      </div>
    </>
  );
}

function EvidenceView({ feeds }) {
  // Every figure in the evidence note comes from the run's own tables:
  // hardware-OOS events from the quality check, onsets and validated onsets
  // by summing the governance breakdown. Verified against the notebook's
  // printed counts for run 6aafe3e0 (7,919,460 / 7,494,025 / 159,981).
  const ev = useMemo(() => {
    const gov = feeds.governance ? (feeds.governance.rows || []) : [];
    const hwEvents = checkValue(feeds.runQuality.rows, 'hardware_oos_events_nonzero');
    const onsets = sumBy(gov, 'event_count', (r) => r.oos_evidence_class === 'HARDWARE_OOS_EPISODE');
    const validated = sumBy(gov, 'event_count', (r) => r.failure_evidence_class === 'VALIDATED_FAILURE_ALLOCATED');
    return {
      hwEvents, onsets, validated,
      absorbedPct: hwEvents && onsets ? ((hwEvents - onsets) / hwEvents) * 100 : null,
    };
  }, [feeds.governance, feeds.runQuality.rows]);

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
    { key: 'run_disposition', label: 'Run disposition' },
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

  const alignCols = [
    { key: 'device_category', label: 'Fleet', render: (r) => deviceShort(r.device_category) },
    { key: 'silver_ps1_failure_device_days', label: 'Failure Prediction label days', num: true },
    { key: 'governed_oos_episode_device_days', label: 'Governed OOS days', num: true },
    { key: 'overlap_device_days', label: 'Overlap', num: true },
    { key: 'silver_to_governed_overlap_rate', label: 'Failure Prediction covered', num: true, d: 3 },
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
      <Section accent={TAB_COLOR.ps2}
        eyebrow="Method"
        title="How we know, and what we do not"
        sub="Everything on this tab is about the analysis itself rather than about the estate. It is here so the numbers on the other tabs can be argued with."
      />

      <EvidenceVisuals feeds={feeds} />

      <Panel title="Governance checks" hint="Run by the notebook before it was allowed to publish">
        <Feed feed={feeds.runQuality} height={360}>
          {(rows) => <DataTable rows={rows} columns={qualityCols} height={380} pageSize={50} searchable={false} exportName="ps2_run_quality" />}
        </Feed>
      </Panel>

      <Section accent={TAB_COLOR.ps2}
        eyebrow="The label"
        title="Why prevalence is not a failure rate"
        sub="will_hardware_oos_3d marks a device-day positive if the device opens any hardware-OOS episode on the next one to three days. On a device that emits OOS events continually, almost every day qualifies."
      >
        <Note>
          {ev.hwEvents && ev.onsets && ev.validated ? (
            <>
              Read alongside the governed counts: {nfmt(ev.hwEvents)} hardware-OOS events collapse to{' '}
              {nfmt(ev.onsets)} episode onsets -- the restart-gap rule absorbs only{' '}
              {ev.absorbedPct.toFixed(1)}% -- against {nfmt(ev.validated)} validated failure onsets.
              The gap between those two numbers is what this section exists to make visible, and it
              is a property of the rule rather than of the fleet.
            </>
          ) : (
            <>
              The governed counts behind this section come from the run-quality and governance
              tables. They have not loaded, so the figures are withheld rather than estimated.
            </>
          )}
        </Note>
      </Section>

      <Grid cols="repeat(auto-fit,minmax(420px,1fr))">
        <Panel title="Positive-day rate over time" hint="Share of eligible device-days marked positive">
          <Feed feed={feeds.labelDaily} height={280}>
            {() => <Trend data={labelTrend} xKey="label_date" series={FLEET_SERIES} height={280} fmt={_fmt12} />}
          </Feed>
        </Panel>
        <Panel title="Positive rate by lookahead day" hint="Day 1, 2 and 3 ahead of the score date">
          <Feed feed={feeds.labelHorizon} height={280}>
            {() => <ColumnBars data={horizon} xKey="lead_day" series={FLEET_SERIES} agg="mean" height={280} fmt={_fmt13} />}
          </Feed>
        </Panel>
      </Grid>

      <Panel title="Label summary by fleet">
        <Feed feed={feeds.labelSummary} height={220}>
          {(rows) => <DataTable rows={rows} columns={summaryCols} height={240} pageSize={20} searchable={false} exportName="ps2_label_summary" />}
        </Feed>
      </Panel>

      {/* Removed on request 06-Aug-2026: the cross-check section header
          and the Failure Prediction model-performance table beneath it.
          Label parity and definition alignment below still carry the
          comparison, and unlike modelPerf they have rows in every run. */}
      <Grid cols="repeat(auto-fit,minmax(420px,1fr))">
        <Panel title="Label parity with Failure Prediction" hint="Same device-days, both label constructions">
          <Feed feed={feeds.parity} height={200}>
            {(rows) => <DataTable rows={rows} columns={parityCols} height={220} pageSize={20} searchable={false} exportName="ps2_label_parity" />}
          </Feed>
        </Panel>
        <Panel title="Definition alignment" hint="Failure Prediction's label against Failure Pattern & Cascade Identification's governed OOS episodes">
          <Feed feed={feeds.alignment} height={200}>
            {(rows) => <DataTable rows={rows} columns={alignCols} height={220} pageSize={20} searchable={false} exportName="ps2_definition_alignment" />}
          </Feed>
        </Panel>
      </Grid>

      <Grid cols="repeat(auto-fit,minmax(420px,1fr))">
        <Panel title="Cross-problem alignment" hint="Failure Pattern & Cascade Identification's signals against other problem statements">
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
      {/* TWO PRODUCERS MEANS TWO RUN IDS.                       23-Sep-2026
          /ps2/status used to cover only the 20 tables of a hand-written view,
          all from the patterns notebook, so one run id was the healthy state
          and this bar printed run_ids[0]. It now covers every ps2_ table in
          the database, and the serial-grain producer stamps its own id -- so
          two ids is correct and the thing worth showing is whether both
          producers landed on the SAME DATE, which is what coherent now means.
          Tables the loader does not manage (SQL seeds, and leftovers of the
          retired auto-creating push Lambda) are counted separately: they
          cannot move when a producer runs, so they must not be read as part
          of this run. */}
      <Badge tone={coherent ? 'good' : 'warning'}>
        {coherent
          ? `All ${nfmt(s.tables_managed || s.tables)} tables from one date`
          : `${(s.computed_dates || []).length} vintages across ${nfmt(s.tables_managed || s.tables)} tables`}
      </Badge>
      <span style={{ fontSize: 12.6, color: INK_2 }}>
        Analysis as of <strong style={{ color: INK }}>{s.computed_date ? dfmt(s.computed_date) : 'unknown'}</strong>
      </span>
      <span style={{ ...font.micro }}>
        {nfmt(s.tables)}/{nfmt(s.expected_tables)} tables carry rows
        {(s.producers || []).length
          ? <> &middot; {(s.producers || []).map((p) => `${String(p.run_id).slice(0, 8)} (${p.tables})`).join(' + ')}</>
          : null}
        {(s.unmanaged_tables || []).length
          ? <> &middot; <span title={(s.unmanaged_tables || []).join(', ')}>{nfmt((s.unmanaged_tables || []).length)} not loader-managed</span></>
          : null}
        {(s.empty_tables || []).length
          ? <> &middot; <span title={(s.empty_tables || []).join(', ')}>{nfmt((s.empty_tables || []).length)} empty</span></>
          : null}
      </span>
      <span style={{ ...font.micro, marginLeft: 'auto' }}>
        {/* This read "Source extract ends 11 Apr 2026" as a literal until
            20-Sep-2026, when the run moved to 29-Aug and the sentence became
            false on every view of the tab. as_of_ts is the run's own answer
            and sits in the same payload. */}
        {s.as_of_ts
          ? <>Source extract ends <strong style={{ color: INK }}>{dfmt(String(s.as_of_ts).slice(0, 10))}</strong>. Nothing here describes the estate after that date.</>
          : 'Source extract end date not reported by this run.'}
      </span>
    </div>
  );
}

export default function PS2Overview({ city = 'CHI' }) {
  const [view, setView] = useState('impact');
  // Clicking a row opens Analyse, which carries an "Open in Device 360"
  // button. Same drill-down Failure Prediction, Root Cause Analysis, Anomaly & Outlier Analysis and Remaining Useful Life & SLA Breach use, so a device found in
  // any problem statement opens with all five in view.
  const [analyse, setAnalyse] = useState(null);
  const { feeds, request } = useFeeds(city);

  useEffect(() => {
    const v = VIEWS.find((x) => x.key === view);
    request(['status', ...(v ? v.feeds : [])]);
  }, [view, request]);

  const active = VIEWS.find((v) => v.key === view) || VIEWS[0];

  return (
    <DrilldownProvider rootLabel="Failure Pattern & Cascade Identification - cascading failure">
      <div style={{ padding: '4px 2px 40px' }}>
        <StatusBar feed={feeds.status} />

        {/* The stylesheet is mounted by V2Shell, but these tabs also serve their own
            its own standalone route, where nothing else mounts it.
            Duplicate <style> tags are identical rules and are harmless. */}
        <V2Style />

        {/* Sub-tabs. Each one carries its own colour off the Failure Pattern & Cascade Identification rotation of the
            nav ramp, and the rail underneath is Failure Pattern & Cascade Identification's own colour -- so the row
            identifies both which sub-tab is open and which problem statement it
            belongs to. See theme.js navColor() for the rotation. */}
        <Tabs items={VIEWS} value={view} onChange={setView} variant="sub" parent="ps2" />

        <Breadcrumb />

        {active.key === 'impact' && <ImpactView feeds={feeds} />}
        {active.key === 'where' && <WhereView feeds={feeds} city={city} />}
        {active.key === 'devices' && <DevicesView feeds={feeds} onAnalyse={setAnalyse} />}
        {active.key === 'components' && <ComponentsView feeds={feeds} onAnalyse={setAnalyse} />}
        {active.key === 'cascades' && <CascadesView feeds={feeds} />}
        {active.key === 'relationships' && <RelationshipsView feeds={feeds} />}

        {analyse && <AnalyseModal city={city} deviceId={analyse} onClose={() => setAnalyse(null)} />}
        {active.key === 'evidence' && <EvidenceView feeds={feeds} />}
      </div>
    </DrilldownProvider>
  );
}

// FONTS_SCALED 04-Aug-2026
