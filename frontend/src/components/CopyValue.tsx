import { Check, Copy } from "lucide-react";
import { useClipboardFeedback } from "../hooks/useClipboardFeedback";

// A DNS value people paste into their DNS provider, with a copy button.
export default function CopyValue({ label, value }: { label?: string; value: string }) {
  const { copied, copy } = useClipboardFeedback();
  return (
    <span className="chip-row" style={{ gap: "0.35rem", alignItems: "center" }}>
      {label && <span className="muted">{label}</span>}
      <code style={{ wordBreak: "break-all" }}>{value}</code>
      <button type="button" className="icon-btn" onClick={() => void copy(value)} aria-label={`Copy ${label ?? value}`}>
        {copied ? <Check size={14} /> : <Copy size={14} />}
      </button>
    </span>
  );
}
