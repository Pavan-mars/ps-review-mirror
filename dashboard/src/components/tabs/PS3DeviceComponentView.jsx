import React, { useState, useMemo, useCallback } from 'react';
import AnalyseButton from '../shared/AnalyseButton';
import {
  TCOL, RISK, num, intf, pct, dayOf, hexA, compLabel, compLabelShort,
  SortTh, Pager, NoRows, Panel, Kpi, useSortPage, VirtualTBody,
  ServiceNowButton, ExportButton, AwaitingRun } from '../shared/DashboardKit';

// ============================================================================
// PS3 · Device & Component Risk — the merged view
//
// WHY MASTER-DETAIL AND NOT A JOIN
// --------------------------------
// Device Risk and Component Risk were two sub-tabs at two different grains: 927
// device rows and 2,515 component rows (10,208 once validators are included).
// Flattening one into the other with a join is the obvious merge and the wrong
// one -- every device-grain measure would repeat on each of that device's
// component rows and fan out on sum. That is exactly the trap that inflated the
// fleet event total 2.60x and the TVM incident total to 132,500 against a true
// 32,842 earlier in this programme.
//
// So the merge is master-detail. One row per device, exactly as before, and a
// device's components open underneath it on click. Nothing is summed across the
// boundary, and the two grains stay visibly separate on screen while living in
// one tab.
//
// The flat component list is still reachable through the grain toggle, because
// filtering, sorting and exporting BY COMPONENT is a real task ("show me every
// billacceptor older than 3,000 days") that a nested view cannot serve.
//
// Components load per device on expand rather than up front: the list route
// caps at 300 rows, so a pre-loaded map would silently hold components for only
// the first 300 devices and show a wrongly-empty panel for the rest.
// ============================================================================

const GRAINS = [
  { key: 'device', label: 'By device', hint: 'One row per device; open a row for its components' },
  { key: 'component', label: 'By component', hint: 'One row per component, for filtering and export by component' },
];

export default function DeviceComponentView({
  deviceRows, deviceTotal, deviceState,
  serialRows, serialTotal, serialState,
  bandOf, pctUsable, drill, apiBase, onAnalyse, apiGet, city,
}) {
  const [grain, setGrain] = useState('device');
  const [open, setOpen] = useState({});          // device_id -> true
  const [kids, setKids] = useState({});          // device_id -> {state, rows}

  const toggle = useCallback((id) => {
    setOpen((o) => ({ ...o, [id]: !o[id] }));
    setKids((k) => {
      if (k[id]) return k;                        // already fetched, keep it
      apiGet('/ps3/serial-predictions', { city, device_id: id, limit: 50 })
        .then((d) => setKids((kk) => ({ ...kk, [id]: { state: 'ok', rows: Array.isArray(d) ? d : [] } })))
        .catch(() => setKids((kk) => ({ ...kk, [id]: { state: 'err', rows: [] } })));
      return { ...k, [id]: { state: 'loading', rows: [] } };
    });
  }, [apiGet, city]);

  if (deviceState !== 'ok' && serialState !== 'ok') {
    return <AwaitingRun title="Device and component risk"
      table="ps3_device_predictions / ps3_serial_predictions"
      note="Device grain is one row per device_id; component grain is one row per (device_id, serial). Both are written by the PS3 v2 OOS notebook and loaded by the run file." />;
  }

  return (
    <div>
      <GrainSwitch grain={grain} setGrain={setGrain} />
      {grain === 'device'
        ? <DeviceGrain rows={deviceRows} total={deviceTotal} state={deviceState}
            bandOf={bandOf} pctUsable={pctUsable} drill={drill} apiBase={apiBase}
            onAnalyse={onAnalyse} open={open} toggle={toggle} kids={kids} />
        : <ComponentGrain rows={serialRows} total={serialTotal} state={serialState}
            bandOf={bandOf} drill={drill} apiBase={apiBase} onAnalyse={onAnalyse} />}
    </div>
  );
}

function GrainSwitch({ grain, setGrain }) {
  const active = GRAINS.find((g) => g.key === grain);
  return (
    <div className="filter-bar" style={{ borderRadius: 12, border: '1px solid var(--border)',
      marginBottom: 14, alignItems: 'center' }}>
      <div className="filter-group">
        <label className="filter-label">Grain</label>
        <div style={{ display: 'flex', gap: 4 }}>
          {GRAINS.map((g) => (
            <button key={g.key} type="button" className="filter-btn" onClick={() => setGrain(g.key)}
              title={g.hint}
              style={grain === g.key ? { background: 'var(--primary)', borderColor: 'var(--primary)',
                color: '#fff', fontWeight: 700 } : undefined}>
              {g.label}
            </button>
          ))}
        </div>
      </div>
      <span style={{ fontSize: 12, color: 'var(--text-secondary)', marginLeft: 12 }}>
        {active?.hint}
      </span>
      <span style={{ fontSize: 11, color: 'var(--text-secondary)', marginLeft: 'auto' }}>
        The two grains are never summed together — a device measure repeated on its component rows
        would double count.
      </span>
    </div>
  );
}

// ------------------------------------------------------------ device grain --
function DeviceGrain({ rows, total, state, bandOf, pctUsable, drill, apiBase, onAnalyse,
  open, toggle, kids }) {
  const t = useSortPage(rows, { key: 'n_incidents', dir: 'desc' }, 50);
  const scored = rows.filter((r) => r.coverage_status !== 'not_in_feed');
  const noFeed = rows.length - scored.length;
  const critical = scored.filter((r) => bandOf(r) === 'CRITICAL').length;
  const gated = scored.filter((r) => r.severity_shippable === false
    && r.coverage_status === 'scored').length;
  const incidents = rows.reduce((s, r) => s + Number(r.n_incidents || 0), 0);

  if (state !== 'ok') return <NoRows msg="Device grain not loaded for this run." />;
  return (
    <div>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 16, marginBottom: 18 }}>
        <Kpi label="Devices (in view)" value={intf(rows.length)}
          sub={noFeed > 0 ? `${intf(noFeed)} have no PS3 feed` : `of ${intf(total)} returned`} />
        <Kpi label="Critical" value={intf(critical)} color={RISK.CRITICAL}
          title="Counted over devices PS3 can score. A device type with no availability-event feed has no severity and is excluded rather than counted as safe."
          sub={pctUsable ? '≥ 60% predicted critical' : 'dominant class collapses to CRITICAL'} />
        <Kpi label="OOS incidents" value={intf(incidents)} sub="attributed in view" />
        <Kpi label="Gated" value={intf(gated)} sub="head below macro-F1 floor" color="var(--text-secondary)" />
      </div>

      {noFeed > 0 && (
        <div className="card" style={{ marginBottom: 14, padding: '10px 14px', borderLeft: '4px solid #f59e0b' }}>
          <span style={{ fontSize: 12, color: 'var(--text-secondary)', lineHeight: 1.6 }}>
            <strong>{intf(noFeed)} device(s) here have no failure severity.</strong>{' '}
            Their device type emits no ServiceNow availability events, so PS3 has nothing to score. Their
            serials, components and ages are real; the model columns are deliberately blank. Shown as
            unknown, never as zero — a blank severity is not a safe severity.
          </span>
        </div>
      )}

      <Panel title="Device risk — click a row to open its components"
        right={<ExportButton rows={t.sorted} filename="ps3_device_risk.csv" />}
        note="One row per device. Opening a row fetches that device's components at their own grain — the two are never summed together. Staging records the payload in servicenow_staging for review; it does not post to ServiceNow.">
        <table className="data-table">
          <thead>
            <tr>
              <th style={{ width: 28 }} />
              <SortTh label="Device" col="device_id" sort={t.sort} setSort={t.setSort} />
              <SortTh label="Type" col="mars_device_category" sort={t.sort} setSort={t.setSort} />
              <SortTh label="OOS incidents" col="n_incidents" sort={t.sort} setSort={t.setSort} align="right" />
              <SortTh label="Critical share" col="pct_critical_pred" sort={t.sort} setSort={t.setSort} align="right" />
              <th>Severity</th>
              <SortTh label="Dominant class" col="dominant_pred_severity" sort={t.sort} setSort={t.setSort} />
              <SortTh label="Attributed subsystem" col="dominant_pred_component" sort={t.sort} setSort={t.setSort} />
              <SortTh label="Serials" col="n_serials" sort={t.sort} setSort={t.setSort} align="right" />
              <SortTh label="Mean age (d)" col="avg_component_age_days" sort={t.sort} setSort={t.setSort} align="right" />
              <SortTh label="Last incident" col="last_incident_dtm" sort={t.sort} setSort={t.setSort} />
              <th>Action</th>
            </tr>
          </thead>
          {/* Plain tbody, not VirtualTBody: a virtualised body renders a fixed
              window of equal-height rows, and an expanded detail row is neither.
              Paging already bounds the row count here. */}
          <tbody>
            {t.slice.map((d) => {
              const gatedRow = d.severity_shippable === false;
              const noFeedRow = d.coverage_status === 'not_in_feed';
              const isOpen = !!open[d.device_id];
              const kid = kids[d.device_id];
              return (
                <React.Fragment key={`${d.device_id}-${d.mars_device_category}`}>
                  <tr style={noFeedRow ? { background: hexA('#f59e0b', 0.04) } : undefined}>
                    <td style={{ cursor: 'pointer', textAlign: 'center', userSelect: 'none',
                      fontWeight: 700, color: 'var(--primary)' }}
                      onClick={() => toggle(d.device_id)}
                      title={isOpen ? 'Hide components' : 'Show this device’s components'}>
                      {isOpen ? '▾' : '▸'}
                    </td>
                    <td style={{ fontFamily: 'monospace', fontWeight: 600, cursor: 'pointer' }}
                      onClick={() => drill.push('device', d.device_id, `device: ${d.device_id}`)}
                      title="Drill the whole tab into this device">{d.device_id}</td>
                    <td>
                      <span className="badge badge-info" style={{
                        background: hexA(TCOL[d.mars_device_category] || '#64748b', 0.12),
                        color: TCOL[d.mars_device_category] || '#64748b' }}>{d.mars_device_category}</span>
                    </td>
                    <td style={{ textAlign: 'right' }}>
                      {noFeedRow
                        ? <span style={{ color: '#b45309', fontSize: 11 }}
                            title="This device type emits no availability events, so PS3 has no incident count. Unknown, not zero.">not in feed</span>
                        : intf(d.n_incidents)}
                    </td>
                    <td style={{ textAlign: 'right', color: 'var(--text-secondary)' }}>
                      {noFeedRow || gatedRow ? '—' : (pctUsable ? pct(d.pct_critical_pred) : 'n/a')}
                    </td>
                    <td>{noFeedRow
                      ? <span style={{ color: 'var(--text-secondary)', fontSize: 11 }}>no severity model</span>
                      : <BandDot band={bandOf(d)} gated={gatedRow} />}</td>
                    <td>{noFeedRow ? '—' : (gatedRow
                      ? <span style={{ color: 'var(--text-secondary)' }}>gated</span>
                      : (d.dominant_pred_severity || '—'))}</td>
                    <td style={{ cursor: d.dominant_pred_component ? 'pointer' : 'default' }}
                      onClick={d.dominant_pred_component
                        ? () => drill.push('component', d.dominant_pred_component,
                            `subsystem: ${compLabelShort(d.dominant_pred_component)}`) : undefined}
                      title={d.dominant_pred_component ? compLabel(d.dominant_pred_component) : undefined}>
                      {d.rootcause_shippable === false
                        ? <span style={{ color: 'var(--text-secondary)' }}>gated</span>
                        : compLabelShort(d.dominant_pred_component)}
                    </td>
                    <td style={{ textAlign: 'right' }}>{intf(d.n_serials)}</td>
                    <td style={{ textAlign: 'right' }}>{num(d.avg_component_age_days, 0)}</td>
                    <td style={{ fontSize: 12, color: 'var(--text-secondary)' }}>
                      {noFeedRow ? <span style={{ color: '#b45309' }}>not in feed</span> : dayOf(d.last_incident_dtm)}
                    </td>
                    <td>
                      <div style={{ display: 'flex', gap: 6 }}>
                        <AnalyseButton compact onClick={() => onAnalyse(d.device_id)}
                          title="Open Device 360 — this device across PS1 to PS5, including its PS2 cascade history" />
                        <ServiceNowButton apiBase={apiBase} psId="ps3" compact
                          deviceId={d.device_id} deviceCategory={d.mars_device_category}
                          shortDescription={`PS3 ${d.mars_device_category} ${d.device_id}: ${d.n_incidents ?? 'no'} OOS incidents, dominant ${d.dominant_pred_component || 'component unknown'}`}
                          payload={{ ps: 'PS3', grain: 'device', ...d }} />
                      </div>
                    </td>
                  </tr>
                  {isOpen && (
                    <tr>
                      <td colSpan={12} style={{ padding: 0, background: hexA('#6366f1', 0.03) }}>
                        <ComponentDetail kid={kid} drill={drill} deviceId={d.device_id} />
                      </td>
                    </tr>
                  )}
                </React.Fragment>
              );
            })}
          </tbody>
        </table>
        <Pager page={t.page} pages={t.pages} total={t.sorted.length} setPage={t.setPage}
          label="devices" pageSize={t.pageSize} setPageSize={t.setPageSize} />
      </Panel>
    </div>
  );
}

// The detail band under an opened device row.
function ComponentDetail({ kid, drill, deviceId }) {
  if (!kid || kid.state === 'loading') {
    return <div style={{ padding: '10px 16px', fontSize: 12, color: 'var(--text-secondary)' }}>
      Loading components for {deviceId}…</div>;
  }
  if (kid.state === 'err') {
    return <div style={{ padding: '10px 16px', fontSize: 12, color: '#b45309' }}>
      Could not load components for {deviceId}.</div>;
  }
  if (!kid.rows.length) {
    return <div style={{ padding: '10px 16px', fontSize: 12, color: 'var(--text-secondary)' }}>
      No component rows recorded against {deviceId}.</div>;
  }
  return (
    <div style={{ padding: '10px 16px 14px 40px' }}>
      <div style={{ fontSize: 11, fontWeight: 700, letterSpacing: 0.4, textTransform: 'uppercase',
        color: 'var(--text-secondary)', marginBottom: 6 }}>
        {kid.rows.length} component{kid.rows.length === 1 ? '' : 's'} fitted to {deviceId}
      </div>
      <table className="data-table" style={{ fontSize: 12 }}>
        <thead>
          <tr>
            <th>Component name</th><th>Serial</th>
            <th style={{ textAlign: 'right' }}>Age (d)</th>
            <th>Attributed subsystem</th>
            <th style={{ textAlign: 'right' }}>Incidents</th>
            <th>Last incident</th>
          </tr>
        </thead>
        <tbody>
          {kid.rows.map((c) => (
            <tr key={`${c.device_id}-${c.matched_serial_nbr}`}>
              <td style={{ fontWeight: 600, cursor: c.component_description ? 'pointer' : 'default' }}
                onClick={c.component_description
                  ? () => drill.push('installed_component', c.component_description,
                      `installed: ${c.component_description}`) : undefined}
                title="The physical component fitted to this serial number">
                {c.component_description || '—'}
              </td>
              <td style={{ fontFamily: 'monospace', fontSize: 11 }}>{c.matched_serial_nbr}</td>
              <td style={{ textAlign: 'right',
                color: c.age_is_negative ? '#f59e0b' : undefined }}
                title={c.age_is_negative
                  ? 'Negative age — this component was fitted AFTER the incident, so it is a repair replacement, not the part that failed.'
                  : undefined}>
                {num(c.component_age_days, 0)}
              </td>
              <td title={c.dominant_pred_component ? compLabel(c.dominant_pred_component) : undefined}>
                {c.rootcause_shippable === false
                  ? <span style={{ color: 'var(--text-secondary)' }}>gated</span>
                  : compLabelShort(c.dominant_pred_component)}
              </td>
              <td style={{ textAlign: 'right' }}>
                {c.coverage_status === 'not_in_feed'
                  ? <span style={{ color: '#b45309', fontSize: 11 }}>not in feed</span>
                  : intf(c.n_incidents)}
              </td>
              <td style={{ color: 'var(--text-secondary)' }}>{dayOf(c.last_incident_dtm)}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <div style={{ fontSize: 11, color: 'var(--text-secondary)', marginTop: 6, lineHeight: 1.5 }}>
        Incident counts on these rows are the DEVICE&apos;s count carried onto each component by the
        exposure join — every component installed at the time was exposed to the incident, and the run
        attributed it to none of them. Do not sum this column; the device row above holds the true total.
      </div>
    </div>
  );
}

// ---------------------------------------------------------- component grain --
function ComponentGrain({ rows, total, state, bandOf, drill, apiBase, onAnalyse }) {
  const t = useSortPage(rows, { key: 'n_incidents', dir: 'desc' }, 50);
  if (state !== 'ok') return <NoRows msg="Component grain not loaded for this run." />;
  const devs = new Set(rows.map((r) => r.device_id)).size;
  const distinct = new Set(rows.map((r) => r.matched_serial_nbr)).size;
  const fan = devs ? rows.length / devs : null;
  const noFeed = rows.filter((r) => r.coverage_status === 'not_in_feed').length;
  const types = new Set(rows.map((r) => r.component_description).filter(Boolean)).size;
  return (
    <div>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 16, marginBottom: 18 }}>
        <Kpi label="Component rows (in view)" value={intf(rows.length)} sub={`of ${intf(total)} returned`} />
        <Kpi label="Distinct serials" value={intf(distinct)} sub="unique matched_serial_nbr" />
        <Kpi label="Component types" value={intf(types)} sub="distinct component_description" />
        <Kpi label="Components per device" value={fan === null ? '—' : num(fan, 2)}
          sub={noFeed > 0 ? `${intf(noFeed)} with no PS3 feed` : 'mean fan-out'} />
      </div>
      <Panel title="Component risk — device_id × serial"
        right={<ExportButton rows={t.sorted} filename="ps3_component_risk.csv" />}
        note="One row per installed component. Component name is the physical part from hw_config; attributed subsystem is the model's separate root-cause taxonomy. The two are different vocabularies and are shown side by side rather than merged.">
        <table className="data-table">
          <thead>
            <tr>
              <SortTh label="Device" col="device_id" sort={t.sort} setSort={t.setSort} />
              <SortTh label="Component name" col="component_description" sort={t.sort} setSort={t.setSort} />
              <SortTh label="Serial" col="matched_serial_nbr" sort={t.sort} setSort={t.setSort} />
              <SortTh label="Type" col="mars_device_category" sort={t.sort} setSort={t.setSort} />
              <SortTh label="Component age (d)" col="component_age_days" sort={t.sort} setSort={t.setSort} align="right" />
              <SortTh label="OOS incidents" col="n_incidents" sort={t.sort} setSort={t.setSort} align="right" />
              <th>Severity</th>
              <SortTh label="Attributed subsystem" col="dominant_pred_component" sort={t.sort} setSort={t.setSort} />
              <SortTh label="Last incident" col="last_incident_dtm" sort={t.sort} setSort={t.setSort} />
              <th>Action</th>
            </tr>
          </thead>
          <VirtualTBody rows={t.slice} renderRow={(s) => {
            const noFeedRow = s.coverage_status === 'not_in_feed';
            return (
              <tr key={`${s.device_id}-${s.matched_serial_nbr}`}
                style={noFeedRow ? { background: hexA('#f59e0b', 0.04) } : undefined}>
                <td style={{ fontFamily: 'monospace', fontWeight: 600, cursor: 'pointer' }}
                  onClick={() => drill.push('device', s.device_id, `device: ${s.device_id}`)}>{s.device_id}</td>
                <td style={{ fontWeight: 600, cursor: s.component_description ? 'pointer' : 'default' }}
                  onClick={s.component_description
                    ? () => drill.push('installed_component', s.component_description,
                        `installed: ${s.component_description}`) : undefined}
                  title="The physical component fitted to this serial number">
                  {s.component_description || '—'}
                </td>
                <td style={{ fontFamily: 'monospace', fontSize: 11 }}>{s.matched_serial_nbr}</td>
                <td>
                  <span className="badge badge-info" style={{
                    background: hexA(TCOL[s.mars_device_category] || '#64748b', 0.12),
                    color: TCOL[s.mars_device_category] || '#64748b' }}>{s.mars_device_category}</span>
                </td>
                <td style={{ textAlign: 'right', color: s.age_is_negative ? '#f59e0b' : undefined }}
                  title={s.age_is_negative
                    ? 'Negative age — fitted AFTER the incident, so a repair replacement rather than the failed part.'
                    : undefined}>{num(s.component_age_days, 0)}</td>
                <td style={{ textAlign: 'right' }}>
                  {noFeedRow ? <span style={{ color: '#b45309', fontSize: 11 }}>not in feed</span>
                    : intf(s.n_incidents)}
                </td>
                <td>{noFeedRow
                  ? <span style={{ color: 'var(--text-secondary)', fontSize: 11 }}>no severity model</span>
                  : <BandDot band={bandOf(s)} gated={s.severity_shippable === false} />}</td>
                <td title={s.dominant_pred_component ? compLabel(s.dominant_pred_component) : undefined}>
                  {s.rootcause_shippable === false
                    ? <span style={{ color: 'var(--text-secondary)' }}>gated</span>
                    : compLabelShort(s.dominant_pred_component)}
                </td>
                <td style={{ fontSize: 12, color: 'var(--text-secondary)' }}>{dayOf(s.last_incident_dtm)}</td>
                <td>
                  <div style={{ display: 'flex', gap: 6 }}>
                    <AnalyseButton compact onClick={() => onAnalyse(s.device_id)}
                      title="Open Device 360 for the device this component is fitted to" />
                    <ServiceNowButton apiBase={apiBase} psId="ps3" compact
                      deviceId={s.device_id} deviceCategory={s.mars_device_category}
                      shortDescription={`PS3 ${s.mars_device_category} ${s.device_id} — ${s.component_description || 'component'} serial ${s.matched_serial_nbr}`}
                      payload={{ ps: 'PS3', grain: 'serial', ...s }} />
                  </div>
                </td>
              </tr>
            );
          }} />
        </table>
        <Pager page={t.page} pages={t.pages} total={t.sorted.length} setPage={t.setPage}
          label="component rows" pageSize={t.pageSize} setPageSize={t.setPageSize} />
      </Panel>
    </div>
  );
}

// Small local badge so this file does not depend on the PS3 tab's Badge wrapper.
function BandDot({ band, gated }) {
  if (gated) return <span style={{ color: 'var(--text-secondary)', fontSize: 11 }}>gated</span>;
  if (!band) return <span style={{ color: 'var(--text-secondary)' }}>—</span>;
  return (
    <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6, fontSize: 12, fontWeight: 600 }}>
      <span style={{ width: 8, height: 8, borderRadius: '50%', background: RISK[band] || '#94a3b8' }} />
      {band}
    </span>
  );
}
