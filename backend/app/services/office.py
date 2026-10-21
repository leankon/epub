"""Office to PDF via LibreOffice headless in an isolated, macro-disabled profile."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from app.core.config import get_settings
from app.core.errors import AppError, ProcessingError, ToolUnavailable
from app.core.subproc import run, which

SUPPORTED = {"docx", "odt", "doc", "rtf", "xlsx", "ods", "xls", "pptx", "odp", "ppt"}
WARNINGS = {
    "text": ["Fonts not installed locally are substituted, which can change line and page breaks.",
             "Tracked changes, comments and fields may render differently than in Microsoft Office."],
    "sheet": ["Each sheet is paginated by LibreOffice; very wide sheets may span several pages.",
              "Formulas are exported as their last computed values; charts may render differently."],
    "slides": ["Animations, transitions, audio and video are not represented in PDF.",
               "Fonts and embedded charts may render differently than in PowerPoint."],
}
KIND = {"docx": "text", "odt": "text", "doc": "text", "rtf": "text", "xlsx": "sheet", "ods": "sheet",
        "xls": "sheet", "pptx": "slides", "odp": "slides", "ppt": "slides"}

# Macro security "very high" with execution disabled, written into the throwaway profile.
REGISTRY = """<?xml version="1.0" encoding="UTF-8"?>
<oor:items xmlns:oor="http://openoffice.org/2001/registry" xmlns:xs="http://www.w3.org/2001/XMLSchema"
 xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
<item oor:path="/org.openoffice.Office.Common/Security/Scripting"><prop oor:name="MacroSecurityLevel" oor:op="fuse"><value>3</value></prop></item>
<item oor:path="/org.openoffice.Office.Common/Security/Scripting"><prop oor:name="DisableMacrosExecution" oor:op="fuse"><value>true</value></prop></item>
<item oor:path="/org.openoffice.Office.Common/Misc"><prop oor:name="UseLocking" oor:op="fuse"><value>false</value></prop></item>
</oor:items>
"""


def soffice_path() -> str:
    s = get_settings()
    exe = which(s.soffice_cmd) or which("libreoffice")
    if exe is None:
        raise ToolUnavailable("LibreOffice is not installed (needed for Office to PDF). See README.",
                              details={"tool": "libreoffice"})
    return exe


def office_to_pdf(src: Path, ext: str, out: Path, workdir: Path) -> dict[str, Any]:
    if ext not in SUPPORTED:
        raise AppError(f"Unsupported Office format: .{ext}", code="unsupported_media", status_code=415)
    exe = soffice_path()
    indir, outdir, profile = workdir / "in", workdir / "out", workdir / "profile"
    for d in (indir, outdir, profile / "user"):
        d.mkdir(parents=True, exist_ok=True)
    (profile / "user" / "registrymodifications.xcu").write_text(REGISTRY, "utf-8")
    inp = indir / f"input.{ext}"  # generated name: never derived from the untrusted filename
    shutil.copyfile(src, inp)
    s = get_settings()
    res = run([exe, f"-env:UserInstallation={profile.resolve().as_uri()}", "--headless", "--invisible",
               "--nologo", "--nodefault", "--nolockcheck", "--norestore", "--convert-to", "pdf",
               "--outdir", str(outdir), str(inp)], tool_name="LibreOffice", cwd=workdir, check=False,
              timeout=s.subprocess_timeout_seconds, limit_address_space=False,
              env={"HOME": str(profile)})
    produced = outdir / "input.pdf"
    if not produced.exists() or produced.stat().st_size == 0:
        raise ProcessingError("LibreOffice could not convert this document.",
                              details={"exit_code": res.returncode, "stderr_tail": res.stderr[-300:]})
    shutil.move(str(produced), str(out))
    return {"format": ext, "warnings": WARNINGS[KIND[ext]] + [
        "Layout fidelity is not guaranteed; compare the PDF with the original before relying on it."]}
