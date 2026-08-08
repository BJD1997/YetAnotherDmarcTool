import { Link } from "react-router-dom";
import { ChevronRight } from "lucide-react";
import type { ActionItem } from "../../api/overview";

export function IssueRow({ item, linkTo }: { item: ActionItem; linkTo?: string }) {
  const row = (
    <>
      <span className={`dot dot--${item.severity}`} />
      <div className="issue-row-body">
        <div className="issue-title">{item.title}</div>
        <div className="issue-hint">{item.action_hint}</div>
      </div>
      {linkTo && <ChevronRight size={14} style={{ flexShrink: 0, color: "var(--ink-muted)" }} />}
    </>
  );
  return linkTo ? (
    <Link to={linkTo} className="issue-row">
      {row}
    </Link>
  ) : (
    <div className="issue-row">{row}</div>
  );
}
