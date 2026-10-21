"use client";

import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { EpubPreview } from "@/components/EpubReport";
import { ErrorBox } from "@/components/ErrorBox";
import { StatusBadge } from "@/components/JobProgress";
import { API, del, downloadUrl, get, patch, thumbUrl } from "@/lib/api";
import { formatBytes, formatDate, opLabel, shortHash } from "@/lib/format";
import type { AuditEvent, DocumentDetail, Version } from "@/lib/types";

interface Comparison {
  identical: boolean;
  size_change_bytes: number;
  size_change_percent: number | null;
  page_count_change: number;
  metadata_differences: Record<string, { a: unknown; b: unknown }>;
  a: Version;
  b: Version;
}

export default function DocumentPage() {
  const { docId } = useParams<{ docId: string }>();
  const router = useRouter();
  const [doc, setDoc] = useState<DocumentDetail | null>(null);
  const [events, setEvents] = useState<AuditEvent[]>([]);
  const [compareSel, setCompareSel] = useState<string[]>([]);
  const [comparison, setComparison] = useState<Comparison | null>(null);
  const [title, setTitle] = useState("");
  const [error, setError] = useState<unknown>(null);
  const [confirmDelete, setConfirmDelete] = useState("");

  const load = useCallback(async () => {
    try {
      const d = await get<DocumentDetail>(`/documents/${docId}`);
      setDoc(d);
      setTitle(d.title);
      setEvents((await get<{ events: AuditEvent[] }>(`/documents/${docId}/history`)).events);
    } catch (e) {
      setError(e);
    }
  }, [docId]);
  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    if (compareSel.length !== 2) {
      setComparison(null);
      return;
    }
    void get<Comparison>(`/versions/compare/${compareSel[0]}/${compareSel[1]}`).then(setComparison).catch(setError);
  }, [compareSel]);

  if (!doc) return error ? <ErrorBox error={error} /> : <p className="muted">Loading…</p>;
  const latest = doc.latest_version;
  const md = latest?.metadata;
  const isPdf = latest?.ext === "pdf";
  const pages = Math.min(latest?.page_count ?? 0, 12);

  return (
    <>
      <p>
        <Link href="/library">← Library</Link>
      </p>
      <div className="row">
        <h1 style={{ margin: 0 }}>{doc.title}</h1>
        <span className="badge">{doc.kind}</span>
      </div>
      <p className="lead">
        Imported {formatDate(doc.created_at)} as <span className="mono">{doc.original_filename}</span>
      </p>
      <ErrorBox error={error} />
      {md?.warnings?.length ? (
        <div className="notice warn">
          <ul>{md.warnings.map((w, i) => <li key={i}>{w}</li>)}</ul>
        </div>
      ) : null}

      <section className="card">
        <div className="row">
          <label htmlFor="title" className="sr-only">
            Title
          </label>
          <input id="title" type="text" value={title} onChange={(e) => setTitle(e.target.value)} style={{ maxWidth: 420 }} />
          <button disabled={!title.trim() || title === doc.title} onClick={async () => { await patch(`/documents/${docId}`, { title }); void load(); }}>
            Rename
          </button>
          <span className="spacer" />
          {latest && (
            <a className="button primary" href={downloadUrl(latest.id)}>
              Download latest (v{latest.version_number})
            </a>
          )}
          <a className="button" href={`${API}/documents/${docId}/export`}>
            Export all versions (ZIP)
          </a>
        </div>
        {isPdf && latest && (
          <div className="row" style={{ marginTop: 12 }}>
            {["organize", "split", "compress", "redact", "watermark", "metadata", "bookmarks", "ocr", "protect", "unlock"].map((t) => (
              <Link key={t} className="button small" href={`/tools/${t}?version=${latest.id}`}>
                {t}
              </Link>
            ))}
            <Link className="button small" href={`/epub?version=${latest.id}`}>
              to EPUB
            </Link>
          </div>
        )}
      </section>

      {isPdf && latest && !md?.encrypted && pages > 0 && (
        <section className="card">
          <h2 style={{ marginTop: 0 }}>Pages (latest version)</h2>
          <div className="pages">
            {Array.from({ length: pages }, (_, i) => (
              <div className="page-tile" key={i}>
                <img src={thumbUrl(latest.id, i, 200)} alt={`Page ${i + 1}`} loading="lazy" />
                {i + 1}
                {md?.page_sizes?.[i] ? <div className="hint">{md.page_sizes[i]![0]}×{md.page_sizes[i]![1]} pt</div> : null}
              </div>
            ))}
          </div>
          {(latest.page_count ?? 0) > pages && <p className="hint">Showing the first {pages} of {latest.page_count} pages.</p>}
        </section>
      )}
      {latest?.ext === "epub" && (
        <section className="card">
          <EpubPreview versionId={latest.id} />
        </section>
      )}

      <section className="card">
        <h2 style={{ marginTop: 0 }}>Version history</h2>
        <p className="hint">Select two versions to compare them.</p>
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Compare</th>
                <th>Version</th>
                <th>Operation</th>
                <th>Pages</th>
                <th>Size</th>
                <th>SHA-256</th>
                <th>Created</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {doc.versions.map((v) => (
                <tr key={v.id}>
                  <td>
                    <input
                      type="checkbox"
                      aria-label={`Compare version ${v.version_number}`}
                      disabled={v.expired || (compareSel.length >= 2 && !compareSel.includes(v.id))}
                      checked={compareSel.includes(v.id)}
                      onChange={(e) => setCompareSel(e.target.checked ? [...compareSel, v.id] : compareSel.filter((x) => x !== v.id))}
                    />
                  </td>
                  <td>
                    v{v.version_number} {v.is_original && <span className="badge ok">original · immutable</span>}
                    {v.expired && <span className="badge warn">expired</span>}
                    <div className="hint">{v.label}</div>
                  </td>
                  <td>
                    {opLabel(v.operation)}
                    {v.parent_version_id && (
                      <div className="hint">from v{doc.versions.find((x) => x.id === v.parent_version_id)?.version_number ?? "other document"}</div>
                    )}
                  </td>
                  <td>{v.page_count ?? "—"}</td>
                  <td>{formatBytes(v.size_bytes)}</td>
                  <td className="mono" title={v.sha256}>
                    {shortHash(v.sha256)}
                  </td>
                  <td>{formatDate(v.created_at)}</td>
                  <td>
                    {!v.expired && v.validation_passed !== false && <a href={downloadUrl(v.id)}>Download</a>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {comparison && (
          <div className="card" style={{ marginTop: 12 }}>
            <h3 style={{ marginTop: 0 }}>
              v{comparison.a.version_number} → v{comparison.b.version_number}
            </h3>
            <dl className="kv">
              <dt>Identical content</dt><dd>{comparison.identical ? "yes (same hash)" : "no"}</dd>
              <dt>Size change</dt><dd>{formatBytes(comparison.size_change_bytes)} ({comparison.size_change_percent ?? "—"}%)</dd>
              <dt>Page count change</dt><dd>{comparison.page_count_change}</dd>
              {Object.entries(comparison.metadata_differences).map(([k, d]) => (
                <div key={k} style={{ display: "contents" }}>
                  <dt>{k}</dt>
                  <dd>{String(d.a ?? "—")} → {String(d.b ?? "—")}</dd>
                </div>
              ))}
            </dl>
            {comparison.a.ext === "pdf" && comparison.b.ext === "pdf" && (
              <div className="row" style={{ marginTop: 8 }}>
                <img src={thumbUrl(comparison.a.id, 0, 260)} alt="First page of the first version" style={{ border: "1px solid var(--border)", background: "#fff" }} />
                <img src={thumbUrl(comparison.b.id, 0, 260)} alt="First page of the second version" style={{ border: "1px solid var(--border)", background: "#fff" }} />
              </div>
            )}
          </div>
        )}
      </section>

      <section className="card">
        <h2 style={{ marginTop: 0 }}>Jobs</h2>
        {doc.jobs.length === 0 ? <p className="muted">None.</p> : (
          <ul>
            {doc.jobs.map((j) => (
              <li key={j.id}>
                <Link href={`/history?job=${j.id}`}>{opLabel(j.kind)}</Link> <StatusBadge status={j.status} /> {formatDate(j.created_at)}
              </li>
            ))}
          </ul>
        )}
        <h2>Audit trail</h2>
        <div className="table-wrap" style={{ maxHeight: 320, overflowY: "auto" }}>
          <table>
            <thead>
              <tr>
                <th>#</th>
                <th>Time</th>
                <th>Event</th>
                <th>Status</th>
                <th>Input hash</th>
                <th>Output hash</th>
              </tr>
            </thead>
            <tbody>
              {events.map((e) => (
                <tr key={e.seq}>
                  <td>{e.seq}</td>
                  <td>{formatDate(e.ts)}</td>
                  <td>{e.event_type}</td>
                  <td>{e.status}{e.delivery_outcome ? ` · ${e.delivery_outcome}` : ""}</td>
                  <td className="mono">{shortHash(e.input_sha256)}</td>
                  <td className="mono">{shortHash(e.output_sha256)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      <section className="card">
        <h2 style={{ marginTop: 0 }}>Delete document</h2>
        <p>
          Removes all {doc.versions.length} version(s), OCR sessions and previews of this document from application
          storage. The audit log keeps hashes of what was deleted (not the content). Ordinary deletion may not guarantee
          forensic erasure on SSDs, snapshots or backups.
        </p>
        <div className="row">
          <label htmlFor="cd">Type DELETE to confirm</label>
          <input id="cd" type="text" value={confirmDelete} onChange={(e) => setConfirmDelete(e.target.value)} style={{ maxWidth: 160 }} />
          <button
            className="danger"
            disabled={confirmDelete !== "DELETE"}
            onClick={async () => {
              try {
                await del(`/documents/${docId}?confirm=true`);
                router.push("/library");
              } catch (e) {
                setError(e);
              }
            }}
          >
            Delete permanently
          </button>
        </div>
      </section>
    </>
  );
}
