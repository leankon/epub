"""Inspect PDFs for metadata and features that processing may alter (signatures, forms, encryption)."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pikepdf

from app.core.errors import PasswordRequired, ProcessingError


def open_pdf(path: Path, password: str | None = None, *, allow_restricted: bool = False) -> pikepdf.Pdf:
    """Open a PDF. Encrypted files require the user-supplied password; nothing is ever guessed."""
    try:
        pdf = pikepdf.open(path, password=password or "")
    except pikepdf.PasswordError:
        raise PasswordRequired("This PDF is encrypted. Supply its password (Protect / Unlock tool).") from None
    except pikepdf.PdfError as exc:
        raise ProcessingError("The PDF is damaged or not a valid PDF.", details={"reason": str(exc)[:200]}) from None
    if pdf.is_encrypted and not password and not allow_restricted:
        pdf.close()
        raise PasswordRequired(
            "This PDF is encrypted (it has an owner password and usage restrictions). "
            "Unlock it with its password first; restrictions are never bypassed.")
    return pdf


def _s(v: Any) -> str | None:
    if v is None:
        return None
    try:
        return str(v)[:2000]
    except Exception:
        return None


def _signature_info(pdf: pikepdf.Pdf, file_size: int) -> list[dict[str, Any]]:
    sigs: list[dict[str, Any]] = []
    acro = pdf.Root.get("/AcroForm")
    if acro is None:
        return sigs

    def walk(fields: Any, prefix: str = "") -> None:
        for f in fields or []:
            name = prefix + (_s(f.get("/T")) or "")
            if f.get("/FT") == "/Sig":
                v = f.get("/V")
                info: dict[str, Any] = {"field": name, "signed": v is not None}
                if v is not None:
                    info["sub_filter"] = _s(v.get("/SubFilter"))
                    info["signing_time"] = _s(v.get("/M"))
                    info["signer_name"] = _s(v.get("/Name"))
                    br = v.get("/ByteRange")
                    if br is not None and len(br) == 4:
                        a, b, c, d = (int(x) for x in br)
                        info["byte_range"] = [a, b, c, d]
                        # A signature only covers the whole file when the second range reaches EOF.
                        info["covers_whole_file"] = a == 0 and c + d == file_size
                sigs.append(info)
            if "/Kids" in f:
                walk(f.Kids, name + ".")

    walk(acro.get("/Fields"))
    return sigs


def inspect_pdf(path: Path, password: str | None = None) -> dict[str, Any]:
    size = path.stat().st_size
    raw = path.read_bytes() if size < 64 * 1024 * 1024 else None
    revisions = len(re.findall(rb"%%EOF", raw)) if raw is not None else None
    try:
        pdf = open_pdf(path, password, allow_restricted=True)
    except PasswordRequired:
        return {"encrypted": True, "needs_password": True, "page_count": None, "revisions": revisions,
                "warnings": ["Encrypted: a password is required to open this PDF."]}
    with pdf:
        warnings: list[str] = []
        docinfo = pdf.docinfo if pdf.trailer.get("/Info") is not None else {}
        meta: dict[str, Any] = {}
        try:
            with pdf.open_metadata(set_pikepdf_as_editor=False) as xmp:
                meta["xmp_language"] = _s(xmp.get("dc:language"))
        except Exception:
            pass
        sizes = []
        annots = 0
        for p in pdf.pages:
            box = p.mediabox
            w, h = float(box[2]) - float(box[0]), float(box[3]) - float(box[1])
            sizes.append([round(w, 2), round(h, 2), int(p.obj.get("/Rotate", 0))])
            annots += len(p.obj.get("/Annots", []) or [])
        acro = pdf.Root.get("/AcroForm")
        fields = list(acro.get("/Fields", [])) if acro is not None else []
        sigs = _signature_info(pdf, size)
        names = pdf.Root.get("/Names")
        has_js = bool(names is not None and "/JavaScript" in names) or "/OpenAction" in pdf.Root
        attachments = len(pdf.attachments)
        try:
            outline_count = sum(1 for _ in _iter_outline(pdf))
        except Exception:
            outline_count = 0
        if sigs:
            warnings.append("Contains digital signature fields. Any modification invalidates existing signatures.")
        if fields and not sigs:
            warnings.append("Contains interactive form fields; some operations may flatten or drop them.")
        if pdf.is_encrypted:
            warnings.append("Encrypted with usage restrictions; unlock with the owner password before editing.")
        if has_js:
            warnings.append("Contains JavaScript or automatic actions. They are never executed by this app.")
        if revisions and revisions > 1:
            warnings.append(f"Has {revisions - 1} incremental update(s); earlier revisions may hold old content.")
        lang = _s(pdf.Root.get("/Lang")) or meta.get("xmp_language")
        return {
            "encrypted": bool(pdf.is_encrypted),
            "needs_password": False,
            "pdf_version": pdf.pdf_version,
            "page_count": len(pdf.pages),
            "page_sizes": sizes,
            "title": _s(docinfo.get("/Title")) if docinfo else None,
            "author": _s(docinfo.get("/Author")) if docinfo else None,
            "subject": _s(docinfo.get("/Subject")) if docinfo else None,
            "keywords": _s(docinfo.get("/Keywords")) if docinfo else None,
            "creator": _s(docinfo.get("/Creator")) if docinfo else None,
            "producer": _s(docinfo.get("/Producer")) if docinfo else None,
            "language": lang,
            "has_forms": bool(fields),
            "form_field_count": len(fields),
            "signatures": sigs,
            "has_signatures": bool(sigs),
            "annotation_count": annots,
            "attachment_count": attachments,
            "has_javascript": has_js,
            "outline_count": outline_count,
            "revisions": revisions,
            "warnings": warnings,
        }


def _iter_outline(pdf: pikepdf.Pdf):  # type: ignore[no-untyped-def]
    with pdf.open_outline() as outline:
        stack = list(outline.root)
        while stack:
            item = stack.pop()
            yield item
            stack.extend(item.children)
