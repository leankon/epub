// Client-side page range validation mirroring the backend (backend remains authoritative).

export type RangeResult = { ok: true; groups: number[][] } | { ok: false; error: string };

export function parseRanges(spec: string, pageCount: number): RangeResult {
  if (!spec.trim()) return { ok: false, error: "Enter a page range, e.g. 1-3, 5, 8-" };
  const groups: number[][] = [];
  for (const raw of spec.split(",")) {
    const part = raw.trim();
    if (!part) return { ok: false, error: `Invalid range "${spec}"` };
    if (part === "last" || part === "end") {
      groups.push([pageCount - 1]);
      continue;
    }
    const m = /^(\d+)?\s*(-)?\s*(\d+)?$/.exec(part);
    if (!m || (m[1] === undefined && m[3] === undefined)) return { ok: false, error: `Invalid range "${part}"` };
    const start = m[1] !== undefined ? Number(m[1]) : 1;
    const end = m[2] ? (m[3] !== undefined ? Number(m[3]) : pageCount) : start;
    if (start < 1 || end < 1 || start > pageCount || end > pageCount)
      return { ok: false, error: `"${part}" is outside 1–${pageCount}` };
    if (end < start) return { ok: false, error: `"${part}" is reversed` };
    const g: number[] = [];
    for (let i = start; i <= end; i++) g.push(i - 1);
    groups.push(g);
  }
  return { ok: true, groups };
}
