"use client";

import { useSearchParams } from "next/navigation";
import { Suspense, useEffect, useState } from "react";
import { DocumentPicker } from "@/components/DocumentPicker";
import { EpubReport } from "@/components/EpubReport";
import { ErrorBox } from "@/components/ErrorBox";
import { JobProgress } from "@/components/JobProgress";
import { ApiError, get, post } from "@/lib/api";
import type { Job, OcrSession } from "@/lib/types";

const STRATEGIES: [string, string, string][] = [
  ["hybrid", "Intelligent hybrid (recommended)", "Reflowable text and headings, original images as separate assets, image fallback only where needed."],
  ["reflowable", "Reflowable", "Semantic XHTML everywhere; adjustable fonts and margins. Tables/formulas are kept as images."],
  ["fixed", "Layout-preserving (fixed layout)", "Each page keeps its look with selectable text positioned over it. No reflow; limited accessibility."],
];

function EpubInner() {
  const search = useSearchParams();
  const [selected, setSelected] = useState<string[]>([]);
  const [sessions, setSessions] = useState<OcrSession[]>([]);
  const [sessionId, setSessionId] = useState<string>("");
  const [strategy, setStrategy] = useState("hybrid");
  const [title, setTitle] = useState("");
  const [author, setAuthor] = useState("");
  const [language, setLanguage] = useState("");
  const [languages, setLanguages] = useState("spa+eng");
  const [jobId, setJobId] = useState<string | null>(null);
  const [done, setDone] = useState<Job | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [needsApproval, setNeedsApproval] = useState(false);

  useEffect(() => {
    const v = search.get("version");
    const s = search.get("session");
    if (v) setSelected([v]);
    if (s) setSessionId(s);
  }, [search]);

  useEffect(() => {
    const vid = selected[0];
    if (!vid) {
      setSessions([]);
      return;
    }
    void get<{ items: OcrSession[] }>("/ocr/sessions").then((r) => setSessions(r.items.filter((s) => s.version_id === vid)));
  }, [selected]);

  async function convert(allowUnreviewed = false) {
    setError(null);
    setNeedsApproval(false);
    setDone(null);
    try {
      const r = await post<{ job_id: string }>("/epub/convert", {
        version_id: selected[0],
        session_id: sessionId || null,
        strategy,
        allow_unreviewed: allowUnreviewed,
        metadata: { title: title || null, author: author || null, language: language || null },
        options: { languages },
      });
      setJobId(r.job_id);
    } catch (e) {
      if (e instanceof ApiError && e.code === "approval_required") setNeedsApproval(true);
      else setError(e);
    }
  }

  const chosen = sessions.find((s) => s.id === sessionId);
  return (
    <>
      <h1>PDF to EPUB</h1>
      <p className="lead">
        Builds a valid EPUB 3 with real, selectable text reconstructed from the PDF (OCR for scanned pages), keeping
        illustrations as separate images. Every result is validated before it can be downloaded.
      </p>
      <section className="card">
        <h2 style={{ marginTop: 0 }}>1. Source PDF</h2>
        <DocumentPicker exts={["pdf"]} selected={selected} onChange={(v) => { setSelected(v); setSessionId(""); }} />
      </section>
      <section className="card">
        <h2 style={{ marginTop: 0 }}>2. Text source</h2>
        <fieldset>
          <legend>OCR / text analysis</legend>
          <label className="check">
            <input type="radio" name="sess" checked={!sessionId} onChange={() => setSessionId("")} /> Analyse automatically now (no manual review)
          </label>
          {sessions.map((s) => (
            <label key={s.id} className="check">
              <input type="radio" name="sess" checked={sessionId === s.id} onChange={() => setSessionId(s.id)} /> Use reviewed session from{" "}
              {new Date(s.created_at ?? "").toLocaleString()} — <strong>{s.status}</strong>
            </label>
          ))}
        </fieldset>
        {!sessionId && (
          <div className="field" style={{ maxWidth: 260 }}>
            <label htmlFor="langs">OCR languages</label>
            <input id="langs" type="text" value={languages} onChange={(e) => setLanguages(e.target.value)} />
          </div>
        )}
        {chosen && chosen.status !== "approved" && (
          <div className="notice warn">This session is not approved yet. Review it in the OCR workspace for best results.</div>
        )}
      </section>
      <section className="card">
        <h2 style={{ marginTop: 0 }}>3. Conversion mode</h2>
        {STRATEGIES.map(([v, l, d]) => (
          <label key={v} className="check" style={{ alignItems: "flex-start", marginBottom: 6 }}>
            <input type="radio" name="strategy" checked={strategy === v} onChange={() => setStrategy(v)} />
            <span>
              <strong>{l}</strong>
              <br />
              <span className="hint">{d}</span>
            </span>
          </label>
        ))}
      </section>
      <section className="card">
        <h2 style={{ marginTop: 0 }}>4. Metadata</h2>
        <p className="hint">Leave empty to use the session&apos;s book metadata or the document title.</p>
        <div className="form-grid">
          <div className="field">
            <label htmlFor="t">Title</label>
            <input id="t" type="text" value={title} onChange={(e) => setTitle(e.target.value)} />
          </div>
          <div className="field">
            <label htmlFor="a">Author(s)</label>
            <input id="a" type="text" value={author} onChange={(e) => setAuthor(e.target.value)} />
          </div>
          <div className="field">
            <label htmlFor="l">Language (BCP 47)</label>
            <input id="l" type="text" value={language} onChange={(e) => setLanguage(e.target.value)} placeholder="es" />
          </div>
        </div>
        <button className="primary" disabled={!selected.length} onClick={() => void convert(false)}>
          Convert to EPUB
        </button>
        {needsApproval && (
          <div className="notice warn" role="alertdialog">
            The selected OCR session has not been approved.{" "}
            <button onClick={() => void convert(true)}>Convert anyway (unreviewed)</button>
          </div>
        )}
        <ErrorBox error={error} />
      </section>
      {jobId && (
        <JobProgress
          key={jobId}
          jobId={jobId}
          onDone={async (j) => setDone(await get<Job>(`/jobs/${j.id}`))}
        />
      )}
      {done && <EpubReport job={done} />}
    </>
  );
}

export default function EpubPage() {
  return (
    <Suspense fallback={<p className="muted">Loading…</p>}>
      <EpubInner />
    </Suspense>
  );
}
