import { useOutletContext } from "react-router-dom";
import type { Domain } from "../../api/types";
import { IssueRow } from "../../components/shared/IssueRow";
import { useActionQueue } from "../../hooks/useDomainInsights";

export default function FixesTab() {
  const domain = useOutletContext<Domain>();

  const { data: fixes, isLoading } = useActionQueue(domain.id);

  return (
    <div className="card">
      <div className="card-header">
        <h3>Fixes</h3>
      </div>
      <p className="section-hint" style={{ marginTop: "-0.4rem", marginBottom: "0.6rem" }}>
        Reflects current configuration and recent traffic — independent of any date range selected elsewhere.
      </p>
      {isLoading && <p className="muted">Loading…</p>}
      {!isLoading && (fixes ?? []).length === 0 && <p className="empty-state">Nothing needs attention right now.</p>}
      {(fixes ?? []).map((item, i) => (
        <IssueRow key={i} item={item} linkTo={item.link_path ?? undefined} />
      ))}
    </div>
  );
}
