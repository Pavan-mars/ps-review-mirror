// =====================================================================
// v2/Device360.jsx -- one device, everything the platform knows about it.
//
// MOST OF THIS IS ONE CALL. /ps1/device-360 already returns ps1, ps1_state,
// ps2, ps3, ps4, ps5, ps3_v2_rootcause_360, ps4_v3_360, cross_ps, causation,
// a recommendation string and a ServiceNow payload. Rebuilding that would be
// duplicating work that is already deployed and already correct.
//
// WHAT IT DOES NOT COVER, AND WHY THERE IS A SECOND CALL.
// That endpoint predates the Failure Pattern & Cascade Identification v2.5 generation. Its `ps2` block is the older
// cascade_rank / in_top_devices shape. The 28-day deterioration signal lives
// in ps2_v2_device_deterioration, so it is fetched separately via
// /ps2/v25/deterioration?device=<id> and shown as its own section rather than
// silently merged -- the two describe different things and were computed by
// different notebooks.
//
// EVERY SECTION SAYS WHICH PROBLEM STATEMENT IT CAME FROM. A device page that
// blends five analyses into one verdict hides exactly the disagreements that
// make it worth looking at.
//
// THE SERVICENOW BUTTON STAGES. It does not raise a ticket. The label says so.
// =====================================================================
import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { getObj, getRows, post, ps5 } from './V4api';
import { Badge, Card, Chip, Empty, Grid, Loading, Note, Panel, Section, TextField, Toolbar, Rule, Tabs, V2Style,
} from './V4Kit';
import DataTable from './V4DataTable';
import Device360Popup from './V4Device360Popup';
import { useLocations } from './V4Locations';
import { evidenceLines } from './V4Evidence';
import { Trend } from './V4Charts';

// STABLE CALLBACK IDENTITIES.                                v5
// These were inline arrows in JSX, so every render produced a NEW
// function and React.memo on the chart components compared unequal
// every time -- the memo was a no-op. Every one of these closes over
// nothing but module scope, so hoisting is enough; no useCallback, no
// dependency array to get wrong. They are only invoked during render,
// so referring to a const declared further down the module is safe.
const _fmt1 = (v) => Number(v).toFixed(2);

import { CARD, CAT, INK, INK_2, INK_3, LINE, STATUS, deviceColor, deviceShort, dfmt, font, nfmt, pct, A, TAB_COLOR, tint,
} from './V4theme';

const num = (v) => (v === null || v === undefined || Number.isNaN(Number(v)) ? 0 : Number(v));

function KV({ k, v, tone }) {
  // The API stringifies Python None in a few text columns, so 'None'/'nan'
  // arrive as literal strings. Treat them as absent rather than printing them.
  if (v === 'None' || v === 'nan' || v === 'NaN') return null;
  if (Array.isArray(v) && v.length === 0) return null;
  if (v === null || v === undefined || v === '') return null;
  return (
    <div style={{ display: 'flex', justifyContent: 'space-between', gap: 14, fontSize: 12.6, padding: '3px 0' }}>
      <span style={{ color: INK_2 }}>{k}</span>
      {/* .fill, not the STATUS entry itself. STATUS.warning is an OBJECT; assigning
          it to `color` yields "[object Object]", which is an invalid colour, so the
          span silently fell back to inherited ink. Every warn-toned value on this
          page has been rendering un-flagged. Same bug was live in two other files. */}
      <span style={{ color: tone === 'warn' ? STATUS.warning.fill : INK, fontWeight: 600, textAlign: 'right' }}>{String(v)}</span>
    </div>
  );
}

// EACH BLOCK WEARS ITS PROBLEM STATEMENT'S TAB COLOUR. Device 360 is the one
// screen where all five analyses sit on top of each other, and the colour is
// what tells you which one you are reading -- the Remaining Useful Life & SLA Breach block on this page and
// the Remaining Useful Life & SLA Breach tab in the header are the same fuchsia. It doubles as the legend
// for the whole colour system.
//
// The `ps` prop is the label ("Failure Prediction"), so the lookup is lower-cased. A block
// whose label is not a tab key falls back to the Device 360 gold rather than
// rendering an undefined colour.
// `absence` replaces the generic "does not appear in Failure Prediction" line. That generic
// line is true but useless: it tells the reader the lookup came back empty and
// leaves them to guess whether that is a bug. Each problem statement knows why
// its own absences happen, and says so in its own words.
function SourcePanel({ ps, title, found, children, note, absence }) {
  const accent = TAB_COLOR[String(ps || '').toLowerCase()] || TAB_COLOR.device;
  return (
    <Card pad="14px 16px" accent={accent}>
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 9 }}>
        <span
          style={{
            fontSize: 11, fontWeight: 800, letterSpacing: '.08em', color: '#FFFFFF',
            background: accent, borderRadius: 5, padding: '2px 6px', lineHeight: 1.3,
          }}
        >
          {ps}
        </span>
        <span style={{ fontSize: 12.6, fontWeight: 700, color: INK }}>{title}</span>
        {found === false && <Badge tone="neutral">no record</Badge>}
      </div>
      <div style={{ marginTop: 10 }}>
        {found === false
          ? <Empty height={absence ? 84 : 60}>{absence || `This device does not appear in ${ps}.`}</Empty>
          : children}
      </div>
      {note && <div style={{ ...font.micro, marginTop: 10, lineHeight: 1.5, textTransform: 'none', letterSpacing: 0 }}>{note}</div>}
    </Card>
  );
}


const PICKER_TYPES = ['GATE', 'TVM', 'VALIDATOR'];

// ---------------------------------------------------------------------
// TRIAGE LIST -- the cold-start view for this tab.
//
// One row per device off v_device_360, so every column comes from the same
// joined row and no cell can disagree with the device page it opens.
// Fleet filter only: the point is a short list to click into, not a second
// analysis surface. Absent values render blank rather than as zero -- a
// device the anomaly run never scored has NO severity, which is not the
// same as a severity of none.
// ---------------------------------------------------------------------
function TriageList({ city, onPick }) {
  const [rows, setRows] = useState([]);
  const [fleet, setFleet] = useState('ALL');
  const [st, setSt] = useState({ loading: true, error: null });

  useEffect(() => {
    let alive = true;
    setSt({ loading: true, error: null });
    getRows('/device/360/risk', { city, limit: 300, ...(fleet === 'ALL' ? {} : { category: fleet }) })
      .then((r) => { if (alive) { setRows(Array.isArray(r) ? r : []); setSt({ loading: false, error: null }); } })
      .catch((e) => { if (alive) setSt({ loading: false, error: String((e && e.message) || e) }); });
    return () => { alive = false; };
  }, [city, fleet]);

  // NB DataTable calls render(ROW), not render(value) -- see V4DataTable's
  // `c.render(r)`. Passing a value-shaped callback hands the row object
  // straight to React and blanks the page with error #31.
  const blank = (v) => (v === null || v === undefined || v === '' ? '' : String(v));
  const columns = useMemo(() => ([
    { key: 'signal_count', label: 'Signals', num: true, width: 84,
      render: (r) => (r.signal_count === null || r.signal_count === undefined
        ? '' : `${r.signal_count} of 4`) },
    { key: 'device_id', label: 'Device', flex: 1.1 },
    { key: 'mars_device_category', label: 'Fleet', width: 96,
      render: (r) => deviceShort(r.mars_device_category) },
    { key: 'facility_name', label: 'Station', flex: 1.4,
      render: (r) => blank(r.facility_name) },
    { key: 'ps1_fail_prob', label: 'Failure prob.', num: true, width: 110,
      render: (r) => (r.ps1_fail_prob === null || r.ps1_fail_prob === undefined
        ? '' : pct(r.ps1_fail_prob)) },
    { key: 'ps1_risk_tier', label: 'Failure band', width: 110, render: (r) => blank(r.ps1_risk_tier) },
    { key: 'ps3_action_band', label: 'Severity action', flex: 1, render: (r) => blank(r.ps3_action_band) },
    { key: 'ps4_severity', label: 'Anomaly', width: 96, render: (r) => blank(r.ps4_severity) },
    { key: 'ps5_risk_band', label: 'Life band', width: 96, render: (r) => blank(r.ps5_risk_band) },
    { key: 'sn_incident_count', label: 'SN tickets', num: true, width: 96,
      render: (r) => (r.sn_incident_count ? nfmt(r.sn_incident_count) : '') },
  ]), []);

  return (
    <Section accent={TAB_COLOR.device}
      eyebrow="Where to start"
      title="Devices with the most signals against them"
      sub="One row per device, every column read from the same cross-problem-statement row this page opens."
      right={
        <Toolbar>
          {['ALL', ...PICKER_TYPES].map((f) => (
            <Chip key={f} active={fleet === f} onClick={() => setFleet(f)}>
              {f === 'ALL' ? 'All fleets' : deviceShort(f)}
            </Chip>
          ))}
        </Toolbar>
      }
    >
      {st.loading && <Loading height={180} label="Loading the shortlist" />}
      {st.error && <Empty height={120}>{`The shortlist could not be loaded (${st.error}). The device lookup above still works.`}</Empty>}
      {!st.loading && !st.error && (
        <>
          <DataTable
            rows={rows}
            columns={columns}
            height={380}
            rowKey={(r) => r.device_id}
            onRowClick={(r) => onPick && onPick(r.device_id)}
            emptyText="No devices for this fleet."
            exportName={`device_triage_${city}`}
          />
          <Note>
            Signals counts how many analyses flagged this device out of four --
            a severity action was queued, the anomaly run called it something
            other than Normal, survival says it is past its typical interval, or
            ServiceNow holds a ticket against it. It is a count of agreement, not
            a risk score, and nothing here is weighted. Failure Prediction is
            deliberately NOT counted: its label marks days a device was already
            out of service rather than the day it failed, so 398 devices share a
            probability of 100% and sorting on it returns an arbitrary slice of
            that tie. The probability is still shown; it just does not decide the
            order. Blank cells mean the analysis did not score this device, which
            is not the same as a score of zero.
          </Note>
        </>
      )}
    </Section>
  );
}

// FETCHED ONCE PER SESSION, NOT PER VISIT. The roster is ~560 KB across the
// three fleets and never changes while the page is open, so re-pulling and
// re-parsing it every time someone returns to this tab is pure waiting.
const ROSTER_CACHE = new Map();

// ---------------------------------------------------------------------
// DEVICE PICKER.
//
// The page used to offer a free-text box only, which assumes you already know
// a device id. That is fine for someone chasing a specific unit and useless
// for anyone browsing.
//
// The roster comes from v_ps5_device_rul because it is the only published feed
// that carries device, fleet, facility AND a risk band for the whole estate in
// one call per fleet -- 4,103 devices. It is a ROSTER here, nothing more; no
// number from it is displayed as a finding. The free-text box is kept, because
// a known id should never require three dropdowns.
// ---------------------------------------------------------------------
function DevicePicker({ city, value, onPick }) {
  const loc = useLocations(city);
  const [roster, setRoster] = useState([]);
  const [state, setState] = useState({ loading: true, error: null });
  const [fleet, setFleet] = useState('ALL');
  const [facility, setFacility] = useState('ALL');
  const [band, setBand] = useState('ALL');

  // THE ROSTER YIELDS TO THE DEVICE LOOKUP.                  04-Aug-2026
  //
  // This used to fire three full-fleet calls in PARALLEL, on mount, before
  // anything else on the page. Together they pull 4,103 rows, and browsers
  // cap concurrent connections per host -- so the /ps1/device-360 request
  // that actually renders the page queued BEHIND them. The page looked
  // broken when it was merely last in line.
  //
  // Two changes, both about ordering rather than volume:
  //   * a short delay, so the device lookup claims a connection first;
  //   * fleets fetched ONE AT A TIME, so the roster never holds more than
  //     one connection and the detail call is never starved.
  // The roster is a convenience list. It has no business outranking the
  // thing the user asked to see.
  useEffect(() => {
    let alive = true;
    setState({ loading: true, error: null });
    const cached = ROSTER_CACHE.get(city);
    if (cached) { setRoster(cached); setState({ loading: false, error: null }); return () => {}; }
    const timer = setTimeout(() => {
      (async () => {
        // 27-Aug-2026: the conformed device dimension covers the WHOLE estate
        // (6,619 devices) where the PS5 feed covers 1,536. Try it first; if the
        // deployed API predates the /device/central route (or it returns
        // nothing), fall back to the original per-fleet PS5 roster so the
        // picker never regresses. Same one-connection-at-a-time discipline.
        const central = await getRows('/device/central', { city, roster: 1 }).catch(() => []);
        if (central && central.length) return [central];
        const res = [];
        for (const t of PICKER_TYPES) {
          if (!alive) return;
          res.push(await ps5.deviceRul(city, t).catch(() => []));
        }
        return res;
      })()
      .then((res) => {
        if (!alive || !res) return;
        const rows = res.flat().map((r) => ({
          device_id: r.device_id,
          device_type: String(r.device_type || '').toUpperCase(),
          facility_id: String(r.facility_id ?? '').replace(/\.0$/, '') || '--',
          risk_band: String(r.risk_band || '').toUpperCase(),
        }));
        if (rows.length) ROSTER_CACHE.set(city, rows);
        setRoster(rows);
        setState({ loading: false, error: rows.length ? null : 'roster feed returned no rows' });
      })
      .catch((e) => { if (alive) setState({ loading: false, error: String((e && e.message) || e) }); });
    }, 1200);
    return () => { alive = false; clearTimeout(timer); };
  }, [city]);

  const facilities = useMemo(() => {
    const f = roster.filter((r) => fleet === 'ALL' || r.device_type === fleet);
    return [...new Set(f.map((r) => r.facility_id))]
      .sort((a, b) => (Number(a) || 1e9) - (Number(b) || 1e9));
  }, [roster, fleet]);

  const matches = useMemo(() => roster.filter((r) => {
    if (fleet !== 'ALL' && r.device_type !== fleet) return false;
    if (facility !== 'ALL' && r.facility_id !== facility) return false;
    if (band !== 'ALL' && r.risk_band !== band) return false;
    return true;
  }).sort((a, b) => String(a.device_id).localeCompare(String(b.device_id))), [roster, fleet, facility, band]);

  const selStyle = {
    border: `1px solid ${LINE}`, borderRadius: 10, padding: '7px 10px',
    fontSize: 13.1, color: INK, background: CARD, minWidth: 130, outline: 'none',
  };

  return (
    <Card pad="14px 16px" style={{ marginBottom: 14 }}>
      <div style={{ ...font.micro, marginBottom: 9 }}>Pick a device</div>
      <Toolbar>
        <Chip active={fleet === 'ALL'} onClick={() => { setFleet('ALL'); setFacility('ALL'); }}>All fleets</Chip>
        {PICKER_TYPES.map((t) => (
          <Chip key={t} active={fleet === t} color={deviceColor(t)}
                onClick={() => { setFleet(t); setFacility('ALL'); }}>
            {deviceShort(t)}
          </Chip>
        ))}
        <span style={{ width: 10 }} />
        <select value={facility} onChange={(e) => setFacility(e.target.value)} style={selStyle}>
          <option value="ALL">All locations</option>
          {/* Named, and sorted by NAME rather than by id -- an operator
              scanning this list is looking for "North Park", not for 45. */}
          {facilities
            .map((x) => ({ id: x, label: loc.known(x) ? `${loc.name(x)} (${x})` : `Depot ${x}` }))
            .sort((a, b) => a.label.localeCompare(b.label))
            .map((x) => <option key={x.id} value={x.id}>{x.label}</option>)}
        </select>
        <select value={band} onChange={(e) => setBand(e.target.value)} style={selStyle}>
          <option value="ALL">All risk bands</option>
          {['CRITICAL', 'HIGH', 'MEDIUM', 'LOW'].map((x) => <option key={x} value={x}>{x}</option>)}
        </select>
        <select
          value={matches.some((m) => m.device_id === value) ? value : ''}
          onChange={(e) => e.target.value && onPick(e.target.value)}
          style={{ ...selStyle, minWidth: 210 }}
        >
          <option value="">
            {state.loading ? 'Loading devices...' : `${nfmt(matches.length)} device${matches.length === 1 ? '' : 's'} - choose one`}
          </option>
          {matches.slice(0, 4000).map((m) => (
            <option key={m.device_id} value={m.device_id}>
              {m.device_id} - {loc.known(m.facility_id) ? loc.name(m.facility_id) : `depot ${m.facility_id}`}{m.risk_band ? ` - ${m.risk_band}` : ''}
            </option>
          ))}
        </select>
      </Toolbar>
      {state.error && (
        <div style={{ ...font.note, fontSize: 12.2, marginTop: 8 }}>
          <Badge tone="warning">Roster unavailable</Badge> {state.error} -- type a device id instead.
        </div>
      )}
    </Card>
  );
}

export default function Device360({ city = 'CHI', initialDevice = '' }) {
  const [q, setQ] = useState(initialDevice);
  const [device, setDevice] = useState(initialDevice);
  const [d360, setD360] = useState(null);
  // The precomputed row from device_level_aggregation. Null whenever the
  // route, the table or the row is absent -- every panel below reads the
  // composite as before in that case, so a missing fast path costs
  // latency and nothing else.
  const [agg, setAgg] = useState(null);
  // THE SUMMARY NO LONGER OPENS ITSELF.                     06-Aug-2026
  // It used to fire on every successful lookup. On this tab that is wrong:
  // the reader is already ON the full record, so the overlay covers the
  // thing they asked for and has to be dismissed before any of it can be
  // read. The popup stays -- it is how other screens surface a device --
  // but here it is opened deliberately, from the "Summary" button on the
  // device header below.
  const [brief, setBrief] = useState(false);
  const [deter, setDeter] = useState([]);
  const [state, setState] = useState({ loading: false, error: null });
  const [staged, setStaged] = useState(null);

  const load = useCallback(async (id) => {
    const dev = String(id || '').trim().toUpperCase();
    if (!dev) return;
    setDevice(dev);
    setState({ loading: true, error: null });
    setD360(null); setDeter([]); setStaged(null); setBrief(false);
    try {
      // THE FAST ROW FIRST, THE COMPOSITE BEHIND IT.          02-Sep-2026
      //
      // device_level_aggregation (sql/59) holds one precomputed row per device
      // -- identity, the four W dimensions, headline measures, component lists
      // as JSON. It answers in milliseconds where /ps1/device-360 takes about
      // six seconds, because the composite stitches twelve queries across seven
      // views on every call.
      //
      // It is NOT a replacement. The causation matrices and the ps3_v2
      // rootcause family are derived in Python inside the composite and have no
      // table to read, so the composite still runs and still owns those panels.
      // What the fast row buys is an immediate first paint of everything above
      // them, and a guarantee that identity and the W columns come from one
      // conformed row rather than twelve independent lookups.
      //
      // Every failure mode here degrades to today's behaviour: the route
      // missing, the row absent, the table not yet built -- all fall through to
      // the composite alone, which is exactly what shipped before this.
      let agg = null;
      try { agg = await getObj('/device/aggregate', { city, device_id: dev }); }
      catch (e) { agg = null; }
      if (agg && agg.found) setAgg(agg); else setAgg(null);

      const spine = await getObj('/ps1/device-360', { city, device_id: dev });
      let rows = [];
      try { rows = await getRows('/ps2/v25/deterioration', { city, device: dev, limit: 2000 }); }
      catch (e) { rows = []; }
      setD360(spine || {});
      setDeter(rows || []);
      setState({ loading: false, error: null });
    } catch (e) {
      setState({ loading: false, error: String((e && e.message) || e) });
    }
  }, [city]);

  // React to a device pushed in from another screen. Device 360 previously
  // took initialDevice as seed state only, so arriving from an Analyse modal
  // landed on an empty search box with the id in the URL and nothing loaded.
  //
  // 27-Aug-2026. The guard used to be `d !== device`, and `device` is SEEDED
  // from initialDevice above -- so a deep link already present at mount made
  // the two equal and the load never fired. The id sat in the box and the
  // reader had to press Look up. Timing hid it: the hash used to arrive after
  // mount, so the values differed. Tracking the id we last loaded fires on
  // mount AND on change, and cannot double-fetch the same device.
  const loadedIdRef = useRef('');
  useEffect(() => {
    const d = String(initialDevice || '').trim().toUpperCase();
    if (d && loadedIdRef.current !== d) { loadedIdRef.current = d; setQ(d); load(d); }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [initialDevice]);

  const stageTicket = useCallback(async () => {
    if (!d360) return;
    const r = await post('/ps1/servicenow-stage', {
      device_id: device,
      device_category: (d360.ps1 || {}).device_category,
      short_description: (d360.servicenow_payload || {}).short_description || `PS cross-signal on ${device}`,
      payload: d360.servicenow_payload || {},
    }, { city });
    setStaged(r);
  }, [d360, device, city]);

  const ps1 = (d360 && d360.ps1) || {};
  const ps1s = (d360 && d360.ps1_state) || {};
  const ps2 = (d360 && d360.ps2) || {};
  const ps3 = (d360 && d360.ps3) || {};
  const ps4 = (d360 && d360.ps4) || {};
  const ps4v3 = (d360 && d360.ps4_v3_360) || {};
  const ps5 = (d360 && d360.ps5) || {};
  const xps = (d360 && d360.cross_ps) || {};
  // Conformed identity + ServiceNow ticket history, from dim_device_incident_cmdb
  // (sql/56). Both sections are absent on an API that predates them, so every
  // read below is optional and the panel simply does not render.
  // Identity and ticket history prefer the precomputed row, which carries the
  // same field names, and fall back to the composite's own sections when the
  // fast row is absent. Nothing renders differently either way -- this is a
  // source swap, not a content change, which is what makes it safe to ship
  // before the parity check rather than after it.
  const ident = agg || (d360 && d360.identity) || null;
  const snh = agg
    ? {
        incident_count: agg.sn_incident_count,
        latest_incident: agg.incident_number,
        latest_opened_at: agg.sn_latest_opened_at,
        latest_closed_at: null,
        cmdb_ci_sys_id: agg.cmdb_ci_sys_id,
      }
    : (d360 && d360.servicenow_history) || null;
  // Same words as the popup: both read V4Evidence, so the two views of one
  // device cannot describe it differently.
  const why = useMemo(() => evidenceLines(d360), [d360]);
  const loc = useLocations(city);

  // ps4_v3_360 returns weeks[], cluster[] and persistent[] as ARRAYS, not
  // scalars. Reading them as scalars is what printed [object Object].
  const ps4Weeks = Array.isArray(ps4v3.weeks) ? ps4v3.weeks : [];
  const ps4Cluster = Array.isArray(ps4v3.cluster) ? ps4v3.cluster[0] : (ps4v3.cluster || null);
  const ps4Persist = Array.isArray(ps4v3.persistent) ? ps4v3.persistent[0] : null;
  const ps4Worst = ps4Weeks.length
    ? ps4Weeks.reduce((a, b) => (num(b.max_abs_z) > num(a.max_abs_z) ? b : a))
    : null;
  // The legacy Anomaly & Outlier Analysis block and the v3 block can disagree. Say so rather than
  // picking one -- the disagreement is the finding.
  const ps4Note = ps4Weeks.length && Number(ps4.alert_count || 0) === 0
    ? 'The earlier Anomaly & Outlier Analysis export reports no alert weeks for this device while the v3 weekly scoring does. Both are shown; the v3 numbers are the current generation.'
    : undefined;

  const ps5Rel = (ps5 && ps5.reliability) || null;
  // 03-Aug-2026. The Remaining Useful Life & SLA Breach panel only ever rendered the CATEGORY fields --
  // concordance, registry status, blockers -- so even a device the survival
  // run ranks first in its fleet showed nothing about itself. These are the
  // device-grain fields from v_ps5_device_rul.
  const ps5Device = ps5 && ps5.level === 'device';
  const ps5Components = Array.isArray(ps5 && ps5.components) ? ps5.components : [];

  const deterCols = [
    { key: 'event_date', label: 'Date' },
    { key: 'alert_reason', label: 'Why flagged' },
    { key: 'hardware_oos_onsets', label: 'OOS onsets', num: true },
    { key: 'hardware_oos_minutes', label: 'OOS minutes', num: true, d: 0 },
    { key: 'validated_failure_onsets', label: 'Validated', num: true },
    { key: 'baseline_mean_28d', label: '28d mean', num: true, d: 2 },
    { key: 'oos_zscore_28d', label: 'z-score', num: true, d: 2 },
  ];

  return (
    <>
      {/* Device 360 is reachable on its own route as well as through the shell. */}
      <V2Style />

      <Section accent={TAB_COLOR.device}
        eyebrow="Device 360"
        title="One device, across every problem statement"
        sub="Each section states which analysis produced it. Where two disagree, both are shown -- the disagreement is the finding."
        right={
          <div style={{ display: 'flex', gap: 8, alignItems: 'center', minWidth: 320 }}>
            <DevicePicker city={city} value={device} onPick={(id) => { setQ(id); load(id); }} />

            <TextField
              value={q}
              onChange={setQ}
              placeholder="Device id, e.g. RVG06302"
              onKeyDown={(e) => { if (e.key === 'Enter') load(q); }}
            />
            <button
              type="button"
              onClick={() => load(q)}
              style={{
                border: `1px solid ${LINE}`, background: CARD, borderRadius: 9,
                padding: '7px 14px', fontSize: 12.6, fontWeight: 600, color: INK, cursor: 'pointer',
              }}
            >
              Look up
            </button>
          </div>
        }
      />

      {!device && <Note>Enter a device id above. Ids look like RVG06302 (fare gate), BMV03868 (bus validator) or TVM03402.</Note>}

      {/* WHERE TO START WHEN YOU DO NOT ALREADY KNOW A DEVICE.  27-Aug-2026
          Every other route into this page assumes a device id in hand -- a
          deep link, the Analyse button, the picker. Someone opening the tab
          cold had a text box and nothing else. This is the one screen where
          all five problem statements are already joined per device, so a
          cross-PS shortlist costs one query.

          THE ORDER IS NOT A RANKING OF RISK. It sorts by the Failure
          Prediction probability, and that model has not passed its quality
          gate: its label marks days a device was ALREADY out of service, not
          the day it went out, which inflates positives roughly nineteenfold.
          Saying "start here" would be dressing an unresolved defect as a work
          queue. The note below says what the order is, so a reader can
          discount it. */}
      {!device && <TriageList city={city} onPick={(id) => { setQ(id); load(id); }} />}

      {state.loading && <Loading height={200} label={`Loading ${device}`} />}

      {state.error && (
        <Card>
          <Badge tone="warning">Could not load {device}</Badge>
          <div style={{ ...font.micro, marginTop: 8 }}>{state.error}</div>
        </Card>
      )}

      {d360 && !state.loading && (
        <>
          <Card>
            <div style={{ display: 'flex', alignItems: 'baseline', gap: 12, flexWrap: 'wrap' }}>
              <span style={{ fontSize: 19.8, fontWeight: 800, color: INK }}>{d360.device_id || device}</span>
              {ps1.device_category && <Badge tone="neutral">{deviceShort(ps1.device_category)}</Badge>}
              {ps1.facility_id && (
                <span style={{ ...font.micro }}>
                  {loc.known(ps1.facility_id) ? `${loc.name(ps1.facility_id)} (${ps1.facility_id})` : `facility ${ps1.facility_id}`}
                </span>
              )}
              {(d360.bus_identity || {}).bus_label && <span style={{ ...font.micro }}>{d360.bus_identity.bus_label}</span>}
              <span style={{ marginLeft: 'auto', display: 'inline-flex', gap: 8 }}>
                <button
                  type="button"
                  onClick={() => setBrief(true)}
                  style={{
                    border: `1px solid ${LINE}`, background: CARD, borderRadius: 9,
                    padding: '6px 13px', fontSize: 12.6, fontWeight: 600, color: INK, cursor: 'pointer',
                  }}
                >
                  Summary
                </button>
                <button
                  type="button"
                  onClick={stageTicket}
                  style={{
                    border: `1px solid ${LINE}`, background: CARD, borderRadius: 9,
                    padding: '6px 13px', fontSize: 12.6, fontWeight: 600, color: INK, cursor: 'pointer',
                  }}
                >
                  Stage a ServiceNow record
                </button>
              </span>
            </div>
            {d360.recommendation && (
              <div style={{ fontSize: 13.1, color: INK_2, marginTop: 10, lineHeight: 1.5 }}>{d360.recommendation}</div>
            )}
            {staged && (
              <div style={{ marginTop: 10 }}>
                <Badge tone={staged.ok ? 'good' : 'warning'}>
                  {staged.ok ? 'Staged - not sent to ServiceNow' : `Staging failed: ${staged.error}`}
                </Badge>
                <div style={{ ...font.micro, marginTop: 6 }}>
                  This writes a row for review. It does not create a ticket in ServiceNow.
                </div>
              </div>
            )}
          </Card>

          <Grid cols="repeat(auto-fit,minmax(320px,1fr))">
            {/* PK's wording, agreed 04-Aug. It does the two things the generic
                line could not: it says what the absence MEANS (no OOS window in
                Failure Prediction's label build, which is a statement about the label, not the
                device) and it points at the screen that does hold this device's
                failure history. Note it does NOT mention ServiceNow -- Failure Prediction's
                absence is about the out-of-service window, not a ticket join. */}
            <SourcePanel
              ps="Failure Prediction" title="Failure prediction" found={ps1.found}
              absence="Not recorded as an out-of-service window during failure device prediction. This device's fault history is on the Remaining Useful Life & SLA Breach tab."
            >
              <KV k="3-day failure probability" v={ps1.failure_probability !== undefined ? pct(ps1.failure_probability, 1) : null} />
              <KV k="Decision threshold" v={ps1.decision_threshold !== undefined ? pct(ps1.decision_threshold, 1) : null} />
              <KV k="Risk band" v={ps1.risk_band} tone={String(ps1.risk_band).toUpperCase() === 'HIGH' || String(ps1.risk_band).toUpperCase() === 'CRITICAL' ? 'warn' : undefined} />
              <KV k="Above threshold" v={ps1.predicted_label === 1 ? 'yes' : ps1.predicted_label === 0 ? 'no' : null} />
              <KV k="Device state" v={ps1s.device_state} />
              <KV k="Current spell day" v={ps1s.current_spell_day} />
              <KV k="Spells observed" v={ps1s.n_spells} />
              <KV k="Last scored" v={ps1s.last_scored_day ? dfmt(ps1s.last_scored_day) : null} />
            </SourcePanel>

            <SourcePanel
              ps="Failure Pattern & Cascade Identification" title="Cascading failure (previous generation)"
              found={ps2.in_catalog === true || ps2.in_top_devices === true}
              note="From the earlier Failure Pattern & Cascade Identification export. The v2.5 deterioration signal is below and was computed by a different notebook."
            >
              <KV k="Cascade rank" v={ps2.cascade_rank} />
              <KV k="Rank within fleet" v={ps2.cascade_rank_in_category} />
              <KV k="Total impact" v={ps2.total_impact !== undefined && ps2.total_impact !== null ? nfmt(ps2.total_impact) : null} />
              <KV k="Average impact" v={ps2.avg_impact} />
              <KV k="Recurrence window" v={ps2.recurrence_cascade_days ? `${nfmt(ps2.recurrence_cascade_days)} days` : null} />
              <KV k="Recent cascades" v={Array.isArray(ps2.recent_cascades) ? ps2.recent_cascades.length : ps2.recent_cascades} />
              <KV k="Chronic" v={ps2.chronic === true ? 'yes' : ps2.chronic === false ? 'no' : null} />
              <KV k="In device catalog" v={ps2.in_catalog === true ? 'yes' : ps2.in_catalog === false ? 'no' : null} />
            </SourcePanel>

            {/* A device absent from Root Cause Analysis is not a lookup failure, and the panel
                should not read like one.
                --------------------------------------------------------------
                THE "2,485 OF 4,103" FRACTION THAT USED TO BE HERE WAS WRONG,
                and measurably so. 4,103 is the Remaining Useful Life & SLA Breach device roster
                (/ps5/summary: 452 gates + 416 TVMs + 3,235 validators). Root Cause Analysis's
                device population is not a subset of it -- measured against the
                live API on 04-Aug, 475 of the 2,806 devices in
                ps3_v25_device_summary do not appear in the Remaining Useful Life & SLA Breach roster at all,
                and Root Cause Analysis counts 854 gates where Remaining Useful Life & SLA Breach has 452. Printing one over
                the other asserts a containment that does not hold, and would
                have put a "coverage" percentage on screen that no query could
                reproduce.
                So the note states the BOUNDARY instead of a fraction. The
                measurable coverage number now lives on the Root Cause Analysis tab's status
                bar, where it is read live from the run's own table counts and
                cannot drift. */}
            <SourcePanel
              ps="Root Cause Analysis" title="Root cause and severity"
              found={ps3.found}
              note={ps3.found === false
                ? 'The published Root Cause Analysis run scores the held-out test window, not the whole estate, so a device outside that window has no root-cause row by construction. That is a boundary, not a lookup failure. The Root Cause Analysis tab states how many devices the run covers.'
                : 'Cause not attributable or the out-of-service event carries no component subsystem. Severity is blank across every device in this run: it publishes a root-cause head only.'}
            >
              <KV k="Dominant component" v={ps3.dominant_pred_component} />
              <KV k="Incidents attributed" v={ps3.n_incidents} />
              <KV k="Dominant severity" v={ps3.dominant_pred_severity} />
              <KV k="Average component age" v={ps3.avg_component_age_days ? `${nfmt(ps3.avg_component_age_days)} days` : null} />
              <KV k="Last incident" v={ps3.last_incident_dtm ? dfmt(ps3.last_incident_dtm) : null} />
            </SourcePanel>

            {/* SERVICENOW HISTORY IS NOT A PROBLEM STATEMENT.        27-Aug-2026
                It is the maintenance record Cubic already holds, joined to this
                device through the CMDB CI. It sits with the PS blocks because a
                reader asking "what do we know about this device" wants it here,
                but it carries no model output and says so.

                THE COUNT ON THIS PANEL AND THE ONE ON ROOT CAUSE ANALYSIS ARE
                DIFFERENT THINGS. Root Cause Analysis counts out-of-service
                episodes from the availability feed; this counts ServiceNow
                tickets raised against the CI. For HBG00011 that is 70 against
                104 -- two true numbers measuring two different events. Printing
                them without that sentence would read as a contradiction. */}
            {(snh || ident) && (
              <SourcePanel
                ps="ServiceNow" title="Maintenance ticket history"
                found={snh && snh.incident_count > 0 ? undefined : false}
                absence={"No ServiceNow ticket is linked to this device's CMDB CI. "
                  + "Roughly a third of the fleet is reachable from the CI map, so an "
                  + "absence here is usually an unmapped device rather than a device "
                  + "that has never needed attention."}
                note={"Tickets are counted from ServiceNow via the CMDB configuration item, "
                  + "not from the availability feed. The out-of-service episode count on the "
                  + "Root Cause Analysis panel measures a different event and will not match."}
              >
                <KV k="Tickets on this CI" v={snh && snh.incident_count != null ? nfmt(snh.incident_count) : null} />
                <KV k="Most recent ticket" v={snh && snh.latest_incident} />
                <KV k="Raised" v={snh && snh.latest_opened_at ? dfmt(snh.latest_opened_at) : null} />
                <KV k="Closed" v={snh && snh.latest_closed_at ? dfmt(snh.latest_closed_at) : null} />
                <KV k="CMDB configuration item" v={(snh && snh.cmdb_ci_sys_id) || (ident && ident.cmdb_ci_sys_id)} />
                <KV k="Serial" v={ident && (ident.serial_number || ident.component_serial_nbr)} />
                <KV k="Component" v={ident && ident.component_type} />
                <KV k="Station" v={ident && ident.facility_name} />
                <KV k="Operator" v={ident && ident.operator_name} />
              </SourcePanel>
            )}

            <SourcePanel
              ps="Anomaly & Outlier Analysis" title="Anomaly detection"
              found={ps4v3.found !== undefined ? ps4v3.found : undefined}
              note={ps4Note}
            >
              <KV k="Weeks scored" v={ps4Weeks.length || null} />
              <KV k="Actionable weeks" v={ps4Weeks.length ? ps4Weeks.filter((w) => Number(w.is_actionable_week) === 1).length : null} />
              <KV k="Worst severity" v={ps4Worst ? ps4Worst.severity : null} tone={ps4Worst && String(ps4Worst.severity).toUpperCase() === 'CRITICAL' ? 'warn' : undefined} />
              <KV k="Worst week" v={ps4Worst && ps4Worst.week_start ? dfmt(ps4Worst.week_start) : null} />
              <KV k="Largest absolute z" v={ps4Worst && ps4Worst.max_abs_z !== undefined ? nfmt(ps4Worst.max_abs_z, 1) : null} />
              <KV k="Cluster" v={ps4Cluster ? ps4Cluster.cluster_id : null} />
              <KV k="Cluster silhouette" v={ps4Cluster && ps4Cluster.silhouette !== undefined ? nfmt(ps4Cluster.silhouette, 2) : null} />
              <KV k="Persistent" v={ps4Persist ? `yes, ${ps4Persist.actionable_weeks} week(s)` : 'no'} />
              <KV k="Legacy alert weeks" v={ps4.alert_count} />
            </SourcePanel>

            <SourcePanel
              ps="Remaining Useful Life & SLA Breach" title="Remaining useful life"
              found={ps5.found === false && !ps5Rel ? false : undefined}
              note={ps5.note}
            >
              <KV k="Level" v={ps5.level} />
              <KV k="Category" v={ps5.category} />
              {ps5Device && (
                <>
                  <KV
                    k="Act now"
                    v={ps5.act_now ? 'yes -- past expected life with 30 days or less left' : 'no'}
                    tone={ps5.act_now ? 'warn' : undefined}
                  />
                  <KV k="Risk band" v={ps5.risk_band} tone={String(ps5.risk_band).toUpperCase() === 'CRITICAL' ? 'warn' : undefined} />
                  <KV k="Days to next OOS event" v={ps5.rul_standard_days === undefined || ps5.rul_standard_days === null ? null : nfmt(ps5.rul_standard_days, 1)} />
                  <KV k="Typical interval (days)" v={ps5.predicted_median_survival_days === undefined || ps5.predicted_median_survival_days === null ? null : nfmt(ps5.predicted_median_survival_days, 1)} />
                  <KV k="Past typical interval" v={ps5.is_overdue === undefined ? null : (ps5.is_overdue ? 'yes' : 'no')} tone={ps5.is_overdue ? 'warn' : undefined} />
                  {/* Rank is WITHIN the fleet. Printed as "n of N" rather than
                      as a bare rank so it cannot be read across device types --
                      the three survival models are fitted separately. */}
                  <KV
                    k="Rank in its own fleet"
                    v={ps5.rul_rank_in_type ? `${nfmt(ps5.rul_rank_in_type)} of ${nfmt(ps5.n_devices_in_type)}` : null}
                  />
                  <KV k="Fault-free days" v={ps5.current_healthy_age_days === undefined || ps5.current_healthy_age_days === null ? null : nfmt(ps5.current_healthy_age_days)} />
                  <KV k="Days since last OOS" v={ps5.days_since_hw_oos === undefined || ps5.days_since_hw_oos === null ? null : nfmt(ps5.days_since_hw_oos)} />
                  <KV k="Prior OOS episodes" v={ps5.n_prior_oos === undefined || ps5.n_prior_oos === null ? null : nfmt(ps5.n_prior_oos)} />
                  <KV k="OOS in the last 30 days" v={ps5.roll_fail_30d === undefined || ps5.roll_fail_30d === null ? null : nfmt(ps5.roll_fail_30d)} />
                </>
              )}
              <KV k="Concordance index" v={ps5Rel && ps5Rel.concordance_index !== undefined ? nfmt(ps5Rel.concordance_index, 3) : null} />
              <KV k="Registry status" v={ps5Rel ? ps5Rel.registry_status : null} />
              <KV k="Dashboard ready" v={ps5Rel ? (ps5Rel.dashboard_ready === true ? 'yes' : 'no') : null} />
              <KV k="Blockers" v={ps5Rel ? ps5Rel.blockers : null} />
            </SourcePanel>

            <SourcePanel ps="CROSS" title="Where the analyses agree"
                         note={xps.subsystem_detail}>
              {/* Composed by V4Evidence from the structured fields, so this
                  panel and the popup say the same thing in the same words.
                  Previously these were chips reading "PS1 above threshold" --
                  a code the reader has to translate before they can act. */}
              {why.length > 0 && (
                <div style={{ display: 'grid', gap: 6, margin: '2px 0 10px' }}>
                  {why.map((w, i) => (
                    <div key={i} style={{ display: 'flex', gap: 7, alignItems: 'flex-start' }}>
                      <span style={{
                        width: 6, height: 6, borderRadius: 3, marginTop: 6, flex: '0 0 auto',
                        background: w.tone === 'bad' ? STATUS.critical.fill
                                  : w.tone === 'warn' ? STATUS.warning.fill
                                  : w.tone === 'ok' ? STATUS.good.fill : INK_3,
                      }} />
                      <span style={{ fontSize: 12.2, lineHeight: 1.45, color: w.tone === 'flat' ? INK_3 : INK_2 }}>
                        {w.t}
                      </span>
                    </div>
                  ))}
                </div>
              )}
              <KV k="Signals firing" v={xps.signal_count} />
              <KV k="Failure Pattern & Cascade Identification subsystem" v={xps.ps2_subsystem} />
              <KV k="Root Cause Analysis subsystem" v={xps.ps3_subsystem} />
              <KV k="Verdict" v={xps.subsystem_verdict} />
              <KV k="Propagation speed" v={xps.propagation_speed} />
              <KV k="Cascades under 15 min" v={xps.pct_cascades_under_15min === undefined || xps.pct_cascades_under_15min === null ? null : pct(xps.pct_cascades_under_15min, 0)} />
            </SourcePanel>
          </Grid>

          {ps5Components.length > 0 && (
            <Panel
              title="Remaining Useful Life & SLA Breach components on this device"
              hint="Serial-numbered parts the survival run scored, worst first. Deduplicated on (device, serial) -- the published table repeats rows and the repeat is a roster fan-out, not a second reading of the part."
            >
              <DataTable
                rows={ps5Components}
                columns={[
                  { key: 'component_serial_nbr', label: 'Serial', width: 150 },
                  { key: 'component_type_name', label: 'Component type', width: 150 },
                  { key: 'risk_tier', label: 'Tier', width: 100 },
                  { key: 'component_age_days', label: 'Age (d)', num: true, d: 0, width: 100 },
                  { key: 'expected_component_rul_days', label: 'Days to next OOS', num: true, d: 1, width: 150 },
                  { key: 'risk_score', label: 'Risk score', num: true, d: 4, width: 110 },
                  {
                    key: 'act_now',
                    label: 'Meets act-now rule',
                    width: 90,
                    render: (r) => (r && r.act_now ? 'yes' : '--'),
                  },
                  { key: 'serial_source', label: 'Source', width: 170 },
                ]}
                height={260}
                pageSize={50}
                searchable={false}
                exportName={`ps5_components_${device}`}
              />
            </Panel>
          )}

          <Panel
            title="Failure Pattern & Cascade Identification v2.5 deterioration"
            hint={`${nfmt(deter.length)} flagged device-days for this device, against its own 28-day baseline`}
          >
            {deter.length
              ? (
                <>
                  {/* The z-score is the reading that matters: how far this
                      device sat from its OWN 28-day baseline, not from the
                      fleet's. Plotted before the table because a shape shows a
                      deterioration trend that forty rows of numbers do not. */}
                  <Trend
                    data={[...deter]
                      .sort((a, b) => String(a.event_date).localeCompare(String(b.event_date)))
                      .map((r) => ({
                        event_date: String(r.event_date).slice(0, 10),
                        z: Number(r.oos_zscore_28d),
                        onsets: Number(r.hardware_oos_onsets) || 0,
                      }))}
                    xKey="event_date"
                    series={[
                      { key: 'z', label: 'Deviation from its own 28-day baseline', color: STATUS.serious.fill },
                      { key: 'onsets', label: 'OOS onsets that day', color: CAT[0] },
                    ]}
                    height={240}
                    fmt={_fmt1}
                  />
                  <div style={{ height: 12 }} />
                  <DataTable rows={deter} columns={deterCols} height={300} pageSize={50} searchable={false} exportName={`ps2_deterioration_${device}`} />
                </>
              )
              : <Empty height={120}>No flagged device-days for this device in the Failure Pattern & Cascade Identification v2.5 run.</Empty>}
          </Panel>
        </>
      )}
      {brief && d360 && (
        <Device360Popup city={city}
                        preloaded={{ ...d360, device_id: d360.device_id || device }}
                        deviceId={d360.device_id || device}
                        onClose={() => setBrief(false)}
                        onOpenDevice={() => setBrief(false)} />
      )}
    </>
  );
}

// FONTS_SCALED 04-Aug-2026
