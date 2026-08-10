import { useState, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";
import { api, ApiError } from "../api/client";
import type { LocalLoginResponse } from "../api/localAuth";
import { useAdminAuth } from "../auth/AdminAuthContext";
import EnrollOtpStep from "../components/auth/EnrollOtpStep";
import RecoveryCodesStep from "../components/auth/RecoveryCodesStep";

type Phase = "password" | "otp" | "enroll" | "recovery";

export default function AdminLogin() {
  const { refetch } = useAdminAuth();
  const navigate = useNavigate();

  const [phase, setPhase] = useState<Phase>("password");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [code, setCode] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [recoveryCodes, setRecoveryCodes] = useState<string[]>([]);

  async function handlePasswordSubmit(e: FormEvent) {
    e.preventDefault();
    setSubmitting(true);
    setError(null);
    try {
      const result = await api.post<LocalLoginResponse>("/admin/login", { email, password });
      setPhase(result.needs_enrollment ? "enroll" : "otp");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "login failed");
    } finally {
      setSubmitting(false);
    }
  }

  async function handleOtpSubmit(e: FormEvent) {
    e.preventDefault();
    setSubmitting(true);
    setError(null);
    try {
      await api.post("/admin/verify-otp", { code });
      await refetch();
      navigate("/admin");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "invalid code");
    } finally {
      setSubmitting(false);
    }
  }

  function handleEnrollComplete(codes: string[]) {
    setRecoveryCodes(codes);
    setPhase("recovery");
  }

  async function handleRecoveryContinue() {
    await refetch();
    navigate("/admin");
  }

  return (
    <main className="auth-page">
      <div className="auth-card">
        <div className="auth-brand">
          <span className="sidebar-brand-mark">Y</span>
          <span className="sidebar-brand-text" style={{ fontSize: "1.1rem" }}>
            Platform Admin
          </span>
        </div>

        {phase === "password" && (
          <form onSubmit={handlePasswordSubmit} className="auth-form">
            <input
              className="input"
              type="email"
              placeholder="Email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              autoFocus
              required
            />
            <input
              className="input"
              type="password"
              placeholder="Password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              required
            />
            {error && <div className="alert alert--critical" style={{ margin: 0 }}>{error}</div>}
            <button type="submit" className="btn btn--primary" disabled={submitting} style={{ padding: "0.65rem" }}>
              Sign in
            </button>
          </form>
        )}

        {phase === "otp" && (
          <form onSubmit={handleOtpSubmit} className="auth-form" style={{ marginTop: "1rem" }}>
            <p className="section-hint" style={{ marginTop: 0 }}>
              Enter the code from your authenticator app, or a recovery code.
            </p>
            <input
              className="input"
              inputMode="numeric"
              placeholder="6-digit code"
              value={code}
              onChange={(e) => setCode(e.target.value)}
              autoFocus
              required
            />
            {error && <div className="alert alert--critical" style={{ margin: 0 }}>{error}</div>}
            <button type="submit" className="btn btn--primary" disabled={submitting} style={{ padding: "0.65rem" }}>
              Sign in
            </button>
          </form>
        )}

        {phase === "enroll" && (
          <div style={{ marginTop: "1rem" }}>
            <EnrollOtpStep basePath="/admin" onComplete={handleEnrollComplete} />
          </div>
        )}

        {phase === "recovery" && (
          <div style={{ marginTop: "1rem" }}>
            <RecoveryCodesStep codes={recoveryCodes} onContinue={handleRecoveryContinue} />
          </div>
        )}
      </div>
    </main>
  );
}
