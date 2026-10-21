"""A small PDF content-stream interpreter (graphics + text state) used by redaction and compression.

It tracks the CTM and text matrices well enough to compute where each glyph, image and path lands
in page user space, so content can be removed - not merely covered.
"""

from __future__ import annotations

import io
import zlib
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

import pikepdf
from pikepdf import Name, Operator, PdfImage
from PIL import Image, ImageDraw

Matrix = tuple[float, float, float, float, float, float]
Rect = tuple[float, float, float, float]  # x0, y0, x1, y1 (user space, x0<x1, y0<y1)
IDENTITY: Matrix = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)


def mul(m1: Matrix, m2: Matrix) -> Matrix:
    """m1 x m2 (PDF row-vector convention: apply m1 first, then m2)."""
    a1, b1, c1, d1, e1, f1 = m1
    a2, b2, c2, d2, e2, f2 = m2
    return (a1 * a2 + b1 * c2, a1 * b2 + b1 * d2, c1 * a2 + d1 * c2, c1 * b2 + d1 * d2,
            e1 * a2 + f1 * c2 + e2, e1 * b2 + f1 * d2 + f2)


def apply(m: Matrix, x: float, y: float) -> tuple[float, float]:
    a, b, c, d, e, f = m
    return a * x + c * y + e, b * x + d * y + f


def invert(m: Matrix) -> Matrix | None:
    a, b, c, d, e, f = m
    det = a * d - b * c
    if abs(det) < 1e-12:
        return None
    ia, ib, ic, id_ = d / det, -b / det, -c / det, a / det
    return (ia, ib, ic, id_, -(e * ia + f * ic), -(e * ib + f * id_))


def bbox_of(m: Matrix, x0: float, y0: float, x1: float, y1: float) -> Rect:
    pts = [apply(m, x, y) for x, y in ((x0, y0), (x1, y0), (x0, y1), (x1, y1))]
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    return min(xs), min(ys), max(xs), max(ys)


def intersects(a: Rect, b: Rect) -> bool:
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


def overlap_fraction(inner: Rect, outer: Rect) -> float:
    w = min(inner[2], outer[2]) - max(inner[0], outer[0])
    h = min(inner[3], outer[3]) - max(inner[1], outer[1])
    if w <= 0 or h <= 0:
        return 0.0
    area = max((inner[2] - inner[0]) * (inner[3] - inner[1]), 1e-9)
    return (w * h) / area


def point_in(rects: list[Rect], x: float, y: float) -> bool:
    return any(r[0] <= x <= r[2] and r[1] <= y <= r[3] for r in rects)


def _num(v: Any) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


# --------------------------------------------------------------------------- fonts

class FontMetrics:
    def __init__(self, font: pikepdf.Dictionary | None) -> None:
        self.font = font
        self.two_byte = False
        self.supported = True
        self.widths: dict[int, float] = {}
        self.default = 500.0
        self.scale = 1 / 1000.0
        if font is None:
            return
        subtype = font.get("/Subtype")
        if subtype == "/Type0":
            enc = font.get("/Encoding")
            self.two_byte = True
            if not (isinstance(enc, Name) and str(enc) in ("/Identity-H", "/Identity-V")):
                # Other CMaps may be variable-length; we still assume 2 bytes and flag reduced precision.
                self.supported = False
            desc = font.DescendantFonts[0]
            self.default = _num(desc.get("/DW", 1000))
            w = desc.get("/W")
            if w is not None:
                i = 0
                items = list(w)
                while i < len(items):
                    first = int(items[i])
                    nxt = items[i + 1]
                    if isinstance(nxt, pikepdf.Array):
                        for k, val in enumerate(nxt):
                            self.widths[first + k] = _num(val)
                        i += 2
                    else:
                        last, val = int(nxt), _num(items[i + 2])
                        for code in range(first, min(last, first + 65535) + 1):
                            self.widths[code] = val
                        i += 3
            return
        if subtype == "/Type3":
            fm = font.get("/FontMatrix")
            if fm is not None:
                self.scale = _num(fm[0])
        first = int(font.get("/FirstChar", 0))
        widths = font.get("/Widths")
        if widths is not None:
            for k, val in enumerate(widths):
                self.widths[first + k] = _num(val)
        fd = font.get("/FontDescriptor")
        if fd is not None and "/MissingWidth" in fd:
            self.default = _num(fd.MissingWidth)
        if widths is None and subtype != "/Type3":
            self._standard14(str(font.get("/BaseFont", "/Helvetica"))[1:])

    def _standard14(self, base: str) -> None:
        try:
            from reportlab.pdfbase import pdfmetrics

            name = base.split("+")[-1]
            if name not in pdfmetrics.standardFonts:
                name = "Helvetica"
            for code in range(32, 256):
                self.widths[code] = pdfmetrics.stringWidth(bytes([code]).decode("cp1252", "replace"), name, 1000)
        except Exception:
            pass

    def codes(self, data: bytes) -> list[bytes]:
        if self.two_byte:
            return [data[i:i + 2] for i in range(0, len(data) - (len(data) % 2), 2)]
        return [data[i:i + 1] for i in range(len(data))]

    def width(self, code: bytes) -> float:
        c = int.from_bytes(code, "big")
        return self.widths.get(c, self.default) * self.scale


# --------------------------------------------------------------------------- state

@dataclass
class TextState:
    tm: Matrix = IDENTITY
    tlm: Matrix = IDENTITY
    font: FontMetrics | None = None
    size: float = 0.0
    tc: float = 0.0
    tw: float = 0.0
    th: float = 1.0
    tl: float = 0.0
    rise: float = 0.0


@dataclass
class GState:
    ctm: Matrix = IDENTITY
    text: TextState = field(default_factory=TextState)


PATH_OPS = {"m", "l", "c", "v", "y", "h", "re"}
PAINT_OPS = {"S", "s", "f", "F", "f*", "B", "B*", "b", "b*", "n"}


def _resources(obj: pikepdf.Object, fallback: pikepdf.Dictionary | None) -> pikepdf.Dictionary:
    res = obj.get("/Resources") if isinstance(obj, (pikepdf.Dictionary, pikepdf.Stream)) else None
    return res if res is not None else (fallback if fallback is not None else pikepdf.Dictionary())


# --------------------------------------------------------------------------- placement scan

def iter_image_placements(page: pikepdf.Page) -> Iterator[tuple[tuple[int, int], float, float]]:
    """Yield (image objgen, displayed width pt, displayed height pt) for each image drawn on a page."""
    yield from _scan_images(page.obj, page.obj.get("/Resources"), IDENTITY, 0)


def _scan_images(stream_owner: pikepdf.Object, resources: Any, base: Matrix, depth: int
                 ) -> Iterator[tuple[tuple[int, int], float, float]]:
    if depth > 8:
        return
    try:
        ops = pikepdf.parse_content_stream(stream_owner)
    except Exception:
        return
    stack: list[Matrix] = []
    ctm = base
    xobjs = resources.get("/XObject") if resources is not None else None
    for operands, op in ops:
        o = str(op)
        if o == "q":
            stack.append(ctm)
        elif o == "Q":
            ctm = stack.pop() if stack else base
        elif o == "cm" and len(operands) == 6:
            ctm = mul(tuple(_num(v) for v in operands), ctm)  # type: ignore[arg-type]
        elif o == "Do" and xobjs is not None:
            xo = xobjs.get(operands[0])
            if xo is None:
                continue
            if xo.get("/Subtype") == "/Image":
                x0, y0, x1, y1 = bbox_of(ctm, 0, 0, 1, 1)
                yield xo.objgen, x1 - x0, y1 - y0
            elif xo.get("/Subtype") == "/Form":
                fm = tuple(_num(v) for v in xo.get("/Matrix", [1, 0, 0, 1, 0, 0]))
                yield from _scan_images(xo, _resources(xo, resources), mul(fm, ctm), depth + 1)  # type: ignore[arg-type]


# --------------------------------------------------------------------------- redaction

@dataclass
class RedactionStats:
    glyphs_removed: int = 0
    text_ops_rewritten: int = 0
    text_ops_removed: int = 0
    images_modified: int = 0
    inline_images_removed: int = 0
    paths_removed: int = 0
    forms_rewritten: int = 0
    annotations_removed: int = 0
    imprecise_fonts: int = 0

    def add(self, other: RedactionStats) -> None:
        for k in self.__dataclass_fields__:
            setattr(self, k, getattr(self, k) + getattr(other, k))


class Redactor:
    def __init__(self, pdf: pikepdf.Pdf, rects: list[Rect]) -> None:
        self.pdf = pdf
        self.rects = rects
        self.stats = RedactionStats()
        self._fonts: dict[tuple[int, int], FontMetrics] = {}
        self._counter = 0

    def _font(self, resources: Any, name: Any) -> FontMetrics:
        fonts = resources.get("/Font") if resources is not None else None
        f = fonts.get(name) if fonts is not None else None
        key = f.objgen if f is not None and f.objgen != (0, 0) else (id(f), 0)
        if key not in self._fonts:
            fm = FontMetrics(f)
            if not fm.supported:
                self.stats.imprecise_fonts += 1
            self._fonts[key] = fm
        return self._fonts[key]

    def _fresh(self, prefix: str) -> Name:
        self._counter += 1
        return Name(f"/{prefix}Rd{self._counter}")

    def redact_page(self, page: pikepdf.Page) -> None:
        # Page-local resource copies so shared XObjects on other pages are untouched.
        res = page.obj.get("/Resources")
        res = pikepdf.Dictionary({k: v for k, v in res.items()}) if res is not None else pikepdf.Dictionary()
        if "/XObject" in res:
            res.XObject = pikepdf.Dictionary({k: v for k, v in res.XObject.items()})
        page.obj.Resources = res
        new_ops, _ = self._process(page.obj, res, IDENTITY, 0) if "/Contents" in page.obj else ([], False)
        body = pikepdf.unparse_content_stream(new_ops)
        boxes = b"".join(b"%.3f %.3f %.3f %.3f re\n" % (r[0], r[1], r[2] - r[0], r[3] - r[1]) for r in self.rects)
        content = b"q\n" + body + b"\nQ\nq 0 0 0 rg 0 0 0 RG\n" + boxes + b"f\nQ\n"
        page.obj.Contents = self.pdf.make_stream(content)
        if "/Thumb" in page.obj:
            del page.obj["/Thumb"]  # embedded thumbnails would still show the original content
        self._redact_annotations(page)

    def _redact_annotations(self, page: pikepdf.Page) -> None:
        annots = page.obj.get("/Annots")
        if annots is None:
            return
        keep = pikepdf.Array()
        for a in annots:
            r = a.get("/Rect")
            if r is not None:
                x0, y0, x1, y1 = (_num(v) for v in r)
                ar = (min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1))
                if any(intersects(ar, rr) for rr in self.rects):
                    self.stats.annotations_removed += 1
                    if a.get("/Subtype") == "/Widget":
                        a.V = pikepdf.String("")  # clear the field value as well
                    continue
            keep.append(a)
        page.obj.Annots = keep

    # -- core interpreter ---------------------------------------------------------
    def _process(self, owner: pikepdf.Object, resources: Any, base: Matrix, depth: int
                 ) -> tuple[list[Any], bool]:
        ops = pikepdf.parse_content_stream(owner)
        out: list[Any] = []
        changed = False
        gs = GState(ctm=base)
        stack: list[GState] = []
        path_start: int | None = None
        path_pts: list[tuple[float, float]] = []

        for operands, op in ops:
            o = str(op)
            if o == "q":
                stack.append(GState(gs.ctm, TextState(**vars(gs.text))))
            elif o == "Q":
                gs = stack.pop() if stack else GState(ctm=base)
            elif o == "cm" and len(operands) == 6:
                gs.ctm = mul(tuple(_num(v) for v in operands), gs.ctm)  # type: ignore[arg-type]
            elif o == "BT":
                gs.text.tm = gs.text.tlm = IDENTITY
            elif o == "Tf" and len(operands) == 2:
                gs.text.font = self._font(resources, operands[0])
                gs.text.size = _num(operands[1])
            elif o == "Tc":
                gs.text.tc = _num(operands[0])
            elif o == "Tw":
                gs.text.tw = _num(operands[0])
            elif o == "Tz":
                gs.text.th = _num(operands[0]) / 100.0
            elif o == "TL":
                gs.text.tl = _num(operands[0])
            elif o == "Ts":
                gs.text.rise = _num(operands[0])
            elif o in ("Td", "TD"):
                tx, ty = _num(operands[0]), _num(operands[1])
                if o == "TD":
                    gs.text.tl = -ty
                gs.text.tlm = mul((1, 0, 0, 1, tx, ty), gs.text.tlm)
                gs.text.tm = gs.text.tlm
            elif o == "Tm" and len(operands) == 6:
                gs.text.tlm = gs.text.tm = tuple(_num(v) for v in operands)  # type: ignore[assignment]
            elif o == "T*":
                gs.text.tlm = mul((1, 0, 0, 1, 0, -gs.text.tl), gs.text.tlm)
                gs.text.tm = gs.text.tlm
            elif o in ("Tj", "TJ", "'", '"'):
                pre: list[Any] = []
                if o in ("'", '"'):
                    if o == '"':
                        gs.text.tw, gs.text.tc = _num(operands[0]), _num(operands[1])
                        pre.append(([operands[0]], Operator("Tw")))
                        pre.append(([operands[1]], Operator("Tc")))
                    gs.text.tlm = mul((1, 0, 0, 1, 0, -gs.text.tl), gs.text.tlm)
                    gs.text.tm = gs.text.tlm
                    pre.append(([], Operator("T*")))
                    items: list[Any] = [operands[-1]]
                elif o == "Tj":
                    items = [operands[0]]
                else:
                    items = list(operands[0])
                new_items, removed = self._show(gs, items)
                if removed:
                    changed = True
                    self.stats.glyphs_removed += removed
                    out.extend(pre)
                    if any(isinstance(x, pikepdf.String) and len(bytes(x)) for x in new_items):
                        out.append(([pikepdf.Array(new_items)], Operator("TJ")))
                        self.stats.text_ops_rewritten += 1
                    else:
                        # Nothing visible left: keep positioning by emitting the spacing only.
                        out.append(([pikepdf.Array(new_items or [0])], Operator("TJ")))
                        self.stats.text_ops_removed += 1
                    continue
            elif o in PATH_OPS:
                if path_start is None:
                    path_start = len(out)
                    path_pts = []
                nums = [_num(v) for v in operands]
                if o == "re" and len(nums) == 4:
                    x, y, w, h = nums
                    path_pts += [apply(gs.ctm, x, y), apply(gs.ctm, x + w, y + h)]
                else:
                    path_pts += [apply(gs.ctm, nums[i], nums[i + 1]) for i in range(0, len(nums) - 1, 2)]
            elif o in PAINT_OPS:
                if path_start is not None and path_pts and o != "n":
                    xs, ys = [p[0] for p in path_pts], [p[1] for p in path_pts]
                    pb = (min(xs), min(ys), max(xs), max(ys))
                    if any(overlap_fraction(pb, r) >= 0.9 for r in self.rects):
                        del out[path_start:]  # drop path construction + paint entirely
                        self.stats.paths_removed += 1
                        changed = True
                        path_start, path_pts = None, []
                        continue
                path_start, path_pts = None, []
            elif o == "INLINE IMAGE":
                box = bbox_of(gs.ctm, 0, 0, 1, 1)
                if any(intersects(box, r) for r in self.rects):
                    self.stats.inline_images_removed += 1
                    changed = True
                    continue
            elif o == "Do":
                repl = self._do(operands[0], resources, gs.ctm, depth)
                if repl is not None:
                    changed = True
                    out.append(([repl], op))
                    continue
            out.append((operands, op))
        return out, changed

    def _show(self, gs: GState, items: list[Any]) -> tuple[list[Any], int]:
        ts = gs.text
        fm = ts.font or FontMetrics(None)
        scale = ts.size * ts.th
        trm_base = mul(ts.tm, gs.ctm)
        removed = 0
        new: list[Any] = []
        buf = b""
        x = 0.0  # advance in text space since the start of this operator
        for it in items:
            if isinstance(it, pikepdf.String):
                for code in fm.codes(bytes(it)):
                    w0 = fm.width(code)
                    adv = (w0 * ts.size + ts.tc + (ts.tw if code == b" " and not fm.two_byte else 0.0)) * ts.th
                    # Glyph centre in text space (mid advance, ~0.35 em above baseline incl. rise).
                    cx, cy = apply(trm_base, x + adv / 2.0, ts.rise + 0.35 * ts.size)
                    if point_in(self.rects, cx, cy) and code != b" ":
                        if buf:
                            new.append(pikepdf.String(buf))
                            buf = b""
                        if scale:
                            new.append(-adv * 1000.0 / scale)
                        removed += 1
                    else:
                        buf += code
                    x += adv
            else:
                n = _num(it)
                if buf:
                    new.append(pikepdf.String(buf))
                    buf = b""
                new.append(n)
                x += -n / 1000.0 * scale
        if buf:
            new.append(pikepdf.String(buf))
        ts.tm = mul((1, 0, 0, 1, x, 0), ts.tm)
        return new, removed

    def _do(self, name: Any, resources: Any, ctm: Matrix, depth: int) -> Name | None:
        xobjs = resources.get("/XObject") if resources is not None else None
        xo = xobjs.get(name) if xobjs is not None else None
        if xo is None:
            return None
        sub = xo.get("/Subtype")
        if sub == "/Image":
            box = bbox_of(ctm, 0, 0, 1, 1)
            hits = [r for r in self.rects if intersects(box, r)]
            if not hits:
                return None
            new_img = self._redact_image(xo, ctm, hits)
            if new_img is None:
                return None
            nm = self._fresh("Im")
            xobjs[nm] = new_img
            self.stats.images_modified += 1
            return nm
        if sub == "/Form" and depth < 8:
            fm = tuple(_num(v) for v in xo.get("/Matrix", [1, 0, 0, 1, 0, 0]))
            fctm = mul(fm, ctm)  # type: ignore[arg-type]
            bb = xo.get("/BBox")
            if bb is not None:
                fb = bbox_of(fctm, *(_num(v) for v in bb))
                if not any(intersects(fb, r) for r in self.rects):
                    return None
            fres = xo.get("/Resources")
            fres = pikepdf.Dictionary({k: v for k, v in fres.items()}) if fres is not None else pikepdf.Dictionary(
                {k: v for k, v in resources.items()})
            if "/XObject" in fres:
                fres.XObject = pikepdf.Dictionary({k: v for k, v in fres.XObject.items()})
            ops, changed = self._process(xo, fres, fctm, depth + 1)
            if not changed:
                return None
            copy = self.pdf.make_stream(pikepdf.unparse_content_stream(ops))
            for k, v in xo.items():
                if k not in ("/Length", "/Filter", "/DecodeParms", "/Resources"):
                    copy[k] = v
            copy.Resources = fres
            nm = self._fresh("Fm")
            xobjs[nm] = copy
            self.stats.forms_rewritten += 1
            return nm
        return None

    def _redact_image(self, xo: pikepdf.Stream, ctm: Matrix, hits: list[Rect]) -> pikepdf.Stream | None:
        inv = invert(ctm)
        if inv is None:
            return None
        try:
            pil = PdfImage(xo).as_pil_image()
        except Exception:
            # Undecodable image: replace it with an opaque black image of the same size.
            pil = Image.new("L", (int(xo.get("/Width", 1)), int(xo.get("/Height", 1))), 255)
        if pil.mode not in ("L", "RGB", "CMYK"):
            pil = pil.convert("RGB")
        pil = pil.copy()
        w, h = pil.size
        black = {"L": 0, "RGB": (0, 0, 0), "CMYK": (0, 0, 0, 255)}[pil.mode]
        draw = ImageDraw.Draw(pil)
        for r in hits:
            poly = []
            for px, py in ((r[0], r[1]), (r[2], r[1]), (r[2], r[3]), (r[0], r[3])):
                u, v = apply(inv, px, py)
                poly.append((u * w, (1 - v) * h))
            draw.polygon(poly, fill=black)
        data = pil.tobytes()
        new = self.pdf.make_stream(zlib.compress(data, 6))
        new.Type = Name.XObject
        new.Subtype = Name.Image
        new.Width, new.Height = w, h
        new.BitsPerComponent = 8
        new.ColorSpace = {"L": Name.DeviceGray, "RGB": Name.DeviceRGB, "CMYK": Name.DeviceCMYK}[pil.mode]
        new.Filter = Name.FlateDecode
        if "/SMask" in xo:
            new.SMask = xo.SMask
        if "/Interpolate" in xo:
            new.Interpolate = xo.Interpolate
        return new


def image_bytes_png(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()
