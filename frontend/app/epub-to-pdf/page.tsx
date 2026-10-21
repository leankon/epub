"use client";

import { useState } from "react";
import { DocumentPicker } from "@/components/DocumentPicker";
import { ValidationView } from "@/components/EpubReport";
import { ErrorBox } from "@/components/ErrorBox";
import { JobProgress } from "@/components/JobProgress";
import { useJobRunner } from "@/components/useJobRunner";
import { get } from "@/lib/api";
import type { Job } from "@/lib/types";

export default function EpubToPdfPage() {
  const [selected, setSelected] = useState<string[]>([]);
  const [pageSize, setPageSize] = useState("a5");
  const [fontSize, setFontSize] = useState(11);
  const convert = useJobRunner();
  const validate = useJobRunner();
  const [validation, setValidation] = useState<Job | null>(null);

  return (
    <>
      <h1>EPUB to PDF</h1>
      <p className="lead">Render an EPUB to PDF locally. Chapters start on new pages and headings become bookmarks.</p>
      <div className="notice">
        EPUB is reflowable while PDF has fixed pages, so pagination and layout will differ from an e-reader. Round-trip
        fidelity is not guaranteed. Only resources inside the EPUB are loaded; nothing is fetched from the network.
      </div>
      <section className="card">
        <DocumentPicker exts={["epub"]} selected={selected} onChange={(v) => { setSelected(v); setValidation(null); }} />
      </section>
      <section className="card">
        <div className="form-grid">
          <div className="field">
            <label htmlFor="ps">Page size</label>
            <select id="ps" value={pageSize} onChange={(e) => setPageSize(e.target.value)}>
              <option value="a5">A5 (book)</option>
              <option value="6x9">6 × 9 in</option>
              <option value="a4">A4</option>
              <option value="letter">Letter</option>
            </select>
          </div>
          <div className="field">
            <label htmlFor="fs">Base font size (pt)</label>
            <input id="fs" type="number" min={7} max={18} value={fontSize} onChange={(e) => setFontSize(Number(e.target.value))} />
          </div>
        </div>
        <div className="row">
          <button className="primary" disabled={!selected.length || convert.busy} onClick={() => void convert.run("epub_to_pdf", selected, { page_size: pageSize, font_size: fontSize })}>
            Convert to PDF
          </button>
          <button disabled={!selected.length || validate.busy} onClick={() => void validate.run("epub_validate", selected, {})}>
            Validate EPUB only
          </button>
        </div>
        <ErrorBox error={convert.error || validate.error} />
      </section>
      {validate.jobId && (
        <JobProgress key={validate.jobId} jobId={validate.jobId} onDone={async (j) => setValidation(await get<Job>(`/jobs/${j.id}`))} />
      )}
      {validation?.result?.validation ? (
        <section className="card">
          <h2 style={{ marginTop: 0 }}>Validation</h2>
          <ValidationView v={validation.result.validation as Parameters<typeof ValidationView>[0]["v"]} />
        </section>
      ) : null}
      {convert.jobId && <JobProgress key={convert.jobId} jobId={convert.jobId} />}
    </>
  );
}
