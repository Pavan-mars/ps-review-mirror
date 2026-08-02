// =====================================================================
// v2/Charts.jsx -- every chart form the v2 screens use, one house style.
//
// RULES BAKED IN HERE SO NO CALLER CAN BREAK THEM:
//   - one y-axis, ever. No dual-axis chart exists in this file.
//   - categorical hues assigned in fixed order, never cycled.
//   - thin marks, 2px lines, recessive grid, tabular numerals.
//   - a 2px surface gap between adjacent fills so bars never merge.
//   - direct labels on discrete marks; END-ONLY labels on series.
//   - a table view on every chart, so the data is never colour-only.
//   - onDrill makes a mark clickable; the cursor and hover say so.
// =====================================================================
import React, { useMemo, useState } from 'react';
import {
  Area, AreaChart, Bar, BarChart, CartesianGrid, Cell, Funnel, FunnelChart, LabelList,
  Line, LineChart, Pie, PieChart, ResponsiveContainer, Scatter, ScatterChart, Tooltip,
  Treemap, XAxis, YAxis, ZAxis,
} from 'recharts';
import { CARD, CAT, INK, INK_2, INK_3, LINE, SEQ_BLUE, compact, font, nfmt } from './theme';
import { Empty } from './Kit';

const AX = { stroke: LINE, tick: { fontSize: 11, fill: INK_3 }, tickLine: false, axisLine: { stroke: LINE } };
const GRID = { stroke: LINE, strokeDasharray: '2 4', vertical: false };

function Tip({ active, payload, label, fmt }) {
  if (!active || !payload || !payload.length) return null;
  return (
    <div style={{ background: CARD, border: `1px solid ${LINE}`, borderRadius: 10, padding: '9px 12px', boxShadow: '0 8px 24px rgba(15,23,42,.10)', fontSize: 12 }}>
      {label !== undefined && <div style={{ fontWeight: 700, color: INK, marginBottom: 5 }}>{label}</div>}
      {payload.map((p, i) => (
        <div key={i} style={{ display: 'flex', alignItems: 'center', gap: 7, color: INK_2, ...font.num }}>
          <span style={{ width: 8, height: 8, borderRadius: 2, background: p.color || p.fill || CAT[0] }} />
          <span>{p.name}</span>
          <strong style={{ color: INK, marginLeft: 'auto' }}>{fmt ? fmt(p.value) : nfmt(p.value)}</strong>
        </div>
      ))}
    </div>
  );
}

// Every chart can be read as a table. This is the accessibility fallback
// and, in practice, the thing people screenshot into an email.
function TableView({ rows, cols }) {
  if (!rows || !rows.length) return null;
  return (
    <div style={{ maxHeight: 240, overflow: 'auto', border: `1px solid ${LINE}`, borderRadius: 10, marginTop: 10 }}>
      <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
        <thead>
          <tr>
            {cols.map((c) => (
              <th key={c.key} style={{ position: 'sticky', top: 0, background: '#F8FAFC', textAlign: c.num ? 'right' : 'left', padding: '7px 10px', color: INK, fontWeight: 700, fontSize: 11.5, borderBottom: `1px solid ${LINE}` }}>
                {c.label}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={i}>
              {cols.map((c) => (
                <td key={c.key} style={{ padding: '6px 10px', borderTop: `1px solid #F1F5F9`, textAlign: c.num ? 'right' : 'left', color: INK_2, ...(c.num ? font.num : {}) }}>
                  {c.num ? nfmt(r[c.key], c.d || 0) : String(r[c.key] ?? '--')}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function ChartFrame({ children, rows, cols, height = 240 }) {
  const [table, setTable] = useState(false);
  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'flex-end', marginBottom: 2 }}>
        <button
          type="button"
          onClick={() => setTable((t) => !t)}
          style={{ border: 'none', background: 'none', color: INK_3, fontSize: 11, cursor: 'pointer', fontWeight: 600 }}
        >
          {table ? 'Chart' : 'Table'}
        </button>
      </div>
      {table ? <TableView rows={rows} cols={cols} /> : <div style={{ height }}>{children}</div>}
    </div>
  );
}

// ---------------------------------------------------------------------
// RANKING BARS -- horizontal, because category names are words and words
// read horizontally. The most common chart on these screens.
// ---------------------------------------------------------------------
export function RankBars({ data, xKey, yKey, height = 260, color, colorBy, onDrill, fmt, unit }) {
  if (!data || !data.length) return <Empty height={height} />;
  const cols = [{ key: yKey, label: 'Name' }, { key: xKey, label: unit || 'Value', num: true }];
  return (
    <ChartFrame rows={data} cols={cols} height={height}>
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={data} layout="vertical" margin={{ top: 4, right: 54, bottom: 4, left: 4 }} barCategoryGap={4}>
          <CartesianGrid {...GRID} horizontal={false} vertical />
          <XAxis type="number" {...AX} tickFormatter={compact} />
          <YAxis type="category" dataKey={yKey} width={132} {...AX} />
          <Tooltip content={<Tip fmt={fmt} />} cursor={{ fill: '#0F172A08' }} />
          <Bar dataKey={xKey} radius={[0, 4, 4, 0]} isAnimationActive={false}
               onClick={onDrill ? (d) => onDrill(d && d.payload) : undefined}
               cursor={onDrill ? 'pointer' : 'default'}>
            {data.map((d, i) => (
              <Cell key={i} fill={colorBy ? colorBy(d) : color || CAT[0]} stroke={CARD} strokeWidth={2} />
            ))}
            <LabelList dataKey={xKey} position="right" formatter={(v) => (fmt ? fmt(v) : compact(v))}
                       style={{ fontSize: 11, fill: INK_2, fontWeight: 600 }} />
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </ChartFrame>
  );
}

// Grouped/stacked vertical bars for a small number of categories.
export function ColumnBars({ data, xKey, series, height = 240, stacked, onDrill, fmt }) {
  if (!data || !data.length) return <Empty height={height} />;
  const cols = [{ key: xKey, label: 'Group' }, ...series.map((s) => ({ key: s.key, label: s.label, num: true }))];
  return (
    <ChartFrame rows={data} cols={cols} height={height}>
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={data} margin={{ top: 16, right: 8, bottom: 4, left: 0 }} barCategoryGap={stacked ? 14 : 10}>
          <CartesianGrid {...GRID} />
          <XAxis dataKey={xKey} {...AX} interval={0} />
          <YAxis {...AX} tickFormatter={compact} />
          <Tooltip content={<Tip fmt={fmt} />} cursor={{ fill: '#0F172A08' }} />
          {series.map((s, si) => (
            <Bar key={s.key} dataKey={s.key} name={s.label} stackId={stacked ? 'a' : undefined}
                 fill={s.color || CAT[si]} stroke={CARD} strokeWidth={2}
                 radius={stacked ? (si === series.length - 1 ? [4, 4, 0, 0] : [0, 0, 0, 0]) : [4, 4, 0, 0]}
                 isAnimationActive={false}
                 onClick={onDrill ? (d) => onDrill(d && d.payload, s.key) : undefined}
                 cursor={onDrill ? 'pointer' : 'default'}>
              {!stacked && series.length === 1 && (
                <LabelList dataKey={s.key} position="top" formatter={(v) => (fmt ? fmt(v) : compact(v))}
                           style={{ fontSize: 11, fill: INK_2, fontWeight: 600 }} />
              )}
            </Bar>
          ))}
        </BarChart>
      </ResponsiveContainer>
    </ChartFrame>
  );
}

// ---------------------------------------------------------------------
// TREND -- line for rates, area for volume. END-ONLY labels: a number on
// every point of a 90-day series is never read.
// ---------------------------------------------------------------------
export function Trend({ data, xKey, series, height = 240, area, fmt, yUnit }) {
  if (!data || !data.length) return <Empty height={height} />;
  const cols = [{ key: xKey, label: 'Date' }, ...series.map((s) => ({ key: s.key, label: s.label, num: true, d: 1 }))];
  const C = area ? AreaChart : LineChart;
  return (
    <ChartFrame rows={data} cols={cols} height={height}>
      <ResponsiveContainer width="100%" height="100%">
        <C data={data} margin={{ top: 12, right: 46, bottom: 4, left: 0 }}>
          <defs>
            {series.map((s, si) => (
              <linearGradient key={s.key} id={`g_${s.key}`} x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor={s.color || CAT[si]} stopOpacity={0.28} />
                <stop offset="100%" stopColor={s.color || CAT[si]} stopOpacity={0.02} />
              </linearGradient>
            ))}
          </defs>
          <CartesianGrid {...GRID} />
          <XAxis dataKey={xKey} {...AX} minTickGap={26} />
          <YAxis {...AX} tickFormatter={compact} unit={yUnit} width={46} />
          <Tooltip content={<Tip fmt={fmt} />} />
          {series.map((s, si) =>
            area ? (
              <Area key={s.key} type="monotone" dataKey={s.key} name={s.label} stroke={s.color || CAT[si]}
                    strokeWidth={2} fill={`url(#g_${s.key})`} isAnimationActive={false} dot={false} activeDot={{ r: 4, strokeWidth: 2, stroke: CARD }} />
            ) : (
              <Line key={s.key} type="monotone" dataKey={s.key} name={s.label} stroke={s.color || CAT[si]}
                    strokeWidth={2} dot={false} isAnimationActive={false} activeDot={{ r: 4, strokeWidth: 2, stroke: CARD }} />
            )
          )}
        </C>
      </ResponsiveContainer>
    </ChartFrame>
  );
}

// ---------------------------------------------------------------------
// TREEMAP -- share of a whole where the categories are many. Labels are
// truncated to the tile, with a halo, and a title tooltip carries the
// full text. An unreadable tile is worse than an unlabelled one.
// ---------------------------------------------------------------------
const HALO = { stroke: 'rgba(15,23,42,0.55)', strokeWidth: 3, paintOrder: 'stroke' };

function TreeCell(props) {
  const { x, y, width, height, name, value, index, colors, onDrill, payload } = props;
  if (width <= 0 || height <= 0) return null;
  const fill = colors ? colors(payload || props, index) : SEQ_BLUE[Math.min(SEQ_BLUE.length - 1, 2 + (index % 4))];
  const show = width > 82 && height > 34;
  const maxChars = Math.max(3, Math.floor((width - 16) / 6.1));
  const text = String(name || '');
  const shown = text.length > maxChars ? `${text.slice(0, Math.max(1, maxChars - 1))}...` : text;
  return (
    <g onClick={onDrill ? () => onDrill(payload || props) : undefined} style={{ cursor: onDrill ? 'pointer' : 'default' }}>
      <title>{`${text} - ${nfmt(value)}`}</title>
      <rect x={x} y={y} width={width} height={height} fill={fill} stroke={CARD} strokeWidth={2} rx={3} />
      {show && (
        <>
          <text x={x + 8} y={y + 20} fill="#FFFFFF" fontSize={13} fontWeight={700} {...HALO}>{shown}</text>
          <text x={x + 8} y={y + 36} fill="#FFFFFF" fontSize={12} {...HALO}>{compact(value)}</text>
        </>
      )}
    </g>
  );
}

export function TreemapChart({ data, height = 300, colors, onDrill, nameKey = 'name', valueKey = 'value' }) {
  const rows = useMemo(
    () => (data || []).map((d) => ({ ...d, name: d[nameKey], value: Number(d[valueKey]) || 0 })).filter((d) => d.value > 0),
    [data, nameKey, valueKey]
  );
  if (!rows.length) return <Empty height={height} />;
  return (
    <ChartFrame rows={rows} cols={[{ key: 'name', label: 'Name' }, { key: 'value', label: 'Value', num: true }]} height={height}>
      <ResponsiveContainer width="100%" height="100%">
        <Treemap data={rows} dataKey="value" nameKey="name" isAnimationActive={false} stroke={CARD}
                 content={<TreeCell colors={colors} onDrill={onDrill} />} />
      </ResponsiveContainer>
    </ChartFrame>
  );
}

// ---------------------------------------------------------------------
// SCATTER / BUBBLE -- two measures plus optional size. Used for
// "risk vs age" and "impact vs frequency", where the interesting thing is
// the corner, not the average.
// ---------------------------------------------------------------------
export function Bubble({ data, xKey, yKey, zKey, xLabel, yLabel, height = 300, colorBy, onDrill, fmtX, fmtY, nameKey }) {
  if (!data || !data.length) return <Empty height={height} />;
  const cols = [
    ...(nameKey ? [{ key: nameKey, label: 'Item' }] : []),
    { key: xKey, label: xLabel || xKey, num: true, d: 2 },
    { key: yKey, label: yLabel || yKey, num: true, d: 2 },
  ];
  return (
    <ChartFrame rows={data} cols={cols} height={height}>
      <ResponsiveContainer width="100%" height="100%">
        <ScatterChart margin={{ top: 12, right: 16, bottom: 26, left: 4 }}>
          <CartesianGrid {...GRID} vertical />
          <XAxis type="number" dataKey={xKey} name={xLabel} {...AX} tickFormatter={compact}
                 label={{ value: xLabel, position: 'insideBottom', offset: -14, fill: INK_3, fontSize: 11 }} />
          <YAxis type="number" dataKey={yKey} name={yLabel} {...AX} tickFormatter={compact} width={48}
                 label={{ value: yLabel, angle: -90, position: 'insideLeft', fill: INK_3, fontSize: 11 }} />
          {zKey && <ZAxis type="number" dataKey={zKey} range={[40, 460]} />}
          <Tooltip content={<Tip />} cursor={{ strokeDasharray: '3 3', stroke: LINE }} />
          <Scatter data={data} isAnimationActive={false}
                   onClick={onDrill ? (d) => onDrill(d && d.payload) : undefined}
                   cursor={onDrill ? 'pointer' : 'default'}>
            {data.map((d, i) => (
              <Cell key={i} fill={colorBy ? colorBy(d) : CAT[0]} fillOpacity={0.72} stroke={CARD} strokeWidth={2} />
            ))}
          </Scatter>
        </ScatterChart>
      </ResponsiveContainer>
    </ChartFrame>
  );
}

// ---------------------------------------------------------------------
// FUNNEL -- only legitimate when each stage is a strict SUBSET of the one
// above. Used for fleet -> scored -> flagged -> actionable -> ticketed.
// ---------------------------------------------------------------------
export function FunnelView({ data, height = 260, onDrill }) {
  if (!data || !data.length) return <Empty height={height} />;
  const rows = data.map((d, i) => ({ ...d, fill: d.fill || CAT[i % CAT.length] }));
  const top = Number(rows[0].value) || 1;
  return (
    <ChartFrame rows={rows} cols={[{ key: 'name', label: 'Stage' }, { key: 'value', label: 'Devices', num: true }]} height={height}>
      <ResponsiveContainer width="100%" height="100%">
        <FunnelChart margin={{ top: 6, right: 130, bottom: 6, left: 130 }}>
          <Tooltip content={<Tip />} />
          <Funnel dataKey="value" data={rows} isAnimationActive={false} stroke={CARD} strokeWidth={2}
                  onClick={onDrill ? (d) => onDrill(d && (d.payload || d)) : undefined}
                  cursor={onDrill ? 'pointer' : 'default'}>
            <LabelList position="right" dataKey="name" style={{ fontSize: 12, fill: INK, fontWeight: 600 }} />
            <LabelList position="left" dataKey="value"
                       formatter={(v) => `${nfmt(v)}  (${((Number(v) / top) * 100).toFixed(0)}%)`}
                       style={{ fontSize: 11.5, fill: INK_2, fontWeight: 600 }} />
          </Funnel>
        </FunnelChart>
      </ResponsiveContainer>
    </ChartFrame>
  );
}

// ---------------------------------------------------------------------
// DONUT -- share of a whole with FEW slices. Never more than five; beyond
// that a ranking bar is easier to read and this file will not stop you,
// so the caller is expected to fold the tail into "Other".
// ---------------------------------------------------------------------
export function Donut({ data, height = 220, colors, centerLabel, centerValue, onDrill }) {
  if (!data || !data.length) return <Empty height={height} />;
  return (
    <ChartFrame rows={data} cols={[{ key: 'name', label: 'Name' }, { key: 'value', label: 'Value', num: true }]} height={height}>
      <div style={{ position: 'relative', height: '100%' }}>
        <ResponsiveContainer width="100%" height="100%">
          <PieChart>
            <Tooltip content={<Tip />} />
            <Pie data={data} dataKey="value" nameKey="name" innerRadius="62%" outerRadius="88%"
                 paddingAngle={2} isAnimationActive={false} stroke={CARD} strokeWidth={2}
                 onClick={onDrill ? (d) => onDrill(d && (d.payload || d)) : undefined}
                 cursor={onDrill ? 'pointer' : 'default'}>
              {data.map((d, i) => <Cell key={i} fill={colors ? colors(d, i) : CAT[i % CAT.length]} />)}
            </Pie>
          </PieChart>
        </ResponsiveContainer>
        {(centerValue !== undefined) && (
          <div style={{ position: 'absolute', inset: 0, display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', pointerEvents: 'none' }}>
            <div style={{ ...font.hero, ...font.num, fontSize: 26 }}>{centerValue}</div>
            {centerLabel && <div style={{ ...font.micro, marginTop: 3 }}>{centerLabel}</div>}
          </div>
        )}
      </div>
    </ChartFrame>
  );
}

// ---------------------------------------------------------------------
// MATRIX -- a pivot rendered as a heat grid. Sequential single hue, so
// intensity reads as magnitude and nothing else.
// ---------------------------------------------------------------------
export function Matrix({ rows, rowKey, colKey, valKey, height, fmt, onDrill, rowLabel, colLabel }) {
  const { rKeys, cKeys, lookup, max } = useMemo(() => {
    const r = [], c = [], map = {};
    let m = 0;
    (rows || []).forEach((d) => {
      const rk = String(d[rowKey]); const ck = String(d[colKey]); const v = Number(d[valKey]) || 0;
      if (!r.includes(rk)) r.push(rk);
      if (!c.includes(ck)) c.push(ck);
      map[`${rk}||${ck}`] = d;
      if (v > m) m = v;
    });
    return { rKeys: r, cKeys: c, lookup: map, max: m || 1 };
  }, [rows, rowKey, colKey, valKey]);

  if (!rows || !rows.length) return <Empty height={height || 160} />;
  const shade = (v) => SEQ_BLUE[Math.min(SEQ_BLUE.length - 1, Math.round((v / max) * (SEQ_BLUE.length - 1)))];

  return (
    <div style={{ overflowX: 'auto' }}>
      <table style={{ borderCollapse: 'separate', borderSpacing: 2, fontSize: 12 }}>
        <thead>
          <tr>
            <th style={{ ...font.micro, textAlign: 'left', padding: '4px 8px' }}>{rowLabel || ''}</th>
            {cKeys.map((c) => (
              <th key={c} style={{ ...font.micro, padding: '4px 8px', textAlign: 'center', whiteSpace: 'nowrap' }}>{c}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rKeys.map((r) => (
            <tr key={r}>
              <td style={{ padding: '4px 10px 4px 4px', fontWeight: 600, color: INK, whiteSpace: 'nowrap' }}>{r}</td>
              {cKeys.map((c) => {
                const d = lookup[`${r}||${c}`];
                const v = d ? Number(d[valKey]) || 0 : null;
                const dark = v !== null && v / max > 0.55;
                return (
                  <td key={c}
                      onClick={onDrill && d ? () => onDrill(d) : undefined}
                      title={`${r} / ${c}: ${v === null ? 'no data' : (fmt ? fmt(v) : nfmt(v))}`}
                      style={{
                        background: v === null ? '#F8FAFC' : shade(v),
                        color: dark ? '#FFFFFF' : INK_2,
                        padding: '9px 12px', borderRadius: 6, textAlign: 'center', minWidth: 62,
                        cursor: onDrill && d ? 'pointer' : 'default', fontWeight: 600, ...font.num,
                      }}>
                    {v === null ? '--' : (fmt ? fmt(v) : nfmt(v))}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
      {colLabel && <div style={{ ...font.micro, marginTop: 6 }}>{colLabel}</div>}
    </div>
  );
}

// A one-line trend for use inside a table cell or a stat tile.
export function Spark({ data, valKey = 'v', color = CAT[0], width = 96, height = 26 }) {
  const pts = (data || []).map((d) => Number(d[valKey]) || 0);
  if (pts.length < 2) return null;
  const min = Math.min(...pts), max = Math.max(...pts), span = max - min || 1;
  const step = width / (pts.length - 1);
  const d = pts.map((v, i) => `${i === 0 ? 'M' : 'L'}${(i * step).toFixed(1)},${(height - ((v - min) / span) * height).toFixed(1)}`).join(' ');
  return (
    <svg width={width} height={height} style={{ display: 'block' }}>
      <path d={d} fill="none" stroke={color} strokeWidth={2} strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}
