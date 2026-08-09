import { Route, Routes } from "react-router-dom";
import Login from "./pages/Login";
import SetPassword from "./pages/SetPassword";
import AdminLogin from "./pages/AdminLogin";
import AdminOrganizations from "./pages/AdminOrganizations";
import AdminJobRuns from "./pages/AdminJobRuns";
import AdminUpdates from "./pages/AdminUpdates";
import Overview from "./pages/Overview";
import Onboarding from "./pages/Onboarding";
import Domains from "./pages/Domains";
import DomainReports from "./pages/DomainReports";
import DomainTlsReports from "./pages/DomainTlsReports";
import DomainDetailLayout from "./pages/domain-detail/DomainDetailLayout";
import OverviewTab from "./pages/domain-detail/OverviewTab";
import FixesTab from "./pages/domain-detail/FixesTab";
import DnsChecksTab from "./pages/domain-detail/DnsChecksTab";
import SendersTab from "./pages/domain-detail/SendersTab";
import InboundTab from "./pages/domain-detail/InboundTab";
import Team from "./pages/Team";
import SettingsLayout from "./pages/SettingsLayout";
import GeneralTab from "./pages/settings/GeneralTab";
import DomainsTab from "./pages/settings/DomainsTab";
import SignInActivityTab from "./pages/settings/SignInActivityTab";
import Shell from "./components/Shell";
import AdminShell from "./components/AdminShell";
import RequireAuth from "./components/RequireAuth";
import RequireAdminAuth from "./components/RequireAdminAuth";

export default function App() {
  return (
    <Routes>
      <Route path="/login" element={<Login />} />
      <Route path="/set-password" element={<SetPassword />} />
      <Route path="/admin/login" element={<AdminLogin />} />
      <Route
        path="/admin"
        element={
          <RequireAdminAuth>
            <AdminShell>
              <AdminOrganizations />
            </AdminShell>
          </RequireAdminAuth>
        }
      />
      <Route
        path="/admin/job-runs"
        element={
          <RequireAdminAuth>
            <AdminShell>
              <AdminJobRuns />
            </AdminShell>
          </RequireAdminAuth>
        }
      />
      <Route
        path="/admin/updates"
        element={
          <RequireAdminAuth>
            <AdminShell>
              <AdminUpdates />
            </AdminShell>
          </RequireAdminAuth>
        }
      />
      <Route
        path="/"
        element={
          <RequireAuth>
            <Shell>
              <Overview />
            </Shell>
          </RequireAuth>
        }
      />
      <Route
        path="/onboarding"
        element={
          <RequireAuth>
            <Shell>
              <Onboarding />
            </Shell>
          </RequireAuth>
        }
      />
      <Route
        path="/domains"
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
              <DomainDetailLayout />
            </Shell>
          </RequireAuth>
        }
      >
        <Route index element={<OverviewTab />} />
        <Route path="fixes" element={<FixesTab />} />
        <Route path="dns" element={<DnsChecksTab />} />
        <Route path="senders" element={<SendersTab />} />
        <Route path="inbound" element={<InboundTab />} />
        <Route path="reports" element={<DomainReports />} />
        <Route path="tls-reports" element={<DomainTlsReports />} />
      </Route>
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
      <Route
        path="/settings"
        element={
          <RequireAuth>
            <Shell>
              <SettingsLayout />
            </Shell>
          </RequireAuth>
        }
      >
        <Route index element={<GeneralTab />} />
        <Route path="domains" element={<DomainsTab />} />
        <Route path="sign-in-activity" element={<SignInActivityTab />} />
      </Route>
    </Routes>
  );
}
