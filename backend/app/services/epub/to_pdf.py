"""EPUB to PDF via WeasyPrint. Resources may only be loaded from the extracted EPUB (no network)."""

from __future__ import annotations

import posixpath
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from lxml import etree
from lxml import html as lxml_html

from app.core.errors import AppError, ProcessingError, ToolUnavailable
from app.core.files import safe_extract_zip
from app.services.epub.validate import NS, SAFE_PARSER, validate_epub

PAGE_SIZES = {"a4": "A4", "a5": "A5", "letter": "letter", "6x9": "6in 9in"}
XHTML_NS = "http://www.w3.org/1999/xhtml"


def epub_to_pdf(src: Path, out: Path, workdir: Path, *, page_size: str = "a5",
                font_size_pt: float = 11.0) -> dict[str, Any]:
    try:
        from weasyprint import CSS, HTML
        from weasyprint.urls import URLFetcher
    except OSError as exc:  # missing Pango
        raise ToolUnavailable("WeasyPrint system libraries (Pango) are missing.") from exc
    if page_size not in PAGE_SIZES:
        raise AppError("Unknown page size.", code="invalid_page_size")
    if not 7 <= font_size_pt <= 18:
        raise AppError("Font size must be between 7 and 18 pt.", code="invalid_font_size")
    rep = validate_epub(src, run_epubcheck=False)
    structural = {"zip_structure", "container", "package_metadata", "spine"}
    fatal = [c for c in rep.checks if c.name in structural and not c.passed]
    if fatal:
        raise AppError("The EPUB is structurally invalid and cannot be converted.", code="invalid_epub",
                       details={"errors": rep.errors[:10]})
    root = workdir / "epub"
    safe_extract_zip(src, root)
    root_r = root.resolve()
    container = etree.parse(str(root / "META-INF" / "container.xml"), SAFE_PARSER)
    opf_rel = container.find(".//c:rootfile", NS).get("full-path")
    opf_path = (root / opf_rel).resolve()
    if root_r not in opf_path.parents:
        raise AppError("Invalid package path.", code="unsafe_archive")
    opf = etree.parse(str(opf_path), SAFE_PARSER).getroot()
    base = posixpath.dirname(opf_rel)
    manifest = {it.get("id"): posixpath.normpath(posixpath.join(base, unquote(it.get("href", ""))))
                for it in opf.iterfind("opf:manifest/opf:item", NS)}
    spine = [manifest[i.get("idref")] for i in opf.iterfind("opf:spine/opf:itemref", NS)
             if i.get("idref") in manifest and i.get("linear", "yes") != "no"]
    title = (opf.findtext("opf:metadata/dc:title", namespaces=NS) or "Untitled").strip()
    author = (opf.findtext("opf:metadata/dc:creator", namespaces=NS) or "").strip()
    lang = (opf.findtext("opf:metadata/dc:language", namespaces=NS) or "en").strip()
    doc_index = {h: k for k, h in enumerate(spine)}
    warnings: list[str] = []
    stylesheets: list[Path] = []
    sections: list[str] = []
    images = 0

    def rewrite(href: str, here: str) -> str:
        p = urlparse(href)
        if p.scheme or href.startswith(("data:", "#")):
            return href
        target = posixpath.normpath(posixpath.join(here, unquote(p.path))) if p.path else ""
        if target in doc_index:
            k = doc_index[target]
            return f"#d{k}-{p.fragment}" if p.fragment else f"#d{k}"
        return (root / target).resolve().as_uri() if target else href

    for k, rel in enumerate(spine):
        f = (root / rel).resolve()
        if root_r not in f.parents or not f.is_file():
            warnings.append(f"Spine document missing: {rel}")
            continue
        here = posixpath.dirname(rel)
        try:
            tree = etree.parse(str(f), SAFE_PARSER).getroot()
        except etree.XMLSyntaxError:
            tree = lxml_html.parse(str(f)).getroot()
            warnings.append(f"{rel} is not well-formed XHTML; parsed leniently.")
        for el in tree.iter():
            if not isinstance(el.tag, str):
                continue
            el.tag = etree.QName(el).localname
            if el.tag in ("script", "iframe", "object", "embed"):
                el.getparent().remove(el) if el.getparent() is not None else None
                continue
            if el.tag == "link" and "stylesheet" in (el.get("rel") or ""):
                css = (root / posixpath.normpath(posixpath.join(here, unquote(el.get("href", ""))))).resolve()
                if root_r in css.parents and css.is_file() and css not in stylesheets:
                    stylesheets.append(css)
            for attr in list(el.attrib):
                local = etree.QName(attr).localname
                if local != attr:
                    val = el.attrib.pop(attr)
                    if local == "href":
                        el.set("href", val)
                    continue
                if local.startswith("on"):
                    del el.attrib[attr]
            if el.get("id"):
                el.set("id", f"d{k}-{el.get('id')}")
            for attr in ("src", "href"):
                if el.get(attr) and el.tag != "link":
                    el.set(attr, rewrite(el.get(attr), here))
            if el.tag in ("img", "image"):
                images += 1
        etree.cleanup_namespaces(tree)
        body = tree.find(".//body")
        inner = "".join(etree.tostring(c, encoding="unicode", method="html") for c in body) if body is not None else ""
        if body is not None and body.text:
            inner = body.text + inner
        sections.append(f'<section class="epub-doc" id="d{k}">{inner}</section>')

    if not sections:
        raise ProcessingError("The EPUB has no readable content documents.")
    doc_html = (f'<!DOCTYPE html><html lang="{lang}"><head><meta charset="utf-8"><title>{_e(title)}</title>'
                f'<meta name="author" content="{_e(author)}"></head><body>{"".join(sections)}</body></html>')
    base_css = CSS(string=(
        f"@page {{ size: {PAGE_SIZES[page_size]}; margin: 18mm 16mm 20mm;"
        " @bottom-center { content: counter(page); font-size: 9pt; } }"
        f" html {{ font-size: {font_size_pt}pt; }} body {{ font-family: serif; line-height: 1.45; }}"
        " section.epub-doc { break-before: page; } section.epub-doc:first-child { break-before: auto; }"
        " img, svg { max-width: 100%; height: auto; } h1, h2, h3 { break-after: avoid; }"
        " figure { break-inside: avoid; }"))

    class SandboxedFetcher(URLFetcher):  # type: ignore[misc]
        """Only data: URIs and files inside the extracted EPUB; everything else is refused."""

        def fetch(self, url: str, headers: Any = None) -> Any:
            p = urlparse(url)
            if p.scheme == "file":
                target = Path(unquote(p.path)).resolve()
                if target != root_r and root_r not in target.parents:
                    raise ValueError("Blocked resource outside the EPUB")
            elif p.scheme != "data":
                raise ValueError("Blocked non-local resource")
            return super().fetch(url, headers)

    fetcher = SandboxedFetcher(allowed_protocols={"file", "data"}, allow_redirects=False)

    user_css = [CSS(filename=str(c), url_fetcher=fetcher) for c in stylesheets]
    HTML(string=doc_html, base_url=root.as_uri() + "/", url_fetcher=fetcher).write_pdf(
        str(out), stylesheets=[base_css, *user_css])
    warnings.append("EPUB is reflowable and PDF has fixed pages: pagination and layout will differ from any "
                    "e-reader. Round-trip fidelity is not guaranteed.")
    return {"documents": len(sections), "images": images, "page_size": page_size, "warnings": warnings,
            "title": title}


def _e(s: str) -> str:
    import html

    return html.escape(s, quote=True)
