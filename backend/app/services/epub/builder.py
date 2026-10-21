"""PDF-to-EPUB construction from analysed (and optionally corrected) pages.

Strategies:
  hybrid     (default) reflowable text + separate image assets; visual fallback only where needed
  reflowable           semantic XHTML everywhere; tables/formulas/drawings still kept as images
  fixed                fixed-layout pages: page image + positioned, selectable (transparent) text
"""

from __future__ import annotations

import html
import re
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ebooklib import epub
from PIL import Image

STRATEGIES = ("hybrid", "reflowable", "fixed")
SKIP_ROLES = {"header", "footer", "page_number"}
END_PUNCT = re.compile(r"[.!?:;»”\"')\]…]$")

CSS = """
body { font-family: serif; line-height: 1.5; margin: 0 4%; }
h1, h2, h3 { font-family: sans-serif; line-height: 1.25; page-break-after: avoid; }
h1 { font-size: 1.6em; margin: 1.2em 0 0.8em; }
h2 { font-size: 1.3em; margin: 1em 0 0.6em; }
p { margin: 0 0 0.6em; text-indent: 0; }
p.list-item { margin-left: 1.2em; }
figure { margin: 1em 0; text-align: center; page-break-inside: avoid; }
figure img { max-width: 100%; height: auto; }
figcaption, p.caption { font-size: 0.9em; font-style: italic; text-align: center; }
figure.fallback { border-top: 1px solid #ccc; border-bottom: 1px solid #ccc; padding: 0.4em 0; }
aside.marginal { font-size: 0.85em; margin: 0.4em 0 0.8em 1.5em; color: #333; }
aside.footnote, section.footnotes { font-size: 0.85em; }
section.footnotes { border-top: 1px solid #999; margin-top: 2em; padding-top: 0.5em; }
span.pagebreak { display: none; }
"""

FIXED_CSS = """
html, body { margin: 0; padding: 0; }
.page { position: relative; overflow: hidden; }
.page img.scan { position: absolute; left: 0; top: 0; width: 100%; height: 100%; }
.page span.t { position: absolute; color: transparent; white-space: pre; line-height: 1; }
"""


@dataclass
class Block:
    kind: str  # heading | para | list | caption | figure | marginal | pageimage
    page: int
    text: str = ""
    level: int = 1
    region_id: str = ""
    asset: str | None = None
    alt: str = ""
    caption: str = ""
    fallback: bool = False
    width: int = 0
    height: int = 0


@dataclass
class Chapter:
    title: str
    blocks: list[Block] = field(default_factory=list)
    footnotes: list[Block] = field(default_factory=list)
    start_page: int = 0


@dataclass
class BuildResult:
    path: Path
    report: dict[str, Any]
    expected_order: list[str]
    expected_images: int


XML_INVALID = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\ufffe\uffff]")


def _esc(s: str) -> str:
    return html.escape(XML_INVALID.sub(" ", s), quote=True)


def region_text(r: dict[str, Any]) -> str:
    t = r.get("corrected_text")
    return (t if t is not None else r.get("text") or "").strip()


def _snippet(text: str) -> str:
    words = text.split()
    return " ".join(words[:6])


def is_cover_page(p: dict[str, Any], page_total: int) -> bool:
    """A first page that is essentially one picture (little real text) is used as the book cover."""
    cls = p.get("classification") or {}
    text_chars = sum(len(region_text(r)) for r in p.get("regions") or [] if r.get("role") not in ("figure", "table"))
    return page_total > 1 and p.get("status") == "done" and cls.get("image_coverage", 0) >= 0.6 \
        and cls.get("chars", 0) < 200 and text_chars < 200 and bool(p.get("page_asset"))


def collect_blocks(pages: list[dict[str, Any]], strategy: str,
                   report: dict[str, Any]) -> tuple[list[Block], list[Block]]:
    """Flatten pages into a block stream; returns (flow blocks, footnotes)."""
    blocks: list[Block] = []
    notes: list[Block] = []
    for p in pages:
        pi = p["page_index"]
        regions = sorted(p.get("regions") or [], key=lambda r: r.get("order", 0))
        cls = p.get("classification") or {}
        if pi == 0 and is_cover_page(p, len(pages)):
            report["cover_asset"] = p.get("page_asset")
            continue
        page_fallback = p.get("status") != "done" or (
            strategy == "hybrid" and cls.get("fallback_recommended") and not p.get("reviewed"))
        if page_fallback:
            reason = p.get("error") or "low OCR confidence; page kept as an image (review the page to use text)"
            report["fallback_pages"].append({"page": pi + 1, "reason": reason})
            blocks.append(Block("pageimage", pi, asset=p.get("page_asset"), alt=f"Page {pi + 1}", fallback=True,
                                width=(p.get("image_size") or [0, 0])[0], height=(p.get("image_size") or [0, 0])[1]))
            continue
        by_id = {r["id"]: r for r in regions}
        captions = {r.get("caption_for"): r for r in regions if r["role"] == "caption" and r.get("caption_for")}
        for r in regions:
            role = r["role"]
            if r.get("excluded"):
                report["excluded_by_user"] += 1
                continue
            if role in SKIP_ROLES:
                report["removed_running_elements"][role] = report["removed_running_elements"].get(role, 0) + 1
                continue
            text = region_text(r)
            if role in ("figure", "table", "formula"):
                fig = r.get("figure") or {}
                cap = captions.get(r["id"])
                cap_text = region_text(cap) if cap and not cap.get("excluded") else ""
                alt = cap_text or (fig.get("labels_text") or "")[:300] or text[:300] or \
                    f"{fig.get('kind', 'image').capitalize()} from page {pi + 1}"
                b = Block("figure", pi, region_id=r["id"], asset=fig.get("asset"), alt=alt, caption=cap_text,
                          fallback=bool(fig.get("fallback")), width=fig.get("width", 0), height=fig.get("height", 0))
                blocks.append(b)
                report["images_retained"] += 1
                if b.fallback:
                    report["fallback_regions"].append({"page": pi + 1, "region": r["id"], "kind": fig.get("kind")})
                continue
            if not text:
                continue
            if role == "caption":
                if r.get("caption_for") in by_id and not by_id[r["caption_for"]].get("excluded"):
                    continue  # rendered inside its <figure>
                blocks.append(Block("caption", pi, text, region_id=r["id"]))
            elif role == "heading":
                level = r.get("heading_level") or 2
                prev = blocks[-1] if blocks else None
                if prev is not None and prev.kind == "heading" and prev.page == pi and prev.level == level \
                        and text.startswith("(") and len(prev.text) + len(text) < 80:  # "ENERO" + "(VERANO)"
                    prev.text = f"{prev.text} {text}"
                    continue
                blocks.append(Block("heading", pi, text, level=level, region_id=r["id"]))
            elif role == "footnote":
                notes.append(Block("para", pi, text, region_id=r["id"]))
                report["footnotes"] += 1
            elif role == "marginal":
                blocks.append(Block("marginal", pi, text, region_id=r["id"]))
            else:
                kind = "list" if role == "list_item" else "para"
                prev = blocks[-1] if blocks else None
                # Continue a paragraph split by a page or column break.
                if (kind == "para" and prev is not None and prev.kind == "para" and not END_PUNCT.search(prev.text)
                        and text[:1].islower()):
                    if prev.text.endswith("-") and prev.text[-2:-1].isalpha():
                        prev.text = prev.text[:-1] + text
                    else:
                        prev.text = f"{prev.text} {text}"
                    continue
                blocks.append(Block(kind, pi, text, region_id=r["id"]))
    return blocks, notes


def split_chapters(blocks: list[Block], notes: list[Block], user_chapters: list[dict[str, Any]] | None,
                   page_count: int) -> list[Chapter]:
    chapters: list[Chapter] = []
    if user_chapters:
        starts = sorted(((int(c["page"]) - 1, str(c.get("title") or f"Chapter {k + 1}"))
                         for k, c in enumerate(user_chapters)), key=lambda t: t[0])
        idx = 0
        current: Chapter | None = None
        for b in blocks:
            while idx < len(starts) and b.page >= starts[idx][0]:
                current = Chapter(starts[idx][1], start_page=starts[idx][0])
                chapters.append(current)
                idx += 1
            if current is None:
                current = Chapter("Front matter", start_page=0)
                chapters.append(current)
            current.blocks.append(b)
    else:
        counts: dict[int, int] = {}
        for b in blocks:
            if b.kind == "heading":
                counts[b.level] = counts.get(b.level, 0) + 1
        levels = sorted(counts)
        split_level = levels[0] if levels else None
        if len(levels) > 1 and counts[levels[0]] < 3 and counts[levels[1]] >= 3:
            split_level = levels[1]  # e.g. a single large "PROLOGUE" heading above many chapter headings
        for b in blocks:
            # Headings at (or above) the split level start a new chapter.
            if b.kind == "heading" and split_level is not None and b.level <= split_level \
                    and (not chapters or chapters[-1].blocks):
                chapters.append(Chapter(b.text[:120], start_page=b.page))
            elif not chapters:
                chapters.append(Chapter("Start", start_page=b.page))
            chapters[-1].blocks.append(b)
        if split_level is None and page_count > 20:  # no headings: chunk for manageable files
            flat = [b for c in chapters for b in c.blocks]
            chapters = []
            for b in flat:
                if not chapters or b.page - chapters[-1].start_page >= 15:
                    chapters.append(Chapter("", start_page=b.page))
                chapters[-1].blocks.append(b)
            for c in chapters:
                last = c.blocks[-1].page if c.blocks else c.start_page
                c.title = f"Pages {c.start_page + 1}–{last + 1}"
    for n in notes:  # footnotes go to the chapter containing their page
        target = next((c for c in reversed(chapters) if c.start_page <= n.page), chapters[0] if chapters else None)
        if target is not None:
            target.footnotes.append(n)
    return [c for c in chapters if c.blocks or c.footnotes]


def _xhtml(title: str, lang: str, body: str, css: str = "style/book.css", head_extra: str = "") -> bytes:
    return ('<?xml version="1.0" encoding="utf-8"?>\n<!DOCTYPE html>\n'
            f'<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" '
            f'lang="{_esc(lang)}" xml:lang="{_esc(lang)}">\n<head>\n<meta charset="utf-8"/>\n'
            f'<title>{_esc(title)}</title>\n{head_extra}<link rel="stylesheet" type="text/css" href="{css}"/>\n'
            f'</head>\n<body>\n{body}\n</body>\n</html>\n').encode()


class AssetStore:
    def __init__(self, book: epub.EpubBook, session_dir: Path) -> None:
        self.book = book
        self.session_dir = session_dir
        self.added: dict[str, str] = {}

    def add(self, rel: str | None) -> str | None:
        if not rel:
            return None
        if rel in self.added:
            return self.added[rel]
        src = (self.session_dir / rel).resolve()
        if self.session_dir.resolve() not in src.parents or not src.is_file():
            return None
        ext = src.suffix.lower()
        mt = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".gif": "image/gif"}.get(ext)
        data = src.read_bytes()
        if mt is None:  # convert unusual native formats (e.g. JPEG 2000, TIFF) to PNG for compatibility
            import io

            with Image.open(src) as im:
                buf = io.BytesIO()
                im.convert("RGB").save(buf, format="PNG")
                data, ext, mt = buf.getvalue(), ".png", "image/png"
        name = f"images/img{len(self.added) + 1:05d}{ext}"
        self.book.add_item(epub.EpubItem(uid=f"img{len(self.added) + 1:05d}", file_name=name, media_type=mt,
                                         content=data))
        self.added[rel] = name
        return name


def _render_block(b: Block, assets: AssetStore, report: dict[str, Any]) -> str:
    anchor = f' id="{_esc(b.region_id)}"' if b.region_id else ""
    if b.kind == "heading":
        lvl = min(max(b.level, 1), 3)
        return f"<h{lvl}{anchor}>{_esc(b.text)}</h{lvl}>"
    if b.kind in ("figure", "pageimage"):
        src = assets.add(b.asset)
        if src is None:
            report["warnings"].append(f"Image for page {b.page + 1} could not be included.")
            return ""
        cls = ' class="fallback"' if b.fallback else ""
        cap = f"<figcaption>{_esc(b.caption)}</figcaption>" if b.caption else ""
        dims = f' width="{b.width}" height="{b.height}"' if b.width and b.height else ""
        return (f'<figure{anchor}{cls}><img src="../{src}" alt="{_esc(b.alt[:300])}"{dims}/>{cap}</figure>')
    if b.kind == "caption":
        return f'<p class="caption"{anchor}>{_esc(b.text)}</p>'
    if b.kind == "marginal":
        return f'<aside class="marginal"{anchor}>{_esc(b.text)}</aside>'
    if b.kind == "list":
        return f'<p class="list-item"{anchor}>{_esc(b.text)}</p>'
    return f"<p{anchor}>{_esc(b.text)}</p>"


def build_epub(*, pages: list[dict[str, Any]], session_dir: Path, out: Path, meta: dict[str, Any],
               strategy: str = "hybrid", chapters: list[dict[str, Any]] | None = None) -> BuildResult:
    if strategy not in STRATEGIES:
        raise ValueError("unknown strategy")
    lang = (meta.get("language") or "es").strip() or "es"
    title = (meta.get("title") or "Untitled").strip() or "Untitled"
    book = epub.EpubBook()
    book.set_identifier(meta.get("identifier") or f"urn:uuid:{uuid.uuid4()}")
    book.set_title(title)
    book.set_language(lang)
    for a in [x.strip() for x in str(meta.get("author") or "").split(";") if x.strip()]:
        book.add_author(a)
    book.add_metadata("DC", "date", datetime.now(UTC).strftime("%Y-%m-%d"))
    book.add_metadata("DC", "source", "Converted locally by PDF Workstation")
    report: dict[str, Any] = {
        "strategy": strategy, "pages_processed": len(pages),
        "ocr_pages": sum(1 for p in pages if (p.get("classification") or {}).get("needs_ocr")),
        "text_layer_pages": sum(1 for p in pages if p.get("status") == "done"
                                and not (p.get("classification") or {}).get("needs_ocr")),
        "images_retained": 0, "chapters": [], "fallback_pages": [], "fallback_regions": [], "footnotes": 0,
        "removed_running_elements": {}, "excluded_by_user": 0, "warnings": [],
        "low_confidence_unreviewed": sum(1 for p in pages if not p.get("reviewed") for r in (p.get("regions") or [])
                                         if r.get("low_confidence") and r.get("corrected_text") is None
                                         and not r.get("excluded")),
        "corrected_regions": sum(1 for p in pages for r in (p.get("regions") or [])
                                 if r.get("corrected_text") is not None),
    }
    confs = [p["mean_confidence"] for p in pages if p.get("mean_confidence") is not None
             and (p.get("classification") or {}).get("needs_ocr")]
    report["mean_ocr_confidence"] = round(sum(confs) / len(confs), 1) if confs else None
    assets = AssetStore(book, session_dir)
    style = epub.EpubItem(uid="style", file_name="style/book.css", media_type="text/css",
                          content=(FIXED_CSS if strategy == "fixed" else CSS).encode("utf-8"))
    book.add_item(style)
    expected_order: list[str] = []
    spine: list[Any] = ["nav"]
    toc: list[Any] = []

    if strategy == "fixed":
        spine.remove("nav")  # the nav document is not pre-paginated, so keep it out of the fixed spine
        _build_fixed(book, pages, assets, lang, title, spine, toc, expected_order, report, chapters)
        book.add_metadata(None, "meta", "pre-paginated", {"property": "rendition:layout"})
        book.add_metadata(None, "meta", "auto", {"property": "rendition:orientation"})
        book.add_metadata(None, "meta", "none", {"property": "rendition:spread"})
        report["warnings"].append("Fixed layout: text does not reflow and font size cannot be adjusted; "
                                  "accessibility is limited to the positioned text layer.")
    else:
        blocks, notes = collect_blocks(pages, strategy, report)
        if strategy == "reflowable" and report["fallback_pages"]:
            report["warnings"].append("Some pages failed analysis and were kept as images.")
        chaps = split_chapters(blocks, notes, chapters, len(pages))
        if report.get("cover_asset"):
            src = (session_dir / report["cover_asset"]).resolve()
            if session_dir.resolve() in src.parents and src.is_file():
                name = f"images/cover{src.suffix.lower()}"
                book.set_cover(name, src.read_bytes(), create_page=False)  # image + cover-image metadata
                page = epub.EpubItem(uid="coverpage", file_name="text/cover.xhtml", media_type="application/xhtml+xml",
                                     content=_xhtml(title, lang, f'<section epub:type="cover"><img src="../{name}" '
                                                    f'alt="{_esc(title)}" style="max-width:100%;max-height:100vh"/>'
                                                    "</section>", css="../style/book.css"))
                book.add_item(page)
                spine.insert(0, page)  # linear page, so readers and EPUBCheck can reach it
                report["cover"] = True
        for k, ch in enumerate(chaps, start=1):
            body = []
            last_page = -1
            for b in ch.blocks:
                if b.page != last_page:
                    body.append(f'<span class="pagebreak" epub:type="pagebreak" id="page{b.page + 1}" '
                                f'title="{b.page + 1}"></span>')
                    last_page = b.page
                body.append(_render_block(b, assets, report))
                if b.kind in ("heading", "para", "list", "caption", "marginal") and b.text:
                    expected_order.append(_snippet(b.text))
                elif b.kind == "figure" and b.caption:
                    expected_order.append(_snippet(b.caption))
            if ch.footnotes:
                notes_html = "".join(f'<aside class="footnote" epub:type="footnote" id="{_esc(n.region_id)}">'
                                     f"<p>{_esc(n.text)}</p></aside>" for n in ch.footnotes)
                body.append(f'<section class="footnotes" epub:type="footnotes"><h2>Notes</h2>{notes_html}</section>')
                expected_order.extend(_snippet(n.text) for n in ch.footnotes)
            name = f"text/chapter{k:03d}.xhtml"
            item = epub.EpubItem(uid=f"chapter{k:03d}", file_name=name, media_type="application/xhtml+xml",
                                 content=_xhtml(ch.title or title, lang, "\n".join(body), css="../style/book.css"))
            book.add_item(item)
            spine.append(item)
            toc.append(epub.Link(name, ch.title or f"Section {k}", f"toc{k}"))
            report["chapters"].append({"title": ch.title, "start_page": ch.start_page + 1})

    if not toc:
        raise ValueError("The document produced no content.")
    book.toc = toc
    book.spine = spine
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())
    epub.write_epub(str(out), book, {"epub3_pages": False})
    report["image_files"] = len(assets.added)
    report["known_limitations"] = [
        "OCR may misread characters, especially in low-quality scans, unusual fonts or handwriting.",
        "Tables, formulas and complex diagrams are preserved as images, not reconstructed as markup.",
        "Running headers, footers and page numbers are omitted from the reading flow.",
        "Chapter detection relies on heading size and keywords; edit chapter boundaries if needed.",
    ]
    images_expected = len(assets.added)
    return BuildResult(out, report, [s for s in expected_order if s], images_expected)


def _build_fixed(book: epub.EpubBook, pages: list[dict[str, Any]], assets: AssetStore, lang: str, title: str,
                 spine: list[Any], toc: list[Any], expected: list[str], report: dict[str, Any],
                 chapters: list[dict[str, Any]] | None) -> None:
    starts = {int(c["page"]) - 1: str(c.get("title") or "") for c in (chapters or [])}
    for p in pages:
        pi = p["page_index"]
        w, h = p.get("image_size") or [1000, 1414]
        vw = 1000
        vh = int(round(h * vw / w)) if w else 1414
        src = assets.add(p.get("page_asset"))
        if src is None:
            report["warnings"].append(f"Page {pi + 1} image missing.")
            continue
        report["images_retained"] += 1
        spans = []
        heading = None
        for r in sorted(p.get("regions") or [], key=lambda r: r.get("order", 0)):
            if r.get("excluded") or r["role"] in ("figure", "table"):
                continue
            corrected = r.get("corrected_text")
            lines = r.get("lines") or []
            if corrected is not None or not lines:
                lines = [{"bbox": r["bbox"], "text": region_text(r)}]
            if r["role"] == "heading" and heading is None:
                heading = region_text(r)
            for ln in lines:
                if not ln.get("text"):
                    continue
                x0, y0, x1, y1 = ln["bbox"]
                fs = max(4.0, (y1 - y0) * vh * 0.9)
                spans.append(f'<span class="t" style="left:{x0 * 100:.3f}%;top:{y0 * 100:.3f}%;'
                             f'font-size:{fs:.1f}px">{_esc(ln["text"])}</span>')
            if region_text(r) and r["role"] not in SKIP_ROLES:
                expected.append(_snippet(region_text(r)))
        body = (f'<div class="page" style="width:{vw}px;height:{vh}px">'
                f'<img class="scan" src="../{src}" alt="Page {pi + 1}"/>{"".join(spans)}</div>')
        name = f"text/page{pi + 1:04d}.xhtml"
        item = epub.EpubItem(uid=f"page{pi + 1:04d}", file_name=name, media_type="application/xhtml+xml",
                             content=_xhtml(f"{title} – {pi + 1}", lang, body, css="../style/book.css",
                                            head_extra=f'<meta name="viewport" content="width={vw}, height={vh}"/>\n'))
        book.add_item(item)
        spine.append(item)
        if pi in starts or (not chapters and heading and (p.get("regions") or [])):
            label = starts.get(pi) or heading or f"Page {pi + 1}"
            toc.append(epub.Link(name, label[:120], f"toc{pi + 1}"))
            report["chapters"].append({"title": label[:120], "start_page": pi + 1})
    if not toc and spine[1:]:
        first = spine[1]
        toc.append(epub.Link(first.file_name, title, "toc1"))
