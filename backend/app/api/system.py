"""Health, readiness, diagnostics, settings, storage, backups, expiration and audit."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.api.schemas import PolicyUpdate, RestoreRequest
from app.core.config import get_settings
from app.core.errors import AppError, NotFound
from app.core.tools import report_dicts, tool_report
from app.db.models import (
    AuditEvent,
    BackupManifest,
    Document,
    DocumentVersion,
    ExpirationPolicy,
    Job,
)
from app.db.session import DBDep, session_scope
from app.services import audit, storage
from app.services.jobs import manager

router = APIRouter(prefix="/api", tags=["system"])
APP_VERSION = "1.0.0"


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/ready")
def ready(db: Session = DBDep) -> dict[str, Any]:
    db.execute(text("SELECT 1"))
    rev = db.execute(text("SELECT version_num FROM alembic_version")).scalar()
    s = get_settings()
    tools = tool_report()
    missing = [{"tool": v.name, "required_for": v.required_for, "install_hint": v.install_hint, "detail": v.detail}
               for v in tools.values() if not v.available]
    return {"status": "ready", "database": "ok", "migration": rev, "data_dir_writable": s.data_dir.exists(),
            "missing_tools": missing, "version": APP_VERSION}


@router.get("/diagnostics")
def diagnostics() -> dict[str, Any]:
    tool_report.cache_clear()
    return {"tools": report_dicts()}


@router.get("/settings")
def settings_view() -> dict[str, Any]:
    s = get_settings()
    return {"bind": f"{s.host}:{s.backend_port}", "allow_network": s.allow_network,
            "data_dir": str(s.data_dir), "max_upload_mb": s.max_upload_mb, "max_pages": s.max_pages,
            "max_concurrent_jobs": s.max_concurrent_jobs, "max_queued_jobs": s.max_queued_jobs,
            "job_timeout_seconds": s.job_timeout_seconds, "subprocess_timeout_seconds": s.subprocess_timeout_seconds,
            "subprocess_memory_mb": s.subprocess_memory_mb, "ocr_dpi": s.ocr_dpi, "ocr_languages": s.ocr_languages,
            "temp_max_age_hours": s.temp_max_age_hours, "version": APP_VERSION,
            "privacy": "All processing runs on this computer. No telemetry, accounts or remote services."}


@router.get("/dashboard")
def dashboard(db: Session = DBDep) -> dict[str, Any]:
    counts = dict(db.execute(select(Job.status, func.count()).group_by(Job.status)).all())
    return {"documents": db.execute(select(func.count()).select_from(Document)).scalar(),
            "versions": db.execute(select(func.count()).select_from(DocumentVersion)
                                   .where(DocumentVersion.expired_at.is_(None))).scalar(),
            "jobs_by_status": counts, "storage": storage.storage_usage()}


# ----------------------------------------------------------------- storage & lifecycle

@router.get("/storage")
def storage_info(db: Session = DBDep) -> dict[str, Any]:
    policies = db.execute(select(ExpirationPolicy).order_by(ExpirationPolicy.id)).scalars()
    return {"usage": storage.storage_usage(),
            "policies": [{"id": p.id, "enabled": p.enabled, "max_age_days": p.max_age_days,
                          "keep_latest": p.keep_latest} for p in policies],
            "documents": db.execute(select(func.count()).select_from(Document)).scalar(),
            "versions": db.execute(select(func.count()).select_from(DocumentVersion)
                                   .where(DocumentVersion.expired_at.is_(None))).scalar(),
            "data_dir": str(get_settings().data_dir)}


@router.put("/storage/policies/{policy_id}")
def update_policy(policy_id: str, body: PolicyUpdate, db: Session = DBDep) -> dict[str, Any]:
    p = db.get(ExpirationPolicy, policy_id)
    if p is None:
        raise NotFound("Unknown policy.")
    p.enabled, p.max_age_days, p.keep_latest = body.enabled, body.max_age_days, body.keep_latest
    audit.record("policy.updated", "success", session=db, operation="expiration_policy",
                 details={"policy": policy_id, "enabled": body.enabled, "max_age_days": body.max_age_days})
    return {"id": p.id, "enabled": p.enabled, "max_age_days": p.max_age_days, "keep_latest": p.keep_latest}


@router.post("/storage/sweep")
def run_sweep() -> dict[str, Any]:
    from app.services.lifecycle import sweep

    return sweep()


@router.post("/storage/verify")
def verify_storage(db: Session = DBDep) -> dict[str, Any]:
    res = storage.verify_hashes(db)
    audit.record("storage.verified", "success" if res["ok"] else "failure", session=db, operation="verify_hashes",
                 details={"checked": res["checked"], "problems": len(res["problems"])})
    return res


@router.get("/export")
def export_all() -> StreamingResponse:
    """Full export: every retained version plus a metadata JSON file per document."""
    import json
    import zipfile

    from app.services.handlers import export_document_zip

    s = get_settings()
    out = s.exports_dir / f"export-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}.zip"
    with session_scope() as db:
        ids = [d for (d,) in db.execute(select(Document.id))]
    with zipfile.ZipFile(out, "w", zipfile.ZIP_STORED, allowZip64=True) as z:
        for d in ids:
            part = s.exports_dir / f"{d}.part.zip"
            export_document_zip(d, part)
            with zipfile.ZipFile(part) as pz:
                for info in pz.infolist():
                    with pz.open(info) as src, z.open(f"{d}/{info.filename}", "w", force_zip64=True) as dst:
                        while b := src.read(1024 * 1024):
                            dst.write(b)
            part.unlink()
        z.writestr("export.json", json.dumps({"exported_at": datetime.now(UTC).isoformat(), "documents": len(ids)}))
    audit.record("data.exported", "success", operation="export_all", delivery_outcome="prepared",
                 details={"documents": len(ids)})

    async def body() -> AsyncIterator[bytes]:
        try:
            with out.open("rb") as f:
                while block := f.read(1024 * 1024):
                    yield block
                    await asyncio.sleep(0)
        finally:
            out.unlink(missing_ok=True)

    return StreamingResponse(body(), media_type="application/zip",
                             headers={"Content-Disposition": f'attachment; filename="{out.name}"'})


# ----------------------------------------------------------------- backups

@router.get("/backups")
def list_backups(db: Session = DBDep) -> dict[str, Any]:
    s = get_settings()
    rows = db.execute(select(BackupManifest).order_by(BackupManifest.created_at.desc())).scalars().all()
    known = {r.directory for r in rows}
    items = [{"id": str(r.id), "directory": r.directory, "created_at": r.created_at.isoformat(),
              "status": r.status, "file_count": r.file_count, "total_bytes": r.total_bytes,
              "manifest_sha256": r.manifest_sha256, "verified_at": r.verified_at.isoformat() if r.verified_at else None,
              "present": (s.backups_dir / r.directory / "manifest.json").exists()} for r in rows]
    for d in sorted(s.backups_dir.glob("*/manifest.json")):
        if d.parent.name not in known:  # e.g. backups made before a restore
            items.append({"id": None, "directory": d.parent.name, "status": "unregistered", "present": True})
    return {"items": items}


@router.post("/backups", status_code=202)
def create_backup() -> dict[str, Any]:
    return {"job_id": str(manager().submit("backup", {}, []))}


def _backup_dir(id_or_dir: str):  # type: ignore[no-untyped-def]
    from app.core.files import ensure_within
    from app.services.backup import backup_dir_for

    try:
        return backup_dir_for(uuid.UUID(id_or_dir))
    except ValueError:
        s = get_settings()
        d = ensure_within(s.backups_dir, s.backups_dir / id_or_dir)
        if not (d / "manifest.json").exists():
            raise NotFound("Backup not found.") from None
        return d


@router.post("/backups/{backup}/verify")
def verify_backup(backup: str) -> dict[str, Any]:
    from app.services.backup import verify_backup_dir

    res = verify_backup_dir(_backup_dir(backup))
    audit.record("backup.verified", "success" if res["ok"] else "failure", operation="backup_verify",
                 input_sha256=res.get("manifest_sha256"), details={"checked": res["checked"],
                                                                   "problems": len(res["problems"])})
    return res


@router.post("/backups/{backup}/restore")
async def restore(backup: str, body: RestoreRequest) -> dict[str, Any]:
    from app.services.backup import restore_backup

    if body.confirm != "RESTORE":
        raise AppError("Type RESTORE to confirm.", code="confirmation_required")
    d = _backup_dir(backup)
    return await asyncio.to_thread(restore_backup, d, confirm=body.confirm, safety_backup=body.safety_backup)


# ----------------------------------------------------------------- audit

@router.get("/audit")
def audit_list(document_id: uuid.UUID | None = None, event_type: str | None = None,
               limit: int = Query(100, ge=1, le=1000), before_seq: int | None = None,
               db: Session = DBDep) -> dict[str, Any]:
    q = select(AuditEvent).order_by(AuditEvent.seq.desc()).limit(limit)
    if document_id:
        q = q.where(AuditEvent.document_id == document_id)
    if event_type:
        q = q.where(AuditEvent.event_type.like(event_type.replace("*", "%")))
    if before_seq:
        q = q.where(AuditEvent.seq < before_seq)
    return {"items": [{"seq": e.seq, "ts": e.ts.isoformat(), "event_type": e.event_type, "status": e.status,
                       "document_id": str(e.document_id) if e.document_id else None,
                       "version_id": str(e.version_id) if e.version_id else None,
                       "job_id": str(e.job_id) if e.job_id else None, "operation": e.operation,
                       "input_sha256": e.input_sha256, "output_sha256": e.output_sha256,
                       "delivery_outcome": e.delivery_outcome, "details": e.details,
                       "event_hash": e.event_hash} for e in db.execute(q).scalars()]}


@router.get("/audit/verify")
def audit_verify(db: Session = DBDep) -> dict[str, Any]:
    return audit.verify_chain(db)


@router.get("/audit/export")
def audit_export(format: str = Query("json", pattern="^(json|csv)$"),
                 document_id: uuid.UUID | None = None) -> StreamingResponse:
    def gen():  # type: ignore[no-untyped-def]
        with session_scope() as db:
            yield from (audit.export_json(db, document_id) if format == "json" else audit.export_csv(db, document_id))
        audit.record("audit.exported", "success", operation="audit_export", delivery_outcome="delivered",
                     details={"format": format})

    mt = "application/json" if format == "json" else "text/csv"
    return StreamingResponse(gen(), media_type=mt,
                             headers={"Content-Disposition": f'attachment; filename="audit-log.{format}"'})
