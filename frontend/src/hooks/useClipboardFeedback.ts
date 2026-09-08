import { useEffect, useRef, useState } from "react";

/** Copies text and exposes the same short-lived success state used throughout
 * the UI. The timer is cancelled on unmount and on a subsequent copy. */
export function useClipboardFeedback(resetAfterMs = 2000) {
  const [copied, setCopied] = useState(false);
  const resetTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(
    () => () => {
      if (resetTimer.current !== null) clearTimeout(resetTimer.current);
    },
    [],
  );

  async function copy(text: string): Promise<void> {
    await navigator.clipboard.writeText(text);
    setCopied(true);
    if (resetTimer.current !== null) clearTimeout(resetTimer.current);
    resetTimer.current = setTimeout(() => setCopied(false), resetAfterMs);
  }

  return { copied, copy };
}
