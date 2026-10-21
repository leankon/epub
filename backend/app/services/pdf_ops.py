"""PDF transformations built on pikepdf. Every function writes a new file; inputs are never touched."""

from __future__ import annotations

import io
import json
import re
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pikepdf
from pikepdf import Name, Pdf, PdfImage
from PIL import Image

from app.core.errors import AppError, PasswordRequired, ProcessingError
from app.services.inspect import open_pdf

# --------------------------------------------------------------------------- page ranges

RANGE_RE = re.compile(r"^\s*(\d+)?\s*(-)?\s*(\d+)?\s*$")


def parse_page_ranges(spec: str, page_count: int) -> list[list[int]]:
    """Parse "1-3, 5, 8-" into groups of zero-based page indices. Raises on invalid input."""
    if not spec or not spec.strip():
        raise AppError("Page range is empty.", code="invalid_range")
    groups: list[list[int]] = []
    for part in spec.split(","):
        part = part.strip()
        if not part:
            raise AppError(f"Invalid page range '{spec}'.", code="invalid_range")
        if part.lower() in ("last", "end"):
            groups.append([page_count - 1])
            continue
        m = RANGE_RE.match(part)
        if not m or (m.group(1) is None and m.group(3) is None):
            raise AppError(f"Invalid page range '{part}'. Use forms like 1-3, 5, 8-.", code="invalid_range")
        start = int(m.group(1)) if m.group(1) else 1
        end = (int(m.group(3)) if m.group(3) else page_count) if m.group(2) else start
        if start < 1 or end < 1 or start > page_count or end > page_count:
            raise AppError(f"Page range '{part}' is outside 1-{page_count}.", code="invalid_range")
        if end < start:
            raise AppError(f"Page range '{part}' is reversed.", code="invalid_range")
        groups.append(list(range(start - 1, end)))
    return groups


def flatten_ranges(spec: str | None, page_count: int) -> list[int]:
    if not spec:
        return list(range(page_count))
    seen: list[int] = []
    for g in parse_page_ranges(spec, page_count):
        for i in g:
            if i not in seen:
                seen.append(i)
    return seen


# --------------------------------------------------------------------------- outlines

@dataclass
class OutlineNode:
    title: str
    page: int | None
    children: list[OutlineNode]

    def to_dict(self) -> dict[str, Any]:
        return {"title": self.title, "page": self.page, "children": [c.to_dict() for c in self.children]}


def _dest_page_index(pdf: Pdf, item: pikepdf.OutlineItem, page_index: dict[tuple[int, int], int]) -> int | None:
    dest = item.destination
    if dest is None and item.action is not None and item.action.get("/S") == "/GoTo":
        dest = item.action.get("/D")
    if isinstance(dest, (pikepdf.String, str, pikepdf.Name)):
        try:
            names = pdf.Root.Names.Dests if "/Names" in pdf.Root and "/Dests" in pdf.Root.Names else None
            if names is not None:
                tree = pikepdf.NameTree(names)
                d = tree.get(str(dest))
                dest = d.get("/D") if isinstance(d, pikepdf.Dictionary) else d
            elif "/Dests" in pdf.Root:
                dest = pdf.Root.Dests.get(Name("/" + str(dest)))
        except Exception:
            return None
    if isinstance(dest, pikepdf.Array) and len(dest) > 0:
        target = dest[0]
        if isinstance(target, int):
            return int(target)
        if isinstance(target, pikepdf.Dictionary) and target.objgen != (0, 0):
            return page_index.get(target.objgen)
    return None


def read_outline(pdf: Pdf) -> list[OutlineNode]:
    page_index = {p.obj.objgen: i for i, p in enumerate(pdf.pages)}

    def conv(items: list[pikepdf.OutlineItem]) -> list[OutlineNode]:
        return [OutlineNode(str(it.title or ""), _dest_page_index(pdf, it, page_index), conv(list(it.children)))
                for it in items]

    try:
        with pdf.open_outline() as ol:
            return conv(list(ol.root))
    except Exception:
        return []


def write_outline(pdf: Pdf, nodes: list[OutlineNode]) -> None:
    n = len(pdf.pages)

    def conv(items: list[OutlineNode]) -> list[pikepdf.OutlineItem]:
        out = []
        for node in items:
            page = node.page if node.page is not None and 0 <= node.page < n else 0
            it = pikepdf.OutlineItem(node.title[:500], page)
            it.children.extend(conv(node.children))
            out.append(it)
        return out

    with pdf.open_outline() as ol:
        ol.root.clear()
        ol.root.extend(conv(nodes))


def outline_from_dicts(items: list[dict[str, Any]]) -> list[OutlineNode]:
    return [OutlineNode(str(d.get("title", ""))[:500], int(d["page"]) if d.get("page") is not None else None,
                        outline_from_dicts(d.get("children") or [])) for d in items]


def _shift(nodes: list[OutlineNode], offset: int) -> list[OutlineNode]:
    return [OutlineNode(nd.title, nd.page + offset if nd.page is not None else None, _shift(nd.children, offset))
            for nd in nodes]


def _filter_outline(nodes: list[OutlineNode], mapping: dict[int, int]) -> list[OutlineNode]:
    out = []
    for nd in nodes:
        kids = _filter_outline(nd.children, mapping)
        if nd.page is not None and nd.page in mapping:
            out.append(OutlineNode(nd.title, mapping[nd.page], kids))
        elif kids:
            out.append(OutlineNode(nd.title, kids[0].page, kids))
    return out


def _save(pdf: Pdf, out: Path, **kw: Any) -> None:
    pdf.remove_unreferenced_resources()
    pdf.save(out, compress_streams=True, object_stream_mode=pikepdf.ObjectStreamMode.generate, **kw)


# --------------------------------------------------------------------------- merge / split / organize

def merge(sources: list[tuple[Path, str]], out: Path) -> list[str]:
    warnings: list[str] = []
    dst = Pdf.new()
    outline: list[OutlineNode] = []
    offset = 0
    for path, title in sources:
        with open_pdf(path) as src:
            n = len(src.pages)
            src_outline = read_outline(src)
            outline.append(OutlineNode(title, offset, _shift(src_outline, offset)))
            if "/AcroForm" in src.Root:
                warnings.append(f"'{title}': interactive form fields are not merged and may stop working.")
            if "/Names" in src.Root and "/Dests" in src.Root.Names:
                warnings.append(f"'{title}': named destinations are not preserved; internal links may break.")
            if any("/Annots" in p.obj for p in src.pages):
                warnings.append(f"'{title}': annotations are copied, but links to other pages may not resolve.")
            if len(src.attachments):
                warnings.append(f"'{title}': embedded file attachments are not carried over.")
            dst.pages.extend(src.pages)
            offset += n
    if offset == 0:
        raise ProcessingError("The selected PDFs contain no pages.")
    write_outline(dst, outline)
    with open_pdf(sources[0][0]) as first:
        for k in ("/Title", "/Author", "/Subject", "/Keywords"):
            if first.trailer.get("/Info") is not None and k in first.docinfo:
                dst.docinfo[k] = first.docinfo[k]
    _save(dst, out)
    return sorted(set(warnings))


def extract_pages(path: Path, indices: list[int], out: Path) -> None:
    with open_pdf(path) as src:
        dst = Pdf.new()
        for i in indices:
            dst.pages.append(src.pages[i])
        mapping = {old: new for new, old in enumerate(indices)}
        write_outline(dst, _filter_outline(read_outline(src), mapping))
        _save(dst, out)


def bookmark_groups(path: Path) -> list[tuple[str, list[int]]]:
    with open_pdf(path) as src:
        n = len(src.pages)
        tops = [nd for nd in read_outline(src) if nd.page is not None]
    starts = sorted({(nd.page, nd.title) for nd in tops if nd.page is not None}, key=lambda t: t[0])
    if not starts:
        raise AppError("This PDF has no usable top-level bookmarks to split by.", code="no_bookmarks")
    groups: list[tuple[str, list[int]]] = []
    if starts[0][0] > 0:
        groups.append(("Front matter", list(range(0, starts[0][0]))))
    for i, (page, title) in enumerate(starts):
        end = starts[i + 1][0] if i + 1 < len(starts) else n
        if end > page:
            groups.append((title, list(range(page, end))))
    return groups


def organize(path: Path, order: list[dict[str, int]], out: Path) -> None:
    """`order` lists output pages: {"page": source index (0-based), "rotate": 0|90|180|270}."""
    if not order:
        raise AppError("The organised document must contain at least one page.", code="invalid_range")
    with open_pdf(path) as src:
        n = len(src.pages)
        dst = Pdf.new()
        mapping: dict[int, int] = {}
        for new_i, item in enumerate(order):
            i = int(item["page"])
            rot = int(item.get("rotate", 0))
            if not 0 <= i < n:
                raise AppError(f"Page {i + 1} does not exist (document has {n} pages).", code="invalid_range")
            if rot % 90 != 0:
                raise AppError("Rotation must be a multiple of 90 degrees.", code="invalid_rotation")
            dst.pages.append(src.pages[i])
            if rot % 360:
                dst.pages[-1].rotate(rot % 360, relative=True)
            mapping.setdefault(i, new_i)
        write_outline(dst, _filter_outline(read_outline(src), mapping))
        _save(dst, out)


# --------------------------------------------------------------------------- compression

PRESETS: dict[str, dict[str, Any]] = {
    "lossless": {"dpi": None, "quality": None, "lossy": False},
    "high_quality": {"dpi": 225, "quality": 85, "lossy": True},
    "balanced": {"dpi": 150, "quality": 75, "lossy": True},
    "strong": {"dpi": 100, "quality": 60, "lossy": True},
}


def _image_display_sizes(pdf: Pdf) -> dict[tuple[int, int], float]:
    """Largest displayed size (points, longest side) per image object, from content streams."""
    from app.services.pdf_content import iter_image_placements

    sizes: dict[tuple[int, int], float] = {}
    for page in pdf.pages:
        for objgen, w_pt, h_pt in iter_image_placements(page):
            sizes[objgen] = max(sizes.get(objgen, 0.0), max(w_pt, h_pt))
    return sizes


def compress(path: Path, out: Path, preset: str = "balanced") -> dict[str, Any]:
    if preset not in PRESETS:
        raise AppError(f"Unknown compression preset '{preset}'.", code="invalid_preset")
    cfg = PRESETS[preset]
    stats = {"images_seen": 0, "images_recompressed": 0, "images_downsampled": 0, "images_skipped": 0}
    with open_pdf(path) as pdf:
        if cfg["lossy"]:
            displayed = _image_display_sizes(pdf)
            done: set[tuple[int, int]] = set()
            for page in pdf.pages:
                for _, raw in page.get_images().items():
                    if raw.objgen in done:
                        continue
                    done.add(raw.objgen)
                    stats["images_seen"] += 1
                    if _recompress_image(raw, displayed.get(raw.objgen), cfg["dpi"], cfg["quality"], stats):
                        stats["images_recompressed"] += 1
                    else:
                        stats["images_skipped"] += 1
        _save(pdf, out, recompress_flate=True)
    before, after = path.stat().st_size, out.stat().st_size
    return {**stats, "preset": preset, "lossy": cfg["lossy"], "input_bytes": before, "output_bytes": after,
            "reduction_percent": round(100.0 * (before - after) / before, 2) if before else 0.0}


def _recompress_image(raw: pikepdf.Object, shown_pt: float | None, dpi: int, quality: int,
                      stats: dict[str, int]) -> bool:
    if raw.get("/ImageMask") or "/Mask" in raw or raw.get("/BitsPerComponent", 8) != 8:
        return False
    filt = raw.get("/Filter")
    filters = [str(f) for f in (filt if isinstance(filt, pikepdf.Array) else [filt] if filt else [])]
    if any(f in ("/JBIG2Decode", "/JPXDecode", "/CCITTFaxDecode") for f in filters):
        return False
    try:
        img = PdfImage(raw)
        if img.colorspace not in ("/DeviceRGB", "/DeviceGray", "/ICCBased"):
            return False
        pil = img.as_pil_image()
    except Exception:
        return False
    if pil.mode not in ("RGB", "L"):
        return False
    w, h = pil.size
    if shown_pt and shown_pt > 0:
        target_long = int(shown_pt / 72.0 * dpi)
        if max(w, h) > target_long * 1.1 and target_long >= 16:
            scale = target_long / max(w, h)
            pil = pil.resize((max(1, int(w * scale)), max(1, int(h * scale))), Image.Resampling.LANCZOS)
            stats["images_downsampled"] += 1
    buf = io.BytesIO()
    pil.save(buf, format="JPEG", quality=quality, optimize=True)
    data = buf.getvalue()
    if len(data) >= len(raw.read_raw_bytes()) and pil.size == (w, h):
        return False
    smask = raw.get("/SMask")
    if smask is not None and pil.size != (w, h):
        try:
            m = PdfImage(smask).as_pil_image().convert("L").resize(pil.size, Image.Resampling.LANCZOS)
            smask.write(_zlib(m.tobytes()), filter=Name.FlateDecode)
            smask.Width, smask.Height = m.size
            for k in ("/DecodeParms",):
                if k in smask:
                    del smask[k]
        except Exception:
            return False
    raw.write(data, filter=Name.DCTDecode)
    raw.Width, raw.Height = pil.size
    raw.ColorSpace = Name.DeviceRGB if pil.mode == "RGB" else Name.DeviceGray
    raw.BitsPerComponent = 8
    for k in ("/DecodeParms", "/Decode"):
        if k in raw:
            del raw[k]
    return True


def _zlib(b: bytes) -> bytes:
    import zlib

    return zlib.compress(b, 9)


# --------------------------------------------------------------------------- overlays

def _display_transform(page: pikepdf.Page) -> tuple[float, float, tuple[float, ...]]:
    """Return display width/height and the matrix mapping display coordinates to user space."""
    x0, y0, x1, y1 = (float(v) for v in page.mediabox)
    w, h = x1 - x0, y1 - y0
    r = int(page.obj.get("/Rotate", 0)) % 360
    if r == 90:
        return h, w, (0, 1, -1, 0, x0 + w, y0)
    if r == 180:
        return w, h, (-1, 0, 0, -1, x0 + w, y0 + h)
    if r == 270:
        return h, w, (0, -1, 1, 0, x0, y0 + h)
    return w, h, (1, 0, 0, 1, x0, y0)


POSITIONS = {"center", "top-left", "top-center", "top-right", "bottom-left", "bottom-center", "bottom-right",
             "middle-left", "middle-right", "tile"}


def _anchor(pos: str, dw: float, dh: float, margin: float) -> tuple[float, float]:
    vx = {"left": margin, "center": dw / 2, "right": dw - margin}
    vy = {"top": dh - margin, "middle": dh / 2, "bottom": margin}
    if pos == "center":
        return dw / 2, dh / 2
    v, hpos = pos.split("-")
    return vx[hpos], vy[v]


def _hex_color(c: str) -> tuple[float, float, float]:
    c = c.lstrip("#")
    if not re.fullmatch(r"[0-9a-fA-F]{6}", c):
        raise AppError("Colour must be a hex value such as #cc0000.", code="invalid_color")
    return tuple(int(c[i:i + 2], 16) / 255 for i in (0, 2, 4))  # type: ignore[return-value]


def _font() -> str:
    """Register a Unicode TTF if available (accents, ñ); fall back to Helvetica."""
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    if "PdfwsSans" in pdfmetrics.getRegisteredFontNames():
        return "PdfwsSans"
    for cand in ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "/Library/Fonts/Arial Unicode.ttf",
                 "/System/Library/Fonts/Supplemental/Arial.ttf", "C:/Windows/Fonts/arial.ttf",
                 "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf"):
        if Path(cand).exists():
            pdfmetrics.registerFont(TTFont("PdfwsSans", cand))
            return "PdfwsSans"
    return "Helvetica"


def _overlay_pdf(pages: list[pikepdf.Page], draw: Any) -> Pdf:
    """Build one overlay page per target page with reportlab; `draw(canvas, idx, dw, dh)` paints upright."""
    from reportlab.pdfgen import canvas as rl_canvas

    buf = io.BytesIO()
    c = rl_canvas.Canvas(buf)
    for idx, page in enumerate(pages):
        x0, y0, x1, y1 = (float(v) for v in page.mediabox)
        c.setPageSize((x1 - x0, y1 - y0))
        dw, dh, m = _display_transform(page)
        c.saveState()
        c.translate(-x0, -y0)  # overlay page origin is 0,0; mediabox may not be
        c.transform(*m)
        draw(c, idx, dw, dh)
        c.restoreState()
        c.showPage()
    c.save()
    return Pdf.open(io.BytesIO(buf.getvalue()))


def _apply_overlay(pdf: Pdf, indices: list[int], draw: Any) -> None:
    targets = [pdf.pages[i] for i in indices]
    ov = _overlay_pdf(targets, draw)
    for k, i in enumerate(indices):
        page = pdf.pages[i]
        x0, y0, x1, y1 = (float(v) for v in page.mediabox)
        page.add_overlay(ov.pages[k], pikepdf.Rectangle(x0, y0, x1, y1))


def watermark(path: Path, out: Path, opts: dict[str, Any], *, only_first: bool = False) -> None:
    kind = opts.get("type", "text")
    pos = opts.get("position", "center")
    if pos not in POSITIONS:
        raise AppError("Invalid watermark position.", code="invalid_position")
    opacity = float(opts.get("opacity", 0.3))
    if not 0.0 < opacity <= 1.0:
        raise AppError("Opacity must be between 0 and 1.", code="invalid_opacity")
    rotation = float(opts.get("rotation", 45))
    size = float(opts.get("font_size", 48))
    scale = float(opts.get("image_scale", 0.4))
    color = _hex_color(opts.get("color", "#cc0000"))
    text = str(opts.get("text", "CONFIDENTIAL"))[:200]
    image_path: Path | None = opts.get("image_path")
    font = _font()
    img_reader = None
    if kind == "image":
        if image_path is None:
            raise AppError("An image is required for an image watermark.", code="missing_image")
        from reportlab.lib.utils import ImageReader

        img_reader = ImageReader(str(image_path))
    elif not text.strip():
        raise AppError("Watermark text is empty.", code="invalid_text")

    def draw(c: Any, _: int, dw: float, dh: float) -> None:
        c.setFillAlpha(opacity)
        c.setStrokeAlpha(opacity)
        c.setFillColorRGB(*color)
        points = ([(dw * fx, dh * fy) for fx in (0.25, 0.75) for fy in (0.2, 0.5, 0.8)] if pos == "tile"
                  else [_anchor(pos, dw, dh, 36 + size / 2)])
        for (ax, ay) in points:
            c.saveState()
            c.translate(ax, ay)
            c.rotate(rotation)
            if img_reader is not None:
                iw, ih = img_reader.getSize()
                tw = dw * scale
                th = tw * ih / iw
                c.drawImage(img_reader, -tw / 2, -th / 2, tw, th, mask="auto")
            else:
                c.setFont(font, size)
                c.drawCentredString(0, -size / 3, text)
            c.restoreState()

    with open_pdf(path) as pdf:
        indices = [0] if only_first else flatten_ranges(opts.get("pages"), len(pdf.pages))
        _apply_overlay(pdf, indices, draw)
        if only_first:
            del pdf.pages[1:]
        _save(pdf, out)


def page_numbers(path: Path, out: Path, opts: dict[str, Any], *, only_first: bool = False) -> None:
    pos = opts.get("position", "bottom-center")
    if pos not in POSITIONS - {"tile", "center"}:
        raise AppError("Invalid page number position.", code="invalid_position")
    fmt = str(opts.get("format", "{n}"))[:80]
    if "{n}" not in fmt:
        raise AppError("Format must contain {n} (and optionally {total}).", code="invalid_format")
    start = int(opts.get("start", 1))
    size = float(opts.get("font_size", 11))
    margin = float(opts.get("margin", 28))
    color = _hex_color(opts.get("color", "#000000"))
    font = _font()
    with open_pdf(path) as pdf:
        indices = [0] if only_first else flatten_ranges(opts.get("pages"), len(pdf.pages))
        total = len(indices) + start - 1

        def draw(c: Any, k: int, dw: float, dh: float) -> None:
            label = fmt.replace("{n}", str(start + k)).replace("{total}", str(total))
            ax, ay = _anchor(pos, dw, dh, margin)
            c.setFillColorRGB(*color)
            c.setFont(font, size)
            if pos.endswith("left"):
                c.drawString(ax, ay, label)
            elif pos.endswith("right"):
                c.drawRightString(ax, ay, label)
            else:
                c.drawCentredString(ax, ay, label)

        _apply_overlay(pdf, indices, draw)
        if only_first:
            del pdf.pages[1:]
        _save(pdf, out)


# --------------------------------------------------------------------------- metadata, bookmarks

META_KEYS = {"title": "/Title", "author": "/Author", "subject": "/Subject", "keywords": "/Keywords"}


def set_metadata(path: Path, out: Path, fields: dict[str, str | None]) -> None:
    with open_pdf(path) as pdf:
        with pdf.open_metadata(set_pikepdf_as_editor=True) as xmp:
            for key, pdfkey in META_KEYS.items():
                if key not in fields:
                    continue
                val = (fields[key] or "").strip()
                if val:
                    pdf.docinfo[pdfkey] = val
                elif pdfkey in pdf.docinfo:
                    del pdf.docinfo[pdfkey]
            xmp.load_from_docinfo(pdf.docinfo)
            if "language" in fields:
                lang = (fields["language"] or "").strip()
                if lang and not re.fullmatch(r"[A-Za-z]{2,3}(-[A-Za-z0-9]{2,8})*", lang):
                    raise AppError("Language must be a BCP 47 tag such as 'es' or 'en-US'.", code="invalid_language")
                if lang:
                    pdf.Root.Lang = pikepdf.String(lang)
                    xmp["dc:language"] = [lang]
                elif "/Lang" in pdf.Root:
                    del pdf.Root["/Lang"]
        _save(pdf, out)


def get_outline(path: Path, password: str | None = None) -> list[dict[str, Any]]:
    with open_pdf(path, password) as pdf:
        return [n.to_dict() for n in read_outline(pdf)]


def set_outline(path: Path, out: Path, items: list[dict[str, Any]]) -> None:
    with open_pdf(path) as pdf:
        nodes = outline_from_dicts(items)
        n = len(pdf.pages)

        def check(ns: list[OutlineNode]) -> None:
            for nd in ns:
                if nd.page is None or not 0 <= nd.page < n:
                    raise AppError(f"Bookmark '{nd.title}' points outside the document.", code="invalid_range")
                check(nd.children)

        check(nodes)
        write_outline(pdf, nodes)
        _save(pdf, out)


# --------------------------------------------------------------------------- encryption

def protect(path: Path, out: Path, user_password: str, owner_password: str | None,
            permissions: dict[str, bool]) -> None:
    if not user_password and not owner_password:
        raise AppError("Provide a user password, an owner password, or both.", code="password_missing")
    with open_pdf(path) as pdf:
        allow = pikepdf.Permissions(
            print_lowres=permissions.get("print", True), print_highres=permissions.get("print", True),
            modify_other=permissions.get("modify", False), modify_assembly=permissions.get("modify", False),
            modify_annotation=permissions.get("annotate", False), modify_form=permissions.get("forms", False),
            extract=permissions.get("extract", False), accessibility=True)
        enc = pikepdf.Encryption(user=user_password or "", owner=owner_password or user_password, R=6, allow=allow)
        pdf.save(out, encryption=enc)


def unlock(path: Path, out: Path, password: str) -> dict[str, Any]:
    if not password:
        raise PasswordRequired("Enter the document password.")
    try:
        pdf = pikepdf.open(path, password=password)
    except pikepdf.PasswordError:
        raise PasswordRequired("The password is incorrect.") from None
    with pdf:
        if not pdf.is_encrypted:
            raise AppError("This PDF is not encrypted.", code="not_encrypted")
        a = pdf.allow
        unrestricted = all([a.extract, a.modify_other, a.modify_assembly, a.print_highres])
        if not pdf.owner_password_matched and not unrestricted:
            raise PasswordRequired(
                "That is the open (user) password, but the document also has usage restrictions. "
                "Removing restrictions requires the owner password.")
        pdf.save(out)
        return {"matched": "owner" if pdf.owner_password_matched else "user"}


# --------------------------------------------------------------------------- images

def extract_images(path: Path, out_zip: Path, pages: str | None = None) -> dict[str, Any]:
    manifest: list[dict[str, Any]] = []
    seen: set[tuple[int, int]] = set()
    with open_pdf(path) as pdf, zipfile.ZipFile(out_zip, "w", zipfile.ZIP_STORED) as zf:
        for pi in flatten_ranges(pages, len(pdf.pages)):
            for name, raw in pdf.pages[pi].get_images().items():
                if raw.objgen in seen:
                    continue
                seen.add(raw.objgen)
                try:
                    img = PdfImage(raw)
                    buf = io.BytesIO()
                    ext = img.extract_to(stream=buf)  # original encoding when possible
                    data = buf.getvalue()
                    method = "native"
                except Exception:
                    try:
                        pil = PdfImage(raw).as_pil_image()
                        buf = io.BytesIO()
                        pil.save(buf, format="PNG")
                        data, ext, method = buf.getvalue(), ".png", "decoded"
                    except Exception:
                        continue
                fname = f"page{pi + 1:04d}-{len(manifest) + 1:04d}{ext}"
                zf.writestr(fname, data)
                manifest.append({"file": fname, "page": pi + 1, "width": int(raw.get("/Width", 0)),
                                 "height": int(raw.get("/Height", 0)), "method": method,
                                 "pdf_name": str(name)})
        zf.writestr("manifest.json", json.dumps(manifest, indent=2))
    if not manifest:
        out_zip.unlink(missing_ok=True)
        raise AppError("No embedded images were found on the selected pages.", code="no_images")
    return {"images": len(manifest)}


def pdf_to_images(path: Path, out_zip: Path, *, dpi: int = 150, fmt: str = "png",
                  pages: str | None = None, check_cancel: Any = None) -> dict[str, Any]:
    import pypdfium2 as pdfium

    if fmt not in ("png", "jpg"):
        raise AppError("Format must be png or jpg.", code="invalid_format")
    if not 36 <= dpi <= 600:
        raise AppError("DPI must be between 36 and 600.", code="invalid_dpi")
    doc = pdfium.PdfDocument(str(path))
    try:
        idx = flatten_ranges(pages, len(doc))
        with zipfile.ZipFile(out_zip, "w", zipfile.ZIP_STORED) as zf:
            for k, i in enumerate(idx):
                if check_cancel:
                    check_cancel(i, len(idx), k)
                pil = doc[i].render(scale=dpi / 72).to_pil()
                buf = io.BytesIO()
                if fmt == "jpg":
                    pil.convert("RGB").save(buf, format="JPEG", quality=90)
                else:
                    pil.save(buf, format="PNG", optimize=False)
                zf.writestr(f"page-{i + 1:04d}.{fmt}", buf.getvalue())
                del pil
    finally:
        doc.close()
    return {"pages": len(idx), "dpi": dpi, "format": fmt}


def images_to_pdf(images: list[Path], out: Path, workdir: Path, *, page_size: str = "image") -> None:
    import img2pdf

    prepared: list[str] = []
    for k, p in enumerate(images):
        with Image.open(p) as im:
            frames = getattr(im, "n_frames", 1)
            needs_convert = im.mode in ("RGBA", "LA", "P", "PA", "I;16", "CMYK") or p.suffix.lower() in (
                ".webp", ".bmp", ".gif") or frames > 1 or ("transparency" in im.info)
            if needs_convert:
                for f in range(frames):
                    im.seek(f)
                    frame = im.convert("RGBA")
                    bg = Image.new("RGB", frame.size, "white")
                    bg.paste(frame, mask=frame.split()[3])
                    q = workdir / f"img-{k:04d}-{f:03d}.png"
                    bg.save(q)
                    prepared.append(str(q))
            else:
                prepared.append(str(p))
    layout = None
    if page_size == "a4":
        layout = img2pdf.get_layout_fun((img2pdf.mm_to_pt(210), img2pdf.mm_to_pt(297)))
    elif page_size == "letter":
        layout = img2pdf.get_layout_fun((img2pdf.in_to_pt(8.5), img2pdf.in_to_pt(11)))
    out.write_bytes(img2pdf.convert(prepared, layout_fun=layout) if layout else img2pdf.convert(prepared))
