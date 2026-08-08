// =====================================================================
// v3/V3Rest.jsx -- PS2 to PS5, V3.                         04-Aug-2026
//
// GRAIN IS THE ORGANISING IDEA, not a detail.
//
// Every screen here separates DEVICE grain from COMPONENT grain into their
// own panels, because the two answer different questions and mixing them
// produces numbers nobody can reconcile. PS1's parts chart drew a single bar
// labelled "Unknown" for exactly this reason -- it read component columns off
// a device-grain feed, where they do not exist.
//
// Each panel states its grain in its own subtitle. If a panel cannot say
// whether it is one row per device or one row per fitted part, it is wrong.
// =====================================================================
import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { getRows, getObj } from '../v2/v2api';
import DataTable from '../v2/DataTable';
import DeviceBrief from './DeviceBrief';
import { Bubble, ColumnBars, RankBars } from '../v2/Charts';
import { CARD, INK, INK_2, INK_3, LINE, deviceShort, font, nfmt, pct } from '../v2/theme';

export const P = {
  page: '#F6F7FC',
  indigoInk: '#4F46E5', indigoFill: '#EEF0FE',
  roseInk: '#E11D6F', roseFill: '#FDEEF5',
  amberInk: '#D97706', amberFill: '#FEF4E6',
  orangeInk: '#EA6A1E', orangeFill: '#FEF0E7',
  tealInk: '#0D9488', tealFill: '#E6F7F5',
  violetInk: '#7C3AED', violetFill: '#F3EDFE',
  blueInk: '#2563EB', blueFill: '#EAF1FE',
};
const num = (v) => (v === null || v === undefined || Number.isNaN(Number(v)) ? 0 : Number(v));

// ---------------------------------------------------------------------
// Shared primitives. Same liveness rule as PS1: tied to the component, so a
// tab switch can never cancel a request that is already in the air.
// ---------------------------------------------------------------------
export function useLiveFeeds(defs, city) {
  const [feeds, setFeeds] = useState(() =>
    Object.fromEntries(Object.keys(defs).map((k) => [k, { rows: [], loading: false, idle: true, error: null }])));
  const [wanted, setWanted] = useState([]);
  const alive = useRef(true);
  useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, [city]);

  const request = useCallback((keys) => {
    setWanted((prev) => {
      const add = keys.filter((k) => !prev.includes(k));
      return add.length ? [...prev, ...add] : prev;
    });
  }, []);

  useEffect(() => {
    const todo = wanted.filter((k) => feeds[k] && feeds[k].idle);
    if (!todo.length) return;
    setFeeds((s) => {
      const n = { ...s };
      todo.forEach((k) => { n[k] = { rows: [], loading: true, idle: false, error: null }; });
      return n;
    });
    let cur = 0;
    const worker = async () => {
      for (;;) {
        const i = cur; cur += 1;
        if (i >= todo.length || !alive.current) return;
        const k = todo[i];
        let rows = []; let error = null;
        try { rows = (await defs[k](city)) || []; } catch (e) { error = String((e && e.message) || e); }
        if (!alive.current) return;
        setFeeds((s) => ({ ...s, [k]: { rows, loading: false, idle: false, error } }));
      }
    };
    Promise.all([worker(), worker()]);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [wanted, city]);

  return [feeds, request];
}

export function Tile({ icon, label, value, foot, tone = INK, fill }) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 14, flex: '1 1 210px', padding: '14px 16px',
                  background: fill || '#FFF', borderRadius: 14, border: `1px solid ${fill ? 'transparent' : LINE}` }}>
      <span style={{ width: 46, height: 46, borderRadius: 13, background: '#FFFFFFC9', flex: '0 0 auto',
                     display: 'inline-flex', alignItems: 'center', justifyContent: 'center', fontSize: 21 }}>{icon}</span>
      <div style={{ minWidth: 0 }}>
        <div style={{ ...font.micro, color: INK_3, letterSpacing: '.06em' }}>{label}</div>
        <div style={{ ...font.num, fontSize: 30, fontWeight: 800, color: tone, lineHeight: 1.15 }}>{value}</div>
        {foot && <div style={{ fontSize: 13, color: INK_2 }}>{foot}</div>}
      </div>
    </div>
  );
}

// GRAIN BADGE. Every panel wears one. It is the fastest way to stop someone
// adding a device count to a component count.
export function Panel({ icon, n, title, sub, grain, children, pad = '18px 20px' }) {
  const g = grain === 'component'
    ? { t: 'ONE ROW PER PART', c: P.violetInk, f: P.violetFill }
    : grain === 'device'
      ? { t: 'ONE ROW PER DEVICE', c: P.blueInk, f: P.blueFill }
      : grain === 'facility'
        ? { t: 'ONE ROW PER DEPOT', c: P.tealInk, f: P.tealFill }
        : null;
  return (
    <div style={{ background: CARD, border: '1px solid #ECEEF6', borderRadius: 18, padding: pad,
                  boxShadow: '0 1px 3px rgba(79,70,229,.05)' }}>
      <div style={{ display: 'flex', alignItems: 'flex-start', gap: 11, marginBottom: 14 }}>
        {icon && (
          <span style={{ width: 32, height: 32, borderRadius: 9, background: P.indigoFill, flex: '0 0 auto',
                         display: 'inline-flex', alignItems: 'center', justifyContent: 'center', fontSize: 16 }}>{icon}</span>
        )}
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ fontSize: 16.5, fontWeight: 750, color: INK }}>
            {n ? <span style={{ color: P.indigoInk, marginRight: 6 }}>{n}.</span> : null}{title}
          </div>
          {sub && <div style={{ fontSize: 13.5, color: INK_2, marginTop: 1 }}>{sub}</div>}
        </div>
        {g && (
          <span style={{ background: g.f, color: g.c, borderRadius: 999, padding: '4px 11px',
                         fontSize: 11, fontWeight: 800, letterSpacing: '.06em', whiteSpace: 'nowrap' }}>{g.t}</span>
        )}
      </div>
      {children}
    </div>
  );
}

export function Insight({ tone = 'indigo', children }) {
  return (
    <div style={{ display: 'flex', gap: 10, marginTop: 12, background: P[`${tone}Fill`], borderRadius: 11,
                  padding: '11px 14px', fontSize: 13.5, color: INK_2, lineHeight: 1.5 }}>
      <span style={{ color: P[`${tone}Ink`], fontWeight: 800 }}>&#9679;</span>
      <div>{children}</div>
    </div>
  );
}

export function Loading({ h = 200, label }) {
  return (
    <div style={{ height: h, display: 'flex', flexDirection: 'column', alignItems: 'center',
                  justifyContent: 'center', gap: 8, color: INK_3, fontSize: 13.5 }}>
      <div style={{ width: 26, height: 26, borderRadius: '50%', border: `3px solid ${LINE}`,
                    borderTopColor: P.indigoInk, animation: 'v3spin .8s linear infinite' }} />
      {label}
    </div>
  );
}

export function Feed({ feed, h = 200, slow, children }) {
  if (!feed || feed.idle || feed.loading) return <Loading h={h} label={slow ? `Loading - about ${slow}` : 'Loading'} />;
  if (feed.error) return <div style={{ height: h, display: 'flex', alignItems: 'center', justifyContent: 'center', color: P.amberInk, fontSize: 13.5 }}>Could not load this panel.</div>;
  if (!feed.rows.length) {
    return (
      <div style={{ height: h, display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', gap: 5, color: INK_3, fontSize: 13.5, textAlign: 'center', padding: '0 24px' }}>
        <strong style={{ color: INK_2 }}>This analysis has not published yet.</strong>
        <span>The screen is wired and will fill the moment the run lands. Nothing is substituted in the meantime.</span>
      </div>
    );
  }
  return children(feed.rows);
}

// Columns derived from the row itself. Six screens' worth of hand-written
// column lists would be guesswork against feeds that are still moving; the
// shape of the row is the one thing that is always true.
export function autoCols(rows, { hide = [], first = [], money = [] } = {}) {
  const r0 = rows[0] || {};
  const skip = new Set(['id', 'city_id', 'pipeline_version', 'run_id', ...hide]);
  const keys = Object.keys(r0).filter((k) => !skip.has(k));
  keys.sort((a, b) => (first.indexOf(b)) - (first.indexOf(a)));
  return keys.slice(0, 9).map((k) => ({
    key: k,
    label: k.replace(/_/g, ' ').replace(/^./, (c) => c.toUpperCase()),
    num: typeof r0[k] === 'number',
    render: money.includes(k) ? (r) => pct(num(r[k]), 1) : undefined,
  }));
}

// =====================================================================
// One screen per problem statement. Same skeleton each time:
// headline tiles, a device-grain panel, a component-grain panel, insight.
// =====================================================================
const SCREENS = {
  ps2: {
    title: 'Failure Patterns & Cascades',
    blurb: 'How failures arrive together, which devices start them, and what they drag down.',
    feeds: {
      devices: (c) => getRows('/ps2/devices', { city: c }),
      top: (c) => getRows('/ps2/topdevices', { city: c }),
      impact: (c) => getRows('/ps2/impact', { city: c }),
      windows: (c) => getRows('/ps2/windows', { city: c }),
    },
    order: ['top', 'impact', 'devices', 'windows'],
  },
  ps3: {
    title: 'Root Cause Analysis',
    blurb: 'Which part a breakdown is attributed to, and how the estate concentrates.',
    feeds: {
      devices: (c) => getRows('/ps3/v2-devices', { city: c }),
      queue: (c) => getRows('/ps3/v2-queue', { city: c }),
      components: (c) => getRows('/ps3/v2-components', { city: c }),
      rollup: (c) => getRows('/ps3/v2-rootcause-rollup', { city: c }),
    },
    order: ['queue', 'devices', 'components', 'rollup'],
  },
  ps4: {
    title: 'Anomaly & Outlier Analysis',
    blurb: 'Devices behaving unlike their peer group in a given week.',
    feeds: {
      weekly: (c) => getRows('/ps4/weekly', { city: c }),
      facility: (c) => getRows('/ps4/weekly-facility', { city: c }),
      persistent: (c) => getRows('/ps4/weekly-persistent', { city: c }),
    },
    order: ['weekly', 'persistent', 'facility'],
  },
  ps5: {
    title: 'Remaining Life & SLA Breach',
    blurb: 'How much service life is left, at device level and at fitted-part level.',
    feeds: {
      device: (c) => getRows('/ps5/device-rul', { city: c, device_type: 'VALIDATOR' }),
      serial: (c) => getRows('/ps5/serial-rul', { city: c }),
      summary: (c) => getRows('/ps5/component-summary', { city: c }),
    },
    order: ['device', 'serial', 'summary'],
  },
};

const HEADLINE_FEED = { ps2: 'impact', ps3: 'components', ps4: 'facility', ps5: 'serial' };

const GRAIN_OF = {
  devices: 'device', top: 'device', impact: 'device', windows: null,
  queue: 'device', components: 'component', rollup: null,
  weekly: 'device', facility: 'facility', persistent: 'device',
  device: 'device', serial: 'component', summary: null,
};

const TITLE_OF = {
  top: 'Devices that start cascades', impact: 'Business impact by device',
  devices: 'Device catalogue', windows: 'Cascade windows',
  queue: 'Work queue', components: 'Components named as cause', rollup: 'Root cause rollup',
  weekly: 'Weekly anomalies by device', facility: 'Anomalies by depot',
  persistent: 'Devices anomalous week after week',
  device: 'Remaining life by device', serial: 'Remaining life by fitted part',
  summary: 'Component coverage',
};

// HEADLINE VISUAL PER PROBLEM STATEMENT.
//
// A screen of tables is a database browser, not a dashboard. Each analysis
// gets ONE chart that answers its own question, plus one computed sentence
// saying what the chart means. Both are derived from the rows on screen, so
// they move when the data moves and disappear honestly when it is absent.
function Headline({ which, feeds, onDevice }) {
  const rows = (k) => (feeds[k] || {}).rows || [];

  if (which === 'ps2') {
    const d = rows('impact').length ? rows('impact') : rows('top');
    if (!d.length) return null;
    const pts = d.map((r) => ({
      name: r.device_id, id: r.device_id,
      days: num(r.cascade_days), impact: num(r.total_impact), avg: num(r.avg_impact) || 1,
    })).filter((r) => r.days || r.impact);
    const top = pts.slice().sort((a, b) => b.impact - a.impact)[0];
    const share = pts.length ? pts.slice().sort((a, b) => b.impact - a.impact).slice(0, 5)
      .reduce((a, p) => a + p.impact, 0) / Math.max(pts.reduce((a, p) => a + p.impact, 0), 1) : 0;
    return (
      <Panel icon="🌐" n={1} title="Which devices start the most damage" grain="device"
             sub="Days spent in a cascade against total business impact. Bubble size is impact per day.">
        <Bubble data={pts} xKey="days" yKey="impact" zKey="avg"
                xLabel="Days in a cascade" yLabel="Total impact"
                nameKey="name" height={320} colorBy={() => P.orangeInk}
                onDrill={(x) => x && x.id && onDevice && onDevice(x.id)} />
        {top && (
          <Insight tone="orange">
            The five worst devices carry <strong style={{ color: INK }}>{pct(share, 0)}</strong> of all
            cascade impact, led by <strong style={{ color: INK }}>{top.name}</strong>. Cascades concentrate
            on a handful of starters - fixing those is not the same job as fixing the fleet.
          </Insight>
        )}
      </Panel>
    );
  }

  if (which === 'ps3') {
    const d = rows('components');
    if (!d.length) return null;
    const bars = d.map((r) => ({
      name: String(r.component_label || r.component || 'Unknown').replace(/_/g, ' '),
      value: num(r.incident_count),
      devices: num(r.affected_devices),
    })).sort((a, b) => b.value - a.value).slice(0, 10);
    const tot = bars.reduce((a, b) => a + b.value, 0) || 1;
    const lead = bars[0];
    return (
      <Panel icon="🧩" n={1} title="Which parts breakdowns are attributed to" grain="component"
             sub="Incidents attributed to each component type.">
        <RankBars data={bars} xKey="value" yKey="name" height={300} color={P.violetInk}
                  fmt={(v) => nfmt(v)} unit="Incidents" />
        {lead && (
          <Insight tone="violet">
            <strong style={{ color: INK }}>{lead.name}</strong> accounts for{' '}
            <strong style={{ color: INK }}>{pct(lead.value / tot, 0)}</strong> of attributed incidents
            across {nfmt(lead.devices)} devices. One mode dominating this heavily means an estate-wide
            total says more about that mode than about the estate.
          </Insight>
        )}
      </Panel>
    );
  }

  if (which === 'ps4') {
    const d = rows('facility');
    if (!d.length) return null;
    const bars = d.map((r) => ({
      name: `Depot ${r.facility_id}`,
      value: num(r.actionable_devices),
      total: num(r.devices),
    })).sort((a, b) => b.value - a.value).slice(0, 12);
    const worst = bars[0];
    return (
      <Panel icon="📈" n={1} title="Where anomalies concentrate" grain="facility"
             sub="Devices behaving unlike their peer group, by depot.">
        <RankBars data={bars} xKey="value" yKey="name" height={310} color={P.tealInk}
                  fmt={(v) => nfmt(v)} unit="Devices" />
        {worst && (
          <Insight tone="teal">
            <strong style={{ color: INK }}>{worst.name}</strong> has the most devices flagged as
            behaving unlike their peers - <strong style={{ color: INK }}>{nfmt(worst.value)}</strong> of{' '}
            {nfmt(worst.total)} ({pct(worst.total ? worst.value / worst.total : 0, 0)}). An anomaly is a
            deviation from peers, not a failure: it is a prompt to look, not a work order.
          </Insight>
        )}
      </Panel>
    );
  }

  if (which === 'ps5') {
    const d = rows('serial');
    if (!d.length) return null;
    const pts = d.slice(0, 600).map((r) => ({
      name: String(r.component_type_name || '').replace(/_/g, ' '), id: r.device_id,
      age: num(r.component_age_days),
      left: Math.max(0, num(r.expected_component_rul_days)),
      risk: Math.round(num(r.risk_score) * 1000) / 10,
    })).filter((r) => r.age);
    const urgent = pts.filter((p) => p.left <= 30).length;
    const overdue = d.filter((r) => r.is_overdue).length;
    return (
      <Panel icon="⏳" n={1} title="How much life is left in each fitted part" grain="component"
             sub="Component age against expected life remaining. Bubble size is risk score.">
        <Bubble data={pts} xKey="age" yKey="left" zKey="risk"
                xLabel="Component age (days)" yLabel="Expected life left (days)"
                nameKey="name" height={320} colorBy={() => P.violetInk}
                onDrill={(x) => x && x.id && onDevice && onDevice(x.id)} />
        <Insight tone="violet">
          <strong style={{ color: INK }}>{nfmt(urgent)}</strong> parts have 30 days or less of expected
          life, and <strong style={{ color: INK }}>{nfmt(overdue)}</strong> are already past it. Age runs
          left to right; urgency runs bottom to top. An old part high on this chart is fine - a young
          part at the bottom is not.
        </Insight>
      </Panel>
    );
  }
  return null;
}

export function V3Problem({ which, city = 'CHI', onDevice }) {
  const spec = SCREENS[which];
  const [feeds, request] = useLiveFeeds(spec.feeds, city);
  // SUB-TABS, one per grain-distinct view, matching PS1's shape.
  // Overview carries the headline visual and the tiles; each remaining tab
  // carries exactly one panel, so a reader is never asked to scroll past a
  // device table to reach a component table.
  const [sub, setSub] = useState('overview');
  // Only the visible tab's feed is requested, plus whatever the headline
  // needs -- the same two-at-a-time discipline as PS1.
  useEffect(() => {
    const need = sub === 'overview'
      ? [spec.order[0], ...(HEADLINE_FEED[which] ? [HEADLINE_FEED[which]] : [])]
      : [sub];
    request(need.filter(Boolean));
  }, [request, sub, which, spec.order]);

  const first = (feeds[spec.order[0]] || {}).rows || [];
  const devIds = useMemo(() => new Set(
    Object.values(feeds).flatMap((f) => (f.rows || []).map((r) => r.device_id)).filter(Boolean)), [feeds]);
  const compRows = (feeds.serial || feeds.components || { rows: [] }).rows || [];

  return (
    <div style={{ display: 'grid', gap: 16 }}>
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 12 }}>
        <Tile icon="📊" label="ROWS IN THIS ANALYSIS" value={nfmt(first.length)} tone={P.indigoInk} fill={P.indigoFill}
              foot={TITLE_OF[spec.order[0]]} />
        <Tile icon="🚍" label="DEVICES NAMED" value={nfmt(devIds.size)} tone={P.blueInk} fill={P.blueFill}
              foot="Distinct across every panel here" />
        <Tile icon="🧩" label="PARTS NAMED" value={nfmt(compRows.length)} tone={P.violetInk} fill={P.violetFill}
              foot={compRows.length ? 'Component grain' : 'No component feed on this analysis'} />
      </div>

      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
        {['overview', ...spec.order].map((k) => (
          <button key={k} type="button" onClick={() => setSub(k)}
                  style={{ border: `1px solid ${sub === k ? P.indigoInk : LINE}`,
                           background: sub === k ? P.indigoInk : CARD,
                           color: sub === k ? '#FFF' : INK_2,
                           borderRadius: 999, padding: '8px 16px', fontSize: 13.5,
                           fontWeight: 650, cursor: 'pointer' }}>
            {k === 'overview' ? 'Overview' : (TITLE_OF[k] || k)}
            {GRAIN_OF[k] === 'component' && sub !== k && (
              <span style={{ marginLeft: 7, color: P.violetInk, fontSize: 11, fontWeight: 800 }}>PART</span>
            )}
          </button>
        ))}
      </div>

      {sub === 'overview' && <Headline which={which} feeds={feeds} onDevice={onDevice} />}

      {['overview', ...spec.order].filter((k) => k === sub && k !== 'overview').map((key) => (
        <Panel key={key} icon="▦" n={spec.order.indexOf(key) + 2} title={TITLE_OF[key] || key} grain={GRAIN_OF[key]}
               sub={GRAIN_OF[key] === 'component'
                 ? 'One row per fitted part. Never added to a device count.'
                 : GRAIN_OF[key] === 'device' ? 'One row per device.' : 'Aggregated - not a device or part list.'}>
          <Feed feed={feeds[key]} h={260} slow="a few seconds">
            {(rows) => (
              <DataTable
                rows={rows} height={360} pageSize={12} exportName={`v3_${which}_${key}`}
                onRowClick={onDevice && rows[0] && rows[0].device_id ? (r) => onDevice(r.device_id) : undefined}
                columns={autoCols(rows, { first: ['device_id', 'component_type_name', 'device_type', 'category'] })}
              />
            )}
          </Feed>
        </Panel>
      ))}
    </div>
  );
}
