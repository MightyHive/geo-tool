import { useEffect, useRef, useState, type ReactNode } from "react";

/**
 * Delays mounting chart children until after first paint (and optionally until
 * near the viewport) so overview scorecards are not blocked by history fetches
 * or Recharts mount cost.
 */
export function DeferredChart({
  children,
  fallback = null,
  rootMargin = "240px",
  /** When false, only wait for after-paint (use for above-the-fold charts). */
  waitUntilVisible = true,
}: {
  children: ReactNode;
  fallback?: ReactNode;
  rootMargin?: string;
  waitUntilVisible?: boolean;
}) {
  const hostRef = useRef<HTMLDivElement | null>(null);
  const [afterPaint, setAfterPaint] = useState(false);
  const [visible, setVisible] = useState(!waitUntilVisible);

  useEffect(() => {
    let cancelled = false;
    const ric = window.requestIdleCallback?.bind(window);
    if (ric) {
      const id = ric(() => {
        if (!cancelled) setAfterPaint(true);
      }, { timeout: 400 });
      return () => {
        cancelled = true;
        window.cancelIdleCallback?.(id);
      };
    }
    const timer = window.setTimeout(() => {
      if (!cancelled) setAfterPaint(true);
    }, 0);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, []);

  useEffect(() => {
    if (!waitUntilVisible || !afterPaint) return;
    const node = hostRef.current;
    if (!node) return;
    if (typeof IntersectionObserver === "undefined") {
      setVisible(true);
      return;
    }
    const observer = new IntersectionObserver(
      (entries) => {
        if (entries.some((entry) => entry.isIntersecting)) {
          setVisible(true);
          observer.disconnect();
        }
      },
      { rootMargin, threshold: 0.01 },
    );
    observer.observe(node);
    return () => observer.disconnect();
  }, [afterPaint, rootMargin, waitUntilVisible]);

  const ready = afterPaint && visible;

  return (
    <div ref={hostRef} className="min-h-[1px]">
      {ready ? children : fallback}
    </div>
  );
}
