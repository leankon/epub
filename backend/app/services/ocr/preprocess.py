"""Image preprocessing for OCR (OpenCV). Originals are never modified: geometric corrections are
applied to a colour copy (used for review and figure crops) and photometric enhancements only to a
separate grayscale OCR image."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from app.services.ocr.engine import detect_orientation

DEFAULT_OPTIONS: dict[str, Any] = {
    "orientation": True,
    "deskew": True,
    "perspective": "auto",  # auto | on | off
    "denoise": "auto",
    "contrast": "auto",
    "threshold": "auto",
    "normalize_resolution": True,
}


@dataclass
class Prepared:
    color: np.ndarray  # geometrically corrected colour image (BGR)
    ocr: np.ndarray  # enhanced grayscale/binary image for Tesseract
    ocr_scale: float  # ocr image size / colour image size
    steps: dict[str, Any] = field(default_factory=dict)


def _rotate_quadrant(img: np.ndarray, deg: int) -> np.ndarray:
    return {90: lambda a: cv2.rotate(a, cv2.ROTATE_90_CLOCKWISE),
            180: lambda a: cv2.rotate(a, cv2.ROTATE_180),
            270: lambda a: cv2.rotate(a, cv2.ROTATE_90_COUNTERCLOCKWISE)}.get(deg, lambda a: a)(img)


def estimate_skew(gray: np.ndarray) -> float:
    """Median angle of text-line blobs in degrees (positive = text rotated counter-clockwise)."""
    h, w = gray.shape
    scale = min(1.0, 2000 / max(h, w))
    g = cv2.resize(gray, (int(w * scale), int(h * scale))) if scale < 1 else gray
    _, bw = cv2.threshold(g, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    kw = max(15, g.shape[1] // 40)
    blobs = cv2.dilate(bw, cv2.getStructuringElement(cv2.MORPH_RECT, (kw, 3)))
    contours, _ = cv2.findContours(blobs, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    angles: list[float] = []
    weights: list[float] = []
    for c in contours:
        (_, _), (cw, ch), ang = cv2.minAreaRect(c)
        if cw < ch:  # make `ang` the angle of the long side
            cw, ch = ch, cw
            ang += 90
        ang = ((ang + 90) % 180) - 90
        if cw < g.shape[1] * 0.15 or cw / max(ch, 1) < 5 or abs(ang) > 45:
            continue
        angles.append(ang)
        weights.append(cw)
    if len(angles) < 3:
        return 0.0
    order = np.argsort(angles)
    a = np.array(angles)[order]
    wts = np.cumsum(np.array(weights)[order])
    median = float(a[np.searchsorted(wts, wts[-1] / 2)])
    return -median  # OpenCV's angle sign is clockwise in image coordinates


def rotate_by(img: np.ndarray, angle: float) -> np.ndarray:
    h, w = img.shape[:2]
    m = cv2.getRotationMatrix2D((w / 2, h / 2), angle, 1.0)
    cos, sin = abs(m[0, 0]), abs(m[0, 1])
    nw, nh = int(h * sin + w * cos), int(h * cos + w * sin)
    m[0, 2] += nw / 2 - w / 2
    m[1, 2] += nh / 2 - h / 2
    border = (255, 255, 255) if img.ndim == 3 else 255
    return cv2.warpAffine(img, m, (nw, nh), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_CONSTANT,
                          borderValue=border)


def find_page_quad(color: np.ndarray) -> np.ndarray | None:
    """Detect the paper outline in a photograph (a 4-point contour covering most of the frame)."""
    h, w = color.shape[:2]
    scale = 1000 / max(h, w)
    small = cv2.resize(color, (int(w * scale), int(h * scale)))
    gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
    gray = cv2.GaussianBlur(gray, (5, 5), 0)
    edges = cv2.Canny(gray, 50, 150)
    edges = cv2.dilate(edges, np.ones((3, 3), np.uint8))
    contours, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    area = small.shape[0] * small.shape[1]
    for c in sorted(contours, key=cv2.contourArea, reverse=True)[:5]:
        peri = cv2.arcLength(c, True)
        approx = cv2.approxPolyDP(c, 0.02 * peri, True)
        a = cv2.contourArea(approx)
        if len(approx) == 4 and 0.5 * area < a < 0.97 * area and cv2.isContourConvex(approx):
            # Only accept when the surroundings clearly differ from the paper (table, hand, shadow);
            # otherwise this is probably a frame or figure inside a clean scan.
            mask = np.zeros(gray.shape, np.uint8)
            cv2.fillConvexPoly(mask, approx.reshape(4, 2), 255)
            inside = float(cv2.mean(gray, mask=mask)[0])
            outside = float(cv2.mean(gray, mask=cv2.bitwise_not(mask))[0])
            if abs(inside - outside) < 25:
                return None
            return (approx.reshape(4, 2) / scale).astype(np.float32)
    return None


def _order_quad(q: np.ndarray) -> np.ndarray:
    s = q.sum(axis=1)
    d = np.diff(q, axis=1).ravel()
    return np.array([q[np.argmin(s)], q[np.argmin(d)], q[np.argmax(s)], q[np.argmax(d)]], dtype=np.float32)


def warp_quad(color: np.ndarray, quad: np.ndarray) -> np.ndarray:
    tl, tr, br, bl = _order_quad(quad)
    wdt = int(max(np.linalg.norm(br - bl), np.linalg.norm(tr - tl)))
    hgt = int(max(np.linalg.norm(tr - br), np.linalg.norm(tl - bl)))
    dst = np.array([[0, 0], [wdt - 1, 0], [wdt - 1, hgt - 1], [0, hgt - 1]], dtype=np.float32)
    m = cv2.getPerspectiveTransform(np.array([tl, tr, br, bl], dtype=np.float32), dst)
    return cv2.warpPerspective(color, m, (wdt, hgt), flags=cv2.INTER_CUBIC)


def text_height(gray: np.ndarray) -> float:
    """Median connected-component height of glyph-sized blobs (px)."""
    _, bw = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    n, _, stats, _ = cv2.connectedComponentsWithStats(bw, connectivity=8)
    hs = [stats[i, cv2.CC_STAT_HEIGHT] for i in range(1, n)
          if 4 <= stats[i, cv2.CC_STAT_HEIGHT] <= 200 and stats[i, cv2.CC_STAT_WIDTH] <= 200
          and stats[i, cv2.CC_STAT_AREA] >= 6]
    return float(np.median(hs)) if len(hs) > 20 else 0.0


def preprocess(color: np.ndarray, workdir: Path, options: dict[str, Any] | None = None, *,
               photographed: bool = False) -> Prepared:
    opt = {**DEFAULT_OPTIONS, **(options or {})}
    steps: dict[str, Any] = {}
    img = color

    if opt["perspective"] == "on" or (opt["perspective"] == "auto" and photographed):
        quad = find_page_quad(img)
        if quad is not None:
            img = warp_quad(img, quad)
            steps["perspective_corrected"] = True

    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    if opt["orientation"]:
        rot, conf = detect_orientation(gray, workdir)
        steps["orientation"] = {"rotate": rot, "confidence": round(conf, 2)}
        if rot in (90, 180, 270) and conf >= 1.5:
            img = _rotate_quadrant(img, rot)
            gray = _rotate_quadrant(gray, rot)
            steps["rotated"] = rot

    if opt["deskew"]:
        angle = estimate_skew(gray)
        steps["skew_degrees"] = round(angle, 2)
        if 0.2 <= abs(angle) <= 15:
            img = rotate_by(img, -angle)
            gray = rotate_by(gray, -angle)
            steps["deskewed"] = True

    # ---- photometric (OCR image only) ----
    ocr = gray
    contrast = float(gray.std())
    steps["contrast_std"] = round(contrast, 1)
    if opt["contrast"] == "on" or (opt["contrast"] == "auto" and contrast < 45):
        ocr = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(ocr)
        steps["clahe"] = True
    noise = float(cv2.Laplacian(gray, cv2.CV_64F).var())
    steps["laplacian_var"] = round(noise, 1)
    if opt["denoise"] == "on" or (opt["denoise"] == "auto" and noise > 2500):
        ocr = cv2.fastNlMeansDenoising(ocr, None, h=12, templateWindowSize=7, searchWindowSize=21)
        steps["denoised"] = True
    bg = cv2.medianBlur(cv2.resize(ocr, (max(1, ocr.shape[1] // 8), max(1, ocr.shape[0] // 8))), 21)
    uneven = float(bg.std())
    steps["illumination_std"] = round(uneven, 1)
    if opt["threshold"] == "on" or (opt["threshold"] == "auto" and uneven > 18):
        block = max(31, (min(ocr.shape) // 40) | 1)
        ocr = cv2.adaptiveThreshold(ocr, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, block, 15)
        steps["adaptive_threshold"] = True

    scale = 1.0
    if opt["normalize_resolution"]:
        th = text_height(gray)
        steps["text_height_px"] = round(th, 1)
        if 0 < th < 18:
            scale = min(2.5, 26 / th)
        elif th > 80:
            scale = max(0.5, 40 / th)
        if scale != 1.0:
            ocr = cv2.resize(ocr, (int(ocr.shape[1] * scale), int(ocr.shape[0] * scale)),
                             interpolation=cv2.INTER_CUBIC if scale > 1 else cv2.INTER_AREA)
            steps["resolution_scale"] = round(scale, 3)
    return Prepared(color=img, ocr=ocr, ocr_scale=scale, steps=steps)
