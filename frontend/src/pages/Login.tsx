import { useEffect, useState, type FormEvent } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { api, ApiError } from "../api/client";
import type { LocalLoginResponse } from "../api/localAuth";
import { useAuth } from "../auth/AuthContext";
import EnrollOtpStep from "../components/auth/EnrollOtpStep";
import RecoveryCodesStep from "../components/auth/RecoveryCodesStep";

const ERROR_MESSAGES: Record<string, string> = {
  invalid_state: "Login session expired or was tampered with. Please try again.",
  token_invalid: "Your provider's response could not be verified. Please try again.",
  email_not_verified: "Your provider didn't confirm your email address.",
  hd_mismatch: "Use your company Google Workspace account, not a personal Google account.",
  email_domain_not_verified: "Your email address isn't on one of your organization's verified domains.",
  provider_unavailable: "Your organization's sign-in provider can't be reached right now. Please try again later.",
  organization_not_provisioned:
    "Your organization hasn't been set up in this dashboard yet. Contact your administrator.",
  organization_suspended: "This organization's access has been suspended. Contact your administrator.",
  user_disabled: "Your account has been disabled. Contact an administrator of your organization.",
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

function GoogleMark() {
  return (
    <svg width="18" height="18" viewBox="0 0 48 48" aria-hidden="true">
      <path fill="#EA4335" d="M24 9.5c3.5 0 6.6 1.2 9 3.5l6.7-6.7C35.6 2.4 30.2 0 24 0 14.6 0 6.6 5.4 2.7 13.3l7.8 6.1C12.4 13.6 17.7 9.5 24 9.5z" />
      <path fill="#4285F4" d="M46.1 24.5c0-1.6-.1-3.1-.4-4.5H24v9h12.4c-.5 2.9-2.2 5.3-4.6 6.9l7.4 5.8c4.3-4 6.9-9.9 6.9-17.2z" />
      <path fill="#FBBC05" d="M10.5 28.6c-.5-1.4-.8-3-.8-4.6s.3-3.2.8-4.6l-7.8-6.1C1 16.6 0 20.2 0 24s1 7.4 2.7 10.7l7.8-6.1z" />
      <path fill="#34A853" d="M24 48c6.5 0 11.9-2.1 15.9-5.8l-7.4-5.8c-2.1 1.4-4.8 2.3-8.5 2.3-6.3 0-11.6-4.1-13.5-9.8l-7.8 6.1C6.6 42.6 14.6 48 24 48z" />
    </svg>
  );
}

interface AuthConfig {
  entra_sso_enabled: boolean;
  google_sso_enabled: boolean;
  company_sign_in_enabled: boolean;
}

const NO_PROVIDERS: AuthConfig = { entra_sso_enabled: false, google_sso_enabled: false, company_sign_in_enabled: false };

type Phase = "start" | "password" | "otp" | "enroll" | "recovery";

export default function Login() {
  const [params] = useSearchParams();
  const error = params.get("error");
  const navigate = useNavigate();
  const { refetch } = useAuth();

  const [phase, setPhase] = useState<Phase>("start");
  const [config, setConfig] = useState<AuthConfig | null>(null);
  const [companyEmail, setCompanyEmail] = useState("");
  const [companyError, setCompanyError] = useState<string | null>(null);
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [code, setCode] = useState("");
  const [formError, setFormError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [recoveryCodes, setRecoveryCodes] = useState<string[]>([]);

  useEffect(() => {
    let cancelled = false;
    api
      .get<AuthConfig>("/auth/config")
      .then((result) => {
        if (cancelled) return;
        setConfig(result);
        if (!result.entra_sso_enabled && !result.google_sso_enabled && !result.company_sign_in_enabled) setPhase("password");
      })
      .catch(() => {
        if (cancelled) return;
        setConfig(NO_PROVIDERS);
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

  async function handleCompanySubmit(e: FormEvent) {
    e.preventDefault();
    setCompanyError(null);
    setSubmitting(true);
    try {
      const { url } = await api.post<{ url: string | null }>("/auth/discover", { email: companyEmail.trim() });
      if (url) window.location.assign(url);
      else setCompanyError("There's no company sign-in for this email address. Use one of the other options.");
    } catch (err) {
      setCompanyError(err instanceof ApiError ? err.message : "Couldn't check this address. Please try again.");
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

        {error && (phase === "start" || phase === "password") && (
          <div className="alert alert--critical" style={{ marginTop: "1.25rem", marginBottom: 0 }}>
            {ERROR_MESSAGES[error] ?? "Sign-in failed. Please try again."}
          </div>
        )}

        {phase === "start" && config && (
          <>
            <p className="page-subtitle" style={{ marginBottom: 0 }}>
              Sign in to view your organization's DMARC reports and email-authentication status.
            </p>

            {config.entra_sso_enabled && (
              <a href="/api/auth/login" className="btn btn--primary" style={{ width: "100%", marginTop: "1.5rem", padding: "0.65rem" }}>
                <MicrosoftMark />
                Sign in with Microsoft
              </a>
            )}
            {config.google_sso_enabled && (
              <a
                href="/api/auth/google/login"
                className="btn btn--secondary"
                style={{ width: "100%", marginTop: config.entra_sso_enabled ? "0.6rem" : "1.5rem", padding: "0.65rem" }}
              >
                <GoogleMark />
                Sign in with Google
              </a>
            )}

            {config.company_sign_in_enabled && (
              <form onSubmit={handleCompanySubmit} className="auth-form" style={{ marginTop: "1.25rem" }}>
                <label className="field-label">
                  Company account
                  <input
                    className="input"
                    type="email"
                    placeholder="you@company.com"
                    value={companyEmail}
                    onChange={(e) => setCompanyEmail(e.target.value)}
                    required
                  />
                </label>
                {companyError && (
                  <div className="alert alert--warning" style={{ margin: 0 }}>
                    {companyError}
                  </div>
                )}
                <button type="submit" className="btn btn--secondary" disabled={submitting} style={{ padding: "0.65rem" }}>
                  Continue with company account
                </button>
              </form>
            )}

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
            {config && (config.entra_sso_enabled || config.google_sso_enabled || config.company_sign_in_enabled) && (
              <button type="button" className="btn btn--ghost btn--sm" onClick={() => setPhase("start")} style={{ justifyContent: "center" }}>
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
