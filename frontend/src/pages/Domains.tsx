import { useState } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Plus, RefreshCw, Trash2, Archive, ArchiveRestore, Mail, ChevronDown, ChevronUp } from "lucide-react";
import { api, ApiError } from "../api/client";
import type { DetectedDomain, Domain, Organization, VerifyDomainResponse } from "../api/types";
import type { MailboxConnectionStatus } from "../api/dmarc";
import { useAuth } from "../auth/AuthContext";

export default function Domains() {
  const { user } = useAuth();
  const queryClient = useQueryClient();
  const { data: domains, isLoading } = useQuery({
    queryKey: ["domains"],
    queryFn: () => api.get<Domain[]>("/domains"),
  });

  const [name, setName] = useState("");
  const [parentId, setParentId] = useState<string>("");
  const [formError, setFormError] = useState<string | null>(null);
  const [formNotice, setFormNotice] = useState<string | null>(null);

  const apexDomains = (domains ?? []).filter((d) => !d.parent_domain_id);
  const selectedParent = apexDomains.find((d) => d.id === parentId);
  // When adding a subdomain, the user only types the label (e.g. "mail") —
  // the parent's domain is appended automatically rather than requiring the
  // full FQDN to be retyped, which was silently rejected before (a bare
  // label like "mail" fails the full-FQDN validation, giving a confusing 422).
  const fullName = selectedParent ? `${name}.${selectedParent.name}` : name;

  const createDomain = useMutation({
    mutationFn: () =>
      api.post<Domain & { reattributed_reports: number }>("/domains", {
        name: fullName,
        parent_domain_id: parentId || null,
      }),
    onSuccess: (created) => {
      setName("");
      setParentId("");
      setFormError(null);
      setFormNotice(
        created.reattributed_reports > 0
          ? `Linked ${created.reattributed_reports} previously-unmatched report(s) to ${created.name}.`
          : null,
      );
      queryClient.invalidateQueries({ queryKey: ["domains"] });
    },
    onError: (err) => {
      setFormNotice(null);
      setFormError(err instanceof ApiError ? err.message : "failed to add domain");
    },
  });
  const subdomainsByParent = new Map<string, Domain[]>();
  for (const d of domains ?? []) {
    if (d.parent_domain_id) {
      const list = subdomainsByParent.get(d.parent_domain_id) ?? [];
      list.push(d);
      subdomainsByParent.set(d.parent_domain_id, list);
    }
  }

  const canManage = user?.role === "org_admin";

  return (
    <section>
      <MailboxConnectionStatusBanner />

      <div className="page-header">
        <div>
          <h1>Domains</h1>
          <p className="page-subtitle">
            A domain must be verified (proof you control its DNS) before any best-practice checks run against it.
            Adding a subdomain of an already-verified domain verifies it automatically.
          </p>
        </div>
      </div>

      {canManage && (
        <div className="card">
          <form
            onSubmit={(e) => {
              e.preventDefault();
              createDomain.mutate();
            }}
            className="field-row"
          >
            <select className="input" value={parentId} onChange={(e) => setParentId(e.target.value)}>
              <option value="">apex domain</option>
              {apexDomains.map((d) => (
                <option key={d.id} value={d.id}>
                  subdomain of {d.name}
                </option>
              ))}
            </select>
            <input
              className="input"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder={selectedParent ? "mail" : "example.com"}
              required
            />
            {selectedParent && <span className="muted">.{selectedParent.name}</span>}
            <button type="submit" className="btn btn--primary" disabled={createDomain.isPending}>
              <Plus />
              Add
            </button>
          </form>
          {formError && (
            <div className="alert alert--critical" style={{ marginTop: "0.75rem", marginBottom: 0 }}>
              {formError}
            </div>
          )}
          {formNotice && (
            <div className="alert alert--good" style={{ marginTop: "0.75rem", marginBottom: 0 }}>
              {formNotice}
            </div>
          )}
        </div>
      )}

      {isLoading && <p className="muted">Loading…</p>}
      {!isLoading && apexDomains.length === 0 && <p className="empty-state">No domains yet.</p>}

      {apexDomains.map((domain) => (
        <div key={domain.id}>
          <DomainRow domain={domain} canManage={canManage} />
          {(subdomainsByParent.get(domain.id) ?? []).map((sub) => (
            <div key={sub.id} style={{ marginLeft: "1.5rem" }}>
              <DomainRow domain={sub} canManage={canManage} />
            </div>
          ))}
        </div>
      ))}

      {canManage && <DetectedDomains />}
    </section>
  );
}

function DetectedDomains() {
  const queryClient = useQueryClient();
  const { data: detected, isLoading } = useQuery({
    queryKey: ["detected-domains"],
    queryFn: () => api.get<DetectedDomain[]>("/dmarc/detected-domains"),
  });
  const [error, setError] = useState<string | null>(null);

  const addDetected = useMutation({
    mutationFn: (item: DetectedDomain) =>
      api.post<Domain & { reattributed_reports: number }>("/domains", {
        name: item.name,
        parent_domain_id: item.suggested_parent_id,
      }),
    onSuccess: () => {
      setError(null);
      queryClient.invalidateQueries({ queryKey: ["domains"] });
      queryClient.invalidateQueries({ queryKey: ["detected-domains"] });
    },
    onError: (err) => setError(err instanceof ApiError ? err.message : "failed to add domain"),
  });

  if (isLoading || !detected || detected.length === 0) return null;

  return (
    <div style={{ marginTop: "2rem" }}>
      <h3 className="section-title">Detected domains</h3>
      <p className="section-hint">
        Your mailbox is receiving DMARC/TLS reports for these domains, but none of them are added yet.
      </p>
      {error && <div className="alert alert--critical">{error}</div>}
      <div className="card" style={{ padding: 0 }}>
        {detected.map((item, i) => (
          <div
            key={item.name}
            style={{
              display: "flex",
              alignItems: "center",
              justifyContent: "space-between",
              gap: "0.75rem",
              padding: "0.75rem 1rem",
              borderBottom: i < detected.length - 1 ? "1px solid var(--border)" : "none",
            }}
          >
            <div>
              <strong>{item.name}</strong>
              <span className="muted" style={{ marginLeft: "0.5rem", fontSize: "0.8rem" }}>
                {item.report_count} report{item.report_count === 1 ? "" : "s"}
                {item.message_volume > 0 && `, ${item.message_volume} messages`}
              </span>
              {item.relationship === "subdomain_of_registered" && (
                <span className="badge badge--good" style={{ marginLeft: "0.5rem" }}>
                  subdomain of {item.suggested_parent_name}
                </span>
              )}
              {item.relationship === "subdomain_of_detected" && (
                <div className="section-hint" style={{ margin: "0.15rem 0 0" }}>
                  looks like a subdomain of {item.suggested_parent_name}, which is also detected but not yet added —
                  consider adding that one first
                </div>
              )}
            </div>
            <button className="btn btn--secondary btn--sm" onClick={() => addDetected.mutate(item)} disabled={addDetected.isPending}>
              Add &amp; verify
            </button>
          </div>
        ))}
      </div>
    </div>
  );
}

function VerificationBadge({ status }: { status: Domain["verification_status"] }) {
  const verified = status === "verified";
  return <span className={`badge ${verified ? "badge--good" : "badge--warning"}`}>{verified ? "verified" : "unverified"}</span>;
}

function DomainRow({ domain, canManage }: { domain: Domain; canManage: boolean }) {
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
    <div className="card" style={{ marginBottom: "0.6rem", opacity: domain.is_active ? 1 : 0.65 }}>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: "0.75rem", flexWrap: "wrap" }}>
        <div style={{ display: "flex", alignItems: "center", gap: "0.5rem" }}>
          <Link to={`/domains/${domain.id}`}>
            <strong>{domain.name}</strong>
          </Link>
          {!domain.is_active && <span className="badge badge--neutral">archived</span>}
          <VerificationBadge status={domain.verification_status} />
        </div>

        {canManage && (
          <div className="chip-row">
            {isPending && (
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
            <button className="icon-btn" onClick={() => toggleActive.mutate()} disabled={toggleActive.isPending} title={domain.is_active ? "Archive" : "Unarchive"}>
              {domain.is_active ? <Archive /> : <ArchiveRestore />}
            </button>
            <button className="icon-btn" onClick={handleRemove} disabled={deleteDomain.isPending} title="Remove">
              <Trash2 />
            </button>
          </div>
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

function MailboxConnectionStatusBanner() {
  const { user } = useAuth();
  const queryClient = useQueryClient();
  const canManage = user?.role === "org_admin";

  const { data: org } = useQuery({
    queryKey: ["organization", "current"],
    queryFn: () => api.get<Organization>("/organizations/current"),
    enabled: !!user,
  });

  const { data: connection, error } = useQuery({
    queryKey: ["mailbox-connection"],
    queryFn: () => api.get<MailboxConnectionStatus>("/mailbox-connection"),
    retry: false,
  });

  const [mailbox, setMailbox] = useState("");
  const [editing, setEditing] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [showLinks, setShowLinks] = useState(false);

  const setConnection = useMutation({
    mutationFn: () => api.put<MailboxConnectionStatus>("/mailbox-connection", { mailbox_address: mailbox }),
    onSuccess: () => {
      setSaveError(null);
      setEditing(false);
      setMailbox("");
      queryClient.invalidateQueries({ queryKey: ["mailbox-connection"] });
      setTimeout(() => queryClient.invalidateQueries({ queryKey: ["mailbox-connection"] }), 4000);
    },
    onError: (err) => setSaveError(err instanceof ApiError ? err.message : "failed to save mailbox"),
  });

  const resync = useMutation({
    mutationFn: () => api.post("/mailbox-connection/resync"),
    onSuccess: () => {
      setTimeout(() => queryClient.invalidateQueries({ queryKey: ["mailbox-connection"] }), 3000);
    },
  });

  const consentLinks = org?.entra_consent_urls;
  const consentGuidance = consentLinks && (
    <div style={{ fontSize: "0.85rem", marginTop: "0.5rem" }}>
      Make sure your Global Admin has approved{" "}
      <a href={consentLinks.mail_access_consent_url} target="_blank" rel="noreferrer">
        mail access
      </a>{" "}
      (required) and, if your tenant blocks user sign-in consent,{" "}
      <a href={consentLinks.sso_consent_url} target="_blank" rel="noreferrer">
        dashboard sign-in
      </a>{" "}
      too.
    </div>
  );

  const noConnectionYet = error instanceof ApiError && error.status === 404;

  if (noConnectionYet) {
    if (!canManage) {
      return (
        <div className="alert alert--warning">
          <Mail size={15} style={{ verticalAlign: "-2px", marginRight: "0.4rem" }} />
          No mailbox connection configured yet — ask your org admin to set one up.
        </div>
      );
    }
    return (
      <div className="alert alert--warning">
        <div style={{ display: "flex", alignItems: "center", gap: "0.4rem" }}>
          <Mail size={15} />
          No mailbox connection configured yet.
        </div>
        {consentGuidance}
        <form
          onSubmit={(e) => {
            e.preventDefault();
            setConnection.mutate();
          }}
          className="field-row"
          style={{ marginTop: "0.6rem" }}
        >
          <input
            className="input"
            value={mailbox}
            onChange={(e) => setMailbox(e.target.value)}
            placeholder="dmarc-reports@yourdomain.com"
            style={{ width: "260px" }}
            required
          />
          <button type="submit" className="btn btn--primary btn--sm" disabled={setConnection.isPending}>
            Save &amp; start syncing
          </button>
        </form>
        {saveError && <div style={{ marginTop: "0.5rem" }}>{saveError}</div>}
      </div>
    );
  }
  if (!connection) return null;

  const ok = connection.consent_status === "granted" && connection.last_sync_status !== "error";
  return (
    <div className={`alert ${ok ? "alert--good" : "alert--warning"}`}>
      {editing ? (
        <form
          onSubmit={(e) => {
            e.preventDefault();
            setConnection.mutate();
          }}
          className="field-row"
        >
          <input className="input" value={mailbox} onChange={(e) => setMailbox(e.target.value)} style={{ width: "260px" }} required />
          <button type="submit" className="btn btn--primary btn--sm" disabled={setConnection.isPending}>
            Save
          </button>
          <button type="button" className="btn btn--ghost btn--sm" onClick={() => setEditing(false)}>
            Cancel
          </button>
        </form>
      ) : (
        <>
          <div style={{ display: "flex", alignItems: "center", gap: "0.4rem", flexWrap: "wrap" }}>
            <Mail size={15} />
            Mailbox: <strong>{connection.mailbox_address}</strong>
            <span className="muted">— consent {connection.consent_status}</span>
            {connection.last_sync_at && (
              <span className="muted">, last synced {new Date(connection.last_sync_at).toLocaleString()}</span>
            )}
          </div>
          {connection.last_sync_status === "error" && connection.last_sync_error && (
            <div style={{ marginTop: "0.35rem" }}>— error: {connection.last_sync_error}</div>
          )}
          {/* Setting the mailbox is self-attested, not verified — a sync
              error very often just means the Entra consent steps were never
              actually completed, so the links that matter for fixing that
              need to stay reachable here too, not just before the mailbox
              was first configured. */}
          {canManage && connection.last_sync_status === "error" && consentGuidance}
          {canManage && (
            <div className="chip-row" style={{ marginTop: "0.6rem" }}>
              <button className="btn btn--secondary btn--sm" onClick={() => resync.mutate()} disabled={resync.isPending}>
                <RefreshCw />
                {resync.isPending || resync.isSuccess ? "Syncing…" : "Resync now"}
              </button>
              <button
                className="btn btn--ghost btn--sm"
                onClick={() => {
                  setMailbox(connection.mailbox_address);
                  setEditing(true);
                }}
              >
                Change mailbox
              </button>
              {connection.last_sync_status !== "error" && (
                <button className="btn btn--ghost btn--sm" onClick={() => setShowLinks((v) => !v)}>
                  {showLinks ? <ChevronUp /> : <ChevronDown />}
                  Entra links
                </button>
              )}
            </div>
          )}
          {showLinks && connection.last_sync_status !== "error" && consentGuidance}
        </>
      )}
      {saveError && <div style={{ marginTop: "0.5rem" }}>{saveError}</div>}
    </div>
  );
}
