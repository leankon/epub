"""Append-only audit log API. Never pass document contents, passwords or OCR text in `details`."""

from __future__ import annotations

import csv
import io
import json
import logging
import uuid
from collections.abc import Iterator
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.db.models import AuditEvent
from app.db.session import session_scope

log = logging.getLogger("pdfws.audit")

SENSITIVE_KEYS = {"password", "user_password", "owner_password", "token", "text", "content", "search_terms",
                  "terms", "corrected_text"}


def scrub(details: dict[str, Any] | None) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, v in (details or {}).items():
        if k.lower() in SENSITIVE_KEYS:
            out[k] = "[redacted]"
        elif isinstance(v, dict):
            out[k] = scrub(v)
        else:
            out[k] = v
    return out


def record(event_type: str, status: str, *, session: Session | None = None,
           document_id: uuid.UUID | None = None, version_id: uuid.UUID | None = None,
           job_id: uuid.UUID | None = None, operation: str | None = None,
           input_sha256: str | list[str] | None = None, output_sha256: str | list[str] | None = None,
           delivery_outcome: str | None = None, details: dict[str, Any] | None = None) -> None:
    """Append an event. When `session` is given it joins that transaction (atomic with the change)."""

    def _join(v: str | list[str] | None) -> str | None:
        return ",".join(v) if isinstance(v, list) else v

    ev = AuditEvent(event_type=event_type, status=status, document_id=document_id, version_id=version_id,
                    job_id=job_id, operation=operation, input_sha256=_join(input_sha256),
                    output_sha256=_join(output_sha256), delivery_outcome=delivery_outcome,
                    details=json.loads(json.dumps(scrub(details), default=str)))
    if session is not None:
        session.add(ev)
        return
    try:
        with session_scope() as s:
            s.add(ev)
    except Exception:  # audit failure must be visible in logs but never leak details
        log.exception("Failed to append audit event %s", event_type)


def verify_chain(session: Session) -> dict[str, Any]:
    row = session.execute(text("SELECT checked, first_bad_seq FROM pdfws_audit_verify()")).one()
    return {"checked": int(row.checked), "intact": row.first_bad_seq is None,
            "first_bad_seq": row.first_bad_seq}


EXPORT_FIELDS = ["seq", "ts", "event_type", "status", "document_id", "version_id", "job_id", "operation",
                 "input_sha256", "output_sha256", "delivery_outcome", "details", "prev_hash", "event_hash"]


def _row(e: AuditEvent) -> dict[str, Any]:
    return {f: (getattr(e, f).isoformat() if f == "ts" else
                str(getattr(e, f)) if isinstance(getattr(e, f), uuid.UUID) else getattr(e, f))
            for f in EXPORT_FIELDS}


def iter_events(session: Session, *, document_id: uuid.UUID | None = None) -> Iterator[AuditEvent]:
    q = select(AuditEvent).order_by(AuditEvent.seq)
    if document_id:
        q = q.where(AuditEvent.document_id == document_id)
    yield from session.execute(q.execution_options(yield_per=500)).scalars()


def export_json(session: Session, document_id: uuid.UUID | None = None) -> Iterator[str]:
    yield "[\n"
    first = True
    for e in iter_events(session, document_id=document_id):
        yield ("" if first else ",\n") + json.dumps(_row(e), ensure_ascii=False)
        first = False
    yield "\n]\n"


def export_csv(session: Session, document_id: uuid.UUID | None = None) -> Iterator[str]:
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=EXPORT_FIELDS)
    w.writeheader()
    yield buf.getvalue()
    for e in iter_events(session, document_id=document_id):
        buf.seek(0)
        buf.truncate()
        r = _row(e)
        r["details"] = json.dumps(r["details"], ensure_ascii=False)
        w.writerow(r)
        yield buf.getvalue()
