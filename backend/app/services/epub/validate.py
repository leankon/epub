"""EPUB validation: internal structural checks plus EPUBCheck when installed.

The report never claims EPUBCheck validation unless EPUBCheck actually ran.
"""

from __future__ import annotations

import hashlib
import json
import posixpath
import re
import tempfile
import zipfile
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import unquote, urlparse

from lxml import etree

from app.core.config import get_settings
from app.core.subproc import run
from app.core.tools import tool_available, tool_report

NS = {"opf": "http://www.idpf.org/2007/opf", "dc": "http://purl.org/dc/elements/1.1/",
      "c": "urn:oasis:names:tc:opendocument:xmlns:container", "x": "http://www.w3.org/1999/xhtml",
      "epub": "http://www.idpf.org/2007/ops", "svg": "http://www.w3.org/2000/svg",
      "xlink": "http://www.w3.org/1999/xlink"}
XHTML_TYPES = {"application/xhtml+xml"}
MEDIA_BY_EXT = {".xhtml": "application/xhtml+xml", ".html": "application/xhtml+xml", ".css": "text/css",
                ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif",
                ".svg": "image/svg+xml", ".ncx": "application/x-dtbncx+xml", ".webp": "image/webp"}
SAFE_PARSER = etree.XMLParser(resolve_entities=False, no_network=True, load_dtd=False, huge_tree=False)


@dataclass
class Check:
    name: str
    passed: bool
    detail: str = ""
    required: bool = True


@dataclass
class ValidationReport:
    checks: list[Check] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    epubcheck: dict[str, Any] = field(default_factory=lambda: {"executed": False})
    stats: dict[str, Any] = field(default_factory=dict)

    def add(self, name: str, passed: bool, detail: str = "", required: bool = True) -> None:
        self.checks.append(Check(name, passed, detail, required))
        if not passed:
            (self.errors if required else self.warnings).append(f"{name}: {detail}")

    @property
    def passed(self) -> bool:
        internal = all(c.passed for c in self.checks if c.required)
        ec = self.epubcheck
        return internal and (not ec.get("executed") or ec.get("errors", 0) + ec.get("fatals", 0) == 0)

    def summary(self) -> dict[str, Any]:
        return {"passed": self.passed, "checks": [vars(c) for c in self.checks], "errors": self.errors,
                "warnings": self.warnings, "epubcheck": self.epubcheck, "stats": self.stats}


def _text_of(doc: etree._Element) -> str:
    body = doc.find(".//x:body", NS)
    if body is None:
        return ""
    for bad in body.xpath(".//x:script|.//x:style", namespaces=NS):
        bad.getparent().remove(bad)
    return re.sub(r"\s+", " ", "".join(body.itertext())).strip()


def validate_epub(path: Path, *, run_epubcheck: bool = True, expect_text: bool = False,
                  expected_images: int = 0, expected_order: list[str] | None = None) -> ValidationReport:
    rep = ValidationReport()
    try:
        zf = zipfile.ZipFile(path)
    except zipfile.BadZipFile:
        rep.add("zip_structure", False, "not a ZIP archive")
        return rep
    with zf:
        infos = zf.infolist()
        names = {i.filename for i in infos}
        unsafe = [n for n in names if n.startswith("/") or ".." in PurePosixPath(n).parts or "\\" in n]
        first = infos[0] if infos else None
        ok = (first is not None and first.filename == "mimetype" and first.compress_type == zipfile.ZIP_STORED
              and zf.read("mimetype").strip() == b"application/epub+zip")
        rep.add("zip_structure", ok and not unsafe,
                "mimetype first, uncompressed, correct value" if ok and not unsafe else
                ("unsafe paths in archive" if unsafe else "mimetype entry missing, compressed or not first"))
        if unsafe:
            return rep
        try:
            container = etree.fromstring(zf.read("META-INF/container.xml"), SAFE_PARSER)
            opf_path = container.find(".//c:rootfile", NS).get("full-path")
            rep.add("container", True, opf_path)
        except Exception:
            rep.add("container", False, "META-INF/container.xml missing or invalid")
            return rep
        try:
            opf = etree.fromstring(zf.read(opf_path), SAFE_PARSER)
        except Exception:
            rep.add("package", False, "package document missing or not well-formed")
            return rep
        base = posixpath.dirname(opf_path)

        def resolve(href: str, rel_to: str = base) -> str:
            return posixpath.normpath(posixpath.join(rel_to, unquote(href.split("#")[0]))) if href else ""

        md = opf.find("opf:metadata", NS)
        uid_ref = opf.get("unique-identifier")
        ident = md.find(f"dc:identifier[@id='{uid_ref}']", NS) if md is not None and uid_ref else None
        title = md.find("dc:title", NS) if md is not None else None
        lang = md.find("dc:language", NS) if md is not None else None
        modified = md.find("opf:meta[@property='dcterms:modified']", NS) if md is not None else None
        problems = [n for n, v in (("identifier", ident), ("title", title), ("language", lang)) if v is None
                    or not (v.text or "").strip()]
        if opf.get("version", "").startswith("3") and modified is None:
            problems.append("dcterms:modified")
        rep.add("package_metadata", not problems, "ok" if not problems else f"missing: {', '.join(problems)}")
        rep.stats["title"] = (title.text or "") if title is not None else ""
        rep.stats["language"] = (lang.text or "") if lang is not None else ""

        manifest: dict[str, dict[str, str]] = {}
        for it in opf.iterfind("opf:manifest/opf:item", NS):
            manifest[it.get("id", "")] = {"href": resolve(it.get("href", "")), "type": it.get("media-type", ""),
                                          "props": it.get("properties", "")}
        missing = [m["href"] for m in manifest.values() if m["href"] not in names]
        rep.add("manifest_resources", not missing,
                "all manifest items present" if not missing else f"missing files: {', '.join(missing[:10])}")
        wrong_type = [m["href"] for m in manifest.values()
                      if MEDIA_BY_EXT.get(PurePosixPath(m["href"]).suffix.lower(), m["type"]) != m["type"]]
        rep.add("media_types", not wrong_type, "ok" if not wrong_type else f"unexpected: {wrong_type[:5]}",
                required=False)
        listed = {m["href"] for m in manifest.values()} | {"mimetype", "META-INF/container.xml", opf_path}
        unlisted = [n for n in names if n not in listed and not n.endswith("/") and not n.startswith("META-INF/")]
        rep.add("unlisted_files", not unlisted, "ok" if not unlisted else f"not in manifest: {unlisted[:5]}",
                required=False)

        spine = [ir.get("idref") for ir in opf.iterfind("opf:spine/opf:itemref", NS)]
        bad_refs = [i for i in spine if i not in manifest]
        rep.add("spine", bool(spine) and not bad_refs,
                f"{len(spine)} item(s)" if spine and not bad_refs else f"invalid itemrefs: {bad_refs[:5]}")

        nav_items = [m for m in manifest.values() if "nav" in m["props"].split()]
        toc_links = 0
        if nav_items and nav_items[0]["href"] in names:
            try:
                navdoc = etree.fromstring(zf.read(nav_items[0]["href"]), SAFE_PARSER)
                toc = navdoc.find(".//x:nav[@epub:type='toc']", NS)
                toc_links = len(toc.findall(".//x:a", NS)) if toc is not None else 0
            except Exception:
                toc_links = 0
        rep.add("navigation", toc_links > 0, f"{toc_links} toc entr(ies)" if toc_links else "no EPUB 3 toc nav")

        # Content documents: well-formedness, references, text, images, duplicates.
        docs: dict[str, etree._Element] = {}
        malformed: list[str] = []
        for m in manifest.values():
            if m["type"] in XHTML_TYPES and m["href"] in names:
                try:
                    docs[m["href"]] = etree.fromstring(zf.read(m["href"]), SAFE_PARSER)
                except etree.XMLSyntaxError as exc:
                    malformed.append(f"{m['href']} ({str(exc)[:80]})")
        rep.add("xhtml_well_formed", not malformed, "ok" if not malformed else "; ".join(malformed[:5]))

        broken: list[str] = []
        img_refs: set[str] = set()
        img_count = 0
        ids: dict[str, set[str]] = {h: set(d.xpath("//@id")) for h, d in docs.items()}
        for href, d in docs.items():
            here = posixpath.dirname(href)
            for el in d.iter():
                if not isinstance(el.tag, str):
                    continue
                for attr in ("src", "href", "{http://www.w3.org/1999/xlink}href"):
                    val = el.get(attr)
                    if not val or val.startswith(("data:", "mailto:")):
                        continue
                    if urlparse(val).scheme in ("http", "https"):
                        rep.warnings.append(f"external link in {href}: {val[:80]}")
                        continue
                    target = resolve(val, here) if not val.startswith("#") else href
                    frag = val.split("#", 1)[1] if "#" in val else None
                    if target not in names:
                        broken.append(f"{href} -> {val}")
                    elif frag and target in ids and frag not in ids[target]:
                        broken.append(f"{href} -> {val} (missing anchor)")
                    if el.tag.endswith("}img") or el.tag.endswith("}image"):
                        img_refs.add(target)
                        img_count += 1
        rep.add("internal_links", not broken, "ok" if not broken else "; ".join(broken[:8]))

        spine_hrefs = [manifest[i]["href"] for i in spine if i in manifest]
        texts = {h: _text_of(docs[h]) for h in spine_hrefs if h in docs}
        total_text = sum(len(t) for t in texts.values())
        rep.stats.update({"content_documents": len(spine_hrefs), "text_characters": total_text,
                          "image_references": img_count,
                          "image_resources": sum(1 for m in manifest.values() if m["type"].startswith("image/"))})
        if expect_text:
            rep.add("real_text", total_text > 0, f"{total_text} characters of XHTML text")
        empty = [h for h in spine_hrefs if h in docs and h != (nav_items[0]["href"] if nav_items else None)
                 and not texts.get(h) and not docs[h].findall(".//x:img", NS)
                 and not docs[h].findall(".//svg:image", NS)]
        rep.add("no_empty_documents", not empty, "ok" if not empty else f"empty: {empty[:5]}")
        hashes: dict[str, str] = {}
        dups = []
        for h in spine_hrefs:
            if h in names:
                digest = hashlib.sha256(zf.read(h)).hexdigest()
                if digest in hashes:
                    dups.append(f"{h} == {hashes[digest]}")
                hashes[digest] = h
        rep.add("no_duplicate_documents", not dups, "ok" if not dups else "; ".join(dups[:5]))
        if expected_images:
            rep.add("images_present", len(img_refs) >= expected_images and all(r in names for r in img_refs),
                    f"{len(img_refs)} of {expected_images} expected image(s) referenced")
        unused = [m["href"] for m in manifest.values() if m["type"].startswith("image/") and m["href"] not in img_refs
                  and "cover-image" not in m["props"]]
        rep.add("images_referenced", not unused, "ok" if not unused else f"unreferenced: {unused[:5]}",
                required=False)
        if expected_order:
            flow = " ".join(texts[h] for h in spine_hrefs if h in texts)
            pos, in_order = 0, 0
            for snippet in expected_order:
                i = flow.find(snippet, pos)
                if i >= 0:
                    in_order += 1
                    pos = i + len(snippet)
            ratio = in_order / len(expected_order)
            rep.add("reading_order", ratio >= 0.95, f"{in_order}/{len(expected_order)} text blocks in order")

    if run_epubcheck:
        rep.epubcheck = run_epubcheck_tool(path)
        if not rep.epubcheck["executed"]:
            rep.warnings.append("EPUBCheck was not executed: " + rep.epubcheck.get("reason", "unavailable"))
    return rep


def run_epubcheck_tool(path: Path) -> dict[str, Any]:
    if not tool_available("epubcheck"):
        st = tool_report()["epubcheck"]
        return {"executed": False, "reason": st.detail or "not installed", "install_hint": st.install_hint}
    s = get_settings()
    s.tmp_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=s.tmp_dir) as td:
        out = Path(td) / "report.json"
        heap = max(512, min(s.subprocess_memory_mb, 4096))
        res = run([s.java_cmd, f"-Xmx{heap}m", "-Xss8m", "-jar", str(s.epubcheck_jar), str(path), "--json",
                   str(out)], tool_name="EPUBCheck", check=False, timeout=600, limit_address_space=False)
        if not out.exists():
            return {"executed": False, "reason": f"EPUBCheck produced no report (exit {res.returncode})"}
        data = json.loads(out.read_text("utf-8"))
    counts = {"FATAL": 0, "ERROR": 0, "WARNING": 0, "USAGE": 0, "INFO": 0}
    messages = []
    for m in data.get("messages", []):
        sev = m.get("severity", "INFO")
        counts[sev] = counts.get(sev, 0) + 1
        if sev in ("FATAL", "ERROR", "WARNING") and len(messages) < 50:
            loc = (m.get("locations") or [{}])[0]
            messages.append({"severity": sev, "id": m.get("ID"), "message": m.get("message", "")[:300],
                             "path": loc.get("path"), "line": loc.get("line")})
    return {"executed": True, "version": data.get("checker", {}).get("checkerVersion"), "fatals": counts["FATAL"],
            "errors": counts["ERROR"], "warnings": counts["WARNING"], "messages": messages}
