import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useId,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import { createPortal } from "react-dom";
import { useSoftProgress } from "../hooks/useSoftProgress";
import { cn } from "../lib/utils";

type PageLoadingContextValue = {
  setLoading: (key: string, loading: boolean) => void;
  isLoading: boolean;
};

const PageLoadingContext = createContext<PageLoadingContextValue | null>(null);

export function PageLoadingProvider({ children }: { children: ReactNode }) {
  const [keys, setKeys] = useState<Set<string>>(() => new Set());

  const setLoading = useCallback((key: string, loading: boolean) => {
    setKeys((prev) => {
      const has = prev.has(key);
      if (loading && has) return prev;
      if (!loading && !has) return prev;
      const next = new Set(prev);
      if (loading) next.add(key);
      else next.delete(key);
      return next;
    });
  }, []);

  const isLoading = keys.size > 0;
  const value = useMemo(
    () => ({ setLoading, isLoading }),
    [isLoading, setLoading],
  );

  return (
    <PageLoadingContext.Provider value={value}>
      {children}
      <PageLoadingBar active={isLoading} />
    </PageLoadingContext.Provider>
  );
}

/** Register a loading signal for the global top bar (clears on unmount). */
export function usePageLoadingSignal(loading: boolean, key?: string) {
  const ctx = useContext(PageLoadingContext);
  const autoKey = useId();
  const signalKey = key ?? autoKey;
  // Depend on the stable setter, not the whole context value. The provider
  // recreates ``value`` whenever keys change; including ``ctx`` here caused
  // Maximum update depth (#185) when a section remounted into a loading UI
  // (cleanup cleared the key, the new value retriggered the effect, repeat).
  const setLoading = ctx?.setLoading;

  useEffect(() => {
    if (!setLoading) return;
    setLoading(signalKey, loading);
    return () => setLoading(signalKey, false);
  }, [setLoading, signalKey, loading]);
}

/**
 * Thin fixed top progress bar (YouTube-style). Soft progress while active;
 * completes and fades when loading ends.
 */
export function PageLoadingBar({
  active,
  className,
}: {
  active: boolean;
  className?: string;
}) {
  const { percent, visible } = useSoftProgress(active);

  if (typeof document === "undefined" || !visible) return null;

  return createPortal(
    <div
      className={cn(
        "pointer-events-none fixed inset-x-0 top-0 z-[200] h-[3px] overflow-hidden",
        className,
      )}
      role="progressbar"
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={Math.round(percent)}
      aria-label="Page loading"
    >
      <div
        className="h-full origin-left bg-[#0984e3] shadow-[0_0_8px_rgba(9,132,227,0.55)] transition-[width] duration-300 ease-out"
        style={{ width: `${percent}%` }}
      />
    </div>,
    document.body,
  );
}

/**
 * In-content loading state for dashboard / report sections.
 * Also drives the global top loading bar when inside PageLoadingProvider.
 */
export function PageLoading({
  label = "Loading…",
  className,
  compact = false,
}: {
  label?: string;
  className?: string;
  compact?: boolean;
}) {
  usePageLoadingSignal(true);

  return (
    <div
      className={cn(
        "flex flex-col items-center justify-center gap-4",
        compact ? "py-10" : "min-h-[40vh] py-16",
        className,
      )}
      role="status"
      aria-live="polite"
      aria-busy="true"
    >
      <div className="w-full max-w-[220px]">
        <div className="h-1.5 overflow-hidden rounded-full bg-gray-200/90">
          <div className="page-loading-bar-indeterminate h-full w-1/3 rounded-full bg-[#0984e3]" />
        </div>
      </div>
      <p className="text-sm text-gray-500">{label}</p>
    </div>
  );
}
