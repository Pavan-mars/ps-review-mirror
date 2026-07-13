import React from 'react';
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
} from '../../data/mockData';
import {
  apiPS2Windows, apiPS2Hub, apiPS2Facility, apiPS2AssociationRules, apiPS2HMMRegimes, useLiveData,
} from '../../data/api';

const WINDOW_COLORS = ['#3b82f6', '#6366f1', '#8b5cf6', '#f59e0b', '#ef4444'];
const REGIME_COLORS = { Critical: '#ef4444', Minor: '#f59e0b', Moderate: '#3b82f6' };

export default function PS2CascadingFailureTab({ city }) {
  const cityName = CITIES.find((c) => c.id === city)?.name || city;

  // Live from the cubic-mars-dashboard-api (VITE_API_BASE_URL); mock is the instant
  // initial value and the fallback if the API is unset/unreachable.
  const windowDist = useLiveData(getPS2CascadeWindowDistribution(city), () => apiPS2Windows(city), [city]);
  const hub = useLiveData(getPS2SubsystemHub(city), () => apiPS2Hub(city), [city]);
  const facility = useLiveData(getPS2FacilityContagion(city), () => apiPS2Facility(city), [city]);
  const rules = useLiveData(getPS2AssociationRules(city), () => apiPS2AssociationRules(city), [city]);
  const regimes = useLiveData(getPS2HMMRegimes(city), () => apiPS2HMMRegimes(city), [city]);

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

  return (
    <div>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 16, marginBottom: 24 }}>
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

      {/* Cascade Window Distribution */}
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
        {/* Facility Contagion Hotspot */}
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

        {/* HMM Regimes */}
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

      {/* Association Rules */}
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
  );
}
