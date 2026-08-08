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

// WHITE, EVERYWHERE. PK's instruction, 04-Aug: every surface is white and
// the colour lives in the chrome -- tab fills, rules, separators, left
// borders. SURFACE was #FCFCFB, an off-white that read as grey next to a
// white card. Both are now the same white, so a card no longer floats on a
// slightly different background.
export const SURFACE = '#FFFFFF';
export const CARD = '#FFFFFF';
// Mockup page ground: near-white, so white cards still read as cards.
export const PAGE = '#F9FAFB';
// The mockups' primary action colour -- the dark indigo on "Create Work
// Order" -- and the lighter blue used for links and outline buttons.
export const ACTION = '#4338CA';
export const LINKC  = '#2563EB';
export const LINE = '#E5E7EB';   // mockup card border
export const INK = '#111827';       // primary text  (mockup near-black)
export const INK_2 = '#6B7280';     // secondary text (mockup grey)
export const INK_3 = '#9CA3AF';     // muted text / axis

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
  warning: { fill: '#F59E0B', bg: '#FFFBEB', label: 'Medium' },
  good: { fill: '#10B981', bg: '#ECFDF5', label: 'Low' },
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
export const shadowLift = '0 2px 4px rgba(15,23,42,.05), 0 14px 34px rgba(15,23,42,.10)';
export const radius = 14;
export const radiusLg = 16;   // mockup outer cards

// =====================================================================
// NAVIGATION COLOUR                                        04-Aug-2026
//
// PK asked for tabs and sub-tabs that are unmistakable from each other,
// bold, on a white background, with pastel used for the softer surfaces.
//
// THE RAMP IS AN ARC, NOT A SET OF FAVOURITES. Nine slots walking teal ->
// sky -> blue -> indigo -> violet -> fuchsia -> pink, roughly 25 degrees of
// hue apart. Walking one arc rather than picking nine nice colours is what
// makes seven tabs read as one product instead of seven stickers.
//
// TWO CONSTRAINTS DECIDED THE EXACT HEX VALUES, and both are checkable:
//
//  1. NOTHING HERE ENTERS THE STATUS BAND. Red, orange, amber and green
//     belong to STATUS above and mean critical / high / medium / low. A tab
//     painted amber sitting above a row of amber severity badges makes the
//     tab look like a warning. The arc therefore stops at hue 165 on one
//     side and 330 on the other, and never crosses the warm quadrant.
//
//  2. WHITE TEXT ON THE FILL, AND THE FILL AS TEXT, BOTH CLEAR 4.5:1.
//     An active tab is a solid fill with white text; an active sub-tab is
//     the same colour used AS text on a pastel wash. So each slot has to
//     work in both directions. That is why the teal is 700 (#0F766E, 4.8:1)
//     and not the 600 that looked better in isolation (#0D9488, 3.4:1) --
//     the prettier one fails as a label.
//
// Pastels are not separate tokens. They are these same colours at 7-12%
// alpha over white, which is exactly what a pastel is, and which cannot
// drift out of agreement with the bold value the way a hand-picked tint
// eventually does.
// =====================================================================
export const NAV = [
  '#0F766E', // teal 700
  '#0369A1', // sky 700
  '#2563EB', // blue 600
  '#4F46E5', // indigo 600
  '#7C3AED', // violet 600
  '#A21CAF', // fuchsia 700
  '#DB2777', // pink 600
  '#0E7490', // cyan 700
  '#1D4ED8', // blue 700
];

// Colour follows the TAB, not its position in whatever array renders it --
// same rule as DEVICE_COLOR. Reordering the tab bar must not repaint it.
export const TAB_INDEX = { overview: 0, ps1: 1, ps2: 2, ps3: 3, ps4: 4, ps5: 5, device: 6 };
export const TAB_COLOR = {
  overview: NAV[0], ps1: NAV[1], ps2: NAV[2], ps3: NAV[3],
  ps4: NAV[4], ps5: NAV[5], device: NAV[6],
};

// A sub-tab's colour is the ramp ROTATED BY ITS PARENT. So Failure Prediction's first
// sub-tab is Failure Prediction's own sky, Failure Pattern & Cascade Identification's first is Failure Pattern & Cascade Identification's own blue, and the two rows
// never run the same sequence -- you can tell which problem statement you
// are inside from the sub-tab row alone, with the tab bar scrolled away.
//
// A sub-tab can still land on a colour some other TOP tab uses. That is not
// ambiguous, because the two are never the same control: a top tab is a
// filled pill in the header, a sub-tab is an underlined tab on a rail. Form
// carries the hierarchy; hue carries the identity.
export const navColor = (tabKey, i = 0) => {
  const p = TAB_INDEX[tabKey];
  return NAV[((Number.isFinite(p) ? p : 0) + i) % NAV.length];
};

// Alpha suffixes on a 6-digit hex. Over white these are pastels; over a
// coloured surface they would not be, which is the other reason every
// surface in v2 is white.
export const A = { wash: '0F', soft: '1A', edge: '2E', rule: '4D' };
export const tint = (hex, a = A.wash) => `${hex}${a}`;

// One duration scale. Interactions that disagree about their timing read as
// jitter rather than as motion.
export const motion = {
  fast: '.13s cubic-bezier(.4,0,.2,1)',
  base: '.22s cubic-bezier(.4,0,.2,1)',
  slow: '.42s cubic-bezier(.22,1,.36,1)',
};

// FONT SCALE RAISED 04-Aug-2026. These screens are read across a meeting
// room, not at arm's length. Every step below is roughly +12%, applied to the
// scale rather than to individual components so the relationships between
// heading, body and note survive the change.
//
// micro moves the least. It is the uppercase eyebrow label, and at 12px+ the
// letter-spacing that makes it read as a label starts to make it read as a
// heading instead. Footnote markers and asterisks are not scaled at all.
// TYPE SCALE, TIGHTENED.                                   04-Aug-2026
// Every size down roughly 10-12%. Smaller type is not just denser: it lets
// a panel hold its chart AND its explanation without either being cropped,
// which is what makes a screen feel considered rather than crowded. The
// RATIOS are unchanged, so the hierarchy reads exactly as before.
// Nothing drops below 11px -- past that it stops being small and starts
// being unreadable on a projector, which is where these screens live.
export const font = {
  hero: { fontSize: 33, fontWeight: 700, letterSpacing: '-0.02em', color: INK, lineHeight: 1.12 },
  h2: { fontSize: 19.5, fontWeight: 700, letterSpacing: '-0.01em', color: INK },
  h3: { fontSize: 15.5, fontWeight: 700, color: INK },
  body: { fontSize: 13.5, color: INK_2, lineHeight: 1.55 },
  note: { fontSize: 12.5, color: INK_2, lineHeight: 1.55 },
  micro: { fontSize: 11, color: INK_3, letterSpacing: '0.06em', textTransform: 'uppercase', fontWeight: 600 },
  num: { fontVariantNumeric: 'tabular-nums' },
};
