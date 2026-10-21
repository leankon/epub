"""Permanent redaction: removes underlying glyphs, image pixels, paths and annotations, sanitises
document-level structures, writes a fresh file (no incremental history) and verifies the result."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import asdict
from pathlib import Path
from typing import Any

import pikepdf
import pypdfium2 as pdfium

from app.core.errors import AppError
from app.services.inspect import open_pdf
from app.services.pdf_content import Rect, RedactionStats, Redactor


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKC", s).casefold()
    return re.sub(r"\s+", "", s)


def find_term_rects(path: Path, terms: list[str], *, match_case: bool = False) -> dict[int, list[Rect]]:
    """Locate each occurrence of the terms and return per-page rectangles in PDF user space."""
    found: dict[int, list[Rect]] = {}
    doc = pdfium.PdfDocument(str(path))
    try:
        for pi in range(len(doc)):
            page = doc[pi]
            tp = page.get_textpage()
            for term in terms:
                if not term.strip():
                    continue
                searcher = tp.search(term, match_case=match_case, match_whole_word=False)
                while (hit := searcher.get_next()) is not None:
                    start, count = hit
                    lines: dict[int, list[float]] = {}
                    for ci in range(start, start + count):
                        left, bottom, right, top = tp.get_charbox(ci, loose=True)
                        if right <= left or top <= bottom:
                            continue
                        key = round((bottom + top) / 2 / 2)  # group chars of the same visual line
                        box = lines.setdefault(key, [left, bottom, right, top])
                        box[0], box[1] = min(box[0], left), min(box[1], bottom)
                        box[2], box[3] = max(box[2], right), max(box[3], top)
                    for b in lines.values():
                        found.setdefault(pi, []).append((b[0] - 0.5, b[1] - 0.5, b[2] + 0.5, b[3] + 0.5))
            tp.close()
            page.close()
    finally:
        doc.close()
    return found


def extract_all_text(path: Path, password: str | None = None) -> str:
    """Text as seen by two independent extractors (PDFium and pdfminer), for verification."""
    parts: list[str] = []
    doc = pdfium.PdfDocument(str(path), password=password)
    try:
        for i in range(len(doc)):
            tp = doc[i].get_textpage()
            parts.append(tp.get_text_range())
            tp.close()
    finally:
        doc.close()
    try:
        from pdfminer.high_level import extract_text

        parts.append(extract_text(str(path), password=password or ""))
    except Exception:
        pass
    return "\n".join(parts)


def chars_in_rects(path: Path, areas: dict[int, list[Rect]]) -> int:
    doc = pdfium.PdfDocument(str(path))
    n = 0
    try:
        for pi, rects in areas.items():
            tp = doc[pi].get_textpage()
            for ci in range(tp.count_chars()):
                left, bottom, right, top = tp.get_charbox(ci)
                cx, cy = (left + right) / 2, (bottom + top) / 2
                text = tp.get_text_range(ci, 1)
                if text.strip() and any(r[0] <= cx <= r[2] and r[1] <= cy <= r[3] for r in rects):
                    n += 1
            tp.close()
    finally:
        doc.close()
    return n


def redact(path: Path, out: Path, *, areas: list[dict[str, Any]] | None = None, terms: list[str] | None = None,
           match_case: bool = False, sanitize: dict[str, bool] | None = None) -> dict[str, Any]:
    terms = [t for t in (terms or []) if t and t.strip()]
    san = {"metadata": True, "attachments": True, "javascript": True, "structure_tree": True,
           "bookmarks": True, "all_annotations": False, **(sanitize or {})}
    by_page: dict[int, list[Rect]] = {}
    with open_pdf(path) as probe:
        n_pages = len(probe.pages)
    for a in areas or []:
        pi = int(a["page"])
        if not 0 <= pi < n_pages:
            raise AppError(f"Redaction area refers to page {pi + 1}, which does not exist.", code="invalid_range")
        x0, y0, x1, y1 = (float(v) for v in a["rect"])
        by_page.setdefault(pi, []).append((min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)))
    term_hits = find_term_rects(path, terms, match_case=match_case) if terms else {}
    for pi, rects in term_hits.items():
        by_page.setdefault(pi, []).extend(rects)
    if not by_page:
        raise AppError("Nothing to redact: no areas given and no search term was found.", code="nothing_to_redact")

    warnings: list[str] = []
    totals = RedactionStats()
    with open_pdf(path) as pdf:
        for pi, rects in sorted(by_page.items()):
            r = Redactor(pdf, rects)
            r.redact_page(pdf.pages[pi])
            totals.add(r.stats)
            if san["all_annotations"] and "/Annots" in pdf.pages[pi].obj:
                del pdf.pages[pi].obj["/Annots"]
        if totals.imprecise_fonts:
            warnings.append("Some fonts use non-identity encodings; glyph positions were approximated.")
        if san["metadata"]:
            if pdf.trailer.get("/Info") is not None:
                del pdf.trailer["/Info"]
            if "/Metadata" in pdf.Root:
                del pdf.Root["/Metadata"]
        if san["attachments"]:
            for name in list(pdf.attachments.keys()):
                del pdf.attachments[name]
        if san["javascript"]:
            for k in ("/OpenAction", "/AA"):
                if k in pdf.Root:
                    del pdf.Root[k]
            if "/Names" in pdf.Root and "/JavaScript" in pdf.Root.Names:
                del pdf.Root.Names["/JavaScript"]
        if san["structure_tree"] and "/StructTreeRoot" in pdf.Root:
            del pdf.Root["/StructTreeRoot"]  # may contain /ActualText or /Alt copies of the text
            if "/MarkInfo" in pdf.Root:
                del pdf.Root["/MarkInfo"]
            warnings.append("Accessibility tags were removed because they can duplicate redacted text.")
        if san["bookmarks"] and terms:
            _scrub_outline(pdf, terms)
        if "/AcroForm" in pdf.Root and totals.annotations_removed:
            warnings.append("Form fields overlapping redaction areas were removed and their values cleared.")
        pdf.remove_unreferenced_resources()
        # A full rewrite: no incremental update section, so earlier revisions are not carried over.
        pdf.save(out, compress_streams=True, object_stream_mode=pikepdf.ObjectStreamMode.generate,
                 linearize=False)

    verification = verify_redaction(out, terms, by_page)
    if not verification["passed"]:
        out.unlink(missing_ok=True)
        raise AppError("Verification failed: redacted content is still extractable. No output was saved.",
                       code="redaction_verification_failed", details={"remaining": verification["remaining"]})
    return {"pages_redacted": sorted(p + 1 for p in by_page), "areas": sum(len(v) for v in by_page.values()),
            "term_matches": sum(len(v) for v in term_hits.values()), "stats": asdict(totals),
            "verification": verification, "warnings": warnings}


def verify_redaction(path: Path, terms: list[str], areas: dict[int, list[Rect]]) -> dict[str, Any]:
    text = _norm(extract_all_text(path))
    remaining_terms = sum(1 for t in terms if _norm(t) and _norm(t) in text)
    remaining_chars = chars_in_rects(path, areas)
    return {"passed": remaining_terms == 0 and remaining_chars == 0, "remaining": {
        "terms": remaining_terms, "chars_in_areas": remaining_chars},
        "methods": ["pdfium text extraction", "pdfminer.six text extraction", "character positions in areas"]}


def _scrub_outline(pdf: pikepdf.Pdf, terms: list[str]) -> None:
    pats = [re.compile(re.escape(t), re.IGNORECASE) for t in terms]
    try:
        with pdf.open_outline() as ol:
            stack = list(ol.root)
            while stack:
                it = stack.pop()
                title = str(it.title or "")
                for p in pats:
                    title = p.sub("[redacted]", title)
                it.title = title
                stack.extend(it.children)
    except Exception:
        pass
