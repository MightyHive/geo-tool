/** Fetch a file with progress callbacks, then trigger a browser download. */

function filenameFromDisposition(header: string | null, fallback: string): string {
  if (!header) return fallback;
  const utf = /filename\*=UTF-8''([^;]+)/i.exec(header);
  if (utf?.[1]) {
    try {
      return decodeURIComponent(utf[1].trim().replace(/^"|"$/g, ""));
    } catch {
      /* fall through */
    }
  }
  const plain = /filename="?([^";]+)"?/i.exec(header);
  return plain?.[1]?.trim() || fallback;
}

export type DownloadProgressUpdate = {
  /** 0–100; may be estimated while the server is still generating the file. */
  percent: number;
  /** True when byte totals are known from Content-Length. */
  determinate: boolean;
};

/**
 * Download ``url`` as a file. Reports progress while waiting for the response
 * (estimated) and while reading the body (real when Content-Length is present).
 */
export async function downloadFileWithProgress(
  url: string,
  fallbackFilename: string,
  onProgress?: (update: DownloadProgressUpdate) => void,
): Promise<void> {
  let softPercent = 4;
  const softTimer = window.setInterval(() => {
    softPercent = Math.min(softPercent + 3, 88);
    onProgress?.({ percent: softPercent, determinate: false });
  }, 450);

  try {
    onProgress?.({ percent: softPercent, determinate: false });
    const res = await fetch(url, { credentials: "include" });
    if (!res.ok) {
      const text = await res.text().catch(() => "");
      throw new Error(text || `Download failed (${res.status})`);
    }

    const filename = filenameFromDisposition(
      res.headers.get("content-disposition"),
      fallbackFilename,
    );
    const total = Number(res.headers.get("content-length") || 0) || null;
    const chunks: Uint8Array[] = [];
    let loaded = 0;

    if (res.body) {
      const reader = res.body.getReader();
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        if (value) {
          chunks.push(value);
          loaded += value.byteLength;
          if (total && total > 0) {
            window.clearInterval(softTimer);
            onProgress?.({
              percent: Math.min(99, Math.round((loaded / total) * 100)),
              determinate: true,
            });
          } else {
            softPercent = Math.min(softPercent + 1, 92);
            onProgress?.({ percent: softPercent, determinate: false });
          }
        }
      }
    } else {
      const buf = new Uint8Array(await res.arrayBuffer());
      chunks.push(buf);
      loaded = buf.byteLength;
    }

    window.clearInterval(softTimer);
    onProgress?.({ percent: 100, determinate: true });

    const blob = new Blob(chunks as BlobPart[], {
      type: res.headers.get("content-type") || "application/octet-stream",
    });
    const objectUrl = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = objectUrl;
    anchor.download = filename;
    anchor.rel = "noopener";
    document.body.appendChild(anchor);
    anchor.click();
    anchor.remove();
    window.setTimeout(() => URL.revokeObjectURL(objectUrl), 2_000);
  } finally {
    window.clearInterval(softTimer);
  }
}

export function downloadProgressLabel(kind: "pdf" | "html" | null): string {
  if (kind === "pdf") return "Download in progress…";
  if (kind === "html") return "Download in progress…";
  return "Download in progress…";
}

/**
 * Enqueue PDF generation, poll until ready, then download the artifact.
 */
export async function downloadPdfViaExportJob(
  auditDirOrSlug: string,
  fallbackFilename: string,
  options?: {
    section?: string;
    onProgress?: (update: DownloadProgressUpdate) => void;
    pollMs?: number;
    timeoutMs?: number;
  },
): Promise<void> {
  const { startPdfExport, fetchPdfExportStatus, reportPdfExportFileUrl } = await import(
    "../api/client"
  );
  const section = options?.section;
  const onProgress = options?.onProgress;
  const pollMs = options?.pollMs ?? 1500;
  const timeoutMs = options?.timeoutMs ?? 15 * 60 * 1000;

  onProgress?.({ percent: 6, determinate: false });
  await startPdfExport(auditDirOrSlug, section);

  const started = Date.now();
  let soft = 8;
  while (Date.now() - started < timeoutMs) {
    const status = await fetchPdfExportStatus(auditDirOrSlug, section);
    if (status.error && status.status === "error") {
      throw new Error(status.error);
    }
    if (status.ready) {
      onProgress?.({ percent: 70, determinate: false });
      await downloadFileWithProgress(
        reportPdfExportFileUrl(auditDirOrSlug, section),
        fallbackFilename,
        (update) => {
          const mapped = 70 + Math.round(update.percent * 0.3);
          onProgress?.({ percent: mapped, determinate: update.determinate });
        },
      );
      onProgress?.({ percent: 100, determinate: true });
      return;
    }
    soft = Math.min(soft + 2, 65);
    onProgress?.({ percent: soft, determinate: false });
    await new Promise((resolve) => window.setTimeout(resolve, pollMs));
  }
  throw new Error("PDF export timed out. Try again in a moment.");
}

