import { useState, type FormEvent } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { api, ApiError } from "../api/client";
import { useAuth } from "../auth/AuthContext";
import EnrollOtpStep from "../components/auth/EnrollOtpStep";
import RecoveryCodesStep from "../components/auth/RecoveryCodesStep";

type Phase = "password" | "enroll" | "recovery";

export default function SetPassword() {
  const [params] = useSearchParams();
  const token = params.get("token") ?? "";
  const navigate = useNavigate();
  const { refetch } = useAuth();

  const [phase, setPhase] = useState<Phase>("password");
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [recoveryCodes, setRecoveryCodes] = useState<string[]>([]);

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    if (password !== confirm) {
      setError("Passwords don't match");
      return;
    }
    setSubmitting(true);
    setError(null);
    try {
      await api.post("/auth/set-password", { token, new_password: password });
      setPhase("enroll");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "couldn't set password");
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
    navigate("/");
  }

  if (!token) {
    return (
      <main className="auth-page">
        <div className="auth-card">
          <div className="alert alert--critical" style={{ margin: 0 }}>
            This link is missing its token. Ask your administrator for a fresh setup link.
          </div>
        </div>
      </main>
    );
  }

  return (
    <main className="auth-page">
      <div className="auth-card">
        <div className="auth-brand">
          <span className="sidebar-brand-mark">Y</span>
          <span className="sidebar-brand-text" style={{ fontSize: "1.1rem" }}>
            YetAnotherDmarcTool
          </span>
        </div>

        {phase === "password" && (
          <>
            <p className="page-subtitle" style={{ marginBottom: "1rem" }}>
              Set a password for your account.
            </p>
            <form onSubmit={handleSubmit} className="auth-form">
              <input
                className="input"
                type="password"
                placeholder="New password (at least 12 characters)"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                autoFocus
                required
                minLength={12}
              />
              <input
                className="input"
                type="password"
                placeholder="Confirm password"
                value={confirm}
                onChange={(e) => setConfirm(e.target.value)}
                required
              />
              {error && (
                <div className="alert alert--critical" style={{ margin: 0 }}>
                  {error}
                </div>
              )}
              <button type="submit" className="btn btn--primary" disabled={submitting} style={{ padding: "0.65rem" }}>
                Continue
              </button>
            </form>
          </>
        )}

        {phase === "enroll" && (
          <div style={{ marginTop: "1rem" }}>
            <EnrollOtpStep onComplete={handleEnrollComplete} />
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
