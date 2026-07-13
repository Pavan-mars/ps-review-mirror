import React from 'react';
import { Download } from 'lucide-react';

const PageHeader = ({ title, subtitle, icon: Icon }) => {
  const today = new Date().toLocaleDateString('en-US', {
    weekday: 'long',
    year: 'numeric',
    month: 'long',
    day: 'numeric',
  });

  return (
    <div className="header-bar">
      <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
        {Icon && <Icon size={24} />}
        <div>
          <h1 className="header-title">{title}</h1>
          <p className="header-subtitle">{subtitle}</p>
        </div>
      </div>
      <div style={{ display: 'flex', alignItems: 'center', gap: 16 }}>
        <span style={{ fontSize: 14, opacity: 0.7 }}>{today}</span>
        <button
          className="export-btn"
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: 6,
            padding: '6px 14px',
            border: '1px solid rgba(255,255,255,0.2)',
            borderRadius: 6,
            background: 'transparent',
            color: 'inherit',
            cursor: 'pointer',
            fontSize: 13,
          }}
        >
          <Download size={14} />
          Export
        </button>
      </div>
    </div>
  );
};

export default PageHeader;
