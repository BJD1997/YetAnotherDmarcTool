import { useEffect, useState } from "react";
import { X, Copy, RefreshCw, Check, Plus, Trash2 } from "lucide-react";
import { ApiError } from "../../api/client";
import { useOnboardingStatus } from "../../hooks/useOnboarding";
import { useClipboardFeedback } from "../../hooks/useClipboardFeedback";
import { useRemoveHostedReportAddress, useRequestHostedReportAddress, useTlsRptPolicyBuilder } from "../../hooks/usePolicyBuilders";
import { sameAddress } from "./sameAddress";

const RUA_STATUS_TEXT: Record<string, { text: string; role: "good" | "warning" | "critical" | "neutral" }> = {
  correct: { text: "Reports are reaching your configured mailbox", role: "good" },
  points_elsewhere: { text: "rua= doesn't include your configured mailbox", role: "warning" },
  no_rua: { text: "No mailto: rua= address configured — reports won't reach your mailbox", role: "critical" },
  not_configured: { text: "No TLS-RPT record published yet", role: "warning" },
  lookup_error: { text: "Couldn't check right now — DNS lookup failed", role: "neutral" },
  no_mailbox: { text: "No mailbox connected yet — connect one in Settings first", role: "critical" },
};

// TLS-RPT's rua= (RFC 8460) allows both mailto: and https: destinations —
// unlike DMARC's, which is mailto:-only — so unlike PolicyBuilder.tsx's
// otherTargets (already mailto:-only at the source), this also has to
// track any https: endpoint already published, or regenerating the record
// would silently drop it.
function parseRuaUris(value: string | undefined): string[] {
  if (!value) return [];
  return value
    .split(",")
    .map((u) => u.trim())
    .filter(Boolean);
}

export default function TlsRptPolicyBuilder({ domainId, domainName, onClose }: { domainId: string; domainName: string; onClose: () => void }) {
  const { data, isLoading, refetch, isFetching } = useTlsRptPolicyBuilder(domainId);
  const { data: onboarding } = useOnboardingStatus();
  // Every org may use a hosted address, as long as this server can host one.
  const hostedMailboxAvailable = !!onboarding?.hosted_mailbox_ready;

  const { copied, copy } = useClipboardFeedback();
  const [selectedUris, setSelectedUris] = useState<string[]>([]);
  const [hostedAddress, setHostedAddress] = useState<string | null>(null);
  const [hostedAddressError, setHostedAddressError] = useState<string | null>(null);
  const [authWarning, setAuthWarning] = useState<string | null>(null);

  const requestHostedAddress = useRequestHostedReportAddress(
    domainId,
    (result) => {
      setHostedAddressError(null);
      setHostedAddress(result.hosted_report_address);
      // With an own mailbox as destination the hosted address is an
      // optional extra the user ticks; without one it is the destination.
      if (!data?.org_mailbox_address) {
        const uri = `mailto:${result.hosted_report_address}`;
        setSelectedUris((prev) => (prev.includes(uri) ? prev : [...prev, uri]));
      }
      const needsAttention = result.authorization_record_status === "unconfigured" || result.authorization_record_status === "error";
      setAuthWarning(needsAttention ? result.authorization_record_detail : null);
    },
    (err) => setHostedAddressError(err instanceof ApiError ? err.message : "couldn't generate a hosted address"),
  );

  const removeHostedAddress = useRemoveHostedReportAddress(
    domainId,
    () => {
      const removedUri = hostedAddress ? `mailto:${hostedAddress}` : null;
      setHostedAddressError(null);
      setAuthWarning(null);
      setHostedAddress(null);
      setSelectedUris((prev) => prev.filter((u) => !sameAddress(u, removedUri)));
    },
    (err) => setHostedAddressError(err instanceof ApiError ? err.message : "couldn't remove the hosted address"),
  );

  // Seed once, when data first loads: the destination (ticked), a hosted
  // address that isn't the destination only when already published, plus
  // whatever else is published (mailto: or https:).
  useEffect(() => {
    if (data) {
      const mailbox = data.org_mailbox_address;
      const currentUris = parseRuaUris(data.current_record?.tags.rua);
      const mailboxUri = mailbox ? `mailto:${mailbox}` : null;
      const hosted = data.hosted_report_address;
      const hostedUri = hosted ? `mailto:${hosted}` : null;
      const others = currentUris.filter((u) => !sameAddress(u, mailboxUri) && !sameAddress(u, hostedUri));
      const hostedPublished = currentUris.some((u) => sameAddress(u, hostedUri));
      const extraHosted = hostedUri && !sameAddress(hostedUri, mailboxUri) && hostedPublished ? [hostedUri] : [];
      setSelectedUris([...(mailboxUri ? [mailboxUri] : []), ...extraHosted, ...others]);
      setHostedAddress(data.hosted_report_address);
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

  const ruaInfo = data ? RUA_STATUS_TEXT[data.rua_destination.status] : null;
  const orgMailbox = data?.org_mailbox_address ?? null;
  const orgMailboxUri = orgMailbox ? `mailto:${orgMailbox}` : null;
  const otherUris = data
    ? parseRuaUris(data.current_record?.tags.rua).filter(
        (u) => !sameAddress(u, orgMailboxUri) && !sameAddress(u, data.hosted_report_address ? `mailto:${data.hosted_report_address}` : null),
      )
    : [];
  const generated = `v=TLSRPTv1; rua=${selectedUris.join(",")}`;

  function copyRecord() {
    void copy(generated);
  }

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal-card" onClick={(e) => e.stopPropagation()}>
        <div className="modal-header">
          <h2 style={{ margin: 0, fontSize: "1.1rem" }}>Build TLS-RPT record — {domainName}</h2>
          <button className="icon-btn" onClick={onClose} aria-label="Close">
            <X />
          </button>
        </div>
        {isLoading && <p className="muted">Loading…</p>}

        {data && (
          <>
            <div style={{ marginBottom: "1rem" }}>
              <div className="stat-tile-label" style={{ marginBottom: "0.3rem" }}>
                Report destination
              </div>
              {orgMailbox ? (
                <div className="chip-row">
                  <code>{orgMailbox}</code>
                  {ruaInfo && <span className={`badge badge--${ruaInfo.role}`}>{ruaInfo.text}</span>}
                </div>
              ) : hostedMailboxAvailable ? (
                <span className="badge badge--warning">No report destination yet — generate a hosted address below</span>
              ) : (
                <span className="badge badge--critical">No mailbox connected — connect one in Settings first</span>
              )}

              {otherUris.length > 0 && (
                <div style={{ marginTop: "0.6rem" }}>
                  <p className="section-hint" style={{ marginTop: 0, marginBottom: "0.3rem" }}>
                    Also currently published — keep or drop each:
                  </p>
                  <div style={{ display: "flex", flexDirection: "column", gap: "0.3rem" }}>
                    {otherUris.map((uri) => (
                      <label key={uri} style={{ display: "flex", alignItems: "center", gap: "0.4rem", fontSize: "0.85rem" }}>
                        <input
                          type="checkbox"
                          checked={selectedUris.includes(uri)}
                          onChange={(e) =>
                            setSelectedUris((prev) => (e.target.checked ? [...prev, uri] : prev.filter((u) => u !== uri)))
                          }
                        />
                        <code>{uri}</code>
                      </label>
                    ))}
                  </div>
                </div>
              )}

              {(hostedMailboxAvailable || hostedAddress) && (
                <div style={{ marginTop: "0.6rem" }}>
                  {hostedAddress && !sameAddress(hostedAddress, orgMailbox) ? (
                    <>
                      <label style={{ display: "flex", alignItems: "center", gap: "0.4rem", fontSize: "0.85rem" }}>
                        <input
                          type="checkbox"
                          checked={selectedUris.includes(`mailto:${hostedAddress}`)}
                          onChange={(e) =>
                            setSelectedUris((prev) =>
                              e.target.checked ? [...prev, `mailto:${hostedAddress}`] : prev.filter((u) => u !== `mailto:${hostedAddress}`),
                            )
                          }
                        />
                        <code>{hostedAddress}</code>
                        <span className="badge badge--good">hosted by YetAnotherDmarcTool</span>
                      </label>
                      {data.has_own_mailbox && (
                        <button
                          className="btn btn--ghost btn--sm"
                          style={{ marginTop: "0.3rem" }}
                          onClick={() => removeHostedAddress.mutate()}
                          disabled={removeHostedAddress.isPending}
                        >
                          <Trash2 />
                          Remove hosted address
                        </button>
                      )}
                    </>
                  ) : !hostedAddress && hostedMailboxAvailable ? (
                    <button
                      className="btn btn--ghost btn--sm"
                      onClick={() => requestHostedAddress.mutate()}
                      disabled={requestHostedAddress.isPending}
                    >
                      <Plus />
                      {orgMailbox ? "Also use a YetAnotherDmarcTool-hosted address" : "No mailbox of your own? Use a YetAnotherDmarcTool-hosted address"}
                    </button>
                  ) : null}
                  {hostedAddressError && (
                    <p className="section-hint" style={{ color: "var(--critical-text)", marginTop: "0.3rem", marginBottom: 0 }}>
                      {hostedAddressError}
                    </p>
                  )}
                  {authWarning && (
                    <p className="section-hint" style={{ color: "var(--warning-text)", marginTop: "0.3rem", marginBottom: 0 }}>
                      This address works, but couldn't be auto-authorized yet: {authWarning}
                    </p>
                  )}
                </div>
              )}
            </div>

            <div style={{ marginTop: "1.25rem", paddingTop: "1rem", borderTop: "1px solid var(--border)" }}>
              <div className="stat-tile-label" style={{ marginBottom: "0.4rem" }}>
                Generated record
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
                {selectedUris.length > 0 ? generated : <span className="muted">Add a report destination above first.</span>}
              </div>
              <div className="chip-row" style={{ marginTop: "0.6rem" }}>
                <button className="btn btn--primary btn--sm" onClick={copyRecord} disabled={selectedUris.length === 0}>
                  {copied ? <Check /> : <Copy />}
                  {copied ? "Copied" : "Copy DNS value"}
                </button>
                <button className="btn btn--ghost btn--sm" onClick={() => refetch()} disabled={isFetching}>
                  <RefreshCw />
                  {isFetching ? "Rechecking…" : "Recheck DNS"}
                </button>
              </div>
              <p className="section-hint" style={{ marginTop: "0.5rem", marginBottom: 0 }}>
                Publish this as a TXT record at <code>_smtp._tls.{domainName}</code>. DNS changes can take a few
                minutes to propagate — use "Recheck DNS" after publishing.
              </p>
            </div>

            {data.current_record && (
              <div style={{ marginTop: "1rem" }}>
                <div className="stat-tile-label" style={{ marginBottom: "0.3rem" }}>
                  Current record
                </div>
                <div className="muted" style={{ fontFamily: "monospace", fontSize: "0.78rem", wordBreak: "break-all" }}>
                  {data.current_record.raw}
                </div>
              </div>
            )}
            {data.current_record_lookup_error && (
              <p className="section-hint" style={{ marginTop: "0.5rem", color: "var(--warning-text)" }}>
                Couldn't look up the current record right now — try "Recheck DNS".
              </p>
            )}
          </>
        )}
      </div>
    </div>
  );
}
