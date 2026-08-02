// =====================================================================
// v2/V2Shell.jsx -- one entry point for the whole v2 estate.
//
// WHY A SHELL. PS1, PS2 and PS4 each shipped as their own route, so seeing
// the estate meant knowing three URLs and holding three sets of numbers in
// your head. This is the screen a Cubic stakeholder opens first.
//
// AS-OF DATES: VERIFIED, NOT ASSUMED.
// I designed this expecting the three to disagree, then read the live API:
//   PS1  /ps1/station-summary  last_inference_date = 2026-04-11
//   PS2  /ps2/status           computed_date       = 2026-04-11
//   PS4  /ps4/weekly-timeline  asof_date           = 2026-04-11
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
import { Badge, Card, Chip, Empty, Grid, Loading, Note, Panel, Section, Stat } from './Kit';
import PS1Overview from './PS1Overview';
import PS2Overview from './PS2Overview';
import PS4Overview from './PS4Overview';
import {
  CARD, INK, INK_2, INK_3, LINE, STATUS,
  compact, deviceColor, deviceShort, dfmt, font, nfmt, pct,
} from './theme';

const num = (v) => (v === null || v === undefined || Number.isNaN(Number(v)) ? 0 : Number(v));
const sumBy = (rows, k, f) => (rows || []).reduce((t, r) => (f && !f(r) ? t : t + num(r[k])), 0);

// ---------------------------------------------------------------------
// Overview feeds. Deliberately the CHEAPEST honest source for each headline,
// not the richest one:
//   PS1  /ps1/station-summary  -- the real fleet roll-up. NOT /ps1/predictions,
//        which is TOP 200 PER DEVICE TYPE by construction and can never be a
//        denominator. That mistake once put "600 of 600 devices need a work
//        order" on screen.
//   PS2  /ps2/status + /ps2/v25/oos-trend + /ps2/v25/label-summary
//   PS4  /ps4/weekly-timeline -- the per-week-per-fleet roll-up, and the only
//        honest denominator. /ps4/weekly is a capped browse list.
// ---------------------------------------------------------------------
const OV_FEEDS = {
  ps1Stations: () => ps1api.stations('CHI'),
  ps2Status:   () => getObj('/ps2/status', { city: 'CHI' }).then((o) => [o]),
  ps2Trend:    () => getRows('/ps2/v25/oos-trend', { city: 'CHI', limit: 5000 }),
  ps2Label:    () => getRows('/ps2/v25/label-summary', { city: 'CHI' }),
  ps4Timeline: () => ps4api.timeline('CHI'),
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
function PSCard({ code, title, what, asOf, tone = 'neutral', loading, error, stats, note, onOpen }) {
  return (
    <Card pad="16px 18px">
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 10, flexWrap: 'wrap' }}>
        <span style={{ fontSize: 11, fontWeight: 800, letterSpacing: '.08em', color: INK_3 }}>{code}</span>
        <span style={{ fontSize: 15, fontWeight: 700, color: INK }}>{title}</span>
        {tone === 'wip' && <Badge tone="warning">In progress</Badge>}
        <span style={{ marginLeft: 'auto' }}>
          {onOpen && (
            <button
              type="button"
              onClick={onOpen}
              style={{
                border: `1px solid ${LINE}`, background: CARD, borderRadius: 9,
                padding: '4px 11px', fontSize: 12, fontWeight: 600, color: INK, cursor: 'pointer',
              }}
            >
              Open
            </button>
          )}
        </span>
      </div>

      <div style={{ fontSize: 12.5, color: INK_2, marginTop: 6, lineHeight: 1.45 }}>{what}</div>

      <div style={{ ...font.micro, marginTop: 8 }}>
        {asOf ? <>Analysis as of <strong style={{ color: INK_2 }}>{asOf}</strong></> : 'As-of date not published by this feed'}
      </div>

      <div style={{ marginTop: 14, minHeight: 76 }}>
        {loading && <Loading height={76} />}
        {!loading && error && (
          <div><Badge tone="warning">Could not load</Badge>
            <div style={{ ...font.micro, marginTop: 6 }}>{error}</div></div>
        )}
        {!loading && !error && stats && (
          <div style={{ display: 'grid', gap: 6 }}>
            {stats.map((s) => (
              <div key={s.k} style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', gap: 12 }}>
                <span style={{ fontSize: 12.5, color: INK_2 }}>{s.k}</span>
                <span style={{ fontSize: 15, fontWeight: 700, color: s.tone === 'warn' ? STATUS.warning : INK }}>{s.v}</span>
              </div>
            ))}
          </div>
        )}
        {!loading && !error && !stats && <Empty height={76}>Not yet published.</Empty>}
      </div>

      {note && <div style={{ ...font.micro, marginTop: 12, lineHeight: 1.5 }}>{note}</div>}
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
          code="PS1" title="Failure prediction"
          what="Which devices are likely to go hardware out-of-service in the next three days."
          asOf={ps1AsOf}
          loading={f.ps1Stations.loading || f.ps1Stations.idle}
          error={f.ps1Stations.error}
          stats={ps1}
          note="Counts are the station roll-up, not the top-200-per-type prediction list."
          onOpen={() => onOpen('ps1')}
        />

        <PSCard
          code="PS2" title="Cascading failure"
          what="How failures arrive together, what precedes them, and what the estate lost."
          asOf={ps2AsOf}
          loading={f.ps2Trend.loading || f.ps2Trend.idle}
          error={f.ps2Trend.error || f.ps2Status.error}
          stats={ps2}
          note="Recorded OOS time above half of available capacity indicates episodes that never close, not fleet availability."
          onOpen={() => onOpen('ps2')}
        />

        <PSCard
          code="PS4" title="Anomaly detection"
          what="Devices behaving unlike their peer group in a given week."
          asOf={ps4AsOf}
          loading={f.ps4Timeline.loading || f.ps4Timeline.idle}
          error={f.ps4Timeline.error}
          stats={ps4}
          note="Scored at device-week grain, so a device can appear in several weeks."
          onOpen={() => onOpen('ps4')}
        />

        <PSCard
          code="PS3" title="Root cause and severity" tone="wip"
          what="Incident severity and component attribution from the Gold incident labels."
          asOf={null}
          note="Notebook under revision. The v2 screen is built once its outputs are published and loaded, the same way PS2 was."
        />

        <PSCard
          code="PS5" title="Remaining useful life" tone="wip"
          what="Component-level survival and remaining-life estimates."
          asOf={null}
          note="Notebook under revision. Serial-grain output exists in Aurora but the grain audit is unresolved, so nothing is surfaced yet."
        />
      </Grid>

      <Panel title="What is wired end to end" hint="Notebook to S3 to Lambda to Aurora to this screen">
        <div style={{ display: 'grid', gap: 8, padding: '4px 2px' }}>
          {[
            ['PS1', 'live', 'Daily inference. Cross-wired daily tables plus the station roll-up.'],
            ['PS2', 'live', '20 tables, refreshed wholesale by the daily loader. No served model.'],
            ['PS4', 'live', 'Weekly anomaly scoring at device-week grain.'],
            ['PS3', 'wip', 'Notebook being revised.'],
            ['PS5', 'wip', 'Notebook being revised.'],
          ].map(([k, s, d]) => (
            <div key={k} style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
              <span style={{ width: 40, fontSize: 12, fontWeight: 800, color: INK_3 }}>{k}</span>
              <Badge tone={s === 'live' ? 'good' : 'warning'}>{s === 'live' ? 'live' : 'in progress'}</Badge>
              <span style={{ fontSize: 12.5, color: INK_2 }}>{d}</span>
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
  { key: 'overview', label: 'Estate overview' },
  { key: 'ps1', label: 'PS1 Failure' },
  { key: 'ps2', label: 'PS2 Cascading' },
  { key: 'ps4', label: 'PS4 Anomaly' },
  { key: 'ps3', label: 'PS3 Root cause', wip: true },
  { key: 'ps5', label: 'PS5 Remaining life', wip: true },
];

function WipPanel({ title, body }) {
  return (
    <Section eyebrow="In progress" title={title}>
      <Note>{body}</Note>
    </Section>
  );
}

export default function V2Shell({ city = 'CHI' }) {
  const [tab, setTab] = useState('overview');

  // Deep links: /v2#ps2 selects that tab, and switching updates the hash so a
  // tab can be sent to someone. The three standalone routes still work.
  useEffect(() => {
    const h = String(window.location.hash || '').replace('#', '');
    if (TABS.some((t) => t.key === h)) setTab(h);
  }, []);

  const open = useCallback((k) => {
    setTab(k);
    try { window.history.replaceState(null, '', `#${k}`); } catch (e) { /* non-fatal */ }
  }, []);

  return (
    <div style={{ padding: '4px 2px 40px' }}>
      <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginBottom: 18 }}>
        {TABS.map((t) => (
          <Chip key={t.key} active={tab === t.key} onClick={() => open(t.key)}>
            {t.label}{t.wip ? ' *' : ''}
          </Chip>
        ))}
      </div>

      {tab === 'overview' && <EstateOverview onOpen={open} />}
      {tab === 'ps1' && <PS1Overview city={city} />}
      {tab === 'ps2' && <PS2Overview city={city} />}
      {tab === 'ps4' && <PS4Overview city={city} />}
      {tab === 'ps3' && (
        <WipPanel
          title="PS3 - root cause and severity"
          body="The PS3 notebook is being revised. Its outputs are not yet published to the production prefix, so nothing is loaded into Aurora and there is nothing honest to show here. The screen gets built the same way PS2's did: run, read the real manifests, write the schema against them, then the routes, then the tab."
        />
      )}
      {tab === 'ps5' && (
        <WipPanel
          title="PS5 - remaining useful life"
          body="The PS5 notebook is being revised. Serial-grain output exists in Aurora but its grain is unresolved - duplicate rows per component are identical on every measured column, which is a roster fan-out rather than real repetition. Surfacing a remaining-life number on top of that would be misleading, so nothing is shown until the grain is settled."
        />
      )}
    </div>
  );
}
