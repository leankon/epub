"""Security unit tests: filenames, paths, archives, sniffing, request protection."""

from __future__ import annotations

import zipfile

import pytest

from app.core.errors import AppError, Unsupported
from app.core.files import ensure_within, safe_display_name, safe_extract_zip, sniff
from tests.conftest import TOKEN


@pytest.mark.parametrize("name", ["../../etc/passwd", "..\\..\\boot.ini", "/abs/x.pdf", "a\x00b.pdf", "CON:<>.pdf"])
def test_unsafe_filenames_are_sanitised(name):
    clean = safe_display_name(name)
    assert "/" not in clean and "\\" not in clean and "\x00" not in clean and not clean.startswith(".")


def test_paths_cannot_escape_storage(tmp):
    base = tmp / "docs"
    base.mkdir()
    assert ensure_within(base, base / "a" / "b") == (base / "a" / "b").resolve()
    for bad in (base / ".." / "x", base / "a" / ".." / ".." / "y"):
        with pytest.raises(AppError):
            ensure_within(base, bad)
    (tmp / "outside").mkdir()
    (base / "link").symlink_to(tmp / "outside")
    with pytest.raises(AppError):
        ensure_within(base, base / "link" / "f")


@pytest.mark.parametrize("member", ["../evil.txt", "/abs.txt", "a/../../evil.txt", "C:/win.txt"])
def test_archive_extraction_rejects_traversal(tmp, member):
    z = tmp / "a.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr(member, "x")
    with pytest.raises(AppError):
        safe_extract_zip(z, tmp / "out")
    assert not (tmp / "evil.txt").exists()


def test_file_signature_beats_extension(tmp, fx):
    exe = tmp / "invoice.pdf"
    exe.write_bytes(b"MZ\x90\x00" + b"\x00" * 100)
    with pytest.raises(Unsupported):
        sniff(exe, "invoice.pdf")
    renamed = tmp / "photo.jpg"
    renamed.write_bytes(fx["text"].read_bytes())
    assert sniff(renamed, "photo.jpg").kind == "pdf"
    assert sniff(fx["epub"], "book.zip").kind == "epub"
    assert sniff(fx["docx"], "x.bin").ext == "docx"


def test_api_requires_token_host_and_same_origin(client):
    from fastapi.testclient import TestClient

    from app.main import create_app

    assert client.get("/api/health").status_code == 200
    bare = TestClient(create_app(token=TOKEN))
    assert bare.get("/api/documents").status_code == 401
    assert bare.get("/api/documents", headers={"x-pdfws-token": "wrong" * 10}).status_code == 401
    assert client.get("/api/documents", headers={"host": "evil.example"}).status_code == 421
    r = client.post("/api/jobs", json={}, headers={"origin": "https://evil.example"})
    assert r.status_code == 403
    assert client.get("/api/documents", headers={"sec-fetch-site": "cross-site"}).status_code == 403
    assert "access-control-allow-origin" not in client.get("/api/documents").headers


def test_errors_do_not_leak_internals(client):
    r = client.get("/api/versions/00000000-0000-0000-0000-000000000000/download")
    assert r.status_code == 404
    body = r.text
    assert "Traceback" not in body and "/home" not in body and "data/documents" not in body


def test_config_refuses_network_binding_without_opt_in(monkeypatch):
    from app.core.config import Settings

    monkeypatch.setenv("PDFWS_HOST", "0.0.0.0")
    monkeypatch.setenv("PDFWS_ALLOW_NETWORK", "false")
    with pytest.raises(ValueError):
        Settings()  # type: ignore[call-arg]
    monkeypatch.setenv("PDFWS_ALLOW_NETWORK", "true")
    assert Settings().host == "0.0.0.0"  # type: ignore[call-arg]
