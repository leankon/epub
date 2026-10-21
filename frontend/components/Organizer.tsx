"use client";

import { useState } from "react";
import { thumbUrl } from "@/lib/api";
import { duplicate, initialPages, move, remove, rotate, rotateAll, type PageItem } from "@/lib/organizer";

interface Props {
  versionId: string;
  pageCount: number;
  items: PageItem[];
  onChange: (items: PageItem[]) => void;
}

/** Page-thumbnail organizer: drag-and-drop (mouse) plus buttons for keyboard users. */
export function Organizer({ versionId, pageCount, items, onChange }: Props) {
  const [drag, setDrag] = useState<number | null>(null);
  const [over, setOver] = useState<number | null>(null);
  const [seq, setSeq] = useState(1);
  return (
    <div>
      <div className="row" style={{ marginBottom: 8 }}>
        <button type="button" onClick={() => onChange(rotateAll(items, -90))}>
          ⟲ Rotate all left
        </button>
        <button type="button" onClick={() => onChange(rotateAll(items, 90))}>
          ⟳ Rotate all right
        </button>
        <button type="button" onClick={() => onChange(initialPages(pageCount))}>
          Reset
        </button>
        <span className="muted">{items.length} page(s) in the new version</span>
      </div>
      <ol className="pages" aria-label="Pages (drag to reorder)" style={{ listStyle: "none", padding: 0 }}>
        {items.map((it, i) => (
          <li
            key={it.key}
            className={`page-tile${drag === i ? " dragging" : ""}${over === i ? " over" : ""}`}
            draggable
            onDragStart={(e) => {
              setDrag(i);
              e.dataTransfer.effectAllowed = "move";
            }}
            onDragOver={(e) => {
              e.preventDefault();
              setOver(i);
            }}
            onDragLeave={() => setOver(null)}
            onDrop={(e) => {
              e.preventDefault();
              if (drag !== null) onChange(move(items, drag, i));
              setDrag(null);
              setOver(null);
            }}
            onDragEnd={() => {
              setDrag(null);
              setOver(null);
            }}
          >
            <img
              src={thumbUrl(versionId, it.page, 200)}
              alt={`Page ${it.page + 1}`}
              style={{ transform: `rotate(${it.rotate}deg)` }}
              loading="lazy"
            />
            <div>
              #{i + 1} · page {it.page + 1}
              {it.rotate ? ` · ${it.rotate}°` : ""}
            </div>
            <div className="row">
              <button type="button" className="small" aria-label={`Move page ${i + 1} left`} disabled={i === 0} onClick={() => onChange(move(items, i, i - 1))}>
                ←
              </button>
              <button type="button" className="small" aria-label={`Move page ${i + 1} right`} disabled={i === items.length - 1} onClick={() => onChange(move(items, i, i + 1))}>
                →
              </button>
              <button type="button" className="small" aria-label={`Rotate page ${i + 1} left`} onClick={() => onChange(rotate(items, i, -90))}>
                ⟲
              </button>
              <button type="button" className="small" aria-label={`Rotate page ${i + 1} right`} onClick={() => onChange(rotate(items, i, 90))}>
                ⟳
              </button>
            </div>
            <div className="row">
              <button
                type="button"
                className="small"
                aria-label={`Duplicate page ${i + 1}`}
                onClick={() => {
                  onChange(duplicate(items, i, seq));
                  setSeq(seq + 1);
                }}
              >
                Duplicate
              </button>
              <button type="button" className="small danger" aria-label={`Remove page ${i + 1}`} disabled={items.length <= 1} onClick={() => onChange(remove(items, i))}>
                Remove
              </button>
            </div>
          </li>
        ))}
      </ol>
    </div>
  );
}
