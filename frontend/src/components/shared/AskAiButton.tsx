import { useEffect, useState, type MouseEvent } from "react";
import { createPortal } from "react-dom";
import { Copy, Sparkles, X } from "lucide-react";

import type { AskAiHint } from "../../api/overview";
import { useAskAiPrompt } from "../../hooks/useAskAi";
import { useCurrentOrganization } from "../../hooks/useOrganization";

const CLAUDE_URL = "https://claude.ai/new?q=";
const CHATGPT_URL = "https://chatgpt.com/?q=";
const GEMINI_URL = "https://gemini.google.com/app";

// The button can sit inside a link (an Action queue row): keep its clicks,
// and the dialog's, from also following that link.
function stop(e: MouseEvent) {
  e.preventDefault();
  e.stopPropagation();
}

/** "Ask AI" for one issue, when the organization has turned it on
 *  (Settings → General). Shows the exact prompt first; nothing is sent
 *  anywhere until the user opens it in their own AI account. */
/** `issue` is the issue as shown to the user (a title and hint), so the
 *  question says what it's about. */
export default function AskAiButton({ domainId, hint, issue }: { domainId: string; hint: AskAiHint; issue?: string }) {
  const { data: org } = useCurrentOrganization();
  const [open, setOpen] = useState(false);
  if (!org?.ask_ai_enabled) return null;

  return (
    <>
      <button
        type="button"
        className="btn btn--ghost btn--sm ask-ai-btn"
        onClick={(e) => {
          stop(e);
          setOpen(true);
        }}
      >
        <Sparkles size={13} />
        Ask AI
      </button>
      {open && createPortal(<AskAiDialog domainId={domainId} hint={hint} issue={issue} onClose={() => setOpen(false)} />, document.body)}
    </>
  );
}

function AskAiDialog({
  domainId,
  hint,
  issue,
  onClose,
}: {
  domainId: string;
  hint: AskAiHint;
  issue?: string;
  onClose: () => void;
}) {
  const { data, isLoading, error } = useAskAiPrompt(domainId, hint, issue, true);
  const [copied, setCopied] = useState(false);
  const prompt = data?.prompt ?? "";
  const encoded = encodeURIComponent(prompt);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);

  async function copy(): Promise<boolean> {
    try {
      await navigator.clipboard.writeText(prompt);
      setCopied(true);
      return true;
    } catch {
      return false;
    }
  }

  return (
    // Portalled to <body>, but React events still bubble to the button's
    // parents (a link, for Action queue rows): stop them here.
    <div className="modal-overlay" onClick={(e) => { e.stopPropagation(); onClose(); }}>
      <div className="modal-card" role="dialog" aria-label="Ask AI" onClick={(e) => e.stopPropagation()}>
        <div className="modal-header">
          <h2 style={{ margin: 0, fontSize: "1.1rem" }}>Ask AI</h2>
          <button className="icon-btn" onClick={onClose} aria-label="Close">
            <X />
          </button>
        </div>
        <p className="section-hint" style={{ marginTop: 0 }}>
          This is exactly what will be sent. It opens in your own AI account; nothing is sent until you choose one.
        </p>
        {isLoading && <p className="muted">Preparing the question…</p>}
        {error && <div className="alert alert--critical">Couldn't prepare the question: {(error as Error).message}</div>}
        {data && (
          <>
            <textarea className="input ask-ai-prompt" readOnly value={prompt} aria-label="Prompt" rows={14} />
            <div className="ask-ai-actions">
              <a className="btn btn--primary btn--sm" href={CLAUDE_URL + encoded} target="_blank" rel="noopener noreferrer">
                Open in Claude
              </a>
              <a className="btn btn--secondary btn--sm" href={CHATGPT_URL + encoded} target="_blank" rel="noopener noreferrer">
                Open in ChatGPT
              </a>
              <button
                className="btn btn--secondary btn--sm"
                onClick={async () => {
                  await copy();
                  window.open(GEMINI_URL, "_blank", "noopener,noreferrer");
                }}
              >
                Copy and open Gemini
              </button>
              <button className="btn btn--ghost btn--sm" onClick={() => void copy()}>
                <Copy size={13} />
                {copied ? "Copied" : "Copy prompt"}
              </button>
            </div>
            <p className="muted" style={{ fontSize: "0.78rem", marginBottom: 0 }}>
              Claude and ChatGPT open with the question filled in. Gemini has no such link: paste it there.
            </p>
          </>
        )}
      </div>
    </div>
  );
}
