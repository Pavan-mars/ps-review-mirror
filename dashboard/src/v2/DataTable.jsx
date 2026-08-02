// =====================================================================
// v2/DataTable.jsx -- one table for every grain-level list in v2.
//
// WHY VIRTUALIZED AND NOT JUST PAGINATED
// /fleet/device-serials returns 5,000 rows and /ps1/predictions 600. React
// will happily mount 5,000 <tr> elements and then drop frames on every
// keystroke in the search box. This renders only the rows inside the
// viewport plus an overscan margin -- typically about 20 nodes regardless
// of row count -- and pages on top of that, so both the DOM and the
// scrollbar stay honest.
//
// Fixed ROW_H is what makes windowing possible without measuring. Every
// cell here is single-line and ellipsised for that reason; a wrapping cell
// would break the offset maths and misalign the whole body.
// =====================================================================
import React, { useEffect, useMemo, useRef, useState } from 'react';
import { ArrowDown, ArrowUp, ChevronLeft, ChevronRight, Download } from 'lucide-react';
import { CARD, INK, INK_2, INK_3, LINE, font, nfmt } from './theme';
import { Empty, TextField } from './Kit';

const ROW_H = 38;
const HEAD_H = 38;
const OVERSCAN = 8;

export default function DataTable({
  rows,
  columns,              // [{key,label,num?,d?,width?,render?,sortable?}]
  height = 420,
  pageSize = 200,
  searchable = true,
  searchKeys,           // which columns the quick filter looks at; defaults to all non-numeric
  onRowClick,
  rowKey = (r, i) => i,
  emptyText = 'No rows for the current selection.',
  toolbarRight,
  exportName,
}) {
  const [q, setQ] = useState('');
  const [sort, setSort] = useState({ key: null, dir: 'desc' });
  const [page, setPage] = useState(0);
  const [scrollTop, setScrollTop] = useState(0);
  const boxRef = useRef(null);

  const keys = useMemo(
    () => searchKeys || columns.filter((c) => !c.num).map((c) => c.key),
    [searchKeys, columns]
  );

  const filtered = useMemo(() => {
    const needle = q.trim().toLowerCase();
    if (!needle) return rows || [];
    return (rows || []).filter((r) => keys.some((k) => String(r[k] ?? '').toLowerCase().includes(needle)));
  }, [rows, q, keys]);

  const sorted = useMemo(() => {
    if (!sort.key) return filtered;
    const col = columns.find((c) => c.key === sort.key);
    const mul = sort.dir === 'asc' ? 1 : -1;
    return [...filtered].sort((a, b) => {
      const av = a[sort.key], bv = b[sort.key];
      if (av === null || av === undefined) return 1;
      if (bv === null || bv === undefined) return -1;
      if (col && col.num) return (Number(av) - Number(bv)) * mul;
      return String(av).localeCompare(String(bv)) * mul;
    });
  }, [filtered, sort, columns]);

  // Any change to the result set must return to page 1, or the user sits
  // on an empty page 7 of a 2-page list and reads it as "no data".
  useEffect(() => { setPage(0); if (boxRef.current) boxRef.current.scrollTop = 0; }, [q, sort.key, sort.dir, rows]);

  const pages = Math.max(1, Math.ceil(sorted.length / pageSize));
  const pageRows = useMemo(() => sorted.slice(page * pageSize, page * pageSize + pageSize), [sorted, page, pageSize]);

  const bodyH = height - HEAD_H;
  const first = Math.max(0, Math.floor(scrollTop / ROW_H) - OVERSCAN);
  const visibleCount = Math.ceil(bodyH / ROW_H) + OVERSCAN * 2;
  const window_ = pageRows.slice(first, first + visibleCount);

  const toggleSort = (c) => {
    if (c.sortable === false) return;
    setSort((s) => (s.key === c.key ? { key: c.key, dir: s.dir === 'desc' ? 'asc' : 'desc' } : { key: c.key, dir: c.num ? 'desc' : 'asc' }));
  };

  const exportCsv = () => {
    const head = columns.map((c) => `"${c.label}"`).join(',');
    const body = sorted.map((r) => columns.map((c) => `"${String(r[c.key] ?? '').replace(/"/g, '""')}"`).join(',')).join('\n');
    const blob = new Blob([`${head}\n${body}`], { type: 'text/csv;charset=utf-8;' });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = `${exportName || 'export'}.csv`;
    a.click();
    URL.revokeObjectURL(a.href);
  };

  return (
    <div>
      {(searchable || toolbarRight || exportName) && (
        <div style={{ display: 'flex', gap: 8, alignItems: 'center', marginBottom: 10, flexWrap: 'wrap' }}>
          {searchable && <TextField value={q} onChange={setQ} placeholder="Filter these rows" />}
          <span style={{ ...font.note, fontSize: 12, color: INK_3, whiteSpace: 'nowrap' }}>
            {nfmt(sorted.length)}{rows && sorted.length !== rows.length ? ` of ${nfmt(rows.length)}` : ''} rows
          </span>
          {toolbarRight}
          {exportName && (
            <button type="button" onClick={exportCsv}
                    style={{ marginLeft: 'auto', display: 'inline-flex', alignItems: 'center', gap: 5, border: `1px solid ${LINE}`, background: CARD, color: INK_2, borderRadius: 9, padding: '6px 11px', fontSize: 12, fontWeight: 600, cursor: 'pointer' }}>
              <Download size={13} /> CSV
            </button>
          )}
        </div>
      )}

      {!sorted.length ? (
        <Empty height={140}>{emptyText}</Empty>
      ) : (
        <div style={{ border: `1px solid ${LINE}`, borderRadius: 12, overflow: 'hidden', background: CARD }}>
          {/* header is outside the scroll box so it cannot drift from the body */}
          <div style={{ display: 'flex', height: HEAD_H, alignItems: 'center', background: '#F8FAFC', borderBottom: `1px solid ${LINE}` }}>
            {columns.map((c) => (
              <div key={c.key}
                   onClick={() => toggleSort(c)}
                   style={{
                     flex: c.width ? `0 0 ${c.width}px` : 1, minWidth: 0, padding: '0 12px',
                     textAlign: c.num ? 'right' : 'left', ...font.micro, fontSize: 11,
                     cursor: c.sortable === false ? 'default' : 'pointer', userSelect: 'none',
                     display: 'flex', alignItems: 'center', gap: 4,
                     justifyContent: c.num ? 'flex-end' : 'flex-start',
                   }}>
                {c.label}
                {sort.key === c.key && (sort.dir === 'asc' ? <ArrowUp size={11} /> : <ArrowDown size={11} />)}
              </div>
            ))}
          </div>

          <div ref={boxRef} onScroll={(e) => setScrollTop(e.currentTarget.scrollTop)}
               style={{ height: bodyH, overflowY: 'auto', position: 'relative' }}>
            <div style={{ height: pageRows.length * ROW_H, position: 'relative' }}>
              <div style={{ position: 'absolute', top: first * ROW_H, left: 0, right: 0 }}>
                {window_.map((r, i) => {
                  const idx = first + i;
                  return (
                    <div key={rowKey(r, idx)}
                         onClick={onRowClick ? () => onRowClick(r) : undefined}
                         style={{
                           display: 'flex', alignItems: 'center', height: ROW_H,
                           borderTop: idx === 0 ? 'none' : `1px solid #F1F5F9`,
                           cursor: onRowClick ? 'pointer' : 'default',
                           background: idx % 2 ? '#FCFDFE' : CARD,
                         }}>
                      {columns.map((c) => (
                        <div key={c.key}
                             style={{
                               flex: c.width ? `0 0 ${c.width}px` : 1, minWidth: 0, padding: '0 12px',
                               fontSize: 12.5, color: INK_2, textAlign: c.num ? 'right' : 'left',
                               whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis',
                               ...(c.num ? font.num : {}),
                             }}
                             title={c.render ? undefined : String(r[c.key] ?? '')}>
                          {c.render ? c.render(r) : c.num ? nfmt(r[c.key], c.d || 0) : (r[c.key] ?? '--')}
                        </div>
                      ))}
                    </div>
                  );
                })}
              </div>
            </div>
          </div>

          {pages > 1 && (
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '8px 12px', borderTop: `1px solid ${LINE}`, background: '#FCFDFE' }}>
              <span style={{ fontSize: 12, color: INK_3 }}>
                Page {page + 1} of {pages}
              </span>
              <div style={{ display: 'flex', gap: 6 }}>
                <PageBtn disabled={page === 0} onClick={() => { setPage((p) => p - 1); if (boxRef.current) boxRef.current.scrollTop = 0; }}>
                  <ChevronLeft size={14} /> Prev
                </PageBtn>
                <PageBtn disabled={page >= pages - 1} onClick={() => { setPage((p) => p + 1); if (boxRef.current) boxRef.current.scrollTop = 0; }}>
                  Next <ChevronRight size={14} />
                </PageBtn>
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function PageBtn({ disabled, onClick, children }) {
  return (
    <button type="button" disabled={disabled} onClick={onClick}
            style={{
              display: 'inline-flex', alignItems: 'center', gap: 3, border: `1px solid ${LINE}`,
              background: CARD, color: disabled ? INK_3 : INK, borderRadius: 8, padding: '4px 10px',
              fontSize: 12, fontWeight: 600, cursor: disabled ? 'default' : 'pointer', opacity: disabled ? 0.55 : 1,
            }}>
      {children}
    </button>
  );
}
