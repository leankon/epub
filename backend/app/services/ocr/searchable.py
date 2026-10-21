"""Searchable PDF: adds an invisible OCR text layer over the original page images (pages unchanged)."""

from __future__ import annotations

import io
from pathlib import Path
from typing import Any

import cv2
import pikepdf

from app.core.config import get_settings
from app.services.inspect import open_pdf
from app.services.ocr.classify import classify_page
from app.services.ocr.engine import detect_orientation, ocr_words
from app.services.ocr.preprocess import _rotate_quadrant, text_height
from app.services.pdf_ops import _display_transform, _font
from app.services.render import render_bgr


def _upright_to_display(rot: int, dw: float, dh: float) -> tuple[float, ...]:
    """Matrix mapping upright-image point space to display point space (both bottom-left origin)."""
    return {90: (0, 1, -1, 0, dw, 0), 180: (-1, 0, 0, -1, dw, dh), 270: (0, -1, 1, 0, 0, dh)}.get(
        rot, (1, 0, 0, 1, 0, 0))


def make_searchable(path: Path, out: Path, workdir: Path, *, languages: str | None = None, force: bool = False,
                    progress: Any = None) -> dict[str, Any]:
    from reportlab.pdfbase.pdfmetrics import stringWidth
    from reportlab.pdfgen import canvas as rl_canvas

    s = get_settings()
    dpi = s.ocr_dpi
    fontname = _font()
    stats = {"pages_ocr": 0, "pages_skipped_text": 0, "pages_skipped_existing_ocr": 0, "words": 0}
    warnings: list[str] = []
    with open_pdf(path) as pdf:
        n = len(pdf.pages)
        for i in range(n):
            if progress:
                progress(i, n)
            cls = classify_page(path, i)
            if not cls["needs_ocr"]:
                stats["pages_skipped_text"] += 1
                continue
            if cls["kind"] == "ocr_layer" and not force:
                stats["pages_skipped_existing_ocr"] += 1
                continue
            color = render_bgr(path, i, dpi)
            gray = cv2.cvtColor(color, cv2.COLOR_BGR2GRAY)
            del color
            rot, conf = detect_orientation(gray, workdir)
            rot = rot if rot in (90, 180, 270) and conf >= 1.5 else 0
            up = _rotate_quadrant(gray, rot)
            th = text_height(up)
            scale = min(2.5, 26 / th) if 0 < th < 18 else 1.0
            ocr_img = cv2.resize(up, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC) if scale != 1 else up
            words = ocr_words(ocr_img, workdir, langs=languages)
            hu, wu = up.shape[:2]
            page = pdf.pages[i]
            dw, dh, m = _display_transform(page)
            uw_pt, uh_pt = (dw, dh) if rot in (0, 180) else (dh, dw)
            x0, y0, x1, y1 = (float(v) for v in page.mediabox)
            buf = io.BytesIO()
            c = rl_canvas.Canvas(buf, pagesize=(x1 - x0, y1 - y0))
            c.translate(-x0, -y0)
            c.transform(*m)
            c.transform(*_upright_to_display(rot, dw, dh))
            for wd in words:
                if wd.conf < 10:
                    continue
                bx0, by0 = wd.x0 / scale / wu * uw_pt, wd.y0 / scale / hu * uh_pt
                bx1, by1 = wd.x1 / scale / wu * uw_pt, wd.y1 / scale / hu * uh_pt
                height = by1 - by0
                size = max(1.0, height * 0.95)
                text = c.beginText()
                text.setTextRenderMode(3)  # invisible: selectable/searchable, not painted
                text.setFont(fontname, size)
                natural = stringWidth(wd.text, fontname, size) or 1
                text.setHorizScale(max(10.0, min(400.0, 100.0 * (bx1 - bx0) / natural)))
                text.setTextOrigin(bx0, uh_pt - by1 + height * 0.2)
                text.textOut(wd.text + " ")
                c.drawText(text)
                stats["words"] += 1
            c.showPage()
            c.save()
            overlay = pikepdf.Pdf.open(io.BytesIO(buf.getvalue()))
            page.add_overlay(overlay.pages[0], pikepdf.Rectangle(x0, y0, x1, y1))
            stats["pages_ocr"] += 1
        if stats["pages_skipped_existing_ocr"]:
            warnings.append(f"{stats['pages_skipped_existing_ocr']} page(s) already had an OCR text layer and "
                            "were left unchanged to avoid duplicate text (use 'force' to redo).")
        pdf.save(out, compress_streams=True, object_stream_mode=pikepdf.ObjectStreamMode.generate)
    return {**stats, "warnings": warnings, "dpi": dpi}
