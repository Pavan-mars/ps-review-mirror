import React, { useMemo } from 'react';
import { useNavigate } from 'react-router-dom';
import { BarChart3 } from 'lucide-react';
import {
  BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, Legend,
  ResponsiveContainer, Cell,
} from 'recharts';
import PageHeader from '../components/layout/PageHeader';
import { useFilters } from '../context/FilterContext';
import { useAuth } from '../auth/AuthContext';
import {
  CITIES, DEVICES,
  getModelPerformance,
  getPredictionSummary,
  getAnomalyAlerts,
  getSLAMetrics,
  getErrorCascades,
  getRootCauseFactors,
  getUptimeTrend,
} from '../data/mockData';

// City color lookup
const CITY_COLORS = { CHI: '#6366f1', BOS: '#f59e0b', LAX: '#ef4444', TOC: '#10b981' };
const SEVERITY_COLORS = { Critical: '#ef4444', High: '#f97316', Medium: '#f59e0b', Low: '#3b82f6', Info: '#6b7280' };

export default function ExecutiveOverview() {
  const navigate = useNavigate();
  const { selectedDevices } = useFilters();
  const { canAccessCity } = useAuth();

  // Only show cities the user can access
  const accessibleCities = useMemo(
    () => CITIES.filter((c) => canAccessCity(c.id)),
    [canAccessCity]
  );

  // =========================================================================
  // Section 1: City Summary Card KPIs
  // =========================================================================
  const citySummaries = useMemo(() => {
    return accessibleCities.map((cityObj) => {
      const cityArr = [cityObj.id];

      // PS1: Model accuracy
      const modelPerf = getModelPerformance(cityArr, selectedDevices);
      const avgAccuracy = modelPerf.length
        ? Math.round((modelPerf.reduce((s, m) => s + m.accuracy, 0) / modelPerf.length) * 100) / 100
        : 0;

      // PS1: High-risk devices
      const predSummary = getPredictionSummary(cityArr, selectedDevices);
      const highRiskCount = predSummary.critical + predSummary.high;

      // PS4: Active anomalies
      const alerts = getAnomalyAlerts(cityArr, selectedDevices);
      const activeAnomalies = alerts.filter((a) => a.status === 'Active' || a.status === 'Investigating').length;

      // PS5: Fleet uptime
      const sla = getSLAMetrics(cityArr, selectedDevices);

      return {
        id: cityObj.id,
        name: cityObj.name,
        color: CITY_COLORS[cityObj.id] || cityObj.color,
        avgAccuracy,
        highRiskCount,
        activeAnomalies,
        uptimePct: sla.uptime_pct,
      };
    });
  }, [accessibleCities, selectedDevices]);

  // =========================================================================
  // Section 2: PS-wise Cross-City Comparison Data
  // =========================================================================

  // PS1 - Failure Prediction: Grouped bar — model accuracy per device type across cities
  const ps1Data = useMemo(() => {
    const byDevice = {};
    accessibleCities.forEach((cityObj) => {
      const perf = getModelPerformance([cityObj.id], selectedDevices);
      perf.forEach((m) => {
        if (!byDevice[m.device]) byDevice[m.device] = { device: m.device };
        byDevice[m.device][cityObj.id] = m.accuracy;
      });
    });
    return Object.values(byDevice);
  }, [accessibleCities, selectedDevices]);

  // PS2 - Cascading Failure: Bar chart of cascade chain counts by city
  const ps2Data = useMemo(() => {
    return accessibleCities.map((cityObj) => {
      const cascades = getErrorCascades([cityObj.id], selectedDevices);
      const totalChains = cascades.reduce((sum, c) => sum + c.value, 0);
      return { city: cityObj.id, chains: totalChains, fill: CITY_COLORS[cityObj.id] };
    });
  }, [accessibleCities, selectedDevices]);

  // PS3 - Root Cause: Horizontal bar of top root cause factors by city
  const ps3Data = useMemo(() => {
    const factorMap = {};
    accessibleCities.forEach((cityObj) => {
      const rc = getRootCauseFactors([cityObj.id], selectedDevices);
      rc.hardware.slice(0, 5).forEach((h) => {
        if (!factorMap[h.component]) factorMap[h.component] = { component: h.component };
        factorMap[h.component][cityObj.id] = h.failure_count;
      });
    });
    return Object.values(factorMap).sort((a, b) => {
      const sumA = accessibleCities.reduce((s, c) => s + (a[c.id] || 0), 0);
      const sumB = accessibleCities.reduce((s, c) => s + (b[c.id] || 0), 0);
      return sumB - sumA;
    });
  }, [accessibleCities, selectedDevices]);

  // PS4 - Anomaly Detection: Stacked bar of anomaly counts by severity per city
  const ps4Data = useMemo(() => {
    return accessibleCities.map((cityObj) => {
      const alerts = getAnomalyAlerts([cityObj.id], selectedDevices);
      const row = { city: cityObj.id };
      ['Critical', 'High', 'Medium', 'Low', 'Info'].forEach((sev) => {
        row[sev] = alerts.filter((a) => a.severity === sev).length;
      });
      return row;
    });
  }, [accessibleCities, selectedDevices]);

  // PS5 - SLA & Reliability: Grouped bar of uptime % per city per device type
  const ps5Data = useMemo(() => {
    const byDevice = {};
    accessibleCities.forEach((cityObj) => {
      selectedDevices.forEach((device) => {
        if (!byDevice[device]) byDevice[device] = { device };
        const sla = getSLAMetrics([cityObj.id], [device]);
        byDevice[device][cityObj.id] = sla.uptime_pct;
      });
    });
    return Object.values(byDevice);
  }, [accessibleCities, selectedDevices]);

  // =========================================================================
  // RENDER
  // =========================================================================
  return (
    <div>
      <PageHeader
        icon={BarChart3}
        title="Executive Overview"
        subtitle="Cross-city performance comparison across all problem statements"
      />

      {/* ================================================================= */}
      {/* Section 1: City Summary Cards */}
      {/* ================================================================= */}
      <div className="grid-4" style={{ marginBottom: 32 }}>
        {citySummaries.map((city) => (
          <div
            key={city.id}
            className="card"
            style={{
              borderLeft: `4px solid ${city.color}`,
              cursor: 'pointer',
              transition: 'transform 0.15s, box-shadow 0.15s',
            }}
            onClick={() => navigate(`/dashboard/city/${city.id}`)}
            onMouseEnter={(e) => {
              e.currentTarget.style.transform = 'translateY(-2px)';
              e.currentTarget.style.boxShadow = `0 4px 20px ${city.color}33`;
            }}
            onMouseLeave={(e) => {
              e.currentTarget.style.transform = 'translateY(0)';
              e.currentTarget.style.boxShadow = 'none';
            }}
          >
            <div className="card-header" style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
              <span>{city.name}</span>
              <span style={{
                fontSize: 10,
                padding: '2px 8px',
                borderRadius: 10,
                background: `${city.color}22`,
                color: city.color,
                fontWeight: 600,
              }}>
                {city.id}
              </span>
            </div>

            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12, marginTop: 8 }}>
              <div>
                <div style={{ fontSize: 11, color: 'var(--text-secondary)' }}>Model Accuracy</div>
                <div style={{ fontSize: 20, fontWeight: 700, color: city.avgAccuracy >= 93 ? '#22c55e' : '#f59e0b' }}>
                  {city.avgAccuracy}%
                </div>
              </div>
              <div>
                <div style={{ fontSize: 11, color: 'var(--text-secondary)' }}>High-Risk Devices</div>
                <div style={{ fontSize: 20, fontWeight: 700, color: city.highRiskCount > 200 ? '#ef4444' : '#f59e0b' }}>
                  {city.highRiskCount.toLocaleString()}
                </div>
              </div>
              <div>
                <div style={{ fontSize: 11, color: 'var(--text-secondary)' }}>Active Anomalies</div>
                <div style={{ fontSize: 20, fontWeight: 700, color: city.activeAnomalies > 10 ? '#ef4444' : '#f59e0b' }}>
                  {city.activeAnomalies}
                </div>
              </div>
              <div>
                <div style={{ fontSize: 11, color: 'var(--text-secondary)' }}>Fleet Uptime</div>
                <div style={{ fontSize: 20, fontWeight: 700, color: city.uptimePct >= 99.5 ? '#22c55e' : '#f59e0b' }}>
                  {city.uptimePct}%
                </div>
              </div>
            </div>

            <div style={{ marginTop: 12, fontSize: 11, color: city.color, textAlign: 'right', fontWeight: 500 }}>
              View City Dashboard &rarr;
            </div>
          </div>
        ))}
      </div>

      {/* ================================================================= */}
      {/* Section 2: PS-wise Cross-City Comparison Charts */}
      {/* ================================================================= */}

      {/* PS1 - Failure Prediction: Accuracy by Device Type */}
      <div className="card" style={{ marginBottom: 24 }}>
        <div className="card-header">PS1 - Failure Prediction: Model Accuracy by Device Type Across Cities</div>
        <ResponsiveContainer width="100%" height={320}>
          <BarChart data={ps1Data}>
            <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.1)" />
            <XAxis dataKey="device" tick={{ fontSize: 12 }} />
            <YAxis domain={[85, 100]} tick={{ fontSize: 11 }} label={{ value: 'Accuracy %', angle: -90, position: 'insideLeft' }} />
            <Tooltip formatter={(val) => [`${val}%`, 'Accuracy']} />
            <Legend />
            {accessibleCities.map((cityObj) => (
              <Bar
                key={cityObj.id}
                dataKey={cityObj.id}
                name={cityObj.name}
                fill={CITY_COLORS[cityObj.id]}
                radius={[4, 4, 0, 0]}
              />
            ))}
          </BarChart>
        </ResponsiveContainer>
      </div>

      {/* PS2 & PS3 side by side */}
      <div className="grid-2" style={{ marginBottom: 24 }}>
        {/* PS2 - Cascading Failure: Cascade Chain Counts */}
        <div className="card">
          <div className="card-header">PS2 - Cascading Failure: Cascade Chain Volume by City</div>
          <ResponsiveContainer width="100%" height={300}>
            <BarChart data={ps2Data}>
              <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.1)" />
              <XAxis dataKey="city" tick={{ fontSize: 12 }} />
              <YAxis tick={{ fontSize: 11 }} label={{ value: 'Total Chains', angle: -90, position: 'insideLeft' }} />
              <Tooltip formatter={(val) => [val.toLocaleString(), 'Cascade Chains']} />
              <Bar dataKey="chains" radius={[4, 4, 0, 0]}>
                {ps2Data.map((entry, i) => (
                  <Cell key={i} fill={entry.fill} />
                ))}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        </div>

        {/* PS3 - Root Cause: Top Hardware Failure Factors */}
        <div className="card">
          <div className="card-header">PS3 - Root Cause: Top Hardware Failure Components by City</div>
          <ResponsiveContainer width="100%" height={300}>
            <BarChart data={ps3Data} layout="vertical" margin={{ left: 20 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.1)" />
              <XAxis type="number" tick={{ fontSize: 11 }} />
              <YAxis dataKey="component" type="category" width={130} tick={{ fontSize: 10 }} />
              <Tooltip />
              <Legend />
              {accessibleCities.map((cityObj) => (
                <Bar
                  key={cityObj.id}
                  dataKey={cityObj.id}
                  name={cityObj.name}
                  fill={CITY_COLORS[cityObj.id]}
                  radius={[0, 4, 4, 0]}
                  stackId="a"
                />
              ))}
            </BarChart>
          </ResponsiveContainer>
        </div>
      </div>

      {/* PS4 & PS5 side by side */}
      <div className="grid-2" style={{ marginBottom: 24 }}>
        {/* PS4 - Anomaly Detection: Anomaly Counts by Severity per City */}
        <div className="card">
          <div className="card-header">PS4 - Anomaly Detection: Anomalies by Severity per City</div>
          <ResponsiveContainer width="100%" height={320}>
            <BarChart data={ps4Data}>
              <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.1)" />
              <XAxis dataKey="city" tick={{ fontSize: 12 }} />
              <YAxis tick={{ fontSize: 11 }} label={{ value: 'Count', angle: -90, position: 'insideLeft' }} />
              <Tooltip />
              <Legend />
              {['Critical', 'High', 'Medium', 'Low', 'Info'].map((sev) => (
                <Bar
                  key={sev}
                  dataKey={sev}
                  stackId="severity"
                  fill={SEVERITY_COLORS[sev]}
                  radius={sev === 'Info' ? [4, 4, 0, 0] : [0, 0, 0, 0]}
                />
              ))}
            </BarChart>
          </ResponsiveContainer>
        </div>

        {/* PS5 - SLA & Reliability: Uptime % per Device Type Across Cities */}
        <div className="card">
          <div className="card-header">PS5 - SLA & Reliability: Uptime % by Device Type Across Cities</div>
          <ResponsiveContainer width="100%" height={320}>
            <BarChart data={ps5Data}>
              <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.1)" />
              <XAxis dataKey="device" tick={{ fontSize: 12 }} />
              <YAxis domain={[98.5, 100]} tick={{ fontSize: 11 }} label={{ value: 'Uptime %', angle: -90, position: 'insideLeft' }} />
              <Tooltip formatter={(val) => [`${val}%`, 'Uptime']} />
              <Legend />
              {accessibleCities.map((cityObj) => (
                <Bar
                  key={cityObj.id}
                  dataKey={cityObj.id}
                  name={cityObj.name}
                  fill={CITY_COLORS[cityObj.id]}
                  radius={[4, 4, 0, 0]}
                />
              ))}
            </BarChart>
          </ResponsiveContainer>
        </div>
      </div>
    </div>
  );
}
