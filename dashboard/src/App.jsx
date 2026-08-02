import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom';
import { AuthProvider } from './auth/AuthContext';
import { FilterProvider } from './context/FilterContext';
import ProtectedRoute from './auth/ProtectedRoute';
import Sidebar from './components/layout/Sidebar';
import FilterBar from './components/layout/FilterBar';
import LoginPage from './pages/LoginPage';
import AdminConsole from './pages/AdminConsole';
import ExecutiveOverview from './pages/ExecutiveOverview';
import CityDashboard from './pages/CityDashboard';
import PS1Overview from './v2/PS1Overview';
import PS4Overview from './v2/PS4Overview';
import PS2Overview from './v2/PS2Overview';
import V2Shell from './v2/V2Shell';

function DashboardLayout({ children }) {
  return (
    <div className="app-layout">
      <Sidebar />
      <div className="main-content">
        {/* RESTORED 29-Jul-2026. Removing this was wrong. FilterBar is not just
            chrome: the tables reconcile their rows against the selections it
            owns, so with it unmounted every PS1 and PS2 table filtered to zero
            and reported "No rows in the current selection" while the API was
            returning 500 rows. Tidying the top of the screen is not worth a
            dashboard that shows nothing. */}
        <FilterBar />
        <div className="page-content">
          {children}
        </div>
      </div>
    </div>
  );
}

function AdminLayout({ children }) {
  return (
    <div className="app-layout">
      <Sidebar />
      <div className="main-content">
        {children}
      </div>
    </div>
  );
}

function App() {
  return (
    <BrowserRouter>
      <AuthProvider>
        <FilterProvider>
          <Routes>
            {/* Public route */}
            <Route path="/login" element={<LoginPage />} />

            {/* Executive Overview */}
            <Route path="/dashboard/overview" element={
              <ProtectedRoute>
                <DashboardLayout><ExecutiveOverview /></DashboardLayout>
              </ProtectedRoute>
            } />

            {/* City-based dashboard routes */}
            <Route path="/dashboard/city/:cityId" element={
              <ProtectedRoute>
                <DashboardLayout><CityDashboard /></DashboardLayout>
              </ProtectedRoute>
            } />

            {/* v2 preview. Additive: no existing route is altered. */}
            <Route path="/v2/ps1" element={
              <ProtectedRoute>
                <AdminLayout>
                  <div style={{ padding: '22px 26px', background: '#FCFCFB', minHeight: '100vh' }}>
                    <PS1Overview city="CHI" />
                  </div>
                </AdminLayout>
              </ProtectedRoute>
            } />

            <Route path="/v2/ps4" element={
              <ProtectedRoute>
                <AdminLayout>
                  <div style={{ padding: '22px 26px', background: '#FCFCFB', minHeight: '100vh' }}>
                    <PS4Overview city="CHI" />
                  </div>
                </AdminLayout>
              </ProtectedRoute>
            } />

            <Route path="/v2/ps2" element={
              <ProtectedRoute>
                <AdminLayout>
                  <div style={{ padding: '22px 26px', background: '#FCFCFB', minHeight: '100vh' }}>
                    <PS2Overview city="CHI" />
                  </div>
                </AdminLayout>
              </ProtectedRoute>
            } />

            <Route path="/v2" element={
              <ProtectedRoute>
                <AdminLayout>
                  <div style={{ padding: '22px 26px', background: '#FCFCFB', minHeight: '100vh' }}>
                    <V2Shell city="CHI" />
                  </div>
                </AdminLayout>
              </ProtectedRoute>
            } />

            {/* Admin-only route */}
            <Route path="/admin" element={
              <ProtectedRoute requiredRole="admin">
                <AdminLayout><AdminConsole /></AdminLayout>
              </ProtectedRoute>
            } />

            {/* Redirects */}
            <Route path="/" element={<Navigate to="/dashboard/overview" replace />} />
            <Route path="/dashboard" element={<Navigate to="/dashboard/overview" replace />} />
            <Route path="*" element={<Navigate to="/dashboard/overview" replace />} />
          </Routes>
        </FilterProvider>
      </AuthProvider>
    </BrowserRouter>
  );
}

export default App;
