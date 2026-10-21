"""Diagnostics for native tools and assets. Every missing tool yields an actionable hint."""

from __future__ import annotations

import re
import subprocess
from dataclasses import asdict, dataclass
from functools import lru_cache
from importlib import metadata

from app.core.config import get_settings
from app.core.subproc import which


@dataclass
class ToolStatus:
    name: str
    available: bool
    version: str | None
    required_for: list[str]
    install_hint: str
    detail: str | None = None


def _version(argv: list[str], pattern: str = r"(\d+(?:\.\d+)+)") -> str | None:
    try:
        out = subprocess.run(argv, capture_output=True, timeout=30, check=False)  # noqa: S603
    except (OSError, subprocess.TimeoutExpired):
        return None
    text = (out.stdout + out.stderr).decode("utf-8", "replace")
    m = re.search(pattern, text)
    return m.group(1) if m else None


def _tesseract_langs(cmd: str) -> list[str]:
    try:
        out = subprocess.run([cmd, "--list-langs"], capture_output=True, timeout=30, check=False)  # noqa: S603
    except (OSError, subprocess.TimeoutExpired):
        return []
    lines = out.stdout.decode("utf-8", "replace").splitlines()
    return [ln.strip() for ln in lines[1:] if ln.strip()]


@lru_cache
def tool_report() -> dict[str, ToolStatus]:
    s = get_settings()
    report: dict[str, ToolStatus] = {}

    tess = which(s.tesseract_cmd)
    langs = _tesseract_langs(tess) if tess else []
    wanted = [lang for lang in s.ocr_languages.split("+") if lang]
    missing = [lang for lang in wanted if lang not in langs]
    report["tesseract"] = ToolStatus(
        "Tesseract OCR", bool(tess) and not missing,
        _version([tess, "--version"]) if tess else None,
        ["OCR", "scanned PDF to EPUB", "searchable PDF"],
        "Debian/Ubuntu: sudo apt install tesseract-ocr tesseract-ocr-eng tesseract-ocr-spa tesseract-ocr-osd; "
        "macOS: brew install tesseract tesseract-lang; Windows: install the UB Mannheim build and add it to PATH.",
        (f"languages installed: {', '.join(langs)}" + (f"; missing: {', '.join(missing)}" if missing else ""))
        if tess else "executable not found",
    )
    report["tesseract_osd"] = ToolStatus(
        "Tesseract orientation data (osd)", "osd" in langs, None, ["automatic page orientation"],
        "Install tesseract-ocr-osd (or osd.traineddata into the tessdata directory).",
    )

    soffice = which(s.soffice_cmd) or which("libreoffice")
    report["libreoffice"] = ToolStatus(
        "LibreOffice (headless)", bool(soffice), _version([soffice, "--version"]) if soffice else None,
        ["Office to PDF"],
        "Debian/Ubuntu: sudo apt install libreoffice-core libreoffice-writer libreoffice-calc libreoffice-impress; "
        "macOS: brew install --cask libreoffice; Windows: install LibreOffice and set PDFWS_SOFFICE_CMD.",
    )

    java = which(s.java_cmd)
    jar_ok = s.epubcheck_jar.is_file()
    report["epubcheck"] = ToolStatus(
        "EPUBCheck", bool(java) and jar_ok,
        _version([java, "-jar", str(s.epubcheck_jar), "--version"], r"EPUBCheck v?(\d+(?:\.\d+)+)")
        if java and jar_ok else None,
        ["independent EPUB validation"],
        "Install a Java 11+ runtime and run ./scripts/install-epubcheck.sh (downloads EPUBCheck into tools/).",
        None if java and jar_ok else ("java not found" if not java else "epubcheck.jar not found"),
    )

    try:
        import weasyprint  # noqa: F401

        wp_ok, wp_detail = True, None
    except OSError as exc:  # Pango/Cairo shared libraries missing
        wp_ok, wp_detail = False, str(exc)[:200]
    report["weasyprint"] = ToolStatus(
        "WeasyPrint (Pango)", wp_ok, metadata.version("weasyprint") if wp_ok else None, ["EPUB to PDF"],
        "Debian/Ubuntu: sudo apt install libpango-1.0-0 libpangoft2-1.0-0; macOS: brew install pango; "
        "Windows: install GTK runtime (see WeasyPrint docs).", wp_detail,
    )
    for pkg in ("pikepdf", "pypdfium2", "pdfminer.six", "opencv-python-headless", "EbookLib", "Pillow"):
        try:
            v = metadata.version(pkg)
            report[pkg] = ToolStatus(pkg, True, v, ["core"], "pip install -r backend/requirements.txt")
        except metadata.PackageNotFoundError:
            report[pkg] = ToolStatus(pkg, False, None, ["core"], "pip install -r backend/requirements.txt")
    return report


def tool_available(key: str) -> bool:
    st = tool_report().get(key)
    return bool(st and st.available)


def tool_versions() -> dict[str, str | None]:
    return {k: v.version for k, v in tool_report().items()}


def report_dicts() -> list[dict[str, object]]:
    return [{"key": k, **asdict(v)} for k, v in tool_report().items()]
