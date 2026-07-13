import React, { useState, useMemo } from 'react';
import {
  LineChart, BarChart, PieChart, ComposedChart,
  Line, Bar, Pie, Cell,
  XAxis, YAxis, CartesianGrid, Tooltip, Legend,
  ResponsiveContainer, ReferenceLine,
} from 'recharts';
import {
  CITIES, DEVICES, DEVICE_COLORS,
  getPredictionSummary,
  getAccuracyTrend,
  getModelPerformance,
  getConfusionMatrix,
  getFeatureImportance,
} from '../../data/mockData';

const SEVERITY_COLORS = {
  Critical: '#ef4444',
  High: '#f97316',
  Medium: '#f59e0b',
  Low: '#3b82f6',
  Info: '#6b7280',
};

const CITY_COLOR_MAP = {};
CITIES.forEach((c) => { CITY_COLOR_MAP[c.id] = c.color; });

const FEATURE_IMPORTANCE_OVER_TIME = (() => {
  const months = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
  const features = [
    { name: 'Transaction Volume', offset: 0 },
    { name: 'Error Frequency', offset: 1.2 },
    { name: 'Temperature', offset: 2.4 },
    { name: 'Operating Hours', offset: 3.6 },
    { name: 'Firmware Age', offset: 4.8 },
  ];
  return months.map((month, i) => {
    const point = { month };
    features.forEach((f) => {
      const t = i / 11;
      const val = 0.20 + 0.10 * Math.sin(t * Math.PI * 2 + f.offset) + 0.05 * Math.cos(t * Math.PI * 3 + f.offset * 1.5);
      point[f.name] = Math.round(Math.max(0.05, Math.min(0.35, val)) * 1000) / 1000;
    });
    return point;
  });
})();

const FEATURE_IMPORTANCE_COLORS = {
  'Transaction Volume': '#6366f1',
  'Error Frequency': '#f59e0b',
  'Temperature': '#ef4444',
  'Operating Hours': '#10b981',
  'Firmware Age': '#3b82f6',
};

const SUB_TABS = [
  { key: 'performance', label: 'Executive Summary' },
  { key: 'model', label: 'Model Performance' },
  { key: 'causation', label: 'Causation Analysis' },
];

export default function PS1FailurePredictionTab({ city, selectedDevices }) {
  const selectedCities = useMemo(() => [city], [city]);
  const [activeTab, setActiveTab] = useState('performance');
  const [causalDevice, setCausalDevice] = useState(selectedDevices[0] || 'Readers');
  const [confDevice, setConfDevice] = useState(selectedDevices[0] || 'Readers');

  // --- Data ---
  const predSummary = useMemo(() => getPredictionSummary(selectedCities, selectedDevices), [selectedCities, selectedDevices]);
  const accuracyTrend = useMemo(() => getAccuracyTrend(selectedCities, selectedDevices), [selectedCities, selectedDevices]);
  const modelPerf = useMemo(() => getModelPerformance(selectedCities, selectedDevices), [selectedCities, selectedDevices]);
  const featureData = useMemo(() => getFeatureImportance(city, causalDevice), [city, causalDevice]);
  const confMatrix = useMemo(() => getConfusionMatrix(city, confDevice), [city, confDevice]);

  const cityAccuracyTrend = useMemo(() => {
    return accuracyTrend.map((row) => {
      const point = { date: row.date };
      const deviceKeys = selectedDevices.map((d) => `${city}_${d}`);
      const vals = deviceKeys.map((k) => row[k]).filter((v) => v != null);
      point[city] = vals.length ? Math.round((vals.reduce((a, b) => a + b, 0) / vals.length) * 100) / 100 : null;
      return point;
    });
  }, [accuracyTrend, city, selectedDevices]);

  const riskBarData = useMemo(() => [
    { category: 'Critical', count: predSummary.critical, fill: SEVERITY_COLORS.Critical },
    { category: 'High', count: predSummary.high, fill: SEVERITY_COLORS.High },
    { category: 'Medium', count: predSummary.medium, fill: SEVERITY_COLORS.Medium },
    { category: 'Low', count: predSummary.low, fill: SEVERITY_COLORS.Low },
    { category: 'Info', count: predSummary.info, fill: SEVERITY_COLORS.Info },
  ], [predSummary]);

  const highRiskCount = predSummary.critical + predSummary.high;

  const avgAccuracy = useMemo(() => {
    if (!modelPerf.length) return 0;
    return Math.round((modelPerf.reduce((s, m) => s + m.accuracy, 0) / modelPerf.length) * 100) / 100;
  }, [modelPerf]);

  const severityPieData = useMemo(() => [
    { name: 'Critical', value: predSummary.critical },
    { name: 'High', value: predSummary.high },
    { name: 'Medium', value: predSummary.medium },
    { name: 'Low', value: predSummary.low },
    { name: 'Info', value: predSummary.info },
  ], [predSummary]);

  const devicePieData = useMemo(() => {
    const counts = {};
    modelPerf.forEach((m) => { counts[m.device] = (counts[m.device] || 0) + 1; });
    return Object.entries(counts).map(([name, value]) => ({ name, value }));
  }, [modelPerf]);

  const accuracyGrouped = useMemo(() => {
    const byDevice = {};
    modelPerf.forEach((m) => {
      if (!byDevice[m.device]) byDevice[m.device] = { device: m.device };
      byDevice[m.device][m.city] = m.accuracy;
    });
    return Object.values(byDevice);
  }, [modelPerf]);

  const confMetrics = useMemo(() => {
    const { tp, fp, fn, tn } = confMatrix;
    const total = tp + fp + fn + tn;
    const accuracy = ((tp + tn) / total * 100).toFixed(2);
    const precision = (tp / (tp + fp) * 100).toFixed(2);
    const recall = (tp / (tp + fn) * 100).toFixed(2);
    const f1 = (2 * (precision * recall) / (parseFloat(precision) + parseFloat(recall))).toFixed(2);
    const fpr = (fp / (fp + tn) * 100).toFixed(2);
    const specificity = (tn / (tn + fp) * 100).toFixed(2);
    return { accuracy, precision, recall, f1, fpr, specificity };
  }, [confMatrix]);

  const precisionRecallCurve = useMemo(() => {
    const points = [];
    for (let threshold = 0; threshold <= 100; threshold += 5) {
      const t = threshold / 100;
      const recall = Math.max(0, 1 - t * 0.95);
      const precision = Math.min(1, 0.6 + t * 0.38 + Math.sin(t * 3) * 0.02);
      points.push({ threshold, recall: Math.round(recall * 100) / 100, precision: Math.round(precision * 100) / 100 });
    }
    return points;
  }, []);

  const fpFnTrend = useMemo(() => {
    return cityAccuracyTrend.map((row, idx) => {
      const baseFP = 45 - idx * 0.8 + Math.sin(idx * 0.5) * 8;
      const baseFN = 60 - idx * 1.0 + Math.cos(idx * 0.7) * 10;
      return {
        date: row.date,
        false_positives: Math.max(5, Math.round(baseFP)),
        false_negatives: Math.max(8, Math.round(baseFN)),
        fp_rate: Math.round(Math.max(1, baseFP / 10) * 100) / 100,
        fn_rate: Math.round(Math.max(1.5, baseFN / 8) * 100) / 100,
      };
    });
  }, [cityAccuracyTrend]);

  const correlationMatrix = useMemo(() => {
    const features = featureData.slice(0, 8).map((f) => f.feature);
    const matrix = features.map((f1, i) => {
      return features.map((f2, j) => {
        if (i === j) return 1.0;
        const seed = (f1.length * 31 + f2.length * 17 + i * 7 + j * 3) % 100;
        return Math.round((seed / 100 - 0.3) * 100) / 100;
      });
    });
    return { features, matrix };
  }, [featureData]);

  const failureTypeBreakdown = useMemo(() => {
    const failureTypes = ['Mechanical Wear', 'Electrical Short', 'Software Crash', 'Communication Loss', 'Sensor Drift', 'Overheating'];
    const factors = ['Temperature', 'Humidity', 'Age', 'Usage', 'Firmware', 'Vibration'];
    return failureTypes.map((ft, i) => {
      const row = { failure_type: ft };
      factors.forEach((f, j) => {
        const seed = (i * 7 + j * 13 + 42) % 100;
        row[f] = Math.round((seed / 100) * 0.8 * 100) / 100;
      });
      return row;
    });
  }, []);

  function accuracyColor(val) {
    if (val >= 94) return '#22c55e';
    if (val >= 91) return '#f59e0b';
    return '#ef4444';
  }

  function correlationColor(val) {
    if (val >= 0.7) return 'rgba(239,68,68,0.7)';
    if (val >= 0.4) return 'rgba(245,158,11,0.6)';
    if (val >= 0) return 'rgba(34,197,94,0.5)';
    if (val >= -0.3) return 'rgba(59,130,246,0.4)';
    return 'rgba(99,102,241,0.5)';
  }

  return (
    <div>
      {/* Sub-Tab Navigation */}
      <div className="tab-container">
        {SUB_TABS.map((t) => (
          <button key={t.key} className={`tab ${activeTab === t.key ? 'active' : ''}`} onClick={() => setActiveTab(t.key)}>
            {t.label}
          </button>
        ))}
      </div>

      {/* Executive Summary */}
      {activeTab === 'performance' && (
        <div>
          <div className="grid-4" style={{ marginBottom: 24 }}>
            <div className="card">
              <div className="card-header">Model Accuracy</div>
              <div className="kpi-value">{avgAccuracy}%</div>
              <div className="kpi-label">Avg across all models</div>
              <div className="kpi-trend" style={{ color: avgAccuracy >= 93 ? '#22c55e' : '#f59e0b' }}>
                {avgAccuracy >= 93 ? 'Above target (>90%)' : 'Near target (>90%)'}
              </div>
            </div>
            <div className="card">
              <div className="card-header">Total Predictions</div>
              <div className="kpi-value">{predSummary.total.toLocaleString()}</div>
              <div className="kpi-label">Last 30 days</div>
              <div className="kpi-trend" style={{ color: '#3b82f6' }}>All severity levels</div>
            </div>
            <div className="card">
              <div className="card-header">High-Risk Devices</div>
              <div className="kpi-value">{highRiskCount.toLocaleString()}</div>
              <div className="kpi-label">Critical + High severity</div>
              <div className="kpi-trend" style={{ color: '#ef4444' }}>Requires attention</div>
            </div>
            <div className="card">
              <div className="card-header">Prediction Latency</div>
              <div className="kpi-value">&lt;5 min</div>
              <div className="kpi-label">End-to-end pipeline</div>
              <div className="kpi-trend" style={{ color: '#22c55e' }}>Within SLA</div>
            </div>
          </div>

          <div className="card" style={{ marginBottom: 24 }}>
            <div className="card-header">30-Day Rolling Accuracy — {CITIES.find(c => c.id === city)?.name || city}</div>
            <ResponsiveContainer width="100%" height={300}>
              <LineChart data={cityAccuracyTrend}>
                <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.1)" />
                <XAxis dataKey="date" tick={{ fontSize: 11 }} interval={4} />
                <YAxis domain={[85, 100]} tick={{ fontSize: 11 }} />
                <Tooltip />
                <Legend />
                <ReferenceLine y={90} stroke="#ef4444" strokeDasharray="5 5" label={{ value: 'Target 90%', position: 'right', fill: '#ef4444', fontSize: 11 }} />
                <Line type="monotone" dataKey={city} stroke={CITY_COLOR_MAP[city] || '#8884d8'} strokeWidth={2} dot={false} />
              </LineChart>
            </ResponsiveContainer>
          </div>

          <div className="grid-2">
            <div className="card">
              <div className="card-header">Prediction Risk Distribution</div>
              <ResponsiveContainer width="100%" height={280}>
                <BarChart data={riskBarData} layout="vertical">
                  <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.1)" />
                  <XAxis type="number" tick={{ fontSize: 11 }} />
                  <YAxis dataKey="category" type="category" width={80} tick={{ fontSize: 12 }} />
                  <Tooltip />
                  <Bar dataKey="count" radius={[0, 4, 4, 0]}>
                    {riskBarData.map((entry, i) => <Cell key={i} fill={entry.fill} />)}
                  </Bar>
                </BarChart>
              </ResponsiveContainer>
            </div>
            <div className="card">
              <div className="card-header">Predictions by Severity</div>
              <ResponsiveContainer width="100%" height={280}>
                <PieChart>
                  <Pie data={severityPieData} dataKey="value" nameKey="name" cx="50%" cy="50%" outerRadius={90} label>
                    {severityPieData.map((entry, i) => <Cell key={i} fill={SEVERITY_COLORS[entry.name] || '#8884d8'} />)}
                  </Pie>
                  <Tooltip />
                  <Legend />
                </PieChart>
              </ResponsiveContainer>
            </div>
          </div>
        </div>
      )}

      {/* Model Performance */}
      {activeTab === 'model' && (
        <div>
          <div className="card" style={{ marginBottom: 24, overflowX: 'auto' }}>
            <div className="card-header">Model Performance — {CITIES.find(c => c.id === city)?.name || city}</div>
            <table className="data-table">
              <thead>
                <tr><th>City</th><th>Device</th><th>Accuracy %</th><th>Precision</th><th>Recall</th><th>F1</th><th>FPR</th><th>Latency (ms)</th><th>Trend</th></tr>
              </thead>
              <tbody>
                {modelPerf.map((m, i) => (
                  <tr key={i}>
                    <td>{m.city}</td><td>{m.device}</td>
                    <td style={{ color: accuracyColor(m.accuracy), fontWeight: 600 }}>{m.accuracy}%</td>
                    <td>{m.precision}</td><td>{m.recall}</td><td>{m.f1}</td><td>{m.fpr}%</td><td>{m.latency}</td>
                    <td><span className={`badge ${m.trend === 'improving' ? 'badge-success' : m.trend === 'stable' ? 'badge-info' : 'badge-high'}`}>{m.trend}</span></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <div className="card" style={{ marginBottom: 24 }}>
            <div className="card-header">Confusion Matrix</div>
            <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 12 }}>
              <label style={{ fontSize: 13, fontWeight: 600 }}>Device:</label>
              <select value={confDevice} onChange={(e) => setConfDevice(e.target.value)}
                style={{ padding: '4px 10px', borderRadius: 4, border: '1px solid var(--border)', fontSize: 12 }}>
                {selectedDevices.map((d) => <option key={d} value={d}>{d}</option>)}
              </select>
            </div>
            <div style={{ display: 'flex', gap: 32, alignItems: 'flex-start', flexWrap: 'wrap', padding: '12px 0' }}>
              <div style={{ flex: '0 0 auto' }}>
                <div style={{ fontSize: 12, fontWeight: 600, textAlign: 'center', marginBottom: 8, color: 'var(--text-secondary)' }}>Predicted</div>
                <div style={{ display: 'grid', gridTemplateColumns: '100px 120px 120px', gridTemplateRows: 'auto 80px 80px', gap: 4 }}>
                  <div /><div style={{ textAlign: 'center', fontWeight: 600, fontSize: 12, padding: 8 }}>Failure</div><div style={{ textAlign: 'center', fontWeight: 600, fontSize: 12, padding: 8 }}>No Failure</div>
                  <div style={{ display: 'flex', alignItems: 'center', fontWeight: 600, fontSize: 12 }}>Failure</div>
                  <div style={{ background: 'rgba(34,197,94,0.2)', borderRadius: 8, display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', border: '2px solid rgba(34,197,94,0.4)' }}><div style={{ fontSize: 24, fontWeight: 700, color: '#22c55e' }}>{confMatrix.tp}</div><div style={{ fontSize: 10, color: 'var(--text-secondary)' }}>TP</div></div>
                  <div style={{ background: 'rgba(239,68,68,0.15)', borderRadius: 8, display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', border: '2px solid rgba(239,68,68,0.3)' }}><div style={{ fontSize: 24, fontWeight: 700, color: '#ef4444' }}>{confMatrix.fn}</div><div style={{ fontSize: 10, color: 'var(--text-secondary)' }}>FN</div></div>
                  <div style={{ display: 'flex', alignItems: 'center', fontWeight: 600, fontSize: 12 }}>No Failure</div>
                  <div style={{ background: 'rgba(245,158,11,0.15)', borderRadius: 8, display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', border: '2px solid rgba(245,158,11,0.3)' }}><div style={{ fontSize: 24, fontWeight: 700, color: '#f59e0b' }}>{confMatrix.fp}</div><div style={{ fontSize: 10, color: 'var(--text-secondary)' }}>FP</div></div>
                  <div style={{ background: 'rgba(34,197,94,0.2)', borderRadius: 8, display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', border: '2px solid rgba(34,197,94,0.4)' }}><div style={{ fontSize: 24, fontWeight: 700, color: '#22c55e' }}>{confMatrix.tn}</div><div style={{ fontSize: 10, color: 'var(--text-secondary)' }}>TN</div></div>
                </div>
              </div>
              <div style={{ flex: 1, minWidth: 240 }}>
                <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 12, color: 'var(--text-secondary)' }}>Derived Metrics</div>
                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 12 }}>
                  {[
                    { label: 'Accuracy', value: `${confMetrics.accuracy}%`, color: '#22c55e' },
                    { label: 'Precision', value: `${confMetrics.precision}%`, color: '#3b82f6' },
                    { label: 'Recall', value: `${confMetrics.recall}%`, color: '#8b5cf6' },
                    { label: 'F1 Score', value: `${confMetrics.f1}%`, color: '#f59e0b' },
                    { label: 'FP Rate', value: `${confMetrics.fpr}%`, color: '#ef4444' },
                    { label: 'Specificity', value: `${confMetrics.specificity}%`, color: '#10b981' },
                  ].map((m) => (
                    <div key={m.label} style={{ padding: 12, background: 'var(--bg)', borderRadius: 8, textAlign: 'center' }}>
                      <div style={{ fontSize: 20, fontWeight: 700, color: m.color }}>{m.value}</div>
                      <div style={{ fontSize: 11, color: 'var(--text-secondary)', marginTop: 2 }}>{m.label}</div>
                    </div>
                  ))}
                </div>
              </div>
            </div>
          </div>

          <div className="grid-2" style={{ marginBottom: 24 }}>
            <div className="card">
              <div className="card-header">Precision-Recall Curve</div>
              <ResponsiveContainer width="100%" height={280}>
                <LineChart data={precisionRecallCurve}>
                  <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.1)" />
                  <XAxis dataKey="recall" tick={{ fontSize: 11 }} label={{ value: 'Recall', position: 'insideBottom', offset: -5 }} reversed />
                  <YAxis domain={[0.5, 1]} tick={{ fontSize: 11 }} label={{ value: 'Precision', angle: -90, position: 'insideLeft' }} />
                  <Tooltip />
                  <Line type="monotone" dataKey="precision" stroke="#6366f1" strokeWidth={2} dot={false} name="Precision" />
                </LineChart>
              </ResponsiveContainer>
            </div>
            <div className="card">
              <div className="card-header">FP / FN Trends (30-Day)</div>
              <ResponsiveContainer width="100%" height={280}>
                <ComposedChart data={fpFnTrend}>
                  <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.1)" />
                  <XAxis dataKey="date" tick={{ fontSize: 10 }} interval={4} />
                  <YAxis tick={{ fontSize: 11 }} />
                  <Tooltip />
                  <Legend />
                  <Bar dataKey="false_positives" fill="#f59e0b" name="False Positives" opacity={0.7} radius={[2, 2, 0, 0]} />
                  <Bar dataKey="false_negatives" fill="#ef4444" name="False Negatives" opacity={0.7} radius={[2, 2, 0, 0]} />
                  <Line type="monotone" dataKey="fp_rate" stroke="#f59e0b" strokeWidth={2} dot={false} name="FP Rate %" />
                  <Line type="monotone" dataKey="fn_rate" stroke="#ef4444" strokeWidth={2} dot={false} name="FN Rate %" />
                </ComposedChart>
              </ResponsiveContainer>
            </div>
          </div>

          <div className="card">
            <div className="card-header">Models by Device Type</div>
            <ResponsiveContainer width="100%" height={280}>
              <PieChart>
                <Pie data={devicePieData} dataKey="value" nameKey="name" cx="50%" cy="50%" outerRadius={90} label>
                  {devicePieData.map((entry, i) => <Cell key={i} fill={DEVICE_COLORS[entry.name] || '#8884d8'} />)}
                </Pie>
                <Tooltip />
                <Legend />
              </PieChart>
            </ResponsiveContainer>
          </div>
        </div>
      )}

      {/* Causation Analysis */}
      {activeTab === 'causation' && (
        <div>
          <div className="card" style={{ marginBottom: 16, padding: '12px 20px', display: 'flex', alignItems: 'center', gap: 16 }}>
            <label style={{ fontSize: 13, fontWeight: 600 }}>Device:</label>
            <select value={causalDevice} onChange={(e) => setCausalDevice(e.target.value)}
              style={{ padding: '4px 10px', borderRadius: 4, border: '1px solid var(--border)', fontSize: 13 }}>
              {selectedDevices.map((d) => <option key={d} value={d}>{d}</option>)}
            </select>
          </div>

          <div className="card" style={{ marginBottom: 24 }}>
            <div className="card-header">Top 20 Feature Importance (SHAP) — {city} / {causalDevice}</div>
            <ResponsiveContainer width="100%" height={500}>
              <BarChart data={featureData} layout="vertical" margin={{ left: 140 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.1)" />
                <XAxis type="number" tick={{ fontSize: 11 }} />
                <YAxis dataKey="feature" type="category" width={130} tick={{ fontSize: 11 }} />
                <Tooltip formatter={(val, name) => [val.toFixed(4), name === 'importance' ? 'Importance' : 'SHAP Value']} />
                <Bar dataKey="importance" radius={[0, 4, 4, 0]}>
                  {featureData.map((entry, i) => <Cell key={i} fill={entry.shap_value >= 0 ? '#ef4444' : '#3b82f6'} />)}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
            <div style={{ padding: '8px 16px', fontSize: 11, color: 'var(--text-secondary)', display: 'flex', gap: 16 }}>
              <span><span style={{ display: 'inline-block', width: 10, height: 10, background: '#ef4444', borderRadius: 2, marginRight: 4 }} />Positive SHAP</span>
              <span><span style={{ display: 'inline-block', width: 10, height: 10, background: '#3b82f6', borderRadius: 2, marginRight: 4 }} />Negative SHAP</span>
            </div>
          </div>

          <div className="card" style={{ marginBottom: 24 }}>
            <div className="card-header">Feature Interaction Heatmap (Top 8)</div>
            <div style={{ overflowX: 'auto', padding: 16 }}>
              <div style={{ display: 'grid', gridTemplateColumns: `120px repeat(${correlationMatrix.features.length}, 60px)`, gap: 2, fontSize: 11 }}>
                <div />
                {correlationMatrix.features.map((f) => (
                  <div key={f} style={{ textAlign: 'center', fontWeight: 600, fontSize: 9, wordBreak: 'break-all', padding: 4 }}>
                    {f.replace(/_/g, ' ').slice(0, 12)}
                  </div>
                ))}
                {correlationMatrix.features.map((f1, i) => (
                  <React.Fragment key={f1}>
                    <div style={{ fontWeight: 600, fontSize: 10, display: 'flex', alignItems: 'center', paddingRight: 8 }}>
                      {f1.replace(/_/g, ' ').slice(0, 18)}
                    </div>
                    {correlationMatrix.matrix[i].map((val, j) => (
                      <div key={j} style={{ background: correlationColor(val), width: 56, height: 36, display: 'flex', alignItems: 'center', justifyContent: 'center', borderRadius: 3, fontWeight: i === j ? 700 : 400, fontSize: 11 }}>
                        {val.toFixed(2)}
                      </div>
                    ))}
                  </React.Fragment>
                ))}
              </div>
            </div>
          </div>

          <div className="card" style={{ marginBottom: 24 }}>
            <div className="card-header">Contributing Factor Breakdown by Failure Type</div>
            <div style={{ overflowX: 'auto' }}>
              <table className="data-table">
                <thead><tr><th>Failure Type</th><th>Temperature</th><th>Humidity</th><th>Age</th><th>Usage</th><th>Firmware</th><th>Vibration</th></tr></thead>
                <tbody>
                  {failureTypeBreakdown.map((row, i) => (
                    <tr key={i}>
                      <td style={{ fontWeight: 600 }}>{row.failure_type}</td>
                      {['Temperature', 'Humidity', 'Age', 'Usage', 'Firmware', 'Vibration'].map((f) => (
                        <td key={f}>
                          <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                            <div style={{ width: 60, height: 6, background: 'var(--border)', borderRadius: 3, overflow: 'hidden' }}>
                              <div style={{ width: `${row[f] * 100}%`, height: '100%', background: row[f] > 0.5 ? '#ef4444' : row[f] > 0.3 ? '#f59e0b' : '#22c55e', borderRadius: 3 }} />
                            </div>
                            <span style={{ fontSize: 11 }}>{row[f].toFixed(2)}</span>
                          </div>
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>

          <div className="card">
            <div className="card-header">Time-Series Feature Importance — SHAP Value Drift (12-Month)</div>
            <ResponsiveContainer width="100%" height={350}>
              <LineChart data={FEATURE_IMPORTANCE_OVER_TIME}>
                <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.1)" />
                <XAxis dataKey="month" tick={{ fontSize: 12 }} />
                <YAxis domain={[0, 0.4]} tick={{ fontSize: 11 }} label={{ value: 'SHAP Importance', angle: -90, position: 'insideLeft' }} />
                <Tooltip formatter={(val) => [val.toFixed(3), 'Importance']} />
                <Legend />
                {Object.entries(FEATURE_IMPORTANCE_COLORS).map(([feature, color]) => (
                  <Line key={feature} type="monotone" dataKey={feature} stroke={color} strokeWidth={2} dot={{ r: 3, fill: color }} name={feature} />
                ))}
              </LineChart>
            </ResponsiveContainer>
            <div style={{ padding: '8px 16px', fontSize: 11, color: 'var(--text-secondary)' }}>
              Tracks how the top 5 SHAP feature importance values drift over a 12-month period.
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
