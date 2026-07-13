// ============================================================================
// ProtectedRoute — redirects unauthenticated users to /login
// Optionally restricts to specific roles (e.g., admin-only pages)
// ============================================================================

import React from 'react';
import { Navigate, useLocation } from 'react-router-dom';
import { useAuth } from './AuthContext';

const ProtectedRoute = ({ children, requiredRole }) => {
  const { isAuthenticated, loading, currentUser } = useAuth();
  const location = useLocation();

  // Still restoring session — show nothing (or a spinner)
  if (loading) {
    return (
      <div style={{
        display: 'flex', alignItems: 'center', justifyContent: 'center',
        height: '100vh', background: '#f1f5f9',
      }}>
        <div style={{ textAlign: 'center', color: '#64748b' }}>
          <div style={{
            width: 36, height: 36, border: '3px solid #e2e8f0',
            borderTopColor: '#6366f1', borderRadius: '50%',
            animation: 'spin 0.8s linear infinite', margin: '0 auto 12px',
          }} />
          <span style={{ fontSize: 13 }}>Loading...</span>
        </div>
      </div>
    );
  }

  // Not logged in → redirect to login, preserving intended destination
  if (!isAuthenticated) {
    return <Navigate to="/login" state={{ from: location }} replace />;
  }

  // Role gate (e.g., admin console)
  if (requiredRole && currentUser?.role !== requiredRole) {
    return <Navigate to="/dashboard/overview" replace />;
  }

  return children;
};

export default ProtectedRoute;
