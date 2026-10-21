"use client";

import { useEffect, useMemo, useState } from "react";
import { DocumentPicker } from "@/components/DocumentPicker";
import { ErrorBox } from "@/components/ErrorBox";
import { StatusBadge } from "@/components/JobProgress";
import { TOOLS, type ToolContext } from "@/components/tools";
import { downloadUrl, get, post } from "@/lib/api";
import { opLabel } from "@/lib/format";
import type { Job } from "@/lib/types";

const BATCH_TOOLS = ["compress", "watermark", "page-numbers", "rotate", "metadata", "ocr", "pdf-to-images", "extract-images"];
const EXTRA: Record<string, { kind: string; exts: string[]; title: string }> = {
  office: { kind: "office_to_pdf", exts: ["docx", "odt", "doc", "rtf", "xlsx", "ods", "xls", "pptx", "odp", "ppt"], title: "Office to PDF" },
  epub: { kind: "epub_to_pdf", exts: ["epub"], title: "EPUB to PDF" },
};

interface BatchState {
  id: string;
  counts: Record<string, number>;
  total: number;
  jobs: Job[];
}

export default function BatchPage() {
  const [toolId, setToolId] = useState("compress");
  const [selected, setSelected] = useState<string[]>([]);
  const tool = TOOLS.find((t) => t.id === toolId);
  const extra = EXTRA[toolId];
  const [params, setParams] = useState<Record<string, unknown>>(tool?.defaults ?? {});
  const [batch, setBatch] = useState<BatchState | null>(null);
  const [rejected, setRejected] = useState<{ version_id: string; message: string }[]>([]);
  const [error, setError] = useState<unknown>(null);
  const [ack, setAck] = useState(false);

  useEffect(() => {
    setParams(tool?.defaults ?? {});
    setSelected([]);
  }, [toolId, tool]);

  useEffect(() => {
    if (!batch) return;
    const running = (batch.counts.queued ?? 0) + (batch.counts.running ?? 0) > 0;
    if (!running) return;
    const t = setTimeout(async () => setBatch(await get<BatchState>(`/batches/${batch.id}`)), 1500);
    return () => clearTimeout(t);
  }, [batch]);

  const ctx: ToolContext = useMemo(
    () => ({ selected, setSelected, docs: [], version: null, params, set: (p) => setParams((x) => ({ ...x, ...p })) }),
    [selected, params],
  );

  async function start() {
    setError(null);
    try {
      const kind = extra?.kind ?? tool!.kind;
      const body = extra ? {} : tool?.build ? tool.build(ctx) : params;
      const r = await post<{ batch_id: string; rejected: { version_id: string; message: string }[] }>("/batches", {
        kind,
        version_ids: selected,
        params: body,
        acknowledge_warnings: ack,
      });
      setRejected(r.rejected);
      setBatch(await get<BatchState>(`/batches/${r.batch_id}`));
    } catch (e) {
      setError(e);
    }
  }

  const done = batch ? (batch.counts.succeeded ?? 0) + (batch.counts.failed ?? 0) + (batch.counts.cancelled ?? 0) : 0;
  return (
    <>
      <h1>Batch processing</h1>
      <p className="lead">Apply one operation to many files. Jobs run with bounded concurrency; each produces its own version.</p>
      <section className="card">
        <div className="field" style={{ maxWidth: 360 }}>
          <label htmlFor="op">Operation</label>
          <select id="op" value={toolId} onChange={(e) => setToolId(e.target.value)}>
            {BATCH_TOOLS.map((id) => (
              <option key={id} value={id}>
                {TOOLS.find((t) => t.id === id)?.title}
              </option>
            ))}
            {Object.entries(EXTRA).map(([id, x]) => (
              <option key={id} value={id}>
                {x.title}
              </option>
            ))}
          </select>
        </div>
        <DocumentPicker exts={extra?.exts ?? tool?.exts ?? ["pdf"]} multiple selected={selected} onChange={(v) => setSelected(v)} />
      </section>
      {!extra && tool?.Options && toolId !== "metadata" && (
        <section className="card">
          <h2 style={{ marginTop: 0 }}>Options</h2>
          {tool.Options(ctx)}
        </section>
      )}
      {toolId === "metadata" && (
        <section className="card">
          <h2 style={{ marginTop: 0 }}>Metadata to apply</h2>
          {tool?.Options?.(ctx)}
        </section>
      )}
      <section className="card">
        <label className="check">
          <input type="checkbox" checked={ack} onChange={(e) => setAck(e.target.checked)} /> Also process files with digital
          signatures or forms (signatures will be invalidated)
        </label>
        <button className="primary" disabled={!selected.length} onClick={() => void start()}>
          Start batch ({selected.length} file{selected.length === 1 ? "" : "s"})
        </button>
        <ErrorBox error={error} />
      </section>
      {batch && (
        <section className="card" aria-live="polite">
          <h2 style={{ marginTop: 0 }}>
            Batch progress: {done} / {batch.total}
          </h2>
          <div className="progress" role="progressbar" aria-valuenow={batch.total ? Math.round((100 * done) / batch.total) : 0} aria-valuemin={0} aria-valuemax={100} aria-label="Batch progress">
            <div style={{ width: `${batch.total ? (100 * done) / batch.total : 0}%` }} />
          </div>
          {rejected.length > 0 && (
            <div className="notice warn">
              {rejected.length} file(s) were not queued:
              <ul>{rejected.map((r) => <li key={r.version_id}>{r.message}</li>)}</ul>
            </div>
          )}
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Job</th>
                  <th>Status</th>
                  <th>Stage / error</th>
                  <th>Output</th>
                </tr>
              </thead>
              <tbody>
                {batch.jobs.map((j) => (
                  <tr key={j.id}>
                    <td>{opLabel(j.kind)}</td>
                    <td>
                      <StatusBadge status={j.status} />
                    </td>
                    <td>{j.error?.message ?? j.progress?.stage}</td>
                    <td>
                      {j.outputs.map((o) => (
                        <a key={o.version_id} href={downloadUrl(o.version_id)}>
                          Download{" "}
                        </a>
                      ))}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}
    </>
  );
}
