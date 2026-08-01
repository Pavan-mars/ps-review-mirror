// ============================================================================
// PS2SerialGrainAnalytics.jsx  ->  src/components/tabs/PS2SerialGrainAnalytics.jsx
// NEW (22-Jul-2026): surfaces the "new analysis families" from the PS2
// serial-grain notebook that have no equivalent device-grain view anywhere
// else in the dashboard -- chronic-cascade recurrence, cross-PS attribution,
// subsystem lead/lag timing distributions, the cascade cost/severity Sankey
// source table, facility contagion (facility grain), subsystem network
// centrality, subsystem ignition/termination counts, association rules and
// business impact at serial grain, and the suppression-summary transparency
// table (what got hidden by the min-support floor, never silently dropped).
//
// All 12 panels here go through ONE generic fetcher -- apiPS2SerialMetric(city,
// metric) -- which hits /ps2/serial/:metric on cubic-mars-dashboard-api (the
// same allow-listed route CorrelationHeatmap/MarkovView/HMMRegimePanel use for
// their serial-grain toggle in PS2RichAnalytics.jsx). Matches this repo's
// established "honest empty state, never invent numbers for brand-new tables"
// convention (same choice PS3DeepDiveAnalytics.jsx made).
//
// Drop into PS2CascadingFailureTab.jsx as its own sub-tab:
//   import PS2SerialGrainAnalytics from './PS2SerialGrainAnalytics';
//   { key: 'serialgrain', label: 'Serial-Grain & New Analytics' }
//   {tab === 'serialgrain' && <PS2SerialGrainAnalytics city={city} />}
// ============================================================================
import React, { useEffect, useMemo, useState } from 'react';
import {
  BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, Legend, ResponsiveContainer, Cell,
  Sankey, Rectangle, LabelList } from 'recharts';
import { apiPS2SerialMetric, useSerialDeviceMap } from '../../data/api';
import AnalyseButton from '../shared/AnalyseButton';
import { useFilters } from '../../context/FilterContext';
import { applyPS2Filters, isAnyPS2FilterActive } from '../../utils/ps2Filters';
// 2026-07-26: direct value labels on every mark. Discrete marks (bars, pie
// slices) get one label each; continuous series (lines, areas) get an END
// label only -- a number on every point of a long series goes unread.
// Label text uses the muted text token, never the series colour.
import { VLAB, fmtV, endOnlyLabel } from '../shared/DashboardKit';

const NAVY = '#1E3A5F', INK = '#5A6B7D', LINE = '#E1E9F1';
const P = { blue:'#9DC3E6', green:'#A9D18E', amber:'#F4CE7A', red:'#F1A9A0', purple:'#C9A9DA', teal:'#8FCFC9' };

const card = { background:'#fff', border:`1px solid ${LINE}`, borderRadius:12, padding:'18px 20px', margin:'16px 0', boxShadow:'0 4px 14px rgba(30,58,95,.05)' };
const h3 = { margin:'0 0 2px', fontSize:16, color:NAVY, fontWeight:700 };
const note = { margin:'0 0 12px', fontSize:12.5, color:INK };
const warn = { margin:'0 0 12px', fontSize:12.5, color:'#B08A2E' };
const th = { textAlign:'left', padding:'7px 9px', background:'#EEF3F9', color:NAVY, fontWeight:600, fontSize:12 };
const td = { padding:'6px 9px', borderTop:`1px solid #EEF2F7`, fontSize:12, color:'#33475B' };
const fmt = (n) => (n == null ? '--' : Number(n).toLocaleString());
const pct1 = (n) => (n == null ? '--' : `${(Number(n) * 100).toFixed(1)}%`);

function useMetric(city, metric, params) {
  const key = JSON.stringify(params || {});
  const [rowsRaw, setRows] = useState([]);
  const [loading, setLoading] = useState(true);
  const filters = useFilters();
  useEffect(() => {
    let alive = true;
    setLoading(true);
    apiPS2SerialMetric(city, metric, params || {}).then((r) => { if (alive) { setRows(r || []); setLoading(false); } });
    return () => { alive = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [city, metric, key]);
  // Every serial-grain panel shares the same dashboard-wide filters (device/
  // serial search, device type, component/subsystem, failure-type) -- applied
  // here once so all 11 call sites below stay unchanged.
  const rows = useMemo(() => applyPS2Filters(rowsRaw, filters), [rowsRaw, filters]);
  return [rows, loading, rowsRaw.length];
}

function EmptyOrEach({ rows, loading, emptyText, children }) {
  if (loading) return <p style={note}>Loading…</p>;
  if (!rows || rows.length === 0) return <p style={warn}>{emptyText}</p>;
  return children;
}

// Shown once per panel body when the shared filters narrowed a non-empty
// raw result down -- distinct from EmptyOrEach's "no data at all" message.
function FilterNarrowedHint({ shown, total }) {
  if (total == null || shown === total) return null;
  return <p style={{ fontSize: 11, color: '#B08A2E', fontWeight: 600, margin: '0 0 10px' }}>Showing {shown} of {total} rows — narrowed by the active Device/Serial, Component, or Failure-Type filter.</p>;
}

// ---- 0. Suppression summary — transparency on what the min-support floor hid
function SuppressionSummaryPanel({ city }) {
  const [rows, loading, rowsRaw] = useMetric(city, 'suppression');
  return (
    <div style={card}>
      <h3 style={h3}>Serial-Grain Suppression Summary</h3>
      <p style={note}>
        Sparsity-prone families (conditional probability, association rules) enforce a minimum-support
        floor per serial. This table reports what got suppressed rather than silently dropping it —
        cells_suppressed &divide; (cells_suppressed + cells_reported) is the suppression rate per family.
      </p>
      <FilterNarrowedHint shown={rows.length} total={rowsRaw} />
      <EmptyOrEach rows={rows} loading={loading} emptyText="No suppression-summary rows yet — run the PS2 serial-grain notebook.">
        <table style={{ width:'100%', borderCollapse:'collapse' }}>
          <thead><tr><th style={th}>Family</th><th style={th}>Min-support floor</th><th style={th}>Cells reported</th>
            <th style={th}>Cells suppressed</th><th style={{ ...th, width:'26%' }}>Suppression rate</th></tr></thead>
          <tbody>{rows.map((r, i) => {
            const total = Number(r.cells_reported || 0) + Number(r.cells_suppressed || 0);
            const rate = total > 0 ? Number(r.cells_suppressed || 0) / total : 0;
            return (
              <tr key={i}>
                <td style={{ ...td, fontWeight:700, color:NAVY }}>{r.family}</td>
                <td style={td}>{fmt(r.min_support_floor)}</td>
                <td style={td}>{fmt(r.cells_reported)}</td>
                <td style={td}>{fmt(r.cells_suppressed)}</td>
                <td style={td}><div style={{ display:'flex', alignItems:'center', gap:8 }}>
                  <div style={{ background:'#F0F4F8', borderRadius:6, height:10, flex:1 }}>
                    <div style={{ width:`${rate * 100}%`, height:10, borderRadius:6, background: rate > 0.3 ? P.red : P.amber }} /></div>
                  <span style={{ minWidth:44, color:INK }}>{(rate * 100).toFixed(1)}%</span></div></td>
              </tr>
            );
          })}</tbody>
        </table>
      </EmptyOrEach>
    </div>
  );
}

// ---- 1. Chronic-cascade recurrence (serial) — explicitly descriptive, not a fitted survival model
function ChronicRecurrencePanel({ city, serialDeviceMap, onAnalyse }) {
  const [rows, loading, rowsRaw] = useMetric(city, 'chronic');
  return (
    <div style={card}>
      <h3 style={h3}>Chronic-Cascade Recurrence (Serial)</h3>
      <p style={note}>
        Empirical hazard/recurrence rate since install, by serial — descriptive only (not a fitted
        survival model, to keep the PS2/PS5 remit boundary clean). Peer-percentile rank is within the
        same device category and reference period.
      </p>
      <FilterNarrowedHint shown={rows.length} total={rowsRaw} />
      <EmptyOrEach rows={rows} loading={loading} emptyText="No chronic-recurrence data yet — run the PS2 serial-grain notebook.">
        <table style={{ width:'100%', borderCollapse:'collapse' }}>
          <thead><tr><th style={th}>Serial</th><th style={th}>Category</th><th style={th}>Reference period</th>
            <th style={th}>Cascades</th><th style={th}>Recurrence / day</th><th style={th}>Peer percentile</th><th style={th}>Chronic?</th><th style={th}></th></tr></thead>
          <tbody>{rows.slice(0, 25).map((r, i) => (
            <tr key={i}>
              <td style={{ ...td, fontFamily:'monospace', fontWeight:700, color:NAVY }}>{r.serial_id}</td>
              <td style={td}>{r.device_category || '--'}</td>
              <td style={td}>{fmt(r.reference_period_days)}d ({r.reference_period_source || '--'})</td>
              <td style={td}>{fmt(r.n_cascades)}</td>
              <td style={td}>{Number(r.recurrence_rate_per_day || 0).toFixed(4)}</td>
              <td style={td}>{pct1(r.peer_pct_rank)}</td>
              <td style={td}>{r.chronicity_flag ? <span style={{ color:'#B03A2E', fontWeight:700 }}>chronic</span> : <span style={{ color:INK }}>--</span>}</td>
              <td style={td}>{onAnalyse && (
                <AnalyseButton
                  onClick={() => onAnalyse(serialDeviceMap[r.serial_id])}
                  disabled={!serialDeviceMap[r.serial_id]}
                  title={serialDeviceMap[r.serial_id] ? `Analyse device ${serialDeviceMap[r.serial_id]}` : 'No device mapping yet for this serial'}
                  compact
                />
              )}</td>
            </tr>))}</tbody>
        </table>
      </EmptyOrEach>
    </div>
  );
}

// ---- 2. Recurrence (serial) — simpler cascade-days + chronic-flag view
function RecurrenceSerialPanel({ city, serialDeviceMap, onAnalyse }) {
  const [rows, loading, rowsRaw] = useMetric(city, 'recurrence');
  const maxD = Math.max(1, ...rows.map((r) => r.cascade_days || 0));
  return (
    <div style={card}>
      <h3 style={h3}>Cascade-Day Recurrence (Serial)</h3>
      <p style={note}>Total cascade-days per serial and whether it crosses the chronic threshold — the simpler companion to the recurrence-rate panel above.</p>
      <FilterNarrowedHint shown={rows.length} total={rowsRaw} />
      <EmptyOrEach rows={rows} loading={loading} emptyText="No recurrence data yet — run the PS2 serial-grain notebook.">
        <table style={{ width:'100%', borderCollapse:'collapse' }}>
          <thead><tr><th style={th}>Serial</th><th style={{ ...th, width:'50%' }}>Cascade days</th><th style={th}>Chronic?</th><th style={th}></th></tr></thead>
          <tbody>{rows.slice(0, 25).map((r, i) => (
            <tr key={i}>
              <td style={{ ...td, fontFamily:'monospace', fontWeight:700, color:NAVY }}>{r.serial_id}</td>
              <td style={td}><div style={{ display:'flex', alignItems:'center', gap:8 }}>
                <div style={{ background:'#F0F4F8', borderRadius:6, height:10, flex:1 }}>
                  <div style={{ width:`${((r.cascade_days || 0) / maxD) * 100}%`, height:10, borderRadius:6, background:P.blue }} /></div>
                <span style={{ minWidth:34, color:INK }}>{fmt(r.cascade_days)}</span></div></td>
              <td style={td}>{r.chronic ? <span style={{ color:'#B03A2E', fontWeight:700 }}>yes</span> : 'no'}</td>
              <td style={td}>{onAnalyse && (
                <AnalyseButton
                  onClick={() => onAnalyse(serialDeviceMap[r.serial_id])}
                  disabled={!serialDeviceMap[r.serial_id]}
                  title={serialDeviceMap[r.serial_id] ? `Analyse device ${serialDeviceMap[r.serial_id]}` : 'No device mapping yet for this serial'}
                  compact
                />
              )}</td>
            </tr>))}</tbody>
        </table>
      </EmptyOrEach>
    </div>
  );
}

// ---- 3. Lead/lag timing distributions (serial) -----------------------------
function LeadLagTimingPanel({ city, serialDeviceMap, onAnalyse }) {
  const [rows, loading, rowsRaw] = useMetric(city, 'leadlag');
  return (
    <div style={card}>
      <h3 style={h3}>Subsystem Lead/Lag Timing (Serial)</h3>
      <p style={note}>
        Full lag-time distributions between subsystem pairs (mean / median / P25 / P75), replacing a
        single conditional-probability number with the actual timing spread — feeds the Markov/Sankey
        views with real elapsed-time context.
      </p>
      <FilterNarrowedHint shown={rows.length} total={rowsRaw} />
      <EmptyOrEach rows={rows} loading={loading} emptyText="No lead/lag timing data yet — run the PS2 serial-grain notebook.">
        <table style={{ width:'100%', borderCollapse:'collapse' }}>
          <thead><tr><th style={th}>Serial</th><th style={th}>A &rarr; B</th><th style={th}>n</th>
            <th style={th}>Mean (min)</th><th style={th}>Median (min)</th><th style={th}>P25</th><th style={th}>P75</th><th style={th}></th></tr></thead>
          <tbody>{rows.slice(0, 25).map((r, i) => (
            <tr key={i}>
              <td style={{ ...td, fontFamily:'monospace', fontWeight:700, color:NAVY }}>{r.serial_id}</td>
              <td style={{ ...td, fontFamily:'monospace' }}>{r.sub_a} &rarr; {r.sub_b}</td>
              <td style={td}>{fmt(r.n)}</td>
              <td style={td}>{Number(r.mean || 0).toFixed(1)}</td>
              <td style={td}>{Number(r.median || 0).toFixed(1)}</td>
              <td style={td}>{Number(r.p25 || 0).toFixed(1)}</td>
              <td style={td}>{Number(r.p75 || 0).toFixed(1)}</td>
              <td style={td}>{onAnalyse && (
                <AnalyseButton
                  onClick={() => onAnalyse(serialDeviceMap[r.serial_id])}
                  disabled={!serialDeviceMap[r.serial_id]}
                  title={serialDeviceMap[r.serial_id] ? `Analyse device ${serialDeviceMap[r.serial_id]}` : 'No device mapping yet for this serial'}
                  compact
                />
              )}</td>
            </tr>))}</tbody>
        </table>
      </EmptyOrEach>
    </div>
  );
}

// ---- 4. Association rules (serial) -----------------------------------------
function AssociationRulesSerialPanel({ city, serialDeviceMap, onAnalyse }) {
  const [rows, loading, rowsRaw] = useMetric(city, 'assoc');
  return (
    <div style={card}>
      <h3 style={h3}>Subsystem Association Rules (Serial)</h3>
      <p style={note}>Per-serial re-run of the subsystem association-rule mining, with the same min-support floor as the device-grain rules (see the suppression summary above for what got filtered out).</p>
      <FilterNarrowedHint shown={rows.length} total={rowsRaw} />
      <EmptyOrEach rows={rows} loading={loading} emptyText="No serial-grain association rules yet — run the PS2 serial-grain notebook.">
        <table style={{ width:'100%', borderCollapse:'collapse' }}>
          <thead><tr><th style={th}>Serial</th><th style={th}>Rule</th><th style={th}>Support</th><th style={th}>Confidence</th><th style={th}>Lift</th><th style={th}>Conviction</th><th style={th}></th></tr></thead>
          <tbody>{rows.slice(0, 20).map((r, i) => (
            <tr key={i}>
              <td style={{ ...td, fontFamily:'monospace', fontWeight:700, color:NAVY }}>{r.serial_id}</td>
              <td style={{ ...td, fontFamily:'monospace' }}>{r.antecedents} &rarr; {r.consequents}</td>
              <td style={td}>{Number(r.support || 0).toFixed(3)}</td>
              <td style={td}>{Number(r.confidence || 0).toFixed(3)}</td>
              <td style={td}><span style={{ color: (r.lift || 0) > 5 ? P.red : P.amber, fontWeight:700 }}>{Number(r.lift || 0).toFixed(3)}</span></td>
              <td style={td}>{Number(r.conviction || 0).toFixed(1)}</td>
              <td style={td}>{onAnalyse && (
                <AnalyseButton
                  onClick={() => onAnalyse(serialDeviceMap[r.serial_id])}
                  disabled={!serialDeviceMap[r.serial_id]}
                  title={serialDeviceMap[r.serial_id] ? `Analyse device ${serialDeviceMap[r.serial_id]}` : 'No device mapping yet for this serial'}
                  compact
                />
              )}</td>
            </tr>))}</tbody>
        </table>
      </EmptyOrEach>
    </div>
  );
}

// ---- 5. Business impact + cascade velocity (serial) ------------------------
function BusinessImpactAndVelocityPanel({ city }) {
  const [impactRows, impactLoading, impactRawCount] = useMetric(city, 'impact');
  const [velRows, velLoading, velRawCount] = useMetric(city, 'velocity');
  const maxImpact = Math.max(1, ...impactRows.map((r) => r.total_impact || 0));
  return (
    <div style={card}>
      <h3 style={h3}>Business Impact &amp; Cascade Velocity (Serial)</h3>
      <p style={note}>Left: highest business-impact serials (cascade-day burden). Right: cascade velocity stratified by serial age-at-event, rather than a full per-serial rerun (the aggregation is the point here).</p>
      <div style={{ display:'grid', gridTemplateColumns:'1.3fr 1fr', gap:16 }}>
        <div>
          <div style={{ fontSize:11, textTransform:'uppercase', letterSpacing:1, color:INK, marginBottom:6 }}>Business impact</div>
          <FilterNarrowedHint shown={impactRows.length} total={impactRawCount} />
          <EmptyOrEach rows={impactRows} loading={impactLoading} emptyText="No serial-grain business-impact data yet.">
            <table style={{ width:'100%', borderCollapse:'collapse' }}>
              <thead><tr><th style={th}>Serial</th><th style={th}>Cascade days</th><th style={{ ...th, width:'40%' }}>Total impact</th><th style={th}>Avg impact</th></tr></thead>
              <tbody>{impactRows.slice(0, 15).map((r, i) => (
                <tr key={i}>
                  <td style={{ ...td, fontFamily:'monospace', fontWeight:700, color:NAVY }}>{r.entity_id}</td>
                  <td style={td}>{fmt(r.cascade_days)}</td>
                  <td style={td}><div style={{ display:'flex', alignItems:'center', gap:8 }}>
                    <div style={{ background:'#F0F4F8', borderRadius:6, height:10, flex:1 }}>
                      <div style={{ width:`${((r.total_impact || 0) / maxImpact) * 100}%`, height:10, borderRadius:6, background:P.amber }} /></div>
                    <span style={{ minWidth:44, color:INK }}>{fmt(r.total_impact)}</span></div></td>
                  <td style={td}>{Number(r.avg_impact || 0).toFixed(1)}</td>
                </tr>))}</tbody>
            </table>
          </EmptyOrEach>
        </div>
        <div>
          <div style={{ fontSize:11, textTransform:'uppercase', letterSpacing:1, color:INK, marginBottom:6 }}>Velocity by serial age</div>
          <FilterNarrowedHint shown={velRows.length} total={velRawCount} />
          <EmptyOrEach rows={velRows} loading={velLoading} emptyText="No cascade-velocity-by-age data yet.">
            <table style={{ width:'100%', borderCollapse:'collapse' }}>
              <thead><tr><th style={th}>Age bucket</th><th style={th}>n</th><th style={th}>Mean min/fault</th></tr></thead>
              <tbody>{velRows.map((r, i) => (
                <tr key={i}>
                  <td style={{ ...td, fontWeight:700, color:NAVY }}>{r.age_bucket}</td>
                  <td style={td}>{fmt(r.n)}</td>
                  <td style={td}>{Number(r.mean_velocity_min_per_fault || 0).toFixed(2)}</td>
                </tr>))}</tbody>
            </table>
          </EmptyOrEach>
        </div>
      </div>
    </div>
  );
}

// ---- 6. Cross-PS cascade attribution ---------------------------------------
function CrossPSAttributionPanel({ city }) {
  const [rows, loading, rowsRaw] = useMetric(city, 'crossps');
  return (
    <div style={card}>
      <h3 style={h3}>Cross-PS Cascade Attribution</h3>
      <p style={note}>
        Correlational co-occurrence between PS2 ignition entities and PS1/PS3/PS4 flags (via
        device_cross_ps / serial_cross_ps) — never re-deriving PS4's anomaly detection, just joining
        against it.
      </p>
      <FilterNarrowedHint shown={rows.length} total={rowsRaw} />
      <EmptyOrEach rows={rows} loading={loading} emptyText="No cross-PS attribution data yet — run the PS2 serial-grain notebook.">
        <>
          <table style={{ width:'100%', borderCollapse:'collapse' }}>
            <thead><tr><th style={th}>Entity grain</th><th style={th}>PS2 ignition entities</th><th style={th}>Matched in cross-PS</th><th style={{ ...th, width:'30%' }}>Match rate</th></tr></thead>
            <tbody>{rows.map((r, i) => (
              <tr key={i}>
                <td style={{ ...td, fontWeight:700, color:NAVY }}>{r.entity_grain}</td>
                <td style={td}>{fmt(r.n_ps2_ignition_entities)}</td>
                <td style={td}>{fmt(r.n_matched_in_cross_ps)}</td>
                <td style={td}><div style={{ display:'flex', alignItems:'center', gap:8 }}>
                  <div style={{ background:'#F0F4F8', borderRadius:6, height:10, flex:1 }}>
                    <div style={{ width:`${(r.match_rate || 0) * 100}%`, height:10, borderRadius:6, background:P.purple }} /></div>
                  <span style={{ minWidth:44, color:INK }}>{pct1(r.match_rate)}</span></div></td>
              </tr>))}</tbody>
          </table>
          {rows.some((r) => Number(r.match_rate || 0) === 0 && Number(r.n_ps2_ignition_entities || 0) > 0) && (
            <p style={{ ...warn, marginTop: 10, marginBottom: 0 }}>
              ⚠ 0% match rate against a non-zero entity count usually indicates a join-key mismatch
              (e.g. device_id format differences) between the PS2 ignition list and device_cross_ps /
              serial_cross_ps, rather than a genuine "no correlation" finding — worth checking the
              notebook's cross-PS attribution cell before trusting this panel.
            </p>
          )}
        </>
      </EmptyOrEach>
    </div>
  );
}

// ---- 7. Cascade cost/severity Sankey source table ---------------------------
const SANKEY_NODE_COLORS = ['#9DC3E6', '#A9D18E', '#F4CE7A', '#F1A9A0', '#C9A9DA', '#8FCFC9', '#6366f1', '#f97316', '#22c55e', '#a855f7'];

// Custom Sankey node: colored rect + label, clickable to drill down (focuses
// the shared Component filter on that one subsystem -- every other
// filter-aware PS2 panel narrows to it too).
function SankeyNode({ x, y, width, height, index, payload, onNodeClick }) {
  const color = SANKEY_NODE_COLORS[index % SANKEY_NODE_COLORS.length];
  return (
    <g style={{ cursor: 'pointer' }} onClick={() => onNodeClick(payload.name)}>
      <Rectangle x={x} y={y} width={width} height={height} fill={color} fillOpacity={0.9} />
      <text x={x + width + 6} y={y + height / 2} textAnchor="start" dominantBaseline="middle" fontSize={11} fill={NAVY} fontWeight={700}>
        {payload.name}
      </text>
    </g>
  );
}

function CascadeSankeyPanel({ city }) {
  const filters = useFilters();
  const [rows, loading, rowsRaw] = useMetric(city, 'sankey');

  const focusSubsystem = (name) => {
    if (!filters.componentOptions.length) return;
    filters.componentOptions.forEach((comp) => {
      const shouldBeSelected = comp === name;
      const isSelected = filters.selectedComponents.includes(comp);
      if (shouldBeSelected !== isSelected) filters.toggleComponent(comp);
    });
  };

  const { nodes, links } = useMemo(() => {
    const names = Array.from(new Set(rows.flatMap((r) => [r.subsystem_from, r.subsystem_to]).filter(Boolean)));
    const idx = Object.fromEntries(names.map((n, i) => [n, i]));
    const sankeyNodes = names.map((name) => ({ name }));
    const sankeyLinks = rows
      .filter((r) => r.subsystem_from && r.subsystem_to && r.subsystem_from !== r.subsystem_to)
      .map((r) => ({ source: idx[r.subsystem_from], target: idx[r.subsystem_to], value: Math.max(1, Number(r.cascade_count || 0)) }));
    return { nodes: sankeyNodes, links: sankeyLinks };
  }, [rows]);

  return (
    <div style={card}>
      <h3 style={h3}>Cascade Cost / Severity Flow (Subsystem &rarr; Subsystem)</h3>
      <p style={note}>
        Real Sankey diagram — flow width = cascade volume between subsystems. Click a subsystem node to
        drill down (focuses the shared Component filter on it across every filter-aware PS2 panel).
      </p>
      <FilterNarrowedHint shown={rows.length} total={rowsRaw} />
      <EmptyOrEach rows={rows} loading={loading} emptyText="No cascade-sankey data yet — run the PS2 serial-grain notebook.">
        {links.length === 0 ? (
          <p style={{ ...warn, margin: '0 0 12px' }}>All flows are self-loops (same subsystem to itself) — nothing to draw as a Sankey; see the table below instead.</p>
        ) : (
          <div style={{ width: '100%', height: 360 }}>
            <ResponsiveContainer>
              <Sankey
                data={{ nodes, links }}
                node={(nodeProps) => <SankeyNode {...nodeProps} onNodeClick={focusSubsystem} />}
                nodePadding={28}
                margin={{ top: 10, bottom: 10, left: 10, right: 90 }}
                link={{ stroke: '#9DC3E6', strokeOpacity: 0.4 }}
              >
                <Tooltip formatter={(v) => [Number(v).toLocaleString(), 'cascade count']} />
              </Sankey>
            </ResponsiveContainer>
          </div>
        )}
        <table style={{ width:'100%', borderCollapse:'collapse', marginTop: 12 }}>
          <thead><tr><th style={th}>From</th><th style={th}>To</th><th style={th}>Cascade count</th><th style={th}>Total business impact</th><th style={th}>Avg severity</th></tr></thead>
          <tbody>{rows.slice(0, 20).map((r, i) => (
            <tr key={i}>
              <td style={{ ...td, fontFamily:'monospace', fontWeight:700, color:NAVY }}>{r.subsystem_from}</td>
              <td style={{ ...td, fontFamily:'monospace' }}>{r.subsystem_to}</td>
              <td style={td}>{fmt(r.cascade_count)}</td>
              <td style={td}>{fmt(r.total_business_impact)}</td>
              <td style={td}>{Number(r.avg_severity || 0).toFixed(2)}</td>
            </tr>))}</tbody>
        </table>
      </EmptyOrEach>
    </div>
  );
}

// ---- 8. Subsystem network centrality (new scoped table) --------------------
function NetworkCentralitySubsystemPanel({ city }) {
  const [rows, loading, rowsRaw] = useMetric(city, 'network');
  return (
    <div style={card}>
      <h3 style={h3}>Subsystem Network Centrality (New Scope)</h3>
      <p style={note}>Betweenness/PageRank per subsystem from the serial-grain notebook run — a separately-scoped centrality table alongside the device-grain network view above.</p>
      <FilterNarrowedHint shown={rows.length} total={rowsRaw} />
      <EmptyOrEach rows={rows} loading={loading} emptyText="No subsystem centrality data yet — run the PS2 serial-grain notebook.">
        <table style={{ width:'100%', borderCollapse:'collapse' }}>
          <thead><tr><th style={th}>Scope</th><th style={th}>Subsystem</th><th style={th}>Betweenness</th><th style={th}>PageRank</th><th style={th}>In</th><th style={th}>Out</th></tr></thead>
          <tbody>{rows.map((r, i) => (
            <tr key={i}>
              <td style={td}>{r.scope}</td>
              <td style={{ ...td, fontFamily:'monospace', fontWeight:700, color:NAVY }}>{r.subsystem}</td>
              <td style={td}>{Number(r.betweenness || 0).toFixed(3)}</td>
              <td style={td}>{Number(r.pagerank || 0).toFixed(3)}</td>
              <td style={td}>{fmt(r.in_degree)}</td>
              <td style={td}>{fmt(r.out_degree)}</td>
            </tr>))}</tbody>
        </table>
      </EmptyOrEach>
    </div>
  );
}

// ---- 9. Ignition/termination (subsystem, new scope) ------------------------
function IgnitionTerminationSubsystemPanel({ city }) {
  const [rows, loading, rowsRaw] = useMetric(city, 'ignition');
  const chartData = rows.map((r) => ({ subsystem: r.subsystem, ignition: Number(r.ignition_count || 0), termination: Number(r.termination_count || 0) }));
  return (
    <div style={card}>
      <h3 style={h3}>Subsystem Ignition vs. Termination Counts (New Scope)</h3>
      <p style={note}>Raw ignition/termination event counts per subsystem from the serial-grain run — complements the days/percentage-based device-grain Ignition&rarr;Termination view in the Cascade Analytics tab.</p>
      <FilterNarrowedHint shown={rows.length} total={rowsRaw} />
      <EmptyOrEach rows={rows} loading={loading} emptyText="No subsystem ignition/termination data yet — run the PS2 serial-grain notebook.">
        <div style={{ width: '100%', height: 260 }}>
          <ResponsiveContainer>
            <BarChart data={chartData}>
              <CartesianGrid strokeDasharray="3 3" stroke={LINE} />
              <XAxis dataKey="subsystem" tick={{ fontSize: 11 }} />
              <YAxis tick={{ fontSize: 11 }} />
              <Tooltip />
              <Legend />
              <Bar dataKey="ignition" name="Ignition count" fill={P.red}>
            <LabelList dataKey="ignition" position="top" formatter={fmtV} style={VLAB} />
          </Bar>
              <Bar dataKey="termination" name="Termination count" fill={P.blue}>
            <LabelList dataKey="termination" position="top" formatter={fmtV} style={VLAB} />
          </Bar>
            </BarChart>
          </ResponsiveContainer>
        </div>
      </EmptyOrEach>
    </div>
  );
}

// ---- 10. Facility contagion (facility grain, new table) --------------------
function FacilityContagionPanel({ city }) {
  const [rows, loading, rowsRaw] = useMetric(city, 'facility');
  const maxD = Math.max(1, ...rows.map((r) => r.cascade_days || 0));
  return (
    <div style={card}>
      <h3 style={h3}>Facility Contagion (Facility Grain)</h3>
      <p style={note}>Per-facility cascade-day burden, distinct devices involved, and same-day multi-device contagion rate — the facility-level companion to the city-wide contagion summary shown in the Cascade Overview tab.</p>
      <FilterNarrowedHint shown={rows.length} total={rowsRaw} />
      <EmptyOrEach rows={rows} loading={loading} emptyText="No facility-grain contagion data yet — run the PS2 serial-grain notebook.">
        <table style={{ width:'100%', borderCollapse:'collapse' }}>
          <thead><tr><th style={th}>Facility</th><th style={{ ...th, width:'30%' }}>Cascade days</th><th style={th}>Distinct devices</th><th style={th}>Contagion rate</th></tr></thead>
          <tbody>{rows.slice(0, 25).map((r, i) => (
            <tr key={i}>
              <td style={{ ...td, fontWeight:700, color:NAVY }}>{r.facility_id}</td>
              <td style={td}><div style={{ display:'flex', alignItems:'center', gap:8 }}>
                <div style={{ background:'#F0F4F8', borderRadius:6, height:10, flex:1 }}>
                  <div style={{ width:`${((r.cascade_days || 0) / maxD) * 100}%`, height:10, borderRadius:6, background:P.teal }} /></div>
                <span style={{ minWidth:34, color:INK }}>{fmt(r.cascade_days)}</span></div></td>
              <td style={td}>{fmt(r.distinct_devices)}</td>
              <td style={td}>{pct1(r.contagion_rate)}</td>
            </tr>))}</tbody>
        </table>
      </EmptyOrEach>
    </div>
  );
}

export default function PS2SerialGrainAnalytics({ city = 'CHI', onAnalyse }) {
  // Shared serial -> device lookup for every serial-keyed panel's Analyse
  // button below (chronic recurrence, cascade-day recurrence, lead/lag
  // timing, association rules). Panels keyed by subsystem/facility/entity
  // grain (suppression, cross-PS, sankey, network, ignition, facility) have
  // no single device/serial per row, so they correctly have no Analyse button.
  const serialDeviceMap = useSerialDeviceMap(city);

  return (
    <div>
      <SuppressionSummaryPanel city={city} />
      <ChronicRecurrencePanel city={city} serialDeviceMap={serialDeviceMap} onAnalyse={onAnalyse} />
      <RecurrenceSerialPanel city={city} serialDeviceMap={serialDeviceMap} onAnalyse={onAnalyse} />
      <LeadLagTimingPanel city={city} serialDeviceMap={serialDeviceMap} onAnalyse={onAnalyse} />
      <AssociationRulesSerialPanel city={city} serialDeviceMap={serialDeviceMap} onAnalyse={onAnalyse} />
      <BusinessImpactAndVelocityPanel city={city} />
      <CrossPSAttributionPanel city={city} />
      <CascadeSankeyPanel city={city} />
      <NetworkCentralitySubsystemPanel city={city} />
      <IgnitionTerminationSubsystemPanel city={city} />
      <FacilityContagionPanel city={city} />
    </div>
  );
}
