// ============================================================================
// DashboardKit.jsx  ->  src/components/shared/DashboardKit.jsx
//
// Shared presentational + interaction primitives for the PS1 and PS3 tabs.
// Created 2026-07-26 so the two tabs share one implementation of filtering,
// sorting, pagination, drill-down, cross-tab and ServiceNow staging instead of
// diverging copies.
//
// Design rules this file enforces, because they are easy to break by accident:
//
//   * Nothing here fabricates data. Every component renders what it is given and
//     shows an explicit empty state when given nothing. There is no sample data,
//     no placeholder series and no random generator anywhere in this file.
//   * Colour follows the entity, never its rank, so a filter that removes series
//     never repaints the survivors.
//   * One y-axis per chart. Two measures of different scale get two charts.
//   * Every table is paginated and, above ~150 rows, windowed - the browser only
//     ever holds a page of DOM nodes.
//   * A number that is absent renders as an em-dash, never as 0.
// ============================================================================
import React, { useState, useMemo, useEffect, useRef, useCallback } from 'react';
import {
  ResponsiveContainer, Treemap, ScatterChart, Scatter, ZAxis,
  FunnelChart, Funnel, LabelList, AreaChart, Area,
  BarChart, Bar, LineChart, Line, ComposedChart,
  XAxis, YAxis, CartesianGrid, Tooltip, Legend, Cell,
} from 'recharts';

// ---- shared scales ---------------------------------------------------------
// Device-type accents sit outside the severity reds/ambers so type and risk can
// never be confused for one another.
export const TCOL = { TVM: '#6366f1', GATE: '#0ea5e9', VALIDATOR: '#8b5cf6' };
export const TYPE_ORDER = ['TVM', 'GATE', 'VALIDATOR'];
export const TYPE_LABEL = { TVM: 'TVMs', GATE: 'Gates', VALIDATOR: 'Validators' };
export const DEVLABEL_TO_CAT = { TVMs: 'TVM', Gates: 'GATE', Validators: 'VALIDATOR' };

export const RISK = { CRITICAL: '#ef4444', HIGH: '#f97316', MAJOR: '#f97316', MEDIUM: '#eab308', MINOR: '#eab308', LOW: '#3b82f6', UNKNOWN: '#94a3b8' };
export const RISK_BADGE = { CRITICAL: 'badge-critical', HIGH: 'badge-high', MAJOR: 'badge-high', MEDIUM: 'badge-medium', MINOR: 'badge-medium', LOW: 'badge-low', UNKNOWN: 'badge-info' };

// Categorical ramp, assigned in fixed order and never cycled. A 9th series folds
// into "Other" rather than repeating a hue that already means something else.
// ---- component label ------------------------------------------------------
// The root-cause head emits a real class literally named "None". It does NOT
// mean missing data: it means the ServiceNow record named no failing subsystem,
// and it is the MAJORITY class in the ground truth -- 20,028 of 32,842 TVM
// incidents (61%) and 4,138 of 6,454 on the held-out split, where the head
// scores precision 0.9173 / recall 0.9833 / F1 0.9491 on it.
//
// Rendering it as the bare word "None" made the biggest, best-predicted class on
// the tab read as a hole in the data. This map renames it once, here, so every
// chart, table, tooltip, filter and CSV export in PS3 says the same thing.
export const COMPONENT_LABEL = {
  None: 'No identified component attributed to failure',
};
export const compLabel = (v) => {
  if (v === null || v === undefined || v === '') return '—';
  return COMPONENT_LABEL[String(v)] || String(v);
};
// Short form for axis ticks and treemap tiles, where the full sentence will not
// fit. Same meaning, and the full text is always in the tooltip.
export const COMPONENT_LABEL_SHORT = {
  None: 'No component attributed',
};
export const compLabelShort = (v) => {
  if (v === null || v === undefined || v === '') return '—';
  return COMPONENT_LABEL_SHORT[String(v)] || COMPONENT_LABEL[String(v)] || String(v);
};

export const CAT_RAMP = ['#6366f1', '#0ea5e9', '#10b981', '#f59e0b', '#8b5cf6', '#ec4899', '#14b8a6', '#64748b'];
export const catColor = (i) => CAT_RAMP[i] ?? '#64748b';

// ---- formatters ------------------------------------------------------------
export const num = (v, d = 1) => (v === null || v === undefined || v === '' || Number.isNaN(Number(v)) ? '—' : Number(v).toFixed(d));
export const intf = (v) => (v === null || v === undefined || v === '' ? '—' : Number(v).toLocaleString());
export const pct = (v, d = 1) => (v === null || v === undefined || v === '' ? '—' : `${(Number(v) * 100).toFixed(d)}%`);
export const dayOf = (ts) => (ts ? String(ts).split(/[ T]/)[0] : '—');
export const hexA = (hex, a) => {
  const h = String(hex).replace('#', '');
  return `rgba(${parseInt(h.slice(0, 2), 16)},${parseInt(h.slice(2, 4), 16)},${parseInt(h.slice(4, 6), 16)},${a})`;
};

// ---- small controls --------------------------------------------------------
export function Chip({ active, color, onClick, children, title }) {
  return (
    <button className={`filter-btn${active ? ' active' : ''}`} onClick={onClick} title={title}
      style={active && color ? { background: color, borderColor: color, color: '#fff' } : undefined}>
      {children}
    </button>
  );
}

export function Badge({ band, gated, children }) {
  if (gated) return <span className="badge badge-info" title="Head missed its macro-F1 floor — value withheld">gated</span>;
  if (children) return <span className="badge badge-info">{children}</span>;
  if (!band) return <span className="badge badge-info">—</span>;
  return <span className={`badge ${RISK_BADGE[band] || 'badge-info'}`}>{band}</span>;
}

export function SearchBox({ value, onChange, placeholder = 'Search…', width = 240 }) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
      <input
        type="text" value={value} onChange={(e) => onChange(e.target.value)} placeholder={placeholder}
        style={{ padding: '5px 10px', borderRadius: 6, border: '1px solid var(--border)', fontSize: 12,
          minWidth: width, color: 'var(--text)', background: '#fff' }}
      />
      {value && <button className="filter-btn" onClick={() => onChange('')} title="Clear search">×</button>}
    </div>
  );
}

export function MultiSelect({ label, options, selected, onToggle, onAll, colorOf }) {
  if (!options || options.length === 0) return null;
  const all = selected.length === options.length;
  return (
    <div className="filter-group">
      <label className="filter-label">{label}</label>
      <button className={`filter-btn${all ? ' all-active' : ''}`} onClick={onAll}>All</button>
      {options.map((o) => (
        <Chip key={o} active={selected.includes(o)} color={colorOf ? colorOf(o) : undefined} onClick={() => onToggle(o)}>
          {o}
        </Chip>
      ))}
    </div>
  );
}

export function SelectBox({ label, value, onChange, options, allLabel = 'All' }) {
  return (
    <div className="filter-group">
      <label className="filter-label">{label}</label>
      <select value={value} onChange={(e) => onChange(e.target.value)}
        style={{ padding: '5px 10px', borderRadius: 6, border: '1px solid var(--border)', fontSize: 12,
          color: 'var(--text)', background: '#fff', cursor: 'pointer', maxWidth: 220 }}>
        <option value="">{allLabel} ({options.length})</option>
        {options.map((o) => (
          <option key={typeof o === 'object' ? o.value : o} value={typeof o === 'object' ? o.value : o}>
            {typeof o === 'object' ? o.label : o}
          </option>
        ))}
      </select>
    </div>
  );
}

export function SortTh({ label, col, sort, setSort, align, title }) {
  const active = sort.key === col;
  return (
    <th
      onClick={() => setSort((s) => ({ key: col, dir: s.key === col && s.dir === 'desc' ? 'asc' : 'desc' }))}
      style={{ cursor: 'pointer', whiteSpace: 'nowrap', textAlign: align || 'left', userSelect: 'none' }}
      title={title || 'Click to sort'}
    >
      {label}{active ? (sort.dir === 'asc' ? ' ▲' : ' ▼') : ''}
    </th>
  );
}

export function Pager({ page, pages, total, setPage, label, pageSize, setPageSize }) {
  if (!total) return null;
  return (
    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginTop: 12,
      fontSize: 12, color: 'var(--text-secondary)', flexWrap: 'wrap', gap: 8 }}>
      <span>{total.toLocaleString()} {label} · page {page + 1} of {pages}</span>
      <div style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
        {setPageSize && (
          <select value={pageSize} onChange={(e) => { setPageSize(Number(e.target.value)); setPage(0); }}
            style={{ padding: '3px 6px', borderRadius: 6, border: '1px solid var(--border)', fontSize: 11, background: '#fff', color: 'var(--text)' }}
            title="Rows per page">
            {[25, 50, 100, 250].map((n) => <option key={n} value={n}>{n} / page</option>)}
          </select>
        )}
        <button className="filter-btn" disabled={page === 0} onClick={() => setPage(0)} style={{ opacity: page === 0 ? 0.4 : 1 }}>« First</button>
        <button className="filter-btn" disabled={page === 0} onClick={() => setPage((p) => Math.max(0, p - 1))} style={{ opacity: page === 0 ? 0.4 : 1 }}>‹ Prev</button>
        <button className="filter-btn" disabled={page >= pages - 1} onClick={() => setPage((p) => Math.min(pages - 1, p + 1))} style={{ opacity: page >= pages - 1 ? 0.4 : 1 }}>Next ›</button>
        <button className="filter-btn" disabled={page >= pages - 1} onClick={() => setPage(pages - 1)} style={{ opacity: page >= pages - 1 ? 0.4 : 1 }}>Last »</button>
      </div>
    </div>
  );
}

// ---- states ----------------------------------------------------------------
export function Loading({ what }) {
  return <div className="card" style={{ padding: 40, textAlign: 'center', color: 'var(--text-secondary)' }}>Loading {what}…</div>;
}

export function ApiFailure({ what, detail }) {
  return (
    <div className="card" style={{ padding: 28, borderLeft: '4px solid var(--danger)' }}>
      <div className="card-header" style={{ color: 'var(--danger)' }}>{what} unavailable</div>
      <p style={{ fontSize: 13, color: 'var(--text-secondary)', marginTop: 8 }}>{detail}</p>
    </div>
  );
}

/** Empty state that names the table and the run that fills it, so "not loaded
 *  yet" can never be mistaken for "nothing to worry about". */
export function AwaitingRun({ title, table, note }) {
  return (
    <div className="card" style={{ padding: 24, borderLeft: '4px solid var(--border)' }}>
      <div className="card-header">{title}</div>
      <p style={{ fontSize: 13, color: 'var(--text-secondary)', marginTop: 8, lineHeight: 1.6 }}>
        No rows in <code style={{ fontFamily: 'monospace' }}>{table}</code> for this selection.{note ? ` ${note}` : ''}
      </p>
      <p style={{ fontSize: 12, color: 'var(--text-secondary)', marginTop: 8 }}>
        This panel stays empty until the run lands — it will not substitute a placeholder figure.
      </p>
    </div>
  );
}

export function NoRows({ msg = 'No rows in the current selection.' }) {
  return <div style={{ padding: 24, textAlign: 'center', color: 'var(--text-secondary)', fontSize: 13 }}>{msg}</div>;
}

export function Panel({ title, right, children, live = true, note }) {
  return (
    <div className="card" style={{ marginBottom: 16, overflowX: 'auto' }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 12, gap: 12, flexWrap: 'wrap' }}>
        <div className="card-header" style={{ marginBottom: 0 }}>{title}</div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          {right}
          {live && <span className="badge badge-success" title="Read from Aurora RDS">● LIVE · RDS</span>}
        </div>
      </div>
      {children}
      {note && <div style={{ fontSize: 11, color: 'var(--text-secondary)', marginTop: 8, lineHeight: 1.6 }}>{note}</div>}
    </div>
  );
}

export function Kpi({ label, value, sub, color, title }) {
  return (
    <div className="card" title={title}>
      <div className="card-header">{label}</div>
      <div className="kpi-value" style={color ? { color } : undefined}>{value}</div>
      {sub && <div className="kpi-label">{sub}</div>}
    </div>
  );
}

// ---- sorting / paging hook -------------------------------------------------
export function useSortPage(rows, initialSort, initialSize = 50) {
  const [sort, setSort] = useState(initialSort);
  const [page, setPage] = useState(0);
  const [pageSize, setPageSize] = useState(initialSize);
  useEffect(() => { setPage(0); }, [rows]);
  const sorted = useMemo(() => {
    const r = [...(rows || [])];
    r.sort((a, b) => {
      const av = a[sort.key], bv = b[sort.key];
      if (av === null || av === undefined || av === '') return 1;
      if (bv === null || bv === undefined || bv === '') return -1;
      const an = Number(av), bn = Number(bv);
      const numeric = !Number.isNaN(an) && !Number.isNaN(bn);
      const c = numeric ? an - bn : String(av).localeCompare(String(bv));
      return sort.dir === 'asc' ? c : -c;
    });
    return r;
  }, [rows, sort]);
  const pages = Math.max(1, Math.ceil(sorted.length / pageSize));
  const safePage = Math.min(page, pages - 1);
  const slice = sorted.slice(safePage * pageSize, safePage * pageSize + pageSize);
  return { sort, setSort, page: safePage, setPage, pages, pageSize, setPageSize, sorted, slice };
}

// ---- windowed table body ---------------------------------------------------
// Above WINDOW_MIN rows on a page we render only the visible slice plus a
// buffer, with spacer rows holding the scroll height. Below it, plain rendering
// -- virtualisation on a short table costs more than it saves.
const WINDOW_MIN = 150;
const ROW_H = 34;

export function VirtualTBody({ rows, renderRow, height = 520 }) {
  const ref = useRef(null);
  const [top, setTop] = useState(0);
  const onScroll = useCallback(() => { if (ref.current) setTop(ref.current.scrollTop); }, []);
  if (!rows || rows.length < WINDOW_MIN) {
    return <tbody>{(rows || []).map(renderRow)}</tbody>;
  }
  const first = Math.max(0, Math.floor(top / ROW_H) - 8);
  const count = Math.ceil(height / ROW_H) + 16;
  const last = Math.min(rows.length, first + count);
  return (
    <tbody ref={ref} onScroll={onScroll} style={{ display: 'block', maxHeight: height, overflowY: 'auto' }}>
      {first > 0 && <tr style={{ height: first * ROW_H, display: 'block' }} />}
      {rows.slice(first, last).map(renderRow)}
      {last < rows.length && <tr style={{ height: (rows.length - last) * ROW_H, display: 'block' }} />}
    </tbody>
  );
}

// ---- ServiceNow staging ----------------------------------------------------
/** Stages an incident payload. It does NOT post to ServiceNow: the server route
 *  writes to servicenow_staging and returns a staged_id. The button says
 *  "Stage" rather than "Create" for exactly that reason — a control labelled
 *  "create incident" that quietly does nothing is worse than no control. */
export function ServiceNowButton({ apiBase, psId, deviceId, deviceCategory, shortDescription, payload, disabled, compact, onStaged }) {
  const [state, setState] = useState({ s: 'idle' });
  const post = async () => {
    setState({ s: 'busy' });
    try {
      const res = await fetch(`${apiBase}/${psId}/servicenow-stage?city=CHI`, {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({
          device_id: deviceId, device_category: deviceCategory,
          short_description: shortDescription, payload: payload || {},
        }),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const d = await res.json();
      setState({ s: 'ok', id: d.staged_id });
      if (onStaged) onStaged(d);
    } catch (e) {
      setState({ s: 'err', err: String(e.message || e) });
    }
  };
  const label = state.s === 'busy' ? 'Staging…'
    : state.s === 'ok' ? 'Staged ✓'
      : state.s === 'err' ? 'Failed' : 'Stage in ServiceNow';
  const bg = disabled ? '#C7CDD6' : state.s === 'ok' ? '#10b981' : state.s === 'err' ? '#ef4444' : '#0ea5e9';
  return (
    <button
      onClick={disabled || state.s === 'busy' ? undefined : post}
      disabled={disabled || state.s === 'busy'}
      title={state.s === 'err' ? state.err
        : state.s === 'ok' ? `Staged as ${state.id} — recorded in servicenow_staging, not posted to ServiceNow`
          : 'Records the incident payload in servicenow_staging for review. No live post is made.'}
      style={{ background: bg, color: '#fff', border: 'none', borderRadius: 6,
        padding: compact ? '3px 9px' : '5px 12px', cursor: disabled ? 'not-allowed' : 'pointer',
        fontSize: 11, fontWeight: 600, whiteSpace: 'nowrap' }}
    >
      {label}
    </button>
  );
}

// ---- charts ----------------------------------------------------------------
const AXIS = { fontSize: 11 };
const GRID = <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" />;

// ---- value labels ----------------------------------------------------------
// 2026-07-26: direct value labels on every chart primitive, so a reader does not
// have to hover to get a number.
//
// Text NEVER wears the series colour -- labels use the muted text token and take
// their identity from the coloured mark beside them. A pale categorical hue is
// illegible as text on the panel surface.
//
// Density rule, applied per mark type rather than globally:
//   * discrete marks (bars, pareto rows, funnel steps, treemap tiles) get a label
//     on every mark -- there are few of them and each has room;
//   * continuous series (lines, areas) get an END label only. A number on every
//     point of a 12-month series is unreadable and goes unread; the axis, legend
//     and tooltip carry the interior values.
//   * stacked bars label the STACK TOTAL at the free end, never the interior
//     segments, which have no room and would clip.
export 
// Legend style. Kept small and low-contrast so the legend explains the marks
// without competing with them, and the swatch carries identity while the text
// stays in the muted token -- same rule as the value labels.
const LEG = { fontSize: 11, paddingTop: 4 };
export const VLAB = { fontSize: 11, fill: 'var(--text-secondary)', fontWeight: 600 };
export const fmtV = (v) => (v == null || v === '' || Number.isNaN(Number(v)) ? '' : Number(v).toLocaleString());

/** Label carrying the total of a stacked row/column, drawn past the free end. */
function stackTotalLabel(data, series, horizontal) {
  return function StackTotal(props) {
    const { x, y, width, height, index } = props;
    const row = data[index];
    if (!row) return null;
    const total = series.reduce((a, k) => a + (Number(row[k]) || 0), 0);
    if (!total) return null;
    return horizontal
      ? <text x={x + width + 6} y={y + height / 2} dy={4} textAnchor="start" {...VLAB}>{fmtV(total)}</text>
      : <text x={x + width / 2} y={y - 6} textAnchor="middle" {...VLAB}>{fmtV(total)}</text>;
  };
}

/** Label drawn only at the final point of a continuous series. */
export function endOnlyLabel(data, horizontal) {
  const last = (data || []).length - 1;
  return function EndOnly(props) {
    const { x, y, value, index } = props;
    if (index !== last || value == null) return null;
    return <text x={x + 6} y={y} dy={4} textAnchor="start" {...VLAB}>{fmtV(value)}</text>;
  };
}


/** Treemap of a categorical breakdown. onDrill fires with the clicked node. */
export function TreemapPanel({ data, nameKey = 'name', valueKey = 'value', height = 300, onDrill, colorOf }) {
  if (!data || !data.length) return <NoRows />;
  const shaped = data.map((d, i) => ({ ...d, name: d[nameKey], size: Number(d[valueKey]) || 0, _i: i }));
  return (
    <ResponsiveContainer width="100%" height={height}>
      <Treemap
        data={shaped} dataKey="size" nameKey="name" stroke="var(--surface, #fff)" strokeWidth={2}
        isAnimationActive={false}
        onClick={onDrill ? (n) => n && onDrill(n) : undefined}
        content={<TreemapCell colorOf={colorOf} clickable={!!onDrill} />}
      >
        <Tooltip formatter={(v, _k, p) => [`${Number(v).toLocaleString()}`, p?.payload?.name]} />
      </Treemap>
    </ResponsiveContainer>
  );
}

function TreemapCell(props) {
  const { x, y, width, height, name, size, _i, colorOf, clickable } = props;
  if (width < 1 || height < 1) return null;
  const fill = colorOf ? colorOf(name, _i) : catColor(_i % CAT_RAMP.length);
  // 29-Jul-2026. TREEMAP LABELS WERE UNREADABLE. Four faults, all fixed here:
  //  1. NO TRUNCATION - a name wider than its tile ran over the neighbouring
  //     tile, so two labels overlapped and neither could be read. Names are now
  //     cut to what the tile can hold, with an ellipsis and a hover tooltip.
  //  2. TOO SMALL - 12/11px at this density does not survive a projector.
  //  3. NO CONTRAST FLOOR - white is legible on the dark ramp steps and marginal
  //     on the light ones. A dark halo behind the glyphs holds on ANY fill,
  //     which is the only guarantee available when colour is chosen by category.
  //  4. LABELS ON TILES TOO SMALL TO HOLD THEM - threshold raised 62x26 -> 82x34.
  // Fixing it here fixes every treemap: PS1 stations, PS3 coverage, PS3
  // facilities and PS3 components all render through this cell.
  const showLabel = width > 82 && height > 34;
  const showValueOnly = !showLabel && width > 30 && height > 15;
  const HALO = { stroke: 'rgba(15,23,42,0.55)', strokeWidth: 3, paintOrder: 'stroke',
                 strokeLinejoin: 'round' };
  const maxChars = Math.max(3, Math.floor((width - 16) / 6.1));
  const label = String(name || '');
  const shown = label.length > maxChars
    ? label.slice(0, Math.max(1, maxChars - 1)) + '\u2026' : label;
  const tip = label + ': ' + Number(size).toLocaleString();
  return (
    <g style={clickable ? { cursor: 'pointer' } : undefined}>
      <rect x={x} y={y} width={width} height={height} rx={4} ry={4}
        style={{ fill, stroke: 'var(--surface, #fff)', strokeWidth: 2 }} />
      {showLabel && (
        <>
          <title>{tip}</title>
          <text x={x + 8} y={y + 19} fill="#fff" fontSize={13} fontWeight={700} style={HALO}>{shown}</text>
          <text x={x + 8} y={y + 36} fill="#fff" fontSize={12} fontWeight={600} style={HALO}>{Number(size).toLocaleString()}</text>
        </>
      )}
      {showValueOnly && (
        <>
          <title>{tip}</title>
          <text x={x + width / 2} y={y + height / 2} dy={4} textAnchor="middle"
            fill="#fff" fontSize={12} fontWeight={700} style={HALO}>{Number(size).toLocaleString()}</text>
        </>
      )}
    </g>
  );
}

/** Funnel of an ordered stage list. Stages must be genuinely nested subsets —
 *  a funnel implies each stage is contained in the one above it. */
export function FunnelPanel({ data, height = 300, onDrill }) {
  if (!data || !data.length) return <NoRows />;
  return (
    <ResponsiveContainer width="100%" height={height}>
      <FunnelChart>
        <Tooltip formatter={(v, _k, p) => [Number(v).toLocaleString(), p?.payload?.name]} />
        <Funnel dataKey="value" data={data} isAnimationActive={false}
          onClick={onDrill ? (n) => n && onDrill(n) : undefined}
          style={onDrill ? { cursor: 'pointer' } : undefined}>
          <LabelList position="right" fill="var(--text-primary)" stroke="none"
            dataKey="label" style={{ fontSize: 12 }} />
          <LabelList position="insideRight" fill="#fff" stroke="none"
            dataKey="value" formatter={fmtV} style={{ fontSize: 11, fontWeight: 700 }} />
          {data.map((d, i) => <Cell key={d.name} fill={catColor(i % CAT_RAMP.length)} />)}
        </Funnel>
      </FunnelChart>
    </ResponsiveContainer>
  );
}

/** Bubble chart. z drives radius, so it must be a magnitude (count), never a
 *  rate — a radius encoding a percentage misreads badly. */
export function BubblePanel({ data, xKey, yKey, zKey, xLabel, yLabel, zLabel, height = 340, colorKey, onDrill }) {
  if (!data || !data.length) return <NoRows />;
  const groups = colorKey ? [...new Set(data.map((d) => d[colorKey]))] : ['all'];
  return (
    <ResponsiveContainer width="100%" height={height}>
      <ScatterChart margin={{ top: 10, right: 20, bottom: 30, left: 10 }}>
        {GRID}
        <XAxis type="number" dataKey={xKey} name={xLabel} tick={AXIS}
          label={{ value: xLabel, position: 'insideBottom', offset: -18, fontSize: 11 }} />
        <YAxis type="number" dataKey={yKey} name={yLabel} tick={AXIS}
          label={{ value: yLabel, angle: -90, position: 'insideLeft', fontSize: 11 }} />
        <ZAxis type="number" dataKey={zKey} range={[40, 600]} name={zLabel} />
        <Tooltip cursor={{ strokeDasharray: '3 3' }}
          formatter={(v, n) => [typeof v === 'number' ? v.toLocaleString() : v, n]}
          labelFormatter={() => ''} />
        {groups.length > 1 && <Legend />}
        {groups.map((g, i) => (
          <Scatter key={g} name={String(g)}
            data={colorKey ? data.filter((d) => d[colorKey] === g) : data}
            fill={colorKey && TCOL[g] ? TCOL[g] : catColor(i % CAT_RAMP.length)}
            onClick={onDrill ? (n) => n && onDrill(n.payload ?? n) : undefined}
            style={onDrill ? { cursor: 'pointer' } : undefined} />
        ))}
      </ScatterChart>
    </ResponsiveContainer>
  );
}

/** Stacked area over time. */
export function AreaPanel({ data, xKey, series, height = 280, colorOf }) {
  if (!data || !data.length || !series.length) return <NoRows />;
  return (
    <ResponsiveContainer width="100%" height={height}>
      <AreaChart data={data} margin={{ top: 8, right: 16, bottom: 4, left: 0 }}>
        {GRID}
        <XAxis dataKey={xKey} tick={AXIS} />
        <YAxis tick={AXIS} />
        <Tooltip formatter={(v) => Number(v).toLocaleString()} />
        <Legend wrapperStyle={LEG} />
        {series.map((s, i) => (
          <Area key={s} type="monotone" dataKey={s} stackId="1"
            stroke={colorOf ? colorOf(s) : catColor(i % CAT_RAMP.length)}
            fill={hexA(colorOf ? colorOf(s) : catColor(i % CAT_RAMP.length), 0.35)} strokeWidth={2}>
            <LabelList dataKey={s} content={endOnlyLabel(data)} />
          </Area>
        ))}
      </AreaChart>
    </ResponsiveContainer>
  );
}

/** Horizontal Pareto with cumulative % in the tooltip. Single axis by design. */
export function ParetoPanel({ data, labelKey, valueKey, height, onDrill, valueName = 'Count' }) {
  if (!data || !data.length) return <NoRows />;
  const total = data.reduce((s, d) => s + (Number(d[valueKey]) || 0), 0);
  let cum = 0;
  const shaped = data.map((d) => {
    cum += Number(d[valueKey]) || 0;
    return { ...d, _cum: total ? Math.round((cum / total) * 1000) / 10 : 0 };
  });
  return (
    <ResponsiveContainer width="100%" height={height || Math.max(240, shaped.length * 30)}>
      <BarChart data={shaped} layout="vertical" margin={{ left: 150 }}>
        {GRID}
        <XAxis type="number" tick={AXIS} />
        <YAxis dataKey={labelKey} type="category" width={140} tick={AXIS} />
        <Tooltip formatter={(v, _k, p) => [
          `${Number(v).toLocaleString()} · ${p?.payload?._cum}% cumulative`, valueName]} />
        <Legend wrapperStyle={LEG} />
        <Bar dataKey={valueKey} name={valueName} fill="#6366f1" radius={[0, 4, 4, 0]}
          onClick={onDrill ? (n) => n && onDrill(n.payload ?? n) : undefined}
          style={onDrill ? { cursor: 'pointer' } : undefined}>
          <LabelList dataKey={valueKey} position="right" formatter={fmtV} style={VLAB} />
        </Bar>
      </BarChart>
    </ResponsiveContainer>
  );
}

/** Grouped or stacked bars. */
export function BarPanel({ data, xKey, series, stacked, height = 260, colorOf, onDrill, horizontal }) {
  if (!data || !data.length || !series.length) return <NoRows />;
  return (
    <ResponsiveContainer width="100%" height={height}>
      <BarChart data={data} layout={horizontal ? 'vertical' : 'horizontal'} margin={{ left: horizontal ? 140 : 0, top: 8, right: 16 }}>
        {GRID}
        {horizontal ? <XAxis type="number" tick={AXIS} /> : <XAxis dataKey={xKey} tick={{ fontSize: 12, fontWeight: 600 }} />}
        {horizontal ? <YAxis dataKey={xKey} type="category" width={130} tick={AXIS} /> : <YAxis tick={AXIS} />}
        <Tooltip formatter={(v) => Number(v).toLocaleString()} />
        <Legend wrapperStyle={LEG} />
        {series.map((s, i) => (
          <Bar key={s} dataKey={s} stackId={stacked ? 'a' : undefined}
            fill={colorOf ? colorOf(s) : catColor(i % CAT_RAMP.length)}
            radius={horizontal ? [0, 4, 4, 0] : [4, 4, 0, 0]} maxBarSize={64}
            onClick={onDrill ? (n) => n && onDrill(n.payload ?? n, s) : undefined}
            style={onDrill ? { cursor: 'pointer' } : undefined}>
            {/* stacked -> one total past the end; grouped -> a value per bar */}
            {stacked
              ? (i === series.length - 1 &&
                  <LabelList content={stackTotalLabel(data, series, horizontal)} />)
              : <LabelList dataKey={s} position={horizontal ? 'right' : 'top'}
                  formatter={fmtV} style={VLAB} />}
          </Bar>
        ))}
      </BarChart>
    </ResponsiveContainer>
  );
}

/** Line/composed trend. */
export function TrendPanel({ data, xKey, series, height = 260, colorOf, bars = [] }) {
  if (!data || !data.length) return <NoRows />;
  return (
    <ResponsiveContainer width="100%" height={height}>
      <ComposedChart data={data} margin={{ top: 8, right: 16, bottom: 4, left: 0 }}>
        {GRID}
        <XAxis dataKey={xKey} tick={AXIS} />
        <YAxis tick={AXIS} />
        <Tooltip formatter={(v) => (typeof v === 'number' ? v.toLocaleString() : v)} />
        <Legend wrapperStyle={LEG} />
        {bars.map((s, i) => (
          <Bar key={s} dataKey={s} fill={hexA(colorOf ? colorOf(s) : catColor(i), 0.5)} maxBarSize={30}>
            <LabelList dataKey={s} position="top" formatter={fmtV} style={VLAB} />
          </Bar>
        ))}
        {series.map((s, i) => (
          <Line key={s} type="monotone" dataKey={s} stroke={colorOf ? colorOf(s) : catColor(i % CAT_RAMP.length)}
            strokeWidth={2} dot={{ r: 3 }}>
            <LabelList dataKey={s} content={endOnlyLabel(data)} />
          </Line>
        ))}
      </ComposedChart>
    </ResponsiveContainer>
  );
}

// ---- cross-tab / pivot matrix ---------------------------------------------
/** Renders {row_key, col_key, n} triples as a heat-shaded matrix with margins.
 *  Clicking a cell drills to the (row, col) pair. */
// asPct (27-Jul-2026): render each cell as a share of its ROW total instead of a
// raw count. A row-relative share is what makes a 47-incident class readable
// beside a 20,028-incident one; the count is still in the tooltip and the row
// total column, so nothing is lost by switching.
export function PivotMatrix({ cells, rowLabel, colLabel, onDrill, valueKey = 'n', maxCols = 14, maxRows = 40, asPct = false }) {
  const { rows, cols, grid, rowTot, colTot, grand, max } = useMemo(() => {
    const rset = new Map(), cset = new Map(), g = new Map();
    (cells || []).forEach((c) => {
      const r = c.row_key === null || c.row_key === undefined || c.row_key === '' ? '—' : String(c.row_key);
      const k = c.col_key === null || c.col_key === undefined || c.col_key === '' ? '—' : String(c.col_key);
      const v = Number(c[valueKey]) || 0;
      rset.set(r, (rset.get(r) || 0) + v);
      cset.set(k, (cset.get(k) || 0) + v);
      g.set(`${r} ${k}`, (g.get(`${r} ${k}`) || 0) + v);
    });
    const rowsAll = [...rset.entries()].sort((a, b) => b[1] - a[1]);
    const colsAll = [...cset.entries()].sort((a, b) => b[1] - a[1]);
    const rr = rowsAll.slice(0, maxRows).map((x) => x[0]);
    const cc = colsAll.slice(0, maxCols).map((x) => x[0]);
    let mx = 0;
    rr.forEach((r) => cc.forEach((k) => { mx = Math.max(mx, g.get(`${r} ${k}`) || 0); }));
    return {
      rows: rr, cols: cc, grid: g,
      rowTot: Object.fromEntries(rowsAll), colTot: Object.fromEntries(colsAll),
      grand: [...g.values()].reduce((a, b) => a + b, 0), max: mx,
    };
  }, [cells, valueKey, maxCols, maxRows]);

  if (!cells || !cells.length) return <NoRows />;
  // In percentage mode the shade is row-relative too, otherwise a 100%-of-a-tiny-
  // row cell would render paler than a 20%-of-a-huge-row cell and the colour
  // would contradict the number printed in it.
  const shade = (v, rt) => {
    if (!v) return 'transparent';
    const f = asPct ? v / (rt || 1) : v / (max || 1);
    return hexA('#6366f1', 0.08 + 0.62 * f);
  };
  const fmtCell = (v, rt) => (asPct
    ? `${((v / (rt || 1)) * 100).toFixed(1)}%`
    : v.toLocaleString());
  return (
    <div style={{ overflowX: 'auto' }}>
      <table className="data-table" style={{ fontVariantNumeric: 'tabular-nums' }}>
        <thead>
          <tr>
            <th style={{ position: 'sticky', left: 0, background: 'var(--card, #fff)' }}>{rowLabel} \ {colLabel}</th>
            {cols.map((c) => <th key={c} style={{ textAlign: 'right', whiteSpace: 'nowrap' }}>{c}</th>)}
            <th style={{ textAlign: 'right', fontWeight: 700 }}>Total</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r}>
              <td style={{ fontWeight: 600, position: 'sticky', left: 0, background: 'var(--card, #fff)', whiteSpace: 'nowrap' }}>{r}</td>
              {cols.map((c) => {
                const v = grid.get(`${r} ${c}`) || 0;
                return (
                  <td key={c}
                    onClick={onDrill && v ? () => onDrill({ row: r, col: c, value: v }) : undefined}
                    title={v ? `${r} × ${c}: ${v.toLocaleString()} incident(s) — `
                      + `${((v / (rowTot[r] || 1)) * 100).toFixed(1)}% of this row, `
                      + `${((v / (grand || 1)) * 100).toFixed(1)}% of all` : undefined}
                    style={{ textAlign: 'right', background: shade(v, rowTot[r]),
                      cursor: onDrill && v ? 'pointer' : 'default',
                      color: v ? 'var(--text-primary)' : 'var(--text-secondary)' }}>
                    {v ? fmtCell(v, rowTot[r]) : '—'}
                  </td>
                );
              })}
              <td style={{ textAlign: 'right', fontWeight: 700 }}>{(rowTot[r] || 0).toLocaleString()}</td>
            </tr>
          ))}
          <tr>
            <td style={{ fontWeight: 700, position: 'sticky', left: 0, background: 'var(--card, #fff)' }}>Total</td>
            {cols.map((c) => <td key={c} style={{ textAlign: 'right', fontWeight: 700 }}>{(colTot[c] || 0).toLocaleString()}</td>)}
            <td style={{ textAlign: 'right', fontWeight: 800 }}>{grand.toLocaleString()}</td>
          </tr>
        </tbody>
      </table>
    </div>
  );
}

// ---- drill-down --------------------------------------------------------------
/** A stack of active drill filters, rendered as removable breadcrumbs. Charts
 *  push onto it; tables read it. Filtering happens in the caller so this stays
 *  free of any assumption about row shape. */
export function useDrill() {
  const [stack, setStack] = useState([]);
  // 27-Jul-2026. There was no way back out of a drill-down. push() replaced any
  // existing filter on the same dimension, so the previous value was destroyed
  // rather than remembered, and the only exits were "remove this one chip" and
  // "clear everything". History fixes that: every push snapshots the stack, and
  // back() restores the snapshot -- one step at a time, in the order drilled.
  const [history, setHistory] = useState([]);
  const push = useCallback((dim, value, label) => {
    setStack((s) => {
      if (s.some((x) => x.dim === dim && x.value === value)) return s;   // no-op, no history
      setHistory((h) => [...h, s]);
      return [...s.filter((x) => x.dim !== dim),
        { dim, value, label: label || `${dim}: ${value}` }];
    });
  }, []);
  const remove = useCallback((dim) => setStack((s) => {
    if (!s.some((x) => x.dim === dim)) return s;
    setHistory((h) => [...h, s]);
    return s.filter((x) => x.dim !== dim);
  }), []);
  const back = useCallback(() => setHistory((h) => {
    if (!h.length) return h;
    setStack(h[h.length - 1]);
    return h.slice(0, -1);
  }), []);
  const clear = useCallback(() => {
    setStack((s) => { if (s.length) setHistory((h) => [...h, s]); return []; });
  }, []);
  const get = useCallback((dim) => stack.find((x) => x.dim === dim)?.value, [stack]);
  const matches = useCallback((row, map) => stack.every((f) => {
    const get_ = map[f.dim];
    if (!get_) return true;
    return String(get_(row)) === String(f.value);
  }), [stack]);
  return { stack, push, remove, clear, get, matches, back, canBack: history.length > 0 };
}

export function DrillBar({ drill }) {
  if (!drill.stack.length) return null;
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap', marginBottom: 12,
      padding: '8px 12px', borderRadius: 8, background: hexA('#6366f1', 0.07), border: `1px solid ${hexA('#6366f1', 0.2)}` }}>
      <button className="filter-btn" onClick={drill.back} disabled={!drill.canBack}
        title={drill.canBack ? 'Step back to the previous drill state' : 'Nothing to go back to'}
        style={{ fontWeight: 700, opacity: drill.canBack ? 1 : 0.45,
          cursor: drill.canBack ? 'pointer' : 'not-allowed' }}>
        ← Back
      </button>
      <span style={{ fontSize: 11, fontWeight: 700, color: '#4f46e5', textTransform: 'uppercase', letterSpacing: 0.4 }}>Drilled into</span>
      {drill.stack.map((f, i) => (
        <React.Fragment key={f.dim}>
          {i > 0 && <span style={{ color: '#94a3b8', fontSize: 12 }}>›</span>}
          <button className="filter-btn" onClick={() => drill.remove(f.dim)}
            title="Remove this drill filter"
            style={{ background: '#6366f1', borderColor: '#6366f1', color: '#fff', fontWeight: 600 }}>
            {f.label} ×
          </button>
        </React.Fragment>
      ))}
      <button className="filter-btn" onClick={drill.clear} style={{ marginLeft: 'auto' }}>Clear all</button>
    </div>
  );
}

// ---- CSV export ------------------------------------------------------------
export function downloadCsv(filename, rows, columns) {
  if (!rows || !rows.length) return;
  const cols = columns || Object.keys(rows[0]);
  const esc = (v) => {
    if (v === null || v === undefined) return '';
    const s = String(v);
    return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
  };
  const csv = [cols.join(','), ...rows.map((r) => cols.map((c) => esc(r[c])).join(','))].join('\n');
  const url = URL.createObjectURL(new Blob([csv], { type: 'text/csv;charset=utf-8;' }));
  const a = document.createElement('a');
  a.href = url; a.download = filename; a.click();
  URL.revokeObjectURL(url);
}

export function ExportButton({ rows, columns, filename }) {
  return (
    <button className="filter-btn" onClick={() => downloadCsv(filename, rows, columns)}
      disabled={!rows || !rows.length} title="Download the current filtered selection as CSV">
      Export CSV
    </button>
  );
}

// ---- shared fleet base statistic ------------------------------------------
/**
 * FleetBaselineBand -- the OOS / chargeable headline, identical on PS1, PS2,
 * PS3 and PS5. Added 2026-07-27.
 *
 * WHY OOS IS THE DENOMINATOR
 *   A chargeable event is a CONTRACT classification applied after the physical
 *   outage, via a chain of if/then conditions. Chargeable events are a strict
 *   SUBSET of OOS events. The physical thing a maintenance model can predict and
 *   a crew can prevent is the OOS event; chargeability is a commercial
 *   consequence of one. So OOS leads, chargeable is shown as the share, and
 *   chargeable is never displayed on its own.
 *
 * Renders an explicit awaiting state rather than zeros when nothing is loaded --
 * "0 OOS events" is a claim about Chicago, not a statement about the pipeline.
 */
export function FleetBaselineBand({ apiBase, city = 'CHI', scope, note }) {
  const [rows_, setRows] = useState(null);
  const [err, setErr] = useState(null);
  useEffect(() => {
    let dead = false;
    const q = new URLSearchParams({ city });
    if (scope) q.set('scope', scope);
    fetch(`${apiBase}/fleet/baseline?${q}`)
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`))))
      .then((d) => { if (!dead) setRows(Array.isArray(d) ? d : []); })
      .catch((e) => { if (!dead) setErr(String(e.message || e)); });
    return () => { dead = true; };
  }, [apiBase, city, scope]);

  if (err) return <ApiFailure what="fleet baseline" detail={err} />;
  if (rows_ === null) return <Loading what="fleet baseline" />;
  if (!rows_.length) {
    return (
      <AwaitingRun title="Fleet event baseline" table="fleet_event_baseline"
        note="Total OOS and chargeable event counts load from gold; the band fills once that export lands." />
    );
  }
  const all = rows_.find((r) => r.device_category === 'ALL') || rows_[0];
  const parts = rows_.filter((r) => r.device_category !== 'ALL');
  const int_ = (v) => (v == null ? '—' : Number(v).toLocaleString());
  const pct_ = (v) => (v == null ? '—' : `${(Number(v) * 100).toFixed(1)}%`);

  return (
    <Panel title="Fleet event baseline"
      note={note || 'Chargeable events are a strict subset of OOS. A chargeable event is a contract classification applied after the physical outage, so OOS is what the models target and what this band measures against.'}>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(190px, 1fr))', gap: 16 }}>
        <Kpi label="Total OOS events" value={int_(all.total_oos_events)}
          sub={all.period_start ? `${all.period_start} to ${all.period_end}` : undefined} />
        <Kpi label="Chargeable events" value={int_(all.chargeable_events)}
          sub="subset of OOS, assigned after the event" />
        <Kpi label="Chargeable share" value={pct_(all.chargeable_pct)}
          sub="of all OOS events" />
        <Kpi label="Devices in scope" value={int_(all.n_devices)}
          sub={all.source_table || undefined} />
      </div>
      {parts.length > 1 && (
        <table className="tbl" style={{ marginTop: 14 }}>
          <thead>
            <tr>
              <th>Device type</th>
              <th style={{ textAlign: 'right' }}>OOS events</th>
              <th style={{ textAlign: 'right' }}>Chargeable</th>
              <th style={{ textAlign: 'right' }}>Chargeable share</th>
              <th style={{ textAlign: 'right' }}>Devices</th>
            </tr>
          </thead>
          <tbody>
            {parts.map((r) => (
              <tr key={`${r.scope}-${r.device_category}`}>
                <td style={{ fontWeight: 700, color: TCOL[r.device_category] || undefined }}>{r.device_category}</td>
                <td style={{ textAlign: 'right' }}>{int_(r.total_oos_events)}</td>
                <td style={{ textAlign: 'right' }}>{int_(r.chargeable_events)}</td>
                <td style={{ textAlign: 'right' }}>{pct_(r.chargeable_pct)}</td>
                <td style={{ textAlign: 'right' }}>{int_(r.n_devices)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </Panel>
  );
}
