"""PDF-to-EPUB conversion, EPUB validation and EPUB preview."""

from __future__ import annotations

import posixpath
import uuid
import zipfile
from typing import Any
from urllib.parse import quote, unquote

from fastapi import APIRouter, Query
from fastapi.responses import Response
from lxml import etree
from sqlalchemy.orm import Session

from app.api.schemas import EpubConvert
from app.core.errors import AppError, Conflict, NotFound
from app.db.models import OcrSession
from app.db.session import DBDep, session_scope
from app.services import storage
from app.services.epub.validate import NS, SAFE_PARSER
from app.services.jobs import manager

router = APIRouter(prefix="/api/epub", tags=["epub"])


@router.post("/convert", status_code=202)
def convert(body: EpubConvert) -> dict[str, Any]:
    """Convert using an existing OCR session (reviewed or not) or analyse automatically."""
    from app.api.ocr import create_session

    sid = body.session_id
    if sid is not None:
        with session_scope() as db:
            sess = db.get(OcrSession, sid)
            if sess is None or sess.version_id != body.version_id:
                raise NotFound("OCR session not found for this version.")
            if sess.status == "processing":
                raise Conflict("The OCR session is still being analysed.")
            if sess.status != "approved" and not body.allow_unreviewed:
                raise AppError("The OCR results have not been approved. Approve them in the OCR workspace, or "
                               "confirm conversion without review.", code="approval_required", status_code=409)
    else:
        sid = create_session(body.version_id, body.options)
    params = {"session_id": str(sid), "strategy": body.strategy,
              "metadata": body.metadata.model_dump(mode="json", exclude_none=True, exclude={"chapters"}),
              "chapters": [c.model_dump() for c in body.metadata.chapters] if body.metadata.chapters else None}
    job_id = manager().submit("pdf_to_epub", params, [body.version_id])
    with session_scope() as db:
        sess = db.get(OcrSession, sid)
        if sess is not None and sess.job_id is None:
            sess.job_id = job_id
    return {"job_id": str(job_id), "session_id": str(sid)}


def _open_epub(db: Session, vid: uuid.UUID) -> tuple[zipfile.ZipFile, str, etree._Element]:
    v = storage.get_version(db, vid)
    if v.file_ext != "epub":
        raise AppError("Not an EPUB version.", code="not_epub")
    zf = zipfile.ZipFile(storage.version_path(v))
    container = etree.fromstring(zf.read("META-INF/container.xml"), SAFE_PARSER)
    opf_path = container.find(".//c:rootfile", NS).get("full-path")
    return zf, opf_path, etree.fromstring(zf.read(opf_path), SAFE_PARSER)


@router.get("/versions/{vid}/toc")
def toc(vid: uuid.UUID, db: Session = DBDep) -> dict[str, Any]:
    zf, opf_path, opf = _open_epub(db, vid)
    with zf:
        base = posixpath.dirname(opf_path)
        manifest = {i.get("id"): {"href": posixpath.normpath(posixpath.join(base, unquote(i.get("href", "")))),
                                  "type": i.get("media-type")} for i in opf.iterfind("opf:manifest/opf:item", NS)}
        spine = [manifest[r.get("idref")]["href"] for r in opf.iterfind("opf:spine/opf:itemref", NS)
                 if r.get("idref") in manifest]
        images = [m["href"] for m in manifest.values() if (m["type"] or "").startswith("image/")]
        md = opf.find("opf:metadata", NS)
        meta = {k: (md.findtext(f"dc:{k}", namespaces=NS) or "") for k in ("title", "creator", "language")}
        layout = md.findtext("opf:meta[@property='rendition:layout']", namespaces=NS) if md is not None else None
    return {"spine": spine, "images": images, "metadata": meta, "layout": layout or "reflowable"}


@router.get("/versions/{vid}/doc")
def doc(vid: uuid.UUID, path: str = Query(..., max_length=500), db: Session = DBDep) -> Response:
    """Serve a content document for preview, with scripts removed and assets rewritten to this API."""
    zf, _, _ = _open_epub(db, vid)
    with zf:
        if path not in zf.namelist():
            raise NotFound("Not found in EPUB.")
        tree = etree.fromstring(zf.read(path), SAFE_PARSER)
        here = posixpath.dirname(path)
        for el in tree.iter():
            if not isinstance(el.tag, str):
                continue
            local = etree.QName(el).localname
            if local in ("script", "iframe", "object", "embed"):
                el.getparent().remove(el)
                continue
            for attr in list(el.attrib):
                if etree.QName(attr).localname.startswith("on"):
                    del el.attrib[attr]
            for attr in ("src", "href", "{http://www.w3.org/1999/xlink}href"):
                val = el.get(attr)
                if val and not val.startswith(("#", "data:", "http:", "https:")):
                    target = posixpath.normpath(posixpath.join(here, unquote(val.split("#")[0])))
                    frag = ("#" + val.split("#", 1)[1]) if "#" in val else ""
                    endpoint = "doc" if target.endswith((".xhtml", ".html", ".htm")) else "file"
                    el.set(attr, f"{endpoint}?path={quote(target)}{frag}")
        body = etree.tostring(tree, encoding="utf-8", xml_declaration=True)
    return Response(body, media_type="application/xhtml+xml",
                    headers={"Content-Security-Policy": "default-src 'none'; img-src 'self'; style-src 'self' "
                             "'unsafe-inline'; frame-ancestors 'self'", "X-Frame-Options": "SAMEORIGIN"})


@router.get("/versions/{vid}/file")
def file(vid: uuid.UUID, path: str = Query(..., max_length=500), db: Session = DBDep) -> Response:
    zf, _, _ = _open_epub(db, vid)
    with zf:
        if path not in zf.namelist():
            raise NotFound("Not found in EPUB.")
        ext = posixpath.splitext(path)[1].lower()
        mt = {".css": "text/css", ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
              ".gif": "image/gif", ".svg": "image/svg+xml", ".webp": "image/webp"}.get(ext)
        if mt is None:
            raise AppError("Only images and stylesheets are served for preview.", code="unsupported_media")
        return Response(zf.read(path), media_type=mt, headers={"X-Frame-Options": "SAMEORIGIN"})
