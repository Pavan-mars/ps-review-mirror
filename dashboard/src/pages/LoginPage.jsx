// ============================================================================
// Login Page — credential entry with role-based demo quick-login buttons
// ============================================================================

import React, { useState } from 'react';
import { useNavigate, useLocation } from 'react-router-dom';
import { useAuth } from '../auth/AuthContext';
// ROLE_LABELS available if needed for dynamic role display

const DEMO_ACCOUNTS = [
  { username: 'admin', password: 'admin123', label: 'Admin', color: '#6366f1' },
  { username: 'chi_manager', password: 'chicago1', label: 'Chicago Manager', color: '#f59e0b' },
  { username: 'bos_manager', password: 'boston1', label: 'Boston Manager', color: '#3b82f6' },
  { username: 'lax_manager', password: 'losangeles1', label: 'LA Manager', color: '#ef4444' },
  { username: 'toc_manager', password: 'toc1', label: 'TOC Manager', color: '#10b981' },
  { username: 'reader_sme', password: 'readers1', label: 'Reader SME', color: '#0ea5e9' },
  { username: 'tvm_sme', password: 'tvms1', label: 'TVM SME', color: '#8b5cf6' },
  { username: 'lner_operator', password: 'lner1', label: 'LNER Operator', color: '#14b8a6' },
];

const LoginPage = () => {
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const { login, error } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();

  const from = location.state?.from?.pathname || '/dashboard/overview';

  const handleSubmit = async (e) => {
    e.preventDefault();
    setSubmitting(true);
    try {
      await login(username, password);
      navigate(from, { replace: true });
    } catch {
      // error is captured in AuthContext
    } finally {
      setSubmitting(false);
    }
  };

  const handleDemoLogin = async (demo) => {
    setUsername(demo.username);
    setPassword(demo.password);
    setSubmitting(true);
    try {
      await login(demo.username, demo.password);
      navigate(from, { replace: true });
    } catch {
      // error shown via context
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div style={styles.wrapper}>
      <div style={styles.container}>
        {/* Left panel — branding */}
        <div style={styles.leftPanel}>
          <div>
            <h1 style={styles.brand}>CUBIC MARS</h1>
            <p style={styles.tagline}>Predictive Maintenance Platform</p>
            <div style={styles.stats}>
              <div style={styles.statItem}><span style={styles.statNum}>80</span><span style={styles.statLabel}>ML Models</span></div>
              <div style={styles.statItem}><span style={styles.statNum}>4</span><span style={styles.statLabel}>Cities</span></div>
              <div style={styles.statItem}><span style={styles.statNum}>11</span><span style={styles.statLabel}>TOC</span></div>
            </div>
          </div>
          <p style={styles.copyright}>© 2025 MARS Technologies</p>
        </div>

        {/* Right panel — login form */}
        <div style={styles.rightPanel}>
          <div style={styles.formContainer}>
            <h2 style={styles.formTitle}>Sign In</h2>
            <p style={styles.formSubtitle}>Enter your credentials to access the dashboard</p>

            {error && (
              <div style={styles.errorBox}>
                <span style={{ fontSize: 14 }}>⚠</span> {error}
              </div>
            )}

            <form onSubmit={handleSubmit}>
              <div style={styles.field}>
                <label style={styles.label}>Username</label>
                <input
                  style={styles.input}
                  type="text"
                  value={username}
                  onChange={(e) => setUsername(e.target.value)}
                  placeholder="Enter username"
                  required
                  autoFocus
                />
              </div>
              <div style={styles.field}>
                <label style={styles.label}>Password</label>
                <input
                  style={styles.input}
                  type="password"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  placeholder="Enter password"
                  required
                />
              </div>
              <button
                type="submit"
                style={{
                  ...styles.submitBtn,
                  opacity: submitting ? 0.7 : 1,
                  cursor: submitting ? 'not-allowed' : 'pointer',
                }}
                disabled={submitting}
              >
                {submitting ? 'Signing in...' : 'Sign In'}
              </button>
            </form>

            {/* Quick demo logins */}
            <div style={styles.demoSection}>
              <p style={styles.demoLabel}>Quick Demo Access</p>
              <div style={styles.demoGrid}>
                {DEMO_ACCOUNTS.map((d) => (
                  <button
                    key={d.username}
                    style={{ ...styles.demoBtn, borderColor: d.color, color: d.color }}
                    onClick={() => handleDemoLogin(d)}
                    disabled={submitting}
                    title={`Login as ${d.label}`}
                  >
                    {d.label}
                  </button>
                ))}
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
};

// --- Inline styles (keeps it self-contained) ---
const styles = {
  wrapper: {
    minHeight: '100vh',
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    background: 'linear-gradient(135deg, #1e293b 0%, #334155 100%)',
    padding: 20,
  },
  container: {
    display: 'flex',
    width: '100%',
    maxWidth: 900,
    minHeight: 540,
    borderRadius: 16,
    overflow: 'hidden',
    boxShadow: '0 25px 50px rgba(0,0,0,0.25)',
  },
  leftPanel: {
    flex: '0 0 320px',
    background: 'linear-gradient(135deg, #6366f1, #4f46e5)',
    color: '#fff',
    padding: '48px 36px',
    display: 'flex',
    flexDirection: 'column',
    justifyContent: 'space-between',
  },
  brand: { fontSize: 28, fontWeight: 700, letterSpacing: -1 },
  tagline: { fontSize: 14, marginTop: 8, opacity: 0.85 },
  stats: { display: 'flex', gap: 24, marginTop: 40 },
  statItem: { display: 'flex', flexDirection: 'column', gap: 2 },
  statNum: { fontSize: 28, fontWeight: 700 },
  statLabel: { fontSize: 11, opacity: 0.7, textTransform: 'uppercase', letterSpacing: 0.5 },
  copyright: { fontSize: 11, opacity: 0.5 },

  rightPanel: {
    flex: 1,
    background: '#fff',
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    padding: '40px 36px',
  },
  formContainer: { width: '100%', maxWidth: 360 },
  formTitle: { fontSize: 22, fontWeight: 700, color: '#1e293b' },
  formSubtitle: { fontSize: 13, color: '#64748b', marginTop: 6, marginBottom: 24 },
  errorBox: {
    display: 'flex', alignItems: 'center', gap: 8, padding: '10px 14px',
    background: '#fef2f2', color: '#dc2626', borderRadius: 8,
    fontSize: 13, marginBottom: 16, border: '1px solid #fecaca',
  },
  field: { marginBottom: 16 },
  label: { display: 'block', fontSize: 12, fontWeight: 600, color: '#374151', marginBottom: 6 },
  input: {
    width: '100%', padding: '10px 14px', borderRadius: 8,
    border: '1px solid #d1d5db', fontSize: 14, outline: 'none',
    transition: 'border-color 0.15s',
  },
  submitBtn: {
    width: '100%', padding: '11px 0', borderRadius: 8, border: 'none',
    background: '#6366f1', color: '#fff', fontSize: 14, fontWeight: 600,
    transition: 'background 0.15s',
    marginTop: 4,
  },
  demoSection: { marginTop: 28, paddingTop: 20, borderTop: '1px solid #e5e7eb' },
  demoLabel: { fontSize: 11, fontWeight: 600, color: '#9ca3af', textTransform: 'uppercase', letterSpacing: 0.5, marginBottom: 10 },
  demoGrid: { display: 'flex', flexWrap: 'wrap', gap: 6 },
  demoBtn: {
    padding: '5px 10px', borderRadius: 6, border: '1px solid',
    background: '#fff', fontSize: 11, fontWeight: 600, cursor: 'pointer',
    transition: 'all 0.15s',
  },
};

export default LoginPage;
