// ============================================================================
// Admin Console — User management, role assignment, permission matrix, audit log
// Only accessible by users with role === 'admin'
// ============================================================================

import React, { useState, useEffect, useCallback } from 'react';
import { useAuth } from '../auth/AuthContext';
import {
  ROLES, ROLE_LABELS,
  listUsersAPI, createUserAPI, updateUserAPI, deleteUserAPI,
  getAuditLogAPI, resetToDefaultsAPI,
} from '../auth/mockAuthAPI';
import { CITIES, TOC_COMPANIES } from '../data/mockData';

const DEVICE_OPTIONS = ['Readers', 'TVMs', 'Gates', 'Validators'];
const CITY_OPTIONS = CITIES.map((c) => ({ id: c.id, name: c.name }));
const TOC_OPTIONS = TOC_COMPANIES.map((t) => ({ id: t.id, name: t.name }));

const AdminConsole = () => {
  const { currentUser } = useAuth();
  const [activeTab, setActiveTab] = useState('users');
  const [users, setUsers] = useState([]);
  const [auditLog, setAuditLog] = useState([]);
  const [loading, setLoading] = useState(true);
  const [editUser, setEditUser] = useState(null);
  const [showCreateModal, setShowCreateModal] = useState(false);
  const [message, setMessage] = useState(null);

  const loadData = useCallback(async () => {
    setLoading(true);
    try {
      const [u, a] = await Promise.all([listUsersAPI(), getAuditLogAPI()]);
      setUsers(u);
      setAuditLog(a);
    } catch (err) {
      setMessage({ type: 'error', text: err.message });
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { loadData(); }, [loadData]);

  const flash = (type, text) => {
    setMessage({ type, text });
    setTimeout(() => setMessage(null), 3500);
  };

  // --- Handlers ---
  const handleToggleActive = async (user) => {
    try {
      await updateUserAPI(user.id, { active: !user.active }, currentUser.username);
      flash('success', `${user.name} ${user.active ? 'disabled' : 'enabled'}`);
      loadData();
    } catch (err) { flash('error', err.message); }
  };

  const handleDeleteUser = async (user) => {
    if (!window.confirm(`Delete user "${user.name}"? This cannot be undone.`)) return;
    try {
      await deleteUserAPI(user.id, currentUser.username);
      flash('success', `Deleted ${user.name}`);
      loadData();
    } catch (err) { flash('error', err.message); }
  };

  const handleReset = async () => {
    if (!window.confirm('Reset all users to defaults? This will remove any custom users.')) return;
    try {
      await resetToDefaultsAPI(currentUser.username);
      flash('success', 'Reset to defaults');
      loadData();
    } catch (err) { flash('error', err.message); }
  };

  const tabs = [
    { id: 'users', label: 'User Management' },
    { id: 'permissions', label: 'Permission Matrix' },
    { id: 'audit', label: 'Audit Log' },
  ];

  return (
    <div>
      <div className="header-bar">
        <div>
          <div className="header-title">Admin Console</div>
          <div className="header-subtitle">Manage users, roles, and permissions</div>
        </div>
        <div style={{ display: 'flex', gap: 8 }}>
          <button style={s.btnOutline} onClick={handleReset}>Reset Defaults</button>
          <button style={s.btnPrimary} onClick={() => { setEditUser(null); setShowCreateModal(true); }}>
            + New User
          </button>
        </div>
      </div>

      {message && (
        <div style={{
          ...s.flash,
          background: message.type === 'error' ? '#fef2f2' : '#f0fdf4',
          color: message.type === 'error' ? '#dc2626' : '#16a34a',
          borderColor: message.type === 'error' ? '#fecaca' : '#bbf7d0',
        }}>
          {message.text}
        </div>
      )}

      <div className="tab-container" style={{ margin: '0 24px' }}>
        {tabs.map((t) => (
          <div key={t.id} className={`tab${activeTab === t.id ? ' active' : ''}`} onClick={() => setActiveTab(t.id)}>
            {t.label}
          </div>
        ))}
      </div>

      <div style={{ padding: 24 }}>
        {loading ? (
          <p style={{ color: '#64748b', textAlign: 'center', padding: 40 }}>Loading...</p>
        ) : (
          <>
            {activeTab === 'users' && <UserTable users={users} onEdit={(u) => { setEditUser(u); setShowCreateModal(true); }} onToggle={handleToggleActive} onDelete={handleDeleteUser} />}
            {activeTab === 'permissions' && <PermissionMatrix users={users} />}
            {activeTab === 'audit' && <AuditLogTable logs={auditLog} />}
          </>
        )}
      </div>

      {showCreateModal && (
        <UserModal
          user={editUser}
          actorUsername={currentUser.username}
          onClose={() => { setShowCreateModal(false); setEditUser(null); }}
          onSaved={() => { setShowCreateModal(false); setEditUser(null); loadData(); flash('success', editUser ? 'User updated' : 'User created'); }}
        />
      )}
    </div>
  );
};

// ===================== Sub-components =====================

const UserTable = ({ users, onEdit, onToggle, onDelete }) => (
  <div className="card" style={{ padding: 0, overflow: 'hidden' }}>
    <table className="data-table">
      <thead>
        <tr>
          <th>Name</th>
          <th>Username</th>
          <th>Role</th>
          <th>Cities</th>
          <th>Devices</th>
          <th>Status</th>
          <th style={{ textAlign: 'right' }}>Actions</th>
        </tr>
      </thead>
      <tbody>
        {users.map((u) => (
          <tr key={u.id} style={{ opacity: u.active ? 1 : 0.5 }}>
            <td>
              <div style={{ fontWeight: 600, fontSize: 13 }}>{u.name}</div>
              <div style={{ fontSize: 11, color: '#94a3b8' }}>{u.email}</div>
            </td>
            <td><code style={s.code}>{u.username}</code></td>
            <td><span style={{ ...s.roleBadge, background: roleColor(u.role) }}>{ROLE_LABELS[u.role]}</span></td>
            <td style={{ fontSize: 12 }}>{u.permissions.cities.join(', ') || '—'}</td>
            <td style={{ fontSize: 12 }}>{u.permissions.devices.join(', ') || '—'}</td>
            <td>
              <span className={`badge ${u.active ? 'badge-success' : 'badge-info'}`}>
                {u.active ? 'Active' : 'Disabled'}
              </span>
            </td>
            <td style={{ textAlign: 'right' }}>
              <button style={s.actionBtn} onClick={() => onEdit(u)} title="Edit">✎</button>
              <button style={s.actionBtn} onClick={() => onToggle(u)} title={u.active ? 'Disable' : 'Enable'}>
                {u.active ? '⏸' : '▶'}
              </button>
              <button style={{ ...s.actionBtn, color: '#ef4444' }} onClick={() => onDelete(u)} title="Delete">✕</button>
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  </div>
);

const PermissionMatrix = ({ users }) => {
  const activeUsers = users.filter((u) => u.active);
  return (
    <div>
      <div className="card" style={{ marginBottom: 16 }}>
        <div className="card-header">City Access Matrix</div>
        <table className="data-table">
          <thead>
            <tr>
              <th>User</th>
              <th>Role</th>
              {CITY_OPTIONS.map((c) => <th key={c.id} style={{ textAlign: 'center' }}>{c.id}</th>)}
            </tr>
          </thead>
          <tbody>
            {activeUsers.map((u) => (
              <tr key={u.id}>
                <td style={{ fontWeight: 500 }}>{u.name}</td>
                <td><span style={{ ...s.roleBadge, background: roleColor(u.role), fontSize: 10 }}>{ROLE_LABELS[u.role]}</span></td>
                {CITY_OPTIONS.map((c) => (
                  <td key={c.id} style={{ textAlign: 'center' }}>
                    {u.role === 'admin' || u.permissions.cities.includes(c.id)
                      ? <span style={{ color: '#10b981', fontWeight: 700, fontSize: 16 }}>✓</span>
                      : <span style={{ color: '#e2e8f0' }}>—</span>}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="card" style={{ marginBottom: 16 }}>
        <div className="card-header">Device Access Matrix</div>
        <table className="data-table">
          <thead>
            <tr>
              <th>User</th>
              <th>Role</th>
              {DEVICE_OPTIONS.map((d) => <th key={d} style={{ textAlign: 'center' }}>{d}</th>)}
            </tr>
          </thead>
          <tbody>
            {activeUsers.map((u) => (
              <tr key={u.id}>
                <td style={{ fontWeight: 500 }}>{u.name}</td>
                <td><span style={{ ...s.roleBadge, background: roleColor(u.role), fontSize: 10 }}>{ROLE_LABELS[u.role]}</span></td>
                {DEVICE_OPTIONS.map((d) => (
                  <td key={d} style={{ textAlign: 'center' }}>
                    {u.role === 'admin' || u.permissions.devices.includes(d)
                      ? <span style={{ color: '#10b981', fontWeight: 700, fontSize: 16 }}>✓</span>
                      : <span style={{ color: '#e2e8f0' }}>—</span>}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="card">
        <div className="card-header">TOC Access Matrix</div>
        <div style={{ overflowX: 'auto' }}>
          <table className="data-table">
            <thead>
              <tr>
                <th>User</th>
                <th>Role</th>
                {TOC_OPTIONS.map((t) => <th key={t.id} style={{ textAlign: 'center', fontSize: 10 }}>{t.id}</th>)}
              </tr>
            </thead>
            <tbody>
              {activeUsers.filter((u) => u.permissions.cities.includes('TOC') || u.role === 'admin').map((u) => (
                <tr key={u.id}>
                  <td style={{ fontWeight: 500 }}>{u.name}</td>
                  <td><span style={{ ...s.roleBadge, background: roleColor(u.role), fontSize: 10 }}>{ROLE_LABELS[u.role]}</span></td>
                  {TOC_OPTIONS.map((t) => (
                    <td key={t.id} style={{ textAlign: 'center' }}>
                      {u.role === 'admin' || u.permissions.tocs.includes(t.id)
                        ? <span style={{ color: '#10b981', fontWeight: 700, fontSize: 16 }}>✓</span>
                        : <span style={{ color: '#e2e8f0' }}>—</span>}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
};

const AuditLogTable = ({ logs }) => (
  <div className="card" style={{ padding: 0, overflow: 'hidden' }}>
    {logs.length === 0 ? (
      <p style={{ padding: 24, color: '#94a3b8', textAlign: 'center' }}>No audit events yet</p>
    ) : (
      <table className="data-table">
        <thead>
          <tr>
            <th>Timestamp</th>
            <th>Action</th>
            <th>Actor</th>
            <th>Details</th>
          </tr>
        </thead>
        <tbody>
          {logs.slice(0, 100).map((log) => (
            <tr key={log.id}>
              <td style={{ fontSize: 12, color: '#64748b', whiteSpace: 'nowrap' }}>
                {new Date(log.timestamp).toLocaleString()}
              </td>
              <td>
                <span style={{
                  ...s.roleBadge,
                  background: log.action === 'LOGIN' ? '#dbeafe' : log.action === 'DELETE_USER' ? '#fee2e2' : '#f0fdf4',
                  color: log.action === 'LOGIN' ? '#2563eb' : log.action === 'DELETE_USER' ? '#dc2626' : '#16a34a',
                  fontSize: 10,
                }}>
                  {log.action}
                </span>
              </td>
              <td style={{ fontWeight: 500, fontSize: 13 }}>{log.actor}</td>
              <td style={{ fontSize: 12, color: '#64748b' }}>{log.details}</td>
            </tr>
          ))}
        </tbody>
      </table>
    )}
  </div>
);

// ===================== Create/Edit Modal =====================

const UserModal = ({ user, actorUsername, onClose, onSaved }) => {
  const isEdit = !!user;
  const [form, setForm] = useState({
    username: user?.username || '',
    name: user?.name || '',
    email: user?.email || '',
    password: '',
    role: user?.role || ROLES.CITY_MANAGER,
    cities: user?.permissions?.cities || [],
    devices: user?.permissions?.devices || [...DEVICE_OPTIONS],
    tocs: user?.permissions?.tocs || [],
    active: user?.active ?? true,
  });
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState(null);

  const toggle = (arr, val) => arr.includes(val) ? arr.filter((v) => v !== val) : [...arr, val];

  const handleSave = async () => {
    setSaving(true);
    setError(null);
    try {
      const payload = {
        username: form.username,
        name: form.name,
        email: form.email,
        role: form.role,
        permissions: { cities: form.cities, devices: form.devices, tocs: form.tocs },
        active: form.active,
      };
      if (isEdit) {
        await updateUserAPI(user.id, payload, actorUsername);
      } else {
        await createUserAPI({ ...payload, password: form.password || 'changeme' }, actorUsername);
      }
      onSaved();
    } catch (err) {
      setError(err.message);
    } finally {
      setSaving(false);
    }
  };

  return (
    <div style={s.overlay} onClick={onClose}>
      <div style={s.modal} onClick={(e) => e.stopPropagation()}>
        <h3 style={{ fontSize: 18, fontWeight: 700, marginBottom: 20 }}>
          {isEdit ? `Edit: ${user.name}` : 'Create New User'}
        </h3>

        {error && <div style={{ ...s.flash, background: '#fef2f2', color: '#dc2626', borderColor: '#fecaca', marginBottom: 16 }}>{error}</div>}

        <div style={s.formGrid}>
          <div>
            <label style={s.fLabel}>Username</label>
            <input style={s.fInput} value={form.username} onChange={(e) => setForm({ ...form, username: e.target.value })} disabled={isEdit} />
          </div>
          <div>
            <label style={s.fLabel}>Full Name</label>
            <input style={s.fInput} value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} />
          </div>
          <div>
            <label style={s.fLabel}>Email</label>
            <input style={s.fInput} value={form.email} onChange={(e) => setForm({ ...form, email: e.target.value })} />
          </div>
          {!isEdit && (
            <div>
              <label style={s.fLabel}>Password</label>
              <input style={s.fInput} type="password" value={form.password} onChange={(e) => setForm({ ...form, password: e.target.value })} placeholder="changeme" />
            </div>
          )}
          <div>
            <label style={s.fLabel}>Role</label>
            <select style={s.fInput} value={form.role} onChange={(e) => setForm({ ...form, role: e.target.value })}>
              {Object.entries(ROLE_LABELS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
            </select>
          </div>
        </div>

        {/* Permission toggles */}
        <div style={{ marginTop: 20 }}>
          <label style={s.fLabel}>City Access</label>
          <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginTop: 6 }}>
            {CITY_OPTIONS.map((c) => (
              <button key={c.id} style={{ ...s.chip, ...(form.cities.includes(c.id) ? s.chipActive : {}) }}
                onClick={() => setForm({ ...form, cities: toggle(form.cities, c.id) })}>
                {c.id}
              </button>
            ))}
          </div>
        </div>

        <div style={{ marginTop: 14 }}>
          <label style={s.fLabel}>Device Access</label>
          <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginTop: 6 }}>
            {DEVICE_OPTIONS.map((d) => (
              <button key={d} style={{ ...s.chip, ...(form.devices.includes(d) ? s.chipActive : {}) }}
                onClick={() => setForm({ ...form, devices: toggle(form.devices, d) })}>
                {d}
              </button>
            ))}
          </div>
        </div>

        {form.cities.includes('TOC') && (
          <div style={{ marginTop: 14 }}>
            <label style={s.fLabel}>TOC Access</label>
            <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginTop: 6 }}>
              {TOC_OPTIONS.map((t) => (
                <button key={t.id} style={{ ...s.chip, ...(form.tocs.includes(t.id) ? s.chipActive : {}) }}
                  onClick={() => setForm({ ...form, tocs: toggle(form.tocs, t.id) })}>
                  {t.id}
                </button>
              ))}
            </div>
          </div>
        )}

        <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 10, marginTop: 28 }}>
          <button style={s.btnOutline} onClick={onClose}>Cancel</button>
          <button style={{ ...s.btnPrimary, opacity: saving ? 0.7 : 1 }} onClick={handleSave} disabled={saving}>
            {saving ? 'Saving...' : isEdit ? 'Update User' : 'Create User'}
          </button>
        </div>
      </div>
    </div>
  );
};

// ===================== Helpers & Styles =====================

function roleColor(role) {
  switch (role) {
    case 'admin': return '#ede9fe';
    case 'city_manager': return '#dbeafe';
    case 'device_sme': return '#fef3c7';
    case 'toc_operator': return '#d1fae5';
    default: return '#f1f5f9';
  }
}

const s = {
  btnPrimary: {
    padding: '8px 18px', borderRadius: 8, border: 'none',
    background: '#6366f1', color: '#fff', fontSize: 13, fontWeight: 600, cursor: 'pointer',
  },
  btnOutline: {
    padding: '8px 18px', borderRadius: 8, border: '1px solid #d1d5db',
    background: '#fff', color: '#374151', fontSize: 13, fontWeight: 500, cursor: 'pointer',
  },
  actionBtn: {
    padding: '4px 8px', border: 'none', background: 'transparent',
    cursor: 'pointer', fontSize: 14, borderRadius: 4,
  },
  code: {
    background: '#f1f5f9', padding: '2px 8px', borderRadius: 4,
    fontSize: 12, fontFamily: 'monospace', color: '#6366f1',
  },
  roleBadge: {
    display: 'inline-block', padding: '3px 10px', borderRadius: 999,
    fontSize: 11, fontWeight: 600,
  },
  flash: {
    margin: '12px 24px', padding: '10px 16px', borderRadius: 8,
    border: '1px solid', fontSize: 13, fontWeight: 500,
  },
  overlay: {
    position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.5)',
    display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 1000,
  },
  modal: {
    background: '#fff', borderRadius: 14, padding: 32,
    width: '100%', maxWidth: 560, maxHeight: '90vh', overflowY: 'auto',
    boxShadow: '0 20px 60px rgba(0,0,0,0.3)',
  },
  formGrid: { display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 14 },
  fLabel: { display: 'block', fontSize: 12, fontWeight: 600, color: '#374151', marginBottom: 5 },
  fInput: {
    width: '100%', padding: '8px 12px', borderRadius: 8,
    border: '1px solid #d1d5db', fontSize: 13, outline: 'none',
  },
  chip: {
    padding: '4px 12px', borderRadius: 6, border: '1px solid #d1d5db',
    background: '#fff', fontSize: 12, fontWeight: 500, cursor: 'pointer',
    color: '#64748b', transition: 'all 0.15s',
  },
  chipActive: {
    background: '#6366f1', color: '#fff', borderColor: '#6366f1',
  },
};

export default AdminConsole;
