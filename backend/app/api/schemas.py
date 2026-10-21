"""Pydantic request models. Every operation's parameters are validated before a job is queued."""

from __future__ import annotations

import uuid
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Empty(Strict):
    pass


class MergeParams(Strict):
    title: str | None = Field(None, max_length=200)


class SplitParams(Strict):
    mode: Literal["ranges", "extract", "every", "bookmarks"] = "ranges"
    ranges: str | None = Field(None, max_length=2000)


class PageOrder(Strict):
    page: int = Field(ge=0)
    rotate: Literal[0, 90, 180, 270, -90] = 0


class OrganizeParams(Strict):
    order: list[PageOrder] = Field(min_length=1, max_length=20000)


class RotateParams(Strict):
    angle: Literal[90, 180, 270] = 90
    pages: str | None = Field(None, max_length=2000)


class CompressParams(Strict):
    preset: Literal["lossless", "high_quality", "balanced", "strong"] = "balanced"


Position = Literal["center", "top-left", "top-center", "top-right", "bottom-left", "bottom-center",
                   "bottom-right", "middle-left", "middle-right", "tile"]


class WatermarkParams(Strict):
    type: Literal["text", "image"] = "text"
    text: str = Field("CONFIDENTIAL", max_length=200)
    image_version_id: uuid.UUID | None = None
    position: Position = "center"
    opacity: float = Field(0.3, gt=0, le=1)
    rotation: float = Field(45, ge=-360, le=360)
    font_size: float = Field(48, ge=4, le=400)
    image_scale: float = Field(0.4, gt=0, le=1)
    color: str = Field("#cc0000", pattern=r"^#[0-9a-fA-F]{6}$")
    pages: str | None = Field(None, max_length=2000)


class PageNumberParams(Strict):
    position: Literal["top-left", "top-center", "top-right", "bottom-left", "bottom-center",
                      "bottom-right"] = "bottom-center"
    format: str = Field("{n}", max_length=80)
    start: int = Field(1, ge=0, le=100000)
    font_size: float = Field(11, ge=4, le=72)
    margin: float = Field(28, ge=0, le=300)
    color: str = Field("#000000", pattern=r"^#[0-9a-fA-F]{6}$")
    pages: str | None = Field(None, max_length=2000)


class RedactArea(Strict):
    page: int = Field(ge=0)
    rect: list[float] = Field(min_length=4, max_length=4)


class RedactParams(Strict):
    areas: list[RedactArea] = Field(default_factory=list, max_length=5000)
    terms: list[str] = Field(default_factory=list, max_length=200)
    match_case: bool = False
    sanitize: dict[str, bool] = Field(default_factory=dict)

    @field_validator("terms")
    @classmethod
    def _terms(cls, v: list[str]) -> list[str]:
        return [t[:500] for t in v if t.strip()]


class MetadataParams(Strict):
    title: str | None = Field(None, max_length=1000)
    author: str | None = Field(None, max_length=1000)
    subject: str | None = Field(None, max_length=2000)
    keywords: str | None = Field(None, max_length=2000)
    language: str | None = Field(None, max_length=40)


class Bookmark(Strict):
    title: str = Field(max_length=500)
    page: int = Field(ge=0)
    children: list[Bookmark] = Field(default_factory=list)


class BookmarkParams(Strict):
    items: list[Bookmark] = Field(max_length=5000)


class ProtectParams(Strict):
    user_password: str = Field("", max_length=127)
    owner_password: str = Field("", max_length=127)
    permissions: dict[str, bool] = Field(default_factory=dict)


class UnlockParams(Strict):
    password: str = Field(min_length=1, max_length=127)


class PagesParams(Strict):
    pages: str | None = Field(None, max_length=2000)


class PdfToImagesParams(Strict):
    dpi: int = Field(150, ge=36, le=600)
    format: Literal["png", "jpg"] = "png"
    pages: str | None = Field(None, max_length=2000)


class ImagesToPdfParams(Strict):
    page_size: Literal["image", "a4", "letter"] = "image"
    title: str | None = Field(None, max_length=200)


class OcrPdfParams(Strict):
    languages: str | None = Field(None, pattern=r"^[a-z_]{3,8}(\+[a-z_]{3,8})*$")
    force: bool = False


class EpubToPdfParams(Strict):
    page_size: Literal["a4", "a5", "letter", "6x9"] = "a5"
    font_size: float = Field(11, ge=7, le=18)


PARAM_MODELS: dict[str, type[BaseModel]] = {
    "merge": MergeParams, "split": SplitParams, "organize": OrganizeParams, "rotate": RotateParams,
    "compress": CompressParams, "watermark": WatermarkParams, "page_numbers": PageNumberParams,
    "redact": RedactParams, "metadata": MetadataParams, "bookmarks": BookmarkParams, "protect": ProtectParams,
    "unlock": UnlockParams, "extract_images": PagesParams, "pdf_to_images": PdfToImagesParams,
    "images_to_pdf": ImagesToPdfParams, "ocr_pdf": OcrPdfParams, "epub_to_pdf": EpubToPdfParams,
    "office_to_pdf": Empty, "epub_validate": Empty, "backup": Empty,
}
BATCHABLE = {"compress", "watermark", "page_numbers", "rotate", "metadata", "ocr_pdf", "pdf_to_images",
             "extract_images", "epub_to_pdf", "office_to_pdf", "protect", "epub_validate"}


class JobCreate(Strict):
    kind: str = Field(max_length=60)
    version_ids: list[uuid.UUID] = Field(default_factory=list, max_length=500)
    params: dict[str, Any] = Field(default_factory=dict)
    idempotency_key: str | None = Field(None, max_length=120)
    acknowledge_warnings: bool = False


class BatchCreate(Strict):
    kind: str
    version_ids: list[uuid.UUID] = Field(min_length=1, max_length=500)
    params: dict[str, Any] = Field(default_factory=dict)
    acknowledge_warnings: bool = False


class ResumeRequest(Strict):
    secrets: dict[str, Any] = Field(default_factory=dict)


class DocumentPatch(Strict):
    title: str | None = Field(None, min_length=1, max_length=300)
    tags: list[str] | None = Field(None, max_length=50)


class PreprocessOptions(Strict):
    orientation: bool = True
    deskew: bool = True
    perspective: Literal["auto", "on", "off"] = "auto"
    denoise: Literal["auto", "on", "off"] = "auto"
    contrast: Literal["auto", "on", "off"] = "auto"
    threshold: Literal["auto", "on", "off"] = "auto"
    normalize_resolution: bool = True


class OcrOptions(Strict):
    languages: str | None = Field(None, pattern=r"^[a-z_]{3,8}(\+[a-z_]{3,8})*$")
    dpi: int | None = Field(None, ge=150, le=600)
    psm: Literal[1, 3, 4, 6, 11, 12] = 3
    force_ocr: bool = False
    preprocess: PreprocessOptions = Field(default_factory=PreprocessOptions)


class OcrSessionCreate(Strict):
    version_id: uuid.UUID
    options: OcrOptions = Field(default_factory=OcrOptions)


class RegionUpdate(Strict):
    id: str = Field(max_length=40)
    corrected_text: str | None = Field(None, max_length=100000)
    revert: bool = False
    role: Literal["paragraph", "heading", "caption", "footnote", "header", "footer", "page_number",
                  "marginal", "list_item", "figure", "table", "formula"] | None = None
    heading_level: int | None = Field(None, ge=1, le=3)
    excluded: bool | None = None


class RegionsUpdate(Strict):
    regions: list[RegionUpdate] = Field(max_length=2000)
    mark_reviewed: bool = False


class ReprocessRequest(Strict):
    pages: list[int] = Field(min_length=1, max_length=5000)
    options: OcrOptions = Field(default_factory=OcrOptions)


class Chapter(Strict):
    title: str = Field(max_length=300)
    page: int = Field(ge=1)


class BookMetadata(Strict):
    title: str | None = Field(None, max_length=500)
    author: str | None = Field(None, max_length=500)
    language: str | None = Field(None, pattern=r"^[A-Za-z]{2,3}(-[A-Za-z0-9]{2,8})*$")
    publisher: str | None = Field(None, max_length=300)
    chapters: list[Chapter] | None = Field(None, max_length=2000)


class EpubConvert(Strict):
    version_id: uuid.UUID
    session_id: uuid.UUID | None = None
    strategy: Literal["hybrid", "reflowable", "fixed"] = "hybrid"
    metadata: BookMetadata = Field(default_factory=BookMetadata)
    allow_unreviewed: bool = False
    options: OcrOptions = Field(default_factory=OcrOptions)


class PolicyUpdate(Strict):
    enabled: bool
    max_age_days: int = Field(ge=1, le=36500)
    keep_latest: bool = True


class RestoreRequest(Strict):
    confirm: str
    safety_backup: bool = True


class PreviewRequest(Strict):
    kind: Literal["watermark", "page_numbers"]
    params: dict[str, Any] = Field(default_factory=dict)


class InspectRequest(Strict):
    password: str = Field(max_length=127)
