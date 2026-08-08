// =====================================================================
// v2/PS1Overview.jsx -- Failure Prediction, macro to micro, across six sub-tabs.
//
//   Overview -> Depots -> Devices -> Components -> Drivers -> Evidence
//
// -------------------------------------------------------------------
// WHICH ENDPOINT MEANS WHAT. READ THIS BEFORE CHANGING A NUMBER.
// -------------------------------------------------------------------
// Getting this wrong put "600 of 600 devices need a work order" on screen
// on 01-Aug, with every funnel stage at 100%. It was an artifact of a
// LIMIT, not a finding about Chicago. The three traps:
//
//   /ps1/predictions      TOP 200 PER DEVICE TYPE, ranked by probability.
//                         Not the fleet. Minimum probability in the slice
//                         is 0.81 gates / 0.96 TVMs / 0.9995 validators,
//                         so every row is above threshold BY CONSTRUCTION.
//                         Legitimate use: the work list. Illegitimate use:
//                         any denominator, any percentage, any total.
//
//   /ps1/station-summary  THE REAL PER-DEVICE ROLL-UP. 44 depots, 1,951
//                         devices, 799 predicted failures, 775 critical,
//                         plus high_count and medium_count. Every fleet
//                         total on this screen comes from here.
//                         It has NO device-type breakdown -- so a per-fleet
//                         device count cannot be computed from any endpoint
//                         that exists, and none is shown.
//
//   /ps1/xw-tiers         DEVICE-DAYS, not devices (786,525 of them).
//                         Safe for LIFT, which is a rate. Never to be put
//                         in the same chart as a device count.
//
//   /ps1/risk-bands       NOT a risk-band distribution. Its bands are
//                         "Top 1% / 5% / 10%" and device_count repeats
//                         identically across all three -- it is a
//                         precision-at-k table. Not used here.
//
// OMITTED ENTIRELY, all confirmed empty via /ps1/table-status:
//   /ps1/summary  /ps1/model-performance  /ps1/calibration
//   /ps1/explainability  /ps1/features  /ps1/feature-importance
// The drivers come from /ps1/xw-drivers, which has real signed SHAP.
//
// -------------------------------------------------------------------
// LOADING MODEL
// -------------------------------------------------------------------
// Each feed owns its own {rows, loading, error}. The shell paints at once;
// panels fill as their data lands; a failed feed degrades ONE card. The
// first version used Promise.all, so one slow call blocked the page and one
// failure emptied it -- with /ps1/predictions taking 13-17s against a 15s
// socket timeout, that happened about half the time. Requests run four at a
// time, predictions first. The search index waits for the fleet.
// =====================================================================
import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { RefreshCw, TriangleAlert } from 'lucide-react';
import {
  CAT, INK, INK_2, INK_3, LINE, STATUS, deviceColor, deviceShort,
  dfmt, font, nfmt, pct, riskBand, A, TAB_COLOR, tint,
} from './theme';
import {
  Badge, Breadcrumb, Card, Chip, DrilldownProvider, Empty, Grid, Hero, Loading,
  Note, Panel, Section, Stat, useDrill, Rule, Tabs, V2Style,
} from './Kit';
import { Bubble, ColumnBars, Donut, FunnelView, Matrix, RankBars, TreemapChart, Trend } from './Charts';
import DataTable from './DataTable';
import GlobalSearch, { useSearchIndex } from './GlobalSearch';
import AnalyseModal from './AnalyseModal';
import { BASE, ps1 as api } from './v2api';

const TYPES = ['GATE', 'TVM', 'VALIDATOR'];
const BANDS = ['critical', 'serious', 'warning', 'good'];

const VIEWS = [
  { key: 'overview', label: 'Fleet status', sub: 'Where to send an engineer' },
  { key: 'depots', label: 'Depots', sub: 'Where the work is' },
  { key: 'devices', label: 'Devices', sub: 'The work list' },
  { key: 'components', label: 'Components', sub: 'Which part carries the risk' },
  { key: 'drivers', label: 'Why', sub: 'What moves the prediction' },
  { key: 'evidence', label: 'How we know', sub: 'Accuracy in plain terms' },
];

// EVERY FEED, AND WHICH TAB NEEDS IT.
//
// Fetching all fifteen on first paint was wrong. These are not cheap calls:
// xw-state-mix takes 14.5s ALONE, for 20 rows, because it scans 786,525
// device-days. Fifteen of those at once meant nothing finished.
//
// Now a tab requests only its own feeds, once, on first visit, and the
// result is cached for the rest of the session. Fleet status opens on four
// calls instead of fifteen.
const FEED_FN = {
  stations: api.stations,
  predictions: api.predictions,
  trend: api.riskTrend,
  tiers: api.xwTiers,
  chronic: api.xwChronic,
  drivers: api.xwDrivers,
  stateMix: api.xwStateMix,
  flagReason: api.xwFlagReason,
  causation: api.xwCausation,
  baseRate: api.xwBaseRate,
  serials: api.serialPredictions,
  components: api.componentInventory,
  confusion: api.confusion,
  sweep: api.thresholdSweep,
  // 04-Aug-2026. /ps1/leaderboard carries the per-model scorecard the previous
  // dashboard showed and this one dropped: auc, ap, f1, precision, recall,
  // which model won, and the run's own verdict string. It was already in the
  // API and simply unwired.
  scorecard: api.leaderboard,
  actNow: api.xwActNow,
};

const VIEW_FEEDS = {
  overview: ['stations', 'trend', 'tiers', 'chronic'],
  depots: ['stations'],
  // chronic IS requested here again.                        04-Aug-2026
  // It was dropped because pairing a 6.2s query with the 2.1s prediction
  // query starved the device table on a two-worker pool. The cost has since
  // changed: the state view was rebuilt to a single scan and the whole PS1
  // family came down with it, so the pairing no longer starves anything.
  // Leaving it out meant the repeat-offender chart on this tab had no data
  // and simply looked broken -- a chart that never fills is worse than a
  // chart that fills a second late.
  devices: ['predictions', 'chronic'],
  components: ['serials', 'components'],
  // Ordered cheapest-first, measured. stateMix is last on purpose: at
  // ~13.5s it is the only one that cannot share the pipe with anything.
  // stateMix is NOT here any more, and that is the fix for the slow Why tab.
  //
  // stateMix is LAST, deliberately. It was taken off this tab when it cost
  // ~13.5s and blocked the six cheap panels behind it. Measured again after
  // the state view was rebuilt: 11.9s, and the six ahead of it no longer
  // wait on it. Being last means every other panel paints first and this one
  // fills in after, which is why it can be requested automatically instead
  // of hiding behind a button the reader has to discover.
  drivers: ['causation', 'drivers', 'actNow', 'baseRate', 'flagReason', 'chronic', 'stateMix'],
  // eslint-disable-next-line no-unused-vars
  STATE_MIX_ON_DEMAND: [],
  evidence: ['confusion', 'sweep', 'scorecard'],
};

const ALL_KEYS = Object.keys(FEED_FN);

const EMPTY_FEED = { rows: [], loading: true, error: null };

function useFeeds(city) {
  const [feeds, setFeeds] = useState(
    () => Object.fromEntries(ALL_KEYS.map((k) => [k, { rows: [], loading: false, error: null, idle: true }]))
  );
  const [wanted, setWanted] = useState([]);
  const [nonce, setNonce] = useState(0);

  // IN-FLIGHT FETCHES SURVIVE A TAB SWITCH.                 04-Aug-2026
  //
  // The fetch effect below depends on `wanted`, and `wanted` grows every time
  // the reader opens a sub-tab. React runs the previous effect's cleanup
  // first, so the old cleanup was aborting requests that were still in the
  // air -- requests already marked loading. Nothing ever resolved them, so
  // those panels spun forever. That is what "the chart is not working" was:
  // not an empty result, a request that was cancelled and never retried.
  //
  // Liveness now tracks the COMPONENT, not the effect run. Only unmount or a
  // change of city stops a fetch; opening another tab does not.
  const aliveRef = useRef(true);
  useEffect(() => {
    aliveRef.current = true;
    return () => { aliveRef.current = false; };
  }, [city]);

  const request = useCallback((keys) => {
    setWanted((prev) => {
      const add = keys.filter((k) => !prev.includes(k));
      return add.length ? [...prev, ...add] : prev;
    });
  }, []);

  useEffect(() => {
    const todo = wanted.filter((k) => feeds[k] && feeds[k].idle);
    if (!todo.length) return undefined;

    setFeeds((s) => {
      const next = { ...s };
      todo.forEach((k) => { next[k] = { rows: [], loading: true, error: null, idle: false }; });
      return next;
    });

    // TWO at a time. Measured: xw-state-mix returns in 13.5s every time when
    // it is the only request in flight, and fails outright when it is sharing
    // the pipe with five others. Piling more on makes every query slower, not
    // the set faster -- past three, they start exceeding API Gateway's 30s cap.
    let cursor = 0;
    const worker = async () => {
      for (;;) {
        const i = cursor;
        cursor += 1;
        if (i >= todo.length || !aliveRef.current) return;
        const key = todo[i];
        let rows = [];
        let error = null;
        try { rows = (await FEED_FN[key](city)) || []; }
        catch (e) { error = String((e && e.message) || e); }
        if (!aliveRef.current) return;
        setFeeds((s) => ({ ...s, [key]: { rows, loading: false, error, idle: false } }));
      }
    };
    Promise.all([worker(), worker()]);
    return undefined;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [wanted, city, nonce]);

  // Re-arm a single feed. Used by the per-panel Retry button: on its own a
  // slow query almost always succeeds, so one manual retry beats an automatic
  // one that just adds load at the worst moment.
  const refetch = useCallback((key) => {
    setFeeds((s) => ({ ...s, [key]: { rows: [], loading: false, error: null, idle: true } }));
    setNonce((n) => n + 1);
  }, []);

  // Reset clears the cache so everything already requested is refetched.
  const refresh = useCallback(() => {
    setFeeds((s) => {
      const next = { ...s };
      Object.keys(next).forEach((k) => { next[k] = { rows: [], loading: false, error: null, idle: true }; });
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
        <span style={{ display: 'inline-flex', alignItems: 'center', gap: 8, color: STATUS.warning.fill, fontSize: 14 }}>
          <TriangleAlert size={15} /> This one is slow to build and timed out.
        </span>
        {onRetry && (
          <button type="button" onClick={onRetry}
                  style={{ display: 'inline-flex', alignItems: 'center', gap: 6, border: `1px solid ${LINE}`, background: '#FFF', color: INK_2, borderRadius: 9, padding: '6px 13px', fontSize: 13.5, fontWeight: 600, cursor: 'pointer' }}>
            <RefreshCw size={13} /> Try again
          </button>
        )}
      </div>
    );
  }
  if (!feed.rows.length) return <Empty height={height}>{empty}</Empty>;
  return children;
}


// ---------------------------------------------------------------------
// The work list is 200-per-fleet by construction. These panels describe THAT
// shortlist and say so -- they are not fleet statistics, and the caption on
// each one repeats it, because a chart above a table gets read as the whole
// population unless it is told otherwise.
// ---------------------------------------------------------------------
function WorkListVisuals({ rows, feed, chronic, onAnalyse }) {
  const list = useMemo(() => (rows || []).map((r) => ({
    ...r,
    p: Number(r.failure_probability),
    thr: Number(r.decision_threshold),
  })).filter((r) => Number.isFinite(r.p)), [rows]);

  // Risk mix inside the shortlist, by fleet.
  const mix = useMemo(() => {
    const m = {};
    list.forEach((r) => {
      const t = String(r.device_category || '').toUpperCase();
      const b = riskBand(r.p);
      m[t] = m[t] || { fleet: deviceShort(t), critical: 0, serious: 0, warning: 0, good: 0 };
      m[t][b] += 1;
    });
    return Object.values(m);
  }, [list]);

  // Depots carrying the most shortlisted devices.
  const depots = useMemo(() => {
    const m = {};
    list.forEach((r) => {
      const k = String(r.facility_id ?? '--');
      m[k] = (m[k] || 0) + 1;
    });
    return Object.entries(m).map(([name, value]) => ({ name: `Depot ${name}`, value }))
      .sort((a, b) => b.value - a.value).slice(0, 10);
  }, [list]);

  // Chronic offenders: total days out against number of separate spells. A
  // device high on both has repeated, long outages; high on days but low on
  // spells is one long outage and a different conversation.
  const chron = useMemo(() => (chronic.rows || []).map((r) => ({
    name: r.device_id, id: r.device_id, t: r.device_type,
    spells: Number(r.n_spells) || 0,
    days: Number(r.total_days_out) || 0,
    longest: Number(r.longest_spell) || 0,
  })).filter((r) => r.spells || r.days).slice(0, 200), [chronic.rows]);

  if (!list.length) return null;

  return (
    <Grid cols="repeat(auto-fit,minmax(400px,1fr))" style={{ marginTop: 14 }}>
      <Panel title="Risk mix inside the shortlist"
             hint="Bands within this list only. Every device here is already above its fleet's alert line.">
        <ColumnBars
          data={mix} xKey="fleet" stacked height={260}
          series={[
            { key: 'critical', label: 'Critical', color: STATUS.critical.fill },
            { key: 'serious', label: 'High', color: STATUS.serious.fill },
            { key: 'warning', label: 'Medium', color: STATUS.warning.fill },
            { key: 'good', label: 'Low', color: STATUS.good.fill },
          ]}
          fmt={(v) => nfmt(v)}
        />
      </Panel>

      <Panel title="Where the shortlisted devices sit"
             hint="Top ten depots by shortlisted device count. A depot's share of the shortlist, not its failure rate.">
        <RankBars
          data={depots} xKey="value" yKey="name" height={260}
          color={CAT[0]} fmt={(v) => nfmt(v)} unit="Devices"
        />
      </Panel>

      <Panel title="Repeat offenders: how often against how long"
             hint="Separate outage spells on the horizontal axis, total days out on the vertical. Top right is a device that fails often AND stays down."
             style={{ gridColumn: '1 / -1' }}>
        {chron.length ? (
          <>
            <Bubble
              data={chron} xKey="spells" yKey="days" zKey="longest"
              xLabel="Separate outage spells" yLabel="Total days out"
              nameKey="name" height={330}
              colorBy={(d) => deviceColor(d.t)}
              onDrill={(d) => d && d.id && onAnalyse && onAnalyse(d.id)}
            />
            <Note>
              Bubble size is the longest single spell. A large bubble low and left is one long
              outage, not a chronic device -- the two need different responses and the chart
              separates them. Click a bubble to open the device.
            </Note>
          </>
        ) : <Empty height={300}>The chronic-device feed returned no rows.</Empty>}
      </Panel>
    </Grid>
  );
}

export default function PS1Overview({ city = 'CHI' }) {
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
  const [bands, setBands] = useState([]);
  const [analyse, setAnalyse] = useState(null);
  // Ask for this tab's feeds the first time it is shown, and nothing more.
  useEffect(() => { request(VIEW_FEEDS[view] || []); }, [view, request]);

  // The search index costs 5 further calls, one of them a 5,000-row list and
  // one of them /ps1/predictions at 13s+. It is built ON FIRST FOCUS of the
  // search box and not before -- nobody searches in the first second, and
  // racing it against the fleet roll-up is what starved the opening screen.
  const [searchWanted, setSearchWanted] = useState(false);
  const { index, ready } = useSearchIndex(BASE, city, searchWanted);

  const typeOn = useCallback(
    (t) => !types.length || types.includes(String(t || '').toUpperCase()),
    [types]
  );

  // ---- FLEET TOTALS. station-summary only. Never predictions. --------
  const stationRows = useMemo(() => {
    const rows = F.stations.rows || [];
    return scope.facility_id ? rows.filter((r) => String(r.facility_id) === String(scope.facility_id)) : rows;
  }, [F.stations.rows, scope.facility_id]);

  const fleet = useMemo(() => {
    const t = stationRows.reduce((a, r) => ({
      devices: a.devices + (Number(r.total_devices) || 0),
      flagged: a.flagged + (Number(r.predicted_failures) || 0),
      critical: a.critical + (Number(r.critical_count) || 0),
      high: a.high + (Number(r.high_count) || 0),
      medium: a.medium + (Number(r.medium_count) || 0),
    }), { devices: 0, flagged: 0, critical: 0, high: 0, medium: 0 });
    t.lower = Math.max(0, t.devices - t.critical - t.high - t.medium);
    t.depots = stationRows.length;
    return t;
  }, [stationRows]);

  const scoredDate = (stationRows[0] && stationRows[0].last_inference_date) || null;

  // ---- WORK LIST. predictions is a top-200-per-type slice. -----------
  const workList = useMemo(() => (F.predictions.rows || []).filter((r) => {
    if (!typeOn(r.device_category)) return false;
    if (scope.device_type && String(r.device_category).toUpperCase() !== String(scope.device_type).toUpperCase()) return false;
    if (scope.facility_id && String(r.facility_id) !== String(scope.facility_id)) return false;
    if (scope.device_id && String(r.device_id) !== String(scope.device_id)) return false;
    if (bands.length && !bands.includes(riskBand(r.failure_probability))) return false;
    return true;
  }), [F.predictions.rows, typeOn, scope, bands]);

  const lift = useMemo(() => {
    const out = [];
    TYPES.forEach((t) => {
      const rows = (F.tiers.rows || []).filter((r) => String(r.device_type).toUpperCase() === t);
      if (!rows.length) return;
      const tot = rows.reduce((a, r) => a + Number(r.n_rows || 0), 0);
      const pos = rows.reduce((a, r) => a + Number(r.n_positive || 0), 0);
      const base = tot ? pos / tot : 0;
      const top = rows.find((r) => String(r.ps1_risk_tier).toUpperCase() === 'CRITICAL')
        || rows.slice().sort((a, b) => Number(b.positive_rate) - Number(a.positive_rate))[0];
      const topRate = Number(top && top.positive_rate) || 0;
      out.push({ name: deviceShort(t), type: t, value: base ? Number((topRate / base).toFixed(3)) : 0 });
    });
    return out;
  }, [F.tiers.rows]);

  const depots = useMemo(() => stationRows
    .map((s) => ({
      facility_id: String(s.facility_id),
      name: s.facility_name || `Facility ${s.facility_id}`,
      operator: s.operator_name,
      devices: Number(s.total_devices) || 0,
      flagged: Number(s.predicted_failures) || 0,
      critical: Number(s.critical_count) || 0,
      high: Number(s.high_count) || 0,
      medium: Number(s.medium_count) || 0,
      avg_risk_pct: Number(s.avg_risk_pct) || 0,
    }))
    .sort((a, b) => b.flagged - a.flagged || b.devices - a.devices), [stationRows]);

  const comps = useMemo(() => {
    const ids = new Set(workList.map((r) => r.device_id));
    const rows = F.serials.rows || [];
    return ids.size ? rows.filter((s) => ids.has(s.device_id)) : rows;
  }, [F.serials.rows, workList]);

  const drivers = useMemo(() => {
    const t = scope.device_type || (types.length === 1 ? types[0] : null);
    return (F.drivers.rows || [])
      .filter((r) => !t || String(r.device_type).toUpperCase() === String(t).toUpperCase())
      .map((r) => ({
        name: String(r.feature_name || '').replace(/_/g, ' '),
        value: Math.abs(Number(r.mean_abs_shap) || 0),
        signed: Number(r.mean_signed_shap) || 0,
      }))
      .sort((a, b) => b.value - a.value)
      .slice(0, 10);
  }, [F.drivers.rows, scope.device_type, types]);

  // The risk-tier ladder. If the model ranks meaningfully, the observed
  // failure rate falls monotonically from CRITICAL to LOW. GATE runs
  // 95.8 / 82.1 / 33.9 / 21.1 -- a clean ladder, and far more convincing
  // than any single accuracy figure.
  const tierLadder = useMemo(() => {
    const order = ['CRITICAL', 'HIGH', 'MEDIUM', 'LOW'];
    const rows = (F.tiers.rows || []).filter((r) => typeOn(r.device_type));
    return order.map((tier) => {
      const o = { name: tier.charAt(0) + tier.slice(1).toLowerCase() };
      TYPES.forEach((t) => {
        const hit = rows.find((r) => String(r.device_type).toUpperCase() === t
          && String(r.ps1_risk_tier).toUpperCase() === tier);
        if (hit) o[deviceShort(t)] = Number((Number(hit.positive_rate) * 100).toFixed(1));
      });
      return o;
    }).filter((o) => Object.keys(o).length > 1);
  }, [F.tiers.rows, typeOn]);

  // Being inside a fault chain against being outside one. This is the
  // clearest cross-PS statement Failure Prediction can make and it differs enormously by
  // fleet: validators 99.2% vs 8.1%, TVMs 95.9% vs 86.8%.
  const cascade = useMemo(() => (F.causation.rows || [])
    .filter((r) => typeOn(r.device_type))
    .map((r) => ({
      name: deviceShort(r.device_type),
      'In a fault chain': Number((Number(r.critical_rate_in_chain) * 100).toFixed(1)),
      'Not in a chain': Number((Number(r.critical_rate_no_chain) * 100).toFixed(1)),
      lift: Number(r.critical_lift) || 0,
    })), [F.causation.rows, typeOn]);

  // How often a device ACTUALLY starts failing, as opposed to how often the
  // stored label says "failure". inflation_factor is the gap between the two
  // and it is the reason accuracy is not the headline anywhere on this screen.
  const baseRate = useMemo(() => (F.baseRate.rows || [])
    .filter((r) => typeOn(r.device_type))
    .map((r) => ({
      name: deviceShort(r.device_type),
      value: Number(r.onsets) || 0,
      onsetRate: Number(r.onset_rate_eligible) || 0,
      storedRate: Number(r.stored_base_rate) || 0,
      inflation: Number(r.inflation_factor) || 0,
      type: r.device_type,
    })), [F.baseRate.rows, typeOn]);

  const trend = useMemo(() => {
    const byDate = {};
    (F.trend.rows || []).forEach((r) => {
      if (!typeOn(r.device_category)) return;
      if (scope.device_type && String(r.device_category).toUpperCase() !== String(scope.device_type).toUpperCase()) return;
      const k = String(r.date).slice(0, 10);
      byDate[k] = byDate[k] || { date: dfmt(k).replace(/ \d{4}$/, ''), failures: 0 };
      byDate[k].failures += Number(r.failures) || 0;
    });
    return Object.values(byDate);
  }, [F.trend.rows, typeOn, scope.device_type]);

  const conf = useMemo(() => (F.confusion.rows || []).map((c) => {
    const tp = Number(c.tp) || 0, fp = Number(c.fp) || 0, fn = Number(c.fn) || 0;
    return {
      name: deviceShort(c.device_category), type: c.device_category,
      precision: tp + fp ? tp / (tp + fp) : 0,
      recall: tp + fn ? tp / (tp + fn) : 0,
    };
  }), [F.confusion.rows]);

  // Accuracy is computed HERE from the confusion counts rather than read from
  // a column, because no route publishes it -- and because computing it in
  // view makes the denominator visible. On a fleet that is 99% healthy,
  // accuracy is dominated by true negatives and is the least informative
  // number on the page; it is shown because it was asked for, next to the
  // three that actually decide whether a flag is worth acting on.
  // STATED ACCURACY OVERRIDE -- read this before trusting the number.
  //
  // The gate figure below is NOT the one computed from /ps1/confusion, which
  // yields 87.0%. It is a stated value carried at explicit request. It is
  // flagged on screen as stated rather than computed, because a metric that
  // silently disagrees with its own source is how a dashboard stops being
  // evidence.
  //
  // ITS END CONDITION IS ON SCREEN, NOT IN A TICKET (PK, 04-Aug): the override
  // stands until the models are re-run on data past 11 Apr 2026. That is the
  // same event that retires every other 11-Apr-bounded figure in the estate,
  // so there is one date to watch rather than a private one for this cell.
  //
  // Delete this map to go back to the computed figure. Nothing else depends
  // on it.
  const ACCURACY_OVERRIDE = { GATE: 0.901 };

  const scorecard = useMemo(() => {
    const acc = {};
    (F.confusion.rows || []).forEach((c) => {
      const tp = Number(c.tp) || 0, fp = Number(c.fp) || 0;
      const fn = Number(c.fn) || 0, tn = Number(c.tn) || 0;
      const n = tp + fp + fn + tn;
      acc[String(c.device_category).toUpperCase()] = {
        accuracy: n ? (tp + tn) / n : null,
        n, tp, fp, fn, tn,
      };
    });
    return (F.scorecard.rows || []).map((r) => {
      const t = String(r.device || '').toUpperCase();
      const a = acc[t] || {};
      return {
        type: t,
        fleet: deviceShort(t),
        model: r.model,
        champion: r.is_champion === true || r.is_champion === 'true',
        auc: r.auc === null || r.auc === undefined ? null : Number(r.auc),
        ap: r.ap === null || r.ap === undefined ? null : Number(r.ap),
        f1: r.f1 === null || r.f1 === undefined ? null : Number(r.f1),
        precision: r.prec === null || r.prec === undefined ? null : Number(r.prec),
        recall: r.rec === null || r.rec === undefined ? null : Number(r.rec),
        accuracy: ACCURACY_OVERRIDE[t] !== undefined
          ? ACCURACY_OVERRIDE[t]
          : (a.accuracy === undefined ? null : a.accuracy),
        accuracy_stated: ACCURACY_OVERRIDE[t] !== undefined,
        accuracy_computed: a.accuracy === undefined ? null : a.accuracy,
        n_scored: a.n || null,
        verdict: r.verdict || r.note || null,
      };
    })
      // Champions only. The candidate rows are still published on
      // /ps1/leaderboard for anyone who wants them; the scorecard answers
      // "how good is the model we shipped", and fifteen rows answered a
      // different question.
      .filter((r) => r.champion)
      .sort((x, y) => String(x.fleet).localeCompare(String(y.fleet)));
  }, [F.scorecard.rows, F.confusion.rows]);

  const onSearchPick = useCallback((e) => {
    if (e.scope.device_id) { setAnalyse(e.scope.device_id); return; }
    if (e.scope.facility_id) { push(e.label, { facility_id: e.scope.facility_id }); setView('depots'); }
    else if (e.scope.device_type) { push(deviceShort(e.scope.device_type), { device_type: e.scope.device_type }); setView('devices'); }
  }, [push]);

  if (!BASE) {
    return <Card><Empty height={160}>No API base URL is configured for this build (VITE_API_BASE_URL).</Empty></Card>;
  }

  const funnel = [
    { name: 'Devices in service', value: fleet.devices, fill: CAT[0] },
    { name: 'Above alert threshold', value: fleet.flagged, fill: CAT[2] },
    { name: 'Critical band', value: fleet.critical, fill: STATUS.critical.fill },
  ];
  const riskMix = [
    { name: 'Critical', value: fleet.critical },
    { name: 'High', value: fleet.high },
    { name: 'Medium', value: fleet.medium },
    { name: 'Lower', value: fleet.lower },
  ].filter((d) => d.value > 0);
  const mixColor = (d) => ({ Critical: STATUS.critical.fill, High: STATUS.serious.fill, Medium: STATUS.warning.fill, Lower: STATUS.good.fill }[d.name] || INK_3);

  return (
    <div>
      {/* ---- one control strip. There is no second filter bar. ------- */}
      <Card pad="12px 14px" style={{ marginBottom: 14 }}>
        <div style={{ display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap' }}>
          <GlobalSearch index={index} ready={ready} onPick={onSearchPick}
                        onActivate={() => setSearchWanted(true)} />
          <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
            {TYPES.map((t) => (
              <Chip key={t} color={deviceColor(t)} active={types.includes(t)}
                    onClick={() => setTypes((s) => (s.includes(t) ? s.filter((x) => x !== t) : [...s, t]))}>
                {deviceShort(t)}
              </Chip>
            ))}
          </div>
          <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
            {BANDS.map((b) => (
              <Chip key={b} color={STATUS[b].fill} active={bands.includes(b)}
                    onClick={() => setBands((s) => (s.includes(b) ? s.filter((x) => x !== b) : [...s, b]))}>
                {STATUS[b].label}
              </Chip>
            ))}
          </div>
          <button type="button" onClick={() => { setTypes([]); setBands([]); reset(); refresh(); }}
                  style={{ marginLeft: 'auto', display: 'inline-flex', alignItems: 'center', gap: 6, border: `1px solid ${LINE}`, background: '#FFF', color: INK_2, borderRadius: 9, padding: '7px 12px', fontSize: 13.5, fontWeight: 600, cursor: 'pointer' }}>
            <RefreshCw size={13} /> Reset
          </button>
        </div>
      </Card>

      {/* ---- sub-tabs ------------------------------------------------ */}
      {/* The stylesheet is mounted by V2Shell, but these tabs also serve their own
          standalone routes (/v2/ps1 and friends), where nothing else mounts it.
          Duplicate <style> tags are identical rules and are harmless. */}
      <V2Style />

      {/* Sub-tabs. Each one carries its own colour off the Failure Prediction rotation of the
          nav ramp, and the rail underneath is Failure Prediction's own colour -- so the row
          identifies both which sub-tab is open and which problem statement it
          belongs to. See theme.js navColor() for the rotation. */}
      <Tabs items={VIEWS} value={view} onChange={setView} variant="sub" parent="ps1" />

      <Breadcrumb />

      {/* ================= FLEET STATUS ============================== */}
      {view === 'overview' && (
        <>
          <Section accent={TAB_COLOR.ps1} eyebrow="Where to send an engineer" title="Fleet status"
                   sub={F.stations.loading
                     ? 'Loading the current scoring run.'
                     : `Scored ${dfmt(scoredDate)} across ${nfmt(fleet.depots)} depots. A device is flagged when its failure probability passes the alert threshold set for its own device type.`}>
            <Grid cols="minmax(300px,1.1fr) minmax(320px,1fr)" style={{ marginBottom: 14 }}>
              {F.stations.loading ? (
                <Card><Loading height={140} label="Loading the fleet" /></Card>
              ) : (
                <Hero accent={TAB_COLOR.ps1} label="Devices needing a work order"
                      value={nfmt(fleet.flagged)}
                      unit={`of ${nfmt(fleet.devices)} in service`}
                      sub={`${nfmt(fleet.critical)} are in the critical band. Counts are whole-fleet; the device-type filter applies to the work list and the tabs below.`} />
              )}
              <Panel title="From fleet to work order" hint="Each stage is a subset of the one above it.">
                <Feed feed={F.stations} onRetry={() => refetch('stations')} height={190}>
                  <FunnelView data={funnel} height={190} />
                </Feed>
              </Panel>
            </Grid>

            <Feed feed={F.stations} onRetry={() => refetch('stations')} height={90}>
              <Grid cols="repeat(auto-fit,minmax(190px,1fr))">
                <Stat label="Devices in service" value={nfmt(fleet.devices)} tone="neutral" foot={`${nfmt(fleet.depots)} depots`} />
                <Stat label="Needing a work order" value={nfmt(fleet.flagged)} tone="warning"
                      foot={fleet.devices ? `${((fleet.flagged / fleet.devices) * 100).toFixed(0)}% of the fleet` : null} />
                <Stat label="Critical band" value={nfmt(fleet.critical)} tone="critical" foot="Highest urgency" />
                <Stat label="Repeat offenders" value={nfmt((F.chronic.rows || []).length)} tone="serious"
                      foot="Out of service more than once" onClick={() => setView('drivers')} />
              </Grid>
            </Feed>
          </Section>

          <Section accent={TAB_COLOR.ps1} eyebrow="Macro" title="How the three fleets compare"
                   sub="Fare gates, ticket machines and bus validators fail at very different rates, so each has its own alert threshold. Comparing raw percentages between them is misleading; comparing lift is not.">
            <Grid cols="repeat(auto-fit,minmax(320px,1fr))">
              <Panel title="Risk mix across the fleet" hint="Every device in service sits in exactly one band.">
                <Feed feed={F.stations} onRetry={() => refetch('stations')} height={220}>
                  <Donut data={riskMix} height={220} colors={mixColor}
                         centerValue={nfmt(fleet.devices)} centerLabel="devices" />
                </Feed>
              </Panel>

              <Panel title="How much better than chance"
                     hint="A lift of 2.5 means a device in the top risk tier fails 2.5 times more often than that fleet's average.">
                <Feed feed={F.tiers} onRetry={() => refetch('tiers')} height={150}>
                  <RankBars data={lift} xKey="value" yKey="name" height={150} unit="Lift"
                            colorBy={(d) => deviceColor(d.type)} fmt={(v) => `${Number(v).toFixed(2)}x`}
                            onDrill={(row) => row && push(row.name, { device_type: row.type })} />
                </Feed>
                <Note>Lift near 1.0 means the ranking is no better than picking at random for that fleet, whatever its accuracy score says.</Note>
              </Panel>

              <Panel title="Failures over time" hint="Recorded failures against devices in service, by day.">
                <Feed feed={F.trend} height={220}>
                  <Trend data={trend} xKey="date" area height={220}
                         series={[{ key: 'failures', label: 'Failures', color: STATUS.critical.fill }]} />
                </Feed>
              </Panel>
            </Grid>
          </Section>
        </>
      )}

      {/* ================= DEPOTS ==================================== */}
      {view === 'depots' && (
        <Section accent={TAB_COLOR.ps1} eyebrow="Where the work is" title="Depots and stations"
                 sub="Real device counts per depot. Click any depot to narrow every other tab to it.">
          <Grid cols="minmax(340px,1.1fr) minmax(320px,1fr)" style={{ marginBottom: 14 }}>
            <Panel title="Depots by devices needing a work order" hint="Click a bar to drill into that depot.">
              <Feed feed={F.stations} onRetry={() => refetch('stations')} height={320}>
                <RankBars data={depots.slice(0, 12).map((d) => ({ name: d.name, value: d.flagged, facility_id: d.facility_id }))}
                          xKey="value" yKey="name" height={320} color={CAT[0]} unit="Flagged"
                          onDrill={(row) => row && push(row.name, { facility_id: row.facility_id })} />
              </Feed>
            </Panel>
            <Panel title="Fleet size by depot" hint="Tile area is the number of devices; click to drill in.">
              <Feed feed={F.stations} onRetry={() => refetch('stations')} height={320}>
                <TreemapChart data={depots.slice(0, 24).map((d) => ({ name: d.name, value: d.devices, facility_id: d.facility_id }))}
                              height={320} colors={(d, i) => CAT[i % CAT.length]}
                              onDrill={(row) => row && row.facility_id && push(row.name, { facility_id: row.facility_id })} />
              </Feed>
            </Panel>
          </Grid>

          <Panel title="Risk mix by depot" hint="The twelve depots carrying the most flagged devices.">
            <Feed feed={F.stations} onRetry={() => refetch('stations')} height={260}>
              <ColumnBars data={depots.slice(0, 12).map((d) => ({ name: d.name, Critical: d.critical, High: d.high, Medium: d.medium }))}
                          xKey="name" stacked height={260}
                          series={[
                            { key: 'Critical', label: 'Critical', color: STATUS.critical.fill },
                            { key: 'High', label: 'High', color: STATUS.serious.fill },
                            { key: 'Medium', label: 'Medium', color: STATUS.warning.fill },
                          ]} />
            </Feed>
          </Panel>

          <div style={{ marginTop: 14 }}>
            <Feed feed={F.stations} onRetry={() => refetch('stations')} height={200}>
              <DataTable rows={depots} height={360} pageSize={100} exportName={`ps1-depots-${city}`}
                         onRowClick={(r) => push(r.name, { facility_id: r.facility_id })}
                         columns={[
                           { key: 'name', label: 'Depot' },
                           { key: 'operator', label: 'Operator', width: 150 },
                           { key: 'devices', label: 'Devices', num: true, width: 90 },
                           { key: 'flagged', label: 'Flagged', num: true, width: 90 },
                           { key: 'critical', label: 'Critical', num: true, width: 90 },
                           { key: 'avg_risk_pct', label: 'Average risk', num: true, width: 120, d: 1, render: (r) => `${r.avg_risk_pct.toFixed(1)}%` },
                         ]} />
            </Feed>
          </div>
        </Section>
      )}

      {/* ================= DEVICES =================================== */}
      {view === 'devices' && (
        <Section accent={TAB_COLOR.ps1} eyebrow="The work list" title="Highest-risk devices"
                 sub="The 200 highest-scoring devices in each fleet, ranked by failure probability. Click a row for the full cross-check and to raise a work order.">
          <Note>
            This is a ranked shortlist, not the whole fleet - every device here is above its alert threshold by
            construction. Fleet totals are on the Fleet status tab.
          </Note>
          <WorkListVisuals rows={workList} feed={F.predictions} chronic={F.chronic} onAnalyse={setAnalyse} />

          <div style={{ marginTop: 14 }}>
            <Feed feed={F.predictions} onRetry={() => refetch('predictions')} height={300} empty="The scoring feed did not return in time. Press Reset to retry.">
              <DataTable
                rows={workList.slice().sort((a, b) => Number(b.failure_probability) - Number(a.failure_probability))}
                height={520} pageSize={250} exportName={`ps1-worklist-${city}`}
                onRowClick={(r) => setAnalyse(r.device_id)}
                rowKey={(r, i) => r.prediction_id || `${r.device_id}-${i}`}
                columns={[
                  { key: 'device_id', label: 'Device', width: 130, render: (r) => <span style={{ fontFamily: 'ui-monospace,monospace', fontWeight: 700, color: INK }}>{r.device_id}</span> },
                  { key: 'device_category', label: 'Fleet', width: 150, render: (r) => (
                    <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
                      <span style={{ width: 8, height: 8, borderRadius: 4, background: deviceColor(r.device_category) }} />
                      {deviceShort(r.device_category)}
                    </span>) },
                  { key: 'facility_id', label: 'Depot', width: 100 },
                  { key: 'failure_probability', label: 'Risk', num: true, width: 90, render: (r) => (
                    <span style={{ fontWeight: 700, color: STATUS[riskBand(r.failure_probability)].fill, ...font.num }}>{pct(r.failure_probability)}</span>) },
                  { key: 'decision_threshold', label: 'Alerts above', num: true, width: 120, d: 3 },
                  { key: 'predicted_label', label: 'Status', width: 130, render: (r) => (
                    <Badge tone={riskBand(r.failure_probability)}>{STATUS[riskBand(r.failure_probability)].label}</Badge>) },
                ]} />
            </Feed>
          </div>
        </Section>
      )}

      {/* ================= COMPONENTS ================================ */}
      {view === 'components' && (
        <Section accent={TAB_COLOR.ps1} eyebrow="Which part carries the risk" title="Components"
                 sub="The fitted components on the shortlisted devices, and how old they are.">
          <Grid cols="minmax(340px,1fr) minmax(340px,1fr)" style={{ marginBottom: 14 }}>
            <Panel title="Component age against device risk"
                   hint="Bubble size is how much of the device's risk this component accounts for.">
              <Feed feed={F.serials} onRetry={() => refetch('serials')} height={320}>
                <Bubble
                  data={comps.filter((s) => Number(s.component_age_days) > 0).slice(0, 400).map((s) => ({
                    ...s,
                    age: Number(s.component_age_days) || 0,
                    risk: (Number(s.device_failure_probability) || 0) * 100,
                    w: Math.max(1, (Number(s.attribution_weight) || 0) * 100),
                  }))}
                  xKey="age" yKey="risk" zKey="w" nameKey="matched_serial_nbr"
                  xLabel="Component age (days)" yLabel="Device risk (%)" height={320}
                  colorBy={(d) => deviceColor(d.device_category)}
                  onDrill={(d) => d && d.device_id && setAnalyse(d.device_id)} />
              </Feed>
            </Panel>
            <Panel title="What is fitted across the fleet" hint="Component count and how many devices carry each part.">
              <Feed feed={F.components} height={320}>
                <RankBars data={(F.components.rows || []).map((c) => ({ name: c.component_description, value: Number(c.n_components) || 0, cat: c.device_category }))}
                          xKey="value" yKey="name" height={320} unit="Components"
                          colorBy={(d) => deviceColor(d.cat)} />
              </Feed>
            </Panel>
          </Grid>

          <Feed feed={F.serials} onRetry={() => refetch('serials')} height={200}>
            <DataTable
              rows={comps.slice().sort((a, b) => Number(b.attribution_weight) - Number(a.attribution_weight))}
              height={400} pageSize={250} exportName={`ps1-components-${city}`}
              onRowClick={(r) => setAnalyse(r.device_id)}
              columns={[
                { key: 'matched_serial_nbr', label: 'Serial', width: 180, render: (r) => <span style={{ fontFamily: 'ui-monospace,monospace' }}>{r.matched_serial_nbr}</span> },
                { key: 'device_id', label: 'Device', width: 120, render: (r) => <span style={{ fontFamily: 'ui-monospace,monospace', fontWeight: 700, color: INK }}>{r.device_id}</span> },
                { key: 'component_type', label: 'Part' },
                { key: 'device_category', label: 'Fleet', width: 140, render: (r) => deviceShort(r.device_category) },
                { key: 'component_age_days', label: 'Age (days)', num: true, width: 110 },
                { key: 'attribution_weight', label: 'Share of risk', num: true, width: 120, render: (r) => pct(r.attribution_weight) },
              ]} />
          </Feed>
        </Section>
      )}

      {/* ================= WHY ======================================= */}
      {view === 'drivers' && (
        <Section accent={TAB_COLOR.ps1} eyebrow="What moves the prediction" title="Drivers and fleet condition"
                 sub="The measurements the model leans on most, and how the fleet's condition is distributed.">
          <Grid cols="minmax(340px,1fr) minmax(340px,1fr)">
            <Panel title="Strongest drivers" hint="Bar length is how much a measurement moves the prediction; colour shows which way.">
              <Feed feed={F.drivers} onRetry={() => refetch('drivers')} height={200} empty="No driver rows published for this fleet in the current run.">
                <RankBars data={drivers} xKey="value" yKey="name" height={Math.max(200, drivers.length * 30)}
                          unit="Influence" colorBy={(d) => (d.signed >= 0 ? STATUS.critical.fill : CAT[0])}
                          fmt={(v) => Number(v).toFixed(3)} />
              </Feed>
              <Note>Red pushes the prediction towards failure, blue away from it.</Note>
            </Panel>

            {/* ON DEMAND. /ps1/xw-state-mix takes ~13.5s -- by far the slowest
                route on the screen -- and it was loading with the other six
                panels on a two-worker pool, so the whole Why tab waited on the
                panel readers scroll to least. It now loads when asked for. */}
            <Panel title="Condition by risk tier" hint="Device-days in each combination of risk tier and current state."
                   right={F.stateMix.idle ? (
                     <button type="button" onClick={() => request(['stateMix'])}
                             style={{ border: `1px solid ${LINE}`, background: '#FFF', color: INK_2,
                                      borderRadius: 9, padding: '5px 11px', fontSize: 13.5,
                                      fontWeight: 600, cursor: 'pointer' }}>
                       Load this panel
                     </button>
                   ) : null}>
              {F.stateMix.idle ? (
                <Empty height={200}>
                  This one takes about 13 seconds to build, so it is not loaded with the rest of the
                  tab. Press Load this panel when you want it.
                </Empty>
              ) : (
              <Feed feed={F.stateMix} onRetry={() => refetch('stateMix')} height={200}>
                <Matrix rows={(F.stateMix.rows || []).filter((r) => typeOn(r.device_type) && (!scope.device_type || String(r.device_type).toUpperCase() === String(scope.device_type).toUpperCase()))}
                        rowKey="ps1_risk_tier" colKey="device_state" valKey="n_devices"
                        rowLabel="Risk tier" fmt={(v) => nfmt(v)} />
              </Feed>
              )}
            </Panel>

            <Panel title="Repeat offenders" hint="Devices that have been out of service more than once.">
              <Feed feed={F.chronic} onRetry={() => refetch('chronic')} height={220}>
                <DataTable rows={(F.chronic.rows || []).filter((r) => typeOn(r.device_type))}
                           height={300} pageSize={100} searchable={false}
                           onRowClick={(r) => setAnalyse(r.device_id)}
                           columns={[
                             { key: 'device_id', label: 'Device', width: 130, render: (r) => <span style={{ fontFamily: 'ui-monospace,monospace', fontWeight: 700, color: INK }}>{r.device_id}</span> },
                             { key: 'device_type', label: 'Fleet', width: 130, render: (r) => deviceShort(r.device_type) },
                             { key: 'n_spells', label: 'Outages', num: true, width: 90 },
                             { key: 'total_days_out', label: 'Days out', num: true, width: 100 },
                             { key: 'longest_spell', label: 'Longest', num: true, width: 90 },
                           ]} />
              </Feed>
            </Panel>

            <Panel title="When the alert fired" hint="Whether a flag landed on the day a fault began, during an existing outage, or with no recorded event.">
              <Feed feed={F.flagReason} onRetry={() => refetch('flagReason')} height={240}>
                <ColumnBars
                  data={(F.flagReason.rows || []).filter((r) => typeOn(r.device_type)).map((r) => ({
                    name: deviceShort(r.device_type),
                    'On the day it failed': Number(r.flag_on_onset) || 0,
                    'During an outage': Number(r.flag_during_spell) || 0,
                    'No recorded event': Number(r.flag_no_event) || 0,
                  }))}
                  xKey="name" stacked height={240}
                  series={[
                    { key: 'On the day it failed', label: 'On the day it failed', color: STATUS.good.fill },
                    { key: 'During an outage', label: 'During an outage', color: CAT[4] },
                    { key: 'No recorded event', label: 'No recorded event', color: INK_3 },
                  ]} />
              </Feed>
            </Panel>

            <Panel title="Does the risk ranking hold?"
                   hint="Observed failure rate in each risk tier. A ladder that falls left to right means the ranking is doing real work.">
              <Feed feed={F.tiers} onRetry={() => refetch('tiers')} height={240}>
                <ColumnBars data={tierLadder} xKey="name" height={240} fmt={(v) => `${v}%`}
                            series={TYPES.map((t) => ({ key: deviceShort(t), label: deviceShort(t), color: deviceColor(t) }))} />
              </Feed>
              <Note>Read down each tier, not across fleets - each fleet has its own alert threshold.</Note>
            </Panel>

            <Panel title="Does a fault cascade change the risk?"
                   hint="Share of devices in the critical band when the device is part of a fault chain, against when it is not.">
              <Feed feed={F.causation} onRetry={() => refetch('causation')} height={240}>
                <ColumnBars data={cascade} xKey="name" height={240} fmt={(v) => `${v}%`}
                            series={[
                              { key: 'In a fault chain', label: 'In a fault chain', color: STATUS.critical.fill },
                              { key: 'Not in a chain', label: 'Not in a chain', color: CAT[0] },
                            ]} />
              </Feed>
              <Note>
                {cascade.length
                  ? cascade.map((c) => `${c.name} ${c.lift.toFixed(1)}x`).join('  -  ')
                  : 'Cascade comparison unavailable.'}
                {' '}more likely to be critical inside a chain. Where the two bars are close, the cascade tells you little.
              </Note>
            </Panel>

            <Panel title="How often devices actually fail"
                   hint="New failures that began in the period, per fleet - not days spent already broken.">
              <Feed feed={F.baseRate} onRetry={() => refetch('baseRate')} height={220}>
                <RankBars data={baseRate} xKey="value" yKey="name" height={220} unit="New failures"
                          colorBy={(d) => deviceColor(d.type)} />
              </Feed>
              <Note>
                {baseRate.length
                  ? `The stored label marks far more days as failure than there were new failures - by ${baseRate.map((b) => `${b.inflation.toFixed(0)}x`).join(', ')} respectively. That gap is why a high accuracy score means nothing here.`
                  : 'Base-rate comparison unavailable.'}
              </Note>
            </Panel>
          </Grid>

          {!F.actNow.loading && !F.actNow.error && (F.actNow.rows || []).length === 0 && (
            <Note>
              No device is currently mid-outage with an open prediction, so the immediate-action list is empty. That is a
              reading of the fleet, not a missing feed.
            </Note>
          )}
        </Section>
      )}

      {/* ================= EVIDENCE ================================== */}
      {view === 'evidence' && (
        <Section accent={TAB_COLOR.ps1} eyebrow="Accuracy in plain terms" title="How we know this"
                 sub="Two questions decide whether a prediction is worth acting on, and neither of them is accuracy.">
          <Grid cols="repeat(auto-fit,minmax(300px,1fr))" style={{ marginBottom: 14 }}>
            <Panel title="When we flag a device, how often are we right?"
                   hint="Out of every hundred flags raised, how many were followed by a real failure.">
              <Feed feed={F.confusion} height={160}>
                <RankBars data={conf.map((c) => ({ name: c.name, value: Number((c.precision * 100).toFixed(1)), type: c.type }))}
                          xKey="value" yKey="name" height={160} unit="%"
                          colorBy={(d) => deviceColor(d.type)} fmt={(v) => `${v}%`} />
              </Feed>
            </Panel>
            <Panel title="Of the failures that happened, how many did we catch?"
                   hint="The other side of the same trade-off. Catching more means flagging more.">
              <Feed feed={F.confusion} height={160}>
                <RankBars data={conf.map((c) => ({ name: c.name, value: Number((c.recall * 100).toFixed(1)), type: c.type }))}
                          xKey="value" yKey="name" height={160} unit="%"
                          colorBy={(d) => deviceColor(d.type)} fmt={(v) => `${v}%`} />
              </Feed>
            </Panel>
          </Grid>

          <Panel
            title="Model scorecard"
            hint="Every candidate model that was evaluated, with the champion marked. AUC and average precision come from the run; accuracy is computed from the confusion counts."
            style={{ marginBottom: 14 }}
          >
            <Feed feed={F.scorecard} height={220}>
              <DataTable
                rows={scorecard}
                height={300}
                pageSize={50}
                searchable={false}
                exportName="ps1_model_scorecard"
                emptyText="No leaderboard rows published for this run."
                columns={[
                  { key: 'fleet', label: 'Fleet', width: 120 },
                  { key: 'model', label: 'Model', width: 190 },
                  {
                    key: 'champion',
                    label: 'Champion',
                    width: 100,
                    render: (r) => (r && r.champion
                      ? <Badge tone="good">champion</Badge>
                      : <span style={{ color: INK_3 }}>--</span>),
                  },
                  {
                    key: 'accuracy',
                    label: 'Accuracy',
                    num: true,
                    width: 120,
                    render: (r) => {
                      if (!r || r.accuracy === null) return '--';
                      const v = pct(r.accuracy, 1);
                      if (!r.accuracy_stated) return v;
                      return (
                        <span title={`Stated figure. Computed from the confusion counts: ${r.accuracy_computed === null ? 'n/a' : pct(r.accuracy_computed, 1)}`}>
                          {v} <span style={{ color: INK_3, fontWeight: 600 }}>*</span>
                        </span>
                      );
                    },
                  },
                  { key: 'precision', label: 'Precision', num: true, width: 100, render: (r) => (r && r.precision !== null ? pct(r.precision, 1) : '--') },
                  { key: 'recall', label: 'Recall', num: true, width: 95, render: (r) => (r && r.recall !== null ? pct(r.recall, 1) : '--') },
                  { key: 'f1', label: 'F1', num: true, d: 3, width: 85 },
                  { key: 'auc', label: 'AUC', num: true, d: 3, width: 85 },
                  { key: 'ap', label: 'Avg precision', num: true, d: 3, width: 115 },
                  { key: 'n_scored', label: 'Rows scored', num: true, d: 0, width: 110 },
                ]}
              />
            </Feed>
            {scorecard.some((r) => r.verdict) && (
              <div style={{ marginTop: 12, display: 'grid', gap: 6 }}>
                {scorecard.filter((r) => r.champion && r.verdict).map((r) => (
                  <div key={r.fleet + r.model} style={{ ...font.note, fontSize: 13.5 }}>
                    <strong style={{ color: INK }}>{r.fleet}</strong> -- {r.verdict}
                  </div>
                ))}
              </div>
            )}
            {scorecard.some((r) => r.accuracy_stated) && (
              <div style={{ ...font.note, fontSize: 13.5, marginTop: 10 }}>
                <strong style={{ color: INK }}>*</strong> Stated figure, not the one computed from
                this run's confusion counts
                {scorecard.filter((r) => r.accuracy_stated && r.accuracy_computed !== null)
                  .map((r) => ` (${r.fleet} computes to ${pct(r.accuracy_computed, 1)})`).join('')}
                {/* PK's end condition, 04-Aug. "Pending reconciliation" described a
                    task nobody owned and no date; this states the event that retires
                    the override, which is the same event that retires every other
                    11-Apr-bounded figure on the estate. */}
                . Stands until the models are re-run on data past 11 Apr 2026; hover the
                cell to see both.
              </div>
            )}
            <Note>
              One row per fleet -- the champion only. The accuracy column is a FLEET number, not a
              model one:
              /ps1/confusion publishes one set of counts per device category, not per model.
              Precision, recall, F1, AUC and average precision ARE per model.
              {' '}
              Accuracy is also the weakest number here. When almost every
              device-day is healthy, a model that always says "healthy" scores high on accuracy and catches
              nothing. Average precision is the one to read on a rare-event problem: it is the area under the
              precision-recall curve and it does not get a free ride from true negatives.
            </Note>
          </Panel>

          <Panel title="Where the alert line is set"
                 hint="Moving the line right raises fewer alerts and misses more; moving it left does the opposite.">
            <Feed feed={F.sweep} height={180}>
              <DataTable
                rows={(F.sweep.rows || []).map((s) => ({
                  fleet: deviceShort(s.device_category),
                  threshold: Number(s.threshold),
                  precision: Number(s.precision),
                  recall: Number(s.recall),
                  alert_rate: Number(s.alert_rate),
                }))}
                height={220} pageSize={50} searchable={false}
                columns={[
                  { key: 'fleet', label: 'Fleet' },
                  { key: 'threshold', label: 'Alert line', num: true, d: 4 },
                  { key: 'precision', label: 'Right when we flag', num: true, render: (r) => pct(r.precision) },
                  { key: 'recall', label: 'Failures caught', num: true, render: (r) => pct(r.recall) },
                  { key: 'alert_rate', label: 'Share of fleet flagged', num: true, render: (r) => pct(r.alert_rate) },
                ]} />
            </Feed>
          </Panel>

          <Note>
            A high accuracy score on its own proves nothing when almost every device is healthy - always guessing
            "healthy" would score well and be useless. The two questions above are the ones worth asking.
          </Note>
        </Section>
      )}

      {analyse && <AnalyseModal city={city} deviceId={analyse} onClose={() => setAnalyse(null)} />}
    </div>
  );
}

// FONTS_SCALED 04-Aug-2026
