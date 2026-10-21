"use client";

import { useSearchParams } from "next/navigation";
import { Suspense, useCallback, useEffect, useState } from "react";
import { ErrorBox } from "@/components/ErrorBox";
import { JobProgress, StatusBadge } from "@/components/JobProgress";
import { API, ApiError, get, post } from "@/lib/api";
import { formatDate, formatDuration, opLabel, shortHash } from "@/lib/format";
import type { AuditEvent, Job } from "@/lib/types";

function HistoryInner() {
  const search = useSearchParams();
  const [jobs, setJobs] = useState<Job[]>([]);
  const [status, setStatus] = useState("");
  const [detail, setDetail] = useState<Job | null>(null);
  const [events, setEvents] = useState<AuditEvent[]>([]);
  const [chain, setChain] = useState<{ checked: number; intact: boolean; first_bad_seq: number | null } | null>(null);
  const [secretFields, setSecretFields] = useState<string[]>([]);
  const [secrets, setSecrets] = useState<Record<string, string>>({});
  const [error, setError] = useState<unknown>(null);

  const load = useCallback(async () => {
    try {
      const q = status ? `?status=${status}&limit=200` : "?limit=200";
      setJobs((await get<{ items: Job[] }>(`/jobs${q}`)).items);
      setEvents((await get<{ items: AuditEvent[] }>("/audit?limit=200")).items);
    } catch (e) {
      setError(e);
    }
  }, [status]);
  useEffect(() => {
    void load();
  }, [load]);

  const openJob = useCallback(async (id: string) => {
    try {
      setDetail(await get<Job>(`/jobs/${id}`));
      setSecretFields([]);
    } catch (e) {
      setError(e);
    }
  }, []);
  useEffect(() => {
    const j = search.get("job");
    if (j) void openJob(j);
  }, [search, openJob]);

  async function resume(job: Job) {
    setError(null);
    try {
      await post(`/jobs/${job.id}/resume`, { secrets });
      setSecrets({});
      setSecretFields([]);
      void openJob(job.id);
      void load();
    } catch (e) {
      if (e instanceof ApiError && e.code === "secrets_required") setSecretFields((e.details.fields as string[]) ?? []);
      else setError(e);
    }
  }

  return (
    <>
      <h1>Processing history</h1>
      <p className="lead">Jobs survive restarts. Interrupted or failed jobs can be resumed; OCR resumes from its last completed page.</p>
      <ErrorBox error={error} />
      {detail && (
        <section className="card">
          <div className="row">
            <h2 style={{ margin: 0 }}>{opLabel(detail.kind)}</h2>
            <StatusBadge status={detail.status} />
            <span className="spacer" />
            <button className="small" onClick={() => setDetail(null)}>
              Close
            </button>
          </div>
          {(detail.status === "queued" || detail.status === "running") && <JobProgress jobId={detail.id} onDone={() => void openJob(detail.id)} />}
          <dl className="kv" style={{ marginTop: 12 }}>
            <dt>Job ID</dt><dd className="mono">{detail.id}</dd>
            <dt>Created / started / finished</dt><dd>{formatDate(detail.created_at)} / {formatDate(detail.started_at)} / {formatDate(detail.finished_at)}</dd>
            <dt>Duration</dt><dd>{formatDuration(detail.duration_ms)}</dd>
            <dt>Attempts</dt><dd>{detail.attempts}</dd>
            <dt>Parameters (sanitised)</dt><dd className="mono">{JSON.stringify(detail.params)}</dd>
            <dt>Inputs</dt><dd className="mono">{detail.inputs?.map((i) => `#${i.position} ${shortHash(i.sha256)}`).join(", ") || "—"}</dd>
            <dt>Outputs</dt><dd className="mono">{detail.outputs.map((o) => `${o.label}: ${shortHash(o.sha256)}`).join(", ") || "—"}</dd>
            <dt>Tool versions</dt><dd className="mono">{Object.entries((detail as Job & { tool_versions?: Record<string, string> }).tool_versions ?? {}).map(([k, v]) => `${k} ${v ?? "n/a"}`).join(", ")}</dd>
          </dl>
          {detail.error && <div className="notice err">{detail.error.message}</div>}
          {detail.warnings.length > 0 && <div className="notice warn"><ul>{detail.warnings.map((w, i) => <li key={i}>{w}</li>)}</ul></div>}
          <h3>Steps</h3>
          <ol>
            {detail.steps?.map((s, i) => (
              <li key={i}>
                {s.name} — {s.status} <span className="muted">{formatDate(s.started_at)}</span>
              </li>
            ))}
          </ol>
          {detail.resumable && (
            <div className="card">
              {secretFields.length > 0 && (
                <div className="stack">
                  <p>This job needs its secret parameters again (they are never stored):</p>
                  {secretFields.map((f) => (
                    <div key={f} className="field" style={{ maxWidth: 320 }}>
                      <label htmlFor={`s-${f}`}>{f}</label>
                      <input id={`s-${f}`} type="password" value={secrets[f] ?? ""} onChange={(e) => setSecrets({ ...secrets, [f]: e.target.value })} />
                    </div>
                  ))}
                </div>
              )}
              <button className="primary" onClick={() => void resume(detail)}>
                Resume job
              </button>
            </div>
          )}
        </section>
      )}
      <h2>Jobs</h2>
      <div className="row" style={{ marginBottom: 8 }}>
        <label htmlFor="st">Status</label>
        <select id="st" value={status} onChange={(e) => setStatus(e.target.value)} style={{ width: "auto" }}>
          <option value="">All</option>
          <option value="queued,running">Active</option>
          <option value="succeeded">Succeeded</option>
          <option value="failed,interrupted">Failed / interrupted</option>
          <option value="cancelled">Cancelled</option>
        </select>
        <button className="small" onClick={() => void load()}>
          Refresh
        </button>
      </div>
      <div className="table-wrap" style={{ maxHeight: 420, overflowY: "auto" }}>
        <table>
          <thead>
            <tr>
              <th>Operation</th>
              <th>Status</th>
              <th>Created</th>
              <th>Duration</th>
              <th>Details</th>
            </tr>
          </thead>
          <tbody>
            {jobs.map((j) => (
              <tr key={j.id}>
                <td>{opLabel(j.kind)}</td>
                <td>
                  <StatusBadge status={j.status} />
                </td>
                <td>{formatDate(j.created_at)}</td>
                <td>{formatDuration(j.duration_ms)}</td>
                <td>
                  <button className="small" onClick={() => void openJob(j.id)}>
                    Open
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <h2>Audit log</h2>
      <div className="row" style={{ marginBottom: 8 }}>
        <button className="small" onClick={async () => setChain(await get("/audit/verify"))}>
          Verify hash chain
        </button>
        {chain && (
          <span className={`badge ${chain.intact ? "ok" : "err"}`}>
            {chain.intact ? `intact (${chain.checked} events)` : `broken at event #${chain.first_bad_seq}`}
          </span>
        )}
        <a className="button small" href={`${API}/audit/export?format=json`}>
          Export JSON
        </a>
        <a className="button small" href={`${API}/audit/export?format=csv`}>
          Export CSV
        </a>
      </div>
      <p className="hint">
        Append-only: the application database role cannot update or delete events, and each event is hash-chained to the
        previous one. This detects accidental changes; it cannot stop the computer&apos;s administrator.
      </p>
      <div className="table-wrap" style={{ maxHeight: 420, overflowY: "auto" }}>
        <table>
          <thead>
            <tr>
              <th>#</th>
              <th>Time</th>
              <th>Event</th>
              <th>Status</th>
              <th>Operation</th>
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
                <td>{e.operation}</td>
                <td className="mono">{shortHash(e.output_sha256)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}

export default function HistoryPage() {
  return (
    <Suspense fallback={<p className="muted">Loading…</p>}>
      <HistoryInner />
    </Suspense>
  );
}
