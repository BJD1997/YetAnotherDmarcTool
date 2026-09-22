import { act, render } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { useInView } from "./useInView";

let triggerIntersecting: ((isIntersecting: boolean) => void) | null = null;
let realIntersectionObserver: typeof IntersectionObserver | undefined;

class FakeIntersectionObserver {
  constructor(private callback: IntersectionObserverCallback) {
    triggerIntersecting = (isIntersecting: boolean) =>
      this.callback([{ isIntersecting } as IntersectionObserverEntry], this as unknown as IntersectionObserver);
  }
  observe(): void {}
  disconnect(): void {}
  unobserve(): void {}
}

function Probe() {
  const { ref, inView } = useInView<HTMLDivElement>();
  return <div ref={ref} data-testid="target" data-inview={inView} />;
}

describe("useInView", () => {
  beforeEach(() => {
    triggerIntersecting = null;
    realIntersectionObserver = globalThis.IntersectionObserver;
    globalThis.IntersectionObserver = FakeIntersectionObserver as unknown as typeof IntersectionObserver;
  });

  afterEach(() => {
    globalThis.IntersectionObserver = realIntersectionObserver as typeof IntersectionObserver;
  });

  it("stays false until the observed element intersects, then flips true and stays true", () => {
    const { getByTestId } = render(<Probe />);
    expect(getByTestId("target").dataset.inview).toBe("false");

    act(() => triggerIntersecting?.(false));
    expect(getByTestId("target").dataset.inview).toBe("false");

    act(() => triggerIntersecting?.(true));
    expect(getByTestId("target").dataset.inview).toBe("true");
  });

  it("falls back to true immediately when IntersectionObserver is unavailable", () => {
    // @ts-expect-error simulating an environment with no IntersectionObserver support
    globalThis.IntersectionObserver = undefined;
    const { getByTestId } = render(<Probe />);
    expect(getByTestId("target").dataset.inview).toBe("true");
  });
});
