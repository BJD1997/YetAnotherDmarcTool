import { useState } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { RefreshCw, Trash2, Archive, ArchiveRestore, ChevronDown, ChevronRight, Settings as SettingsIcon } from "lucide-react";
import { api, ApiError } from "../api/client";
import type { Domain, VerifyDomainResponse } from "../api/types";
import { useAuth } from "../auth/AuthContext";

export default function Domains() {
  const { user } = useAuth();
  const { data: domains, isLoading } = useQuery({
    queryKey: ["domains"],
    queryFn: () => api.get<Domain[]>("/domains"),
  });
  const [expanded, setExpanded] = useState<Set<string>>(new Set());

  const apexDomains = (domains ?? []).filter((d) => !d.parent_domain_id);
  const subdomainsByParent = new Map<string, Domain[]>();
  for (const d of domains ?? []) {
    if (d.parent_domain_id) {
      const list = subdomainsByParent.get(d.parent_domain_id) ?? [];
      list.push(d);
      subdomainsByParent.set(d.parent_domain_id, list);
    }
  }

  const canManage = user?.role === "org_admin";

  function toggleExpanded(id: string) {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  return (
    <section>
      <div className="page-header">
        <div>
          <h1>Domains</h1>
          <p className="page-subtitle">
            A domain must be verified (proof you control its DNS) before any best-practice checks run against it.
          </p>
        </div>
        {canManage && (
          <Link to="/settings" className="btn btn--secondary btn--sm">
            <SettingsIcon />
            Add domain
          </Link>
        )}
      </div>

      {isLoading && <p className="muted">Loading…</p>}
      {!isLoading && apexDomains.length === 0 && (
        <p className="empty-state">
          No domains yet. {canManage && <Link to="/settings">Add one in Settings.</Link>}
        </p>
      )}

      <div className="domain-grid">
        {apexDomains.map((domain) => {
          const subs = subdomainsByParent.get(domain.id) ?? [];
          const isOpen = expanded.has(domain.id);
          return (
            <div key={domain.id} className="card" style={{ marginBottom: 0 }}>
              <DomainRowContent domain={domain} canManage={canManage} />
              {subs.length > 0 && (
                <>
                  <button
                    className="btn btn--ghost btn--sm"
                    onClick={() => toggleExpanded(domain.id)}
                    style={{ marginTop: "0.6rem", paddingLeft: 0 }}
                  >
                    {isOpen ? <ChevronDown /> : <ChevronRight />}
                    {subs.length} subdomain{subs.length === 1 ? "" : "s"}
                  </button>
                  {isOpen && (
                    <div className="subdomain-list">
                      {subs.map((sub) => (
                        <div key={sub.id} style={{ opacity: sub.is_active ? 1 : 0.65 }}>
                          <DomainRowContent domain={sub} canManage={canManage} />
                        </div>
                      ))}
                    </div>
                  )}
                </>
              )}
            </div>
          );
        })}
      </div>
    </section>
  );
}

function VerificationBadge({ status }: { status: Domain["verification_status"] }) {
  const verified = status === "verified";
  return <span className={`badge ${verified ? "badge--good" : "badge--warning"}`}>{verified ? "verified" : "unverified"}</span>;
}

function DomainRowContent({ domain, canManage }: { domain: Domain; canManage: boolean }) {
  const queryClient = useQueryClient();
  const [showInstructions, setShowInstructions] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);

  const verifyDomain = useMutation({
    mutationFn: () => api.post<VerifyDomainResponse>(`/domains/${domain.id}/verify`),
    onSuccess: (res) => {
      queryClient.invalidateQueries({ queryKey: ["domains"] });
      if (!res.verified) {
        setShowInstructions(true);
      }
    },
  });

  const deleteDomain = useMutation({
    mutationFn: () => api.delete(`/domains/${domain.id}`),
    onSuccess: () => {
      setActionError(null);
      queryClient.invalidateQueries({ queryKey: ["domains"] });
    },
    onError: (err) => setActionError(err instanceof ApiError ? err.message : "failed to remove domain"),
  });

  const toggleActive = useMutation({
    mutationFn: () => api.patch<Domain>(`/domains/${domain.id}`, { is_active: !domain.is_active }),
    onSuccess: () => {
      setActionError(null);
      queryClient.invalidateQueries({ queryKey: ["domains"] });
    },
    onError: (err) => setActionError(err instanceof ApiError ? err.message : "failed to update domain"),
  });

  function handleRemove() {
    if (window.confirm(`Remove ${domain.name}? This can't be undone.`)) {
      deleteDomain.mutate();
    }
  }

  const isPending = domain.verification_status === "pending";

  return (
    <div>
      <div className="domain-card-header">
        <div className="domain-card-title">
          <Link to={`/domains/${domain.id}`}>
            <strong>{domain.name}</strong>
          </Link>
        </div>
        {canManage && (
          <div className="chip-row">
            <button className="icon-btn" onClick={() => toggleActive.mutate()} disabled={toggleActive.isPending} title={domain.is_active ? "Archive" : "Unarchive"}>
              {domain.is_active ? <Archive /> : <ArchiveRestore />}
            </button>
            <button className="icon-btn" onClick={handleRemove} disabled={deleteDomain.isPending} title="Remove">
              <Trash2 />
            </button>
          </div>
        )}
      </div>

      <div className="chip-row" style={{ marginTop: "0.4rem" }}>
        {!domain.is_active && <span className="badge badge--neutral">archived</span>}
        <VerificationBadge status={domain.verification_status} />
        {canManage && isPending && (
          <>
            <button className="btn btn--ghost btn--sm" onClick={() => setShowInstructions((v) => !v)}>
              {showInstructions ? "Hide" : "How to verify"}
            </button>
            <button className="btn btn--secondary btn--sm" onClick={() => verifyDomain.mutate()} disabled={verifyDomain.isPending}>
              <RefreshCw />
              Check now
            </button>
          </>
        )}
      </div>

      {actionError && (
        <div className="alert alert--critical" style={{ marginTop: "0.6rem", marginBottom: 0 }}>
          {actionError}
          {actionError.includes("report history") && " (use Archive instead)"}
        </div>
      )}

      {isPending && showInstructions && (
        <div
          style={{
            marginTop: "0.75rem",
            padding: "0.75rem 0.9rem",
            background: "var(--plane)",
            border: "1px solid var(--border)",
            borderRadius: "var(--radius-sm)",
            fontFamily: "monospace",
            fontSize: "0.83rem",
          }}
        >
          Add this DNS TXT record, then click "Check now":
          <br />
          <strong>{domain.verification_record_name}</strong>
          <br />
          TXT value: <strong>{domain.verification_token}</strong>
          {verifyDomain.isSuccess && !verifyDomain.data.verified && (
            <p style={{ color: "var(--critical-text)", fontFamily: "var(--font-sans)", marginTop: "0.5rem" }}>
              Not found yet — DNS changes can take a few minutes to propagate.
            </p>
          )}
        </div>
      )}
    </div>
  );
}
