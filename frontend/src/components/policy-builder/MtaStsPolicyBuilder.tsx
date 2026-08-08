import { useEffect, useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { X, Copy, Check } from "lucide-react";
import { api } from "../../api/client";
import type { MtaStsBuilderData, MtaStsMode } from "../../api/dnsChecks";

// RFC 8461 §4.1: a leading "*." wildcard matches exactly one label, not
// "one or more" — *.mx.microsoft covers foo.mx.microsoft but NOT
// foo.bar.mx.microsoft. Ported from the (now-fixed) backend _mx_covered in
// app/services/dns_checks/mta_sts.py so this can update live as the user
// types, instead of only catching a wrong pattern after publishing — the
// exact class of bug found on davids.online this session.
function mxCovered(mxHost: string, patterns: string[]): boolean {
  const host = mxHost.replace(/\.$/, "").toLowerCase();
  for (const raw of patterns) {
    const pattern = raw.replace(/\.$/, "").toLowerCase();
    if (pattern.startsWith("*.")) {
      const suffix = pattern.slice(2);
      if (suffix && host.endsWith("." + suffix)) {
        const remainder = host.slice(0, host.length - suffix.length - 1);
        if (remainder && !remainder.includes(".")) return true;
      }
    } else if (host === pattern) {
      return true;
    }
  }
  return false;
}

function generatePolicyId(): string {
  const now = new Date();
  const pad = (n: number) => String(n).padStart(2, "0");
  return (
    `${now.getUTCFullYear()}${pad(now.getUTCMonth() + 1)}${pad(now.getUTCDate())}` +
    `${pad(now.getUTCHours())}${pad(now.getUTCMinutes())}${pad(now.getUTCSeconds())}Z`
  );
}

const MODES: { key: MtaStsMode; label: string }[] = [
  { key: "none", label: "None" },
  { key: "testing", label: "Testing" },
  { key: "enforce", label: "Enforce" },
];

export default function MtaStsPolicyBuilder({
  domainId,
  domainName,
  onClose,
}: {
  domainId: string;
  domainName: string;
  onClose: () => void;
}) {
  const { data, isLoading } = useQuery({
    queryKey: ["mta-sts-builder", domainId],
    queryFn: () => api.get<MtaStsBuilderData>(`/domains/${domainId}/dns/mta-sts-builder`),
  });

  const [mode, setMode] = useState<MtaStsMode>("testing");
  const [mxPatternsText, setMxPatternsText] = useState("");
  const [copiedTxt, setCopiedTxt] = useState(false);
  const [copiedPolicy, setCopiedPolicy] = useState(false);

  // Seed once, when data first loads. If the domain already has a policy
  // that fully covers its real MX hosts, keep editing that (no reason to
  // discard a working wildcard for a more verbose exact-match list) —
  // otherwise default to an exact match per real MX host, which is always
  // structurally correct under RFC 8461's single-label wildcard rule.
  useEffect(() => {
    if (data) {
      setMode(data.recommended_mode);
      const current = data.current_policy?.mx_patterns ?? [];
      const currentCoversAll = current.length > 0 && data.mx_hosts.every((h) => mxCovered(h, current));
      setMxPatternsText((currentCoversAll ? current : data.mx_hosts).join("\n"));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [data === undefined]);

  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") onClose();
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const mxPatterns = mxPatternsText
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean);

  const policyId = useMemo(() => generatePolicyId(), [mode, mxPatternsText]);
  const txtRecord = `v=STSv1; id=${policyId}`;
  const policyFile = ["version: STSv1", `mode: ${mode}`, ...mxPatterns.map((p) => `mx: ${p}`), "max_age: 604800"].join("\n");

  function copy(text: string, setCopied: (v: boolean) => void) {
    navigator.clipboard.writeText(text).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    });
  }

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal-card" onClick={(e) => e.stopPropagation()}>
        <div className="modal-header">
          <h2 style={{ margin: 0, fontSize: "1.1rem" }}>Build MTA-STS policy — {domainName}</h2>
          <button className="icon-btn" onClick={onClose} aria-label="Close">
            <X />
          </button>
        </div>

        {isLoading && <p className="muted">Loading…</p>}

        {data && (
          <>
            <div style={{ marginBottom: "1rem" }}>
              <div className="stat-tile-label" style={{ marginBottom: "0.3rem" }}>
                Real MX hosts
              </div>
              {data.mx_hosts.length === 0 ? (
                <p className="section-hint" style={{ margin: 0 }}>No MX records found — nothing for a policy to protect.</p>
              ) : (
                <div style={{ display: "flex", flexDirection: "column", gap: "0.3rem" }}>
                  {data.mx_hosts.map((host) => {
                    const covered = mxCovered(host, mxPatterns);
                    return (
                      <div key={host} className="chip-row" style={{ gap: "0.5rem" }}>
                        <code style={{ fontSize: "0.82rem" }}>{host}</code>
                        <span className={`badge badge--${covered ? "good" : "critical"}`}>
                          {covered ? "Covered" : "Not covered"}
                        </span>
                      </div>
                    );
                  })}
                </div>
              )}
              <p className="section-hint" style={{ marginTop: "0.4rem", marginBottom: 0 }}>
                {data.current_txt ? <>Current TXT: <code>{data.current_txt}</code></> : "No MTA-STS TXT record found yet."}
              </p>
              {data.current_policy_fetch_error && (
                <p className="section-hint" style={{ marginTop: "0.2rem", marginBottom: 0, color: "var(--warning-text)" }}>
                  Couldn't fetch the current policy file: {data.current_policy_fetch_error}
                </p>
              )}
            </div>

            <div style={{ marginTop: "1rem" }}>
              <div className="stat-tile-label" style={{ marginBottom: "0.4rem" }}>
                Mode
              </div>
              <div className="chip-row">
                {MODES.map((m) => (
                  <button
                    key={m.key}
                    className="btn btn--secondary btn--sm"
                    style={
                      mode === m.key
                        ? { background: "var(--accent-wash)", color: "var(--accent)", borderColor: "var(--accent)" }
                        : undefined
                    }
                    onClick={() => setMode(m.key)}
                  >
                    {m.label}
                  </button>
                ))}
              </div>
              <p className="section-hint" style={{ marginTop: "0.4rem", marginBottom: 0 }}>
                Start at "testing" to confirm the policy doesn't block legitimate mail before moving to "enforce".
              </p>
            </div>

            <div style={{ marginTop: "1rem" }}>
              <div className="stat-tile-label" style={{ marginBottom: "0.4rem" }}>
                mx: patterns <span className="muted" style={{ textTransform: "none", fontWeight: 400 }}>(one per line)</span>
              </div>
              <p className="section-hint" style={{ marginTop: 0, marginBottom: "0.4rem" }}>
                Pre-filled with an exact match per real MX host — always correct. A <code>*.</code> wildcard only
                covers exactly one label (<code>*.example.com</code> matches <code>foo.example.com</code>, not{" "}
                <code>foo.bar.example.com</code>) — check the coverage badges above before relying on one.
              </p>
              <textarea
                className="input"
                style={{ width: "100%", minHeight: "80px", fontFamily: "monospace", fontSize: "0.82rem" }}
                value={mxPatternsText}
                onChange={(e) => setMxPatternsText(e.target.value)}
              />
            </div>

            <div style={{ marginTop: "1.25rem", paddingTop: "1rem", borderTop: "1px solid var(--border)" }}>
              <div className="stat-tile-label" style={{ marginBottom: "0.4rem" }}>
                1. DNS TXT record
              </div>
              <div
                style={{
                  fontFamily: "monospace",
                  fontSize: "0.82rem",
                  background: "var(--plane)",
                  border: "1px solid var(--border)",
                  borderRadius: "var(--radius-sm)",
                  padding: "0.6rem 0.75rem",
                  wordBreak: "break-all",
                }}
              >
                {txtRecord}
              </div>
              <div className="chip-row" style={{ marginTop: "0.6rem" }}>
                <button className="btn btn--primary btn--sm" onClick={() => copy(txtRecord, setCopiedTxt)}>
                  {copiedTxt ? <Check /> : <Copy />}
                  {copiedTxt ? "Copied" : "Copy DNS value"}
                </button>
              </div>
              <p className="section-hint" style={{ marginTop: "0.5rem", marginBottom: 0 }}>
                Publish this as a TXT record at <code>_mta-sts.{domainName}</code>.
              </p>
            </div>

            <div style={{ marginTop: "1.1rem" }}>
              <div className="stat-tile-label" style={{ marginBottom: "0.4rem" }}>
                2. Policy file
              </div>
              <div
                style={{
                  fontFamily: "monospace",
                  fontSize: "0.82rem",
                  background: "var(--plane)",
                  border: "1px solid var(--border)",
                  borderRadius: "var(--radius-sm)",
                  padding: "0.6rem 0.75rem",
                  whiteSpace: "pre-wrap",
                  wordBreak: "break-all",
                }}
              >
                {policyFile}
              </div>
              <div className="chip-row" style={{ marginTop: "0.6rem" }}>
                <button className="btn btn--primary btn--sm" onClick={() => copy(policyFile, setCopiedPolicy)}>
                  {copiedPolicy ? <Check /> : <Copy />}
                  {copiedPolicy ? "Copied" : "Copy policy file"}
                </button>
              </div>
              <p className="section-hint" style={{ marginTop: "0.5rem", marginBottom: 0 }}>
                Host this exact content at <code>https://mta-sts.{domainName}/.well-known/mta-sts.txt</code> over
                valid HTTPS — this app doesn't host it for you, unlike the DNS record above.
              </p>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
