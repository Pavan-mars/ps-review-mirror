// ============================================================================
// AnalyseButton.jsx  ->  src/components/shared/AnalyseButton.jsx
// NEW (22-Jul-2026): one reusable "Analyse" button + wiring convention for
// EVERY PS2 table that has a device_id (directly, or resolved from a serial_id
// via useSerialDeviceMap in data/api.js). Styled to match the button PS1
// already ships in PS1FailurePredictionTab.jsx (background #6366f1, white
// text) so "Analyse" looks and behaves identically everywhere in the
// dashboard, not just in PS2. Dumb/presentational on purpose -- the caller
// decides what "analyse" means (almost always: open Device360Modal for a
// resolved deviceId), this component does not know about Device360Modal.
//
// Usage (device_id already in-row):
//   <AnalyseButton onClick={() => onAnalyse(row.device_id)} />
//
// Usage (serial_id row, device resolved via the shared serial->device map):
//   const deviceId = serialDeviceMap[row.serial_id];
//   <AnalyseButton onClick={() => onAnalyse(deviceId)} disabled={!deviceId}
//     title={deviceId ? undefined : 'No device mapping yet for this serial'} />
// ============================================================================
import React from 'react';

export default function AnalyseButton({ onClick, disabled, title, label = 'Analyse', compact = false }) {
  return (
    <button
      onClick={disabled ? undefined : onClick}
      disabled={disabled}
      title={title}
      style={{
        background: disabled ? '#C7CDD6' : '#6366f1',
        color: '#fff',
        border: 'none',
        borderRadius: 6,
        padding: compact ? '3px 9px' : '4px 10px',
        cursor: disabled ? 'not-allowed' : 'pointer',
        fontSize: 11,
        fontWeight: 600,
        whiteSpace: 'nowrap',
      }}
    >
      {label}
    </button>
  );
}
