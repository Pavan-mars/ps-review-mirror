// =====================================================================
// v2/V2Shell.jsx -- one entry point for the whole v2 estate.
//
// WHY A SHELL. Failure Prediction, Failure Pattern & Cascade Identification and Anomaly & Outlier Analysis each shipped as their own route, so seeing
// the estate meant knowing three URLs and holding three sets of numbers in
// your head. This is the screen a Cubic stakeholder opens first.
//
// AS-OF DATES: VERIFIED, NOT ASSUMED.
// I designed this expecting the three to disagree, then read the live API:
//   Failure Prediction  /ps1/station-summary  last_inference_date = 2026-04-11
//   Failure Pattern & Cascade Identification  /ps2/status           computed_date       = 2026-04-11
//   Anomaly & Outlier Analysis  /ps4/weekly-timeline  asof_date           = 2026-04-11
// All three run off the SAME client extract, so they ARE comparable today.
// The date is still printed on every card, because that is what makes a future
// divergence visible the day it happens rather than the day someone notices a
// number looks wrong. The cards still never sum into a combined total: they
// count different things (devices, episodes, device-weeks), not parts of one.
//
// The three existing routes (/v2/ps1, /v2/ps2, /v2/ps4) still work and are
// untouched. This is additive.
// =====================================================================
import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { getObj, getRows, ps1 as ps1api, ps4 as ps4api } from './v2api';
import { Badge, Card, Chip, Empty, Grid, Loading, Note, Panel, Rule, Section, Stat, Tabs, V2Style } from './Kit';
import PS1Overview from './PS1Overview';
import PS2Overview from './PS2Overview';
import PS4Overview from './PS4Overview';
import PS3Overview from './PS3Overview';
import PS5Overview from './PS5Overview';
import Device360 from './Device360';
import {
  A, CARD, INK, INK_2, INK_3, LINE, STATUS, TAB_COLOR,
  compact, deviceColor, deviceShort, dfmt, font, motion, nfmt, pct, tint,
} from './theme';

const num = (v) => (v === null || v === undefined || Number.isNaN(Number(v)) ? 0 : Number(v));
const sumBy = (rows, k, f) => (rows || []).reduce((t, r) => (f && !f(r) ? t : t + num(r[k])), 0);

// ---------------------------------------------------------------------
// Overview feeds. Deliberately the CHEAPEST honest source for each headline,
// not the richest one:
//   Failure Prediction  /ps1/station-summary  -- the real fleet roll-up. NOT /ps1/predictions,
//        which is TOP 200 PER DEVICE TYPE by construction and can never be a
//        denominator. That mistake once put "600 of 600 devices need a work
//        order" on screen.
//   Failure Pattern & Cascade Identification  /ps2/status + /ps2/v25/oos-trend + /ps2/v25/label-summary
//   Anomaly & Outlier Analysis  /ps4/weekly-timeline -- the per-week-per-fleet roll-up, and the only
//        honest denominator. /ps4/weekly is a capped browse list.
// ---------------------------------------------------------------------
const OV_FEEDS = {
  ps1Stations: () => ps1api.stations('CHI'),
  ps2Status:   () => getObj('/ps2/status', { city: 'CHI' }).then((o) => [o]),
  ps2Trend:    () => getRows('/ps2/v25/oos-trend', { city: 'CHI', limit: 5000 }),
  ps2Label:    () => getRows('/ps2/v25/label-summary', { city: 'CHI' }),
  ps4Timeline: () => ps4api.timeline('CHI'),
  //   Root Cause Analysis  /ps3/status + /ps3/v25/commanded-split. status carries the run id,
  //        the computed date and the row totals in one call; commanded-split is
  //        three rows and is the only per-fleet episode count that is a true
  //        denominator rather than a capped browse list.
  ps3Status:   () => getObj('/ps3/status', { city: 'CHI' }).then((o) => [o]),
  ps3Split:    () => getRows('/ps3/v25/commanded-split', { city: 'CHI' }),
  //   Remaining Useful Life & SLA Breach  /ps5/summary is the SQL roll-up over the whole device table, so it
  //        is safe as a denominator. /ps5/device-rul is NOT -- it is a capped,
  //        worst-first browse list, and counting it would overstate the fleet.
  //        /ps5/status carries the registry sign-off state, which is what lets
  //        the card say the model is not cleared for scheduling yet.
  ps5Summary:  () => getRows('/ps5/summary', { city: 'CHI' }),
  ps5Registry: () => getRows('/ps5/status', { city: 'CHI' }),
};
const OV_KEYS = Object.keys(OV_FEEDS);

// Same hook shape as PS2Overview, and for the same reason: `feeds` must NOT be
// in the dependency array or setFeeds retriggers the effect, the cleanup kills
// the in-flight workers, and the page loads forever.
function useOverviewFeeds(enabled) {
  const [feeds, setFeeds] = useState(
    () => Object.fromEntries(OV_KEYS.map((k) => [k, { rows: [], loading: false, error: null, idle: true }]))
  );
  const started = useRef(false);
  const alive = useRef(true);

  useEffect(() => {
    alive.current = true;
    return () => { alive.current = false; };
  }, []);

  useEffect(() => {
    if (!enabled || started.current) return;
    started.current = true;
    setFeeds((s) => Object.fromEntries(OV_KEYS.map((k) => [k, { ...s[k], loading: true, idle: false }])));
    let cursor = 0;
    const worker = async () => {
      for (;;) {
        const i = cursor;
        cursor += 1;
        if (i >= OV_KEYS.length) return;
        const key = OV_KEYS[i];
        let rows = [];
        let error = null;
        try { rows = (await OV_FEEDS[key]()) || []; }
        catch (e) { error = String((e && e.message) || e); }
        if (!alive.current) return;
        setFeeds((s) => ({ ...s, [key]: { rows, loading: false, error, idle: false } }));
      }
    };
    Promise.all([worker(), worker()]);
  }, [enabled]);

  return feeds;
}

// ---------------------------------------------------------------------
// One card per problem statement. Its own as-of date, its own denominator,
// and a stated scope so two cards are never silently compared.
// ---------------------------------------------------------------------
// The card carries the SAME colour as its tab, so the overview grid is a map
// of the tab bar above it -- click the violet card, land on the violet tab.
// That is the whole reason colour is assigned per tab rather than per card.
//
// One thing the colour is NOT allowed to do here: the `warn` tone on a stat
// row still resolves to the STATUS amber, not to the card's accent. A number
// that is flagged has to look flagged on every card, or the flag stops
// meaning anything.
function PSCard({ code, title, what, asOf, tone = 'neutral', loading, error, stats, note, onOpen, accent = TAB_COLOR.overview }) {
  return (
    <Card pad="16px 18px" accent={accent} hit={!!onOpen} onClick={onOpen}>
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 10, flexWrap: 'wrap' }}>
        <span
          style={{
            fontSize: 12, fontWeight: 800, letterSpacing: '.08em', color: '#FFFFFF',
            background: accent, borderRadius: 6, padding: '3px 7px', lineHeight: 1.2,
          }}
        >
          {code}
        </span>
        <span style={{ fontSize: 17, fontWeight: 700, color: INK }}>{title}</span>
        {tone === 'wip' && <Badge tone="warning">In progress</Badge>}
        <span style={{ marginLeft: 'auto' }}>
          {onOpen && (
            <button
              type="button"
              onClick={(e) => { e.stopPropagation(); onOpen(); }}
              className="v2-chip"
              data-on="0"
              style={{
                border: `1.5px solid ${tint(accent, A.rule)}`, background: CARD, borderRadius: 9,
                padding: '4px 12px', fontSize: 13.5, fontWeight: 700, color: accent,
                cursor: 'pointer', fontFamily: 'inherit',
              }}
            >
              Open
            </button>
          )}
        </span>
      </div>

      <div style={{ fontSize: 14, color: INK_2, marginTop: 6, lineHeight: 1.45 }}>{what}</div>

      <div style={{ ...font.micro, marginTop: 8, color: accent }}>
        {asOf ? <>Analysis as of <strong>{asOf}</strong></> : 'As-of date not published by this feed'}
      </div>

      <div style={{ marginTop: 14, minHeight: 76 }}>
        {loading && <Loading height={76} label="Loading" />}
        {!loading && error && (
          <div><Badge tone="warning">Could not load</Badge>
            <div style={{ ...font.micro, marginTop: 6 }}>{error}</div></div>
        )}
        {!loading && !error && stats && (
          <div style={{ display: 'grid', gap: 6 }}>
            {stats.map((s) => (
              <div
                key={s.k}
                style={{
                  display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', gap: 12,
                  borderBottom: `1px solid ${tint(accent, A.wash)}`, paddingBottom: 5,
                }}
              >
                <span style={{ fontSize: 14, color: INK_2 }}>{s.k}</span>
                <span style={{ ...font.num, fontSize: 17, fontWeight: 700, color: s.tone === 'warn' ? STATUS.warning.fill : INK }}>{s.v}</span>
              </div>
            ))}
          </div>
        )}
        {!loading && !error && !stats && <Empty height={76}>Not yet published.</Empty>}
      </div>

      {note && <div style={{ ...font.micro, marginTop: 12, lineHeight: 1.5, textTransform: 'none', letterSpacing: 0 }}>{note}</div>}
    </Card>
  );
}

function EstateOverview({ onOpen }) {
  const f = useOverviewFeeds(true);

  const ps1 = useMemo(() => {
    const rows = f.ps1Stations.rows || [];
    if (!rows.length) return null;
    // Column names read off the live route, not inferred:
    // total_devices, predicted_failures, critical_count, high_count.
    return [
      { k: 'Devices in the fleet', v: nfmt(sumBy(rows, 'total_devices')) },
      { k: 'Predicted to fail', v: nfmt(sumBy(rows, 'predicted_failures')) },
      { k: 'Critical risk band', v: nfmt(sumBy(rows, 'critical_count')) },
      { k: 'Depots and stations', v: nfmt(rows.length) },
    ];
  }, [f.ps1Stations.rows]);

  const ps2 = useMemo(() => {
    const st = (f.ps2Status.rows || [])[0];
    const trend = f.ps2Trend.rows || [];
    const all = (f.ps2Label.rows || []).find((r) => String(r.device_category).toUpperCase() === 'ALL');
    if (!st && !trend.length) return null;
    const days = new Set(trend.map((r) => r.event_date)).size;
    const devices = num(all && all.eligible_devices);
    const hours = sumBy(trend, 'hardware_oos_minutes') / 60;
    const capacity = devices * days * 24;
    return [
      { k: 'Devices in scope', v: nfmt(devices) },
      { k: 'Hardware-OOS episode onsets', v: compact(sumBy(trend, 'hardware_oos_onsets')) },
      { k: 'Validated failure onsets', v: nfmt(sumBy(trend, 'validated_failure_onsets')) },
      {
        k: 'Recorded OOS time',
        v: capacity ? `${pct(hours / capacity, 1)} of available` : nfmt(hours),
        tone: capacity && hours / capacity > 0.5 ? 'warn' : undefined,
      },
    ];
  }, [f.ps2Status.rows, f.ps2Trend.rows, f.ps2Label.rows]);

  const ps4 = useMemo(() => {
    const rows = f.ps4Timeline.rows || [];
    if (!rows.length) return null;
    // Scoped to the LATEST week. Summing every week would double-count any
    // device scored in more than one, which is most of them.
    let week = '';
    rows.forEach((r) => { const w = String(r.week_start || ''); if (w > week) week = w; });
    const latest = rows.filter((r) => String(r.week_start || '') === week);
    const observed = sumBy(latest, 'devices_observed');
    const flagged = sumBy(latest, 'actionable_devices');
    return [
      { k: 'Devices scored, latest week', v: nfmt(observed) },
      { k: 'Flagged as anomalous', v: nfmt(flagged) },
      { k: 'Flag rate', v: observed ? pct(flagged / observed, 1) : '--' },
      { k: 'Weeks in the window', v: nfmt(new Set(rows.map((r) => r.week_start)).size) },
    ];
  }, [f.ps4Timeline.rows]);

  const ps3 = useMemo(() => {
    const st = (f.ps3Status.rows || [])[0];
    const split = f.ps3Split.rows || [];
    if (!st && !split.length) return null;
    const epi = sumBy(split, 'oos_episodes');
    const gate = split.find((r) => String(r.mars_device_category).toUpperCase() === 'GATE');
    return [
      { k: 'OOS episodes', v: compact(epi) },
      { k: 'Fare gates share', v: gate && epi ? pct(num(gate.oos_episodes) / epi, 0) : '--' },
      { k: 'Confirmed root cause', v: '0%', tone: 'warn' },
      { k: 'Tables loaded', v: st ? `${st.tables}/${st.expected_tables}` : '--' },
    ];
  }, [f.ps3Status.rows, f.ps3Split.rows]);

  const ps5 = useMemo(() => {
    const rows = f.ps5Summary.rows || [];
    const reg = f.ps5Registry.rows || [];
    if (!rows.length) return null;
    const devices = sumBy(rows, 'n_devices');
    const actNow = sumBy(rows, 'n_act_now');
    const overdue = sumBy(rows, 'n_overdue');
    const ready = reg.filter((r) => r.dashboard_ready).length;
    return [
      { k: 'Devices with a life estimate', v: nfmt(devices) },
      { k: 'Act now', v: nfmt(actNow), tone: actNow ? 'warn' : undefined },
      { k: 'Past expected life', v: devices ? pct(overdue / devices, 0) : '--' },
      { k: 'Fleets cleared to schedule on', v: `${nfmt(ready)} of ${nfmt(reg.length || 3)}`, tone: ready ? undefined : 'warn' },
    ];
  }, [f.ps5Summary.rows, f.ps5Registry.rows]);

  const ps5AsOf = useMemo(() => {
    const rows = f.ps5Summary.rows || [];
    let d = '';
    rows.forEach((r) => { const v = String(r.feature_asof_date || ''); if (v > d) d = v; });
    return d ? dfmt(d) : null;
  }, [f.ps5Summary.rows]);

  const ps3AsOf = useMemo(() => {
    const st = (f.ps3Status.rows || [])[0];
    return st && st.computed_date ? dfmt(st.computed_date) : null;
  }, [f.ps3Status.rows]);

  const ps1AsOf = useMemo(() => {
    const rows = f.ps1Stations.rows || [];
    let d = '';
    rows.forEach((r) => { const v = String(r.last_inference_date || ''); if (v > d) d = v; });
    return d ? dfmt(d) : null;
  }, [f.ps1Stations.rows]);

  const ps2AsOf = useMemo(() => {
    const st = (f.ps2Status.rows || [])[0];
    return st && st.computed_date ? dfmt(st.computed_date) : null;
  }, [f.ps2Status.rows]);

  const ps4AsOf = useMemo(() => {
    const rows = f.ps4Timeline.rows || [];
    let week = '';
    rows.forEach((r) => { const w = String(r.week_start || ''); if (w > week) week = w; });
    const asof = rows.length ? rows[0].asof_date : null;
    return week ? `${asof ? dfmt(asof) : ''}${asof ? ' - ' : ''}week of ${dfmt(week)}` : null;
  }, [f.ps4Timeline.rows]);

  return (
    <>
      <Section
        accent={TAB_COLOR.overview}
        eyebrow="Chicago / CTA-Ventra"
        title="The estate, across problem statements"
        sub="Each card answers a different question about the same fleet. They are shown side by side, never summed."
      >
        <Note>
          All three analyses currently run off the same client extract, which ends 11 Apr 2026, so
          the dates below agree and the cards are comparable. The date is printed on each one
          anyway, so the day they stop agreeing is visible immediately. What the cards count still
          differs -- devices, episodes and device-weeks are not parts of one total, so they are
          never summed.
        </Note>
      </Section>

      <Grid cols="repeat(auto-fit,minmax(330px,1fr))">
        <PSCard
          code="Failure Prediction" title="Failure prediction" accent={TAB_COLOR.ps1}
          what="Which devices are likely to go hardware out-of-service in the next three days."
          asOf={ps1AsOf}
          loading={f.ps1Stations.loading || f.ps1Stations.idle}
          error={f.ps1Stations.error}
          stats={ps1}
          note="Counts are the station roll-up, not the top-200-per-type prediction list."
          onOpen={() => onOpen('ps1')}
        />

        <PSCard
          code="Failure Pattern & Cascade Identification" title="Cascading failure" accent={TAB_COLOR.ps2}
          what="How failures arrive together, what precedes them, and what the estate lost."
          asOf={ps2AsOf}
          loading={f.ps2Trend.loading || f.ps2Trend.idle}
          error={f.ps2Trend.error || f.ps2Status.error}
          stats={ps2}
          note="Recorded OOS time above half of available capacity indicates episodes that never close, not fleet availability."
          onOpen={() => onOpen('ps2')}
        />

        <PSCard
          code="Root Cause Analysis" title="Root cause and severity" accent={TAB_COLOR.ps3}
          what="Which component an OOS episode is attributed to, and how soon that device comes back."
          asOf={ps3AsOf}
          loading={f.ps3Status.loading || f.ps3Status.idle}
          error={f.ps3Status.error}
          stats={ps3}
          note="Severity and confirmed root cause are not available in this run -- all seven evidence sources report not_configured. The screen says so rather than showing empty panels."
          onOpen={() => onOpen('ps3')}
        />

        <PSCard
          code="Anomaly & Outlier Analysis" title="Anomaly detection" accent={TAB_COLOR.ps4}
          what="Devices behaving unlike their peer group in a given week."
          asOf={ps4AsOf}
          loading={f.ps4Timeline.loading || f.ps4Timeline.idle}
          error={f.ps4Timeline.error}
          stats={ps4}
          note="Scored at device-week grain, so a device can appear in several weeks."
          onOpen={() => onOpen('ps4')}
        />

        <PSCard
          code="Remaining Useful Life & SLA Breach" title="Remaining useful life" accent={TAB_COLOR.ps5}
          what="How much service life is left in each device, and which components are closest to the end of it."
          asOf={ps5AsOf}
          loading={f.ps5Summary.loading || f.ps5Summary.idle}
          error={f.ps5Summary.error}
          stats={ps5}
          note="Estimates how LONG a device keeps running, not whether it fails today. Day counts come from three separately fitted models and are not comparable across fleets. No fleet is signed off in the model registry, so this is a prioritisation aid rather than an automatic scheduler."
          onOpen={() => onOpen('ps5')}
        />
      </Grid>

      <Rule accent={TAB_COLOR.overview} />

      <Panel
        title="What is wired end to end"
        hint="Notebook to S3 to Lambda to Aurora to this screen"
        accent={TAB_COLOR.overview}
      >
        <div style={{ display: 'grid', gap: 2, padding: '4px 2px' }}>
          {[
            ['Failure Prediction', 'ps1', 'live', 'Daily inference. Cross-wired daily tables plus the station roll-up.'],
            ['Failure Pattern & Cascade Identification', 'ps2', 'live', '20 tables, refreshed wholesale by the daily loader. No served model.'],
            ['Root Cause Analysis', 'ps3', 'live', '20 tables from the V26 source-first run, refreshed wholesale by the v25 loader.'],
            ['Anomaly & Outlier Analysis', 'ps4', 'live', 'Weekly anomaly scoring at device-week grain.'],
            ['Remaining Useful Life & SLA Breach', 'ps5', 'live', 'Survival models per fleet. Device and component remaining life served from Aurora. Registry sign-off outstanding.'],
          ].map(([k, key, s, d]) => (
            <div
              key={k}
              style={{
                display: 'flex', alignItems: 'center', gap: 12, padding: '7px 10px',
                borderLeft: `3px solid ${TAB_COLOR[key]}`, background: tint(TAB_COLOR[key], '08'),
                borderRadius: '0 8px 8px 0',
              }}
            >
              <span style={{ width: 40, fontSize: 13.5, fontWeight: 800, color: TAB_COLOR[key] }}>{k}</span>
              <Badge tone={s === 'live' ? 'good' : 'warning'}>{s === 'live' ? 'live' : 'in progress'}</Badge>
              <span style={{ fontSize: 14, color: INK_2 }}>{d}</span>
            </div>
          ))}
        </div>
      </Panel>
    </>
  );
}

// ---------------------------------------------------------------------
// Shell
// ---------------------------------------------------------------------
const TABS = [
  { key: 'overview', label: 'Overview' },
  { key: 'ps1', label: 'Failure Prediction' },
  { key: 'ps2', label: 'Failure Patterns & Cascades' },
  { key: 'ps3', label: 'Root Cause Analysis' },
  { key: 'ps4', label: 'Anomaly & Outliers' },
  { key: 'ps5', label: 'Remaining Life & SLA' },
  { key: 'device', label: 'Device 360' },
];

export default function V2Shell({ city = 'CHI' }) {
  const [tab, setTab] = useState('overview');
  // A device carried in on the hash, e.g. #device:BMV02633. Set by the Analyse
  // modal's "Open in Device 360" button from any screen, so the drill-down is
  // reachable everywhere without threading a callback through five components.
  const [deviceFocus, setDeviceFocus] = useState('');

  // Deep links: /v2#ps2 selects that tab, and switching updates the hash so a
  // tab can be sent to someone. The three standalone routes still work.
  useEffect(() => {
    const apply = () => {
      const h = String(window.location.hash || '').replace('#', '').trim();
      if (h.startsWith('device:')) {
        const id = h.slice(7).trim().toUpperCase();
        if (id) { setDeviceFocus(id); setTab('device'); }
        return;
      }
      if (h && TABS.some((t) => t.key === h)) setTab(h);
    };
    apply();
    window.addEventListener('hashchange', apply);
    return () => window.removeEventListener('hashchange', apply);
  }, []);

  const open = useCallback((k) => {
    setTab(k);
    try { window.history.replaceState(null, '', `#${k}`); } catch (e) { /* non-fatal */ }
  }, []);

  return (
    <div style={{ padding: '4px 2px 40px', background: '#FFFFFF' }}>
      {/* Mounted once for the whole estate. Every :hover, :focus-visible and
          keyframe in v2 comes from here -- see Kit.jsx for why it is a style
          tag in a codebase of inline styles. */}
      <V2Style />

      {/* The tab bar sits on a hairline that takes the ACTIVE tab's colour, so
          the header re-tints as you move across it and the page you are on is
          readable from the chrome alone, not only from the filled pill. */}
      <div
        style={{
          borderBottom: `2px solid ${tint(TAB_COLOR[tab] || TAB_COLOR.overview, A.soft)}`,
          marginBottom: 20, paddingBottom: 2, transition: `border-color ${motion.base}`,
        }}
      >
        <Tabs items={TABS} value={tab} onChange={open} variant="top" />
      </div>

      {tab === 'overview' && <EstateOverview onOpen={open} />}
      {tab === 'ps1' && <PS1Overview city={city} />}
      {tab === 'ps2' && <PS2Overview city={city} />}
      {tab === 'ps3' && <PS3Overview city={city} />}
      {tab === 'ps4' && <PS4Overview city={city} />}
      {tab === 'device' && <Device360 city={city} initialDevice={deviceFocus} />}
      {tab === 'ps5' && <PS5Overview city={city} />}
    </div>
  );
}

// FONTS_SCALED 04-Aug-2026
