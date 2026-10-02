import { useOutletContext } from "react-router-dom";
import type { Domain } from "../../api/types";
import SenderInventory from "../../components/overview/SenderInventory";

export default function SendersTab() {
  const domain = useOutletContext<Domain>();
  if (domain.verification_status !== "verified") {
    return (
      <div className="card">
        <p className="empty-state" style={{ margin: 0 }}>
          Senders show up once this domain is verified.
        </p>
      </div>
    );
  }
  return <SenderInventory domainId={domain.id} domains={[domain]} />;
}
