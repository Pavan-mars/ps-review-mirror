// ============================================================================
// PS2CascadeNetwork.jsx  ->  src/components/tabs/PS2CascadeNetwork.jsx
// Interactive cascade subsystem network (draggable, hover-able).
// Uses react-force-graph-2d if installed:   npm install react-force-graph-2d
// Degrades gracefully to a centrality table if the package is absent, so the
// dashboard never crashes when the dependency hasn't been added yet.
// Nodes = subsystems sized/coloured by betweenness; edges = Markov transitions.
// ============================================================================
import React, { useState, useEffect, useRef } from 'react';
import { apiPS2Network, apiPS2Markov } from '../../data/api';

const NAVY = '#1E3A5F', INK = '#5A6B7D', LINE = '#E1E9F1';
const card = { background:'#fff', border:`1px solid ${LINE}`, borderRadius:12, padding:'18px 20px', margin:'16px 0', boxShadow:'0 4px 14px rgba(30,58,95,.05)' };

function roleColor(btw) {
  if (btw > 0.3) return '#D15C4D';      // major hub
  if (btw > 0.05) return '#F4CE7A';     // relay
  return '#9DC3E6';                     // peripheral
}

export default function PS2CascadeNetwork({ city = 'CHI' }) {
  const [FG, setFG] = useState(null);
  const [mode, setMode] = useState('loading'); // loading | graph | table
  // 2026-07-26 -- live-only: apiPS2Network/apiPS2Markov now throw ApiError on
  // failure instead of falling back to mock; start empty and stay empty (no
  // fabricated numbers) until the live fetch below succeeds.
  const [net, setNet] = useState([]);
  const [mk, setMk] = useState([]);
  const wrapRef = useRef(null);
  const [width, setWidth] = useState(720);

  useEffect(() => {
    let live = true;
    // load data (live-only; throws on failure, net/mk simply stay at [])
    Promise.all([apiPS2Network(city), apiPS2Markov(city)]).then(([n, m]) => {
      if (!live) return; if (n && n.length) setNet(n); if (m && m.length) setMk(m);
    }).catch(() => {});
    // try to load the force-graph lib
    import(/* @vite-ignore */ 'react-force-graph-2d')
      .then((mod) => { if (live) { setFG(() => mod.default); setMode('graph'); } })
      .catch(() => { if (live) setMode('table'); });
    return () => { live = false; };
  }, [city]);

  useEffect(() => {
    if (!wrapRef.current) return;
    const ro = new ResizeObserver((e) => setWidth(e[0].contentRect.width));
    ro.observe(wrapRef.current);
    return () => ro.disconnect();
  }, []);

  const maxBtw = Math.max(0.01, ...net.map((n) => n.betweenness || 0));
  const graphData = {
    nodes: net.map((n) => ({ id: n.node_id, btw: n.betweenness, pr: n.pagerank, role: n.role,
      val: 3 + 22 * ((n.betweenness || 0) / maxBtw) })),
    links: mk.filter((e) => net.some((n) => n.node_id === e.from_sub) && net.some((n) => n.node_id === e.to_sub))
             .map((e) => ({ source: e.from_sub, target: e.to_sub, prob: e.prob })),
  };

  return (
    <div style={card} ref={wrapRef}>
      <h3 style={{ margin:'0 0 2px', fontSize:16, color:NAVY, fontWeight:700 }}>Cascade Subsystem Network</h3>
      <p style={{ margin:'0 0 12px', fontSize:12.5, color:INK }}>
        Node size/colour = betweenness (cascade routing importance); edges = Markov transitions.
        {mode === 'graph' ? ' Drag nodes; hover for detail.' : ''}
      </p>
      {mode === 'graph' && FG ? (
        <div style={{ height: 460, border:`1px solid ${LINE}`, borderRadius:8, overflow:'hidden' }}>
          <FG
            graphData={graphData}
            width={Math.max(320, width - 44)}
            height={456}
            backgroundColor="#FFFFFF"
            nodeRelSize={5}
            nodeVal={(n) => n.val}
            nodeLabel={(n) => `${n.id} — betweenness ${(n.btw ?? 0).toFixed(3)}, pagerank ${(n.pr ?? 0).toFixed(3)} (${n.role})`}
            nodeColor={(n) => roleColor(n.btw || 0)}
            linkColor={() => 'rgba(30,58,95,0.25)'}
            linkWidth={(l) => 0.5 + 4 * (l.prob || 0)}
            linkDirectionalArrowLength={4}
            linkDirectionalParticles={0}
            nodeCanvasObjectMode={() => 'after'}
            nodeCanvasObject={(n, ctx, scale) => {
              const label = n.id; ctx.font = `${11 / scale}px Segoe UI, sans-serif`;
              ctx.fillStyle = NAVY; ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
              ctx.fillText(label, n.x, n.y + 10 + n.val / scale);
            }}
          />
        </div>
      ) : (
        <div>
          {mode === 'loading' && <p style={{ fontSize:12.5, color:INK }}>Loading network…</p>}
          {mode === 'table' && (
            <p style={{ fontSize:12, color:'#B08A2E', margin:'0 0 8px' }}>
              Interactive graph needs <code>react-force-graph-2d</code> (run <code>npm install react-force-graph-2d</code>).
              Showing centrality ranking meanwhile.
            </p>)}
          <table style={{ width:'100%', borderCollapse:'collapse' }}>
            <thead><tr>
              {['Subsystem','Role','Betweenness','PageRank','In','Out'].map((c) => (
                <th key={c} style={{ textAlign:'left', padding:'7px 9px', background:'#EEF3F9', color:NAVY, fontWeight:600, fontSize:12 }}>{c}</th>))}
            </tr></thead>
            <tbody>{net.map((n) => (
              <tr key={n.node_id}>
                <td style={{ padding:'6px 9px', borderTop:`1px solid #EEF2F7`, fontFamily:'monospace', fontWeight:700, color:NAVY, fontSize:12 }}>{n.node_id}</td>
                <td style={{ padding:'6px 9px', borderTop:`1px solid #EEF2F7`, fontSize:12 }}>
                  <span style={{ padding:'2px 8px', borderRadius:12, fontSize:11, fontWeight:600, color:NAVY, background:roleColor(n.betweenness || 0) }}>{n.role}</span></td>
                <td style={{ padding:'6px 9px', borderTop:`1px solid #EEF2F7`, fontSize:12 }}>{(n.betweenness ?? 0).toFixed(3)}</td>
                <td style={{ padding:'6px 9px', borderTop:`1px solid #EEF2F7`, fontSize:12 }}>{(n.pagerank ?? 0).toFixed(3)}</td>
                <td style={{ padding:'6px 9px', borderTop:`1px solid #EEF2F7`, fontSize:12 }}>{n.in_degree}</td>
                <td style={{ padding:'6px 9px', borderTop:`1px solid #EEF2F7`, fontSize:12 }}>{n.out_degree}</td>
              </tr>))}</tbody>
          </table>
        </div>
      )}
    </div>
  );
}
