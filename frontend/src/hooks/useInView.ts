import { useEffect, useRef, useState } from "react";

/** True once the returned ref's element has intersected the viewport at
 * least once; stays true after that (a one-shot trigger for lazy-loading a
 * section once it's been scrolled near, not a live visibility toggle).
 * `rootMargin` defaults to loading slightly ahead of the element actually
 * becoming visible. Falls back to true immediately where IntersectionObserver
 * isn't available, rather than leaving the section stuck unloaded. */
export function useInView<T extends Element>(rootMargin = "200px") {
  const ref = useRef<T | null>(null);
  const [inView, setInView] = useState(false);

  useEffect(() => {
    if (inView) return;
    const node = ref.current;
    if (!node) return;
    if (typeof IntersectionObserver === "undefined") {
      setInView(true);
      return;
    }
    const observer = new IntersectionObserver(([entry]) => {
      if (entry.isIntersecting) setInView(true);
    }, { rootMargin });
    observer.observe(node);
    return () => observer.disconnect();
  }, [inView, rootMargin]);

  return { ref, inView };
}
