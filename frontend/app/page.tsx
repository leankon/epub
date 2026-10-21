"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { ErrorBox } from "@/components/ErrorBox";
import { StatusBadge } from "@/components/JobProgress";
import { get, post } from "@/lib/api";
import { formatBytes, formatDate, opLabel } from "@/lib/format";
import type { DocumentSummary, Job } from "@/lib/types";

interface Dashboard {
  documents: number;
  versions: number;
  jobs_by_status: Record<string, number>;
  storage: Record<string, number>;
}

const TOOLS: [string, string, string][] = [
  ["/ocr", "Scanned book to text", "OCR, layout analysis and review"],
  ["/epub", "PDF to EPUB", "Hybrid EPUB with real text and images"],
  ["/tools/merge", "Merge PDFs", "Combine files in order"],
  ["/tools/split", "Split PDF", "Ranges, pages or bookmarks"],
  ["/tools/organize", "Organize pages", "Reorder, rotate, delete"],
  ["/tools/compress", "Compress", "Quality-oriented presets"],
  ["/tools/redact", "Redact", "Permanent removal with verification"],
  ["/office", "Office to PDF", "Word, Excel, PowerPoint"],
];

export default function Home() {
  const [dash, setDash] = useState<Dashboard | null>(null);
  const [docs, setDocs] = useState<DocumentSummary[]>([]);
  const [jobs, setJobs] = useState<Job[]>([]);
  const [problems, setProblems] = useState<Job[]>([]);
  const [missing, setMissing] = useState<{ tool: string; install_hint: string }[]>([]);
  const [error, setError] = useState<unknown>(null);

  const load = useCallback(async () => {
    try {
      const [d, dl, jl, pl, ready] = await Promise.all([
        get<Dashboard>("/dashboard"),
        get<{ items: DocumentSummary[] }>("/documents?limit=6"),
        get<{ items: Job[] }>("/jobs?limit=10"),
        get<{ items: Job[] }>("/jobs?status=failed,interrupted&limit=10"),
        get<{ missing_tools: { tool: string; install_hint: string }[] }>("/ready"),
      ]);
      setDash(d);
      setDocs(dl.items);
      setJobs(jl.items);
      setProblems(pl.items);
      setMissing(ready.missing_tools);
      setError(null);
    } catch (e) {
      setError(e);
    }
  }, []);

  useEffect(() => {
    void load();
    const t = setInterval(load, 5000);
    return () => clearInterval(t);
  }, [load]);

  const used = dash ? Object.values(dash.storage).reduce((a, b) => a + b, 0) : 0;
  return (
    <>
      <h1>Home</h1>
      <p className="lead">Your private PDF, OCR and EPUB workstation. All processing runs on this computer.</p>
      <ErrorBox error={error} />
      {missing.length > 0 && (
        <div className="notice warn">
          <strong>Some optional tools are missing; related features are unavailable:</strong>
          <ul>
            {missing.map((m) => (
              <li key={m.tool}>
                {m.tool}: {m.install_hint}
              </li>
            ))}
          </ul>
        </div>
      )}
      <div className="grid" style={{ marginBottom: 16 }}>
        <div className="card">
          <div className="muted">Documents</div>
          <strong style={{ fontSize: 24 }}>{dash?.documents ?? "—"}</strong>
          <div className="hint">{dash?.versions ?? "—"} stored versions</div>
        </div>
        <div className="card">
          <div className="muted">Local storage used</div>
          <strong style={{ fontSize: 24 }}>{dash ? formatBytes(used) : "—"}</strong>
          <div className="hint">
            <Link href="/storage">Manage storage and backups</Link>
          </div>
        </div>
        <div className="card">
          <div className="muted">Jobs</div>
          <strong style={{ fontSize: 24 }}>{(dash?.jobs_by_status.running ?? 0) + (dash?.jobs_by_status.queued ?? 0)}</strong>
          <div className="hint">running or queued · {dash?.jobs_by_status.succeeded ?? 0} succeeded</div>
        </div>
      </div>

      {problems.length > 0 && (
        <section className="card">
          <h2 style={{ marginTop: 0 }}>Failed or interrupted jobs</h2>
          <ul>
            {problems.map((j) => (
              <li key={j.id}>
                {opLabel(j.kind)} <StatusBadge status={j.status} /> — {j.error?.message}{" "}
                {j.resumable && (
                  <button
                    className="small"
                    onClick={async () => {
                      try {
                        await post(`/jobs/${j.id}/resume`, { secrets: {} });
                        void load();
                      } catch (e) {
                        setError(e);
                      }
                    }}
                  >
                    Resume
                  </button>
                )}
              </li>
            ))}
          </ul>
        </section>
      )}

      <h2>Tools</h2>
      <div className="grid">
        {TOOLS.map(([href, title, desc]) => (
          <Link key={href} className="tile" href={href}>
            <strong>{title}</strong>
            <span>{desc}</span>
          </Link>
        ))}
      </div>

      <h2>Recent documents</h2>
      {docs.length === 0 ? (
        <p className="muted">
          No documents yet. <Link href="/library">Import files</Link> to get started.
        </p>
      ) : (
        <ul>
          {docs.map((d) => (
            <li key={d.id}>
              <Link href={`/library/${d.id}`}>{d.title}</Link>{" "}
              <span className="muted">
                {d.kind} · {d.latest_version?.page_count ?? "?"} pages · {formatDate(d.created_at)}
              </span>
            </li>
          ))}
        </ul>
      )}

      <h2>Recent jobs</h2>
      {jobs.length === 0 ? (
        <p className="muted">No jobs yet.</p>
      ) : (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Operation</th>
                <th>Status</th>
                <th>Stage</th>
                <th>Created</th>
              </tr>
            </thead>
            <tbody>
              {jobs.map((j) => (
                <tr key={j.id}>
                  <td>
                    <Link href={`/history?job=${j.id}`}>{opLabel(j.kind)}</Link>
                  </td>
                  <td>
                    <StatusBadge status={j.status} />
                  </td>
                  <td>{j.progress?.stage ?? ""}</td>
                  <td>{formatDate(j.created_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}
