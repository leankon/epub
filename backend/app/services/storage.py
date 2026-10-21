"""Document storage: immutable originals, versioned outputs, deletion and orphan recovery.

Layout (all names generated, never derived from user input):
    data/documents/<document-id>/original.<ext>
    data/documents/<document-id>/versions/v002-<operation>-<short-id>.<ext>
"""

from __future__ import annotations

import logging
import re
import shutil
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from PIL import Image
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.errors import AppError, NotFound, TooLarge, Unsupported
from app.core.files import Sniffed, atomic_move, ensure_within, make_readonly, safe_display_name, sha256_file, sniff
from app.db.models import Document, DocumentMetadata, DocumentVersion, Job, OcrSession
from app.db.session import session_scope
from app.services import audit
from app.services.inspect import inspect_pdf

log = logging.getLogger("pdfws.storage")

Image.MAX_IMAGE_PIXELS = 300_000_000  # decompression-bomb guard for very large scans


def doc_dir(document_id: uuid.UUID) -> Path:
    s = get_settings()
    return ensure_within(s.documents_dir, s.documents_dir / str(document_id))


def version_path(v: DocumentVersion) -> Path:
    s = get_settings()
    return ensure_within(s.documents_dir, s.documents_dir / v.storage_path)


def _slug(op: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", op.lower()).strip("-")[:30] or "output"


def get_version(session: Session, version_id: uuid.UUID) -> DocumentVersion:
    v = session.get(DocumentVersion, version_id)
    if v is None or v.expired_at is not None:
        raise NotFound("Version not found (it may have expired or been deleted).")
    return v


def latest_version(session: Session, document_id: uuid.UUID, *, kind_ext: str | None = None) -> DocumentVersion:
    q = select(DocumentVersion).where(DocumentVersion.document_id == document_id,
                                      DocumentVersion.expired_at.is_(None))
    if kind_ext:
        q = q.where(DocumentVersion.file_ext == kind_ext)
    v = session.execute(q.order_by(DocumentVersion.version_number.desc()).limit(1)).scalar_one_or_none()
    if v is None:
        raise NotFound("No available version for this document.")
    return v


def _validate_content(path: Path, sn: Sniffed) -> dict[str, Any]:
    """Type-specific validation of an untrusted upload. Returns inspection details."""
    s = get_settings()
    if sn.kind == "pdf":
        info = inspect_pdf(path)
        if info.get("page_count") and info["page_count"] > s.max_pages:
            raise TooLarge(f"PDF has {info['page_count']} pages; the limit is {s.max_pages} (PDFWS_MAX_PAGES).")
        return info
    if sn.kind == "image":
        try:
            with Image.open(path) as im:
                im.verify()
            with Image.open(path) as im:
                return {"width": im.width, "height": im.height, "mode": im.mode,
                        "frames": getattr(im, "n_frames", 1), "warnings": []}
        except (OSError, Image.DecompressionBombError) as exc:
            raise Unsupported("The image is damaged or too large to process safely.",
                              details={"reason": type(exc).__name__}) from None
    if sn.kind == "epub":
        from app.services.epub.validate import validate_epub

        report = validate_epub(path, run_epubcheck=False)
        return {"epub_validation": report.summary(), "warnings": report.warnings[:20]}
    return {"warnings": ["Office documents are never opened with macros enabled."]}


def _write_metadata(session: Session, version: DocumentVersion, info: dict[str, Any]) -> None:
    md = DocumentMetadata(
        version_id=version.id,
        title=info.get("title"), author=info.get("author"), subject=info.get("subject"),
        keywords=info.get("keywords"), language=info.get("language"),
        encrypted=bool(info.get("encrypted")), has_signatures=bool(info.get("has_signatures")),
        has_forms=bool(info.get("has_forms")), has_annotations=bool(info.get("annotation_count")),
        has_attachments=bool(info.get("attachment_count")), has_javascript=bool(info.get("has_javascript")),
        page_sizes=info.get("page_sizes"),
        details={k: v for k, v in info.items() if k not in ("page_sizes",)},
    )
    session.merge(md)


def import_file(tmp_path: Path, claimed_name: str, *, title: str | None = None) -> uuid.UUID:
    """Validate an uploaded temp file and register it as a new document with an immutable original."""
    s = get_settings()
    size = tmp_path.stat().st_size
    if size == 0:
        raise Unsupported("The file is empty.")
    if size > s.max_upload_bytes:
        raise TooLarge(f"File exceeds the {s.max_upload_mb} MB limit.")
    sn = sniff(tmp_path, claimed_name)
    info = _validate_content(tmp_path, sn)
    digest = sha256_file(tmp_path)
    display = safe_display_name(claimed_name, f"document.{sn.ext}")
    doc_id = uuid.uuid4()
    ddir = doc_dir(doc_id)
    dest = ddir / f"original.{sn.ext}"
    (ddir / "versions").mkdir(parents=True, exist_ok=True)
    ddir.chmod(0o700)
    atomic_move(tmp_path, dest)
    make_readonly(dest)
    try:
        with session_scope() as session:
            doc = Document(id=doc_id, title=(title or Path(display).stem)[:300], original_filename=display,
                           kind=sn.kind, tags=[])
            session.add(doc)
            session.flush()
            v = DocumentVersion(document_id=doc_id, version_number=1, is_original=True, operation="import",
                                label="Original", storage_path=f"{doc_id}/original.{sn.ext}", mime_type=sn.mime,
                                file_ext=sn.ext, size_bytes=size, sha256=digest, page_count=info.get("page_count"))
            session.add(v)
            session.flush()
            _write_metadata(session, v, info)
            audit.record("document.imported", "success", session=session, document_id=doc_id, version_id=v.id,
                         operation="import", output_sha256=digest,
                         details={"kind": sn.kind, "size_bytes": size, "page_count": info.get("page_count"),
                                  "encrypted": bool(info.get("encrypted"))})
    except Exception:
        shutil.rmtree(ddir, ignore_errors=True)
        raise
    return doc_id


def next_version_number(session: Session, document_id: uuid.UUID) -> int:
    session.execute(select(Document.id).where(Document.id == document_id).with_for_update())
    cur = session.execute(select(func.max(DocumentVersion.version_number))
                          .where(DocumentVersion.document_id == document_id)).scalar()
    return int(cur or 0) + 1


def place_output(session: Session, *, document_id: uuid.UUID, src: Path, operation: str, label: str,
                 mime: str, ext: str, parent_version_id: uuid.UUID | None, job_id: uuid.UUID | None,
                 page_count: int | None = None, validation: dict[str, Any] | None = None,
                 placed: list[Path] | None = None) -> DocumentVersion:
    """Move a finished output into the document's versions directory and create its row.

    Runs inside the caller's transaction. Files placed are appended to `placed` so the caller
    can remove them if the transaction later fails.
    """
    num = next_version_number(session, document_id)
    vid = uuid.uuid4()
    name = f"v{num:03d}-{_slug(operation)}-{vid.hex[:8]}.{ext}"
    rel = f"{document_id}/versions/{name}"
    dest = ensure_within(get_settings().documents_dir, get_settings().documents_dir / rel)
    digest = sha256_file(src)
    size = src.stat().st_size
    atomic_move(src, dest)
    make_readonly(dest)
    if placed is not None:
        placed.append(dest)
    v = DocumentVersion(id=vid, document_id=document_id, version_number=num, is_original=False,
                        parent_version_id=parent_version_id, produced_by_job_id=job_id, operation=operation,
                        label=label[:200], storage_path=rel, mime_type=mime, file_ext=ext, size_bytes=size,
                        sha256=digest, page_count=page_count, validation=validation)
    session.add(v)
    session.flush()
    if ext == "pdf":
        try:
            _write_metadata(session, v, inspect_pdf(dest))
        except AppError:
            pass
    return v


def new_document(session: Session, *, title: str, kind: str, filename: str) -> uuid.UUID:
    doc_id = uuid.uuid4()
    (doc_dir(doc_id) / "versions").mkdir(parents=True, exist_ok=True)
    session.add(Document(id=doc_id, title=title[:300], original_filename=safe_display_name(filename),
                         kind=kind, tags=[]))
    session.flush()
    return doc_id


def delete_document(document_id: uuid.UUID) -> dict[str, Any]:
    """Delete a document's files and database rows. The audit log keeps hashes (no content)."""
    s = get_settings()
    with session_scope() as session:
        doc = session.get(Document, document_id)
        if doc is None:
            raise NotFound("Document not found.")
        running = session.execute(select(func.count()).select_from(Job).where(
            Job.document_id == document_id, Job.status.in_(["queued", "running"]))).scalar()
        if running:
            raise AppError("Cancel or finish this document's running jobs before deleting it.", code="busy",
                           status_code=409)
        hashes = [v.sha256 for v in doc.versions]
        sessions = session.execute(select(OcrSession.id).where(OcrSession.document_id == document_id)).scalars().all()
        audit.record("document.deleted", "success", session=session, document_id=document_id, operation="delete",
                     input_sha256=hashes, details={"versions": len(hashes)})
        session.delete(doc)
    removed = 0
    targets = [doc_dir(document_id)] + [ensure_within(s.ocr_dir, s.ocr_dir / str(sid)) for sid in sessions]
    for v_cache in (s.cache_dir / "render").glob(f"{document_id}*"):
        targets.append(ensure_within(s.cache_dir, v_cache))
    for t in targets:
        if t.exists():
            for p in t.rglob("*"):
                if p.is_file():
                    p.chmod(0o600)
                    removed += 1
            shutil.rmtree(t, ignore_errors=True)
    return {"files_removed": removed, "versions_removed": len(hashes)}


def recover_orphans() -> dict[str, int]:
    """Startup recovery: quarantine stored files with no DB row; flag DB rows with missing files."""
    s = get_settings()
    with session_scope() as session:
        known = {p for (p,) in session.execute(select(DocumentVersion.storage_path))}
        doc_ids = {str(d) for (d,) in session.execute(select(Document.id))}
    moved = missing = 0
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
    for ddir in s.documents_dir.iterdir() if s.documents_dir.exists() else []:
        if not ddir.is_dir():
            continue
        for f in ddir.rglob("*"):
            if not f.is_file():
                continue
            rel = f.relative_to(s.documents_dir).as_posix()
            if rel not in known:
                q = s.quarantine_dir / "orphans" / stamp / rel
                q.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(f), str(q))
                moved += 1
        if ddir.name not in doc_ids and not any(ddir.rglob("*.*")):
            shutil.rmtree(ddir, ignore_errors=True)
    for rel in known:
        if not (s.documents_dir / rel).exists():
            missing += 1
            log.error("Registered version file is missing: %s", rel)
    if moved or missing:
        audit.record("storage.recovery", "warning", operation="recover_orphans",
                     details={"orphans_quarantined": moved, "missing_files": missing})
    return {"orphans_quarantined": moved, "missing_files": missing}


def verify_hashes(session: Session) -> dict[str, Any]:
    """Re-hash every stored version and compare against the database."""
    bad: list[dict[str, str]] = []
    checked = 0
    for v in session.execute(select(DocumentVersion).where(DocumentVersion.expired_at.is_(None))).scalars():
        p = version_path(v)
        checked += 1
        if not p.exists():
            bad.append({"version_id": str(v.id), "problem": "missing"})
        elif sha256_file(p) != v.sha256:
            bad.append({"version_id": str(v.id), "problem": "hash_mismatch"})
    return {"checked": checked, "problems": bad, "ok": not bad}


def storage_usage() -> dict[str, int]:
    s = get_settings()
    out: dict[str, int] = {}
    for name, d in (("documents", s.documents_dir), ("ocr_workspace", s.ocr_dir), ("cache", s.cache_dir),
                    ("temporary", s.tmp_dir), ("backups", s.backups_dir), ("exports", s.exports_dir),
                    ("quarantine", s.quarantine_dir)):
        out[name] = sum(p.stat().st_size for p in d.rglob("*") if p.is_file()) if d.exists() else 0
    return out
