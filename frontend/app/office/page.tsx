"use client";

import { useState } from "react";
import { DocumentPicker } from "@/components/DocumentPicker";
import { ErrorBox } from "@/components/ErrorBox";
import { JobProgress } from "@/components/JobProgress";
import { useJobRunner } from "@/components/useJobRunner";

const OFFICE = ["docx", "odt", "doc", "rtf", "xlsx", "ods", "xls", "pptx", "odp", "ppt"];

export default function OfficePage() {
  const [selected, setSelected] = useState<string[]>([]);
  const runner = useJobRunner();
  return (
    <>
      <h1>Office conversion</h1>
      <p className="lead">Word, Excel and PowerPoint (and OpenDocument) files to PDF with LibreOffice running locally.</p>
      <div className="notice">
        Macros are never executed (an isolated, macro-disabled LibreOffice profile is used for each conversion). Fonts that
        are not installed locally are substituted, so line breaks, page breaks, charts, formulas and animations may
        change. The original Office file is kept unchanged.
      </div>
      <section className="card">
        <DocumentPicker exts={OFFICE} selected={selected} onChange={(v) => setSelected(v)} uploadAccept={OFFICE.map((e) => "." + e).join(",")} />
        <button className="primary" style={{ marginTop: 12 }} disabled={!selected.length || runner.busy} onClick={() => void runner.run("office_to_pdf", selected, {})}>
          Convert to PDF
        </button>
        <ErrorBox error={runner.error} />
      </section>
      {runner.jobId && <JobProgress key={runner.jobId} jobId={runner.jobId} />}
    </>
  );
}
