import { useState } from "react";
import { Link } from "react-router-dom";
import { ChevronDown, ChevronRight, Pencil, Plus } from "lucide-react";
import type { Domain } from "../../api/types";
import type { SenderInventoryRow, SenderReviewStatus, SenderReviewUpdate, SenderSourceIp } from "../../api/overview";
import { useAuth } from "../../auth/AuthContext";
import { useSenderInventory, useUpdateSenderReview } from "../../hooks/useOverviewResources";
import { ServiceBadge, riskScore, passRateStyle } from "../domain/shared";

interface MergedRow extends SenderInventoryRow {
  domain_id: string;
  domain_name: string;
}

// Blocked senders that only ever sent a handful of messages are usually
// long-tail noise (scanners, one-off misconfigured hosts) — worth keeping
// out of the main scan, not worth deleting the record of.
const BLOCKED_LOW_VOLUME_THRESHOLD = 10;

// Recency window: senders/IPs with no traffic in this window drop out, so a
// decommissioned host (e.g. an old web01 replaced by web02) stops cluttering
// the list without any manual step. Defaults to a recent window; "All time"
// still shows everything.
const WINDOW_OPTIONS: { label: string; days: number | null }[] = [
  { label: "Last 30 days", days: 30 },
  { label: "Last 90 days", days: 90 },
  { label: "All time", days: null },
];
const DEFAULT_WINDOW_DAYS: number | null = 90;

type FilterKey = "all" | "failing" | "unknown" | "approved" | "needs_owner" | "spoofed" | "rdns" | "archived";

const FILTERS: { key: FilterKey; label: string }[] = [
  { key: "all", label: "All" },
  { key: "failing", label: "Failing" },
  { key: "unknown", label: "Unknown" },
  { key: "approved", label: "Approved" },
  { key: "needs_owner", label: "Needs owner" },
  { key: "spoofed", label: "Likely spoofed" },
  { key: "rdns", label: "rDNS issues" },
  { key: "archived", label: "Archived" },
];

function matchesFilter(row: MergedRow, filter: FilterKey): boolean {
  switch (filter) {
    case "all":
      return true;
    case "failing":
      return row.dmarc_pass_pct !== null && row.dmarc_pass_pct < 50;
    case "unknown":
      return row.status === "pending";
    case "approved":
      return row.status === "approved";
    case "needs_owner":
      return row.owner === null;
    case "spoofed":
      return row.likely_spoofed;
    case "rdns":
      // Only meaningful for senders you've claimed as your own — a spoofer with
      // no reverse DNS is expected, not a problem to fix.
      return row.status === "approved" && row.fcrdns_status !== "pass";
    case "archived":
      return row.status === "archived";
  }
}

export default function SenderInventory({ domainId, domains }: { domainId: string | null; domains: Domain[] }) {
  const { user } = useAuth();
  const canManage = user?.role === "org_admin";
  const [filter, setFilter] = useState<FilterKey>("all");
  const [showBlockedGroup, setShowBlockedGroup] = useState(false);
  const [windowDays, setWindowDays] = useState<number | null>(DEFAULT_WINDOW_DAYS);

  const targetDomains = domainId ? domains.filter((d) => d.id === domainId) : domains;
  const { data, isLoading } = useSenderInventory(targetDomains, windowDays, riskScore);
  const updateReview = useUpdateSenderReview();

  const allRows = data ?? [];
  // Archived senders are hidden everywhere except the explicit "Archived"
  // filter — that's the whole point of archiving one.
  const filteredRows = allRows.filter(
    (r) => matchesFilter(r, filter) && (filter === "archived" || r.status !== "archived"),
  );
  const rows = filteredRows.filter((r) => !(r.status === "blocked" && r.volume < BLOCKED_LOW_VOLUME_THRESHOLD));
  const collapsedBlockedRows = filteredRows.filter(
    (r) => r.status === "blocked" && r.volume < BLOCKED_LOW_VOLUME_THRESHOLD,
  );

  return (
    <div className="card">
      <div className="card-header" style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: "0.5rem" }}>
        <h3>Sender inventory</h3>
        <select
          className="input"
          style={{ padding: "0.25rem 0.5rem", fontSize: "0.8rem", width: "auto" }}
          value={windowDays ?? "all"}
          onChange={(e) => setWindowDays(e.target.value === "all" ? null : Number(e.target.value))}
          title="Senders with no traffic in this window drop off — retired hosts stop cluttering the list."
        >
          {WINDOW_OPTIONS.map((o) => (
            <option key={o.label} value={o.days ?? "all"}>
              {o.label}
            </option>
          ))}
        </select>
      </div>
      {!isLoading && allRows.length > 0 && (
        <div className="chip-row" style={{ marginBottom: "0.9rem" }}>
          {FILTERS.map((f) => (
            <button
              key={f.key}
              className="btn btn--ghost btn--sm"
              style={
                filter === f.key
                  ? { background: "var(--accent-wash)", color: "var(--accent)" }
                  : undefined
              }
              onClick={() => setFilter(f.key)}
            >
              {f.label}
            </button>
          ))}
        </div>
      )}
      {isLoading && <p className="muted">Loading…</p>}
      {!isLoading && allRows.length === 0 && <p className="empty-state">No senders observed in this range.</p>}
      {!isLoading && allRows.length > 0 && filteredRows.length === 0 && (
        <p className="empty-state">No senders match this filter.</p>
      )}
      {!isLoading && filteredRows.length > 0 && (
        <div className="table-wrap">
          <table className="table">
            <thead>
              <tr>
                <th>Service</th>
                {!domainId && <th>Domain</th>}
                <th>Volume</th>
                <th>DMARC pass</th>
                <th>Status</th>
                <th>Owner</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <SenderInventoryRowView
                  key={`${r.domain_id}:${r.service_label}`}
                  row={r}
                  showDomain={!domainId}
                  canManage={canManage}
                  onUpdate={(body) => updateReview.mutate({ domain_id: r.domain_id, service_label: r.service_label, body })}
                />
              ))}
              {collapsedBlockedRows.length > 0 && showBlockedGroup &&
                collapsedBlockedRows.map((r) => (
                  <SenderInventoryRowView
                    key={`${r.domain_id}:${r.service_label}`}
                    row={r}
                    showDomain={!domainId}
                    canManage={canManage}
                    onUpdate={(body) => updateReview.mutate({ domain_id: r.domain_id, service_label: r.service_label, body })}
                  />
                ))}
            </tbody>
          </table>
          {collapsedBlockedRows.length > 0 && (
            <button className="btn btn--ghost btn--sm" style={{ marginTop: "0.5rem" }} onClick={() => setShowBlockedGroup((v) => !v)}>
              {showBlockedGroup ? <ChevronDown /> : <ChevronRight />}
              {showBlockedGroup ? "Hide" : "Show"} blocked low-volume sources ({collapsedBlockedRows.length})
            </button>
          )}
        </div>
      )}
    </div>
  );
}

const STATUS_ROLE: Record<SenderReviewStatus, "good" | "warning" | "serious" | "critical" | "neutral"> = {
  pending: "warning",
  approved: "good",
  ignored: "neutral",
  blocked: "critical",
  archived: "neutral",
};

// Per-IP reverse-DNS detail shown in the expanded sender view: the PTR
// hostname, plus whether it forward-confirms back to the IP (FCrDNS).
function FcrdnsCell({ ip }: { ip: SenderSourceIp }) {
  if (ip.fcrdns_valid === null) {
    return (
      <span className="muted" title="This IP has no PTR (reverse DNS) record at all.">
        no PTR
      </span>
    );
  }
  return (
    <span style={{ display: "inline-flex", alignItems: "center", gap: "0.35rem", fontSize: "0.8rem" }}>
      <span style={{ fontFamily: "var(--font-mono, ui-monospace, monospace)", wordBreak: "break-all" }}>{ip.ptr_hostname}</span>
      {ip.fcrdns_valid === false && (
        <span
          className="badge badge--warning"
          title="This PTR hostname does not resolve back to this IP — reverse DNS is not forward-confirmed (FCrDNS)."
        >
          not confirmed
        </span>
      )}
    </span>
  );
}

// Per-address-family reverse-DNS status shown on an approved sender's row, so a
// clean IPv4 vs a broken IPv6 (the case Gmail/Microsoft actually junk) reads at
// a glance without expanding.
function FamilyRdnsBadge({ family, status }: { family: "IPv4" | "IPv6"; status: "pass" | "partial" | "fail" }) {
  const role = status === "pass" ? "good" : status === "fail" ? "serious" : "warning";
  const glyph = status === "pass" ? "✓" : status === "fail" ? "✗" : "⚠";
  const title =
    status === "pass"
      ? `Every ${family} sending IP has forward-confirmed reverse DNS.`
      : status === "fail"
        ? `No ${family} sending IP has forward-confirmed reverse DNS (PTR pointing back to the IP)${family === "IPv6" ? " — Gmail/Microsoft commonly junk or reject IPv6 mail without it." : "."}`
        : `Some ${family} sending IPs lack forward-confirmed reverse DNS.`;
  return (
    <span className={`badge badge--${role}`} style={{ marginLeft: "0.4rem" }} title={title}>
      {family} {glyph}
    </span>
  );
}

function SenderInventoryRowView({
  row,
  showDomain,
  canManage,
  onUpdate,
}: {
  row: MergedRow;
  showDomain: boolean;
  canManage: boolean;
  onUpdate: (body: Partial<Pick<SenderReviewUpdate, "status" | "owner">>) => void;
}) {
  const [expanded, setExpanded] = useState(false);
  const [editingStatus, setEditingStatus] = useState(false);
  const [editingOwner, setEditingOwner] = useState(false);
  const [ownerDraft, setOwnerDraft] = useState(row.owner ?? "");
  // Expandable for multi-IP grouping (as before) or whenever there's a reverse-DNS
  // issue worth inspecting per IP — including single-IP senders.
  const canExpand = row.source_ips.length > 1 || row.fcrdns_status !== "pass";
  const colSpan = 5 + (showDomain ? 1 : 0);

  return (
    <>
      <tr>
        <td>
          <div style={{ display: "flex", alignItems: "center" }}>
            {canExpand ? (
              <button
                className="icon-btn"
                style={{ width: 20, height: 20, marginRight: "0.15rem" }}
                onClick={() => setExpanded((v) => !v)}
                aria-label={expanded ? "Collapse" : "Expand"}
              >
                {expanded ? <ChevronDown style={{ width: 14, height: 14 }} /> : <ChevronRight style={{ width: 14, height: 14 }} />}
              </button>
            ) : (
              <span style={{ width: 20, display: "inline-block", flexShrink: 0 }} />
            )}
            <ServiceBadge label={row.service_label} />
            {row.service_label}
            {row.source_ip_count > 1 && (
              <span className="muted" style={{ fontSize: "0.8rem" }}>
                {" "}
                ({row.source_ip_count} IPs)
              </span>
            )}
            {row.likely_spoofed && row.status === "pending" && (
              <span className="badge badge--critical" style={{ marginLeft: "0.4rem" }}>
                likely spoofed
              </span>
            )}
            {row.status === "approved" && row.fcrdns_status !== "pass" && (
              <>
                {row.fcrdns_status_v4 !== null && <FamilyRdnsBadge family="IPv4" status={row.fcrdns_status_v4} />}
                {row.fcrdns_status_v6 !== null && <FamilyRdnsBadge family="IPv6" status={row.fcrdns_status_v6} />}
              </>
            )}
            {row.sends_ipv6 && !(row.status === "approved" && row.fcrdns_status !== "pass") && (
              <span
                className="badge badge--neutral"
                style={{ marginLeft: "0.4rem" }}
                title="This sender uses IPv6. IPv6 senders face stricter deliverability rules (a valid PTR and, for Gmail/Microsoft, DKIM are effectively required)."
              >
                IPv6
              </span>
            )}
          </div>
        </td>
        {showDomain && (
          <td>
            <Link to={`/domains/${row.domain_id}`}>{row.domain_name}</Link>
          </td>
        )}
        <td className="num">{row.volume.toLocaleString()}</td>
        <td className="num">
          <span style={passRateStyle(row.dmarc_pass_pct)}>{row.dmarc_pass_pct === null ? "—" : `${row.dmarc_pass_pct}%`}</span>
        </td>
        <td>
          {canManage && editingStatus ? (
            <select
              className="input"
              autoFocus
              style={{ padding: "0.2rem 0.4rem", fontSize: "0.78rem" }}
              value={row.status}
              onChange={(e) => {
                onUpdate({ status: e.target.value as SenderReviewStatus });
                setEditingStatus(false);
              }}
              onBlur={() => setEditingStatus(false)}
            >
              <option value="pending">pending</option>
              <option value="approved">approved</option>
              <option value="ignored">ignored</option>
              <option value="blocked">blocked</option>
              <option value="archived">archived</option>
            </select>
          ) : (
            <span style={{ display: "inline-flex", alignItems: "center", gap: "0.3rem" }}>
              <span className={`badge badge--${STATUS_ROLE[row.status]}`}>{row.status}</span>
              {canManage && (
                <button
                  className="icon-btn"
                  style={{ width: 20, height: 20 }}
                  onClick={() => setEditingStatus(true)}
                  aria-label="Change status"
                >
                  <Pencil style={{ width: 12, height: 12 }} />
                </button>
              )}
            </span>
          )}
        </td>
        <td>
          {canManage && editingOwner ? (
            <input
              className="input"
              style={{ padding: "0.2rem 0.4rem", fontSize: "0.78rem", width: "120px" }}
              value={ownerDraft}
              autoFocus
              onChange={(e) => setOwnerDraft(e.target.value)}
              onBlur={() => {
                setEditingOwner(false);
                if (ownerDraft !== (row.owner ?? "")) onUpdate({ owner: ownerDraft || null });
              }}
              onKeyDown={(e) => {
                if (e.key === "Enter") e.currentTarget.blur();
              }}
            />
          ) : row.owner ? (
            <span
              className="badge badge--neutral"
              style={{ cursor: canManage ? "pointer" : undefined, textTransform: "none" }}
              onClick={() => canManage && setEditingOwner(true)}
            >
              {row.owner}
            </span>
          ) : canManage ? (
            <button className="icon-btn" style={{ width: 22, height: 22 }} onClick={() => setEditingOwner(true)} aria-label="Set owner">
              <Plus style={{ width: 14, height: 14 }} />
            </button>
          ) : (
            <span className="muted">—</span>
          )}
        </td>
      </tr>
      {expanded && (
        <tr>
          <td colSpan={colSpan} style={{ padding: "0 0 0.75rem 2.1rem", borderBottom: "1px solid var(--border)" }}>
            <table className="table" style={{ width: "100%" }}>
              <thead>
                <tr>
                  <th>Source IP</th>
                  <th>Reverse DNS</th>
                  <th>Volume</th>
                  <th>SPF aligned</th>
                  <th>DKIM aligned</th>
                  <th>Accepted</th>
                  <th>Quarantined</th>
                  <th>Rejected</th>
                </tr>
              </thead>
              <tbody>
                {row.source_ips.map((ip) => (
                  <tr key={ip.source_ip}>
                    <td>
                      <a href={`https://ipinfo.io/${ip.source_ip}`} target="_blank" rel="noreferrer">
                        {ip.source_ip}
                      </a>
                      {ip.is_ipv6 && (
                        <span className="badge badge--neutral" style={{ marginLeft: "0.35rem" }} title="IPv6 source">
                          v6
                        </span>
                      )}
                    </td>
                    <td>
                      <FcrdnsCell ip={ip} />
                    </td>
                    <td className="num">{ip.volume.toLocaleString()}</td>
                    <td className="num">{ip.spf_aligned_pct === null ? "—" : `${ip.spf_aligned_pct}%`}</td>
                    <td className="num">{ip.dkim_aligned_pct === null ? "—" : `${ip.dkim_aligned_pct}%`}</td>
                    <td className="num">{ip.accepted}</td>
                    <td className="num" style={{ color: ip.quarantined > 0 ? "var(--warning-text)" : undefined }}>
                      {ip.quarantined}
                    </td>
                    <td className="num" style={{ color: ip.rejected > 0 ? "var(--critical-text)" : undefined }}>
                      {ip.rejected}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </td>
        </tr>
      )}
    </>
  );
}
