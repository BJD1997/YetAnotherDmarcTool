import { useState, type ReactNode } from "react";
import { ChevronDown, ChevronRight, X } from "lucide-react";
import { ApiError } from "../../api/client";
import AddDomainForm from "../../components/settings/AddDomainForm";
import type { DetectedDomain } from "../../api/types";
import {
  type DetectedDomainAdded,
  useAddDetectedDomain,
  useAddDetectedDomainAlone,
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
  const [open, setOpen] = useState<Set<string>>(new Set());

  const onError = (fallback: string) => (err: Error) => setError(err instanceof ApiError ? err.message : fallback);
  const addDetected = useAddDetectedDomain((result) => {
    setError(null);
    setAdded(result);
  }, onError("failed to add domain"));
  const addAlone = useAddDetectedDomainAlone(() => {
    setError(null);
    setAdded(null);
  }, onError("failed to add domain"));
  const dismissDetected = useDismissDetectedDomain(() => setError(null), onError("failed to dismiss"));

  if (isLoading || !detected || (detected.length === 0 && !added)) return null;

  // Subdomains whose root domain is also detected sit under that root: adding
  // the root adds them too, and they can be added on their own from there.
  const nested = new Map<string, DetectedDomain[]>();
  for (const item of detected) {
    if (item.relationship === "subdomain_of_detected" && item.suggested_parent_name) {
      nested.set(item.suggested_parent_name, [...(nested.get(item.suggested_parent_name) ?? []), item]);
    }
  }
  const rows = detected.filter((item) => !(item.relationship === "subdomain_of_detected" && item.suggested_parent_name));
  const busy = addDetected.isPending || addAlone.isPending || dismissDetected.isPending;

  const toggle = (name: string) =>
    setOpen((current) => {
      const next = new Set(current);
      if (next.has(name)) next.delete(name);
      else next.add(name);
      return next;
    });

  return (
    <div style={{ marginTop: "1.25rem" }}>
      <div className="section-title" style={{ fontSize: "0.85rem" }}>
        Detected domains
      </div>
      <p className="section-hint">Your mailbox is receiving DMARC/TLS reports for these domains, but none of them are added yet.</p>
      {error && <div className="alert alert--critical">{error}</div>}
      {added && <AddedNotice result={added} />}
      {rows.length > 0 && (
        <div className="card" style={{ padding: 0 }}>
          {rows.map((item, i) => {
            const subdomains = nested.get(item.name) ?? [];
            const isOpen = open.has(item.name);
            return (
              <div key={item.name} style={{ borderBottom: i < rows.length - 1 ? "1px solid var(--border)" : "none" }}>
                <DetectedRow
                  item={item}
                  expander={
                    subdomains.length > 0 ? (
                      <button
                        type="button"
                        className="icon-btn"
                        aria-expanded={isOpen}
                        aria-label={`${isOpen ? "Hide" : "Show"} subdomains of ${item.name}`}
                        onClick={() => toggle(item.name)}
                      >
                        {isOpen ? <ChevronDown size={15} /> : <ChevronRight size={15} />}
                      </button>
                    ) : (
                      <span style={{ width: "1.75rem", flexShrink: 0 }} />
                    )
                  }
                  extra={
                    subdomains.length > 0 && (
                      <span className="muted" style={{ fontSize: "0.8rem" }}>
                        {" "}
                        · verifies {subdomains.length} subdomain{subdomains.length === 1 ? "" : "s"}
                      </span>
                    )
                  }
                  action="Add & verify"
                  onAdd={() => addDetected.mutate(item)}
                  onDismiss={() => dismissDetected.mutate(item)}
                  disabled={busy}
                />
                {isOpen &&
                  subdomains.map((sub) => (
                    <div key={sub.name} style={{ borderTop: "1px solid var(--border)", background: "var(--plane)" }}>
                      <DetectedRow
                        item={sub}
                        indent
                        action="Add on its own"
                        actionTitle={`Add ${sub.name} without ${item.name}; it's verified with its own DNS record`}
                        onAdd={() => addAlone.mutate(sub)}
                        onDismiss={() => dismissDetected.mutate(sub)}
                        disabled={busy}
                      />
                    </div>
                  ))}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

function DetectedRow({
  item,
  expander,
  extra,
  indent = false,
  action,
  actionTitle,
  onAdd,
  onDismiss,
  disabled,
}: {
  item: DetectedDomain;
  expander?: ReactNode;
  extra?: ReactNode;
  indent?: boolean;
  action: string;
  actionTitle?: string;
  onAdd: () => void;
  onDismiss: () => void;
  disabled: boolean;
}) {
  return (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        justifyContent: "space-between",
        gap: "0.75rem",
        padding: indent ? "0.6rem 1rem 0.6rem 3.25rem" : "0.75rem 1rem 0.75rem 0.5rem",
      }}
    >
      <div style={{ display: "flex", alignItems: "center", gap: "0.35rem", minWidth: 0 }}>
        {expander}
        <div style={{ minWidth: 0 }}>
          <strong>{item.name}</strong>
          <span className="muted" style={{ marginLeft: "0.5rem", fontSize: "0.8rem" }}>
            {item.report_count} report{item.report_count === 1 ? "" : "s"}
            {item.message_volume > 0 && `, ${item.message_volume} messages`}
          </span>
          {extra}
          {item.relationship === "subdomain_of_registered" && (
            <span className="badge badge--good" style={{ marginLeft: "0.5rem" }}>
              subdomain of {item.suggested_parent_name}
            </span>
          )}
        </div>
      </div>
      <div style={{ display: "flex", gap: "0.4rem", flexShrink: 0 }}>
        <button className="btn btn--secondary btn--sm" title={actionTitle} onClick={onAdd} disabled={disabled}>
          {action}
        </button>
        <button className="btn btn--ghost btn--sm" title="Not mine — stop suggesting it" onClick={onDismiss} disabled={disabled}>
          <X size={14} />
        </button>
      </div>
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
