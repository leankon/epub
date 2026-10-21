"""Layout analysis: regions, roles and reading order.

Strategy (documented in docs/architecture.md):
* Tesseract provides word/line/paragraph segmentation (it already separates columns into blocks).
* OpenCV finds non-text visual regions: ink not explained by recognised words, and mid-tone areas
  typical of photographs. These become figure regions; words inside them become figure labels and
  are kept out of the main reading flow, which prevents text duplication.
* Ruled grids become table regions; symbol-dense lines become formula regions. Both are preserved
  as image crops (visual fallback) because their structure cannot be rebuilt reliably.
* Paragraph roles come from geometry and typography relative to the page's body text size.
* Reading order uses a recursive XY-cut that prefers column splits, which avoids interleaving
  lines of adjacent columns.
"""

from __future__ import annotations

import re
import statistics
from dataclasses import dataclass, field
from typing import Any

import cv2
import numpy as np

from app.services.ocr.engine import Word

BBox = tuple[float, float, float, float]  # x0, y0, x1, y1 in pixels (top-left origin)

CAPTION_RE = re.compile(r"^\s*(fig(ura|ure)?\.?|imagen|image|l[áa]mina|plate|tabla|table|ilustraci[óo]n|"
                        r"illustration|foto(graf[íi]a)?|photo|gr[áa]fico|chart|diagrama|diagram|mapa|map)\b",
                        re.IGNORECASE)
CHAPTER_RE = re.compile(r"^\s*(cap[íi]tulo|chapter|parte|part|libro|book|pr[óo]logo|prologue|ep[íi]logo|"
                        r"epilogue|introducci[óo]n|introduction|ap[ée]ndice|appendix)\b", re.IGNORECASE)
PAGENUM_RE = re.compile(r"^[\W_]*(\d{1,4}|[ivxlcdm]{1,7})[\W_]*$", re.IGNORECASE)
LIST_RE = re.compile(r"^\s*([•·▪‣◦\-–—*]|\(?\d{1,2}[.)]|\(?[a-z][.)])\s+", re.IGNORECASE)
FOOTNOTE_RE = re.compile(r"^\s*(\d{1,3}|[*†‡§¹²³⁴⁵⁶⁷⁸⁹⁰]+)[\s.)]")
CAPS_HEADING_RE = re.compile(r"^[A-ZÁÉÍÓÚÜÑÇÀÈÌÒÙ0-9(][A-ZÁÉÍÓÚÜÑÇÀÈÌÒÙ0-9 .,:'()\-–]{0,39}$")
MATH_CHARS = set("=+−×÷∑∫√^<>±≤≥∞πθλμσ∂∆∇≈≠∈∀∃→⇒")


@dataclass
class Line:
    words: list[Word]

    @property
    def text(self) -> str:
        return " ".join(w.text for w in self.words)

    @property
    def bbox(self) -> BBox:
        return (min(w.x0 for w in self.words), min(w.y0 for w in self.words),
                max(w.x1 for w in self.words), max(w.y1 for w in self.words))

    @property
    def height(self) -> float:
        return statistics.median([w.h for w in self.words])

    @property
    def conf(self) -> float:
        total = sum(len(w.text) for w in self.words) or 1
        return sum(w.conf * len(w.text) for w in self.words) / total


@dataclass
class Region:
    role: str
    bbox: BBox
    lines: list[Line] = field(default_factory=list)
    text: str = ""
    conf: float = 100.0
    heading_level: int | None = None
    figure: dict[str, Any] | None = None
    order: int = 0
    font_size: float = 0.0
    caption_target: Region | None = None  # figure/table region a caption belongs to


def join_lines(lines: list[str]) -> str:
    """Join OCR lines into a paragraph, undoing end-of-line hyphenation."""
    out = ""
    for ln in lines:
        ln = ln.strip()
        if not ln:
            continue
        if out.endswith("-") and len(out) > 1 and out[-2].isalpha() and ln[:1].islower():
            out = out[:-1] + ln
        elif out.endswith("¬"):
            out = out[:-1] + ln
        else:
            out = f"{out} {ln}" if out else ln
    return re.sub(r"\s{2,}", " ", out)


def union(boxes: list[BBox]) -> BBox:
    return (min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes))


def overlap(a: BBox, b: BBox) -> float:
    w = min(a[2], b[2]) - max(a[0], b[0])
    h = min(a[3], b[3]) - max(a[1], b[1])
    return max(0.0, w) * max(0.0, h)


def area(b: BBox) -> float:
    return max(0.0, b[2] - b[0]) * max(0.0, b[3] - b[1])


def contains_center(outer: BBox, inner: BBox) -> bool:
    cx, cy = (inner[0] + inner[2]) / 2, (inner[1] + inner[3]) / 2
    return outer[0] <= cx <= outer[2] and outer[1] <= cy <= outer[3]


# --------------------------------------------------------------------------- visual regions

def _text_mask(shape: tuple[int, int], words: list[Word], med_h: float) -> np.ndarray:
    mask = np.zeros(shape, np.uint8)
    for w in words:
        if w.conf >= 30 and any(ch.isalnum() for ch in w.text) and 0.4 * med_h <= w.h <= 3.5 * med_h:
            pad = int(max(2, w.h * 0.25))
            cv2.rectangle(mask, (w.x0 - pad, w.y0 - pad), (w.x1 + pad, w.y1 + pad), 255, -1)
    return mask


def detect_visual_regions(color: np.ndarray, words: list[Word]) -> list[dict[str, Any]]:
    """Find photographs, illustrations and tables. Returns dicts with bbox and kind."""
    h, w = color.shape[:2]
    gray = cv2.cvtColor(color, cv2.COLOR_BGR2GRAY)
    med_h = statistics.median([wd.h for wd in words]) if words else max(10.0, h / 80)
    tmask = _text_mask((h, w), words, med_h)
    ink = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_MEAN_C, cv2.THRESH_BINARY_INV,
                                max(31, (min(h, w) // 30) | 1), 12)
    hsv = cv2.cvtColor(color, cv2.COLOR_BGR2HSV)
    midtone = ((gray > 45) & (gray < 210)).astype(np.uint8) * 255
    saturated = (hsv[:, :, 1] > 60).astype(np.uint8) * 255
    visual = cv2.bitwise_or(cv2.bitwise_and(ink, cv2.bitwise_not(tmask)),
                            cv2.bitwise_and(cv2.bitwise_or(midtone, saturated), cv2.bitwise_not(tmask)))
    visual = cv2.medianBlur(visual, 5)  # drop speckle noise
    k = max(9, int(min(h, w) * 0.02))
    closed = cv2.morphologyEx(visual, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_RECT, (k, k)))
    contours, _ = cv2.findContours(closed, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    page_area = h * w
    cands: list[BBox] = []
    for c in contours:
        x, y, cw, ch = cv2.boundingRect(c)
        if cw * ch < 0.012 * page_area or cw < 0.06 * w or ch < 0.04 * h:
            continue
        if cw * ch > 0.97 * page_area:
            continue  # the paper border of a photographed page, not an illustration
        fill = float(np.count_nonzero(visual[y:y + ch, x:x + cw])) / (cw * ch)
        if fill < 0.04:
            continue
        cands.append((x, y, x + cw, y + ch))
    cands = _merge_boxes(cands, gap=k)

    regions: list[dict[str, Any]] = []
    for b in cands:
        x0, y0, x1, y1 = (int(v) for v in b)
        crop_g = gray[y0:y1, x0:x1]
        crop_ink = ink[y0:y1, x0:x1]
        mid_frac = float(np.count_nonzero(midtone[y0:y1, x0:x1])) / max(1, crop_g.size)
        sat_frac = float(np.count_nonzero(saturated[y0:y1, x0:x1])) / max(1, crop_g.size)
        kind = "photo" if (mid_frac > 0.35 or sat_frac > 0.25) else "illustration"
        if kind == "illustration" and _is_table(crop_ink):
            kind = "table"
        inside = [wd for wd in words if contains_center(b, (wd.x0, wd.y0, wd.x1, wd.y1))]
        text_area = sum((wd.x1 - wd.x0) * (wd.y1 - wd.y0) for wd in inside if wd.conf >= 60)
        # A "figure" that is mostly confidently recognised text is a text block; drop it.
        if kind != "table" and text_area > 0.55 * area(b) and mid_frac < 0.2:
            continue
        regions.append({"bbox": b, "kind": kind, "midtone": round(mid_frac, 3), "colour": round(sat_frac, 3)})
    return regions


def _is_table(ink: np.ndarray) -> bool:
    h, w = ink.shape
    if h < 40 or w < 40:
        return False
    hor = cv2.morphologyEx(ink, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (max(20, w // 4), 1)))
    ver = cv2.morphologyEx(ink, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (1, max(20, h // 4))))
    n_h = len(cv2.findContours(hor, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)[0])
    n_v = len(cv2.findContours(ver, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)[0])
    joints = cv2.bitwise_and(hor, ver)
    n_j = len(cv2.findContours(joints, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)[0])
    return n_h >= 3 and n_v >= 2 and n_j >= 6


def _merge_boxes(boxes: list[BBox], gap: float) -> list[BBox]:
    boxes = list(boxes)
    changed = True
    while changed:
        changed = False
        out: list[BBox] = []
        while boxes:
            b = boxes.pop()
            for i, o in enumerate(out):
                if (b[0] - gap < o[2] and o[0] - gap < b[2] and b[1] - gap < o[3] and o[1] - gap < b[3]):
                    out[i] = union([b, o])
                    changed = True
                    break
            else:
                out.append(b)
        boxes = out
    return boxes


# --------------------------------------------------------------------------- text regions

def group_paragraphs(words: list[Word]) -> list[list[Line]]:
    pars: dict[tuple[int, int], dict[int, list[Word]]] = {}
    for w in words:
        pars.setdefault((w.block, w.par), {}).setdefault(w.line, []).append(w)
    out: list[list[Line]] = []
    for key in sorted(pars):
        lines = [Line(sorted(ws, key=lambda w: w.x0)) for _, ws in sorted(pars[key].items())]
        out.append(lines)
    return out


def _is_math(text: str) -> bool:
    t = text.replace(" ", "")
    if len(t) < 4:
        return False
    m = sum(1 for ch in t if ch in MATH_CHARS)
    return m / len(t) >= 0.15 and m >= 2


def build_regions(words: list[Word], visual: list[dict[str, Any]], size: tuple[int, int]) -> list[Region]:
    """Combine words and visual regions into typed regions in reading order."""
    w_img, h_img = size
    figures = [Region(role="table" if v["kind"] == "table" else "figure", bbox=v["bbox"],
                      figure={"kind": v["kind"], "labels": []}) for v in visual]
    flow_words: list[Word] = []
    for wd in words:
        wb = (wd.x0, wd.y0, wd.x1, wd.y1)
        host = next((f for f in figures if contains_center(f.bbox, wb)), None)
        if host is not None:
            host.figure["labels"].append(wd.text)  # type: ignore[index]
        else:
            flow_words.append(wd)

    paragraphs = group_paragraphs(flow_words)
    all_lines = [ln for p in paragraphs for ln in p]
    body_h = _body_height(all_lines)
    regions: list[Region] = []
    for lines in paragraphs:
        # Split off symbol-dense lines as formula regions.
        buf: list[Line] = []
        for ln in lines:
            if _is_math(ln.text):
                if buf:
                    regions.append(_text_region(buf))
                    buf = []
                regions.append(Region(role="formula", bbox=ln.bbox, lines=[ln], text=ln.text, conf=ln.conf,
                                      figure={"kind": "formula", "labels": []}))
            else:
                buf.append(ln)
        if buf:
            regions.append(_text_region(buf))

    for r in regions:
        if r.role == "paragraph":
            _assign_role(r, body_h, w_img, h_img, figures)
    regions.extend(figures)
    _link_captions(regions)
    return order_regions(regions, w_img, h_img, body_h)


def _text_region(lines: list[Line]) -> Region:
    total = sum(len(ln.text) for ln in lines) or 1
    conf = sum(ln.conf * len(ln.text) for ln in lines) / total
    return Region(role="paragraph", bbox=union([ln.bbox for ln in lines]), lines=lines,
                  text=join_lines([ln.text for ln in lines]), conf=conf,
                  font_size=statistics.median([ln.height for ln in lines]))


def _body_height(lines: list[Line]) -> float:
    if not lines:
        return 0.0
    weighted: list[float] = []
    for ln in lines:
        weighted.extend([ln.height] * max(1, len(ln.text) // 10))
    return statistics.median(weighted)


def _assign_role(r: Region, body_h: float, w: int, h: int, figures: list[Region]) -> None:
    text = r.text.strip()
    n_words = len(text.split())
    x0, y0, x1, y1 = r.bbox
    in_top, in_bottom = y1 < 0.085 * h, y0 > 0.915 * h
    if (in_top or in_bottom) and n_words <= 12 and PAGENUM_RE.match(text):
        r.role = "page_number"
        return
    if (in_top or in_bottom) and n_words <= 12 and (not body_h or r.font_size < 1.25 * body_h):
        r.role = "header" if in_top else "footer"
        return
    near_fig = any(_caption_distance(r.bbox, f.bbox, h) for f in figures)
    small = bool(body_h) and r.font_size < 0.92 * body_h
    if near_fig and ((CAPTION_RE.match(text) and len(r.lines) <= 4) or (small and len(r.lines) <= 2)):
        r.role = "caption"
        return
    if body_h and r.font_size >= 1.3 * body_h and n_words <= 18 and len(r.lines) <= 3:
        r.role = "heading"
        r.heading_level = 1 if r.font_size >= 1.75 * body_h else 2
        return
    if CHAPTER_RE.match(text) and n_words <= 10 and len(r.lines) <= 2:
        r.role, r.heading_level = "heading", 1
        return
    if len(r.lines) <= 2 and n_words <= 5 and CAPS_HEADING_RE.match(text) and sum(c.isalpha() for c in text) >= 2:
        r.role, r.heading_level = "heading", 2  # e.g. "AXEL", "ENERO", "(VERANO)" in novels
        return
    if (x1 - x0) < 0.2 * w and ((x1 < 0.16 * w) or (x0 > 0.84 * w)) and len(text) >= 20:
        r.role = "marginal"
        return
    if body_h and y0 > 0.65 * h and r.font_size < 0.86 * body_h and FOOTNOTE_RE.match(text):
        r.role = "footnote"
        return
    if LIST_RE.match(text) and len(r.lines) <= 6:
        r.role = "list_item"


def _caption_distance(tb: BBox, fb: BBox, h: int) -> bool:
    horiz = min(tb[2], fb[2]) - max(tb[0], fb[0]) > 0.3 * min(tb[2] - tb[0], fb[2] - fb[0])
    below = 0 <= tb[1] - fb[3] <= 0.05 * h
    above = 0 <= fb[1] - tb[3] <= 0.03 * h
    return horiz and (below or above)


def _link_captions(regions: list[Region]) -> None:
    figs = [i for i, r in enumerate(regions) if r.role in ("figure", "table")]
    for r in regions:
        if r.role != "caption":
            continue
        cy = (r.bbox[1] + r.bbox[3]) / 2
        best = min(figs, key=lambda i: abs(cy - (regions[i].bbox[1] + regions[i].bbox[3]) / 2), default=None)
        r.caption_target = regions[best] if best is not None else None


# --------------------------------------------------------------------------- reading order

def xy_cut(items: list[tuple[int, BBox]], min_vgap: float, min_hgap: float, depth: int = 0) -> list[int]:
    """Recursive XY-cut. Column (vertical) cuts take priority; horizontal cuts peel the top block."""
    if len(items) <= 1 or depth > 60:
        return [i for i, _ in sorted(items, key=lambda t: (t[1][1], t[1][0]))]
    boxes = [b for _, b in items]
    # Vertical cut: a gap in the x-projection not crossed by any box.
    xs = sorted((b[0], b[2]) for b in boxes)
    reach = xs[0][1]
    for x0, x1 in xs[1:]:
        if x0 - reach >= min_vgap:
            cut = (reach + x0) / 2
            left = [t for t in items if (t[1][0] + t[1][2]) / 2 < cut]
            right = [t for t in items if (t[1][0] + t[1][2]) / 2 >= cut]
            if left and right:
                return (xy_cut(left, min_vgap, min_hgap, depth + 1) +
                        xy_cut(right, min_vgap, min_hgap, depth + 1))
        reach = max(reach, x1)
    # Horizontal cut: split off the top-most band.
    ys = sorted((b[1], b[3]) for b in boxes)
    reach = ys[0][1]
    for y0, y1 in ys[1:]:
        if y0 - reach >= min_hgap:
            cut = (reach + y0) / 2
            top = [t for t in items if (t[1][1] + t[1][3]) / 2 < cut]
            rest = [t for t in items if (t[1][1] + t[1][3]) / 2 >= cut]
            if top and rest:
                return xy_cut(top, min_vgap, min_hgap, depth + 1) + xy_cut(rest, min_vgap, min_hgap, depth + 1)
        reach = max(reach, y1)
    return [i for i, _ in sorted(items, key=lambda t: (t[1][1], t[1][0]))]


def order_regions(regions: list[Region], w: int, h: int, body_h: float) -> list[Region]:
    top = [r for r in regions if r.role in ("header",) or (r.role == "page_number" and r.bbox[1] < h / 2)]
    bottom_marg = [r for r in regions if r.role in ("footer",) or (r.role == "page_number" and r.bbox[1] >= h / 2)]
    notes = [r for r in regions if r.role == "footnote"]
    captions = [r for r in regions if r.role == "caption"]
    excluded = {id(r) for r in top + bottom_marg + notes + captions}
    flow = [r for r in regions if id(r) not in excluded]
    order = xy_cut(list(enumerate(r.bbox for r in flow)), min_vgap=max(8.0, 0.012 * w),
                   min_hgap=max(4.0, 0.5 * body_h if body_h else 0.006 * h))
    ordered_flow: list[Region] = []
    for i in order:
        ordered_flow.append(flow[i])
        # Captions follow (or precede, if above) the figure they describe.
        for c in captions:
            if c.caption_target is flow[i]:
                ordered_flow.append(c)
    orphans = [c for c in captions if c not in ordered_flow]
    seq = sorted(top, key=lambda r: r.bbox[0]) + ordered_flow + orphans + \
        sorted(notes, key=lambda r: r.bbox[1]) + sorted(bottom_marg, key=lambda r: r.bbox[0])
    for k, r in enumerate(seq):
        r.order = k
    return seq
