import { useState } from "react";
import { Copy, Check } from "lucide-react";

// Shown exactly once, right after TOTP enrollment completes — these codes
// can't be retrieved again afterward (only their hashes are stored).
export default function RecoveryCodesStep({ codes, onContinue }: { codes: string[]; onContinue: () => void }) {
  const [copied, setCopied] = useState(false);
  const [acknowledged, setAcknowledged] = useState(false);

  function copyAll() {
    navigator.clipboard.writeText(codes.join("\n")).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    });
  }

  return (
    <div>
      <p className="page-subtitle" style={{ marginBottom: "0.75rem" }}>
        Save these recovery codes somewhere safe — each works once, to sign in if you lose access to your
        authenticator app. They won't be shown again.
      </p>
      <div
        style={{
          fontFamily: "monospace",
          fontSize: "0.9rem",
          background: "var(--plane)",
          border: "1px solid var(--border)",
          borderRadius: "var(--radius-sm)",
          padding: "0.75rem 1rem",
          display: "grid",
          gridTemplateColumns: "1fr 1fr",
          gap: "0.4rem",
          marginBottom: "0.75rem",
        }}
      >
        {codes.map((c) => (
          <span key={c}>{c}</span>
        ))}
      </div>
      <button className="btn btn--ghost btn--sm" onClick={copyAll} style={{ marginBottom: "1rem" }}>
        {copied ? <Check /> : <Copy />}
        {copied ? "Copied" : "Copy all"}
      </button>
      <label className="chip-row" style={{ fontSize: "0.85rem", marginBottom: "1rem" }}>
        <input type="checkbox" checked={acknowledged} onChange={(e) => setAcknowledged(e.target.checked)} />
        I've saved these codes
      </label>
      <button className="btn btn--primary" disabled={!acknowledged} onClick={onContinue} style={{ width: "100%", padding: "0.65rem" }}>
        Continue to dashboard
      </button>
    </div>
  );
}
