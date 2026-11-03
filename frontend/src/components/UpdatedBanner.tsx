import { useRef } from "react";
import { RefreshCw } from "lucide-react";
import { useHealth } from "../hooks/useOverviewResources";

// A tab keeps running the screens it loaded, even after the server was
// updated; until it's reloaded, people see old screens next to a footer
// showing the new version. This compares the server's version with the one
// at page load and offers the reload.
export default function UpdatedBanner() {
  const { data: health } = useHealth();
  const loadedWith = useRef<string | null>(null);
  if (health?.version && loadedWith.current === null) loadedWith.current = health.version;
  if (!health?.version || health.version === loadedWith.current) return null;

  return (
    <div className="alert alert--warning" role="status" style={{ display: "flex", alignItems: "center", gap: "0.6rem", flexWrap: "wrap" }}>
      <span>
        YetAnotherDmarcTool was updated to <strong>{health.version}</strong>. Reload to use the new version.
      </span>
      <button type="button" className="btn btn--secondary btn--sm" onClick={() => window.location.reload()}>
        <RefreshCw />
        Reload
      </button>
    </div>
  );
}
