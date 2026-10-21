"""Per-page analysis pipeline producing reviewable regions, with checkpointing per page."""

from __future__ import annotations

import logging
import re
import statistics
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import pypdfium2 as pdfium
import pypdfium2.raw as pdfium_c
from pdfminer.high_level import extract_pages
from pdfminer.layout import LAParams, LTChar, LTTextBox, LTTextLine

from app.core.config import get_settings
from app.core.files import ensure_within
from app.db.models import OcrPage, OcrSession
from app.db.session import session_scope
from app.services import audit
from app.services.ocr.classify import classify_page
from app.services.ocr.engine import Word, ocr_words
from app.services.ocr.layout import BBox, Region, _merge_boxes, build_regions
from app.services.ocr.preprocess import preprocess
from app.services.render import _pdfium_lock, render_bgr

log = logging.getLogger("pdfws.ocr")

LOW_CONF = 70.0
WS_RE = re.compile(r"[\x00-\x1f\x7f \u00a0\u2000-\u200b]+")
PAR_END_RE = re.compile(r"[.!?:»”\"…)]$")
REVIEW_DPI_DIGITAL = 200


def session_dir(session_id: uuid.UUID) -> Path:
    s = get_settings()
    return ensure_within(s.ocr_dir, s.ocr_dir / str(session_id))


def _norm_bbox(b: BBox, w: int, h: int) -> list[float]:
    return [round(max(0.0, b[0] / w), 5), round(max(0.0, b[1] / h), 5),
            round(min(1.0, b[2] / w), 5), round(min(1.0, b[3] / h), 5)]


def _save_crop(img: np.ndarray, b: BBox, dest: Path, kind: str) -> tuple[str, int, int]:
    h, w = img.shape[:2]
    pad = int(0.006 * max(w, h))
    x0, y0 = max(0, int(b[0]) - pad), max(0, int(b[1]) - pad)
    x1, y1 = min(w, int(b[2]) + pad), min(h, int(b[3]) + pad)
    crop = img[y0:y1, x0:x1]
    if kind == "photo":
        path = dest.with_suffix(".jpg")
        cv2.imwrite(str(path), crop, [cv2.IMWRITE_JPEG_QUALITY, 92])
    else:
        path = dest.with_suffix(".png")
        cv2.imwrite(str(path), crop, [cv2.IMWRITE_PNG_COMPRESSION, 6])
    return path.name, crop.shape[1], crop.shape[0]


def serialize(regions: list[Region], page_index: int, img: np.ndarray, assets_dir: Path, source: str,
              native_assets: dict[int, tuple[str, int, int]] | None = None) -> list[dict[str, Any]]:
    h, w = img.shape[:2]
    ids = {id(r): f"p{page_index + 1}-r{k + 1}" for k, r in enumerate(regions)}
    out: list[dict[str, Any]] = []
    for k, r in enumerate(regions):
        d: dict[str, Any] = {
            "id": ids[id(r)], "role": r.role, "order": r.order, "bbox": _norm_bbox(r.bbox, w, h),
            "text": r.text, "corrected_text": None, "excluded": False, "source": source,
            "conf": round(r.conf, 1), "heading_level": r.heading_level,
            "low_confidence": source == "ocr" and bool(r.text) and r.conf < LOW_CONF,
            "lines": [{"bbox": _norm_bbox(ln.bbox, w, h), "text": ln.text, "conf": round(ln.conf, 1)}
                      for ln in r.lines],
        }
        if r.role == "caption" and r.caption_target is not None:
            d["caption_for"] = ids.get(id(r.caption_target))
        if r.figure is not None:
            kind = r.figure["kind"]
            fallback = kind in ("table", "formula") or r.figure.get("rendered", False)
            if native_assets and k in native_assets:
                name, iw, ih = native_assets[k]
                method = "embedded"
            else:
                name, iw, ih = _save_crop(img, r.bbox, assets_dir / f"p{page_index + 1:04d}-r{k + 1:02d}", kind)
                method = "rendered" if source == "text_layer" else "scan_crop"
            d["figure"] = {"kind": kind, "asset": f"assets/{name}", "width": iw, "height": ih,
                           "labels_text": " ".join(r.figure.get("labels", []))[:2000], "fallback": fallback,
                           "method": method}
            d["text"] = d["text"] if r.role == "formula" else ""
        out.append(d)
    return out


# --------------------------------------------------------------------------- scanned pages

def analyze_scanned(path: Path, page_index: int, sdir: Path, cls: dict[str, Any],
                    options: dict[str, Any], workdir: Path) -> dict[str, Any]:
    s = get_settings()
    dpi = int(options.get("dpi") or s.ocr_dpi)
    color = render_bgr(path, page_index, dpi)
    pages_dir = sdir / "pages"
    pages_dir.mkdir(parents=True, exist_ok=True)
    src_name = f"p{page_index + 1:04d}-source.jpg"
    cv2.imwrite(str(pages_dir / src_name), color, [cv2.IMWRITE_JPEG_QUALITY, 90])
    prep = preprocess(color, workdir, options.get("preprocess"), photographed=bool(cls.get("photographed_hint")))
    del color
    words = ocr_words(prep.ocr, workdir, langs=options.get("languages"), psm=int(options.get("psm", 3)))
    if prep.ocr_scale != 1.0:
        f = 1.0 / prep.ocr_scale
        words = [Word(wd.text, wd.conf, int(wd.x0 * f), int(wd.y0 * f), int(wd.x1 * f), int(wd.y1 * f),
                      wd.block, wd.par, wd.line) for wd in words]
    from app.services.ocr.layout import detect_visual_regions

    visual = detect_visual_regions(prep.color, words)
    h, w = prep.color.shape[:2]
    regions = build_regions(words, visual, (w, h))
    proc_name = f"p{page_index + 1:04d}-processed.jpg"
    cv2.imwrite(str(pages_dir / proc_name), prep.color, [cv2.IMWRITE_JPEG_QUALITY, 90])
    assets = sdir / "assets"
    assets.mkdir(exist_ok=True)
    data = serialize(regions, page_index, prep.color, assets, "ocr")
    return {"regions": data, "preprocessing": {**prep.steps, "dpi": dpi},
            "source_image": f"pages/{src_name}", "processed_image": f"pages/{proc_name}", "image_size": [w, h],
            "words": len(words)}


# --------------------------------------------------------------------------- digital pages

def _to_display(x: float, y: float, w: float, h: float, rot: int) -> tuple[float, float]:
    """Unrotated PDF user coords -> normalised display coords (top-left origin)."""
    if rot == 90:
        return y / h, x / w
    if rot == 180:
        return (w - x) / w, y / h
    if rot == 270:
        return (h - y) / h, 1 - x / w
    return x / w, 1 - y / h


def analyze_digital(path: Path, page_index: int, sdir: Path, cls: dict[str, Any], workdir: Path
                    ) -> dict[str, Any]:
    dpi = REVIEW_DPI_DIGITAL
    color = render_bgr(path, page_index, dpi)
    ih, iw = color.shape[:2]
    pages_dir = sdir / "pages"
    assets = sdir / "assets"
    pages_dir.mkdir(parents=True, exist_ok=True)
    assets.mkdir(exist_ok=True)
    src_name = f"p{page_index + 1:04d}-source.jpg"
    cv2.imwrite(str(pages_dir / src_name), color, [cv2.IMWRITE_JPEG_QUALITY, 90])

    # Text from the existing layer with pdfminer layout analysis (display coordinates).
    words: list[Word] = []
    layout = next(extract_pages(str(path), page_numbers=[page_index],
                                laparams=LAParams(line_margin=0.5, boxes_flow=0.5)))
    lw, lh = layout.width, layout.height
    sx, sy = iw / lw, ih / lh
    boxes: list[list[tuple[str, float, tuple[float, float, float, float]]]] = []
    for el in layout:
        if not isinstance(el, LTTextBox):
            continue
        lines = []
        for line in el:
            if not isinstance(line, LTTextLine):
                continue
            text = WS_RE.sub(" ", line.get_text()).strip()  # PDFs often encode word gaps as tabs
            if not text:
                continue
            sizes = [c.size for c in line if isinstance(c, LTChar)]
            lines.append((text, statistics.median(sizes) if sizes else line.height, line.bbox))
        if lines:
            boxes.append(lines)
    # Body-text left margin of the page: the most common line start.
    starts = [round(b[0]) for lines in boxes for _, _, b in lines]
    margin = max(set(starts), key=starts.count) if starts else 0.0
    for block, lines in enumerate(boxes, start=1):
        right = max(b[2] for _, _, b in lines)
        par = 0
        for ln_no, (text, size, (x0, _y0, x1, y1)) in enumerate(lines, start=1):
            if ln_no > 1:
                prev_text, _, prev_box = lines[ln_no - 2]
                ended = bool(PAR_END_RE.search(prev_text))
                indented = x0 - margin > 0.8 * size  # first-line indent starts a new paragraph
                prev_short = prev_box[2] < right - 2.5 * size and ended
                dialogue = ended and text[:1] in "—«\"“"
                if indented or prev_short or dialogue:
                    par += 1
            top = (lh - y1) * sy
            words.append(Word(text, 100.0, int(x0 * sx), int(top), int(x1 * sx), int(top + size * sy),
                              block, par, ln_no))

    # Embedded images (extracted natively) and vector drawings (rendered crops).
    visual: list[dict[str, Any]] = []
    native: list[tuple[BBox, Path]] = []
    rot = int(cls.get("rotation", 0))
    with _pdfium_lock:
        doc = pdfium.PdfDocument(str(path))
        try:
            page = doc[page_index]
            pw, ph = page.get_size()
            path_boxes: list[BBox] = []
            k = 0
            for obj in page.get_objects(max_depth=4):
                l_, b_, r_, t_ = obj.get_bounds()
                ax, ay = _to_display(l_, t_, pw, ph, rot)
                bx, by = _to_display(r_, b_, pw, ph, rot)
                box = (min(ax, bx) * iw, min(ay, by) * ih, max(ax, bx) * iw, max(ay, by) * ih)
                frac = (box[2] - box[0]) * (box[3] - box[1]) / (iw * ih)
                if obj.type == pdfium_c.FPDF_PAGEOBJ_IMAGE and frac >= 0.01:
                    k += 1
                    prefix = workdir / f"native-{page_index}-{k}"
                    try:
                        obj.extract(str(prefix), fb_format="png")
                        found = sorted(workdir.glob(f"native-{page_index}-{k}.*"))
                    except Exception:
                        found = []
                    visual.append({"bbox": box, "kind": "photo" if found and found[0].suffix in (".jpg", ".jpeg")
                                   else "illustration"})
                    native.append((box, found[0] if found else Path()))
                elif obj.type == pdfium_c.FPDF_PAGEOBJ_PATH:
                    if (box[2] - box[0]) > 2 and (box[3] - box[1]) > 2:
                        path_boxes.append(box)
            page.close()
        finally:
            doc.close()
    for vb in _merge_boxes(path_boxes, gap=0.01 * max(iw, ih)):
        frac = (vb[2] - vb[0]) * (vb[3] - vb[1]) / (iw * ih)
        if frac >= 0.015 and (vb[3] - vb[1]) > 0.03 * ih and (vb[2] - vb[0]) > 0.05 * iw and frac < 0.9:
            if not any(_contains(v["bbox"], vb) for v in visual):
                visual.append({"bbox": vb, "kind": "illustration", "rendered": True})

    regions = build_regions(words, visual, (iw, ih))
    for r in regions:
        if r.figure is not None:
            match = next((v for v in visual if v["bbox"] == r.bbox), None)
            if match and match.get("rendered"):
                r.figure["rendered"] = True
    native_assets: dict[int, tuple[str, int, int]] = {}
    for k, r in enumerate(regions):
        if r.role != "figure":
            continue
        for box, f in native:
            if box == r.bbox and f.name:
                dest = assets / f"p{page_index + 1:04d}-r{k + 1:02d}{f.suffix.lower()}"
                f.replace(dest)
                try:
                    import PIL.Image

                    with PIL.Image.open(dest) as im:
                        native_assets[k] = (dest.name, im.width, im.height)
                except Exception:
                    dest.unlink(missing_ok=True)
    data = serialize(regions, page_index, color, assets, "text_layer", native_assets)
    return {"regions": data, "preprocessing": {"dpi": dpi, "text_layer": True}, "source_image": f"pages/{src_name}",
            "processed_image": f"pages/{src_name}", "image_size": [iw, ih], "words": len(words)}


def _contains(outer: BBox, inner: BBox) -> bool:
    return outer[0] <= inner[0] + 2 and outer[1] <= inner[1] + 2 and outer[2] >= inner[2] - 2 and \
        outer[3] >= inner[3] - 2


# --------------------------------------------------------------------------- session

def page_stats(regions: list[dict[str, Any]]) -> tuple[float | None, bool]:
    flow = [r for r in regions if r.get("text") and r["source"] == "ocr" and r["role"] not in ("figure", "table")]
    if not flow:
        return (100.0 if regions and all(r["source"] == "text_layer" for r in regions) else None), False
    total = sum(len(r["text"]) for r in flow) or 1
    mean = sum(r["conf"] * len(r["text"]) for r in flow) / total
    return round(mean, 1), mean < 80 or any(r["conf"] < 60 for r in flow)


def analyze_page(path: Path, page_index: int, sdir: Path, options: dict[str, Any], workdir: Path,
                 force_ocr: bool = False) -> dict[str, Any]:
    cls = classify_page(path, page_index)
    if cls["needs_ocr"] or force_ocr:
        res = analyze_scanned(path, page_index, sdir, cls, options, workdir)
        res["mode"] = "ocr"
    else:
        res = analyze_digital(path, page_index, sdir, cls, workdir)
        res["mode"] = "text_layer"
    mean, review = page_stats(res["regions"])
    flow_chars = sum(len(r["text"]) for r in res["regions"] if r["role"] not in ("figure", "table"))
    cls["fallback_recommended"] = res["mode"] == "ocr" and mean is not None and mean < 50 and flow_chars > 0
    res.update({"classification": cls, "mean_confidence": mean, "needs_review": review})
    return res


def run_session(ctx: Any, session_id: uuid.UUID, pages: list[int] | None, force: bool,
                options_override: dict[str, Any] | None = None) -> dict[str, Any]:
    """Analyse pages, committing each page as a checkpoint so interrupted jobs resume where they stopped."""
    sdir = session_dir(session_id)
    sdir.mkdir(parents=True, exist_ok=True)
    with session_scope() as s:
        sess = s.get(OcrSession, session_id)
        assert sess is not None
        options = {**(sess.settings or {}), **(options_override or {})}
        total = sess.page_count
        done = {p.page_index for p in s.query(OcrPage).filter(OcrPage.session_id == session_id,
                                                              OcrPage.status == "done")}
        sess.status = "processing"
    todo = pages if pages is not None else list(range(total))
    failures = 0
    for k, pi in enumerate(todo):
        if pi in done and not force:
            continue
        ctx.stage("analysing pages", k, len(todo))
        try:
            res = analyze_page(ctx.input.path, pi, sdir, options, ctx.workdir / f"p{pi}",
                               force_ocr=bool(options.get("force_ocr")))
            with session_scope() as s:
                s.merge(OcrPage(session_id=session_id, page_index=pi, status="done",
                                classification=res["classification"], preprocessing=res["preprocessing"],
                                regions=res["regions"], mean_confidence=res["mean_confidence"],
                                needs_review=res["needs_review"], reviewed=False,
                                source_image=res["source_image"], processed_image=res["processed_image"],
                                image_size=res["image_size"], error=None, updated_at=datetime.now(UTC)))
        except Exception as exc:  # record the page failure and continue; never drop pages silently
            from app.core.errors import AppError, ToolUnavailable

            if isinstance(exc, ToolUnavailable):
                raise
            log.exception("OCR failed on page %s", pi + 1)
            failures += 1
            msg = exc.message if isinstance(exc, AppError) else f"{type(exc).__name__}"
            with session_scope() as s:
                s.merge(OcrPage(session_id=session_id, page_index=pi, status="failed", regions=[],
                                error=msg[:500], needs_review=True, updated_at=datetime.now(UTC)))
            ctx.warn(f"Page {pi + 1} could not be analysed ({msg}); it will be kept as a page image.")
        ctx.check_cancel()
    ctx.stage("summarising", len(todo), len(todo))
    summary = summarize(session_id)
    with session_scope() as s:
        sess = s.get(OcrSession, session_id)
        assert sess is not None
        sess.summary = summary
        if sess.status != "approved":
            sess.status = "review"
        audit.record("ocr.completed", "warning" if failures else "success", session=s, job_id=ctx.job_id,
                     document_id=sess.document_id, version_id=sess.version_id, operation="ocr_analyze",
                     input_sha256=ctx.input.sha256,
                     details={"pages": summary["pages"], "failed_pages": failures,
                              "ocr_pages": summary["ocr_pages"], "mean_confidence": summary["mean_confidence"]})
    return summary


def summarize(session_id: uuid.UUID) -> dict[str, Any]:
    with session_scope() as s:
        pages = s.query(OcrPage).filter(OcrPage.session_id == session_id).order_by(OcrPage.page_index).all()
        roles: dict[str, int] = {}
        confs = []
        for p in pages:
            for r in p.regions or []:
                roles[r["role"]] = roles.get(r["role"], 0) + 1
            if p.mean_confidence is not None and (p.classification or {}).get("needs_ocr"):
                confs.append(p.mean_confidence)
        return {
            "pages": len(pages),
            "done": sum(1 for p in pages if p.status == "done"),
            "failed": [p.page_index + 1 for p in pages if p.status == "failed"],
            "ocr_pages": sum(1 for p in pages if (p.classification or {}).get("needs_ocr")),
            "text_layer_pages": sum(1 for p in pages if p.status == "done"
                                    and not (p.classification or {}).get("needs_ocr")),
            "needs_review": [p.page_index + 1 for p in pages if p.needs_review and not p.reviewed],
            "fallback_recommended": [p.page_index + 1 for p in pages
                                     if (p.classification or {}).get("fallback_recommended")],
            "mean_confidence": round(sum(confs) / len(confs), 1) if confs else None,
            "regions_by_role": roles,
        }
