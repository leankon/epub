"""Job handlers: one function per operation. Each writes outputs in the job workdir and declares
them with `ctx.add_output`; the job manager validates and registers them atomically."""

from __future__ import annotations

import json
import re
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.core.errors import AppError, NotFound
from app.db.models import Document, DocumentVersion, OcrPage, OcrSession
from app.db.session import session_scope
from app.services import audit, pdf_ops, redact, storage
from app.services.jobs import JobContext, PendingOutput, handler
from app.services.render import page_count

PDF = "application/pdf"
ZIP = "application/zip"
EPUB = "application/epub+zip"
IMAGE_EXTS = ("png", "jpg", "tiff", "webp", "bmp", "gif")
OFFICE_EXTS = ("docx", "odt", "doc", "rtf", "xlsx", "ods", "xls", "pptx", "odp", "ppt")


def _pages(path: Path) -> int:
    return page_count(path)


def _pdf_out(ctx: JobContext, path: Path, operation: str, label: str, **kw: Any) -> None:
    ctx.add_output(PendingOutput(path=path, operation=operation, label=label, mime=PDF, ext="pdf",
                                 page_count=_pages(path), **kw))


@handler("merge", min_inputs=2, max_inputs=None, description="Merge PDFs in the given order")
def h_merge(ctx: JobContext) -> None:
    ctx.stage("merging", 0, len(ctx.inputs))
    out = ctx.tmp("merged.pdf")
    warnings = pdf_ops.merge([(i.path, i.title) for i in ctx.inputs], out)
    for w in warnings:
        ctx.warn(w)
    title = ctx.params.get("title") or f"Merged: {', '.join(i.title for i in ctx.inputs[:3])}"[:200]
    _pdf_out(ctx, out, "merge", "Merged", new_document={"title": title, "kind": "pdf", "filename": "merged.pdf"})
    ctx.result["merged_from"] = [str(i.version_id) for i in ctx.inputs]


@handler("split", description="Split by ranges, extract pages, every page, or by bookmarks")
def h_split(ctx: JobContext) -> None:
    mode = ctx.params.get("mode", "ranges")
    n = ctx.input.page_count or _pages(ctx.input.path)
    if mode == "ranges":
        groups = [(f"pages {g[0] + 1}-{g[-1] + 1}", g) for g in
                  pdf_ops.parse_page_ranges(str(ctx.params.get("ranges", "")), n)]
    elif mode == "extract":
        idx = pdf_ops.flatten_ranges(str(ctx.params.get("ranges", "")), n)
        groups = [(f"extracted {ctx.params.get('ranges')}", idx)]
    elif mode == "every":
        groups = [(f"page {i + 1}", [i]) for i in range(n)]
    elif mode == "bookmarks":
        groups = pdf_ops.bookmark_groups(ctx.input.path)
    else:
        raise AppError("Unknown split mode.", code="invalid_mode")
    if len(groups) > 2000:
        raise AppError("Too many output parts (max 2000).", code="too_many_parts")
    for k, (label, idx) in enumerate(groups):
        ctx.stage("splitting", k, len(groups))
        out = ctx.tmp(f"part-{k + 1:04d}.pdf")
        pdf_ops.extract_pages(ctx.input.path, idx, out)
        ctx.add_output(PendingOutput(path=out, operation="split", label=f"Split: {label}"[:200], mime=PDF,
                                     ext="pdf", page_count=len(idx), key=f"part{k + 1}"))
    ctx.result["parts"] = len(groups)


@handler("organize", description="Reorder, delete, duplicate and rotate pages")
def h_organize(ctx: JobContext) -> None:
    ctx.stage("reorganising pages")
    out = ctx.tmp("organized.pdf")
    pdf_ops.organize(ctx.input.path, list(ctx.params.get("order") or []), out)
    _pdf_out(ctx, out, "organize", "Reordered/rotated")


@handler("rotate", description="Rotate selected pages")
def h_rotate(ctx: JobContext) -> None:
    n = ctx.input.page_count or _pages(ctx.input.path)
    angle = int(ctx.params.get("angle", 90))
    if angle not in (90, 180, 270):
        raise AppError("Angle must be 90, 180 or 270.", code="invalid_rotation")
    sel = set(pdf_ops.flatten_ranges(ctx.params.get("pages"), n))
    out = ctx.tmp("rotated.pdf")
    ctx.stage("rotating")
    pdf_ops.organize(ctx.input.path, [{"page": i, "rotate": angle if i in sel else 0} for i in range(n)], out)
    _pdf_out(ctx, out, "rotate", f"Rotated {angle}°")


@handler("compress", description="Compress with quality presets")
def h_compress(ctx: JobContext) -> None:
    ctx.stage("compressing")
    out = ctx.tmp("compressed.pdf")
    stats = pdf_ops.compress(ctx.input.path, out, str(ctx.params.get("preset", "balanced")))
    ctx.result["compression"] = stats
    if stats["output_bytes"] >= stats["input_bytes"]:
        ctx.warn("The compressed file is not smaller than the input; the original may already be optimised.")
    if stats["lossy"]:
        ctx.warn("Lossy compression: images were recompressed and/or downsampled. Vector content and text are "
                 "kept as is.")
    _pdf_out(ctx, out, "compress", f"Compressed ({stats['preset']}, -{stats['reduction_percent']}%)")


def _image_path(ctx: JobContext, opts: dict[str, Any]) -> dict[str, Any]:
    if opts.get("type") == "image":
        vid = opts.get("image_version_id")
        if not vid:
            raise AppError("Choose an image for the watermark.", code="missing_image")
        with session_scope() as s:
            v = storage.get_version(s, uuid.UUID(str(vid)))
            if v.file_ext not in IMAGE_EXTS:
                raise AppError("The watermark file must be an image.", code="invalid_image")
            opts = {**opts, "image_path": storage.version_path(v)}
    return opts


@handler("watermark", description="Text or image watermark")
def h_watermark(ctx: JobContext) -> None:
    ctx.stage("watermarking")
    out = ctx.tmp("watermarked.pdf")
    pdf_ops.watermark(ctx.input.path, out, _image_path(ctx, dict(ctx.params)))
    _pdf_out(ctx, out, "watermark", "Watermarked")


@handler("page_numbers", description="Add page numbers")
def h_page_numbers(ctx: JobContext) -> None:
    ctx.stage("numbering pages")
    out = ctx.tmp("numbered.pdf")
    pdf_ops.page_numbers(ctx.input.path, out, dict(ctx.params))
    _pdf_out(ctx, out, "page_numbers", "Page numbers")


@handler("redact", description="Permanent redaction with verification")
def h_redact(ctx: JobContext) -> None:
    ctx.stage("redacting")
    out = ctx.tmp("redacted.pdf")
    res = redact.redact(ctx.input.path, out, areas=ctx.params.get("areas") or [],
                        terms=list(ctx.params.get("terms") or []), match_case=bool(ctx.params.get("match_case")),
                        sanitize=ctx.params.get("sanitize") or {})
    for w in res.pop("warnings"):
        ctx.warn(w)
    ctx.warn("Verify the result visually. Automated checks cover the supported text-extraction paths and are "
             "not a forensic guarantee.")
    ctx.result["redaction"] = res
    _pdf_out(ctx, out, "redact", "Redacted", validation={"passed": True, "redaction_verification": res["verification"]})


@handler("metadata", description="Edit title, author, subject, keywords and language")
def h_metadata(ctx: JobContext) -> None:
    ctx.stage("writing metadata")
    out = ctx.tmp("metadata.pdf")
    fields = {k: ctx.params[k] for k in ("title", "author", "subject", "keywords", "language") if k in ctx.params}
    pdf_ops.set_metadata(ctx.input.path, out, fields)
    _pdf_out(ctx, out, "metadata", "Metadata edited")


@handler("bookmarks", description="Replace the bookmark tree")
def h_bookmarks(ctx: JobContext) -> None:
    ctx.stage("writing bookmarks")
    out = ctx.tmp("bookmarks.pdf")
    pdf_ops.set_outline(ctx.input.path, out, list(ctx.params.get("items") or []))
    _pdf_out(ctx, out, "bookmarks", "Bookmarks edited")


@handler("protect", description="Encrypt with AES-256 passwords")
def h_protect(ctx: JobContext) -> None:
    ctx.stage("encrypting")
    out = ctx.tmp("protected.pdf")
    pdf_ops.protect(ctx.input.path, out, str(ctx.params.get("user_password") or ""),
                    str(ctx.params.get("owner_password") or "") or None, dict(ctx.params.get("permissions") or {}))
    ctx.warn("Keep the password safe: it is not stored anywhere and cannot be recovered.")
    ctx.add_output(PendingOutput(path=out, operation="protect", label="Password protected", mime=PDF, ext="pdf",
                                 page_count=ctx.input.page_count))


@handler("unlock", description="Remove encryption with the authorised password", modifies_pdf=False)
def h_unlock(ctx: JobContext) -> None:
    ctx.stage("decrypting")
    out = ctx.tmp("unlocked.pdf")
    res = pdf_ops.unlock(ctx.input.path, out, str(ctx.params.get("password") or ""))
    ctx.result["unlock"] = res
    ctx.warn("Removing encryption changes the file; any digital signatures are no longer valid for it.")
    _pdf_out(ctx, out, "unlock", "Unlocked")


@handler("extract_images", modifies_pdf=False, description="Extract embedded images (original encoding)")
def h_extract_images(ctx: JobContext) -> None:
    ctx.stage("extracting images")
    out = ctx.tmp("images.zip")
    ctx.result.update(pdf_ops.extract_images(ctx.input.path, out, ctx.params.get("pages")))
    ctx.add_output(PendingOutput(path=out, operation="extract_images", label=f"{ctx.result['images']} images",
                                 mime=ZIP, ext="zip"))


@handler("pdf_to_images", modifies_pdf=False, description="Render pages to PNG/JPEG")
def h_pdf_to_images(ctx: JobContext) -> None:
    out = ctx.tmp("pages.zip")

    def prog(i: int, total: int, k: int) -> None:
        ctx.stage("rendering pages", k, total)

    ctx.result.update(pdf_ops.pdf_to_images(ctx.input.path, out, dpi=int(ctx.params.get("dpi", 150)),
                                            fmt=str(ctx.params.get("format", "png")), pages=ctx.params.get("pages"),
                                            check_cancel=prog))
    ctx.add_output(PendingOutput(path=out, operation="pdf_to_images", label=f"{ctx.result['pages']} page images",
                                 mime=ZIP, ext="zip"))


@handler("images_to_pdf", input_exts=IMAGE_EXTS, max_inputs=None, modifies_pdf=False,
         description="Combine images into a PDF")
def h_images_to_pdf(ctx: JobContext) -> None:
    ctx.stage("building PDF")
    out = ctx.tmp("images.pdf")
    pdf_ops.images_to_pdf([i.path for i in ctx.inputs], out, ctx.workdir,
                          page_size=str(ctx.params.get("page_size", "image")))
    title = ctx.params.get("title") or (ctx.inputs[0].title if len(ctx.inputs) == 1 else "Images")
    _pdf_out(ctx, out, "images_to_pdf", "From images", new_document={"title": title, "kind": "pdf",
                                                                    "filename": "images.pdf"})


@handler("ocr_pdf", description="Make a searchable PDF (invisible OCR text layer)")
def h_ocr_pdf(ctx: JobContext) -> None:
    from app.services.ocr.engine import require_tesseract
    from app.services.ocr.searchable import make_searchable

    require_tesseract()
    out = ctx.tmp("searchable.pdf")
    res = make_searchable(ctx.input.path, out, ctx.workdir, languages=ctx.params.get("languages"),
                          force=bool(ctx.params.get("force")),
                          progress=lambda i, n: ctx.stage("recognising pages", i, n))
    for w in res.pop("warnings"):
        ctx.warn(w)
    ctx.result["ocr"] = res
    if res["pages_ocr"] == 0:
        ctx.warn("No page needed OCR: the document already has a usable text layer.")
    audit.record("ocr.completed", "success", job_id=ctx.job_id, document_id=ctx.input.document_id,
                 operation="ocr_pdf", input_sha256=ctx.input.sha256, details={"pages_ocr": res["pages_ocr"]})
    _pdf_out(ctx, out, "ocr", "Searchable (OCR)")


@handler("ocr_analyze", modifies_pdf=False, description="OCR + layout analysis for review and EPUB")
def h_ocr_analyze(ctx: JobContext) -> None:
    from app.services.ocr.engine import require_tesseract
    from app.services.ocr.pipeline import run_session

    require_tesseract()
    sid = uuid.UUID(str(ctx.params["session_id"]))
    pages = ctx.params.get("pages")
    audit.record("ocr.started", "running", job_id=ctx.job_id, document_id=ctx.input.document_id,
                 operation="ocr_analyze", input_sha256=ctx.input.sha256)
    if pages:
        with session_scope() as s:
            for pi in pages:
                pg = s.get(OcrPage, (sid, int(pi)))
                if pg is not None:
                    pg.reviewed = False
    summary = run_session(ctx, sid, [int(p) for p in pages] if pages else None, bool(ctx.params.get("force")),
                          ctx.params.get("options"))
    ctx.result.update({"session_id": str(sid), "summary": summary})
    if summary["failed"]:
        ctx.warn(f"{len(summary['failed'])} page(s) failed analysis: {summary['failed'][:20]}")


def _language_code(langs: str) -> str:
    first = (langs or "spa").split("+")[0]
    return {"spa": "es", "eng": "en", "fra": "fr", "deu": "de", "ita": "it", "por": "pt", "cat": "ca"}.get(first,
                                                                                                          first[:2])


def pretty_title(name: str) -> str:
    """'911139982-1-Todo-Lo-Que-Nunca-Fuimos' -> 'Todo Lo Que Nunca Fuimos'."""
    t = re.sub(r"^[\d\s_\-.]+", "", name or "")
    t = re.sub(r"[_\-]+", " ", t).strip()
    return t or name or "Untitled"


def _pdf_book_metadata(path: Path) -> dict[str, str]:
    """Title/author from the PDF's own metadata, when present and meaningful."""
    from app.services.inspect import inspect_pdf

    try:
        info = inspect_pdf(path)
    except Exception:
        return {}
    out: dict[str, str] = {}
    title = (info.get("title") or "").strip()
    if title and not re.match(r"(?i)^(untitled|microsoft word|document\d*|\S+\.(docx?|pdf|indd))", title):
        out["title"] = title
    author = (info.get("author") or "").strip()
    if author and author.lower() not in ("unknown", "admin", "user"):
        out["author"] = author
    return out


def outline_chapters(path: Path) -> list[dict[str, Any]] | None:
    """Chapter boundaries from the PDF bookmarks (all levels; entries on the same page are combined)."""
    try:
        nodes = pdf_ops.get_outline(path)
    except Exception:
        return None
    flat: list[tuple[int, str]] = []

    def walk(items: list[dict[str, Any]]) -> None:
        for it in items:
            if it.get("page") is not None and str(it.get("title") or "").strip():
                flat.append((int(it["page"]), re.sub(r"\s+", " ", str(it["title"])).strip()[:200]))
            walk(it.get("children") or [])

    walk(nodes)
    if len(flat) < 3:
        return None
    by_page: dict[int, list[str]] = {}
    for page, title in sorted(flat, key=lambda t: t[0]):
        if title not in by_page.setdefault(page, []):
            by_page[page].append(title)
    return [{"title": " · ".join(titles), "page": page + 1} for page, titles in sorted(by_page.items())]


def load_session_pages(sid: uuid.UUID, pdf_path: Path, sdir: Path) -> list[dict[str, Any]]:
    from app.services.render import render_pil

    with session_scope() as s:
        rows = s.query(OcrPage).filter(OcrPage.session_id == sid).order_by(OcrPage.page_index).all()
        pages = [{"page_index": p.page_index, "status": p.status, "classification": p.classification or {},
                  "regions": p.regions or [], "mean_confidence": p.mean_confidence, "reviewed": p.reviewed,
                  "image_size": p.image_size, "error": p.error,
                  "page_asset": p.processed_image or p.source_image} for p in rows]
    for p in pages:
        if not p["page_asset"]:
            img = render_pil(pdf_path, p["page_index"], dpi=150)
            name = f"pages/p{p['page_index'] + 1:04d}-fallback.jpg"
            (sdir / "pages").mkdir(parents=True, exist_ok=True)
            img.save(sdir / name, format="JPEG", quality=90)
            p["page_asset"], p["image_size"] = name, [img.width, img.height]
    return pages


@handler("pdf_to_epub", modifies_pdf=False, description="Convert PDF (scanned or digital) to EPUB 3")
def h_pdf_to_epub(ctx: JobContext) -> None:
    from app.services.epub.builder import build_epub
    from app.services.epub.validate import validate_epub
    from app.services.ocr.pipeline import run_session, session_dir

    sid = uuid.UUID(str(ctx.params["session_id"]))
    with session_scope() as s:
        sess = s.get(OcrSession, sid)
        if sess is None:
            raise NotFound("OCR session not found.")
        doc = s.get(Document, sess.document_id)
        status, langs = sess.status, sess.languages
        book_meta = dict(sess.book_metadata or {})
        title = doc.title if doc else "Untitled"
        pending = s.query(OcrPage).filter(OcrPage.session_id == sid, OcrPage.status == "done").count() < sess.page_count
    if pending:
        from app.services.ocr.engine import require_tesseract

        require_tesseract()
        audit.record("ocr.started", "running", job_id=ctx.job_id, document_id=ctx.input.document_id,
                     operation="ocr_analyze", input_sha256=ctx.input.sha256)
        run_session(ctx, sid, None, False)
    strategy = str(ctx.params.get("strategy", "hybrid"))
    pdf_meta = _pdf_book_metadata(ctx.input.path)
    meta = {"title": pretty_title(title), "language": _language_code(langs), **pdf_meta, **book_meta,
            **(ctx.params.get("metadata") or {})}
    chapters = ctx.params.get("chapters") or book_meta.get("chapters") or outline_chapters(ctx.input.path)
    if chapters and not (ctx.params.get("chapters") or book_meta.get("chapters")):
        ctx.result["chapters_from"] = "pdf_bookmarks"
    sdir = session_dir(sid)
    ctx.stage("building EPUB")
    pages = load_session_pages(sid, ctx.input.path, sdir)
    out = ctx.tmp("book.epub")
    audit.record("conversion.started", "running", job_id=ctx.job_id, document_id=ctx.input.document_id,
                 operation="pdf_to_epub", input_sha256=ctx.input.sha256, details={"strategy": strategy})
    try:
        built = build_epub(pages=pages, session_dir=sdir, out=out, meta=meta, strategy=strategy, chapters=chapters)
    except ValueError as exc:
        raise AppError(f"EPUB could not be built: {exc}", code="epub_build_failed") from None
    ctx.stage("validating EPUB")
    rep = validate_epub(out, expect_text=sum(len(r.get("text") or "") for p in pages for r in p["regions"]) > 0,
                        expected_images=built.expected_images, expected_order=built.expected_order)
    summary = rep.summary()
    report = {**built.report, "reviewed": status == "approved", "session_id": str(sid), "validation": summary}
    if status != "approved":
        report["warnings"].append("OCR results were not reviewed/approved before export.")
    for w in report["warnings"]:
        ctx.warn(w)
    ctx.result.update({"report": report, "session_id": str(sid)})
    audit.record("epub.validated", "success" if rep.passed else "failure", job_id=ctx.job_id,
                 document_id=ctx.input.document_id, operation="pdf_to_epub",
                 details={"passed": rep.passed, "epubcheck_executed": rep.epubcheck.get("executed", False),
                          "errors": len(rep.errors)})
    if not rep.passed:
        raise AppError("The generated EPUB failed validation; it was not saved. See the validation report.",
                       code="validation_failed")
    ctx.add_output(PendingOutput(path=out, operation="pdf_to_epub", label=f"EPUB ({strategy})", mime=EPUB,
                                 ext="epub", validation=summary))


@handler("epub_validate", input_exts=("epub",), modifies_pdf=False, description="Validate an EPUB")
def h_epub_validate(ctx: JobContext) -> None:
    from app.services.epub.validate import validate_epub

    ctx.stage("validating EPUB")
    rep = validate_epub(ctx.input.path, expect_text=True)
    ctx.result["validation"] = rep.summary()
    audit.record("epub.validated", "success" if rep.passed else "failure", job_id=ctx.job_id,
                 document_id=ctx.input.document_id, version_id=ctx.input.version_id, operation="epub_validate",
                 input_sha256=ctx.input.sha256, details={"passed": rep.passed,
                                                         "epubcheck_executed": rep.epubcheck.get("executed", False)})
    with session_scope() as s:
        v = s.get(DocumentVersion, ctx.input.version_id)
        if v is not None and v.validation is None:
            v.validation = rep.summary()


@handler("epub_to_pdf", input_exts=("epub",), modifies_pdf=False, description="Convert EPUB to PDF")
def h_epub_to_pdf(ctx: JobContext) -> None:
    from app.services.epub.to_pdf import epub_to_pdf

    ctx.stage("rendering EPUB")
    out = ctx.tmp("book.pdf")
    res = epub_to_pdf(ctx.input.path, out, ctx.workdir, page_size=str(ctx.params.get("page_size", "a5")),
                      font_size_pt=float(ctx.params.get("font_size", 11)))
    for w in res.pop("warnings"):
        ctx.warn(w)
    ctx.result["conversion"] = res
    _pdf_out(ctx, out, "epub_to_pdf", "PDF from EPUB")


@handler("office_to_pdf", input_exts=OFFICE_EXTS, modifies_pdf=False, description="Convert Office documents")
def h_office_to_pdf(ctx: JobContext) -> None:
    from app.services.office import office_to_pdf

    ctx.stage("converting with LibreOffice")
    out = ctx.tmp("office.pdf")
    res = office_to_pdf(ctx.input.path, ctx.input.ext, out, ctx.workdir)
    for w in res["warnings"]:
        ctx.warn(w)
    audit.record("conversion.completed", "success", job_id=ctx.job_id, document_id=ctx.input.document_id,
                 operation="office_to_pdf", input_sha256=ctx.input.sha256)
    _pdf_out(ctx, out, "office_to_pdf", f"PDF from .{ctx.input.ext}")


@handler("backup", input_exts=(), min_inputs=0, max_inputs=0, modifies_pdf=False,
         description="Back up database metadata and document files")
def h_backup(ctx: JobContext) -> None:
    from app.services.backup import create_backup

    res = create_backup(progress=lambda name, i, n: ctx.stage(name, i, n))
    ctx.result["backup"] = res
    if not res.get("verified"):
        raise AppError("Backup verification failed.", code="backup_unverified")


def export_document_zip(document_id: uuid.UUID, out: Path) -> None:
    """All retained versions of a document plus a JSON metadata file."""
    import zipfile

    with session_scope() as s:
        doc = s.get(Document, document_id)
        if doc is None:
            raise NotFound("Document not found.")
        meta = {"document": {"id": str(doc.id), "title": doc.title, "original_filename": doc.original_filename,
                             "kind": doc.kind, "created_at": doc.created_at.isoformat()},
                "versions": [], "exported_at": datetime.now(UTC).isoformat()}
        files = []
        for v in doc.versions:
            if v.expired_at is not None:
                continue
            p = storage.version_path(v)
            name = f"v{v.version_number:03d}-{v.operation}.{v.file_ext}"
            files.append((p, name))
            meta["versions"].append({"id": str(v.id), "number": v.version_number, "original": v.is_original,
                                     "operation": v.operation, "label": v.label, "sha256": v.sha256,
                                     "size_bytes": v.size_bytes, "parent_version_id": str(v.parent_version_id)
                                     if v.parent_version_id else None, "file": name,
                                     "created_at": v.created_at.isoformat()})
    with zipfile.ZipFile(out, "w", zipfile.ZIP_STORED) as z:
        for p, name in files:
            z.write(p, name)
        z.writestr("metadata.json", json.dumps(meta, indent=2, ensure_ascii=False))
