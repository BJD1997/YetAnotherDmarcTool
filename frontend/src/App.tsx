import { Route, Routes } from "react-router-dom";
import Login from "./pages/Login";
import AdminLogin from "./pages/AdminLogin";
import AdminOrganizations from "./pages/AdminOrganizations";
import Domains from "./pages/Domains";
import DomainDetail from "./pages/DomainDetail";
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
