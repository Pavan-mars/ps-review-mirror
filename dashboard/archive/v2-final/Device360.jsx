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
import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { getObj, getRows, post, ps5 } from './v2api';
import { Badge, Card, Chip, Empty, Grid, Loading, Note, Panel, Section, TextField, Toolbar, Rule, Tabs, V2Style,
} from './Kit';
import DataTable from './DataTable';
import DeviceBrief from './DeviceBrief';
import { Trend } from './Charts';
import { CARD, CAT, INK, INK_2, INK_3, LINE, STATUS, deviceColor, deviceShort, dfmt, font, nfmt, pct, A, TAB_COLOR, tint,
} from './theme';

const num = (v) => (v === null || v === undefined || Number.isNaN(Number(v)) ? 0 : Number(v));

function KV({ k, v, tone }) {
  // The API stringifies Python None in a few text columns, so 'None'/'nan'
  // arrive as literal strings. Treat them as absent rather than printing them.
  if (v === 'None' || v === 'nan' || v === 'NaN') return null;
  if (Array.isArray(v) && v.length === 0) return null;
  if (v === null || v === undefined || v === '') return null;
  return (
    <div style={{ display: 'flex', justifyContent: 'space-between', gap: 14, fontSize: 14, padding: '3px 0' }}>
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
            fontSize: 11.5, fontWeight: 800, letterSpacing: '.08em', color: '#FFFFFF',
            background: accent, borderRadius: 5, padding: '2px 6px', lineHeight: 1.3,
          }}
        >
          {ps}
        </span>
        <span style={{ fontSize: 14, fontWeight: 700, color: INK }}>{title}</span>
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
    fontSize: 14.5, color: INK, background: CARD, minWidth: 130, outline: 'none',
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
          <option value="ALL">All depots</option>
          {facilities.map((x) => <option key={x} value={x}>Depot {x}</option>)}
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
              {m.device_id} - depot {m.facility_id}{m.risk_band ? ` - ${m.risk_band}` : ''}
            </option>
          ))}
        </select>
      </Toolbar>
      {state.error && (
        <div style={{ ...font.note, fontSize: 13.5, marginTop: 8 }}>
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
  // The brief opens by itself the first time a device loads. Someone who
  // typed a device id wants the answer, not a page of panels with the
  // answer somewhere in it. Closing it leaves the full record underneath.
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
      // Two calls, deliberately sequential-ish: the 360 is the spine, the Failure Pattern & Cascade Identification
      // v2.5 deterioration rows are the supplement. A failure in the second
      // must not blank the first.
      const spine = await getObj('/ps1/device-360', { city, device_id: dev });
      let rows = [];
      try { rows = await getRows('/ps2/v25/deterioration', { city, device: dev, limit: 2000 }); }
      catch (e) { rows = []; }
      setD360(spine || {});
      setBrief(true);
      setDeter(rows || []);
      setState({ loading: false, error: null });
    } catch (e) {
      setState({ loading: false, error: String((e && e.message) || e) });
    }
  }, [city]);

  // React to a device pushed in from another screen. Device 360 previously
  // took initialDevice as seed state only, so arriving from an Analyse modal
  // landed on an empty search box with the id in the URL and nothing loaded.
  useEffect(() => {
    const d = String(initialDevice || '').trim().toUpperCase();
    if (d && d !== device) { setQ(d); load(d); }
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
                padding: '7px 14px', fontSize: 14, fontWeight: 600, color: INK, cursor: 'pointer',
              }}
            >
              Look up
            </button>
          </div>
        }
      />

      {!device && <Note>Enter a device id above. Ids look like RVG06302 (fare gate), BMV03868 (bus validator) or TVM03402.</Note>}

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
              <span style={{ fontSize: 22, fontWeight: 800, color: INK }}>{d360.device_id || device}</span>
              {ps1.device_category && <Badge tone="neutral">{deviceShort(ps1.device_category)}</Badge>}
              {ps1.facility_id && <span style={{ ...font.micro }}>facility {ps1.facility_id}</span>}
              {(d360.bus_identity || {}).bus_label && <span style={{ ...font.micro }}>{d360.bus_identity.bus_label}</span>}
              <span style={{ marginLeft: 'auto' }}>
                <button
                  type="button"
                  onClick={stageTicket}
                  style={{
                    border: `1px solid ${LINE}`, background: CARD, borderRadius: 9,
                    padding: '6px 13px', fontSize: 14, fontWeight: 600, color: INK, cursor: 'pointer',
                  }}
                >
                  Stage a ServiceNow record
                </button>
              </span>
            </div>
            {d360.recommendation && (
              <div style={{ fontSize: 14.5, color: INK_2, marginTop: 10, lineHeight: 1.5 }}>{d360.recommendation}</div>
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
              absence="Not recorded as an out-of-service window during failure device prediction. This device's failure history is in Remaining Useful Life & SLA Breach Remaining life."
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
                  <KV k="Remaining life (days)" v={ps5.rul_standard_days === undefined || ps5.rul_standard_days === null ? null : nfmt(ps5.rul_standard_days, 1)} />
                  <KV k="Median survival (days)" v={ps5.predicted_median_survival_days === undefined || ps5.predicted_median_survival_days === null ? null : nfmt(ps5.predicted_median_survival_days, 1)} />
                  <KV k="Past expected life" v={ps5.is_overdue === undefined ? null : (ps5.is_overdue ? 'yes' : 'no')} tone={ps5.is_overdue ? 'warn' : undefined} />
                  {/* Rank is WITHIN the fleet. Printed as "n of N" rather than
                      as a bare rank so it cannot be read across device types --
                      the three survival models are fitted separately. */}
                  <KV
                    k="Rank in its own fleet"
                    v={ps5.rul_rank_in_type ? `${nfmt(ps5.rul_rank_in_type)} of ${nfmt(ps5.n_devices_in_type)}` : null}
                  />
                  <KV k="Healthy age (days)" v={ps5.current_healthy_age_days === undefined || ps5.current_healthy_age_days === null ? null : nfmt(ps5.current_healthy_age_days)} />
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
              {/* The signals array is the substance of this panel and was not
                  rendered -- only its length was. "1 signal firing" tells a
                  reader nothing; "Failure Pattern & Cascade Identification cascade rank #2031" tells them where to
                  look next. */}
              {Array.isArray(xps.signals) && xps.signals.length > 0 && (
                <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, margin: '2px 0 10px' }}>
                  {xps.signals.map((sg, i) => (
                    <span key={i} style={{
                      border: `1px solid ${LINE}`, background: '#F8FAFC', borderRadius: 999,
                      padding: '3px 10px', fontSize: 12.5, color: INK_2, fontWeight: 600,
                    }}>{String(sg)}</span>
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
                  { key: 'component_type_name', label: 'Component', width: 140 },
                  { key: 'risk_tier', label: 'Tier', width: 100 },
                  { key: 'component_age_days', label: 'Age (d)', num: true, d: 0, width: 100 },
                  { key: 'expected_component_rul_days', label: 'Remaining (d)', num: true, d: 1, width: 120 },
                  { key: 'risk_score', label: 'Risk score', num: true, d: 4, width: 110 },
                  {
                    key: 'act_now',
                    label: 'Act now',
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
                    fmt={(v) => Number(v).toFixed(2)}
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
        <DeviceBrief data={{ ...d360, device_id: d360.device_id || device }}
                     onClose={() => setBrief(false)}
                     onFull={() => setBrief(false)} />
      )}
    </>
  );
}

// FONTS_SCALED 04-Aug-2026
