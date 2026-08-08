import { useEffect, useState, type FormEvent } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { api, ApiError } from "../api/client";
import type { LocalLoginResponse } from "../api/localAuth";
import { useAuth } from "../auth/AuthContext";
import EnrollOtpStep from "../components/auth/EnrollOtpStep";
import RecoveryCodesStep from "../components/auth/RecoveryCodesStep";

const ERROR_MESSAGES: Record<string, string> = {
  invalid_state: "Login session expired or was tampered with. Please try again.",
  token_invalid: "Microsoft's response could not be verified. Please try again.",
  organization_not_provisioned:
    "Your organization hasn't been set up in this dashboard yet. Contact your administrator.",
  organization_suspended: "This organization's access has been suspended. Contact your administrator.",
};

function MicrosoftMark() {
  return (
    <svg width="18" height="18" viewBox="0 0 21 21" aria-hidden="true">
      <rect x="1" y="1" width="9" height="9" fill="#f25022" />
      <rect x="11" y="1" width="9" height="9" fill="#7fba00" />
      <rect x="1" y="11" width="9" height="9" fill="#00a4ef" />
      <rect x="11" y="11" width="9" height="9" fill="#ffb900" />
    </svg>
  );
}

type Phase = "entra" | "password" | "otp" | "enroll" | "recovery";

export default function Login() {
  const [params] = useSearchParams();
  const error = params.get("error");
  const navigate = useNavigate();
  const { refetch } = useAuth();

  const [phase, setPhase] = useState<Phase>("entra");
  const [entraEnabled, setEntraEnabled] = useState<boolean | null>(null);
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [code, setCode] = useState("");
  const [formError, setFormError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [recoveryCodes, setRecoveryCodes] = useState<string[]>([]);

  useEffect(() => {
    let cancelled = false;
    api
      .get<{ entra_sso_enabled: boolean }>("/auth/config")
      .then((config) => {
        if (cancelled) return;
        setEntraEnabled(config.entra_sso_enabled);
        if (!config.entra_sso_enabled) setPhase("password");
      })
      .catch(() => {
        if (cancelled) return;
        setEntraEnabled(false);
        setPhase("password");
      });
    return () => {
      cancelled = true;
    };
  }, []);

  async function handlePasswordSubmit(e: FormEvent) {
    e.preventDefault();
    setSubmitting(true);
    setFormError(null);
    try {
      const result = await api.post<LocalLoginResponse | undefined>("/auth/local-login", { email, password });
      if (result === undefined) {
        // Read-only demo login — the backend skips the MFA dance entirely
        // and the session is already live (204, no body).
        await refetch();
        navigate("/");
        return;
      }
      setPhase(result.needs_enrollment ? "enroll" : "otp");
    } catch (err) {
      setFormError(err instanceof ApiError ? err.message : "sign-in failed");
    } finally {
      setSubmitting(false);
    }
  }

  async function handleOtpSubmit(e: FormEvent) {
    e.preventDefault();
    setSubmitting(true);
    setFormError(null);
    try {
      await api.post("/auth/verify-otp", { code });
      await refetch();
      navigate("/");
    } catch (err) {
      setFormError(err instanceof ApiError ? err.message : "invalid code");
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

  return (
    <main className="auth-page">
      <div className="auth-card">
        <div className="auth-brand">
          <span className="sidebar-brand-mark">Y</span>
          <span className="sidebar-brand-text" style={{ fontSize: "1.1rem" }}>
            YetAnotherDmarcTool
          </span>
        </div>

        {phase === "entra" && entraEnabled && (
          <>
            <p className="page-subtitle" style={{ marginBottom: 0 }}>
              Sign in to view your organization's DMARC reports and email-authentication status.
            </p>

            {error && (
              <div className="alert alert--critical" style={{ marginTop: "1.25rem", marginBottom: 0 }}>
                {ERROR_MESSAGES[error] ?? "Sign-in failed. Please try again."}
              </div>
            )}

            <a href="/api/auth/login" className="btn btn--primary" style={{ width: "100%", marginTop: "1.5rem", padding: "0.65rem" }}>
              <MicrosoftMark />
              Sign in with Microsoft
            </a>

            <button
              className="btn btn--ghost btn--sm"
              onClick={() => setPhase("password")}
              style={{ width: "100%", marginTop: "0.75rem", justifyContent: "center" }}
            >
              Sign in with email and password instead
            </button>
          </>
        )}

        {phase === "password" && (
          <form onSubmit={handlePasswordSubmit} className="auth-form" style={{ marginTop: "1rem" }}>
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
            {formError && (
              <div className="alert alert--critical" style={{ margin: 0 }}>
                {formError}
              </div>
            )}
            <button type="submit" className="btn btn--primary" disabled={submitting} style={{ padding: "0.65rem" }}>
              Continue
            </button>
            {entraEnabled && (
              <button type="button" className="btn btn--ghost btn--sm" onClick={() => setPhase("entra")} style={{ justifyContent: "center" }}>
                Back
              </button>
            )}
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
            {formError && (
              <div className="alert alert--critical" style={{ margin: 0 }}>
                {formError}
              </div>
            )}
            <button type="submit" className="btn btn--primary" disabled={submitting} style={{ padding: "0.65rem" }}>
              Sign in
            </button>
          </form>
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
