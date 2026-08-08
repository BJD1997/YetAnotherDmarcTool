import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { Download, Settings as SettingsIcon } from "lucide-react";
import { api } from "../../api/client";
import type { Domain } from "../../api/types";
import type { MailboxConnectionStatus } from "../../api/dmarc";
import { DATE_RANGE_PRESETS } from "../../api/overview";
import { MailboxHealthWidget } from "./widgets";

export default function CommandBar({
  domains,
  domainId,
  onDomainChange,
  days,
  onDaysChange,
  onExport,
}: {
  domains: Domain[];
  domainId: string | null;
  onDomainChange: (id: string | null) => void;
  days: number;
  onDaysChange: (days: number) => void;
  onExport: () => void;
}) {
  const { data: connection, isLoading: mailboxLoading } = useQuery({
    queryKey: ["mailbox-connection"],
    queryFn: () => api.get<MailboxConnectionStatus>("/mailbox-connection"),
    retry: false,
  });

  return (
    <div
      className="card"
      style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: "1rem", flexWrap: "wrap" }}
    >
      <div className="field-row">
        <select className="input" value={domainId ?? ""} onChange={(e) => onDomainChange(e.target.value || null)}>
          <option value="">All domains</option>
          {domains.map((d) => (
            <option key={d.id} value={d.id}>
              {d.name}
            </option>
          ))}
        </select>
        <select className="input" value={days} onChange={(e) => onDaysChange(Number(e.target.value))}>
          {DATE_RANGE_PRESETS.map((d) => (
            <option key={d} value={d}>
              Last {d} days
            </option>
          ))}
        </select>
      </div>
      <div className="chip-row">
        <Link to="/settings" style={{ textDecoration: "none" }}>
          <MailboxHealthWidget connection={connection} isLoading={mailboxLoading} />
        </Link>
        <button className="btn btn--ghost btn--sm" onClick={onExport}>
          <Download />
          Export CSV
        </button>
        <Link to="/settings" className="btn btn--ghost btn--sm">
          <SettingsIcon />
          Settings
        </Link>
      </div>
    </div>
  );
}
