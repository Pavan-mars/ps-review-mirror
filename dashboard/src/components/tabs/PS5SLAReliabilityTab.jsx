import React, { useState, useMemo } from 'react';
import {
  LineChart, BarChart, PieChart, ComposedChart, RadialBarChart,
  Line, Bar, Pie, Cell, RadialBar,
  XAxis, YAxis, CartesianGrid, Tooltip, Legend,
  ResponsiveContainer, ReferenceLine,
} from 'recharts';
import {
  CITIES, DEVICES, DEVICE_COLORS,
  getSLAMetrics,
  getUptimeTrend,
  getDowntimeByDevice,
  getDowntimeByCause,
  getMTBFTrend,
  getSLABreaches,
  getComplianceScorecard,
  getPS5ReliabilityStatus,
} from '../../data/mockData';
import { apiPS5Status, useLiveData } from '../../data/api';

const CITY_COLOR_MAP = {};
CITIES.forEach((c) => { CITY_COLOR_MAP[c.id] = c.color; });

const SUB_TABS = [
  { key: 'overview', label: 'SLA Overview' },
  { key: 'downtime', label: 'Downtime Analysis' },
  { key: 'failure', label: 'Failure Metrics' },
  { key: 'breaches', label: 'Breach Tracking' },
  { key: 'compliance', label: 'Compliance Reporting' },
  { key: 'reliability', label: 'Reliability Metrics' },
];

const DOWNTIME_INCIDENTS = [
  { device_id: 'TVM-CHI-042', city: 'CHI', duration: '14h 23m', cause: 'Power supply failure', impact: '2,340 transactions lost' },
  { device_id: 'GTE-BOS-018', city: 'BOS', duration: '11h 45m', cause: 'Motor burnout', impact: '1,890 passengers delayed' },
  { device_id: 'RDR-LAX-031', city: 'LAX', duration: '9h 12m', cause: 'NFC module fault', impact: '1,456 tap failures' },
  { device_id: 'VLD-TOC-055', city: 'TOC', duration: '8h 38m', cause: 'Firmware crash', impact: '1,205 validation errors' },
  { device_id: 'TVM-BOS-009', city: 'BOS', duration: '7h 54m', cause: 'Display panel failure', impact: '980 transactions lost' },
];

const MTTR_DISTRIBUTION = [{ bin: '0-1hr', count: 42 }, { bin: '1-2hr', count: 28 }, { bin: '2-4hr', count: 18 }, { bin: '4-8hr', count: 9 }, { bin: '8+hr', count: 3 }];

const ERROR_RATE_TREND = Array.from({ length: 30 }, (_, i) => {
  const d = new Date(); d.setDate(d.getDate() - 29 + i);
  return { date: d.toISOString().split('T')[0].slice(5), errors: Math.round((3.2 + Math.sin(i / 5) * 1.5 + Math.cos(i / 3) * 0.4) * 10) / 10 };
});

const BREACH_TREND_30D = Array.from({ length: 30 }, (_, i) => {
  const d = new Date(); d.setDate(d.getDate() - 29 + i);
  return { date: d.toISOString().split('T')[0].slice(5), critical: Math.floor(Math.abs(Math.sin(i * 0.7)) * 3), high: Math.floor(Math.abs(Math.cos(i * 0.5)) * 4), medium: Math.floor(Math.abs(Math.sin(i * 0.3)) * 5 + 1), low: Math.floor(Math.abs(Math.cos(i * 0.2)) * 3 + 1) };
});

const BREACH_DURATION_DIST = [{ bin: '0-2hr', count: 34 }, { bin: '2-4hr', count: 22 }, { bin: '4-8hr', count: 12 }, { bin: '8+hr', count: 5 }];

const REMEDIATION_ACTIONS = [
  { action: 'Deploy firmware v3.4.2 hotfix for TVM fleet', owner: 'Engineering', due: '2026-05-01', status: 'In Progress', priority: 'critical' },
  { action: 'Replace aging gate motors in BOS stations', owner: 'Field Ops', due: '2026-05-15', status: 'Planned', priority: 'high' },
  { action: 'Upgrade NFC modules in LAX readers', owner: 'Hardware Team', due: '2026-05-10', status: 'In Progress', priority: 'high' },
  { action: 'Install UPS backup for TOC validators', owner: 'Infrastructure', due: '2026-05-20', status: 'Planned', priority: 'medium' },
  { action: 'Retrain ML models with updated thresholds', owner: 'Data Science', due: '2026-04-30', status: 'Complete', priority: 'low' },
];

function uptimeColor(val) { if (val >= 99.5) return '#22c55e'; if (val >= 99) return '#f59e0b'; return '#ef4444'; }
function formatTimestamp(iso) { try { const d = new Date(iso); return d.toLocaleDateString() + ' ' + d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }); } catch { return iso; } }

export default function PS5SLAReliabilityTab({ city, selectedDevices }) {
  const selectedCities = useMemo(() => [city], [city]);
  const [activeTab, setActiveTab] = useState('overview');

  const slaMetrics = useMemo(() => getSLAMetrics(selectedCities, selectedDevices), [selectedCities, selectedDevices]);
  const uptimeTrend = useMemo(() => getUptimeTrend(selectedCities, selectedDevices), [selectedCities, selectedDevices]);
  const downtimeByDevice = useMemo(() => getDowntimeByDevice(selectedCities, selectedDevices), [selectedCities, selectedDevices]);
  const downtimeByCause = useMemo(() => getDowntimeByCause(selectedCities, selectedDevices), [selectedCities, selectedDevices]);
  const mtbfTrend = useMemo(() => getMTBFTrend(selectedCities, selectedDevices), [selectedCities, selectedDevices]);
  const slaBreaches = useMemo(() => getSLABreaches(selectedCities, selectedDevices), [selectedCities, selectedDevices]);
  const complianceScorecard = useMemo(() => getComplianceScorecard(selectedCities, selectedDevices), [selectedCities, selectedDevices]);
  // Reliability Metrics sub-tab: live from cubic-mars-dashboard-api; mock fallback.
  const ps5Status = useLiveData(getPS5ReliabilityStatus(city), () => apiPS5Status(city), [city]);

  const cityName = CITIES.find(c => c.id === city)?.name || city;

  // SLA derived data
  const downtimeCausePie = useMemo(() => downtimeByCause.map((d) => ({ name: d.cause, value: d.hours })), [downtimeByCause]);
  const devicesExceedingThreshold = useMemo(() => {
    const devices = [];
    selectedDevices.forEach((device, i) => {
      const errorRate = 2.5 + Math.sin((city.length + i) * 1.3) * 3;
      if (errorRate > 4) devices.push({ city, device, error_rate: Math.round(errorRate * 100) / 100, threshold: 5.0, pct_of_threshold: Math.round((errorRate / 5) * 100) });
    });
    return devices.sort((a, b) => b.error_rate - a.error_rate);
  }, [city, selectedDevices]);

  const overallCompliance = useMemo(() => {
    if (!complianceScorecard.length) return 0;
    return Math.round((complianceScorecard.filter((c) => c.status === 'Pass').length / complianceScorecard.length) * 100);
  }, [complianceScorecard]);
  const gaugeData = useMemo(() => [{ name: 'Compliance', value: overallCompliance, fill: overallCompliance >= 80 ? '#22c55e' : overallCompliance >= 60 ? '#f59e0b' : '#ef4444' }], [overallCompliance]);

  const breachByCause = useMemo(() => {
    const counts = {};
    slaBreaches.forEach((b) => { const cause = b.cause || 'Unknown'; counts[cause] = (counts[cause] || 0) + 1; });
    return Object.entries(counts).map(([cause, count]) => ({ cause, count })).sort((a, b) => b.count - a.count);
  }, [slaBreaches]);

  const breachBySeverity = useMemo(() => {
    const SEVERITY_COLORS = { Critical: '#ef4444', High: '#f59e0b', Medium: '#3b82f6', Low: '#22c55e' };
    const counts = {};
    slaBreaches.forEach((b) => { const sev = b.severity || 'Medium'; counts[sev] = (counts[sev] || 0) + 1; });
    return ['Critical', 'High', 'Medium', 'Low'].filter((sev) => counts[sev]).map((sev) => ({ severity: sev, count: counts[sev], fill: SEVERITY_COLORS[sev] }));
  }, [slaBreaches]);

  const uptimeByDeviceType = useMemo(() => {
    const colors = { Readers: '#6366f1', TVMs: '#f59e0b', Gates: '#ef4444', Validators: '#10b981' };
    return selectedDevices.map((device) => {
      const seed = device.length * 7 + device.charCodeAt(0);
      return { device, uptime: Math.round(Math.min(99.0 + (seed % 100) / 100, 100) * 100) / 100, fill: colors[device] || '#8884d8' };
    });
  }, [selectedDevices]);

  // PS5 concordance-index color helper (0.5 = coin flip, everything here is currently weak)
  function ciColor(ci) { if (ci >= 0.7) return '#22c55e'; if (ci >= 0.6) return '#f59e0b'; return '#ef4444'; }

  return (
    <div>
      <div className="tab-container">
        {SUB_TABS.map((t) => <button key={t.key} className={`tab ${activeTab === t.key ? 'active' : ''}`} onClick={() => setActiveTab(t.key)}>{t.label}</button>)}
      </div>

      {/* SLA Overview */}
      {activeTab === 'overview' && (
        <div>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(5, 1fr)', gap: 16, marginBottom: 24 }}>
            <div className="card"><div className="card-header">Current Uptime</div><div className="kpi-value" style={{ color: uptimeColor(slaMetrics.uptime_pct) }}>{slaMetrics.uptime_pct}%</div><div className="kpi-label">Fleet-wide</div><div className="kpi-trend" style={{ color: slaMetrics.uptime_pct >= 99.5 ? '#22c55e' : '#f59e0b' }}>Target: 99.9%</div></div>
            <div className="card"><div className="card-header">Monthly Downtime</div><div className="kpi-value">{slaMetrics.downtime_hours}h</div><div className="kpi-label">Total hours</div></div>
            <div className="card"><div className="card-header">MTBF</div><div className="kpi-value">{slaMetrics.mtbf_days}d</div><div className="kpi-label">Mean time between failures</div></div>
            <div className="card"><div className="card-header">MTTR</div><div className="kpi-value">{slaMetrics.mttr_hours}h</div><div className="kpi-label">Mean time to recovery</div><div className="kpi-trend" style={{ color: slaMetrics.mttr_hours <= 4 ? '#22c55e' : '#ef4444' }}>Target: &lt;4h</div></div>
            <div className="card"><div className="card-header">Error Rate</div><div className="kpi-value">{slaMetrics.error_rate_per_device}</div><div className="kpi-label">Per device per day</div></div>
          </div>

          <div className="card" style={{ marginBottom: 24 }}>
            <div className="card-header">Uptime Trend — {cityName} (30 Days)</div>
            <ResponsiveContainer width="100%" height={320}>
              <LineChart data={uptimeTrend}><CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.1)" /><XAxis dataKey="date" tick={{ fontSize: 10 }} interval={4} /><YAxis domain={[98, 100]} tick={{ fontSize: 11 }} /><Tooltip /><Legend />
                <ReferenceLine y={99.9} stroke="#ef4444" strokeDasharray="5 5" label={{ value: '99.9% SLA', position: 'right', fontSize: 10, fill: '#ef4444' }} />
                <Line type="monotone" dataKey={city} stroke={CITY_COLOR_MAP[city] || '#8884d8'} strokeWidth={2} dot={false} />
              </LineChart>
            </ResponsiveContainer>
          </div>

          <div className="card" style={{ marginTop: 24 }}>
            <div className="card-header">Uptime by Device Type</div>
            <ResponsiveContainer width="100%" height={300}>
              <BarChart data={uptimeByDeviceType}><CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.1)" /><XAxis dataKey="device" tick={{ fontSize: 12 }} /><YAxis domain={[98, 100]} tick={{ fontSize: 11 }} /><Tooltip formatter={(value) => `${value}%`} />
                <ReferenceLine y={99.9} stroke="#ef4444" strokeDasharray="5 5" label={{ value: '99.9%', position: 'right', fontSize: 10, fill: '#ef4444' }} />
                <Bar dataKey="uptime" name="Uptime %" radius={[4, 4, 0, 0]}>{uptimeByDeviceType.map((entry, i) => <Cell key={i} fill={entry.fill} />)}</Bar>
              </BarChart>
            </ResponsiveContainer>
          </div>
        </div>
      )}

      {/* Downtime Analysis */}
      {activeTab === 'downtime' && (
        <div>
          <div className="card" style={{ marginBottom: 24 }}>
            <div className="card-header">Downtime by Device Type (Planned vs Unplanned)</div>
            <ResponsiveContainer width="100%" height={300}>
              <BarChart data={downtimeByDevice}><CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.1)" /><XAxis dataKey="device_type" tick={{ fontSize: 12 }} /><YAxis tick={{ fontSize: 11 }} /><Tooltip /><Legend />
                <Bar dataKey="planned_hours" name="Planned" stackId="a" fill="#3b82f6" /><Bar dataKey="unplanned_hours" name="Unplanned" stackId="a" fill="#ef4444" radius={[4, 4, 0, 0]} />
              </BarChart>
            </ResponsiveContainer>
          </div>
          <div className="grid-2">
            <div className="card">
              <div className="card-header">Downtime by Cause</div>
              <ResponsiveContainer width="100%" height={300}>
                <PieChart><Pie data={downtimeCausePie} dataKey="value" nameKey="name" cx="50%" cy="50%" innerRadius={55} outerRadius={95} label={({ name, percent }) => `${name} (${(percent * 100).toFixed(0)}%)`}>
                  {downtimeCausePie.map((_, i) => { const colors = ['#ef4444', '#f97316', '#f59e0b', '#3b82f6', '#6366f1', '#10b981', '#ec4899']; return <Cell key={i} fill={colors[i % colors.length]} />; })}
                </Pie><Tooltip /></PieChart>
              </ResponsiveContainer>
            </div>
            <div className="card" style={{ overflowX: 'auto' }}>
              <div className="card-header">Longest Downtime Incidents</div>
              <table className="data-table">
                <thead><tr><th>Device</th><th>City</th><th>Duration</th><th>Cause</th><th>Impact</th></tr></thead>
                <tbody>{DOWNTIME_INCIDENTS.filter(inc => inc.city === city || city === 'TOC').slice(0, 5).map((inc, i) => (
                  <tr key={i}><td style={{ fontFamily: 'monospace', fontSize: 11 }}>{inc.device_id}</td><td>{inc.city}</td><td style={{ fontWeight: 600, color: '#ef4444' }}>{inc.duration}</td><td style={{ fontSize: 12 }}>{inc.cause}</td><td style={{ fontSize: 11, opacity: 0.8 }}>{inc.impact}</td></tr>
                ))}</tbody>
              </table>
            </div>
          </div>
        </div>
      )}

      {/* Failure Metrics */}
      {activeTab === 'failure' && (
        <div>
          <div className="card" style={{ marginBottom: 24 }}>
            <div className="card-header">MTBF Trend by Device Type (12 Months)</div>
            <ResponsiveContainer width="100%" height={320}>
              <LineChart data={mtbfTrend}><CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.1)" /><XAxis dataKey="month" tick={{ fontSize: 10 }} /><YAxis tick={{ fontSize: 11 }} /><Tooltip /><Legend />
                <Line type="monotone" dataKey="tvms" name="TVMs" stroke={DEVICE_COLORS.TVMs} strokeWidth={2} dot={{ r: 3 }} />
                <Line type="monotone" dataKey="gates" name="Gates" stroke={DEVICE_COLORS.Gates} strokeWidth={2} dot={{ r: 3 }} />
                <Line type="monotone" dataKey="validators" name="Validators" stroke={DEVICE_COLORS.Validators} strokeWidth={2} dot={{ r: 3 }} />
              </LineChart>
            </ResponsiveContainer>
          </div>
          <div className="grid-2">
            <div className="card">
              <div className="card-header">MTTR Distribution</div>
              <ResponsiveContainer width="100%" height={280}>
                <BarChart data={MTTR_DISTRIBUTION}><CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.1)" /><XAxis dataKey="bin" tick={{ fontSize: 12 }} /><YAxis tick={{ fontSize: 11 }} /><Tooltip />
                  <Bar dataKey="count" radius={[4, 4, 0, 0]}>{MTTR_DISTRIBUTION.map((_, i) => { const colors = ['#22c55e', '#84cc16', '#f59e0b', '#f97316', '#ef4444']; return <Cell key={i} fill={colors[i]} />; })}</Bar>
                </BarChart>
              </ResponsiveContainer>
            </div>
            <div className="card">
              <div className="card-header">Error Rate Trend (30 Days)</div>
              <ResponsiveContainer width="100%" height={280}>
                <LineChart data={ERROR_RATE_TREND}><CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.1)" /><XAxis dataKey="date" tick={{ fontSize: 10 }} interval={4} /><YAxis tick={{ fontSize: 11 }} /><Tooltip />
                  <ReferenceLine y={5} stroke="#ef4444" strokeDasharray="5 5" label={{ value: 'Threshold', position: 'right', fontSize: 10, fill: '#ef4444' }} />
                  <Line type="monotone" dataKey="errors" stroke="#6366f1" strokeWidth={2} dot={false} />
                </LineChart>
              </ResponsiveContainer>
            </div>
          </div>
        </div>
      )}

      {/* Breach Tracking */}
      {activeTab === 'breaches' && (
        <div>
          <div className="card" style={{ marginBottom: 24, overflowX: 'auto' }}>
            <div className="card-header">SLA Breaches — {cityName}</div>
            <table className="data-table">
              <thead><tr><th>ID</th><th>City</th><th>Device</th><th>Severity</th><th>Duration (hrs)</th><th>Start Time</th><th>Status</th><th>Cause</th></tr></thead>
              <tbody>
                {slaBreaches.map((b, i) => (
                  <tr key={i}><td style={{ fontFamily: 'monospace', fontSize: 11 }}>{b.id}</td><td>{b.city}</td><td style={{ fontFamily: 'monospace', fontSize: 11 }}>{b.device_id}</td>
                    <td><span className={`badge badge-${b.severity === 'Critical' ? 'critical' : b.severity === 'Major' ? 'high' : 'medium'}`}>{b.severity}</span></td>
                    <td style={{ fontWeight: 600 }}>{b.duration_hours}h</td><td style={{ fontSize: 11, opacity: 0.8 }}>{formatTimestamp(b.start_time)}</td>
                    <td><span className={b.status === 'Resolved' ? 'badge badge-success' : 'badge badge-critical'}>{b.status}</span></td><td style={{ fontSize: 12 }}>{b.cause}</td>
                  </tr>
                ))}
                {slaBreaches.length === 0 && <tr><td colSpan={8} style={{ textAlign: 'center', opacity: 0.5, padding: 24 }}>No breaches</td></tr>}
              </tbody>
            </table>
          </div>
          <div className="grid-2">
            <div className="card">
              <div className="card-header">Breach Count Trend (30 Days)</div>
              <ResponsiveContainer width="100%" height={280}>
                <BarChart data={BREACH_TREND_30D}><CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.1)" /><XAxis dataKey="date" tick={{ fontSize: 9 }} interval={4} /><YAxis tick={{ fontSize: 11 }} /><Tooltip /><Legend />
                  <Bar dataKey="critical" name="Critical" stackId="a" fill="#ef4444" /><Bar dataKey="high" name="High" stackId="a" fill="#f97316" /><Bar dataKey="medium" name="Medium" stackId="a" fill="#f59e0b" /><Bar dataKey="low" name="Low" stackId="a" fill="#3b82f6" radius={[4, 4, 0, 0]} />
                </BarChart>
              </ResponsiveContainer>
            </div>
            <div className="card">
              <div className="card-header">Root Cause of Breaches</div>
              <ResponsiveContainer width="100%" height={280}>
                <BarChart data={breachByCause} layout="vertical" margin={{ left: 160 }}><CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.1)" /><XAxis type="number" tick={{ fontSize: 11 }} /><YAxis dataKey="cause" type="category" width={150} tick={{ fontSize: 10 }} /><Tooltip />
                  <Bar dataKey="count" fill="#ef4444" radius={[0, 4, 4, 0]} />
                </BarChart>
              </ResponsiveContainer>
            </div>
          </div>
        </div>
      )}

      {/* Compliance Reporting */}
      {activeTab === 'compliance' && (
        <div>
          <div className="grid-2" style={{ marginBottom: 24 }}>
            <div className="card" style={{ overflowX: 'auto' }}>
              <div className="card-header">SLA Compliance Scorecard</div>
              <table className="data-table">
                <thead><tr><th>Metric</th><th>Target</th><th>Actual</th><th>Status</th><th>Trend</th></tr></thead>
                <tbody>
                  {complianceScorecard.map((c, i) => (
                    <tr key={i}><td style={{ fontWeight: 500 }}>{c.metric}</td><td style={{ opacity: 0.8 }}>{c.target}</td><td style={{ fontWeight: 600 }}>{c.actual}</td>
                      <td><span className={`badge ${c.status === 'Pass' ? 'badge-success' : c.status === 'Fail' ? 'badge-critical' : 'badge-medium'}`}>{c.status}</span></td>
                      <td style={{ fontSize: 14 }}>{c.trend === 'improving' ? <span style={{ color: '#22c55e' }}>&#9650; Improving</span> : c.trend === 'declining' ? <span style={{ color: '#ef4444' }}>&#9660; Declining</span> : <span style={{ color: '#f59e0b' }}>&#9644; Stable</span>}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <div className="card" style={{ display: 'flex', flexDirection: 'column', alignItems: 'center' }}>
              <div className="card-header" style={{ width: '100%' }}>Monthly SLA Compliance</div>
              <div style={{ position: 'relative', width: 250, height: 250 }}>
                <ResponsiveContainer width="100%" height="100%">
                  <RadialBarChart cx="50%" cy="50%" innerRadius="70%" outerRadius="90%" startAngle={180} endAngle={0} data={gaugeData}>
                    <RadialBar dataKey="value" cornerRadius={10} background={{ fill: 'rgba(255,255,255,0.05)' }} />
                  </RadialBarChart>
                </ResponsiveContainer>
                <div style={{ position: 'absolute', top: '40%', left: '50%', transform: 'translate(-50%, -50%)', textAlign: 'center' }}>
                  <div style={{ fontSize: 36, fontWeight: 700, color: overallCompliance >= 80 ? '#22c55e' : '#f59e0b' }}>{overallCompliance}%</div>
                  <div style={{ fontSize: 12, opacity: 0.6 }}>Overall Compliance</div>
                </div>
              </div>
            </div>
          </div>

          <div className="card" style={{ marginBottom: 24 }}>
            <div className="card-header">Breach Incidents by Severity</div>
            <ResponsiveContainer width="100%" height={300}>
              <BarChart data={breachBySeverity}><CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.1)" /><XAxis dataKey="severity" tick={{ fontSize: 12 }} /><YAxis tick={{ fontSize: 11 }} allowDecimals={false} /><Tooltip />
                <Bar dataKey="count" name="Breaches" radius={[4, 4, 0, 0]}>{breachBySeverity.map((entry, i) => <Cell key={i} fill={entry.fill} />)}</Bar>
              </BarChart>
            </ResponsiveContainer>
          </div>

          <div className="card" style={{ overflowX: 'auto' }}>
            <div className="card-header">Remediation Actions</div>
            <table className="data-table">
              <thead><tr><th>Action</th><th>Owner</th><th>Due Date</th><th>Status</th><th>Priority</th></tr></thead>
              <tbody>
                {REMEDIATION_ACTIONS.map((r, i) => (
                  <tr key={i}><td style={{ fontSize: 12, maxWidth: 300 }}>{r.action}</td><td>{r.owner}</td><td style={{ fontFamily: 'monospace', fontSize: 12 }}>{r.due}</td>
                    <td><span className={`badge ${r.status === 'Complete' ? 'badge-success' : r.status === 'In Progress' ? 'badge-info' : 'badge-medium'}`}>{r.status}</span></td>
                    <td><span className={`badge badge-${r.priority}`}>{r.priority.charAt(0).toUpperCase() + r.priority.slice(1)}</span></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* Reliability Metrics — real PS5 model outputs (RUL / Weibull / Cox), gated */}
      {activeTab === 'reliability' && (
        <div>
          {!ps5Status ? (
            <div className="card" style={{ padding: 32, textAlign: 'center' }}>
              <div className="card-header">PS5 — Reliability / RUL</div>
              <p style={{ opacity: 0.7, marginTop: 12 }}>
                No PS5 reliability run exists for {cityName} yet. Chicago is currently the only
                city with a completed PS5 run.
              </p>
            </div>
          ) : (
            <div>
              <div className="card" style={{ marginBottom: 24, padding: 16, borderLeft: '4px solid #ef4444', background: 'rgba(239,68,68,0.06)' }}>
                <div style={{ fontWeight: 700, color: '#ef4444', marginBottom: 8 }}>⚠ PS5 is not dashboard-ready — data quality gate active</div>
                <p style={{ fontSize: 13, opacity: 0.85, margin: 0 }}>{ps5Status.interpretation}</p>
                <ul style={{ fontSize: 12, opacity: 0.8, marginTop: 8, marginBottom: 0, paddingLeft: 18 }}>
                  {ps5Status.shared_blockers.map((b) => <li key={b.id}><strong>{b.id}</strong> — {b.issue}</li>)}
                </ul>
              </div>

              <div style={{ display: 'grid', gridTemplateColumns: `repeat(${ps5Status.devices.length}, 1fr)`, gap: 16, marginBottom: 24 }}>
                {ps5Status.devices.map((d) => (
                  <div className="card" key={d.device}>
                    <div className="card-header">{d.device} — Concordance Index</div>
                    <div className="kpi-value" style={{ color: ciColor(d.concordance_index) }}>{d.concordance_index.toFixed(4)}</div>
                    <div className="kpi-label">0.50 = random · 1.00 = perfect ranking</div>
                    <div style={{ marginTop: 8, fontSize: 11 }}>
                      <span className={`badge ${d.registry_status === 'clean_v1' ? 'badge-success' : 'badge-critical'}`}>
                        {d.registry_status === 'clean_v1' ? 'Registry clean' : 'Registry broken'}
                      </span>
                    </div>
                    {d.blockers.length > 0 && (
                      <div style={{ fontSize: 11, opacity: 0.75, marginTop: 8 }}>{d.blockers.join('; ')}</div>
                    )}
                  </div>
                ))}
              </div>

              <div className="card" style={{ padding: 20 }}>
                <div className="card-header">What will render here once PS5 clears the gate</div>
                <p style={{ fontSize: 13, opacity: 0.8 }}>
                  RUL heatmap (remaining useful life, days), Weibull survival probability curves per
                  device category, and Cox proportional-hazards ratios per fault code — the same
                  layout previously scaffolded here — will be restored once tasks #66 (code review),
                  #88 (implausible RUL fix), #89 (TVM MLflow champion-selection fix), and #91
                  (oversized CoxPH artifact fix) are resolved. Showing placeholder numbers for these
                  now would misrepresent known-broken model output to Cubic, so they are intentionally
                  withheld rather than faked.
                </p>
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
