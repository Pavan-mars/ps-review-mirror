import React, { useState, useMemo, useEffect } from 'react';
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
  getPS1MockPredictions,
  getPS1MockModelPerf,
  getPS1MockRiskTrend,
  getPS1MockFeatureImportance,
  getPS1MockStationSummary,
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

const API_BASE = import.meta.env.VITE_API_BASE_URL ?? 'https://a9yuqt9j9b.execute-api.us-east-1.amazonaws.com';

function probSeverity(prob, threshold) {
  const ratio = parseFloat(prob) / parseFloat(threshold);
  if (ratio >= 1.8) return 'Critical';
  if (ratio >= 1.3) return 'High';
  if (ratio >= 1.0) return 'Medium';
  if (ratio >= 0.5) return 'Low';
  return 'Info';
}

const SUB_TABS = [
  { key: 'performance', label: 'Executive Summary' },
  { key: 'model', label: 'Model Performance' },
  { key: 'causation', label: 'Causation Analysis' },
];

// PS1 only has TVM and GATE models — exclude Validators from all PS1 selectors
const PS1_DEVICES = ['TVMs', 'Gates'];

export default function PS1FailurePredictionTab({ city, selectedDevices }) {
  const selectedCities = useMemo(() => [city], [city]);
  const ps1Devices = useMemo(() => selectedDevices.filter((d) => PS1_DEVICES.includes(d)), [selectedDevices]);
  const [activeTab, setActiveTab] = useState('performance');
  const [causalDevice, setCausalDevice] = useState(() => ps1Devices[0] || 'TVMs');
  const [confDevice, setConfDevice] = useState(() => ps1Devices[0] || 'TVMs');

  // --- Live API state (seeded with mock so tab is never blank without an API) ---
  const [livePredictions, setLivePredictions] = useState(getPS1MockPredictions);
  const [liveExplainability, setLiveExplainability] = useState([]);
  const [liveLoading, setLiveLoading] = useState(false);
  const [liveModelPerf, setLiveModelPerf] = useState(getPS1MockModelPerf);
  const [liveModelPerfLoading, setLiveModelPerfLoading] = useState(false);
  const [liveRiskTrend, setLiveRiskTrend] = useState(getPS1MockRiskTrend);
  const [liveFeatImpTVM, setLiveFeatImpTVM] = useState(() => getPS1MockFeatureImportance('TVM'));
  const [liveFeatImpGATE, setLiveFeatImpGATE] = useState(() => getPS1MockFeatureImportance('GATE'));
  const [featImpLoading, setFeatImpLoading] = useState(false);
  const [liveStationSummary, setLiveStationSummary] = useState(getPS1MockStationSummary);
  const [stationLoading, setStationLoading] = useState(false);

  useEffect(() => {
    setLiveLoading(true);
    fetch(`${API_BASE}/ps1/predictions`)
      .then((r) => r.json())
      .then((data) => { setLivePredictions(Array.isArray(data) ? data : []); setLiveLoading(false); })
      .catch(() => setLiveLoading(false));
  }, []);

  useEffect(() => {
    fetch(`${API_BASE}/ps1/model-performance`)
      .then((r) => r.json())
      .then((data) => { setLiveModelPerf(Array.isArray(data) ? data : []); setLiveModelPerfLoading(false); })
      .catch(() => setLiveModelPerfLoading(false));
  }, []);

  useEffect(() => {
    fetch(`${API_BASE}/ps1/risk-trend`)
      .then((r) => r.json())
      .then((data) => setLiveRiskTrend(Array.isArray(data) ? data : []))
      .catch(() => {});
  }, []);

  useEffect(() => {
    Promise.all([
      fetch(`${API_BASE}/ps1/feature-importance?device_category=TVM`).then((r) => r.json()),
      fetch(`${API_BASE}/ps1/feature-importance?device_category=GATE`).then((r) => r.json()),
    ]).then(([tvm, gate]) => {
      setLiveFeatImpTVM(Array.isArray(tvm) ? tvm : []);
      setLiveFeatImpGATE(Array.isArray(gate) ? gate : []);
      setFeatImpLoading(false);
    }).catch(() => setFeatImpLoading(false));
  }, []);

  useEffect(() => {
    fetch(`${API_BASE}/ps1/station-summary`)
      .then((r) => r.json())
      .then((data) => { setLiveStationSummary(Array.isArray(data) ? data : []); setStationLoading(false); })
      .catch(() => setStationLoading(false));
  }, []);

  useEffect(() => {
    if (!livePredictions.length) return;
    const catMap = { TVMs: 'TVM', Gates: 'GATE', Validators: 'VALIDATOR' };
    const cat = catMap[causalDevice];
    const match = livePredictions.find((p) => p.device_category === cat) || livePredictions[0];
    fetch(`${API_BASE}/ps1/explainability?prediction_id=${match.prediction_id}`)
      .then((r) => r.json())
      .then((data) => {
        const unique = Array.isArray(data)
          ? data.filter((v, i, a) => a.findIndex((x) => x.feature_name === v.feature_name) === i)
          : [];
        setLiveExplainability(unique);
      })
      .catch(() => setLiveExplainability([]));
  }, [livePredictions, causalDevice]);

  // --- Live KPIs ---
  const liveKPIs = useMemo(() => {
    if (!livePredictions.length) return { total: 0, failures: 0, avgProb: 0, lastRun: '—' };
    const failures = livePredictions.filter((p) => p.predicted_label).length;
    const avgProb = livePredictions.reduce((s, p) => s + parseFloat(p.failure_probability), 0) / livePredictions.length;
    const lastRun = livePredictions[0]?.inference_ts?.split(' ')[0] || '—';
    return { total: livePredictions.length, failures, avgProb: (avgProb * 100).toFixed(1), lastRun };
  }, [livePredictions]);

  // Live train/val/test breakdown from S3 (via SageMaker)
  const liveTrainValTestData = useMemo(() => {
    const splits = [
      { key: 'train', label: 'Train' },
      { key: 'val',   label: 'Validation' },
      { key: 'test',  label: 'Test' },
    ];
    return splits.map(({ key, label }) => {
      const point = { split: label };
      liveModelPerf.forEach((m) => {
        if (m.s3_metrics) {
          point[`${m.device_category}_auc`] = parseFloat(m.s3_metrics[`${key}_auc`] || 0);
          point[`${m.device_category}_ap`]  = parseFloat(m.s3_metrics[`${key}_ap`]  || 0);
          point[`${m.device_category}_f1`]  = parseFloat(m.s3_metrics[`${key}_f1`]  || 0);
        }
      });
      return point;
    });
  }, [liveModelPerf]);

  // Live SHAP data mapped to chart format
  const liveShapData = useMemo(() =>
    liveExplainability.map((f) => ({
      feature: f.feature_name,
      importance: Math.abs(parseFloat(f.shap_value)),
      shap_value: parseFloat(f.shap_value),
      feature_value: f.feature_value,
    })).sort((a, b) => b.importance - a.importance),
  [liveExplainability]);

  // Risk trend chart: pivot rows by date → { date, TVM_prob, GATE_prob, TVM_fail, GATE_fail }
  const riskTrendChart = useMemo(() => {
    const byDate = {};
    liveRiskTrend.forEach((row) => {
      const d = row.date;
      if (!byDate[d]) byDate[d] = { date: d };
      byDate[d][`${row.device_category}_prob`] = parseFloat(row.avg_prob_pct);
      byDate[d][`${row.device_category}_fail`] = parseInt(row.failures, 10);
      byDate[d][`${row.device_category}_total`] = parseInt(row.total, 10);
    });
    return Object.values(byDate).sort((a, b) => a.date.localeCompare(b.date));
  }, [liveRiskTrend]);

  // Feature comparison: merge TVM + GATE importance into top-15 union, sorted by max(TVM, GATE)
  const featCompChart = useMemo(() => {
    const all = {};
    liveFeatImpTVM.forEach((f) => {
      if (!all[f.feature_name]) all[f.feature_name] = { feature: f.feature_name };
      all[f.feature_name].TVM = parseFloat(f.avg_importance);
    });
    liveFeatImpGATE.forEach((f) => {
      if (!all[f.feature_name]) all[f.feature_name] = { feature: f.feature_name };
      all[f.feature_name].GATE = parseFloat(f.avg_importance);
    });
    return Object.values(all)
      .sort((a, b) => Math.max(b.TVM || 0, b.GATE || 0) - Math.max(a.TVM || 0, a.GATE || 0))
      .slice(0, 15);
  }, [liveFeatImpTVM, liveFeatImpGATE]);

  // Aggregate SHAP for the currently selected device (Causation tab)
  const liveAggShap = useMemo(() => {
    const src = causalDevice === 'Gates' ? liveFeatImpGATE : liveFeatImpTVM;
    return src.map((f) => ({
      feature: f.feature_name,
      importance: parseFloat(f.avg_importance),
      shap_value: parseFloat(f.avg_shap),
    }));
  }, [causalDevice, liveFeatImpTVM, liveFeatImpGATE]);

  // S3 test metrics as chart rows (for classifier metrics bar chart)
  const testMetricsChart = useMemo(() =>
    liveModelPerf.map((m) => ({
      category: m.device_category,
      AUC: parseFloat(m.s3_metrics?.test_auc || 0),
      Precision: parseFloat(m.s3_metrics?.test_prec || 0),
      Recall: parseFloat(m.s3_metrics?.test_rec || 0),
      F1: parseFloat(m.s3_metrics?.test_f1 || 0),
      Accuracy: parseFloat(m.s3_metrics?.test_acc || 0),
    })),
  [liveModelPerf]);

  // Top 5 stations by predicted failures (for bar chart and table)
  const top5Stations = useMemo(() => {
    const byStation = {};
    liveStationSummary.forEach((row) => {
      const key = row.facility_id;
      if (!byStation[key]) byStation[key] = { facility_id: key, total_devices: 0, predicted_failures: 0, critical_count: 0, high_count: 0, medium_count: 0, last_inference_date: row.last_inference_date, categories: [] };
      byStation[key].total_devices += parseInt(row.total_devices, 10);
      byStation[key].predicted_failures += parseInt(row.predicted_failures, 10);
      byStation[key].critical_count += parseInt(row.critical_count || 0, 10);
      byStation[key].high_count += parseInt(row.high_count || 0, 10);
      byStation[key].medium_count += parseInt(row.medium_count || 0, 10);
      byStation[key].avg_risk_pct = Math.max(byStation[key].avg_risk_pct || 0, parseFloat(row.avg_risk_pct));
      byStation[key].categories.push(row.device_category);
    });
    return Object.values(byStation)
      .sort((a, b) => b.predicted_failures - a.predicted_failures || b.avg_risk_pct - a.avg_risk_pct)
      .slice(0, 5);
  }, [liveStationSummary]);

  // Hardware failure breakdown: TVM vs GATE predicted failures + severity breakdown
  const hardwareBreakdown = useMemo(() => {
    const byCategory = {};
    liveStationSummary.forEach((row) => {
      const cat = row.device_category;
      if (!byCategory[cat]) byCategory[cat] = { category: cat, total: 0, failures: 0, critical: 0, high: 0, medium: 0, avg_risk: 0, count: 0 };
      byCategory[cat].total += parseInt(row.total_devices, 10);
      byCategory[cat].failures += parseInt(row.predicted_failures, 10);
      byCategory[cat].critical += parseInt(row.critical_count || 0, 10);
      byCategory[cat].high += parseInt(row.high_count || 0, 10);
      byCategory[cat].medium += parseInt(row.medium_count || 0, 10);
      byCategory[cat].avg_risk += parseFloat(row.avg_risk_pct);
      byCategory[cat].count += 1;
    });
    return Object.values(byCategory).map((c) => ({ ...c, avg_risk: c.count ? Math.round(c.avg_risk / c.count * 100) / 100 : 0 }));
  }, [liveStationSummary]);

  // --- Mock Data (kept for sections not yet replaced) ---
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

  // --- Live-computed distributions (from real predictions) ---
  const riskBarData = useMemo(() => {
    const counts = { Critical: 0, High: 0, Medium: 0, Low: 0, Info: 0 };
    livePredictions.forEach((p) => { const s = probSeverity(p.failure_probability, p.decision_threshold); counts[s] = (counts[s] || 0) + 1; });
    return Object.entries(counts).map(([category, count]) => ({ category, count, fill: SEVERITY_COLORS[category] }));
  }, [livePredictions]);

  const severityPieData = useMemo(() => {
    const counts = { Critical: 0, High: 0, Medium: 0, Low: 0, Info: 0 };
    livePredictions.forEach((p) => { const s = probSeverity(p.failure_probability, p.decision_threshold); counts[s] = (counts[s] || 0) + 1; });
    return Object.entries(counts).map(([name, value]) => ({ name, value })).filter((d) => d.value > 0);
  }, [livePredictions]);

  // Use real test_auc from model registry (average TVM + GATE)
  const avgAccuracy = useMemo(() => {
    if (liveModelPerf.length) {
      const avg = liveModelPerf.reduce((s, m) => s + parseFloat(m.test_auc), 0) / liveModelPerf.length;
      return Math.round(avg * 10000) / 100;
    }
    return 0;
  }, [liveModelPerf]);

  const devicePieData = useMemo(() =>
    liveModelPerf.map((m) => ({ name: m.device_category, value: 1 })),
  [liveModelPerf]);

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
          {/* Live data banner */}
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 16, padding: '8px 14px', background: 'rgba(34,197,94,0.08)', border: '1px solid rgba(34,197,94,0.25)', borderRadius: 8 }}>
            <span style={{ width: 8, height: 8, borderRadius: '50%', background: liveLoading ? '#f59e0b' : '#22c55e', display: 'inline-block' }} />
            <span style={{ fontSize: 12, fontWeight: 600, color: 'var(--text-secondary)' }}>
              {liveLoading ? 'Loading live predictions…' : `LIVE — SageMaker PS1 endpoint · Last run: ${liveKPIs.lastRun}`}
            </span>
          </div>

          <div className="grid-4" style={{ marginBottom: 24 }}>
            <div className="card">
              <div className="card-header">Total Predictions</div>
              <div className="kpi-value">{liveLoading ? '…' : liveKPIs.total}</div>
              <div className="kpi-label">Live from RDS</div>
              <div className="kpi-trend" style={{ color: '#3b82f6' }}>TVM + GATE</div>
            </div>
            <div className="card">
              <div className="card-header">Failures Predicted</div>
              <div className="kpi-value" style={{ color: liveKPIs.failures > 0 ? '#ef4444' : '#22c55e' }}>
                {liveLoading ? '…' : liveKPIs.failures}
              </div>
              <div className="kpi-label">Above decision threshold</div>
              <div className="kpi-trend" style={{ color: liveKPIs.failures > 0 ? '#ef4444' : '#22c55e' }}>
                {liveKPIs.failures > 0 ? 'Requires attention' : 'All clear'}
              </div>
            </div>
            <div className="card">
              <div className="card-header">Avg Failure Probability</div>
              <div className="kpi-value">{liveLoading ? '…' : `${liveKPIs.avgProb}%`}</div>
              <div className="kpi-label">Across all devices</div>
              <div className="kpi-trend" style={{ color: parseFloat(liveKPIs.avgProb) > 40 ? '#f59e0b' : '#22c55e' }}>
                {parseFloat(liveKPIs.avgProb) > 40 ? 'Elevated' : 'Normal'}
              </div>
            </div>
            <div className="card">
              <div className="card-header">Avg AUC-ROC</div>
              <div className="kpi-value">{liveModelPerfLoading ? '…' : avgAccuracy}</div>
              <div className="kpi-label">TVM + GATE champion models</div>
              <div className="kpi-trend" style={{ color: avgAccuracy >= 70 ? '#22c55e' : '#f59e0b' }}>
                {liveModelPerfLoading ? '—' : avgAccuracy >= 70 ? 'Good discrimination' : 'Moderate'}
              </div>
            </div>
          </div>

          {/* Live predictions table */}
          {!liveLoading && livePredictions.length > 0 && (
            <div className="card" style={{ marginBottom: 24, overflowX: 'auto' }}>
              <div className="card-header">Live Predictions — SageMaker PS1 Output</div>
              <table className="data-table">
                <thead>
                  <tr><th>Device ID</th><th>Category</th><th>Facility</th><th>Prediction Date</th><th>Failure Probability</th><th>Threshold</th><th>Severity</th><th>Label</th><th>Inference Time</th></tr>
                </thead>
                <tbody>
                  {livePredictions.map((p) => {
                    const sev = probSeverity(p.failure_probability, p.decision_threshold);
                    const sevColor = { Critical: '#ef4444', High: '#f97316', Medium: '#f59e0b', Low: '#3b82f6', Info: '#6b7280' }[sev];
                    return (
                      <tr key={p.prediction_id}>
                        <td style={{ fontFamily: 'monospace', fontSize: 12 }}>{p.device_id}</td>
                        <td><span className="badge badge-info">{p.device_category}</span></td>
                        <td style={{ fontSize: 12 }}>{p.facility_id || '—'}</td>
                        <td style={{ fontSize: 12 }}>{p.prediction_date}</td>
                        <td style={{ fontWeight: 700, color: sevColor }}>{(parseFloat(p.failure_probability) * 100).toFixed(1)}%</td>
                        <td style={{ fontSize: 12, color: 'var(--text-secondary)' }}>{(parseFloat(p.decision_threshold) * 100).toFixed(1)}%</td>
                        <td><span style={{ color: sevColor, fontWeight: 600, fontSize: 12 }}>{sev}</span></td>
                        <td><span className={`badge ${p.predicted_label ? 'badge-critical' : 'badge-success'}`}>{p.predicted_label ? 'FAIL' : 'OK'}</span></td>
                        <td style={{ fontSize: 11, color: 'var(--text-secondary)' }}>{p.inference_ts}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}

          {/* ── Station-Wise Device Severity ── */}
          {!stationLoading && liveStationSummary.length > 0 && (
            <>
              <div className="grid-2" style={{ marginBottom: 24 }}>
                {/* Top 5 stations bar chart */}
                <div className="card">
                  <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 12 }}>
                    <div className="card-header" style={{ marginBottom: 0 }}>Top 5 Stations — Predicted Failures</div>
                    <span style={{ fontSize: 11, padding: '2px 8px', borderRadius: 10, background: 'rgba(34,197,94,0.12)', color: '#22c55e', fontWeight: 600 }}>LIVE</span>
                  </div>
                  <ResponsiveContainer width="100%" height={220}>
                    <BarChart data={top5Stations} layout="vertical" margin={{ left: 80 }}>
                      <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.1)" />
                      <XAxis type="number" tick={{ fontSize: 11 }} />
                      <YAxis dataKey="facility_id" type="category" width={75} tick={{ fontSize: 11 }} />
                      <Tooltip />
                      <Legend />
                      <Bar dataKey="critical_count" name="Critical" stackId="a" fill="#ef4444" radius={[0, 0, 0, 0]} />
                      <Bar dataKey="high_count" name="High" stackId="a" fill="#f97316" />
                      <Bar dataKey="medium_count" name="Medium" stackId="a" fill="#f59e0b" radius={[0, 3, 3, 0]} />
                    </BarChart>
                  </ResponsiveContainer>
                </div>

                {/* Hardware failure breakdown by device category */}
                <div className="card">
                  <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 12 }}>
                    <div className="card-header" style={{ marginBottom: 0 }}>Hardware Failure by Device Category</div>
                    <span style={{ fontSize: 11, padding: '2px 8px', borderRadius: 10, background: 'rgba(34,197,94,0.12)', color: '#22c55e', fontWeight: 600 }}>LIVE</span>
                  </div>
                  <ResponsiveContainer width="100%" height={220}>
                    <BarChart data={hardwareBreakdown}>
                      <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.1)" />
                      <XAxis dataKey="category" tick={{ fontSize: 13, fontWeight: 600 }} />
                      <YAxis tick={{ fontSize: 11 }} />
                      <Tooltip />
                      <Legend />
                      <Bar dataKey="critical" name="Critical" stackId="a" fill="#ef4444" radius={[0, 0, 0, 0]} />
                      <Bar dataKey="high" name="High" stackId="a" fill="#f97316" />
                      <Bar dataKey="medium" name="Medium" stackId="a" fill="#f59e0b" radius={[3, 3, 0, 0]} />
                    </BarChart>
                  </ResponsiveContainer>
                </div>
              </div>

              {/* Top 5 stations detail table */}
              <div className="card" style={{ marginBottom: 24, overflowX: 'auto' }}>
                <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 12 }}>
                  <div className="card-header" style={{ marginBottom: 0 }}>Station Severity Detail — Top 5</div>
                  <span style={{ fontSize: 11, padding: '2px 8px', borderRadius: 10, background: 'rgba(34,197,94,0.12)', color: '#22c55e', fontWeight: 600 }}>LIVE</span>
                </div>
                <table className="data-table">
                  <thead>
                    <tr><th>#</th><th>Station ID</th><th>Device Types</th><th>Total Devices</th><th>Failures</th><th style={{ color: '#ef4444' }}>Critical</th><th style={{ color: '#f97316' }}>High</th><th style={{ color: '#f59e0b' }}>Medium</th><th>Avg Risk</th><th>Last Run</th></tr>
                  </thead>
                  <tbody>
                    {top5Stations.map((s, i) => {
                      const sevColor = s.critical_count > 0 ? '#ef4444' : s.high_count > 0 ? '#f97316' : '#f59e0b';
                      return (
                        <tr key={s.facility_id}>
                          <td style={{ fontWeight: 700, color: sevColor }}>{i + 1}</td>
                          <td style={{ fontFamily: 'monospace', fontWeight: 600 }}>{s.facility_id}</td>
                          <td style={{ fontSize: 11 }}>{s.categories.join(' + ')}</td>
                          <td>{s.total_devices}</td>
                          <td style={{ fontWeight: 700, color: s.predicted_failures > 0 ? '#ef4444' : '#22c55e' }}>{s.predicted_failures}</td>
                          <td style={{ fontWeight: 700, color: '#ef4444' }}>{s.critical_count || 0}</td>
                          <td style={{ fontWeight: 600, color: '#f97316' }}>{s.high_count || 0}</td>
                          <td style={{ color: '#f59e0b' }}>{s.medium_count || 0}</td>
                          <td style={{ fontVariantNumeric: 'tabular-nums' }}>{s.avg_risk_pct}%</td>
                          <td style={{ fontSize: 11, color: 'var(--text-secondary)' }}>{s.last_inference_date || '—'}</td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            </>
          )}

          <div className="card" style={{ marginBottom: 24 }}>
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 12 }}>
              <div className="card-header" style={{ marginBottom: 0 }}>Predicted Failure Risk Trend — {CITIES.find(c => c.id === city)?.name || city}</div>
              <span style={{ fontSize: 11, padding: '2px 8px', borderRadius: 10, background: 'rgba(34,197,94,0.12)', color: '#22c55e', fontWeight: 600 }}>LIVE</span>
            </div>
            {riskTrendChart.length > 0 ? (
              <ResponsiveContainer width="100%" height={300}>
                <ComposedChart data={riskTrendChart}>
                  <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.1)" />
                  <XAxis dataKey="date" tick={{ fontSize: 11 }} interval="preserveStartEnd" />
                  <YAxis yAxisId="prob" domain={[0, 100]} tick={{ fontSize: 11 }} unit="%" label={{ value: 'Avg Risk %', angle: -90, position: 'insideLeft', fontSize: 11 }} />
                  <YAxis yAxisId="count" orientation="right" tick={{ fontSize: 11 }} label={{ value: 'Failures Flagged', angle: 90, position: 'insideRight', fontSize: 11 }} />
                  <Tooltip formatter={(val, name) => name.includes('prob') ? `${val}%` : val} />
                  <Legend />
                  <Bar yAxisId="count" dataKey="TVM_fail" name="TVM Failures Flagged" fill="#6366f1" opacity={0.4} radius={[2, 2, 0, 0]} />
                  <Bar yAxisId="count" dataKey="GATE_fail" name="GATE Failures Flagged" fill="#10b981" opacity={0.4} radius={[2, 2, 0, 0]} />
                  <Line yAxisId="prob" type="monotone" dataKey="TVM_prob" name="TVM Avg Risk %" stroke="#6366f1" strokeWidth={2} dot={false} />
                  <Line yAxisId="prob" type="monotone" dataKey="GATE_prob" name="GATE Avg Risk %" stroke="#10b981" strokeWidth={2} dot={false} />
                </ComposedChart>
              </ResponsiveContainer>
            ) : (
              <div style={{ height: 120, display: 'flex', alignItems: 'center', justifyContent: 'center', color: 'var(--text-secondary)', fontSize: 13 }}>
                No trend data yet — run more inference batches to populate.
              </div>
            )}
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
          {/* Live model registry banner */}
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 16, padding: '8px 14px', background: 'rgba(34,197,94,0.08)', border: '1px solid rgba(34,197,94,0.25)', borderRadius: 8 }}>
            <span style={{ width: 8, height: 8, borderRadius: '50%', background: liveModelPerfLoading ? '#f59e0b' : '#22c55e', display: 'inline-block' }} />
            <span style={{ fontSize: 12, fontWeight: 600, color: 'var(--text-secondary)' }}>
              {liveModelPerfLoading ? 'Loading live model data…' : `LIVE — SageMaker Model Registry + S3 metrics · ${liveModelPerf.length} PS1 champion models`}
            </span>
          </div>

          {/* Live model cards — one per champion model */}
          {!liveModelPerfLoading && liveModelPerf.length > 0 && (
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(340px, 1fr))', gap: 16, marginBottom: 24 }}>
              {liveModelPerf.map((m) => {
                const aucColor = parseFloat(m.test_auc) >= 0.75 ? '#22c55e' : parseFloat(m.test_auc) >= 0.60 ? '#f59e0b' : '#ef4444';
                const catColor = m.device_category === 'TVM' ? '#6366f1' : m.device_category === 'GATE' ? '#10b981' : '#f59e0b';
                const catPreds = livePredictions.filter((p) => p.device_category === m.device_category);
                const lastInference = catPreds.length
                  ? catPreds.reduce((a, b) => (a.inference_ts > b.inference_ts ? a : b)).inference_ts
                  : null;
                const lastInferenceDate = lastInference ? lastInference.split(' ')[0] : null;
                const inferenceCount = catPreds.length;
                const failCount = catPreds.filter((p) => p.predicted_label).length;
                return (
                  <div key={m.model_registry_id} className="card" style={{ border: `2px solid ${catColor}22`, position: 'relative', overflow: 'hidden' }}>
                    <div style={{ position: 'absolute', top: 0, left: 0, right: 0, height: 3, background: catColor }} />
                    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 16, paddingTop: 4 }}>
                      <div>
                        <span style={{ fontSize: 11, fontWeight: 700, color: catColor, textTransform: 'uppercase', letterSpacing: 0.5 }}>{m.device_category}</span>
                        <div style={{ fontSize: 14, fontWeight: 700, color: 'var(--text-primary)', marginTop: 2 }}>{m.model_name}</div>
                        <div style={{ fontSize: 11, color: 'var(--text-secondary)', marginTop: 2 }}>{m.algorithm} · v{m.model_version} · {m.registry_alias}</div>
                      </div>
                      <span style={{ padding: '3px 10px', borderRadius: 10, background: 'rgba(34,197,94,0.12)', color: '#22c55e', fontSize: 11, fontWeight: 700 }}>
                        {m.status}
                      </span>
                    </div>
                    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 10, marginBottom: 14 }}>
                      <div style={{ textAlign: 'center', padding: 10, background: 'var(--bg)', borderRadius: 8 }}>
                        <div style={{ fontSize: 22, fontWeight: 800, color: aucColor, fontVariantNumeric: 'tabular-nums' }}>{parseFloat(m.test_auc).toFixed(4)}</div>
                        <div style={{ fontSize: 10, color: 'var(--text-secondary)', marginTop: 2 }}>AUC-ROC</div>
                      </div>
                      <div style={{ textAlign: 'center', padding: 10, background: 'var(--bg)', borderRadius: 8 }}>
                        <div style={{ fontSize: 22, fontWeight: 800, color: '#3b82f6', fontVariantNumeric: 'tabular-nums' }}>{parseFloat(m.test_pr_auc).toFixed(4)}</div>
                        <div style={{ fontSize: 10, color: 'var(--text-secondary)', marginTop: 2 }}>PR-AUC</div>
                      </div>
                      <div style={{ textAlign: 'center', padding: 10, background: 'var(--bg)', borderRadius: 8 }}>
                        <div style={{ fontSize: 22, fontWeight: 800, color: '#f59e0b', fontVariantNumeric: 'tabular-nums' }}>{(parseFloat(m.decision_threshold) * 100).toFixed(1)}%</div>
                        <div style={{ fontSize: 10, color: 'var(--text-secondary)', marginTop: 2 }}>Decision Threshold</div>
                      </div>
                    </div>
                    {/* Live inference activity from predictions table */}
                    {lastInferenceDate && (
                      <div style={{ display: 'flex', gap: 8, marginBottom: 12 }}>
                        <div style={{ flex: 1, padding: '8px 10px', background: 'rgba(34,197,94,0.07)', borderRadius: 6, border: '1px solid rgba(34,197,94,0.2)' }}>
                          <div style={{ fontSize: 11, fontWeight: 700, color: '#22c55e' }}>{lastInferenceDate}</div>
                          <div style={{ fontSize: 10, color: 'var(--text-secondary)' }}>Last inference run</div>
                        </div>
                        <div style={{ flex: 1, padding: '8px 10px', background: 'var(--bg)', borderRadius: 6 }}>
                          <div style={{ fontSize: 11, fontWeight: 700, color: 'var(--text-primary)' }}>{inferenceCount} scored · {failCount} flagged</div>
                          <div style={{ fontSize: 10, color: 'var(--text-secondary)' }}>Devices in last run</div>
                        </div>
                      </div>
                    )}
                    <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8, fontSize: 11, color: 'var(--text-secondary)' }}>
                      <div><span style={{ fontWeight: 600 }}>SM deployed:</span> {m.deployed_at ? m.deployed_at.split(' ')[0] : '—'}</div>
                      <div><span style={{ fontWeight: 600 }}>Prediction head:</span> {m.prediction_head || '—'}</div>
                      <div><span style={{ fontWeight: 600 }}>MLflow version:</span> {m.mlflow_version || '—'}</div>
                      <div><span style={{ fontWeight: 600 }}>Features:</span> {m.n_features || '—'}</div>
                      {m.mlflow_run_id && (
                        <div style={{ gridColumn: '1 / -1', fontSize: 10, fontFamily: 'monospace', color: 'var(--text-secondary)', wordBreak: 'break-all' }}>
                          MLflow run: {m.mlflow_run_id}
                        </div>
                      )}
                    </div>
                  </div>
                );
              })}
            </div>
          )}

          {/* Live registry table */}
          {!liveModelPerfLoading && liveModelPerf.length > 0 && (
            <div className="card" style={{ marginBottom: 24, overflowX: 'auto' }}>
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 12 }}>
                <div className="card-header" style={{ marginBottom: 0 }}>ML Model Registry — PS1 Champions</div>
                <span style={{ fontSize: 11, padding: '2px 8px', borderRadius: 10, background: 'rgba(34,197,94,0.12)', color: '#22c55e', fontWeight: 600 }}>LIVE</span>
              </div>
              <table className="data-table">
                <thead>
                  <tr><th>Category</th><th>Algorithm</th><th>Version</th><th>Registry</th><th>AUC-ROC</th><th>PR-AUC</th><th>Threshold</th><th>Status</th><th>Deployed</th></tr>
                </thead>
                <tbody>
                  {liveModelPerf.map((m) => (
                    <tr key={m.model_registry_id}>
                      <td><span className="badge badge-info">{m.device_category}</span></td>
                      <td style={{ fontSize: 12 }}>{m.algorithm}</td>
                      <td>v{m.model_version}</td>
                      <td style={{ fontFamily: 'monospace', fontSize: 12 }}>{m.registry_alias}</td>
                      <td style={{ fontWeight: 700, color: parseFloat(m.test_auc) >= 0.75 ? '#22c55e' : '#f59e0b', fontVariantNumeric: 'tabular-nums' }}>{parseFloat(m.test_auc).toFixed(4)}</td>
                      <td style={{ fontVariantNumeric: 'tabular-nums' }}>{parseFloat(m.test_pr_auc).toFixed(4)}</td>
                      <td style={{ fontVariantNumeric: 'tabular-nums' }}>{(parseFloat(m.decision_threshold) * 100).toFixed(1)}%</td>
                      <td><span className="badge badge-success">{m.status}</span></td>
                      <td style={{ fontSize: 11, color: 'var(--text-secondary)' }}>{m.deployed_at ? m.deployed_at.split(' ')[0] : '—'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          {/* Live S3 Train / Validation / Test Metrics */}
          {liveTrainValTestData.some((d) => Object.keys(d).length > 1) && (
            <div className="card" style={{ marginBottom: 24 }}>
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 16 }}>
                <div className="card-header" style={{ marginBottom: 0 }}>Train / Validation / Test AUC — S3 Artifacts</div>
                <span style={{ fontSize: 11, padding: '2px 8px', borderRadius: 10, background: 'rgba(34,197,94,0.12)', color: '#22c55e', fontWeight: 600 }}>LIVE · S3</span>
              </div>
              <ResponsiveContainer width="100%" height={260}>
                <BarChart data={liveTrainValTestData} margin={{ top: 4, right: 20, bottom: 4, left: 4 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.1)" />
                  <XAxis dataKey="split" tick={{ fontSize: 13, fontWeight: 600 }} />
                  <YAxis domain={[0, 1.05]} tick={{ fontSize: 11 }} tickFormatter={(v) => v.toFixed(2)} />
                  <Tooltip formatter={(val) => val.toFixed(4)} />
                  <Legend />
                  {liveModelPerf.map((m) => {
                    const c = m.device_category === 'TVM' ? '#6366f1' : '#10b981';
                    return (
                      <Bar key={m.device_category} dataKey={`${m.device_category}_auc`}
                        name={`${m.device_category} AUC-ROC`} fill={c} radius={[3, 3, 0, 0]} maxBarSize={60} />
                    );
                  })}
                </BarChart>
              </ResponsiveContainer>
              {/* Full metrics table */}
              <div style={{ overflowX: 'auto', marginTop: 20 }}>
                <table className="data-table">
                  <thead>
                    <tr>
                      <th>Model</th><th>Split</th><th>AUC-ROC</th><th>PR-AUC</th><th>Accuracy</th><th>F1</th><th>Precision</th><th>Recall</th>
                    </tr>
                  </thead>
                  <tbody>
                    {liveModelPerf.map((m) => {
                      const s3 = m.s3_metrics || {};
                      const catColor = m.device_category === 'TVM' ? '#6366f1' : '#10b981';
                      return (['train', 'val', 'test'].map((split, si) => (
                        <tr key={`${m.device_category}-${split}`}>
                          {si === 0 && (
                            <td rowSpan={3} style={{ fontWeight: 700, color: catColor, verticalAlign: 'middle', textAlign: 'center' }}>
                              {m.device_category}<br />
                              <span style={{ fontWeight: 400, fontSize: 10, color: 'var(--text-secondary)' }}>v{m.model_version}</span>
                            </td>
                          )}
                          <td><span className={`badge ${split === 'test' ? 'badge-info' : ''}`} style={split !== 'test' ? { background: 'var(--bg)', color: 'var(--text-secondary)', border: '1px solid var(--border)', borderRadius: 8, padding: '2px 8px', fontSize: 11 } : {}}>{split === 'val' ? 'Validation' : split.charAt(0).toUpperCase() + split.slice(1)}</span></td>
                          <td style={{ fontWeight: split === 'test' ? 700 : 400, color: split === 'test' ? (parseFloat(s3[`${split}_auc`]) >= 0.75 ? '#22c55e' : '#f59e0b') : 'var(--text-primary)', fontVariantNumeric: 'tabular-nums' }}>{(s3[`${split}_auc`] || 0).toFixed(4)}</td>
                          <td style={{ fontVariantNumeric: 'tabular-nums' }}>{(s3[`${split}_ap`] || 0).toFixed(4)}</td>
                          <td style={{ fontVariantNumeric: 'tabular-nums' }}>{((s3[`${split}_acc`] || 0) * 100).toFixed(2)}%</td>
                          <td style={{ fontVariantNumeric: 'tabular-nums' }}>{(s3[`${split}_f1`] || 0).toFixed(4)}</td>
                          <td style={{ fontVariantNumeric: 'tabular-nums' }}>{(s3[`${split}_prec`] || 0).toFixed(4)}</td>
                          <td style={{ fontVariantNumeric: 'tabular-nums' }}>{(s3[`${split}_rec`] || 0).toFixed(4)}</td>
                        </tr>
                      )));
                    })}
                  </tbody>
                </table>
              </div>
            </div>
          )}

          {/* Live classifier metrics from S3 test run (replaces Confusion Matrix) */}
          {testMetricsChart.length > 0 && (
            <div className="card" style={{ marginBottom: 24 }}>
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 16 }}>
                <div className="card-header" style={{ marginBottom: 0 }}>Champion Model Test-Set Performance</div>
                <span style={{ fontSize: 11, padding: '2px 8px', borderRadius: 10, background: 'rgba(34,197,94,0.12)', color: '#22c55e', fontWeight: 600 }}>LIVE · S3</span>
              </div>
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(280px, 1fr))', gap: 16 }}>
                {testMetricsChart.map((m) => {
                  const catColor = m.category === 'TVM' ? '#6366f1' : '#10b981';
                  return (
                    <div key={m.category} style={{ padding: 16, background: 'var(--bg)', borderRadius: 10, border: `1px solid ${catColor}33` }}>
                      <div style={{ fontSize: 13, fontWeight: 700, color: catColor, marginBottom: 14 }}>{m.category} — Test Set Metrics</div>
                      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 10 }}>
                        {[
                          { label: 'AUC-ROC', value: m.AUC.toFixed(4), color: m.AUC >= 0.75 ? '#22c55e' : '#f59e0b' },
                          { label: 'Precision', value: m.Precision.toFixed(4), color: '#3b82f6' },
                          { label: 'Recall', value: m.Recall.toFixed(4), color: '#8b5cf6' },
                          { label: 'F1 Score', value: m.F1.toFixed(4), color: '#f59e0b' },
                          { label: 'Accuracy', value: `${(m.Accuracy * 100).toFixed(1)}%`, color: '#22c55e' },
                          { label: 'PR-AUC', value: liveModelPerf.find(x => x.device_category === m.category)?.test_pr_auc ? parseFloat(liveModelPerf.find(x => x.device_category === m.category).test_pr_auc).toFixed(4) : '—', color: '#ef4444' },
                        ].map((mt) => (
                          <div key={mt.label} style={{ textAlign: 'center', padding: '10px 6px', background: 'var(--surface)', borderRadius: 7 }}>
                            <div style={{ fontSize: 18, fontWeight: 800, color: mt.color, fontVariantNumeric: 'tabular-nums' }}>{mt.value}</div>
                            <div style={{ fontSize: 10, color: 'var(--text-secondary)', marginTop: 2 }}>{mt.label}</div>
                          </div>
                        ))}
                      </div>
                    </div>
                  );
                })}
              </div>
            </div>
          )}

          {/* Live classifier metrics bar chart (replaces PR Curve) + Daily failure flag trend (replaces FP/FN) */}
          <div className="grid-2" style={{ marginBottom: 24 }}>
            <div className="card">
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 12 }}>
                <div className="card-header" style={{ marginBottom: 0 }}>Test Precision / Recall / F1</div>
                <span style={{ fontSize: 11, padding: '2px 8px', borderRadius: 10, background: 'rgba(34,197,94,0.12)', color: '#22c55e', fontWeight: 600 }}>LIVE · S3</span>
              </div>
              <ResponsiveContainer width="100%" height={280}>
                <BarChart data={testMetricsChart} layout="horizontal">
                  <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.1)" />
                  <XAxis dataKey="category" tick={{ fontSize: 13, fontWeight: 600 }} />
                  <YAxis domain={[0, 1]} tick={{ fontSize: 11 }} tickFormatter={(v) => v.toFixed(2)} />
                  <Tooltip formatter={(val) => val.toFixed(4)} />
                  <Legend />
                  <Bar dataKey="Precision" fill="#3b82f6" radius={[3, 3, 0, 0]} maxBarSize={40} />
                  <Bar dataKey="Recall" fill="#8b5cf6" radius={[3, 3, 0, 0]} maxBarSize={40} />
                  <Bar dataKey="F1" fill="#f59e0b" radius={[3, 3, 0, 0]} maxBarSize={40} />
                </BarChart>
              </ResponsiveContainer>
            </div>
            <div className="card">
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 12 }}>
                <div className="card-header" style={{ marginBottom: 0 }}>Daily Predicted Failures by Category</div>
                <span style={{ fontSize: 11, padding: '2px 8px', borderRadius: 10, background: 'rgba(34,197,94,0.12)', color: '#22c55e', fontWeight: 600 }}>LIVE</span>
              </div>
              {riskTrendChart.length > 0 ? (
                <ResponsiveContainer width="100%" height={280}>
                  <BarChart data={riskTrendChart}>
                    <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.1)" />
                    <XAxis dataKey="date" tick={{ fontSize: 10 }} interval="preserveStartEnd" />
                    <YAxis tick={{ fontSize: 11 }} />
                    <Tooltip />
                    <Legend />
                    <Bar dataKey="TVM_fail" name="TVM Failures" fill="#6366f1" opacity={0.8} radius={[2, 2, 0, 0]} />
                    <Bar dataKey="GATE_fail" name="GATE Failures" fill="#10b981" opacity={0.8} radius={[2, 2, 0, 0]} />
                  </BarChart>
                </ResponsiveContainer>
              ) : (
                <div style={{ height: 240, display: 'flex', alignItems: 'center', justifyContent: 'center', color: 'var(--text-secondary)', fontSize: 13 }}>
                  No trend data yet — run more inference batches.
                </div>
              )}
            </div>
          </div>

          <div className="card">
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 12 }}>
              <div className="card-header" style={{ marginBottom: 0 }}>Models by Device Type</div>
              <span style={{ fontSize: 11, padding: '2px 8px', borderRadius: 10, background: 'rgba(34,197,94,0.12)', color: '#22c55e', fontWeight: 600 }}>LIVE</span>
            </div>
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
              {ps1Devices.map((d) => <option key={d} value={d}>{d}</option>)}
            </select>
          </div>

          {/* Live SHAP chart — uses real explainability from RDS */}
          {liveShapData.length > 0 && (
            <div className="card" style={{ marginBottom: 24 }}>
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 12 }}>
                <div className="card-header" style={{ marginBottom: 0 }}>Feature Importance (SHAP) — Live · {causalDevice}</div>
                <span style={{ fontSize: 11, padding: '2px 8px', borderRadius: 10, background: 'rgba(34,197,94,0.12)', color: '#22c55e', fontWeight: 600 }}>LIVE</span>
              </div>
              <ResponsiveContainer width="100%" height={Math.max(200, liveShapData.length * 40)}>
                <BarChart data={liveShapData} layout="vertical" margin={{ left: 160 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.1)" />
                  <XAxis type="number" tick={{ fontSize: 11 }} />
                  <YAxis dataKey="feature" type="category" width={155} tick={{ fontSize: 11 }} />
                  <Tooltip formatter={(val, name) => [`${val.toFixed(4)} (raw: ${name})`, 'SHAP |value|']} />
                  <Bar dataKey="importance" radius={[0, 4, 4, 0]}>
                    {liveShapData.map((entry, i) => <Cell key={i} fill={entry.shap_value >= 0 ? '#ef4444' : '#3b82f6'} />)}
                  </Bar>
                </BarChart>
              </ResponsiveContainer>
              <div style={{ padding: '8px 16px', fontSize: 11, color: 'var(--text-secondary)', display: 'flex', gap: 16 }}>
                <span><span style={{ display: 'inline-block', width: 10, height: 10, background: '#ef4444', borderRadius: 2, marginRight: 4 }} />Positive SHAP (increases failure risk)</span>
                <span><span style={{ display: 'inline-block', width: 10, height: 10, background: '#3b82f6', borderRadius: 2, marginRight: 4 }} />Negative SHAP (decreases failure risk)</span>
              </div>
            </div>
          )}

          {/* Aggregate SHAP across all predictions for selected device — replaces "Historical" mock */}
          {!featImpLoading && liveAggShap.length > 0 && (
            <div className="card" style={{ marginBottom: 24 }}>
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 12 }}>
                <div className="card-header" style={{ marginBottom: 0 }}>Top 20 Aggregate Feature Importance (SHAP) — All Predictions · {causalDevice}</div>
                <span style={{ fontSize: 11, padding: '2px 8px', borderRadius: 10, background: 'rgba(34,197,94,0.12)', color: '#22c55e', fontWeight: 600 }}>LIVE · RDS</span>
              </div>
              <ResponsiveContainer width="100%" height={Math.max(300, liveAggShap.length * 28)}>
                <BarChart data={liveAggShap} layout="vertical" margin={{ left: 160 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.1)" />
                  <XAxis type="number" tick={{ fontSize: 11 }} />
                  <YAxis dataKey="feature" type="category" width={155} tick={{ fontSize: 11 }} />
                  <Tooltip formatter={(val) => val.toFixed(6)} />
                  <Bar dataKey="importance" name="Avg |SHAP|" radius={[0, 4, 4, 0]}>
                    {liveAggShap.map((entry, i) => <Cell key={i} fill={entry.shap_value >= 0 ? '#ef4444' : '#3b82f6'} />)}
                  </Bar>
                </BarChart>
              </ResponsiveContainer>
              <div style={{ padding: '8px 16px', fontSize: 11, color: 'var(--text-secondary)', display: 'flex', gap: 16 }}>
                <span><span style={{ display: 'inline-block', width: 10, height: 10, background: '#ef4444', borderRadius: 2, marginRight: 4 }} />Positive avg SHAP (increases failure risk)</span>
                <span><span style={{ display: 'inline-block', width: 10, height: 10, background: '#3b82f6', borderRadius: 2, marginRight: 4 }} />Negative avg SHAP (decreases failure risk)</span>
              </div>
            </div>
          )}

          {/* TVM vs GATE feature importance comparison — replaces Feature Heatmap */}
          {!featImpLoading && featCompChart.length > 0 && (
            <div className="card" style={{ marginBottom: 24 }}>
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 12 }}>
                <div className="card-header" style={{ marginBottom: 0 }}>Feature Importance: TVM vs GATE (Top 15)</div>
                <span style={{ fontSize: 11, padding: '2px 8px', borderRadius: 10, background: 'rgba(34,197,94,0.12)', color: '#22c55e', fontWeight: 600 }}>LIVE · RDS</span>
              </div>
              <ResponsiveContainer width="100%" height={Math.max(320, featCompChart.length * 30)}>
                <BarChart data={featCompChart} layout="vertical" margin={{ left: 180 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.1)" />
                  <XAxis type="number" tick={{ fontSize: 11 }} />
                  <YAxis dataKey="feature" type="category" width={175} tick={{ fontSize: 10 }} />
                  <Tooltip formatter={(val) => val ? val.toFixed(6) : '—'} />
                  <Legend />
                  <Bar dataKey="TVM" name="TVM Avg |SHAP|" fill="#6366f1" radius={[0, 4, 4, 0]} />
                  <Bar dataKey="GATE" name="GATE Avg |SHAP|" fill="#10b981" radius={[0, 4, 4, 0]} />
                </BarChart>
              </ResponsiveContainer>
            </div>
          )}

          {/* Contributing features table — replaces "Contributing Factor Breakdown" */}
          {!featImpLoading && featCompChart.length > 0 && (
            <div className="card" style={{ marginBottom: 24 }}>
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 12 }}>
                <div className="card-header" style={{ marginBottom: 0 }}>Top Feature Impact by Device Category</div>
                <span style={{ fontSize: 11, padding: '2px 8px', borderRadius: 10, background: 'rgba(34,197,94,0.12)', color: '#22c55e', fontWeight: 600 }}>LIVE · RDS</span>
              </div>
              <div style={{ overflowX: 'auto' }}>
                <table className="data-table">
                  <thead>
                    <tr><th>#</th><th>Feature</th><th>TVM Avg |SHAP|</th><th>TVM Avg SHAP</th><th>GATE Avg |SHAP|</th><th>GATE Avg SHAP</th><th>Dominant</th></tr>
                  </thead>
                  <tbody>
                    {featCompChart.map((row, i) => {
                      const tvmWins = (row.TVM || 0) >= (row.GATE || 0);
                      return (
                        <tr key={row.feature}>
                          <td style={{ color: 'var(--text-secondary)', fontSize: 11 }}>{i + 1}</td>
                          <td style={{ fontFamily: 'monospace', fontSize: 11, fontWeight: 600 }}>{row.feature}</td>
                          <td style={{ fontVariantNumeric: 'tabular-nums', color: '#6366f1', fontWeight: row.TVM ? 600 : 400 }}>{row.TVM ? row.TVM.toFixed(6) : '—'}</td>
                          <td style={{ fontVariantNumeric: 'tabular-nums', fontSize: 11, color: 'var(--text-secondary)' }}>{liveFeatImpTVM.find(f => f.feature_name === row.feature)?.avg_shap != null ? parseFloat(liveFeatImpTVM.find(f => f.feature_name === row.feature).avg_shap).toFixed(6) : '—'}</td>
                          <td style={{ fontVariantNumeric: 'tabular-nums', color: '#10b981', fontWeight: row.GATE ? 600 : 400 }}>{row.GATE ? row.GATE.toFixed(6) : '—'}</td>
                          <td style={{ fontVariantNumeric: 'tabular-nums', fontSize: 11, color: 'var(--text-secondary)' }}>{liveFeatImpGATE.find(f => f.feature_name === row.feature)?.avg_shap != null ? parseFloat(liveFeatImpGATE.find(f => f.feature_name === row.feature).avg_shap).toFixed(6) : '—'}</td>
                          <td><span style={{ fontWeight: 700, color: tvmWins ? '#6366f1' : '#10b981', fontSize: 12 }}>{row.TVM && row.GATE ? (tvmWins ? 'TVM' : 'GATE') : (row.TVM ? 'TVM' : 'GATE')}</span></td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            </div>
          )}

          {/* Failure risk trend by device — replaces "SHAP Drift" mock */}
          <div className="card">
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 12 }}>
              <div className="card-header" style={{ marginBottom: 0 }}>Predicted Failure Risk Trend by Device Category</div>
              <span style={{ fontSize: 11, padding: '2px 8px', borderRadius: 10, background: 'rgba(34,197,94,0.12)', color: '#22c55e', fontWeight: 600 }}>LIVE</span>
            </div>
            {riskTrendChart.length > 0 ? (
              <ResponsiveContainer width="100%" height={300}>
                <LineChart data={riskTrendChart}>
                  <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.1)" />
                  <XAxis dataKey="date" tick={{ fontSize: 11 }} interval="preserveStartEnd" />
                  <YAxis domain={[0, 100]} tick={{ fontSize: 11 }} unit="%" />
                  <Tooltip formatter={(val) => `${val}%`} />
                  <Legend />
                  <Line type="monotone" dataKey="TVM_prob" name="TVM Avg Risk %" stroke="#6366f1" strokeWidth={2} dot={{ r: 4, fill: '#6366f1' }} />
                  <Line type="monotone" dataKey="GATE_prob" name="GATE Avg Risk %" stroke="#10b981" strokeWidth={2} dot={{ r: 4, fill: '#10b981' }} />
                </LineChart>
              </ResponsiveContainer>
            ) : (
              <div style={{ height: 160, display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', gap: 8, color: 'var(--text-secondary)', fontSize: 13 }}>
                <div>No trend data yet.</div>
                <div style={{ fontSize: 11 }}>Run additional inference batches to populate this chart.</div>
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
