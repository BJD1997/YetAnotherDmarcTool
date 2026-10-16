import { useEffect, useState, type ChangeEvent } from "react";
import { Check, Copy } from "lucide-react";
import { ApiError } from "../../api/client";
import { useClipboardFeedback } from "../../hooks/useClipboardFeedback";
import {
  useRemoveProvider,
  useSaveProvider,
  useSignInSettings,
  useStartSignInTest,
  type ProviderIn,
  type SignInProvider,
} from "../../hooks/useSignInSettings";

const PROVIDER_NAME: Record<SignInProvider, string> = {
  none: "local accounts only",
  microsoft: "Microsoft",
  google: "Google Workspace",
  oidc: "your own OpenID Connect provider",
};

type Choice = Exclude<SignInProvider, "none">;

interface TestResult {
  ticket: string;
  email: string;
  tenant_id?: string | null;
  workspace?: string | null;
}

// How people in this organization sign in: local accounts plus at most one
// provider. Only the break-glass account changes it, and only after a test
// sign-in with the new provider passed; everyone else sees it read-only.
export default function SignInTab() {
  const { data } = useSignInSettings();
  const [choice, setChoice] = useState<Choice>("oidc");
  const [issuer, setIssuer] = useState("");
  const [clientId, setClientId] = useState("");
  const [secret, setSecret] = useState("");
  const [passed, setPassed] = useState<TestResult | null>(null);
  const [result, setResult] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const startTest = useStartSignInTest();
  const save = useSaveProvider();
  const remove = useRemoveProvider();
  const { copied, copy } = useClipboardFeedback();

  useEffect(() => {
    if (!data) return;
    if (data.provider !== "none") setChoice(data.provider);
    if (data.oidc) {
      setIssuer(data.oidc.issuer);
      setClientId(data.oidc.client_id);
    }
  }, [data]);

  useEffect(() => {
    function onMessage(event: MessageEvent) {
      if (event.origin !== window.location.origin || event.data?.type !== "yadt-sign-in-test") return;
      if (event.data.ok) {
        setPassed({ ticket: event.data.ticket, email: event.data.email, tenant_id: event.data.tenant_id, workspace: event.data.workspace });
        setError(null);
        const where = event.data.tenant_id ? ` (tenant ${event.data.tenant_id})` : event.data.workspace ? ` (${event.data.workspace})` : "";
        setResult(`Test passed. Signed in as ${event.data.email}${where}. You can save now.`);
      } else {
        setPassed(null);
        setResult(null);
        setError(event.data.error ?? "The test sign-in failed.");
      }
    }
    window.addEventListener("message", onMessage);
    return () => window.removeEventListener("message", onMessage);
  }, []);

  if (!data) return <p className="muted">Loading…</p>;

  const body: ProviderIn =
    choice === "oidc"
      ? { provider: "oidc", issuer: issuer.trim(), client_id: clientId.trim(), ...(secret ? { client_secret: secret } : {}) }
      : { provider: choice };
  const message = (err: Error, fallback: string) => (err instanceof ApiError ? err.message : fallback);
  const resetTest = () => {
    setPassed(null);
    setResult(null);
  };
  const edit = (set: (value: string) => void) => (e: ChangeEvent<HTMLInputElement>) => {
    set(e.target.value);
    resetTest();
  };

  const current =
    data.provider === "google" && data.google_workspace_domain
      ? `${PROVIDER_NAME.google} (${data.google_workspace_domain})`
      : PROVIDER_NAME[data.provider];

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
        <p className="section-hint">
          Now: <strong>{current}</strong>. Your organization's break-glass account always signs in with its own password and
          authenticator.
        </p>

        {!data.can_change ? (
          <p className="section-hint" style={{ margin: 0 }}>
            Only your organization's break-glass account can change this.
          </p>
        ) : (
          <form
            onSubmit={(e) => {
              e.preventDefault();
              if (!passed) return;
              const switching = data.provider !== choice || choice === "oidc";
              if (
                switching &&
                !window.confirm(
                  "Save this provider? Everyone signed in through a provider is signed out, and other local accounts stop working. The break-glass account stays signed in.",
                )
              )
                return;
              setError(null);
              save.mutate(
                { ...body, ticket: passed.ticket },
                {
                  onSuccess: () => {
                    setSecret("");
                    setPassed(null);
                    setResult("Saved. People now sign in with this provider.");
                  },
                  onError: (err) => setError(message(err, "couldn't save")),
                },
              );
            }}
            style={{ display: "grid", gap: "0.6rem" }}
          >
            <label className="field-label">
              Provider
              <select
                className="input"
                value={choice}
                onChange={(e) => {
                  setChoice(e.target.value as Choice);
                  resetTest();
                  setError(null);
                }}
              >
                {(["microsoft", "google", "oidc"] as const).map((p) => (
                  <option key={p} value={p} disabled={!data.available[p]}>
                    {p === "oidc" ? "Other OpenID Connect provider" : PROVIDER_NAME[p]}
                    {!data.available[p] ? " (not set up on this server)" : ""}
                  </option>
                ))}
              </select>
            </label>

            {choice === "microsoft" && (
              <p className="section-hint" style={{ margin: 0 }}>
                Test sign-in with a Microsoft work account of your organization. Its tenant becomes your organization's
                tenant; the account's email must be on one of your verified domains.
              </p>
            )}
            {choice === "google" && (
              <p className="section-hint" style={{ margin: 0 }}>
                Test sign-in with a Google Workspace account of your organization. Its Workspace becomes your organization's;
                the account's email must be on one of your verified domains.
              </p>
            )}
            {choice === "oidc" && (
              <>
                <p className="section-hint" style={{ margin: 0 }}>
                  Any OpenID Connect provider works (Authentik, Keycloak, Zitadel, Okta, ...). Register this app there with
                  the redirect URI below and the scopes <code>openid email profile</code>. People sign in with an email
                  address on one of your verified domains.
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
              </>
            )}

            {result && <div className="alert alert--good" style={{ margin: 0 }}>{result}</div>}
            {error && <div className="alert alert--critical" style={{ margin: 0 }}>{error}</div>}
            <div className="chip-row">
              <button
                type="button"
                className="btn btn--secondary btn--sm"
                disabled={startTest.isPending || (choice === "oidc" && (!issuer.trim() || !clientId.trim()))}
                onClick={() => {
                  setError(null);
                  resetTest();
                  startTest.mutate(body, {
                    onSuccess: ({ start_url }) => {
                      window.open(start_url, "yadt-sign-in-test", "width=520,height=680");
                    },
                    onError: (err) => setError(message(err, "couldn't start the test")),
                  });
                }}
              >
                {startTest.isPending ? "Starting…" : "Test sign-in"}
              </button>
              <button type="submit" className="btn btn--primary btn--sm" disabled={!passed || save.isPending}>
                Save
              </button>
              {data.provider !== "none" && (
                <button
                  type="button"
                  className="btn btn--ghost btn--sm"
                  disabled={remove.isPending}
                  onClick={() => {
                    if (window.confirm("Remove the sign-in provider? Everyone signed in through it is signed out; only local accounts remain."))
                      remove.mutate(undefined, { onError: (err) => setError(message(err, "couldn't remove it")) });
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
