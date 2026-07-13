import React, { useState, useMemo, useEffect } from 'react';
import {
  BarChart, PieChart, LineChart, ComposedChart,
  Bar, Pie, Cell, Line,
  XAxis, YAxis, CartesianGrid, Tooltip, Legend,
  ResponsiveContainer,
} from 'recharts';
import {
  CITIES,
  getRootCauseFactors,
  getTemporalPatterns,
} from '../../data/mockData';

const API_BASE = 'https://a9yuqt9j9b.execute-api.us-east-1.amazonaws.com';

const FAILURE_MODE_COLORS = ['#6366f1', '#f59e0b', '#ef4444', '#10b981', '#3b82f6', '#ec4899'];

const SEQUENTIAL_PATTERNS = [
  { pattern: 'ERR_CARD_READ → ERR_NFC_FAIL → ERR_TIMEOUT', frequency: 142, avgSpan: '4.2 min', confidence: 0.84 },
  { pattern: 'ERR_COIN_JAM → ERR_PAYMENT_FAIL → ERR_TIMEOUT', frequency: 118, avgSpan: '2.8 min', confidence: 0.79 },
  { pattern: 'ERR_POWER_SURGE → ERR_DISPLAY → ERR_TOUCH_CAL', frequency: 96, avgSpan: '12.5 min', confidence: 0.72 },
  { pattern: 'ERR_GATE_MOTOR → ERR_SENSOR_ALIGN → ERR_GATE_MOTOR', frequency: 87, avgSpan: '8.1 min', confidence: 0.68 },
  { pattern: 'ERR_PRINTER → ERR_PAPER_JAM → ERR_PRINTER', frequency: 74, avgSpan: '3.6 min', confidence: 0.91 },
  { pattern: 'ERR_NFC_FAIL → ERR_CARD_READ → ERR_PAYMENT_FAIL', frequency: 63, avgSpan: '6.4 min', confidence: 0.61 },
];

const MARKOV_STATES = ['Normal', 'Degraded', 'Warning', 'Critical', 'Failed'];
const MARKOV_MATRIX = [
  [0.85, 0.10, 0.03, 0.01, 0.01],
  [0.20, 0.55, 0.18, 0.05, 0.02],
  [0.05, 0.15, 0.50, 0.22, 0.08],
  [0.02, 0.05, 0.10, 0.48, 0.35],
  [0.30, 0.10, 0.05, 0.05, 0.50],
];

const PREVENTABLE_FAILURES = [
  { mode: 'Card Reader Degradation', preventable: 78, action: 'Schedule proactive module replacement every 180 days' },
  { mode: 'Gate Motor Burnout', preventable: 65, action: 'Install current-limiting protection and lubricate every 90 days' },
  { mode: 'NFC Antenna Signal Loss', preventable: 72, action: 'Add environmental shielding and quarterly calibration' },
  { mode: 'Display Panel Failure', preventable: 58, action: 'Deploy thermal management upgrades and reduce brightness during off-peak' },
  { mode: 'Coin Mechanism Jam', preventable: 84, action: 'Implement weekly debris clearing schedule and sensor-based jam detection' },
];

const REPLACEMENT_PRIORITIES = [
  { component: 'Card Reader Module', criticality: 'critical', window: '0-30 days', savings: '$128,400' },
  { component: 'Gate Motor Assembly', criticality: 'high', window: '30-60 days', savings: '$94,200' },
  { component: 'NFC Antenna Unit', criticality: 'high', window: '30-60 days', savings: '$67,800' },
  { component: 'Thermal Printer Head', criticality: 'medium', window: '60-90 days', savings: '$43,500' },
  { component: 'Coin Sorting Mechanism', criticality: 'medium', window: '60-90 days', savings: '$38,900' },
  { component: 'Display Panel (LCD)', criticality: 'low', window: '90-120 days', savings: '$29,100' },
  { component: 'Power Supply Unit', criticality: 'critical', window: '0-30 days', savings: '$112,700' },
  { component: 'Touchscreen Digitizer', criticality: 'medium', window: '60-90 days', savings: '$35,200' },
];

const SUB_TABS = [
  { key: 'factors', label: 'Contributing Factors' },
  { key: 'patterns', label: 'Pattern Discovery' },
  { key: 'insights', label: 'Actionable Insights' },
];

export default function PS3RootCauseTab({ city, selectedDevices }) {
  const selectedCities = useMemo(() => [city], [city]);
  const [activeTab, setActiveTab] = useState('factors');

  // --- Live PS3 API state ---
  const [liveSummary, setLiveSummary] = useState(null);
  const [liveDrivers, setLiveDrivers] = useState([]);
  const [liveLoading, setLiveLoading] = useState(true);
  const [livePS3Preds, setLivePS3Preds] = useState([]);

  useEffect(() => {
    setLiveLoading(true);
    Promise.all([
      fetch(`${API_BASE}/ps3/summary?city=${city}`).then((r) => r.json()).catch(() => null),
      fetch(`${API_BASE}/ps3/drivers?city=${city}`).then((r) => r.json()).catch(() => []),
      fetch(`${API_BASE}/ps3/predictions?city=${city}`).then((r) => r.json()).catch(() => []),
    ]).then(([summary, drivers, preds]) => {
      setLiveSummary(summary && !summary.error ? summary : null);
      setLiveDrivers(Array.isArray(drivers) ? drivers : []);
      setLivePS3Preds(Array.isArray(preds) ? preds : []);
      setLiveLoading(false);
    });
  }, [city]);

  const liveDriversChart = useMemo(() =>
    liveDrivers
      .map((d) => ({ feature: d.feature, shap: parseFloat(d.shap_importance) || 0, solo_auc: parseFloat(d.solo_auc) || 0, rank: d.driver_rank }))
      .sort((a, b) => a.rank - b.rank),
  [liveDrivers]);

  // Live severity distribution from ps3_severity_predictions (populated via PS1 → PS3 inserts)
  const liveSeverityPie = useMemo(() => {
    if (!livePS3Preds.length) return null;
    const counts = {};
    livePS3Preds.forEach((p) => { counts[p.predicted_label] = (counts[p.predicted_label] || 0) + 1; });
    return Object.entries(counts).map(([name, value]) => ({ name, value }));
  }, [livePS3Preds]);

  // Live hardware failure breakdown (TVM vs GATE) from ps3_severity_predictions
  const liveHardwareBreakdown = useMemo(() => {
    if (!livePS3Preds.length) return null;
    const byCategory = {};
    livePS3Preds.forEach((p) => {
      const cat = p.mars_device_category || 'UNKNOWN';
      if (!byCategory[cat]) byCategory[cat] = { category: cat, MAJOR: 0, CRITICAL: 0 };
      byCategory[cat][p.predicted_label] = (byCategory[cat][p.predicted_label] || 0) + 1;
    });
    return Object.values(byCategory);
  }, [livePS3Preds]);

  const rootCause = useMemo(() => getRootCauseFactors(selectedCities, selectedDevices), [selectedCities, selectedDevices]);
  const temporal = useMemo(() => getTemporalPatterns(selectedCities, selectedDevices), [selectedCities, selectedDevices]);

  const paretoData = useMemo(() => {
    const hw = [...rootCause.hardware];
    const total = hw.reduce((s, h) => s + h.failure_count, 0);
    let cumulative = 0;
    return hw.map((h) => { cumulative += h.failure_count; return { ...h, cumulative_pct: Math.round((cumulative / total) * 10000) / 100 }; });
  }, [rootCause]);

  const failureModesPie = useMemo(() => rootCause.failureModes.map((f) => ({ name: f.mode, value: f.count })), [rootCause]);

  const hourlyWithPeak = useMemo(() => {
    const maxCount = Math.max(...temporal.hourly.map((h) => h.count));
    const threshold = maxCount * 0.75;
    return temporal.hourly.map((h) => ({ ...h, isPeak: h.count >= threshold, label: `${String(h.hour).padStart(2, '0')}:00` }));
  }, [temporal]);

  function envCorrelationColor(val) {
    if (val >= 0.7) return '#ef4444';
    if (val >= 0.5) return '#f59e0b';
    return '#22c55e';
  }

  return (
    <div>
      <div className="tab-container">
        {SUB_TABS.map((t) => (
          <button key={t.key} className={`tab ${activeTab === t.key ? 'active' : ''}`} onClick={() => setActiveTab(t.key)}>{t.label}</button>
        ))}
      </div>

      {/* Contributing Factors */}
      {activeTab === 'factors' && (
        <div>
          {/* LIVE banner */}
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 16, padding: '8px 14px', background: 'rgba(34,197,94,0.08)', border: '1px solid rgba(34,197,94,0.25)', borderRadius: 8 }}>
            <span style={{ width: 8, height: 8, borderRadius: '50%', background: liveLoading ? '#f59e0b' : '#22c55e', display: 'inline-block' }} />
            <span style={{ fontSize: 12, fontWeight: 600, color: 'var(--text-secondary)' }}>
              {liveLoading ? 'Loading live PS3 data…' : `LIVE — ${liveSummary?.endpoint_name || 'chicago-ps3-rootcause-v1'} · MLflow ${liveSummary?.mlflow_version || ''} · as of ${liveSummary?.as_of_date || '—'}`}
            </span>
          </div>

          {/* Live model KPI cards */}
          {!liveLoading && liveSummary && (
            <>
              <div className="grid-4" style={{ marginBottom: 24 }}>
                <div className="card">
                  <div className="card-header">AUC-Macro</div>
                  <div className="kpi-value" style={{ color: '#22c55e' }}>{parseFloat(liveSummary.test_auc_macro).toFixed(4)}</div>
                  <div className="kpi-label">3-class severity model</div>
                  <div className="kpi-trend" style={{ color: '#22c55e' }}>Excellent</div>
                </div>
                <div className="card">
                  <div className="card-header">F1-Macro</div>
                  <div className="kpi-value">{parseFloat(liveSummary.test_f1_macro).toFixed(4)}</div>
                  <div className="kpi-label">Macro-averaged F1</div>
                  <div className="kpi-trend" style={{ color: '#22c55e' }}>Above 0.90</div>
                </div>
                <div className="card">
                  <div className="card-header">Total Incidents</div>
                  <div className="kpi-value">{liveSummary.n_incidents?.toLocaleString()}</div>
                  <div className="kpi-label">{liveSummary.date_start} → {liveSummary.date_end}</div>
                  <div className="kpi-trend" style={{ color: '#3b82f6' }}>
                    {liveSummary.n_major?.toLocaleString()} major · {liveSummary.n_critical?.toLocaleString()} critical
                  </div>
                </div>
                <div className="card">
                  <div className="card-header">Top Driver</div>
                  <div className="kpi-value" style={{ fontSize: 14, fontFamily: 'monospace', wordBreak: 'break-all' }}>{liveSummary.dominant_feature}</div>
                  <div className="kpi-label">SHAP importance</div>
                  <div className="kpi-trend" style={{ color: '#f59e0b' }}>{parseFloat(liveSummary.dominant_feature_shap).toFixed(4)}</div>
                </div>
              </div>

              {/* PS3 model info row */}
              <div className="card" style={{ marginBottom: 24, padding: '12px 20px' }}>
                <div style={{ display: 'flex', flexWrap: 'wrap', gap: 24, fontSize: 12, color: 'var(--text-secondary)' }}>
                  <span><strong>Model:</strong> {liveSummary.champion_model}</span>
                  <span><strong>Accuracy:</strong> {(parseFloat(liveSummary.test_accuracy) * 100).toFixed(1)}%</span>
                  <span><strong>Feasibility:</strong> {liveSummary.feasibility_pct}%</span>
                  <span><strong>Training data:</strong> {liveSummary.device_note}</span>
                  <span style={{ color: '#f59e0b' }}><strong>Note:</strong> {liveSummary.is_root_cause ? 'True root cause' : 'Severity classification (not true root cause)'}</span>
                </div>
              </div>
            </>
          )}

          {/* Live severity drivers chart */}
          {!liveLoading && liveDriversChart.length > 0 && (
            <div className="card" style={{ marginBottom: 24 }}>
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 12 }}>
                <div className="card-header" style={{ marginBottom: 0 }}>Severity Drivers — SHAP Importance (Live)</div>
                <span style={{ fontSize: 11, padding: '2px 8px', borderRadius: 10, background: 'rgba(34,197,94,0.12)', color: '#22c55e', fontWeight: 600 }}>LIVE</span>
              </div>
              <ResponsiveContainer width="100%" height={Math.max(220, liveDriversChart.length * 36)}>
                <BarChart data={liveDriversChart} layout="vertical" margin={{ left: 180 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.1)" />
                  <XAxis type="number" tick={{ fontSize: 11 }} domain={[0, 1]} />
                  <YAxis dataKey="feature" type="category" width={175} tick={{ fontSize: 11 }} />
                  <Tooltip formatter={(val, key) => [val.toFixed(4), key === 'shap' ? 'SHAP Importance' : 'Solo AUC']} />
                  <Legend />
                  <Bar dataKey="shap" name="SHAP Importance" fill="#6366f1" radius={[0, 4, 4, 0]} />
                  <Bar dataKey="solo_auc" name="Solo AUC" fill="#10b981" radius={[0, 4, 4, 0]} opacity={0.7} />
                </BarChart>
              </ResponsiveContainer>
            </div>
          )}

          <div className="card" style={{ marginBottom: 24 }}>
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 12 }}>
              <div className="card-header" style={{ marginBottom: 0 }}>Hardware Component Failures (Pareto) — {CITIES.find(c => c.id === city)?.name || city}</div>
              <span style={{ fontSize: 11, padding: '2px 8px', borderRadius: 10, background: 'rgba(245,158,11,0.12)', color: '#f59e0b', fontWeight: 600 }}>SIMULATED</span>
            </div>
            <ResponsiveContainer width="100%" height={320}>
              <ComposedChart data={paretoData} layout="vertical" margin={{ left: 130 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.1)" />
                <XAxis type="number" tick={{ fontSize: 11 }} />
                <YAxis dataKey="component" type="category" width={120} tick={{ fontSize: 11 }} />
                <Tooltip />
                <Legend />
                <Bar dataKey="failure_count" name="Failure Count" fill="#6366f1" radius={[0, 4, 4, 0]} />
                <Line dataKey="cumulative_pct" name="Cumulative %" type="monotone" stroke="#f59e0b" strokeWidth={2} dot={{ fill: '#f59e0b', r: 4 }} yAxisId="right" />
                <YAxis yAxisId="right" orientation="right" domain={[0, 100]} tick={{ fontSize: 10 }} />
              </ComposedChart>
            </ResponsiveContainer>
          </div>

          <div className="grid-2" style={{ marginBottom: 24 }}>
            {/* Severity Distribution — live when ps3_severity_predictions has rows */}
            <div className="card">
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 12 }}>
                <div className="card-header" style={{ marginBottom: 0 }}>
                  {liveSeverityPie ? 'Severity Distribution (MAJOR / CRITICAL)' : 'Failure Modes Distribution'}
                </div>
                <span style={{ fontSize: 11, padding: '2px 8px', borderRadius: 10, background: liveSeverityPie ? 'rgba(34,197,94,0.12)' : 'rgba(245,158,11,0.12)', color: liveSeverityPie ? '#22c55e' : '#f59e0b', fontWeight: 600 }}>
                  {liveSeverityPie ? 'LIVE · RDS' : 'SIMULATED'}
                </span>
              </div>
              <ResponsiveContainer width="100%" height={300}>
                <PieChart>
                  {liveSeverityPie ? (
                    <Pie data={liveSeverityPie} dataKey="value" nameKey="name" cx="50%" cy="50%" innerRadius={60} outerRadius={100}
                      label={({ name, percent, value }) => `${name} (${value} · ${(percent * 100).toFixed(0)}%)`}>
                      {liveSeverityPie.map((entry, i) => (
                        <Cell key={i} fill={entry.name === 'CRITICAL' ? '#ef4444' : '#f59e0b'} />
                      ))}
                    </Pie>
                  ) : (
                    <Pie data={failureModesPie} dataKey="value" nameKey="name" cx="50%" cy="50%" innerRadius={60} outerRadius={100}
                      label={({ name, percent }) => `${name} (${(percent * 100).toFixed(0)}%)`}>
                      {failureModesPie.map((_, i) => <Cell key={i} fill={FAILURE_MODE_COLORS[i % FAILURE_MODE_COLORS.length]} />)}
                    </Pie>
                  )}
                  <Tooltip />
                  <Legend />
                </PieChart>
              </ResponsiveContainer>
              {liveSeverityPie && (
                <div style={{ padding: '8px 12px', fontSize: 11, color: 'var(--text-secondary)' }}>
                  {livePS3Preds.length} scored incidents from PS1 failure predictions · Model v8
                </div>
              )}
            </div>

            {/* Hardware breakdown — live when predictions exist, else show simulated software/env */}
            <div className="card">
              {liveHardwareBreakdown ? (
                <>
                  <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 12 }}>
                    <div className="card-header" style={{ marginBottom: 0 }}>Hardware Severity by Device Category</div>
                    <span style={{ fontSize: 11, padding: '2px 8px', borderRadius: 10, background: 'rgba(34,197,94,0.12)', color: '#22c55e', fontWeight: 600 }}>LIVE · RDS</span>
                  </div>
                  <ResponsiveContainer width="100%" height={160}>
                    <BarChart data={liveHardwareBreakdown}>
                      <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.1)" />
                      <XAxis dataKey="category" tick={{ fontSize: 13, fontWeight: 600 }} />
                      <YAxis tick={{ fontSize: 11 }} />
                      <Tooltip />
                      <Legend />
                      <Bar dataKey="CRITICAL" fill="#ef4444" radius={[3, 3, 0, 0]} maxBarSize={50} />
                      <Bar dataKey="MAJOR" fill="#f59e0b" radius={[3, 3, 0, 0]} maxBarSize={50} />
                    </BarChart>
                  </ResponsiveContainer>
                  <div style={{ overflowX: 'auto', marginTop: 12 }}>
                    <table className="data-table">
                      <thead><tr><th>Device Type</th><th>CRITICAL</th><th>MAJOR</th><th>Total</th></tr></thead>
                      <tbody>
                        {liveHardwareBreakdown.map((r) => (
                          <tr key={r.category}>
                            <td><span className="badge badge-info">{r.category}</span></td>
                            <td style={{ fontWeight: 700, color: '#ef4444' }}>{r.CRITICAL || 0}</td>
                            <td style={{ fontWeight: 600, color: '#f59e0b' }}>{r.MAJOR || 0}</td>
                            <td style={{ fontWeight: 600 }}>{(r.CRITICAL || 0) + (r.MAJOR || 0)}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </>
              ) : (
                <>
                  <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 12 }}>
                    <div className="card-header" style={{ marginBottom: 0 }}>Software Version Failure Rates</div>
                    <span style={{ fontSize: 11, padding: '2px 8px', borderRadius: 10, background: 'rgba(245,158,11,0.12)', color: '#f59e0b', fontWeight: 600 }}>SIMULATED</span>
                  </div>
                  <table className="data-table">
                    <thead><tr><th>Version</th><th>Failure Rate (%)</th></tr></thead>
                    <tbody>
                      {rootCause.software.map((s, i) => (
                        <tr key={i}>
                          <td style={{ fontFamily: 'monospace' }}>{s.version}</td>
                          <td style={{ color: s.failure_rate > 10 ? '#ef4444' : s.failure_rate > 5 ? '#f59e0b' : '#22c55e', fontWeight: 600 }}>{s.failure_rate}%</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                  <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginTop: 20, marginBottom: 8 }}>
                    <div className="card-header" style={{ marginBottom: 0 }}>Environmental Correlations</div>
                    <span style={{ fontSize: 11, padding: '2px 8px', borderRadius: 10, background: 'rgba(245,158,11,0.12)', color: '#f59e0b', fontWeight: 600 }}>SIMULATED</span>
                  </div>
                  <table className="data-table">
                    <thead><tr><th>Factor</th><th>Correlation</th></tr></thead>
                    <tbody>
                      {rootCause.environmental.map((e, i) => (
                        <tr key={i}>
                          <td>{e.factor}</td>
                          <td style={{ color: envCorrelationColor(e.correlation), fontWeight: 600 }}>{e.correlation.toFixed(2)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </>
              )}
            </div>
          </div>
        </div>
      )}

      {/* Pattern Discovery */}
      {activeTab === 'patterns' && (
        <div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 16, padding: '8px 14px', background: 'rgba(245,158,11,0.07)', border: '1px solid rgba(245,158,11,0.25)', borderRadius: 8 }}>
            <span style={{ width: 8, height: 8, borderRadius: '50%', background: '#f59e0b', display: 'inline-block' }} />
            <span style={{ fontSize: 12, fontWeight: 600, color: 'var(--text-secondary)' }}>
              SIMULATED — Temporal patterns require raw incident log aggregation (not yet in RDS). Charts below use synthetic data.
            </span>
          </div>
          <div className="card" style={{ marginBottom: 24 }}>
            <div className="card-header">Hourly Failure Distribution (24h)</div>
            <ResponsiveContainer width="100%" height={280}>
              <BarChart data={hourlyWithPeak}>
                <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.1)" />
                <XAxis dataKey="label" tick={{ fontSize: 10 }} />
                <YAxis tick={{ fontSize: 11 }} />
                <Tooltip />
                <Bar dataKey="count" radius={[4, 4, 0, 0]}>
                  {hourlyWithPeak.map((entry, i) => <Cell key={i} fill={entry.isPeak ? '#ef4444' : '#6366f1'} />)}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          </div>

          <div className="grid-2">
            <div className="card">
              <div className="card-header">Daily Failure Pattern (Mon-Sun)</div>
              <ResponsiveContainer width="100%" height={280}>
                <BarChart data={temporal.daily}>
                  <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.1)" />
                  <XAxis dataKey="day" tick={{ fontSize: 11 }} />
                  <YAxis tick={{ fontSize: 11 }} />
                  <Tooltip />
                  <Bar dataKey="count" fill="#f59e0b" radius={[4, 4, 0, 0]} />
                </BarChart>
              </ResponsiveContainer>
            </div>
            <div className="card">
              <div className="card-header">Monthly Failure Trend (12 months)</div>
              <ResponsiveContainer width="100%" height={280}>
                <LineChart data={temporal.monthly}>
                  <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.1)" />
                  <XAxis dataKey="month" tick={{ fontSize: 11 }} />
                  <YAxis tick={{ fontSize: 11 }} />
                  <Tooltip />
                  <Line type="monotone" dataKey="count" stroke="#10b981" strokeWidth={2} dot={{ fill: '#10b981', r: 4 }} />
                </LineChart>
              </ResponsiveContainer>
            </div>
          </div>

          {/* Calendar Heatmap */}
          <div className="card" style={{ marginTop: 24 }}>
            <div className="card-header">Temporal Pattern Calendar Heatmap (Day x Hour)</div>
            <div style={{ overflowX: 'auto', padding: 16 }}>
              {(() => {
                const days = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];
                const hours = Array.from({ length: 24 }, (_, i) => i);
                return (
                  <div style={{ display: 'grid', gridTemplateColumns: `50px repeat(24, 1fr)`, gap: 2, fontSize: 10 }}>
                    <div />
                    {hours.map((h) => <div key={h} style={{ textAlign: 'center', fontWeight: 600, fontSize: 9, padding: 2 }}>{String(h).padStart(2, '0')}</div>)}
                    {days.map((day, di) => (
                      <React.Fragment key={day}>
                        <div style={{ fontWeight: 600, fontSize: 10, display: 'flex', alignItems: 'center' }}>{day}</div>
                        {hours.map((h) => {
                          const val = Math.abs(Math.sin(di * 1.7 + h * 0.43) * Math.cos(di * 0.3 + h * 0.8));
                          const intensity = Math.round(val * 100);
                          const bg = intensity > 75 ? 'rgba(239,68,68,0.8)' : intensity > 50 ? 'rgba(245,158,11,0.7)' : intensity > 25 ? 'rgba(99,102,241,0.5)' : 'rgba(99,102,241,0.15)';
                          return <div key={h} title={`${day} ${String(h).padStart(2, '0')}:00 — Intensity: ${intensity}%`} style={{ background: bg, borderRadius: 2, height: 28, display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 8, cursor: 'default' }}>{intensity > 50 ? intensity : ''}</div>;
                        })}
                      </React.Fragment>
                    ))}
                  </div>
                );
              })()}
            </div>
          </div>

          {/* Sequential Patterns */}
          <div className="card" style={{ marginTop: 24, overflowX: 'auto' }}>
            <div className="card-header">Sequential Pattern Explorer</div>
            <table className="data-table">
              <thead><tr><th>Sequential Pattern</th><th>Frequency</th><th>Avg Time Span</th><th>Confidence</th></tr></thead>
              <tbody>
                {SEQUENTIAL_PATTERNS.map((sp, i) => (
                  <tr key={i}>
                    <td style={{ fontFamily: 'monospace', fontSize: 12 }}>{sp.pattern}</td>
                    <td style={{ fontWeight: 600 }}>{sp.frequency}</td>
                    <td>{sp.avgSpan}</td>
                    <td><span style={{ color: sp.confidence >= 0.8 ? '#10b981' : sp.confidence >= 0.65 ? '#f59e0b' : '#3b82f6', fontWeight: 700, padding: '2px 8px', borderRadius: 4, background: sp.confidence >= 0.8 ? 'rgba(16,185,129,0.1)' : sp.confidence >= 0.65 ? 'rgba(245,158,11,0.1)' : 'rgba(59,130,246,0.1)' }}>{(sp.confidence * 100).toFixed(0)}%</span></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {/* Markov Chain */}
          <div className="card" style={{ marginTop: 24 }}>
            <div className="card-header">Markov Chain State Transition Probabilities</div>
            <div style={{ overflowX: 'auto', padding: 16 }}>
              <div style={{ display: 'grid', gridTemplateColumns: `90px repeat(${MARKOV_STATES.length}, 1fr)`, gap: 2, fontSize: 11 }}>
                <div style={{ fontWeight: 700, fontSize: 10, textAlign: 'center' }}>From \ To</div>
                {MARKOV_STATES.map((s) => <div key={s} style={{ textAlign: 'center', fontWeight: 700, fontSize: 10, padding: 6 }}>{s}</div>)}
                {MARKOV_STATES.map((fromState, ri) => (
                  <React.Fragment key={fromState}>
                    <div style={{ fontWeight: 700, fontSize: 10, display: 'flex', alignItems: 'center', padding: '0 4px' }}>{fromState}</div>
                    {MARKOV_MATRIX[ri].map((prob, ci) => {
                      const bg = prob >= 0.5 ? 'rgba(239,68,68,0.7)' : prob >= 0.3 ? 'rgba(245,158,11,0.6)' : prob >= 0.15 ? 'rgba(99,102,241,0.5)' : prob >= 0.05 ? 'rgba(99,102,241,0.2)' : 'rgba(255,255,255,0.05)';
                      return <div key={ci} style={{ background: ri === ci ? 'rgba(99,102,241,0.6)' : bg, borderRadius: 3, display: 'flex', alignItems: 'center', justifyContent: 'center', height: 40, fontWeight: prob >= 0.3 ? 700 : 400, fontSize: 11 }}>{prob.toFixed(2)}</div>;
                    })}
                  </React.Fragment>
                ))}
              </div>
            </div>
            <div style={{ display: 'flex', justifyContent: 'center', gap: 16, fontSize: 10, opacity: 0.6, paddingBottom: 12 }}>
              <span>Diagonal = self-transition</span><span>Rows sum to 1.0</span>
            </div>
          </div>
        </div>
      )}

      {/* Actionable Insights */}
      {activeTab === 'insights' && (
        <div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 16, padding: '8px 14px', background: 'rgba(245,158,11,0.07)', border: '1px solid rgba(245,158,11,0.25)', borderRadius: 8 }}>
            <span style={{ width: 8, height: 8, borderRadius: '50%', background: '#f59e0b', display: 'inline-block' }} />
            <span style={{ fontSize: 12, fontWeight: 600, color: 'var(--text-secondary)' }}>
              SIMULATED — Preventable failure recommendations require domain logic not yet in RDS. These are representative placeholders.
            </span>
          </div>
          <div className="card" style={{ marginBottom: 24 }}>
            <div className="card-header">Top 5 Preventable Failure Modes</div>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 12, padding: '8px 0' }}>
              {PREVENTABLE_FAILURES.map((f, i) => (
                <div key={i} style={{ display: 'flex', alignItems: 'flex-start', gap: 16, padding: '14px 16px', background: 'rgba(255,255,255,0.03)', borderRadius: 8, borderLeft: `4px solid ${f.preventable >= 75 ? '#22c55e' : f.preventable >= 60 ? '#f59e0b' : '#3b82f6'}` }}>
                  <div style={{ minWidth: 32, height: 32, borderRadius: '50%', background: 'rgba(99,102,241,0.15)', display: 'flex', alignItems: 'center', justifyContent: 'center', fontWeight: 700, fontSize: 14, color: '#6366f1' }}>{i + 1}</div>
                  <div style={{ flex: 1 }}>
                    <div style={{ fontWeight: 600, fontSize: 14, marginBottom: 4 }}>{f.mode}</div>
                    <div style={{ fontSize: 12, opacity: 0.7, marginBottom: 6 }}>{f.action}</div>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                      <div style={{ width: 120, height: 6, background: 'rgba(255,255,255,0.1)', borderRadius: 3, overflow: 'hidden' }}>
                        <div style={{ width: `${f.preventable}%`, height: '100%', background: f.preventable >= 75 ? '#22c55e' : f.preventable >= 60 ? '#f59e0b' : '#3b82f6', borderRadius: 3 }} />
                      </div>
                      <span style={{ fontSize: 12, fontWeight: 700, color: f.preventable >= 75 ? '#22c55e' : f.preventable >= 60 ? '#f59e0b' : '#3b82f6' }}>{f.preventable}% preventable</span>
                    </div>
                  </div>
                </div>
              ))}
            </div>
          </div>

          <div className="card" style={{ overflowX: 'auto' }}>
            <div className="card-header">Component Replacement Priorities</div>
            <table className="data-table">
              <thead><tr><th>Component</th><th>Criticality</th><th>Replacement Window</th><th>Est. Cost Savings</th></tr></thead>
              <tbody>
                {REPLACEMENT_PRIORITIES.map((r, i) => (
                  <tr key={i}>
                    <td style={{ fontWeight: 500 }}>{r.component}</td>
                    <td><span className={`badge-${r.criticality}`}>{r.criticality.charAt(0).toUpperCase() + r.criticality.slice(1)}</span></td>
                    <td>{r.window}</td>
                    <td style={{ fontWeight: 600, color: '#22c55e' }}>{r.savings}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
}
