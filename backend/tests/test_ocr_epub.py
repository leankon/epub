"""OCR, layout and EPUB unit tests (local Tesseract; skipped only when it is not installed)."""

from __future__ import annotations

import difflib
import re
import shutil
import zipfile
from pathlib import Path

import pytest

from app.core.tools import tool_available
from app.services.epub.builder import build_epub
from app.services.epub.validate import validate_epub
from app.services.ocr.pipeline import analyze_page
from app.services.redact import extract_all_text
from tests.fixtures import ENGLISH_TEXT, SPANISH_TEXT, image_pdf, scanned_page_image

needs_tesseract = pytest.mark.skipif(not tool_available("tesseract"),
                                     reason="Tesseract with spa+eng language data is not installed")

# Documented tolerance: >= 95% character similarity on clean synthetic scans.
OCR_TOLERANCE = 0.95


def similarity(a: str, b: str) -> float:
    norm = lambda s: re.sub(r"\s+", " ", s).strip()  # noqa: E731
    return difflib.SequenceMatcher(None, norm(a), norm(b)).ratio()


def analyse(pdf: Path, tmp: Path) -> list[dict]:
    from app.services.render import page_count

    out = []
    for i in range(page_count(pdf)):
        r = analyze_page(pdf, i, tmp / "sess", {}, tmp / "work")
        r.update(page_index=i, status="done", reviewed=False, page_asset=r["processed_image"])
        out.append(r)
    return out


def flow_text(page: dict) -> str:
    return " ".join(r["text"] for r in sorted(page["regions"], key=lambda r: r["order"])
                    if r["role"] in ("paragraph", "heading", "caption"))


@needs_tesseract
def test_ocr_recognises_spanish_and_english_within_tolerance(fx, tmp):
    page = analyse(fx["scanned"], tmp)[0]
    text = flow_text(page)
    assert page["mode"] == "ocr" and page["classification"]["kind"] == "scanned"
    es = text[text.index("El viejo"): text.index("océano") + len("océano.")]
    en = text[text.index("The lighthouse"):]
    assert similarity(es, SPANISH_TEXT) >= OCR_TOLERANCE, es
    assert similarity(en[: len(ENGLISH_TEXT)], ENGLISH_TEXT) >= OCR_TOLERANCE, en
    for word in ("señora", "Núñez", "lámpara", "océano"):  # accents and ñ preserved
        assert word in text
    assert page["mean_confidence"] > 80


@needs_tesseract
def test_layout_separates_illustration_and_caption(fx, tmp):
    regions = analyse(fx["scanned"], tmp)[0]["regions"]
    figs = [r for r in regions if r["role"] == "figure"]
    assert len(figs) == 1 and figs[0]["figure"]["kind"] == "photo"
    assert (tmp / "sess" / figs[0]["figure"]["asset"]).exists()
    cap = next(r for r in regions if r["role"] == "caption")
    assert cap["caption_for"] == figs[0]["id"] and cap["text"].startswith("Figura 1")
    headings = [r for r in regions if r["role"] == "heading"]
    assert headings and headings[0]["text"] == "Capítulo 1" and headings[0]["heading_level"] == 1
    assert not any("faro" in (r.get("text") or "") and r["role"] == "figure" for r in regions)


@needs_tesseract
def test_photographed_page_is_corrected_and_recognised(fx, tmp):
    page = analyse(fx["photographed"], tmp)[0]
    assert page["preprocessing"].get("perspective_corrected")
    assert similarity(flow_text(page)[:60], (" ".join(["Capítulo 1", SPANISH_TEXT]))[:60]) > 0.9


def test_two_columns_are_not_interleaved(fx, tmp):
    page = analyse(fx["two_column"], tmp)[0]
    text = flow_text(page)
    assert text.index("Left column line 15") < text.index("Right column line 1 ")
    assert page["mode"] == "text_layer"  # good digital text is not re-OCR'd


@needs_tesseract
def test_searchable_pdf_output_is_selectable_text(tmp):
    from app.services.ocr.searchable import make_searchable

    src = image_pdf([scanned_page_image(300)], tmp / "scan.pdf")
    assert "faro" not in extract_all_text(src)  # image only
    res = make_searchable(src, tmp / "s.pdf", tmp / "w")
    assert res["pages_ocr"] == 1
    text = extract_all_text(tmp / "s.pdf")
    assert "faro" in text and "lighthouse" in text


@needs_tesseract
def test_image_assets_survive_hybrid_conversion(fx, tmp):
    pages = analyse(fx["scanned"], tmp)
    res = build_epub(pages=pages, session_dir=tmp / "sess", out=tmp / "b.epub",
                     meta={"title": "T", "language": "es"}, strategy="hybrid")
    with zipfile.ZipFile(res.path) as z:
        imgs = [n for n in z.namelist() if n.startswith("EPUB/images/")]
        assert len(imgs) == 1 and z.getinfo(imgs[0]).file_size > 50_000  # full-resolution colour crop
        chapter = z.read("EPUB/text/chapter001.xhtml").decode()
    assert f'src="../{imgs[0][5:]}"' in chapter
    assert "lámpara" in chapter and "<h1" in chapter
    rep = validate_epub(res.path, expect_text=True, expected_images=1, expected_order=res.expected_order)
    assert rep.passed, rep.errors


def test_reflowable_and_fixed_strategies_validate(fx, tmp):
    pages = analyse(fx["text_image"], tmp)
    for strategy in ("reflowable", "fixed"):
        res = build_epub(pages=pages, session_dir=tmp / "sess", out=tmp / f"{strategy}.epub",
                         meta={"title": "T", "language": "en"}, strategy=strategy)
        rep = validate_epub(res.path, expect_text=True, expected_images=res.expected_images,
                            expected_order=res.expected_order)
        assert rep.passed, (strategy, rep.errors, rep.epubcheck)


def _rewrite_zip(src: Path, dst: Path, drop: str | None = None, patch: tuple[str, bytes, bytes] | None = None) -> None:
    with zipfile.ZipFile(src) as zin, zipfile.ZipFile(dst, "w") as zout:
        for info in zin.infolist():
            if info.filename == drop:
                continue
            data = zin.read(info.filename)
            if patch and info.filename == patch[0]:
                data = data.replace(patch[1], patch[2])
            zout.writestr(info, data)


def test_epub_validation_detects_missing_resources_and_broken_links(fx, tmp):
    good = fx["epub"]
    assert validate_epub(good, run_epubcheck=False).passed
    _rewrite_zip(good, tmp / "missing.epub", drop="EPUB/images/faro.png")
    rep = validate_epub(tmp / "missing.epub", run_epubcheck=False)
    assert not rep.passed
    assert any(c.name == "manifest_resources" and not c.passed for c in rep.checks)
    assert any(c.name == "internal_links" and not c.passed for c in rep.checks)
    _rewrite_zip(good, tmp / "broken.epub", patch=("EPUB/c2.xhtml", b"<p>La", b'<p><a href="nowhere.xhtml">x</a>La'))
    rep = validate_epub(tmp / "broken.epub", run_epubcheck=False)
    assert any(c.name == "internal_links" and not c.passed for c in rep.checks)


def test_epubcheck_runs_when_installed(fx):
    rep = validate_epub(fx["epub"])
    if not tool_available("epubcheck"):
        assert rep.epubcheck["executed"] is False  # never claims EPUBCheck validation it did not run
        pytest.skip("EPUBCheck not installed (./scripts/install-epubcheck.sh)")
    assert rep.epubcheck["executed"] and rep.epubcheck["errors"] == 0


def test_epub_to_pdf_keeps_text_images_and_headings(fx, tmp):
    from app.services.epub.to_pdf import epub_to_pdf
    from app.services.pdf_ops import get_outline

    res = epub_to_pdf(fx["epub"], tmp / "b.pdf", tmp / "w")
    text = extract_all_text(tmp / "b.pdf")
    assert "Había una vez un faro" in text and "señora Núñez" in text
    assert res["images"] == 1
    assert "Capítulo 2" in [o["title"] for o in get_outline(tmp / "b.pdf")]


@pytest.mark.skipif(not tool_available("libreoffice"), reason="LibreOffice is not installed")
def test_office_document_converts_to_pdf(fx, tmp):
    from app.services.office import office_to_pdf

    (tmp / "w").mkdir()
    res = office_to_pdf(fx["docx"], "docx", tmp / "o.pdf", tmp / "w")
    assert "canción, niño, acción" in extract_all_text(tmp / "o.pdf")
    assert res["warnings"]
    assert fx["docx"].exists()  # original untouched
    shutil.rmtree(tmp / "w", ignore_errors=True)


def test_novel_structure_chapters_cover_and_clean_text(tmp):
    """Regression for a real ebook-to-PDF novel: same-size all-caps chapter names, bookmarks, image
    cover, tab-encoded word gaps and a hanging last word."""
    from app.services.handlers import _pdf_book_metadata, outline_chapters
    from tests.fixtures import novel_pdf

    for with_bm, expected in ((True, ["1. Axel", "2. Leah", "3. Axel"]),
                              (False, ["ENERO (VERANO)", "AXEL", "LEAH", "FEBRERO (VERANO)", "AXEL"])):
        pdf = novel_pdf(tmp / f"novel-{with_bm}.pdf", with_bookmarks=with_bm)
        pages = analyse(pdf, tmp / str(with_bm))
        assert pages[0]["mode"] == "ocr"  # the image-only cover page
        res = build_epub(pages=pages, session_dir=tmp / str(with_bm) / "sess", out=tmp / f"n{with_bm}.epub",
                         meta={"title": "T", "language": "es"}, chapters=outline_chapters(pdf))
        assert [c["title"] for c in res.report["chapters"]] == expected
        assert res.report.get("cover") is True
        rep = validate_epub(res.path, expect_text=True, expected_images=res.expected_images,
                            expected_order=res.expected_order)
        assert rep.passed, (rep.errors, rep.epubcheck)
        with zipfile.ZipFile(res.path) as z:
            body = " ".join(z.read(n).decode() for n in z.namelist() if n.startswith("EPUB/text/chapter"))
        assert "\t" not in body and "\x00" not in body
        assert "se echó a reír." in body  # hanging word stays in its sentence, not a "marginal note"
        assert 'class="marginal"' not in body
    assert _pdf_book_metadata(pdf) == {"title": "Novela de Prueba", "author": "Autora Sintética"}


def test_indented_paragraphs_become_separate_epub_paragraphs(tmp):
    from reportlab.pdfgen import canvas

    src = tmp / "indent.pdf"
    c = canvas.Canvas(str(src), pagesize=(420, 595))
    c.setFont("Helvetica", 11)
    lines = [(64, "Primer párrafo que empieza con sangría y sigue"), (50, "en una segunda línea completa hasta el"),
             (50, "final del párrafo."), (64, "Segundo párrafo con su propia sangría y"), (50, "más texto aquí."),
             (64, "—Una línea de diálogo."), (64, "—Y la respuesta.")]
    for k, (x, ln) in enumerate(lines):
        c.drawString(x, 500 - 14 * k, ln)
    c.showPage()
    c.save()
    pages = analyse(src, tmp)
    res = build_epub(pages=pages, session_dir=tmp / "sess", out=tmp / "i.epub", meta={"title": "T", "language": "es"})
    with zipfile.ZipFile(res.path) as z:
        xhtml = z.read("EPUB/text/chapter001.xhtml").decode()
    paras = [re.sub(r"<[^>]+>", "", p) for p in re.findall(r"<p[^>]*>.*?</p>", xhtml, re.S)]
    assert paras == ["Primer párrafo que empieza con sangría y sigue en una segunda línea completa hasta el final "
                     "del párrafo.", "Segundo párrafo con su propia sangría y más texto aquí.",
                     "—Una línea de diálogo.", "—Y la respuesta."], paras


def _page(i: int, regions: list[tuple[str, str]]) -> dict:
    return {"page_index": i, "status": "done", "reviewed": False, "classification": {"needs_ocr": False},
            "regions": [{"id": f"p{i}-r{k}", "role": role, "order": k, "text": text, "corrected_text": None,
                         "excluded": False, "heading_level": 2 if role == "heading" else None, "conf": 100,
                         "source": "text_layer", "bbox": [0, 0, 1, 1], "lines": []}
                        for k, (role, text) in enumerate(regions)]}


def test_builder_structure_rules_from_real_novel():
    from app.services.epub.builder import collect_blocks, continues, split_chapters

    pages = [
        _page(0, [("heading", "ÍNDICE"), ("heading", "ENERO (VERANO)"), ("paragraph", "1. Axel 2. Axel 3. Leah")]),
        _page(1, [("heading", "ENERO")]),
        _page(2, [("heading", "(VERANO)")]),
        _page(3, [("paragraph", "1"), ("heading", "AXEL"),
                  ("paragraph", "—Genial. Liberador. —Expulsé el humo—. ¿Cómo van las cosas por"),
                  ("paragraph", "Sídney?"), ("paragraph", "—Bien, gracias."),
                  ("paragraph", "Algo dijo antes de callarse al ver mi expresión, que era seria y muy"),
                  ("paragraph", "—. ¿Qué ocurre?"), ("paragraph", "—Nada.")]),
    ]
    report = {"fallback_pages": [], "fallback_regions": [], "images_retained": 0, "removed_running_elements": {},
              "excluded_by_user": 0, "footnotes": 0, "warnings": []}
    blocks, notes = collect_blocks(pages, "hybrid", report)
    chapters = split_chapters(blocks, notes, None, len(pages))
    assert [c.title for c in chapters] == ["ÍNDICE", "ENERO (VERANO)", "1. AXEL"]
    texts = [b.text for b in chapters[2].blocks]
    assert "—Genial. Liberador. —Expulsé el humo—. ¿Cómo van las cosas por Sídney?" in texts
    assert "Algo dijo antes de callarse al ver mi expresión, que era seria y muy—. ¿Qué ocurre?" in texts
    assert "—Bien, gracias." in texts and "—Nada." in texts  # new dialogue turns stay separate
    assert not continues("Primeros capítulos", "Fragmentos de próximas publicaciones")  # short list items
    assert not continues("100", "Era el penúltimo sábado")


@needs_tesseract
def test_repeated_site_footer_is_removed_from_scanned_book(tmp):
    from tests.fixtures import scanned_book_with_footer

    pdf = scanned_book_with_footer(tmp / "footer.pdf")
    pages = analyse(pdf, tmp)
    assert any("lectulandia" in r["text"] for p in pages for r in p["regions"])  # it is recognised...
    res = build_epub(pages=pages, session_dir=tmp / "sess", out=tmp / "f.epub", meta={"title": "T", "language": "es"})
    with zipfile.ZipFile(res.path) as z:
        body = " ".join(z.read(n).decode() for n in z.namelist() if n.startswith("EPUB/text/chapter"))
    assert "lectulandia" not in body and "Página 9" not in body  # ...but kept out of the book text
    assert "Fin del texto de la p" in body and "gina 6." in body  # body text kept (OCR may drop the accent)
    removed = res.report["removed_running_elements"]
    assert removed.get("repeated", 0) + removed.get("footer", 0) >= 6
