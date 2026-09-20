// =====================================================================
// v4/V4ChartsPlus.jsx -- the rest of the chart vocabulary.
//                                                          04-Aug-2026
// V4Charts already carries: ChartFrame, RankBars, ColumnBars, Trend,
// TreemapChart, Bubble, FunnelView, Donut, Matrix, Spark.
//
// This file adds the forms those cannot express, so a panel can be swapped
// to the RIGHT chart without touching the query behind it. Every component
// here takes plain rows and key names -- the same shape the existing charts
// take -- so a swap is a one-line change at the call site.
//
// EVERY CHART WRAPS ChartFrame, which gives it the table view, the CSV and
// the empty state for free. A chart the reader cannot read as numbers is a
// picture, not evidence.
//
// Colour comes from V4theme. Nothing here invents a hue.
// =====================================================================
import React, { useMemo } from 'react';
import {
  Area, AreaChart, CartesianGrid, Cell, ComposedChart, Line, LineChart,
  PolarAngleAxis, PolarGrid, PolarRadiusAxis, Radar, RadarChart,
  ReferenceLine, ResponsiveContainer, Scatter, ScatterChart, Tooltip,
  XAxis, YAxis, ZAxis,
} from 'recharts';
import { ChartFrame } from './V4Charts';
import { CAT, INK, INK_2, INK_3, LINE, SEQ_BLUE, DIVERGING, STATUS, font, nfmt } from './V4theme';

const n = (v) => (v === null || v === undefined || Number.isNaN(Number(v)) ? 0 : Number(v));
const AX = { fontSize: 11, fill: INK_3 };
const TIP = {
  contentStyle: { border: `1px solid ${LINE}`, borderRadius: 10, fontSize: 11.7 },
  cursor: { stroke: LINE },
};

// ---------------------------------------------------------------------
// 1. AREA CHART -- magnitude over time.
// Use when the QUANTITY matters as much as the direction. A line says
// "which way"; an area says "how much". Never stack areas that are not
// parts of one whole.
// ---------------------------------------------------------------------
export function AreaTrend({ data, xKey, yKey, height = 260, color = CAT[0], label, stacked, series }) {
  const ss = series || [{ key: yKey, label: label || yKey, color }];
  if (!data || !data.length) return null;
  return (
    <ChartFrame rows={data} cols={[{ key: xKey, label: 'Period' }, ...ss.map((s) => ({ key: s.key, label: s.label, num: true }))]} height={height}>
      <ResponsiveContainer width="100%" height="100%">
        <AreaChart data={data} margin={{ top: 12, right: 10, bottom: 4, left: 0 }}>
          <defs>
            {ss.map((s) => (
              <linearGradient key={s.key} id={`ar-${s.key}`} x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor={s.color} stopOpacity={0.34} />
                <stop offset="100%" stopColor={s.color} stopOpacity={0.02} />
              </linearGradient>
            ))}
          </defs>
          <CartesianGrid stroke={LINE} strokeDasharray="3 3" vertical={false} />
          <XAxis dataKey={xKey} tick={AX} tickLine={false} axisLine={{ stroke: LINE }} />
          <YAxis tick={AX} tickLine={false} axisLine={false} width={54} />
          <Tooltip {...TIP} />
          {ss.map((s) => (
            <Area key={s.key} type="monotone" dataKey={s.key} name={s.label}
                  stroke={s.color} strokeWidth={2} fill={`url(#ar-${s.key})`}
                  stackId={stacked ? 'a' : undefined} />
          ))}
        </AreaChart>
      </ResponsiveContainer>
    </ChartFrame>
  );
}

// ---------------------------------------------------------------------
// 2. DISCRETE STEP LINE -- a value that HOLDS until it changes.
// Risk tiers, thresholds, states, counts read once a day. A sloped line
// between two daily readings implies values in between that were never
// measured; a step does not.
// ---------------------------------------------------------------------
export function StepLine({ data, xKey, yKey, height = 240, color = CAT[2], label }) {
  if (!data || !data.length) return null;
  return (
    <ChartFrame rows={data} cols={[{ key: xKey, label: 'Point' }, { key: yKey, label: label || yKey, num: true }]} height={height}>
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={data} margin={{ top: 12, right: 10, bottom: 4, left: 0 }}>
          <CartesianGrid stroke={LINE} strokeDasharray="3 3" vertical={false} />
          <XAxis dataKey={xKey} tick={AX} tickLine={false} axisLine={{ stroke: LINE }} />
          <YAxis tick={AX} tickLine={false} axisLine={false} width={54} />
          <Tooltip {...TIP} />
          <Line type="stepAfter" dataKey={yKey} name={label || yKey} stroke={color}
                strokeWidth={2.4} dot={{ r: 2.5, fill: color }} activeDot={{ r: 4 }} />
        </LineChart>
      </ResponsiveContainer>
    </ChartFrame>
  );
}

// ---------------------------------------------------------------------
// 3. BINNED FREQUENCY DISTRIBUTION (histogram).
// Answers "what does the SPREAD look like", which an average hides. Bins
// are computed here rather than asked of the caller, because an arbitrary
// bin count is how a distribution gets made to look bimodal.
// ---------------------------------------------------------------------
export function Histogram({ data, valueKey, bins = 20, height = 260, color = CAT[0], xLabel }) {
  const rows = useMemo(() => {
    const vs = (data || []).map((d) => n(d[valueKey])).filter((v) => Number.isFinite(v));
    if (!vs.length) return [];
    const min = Math.min(...vs), max = Math.max(...vs);
    const span = (max - min) || 1;
    const w = span / bins;
    const out = Array.from({ length: bins }, (_, i) => ({
      bin: `${nfmt(min + i * w, w < 1 ? 2 : 0)}`,
      lo: min + i * w, hi: min + (i + 1) * w, count: 0,
    }));
    vs.forEach((v) => {
      const i = Math.min(bins - 1, Math.floor((v - min) / w));
      out[i].count += 1;
    });
    return out;
  }, [data, valueKey, bins]);
  if (!rows.length) return null;
  const peak = Math.max(...rows.map((r) => r.count));
  return (
    <ChartFrame rows={rows} cols={[{ key: 'bin', label: xLabel || 'Bin' }, { key: 'count', label: 'Count', num: true }]} height={height}>
      <div style={{ display: 'flex', alignItems: 'flex-end', gap: 2, height: height - 34, padding: '8px 2px 0' }}>
        {rows.map((r) => (
          <div key={r.bin} title={`${nfmt(r.lo, 1)} to ${nfmt(r.hi, 1)}: ${nfmt(r.count)}`}
               style={{ flex: 1, display: 'flex', flexDirection: 'column', justifyContent: 'flex-end', height: '100%' }}>
            <div style={{ height: `${(r.count / peak) * 100}%`, background: color, borderRadius: '4px 4px 0 0',
                          opacity: 0.45 + 0.55 * (r.count / peak), minHeight: r.count ? 2 : 0 }} />
          </div>
        ))}
      </div>
      <div style={{ display: 'flex', justifyContent: 'space-between', ...font.micro, color: INK_3, marginTop: 4 }}>
        <span>{nfmt(rows[0].lo, 1)}</span>
        <span>{xLabel || ''}</span>
        <span>{nfmt(rows[rows.length - 1].hi, 1)}</span>
      </div>
    </ChartFrame>
  );
}

// ---------------------------------------------------------------------
// 4. SCATTER + OPTIONAL TREND LINE.
// Plain scatter for two measures with no third dimension -- use Bubble
// when a size actually means something. The trend line is least-squares
// and is drawn ONLY when asked for, with r-squared reported: a line
// through a cloud with no correlation is a lie told confidently.
// ---------------------------------------------------------------------
export function ScatterPlot({ data, xKey, yKey, height = 300, color = CAT[0], xLabel, yLabel, trend, onDrill }) {
  const pts = (data || []).map((d) => ({ ...d, __x: n(d[xKey]), __y: n(d[yKey]) }));
  const fit = useMemo(() => {
    if (!trend || pts.length < 3) return null;
    const N = pts.length;
    const sx = pts.reduce((a, p) => a + p.__x, 0), sy = pts.reduce((a, p) => a + p.__y, 0);
    const mx = sx / N, my = sy / N;
    const num = pts.reduce((a, p) => a + (p.__x - mx) * (p.__y - my), 0);
    const den = pts.reduce((a, p) => a + (p.__x - mx) ** 2, 0) || 1;
    const m = num / den, b = my - m * mx;
    const ssTot = pts.reduce((a, p) => a + (p.__y - my) ** 2, 0) || 1;
    const ssRes = pts.reduce((a, p) => a + (p.__y - (m * p.__x + b)) ** 2, 0);
    const xs = pts.map((p) => p.__x);
    return { m, b, r2: 1 - ssRes / ssTot, x0: Math.min(...xs), x1: Math.max(...xs) };
  }, [pts, trend]);
  if (!pts.length) return null;
  return (
    <ChartFrame rows={data} cols={[{ key: xKey, label: xLabel || xKey, num: true }, { key: yKey, label: yLabel || yKey, num: true }]} height={height}>
      <>
        <ResponsiveContainer width="100%" height={fit ? height - 22 : height}>
          <ScatterChart margin={{ top: 14, right: 16, bottom: 22, left: 4 }}>
            <CartesianGrid stroke={LINE} strokeDasharray="3 3" />
            <XAxis type="number" dataKey="__x" tick={AX} tickLine={false} axisLine={{ stroke: LINE }}
                   label={xLabel ? { value: xLabel, position: 'insideBottom', offset: -12, style: AX } : undefined} />
            <YAxis type="number" dataKey="__y" tick={AX} tickLine={false} axisLine={false} width={58}
                   label={yLabel ? { value: yLabel, angle: -90, position: 'insideLeft', style: AX } : undefined} />
            <Tooltip {...TIP} />
            {fit && (
              <ReferenceLine ifOverflow="extendDomain" stroke={INK_3} strokeDasharray="5 4"
                             segment={[{ x: fit.x0, y: fit.m * fit.x0 + fit.b }, { x: fit.x1, y: fit.m * fit.x1 + fit.b }]} />
            )}
            <Scatter data={pts} fill={color} onClick={onDrill} />
          </ScatterChart>
        </ResponsiveContainer>
        {fit && (
          <div style={{ ...font.note, fontSize: 11.2, textAlign: 'right' }}>
            Least-squares fit, r&sup2; = {fit.r2.toFixed(2)}
            {fit.r2 < 0.2 && ' -- weak, treat the line as decoration rather than a relationship'}
          </div>
        )}
      </>
    </ChartFrame>
  );
}

// ---------------------------------------------------------------------
// 5. HEATMAP / CORRELATION MATRIX.
// One grid, two uses. `diverging` colours a correlation (-1..1) with a
// NEUTRAL midpoint; without it, a single-hue ramp carries magnitude.
// A rainbow ramp is never offered, because rank in a rainbow is invented.
// ---------------------------------------------------------------------
export function HeatGrid({ rows, rowKey, colKey, valKey, height = 320, diverging, fmt = (v) => nfmt(v, 2), rowLabel = '' }) {
  const rs = [...new Set((rows || []).map((r) => r[rowKey]))];
  const cs = [...new Set((rows || []).map((r) => r[colKey]))];
  const at = {};
  (rows || []).forEach((r) => { at[`${r[rowKey]}|||${r[colKey]}`] = n(r[valKey]); });
  const vals = Object.values(at);
  // DIVERGING DOMAIN MUST BE SYMMETRIC AND DATA-DRIVEN.       06-Aug-2026
  // This hardcoded [-1, +1], which is right for a correlation coefficient
  // and wrong for anything else. PS2's association statistic spans roughly
  // -161 to +211, so every cell clamped to an extreme and the whole grid
  // rendered as two flat colours -- worse than the sequential ramp it was
  // meant to replace. Symmetric about zero is what makes the neutral
  // midpoint mean zero, which is the entire point of a diverging scale.
  const mag = Math.max(1e-9, ...vals.map((v) => Math.abs(v)));
  const lo = diverging ? -mag : Math.min(...vals, 0);
  const hi = diverging ? mag : Math.max(...vals, 1);
  const colour = (v) => {
    if (v === undefined) return '#FFFFFF';
    if (diverging) {
      const t = Math.max(0, Math.min(1, (v - lo) / (hi - lo)));
      const i = Math.min(DIVERGING.length - 1, Math.floor(t * DIVERGING.length));
      return DIVERGING[i];
    }
    const t = Math.max(0, Math.min(1, (v - lo) / ((hi - lo) || 1)));
    return SEQ_BLUE[Math.min(SEQ_BLUE.length - 1, Math.floor(t * SEQ_BLUE.length))];
  };
  if (!rs.length || !cs.length) return null;
  return (
    <ChartFrame rows={rows} cols={[{ key: rowKey, label: rowLabel || 'Row' }, { key: colKey, label: 'Column' }, { key: valKey, label: 'Value', num: true }]} height={height}>
      <div style={{ overflowX: 'auto' }}>
        <table style={{ borderCollapse: 'separate', borderSpacing: 3, fontSize: 11.2 }}>
          <thead>
            <tr>
              <th />
              {cs.map((c) => (
                <th key={c} style={{ ...font.micro, color: INK_2, padding: '0 6px', textAlign: 'center', fontWeight: 700 }}>{String(c)}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rs.map((r) => (
              <tr key={r}>
                <td style={{ ...font.micro, color: INK_2, paddingRight: 8, whiteSpace: 'nowrap', fontWeight: 700 }}>{String(r)}</td>
                {cs.map((c) => {
                  const v = at[`${r}|||${c}`];
                  const bg = colour(v);
                  return (
                    <td key={c} title={`${r} / ${c}: ${v === undefined ? 'no value' : fmt(v)}`}
                        style={{ background: bg, borderRadius: 6, minWidth: 46, height: 34,
                                 textAlign: 'center', color: v === undefined ? INK_3 : INK,
                                 fontWeight: 650 }}>
                      {v === undefined ? '--' : fmt(v)}
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </ChartFrame>
  );
}

// ---------------------------------------------------------------------
// 6. KEY INFLUENCERS -- what moves the outcome, and IN WHICH DIRECTION.
// Signed bars from a centre line. Direction is the whole point: a driver
// that pushes risk DOWN is not a small version of one that pushes it up.
// ---------------------------------------------------------------------
export function KeyInfluencers({ data, nameKey = 'name', valueKey = 'value', height = 300, fmt = (v) => nfmt(v, 3) }) {
  const rows = (data || []).map((d) => ({ ...d, __v: n(d[valueKey]) }))
    .sort((a, b) => Math.abs(b.__v) - Math.abs(a.__v)).slice(0, 12);
  if (!rows.length) return null;
  const max = Math.max(...rows.map((r) => Math.abs(r.__v))) || 1;
  return (
    <ChartFrame rows={data} cols={[{ key: nameKey, label: 'Driver' }, { key: valueKey, label: 'Effect', num: true }]} height={height}>
      <div style={{ display: 'grid', gap: 7 }}>
        {rows.map((r) => {
          const up = r.__v >= 0;
          const w = (Math.abs(r.__v) / max) * 50;
          const c = up ? STATUS.critical.fill : STATUS.good.fill;
          return (
            <div key={String(r[nameKey])} style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
              <span style={{ width: 168, fontSize: 11.2, color: INK_2, textAlign: 'right',
                             overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                {String(r[nameKey]).replace(/_/g, ' ')}
              </span>
              <span style={{ position: 'relative', flex: 1, height: 16 }}>
                <span style={{ position: 'absolute', left: '50%', top: 0, bottom: 0, width: 1, background: LINE }} />
                <span style={{ position: 'absolute', top: 2, height: 12, borderRadius: 3, background: c,
                               left: up ? '50%' : `${50 - w}%`, width: `${w}%` }} />
              </span>
              <span style={{ ...font.num, fontSize: 11.2, width: 62, textAlign: 'right', color: c, fontWeight: 700 }}>
                {fmt(r.__v)}
              </span>
            </div>
          );
        })}
        <div style={{ ...font.note, fontSize: 11, marginTop: 2 }}>
          Right of the centre line pushes the outcome up, left pushes it down.
        </div>
      </div>
    </ChartFrame>
  );
}

// ---------------------------------------------------------------------
// 7. SLOPE CHART -- two points in time, one line each.
// The clearest way to show WHO MOVED. A grouped bar chart makes the reader
// compute the change; a slope draws it.
// ---------------------------------------------------------------------
export function SlopeChart({ data, nameKey = 'name', fromKey = 'from', toKey = 'to', height = 320, fromLabel = 'Before', toLabel = 'After', fmt = (v) => nfmt(v) }) {
  const rows = (data || []).map((d) => ({ name: d[nameKey], a: n(d[fromKey]), b: n(d[toKey]) }));
  if (!rows.length) return null;
  const all = rows.flatMap((r) => [r.a, r.b]);
  const lo = Math.min(...all), hi = Math.max(...all), span = (hi - lo) || 1;
  const H = height - 46, pad = 14;
  const y = (v) => pad + (1 - (v - lo) / span) * (H - pad * 2);
  return (
    <ChartFrame rows={data} cols={[{ key: nameKey, label: 'Item' }, { key: fromKey, label: fromLabel, num: true }, { key: toKey, label: toLabel, num: true }]} height={height}>
      <svg width="100%" height={H} style={{ overflow: 'visible' }}>
        {rows.map((r, i) => {
          const up = r.b >= r.a;
          const c = up ? STATUS.critical.fill : STATUS.good.fill;
          return (
            <g key={r.name}>
              <line x1="22%" y1={y(r.a)} x2="78%" y2={y(r.b)} stroke={c} strokeWidth={2} opacity={0.85} />
              <circle cx="22%" cy={y(r.a)} r={4} fill={c} />
              <circle cx="78%" cy={y(r.b)} r={4} fill={c} />
              <text x="21%" y={y(r.a)} textAnchor="end" dominantBaseline="middle" fontSize="11.5" fill={INK_2}>
                {String(r.name)} {fmt(r.a)}
              </text>
              <text x="79%" y={y(r.b)} textAnchor="start" dominantBaseline="middle" fontSize="11.5" fill={INK}>
                {fmt(r.b)}
              </text>
            </g>
          );
        })}
      </svg>
      <div style={{ display: 'flex', justifyContent: 'space-between', ...font.micro, color: INK_3 }}>
        <span>{fromLabel}</span><span>{toLabel}</span>
      </div>
    </ChartFrame>
  );
}

// ---------------------------------------------------------------------
// 8. BUMP CHART -- rank over time.
// Rank, not value. Use when "who is worst this week" matters more than by
// how much. Rank 1 sits at the TOP, which is the only arrangement people
// read without a legend.
// ---------------------------------------------------------------------
export function BumpChart({ data, nameKey = 'name', periodKey = 'period', rankKey = 'rank', height = 320 }) {
  const names = [...new Set((data || []).map((d) => d[nameKey]))];
  const periods = [...new Set((data || []).map((d) => d[periodKey]))];
  if (!names.length || periods.length < 2) return null;
  const maxRank = Math.max(...(data || []).map((d) => n(d[rankKey])), 1);
  const H = height - 34, pad = 16;
  const x = (i) => `${(i / (periods.length - 1)) * 78 + 11}%`;
  const y = (r) => pad + ((r - 1) / Math.max(maxRank - 1, 1)) * (H - pad * 2);
  return (
    <ChartFrame rows={data} cols={[{ key: nameKey, label: 'Item' }, { key: periodKey, label: 'Period' }, { key: rankKey, label: 'Rank', num: true }]} height={height}>
      <svg width="100%" height={H} style={{ overflow: 'visible' }}>
        {names.map((nm, k) => {
          const c = CAT[k % CAT.length];
          const pts = periods.map((p, i) => {
            const row = (data || []).find((d) => d[nameKey] === nm && d[periodKey] === p);
            return row ? { i, r: n(row[rankKey]) } : null;
          }).filter(Boolean);
          return (
            <g key={String(nm)}>
              <polyline fill="none" stroke={c} strokeWidth={2.2} strokeLinejoin="round"
                        points={pts.map((p) => `${(p.i / (periods.length - 1)) * 78 + 11}%,${y(p.r)}`).join(' ')}
                        style={{ vectorEffect: 'non-scaling-stroke' }} />
              {pts.map((p) => <circle key={p.i} cx={x(p.i)} cy={y(p.r)} r={4} fill={c} />)}
              {pts[0] && (
                <text x="10%" y={y(pts[0].r)} textAnchor="end" dominantBaseline="middle" fontSize="11.5" fill={INK_2}>{String(nm)}</text>
              )}
            </g>
          );
        })}
      </svg>
      <div style={{ display: 'flex', justifyContent: 'space-between', padding: '0 9%', ...font.micro, color: INK_3 }}>
        {periods.map((p) => <span key={String(p)}>{String(p)}</span>)}
      </div>
    </ChartFrame>
  );
}

// ---------------------------------------------------------------------
// 9. RADAR -- one entity across several bounded measures.
// Only honest when every axis shares a scale (all 0-100, all 0-1). Mixed
// units on a radar produce a shape that means nothing, so the caller must
// normalise before calling.
// ---------------------------------------------------------------------
export function Radar360({ data, angleKey = 'axis', valueKeys = ['value'], labels, height = 320, colors }) {
  if (!data || !data.length) return null;
  const cols = valueKeys.map((k, i) => ({ key: k, label: (labels && labels[i]) || k, num: true }));
  return (
    <ChartFrame rows={data} cols={[{ key: angleKey, label: 'Measure' }, ...cols]} height={height}>
      <ResponsiveContainer width="100%" height="100%">
        <RadarChart data={data} outerRadius="72%">
          <PolarGrid stroke={LINE} />
          <PolarAngleAxis dataKey={angleKey} tick={{ fontSize: 11, fill: INK_2 }} />
          <PolarRadiusAxis tick={{ fontSize: 10.5, fill: INK_3 }} axisLine={false} />
          <Tooltip {...TIP} />
          {valueKeys.map((k, i) => {
            const c = (colors && colors[i]) || CAT[i % CAT.length];
            return <Radar key={k} name={(labels && labels[i]) || k} dataKey={k}
                          stroke={c} fill={c} fillOpacity={0.22} strokeWidth={2} />;
          })}
        </RadarChart>
      </ResponsiveContainer>
    </ChartFrame>
  );
}

// ---------------------------------------------------------------------
// 10. NETWORK GRAPH -- who leads to whom.
// Nodes on a circle, edges weighted by strength. A force layout looks
// better and moves every time you open it, which makes two screenshots
// impossible to compare; a fixed circle is boring and reproducible.
// ---------------------------------------------------------------------
export function NetworkGraph({ nodes, edges, height = 360, sourceKey = 'source', targetKey = 'target', weightKey = 'weight', onDrill }) {
  const ns = (nodes || []).map((d) => (typeof d === 'string' ? { id: d } : d));
  if (!ns.length || !edges || !edges.length) return null;
  const R = (height - 60) / 2, cx = '50%', cy = height / 2 - 10;
  const pos = {};
  ns.forEach((d, i) => {
    const a = (i / ns.length) * Math.PI * 2 - Math.PI / 2;
    pos[d.id] = { x: 50 + Math.cos(a) * 38, y: cy + Math.sin(a) * R };
  });
  const maxW = Math.max(...edges.map((e) => n(e[weightKey])), 1);
  return (
    <ChartFrame rows={edges} cols={[{ key: sourceKey, label: 'From' }, { key: targetKey, label: 'To' }, { key: weightKey, label: 'Strength', num: true }]} height={height}>
      <svg width="100%" height={height - 20} style={{ overflow: 'visible' }}>
        {edges.map((e, i) => {
          const a = pos[e[sourceKey]], b = pos[e[targetKey]];
          if (!a || !b) return null;
          const w = n(e[weightKey]) / maxW;
          return <line key={i} x1={`${a.x}%`} y1={a.y} x2={`${b.x}%`} y2={b.y}
                       stroke={CAT[0]} strokeWidth={0.6 + w * 3.4} opacity={0.16 + w * 0.5} />;
        })}
        {ns.map((d, i) => (
          <g key={d.id} onClick={() => onDrill && onDrill(d)} style={{ cursor: onDrill ? 'pointer' : 'default' }}>
            <circle cx={`${pos[d.id].x}%`} cy={pos[d.id].y} r={d.size || 7} fill={d.color || CAT[i % CAT.length]} />
            <text x={`${pos[d.id].x}%`} y={pos[d.id].y - 12} textAnchor="middle" fontSize="11" fill={INK_2}>
              {String(d.label || d.id)}
            </text>
          </g>
        ))}
      </svg>
    </ChartFrame>
  );
}

// ---------------------------------------------------------------------
// 10b. SANKEY FLOW -- how much goes from each source to each target.
// Annexure 4 Dashboard 2 names a cascade flow view. This data is strictly
// ONE HOP (subsystem_from -> subsystem_to), so a two-column ribbon diagram
// is the whole of it; a multi-level d3-sankey layout would add a solver
// whose output shifts between renders, and this file already refuses that
// trade for NetworkGraph. Node order is by total flow, descending, so the
// picture is reproducible.
//
// Ribbon THICKNESS is the value. Ribbon ORDER within a node follows the
// same descending rule, which keeps the largest flows adjacent to the axis
// where they are easiest to compare.
// ---------------------------------------------------------------------
export function SankeyFlow({
  rows, sourceKey = 'source', targetKey = 'target', valueKey = 'value',
  height = 380, maxNodes = 12, unit, note,
}) {
  const model = useMemo(() => {
    const clean = (rows || [])
      .map((r) => ({ s: String(r[sourceKey] ?? ''), t: String(r[targetKey] ?? ''), v: n(r[valueKey]) }))
      .filter((r) => r.s && r.t && r.v > 0);
    if (!clean.length) return null;

    const tally = (key) => {
      const m = new Map();
      clean.forEach((r) => m.set(r[key], (m.get(r[key]) || 0) + r.v));
      return [...m.entries()].sort((a, b) => b[1] - a[1]).slice(0, maxNodes);
    };
    const left = tally('s');
    const right = tally('t');
    const lKeep = new Set(left.map((d) => d[0]));
    const rKeep = new Set(right.map((d) => d[0]));
    const links = clean.filter((r) => lKeep.has(r.s) && rKeep.has(r.t))
                       .sort((a, b) => b.v - a.v);
    if (!links.length) return null;

    // Lay out each column against ITS OWN total, so both columns fill the
    // height. Using one shared total would shrink whichever side has flows
    // that were cut by maxNodes, which reads as missing data.
    //
    // THE FLOOR HAS TO BE PAID FOR.                            20-Sep-2026
    // Giving every node a 3px minimum makes a small subsystem visible, but
    // ten floored nodes add 30px the proportional split never budgeted, and
    // the column then runs off the bottom of the SVG -- measured at 393.5px
    // in a 380px canvas on the real 10x10 Chicago data. So: apply the floor,
    // then rescale the column to fit. A floored band may end up slightly
    // under the minimum after rescaling, which is the right trade against
    // silently drawing outside the viewport.
    const GAP = 6;
    const place = (entries) => {
      const total = entries.reduce((t, d) => t + d[1], 0) || 1;
      // The SVG is (height - 20) tall, so a 20px band top and bottom costs 40
      // off THAT, not off height. Budgeting height-40 left no bottom margin
      // and the last node sat flush on the edge.
      const usable = height - 60 - GAP * Math.max(0, entries.length - 1);
      let hs = entries.map(([, v]) => Math.max(3, (v / total) * usable));
      const sum = hs.reduce((a, b) => a + b, 0);
      if (sum > usable) { const k = usable / sum; hs = hs.map((h) => h * k); }
      const pos = new Map();
      let y = 20;
      entries.forEach(([id, v], i) => {
        pos.set(id, { y0: y, y1: y + hs[i], v, cursor: y });
        y += hs[i] + GAP;
      });
      return pos;
    };
    return { links, L: place(left), R: place(right), left, right };
  }, [rows, sourceKey, targetKey, valueKey, height, maxNodes]);

  if (!model) return null;
  const { links, L, R, left, right } = model;

  // Each ribbon consumes its share of both endpoints, so a node's band is
  // exactly filled by the flows that touch it.
  const lTot = new Map(left);
  const rTot = new Map(right);
  const ribbons = links.map((lk, i) => {
    const a = L.get(lk.s), b = R.get(lk.t);
    const ah = ((a.y1 - a.y0) * lk.v) / (lTot.get(lk.s) || 1);
    const bh = ((b.y1 - b.y0) * lk.v) / (rTot.get(lk.t) || 1);
    const a0 = a.cursor, b0 = b.cursor;
    a.cursor += ah; b.cursor += bh;
    return { ...lk, i, a0, a1: a0 + ah, b0, b1: b0 + bh };
  });

  const X0 = 14, X1 = 86;            // percent: left and right node columns
  const maxV = Math.max(...links.map((d) => d.v), 1);
  const colOf = (id) => CAT[left.findIndex((d) => d[0] === id) % CAT.length];

  // A skewed flow distribution -- Chicago's top pair carries 45% of all
  // cascades -- leaves most ribbons under a pixel. They are drawn anyway,
  // because thickness IS the value and inflating the small ones would lie.
  // What the chart owes the reader is to say so, and to point at the table
  // view where every flow is legible as a number.
  const thin = ribbons.filter((d) => (d.a1 - d.a0) < 0.5);
  const thinShare = thin.reduce((t, d) => t + d.v, 0)
                  / (ribbons.reduce((t, d) => t + d.v, 0) || 1);
  const autoNote = thin.length
    ? `${thin.length} of ${ribbons.length} flows are too thin to see at this size `
      + `(${(thinShare * 100).toFixed(1)}% of total flow). Switch to Table for all of them.`
    : null;

  return (
    <ChartFrame
      rows={links.map((d) => ({ from: d.s, to: d.t, value: d.v }))}
      cols={[{ key: 'from', label: 'From' }, { key: 'to', label: 'To' },
             { key: 'value', label: 'Flow', num: true }]}
      height={height} unit={unit} note={[note, autoNote].filter(Boolean).join(' ')}
    >
      <svg width="100%" height={height - 20} style={{ overflow: 'visible' }}>
        {ribbons.map((d) => (
          <path
            key={d.i}
            d={`M ${X0}% ${d.a0} C 50% ${d.a0}, 50% ${d.b0}, ${X1}% ${d.b0}
                L ${X1}% ${d.b1} C 50% ${d.b1}, 50% ${d.a1}, ${X0}% ${d.a1} Z`}
            fill={colOf(d.s)}
            opacity={0.14 + (d.v / maxV) * 0.4}
          />
        ))}
        {[...L.entries()].map(([id, p]) => (
          <g key={`l-${id}`}>
            <rect x={`${X0 - 1.6}%`} y={p.y0} width="1.6%" height={Math.max(2, p.y1 - p.y0)}
                  fill={colOf(id)} rx={1} />
            <text x={`${X0 - 2.6}%`} y={(p.y0 + p.y1) / 2 + 3.5} textAnchor="end"
                  fontSize="11" fill={INK_2}>{id}</text>
          </g>
        ))}
        {[...R.entries()].map(([id, p]) => (
          <g key={`r-${id}`}>
            <rect x={`${X1}%`} y={p.y0} width="1.6%" height={Math.max(2, p.y1 - p.y0)}
                  fill={INK_3} rx={1} />
            <text x={`${X1 + 2.6}%`} y={(p.y0 + p.y1) / 2 + 3.5} textAnchor="start"
                  fontSize="11" fill={INK_2}>{id}</text>
          </g>
        ))}
        <text x={`${X0 - 2.6}%`} y="10" textAnchor="end" style={font.micro} fill={INK_3}>from</text>
        <text x={`${X1 + 2.6}%`} y="10" textAnchor="start" style={font.micro} fill={INK_3}>to</text>
      </svg>
    </ChartFrame>
  );
}

// ---------------------------------------------------------------------
// 11. DECOMPOSITION TREE -- a total, broken down, one level at a time.
// Each level states what it removed or split, so a reader can follow a
// number from the headline to the leaf without re-running the query.
// ---------------------------------------------------------------------
export function DecompositionTree({ root, levels, height, fmt = (v) => nfmt(v) }) {
  if (!root || !levels || !levels.length) return null;
  const total = n(root.value) || 1;
  // HEIGHT FOLLOWS THE CONTENT.                            06-Aug-2026
  // This defaulted to a fixed 340px, and ChartFrame renders its child in a
  // box of exactly that height. PS3's tree has TWO items per level, so it
  // drew about 150px of content inside a 340px box and left ~190px of dead
  // white space bordered like a panel -- which reads as a chart that failed
  // to load, not as a chart that is simply short. Measure instead: each row
  // is a 7px-padded box with one line of text and a 5px bar, ~52px with the
  // gap, plus the level caption. A caller can still pass `height` to pin it.
  const deepest = Math.max(1, ...levels.map((l) => Math.min(6, (l.items || []).length)));
  const h = height || Math.max(120, deepest * 52 + 26);
  return (
    <ChartFrame rows={levels.flatMap((l) => l.items.map((it) => ({ level: l.label, name: it.name, value: it.value })))}
                cols={[{ key: 'level', label: 'Level' }, { key: 'name', label: 'Branch' }, { key: 'value', label: 'Value', num: true }]}
                height={h}>
      <div style={{ display: 'flex', gap: 14, alignItems: 'stretch', overflowX: 'auto', paddingBottom: 4 }}>
        <div style={{ flex: '0 0 168px', display: 'flex', flexDirection: 'column', justifyContent: 'center',
                      background: SEQ_BLUE[1], borderRadius: 12, padding: '14px 16px' }}>
          <div style={{ ...font.micro, color: INK_2 }}>{root.label}</div>
          <div style={{ ...font.num, fontSize: 23.4, fontWeight: 800, color: INK }}>{fmt(total)}</div>
        </div>
        {levels.map((lv, li) => (
          <React.Fragment key={lv.label}>
            <div style={{ alignSelf: 'center', color: INK_3, fontSize: 16.2 }}>&rarr;</div>
            <div style={{ flex: '1 1 210px', minWidth: 190 }}>
              <div style={{ ...font.micro, color: INK_2, marginBottom: 6 }}>{lv.label}</div>
              <div style={{ display: 'grid', gap: 5 }}>
                {lv.items.slice(0, 6).map((it, i) => {
                  const share = n(it.value) / total;
                  const c = CAT[(li * 3 + i) % CAT.length];
                  return (
                    <div key={it.name} style={{ border: `1px solid ${LINE}`, borderRadius: 9, padding: '7px 10px' }}>
                      <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8, fontSize: 11.2 }}>
                        <span style={{ color: INK_2, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{it.name}</span>
                        <span style={{ ...font.num, fontWeight: 750, color: INK }}>{fmt(it.value)}</span>
                      </div>
                      <div style={{ height: 5, background: LINE, borderRadius: 3, marginTop: 5 }}>
                        <div style={{ width: `${Math.min(100, share * 100)}%`, height: '100%', background: c, borderRadius: 3 }} />
                      </div>
                    </div>
                  );
                })}
              </div>
            </div>
          </React.Fragment>
        ))}
      </div>
    </ChartFrame>
  );
}

// ---------------------------------------------------------------------
// 12. SLICERS AND CONTROLS.
// Filters belong ABOVE the charts they filter and must show their current
// state without being opened. A filter whose effect is invisible is how a
// reader ends up quoting a filtered number as a fleet total.
// ---------------------------------------------------------------------
export function ChipSlicer({ options, value, onChange, multi, label }) {
  const sel = multi ? (value || []) : [value].filter(Boolean);
  const toggle = (v) => {
    if (!multi) return onChange(value === v ? null : v);
    return onChange(sel.includes(v) ? sel.filter((x) => x !== v) : [...sel, v]);
  };
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
      {label && <span style={{ ...font.micro, color: INK_3 }}>{label}</span>}
      {(options || []).map((o) => {
        const v = typeof o === 'string' ? o : o.value;
        const lbl = typeof o === 'string' ? o : o.label;
        const c = (typeof o === 'object' && o.color) || CAT[0];
        const on = sel.includes(v);
        return (
          <button key={v} type="button" onClick={() => toggle(v)}
                  style={{ border: `1px solid ${on ? c : LINE}`, background: on ? c : '#FFF',
                           color: on ? '#FFF' : INK_2, borderRadius: 999, padding: '5px 13px',
                           fontSize: 11.7, fontWeight: 650, cursor: 'pointer' }}>
            {lbl}
          </button>
        );
      })}
      {sel.length > 0 && (
        <button type="button" onClick={() => onChange(multi ? [] : null)}
                style={{ border: 'none', background: 'transparent', color: INK_3, fontSize: 11.2,
                         cursor: 'pointer', textDecoration: 'underline' }}>
          clear
        </button>
      )}
    </div>
  );
}

export function RangeSlicer({ min, max, value, onChange, label, fmt = (v) => nfmt(v) }) {
  const v = value === null || value === undefined ? min : value;
  return (
    <label style={{ display: 'inline-flex', alignItems: 'center', gap: 10 }}>
      {label && <span style={{ ...font.micro, color: INK_3 }}>{label}</span>}
      <input type="range" min={min} max={max} value={v} onChange={(e) => onChange(Number(e.target.value))}
             style={{ accentColor: CAT[0], width: 150 }} />
      <span style={{ ...font.num, fontSize: 11.7, fontWeight: 700, color: INK, minWidth: 46 }}>{fmt(v)}</span>
    </label>
  );
}
