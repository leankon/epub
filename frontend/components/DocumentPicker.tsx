"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { get } from "@/lib/api";
import { formatBytes, formatDate } from "@/lib/format";
import type { DocumentSummary } from "@/lib/types";
import { ErrorBox } from "./ErrorBox";
import { FileDrop } from "./FileDrop";

interface Props {
  exts: string[]; // accepted version extensions, e.g. ["pdf"]
  multiple?: boolean;
  selected: string[]; // version ids, in order
  onChange: (versionIds: string[], docs: DocumentSummary[]) => void;
  uploadAccept?: string;
}

/** Choose the latest version of documents from the library, or import new files. */
export function DocumentPicker({ exts, multiple = false, selected, onChange, uploadAccept }: Props) {
  const [docs, setDocs] = useState<DocumentSummary[]>([]);
  const [q, setQ] = useState("");
  const [error, setError] = useState<unknown>(null);

  const load = useCallback(async () => {
    try {
      const r = await get<{ items: DocumentSummary[] }>(`/documents?limit=500`);
      setDocs(r.items);
    } catch (e) {
      setError(e);
    }
  }, []);
  useEffect(() => {
    void load();
  }, [load]);

  const eligible = useMemo(
    () =>
      docs.filter(
        (d) =>
          d.latest_version &&
          exts.includes(d.latest_version.ext) &&
          (!q || d.title.toLowerCase().includes(q.toLowerCase())),
      ),
    [docs, exts, q],
  );
  const byVersion = useMemo(() => new Map(docs.map((d) => [d.latest_version?.id ?? "", d])), [docs]);

  function toggle(d: DocumentSummary) {
    const vid = d.latest_version!.id;
    let next: string[];
    if (multiple) next = selected.includes(vid) ? selected.filter((x) => x !== vid) : [...selected, vid];
    else next = selected[0] === vid ? [] : [vid];
    onChange(next, next.map((v) => byVersion.get(v)).filter(Boolean) as DocumentSummary[]);
  }

  return (
    <div className="stack">
      <FileDrop
        accept={uploadAccept ?? exts.map((e) => "." + e).join(",")}
        multiple={multiple}
        onUploaded={async (newDocs) => {
          await load();
          const vids = newDocs.map((d) => d.latest_version?.id).filter((v): v is string => !!v);
          const usable = newDocs.filter((d) => d.latest_version && exts.includes(d.latest_version.ext));
          const nextSel = multiple ? [...selected, ...vids.filter((v) => usable.some((u) => u.latest_version?.id === v))] : vids.slice(0, 1);
          onChange(nextSel, [...(multiple ? (selected.map((v) => byVersion.get(v)).filter(Boolean) as DocumentSummary[]) : []), ...usable]);
        }}
      />
      <ErrorBox error={error} />
      <div className="field">
        <label htmlFor="docsearch">Or choose from the library ({exts.join(", ")})</label>
        <input id="docsearch" type="search" value={q} onChange={(e) => setQ(e.target.value)} placeholder="Filter by title" />
      </div>
      {eligible.length === 0 ? (
        <p className="muted">No matching documents yet. Import a file above.</p>
      ) : (
        <div className="table-wrap" style={{ maxHeight: 280, overflowY: "auto" }}>
          <table>
            <thead>
              <tr>
                <th scope="col">
                  <span className="sr-only">Select</span>
                </th>
                <th scope="col">Title</th>
                <th scope="col">Latest version</th>
                <th scope="col">Pages</th>
                <th scope="col">Size</th>
                <th scope="col">Added</th>
              </tr>
            </thead>
            <tbody>
              {eligible.map((d) => {
                const v = d.latest_version!;
                const pos = selected.indexOf(v.id);
                return (
                  <tr key={d.id}>
                    <td>
                      <input
                        type={multiple ? "checkbox" : "radio"}
                        name="docpick"
                        checked={pos >= 0}
                        onChange={() => toggle(d)}
                        aria-label={`Select ${d.title}`}
                      />
                      {multiple && pos >= 0 && <span className="badge">{pos + 1}</span>}
                    </td>
                    <td>
                      {d.title}
                      {v.metadata?.encrypted && <span className="badge warn"> encrypted</span>}
                      {v.metadata?.has_signatures && <span className="badge warn"> signed</span>}
                    </td>
                    <td>
                      v{v.version_number} · {v.label}
                    </td>
                    <td>{v.page_count ?? "—"}</td>
                    <td>{formatBytes(v.size_bytes)}</td>
                    <td>{formatDate(d.created_at)}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
