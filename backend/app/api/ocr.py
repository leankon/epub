"""OCR sessions: analysis, review/correction, reprocessing and approval."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Query
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from app.api import serialize
from app.api.schemas import BookMetadata, OcrOptions, OcrSessionCreate, RegionsUpdate, ReprocessRequest
from app.core.config import get_settings
from app.core.errors import AppError, Conflict, NotFound
from app.core.files import ensure_within
from app.db.models import Job, OcrCorrection, OcrPage, OcrSession
from app.db.session import DBDep, session_scope
from app.services import audit, storage
from app.services.jobs import manager
from app.services.ocr.pipeline import session_dir, summarize

router = APIRouter(prefix="/api/ocr", tags=["ocr"])


def create_session(version_id: uuid.UUID, options: OcrOptions) -> uuid.UUID:
    s = get_settings()
    with session_scope() as db:
        v = storage.get_version(db, version_id)
        if v.file_ext != "pdf":
            raise AppError("OCR needs a PDF version (convert images to PDF first).", code="not_pdf")
        sess = OcrSession(document_id=v.document_id, version_id=v.id, status="processing",
                          languages=options.languages or s.ocr_languages,
                          settings=options.model_dump(mode="json", exclude_none=True),
                          page_count=v.page_count or 0)
        db.add(sess)
        db.flush()
        return sess.id


@router.post("/sessions", status_code=202)
def start_session(body: OcrSessionCreate) -> dict[str, Any]:
    sid = create_session(body.version_id, body.options)
    job_id = manager().submit("ocr_analyze", {"session_id": str(sid)}, [body.version_id])
    with session_scope() as db:
        sess = db.get(OcrSession, sid)
        assert sess is not None
        sess.job_id = job_id
        return {"session": serialize.ocr_session(sess), "job_id": str(job_id)}


@router.get("/sessions")
def list_sessions(document_id: uuid.UUID | None = None, db: Session = DBDep) -> dict[str, Any]:
    q = select(OcrSession).order_by(OcrSession.created_at.desc()).limit(200)
    if document_id:
        q = q.where(OcrSession.document_id == document_id)
    return {"items": [serialize.ocr_session(s) for s in db.execute(q).scalars()]}


def _session(db: Session, sid: uuid.UUID) -> OcrSession:
    sess = db.get(OcrSession, sid)
    if sess is None:
        raise NotFound("OCR session not found.")
    return sess


@router.get("/sessions/{sid}")
def get_session(sid: uuid.UUID, db: Session = DBDep) -> dict[str, Any]:
    sess = _session(db, sid)
    pages = db.execute(select(OcrPage).where(OcrPage.session_id == sid).order_by(OcrPage.page_index)).scalars()
    out = serialize.ocr_session(sess)
    out["pages"] = [{"page_index": p.page_index, "status": p.status, "mode": "ocr" if (p.classification or {})
                     .get("needs_ocr") else "text_layer", "kind": (p.classification or {}).get("kind"),
                     "mean_confidence": p.mean_confidence, "needs_review": p.needs_review, "reviewed": p.reviewed,
                     "low_confidence_regions": sum(1 for r in (p.regions or []) if r.get("low_confidence")
                                                   and r.get("corrected_text") is None),
                     "fallback_recommended": (p.classification or {}).get("fallback_recommended", False),
                     "error": p.error} for p in pages]
    if sess.job_id:
        j = db.get(Job, sess.job_id)
        out["job"] = serialize.job(j) if j else None
    return out


@router.get("/sessions/{sid}/pages/{index}")
def get_page(sid: uuid.UUID, index: int, db: Session = DBDep) -> dict[str, Any]:
    _session(db, sid)
    p = db.get(OcrPage, (sid, index))
    if p is None:
        raise NotFound("Page not analysed yet.")
    return {"page_index": p.page_index, "status": p.status, "classification": p.classification,
            "preprocessing": p.preprocessing, "regions": p.regions, "mean_confidence": p.mean_confidence,
            "needs_review": p.needs_review, "reviewed": p.reviewed, "image_size": p.image_size, "error": p.error,
            "has_processed_image": bool(p.processed_image and p.processed_image != p.source_image)}


@router.get("/sessions/{sid}/pages/{index}/image")
def page_image(sid: uuid.UUID, index: int, kind: str = Query("processed", pattern="^(processed|source)$"),
               db: Session = DBDep) -> FileResponse:
    p = db.get(OcrPage, (sid, index))
    if p is None:
        raise NotFound("Page not analysed yet.")
    rel = p.processed_image if kind == "processed" else p.source_image
    if not rel:
        raise NotFound("No image for this page.")
    sdir = session_dir(sid)
    return FileResponse(ensure_within(sdir, sdir / rel), media_type="image/jpeg",
                        headers={"Cache-Control": "private, max-age=600"})


@router.get("/sessions/{sid}/assets/{name}")
def asset(sid: uuid.UUID, name: str, db: Session = DBDep) -> FileResponse:
    _session(db, sid)
    if "/" in name or "\\" in name or name.startswith("."):
        raise AppError("Invalid asset name.", code="unsafe_path")
    sdir = session_dir(sid)
    path = ensure_within(sdir, sdir / "assets" / name)
    if not path.is_file():
        raise NotFound("Asset not found.")
    mt = "image/jpeg" if path.suffix.lower() in (".jpg", ".jpeg") else "image/png"
    return FileResponse(path, media_type=mt)


@router.put("/sessions/{sid}/pages/{index}/regions")
def update_regions(sid: uuid.UUID, index: int, body: RegionsUpdate, db: Session = DBDep) -> dict[str, Any]:
    """Save corrections. OCR text is preserved; user text goes to `corrected_text` (never the PDF)."""
    sess = _session(db, sid)
    if sess.status == "processing":
        raise Conflict("The session is still being analysed.")
    p = db.get(OcrPage, (sid, index), with_for_update=True)
    if p is None:
        raise NotFound("Page not analysed yet.")
    regions = [dict(r) for r in (p.regions or [])]
    by_id = {r["id"]: r for r in regions}
    changed = 0
    for upd in body.regions:
        r = by_id.get(upd.id)
        if r is None:
            raise AppError(f"Unknown region {upd.id}.", code="unknown_region")
        if upd.revert:
            if r.get("corrected_text") is not None:
                db.add(OcrCorrection(session_id=sid, page_index=index, region_id=upd.id, field="revert",
                                     before_len=len(r["corrected_text"]), after_len=len(r.get("text") or "")))
                r["corrected_text"] = None
                changed += 1
        elif upd.corrected_text is not None and upd.corrected_text != (r.get("corrected_text") or r.get("text")):
            before = r.get("corrected_text") if r.get("corrected_text") is not None else r.get("text") or ""
            db.add(OcrCorrection(session_id=sid, page_index=index, region_id=upd.id, field="text",
                                 before_len=len(before), after_len=len(upd.corrected_text)))
            r["corrected_text"] = upd.corrected_text
            changed += 1
        if upd.role is not None and upd.role != r["role"]:
            db.add(OcrCorrection(session_id=sid, page_index=index, region_id=upd.id, field="role"))
            r["role"] = upd.role
            changed += 1
        if upd.heading_level is not None:
            r["heading_level"] = upd.heading_level
        if upd.excluded is not None and upd.excluded != r.get("excluded"):
            db.add(OcrCorrection(session_id=sid, page_index=index, region_id=upd.id, field="exclude"))
            r["excluded"] = upd.excluded
            changed += 1
    p.regions = regions
    flag_modified(p, "regions")
    if body.mark_reviewed:
        p.reviewed = True
    if sess.status == "approved" and changed:
        sess.status = "review"  # edits after approval require re-approval
        sess.approved_at = None
    if changed or body.mark_reviewed:
        audit.record("ocr.corrected", "success", session=db, document_id=sess.document_id,
                     operation="ocr_correction",
                     details={"session_id": str(sid), "page": index + 1, "changes": changed,
                              "reviewed": body.mark_reviewed})
    db.flush()
    return {"changed": changed, "reviewed": p.reviewed, "status": sess.status}


@router.post("/sessions/{sid}/reprocess", status_code=202)
def reprocess(sid: uuid.UUID, body: ReprocessRequest) -> dict[str, Any]:
    with session_scope() as db:
        sess = _session(db, sid)
        if sess.status == "processing":
            raise Conflict("The session is still being analysed.")
        if any(not 0 <= p < sess.page_count for p in body.pages):
            raise AppError("Page out of range.", code="invalid_range")
        vid = sess.version_id
        sess.status = "processing"
        sess.approved_at = None
    job_id = manager().submit("ocr_analyze", {"session_id": str(sid), "pages": body.pages, "force": True,
                                              "options": body.options.model_dump(mode="json", exclude_none=True)},
                              [vid])
    with session_scope() as db:
        sess = _session(db, sid)
        sess.job_id = job_id
    return {"job_id": str(job_id)}


@router.post("/sessions/{sid}/approve")
def approve(sid: uuid.UUID, db: Session = DBDep) -> dict[str, Any]:
    sess = _session(db, sid)
    if sess.status == "processing":
        raise Conflict("The session is still being analysed.")
    summary = summarize(sid)
    if summary["done"] + len(summary["failed"]) < sess.page_count:
        raise Conflict("Not all pages have been analysed yet.")
    sess.status = "approved"
    sess.approved_at = datetime.now(UTC)
    sess.summary = summary
    audit.record("ocr.approved", "success", session=db, document_id=sess.document_id, version_id=sess.version_id,
                 operation="ocr_approve", details={"session_id": str(sid), "pages": sess.page_count,
                                                   "unreviewed_low_confidence_pages": len(summary["needs_review"])})
    return serialize.ocr_session(sess)


@router.put("/sessions/{sid}/book")
def set_book_metadata(sid: uuid.UUID, body: BookMetadata, db: Session = DBDep) -> dict[str, Any]:
    sess = _session(db, sid)
    data = body.model_dump(mode="json", exclude_none=True)
    for c in data.get("chapters") or []:
        if c["page"] > max(sess.page_count, 1):
            raise AppError(f"Chapter '{c['title']}' starts after the last page.", code="invalid_range")
    sess.book_metadata = data
    return serialize.ocr_session(sess)
