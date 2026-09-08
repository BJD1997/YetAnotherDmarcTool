import { useState } from "react";
import { useOverviewActionQueue } from "../../hooks/useOverviewResources";
import { IssueRow } from "../shared/IssueRow";

const VISIBLE_COUNT = 4;

export default function ActionQueue({ domainId }: { domainId: string | null }) {
  const { data, isLoading } = useOverviewActionQueue(domainId);

  const [expanded, setExpanded] = useState(false);
  const items = data ?? [];
  const visible = expanded ? items : items.slice(0, VISIBLE_COUNT);
  const hiddenCount = items.length - visible.length;

  return (
    <div>
      <div className="card-header">
        <h3>Action queue</h3>
      </div>
      {isLoading && <p className="muted">Loading…</p>}
      {!isLoading && items.length === 0 && <p className="empty-state">Nothing needs attention right now.</p>}
      <div>
        {visible.map((item, i) => (
          <IssueRow key={i} item={item} linkTo={item.domain_id ? `/domains/${item.domain_id}` : undefined} />
        ))}
      </div>
      {hiddenCount > 0 && (
        <button className="btn btn--ghost btn--sm" style={{ marginTop: "0.5rem" }} onClick={() => setExpanded(true)}>
          View all issues ({items.length})
        </button>
      )}
      {expanded && items.length > VISIBLE_COUNT && (
        <button className="btn btn--ghost btn--sm" style={{ marginTop: "0.5rem" }} onClick={() => setExpanded(false)}>
          Show fewer
        </button>
      )}
    </div>
  );
}
