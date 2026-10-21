"use client";

import { useState } from "react";
import { ApiError, submitJob } from "@/lib/api";

/**
 * Submit a job. If the backend asks for confirmation (signatures/forms would be affected) the
 * warnings are exposed so the user can explicitly confirm and resubmit.
 */
export function useJobRunner() {
  const [jobId, setJobId] = useState<string | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const [confirm, setConfirm] = useState<{ warnings: string[]; retry: () => Promise<void> } | null>(null);

  async function run(kind: string, versionIds: string[], params: Record<string, unknown>, ack = false): Promise<void> {
    setBusy(true);
    setError(null);
    setConfirm(null);
    try {
      const job = await submitJob(kind, versionIds, params, { acknowledgeWarnings: ack });
      setJobId(job.id);
    } catch (e) {
      if (e instanceof ApiError && e.code === "confirmation_required") {
        setConfirm({
          warnings: (e.details.warnings as string[]) ?? [e.message],
          retry: () => run(kind, versionIds, params, true),
        });
      } else setError(e);
    } finally {
      setBusy(false);
    }
  }

  return { jobId, setJobId, error, setError, busy, confirm, run };
}

export function ConfirmWarnings({ confirm }: { confirm: { warnings: string[]; retry: () => Promise<void> } | null }) {
  const [ok, setOk] = useState(false);
  if (!confirm) return null;
  return (
    <div className="notice warn" role="alertdialog" aria-label="Confirmation required">
      <strong>This operation may alter protected features of the document:</strong>
      <ul>
        {confirm.warnings.map((w, i) => (
          <li key={i}>{w}</li>
        ))}
      </ul>
      <label className="check">
        <input type="checkbox" checked={ok} onChange={(e) => setOk(e.target.checked)} /> I understand and want to
        continue. The original file stays unchanged.
      </label>
      <button className="primary" disabled={!ok} onClick={() => void confirm.retry()}>
        Continue
      </button>
    </div>
  );
}
