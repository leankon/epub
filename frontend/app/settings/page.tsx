"use client";

import { useEffect, useState } from "react";
import { ErrorBox } from "@/components/ErrorBox";
import { get } from "@/lib/api";
import type { ToolStatus } from "@/lib/types";

export default function SettingsPage() {
  const [settings, setSettings] = useState<Record<string, unknown> | null>(null);
  const [tools, setTools] = useState<ToolStatus[]>([]);
  const [error, setError] = useState<unknown>(null);
  useEffect(() => {
    Promise.all([get<Record<string, unknown>>("/settings"), get<{ tools: ToolStatus[] }>("/diagnostics")])
      .then(([s, d]) => {
        setSettings(s);
        setTools(d.tools);
      })
      .catch(setError);
  }, []);
  return (
    <>
      <h1>Settings</h1>
      <p className="lead">Configuration is read from the <span className="mono">.env</span> file at the repository root. Restart after editing it.</p>
      <ErrorBox error={error} />
      <section className="card">
        <h2 style={{ marginTop: 0 }}>Privacy &amp; security</h2>
        <ul>
          <li>All processing (OCR, conversion, rendering, validation) runs on this computer. No accounts, telemetry, analytics or cloud services.</li>
          <li>The application listens on 127.0.0.1 only unless PDFWS_ALLOW_NETWORK is set. Requests need a local session token that browser code never sees.</li>
          <li>Local processing does not protect against malware, a compromised computer, other users of this computer, or an unencrypted disk. Use full-disk encryption and an OS account only you can access.</li>
          <li>Passwords and redaction terms are kept in memory only and are never written to the database or logs.</li>
        </ul>
      </section>
      <section className="card">
        <h2 style={{ marginTop: 0 }}>Effective configuration</h2>
        {settings && (
          <dl className="kv">
            {Object.entries(settings).map(([k, v]) => (
              <div key={k} style={{ display: "contents" }}>
                <dt>{k}</dt>
                <dd className="mono">{String(v)}</dd>
              </div>
            ))}
          </dl>
        )}
      </section>
      <section className="card">
        <h2 style={{ marginTop: 0 }}>Local tools</h2>
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Tool</th>
                <th>Status</th>
                <th>Version</th>
                <th>Needed for</th>
                <th>Notes / how to install</th>
              </tr>
            </thead>
            <tbody>
              {tools.map((t) => (
                <tr key={t.key}>
                  <td>{t.name}</td>
                  <td>
                    <span className={`badge ${t.available ? "ok" : "err"}`}>{t.available ? "available" : "missing"}</span>
                  </td>
                  <td>{t.version ?? "—"}</td>
                  <td>{t.required_for.join(", ")}</td>
                  <td className="muted">{t.available ? t.detail : `${t.detail ?? ""} ${t.install_hint}`}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
    </>
  );
}
