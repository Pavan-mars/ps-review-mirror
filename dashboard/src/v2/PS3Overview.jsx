// =====================================================================
// v2/PS3Overview.jsx -- PS3 (root cause and severity) in the v2 shape.
//
// WHAT THIS TAB HONESTLY IS. The tab is named "root cause and severity" and
// PS3 v2.5 can currently deliver NEITHER of those. Measured on the loaded run:
//   confirmed_root_cause_coverage  0.0  on all three fleets
//   severity_coverage              0.0  on all three fleets
// All seven root-cause evidence sources report status not_configured, and
// observed_event_severity was refused by the notebook's own column profile
// because it is fully determined by EVENT_TYPE_ID -- a lookup on the event
// type, not an observation of the incident. event_type_severity is 99.98%
// null.
//
// So this screen does not pretend. It reports what PS3 DOES establish --
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
import { getObj, getRows } from './v2api';
import {
  Badge, Breadcrumb, Card, Chip, DrilldownProvider, Empty, Grid, Hero,
  Loading, Note, Panel, Section, Stat,
} from './Kit';
import { ChartFrame, ColumnBars, Donut, RankBars } from './Charts';
import DataTable from './DataTable';
import {
  CARD, INK, INK_2, INK_3, LINE, STATUS,
  compact, deviceColor, deviceShort, dfmt, font, nfmt, pct,
} from './theme';

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
};
const ALL_KEYS = Object.keys(FEED_FN);

const VIEWS = [
  { key: 'components', label: 'What breaks',      feeds: ['components', 'commanded', 'maturity', 'repeat'] },
  { key: 'devices',    label: 'Devices',          feeds: ['reliability', 'devices', 'serials'] },
  { key: 'where',      label: 'Where it happens', feeds: ['facilities'] },
  { key: 'repeats',    label: 'What repeats',     feeds: ['repeat', 'effects', 'balance'] },
  { key: 'evidence',   label: 'How we know',      feeds: ['scorecard', 'importance', 'maturity', 'columns', 'evidence', 'sourceAudit', 'stages', 'runStatus'] },
];

// ---------------------------------------------------------------------
// useFeeds -- identical in shape to PS1/PS2/PS4. See PS2Overview for why the
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
      {nfmt(rc)} carry a confirmed root cause. That is not a gap in the fleet -- all seven
      root-cause evidence sources report <em>not_configured</em>, and the notebook refused
      observed_event_severity because it is fully determined by the event type, which makes it
      a lookup rather than an observation. Panels that would need either say so instead of
      rendering blank.
    </Note>
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

  const mix = useMemo(() => {
    const tot = shown.reduce((t, r) => t + num(r.oos_episode_count), 0) || 1;
    const byComp = new Map();
    shown.forEach((r) => {
      const k = r.component_attribution || '(unattributed)';
      byComp.set(k, (byComp.get(k) || 0) + num(r.oos_episode_count));
    });
    return Array.from(byComp, ([name, value]) => ({
      name, value, share: value / tot,
    })).sort((a, b) => b.value - a.value);
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
      <Section
        eyebrow="Counted, not modelled"
        title="What the estate recorded as out of service"
        sub="Every figure here is a count of OOS episodes taken from the Silver event stream, on the same definition PS1 uses."
      >
        <Grid cols="repeat(auto-fit,minmax(220px,1fr))">
          <Hero
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

      {!loading && gateDap && (
        <Note>
          <strong>One mode dominates every unfiltered total on this screen.</strong>{' '}
          {nfmt(gateDap.episodes)} fare-gate episodes are attributed to DAP across{' '}
          {nfmt(gateDap.devices)} devices -- {pct(gateDap.shareOfGate, 0)} of gate episodes and{' '}
          {pct(gateDap.shareOfAll, 0)} of the whole estate. Sampled across four independent
          three-month windows, 89% to 97% of them <em>start in the 02:00 hour</em>, and 40.4% of
          gates recur on an interval within six minutes of a whole number of days. Use the fleet
          selector below before drawing any conclusion about the estate as a whole.
        </Note>
      )}

      {!loading && totals.commandedTotal === 0 && (
        <Note>
          Commanded (planned) OOS is retained in the spine and flagged, per the agreed treatment --
          but <strong>zero episodes carry the commanded signal</strong> on any fleet, so every one of
          the {nfmt(totals.epi)} is counted as failure-only. Either the estate genuinely logged no
          planned outage in 24 months, or the commanded signal is not reaching this pipeline. Those
          need different responses and the split cannot tell them apart.
        </Note>
      )}

      <CoverageNote maturity={maturity} />

      <Section eyebrow="Attribution" title="Which component the episode is attributed to"
        right={<FleetChips value={fleet} onChange={setFleet} />}>
        <Note>
          Attribution comes from the observed component on the event, not from a confirmed repair.
          It says which subsystem raised the signal; it does not say which part failed.
        </Note>
        <Grid cols="repeat(auto-fit,minmax(320px,1fr))">
          <Panel title="Share of episodes" hint={fleet ? deviceShort(fleet) : 'All fleets'}>
            <Feed feed={feeds.components} height={260}>
              {() => (mix.length ? (
                <Donut data={mix.slice(0, 8)} height={240}
                  centerLabel="episodes"
                  centerValue={compact(mix.reduce((t, m) => t + m.value, 0))} />
              ) : <Empty height={240} />)}
            </Feed>
          </Panel>
          <Panel title="Episodes by component" hint="ranked">
            <Feed feed={feeds.components} height={260}>
              {() => <RankBars data={mix.slice(0, 10)} xKey="name" yKey="value" height={240} fmt={nfmt} />}
            </Feed>
          </Panel>
        </Grid>
      </Section>

      <Section eyebrow="Recurrence" title="How soon the same component comes back"
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
// 2. DEVICES
// =====================================================================
const BAND_TONE = {
  WORST_5_PCT: 'critical',
  WORST_20_PCT: 'serious',
  ABOVE_MEDIAN: 'warning',
  BELOW_MEDIAN: 'good',
};
const BAND_ORDER = ['WORST_5_PCT', 'WORST_20_PCT', 'ABOVE_MEDIAN', 'BELOW_MEDIAN'];

function DevicesView({ feeds }) {
  const [fleet, setFleet] = useState(null);
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

  // The notebook labels the basis itself. Read it off the data rather than
  // describing it from memory -- if the basis ever changes, this text follows.
  const basis = useMemo(() => {
    const s = new Set((rel || []).map((r) => r.band_basis).filter(Boolean));
    return Array.from(s);
  }, [rel]);

  const serialUseless = useMemo(() => {
    const d = new Set((serials || []).map((r) => String(r.component_serial_nbr)));
    return { distinct: d.size, rows: (serials || []).length, sample: Array.from(d).slice(0, 4) };
  }, [serials]);

  return (
    <>
      <Section eyebrow="Devices" title="Which devices carry the most episodes"
        right={<FleetChips value={fleet} onChange={setFleet} />}>
        <Note>
          These bands are <strong>percentiles of observed episode counts, not a model score</strong>
          {basis.length === 1 ? ` -- the run labels the basis "${basis[0]}"` : ''}. A device in the
          worst 5% has recorded more episodes than 95% of its fleet over the window. It is not a
          prediction that it will fail next.
        </Note>
        <Grid cols="repeat(auto-fit,minmax(300px,1fr))">
          <Panel title="Devices by band" hint={fleet ? deviceShort(fleet) : 'All fleets'}>
            <Feed feed={feeds.reliability} height={240}>
              {() => (bands.length
                ? <RankBars data={bands} xKey="name" yKey="value" height={220} fmt={nfmt} />
                : <Empty height={220} />)}
            </Feed>
          </Panel>
          <Panel title="Band mix">
            <Feed feed={feeds.reliability} height={240}>
              {() => (
                <div style={{ display: 'grid', gap: 10, padding: '8px 2px' }}>
                  {bands.map((b) => (
                    <div key={b.band} style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
                      <Badge tone={BAND_TONE[b.band] || 'neutral'}>{b.name}</Badge>
                      <span style={{ fontSize: 13, color: INK, fontWeight: 700 }}>{nfmt(b.value)}</span>
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

      <Section eyebrow="Detail" title="Device reliability">
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
              />
            )}
          </Feed>
        </Card>
      </Section>

      <Section eyebrow="Serials" title="Component serials">
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
// 3. WHERE IT HAPPENS
// =====================================================================
function WhereView({ feeds }) {
  const [fleet, setFleet] = useState(null);
  const fac = feeds.facilities.rows;
  const shown = useMemo(() => (fac || []).filter((r) => !fleet || cat(r) === fleet), [fac, fleet]);

  const top = useMemo(
    () => [...shown].sort((a, b) => num(b.oos_episodes) - num(a.oos_episodes)).slice(0, 12)
      .map((r) => ({ name: `${r.facility_id} ${deviceShort(r.mars_device_category)}`, value: num(r.oos_episodes) })),
    [shown]
  );

  const perDevice = useMemo(
    () => [...shown].filter((r) => num(r.devices) >= 20)
      .sort((a, b) => num(b.episodes_per_device) - num(a.episodes_per_device)).slice(0, 12)
      .map((r) => ({ name: `${r.facility_id} ${deviceShort(r.mars_device_category)}`, value: num(r.episodes_per_device) })),
    [shown]
  );

  const facilityCount = useMemo(() => new Set((fac || []).map((r) => r.facility_id)).size, [fac]);

  return (
    <>
      <Section eyebrow="Location" title="Where the episodes land"
        sub={`${nfmt(facilityCount)} facilities, split by fleet.`}
        right={<FleetChips value={fleet} onChange={setFleet} />}>
        <Note>
          Ranked by raw count a big facility always wins, so both views are shown. Episodes per
          device is the comparable one, and it is restricted to facilities with at least 20 devices
          -- a three-device site with one bad gate would otherwise top the chart.
        </Note>
        <Grid cols="repeat(auto-fit,minmax(320px,1fr))">
          <Panel title="Most episodes" hint="raw count">
            <Feed feed={feeds.facilities} height={300}>
              {() => <RankBars data={top} xKey="name" yKey="value" height={280} fmt={nfmt} />}
            </Feed>
          </Panel>
          <Panel title="Most episodes per device" hint="20+ devices only">
            <Feed feed={feeds.facilities} height={300}>
              {() => (perDevice.length
                ? <RankBars data={perDevice} xKey="name" yKey="value" height={280} fmt={(v) => nfmt(v, 1)} />
                : <Empty height={280} />)}
            </Feed>
          </Panel>
        </Grid>
      </Section>

      <Section eyebrow="Detail" title="Facility rollup">
        <Card>
          <Feed feed={feeds.facilities} height={360}>
            {() => (
              <DataTable
                rows={shown}
                columns={[
                  { key: 'facility_id', label: 'Facility' },
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
// 4. WHAT REPEATS  (recurrence + the gated causal layer)
// =====================================================================
const WEAK = 'estimated_weak_overlap';

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
      <Section eyebrow="Recurrence" title="Does the same component come back"
        sub="An estimate of how much an episode being attributed to a component changes the chance of another episode on that device within 30 days.">
        <Note>
          This is the one part of PS3 that makes a causal claim, so it carries the most caveats.
          The estimator is cross-fitted AIPW over time folds with Holm adjustment for testing six
          components at once. It is not a randomised comparison, and a component is not assigned to
          a device -- so read these as "episodes attributed to X are followed by another episode
          more often", not as "X causes repeats".
        </Note>
      </Section>

      <Section eyebrow="Reportable" title="Components with adequate common support">
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
        <Section eyebrow="Held back" title="Components the run itself flagged as weak overlap">
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

      <Section eyebrow="Diagnostics" title="Covariate balance">
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
// 5. HOW WE KNOW
// =====================================================================
function EvidenceView({ feeds }) {
  const score = feeds.scorecard.rows;
  const imp = feeds.importance.rows;
  const cols = feeds.columns.rows;
  const ev = feeds.evidence.rows;

  // Best model per scope, and how much of it is clock and calendar.
  const scopes = useMemo(() => {
    const byScope = new Map();
    (score || []).forEach((r) => {
      const k = r.model_scope;
      const cur = byScope.get(k);
      if (!cur || num(r.f1_macro) > num(cur.f1_macro)) byScope.set(k, r);
    });
    const CLOCK = /event_hour|event_month|event_day_of_week/;
    return Array.from(byScope.values()).map((r) => {
      const feats = (imp || []).filter((f) => f.model_scope === r.model_scope);
      const total = feats.reduce((t, f) => t + num(f.importance), 0) || 1;
      const clock = feats.filter((f) => CLOCK.test(f.feature))
        .reduce((t, f) => t + num(f.importance), 0);
      const topFeat = [...feats].sort((a, b) => num(b.importance) - num(a.importance))[0];
      return { ...r, clockShare: clock / total, topFeat, nFeatures: feats.length };
    }).sort((a, b) => num(b.f1_macro) - num(a.f1_macro));
  }, [score, imp]);

  const passing = scopes.filter((s) => String(s.quality_gate) === 'passed');
  const worstClock = scopes.filter((s) => s.nFeatures && s.clockShare >= 0.4);

  const refused = useMemo(
    () => (cols || []).filter((r) => r.usable_as_observed_label === false),
    [cols]
  );
  const notConfigured = useMemo(
    () => (ev || []).filter((r) => String(r.status) === 'not_configured'),
    [ev]
  );

  return (
    <>
      <Section eyebrow="Models" title="The component-attribution model"
        sub="One model per fleet, plus a pooled fallback. The gate compares macro-F1 against always predicting the majority component.">
        <Feed feed={feeds.scorecard} height={260}>
          {() => (
            <>
              <Note>
                {passing.length === 0
                  ? 'No scope passes its quality gate in this run.'
                  : `${passing.length} of ${scopes.length} scopes pass: ${passing.map((p) => p.model_scope).join(', ')}.`}
                {worstClock.length > 0 && (
                  <>
                    {' '}
                    <strong>Read the feature mix before the score.</strong>{' '}
                    {worstClock.map((s) => `${s.model_scope} draws ${pct(s.clockShare, 0)} of its importance from hour, day-of-week and month`).join('; ')}
                    . Gate DAP episodes -- 61% of gate episodes -- start in the 02:00 hour between
                    89% and 97% of the time, so a model separating components partly by clock is
                    substantially learning when the periodic signal fires rather than what a
                    component failure looks like. The score is real and reproducible; it is not
                    evidence of component identification.
                  </>
                )}
              </Note>
              <Card>
                <DataTable
                  rows={scopes}
                  columns={[
                    { key: 'model_scope', label: 'Scope' },
                    { key: 'candidate_model', label: 'Model' },
                    { key: 'f1_macro', label: 'Macro F1', num: true, d: 3 },
                    { key: 'majority_f1_macro', label: 'Majority baseline', num: true, d: 3 },
                    { key: 'macro_f1_lift', label: 'Lift over baseline', num: true, d: 3 },
                    { key: 'clockShare', label: 'Clock/calendar share', num: true,
                      render: (r) => (r.nFeatures ? pct(r.clockShare, 0) : '--') },
                    { key: 'quality_gate', label: 'Gate', render: (r) => (
                      <Badge tone={String(r.quality_gate) === 'passed' ? 'good' : 'neutral'}>
                        {r.quality_gate}
                      </Badge>
                    ) },
                  ]}
                  height={230} pageSize={20} searchable={false} exportName="ps3_model_scorecard"
                />
              </Card>
            </>
          )}
        </Feed>
      </Section>

      <Section eyebrow="Drivers" title="What the models actually use">
        <Card>
          <Feed feed={feeds.importance} height={300}>
            {(rows) => (
              <DataTable
                rows={[...rows].sort((a, b) => num(b.importance) - num(a.importance))}
                columns={[
                  { key: 'model_scope', label: 'Scope' },
                  { key: 'model', label: 'Model' },
                  { key: 'feature', label: 'Feature' },
                  { key: 'importance', label: 'Importance', num: true,
                    render: (r) => pct(num(r.importance), 1) },
                ]}
                height={300} pageSize={30} exportName="ps3_feature_importance"
              />
            )}
          </Feed>
        </Card>
      </Section>

      <Section eyebrow="Labels" title="Why severity was refused">
        <Feed feed={feeds.columns} height={260}>
          {(rows) => (
            <>
              <Note>
                A candidate label that is fully determined by EVENT_TYPE_ID is a lookup on the event
                type, not an observation of the incident, so the run refuses it rather than training
                on it. {refused.length} of {rows.length} profiled columns were refused on that basis.
              </Note>
              <Card>
                <DataTable
                  rows={rows}
                  columns={[
                    { key: 'column', label: 'Column' },
                    { key: 'non_null', label: 'Non-null', num: true },
                    { key: 'null_rate', label: 'Null rate', num: true, render: (r) => pct(num(r.null_rate), 2) },
                    { key: 'distinct_values', label: 'Distinct', num: true },
                    { key: 'deterministic_given_event_type', label: 'Determined by event type',
                      render: (r) => (r.deterministic_given_event_type ? 'yes' : 'no') },
                    { key: 'usable_as_observed_label', label: 'Usable', render: (r) => (
                      <Badge tone={r.usable_as_observed_label ? 'good' : 'neutral'}>
                        {r.usable_as_observed_label ? 'usable' : 'refused'}
                      </Badge>
                    ) },
                  ]}
                  height={260} pageSize={20} searchable={false} exportName="ps3_column_profile"
                />
              </Card>
            </>
          )}
        </Feed>
      </Section>

      <Section eyebrow="Sources" title="Where root cause would come from">
        <Feed feed={feeds.evidence} height={220}>
          {(rows) => (
            <>
              <Note>
                {notConfigured.length} of {rows.length} root-cause evidence sources report
                not_configured. Until at least one is wired, confirmed root cause stays empty and
                the columns that would carry it stay null -- they are present in the schema and in
                the API so they populate without a code change on the day the export lands.
              </Note>
              <Card>
                <DataTable
                  rows={rows}
                  columns={[
                    { key: 'source', label: 'Source' },
                    { key: 'status', label: 'Status', render: (r) => (
                      <Badge tone={String(r.status) === 'not_configured' ? 'neutral' : 'good'}>{r.status}</Badge>
                    ) },
                    { key: 'rows', label: 'Rows', num: true },
                    { key: 'detail', label: 'Detail' },
                  ]}
                  height={220} pageSize={20} searchable={false} exportName="ps3_evidence_audit"
                />
              </Card>
            </>
          )}
        </Feed>
      </Section>

      <Grid cols="repeat(auto-fit,minmax(340px,1fr))">
        <Panel title="OOS source" hint="what the run read">
          <Feed feed={feeds.sourceAudit} height={200}>
            {(rows) => (
              <div style={{ display: 'grid', gap: 8, padding: '4px 2px' }}>
                {rows.map((r, i) => (
                  <div key={i}>
                    <Badge tone={String(r.status) === 'loaded' ? 'good' : 'warning'}>{r.status}</Badge>
                    <div style={{ fontSize: 12.5, color: INK, marginTop: 6, fontWeight: 700 }}>{r.source}</div>
                    <div style={{ ...font.micro, marginTop: 4 }}>{r.detail}</div>
                    <div style={{ ...font.micro, marginTop: 4 }}>
                      engine {r.engine} &middot; scanned {nfmt(r.scanned_rows)} &middot; selected {nfmt(r.selected_rows)}
                    </div>
                  </div>
                ))}
              </div>
            )}
          </Feed>
        </Panel>
        <Panel title="Run stages" hint="notebook execution record">
          <Feed feed={feeds.stages} height={200}>
            {(rows) => (
              <DataTable
                rows={rows}
                columns={[
                  { key: 'stage', label: 'Stage' },
                  { key: 'status', label: 'Status' },
                  { key: 'rows', label: 'Rows', num: true },
                  { key: 'at_utc', label: 'At (UTC)', render: (r) => String(r.at_utc || '').slice(0, 19) },
                ]}
                height={200} pageSize={20} searchable={false} exportName="ps3_run_stages"
              />
            )}
          </Feed>
        </Panel>
      </Grid>

      <Section eyebrow="Publication" title="What this run published">
        <Card>
          <Feed feed={feeds.runStatus} height={280}>
            {(rows) => (
              <DataTable
                rows={rows}
                columns={[
                  { key: 'table_name', label: 'Table' },
                  { key: 'publish_status', label: 'Status' },
                  { key: 'rows', label: 'Rows', num: true },
                  { key: 'run_mode', label: 'Mode' },
                  { key: 'data_as_of_date', label: 'Data as of', render: (r) => dfmt(r.data_as_of_date) },
                ]}
                height={280} pageSize={25} exportName="ps3_run_status"
              />
            )}
          </Feed>
        </Card>
      </Section>
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
      <span style={{ fontSize: 12.5, color: INK_2 }}>
        Analysis as of <strong style={{ color: INK }}>{s.computed_date ? dfmt(s.computed_date) : 'unknown'}</strong>
      </span>
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
  const { feeds, request } = useFeeds(city);

  useEffect(() => {
    const v = VIEWS.find((x) => x.key === view);
    request(['status', ...(v ? v.feeds : [])]);
  }, [view, request]);

  const active = VIEWS.find((v) => v.key === view) || VIEWS[0];

  return (
    <DrilldownProvider rootLabel="PS3 - root cause and severity">
      <div style={{ padding: '4px 2px 40px' }}>
        <StatusBar feed={feeds.status} />

        <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginBottom: 18 }}>
          {VIEWS.map((v) => (
            <Chip key={v.key} active={view === v.key} onClick={() => setView(v.key)}>{v.label}</Chip>
          ))}
        </div>

        <Breadcrumb />

        {active.key === 'components' && <ComponentsView feeds={feeds} />}
        {active.key === 'devices' && <DevicesView feeds={feeds} />}
        {active.key === 'where' && <WhereView feeds={feeds} />}
        {active.key === 'repeats' && <RepeatsView feeds={feeds} />}
        {active.key === 'evidence' && <EvidenceView feeds={feeds} />}
      </div>
    </DrilldownProvider>
  );
}
