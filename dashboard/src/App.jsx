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

function DashboardLayout({ children }) {
  return (
    <div className="app-layout">
      <Sidebar />
      <div className="main-content">
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
