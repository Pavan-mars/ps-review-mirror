import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom';
import { AuthProvider } from './auth/AuthContext';
import { FilterProvider } from './context/FilterContext';
import ProtectedRoute from './auth/ProtectedRoute';
import Sidebar from './components/layout/Sidebar';
import FilterBar from './components/layout/FilterBar';
import LoginPage from './pages/LoginPage';
import AdminConsole from './pages/AdminConsole';
import ExecutiveOverview from './pages/ExecutiveOverview';
import V4Shell from './v4/V4Shell';

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

            {/* The /dashboard/city/:cityId route is GONE.          23-Sep-2026
                It rendered the pre-V4 dashboard: its own PS1/PS2/PS4/PS5 tabs,
                its own API client in data/api.js, and its own copies of panels
                V4 had already replaced. Two dashboards read the same Aurora
                tables and only one of them got the corrections -- so the OOS
                overstatement, the fabricated cascade-paths table and the
                ignition seed were all fixed in V4 and still rendered here.
                Worse, this was not an obscure URL: Sidebar.jsx linked four
                cities straight to it, from inside the V4 shell. */}

            {/* V4 -- the CURRENT Chicago dashboard: PS1-PS5, Device 360,
                shared location + evidence modules. / and * both redirect here.
                V2 and V3 were removed on 08-Aug-2026; see src/README.md. */}
            <Route path="/v4" element={
              <ProtectedRoute>
                <AdminLayout>
                  <V4Shell city="CHI" />
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
            {/* CLIENT ENTRY POINT.                              05-Aug-2026
                "/" and the catch-all used to land on /dashboard/overview --
                the old build. Chicago will be handed a bare ALB hostname with
                no path, so the root MUST be the shipped dashboard or every
                user sees the wrong product and nobody finds out until a
                meeting. /dashboard/overview stays reachable by direct link. */}
            <Route path="/" element={<Navigate to="/v4" replace />} />
            <Route path="/dashboard" element={<Navigate to="/dashboard/overview" replace />} />
            <Route path="*" element={<Navigate to="/v4" replace />} />
          </Routes>
        </FilterProvider>
      </AuthProvider>
    </BrowserRouter>
  );
}

export default App;
