"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { API, downloadUrl, get } from "@/lib/api";
import type { Job } from "@/lib/types";

interface Check {
  name: string;
  passed: boolean;
  detail: string;
  required: boolean;
}
interface Report {
  strategy: string;
  pages_processed: number;
  ocr_pages: number;
  text_layer_pages: number;
  mean_ocr_confidence: number | null;
  images_retained: number;
  chapters: { title: string; start_page: number }[];
  fallback_pages: { page: number; reason: string }[];
  fallback_regions: { page: number; region: string; kind: string }[];
  footnotes: number;
  removed_running_elements: Record<string, number>;
  excluded_by_user: number;
  low_confidence_unreviewed: number;
  corrected_regions: number;
  warnings: string[];
  known_limitations: string[];
  reviewed: boolean;
  session_id: string;
  validation: {
    passed: boolean;
    checks: Check[];
    errors: string[];
    warnings: string[];
    stats: Record<string, unknown>;
    epubcheck: { executed: boolean; version?: string; errors?: number; fatals?: number; warnings?: number; reason?: string; messages?: { severity: string; message: string; path?: string }[] };
  };
}

export function ValidationView({ v }: { v: Report["validation"] }) {
  const ec = v.epubcheck;
  return (
    <div>
      <p>
        <span className={`badge ${v.passed ? "ok" : "err"}`}>{v.passed ? "Validation passed" : "Validation failed"}</span>{" "}
        {ec.executed ? (
          <span className={`badge ${(ec.errors ?? 0) + (ec.fatals ?? 0) === 0 ? "ok" : "err"}`}>
            EPUBCheck {ec.version}: {ec.fatals ?? 0} fatal, {ec.errors ?? 0} errors, {ec.warnings ?? 0} warnings
          </span>
        ) : (
          <span className="badge warn">EPUBCheck not executed ({ec.reason ?? "unavailable"}) — internal checks only</span>
        )}
      </p>
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Check</th>
              <th>Result</th>
              <th>Detail</th>
            </tr>
          </thead>
          <tbody>
            {v.checks.map((c) => (
              <tr key={c.name}>
                <td>
                  {c.name}
                  {!c.required && <span className="muted"> (advisory)</span>}
                </td>
                <td>
                  <span className={`badge ${c.passed ? "ok" : c.required ? "err" : "warn"}`}>{c.passed ? "pass" : "fail"}</span>
                </td>
                <td className="muted">{c.detail}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {ec.messages && ec.messages.length > 0 && (
        <ul>
          {ec.messages.map((m, i) => (
            <li key={i}>
              {m.severity}: {m.message} {m.path && <span className="mono">{m.path}</span>}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

export function EpubReport({ job }: { job: Job }) {
  const report = (job.result?.report as Report | undefined) ?? null;
  const out = job.outputs[0];
  if (!report) return null;
  return (
    <section className="card">
      <h2 style={{ marginTop: 0 }}>Conversion report</h2>
      {out && (
        <p>
          <a className="button primary" href={downloadUrl(out.version_id)}>
            Download EPUB
          </a>{" "}
          <Link href={`/library/${out.document_id}`}>Open in library</Link>
        </p>
      )}
      {!report.reviewed && <div className="notice warn">OCR results were not reviewed before this export.</div>}
      <dl className="kv">
        <dt>Mode</dt><dd>{report.strategy}</dd>
        <dt>Pages processed</dt><dd>{report.pages_processed}</dd>
        <dt>OCR coverage</dt><dd>{report.ocr_pages} OCR page(s), {report.text_layer_pages} with digital text</dd>
        <dt>Mean OCR confidence</dt><dd>{report.mean_ocr_confidence ?? "—"}</dd>
        <dt>Images retained</dt><dd>{report.images_retained}</dd>
        <dt>Chapters</dt><dd>{report.chapters.length}</dd>
        <dt>Footnotes</dt><dd>{report.footnotes}</dd>
        <dt>Corrected regions</dt><dd>{report.corrected_regions}</dd>
        <dt>Unreviewed low-confidence regions</dt><dd>{report.low_confidence_unreviewed}</dd>
        <dt>Omitted running elements</dt><dd>{Object.entries(report.removed_running_elements).map(([k, n]) => `${n} ${k}`).join(", ") || "none"}</dd>
        <dt>Excluded by you</dt><dd>{report.excluded_by_user}</dd>
      </dl>
      <h3>Chapters</h3>
      <ol>
        {report.chapters.map((c, i) => (
          <li key={i}>
            {c.title || "(untitled)"} <span className="muted">— page {c.start_page}</span>
          </li>
        ))}
      </ol>
      <h3>Visual fallbacks</h3>
      {report.fallback_pages.length + report.fallback_regions.length === 0 ? (
        <p className="muted">None: all content was reconstructed as text and images.</p>
      ) : (
        <ul>
          {report.fallback_pages.map((f) => (
            <li key={`p${f.page}`}>Page {f.page} kept as image: {f.reason}</li>
          ))}
          {report.fallback_regions.map((f) => (
            <li key={f.region}>
              Page {f.page}: {f.kind} kept as image ({f.region})
            </li>
          ))}
        </ul>
      )}
      <h3>Validation</h3>
      <ValidationView v={report.validation} />
      {report.warnings.length > 0 && (
        <div className="notice warn">
          <ul>{report.warnings.map((w, i) => <li key={i}>{w}</li>)}</ul>
        </div>
      )}
      <h3>Known limitations</h3>
      <ul className="muted">{report.known_limitations.map((l, i) => <li key={i}>{l}</li>)}</ul>
      {out && <EpubPreview versionId={out.version_id} />}
    </section>
  );
}

export function EpubPreview({ versionId }: { versionId: string }) {
  const [toc, setToc] = useState<{ spine: string[]; images: string[]; metadata: Record<string, string>; layout: string } | null>(null);
  const [doc, setDoc] = useState<string | null>(null);
  useEffect(() => {
    void get<typeof toc>(`/epub/versions/${versionId}/toc`).then((t) => {
      setToc(t);
      const first = t?.spine.find((s) => !s.endsWith("nav.xhtml")) ?? t?.spine[0];
      setDoc(first ?? null);
    });
  }, [versionId]);
  if (!toc) return null;
  return (
    <div>
      <h3>Preview</h3>
      <p className="muted">
        {toc.metadata.title} · {toc.metadata.creator} · {toc.metadata.language} · {toc.layout} · {toc.images.length} image(s)
      </p>
      <div className="row" style={{ marginBottom: 8 }}>
        <label htmlFor="docsel">Content document</label>
        <select id="docsel" value={doc ?? ""} onChange={(e) => setDoc(e.target.value)} style={{ width: "auto" }}>
          {toc.spine.map((s) => (
            <option key={s} value={s}>
              {s}
            </option>
          ))}
        </select>
      </div>
      {doc && (
        <iframe
          className="preview"
          title="EPUB content preview"
          sandbox="allow-same-origin" /* no scripts; same-origin only so its images load through the proxy */
          src={`${API}/epub/versions/${versionId}/doc?path=${encodeURIComponent(doc)}`}
        />
      )}
    </div>
  );
}
