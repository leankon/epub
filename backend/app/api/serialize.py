"""Response shaping. Storage paths are never exposed; only identifiers."""

from __future__ import annotations

from typing import Any

from app.db.models import Document, DocumentMetadata, DocumentVersion, Job, OcrSession


def version(v: DocumentVersion, md: DocumentMetadata | None = None) -> dict[str, Any]:
    out: dict[str, Any] = {
        "id": str(v.id), "document_id": str(v.document_id), "version_number": v.version_number,
        "is_original": v.is_original, "operation": v.operation, "label": v.label, "mime_type": v.mime_type,
        "ext": v.file_ext, "size_bytes": v.size_bytes, "sha256": v.sha256, "page_count": v.page_count,
        "parent_version_id": str(v.parent_version_id) if v.parent_version_id else None,
        "produced_by_job_id": str(v.produced_by_job_id) if v.produced_by_job_id else None,
        "created_at": v.created_at.isoformat() if v.created_at else None,
        "expired": v.expired_at is not None,
        "validation_passed": (v.validation or {}).get("passed") if v.validation else None,
    }
    if md is not None:
        out["metadata"] = {
            "title": md.title, "author": md.author, "subject": md.subject, "keywords": md.keywords,
            "language": md.language, "encrypted": md.encrypted, "has_signatures": md.has_signatures,
            "has_forms": md.has_forms, "has_annotations": md.has_annotations,
            "has_attachments": md.has_attachments, "has_javascript": md.has_javascript,
            "page_sizes": md.page_sizes, "warnings": (md.details or {}).get("warnings", []),
            "signatures": (md.details or {}).get("signatures", []),
            "needs_password": (md.details or {}).get("needs_password", False),
            "pdf_version": (md.details or {}).get("pdf_version"),
            "outline_count": (md.details or {}).get("outline_count"),
            "revisions": (md.details or {}).get("revisions"),
            "epub_validation": (md.details or {}).get("epub_validation"),
        }
    return out


def document(d: Document, latest: DocumentVersion | None = None, md: DocumentMetadata | None = None,
             n_versions: int | None = None) -> dict[str, Any]:
    return {"id": str(d.id), "title": d.title, "original_filename": d.original_filename, "kind": d.kind,
            "tags": d.tags or [], "created_at": d.created_at.isoformat() if d.created_at else None,
            "updated_at": d.updated_at.isoformat() if d.updated_at else None,
            "latest_version": version(latest, md) if latest else None, "version_count": n_versions}


def job(j: Job, *, detail: bool = False) -> dict[str, Any]:
    out: dict[str, Any] = {
        "id": str(j.id), "kind": j.kind, "status": j.status, "document_id": str(j.document_id) if j.document_id
        else None, "batch_id": str(j.batch_id) if j.batch_id else None, "progress": j.progress or {},
        "warnings": j.warnings or [], "error": {"code": j.error_code, "message": j.error_message}
        if j.error_message else None, "created_at": j.created_at.isoformat() if j.created_at else None,
        "started_at": j.started_at.isoformat() if j.started_at else None,
        "finished_at": j.finished_at.isoformat() if j.finished_at else None, "duration_ms": j.duration_ms,
        "attempts": j.attempts, "outputs": (j.result or {}).get("outputs", []),
        "resumable": j.status in ("interrupted", "failed"),
    }
    if detail:
        out.update({"params": j.params, "result": j.result or {}, "tool_versions": j.tool_versions or {},
                    "inputs": [{"position": i.position, "version_id": str(i.version_id) if i.version_id else None,
                                "sha256": i.sha256} for i in j.inputs],
                    "steps": [{"name": st.name, "status": st.status, "detail": st.detail,
                               "started_at": st.started_at.isoformat() if st.started_at else None,
                               "finished_at": st.finished_at.isoformat() if st.finished_at else None}
                              for st in j.steps]})
    return out


def ocr_session(s: OcrSession) -> dict[str, Any]:
    return {"id": str(s.id), "document_id": str(s.document_id), "version_id": str(s.version_id),
            "job_id": str(s.job_id) if s.job_id else None, "status": s.status, "languages": s.languages,
            "settings": s.settings, "page_count": s.page_count, "summary": s.summary or {},
            "book_metadata": s.book_metadata or {}, "created_at": s.created_at.isoformat() if s.created_at else None,
            "approved_at": s.approved_at.isoformat() if s.approved_at else None}
