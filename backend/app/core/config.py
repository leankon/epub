"""Application configuration loaded from environment variables and the repo-level `.env`."""

from __future__ import annotations

import ipaddress
from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(REPO_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Network binding. Loopback only unless PDFWS_ALLOW_NETWORK is explicitly true.
    host: str = Field("127.0.0.1", alias="PDFWS_HOST")
    backend_port: int = Field(8765, alias="PDFWS_BACKEND_PORT")
    frontend_port: int = Field(3000, alias="PDFWS_FRONTEND_PORT")
    allow_network: bool = Field(False, alias="PDFWS_ALLOW_NETWORK")
    extra_allowed_hosts: str = Field("", alias="PDFWS_EXTRA_ALLOWED_HOSTS")

    data_dir: Path = Field(REPO_ROOT / "data", alias="PDFWS_DATA_DIR")
    session_token_file: Path | None = Field(None, alias="PDFWS_SESSION_TOKEN_FILE")

    database_url: str = Field(
        "postgresql+psycopg://pdfws_app:change-me-app@127.0.0.1:54329/pdfws",
        alias="DATABASE_URL",
    )
    database_admin_url: str = Field(
        "postgresql+psycopg://pdfws_owner:change-me-owner@127.0.0.1:54329/pdfws",
        alias="DATABASE_ADMIN_URL",
    )

    max_upload_mb: int = Field(500, alias="PDFWS_MAX_UPLOAD_MB")
    max_pages: int = Field(3000, alias="PDFWS_MAX_PAGES")
    max_concurrent_jobs: int = Field(2, alias="PDFWS_MAX_CONCURRENT_JOBS")
    max_queued_jobs: int = Field(200, alias="PDFWS_MAX_QUEUED_JOBS")
    job_timeout_seconds: int = Field(4 * 3600, alias="PDFWS_JOB_TIMEOUT_SECONDS")
    subprocess_timeout_seconds: int = Field(600, alias="PDFWS_SUBPROCESS_TIMEOUT_SECONDS")
    subprocess_memory_mb: int = Field(4096, alias="PDFWS_SUBPROCESS_MEMORY_MB")
    temp_max_age_hours: int = Field(24, alias="PDFWS_TEMP_MAX_AGE_HOURS")

    ocr_dpi: int = Field(300, alias="PDFWS_OCR_DPI")
    ocr_languages: str = Field("spa+eng", alias="PDFWS_OCR_LANGUAGES")
    tesseract_cmd: str = Field("tesseract", alias="PDFWS_TESSERACT_CMD")
    soffice_cmd: str = Field("soffice", alias="PDFWS_SOFFICE_CMD")
    java_cmd: str = Field("java", alias="PDFWS_JAVA_CMD")
    epubcheck_jar: Path = Field(REPO_ROOT / "tools" / "epubcheck" / "epubcheck.jar", alias="PDFWS_EPUBCHECK_JAR")

    # Workers are disabled in some tests that drive jobs synchronously.
    start_workers: bool = Field(True, alias="PDFWS_START_WORKERS")

    @field_validator("data_dir", "epubcheck_jar", mode="after")
    @classmethod
    def _absolute(cls, v: Path) -> Path:
        return v if v.is_absolute() else (REPO_ROOT / v).resolve()

    @model_validator(mode="after")
    def _check_binding(self) -> Settings:
        if not self.allow_network:
            try:
                is_loopback = ipaddress.ip_address(self.host).is_loopback
            except ValueError:
                is_loopback = self.host == "localhost"
            if not is_loopback:
                raise ValueError(
                    f"PDFWS_HOST={self.host!r} is not a loopback address. "
                    "Set PDFWS_ALLOW_NETWORK=true to listen on a network interface."
                )
        return self

    # Derived locations -------------------------------------------------
    @property
    def documents_dir(self) -> Path:
        return self.data_dir / "documents"

    @property
    def tmp_dir(self) -> Path:
        return self.data_dir / "tmp"

    @property
    def cache_dir(self) -> Path:
        return self.data_dir / "cache"

    @property
    def backups_dir(self) -> Path:
        return self.data_dir / "backups"

    @property
    def exports_dir(self) -> Path:
        return self.data_dir / "exports"

    @property
    def ocr_dir(self) -> Path:
        return self.data_dir / "ocr"

    @property
    def quarantine_dir(self) -> Path:
        return self.data_dir / "quarantine"

    @property
    def run_dir(self) -> Path:
        return self.data_dir / "run"

    @property
    def token_file(self) -> Path:
        return self.session_token_file or (self.run_dir / "session-token")

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024

    @property
    def allowed_hosts(self) -> set[str]:
        hosts = {
            f"127.0.0.1:{self.backend_port}",
            f"localhost:{self.backend_port}",
            f"127.0.0.1:{self.frontend_port}",
            f"localhost:{self.frontend_port}",
            f"{self.host}:{self.backend_port}",
        }
        hosts |= {h.strip() for h in self.extra_allowed_hosts.split(",") if h.strip()}
        return hosts

    @property
    def allowed_origins(self) -> set[str]:
        origins = {f"http://{h}" for h in self.allowed_hosts}
        return origins

    def ensure_dirs(self) -> None:
        for d in (
            self.data_dir,
            self.documents_dir,
            self.tmp_dir,
            self.cache_dir,
            self.backups_dir,
            self.exports_dir,
            self.ocr_dir,
            self.quarantine_dir,
            self.run_dir,
        ):
            d.mkdir(parents=True, exist_ok=True)
            try:
                # The data root is traverse-only for others (0711) so a PostgreSQL cluster in
                # data/postgres can run under its own OS account; every subdirectory is private (0700).
                d.chmod(0o711 if d == self.data_dir else 0o700)
            except PermissionError:
                pass


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
