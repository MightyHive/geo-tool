/**
 * Window long table bodies with @tanstack/react-virtual (padding-row technique
 * keeps real <tr> elements and sticky thead).
 */
import { useRef, type ReactElement, type ReactNode } from "react";
import { useVirtualizer } from "@tanstack/react-virtual";

export const DEFAULT_VIRTUALIZE_ROW_THRESHOLD = 40;

/**
 * Self-contained virtualized table shell (thead + body).
 * When ``rows.length < threshold``, renders a normal scrollable table.
 */
export function VirtualScrollTable({
  head,
  rows,
  colSpan,
  estimateSize = 48,
  threshold = DEFAULT_VIRTUALIZE_ROW_THRESHOLD,
  tableClassName = "w-full text-sm min-w-[680px]",
  empty,
}: {
  head: ReactNode;
  rows: ReactNode[];
  colSpan: number;
  estimateSize?: number;
  threshold?: number;
  tableClassName?: string;
  empty?: ReactNode;
}) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const shouldVirtualize = rows.length >= threshold;

  const virtualizer = useVirtualizer({
    count: shouldVirtualize ? rows.length : 0,
    getScrollElement: () => scrollRef.current,
    estimateSize: () => estimateSize,
    overscan: 12,
  });

  let body: ReactNode = null;
  if (rows.length === 0) {
    body = empty ?? null;
  } else if (!shouldVirtualize) {
    body = rows;
  } else {
    const items = virtualizer.getVirtualItems();
    const padTop = items.length > 0 ? items[0]!.start : 0;
    const last = items.length > 0 ? items[items.length - 1]! : null;
    const padBottom = last ? virtualizer.getTotalSize() - last.end : 0;
    body = (
      <>
        {padTop > 0 ? (
          <tr aria-hidden="true">
            <td colSpan={colSpan} style={{ height: padTop, padding: 0, border: "none" }} />
          </tr>
        ) : null}
        {items.map((v) => rows[v.index] as ReactElement)}
        {padBottom > 0 ? (
          <tr aria-hidden="true">
            <td colSpan={colSpan} style={{ height: padBottom, padding: 0, border: "none" }} />
          </tr>
        ) : null}
      </>
    );
  }

  return (
    <div
      ref={scrollRef}
      className={
        shouldVirtualize
          ? "max-h-[min(70vh,720px)] overflow-auto"
          : "overflow-x-auto"
      }
    >
      <table className={tableClassName}>
        <thead className={shouldVirtualize ? "sticky top-0 z-10 bg-gray-50" : undefined}>
          {head}
        </thead>
        <tbody>{body}</tbody>
      </table>
    </div>
  );
}
