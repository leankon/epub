"use client";

import Link from "next/link";
import { useState } from "react";
import { post } from "@/lib/api";
import { formatBytes } from "@/lib/format";
import { DocumentPicker } from "./DocumentPicker";
import { ErrorBox } from "./ErrorBox";

type Info = Record<string, unknown> & {
  page_sizes?: [number, number, number][];
  signatures?: { field: string; signed: boolean; sub_filter?: string; signing_time?: string; signer_name?: string; covers_whole_file?: boolean }[];
  warnings?: string[];
};

export function InspectTool() {
  const [selected, setSelected] = useState<string[]>([]);
  const [password, setPassword] = useState("");
  const [info, setInfo] = useState<Info | null>(null);
  const [error, setError] = useState<unknown>(null);

  async function inspect() {
    setError(null);
    try {
      setInfo(await post<Info>(`/versions/${selected[0]}/inspect`, password ? { password } : undefined));
    } catch (e) {
      setError(e);
    }
  }

  const sizes = info?.page_sizes ?? [];
  const distinct = Array.from(new Set(sizes.map((s) => `${s[0]} × ${s[1]} pt${s[2] ? ` (rotated ${s[2]}°)` : ""}`)));
  return (
    <>
      <p>
        <Link href="/tools">← PDF tools</Link>
      </p>
      <h1>Inspect &amp; integrity</h1>
      <p className="lead">Read-only inspection. Nothing is modified.</p>
      <section className="card">
        <DocumentPicker exts={["pdf"]} selected={selected} onChange={(v) => { setSelected(v); setInfo(null); }} />
        <div className="field" style={{ maxWidth: 320, marginTop: 12 }}>
          <label htmlFor="ipw">Password (only for encrypted files; not stored)</label>
          <input id="ipw" type="password" value={password} onChange={(e) => setPassword(e.target.value)} />
        </div>
        <button className="primary" disabled={!selected.length} onClick={() => void inspect()}>
          Inspect
        </button>
        <ErrorBox error={error} />
      </section>
      {info && (
        <section className="card">
          {info.warnings && info.warnings.length > 0 && (
            <div className="notice warn">
              <ul>{info.warnings.map((w, i) => <li key={i}>{w}</li>)}</ul>
            </div>
          )}
          <dl className="kv">
            <dt>SHA-256</dt><dd className="mono">{String(info.sha256 ?? "")}</dd>
            <dt>Size</dt><dd>{formatBytes(info.size_bytes as number)}</dd>
            <dt>PDF version</dt><dd>{String(info.pdf_version ?? "—")}</dd>
            <dt>Pages</dt><dd>{String(info.page_count ?? "—")}</dd>
            <dt>Page sizes</dt><dd>{distinct.join("; ") || "—"}</dd>
            <dt>Title / author</dt><dd>{String(info.title ?? "—")} / {String(info.author ?? "—")}</dd>
            <dt>Language</dt><dd>{String(info.language ?? "—")}</dd>
            <dt>Producer</dt><dd>{String(info.producer ?? "—")}</dd>
            <dt>Encrypted</dt><dd>{info.encrypted ? "yes" : "no"}</dd>
            <dt>Form fields</dt><dd>{String(info.form_field_count ?? 0)}</dd>
            <dt>Annotations</dt><dd>{String(info.annotation_count ?? 0)}</dd>
            <dt>Attachments</dt><dd>{String(info.attachment_count ?? 0)}</dd>
            <dt>Bookmarks</dt><dd>{String(info.outline_count ?? 0)}</dd>
            <dt>Incremental revisions</dt><dd>{info.revisions ? String(Number(info.revisions) - 1) : "0"}</dd>
            <dt>JavaScript / auto actions</dt><dd>{info.has_javascript ? "present (never executed)" : "none"}</dd>
          </dl>
          <h3>Digital signatures</h3>
          {info.signatures && info.signatures.length > 0 ? (
            <>
              <ul>
                {info.signatures.map((s, i) => (
                  <li key={i}>
                    {s.field}: {s.signed ? "signed" : "empty field"}
                    {s.sub_filter ? ` · ${s.sub_filter}` : ""}
                    {s.signing_time ? ` · ${s.signing_time}` : ""}
                    {s.signer_name ? ` · ${s.signer_name}` : ""}
                    {s.signed && (s.covers_whole_file ? " · byte range covers the whole file" : " · ⚠ byte range does not cover the whole file (later changes exist)")}
                  </li>
                ))}
              </ul>
              <p className="hint">
                This is structural inspection only. Cryptographic validation of signer identity is not performed, and
                this application is not a qualified electronic-signature platform.
              </p>
            </>
          ) : (
            <p className="muted">No signature fields.</p>
          )}
        </section>
      )}
    </>
  );
}
