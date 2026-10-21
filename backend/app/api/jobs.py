"""Operations (job submission), job status, cancellation, resume and batches."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Query
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api import serialize
from app.api.schemas import BATCHABLE, PARAM_MODELS, BatchCreate, JobCreate, ResumeRequest
from app.core.errors import AppError, NotFound
from app.db.models import Batch, Job
from app.db.session import DBDep, session_scope
from app.services.jobs import HANDLERS, manager

router = APIRouter(prefix="/api", tags=["jobs"])


def validate_params(kind: str, params: dict[str, Any]) -> dict[str, Any]:
    model = PARAM_MODELS.get(kind)
    if model is None:
        raise AppError(f"Operation '{kind}' cannot be submitted directly.", code="invalid_operation")
    try:
        return model.model_validate(params).model_dump(mode="json", exclude_none=True)
    except ValidationError as exc:
        issues = [{"loc": [str(x) for x in e["loc"]], "msg": e["msg"]} for e in exc.errors()]
        raise AppError("Invalid parameters.", code="invalid_params", status_code=422,
                       details={"issues": issues}) from None


@router.get("/operations")
def operations() -> dict[str, Any]:
    return {"operations": [{"kind": k, "description": h.description, "input_exts": list(h.input_exts),
                            "min_inputs": h.min_inputs, "max_inputs": h.max_inputs,
                            "batchable": k in BATCHABLE, "direct": k in PARAM_MODELS}
                           for k, h in HANDLERS.items()]}


RANGE_FIELDS = {"split": "ranges", "rotate": "pages", "watermark": "pages", "page_numbers": "pages",
                "extract_images": "pages", "pdf_to_images": "pages"}


def precheck_pages(kind: str, params: dict[str, Any], version_ids: list[uuid.UUID]) -> None:
    """Reject invalid page ranges synchronously (422) instead of queueing a job that would fail."""
    from app.services import pdf_ops, storage

    if not version_ids:
        return
    with session_scope() as db:
        counts = [storage.get_version(db, v).page_count for v in version_ids]
    n = counts[0]
    if not n:
        return
    field = RANGE_FIELDS.get(kind)
    if field and params.get(field) and not (kind == "split" and params.get("mode") in ("every", "bookmarks")):
        pdf_ops.parse_page_ranges(str(params[field]), n)
    if kind == "split" and params.get("mode", "ranges") in ("ranges", "extract") and not params.get("ranges"):
        raise AppError("Enter the page ranges to split or extract.", code="invalid_range", status_code=422)
    if kind == "organize":
        bad = [o["page"] for o in params.get("order", []) if o["page"] >= n]
        if bad:
            raise AppError(f"Page {bad[0] + 1} does not exist (document has {n} pages).", code="invalid_range",
                           status_code=422)
    if kind == "redact":
        bad = [a["page"] for a in params.get("areas", []) if a["page"] >= n]
        if bad:
            raise AppError(f"Redaction area on page {bad[0] + 1}, which does not exist.", code="invalid_range",
                           status_code=422)


@router.post("/jobs", status_code=202)
def create_job(body: JobCreate) -> dict[str, Any]:
    params = validate_params(body.kind, body.params)
    try:
        precheck_pages(body.kind, params, body.version_ids)
    except AppError as exc:
        if exc.code == "invalid_range":
            exc.status_code = 422
        raise
    job_id = manager().submit(body.kind, params, body.version_ids, idempotency_key=body.idempotency_key,
                              acknowledge_warnings=body.acknowledge_warnings)
    with session_scope() as db:
        j = db.get(Job, job_id)
        assert j is not None
        return serialize.job(j)


@router.post("/operations/{kind}", status_code=202)
def run_operation(kind: str, body: JobCreate) -> dict[str, Any]:
    """Alias: POST /api/operations/merge etc. (`kind` in the body is ignored)."""
    return create_job(JobCreate(kind=kind, version_ids=body.version_ids, params=body.params,
                                idempotency_key=body.idempotency_key, acknowledge_warnings=body.acknowledge_warnings))


@router.get("/jobs")
def list_jobs(status: str | None = None, document_id: uuid.UUID | None = None, batch_id: uuid.UUID | None = None,
              kind: str | None = None, limit: int = Query(50, ge=1, le=500), offset: int = Query(0, ge=0),
              db: Session = DBDep) -> dict[str, Any]:
    q = select(Job)
    if status:
        q = q.where(Job.status.in_(status.split(",")))
    if document_id:
        q = q.where(Job.document_id == document_id)
    if batch_id:
        q = q.where(Job.batch_id == batch_id)
    if kind:
        q = q.where(Job.kind == kind)
    jobs = db.execute(q.order_by(Job.created_at.desc()).limit(limit).offset(offset)).scalars()
    return {"items": [serialize.job(j) for j in jobs]}


@router.get("/jobs/{job_id}")
def get_job(job_id: uuid.UUID, db: Session = DBDep) -> dict[str, Any]:
    j = db.get(Job, job_id)
    if j is None:
        raise NotFound("Job not found.")
    return serialize.job(j, detail=True)


@router.post("/jobs/{job_id}/cancel")
def cancel_job(job_id: uuid.UUID) -> dict[str, Any]:
    return {"status": manager().cancel(job_id)}


@router.post("/jobs/{job_id}/resume")
def resume_job(job_id: uuid.UUID, body: ResumeRequest) -> dict[str, Any]:
    manager().resume(job_id, body.secrets or None)
    return {"status": "queued"}


@router.post("/batches", status_code=202)
def create_batch(body: BatchCreate) -> dict[str, Any]:
    if body.kind not in BATCHABLE:
        raise AppError(f"'{body.kind}' cannot be run as a batch.", code="invalid_operation")
    params = validate_params(body.kind, body.params)
    with session_scope() as db:
        b = Batch(operation=body.kind, params={k: v for k, v in params.items() if "password" not in k})
        db.add(b)
        db.flush()
        bid = b.id
    jobs, errors = [], []
    for vid in body.version_ids:
        try:
            jobs.append(str(manager().submit(body.kind, params, [vid], batch_id=bid,
                                             acknowledge_warnings=body.acknowledge_warnings)))
        except AppError as exc:
            errors.append({"version_id": str(vid), "code": exc.code, "message": exc.message})
    return {"batch_id": str(bid), "jobs": jobs, "rejected": errors}


@router.get("/batches/{batch_id}")
def get_batch(batch_id: uuid.UUID, db: Session = DBDep) -> dict[str, Any]:
    b = db.get(Batch, batch_id)
    if b is None:
        raise NotFound("Batch not found.")
    jobs = db.execute(select(Job).where(Job.batch_id == batch_id).order_by(Job.created_at)).scalars().all()
    counts: dict[str, int] = {}
    for j in jobs:
        counts[j.status] = counts.get(j.status, 0) + 1
    return {"id": str(b.id), "operation": b.operation, "created_at": b.created_at.isoformat(), "counts": counts,
            "total": len(jobs), "jobs": [serialize.job(j) for j in jobs]}


@router.get("/batches")
def list_batches(db: Session = DBDep) -> dict[str, Any]:
    rows = db.execute(select(Batch).order_by(Batch.created_at.desc()).limit(50)).scalars()
    return {"items": [{"id": str(b.id), "operation": b.operation, "created_at": b.created_at.isoformat()}
                      for b in rows]}
