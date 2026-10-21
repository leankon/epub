"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { ErrorBox } from "@/components/ErrorBox";
import { FileDrop } from "@/components/FileDrop";
import { get } from "@/lib/api";
import { formatBytes, formatDate } from "@/lib/format";
import type { DocumentSummary } from "@/lib/types";

export default function LibraryPage() {
  const [docs, setDocs] = useState<DocumentSummary[]>([]);
  const [total, setTotal] = useState(0);
  const [q, setQ] = useState("");
  const [kind, setKind] = useState("");
  const [error, setError] = useState<unknown>(null);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    try {
      const params = new URLSearchParams({ limit: "500" });
      if (q) params.set("q", q);
      if (kind) params.set("kind", kind);
      const r = await get<{ items: DocumentSummary[]; total: number }>(`/documents?${params}`);
      setDocs(r.items);
      setTotal(r.total);
      setError(null);
    } catch (e) {
      setError(e);
    } finally {
      setLoading(false);
    }
  }, [q, kind]);
  useEffect(() => {
    void load();
  }, [load]);

  return (
    <>
      <h1>Document library</h1>
      <p className="lead">Imported originals are immutable; every operation adds a new version.</p>
      <section className="card">
        <FileDrop onUploaded={() => void load()} label="Import PDFs, EPUBs, Office documents or images" />
      </section>
      <div className="row" style={{ marginBottom: 12 }}>
        <div style={{ flex: 1, minWidth: 200 }}>
          <label htmlFor="q" className="sr-only">
            Search
          </label>
          <input id="q" type="search" placeholder="Search titles" value={q} onChange={(e) => setQ(e.target.value)} />
        </div>
        <label htmlFor="kind" className="sr-only">
          Type
        </label>
        <select id="kind" value={kind} onChange={(e) => setKind(e.target.value)} style={{ width: "auto" }}>
          <option value="">All types</option>
          <option value="pdf">PDF</option>
          <option value="epub">EPUB</option>
          <option value="office">Office</option>
          <option value="image">Image</option>
        </select>
        <span className="muted">{total} document(s)</span>
      </div>
      <ErrorBox error={error} />
      {loading ? (
        <p className="muted">Loading…</p>
      ) : docs.length === 0 ? (
        <p className="muted">No documents. Import a file above.</p>
      ) : (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Title</th>
                <th>Type</th>
                <th>Pages</th>
                <th>Latest size</th>
                <th>Versions</th>
                <th>Added</th>
              </tr>
            </thead>
            <tbody>
              {docs.map((d) => (
                <tr key={d.id}>
                  <td>
                    <Link href={`/library/${d.id}`}>{d.title}</Link>
                    <div className="hint">{d.original_filename}</div>
                    {d.latest_version?.metadata?.warnings?.length ? <span className="badge warn">{d.latest_version.metadata.warnings.length} warning(s)</span> : null}
                  </td>
                  <td>{d.latest_version?.ext ?? d.kind}</td>
                  <td>{d.latest_version?.page_count ?? "—"}</td>
                  <td>{formatBytes(d.latest_version?.size_bytes)}</td>
                  <td>{d.version_count}</td>
                  <td>{formatDate(d.created_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}
