import { Plus, Trash2 } from "lucide-react";
import { sameAddress } from "./sameAddress";

// The hosted-address part of a builder's "Report destination" block: a
// ticked/unticked row (with Remove, when an own mailbox makes that
// possible), or the button that requests one. Shows nothing when the
// hosted address is itself the destination (the destination row already
// shows it) or when none exists and this server can't host one.
export default function HostedAddressRow({
  hostedAddress,
  destination,
  hostedMailboxAvailable,
  checked,
  onToggle,
  canRemove,
  onRemove,
  removePending,
  onRequest,
  requestPending,
  error,
  authWarning,
}: {
  hostedAddress: string | null;
  destination: string | null;
  hostedMailboxAvailable: boolean;
  checked: boolean;
  onToggle: (checked: boolean) => void;
  canRemove: boolean;
  onRemove: () => void;
  removePending: boolean;
  onRequest: () => void;
  requestPending: boolean;
  error: string | null;
  authWarning: string | null;
}) {
  if (!hostedMailboxAvailable && !hostedAddress) return null;
  return (
    <div style={{ marginTop: "0.6rem" }}>
      {hostedAddress && !sameAddress(hostedAddress, destination) ? (
        <>
          <label style={{ display: "flex", alignItems: "center", gap: "0.4rem", fontSize: "0.85rem" }}>
            <input type="checkbox" checked={checked} onChange={(e) => onToggle(e.target.checked)} />
            <code>{hostedAddress}</code>
            <span className="badge badge--good">hosted by YetAnotherDmarcTool</span>
          </label>
          {canRemove && (
            <button className="btn btn--ghost btn--sm" style={{ marginTop: "0.3rem" }} onClick={onRemove} disabled={removePending}>
              <Trash2 />
              Remove hosted address
            </button>
          )}
        </>
      ) : !hostedAddress && hostedMailboxAvailable ? (
        <button className="btn btn--ghost btn--sm" onClick={onRequest} disabled={requestPending}>
          <Plus />
          {destination ? "Also use a YetAnotherDmarcTool-hosted address" : "No mailbox of your own? Use a YetAnotherDmarcTool-hosted address"}
        </button>
      ) : null}
      {error && (
        <p className="section-hint" style={{ color: "var(--critical-text)", marginTop: "0.3rem", marginBottom: 0 }}>
          {error}
        </p>
      )}
      {authWarning && (
        <p className="section-hint" style={{ color: "var(--warning-text)", marginTop: "0.3rem", marginBottom: 0 }}>
          This address works, but couldn't be auto-authorized yet: {authWarning}
        </p>
      )}
    </div>
  );
}
