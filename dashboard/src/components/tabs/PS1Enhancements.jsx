import React, { useMemo } from 'react';

// Phase-2 PS1 enhancements: egovmars-style KPI header, computed insight callouts,
// interactive filter bar + tags, and per-station health cards. All pure/presentational
// (fed by props from PS1FailurePredictionTab). Grounded in real /ps1 data; PS1 is REVIEW-ONLY.
const BAND_COLOR = { Critical: '#ef4444', High: '#f97316', Medium: '#f59e0b', Low: '#3b82f6', Info: '#6b7280' };
const N = (v) => (v === null || v === undefined || v === '' ? null : parseFloat(v));

function pill(label, value, color) {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', padding: '4px 14px', borderRight: '1px solid rgba(255,255,255,0.12)' }}>
      <span style={{ fontSize: 18, fontWeight: 800, color: color || '#fff', lineHeight: 1.1 }}>{value}</span>
      <span style={{ fontSize: 10, color: 'rgba(255,255,255,0.6)', textTransform: 'uppercase', letterSpacing: 0.4 }}>{label}</span>
    </div>
  );
}

export function Ps1KpiHeader({ predictions = [], modelPerf = [], sevFn }) {
  const s = useMemo(() => {
    const total = predictions.length;
    const failures = predictions.filter((p) => p.predicted_label).length;
    const critical = sevFn ? predictions.filter((p) => sevFn(p.failure_probability, p.decision_threshold) === 'Critical').length : 0;
    const avgProb = total ? predictions.reduce((a, p) => a + N(p.failure_probability), 0) / total * 100 : 0;
    const aucs = modelPerf.map((m) => N(m.test_auc)).filter((x) => x != null);
    const avgAuc = aucs.length ? aucs.reduce((a, b) => a + b, 0) / aucs.length : null;
    const lastRun = predictions[0]?.inference_ts || '—';
    return { total, failures, critical, avgProb, avgAuc, lastRun };
  }, [predictions, modelPerf, sevFn]);

  function exportCsv() {
    const cols = ['device_id', 'device_category', 'facility_id', 'failure_probability', 'decision_threshold', 'predicted_label', 'prediction_date', 'inference_ts'];
    const rows = [cols.join(',')].concat(predictions.map((p) => cols.map((c) => `"${(p[c] ?? '')}"`).join(',')));
    const blob = new Blob([rows.join('\n')], { type: 'text/csv' });
    const a = document.createElement('a'); a.href = URL.createObjectURL(blob);
    a.download = `ps1_predictions_${new Date().toISOString().slice(0, 10)}.csv`; a.click();
  }

  return (
    <div style={{ background: 'linear-gradient(90deg,#0b1220,#111c34)', border: '1px solid rgba(255,255,255,0.08)', borderRadius: 12, padding: '14px 18px', marginBottom: 16 }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap', gap: 10 }}>
        <div>
          <div style={{ fontSize: 17, fontWeight: 800, color: '#fff' }}>PS1 · Failure Prediction — Live</div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginTop: 4 }}>
            <span style={{ width: 8, height: 8, borderRadius: '50%', background: '#22c55e', display: 'inline-block' }} />
            <span style={{ fontSize: 11, color: 'rgba(255,255,255,0.65)' }}>SageMaker PS1 · last run {String(s.lastRun).slice(0, 16)}</span>
            <span style={{ fontSize: 10, fontWeight: 700, padding: '2px 8px', borderRadius: 10, background: 'rgba(239,68,68,0.18)', color: '#fca5a5' }}>REVIEW-ONLY · not promoted</span>
          </div>
        </div>
        <div style={{ display: 'flex', alignItems: 'center' }}>
          {pill('Devices', s.total, '#fff')}
          {pill('Flagged FAIL', s.failures, s.failures ? '#f87171' : '#4ade80')}
          {pill('Critical', s.critical, '#f87171')}
          {pill('Avg risk', `${s.avgProb.toFixed(1)}%`, '#fbbf24')}
          {pill('Avg AUC-ROC', s.avgAuc != null ? s.avgAuc.toFixed(3) : '—', '#4ade80')}
          <button onClick={exportCsv} style={{ marginLeft: 12, background: 'rgba(255,255,255,0.1)', color: '#fff', border: '1px solid rgba(255,255,255,0.2)', borderRadius: 8, padding: '6px 12px', cursor: 'pointer', fontSize: 12, fontWeight: 600 }}>⤓ Export CSV</button>
        </div>
      </div>
    </div>
  );
}

function InsightCard({ tint, tag, headline, body }) {
  return (
    <div className="card" style={{ borderLeft: `3px solid ${tint}`, marginBottom: 0 }}>
      <div style={{ fontSize: 10, fontWeight: 700, color: tint, letterSpacing: 0.5, marginBottom: 4 }}>{tag}</div>
      <div style={{ fontSize: 15, fontWeight: 700, marginBottom: 4 }}>{headline}</div>
      <div style={{ fontSize: 12, color: 'var(--text-secondary)', lineHeight: 1.45 }}>{body}</div>
    </div>
  );
}

export function Ps1Insights({ predictions = [], stations = [], features = [], sevFn }) {
  const ins = useMemo(() => {
    const total = predictions.length || 1;
    const failures = predictions.filter((p) => p.predicted_label).length;
    const failPct = (failures / total * 100).toFixed(0);
    const byCat = {};
    predictions.forEach((p) => { const c = p.device_category; if (!byCat[c]) byCat[c] = { thr: N(p.decision_threshold), n: 0, f: 0 }; byCat[c].n++; if (p.predicted_label) byCat[c].f++; });
    const thrTxt = Object.entries(byCat).map(([c, v]) => `${c} ${(v.thr * 100).toFixed(1)}%`).join(', ');
    const hot = [...stations].sort((a, b) => N(b.avg_risk_pct) - N(a.avg_risk_pct))[0];
    const drv = features[0];
    return { failures, failPct, thrTxt, hot, drv };
  }, [predictions, stations, features]);

  return (
    <div className="grid-4" style={{ marginBottom: 24 }}>
      <InsightCard tint="#ef4444" tag="OVER-ALERT WATCH"
        headline={`${ins.failures} of ${predictions.length} flagged (${ins.failPct}%)`}
        body="PS1 over-alerts at current thresholds and has NOT passed its quality gate. Use as a watchlist and corroborate with PS2 cascade / PS4 anomaly before dispatch." />
      <InsightCard tint="#6366f1" tag="TOP FLEET DRIVER"
        headline={ins.drv ? ins.drv.feature_name : '—'}
        body={ins.drv ? `Highest mean |SHAP| driver of TVM failure risk (avg importance ${N(ins.drv.avg_importance)?.toFixed(3) ?? '—'}). Recent failure rate dominates.` : 'Feature importance loading.'} />
      <InsightCard tint="#f97316" tag="STATION HOTSPOT"
        headline={ins.hot ? `Facility ${ins.hot.facility_id}` : '—'}
        body={ins.hot ? `${N(ins.hot.avg_risk_pct)?.toFixed(1)}% avg risk · ${ins.hot.critical_count || 0} critical · ${ins.hot.predicted_failures} predicted failures across ${ins.hot.total_devices} devices.` : 'No station summary yet.'} />
      <InsightCard tint="#0ea5e9" tag="HOW TO READ THIS"
        headline="Judge by recall / AP, not accuracy"
        body={`Thresholds tuned for recall (${ins.thrTxt || '—'}) at a ~1.5-2% failure base rate, so raw accuracy is misleading. AP / recall are the promotion gates.`} />
    </div>
  );
}

export function Ps1FilterBar({ filters, onChange, categories = [], shown = 0, total = 0 }) {
  const SEV = ['ALL', 'Critical', 'High', 'Medium', 'Low', 'Info'];
  const chips = [];
  if (filters.category !== 'ALL') chips.push(['category', `Type: ${filters.category}`]);
  if (filters.severity !== 'ALL') chips.push(['severity', `Severity: ${filters.severity}`]);
  if (filters.failOnly) chips.push(['failOnly', 'FAIL only']);
  if (filters.search) chips.push(['search', `"${filters.search}"`]);
  const sel = { padding: '6px 10px', borderRadius: 8, border: '1px solid var(--border)', background: 'var(--bg)', color: 'var(--text-primary)', fontSize: 12 };
  return (
    <div className="card" style={{ marginBottom: 12, padding: '12px 14px' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
        <span style={{ fontSize: 12, fontWeight: 700, color: 'var(--text-secondary)' }}>Filters</span>
        <select value={filters.category} onChange={(e) => onChange({ ...filters, category: e.target.value })} style={sel}>
          {['ALL', ...categories].map((c) => <option key={c} value={c}>{c === 'ALL' ? 'All types' : c}</option>)}
        </select>
        <select value={filters.severity} onChange={(e) => onChange({ ...filters, severity: e.target.value })} style={sel}>
          {SEV.map((s) => <option key={s} value={s}>{s === 'ALL' ? 'All severities' : s}</option>)}
        </select>
        <input value={filters.search} onChange={(e) => onChange({ ...filters, search: e.target.value })} placeholder="Search device / facility…" style={{ ...sel, minWidth: 190 }} />
        <label style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 12, cursor: 'pointer' }}>
          <input type="checkbox" checked={filters.failOnly} onChange={(e) => onChange({ ...filters, failOnly: e.target.checked })} /> FAIL only
        </label>
        <span style={{ marginLeft: 'auto', fontSize: 12, color: 'var(--text-secondary)' }}>Showing <b>{shown}</b> of {total}</span>
        {chips.length > 0 && <button onClick={() => onChange({ category: 'ALL', severity: 'ALL', search: '', failOnly: false })} style={{ fontSize: 11, background: 'transparent', color: '#818cf8', border: '1px solid rgba(129,140,248,0.4)', borderRadius: 8, padding: '4px 10px', cursor: 'pointer' }}>Clear all</button>}
      </div>
      {chips.length > 0 && (
        <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginTop: 10 }}>
          {chips.map(([k, lbl]) => (
            <span key={k} style={{ display: 'inline-flex', alignItems: 'center', gap: 6, fontSize: 11, padding: '3px 10px', borderRadius: 14, background: 'rgba(99,102,241,0.15)', color: '#a5b4fc' }}>
              {lbl}
              <span onClick={() => onChange({ ...filters, [k]: k === 'failOnly' ? false : (k === 'search' ? '' : 'ALL') })} style={{ cursor: 'pointer', fontWeight: 700 }}>×</span>
            </span>
          ))}
        </div>
      )}
    </div>
  );
}

export function Ps1StationCards({ stations = [], names = {} }) {
  const top = useMemo(() => [...stations].sort((a, b) => N(b.avg_risk_pct) - N(a.avg_risk_pct)).slice(0, 8), [stations]);
  if (!top.length) return null;
  return (
    <div style={{ marginBottom: 24 }}>
      <div className="card-header" style={{ marginBottom: 12 }}>Station Health Overview — Top {top.length} by risk</div>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(220px, 1fr))', gap: 12 }}>
        {top.map((s) => {
          const risk = N(s.avg_risk_pct) || 0;
          const tint = s.critical_count > 0 ? '#ef4444' : risk >= 40 ? '#f97316' : risk >= 20 ? '#f59e0b' : '#22c55e';
          return (
            <div key={s.facility_id} className="card" style={{ borderTop: `3px solid ${tint}`, marginBottom: 0 }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline' }}>
                <span style={{ fontWeight: 700, fontSize: 14 }} title={`Facility ${s.facility_id}`}>{names[s.facility_id] || `Facility ${s.facility_id}`}</span>
                <span style={{ fontSize: 16, fontWeight: 800, color: tint }}>{risk.toFixed(0)}%</span>
              </div>
              <div style={{ background: 'rgba(255,255,255,0.06)', borderRadius: 4, height: 6, margin: '8px 0' }}>
                <div style={{ width: `${Math.min(100, risk)}%`, background: tint, height: 6, borderRadius: 4 }} />
              </div>
              <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', fontSize: 11, color: 'var(--text-secondary)' }}>
                <span>{s.total_devices} devices</span>
                <span style={{ color: s.predicted_failures > 0 ? '#f87171' : '#4ade80' }}>{s.predicted_failures} fail</span>
                {s.critical_count > 0 && <span style={{ color: '#ef4444' }}>{s.critical_count} crit</span>}
                {s.high_count > 0 && <span style={{ color: '#f97316' }}>{s.high_count} high</span>}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
