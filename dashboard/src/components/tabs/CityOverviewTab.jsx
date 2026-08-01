import React, { useMemo } from 'react';
import {
  BarChart, PieChart, LineChart, ComposedChart,
  Bar, Pie, Cell, Line,
  XAxis, YAxis, CartesianGrid, Tooltip, Legend,
  ResponsiveContainer, ReferenceLine, LabelList } from 'recharts';
import {
  CITIES, DEVICES, DEVICE_COLORS,
  getPredictionSummary,
  getModelPerformance,
  getAnomalyAlerts,
  getSLAMetrics,
  getReliabilityMetrics,
  getErrorCascades,
  getRootCauseFactors,
  getAccuracyTrend,
} from '../../data/mockData';
// 2026-07-26: direct value labels on every mark. Discrete marks (bars, pie
// slices) get one label each; continuous series (lines, areas) get an END
// label only -- a number on every point of a long series goes unread.
// Label text uses the muted text token, never the series colour.
import { VLAB, fmtV, endOnlyLabel } from '../shared/DashboardKit';

const SEVERITY_COLORS = { Critical: '#ef4444', High: '#f97316', Medium: '#f59e0b', Low: '#3b82f6', Info: '#6b7280' };

export default function CityOverviewTab({ city, selectedDevices }) {
  const selectedCities = useMemo(() => [city], [city]);
  const cityName = CITIES.find(c => c.id === city)?.name || city;

  // PS1 data
  const predSummary = useMemo(() => getPredictionSummary(selectedCities, selectedDevices), [selectedCities, selectedDevices]);
  const modelPerf = useMemo(() => getModelPerformance(selectedCities, selectedDevices), [selectedCities, selectedDevices]);
  const accuracyTrend = useMemo(() => getAccuracyTrend(selectedCities, selectedDevices), [selectedCities, selectedDevices]);

  // PS2/PS3 data
  const cascades = useMemo(() => getErrorCascades(selectedCities, selectedDevices), [selectedCities, selectedDevices]);
  const rootCause = useMemo(() => getRootCauseFactors(selectedCities, selectedDevices), [selectedCities, selectedDevices]);

  // PS4 data
  const alerts = useMemo(() => getAnomalyAlerts(selectedCities, selectedDevices), [selectedCities, selectedDevices]);

  // PS5 data
  const slaMetrics = useMemo(() => getSLAMetrics(selectedCities, selectedDevices), [selectedCities, selectedDevices]);
  const reliabilityData = useMemo(() => getReliabilityMetrics(selectedCities, selectedDevices), [selectedCities, selectedDevices]);

  // Derived KPIs
  const avgAccuracy = useMemo(() => {
    if (!modelPerf.length) return 0;
    return Math.round((modelPerf.reduce((s, m) => s + m.accuracy, 0) / modelPerf.length) * 100) / 100;
  }, [modelPerf]);

  const criticalAlerts = useMemo(() => alerts.filter(a => a.severity === 'Critical').length, [alerts]);
  const highAlerts = useMemo(() => alerts.filter(a => a.severity === 'High').length, [alerts]);
  const highRiskCount = predSummary.critical + predSummary.high;

  const avgRUL = useMemo(() => {
    if (!reliabilityData.length) return 0;
    return Math.round(reliabilityData.reduce((s, r) => s + r.rul_avg, 0) / reliabilityData.length);
  }, [reliabilityData]);

  // Accuracy trend (averaged across devices for this city)
  const cityAccuracyTrend = useMemo(() => {
    return accuracyTrend.map((row) => {
      const deviceKeys = selectedDevices.map((d) => `${city}_${d}`);
      const vals = deviceKeys.map((k) => row[k]).filter((v) => v != null);
      return {
        date: row.date,
        accuracy: vals.length ? Math.round((vals.reduce((a, b) => a + b, 0) / vals.length) * 100) / 100 : null,
      };
    });
  }, [accuracyTrend, city, selectedDevices]);

  // Alert severity distribution
  const alertSeverityData = useMemo(() => {
    const counts = { Critical: 0, High: 0, Medium: 0, Low: 0 };
    alerts.forEach(a => { if (counts[a.severity] !== undefined) counts[a.severity]++; });
    return Object.entries(counts).map(([name, value]) => ({ name, value }));
  }, [alerts]);

  // Top cascade flows
  const topCascades = useMemo(() => [...cascades].sort((a, b) => b.value - a.value).slice(0, 5), [cascades]);

  // Top failure modes
  const topFailureModes = useMemo(() => rootCause.failureModes.slice(0, 5), [rootCause]);

  // Error patterns by device type
  const errorsByDevice = useMemo(() => {
    const counts = {};
    alerts.forEach(a => { const type = a.device_type || 'Unknown'; counts[type] = (counts[type] || 0) + 1; });
    return Object.entries(counts).map(([device, count]) => ({ device, count, fill: DEVICE_COLORS[device] || '#8884d8' }));
  }, [alerts]);

  return (
    <div>
      {/* KPI Summary Row */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(6, 1fr)', gap: 16, marginBottom: 24 }}>
        <div className="card">
          <div className="card-header">Model Accuracy</div>
          <div className="kpi-value" style={{ color: avgAccuracy >= 93 ? '#22c55e' : '#f59e0b' }}>{avgAccuracy}%</div>
          <div className="kpi-label">PS1: Failure Prediction</div>
        </div>
        <div className="card">
          <div className="card-header">High-Risk Devices</div>
          <div className="kpi-value" style={{ color: '#ef4444' }}>{highRiskCount}</div>
          <div className="kpi-label">Critical + High severity</div>
        </div>
        <div className="card">
          <div className="card-header">Active Anomalies</div>
          <div className="kpi-value" style={{ color: '#f97316' }}>{alerts.length}</div>
          <div className="kpi-label">PS4: {criticalAlerts} critical, {highAlerts} high</div>
        </div>
        <div className="card">
          <div className="card-header">Fleet Uptime</div>
          <div className="kpi-value" style={{ color: slaMetrics.uptime_pct >= 99.5 ? '#22c55e' : '#f59e0b' }}>{slaMetrics.uptime_pct}%</div>
          <div className="kpi-label">PS5: Target 99.9%</div>
        </div>
        <div className="card">
          <div className="card-header">Avg RUL</div>
          <div className="kpi-value" style={{ color: avgRUL <= 30 ? '#ef4444' : avgRUL <= 60 ? '#f59e0b' : '#22c55e' }}>{avgRUL}d</div>
          <div className="kpi-label">Remaining useful life</div>
        </div>
        <div className="card">
          <div className="card-header">MTTR</div>
          <div className="kpi-value" style={{ color: slaMetrics.mttr_hours <= 4 ? '#22c55e' : '#ef4444' }}>{slaMetrics.mttr_hours}h</div>
          <div className="kpi-label">Target: &lt;4 hours</div>
        </div>
      </div>

      {/* Accuracy Trend + Alert Severity */}
      <div className="grid-2" style={{ marginBottom: 24 }}>
        <div className="card">
          <div className="card-header">30-Day Model Accuracy Trend — {cityName}</div>
          <ResponsiveContainer width="100%" height={280}>
            <LineChart data={cityAccuracyTrend}>
              <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.1)" />
              <XAxis dataKey="date" tick={{ fontSize: 10 }} interval={4} />
              <YAxis domain={[85, 100]} tick={{ fontSize: 11 }} />
              <Tooltip />
              <ReferenceLine y={90} stroke="#ef4444" strokeDasharray="5 5" label={{ value: '90% Target', position: 'right', fill: '#ef4444', fontSize: 10 }} />
              <Line type="monotone" dataKey="accuracy" stroke="#6366f1" strokeWidth={2} dot={false} name="Accuracy">
            <LabelList dataKey="accuracy" content={endOnlyLabel(cityAccuracyTrend)} />
          </Line>
            <Legend wrapperStyle={{ fontSize: 11, paddingTop: 4 }} />
            </LineChart>
          </ResponsiveContainer>
        </div>
        <div className="card">
          <div className="card-header">Anomaly Alert Distribution</div>
          <ResponsiveContainer width="100%" height={280}>
            <PieChart>
              <Pie data={alertSeverityData} dataKey="value" nameKey="name" cx="50%" cy="50%" outerRadius={90} label>
                {alertSeverityData.map((entry, i) => <Cell key={i} fill={SEVERITY_COLORS[entry.name] || '#6b7280'} />)}
              
            <LabelList dataKey="value" position="outside" formatter={fmtV} style={VLAB} />
          </Pie>
              <Tooltip />
              <Legend />
            </PieChart>
          </ResponsiveContainer>
        </div>
      </div>

      {/* Error Patterns Section */}
      <div className="grid-2" style={{ marginBottom: 24 }}>
        <div className="card">
          <div className="card-header">Error Patterns by Device Type</div>
          <ResponsiveContainer width="100%" height={280}>
            <BarChart data={errorsByDevice}>
              <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.1)" />
              <XAxis dataKey="device" tick={{ fontSize: 11 }} />
              <YAxis tick={{ fontSize: 11 }} />
              <Tooltip />
              <Bar dataKey="count" name="Anomalies" radius={[4, 4, 0, 0]}>
                {errorsByDevice.map((entry, i) => <Cell key={i} fill={entry.fill} />)}
              
            <LabelList dataKey="count" position="top" formatter={fmtV} style={VLAB} />
          </Bar>
            <Legend wrapperStyle={{ fontSize: 11, paddingTop: 4 }} />
            </BarChart>
          </ResponsiveContainer>
        </div>
        <div className="card">
          <div className="card-header">Top Failure Modes</div>
          <ResponsiveContainer width="100%" height={280}>
            <BarChart data={topFailureModes} layout="vertical" margin={{ left: 120 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.1)" />
              <XAxis type="number" tick={{ fontSize: 11 }} />
              <YAxis dataKey="mode" type="category" width={110} tick={{ fontSize: 10 }} />
              <Tooltip />
              <Bar dataKey="count" fill="#6366f1" radius={[0, 4, 4, 0]}>
            <LabelList dataKey="count" position="right" formatter={fmtV} style={VLAB} />
          </Bar>
            <Legend wrapperStyle={{ fontSize: 11, paddingTop: 4 }} />
            </BarChart>
          </ResponsiveContainer>
        </div>
      </div>

      {/* Cascade Failures Summary */}
      <div className="card" style={{ marginBottom: 24 }}>
        <div className="card-header">Top Cascade Failure Paths — {cityName}</div>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 8, padding: '8px 0' }}>
          {topCascades.map((c, i) => {
            const maxVal = topCascades[0]?.value || 1;
            const pct = Math.round((c.value / maxVal) * 100);
            return (
              <div key={i} style={{ display: 'flex', alignItems: 'center', gap: 12, padding: '8px 12px', background: 'rgba(255,255,255,0.03)', borderRadius: 8 }}>
                <div style={{ minWidth: 24, height: 24, borderRadius: '50%', background: 'rgba(99,102,241,0.15)', display: 'flex', alignItems: 'center', justifyContent: 'center', fontWeight: 700, fontSize: 12, color: '#6366f1' }}>{i + 1}</div>
                <div style={{ flex: 1 }}>
                  <div style={{ fontFamily: 'monospace', fontSize: 12 }}>{c.source} &rarr; {c.target}</div>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginTop: 4 }}>
                    <div style={{ flex: 1, height: 8, background: 'rgba(255,255,255,0.05)', borderRadius: 4, overflow: 'hidden' }}>
                      <div style={{ width: `${pct}%`, height: '100%', background: pct > 75 ? '#ef4444' : pct > 40 ? '#f59e0b' : '#3b82f6', borderRadius: 4 }} />
                    </div>
                    <span style={{ fontSize: 12, fontWeight: 600, minWidth: 36 }}>{c.value}</span>
                  </div>
                </div>
              </div>
            );
          })}
        </div>
      </div>

      {/* Reliability Overview */}
      <div className="card">
        <div className="card-header">Device Reliability Overview — {cityName}</div>
        <div style={{ overflowX: 'auto' }}>
          <table className="data-table">
            <thead>
              <tr><th>Device</th><th>MTTF (days)</th><th>RUL Avg (days)</th><th>Cox Hazard</th><th>Weibull Shape</th><th>Health Status</th></tr>
            </thead>
            <tbody>
              {reliabilityData.map((r, i) => {
                const health = r.rul_avg > 90 ? 'Healthy' : r.rul_avg > 60 ? 'Watch' : r.rul_avg > 30 ? 'Warning' : 'Critical';
                const healthColor = r.rul_avg > 90 ? '#22c55e' : r.rul_avg > 60 ? '#f59e0b' : r.rul_avg > 30 ? '#f97316' : '#ef4444';
                return (
                  <tr key={i}>
                    <td style={{ fontWeight: 500 }}>{r.device}</td>
                    <td>{r.mttf_days}</td>
                    <td style={{ color: healthColor, fontWeight: 600 }}>{r.rul_avg}</td>
                    <td>{r.cox_hazard}</td>
                    <td>{r.weibull_shape}</td>
                    <td><span style={{ color: healthColor, fontWeight: 600, padding: '2px 8px', borderRadius: 4, background: `${healthColor}18` }}>{health}</span></td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
