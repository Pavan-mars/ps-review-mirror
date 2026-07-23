// ============================================================================
// PS5 v5 RELIABILITY (device-grain survival / RUL) — mock shape for the tab.
// Event = ANY HARDWARE OOS 'Set' (device_failures: fault_state=Set, is_device_fault,
// Counted-as-OOS; commanded/maintenance OOS excluded; chargeable-SLA gating removed).
// Mirrors the fields the v5 notebook writes to <type>_reliability_summary.json.
//
// Swapped live by apiPS5Reliability() -> GET /ps5/reliability once the RDS route is up.
// The concordance indices below are the real last-known values; the RUL / survival /
// recency figures are ILLUSTRATIVE SAMPLES pending the live v5 run (the UI shows a
// "sample" ribbon so nothing is misrepresented to the client).
// ============================================================================

// Weibull-style survival curve S(t) = exp(-(t/scale)^k), sampled to ~2x scale days.
function survCurve(scaleDays, k = 1.2, n = 12) {
  const pts = [];
  for (let i = 0; i <= n; i++) {
    const day = Math.round((i / n) * scaleDays * 2);
    const surv = Math.exp(-Math.pow(day / scaleDays, k));
    pts.push({ day, surv: Math.round(surv * 1000) / 1000 });
  }
  return pts;
}

export function getPS5ReliabilityDetail(city = 'CHI') {
  if (city !== 'CHI') return null;
  return {
    event_definition: 'hardware OOS (Set)',
    event_def_version: '2026-07-23.v1',
    window: 'telemetry-era (2024-01-01+)',
    floor: 0.65,
    is_sample: true, // RUL/survival/recency are illustrative until the live v5 run populates /ps5/reliability
    devices: [
      {
        device: 'TVMs', cv_cindex: 0.5943, champion: 'Enriched CoxPH (perm-selected)', gate_pass: false,
        rul_median_days: 41, rul_p10_days: 9, rul_p90_days: 128, days_since_fail: 22, roll_fail_30d: 1,
        n_events: 96725, ibs: 0.171, survival: survCurve(45, 1.3),
      },
      {
        device: 'Gates', cv_cindex: 0.5777, champion: 'Enriched Cox + facility frailty', gate_pass: false,
        rul_median_days: 63, rul_p10_days: 14, rul_p90_days: 180, days_since_fail: 35, roll_fail_30d: 0,
        n_events: 15738, ibs: 0.152, survival: survCurve(70, 1.1),
      },
      {
        device: 'Validators', cv_cindex: 0.6287, champion: 'Enriched CoxPH', gate_pass: false,
        rul_median_days: 38, rul_p10_days: 8, rul_p90_days: 119, days_since_fail: 12, roll_fail_30d: 2,
        n_events: 60036, ibs: 0.129, survival: survCurve(42, 0.95),
      },
    ],
    note:
      'Failure = any hardware OOS "Set" (fault_state=Set + is_device_fault + Counted-as-OOS; commanded/maintenance OOS excluded; ' +
      'chargeable-SLA gating removed). Feature-label alignment verified: days_since_fail / roll_fail_* are computed strictly ' +
      'as-of interval-start. C-index shown is the real last-known value; RUL / survival / recency are illustrative samples ' +
      'that populate from the live v5 run via /ps5/reliability.',
  };
}

// ============================================================================
// v5.1 DEVICE-grain RUL + SERIAL-grain health (sample) for the PS5 subtabs.
// Event = hardware OOS 'Set'. Rows are ILLUSTRATIVE SAMPLES (clearly labelled) with
// the shape the v5.1 notebook writes to <type>_device_rul_estimates.csv /
// <type>_serial_reliability.csv. Swapped live by apiPS5DeviceRUL()/apiPS5SerialHealth()
// -> GET /ps5/devices , /ps5/serials once the RDS views (migration 06) + routes are up.
// ============================================================================
const _PS5_CATS = [
  { type: 'TVMs', cat: 'TVM', pfx: 'TVM', scale: 45, cidx: 0.5943 },
  { type: 'Gates', cat: 'GATE', pfx: 'GTE', scale: 70, cidx: 0.5777 },
  { type: 'Validators', cat: 'VALIDATOR', pfx: 'VAL', scale: 42, cidx: 0.6287 },
];
const _riskBand = (hz) => (hz >= 0.90 ? 'CRITICAL' : hz >= 0.70 ? 'HIGH' : hz >= 0.40 ? 'MEDIUM' : 'LOW');
const _riskTier = (rs) => (rs > 0.02 ? 'CRITICAL' : rs > 0.008 ? 'HIGH' : rs > 0.002 ? 'MEDIUM' : 'LOW');
const _u = (n) => (Math.sin(n) + 1) / 2;   // deterministic pseudo-uniform 0..1 (no Math.random)

export function getPS5DeviceRUL(city = 'CHI') {
  if (city !== 'CHI') return null;
  const devices = [];
  _PS5_CATS.forEach((c, ci) => {
    for (let i = 0; i < 6; i++) {
      const k = ci * 6 + i;
      const hz = Math.round(_u(k * 1.7 + 0.3) * 1000) / 1000;
      const age = Math.round(30 + _u(k * 0.9 + 1.1) * 560);
      const rul = Math.max(6, Math.round(c.scale * (0.3 + 1.8 * (1 - hz))));
      devices.push({
        device_id: `${c.pfx}-CHI-${100 + k}`, device_type: c.type, mars_device_category: c.cat,
        facility_id: `FAC-${String(1 + (k * 7) % 60).padStart(3, '0')}`,
        current_age_days: age, rul_days: rul,
        rul_p10: Math.max(2, Math.round(rul * 0.28)), rul_p90: Math.round(rul * 3.0),
        hazard_score: hz, risk_band: _riskBand(hz),
        days_since_hw_oos: age, roll_fail_30d: Math.round(_u(k * 2.3) * 3),
        is_overdue: age > Math.round(c.scale * 0.75), cv_cindex: c.cidx, gate_pass: false,
      });
    }
  });
  devices.sort((a, b) => a.rul_days - b.rul_days);
  return {
    is_sample: true, event_definition: 'hardware OOS (Set)', event_def_version: '2026-07-23.v1',
    floor: 0.65, window: 'telemetry-era (2024-01-01+)', devices,
    note: 'Per-device Remaining Useful Life on the hardware-OOS-Set event. Illustrative sample rows until the live '
      + 'v5.1 run + the /ps5/devices route populate real devices.',
  };
}

export function getPS5SerialHealth(city = 'CHI') {
  if (city !== 'CHI') return null;
  const COMP = ['CSC_READER', 'GATE_MECH', 'BILL_ACCEPTOR', 'COIN_MECH', 'DISPLAY', 'PRINTER'];
  const serials = [];
  _PS5_CATS.forEach((c, ci) => {
    for (let i = 0; i < 8; i++) {
      const k = ci * 8 + i;
      const age = Math.round(60 + _u(k * 1.3 + 0.5) * 2500);
      const fails = Math.round(_u(k * 0.7 + 0.2) * 11);
      const rs = Math.round((fails / Math.max(age, 1)) * 1e6) / 1e6;
      const rul = Math.max(5, Math.round(c.scale * (0.5 + 1.5 * (1 - Math.min(1, rs * 45)))));
      serials.push({
        device_id: `${c.pfx}-CHI-${100 + (i % 6)}`, serial: `SN-${c.cat.slice(0, 2)}-${10000 + k * 137}`,
        component_type: COMP[k % COMP.length], mars_device_category: c.cat,
        component_age_days: age, oos_failures: fails, risk_score: rs, risk_tier: _riskTier(rs),
        component_rul_days: rul, is_overdue: age > Math.round(c.scale * 0.9),
      });
    }
  });
  serials.sort((a, b) => b.risk_score - a.risk_score);
  return {
    is_sample: true, event_def_version: '2026-07-23.v1', serials,
    note: 'Per-serial component reliability on the hardware-OOS-Set event. Illustrative sample rows until '
      + 'gold.device_ps5_component is rebuilt on the OOS-Set event and the /ps5/serials route is live.',
  };
}
