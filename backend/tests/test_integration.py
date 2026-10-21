"""Integration tests through the HTTP API with a real PostgreSQL database."""

from __future__ import annotations

import json
import uuid

import pytest
from sqlalchemy import create_engine, select, text

from app.core.files import sha256_file
from app.core.tools import tool_available
from tests.conftest import APP_URL, submit, upload


def job(client, jid):  # type: ignore[no-untyped-def]
    return client.get(f"/api/jobs/{jid}").json()


def events(client, **filters) -> list[dict]:  # type: ignore[no-untyped-def]
    q = "&".join(f"{k}={v}" for k, v in filters.items())
    return client.get(f"/api/audit?limit=1000&{q}").json()["items"]


def test_versioning_keeps_original_immutable_and_links_outputs(client, fx, run_jobs):
    from app.core.config import get_settings

    doc = upload(client, fx["multipage"])
    v1 = doc["latest_version"]
    orig = get_settings().documents_dir / doc["id"] / "original.pdf"
    before = sha256_file(orig)
    assert before == v1["sha256"] and v1["is_original"]
    j1 = submit(client, "compress", [v1["id"]], {"preset": "lossless"})
    j2 = submit(client, "split", [v1["id"]], {"mode": "ranges", "ranges": "1-2,3"})
    run_jobs()
    r1, r2 = job(client, j1["id"]), job(client, j2["id"])
    assert r1["status"] == r2["status"] == "succeeded"
    assert sha256_file(orig) == before  # original never modified
    detail = client.get(f"/api/documents/{doc['id']}").json()
    nums = [v["version_number"] for v in detail["versions"]]
    assert nums == [1, 2, 3, 4]
    for v in detail["versions"][1:]:
        assert v["parent_version_id"] == v1["id"] and not v["is_original"]
        stored = get_settings().documents_dir / v["document_id"] / "versions"
        assert any(sha256_file(p) == v["sha256"] for p in stored.iterdir())
    # Job inputs record the exact input version and hash.
    assert r1["inputs"] == [{"position": 0, "version_id": v1["id"], "sha256": v1["sha256"]}]
    out = r1["outputs"][0]
    ev = [e for e in events(client, document_id=doc["id"]) if e["event_type"] == "output.generated"
          and e["job_id"] == j1["id"]]
    assert ev and ev[0]["output_sha256"] == out["sha256"] and ev[0]["input_sha256"] == v1["sha256"]
    dl = client.get(f"/api/versions/{out['version_id']}/download")
    assert dl.status_code == 200 and dl.headers["x-content-sha256"] == out["sha256"]
    assert any(e["event_type"] == "output.downloaded" and e["delivery_outcome"] == "delivered"
               for e in events(client, document_id=doc["id"]))


def test_invalid_range_rejected_at_submission(client, fx):
    v = upload(client, fx["text"])["latest_version"]["id"]
    r = client.post("/api/jobs", json={"kind": "split", "version_ids": [v], "params": {"ranges": "7-9"}})
    assert r.status_code == 422 and r.json()["error"]["code"] == "invalid_range"
    r = client.post("/api/jobs", json={"kind": "split", "version_ids": [v], "params": {"mode": "nope"}})
    assert r.status_code == 422


def test_failed_jobs_register_no_outputs_and_are_audited(client, fx, run_jobs):
    doc = upload(client, fx["text"])
    vid = doc["latest_version"]["id"]
    j = submit(client, "redact", [vid], {"terms": ["no-such-term-anywhere"]})
    run_jobs()
    r = job(client, j["id"])
    assert r["status"] == "failed" and r["outputs"] == []
    assert r["params"]["terms"] == "[1 item(s)]"  # secret terms never persisted
    detail = client.get(f"/api/documents/{doc['id']}").json()
    assert len(detail["versions"]) == 1
    evs = [e for e in events(client) if e["job_id"] == j["id"]]
    assert {"job.queued", "job.started", "job.failed"} <= {e["event_type"] for e in evs}
    assert not any("no-such-term" in json.dumps(e) for e in evs)
    ok = submit(client, "redact", [vid], {"terms": ["SWORDFISH-2"]})
    run_jobs()
    assert {e["event_type"] for e in events(client) if e["job_id"] == ok["id"]} >= {"job.succeeded", "output.generated"}


def test_signed_form_requires_confirmation(client, tmp, run_jobs):
    import pikepdf

    from tests.fixtures import text_pdf

    src = text_pdf(tmp / "f.pdf", pages=1)
    with pikepdf.open(src, allow_overwriting_input=True) as pdf:
        sig = pdf.make_indirect(pikepdf.Dictionary(FT=pikepdf.Name.Sig, T=pikepdf.String("Sig1")))
        pdf.Root.AcroForm = pikepdf.Dictionary(Fields=pikepdf.Array([sig]))
        pdf.save(src)
    v = upload(client, src)["latest_version"]
    assert v["metadata"]["has_signatures"]
    r = client.post("/api/jobs", json={"kind": "compress", "version_ids": [v["id"]], "params": {}})
    assert r.status_code == 409 and r.json()["error"]["code"] == "confirmation_required"
    assert submit(client, "compress", [v["id"]], {}, acknowledge_warnings=True)["status"] == "queued"
    run_jobs()


def test_rejects_disguised_and_malformed_uploads(client, fx):
    r = client.post("/api/documents", params={"filename": "x.pdf"}, content=b"MZ" + b"\0" * 64)
    assert r.status_code == 415
    r = client.post("/api/documents", params={"filename": "bad.pdf"}, content=fx["malformed"].read_bytes())
    assert r.status_code == 422
    r = client.post("/api/documents", params={"filename": "../../etc/passwd.pdf"}, content=fx["text"].read_bytes())
    assert r.status_code == 201 and r.json()["original_filename"] == "passwd.pdf"


def test_audit_log_is_append_only_and_chained(client, database):
    eng = create_engine(APP_URL)
    with eng.connect() as c:
        with pytest.raises(Exception, match="permission denied|append-only"):
            c.execute(text("DELETE FROM audit_events"))
        c.rollback()
        with pytest.raises(Exception, match="permission denied|append-only"):
            c.execute(text("UPDATE audit_events SET status='x'"))
        c.rollback()
    eng.dispose()
    assert client.get("/api/audit/verify").json()["intact"] is True
    csv_ = client.get("/api/audit/export?format=csv")
    assert csv_.status_code == 200 and csv_.text.startswith("seq,ts,event_type")
    assert json.loads(client.get("/api/audit/export?format=json").text)


def test_deletion_removes_files_but_keeps_audit_hashes(client, fx, run_jobs):
    from app.core.config import get_settings

    doc = upload(client, fx["text"])
    other = upload(client, fx["text"])
    submit(client, "compress", [doc["latest_version"]["id"]], {})
    run_jobs()
    d = get_settings().documents_dir / doc["id"]
    assert d.exists()
    assert client.delete(f"/api/documents/{doc['id']}").status_code == 400  # needs confirmation
    r = client.delete(f"/api/documents/{doc['id']}?confirm=true")
    assert r.status_code == 200 and r.json()["versions_removed"] == 2
    assert not d.exists()
    assert (get_settings().documents_dir / other["id"]).exists()  # unrelated documents untouched
    assert client.get(f"/api/documents/{doc['id']}").status_code == 404
    ev = [e for e in events(client, document_id=doc["id"]) if e["event_type"] == "document.deleted"]
    assert ev and doc["latest_version"]["sha256"] in ev[0]["input_sha256"]


def test_backup_verify_detects_tampering_and_restore_roundtrip(client, fx, run_jobs):
    from app.core.config import get_settings
    from app.services.backup import restore_backup, verify_backup_dir

    keep = upload(client, fx["text_image"])
    j = client.post("/api/backups").json()
    run_jobs()
    res = job(client, j["job_id"])
    assert res["status"] == "succeeded" and res["result"]["backup"]["verified"]
    bdir = get_settings().backups_dir / res["result"]["backup"]["directory"]
    manifest = json.loads((bdir / "manifest.json").read_text())
    assert manifest["files"] and all(len(f["sha256"]) == 64 for f in manifest["files"])
    assert verify_backup_dir(bdir)["ok"]

    # Changes after the backup...
    later = upload(client, fx["multipage"])
    # ...tampering is detected and restore refuses an unverifiable backup.
    victim = bdir / manifest["files"][0]["path"]
    original_bytes = victim.read_bytes()
    victim.chmod(0o600)
    victim.write_bytes(original_bytes + b"x")
    assert not verify_backup_dir(bdir)["ok"]
    with pytest.raises(Exception, match="verification"):
        restore_backup(bdir, confirm="RESTORE", safety_backup=False)
    victim.write_bytes(original_bytes)
    with pytest.raises(Exception, match="RESTORE"):
        restore_backup(bdir, confirm="yes", safety_backup=False)

    out = restore_backup(bdir, confirm="RESTORE", safety_backup=True)
    assert out["integrity"]["ok"] and out["safety_backup"]["verified"]
    assert client.get(f"/api/documents/{keep['id']}").status_code == 200
    assert client.get(f"/api/documents/{later['id']}").status_code == 404  # state as of the backup
    assert client.get("/api/audit/verify").json()["intact"] is True
    assert client.post("/api/storage/verify").json()["ok"] is True


def test_interrupted_jobs_are_recovered_and_resumable(client, fx, run_jobs):
    from app.db.models import Job
    from app.db.session import session_scope
    from app.services.jobs import recover_interrupted

    v = upload(client, fx["text"])["latest_version"]["id"]
    j = submit(client, "unlock", [v], {"password": "pw"}, idempotency_key=f"k-{uuid.uuid4()}")
    with session_scope() as s:  # simulate a crash while running
        s.get(Job, uuid.UUID(j["id"])).status = "running"
    assert recover_interrupted() >= 1
    assert job(client, j["id"])["status"] == "interrupted"
    from app.services.jobs import manager

    manager()._secrets.pop(uuid.UUID(j["id"]), None)  # simulate process restart: secrets are gone
    r = client.post(f"/api/jobs/{j['id']}/resume", json={"secrets": {}})
    assert r.status_code == 400 and r.json()["error"]["code"] == "secrets_required"
    r = client.post(f"/api/jobs/{j['id']}/resume", json={"secrets": {"password": "pw"}})
    assert r.status_code == 200
    run_jobs()
    assert job(client, j["id"])["status"] == "failed"  # not encrypted -> clean, reported failure


def test_idempotency_and_cancellation(client, fx, run_jobs):
    v = upload(client, fx["text"])["latest_version"]["id"]
    key = f"idem-{uuid.uuid4()}"
    a = submit(client, "compress", [v], {}, idempotency_key=key)
    b = submit(client, "compress", [v], {}, idempotency_key=key)
    assert a["id"] == b["id"]
    assert client.post(f"/api/jobs/{a['id']}/cancel").json()["status"] == "cancelled"
    run_jobs()
    assert job(client, a["id"])["status"] == "cancelled"


def test_batch_and_ocr_review_api(client, fx, run_jobs):
    docs = [upload(client, fx["text"])["latest_version"]["id"] for _ in range(2)]
    r = client.post("/api/batches", json={"kind": "watermark", "version_ids": docs, "params": {"text": "LOTE"}})
    assert r.status_code == 202 and len(r.json()["jobs"]) == 2
    run_jobs()
    b = client.get(f"/api/batches/{r.json()['batch_id']}").json()
    assert b["counts"] == {"succeeded": 2}


@pytest.mark.skipif(not tool_available("libreoffice"), reason="LibreOffice is not installed")
def test_office_conversion_through_api(client, fx, run_jobs):
    doc = upload(client, fx["docx"])
    assert doc["kind"] == "office"
    j = submit(client, "office_to_pdf", [doc["latest_version"]["id"]])
    run_jobs()
    r = job(client, j["id"])
    assert r["status"] == "succeeded", r["error"]
    assert r["outputs"][0]["document_id"] == doc["id"]


def test_expiration_policy_keeps_originals(client, fx, run_jobs):
    from datetime import UTC, datetime, timedelta

    from app.db.models import DocumentVersion
    from app.db.session import session_scope
    from app.services.lifecycle import sweep

    doc = upload(client, fx["text"])
    v1 = doc["latest_version"]["id"]
    submit(client, "compress", [v1], {})
    submit(client, "rotate", [v1], {"angle": 90})
    run_jobs()
    assert client.put("/api/storage/policies/derived", json={"enabled": True, "max_age_days": 1,
                                                             "keep_latest": True}).status_code == 200
    res = sweep(now=datetime.now(UTC) + timedelta(days=3))
    assert res["expired_versions"] >= 1
    with session_scope() as s:
        rows = s.execute(select(DocumentVersion).where(DocumentVersion.document_id == uuid.UUID(doc["id"]))
                         .order_by(DocumentVersion.version_number)).scalars().all()
        assert rows[0].is_original and rows[0].expired_at is None  # originals never expire by default
        assert rows[1].expired_at is not None and rows[2].expired_at is None  # latest kept
    client.put("/api/storage/policies/derived", json={"enabled": False, "max_age_days": 180, "keep_latest": True})
