"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { DocumentPicker } from "@/components/DocumentPicker";
import { ErrorBox } from "@/components/ErrorBox";
import { StatusBadge } from "@/components/JobProgress";
import { get, post } from "@/lib/api";
import { formatDate } from "@/lib/format";
import type { DocumentSummary, OcrSession } from "@/lib/types";

export default function OcrPage() {
  const router = useRouter();
  const [selected, setSelected] = useState<string[]>([]);
  const [sessions, setSessions] = useState<OcrSession[]>([]);
  const [docs, setDocs] = useState<Record<string, string>>({});
  const [languages, setLanguages] = useState("spa+eng");
  const [dpi, setDpi] = useState(300);
  const [perspective, setPerspective] = useState("auto");
  const [forceOcr, setForceOcr] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      const [s, d] = await Promise.all([
        get<{ items: OcrSession[] }>("/ocr/sessions"),
        get<{ items: DocumentSummary[] }>("/documents?limit=500"),
      ]);
      setSessions(s.items);
      setDocs(Object.fromEntries(d.items.map((x) => [x.id, x.title])));
    } catch (e) {
      setError(e);
    }
  }, []);
  useEffect(() => {
    void load();
    const t = setInterval(load, 4000);
    return () => clearInterval(t);
  }, [load]);

  async function start() {
    setBusy(true);
    setError(null);
    try {
      const r = await post<{ session: OcrSession }>("/ocr/sessions", {
        version_id: selected[0],
        options: { languages, dpi, force_ocr: forceOcr, preprocess: { perspective } },
      });
      router.push(`/ocr/${r.session.id}`);
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <h1>OCR &amp; scanned books</h1>
      <p className="lead">
        Recognise scanned or photographed pages, check layout and reading order, correct the text, and approve it
        before generating an EPUB. Pages that already contain good digital text are not re-OCR&apos;d.
      </p>
      <section className="card">
        <h2 style={{ marginTop: 0 }}>Start a new analysis</h2>
        <DocumentPicker exts={["pdf"]} selected={selected} onChange={(v) => setSelected(v)} />
        <div className="form-grid" style={{ marginTop: 12 }}>
          <div className="field">
            <label htmlFor="langs">Languages</label>
            <input id="langs" type="text" value={languages} onChange={(e) => setLanguages(e.target.value)} />
            <div className="hint">Tesseract codes, e.g. spa+eng</div>
          </div>
          <div className="field">
            <label htmlFor="dpi">Render resolution (DPI)</label>
            <input id="dpi" type="number" min={150} max={600} value={dpi} onChange={(e) => setDpi(Number(e.target.value))} />
          </div>
          <div className="field">
            <label htmlFor="persp">Perspective correction (photographed pages)</label>
            <select id="persp" value={perspective} onChange={(e) => setPerspective(e.target.value)}>
              <option value="auto">Automatic</option>
              <option value="on">Always try</option>
              <option value="off">Off</option>
            </select>
          </div>
        </div>
        <label className="check">
          <input type="checkbox" checked={forceOcr} onChange={(e) => setForceOcr(e.target.checked)} /> OCR every page,
          even those with a digital text layer
        </label>
        <div className="row" style={{ marginTop: 12 }}>
          <button className="primary" disabled={!selected.length || busy} onClick={() => void start()}>
            Analyse
          </button>
        </div>
        <ErrorBox error={error} />
      </section>
      <h2>Sessions</h2>
      {sessions.length === 0 ? (
        <p className="muted">No OCR sessions yet.</p>
      ) : (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Document</th>
                <th>Status</th>
                <th>Pages</th>
                <th>Mean confidence</th>
                <th>Needs review</th>
                <th>Started</th>
              </tr>
            </thead>
            <tbody>
              {sessions.map((s) => (
                <tr key={s.id}>
                  <td>
                    <Link href={`/ocr/${s.id}`}>{docs[s.document_id] ?? s.document_id}</Link>
                  </td>
                  <td>
                    <StatusBadge status={s.status} />
                  </td>
                  <td>{s.page_count}</td>
                  <td>{(s.summary?.mean_confidence as number | null) ?? "—"}</td>
                  <td>{((s.summary?.needs_review as number[]) ?? []).length}</td>
                  <td>{formatDate(s.created_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}
