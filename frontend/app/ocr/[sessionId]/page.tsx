"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ErrorBox } from "@/components/ErrorBox";
import { JobProgress, StatusBadge } from "@/components/JobProgress";
import { API, get, post, put } from "@/lib/api";
import type { OcrPage, OcrSession, Region } from "@/lib/types";

const ROLES = ["paragraph", "heading", "caption", "footnote", "list_item", "marginal", "header", "footer", "page_number", "figure", "table", "formula"];
const TEXT_ROLES = new Set(["paragraph", "heading", "caption", "footnote", "list_item", "marginal", "header", "footer", "page_number", "formula"]);
const SKIPPED = new Set(["header", "footer", "page_number"]);

type Edit = { corrected_text?: string; role?: string; excluded?: boolean; heading_level?: number; revert?: boolean };

export default function OcrWorkspace() {
  const { sessionId } = useParams<{ sessionId: string }>();
  const [session, setSession] = useState<OcrSession | null>(null);
  const [pageIndex, setPageIndex] = useState(0);
  const [page, setPage] = useState<OcrPage | null>(null);
  const [edits, setEdits] = useState<Record<string, Edit>>({});
  const [active, setActive] = useState<string | null>(null);
  const [zoom, setZoom] = useState(100);
  const [imageKind, setImageKind] = useState<"processed" | "source">("processed");
  const [error, setError] = useState<unknown>(null);
  const [saving, setSaving] = useState(false);
  const [jobId, setJobId] = useState<string | null>(null);
  const [psm, setPsm] = useState(3);
  const [threshold, setThreshold] = useState("auto");
  const regionRefs = useRef<Record<string, HTMLDivElement | null>>({});

  const loadSession = useCallback(async () => {
    try {
      const s = await get<OcrSession>(`/ocr/sessions/${sessionId}`);
      setSession(s);
      if (s.job && (s.job.status === "queued" || s.job.status === "running")) setJobId(s.job.id);
    } catch (e) {
      setError(e);
    }
  }, [sessionId]);

  const loadPage = useCallback(async () => {
    try {
      setPage(await get<OcrPage>(`/ocr/sessions/${sessionId}/pages/${pageIndex}`));
      setEdits({});
    } catch {
      setPage(null);
    }
  }, [sessionId, pageIndex]);

  useEffect(() => {
    void loadSession();
  }, [loadSession]);
  useEffect(() => {
    void loadPage();
  }, [loadPage]);
  useEffect(() => {
    if (session?.status !== "processing") return;
    const t = setInterval(() => void loadSession(), 2000);
    return () => clearInterval(t);
  }, [session?.status, loadSession]);

  const dirty = Object.keys(edits).length > 0;
  const regions = useMemo(() => (page?.regions ?? []).slice().sort((a, b) => a.order - b.order), [page]);
  const lowIds = regions.filter((r) => r.low_confidence && r.corrected_text === null && !edits[r.id]?.corrected_text).map((r) => r.id);

  function value(r: Region): string {
    const e = edits[r.id];
    if (e?.corrected_text !== undefined) return e.corrected_text;
    return r.corrected_text ?? r.text;
  }

  function edit(id: string, patch: Edit) {
    setEdits((prev) => ({ ...prev, [id]: { ...prev[id], ...patch } }));
  }

  async function save(markReviewed: boolean) {
    setSaving(true);
    setError(null);
    try {
      await put(`/ocr/sessions/${sessionId}/pages/${pageIndex}/regions`, {
        regions: Object.entries(edits).map(([id, e]) => ({ id, ...e })),
        mark_reviewed: markReviewed,
      });
      await Promise.all([loadPage(), loadSession()]);
    } catch (e) {
      setError(e);
    } finally {
      setSaving(false);
    }
  }

  function go(i: number) {
    if (dirty && !confirm("Discard unsaved corrections on this page?")) return;
    setPageIndex(i);
    setActive(null);
  }

  function focusRegion(id: string) {
    setActive(id);
    const el = regionRefs.current[id];
    el?.scrollIntoView({ block: "center", behavior: "smooth" });
    el?.querySelector("textarea")?.focus();
  }

  function nextLow() {
    const i = active ? lowIds.indexOf(active) : -1;
    const id = lowIds[(i + 1) % Math.max(1, lowIds.length)];
    if (id) {
      focusRegion(id);
      return;
    }
    const pages = session?.pages ?? [];
    const next = pages.find((p) => p.page_index > pageIndex && p.low_confidence_regions > 0);
    if (next) go(next.page_index);
  }

  if (!session) return error ? <ErrorBox error={error} /> : <p className="muted">Loading session…</p>;
  const pages = session.pages ?? [];
  const total = session.page_count;
  const pageSummary = pages.find((p) => p.page_index === pageIndex);
  const imgSrc = `${API}/ocr/sessions/${sessionId}/pages/${pageIndex}/image?kind=${page?.has_processed_image ? imageKind : "source"}`;
  const approved = session.status === "approved";
  const reviewedCount = pages.filter((p) => p.reviewed).length;

  return (
    <>
      <p>
        <Link href="/ocr">← OCR sessions</Link>
      </p>
      <div className="row">
        <h1 style={{ margin: 0 }}>OCR review</h1>
        <StatusBadge status={session.status} />
        <span className="spacer" />
        <Link className="button" href={`/epub?version=${session.version_id}&session=${session.id}`}>
          Generate EPUB →
        </Link>
      </div>
      <p className="lead">
        {total} pages · languages {session.languages} · mean OCR confidence {(session.summary?.mean_confidence as number) ?? "—"} ·{" "}
        {reviewedCount} reviewed. Corrections are stored separately and never change the original PDF.
      </p>
      {jobId && <JobProgress jobId={jobId} onDone={() => { setJobId(null); void loadSession(); void loadPage(); }} />}
      <ErrorBox error={error} />

      <section className="card">
        <div className="row">
          <button disabled={pageIndex === 0} onClick={() => go(pageIndex - 1)}>
            ← Previous
          </button>
          <label htmlFor="pagesel" className="sr-only">
            Page
          </label>
          <select id="pagesel" value={pageIndex} onChange={(e) => go(Number(e.target.value))} style={{ width: "auto" }}>
            {Array.from({ length: total }, (_, i) => {
              const p = pages.find((x) => x.page_index === i);
              const mark = !p ? "…" : p.status === "failed" ? "✗" : p.reviewed ? "✓" : p.needs_review ? "!" : "";
              return (
                <option key={i} value={i}>
                  Page {i + 1} {mark} {p?.mean_confidence != null ? `(${p.mean_confidence})` : ""}
                </option>
              );
            })}
          </select>
          <button disabled={pageIndex >= total - 1} onClick={() => go(pageIndex + 1)}>
            Next →
          </button>
          <button onClick={nextLow}>Next low-confidence region ({lowIds.length} here)</button>
          <span className="spacer" />
          <span className="muted">
            {pageSummary?.mode === "ocr" ? "OCR" : "Digital text layer"} · {pageSummary?.kind}
            {pageSummary?.fallback_recommended ? " · image fallback recommended" : ""}
          </span>
        </div>
      </section>

      {!page ? (
        <p className="muted">{session.status === "processing" ? "This page has not been analysed yet…" : "No data for this page."}</p>
      ) : page.status === "failed" ? (
        <div className="notice err">This page could not be analysed: {page.error}. It will be kept as a page image unless reprocessed.</div>
      ) : (
        <div className="ocr-layout">
          <div>
            <div className="row" style={{ marginBottom: 8 }}>
              <button className="small" onClick={() => setZoom(Math.max(50, zoom - 25))} aria-label="Zoom out">
                −
              </button>
              <span>{zoom}%</span>
              <button className="small" onClick={() => setZoom(Math.min(400, zoom + 25))} aria-label="Zoom in">
                +
              </button>
              {page.has_processed_image && (
                <label className="check">
                  <input type="checkbox" checked={imageKind === "source"} onChange={(e) => setImageKind(e.target.checked ? "source" : "processed")} />
                  Show original render (before deskew/perspective)
                </label>
              )}
            </div>
            <div className="page-view">
              <div className="page-canvas" style={{ width: `${zoom}%` }}>
                <img src={imgSrc} alt={`Page ${pageIndex + 1}`} />
                {imageKind === "processed" || !page.has_processed_image
                  ? regions.map((r) => {
                      const [x0, y0, x1, y1] = r.bbox;
                      const cls = [
                        "region-box",
                        r.low_confidence ? "low" : "",
                        r.figure ? "figure" : "",
                        SKIPPED.has(r.role) || r.excluded ? "skip" : "",
                        active === r.id ? "active" : "",
                      ].join(" ");
                      return (
                        <button
                          key={r.id}
                          className={cls}
                          style={{ left: `${x0 * 100}%`, top: `${y0 * 100}%`, width: `${(x1 - x0) * 100}%`, height: `${(y1 - y0) * 100}%` }}
                          aria-label={`${r.role} ${r.order + 1}, confidence ${r.conf}`}
                          title={`${r.order + 1}. ${r.role} (${r.conf})`}
                          onClick={() => focusRegion(r.id)}
                        />
                      );
                    })
                  : null}
              </div>
            </div>
            <p className="hint">
              Blue: text · red: low confidence · green dashed: figure/table · grey dotted: omitted from the reading flow.
              Numbers in tooltips show the reading order.
            </p>
          </div>
          <div>
            <div className="row" style={{ marginBottom: 8 }}>
              <button className="primary" disabled={!dirty || saving || session.status === "processing"} onClick={() => void save(false)}>
                Save corrections
              </button>
              <button disabled={saving || session.status === "processing"} onClick={() => void save(true)}>
                Save &amp; mark page reviewed
              </button>
              {page.reviewed && <span className="badge ok">reviewed</span>}
            </div>
            <div className="region-list">
              {regions.length === 0 && <p className="muted">No text or images detected on this page.</p>}
              {regions.map((r) => {
                const e = edits[r.id] ?? {};
                const role = e.role ?? r.role;
                const excluded = e.excluded ?? r.excluded;
                return (
                  <div
                    key={r.id}
                    ref={(el) => {
                      regionRefs.current[r.id] = el;
                    }}
                    className={`region${active === r.id ? " active" : ""}${r.low_confidence ? " low" : ""}`}
                    onFocus={() => setActive(r.id)}
                  >
                    <div className="row" style={{ marginBottom: 6 }}>
                      <strong>#{r.order + 1}</strong>
                      <label className="sr-only" htmlFor={`role-${r.id}`}>
                        Role
                      </label>
                      <select id={`role-${r.id}`} value={role} onChange={(ev) => edit(r.id, { role: ev.target.value })} style={{ width: "auto" }}>
                        {ROLES.map((x) => (
                          <option key={x} value={x}>
                            {x}
                          </option>
                        ))}
                      </select>
                      {role === "heading" && (
                        <select aria-label="Heading level" value={e.heading_level ?? r.heading_level ?? 2} onChange={(ev) => edit(r.id, { heading_level: Number(ev.target.value) })} style={{ width: "auto" }}>
                          <option value={1}>H1 (chapter)</option>
                          <option value={2}>H2</option>
                          <option value={3}>H3</option>
                        </select>
                      )}
                      <span className={`badge ${r.source === "text_layer" ? "ok" : r.conf >= 85 ? "ok" : r.conf >= 70 ? "warn" : "err"}`}>
                        {r.source === "text_layer" ? "text layer" : `conf ${r.conf}`}
                      </span>
                      {r.corrected_text !== null && <span className="badge">corrected</span>}
                      <label className="check">
                        <input type="checkbox" checked={!!excluded} onChange={(ev) => edit(r.id, { excluded: ev.target.checked })} /> exclude
                      </label>
                    </div>
                    {r.figure && (
                      <div>
                        <img
                          src={`${API}/ocr/sessions/${sessionId}/${r.figure.asset}`}
                          alt={`Extracted ${r.figure.kind}`}
                          style={{ maxWidth: "100%", maxHeight: 180, display: "block", background: "#fff" }}
                        />
                        <div className="hint">
                          {r.figure.kind} · {r.figure.width}×{r.figure.height}px · {r.figure.method}
                          {r.figure.fallback ? " · kept as image (visual fallback)" : ""}
                          {r.figure.labels_text ? ` · labels: “${r.figure.labels_text.slice(0, 80)}”` : ""}
                        </div>
                      </div>
                    )}
                    {TEXT_ROLES.has(role) && (r.text || r.corrected_text) !== null && (
                      <>
                        <label className="sr-only" htmlFor={`txt-${r.id}`}>
                          Text of region {r.order + 1}
                        </label>
                        <textarea
                          id={`txt-${r.id}`}
                          value={value(r)}
                          lang={session.languages.startsWith("spa") ? "es" : undefined}
                          rows={Math.min(12, Math.max(2, Math.ceil(value(r).length / 70)))}
                          onChange={(ev) => edit(r.id, { corrected_text: ev.target.value })}
                        />
                        {r.corrected_text !== null && (
                          <details>
                            <summary className="hint">Original OCR text</summary>
                            <p className="hint">{r.text}</p>
                            <button className="small" onClick={() => edit(r.id, { revert: true })}>
                              Revert to OCR text
                            </button>
                          </details>
                        )}
                      </>
                    )}
                  </div>
                );
              })}
            </div>
          </div>
        </div>
      )}

      <section className="card">
        <h2 style={{ marginTop: 0 }}>Reprocess this page</h2>
        <p className="hint">Runs OCR again with different settings. Corrections on this page are replaced.</p>
        <div className="row">
          <label className="check">
            Segmentation
            <select value={psm} onChange={(e) => setPsm(Number(e.target.value))} style={{ width: "auto" }}>
              <option value={3}>Automatic (default)</option>
              <option value={4}>Single column</option>
              <option value={6}>Single uniform block</option>
              <option value={11}>Sparse text</option>
            </select>
          </label>
          <label className="check">
            Binarisation
            <select value={threshold} onChange={(e) => setThreshold(e.target.value)} style={{ width: "auto" }}>
              <option value="auto">Automatic</option>
              <option value="on">Adaptive threshold</option>
              <option value="off">Off</option>
            </select>
          </label>
          <button
            disabled={session.status === "processing"}
            onClick={async () => {
              try {
                const r = await post<{ job_id: string }>(`/ocr/sessions/${sessionId}/reprocess`, {
                  pages: [pageIndex],
                  options: { psm, force_ocr: true, preprocess: { threshold } },
                });
                setJobId(r.job_id);
                void loadSession();
              } catch (e) {
                setError(e);
              }
            }}
          >
            Reprocess page {pageIndex + 1}
          </button>
        </div>
      </section>

      <BookMetadataEditor session={session} onSaved={loadSession} />

      <section className="card">
        <h2 style={{ marginTop: 0 }}>Approve</h2>
        <p>
          Approving confirms the text is ready for EPUB export. {((session.summary?.needs_review as number[]) ?? []).length} page(s) were
          flagged for review.
        </p>
        <button
          className="primary"
          disabled={approved || session.status === "processing" || dirty}
          onClick={async () => {
            try {
              await post(`/ocr/sessions/${sessionId}/approve`);
              void loadSession();
            } catch (e) {
              setError(e);
            }
          }}
        >
          {approved ? "Approved" : "Approve OCR results"}
        </button>
        {dirty && <span className="muted"> Save your corrections first.</span>}
      </section>
    </>
  );
}

function BookMetadataEditor({ session, onSaved }: { session: OcrSession; onSaved: () => void }) {
  const bm = session.book_metadata ?? {};
  const [title, setTitle] = useState(bm.title ?? "");
  const [author, setAuthor] = useState(bm.author ?? "");
  const [language, setLanguage] = useState(bm.language ?? (session.languages.startsWith("spa") ? "es" : "en"));
  const [chapters, setChapters] = useState<{ title: string; page: number }[]>(bm.chapters ?? []);
  const [msg, setMsg] = useState<string | null>(null);
  const [error, setError] = useState<unknown>(null);
  return (
    <section className="card">
      <h2 style={{ marginTop: 0 }}>Book metadata &amp; chapters</h2>
      <div className="form-grid">
        <div className="field">
          <label htmlFor="bt">Title</label>
          <input id="bt" type="text" value={title} onChange={(e) => setTitle(e.target.value)} />
        </div>
        <div className="field">
          <label htmlFor="ba">Author(s)</label>
          <input id="ba" type="text" value={author} onChange={(e) => setAuthor(e.target.value)} />
          <div className="hint">Separate several authors with “;”.</div>
        </div>
        <div className="field">
          <label htmlFor="bl">Language</label>
          <input id="bl" type="text" value={language} onChange={(e) => setLanguage(e.target.value)} />
        </div>
      </div>
      <h3>Chapter boundaries (optional)</h3>
      <p className="hint">Leave empty to detect chapters from headings automatically.</p>
      {chapters.map((c, i) => (
        <div className="row" key={i} style={{ marginBottom: 6 }}>
          <input type="text" aria-label={`Chapter ${i + 1} title`} value={c.title} onChange={(e) => setChapters(chapters.map((x, k) => (k === i ? { ...x, title: e.target.value } : x)))} style={{ maxWidth: 360 }} />
          <label className="check">
            starts on page
            <input type="number" min={1} max={session.page_count} value={c.page} onChange={(e) => setChapters(chapters.map((x, k) => (k === i ? { ...x, page: Number(e.target.value) } : x)))} style={{ width: 90 }} />
          </label>
          <button className="small danger" onClick={() => setChapters(chapters.filter((_, k) => k !== i))}>
            Remove
          </button>
        </div>
      ))}
      <div className="row">
        <button onClick={() => setChapters([...chapters, { title: `Chapter ${chapters.length + 1}`, page: 1 }])}>Add chapter</button>
        <button
          className="primary"
          onClick={async () => {
            setError(null);
            try {
              await put(`/ocr/sessions/${session.id}/book`, {
                title: title || null,
                author: author || null,
                language: language || null,
                chapters: chapters.length ? chapters : null,
              });
              setMsg("Saved.");
              onSaved();
            } catch (e) {
              setError(e);
            }
          }}
        >
          Save metadata
        </button>
        {msg && <span className="muted">{msg}</span>}
      </div>
      <ErrorBox error={error} />
    </section>
  );
}
