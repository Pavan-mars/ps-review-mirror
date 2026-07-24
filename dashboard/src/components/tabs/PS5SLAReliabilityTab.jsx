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
import { apiPS5Status, apiPS5Reliability, apiPS5DeviceRUL, apiPS5SerialHealth, useLiveData } from '../../data/api';
import { getPS5ReliabilityDetail, getPS5DeviceRUL, getPS5SerialHealth } from '../../data/ps5ReliabilityMock';

const CITY_COLOR_MAP = {};
CITIES.forEach((c) => { CITY_COLOR_MAP[c.id] = c.color; });

const SUB_TABS = [
  { key: 'overview', label: 'SLA Overview' },
  { key: 'downtime', label: 'Downtime Analysis' },
  { key: 'failure', label: 'Failure Metrics' },
  { key: 'breaches', label: 'Breach Tracking' },
  { key: 'compliance', label: 'Compliance Reporting' },
  { key: 'reliability', label: 'Reliability Metrics' },
  { key: 'devicerul', label: 'Device RUL' },
  { key: 'serial', label: 'Serial Health' },
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
  const ps5Detail = useLiveData(getPS5ReliabilityDetail(city), () => apiPS5Reliability(city), [city]);
  // v5.1 device-grain RUL + serial-grain health (hardware-OOS-Set); live from /ps5/devices , /ps5/serials.
  const ps5Devices = useLiveData(getPS5DeviceRUL(city), () => apiPS5DeviceRUL(city), [city]);
  const ps5Serials = useLiveData(getPS5SerialHealth(city), () => apiPS5SerialHealth(city), [city]);
  const [devSort, setDevSort] = useState({ key: 'rul_days', dir: 'asc' });
  const [serSort, setSerSort] = useState({ key: 'risk_score', dir: 'desc' });

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
  function sortRows(rows, s) {
    const out = [...(rows || [])];
    out.sort((a, b) => {
      const av = a[s.key], bv = b[s.key];
      const c = (typeof av === 'number' && typeof bv === 'number') ? av - bv : String(av).localeCompare(String(bv));
      return s.dir === 'asc' ? c : -c;
    });
    return out;
  }
  const toggleSort = (setter, cur, key) => setter(cur.key === key ? { key, dir: cur.dir === 'asc' ? 'desc' : 'asc' } : { key, dir: 'asc' });
  const bandBadge = (b) => `badge badge-${b === 'CRITICAL' ? 'critical' : b === 'HIGH' ? 'high' : b === 'MEDIUM' ? 'medium' : 'success'}`;

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
          {!ps5Detail ? (
            <div className="card" style={{ padding: 32, textAlign: 'center' }}>
              <div className="card-header">PS5 — Reliability / RUL</div>
              <p style={{ opacity: 0.7, marginTop: 12 }}>
                No PS5 reliability run exists for {cityName} yet. Chicago is currently the only
                city with a completed PS5 run.
              </p>
            </div>
          ) : (
            <div>
              {/* v5 status — models rebuilt on the redefined hardware-OOS-Set event; still below the promotion floor */}
              <div className="card" style={{ marginBottom: 24, padding: 16, borderLeft: '4px solid #f59e0b', background: 'rgba(245,158,11,0.06)' }}>
                <div style={{ fontWeight: 700, color: '#f59e0b', marginBottom: 8 }}>PS5 v5 — rebuilt on redefined failure event, not yet promoted</div>
                <p style={{ fontSize: 13, opacity: 0.85, margin: 0 }}>
                  The survival event was redefined to <strong>any hardware OOS "Set"</strong>, and the notebook rebuilt on the
                  telemetry-era window (2024-01-01+) with verified feature–label alignment. RUL estimates are now plausible
                  (tens of days, not decades), superseding the earlier implausible-RUL, oversized-artifact and missing-notebook
                  blockers. All three device types still sit below the C-index ≥ {ps5Detail.floor} promotion floor on the latest
                  run, so RUL and survival below are a labelled <strong>SAMPLE</strong> pending the live v5 scoring run into RDS —
                  nothing here is shown as promoted output.
                </p>
              </div>

              {ps5Detail && (
                <div>
                  {/* Event-definition chip + provenance (reads ps5_event_definition once RDS is live) */}
                  <div className="card" style={{ marginBottom: 24, padding: 16 }}>
                    <div style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: 10 }}>
                      <span style={{ padding: '4px 10px', borderRadius: 6, background: 'rgba(99,102,241,0.15)', color: '#a5b4fc', fontSize: 12, fontWeight: 600 }}>
                        Failure = {ps5Detail.event_definition} · v{ps5Detail.event_def_version}
                      </span>
                      <span style={{ fontSize: 12, opacity: 0.7 }}>Window: {ps5Detail.window}</span>
                      <span style={{ fontSize: 12, opacity: 0.7 }}>Promotion floor: C-index ≥ {ps5Detail.floor}</span>
                      {ps5Detail.is_sample && (
                        <span style={{ marginLeft: 'auto', padding: '4px 10px', borderRadius: 6, background: 'rgba(245,158,11,0.15)', color: '#fbbf24', fontSize: 11, fontWeight: 700, letterSpacing: 0.4 }}>
                          SAMPLE — RUL / survival illustrative pending live v5 run
                        </span>
                      )}
                    </div>
                  </div>

                  {/* Per-device RUL + as-of recency (days_since_fail, roll_fail_30d) on the hardware-OOS-Set event */}
                  <div style={{ display: 'grid', gridTemplateColumns: `repeat(${ps5Detail.devices.length}, 1fr)`, gap: 16, marginBottom: 24 }}>
                    {ps5Detail.devices.map((d) => (
                      <div className="card" key={d.device}>
                        <div className="card-header" style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                          <span>{d.device} — Remaining Useful Life</span>
                          <span className={`badge ${d.gate_pass ? 'badge-success' : 'badge-critical'}`}>{d.gate_pass ? 'Gate pass' : 'Below floor'}</span>
                        </div>
                        <div className="kpi-value" style={{ color: ciColor(d.cv_cindex) }}>{d.rul_median_days}<span style={{ fontSize: 14, opacity: 0.6 }}> days</span></div>
                        <div className="kpi-label">Median RUL · P10–P90 {d.rul_p10_days}–{d.rul_p90_days}d</div>
                        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8, marginTop: 12, fontSize: 12 }}>
                          <div><div style={{ opacity: 0.6 }}>CV C-index</div><div style={{ fontWeight: 600, color: ciColor(d.cv_cindex) }}>{d.cv_cindex.toFixed(4)}</div></div>
                          <div><div style={{ opacity: 0.6 }}>Days since HW-OOS</div><div style={{ fontWeight: 600 }}>{d.days_since_fail}</div></div>
                          <div><div style={{ opacity: 0.6 }}>Failures / 30d</div><div style={{ fontWeight: 600 }}>{d.roll_fail_30d}</div></div>
                          <div><div style={{ opacity: 0.6 }}>Events (n)</div><div style={{ fontWeight: 600 }}>{d.n_events.toLocaleString()}</div></div>
                        </div>
                        <div style={{ marginTop: 10, fontSize: 11, opacity: 0.7 }}>{d.champion} · IBS {d.ibs.toFixed(3)}</div>
                      </div>
                    ))}
                  </div>

                  {/* Survival curves S(t) per device type — event = hardware OOS (Set) */}
                  <div className="card" style={{ marginBottom: 24 }}>
                    <div className="card-header">Survival Probability S(t) by Device Type — event = {ps5Detail.event_definition}</div>
                    <ResponsiveContainer width="100%" height={320}>
                      <LineChart margin={{ top: 8, right: 24, bottom: 16, left: 8 }}>
                        <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.1)" />
                        <XAxis type="number" dataKey="day" allowDuplicatedCategory={false} tick={{ fontSize: 11 }} label={{ value: 'Days in service', position: 'insideBottom', offset: -6, fontSize: 11 }} />
                        <YAxis domain={[0, 1]} tick={{ fontSize: 11 }} label={{ value: 'S(t)', angle: -90, position: 'insideLeft', fontSize: 11 }} />
                        <Tooltip formatter={(v) => (typeof v === 'number' ? v.toFixed(3) : v)} labelFormatter={(l) => `Day ${l}`} />
                        <Legend />
                        {ps5Detail.devices.map((d) => (
                          <Line key={d.device} type="monotone" dataKey="surv" data={d.survival} name={d.device} stroke={DEVICE_COLORS[d.device] || '#8884d8'} strokeWidth={2} dot={false} />
                        ))}
                      </LineChart>
                    </ResponsiveContainer>
                  </div>

                  <div className="card" style={{ padding: 16 }}>
                    <div className="card-header">How to read this</div>
                    <p style={{ fontSize: 12, opacity: 0.8, margin: 0 }}>{ps5Detail.note}</p>
                  </div>
                </div>
              )}
            </div>
          )}
        </div>
      )}

      {/* Device RUL — per-device remaining useful life on the hardware-OOS-Set event */}
      {activeTab === 'devicerul' && (
        <div>
          {!ps5Devices ? (
            <div className="card" style={{ padding: 32, textAlign: 'center' }}>
              <div className="card-header">PS5 — Device RUL</div>
              <p style={{ opacity: 0.7, marginTop: 12 }}>No PS5 device-level reliability run for {cityName} yet.</p>
            </div>
          ) : (
            <div>
              <div className="card" style={{ marginBottom: 16, padding: 14 }}>
                <div style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: 10 }}>
                  <span style={{ padding: '4px 10px', borderRadius: 6, background: 'rgba(99,102,241,0.15)', color: '#a5b4fc', fontSize: 12, fontWeight: 600 }}>Failure = {ps5Devices.event_definition} · v{ps5Devices.event_def_version}</span>
                  <span style={{ fontSize: 12, opacity: 0.7 }}>{ps5Devices.devices.length} devices · {ps5Devices.window}</span>
                  {ps5Devices.is_sample && <span style={{ marginLeft: 'auto', padding: '4px 10px', borderRadius: 6, background: 'rgba(245,158,11,0.15)', color: '#fbbf24', fontSize: 11, fontWeight: 700 }}>SAMPLE — pending live run</span>}
                </div>
              </div>
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 12, marginBottom: 16 }}>
                {['CRITICAL', 'HIGH', 'MEDIUM', 'LOW'].map((b) => {
                  const n = ps5Devices.devices.filter((d) => d.risk_band === b).length;
                  const col = { CRITICAL: '#ef4444', HIGH: '#f59e0b', MEDIUM: '#3b82f6', LOW: '#22c55e' }[b];
                  return (<div className="card" key={b}><div className="card-header">{b}</div><div className="kpi-value" style={{ color: col }}>{n}</div><div className="kpi-label">devices</div></div>);
                })}
              </div>
              <div className="card" style={{ overflowX: 'auto' }}>
                <div className="card-header">Device Remaining Useful Life — click a column to sort</div>
                <table className="data-table">
                  <thead><tr>
                    {[['device_id', 'Device'], ['device_type', 'Type'], ['facility_id', 'Facility'], ['current_age_days', 'Age (d)'], ['rul_days', 'RUL (d)'], ['risk_band', 'Risk'], ['days_since_hw_oos', 'Days since HW-OOS'], ['roll_fail_30d', 'Fails/30d'], ['is_overdue', 'Overdue']].map(([k, l]) => (
                      <th key={k} style={{ cursor: 'pointer', whiteSpace: 'nowrap' }} onClick={() => toggleSort(setDevSort, devSort, k)}>{l}{devSort.key === k ? (devSort.dir === 'asc' ? ' ▲' : ' ▼') : ''}</th>
                    ))}
                  </tr></thead>
                  <tbody>
                    {sortRows(ps5Devices.devices, devSort).map((d, i) => (
                      <tr key={i}>
                        <td style={{ fontFamily: 'monospace', fontSize: 11 }}>{d.device_id}</td>
                        <td>{d.device_type}</td>
                        <td style={{ fontSize: 11, opacity: 0.8 }}>{d.facility_id}</td>
                        <td>{d.current_age_days}</td>
                        <td style={{ fontWeight: 600, color: ciColor(d.cv_cindex) }}>{d.rul_days}<span style={{ opacity: 0.5, fontSize: 10 }}> ({d.rul_p10}–{d.rul_p90})</span></td>
                        <td><span className={bandBadge(d.risk_band)}>{d.risk_band}</span></td>
                        <td>{d.days_since_hw_oos}</td>
                        <td>{d.roll_fail_30d}</td>
                        <td>{d.is_overdue ? <span style={{ color: '#ef4444', fontWeight: 600 }}>Yes</span> : '—'}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <p style={{ fontSize: 12, opacity: 0.75, marginTop: 12 }}>{ps5Devices.note}</p>
            </div>
          )}
        </div>
      )}

      {/* Serial Health — per-component reliability on the hardware-OOS-Set event */}
      {activeTab === 'serial' && (
        <div>
          {!ps5Serials ? (
            <div className="card" style={{ padding: 32, textAlign: 'center' }}>
              <div className="card-header">PS5 — Serial Health</div>
              <p style={{ opacity: 0.7, marginTop: 12 }}>No PS5 serial-level reliability run for {cityName} yet.</p>
            </div>
          ) : (
            <div>
              <div className="card" style={{ marginBottom: 16, padding: 14 }}>
                <div style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: 10 }}>
                  <span style={{ padding: '4px 10px', borderRadius: 6, background: 'rgba(99,102,241,0.15)', color: '#a5b4fc', fontSize: 12, fontWeight: 600 }}>Failure = hardware OOS (Set) · v{ps5Serials.event_def_version}</span>
                  <span style={{ fontSize: 12, opacity: 0.7 }}>{ps5Serials.serials.length} components</span>
                  {ps5Serials.is_sample && <span style={{ marginLeft: 'auto', padding: '4px 10px', borderRadius: 6, background: 'rgba(245,158,11,0.15)', color: '#fbbf24', fontSize: 11, fontWeight: 700 }}>SAMPLE — pending live run</span>}
                </div>
              </div>
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 12, marginBottom: 16 }}>
                {['CRITICAL', 'HIGH', 'MEDIUM', 'LOW'].map((t) => {
                  const n = ps5Serials.serials.filter((s) => s.risk_tier === t).length;
                  const col = { CRITICAL: '#ef4444', HIGH: '#f59e0b', MEDIUM: '#3b82f6', LOW: '#22c55e' }[t];
                  return (<div className="card" key={t}><div className="card-header">{t}</div><div className="kpi-value" style={{ color: col }}>{n}</div><div className="kpi-label">components</div></div>);
                })}
              </div>
              <div className="card" style={{ overflowX: 'auto' }}>
                <div className="card-header">Serial / Component Reliability — click a column to sort</div>
                <table className="data-table">
                  <thead><tr>
                    {[['device_id', 'Device'], ['serial', 'Serial'], ['component_type', 'Component'], ['component_age_days', 'Age (d)'], ['oos_failures', 'OOS fails'], ['risk_score', 'Risk score'], ['risk_tier', 'Tier'], ['component_rul_days', 'Comp. RUL (d)'], ['is_overdue', 'Overdue']].map(([k, l]) => (
                      <th key={k} style={{ cursor: 'pointer', whiteSpace: 'nowrap' }} onClick={() => toggleSort(setSerSort, serSort, k)}>{l}{serSort.key === k ? (serSort.dir === 'asc' ? ' ▲' : ' ▼') : ''}</th>
                    ))}
                  </tr></thead>
                  <tbody>
                    {sortRows(ps5Serials.serials, serSort).map((s, i) => (
                      <tr key={i}>
                        <td style={{ fontFamily: 'monospace', fontSize: 11 }}>{s.device_id}</td>
                        <td style={{ fontFamily: 'monospace', fontSize: 11 }}>{s.serial}</td>
                        <td style={{ fontSize: 11 }}>{s.component_type}</td>
                        <td>{s.component_age_days}</td>
                        <td>{s.oos_failures}</td>
                        <td style={{ fontFamily: 'monospace', fontSize: 11 }}>{s.risk_score}</td>
                        <td><span className={bandBadge(s.risk_tier)}>{s.risk_tier}</span></td>
                        <td style={{ fontWeight: 600 }}>{s.component_rul_days}</td>
                        <td>{s.is_overdue ? <span style={{ color: '#ef4444', fontWeight: 600 }}>Yes</span> : '—'}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <p style={{ fontSize: 12, opacity: 0.75, marginTop: 12 }}>{ps5Serials.note}</p>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
