// =====================================================================
// v2/Kit.jsx -- the pieces every v2 screen is built from, plus the
// drill-down stack that makes "go deeper" reversible.
//
// NOTHING HERE FETCHES. These are presentation primitives only, so a
// panel that breaks breaks in one place and not across five tabs.
// =====================================================================
import React, { createContext, useCallback, useContext, useMemo, useState } from 'react';
import { ChevronLeft, ChevronRight, Info, Search, X } from 'lucide-react';
import { CARD, INK, INK_2, INK_3, LINE, STATUS, font, radius, shadow } from './theme';

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
          fontSize: 12.5, fontWeight: 600, cursor: 'pointer', boxShadow: shadow,
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
                border: 'none', background: 'none', padding: '2px 2px', fontSize: 12.5,
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
export function Card({ children, style, pad = '18px 20px' }) {
  return (
    <div style={{ background: CARD, border: `1px solid ${LINE}`, borderRadius: radius, boxShadow: shadow, padding: pad, ...style }}>
      {children}
    </div>
  );
}

// title + one line of why-this-matters. The line is capped deliberately:
// a paragraph above a chart does not get read, and the chart should carry
// the point on its own.
export function Panel({ title, hint, right, children, style }) {
  return (
    <Card style={style}>
      <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: 12, marginBottom: hint ? 2 : 12 }}>
        <h3 style={{ ...font.h3, margin: 0 }}>{title}</h3>
        {right}
      </div>
      {hint && <p style={{ ...font.note, margin: '0 0 13px' }}>{hint}</p>}
      {children}
    </Card>
  );
}

export function Section({ eyebrow, title, sub, right, children }) {
  return (
    <section style={{ margin: '0 0 26px' }}>
      <div style={{ display: 'flex', alignItems: 'flex-end', justifyContent: 'space-between', gap: 16, margin: '0 0 12px' }}>
        <div>
          {eyebrow && <div style={{ ...font.micro, marginBottom: 5 }}>{eyebrow}</div>}
          <h2 style={{ ...font.h2, margin: 0 }}>{title}</h2>
          {sub && <p style={{ ...font.note, margin: '5px 0 0', maxWidth: 720 }}>{sub}</p>}
        </div>
        {right}
      </div>
      {children}
    </section>
  );
}

export function Grid({ cols = 'repeat(auto-fit,minmax(260px,1fr))', gap = 14, children, style }) {
  return <div style={{ display: 'grid', gridTemplateColumns: cols, gap, ...style }}>{children}</div>;
}

// ---------------------------------------------------------------------
// NUMBERS
// ---------------------------------------------------------------------
export function Stat({ label, value, unit, tone = 'neutral', foot, onClick }) {
  const s = STATUS[tone] || STATUS.neutral;
  const clickable = typeof onClick === 'function';
  return (
    <Card
      pad="15px 17px"
      style={{
        cursor: clickable ? 'pointer' : 'default',
        borderLeft: `3px solid ${s.fill}`,
        transition: 'transform .12s ease, box-shadow .12s ease',
      }}
    >
      <div onClick={onClick} role={clickable ? 'button' : undefined}>
        <div style={{ ...font.micro, marginBottom: 7 }}>{label}</div>
        <div style={{ display: 'flex', alignItems: 'baseline', gap: 6 }}>
          <span style={{ ...font.hero, ...font.num, fontSize: 28 }}>{value}</span>
          {unit && <span style={{ fontSize: 13, color: INK_3, fontWeight: 600 }}>{unit}</span>}
        </div>
        {foot && <div style={{ ...font.note, marginTop: 6, fontSize: 12 }}>{foot}</div>}
      </div>
    </Card>
  );
}

// The one number the room should leave with. Exactly one per screen.
export function Hero({ label, value, unit, sub, right }) {
  return (
    <Card pad="22px 24px" style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 24, flexWrap: 'wrap' }}>
      <div>
        <div style={{ ...font.micro, marginBottom: 8 }}>{label}</div>
        <div style={{ display: 'flex', alignItems: 'baseline', gap: 8 }}>
          <span style={{ ...font.hero, ...font.num, fontSize: 44 }}>{value}</span>
          {unit && <span style={{ fontSize: 16, color: INK_2, fontWeight: 600 }}>{unit}</span>}
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
        fontSize: 11.5, fontWeight: 700, whiteSpace: 'nowrap',
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
        border: `1px dashed ${LINE}`, borderRadius: 10, color: INK_3, fontSize: 12.5,
        textAlign: 'center', padding: '0 24px',
      }}
    >
      {children}
    </div>
  );
}

export function Loading({ height = 120, label = 'Loading' }) {
  return (
    <div style={{ height, display: 'flex', alignItems: 'center', justifyContent: 'center', color: INK_3, fontSize: 12.5 }}>
      {label}
      <span style={{ marginLeft: 2 }}>...</span>
    </div>
  );
}

// A short, factual caveat. Used where a number could be over-read -- e.g.
// that PS4 measures unlikeness, not probability of failure.
export function Note({ children }) {
  return (
    <div style={{ display: 'flex', gap: 8, alignItems: 'flex-start', background: '#F8FAFC', border: `1px solid ${LINE}`, borderRadius: 10, padding: '9px 12px', margin: '10px 0 0' }}>
      <Info size={14} color={INK_3} style={{ flex: '0 0 auto', marginTop: 1 }} />
      <span style={{ ...font.note, fontSize: 12 }}>{children}</span>
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
        <span key={it.label} style={{ display: 'inline-flex', alignItems: 'center', gap: 6, fontSize: 11.5, color: INK_2 }}>
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
export function Chip({ active, onClick, children, color }) {
  return (
    <button
      type="button"
      onClick={onClick}
      style={{
        border: `1px solid ${active ? (color || INK) : LINE}`,
        background: active ? (color ? `${color}14` : '#F1F5F9') : CARD,
        color: active ? INK : INK_2,
        borderRadius: 999, padding: '5px 12px', fontSize: 12, fontWeight: 600,
        cursor: 'pointer', display: 'inline-flex', alignItems: 'center', gap: 6,
      }}
    >
      {color && <span style={{ width: 8, height: 8, borderRadius: 4, background: color }} />}
      {children}
    </button>
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
          padding: icon ? '8px 30px 8px 32px' : '8px 12px', fontSize: 13, color: INK,
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

export default { Card, Panel, Section, Grid, Stat, Hero, Badge, Empty, Loading, Note, Legend, Chip, TextField, Toolbar, Breadcrumb, DrilldownProvider, useDrill };
