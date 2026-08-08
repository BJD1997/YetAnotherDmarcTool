import { useOutletContext } from "react-router-dom";
import type { Domain } from "../../api/types";
import SenderInventory from "../../components/overview/SenderInventory";

export default function SendersTab() {
  const domain = useOutletContext<Domain>();
  return <SenderInventory domainId={domain.id} domains={[domain]} />;
}
