"""Unit tests for PDF transformations (no database)."""

from __future__ import annotations

import zipfile
from pathlib import Path

import pikepdf
import pytest

from app.core.errors import AppError, PasswordRequired, ProcessingError
from app.services import pdf_ops, redact
from app.services.inspect import inspect_pdf, open_pdf
from tests.fixtures import illustration, image_pdf, text_pdf


def pages(p: Path) -> int:
    with pikepdf.open(p) as pdf:
        return len(pdf.pages)


def test_merge_preserves_page_count_and_adds_bookmarks(fx, tmp):
    out = tmp / "m.pdf"
    pdf_ops.merge([(fx["text"], "Text"), (fx["multipage"], "Multi"), (fx["rotated"], "Rotated")], out)
    assert pages(out) == pages(fx["text"]) + pages(fx["multipage"]) + pages(fx["rotated"])
    outline = pdf_ops.get_outline(out)
    assert [o["title"] for o in outline] == ["Text", "Multi", "Rotated"]
    assert outline[1]["page"] == 3
    with pikepdf.open(out) as pdf:  # rotation of the source page is preserved
        assert int(pdf.pages[3 + 6 + 1].obj.get("/Rotate", 0)) == 90


def test_split_produces_requested_pages(fx, tmp):
    groups = pdf_ops.parse_page_ranges("1-2, 4, 6-", 6)
    assert groups == [[0, 1], [3], [5]]
    for k, g in enumerate(groups):
        out = tmp / f"s{k}.pdf"
        pdf_ops.extract_pages(fx["multipage"], g, out)
        assert pages(out) == len(g)
        text = redact.extract_all_text(out)
        for i in g:
            assert f"Page {i + 1} body text" in text


def test_split_by_bookmarks(fx):
    groups = pdf_ops.bookmark_groups(fx["multipage"])
    assert [g[1] for g in groups] == [[i] for i in range(6)]


@pytest.mark.parametrize("spec", ["", "0", "7", "3-2", "a", "1,,2", "1-2-3"])
def test_invalid_page_ranges_are_rejected(spec):
    with pytest.raises(AppError):
        pdf_ops.parse_page_ranges(spec, 6)


def test_reorder_changes_page_order(fx, tmp):
    out = tmp / "o.pdf"
    pdf_ops.organize(fx["multipage"], [{"page": 5}, {"page": 0}, {"page": 2}], out)
    from pdfminer.high_level import extract_text

    firsts = [extract_text(str(out), page_numbers=[i]).split("Page ")[1][:1] for i in range(3)]
    assert firsts == ["6", "1", "3"]


def test_rotation_changes_orientation(fx, tmp):
    out = tmp / "r.pdf"
    pdf_ops.organize(fx["text"], [{"page": 0, "rotate": 90}, {"page": 1, "rotate": 270}, {"page": 2, "rotate": 180}], out)
    with pikepdf.open(out) as pdf:
        assert [int(p.obj.get("/Rotate", 0)) for p in pdf.pages] == [90, 270, 180]
    with pytest.raises(AppError):
        pdf_ops.organize(fx["text"], [{"page": 0, "rotate": 45}], tmp / "bad.pdf")


def test_compression_creates_valid_output_and_reports_sizes(tmp):
    big = illustration(2400, 1600)
    src = image_pdf([big], tmp / "img.pdf", dpi=600)  # image displayed small -> high effective DPI
    out = tmp / "c.pdf"
    stats = pdf_ops.compress(src, out, "strong")
    assert stats["input_bytes"] == src.stat().st_size and stats["output_bytes"] == out.stat().st_size
    assert stats["output_bytes"] < stats["input_bytes"]
    assert stats["lossy"] is True and stats["images_downsampled"] == 1
    with pikepdf.open(out) as pdf:
        assert len(pdf.pages) == 1
        img = next(iter(pdf.pages[0].get_images().values()))
        assert int(img.Width) < 2400
    lossless = pdf_ops.compress(src, tmp / "l.pdf", "lossless")
    assert lossless["lossy"] is False and lossless["images_recompressed"] == 0


def test_watermark_adds_visible_text(fx, tmp):
    out = tmp / "w.pdf"
    pdf_ops.watermark(fx["text"], out, {"text": "BORRADOR Ñ", "pages": "1,3"})
    from pdfminer.high_level import extract_text

    assert "BORRADOR" in extract_text(str(out), page_numbers=[0])
    assert "BORRADOR" not in extract_text(str(out), page_numbers=[1])
    assert "BORRADOR" in extract_text(str(out), page_numbers=[2])


def test_page_numbers_on_rotated_page(fx, tmp):
    out = tmp / "n.pdf"
    pdf_ops.page_numbers(fx["rotated"], out, {"format": "Página {n} de {total}"})
    text = redact.extract_all_text(out)
    assert "Página 1 de 2" in text and "Página 2 de 2" in text


def test_redaction_removes_terms_from_all_supported_extraction_paths(fx, tmp):
    out = tmp / "red.pdf"
    res = redact.redact(fx["text"], out, terms=["SWORDFISH-1", "maria.lopez@example.com"])
    assert res["verification"]["passed"]
    text = redact.extract_all_text(out)
    assert "SWORDFISH-1" not in text and "maria.lopez" not in text
    assert "SWORDFISH-0" in text and "SWORDFISH-2" in text  # untargeted content survives
    assert "Contact" in text and "for details." in text
    with pikepdf.open(out) as pdf:  # raw content streams no longer contain the glyphs
        raw = b"".join(pikepdf.unparse_content_stream(pikepdf.parse_content_stream(p)) for p in pdf.pages)
        assert b"SWORDFISH-1" not in raw and b"lopez" not in raw
        assert "/Info" not in pdf.trailer  # metadata sanitised by default
    assert out.read_bytes().count(b"%%EOF") == 1  # no incremental history


def test_redaction_by_area_removes_image_pixels_and_text(tmp):
    from reportlab.lib.utils import ImageReader
    from reportlab.pdfgen import canvas

    src = tmp / "mixed.pdf"
    c = canvas.Canvas(str(src), pagesize=(600, 800))
    c.drawImage(ImageReader(illustration(400, 300)), 100, 400, 400, 300)
    c.setFont("Helvetica", 12)
    c.drawString(100, 350, "Account number 1234-5678 belongs to someone")
    c.save()
    out = tmp / "r.pdf"
    res = redact.redact(src, out, areas=[{"page": 0, "rect": [90, 340, 300, 362]}, {"page": 0, "rect": [100, 400, 300, 700]}])
    assert res["stats"]["images_modified"] == 1
    text = redact.extract_all_text(out)
    assert "1234-5678" not in text and "Account" not in text
    with pikepdf.open(out) as pdf:
        img = next(iter(pdf.pages[0].get_images().values()))
        pil = pikepdf.PdfImage(img).as_pil_image().convert("L")
        assert pil.getpixel((10, 150)) < 10  # left half of the picture is now black pixels
        assert pil.getpixel((390, 150)) > 50  # right half is untouched


def test_redaction_with_nothing_found_fails(fx, tmp):
    with pytest.raises(AppError):
        redact.redact(fx["text"], tmp / "x.pdf", terms=["not-present-anywhere"])


def test_metadata_and_bookmarks(fx, tmp):
    out = tmp / "md.pdf"
    pdf_ops.set_metadata(fx["text"], out, {"title": "Título nuevo", "author": "Ana", "language": "es"})
    info = inspect_pdf(out)
    assert info["title"] == "Título nuevo" and info["author"] == "Ana" and info["language"] == "es"
    out2 = tmp / "bm.pdf"
    pdf_ops.set_outline(out, out2, [{"title": "Uno", "page": 0, "children": [{"title": "Dos", "page": 2, "children": []}]}])
    assert pdf_ops.get_outline(out2) == [{"title": "Uno", "page": 0, "children": [{"title": "Dos", "page": 2, "children": []}]}]
    with pytest.raises(AppError):
        pdf_ops.set_outline(out, tmp / "bad.pdf", [{"title": "X", "page": 99, "children": []}])


def test_encrypted_pdf_detection_and_authorised_unlock(fx, tmp):
    info = inspect_pdf(fx["encrypted"])
    assert info["encrypted"] and info["needs_password"]
    with pytest.raises(PasswordRequired):
        open_pdf(fx["encrypted"])
    with pytest.raises(PasswordRequired):  # user password alone does not lift restrictions
        pdf_ops.unlock(fx["encrypted"], tmp / "u.pdf", "s3cret")
    assert pdf_ops.unlock(fx["encrypted"], tmp / "u.pdf", "s3cret-owner")["matched"] == "owner"
    assert not inspect_pdf(tmp / "u.pdf")["encrypted"]
    pdf_ops.protect(tmp / "u.pdf", tmp / "p.pdf", "new", None, {})
    assert inspect_pdf(tmp / "p.pdf")["needs_password"]


def test_malformed_pdf_is_rejected(fx):
    with pytest.raises((ProcessingError, PasswordRequired)):
        open_pdf(fx["malformed"])


def test_images_extract_and_render(fx, tmp):
    res = pdf_ops.extract_images(fx["text_image"], tmp / "imgs.zip")
    assert res["images"] == 1
    with zipfile.ZipFile(tmp / "imgs.zip") as z:
        assert any(n.endswith(".jpg") for n in z.namelist())  # original JPEG, not re-encoded
    r = pdf_ops.pdf_to_images(fx["multipage"], tmp / "pages.zip", dpi=72, pages="1-2")
    assert r["pages"] == 2


def test_images_to_pdf(tmp):
    from PIL import Image

    a = tmp / "a.png"
    Image.new("RGBA", (200, 100), (255, 0, 0, 128)).save(a)
    b = tmp / "b.jpg"
    illustration(300, 200).save(b, quality=90)
    out = tmp / "i.pdf"
    pdf_ops.images_to_pdf([a, b], out, tmp)
    assert pages(out) == 2


def test_text_pdf_helper_has_three_pages(tmp):
    assert pages(text_pdf(tmp / "t.pdf")) == 3
