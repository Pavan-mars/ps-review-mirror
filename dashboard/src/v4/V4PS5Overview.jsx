// =====================================================================
// v2/PS5Overview.jsx -- Remaining Useful Life & SLA Breach remaining useful life, macro to micro.
//
//   Fleet status -> Devices -> Components -> Model quality -> How we know
//
// -------------------------------------------------------------------
// THE ONE SENTENCE THAT MUST SURVIVE THE MEETING
// -------------------------------------------------------------------
// Remaining Useful Life & SLA Breach estimates HOW LONG a device or component is likely to keep running.
// It is not a probability that anything fails today, and a day count from
// one fleet is not comparable with a day count from another -- the three
// survival models are fitted separately, on separate populations, with
// separate baseline hazards. Every panel that could be read across fleets
// says so.
//
// -------------------------------------------------------------------
// WHICH ENDPOINT MEANS WHAT
// -------------------------------------------------------------------
//   /ps5/summary            THE DEVICE DENOMINATOR. Aggregated in SQL over
//                           the whole of v_ps5_device_rul: n_devices,
//                           act_now, overdue, the four risk bands, median
//                           RUL. Safe for totals.
//   /ps5/component-summary  THE COMPONENT DENOMINATOR. Counts DISTINCT
//                           (device_id, component_serial_nbr), and carries
//                           n_rows beside it so the validator fan-out
//                           stays measurable rather than hidden.
//   /ps5/device-rul         A CAPPED, ORDERED BROWSE LIST (act_now first,
//                           then shortest RUL). Never a denominator.
//   /ps5/serial-rul         Same shape at component grain.
//   /ps5/leaderboard        Out-of-time concordance per candidate model,
//                           with its fold-to-fold sd. The pair is read
//                           together or not at all.
//   /ps5/status             The MLflow REGISTRY state. Its concordance is
//                           a different measurement from the leaderboard's
//                           and the two are shown side by side, never
//                           merged into one number.
//   /ps5/importance         Permutation importance. cindex_drop is the FALL
//                           in concordance when the feature is shuffled, so
//                           larger means more important.
//   /ps5/coverage           Which enrichment sources actually joined.
//   /ps5/serial-grain       The grain audit. Kept on screen because it is
//                           the reason component counts carry a caveat.
//
// -------------------------------------------------------------------
// TRUNCATION: WHICH DERIVED NUMBERS SURVIVE IT AND WHICH DO NOT
// -------------------------------------------------------------------
// Both browse routes are ORDER BY act_now DESC. So when a fleet is capped,
// the rows that fall off the end are the HEALTHY ones. That means:
//   act_now counts    SURVIVE truncation (every act_now row sorts first).
//   overdue counts    DO NOT -- overdue is a superset of act_now.
//   risk band mix     DOES NOT -- the LOW band is what gets cut.
//   medians, means    DO NOT.
// The screen therefore takes every total from /ps5/summary when it is
// available, and when it is not it falls back to the browse list and says
// on the card which numbers are affected. It does not quietly count a
// truncated list -- that is the mistake that once put "600 of 600 devices
// need a work order" on the Failure Prediction screen.
// =====================================================================
import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { RefreshCw, TriangleAlert } from 'lucide-react';
import {
  CAT, INK, INK_2, INK_3, LINE, STATUS, deviceColor, deviceName, deviceShort,
  dfmt, font, nfmt, pct, pct100, A, TAB_COLOR, tint,
} from './V4theme';
import {
  Badge, Card, Chip, Empty, Grid, Hero, Loading, Note, Panel, Section, Stat, Toolbar, Rule, Tabs, V2Style, StatRow,
} from './V4Kit';
import { ColumnBars, Donut, RankBars } from './V4Charts';
import DataTable from './V4DataTable';
import AnalyseModal from './V4Device360Popup';
import { ps5 as api } from './V4api';
import { analyseColumn } from './V4DeviceTable';
import { Histogram } from './V4ChartsPlus';
import { useLocations, normFacilityId } from './V4Locations';


// STABLE CALLBACK IDENTITIES.                                v5
// These were inline arrows in JSX, so every render produced a NEW
// function and React.memo on the chart components compared unequal
// every time -- the memo was a no-op. Every one of these closes over
// nothing but module scope, so hoisting is enough; no useCallback, no
// dependency array to get wrong. They are only invoked during render,
// so referring to a const declared further down the module is safe.
const _fmt1 = (v) => nfmt(v);
const _colorBy2 = (d) => deviceColor(d.t);
const _colors3 = (d) => bandColor(d.band);
const _colorBy4 = (d) => (d.value >= CINDEX_FLOOR ? STATUS.good.fill : STATUS.warning.fill);
const _fmt5 = (v) => Number(v).toFixed(3);
const _colorBy6 = (d) => (d.enr ? CAT[5] : CAT[0]);
const _fmt7 = (v) => Number(v).toFixed(4);

const TYPES = ['GATE', 'TVM', 'VALIDATOR'];

// The cap the route applies when ?limit is not honoured by the deployed
// Lambda. A per-type response of exactly this length is treated as capped.
const SERVER_CAP = 3000;

// The concordance a survival model has to clear before it is allowed to
// drive a work order. Set in the Remaining Useful Life & SLA Breach engine, not invented here.
const CINDEX_FLOOR = 0.65;

const BAND_TONE = { CRITICAL: 'critical', HIGH: 'serious', MEDIUM: 'warning', LOW: 'good' };
const BAND_ORDER = ['CRITICAL', 'HIGH', 'MEDIUM', 'LOW'];
const bandColor = (b) => (STATUS[BAND_TONE[String(b || '').toUpperCase()]] || STATUS.neutral).fill;

// /ps5/status reports lowercase plurals (gates / tvms / validators) while
// every other route reports the singular upper form. Normalising here means
// the join between the registry and the leaderboard is done in one place.
const REG_TO_TYPE = { gates: 'GATE', tvms: 'TVM', validators: 'VALIDATOR' };
const regType = (s) => REG_TO_TYPE[String(s || '').toLowerCase()] || String(s || '').toUpperCase();

const num = (v) => (v === null || v === undefined || Number.isNaN(Number(v)) ? 0 : Number(v));
const sumBy = (rows, k) => (rows || []).reduce((t, r) => t + num(r[k]), 0);
const byType = (rows, t) => (rows || []).filter((r) => String(r.device_type).toUpperCase() === t);

// facility_id arrives here as the string "44.0" because it came through a
// float column, and as "44" on every other tab. That normalisation now lives
// in V4Locations so the id PS5 holds and the id PS1 names are the same string
// -- otherwise the lookup misses on every row and every location reads as an
// unmapped number.
const facility = (v) => normFacilityId(v) || '--';

// DataTable's column spec documents `render?` but not its arity, and the two
// conventions in circulation -- render(row) and render(value, row) -- fail
// differently and silently: the wrong one renders "[object Object]" in every
// cell of the column. This adapter accepts either, so a change of convention
// in DataTable cannot quietly break this screen.
const cell = (fn) => (a, b) => fn(b && typeof b === 'object' && !Array.isArray(b) ? b : a);

// ORDERING ACROSS FLEETS, AND WHY IT IS NOT BY DAYS.
//
// The browse feeds are fetched one request per fleet and concatenated, so the
// list arrived in GATE -> TVM -> VALIDATOR order. Page one was thirteen fare
// gates while 310 act-now TVMs at a median 5.9 days sat below 452 gates --
// the top of the table read as "the most urgent devices in the estate" and
// was nothing of the kind.
//
// The obvious fix is to sort by remaining life. That is the one thing we
// cannot do: the three survival models are fitted separately on separate
// populations with separate baseline hazards, so a 3-day TVM and a 3-day
// validator are not the same claim and interleaving them by raw days invents
// a comparison the models do not support.
//
// So: act-now first (a boolean, identical in meaning across fleets), then
// RANK WITHIN THE DEVICE'S OWN FLEET. The worst gate, worst TVM and worst
// validator land at the top together, each measured against its own peers.
const worstFirst = (a, b) => {
  const an = a.act_now ? 1 : 0, bn = b.act_now ? 1 : 0;
  if (an !== bn) return bn - an;
  const ar = Number(a.rul_rank_in_type), br = Number(b.rul_rank_in_type);
  const av = Number.isFinite(ar) ? ar : Infinity;
  const bv = Number.isFinite(br) ? br : Infinity;
  if (av !== bv) return av - bv;
  return String(a.device_id).localeCompare(String(b.device_id));
};

// Component grain has no rank column, so the same principle is applied
// through risk_tier -- which is also assigned within a fleet -- and only then
// by days, inside a single tier.
const TIER_ORDER = { CRITICAL: 0, HIGH: 1, MEDIUM: 2, LOW: 3 };
const worstComponentFirst = (a, b) => {
  const an = a.act_now ? 1 : 0, bn = b.act_now ? 1 : 0;
  if (an !== bn) return bn - an;
  const at = TIER_ORDER[String(a.risk_tier).toUpperCase()] ?? 9;
  const bt = TIER_ORDER[String(b.risk_tier).toUpperCase()] ?? 9;
  if (at !== bt) return at - bt;
  const ad = Number(a.expected_component_rul_days);
  const bd = Number(b.expected_component_rul_days);
  return (Number.isFinite(ad) ? ad : Infinity) - (Number.isFinite(bd) ? bd : Infinity);
};

const VIEWS = [
  { key: 'overview', label: 'Fleet status' },
  { key: 'devices', label: 'Devices' },
  { key: 'location', label: 'Location' },
  { key: 'components', label: 'Components' },
  { key: 'model', label: 'Model quality' },
  { key: 'evidence', label: 'How we know' },
];

// ---------------------------------------------------------------------
// Feeds
//
// The two browse feeds fan out to one request PER DEVICE TYPE rather than
// one request for everything. Without that split a single 3,000-row cap is
// shared across three fleets and the largest one eats it: an unsplit call
// returns 2,539 validators, 416 TVMs and 45 of 452 gates, so the gate fleet
// silently loses 90% of its rows. Split, each fleet gets its own cap and
// only validators can reach it.
//
// A failure in one type does not fail the feed. The type is recorded in
// `failed` and the panel says which fleet is missing instead of showing
// three fleets' worth of numbers with one silently absent.
// ---------------------------------------------------------------------
async function fanOutByType(fn, city) {
  const settled = await Promise.all(TYPES.map(async (t) => {
    try { return { t, rows: (await fn(city, t)) || [] }; }
    catch (e) { return { t, err: String((e && e.message) || e) }; }
  }));
  const out = { rows: [], failed: [], capped: [] };
  settled.forEach(({ t, rows, err }) => {
    if (err) { out.failed.push(t); return; }
    if (rows.length >= SERVER_CAP) out.capped.push(t);
    out.rows.push(...rows);
  });
  if (out.failed.length === TYPES.length) throw new Error(`no fleet responded (${out.failed.join(', ')})`);
  return out;
}

const FEED_FN = {
  summary: (city) => api.summary(city),
  compSummary: (city) => api.componentSummary(city),
  devices: (city) => fanOutByType(api.deviceRul, city),
  components: (city) => fanOutByType(api.serialRul, city),
  leaderboard: (city) => api.leaderboard(city),
  registry: (city) => api.status(city),
  importance: (city) => api.importance(city),
  coverage: (city) => api.coverage(city),
  grain: (city) => api.serialGrain(city),
};

const VIEW_FEEDS = {
  overview: ['summary', 'devices', 'leaderboard', 'registry'],
  devices: ['devices', 'summary'],
  // Rolled up from the same device rows the Devices tab browses -- no new
  // endpoint, and the two tabs can never disagree about a total.
  location: ['devices', 'summary'],
  components: ['components', 'compSummary', 'grain'],
  model: ['leaderboard', 'registry', 'importance'],
  // 'grain' and 'registry' dropped 06-Aug-2026 with the two panels that read
  // them -- two fewer round trips on this tab's first paint. Both feeds are
  // still fetched by the Components and Model quality views that use them.
  evidence: ['coverage', 'summary', 'compSummary', 'leaderboard'],
};

const ALL_KEYS = Object.keys(FEED_FN);

function useFeeds(city) {
  const [feeds, setFeeds] = useState(
    () => Object.fromEntries(ALL_KEYS.map((k) => [k, { data: null, loading: false, error: null, idle: true }]))
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
      todo.forEach((k) => { next[k] = { data: null, loading: true, error: null, idle: false }; });
      return next;
    });
    let cursor = 0;
    const worker = async () => {
      for (;;) {
        const i = cursor;
        cursor += 1;
        if (i >= todo.length || !alive) return;
        const key = todo[i];
        let data = null;
        let error = null;
        try { data = await FEED_FN[key](city); }
        catch (e) { error = String((e && e.message) || e); }
        if (!alive) return;
        setFeeds((s) => ({ ...s, [key]: { data, loading: false, error, idle: false } }));
      }
    };
    Promise.all([worker(), worker(), worker()]);
    return () => { alive = false; };
    // feeds is deliberately absent: including it retriggers the effect on
    // every setFeeds, the cleanup kills the in-flight workers, and the tab
    // loads forever. Same rule as PS2Overview and PS4Overview.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [wanted, city, nonce]);

  const refetch = useCallback(() => {
    setFeeds((s) => Object.fromEntries(
      Object.keys(s).map((k) => [k, { data: null, loading: false, error: null, idle: true }])
    ));
    setNonce((n) => n + 1);
  }, []);

  return { feeds, request, refetch };
}

// ---------------------------------------------------------------------
// Roll-up. Prefers /ps5/summary; falls back to the browse list and reports
// exactly which of the fallback numbers truncation can distort.
// ---------------------------------------------------------------------
function useRollup(feeds) {
  return useMemo(() => {
    const sum = feeds.summary.data;
    const browse = feeds.devices.data;
    const rows = (browse && browse.rows) || [];
    const capped = (browse && browse.capped) || [];

    if (Array.isArray(sum) && sum.length) {
      const per = sum.map((r) => ({
        device_type: String(r.device_type).toUpperCase(),
        n_devices: num(r.n_devices),
        n_in_type: num(r.n_devices_in_type) || num(r.n_devices),
        act_now: num(r.n_act_now),
        overdue: num(r.n_overdue),
        CRITICAL: num(r.n_critical),
        HIGH: num(r.n_high),
        MEDIUM: num(r.n_medium),
        LOW: num(r.n_low),
        median_rul: r.median_rul_days === null || r.median_rul_days === undefined ? null : Number(r.median_rul_days),
        threshold: r.act_now_threshold === null || r.act_now_threshold === undefined ? null : Number(r.act_now_threshold),
        asof: r.feature_asof_date,
      }));
      let asof = '';
      per.forEach((p) => { const v = String(p.asof || ''); if (v > asof) asof = v; });
      return { source: 'summary', exact: true, per, capped, asof: asof || null };
    }

    if (!rows.length) return null;

    const per = TYPES.map((t) => {
      const r = byType(rows, t);
      if (!r.length) return null;
      const band = (b) => r.filter((x) => String(x.risk_band).toUpperCase() === b).length;
      return {
        device_type: t,
        n_devices: r.length,
        // n_devices_in_type is the model's OWN count of the fleet it was
        // fitted on and travels on every row, so it is right even when the
        // rows around it are a truncated slice.
        n_in_type: num(r[0].n_devices_in_type) || r.length,
        act_now: r.filter((x) => x.act_now).length,
        overdue: r.filter((x) => x.is_overdue).length,
        CRITICAL: band('CRITICAL'), HIGH: band('HIGH'), MEDIUM: band('MEDIUM'), LOW: band('LOW'),
        median_rul: null,
        threshold: r[0].act_now_threshold === null || r[0].act_now_threshold === undefined ? null : Number(r[0].act_now_threshold),
        asof: r[0].feature_asof_date,
      };
    }).filter(Boolean);

    let asof = '';
    per.forEach((p) => { const v = String(p.asof || ''); if (v > asof) asof = v; });
    return { source: 'browse', exact: capped.length === 0, per, capped, asof: asof || null };
  }, [feeds.summary.data, feeds.devices.data]);
}

// "GATE 0.90, TVM 0.90" -- the per-fleet act-now bar from ps5_act_now_policy (sql/67).
const thresholdText = (roll) => {
  const t = (roll ? roll.per : []).filter((p) => Number.isFinite(p.threshold));
  return t.length ? t.map((p) => `${deviceShort(p.device_type)} ${p.threshold.toFixed(2)}`).join(', ') : 'the fleet threshold';
};

// Estimates are a snapshot at feature_asof_date. Past STALE_DAYS the day counts describe a past
// state of the fleet, and every tab says so rather than presenting them as current.
const STALE_DAYS = 14;
function StaleBanner({ asof }) {
  if (!asof) return null;
  const age = Math.floor((Date.now() - new Date(asof).getTime()) / 86400000);
  if (!Number.isFinite(age) || age <= STALE_DAYS) return null;
  return (
    <Note>
      <strong>These estimates are {nfmt(age)} days old</strong> (as of {dfmt(asof)}). Every day count,
      overdue flag and probability on this tab describes the fleet on that date, not today. Refresh the
      survival run before using them to plan work.
    </Note>
  );
}

// A single line that appears wherever a fallback total is shown. It names
// the affected fleets and separates the numbers truncation can distort from
// the ones it cannot.
function TruncationNote({ roll }) {
  if (!roll || roll.source === 'summary' || !roll.capped.length) return null;
  const names = roll.capped.map((t) => deviceShort(t)).join(' and ');
  return (
    <Note>
      <strong>{names}</strong> reached the {nfmt(SERVER_CAP)}-row feed cap, so this screen is
      counting a truncated list for {roll.capped.length > 1 ? 'those fleets' : 'that fleet'}. The
      list is ordered worst-first, so the <strong>act-now counts are still complete</strong> --
      every act-now device sorts above the cut. Overdue counts, the risk-band mix and any average
      are understated at the healthy end. Deploy the <code>/ps5/summary</code> route to replace
      these with SQL aggregates over the whole table.
    </Note>
  );
}

function ExactnessBadge({ roll }) {
  if (!roll) return null;
  if (roll.source === 'summary') return <Badge tone="good">Totals from SQL aggregate</Badge>;
  if (!roll.capped.length) return <Badge tone="good">Complete feed</Badge>;
  return <Badge tone="warning" title="Counted from a capped browse list">Partial feed</Badge>;
}

// ---------------------------------------------------------------------
// VIEW 1 -- Fleet status
// ---------------------------------------------------------------------
function FleetStatus({ feeds }) {
  const roll = useRollup(feeds);
  const lb = feeds.leaderboard.data || [];
  const reg = feeds.registry.data || [];

  const best = useMemo(() => {
    const m = {};
    lb.forEach((r) => {
      const t = String(r.device_type).toUpperCase();
      const c = Number(r.oot_cindex);
      if (!Number.isFinite(c)) return;
      if (!m[t] || c > m[t].oot_cindex) m[t] = { ...r, oot_cindex: c, sd: Number(r.sd) };
    });
    return m;
  }, [lb]);

  const loading = feeds.devices.loading || feeds.devices.idle || feeds.summary.loading;

  if (loading && !roll) return <Loading height={260} label="Loading Remaining Useful Life & SLA Breach" />;
  if (feeds.devices.error && !roll) {
    return (
      <Section accent={TAB_COLOR.ps5} eyebrow="Remaining Useful Life & SLA Breach" title="Remaining useful life">
        <Card>
          <Badge tone="critical">Could not load</Badge>
          <div style={{ ...font.note, marginTop: 8 }}>{feeds.devices.error}</div>
        </Card>
      </Section>
    );
  }
  if (!roll) return <Empty height={200}>No Remaining Useful Life & SLA Breach rows are published for this city.</Empty>;

  const fleet = sumBy(roll.per, 'n_in_type');
  const scored = sumBy(roll.per, 'n_devices');
  const actNow = sumBy(roll.per, 'act_now');
  const overdue = sumBy(roll.per, 'overdue');
  const critical = sumBy(roll.per, 'CRITICAL');

  const bandRows = roll.per.map((p) => ({
    fleet: deviceShort(p.device_type),
    CRITICAL: p.CRITICAL, HIGH: p.HIGH, MEDIUM: p.MEDIUM, LOW: p.LOW,
  }));

  return (
    <>
      <Section accent={TAB_COLOR.ps5}
        eyebrow="Chicago / CTA-Ventra"
        title="How much life is left in the estate"
        sub="Survival models fitted per fleet on hardware out-of-service history, then read forward to a remaining-life estimate for every device."
        right={<ExactnessBadge roll={roll} />}
      >
        <Hero accent={TAB_COLOR.ps5}
          label="Devices to act on now"
          value={nfmt(actNow)}
          unit={`of ${nfmt(fleet)} scored`}
          sub={`Act-now means the model gives at least the fleet's threshold probability (${thresholdText(roll)}) of another out-of-service event within 7 days. Analysis as of ${roll.asof ? dfmt(roll.asof) : 'the date published by the feed'}.`}
          right={(
            <div style={{ textAlign: 'right' }}>
              <div style={{ ...font.micro, marginBottom: 6 }}>Share of the scored fleet</div>
              <div style={{ ...font.hero, ...font.num, fontSize: 27, color: actNow / Math.max(fleet, 1) > 0.1 ? STATUS.serious.fill : INK }}>
                {fleet ? pct(actNow / fleet, 1) : '--'}
              </div>
            </div>
          )}
        />
        <TruncationNote roll={roll} />
      </Section>

      <StatRow style={{ marginBottom: 22 }}>
        <Stat label="Devices with an estimate" value={nfmt(scored)} foot={scored === fleet ? 'Every device in the fitted population' : `${nfmt(fleet - scored)} not returned by the feed`} />
        {/* A FLAG THAT FIRES ON 93% OF THE FLEET IS NOT AN ALERT. Measured on
            the 08-Aug run: GATE 404/429, VALIDATOR 856/917, TVM 52/190. The
            tone follows the share, so the tile stops shouting once it is
            describing the norm rather than an exception. */}
        <Stat label="Past its typical interval"
              value={nfmt(overdue)}
              tone={fleet && overdue / fleet > 0.8 ? 'neutral' : 'warning'}
              foot={fleet
                ? (overdue / fleet > 0.8
                    ? `${pct(overdue / fleet, 0)} of the fleet -- at this share it describes the fleet, not a shortlist`
                    : `${pct(overdue / fleet, 0)} of the fleet is past its usual gap between faults`)
                : ''} />
        <Stat label="Critical risk band" value={nfmt(critical)} tone="critical" foot="Highest hazard score within its own fleet" />
        <Stat label="Fleets modelled" value={nfmt(roll.per.length)} foot="Fitted separately -- day counts are not comparable across them" />
      </StatRow>

      <Grid cols="repeat(auto-fit,minmax(420px,1fr))">
        <Panel
          title="Risk mix by fleet"
          hint="Bands are assigned WITHIN a fleet. A critical validator and a critical TVM are each the worst of their own population, not of each other."
        >
          <ColumnBars
            data={bandRows}
            xKey="fleet"
            stacked
            series={BAND_ORDER.map((b) => ({ key: b, label: STATUS[BAND_TONE[b]].label, color: bandColor(b) }))}
            fmt={_fmt1}
          />
        </Panel>

        <Panel
          title="Where the act-now devices are"
          hint="Counted per fleet. This total survives a capped feed because the browse list is ordered act-now first."
        >
          <RankBars
            data={roll.per
              .map((p) => ({ name: deviceShort(p.device_type), value: p.act_now, t: p.device_type }))
              .sort((a, b) => b.value - a.value)}
            xKey="value"
            yKey="name"
            height={200}
            colorBy={_colorBy2}
            fmt={_fmt1}
            unit="Devices"
          />
        </Panel>
      </Grid>

      <Panel
        style={{ marginTop: 18 }}
        title="Is this model allowed to raise a ServiceNow ticket yet?"
        hint={`The bar is a concordance of ${CINDEX_FLOOR} out of time. Below it, the ranking is not reliable enough to schedule against.`}
      >
        <div style={{ display: 'grid', gap: 10 }}>
          {roll.per.map((p) => {
            const b = best[p.device_type];
            const r = reg.find((x) => regType(x.device_type) === p.device_type);
            const ci = b ? b.oot_cindex : null;
            const passes = ci !== null && ci >= CINDEX_FLOOR;
            return (
              <div
                key={p.device_type}
                style={{
                  display: 'flex', alignItems: 'center', gap: 14, flexWrap: 'wrap',
                  padding: '10px 12px', border: `1px solid ${LINE}`, borderRadius: 10,
                  borderLeft: `3px solid ${deviceColor(p.device_type)}`,
                }}
              >
                <span style={{ fontSize: 13.5, fontWeight: 700, color: INK, minWidth: 140 }}>
                  {deviceName(p.device_type)}
                </span>
                <Badge tone={passes ? 'good' : 'warning'}>
                  {ci === null ? 'not measured' : `${ci.toFixed(3)} concordance`}
                </Badge>
                {b && Number.isFinite(b.sd) && (
                  <span style={{ ...font.note, fontSize: 12.2 }}>
                    +/- {b.sd.toFixed(3)} across folds
                  </span>
                )}
                <span style={{ ...font.note, fontSize: 12.2, flex: 1, minWidth: 220 }}>
                  {b ? b.model : 'no candidate model published'}
                </span>
                {r && r.dashboard_ready === false && (
                  <Badge tone="warning" title={r.blockers || ''}>registry not signed off</Badge>
                )}
              </div>
            );
          })}
        </div>
        <Note>
          {(() => {
            const pass = roll.per.filter((p) => best[p.device_type] && best[p.device_type].oot_cindex >= CINDEX_FLOOR)
              .map((p) => deviceShort(p.device_type));
            const ready = reg.some((x) => x.dashboard_ready === true);
            return `${pass.length ? `${pass.join(', ')} clear${pass.length === 1 ? 's' : ''}` : 'No fleet clears'} the ${CINDEX_FLOOR} bar. `
              + (ready ? '' : 'No fleet is signed off in the model registry, so ')
              + 'these estimates are a prioritisation aid for a planner, not an automatic scheduler.';
          })()}
        </Note>
      </Panel>
    </>
  );
}

// ---------------------------------------------------------------------
// VIEW 2 -- Devices
// ---------------------------------------------------------------------
function Devices({ feeds, onAnalyse, city }) {
  const loc = useLocations(city);
  const d = feeds.devices.data;
  const roll = useRollup(feeds);
  const rows = (d && d.rows) || [];

  const [type, setType] = useState('ALL');
  const [band, setBand] = useState('ALL');
  const [actOnly, setActOnly] = useState(false);

  const filtered = useMemo(() => rows.filter((r) => {
    if (type !== 'ALL' && String(r.device_type).toUpperCase() !== type) return false;
    if (band !== 'ALL' && String(r.risk_band).toUpperCase() !== band) return false;
    if (actOnly && !r.act_now) return false;
    return true;
  }).sort(worstFirst), [rows, type, band, actOnly]);

  const columns = useMemo(() => [
    { key: 'device_id', label: 'Device', width: 120 },
    {
      key: 'device_type',
      label: 'Fleet',
      width: 110,
      render: cell((r) => <span style={{ color: deviceColor(r.device_type), fontWeight: 600 }}>{deviceShort(r.device_type)}</span>),
    },
    { key: 'facility_id', label: 'Location', width: 190, render: cell((r) => loc.label(r.facility_id)) },
    {
      key: 'risk_band',
      label: 'Band',
      width: 100,
      render: cell((r) => <Badge tone={BAND_TONE[String(r.risk_band).toUpperCase()] || 'neutral'}>{r.risk_band}</Badge>),
    },
    // THE MEASURE IS TIME TO THE NEXT OOS EVENT, NOT TIME TO WEAR-OUT.
    // The fitted Weibull medians are 1.36d (GATE), 1.53d (TVM), 0.68d
    // (VALIDATOR), and they match the raw event rate -- 330,413 episodes over
    // 866 gates in ~730 days is one event every 1.9 days. So the model is
    // right and the old label was wrong: "Remaining life 1.0 days" reads as
    // "this device is about to be scrapped" when it means "a hardware-OOS Set
    // is expected within about a day". Renamed, not recalculated.
    { key: 'rul_standard_days', label: 'Days to next OOS', num: true, d: 1, width: 140 },
    { key: 'predicted_median_survival_days', label: 'Typical interval (d)', num: true, d: 1, width: 150 },
    { key: 'current_healthy_age_days', label: 'Fault-free days', num: true, d: 0, width: 130 },
    { key: 'days_since_hw_oos', label: 'Days since OOS', num: true, d: 0, width: 120 },
    { key: 'n_prior_oos', label: 'Prior OOS', num: true, d: 0, width: 95 },
    { key: 'roll_fail_30d', label: 'OOS in last 30d', num: true, d: 0, width: 120 },
    { key: 'p_oos_7d', label: 'P(OOS within 7d)', num: true, width: 140,
      render: cell((r) => (r.p_oos_7d === null || r.p_oos_7d === undefined ? <span style={{ color: INK_3 }}>--</span> : pct(Number(r.p_oos_7d), 0))) },
    { key: 'rul_rank_in_type', label: 'Rank in fleet', num: true, d: 0, width: 110 },
    {
      key: 'act_now',
      label: 'Meets act-now rule',
      width: 90,
      render: cell((r) => (r.act_now ? <Badge tone="critical">yes</Badge> : <span style={{ color: INK_3 }}>--</span>)),
    },
      analyseColumn(onAnalyse),
  ], [onAnalyse, loc]);

  if (feeds.devices.loading || feeds.devices.idle) return <Loading height={300} label="Loading devices" />;
  if (feeds.devices.error) {
    return (
      <Card><Badge tone="critical">Could not load</Badge>
        <div style={{ ...font.note, marginTop: 8 }}>{feeds.devices.error}</div></Card>
    );
  }

  return (
    <Section accent={TAB_COLOR.ps5}
      eyebrow="Device grain"
      title="Every device, worst first"
      sub="Ordered by soonest expected out-of-service event, exactly as the route returns it. Rank is within the device's own fleet."
    >
      {/* SURVIVAL DATA HAS A SHAPE.
          "3,235 devices, 2,504 past expected life" is a distribution collapsed
          into two numbers. The histogram shows whether the fleet is ageing
          evenly or splitting into a healthy group and a spent one -- which
          are different problems with different budgets. */}
      <Panel title="How the intervals are spread"
             hint="Every device placed by its expected days to the next out-of-service event. A pile at zero is a fleet that faults constantly, not a few urgent devices.">
        <Histogram data={filtered} valueKey="rul_standard_days" bins={26}
                   height={240} color={STATUS.critical.fill} xLabel="Days to next out-of-service event" />
      </Panel>

      <Toolbar>
        <Chip active={type === 'ALL'} onClick={() => setType('ALL')}>All fleets</Chip>
        {TYPES.map((t) => (
          <Chip key={t} active={type === t} onClick={() => setType(t)} color={deviceColor(t)}>
            {deviceShort(t)}
          </Chip>
        ))}
        <span style={{ width: 14 }} />
        <Chip active={band === 'ALL'} onClick={() => setBand('ALL')}>All bands</Chip>
        {BAND_ORDER.map((b) => (
          <Chip key={b} active={band === b} onClick={() => setBand(b)} color={bandColor(b)}>
            {STATUS[BAND_TONE[b]].label}
          </Chip>
        ))}
        <span style={{ width: 14 }} />
        {/* The chip carries its own count because act_now is TRUE for ~100%
            of devices on this run -- filtering by it removes almost nothing,
            and a filter that appears to narrow but does not is worse than no
            filter. */}
        <Chip active={actOnly} onClick={() => setActOnly((v) => !v)}>
          Act now only ({nfmt(rows.filter((r) => r.act_now).length)})
        </Chip>
      </Toolbar>

      <div style={{ ...font.note, margin: '0 0 10px' }}>
        Showing <strong style={{ color: INK }}>{nfmt(filtered.length)}</strong> of{' '}
        {nfmt(rows.length)} returned rows
        {roll && roll.source === 'summary' && sumBy(roll.per, 'n_devices') > rows.length && (
          <> -- the table is a browse list of {nfmt(rows.length)}; the fleet total of{' '}
            {nfmt(sumBy(roll.per, 'n_devices'))} on the status tab comes from the SQL aggregate.</>
        )}
      </div>

      <TruncationNote roll={roll} />

      <Card style={{ marginTop: 12 }} pad="12px 14px">
        <DataTable
          rows={filtered}
          columns={columns}
          height={520}
          pageSize={200}
          searchKeys={['device_id', 'device_type', 'facility_id', 'risk_band']}
          onRowClick={(r) => onAnalyse && onAnalyse(r.device_id)}
          exportName="ps5_device_rul"
          emptyText="No devices match the current filters."
        />
      </Card>

      <Note>
        With every fleet shown, the list is ordered act-now first and then by each device's rank
        WITHIN ITS OWN FLEET -- not by remaining-life days. Sorting the estate by days would put a
        3-day TVM and a 3-day validator side by side as if they meant the same thing, and they do
        not. Click any column header to re-sort; a days column sorted across fleets is a browse
        aid, not a ranking.
      </Note>

      <Note>
        <strong>This is time to the next out-of-service event, not time to wear-out.</strong> The
        survival models are fitted on hardware-OOS &lsquo;Set&rsquo; onsets, which occur about
        every one to two days per device across all three fleets -- so a reading of
        &ldquo;1 day&rdquo; means another fault is expected soon, not that the device is finished.
        It is not a probability of failing on a given day. Day counts come from separately fitted
        models, so a 1-day TVM and a 1-day validator are not the same claim: compare within a
        fleet, and use the rank column for any cross-fleet ordering.
      </Note>
    </Section>
  );
}

// ---------------------------------------------------------------------
// VIEW 2b -- Location
//
// PS5 had no location view at all while PS1-PS4 each had one, so a planner
// could ask "which depot is carrying the remaining-life risk" on four tabs
// out of five. This is a client-side rollup of the SAME device rows the
// Devices tab browses: no new endpoint, no new table, and the two tabs
// cannot disagree about a total because there is only one source.
//
// Ranked by CRITICAL then overdue, not by device count. A 300-device garage
// is not the headline; the one with eleven devices past their expected life
// is.
// ---------------------------------------------------------------------
function LocationView({ feeds, city }) {
  const loc = useLocations(city);
  const d = feeds.devices.data;
  const rows = (d && d.rows) || [];
  const [type, setType] = useState('ALL');

  const scoped = useMemo(
    () => rows.filter((r) => type === 'ALL' || String(r.device_type).toUpperCase() === type),
    [rows, type]
  );

  const sites = useMemo(() => {
    const by = new Map();
    scoped.forEach((r) => {
      const id = normFacilityId(r.facility_id);
      if (!id) return;
      const c = by.get(id) || {
        facility_id: id, devices: 0, overdue: 0, act_now: 0,
        CRITICAL: 0, HIGH: 0, MEDIUM: 0, LOW: 0, rul: [],
      };
      c.devices += 1;
      if (r.is_overdue) c.overdue += 1;
      if (r.act_now) c.act_now += 1;
      const b = String(r.risk_band || '').toUpperCase();
      if (b in c) c[b] += 1;
      const v = Number(r.rul_standard_days);
      if (Number.isFinite(v)) c.rul.push(v);
      by.set(id, c);
    });
    return Array.from(by.values()).map((c) => {
      // MEDIAN, not mean. One device at 4,000 days drags a mean far enough
      // to make a depot look healthy when half of it is not.
      const sorted = c.rul.slice().sort((a, b) => a - b);
      const n = sorted.length;
      const med = n ? (n % 2 ? sorted[(n - 1) / 2] : (sorted[n / 2 - 1] + sorted[n / 2]) / 2) : null;
      return {
        ...c,
        name: loc.name(c.facility_id),
        mapped: loc.known(c.facility_id),
        median_rul: med,
        overdue_share: c.devices ? c.overdue / c.devices : 0,
      };
    }).sort((a, b) => b.CRITICAL - a.CRITICAL || b.overdue - a.overdue || b.devices - a.devices);
  }, [scoped, loc]);

  const unmapped = sites.filter((s) => !s.mapped).length;

  if (feeds.devices.loading || feeds.devices.idle) return <Loading height={300} label="Loading locations" />;
  if (!rows.length) return <Card><Empty height={200}>No device rows to roll up.</Empty></Card>;

  const columns = [
    { key: 'name', label: 'Location', flex: 2,
      render: cell((r) => (r.mapped
        ? <span>{r.name}</span>
        : <span title="No name in the PS1 station dimension for this facility id">
            {r.facility_id} <span style={{ color: INK_3 }}>unmapped</span>
          </span>)) },
    { key: 'devices', label: 'Devices', num: true, width: 100 },
    { key: 'CRITICAL', label: 'Critical', num: true, width: 95 },
    { key: 'HIGH', label: 'High', num: true, width: 85 },
    { key: 'overdue', label: 'Past typical interval', num: true, width: 160 },
    { key: 'overdue_share', label: 'Share overdue', num: true, width: 130,
      render: cell((r) => pct(r.overdue_share, 1)) },
    { key: 'median_rul', label: 'Median days to next OOS', num: true, d: 1, width: 200 },
    { key: 'act_now', label: 'Meets act-now rule', num: true, width: 165 },
  ];

  return (
    <>
      <Section accent={TAB_COLOR.ps5}
        eyebrow="Location"
        title="Where the remaining-life risk sits"
        sub="Rolled up from the same device rows the Devices tab lists. Names come from the station dimension Failure Prediction uses, so a depot is called the same thing on every tab."
      >
        <Toolbar>
          <Chip active={type === 'ALL'} onClick={() => setType('ALL')}>All fleets</Chip>
          {TYPES.map((t) => (
            <Chip key={t} active={type === t} onClick={() => setType(t)} color={deviceColor(t)}>
              {deviceShort(t)}
            </Chip>
          ))}
        </Toolbar>

        <Grid cols="repeat(auto-fit,minmax(320px,1fr))">
          <Panel accent={TAB_COLOR.ps5} title="Most devices past their typical interval" hint="count, worst first">
            {sites.some((x) => x.overdue > 0) ? (
              <RankBars
                data={sites.filter((x) => x.overdue > 0).slice(0, 12)
                  .map((x) => ({ name: x.name, value: x.overdue }))}
                xKey="value" yKey="name" height={280}
                fmt={(v) => nfmt(v)} unit=" devices"
              />
            ) : <Empty height={240}>No device is past its typical interval in this run.</Empty>}
          </Panel>
          <Panel accent={TAB_COLOR.ps5} title="Shortest median interval to next OOS" hint="20+ devices only, so one bad unit cannot top the chart">
            {sites.filter((x) => x.devices >= 20 && x.median_rul !== null).length ? (
              <RankBars
                data={sites.filter((x) => x.devices >= 20 && x.median_rul !== null)
                  .sort((a, b) => a.median_rul - b.median_rul).slice(0, 12)
                  .map((x) => ({ name: x.name, value: Math.round(x.median_rul * 10) / 10 }))}
                xKey="value" yKey="name" height={280}
                fmt={(v) => nfmt(v, 1)} unit=" days"
              />
            ) : <Empty height={240}>No location has 20 or more scored devices.</Empty>}
          </Panel>
        </Grid>
      </Section>

      <Section accent={TAB_COLOR.ps5} eyebrow="Detail" title="Location rollup"
        sub={`${nfmt(sites.length)} location${sites.length === 1 ? '' : 's'}${unmapped ? `, ${nfmt(unmapped)} with no name in the station dimension` : ''}.`}
      >
        <Card pad="12px 14px">
          <DataTable rows={sites} columns={columns} height={420} pageSize={50}
                     accent={TAB_COLOR.ps5} exportName="ps5_location_rollup"
                     searchKeys={['name', 'facility_id']} />
        </Card>
        <Note accent={TAB_COLOR.ps5}>
          Day counts are fitted per fleet, so a median here mixes three baseline hazards when
          "All fleets" is selected. Use the fleet chips before comparing one location to another.
        </Note>
      </Section>
    </>
  );
}

// ---------------------------------------------------------------------
// VIEW 3 -- Components
// ---------------------------------------------------------------------
function Components({ feeds, onAnalyse }) {
  const c = feeds.components.data;
  const rows = (c && c.rows) || [];
  const capped = (c && c.capped) || [];
  const summary = feeds.compSummary.data;
  const grain = feeds.grain.data || [];

  const [type, setType] = useState('ALL');
  const [tier, setTier] = useState('ALL');

  // Dedupe on the declared key before ANY count. v_ps5_serial_dupes measures
  // 3,823 duplicated (device_id, component_serial_nbr) keys on validators at
  // up to 25 rows each, with every measured column identical inside the
  // duplicate -- a roster fan-out, not a second reading of the part. Counting
  // the raw rows would multiply the validator component estimate by about
  // four.
  const deduped = useMemo(() => {
    const seen = new Set();
    const out = [];
    rows.forEach((r) => {
      const k = `${r.device_id}||${r.component_serial_nbr}`;
      if (seen.has(k)) return;
      seen.add(k);
      out.push(r);
    });
    return out;
  }, [rows]);

  const dropped = rows.length - deduped.length;

  const filtered = useMemo(() => deduped.filter((r) => {
    if (type !== 'ALL' && String(r.device_type).toUpperCase() !== type) return false;
    if (tier !== 'ALL' && String(r.risk_tier).toUpperCase() !== tier) return false;
    return true;
  }).sort(worstComponentFirst), [deduped, type, tier]);

  const byComponent = useMemo(() => {
    const m = {};
    deduped.forEach((r) => {
      const k = r.component_type_name || 'unknown';
      if (!m[k]) m[k] = { name: k, total: 0, critical: 0, act: 0 };
      m[k].total += 1;
      if (String(r.risk_tier).toUpperCase() === 'CRITICAL') m[k].critical += 1;
      if (r.act_now) m[k].act += 1;
    });
    return Object.values(m).sort((a, b) => b.critical - a.critical);
  }, [deduped]);

  const tierMix = useMemo(() => BAND_ORDER
    .map((b) => ({ name: STATUS[BAND_TONE[b]].label, value: deduped.filter((r) => String(r.risk_tier).toUpperCase() === b).length, band: b }))
    .filter((d) => d.value > 0), [deduped]);

  const columns = useMemo(() => [
    { key: 'component_serial_nbr', label: 'Serial', width: 140 },
    { key: 'component_type_name', label: 'Component', width: 130 },
    { key: 'device_id', label: 'On device', width: 120 },
    {
      key: 'device_type',
      label: 'Fleet',
      width: 110,
      render: cell((r) => <span style={{ color: deviceColor(r.device_type), fontWeight: 600 }}>{deviceShort(r.device_type)}</span>),
    },
    {
      key: 'risk_tier',
      label: 'Tier (within fleet)',
      width: 100,
      render: cell((r) => <Badge tone={BAND_TONE[String(r.risk_tier).toUpperCase()] || 'neutral'}>{r.risk_tier}</Badge>),
    },
    { key: 'component_age_days', label: 'Age (d)', num: true, d: 0, width: 95 },
    { key: 'expected_component_rul_days', label: 'Component RUL (d)', num: true, d: 1, width: 140 },
    { key: 'predicted_median_survival_days', label: 'Device median (d)', num: true, d: 1, width: 140 },
    { key: 'device_oos_failures_total', label: 'Device OOS total', num: true, d: 0, width: 130 },
    {
      key: 'act_now',
      label: 'Meets act-now rule',
      width: 90,
      render: cell((r) => (r.act_now ? <Badge tone="critical">yes</Badge> : <span style={{ color: INK_3 }}>--</span>)),
    },
  ], []);

  if (feeds.components.loading || feeds.components.idle) return <Loading height={300} label="Loading components" />;
  if (feeds.components.error) {
    return (
      <Card><Badge tone="critical">Could not load</Badge>
        <div style={{ ...font.note, marginTop: 8 }}>{feeds.components.error}</div></Card>
    );
  }

  const totalComponents = Array.isArray(summary) && summary.length
    ? sumBy(summary, 'n_components')
    : deduped.length;
  const summaryRows = Array.isArray(summary) && summary.length ? sumBy(summary, 'n_rows') : rows.length;

  return (
    <Section accent={TAB_COLOR.ps5}
      eyebrow="Component grain"
      title="Which parts are closest to the end of their life"
      sub="Serial-numbered components carried by devices that Remaining Useful Life & SLA Breach has scored. Counts are distinct on device plus serial, never on raw rows."
      right={dropped > 0 || summaryRows > totalComponents ? <Badge tone="warning">Grain defect present</Badge> : <Badge tone="good">Grain clean</Badge>}
    >
      {/* Decided 26-Sep-2026: the component columns stay on screen for verification, with this notice. */}
      <Card style={{ borderLeft: `3px solid ${STATUS.critical.fill}`, marginBottom: 16 }}>
        <div style={{ fontSize: 13.5, fontWeight: 700, color: INK }}>For verification and UAT only -- not for operational use</div>
        <div style={{ ...font.note, marginTop: 6 }}>
          There is no component-level failure model yet. Component remaining life, overdue flags and risk tiers are
          derived from the host DEVICE's out-of-service model (typical gap about a day) applied to part ages of
          months or years, and the models were fitted on data as of the analysis date shown on this tab. That is why
          nearly every part reads as overdue. The rows are here so the data can be checked end to end; they must not
          be used to schedule part replacement. A part's act-now flag is its host device's.
        </div>
      </Card>
      {(dropped > 0 || (Array.isArray(summary) && summaryRows > totalComponents)) && (
        <Card style={{ borderLeft: `3px solid ${STATUS.warning.fill}`, marginBottom: 16 }}>
          <div style={{ display: 'flex', gap: 10, alignItems: 'flex-start' }}>
            <TriangleAlert size={16} color={STATUS.warning.fill} style={{ marginTop: 2, flex: '0 0 auto' }} />
            <div>
              <div style={{ fontSize: 13.5, fontWeight: 700, color: INK }}>
                The component feed repeats rows, and this screen collapses them
              </div>
              <div style={{ ...font.note, marginTop: 6 }}>
                The published component table is not unique on (device, serial).{' '}
                {Array.isArray(summary) && summaryRows > totalComponents
                  ? <>Across the estate it holds <strong>{nfmt(summaryRows)}</strong> rows for{' '}
                    <strong>{nfmt(totalComponents)}</strong> distinct components.</>
                  : <>This page received {nfmt(rows.length)} rows for {nfmt(deduped.length)} distinct components.</>}
                {' '}Every column measured by the grain audit is identical inside a repeated key,
                so the repetition is a roster fan-out rather than a second reading of the part.
                Counts here are deduplicated. The audit itself is on the How we know tab and is
                left visible on purpose -- the defect is in the pipeline, not on this screen, and
                it is not fixed until the notebook writes one row per part.
              </div>
            </div>
          </div>
        </Card>
      )}

      <Grid cols="repeat(auto-fit,minmax(210px,1fr))" style={{ marginBottom: 20 }}>
        <Stat label="Distinct components scored" value={nfmt(totalComponents)} foot={`Across ${nfmt(new Set(deduped.map((r) => r.device_id)).size)} devices in the returned set`} />
        {/* risk_score = device_oos_failures_total / component_age_days. On
            GATE and TVM the numerator is large enough that the tiers collapse:
            measured 1,123/1,236 (91%) and 1,813/1,963 (92%) CRITICAL on the
            08-Aug run, against 2,422/8,519 (28%) on VALIDATOR. The share is
            shown so nobody reads a 91% CRITICAL fleet as a shortlist. */}
        <Stat label="Critical tier"
              value={nfmt(deduped.filter((r) => String(r.risk_tier).toUpperCase() === 'CRITICAL').length)}
              tone={deduped.length && deduped.filter((r) => String(r.risk_tier).toUpperCase() === 'CRITICAL').length / deduped.length > 0.8 ? 'neutral' : 'critical'}
              foot={deduped.length
                ? `${pct(deduped.filter((r) => String(r.risk_tier).toUpperCase() === 'CRITICAL').length / deduped.length, 0)} of the components returned`
                : 'Of the components returned'} />
        <Stat label="Meets act-now rule"
              value={nfmt(deduped.filter((r) => r.act_now).length)}
              tone={deduped.length && deduped.filter((r) => r.act_now).length / deduped.length > 0.8 ? 'neutral' : 'warning'}
              foot={deduped.length
                ? `${pct(deduped.filter((r) => r.act_now).length / deduped.length, 0)} of components -- host device meets its fleet's act-now probability`
                : 'Host device meets its fleet act-now probability'} />
        <Stat label="Component types" value={nfmt(byComponent.length)} foot={byComponent.slice(0, 3).map((b) => b.name).join(', ')} />
      </Grid>

      {capped.length > 0 && (
        <Note>
          <strong>{capped.map((t) => deviceShort(t)).join(' and ')}</strong> reached the{' '}
          {nfmt(SERVER_CAP)}-row feed cap, so the component list below is a worst-first slice for{' '}
          {capped.length > 1 ? 'those fleets' : 'that fleet'} rather than the whole of it. The
          estate totals above come from the aggregate route where it is available.
        </Note>
      )}

      <Grid cols="repeat(auto-fit,minmax(400px,1fr))" style={{ margin: '16px 0 20px' }}>
        <Panel title="Components in the critical tier, by part" hint="Ranked by the number of parts of that type sitting in the worst tier.">
          <RankBars
            data={byComponent.slice(0, 10).map((b) => ({ name: b.name, value: b.critical }))}
            xKey="value"
            yKey="name"
            height={260}
            color={STATUS.critical.fill}
            fmt={_fmt1}
            unit="Components"
          />
        </Panel>
        <Panel title="Risk tier mix" hint="Across every component returned, after deduplication.">
          <Donut
            data={tierMix}
            colors={_colors3}
            centerValue={nfmt(deduped.length)}
            centerLabel="components"
            height={260}
          />
        </Panel>
      </Grid>

      <Toolbar>
        <Chip active={type === 'ALL'} onClick={() => setType('ALL')}>All fleets</Chip>
        {TYPES.map((t) => (
          <Chip key={t} active={type === t} onClick={() => setType(t)} color={deviceColor(t)}>
            {deviceShort(t)}
          </Chip>
        ))}
        <span style={{ width: 14 }} />
        <Chip active={tier === 'ALL'} onClick={() => setTier('ALL')}>All tiers</Chip>
        {BAND_ORDER.map((b) => (
          <Chip key={b} active={tier === b} onClick={() => setTier(b)} color={bandColor(b)}>
            {STATUS[BAND_TONE[b]].label}
          </Chip>
        ))}
      </Toolbar>

      <Card pad="12px 14px">
        <DataTable
          rows={filtered}
          columns={columns}
          height={500}
          pageSize={200}
          searchKeys={['component_serial_nbr', 'component_type_name', 'device_id', 'device_type', 'risk_tier']}
          onRowClick={(r) => onAnalyse && onAnalyse(r.device_id)}
          exportName="ps5_component_rul"
          emptyText="No components match the current filters."
        />
      </Card>

      {grain.length > 0 && (
        <Note>
          Grain audit, straight from the database:{' '}
          {grain.map((g) => `${deviceShort(g.device_type)} ${nfmt(g.n_dup_keys)} repeated keys across ${nfmt(g.n_rows)} rows, worst ${nfmt(g.worst_dup)}`).join('; ')}.
          Fleets absent from that list are unique on (device, serial) and need no deduplication.
        </Note>
      )}
    </Section>
  );
}

// ---------------------------------------------------------------------
// VIEW 4 -- Model quality
// ---------------------------------------------------------------------
function ModelQuality({ feeds }) {
  const lb = feeds.leaderboard.data || [];
  const reg = feeds.registry.data || [];
  const imp = feeds.importance.data || [];
  const [type, setType] = useState('GATE');

  const loading = feeds.leaderboard.loading || feeds.leaderboard.idle;
  if (loading) return <Loading height={280} label="Loading model quality" />;

  const forType = lb
    .filter((r) => String(r.device_type).toUpperCase() === type)
    .map((r) => ({ ...r, oot_cindex: Number(r.oot_cindex), sd: Number(r.sd), feats: Number(r.feats) }))
    .sort((a, b) => b.oot_cindex - a.oot_cindex);

  const impRows = imp
    .filter((r) => String(r.device_type).toUpperCase() === type)
    .map((r) => ({ ...r, cindex_drop: Number(r.cindex_drop) }))
    .sort((a, b) => b.cindex_drop - a.cindex_drop)
    .slice(0, 12);

  const bestByType = {};
  lb.forEach((r) => {
    const t = String(r.device_type).toUpperCase();
    const c = Number(r.oot_cindex);
    if (!Number.isFinite(c)) return;
    if (!bestByType[t] || c > bestByType[t]) bestByType[t] = c;
  });

  return (
    <>
      <Section accent={TAB_COLOR.ps5}
        eyebrow="Evidence"
        title="How well does the model actually rank?"
        sub="Concordance is the chance the model puts a device that failed sooner ahead of one that failed later. 0.5 is a coin toss."
      >
        <Panel
          title="The two concordance numbers, side by side"
          hint="They measure different things and are never averaged. The gap between them is the finding."
        >
          <div style={{ display: 'grid', gap: 10 }}>
            <div style={{ display: 'flex', gap: 12, ...font.micro, padding: '0 12px' }}>
              <span style={{ minWidth: 150 }}>Fleet</span>
              <span style={{ minWidth: 150 }}>Best candidate</span>
              <span style={{ minWidth: 150 }}>Promoted in registry</span>
              <span>Registry state</span>
            </div>
            {TYPES.map((t) => {
              const r = reg.find((x) => regType(x.device_type) === t);
              const cand = bestByType[t];
              const promoted = r ? Number(r.concordance_index) : null;
              return (
                <div
                  key={t}
                  style={{
                    display: 'flex', gap: 12, alignItems: 'center', flexWrap: 'wrap',
                    padding: '10px 12px', border: `1px solid ${LINE}`, borderRadius: 10,
                    borderLeft: `3px solid ${deviceColor(t)}`,
                  }}
                >
                  <span style={{ minWidth: 150, fontSize: 13.5, fontWeight: 700, color: INK }}>{deviceName(t)}</span>
                  <span style={{ minWidth: 150 }}>
                    <Badge tone={cand >= CINDEX_FLOOR ? 'good' : 'warning'}>
                      {Number.isFinite(cand) ? cand.toFixed(3) : '--'}
                    </Badge>
                  </span>
                  <span style={{ minWidth: 150 }}>
                    <Badge tone={promoted !== null && promoted >= CINDEX_FLOOR ? 'good' : 'critical'}>
                      {promoted === null || !Number.isFinite(promoted) ? '--' : promoted.toFixed(3)}
                    </Badge>
                  </span>
                  <span style={{ ...font.note, fontSize: 12.2, flex: 1, minWidth: 220 }}>
                    {r ? `${r.registry_status}${r.blockers ? ` -- ${r.blockers}` : ''}` : 'no registry row'}
                  </span>
                </div>
              );
            })}
          </div>
          <Note>
            {(() => {
              const pass = TYPES.filter((t) => bestByType[t] >= CINDEX_FLOOR).map((t) => deviceShort(t));
              const same = TYPES.every((t) => {
                const r = reg.find((x) => regType(x.device_type) === t);
                return !r || !Number.isFinite(bestByType[t]) || Math.abs(Number(r.concordance_index) - bestByType[t]) < 1e-6;
              });
              return `${pass.length ? `The best candidate clears ${CINDEX_FLOOR} on ${pass.join(', ')}` : `No candidate clears ${CINDEX_FLOOR}`}. `
                + (same ? 'The registry figure is currently taken from the same leaderboard row, so the two columns agree. ' : 'The registry figure differs from the best candidate on at least one fleet. ')
                + (reg.some((x) => x.dashboard_ready === true) ? '' : 'No fleet is signed off, so this is a prioritisation aid, not an automatic scheduler.');
            })()}
          </Note>
        </Panel>
      </Section>

      <Toolbar>
        {TYPES.map((t) => (
          <Chip key={t} active={type === t} onClick={() => setType(t)} color={deviceColor(t)}>
            {deviceShort(t)}
          </Chip>
        ))}
      </Toolbar>

      <Grid cols="repeat(auto-fit,minmax(420px,1fr))">
        <Panel
          title={`Candidate models -- ${deviceName(type)}`}
          hint="Out-of-time concordance with its fold-to-fold spread. A high score with a wide spread is not better than a steadier lower one."
        >
          {forType.length ? (
            <>
              <RankBars
                data={forType.map((r) => ({ name: r.model, value: r.oot_cindex }))}
                xKey="value"
                yKey="name"
                height={280}
                colorBy={_colorBy4}
                fmt={_fmt5}
                unit="Concordance"
              />
              <div style={{ marginTop: 12, display: 'grid', gap: 5 }}>
                {forType.map((r) => (
                  <div key={r.model} style={{ display: 'flex', justifyContent: 'space-between', gap: 12, ...font.note, fontSize: 12.2 }}>
                    <span>{r.model}</span>
                    <span style={font.num}>
                      {r.oot_cindex.toFixed(3)} +/- {r.sd.toFixed(3)} &middot; {nfmt(r.feats)} features
                    </span>
                  </div>
                ))}
              </div>
            </>
          ) : <Empty height={240}>No leaderboard rows for this fleet.</Empty>}
        </Panel>

        <Panel
          title={`What the model leans on -- ${deviceName(type)}`}
          hint="Permutation importance: the FALL in concordance when the feature is shuffled. Larger means the model needs it more."
        >
          {impRows.length ? (
            <>
              <RankBars
                data={impRows.map((r) => ({ name: r.feature_name, value: r.cindex_drop, enr: r.is_enriched }))}
                xKey="value"
                yKey="name"
                height={300}
                colorBy={_colorBy6}
                fmt={_fmt7}
                unit="Concordance drop"
              />
              <div style={{ display: 'flex', gap: 16, marginTop: 10 }}>
                <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6, fontSize: 11.2, color: INK_2 }}>
                  <span style={{ width: 10, height: 10, borderRadius: 3, background: CAT[0] }} /> From the failure history alone
                </span>
                <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6, fontSize: 11.2, color: INK_2 }}>
                  <span style={{ width: 10, height: 10, borderRadius: 3, background: CAT[5] }} /> From an enrichment source
                </span>
              </div>
            </>
          ) : <Empty height={280}>No importance rows for this fleet.</Empty>}
        </Panel>
      </Grid>
    </>
  );
}

// ---------------------------------------------------------------------
// VIEW 5 -- How we know
// ---------------------------------------------------------------------
function Evidence({ feeds }) {
  const cov = feeds.coverage.data || [];
  const lb = feeds.leaderboard.data || [];
  const roll = useRollup(feeds);
  const compSummary = feeds.compSummary.data;
  const [type, setType] = useState('GATE');

  const covRows = cov.filter((r) => String(r.device_type).toUpperCase() === type);

  const covColumns = [
    { key: 'source_name', label: 'Source', width: 90 },
    { key: 'table_name', label: 'Table', width: 320 },
    {
      key: 'status',
      label: 'Status',
      width: 100,
      render: cell((r) => <Badge tone={String(r.status) === 'joined' ? 'good' : 'warning'}>{r.status}</Badge>),
    },
    { key: 'pct_matched', label: 'Matched', num: true, d: 1, width: 100 },
    { key: 'n_feats', label: 'Features', num: true, d: 0, width: 90 },
    { key: 'tel_min', label: 'From', width: 110 },
    { key: 'tel_max', label: 'To', width: 110 },
  ];

  return (
    <>
      <Section accent={TAB_COLOR.ps5}
        eyebrow="Provenance"
        title="Where these numbers come from"
        sub="Every panel on this tab is a live read of the same Aurora tables the rest of the screen uses. Nothing here is typed in."
      >
        <Grid cols="repeat(auto-fit,minmax(260px,1fr))">
          <Stat
            label="Analysis as of"
            value={roll && roll.asof ? dfmt(roll.asof) : '--'}
            foot="feature_asof_date on the device table -- the same client extract Failure Prediction, Failure Pattern & Cascade Identification and Anomaly & Outlier Analysis run on"
          />
          <Stat
            label="Enrichment sources joined"
            value={`${nfmt(cov.filter((r) => String(r.status) === 'joined').length)} / ${nfmt(cov.length)}`}
            foot="Across all three fleets"
          />
          <Stat
            label="Component rows per distinct part"
            value={Array.isArray(compSummary) && sumBy(compSummary, 'n_components')
              ? (sumBy(compSummary, 'n_rows') / sumBy(compSummary, 'n_components')).toFixed(2)
              : '--'}
            tone={Array.isArray(compSummary) && sumBy(compSummary, 'n_rows') > sumBy(compSummary, 'n_components') ? 'warning' : 'good'}
            foot="1.00 is correct. Anything above it is the fan-out."
          />
        </Grid>
      </Section>

      {/* WHICH SURVIVAL FAMILY IS ACTUALLY FITTED.            06-Aug-2026
          Every reliability figure on this tab is produced by one of these
          models, and until now the screen never said which. Read entirely
          from ps5_cindex_leaderboard, which is already loaded -- no new
          table, no new endpoint. The Weibull SHAPE and SCALE the notebook
          fits, and the Cox hazard ratios, are NOT in Aurora yet:
          ps5_weibull_params and ps5_cox_hazard_ratios are declared in
          01_schema_core.sql and nothing writes to them. Survival curves
          wait on that, after the PS5 run. */}
      <Section accent={TAB_COLOR.ps5}
        eyebrow="Method"
        title="Which survival model each fleet is fitted with"
        sub="Remaining Useful Life is not one model. Each fleet is fitted separately and the winning family differs, which is why a day count from one fleet cannot be read beside another."
      >
        {lb.length ? (
          <div style={{ display: 'grid', gap: 10 }}>
            {TYPES.map((t) => {
              const rows = lb
                .filter((r) => String(r.device_type).toUpperCase() === t)
                .map((r) => ({ ...r, oot_cindex: Number(r.oot_cindex) }))
                .filter((r) => Number.isFinite(r.oot_cindex))
                .sort((a, b) => b.oot_cindex - a.oot_cindex);
              const top = rows[0];
              return (
                <div key={t} style={{
                  display: 'flex', gap: 12, alignItems: 'center', flexWrap: 'wrap',
                  padding: '10px 12px', border: `1px solid ${LINE}`, borderRadius: 10,
                  borderLeft: `3px solid ${deviceColor(t)}`,
                }}>
                  <span style={{ minWidth: 140, fontSize: 13.5, fontWeight: 700, color: INK }}>{deviceName(t)}</span>
                  {top ? (
                    <>
                      <span style={{ minWidth: 190, fontSize: 13.1, color: INK }}>{top.model}</span>
                      <Badge tone={top.oot_cindex >= CINDEX_FLOOR ? 'good' : 'warning'}>
                        {top.oot_cindex.toFixed(3)} concordance
                      </Badge>
                      <span style={{ ...font.note, fontSize: 12.2, flex: 1, minWidth: 220 }}>
                        Best of {nfmt(rows.length)} candidate{rows.length === 1 ? '' : 's'} fitted for this fleet.
                      </span>
                    </>
                  ) : (
                    <span style={{ ...font.note, fontSize: 12.2 }}>No leaderboard row for this fleet.</span>
                  )}
                </div>
              );
            })}
          </div>
        ) : <Empty height={140}>The leaderboard returned no rows.</Empty>}
        <Note accent={TAB_COLOR.ps5}>
          The hazard score on the device and component tables is a RELATIVE hazard from the fitted
          model, not a probability and not a rate. It orders devices within their own fleet and has
          no meaning across fleets, because each fleet has its own baseline hazard. Weibull shape
          and scale, and the Cox hazard ratios per fault code, are computed by the notebook but are
          not yet loaded into Aurora, so no survival curve is drawn here.
        </Note>
      </Section>

      <Section accent={TAB_COLOR.ps5} eyebrow="Enrichment" title="Which sources actually joined" sub="A survival model is only as good as the history it can see. This is what it could see.">
        <Toolbar>
          {TYPES.map((t) => (
            <Chip key={t} active={type === t} onClick={() => setType(t)} color={deviceColor(t)}>
              {deviceShort(t)}
            </Chip>
          ))}
        </Toolbar>
        {feeds.coverage.loading || feeds.coverage.idle ? <Loading height={220} /> : (
          <Card pad="12px 14px">
            <DataTable
              rows={covRows}
              columns={covColumns}
              height={340}
              pageSize={50}
              searchable={false}
              exportName={`ps5_coverage_${type}`}
              emptyText="No coverage rows for this fleet."
            />
          </Card>
        )}
        {covRows.length > 0 && (
          <Note>
            Lowest match rate on this fleet is{' '}
            <strong>{pct100(Math.min(...covRows.map((r) => Number(r.pct_matched) || 0)), 1)}</strong>{' '}
            ({covRows.slice().sort((a, b) => Number(a.pct_matched) - Number(b.pct_matched))[0].table_name}).
            A source that matched on only part of the fleet contributes features to some devices
            and nulls to the rest, which is why the permutation importances differ by fleet.
          </Note>
        )}
      </Section>
    </>
  );
}

// ---------------------------------------------------------------------
// Shell
// ---------------------------------------------------------------------
export default function PS5Overview({ city = 'CHI' }) {
  const [view, setView] = useState('overview');
  const [analyse, setAnalyse] = useState(null);
  const { feeds, request, refetch } = useFeeds(city);

  useEffect(() => { request(['summary', ...(VIEW_FEEDS[view] || [])]); }, [view, request]);
  const asof = useMemo(() => {
    let a = '';
    (feeds.summary.data || []).forEach((r) => { const v = String(r.feature_asof_date || ''); if (v > a) a = v; });
    return a || null;
  }, [feeds.summary.data]);

  const busy = ALL_KEYS.some((k) => feeds[k].loading);

  return (
    <div>
      {/* The stylesheet is mounted by V2Shell, but this tab also serves its own
          standalone route, where nothing else mounts it. Duplicate <style> tags
          are identical rules and are harmless. */}
      <V2Style />

      {/* Refresh rides in the tab rail's `right` slot rather than sitting in the
          tab row itself. As a sibling of the tabs it looked like a sixth tab,
          and clicking a tab that reloads everything is not what it promises. */}
      <Tabs
        items={VIEWS}
        value={view}
        onChange={setView}
        variant="sub"
        parent="ps5"
        right={(
          <button
            type="button"
            onClick={refetch}
            disabled={busy}
            className="v2-chip"
            data-on="0"
            style={{
              border: `1.5px solid ${tint(TAB_COLOR.ps5, A.rule)}`, background: '#FFF', borderRadius: 9,
              padding: '5px 12px', fontSize: 12.2, fontWeight: 700,
              color: busy ? INK_3 : TAB_COLOR.ps5, fontFamily: 'inherit',
              cursor: busy ? 'default' : 'pointer', display: 'inline-flex', alignItems: 'center', gap: 6,
            }}
          >
            <RefreshCw size={13} /> {busy ? 'Loading' : 'Refresh'}
          </button>
        )}
      />

      <StaleBanner asof={asof} />
      {view === 'overview' && <FleetStatus feeds={feeds} />}
      {view === 'devices' && <Devices feeds={feeds} onAnalyse={setAnalyse} city={city} />}
      {view === 'location' && <LocationView feeds={feeds} city={city} />}
      {view === 'components' && <Components feeds={feeds} onAnalyse={setAnalyse} />}
      {view === 'model' && <ModelQuality feeds={feeds} />}
      {view === 'evidence' && <Evidence feeds={feeds} />}

      {analyse && <AnalyseModal city={city} deviceId={analyse} onClose={() => setAnalyse(null)} />}
    </div>
  );
}

// FONTS_SCALED 04-Aug-2026
