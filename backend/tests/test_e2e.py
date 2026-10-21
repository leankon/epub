"""Required end-to-end happy path: scanned PDF -> OCR/layout -> review/approve -> hybrid EPUB."""

from __future__ import annotations

import io
import re
import zipfile

import pytest
from lxml import etree

from app.core.config import get_settings
from app.core.files import sha256_file
from app.core.tools import tool_available
from app.services.epub.validate import validate_epub
from tests.conftest import upload

pytestmark = [pytest.mark.e2e,
              pytest.mark.skipif(not tool_available("tesseract"), reason="Tesseract (spa+eng) is not installed")]


def test_scanned_pdf_to_hybrid_epub_happy_path(client, fx, run_jobs, tmp):
    # 1-2. Synthetic PDF: image of printed text plus a separate illustration, imported via the API.
    doc = upload(client, fx["scanned"], "libro-escaneado.pdf")
    v = doc["latest_version"]
    original = get_settings().documents_dir / doc["id"] / "original.pdf"
    original_hash = sha256_file(original)

    # 3. OCR + layout pipeline.
    r = client.post("/api/ocr/sessions", json={"version_id": v["id"]})
    assert r.status_code == 202, r.text
    sid = r.json()["session"]["id"]
    run_jobs()
    sess = client.get(f"/api/ocr/sessions/{sid}").json()
    assert sess["status"] == "review" and sess["pages"][0]["mode"] == "ocr"

    # Review: correct one region, then approve.
    page = client.get(f"/api/ocr/sessions/{sid}/pages/0").json()
    para = next(x for x in page["regions"] if x["role"] == "paragraph")
    fixed = para["text"] + " Revisado."
    u = client.put(f"/api/ocr/sessions/{sid}/pages/0/regions",
                   json={"regions": [{"id": para["id"], "corrected_text": fixed}], "mark_reviewed": True})
    assert u.json()["changed"] == 1
    blocked = client.post("/api/epub/convert", json={"version_id": v["id"], "session_id": sid})
    assert blocked.status_code == 409  # approval required
    assert client.post(f"/api/ocr/sessions/{sid}/approve").json()["status"] == "approved"

    # 4. Hybrid EPUB.
    r = client.post("/api/epub/convert", json={"version_id": v["id"], "session_id": sid, "strategy": "hybrid",
                                               "metadata": {"title": "El faro", "author": "Prueba", "language": "es"}})
    assert r.status_code == 202, r.text
    run_jobs()
    job = client.get(f"/api/jobs/{r.json()['job_id']}").json()
    assert job["status"] == "succeeded", job["error"]
    out = job["outputs"][0]
    report = job["result"]["report"]
    assert report["images_retained"] == 1 and report["ocr_pages"] == 1 and report["reviewed"] is True

    # 5. Download and open as an archive.
    dl = client.get(f"/api/versions/{out['version_id']}/download")
    assert dl.status_code == 200
    z = zipfile.ZipFile(io.BytesIO(dl.content))
    names = z.namelist()
    assert names[0] == "mimetype"
    xhtml = [n for n in names if n.endswith(".xhtml") and "nav" not in n]
    body_text = " ".join(" ".join(etree.fromstring(z.read(n)).itertext()) for n in xhtml)

    # 6. Recognised text (including the correction) is real XHTML text.
    for phrase in ("El viejo faro se alzaba", "señora Núñez", "Revisado.", "The lighthouse keeper"):
        assert phrase in re.sub(r"\s+", " ", body_text), phrase

    # 7. The illustration is included and referenced.
    images = [n for n in names if n.startswith("EPUB/images/")]
    assert len(images) == 1
    assert any(f"../{images[0][5:]}" in z.read(n).decode() for n in xhtml)

    # 8. Structure passes the available validation checks (EPUBCheck too when installed).
    path = tmp / "out.epub"
    path.write_bytes(dl.content)
    rep = validate_epub(path, expect_text=True, expected_images=1)
    assert rep.passed, rep.errors
    if tool_available("epubcheck"):
        assert report["validation"]["epubcheck"]["executed"] and report["validation"]["epubcheck"]["errors"] == 0

    # 9. Original unchanged.
    assert sha256_file(original) == original_hash == v["sha256"]

    # 10. Output hash and audit events recorded.
    assert out["sha256"] == __import__("hashlib").sha256(dl.content).hexdigest()
    evs = client.get(f"/api/audit?document_id={doc['id']}&limit=500").json()["items"]
    kinds = {e["event_type"] for e in evs}
    assert {"document.imported", "ocr.completed", "ocr.corrected", "ocr.approved", "epub.validated",
            "output.generated", "output.downloaded"} <= kinds
    gen = next(e for e in evs if e["event_type"] == "output.generated" and e["job_id"] == job["id"])
    assert gen["output_sha256"] == out["sha256"] and gen["input_sha256"] == original_hash
    assert client.get("/api/audit/verify").json()["intact"]
