import { useState } from "react";
import { Link } from "react-router-dom";
import { Sparkles, X } from "lucide-react";
import type { Discoveries } from "../../api/overview";
import { useDiscoveries } from "../../hooks/useOverviewResources";

const DISMISS_KEY = "yadt.discoveries.dismissed";

// What's in the notice, so dismissing it hides only what was seen: anything
// new brings it back.
function signature(d: Discoveries): string {
  return [
    ...d.domains.map((x) => `d:${x.name}`),
    ...d.selectors.flatMap((x) => x.selectors.map((s) => `s:${x.domain_id}:${s.selector}`)),
  ]
    .sort()
    .join("|");
}

function readDismissed(): string | null {
  try {
    return localStorage.getItem(DISMISS_KEY);
  } catch {
    return null;
  }
}

function listNames(names: string[], max = 3): string {
  return names.length <= max ? names.join(", ") : `${names.slice(0, max).join(", ")} and ${names.length - max} more`;
}

/** Org admins only: domains and DKIM selectors the reports turned up that
 *  aren't added yet, at the top of the Overview so they're seen on sign-in
 *  rather than only when browsing Settings or a domain's DNS tab. */
export default function DiscoveriesNotice({ enabled }: { enabled: boolean }) {
  const { data } = useDiscoveries(enabled);
  const [dismissed, setDismissed] = useState(readDismissed);

  if (!enabled || !data) return null;
  const sig = signature(data);
  if (!sig || sig === dismissed) return null;

  function dismiss() {
    try {
      localStorage.setItem(DISMISS_KEY, sig);
    } catch {
      /* private mode: hides until reload */
    }
    setDismissed(sig);
  }

  const domainNames = data.domains.map((d) => d.name);
  return (
    <div className="alert alert--neutral discoveries-notice">
      <Sparkles size={15} style={{ flexShrink: 0, marginTop: 2 }} />
      <div style={{ flex: 1, minWidth: 0 }}>
        <strong>New in your reports</strong>
        <ul>
          {domainNames.length > 0 && (
            <li>
              {domainNames.length === 1 ? "A domain you haven't added" : `${domainNames.length} domains you haven't added`}:{" "}
              {listNames(domainNames)}. <Link to="/settings/domains">Add or dismiss</Link>
            </li>
          )}
          {data.selectors.map((d) => (
            <li key={d.domain_id}>
              {d.domain_name} signs with DKIM {d.selectors.length === 1 ? "selector" : "selectors"}{" "}
              {listNames(d.selectors.map((s) => s.selector))}, not monitored yet.{" "}
              <Link to={`/domains/${d.domain_id}/dns`}>Add on the DNS tab</Link>
            </li>
          ))}
        </ul>
      </div>
      <button className="icon-btn" onClick={dismiss} aria-label="Dismiss until something new shows up" title="Dismiss until something new shows up">
        <X size={14} />
      </button>
    </div>
  );
}
