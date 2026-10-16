import { useEffect, useState, type ChangeEvent } from "react";
import { Check, Copy } from "lucide-react";
import { ApiError } from "../../api/client";
import { useClipboardFeedback } from "../../hooks/useClipboardFeedback";
import { useRemoveOidc, useSaveOidc, useSignInSettings, useStartOidcTest } from "../../hooks/useSignInSettings";

const PROVIDER_NAME = { microsoft: "Microsoft", google: "Google Workspace" } as const;

// How people in this organization sign in. Local accounts always work; on
// top of that one provider. Org admins manage their own OIDC provider here,
// and only save it after a test sign-in with it passed; Microsoft and
// Google are set by the platform admin.
export default function SignInTab() {
  const { data } = useSignInSettings();
  const [issuer, setIssuer] = useState("");
  const [clientId, setClientId] = useState("");
  const [secret, setSecret] = useState("");
  const [ticket, setTicket] = useState<string | null>(null);
  const [result, setResult] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const startTest = useStartOidcTest();
  const save = useSaveOidc();
  const remove = useRemoveOidc();
  const { copied, copy } = useClipboardFeedback();

  useEffect(() => {
    if (data?.oidc) {
      setIssuer(data.oidc.issuer);
      setClientId(data.oidc.client_id);
    }
  }, [data?.oidc]);

  useEffect(() => {
    function onMessage(event: MessageEvent) {
      if (event.origin !== window.location.origin || event.data?.type !== "yadt-oidc-test") return;
      if (event.data.ok) {
        setTicket(event.data.ticket);
        setError(null);
        setResult(`Test passed. Signed in as ${event.data.email}. You can save now.`);
      } else {
        setTicket(null);
        setResult(null);
        setError(event.data.error ?? "The test sign-in failed.");
      }
    }
    window.addEventListener("message", onMessage);
    return () => window.removeEventListener("message", onMessage);
  }, []);

  if (!data) return <p className="muted">Loading…</p>;

  const body = { issuer: issuer.trim(), client_id: clientId.trim(), ...(secret ? { client_secret: secret } : {}) };
  const message = (err: Error, fallback: string) => (err instanceof ApiError ? err.message : fallback);
  // Any change after a test needs a new test.
  const edit = (set: (value: string) => void) => (e: ChangeEvent<HTMLInputElement>) => {
    set(e.target.value);
    setTicket(null);
    setResult(null);
  };

  return (
    <div style={{ display: "grid", gap: "1rem", maxWidth: "42rem" }}>
      <div className="card">
        <h2 style={{ marginTop: 0 }}>Direct sign-in link</h2>
        <p className="section-hint">Share or bookmark this link: it goes straight to your organization's sign-in.</p>
        <div className="field-row">
          <code className="input" style={{ display: "inline-block" }}>
            {data.login_link}
          </code>
          <button className="btn btn--secondary btn--sm" onClick={() => void copy(data.login_link)}>
            {copied ? <Check /> : <Copy />}
            {copied ? "Copied" : "Copy link"}
          </button>
        </div>
      </div>

      <div className="card">
        <h2 style={{ marginTop: 0 }}>Sign-in provider</h2>
        {data.provider === "microsoft" || data.provider === "google" ? (
          <p className="section-hint" style={{ margin: 0 }}>
            Your organization signs in with {PROVIDER_NAME[data.provider]}
            {data.provider === "google" && data.google_workspace_domain ? ` (${data.google_workspace_domain})` : ""}. Ask the
            platform admin to change this. Local accounts keep working next to it.
          </p>
        ) : (
          <form
            onSubmit={(e) => {
              e.preventDefault();
              if (!ticket) return;
              setError(null);
              save.mutate(
                { ...body, ticket },
                {
                  onSuccess: () => {
                    setSecret("");
                    setTicket(null);
                    setResult("Saved. People can now sign in with your provider.");
                  },
                  onError: (err) => setError(message(err, "couldn't save")),
                },
              );
            }}
            style={{ display: "grid", gap: "0.6rem" }}
          >
            <p className="section-hint" style={{ margin: 0 }}>
              Any OpenID Connect provider works (Authentik, Keycloak, Zitadel, Okta, ...). Register this app there with
              the redirect URI below and the scopes <code>openid email profile</code>. People sign in with an email address
              on one of your verified domains; local accounts keep working.
            </p>
            <div style={{ fontSize: "0.9rem" }}>
              Redirect URI: <code>{data.redirect_uri}</code>
            </div>
            <label className="field-label">
              Issuer URL
              <input className="input" value={issuer} onChange={edit(setIssuer)} placeholder="https://auth.example.com/application/o/yadt" required />
            </label>
            <label className="field-label">
              Client ID
              <input className="input" value={clientId} onChange={edit(setClientId)} required />
            </label>
            <label className="field-label">
              Client secret
              <input
                className="input"
                type="password"
                value={secret}
                onChange={edit(setSecret)}
                placeholder={data.oidc?.secret_set ? "unchanged" : ""}
                required={!data.oidc?.secret_set}
                autoComplete="new-password"
              />
            </label>
            {result && <div className="alert alert--good" style={{ margin: 0 }}>{result}</div>}
            {error && <div className="alert alert--critical" style={{ margin: 0 }}>{error}</div>}
            <div className="chip-row">
              <button
                type="button"
                className="btn btn--secondary btn--sm"
                disabled={startTest.isPending || !issuer.trim() || !clientId.trim()}
                onClick={() => {
                  setError(null);
                  setResult(null);
                  startTest.mutate(body, {
                    onSuccess: ({ start_url }) => {
                      window.open(start_url, "yadt-oidc-test", "width=520,height=680");
                    },
                    onError: (err) => setError(message(err, "couldn't start the test")),
                  });
                }}
              >
                {startTest.isPending ? "Starting…" : "Test sign-in"}
              </button>
              <button type="submit" className="btn btn--primary btn--sm" disabled={!ticket || save.isPending}>
                Save
              </button>
              {data.oidc && (
                <button
                  type="button"
                  className="btn btn--ghost btn--sm"
                  disabled={remove.isPending}
                  onClick={() => {
                    if (window.confirm("Remove your sign-in provider? People then sign in with local accounts only.")) remove.mutate();
                  }}
                >
                  Remove provider
                </button>
              )}
            </div>
          </form>
        )}
      </div>
    </div>
  );
}
