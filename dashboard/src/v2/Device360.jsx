// =====================================================================
// v2/Device360.jsx -- one device, everything the platform knows about it.
//
// MOST OF THIS IS ONE CALL. /ps1/device-360 already returns ps1, ps1_state,
// ps2, ps3, ps4, ps5, ps3_v2_rootcause_360, ps4_v3_360, cross_ps, causation,
// a recommendation string and a ServiceNow payload. Rebuilding that would be
// duplicating work that is already deployed and already correct.
//
// WHAT IT DOES NOT COVER, AND WHY THERE IS A SECOND CALL.
// That endpoint predates the PS2 v2.5 generation. Its `ps2` block is the older
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
import React, { useCallback, useState } from 'react';
import { getObj, getRows, post } from './v2api';
import { Badge, Card, Empty, Grid, Loading, Note, Panel, Section, TextField } from './Kit';
import DataTable from './DataTable';
import { CARD, INK, INK_2, INK_3, LINE, STATUS, deviceShort, dfmt, font, nfmt, pct } from './theme';

const num = (v) => (v === null || v === undefined || Number.isNaN(Number(v)) ? 0 : Number(v));

function KV({ k, v, tone }) {
  // The API stringifies Python None in a few text columns, so 'None'/'nan'
  // arrive as literal strings. Treat them as absent rather than printing them.
  if (v === 'None' || v === 'nan' || v === 'NaN') return null;
  if (Array.isArray(v) && v.length === 0) return null;
  if (v === null || v === undefined || v === '') return null;
  return (
    <div style={{ display: 'flex', justifyContent: 'space-between', gap: 14, fontSize: 12.5, padding: '3px 0' }}>
      <span style={{ color: INK_2 }}>{k}</span>
      <span style={{ color: tone === 'warn' ? STATUS.warning : INK, fontWeight: 600, textAlign: 'right' }}>{String(v)}</span>
    </div>
  );
}

function SourcePanel({ ps, title, found, children, note }) {
  return (
    <Card pad="14px 16px">
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 9 }}>
        <span style={{ fontSize: 10.5, fontWeight: 800, letterSpacing: '.08em', color: INK_3 }}>{ps}</span>
        <span style={{ fontSize: 14, fontWeight: 700, color: INK }}>{title}</span>
        {found === false && <Badge tone="neutral">no record</Badge>}
      </div>
      <div style={{ marginTop: 10 }}>
        {found === false ? <Empty height={60}>This device does not appear in {ps}.</Empty> : children}
      </div>
      {note && <div style={{ ...font.micro, marginTop: 10, lineHeight: 1.5 }}>{note}</div>}
    </Card>
  );
}

export default function Device360({ city = 'CHI', initialDevice = '' }) {
  const [q, setQ] = useState(initialDevice);
  const [device, setDevice] = useState(initialDevice);
  const [d360, setD360] = useState(null);
  const [deter, setDeter] = useState([]);
  const [state, setState] = useState({ loading: false, error: null });
  const [staged, setStaged] = useState(null);

  const load = useCallback(async (id) => {
    const dev = String(id || '').trim().toUpperCase();
    if (!dev) return;
    setDevice(dev);
    setState({ loading: true, error: null });
    setD360(null); setDeter([]); setStaged(null);
    try {
      // Two calls, deliberately sequential-ish: the 360 is the spine, the PS2
      // v2.5 deterioration rows are the supplement. A failure in the second
      // must not blank the first.
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
  // The legacy PS4 block and the v3 block can disagree. Say so rather than
  // picking one -- the disagreement is the finding.
  const ps4Note = ps4Weeks.length && Number(ps4.alert_count || 0) === 0
    ? 'The earlier PS4 export reports no alert weeks for this device while the v3 weekly scoring does. Both are shown; the v3 numbers are the current generation.'
    : undefined;

  const ps5Rel = (ps5 && ps5.reliability) || null;

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
      <Section
        eyebrow="Device 360"
        title="One device, across every problem statement"
        sub="Each section states which analysis produced it. Where two disagree, both are shown -- the disagreement is the finding."
        right={
          <div style={{ display: 'flex', gap: 8, alignItems: 'center', minWidth: 320 }}>
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
                padding: '7px 14px', fontSize: 12.5, fontWeight: 600, color: INK, cursor: 'pointer',
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
                    padding: '6px 13px', fontSize: 12.5, fontWeight: 600, color: INK, cursor: 'pointer',
                  }}
                >
                  Stage a ServiceNow record
                </button>
              </span>
            </div>
            {d360.recommendation && (
              <div style={{ fontSize: 13, color: INK_2, marginTop: 10, lineHeight: 1.5 }}>{d360.recommendation}</div>
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
            <SourcePanel ps="PS1" title="Failure prediction" found={ps1.found}>
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
              ps="PS2" title="Cascading failure (previous generation)"
              found={ps2.in_catalog === true || ps2.in_top_devices === true}
              note="From the earlier PS2 export. The v2.5 deterioration signal is below and was computed by a different notebook."
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

            <SourcePanel ps="PS3" title="Root cause and severity" found={ps3.found}>
              <KV k="Dominant severity" v={ps3.dominant_pred_severity} />
              <KV k="Dominant component" v={ps3.dominant_pred_component} />
              <KV k="Average component age" v={ps3.avg_component_age_days ? `${nfmt(ps3.avg_component_age_days)} days` : null} />
              <KV k="Last incident" v={ps3.last_incident_dtm ? dfmt(ps3.last_incident_dtm) : null} />
            </SourcePanel>

            <SourcePanel
              ps="PS4" title="Anomaly detection"
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
              ps="PS5" title="Remaining useful life"
              found={ps5.found === false && !ps5Rel ? false : undefined}
              note={ps5.note}
            >
              <KV k="Level" v={ps5.level} />
              <KV k="Category" v={ps5.category} />
              <KV k="Concordance index" v={ps5Rel && ps5Rel.concordance_index !== undefined ? nfmt(ps5Rel.concordance_index, 3) : null} />
              <KV k="Registry status" v={ps5Rel ? ps5Rel.registry_status : null} />
              <KV k="Dashboard ready" v={ps5Rel ? (ps5Rel.dashboard_ready === true ? 'yes' : 'no') : null} />
              <KV k="Blockers" v={ps5Rel ? ps5Rel.blockers : null} />
            </SourcePanel>

            <SourcePanel ps="CROSS" title="Where the analyses agree">
              <KV k="Signals firing" v={xps.signal_count} />
              <KV k="PS2 subsystem" v={xps.ps2_subsystem} />
              <KV k="PS3 subsystem" v={xps.ps3_subsystem} />
              <KV k="Verdict" v={xps.subsystem_verdict} />
              <KV k="Propagation speed" v={xps.propagation_speed} />
              <KV k="Cascades under 15 min" v={xps.pct_cascades_under_15min === undefined || xps.pct_cascades_under_15min === null ? null : pct(xps.pct_cascades_under_15min, 0)} />
            </SourcePanel>
          </Grid>

          <Panel
            title="PS2 v2.5 deterioration"
            hint={`${nfmt(deter.length)} flagged device-days for this device, against its own 28-day baseline`}
          >
            {deter.length
              ? <DataTable rows={deter} columns={deterCols} height={300} pageSize={50} searchable={false} exportName={`ps2_deterioration_${device}`} />
              : <Empty height={120}>No flagged device-days for this device in the PS2 v2.5 run.</Empty>}
          </Panel>
        </>
      )}
    </>
  );
}
