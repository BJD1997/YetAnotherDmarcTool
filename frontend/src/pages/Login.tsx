import { useSearchParams } from "react-router-dom";

const ERROR_MESSAGES: Record<string, string> = {
  invalid_state: "Login session expired or was tampered with. Please try again.",
  token_invalid: "Microsoft's response could not be verified. Please try again.",
  organization_not_provisioned:
    "Your organization hasn't been set up in this dashboard yet. Contact your administrator.",
};

export default function Login() {
  const [params] = useSearchParams();
  const error = params.get("error");

  return (
    <main style={{ fontFamily: "system-ui, sans-serif", padding: "3rem", maxWidth: 420, margin: "0 auto" }}>
      <h1>DMARC Dashboard</h1>
      {error && (
        <p style={{ color: "#b91c1c", background: "#fee2e2", padding: "0.75rem 1rem", borderRadius: 6 }}>
          {ERROR_MESSAGES[error] ?? "Sign-in failed. Please try again."}
        </p>
      )}
      <a
        href="/api/auth/login"
        style={{
          display: "inline-block",
          marginTop: "1rem",
          padding: "0.75rem 1.25rem",
          background: "#2563eb",
          color: "white",
          borderRadius: 6,
          textDecoration: "none",
        }}
      >
        Sign in with Microsoft
      </a>
    </main>
  );
}
