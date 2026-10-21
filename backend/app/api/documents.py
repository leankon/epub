"""Import, listing, metadata, download, previews, comparison, export and deletion."""

from __future__ import annotations

import asyncio
import io
import uuid
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Query, Request
from fastapi.responses import Response, StreamingResponse
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.api import serialize
from app.api.schemas import DocumentPatch, InspectRequest, PreviewRequest
from app.core.config import get_settings
from app.core.errors import AppError, Busy, NotFound, TooLarge
from app.core.files import safe_display_name
from app.db.models import AuditEvent, Document, DocumentMetadata, DocumentVersion, Job
from app.db.session import DBDep, session_scope
from app.services import audit, pdf_ops, storage
from app.services.inspect import inspect_pdf
from app.services.render import cached_thumbnail, render_pil

router = APIRouter(prefix="/api", tags=["documents"])
_upload_slots = asyncio.Semaphore(4)


@router.post("/documents", status_code=201)
async def upload(request: Request, filename: str = Query(..., max_length=300),
                 title: str | None = Query(None, max_length=300)) -> dict[str, Any]:
    """Stream the raw request body to a private temp file (size-limited), then validate and import."""
    s = get_settings()
    if _upload_slots.locked():
        raise Busy("Too many simultaneous uploads; try again shortly.")
    async with _upload_slots:
        up_dir = s.tmp_dir / "uploads"
        up_dir.mkdir(parents=True, exist_ok=True)
        tmp = up_dir / f"{uuid.uuid4().hex}.upload"
        size = 0
        try:
            with tmp.open("wb") as f:
                async for chunk in request.stream():
                    size += len(chunk)
                    if size > s.max_upload_bytes:
                        raise TooLarge(f"File exceeds the {s.max_upload_mb} MB limit (PDFWS_MAX_UPLOAD_MB).")
                    f.write(chunk)
            doc_id = await asyncio.to_thread(storage.import_file, tmp, safe_display_name(filename), title=title)
        except Exception:
            audit.record("document.import_failed", "failure", operation="import",
                         details={"size_bytes": size})
            raise
        finally:
            tmp.unlink(missing_ok=True)
    with session_scope() as db:
        return _doc_detail(db, doc_id)


def _doc_detail(db: Session, doc_id: uuid.UUID) -> dict[str, Any]:
    d = db.get(Document, doc_id)
    if d is None:
        raise NotFound("Document not found.")
    versions = [serialize.version(v, db.get(DocumentMetadata, v.id)) for v in d.versions]
    latest = next((v for v in reversed(d.versions) if v.expired_at is None), None)
    out = serialize.document(d, latest, db.get(DocumentMetadata, latest.id) if latest else None, len(versions))
    out["versions"] = versions
    jobs = db.execute(select(Job).where(Job.document_id == doc_id).order_by(Job.created_at.desc()).limit(50)).scalars()
    out["jobs"] = [serialize.job(j) for j in jobs]
    return out


@router.get("/documents")
def list_documents(q: str | None = Query(None, max_length=200), kind: str | None = None,
                   limit: int = Query(100, ge=1, le=500), offset: int = Query(0, ge=0),
                   db: Session = DBDep) -> dict[str, Any]:
    query = select(Document)
    if q:
        like = f"%{q.replace('%', '').replace('_', '')}%"
        query = query.where(or_(Document.title.ilike(like), Document.original_filename.ilike(like)))
    if kind:
        query = query.where(Document.kind == kind)
    total = db.execute(select(func.count()).select_from(query.subquery())).scalar()
    docs = db.execute(query.order_by(Document.created_at.desc()).limit(limit).offset(offset)).scalars().all()
    items = []
    for d in docs:
        live = [v for v in d.versions if v.expired_at is None]
        latest = live[-1] if live else None
        items.append(serialize.document(d, latest, db.get(DocumentMetadata, latest.id) if latest else None,
                                        len(d.versions)))
    return {"items": items, "total": total}


@router.get("/documents/{doc_id}")
def get_document(doc_id: uuid.UUID, db: Session = DBDep) -> dict[str, Any]:
    return _doc_detail(db, doc_id)


@router.patch("/documents/{doc_id}")
def patch_document(doc_id: uuid.UUID, body: DocumentPatch, db: Session = DBDep) -> dict[str, Any]:
    d = db.get(Document, doc_id)
    if d is None:
        raise NotFound("Document not found.")
    if body.title is not None:
        d.title = body.title
    if body.tags is not None:
        d.tags = [t[:40] for t in body.tags]
    db.flush()
    return _doc_detail(db, doc_id)


@router.delete("/documents/{doc_id}")
def delete_document(doc_id: uuid.UUID, confirm: bool = False) -> dict[str, Any]:
    if not confirm:
        raise AppError("Deletion must be confirmed (confirm=true).", code="confirmation_required")
    return storage.delete_document(doc_id)


@router.get("/documents/{doc_id}/history")
def document_history(doc_id: uuid.UUID, db: Session = DBDep) -> dict[str, Any]:
    if db.get(Document, doc_id) is None:
        raise NotFound("Document not found.")
    events = db.execute(select(AuditEvent).where(AuditEvent.document_id == doc_id)
                        .order_by(AuditEvent.seq)).scalars()
    return {"events": [{"seq": e.seq, "ts": e.ts.isoformat(), "event_type": e.event_type, "status": e.status,
                        "operation": e.operation, "job_id": str(e.job_id) if e.job_id else None,
                        "version_id": str(e.version_id) if e.version_id else None,
                        "input_sha256": e.input_sha256, "output_sha256": e.output_sha256,
                        "delivery_outcome": e.delivery_outcome, "details": e.details} for e in events]}


def _stream_file(path: Path, *, version: DocumentVersion, outcome_event: str, chunk: int = 1024 * 1024
                 ) -> Iterator[bytes]:
    sent = 0
    try:
        with path.open("rb") as f:
            while block := f.read(chunk):
                sent += len(block)
                yield block
        audit.record(outcome_event, "success", document_id=version.document_id, version_id=version.id,
                     operation="download", output_sha256=version.sha256, delivery_outcome="delivered",
                     details={"bytes": sent})
    except GeneratorExit:
        audit.record(outcome_event, "failure", document_id=version.document_id, version_id=version.id,
                     operation="download", output_sha256=version.sha256, delivery_outcome="aborted",
                     details={"bytes": sent})
        raise


@router.get("/versions/{vid}/download")
def download(vid: uuid.UUID, db: Session = DBDep) -> StreamingResponse:
    v = storage.get_version(db, vid)
    if v.validation is not None and v.validation.get("passed") is False:
        raise AppError("This output did not pass validation and cannot be downloaded.", code="validation_failed")
    d = db.get(Document, v.document_id)
    stem = Path(safe_display_name(d.title if d else "document")).stem[:120] or "document"
    name = f"{stem}-v{v.version_number:03d}.{v.file_ext}"
    path = storage.version_path(v)
    ascii_name = name.encode("ascii", "replace").decode().replace("?", "_").replace('"', "_")
    from urllib.parse import quote

    headers = {"Content-Disposition": f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(name)}",
               "Content-Length": str(path.stat().st_size), "X-Content-SHA256": v.sha256}
    return StreamingResponse(_stream_file(path, version=v, outcome_event="output.downloaded"),
                             media_type=v.mime_type, headers=headers)


@router.get("/versions/{vid}")
def get_version(vid: uuid.UUID, db: Session = DBDep) -> dict[str, Any]:
    v = storage.get_version(db, vid)
    out = serialize.version(v, db.get(DocumentMetadata, v.id))
    out["validation"] = v.validation
    return out


@router.get("/versions/{vid}/pages/{index}/thumbnail")
def thumbnail(vid: uuid.UUID, index: int, w: int = Query(240, ge=64, le=2000),
              db: Session = DBDep) -> Response:
    v = storage.get_version(db, vid)
    if v.file_ext != "pdf":
        raise AppError("Previews are available for PDF versions.", code="not_pdf")
    md = db.get(DocumentMetadata, v.id)
    if md and md.encrypted:
        raise AppError("Encrypted PDF: unlock it to preview pages.", code="password_required", status_code=423)
    png = cached_thumbnail(storage.version_path(v), v.id, v.document_id, index, w)
    return Response(png, media_type="image/png", headers={"Cache-Control": "private, max-age=3600"})


@router.get("/versions/{vid}/outline")
def outline(vid: uuid.UUID, db: Session = DBDep) -> dict[str, Any]:
    v = storage.get_version(db, vid)
    return {"items": pdf_ops.get_outline(storage.version_path(v))}


@router.post("/versions/{vid}/inspect")
def inspect(vid: uuid.UUID, body: InspectRequest | None = None, db: Session = DBDep) -> dict[str, Any]:
    """Re-inspect (optionally with the password, which is used in memory only and never stored)."""
    v = storage.get_version(db, vid)
    if v.file_ext != "pdf":
        raise AppError("Inspection is available for PDF versions.", code="not_pdf")
    info = inspect_pdf(storage.version_path(v), body.password if body else None)
    info["sha256"] = v.sha256
    info["size_bytes"] = v.size_bytes
    return info


@router.post("/versions/{vid}/preview")
def preview(vid: uuid.UUID, body: PreviewRequest, w: int = Query(600, ge=100, le=1600),
            db: Session = DBDep) -> Response:
    """Render page 1 with a watermark/page numbers applied in a temp file; nothing is stored."""
    from app.api.schemas import PageNumberParams, WatermarkParams

    v = storage.get_version(db, vid)
    s = get_settings()
    params = (WatermarkParams if body.kind == "watermark" else PageNumberParams).model_validate(body.params)
    opts = params.model_dump()
    tmpd = s.tmp_dir / "preview"
    tmpd.mkdir(parents=True, exist_ok=True)
    out = tmpd / f"{uuid.uuid4().hex}.pdf"
    try:
        if body.kind == "watermark":
            if opts.get("type") == "image" and opts.get("image_version_id"):
                iv = storage.get_version(db, opts["image_version_id"])
                opts["image_path"] = storage.version_path(iv)
            pdf_ops.watermark(storage.version_path(v), out, opts, only_first=True)
        else:
            pdf_ops.page_numbers(storage.version_path(v), out, opts, only_first=True)
        img = render_pil(out, 0, width=w)
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return Response(buf.getvalue(), media_type="image/png")
    finally:
        out.unlink(missing_ok=True)


@router.get("/versions/compare/{a}/{b}")
def compare(a: uuid.UUID, b: uuid.UUID, db: Session = DBDep) -> dict[str, Any]:
    va, vb = storage.get_version(db, a), storage.get_version(db, b)
    ma, mb = db.get(DocumentMetadata, va.id), db.get(DocumentMetadata, vb.id)
    sa, sb = serialize.version(va, ma), serialize.version(vb, mb)
    diffs = {}
    for key in ("title", "author", "subject", "keywords", "language", "encrypted"):
        x = (sa.get("metadata") or {}).get(key)
        y = (sb.get("metadata") or {}).get(key)
        if x != y:
            diffs[key] = {"a": x, "b": y}
    return {"a": sa, "b": sb, "identical": va.sha256 == vb.sha256,
            "size_change_bytes": vb.size_bytes - va.size_bytes,
            "size_change_percent": round(100.0 * (vb.size_bytes - va.size_bytes) / va.size_bytes, 2)
            if va.size_bytes else None,
            "page_count_change": (vb.page_count or 0) - (va.page_count or 0), "metadata_differences": diffs}


@router.get("/documents/{doc_id}/export")
def export_document(doc_id: uuid.UUID) -> StreamingResponse:
    from app.services.handlers import export_document_zip

    s = get_settings()
    out = s.exports_dir / f"{doc_id}-{uuid.uuid4().hex[:8]}.zip"
    export_document_zip(doc_id, out)
    audit.record("document.exported", "success", document_id=doc_id, operation="export",
                 delivery_outcome="prepared")

    async def body() -> AsyncIterator[bytes]:
        try:
            with out.open("rb") as f:
                while block := f.read(1024 * 1024):
                    yield block
        finally:
            out.unlink(missing_ok=True)

    return StreamingResponse(body(), media_type="application/zip",
                             headers={"Content-Disposition": f'attachment; filename="document-{doc_id}.zip"'})
