"""Safe file handling: signature sniffing, hashing, path containment and atomic placement."""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import stat
import unicodedata
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from app.core.errors import AppError, Unsupported

CHUNK = 1024 * 1024


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(CHUNK), b""):
            h.update(block)
    return h.hexdigest()


def safe_display_name(name: str | None, default: str = "document") -> str:
    """Sanitise an untrusted filename for display/download only (never used as a storage path)."""
    name = unicodedata.normalize("NFC", name or "")
    name = name.replace("\\", "/").split("/")[-1]
    name = re.sub(r"[\x00-\x1f\x7f]", "", name)
    name = re.sub(r'[<>:"|?*]', "_", name).strip(" .")
    return (name or default)[:180]


def ensure_within(base: Path, candidate: Path) -> Path:
    """Resolve `candidate` and guarantee it stays inside `base` (path traversal protection)."""
    base_r = base.resolve()
    cand_r = candidate.resolve()
    if cand_r != base_r and base_r not in cand_r.parents:
        raise AppError("Path escapes the storage directory.", code="unsafe_path")
    return cand_r


def make_readonly(path: Path) -> None:
    try:
        path.chmod(stat.S_IRUSR | stat.S_IRGRP)
    except PermissionError:
        pass


def atomic_move(src: Path, dest: Path) -> None:
    """Move a finished file into place without ever overwriting an existing file."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        raise AppError("Refusing to overwrite an existing stored file.", code="immutable_violation")
    try:
        os.link(src, dest)  # atomic and fails if dest exists
        src.unlink()
    except OSError:
        tmp = dest.with_name(dest.name + ".partial")
        shutil.copyfile(src, tmp)
        os.replace(tmp, dest)
        src.unlink(missing_ok=True)


# --------------------------------------------------------------------------- sniffing

@dataclass(frozen=True)
class Sniffed:
    kind: str  # pdf | epub | office | image
    ext: str
    mime: str


OFFICE_EXTS = {
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "odt": "application/vnd.oasis.opendocument.text",
    "ods": "application/vnd.oasis.opendocument.spreadsheet",
    "odp": "application/vnd.oasis.opendocument.presentation",
    "doc": "application/msword",
    "xls": "application/vnd.ms-excel",
    "ppt": "application/vnd.ms-powerpoint",
    "rtf": "application/rtf",
}
OOXML_PREFIX = {"docx": "word/", "xlsx": "xl/", "pptx": "ppt/"}
IMAGE_MIMES = {"png": "image/png", "jpg": "image/jpeg", "tiff": "image/tiff", "webp": "image/webp",
               "bmp": "image/bmp", "gif": "image/gif"}


def _zip_names(path: Path) -> list[str] | None:
    try:
        with zipfile.ZipFile(path) as zf:
            return zf.namelist()
    except (zipfile.BadZipFile, OSError):
        return None


def sniff(path: Path, claimed_name: str) -> Sniffed:
    """Determine the real file type from its content. The extension is only a hint."""
    with path.open("rb") as f:
        head = f.read(2048)
    ext = PurePosixPath(claimed_name.lower()).suffix.lstrip(".")
    if ext == "jpeg":
        ext = "jpg"
    if ext == "tif":
        ext = "tiff"

    if b"%PDF-" in head[:1024]:
        return Sniffed("pdf", "pdf", "application/pdf")

    if head.startswith(b"PK\x03\x04"):
        names = _zip_names(path)
        if names is None:
            raise Unsupported("The archive is damaged or not a valid ZIP container.")
        with zipfile.ZipFile(path) as zf:
            mimetype = zf.read("mimetype").decode("ascii", "replace").strip() if "mimetype" in names else ""
        if mimetype == "application/epub+zip":
            return Sniffed("epub", "epub", mimetype)
        for e, mime in OFFICE_EXTS.items():
            if mimetype == mime:
                return Sniffed("office", e, mime)
        if "[Content_Types].xml" in names:
            for e, prefix in OOXML_PREFIX.items():
                if any(n.startswith(prefix) for n in names):
                    return Sniffed("office", e, OFFICE_EXTS[e])
        raise Unsupported("ZIP-based file is not a recognised EPUB or Office document.")

    if head.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"):
        if ext in ("doc", "xls", "ppt"):
            return Sniffed("office", ext, OFFICE_EXTS[ext])
        raise Unsupported("Legacy OLE document: rename with .doc, .xls or .ppt to indicate its type.")

    if head.startswith(b"{\\rtf"):
        return Sniffed("office", "rtf", OFFICE_EXTS["rtf"])

    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return Sniffed("image", "png", IMAGE_MIMES["png"])
    if head.startswith(b"\xff\xd8\xff"):
        return Sniffed("image", "jpg", IMAGE_MIMES["jpg"])
    if head[:4] in (b"II*\x00", b"MM\x00*"):
        return Sniffed("image", "tiff", IMAGE_MIMES["tiff"])
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return Sniffed("image", "webp", IMAGE_MIMES["webp"])
    if head[:2] == b"BM":
        return Sniffed("image", "bmp", IMAGE_MIMES["bmp"])
    if head[:6] in (b"GIF87a", b"GIF89a"):
        return Sniffed("image", "gif", IMAGE_MIMES["gif"])

    raise Unsupported("Unsupported or unrecognised file type (content does not match a supported format).")


# --------------------------------------------------------------------------- archives

def safe_extract_zip(zip_path: Path, dest: Path, *, max_files: int = 20000,
                     max_total_bytes: int = 2 * 1024**3) -> list[Path]:
    """Extract a ZIP while rejecting traversal, absolute paths, symlinks and zip bombs."""
    dest.mkdir(parents=True, exist_ok=True)
    out: list[Path] = []
    total = 0
    with zipfile.ZipFile(zip_path) as zf:
        infos = zf.infolist()
        if len(infos) > max_files:
            raise AppError("Archive contains too many entries.", code="unsafe_archive")
        for info in infos:
            name = info.filename
            p = PurePosixPath(name)
            unsafe = (
                name.startswith("/")
                or "\\" in name
                or ".." in p.parts
                or (bool(p.parts) and ":" in p.parts[0])
            )
            if unsafe:
                raise AppError("Archive contains an unsafe path.", code="unsafe_archive")
            if (info.external_attr >> 16) & 0o170000 == 0o120000:
                raise AppError("Archive contains a symbolic link.", code="unsafe_archive")
            total += info.file_size
            if total > max_total_bytes:
                raise AppError("Archive expands beyond the allowed size.", code="unsafe_archive")
            if info.compress_size and info.file_size / max(info.compress_size, 1) > 1000:
                raise AppError("Archive has a suspicious compression ratio.", code="unsafe_archive")
            target = ensure_within(dest, dest / name)
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info) as src, target.open("wb") as dst:
                shutil.copyfileobj(src, dst, CHUNK)
            out.append(target)
    return out
