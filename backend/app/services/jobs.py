"""Persistent job queue and bounded worker pool.

PostgreSQL is the source of truth: jobs are claimed with `FOR UPDATE SKIP LOCKED`, progress and
steps are persisted, and outputs are registered in the same transaction that marks a job
successful. Secrets (passwords, redaction terms) are held only in memory and never persisted.
"""

from __future__ import annotations

import logging
import shutil
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import func, select, text, update

from app.core.config import get_settings
from app.core.errors import AppError, Busy, Conflict, NotFound
from app.core.files import sha256_file
from app.core.tools import tool_versions
from app.db.models import Document, DocumentMetadata, DocumentVersion, Job, JobInput, JobStep
from app.db.session import session_scope
from app.services import audit, storage

log = logging.getLogger("pdfws.jobs")

TERMINAL = {"succeeded", "failed", "cancelled"}
SECRET_KEYS = {"password", "user_password", "owner_password", "new_user_password", "new_owner_password", "terms"}


class JobCancelled(Exception):
    pass


@dataclass
class InputFile:
    version_id: uuid.UUID
    document_id: uuid.UUID
    path: Path
    sha256: str
    ext: str
    page_count: int | None
    title: str
    filename: str
    version_number: int


@dataclass
class PendingOutput:
    path: Path
    operation: str
    label: str
    mime: str
    ext: str
    document_id: uuid.UUID | None = None
    new_document: dict[str, str] | None = None  # {"title":..., "kind":..., "filename":...}
    parent_version_id: uuid.UUID | None = None
    page_count: int | None = None
    validation: dict[str, Any] | None = None
    key: str = "output"


@dataclass
class HandlerSpec:
    func: Callable[[JobContext], None]
    input_exts: tuple[str, ...] = ("pdf",)
    min_inputs: int = 1
    max_inputs: int | None = 1
    modifies_pdf: bool = True  # warn about signatures/forms
    description: str = ""


HANDLERS: dict[str, HandlerSpec] = {}


def handler(kind: str, **kw: Any) -> Callable[[Callable[[JobContext], None]], Callable[[JobContext], None]]:
    def deco(fn: Callable[[JobContext], None]) -> Callable[[JobContext], None]:
        HANDLERS[kind] = HandlerSpec(func=fn, **kw)
        return fn

    return deco


@dataclass
class JobContext:
    job_id: uuid.UUID
    kind: str
    params: dict[str, Any]
    inputs: list[InputFile]
    workdir: Path
    deadline: float
    outputs: list[PendingOutput] = field(default_factory=list)
    result: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    _step_id: int | None = None
    _last_cancel_check: float = 0.0

    # -- progress -------------------------------------------------------------
    def stage(self, name: str, current: int | None = None, total: int | None = None) -> None:
        """Record the current stage. Percentages are only shown when `total` is known."""
        self.check_cancel(force=True)
        now = datetime.now(UTC)
        with session_scope() as s:
            prog = {"stage": name, "current": current, "total": total}
            s.execute(update(Job).where(Job.id == self.job_id).values(progress=prog, heartbeat_at=now))
            step = s.get(JobStep, self._step_id) if self._step_id else None
            if step is None or step.name != name:
                if step is not None:
                    step.status, step.finished_at = "done", now
                new = JobStep(job_id=self.job_id, name=name, status="running", detail={})
                s.add(new)
                s.flush()
                self._step_id = new.id

    def step_detail(self, **detail: Any) -> None:
        if self._step_id is None:
            return
        with session_scope() as s:
            step = s.get(JobStep, self._step_id)
            if step is not None:
                step.detail = {**(step.detail or {}), **detail}

    def check_cancel(self, force: bool = False) -> None:
        now = time.monotonic()
        if now > self.deadline:
            raise AppError("The job exceeded its time limit (PDFWS_JOB_TIMEOUT_SECONDS).", code="timeout")
        if _manager.is_cancel_flagged(self.job_id):
            raise JobCancelled()
        if force or now - self._last_cancel_check > 1.0:
            self._last_cancel_check = now
            with session_scope() as s:
                if s.execute(select(Job.cancel_requested).where(Job.id == self.job_id)).scalar():
                    raise JobCancelled()

    def warn(self, msg: str) -> None:
        if msg not in self.warnings:
            self.warnings.append(msg)

    def add_output(self, out: PendingOutput) -> None:
        if out.document_id is None and out.new_document is None:
            out.document_id = self.inputs[0].document_id
        if out.parent_version_id is None and self.inputs and out.new_document is None:
            same = [i for i in self.inputs if i.document_id == out.document_id]
            out.parent_version_id = (same or self.inputs)[0].version_id
        self.outputs.append(out)

    def tmp(self, name: str) -> Path:
        p = self.workdir / name
        p.parent.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def input(self) -> InputFile:
        return self.inputs[0]


def sanitize_params(params: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    clean: dict[str, Any] = {}
    secrets: dict[str, Any] = {}
    for k, v in params.items():
        if k in SECRET_KEYS:
            secrets[k] = v
            clean[k] = "[provided]" if not isinstance(v, list) else f"[{len(v)} item(s)]"
        else:
            clean[k] = v
    return clean, secrets


class JobManager:
    def __init__(self) -> None:
        self._secrets: dict[uuid.UUID, dict[str, Any]] = {}
        self._cancel: set[uuid.UUID] = set()
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []
        self.paused = threading.Event()  # set during restore

    # -- creation ---------------------------------------------------------------
    def submit(self, kind: str, params: dict[str, Any], version_ids: list[uuid.UUID], *,
               idempotency_key: str | None = None, batch_id: uuid.UUID | None = None,
               acknowledge_warnings: bool = False) -> uuid.UUID:
        spec = HANDLERS.get(kind)
        if spec is None:
            raise NotFound(f"Unknown operation '{kind}'.")
        if len(version_ids) < spec.min_inputs or (spec.max_inputs and len(version_ids) > spec.max_inputs):
            raise AppError(f"Operation '{kind}' expects between {spec.min_inputs} and "
                           f"{spec.max_inputs or 'any number of'} input file(s).", code="invalid_inputs")
        clean, secrets = sanitize_params(params)
        s = get_settings()
        with session_scope() as session:
            if idempotency_key:
                existing = session.execute(select(Job).where(Job.idempotency_key == idempotency_key)).scalar()
                if existing is not None:
                    return existing.id
            queued = session.execute(select(func.count()).select_from(Job)
                                     .where(Job.status == "queued")).scalar() or 0
            if queued >= s.max_queued_jobs:
                raise Busy("Too many queued jobs. Wait for some to finish (PDFWS_MAX_QUEUED_JOBS).")
            versions: list[DocumentVersion] = []
            for vid in version_ids:
                v = storage.get_version(session, vid)
                if v.file_ext not in spec.input_exts:
                    raise AppError(f"Operation '{kind}' does not accept .{v.file_ext} files "
                                   f"(expects: {', '.join(spec.input_exts)}).", code="invalid_inputs")
                versions.append(v)
            if spec.modifies_pdf and not acknowledge_warnings:
                risky: list[str] = []
                for v in versions:
                    md = session.get(DocumentMetadata, v.id)
                    if md and md.has_signatures:
                        risky.append("A digital signature will be invalidated by this operation.")
                    if md and md.has_forms:
                        risky.append("Interactive form fields may be altered or lost by this operation.")
                if risky:
                    raise Conflict("This operation may alter protected document features. Confirm to continue.",
                                   code="confirmation_required", details={"warnings": sorted(set(risky))})
            job = Job(kind=kind, params=clean, status="queued", idempotency_key=idempotency_key,
                      batch_id=batch_id, document_id=versions[0].document_id if versions else None,
                      progress={"stage": "queued"})
            session.add(job)
            session.flush()
            for i, v in enumerate(versions):
                session.add(JobInput(job_id=job.id, position=i, version_id=v.id, sha256=v.sha256))
            audit.record("job.queued", "queued", session=session, job_id=job.id, operation=kind,
                         document_id=job.document_id, input_sha256=[v.sha256 for v in versions],
                         details={"params": clean})
            job_id = job.id
        if secrets:
            with self._lock:
                self._secrets[job_id] = secrets
        self._wake.set()
        return job_id

    def cancel(self, job_id: uuid.UUID) -> str:
        with session_scope() as session:
            job = session.get(Job, job_id)
            if job is None:
                raise NotFound("Job not found.")
            if job.status in TERMINAL:
                return job.status
            job.cancel_requested = True
            if job.status in ("queued", "interrupted"):
                job.status = "cancelled"
                job.finished_at = datetime.now(UTC)
                audit.record("job.cancelled", "cancelled", session=session, job_id=job.id, operation=job.kind,
                             document_id=job.document_id)
            status = job.status
        with self._lock:
            self._cancel.add(job_id)
        return status

    def resume(self, job_id: uuid.UUID, secrets: dict[str, Any] | None = None) -> None:
        with session_scope() as session:
            job = session.get(Job, job_id)
            if job is None:
                raise NotFound("Job not found.")
            if job.status not in ("interrupted", "failed"):
                raise Conflict(f"Only interrupted or failed jobs can be resumed (status: {job.status}).")
            needs = [k for k, v in (job.params or {}).items() if k in SECRET_KEYS]
            have = set((secrets or {}).keys()) | set(self._secrets.get(job_id, {}).keys())
            missing = [k for k in needs if k not in have]
            if missing:
                raise AppError("This job needs its secret parameters again (they are never stored).",
                               code="secrets_required", details={"fields": missing})
            job.status, job.cancel_requested, job.error_message, job.error_code = "queued", False, None, None
            job.progress = {"stage": "queued (resumed)"}
            audit.record("job.resumed", "queued", session=session, job_id=job.id, operation=job.kind,
                         document_id=job.document_id)
        if secrets:
            with self._lock:
                self._secrets.setdefault(job_id, {}).update(secrets)
        self._wake.set()

    def is_cancel_flagged(self, job_id: uuid.UUID) -> bool:
        with self._lock:
            return job_id in self._cancel

    # -- workers ------------------------------------------------------------------
    def start(self, n: int) -> None:
        self._stop.clear()
        for i in range(n):
            t = threading.Thread(target=self._loop, name=f"pdfws-worker-{i}", daemon=True)
            t.start()
            self._threads.append(t)

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        for t in self._threads:
            t.join(timeout=5)
        self._threads.clear()

    def _claim(self) -> uuid.UUID | None:
        with session_scope() as session:
            row = session.execute(text(
                "UPDATE jobs SET status='running', started_at=now(), heartbeat_at=now(), attempts=attempts+1 "
                "WHERE id = (SELECT id FROM jobs WHERE status='queued' ORDER BY created_at "
                "FOR UPDATE SKIP LOCKED LIMIT 1) RETURNING id")).first()
            return row[0] if row else None

    def _loop(self) -> None:
        while not self._stop.is_set():
            if self.paused.is_set():
                time.sleep(0.5)
                continue
            try:
                job_id = self._claim()
            except Exception:
                log.exception("Job claim failed")
                job_id = None
            if job_id is None:
                self._wake.wait(timeout=2.0)
                self._wake.clear()
                continue
            self.execute(job_id)

    def run_pending_sync(self) -> int:
        """Run queued jobs in the calling thread until the queue is empty (tests, CLI)."""
        n = 0
        while (job_id := self._claim()) is not None:
            self.execute(job_id)
            n += 1
        return n

    # -- execution -------------------------------------------------------------------
    def execute(self, job_id: uuid.UUID) -> None:
        s = get_settings()
        workdir = s.tmp_dir / "jobs" / str(job_id)
        started = time.monotonic()
        placed: list[Path] = []
        with session_scope() as session:
            job = session.get(Job, job_id)
            assert job is not None
            kind, params = job.kind, dict(job.params or {})
            job.tool_versions = tool_versions()
            rows = session.execute(
                select(JobInput, DocumentVersion, Document)
                .join(DocumentVersion, DocumentVersion.id == JobInput.version_id)
                .join(Document, Document.id == DocumentVersion.document_id)
                .where(JobInput.job_id == job_id).order_by(JobInput.position)).all()
            inputs = [InputFile(v.id, v.document_id, storage.version_path(v), ji.sha256, v.file_ext,
                                v.page_count, d.title, d.original_filename, v.version_number)
                      for ji, v, d in rows]
            audit.record("job.started", "running", session=session, job_id=job_id, operation=kind,
                         document_id=job.document_id, input_sha256=[i.sha256 for i in inputs])
        with self._lock:
            params.update(self._secrets.get(job_id, {}))
        ctx = JobContext(job_id=job_id, kind=kind, params=params, inputs=inputs, workdir=workdir,
                         deadline=time.monotonic() + s.job_timeout_seconds)
        status, err_msg, err_code = "succeeded", None, None
        try:
            workdir.mkdir(parents=True, exist_ok=True)
            workdir.chmod(0o700)
            spec = HANDLERS[kind]
            ctx.stage("verifying inputs")
            for inp in inputs:
                if not inp.path.exists() or sha256_file(inp.path) != inp.sha256:
                    raise AppError("An input file is missing or its hash no longer matches. "
                                   "Run the storage integrity check.", code="integrity_error")
            spec.func(ctx)
            ctx.stage("registering outputs")
            self._finalize(ctx, placed, started)
        except JobCancelled:
            status, err_msg, err_code = "cancelled", "Cancelled by user.", "cancelled"
        except AppError as exc:
            status, err_msg, err_code = "failed", exc.message, exc.code
        except MemoryError:
            status, err_msg, err_code = "failed", "Ran out of memory. Try smaller batches or lower DPI.", "memory"
        except Exception as exc:  # noqa: BLE001
            log.exception("Job %s (%s) crashed", job_id, kind)
            status, err_msg, err_code = "failed", f"Processing failed unexpectedly ({type(exc).__name__}).", "crash"
        finally:
            shutil.rmtree(workdir, ignore_errors=True)
            with self._lock:
                self._cancel.discard(job_id)
                if status in TERMINAL:
                    self._secrets.pop(job_id, None)
        if status != "succeeded":
            for p in placed:  # never leave unregistered outputs behind
                p.unlink(missing_ok=True)
            with session_scope() as session:
                job = session.get(Job, job_id)
                assert job is not None
                job.status, job.error_message, job.error_code = status, err_msg, err_code
                job.warnings = ctx.warnings
                job.result = {**(job.result or {}), **ctx.result}
                job.finished_at = datetime.now(UTC)
                job.duration_ms = int((time.monotonic() - started) * 1000)
                job.progress = {**(job.progress or {}), "stage": status}
                for st in job.steps:
                    if st.status == "running":
                        st.status = "cancelled" if status == "cancelled" else "failed"
                        st.finished_at = job.finished_at
                audit.record(f"job.{status}", status, session=session, job_id=job_id, operation=kind,
                             document_id=job.document_id, input_sha256=[i.sha256 for i in inputs],
                             details={"error_code": err_code, "message": err_msg})

    def _finalize(self, ctx: JobContext, placed: list[Path], started: float) -> None:
        with session_scope() as session:
            out_records = []
            for out in ctx.outputs:
                if not out.path.exists() or out.path.stat().st_size == 0:
                    raise AppError("Processing produced no output file.", code="empty_output")
                if out.ext == "pdf":
                    _check_pdf(out.path)
                if out.validation is not None and out.validation.get("passed") is False:
                    raise AppError("Output failed validation and was not registered.", code="validation_failed")
                doc_id = out.document_id
                if out.new_document:
                    doc_id = storage.new_document(session, **out.new_document)
                assert doc_id is not None
                v = storage.place_output(session, document_id=doc_id, src=out.path, operation=out.operation,
                                         label=out.label, mime=out.mime, ext=out.ext,
                                         parent_version_id=out.parent_version_id, job_id=ctx.job_id,
                                         page_count=out.page_count, validation=out.validation, placed=placed)
                out_records.append({"key": out.key, "version_id": str(v.id), "document_id": str(doc_id),
                                    "sha256": v.sha256, "size_bytes": v.size_bytes, "label": v.label})
                audit.record("output.generated", "success", session=session, job_id=ctx.job_id,
                             document_id=doc_id, version_id=v.id, operation=ctx.kind,
                             input_sha256=[i.sha256 for i in ctx.inputs], output_sha256=v.sha256,
                             details={"size_bytes": v.size_bytes, "label": v.label})
            job = session.get(Job, ctx.job_id)
            assert job is not None
            job.status = "succeeded"
            job.result = {**ctx.result, "outputs": out_records}
            job.warnings = ctx.warnings
            job.finished_at = datetime.now(UTC)
            job.duration_ms = int((time.monotonic() - started) * 1000)
            job.progress = {"stage": "done"}
            for st in job.steps:
                if st.status == "running":
                    st.status, st.finished_at = "done", job.finished_at
            audit.record("job.succeeded", "success", session=session, job_id=ctx.job_id, operation=ctx.kind,
                         document_id=job.document_id, input_sha256=[i.sha256 for i in ctx.inputs],
                         output_sha256=[o["sha256"] for o in out_records],
                         details={"duration_ms": job.duration_ms, "warnings": len(ctx.warnings)})


def _check_pdf(path: Path) -> None:
    import pikepdf

    try:
        with pikepdf.open(path) as pdf:
            if len(pdf.pages) == 0:
                raise AppError("Output PDF has no pages.", code="invalid_output")
    except pikepdf.PasswordError:
        return  # encrypted output (protect operation) is expected to require a password
    except pikepdf.PdfError:
        raise AppError("Output PDF is not valid.", code="invalid_output") from None


def recover_interrupted() -> int:
    """Mark jobs left running by a crash/restart as interrupted so the user can resume them."""
    with session_scope() as session:
        jobs = session.execute(select(Job).where(Job.status == "running")).scalars().all()
        for job in jobs:
            job.status = "interrupted"
            job.error_message = "The backend stopped while this job was running. Resume to continue."
            job.error_code = "interrupted"
            job.progress = {**(job.progress or {}), "stage": "interrupted"}
            audit.record("job.interrupted", "interrupted", session=session, job_id=job.id, operation=job.kind,
                         document_id=job.document_id)
        return len(jobs)


def cleanup_temp() -> int:
    s = get_settings()
    cutoff = time.time() - s.temp_max_age_hours * 3600
    removed = 0
    for p in s.tmp_dir.glob("*/*"):
        try:
            if p.stat().st_mtime < cutoff:
                shutil.rmtree(p, ignore_errors=True) if p.is_dir() else p.unlink(missing_ok=True)
                removed += 1
        except FileNotFoundError:
            pass
    return removed


_manager = JobManager()


def manager() -> JobManager:
    return _manager
