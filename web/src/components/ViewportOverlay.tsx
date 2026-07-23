import { useEffect, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { cn } from "../lib/utils";

/**
 * Full-viewport modal shell that always appears in the visible window.
 * Portals to document.body so nested overflow/scroll containers cannot
 * pin the overlay to the top of the report section.
 */
export function ViewportOverlay({
  children,
  onClose,
  labelledBy,
  backdropOpacity = 0.55,
  className,
}: {
  children: ReactNode;
  onClose: () => void;
  labelledBy?: string;
  backdropOpacity?: number;
  className?: string;
}) {
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);

    const previousBodyOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";

    const scroller = document.querySelector(".report-section-body") as HTMLElement | null;
    const previousScrollerOverflow = scroller?.style.overflow ?? "";
    if (scroller) scroller.style.overflow = "hidden";

    return () => {
      window.removeEventListener("keydown", onKey);
      document.body.style.overflow = previousBodyOverflow;
      if (scroller) scroller.style.overflow = previousScrollerOverflow;
    };
  }, [onClose]);

  if (typeof document === "undefined") return null;

  return createPortal(
    <div
      className={cn(
        "fixed inset-0 z-[100] flex items-center justify-center p-4 sm:p-6",
        className,
      )}
      style={{ background: `rgba(0,0,0,${backdropOpacity})` }}
      onClick={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
      role="presentation"
    >
      <div
        className="flex max-h-[min(92vh,920px)] w-full items-stretch justify-center"
        role="dialog"
        aria-modal="true"
        aria-labelledby={labelledBy}
      >
        {children}
      </div>
    </div>,
    document.body,
  );
}
