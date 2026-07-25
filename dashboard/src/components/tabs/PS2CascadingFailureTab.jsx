import React, { useState, useEffect, useMemo } from 'react';
import {
  BarChart, Bar, Cell,
  PieChart, Pie,
  XAxis, YAxis, CartesianGrid, Tooltip, Legend,
  ResponsiveContainer,
} from 'recharts';
import {
  CITIES,
  getPS2CascadeWindowDistribution,
  getPS2SubsystemHub,
  getPS2FacilityContagion,
  getPS2AssociationRules,
  getPS2HMMRegimes,
  getPS2WindowDetail,
  getPS2TopDevices,
} from '../../data/mockData';
import {
  apiPS2Windows, apiPS2Hub, apiPS2Facility, apiPS2AssociationRules, apiPS2HMMRegimes,
  apiPS2WindowDetail, apiPS2TopDevices, useLiveData,
} from '../../data/api';
import PS2AnalyticsSections from './PS2AnalyticsSections';
import PS2RichAnalytics from './PS2RichAnalytics';
import PS2SerialGrainAnalytics from './PS2SerialGrainAnalytics';
import Device360Modal from './Device360Modal';
import AnalyseButton from '../shared/AnalyseButton';
import { useFilters } from '../../context/FilterContext';
import { applyPS2Filters, isAnyPS2FilterActive, DEVICE_CATEGORY_LABEL } from '../../utils/ps2Filters';

// PS2's own component (subsystem) and failure-type (error code) vocabularies,
// registered into the dashboard-wide FilterContext while this tab is mounted
// -- see registerComponentOptions/registerFailureTypeOptions in
// context/FilterContext.jsx. Subsystem list matches getPS2Phi()'s S array;
// error codes match getPS2ErrorCodes()'s known codes.
const PS2_SUBSYSTEMS = ['ALARM', 'BHU', 'CHU', 'COMMS', 'CSC_READER', 'DOPP', 'GATE_MECH', 'PRINTER', 'SCRST', 'SYSTEM'];
const PS2_ERROR_CODES = ['101', '50101', '2201', '2202'];

// PS2 = Cascading-Failure analysis (descriptive-only, batch, no ML endpoint).
// Live from the cubic-mars-dashboard-api (VITE_API_BASE_URL); mockData is the
// instant initial value + the fallback if the API is unset/unreachable.
const WINDOW_COLORS = ['#3b82f6', '#6366f1', '#8b5cf6', '#f59e0b', '#ef4444'];
const WKEYS = [
  { key: 'w0_5', label: '0-5min', color: '#3b82f6' },
  { key: 'w5_15', label: '5-15min', color: '#6366f1' },
  { key: 'w15_30', label: '15-30min', color: '#8b5cf6' },
  { key: 'w30_60', label: '30-60min', color: '#f59e0b' },
  { key: 'w60plus', label: '60min+', color: '#ef4444' },
];
const REGIME_COLORS = { Critical: '#ef4444', Minor: '#f59e0b', Moderate: '#3b82f6' };
const CAT_COLOR = { TVM: '#3b82f6', VALIDATOR: '#10b981', GATE: '#f59e0b' };
const SUB_TABS = [
  { key: 'overview', label: 'Cascade Overview' },
  { key: 'devices', label: 'Device-Level Hotspots' },
  { key: 'mechanics', label: 'Window Mechanics' },
  { key: 'analytics', label: 'Cascade Analytics' },
  { key: 'deep', label: 'Deep Analytics' },
  { key: 'serialgrain', label: 'Serial-Grain & New Analytics' },
];
const nfmt = (v) => (v === null || v === undefined ? '-' : Number(v).toLocaleString());
// Null-safe decimal formatter — fixes the "Window Mechanics" crash where a raw
// null field (e.g. from a partial live API row) hit `.toFixed()` directly and
// threw, unmounting the whole app (no error boundary catches render errors).
const dfmt = (v, d) => (v === null || v === undefined || Number.isNaN(Number(v)) ? '--' : Number(v).toFixed(d));
const parseSubsystems = (s) => (s || '').split('+').map((t) => t.trim()).filter(Boolean);

export default function PS2CascadingFailureTab({ city }) {
  const cityName = CITIES.find((c) => c.id === city)?.name || city;
  const [tab, setTab] = useState('overview');
  // Single Device360Modal instance for the whole PS2 tab -- every sub-tab's
  // Analyse button (device tables directly, serial tables via the resolved
  // serial->device map) calls this same setter rather than each sub-component
  // mounting its own modal + state.
  const [analyseDevice, setAnalyseDevice] = useState(null);

  const filters = useFilters();
  const { registerComponentOptions, registerFailureTypeOptions } = filters;
  // Register PS2's filter vocabulary into the shared FilterBar while this tab
  // is mounted; clear it on unmount so switching to PS1/PS3/PS4/PS5 doesn't
  // keep showing PS2-specific Component/Failure-Type facets.
  useEffect(() => {
    registerComponentOptions(PS2_SUBSYSTEMS);
    registerFailureTypeOptions(PS2_ERROR_CODES);
    return () => {
      registerComponentOptions([]);
      registerFailureTypeOptions([]);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const windowDist = useLiveData(getPS2CascadeWindowDistribution(city), () => apiPS2Windows(city), [city]);
  const hub = useLiveData(getPS2SubsystemHub(city), () => apiPS2Hub(city), [city]);
  const facility = useLiveData(getPS2FacilityContagion(city), () => apiPS2Facility(city), [city]);
  const rulesRaw = useLiveData(getPS2AssociationRules(city), () => apiPS2AssociationRules(city), [city]);
  const regimes = useLiveData(getPS2HMMRegimes(city), () => apiPS2HMMRegimes(city), [city]);
  const windowDetail = useLiveData(getPS2WindowDetail(city), () => apiPS2WindowDetail(city), [city]);
  const topDevicesRaw = useLiveData(getPS2TopDevices(city), () => apiPS2TopDevices(city), [city]);

  const rules = useMemo(() => applyPS2Filters(rulesRaw, filters), [rulesRaw, filters]);
  const topDevices = useMemo(() => applyPS2Filters(topDevicesRaw, filters), [topDevicesRaw, filters]);
  const filtersActive = isAnyPS2FilterActive(filters);

  // High-level: device-type breakdown by cascade-day share -- feeds the
  // Overview donut chart. Built from the (filter-aware) topDevices list so it
  // stays in sync with whatever the shared filter bar has narrowed to.
  const deviceTypeBreakdown = useMemo(() => {
    const totals = {};
    (topDevices || []).forEach((d) => {
      if (!d.category) return;
      totals[d.category] = (totals[d.category] || 0) + Number(d.cascade_days || 0);
    });
    return Object.entries(totals)
      .map(([category, cascade_days]) => ({ category, cascade_days }))
      .filter((r) => r.cascade_days > 0);
  }, [topDevices]);

  // Granular/component-level: subsystem involvement ranking -- how many of
  // the currently-visible association rules each subsystem appears in. Reuses
  // the same antecedent/consequent parsing as the rule-row Focus button.
  const subsystemInvolvement = useMemo(() => {
    const counts = {};
    (rules || []).forEach((r) => {
      const subs = Array.from(new Set([...parseSubsystems(r.antecedent), ...parseSubsystems(r.consequent)]));
      subs.forEach((s) => { counts[s] = (counts[s] || 0) + 1; });
    });
    return Object.entries(counts)
      .map(([subsystem, rule_count]) => ({ subsystem, rule_count }))
      .sort((a, b) => b.rule_count - a.rule_count)
      .slice(0, 10);
  }, [rules]);

  // No PS2 pipeline has run for this city yet — descriptive-only, batch, no model/endpoint
  if (!windowDist) {
    return (
      <div className="card" style={{ padding: 32, textAlign: 'center' }}>
        <div className="card-header">PS2 — Cascading Failure Analysis</div>
        <p style={{ opacity: 0.7, marginTop: 12 }}>
          No PS2 cascade analysis has been run for {cityName} yet. PS2 is descriptive-only
          (no ML model/endpoint) and currently only has a completed, locked run for Chicago.
          Extending PS2 to other cities is tracked under task #80 (Databricks medallion
          orchestration) once their bronze/silver/gold layers are built.
        </p>
      </div>
    );
  }

  const devs = topDevices || [];
  const chartDevs = devs.slice(0, 14);
  const devsRawCount = (topDevicesRaw || []).length;
  const rulesRawCount = (rulesRaw || []).length;

  const FilterHint = ({ shown, total }) => (
    !filtersActive ? null : (
      <div style={{ fontSize: 11.5, color: '#B08A2E', padding: '0 16px 8px', fontWeight: 600 }}>
        Showing {shown} of {total} — narrowed by the active Device/Serial, Device-type, Component, or Failure-type filter.
      </div>
    )
  );
  const NoFilterMatches = () => (
    <p style={{ fontSize: 12.5, color: '#B08A2E', padding: '12px 16px' }}>
      No rows match the current filters — try widening the Component, Failure Type, or Device/Serial search in the filter bar above.
    </p>
  );

  // Clicking an association-rule row focuses the shared Component filter on
  // exactly the subsystems in that rule (cross-tab drill-down: every other
  // PS2 panel that respects Component filtering narrows to the same pair).
  const focusRuleComponents = (rule) => {
    const subs = Array.from(new Set([...parseSubsystems(rule.antecedent), ...parseSubsystems(rule.consequent)]));
    if (!subs.length) return;
    filters.registerComponentOptions(PS2_SUBSYSTEMS); // ensure facet stays registered
    subs.forEach((s) => { if (!filters.selectedComponents.includes(s)) filters.toggleComponent(s); });
    const toDeselect = filters.componentOptions.filter((c) => !subs.includes(c) && filters.selectedComponents.includes(c));
    toDeselect.forEach((c) => filters.toggleComponent(c));
  };

  // Donut-slice drill-down: isolate one device category (TVMs/Gates/Validators)
  // in the shared Device-type filter -- narrows every filter-aware PS2 panel.
  const focusDeviceType = (category) => {
    const label = DEVICE_CATEGORY_LABEL[category] || category;
    filters.deviceOptions.forEach((opt) => {
      const shouldBeSelected = opt === label;
      const isSelected = filters.selectedDevices.includes(opt);
      if (shouldBeSelected !== isSelected) filters.toggleDevice(opt);
    });
  };

  // Ranking-bar drill-down: isolate one subsystem in the shared Component
  // filter -- the single-subsystem sibling of focusRuleComponents above.
  const focusSubsystemOnly = (subsystem) => {
    filters.componentOptions.forEach((c) => {
      const shouldBeSelected = c === subsystem;
      const isSelected = filters.selectedComponents.includes(c);
      if (shouldBeSelected !== isSelected) filters.toggleComponent(c);
    });
  };

  return (
    <div>
      {analyseDevice && <Device360Modal deviceId={analyseDevice} onClose={() => setAnalyseDevice(null)} />}
      {/* KPI cards — always visible */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 16, marginBottom: 20 }}>
        <div className="card">
          <div className="card-header">Total Cascade-Days</div>
          <div className="kpi-value">{windowDist.total_cascade_days.toLocaleString()}</div>
          <div className="kpi-label">{cityName}, all devices</div>
        </div>
        <div className="card">
          <div className="card-header">Slow Cascades (60+ min)</div>
          <div className="kpi-value" style={{ color: '#ef4444' }}>{dfmt((windowDist.windows[windowDist.windows.length - 1] || {}).pct, 1)}%</div>
          <div className="kpi-label">Carry {windowDist.slow_vs_fast_fault_multiplier}x more faults, span {windowDist.slow_vs_fast_duration_multiplier}x longer</div>
        </div>
        <div className="card">
          <div className="card-header">Facility-Level Contagion</div>
          <div className="kpi-value">{facility.multi_device_contagion_pct}%</div>
          <div className="kpi-label">of facility-cascade-days are multi-device</div>
        </div>
        <div className="card">
          <div className="card-header">Subsystem Hub</div>
          <div className="kpi-value" style={{ fontSize: 22 }}>{(hub.hub_pair || []).join(' ↔ ') || '--'}</div>
          <div className="kpi-label">{(hub.edges && hub.edges[0]) ? `max phi ${hub.edges[0].phi} (${hub.edges[0].source}-${hub.edges[0].target})` : 'no phi edges yet'}</div>
        </div>
      </div>

      <div className="tab-container">
        {SUB_TABS.map((t) => (
          <button key={t.key} className={`tab ${tab === t.key ? 'active' : ''}`} onClick={() => setTab(t.key)}>{t.label}</button>
        ))}
      </div>

      {/* ============ CASCADE OVERVIEW ============ */}
      {tab === 'overview' && (
        <div>
          <div className="card" style={{ marginBottom: 24 }}>
            <div className="card-header">Cascade Propagation Window Distribution — {cityName}</div>
            <ResponsiveContainer width="100%" height={300}>
              <BarChart data={windowDist.windows}>
                <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.1)" />
                <XAxis dataKey="window" tick={{ fontSize: 12 }} />
                <YAxis tick={{ fontSize: 11 }} label={{ value: 'Cascade-days', angle: -90, position: 'insideLeft' }} />
                <Tooltip formatter={(v, n, p) => [`${v.toLocaleString()} days (${p.payload.pct}%)`, 'Cascade-days']} />
                <Bar dataKey="days" radius={[4, 4, 0, 0]}>
                  {windowDist.windows.map((_, i) => <Cell key={i} fill={WINDOW_COLORS[i]} />)}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          </div>

          <div className="grid-2" style={{ marginBottom: 24 }}>
            <div className="card">
              <div className="card-header">Facility Contagion Hotspot — {cityName}</div>
              <div style={{ padding: 16 }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
                  <div>
                    <div style={{ fontSize: 20, fontWeight: 700 }}>Facility {facility.top_hotspot.facility_id} &mdash; {facility.top_hotspot.facility_name}</div>
                    <div style={{ fontSize: 12, opacity: 0.7 }}>Same-day multi-device cascade hotspot</div>
                  </div>
                  <div style={{ textAlign: 'right' }}>
                    <div style={{ fontSize: 28, fontWeight: 700, color: '#ef4444' }}>{facility.top_hotspot.min_devices_same_day}&ndash;{facility.top_hotspot.max_devices_same_day}</div>
                    <div style={{ fontSize: 11, opacity: 0.7 }}>devices cascading same-day</div>
                  </div>
                </div>
                <div style={{ fontSize: 12, opacity: 0.75 }}>
                  Monthly contagion rate trending {facility.monthly_contagion_rate_trend.start_pct}% &rarr; {facility.monthly_contagion_rate_trend.end_pct}%
                  across {facility.total_facility_cascade_days.toLocaleString()} total facility-cascade-days.
                </div>
              </div>
            </div>

            <div className="card">
              <div className="card-header">HMM Cascade Regimes — {cityName}</div>
              <ResponsiveContainer width="100%" height={220}>
                <BarChart data={regimes} layout="vertical" margin={{ left: 60 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.1)" />
                  <XAxis type="number" tick={{ fontSize: 11 }} unit="%" />
                  <YAxis dataKey="regime" type="category" width={70} tick={{ fontSize: 12 }} />
                  <Tooltip formatter={(v, n, p) => [`${v}% • dwell ${p.payload.dwell_days_min}-${p.payload.dwell_days_max}d`, 'Share']} />
                  <Bar dataKey="pct" radius={[0, 4, 4, 0]}>
                    {regimes.map((r, i) => <Cell key={i} fill={REGIME_COLORS[r.regime]} />)}
                  </Bar>
                </BarChart>
              </ResponsiveContainer>
            </div>
          </div>

          <div className="grid-2" style={{ marginBottom: 24 }}>
            <div className="card">
              <div className="card-header">Device-Type Breakdown — Cascade-Days Share</div>
              <p style={{ fontSize: 11, opacity: 0.6, padding: '0 16px 4px' }}>Click a slice to focus the shared Device-type filter on that category.</p>
              {deviceTypeBreakdown.length === 0 ? (
                <NoFilterMatches />
              ) : (
                <ResponsiveContainer width="100%" height={260}>
                  <PieChart>
                    <Pie
                      data={deviceTypeBreakdown}
                      dataKey="cascade_days"
                      nameKey="category"
                      innerRadius={55}
                      outerRadius={90}
                      paddingAngle={2}
                      cursor="pointer"
                      onClick={(d) => d && d.category && focusDeviceType(d.category)}
                      label={({ category, percent }) => `${category} ${(percent * 100).toFixed(0)}%`}
                    >
                      {deviceTypeBreakdown.map((d, i) => <Cell key={i} fill={CAT_COLOR[d.category] || '#94a3b8'} />)}
                    </Pie>
                    <Tooltip formatter={(v, n, p) => [`${Number(v).toLocaleString()} cascade-days`, p.payload.category]} />
                    <Legend />
                  </PieChart>
                </ResponsiveContainer>
              )}
            </div>
            <div className="card">
              <div className="card-header">Subsystem Involvement Ranking — Association Rules</div>
              <p style={{ fontSize: 11, opacity: 0.6, padding: '0 16px 4px' }}>How many of the visible rules each subsystem appears in. Click a bar to focus the shared Component filter.</p>
              {subsystemInvolvement.length === 0 ? (
                <NoFilterMatches />
              ) : (
                <ResponsiveContainer width="100%" height={260}>
                  <BarChart data={subsystemInvolvement} layout="vertical" margin={{ left: 10 }}>
                    <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.1)" />
                    <XAxis type="number" tick={{ fontSize: 11 }} allowDecimals={false} />
                    <YAxis dataKey="subsystem" type="category" width={90} tick={{ fontSize: 11 }} />
                    <Tooltip formatter={(v) => [`${v} rule(s)`, 'Involvement']} />
                    <Bar dataKey="rule_count" radius={[0, 4, 4, 0]} cursor="pointer"
                      onClick={(d) => d && d.subsystem && focusSubsystemOnly(d.subsystem)}>
                      {subsystemInvolvement.map((_, i) => <Cell key={i} fill={WINDOW_COLORS[i % WINDOW_COLORS.length]} />)}
                    </Bar>
                  </BarChart>
                </ResponsiveContainer>
              )}
            </div>
          </div>

          <div className="card" style={{ overflowX: 'auto' }}>
            <div className="card-header">Subsystem Association Rules — {cityName} (refreshed weekly, not daily)</div>
            <FilterHint shown={rules.length} total={rulesRawCount} />
            {rules.length === 0 && rulesRawCount > 0 ? <NoFilterMatches /> : (
            <table className="data-table">
              <thead><tr><th>Rule</th><th>Support</th><th>Confidence</th><th>Lift</th><th>Conviction</th><th></th></tr></thead>
              <tbody>
                {rules.map((r, i) => (
                  <tr key={i}>
                    <td style={{ fontFamily: 'monospace', fontSize: 12 }}>{r.antecedent} &rarr; {r.consequent}</td>
                    <td>{dfmt(r.support, 2)}</td>
                    <td>{dfmt(r.confidence, 2)}</td>
                    <td><span style={{ color: (r.lift || 0) > 5 ? '#ef4444' : '#f59e0b', fontWeight: 700 }}>{dfmt(r.lift, 3)}</span></td>
                    <td style={{ opacity: 0.75 }}>{dfmt(r.conviction, 1)}</td>
                    <td>
                      <button onClick={() => focusRuleComponents(r)} title="Drill down: focus the Component filter on this rule's subsystems"
                        style={{ fontSize: 10.5, padding: '3px 8px', borderRadius: 6, border: '1px solid var(--border)', background: '#fff', cursor: 'pointer', color: 'var(--text-secondary)' }}>
                        Focus
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            )}
          </div>
        </div>
      )}

      {/* ============ DEVICE-LEVEL HOTSPOTS ============ */}
      {tab === 'devices' && (
        <div>
          <div className="card" style={{ marginBottom: 24 }}>
            <div className="card-header">Top {chartDevs.length} Cascade-Active Devices — window breakdown (cascade-days)</div>
            <p style={{ fontSize: 11, opacity: 0.6, padding: '0 16px 4px' }}>Click any bar to drill into that device's cross-PS Device 360 view.</p>
            <FilterHint shown={devs.length} total={devsRawCount} />
            {devs.length === 0 && devsRawCount > 0 ? <NoFilterMatches /> : (
            <ResponsiveContainer width="100%" height={360}>
              <BarChart data={chartDevs} margin={{ bottom: 40 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.1)" />
                <XAxis dataKey="device_id" angle={-40} textAnchor="end" interval={0} height={70} tick={{ fontSize: 10 }} />
                <YAxis tick={{ fontSize: 11 }} />
                <Tooltip formatter={(v, n) => [Number(v).toLocaleString(), n]} />
                <Legend />
                {WKEYS.map((w) => (
                  <Bar key={w.key} dataKey={w.key} name={w.label} stackId="win" fill={w.color} cursor="pointer"
                    onClick={(data) => data && data.device_id && setAnalyseDevice(data.device_id)} />
                ))}
              </BarChart>
            </ResponsiveContainer>
            )}
          </div>

          <div className="card" style={{ overflowX: 'auto' }}>
            <div className="card-header">Device Cascade Leaderboard — {cityName} (top 20 by total cascade-days)</div>
            <FilterHint shown={devs.length} total={devsRawCount} />
            {devs.length === 0 && devsRawCount > 0 ? <NoFilterMatches /> : (
            <table className="data-table">
              <thead>
                <tr>
                  <th>#</th><th>Device</th><th>Category</th><th>Cascade-days</th>
                  <th>0-5m</th><th>5-15m</th><th>15-30m</th><th>30-60m</th><th>60m+</th><th>Slow %</th><th></th>
                </tr>
              </thead>
              <tbody>
                {devs.map((d) => {
                  const slowPct = d.cascade_days ? Math.round((d.w60plus / d.cascade_days) * 100) : 0;
                  return (
                    <tr key={d.device_id}>
                      <td style={{ opacity: 0.6 }}>{d.dev_rank}</td>
                      <td style={{ fontFamily: 'monospace', fontWeight: 600 }}>{d.device_id}</td>
                      <td><span style={{ color: CAT_COLOR[d.category] || '#94a3b8', fontWeight: 600 }}>{d.category}</span></td>
                      <td style={{ fontWeight: 700 }}>{nfmt(d.cascade_days)}</td>
                      <td>{nfmt(d.w0_5)}</td><td>{nfmt(d.w5_15)}</td><td>{nfmt(d.w15_30)}</td><td>{nfmt(d.w30_60)}</td>
                      <td style={{ color: '#ef4444', fontWeight: 600 }}>{nfmt(d.w60plus)}</td>
                      <td><span style={{ color: slowPct >= 70 ? '#ef4444' : '#f59e0b', fontWeight: 700 }}>{slowPct}%</span></td>
                      <td><AnalyseButton onClick={() => setAnalyseDevice(d.device_id)} /></td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
            )}
          </div>
        </div>
      )}

      {/* ============ WINDOW MECHANICS ============ */}
      {tab === 'mechanics' && (
        <div>
          <div className="grid-2" style={{ marginBottom: 24 }}>
            <div className="card">
              <div className="card-header">Mean Chain Length by Window</div>
              <ResponsiveContainer width="100%" height={260}>
                <BarChart data={windowDetail || []}>
                  <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.1)" />
                  <XAxis dataKey="window" tick={{ fontSize: 11 }} />
                  <YAxis tick={{ fontSize: 11 }} label={{ value: 'devices in chain', angle: -90, position: 'insideLeft', style: { fontSize: 10 } }} />
                  <Tooltip formatter={(v) => Number(v).toFixed(3)} />
                  <Bar dataKey="chain_len_mean" name="Mean chain length" radius={[4, 4, 0, 0]}>
                    {(windowDetail || []).map((_, i) => <Cell key={i} fill={WINDOW_COLORS[i]} />)}
                  </Bar>
                </BarChart>
              </ResponsiveContainer>
            </div>
            <div className="card">
              <div className="card-header">Mean Span (minutes) by Window</div>
              <ResponsiveContainer width="100%" height={260}>
                <BarChart data={windowDetail || []}>
                  <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.1)" />
                  <XAxis dataKey="window" tick={{ fontSize: 11 }} />
                  <YAxis tick={{ fontSize: 11 }} />
                  <Tooltip formatter={(v) => `${Number(v).toFixed(1)} min`} />
                  <Bar dataKey="span_min_mean" name="Mean span (min)" radius={[4, 4, 0, 0]}>
                    {(windowDetail || []).map((_, i) => <Cell key={i} fill={WINDOW_COLORS[i]} />)}
                  </Bar>
                </BarChart>
              </ResponsiveContainer>
            </div>
          </div>

          <div className="card" style={{ overflowX: 'auto' }}>
            <div className="card-header">Cascade Window Mechanics — {cityName}</div>
            <table className="data-table">
              <thead>
                <tr>
                  <th>Window</th><th>Cascade-days</th><th>Chain len (mean)</th><th>Chain len (median)</th>
                  <th>Chain len (max)</th><th>Span mean (min)</th><th>Span median (min)</th><th>Velocity (min/fault)</th>
                </tr>
              </thead>
              <tbody>
                {(windowDetail || []).map((w, i) => (
                  <tr key={i} style={{ background: w.window === '60min+' ? 'rgba(239,68,68,0.1)' : undefined }}>
                    <td style={{ fontWeight: 700 }}>{w.window}</td>
                    <td>{nfmt(w.cascade_days)}</td>
                    <td>{dfmt(w.chain_len_mean, 3)}</td>
                    <td>{dfmt(w.chain_len_median, 1)}</td>
                    <td>{nfmt(w.chain_len_max)}</td>
                    <td>{dfmt(w.span_min_mean, 2)}</td>
                    <td>{dfmt(w.span_min_median, 2)}</td>
                    <td>{dfmt(w.velocity, 2)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* ============ CASCADE ANALYTICS (paths, ignition->termination, business impact) ============ */}
      {tab === 'analytics' && <PS2AnalyticsSections city={city} onAnalyse={setAnalyseDevice} />}

      {/* ============ DEEP ANALYTICS (correlation, conditional, markov, HMM, network, error codes, device drill-down) ============ */}
      {tab === 'deep' && <PS2RichAnalytics city={city} onAnalyse={setAnalyseDevice} />}

      {/* ============ SERIAL-GRAIN & NEW ANALYTICS (chronic recurrence, lead/lag timing, cross-PS
           attribution, cascade sankey, facility contagion, subsystem network/ignition, suppression) ==== */}
      {tab === 'serialgrain' && <PS2SerialGrainAnalytics city={city} onAnalyse={setAnalyseDevice} />}
    </div>
  );
}
