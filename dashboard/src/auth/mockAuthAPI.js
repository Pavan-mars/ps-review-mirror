// ============================================================================
// Mock Auth API — JWT-ready stubs for RBAC
// Replace these with real AWS Cognito / Auth0 / custom backend calls
// ============================================================================

import { TOC_COMPANIES } from '../data/mockData';

// --- Roles ---
export const ROLES = {
  ADMIN: 'admin',
  CITY_MANAGER: 'city_manager',
  DEVICE_SME: 'device_sme',
  TOC_OPERATOR: 'toc_operator',
};

export const ROLE_LABELS = {
  [ROLES.ADMIN]: 'Admin',
  [ROLES.CITY_MANAGER]: 'City Manager',
  [ROLES.DEVICE_SME]: 'Device SME',
  [ROLES.TOC_OPERATOR]: 'TOC Operator',
};

// --- Seed Users ---
const DEFAULT_USERS = [
  {
    id: 'usr_001',
    username: 'admin',
    password: 'admin123',
    name: 'System Administrator',
    email: 'admin@cubic-mars.com',
    role: ROLES.ADMIN,
    permissions: {
      cities: ['CHI', 'BOS', 'LAX', 'TOC'],
      devices: ['Readers', 'TVMs', 'Gates', 'Validators'],
      tocs: TOC_COMPANIES.map((t) => t.id),
    },
    active: true,
    createdAt: '2025-01-15T09:00:00Z',
  },
  {
    id: 'usr_002',
    username: 'chi_manager',
    password: 'chicago1',
    name: 'David Chen',
    email: 'david.chen@cubic-mars.com',
    role: ROLES.CITY_MANAGER,
    permissions: {
      cities: ['CHI'],
      devices: ['Readers', 'TVMs', 'Gates', 'Validators'],
      tocs: [],
    },
    active: true,
    createdAt: '2025-02-01T09:00:00Z',
  },
  {
    id: 'usr_003',
    username: 'bos_manager',
    password: 'boston1',
    name: 'Sarah Miller',
    email: 'sarah.miller@cubic-mars.com',
    role: ROLES.CITY_MANAGER,
    permissions: {
      cities: ['BOS'],
      devices: ['Readers', 'TVMs', 'Gates', 'Validators'],
      tocs: [],
    },
    active: true,
    createdAt: '2025-02-01T09:00:00Z',
  },
  {
    id: 'usr_004',
    username: 'lax_manager',
    password: 'losangeles1',
    name: 'Maria Garcia',
    email: 'maria.garcia@cubic-mars.com',
    role: ROLES.CITY_MANAGER,
    permissions: {
      cities: ['LAX'],
      devices: ['Readers', 'TVMs', 'Gates', 'Validators'],
      tocs: [],
    },
    active: true,
    createdAt: '2025-02-10T09:00:00Z',
  },
  {
    id: 'usr_005',
    username: 'toc_manager',
    password: 'toc1',
    name: 'James Wilson',
    email: 'james.wilson@cubic-mars.com',
    role: ROLES.CITY_MANAGER,
    permissions: {
      cities: ['TOC'],
      devices: ['Readers', 'TVMs', 'Gates', 'Validators'],
      tocs: TOC_COMPANIES.map((t) => t.id),
    },
    active: true,
    createdAt: '2025-02-10T09:00:00Z',
  },
  {
    // NOTE (11-Jul-2026): READER reversed to a component-level feature slice embedded
    // inside TVM/GATE/VALIDATOR — it is no longer a standalone filterable device, so this
    // account's scope was widened to all three real devices (reader-derived signals show
    // up inside each of them). Previously scoped to devices: ['Readers'] only, which would
    // have resolved to zero allowed devices after the taxonomy fix and broken this login.
    id: 'usr_006',
    username: 'reader_sme',
    password: 'readers1',
    name: 'Alex Thompson',
    email: 'alex.thompson@cubic-mars.com',
    role: ROLES.DEVICE_SME,
    permissions: {
      cities: ['CHI', 'BOS', 'LAX', 'TOC'],
      devices: ['TVMs', 'Gates', 'Validators'],
      tocs: TOC_COMPANIES.map((t) => t.id),
    },
    active: true,
    createdAt: '2025-03-01T09:00:00Z',
  },
  {
    id: 'usr_007',
    username: 'tvm_sme',
    password: 'tvms1',
    name: 'Priya Patel',
    email: 'priya.patel@cubic-mars.com',
    role: ROLES.DEVICE_SME,
    permissions: {
      cities: ['CHI', 'BOS', 'LAX', 'TOC'],
      devices: ['TVMs'],
      tocs: TOC_COMPANIES.map((t) => t.id),
    },
    active: true,
    createdAt: '2025-03-01T09:00:00Z',
  },
  {
    id: 'usr_008',
    username: 'lner_operator',
    password: 'lner1',
    name: 'Tom Hartley',
    email: 'tom.hartley@lner.co.uk',
    role: ROLES.TOC_OPERATOR,
    permissions: {
      cities: ['TOC'],
      devices: ['Readers', 'TVMs', 'Gates', 'Validators'],
      tocs: ['LNER'],
    },
    active: true,
    createdAt: '2025-03-15T09:00:00Z',
  },
  {
    id: 'usr_009',
    username: 'northern_operator',
    password: 'northern1',
    name: 'Emma Brooks',
    email: 'emma.brooks@northerntrains.co.uk',
    role: ROLES.TOC_OPERATOR,
    permissions: {
      cities: ['TOC'],
      devices: ['Readers', 'TVMs', 'Gates', 'Validators'],
      tocs: ['NTL'],
    },
    active: true,
    createdAt: '2025-04-01T09:00:00Z',
  },
  {
    id: 'usr_010',
    username: 'tfw_operator',
    password: 'tfw1',
    name: 'Owen Davies',
    email: 'owen.davies@tfw.wales',
    role: ROLES.TOC_OPERATOR,
    permissions: {
      cities: ['TOC'],
      devices: ['Readers', 'TVMs', 'Gates', 'Validators'],
      tocs: ['TFW'],
    },
    active: true,
    createdAt: '2025-04-01T09:00:00Z',
  },
];

// --- In-memory user store (simulates DB) ---
let userStore = [...DEFAULT_USERS];
let auditLog = [];

function loadStore() {
  try {
    const saved = localStorage.getItem('cubic_mars_users');
    if (saved) userStore = JSON.parse(saved);
    const savedLog = localStorage.getItem('cubic_mars_audit');
    if (savedLog) auditLog = JSON.parse(savedLog);
  } catch {
    // ignore parse errors
  }
}

function saveStore() {
  try {
    localStorage.setItem('cubic_mars_users', JSON.stringify(userStore));
    localStorage.setItem('cubic_mars_audit', JSON.stringify(auditLog));
  } catch {
    // ignore
  }
}

loadStore();
if (userStore.length === 0) {
  userStore = [...DEFAULT_USERS];
  saveStore();
}

// --- Simple JWT mock (base64-encoded payload, no real signing) ---
function createMockJWT(user) {
  const header = btoa(JSON.stringify({ alg: 'HS256', typ: 'JWT' }));
  const payload = btoa(
    JSON.stringify({
      sub: user.id,
      username: user.username,
      name: user.name,
      email: user.email,
      role: user.role,
      permissions: user.permissions,
      iat: Math.floor(Date.now() / 1000),
      exp: Math.floor(Date.now() / 1000) + 3600 * 8, // 8 hours
    })
  );
  const signature = btoa('mock-signature-' + user.id);
  return `${header}.${payload}.${signature}`;
}

export function parseJWT(token) {
  try {
    const parts = token.split('.');
    if (parts.length !== 3) return null;
    return JSON.parse(atob(parts[1]));
  } catch {
    return null;
  }
}

function isTokenExpired(payload) {
  return payload.exp * 1000 < Date.now();
}

// --- Audit logging ---
function logAudit(action, actor, details) {
  auditLog.unshift({
    id: `audit_${Date.now()}_${Math.random().toString(36).slice(2, 6)}`,
    action,
    actor,
    details,
    timestamp: new Date().toISOString(),
  });
  if (auditLog.length > 200) auditLog.length = 200;
  saveStore();
}

// --- API Stubs (return Promises to simulate async) ---

export async function loginAPI(username, password) {
  await new Promise((r) => setTimeout(r, 400)); // simulate latency
  const user = userStore.find(
    (u) => u.username === username && u.password === password && u.active
  );
  if (!user) {
    throw new Error('Invalid credentials or account disabled');
  }
  const token = createMockJWT(user);
  logAudit('LOGIN', user.username, `User ${user.name} logged in`);
  return {
    token,
    user: {
      id: user.id,
      username: user.username,
      name: user.name,
      email: user.email,
      role: user.role,
      permissions: user.permissions,
    },
  };
}

export async function refreshTokenAPI(currentToken) {
  const payload = parseJWT(currentToken);
  if (!payload) throw new Error('Invalid token');
  const user = userStore.find((u) => u.id === payload.sub && u.active);
  if (!user) throw new Error('User not found or disabled');
  return { token: createMockJWT(user) };
}

export async function validateTokenAPI(token) {
  const payload = parseJWT(token);
  if (!payload) return { valid: false };
  if (isTokenExpired(payload)) return { valid: false, reason: 'expired' };
  const user = userStore.find((u) => u.id === payload.sub && u.active);
  if (!user) return { valid: false, reason: 'user_disabled' };
  return { valid: true, payload };
}

// --- User Management API (Admin only) ---

export async function listUsersAPI() {
  await new Promise((r) => setTimeout(r, 200));
  return userStore.map((u) => ({
    id: u.id,
    username: u.username,
    name: u.name,
    email: u.email,
    role: u.role,
    permissions: u.permissions,
    active: u.active,
    createdAt: u.createdAt,
  }));
}

export async function createUserAPI(userData, actorUsername) {
  await new Promise((r) => setTimeout(r, 300));
  if (userStore.find((u) => u.username === userData.username)) {
    throw new Error('Username already exists');
  }
  const newUser = {
    id: `usr_${Date.now().toString(36)}`,
    username: userData.username,
    password: userData.password || 'changeme',
    name: userData.name,
    email: userData.email,
    role: userData.role,
    permissions: userData.permissions || { cities: [], devices: [], tocs: [] },
    active: true,
    createdAt: new Date().toISOString(),
  };
  userStore.push(newUser);
  saveStore();
  logAudit('CREATE_USER', actorUsername, `Created user ${newUser.username} with role ${newUser.role}`);
  return { id: newUser.id, username: newUser.username };
}

export async function updateUserAPI(userId, updates, actorUsername) {
  await new Promise((r) => setTimeout(r, 300));
  const idx = userStore.findIndex((u) => u.id === userId);
  if (idx === -1) throw new Error('User not found');
  const changes = [];
  if (updates.role && updates.role !== userStore[idx].role) {
    changes.push(`role: ${userStore[idx].role} → ${updates.role}`);
  }
  if (updates.permissions) changes.push('permissions updated');
  if (updates.active !== undefined && updates.active !== userStore[idx].active) {
    changes.push(updates.active ? 'account enabled' : 'account disabled');
  }
  userStore[idx] = { ...userStore[idx], ...updates };
  saveStore();
  logAudit(
    'UPDATE_USER',
    actorUsername,
    `Updated user ${userStore[idx].username}: ${changes.join(', ') || 'minor changes'}`
  );
  return { success: true };
}

export async function deleteUserAPI(userId, actorUsername) {
  await new Promise((r) => setTimeout(r, 200));
  const user = userStore.find((u) => u.id === userId);
  if (!user) throw new Error('User not found');
  userStore = userStore.filter((u) => u.id !== userId);
  saveStore();
  logAudit('DELETE_USER', actorUsername, `Deleted user ${user.username}`);
  return { success: true };
}

export async function getAuditLogAPI() {
  await new Promise((r) => setTimeout(r, 200));
  return [...auditLog];
}

export async function resetToDefaultsAPI(actorUsername) {
  userStore = [...DEFAULT_USERS];
  auditLog = [];
  saveStore();
  logAudit('RESET', actorUsername, 'Reset all users to defaults');
  return { success: true };
}
