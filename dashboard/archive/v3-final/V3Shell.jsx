// =====================================================================
// v3/V3Shell.jsx -- the V3 frame.                          04-Aug-2026
// PS1 is its own screen because it is the most built-out. PS2 to PS5 share
// one screen driven by a spec, because they share a skeleton: headline
// tiles, a device-grain panel, a component-grain panel.
// =====================================================================
import React, { useEffect, useState } from 'react';
import { getObj } from '../v2/v2api';
import V3PS1 from './V3PS1';
import DeviceBrief from './DeviceBrief';
import { V3Problem, P, Loading } from './V3Rest';
import { CARD, INK, INK_2, LINE } from '../v2/theme';

const TABS = [
  { key: 'ps1', label: 'Failure Prediction' },
  { key: 'ps2', label: 'Patterns & Cascades' },
  { key: 'ps3', label: 'Root Cause' },
  { key: 'ps4', label: 'Anomalies' },
  { key: 'ps5', label: 'Remaining Life' },
];

export default function V3Shell({ city = 'CHI' }) {
  const [tab, setTab] = useState('ps1');
  const [device, setDevice] = useState(null);
  const [brief, setBrief] = useState(null);

  useEffect(() => {
    if (!device) return undefined;
    let ok = true;
    setBrief({ loading: true });
    getObj('/ps1/device-360', { city, device_id: device })
      .then((d) => { if (ok) setBrief(d || {}); })
      .catch(() => { if (ok) setBrief(null); });
    return () => { ok = false; };
  }, [device, city]);

  return (
    <div style={{ background: P.page, minHeight: '100%' }}>
      <style>{'@keyframes v3spin{to{transform:rotate(360deg)}}'}</style>

      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', padding: '16px 22px 0' }}>
        {TABS.map((t) => (
          <button key={t.key} type="button" onClick={() => setTab(t.key)}
                  style={{ border: `1px solid ${tab === t.key ? P.indigoInk : LINE}`,
                           background: tab === t.key ? P.indigoInk : CARD,
                           color: tab === t.key ? '#FFF' : INK_2,
                           borderRadius: 999, padding: '10px 19px', fontSize: 14, fontWeight: 700, cursor: 'pointer' }}>
            {t.label}
          </button>
        ))}
      </div>

      {tab === 'ps1' ? <V3PS1 city={city} /> : (
        <div style={{ padding: '18px 22px 40px' }}>
          <div style={{ marginBottom: 16 }}>
            <div style={{ fontSize: 27, fontWeight: 800, color: INK, letterSpacing: '-.02em' }}>
              {({ ps2: 'Failure Patterns & Cascades', ps3: 'Root Cause Analysis',
                  ps4: 'Anomaly & Outlier Analysis', ps5: 'Remaining Life & SLA Breach' })[tab]}
            </div>
            <div style={{ fontSize: 14, color: INK_2 }}>
              Device grain and component grain are shown separately. Each panel says which it is.
            </div>
          </div>
          {/* key={tab} REMOUNTS on switch. Without it the feed state from the
              previous problem statement survives while the spec moves on, so
              the new screen reads keys that do not exist and throws. */}
          <V3Problem key={tab} which={tab} city={city} onDevice={setDevice} />
        </div>
      )}

      {brief && !brief.loading && (
        <DeviceBrief data={{ ...brief, device_id: brief.device_id || device }}
                     onClose={() => { setBrief(null); setDevice(null); }} />
      )}
      {brief && brief.loading && (
        <div style={{ position: 'fixed', inset: 0, zIndex: 1200, background: 'rgba(15,23,42,.4)',
                      display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
          <div style={{ background: CARD, borderRadius: 14, padding: '26px 34px' }}>
            <Loading h={70} label={`Opening ${device}`} />
          </div>
        </div>
      )}
    </div>
  );
}
