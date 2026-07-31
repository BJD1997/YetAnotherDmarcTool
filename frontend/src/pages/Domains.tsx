import { useState, type CSSProperties } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
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
      <h2>Domains</h2>
      <p style={{ color: "#4b5563", maxWidth: 640 }}>
        A domain must be verified (proof you control its DNS) before any best-practice checks run
        against it. Adding a subdomain of an already-verified domain verifies it automatically.
      </p>

      {canManage && (
        <form
          onSubmit={(e) => {
            e.preventDefault();
            createDomain.mutate();
          }}
          style={{ display: "flex", gap: "0.5rem", alignItems: "center", margin: "1rem 0" }}
        >
          <select value={parentId} onChange={(e) => setParentId(e.target.value)}>
            <option value="">apex domain</option>
            {apexDomains.map((d) => (
              <option key={d.id} value={d.id}>
                subdomain of {d.name}
              </option>
            ))}
          </select>
          <input
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder={selectedParent ? "mail" : "example.com"}
            required
          />
          {selectedParent && <span style={{ color: "#6b7280" }}>.{selectedParent.name}</span>}
          <button type="submit" disabled={createDomain.isPending}>
            Add
          </button>
        </form>
      )}
      {formError && <p style={{ color: "#b91c1c" }}>{formError}</p>}
      {formNotice && <p style={{ color: "#166534" }}>{formNotice}</p>}

      {isLoading && <p>Loading…</p>}
      {!isLoading && apexDomains.length === 0 && <p>No domains yet.</p>}

      <ul style={{ listStyle: "none", padding: 0 }}>
        {apexDomains.map((domain) => (
          <li key={domain.id} style={{ marginBottom: "1rem" }}>
            <DomainRow domain={domain} canManage={canManage} />
            <ul style={{ marginTop: "0.5rem" }}>
              {(subdomainsByParent.get(domain.id) ?? []).map((sub) => (
                <li key={sub.id} style={{ marginBottom: "0.5rem" }}>
                  <DomainRow domain={sub} canManage={canManage} />
                </li>
              ))}
            </ul>
          </li>
        ))}
      </ul>

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
    <div style={{ marginTop: "2rem", paddingTop: "1.5rem", borderTop: "1px solid #e5e7eb" }}>
      <h3>Detected domains</h3>
      <p style={{ color: "#4b5563", maxWidth: 640 }}>
        Your mailbox is receiving DMARC/TLS reports for these domains, but none of them are added yet.
      </p>
      {error && <p style={{ color: "#b91c1c" }}>{error}</p>}
      <ul style={{ listStyle: "none", padding: 0 }}>
        {detected.map((item) => (
          <li key={item.name} style={{ marginBottom: "0.5rem" }}>
            <strong>{item.name}</strong>
            <span style={{ color: "#6b7280", marginLeft: "0.5rem", fontSize: "0.85rem" }}>
              {item.report_count} report{item.report_count === 1 ? "" : "s"}
              {item.message_volume > 0 && `, ${item.message_volume} messages`}
            </span>
            {item.relationship === "subdomain_of_registered" && (
              <span style={{ marginLeft: "0.5rem", fontSize: "0.75rem", color: "#166534" }}>
                subdomain of {item.suggested_parent_name} (already added)
              </span>
            )}
            {item.relationship === "subdomain_of_detected" && (
              <span style={{ marginLeft: "0.5rem", fontSize: "0.75rem", color: "#92400e" }}>
                looks like a subdomain of {item.suggested_parent_name}, which is also detected but not yet
                added — consider adding that one first
              </span>
            )}
            <button
              style={{ marginLeft: "0.75rem" }}
              onClick={() => addDetected.mutate(item)}
              disabled={addDetected.isPending}
            >
              Add &amp; verify
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}

function VerificationBadge({ status }: { status: Domain["verification_status"] }) {
  const verified = status === "verified";
  return (
    <span
      style={{
        marginLeft: "0.5rem",
        fontSize: "0.75rem",
        padding: "0.1rem 0.5rem",
        borderRadius: 999,
        background: verified ? "#dcfce7" : "#fef3c7",
        color: verified ? "#166534" : "#92400e",
      }}
    >
      {verified ? "verified" : "unverified"}
    </span>
  );
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
    <div>
      <Link to={`/domains/${domain.id}`}>
        <strong>{domain.name}</strong>
      </Link>
      {!domain.is_active && <span style={{ color: "#92400e" }}> (archived)</span>}
      <VerificationBadge status={domain.verification_status} />
      {canManage && isPending && (
        <>
          <button style={{ marginLeft: "0.75rem" }} onClick={() => setShowInstructions((v) => !v)}>
            {showInstructions ? "Hide" : "How to verify"}
          </button>
          <button
            style={{ marginLeft: "0.5rem" }}
            onClick={() => verifyDomain.mutate()}
            disabled={verifyDomain.isPending}
          >
            Check now
          </button>
        </>
      )}
      {canManage && (
        <>
          <button style={{ marginLeft: "0.5rem" }} onClick={() => toggleActive.mutate()} disabled={toggleActive.isPending}>
            {domain.is_active ? "Archive" : "Unarchive"}
          </button>
          <button style={{ marginLeft: "0.5rem" }} onClick={handleRemove} disabled={deleteDomain.isPending}>
            Remove
          </button>
        </>
      )}
      {actionError && (
        <div style={{ color: "#b91c1c", fontSize: "0.85rem", marginTop: "0.25rem" }}>
          {actionError}
          {actionError.includes("report history") && " (use Archive instead)"}
        </div>
      )}
      {isPending && showInstructions && (
        <div
          style={{
            marginTop: "0.4rem",
            padding: "0.6rem 0.8rem",
            background: "#f9fafb",
            border: "1px solid #e5e7eb",
            borderRadius: 6,
            fontFamily: "monospace",
            fontSize: "0.85rem",
            maxWidth: 640,
          }}
        >
          Add this DNS TXT record, then click "Check now":
          <br />
          <strong>{domain.verification_record_name}</strong>
          <br />
          TXT value: <strong>{domain.verification_token}</strong>
          {verifyDomain.isSuccess && !verifyDomain.data.verified && (
            <p style={{ color: "#b91c1c", fontFamily: "system-ui, sans-serif" }}>
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
    <div style={{ fontSize: "0.85rem", marginTop: "0.4rem" }}>
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
        <div style={bannerStyle("#fef3c7", "#92400e")}>
          No mailbox connection configured yet — ask your org admin to set one up.
        </div>
      );
    }
    return (
      <div style={bannerStyle("#fef3c7", "#92400e")}>
        <div>No mailbox connection configured yet.</div>
        {consentGuidance}
        <form
          onSubmit={(e) => {
            e.preventDefault();
            setConnection.mutate();
          }}
          style={{ display: "flex", gap: "0.5rem", marginTop: "0.5rem" }}
        >
          <input
            value={mailbox}
            onChange={(e) => setMailbox(e.target.value)}
            placeholder="dmarc-reports@yourdomain.com"
            style={{ width: "260px" }}
            required
          />
          <button type="submit" disabled={setConnection.isPending}>
            Save &amp; start syncing
          </button>
        </form>
        {saveError && <div style={{ color: "#b91c1c" }}>{saveError}</div>}
      </div>
    );
  }
  if (!connection) return null;

  const ok = connection.consent_status === "granted" && connection.last_sync_status !== "error";
  return (
    <div style={bannerStyle(ok ? "#dcfce7" : "#fef3c7", ok ? "#166534" : "#92400e")}>
      {editing ? (
        <form
          onSubmit={(e) => {
            e.preventDefault();
            setConnection.mutate();
          }}
          style={{ display: "flex", gap: "0.5rem", alignItems: "center" }}
        >
          <input value={mailbox} onChange={(e) => setMailbox(e.target.value)} style={{ width: "260px" }} required />
          <button type="submit" disabled={setConnection.isPending}>
            Save
          </button>
          <button type="button" onClick={() => setEditing(false)}>
            Cancel
          </button>
        </form>
      ) : (
        <>
          Mailbox: <strong>{connection.mailbox_address}</strong> — consent {connection.consent_status}
          {connection.last_sync_at && `, last synced ${new Date(connection.last_sync_at).toLocaleString()}`}
          {connection.last_sync_status === "error" && connection.last_sync_error && (
            <> — error: {connection.last_sync_error}</>
          )}
          {/* Setting the mailbox is self-attested, not verified — a sync
              error very often just means the Entra consent steps were never
              actually completed, so the links that matter for fixing that
              need to stay reachable here too, not just before the mailbox
              was first configured. */}
          {canManage && connection.last_sync_status === "error" && consentGuidance}
          {canManage && (
            <>
              <button style={{ marginLeft: "1rem" }} onClick={() => resync.mutate()} disabled={resync.isPending}>
                {resync.isPending || resync.isSuccess ? "Syncing…" : "Resync now"}
              </button>
              <button
                style={{ marginLeft: "0.5rem" }}
                onClick={() => {
                  setMailbox(connection.mailbox_address);
                  setEditing(true);
                }}
              >
                Change mailbox
              </button>
              {connection.last_sync_status !== "error" && (
                <button style={{ marginLeft: "0.5rem" }} onClick={() => setShowLinks((v) => !v)}>
                  {showLinks ? "Hide Entra links" : "Entra links"}
                </button>
              )}
              {showLinks && connection.last_sync_status !== "error" && consentGuidance}
            </>
          )}
        </>
      )}
      {saveError && <div style={{ color: "#b91c1c" }}>{saveError}</div>}
    </div>
  );
}

function bannerStyle(background: string, color: string): CSSProperties {
  return { background, color, padding: "0.6rem 1rem", borderRadius: 6, marginBottom: "1rem" };
}
