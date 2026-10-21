"""Request protection for the local API.

* Host header allow-list (blocks DNS-rebinding attacks from malicious web pages).
* Unpredictable per-installation session token required on every API call except /api/health.
  The Next.js server injects it when proxying; browser JavaScript never sees it.
* Origin / Sec-Fetch-Site checks reject cross-site browser requests (CSRF).
* No CORS headers are ever sent, so other origins cannot read responses.
"""

from __future__ import annotations

import hmac
import os
import secrets
from pathlib import Path

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from app.core.config import get_settings

TOKEN_HEADER = "x-pdfws-token"  # noqa: S105 - header name, not a secret
PUBLIC_PATHS = {"/api/health"}


def load_or_create_token(path: Path) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        tok = path.read_text().strip()
        if len(tok) >= 32:
            return tok
    tok = secrets.token_urlsafe(32)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(tok)
    return tok


def _deny(status: int, code: str, msg: str) -> JSONResponse:
    return JSONResponse({"error": {"code": code, "message": msg, "details": {}}}, status_code=status)


class LocalSecurityMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: object, token: str) -> None:
        super().__init__(app)  # type: ignore[arg-type]
        self.token = token
        s = get_settings()
        self.hosts = s.allowed_hosts
        self.origins = s.allowed_origins

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        host = request.headers.get("host", "")
        if host not in self.hosts:
            return _deny(421, "host_not_allowed", "Requests must target the local application address.")
        origin = request.headers.get("origin")
        if origin and origin not in self.origins:
            return _deny(403, "origin_not_allowed", "Cross-origin requests are not allowed.")
        if request.headers.get("sec-fetch-site") == "cross-site":
            return _deny(403, "cross_site", "Cross-site requests are not allowed.")
        path = request.url.path
        if path.startswith("/api/") and path not in PUBLIC_PATHS:
            supplied = request.headers.get(TOKEN_HEADER, "")
            if not supplied or not hmac.compare_digest(supplied, self.token):
                return _deny(401, "unauthorized", "Missing or invalid local session token.")
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault("Cache-Control", "no-store")
        response.headers.setdefault("Content-Security-Policy", "default-src 'none'; img-src 'self' data:; "
                                    "style-src 'unsafe-inline'; frame-ancestors 'self'")
        return response
