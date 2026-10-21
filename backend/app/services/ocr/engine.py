"""Tesseract invocation (local executable, argv only, time and memory limited)."""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from app.core.config import get_settings
from app.core.errors import ToolUnavailable
from app.core.subproc import run
from app.core.tools import tool_available


@dataclass
class Word:
    text: str
    conf: float
    x0: int
    y0: int
    x1: int
    y1: int
    block: int
    par: int
    line: int

    @property
    def h(self) -> int:
        return self.y1 - self.y0

    @property
    def cx(self) -> float:
        return (self.x0 + self.x1) / 2

    @property
    def cy(self) -> float:
        return (self.y0 + self.y1) / 2


def require_tesseract() -> None:
    if not tool_available("tesseract"):
        raise ToolUnavailable("Tesseract OCR (with the configured language packs) is not installed. "
                              "See README: OCR language installation.", details={"tool": "tesseract"})


def validate_languages(langs: str) -> str:
    if not re.fullmatch(r"[a-z_]{3,8}(\+[a-z_]{3,8})*", langs or ""):
        raise ValueError("invalid language list")
    return langs


def ocr_words(img: np.ndarray, workdir: Path, *, langs: str | None = None, psm: int = 3) -> list[Word]:
    """Run Tesseract on an image array and return word boxes with confidences."""
    require_tesseract()
    s = get_settings()
    langs = validate_languages(langs or s.ocr_languages)
    if psm not in (1, 3, 4, 6, 11, 12):
        psm = 3
    workdir.mkdir(parents=True, exist_ok=True)
    src = workdir / "ocr-input.png"
    cv2.imwrite(str(src), img)
    try:
        res = run([s.tesseract_cmd, str(src), "stdout", "-l", langs, "--psm", str(psm), "--oem", "1",
                   "-c", "preserve_interword_spaces=1", "tsv"], tool_name="tesseract",
                  env={"OMP_THREAD_LIMIT": "1"})
    finally:
        src.unlink(missing_ok=True)
    words: list[Word] = []
    reader = csv.DictReader(io.StringIO(res.stdout), delimiter="\t", quoting=csv.QUOTE_NONE)
    for row in reader:
        try:
            if int(row["level"]) != 5:
                continue
            text = (row.get("text") or "").strip()
            conf = float(row["conf"])
        except (KeyError, ValueError, TypeError):
            continue
        if not text or conf < 0:
            continue
        x, y, w, h = (int(row[k]) for k in ("left", "top", "width", "height"))
        words.append(Word(text, conf, x, y, x + w, y + h, int(row["block_num"]), int(row["par_num"]),
                          int(row["line_num"])))
    return words


def detect_orientation(gray: np.ndarray, workdir: Path) -> tuple[int, float]:
    """Return (degrees clockwise the image must be rotated to be upright, confidence)."""
    s = get_settings()
    if not tool_available("tesseract_osd"):
        return 0, 0.0
    h, w = gray.shape[:2]
    scale = min(1.0, 1800 / max(h, w))
    small = cv2.resize(gray, (int(w * scale), int(h * scale))) if scale < 1 else gray
    src = workdir / "osd-input.png"
    workdir.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(src), small)
    try:
        res = run([s.tesseract_cmd, str(src), "stdout", "--psm", "0", "-l", "osd"], tool_name="tesseract-osd",
                  check=False, timeout=120)
    finally:
        src.unlink(missing_ok=True)
    rot = re.search(r"Rotate:\s*(\d+)", res.stdout)
    conf = re.search(r"Orientation confidence:\s*([\d.]+)", res.stdout)
    if not rot:
        return 0, 0.0
    return int(rot.group(1)) % 360, float(conf.group(1)) if conf else 0.0
