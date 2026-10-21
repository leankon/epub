"use client";

import { useRef, useState } from "react";
import { thumbUrl } from "@/lib/api";

export interface Area {
  page: number;
  rect: [number, number, number, number]; // PDF points, user space, origin bottom-left
}

interface Props {
  versionId: string;
  pageCount: number;
  pageSizes: [number, number, number][] | null; // [width, height, rotate] in points
  areas: Area[];
  onChange: (areas: Area[]) => void;
}

/** Map a rectangle drawn on the displayed (rotated) page image to unrotated PDF user space. */
export function displayToPdf(
  x0: number, y0: number, x1: number, y1: number, // fractions of displayed image, top-left origin
  w: number, h: number, rot: number,
): [number, number, number, number] {
  const pts = [[x0, y0], [x1, y1]].map(([fx, fy]) => {
    const u = fx!, v = fy!;
    switch (((rot % 360) + 360) % 360) {
      case 90:
        return [v * w, u * h];
      case 180:
        return [(1 - u) * w, v * h];
      case 270:
        return [(1 - v) * w, (1 - u) * h];
      default:
        return [u * w, (1 - v) * h];
    }
  });
  const xs = pts.map((p) => p[0]!), ys = pts.map((p) => p[1]!);
  return [Math.min(...xs), Math.min(...ys), Math.max(...xs), Math.max(...ys)].map((n) => Math.round(n * 100) / 100) as [number, number, number, number];
}

export function pdfToDisplay(r: [number, number, number, number], w: number, h: number, rot: number) {
  const corners = [[r[0], r[1]], [r[2], r[3]]].map(([x, y]) => {
    const px = x!, py = y!;
    switch (((rot % 360) + 360) % 360) {
      case 90:
        return [py / h, px / w];
      case 180:
        return [1 - px / w, py / h];
      case 270:
        return [1 - py / h, 1 - px / w];
      default:
        return [px / w, 1 - py / h];
    }
  });
  const xs = corners.map((c) => c[0]!), ys = corners.map((c) => c[1]!);
  return { left: Math.min(...xs), top: Math.min(...ys), width: Math.abs(xs[1]! - xs[0]!), height: Math.abs(ys[1]! - ys[0]!) };
}

export function RedactEditor({ versionId, pageCount, pageSizes, areas, onChange }: Props) {
  const [page, setPage] = useState(0);
  const [start, setStart] = useState<[number, number] | null>(null);
  const [cur, setCur] = useState<[number, number] | null>(null);
  const box = useRef<HTMLDivElement>(null);
  const size = pageSizes?.[page] ?? [612, 792, 0];
  const [w, h, rot] = size;

  function frac(e: React.MouseEvent): [number, number] {
    const r = box.current!.getBoundingClientRect();
    return [Math.min(1, Math.max(0, (e.clientX - r.left) / r.width)), Math.min(1, Math.max(0, (e.clientY - r.top) / r.height))];
  }

  return (
    <div>
      <div className="row" style={{ marginBottom: 8 }}>
        <button type="button" disabled={page === 0} onClick={() => setPage(page - 1)}>
          ← Previous page
        </button>
        <span>
          Page {page + 1} of {pageCount}
        </span>
        <button type="button" disabled={page >= pageCount - 1} onClick={() => setPage(page + 1)}>
          Next page →
        </button>
        <span className="muted">Drag on the page to mark an area. {areas.length} area(s) marked.</span>
      </div>
      <div
        ref={box}
        className="redact-canvas"
        onMouseDown={(e) => {
          setStart(frac(e));
          setCur(frac(e));
        }}
        onMouseMove={(e) => start && setCur(frac(e))}
        onMouseUp={() => {
          if (start && cur && Math.abs(cur[0] - start[0]) > 0.005 && Math.abs(cur[1] - start[1]) > 0.005) {
            const rect = displayToPdf(
              Math.min(start[0], cur[0]), Math.min(start[1], cur[1]), Math.max(start[0], cur[0]), Math.max(start[1], cur[1]), w, h, rot,
            );
            onChange([...areas, { page, rect }]);
          }
          setStart(null);
          setCur(null);
        }}
      >
        <img src={thumbUrl(versionId, page, 900)} alt={`Page ${page + 1} for marking redactions`} draggable={false} style={{ width: 640 }} />
        {areas
          .map((a, i) => ({ a, i }))
          .filter(({ a }) => a.page === page)
          .map(({ a, i }) => {
            const d = pdfToDisplay(a.rect, w, h, rot);
            return (
              <div
                key={i}
                className="redact-rect"
                style={{ left: `${d.left * 100}%`, top: `${d.top * 100}%`, width: `${d.width * 100}%`, height: `${d.height * 100}%` }}
                title="Redaction area"
              />
            );
          })}
        {start && cur && (
          <div
            className="redact-rect"
            style={{
              left: `${Math.min(start[0], cur[0]) * 100}%`,
              top: `${Math.min(start[1], cur[1]) * 100}%`,
              width: `${Math.abs(cur[0] - start[0]) * 100}%`,
              height: `${Math.abs(cur[1] - start[1]) * 100}%`,
              opacity: 0.5,
            }}
          />
        )}
      </div>
      {areas.length > 0 && (
        <ul>
          {areas.map((a, i) => (
            <li key={i}>
              Page {a.page + 1}: [{a.rect.map((n) => n.toFixed(0)).join(", ")}] pt{" "}
              <button type="button" className="small" onClick={() => onChange(areas.filter((_, k) => k !== i))}>
                Remove
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
