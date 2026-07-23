import { useEffect, useRef, useState } from "react";

/**
 * Soft (indeterminate) progress that creeps toward ~92% while `active`,
 * then snaps to 100% and clears shortly after `active` becomes false.
 */
export function useSoftProgress(active: boolean): { percent: number; visible: boolean } {
  const [percent, setPercent] = useState(0);
  const [visible, setVisible] = useState(false);
  const hideTimer = useRef<ReturnType<typeof window.setTimeout> | null>(null);
  const wasActive = useRef(false);

  useEffect(() => {
    if (hideTimer.current) {
      window.clearTimeout(hideTimer.current);
      hideTimer.current = null;
    }

    if (active) {
      wasActive.current = true;
      setVisible(true);
      setPercent((p) => (p > 0 && p < 100 ? Math.min(p, 40) : 8));
      const tick = window.setInterval(() => {
        setPercent((p) => {
          if (p >= 92) return p;
          const step = p < 40 ? 4 + Math.random() * 5 : 1.5 + Math.random() * 2.5;
          return Math.min(92, p + step);
        });
      }, 380);
      return () => window.clearInterval(tick);
    }

    if (wasActive.current) {
      wasActive.current = false;
      setPercent(100);
      hideTimer.current = window.setTimeout(() => {
        setVisible(false);
        setPercent(0);
        hideTimer.current = null;
      }, 280);
    }

    return () => {
      if (hideTimer.current) {
        window.clearTimeout(hideTimer.current);
        hideTimer.current = null;
      }
    };
  }, [active]);

  return { percent, visible };
}
