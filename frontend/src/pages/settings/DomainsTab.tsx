import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { X } from "lucide-react";
import { api, ApiError } from "../../api/client";
import type { DetectedDomain, Domain } from "../../api/types";
import AddDomainForm from "../../components/settings/AddDomainForm";

export default function DomainsTab() {
  return (
    <section>
      <h3 className="section-title">Add a domain</h3>
      <p className="section-hint">Domains and subdomains to monitor. Manage the full list from the Domains page.</p>
      <AddDomainForm />
      <DetectedDomains />
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
      api.post<Domain & { reattributed_reports: number; reattributed_records: number }>("/domains", {
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

  const dismissDetected = useMutation({
    mutationFn: (item: DetectedDomain) => api.post<void>(`/dmarc/detected-domains/${encodeURIComponent(item.name)}/dismiss`),
    onSuccess: () => {
      setError(null);
      queryClient.invalidateQueries({ queryKey: ["detected-domains"] });
    },
    onError: (err) => setError(err instanceof ApiError ? err.message : "failed to dismiss"),
  });

  if (isLoading || !detected || detected.length === 0) return null;

  return (
    <div style={{ marginTop: "1.25rem" }}>
      <div className="section-title" style={{ fontSize: "0.85rem" }}>
        Detected domains
      </div>
      <p className="section-hint">Your mailbox is receiving DMARC/TLS reports for these domains, but none of them are added yet.</p>
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
            <div style={{ display: "flex", gap: "0.4rem", flexShrink: 0 }}>
              <button className="btn btn--secondary btn--sm" onClick={() => addDetected.mutate(item)} disabled={addDetected.isPending}>
                Add &amp; verify
              </button>
              <button
                className="btn btn--ghost btn--sm"
                title="Not mine — stop suggesting it"
                onClick={() => dismissDetected.mutate(item)}
                disabled={dismissDetected.isPending}
              >
                <X size={14} />
              </button>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
