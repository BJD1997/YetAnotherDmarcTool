import { useRef, useState } from "react";
import { ApiError } from "../../api/client";
import { useRemoveHostedReportAddress, useRequestHostedReportAddress } from "../../hooks/usePolicyBuilders";

// Hosted-address state and actions shared by the DMARC and TLS-RPT builders.
// `destination` is the domain's report destination; `onGenerated` fires only
// when there is none, so the new address becomes it (with an own mailbox as
// destination the hosted address is an optional extra the user ticks).
// `onRemoved` receives the removed address so the builder can drop its
// own (mailto:) form from the selection.
export function useHostedAddress(
  domainId: string,
  destination: string | null | undefined,
  onGenerated: (address: string) => void,
  onRemoved: (address: string) => void,
) {
  const [hostedAddress, setHostedAddress] = useState<string | null>(null);
  // The mutation callbacks below can run from an older render's closure, so
  // they read the current address and destination through refs.
  const hostedRef = useRef<string | null>(null);
  hostedRef.current = hostedAddress;
  const destinationRef = useRef(destination);
  destinationRef.current = destination;
  const [error, setError] = useState<string | null>(null);
  // Set only when the address itself was generated fine but the RFC 7489
  // §7.1 authorization record on the hosted domain couldn't be
  // auto-provisioned (Cloudflare unconfigured/erroring) — distinct from
  // error, since the address is still usable, just not yet authorized to
  // receive reports.
  const [authWarning, setAuthWarning] = useState<string | null>(null);

  const request = useRequestHostedReportAddress(
    domainId,
    (result) => {
      setError(null);
      setHostedAddress(result.hosted_report_address);
      if (!destinationRef.current) onGenerated(result.hosted_report_address);
      const needsAttention = result.authorization_record_status === "unconfigured" || result.authorization_record_status === "error";
      setAuthWarning(needsAttention ? result.authorization_record_detail : null);
    },
    (err) => setError(err instanceof ApiError ? err.message : "couldn't generate a hosted address"),
  );

  const remove = useRemoveHostedReportAddress(
    domainId,
    () => {
      const removed = hostedRef.current;
      setError(null);
      setAuthWarning(null);
      setHostedAddress(null);
      if (removed) onRemoved(removed);
    },
    (err) => setError(err instanceof ApiError ? err.message : "couldn't remove the hosted address"),
  );

  return { hostedAddress, setHostedAddress, error, authWarning, request, remove };
}
