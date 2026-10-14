import { useState } from "react";
import { X } from "lucide-react";
import { ApiError } from "../../api/client";
import AddDomainForm from "../../components/settings/AddDomainForm";
import {
  type DetectedDomainAdded,
  useAddDetectedDomain,
  useDetectedDomains,
  useDismissDetectedDomain,
} from "../../hooks/useDetectedDomains";

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
  const { data: detected, isLoading } = useDetectedDomains();
  const [error, setError] = useState<string | null>(null);
  const [added, setAdded] = useState<DetectedDomainAdded | null>(null);

  const addDetected = useAddDetectedDomain(
    (result) => {
      setError(null);
      setAdded(result);
    },
    (err) => setError(err instanceof ApiError ? err.message : "failed to add domain"),
  );

  const dismissDetected = useDismissDetectedDomain(
    () => setError(null),
    (err) => setError(err instanceof ApiError ? err.message : "failed to dismiss"),
  );

  if (isLoading || !detected || (detected.length === 0 && !added)) return null;

  return (
    <div style={{ marginTop: "1.25rem" }}>
      <div className="section-title" style={{ fontSize: "0.85rem" }}>
        Detected domains
      </div>
      <p className="section-hint">Your mailbox is receiving DMARC/TLS reports for these domains, but none of them are added yet.</p>
      {error && <div className="alert alert--critical">{error}</div>}
      {added && <AddedNotice result={added} />}
      {detected.length > 0 && (
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
                  subdomain of {item.suggested_parent_name}, also detected: both are added, and verifying{" "}
                  {item.suggested_parent_name} verifies this one too
                </div>
              )}
            </div>
            <div style={{ display: "flex", gap: "0.4rem", flexShrink: 0 }}>
              <button className="btn btn--secondary btn--sm" onClick={() => addDetected.mutate(item)} disabled={addDetected.isPending}>
                {item.relationship === "subdomain_of_detected" ? `Add with ${item.suggested_parent_name}` : "Add & verify"}
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
      )}
    </div>
  );
}

function AddedNotice({ result }: { result: DetectedDomainAdded }) {
  const others = result.added.filter((name) => name !== result.root);
  return (
    <div className="alert alert--good">
      Added {result.root}
      {others.length > 0 && <> with {others.join(", ")}</>}
      {result.moved.length > 0 && <>; moved {result.moved.join(", ")} under it</>}. Verify {result.root} on the Domains
      page{others.length + result.moved.length > 0 ? ": its subdomains are verified together with it." : "."}
    </div>
  );
}
