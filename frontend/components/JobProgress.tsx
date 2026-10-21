"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { downloadUrl, get, post } from "@/lib/api";
import { formatBytes, formatDuration, opLabel, progressPercent } from "@/lib/format";
import type { Job } from "@/lib/types";
import { ErrorBox } from "./ErrorBox";

const TERMINAL = new Set(["succeeded", "failed", "cancelled", "interrupted"]);

export function useJob(jobId: string | null, intervalMs = 1000): { job: Job | null; error: unknown } {
  const [job, setJob] = useState<Job | null>(null);
  const [error, setError] = useState<unknown>(null);
  useEffect(() => {
    if (!jobId) {
      setJob(null);
      return;
    }
    let stop = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const tick = async () => {
      try {
        const j = await get<Job>(`/jobs/${jobId}`);
        if (stop) return;
        setJob(j);
        setError(null);
        if (!TERMINAL.has(j.status)) timer = setTimeout(tick, intervalMs);
      } catch (e) {
        if (stop) return;
        setError(e);
        timer = setTimeout(tick, intervalMs * 3);
      }
    };
    void tick();
    return () => {
      stop = true;
      if (timer) clearTimeout(timer);
    };
  }, [jobId, intervalMs]);
  return { job, error };
}

export function ProgressBar({ current, total, label }: { current?: number | null; total?: number | null; label: string }) {
  const pct = progressPercent(current, total);
  return (
    <div>
      <div
        className={`progress${pct === null ? " indeterminate" : ""}`}
        role="progressbar"
        aria-label={label}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={pct ?? undefined}
        aria-valuetext={pct === null ? `${label} (in progress)` : `${pct}%`}
      >
        <div style={pct === null ? undefined : { width: `${pct}%` }} />
      </div>
    </div>
  );
}

export function JobProgress({ jobId, onDone }: { jobId: string; onDone?: (job: Job) => void }) {
  const { job, error } = useJob(jobId);
  const [cancelling, setCancelling] = useState(false);
  const [notified, setNotified] = useState(false);

  useEffect(() => {
    if (job && TERMINAL.has(job.status) && !notified) {
      setNotified(true);
      onDone?.(job);
    }
  }, [job, notified, onDone]);

  if (error && !job) return <ErrorBox error={error} />;
  if (!job) return <p className="muted">Loading job…</p>;
  const p = job.progress ?? {};
  const running = job.status === "queued" || job.status === "running";
  const stepCount = p.total ? ` (${p.current ?? 0}/${p.total})` : "";
  return (
    <section className="card" aria-live="polite">
      <div className="row">
        <strong>{opLabel(job.kind)}</strong>
        <StatusBadge status={job.status} />
        <span className="spacer" />
        {running && (
          <button
            className="small danger"
            disabled={cancelling}
            onClick={async () => {
              setCancelling(true);
              try {
                await post(`/jobs/${job.id}/cancel`);
              } finally {
                setCancelling(false);
              }
            }}
          >
            Cancel
          </button>
        )}
        <Link className="small button" href={`/history?job=${job.id}`}>
          Details
        </Link>
      </div>
      {running && (
        <>
          <p className="muted" style={{ margin: "8px 0 0" }}>
            {job.status === "queued" ? "Waiting in queue…" : `Stage: ${p.stage ?? "working"}${stepCount}`}
          </p>
          <ProgressBar current={p.current} total={p.total} label={p.stage ?? "Processing"} />
        </>
      )}
      {job.status === "succeeded" && (
        <div className="notice ok">
          Completed in {formatDuration(job.duration_ms)}.
          {job.outputs.length > 0 && (
            <ul>
              {job.outputs.map((o) => (
                <li key={o.version_id}>
                  {o.label} — {formatBytes(o.size_bytes)} <span className="mono">sha256 {o.sha256.slice(0, 12)}…</span>{" "}
                  <a href={downloadUrl(o.version_id)}>Download</a> · <Link href={`/library/${o.document_id}`}>Open</Link>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
      {job.error && job.status !== "succeeded" && (
        <div className={`notice ${job.status === "cancelled" ? "warn" : "err"}`} role="alert">
          {job.error.message}
          {job.resumable && (
            <div className="hint">
              This job can be resumed from <Link href={`/history?job=${job.id}`}>Processing history</Link>.
            </div>
          )}
        </div>
      )}
      {job.warnings.length > 0 && (
        <div className="notice warn">
          <strong>Warnings</strong>
          <ul>
            {job.warnings.map((w, i) => (
              <li key={i}>{w}</li>
            ))}
          </ul>
        </div>
      )}
    </section>
  );
}

export function StatusBadge({ status }: { status: string }) {
  const cls =
    status === "succeeded" || status === "approved" || status === "verified"
      ? "ok"
      : status === "failed"
        ? "err"
        : status === "cancelled" || status === "interrupted" || status === "review"
          ? "warn"
          : "";
  return <span className={`badge ${cls}`}>{status}</span>;
}
