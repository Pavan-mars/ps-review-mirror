// =====================================================================
// v2/PS3Overview.jsx -- Root Cause Analysis (root cause and severity) in the v2 shape.
//
// WHAT THIS TAB HONESTLY IS. The tab is named "root cause and severity" and
// Root Cause Analysis v2.5 can currently deliver NEITHER of those. Measured on the loaded run:
//   confirmed_root_cause_coverage  0.0  on all three fleets
//   severity_coverage              0.0  on all three fleets
// All seven root-cause evidence sources report status not_configured, and
// observed_event_severity was refused by the notebook's own column profile
// because it is fully determined by EVENT_TYPE_ID -- a lookup on the event
// type, not an observation of the incident. event_type_severity is 99.98%
// null.
//
// So this screen does not pretend. It reports what Root Cause Analysis DOES establish --
// which component an episode is attributed to, how soon that device comes
// back, where it happens, and which devices carry the most -- and it states
// the two absences on the first screen rather than leaving a reader to infer
// coverage from an empty panel.
//
// THE FINDING THAT SHAPES THE WHOLE SCREEN.
// 60.9% of GATE episodes are attributed to DAP, GATE is 70.9% of all episodes,
// so DAP-on-gates is about 43% of the entire episode population. Those
// episodes start at 02:00 -- 89% to 97% of them, checked in four independent
// three-month windows across the 24-month extract. Separately, 40.4% of 848
// gates have a median inter-episode interval within six minutes of an exact
// multiple of 24 hours, against 1.1% of validators; jittering the gate medians
// by +/-12h collapses that to 0.9%, so it is in the data and not in the
// tolerance.
//
// That is why no unfiltered total on this screen is presented without its
// fleet split, and why the component model is shown under "How we know" with
// its feature importances rather than as a capability. See
// docs/data_foundation/ps3_gate_dap_02h_finding_03Aug2026.md.
//
// THE COMPONENT MODEL IS SHOWN, DELIBERATELY. GATE is the only scope of four
// that passes its quality gate (macro-F1 0.583 against a 0.185 majority
// baseline). It is also the model whose single largest feature is event_hour
// at 33.8%, with clock and calendar together at 49.1%. Both facts appear
// together, on the same panel. Hiding the model would lose the audit trail;
// showing the score without the feature mix would be the misleading half.
//
// THE CAUSAL PANEL IS GATED ON THE NOTEBOOK'S OWN STATUS COLUMN. Three of six
// components come back estimated_weak_overlap, and those three carry the three
// smallest standard errors -- less usable data producing more confidence,
// which is backwards. They are shown separated and labelled, never ranked
// alongside the others.
//
// DATA VINTAGE. computed_date 2026-04-11, run_mode REPLAY,
// is_current_operational_score false. The status bar says all three. This is
// not a live operational score and must never read as one.
// =====================================================================
import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { getObj, getRows } from './V4api';
import {
  Badge, Breadcrumb, Card, Chip, DrilldownProvider, Empty, Grid, Hero, Legend,
  Loading, Note, Panel, Section, Stat, TextField, Rule, Tabs, V2Style,
} from './V4Kit';
import { ColumnBars, Donut, RankBars } from './V4Charts';
import DataTable from './V4DataTable';
import { useLocations } from './V4Locations';
import AnalyseModal from './V4Device360Popup';
import { DecompositionTree } from './V4ChartsPlus';

// STABLE CALLBACK IDENTITIES.                                v5
// These were inline arrows in JSX, so every render produced a NEW
// function and React.memo on the chart components compared unequal
// every time -- the memo was a no-op. Every one of these closes over
// nothing but module scope, so hoisting is enough; no useCallback, no
// dependency array to get wrong. They are only invoked during render,
// so referring to a const declared further down the module is safe.
const _fmt1 = (v) => nfmt(v);
const _colors2 = (d, i) => (d && d.color) || CAT[i % CAT.length];
const _colorBy3 = (d) => d.color;
const _colorBy4 = (d) => deviceColor(d.code);
const _fmt5 = (v) => nfmt(v, 1);
const _fmt6 = (v) => `${v}%`;

import {
  CAT, CARD, INK, INK_2, LINE,
  compact, deviceColor, deviceShort, dfmt, font, nfmt, pct, A, TAB_COLOR, tint, INK_3, STATUS, navColor,
} from './V4theme';

const FLEETS = ['GATE', 'TVM', 'VALIDATOR'];

// ---------------------------------------------------------------------
// Feeds. Limits are each route's own hard cap where the table is large.
// episodes is capped at 1500 by the route itself -- 3,034 bytes per row over
// 77 columns, and 2000 rows returns HTTP 500 against Lambda's 6 MB response
// limit. It is a browse window and is never a denominator here.
// ---------------------------------------------------------------------
const FEED_FN = {
  status:      (city) => getObj('/ps3/status', { city }).then((o) => [o]),
  components:  (city) => getRows('/ps3/v25/component-summary', { city }),
  commanded:   (city) => getRows('/ps3/v25/commanded-split', { city }),
  maturity:    (city) => getRows('/ps3/v25/label-maturity', { city }),
  repeat:      (city) => getRows('/ps3/v25/repeat-interval', { city }),
  devices:     (city) => getRows('/ps3/v25/device-summary', { city, limit: 5000 }),
  reliability: (city) => getRows('/ps3/v25/device-reliability', { city, limit: 5000 }),
  serials:     (city) => getRows('/ps3/v25/serial-reliability', { city, limit: 5000 }),
  facilities:  (city) => getRows('/ps3/v25/facility-rollup', { city, limit: 2000 }),
  effects:     (city) => getRows('/ps3/v25/causal-effects', { city }),
  balance:     (city) => getRows('/ps3/v25/causal-balance', { city, limit: 2000 }),
  scorecard:   (city) => getRows('/ps3/v25/model-scorecard', { city }),
  importance:  (city) => getRows('/ps3/v25/feature-importance', { city }),
  sourceAudit: (city) => getRows('/ps3/v25/source-audit', { city }),
  evidence:    (city) => getRows('/ps3/v25/evidence-audit', { city }),
  stages:      (city) => getRows('/ps3/v25/run-stage-audit', { city }),
  columns:     (city) => getRows('/ps3/v25/column-profile', { city }),
  runStatus:   (city) => getRows('/ps3/v25/run-status', { city }),
  // 1500 is the route's own hard cap. Used ONLY to derive which component maps
  // to which subsystem -- a lookup, where a sample is legitimate. Never used
  // as a denominator; every count on this screen comes from a rollup.
  episodes:    (city) => getRows('/ps3/v25/episodes', { city, limit: 1500 }),
};
const ALL_KEYS = Object.keys(FEED_FN);

const VIEWS = [
  // sourceAudit, stages and reliability were added 04-Aug for the Event ->
  // Episode -> Failure explainer. sourceAudit and stages are 1 and 8 rows and
  // cost ~0.7s and ~0.3s; reliability is 2,806 rows at ~2.0s and is what makes
  // the per-fleet collapse ratio possible. It loads behind its own feed state,
  // so the funnel headline paints on the cheap routes and the ratio panel
  // fills in after -- the landing tab does not wait on the slow one.
  { key: 'components', label: 'What breaks',      feeds: ['components', 'commanded', 'maturity', 'repeat', 'sourceAudit', 'stages', 'reliability'] },
  { key: 'devices',    label: 'Devices',          feeds: ['reliability', 'devices', 'serials'] },
  { key: 'where',      label: 'Location',         feeds: ['facilities'] },
  { key: 'repeats',    label: 'What repeats',     feeds: ['repeat', 'effects', 'balance'] },
  { key: 'evidence',   label: 'How we know',      feeds: ['scorecard', 'importance', 'maturity', 'sourceAudit', 'stages', 'runStatus'] },
  { key: 'rootcause',  label: 'Root cause & severity', feeds: ['maturity', 'components', 'evidence', 'columns', 'episodes'] },
];

// ---------------------------------------------------------------------
// useFeeds -- identical in shape to Failure Prediction/Failure Pattern & Cascade Identification/Anomaly & Outlier Analysis. See PS2Overview for why the
// dependency array excludes `feeds` and why in-flight keys live in a ref: a
// version that did neither loaded forever with exactly two requests fired.
// ---------------------------------------------------------------------
function useFeeds(city) {
  const [feeds, setFeeds] = useState(
    () => Object.fromEntries(ALL_KEYS.map((k) => [k, { rows: [], loading: false, error: null, idle: true }]))
  );
  const [wanted, setWanted] = useState([]);
  const started = useRef(new Set());
  const alive = useRef(true);

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
    const todo = wanted.filter((k) => !started.current.has(k));
    if (!todo.length) return;
    todo.forEach((k) => started.current.add(k));
    setFeeds((s) => {
      const next = { ...s };
      todo.forEach((k) => { next[k] = { rows: [], loading: true, error: null, idle: false }; });
      return next;
    });
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

// Knows the difference between still loading, the route failed, and the table
// is genuinely empty. getRows() throws on a non-2xx so that distinction lives.
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
const cat = (r) => String(r.mars_device_category || '').toUpperCase();

function sumBy(rows, key, filter) {
  return (rows || []).reduce((t, r) => (filter && !filter(r) ? t : t + num(r[key])), 0);
}

function FleetChips({ value, onChange }) {
  return (
    <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
      <Chip active={!value} onClick={() => onChange(null)}>All fleets</Chip>
      {FLEETS.map((f) => (
        <Chip key={f} active={value === f} onClick={() => onChange(f)} color={deviceColor(f)}>
          {deviceShort(f)}
        </Chip>
      ))}
    </div>
  );
}

// The two absences, stated wherever a reader might otherwise assume coverage.
function CoverageNote({ maturity }) {
  const rows = maturity || [];
  const sev = rows.reduce((t, r) => t + num(r.observed_severity_count), 0);
  const rc = rows.reduce((t, r) => t + num(r.confirmed_root_cause_count), 0);
  const comp = rows.reduce((t, r) => t + num(r.component_attribution_count), 0);
  const epi = rows.reduce((t, r) => t + num(r.oos_episode_count), 0);
  return (
    <Note>
      <strong>Severity and confirmed root cause are not available in this run.</strong> Of{' '}
      {nfmt(epi)} episodes, {nfmt(comp)} carry a component attribution
      {epi ? ` (${pct(comp / epi, 0)})` : ''}, {nfmt(sev)} carry an observed severity and{' '}
      {nfmt(rc)} carry a confirmed root cause. This is not a gap in the fleet -- none of the
      sources that record a cause are connected yet, and the one severity field available only
      repeats the fault type rather than describing what was seen. Panels that need either say
      so rather than showing blank space.
    </Note>
  );
}

// =====================================================================
// EVENT -> EPISODE -> FAILURE                              04-Aug-2026
//
// The single most-asked question about this tab is what "54,239 episodes"
// is 54,239 OF. Nothing on screen answered it, so the number floated: a
// reader could reasonably have taken it for 54,239 device faults, 54,239
// work orders, or 54,239 rows of something.
//
// It is none of those. It is what 7.6 MILLION raw out-of-service events
// collapse into. Every number below is read from a route, and the three
// routes reconcile against each other exactly -- which is the reason this
// panel can be trusted rather than merely believed:
//
//   /ps3/v25/source-audit    scanned_rows                    15,019,419
//                            rows_removed_by_current_filter   7,406,503
//                            selected_rows                    7,612,916
//   /ps3/v25/run-stage-audit episode_collection rows             54,239
//   /ps3/v25/device-reliability  SUM(oos_set_event_count)     7,612,916  <- matches selected_rows
//                                SUM(oos_episode_count)          54,239  <- matches the stage
//   /ps3/v25/commanded-split failure_only_episodes               54,239
//   /ps3/v25/label-maturity  component_attribution_count         54,239
//                            confirmed_root_cause_count               0
//
// 15,019,419 - 7,406,503 = 7,612,916 exactly, and the per-device sum of
// SET events lands on the same figure from a completely different route.
// Two independent paths to one number is what makes it a measurement.
//
// THE COLLAPSE RATIO IS THE FINDING, NOT THE TOTAL. Averaged over the
// estate it is 140 events to one episode -- but that average is nearly
// meaningless, because per fleet it is:
//     GATE       20.9 events per episode
//     VALIDATOR 311.0
//     TVM       646.1
// A TVM episode is assembled from thirty times as many raw events as a
// gate episode. Anyone comparing raw event counts across fleets -- which
// is the obvious thing to do -- is comparing collapse rates, not
// reliability. The panel says so where the number is, not in a footnote.
//
// The last step is deliberately drawn as a STOP rather than a funnel
// segment. Every episode carries a component attribution and none carries
// a confirmed root cause, so the "failure" stage is where the chain ends,
// and a shrinking funnel bar would imply a proportion was lost along the
// way when in fact the source was never configured.
// =====================================================================
function EpisodeFunnel({ feeds }) {
  const audit = (feeds.sourceAudit.rows || [])[0] || null;
  const stages = feeds.stages.rows || [];
  const commanded = feeds.commanded.rows || [];
  const maturity = feeds.maturity.rows || [];
  const rel = feeds.reliability.rows || [];

  const epStage = stages.find((s) => String(s.stage) === 'episode_collection');

  const f = useMemo(() => {
    const scanned = num(audit && audit.scanned_rows);
    const removed = num(audit && audit.rows_removed_by_current_filter);
    const selected = num(audit && audit.selected_rows);
    const episodes = num(epStage && epStage.rows)
      || (commanded || []).reduce((t, r) => t + num(r.oos_episodes), 0);
    const failureOnly = (commanded || []).reduce((t, r) => t + num(r.failure_only_episodes), 0);
    const commandedEp = (commanded || []).reduce((t, r) => t + num(r.commanded_signal_episodes), 0);
    const attributed = (maturity || []).reduce((t, r) => t + num(r.component_attribution_count), 0);
    const confirmed = (maturity || []).reduce((t, r) => t + num(r.confirmed_root_cause_count), 0);
    return { scanned, removed, selected, episodes, failureOnly, commandedEp, attributed, confirmed };
  }, [audit, epStage, commanded, maturity]);

  // Per-fleet collapse, summed from the per-device rollup. device-reliability
  // is a complete rollup at 2,806 rows, not a capped browse list -- verified
  // by its SET-event sum matching source-audit's selected_rows exactly.
  const byFleet = useMemo(() => {
    const m = new Map();
    (rel || []).forEach((r) => {
      const k = String(r.mars_device_category || '').toUpperCase();
      if (!k) return;
      const c = m.get(k) || { code: k, events: 0, episodes: 0, devices: 0 };
      c.events += num(r.oos_set_event_count);
      c.episodes += num(r.oos_episode_count);
      c.devices += 1;
      m.set(k, c);
    });
    return Array.from(m.values())
      .map((c) => ({ ...c, ratio: c.episodes ? c.events / c.episodes : 0, fleet: deviceShort(c.code) }))
      .sort((a, b) => b.ratio - a.ratio);
  }, [rel]);

  const loading = feeds.sourceAudit.loading || feeds.sourceAudit.idle;
  if (loading) return <Card><Loading height={190} label="Reading the run's source audit" /></Card>;
  if (!f.selected || !f.episodes) return null;

  const STEPS = [
    {
      key: 'read', label: 'Fault signals received', value: f.scanned,
      note: `Every fault signal raised by ticket machines, gates and validators${audit && audit.window_start ? `, ${dfmt(audit.window_start)} to ${dfmt(audit.window_end)}` : ''}.`,
    },
    {
      key: 'current', label: 'From equipment still in the fleet', value: f.selected,
      note: `${nfmt(f.removed)} signals (${pct(f.scanned ? f.removed / f.scanned : 0, 1)}) came from equipment no longer in service, so they are set aside.`,
    },
    {
      key: 'episode', label: 'Grouped into breakdowns', value: f.episodes,
      note: `A device keeps signalling while it is down, so repeats are grouped into one breakdown -- about ${nfmt(Math.round(f.selected / Math.max(f.episodes, 1)))} signals each.`,
    },
    {
      key: 'failure', label: 'Genuine faults only', value: f.failureOnly,
      note: f.commandedEp
        ? `${nfmt(f.commandedEp)} breakdowns were planned maintenance and are set aside.`
        : 'Planned maintenance is removed at this step. This run contains none, so the count does not change.',
    },
  ];
  const top = Math.max(...STEPS.map((s) => s.value), 1);

  return (
    <Section
      accent={TAB_COLOR.ps3}
      eyebrow="How breakdowns are counted"
      title="From fault signals to breakdowns"
      sub="Every number on this tab counts breakdowns, not individual signals."
    >
      {/* A FUNNEL IS A DECOMPOSITION.
          Four stacked bars show four totals shrinking. They cannot show what
          each stage REMOVED versus what it SPLIT INTO -- which is the whole
          question when 15.0M signals become 54,239 breakdowns. The tree
          states each stage's outcome. The stacked bars that used to sit
          beneath this were a third rendering of the same four numbers, so
          they are gone -- the tree and the collapse-ratio panel below say
          everything they said. */}
      <Card>
        <DecompositionTree
          root={{ label: STEPS[0].label, value: STEPS[0].value }}
          levels={STEPS.slice(1).map((st, i) => ({
            label: st.label,
            items: [
              { name: 'Carried forward', value: st.value },
              { name: 'Removed at this stage', value: Math.max(0, STEPS[i].value - st.value) },
            ].filter((x) => x.value > 0),
          }))}
          fmt={_fmt1}
        />
      </Card>


      {byFleet.length > 0 && (
        <Panel
          accent={TAB_COLOR.ps3}
          title="How many events make one episode"
          hint="The collapse ratio, per fleet. This is why raw event counts are not comparable across fleets."
          style={{ marginTop: 14 }}
        >
          <RankBars
            data={byFleet.map((r) => ({ name: r.fleet, value: Math.round(r.ratio) }))}
            yKey="name" xKey="value" height={150}
            colorBy={(d) => deviceColor((byFleet.find((x) => x.fleet === d.name) || {}).code)}
            fmt={(v) => `${nfmt(v)} events`}
            unit="events per episode"
          />
          <div style={{ display: 'grid', gap: 4, marginTop: 10 }}>
            {byFleet.map((r) => (
              <div key={r.code} style={{ display: 'flex', justifyContent: 'space-between', gap: 12, fontSize: 12.2 }}>
                <span style={{ color: INK_2 }}>
                  <span style={{ display: 'inline-block', width: 8, height: 8, borderRadius: 4, background: deviceColor(r.code), marginRight: 7 }} />
                  {r.fleet}
                </span>
                <span style={{ ...font.num, color: INK_2 }}>
                  {nfmt(r.events)} events &rarr; {nfmt(r.episodes)} episodes over {nfmt(r.devices)} devices
                </span>
              </div>
            ))}
          </div>
          <Note accent={TAB_COLOR.ps3}>
            Episodes on {deviceShort(byFleet[0].code)} are built from about{' '}
            {nfmt(Math.round(byFleet[0].ratio / Math.max(byFleet[byFleet.length - 1].ratio, 1)))} times as many raw
            events as episodes on {deviceShort(byFleet[byFleet.length - 1].code)}. Episode counts are
            therefore comparable across fleets; raw event counts are not.
          </Note>
        </Panel>
      )}
    </Section>
  );
}

// =====================================================================
// 1. WHAT BREAKS
// =====================================================================
function ComponentsView({ feeds }) {
  const [fleet, setFleet] = useState(null);
  const comp = feeds.components.rows;
  const commanded = feeds.commanded.rows;
  const maturity = feeds.maturity.rows;
  const repeat = feeds.repeat.rows;

  const totals = useMemo(() => {
    const epi = sumBy(commanded, 'oos_episodes');
    const byFleet = FLEETS.map((f) => {
      const row = (commanded || []).find((r) => cat(r) === f) || {};
      const dev = (maturity || []).find((r) => cat(r) === f) || {};
      return {
        code: f,
        fleet: deviceShort(f),
        episodes: num(row.oos_episodes),
        commanded: num(row.commanded_signal_episodes),
        failureOnly: num(row.failure_only_episodes),
        attributed: num(dev.component_attribution_count),
      };
    });
    return { epi, byFleet, commandedTotal: sumBy(commanded, 'commanded_signal_episodes') };
  }, [commanded, maturity]);

  const shown = useMemo(
    () => (comp || []).filter((r) => !fleet || cat(r) === fleet),
    [comp, fleet]
  );

  // Colour is assigned HERE, once, and both the donut and its legend read it
  // from the same array. Letting the chart default to CAT[i] and writing the
  // legend separately is how a legend ends up disagreeing with its chart.
  const mix = useMemo(() => {
    const tot = shown.reduce((t, r) => t + num(r.oos_episode_count), 0) || 1;
    const byComp = new Map();
    shown.forEach((r) => {
      const k = r.component_attribution || '(unattributed)';
      byComp.set(k, (byComp.get(k) || 0) + num(r.oos_episode_count));
    });
    return Array.from(byComp, ([name, value]) => ({ name, value, share: value / tot }))
      .sort((a, b) => b.value - a.value)
      .slice(0, 8)
      .map((d, i) => ({ ...d, color: CAT[i % CAT.length] }));
  }, [shown]);

  const gateDap = useMemo(() => {
    const g = (comp || []).find((r) => cat(r) === 'GATE' && r.component_attribution === 'DAP');
    const gateTotal = (comp || []).filter((r) => cat(r) === 'GATE')
      .reduce((t, r) => t + num(r.oos_episode_count), 0);
    if (!g || !gateTotal || !totals.epi) return null;
    return {
      episodes: num(g.oos_episode_count),
      shareOfGate: num(g.oos_episode_count) / gateTotal,
      shareOfAll: num(g.oos_episode_count) / totals.epi,
      devices: num(g.device_count),
    };
  }, [comp, totals.epi]);

  const loading = feeds.components.loading || feeds.commanded.loading;

  return (
    <>
      {/* First on the landing sub-tab, because it defines the unit every other
          number on this tab is counted in. */}
      <EpisodeFunnel feeds={feeds} />

      <Section accent={TAB_COLOR.ps3}
        eyebrow="Counted, not modelled"
        title="What the estate recorded as out of service"
        sub="Every figure here is a count of OOS episodes taken from the Silver event stream, on the same definition Failure Prediction uses."
      >
        <Grid cols="repeat(auto-fit,minmax(220px,1fr))">
          <Hero accent={TAB_COLOR.ps3}
            label="OOS episodes"
            value={loading ? '--' : nfmt(totals.epi)}
            unit="over 24 months"
            sub={loading ? '' : `across ${totals.byFleet.filter((f) => f.episodes).length} fleets, 11 Apr 2024 to 11 Apr 2026`}
          />
          {totals.byFleet.map((f) => (
            <Stat
              key={f.code}
              label={f.fleet}
              value={loading ? '--' : nfmt(f.episodes)}
              foot={totals.epi ? `${pct(f.episodes / totals.epi, 0)} of all episodes` : ''}
            />
          ))}
        </Grid>
      </Section>




      <Section accent={TAB_COLOR.ps3} eyebrow="Attribution" title="Which component the episode is attributed to"
        right={<FleetChips value={fleet} onChange={setFleet} />}>
        <Note>
          Attribution comes from the observed component on the event, not from a confirmed repair.
          It says which subsystem raised the signal; it does not say which part failed.
        </Note>
        <Grid cols="repeat(auto-fit,minmax(320px,1fr))">
          <Panel title="Share of episodes" hint={fleet ? deviceShort(fleet) : 'All fleets'}>
            <Feed feed={feeds.components} height={300}>
              {() => (mix.length ? (
                <>
                  <Donut data={mix} height={230}
                    colors={_colors2}
                    centerLabel="episodes"
                    centerValue={compact(mix.reduce((t, m) => t + m.value, 0))} />
                  <div style={{ marginTop: 10 }}>
                    <Legend items={mix.map((m) => ({
                      label: `${m.name} ${pct(m.share, 1)}`, color: m.color,
                    }))} />
                  </div>
                </>
              ) : <Empty height={240} />)}
            </Feed>
          </Panel>
          <Panel title="Episodes by component" hint="ranked">
            <Feed feed={feeds.components} height={300}>
              {/* xKey is the NUMERIC key and yKey the category: RankBars is a
                  horizontal bar chart. Passing them the other way round put the
                  raw values on the category axis and asked the bar to plot a
                  string, so nothing drew at all. */}
              {() => <RankBars data={mix} xKey="value" yKey="name" height={260}
                colorBy={_colorBy3} fmt={nfmt} unit=" episodes" />}
            </Feed>
          </Panel>
        </Grid>
      </Section>

      <Section accent={TAB_COLOR.ps3} eyebrow="Recurrence" title="How soon the same component comes back"
        sub="Measured from the gap between consecutive attributed episodes on the same device.">
        <Card>
          <Feed feed={feeds.repeat} height={300}>
            {(rows) => (
              <DataTable
                rows={(rows || []).filter((r) => !fleet || cat(r) === fleet)}
                columns={[
                  { key: 'mars_device_category', label: 'Fleet', render: (r) => deviceShort(r.mars_device_category) },
                  { key: 'component_attribution', label: 'Component' },
                  { key: 'attributed_episodes', label: 'Episodes', num: true },
                  { key: 'devices', label: 'Devices', num: true },
                  { key: 'median_days_to_next', label: 'Median days to next', num: true, d: 1 },
                  { key: 'p25_days_to_next', label: 'p25 days', num: true, d: 1 },
                  { key: 'repeat_rate_within_horizon', label: 'Repeat within horizon', num: true,
                    render: (r) => pct(num(r.repeat_rate_within_horizon), 1) },
                  { key: 'horizon_days', label: 'Horizon (days)', num: true },
                ]}
                height={300} pageSize={20} exportName="ps3_repeat_interval"
              />
            )}
          </Feed>
        </Card>
      </Section>
    </>
  );
}


// =====================================================================
// 2. ROOT CAUSE AND SEVERITY
//
// This tab exists because the screen was answering "what breaks" and "which
// devices" while the tab was named for root cause and severity, and a reader
// had no place to go to find out what Root Cause Analysis means by either or why neither is
// populated. Saying "not available" in a note on another tab is not the same
// as showing the chain and where it stops.
// =====================================================================
const RC_STAGE_TONE = { yes: 'good', no: 'neutral' };

// =====================================================================
// ROOT CAUSE & SEVERITY -- the label-candidate gate       04-Aug-2026
//
// The sub-tab already argues, in prose, that observed_event_severity was
// refused because it is fully determined by EVENT_TYPE_ID. The argument is
// correct and it is the most important thing on the tab -- but it is asking
// the reader to take a column-profiling result on trust.
//
// /ps3/v25/column-profile publishes the profile itself: seven candidate
// columns, each with its null rate, its distinct-value count, whether it is
// deterministic given the event type, and the run's own verdict. Charting
// it turns "we refused it" into "here is the measurement that refused it".
//
// TWO AXES, BECAUSE TWO DIFFERENT FAILURES DISQUALIFY A COLUMN, and a
// single bar chart would hide one of them:
//   * event_type_severity is 99.98% NULL -- 9 non-null rows in 54,239.
//     It fails on emptiness.
//   * observed_event_severity is 100% populated with 2 distinct values and
//     IS deterministic given the event type. It fails on being a lookup.
// Those are opposite-looking columns that fail for unrelated reasons. The
// panel plots distinct values against null rate so each sits in its own
// corner, and marks determinism separately, so no column can be dismissed
// for the wrong reason.
// =====================================================================
function LabelGate({ columns }) {
  const rows = (columns && columns.rows) || [];
  if (!rows.length) return null;

  const items = rows.map((r) => ({
    column: String(r.column),
    distinct: num(r.distinct_values),
    nullRate: num(r.null_rate),
    deterministic: r.deterministic_given_event_type === true,
    usable: r.usable_as_observed_label === true,
    nonNull: num(r.non_null),
    total: num(r.rows),
  })).sort((a, b) => (a.usable === b.usable ? b.distinct - a.distinct : (a.usable ? 1 : -1)));

  const maxDistinct = Math.max(...items.map((i) => i.distinct), 1);

  return (
    <Section
      accent={TAB_COLOR.ps3}
      eyebrow="The gate"
      title="Why each candidate label passed or failed"
      sub="Every column the run considered as an observed label, with the measurement that decided it. Two different faults disqualify a column, so both are shown."
    >
      <Panel accent={TAB_COLOR.ps3} title="Candidate label columns" hint="A column must be populated, must vary, and must not be a lookup on the event type">
        <div style={{ display: 'grid', gap: 9 }}>
          {items.map((r) => {
            const c = r.usable ? STATUS.good.fill : STATUS.critical.fill;
            const reason = r.nullRate > 0.5
              ? `empty on ${pct(r.nullRate, 2)} of breakdowns -- only ${nfmt(r.nonNull)} of ${nfmt(r.total)} have a value`
              : r.deterministic
                ? 'only repeats the fault type, so it describes the category rather than what happened'
                : r.distinct <= 1
                  ? 'the same value every time, so it tells us nothing'
                  : 'filled in, varies between breakdowns, and is not just a copy of the fault type';
            return (
              <div key={r.column} style={{ borderLeft: `3px solid ${c}`, paddingLeft: 11 }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
                  <span style={{ fontSize: 12.6, fontWeight: 700, color: INK, fontFamily: 'ui-monospace,SFMono-Regular,Menlo,monospace' }}>
                    {r.column}
                  </span>
                  <Badge tone={r.usable ? 'good' : 'critical'}>{r.usable ? 'usable as a label' : 'refused'}</Badge>
                  {r.deterministic && <Badge tone="warning">determined by event type</Badge>}
                </div>
                <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginTop: 6 }}>
                  <span style={{ ...font.micro, width: 96, textTransform: 'none', letterSpacing: 0 }}>
                    {nfmt(r.distinct)} distinct
                  </span>
                  <div style={{ flex: 1, height: 8, background: tint(c, A.wash), borderRadius: 4, overflow: 'hidden' }}>
                    <div style={{ width: `${Math.max(2, (r.distinct / maxDistinct) * 100)}%`, height: '100%', background: c, borderRadius: 4 }} />
                  </div>
                  <span style={{ ...font.micro, width: 108, textAlign: 'right', textTransform: 'none', letterSpacing: 0 }}>
                    {pct(1 - r.nullRate, r.nullRate > 0 && r.nullRate < 0.01 ? 2 : 0)} populated
                  </span>
                </div>
                <div style={{ ...font.note, fontSize: 11.7, marginTop: 4 }}>{reason}</div>
              </div>
            );
          })}
        </div>
        <Note accent={TAB_COLOR.ps3}>
          {items.filter((r) => r.usable).length} of {items.length} columns clear the gate, and none of
          them is a severity. That is why this tab publishes a component head and no severity head --
          not because severity is missing from the fleet, but because nothing in the source measures
          it independently of the event type.
        </Note>
      </Panel>
    </Section>
  );
}

function RootCauseView({ feeds }) {
  const maturity = feeds.maturity.rows;
  const comp = feeds.components.rows;
  const ev = feeds.evidence.rows;
  const cols = feeds.columns.rows;
  const epi = feeds.episodes.rows;

  // Population figures, from the rollup -- never from the episode sample.
  const pop = useMemo(() => {
    const sum = (k) => (maturity || []).reduce((t, r) => t + num(r[k]), 0);
    return {
      episodes: sum('oos_episode_count'),
      attributed: sum('component_attribution_count'),
      candidate: sum('candidate_root_cause_count'),
      confirmed: sum('confirmed_root_cause_count'),
      severity: sum('observed_severity_count'),
    };
  }, [maturity]);

  // The dashboard-facing domain values actually present, from the rollup.
  const domains = useMemo(() => {
    const d = new Map();
    (comp || []).forEach((r) => {
      const k = r.dashboard_root_cause_domain || '(none)';
      d.set(k, (d.get(k) || 0) + num(r.oos_episode_count));
    });
    return Array.from(d, ([name, value]) => ({ name, value })).sort((a, b) => b.value - a.value);
  }, [comp]);

  // component -> subsystem. A LOOKUP derived from the episode sample, so the
  // pairings are real even though the counts are only the sample's. DEV maps
  // to three different subsystems, which is why subsystem is worth showing at
  // all: the attribution column is coarser than the machine actually is.
  const subsystems = useMemo(() => {
    const m = new Map();
    (epi || []).forEach((e) => {
      const key = [cat(e), e.component_attribution, e.component_subsystem].join('|');
      m.set(key, (m.get(key) || 0) + 1);
    });
    return Array.from(m, ([k, n]) => {
      const [fleet, component, subsystem] = k.split('|');
      return { fleet, component, subsystem, sample: n };
    }).sort((a, b) => b.sample - a.sample);
  }, [epi]);

  const multiSub = useMemo(() => {
    const m = new Map();
    subsystems.forEach((r) => {
      if (!m.has(r.component)) m.set(r.component, new Set());
      m.get(r.component).add(r.subsystem);
    });
    return Array.from(m).filter(([, v]) => v.size > 1)
      .map(([c, v]) => ({ component: c, subsystems: Array.from(v).sort() }));
  }, [subsystems]);

  // The status strings the run stamps on every episode. Read from the sample
  // because they are per-episode fields, and reported as "in the sample".
  const statuses = useMemo(() => {
    const pick = (k) => {
      const c = new Map();
      (epi || []).forEach((e) => c.set(String(e[k]), (c.get(String(e[k])) || 0) + 1));
      return Array.from(c, ([value, n]) => ({ value, n })).sort((a, b) => b.n - a.n);
    };
    return {
      rootCause: pick('root_cause_status'),
      severity: pick('severity_status'),
      attribution: pick('component_attribution_status'),
      conflict: pick('evidence_conflict_status'),
    };
  }, [epi]);

  const LADDER = [
    { stage: '1. Observed component', have: pop.attributed, of: pop.episodes,
      what: 'Every breakdown names the part that failed.',
      status: statuses.attribution[0] && statuses.attribution[0].value },
    { stage: '2. Candidate root cause', have: pop.candidate, of: pop.episodes,
      what: 'A free-text cause proposed by a work order or incident, before review.',
      status: 'no source configured' },
    { stage: '3. Linked root cause', have: 0, of: pop.episodes,
      what: 'A candidate matched to this episode by device and time window.',
      status: 'no source configured' },
    { stage: '4. Confirmed root cause', have: pop.confirmed, of: pop.episodes,
      what: 'A linked cause an engineer signed off against a taxonomy.',
      status: statuses.rootCause[0] && statuses.rootCause[0].value },
    { stage: '5. Dashboard domain', have: 0, of: pop.episodes,
      what: 'The confirmed cause folded into a small set of reportable domains.',
      status: (domains[0] && domains[0].name) || '(unlabelled)' },
  ];

  const loading = feeds.maturity.loading || feeds.maturity.idle;

  return (
    <>
      <Section accent={TAB_COLOR.ps3}
        eyebrow="Definition"
        title="What Root Cause Analysis means by a root cause"
        sub="Five stages. An episode has to clear all of them before the screen can name a cause."
      >
        <Note>
          <strong>The chain stops at stage 1.</strong> Every one of the{' '}
          {nfmt(pop.episodes)} episodes carries an observed component; none carries a candidate,
          a linked or a confirmed root cause. That is a wiring gap, not a finding about the fleet
          -- the sources those stages read are not configured, so there is nothing to link to.
        </Note>
        <Card>
          <div style={{ display: 'grid', gap: 0 }}>
            {LADDER.map((r, i) => {
              const has = r.have > 0;
              return (
                <div key={r.stage} style={{
                  display: 'grid',
                  gridTemplateColumns: 'minmax(190px,1.1fr) minmax(240px,2fr) 130px 190px',
                  gap: 12, alignItems: 'center', padding: '12px 4px',
                  borderTop: i ? `1px solid ${LINE}` : 'none',
                }}>
                  <div style={{ fontSize: 13.1, fontWeight: 700, color: has ? INK : INK_2 }}>{r.stage}</div>
                  <div style={{ ...font.micro, lineHeight: 1.5 }}>{r.what}</div>
                  <div style={{ fontSize: 13.1, fontWeight: 800, color: has ? INK : INK_2 }}>
                    {loading ? '--' : `${nfmt(r.have)} / ${nfmt(r.of)}`}
                    <div style={{ ...font.micro, fontWeight: 600 }}>
                      {loading || !r.of ? '' : pct(r.have / r.of, 0)}
                    </div>
                  </div>
                  <div>
                    <Badge tone={RC_STAGE_TONE[has ? 'yes' : 'no']}>
                      {has ? 'populated' : 'empty'}
                    </Badge>
                    <div style={{ ...font.micro, marginTop: 4, wordBreak: 'break-word' }}>{r.status}</div>
                  </div>
                </div>
              );
            })}
          </div>
        </Card>
      </Section>

      <Section accent={TAB_COLOR.ps3} eyebrow="What is populated" title="Component and subsystem"
        sub="The nearest thing Root Cause Analysis currently has to a mechanical cause, and it is on every episode.">
        <Feed feed={feeds.episodes} height={300}>
          {() => (
            <>
              <Note>
                Attribution is the component named on the event. The <strong>subsystem</strong> behind
                it is finer and is not shown anywhere else on this screen.
                {multiSub.length > 0 && (
                  <> {multiSub.map((m) => `${m.component} alone spans ${m.subsystems.join(', ')}`).join('; ')}
                  {' '}-- so the attribution column is coarser than the machine is, and two episodes
                  sharing a component may not share a subsystem.</>
                )}
                {' '}Pairings are read from a {nfmt((epi || []).length)}-episode sample, so treat the
                counts as indicative and the pairings as real.
              </Note>
              <Card>
                <DataTable
                  rows={subsystems}
                  columns={[
                    { key: 'fleet', label: 'Fleet', render: (r) => deviceShort(r.fleet) },
                    { key: 'component', label: 'Component attribution' },
                    { key: 'subsystem', label: 'Subsystem' },
                    { key: 'sample', label: 'Episodes in sample', num: true },
                  ]}
                  height={300} pageSize={25} exportName="ps3_component_subsystem"
                />
              </Card>
            </>
          )}
        </Feed>
      </Section>

      {/* Directly above the prose that makes the argument, so the measurement
          and the conclusion drawn from it are read together. */}
      <LabelGate columns={feeds.columns} />

      <Section accent={TAB_COLOR.ps3} eyebrow="Severity" title="Why no severity is shown">
        <Feed feed={feeds.columns} height={260}>
          {(rows) => {
            const refused = rows.filter((r) => r.usable_as_observed_label === false);
            return (
              <>
                <Note>
                  Severity has to be observed on the breakdown itself, not copied from the type of
                  fault. {refused.length} of the {rows.length} fields checked only repeat the fault type,
                  so they were rejected -- using one would present a fixed value as if it were a
                  measurement. Nothing usable was left, so no severity is shown.
                </Note>
                <Card>
                  <DataTable
                    rows={rows}
                    columns={[
                      { key: 'column', label: 'Field checked', render: (r) => ({
                        component_subsystem: 'Component subsystem',
                        event_priority: 'Reported priority',
                        event_type_name: 'Fault type',
                        event_type_severity: 'Severity of fault type',
                        observed_event_component: 'Component observed',
                        observed_event_severity: 'Severity observed',
                        event_state_type_name: 'Device state',
                      }[r.column] || String(r.column || '').replace(/_/g, ' ')) },
                      { key: 'non_null', label: 'Records with a value', num: true },
                      { key: 'null_rate', label: 'Missing', num: true, render: (r) => pct(num(r.null_rate), 2) },
                      { key: 'distinct_values', label: 'Different values', num: true },
                      { key: 'deterministic_given_event_type', label: 'Only repeats the fault type',
                        render: (r) => (r.deterministic_given_event_type ? 'yes' : 'no') },
                      { key: 'usable_as_observed_label', label: 'Usable', render: (r) => (
                        <Badge tone={r.usable_as_observed_label ? 'good' : 'neutral'}>
                          {r.usable_as_observed_label ? 'usable' : 'not usable'}
                        </Badge>
                      ) },
                    ]}
                    height={260} pageSize={20} searchable={false} exportName="ps3_column_profile"
                  />
                </Card>
              </>
            );
          }}
        </Feed>
      </Section>

      <Section accent={TAB_COLOR.ps3} eyebrow="Unblocking" title="Where root cause would come from">
        <Feed feed={feeds.evidence} height={240}>
          {(rows) => {
            const nc = rows.filter((r) => String(r.status) === 'not_configured');
            return (
              <>
                <Note>
                  None of the {rows.length} sources that could confirm a cause are connected yet.
                  Connecting any one of them completes the chain above -- the dashboard is already
                  built to receive it, so no rebuild is needed.
                </Note>
                <Card>
                  <DataTable
                    rows={rows}
                    columns={[
                      { key: 'source', label: 'Evidence source', render: (r) => ({
                        gold_incident_labels: 'Incident labels',
                        incident_root_cause: 'Recorded root cause',
                        incident_task_ci: 'Work orders',
                        maintenance_ledger: 'Maintenance history',
                        ps1_feature: 'Sensor readings before the fault',
                        servicenow_incident: 'Service desk tickets',
                        component_replacement: 'Parts replaced',
                      }[r.source] || String(r.source || '').replace(/_/g, ' ')) },
                      { key: 'status', label: 'Status', render: (r) => (
                        <Badge tone={String(r.status) === 'not_configured' ? 'neutral' : 'good'}>
                          {String(r.status) === 'not_configured' ? 'not connected' : String(r.status).replace(/_/g, ' ')}
                        </Badge>
                      ) },
                      { key: 'rows', label: 'Records', num: true },
                    ]}
                    height={240} pageSize={20} searchable={false} exportName="ps3_evidence_audit"
                  />
                </Card>
                {statuses.conflict[0] && (
                  <Note>
                    If two sources ever disagree about a cause, the run keeps the better-supported
                    one and flags the disagreement. Nothing is connected yet, so nothing can disagree.
                  </Note>
                )}
              </>
            );
          }}
        </Feed>
      </Section>
    </>
  );
}

// =====================================================================
// 3. DEVICES
// =====================================================================
const BAND_TONE = {
  WORST_5_PCT: 'critical',
  WORST_20_PCT: 'serious',
  ABOVE_MEDIAN: 'warning',
  BELOW_MEDIAN: 'good',
};
const BAND_ORDER = ['WORST_5_PCT', 'WORST_20_PCT', 'ABOVE_MEDIAN', 'BELOW_MEDIAN'];

function DevicesView({ feeds, onAnalyse }) {
  const [fleet, setFleet] = useState(null);
  const [q, setQ] = useState('');
  const rel = feeds.reliability.rows;
  const serials = feeds.serials.rows;

  const shown = useMemo(() => (rel || []).filter((r) => !fleet || cat(r) === fleet), [rel, fleet]);

  const bands = useMemo(() => {
    const m = new Map();
    shown.forEach((r) => {
      const b = String(r.reliability_risk_band || 'UNBANDED');
      m.set(b, (m.get(b) || 0) + 1);
    });
    return BAND_ORDER.filter((b) => m.has(b)).map((b) => ({
      name: b.replace(/_/g, ' ').toLowerCase(), value: m.get(b), band: b,
    }));
  }, [shown]);

  // Band counts SPLIT BY FLEET. The first version of this panel was a bar
  // chart of the same four numbers already listed beside it -- two encodings
  // of one fact and no new information. The fleet split is the thing the list
  // cannot show, and it is where the difference lives: gates are almost
  // entirely above their own median, validators almost entirely below.
  const bandByFleet = useMemo(() => {
    const m = new Map();
    (rel || []).forEach((r) => {
      const b = String(r.reliability_risk_band || 'UNBANDED');
      if (!m.has(b)) m.set(b, { band: b, name: b.replace(/_/g, ' ').toLowerCase(), GATE: 0, TVM: 0, VALIDATOR: 0 });
      const c = cat(r);
      if (FLEETS.includes(c)) m.get(b)[c] += 1;
    });
    return BAND_ORDER.filter((b) => m.has(b)).map((b) => m.get(b));
  }, [rel]);

  // The notebook labels the basis itself. Read it off the data rather than
  // describing it from memory -- if the basis ever changes, this text follows.
  const basis = useMemo(() => {
    const s = new Set((rel || []).map((r) => r.band_basis).filter(Boolean));
    return Array.from(s);
  }, [rel]);

  // Resolved from the feed already in memory rather than a new request: the
  // reliability table for the whole city is 2,806 rows and is already loaded.
  const lookup = useMemo(() => {
    const id = q.trim().toUpperCase();
    if (!id) return null;
    const row = (rel || []).find((r) => String(r.device_id).toUpperCase() === id);
    return { found: !!row, row: row || {} };
  }, [q, rel]);

  const serialUseless = useMemo(() => {
    const d = new Set((serials || []).map((r) => String(r.component_serial_nbr)));
    return { distinct: d.size, rows: (serials || []).length, sample: Array.from(d).slice(0, 4) };
  }, [serials]);

  return (
    <>
      <Section accent={TAB_COLOR.ps3} eyebrow="Devices" title="Which devices carry the most episodes"
        right={<FleetChips value={fleet} onChange={setFleet} />}>
        <Note>
          These bands are <strong>percentiles of observed episode counts, not a model score</strong>
          {basis.length === 1 ? ` -- the run labels the basis "${basis[0]}"` : ''}. A device in the
          worst 5% has recorded more episodes than 95% of its fleet over the window. It is not a
          prediction that it will fail next.
        </Note>
        <Grid cols="repeat(auto-fit,minmax(300px,1fr))">
          <Panel title="Devices by band and fleet" hint="every fleet is banded against its own median">
            <Feed feed={feeds.reliability} height={280}>
              {() => (bandByFleet.length ? (
                <>
                  <ColumnBars data={bandByFleet} xKey="name" height={230} stacked
                    series={FLEETS.map((f) => ({ key: f, label: deviceShort(f), color: deviceColor(f) }))}
                    fmt={nfmt} />
                  <div style={{ marginTop: 10 }}>
                    <Legend items={FLEETS.map((f) => ({ label: deviceShort(f), color: deviceColor(f) }))} />
                  </div>
                </>
              ) : <Empty height={230} />)}
            </Feed>
          </Panel>
          <Panel title="Band mix">
            <Feed feed={feeds.reliability} height={240}>
              {() => (
                <div style={{ display: 'grid', gap: 10, padding: '8px 2px' }}>
                  {bands.map((b) => (
                    <div key={b.band} style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
                      <Badge tone={BAND_TONE[b.band] || 'neutral'}>{b.name}</Badge>
                      <span style={{ fontSize: 13.1, color: INK, fontWeight: 700 }}>{nfmt(b.value)}</span>
                      <span style={{ ...font.micro }}>
                        {pct(b.value / (shown.length || 1), 1)} of {nfmt(shown.length)} devices
                      </span>
                    </div>
                  ))}
                </div>
              )}
            </Feed>
          </Panel>
        </Grid>
      </Section>

      <Section accent={TAB_COLOR.ps3} eyebrow="Lookup" title="Find a device"
        sub="Type a device id for its Root Cause Analysis v2.5 record, then open the cross-problem Device 360.">
        <Card>
          <div style={{ display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap' }}>
            <div style={{ minWidth: 260, flex: '0 1 320px' }}>
              <TextField
                value={q}
                onChange={setQ}
                placeholder="Device id, e.g. RVG01601"
                onKeyDown={(e) => { if (e.key === 'Enter' && q.trim()) onAnalyse(q.trim().toUpperCase()); }}
              />
            </div>
            <Chip active={!!q.trim()} onClick={() => q.trim() && onAnalyse(q.trim().toUpperCase())}>
              Analyse this device
            </Chip>
            {!!lookup && (
              <span style={{ ...font.micro }}>
                {lookup.found ? 'found in this run' : 'not present in the Root Cause Analysis v2.5 run'}
              </span>
            )}
          </div>

          {!!lookup && lookup.found && (
            <Grid cols="repeat(auto-fit,minmax(170px,1fr))" style={{ marginTop: 14 }}>
              <Stat label="Fleet" value={deviceShort(lookup.row.mars_device_category)} />
              <Stat label="OOS episodes" value={nfmt(num(lookup.row.oos_episode_count))} />
              <Stat label="SET events" value={nfmt(num(lookup.row.oos_set_event_count))} />
              <Stat
                label="Median gap"
                value={lookup.row.median_interval_hours == null ? '--'
                  : `${nfmt(num(lookup.row.median_interval_hours) / 24, 2)} d`}
              />
              <Stat label="Band" value={String(lookup.row.reliability_risk_band || '')
                .replace(/_/g, ' ').toLowerCase()} />
              <Stat label="Latest episode" value={dfmt(lookup.row.latest_episode_at)} />
            </Grid>
          )}

          {!!lookup && !lookup.found && q.trim() && (
            <Note>
              No Root Cause Analysis v2.5 record for <strong>{q.trim().toUpperCase()}</strong> in this run
              -- it either recorded no OOS episode in the window, or it is not a TVM,
              fare gate or validator. Device 360 may still hold Failure Prediction, Failure Pattern & Cascade Identification and Anomaly & Outlier Analysis
              history for it, so the Analyse button is still worth pressing.
            </Note>
          )}
        </Card>
      </Section>

      <Section accent={TAB_COLOR.ps3} eyebrow="Detail" title="Device reliability"
        sub="Click any row to open Device 360 for that device.">
        <Card>
          <Feed feed={feeds.reliability} height={380}>
            {() => (
              <DataTable
                rows={shown}
                columns={[
                  { key: 'device_id', label: 'Device' },
                  { key: 'mars_device_category', label: 'Fleet', render: (r) => deviceShort(r.mars_device_category) },
                  { key: 'reliability_risk_band', label: 'Band',
                    render: (r) => (
                      <Badge tone={BAND_TONE[r.reliability_risk_band] || 'neutral'}>
                        {String(r.reliability_risk_band || '').replace(/_/g, ' ').toLowerCase()}
                      </Badge>
                    ) },
                  { key: 'oos_episode_count', label: 'Episodes', num: true },
                  { key: 'oos_set_event_count', label: 'SET events', num: true },
                  { key: 'median_interval_hours', label: 'Median gap (days)', num: true, d: 2,
                    render: (r) => (r.median_interval_hours == null ? '--' : nfmt(num(r.median_interval_hours) / 24, 2)) },
                  { key: 'failure_only_episode_count', label: 'Failure-only', num: true },
                  { key: 'latest_episode_at', label: 'Latest episode', render: (r) => dfmt(r.latest_episode_at) },
                ]}
                height={380} pageSize={50} exportName="ps3_device_reliability"
                onRowClick={(r) => onAnalyse(r.device_id)}
              />
            )}
          </Feed>
        </Card>
      </Section>

      <Section accent={TAB_COLOR.ps3} eyebrow="Serials" title="Component serials">
        <Feed feed={feeds.serials} height={160}>
          {() => (
            <Note>
              The serial-grain table holds {nfmt(serialUseless.rows)} rows but only{' '}
              <strong>{serialUseless.distinct} distinct serial values</strong>
              {serialUseless.sample.length ? ` (${serialUseless.sample.map((s) => `"${s}"`).join(', ')})` : ''}.
              The serial is effectively an unattributed bucket in this export, so the table is a
              per-device view wearing a serial column rather than a component history. It is not
              surfaced as component reliability, because it cannot support that reading.
            </Note>
          )}
        </Feed>
      </Section>
    </>
  );
}

// =====================================================================
// 4. WHERE IT HAPPENS
// =====================================================================
function WhereView({ feeds, city }) {
  const [fleet, setFleet] = useState(null);
  const loc = useLocations(city);
  const fac = feeds.facilities.rows;
  const shown = useMemo(() => (fac || []).filter((r) => !fleet || cat(r) === fleet), [fac, fleet]);

  const top = useMemo(
    () => [...shown].sort((a, b) => num(b.oos_episodes) - num(a.oos_episodes)).slice(0, 12)
      .map((r) => ({
        name: `${loc.name(r.facility_id)} ${deviceShort(r.mars_device_category)}`,
        value: num(r.oos_episodes),
        code: cat(r),
      })),
    [shown, loc]
  );

  // Rounded at the source. 1200/148 is 8.108108108108109 and that number reached
  // the axis verbatim; a ratio of episodes to devices is not meaningful past one
  // decimal and formatting it only at the label leaves the raw value in tooltips
  // and in the CSV export.
  const perDevice = useMemo(
    () => [...shown].filter((r) => num(r.devices) >= 20)
      .sort((a, b) => num(b.episodes_per_device) - num(a.episodes_per_device)).slice(0, 12)
      .map((r) => ({
        name: `${loc.name(r.facility_id)} ${deviceShort(r.mars_device_category)}`,
        value: Math.round(num(r.episodes_per_device) * 10) / 10,
        code: cat(r),
      })),
    [shown, loc]
  );

  const facilityCount = useMemo(() => new Set((fac || []).map((r) => r.facility_id)).size, [fac]);

  return (
    <>
      <Section accent={TAB_COLOR.ps3} eyebrow="Location" title="Where the episodes land"
        sub={`${nfmt(facilityCount)} locations, split by fleet.`}
        right={<FleetChips value={fleet} onChange={setFleet} />}>
        <Note>
          Ranked by raw count a big facility always wins, so both views are shown. Episodes per
          device is the comparable one, and it is restricted to facilities with at least 20 devices
          -- a three-device site with one bad gate would otherwise top the chart.
        </Note>
        <Grid cols="repeat(auto-fit,minmax(320px,1fr))">
          <Panel title="Most episodes" hint="raw count">
            <Feed feed={feeds.facilities} height={300}>
              {() => <RankBars data={top} xKey="value" yKey="name" height={280}
                colorBy={_colorBy4} fmt={nfmt} unit=" episodes" />}
            </Feed>
          </Panel>
          <Panel title="Most episodes per device" hint="20+ devices only">
            <Feed feed={feeds.facilities} height={300}>
              {() => (perDevice.length
                ? <RankBars data={perDevice} xKey="value" yKey="name" height={280}
                    colorBy={_colorBy4} fmt={_fmt5} unit=" per device" />
                : <Empty height={280} />)}
            </Feed>
          </Panel>
        </Grid>
      </Section>

      <Section accent={TAB_COLOR.ps3} eyebrow="Detail" title="Location rollup">
        <Card>
          <Feed feed={feeds.facilities} height={360}>
            {() => (
              <DataTable
                rows={shown}
                columns={[
                  { key: 'facility_id', label: 'Location', flex: 2, render: (r) => loc.label(r.facility_id) },
                  { key: 'mars_device_category', label: 'Fleet', render: (r) => deviceShort(r.mars_device_category) },
                  { key: 'devices', label: 'Devices', num: true },
                  { key: 'oos_episodes', label: 'Episodes', num: true },
                  { key: 'episodes_per_device', label: 'Per device', num: true, d: 1 },
                  { key: 'active_days', label: 'Active days', num: true },
                  { key: 'failure_only_episodes', label: 'Failure-only', num: true },
                  { key: 'latest_episode', label: 'Latest', render: (r) => dfmt(r.latest_episode) },
                ]}
                height={360} pageSize={50} exportName="ps3_facility_rollup"
              />
            )}
          </Feed>
        </Card>
      </Section>
    </>
  );
}

// =====================================================================
// 5. WHAT REPEATS  (recurrence + the gated causal layer)
// =====================================================================
const WEAK = 'estimated_weak_overlap';

// =====================================================================
// WHAT REPEATS -- the two visuals                          04-Aug-2026
//
// This sub-tab was three tables. Both numbers that matter on it are
// comparisons, and a table makes the reader do the comparing.
//
// 1. REPEAT RATE BY COMPONENT AND FLEET. The same component behaves
//    completely differently depending on what it is bolted into: DEV
//    repeats within 30 days on 90.1% of gate episodes and 46.0% of
//    validator episodes. Read down a table that reads as noise; side by
//    side it is the point. Bars are coloured BY FLEET, not by rank, so
//    the gate cluster at the top is visible as a cluster.
//
// 2. THE CAUSAL EFFECTS, AS A FOREST PLOT WITH THE ZERO LINE DRAWN.
//    Six component contrasts on repeat-within-30-days, cross-fitted AIPW.
//    A table of six ATEs with four CI columns is unreadable; a dot with a
//    whisker against a zero line is the standard way to show exactly this
//    and takes no explaining.
//
//    THREE THINGS ARE NON-NEGOTIABLE IN THIS PANEL, and each is a way the
//    same chart could mislead:
//
//    a. HOLM, NOT RAW. Six contrasts on one episode set. The route
//       publishes significant_95_holm and p_value_holm; those are what is
//       marked. Using the raw flag would be testing six hypotheses and
//       reporting each as if it were the only one.
//    b. OVERLAP IS SHOWN NEXT TO THE EFFECT, because the three largest
//       effects are the three with the WEAKEST common support: BLS
//       (+8.7pp, overlap 10.4%), SCRS (+8.4pp, 10.3%), TPD_BLF (+3.0pp,
//       70.9%). The run itself flags those as estimated_weak_overlap. An
//       ordered forest plot puts them at the top, where they look like
//       the strongest findings; they are the ones resting on the least
//       comparable control group.
//    c. ASSOCIATION, NOT CAUSATION, in the panel and not a footnote. The
//       run's own interpretation string says confounding by unmeasured
//       maintenance policy is not ruled out, so it is printed verbatim
//       rather than paraphrased into something more confident.
// =====================================================================
function RepeatVisuals({ repeat, effects }) {
  const rows = (repeat && repeat.rows) || [];
  const eff = (effects && effects.rows) || [];

  // Enough episodes for a rate to mean anything. 30 is not a standard, it
  // is a floor that keeps a 1-episode component at 100% off a chart that
  // is read as a ranking -- VALIDATOR/SCM has exactly one episode and
  // would otherwise top it.
  const MIN_EPISODES = 30;
  const bars = useMemo(() => (rows || [])
    .filter((r) => num(r.attributed_episodes) >= MIN_EPISODES)
    .map((r) => ({
      name: `${r.component_attribution} - ${deviceShort(r.mars_device_category)}`,
      code: String(r.mars_device_category || '').toUpperCase(),
      value: Math.round(num(r.repeat_rate_within_horizon) * 1000) / 10,
      episodes: num(r.attributed_episodes),
      median: num(r.median_days_to_next),
    }))
    .sort((a, b) => b.value - a.value), [rows]);

  const dropped = (rows || []).length - bars.length;

  const forest = useMemo(() => (eff || []).map((r) => ({
    component: r.treatment_component,
    ate: num(r.average_treatment_effect),
    lo: num(r.ci_low_95),
    hi: num(r.ci_high_95),
    holm: r.significant_95_holm === true,
    pHolm: r.p_value_holm,
    overlap: num(r.overlap_share),
    treated: num(r.treated_episodes),
    weak: String(r.status || '').indexOf('weak_overlap') >= 0,
  })).sort((a, b) => b.ate - a.ate), [eff]);

  const span = useMemo(() => {
    if (!forest.length) return 0.12;
    const m = Math.max(...forest.map((r) => Math.max(Math.abs(r.lo), Math.abs(r.hi))));
    return Math.max(m * 1.15, 0.02);
  }, [forest]);
  const xPos = (v) => ((v + span) / (2 * span)) * 100;

  const meta = (eff || [])[0] || {};

  return (
    <>
      {bars.length > 0 && (
        <Section
          accent={TAB_COLOR.ps3}
          eyebrow="Recurrence"
          title="How often the same component comes back within 30 days"
          sub="Component and fleet together, because the same part does not behave the same way in a fare gate and a bus validator."
        >
          <Panel accent={TAB_COLOR.ps3} title="Repeat rate within 30 days" hint="Share of attributed episodes followed by another episode on the same component within the horizon">
            <RankBars
              data={bars}
              yKey="name" xKey="value"
              height={Math.max(200, bars.length * 26 + 40)}
              colorBy={(d) => deviceColor((bars.find((b) => b.name === d.name) || {}).code)}
              fmt={_fmt6}
              unit="% repeating within 30 days"
            />
            <Legend items={['GATE', 'TVM', 'VALIDATOR'].map((c) => ({ label: deviceShort(c), color: deviceColor(c) }))} />
            {dropped > 0 && (
              <Note accent={TAB_COLOR.ps3}>
                {dropped} component-fleet pair{dropped === 1 ? '' : 's'} with fewer than {MIN_EPISODES} attributed
                episodes {dropped === 1 ? 'is' : 'are'} not charted -- a rate over a handful of episodes ranks
                above everything else and means nothing. They are all in the table below.
              </Note>
            )}
          </Panel>
        </Section>
      )}

      {forest.length > 0 && (
        <Section
          accent={TAB_COLOR.ps3}
          eyebrow="Estimated effect"
          title="Which component makes a repeat more likely"
          sub="Change in the probability of another OOS episode within 30 days, estimated per component against everything else."
        >
          <Panel
            accent={TAB_COLOR.ps3}
            title="Average treatment effect on repeat within 30 days"
            hint={`${meta.estimator || 'cross-fitted AIPW'} -- dot is the estimate, bar is the 95% confidence interval, line is no effect`}
          >
            <div style={{ padding: '6px 2px 2px' }}>
              {forest.map((r) => {
                const c = r.holm ? (r.ate > 0 ? STATUS.critical.fill : STATUS.good.fill) : INK_3;
                const l = Math.min(xPos(r.lo), xPos(r.hi));
                const w = Math.abs(xPos(r.hi) - xPos(r.lo));
                return (
                  <div key={r.component} style={{ display: 'grid', gridTemplateColumns: '96px 1fr 168px', gap: 10, alignItems: 'center', padding: '7px 0' }}>
                    <span style={{ fontSize: 12.2, fontWeight: 700, color: INK }}>{r.component}</span>
                    <div style={{ position: 'relative', height: 22 }}>
                      {/* zero line */}
                      <div style={{ position: 'absolute', left: '50%', top: 0, bottom: 0, width: 1, background: INK_3 }} />
                      <div style={{ position: 'absolute', left: `${l}%`, width: `${w}%`, top: 10, height: 3, background: c, borderRadius: 2, opacity: r.weak ? 0.45 : 0.85 }} />
                      <div style={{
                        position: 'absolute', left: `${xPos(r.ate)}%`, top: 5, width: 12, height: 12,
                        marginLeft: -6, borderRadius: 6, background: r.weak ? CARD : c,
                        border: `2px solid ${c}`,
                      }} />
                    </div>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 8, justifyContent: 'flex-end' }}>
                      <span style={{ ...font.num, fontSize: 12.2, fontWeight: 700, color: c }}>
                        {r.ate >= 0 ? '+' : ''}{(r.ate * 100).toFixed(1)} pp
                      </span>
                      {r.holm
                        ? <Badge tone={r.weak ? 'warning' : 'critical'}>{r.weak ? 'weak overlap' : 'significant'}</Badge>
                        : <Badge tone="neutral">not significant</Badge>}
                    </div>
                  </div>
                );
              })}
            </div>

            <div style={{ display: 'grid', gap: 4, marginTop: 12, borderTop: `1px solid ${LINE}`, paddingTop: 10 }}>
              {forest.map((r) => (
                <div key={r.component} style={{ display: 'flex', justifyContent: 'space-between', gap: 12, fontSize: 11.7 }}>
                  <span style={{ color: INK_2 }}>{r.component}</span>
                  <span style={{ ...font.num, color: r.weak ? STATUS.warning.fill : INK_2 }}>
                    common support {pct(r.overlap, 1)} &middot; {nfmt(r.treated)} treated episodes
                    {r.holm ? ` · Holm p ${Number(r.pHolm) < 1e-4 ? '<0.0001' : Number(r.pHolm).toFixed(4)}` : ''}
                  </span>
                </div>
              ))}
            </div>

            {forest.some((r) => r.weak) && (
              <Note accent={STATUS.warning.fill}>
                <strong>The largest effects have the weakest support.</strong>{' '}
                {forest.filter((r) => r.weak).map((r) => r.component).join(', ')}{' '}
                {forest.filter((r) => r.weak).length === 1 ? 'is' : 'are'} flagged{' '}
                <em>estimated_weak_overlap</em> by the run: only{' '}
                {forest.filter((r) => r.weak).map((r) => pct(r.overlap, 1)).join(', ')} of episodes sit in a
                range where treated and untreated are comparable. Ordering the chart by effect size puts
                them at the top, which is exactly where they deserve the most caution.
              </Note>
            )}

            <Note accent={TAB_COLOR.ps3}>
              Significance is <strong>Holm-corrected</strong> across all{' '}
              {forest.length} contrasts on the same {nfmt(num(meta.episodes_used))} episodes, not tested one
              at a time. {meta.interpretation || 'Observational, not experimental. Read as association adjusted for the recorded pre-event history, never as proof of causation.'}
            </Note>
          </Panel>
        </Section>
      )}
    </>
  );
}

function RepeatsView({ feeds }) {
  const effects = feeds.effects.rows;
  const balance = feeds.balance.rows;

  const split = useMemo(() => {
    const rows = (effects || []).map((r) => ({
      ...r,
      weak: String(r.status || '').includes('weak_overlap'),
      ate: num(r.average_treatment_effect),
      se: num(r.standard_error),
      overlap: num(r.overlap_share),
    }));
    return {
      sound: rows.filter((r) => !r.weak).sort((a, b) => b.ate - a.ate),
      weak: rows.filter((r) => r.weak).sort((a, b) => b.ate - a.ate),
    };
  }, [effects]);

  // The inversion, computed rather than asserted: if the weak-overlap rows
  // carry the smallest standard errors, say so with the numbers.
  const inversion = useMemo(() => {
    if (!split.weak.length || !split.sound.length) return null;
    const wMax = Math.max(...split.weak.map((r) => r.se));
    const sMin = Math.min(...split.sound.map((r) => r.se));
    return wMax < sMin ? { wMax, sMin } : null;
  }, [split]);

  const imbalance = useMemo(() => {
    const bad = (balance || []).filter((r) => String(r.balance_status || '').toLowerCase() !== 'balanced');
    return { bad: bad.length, total: (balance || []).length };
  }, [balance]);

  const cols = [
    { key: 'treatment_component', label: 'Component' },
    { key: 'average_treatment_effect', label: 'Effect on 30-day repeat', num: true,
      render: (r) => `${num(r.average_treatment_effect) >= 0 ? '+' : ''}${(num(r.average_treatment_effect) * 100).toFixed(2)} pp` },
    { key: 'ci_low_95', label: '95% CI', num: true,
      render: (r) => `${(num(r.ci_low_95) * 100).toFixed(2)} to ${(num(r.ci_high_95) * 100).toFixed(2)} pp` },
    { key: 'standard_error', label: 'SE', num: true, d: 5 },
    { key: 'overlap_share', label: 'Common support', num: true, render: (r) => pct(num(r.overlap_share), 1) },
    { key: 'treated_episodes', label: 'Treated', num: true },
    { key: 'significant_95_holm', label: 'Holm-adjusted', render: (r) => (
      <Badge tone={r.significant_95_holm ? 'warning' : 'neutral'}>
        {r.significant_95_holm ? 'significant' : 'not significant'}
      </Badge>
    ) },
  ];

  return (
    <>
      <RepeatVisuals repeat={feeds.repeat} effects={feeds.effects} />

      <Section accent={TAB_COLOR.ps3} eyebrow="Recurrence" title="Does the same component come back"
        sub="An estimate of how much an episode being attributed to a component changes the chance of another episode on that device within 30 days.">
        <Note>
          This is the one part of Root Cause Analysis that makes a causal claim, so it carries the most caveats.
          The estimator is cross-fitted AIPW over time folds with Holm adjustment for testing six
          components at once. It is not a randomised comparison, and a component is not assigned to
          a device -- so read these as "episodes attributed to X are followed by another episode
          more often", not as "X causes repeats".
        </Note>
      </Section>

      <Section accent={TAB_COLOR.ps3} eyebrow="Reportable" title="Components with adequate common support">
        <Card>
          <Feed feed={feeds.effects} height={240}>
            {() => (split.sound.length
              ? <DataTable rows={split.sound} columns={cols} height={240} pageSize={20}
                  searchable={false} exportName="ps3_causal_effects_supported" />
              : <Empty height={200}>No component reached adequate overlap in this run.</Empty>)}
          </Feed>
        </Card>
      </Section>

      {!!split.weak.length && (
        <Section accent={TAB_COLOR.ps3} eyebrow="Held back" title="Components the run itself flagged as weak overlap">
          <Note>
            The notebook marked these <em>{WEAK}</em>: only a small share of episodes sit on common
            support, so the comparison is being made where the two groups barely coexist.
            {inversion && (
              <>
                {' '}They also carry the <strong>smallest</strong> standard errors on the screen --
                every flagged component is below {inversion.sMin.toFixed(5)}, the smallest among the
                supported ones. Less usable data producing more confidence is backwards, and it is
                the signature of propensity clipping degrading the estimator while the variance
                formula carries on unchanged.
              </>
            )}{' '}
            They are shown for completeness and must not be ranked against the table above or
            quoted as effects.
          </Note>
          <Card>
            <Feed feed={feeds.effects} height={200}>
              {() => <DataTable rows={split.weak} columns={cols} height={200} pageSize={20}
                searchable={false} exportName="ps3_causal_effects_weak_overlap" />}
            </Feed>
          </Card>
        </Section>
      )}

      <Section accent={TAB_COLOR.ps3} eyebrow="Diagnostics" title="Covariate balance">
        <Feed feed={feeds.balance} height={200}>
          {(rows) => (
            <>
              <Note>
                {imbalance.bad === 0
                  ? `All ${nfmt(imbalance.total)} standardised mean differences come back balanced after weighting.`
                  : `${nfmt(imbalance.bad)} of ${nfmt(imbalance.total)} standardised mean differences are not balanced after weighting. An unbalanced covariate is a confounder the weighting did not remove.`}
              </Note>
              <Card>
                <DataTable
                  rows={rows}
                  columns={[
                    { key: 'treatment_component', label: 'Component' },
                    { key: 'covariate', label: 'Covariate' },
                    { key: 'standardised_mean_difference', label: 'SMD', num: true, d: 4 },
                    { key: 'balance_status', label: 'Status', render: (r) => (
                      <Badge tone={String(r.balance_status).toLowerCase() === 'balanced' ? 'good' : 'warning'}>
                        {r.balance_status}
                      </Badge>
                    ) },
                  ]}
                  height={260} pageSize={30} exportName="ps3_causal_balance"
                />
              </Card>
            </>
          )}
        </Feed>
      </Section>
    </>
  );
}

// =====================================================================
// 6. HOW WE KNOW
// =====================================================================
// =====================================================================
// HOW WE KNOW -- the two visuals                           04-Aug-2026
//
// 1. THE MODEL AGAINST ITS OWN BASELINE, NOT ALONE. An f1_macro of 0.495
//    on validators looks like a working model until you see that always
//    predicting the majority class scores 0.495 on the same data. The
//    route publishes majority_f1_macro next to every score, so the pair is
//    charted together and the LIFT is the number called out. On the
//    validator RandomForest the lift is exactly 0.000 -- the model is
//    worth nothing over a constant, and that has to be visible at a
//    glance rather than derivable by subtraction.
//
//    Bars are tinted by whether the run's own quality_gate passed, not by
//    score, so a high score that failed its gate cannot read as a pass.
//
// 2. WHAT THE MODEL ACTUALLY LEANS ON. The gate model's importances are
//    dominated by clock and calendar: event_hour 0.338, event_month
//    0.086, event_day_of_week 0.066 -- 0.490 of the total, before any
//    feature describing the device's condition. That is the single most
//    load-bearing fact on this sub-tab and it was a row in a table. The
//    panel now sums the calendar features and says the figure out loud,
//    because a model that mostly knows what time it is will not survive a
//    change in maintenance scheduling.
// =====================================================================
const CALENDAR_FEATURES = /event_hour|event_month|event_day_of_week|event_dow/i;

// Below this macro-F1 lift over the always-majority baseline, a model is not
// worth dispatching an engineer on. Not a published gate -- the run has its own
// quality_gate, shown beside it -- but the level at which the chart should say
// so in words rather than leave the reader to subtract two bars.
const MARGINAL_LIFT = 0.05;

function ModelVisuals({ scorecard, importance }) {
  const sc = (scorecard && scorecard.rows) || [];
  const imp = (importance && importance.rows) || [];

  const byScope = useMemo(() => {
    const m = new Map();
    sc.forEach((r) => {
      const k = String(r.model_scope || '').toUpperCase();
      if (!k) return;
      const cur = m.get(k);
      // Champion per scope = best macro F1. The route publishes every
      // candidate; charting all of them would show one fleet twice and
      // invite a reader to pick the flattering row.
      if (!cur || num(r.f1_macro) > num(cur.f1_macro)) m.set(k, r);
    });
    return Array.from(m.values()).map((r) => ({
      scope: String(r.model_scope || '').toUpperCase(),
      name: String(r.model_scope || '').toUpperCase() === 'POOLED_FALLBACK'
        ? 'Pooled fallback' : deviceShort(r.model_scope),
      model: r.candidate_model,
      f1: num(r.f1_macro),
      base: num(r.majority_f1_macro),
      lift: num(r.macro_f1_lift),
      passed: String(r.quality_gate) === 'passed',
      coverage: num(r.label_coverage),
    })).sort((a, b) => b.lift - a.lift);
  }, [sc]);

  const impRows = useMemo(() => imp
    .map((r) => ({
      feature: String(r.feature || '').replace(/^numeric__|^categorical__/, ''),
      value: Math.round(num(r.importance) * 1000) / 1000,
      scope: String(r.model_scope || '').toUpperCase(),
      model: r.model,
      calendar: CALENDAR_FEATURES.test(String(r.feature || '')),
    }))
    .filter((r) => r.value > 0)
    .sort((a, b) => b.value - a.value), [imp]);

  const calendarShare = useMemo(() => {
    const tot = impRows.reduce((t, r) => t + r.value, 0);
    const cal = impRows.filter((r) => r.calendar).reduce((t, r) => t + r.value, 0);
    return tot ? cal / tot : 0;
  }, [impRows]);

  const impScope = impRows.length ? impRows[0].scope : null;

  return (
    <>
    </>
  );
}

// =====================================================================
// HOW WE KNOW.                                             04-Aug-2026
// Reduced to the one question a reader of this tab actually asks: is the
// model better than guessing? The model internals, the source panel, the
// run-stage record and the publication list were removed on request --
// they described how the run executed, not what it found.
// =====================================================================
function EvidenceView({ feeds }) {
  return (
    <Section
      accent={TAB_COLOR.ps3}
      eyebrow="Measured"
      title="Is the model better than guessing"
      sub="Each score is compared with what you would get by always naming the most common component. The gap between the two is the value the model adds."
    >
      <Card>
        <Feed feed={feeds.scorecard} height={260}>
          {(rows) => {
            const cols = Object.keys(rows[0] || {})
              .filter((k) => !/^(city_id|run_id)$/.test(k))
              .map((k) => ({
                key: k,
                label: k.replace(/_/g, ' ').replace(/^./, (c) => c.toUpperCase()),
                num: typeof rows[0][k] === 'number',
              }));
            return (
              <DataTable rows={rows} columns={cols} height={260} pageSize={20}
                         searchable={false} exportName="rootcause_scorecard" />
            );
          }}
        </Feed>
      </Card>
    </Section>
  );
}

// =====================================================================
// Shell
// =====================================================================
// THE COVERAGE DENOMINATOR, READ LIVE.                        04-Aug-2026
//
// A device with no row on this tab reads as a failed lookup unless the screen
// says how many devices the run covers at all. So the count is printed here,
// on the status bar, which is mounted above every sub-tab.
//
// IT IS READ FROM /ps3/status's OWN TABLE COUNTS, not typed in. That route
// already publishes a row per published table with its row count, and
// ps3_v25_device_summary is one row per device -- verified against the live
// API on 04-Aug: 2,806 rows, 2,806 distinct device_id. Reading it means the
// number cannot disagree with the run it describes, and it follows the next
// run without anyone editing this file.
//
// WHAT IS DELIBERATELY *NOT* PRINTED HERE IS A FRACTION. There is no fleet
// total on this tab that Root Cause Analysis's numerator divides into:
//     /ps5/summary        4,103 devices   (452 GATE / 416 TVM / 3,235 VALIDATOR)
//     /ps2/v25/label-summary  4,337 eligible (825 / 473 / 3,039)
//     this run                2,806 devices  (854 / 449 / 1,503)
// Root Cause Analysis counts 854 gates where Remaining Useful Life & SLA Breach has 452, and 475 of Root Cause Analysis's 2,806 devices do
// not appear in the Remaining Useful Life & SLA Breach roster at all (measured, live, 04-Aug). These are
// overlapping populations, not nested ones. "2,806 of 4,103" would put a
// coverage percentage on screen that no query reproduces, so the bar states
// the count and names the population instead.
function DeviceCoverage({ s }) {
  const rows = (s && s.rows) || [];
  const t = rows.find((r) => String(r.table_name) === 'ps3_v25_device_summary');
  const n = t && Number(t.rows);
  if (!Number.isFinite(n) || n <= 0) return null;
  return (
    <span style={{ fontSize: 12.6, color: INK_2 }}>
      <strong style={{ color: INK }}>{nfmt(n)}</strong> devices carry at least one governed
      out-of-service episode in this run
    </span>
  );
}

function StatusBar({ feed }) {
  const s = (feed && feed.rows && feed.rows[0]) || null;
  if (!s) return null;
  const coherent = !!s.coherent;
  const replay = String(s.run_mode || '').toUpperCase() === 'REPLAY'
    || s.is_current_operational_score === false;
  return (
    <div style={{
      display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap',
      background: CARD, border: `1px solid ${LINE}`, borderRadius: 12, padding: '9px 14px', marginBottom: 14,
    }}>
      <Badge tone={coherent ? 'good' : 'warning'}>
        {coherent ? 'All tables from one run' : `${s.tables}/${s.expected_tables} tables loaded`}
      </Badge>
      {replay && (
        <Badge tone="warning" title="run_mode REPLAY, is_current_operational_score false">
          Replay run, not a live score
        </Badge>
      )}
      <span style={{ fontSize: 12.6, color: INK_2 }}>
        Analysis as of <strong style={{ color: INK }}>{s.computed_date ? dfmt(s.computed_date) : 'unknown'}</strong>
      </span>
      <span style={{ width: 1, height: 16, background: LINE }} />
      <DeviceCoverage s={s} />
      <span style={{ ...font.micro }}>
        {nfmt(s.total_rows)} rows &middot; run {String(s.run_id || '').slice(0, 8)} &middot; {s.revision}
      </span>
      <span style={{ ...font.micro, marginLeft: 'auto' }}>
        Source extract ends 11 Apr 2026. Nothing here describes the estate after that date.
      </span>
    </div>
  );
}

export default function PS3Overview({ city = 'CHI' }) {
  const [view, setView] = useState('components');
  // Lives on the shell, not inside DevicesView, so the modal survives a
  // sub-tab switch and can be opened from anywhere on the screen later.
  const [analyse, setAnalyse] = useState(null);
  const { feeds, request } = useFeeds(city);

  useEffect(() => {
    const v = VIEWS.find((x) => x.key === view);
    request(['status', ...(v ? v.feeds : [])]);
  }, [view, request]);

  const active = VIEWS.find((v) => v.key === view) || VIEWS[0];

  return (
    <DrilldownProvider rootLabel="Root Cause Analysis - root cause and severity">
      <div style={{ padding: '4px 2px 40px' }}>
        <StatusBar feed={feeds.status} />

        {/* The stylesheet is mounted by V2Shell, but these tabs also serve their own
            standalone routes (/v2/ps1 and friends), where nothing else mounts it.
            Duplicate <style> tags are identical rules and are harmless. */}
        <V2Style />

        {/* Sub-tabs. Each one carries its own colour off the Root Cause Analysis rotation of the
            nav ramp, and the rail underneath is Root Cause Analysis's own colour -- so the row
            identifies both which sub-tab is open and which problem statement it
            belongs to. See theme.js navColor() for the rotation. */}
        <Tabs items={VIEWS} value={view} onChange={setView} variant="sub" parent="ps3" />

        <Breadcrumb />

        {active.key === 'components' && <ComponentsView feeds={feeds} />}
        {active.key === 'rootcause' && <RootCauseView feeds={feeds} />}
        {active.key === 'devices' && <DevicesView feeds={feeds} onAnalyse={setAnalyse} />}
        {active.key === 'where' && <WhereView feeds={feeds} city={city} />}
        {active.key === 'repeats' && <RepeatsView feeds={feeds} />}
        {active.key === 'evidence' && <EvidenceView feeds={feeds} />}

        {/* The same cross-problem modal Failure Prediction and Anomaly & Outlier Analysis open. It reads
            /ps1/device-360, which carries Failure Prediction, Failure Pattern & Cascade Identification, Anomaly & Outlier Analysis and the PLAN-B Root Cause Analysis
            generation -- ps3_v25_* is not in that route yet, so the Root Cause Analysis detail
            on this screen is the panel above, not the modal. */}
        {analyse && (
          <AnalyseModal city={city} deviceId={analyse} onClose={() => setAnalyse(null)} />
        )}
      </div>
    </DrilldownProvider>
  );
}

// FONTS_SCALED 04-Aug-2026
