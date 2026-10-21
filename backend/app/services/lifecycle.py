"""Expiration policies. Conservative defaults: nothing expires unless the user enables a policy,
and originals expire only if the separate 'original' policy is explicitly enabled."""

from __future__ import annotations

import logging
import threading
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select

from app.db.models import Document, DocumentVersion, ExpirationPolicy, Job
from app.db.session import session_scope
from app.services import audit, storage
from app.services.jobs import cleanup_temp

log = logging.getLogger("pdfws.lifecycle")


def sweep(now: datetime | None = None) -> dict[str, Any]:
    now = now or datetime.now(UTC)
    expired_versions = 0
    deleted_docs: list[str] = []
    with session_scope() as s:
        pol = {p.id: p for p in s.execute(select(ExpirationPolicy)).scalars()}
        derived = pol.get("derived")
        if derived is not None and derived.enabled:
            cutoff = now - timedelta(days=derived.max_age_days)
            latest = {d: n for d, n in s.execute(select(DocumentVersion.document_id,
                                                        func.max(DocumentVersion.version_number))
                                                 .group_by(DocumentVersion.document_id))}
            q = select(DocumentVersion).where(DocumentVersion.is_original.is_(False),
                                              DocumentVersion.expired_at.is_(None),
                                              DocumentVersion.created_at < cutoff)
            for v in s.execute(q).scalars():
                if derived.keep_latest and latest.get(v.document_id) == v.version_number:
                    continue
                busy = s.execute(select(func.count()).select_from(Job).where(
                    Job.document_id == v.document_id, Job.status.in_(["queued", "running"]))).scalar()
                if busy:
                    continue
                p = storage.version_path(v)
                if p.exists():
                    p.chmod(0o600)
                    p.unlink()
                v.expired_at = now
                expired_versions += 1
                audit.record("version.expired", "success", session=s, document_id=v.document_id,
                             version_id=v.id, operation="expire", input_sha256=v.sha256,
                             details={"policy": "derived", "max_age_days": derived.max_age_days})
        original = pol.get("original")
        if original is not None and original.enabled:
            cutoff = now - timedelta(days=original.max_age_days)
            deleted_docs = [str(d) for (d,) in s.execute(select(Document.id).where(Document.created_at < cutoff))]
    for d in deleted_docs:
        import uuid

        try:
            storage.delete_document(uuid.UUID(d))
            audit.record("document.expired", "success", document_id=uuid.UUID(d), operation="expire",
                         details={"policy": "original"})
        except Exception as exc:  # busy documents are retried at the next sweep
            log.info("Skipping expiry of %s: %s", d, type(exc).__name__)
    temp_removed = cleanup_temp()
    return {"expired_versions": expired_versions, "expired_documents": len(deleted_docs),
            "temp_entries_removed": temp_removed}


class Sweeper:
    def __init__(self, interval_s: int = 6 * 3600) -> None:
        self.interval = interval_s
        self._stop = threading.Event()
        self._t: threading.Thread | None = None

    def start(self) -> None:
        self._t = threading.Thread(target=self._run, name="pdfws-sweeper", daemon=True)
        self._t.start()

    def _run(self) -> None:
        while not self._stop.wait(self.interval):
            try:
                sweep()
            except Exception:
                log.exception("Expiration sweep failed")

    def stop(self) -> None:
        self._stop.set()
