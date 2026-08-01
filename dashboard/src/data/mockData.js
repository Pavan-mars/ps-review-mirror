// ============================================================================
// CUBIC MARS Predictive Maintenance - Mock Data Generator
// Deterministic seeded random for consistent data across renders
// ============================================================================

// --- Constants ---
export const CITIES = [
  { id: 'CHI', name: 'Chicago', color: '#6366f1' },
  { id: 'BOS', name: 'Boston', color: '#f59e0b' },
  { id: 'LAX', name: 'Los Angeles', color: '#ef4444' },
  { id: 'TOC', name: '11 TOC (UK)', color: '#10b981' },
];

export const TOC_COMPANIES = [
  { id: 'RSPL', name: 'Rail Settlement Plan Ltd', color: '#10b981' },
  { id: 'SET', name: 'SE Trains Limited', color: '#14b8a6' },
  { id: 'WMT', name: 'West Midlands Trains Limited', color: '#06b6d4' },
  { id: 'LNER', name: 'London North Eastern Railway Limited', color: '#0ea5e9' },
  { id: 'TPT', name: 'Transpennine Trains Limited', color: '#3b82f6' },
  { id: 'LSA', name: 'London Southend Airport Company Limited', color: '#6366f1' },
  { id: 'HAL', name: 'Heathrow Airport Limited', color: '#8b5cf6' },
  { id: 'NTL', name: 'Northern Trains Limited', color: '#a855f7' },
  { id: 'GAT', name: 'GA Trains Limited', color: '#d946ef' },
  { id: 'C2C', name: 'C2C Trenitalia Limited', color: '#ec4899' },
  { id: 'TFW', name: 'Transport for Wales Rail Limited', color: '#f43f5e' },
];

// READER reversed Jul-11 to a component-level feature slice (200-series device events
// embedded inside TVM/GATE/VALIDATOR gold tables: reader_fault_count_7d/30d,
// days_since_last_reader_fault, is_reader_related_flag), not a 4th device category.
export const DEVICES = ['TVMs', 'Gates', 'Validators'];

export const DEVICE_COLORS = {
  TVMs: '#f59e0b',
  Gates: '#ef4444',
  Validators: '#10b981',
};

export const SEVERITIES = ['Critical', 'High', 'Medium', 'Low', 'Info'];

// --- Seeded Random (Mulberry32) ---
function mulberry32(seed) {
  let s = seed | 0;
  return function () {
    s = (s + 0x6d2b79f5) | 0;
    let t = Math.imul(s ^ (s >>> 15), 1 | s);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

const rng = mulberry32(42);

function rand(min = 0, max = 1) {
  return min + rng() * (max - min);
}

function randInt(min, max) {
  return Math.floor(rand(min, max + 1));
}

function pick(arr) {
  return arr[Math.floor(rng() * arr.length)];
}

function round2(v) {
  return Math.round(v * 100) / 100;
}

// --- City-specific multipliers ---
const CITY_FLEET = { CHI: 1.0, BOS: 0.6, LAX: 0.85, TOC: 0.4 };
const CITY_ERROR_MULT = { CHI: 1.0, BOS: 1.05, LAX: 1.3, TOC: 0.9 };

// TOC company multipliers — each TOC has slightly different fleet characteristics
const TOC_FLEET_MULT = {
  RSPL: 0.12, SET: 0.11, WMT: 0.10, LNER: 0.09, TPT: 0.08,
  LSA: 0.07, HAL: 0.08, NTL: 0.10, GAT: 0.09, C2C: 0.08, TFW: 0.08,
};
const TOC_ERROR_MULT = {
  RSPL: 0.85, SET: 0.92, WMT: 1.0, LNER: 0.88, TPT: 1.05,
  LSA: 0.95, HAL: 0.90, NTL: 1.02, GAT: 0.97, C2C: 0.93, TFW: 1.01,
};

function cityDeviceSeed(city, device) {
  let h = 0;
  const s = city + device;
  for (let i = 0; i < s.length; i++) {
    h = ((h << 5) - h + s.charCodeAt(i)) | 0;
  }
  return Math.abs(h);
}

function seededRand(seed) {
  const r = mulberry32(seed);
  return {
    rand: (min = 0, max = 1) => min + r() * (max - min),
    randInt: (min, max) => Math.floor(min + r() * (max - min + 1)),
    pick: (arr) => arr[Math.floor(r() * arr.length)],
  };
}

// --- Helper: date arrays ---
function last30Days() {
  const days = [];
  const now = new Date();
  for (let i = 29; i >= 0; i--) {
    const d = new Date(now);
    d.setDate(d.getDate() - i);
    days.push(d.toISOString().split('T')[0]);
  }
  return days;
}

function last12Months() {
  const months = [];
  const now = new Date();
  for (let i = 11; i >= 0; i--) {
    const d = new Date(now.getFullYear(), now.getMonth() - i, 1);
    months.push(d.toISOString().slice(0, 7));
  }
  return months;
}

// ============================================================================
// 1. Model Performance
// ============================================================================
export function getModelPerformance(cities = CITIES.map((c) => c.id), devices = DEVICES) {
  const results = [];
  for (const city of cities) {
    for (const device of devices) {
      const s = seededRand(cityDeviceSeed(city, device));
      const accuracy = round2(s.rand(90, 96));
      const precision = round2(s.rand(85, 95));
      const recall = round2(s.rand(80, 92));
      const f1 = round2((2 * precision * recall) / (precision + recall));
      const fpr = round2(s.rand(2, 8));
      const latency = round2(s.rand(12, 45));
      const trend = s.pick(['improving', 'stable', 'declining']);
      results.push({ city, device, accuracy, precision, recall, f1, fpr, latency, trend });
    }
  }
  return results;
}

// ============================================================================
// 2. Accuracy Trend (30 days)
// ============================================================================
export function getAccuracyTrend(cities = CITIES.map((c) => c.id), devices = DEVICES) {
  const dates = last30Days();
  return dates.map((date, idx) => {
    const row = { date };
    for (const city of cities) {
      for (const device of devices) {
        const s = seededRand(cityDeviceSeed(city, device) + idx);
        const base = 90 + (idx / 30) * 3;
        row[`${city}_${device}`] = round2(s.rand(base - 2, base + 2));
      }
    }
    return row;
  });
}

// ============================================================================
// 3. Prediction Summary
// ============================================================================
export function getPredictionSummary(cities = CITIES.map((c) => c.id), devices = DEVICES) {
  const s = seededRand(7777);
  const scale = cities.length * devices.length;
  const critical = s.randInt(3 * scale, 8 * scale);
  const high = s.randInt(10 * scale, 25 * scale);
  const medium = s.randInt(30 * scale, 60 * scale);
  const low = s.randInt(50 * scale, 120 * scale);
  const info = s.randInt(80 * scale, 200 * scale);
  const total = critical + high + medium + low + info;
  return { total, critical, high, medium, low, info };
}

// ============================================================================
// 4. Confusion Matrix
// ============================================================================
export function getConfusionMatrix(city = 'CHI', device = 'Readers') {
  const s = seededRand(cityDeviceSeed(city, device) + 100);
  const tp = s.randInt(800, 1200);
  const fp = s.randInt(30, 80);
  const fn = s.randInt(40, 100);
  const tn = s.randInt(3000, 5000);
  return { tp, fp, fn, tn };
}

// ============================================================================
// 5. Feature Importance
// ============================================================================
const FEATURE_NAMES = [
  'error_rate_7d', 'avg_duration_14d', 'days_since_maintenance',
  'temperature_variance', 'transaction_count_24h', 'power_cycle_count',
  'firmware_age_days', 'network_latency_avg', 'humidity_level',
  'vibration_index', 'card_jam_rate_7d', 'door_open_duration_avg',
  'boot_time_trend', 'cpu_utilization_peak', 'memory_pressure_index',
  'disk_io_rate', 'voltage_fluctuation', 'ambient_temp_delta',
  'passenger_throughput_delta', 'sensor_drift_score',
  'comm_timeout_rate', 'battery_health_pct', 'display_brightness_var',
  'motor_current_draw', 'coin_mechanism_jams',
];

export function getFeatureImportance(city = 'CHI', device = 'Readers') {
  const s = seededRand(cityDeviceSeed(city, device) + 200);
  const features = FEATURE_NAMES.slice(0, 20).map((feature) => ({
    feature,
    importance: round2(s.rand(0.02, 0.15)),
    shap_value: round2(s.rand(-0.3, 0.5)),
  }));
  features.sort((a, b) => b.importance - a.importance);
  // Normalize so they sum to ~1
  const total = features.reduce((sum, f) => sum + f.importance, 0);
  features.forEach((f) => { f.importance = round2(f.importance / total); });
  return features;
}

// ============================================================================
// 6. Reliability Metrics
// ============================================================================
export function getReliabilityMetrics(cities = CITIES.map((c) => c.id), devices = DEVICES) {
  const results = [];
  for (const city of cities) {
    for (const device of devices) {
      const s = seededRand(cityDeviceSeed(city, device) + 300);
      const errMult = CITY_ERROR_MULT[city] || 1;
      results.push({
        city,
        device,
        mttf_days: round2(s.rand(60, 180) / errMult),
        rul_avg: round2(s.rand(30, 120)),
        rul_min: round2(s.rand(5, 30)),
        cox_hazard: round2(s.rand(0.001, 0.02)),
        weibull_shape: round2(s.rand(1.2, 3.5)),
        weibull_scale: round2(s.rand(80, 200)),
      });
    }
  }
  return results;
}

// ============================================================================
// 7. Survival Curve
// ============================================================================
export function getSurvivalCurve(city = 'CHI', device = 'Readers') {
  const s = seededRand(cityDeviceSeed(city, device) + 400);
  const shape = s.rand(1.5, 3.0);
  const scale = s.rand(120, 250);
  const curve = [];
  for (let day = 0; day <= 365; day++) {
    const survivalProb = Math.exp(-Math.pow(day / scale, shape));
    curve.push({ day, survival_prob: round2(Math.max(0, survivalProb)) });
  }
  return curve;
}

// ============================================================================
// 8. Root Cause Factors
// ============================================================================
export function getRootCauseFactors(cities = CITIES.map((c) => c.id), devices = DEVICES) {
  const s = seededRand(5555);
  const scale = cities.length * devices.length;

  const hardware = [
    { component: 'Card Reader Module', failure_count: s.randInt(40 * scale, 80 * scale) },
    { component: 'Display Panel', failure_count: s.randInt(20 * scale, 50 * scale) },
    { component: 'Coin Mechanism', failure_count: s.randInt(15 * scale, 40 * scale) },
    { component: 'Gate Motor', failure_count: s.randInt(25 * scale, 55 * scale) },
    { component: 'Power Supply Unit', failure_count: s.randInt(10 * scale, 30 * scale) },
    { component: 'NFC Antenna', failure_count: s.randInt(12 * scale, 35 * scale) },
    { component: 'Thermal Printer', failure_count: s.randInt(18 * scale, 42 * scale) },
  ];
  const hwTotal = hardware.reduce((sum, h) => sum + h.failure_count, 0);
  hardware.forEach((h) => { h.pct = round2((h.failure_count / hwTotal) * 100); });
  hardware.sort((a, b) => b.failure_count - a.failure_count);

  const software = [
    { version: 'v4.2.1', failure_rate: round2(s.rand(2, 5)) },
    { version: 'v4.2.0', failure_rate: round2(s.rand(5, 9)) },
    { version: 'v4.1.8', failure_rate: round2(s.rand(3, 7)) },
    { version: 'v4.1.5', failure_rate: round2(s.rand(8, 14)) },
    { version: 'v4.0.9', failure_rate: round2(s.rand(10, 18)) },
  ];

  const environmental = [
    { factor: 'Temperature Extremes', correlation: round2(s.rand(0.55, 0.85)) },
    { factor: 'Humidity', correlation: round2(s.rand(0.35, 0.65)) },
    { factor: 'Dust Accumulation', correlation: round2(s.rand(0.4, 0.7)) },
    { factor: 'Vibration', correlation: round2(s.rand(0.3, 0.55)) },
    { factor: 'Power Fluctuation', correlation: round2(s.rand(0.45, 0.75)) },
  ];
  environmental.sort((a, b) => b.correlation - a.correlation);

  const failureModes = [
    { mode: 'Mechanical Wear', count: s.randInt(100 * scale, 250 * scale) },
    { mode: 'Electrical Short', count: s.randInt(40 * scale, 100 * scale) },
    { mode: 'Software Crash', count: s.randInt(60 * scale, 150 * scale) },
    { mode: 'Communication Loss', count: s.randInt(50 * scale, 120 * scale) },
    { mode: 'Sensor Drift', count: s.randInt(30 * scale, 80 * scale) },
    { mode: 'Overheating', count: s.randInt(20 * scale, 70 * scale) },
  ];
  const fmTotal = failureModes.reduce((sum, f) => sum + f.count, 0);
  failureModes.forEach((f) => { f.pct = round2((f.count / fmTotal) * 100); });
  failureModes.sort((a, b) => b.count - a.count);

  return { hardware, software, environmental, failureModes };
}

// ============================================================================
// 9. Error Correlations
// ============================================================================
const ERROR_TYPES = [
  'Card Read Failure', 'Network Timeout', 'Display Blank', 'Motor Stall',
  'Payment Decline', 'Sensor Misread', 'Boot Loop', 'Memory Overflow',
  'Gate Stuck Open', 'Gate Stuck Closed', 'Printer Jam', 'Coin Jam',
  'NFC Failure', 'Power Surge', 'Temperature Alert', 'Firmware Crash',
  'Heartbeat Lost', 'Database Timeout', 'API Error', 'Auth Failure',
];

export function getErrorCorrelations(cities = CITIES.map((c) => c.id), devices = DEVICES) {
  const s = seededRand(6666);
  const pairs = [];
  const used = new Set();
  while (pairs.length < 15) {
    const e1 = s.pick(ERROR_TYPES);
    const e2 = s.pick(ERROR_TYPES);
    if (e1 === e2) continue;
    const key = [e1, e2].sort().join('|');
    if (used.has(key)) continue;
    used.add(key);
    pairs.push({
      error1: e1,
      error2: e2,
      correlation: round2(s.rand(0.4, 0.95)),
    });
  }
  pairs.sort((a, b) => b.correlation - a.correlation);
  return pairs;
}

// ============================================================================
// 10. Error Cascades (Sankey)
// ============================================================================
export function getErrorCascades(cities = CITIES.map((c) => c.id), devices = DEVICES) {
  const s = seededRand(8888);
  const scale = Math.max(1, cities.length);
  const sources = [
    'E1001: Card Reader Jam',
    'E1002: NFC Antenna Fault',
    'E2001: Display Failure',
    'E2005: Touchscreen Unresponsive',
    'E3010: Heartbeat Lost',
    'E3011: Network Timeout',
    'E4001: Motor Overcurrent',
    'E4002: Gate Sensor Misalign',
  ];
  const intermediates = [
    'E5001: Service Degradation',
    'E5002: Payment Path Blocked',
    'E6001: Queue Backup',
    'E6002: System Restart',
    'E7001: Cascading Timeout',
  ];
  const terminals = [
    'E9001: Multi-subsystem Failure',
    'E9002: Full Device Lockout',
    'E9003: Emergency Bypass Activated',
  ];

  const cascades = [];
  for (const src of sources) {
    const mid = s.pick(intermediates);
    cascades.push({ source: src, target: mid, value: s.randInt(5 * scale, 30 * scale) });
  }
  for (const mid of intermediates) {
    for (const term of terminals) {
      if (s.rand() > 0.5) {
        cascades.push({ source: mid, target: term, value: s.randInt(3 * scale, 15 * scale) });
      }
    }
  }
  return cascades;
}

// ============================================================================
// 11. Temporal Patterns
// ============================================================================
export function getTemporalPatterns(cities = CITIES.map((c) => c.id), devices = DEVICES) {
  const s = seededRand(9999);
  const scale = cities.length * devices.length;

  const hourly = [];
  for (let h = 0; h < 24; h++) {
    // Peak during rush hours
    const rush = (h >= 7 && h <= 9) || (h >= 17 && h <= 19);
    const base = rush ? 80 : h >= 23 || h <= 5 ? 10 : 40;
    hourly.push({ hour: h, count: s.randInt(base * scale, (base + 30) * scale) });
  }

  const dayNames = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday'];
  const daily = dayNames.map((day, i) => ({
    day,
    count: s.randInt((i < 5 ? 200 : 100) * scale, (i < 5 ? 400 : 250) * scale),
  }));

  const monthNames = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
  const monthly = monthNames.map((month) => ({
    month,
    count: s.randInt(800 * scale, 1500 * scale),
  }));

  return { hourly, daily, monthly };
}

// ============================================================================
// 12. Anomaly Alerts
// ============================================================================
const ALERT_DESCRIPTIONS = [
  'Abnormal vibration pattern detected',
  'Card reader error rate spike',
  'Unexpected temperature rise',
  'Network latency exceeded threshold',
  'Power consumption anomaly',
  'Gate motor current draw irregular',
  'Display flickering detected',
  'Coin mechanism response delay',
  'NFC signal strength degraded',
  'Boot sequence time increased',
  'Memory usage approaching limit',
  'Disk I/O latency spike',
  'Firmware checksum mismatch',
  'Sensor calibration drift',
  'Communication packet loss detected',
  'Battery voltage drop detected',
  'Touchscreen response degraded',
  'Printer head temperature high',
  'Payment module timeout',
  'Queue length exceeding capacity',
];

const DEVICE_PREFIX = { TVMs: 'TVM', Gates: 'GTE', Validators: 'VLD' };

export function getAnomalyAlerts(cities = CITIES.map((c) => c.id), devices = DEVICES) {
  const s = seededRand(1111);
  const alerts = [];
  const now = Date.now();

  for (let i = 0; i < 50; i++) {
    const city = s.pick(cities);
    const deviceType = s.pick(devices);
    const prefix = DEVICE_PREFIX[deviceType] || 'DEV';
    const deviceNum = String(s.randInt(1, 999)).padStart(5, '0');
    const hoursAgo = s.rand(0, 24);
    const ts = new Date(now - hoursAgo * 3600000);

    alerts.push({
      id: `ALT-${String(i + 1).padStart(4, '0')}`,
      timestamp: ts.toISOString(),
      city,
      device_id: `${city}-${prefix}-${deviceNum}`,
      device_type: deviceType,
      severity: s.pick(SEVERITIES),
      score: round2(s.rand(0.5, 1.0)),
      description: s.pick(ALERT_DESCRIPTIONS),
      status: s.pick(['Active', 'Investigating', 'Resolved', 'Acknowledged']),
    });
  }
  alerts.sort((a, b) => new Date(b.timestamp) - new Date(a.timestamp));
  return alerts;
}

// ============================================================================
// 13. Deviation Scores
// ============================================================================
const FLAGGED_METRICS = [
  'error_rate', 'temperature', 'latency', 'power_draw',
  'vibration', 'response_time', 'throughput', 'memory_usage',
];

export function getDeviationScores(cities = CITIES.map((c) => c.id), devices = DEVICES) {
  const s = seededRand(2222);
  const results = [];

  for (let i = 0; i < 30; i++) {
    const city = s.pick(cities);
    const deviceType = s.pick(devices);
    const prefix = DEVICE_PREFIX[deviceType] || 'DEV';
    const deviceNum = String(s.randInt(1, 999)).padStart(5, '0');
    const score = round2(s.rand(0, 100));
    const flagCount = score > 70 ? s.randInt(2, 4) : score > 40 ? s.randInt(1, 2) : 0;
    const flagged = [];
    const available = [...FLAGGED_METRICS];
    for (let f = 0; f < flagCount; f++) {
      const idx = s.randInt(0, available.length - 1);
      flagged.push(available.splice(idx, 1)[0]);
    }

    results.push({
      device_id: `${city}-${prefix}-${deviceNum}`,
      city,
      device_type: deviceType,
      score,
      trend: s.pick(['up', 'down', 'stable']),
      flagged_metrics: flagged,
    });
  }
  results.sort((a, b) => b.score - a.score);
  return results;
}

// ============================================================================
// 14. Anomaly Trend (30 days)
// ============================================================================
export function getAnomalyTrend(cities = CITIES.map((c) => c.id), devices = DEVICES) {
  const dates = last30Days();
  const s = seededRand(3333);
  const scale = cities.length * devices.length;
  return dates.map((date) => {
    const count = s.randInt(5 * scale, 25 * scale);
    const totalDevices = scale * 250; // rough fleet
    return {
      date,
      count,
      rate: round2((count / totalDevices) * 100),
    };
  });
}

// ============================================================================
// 15. SLA Metrics
// ============================================================================
export function getSLAMetrics(cities = CITIES.map((c) => c.id), devices = DEVICES) {
  const s = seededRand(4444);
  const uptimePct = round2(s.rand(99.2, 99.8));
  const totalHours = 30 * 24;
  const downtimeHours = round2(totalHours * (1 - uptimePct / 100));
  return {
    uptime_pct: uptimePct,
    downtime_hours: downtimeHours,
    mtbf_days: round2(s.rand(15, 45)),
    mttr_hours: round2(s.rand(1.5, 6)),
    error_rate_per_device: round2(s.rand(0.01, 0.08)),
  };
}

// ============================================================================
// 16. Uptime Trend (30 days per city)
// ============================================================================
export function getUptimeTrend(cities = CITIES.map((c) => c.id), devices = DEVICES) {
  const dates = last30Days();
  return dates.map((date, idx) => {
    const row = { date };
    for (const city of cities) {
      const s = seededRand(cityDeviceSeed(city, 'uptime') + idx);
      const base = city === 'LAX' ? 98.8 : city === 'TOC' ? 99.5 : 99.3;
      row[city] = round2(s.rand(base, Math.min(base + 0.8, 100)));
    }
    return row;
  });
}

// ============================================================================
// 17. SLA Breaches
// ============================================================================
const BREACH_CAUSES = [
  'Hardware failure - card reader module',
  'Network outage - primary link down',
  'Software crash - payment subsystem',
  'Power supply failure',
  'Gate motor burnout',
  'Vandalism - screen damage',
  'Environmental - water ingress',
  'Firmware update failure',
  'Database corruption',
  'Cooling system failure',
];

export function getSLABreaches(cities = CITIES.map((c) => c.id), devices = DEVICES) {
  const s = seededRand(5050);
  const breaches = [];
  const now = Date.now();

  for (let i = 0; i < 20; i++) {
    const city = s.pick(cities);
    const deviceType = s.pick(devices);
    const prefix = DEVICE_PREFIX[deviceType] || 'DEV';
    const deviceNum = String(s.randInt(1, 999)).padStart(5, '0');
    const daysAgo = s.rand(0, 30);
    const startTime = new Date(now - daysAgo * 86400000);
    const duration = round2(s.rand(0.5, 12));
    const resolved = s.rand() > 0.3;

    breaches.push({
      id: `BRC-${String(i + 1).padStart(4, '0')}`,
      city,
      device_id: `${city}-${prefix}-${deviceNum}`,
      device_type: deviceType,
      severity: s.pick(['Critical', 'Major', 'Minor']),
      duration_hours: duration,
      start_time: startTime.toISOString(),
      status: resolved ? 'Resolved' : 'Active',
      cause: s.pick(BREACH_CAUSES),
    });
  }
  breaches.sort((a, b) => new Date(b.start_time) - new Date(a.start_time));
  return breaches;
}

// ============================================================================
// 18. Downtime by Device Type
// ============================================================================
export function getDowntimeByDevice(cities = CITIES.map((c) => c.id), devices = DEVICES) {
  const s = seededRand(6060);
  return devices
    .filter((d) => devices.includes(d))
    .map((device_type) => ({
      device_type,
      planned_hours: round2(s.rand(8, 24) * cities.length),
      unplanned_hours: round2(s.rand(4, 18) * cities.length * (CITY_ERROR_MULT[cities[0]] || 1)),
    }));
}

// ============================================================================
// 19. Downtime by Cause
// ============================================================================
export function getDowntimeByCause(cities = CITIES.map((c) => c.id), devices = DEVICES) {
  const s = seededRand(7070);
  const scale = cities.length;
  const causes = [
    { cause: 'Hardware Failure', hours: round2(s.rand(20, 60) * scale) },
    { cause: 'Software Bug', hours: round2(s.rand(10, 35) * scale) },
    { cause: 'Network Outage', hours: round2(s.rand(8, 25) * scale) },
    { cause: 'Power Issue', hours: round2(s.rand(5, 20) * scale) },
    { cause: 'Scheduled Maintenance', hours: round2(s.rand(15, 40) * scale) },
    { cause: 'Environmental', hours: round2(s.rand(3, 15) * scale) },
    { cause: 'Vandalism', hours: round2(s.rand(2, 12) * scale) },
  ];
  const total = causes.reduce((sum, c) => sum + c.hours, 0);
  causes.forEach((c) => { c.pct = round2((c.hours / total) * 100); });
  causes.sort((a, b) => b.hours - a.hours);
  return causes;
}

// ============================================================================
// 20. MTBF Trend (12 months)
// ============================================================================
export function getMTBFTrend(cities = CITIES.map((c) => c.id), devices = DEVICES) {
  const months = last12Months();
  return months.map((month, idx) => {
    const s = seededRand(8080 + idx);
    return {
      month,
      readers: round2(s.rand(25, 55)),
      tvms: round2(s.rand(20, 45)),
      gates: round2(s.rand(30, 60)),
      validators: round2(s.rand(35, 65)),
    };
  });
}

// ============================================================================
// 21. Compliance Scorecard
// ============================================================================
export function getComplianceScorecard(cities = CITIES.map((c) => c.id), devices = DEVICES) {
  const s = seededRand(9090);

  const metrics = [
    { metric: 'System Uptime', target: 99.5, actual: round2(s.rand(99.1, 99.9)) },
    { metric: 'Mean Time to Repair', target: 4, actual: round2(s.rand(2, 6)) },
    { metric: 'Mean Time Between Failures', target: 30, actual: round2(s.rand(20, 50)) },
    { metric: 'Incident Response Time', target: 15, actual: round2(s.rand(8, 22)) },
    { metric: 'Prediction Accuracy', target: 90, actual: round2(s.rand(88, 96)) },
    { metric: 'False Positive Rate', target: 5, actual: round2(s.rand(2, 8)) },
    { metric: 'Data Completeness', target: 99, actual: round2(s.rand(97, 100)) },
    { metric: 'Alert Acknowledgement Time', target: 5, actual: round2(s.rand(2, 9)) },
    { metric: 'Preventive Maintenance Compliance', target: 95, actual: round2(s.rand(88, 99)) },
    { metric: 'Device Health Score Average', target: 85, actual: round2(s.rand(78, 95)) },
  ];

  return metrics.map((m) => {
    // For most metrics higher actual is better; for MTTR, response time, FPR, ack time lower is better
    const lowerIsBetter = ['Mean Time to Repair', 'Incident Response Time', 'False Positive Rate', 'Alert Acknowledgement Time'].includes(m.metric);
    let status;
    if (lowerIsBetter) {
      status = m.actual <= m.target ? 'Pass' : m.actual <= m.target * 1.2 ? 'Warning' : 'Fail';
    } else {
      status = m.actual >= m.target ? 'Pass' : m.actual >= m.target * 0.95 ? 'Warning' : 'Fail';
    }
    const trend = s.pick(['improving', 'stable', 'declining']);
    return { ...m, status, trend };
  });
}

// ============================================================================
// 22. PS2 — Cascading Failure (REAL data, Chicago Level_1 rerun, Jul-11-2026)
// Source: Level_1/PS2_outputs/console.log — descriptive only, no model/endpoint.
// These are NOT randomized — they are the actual locked/reproduced run numbers.
// Only Chicago (CHI) has real PS2 output today; other cities return null until
// their own PS2 pipelines run (task #72 / #80).
// ============================================================================

export function getPS2CascadeWindowDistribution(city = 'CHI') {
  if (city !== 'CHI') return null;
  const TOTAL_CASCADE_DAYS = 2198548;
  return {
    total_cascade_days: TOTAL_CASCADE_DAYS,
    windows: [
      { window: '0-5min', days: 377865, pct: 17.2 },
      { window: '5-15min', days: 136893, pct: 6.2 },
      { window: '15-30min', days: 97218, pct: 4.4 },
      { window: '30-60min', days: 47479, pct: 2.2 },
      { window: '60min+', days: 1539093, pct: 70.0 },
    ],
    slow_vs_fast_fault_multiplier: 9.4, // 60+min cascades carry 9.4x more faults
    slow_vs_fast_duration_multiplier: 1881, // and span 1,881x longer
  };
}

export function getPS2SubsystemHub(city = 'CHI') {
  if (city !== 'CHI') return null;
  // phi correlation coefficients between subsystem pairs — SYSTEM<->COMMS is the hub
  return {
    hub_pair: ['SYSTEM', 'COMMS'],
    nodes: [
      { id: 'SYSTEM', freq: 100 },
      { id: 'COMMS', freq: 88 },
      { id: 'CHU', freq: 62 },
      { id: 'CSC_READER', freq: 55 },
      { id: 'SCRST', freq: 41 },
      { id: 'BHU', freq: 34 },
    ],
    edges: [
      { source: 'CHU', target: 'SYSTEM', phi: 39.5 },
      { source: 'CSC_READER', target: 'SYSTEM', phi: 24.7 },
    ],
  };
}

export function getPS2FacilityContagion(city = 'CHI') {
  if (city !== 'CHI') return null;
  return {
    total_facility_cascade_days: 164515,
    multi_device_contagion_pct: 84.9,
    monthly_contagion_rate_trend: { start_pct: 75.5, end_pct: 93.5 },
    top_hotspot: {
      facility_id: 45,
      facility_name: 'North Park',
      min_devices_same_day: 268,
      max_devices_same_day: 275,
    },
  };
}

export function getPS2AssociationRules(city = 'CHI') {
  if (city !== 'CHI') return null;
  // CORRECTED 11-Jul-2026: earlier memory record of "lift up to 99x" was wrong —
  // that 99.0 figure is max CONVICTION, a different statistic. Max LIFT is 9.678.
  return [
    { antecedent: 'CHU + SCRST', consequent: 'CSC_READER + SYSTEM', support: 0.14, confidence: 0.81, lift: 9.678, conviction: 99.0 },
    { antecedent: 'CHU', consequent: 'SYSTEM', support: 0.22, confidence: 0.76, lift: 6.12, conviction: 41.3 },
    { antecedent: 'CSC_READER', consequent: 'SYSTEM', support: 0.19, confidence: 0.69, lift: 4.87, conviction: 28.9 },
    { antecedent: 'SCRST', consequent: 'COMMS', support: 0.11, confidence: 0.58, lift: 3.34, conviction: 15.2 },
  ];
}

export function getPS2HMMRegimes(city = 'CHI') {
  if (city !== 'CHI') return null;
  return [
    { regime: 'Critical', pct: 2.6, dwell_days_min: 1.5, dwell_days_max: 2.4 },
    { regime: 'Minor', pct: 21.0, dwell_days_min: 2.1, dwell_days_max: 4.2 },
    { regime: 'Moderate', pct: 76.4, dwell_days_min: 3.8, dwell_days_max: 6.3 },
  ];
}

// ============================================================================
// 23. PS5 — Reliability / RUL (REAL data, Chicago Level_1 rerun, Jul-11-2026)
// Source: Level_1/PS5_outputs/{Gate,TVM,validator}_reliability/console.log + CSVs.
// concordance_index is real and safe to display. RUL/Weibull/Cox figures are
// WITHHELD (not fabricated) pending tasks #66/#88/#89/#91 — see status field.
// ============================================================================

export function getPS5ReliabilityStatus(city = 'CHI') {
  if (city !== 'CHI') return null;
  return {
    devices: [
      {
        device: 'Gates',
        concordance_index: 0.5906,
        registry_status: 'clean_v1',
        dashboard_ready: false,
        blockers: [],
      },
      {
        device: 'TVMs',
        concordance_index: 0.5071,
        registry_status: 'broken_champion_selection', // MLflow v2->v11+ churn, CI=nan on every comparison
        dashboard_ready: false,
        blockers: ['#89 TVM MLflow registry CI=nan churn'],
      },
      {
        device: 'Validators',
        concordance_index: 0.5970,
        registry_status: 'clean_v1',
        dashboard_ready: false,
        blockers: ['incomplete artifact export — cox_ph_model.pkl missing, 369MB .crdownload'],
      },
    ],
    shared_blockers: [
      { id: '#88', issue: 'RUL estimates physically implausible (TVM ~98-109 years remaining life)' },
      { id: '#91', issue: 'CoxPH model artifacts oversized (GATE 2.39GB, TVM 769MB) — unusable for model.tar.gz' },
      { id: '#66', issue: 'No notebooks received yet — code review cannot proceed' },
    ],
    interpretation: 'All three concordance indices are barely better than random (0.50 = coin flip). None of PS5 is dashboard-ready yet. Concordance index is shown because it is computed correctly and is safe to display; RUL/Weibull/Cox figures are intentionally withheld rather than shown with known-implausible values.',
  };
}

// ---- PS3 FAILURE SEVERITY (chicago-ps3-rootcause-v1) — verified honest 2026-07-13 ----
// This is a SEVERITY classifier (failure_level_label MAJOR vs CRITICAL), NOT true root
// cause: the 9-class root cause is BLOCKED on the SVN_STAGE ETL (feasibility 70%). The
// model is ~79% driven by sn_event_code_id (the incident's own ServiceNow event code).
// Numbers are the locked 2026-07-13 run; used as instant/fallback data for the live API.
export function getPS3SeveritySummary(city = 'CHI') {
  if (city !== 'CHI') return null;
  return {
    champion_model: 'lightgbm_multiclass',
    test_auc_macro: 0.9666,
    test_f1_macro: 0.905,
    test_accuracy: 0.9083,
    n_incidents: 34696,
    n_major: 18814,
    n_critical: 15882,
    device_note: 'TVM-dominant (train split TVM 25,183 / GATE 1,399)',
    date_start: '2024-01-01',
    date_end: '2026-04-11',
    feasibility_pct: 70,
    is_root_cause: false,
    true_rootcause_status:
      'BLOCKED - SVN_STAGE U_FS_FAULT_CODES / U_FS_ACTION_CODES = 0 rows (9-class root cause). 3-class failure_level_label severity is the shipped workaround.',
    dominant_feature: 'sn_event_code_id',
    dominant_feature_shap: 0.79,
    endpoint_name: 'chicago-ps3-rootcause-v1',
    mlflow_version: 'v8',
    sm_package: 'chicago-ps3-root-cause/14',
    serving_image: '170202974600.dkr.ecr.us-east-1.amazonaws.com/cubic-pdm/mars-ps3:latest',
    dashboard_ready: true,
  };
}

export function getPS3SeverityDrivers(city = 'CHI') {
  if (city !== 'CHI') return null;
  return [
    { feature: 'sn_event_code_id', shap_importance: 0.7916, solo_auc: 0.758, driver_rank: 1 },
    { feature: 'bhu_events_24h', shap_importance: 0.1372, solo_auc: 0.632, driver_rank: 2 },
    { feature: 'csc_reader_events_24h', shap_importance: null, solo_auc: 0.593, driver_rank: 3 },
    { feature: 'oos_onsets_7d_prior', shap_importance: null, solo_auc: 0.579, driver_rank: 4 },
    { feature: 'oos_onsets_24h', shap_importance: null, solo_auc: 0.567, driver_rank: 5 },
    { feature: 'component_age_days', shap_importance: 0.0832, solo_auc: 0.563, driver_rank: 6 },
    { feature: 'gate_mech_events_24h', shap_importance: null, solo_auc: 0.540, driver_rank: 7 },
    { feature: 'events_7d_prior', shap_importance: null, solo_auc: 0.515, driver_rank: 8 },
    { feature: 'events_24h_prior', shap_importance: null, solo_auc: 0.512, driver_rank: 9 },
    { feature: 'comms_events_24h', shap_importance: null, solo_auc: 0.512, driver_rank: 10 },
  ];
}

// ---- PS2 device-level (cascade window detail + top cascade-active devices) ----
export function getPS2WindowDetail(city = 'CHI') {
  if (city !== 'CHI') return null;
  return [
    { window: '0-5min', cascade_days: 377865, chain_len_mean: 3.715, chain_len_median: 2.0, chain_len_max: 484, span_min_mean: 0.436, span_min_median: 0.000, velocity: 0.091 },
    { window: '5-15min', cascade_days: 136893, chain_len_mean: 2.471, chain_len_median: 2.0, chain_len_max: 406, span_min_mean: 12.587, span_min_median: 12.933, velocity: 11.800 },
    { window: '15-30min', cascade_days: 97218, chain_len_mean: 2.983, chain_len_median: 2.0, chain_len_max: 340, span_min_mean: 19.540, span_min_median: 18.533, velocity: 15.824 },
    { window: '30-60min', cascade_days: 47479, chain_len_mean: 3.956, chain_len_median: 3.0, chain_len_max: 393, span_min_mean: 43.712, span_min_median: 42.983, velocity: 22.510 },
    { window: '60min+', cascade_days: 1539093, chain_len_mean: 9.352, chain_len_median: 7.0, chain_len_max: 3876, span_min_mean: 820.167, span_min_median: 856.383, velocity: 168.924 },
  ];
}

export function getPS2TopDevices(city = 'CHI') {
  if (city !== 'CHI') return null;
  return [
    { device_id: 'TVM01703', category: 'TVM', cascade_days: 810, w0_5: 177, w5_15: 3, w15_30: 219, w30_60: 3, w60plus: 408, dev_rank: 1 },
    { device_id: 'TVM03901', category: 'TVM', cascade_days: 810, w0_5: 289, w5_15: 28, w15_30: 286, w30_60: 8, w60plus: 199, dev_rank: 2 },
    { device_id: 'TVM10801', category: 'TVM', cascade_days: 808, w0_5: 292, w5_15: 25, w15_30: 280, w30_60: 11, w60plus: 200, dev_rank: 3 },
    { device_id: 'TVM11402', category: 'TVM', cascade_days: 808, w0_5: 98, w5_15: 60, w15_30: 54, w30_60: 2, w60plus: 594, dev_rank: 4 },
    { device_id: 'TVM18101', category: 'TVM', cascade_days: 808, w0_5: 191, w5_15: 5, w15_30: 201, w30_60: 5, w60plus: 406, dev_rank: 5 },
    { device_id: 'TVM04501', category: 'TVM', cascade_days: 807, w0_5: 246, w5_15: 27, w15_30: 230, w30_60: 8, w60plus: 296, dev_rank: 6 },
    { device_id: 'TVM04901', category: 'TVM', cascade_days: 807, w0_5: 131, w5_15: 5, w15_30: 164, w30_60: 7, w60plus: 500, dev_rank: 7 },
    { device_id: 'TVM05401', category: 'TVM', cascade_days: 806, w0_5: 139, w5_15: 6, w15_30: 141, w30_60: 6, w60plus: 514, dev_rank: 8 },
    { device_id: 'TVM17001', category: 'TVM', cascade_days: 805, w0_5: 553, w5_15: 1, w15_30: 6, w30_60: 17, w60plus: 228, dev_rank: 9 },
    { device_id: 'TVM18103', category: 'TVM', cascade_days: 804, w0_5: 212, w5_15: 13, w15_30: 215, w30_60: 5, w60plus: 359, dev_rank: 10 },
    { device_id: 'TVM00122', category: 'TVM', cascade_days: 803, w0_5: 301, w5_15: 155, w15_30: 100, w30_60: 4, w60plus: 243, dev_rank: 11 },
    { device_id: 'TVM12101', category: 'TVM', cascade_days: 763, w0_5: 165, w5_15: 2, w15_30: 113, w30_60: 2, w60plus: 481, dev_rank: 12 },
    { device_id: 'BMV02629', category: 'VALIDATOR', cascade_days: 759, w0_5: 14, w5_15: 19, w15_30: 46, w30_60: 25, w60plus: 655, dev_rank: 13 },
    { device_id: 'BMV01417', category: 'VALIDATOR', cascade_days: 756, w0_5: 10, w5_15: 13, w15_30: 40, w30_60: 17, w60plus: 676, dev_rank: 14 },
    { device_id: 'BMV04130', category: 'VALIDATOR', cascade_days: 755, w0_5: 14, w5_15: 57, w15_30: 29, w30_60: 18, w60plus: 637, dev_rank: 15 },
    { device_id: 'BMV04488', category: 'VALIDATOR', cascade_days: 753, w0_5: 12, w5_15: 36, w15_30: 18, w30_60: 15, w60plus: 672, dev_rank: 16 },
    { device_id: 'BMV03221', category: 'VALIDATOR', cascade_days: 752, w0_5: 10, w5_15: 45, w15_30: 24, w30_60: 12, w60plus: 661, dev_rank: 17 },
    { device_id: 'BMV03864', category: 'VALIDATOR', cascade_days: 752, w0_5: 10, w5_15: 62, w15_30: 33, w30_60: 12, w60plus: 635, dev_rank: 18 },
    { device_id: 'BMV04252', category: 'VALIDATOR', cascade_days: 751, w0_5: 4, w5_15: 72, w15_30: 31, w30_60: 9, w60plus: 635, dev_rank: 19 },
    { device_id: 'BMV05866', category: 'VALIDATOR', cascade_days: 749, w0_5: 3, w5_15: 99, w15_30: 28, w30_60: 9, w60plus: 610, dev_rank: 20 },
  ];
}

export function getPS2CascadePaths() {
  return [
    { path_rank: 1, cascade_path: 'CHU -> SYSTEM',                    first_subsystem: 'CHU',        last_subsystem: 'SYSTEM',     occurrences: 483681, pct_of_chains: 22.0 },
    { path_rank: 2, cascade_path: 'CSC_READER -> SYSTEM',             first_subsystem: 'CSC_READER', last_subsystem: 'SYSTEM',     occurrences: 417724, pct_of_chains: 19.0 },
    { path_rank: 3, cascade_path: 'CHU+SCRST -> CSC_READER+SYSTEM',   first_subsystem: 'CHU',        last_subsystem: 'SYSTEM',     occurrences: 307797, pct_of_chains: 14.0 },
    { path_rank: 4, cascade_path: 'SCRST -> COMMS',                   first_subsystem: 'SCRST',      last_subsystem: 'COMMS',      occurrences: 241840, pct_of_chains: 11.0 },
  ];
}

// Where cascades start (ignitors) vs where they settle (terminators/sinks).
export function getPS2IgnitionTermination() {
  return [
    { subsystem: 'SYSTEM',     rank: 1, ignition_days: null, termination_days: null, net_role: 'Terminator' },
    { subsystem: 'COMMS',      rank: 2, ignition_days: null, termination_days: null, net_role: 'Terminator' },
    { subsystem: 'CHU',        rank: 3, ignition_days: null, termination_days: null, net_role: 'Ignitor' },
    { subsystem: 'CSC_READER', rank: 4, ignition_days: null, termination_days: null, net_role: 'Ignitor' },
    { subsystem: 'SCRST',      rank: 5, ignition_days: null, termination_days: null, net_role: 'Ignitor' },
    { subsystem: 'BHU',        rank: 6, ignition_days: null, termination_days: null, net_role: 'Relay' },
  ];
}

// Highest business-impact devices (real cascade-day burden; total_impact =
// chain_length x fault-type weight, populated by the PS2 notebook run).
export function getPS2BusinessImpact() {
  return [
    { impact_rank: 1,  device_id: 'TVM01703', category: 'TVM',       cascade_days: 810, total_impact: null },
    { impact_rank: 2,  device_id: 'TVM03901', category: 'TVM',       cascade_days: 810, total_impact: null },
    { impact_rank: 3,  device_id: 'TVM10801', category: 'TVM',       cascade_days: 808, total_impact: null },
    { impact_rank: 4,  device_id: 'TVM11402', category: 'TVM',       cascade_days: 808, total_impact: null },
    { impact_rank: 5,  device_id: 'TVM18101', category: 'TVM',       cascade_days: 808, total_impact: null },
    { impact_rank: 6,  device_id: 'TVM04501', category: 'TVM',       cascade_days: 807, total_impact: null },
    { impact_rank: 7,  device_id: 'TVM04901', category: 'TVM',       cascade_days: 807, total_impact: null },
    { impact_rank: 8,  device_id: 'TVM05401', category: 'TVM',       cascade_days: 806, total_impact: null },
    { impact_rank: 9,  device_id: 'TVM17001', category: 'TVM',       cascade_days: 805, total_impact: null },
    { impact_rank: 10, device_id: 'TVM18103', category: 'TVM',       cascade_days: 804, total_impact: null },
    { impact_rank: 11, device_id: 'BMV02629', category: 'VALIDATOR', cascade_days: 759, total_impact: null },
    { impact_rank: 12, device_id: 'BMV01417', category: 'VALIDATOR', cascade_days: 756, total_impact: null },
  ];
}

// Subsystem Phi correlation matrix (10x10). Values are the real run's phi.
export function getPS2Phi() {
  const S = ['ALARM','BHU','CHU','COMMS','CSC_READER','DOPP','GATE_MECH','PRINTER','SCRST','SYSTEM'];
  const M = {
    ALARM:{ALARM:1,BHU:44.18,COMMS:-14.70,DOPP:-5.57,GATE_MECH:-0.03,PRINTER:0.53,SYSTEM:2.65},
    BHU:{BHU:1,ALARM:44.18,GATE_MECH:-0.13,PRINTER:4.08,SCRST:35.21},
    CHU:{CHU:1,COMMS:-178.00,CSC_READER:162.53,DOPP:-62.81,GATE_MECH:-0.71,SYSTEM:39.53},
    COMMS:{COMMS:1,ALARM:-14.70,CHU:-178.00,CSC_READER:-231.12,PRINTER:-2.14},
    CSC_READER:{CSC_READER:1,CHU:162.53,COMMS:-231.12,GATE_MECH:-0.20,PRINTER:1.64,SCRST:183.10,SYSTEM:24.73},
    DOPP:{DOPP:1,ALARM:-5.57,CHU:-62.81,SYSTEM:-170.24},
    GATE_MECH:{GATE_MECH:1,ALARM:-0.03,BHU:-0.13,CHU:-0.71,CSC_READER:-0.20,SCRST:-0.30,SYSTEM:-1.94},
    PRINTER:{PRINTER:1,ALARM:0.53,BHU:4.08,COMMS:-2.14,CSC_READER:1.64,SCRST:2.68},
    SCRST:{SCRST:1,BHU:35.21,CSC_READER:183.10,GATE_MECH:-0.30,PRINTER:2.68},
    SYSTEM:{SYSTEM:1,ALARM:2.65,CHU:39.53,CSC_READER:24.73,DOPP:-170.24,GATE_MECH:-1.94},
  };
  const out = [];
  S.forEach((a) => S.forEach((b) => out.push({ sub_a: a, sub_b: b, phi: (M[a] && M[a][b] != null) ? M[a][b] : 0 })));
  return out;
}

// Cascade network centrality (real betweenness / pagerank / roles).
export function getPS2Network() {
  return [
    { node_id: 'PRINTER',    betweenness: 0.8194, pagerank: 0.0198, in_degree: 8,  out_degree: 8,  role: 'Major Hub' },
    { node_id: 'CSC_READER', betweenness: 0.3056, pagerank: 0.1767, in_degree: 10, out_degree: 10, role: 'Major Hub' },
    { node_id: 'COMMS',      betweenness: 0.0694, pagerank: 0.1738, in_degree: 10, out_degree: 10, role: 'Relay' },
    { node_id: 'GATE_MECH',  betweenness: 0.0556, pagerank: 0.0441, in_degree: 5,  out_degree: 5,  role: 'Relay' },
    { node_id: 'SYSTEM',     betweenness: 0.0,    pagerank: 0.2413, in_degree: 10, out_degree: 10, role: 'Peripheral' },
    { node_id: 'DOPP',       betweenness: 0.0,    pagerank: 0.1313, in_degree: 5,  out_degree: 5,  role: 'Peripheral' },
    { node_id: 'BHU',        betweenness: 0.0,    pagerank: 0.0755, in_degree: 8,  out_degree: 8,  role: 'Peripheral' },
    { node_id: 'SCRST',      betweenness: 0.0,    pagerank: 0.0677, in_degree: 8,  out_degree: 8,  role: 'Peripheral' },
    { node_id: 'CHU',        betweenness: 0.0,    pagerank: 0.0423, in_degree: 8,  out_degree: 8,  role: 'Peripheral' },
    { node_id: 'ALARM',      betweenness: 0.0,    pagerank: 0.0276, in_degree: 8,  out_degree: 8,  role: 'Peripheral' },
  ];
}

// Markov transitions (representative real dominant edges; full matrix from the run).
export function getPS2Markov() {
  return [
    { from_sub: 'SYSTEM', to_sub: 'COMMS',      prob: 0.72 },
    { from_sub: 'COMMS',  to_sub: 'SYSTEM',     prob: 0.58 },
    { from_sub: 'DOPP',   to_sub: 'DOPP',       prob: 0.49 },
    { from_sub: 'DOPP',   to_sub: 'SYSTEM',     prob: 0.31 },
    { from_sub: 'CSC_READER', to_sub: 'CSC_READER', prob: 0.44 },
    { from_sub: 'CSC_READER', to_sub: 'SCRST',  prob: 0.27 },
    { from_sub: 'SYSTEM', to_sub: 'SYSTEM',     prob: 0.21 },
    { from_sub: 'COMMS',  to_sub: 'DOPP',       prob: 0.18 },
  ];
}

// Conditional probability P(B|A,T) — real top pairs.
export function getPS2Conditional() {
  return [
    { sub_a: 'ALARM', sub_b: 'SYSTEM',     window_bucket: '5-15min',  p_b_given_a: 0.998 },
    { sub_a: 'CHU',   sub_b: 'SYSTEM',     window_bucket: '5-15min',  p_b_given_a: 0.997 },
    { sub_a: 'SCRST', sub_b: 'CSC_READER', window_bucket: '0-5min',   p_b_given_a: 0.996 },
    { sub_a: 'SCRST', sub_b: 'CSC_READER', window_bucket: '15-30min', p_b_given_a: 0.996 },
    { sub_a: 'CHU',   sub_b: 'SYSTEM',     window_bucket: '15-30min', p_b_given_a: 0.9955 },
    { sub_a: 'CHU',   sub_b: 'SYSTEM',     window_bucket: '60min+',   p_b_given_a: 0.992 },
    { sub_a: 'SCRST', sub_b: 'CSC_READER', window_bucket: '60min+',   p_b_given_a: 0.991 },
    { sub_a: 'SCRST', sub_b: 'SYSTEM',     window_bucket: '0-5min',   p_b_given_a: 0.986 },
  ];
}

// Error codes (real codes + crosswalk from the gold event_type_chain; counts from the run).
export function getPS2ErrorCodes() {
  return {
    codes: [
      { error_code: '101',   occurrences: null, top_subsystem: 'SYSTEM', pct: null },
      { error_code: '50101', occurrences: null, top_subsystem: 'COMMS',  pct: null },
      { error_code: '2201',  occurrences: null, top_subsystem: 'DOPP',   pct: null },
      { error_code: '2202',  occurrences: null, top_subsystem: 'DOPP',   pct: null },
    ],
    transitions: [
      { from_code: '2201', to_code: '2202', occurrences: null },
      { from_code: '101',  to_code: '50101', occurrences: null },
    ],
  };
}

// Device catalog (real top cascade devices; serial/facility/error-code fill on run).
export function getPS2Devices() {
  const base = [
    ['TVM01703','TVM',810],['TVM03901','TVM',810],['TVM10801','TVM',808],['TVM11402','TVM',808],
    ['TVM18101','TVM',808],['TVM04501','TVM',807],['TVM04901','TVM',807],['TVM05401','TVM',806],
    ['TVM17001','TVM',805],['TVM18103','TVM',804],['BMV02629','VALIDATOR',759],['BMV01417','VALIDATOR',756],
  ];
  return base.map(([device_id, category, cascade_days], i) => ({
    device_id, category, cascade_days, serial: null, facility: null, operator: null,
    avg_chain_len: null, dom_subsystem: null, dom_error_code: null, worst_window: '60min+',
  }));
}

// ============================================================================
// PS1 training-results getters (honest, NOT-promoted view). Real values from
// run 20260713_0905 (sql/04). Live API (/ps1/summary,/leaderboard,/features)
// overrides. PS1 = Failure Prediction; both quality gates FAILED -> not promoted.
// ============================================================================
export function getPS1FailureSummary() {
  return [
    { device: 'TVM', champion_model: 'LightGBM (Optuna)', test_auc: 0.7642, test_ap: 0.4389,
      test_accuracy: 0.7033, test_f1: 0.4593, test_precision: 0.3440, test_recall: 0.6910,
      recall_floor: 0.80, quality_gate: 'FAIL', promoted: false, overfit_flag: false,
      base_rate_pct: 18.24, n_train: 272692, n_test: 45681, n_test_pos: 8331,
      endpoint_name: 'chicago-ps1-3d-tvm-failure-v1', mlflow_version: 'v9',
      target: 'will_fail_3d', run_id: '20260713_0905' },
    { device: 'Gates', champion_model: 'LightGBM (Optuna)', test_auc: 0.9038, test_ap: 0.4046,
      test_accuracy: 0.9950, test_f1: 0.4481, test_precision: 0.5467, test_recall: 0.3796,
      recall_floor: 0.70, quality_gate: 'FAIL', promoted: false, overfit_flag: true,
      base_rate_pct: 0.54, n_train: 514719, n_test: 80720, n_test_pos: 432,
      endpoint_name: 'chicago-ps1-3d-gate-failure-v1', mlflow_version: 'None',
      target: 'will_fail_3d', run_id: '20260713_0905' },
  ];
}
export function getPS1Leaderboard() {
  return [
    { device: 'TVM',   model: 'honest_stack_calib', auc: 0.766, ap: 0.440, f1: null, prec: null, rec: null, lb_rank: 1, is_champion: false, note: 'calibrated stacking ensemble' },
    { device: 'TVM',   model: 'LightGBM (Optuna)',  auc: 0.764, ap: 0.439, f1: 0.459, prec: 0.344, rec: 0.691, lb_rank: 2, is_champion: true,  note: 'deployed champion' },
    { device: 'TVM',   model: 'XGBoost (Optuna)',   auc: 0.763, ap: 0.437, f1: 0.459, prec: 0.340, rec: 0.707, lb_rank: 3, is_champion: false, note: '' },
    { device: 'TVM',   model: 'LightGBM',           auc: 0.761, ap: 0.429, f1: 0.454, prec: 0.329, rec: 0.729, lb_rank: 4, is_champion: false, note: '' },
    { device: 'TVM',   model: 'CatBoost',           auc: 0.762, ap: 0.428, f1: 0.456, prec: 0.322, rec: 0.781, lb_rank: 5, is_champion: false, note: '' },
    { device: 'TVM',   model: 'XGBoost',            auc: 0.760, ap: 0.426, f1: 0.455, prec: 0.327, rec: 0.750, lb_rank: 6, is_champion: false, note: '' },
    { device: 'TVM',   model: 'Random Forest',      auc: 0.700, ap: 0.358, f1: 0.378, prec: 0.294, rec: 0.528, lb_rank: 7, is_champion: false, note: '' },
    { device: 'TVM',   model: 'Logistic Reg.',      auc: 0.657, ap: 0.314, f1: 0.362, prec: 0.261, rec: 0.590, lb_rank: 8, is_champion: false, note: '' },
    { device: 'Gates', model: 'CatBoost',           auc: 0.924, ap: 0.419, f1: 0.413, prec: 0.298, rec: 0.676, lb_rank: 1, is_champion: false, note: 'strongest honest Gates model' },
    { device: 'Gates', model: 'XGBoost',            auc: 0.920, ap: 0.412, f1: 0.396, prec: 0.278, rec: 0.688, lb_rank: 2, is_champion: false, note: '' },
    { device: 'Gates', model: 'honest_stack_calib', auc: 0.930, ap: 0.412, f1: null, prec: null, rec: null, lb_rank: 3, is_champion: false, note: 'calibrated stacking ensemble' },
    { device: 'Gates', model: 'LightGBM (Optuna)',  auc: 0.904, ap: 0.405, f1: 0.448, prec: 0.547, rec: 0.380, lb_rank: 4, is_champion: true,  note: 'deployed champion — over-fit (train/val AUC 1.0)' },
  ];
}
export function getPS1Features() {
  return [
    { device: 'TVM', feature: 'device_fail_rate_30d', mean_abs_shap: 0.80040, pct_total: 47.40, feat_rank: 1 },
    { device: 'TVM', feature: 'sales_7d_avg',         mean_abs_shap: 0.12870, pct_total: 7.60,  feat_rank: 2 },
    { device: 'TVM', feature: 'total_outage_min',     mean_abs_shap: 0.07584, pct_total: 4.50,  feat_rank: 3 },
    { device: 'TVM', feature: 'scrst_events',         mean_abs_shap: 0.07548, pct_total: 4.50,  feat_rank: 4 },
    { device: 'TVM', feature: 'oos_lifetime_count',   mean_abs_shap: 0.06891, pct_total: 4.10,  feat_rank: 5 },
    { device: 'TVM', feature: 'availability_pct_7d',  mean_abs_shap: 0.06402, pct_total: 3.80,  feat_rank: 6 },
    { device: 'TVM', feature: 'device_age_days',      mean_abs_shap: 0.06318, pct_total: 3.70,  feat_rank: 7 },
    { device: 'TVM', feature: 'events_30d',           mean_abs_shap: 0.04784, pct_total: 2.80,  feat_rank: 8 },
    { device: 'Gates', feature: 'device_fail_rate_30d', mean_abs_shap: 0.44900, pct_total: 44.90, feat_rank: 1 },
    { device: 'Gates', feature: 'quarter',            mean_abs_shap: 0.11200, pct_total: 11.20, feat_rank: 2 },
    { device: 'Gates', feature: 'month',              mean_abs_shap: 0.06800, pct_total: 6.80,  feat_rank: 3 },
    { device: 'Gates', feature: 'events_30d',         mean_abs_shap: 0.05100, pct_total: 5.10,  feat_rank: 4 },
    { device: 'Gates', feature: 'availability_pct_7d',mean_abs_shap: 0.04300, pct_total: 4.30,  feat_rank: 5 },
    { device: 'Gates', feature: 'total_outage_min',   mean_abs_shap: 0.03900, pct_total: 3.90,  feat_rank: 6 },
  ];
}
