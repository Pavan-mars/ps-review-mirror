import React, { useEffect, useState } from 'react';
import { NavLink, useNavigate, useLocation } from 'react-router-dom';
import { BarChart3, Settings, LogOut, ChevronLeft, ChevronRight } from 'lucide-react';
import { useAuth } from '../../auth/AuthContext';
import { ROLE_LABELS } from '../../auth/mockAuthAPI';

const cityItems = [
  { to: '/dashboard/city/CHI', label: 'Chicago', color: '#6366f1', cityCode: 'CHI', badge: 'Pilot', badgeClass: 'nav-badge daily' },
  { to: '/dashboard/city/BOS', label: 'Boston', color: '#f59e0b', cityCode: 'BOS' },
  { to: '/dashboard/city/LAX', label: 'Los Angeles', color: '#ef4444', cityCode: 'LAX' },
  { to: '/dashboard/city/TOC', label: 'TOC (UK)', color: '#10b981', cityCode: 'TOC', badge: '11 Co.', badgeClass: 'nav-badge rt' },
];

const Sidebar = () => {
  const { currentUser, isAdmin, canAccessCity, logout } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();

  const handleLogout = () => {
    logout();
    navigate('/login');
  };

  // Collapse is remembered for the session: a reader who narrows the rail
  // wants it narrow on the next screen too, not just this one.
  const [rail, setRail] = useState(false);
  useEffect(() => {
    const el = document.querySelector('.app-layout');
    if (el) el.classList.toggle('rail', rail);
  }, [rail]);

  return (
    <aside className="sidebar">
      <button type="button" className="rail-toggle" onClick={() => setRail((v) => !v)}
              aria-label={rail ? 'Expand navigation' : 'Collapse navigation'}
              title={rail ? 'Expand' : 'Collapse'}>
        {rail ? <ChevronRight size={14} /> : <ChevronLeft size={14} />}
      </button>
      <div className="sidebar-logo">
        <h1>CUBIC MARS</h1>
        <span className="sidebar-subtitle">Predictive Maintenance Platform</span>
      </div>

      <nav className="sidebar-nav">
        <label className="nav-label">OVERVIEW</label>
        <NavLink
          to="/dashboard/overview"
          className={({ isActive }) => 'nav-item' + (isActive ? ' active' : '')}
        >
          <BarChart3 size={18} />
          <span>Executive Overview</span>
        </NavLink>

        <label className="nav-label" style={{ marginTop: 16 }}>CITIES</label>
        {cityItems
          .filter((city) => canAccessCity(city.cityCode))
          .map((city) => {
            const active = location.pathname.startsWith(city.to);
            return (
              <NavLink
                key={city.to}
                to={city.to}
                className={'nav-item' + (active ? ' active' : '')}
              >
                <span style={{ display: 'inline-block', width: 8, height: 8, borderRadius: '50%', background: city.color, flexShrink: 0 }} />
                <span>{city.label}</span>
                {city.badge && <span className={city.badgeClass}>{city.badge}</span>}
              </NavLink>
            );
          })}

        {isAdmin && (
          <>
            <label className="nav-label" style={{ marginTop: 16 }}>ADMIN</label>
            <NavLink
              to="/admin"
              className={({ isActive }) => 'nav-item' + (isActive ? ' active' : '')}
            >
              <Settings size={18} />
              <span>Admin Console</span>
            </NavLink>
          </>
        )}
      </nav>

      {currentUser && (
        <div style={{ padding: '14px 16px', borderTop: '1px solid rgba(255,255,255,0.08)', display: 'flex', alignItems: 'center', gap: 10 }}>
          <div style={{ width: 34, height: 34, borderRadius: '50%', background: 'rgba(99,102,241,0.25)', color: '#818cf8', display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 13, fontWeight: 700, flexShrink: 0 }}>
            {currentUser.name.split(' ').map((n) => n[0]).join('').slice(0, 2)}
          </div>
          <div style={{ flex: 1, minWidth: 0 }}>
            <div style={{ fontSize: 13, fontWeight: 600, color: '#e2e8f0', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
              {currentUser.name}
            </div>
            <div style={{ fontSize: 10, color: '#94a3b8', textTransform: 'uppercase', letterSpacing: 0.5 }}>
              {ROLE_LABELS[currentUser.role] || currentUser.role}
            </div>
          </div>
          <button onClick={handleLogout} title="Sign out" style={{ background: 'transparent', border: 'none', cursor: 'pointer', color: '#94a3b8', padding: 4, borderRadius: 4 }}>
            <LogOut size={16} />
          </button>
        </div>
      )}

      <div className="sidebar-footer">
        <span className="sidebar-footer-title">MARS Technologies</span>
        <span className="sidebar-footer-version">v2.0 — 80 Models (5 PS × 4 Cities × 4 Devices) | 11 TOC</span>
      </div>
    </aside>
  );
};

export default Sidebar;
