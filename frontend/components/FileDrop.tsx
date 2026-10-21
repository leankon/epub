"use client";

import { useRef, useState } from "react";
import { uploadFile } from "@/lib/api";
import { formatBytes } from "@/lib/format";
import type { DocumentSummary } from "@/lib/types";
import { ErrorBox } from "./ErrorBox";

interface Props {
  accept?: string; // e.g. ".pdf"
  multiple?: boolean;
  label?: string;
  onUploaded: (docs: DocumentSummary[]) => void;
}

interface Pending {
  name: string;
  size: number;
  progress: number;
  error?: unknown;
  doc?: DocumentSummary;
}

export function FileDrop({ accept, multiple = true, label = "Drop files here or choose files", onUploaded }: Props) {
  const input = useRef<HTMLInputElement>(null);
  const [over, setOver] = useState(false);
  const [items, setItems] = useState<Pending[]>([]);

  async function handle(files: FileList | File[]) {
    const list = Array.from(files);
    if (!list.length) return;
    const start = items.length;
    setItems((prev) => [...prev, ...list.map((f) => ({ name: f.name, size: f.size, progress: 0 }))]);
    const done: DocumentSummary[] = [];
    for (let i = 0; i < list.length; i++) {
      const f = list[i]!;
      const idx = start + i;
      try {
        const doc = await uploadFile(f, (p) =>
          setItems((prev) => prev.map((it, k) => (k === idx ? { ...it, progress: p } : it))),
        );
        done.push(doc);
        setItems((prev) => prev.map((it, k) => (k === idx ? { ...it, progress: 1, doc } : it)));
      } catch (e) {
        setItems((prev) => prev.map((it, k) => (k === idx ? { ...it, error: e } : it)));
      }
    }
    if (done.length) onUploaded(done);
  }

  return (
    <div>
      <div
        className={`dropzone${over ? " over" : ""}`}
        onDragOver={(e) => {
          e.preventDefault();
          setOver(true);
        }}
        onDragLeave={() => setOver(false)}
        onDrop={(e) => {
          e.preventDefault();
          setOver(false);
          void handle(e.dataTransfer.files);
        }}
      >
        <p style={{ margin: "0 0 8px" }}>{label}</p>
        <button type="button" onClick={() => input.current?.click()}>
          Choose file{multiple ? "s" : ""}
        </button>
        <input
          ref={input}
          type="file"
          className="sr-only"
          accept={accept}
          multiple={multiple}
          aria-label={label}
          onChange={(e) => {
            if (e.target.files) void handle(e.target.files);
            e.target.value = "";
          }}
        />
        <p className="hint">Files stay on this computer. The type is verified from the file content.</p>
      </div>
      {items.length > 0 && (
        <ul style={{ paddingLeft: 18 }}>
          {items.map((it, k) => (
            <li key={k}>
              {it.name} ({formatBytes(it.size)}){" "}
              {it.error ? null : it.doc ? (
                <span className="badge ok">
                  imported · {it.doc.kind}
                  {it.doc.latest_version?.page_count ? ` · ${it.doc.latest_version.page_count} pages` : ""}
                </span>
              ) : (
                <span className="badge">{it.progress < 1 ? `uploading ${Math.round(it.progress * 100)}%` : "validating…"}</span>
              )}
              {it.doc?.latest_version?.metadata?.warnings?.map((w, i) => (
                <div key={i} className="hint" style={{ color: "var(--warn)" }}>
                  ⚠ {w}
                </div>
              ))}
              {it.error ? <ErrorBox error={it.error} /> : null}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
