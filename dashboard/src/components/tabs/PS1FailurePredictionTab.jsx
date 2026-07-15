// ============================================================================
// PS1 — Failure Prediction (HONEST training-results view).
// PS1's model was NOT promoted (both quality gates failed: TVM recall 0.691 <
// 0.80 floor, Gates recall 0.380 < 0.70 floor), so there is no live prediction
// feed. This tab shows what genuinely exists in RDS — per-device model
// performance, the model leaderboard, and SHAP feature drivers — behind a clear
// "not promoted" banner. Live from /ps1/summary,/leaderboard,/features;
// real-anchored mock fallback (run 20260713_0905). Wired 2026-07-15.
// ============================================================================
import React, { useState } from 'react';
import { CITIES, getPS1FailureSummary, getPS1Leaderboard, getPS1Features } from '../../data/mockData';
import { apiPS1Summary, apiPS1Leaderboard, apiPS1Features, useLiveData } from '../../data/api';

const NAVY = '#1E3A5F', INK = '#5A6B7D', LINE = '#E1E9F1';
const SUB_TABS = [
  { key: 'overview',    label: 'Model Scorecard' },
  { key: 'leaderboard', label: 'Model Leaderboard' },
  { key: 'drivers',     label: 'Feature Drivers' },
];
const pct = (v) => (v === null || v === undefined ? '--' : `${(Number(v) * 100).toFixed(1)}%`);
const num = (v, d = 3) => (v === null || v === undefined ? '--' : Number(v).toFixed(d));
const nfmt = (v) => (v === null || v === undefined ? '--' : Number(v).toLocaleString());
const th = { textAlign: 'left', padding: '8px 10px', background: '#EEF3F9', color: NAVY, fontWeight: 600, fontSize: 12 };
const td = { padding: '7px 10px', borderTop: `1px solid #EEF2F7`, fontSize: 12.5, color: '#33475B' };

function Bar({ value, max, color }) {
  const w = max > 0 ? Math.max(2, (value / max) * 100) : 0;
  return (
    <div style={{ background: '#F0F4F8', borderRadius: 6, height: 10, width: '100%' }}>
      <div style={{ width: `${w}%`, height: 10, borderRadius: 6, background: color }} />
    </div>
  );
}

export default function PS1FailurePredictionTab({ city }) {
  const cityName = CITIES.find((c) => c.id === city)?.name || city;
  const [tab, setTab] = useState('overview');
  const summary = useLiveData(getPS1FailureSummary(), () => apiPS1Summary(city), [city]);
  const leaderboard = useLiveData(getPS1Leaderboard(), () => apiPS1Leaderboard(city), [city]);
  const features = useLiveData(getPS1Features(), () => apiPS1Features(city), [city]);

  const devices = (summary || []).map((s) => s.device);

  return (
    <div>
      {/* NOT-PROMOTED banner */}
      <div style={{ background: '#FDECEA', border: '1px solid #F1A9A0', borderLeft: '5px solid #D14C3C',
        borderRadius: 10, padding: '12px 16px', margin: '4px 0 18px', color: '#7A241B', fontSize: 13 }}>
        <b>PS1 model NOT promoted.</b> Both quality gates failed this run (TVM recall {pct(summary?.[0]?.test_recall)} &lt; {pct(summary?.[0]?.recall_floor)} floor; Gates recall {pct(summary?.[1]?.test_recall)} &lt; {pct(summary?.[1]?.recall_floor)} floor). These are <b>training results for review only</b> — there is no live prediction feed and this must not drive maintenance decisions. Run {summary?.[0]?.run_id}.
      </div>

      {/* KPI cards per device */}
      <div style={{ display: 'grid', gridTemplateColumns: `repeat(${Math.max(1, devices.length * 2)}, 1fr)`, gap: 14, marginBottom: 18 }}>
        {(summary || []).map((s) => (
          <React.Fragment key={s.device}>
            <div className="card">
              <div className="card-header">{s.device} — Champion</div>
              <div className="kpi-value" style={{ fontSize: 18 }}>{s.champion_model}</div>
              <div className="kpi-label">test AUC {num(s.test_auc)} · AP {num(s.test_ap)}{s.overfit_flag ? ' · ⚠ over-fit' : ''}</div>
            </div>
            <div className="card">
              <div className="card-header">{s.device} — Recall vs floor</div>
              <div className="kpi-value" style={{ color: '#ef4444' }}>{pct(s.test_recall)}</div>
              <div className="kpi-label">floor {pct(s.recall_floor)} · gate <b style={{ color: '#ef4444' }}>{s.quality_gate}</b> · base rate {num(s.base_rate_pct, 2)}%</div>
            </div>
          </React.Fragment>
        ))}
      </div>

      <div className="tab-container">
        {SUB_TABS.map((t) => (
          <button key={t.key} className={`tab ${tab === t.key ? 'active' : ''}`} onClick={() => setTab(t.key)}>{t.label}</button>
        ))}
      </div>

      {/* ---- Model Scorecard ---- */}
      {tab === 'overview' && (
        <div style={{ display: 'grid', gridTemplateColumns: `repeat(${Math.max(1, devices.length)}, 1fr)`, gap: 16 }}>
          {(summary || []).map((s) => (
            <div className="card" key={s.device}>
              <div className="card-header">{s.device} — {cityName} ({s.target})</div>
              <table style={{ width: '100%', borderCollapse: 'collapse', marginTop: 6 }}>
                <tbody>
                  {[
                    ['Champion', s.champion_model], ['Quality gate', s.quality_gate + (s.promoted ? '' : ' · not promoted')],
                    ['Test AUC', num(s.test_auc)], ['Test AP (PR-AUC)', num(s.test_ap)],
                    ['Test recall', `${pct(s.test_recall)}  (floor ${pct(s.recall_floor)})`],
                    ['Test precision', pct(s.test_precision)], ['Test F1', num(s.test_f1)], ['Accuracy', pct(s.test_accuracy)],
                    ['Base rate', `${num(s.base_rate_pct, 2)}%`], ['Over-fit flag', s.overfit_flag ? 'YES (train/val AUC ~1.0)' : 'no'],
                    ['Train / test rows', `${nfmt(s.n_train)} / ${nfmt(s.n_test)} (${nfmt(s.n_test_pos)} pos)`],
                    ['MLflow / endpoint', `${s.mlflow_version} · ${s.endpoint_name}`],
                  ].map(([k, v]) => (
                    <tr key={k}><td style={{ ...td, color: INK, width: '46%' }}>{k}</td><td style={{ ...td, fontWeight: 600, color: NAVY }}>{v}</td></tr>
                  ))}
                </tbody>
              </table>
            </div>
          ))}
        </div>
      )}

      {/* ---- Model Leaderboard ---- */}
      {tab === 'leaderboard' && (
        <div style={{ display: 'grid', gridTemplateColumns: `repeat(${Math.max(1, devices.length)}, 1fr)`, gap: 16 }}>
          {devices.map((dev) => {
            const rows = (leaderboard || []).filter((r) => r.device === dev).sort((a, b) => a.lb_rank - b.lb_rank);
            const maxAP = Math.max(0.01, ...rows.map((r) => r.ap || 0));
            return (
              <div className="card" key={dev}>
                <div className="card-header">{dev} — model leaderboard (by AP)</div>
                <table style={{ width: '100%', borderCollapse: 'collapse' }}>
                  <thead><tr><th style={th}>#</th><th style={th}>Model</th><th style={th}>AUC</th><th style={{ ...th, width: '34%' }}>AP</th><th style={th}>Recall</th></tr></thead>
                  <tbody>{rows.map((r) => (
                    <tr key={r.model} style={{ background: r.is_champion ? '#EEF6FF' : 'transparent' }}>
                      <td style={{ ...td, fontWeight: 700, color: NAVY }}>{r.lb_rank}</td>
                      <td style={td}>{r.model}{r.is_champion ? ' ★' : ''}{r.note ? <div style={{ fontSize: 10.5, color: INK }}>{r.note}</div> : null}</td>
                      <td style={td}>{num(r.auc)}</td>
                      <td style={td}><div style={{ display: 'flex', alignItems: 'center', gap: 8 }}><Bar value={r.ap} max={maxAP} color={r.is_champion ? '#3E7CB1' : '#9DC3E6'} /><span style={{ minWidth: 40, color: INK }}>{num(r.ap)}</span></div></td>
                      <td style={td}>{pct(r.rec)}</td>
                    </tr>
                  ))}</tbody>
                </table>
              </div>
            );
          })}
        </div>
      )}

      {/* ---- Feature Drivers (SHAP) ---- */}
      {tab === 'drivers' && (
        <div style={{ display: 'grid', gridTemplateColumns: `repeat(${Math.max(1, devices.length)}, 1fr)`, gap: 16 }}>
          {devices.map((dev) => {
            const rows = (features || []).filter((r) => r.device === dev).sort((a, b) => a.feat_rank - b.feat_rank);
            const maxP = Math.max(1, ...rows.map((r) => r.pct_total || 0));
            return (
              <div className="card" key={dev}>
                <div className="card-header">{dev} — SHAP feature drivers</div>
                <p style={{ fontSize: 11.5, color: INK, margin: '0 0 10px' }}>
                  {rows[0]?.feature} dominates ({num(rows[0]?.pct_total, 1)}% of importance) — the model largely re-reads recent failure rate.
                </p>
                <table style={{ width: '100%', borderCollapse: 'collapse' }}>
                  <tbody>{rows.map((r) => (
                    <tr key={r.feature}>
                      <td style={{ ...td, fontFamily: 'monospace', width: '40%' }}>{r.feature}</td>
                      <td style={td}><div style={{ display: 'flex', alignItems: 'center', gap: 8 }}><Bar value={r.pct_total} max={maxP} color="#A9D18E" /><span style={{ minWidth: 46, color: INK }}>{num(r.pct_total, 1)}%</span></div></td>
                    </tr>
                  ))}</tbody>
                </table>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
