"""Per-page classification: does a usable text layer exist, is the page scanned, is it mixed?"""

from __future__ import annotations

import re
import unicodedata
from pathlib import Path
from typing import Any

import pypdfium2 as pdfium
import pypdfium2.raw as pdfium_c

from app.services.render import _pdfium_lock

WORD_RE = re.compile(r"[^\W\d_]{2,}", re.UNICODE)
VOWELS = set("aeiouáéíóúàèìòùäëïöüâêîôûyAEIOUÁÉÍÓÚY")


def _text_quality(text: str) -> float:
    """0..1 plausibility of extracted text (catches broken encodings and garbage layers)."""
    chars = [c for c in text if not c.isspace()]
    if not chars:
        return 0.0
    bad = sum(1 for c in chars if unicodedata.category(c) in ("Co", "Cn", "Cc", "Cs") or c == "�")
    words = WORD_RE.findall(text)
    if not words:
        return max(0.0, 0.3 - bad / len(chars))
    vowel_words = sum(1 for w in words if any(ch in VOWELS for ch in w) and len(w) <= 25)
    return max(0.0, min(1.0, (vowel_words / len(words)) * (1 - bad / len(chars))))


def classify_page(path: Path, index: int) -> dict[str, Any]:
    with _pdfium_lock:
        doc = pdfium.PdfDocument(str(path))
        try:
            page = doc[index]
            w, h = page.get_size()
            page_area = max(w * h, 1.0)
            tp = page.get_textpage()
            text = tp.get_text_range()
            n_chars = len([c for c in text if not c.isspace()])
            tp.close()
            img_area = 0.0
            images = []
            invisible = visible = 0
            for obj in page.get_objects(max_depth=4):
                if obj.type == pdfium_c.FPDF_PAGEOBJ_IMAGE:
                    x0, b, r, t = obj.get_bounds()
                    a = max(0.0, (min(r, w) - max(x0, 0)) * (min(t, h) - max(b, 0)))
                    img_area += a
                    images.append({"bounds": [x0, b, r, t], "area_fraction": round(a / page_area, 4)})
                elif obj.type == pdfium_c.FPDF_PAGEOBJ_TEXT:
                    mode = pdfium_c.FPDFTextObj_GetTextRenderMode(obj.raw)
                    if mode == pdfium_c.FPDF_TEXTRENDERMODE_INVISIBLE:
                        invisible += 1
                    else:
                        visible += 1
            rotation = page.get_rotation()
            page.close()
        finally:
            doc.close()
    coverage = min(1.0, img_area / page_area)
    quality = _text_quality(text)
    invisible_frac = invisible / max(1, invisible + visible)
    if n_chars < 20:
        kind = "scanned" if coverage > 0.3 else "no_text"
        needs_ocr = True
        reason = "no usable text layer"
    elif invisible_frac > 0.8:
        kind, needs_ocr = "ocr_layer", True
        reason = "existing invisible OCR layer; re-recognised to rebuild layout without duplicating text"
    elif quality < 0.55:
        kind, needs_ocr = "corrupt_text", True
        reason = "text layer looks corrupt or badly encoded"
    elif coverage > 0.85 and n_chars < 200:
        kind, needs_ocr = "scanned", True
        reason = "page is mostly a scanned image with a sparse text layer"
    elif coverage > 0.03:
        kind, needs_ocr, reason = "mixed", False, "digital text with images"
    else:
        kind, needs_ocr, reason = "digital", False, "good digital text layer"
    return {"kind": kind, "needs_ocr": needs_ocr, "reason": reason, "chars": n_chars,
            "text_quality": round(quality, 3), "image_coverage": round(coverage, 3),
            "invisible_text_fraction": round(invisible_frac, 3), "images": images[:50],
            "page_size_pt": [round(w, 2), round(h, 2)], "rotation": rotation,
            "photographed_hint": kind == "scanned" and coverage > 0.9}
