import React, { useState, useMemo } from 'react';
import {
  BarChart, PieChart, AreaChart, ComposedChart, LineChart,
  Bar, Pie, Cell, Area, Line,
  XAxis, YAxis, CartesianGrid, Tooltip, Legend,
  ResponsiveContainer, ReferenceLine,
} from 'recharts';
import {
  CITIES,
  getAnomalyAlerts,
  getDeviationScores,
  getAnomalyTrend,
} from '../../data/mockData';

const SEVERITY_COLORS = { Critical: '#ef4444', High: '#f97316', Medium: '#f59e0b', Low: '#3b82f6' };

const SUB_TABS = [
  { key: 'alerts', label: 'Real-Time Alerts' },
  { key: 'deviation', label: 'Deviation Scoring' },
  { key: 'outliers', label: 'Outlier Analysis' },
  { key: 'trends', label: 'Trend Monitoring' },
];

function timeAgo(timestamp) {
  const now = new Date();
  const then = new Date(timestamp);
  const diffMs = now - then;
  const diffMin = Math.floor(diffMs / 60000);
  if (diffMin < 1) return 'just now';
  if (diffMin < 60) return `${diffMin}m ago`;
  const diffHr = Math.floor(diffMin / 60);
  if (diffHr < 24) return `${diffHr}h ago`;
  return `${Math.floor(diffHr / 24)}d ago`;
}

function deviationCellColor(score) {
  if (score >= 80) return '#ef4444';
  if (score >= 60) return '#f97316';
  if (score >= 30) return '#f59e0b';
  return '#22c55e';
}

export default function PS4AnomalyDetectionTab({ city, selectedDevices }) {
  const selectedCities = useMemo(() => [city], [city]);
  const [activeTab, setActiveTab] = useState('alerts');

  const alerts = useMemo(() => getAnomalyAlerts(selectedCities, selectedDevices), [selectedCities, selectedDevices]);
  const deviationScores = useMemo(() => getDeviationScores(selectedCities, selectedDevices), [selectedCities, selectedDevices]);
  const anomalyTrend = useMemo(() => getAnomalyTrend(selectedCities, selectedDevices), [selectedCities, selectedDevices]);

  const alertKPIs = useMemo(() => {
    const total = alerts.length;
    const critical = alerts.filter((a) => a.severity === 'Critical').length;
    const high = alerts.filter((a) => a.severity === 'High').length;
    const responded = alerts.filter((a) => a.status === 'Acknowledged' || a.status === 'Resolved').length;
    const responseRate = total > 0 ? Math.round((responded / total) * 100) : 0;
    return { total, critical, high, responseRate };
  }, [alerts]);

  const alertSeverityPie = useMemo(() => {
    const counts = { Critical: 0, High: 0, Medium: 0, Low: 0 };
    alerts.forEach((a) => { if (counts[a.severity] !== undefined) counts[a.severity]++; });
    return Object.entries(counts).map(([name, value]) => ({ name, value }));
  }, [alerts]);

  const alertsByDevice = useMemo(() => {
    const counts = {};
    alerts.forEach((a) => { const type = a.device_type || 'Unknown'; counts[type] = (counts[type] || 0) + 1; });
    return Object.entries(counts).map(([device, count]) => ({ device, count }));
  }, [alerts]);

  const top20Deviations = useMemo(() => [...deviationScores].sort((a, b) => b.score - a.score).slice(0, 20), [deviationScores]);

  const scoreDistribution = useMemo(() => {
    const bins = [{ bin: '0-20', count: 0 }, { bin: '20-40', count: 0 }, { bin: '40-60', count: 0 }, { bin: '60-80', count: 0 }, { bin: '80-100', count: 0 }];
    deviationScores.forEach((d) => { const idx = Math.min(Math.floor(d.score / 20), 4); bins[idx].count++; });
    return bins;
  }, [deviationScores]);

  const outlierDevices = useMemo(() => deviationScores.filter((d) => d.score > 70).sort((a, b) => b.score - a.score), [deviationScores]);

  const alertStatusTracker = useMemo(() => {
    const statuses = { Active: 0, Investigating: 0, Acknowledged: 0, Resolved: 0 };
    alerts.forEach((a) => { if (statuses[a.status] !== undefined) statuses[a.status]++; });
    return Object.entries(statuses).map(([status, count]) => ({ status, count }));
  }, [alerts]);

  const outlierComparison = [
    { metric: 'Avg Error Rate (%)', outlier: 12.4, normal: 2.1 },
    { metric: 'Avg Transactions/Day', outlier: 145, normal: 312 },
    { metric: 'Avg Temperature (C)', outlier: 48.2, normal: 35.6 },
  ];

  const outlierPersistence = useMemo(() => {
    return outlierDevices.slice(0, 15).map((d, i) => ({ device_id: d.device_id, days: Math.max(3, Math.round((d.score / 100) * 30) + (i % 7)) }));
  }, [outlierDevices]);

  const trendKPIs = useMemo(() => {
    if (!anomalyTrend.length) return { avg7: 0, direction: 'stable', rate: 0 };
    const last7 = anomalyTrend.slice(-7);
    const prev7 = anomalyTrend.slice(-14, -7);
    const avg7 = Math.round(last7.reduce((s, d) => s + d.count, 0) / last7.length * 10) / 10;
    const prevAvg = prev7.length ? prev7.reduce((s, d) => s + d.count, 0) / prev7.length : avg7;
    const direction = avg7 > prevAvg * 1.05 ? 'increasing' : avg7 < prevAvg * 0.95 ? 'decreasing' : 'stable';
    const totalDevices = deviationScores.length || 1;
    const anomalous = deviationScores.filter((d) => d.score > 60).length;
    const rate = Math.round((anomalous / totalDevices) * 1000) / 10;
    return { avg7, direction, rate };
  }, [anomalyTrend, deviationScores]);

  const anomalyRateData = useMemo(() => {
    if (!anomalyTrend.length) return [];
    let runningAvg = anomalyTrend[0]?.count || 0;
    return anomalyTrend.map((d) => { runningAvg = runningAvg * 0.7 + d.count * 0.3; return { ...d, trend: Math.round(runningAvg * 10) / 10 }; });
  }, [anomalyTrend]);

  const deviationRollingTrend = useMemo(() => {
    const baseScore = deviationScores.length ? deviationScores.reduce((s, d) => s + d.score, 0) / deviationScores.length : 50;
    return Array.from({ length: 30 }, (_, i) => {
      const day = i + 1;
      const raw = baseScore / 100 * 0.6 + 0.25 * Math.sin(day * 0.5) + 0.1 * Math.sin(day * 1.3);
      return { day: `Day ${day}`, score: Math.round(Math.max(0, Math.min(1, raw)) * 100) / 100 };
    });
  }, [deviationScores]);

  const externalEvents = [
    { event: 'Firmware Update v3.2.1', date: '2026-04-05', spike: 34, devices: 18, correlation: 0.91 },
    { event: 'Heatwave (>38°C)', date: '2026-04-12', spike: 28, devices: 42, correlation: 0.87 },
    { event: 'Holiday Rush Period', date: '2026-04-18', spike: 22, devices: 35, correlation: 0.78 },
    { event: 'Power Grid Fluctuation', date: '2026-04-22', spike: 41, devices: 27, correlation: 0.94 },
    { event: 'Network Upgrade', date: '2026-04-26', spike: 19, devices: 15, correlation: 0.72 },
    { event: 'Shift Change', date: '2026-04-29', spike: 12, devices: 9, correlation: 0.65 },
  ];

  return (
    <div>
      <div className="tab-container">
        {SUB_TABS.map((t) => (
          <button key={t.key} className={`tab ${activeTab === t.key ? 'active' : ''}`} onClick={() => setActiveTab(t.key)}>{t.label}</button>
        ))}
      </div>

      {/* Real-Time Alerts */}
      {activeTab === 'alerts' && (
        <div>
          <div className="grid-4" style={{ marginBottom: 24 }}>
            <div className="card"><div className="card-header">Active Alerts</div><div className="kpi-value">{alertKPIs.total}</div><div className="kpi-label">Total alerts</div></div>
            <div className="card"><div className="card-header">Critical</div><div className="kpi-value" style={{ color: '#ef4444' }}>{alertKPIs.critical}</div><div className="kpi-label"><span className="badge-critical">Critical</span></div></div>
            <div className="card"><div className="card-header">High</div><div className="kpi-value" style={{ color: '#f97316' }}>{alertKPIs.high}</div><div className="kpi-label">Requires attention</div></div>
            <div className="card"><div className="card-header">Response Rate</div><div className="kpi-value" style={{ color: alertKPIs.responseRate >= 80 ? '#22c55e' : '#f59e0b' }}>{alertKPIs.responseRate}%</div><div className="kpi-label">Acknowledged/Resolved</div></div>
          </div>

          <div className="grid-2">
            <div className="card" style={{ maxHeight: 600, overflowY: 'auto' }}>
              <div className="card-header">Recent Alerts — {CITIES.find(c => c.id === city)?.name || city}</div>
              <div className="alert-feed">
                {alerts.slice(0, 20).map((alert, i) => (
                  <div key={i} style={{ display: 'flex', alignItems: 'flex-start', gap: 10, padding: '10px 0', borderBottom: '1px solid rgba(255,255,255,0.06)' }}>
                    <div style={{ width: 10, height: 10, borderRadius: '50%', marginTop: 5, flexShrink: 0, background: SEVERITY_COLORS[alert.severity] || '#6b7280' }} />
                    <div style={{ flex: 1, minWidth: 0 }}>
                      <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap', marginBottom: 4 }}>
                        <span style={{ fontSize: 11, opacity: 0.6 }}>{timeAgo(alert.timestamp)}</span>
                        <span className="badge-info" style={{ fontSize: 10 }}>{alert.city}</span>
                        <span style={{ fontSize: 11, fontFamily: 'monospace', opacity: 0.8 }}>{alert.device_id}</span>
                      </div>
                      <div style={{ fontSize: 13 }}>{alert.description}</div>
                      <span className={`badge-${alert.severity?.toLowerCase() || 'info'}`} style={{ fontSize: 10, marginTop: 4, display: 'inline-block' }}>{alert.severity}</span>
                    </div>
                  </div>
                ))}
              </div>
            </div>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
              <div className="card">
                <div className="card-header">Alert Distribution by Severity</div>
                <ResponsiveContainer width="100%" height={260}>
                  <PieChart><Pie data={alertSeverityPie} dataKey="value" nameKey="name" cx="50%" cy="50%" outerRadius={85} label>{alertSeverityPie.map((entry, i) => <Cell key={i} fill={SEVERITY_COLORS[entry.name] || '#6b7280'} />)}</Pie><Tooltip /><Legend /></PieChart>
                </ResponsiveContainer>
              </div>
              <div className="card">
                <div className="card-header">Alerts by Device Type</div>
                <ResponsiveContainer width="100%" height={260}>
                  <BarChart data={alertsByDevice}><CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.1)" /><XAxis dataKey="device" tick={{ fontSize: 11 }} /><YAxis tick={{ fontSize: 11 }} /><Tooltip /><Bar dataKey="count" fill="#6366f1" radius={[4, 4, 0, 0]} /></BarChart>
                </ResponsiveContainer>
              </div>
            </div>
          </div>

          <div className="grid-2" style={{ marginTop: 16 }}>
            <div className="card">
              <div className="card-header">Alert Response Status</div>
              <ResponsiveContainer width="100%" height={260}>
                <BarChart data={alertStatusTracker}><CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.1)" /><XAxis dataKey="status" tick={{ fontSize: 11 }} /><YAxis tick={{ fontSize: 11 }} /><Tooltip />
                  <Bar dataKey="count" radius={[4, 4, 0, 0]}>{alertStatusTracker.map((entry, i) => { const colors = { Active: '#ef4444', Investigating: '#f59e0b', Acknowledged: '#3b82f6', Resolved: '#22c55e' }; return <Cell key={i} fill={colors[entry.status] || '#6b7280'} />; })}</Bar>
                </BarChart>
              </ResponsiveContainer>
            </div>
            <div className="card">
              <div className="card-header">Anomaly Rate — {CITIES.find(c => c.id === city)?.name || city}</div>
              <ResponsiveContainer width="100%" height={260}>
                <BarChart data={[{ city: CITIES.find(c => c.id === city)?.name || city, total: alertKPIs.total, critical_high: alertKPIs.critical + alertKPIs.high }]}>
                  <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.1)" /><XAxis dataKey="city" tick={{ fontSize: 11 }} /><YAxis tick={{ fontSize: 11 }} /><Tooltip /><Legend />
                  <Bar dataKey="total" name="Total Anomalies" fill="#6366f1" radius={[4, 4, 0, 0]} /><Bar dataKey="critical_high" name="Critical + High" fill="#ef4444" radius={[4, 4, 0, 0]} />
                </BarChart>
              </ResponsiveContainer>
            </div>
          </div>
        </div>
      )}

      {/* Deviation Scoring */}
      {activeTab === 'deviation' && (
        <div>
          <div className="card" style={{ marginBottom: 24 }}>
            <div className="card-header">Fleet Deviation Heatmap</div>
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, padding: 16 }}>
              {deviationScores.map((d, i) => (
                <div key={i} style={{ width: 80, height: 56, background: deviationCellColor(d.score), borderRadius: 6, display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', opacity: 0.9 }}>
                  <span style={{ fontSize: 9, fontWeight: 600, color: '#fff', textShadow: '0 1px 2px rgba(0,0,0,0.5)' }}>{d.device_id}</span>
                  <span style={{ fontSize: 14, fontWeight: 700, color: '#fff', textShadow: '0 1px 2px rgba(0,0,0,0.5)' }}>{d.score}</span>
                </div>
              ))}
            </div>
          </div>

          <div className="card" style={{ marginBottom: 24 }}>
            <div className="card-header">Top 20 Highest Deviation Scores</div>
            <ResponsiveContainer width="100%" height={500}>
              <BarChart data={top20Deviations} layout="vertical" margin={{ left: 100 }}><CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.1)" /><XAxis type="number" domain={[0, 100]} tick={{ fontSize: 11 }} /><YAxis dataKey="device_id" type="category" width={90} tick={{ fontSize: 10 }} /><Tooltip />
                <Bar dataKey="score" radius={[0, 4, 4, 0]}>{top20Deviations.map((entry, i) => <Cell key={i} fill={deviationCellColor(entry.score)} />)}</Bar>
              </BarChart>
            </ResponsiveContainer>
          </div>

          <div className="grid-2">
            <div className="card">
              <div className="card-header">Score Distribution</div>
              <ResponsiveContainer width="100%" height={280}>
                <BarChart data={scoreDistribution}><CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.1)" /><XAxis dataKey="bin" tick={{ fontSize: 12 }} /><YAxis tick={{ fontSize: 11 }} /><Tooltip />
                  <Bar dataKey="count" radius={[4, 4, 0, 0]}>{scoreDistribution.map((_, i) => { const colors = ['#22c55e', '#84cc16', '#f59e0b', '#f97316', '#ef4444']; return <Cell key={i} fill={colors[i]} />; })}</Bar>
                </BarChart>
              </ResponsiveContainer>
            </div>
            <div className="card">
              <div className="card-header">Deviation Score 7-Day Rolling Trend</div>
              <ResponsiveContainer width="100%" height={280}>
                <LineChart data={deviationRollingTrend}><CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.1)" /><XAxis dataKey="day" tick={{ fontSize: 10 }} interval={4} /><YAxis domain={[0, 1]} tick={{ fontSize: 11 }} /><Tooltip />
                  <ReferenceLine y={0.7} stroke="#ef4444" strokeDasharray="6 3" label={{ value: 'Threshold', fill: '#ef4444', fontSize: 11, position: 'insideTopRight' }} />
                  <Line type="monotone" dataKey="score" stroke="#f59e0b" strokeWidth={2} dot={{ r: 3, fill: '#f59e0b' }} name="Rolling Avg" />
                </LineChart>
              </ResponsiveContainer>
            </div>
          </div>
        </div>
      )}

      {/* Outlier Analysis */}
      {activeTab === 'outliers' && (
        <div>
          <div className="card" style={{ marginBottom: 24, overflowX: 'auto' }}>
            <div className="card-header">Outlier Devices (Score &gt; 70)</div>
            <table className="data-table">
              <thead><tr><th>Device ID</th><th>City</th><th>Type</th><th>Score</th><th>Trend</th><th>Flagged Metrics</th><th>Days Anomalous</th></tr></thead>
              <tbody>
                {outlierDevices.map((d, i) => (
                  <tr key={i}>
                    <td style={{ fontFamily: 'monospace', fontWeight: 500 }}>{d.device_id}</td><td>{d.city}</td><td>{d.device_type}</td>
                    <td style={{ color: deviationCellColor(d.score), fontWeight: 700 }}>{d.score}</td>
                    <td style={{ fontSize: 16 }}>{d.score >= 85 ? <span style={{ color: '#ef4444' }}>&#9650;</span> : <span style={{ color: '#f59e0b' }}>&#9660;</span>}</td>
                    <td style={{ fontSize: 12 }}>{d.flagged_metrics || 'Error Rate, Latency'}</td>
                    <td>{d.days_anomalous || Math.round(d.score / 10 + 2)}</td>
                  </tr>
                ))}
                {outlierDevices.length === 0 && <tr><td colSpan={7} style={{ textAlign: 'center', opacity: 0.5, padding: 24 }}>No outlier devices detected</td></tr>}
              </tbody>
            </table>
          </div>

          <div className="grid-2" style={{ marginBottom: 24 }}>
            <div className="card">
              <div className="card-header">Outlier vs Normal Device Metrics</div>
              <ResponsiveContainer width="100%" height={280}>
                <BarChart data={outlierComparison}><CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.1)" /><XAxis dataKey="metric" tick={{ fontSize: 10 }} /><YAxis tick={{ fontSize: 11 }} /><Tooltip /><Legend />
                  <Bar dataKey="outlier" name="Outlier Devices" fill="#ef4444" radius={[4, 4, 0, 0]} /><Bar dataKey="normal" name="Normal Devices" fill="#22c55e" radius={[4, 4, 0, 0]} />
                </BarChart>
              </ResponsiveContainer>
            </div>
            <div className="card">
              <div className="card-header">Outlier Persistence (Days Anomalous)</div>
              <ResponsiveContainer width="100%" height={280}>
                <BarChart data={outlierPersistence} layout="vertical" margin={{ left: 90 }}><CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.1)" /><XAxis type="number" tick={{ fontSize: 11 }} /><YAxis dataKey="device_id" type="category" width={80} tick={{ fontSize: 9 }} /><Tooltip />
                  <Bar dataKey="days" fill="#f97316" radius={[0, 4, 4, 0]}>{outlierPersistence.map((entry, i) => <Cell key={i} fill={entry.days > 20 ? '#ef4444' : entry.days > 10 ? '#f97316' : '#f59e0b'} />)}</Bar>
                </BarChart>
              </ResponsiveContainer>
            </div>
          </div>
        </div>
      )}

      {/* Trend Monitoring */}
      {activeTab === 'trends' && (
        <div>
          <div className="grid-3" style={{ marginBottom: 24 }}>
            <div className="card"><div className="card-header">7-Day Avg Anomalies</div><div className="kpi-value">{trendKPIs.avg7}</div><div className="kpi-label">Per day</div></div>
            <div className="card"><div className="card-header">Trend Direction</div><div className="kpi-value" style={{ color: trendKPIs.direction === 'increasing' ? '#ef4444' : trendKPIs.direction === 'decreasing' ? '#22c55e' : '#f59e0b' }}>{trendKPIs.direction === 'increasing' ? '↑ Increasing' : trendKPIs.direction === 'decreasing' ? '↓ Decreasing' : '↔ Stable'}</div><div className="kpi-trend" style={{ color: trendKPIs.direction === 'increasing' ? '#ef4444' : '#22c55e' }}>vs. previous 7 days</div></div>
            <div className="card"><div className="card-header">Anomaly Rate</div><div className="kpi-value">{trendKPIs.rate}%</div><div className="kpi-label">% of fleet with score &gt; 60</div></div>
          </div>

          <div className="card" style={{ marginBottom: 24 }}>
            <div className="card-header">30-Day Anomaly Trend</div>
            <ResponsiveContainer width="100%" height={300}>
              <AreaChart data={anomalyTrend}>
                <defs><linearGradient id="anomalyGradient" x1="0" y1="0" x2="0" y2="1"><stop offset="5%" stopColor="#ef4444" stopOpacity={0.4} /><stop offset="95%" stopColor="#ef4444" stopOpacity={0.05} /></linearGradient></defs>
                <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.1)" /><XAxis dataKey="date" tick={{ fontSize: 10 }} interval={4} /><YAxis tick={{ fontSize: 11 }} /><Tooltip />
                <Area type="monotone" dataKey="count" stroke="#ef4444" strokeWidth={2} fill="url(#anomalyGradient)" />
              </AreaChart>
            </ResponsiveContainer>
          </div>

          <div className="card" style={{ marginBottom: 24 }}>
            <div className="card-header">Anomaly Count with Trend Line</div>
            <ResponsiveContainer width="100%" height={300}>
              <ComposedChart data={anomalyRateData}><CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.1)" /><XAxis dataKey="date" tick={{ fontSize: 10 }} interval={4} /><YAxis tick={{ fontSize: 11 }} /><Tooltip /><Legend />
                <Bar dataKey="count" name="Daily Anomalies" fill="#6366f1" radius={[4, 4, 0, 0]} opacity={0.7} />
                <Line type="monotone" dataKey="trend" name="Trend (EMA)" stroke="#f59e0b" strokeWidth={2} dot={false} />
              </ComposedChart>
            </ResponsiveContainer>
          </div>

          <div className="card">
            <div className="card-header">Correlation with External Events</div>
            <div style={{ overflowX: 'auto' }}>
              <table className="data-table">
                <thead><tr><th>Event</th><th>Date</th><th>Anomaly Spike %</th><th>Affected Devices</th><th>Correlation Score</th></tr></thead>
                <tbody>
                  {externalEvents.map((evt, i) => (
                    <tr key={i}>
                      <td style={{ fontWeight: 500 }}>{evt.event}</td><td>{evt.date}</td>
                      <td style={{ color: evt.spike > 30 ? '#ef4444' : evt.spike > 20 ? '#f59e0b' : '#3b82f6', fontWeight: 600 }}>+{evt.spike}%</td>
                      <td>{evt.devices}</td>
                      <td><span style={{ display: 'inline-block', padding: '2px 8px', borderRadius: 4, fontSize: 12, fontWeight: 600, background: evt.correlation >= 0.9 ? 'rgba(239,68,68,0.15)' : evt.correlation >= 0.75 ? 'rgba(245,158,11,0.15)' : 'rgba(59,130,246,0.15)', color: evt.correlation >= 0.9 ? '#ef4444' : evt.correlation >= 0.75 ? '#f59e0b' : '#3b82f6' }}>{evt.correlation.toFixed(2)}</span></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
