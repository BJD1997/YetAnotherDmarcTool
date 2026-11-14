import { useEffect, useState } from "react";
import { X, Copy, RefreshCw, Check } from "lucide-react";
import { useOnboardingStatus } from "../../hooks/useOnboarding";
import { useClipboardFeedback } from "../../hooks/useClipboardFeedback";
import { useTlsRptPolicyBuilder } from "../../hooks/usePolicyBuilders";
import HostedAddressRow from "./HostedAddressRow";
import { sameAddress } from "./sameAddress";
import { useHostedAddress } from "./useHostedAddress";

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
  const hostedState = useHostedAddress(
    domainId,
    data?.org_mailbox_address,
    (address) => {
      const uri = `mailto:${address}`;
      setSelectedUris((prev) => (prev.includes(uri) ? prev : [...prev, uri]));
    },
    (removed) => setSelectedUris((prev) => prev.filter((u) => !sameAddress(u, `mailto:${removed}`))),
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
      hostedState.setHostedAddress(data.hosted_report_address);
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
  const hostedUri = data?.hosted_report_address ? `mailto:${data.hosted_report_address}` : null;
  const otherUris = data
    ? parseRuaUris(data.current_record?.tags.rua).filter(
        (u) => !sameAddress(u, orgMailboxUri) && !sameAddress(u, hostedUri),
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

              <HostedAddressRow
                hostedAddress={hostedState.hostedAddress}
                destination={orgMailbox}
                hostedMailboxAvailable={hostedMailboxAvailable}
                checked={!!hostedState.hostedAddress && selectedUris.includes(`mailto:${hostedState.hostedAddress}`)}
                onToggle={(on) => {
                  const uri = `mailto:${hostedState.hostedAddress}`;
                  setSelectedUris((prev) => (on ? [...prev, uri] : prev.filter((u) => u !== uri)));
                }}
                canRemove={data.has_own_mailbox}
                onRemove={() => hostedState.remove.mutate()}
                removePending={hostedState.remove.isPending}
                onRequest={() => hostedState.request.mutate()}
                requestPending={hostedState.request.isPending}
                error={hostedState.error}
                authWarning={hostedState.authWarning}
              />
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
