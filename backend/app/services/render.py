"""Page rendering with PDFium (local), with an on-disk cache for thumbnails/previews."""

from __future__ import annotations

import io
import threading
import uuid
from pathlib import Path

import numpy as np
import pypdfium2 as pdfium
from PIL import Image

from app.core.config import get_settings
from app.core.errors import AppError

_pdfium_lock = threading.Lock()  # PDFium is not thread-safe


def render_pil(path: Path, index: int, *, dpi: float | None = None, width: int | None = None,
               password: str | None = None) -> Image.Image:
    with _pdfium_lock:
        doc = pdfium.PdfDocument(str(path), password=password)
        try:
            if not 0 <= index < len(doc):
                raise AppError("Page does not exist.", code="invalid_range", status_code=404)
            page = doc[index]
            pw, _ = page.get_size()
            if page.get_rotation() in (90, 270):
                pw = page.get_size()[1]
            scale = (width / pw) if width else (dpi or 96) / 72.0
            scale = max(0.05, min(scale, 1200 / 72.0))
            img = page.render(scale=scale, may_draw_forms=True).to_pil()
            page.close()
            return img.convert("RGB")
        finally:
            doc.close()


def render_bgr(path: Path, index: int, dpi: float) -> np.ndarray:
    img = render_pil(path, index, dpi=dpi)
    arr = np.asarray(img)[:, :, ::-1].copy()
    del img
    return arr


def page_count(path: Path) -> int:
    with _pdfium_lock:
        doc = pdfium.PdfDocument(str(path))
        try:
            return len(doc)
        finally:
            doc.close()


def cached_thumbnail(path: Path, version_id: uuid.UUID, document_id: uuid.UUID, index: int, width: int) -> bytes:
    width = max(64, min(int(width), 2000))
    cache = get_settings().cache_dir / "render" / f"{document_id}-{version_id}"
    f = cache / f"p{index:05d}-w{width}.png"
    if f.exists():
        return f.read_bytes()
    img = render_pil(path, index, width=width)
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=False)
    cache.mkdir(parents=True, exist_ok=True)
    tmp = f.with_suffix(".tmp")
    tmp.write_bytes(buf.getvalue())
    tmp.replace(f)
    return buf.getvalue()
