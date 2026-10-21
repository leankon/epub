"""Test configuration.

Tests that need PostgreSQL use a dedicated database `<name>_test` on the configured server (created
and migrated per session) plus a temporary data directory, so development data is never touched.
They are skipped - with the reason reported - only if PostgreSQL is genuinely unreachable.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest
from dotenv import dotenv_values

REPO = Path(__file__).resolve().parents[2]
_env = {**dotenv_values(REPO / ".env"), **os.environ}
_TMP = Path(tempfile.mkdtemp(prefix="pdfws-test-"))


def _test_url(url: str) -> str:
    base, _, name = url.rpartition("/")
    return f"{base}/{name.split('?')[0]}_test"


ADMIN_URL = _test_url(_env.get("DATABASE_ADMIN_URL", "postgresql+psycopg://pdfws_owner:x@127.0.0.1:54329/pdfws"))
APP_URL = _test_url(_env.get("DATABASE_URL", "postgresql+psycopg://pdfws_app:x@127.0.0.1:54329/pdfws"))

# Must be set before any app module reads settings (they are cached).
os.environ.update({
    "PDFWS_DATA_DIR": str(_TMP / "data"),
    "DATABASE_URL": APP_URL,
    "DATABASE_ADMIN_URL": ADMIN_URL,
    "PDFWS_START_WORKERS": "false",
    "PDFWS_EXTRA_ALLOWED_HOSTS": "testserver",
    "PDFWS_SESSION_TOKEN_FILE": str(_TMP / "token"),
})

TOKEN = "t" * 48
HEADERS = {"x-pdfws-token": TOKEN}


def _db_problem() -> str | None:
    from sqlalchemy import create_engine, text

    server = ADMIN_URL.rsplit("/", 1)[0] + "/postgres"
    try:
        eng = create_engine(server, isolation_level="AUTOCOMMIT")
        with eng.connect() as c:
            dbname = ADMIN_URL.rsplit("/", 1)[1]
            c.execute(text(f'DROP DATABASE IF EXISTS "{dbname}" WITH (FORCE)'))
            owner = ADMIN_URL.split("//", 1)[1].split(":", 1)[0]
            c.execute(text(f'CREATE DATABASE "{dbname}" OWNER "{owner}"'))
        eng.dispose()
    except Exception as exc:  # noqa: BLE001
        return f"PostgreSQL not reachable for tests ({type(exc).__name__}). Start it with ./scripts/db.sh start"
    from alembic import command
    from alembic.config import Config

    cfg = Config(str(REPO / "backend" / "alembic.ini"))
    cfg.set_main_option("script_location", str(REPO / "backend" / "migrations"))
    cfg.attributes["url"] = ADMIN_URL
    command.upgrade(cfg, "head")
    return None


_DB_PROBLEM: str | None | bool = False


@pytest.fixture(scope="session")
def database() -> Iterator[None]:
    global _DB_PROBLEM
    if _DB_PROBLEM is False:
        _DB_PROBLEM = _db_problem()
    if _DB_PROBLEM:
        pytest.skip(str(_DB_PROBLEM))
    yield


@pytest.fixture(scope="session")
def client(database: None):  # type: ignore[no-untyped-def]
    from fastapi.testclient import TestClient

    from app.main import create_app

    with TestClient(create_app(token=TOKEN), headers=HEADERS) as c:
        yield c


@pytest.fixture
def run_jobs():  # type: ignore[no-untyped-def]
    from app.services.jobs import manager

    return manager().run_pending_sync


@pytest.fixture(scope="session")
def fx() -> dict[str, Path]:
    from tests.fixtures import write_all

    return write_all(_TMP / "fixtures")


@pytest.fixture
def tmp(tmp_path: Path) -> Path:
    return tmp_path


def upload(client, path: Path, name: str | None = None) -> dict:  # type: ignore[no-untyped-def]
    r = client.post("/api/documents", params={"filename": name or path.name}, content=path.read_bytes())
    assert r.status_code == 201, r.text
    return r.json()


def submit(client, kind: str, versions: list[str], params: dict | None = None, **kw) -> dict:  # type: ignore[no-untyped-def]
    r = client.post("/api/jobs", json={"kind": kind, "version_ids": versions, "params": params or {}, **kw})
    assert r.status_code == 202, r.text
    return r.json()


def pytest_sessionfinish(session, exitstatus):  # type: ignore[no-untyped-def]
    shutil.rmtree(_TMP, ignore_errors=True)
