"""FastAPI application factory."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api import documents, epub, jobs, ocr, system
from app.core.config import get_settings
from app.core.errors import install_error_handlers
from app.core.security import LocalSecurityMiddleware, load_or_create_token
from app.services import handlers  # noqa: F401  (registers job handlers)
from app.services.jobs import cleanup_temp, manager, recover_interrupted
from app.services.lifecycle import Sweeper, sweep
from app.services.storage import recover_orphans

log = logging.getLogger("pdfws")


def create_app(*, token: str | None = None) -> FastAPI:
    s = get_settings()
    s.ensure_dirs()
    tok = token or load_or_create_token(s.token_file)
    sweeper = Sweeper()

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        interrupted = recover_interrupted()
        orphans = recover_orphans()
        removed = cleanup_temp()
        log.info("Startup recovery: %s interrupted job(s), %s, %s temp entr(ies) removed", interrupted, orphans,
                 removed)
        try:
            sweep()
        except Exception:
            log.exception("Initial expiration sweep failed")
        if s.start_workers:
            manager().start(s.max_concurrent_jobs)
            sweeper.start()
        yield
        sweeper.stop()
        manager().stop()

    app = FastAPI(title="PDF Workstation (local)", version=system.APP_VERSION, lifespan=lifespan,
                  docs_url="/api/docs", openapi_url="/api/openapi.json", redoc_url=None)
    app.add_middleware(LocalSecurityMiddleware, token=tok)
    install_error_handlers(app)
    for r in (system.router, documents.router, jobs.router, ocr.router, epub.router):
        app.include_router(r)
    return app


def app_factory() -> FastAPI:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    return create_app()
