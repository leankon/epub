"use client";

import { useEffect, useState, type ReactNode } from "react";
import { API, get } from "@/lib/api";
import { initialPages, isUnchanged, toOrderPayload, type PageItem } from "@/lib/organizer";
import { parseRanges } from "@/lib/ranges";
import type { DocumentSummary, Version } from "@/lib/types";
import { BookmarkEditor, flatten, nest, type BookmarkNode, type FlatBookmark } from "./BookmarkEditor";
import { Organizer } from "./Organizer";
import { RedactEditor, type Area } from "./RedactEditor";

export interface ToolContext {
  selected: string[];
  setSelected: (v: string[]) => void;
  docs: DocumentSummary[];
  version: Version | null; // first selected version
  params: Record<string, unknown>;
  set: (patch: Record<string, unknown>) => void;
}

export interface ToolDef {
  id: string;
  kind: string;
  title: string;
  description: string;
  exts: string[];
  multiple?: boolean;
  defaults: Record<string, unknown>;
  Options?: (ctx: ToolContext) => ReactNode;
  validate?: (ctx: ToolContext) => string | null;
  build?: (ctx: ToolContext) => Record<string, unknown>;
  note?: string;
}

const str = (v: unknown) => (typeof v === "string" ? v : v === undefined || v === null ? "" : String(v));

function Field({ id, label, hint, children }: { id: string; label: string; hint?: string; children: ReactNode }) {
  return (
    <div className="field">
      <label htmlFor={id}>{label}</label>
      {children}
      {hint && <div className="hint">{hint}</div>}
    </div>
  );
}

function RangeInput({ ctx, name, label, optional = true }: { ctx: ToolContext; name: string; label: string; optional?: boolean }) {
  const value = str(ctx.params[name]);
  const n = ctx.version?.page_count ?? 0;
  const check = value && n ? parseRanges(value, n) : null;
  return (
    <Field id={name} label={label} hint={optional ? "Leave empty for all pages. Example: 1-3, 5, 8-" : "Example: 1-3, 4-6, 7-"}>
      <input id={name} type="text" value={value} onChange={(e) => ctx.set({ [name]: e.target.value })} aria-invalid={check?.ok === false} />
      {check && !check.ok && <div className="hint" style={{ color: "var(--err)" }}>{check.error}</div>}
    </Field>
  );
}

function rangeError(ctx: ToolContext, name: string, required = false): string | null {
  const v = str(ctx.params[name]);
  if (!v) return required ? "Enter a page range." : null;
  const n = ctx.version?.page_count;
  if (!n) return null;
  const r = parseRanges(v, n);
  return r.ok ? null : r.error;
}

const POSITIONS = ["center", "top-left", "top-center", "top-right", "middle-left", "middle-right", "bottom-left", "bottom-center", "bottom-right", "tile"];

function Preview({ ctx, kind }: { ctx: ToolContext; kind: "watermark" | "page_numbers" }) {
  const [url, setUrl] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => () => { if (url) URL.revokeObjectURL(url); }, [url]);
  if (!ctx.version) return null;
  return (
    <div className="field">
      <button
        type="button"
        onClick={async () => {
          setErr(null);
          const res = await fetch(`${API}/versions/${ctx.version!.id}/preview?w=520`, {
            method: "POST",
            headers: { "content-type": "application/json", "x-pdfws-client": "1" },
            body: JSON.stringify({ kind, params: Object.fromEntries(Object.entries(ctx.params).filter(([, v]) => v !== "")) }),
          });
          if (!res.ok) {
            setErr("Preview failed — check the options.");
            return;
          }
          setUrl(URL.createObjectURL(await res.blob()));
        }}
      >
        Preview first page
      </button>
      {err && <div className="hint" style={{ color: "var(--err)" }}>{err}</div>}
      {url && <img src={url} alt="Preview of the first page" style={{ display: "block", marginTop: 8, maxWidth: 520, border: "1px solid var(--border)" }} />}
    </div>
  );
}

function ImageVersionSelect({ ctx }: { ctx: ToolContext }) {
  const [imgs, setImgs] = useState<DocumentSummary[]>([]);
  useEffect(() => {
    void get<{ items: DocumentSummary[] }>("/documents?kind=image&limit=200").then((r) => setImgs(r.items));
  }, []);
  return (
    <Field id="wm-image" label="Watermark image" hint="Import PNG/JPEG images in the Document library first.">
      <select id="wm-image" value={str(ctx.params.image_version_id)} onChange={(e) => ctx.set({ image_version_id: e.target.value })}>
        <option value="">— choose —</option>
        {imgs.map((d) => (
          <option key={d.id} value={d.latest_version?.id}>
            {d.title}
          </option>
        ))}
      </select>
    </Field>
  );
}

function OrganizeOptions(ctx: ToolContext) {
  const n = ctx.version?.page_count ?? 0;
  const items = (ctx.params.items as PageItem[] | undefined) ?? initialPages(n);
  useEffect(() => {
    if (ctx.version && !ctx.params.items) ctx.set({ items: initialPages(n) });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ctx.version?.id]);
  if (!ctx.version || !n) return <p className="muted">Choose a PDF to see its pages.</p>;
  return <Organizer versionId={ctx.version.id} pageCount={n} items={items} onChange={(it) => ctx.set({ items: it })} />;
}

function BookmarksOptions(ctx: ToolContext) {
  const [loaded, setLoaded] = useState<string | null>(null);
  useEffect(() => {
    if (ctx.version && loaded !== ctx.version.id) {
      setLoaded(ctx.version.id);
      void get<{ items: BookmarkNode[] }>(`/versions/${ctx.version.id}/outline`).then((r) => ctx.set({ flat: flatten(r.items) }));
    }
  }, [ctx, loaded]);
  if (!ctx.version) return <p className="muted">Choose a PDF to edit its bookmarks.</p>;
  return <BookmarkEditor items={(ctx.params.flat as FlatBookmark[]) ?? []} pageCount={ctx.version.page_count ?? 1} onChange={(f) => ctx.set({ flat: f })} />;
}

export const TOOLS: ToolDef[] = [
  {
    id: "merge", kind: "merge", title: "Merge PDFs", description: "Combine PDFs in the order you select them.", exts: ["pdf"], multiple: true,
    defaults: { title: "" },
    validate: (c) => (c.selected.length < 2 ? "Select at least two PDFs." : null),
    note: "Bookmarks are kept (grouped per file). Form fields, named destinations and attachments are reported if they cannot be preserved.",
    Options: (c) => (
      <>
        <h3>Order</h3>
        <ol>
          {c.selected.map((v, i) => {
            const d = c.docs.find((x) => x.latest_version?.id === v);
            return (
              <li key={v}>
                {d?.title ?? v}{" "}
                <button type="button" className="small" disabled={i === 0} aria-label="Move up" onClick={() => { const n = c.selected.slice(); [n[i - 1], n[i]] = [n[i]!, n[i - 1]!]; c.setSelected(n); }}>↑</button>{" "}
                <button type="button" className="small" disabled={i === c.selected.length - 1} aria-label="Move down" onClick={() => { const n = c.selected.slice(); [n[i + 1], n[i]] = [n[i]!, n[i + 1]!]; c.setSelected(n); }}>↓</button>
              </li>
            );
          })}
        </ol>
        <Field id="title" label="Title of the merged document (optional)">
          <input id="title" type="text" value={str(c.params.title)} onChange={(e) => c.set({ title: e.target.value })} />
        </Field>
      </>
    ),
    build: (c) => (c.params.title ? { title: c.params.title } : {}),
  },
  {
    id: "split", kind: "split", title: "Split PDF", description: "Split by page ranges, extract pages, every page, or bookmarks.", exts: ["pdf"],
    defaults: { mode: "ranges", ranges: "" },
    validate: (c) => (["ranges", "extract"].includes(str(c.params.mode)) ? rangeError(c, "ranges", true) : null),
    Options: (c) => (
      <>
        <fieldset>
          <legend>Mode</legend>
          {[["ranges", "One file per range"], ["extract", "Extract selected pages into one file"], ["every", "Every page as its own file"], ["bookmarks", "By top-level bookmarks"]].map(([v, l]) => (
            <label key={v} className="check">
              <input type="radio" name="mode" checked={c.params.mode === v} onChange={() => c.set({ mode: v })} /> {l}
            </label>
          ))}
        </fieldset>
        {["ranges", "extract"].includes(str(c.params.mode)) && <RangeInput ctx={c} name="ranges" label="Page ranges" optional={false} />}
      </>
    ),
    build: (c) => ({ mode: c.params.mode, ...(c.params.ranges ? { ranges: c.params.ranges } : {}) }),
  },
  {
    id: "organize", kind: "organize", title: "Organize pages", description: "Reorder (drag and drop), rotate, duplicate or remove pages.", exts: ["pdf"],
    defaults: {},
    Options: OrganizeOptions,
    validate: (c) => {
      const items = c.params.items as PageItem[] | undefined;
      if (!items?.length) return "The document must keep at least one page.";
      return isUnchanged(items, c.version?.page_count ?? 0) ? "No changes yet." : null;
    },
    build: (c) => ({ order: toOrderPayload((c.params.items as PageItem[]) ?? []) }),
  },
  {
    id: "rotate", kind: "rotate", title: "Rotate pages", description: "Rotate selected pages by 90, 180 or 270 degrees.", exts: ["pdf"],
    defaults: { angle: 90, pages: "" },
    validate: (c) => rangeError(c, "pages"),
    Options: (c) => (
      <>
        <Field id="angle" label="Rotation (clockwise)">
          <select id="angle" value={str(c.params.angle)} onChange={(e) => c.set({ angle: Number(e.target.value) })}>
            <option value="90">90°</option>
            <option value="180">180°</option>
            <option value="270">270°</option>
          </select>
        </Field>
        <RangeInput ctx={c} name="pages" label="Pages" />
      </>
    ),
    build: (c) => ({ angle: c.params.angle, ...(c.params.pages ? { pages: c.params.pages } : {}) }),
  },
  {
    id: "compress", kind: "compress", title: "Compress PDF", description: "Reduce file size with quality-oriented presets.", exts: ["pdf"],
    defaults: { preset: "balanced" },
    note: "Only 'Lossless' is lossless. Other presets recompress/downsample images; text and vector graphics are kept. The original is preserved.",
    Options: (c) => (
      <fieldset>
        <legend>Preset</legend>
        {[["lossless", "Lossless — rewrite streams and remove unused objects"], ["high_quality", "High quality — images ≤ 225 dpi, JPEG 85"], ["balanced", "Balanced — images ≤ 150 dpi, JPEG 75"], ["strong", "Strong — images ≤ 100 dpi, JPEG 60"]].map(([v, l]) => (
          <label key={v} className="check">
            <input type="radio" name="preset" checked={c.params.preset === v} onChange={() => c.set({ preset: v })} /> {l}
          </label>
        ))}
      </fieldset>
    ),
    build: (c) => ({ preset: c.params.preset }),
  },
  {
    id: "watermark", kind: "watermark", title: "Watermark", description: "Add a text or image watermark.", exts: ["pdf"],
    defaults: { type: "text", text: "CONFIDENTIAL", position: "center", opacity: 0.3, rotation: 45, font_size: 48, color: "#cc0000", image_scale: 0.4, pages: "" },
    validate: (c) => (c.params.type === "image" && !c.params.image_version_id ? "Choose a watermark image." : rangeError(c, "pages")),
    Options: (c) => (
      <>
        <div className="form-grid">
          <Field id="wtype" label="Type">
            <select id="wtype" value={str(c.params.type)} onChange={(e) => c.set({ type: e.target.value })}>
              <option value="text">Text</option>
              <option value="image">Image</option>
            </select>
          </Field>
          {c.params.type === "text" ? (
            <>
              <Field id="wtext" label="Text">
                <input id="wtext" type="text" value={str(c.params.text)} onChange={(e) => c.set({ text: e.target.value })} />
              </Field>
              <Field id="wsize" label="Font size (pt)">
                <input id="wsize" type="number" min={4} max={400} value={str(c.params.font_size)} onChange={(e) => c.set({ font_size: Number(e.target.value) })} />
              </Field>
              <Field id="wcolor" label="Colour">
                <input id="wcolor" type="color" value={str(c.params.color)} onChange={(e) => c.set({ color: e.target.value })} />
              </Field>
            </>
          ) : (
            <>
              <ImageVersionSelect ctx={c} />
              <Field id="wscale" label="Width (fraction of page)">
                <input id="wscale" type="number" step={0.05} min={0.05} max={1} value={str(c.params.image_scale)} onChange={(e) => c.set({ image_scale: Number(e.target.value) })} />
              </Field>
            </>
          )}
          <Field id="wpos" label="Position">
            <select id="wpos" value={str(c.params.position)} onChange={(e) => c.set({ position: e.target.value })}>
              {POSITIONS.map((p) => <option key={p} value={p}>{p}</option>)}
            </select>
          </Field>
          <Field id="wop" label="Opacity">
            <input id="wop" type="number" step={0.05} min={0.05} max={1} value={str(c.params.opacity)} onChange={(e) => c.set({ opacity: Number(e.target.value) })} />
          </Field>
          <Field id="wrot" label="Rotation (degrees)">
            <input id="wrot" type="number" min={-360} max={360} value={str(c.params.rotation)} onChange={(e) => c.set({ rotation: Number(e.target.value) })} />
          </Field>
        </div>
        <RangeInput ctx={c} name="pages" label="Pages" />
        <Preview ctx={c} kind="watermark" />
      </>
    ),
    build: (c) => {
      const p = { ...c.params };
      if (!p.pages) delete p.pages;
      if (p.type !== "image") delete p.image_version_id;
      return p;
    },
  },
  {
    id: "page-numbers", kind: "page_numbers", title: "Page numbers", description: "Add configurable page numbers.", exts: ["pdf"],
    defaults: { position: "bottom-center", format: "{n}", start: 1, font_size: 11, margin: 28, color: "#000000", pages: "" },
    validate: (c) => (!str(c.params.format).includes("{n}") ? "The format must contain {n}." : rangeError(c, "pages")),
    Options: (c) => (
      <>
        <div className="form-grid">
          <Field id="pnpos" label="Position">
            <select id="pnpos" value={str(c.params.position)} onChange={(e) => c.set({ position: e.target.value })}>
              {POSITIONS.filter((p) => p.startsWith("top") || p.startsWith("bottom")).map((p) => <option key={p} value={p}>{p}</option>)}
            </select>
          </Field>
          <Field id="pnfmt" label="Format" hint="{n} = number, {total} = total. E.g. Página {n} de {total}">
            <input id="pnfmt" type="text" value={str(c.params.format)} onChange={(e) => c.set({ format: e.target.value })} />
          </Field>
          <Field id="pnstart" label="First number">
            <input id="pnstart" type="number" min={0} value={str(c.params.start)} onChange={(e) => c.set({ start: Number(e.target.value) })} />
          </Field>
          <Field id="pnsize" label="Font size (pt)">
            <input id="pnsize" type="number" min={4} max={72} value={str(c.params.font_size)} onChange={(e) => c.set({ font_size: Number(e.target.value) })} />
          </Field>
          <Field id="pnmargin" label="Margin (pt)">
            <input id="pnmargin" type="number" min={0} max={300} value={str(c.params.margin)} onChange={(e) => c.set({ margin: Number(e.target.value) })} />
          </Field>
        </div>
        <RangeInput ctx={c} name="pages" label="Pages to number" />
        <Preview ctx={c} kind="page_numbers" />
      </>
    ),
    build: (c) => {
      const p = { ...c.params };
      if (!p.pages) delete p.pages;
      return p;
    },
  },
  {
    id: "redact", kind: "redact", title: "Redact (permanent)", description: "Remove text, image pixels and annotations under marked areas or matching terms.", exts: ["pdf"],
    defaults: { termsText: "", match_case: false, areas: [], sanitize: { metadata: true, attachments: true, javascript: true, structure_tree: true, bookmarks: true, all_annotations: false } },
    note: "Underlying content is removed and the result is verified with two text extractors; the job fails if anything remains extractable. Still inspect the result visually; this is not a forensic guarantee.",
    validate: (c) => (!str(c.params.termsText).trim() && !(c.params.areas as Area[]).length ? "Enter search terms or mark at least one area." : null),
    Options: (c) => (
      <>
        <Field id="terms" label="Search terms to redact (one per line)" hint="Terms are sent only to the local backend, kept in memory, and never stored or logged.">
          <textarea id="terms" value={str(c.params.termsText)} onChange={(e) => c.set({ termsText: e.target.value })} />
        </Field>
        <label className="check">
          <input type="checkbox" checked={!!c.params.match_case} onChange={(e) => c.set({ match_case: e.target.checked })} /> Match case
        </label>
        <fieldset>
          <legend>Also sanitise</legend>
          {Object.entries(c.params.sanitize as Record<string, boolean>).map(([k, v]) => (
            <label key={k} className="check">
              <input type="checkbox" checked={v} onChange={(e) => c.set({ sanitize: { ...(c.params.sanitize as object), [k]: e.target.checked } })} />
              {{ metadata: "Document metadata (title, author, XMP)", attachments: "Embedded file attachments", javascript: "JavaScript and automatic actions", structure_tree: "Accessibility tags (may contain copies of text)", bookmarks: "Search terms in bookmark titles", all_annotations: "All annotations on redacted pages" }[k] ?? k}
            </label>
          ))}
        </fieldset>
        <h3>Mark areas</h3>
        {c.version ? (
          <RedactEditor versionId={c.version.id} pageCount={c.version.page_count ?? 1} pageSizes={c.version.metadata?.page_sizes ?? null} areas={c.params.areas as Area[]} onChange={(a) => c.set({ areas: a })} />
        ) : (
          <p className="muted">Choose a PDF to mark areas.</p>
        )}
      </>
    ),
    build: (c) => ({
      terms: str(c.params.termsText).split("\n").map((t) => t.trim()).filter(Boolean),
      match_case: !!c.params.match_case,
      areas: c.params.areas,
      sanitize: c.params.sanitize,
    }),
  },
  {
    id: "metadata", kind: "metadata", title: "Edit metadata", description: "Title, author, subject, keywords and language.", exts: ["pdf"],
    defaults: {},
    Options: (c) => {
      const md = c.version?.metadata;
      const val = (k: string) => (k in c.params ? str(c.params[k]) : str((md as unknown as Record<string, unknown> | undefined)?.[k]));
      return (
        <div className="form-grid">
          {["title", "author", "subject", "keywords", "language"].map((k) => (
            <Field key={k} id={`md-${k}`} label={k[0]!.toUpperCase() + k.slice(1)} hint={k === "language" ? "BCP 47 tag, e.g. es, en-US" : undefined}>
              <input id={`md-${k}`} type="text" value={val(k)} onChange={(e) => c.set({ [k]: e.target.value })} />
            </Field>
          ))}
        </div>
      );
    },
    validate: (c) => (Object.keys(c.params).length === 0 ? "Change at least one field." : null),
    build: (c) => c.params,
  },
  {
    id: "bookmarks", kind: "bookmarks", title: "Bookmarks", description: "Add, edit, nest and remove bookmarks.", exts: ["pdf"],
    defaults: {},
    Options: BookmarksOptions,
    build: (c) => ({ items: nest((c.params.flat as FlatBookmark[]) ?? []) }),
  },
  {
    id: "protect", kind: "protect", title: "Protect with password", description: "AES-256 encryption with optional usage restrictions.", exts: ["pdf"],
    defaults: { user_password: "", owner_password: "", permissions: { print: true, extract: false, modify: false, annotate: false, forms: false } },
    note: "Passwords are used in memory only and never stored. If you forget them the file cannot be opened.",
    validate: (c) => (!c.params.user_password && !c.params.owner_password ? "Enter at least one password." : null),
    Options: (c) => (
      <>
        <div className="form-grid">
          <Field id="upw" label="Open password (user)">
            <input id="upw" type="password" autoComplete="new-password" value={str(c.params.user_password)} onChange={(e) => c.set({ user_password: e.target.value })} />
          </Field>
          <Field id="opw" label="Permissions password (owner)">
            <input id="opw" type="password" autoComplete="new-password" value={str(c.params.owner_password)} onChange={(e) => c.set({ owner_password: e.target.value })} />
          </Field>
        </div>
        <fieldset>
          <legend>Allow</legend>
          {Object.entries(c.params.permissions as Record<string, boolean>).map(([k, v]) => (
            <label key={k} className="check">
              <input type="checkbox" checked={v} onChange={(e) => c.set({ permissions: { ...(c.params.permissions as object), [k]: e.target.checked } })} /> {k}
            </label>
          ))}
        </fieldset>
      </>
    ),
    build: (c) => c.params,
  },
  {
    id: "unlock", kind: "unlock", title: "Unlock PDF", description: "Remove encryption using the password you are authorised to use.", exts: ["pdf"],
    defaults: { password: "" },
    note: "Restrictions are only removed with the owner password. Unknown passwords are never guessed or bypassed.",
    validate: (c) => (!c.params.password ? "Enter the password." : null),
    Options: (c) => (
      <Field id="pw" label="Password">
        <input id="pw" type="password" autoComplete="off" value={str(c.params.password)} onChange={(e) => c.set({ password: e.target.value })} />
      </Field>
    ),
    build: (c) => ({ password: c.params.password }),
  },
  {
    id: "ocr", kind: "ocr_pdf", title: "Searchable PDF (OCR)", description: "Add an invisible text layer to scanned pages; pages with good text are skipped.", exts: ["pdf"],
    defaults: { languages: "spa+eng", force: false },
    Options: (c) => (
      <>
        <Field id="langs" label="OCR languages" hint="Tesseract codes joined by +, e.g. spa+eng">
          <input id="langs" type="text" value={str(c.params.languages)} onChange={(e) => c.set({ languages: e.target.value })} />
        </Field>
        <label className="check">
          <input type="checkbox" checked={!!c.params.force} onChange={(e) => c.set({ force: e.target.checked })} /> Re-OCR pages that already have an OCR layer
        </label>
      </>
    ),
    build: (c) => c.params,
  },
  {
    id: "extract-images", kind: "extract_images", title: "Extract images", description: "Export embedded images in their original encoding (ZIP).", exts: ["pdf"],
    defaults: { pages: "" },
    validate: (c) => rangeError(c, "pages"),
    Options: (c) => <RangeInput ctx={c} name="pages" label="Pages" />,
    build: (c) => (c.params.pages ? { pages: c.params.pages } : {}),
  },
  {
    id: "pdf-to-images", kind: "pdf_to_images", title: "PDF to images", description: "Render pages to PNG or JPEG (ZIP).", exts: ["pdf"],
    defaults: { dpi: 150, format: "png", pages: "" },
    validate: (c) => rangeError(c, "pages"),
    Options: (c) => (
      <>
        <div className="form-grid">
          <Field id="dpi" label="Resolution (DPI)">
            <input id="dpi" type="number" min={36} max={600} value={str(c.params.dpi)} onChange={(e) => c.set({ dpi: Number(e.target.value) })} />
          </Field>
          <Field id="fmt" label="Format">
            <select id="fmt" value={str(c.params.format)} onChange={(e) => c.set({ format: e.target.value })}>
              <option value="png">PNG (lossless)</option>
              <option value="jpg">JPEG</option>
            </select>
          </Field>
        </div>
        <RangeInput ctx={c} name="pages" label="Pages" />
      </>
    ),
    build: (c) => ({ dpi: c.params.dpi, format: c.params.format, ...(c.params.pages ? { pages: c.params.pages } : {}) }),
  },
  {
    id: "images-to-pdf", kind: "images_to_pdf", title: "Images to PDF", description: "Combine images (in selection order) into one PDF.", exts: ["png", "jpg", "tiff", "webp", "bmp", "gif"], multiple: true,
    defaults: { page_size: "image", title: "" },
    note: "JPEG and PNG images are embedded without recompression when possible.",
    validate: (c) => (c.selected.length < 1 ? "Select at least one image." : null),
    Options: (c) => (
      <Field id="psize" label="Page size">
        <select id="psize" value={str(c.params.page_size)} onChange={(e) => c.set({ page_size: e.target.value })}>
          <option value="image">Same as image</option>
          <option value="a4">A4</option>
          <option value="letter">Letter</option>
        </select>
      </Field>
    ),
    build: (c) => ({ page_size: c.params.page_size, ...(c.params.title ? { title: c.params.title } : {}) }),
  },
];

export const findTool = (id: string) => TOOLS.find((t) => t.id === id);
