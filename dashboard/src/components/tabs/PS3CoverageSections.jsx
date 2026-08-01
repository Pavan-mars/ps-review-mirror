import React, { useState, useMemo } from 'react';
import AnalyseButton from '../shared/AnalyseButton';
import {
  TCOL, TYPE_LABEL, RISK,
  num, intf, pct, hexA,
  SearchBox, SelectBox, SortTh, Pager, NoRows, Panel, Kpi,
  useSortPage, VirtualTBody, ServiceNowButton, ExportButton,
  TreemapPanel, BubblePanel, AreaPanel, BarPanel, TrendPanel } from '../shared/DashboardKit';

// ============================================================================
// PS3 · coverage, fleet inventory and the views that were missing
//
// WHY THIS FILE EXISTS
// --------------------
// The PS3 tab showed nothing whatsoever for VALIDATOR devices and nothing on
// screen said why, so the only available reading was that the dashboard was
// broken. It is not. PS3 is sourced from silver.incident_root_cause -- ServiceNow
// AVAILABILITY EVENTS -- and validator failures are never recorded as
// availability events; they surface as device_event out-of-service 'Set' rows.
// The PS3 feed therefore holds 0 VALIDATOR incidents BY CONSTRUCTION.
//
// Measured on the 26-Jul-2026 run, joined to the daily device<->serial dimension:
//
//     TVM         32,842 incidents    472 of   475 fleet devices scored   99.4%
//     GATE         1,770 incidents    455 of   872 fleet devices scored   52.2%
//     VALIDATOR        0 incidents      0 of 3,329 fleet devices scored    0.0%
//
// So a third of the programme's device population is invisible to PS3, and until
// now the tab did not say so. Everything below is built to state that plainly and
// then show validators the detail that DOES exist for them -- their devices,
// their serials, their components and their component ages, from
// dim_device_serial, which covers all three categories.
//
// WHAT IS DELIBERATELY NOT HERE
// -----------------------------
// No modelled severity, root cause, risk score, confidence or ranking for
// VALIDATOR. No proxy scores presented as model output. There is no validator
// PS3 model, and inventing screen furniture that implies one would be worse than
// the blank panel this file replaces.
//
// Sources, one per view, all RDS:
//   /ps3/coverage         v_ps3_category_coverage  (sql/23 + the run's own stub)
//   /ps3/fleet-inventory  v_component_inventory    (sql/22, all 3 categories)
//   /ps3/fleet-devices    v_device_serial LEFT JOIN v_ps3_device_risk
//   /ps3/timeline         ps3_incident_predictions by month
//   /ps3/facility-rollup  ps3_incident_predictions by facility
//   /ps3/age-risk         ps3_incident_predictions bucketed by component age
// ============================================================================

const SEV_BANDS = ['CRITICAL', 'MAJOR'];

// ---------------------------------------------------------------- banner ----
// Sits above every sub-tab. One line per category, so "where did the validators
// go" is answered before the reader has to ask it.
export function CoverageBanner({ rows, onSelect, selected }) {
  if (!rows || !rows.length) return null;
  const missing = rows.filter((r) => !r.modeled);
  return (
    <div className="card" style={{ marginBottom: 14, padding: 14,
      borderLeft: `4px solid ${missing.length ? '#f59e0b' : '#22c55e'}` }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between',
        gap: 12, flexWrap: 'wrap', marginBottom: 10 }}>
        <div className="card-header" style={{ marginBottom: 0 }}>PS3 coverage by device type</div>
        <span style={{ fontSize: 11, color: 'var(--text-secondary)' }}>
          Fleet counts from the daily device&nbsp;&harr;&nbsp;serial dimension · scored counts from this run
        </span>
      </div>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(260px, 1fr))', gap: 12 }}>
        {rows.map((r) => {
          const c = TCOL[r.device_category] || '#64748b';
          const on = selected === r.device_category;
          const ratio = r.device_coverage_ratio === null || r.device_coverage_ratio === undefined
            ? null : Number(r.device_coverage_ratio);
          return (
            <button key={r.device_category} type="button"
              onClick={() => onSelect && onSelect(on ? '' : r.device_category)}
              title={r.modeled
                ? `${r.device_category}: modelled from ${r.source_table}`
                : r.exclusion_reason}
              style={{ textAlign: 'left', cursor: onSelect ? 'pointer' : 'default',
                background: on ? hexA(c, 0.1) : 'transparent',
                border: `1px solid ${on ? c : 'var(--border)'}`,
                borderRadius: 10, padding: '10px 12px', font: 'inherit' }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6 }}>
                <span style={{ width: 10, height: 10, borderRadius: 3, background: c }} />
                <span style={{ fontWeight: 700, fontSize: 13 }}>
                  {TYPE_LABEL[r.device_category] || r.device_category}
                </span>
                <span className={`badge ${r.modeled ? 'badge-success' : 'badge-high'}`}
                  style={{ marginLeft: 'auto' }}>
                  {r.modeled ? 'modelled' : 'not in PS3 feed'}
                </span>
              </div>
              <div style={{ fontSize: 12, color: 'var(--text-secondary)', lineHeight: 1.6 }}>
                <strong style={{ color: 'var(--text-primary)' }}>{intf(r.n_fleet_devices)}</strong> in fleet
                {' · '}
                <strong style={{ color: r.modeled ? 'var(--text-primary)' : '#b45309' }}>
                  {intf(r.n_devices_scored)}
                </strong> scored
                {ratio !== null && <> · {(ratio * 100).toFixed(1)}% covered</>}
                <br />
                {intf(r.n_ps3_incidents)} incidents · {intf(r.n_fleet_serials)} serials
                · {intf(r.n_fleet_components)} component type{Number(r.n_fleet_components) === 1 ? '' : 's'}
              </div>
            </button>
          );
        })}
      </div>
      {/* 27-Jul-2026 (PK, item 5). The "Validators are absent from PS3, not
          missing from the fleet…" paragraph was removed. It restated the
          exclusion_reason, alternative_coverage and remediation columns from
          ps3_category_coverage as a wall of prose under the cards.

          The information is NOT lost and nothing was deleted from the database:
          each card's "not in PS3 feed" badge still carries the same
          exclusion_reason as its hover title, and the "Where each device type is
          sourced from" table below spells out the source and the reason per
          category. The card row already says the thing; the paragraph said it
          again at four times the length. */}
    </div>
  );
}

// -------------------------------------------------------------- coverage ----
// The first sub-tab. Fleet against scored, then the inventory that exists for
// every category including the one PS3 cannot model.
export function CoverageView({ coverage, inventory, fleetDevices, fleetState,
  cat, setCat, q, setQ, apiBase, drill, onAnalyse }) {
  const rows = coverage || [];
  const total = rows.reduce((a, r) => ({
    fleet: a.fleet + Number(r.n_fleet_devices || 0),
    scored: a.scored + Number(r.n_devices_scored || 0),
    inc: a.inc + Number(r.n_ps3_incidents || 0),
  }), { fleet: 0, scored: 0, inc: 0 });

  // Fleet against scored, per category. Two plain bars beat a percentage: the
  // gap IS the message and a ratio hides how big the population is.
  const covBars = useMemo(() => rows.map((r) => ({
    category: TYPE_LABEL[r.device_category] || r.device_category,
    'In fleet': Number(r.n_fleet_devices || 0),
    'Scored by PS3': Number(r.n_devices_scored || 0),
  })), [rows]);

  const invRows = useMemo(
    () => (inventory || []).filter((r) => !cat || r.device_category === cat), [inventory, cat]);

  const invTree = useMemo(() => invRows.map((r) => ({
    name: `${r.device_category} · ${r.component_description}`,
    value: Number(r.n_components || 0),
    device_category: r.device_category,
    component_description: r.component_description,
    avg_age_days: r.avg_age_days,
  })), [invRows]);

  // Age against population, one point per component type. Colour is device type,
  // so a validator component sits on the same axes as a TVM one and the
  // comparison is direct.
  const ageBubbles = useMemo(() => invRows
    .filter((r) => r.avg_age_days !== null && r.avg_age_days !== undefined)
    .map((r) => ({
      name: r.component_description,
      device_category: r.device_category,
      age: Number(r.avg_age_days) || 0,
      components: Number(r.n_components) || 0,
      devices: Number(r.n_devices) || 0,
    })), [invRows]);

  const t = useSortPage(fleetDevices || [], { key: 'ps3_n_incidents', dir: 'desc' }, 50);

  return (
    <div>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 16, marginBottom: 18 }}>
        <Kpi label="Devices in fleet" value={intf(total.fleet)}
          sub="all three types · dim_device_serial" />
        <Kpi label="Scored by PS3" value={intf(total.scored)}
          sub={total.fleet ? `${((total.scored / total.fleet) * 100).toFixed(1)}% of the fleet` : '—'}
          color={total.scored < total.fleet ? '#f59e0b' : undefined} />
        <Kpi label="Outside the PS3 feed" value={intf(total.fleet - total.scored)}
          color={RISK.MAJOR} sub="no availability events emitted" />
        <Kpi label="Incidents in this run" value={intf(total.inc)}
          sub="availability-event grain" />
      </div>

      <div className="grid-2">
        <Panel title="Fleet population against PS3 coverage"
          note="Left bar is every device of that type in the fleet. Right bar is how many this run actually scored. The gap is the coverage debt, device by device.">
          <BarPanel data={covBars} xKey="category" series={['In fleet', 'Scored by PS3']}
            height={280} colorOf={(s) => (s === 'In fleet' ? '#94a3b8' : '#6366f1')} />
        </Panel>
        <Panel title="Where each device type is sourced from"
          note="A category is only modelled when its failures reach the PS3 feed. This table is loaded from the run's own artefacts, not written by hand.">
          <table className="data-table">
            <thead>
              <tr>
                <th>Device type</th><th>Status</th>
                <th style={{ textAlign: 'right' }}>Fleet</th>
                <th style={{ textAlign: 'right' }}>Scored</th>
                <th>Source feed</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.device_category}>
                  <td style={{ fontWeight: 700, color: TCOL[r.device_category] || 'var(--text-primary)' }}>
                    {TYPE_LABEL[r.device_category] || r.device_category}
                  </td>
                  <td>
                    <span className={`badge ${r.modeled ? 'badge-success' : 'badge-high'}`}>
                      {r.modeled ? 'modelled' : 'no feed'}
                    </span>
                  </td>
                  <td style={{ textAlign: 'right' }}>{intf(r.n_fleet_devices)}</td>
                  <td style={{ textAlign: 'right', fontWeight: 700,
                    color: Number(r.n_devices_scored) === 0 ? '#b45309' : undefined }}>
                    {intf(r.n_devices_scored)}
                  </td>
                  <td style={{ fontFamily: 'monospace', fontSize: 11 }}>{r.source_table || '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Panel>
      </div>

      {/* 27-Jul-2026 (PK, item 5). The "What it takes to bring Validators into
          PS3" card is removed with the paragraph above it — its remediation text
          was the closing clause of the same passage ("…needs Cubic to emit
          validator availability events or fault codes").

          ps3_category_coverage.remediation is untouched in the database and
          still served by /ps3/coverage, so this is a display decision and not a
          data loss. */}

      <Panel title="Installed component inventory — every device type"
        note="From the daily device ↔ serial dimension, which covers all three categories. This is the component-level detail that exists for Validators today. Area is the number of installed components."
        right={<SelectBox label="Device type" value={cat} onChange={setCat}
          options={[...new Set((inventory || []).map((r) => r.device_category))]} />}>
        <TreemapPanel data={invTree} height={300}
          onDrill={(n) => n?.component_description
            && drill.push('component', n.component_description, `component: ${n.component_description}`)} />
      </Panel>

      <div className="grid-2">
        <Panel title="Component age against installed population"
          note="One point per component type. Bubble area is how many are installed, colour is device type. Old and numerous is the replacement conversation.">
          <BubblePanel data={ageBubbles} xKey="age" yKey="devices" zKey="components"
            xLabel="Mean component age (days)" yLabel="Devices carrying it" zLabel="Installed"
            colorKey="device_category" height={320} />
        </Panel>
        <Panel title="Component inventory detail"
          right={<ExportButton rows={invRows} filename="ps3_fleet_inventory.csv" />}
          note="Negative ages are components fitted after the event being examined — counted, never averaged in.">
          <table className="data-table">
            <thead>
              <tr>
                <th>Type</th><th>Component</th>
                <th style={{ textAlign: 'right' }}>Installed</th>
                <th style={{ textAlign: 'right' }}>Devices</th>
                <th style={{ textAlign: 'right' }}>Mean age (d)</th>
                <th style={{ textAlign: 'right' }}>Oldest (d)</th>
                <th style={{ textAlign: 'right' }}>Fitted after</th>
              </tr>
            </thead>
            <tbody>
              {invRows.length === 0 && (
                <tr><td colSpan={7}><NoRows msg="No inventory rows for this selection." /></td></tr>
              )}
              {invRows.map((r) => (
                <tr key={`${r.device_category}-${r.component_description}`}>
                  <td>
                    <span className="badge badge-info" style={{
                      background: hexA(TCOL[r.device_category] || '#64748b', 0.12),
                      color: TCOL[r.device_category] || '#64748b' }}>{r.device_category}</span>
                  </td>
                  <td style={{ fontFamily: 'monospace', fontSize: 12, cursor: 'pointer' }}
                    onClick={() => drill.push('component', r.component_description,
                      `component: ${r.component_description}`)}>
                    {r.component_description}
                  </td>
                  <td style={{ textAlign: 'right', fontWeight: 600 }}>{intf(r.n_components)}</td>
                  <td style={{ textAlign: 'right' }}>{intf(r.n_devices)}</td>
                  <td style={{ textAlign: 'right' }}>{num(r.avg_age_days, 0)}</td>
                  <td style={{ textAlign: 'right' }}>{num(r.max_age_days, 0)}</td>
                  <td style={{ textAlign: 'right',
                    color: Number(r.n_replaced_after_event) > 0 ? '#f59e0b' : 'var(--text-secondary)' }}>
                    {intf(r.n_replaced_after_event)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </Panel>
      </div>

      <Panel title="Fleet devices — every device type, with PS3 incidents where they exist"
        right={<>
          <SearchBox value={q} onChange={setQ} placeholder="Device / serial / component…" width={230} />
          <ExportButton rows={t.sorted} filename="ps3_fleet_devices.csv" />
        </>}
        note="Searched and paged server-side against the whole dimension, so a device outside the first page is still findable. A Validator row shows its components and a blank incident count — that is the truth, not a gap in the table.">
        {fleetState === 'loading' && <NoRows msg="Loading fleet devices…" />}
        {fleetState === 'err' && <NoRows msg="Fleet device list unavailable." />}
        {fleetState === 'ok' && (
          <>
            <table className="data-table">
              <thead>
                <tr>
                  <SortTh label="Device" col="device_id" sort={t.sort} setSort={t.setSort} />
                  <SortTh label="Type" col="device_category" sort={t.sort} setSort={t.setSort} />
                  <SortTh label="Serials" col="n_serials" sort={t.sort} setSort={t.setSort} align="right" />
                  <SortTh label="Component types" col="n_component_types" sort={t.sort} setSort={t.setSort} align="right" />
                  <th>Components</th>
                  <SortTh label="Mean age (d)" col="avg_component_age_days" sort={t.sort} setSort={t.setSort} align="right" />
                  <SortTh label="PS3 incidents" col="ps3_n_incidents" sort={t.sort} setSort={t.setSort} align="right" />
                  <th>Action</th>
                </tr>
              </thead>
              <VirtualTBody rows={t.slice} renderRow={(d) => (
                <tr key={d.device_id}>
                  <td style={{ fontFamily: 'monospace', fontWeight: 600, cursor: 'pointer' }}
                    onClick={() => drill.push('device', d.device_id, `device: ${d.device_id}`)}>
                    {d.device_id}
                  </td>
                  <td>
                    <span className="badge badge-info" style={{
                      background: hexA(TCOL[d.device_category] || '#64748b', 0.12),
                      color: TCOL[d.device_category] || '#64748b' }}>{d.device_category}</span>
                  </td>
                  <td style={{ textAlign: 'right' }}>{intf(d.n_serials)}</td>
                  <td style={{ textAlign: 'right' }}>{intf(d.n_component_types)}</td>
                  <td style={{ fontFamily: 'monospace', fontSize: 11, maxWidth: 240,
                    overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}
                    title={d.components}>{d.components || '—'}</td>
                  <td style={{ textAlign: 'right' }}>{num(d.avg_component_age_days, 0)}</td>
                  <td style={{ textAlign: 'right', fontWeight: 600 }}>
                    {d.ps3_n_incidents === null || d.ps3_n_incidents === undefined
                      ? <span style={{ color: 'var(--text-secondary)', fontWeight: 400 }}
                        title="This device type does not reach the PS3 availability-event feed">not in feed</span>
                      : intf(d.ps3_n_incidents)}
                  </td>
                  <td>
                    <div style={{ display: 'flex', gap: 6 }}>
                    <AnalyseButton compact onClick={() => onAnalyse && onAnalyse(d.device_id)}
                      title="Open Device 360 — this device across PS1 to PS5. A Validator has no PS3 section, but PS1, PS4 and PS5 cover it." />
                    <ServiceNowButton apiBase={apiBase} psId="ps3" compact
                      deviceId={d.device_id} deviceCategory={d.device_category}
                      shortDescription={`PS3 fleet ${d.device_category} ${d.device_id}: `
                        + `${d.n_serials} serial(s), ${d.n_component_types} component type(s)`
                        + `${d.ps3_n_incidents ? `, ${d.ps3_n_incidents} PS3 incidents` : ', no PS3 incidents in feed'}`}
                      payload={{ ps: 'PS3', grain: 'fleet_device', ...d }} />
                    </div>
                  </td>
                </tr>
              )} />
            </table>
            <Pager page={t.page} pages={t.pages} total={t.sorted.length} setPage={t.setPage}
              label="devices" pageSize={t.pageSize} setPageSize={t.setPageSize} />
          </>
        )}
      </Panel>
    </div>
  );
}

// ----------------------------------------------------------------- trend ----
// ae_start_dtm has been on ps3_incident_predictions all along and nothing ever
// aggregated it, so "is this getting better or worse" had no answer on the tab.
export function TrendSection({ timeline, cats }) {
  const wanted = useMemo(() => new Set(cats && cats.length ? cats : []), [cats]);
  const byMonth = useMemo(() => {
    const m = new Map();
    (timeline || []).forEach((r) => {
      if (wanted.size && !wanted.has(r.device_category)) return;
      const k = String(r.period).slice(0, 10);
      const row = m.get(k) || { period: k };
      row[r.device_category] = (row[r.device_category] || 0) + Number(r.n || 0);
      m.set(k, row);
    });
    return [...m.values()].sort((a, b) => a.period.localeCompare(b.period));
  }, [timeline, wanted]);

  const bySeverity = useMemo(() => {
    const m = new Map();
    (timeline || []).forEach((r) => {
      if (wanted.size && !wanted.has(r.device_category)) return;
      const band = r.pred_severity_collapsed;
      // A category whose severity head is gated writes NULL here. Bucketed as
      // "Severity gated" rather than folded into MAJOR, which would invent a
      // severity the model is not allowed to assert.
      const key = band || 'Severity gated';
      const k = String(r.period).slice(0, 10);
      const row = m.get(k) || { period: k };
      row[key] = (row[key] || 0) + Number(r.n || 0);
      m.set(k, row);
    });
    return [...m.values()].sort((a, b) => a.period.localeCompare(b.period));
  }, [timeline, wanted]);

  const catSeries = useMemo(
    () => [...new Set((timeline || []).map((r) => r.device_category))]
      .filter((c) => !wanted.size || wanted.has(c)).sort(), [timeline, wanted]);
  const sevSeries = useMemo(() => {
    const s = new Set();
    bySeverity.forEach((r) => Object.keys(r).forEach((k) => k !== 'period' && s.add(k)));
    return [...SEV_BANDS.filter((b) => s.has(b)), ...[...s].filter((k) => !SEV_BANDS.includes(k)).sort()];
  }, [bySeverity]);

  if (!timeline || !timeline.length) return null;
  return (
    <div className="grid-2">
      <Panel title="OOS incidents per month by device type"
        note="Counted at the availability-event grain across the whole run. Validators do not appear because they emit no availability events — see the coverage strip above.">
        <TrendPanel data={byMonth} xKey="period" series={catSeries} height={280}
          colorOf={(s) => TCOL[s] || '#64748b'} />
      </Panel>
      <Panel title="Severity mix over time"
        note="Stacked to the monthly total. 'Severity gated' is a category whose severity head missed its macro-F1 floor — the model is not permitted to assert a band for those incidents, so they are shown as their own layer rather than folded in.">
        <AreaPanel data={bySeverity} xKey="period" series={sevSeries} height={280}
          colorOf={(s) => RISK[s] || '#94a3b8'} />
      </Panel>
    </div>
  );
}

// -------------------------------------------------------------- facility ----
export function FacilitySection({ facilities, cats, drill }) {
  const rows = useMemo(() => (facilities || [])
    .filter((r) => !cats || !cats.length || cats.includes(r.device_category)), [facilities, cats]);

  const byFacility = useMemo(() => {
    const m = new Map();
    rows.forEach((r) => {
      const k = r.facility_name || 'Unmapped';
      const cur = m.get(k) || { name: k, value: 0, devices: 0, critical: 0 };
      cur.value += Number(r.n_incidents || 0);
      cur.devices += Number(r.n_devices || 0);
      cur.critical += Number(r.n_critical || 0);
      m.set(k, cur);
    });
    return [...m.values()].sort((a, b) => b.value - a.value);
  }, [rows]);

  const top = byFacility.slice(0, 15).map((f) => ({
    facility: f.name,
    'Critical': f.critical,
    'Other severity': Math.max(0, f.value - f.critical),
  }));

  if (!byFacility.length) return null;
  return (
    <>
      <Panel title="Where the incidents are — stations by OOS volume"
        note="Area is incident count across the whole run. Click a station to drill every view on this tab into it.">
        <TreemapPanel data={byFacility} height={300}
          onDrill={(n) => n?.name && drill.push('facility', n.name, `station: ${n.name}`)} />
      </Panel>
      <Panel title="Top 15 stations — critical against the rest"
        note="Split by the collapsed severity band. A tall red segment is where a truck roll changes the most.">
        <BarPanel data={top} xKey="facility" series={['Critical', 'Other severity']} stacked
          height={320} colorOf={(s) => (s === 'Critical' ? RISK.CRITICAL : '#94a3b8')}
          onDrill={(p) => p?.facility && drill.push('facility', p.facility, `station: ${p.facility}`)} />
      </Panel>
    </>
  );
}

// -------------------------------------------------------------- age risk ----
export function AgeRiskSection({ ageRisk, cats }) {
  const rows = useMemo(() => (ageRisk || [])
    .filter((r) => !cats || !cats.length || cats.includes(r.device_category)), [ageRisk, cats]);

  const points = rows.map((r) => ({
    device_category: r.device_category,
    age: Number(r.bucket_min_days) || 0,
    incidents: Number(r.n_incidents) || 0,
    perDevice: Number(r.incidents_per_device) || 0,
    devices: Number(r.n_devices) || 0,
    band: `${num(r.bucket_min_days, 0)}–${num(r.bucket_max_days, 0)} d`,
  }));

  // A category whose component age barely varies collapses into one bucket. Say
  // so, or a single point reads as a broken chart rather than a flat feature.
  const flat = useMemo(() => {
    const by = new Map();
    rows.forEach((r) => {
      const cur = by.get(r.device_category) || { buckets: 0, ages: 0 };
      cur.buckets += 1;
      cur.ages = Math.max(cur.ages, Number(r.n_distinct_ages) || 0);
      by.set(r.device_category, cur);
    });
    return [...by.entries()].filter(([, v]) => v.buckets <= 1);
  }, [rows]);

  if (!points.length) return null;
  return (
    <Panel title="Does an older component fail more? — incidents per device by component age"
      note="Counted at the incident grain, not the serial grain: ps3_serial_predictions repeats a device's incident count on each of its serial rows, which would inflate the TVM total to 132,500 against a true 32,842. Incidents whose component age is negative (the part was fitted after the event) are excluded and reported separately.">
      <BubblePanel data={points} xKey="age" yKey="perDevice" zKey="incidents"
        xLabel="Component age at incident (days, bucket start)"
        yLabel="Incidents per device" zLabel="Incidents"
        colorKey="device_category" height={330} />
      {flat.length > 0 && (
        <div style={{ marginTop: 10, fontSize: 11, color: '#b45309',
          background: hexA('#f59e0b', 0.09), padding: '8px 10px', borderRadius: 6, lineHeight: 1.6 }}>
          {flat.map(([c, v]) => (
            <div key={c}>
              <strong>{TYPE_LABEL[c] || c} sit in a single age bucket.</strong>{' '}
              Their incidents carry only {v.ages} distinct component_age_days value
              {v.ages === 1 ? '' : 's'}, so the feature is effectively constant for this
              device type — the single point is the data being flat, not the chart failing.
            </div>
          ))}
        </div>
      )}
    </Panel>
  );
}

// ------------------------------------------------------- confusion matrix ----
// Predicted against actual for one head and one device type. The generic
// cross-tab could already pivot these two dimensions, but it mixed 32,842 TVM
// rows with 1,770 GATE rows into one grid where the GATE block was unreadable.
export function ConfusionSection({ cells, head, setHead, cat, setCat, cats, state }) {
  const { labels, grid, total, correct } = useMemo(() => {
    const l = new Set(); const g = new Map(); let tot = 0; let ok = 0;
    (cells || []).forEach((c) => {
      const r = c.row_key === null || c.row_key === undefined || c.row_key === '' ? '—' : String(c.row_key);
      const k = c.col_key === null || c.col_key === undefined || c.col_key === '' ? '—' : String(c.col_key);
      const v = Number(c.n) || 0;
      l.add(r); l.add(k); g.set(`${r}|${k}`, (g.get(`${r}|${k}`) || 0) + v);
      tot += v; if (r === k) ok += v;
    });
    return { labels: [...l].sort(), grid: g, total: tot, correct: ok };
  }, [cells]);

  const rowTot = (r) => labels.reduce((s, k) => s + (grid.get(`${r}|${k}`) || 0), 0);

  return (
    <Panel title={`Confusion matrix — ${head === 'severity' ? 'severity' : 'root cause'}${cat ? ` · ${TYPE_LABEL[cat] || cat}` : ''}`}
      right={<>
        {/* Head is a two-way choice, not a filter -- SelectBox's blank "All"
            option would leave head === '' and silently fall through to the
            root-cause branch of the fetch. A plain toggle cannot do that. */}
        <div className="filter-group">
          <label className="filter-label">Head</label>
          <div style={{ display: 'flex', gap: 4 }}>
            {[['severity', 'Severity'], ['root_cause', 'Root cause']].map(([v, l]) => (
              <button key={v} type="button" className={`filter-btn${head === v ? ' active' : ''}`}
                onClick={() => setHead(v)}
                style={head === v ? { background: 'var(--primary)', borderColor: 'var(--primary)', color: '#fff' } : undefined}>
                {l}
              </button>
            ))}
          </div>
        </div>
        <SelectBox label="Device type" value={cat} onChange={setCat} options={cats} />
      </>}
      note="Rows are what the model predicted, columns are the labelled truth. The diagonal is agreement. Shade is row-relative, so a small class is not washed out by a large one.">
      {state === 'loading' && <NoRows msg="Loading matrix…" />}
      {state === 'err' && <NoRows msg="Matrix unavailable." />}
      {state === 'ok' && labels.length === 0 && <NoRows msg="No labelled rows for this selection." />}
      {/* A single "—" label means every prediction AND every truth value is
          NULL for this head and this device type. That happens on GATE
          severity: the head scored macro-F1 0.4978 against a 0.5000 ceiling for
          2 classes, was suppressed as degenerate, and never wrote a prediction.
          One empty cell would read as a rendering fault, so name the cause. */}
      {state === 'ok' && labels.length === 1 && labels[0] === '—' && (
        <div style={{ padding: '14px 16px', fontSize: 12, lineHeight: 1.7,
          color: 'var(--text-secondary)', background: hexA('#f59e0b', 0.08), borderRadius: 8 }}>
          <strong style={{ color: '#b45309' }}>
            No {head === 'severity' ? 'severity' : 'root-cause'} predictions exist for {TYPE_LABEL[cat] || cat}.
          </strong>{' '}
          All {intf(total)} incidents carry NULL on both the predicted and the actual column, because this
          head was suppressed before it could write one. There is nothing to confuse — see the Model
          Scorecard for the macro-F1 against the constant-predictor ceiling that ruled it out.
        </div>
      )}
      {state === 'ok' && labels.length > 0 && !(labels.length === 1 && labels[0] === '—') && (
        <>
          <div style={{ marginBottom: 10, fontSize: 12, color: 'var(--text-secondary)' }}>
            {intf(total)} labelled incidents · {total ? pct(correct / total) : '—'} on the diagonal
          </div>
          <table className="data-table">
            <thead>
              <tr>
                <th style={{ position: 'sticky', left: 0 }}>predicted ↓ / actual →</th>
                {labels.map((c) => <th key={c} style={{ textAlign: 'right', fontSize: 11 }}>{c}</th>)}
                <th style={{ textAlign: 'right' }}>Total</th>
              </tr>
            </thead>
            <tbody>
              {labels.map((r) => {
                const rt = rowTot(r);
                return (
                  <tr key={r}>
                    <td style={{ fontWeight: 700, fontSize: 12 }}>{r}</td>
                    {labels.map((c) => {
                      const v = grid.get(`${r}|${c}`) || 0;
                      const share = rt ? v / rt : 0;
                      const diag = r === c;
                      return (
                        <td key={c} style={{ textAlign: 'right', fontVariantNumeric: 'tabular-nums',
                          background: v === 0 ? undefined
                            : hexA(diag ? '#22c55e' : '#ef4444', Math.min(0.42, 0.06 + share * 0.4)),
                          fontWeight: diag && v > 0 ? 700 : 400 }}
                          title={`predicted ${r} · actual ${c} · ${v.toLocaleString()} (${(share * 100).toFixed(1)}% of predicted ${r})`}>
                          {v === 0 ? <span style={{ color: 'var(--text-secondary)' }}>·</span> : intf(v)}
                        </td>
                      );
                    })}
                    <td style={{ textAlign: 'right', fontWeight: 600 }}>{intf(rt)}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </>
      )}
    </Panel>
  );
}
