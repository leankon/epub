export function formatBytes(n: number | null | undefined): string {
  if (n === null || n === undefined || Number.isNaN(n)) return "—";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let v = Math.abs(n);
  let i = 0;
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024;
    i++;
  }
  const s = i === 0 ? String(v) : v.toFixed(v < 10 ? 2 : 1);
  return `${n < 0 ? "-" : ""}${s} ${units[i]}`;
}

export function formatDate(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  return d.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
}

export function formatDuration(ms: number | null | undefined): string {
  if (ms === null || ms === undefined) return "—";
  if (ms < 1000) return `${ms} ms`;
  const s = ms / 1000;
  if (s < 60) return `${s.toFixed(1)} s`;
  const m = Math.floor(s / 60);
  return `${m} min ${Math.round(s % 60)} s`;
}

export const shortHash = (h: string | null | undefined) => (h ? `${h.slice(0, 12)}…` : "—");

/** Percentage only when the backend reports a real total; otherwise null (indeterminate). */
export function progressPercent(current: number | null | undefined, total: number | null | undefined): number | null {
  if (current === null || current === undefined || !total || total <= 0) return null;
  return Math.max(0, Math.min(100, Math.round((100 * current) / total)));
}

export const OPERATION_LABELS: Record<string, string> = {
  merge: "Merge",
  split: "Split",
  organize: "Organize pages",
  rotate: "Rotate",
  compress: "Compress",
  watermark: "Watermark",
  page_numbers: "Page numbers",
  redact: "Redact",
  metadata: "Metadata",
  bookmarks: "Bookmarks",
  protect: "Protect",
  unlock: "Unlock",
  extract_images: "Extract images",
  pdf_to_images: "PDF to images",
  images_to_pdf: "Images to PDF",
  ocr_pdf: "Searchable PDF (OCR)",
  ocr_analyze: "OCR analysis",
  pdf_to_epub: "PDF to EPUB",
  epub_validate: "Validate EPUB",
  epub_to_pdf: "EPUB to PDF",
  office_to_pdf: "Office to PDF",
  backup: "Backup",
  import: "Import",
};

export const opLabel = (k: string) => OPERATION_LABELS[k] ?? k;
