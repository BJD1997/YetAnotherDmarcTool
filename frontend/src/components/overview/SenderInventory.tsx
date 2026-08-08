import { useState } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ChevronDown, ChevronRight, Pencil, Plus } from "lucide-react";
import { api } from "../../api/client";
import type { Domain } from "../../api/types";
import type { SenderInventoryRow, SenderReviewStatus, SenderReviewUpdate } from "../../api/overview";
import { useAuth } from "../../auth/AuthContext";
import { ServiceBadge, riskScore, passRateStyle } from "../domain/shared";

interface MergedRow extends SenderInventoryRow {
  domain_id: string;
  domain_name: string;
}

// Blocked senders that only ever sent a handful of messages are usually
// long-tail noise (scanners, one-off misconfigured hosts) — worth keeping
// out of the main scan, not worth deleting the record of.
const BLOCKED_LOW_VOLUME_THRESHOLD = 10;

async function fetchDomainInventory(domain: Domain): Promise<MergedRow[]> {
  const rows = await api.get<SenderInventoryRow[]>(`/domains/${domain.id}/dmarc/sender-inventory`);
  return rows.map((r) => ({ ...r, domain_id: domain.id, domain_name: domain.name }));
}

type FilterKey = "all" | "failing" | "unknown" | "approved" | "needs_owner" | "spoofed";

const FILTERS: { key: FilterKey; label: string }[] = [
  { key: "all", label: "All" },
  { key: "failing", label: "Failing" },
  { key: "unknown", label: "Unknown" },
  { key: "approved", label: "Approved" },
  { key: "needs_owner", label: "Needs owner" },
  { key: "spoofed", label: "Likely spoofed" },
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
  }
}

export default function SenderInventory({ domainId, domains }: { domainId: string | null; domains: Domain[] }) {
  const { user } = useAuth();
  const canManage = user?.role === "org_admin";
  const queryClient = useQueryClient();
  const [filter, setFilter] = useState<FilterKey>("all");
  const [showBlockedGroup, setShowBlockedGroup] = useState(false);

  const targetDomains = domainId ? domains.filter((d) => d.id === domainId) : domains;
  const targetIds = targetDomains.map((d) => d.id).join(",");

  const { data, isLoading } = useQuery({
    queryKey: ["sender-inventory", targetIds],
    queryFn: async () => {
      const results = await Promise.all(targetDomains.map(fetchDomainInventory));
      // Worst first (volume x fail rate), not raw volume — the same
      // "top failing" ordering OutboundTable uses.
      return results.flat().sort((a, b) => riskScore(b) - riskScore(a));
    },
    enabled: targetDomains.length > 0,
  });

  const updateReview = useMutation({
    mutationFn: ({
      domain_id,
      service_label,
      body,
    }: {
      domain_id: string;
      service_label: string;
      body: Partial<Pick<SenderReviewUpdate, "status" | "owner">>;
    }) => api.patch<SenderReviewUpdate>(`/domains/${domain_id}/dmarc/sender-inventory/${encodeURIComponent(service_label)}`, body),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["sender-inventory"] }),
  });

  const allRows = data ?? [];
  const filteredRows = allRows.filter((r) => matchesFilter(r, filter));
  const rows = filteredRows.filter((r) => !(r.status === "blocked" && r.volume < BLOCKED_LOW_VOLUME_THRESHOLD));
  const collapsedBlockedRows = filteredRows.filter(
    (r) => r.status === "blocked" && r.volume < BLOCKED_LOW_VOLUME_THRESHOLD,
  );

  return (
    <div className="card">
      <div className="card-header">
        <h3>Sender inventory</h3>
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
};

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
  const canExpand = row.source_ips.length > 1;
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
