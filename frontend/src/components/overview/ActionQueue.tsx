import { useOverviewActionQueue } from "../../hooks/useOverviewResources";
import { IssueRow } from "../shared/IssueRow";

export default function ActionQueue({ domainId }: { domainId: string | null }) {
  const { data, isLoading } = useOverviewActionQueue(domainId);
  const items = data ?? [];

  return (
    <div>
      <div className="card-header">
        <h3>Action queue</h3>
      </div>
      <p className="section-hint" style={{ marginTop: "-0.4rem", marginBottom: "0.6rem" }}>
        Reflects current configuration and recent traffic — independent of the date range selected above.
      </p>
      {isLoading && <p className="muted">Loading…</p>}
      {!isLoading && items.length === 0 && <p className="empty-state">Nothing needs attention right now.</p>}
      <div>
        {items.map((item, i) => (
          <IssueRow key={i} item={item} linkTo={item.link_path ?? (item.domain_id ? `/domains/${item.domain_id}` : undefined)} />
        ))}
      </div>
    </div>
  );
}
