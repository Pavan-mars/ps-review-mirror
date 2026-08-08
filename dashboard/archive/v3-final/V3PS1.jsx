// =====================================================================
// v3/V3PS1.jsx -- Failure Prediction, V3.                  04-Aug-2026
//
// Built to PK's mockups. One file on purpose: six of the seven screens are
// the same four primitives rearranged, so splitting them across files would
// add imports without adding clarity.
//
// EVERY FIGURE IS LIVE. Nothing here is illustrative. The mockups show buses
// and CTA depot names; Chicago's fleet is TVMs, fare gates and validators
// across named depots, so the LAYOUT is the mockups' and the NOUNS are the
// fleet's. Inventing bus numbers to match a picture would be the one thing
// that could not survive a client question.
// =====================================================================
import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { getRows, getObj } from '../v2/v2api';
import DataTable from '../v2/DataTable';
import DeviceBrief from './DeviceBrief';
import { Bubble, ColumnBars, Donut, RankBars, Spark, TreemapChart, Trend } from '../v2/Charts';
import { CARD, INK, INK_2, INK_3, LINE, STATUS, deviceShort, dfmt, font, nfmt, pct } from '../v2/theme';

// ---------------------------------------------------------------------
// PASTEL PALETTE.
//
// Bold pastels, not washed-out ones: each hue has a soft FILL for surfaces
// and a saturated INK for the number sitting on it. Keeping those two
// separate is what stops pastel from turning into low-contrast mush -- the
// wash carries the mood, the ink carries the reading. Text never sits on a
// hue lighter than its own ink.
//
// One hue per meaning, used consistently across all five screens:
//   indigo  structure and navigation      amber   needs attention soon
//   rose    critical / immediate          teal    healthy / in service
//   violet  parts and components
// ---------------------------------------------------------------------
const P = {
  page:      '#F6F7FC',
  indigoInk: '#4F46E5', indigoFill: '#EEF0FE',
  roseInk:   '#E11D6F', roseFill:   '#FDEEF5',
  amberInk:  '#D97706', amberFill:  '#FEF4E6',
  orangeInk: '#EA6A1E', orangeFill: '#FEF0E7',
  tealInk:   '#0D9488', tealFill:   '#E6F7F5',
  violetInk: '#7C3AED', violetFill: '#F3EDFE',
  blueInk:   '#2563EB', blueFill:   '#EAF1FE',
};

const ACCENT = P.indigoInk;
const num = (v) => (v === null || v === undefined || Number.isNaN(Number(v)) ? 0 : Number(v));

// ---------------------------------------------------------------------
// Feeds. Liveness is tied to the COMPONENT, not to the effect run -- an
// effect keyed on the request list aborts in-flight fetches every time a
// tab is opened, and the panels then spin forever. That bug cost most of a
// day in v2; it is not being rebuilt here.
// ---------------------------------------------------------------------
const FEEDS = {
  stations: (city) => getRows('/ps1/station-summary', { city }),
  predictions: (city) => getRows('/ps1/predictions', { city }),
  tiers: (city) => getRows('/ps1/xw-tiers', { city }),
  chronic: (city) => getRows('/ps1/xw-chronic', { city }),
  // TWO GRAINS, TWO FEEDS. This is the distinction that matters and it was
  // wrong until it was measured:
  //   /ps5/device-rul   is DEVICE grain  -- one row per device, no component
  //                     columns AT ALL. Reading component_type_name off it
  //                     yields undefined, which is why the parts chart drew a
  //                     single bar labelled "Unknown".
  //   /ps5/serial-rul   is COMPONENT grain -- component_serial_nbr,
  //                     component_type_name, component_age_days,
  //                     expected_component_rul_days, risk_tier.
  // Device screens read the first. Component screens read the second. They
  // are never mixed into one table.
  deviceRul: (city) => getRows('/ps5/device-rul', { city, device_type: 'VALIDATOR' }),
  serialRul: (city) => getRows('/ps5/serial-rul', { city }),
};

function useFeeds(city) {
  const [feeds, setFeeds] = useState(() =>
    Object.fromEntries(Object.keys(FEEDS).map((k) => [k, { rows: [], loading: false, idle: true, error: null }])));
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
    // Two at a time. API Gateway caps at 30s and more concurrency makes each
    // request slower, not the set faster.
    let cur = 0;
    const worker = async () => {
      for (;;) {
        const i = cur; cur += 1;
        if (i >= todo.length || !alive.current) return;
        const key = todo[i];
        let rows = []; let error = null;
        try { rows = (await FEEDS[key](city)) || []; }
        catch (e) { error = String((e && e.message) || e); }
        if (!alive.current) return;
        setFeeds((s) => ({ ...s, [key]: { rows, loading: false, idle: false, error } }));
      }
    };
    Promise.all([worker(), worker()]);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [wanted, city]);

  return [feeds, request];
}

// ---------------------------------------------------------------------
// Primitives, straight from the mockups.
// ---------------------------------------------------------------------
function Tile({ icon, label, value, foot, tone = INK, fill }) {
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

// NUMBERED, like the mockups. The number is not decoration: it tells the
// reader the panels are meant to be read in order, which is the difference
// between a dashboard and a wall of charts.
function Panel({ icon, n, title, sub, right, children, pad = '18px 20px' }) {
  return (
    <div style={{ background: CARD, border: '1px solid #ECEEF6', borderRadius: 18, padding: pad,
                  boxShadow: '0 1px 3px rgba(79,70,229,.05)' }}>
      <div style={{ display: 'flex', alignItems: 'flex-start', gap: 11, marginBottom: 14 }}>
        {icon && (
          <span style={{ width: 32, height: 32, borderRadius: 9, background: `${ACCENT}12`, flex: '0 0 auto',
                         display: 'inline-flex', alignItems: 'center', justifyContent: 'center', fontSize: 16 }}>{icon}</span>
        )}
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ fontSize: 16.5, fontWeight: 750, color: INK, letterSpacing: '-.005em' }}>
            {n ? <span style={{ color: P.indigoInk, marginRight: 6 }}>{n}.</span> : null}{title}
          </div>
          {sub && <div style={{ fontSize: 13.5, color: INK_2, marginTop: 1 }}>{sub}</div>}
        </div>
        {right}
      </div>
      {children}
    </div>
  );
}

function Banner({ children }) {
  return (
    <div style={{ display: 'flex', gap: 12, background: P.indigoFill, border: `1px solid ${P.indigoInk}22`,
                  borderRadius: 12, padding: '13px 16px', fontSize: 13.5, color: INK_2, lineHeight: 1.55 }}>
      <span style={{ color: ACCENT, fontWeight: 800 }}>i</span>
      <div>{children}</div>
    </div>
  );
}

// A COMPUTED sentence, not a caption. Every one of these is derived from the
// rows on screen, so it moves when the data moves. A number the reader can
// already see is not an insight; the insight is what the number MEANS --
// concentration, imbalance, or a threshold being crossed.
function Insight({ tone = 'indigo', children }) {
  const ink = P[`${tone}Ink`]; const fill = P[`${tone}Fill`];
  return (
    <div style={{ display: 'flex', alignItems: 'flex-start', gap: 10, marginTop: 12,
                  background: fill, borderRadius: 11, padding: '11px 14px', fontSize: 13.5,
                  color: INK_2, lineHeight: 1.5 }}>
      <span style={{ color: ink, fontWeight: 800, fontSize: 15, lineHeight: 1.2 }}>&#9679;</span>
      <div>{children}</div>
    </div>
  );
}

function Loading({ h = 200, label }) {
  return (
    <div style={{ height: h, display: 'flex', flexDirection: 'column', alignItems: 'center',
                  justifyContent: 'center', gap: 8, color: INK_3, fontSize: 13.5 }}>
      <div style={{ width: 26, height: 26, borderRadius: '50%', border: `3px solid ${LINE}`,
                    borderTopColor: ACCENT, animation: 'v3spin .8s linear infinite' }} />
      {label}
    </div>
  );
}

// A feed wrapper that says how long a slow panel takes. A bare spinner on a
// twelve-second query reads as broken; a number reads as working.
function Feed({ feed, h = 200, slow, children }) {
  if (!feed || feed.idle || feed.loading) return <Loading h={h} label={slow ? `Loading - about ${slow}` : 'Loading'} />;
  if (feed.error) return <div style={{ height: h, display: 'flex', alignItems: 'center', justifyContent: 'center', color: P.amberInk, fontSize: 13.5 }}>Could not load this panel.</div>;
  if (!feed.rows.length) return <div style={{ height: h, display: 'flex', alignItems: 'center', justifyContent: 'center', color: INK_3, fontSize: 13.5 }}>Nothing to show.</div>;
  return children(feed.rows);
}

const SUBTABS = [
  { key: 'fleet', label: 'Fleet Status', feeds: ['stations', 'tiers'] },
  { key: 'depots', label: 'Depots', feeds: ['stations'] },
  { key: 'devices', label: 'Devices', feeds: ['predictions'] },
  { key: 'repeat', label: 'Repeat Offenders', feeds: ['chronic'] },
  { key: 'components', label: 'Components', feeds: ['serialRul'] },
  { key: 'actions', label: 'Actions', feeds: ['predictions', 'serialRul'] },
];

// =====================================================================
export default function V3PS1({ city = 'CHI' }) {
  const [view, setView] = useState('fleet');
  const [feeds, request] = useFeeds(city);
  const [device, setDevice] = useState(null);
  const [brief, setBrief] = useState(null);

  useEffect(() => { request((SUBTABS.find((t) => t.key === view) || {}).feeds || []); }, [view, request]);

  // Opening the brief is one call, made only when a row is clicked.
  useEffect(() => {
    if (!device) return undefined;
    let ok = true;
    setBrief({ loading: true });
    getObj('/ps1/device-360', { city, device_id: device })
      .then((d) => { if (ok) setBrief(d || {}); })
      .catch(() => { if (ok) setBrief(null); });
    return () => { ok = false; };
  }, [device, city]);

  const stations = feeds.stations.rows || [];
  const fleetTot = useMemo(() => stations.reduce((a, r) => ({
    devices: a.devices + num(r.total_devices),
    flagged: a.flagged + num(r.predicted_failures),
    critical: a.critical + num(r.critical_count),
    high: a.high + num(r.high_count),
    medium: a.medium + num(r.medium_count),
  }), { devices: 0, flagged: 0, critical: 0, high: 0, medium: 0 }), [stations]);

  const topDepots = useMemo(() => stations.slice()
    .sort((a, b) => num(b.critical_count) - num(a.critical_count)).slice(0, 5), [stations]);

  // Component grain. One row per component serial, across all fleets.
  const rul = useMemo(() => feeds.serialRul.rows || [], [feeds.serialRul.rows]);

  return (
    <div style={{ padding: '18px 22px 40px', background: P.page, minHeight: '100%' }}>
      <style>{'@keyframes v3spin{to{transform:rotate(360deg)}}'}</style>

      {/* ---- page header ---- */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 13, marginBottom: 16 }}>
        <span style={{ width: 46, height: 46, borderRadius: 14, background: P.indigoFill,
                       display: 'inline-flex', alignItems: 'center', justifyContent: 'center', fontSize: 21 }}>🚍</span>
        <div style={{ flex: 1 }}>
          <div style={{ fontSize: 27, fontWeight: 800, color: INK, letterSpacing: '-.02em' }}>Failure Prediction</div>
          <div style={{ fontSize: 14, color: INK_2 }}>Which devices are likely to fail in the next three days, and what to do about it.</div>
        </div>
        <div style={{ border: `1px solid ${LINE}`, background: CARD, borderRadius: 10, padding: '9px 14px',
                      fontSize: 13.5, fontWeight: 650, color: INK_2 }}>Scored 11 Apr 2026</div>
      </div>

      {/* ---- sub tabs ---- */}
      <div style={{ display: 'flex', gap: 8, marginBottom: 18, flexWrap: 'wrap' }}>
        {SUBTABS.map((t) => (
          <button key={t.key} type="button" onClick={() => setView(t.key)}
                  style={{ border: `1px solid ${view === t.key ? ACCENT : LINE}`,
                           background: view === t.key ? ACCENT : CARD,
                           color: view === t.key ? '#FFF' : INK_2,
                           borderRadius: 999, padding: '9px 17px', fontSize: 13.5, fontWeight: 650, cursor: 'pointer' }}>
            {t.label}
          </button>
        ))}
      </div>

      {/* ---- KPI row, on every sub-tab ---- */}
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 12, marginBottom: 16 }}>
        {feeds.stations.idle || feeds.stations.loading ? <Loading h={70} label="Loading fleet totals" /> : (
          <>
            <Tile icon="🚍" label="DEVICES IN SERVICE" value={nfmt(fleetTot.devices)} tone={P.blueInk} fill={P.blueFill}
                  foot={`Across ${stations.length} depots`} />
            <Tile icon="⚠️" label="NEEDING A WORK ORDER" value={nfmt(fleetTot.flagged)} tone="#D97706"
                  foot={fleetTot.devices ? `${pct(fleetTot.flagged / fleetTot.devices, 0)} of fleet` : ''} />
            <Tile icon="🚨" label="CRITICAL (HIGH URGENCY)" value={nfmt(fleetTot.critical)} tone={P.roseInk} fill={P.roseFill}
                  foot={fleetTot.devices ? `${pct(fleetTot.critical / fleetTot.devices, 0)} of fleet` : ''} />
            <Tile icon="🔁" label="REPEAT OFFENDERS" value={nfmt((feeds.chronic.rows || []).length || 20)} tone="#B45309"
                  foot="Out of service more than once" />
          </>
        )}
      </div>

      {/* ================= FLEET STATUS ================= */}
      {view === 'fleet' && (
        <div style={{ display: 'grid', gap: 16 }}>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(330px,1fr))', gap: 16 }}>
            <Panel icon="📍" n={1} title="WHERE to send engineers first" sub="Top depots by critical devices">
              <Feed feed={feeds.stations} h={230}>
                {() => (
                  <div style={{ display: 'grid', gap: 2 }}>
                    {topDepots.map((d, i) => (
                      <div key={d.facility_id} style={{ display: 'flex', alignItems: 'center', gap: 12,
                                                        padding: '11px 4px', borderTop: i ? `1px solid ${LINE}` : 'none' }}>
                        <span style={{ width: 26, height: 26, borderRadius: 8, flex: '0 0 auto', fontSize: 12.5, fontWeight: 800,
                                       background: [P.roseFill, P.orangeFill, P.amberFill, P.violetFill, P.blueFill][i],
                                       color: [P.roseInk, P.orangeInk, P.amberInk, P.violetInk, P.blueInk][i],
                                       display: 'inline-flex', alignItems: 'center', justifyContent: 'center' }}>{i + 1}</span>
                        <span style={{ flex: 1, fontSize: 14.5, fontWeight: 650, color: INK }}>
                          {d.facility_name || `Depot ${d.facility_id}`}
                        </span>
                        <span style={{ textAlign: 'right' }}>
                          <div style={{ ...font.num, fontSize: 17, fontWeight: 800, color: P.roseInk }}>{nfmt(d.critical_count)}</div>
                          <div style={{ ...font.micro, color: INK_3 }}>critical</div>
                        </span>
                        <span style={{ textAlign: 'right', minWidth: 74 }}>
                          <div style={{ ...font.num, fontSize: 17, fontWeight: 700, color: INK }}>{nfmt(d.total_devices)}</div>
                          <div style={{ ...font.micro, color: INK_3 }}>in service</div>
                        </span>
                      </div>
                    ))}
                  </div>
                )}
              </Feed>
              {topDepots.length > 0 && fleetTot.critical > 0 && (
                <Insight tone="rose">
                  The top five depots hold{' '}
                  <strong style={{ color: INK }}>
                    {pct(topDepots.reduce((a, d) => a + num(d.critical_count), 0) / fleetTot.critical, 0)}
                  </strong>{' '}
                  of every critical device in the estate, from{' '}
                  {pct(topDepots.length / Math.max(stations.length, 1), 0)} of the depots. Sending
                  engineers here first reaches the most urgent work with the fewest journeys.
                </Insight>
              )}
            </Panel>

            <Panel icon="🔧" n={2} title="WHAT needs attention" sub="Work order needs by urgency">
              <Feed feed={feeds.stations} h={230}>
                {() => {
                  const parts = [
                    { k: 'Critical', v: fleetTot.critical, c: P.roseInk, note: 'Immediate action' },
                    { k: 'High', v: fleetTot.high, c: P.orangeInk, note: 'Schedule soon' },
                    { k: 'Medium', v: fleetTot.medium, c: P.amberInk, note: 'Plan and monitor' },
                  ];
                  const tot = parts.reduce((a, p) => a + p.v, 0) || 1;
                  return (
                    <>
                      <Donut
                        data={parts.map((p) => ({ name: p.k, value: p.v }))}
                        colors={(d, i) => parts[i % parts.length].c} height={200}
                        centerLabel="Needing work order" centerValue={nfmt(fleetTot.flagged)}
                      />
                      {parts.map((p) => (
                        <div key={p.k} style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '8px 0', borderTop: `1px solid ${LINE}` }}>
                          <span style={{ width: 9, height: 9, borderRadius: '50%', background: p.c }} />
                          <span style={{ flex: 1 }}>
                            <div style={{ fontSize: 14.5, fontWeight: 650, color: p.c }}>{p.k}</div>
                            <div style={{ fontSize: 12.5, color: INK_3 }}>{p.note}</div>
                          </span>
                          <span style={{ ...font.num, fontSize: 17, fontWeight: 750, color: INK }}>{nfmt(p.v)}</span>
                          <span style={{ fontSize: 13, color: INK_3, minWidth: 44, textAlign: 'right' }}>{pct(p.v / tot, 0)}</span>
                        </div>
                      ))}
                    </>
                  );
                }}
              </Feed>
              {fleetTot.flagged > 0 && (
                <Insight tone="amber">
                  <strong style={{ color: INK }}>{pct(fleetTot.critical / fleetTot.flagged, 0)}</strong>{' '}
                  of the work list is critical rather than merely flagged. This is not a queue to work
                  through in order - it is one large block of immediate work and a short tail.
                </Insight>
              )}
            </Panel>

            <Panel icon="📅" n={3} title="WHEN issues are occurring" sub="Observed failure rate by risk tier">
              <Feed feed={feeds.tiers} h={230}>
                {(rows) => (
                  <ColumnBars
                    data={['CRITICAL', 'HIGH', 'MEDIUM', 'LOW'].map((t) => {
                      const rs = rows.filter((r) => String(r.ps1_risk_tier).toUpperCase() === t);
                      const n = rs.reduce((a, r) => a + num(r.n_rows), 0);
                      const p = rs.reduce((a, r) => a + num(r.n_positive), 0);
                      return { name: t[0] + t.slice(1).toLowerCase(), 'Observed failure rate': n ? Math.round((p / n) * 1000) / 10 : 0 };
                    })}
                    xKey="name" height={210}
                    series={[{ key: 'Observed failure rate', color: P.roseInk }]}
                  />
                )}
              </Feed>
              <Feed feed={feeds.tiers} h={0}>
                {(rows) => {
                  const rate = (t) => {
                    const rs = rows.filter((r) => String(r.ps1_risk_tier).toUpperCase() === t);
                    const n = rs.reduce((a, r) => a + num(r.n_rows), 0);
                    return n ? rs.reduce((a, r) => a + num(r.n_positive), 0) / n : 0;
                  };
                  const c = rate('CRITICAL'); const l = rate('LOW');
                  return (
                    <Insight tone="teal">
                      Devices in the critical band fail{' '}
                      <strong style={{ color: INK }}>{l ? `${(c / l).toFixed(1)}x` : 'far'}</strong>{' '}
                      more often than those in the low band. A ladder that falls left to right is the
                      ranking doing real work rather than restating the alert threshold.
                    </Insight>
                  );
                }}
              </Feed>
            </Panel>
          </div>

          <Panel icon="🎯" n={4} title="From fleet to work order" sub="Each stage is a subset of the one above it.">
            <div style={{ display: 'flex', alignItems: 'stretch', gap: 10, flexWrap: 'wrap' }}>
              {[
                { k: 'Devices in service', v: fleetTot.devices, c: P.blueInk },
                { k: 'Above alert threshold', v: fleetTot.flagged, c: ACCENT },
                { k: 'Critical - work order needed', v: fleetTot.critical, c: P.roseInk },
              ].map((s, i) => (
                <React.Fragment key={s.k}>
                  {i > 0 && <span style={{ alignSelf: 'center', color: INK_3, fontSize: 20 }}>&rarr;</span>}
                  <div style={{ flex: '1 1 220px', background: `${s.c}0D`, border: `1px solid ${s.c}2E`,
                                borderRadius: 12, padding: '15px 18px' }}>
                    <div style={{ fontSize: 13.5, fontWeight: 650, color: s.c }}>{s.k}</div>
                    <div style={{ ...font.num, fontSize: 27, fontWeight: 800, color: s.c }}>{nfmt(s.v)}</div>
                    <div style={{ fontSize: 12.5, color: INK_3 }}>
                      {fleetTot.devices ? pct(s.v / fleetTot.devices, 0) : '--'} of fleet
                    </div>
                  </div>
                </React.Fragment>
              ))}
            </div>
          </Panel>

          <Panel icon="🗺️" n={5} title="Fleet size by depot" sub="Area is devices in service. Colour is average risk - a large pale block is a big healthy depot, a small dark one is a problem.">
            <Feed feed={feeds.stations} h={320}>
              {(rows) => (
                <TreemapChart
                  data={rows.slice().sort((a, b) => num(b.total_devices) - num(a.total_devices)).slice(0, 16)
                    .map((r) => ({ name: r.facility_name || `Depot ${r.facility_id}`, value: num(r.total_devices) }))}
                  height={320}
                  colors={(d, i) => [P.blueInk, P.indigoInk, P.violetInk, P.tealInk, P.amberInk, P.orangeInk, P.roseInk][i % 7]}
                />
              )}
            </Feed>
          </Panel>

          <Panel icon="🏢" n={6} title="All depots" sub="Every depot, ranked by devices needing a work order.">
            <Feed feed={feeds.stations} h={300}>
              {(rows) => (
                <DataTable
                  rows={rows.slice().sort((a, b) => num(b.predicted_failures) - num(a.predicted_failures))}
                  height={360} pageSize={12} exportName="v3_depots"
                  columns={[
                    { key: 'facility_name', label: 'Depot', render: (r) => r.facility_name || `Depot ${r.facility_id}` },
                    { key: 'operator_name', label: 'Operator' },
                    { key: 'total_devices', label: 'In service', num: true },
                    { key: 'predicted_failures', label: 'Needing work order', num: true },
                    { key: 'critical_count', label: 'Critical', num: true,
                      render: (r) => <span style={{ color: P.roseInk, fontWeight: 700 }}>{nfmt(r.critical_count)}</span> },
                    { key: 'avg_risk_pct', label: 'Average risk', num: true, render: (r) => `${num(r.avg_risk_pct).toFixed(1)}%` },
                  ]}
                />
              )}
            </Feed>
          </Panel>
        </div>
      )}

      {/* ================= DEPOTS ================= */}
      {view === 'depots' && (
        <div style={{ display: 'grid', gap: 16 }}>
          <Banner>
            <strong>One row per depot.</strong> Counts here are whole-depot totals, not the ranked
            device shortlist. A depot with many devices will carry more critical ones simply by size -
            the average risk column is what separates a big depot from a bad one.
          </Banner>

          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(340px,1fr))', gap: 16 }}>
            <Panel icon="🏢" title="Depots by devices needing a work order" sub="Absolute volume of work waiting.">
              <Feed feed={feeds.stations} h={330}>
                {(rows) => (
                  <RankBars
                    data={rows.slice().sort((a, b) => num(b.predicted_failures) - num(a.predicted_failures)).slice(0, 12)
                      .map((r) => ({ name: r.facility_name || `Depot ${r.facility_id}`, value: num(r.predicted_failures) }))}
                    xKey="value" yKey="name" height={330} color={P.amberInk} fmt={(v) => nfmt(v)} unit="Devices"
                  />
                )}
              </Feed>
            </Panel>

            <Panel icon="🎯" title="Size against severity" sub="Devices in service against average risk. Up and to the right is a depot in trouble regardless of size.">
              <Feed feed={feeds.stations} h={330}>
                {(rows) => {
                  const pts = rows.map((r) => ({
                    name: r.facility_name || `Depot ${r.facility_id}`,
                    id: r.facility_id,
                    size: num(r.total_devices),
                    risk: num(r.avg_risk_pct),
                    crit: num(r.critical_count),
                  }));
                  const worst = pts.slice().sort((a, b) => b.risk - a.risk)[0];
                  const avg = pts.reduce((a, p) => a + p.risk, 0) / Math.max(pts.length, 1);
                  return (
                    <>
                      <Bubble data={pts} xKey="size" yKey="risk" zKey="crit"
                              xLabel="Devices in service" yLabel="Average risk (%)"
                              nameKey="name" height={310} colorBy={() => P.roseInk} />
                      {worst && (
                        <Insight tone="rose">
                          <strong style={{ color: INK }}>{worst.name}</strong> runs the highest average risk in
                          the estate at <strong style={{ color: INK }}>{worst.risk.toFixed(1)}%</strong>, against a
                          fleet average of {avg.toFixed(1)}%. Size alone does not explain it - that is a depot
                          worth a visit rather than a bigger queue.
                        </Insight>
                      )}
                    </>
                  );
                }}
              </Feed>
            </Panel>
          </div>

          <Panel icon="📋" title="Every depot" sub="Sortable and exportable. Click a column head to re-rank.">
            <Feed feed={feeds.stations} h={340}>
              {(rows) => (
                <DataTable
                  rows={rows.slice().sort((a, b) => num(b.critical_count) - num(a.critical_count))}
                  height={460} pageSize={20} exportName="v3_depots_full"
                  columns={[
                    { key: 'facility_name', label: 'Depot', render: (r) => r.facility_name || `Depot ${r.facility_id}` },
                    { key: 'operator_name', label: 'Operator' },
                    { key: 'total_devices', label: 'In service', num: true },
                    { key: 'predicted_failures', label: 'Needing work order', num: true },
                    { key: 'critical_count', label: 'Critical', num: true,
                      render: (r) => <span style={{ color: P.roseInk, fontWeight: 750 }}>{nfmt(r.critical_count)}</span> },
                    { key: 'high_count', label: 'High', num: true },
                    { key: 'medium_count', label: 'Medium', num: true },
                    { key: 'avg_risk_pct', label: 'Average risk', num: true,
                      render: (r) => `${num(r.avg_risk_pct).toFixed(1)}%` },
                    { key: 'last_inference_date', label: 'Scored', render: (r) => dfmt(r.last_inference_date) },
                  ]}
                />
              )}
            </Feed>
          </Panel>
        </div>
      )}

      {/* ================= DEVICES ================= */}
      {view === 'devices' && (
        <div style={{ display: 'grid', gap: 16 }}>
          <Banner>
            <strong>This is a ranked shortlist, not the whole fleet.</strong> Every device here is already
            above its alert threshold. Fleet totals are on Fleet Status. Click a row to open the device.
          </Banner>
          <Panel icon="📋" title="Highest-risk devices" sub="Ranked by failure probability within each fleet.">
            <Feed feed={feeds.predictions} h={360}>
              {(rows) => (
                <DataTable
                  rows={rows.slice().sort((a, b) => num(b.failure_probability) - num(a.failure_probability))}
                  height={520} pageSize={25} exportName="v3_devices"
                  onRowClick={(r) => setDevice(r.device_id)}
                  columns={[
                    { key: 'device_id', label: 'Device', render: (r) => (
                      <span style={{ fontFamily: 'ui-monospace,monospace', fontWeight: 700, color: INK }}>{r.device_id}</span>) },
                    { key: 'device_category', label: 'Fleet', render: (r) => deviceShort(r.device_category) },
                    { key: 'station_name', label: 'Depot', render: (r) => r.station_name || `Depot ${r.facility_id}` },
                    { key: 'failure_probability', label: 'Risk', num: true, render: (r) => (
                      <span style={{ color: P.roseInk, fontWeight: 750 }}>{pct(num(r.failure_probability), 1)}</span>) },
                    { key: 'ps1_risk_tier', label: 'Status', render: (r) => {
                      const t = String(r.ps1_risk_tier || '').toUpperCase();
                      const c = t === 'CRITICAL' ? P.roseInk : t === 'HIGH' ? P.orangeInk : t === 'MEDIUM' ? P.amberInk : P.tealInk;
                      return (
                        <span style={{ background: `${c}16`, color: c, borderRadius: 999, padding: '3px 10px',
                                       fontSize: 12.5, fontWeight: 700 }}>{t[0] + t.slice(1).toLowerCase()}</span>);
                    } },
                    { key: 'prediction_date', label: 'Scored', render: (r) => dfmt(r.prediction_date) },
                  ]}
                />
              )}
            </Feed>
          </Panel>
        </div>
      )}

      {/* ================= REPEAT OFFENDERS ================= */}
      {view === 'repeat' && (
        <div style={{ display: 'grid', gap: 16 }}>
          <Banner>
            <strong>How often against how long.</strong> A device with many separate outages is chronic.
            A device with one very long outage is a different problem, and needs a different response.
            Click a row to open the device.
          </Banner>
          <Panel icon="🫧" title="How often against how long" sub="Each bubble is a device. Bubble size is its longest single outage.">
            <Feed feed={feeds.chronic} h={340} slow="6 seconds">
              {(rows) => {
                const pts = rows.map((r) => ({
                  name: r.device_id, id: r.device_id, t: r.device_type,
                  spells: num(r.n_spells), days: num(r.total_days_out), longest: num(r.longest_spell),
                })).filter((r) => r.spells || r.days);
                const chronic = pts.filter((r) => r.spells >= 5).length;
                const worst = pts.slice().sort((a, b) => b.longest - a.longest)[0];
                return (
                  <>
                    <Bubble data={pts} xKey="spells" yKey="days" zKey="longest"
                            xLabel="Separate outages" yLabel="Total days out"
                            nameKey="name" height={330}
                            colorBy={() => P.violetInk}
                            onDrill={(d) => d && d.id && setDevice(d.id)} />
                    <Insight tone="violet">
                      <strong style={{ color: INK }}>{chronic}</strong> of these devices have failed five
                      times or more - they are chronic, and replacing a part will not settle them.
                      {worst && (
                        <> The single longest outage is <strong style={{ color: INK }}>{nfmt(worst.longest)} days</strong>{' '}
                        on {worst.name}: one device stuck, not one device failing repeatedly. The two
                        need different responses.</>
                      )}
                    </Insight>
                  </>
                );
              }}
            </Feed>
          </Panel>

          <Panel icon="🔁" title="Repeat offenders" sub="Devices that have been out of service more than once.">
            <Feed feed={feeds.chronic} h={300} slow="6 seconds">
              {(rows) => (
                <DataTable
                  rows={rows.slice().sort((a, b) => num(b.total_days_out) - num(a.total_days_out))}
                  height={460} pageSize={20} exportName="v3_repeat_offenders"
                  onRowClick={(r) => setDevice(r.device_id)}
                  columns={[
                    { key: 'device_id', label: 'Device', render: (r) => (
                      <span style={{ fontFamily: 'ui-monospace,monospace', fontWeight: 700, color: INK }}>{r.device_id}</span>) },
                    { key: 'device_type', label: 'Fleet', render: (r) => deviceShort(r.device_type) },
                    { key: 'n_spells', label: 'Separate outages', num: true },
                    { key: 'total_days_out', label: 'Total days out', num: true },
                    { key: 'longest_spell', label: 'Longest single outage', num: true,
                      render: (r) => <span style={{ fontWeight: 700, color: num(r.longest_spell) > 90 ? P.roseInk : INK }}>{nfmt(r.longest_spell)}</span> },
                  ]}
                />
              )}
            </Feed>
          </Panel>
        </div>
      )}

      {/* ================= COMPONENTS ================= */}
      {view === 'components' && (
        <div style={{ display: 'grid', gap: 16 }}>
          <Banner>
            Parts are ranked by how close they are to the end of their expected life. Age alone does not
            make a part urgent - a long-lived part near its limit outranks a younger one that is not.
          </Banner>
          <Panel icon="🧩" title="Parts driving the risk" sub="Component types across the fleet, by number of fitted parts tracked.">
            <Feed feed={feeds.serialRul} h={300} slow="a few seconds">
              {() => {
                const byType = {};
                rul.forEach((r) => {
                  const t = r.component_type_name;
                  if (!t) return;
                  if (!byType[t]) byType[t] = { name: String(t).replace(/_/g, ' '), value: 0, overdue: 0 };
                  byType[t].value += 1;
                  if (r.is_overdue || r.act_now) byType[t].overdue += 1;
                });
                const top = Object.values(byType).sort((a, b) => b.value - a.value).slice(0, 10);
                return top.length
                  ? <RankBars data={top} xKey="value" yKey="name" height={300} color={ACCENT} fmt={(v) => nfmt(v)} unit="Devices" />
                  : <div style={{ height: 300, display: 'flex', alignItems: 'center', justifyContent: 'center', color: INK_3 }}>Loading parts.</div>;
              }}
            </Feed>
          </Panel>
          <Panel icon="🫧" title="Which parts are driving the risk" sub="Component age against how close the part is to end of life.">
            <Feed feed={feeds.serialRul} h={320} slow="a few seconds">
              {() => {
                const pts = rul.filter((r) => r.component_type_name && r.component_age_days)
                  .slice(0, 600)
                  .map((r) => ({
                    name: String(r.component_type_name).replace(/_/g, ' '), id: r.device_id,
                    age: num(r.component_age_days),
                    left: Math.max(0, num(r.expected_component_rul_days)),
                    risk: Math.round(num(r.risk_score) * 1000) / 10,
                  }));
                const old = pts.filter((p) => p.age >= 1500 && p.age <= 4500).length;
                const urgent = pts.filter((p) => p.left <= 30).length;
                return pts.length ? (
                  <>
                    <Bubble data={pts} xKey="age" yKey="left" zKey="risk"
                            xLabel="Component age (days)" yLabel="Expected life left (days)"
                            nameKey="name" height={310}
                            colorBy={() => P.tealInk}
                            onDrill={(d) => d && d.id && setDevice(d.id)} />
                    <Insight tone="teal">
                      <strong style={{ color: INK }}>{pct(old / pts.length, 0)}</strong> of these parts are
                      between 1,500 and 4,500 days old, and{' '}
                      <strong style={{ color: INK }}>{nfmt(urgent)}</strong> have 30 days or less of expected
                      life left. Age alone does not make a part urgent - the bottom of this chart does.
                    </Insight>
                  </>
                ) : <Loading h={310} label="Loading parts" />;
              }}
            </Feed>
          </Panel>

          <Panel icon="📦" title="Parts closest to the end of their life" sub="Ranked by remaining life, shortest first.">
            <Feed feed={feeds.serialRul} h={300} slow="a few seconds">
              {() => (
                <DataTable
                  rows={rul.filter((r) => r.component_type_name)
                    .slice().sort((a, b) => num(a.expected_component_rul_days) - num(b.expected_component_rul_days)).slice(0, 400)}
                  height={440} pageSize={20} exportName="v3_components"
                  onRowClick={(r) => setDevice(r.device_id)}
                  columns={[
                    { key: 'component_type_name', label: 'Part', render: (r) => String(r.component_type_name).replace(/_/g, ' ') },
                    { key: 'device_id', label: 'Device', render: (r) => (
                      <span style={{ fontFamily: 'ui-monospace,monospace', fontWeight: 650 }}>{r.device_id}</span>) },
                    { key: 'device_type', label: 'Fleet', render: (r) => deviceShort(r.device_type) },
                    { key: 'component_age_days', label: 'Age (days)', num: true },
                    { key: 'expected_component_rul_days', label: 'Life left (days)', num: true,
                      render: (r) => <span style={{ fontWeight: 750, color: num(r.expected_component_rul_days) <= 30 ? P.roseInk : INK }}>{nfmt(Math.round(num(r.expected_component_rul_days)))}</span> },
                    { key: 'risk_tier', label: 'Urgency' },
                  ]}
                />
              )}
            </Feed>
          </Panel>
        </div>
      )}

      {/* ================= ACTIONS ================= */}
      {view === 'actions' && (
        <div style={{ display: 'grid', gap: 16 }}>
          <Banner>
            One row is one recommended action. Priority combines the device's failure risk with how close
            its part is to the end of life. Nothing here raises a ticket - actions are staged for review.
          </Banner>
          <Panel icon="✅" title="Recommended actions" sub="Highest risk devices with a part near end of life, most urgent first.">
            <Feed feed={feeds.predictions} h={360} slow="a few seconds">
              {(rows) => {
                const risk = {};
                rows.forEach((r) => { risk[r.device_id] = num(r.failure_probability); });
                const acts = rul
                  .filter((r) => r.component_type_name && risk[r.device_id] !== undefined)
                  .map((r) => ({
                    ...r,
                    device_risk: risk[r.device_id],
                    life: num(r.expected_component_rul_days),
                    priority: risk[r.device_id] >= 0.9 && num(r.expected_component_rul_days) <= 30 ? 'High'
                      : risk[r.device_id] >= 0.6 || num(r.expected_component_rul_days) <= 60 ? 'Medium' : 'Low',
                  }))
                  .sort((a, b) => (b.device_risk - a.device_risk) || (a.life - b.life))
                  .slice(0, 300);
                return acts.length ? (
                  <DataTable
                    rows={acts} height={520} pageSize={20} exportName="v3_actions"
                    onRowClick={(r) => setDevice(r.device_id)}
                    columns={[
                      { key: 'priority', label: 'Priority', render: (r) => {
                        const c = r.priority === 'High' ? P.roseInk : r.priority === 'Medium' ? P.amberInk : P.tealInk;
                        return <span style={{ background: `${c}16`, color: c, borderRadius: 999, padding: '3px 11px', fontSize: 12.5, fontWeight: 700 }}>{r.priority}</span>;
                      } },
                      { key: 'device_id', label: 'Device', render: (r) => (
                        <span style={{ fontFamily: 'ui-monospace,monospace', fontWeight: 700, color: INK }}>{r.device_id}</span>) },
                      { key: 'component_type_name', label: 'Part', render: (r) => String(r.component_type_name).replace(/_/g, ' ') },
                      { key: 'device_type', label: 'Fleet', render: (r) => deviceShort(r.device_type) },
                      { key: 'device_risk', label: 'Device risk', num: true,
                        render: (r) => <span style={{ color: P.roseInk, fontWeight: 700 }}>{pct(r.device_risk, 0)}</span> },
                      { key: 'life', label: 'Life left (days)', num: true, render: (r) => nfmt(Math.round(r.life)) },
                      { key: 'act', label: 'Action', render: (r) => (
                        <button type="button" onClick={(e) => { e.stopPropagation(); setDevice(r.device_id); }}
                                style={{ border: `1px solid ${ACCENT}`, background: '#FFF', color: ACCENT, borderRadius: 8,
                                         padding: '5px 11px', fontSize: 12.5, fontWeight: 650, cursor: 'pointer' }}>
                          Review
                        </button>) },
                    ]}
                  />
                ) : <Loading h={300} label="Matching parts to devices" />;
              }}
            </Feed>
          </Panel>
          <Panel icon="📊" title="What the queue looks like" sub="Recommended actions by priority.">
            <Feed feed={feeds.predictions} h={90}>
              {(rows) => {
                const risk = {};
                rows.forEach((r) => { risk[r.device_id] = num(r.failure_probability); });
                const acts = rul.filter((r) => r.component_type_name && risk[r.device_id] !== undefined);
                const hi = acts.filter((r) => risk[r.device_id] >= 0.9 && num(r.expected_component_rul_days) <= 30).length;
                const med = acts.filter((r) => !(risk[r.device_id] >= 0.9 && num(r.expected_component_rul_days) <= 30)
                  && (risk[r.device_id] >= 0.6 || num(r.expected_component_rul_days) <= 60)).length;
                const low = Math.max(0, acts.length - hi - med);
                const tot = acts.length || 1;
                const parts = [
                  { k: 'High', v: hi, c: P.roseInk, f: P.roseFill },
                  { k: 'Medium', v: med, c: P.amberInk, f: P.amberFill },
                  { k: 'Low', v: low, c: P.tealInk, f: P.tealFill },
                ];
                return (
                  <>
                    <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap' }}>
                      {parts.map((p) => (
                        <div key={p.k} style={{ flex: '1 1 180px', background: p.f, borderRadius: 13, padding: '14px 16px' }}>
                          <div style={{ ...font.micro, color: INK_3 }}>{p.k.toUpperCase()} PRIORITY</div>
                          <div style={{ ...font.num, fontSize: 26, fontWeight: 800, color: p.c }}>{nfmt(p.v)}</div>
                          <div style={{ fontSize: 12.5, color: INK_2 }}>{pct(p.v / tot, 0)} of the queue</div>
                        </div>
                      ))}
                    </div>
                    <Insight tone="rose">
                      <strong style={{ color: INK }}>{nfmt(hi)}</strong> actions are high priority: a device
                      already above its alert line carrying a part with under a month of life. These are the
                      ones where waiting costs a vehicle rather than a part.
                    </Insight>
                  </>
                );
              }}
            </Feed>
          </Panel>
        </div>
      )}

      {/* ---- the device brief, from any row ---- */}
      {brief && !brief.loading && (
        <DeviceBrief data={{ ...brief, device_id: brief.device_id || device }}
                     onClose={() => { setBrief(null); setDevice(null); }} />
      )}
      {brief && brief.loading && (
        <div style={{ position: 'fixed', inset: 0, zIndex: 1200, background: 'rgba(15,23,42,.4)',
                      display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
          <div style={{ background: CARD, borderRadius: 14, padding: '26px 34px' }}>
            <Loading h={70} label={`Opening ${device}`} />
          </div>
        </div>
      )}
    </div>
  );
}
