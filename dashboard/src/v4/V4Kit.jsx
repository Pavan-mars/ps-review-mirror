// =====================================================================
// v2/Kit.jsx -- the pieces every v2 screen is built from, plus the
// drill-down stack that makes "go deeper" reversible.
//
// NOTHING HERE FETCHES. These are presentation primitives only, so a
// panel that breaks breaks in one place and not across five tabs.
// =====================================================================
import React, { createContext, useCallback, useContext, useMemo, useState } from 'react';
import { ChevronLeft, ChevronRight, Search, X } from 'lucide-react';
import {
  A, CARD, INK, INK_2, INK_3, LINE, NAV, STATUS,
  font, motion, navColor, radius, shadow, shadowLift, tint, LINKC,
} from './V4theme';

// =====================================================================
// V2Style -- the one stylesheet in v2.                     04-Aug-2026
//
// WHY A STYLE TAG IN A FILE FULL OF INLINE STYLES. Inline styles cannot
// express :hover, :focus-visible or @keyframes. Every "make it feel alive"
// change needs at least one of those. The alternative -- onMouseEnter /
// onMouseLeave state on every surface -- is a re-render per pointer move
// across a 4,000-row table, which makes the screen feel worse, not better.
//
// Mounted ONCE, by V2Shell. Rendering it twice is harmless (same rules,
// same names) but pointless.
//
// EVERY ANIMATION HERE IS SUPPRESSED under prefers-reduced-motion. Motion
// on a dashboard is decoration; for some people it is a symptom trigger.
// =====================================================================
const CSS = `
.v2-card{transition:box-shadow ${motion.base},transform ${motion.base},border-color ${motion.base};}
.v2-card.v2-hit{cursor:pointer;}
.v2-card.v2-hit:hover{transform:translateY(-2px);box-shadow:${shadowLift};}
.v2-card.v2-hit:active{transform:translateY(0);}

.v2-nav{position:relative;border:1px solid transparent;border-radius:999px;cursor:pointer;
  display:inline-flex;align-items:center;gap:7px;white-space:nowrap;
  font-family:inherit;transition:background ${motion.fast},color ${motion.fast},
  border-color ${motion.fast},box-shadow ${motion.fast},transform ${motion.fast};}
.v2-nav:active{transform:translateY(1px);}

.v2-sub{position:relative;border:none;background:none;cursor:pointer;font-family:inherit;
  white-space:nowrap;transition:color ${motion.fast},background ${motion.fast};}
.v2-sub::after{content:'';position:absolute;left:10px;right:10px;bottom:-1px;height:3px;
  border-radius:3px 3px 0 0;background:currentColor;transform:scaleX(0);transform-origin:center;
  transition:transform ${motion.base};}
.v2-sub[data-on="1"]::after{transform:scaleX(1);}
.v2-sub:hover::after{transform:scaleX(.55);}

/* Hover colour has to come from the element, because every tab is a
   different colour. React writes --c / --w / --e as inline custom
   properties and these rules read them back. */
.v2-nav[data-on="0"]:hover{background:var(--w);color:var(--c);border-color:var(--e);}
.v2-nav[data-on="1"]:hover{filter:brightness(1.08);}
.v2-sub[data-on="0"]:hover{color:var(--c);background:var(--w);}

.v2-chip{transition:background ${motion.fast},color ${motion.fast},border-color ${motion.fast},
  box-shadow ${motion.fast},transform ${motion.fast};}
.v2-chip:active{transform:translateY(1px);}
.v2-chip[data-on="0"]:hover{background:#F8FAFC;border-color:#CBD5E1;color:${INK};}

.v2-rise{animation:v2rise ${motion.slow} both;}
@keyframes v2rise{from{opacity:0;transform:translateY(6px);}to{opacity:1;transform:none;}}

.v2-shimmer{background:linear-gradient(90deg,#EEF2F7 0%,#F8FAFC 42%,#EEF2F7 84%);
  background-size:280% 100%;animation:v2shim 1.35s linear infinite;border-radius:8px;}
@keyframes v2shim{from{background-position:120% 0;}to{background-position:-120% 0;}}

.v2-nav:focus-visible,.v2-sub:focus-visible,.v2-chip:focus-visible{
  outline:2px solid #1D4ED8;outline-offset:2px;}

/* AnalyseModal has rendered <Loader2 className="spin"/> since it was written
   and .spin was never defined anywhere in the tree -- the "loading device
   detail" spinner has been a stationary icon this whole time. Defining it
   here is the first chance the codebase has had to, since this is the first
   stylesheet. */
.spin{animation:v2spin .9s linear infinite;transform-origin:50% 50%;}
@keyframes v2spin{to{transform:rotate(360deg);}}

@media (prefers-reduced-motion: reduce){
  .v2-card,.v2-nav,.v2-sub,.v2-sub::after,.v2-chip{transition:none!important;}
  .v2-card.v2-hit:hover{transform:none;}
  .v2-rise{animation:none;}
  .v2-shimmer{animation:none;}
  .spin{animation:none;}
}
`;

export function V2Style() {
  return <style>{CSS}</style>;
}

// ---------------------------------------------------------------------
// DRILL-DOWN
//
// A stack, not a boolean. Fleet -> Fare Gates -> 77th Street -> BMV03868
// is four levels, and the back button has to return the PREVIOUS one with
// its filters intact, not reset to the top. Each frame carries its own
// scope object; a panel reads scope and narrows itself.
//
// Every chart mark that can be drilled calls push(); the breadcrumb and
// the back button are rendered once, here, so no tab can forget one.
// ---------------------------------------------------------------------
const DrillContext = createContext(null);

export function DrilldownProvider({ rootLabel = 'Fleet', children }) {
  const [stack, setStack] = useState([{ label: rootLabel, scope: {} }]);

  const push = useCallback((label, scopePatch) => {
    setStack((prev) => {
      const merged = { ...(prev[prev.length - 1].scope || {}), ...(scopePatch || {}) };
      // Clicking the same thing twice should not stack two identical frames.
      const top = prev[prev.length - 1];
      if (top.label === label) return prev;
      return [...prev, { label, scope: merged }];
    });
  }, []);

  const back = useCallback(() => setStack((p) => (p.length > 1 ? p.slice(0, -1) : p)), []);
  const goTo = useCallback((i) => setStack((p) => p.slice(0, Math.max(1, i + 1))), []);
  const reset = useCallback(() => setStack((p) => p.slice(0, 1)), []);

  const value = useMemo(
    () => ({ stack, scope: stack[stack.length - 1].scope || {}, depth: stack.length - 1, push, back, goTo, reset }),
    [stack, push, back, goTo, reset]
  );
  return <DrillContext.Provider value={value}>{children}</DrillContext.Provider>;
}

// Safe outside a provider: panels get an inert stub rather than a crash.
// A missing provider used to unmount an entire tab subtree.
export function useDrill() {
  return (
    useContext(DrillContext) || {
      stack: [{ label: 'Fleet', scope: {} }],
      scope: {},
      depth: 0,
      push: () => {},
      back: () => {},
      goTo: () => {},
      reset: () => {},
    }
  );
}

export function Breadcrumb() {
  const { stack, depth, back, goTo } = useDrill();
  if (depth === 0) return null;
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap', margin: '0 0 14px' }}>
      <button
        type="button"
        onClick={back}
        style={{
          display: 'inline-flex', alignItems: 'center', gap: 5, border: `1px solid ${LINE}`,
          background: CARD, color: INK, borderRadius: 999, padding: '5px 13px 5px 9px',
          fontSize: 12.6, fontWeight: 600, cursor: 'pointer', boxShadow: shadow,
        }}
      >
        <ChevronLeft size={15} /> Back
      </button>
      <div style={{ display: 'flex', alignItems: 'center', gap: 4, flexWrap: 'wrap' }}>
        {stack.map((f, i) => (
          <span key={`${f.label}-${i}`} style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}>
            {i > 0 && <ChevronRight size={13} color={INK_3} />}
            <button
              type="button"
              onClick={() => goTo(i)}
              disabled={i === stack.length - 1}
              style={{
                border: 'none', background: 'none', padding: '2px 2px', fontSize: 12.6,
                cursor: i === stack.length - 1 ? 'default' : 'pointer',
                color: i === stack.length - 1 ? INK : INK_2,
                fontWeight: i === stack.length - 1 ? 700 : 500,
              }}
            >
              {f.label}
            </button>
          </span>
        ))}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------
// SURFACES
// ---------------------------------------------------------------------
// `accent` paints the left rule and nothing else. The card body stays white
// -- the colour is chrome, so a screen of eight cards reads as eight cards
// rather than as eight coloured blocks competing for the eye.
// THE CARD IS THE WHOLE LOOK.                              04-Aug-2026
//
// Every panel, stat and table on all five screens renders inside one of
// these, so this component -- not any screen -- is where the product's
// surface is decided. Four changes from the old card:
//
//   * A LARGER RADIUS (18) and a HAIRLINE border. The old 3px coloured
//     left rule made every card look like an alert; the accent now sits
//     as a thin top edge, present but not shouting.
//   * A SOFTER, WIDER SHADOW. The old one was tight and dark, which
//     reads as "raised button". This reads as "sheet of paper".
//   * MORE INTERNAL AIR. Cramped padding is what makes a dense screen
//     feel cheap rather than considered.
//   * A REAL HOVER on clickable cards only. Things that lift should be
//     things you can press.
// DENSITY PASS. Padding 20/22 -> 12/14, radius 18 -> 12, and the wide
// soft second shadow stop dropped. That halo read as ~14px of dead
// margin around every box; the contact shadow alone still lifts the
// card off the page. The border now carries the accent hue instead of
// a grey hairline, so a box is bounded by colour, not by absence.
export function Card({ children, style, pad = '12px 14px', accent, hit, onClick, className = '' }) {
  const edge = accent || LINKC;
  return (
    <div
      className={`v2-card${hit ? ' v2-hit' : ''}${className ? ` ${className}` : ''}`}
      onClick={onClick}
      style={{
        background: CARD,
        border: `1px solid ${edge}44`,
        borderTop: `2px solid ${edge}`,
        borderRadius: 12,
        // MORE PRESENT, NOT LOUDER. A two-stop shadow -- a tight contact
        // shadow plus a wide soft one -- is what makes a card look like it
        // is sitting ON the page rather than printed INTO it. Depth comes
        // from the second stop; a single heavy shadow just looks smudged.
        boxShadow: '0 1px 2px rgba(17,24,39,.05)',
        padding: pad,
        ...style,
      }}
    >
      {children}
    </div>
  );
}

// title + one line of why-this-matters. The line is capped deliberately:
// a paragraph above a chart does not get read, and the chart should carry
// the point on its own.
// PANEL, to the mockups: an icon in a soft-tinted rounded square, the title
// beside it, and an optional NUMBER. The mockups number their questions --
// "1. What types of issues...", "2. Where are..." -- which tells the reader
// the panels have a reading order rather than being a wall of charts.
export function Panel({ title, hint, right, children, style, accent, icon, n }) {
  const hue = accent || LINKC;
  return (
    <Card style={style} accent={hue}>
      <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: 10, marginBottom: hint ? 1 : 8 }}>
        <h3 style={{ ...font.h3, margin: 0, display: 'inline-flex', alignItems: 'center', gap: 10 }}>
          {icon && (
            <span style={{ width: 26, height: 26, borderRadius: 8, background: tint(hue, A.wash),
                           color: hue, flex: '0 0 auto', display: 'inline-flex',
                           alignItems: 'center', justifyContent: 'center', fontSize: 12.5 }}>{icon}</span>
          )}
          {n ? <span style={{ color: hue }}>{n}.</span> : null}
          {title}
        </h3>
        {right}
      </div>
      {hint && <p style={{ ...font.note, margin: '0 0 8px' }}>{hint}</p>}
      {children}
    </Card>
  );
}

// The eyebrow takes the accent and a short rule sits above the heading. That
// rule is the separator PK asked for: it divides the page by colour without
// putting a coloured band across it.
export function Section({ eyebrow, title, sub, right, children, accent }) {
  return (
    <section className="v2-rise" style={{ margin: '0 0 15px' }}>
      {/* The eyebrow now sits INSIDE a tinted chip rather than floating as
          loose caps. It gives the heading a fixed anchor point, which is
          what makes a run of sections look like one system instead of a
          stack of unrelated headings. */}
      <div style={{ display: 'flex', alignItems: 'flex-end', justifyContent: 'space-between', gap: 14, margin: '0 0 9px' }}>
        <div>
          {eyebrow && (
            <span style={{ ...font.micro, display: 'inline-block', marginBottom: 4,
                           color: accent || INK_2, background: tint(accent || INK_3, A.wash),
                           borderRadius: 999, padding: '3px 10px' }}>{eyebrow}</span>
          )}
          <h2 style={{ ...font.h2, margin: 0 }}>{title}</h2>
          {sub && <p style={{ ...font.note, margin: '4px 0 0', maxWidth: 820 }}>{sub}</p>}
        </div>
        {right}
      </div>
      {children}
    </section>
  );
}

// A coloured horizontal rule. Two thirds accent, then a hairline to the edge
// -- a full-width bar of saturated colour is a banner, and a banner reads as
// an alert.
export function Rule({ accent = NAV[2], style }) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 0, margin: '0 0 18px', ...style }}>
      <span style={{ width: 56, height: 3, borderRadius: 2, background: accent, flex: '0 0 auto' }} />
      <span style={{ flex: 1, height: 1, background: `linear-gradient(90deg,${tint(accent, A.rule)},${LINE})` }} />
    </div>
  );
}

// THE MOCKUP'S KPI STRIP.                                  04-Aug-2026
// Four separate cards read as four separate things. The mockups put the
// headline numbers in ONE card divided by hairlines, which says "these are
// four readings of the same fleet" -- a grouping the eye gets for free and
// four floating cards never give you.
//
// Wrap the existing <Stat> children; each renders flat inside the strip.
export function StatRow({ children, style }) {
  const items = React.Children.toArray(children).filter(Boolean);
  return (
    <div
      style={{
        display: 'grid',
        gridTemplateColumns: `repeat(auto-fit,minmax(180px,1fr))`,
        background: CARD, border: `1px solid ${LINKC}44`, borderTop: `2px solid ${LINKC}`, borderRadius: 12,
        boxShadow: '0 1px 2px rgba(17,24,39,.05)',
        overflow: 'hidden', ...style,
      }}
    >
      {items.map((child, i) => (
        <div key={i}
             style={{ borderLeft: i ? `1px solid ${LINE}` : 'none', padding: '1px 2px' }}>
          {React.cloneElement(child, { flat: true })}
        </div>
      ))}
    </div>
  );
}

export function Grid({ cols = 'repeat(auto-fit,minmax(240px,1fr))', gap = 12, children, style }) {
  return <div style={{ display: 'grid', gridTemplateColumns: cols, gap, ...style }}>{children}</div>;
}

// ---------------------------------------------------------------------
// NUMBERS
// ---------------------------------------------------------------------
// STATUS STAYS SEMANTIC HERE. The left rule on a Stat is its severity, not
// its tab -- critical is red on every screen. `accent` only overrides it for
// the un-scored case, where a neutral grey rule carries no meaning and the
// tab colour is more use than a grey line.
// KPI TILE, to the mockups.                                04-Aug-2026
// The mockups put a SOFT-TINTED ICON SQUARE on the left, the label in small
// grey caps, then the number large and in the status colour. No coloured
// left rule and no tinted border -- the icon carries the hue, so the tile
// stays white and the numbers stay the loudest thing on the row.
export function Stat({ label, value, unit, tone = 'neutral', foot, onClick, accent, icon, flat }) {
  const s = STATUS[tone] || STATUS.neutral;
  const hue = tone === 'neutral' && accent ? accent : s.fill;
  const wash = tone === 'neutral' && accent ? tint(accent, A.wash) : s.bg;
  const clickable = typeof onClick === 'function';
  const body = (
      <div role={clickable ? 'button' : undefined}
           onClick={flat ? onClick : undefined}
           style={{ display: 'flex', alignItems: 'flex-start', gap: 14,
                    cursor: clickable ? 'pointer' : undefined,
                    padding: flat ? '14px 18px' : 0 }}>
        {icon && (
          <span style={{ width: 46, height: 46, borderRadius: 12, background: wash, color: hue,
                         flex: '0 0 auto', display: 'inline-flex', alignItems: 'center',
                         justifyContent: 'center', fontSize: 18.9 }}>{icon}</span>
        )}
        <div style={{ minWidth: 0 }}>
          <div style={{ ...font.micro, marginBottom: 4, color: INK_2 }}>{label}</div>
          <div style={{ display: 'flex', alignItems: 'baseline', gap: 6 }}>
            <span style={{ ...font.num, fontSize: 27, fontWeight: 800, color: hue, lineHeight: 1.1 }}>{value}</span>
            {unit && <span style={{ fontSize: 12.6, color: INK_3, fontWeight: 600 }}>{unit}</span>}
          </div>
          {foot && <div style={{ ...font.note, marginTop: 3, fontSize: 11.7 }}>{foot}</div>}
        </div>
      </div>
  );
  if (flat) return body;
  return <Card pad="16px 18px" hit={clickable} onClick={onClick}>{body}</Card>;
}

// The one number the room should leave with. Exactly one per screen.
// The hero keeps a WHITE body and takes its colour as a 4px left rule plus a
// pastel wash that fades out within the first third of the card. A fully
// washed hero stops looking like a number and starts looking like a banner,
// and the number is the point.
export function Hero({ label, value, unit, sub, right, accent = NAV[2] }) {
  return (
    <Card
      pad="22px 24px"
      accent={accent}
      style={{
        display: 'flex', alignItems: 'center', justifyContent: 'space-between',
        gap: 24, flexWrap: 'wrap', borderLeftWidth: 4,
        backgroundImage: `linear-gradient(100deg,${tint(accent, A.wash)} 0%,${tint(accent, '06')} 32%,#FFFFFF 62%)`,
      }}
    >
      <div>
        <div style={{ ...font.micro, marginBottom: 8, color: accent }}>{label}</div>
        <div style={{ display: 'flex', alignItems: 'baseline', gap: 8 }}>
          <span style={{ ...font.hero, ...font.num, fontSize: 39.6 }}>{value}</span>
          {unit && <span style={{ fontSize: 14.4, color: INK_2, fontWeight: 600 }}>{unit}</span>}
        </div>
        {sub && <p style={{ ...font.note, margin: '8px 0 0', maxWidth: 560 }}>{sub}</p>}
      </div>
      {right}
    </Card>
  );
}

export function Badge({ tone = 'neutral', children, title }) {
  const s = STATUS[tone] || STATUS.neutral;
  return (
    <span
      title={title}
      style={{
        display: 'inline-flex', alignItems: 'center', gap: 5, background: s.bg, color: s.fill,
        border: `1px solid ${s.fill}22`, borderRadius: 999, padding: '2px 9px',
        fontSize: 11.2, fontWeight: 700, whiteSpace: 'nowrap',
      }}
    >
      <span style={{ width: 6, height: 6, borderRadius: 3, background: s.fill, flex: '0 0 auto' }} />
      {children}
    </span>
  );
}

// ---------------------------------------------------------------------
// STATES
//
// An empty panel must say WHY it is empty. "No data" and "nothing was
// mid-outage at this date" are completely different claims, and the second
// one is a finding rather than a fault.
// ---------------------------------------------------------------------
export function Empty({ children = 'Nothing to show for the current selection.', height = 120 }) {
  return (
    <div
      style={{
        height, display: 'flex', alignItems: 'center', justifyContent: 'center',
        border: `1px dashed ${LINE}`, borderRadius: 10, color: INK_3, fontSize: 12.6,
        textAlign: 'center', padding: '0 24px',
      }}
    >
      {children}
    </div>
  );
}

// A SKELETON, NOT A SPINNER. These feeds take 2-14 seconds; a spinner over
// that long reads as a hang. Bars that occupy the shape the content will
// occupy tell you something is coming and roughly how much.
//
// The word is kept underneath, because a skeleton alone is ambiguous with a
// panel that loaded and had nothing in it -- which is a different claim and,
// in this dashboard, sometimes a finding.
export function Loading({ height = 120, label = 'Loading' }) {
  const bars = Math.max(2, Math.min(5, Math.round((height - 34) / 26)));
  return (
    <div style={{ height, display: 'flex', flexDirection: 'column', justifyContent: 'center', gap: 9, padding: '0 2px' }}>
      {Array.from({ length: bars }).map((_, i) => (
        <div
          key={i}
          className="v2-shimmer"
          style={{ height: 11, width: `${[92, 76, 84, 62, 70][i % 5]}%`, animationDelay: `${i * 0.11}s` }}
        />
      ))}
      <div style={{ ...font.micro, marginTop: 3 }}>{label}</div>
    </div>
  );
}

// A short, factual caveat. Used where a number could be over-read -- e.g.
// that Anomaly & Outlier Analysis measures unlikeness, not probability of failure.
// White body, coloured left rule. The old grey fill made a caveat look like
// a disabled panel; a rule marks it as an aside without dimming it.
export function Note({ children, accent = NAV[1] }) {
  return (
    <div
      style={{
        // A note is guidance, not a finding. Tinted ground with no border
        // keeps it clearly subordinate to the cards around it -- the old
        // bordered box competed with the panels it was explaining.
        display: 'flex', gap: 9, alignItems: 'flex-start',
        background: tint(accent, A.wash), border: 'none',
        borderRadius: 12, padding: '11px 15px', margin: '12px 0 0',
      }}
    >
      <span style={{ ...font.note, fontSize: 12.2 }}>{children}</span>
    </div>
  );
}

// A legend is always present for >= 2 series, so identity is never
// carried by colour alone.
export function Legend({ items }) {
  if (!items || items.length < 2) return null;
  return (
    <div style={{ display: 'flex', flexWrap: 'wrap', gap: '6px 16px', marginTop: 10 }}>
      {items.map((it) => (
        <span key={it.label} style={{ display: 'inline-flex', alignItems: 'center', gap: 6, fontSize: 11.2, color: INK_2 }}>
          <span style={{ width: 10, height: 10, borderRadius: 3, background: it.color, flex: '0 0 auto' }} />
          {it.label}
        </span>
      ))}
    </div>
  );
}

// ---------------------------------------------------------------------
// CONTROLS
// ---------------------------------------------------------------------
// Chip is a FILTER, not a tab. It keeps whatever colour its caller gives it,
// because that colour is semantic -- deviceColor for a fleet, STATUS for a
// risk band. Painting these with tab colours would have made a filter chip
// and a navigation tab look like the same control and mean different things.
//
// An active coloured chip is a pastel fill with a saturated border and ring,
// and the label stays ink. It is NOT a solid fill with white text: white on
// the validator green (#10B981) is 2.0:1 and on the gate blue (#3B82F6) is
// 3.7:1, so the one styling that looked boldest is the one that made the
// label unreadable on two of the three fleets. Uncoloured chips have no such
// constraint and do get the solid ink fill.
export function Chip({ active, onClick, children, color, title }) {
  return (
    <button
      type="button"
      onClick={onClick}
      title={title}
      className="v2-chip"
      data-on={active ? '1' : '0'}
      style={{
        border: `${active && color ? 1.5 : 1}px solid ${active ? (color || INK) : LINE}`,
        background: active ? (color ? tint(color, '1F') : INK) : CARD,
        color: active ? (color ? INK : '#FFFFFF') : INK_2,
        boxShadow: active && color ? `0 0 0 3px ${tint(color, A.wash)}` : 'none',
        borderRadius: 999, padding: '5px 12px', fontSize: 12.2, fontWeight: active ? 700 : 600,
        cursor: 'pointer', display: 'inline-flex', alignItems: 'center', gap: 6,
        fontFamily: 'inherit',
      }}
    >
      {color && <span style={{ width: 8, height: 8, borderRadius: 4, background: color, flex: '0 0 auto' }} />}
      {children}
    </button>
  );
}

// =====================================================================
// Tabs -- the ONLY navigation control in v2.
//
// SPLIT FROM Chip DELIBERATELY. Both used to be Chip, which is why the tab
// bar and the fleet filter under it were indistinguishable at a glance:
// same pill, same size, one navigates and one filters.
//
//   variant="top"  solid fill in the tab's own colour, white label.
//   variant="sub"  underlined tab, its own colour, pastel wash when active,
//                  sitting on a rail tinted with the PARENT's colour.
//
// The two are different SHAPES, not just different colours, so the hierarchy
// survives greyscale printing and colour-blind vision -- which is the test
// that decided it, since seven hues 25 degrees apart do not.
// =====================================================================
export function Tabs({ items, value, onChange, variant = 'top', parent, right }) {
  const list = items || [];
  const colorAt = (it, i) =>
    it.color || (variant === 'sub' && parent ? navColor(parent, i) : navColor(it.key, 0)) || NAV[i % NAV.length];

  if (variant === 'sub') {
    const rail = parent ? navColor(parent, 0) : NAV[0];
    return (
      <div
        style={{
          // Pills on a tinted rail rather than an underline. The underline
          // put the active state on a 2px edge that vanished on a projector.
          display: 'flex', gap: 6, alignItems: 'center', overflowX: 'auto',
          background: tint(rail, A.wash), borderRadius: 999,
          padding: 5, margin: '0 0 20px',
        }}
      >
        {list.map((it, i) => {
          const c = colorAt(it, i);
          const on = value === it.key;
          return (
            <button
              key={it.key}
              type="button"
              className="v2-sub"
              data-on={on ? '1' : '0'}
              onClick={() => onChange(it.key)}
              title={it.sub || undefined}
              style={{
                '--c': c, '--w': tint(c, A.wash),
                padding: '9px 14px 11px', marginBottom: -2, borderRadius: '8px 8px 0 0',
                color: on ? c : INK_2, background: on ? tint(c, A.wash) : 'transparent',
                fontSize: 13.5, fontWeight: on ? 700 : 500,
                display: 'inline-flex', alignItems: 'center', gap: 7,
              }}
            >
              <span
                style={{
                  width: 7, height: 7, borderRadius: 4, flex: '0 0 auto',
                  background: on ? c : tint(c, '59'),
                  transition: `background ${motion.fast}`,
                }}
              />
              {it.label}
            </button>
          );
        })}
        {right && <span style={{ marginLeft: 'auto', paddingLeft: 12 }}>{right}</span>}
      </div>
    );
  }

  return (
    <div style={{ display: 'flex', gap: 7, flexWrap: 'wrap', alignItems: 'center', margin: '0 0 18px' }}>
      {list.map((it, i) => {
        const c = colorAt(it, i);
        const on = value === it.key;
        return (
          <button
            key={it.key}
            type="button"
            className="v2-nav"
            data-on={on ? '1' : '0'}
            onClick={() => onChange(it.key)}
            style={{
              '--c': c, '--w': tint(c, A.wash), '--e': tint(c, A.edge),
              padding: '8px 16px', fontSize: 13.1, fontWeight: 700,
              background: on ? c : CARD,
              color: on ? '#FFFFFF' : INK_2,
              borderColor: on ? c : LINE,
              boxShadow: on ? `0 2px 8px ${tint(c, '3D')}, 0 0 0 3px ${tint(c, A.wash)}` : 'none',
            }}
          >
            <span
              style={{
                width: 7, height: 7, borderRadius: 4, flex: '0 0 auto',
                background: on ? 'rgba(255,255,255,.85)' : c,
              }}
            />
            {it.label}
          </button>
        );
      })}
      {right && <span style={{ marginLeft: 'auto' }}>{right}</span>}
    </div>
  );
}

export function TextField({ value, onChange, placeholder, icon = true, onKeyDown, inputRef }) {
  return (
    <div style={{ position: 'relative', flex: 1, minWidth: 180 }}>
      {icon && <Search size={15} color={INK_3} style={{ position: 'absolute', left: 11, top: 10 }} />}
      <input
        ref={inputRef}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        onKeyDown={onKeyDown}
        placeholder={placeholder}
        style={{
          width: '100%', boxSizing: 'border-box', border: `1px solid ${LINE}`, borderRadius: 10,
          padding: icon ? '8px 30px 8px 32px' : '8px 12px', fontSize: 13.1, color: INK,
          background: CARD, outline: 'none',
        }}
      />
      {value ? (
        <button
          type="button"
          onClick={() => onChange('')}
          style={{ position: 'absolute', right: 8, top: 8, border: 'none', background: 'none', cursor: 'pointer', color: INK_3, padding: 2 }}
          aria-label="Clear"
        >
          <X size={14} />
        </button>
      ) : null}
    </div>
  );
}

export function Toolbar({ children }) {
  return <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, alignItems: 'center', margin: '0 0 12px' }}>{children}</div>;
}

export default {
  Card, Panel, Section, Rule, Grid, Stat, Hero, Badge, Empty, Loading, Note, Legend,
  Chip, Tabs, TextField, Toolbar, Breadcrumb, DrilldownProvider, useDrill, V2Style,
};

// FONTS_SCALED 04-Aug-2026
// COLOUR_SYSTEM 04-Aug-2026
