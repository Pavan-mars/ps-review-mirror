// =====================================================================
// v4/V4DeviceTable.jsx -- one table for a device and everything on it.
//                                                          04-Aug-2026
//
// WHY THIS EXISTS.
// The device tabs were bare tables, and components sat on a separate tab.
// That split forces the reader to hold a device id in their head, change
// tab, and search for it again -- to answer a question that is about ONE
// device. So device grain and component grain are joined into a single
// table here: one row per device, with its parts rolled up into the same
// row, and the worst part named.
//
// It is still HONEST about grain. The row is a device. Component figures
// are labelled as rollups ("parts", "worst part"), never as if the row
// were a part. A device with three critical parts shows 3 and the worst
// one -- it does not become three rows.
//
// FIXED HEIGHT, INTERNAL SCROLL. The table occupies the same footprint as
// a chart, so a screen keeps its rhythm whether a panel holds 20 rows or
// 4,000. DataTable already virtualises and paginates; this only guarantees
// the box.
//
// EVERY ROW OPENS DEVICE 360. That is the point of the screen: the table
// is a way in, not a destination.
// =====================================================================
import React, { useMemo, useState } from 'react';
import DataTable from './V4DataTable';
import { Card } from './V4Kit';
import { ChipSlicer } from './V4ChartsPlus';
// Start the Device 360 call on hover. The endpoint takes ~6s server-side,
// so the second between pointing at a row and clicking it is worth having.
import { primeDevice360 } from './V4Device360Popup';
import { CAT, INK, INK_2, INK_3, LINE, STATUS, deviceShort, font, nfmt, pct } from './V4theme';

const n = (v) => (v === null || v === undefined || Number.isNaN(Number(v)) ? 0 : Number(v));
const clean = (v) => String(v ?? '').replace(/\.0$/, '');

// ---------------------------------------------------------------------
// Join device rows to component rows. Left join on device_id: a device
// with no parts recorded still appears, because a device missing from a
// work list is worse than one with a blank column.
// ---------------------------------------------------------------------
export function joinDeviceComponents(devices, components) {
  const byDev = new Map();
  (components || []).forEach((c) => {
    const id = c.device_id;
    if (!id) return;
    if (!byDev.has(id)) byDev.set(id, []);
    byDev.get(id).push(c);
  });
  return (devices || []).map((d) => {
    const parts = byDev.get(d.device_id) || [];
    // "Worst" is the part closest to the end of its life, not the oldest.
    // Age is not urgency: a long-lived part near its limit outranks a
    // younger one that is nowhere near.
    const worst = parts.slice().sort(
      (a, b) => (n(a.expected_component_rul_days) || 1e9) - (n(b.expected_component_rul_days) || 1e9)
    )[0];
    return {
      ...d,
      n_parts: parts.length,
      n_parts_urgent: parts.filter((p) => p.act_now || p.is_overdue).length,
      worst_part: worst ? String(worst.component_type_name || '').replace(/_/g, ' ') : null,
      worst_part_serial: worst ? worst.component_serial_nbr : null,
      worst_part_life: worst ? n(worst.expected_component_rul_days) : null,
      worst_part_age: worst ? n(worst.component_age_days) : null,
    };
  });
}

// A drop-in Analyse column for tables that are NOT the joined DeviceTable.
// PS4 and PS5 keep their own device tables -- PS4 carries a week column,
// PS5 its own filters -- so rather than replace working screens, they get
// the same visible entry point into Device 360.
export function analyseColumn(onAnalyse, label = 'Analyse', city = 'CHI') {
  return {
    key: '__analyse', label: '', width: 116, sortable: false,
    render: (r) => (
      <button
        type="button"
        onMouseEnter={() => primeDevice360(city, r.device_id)}
        onFocus={() => primeDevice360(city, r.device_id)}
        onClick={(e) => { e.stopPropagation(); onAnalyse && onAnalyse(r.device_id); }}
        style={{ border: `1px solid ${CAT[0]}`, background: '#FFF', color: CAT[0], borderRadius: 8,
                 padding: '5px 12px', fontSize: 11.2, fontWeight: 700, cursor: 'pointer',
                 whiteSpace: 'nowrap' }}
      >
        {label}
      </button>
    ),
  };
}

function Pill({ tone, children }) {
  const s = STATUS[tone] || STATUS.neutral;
  return (
    <span style={{ background: s.bg, color: s.fill, border: `1px solid ${s.fill}22`,
                   borderRadius: 999, padding: '2px 10px', fontSize: 11, fontWeight: 700,
                   whiteSpace: 'nowrap' }}>
      {children}
    </span>
  );
}

function Select({ label, value, onChange, options }) {
  return (
    <label style={{ display: 'inline-flex', alignItems: 'center', gap: 7 }}>
      <span style={{ ...font.micro, color: INK_3 }}>{label}</span>
      <select
        value={value || ''}
        onChange={(e) => onChange(e.target.value || null)}
        style={{ border: `1px solid ${LINE}`, borderRadius: 9, padding: '6px 10px',
                 fontSize: 11.7, fontWeight: 600, color: INK, background: '#FFF', minWidth: 120 }}
      >
        <option value="">All</option>
        {options.map((o) => <option key={o} value={o}>{o}</option>)}
      </select>
    </label>
  );
}

// ---------------------------------------------------------------------
// The table itself.
//
// `rows`      -- device rows, ideally already joined via joinDeviceComponents
// `onAnalyse` -- called with a device id; wire this to Device 360
// `extraCols` -- appended after the standard set, per screen
// ---------------------------------------------------------------------
export default function DeviceTable({
  rows,
  onAnalyse,
  city = 'CHI',
  height = 460,
  title,
  sub,
  extraCols = [],
  riskKey = 'failure_probability',
  tierKey = 'ps1_risk_tier',
  exportName = 'devices',
}) {
  const [fleet, setFleet] = useState(null);
  const [depot, setDepot] = useState(null);
  const [tier, setTier] = useState([]);
  const [partsOnly, setPartsOnly] = useState(false);

  const fleets = useMemo(
    () => [...new Set((rows || []).map((r) => r.device_type || r.device_category).filter(Boolean))].sort(),
    [rows]
  );
  const depots = useMemo(
    () => [...new Set((rows || []).map((r) => clean(r.facility_id)).filter(Boolean))]
      .sort((a, b) => (Number(a) || 1e9) - (Number(b) || 1e9)),
    [rows]
  );

  const view = useMemo(() => (rows || []).filter((r) => {
    const f = r.device_type || r.device_category;
    if (fleet && String(f) !== fleet) return false;
    if (depot && clean(r.facility_id) !== depot) return false;
    if (tier.length && !tier.includes(String(r[tierKey] || '').toUpperCase())) return false;
    if (partsOnly && !n(r.n_parts_urgent)) return false;
    return true;
  }), [rows, fleet, depot, tier, partsOnly, tierKey]);

  const cols = [
    {
      key: 'device_id', label: 'Device', width: 132,
      render: (r) => (
        <span style={{ fontFamily: 'ui-monospace,monospace', fontWeight: 700, color: INK }}>{r.device_id}</span>
      ),
    },
    { key: 'device_type', label: 'Fleet', width: 116,
      render: (r) => deviceShort(r.device_type || r.device_category) },
    { key: 'facility_id', label: 'Depot', width: 96,
      render: (r) => r.station_name || r.facility_name || `Depot ${clean(r.facility_id)}` },
    {
      key: riskKey, label: 'Risk', num: true, width: 88,
      render: (r) => {
        const v = n(r[riskKey]);
        const c = v >= 0.9 ? STATUS.critical.fill : v >= 0.6 ? STATUS.serious.fill : INK;
        return <span style={{ color: c, fontWeight: 750 }}>{pct(v, 1)}</span>;
      },
    },
    {
      key: tierKey, label: 'Status', width: 108,
      render: (r) => {
        const t = String(r[tierKey] || r.risk_band || '').toUpperCase();
        const tone = t === 'CRITICAL' ? 'critical' : t === 'HIGH' ? 'serious'
          : t === 'MEDIUM' ? 'warning' : t === 'LOW' ? 'good' : 'neutral';
        return t ? <Pill tone={tone}>{t[0] + t.slice(1).toLowerCase()}</Pill> : null;
      },
    },
    // --- component rollup, clearly labelled as a rollup ---
    { key: 'n_parts', label: 'Parts', num: true, width: 78,
      render: (r) => (r.n_parts ? nfmt(r.n_parts) : <span style={{ color: INK_3 }}>--</span>) },
    {
      key: 'worst_part', label: 'Worst part', width: 152,
      render: (r) => (r.worst_part
        ? <span title={r.worst_part_serial || ''} style={{ color: INK }}>{r.worst_part}</span>
        : <span style={{ color: INK_3 }}>none recorded</span>),
    },
    {
      key: 'worst_part_life', label: 'Days to next OOS', num: true, width: 140,
      render: (r) => {
        if (r.worst_part_life === null || r.worst_part_life === undefined) return <span style={{ color: INK_3 }}>--</span>;
        const v = Math.round(r.worst_part_life);
        return <span style={{ fontWeight: 750, color: v <= 30 ? STATUS.critical.fill : INK }}>{nfmt(v)}</span>;
      },
    },
    ...extraCols,
    {
      key: '__act', label: '', width: 116, sortable: false,
      render: (r) => (
        <button
          type="button"
          onMouseEnter={() => primeDevice360(city, r.device_id)}
          onFocus={() => primeDevice360(city, r.device_id)}
          onClick={(e) => { e.stopPropagation(); onAnalyse && onAnalyse(r.device_id); }}
          style={{ border: `1px solid ${CAT[0]}`, background: '#FFF', color: CAT[0], borderRadius: 8,
                   padding: '5px 12px', fontSize: 11.2, fontWeight: 700, cursor: 'pointer',
                   whiteSpace: 'nowrap' }}
        >
          Analyse
        </button>
      ),
    },
  ];

  const urgent = view.filter((r) => n(r.n_parts_urgent)).length;

  return (
    <Card pad="16px 18px">
      {(title || sub) && (
        <div style={{ marginBottom: 12 }}>
          {title && <div style={{ ...font.h3, margin: 0 }}>{title}</div>}
          {sub && <div style={{ ...font.note, marginTop: 2 }}>{sub}</div>}
        </div>
      )}

      {/* FILTERS ABOVE THE TABLE, STATE VISIBLE.
          A filter whose effect is invisible is how a reader ends up quoting
          a filtered count as a fleet total -- so the row count restates
          what is being shown, every time. */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 14, flexWrap: 'wrap',
                    paddingBottom: 12, marginBottom: 12, borderBottom: `1px solid ${LINE}` }}>
        {fleets.length > 1 && <Select label="Fleet" value={fleet} onChange={setFleet} options={fleets} />}
        {depots.length > 1 && <Select label="Depot" value={depot} onChange={setDepot} options={depots} />}
        <ChipSlicer
          label="Urgency" multi value={tier} onChange={setTier}
          options={[
            { value: 'CRITICAL', label: 'Critical', color: STATUS.critical.fill },
            { value: 'HIGH', label: 'High', color: STATUS.serious.fill },
            { value: 'MEDIUM', label: 'Medium', color: STATUS.warning.fill },
            { value: 'LOW', label: 'Low', color: STATUS.good.fill },
          ]}
        />
        <button
          type="button" onClick={() => setPartsOnly((v) => !v)}
          style={{ border: `1px solid ${partsOnly ? CAT[2] : LINE}`, background: partsOnly ? CAT[2] : '#FFF',
                   color: partsOnly ? '#FFF' : INK_2, borderRadius: 999, padding: '5px 13px',
                   fontSize: 11.7, fontWeight: 650, cursor: 'pointer' }}
        >
          Has an urgent part
        </button>
        <span style={{ marginLeft: 'auto', ...font.micro, color: INK_2 }}>
          {nfmt(view.length)} of {nfmt((rows || []).length)} devices
          {urgent ? ` · ${nfmt(urgent)} with an urgent part` : ''}
        </span>
      </div>

      {/* Fixed height, internal scroll -- the same footprint a chart takes. */}
      <div style={{ height, overflow: 'hidden', borderRadius: 10, border: `1px solid ${LINE}` }}>
        <DataTable
          rows={view}
          columns={cols}
          height={height}
          pageSize={100}
          searchable
          searchKeys={['device_id', 'device_type', 'device_category', 'facility_id',
                       'station_name', 'worst_part', 'worst_part_serial']}
          onRowClick={(r) => onAnalyse && onAnalyse(r.device_id)}
          exportName={exportName}
          emptyText="No devices match the current filters."
        />
      </div>

      <div style={{ ...font.note, fontSize: 11.2, marginTop: 9 }}>
        One row per device. Part figures are a rollup of everything fitted to that
        device -- a device with three urgent parts is one row, not three. Search
        covers device id, depot, part name and serial. Click any row, or Analyse,
        to open the full device record.
      </div>
    </Card>
  );
}
