"""Structured application errors. Messages are user-safe: no paths, secrets or stack traces."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

log = logging.getLogger("pdfws.errors")


class AppError(Exception):
    status_code = 400
    code = "bad_request"

    def __init__(self, message: str, *, code: str | None = None, status_code: int | None = None,
                 details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.message = message
        if code:
            self.code = code
        if status_code:
            self.status_code = status_code
        self.details = details or {}


class NotFound(AppError):
    status_code = 404
    code = "not_found"


class Conflict(AppError):
    status_code = 409
    code = "conflict"


class Unsupported(AppError):
    status_code = 415
    code = "unsupported_media"


class TooLarge(AppError):
    status_code = 413
    code = "too_large"


class ToolUnavailable(AppError):
    status_code = 503
    code = "tool_unavailable"


class ProcessingError(AppError):
    status_code = 422
    code = "processing_failed"


class PasswordRequired(AppError):
    status_code = 423
    code = "password_required"


class Busy(AppError):
    status_code = 429
    code = "queue_full"


def _body(code: str, message: str, details: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"error": {"code": code, "message": message, "details": details or {}}}


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _app_error(_: Request, exc: AppError) -> JSONResponse:
        return JSONResponse(_body(exc.code, exc.message, exc.details), status_code=exc.status_code)

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError) -> JSONResponse:
        issues = [
            {"loc": [str(p) for p in e.get("loc", [])], "msg": str(e.get("msg", ""))}
            for e in exc.errors()
        ]
        return JSONResponse(_body("invalid_request", "The request is invalid.", {"issues": issues}),
                            status_code=422)

    from sqlalchemy.exc import OperationalError

    @app.exception_handler(OperationalError)
    async def _db_down(_: Request, exc: OperationalError) -> JSONResponse:
        log.error("Database unavailable: %s", type(exc.orig).__name__ if exc.orig else "OperationalError")
        return JSONResponse(_body("database_unavailable", "The local PostgreSQL database is not reachable. "
                                  "Start it with ./scripts/db.sh start (or ./scripts/dev.sh)."), status_code=503)

    @app.exception_handler(Exception)
    async def _unhandled(_: Request, exc: Exception) -> JSONResponse:
        log.exception("Unhandled error: %s", type(exc).__name__)
        return JSONResponse(_body("internal_error", "An internal error occurred. See the backend log."),
                            status_code=500)
