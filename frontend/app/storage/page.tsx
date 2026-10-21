"use client";

import { useCallback, useEffect, useState } from "react";
import { ErrorBox } from "@/components/ErrorBox";
import { JobProgress, StatusBadge } from "@/components/JobProgress";
import { API, get, post, put } from "@/lib/api";
import { formatBytes, formatDate } from "@/lib/format";

interface Policy {
  id: string;
  enabled: boolean;
  max_age_days: number;
  keep_latest: boolean;
}
interface StorageInfo {
  usage: Record<string, number>;
  policies: Policy[];
  documents: number;
  versions: number;
  data_dir: string;
}
interface Backup {
  id: string | null;
  directory: string;
  created_at?: string;
  status: string;
  file_count?: number;
  total_bytes?: number;
  manifest_sha256?: string | null;
  verified_at?: string | null;
  present?: boolean;
}

const POLICY_TEXT: Record<string, string> = {
  derived: "Derived versions (outputs of operations). The latest version of each document is kept if selected.",
  original: "Whole documents including their immutable originals. Only enable this if you really want automatic deletion.",
  temp: "Temporary processing files (always cleaned after PDFWS_TEMP_MAX_AGE_HOURS).",
};

export default function StoragePage() {
  const [info, setInfo] = useState<StorageInfo | null>(null);
  const [backups, setBackups] = useState<Backup[]>([]);
  const [error, setError] = useState<unknown>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const [jobId, setJobId] = useState<string | null>(null);
  const [verify, setVerify] = useState<Record<string, { ok: boolean; problems: string[]; checked: number }>>({});
  const [restoreTarget, setRestoreTarget] = useState<string | null>(null);
  const [restoreConfirm, setRestoreConfirm] = useState("");
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      setInfo(await get<StorageInfo>("/storage"));
      setBackups((await get<{ items: Backup[] }>("/backups")).items);
    } catch (e) {
      setError(e);
    }
  }, []);
  useEffect(() => {
    void load();
  }, [load]);

  async function act(fn: () => Promise<unknown>, done?: string) {
    setError(null);
    setMsg(null);
    setBusy(true);
    try {
      const r = await fn();
      if (done) setMsg(done);
      void load();
      return r;
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  }

  const total = info ? Object.values(info.usage).reduce((a, b) => a + b, 0) : 0;
  return (
    <>
      <h1>Storage &amp; backups</h1>
      <p className="lead">
        Data directory: <span className="mono">{info?.data_dir}</span>
      </p>
      <ErrorBox error={error} />
      {msg && <div className="notice ok">{msg}</div>}
      <section className="card">
        <h2 style={{ marginTop: 0 }}>Usage — {formatBytes(total)}</h2>
        <p>
          {info?.documents ?? "—"} documents · {info?.versions ?? "—"} retained versions
        </p>
        {info &&
          Object.entries(info.usage).map(([k, v]) => (
            <div key={k} style={{ marginBottom: 6 }}>
              <div className="row">
                <span style={{ width: 140 }}>{k.replace("_", " ")}</span>
                <span className="muted">{formatBytes(v)}</span>
              </div>
              <div className="progress" aria-hidden="true">
                <div style={{ width: `${total ? (100 * v) / total : 0}%` }} />
              </div>
            </div>
          ))}
        <div className="row">
          <button disabled={busy} onClick={() => act(async () => {
            const r = await post<{ ok: boolean; checked: number; problems: unknown[] }>("/storage/verify");
            setMsg(r.ok ? `All ${r.checked} stored files match their SHA-256 hashes.` : `${r.problems.length} problem(s) found.`);
          })}>
            Verify stored file hashes
          </button>
          <button disabled={busy} onClick={() => act(async () => {
            const r = await post<Record<string, number>>("/storage/sweep");
            setMsg(`Expiration run: ${r.expired_versions} version(s) expired, ${r.temp_entries_removed} temporary entr(ies) removed.`);
          })}>
            Apply expiration policies now
          </button>
          <a className="button" href={`${API}/export`}>
            Export all documents + metadata (ZIP)
          </a>
        </div>
      </section>

      <section className="card">
        <h2 style={{ marginTop: 0 }}>Expiration policies</h2>
        <p className="hint">Defaults are conservative: nothing you imported or produced expires unless you enable it here.</p>
        {info?.policies.map((p) => (
          <PolicyRow key={p.id} policy={p} onSave={(np) => act(() => put(`/storage/policies/${p.id}`, np), "Policy saved.")} />
        ))}
      </section>

      <section className="card">
        <h2 style={{ marginTop: 0 }}>Backups</h2>
        <p>
          A backup contains the database metadata (including the audit log) and every stored document file, with a
          SHA-256 manifest. It is only marked <em>verified</em> after re-hashing every file. Backups are stored in{" "}
          <span className="mono">data/backups</span>; copy them to an external disk for real protection.
        </p>
        <button className="primary" disabled={busy || !!jobId} onClick={() => act(async () => setJobId((await post<{ job_id: string }>("/backups")).job_id))}>
          Create backup
        </button>
        {jobId && <JobProgress key={jobId} jobId={jobId} onDone={() => { setJobId(null); void load(); }} />}
        <div className="table-wrap" style={{ marginTop: 12 }}>
          <table>
            <thead>
              <tr>
                <th>Backup</th>
                <th>Status</th>
                <th>Files</th>
                <th>Size</th>
                <th>Actions</th>
              </tr>
            </thead>
            <tbody>
              {backups.map((b) => {
                const key = b.id ?? b.directory;
                const v = verify[key];
                return (
                  <tr key={key}>
                    <td>
                      <span className="mono">{b.directory}</span>
                      <div className="hint">{formatDate(b.created_at)}</div>
                    </td>
                    <td>
                      <StatusBadge status={b.status} />
                      {b.present === false && <span className="badge err">missing on disk</span>}
                      {v && <span className={`badge ${v.ok ? "ok" : "err"}`}>{v.ok ? `re-verified (${v.checked})` : `${v.problems.length} problem(s)`}</span>}
                    </td>
                    <td>{b.file_count ?? "—"}</td>
                    <td>{formatBytes(b.total_bytes)}</td>
                    <td>
                      <button className="small" disabled={busy} onClick={() => act(async () => {
                        const r = await post<{ ok: boolean; problems: string[]; checked: number }>(`/backups/${key}/verify`);
                        setVerify((x) => ({ ...x, [key]: r }));
                      })}>
                        Verify
                      </button>{" "}
                      <button className="small danger" disabled={busy} onClick={() => { setRestoreTarget(key); setRestoreConfirm(""); }}>
                        Restore…
                      </button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
        {restoreTarget && (
          <div className="notice warn" role="alertdialog" aria-label="Confirm restore">
            <strong>Restore {restoreTarget}?</strong> The current database contents and document files are replaced by
            the backup. A safety backup of the current state is made first, and current files are moved to{" "}
            <span className="mono">data/quarantine</span> (not deleted). Running jobs must finish first.
            <div className="row" style={{ marginTop: 8 }}>
              <label htmlFor="rc">Type RESTORE</label>
              <input id="rc" type="text" value={restoreConfirm} onChange={(e) => setRestoreConfirm(e.target.value)} style={{ maxWidth: 160 }} />
              <button className="danger" disabled={restoreConfirm !== "RESTORE" || busy} onClick={() => act(async () => {
                await post(`/backups/${restoreTarget}/restore`, { confirm: restoreConfirm, safety_backup: true });
                setRestoreTarget(null);
              }, "Restore completed and stored files verified.")}>
                Restore now
              </button>
              <button onClick={() => setRestoreTarget(null)}>Cancel</button>
            </div>
          </div>
        )}
      </section>
    </>
  );
}

function PolicyRow({ policy, onSave }: { policy: Policy; onSave: (p: Omit<Policy, "id">) => void }) {
  const [p, setP] = useState(policy);
  return (
    <fieldset>
      <legend>{policy.id}</legend>
      <p className="hint">{POLICY_TEXT[policy.id]}</p>
      <div className="row">
        <label className="check">
          <input type="checkbox" checked={p.enabled} onChange={(e) => setP({ ...p, enabled: e.target.checked })} /> Enabled
        </label>
        <label className="check">
          Expire after
          <input type="number" min={1} value={p.max_age_days} onChange={(e) => setP({ ...p, max_age_days: Number(e.target.value) })} style={{ width: 90 }} />
          days
        </label>
        {policy.id === "derived" && (
          <label className="check">
            <input type="checkbox" checked={p.keep_latest} onChange={(e) => setP({ ...p, keep_latest: e.target.checked })} /> Keep the latest version
          </label>
        )}
        <button className="small" onClick={() => onSave({ enabled: p.enabled, max_age_days: p.max_age_days, keep_latest: p.keep_latest })}>
          Save
        </button>
      </div>
      {policy.id === "original" && p.enabled && <div className="notice err">Warning: documents and their originals will be deleted automatically after {p.max_age_days} days.</div>}
    </fieldset>
  );
}
