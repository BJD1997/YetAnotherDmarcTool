import { Route, Routes } from "react-router-dom";
import Login from "./pages/Login";
import AdminLogin from "./pages/AdminLogin";
import AdminOrganizations from "./pages/AdminOrganizations";
import AdminJobRuns from "./pages/AdminJobRuns";
import Domains from "./pages/Domains";
import DomainDetail from "./pages/DomainDetail";
import DomainReports from "./pages/DomainReports";
import Team from "./pages/Team";
import Shell from "./components/Shell";
import RequireAuth from "./components/RequireAuth";
import RequireAdminAuth from "./components/RequireAdminAuth";

export default function App() {
  return (
    <Routes>
      <Route path="/login" element={<Login />} />
      <Route path="/admin/login" element={<AdminLogin />} />
      <Route
        path="/admin"
        element={
          <RequireAdminAuth>
            <AdminOrganizations />
          </RequireAdminAuth>
        }
      />
      <Route
        path="/admin/job-runs"
        element={
          <RequireAdminAuth>
            <AdminJobRuns />
          </RequireAdminAuth>
        }
      />
      <Route
        path="/"
        element={
          <RequireAuth>
            <Shell>
              <Domains />
            </Shell>
          </RequireAuth>
        }
      />
      <Route
        path="/domains/:domainId"
        element={
          <RequireAuth>
            <Shell>
              <DomainDetail />
            </Shell>
          </RequireAuth>
        }
      />
      <Route
        path="/domains/:domainId/reports"
        element={
          <RequireAuth>
            <Shell>
              <DomainReports />
            </Shell>
          </RequireAuth>
        }
      />
      <Route
        path="/team"
        element={
          <RequireAuth>
            <Shell>
              <Team />
            </Shell>
          </RequireAuth>
        }
      />
    </Routes>
  );
}
