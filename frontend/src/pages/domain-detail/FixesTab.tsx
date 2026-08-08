import { useOutletContext } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api } from "../../api/client";
import type { Domain } from "../../api/types";
import type { ActionItem } from "../../api/overview";
import { IssueRow } from "../../components/shared/IssueRow";

export default function FixesTab() {
  const domain = useOutletContext<Domain>();

  const { data: fixes, isLoading } = useQuery({
    queryKey: ["action-queue", domain.id],
    queryFn: () => api.get<ActionItem[]>(`/action-queue?domain_id=${domain.id}`),
  });

  return (
    <div className="card">
      <div className="card-header">
        <h3>Fixes</h3>
      </div>
      {isLoading && <p className="muted">Loading…</p>}
      {!isLoading && (fixes ?? []).length === 0 && <p className="empty-state">Nothing needs attention right now.</p>}
      {(fixes ?? []).map((item, i) => (
        <IssueRow key={i} item={item} />
      ))}
    </div>
  );
}
