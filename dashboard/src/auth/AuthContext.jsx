// ============================================================================
// Auth Context — global authentication state for the React app
// Wraps the mock JWT API; swap mockAuthAPI imports for real backend calls
// ============================================================================

import React, { createContext, useContext, useState, useCallback, useEffect, useMemo } from 'react';
import { loginAPI, parseJWT, validateTokenAPI, refreshTokenAPI } from './mockAuthAPI';

const AuthContext = createContext(null);

const TOKEN_KEY = 'cubic_mars_token';

export function AuthProvider({ children }) {
  const [currentUser, setCurrentUser] = useState(null);
  const [token, setToken] = useState(null);
  const [loading, setLoading] = useState(true); // true while restoring session
  const [error, setError] = useState(null);

  // --- Restore session from localStorage on mount ---
  useEffect(() => {
    const restore = async () => {
      try {
        const saved = localStorage.getItem(TOKEN_KEY);
        if (!saved) { setLoading(false); return; }

        const result = await validateTokenAPI(saved);
        if (result.valid) {
          const payload = parseJWT(saved);
          setToken(saved);
          setCurrentUser({
            id: payload.sub,
            username: payload.username,
            name: payload.name,
            email: payload.email,
            role: payload.role,
            permissions: payload.permissions,
          });
        } else {
          localStorage.removeItem(TOKEN_KEY);
        }
      } catch {
        localStorage.removeItem(TOKEN_KEY);
      } finally {
        setLoading(false);
      }
    };
    restore();
  }, []);

  // --- Login ---
  const login = useCallback(async (username, password) => {
    setError(null);
    try {
      const result = await loginAPI(username, password);
      setToken(result.token);
      setCurrentUser(result.user);
      localStorage.setItem(TOKEN_KEY, result.token);
      return result.user;
    } catch (err) {
      setError(err.message);
      throw err;
    }
  }, []);

  // --- Logout ---
  const logout = useCallback(() => {
    setToken(null);
    setCurrentUser(null);
    localStorage.removeItem(TOKEN_KEY);
  }, []);

  // --- Refresh token (call periodically or before expiry) ---
  const refresh = useCallback(async () => {
    if (!token) return;
    try {
      const result = await refreshTokenAPI(token);
      setToken(result.token);
      localStorage.setItem(TOKEN_KEY, result.token);
    } catch {
      logout();
    }
  }, [token, logout]);

  // --- Derived helpers ---
  const isAuthenticated = !!currentUser && !!token;
  const isAdmin = currentUser?.role === 'admin';

  // Permission check helpers
  const canAccessCity = useCallback(
    (cityId) => {
      if (!currentUser) return false;
      if (currentUser.role === 'admin') return true;
      return currentUser.permissions?.cities?.includes(cityId) ?? false;
    },
    [currentUser]
  );

  const canAccessDevice = useCallback(
    (device) => {
      if (!currentUser) return false;
      if (currentUser.role === 'admin') return true;
      return currentUser.permissions?.devices?.includes(device) ?? false;
    },
    [currentUser]
  );

  const canAccessTOC = useCallback(
    (tocId) => {
      if (!currentUser) return false;
      if (currentUser.role === 'admin') return true;
      return currentUser.permissions?.tocs?.includes(tocId) ?? false;
    },
    [currentUser]
  );

  const value = useMemo(
    () => ({
      currentUser,
      token,
      loading,
      error,
      isAuthenticated,
      isAdmin,
      login,
      logout,
      refresh,
      canAccessCity,
      canAccessDevice,
      canAccessTOC,
    }),
    [currentUser, token, loading, error, isAuthenticated, isAdmin, login, logout, refresh, canAccessCity, canAccessDevice, canAccessTOC]
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth() {
  const context = useContext(AuthContext);
  if (!context) {
    throw new Error('useAuth must be used within an AuthProvider');
  }
  return context;
}

export default AuthContext;
