// =====================================================================
// v2/GlobalSearch.jsx -- one box that finds anything on the estate.
//
// WHAT IT INDEXES, AND WHERE EACH MAPPING COMES FROM
//   device      /ps1/predictions        device_id -> type, depot, risk
//   device      /ps2/devices            device_id -> name, facility, operator
//   component   /fleet/device-serials   serial_id -> device, part, age
//   depot       /ps1/station-summary    facility_id -> name, operator, counts
//   bus         /fleet/device-bus       device_id -> bus_id   (optional route)
//   device type static                  GATE/TVM/VALIDATOR/READER
//
// EVERY SOURCE IS OPTIONAL. A route that 404s or errors drops that one
// entity kind out of the index and the rest still works -- the search box
// is not allowed to take the page down with it. This is deliberate: the
// bus route is new and may not be deployed yet.
//
// Ranking is prefix-first, then substring, then fuzzy-subsequence, and
// exact identifier matches always win. An engineer typing a full device id
// must get that device as the first row, never a fuzzy neighbour.
// =====================================================================
import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Search, X } from 'lucide-react';
import { CARD, INK, INK_2, INK_3, LINE, deviceColor, deviceShort, font, nfmt, radius, shadow } from './V4theme';

const KIND = {
  device: { label: 'Device', order: 0 },
  bus: { label: 'Bus', order: 1 },
  component: { label: 'Component serial', order: 2 },
  depot: { label: 'Depot / station', order: 3 },
  type: { label: 'Device type', order: 4 },
};

async function safeGet(base, path) {
  try {
    const r = await fetch(`${base}${path}`);
    if (!r.ok) return [];
    const j = await r.json();
    return Array.isArray(j) ? j : [];
  } catch {
    return [];
  }
}

// Subsequence match: "bmv38" finds "BMV03868". Cheap, and it is what
// people actually type when they half-remember an identifier.
function subseq(hay, needle) {
  let i = 0;
  for (let j = 0; j < hay.length && i < needle.length; j += 1) if (hay[j] === needle[i]) i += 1;
  return i === needle.length;
}

function score(entry, q) {
  const id = entry.id.toLowerCase();
  const label = entry.label.toLowerCase();
  if (id === q) return 0;
  if (id.startsWith(q)) return 1;
  if (label.startsWith(q)) return 2;
  if (id.includes(q)) return 3;
  if (label.includes(q)) return 4;
  if ((entry.extra || '').toLowerCase().includes(q)) return 5;
  if (subseq(id, q)) return 6;
  return 99;
}

// `enabled` exists so the caller can hold this back until the page's own
// data has landed. Building the index costs 5 more calls, one of which is
// the 5,000-row device-serials list; racing it against the fleet fetch is
// what made both of them slow on 01-Aug.
export function useSearchIndex(base, city = 'CHI', enabled = true) {
  const [index, setIndex] = useState([]);
  const [ready, setReady] = useState(false);

  useEffect(() => {
    let alive = true;
    if (!enabled) return undefined;
    if (!base) { setReady(true); return undefined; }
    (async () => {
      const [preds, ps2dev, serials, stations, buses] = await Promise.all([
        safeGet(base, `/ps1/predictions?city=${city}`),
        safeGet(base, `/ps2/devices?city=${city}`),
        safeGet(base, `/fleet/device-serials?city=${city}`),
        safeGet(base, `/ps1/station-summary?city=${city}`),
        safeGet(base, `/fleet/device-bus?city=${city}`),
      ]);
      if (!alive) return;

      const byDevice = new Map();
      const add = (id, patch) => {
        if (!id) return;
        byDevice.set(id, { ...(byDevice.get(id) || {}), ...patch });
      };
      preds.forEach((r) => add(r.device_id, { type: r.device_category, depot: r.facility_id, risk: r.failure_probability }));
      ps2dev.forEach((r) => add(r.device_id, { type: r.category || undefined, name: r.device_name, depot: r.facility, operator: r.operator }));
      buses.forEach((r) => add(r.device_id, { bus: r.bus_id, depot: r.facility_name || undefined, type: r.device_category || undefined }));

      const out = [];
      byDevice.forEach((v, id) => {
        out.push({
          kind: 'device', id, label: v.name && v.name !== id ? `${id} -- ${v.name}` : id,
          extra: [deviceShort(v.type), v.depot, v.bus ? `bus ${v.bus}` : null, v.operator].filter(Boolean).join(' - '),
          scope: { device_id: id, device_type: v.type },
          type: v.type,
        });
      });

      // Buses as first-class results: "which devices are on bus 1289" is a
      // question the depot asks constantly.
      const busMap = new Map();
      buses.forEach((r) => {
        if (!r.bus_id) return;
        const k = String(r.bus_id);
        busMap.set(k, (busMap.get(k) || 0) + 1);
      });
      busMap.forEach((n, bus) => {
        out.push({ kind: 'bus', id: bus, label: `Bus ${bus}`, extra: `${n} validator${n === 1 ? '' : 's'}`, scope: { bus_id: bus } });
      });

      // Components: one entry per distinct serial, carrying its device so a
      // hit can jump straight to the device that holds the part.
      const seenSerial = new Set();
      serials.forEach((r) => {
        const s = r.serial_id;
        if (!s || seenSerial.has(s)) return;
        seenSerial.add(s);
        out.push({
          kind: 'component', id: String(s),
          label: String(s),
          extra: [r.component_description, r.device_id, deviceShort(r.mars_device_category)].filter(Boolean).join(' - '),
          scope: { serial_id: String(s), device_id: r.device_id },
          type: r.mars_device_category,
        });
      });

      stations.forEach((r) => {
        out.push({
          kind: 'depot', id: String(r.facility_id),
          label: r.facility_name || `Facility ${r.facility_id}`,
          extra: [r.operator_name, `${nfmt(r.total_devices)} devices`].filter(Boolean).join(' - '),
          scope: { facility_id: String(r.facility_id), facility_name: r.facility_name },
        });
      });

      ['GATE', 'TVM', 'VALIDATOR', 'READER'].forEach((t) => {
        out.push({ kind: 'type', id: t, label: deviceShort(t), extra: t, scope: { device_type: t }, type: t });
      });

      setIndex(out);
      setReady(true);
    })();
    return () => { alive = false; };
  }, [base, city, enabled]);

  return { index, ready };
}

export default function GlobalSearch({ index, ready, onPick, onActivate, placeholder = 'Search a device, bus, component serial or depot' }) {
  const [q, setQ] = useState('');
  const [open, setOpen] = useState(false);
  const [cursor, setCursor] = useState(0);
  const boxRef = useRef(null);
  const inputRef = useRef(null);

  // Ctrl/Cmd-K is the shortcut people already have in their fingers.
  useEffect(() => {
    const h = (e) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') {
        e.preventDefault();
        if (inputRef.current) inputRef.current.focus();
        setOpen(true);
        if (onActivate) onActivate();
      }
      if (e.key === 'Escape') setOpen(false);
    };
    window.addEventListener('keydown', h);
    return () => window.removeEventListener('keydown', h);
  }, [onActivate]);

  useEffect(() => {
    const h = (e) => { if (boxRef.current && !boxRef.current.contains(e.target)) setOpen(false); };
    document.addEventListener('mousedown', h);
    return () => document.removeEventListener('mousedown', h);
  }, []);

  const results = useMemo(() => {
    const needle = q.trim().toLowerCase();
    if (needle.length < 2) return [];
    const hits = [];
    for (let i = 0; i < index.length; i += 1) {
      const s = score(index[i], needle);
      if (s < 99) hits.push({ e: index[i], s });
      // Hard cap the scan so a two-character query over 10k entries cannot
      // block the input thread.
      if (hits.length > 600) break;
    }
    hits.sort((a, b) => a.s - b.s || KIND[a.e.kind].order - KIND[b.e.kind].order || a.e.id.localeCompare(b.e.id));
    return hits.slice(0, 24).map((h) => h.e);
  }, [q, index]);

  useEffect(() => setCursor(0), [q]);

  const pick = useCallback((entry) => {
    if (!entry) return;
    setOpen(false);
    setQ('');
    if (onPick) onPick(entry);
  }, [onPick]);

  const onKey = (e) => {
    if (!results.length) return;
    if (e.key === 'ArrowDown') { e.preventDefault(); setCursor((c) => Math.min(results.length - 1, c + 1)); }
    if (e.key === 'ArrowUp') { e.preventDefault(); setCursor((c) => Math.max(0, c - 1)); }
    if (e.key === 'Enter') { e.preventDefault(); pick(results[cursor]); }
  };

  let lastKind = null;

  return (
    <div ref={boxRef} style={{ position: 'relative', flex: 1, minWidth: 260, maxWidth: 560 }}>
      <Search size={16} color={INK_3} style={{ position: 'absolute', left: 12, top: 11 }} />
      <input
        ref={inputRef}
        value={q}
        onChange={(e) => { setQ(e.target.value); setOpen(true); if (onActivate) onActivate(); }}
        onFocus={() => { setOpen(true); if (onActivate) onActivate(); }}
        onKeyDown={onKey}
        placeholder={placeholder}
        style={{
          width: '100%', boxSizing: 'border-box', border: `1px solid ${LINE}`, borderRadius: 11,
          padding: '9px 68px 9px 36px', fontSize: 13.5, color: INK, background: CARD, outline: 'none',
          boxShadow: open ? shadow : 'none',
        }}
      />
      <span style={{ position: 'absolute', right: q ? 30 : 11, top: 10, fontSize: 11, color: INK_3, border: `1px solid ${LINE}`, borderRadius: 5, padding: '1px 5px', fontWeight: 600 }}>
        Ctrl K
      </span>
      {q && (
        <button type="button" onClick={() => setQ('')} aria-label="Clear"
                style={{ position: 'absolute', right: 9, top: 9, border: 'none', background: 'none', cursor: 'pointer', color: INK_3, padding: 2 }}>
          <X size={14} />
        </button>
      )}

      {open && q.trim().length >= 2 && (
        <div style={{
          position: 'absolute', top: 'calc(100% + 6px)', left: 0, right: 0, zIndex: 60,
          background: CARD, border: `1px solid ${LINE}`, borderRadius: radius, boxShadow: '0 18px 44px rgba(15,23,42,.16)',
          maxHeight: 420, overflowY: 'auto', padding: 6,
        }}>
          {!results.length ? (
            <div style={{ padding: '14px 12px', fontSize: 12.6, color: INK_3 }}>
              Nothing matches "{q}". Try a device id, a bus number, a component serial, or a depot name.
            </div>
          ) : (
            results.map((r, i) => {
              const header = r.kind !== lastKind ? KIND[r.kind].label : null;
              lastKind = r.kind;
              return (
                <div key={`${r.kind}-${r.id}`}>
                  {header && <div style={{ ...font.micro, padding: '8px 10px 4px' }}>{header}</div>}
                  <button
                    type="button"
                    onMouseEnter={() => setCursor(i)}
                    onClick={() => pick(r)}
                    style={{
                      display: 'flex', alignItems: 'center', gap: 10, width: '100%', textAlign: 'left',
                      border: 'none', borderRadius: 9, padding: '8px 10px', cursor: 'pointer',
                      background: i === cursor ? '#F1F5F9' : 'transparent',
                    }}
                  >
                    <span style={{ width: 8, height: 8, borderRadius: 4, flex: '0 0 auto', background: r.type ? deviceColor(r.type) : INK_3 }} />
                    <span style={{ minWidth: 0, flex: 1 }}>
                      <span style={{ display: 'block', fontSize: 13.1, fontWeight: 600, color: INK, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                        {r.label}
                      </span>
                      {r.extra && (
                        <span style={{ display: 'block', fontSize: 11.2, color: INK_2, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                          {r.extra}
                        </span>
                      )}
                    </span>
                  </button>
                </div>
              );
            })
          )}
        </div>
      )}
    </div>
  );
}

// FONTS_SCALED 04-Aug-2026
