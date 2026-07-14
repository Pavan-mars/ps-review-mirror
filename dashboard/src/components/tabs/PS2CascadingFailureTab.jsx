import React, { useState } from 'react';
import {
  BarChart, Bar, Cell,
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
];
const nfmt = (v) => (v === null || v === undefined ? '-' : Number(v).toLocaleString());

export default function PS2CascadingFailureTab({ city }) {
  const cityName = CITIES.find((c) => c.id === city)?.name || city;
  const [tab, setTab] = useState('overview');

  const windowDist = useLiveData(getPS2CascadeWindowDistribution(city), () => apiPS2Windows(city), [city]);
  const hub = useLiveData(getPS2SubsystemHub(city), () => apiPS2Hub(city), [city]);
  const facility = useLiveData(getPS2FacilityContagion(city), () => apiPS2Facility(city), [city]);
  const rules = useLiveData(getPS2AssociationRules(city), () => apiPS2AssociationRules(city), [city]);
  const regimes = useLiveData(getPS2HMMRegimes(city), () => apiPS2HMMRegimes(city), [city]);
  const windowDetail = useLiveData(getPS2WindowDetail(city), () => apiPS2WindowDetail(city), [city]);
  const topDevices = useLiveData(getPS2TopDevices(city), () => apiPS2TopDevices(city), [city]);

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

  return (
    <div>
      {/* KPI cards — always visible */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 16, marginBottom: 20 }}>
        <div className="card">
          <div className="card-header">Total Cascade-Days</div>
          <div className="kpi-value">{windowDist.total_cascade_days.toLocaleString()}</div>
          <div className="kpi-label">{cityName}, all devices</div>
        </div>
        <div className="card">
          <div className="card-header">Slow Cascades (60+ min)</div>
          <div className="kpi-value" style={{ color: '#ef4444' }}>70.0%</div>
          <div className="kpi-label">Carry {windowDist.slow_vs_fast_fault_multiplier}x more faults, span {windowDist.slow_vs_fast_duration_multiplier}x longer</div>
        </div>
        <div className="card">
          <div className="card-header">Facility-Level Contagion</div>
          <div className="kpi-value">{facility.multi_device_contagion_pct}%</div>
          <div className="kpi-label">of facility-cascade-days are multi-device</div>
        </div>
        <div className="card">
          <div className="card-header">Subsystem Hub</div>
          <div className="kpi-value" style={{ fontSize: 22 }}>{hub.hub_pair.join(' ↔ ')}</div>
          <div className="kpi-label">max phi {hub.edges[0].phi} ({hub.edges[0].source}-{hub.edges[0].target})</div>
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
            <p style={{ fontSize: 12, opacity: 0.65, padding: '0 16px 12px' }}>
              70% of cascades resolve slowly (60+ minutes) rather than fast — these slow cascades carry
              9.4x more faults and span 1,881x longer than fast (0-5 min) cascades, making them the
              higher-value target for early intervention.
            </p>
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

          <div className="card" style={{ overflowX: 'auto' }}>
            <div className="card-header">Subsystem Association Rules — {cityName} (refreshed weekly, not daily)</div>
            <table className="data-table">
              <thead><tr><th>Rule</th><th>Support</th><th>Confidence</th><th>Lift</th><th>Conviction</th></tr></thead>
              <tbody>
                {rules.map((r, i) => (
                  <tr key={i}>
                    <td style={{ fontFamily: 'monospace', fontSize: 12 }}>{r.antecedent} &rarr; {r.consequent}</td>
                    <td>{r.support.toFixed(2)}</td>
                    <td>{r.confidence.toFixed(2)}</td>
                    <td><span style={{ color: r.lift > 5 ? '#ef4444' : '#f59e0b', fontWeight: 700 }}>{r.lift.toFixed(3)}</span></td>
                    <td style={{ opacity: 0.75 }}>{r.conviction.toFixed(1)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            <p style={{ fontSize: 11, opacity: 0.6, padding: '8px 16px 12px' }}>
              Note: lift and conviction are different statistics — max lift is 9.678, max conviction is 99.0.
              An earlier project record conflated these; this table shows both correctly.
            </p>
          </div>
        </div>
      )}

      {/* ============ DEVICE-LEVEL HOTSPOTS ============ */}
      {tab === 'devices' && (
        <div>
          <div className="card" style={{ marginBottom: 24 }}>
            <div className="card-header">Top {chartDevs.length} Cascade-Active Devices — window breakdown (cascade-days)</div>
            <ResponsiveContainer width="100%" height={360}>
              <BarChart data={chartDevs} margin={{ bottom: 40 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.1)" />
                <XAxis dataKey="device_id" angle={-40} textAnchor="end" interval={0} height={70} tick={{ fontSize: 10 }} />
                <YAxis tick={{ fontSize: 11 }} />
                <Tooltip formatter={(v, n) => [Number(v).toLocaleString(), n]} />
                <Legend />
                {WKEYS.map((w) => (
                  <Bar key={w.key} dataKey={w.key} name={w.label} stackId="win" fill={w.color} />
                ))}
              </BarChart>
            </ResponsiveContainer>
            <p style={{ fontSize: 12, opacity: 0.65, padding: '0 16px 12px' }}>
              Each bar is one physical device, stacked by cascade propagation window. TVMs (blue-heavy near
              the left) show a mixed fast/slow profile; the BMV validators are dominated by the 60min+ band —
              their cascades resolve slowly, matching the fleet-wide 70% slow-cascade finding at the device level.
            </p>
          </div>

          <div className="card" style={{ overflowX: 'auto' }}>
            <div className="card-header">Device Cascade Leaderboard — {cityName} (top 20 by total cascade-days)</div>
            <table className="data-table">
              <thead>
                <tr>
                  <th>#</th><th>Device</th><th>Category</th><th>Cascade-days</th>
                  <th>0-5m</th><th>5-15m</th><th>15-30m</th><th>30-60m</th><th>60m+</th><th>Slow %</th>
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
                    </tr>
                  );
                })}
              </tbody>
            </table>
            <p style={{ fontSize: 11, opacity: 0.6, padding: '8px 16px 12px' }}>
              Cascade-days = number of days the device participated in a cascade chain, split by the
              propagation window the chain resolved in. "Slow %" is the share of a device's cascade-days
              in the 60min+ band — the maintenance-priority signal. Validators (BMV) run 82-90% slow.
            </p>
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
              <p style={{ fontSize: 11.5, opacity: 0.65, padding: '0 16px 12px' }}>
                Slow (60min+) cascades average 9.35 devices per chain — roughly 2.5x the fast windows —
                and reach up to 3,876 devices in a single chain.
              </p>
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
              <p style={{ fontSize: 11.5, opacity: 0.65, padding: '0 16px 12px' }}>
                The 60min+ band spans ~820 minutes on average (~13.7 hours) versus &lt;1 minute for fast
                cascades — the 1,881x duration multiplier, shown per window.
              </p>
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
                    <td>{w.chain_len_mean.toFixed(3)}</td>
                    <td>{w.chain_len_median.toFixed(1)}</td>
                    <td>{nfmt(w.chain_len_max)}</td>
                    <td>{w.span_min_mean.toFixed(2)}</td>
                    <td>{w.span_min_median.toFixed(2)}</td>
                    <td>{w.velocity.toFixed(2)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            <p style={{ fontSize: 11, opacity: 0.6, padding: '8px 16px 12px' }}>
              Velocity = minutes elapsed per fault added to the chain. Fast cascades add a fault every
              few seconds (near-simultaneous, likely a shared-facility trigger); slow cascades add one
              roughly every ~2.8 hours — a genuinely propagating, interruptible failure.
            </p>
          </div>
        </div>
      )}

      {/* ============ CASCADE ANALYTICS (paths, ignition->termination, business impact) ============ */}
      {tab === 'analytics' && <PS2AnalyticsSections city={city} />}

      {/* ============ DEEP ANALYTICS (correlation, conditional, markov, HMM, network, error codes, device drill-down) ============ */}
      {tab === 'deep' && <PS2RichAnalytics city={city} />}
    </div>
  );
}
