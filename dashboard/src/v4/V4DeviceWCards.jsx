// =====================================================================
// V4DeviceWCards -- the renderers for V4DeviceW on the full Device 360 tab:
// four device-level cards (WHERE / WHEN / WHAT / WHY) and the per-serial
// component table. All data shaping is in V4DeviceW.js; nothing here reads
// the API shapes directly, so the popup and this tab cannot drift.
// =====================================================================
import { Card, Grid, Panel } from './V4Kit';
import DataTable from './V4DataTable';
import { INK, INK_2, INK_3, STATUS, TAB_COLOR, deviceShort, dfmt, nfmt, pct } from './V4theme';
import { deviceProfile, driverLabel } from './V4DeviceW';

const has = (v) => v !== null && v !== undefined && v !== '' && v !== '--' && v !== 'None' && v !== 'nan' && v !== 'NaN';

// ---------------------------------------------------------------------
// Renderers for the full tab.
// ---------------------------------------------------------------------
function Line({ k, v, tone }) {
  if (!has(v)) return null;
  return (
    <div style={{ display: 'flex', justifyContent: 'space-between', gap: 14, fontSize: 12.6, padding: '3px 0' }}>
      <span style={{ color: INK_2 }}>{k}</span>
      <span style={{ color: tone === 'warn' ? STATUS.warning.fill : INK, fontWeight: 600, textAlign: 'right', wordBreak: 'break-all' }}>{String(v)}</span>
    </div>
  );
}

function WCard({ tag, headline, children, foot }) {
  const accent = TAB_COLOR.device;
  return (
    <Card pad="14px 16px" accent={accent}>
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 9 }}>
        <span style={{ fontSize: 11, fontWeight: 800, letterSpacing: '.1em', color: '#FFFFFF', background: accent, borderRadius: 5, padding: '2px 7px', lineHeight: 1.3 }}>{tag}</span>
        <span style={{ fontSize: 12.6, fontWeight: 700, color: INK, minWidth: 0 }}>{headline}</span>
      </div>
      <div style={{ marginTop: 10 }}>{children}</div>
      {foot && <div style={{ fontSize: 11.2, color: INK_3, marginTop: 10, lineHeight: 1.5 }}>{foot}</div>}
    </Card>
  );
}

const warnTier = (t) => (['CRITICAL', 'HIGH'].includes(String(t || '').toUpperCase()) ? 'warn' : undefined);
const yn = (b) => (b === null || b === undefined ? null : (b ? 'yes' : 'no'));
const shortId = (s) => (has(s) && String(s).length > 16 ? `${String(s).slice(0, 8)}…${String(s).slice(-4)}` : s);

export function DeviceWCards({ agg, d360, loc }) {
  const p = deviceProfile(agg, d360);
  const { where, when, what, why } = p;
  const station = has(where.facility_name)
    ? where.facility_name
    : (loc && loc.known(where.facility_id) ? loc.name(where.facility_id) : (has(where.facility_id) ? `facility ${where.facility_id}` : 'Station not recorded'));
  const agree = what.signal_count !== null
    ? `${nfmt(what.signal_count)} of ${what.signal_of} analyses flag it`
    : (what.xps_signal_count !== null ? `${nfmt(what.xps_signal_count)} signals firing` : 'No headline yet');
  const drivers = why.drivers;
  const dmax = drivers.length ? Math.max(...drivers.map((x) => x.shap)) || 1 : 1;
  const whyHead = why.components.length
    ? `${why.components[0].label} carries the incidents`
    : (has(why.subsystem_ps3) ? `${why.subsystem_ps3} subsystem` : (drivers.length ? driverLabel(drivers[0].feature) : 'No attribution yet'));

  return (
    <Grid cols="repeat(auto-fit,minmax(250px,1fr))">
      <WCard tag="WHERE" headline={station}
             foot={has(where.source_date) ? `Identity from the device dimension as of ${dfmt(where.source_date)}.` : undefined}>
        <Line k="Station" v={has(where.facility_id) && has(where.facility_name) ? `${where.facility_name} (${where.facility_id})` : where.facility_id} />
        <Line k="Operator" v={where.operator} />
        <Line k="Mode" v={where.mode} />
        <Line k="Bus" v={where.bus_id} />
        <Line k="Fleet" v={has(where.fleet) ? `${deviceShort(where.fleet)}${has(where.type) && String(where.type).toUpperCase() !== String(where.fleet).toUpperCase() ? ` · ${where.type}` : ''}` : where.type} />
        <Line k="Serial" v={where.serial} />
        <Line k="Component type" v={where.component_type} />
        <Line k="CMDB item" v={where.cmdb_ci ? shortId(where.cmdb_ci) : 'not mapped'} tone={where.cmdb_ci ? undefined : 'warn'} />
      </WCard>

      <WCard tag="WHEN" headline={has(when.ps1_scored) ? `Scored to ${dfmt(when.ps1_scored)}` : 'No scoring date'}
             foot={when.vintage_spread !== null && when.vintage_spread > 0
               ? `Sources span ${nfmt(when.vintage_spread)} days (${dfmt(when.vintage_oldest)} to ${dfmt(when.vintage_newest)}). This row mixes vintages; each date above says whose.`
               : undefined}>
        <Line k="Failure prediction, last scored" v={has(when.ps1_scored) ? dfmt(when.ps1_scored) : null} />
        <Line k="Last failure in its window" v={has(when.ps1_last_failure) ? dfmt(when.ps1_last_failure) : null} />
        <Line k="Latest attributed incident" v={has(when.ps3_latest_incident) ? dfmt(when.ps3_latest_incident) : null} />
        <Line k="Anomaly week scored" v={has(when.ps4_week) ? dfmt(when.ps4_week) : null} />
        <Line k="Remaining life snapshot" v={has(when.ps5_asof) ? dfmt(when.ps5_asof) : null} />
        <Line k="Latest ServiceNow ticket" v={has(when.sn_latest) ? dfmt(when.sn_latest) : null} />
        <Line k="Device dimension built" v={has(when.dim_asof) ? dfmt(when.dim_asof) : null} />
      </WCard>

      <WCard tag="WHAT" headline={agree}
             foot="Agreement counts Root Cause Analysis, Anomaly, Remaining Life and ServiceNow. Failure Prediction is shown but not counted: its label has not passed its gate.">
        <Line k="Failure probability, 3 days (latest scored day)" v={what.ps1_prob !== null ? `${pct(what.ps1_prob, 1)}${has(what.ps1_tier) ? ` · ${what.ps1_tier}` : ''}` : null} tone={warnTier(what.ps1_tier)} />
        <Line k="Severity action band" v={what.ps3_action} tone={/P1|P2/.test(String(what.ps3_action || '')) ? 'warn' : undefined} />
        <Line k="Predicted severity" v={what.ps3_severity} />
        <Line k="OOS incidents on its serial" v={what.ps3_incidents !== null ? `${nfmt(what.ps3_incidents)}${what.ps3_recurrence_30d !== null ? ` (${nfmt(what.ps3_recurrence_30d)} in 30 d)` : ''}` : null} />
        <Line k="Anomaly, latest week" v={has(what.ps4_severity) ? `${what.ps4_severity}${what.ps4_actionable ? ' · actionable' : ''}${has(what.ps4_types) ? ` · ${what.ps4_types}` : ''}` : null} tone={String(what.ps4_severity || '').toLowerCase() !== 'normal' && has(what.ps4_severity) ? 'warn' : undefined} />
        <Line k="Remaining life band" v={what.ps5_band} tone={warnTier(what.ps5_band)} />
        <Line k="Days to next OOS" v={what.ps5_rul !== null ? `${nfmt(what.ps5_rul, 1)}${what.ps5_overdue ? ' · past typical interval' : ''}` : null} tone={what.ps5_overdue ? 'warn' : undefined} />
        <Line k="Cascade-active days" v={what.ps2_cascade_days} />
        <Line k="ServiceNow tickets on CI" v={what.sn_count !== null ? `${nfmt(what.sn_count)}${has(what.sn_latest) ? ` · latest ${what.sn_latest}` : ''}` : null} />
      </WCard>

      <WCard tag="WHY" headline={whyHead}
             foot={`${why.attribution_note} ${why.linked_note}`}>
        {drivers.length > 0 && (
          <div style={{ marginBottom: 8 }}>
            <div style={{ fontSize: 11, color: INK_3, letterSpacing: '.06em', textTransform: 'uppercase', marginBottom: 4 }}>Model drivers, failure prediction</div>
            {drivers.map((x, i) => (
              <div key={i} style={{ marginBottom: 6 }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 12, color: INK_2 }}>
                  <span>{driverLabel(x.feature)}</span><span style={{ color: INK_3 }}>{nfmt(x.shap, 2)}</span>
                </div>
                <div style={{ height: 5, background: '#F1F2F4', borderRadius: 3 }}>
                  <div style={{ width: `${(x.shap / dmax) * 100}%`, height: '100%', background: TAB_COLOR.device, borderRadius: 3 }} />
                </div>
              </div>
            ))}
          </div>
        )}
        {why.components.length > 0 && (
          <div style={{ marginBottom: 6 }}>
            <div style={{ fontSize: 11, color: INK_3, letterSpacing: '.06em', textTransform: 'uppercase', marginBottom: 2 }}>Components attributed, observed</div>
            {why.components.map((c, i) => (
              <Line key={i} k={`${c.label} · ${c.serial}`} v={`${nfmt(c.incidents)} incident${c.incidents === 1 ? '' : 's'}${c.recurrence_30d ? `, ${nfmt(c.recurrence_30d)} in 30 d` : ''}`} />
            ))}
          </div>
        )}
        <Line k="Root Cause Analysis subsystem" v={why.subsystem_ps3} />
        <Line k="Cascade subsystem" v={why.subsystem_ps2} />
        <Line k="Corroboration" v={has(why.subsystem_verdict) ? String(why.subsystem_verdict).replace(/_/g, ' ') : null} />
        {!drivers.length && !why.components.length && !has(why.subsystem_ps3) && !has(why.subsystem_ps2) && (
          <div style={{ fontSize: 12.2, color: INK_3 }}>No analysis has attributed a cause to this device.</div>
        )}
      </WCard>
    </Grid>
  );
}

export function ComponentWTable({ rows, device }) {
  if (!rows || !rows.length) return null;
  const columns = [
    { key: 'serial', label: 'Serial', width: 165 },
    { key: 'labels_text', label: 'Components attributed (incidents)', flex: 1.4, render: (r) => r.labels_text || '—' },
    { key: 'component_type', label: 'Type', width: 96, render: (r) => (has(r.component_type) ? String(r.component_type).toUpperCase() : '—') },
    { key: 'incidents', label: 'Incidents', num: true, width: 90, render: (r) => (r.has_ps3 ? nfmt(r.incidents) : '—') },
    { key: 'critical_rate', label: 'Critical', num: true, width: 80, render: (r) => (r.critical_rate === null ? '—' : pct(r.critical_rate, 0)) },
    { key: 'recurrence_30d', label: 'In 30 d', num: true, width: 76, render: (r) => (r.has_ps3 ? nfmt(r.recurrence_30d) : '—') },
    { key: 'latest_incident_at', label: 'Latest incident', width: 118, render: (r) => (has(r.latest_incident_at) ? dfmt(r.latest_incident_at) : '—') },
    { key: 'age_days', label: 'Age (d)', num: true, width: 78, render: (r) => (r.age_days === null ? '—' : nfmt(r.age_days)) },
    { key: 'risk_tier', label: 'Life band', width: 90, render: (r) => r.risk_tier || '—' },
    { key: 'rul_days', label: 'Days to next OOS', num: true, width: 128, render: (r) => (r.rul_days === null ? '—' : nfmt(r.rul_days, 1)) },
    { key: 'is_overdue', label: 'Overdue', width: 76, render: (r) => yn(r.is_overdue) || '—' },
    { key: 'act_now', label: 'Act now', width: 72, render: (r) => (r.act_now ? 'yes' : '—') },
    { key: 'basis', label: 'Basis (why)', width: 200, render: (r) => r.basis || '—' },
  ];
  return (
    <Panel
      title="Components on this device: where, when, what, why"
      hint={`One row per serial-numbered part on ${device || 'this device'}. Where: this device. When: latest attributed incident and part age. What: incident load from Root Cause Analysis and remaining life from the survival run, joined on the serial. Why: the basis column. Attribution is observed from incident history, not a confirmed root cause; a serial with several attributed components lists them all rather than repeating its remaining life per component.`}
    >
      <DataTable rows={rows} columns={columns} height={Math.min(320, 52 + rows.length * 38)} pageSize={50} searchable={false} exportName={`components_w_${device || 'device'}`} />
    </Panel>
  );
}
