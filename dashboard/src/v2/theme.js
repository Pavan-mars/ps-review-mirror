// =====================================================================
// v2/theme.js -- one source of truth for colour, type and naming.
//
// Every number in this file is either validated or copied from something
// that was. Nothing here is a taste call dressed up as a token.
//
// THE CATEGORICAL RAMP WAS VALIDATED, NOT CHOSEN BY EYE.
//   worst adjacent pair, normal vision:  #8B5CF6 <-> #F472B6  dE 22.6
//   worst adjacent pair, protanopia:     #F59E0B <-> #10B981  dE  8.9
//   worst adjacent pair, tritanopia:                          dE 16.5
//   lightness band: all 8 inside L 0.43-0.77   chroma floor: all >= 0.1
// Several slots sit under 3:1 against the surface, which is normal for
// chart fills and is why EVERY chart in v2 ships direct labels, a legend
// and a table view. Colour is never the only channel carrying meaning.
//
// The hues deliberately match the executive deck's palette family, so a
// slide and the screen behind it read as one system.
// =====================================================================

export const SURFACE = '#FCFCFB';
export const CARD = '#FFFFFF';
export const LINE = '#E7EDF4';
export const INK = '#0F172A';       // primary text
export const INK_2 = '#475569';     // secondary text
export const INK_3 = '#94A3B8';     // muted text / axis

// Assigned in FIXED ORDER, never cycled. A 9th series folds into "Other"
// or becomes small multiples -- it never gets a generated hue.
export const CAT = [
  '#3B82F6', // blue
  '#F472B6', // pink
  '#8B5CF6', // violet
  '#10B981', // green
  '#F59E0B', // amber
  '#06B6D4', // cyan
  '#EF4444', // red
  '#84CC16', // lime
];

// Colour follows the ENTITY, not its rank. Filtering the fleet down to two
// device types must not repaint the survivors, so device type maps to a
// fixed slot here rather than to its index in whatever array arrives.
export const DEVICE_COLOR = {
  GATE: CAT[0],
  TVM: CAT[2],
  VALIDATOR: CAT[3],
  READER: CAT[5],
};

// Status is RESERVED. These four never appear as "series 5", and they
// always ship with a word next to them, never colour alone.
export const STATUS = {
  critical: { fill: '#DC2626', bg: '#FEF2F2', label: 'Critical' },
  serious: { fill: '#EA580C', bg: '#FFF7ED', label: 'High' },
  warning: { fill: '#D97706', bg: '#FFFBEB', label: 'Medium' },
  good: { fill: '#059669', bg: '#ECFDF5', label: 'Low' },
  neutral: { fill: INK_3, bg: '#F8FAFC', label: 'Not scored' },
};

// Sequential = ONE hue, light to dark. Used for magnitude only (treemap
// depth, matrix intensity). Never a rainbow.
export const SEQ_BLUE = ['#EFF6FF', '#DBEAFE', '#BFDBFE', '#93C5FD', '#60A5FA', '#3B82F6', '#2563EB'];

// Diverging = two poles + a NEUTRAL GREY midpoint. Never a hue in the middle.
export const DIVERGING = ['#2563EB', '#93C5FD', '#E2E8F0', '#FCA5A5', '#DC2626'];

// ---------------------------------------------------------------------
// NAMING -- the customer reads the left side, engineers still find the right.
// PK's call, 01-Aug: plain English with the code shown small alongside.
// ---------------------------------------------------------------------
export const DEVICE_LABEL = {
  GATE: 'Fare Gates',
  TVM: 'Ticket Vending Machines',
  VALIDATOR: 'Bus Validators',
  READER: 'Card Readers',
};
export const DEVICE_SHORT = {
  GATE: 'Fare Gates',
  TVM: 'TVMs',
  VALIDATOR: 'Validators',
  READER: 'Readers',
};
export const deviceName = (code) => DEVICE_LABEL[String(code || '').toUpperCase()] || code || 'Unknown';
export const deviceShort = (code) => DEVICE_SHORT[String(code || '').toUpperCase()] || code || 'Unknown';
export const deviceColor = (code) => DEVICE_COLOR[String(code || '').toUpperCase()] || INK_3;

// Subsystem and component codes stay as they are -- they are what is
// printed on the part and what the engineer types. Translating them would
// make the screen disagree with the depot.

// ---------------------------------------------------------------------
// FORMATTERS -- a number's format is part of its meaning.
// ---------------------------------------------------------------------
export const nfmt = (n, d = 0) =>
  n === null || n === undefined || Number.isNaN(Number(n))
    ? '--'
    : Number(n).toLocaleString(undefined, { minimumFractionDigits: d, maximumFractionDigits: d });

export const pct = (n, d = 1) =>
  n === null || n === undefined || Number.isNaN(Number(n)) ? '--' : `${(Number(n) * 100).toFixed(d)}%`;

// Some routes already return 0-100, others 0-1. Passing the wrong one
// silently renders 4700% or 0.5%, so the caller picks explicitly.
export const pct100 = (n, d = 1) =>
  n === null || n === undefined || Number.isNaN(Number(n)) ? '--' : `${Number(n).toFixed(d)}%`;

export const compact = (n) => {
  const v = Number(n);
  if (!Number.isFinite(v)) return '--';
  if (Math.abs(v) >= 1e9) return `${(v / 1e9).toFixed(1)}B`;
  if (Math.abs(v) >= 1e6) return `${(v / 1e6).toFixed(1)}M`;
  if (Math.abs(v) >= 1e3) return `${(v / 1e3).toFixed(1)}k`;
  return String(Math.round(v));
};

export const dfmt = (d) => {
  if (!d) return '--';
  const s = String(d).slice(0, 10);
  const dt = new Date(`${s}T00:00:00Z`);
  return Number.isNaN(dt.getTime())
    ? s
    : dt.toLocaleDateString(undefined, { day: 'numeric', month: 'short', year: 'numeric', timeZone: 'UTC' });
};

// Risk band from a 0-1 probability. The thresholds are the ones the
// serving layer already uses, so a band on screen agrees with a band in
// the database rather than being a second opinion invented in the UI.
export const riskBand = (p) => {
  const v = Number(p);
  if (!Number.isFinite(v)) return 'neutral';
  if (v >= 0.75) return 'critical';
  if (v >= 0.5) return 'serious';
  if (v >= 0.25) return 'warning';
  return 'good';
};

export const shadow = '0 1px 2px rgba(15,23,42,.04), 0 8px 24px rgba(15,23,42,.06)';
export const radius = 14;

export const font = {
  hero: { fontSize: 34, fontWeight: 700, letterSpacing: '-0.02em', color: INK, lineHeight: 1.1 },
  h2: { fontSize: 19, fontWeight: 700, letterSpacing: '-0.01em', color: INK },
  h3: { fontSize: 15, fontWeight: 700, color: INK },
  body: { fontSize: 13.5, color: INK_2, lineHeight: 1.55 },
  note: { fontSize: 12.5, color: INK_2, lineHeight: 1.5 },
  micro: { fontSize: 11, color: INK_3, letterSpacing: '0.06em', textTransform: 'uppercase', fontWeight: 600 },
  num: { fontVariantNumeric: 'tabular-nums' },
};
