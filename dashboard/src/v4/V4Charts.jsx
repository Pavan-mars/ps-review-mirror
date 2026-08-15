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
  Area, AreaChart, Bar, BarChart, CartesianGrid, Cell, LabelList,
  Line, LineChart, Pie, PieChart, ResponsiveContainer, Scatter, ScatterChart, Tooltip,
  Treemap, XAxis, YAxis, ZAxis,
} from 'recharts';
import { CARD, CAT, INK, INK_2, INK_3, LINKC, LINE, SEQ_BLUE, compact, font, nfmt } from './V4theme';
import { Empty } from './V4Kit';

const AX = { stroke: LINE, tick: { fontSize: 11, fill: INK_3 }, tickLine: false, axisLine: { stroke: LINE } };
const GRID = { stroke: LINE, strokeDasharray: '2 4', vertical: false };

function Tip({ active, payload, label, fmt }) {
  if (!active || !payload || !payload.length) return null;
  // With more than one series the useful question at a hover is not just
  // "what is this" but "how much of the group is it", so the share is
  // computed across the hovered point rather than left to the reader.
  const sum = payload.reduce((a, p) => a + (Number(p.value) || 0), 0);
  const many = payload.length > 1 && sum > 0;
  return (
    <div style={{ background: CARD, border: `1px solid ${LINE}`, borderRadius: 10, padding: '9px 12px', boxShadow: '0 8px 24px rgba(15,23,42,.10)', fontSize: 12.2 }}>
      {label !== undefined && <div style={{ fontWeight: 700, color: INK, marginBottom: 5 }}>{label}</div>}
      {many && (
        <div style={{ display: 'flex', gap: 7, color: INK_3, fontSize: 11, marginBottom: 4,
                      paddingBottom: 4, borderBottom: `1px solid ${LINE}`, ...font.num }}>
          <span>Total</span>
          <strong style={{ color: INK, marginLeft: 'auto' }}>{fmt ? fmt(sum) : nfmt(sum)}</strong>
        </div>
      )}
      {payload.map((p, i) => (
        <div key={i} style={{ display: 'flex', alignItems: 'center', gap: 7, color: INK_2, ...font.num }}>
          <span style={{ width: 8, height: 8, borderRadius: 2, background: p.color || p.fill || CAT[0] }} />
          <span>{p.name}</span>
          <strong style={{ color: INK, marginLeft: 'auto' }}>{fmt ? fmt(p.value) : nfmt(p.value)}</strong>
          {many && (
            <span style={{ color: INK_3, minWidth: 34, textAlign: 'right' }}>
              {`${((Number(p.value) / sum) * 100).toFixed(0)}%`}
            </span>
          )}
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
    <div style={{ maxHeight: 240, overflow: 'auto', border: `1px solid ${LINKC}40`, borderTop: `2px solid ${LINKC}`, borderRadius: 10, marginTop: 10 }}>
      <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12.2 }}>
        <thead>
          <tr>
            {cols.map((c) => (
              <th key={c.key} style={{ position: 'sticky', top: 0, background: `${LINKC}0F`, textAlign: c.num ? 'right' : 'left', padding: '6px 10px', color: LINKC, fontWeight: 750, fontSize: 10.8, letterSpacing: '.05em', textTransform: 'uppercase', borderBottom: `1px solid ${LINKC}33` }}>
                {c.label}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={i} style={{ background: i % 2 ? '#F8FAFC' : CARD }}>
              {cols.map((c) => (
                <td key={c.key} style={{ padding: '5px 10px', borderTop: `1px solid #EEF0F3`, textAlign: c.num ? 'right' : 'left', color: c.num ? INK : INK_2, ...(c.num ? font.num : {}) }}>
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

// LEGEND. The palette validator returns a contrast WARN on five of the
// eight categorical hues against this surface — which the method says
// obligates visible labels or a table view, and is not dismissable. So
// every chart with two or more series carries this legend, and the text
// in it wears INK tokens rather than the series colour: the swatch
// carries identity, the words stay readable.
//
// The value beside each label is the series total. A legend that only
// names things is a colour key; a legend that also carries the number is
// a summary the reader can quote without hovering anything.
export function ChartLegend({ items }) {
  if (!items || items.length < 1) return null;
  return (
    <div style={{ display: 'flex', flexWrap: 'wrap', gap: '4px 16px', marginTop: 8,
                  paddingTop: 7, borderTop: `1px solid ${LINE}` }}>
      {items.map((it, i) => (
        <span key={i} style={{ display: 'inline-flex', alignItems: 'center', gap: 6, fontSize: 11.2 }}>
          <span style={{ width: 9, height: 9, borderRadius: 2.5, background: it.color, flex: '0 0 auto' }} />
          <span style={{ color: INK_2 }}>{it.label}</span>
          {it.value !== undefined && it.value !== null && (
            <strong style={{ color: INK, fontWeight: 700, ...font.num }}>{it.value}</strong>
          )}
          {it.share !== undefined && (
            <span style={{ color: INK_3, ...font.num }}>({it.share})</span>
          )}
        </span>
      ))}
    </div>
  );
}

// The unit caption. An axis of bare numbers is ambiguous — "812" of what?
// One line under the plot answers it once for the whole chart, which is
// cheaper than repeating a unit on every tick.
function Caption({ unit, note }) {
  if (!unit && !note) return null;
  return (
    <div style={{ display: 'flex', gap: 10, marginTop: 6, fontSize: 10.5, color: INK_3 }}>
      {unit && <span style={{ fontWeight: 650, letterSpacing: '.04em', textTransform: 'uppercase' }}>{unit}</span>}
      {note && <span>{note}</span>}
    </div>
  );
}

export function ChartFrame({ children, rows, cols, height = 240, legend, unit, note }) {
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
      {!table && <ChartLegend items={legend} />}
      {!table && <Caption unit={unit} note={note} />}
    </div>
  );
}

// Value label INSIDE a stacked segment, drawn only when the segment is
// tall enough to hold it. Recharts will happily paint an 11px number into
// a 4px band; this refuses instead, so a stack stays readable when one
// category dominates.
function SegLabel({ x, y, width, height, value, fmt }) {
  const h = Number(height) || 0, w = Number(width) || 0;
  if (h < 15 || w < 26 || !value) return null;
  return (
    <text x={x + w / 2} y={y + h / 2 + 4} textAnchor="middle"
          fontSize={10.5} fontWeight={700} fill="#FFFFFF"
          style={{ paintOrder: 'stroke', stroke: 'rgba(15,23,42,.30)', strokeWidth: 2.5 }}>
      {fmt ? fmt(value) : compact(value)}
    </text>
  );
}

// END-ONLY series label. A number on every point of a 90-day line is
// never read and turns the plot into noise; the number at the END is the
// one people actually want, so it is the only one drawn — with a dot in
// the series colour so it binds to its line without recolouring the text.
function EndLabel({ x, y, value, index, n, fmt, color }) {
  if (index !== n - 1 || value === null || value === undefined) return null;
  return (
    <g>
      <circle cx={x} cy={y} r={3} fill={color} stroke={CARD} strokeWidth={1.5} />
      <text x={x + 7} y={y + 4} fontSize={10.5} fontWeight={700} fill={INK}>
        {fmt ? fmt(value) : compact(value)}
      </text>
    </g>
  );
}

// Sum a series across the rows, for the legend.
const total = (rows, key) => (rows || []).reduce((a, r) => a + (Number(r[key]) || 0), 0);

// SPEED. Recharts rebuilds its whole scale and layout on every render of
// the parent, and these screens re-render on every tab, filter and feed
// tick. Wrapping each chart in React.memo means a chart only recomputes
// when ITS OWN props change -- which on a screen carrying eight charts is
// the difference between one chart redrawing and eight.
//
// The comparison is the default shallow one, so callers must not build
// `data` inline in JSX; every screen here already memoises its series.
const memo = (C) => React.memo(C);

// ---------------------------------------------------------------------
// RANKING BARS -- horizontal, because category names are words and words
// read horizontally. The most common chart on these screens.
// ---------------------------------------------------------------------
function RankBarsBase({ data, xKey, yKey, height = 260, color, colorBy, onDrill, fmt, unit, share }) {
  if (!data || !data.length) return <Empty height={height} />;
  const cols = [{ key: yKey, label: 'Name' }, { key: xKey, label: unit || 'Value', num: true }];
  // Share of the visible total, appended to each end label. "812" answers
  // how many; "812 · 41%" answers how many AND whether that is most of it.
  const sum = total(data, xKey);
  // AXIS TICKS MUST RESPECT THE SCALE.                       06-Aug-2026
  // compact() rounds to whole numbers, so a silhouette axis running 0..1
  // printed "0 0 0 0 1" -- five ticks, three of them lying. Below 10 the
  // series is a ratio or a small index and needs its decimals.
  const xMax = Math.max(...data.map((d) => Math.abs(Number(d[xKey]) || 0)), 0);
  const xTick = (v) => (xMax < 10 ? String(+Number(v).toFixed(2)) : compact(v));
  const endLabel = (v) => {
    const base = fmt ? fmt(v) : compact(v);
    if (!sum || !share) return base;
    return `${base}  ${((Number(v) / sum) * 100).toFixed(0)}%`;
  };
  return (
    <ChartFrame rows={data} cols={cols} height={height} unit={unit}>
      <ResponsiveContainer width="100%" height="100%" debounce={80}>
        <BarChart data={data} layout="vertical" margin={{ top: 4, right: share ? 86 : 58, bottom: 4, left: 4 }} barCategoryGap={4}>
          <CartesianGrid {...GRID} horizontal={false} vertical />
          <XAxis type="number" {...AX} tickFormatter={xTick} />
          <YAxis type="category" dataKey={yKey} width={132} {...AX} />
          <Tooltip content={<Tip fmt={fmt} />} cursor={{ fill: '#0F172A08' }} />
          <Bar dataKey={xKey} radius={[0, 4, 4, 0]} isAnimationActive={false}
               onClick={onDrill ? (d) => onDrill(d && d.payload) : undefined}
               cursor={onDrill ? 'pointer' : 'default'}>
            {data.map((d, i) => (
              <Cell key={i} fill={colorBy ? colorBy(d) : color || CAT[0]} stroke={CARD} strokeWidth={2} />
            ))}
            <LabelList dataKey={xKey} position="right" formatter={endLabel}
                       style={{ fontSize: 11, fill: INK, fontWeight: 700 }} />
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </ChartFrame>
  );
}

// Grouped/stacked vertical bars for a small number of categories.
function ColumnBarsBase({ data, xKey, series, height = 240, stacked, onDrill, fmt, unit, note, agg = 'sum' }) {
  if (!data || !data.length) return <Empty height={height} />;
  const cols = [{ key: xKey, label: 'Group' }, ...series.map((s) => ({ key: s.key, label: s.label, num: true }))];
  // A legend is mandatory at two or more series; at one the title already
  // names it, so showing a one-item key is noise.
  //
  // A RATE CANNOT BE SUMMED.                                 06-Aug-2026
  // Trend already knew this; Column did not, so a chart of eleven cluster
  // flag RATES reported "Candidate rate 187.2%" in its legend and gave it a
  // 55% share of a grand total that was itself the sum of two percentages.
  // Callers whose series are rates, ratios or distances pass agg="mean";
  // share is suppressed there because a share of an average is not a share
  // of anything.
  const mean = agg === 'mean';
  const val = (k) => (mean ? total(data, k) / (data.length || 1) : total(data, k));
  const grand = series.reduce((a, s) => a + val(s.key), 0);
  const legend = series.length > 1
    ? series.map((s, si) => {
        const t = val(s.key);
        return {
          color: s.color || CAT[si],
          label: s.label,
          value: fmt ? fmt(t) : compact(t),
          share: (!mean && grand) ? `${((t / grand) * 100).toFixed(0)}%` : undefined,
        };
      })
    : null;
  // TILT THE CATEGORY LABELS RATHER THAN LET THEM OVERPRINT. interval={0}
  // forces every tick to draw, so eleven labels like "Fare Gates c0" in a
  // 380px box collided into an unreadable smear. Tilt buys ~3x the run.
  // Deliberately conservative: a four-fleet chart with "Fare Gates" (exactly
  // ten) must NOT tilt, because tilting costs ~48px of plot height and those
  // labels already fit. Only genuinely long labels, or enough of them that
  // horizontal is hopeless at any width.
  const longest = Math.max(...data.map((d) => String(d[xKey] == null ? '' : d[xKey]).length), 0);
  const tilt = (longest > 10 && data.length > 3) || data.length > 8;
  return (
    <ChartFrame rows={data} cols={cols} height={height} legend={legend} unit={unit}
                note={note || (mean ? 'Legend shows the average across groups, not a total.' : undefined)}>
      <ResponsiveContainer width="100%" height="100%" debounce={80}>
        <BarChart data={data} margin={{ top: 16, right: 8, bottom: tilt ? 6 : 4, left: 0 }} barCategoryGap={stacked ? 14 : 10}>
          <CartesianGrid {...GRID} />
          <XAxis dataKey={xKey} {...AX} interval={0}
                 angle={tilt ? -38 : 0}
                 textAnchor={tilt ? 'end' : 'middle'}
                 height={tilt ? 70 : 30}
                 tick={{ fontSize: tilt ? 10 : 11, fill: INK_3 }} />
          <YAxis {...AX} tickFormatter={compact} />
          <Tooltip content={<Tip fmt={fmt} />} cursor={{ fill: '#0F172A08' }} />
          {series.map((s, si) => (
            <Bar key={s.key} dataKey={s.key} name={s.label} stackId={stacked ? 'a' : undefined}
                 fill={s.color || CAT[si]} stroke={CARD} strokeWidth={2}
                 radius={stacked ? (si === series.length - 1 ? [4, 4, 0, 0] : [0, 0, 0, 0]) : [4, 4, 0, 0]}
                 isAnimationActive={false}
                 onClick={onDrill ? (d) => onDrill(d && d.payload, s.key) : undefined}
                 cursor={onDrill ? 'pointer' : 'default'}>
              {/* VALUES ON EVERY BAR. Stacked segments get the number
                  inside, but only where the segment can hold it; grouped
                  and single bars get it on top. */}
              {stacked ? (
                <LabelList dataKey={s.key} content={<SegLabel fmt={fmt} />} />
              ) : (
                <LabelList dataKey={s.key} position="top" formatter={(v) => (fmt ? fmt(v) : compact(v))}
                           style={{ fontSize: series.length > 2 ? 9.5 : 10.5, fill: INK, fontWeight: 700 }} />
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
function TrendBase({ data, xKey, series, height = 240, area, fmt, yUnit, unit, note }) {
  if (!data || !data.length) return <Empty height={height} />;
  const cols = [{ key: xKey, label: 'Date' }, ...series.map((s) => ({ key: s.key, label: s.label, num: true, d: 1 }))];
  const C = area ? AreaChart : LineChart;
  const last = data[data.length - 1] || {};
  // Legend value is the LATEST reading, not a total: summing a rate over
  // 90 days produces a number that means nothing.
  const legend = series.length > 1
    ? series.map((s, si) => ({
        color: s.color || CAT[si],
        label: s.label,
        value: last[s.key] === undefined ? undefined : (fmt ? fmt(last[s.key]) : compact(last[s.key])),
      }))
    : null;
  return (
    <ChartFrame rows={data} cols={cols} height={height} legend={legend}
                unit={unit || yUnit} note={note || (series.length > 1 ? 'Legend shows the latest reading.' : undefined)}>
      <ResponsiveContainer width="100%" height="100%" debounce={80}>
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
                    strokeWidth={2} fill={`url(#g_${s.key})`} isAnimationActive={false} dot={false} activeDot={{ r: 4, strokeWidth: 2, stroke: CARD }}>
                <LabelList dataKey={s.key} content={<EndLabel n={data.length} fmt={fmt} color={s.color || CAT[si]} />} />
              </Area>
            ) : (
              <Line key={s.key} type="monotone" dataKey={s.key} name={s.label} stroke={s.color || CAT[si]}
                    strokeWidth={2} dot={false} isAnimationActive={false} activeDot={{ r: 4, strokeWidth: 2, stroke: CARD }}>
                <LabelList dataKey={s.key} content={<EndLabel n={data.length} fmt={fmt} color={s.color || CAT[si]} />} />
              </Line>
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
  const share = (payload && payload.share) || props.share || '';
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
          <text x={x + 8} y={y + 36} fill="#FFFFFF" fontSize={12} {...HALO}>
            {compact(value)}{share ? `  ${share}` : ''}
          </text>
        </>
      )}
    </g>
  );
}

function TreemapChartBase({ data, height = 300, colors, onDrill, nameKey = 'name', valueKey = 'value' }) {
  const rows = useMemo(() => {
    const base = (data || [])
      .map((d) => ({ ...d, name: d[nameKey], value: Number(d[valueKey]) || 0 }))
      .filter((d) => d.value > 0);
    const sum = base.reduce((a, d) => a + d.value, 0);
    // Share is precomputed here rather than in the tile renderer, which
    // is called once per tile per repaint.
    return base.map((d) => ({ ...d, share: sum ? `${((d.value / sum) * 100).toFixed(0)}%` : '' }));
  }, [data, nameKey, valueKey]);
  if (!rows.length) return <Empty height={height} />;
  return (
    <ChartFrame rows={rows} cols={[{ key: 'name', label: 'Name' }, { key: 'value', label: 'Value', num: true }]} height={height}>
      <ResponsiveContainer width="100%" height="100%" debounce={80}>
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
function BubbleBase({ data, xKey, yKey, zKey, xLabel, yLabel, height = 300, colorBy, onDrill, fmtX, fmtY, nameKey }) {
  if (!data || !data.length) return <Empty height={height} />;
  const cols = [
    ...(nameKey ? [{ key: nameKey, label: 'Item' }] : []),
    { key: xKey, label: xLabel || xKey, num: true, d: 2 },
    { key: yKey, label: yLabel || yKey, num: true, d: 2 },
  ];
  return (
    <ChartFrame rows={data} cols={cols} height={height}>
      <ResponsiveContainer width="100%" height="100%" debounce={80}>
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
function FunnelViewBase({ data, height = 260, onDrill }) {
  if (!data || !data.length) return <Empty height={height} />;
  const rows = data.map((d, i) => ({ ...d, fill: d.fill || CAT[i % CAT.length] }));
  const top = Number(rows[0].value) || 1;
  // COMPACT. The triangle funnel spent 190px of vertical space and two
  // 130px side gutters to say three numbers, and the taper carried no
  // information the widths did not already carry. Nested bars say the
  // same thing -- each stage a strict subset of the one above -- in a
  // third of the height, and the labels sit ON the bars instead of in
  // margins, so the whole thing fits beside another panel.
  const h = rows.length * 30 + 4;
  return (
    <ChartFrame rows={rows} cols={[{ key: 'name', label: 'Stage' }, { key: 'value', label: 'Devices', num: true }]} height={h}>
      <div style={{ display: 'flex', flexDirection: 'column', justifyContent: 'center', height: '100%', gap: 4 }}>
        {rows.map((r, i) => {
          const v = Number(r.value) || 0;
          const w = Math.max(2, (v / top) * 100);
          return (
            <div key={i}
                 onClick={onDrill ? () => onDrill(r) : undefined}
                 style={{ cursor: onDrill ? 'pointer' : 'default', display: 'flex', alignItems: 'center', gap: 9 }}>
              <div style={{ flex: 1, position: 'relative', height: 22, background: '#F3F4F6', borderRadius: 4, overflow: 'hidden' }}>
                <div style={{ position: 'absolute', inset: 0, width: `${w}%`, background: r.fill, borderRadius: 4 }} />
                <span style={{ position: 'absolute', left: 9, top: 0, lineHeight: '22px', fontSize: 11.2,
                               fontWeight: 700, color: w > 22 ? '#FFFFFF' : INK }}>
                  {r.name}
                </span>
              </div>
              <span style={{ width: 104, textAlign: 'right', fontSize: 11.5, fontWeight: 700, color: INK,
                             fontVariantNumeric: 'tabular-nums' }}>
                {nfmt(v)} <span style={{ color: INK_2, fontWeight: 600 }}>({((v / top) * 100).toFixed(0)}%)</span>
              </span>
            </div>
          );
        })}
      </div>
    </ChartFrame>
  );
}

// ---------------------------------------------------------------------
// DONUT -- share of a whole with FEW slices. Never more than five; beyond
// that a ranking bar is easier to read and this file will not stop you,
// so the caller is expected to fold the tail into "Other".
// ---------------------------------------------------------------------
function DonutBase({ data, height = 220, colors, centerLabel, centerValue, onDrill, unit }) {
  if (!data || !data.length) return <Empty height={height} />;
  const sum = data.reduce((a, d) => a + (Number(d.value) || 0), 0);
  const hue = (d, i) => (colors ? colors(d, i) : CAT[i % CAT.length]);
  // Legend carries the number AND the share, so nobody has to hover a
  // wedge to quote it, and nobody has to estimate an angle by eye.
  const legend = data.map((d, i) => ({
    color: hue(d, i),
    label: d.name,
    value: nfmt(d.value),
    share: sum ? `${((Number(d.value) / sum) * 100).toFixed(0)}%` : undefined,
  }));
  // Wedges under 6% cannot hold a legible label; the legend covers them.
  const sliceLabel = ({ cx, cy, midAngle, innerRadius, outerRadius, percent }) => {
    if (!percent || percent < 0.06) return null;
    const R = innerRadius + (outerRadius - innerRadius) * 0.5;
    const rad = -midAngle * (Math.PI / 180);
    return (
      <text x={cx + R * Math.cos(rad)} y={cy + R * Math.sin(rad)} textAnchor="middle" dominantBaseline="central"
            fontSize={10.5} fontWeight={800} fill="#FFFFFF"
            style={{ paintOrder: 'stroke', stroke: 'rgba(15,23,42,.32)', strokeWidth: 2.5 }}>
        {`${(percent * 100).toFixed(0)}%`}
      </text>
    );
  };
  return (
    <ChartFrame rows={data} cols={[{ key: 'name', label: 'Name' }, { key: 'value', label: 'Value', num: true }]}
                height={height} legend={legend} unit={unit}>
      <div style={{ position: 'relative', height: '100%' }}>
        <ResponsiveContainer width="100%" height="100%" debounce={80}>
          <PieChart>
            <Tooltip content={<Tip />} />
            <Pie data={data} dataKey="value" nameKey="name" innerRadius="62%" outerRadius="88%"
                 paddingAngle={2} isAnimationActive={false} stroke={CARD} strokeWidth={2}
                 labelLine={false} label={sliceLabel}
                 onClick={onDrill ? (d) => onDrill(d && (d.payload || d)) : undefined}
                 cursor={onDrill ? 'pointer' : 'default'}>
              {data.map((d, i) => <Cell key={i} fill={hue(d, i)} />)}
            </Pie>
          </PieChart>
        </ResponsiveContainer>
        {(centerValue !== undefined) && (
          <div style={{ position: 'absolute', inset: 0, display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', pointerEvents: 'none' }}>
            <div style={{ ...font.hero, ...font.num, fontSize: 23.4 }}>{centerValue}</div>
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
function MatrixBase({ rows, rowKey, colKey, valKey, height, fmt, onDrill, rowLabel, colLabel }) {
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
      <table style={{ borderCollapse: 'separate', borderSpacing: 2, fontSize: 12.2 }}>
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
function SparkBase({ data, valKey = 'v', color = CAT[0], width = 96, height = 26 }) {
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

// FONTS_SCALED 04-Aug-2026

// Memoised exports. See the note beside `memo` above.
export const RankBars = memo(RankBarsBase);
export const ColumnBars = memo(ColumnBarsBase);
export const Trend = memo(TrendBase);
export const TreemapChart = memo(TreemapChartBase);
export const Bubble = memo(BubbleBase);
export const FunnelView = memo(FunnelViewBase);
export const Donut = memo(DonutBase);
export const Matrix = memo(MatrixBase);
export const Spark = memo(SparkBase);
