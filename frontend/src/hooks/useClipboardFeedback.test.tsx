import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useClipboardFeedback } from "./useClipboardFeedback";

describe("useClipboardFeedback", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: { writeText: vi.fn().mockResolvedValue(undefined) },
    });
  });

  afterEach(() => vi.useRealTimers());

  it("copies text and resets its success feedback", async () => {
    const { result } = renderHook(() => useClipboardFeedback(100));

    await act(() => result.current.copy("value to copy"));
    expect(navigator.clipboard.writeText).toHaveBeenCalledWith("value to copy");
    expect(result.current.copied).toBe(true);

    act(() => vi.advanceTimersByTime(100));
    expect(result.current.copied).toBe(false);
  });
});
