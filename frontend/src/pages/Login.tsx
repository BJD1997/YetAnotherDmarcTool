import { useSearchParams } from "react-router-dom";

const ERROR_MESSAGES: Record<string, string> = {
  invalid_state: "Login session expired or was tampered with. Please try again.",
  token_invalid: "Microsoft's response could not be verified. Please try again.",
  organization_not_provisioned:
    "Your organization hasn't been set up in this dashboard yet. Contact your administrator.",
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

export default function Login() {
  const [params] = useSearchParams();
  const error = params.get("error");

  return (
    <main className="auth-page">
      <div className="auth-card">
        <div className="auth-brand">
          <span className="sidebar-brand-mark">D</span>
          <span className="sidebar-brand-text" style={{ fontSize: "1.1rem" }}>
            DMARCwatch
          </span>
        </div>
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
      </div>
    </main>
  );
}
