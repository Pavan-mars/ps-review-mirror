import React from 'react';
import {
  BarChart, Bar, Cell, PieChart, Pie,
  XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer,
} from 'recharts';
import { CITIES, getPS3SeveritySummary, getPS3SeverityDrivers } from '../../data/mockData';
import { apiPS3Summary, apiPS3Drivers, useLiveData } from '../../data/api';

// PS3 is a FAILURE-SEVERITY classifier (MAJOR vs CRITICAL), verified honest 2026-07-13.
// It is NOT the 9-class root cause (blocked on the SVN_STAGE ETL). This tab reads live
// from the cubic-mars-dashboard-api (/ps3/*) with mockData as the instant/fallback value.
const SEV_COLORS = { MAJOR: '#f59e0b', CRITICAL: '#ef4444' };

export default function PS3RootCauseTab({ city }) {
  const cityName = CITIES.find((c) => c.id === city)?.name || city;

  const summary = useLiveData(getPS3SeveritySummary(city), () => apiPS3Summary(city), [city]);
  const drivers = useLiveData(getPS3SeverityDrivers(city), () => apiPS3Drivers(city), [city]);

  if (!summary) {
    return (
      <div className="card" style={{ padding: 32, textAlign: 'center' }}>
        <div className="card-header">PS3 - Failure Severity</div>
        <p style={{ opacity: 0.7, marginTop: 12 }}>
          No PS3 severity model has been trained for {cityName} yet. PS3 currently has a
          completed, verified run only for Chicago. Other cities follow once their
          bronze/silver/gold layers and the ServiceNow availability feed are built.
        </p>
      </div>
    );
  }

  const classDist = [
    { name: 'MAJOR', value: summary.n_major },
    { name: 'CRITICAL', value: summary.n_critical },
  ];
  const driverRows = (drivers || []).slice().sort((a, b) => a.driver_rank - b.driver_rank);
  const top = driverRows[0];
  const shapPct = Math.round((summary.dominant_feature_shap || 0) * 100);

  return (
    <div>
      {/* Honest banner - severity, not root cause */}
      <div className="card" style={{ marginBottom: 20, borderLeft: '4px solid #f59e0b', background: 'rgba(245,158,11,0.06)' }}>
        <div style={{ padding: '14px 16px' }}>
          <div style={{ fontWeight: 700, fontSize: 15, marginBottom: 6, color: '#f59e0b' }}>
            Failure Severity model - live (this is not root cause)
          </div>
          <div style={{ fontSize: 13, opacity: 0.88, lineHeight: 1.55 }}>
            The live endpoint <code>{summary.endpoint_name}</code> classifies incident{' '}
            <strong>severity</strong> (MAJOR vs CRITICAL failure level), not root cause. The true
            9-class root cause is <strong>blocked</strong>: {summary.true_rootcause_status}{' '}
            Feasibility {summary.feasibility_pct}%. The model is ~{shapPct}% driven by{' '}
            <code>{summary.dominant_feature}</code> (the incident's own ServiceNow event code),
            so treat it as an auto-triage severity tagger that needs the event code present at
            scoring - not an autonomous predictor.
          </div>
        </div>
      </div>

      {/* KPI cards */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 16, marginBottom: 24 }}>
        <div className="card">
          <div className="card-header">Test AUC (macro)</div>
          <div className="kpi-value">{summary.test_auc_macro.toFixed(4)}</div>
          <div className="kpi-label">champion: {summary.champion_model}</div>
        </div>
        <div className="card">
          <div className="card-header">F1-macro</div>
          <div className="kpi-value">{summary.test_f1_macro.toFixed(3)}</div>
          <div className="kpi-label">accuracy {summary.test_accuracy.toFixed(3)}</div>
        </div>
        <div className="card">
          <div className="card-header">Incidents</div>
          <div className="kpi-value">{summary.n_incidents.toLocaleString()}</div>
          <div className="kpi-label">{summary.date_start} to {summary.date_end}</div>
        </div>
        <div className="card">
          <div className="card-header">Scope</div>
          <div className="kpi-value" style={{ fontSize: 20 }}>Severity (binary)</div>
          <div className="kpi-label">{summary.device_note}</div>
        </div>
      </div>

      <div className="grid-2" style={{ marginBottom: 24 }}>
        {/* Severity class distribution */}
        <div className="card">
          <div className="card-header">Severity Class Distribution - {cityName}</div>
          <ResponsiveContainer width="100%" height={280}>
            <PieChart>
              <Pie data={classDist} dataKey="value" nameKey="name" cx="50%" cy="50%" innerRadius={60} outerRadius={100}
                   label={({ name, percent }) => `${name} (${(percent * 100).toFixed(1)}%)`}>
                {classDist.map((d) => <Cell key={d.name} fill={SEV_COLORS[d.name]} />)}
              </Pie>
              <Tooltip formatter={(v) => v.toLocaleString()} />
            </PieChart>
          </ResponsiveContainer>
          <p style={{ fontSize: 12, opacity: 0.65, padding: '0 16px 12px' }}>
            Balanced binary target - MAJOR {summary.n_major.toLocaleString()} / CRITICAL {summary.n_critical.toLocaleString()}.
            MINOR is absent in the data, so the intended 3-class collapsed to 2.
          </p>
        </div>

        {/* Top signals (single-feature AUC from the leakage scan) */}
        <div className="card">
          <div className="card-header">Top Signals (single-feature AUC)</div>
          <ResponsiveContainer width="100%" height={280}>
            <BarChart data={driverRows} layout="vertical" margin={{ left: 40 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.1)" />
              <XAxis type="number" domain={[0.5, 0.8]} tick={{ fontSize: 11 }} />
              <YAxis dataKey="feature" type="category" width={130} tick={{ fontSize: 10 }} />
              <Tooltip formatter={(v) => (v == null ? '-' : v.toFixed(3))} />
              <Bar dataKey="solo_auc" radius={[0, 4, 4, 0]}>
                {driverRows.map((d, i) => <Cell key={i} fill={d.driver_rank === 1 ? '#ef4444' : '#6366f1'} />)}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
          <p style={{ fontSize: 12, opacity: 0.65, padding: '0 16px 12px' }}>
            No single feature exceeds solo-AUC 0.95 (top is {top ? top.feature : '-'} at{' '}
            {top ? top.solo_auc.toFixed(3) : '-'}), so there is no trivial label leak. SHAP
            importance is concentrated in {summary.dominant_feature} (~{shapPct}% of the model).
          </p>
        </div>
      </div>

      {/* Model + serving */}
      <div className="card" style={{ marginBottom: 24 }}>
        <div className="card-header">Model &amp; Serving</div>
        <table className="data-table">
          <tbody>
            <tr><td style={{ opacity: 0.7, width: 200 }}>Champion</td><td style={{ fontWeight: 600 }}>{summary.champion_model}</td></tr>
            <tr><td style={{ opacity: 0.7 }}>Endpoint</td><td style={{ fontFamily: 'monospace', fontSize: 12 }}>{summary.endpoint_name} (InService)</td></tr>
            <tr><td style={{ opacity: 0.7 }}>MLflow / SM package</td><td style={{ fontFamily: 'monospace', fontSize: 12 }}>{summary.mlflow_version} / {summary.sm_package}</td></tr>
            <tr><td style={{ opacity: 0.7 }}>Serving image (BYOC ECR)</td><td style={{ fontFamily: 'monospace', fontSize: 11 }}>{summary.serving_image}</td></tr>
          </tbody>
        </table>
      </div>

      {/* True root cause - gated */}
      <div className="card" style={{ borderLeft: '4px solid #64748b' }}>
        <div className="card-header">True Root Cause (9-class) - gated</div>
        <p style={{ fontSize: 13, opacity: 0.8, padding: '4px 16px 16px', lineHeight: 1.55 }}>
          The 9-class fault-code root cause is not yet available. {summary.true_rootcause_status}{' '}
          It will be built once the SVN_STAGE ETL is activated (all 35 tables currently have 0 rows).
          Until then this tab shows the verified severity model above rather than fabricated root-cause categories.
        </p>
      </div>
    </div>
  );
}
