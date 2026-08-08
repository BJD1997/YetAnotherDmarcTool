import { useEffect, useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { X, Copy, RefreshCw, Check, Plus } from "lucide-react";
import { api, ApiError } from "../../api/client";
import type { DmarcPolicy, PolicyBuilderData } from "../../api/policyBuilder";
import type { Organization } from "../../api/types";

const RUA_STATUS_TEXT: Record<string, { text: string; role: "good" | "warning" | "critical" | "neutral" }> = {
  correct: { text: "Reports are reaching your connected mailbox", role: "good" },
  points_elsewhere: { text: "rua= points somewhere other than your connected mailbox", role: "warning" },
  no_rua: { text: "No rua= address configured — no reports will arrive", role: "critical" },
  not_configured: { text: "No DMARC record published yet", role: "warning" },
  lookup_error: { text: "Couldn't check right now — DNS lookup failed", role: "neutral" },
  no_mailbox: { text: "No mailbox connected yet — connect one in Settings first", role: "critical" },
};

// Deliberately no pct= anywhere in this builder — RFC 9989 (DMARCbis)
// Appendix A.6 removes it entirely, and dmarc_record.py's own checker
// already flags a published pct= as deprecated. Staged rollout is handled
// by gating each policy move on pass-rate/stability/unreviewed-senders
// (see the recommendation itself) instead of a partial-enforcement tag.
// np= IS included — it's DMARCbis-native (RFC 9989), not deprecated: the
// policy for subdomains that don't exist in DNS at all, distinct from sp=
// (subdomains that exist but lack their own DMARC record).
function buildRecord(opts: {
  policy: DmarcPolicy;
  sp: DmarcPolicy | "";
  np: DmarcPolicy | "";
  adkim: "r" | "s";
  aspf: "r" | "s";
  rua: string[];
}): string {
  const parts = ["v=DMARC1", `p=${opts.policy}`];
  if (opts.sp) parts.push(`sp=${opts.sp}`);
  if (opts.np) parts.push(`np=${opts.np}`);
  parts.push(`adkim=${opts.adkim}`, `aspf=${opts.aspf}`);
  if (opts.rua.length > 0) parts.push(`rua=${opts.rua.map((a) => `mailto:${a}`).join(",")}`);
  return parts.join("; ");
}

export default function PolicyBuilder({ domainId, domainName, onClose }: { domainId: string; domainName: string; onClose: () => void }) {
  const { data, isLoading, refetch, isFetching } = useQuery({
    queryKey: ["policy-builder", domainId],
    queryFn: () => api.get<PolicyBuilderData>(`/domains/${domainId}/dmarc/policy-builder`),
  });
  const { data: org } = useQuery({
    queryKey: ["organization", "current"],
    queryFn: () => api.get<Organization>("/organizations/current"),
  });
  const hostedMailboxAvailable = !org?.entra_tenant_id || org?.hosted_mailbox_opt_in;

  const [policy, setPolicy] = useState<DmarcPolicy>("none");
  const [sp, setSp] = useState<DmarcPolicy | "">("");
  const [np, setNp] = useState<DmarcPolicy | "">("");
  const [advanced, setAdvanced] = useState(false);
  const [adkim, setAdkim] = useState<"r" | "s">("r");
  const [aspf, setAspf] = useState<"r" | "s">("r");
  const [copied, setCopied] = useState(false);
  // Which rua= mailto addresses end up in the generated record — the org
  // mailbox plus whichever of the domain's currently-published addresses
  // the user hasn't unchecked. Without this, regenerating the record would
  // silently drop any other legitimate destination (a prior DMARC vendor,
  // a compliance mailbox) the domain already had configured.
  const [selectedRua, setSelectedRua] = useState<string[]>([]);
  const [hostedAddress, setHostedAddress] = useState<string | null>(null);
  const [hostedAddressError, setHostedAddressError] = useState<string | null>(null);
  // Set only when the address itself was generated fine but the RFC 7489
  // §7.1 authorization record on the hosted domain couldn't be
  // auto-provisioned (Cloudflare unconfigured/erroring) — distinct from
  // hostedAddressError, since the address is still usable, just not yet
  // authorized to receive reports.
  const [authWarning, setAuthWarning] = useState<string | null>(null);

  const requestHostedAddress = useMutation({
    mutationFn: () =>
      api.post<{
        hosted_report_address: string;
        authorization_record_status: "created" | "already_exists" | "unconfigured" | "error";
        authorization_record_detail: string | null;
      }>(`/domains/${domainId}/hosted-report-address`),
    onSuccess: (result) => {
      setHostedAddressError(null);
      setHostedAddress(result.hosted_report_address);
      setSelectedRua((prev) => (prev.includes(result.hosted_report_address) ? prev : [...prev, result.hosted_report_address]));
      const needsAttention = result.authorization_record_status === "unconfigured" || result.authorization_record_status === "error";
      setAuthWarning(needsAttention ? result.authorization_record_detail : null);
    },
    onError: (err) => setHostedAddressError(err instanceof ApiError ? err.message : "couldn't generate a hosted address"),
  });

  // Seed the form from the recommendation exactly once, when it first loads.
  useEffect(() => {
    if (data) {
      setPolicy(data.recommendation.policy);
      setNp(data.recommendation.np ?? "");
      const org = data.org_mailbox_address;
      const others = data.rua_destination.current_targets.filter((t) => !org || t.toLowerCase() !== org.toLowerCase());
      setSelectedRua(org ? [org, ...others] : others);
      // A hosted address may already exist for this domain (generated
      // during onboarding, or on a previous visit here) — without this,
      // the "no mailbox of your own?" button below would re-offer to
      // generate one every time, ignoring that it already has.
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

  const generated = data ? buildRecord({ policy, sp, np, adkim, aspf, rua: selectedRua }) : "";
  const ruaInfo = data ? RUA_STATUS_TEXT[data.rua_destination.status] : null;
  const currentHasPct = !!data?.current_record?.tags.pct;
  const orgMailbox = data?.org_mailbox_address ?? null;
  const otherTargets = data
    ? data.rua_destination.current_targets.filter((t) => !orgMailbox || t.toLowerCase() !== orgMailbox.toLowerCase())
    : [];

  function copyRecord() {
    navigator.clipboard.writeText(generated).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    });
  }

  function useRecommendation() {
    if (!data) return;
    setPolicy(data.recommendation.policy);
    setNp(data.recommendation.np ?? "");
  }

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal-card" onClick={(e) => e.stopPropagation()}>
        <div className="modal-header">
          <h2 style={{ margin: 0, fontSize: "1.1rem" }}>Build DMARC policy — {domainName}</h2>
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
              {data.org_mailbox_address ? (
                <div className="chip-row">
                  <code>{data.org_mailbox_address}</code>
                  {ruaInfo && <span className={`badge badge--${ruaInfo.role}`}>{ruaInfo.text}</span>}
                </div>
              ) : hostedMailboxAvailable ? (
                <span className="badge badge--warning">No report destination yet — generate a hosted address below</span>
              ) : (
                <span className="badge badge--critical">No mailbox connected — connect one in Settings first</span>
              )}
              {otherTargets.length > 0 && (
                <div style={{ marginTop: "0.6rem" }}>
                  <p className="section-hint" style={{ marginTop: 0, marginBottom: "0.3rem" }}>
                    Also currently published — keep or drop each:
                  </p>
                  <div style={{ display: "flex", flexDirection: "column", gap: "0.3rem" }}>
                    {otherTargets.map((addr) => (
                      <label key={addr} style={{ display: "flex", alignItems: "center", gap: "0.4rem", fontSize: "0.85rem" }}>
                        <input
                          type="checkbox"
                          checked={selectedRua.includes(addr)}
                          onChange={(e) =>
                            setSelectedRua((prev) => (e.target.checked ? [...prev, addr] : prev.filter((a) => a !== addr)))
                          }
                        />
                        <code>{addr}</code>
                      </label>
                    ))}
                  </div>
                </div>
              )}
              {hostedMailboxAvailable && (
                <div style={{ marginTop: "0.6rem" }}>
                  {hostedAddress ? (
                    <div className="chip-row">
                      <code>{hostedAddress}</code>
                      <span className="badge badge--good">hosted by YetAnotherDmarcTool</span>
                    </div>
                  ) : (
                    <button
                      className="btn btn--ghost btn--sm"
                      onClick={() => requestHostedAddress.mutate()}
                      disabled={requestHostedAddress.isPending}
                    >
                      <Plus />
                      No mailbox of your own? Use a YetAnotherDmarcTool-hosted address
                    </button>
                  )}
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

            {currentHasPct && (
              <div className="alert alert--warning">
                Your current record publishes <code>pct={data.current_record?.tags.pct}</code>, which is deprecated
                — the record generated below drops it.
              </div>
            )}

            <div className={`alert alert--${data.recommendation.blocked ? "warning" : "good"}`}>
              <div style={{ fontWeight: 600 }}>
                Recommendation: p={data.recommendation.policy}
                {data.recommendation.np && ` np=${data.recommendation.np}`}
              </div>
              <div style={{ marginTop: "0.25rem" }}>{data.recommendation.reasoning}</div>
              {data.recommendation.blocking_reason && (
                <div style={{ marginTop: "0.25rem", fontWeight: 500 }}>{data.recommendation.blocking_reason}</div>
              )}
              <button className="btn btn--secondary btn--sm" style={{ marginTop: "0.6rem" }} onClick={useRecommendation}>
                Use recommendation
              </button>
            </div>

            <div style={{ marginTop: "1.1rem" }}>
              <div className="stat-tile-label" style={{ marginBottom: "0.4rem" }}>
                Enforcement
              </div>
              <div className="chip-row">
                {(["none", "quarantine", "reject"] as DmarcPolicy[]).map((p) => (
                  <button
                    key={p}
                    className="btn btn--secondary btn--sm"
                    style={policy === p ? { background: "var(--accent-wash)", color: "var(--accent)", borderColor: "var(--accent)" } : undefined}
                    onClick={() => setPolicy(p)}
                  >
                    {p === "none" ? "Monitor only" : p === "quarantine" ? "Quarantine" : "Reject"}
                  </button>
                ))}
              </div>
            </div>

            <div style={{ marginTop: "1rem" }}>
              <div className="stat-tile-label" style={{ marginBottom: "0.4rem" }}>
                Subdomain policy
              </div>
              <p className="section-hint" style={{ marginTop: 0, marginBottom: "0.4rem" }}>
                For subdomains that exist but publish no DMARC record of their own.
              </p>
              <select className="input" value={sp} onChange={(e) => setSp(e.target.value as DmarcPolicy | "")}>
                <option value="">Same as main domain</option>
                <option value="none">Monitor only</option>
                <option value="quarantine">Quarantine</option>
                <option value="reject">Reject</option>
              </select>
            </div>

            <div style={{ marginTop: "1rem" }}>
              <div className="stat-tile-label" style={{ marginBottom: "0.4rem" }}>
                Non-existent subdomain policy <span className="muted" style={{ textTransform: "none", fontWeight: 400 }}>(np=)</span>
              </div>
              <p className="section-hint" style={{ marginTop: 0, marginBottom: "0.4rem" }}>
                For subdomains with no DNS presence at all — these can never have a legitimate sender, so rejecting
                them is safe even before your main policy is fully enforcing.
              </p>
              <select className="input" value={np} onChange={(e) => setNp(e.target.value as DmarcPolicy | "")}>
                <option value="">Same as subdomain policy</option>
                <option value="none">Monitor only</option>
                <option value="quarantine">Quarantine</option>
                <option value="reject">Reject</option>
              </select>
            </div>

            <div style={{ marginTop: "1rem" }}>
              <button className="btn btn--ghost btn--sm" onClick={() => setAdvanced((v) => !v)}>
                {advanced ? "Hide" : "Show"} advanced alignment settings
              </button>
              {advanced && (
                <div className="chip-row" style={{ marginTop: "0.5rem" }}>
                  <label className="muted" style={{ fontSize: "0.85rem", display: "flex", alignItems: "center", gap: "0.4rem" }}>
                    DKIM
                    <select className="input" value={adkim} onChange={(e) => setAdkim(e.target.value as "r" | "s")}>
                      <option value="r">Relaxed (recommended)</option>
                      <option value="s">Strict</option>
                    </select>
                  </label>
                  <label className="muted" style={{ fontSize: "0.85rem", display: "flex", alignItems: "center", gap: "0.4rem" }}>
                    SPF
                    <select className="input" value={aspf} onChange={(e) => setAspf(e.target.value as "r" | "s")}>
                      <option value="r">Relaxed (recommended)</option>
                      <option value="s">Strict</option>
                    </select>
                  </label>
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
                {generated}
              </div>
              <div className="chip-row" style={{ marginTop: "0.6rem" }}>
                <button className="btn btn--primary btn--sm" onClick={copyRecord}>
                  {copied ? <Check /> : <Copy />}
                  {copied ? "Copied" : "Copy DNS value"}
                </button>
                <button className="btn btn--ghost btn--sm" onClick={() => refetch()} disabled={isFetching}>
                  <RefreshCw />
                  {isFetching ? "Rechecking…" : "Recheck DNS"}
                </button>
              </div>
              <p className="section-hint" style={{ marginTop: "0.5rem", marginBottom: 0 }}>
                Publish this as a TXT record at <code>_dmarc.{domainName}</code>. DNS changes can take a few minutes
                to propagate — use "Recheck DNS" after publishing.
              </p>
            </div>

            {data.current_record && (
              <div style={{ marginTop: "1rem" }}>
                <div className="stat-tile-label" style={{ marginBottom: "0.3rem" }}>
                  Current record
                </div>
                <div
                  className="muted"
                  style={{
                    fontFamily: "monospace",
                    fontSize: "0.78rem",
                    wordBreak: "break-all",
                  }}
                >
                  {data.current_record.raw}
                </div>
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
}
